"use client";

import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import toast from "react-hot-toast";
import { catalogApi } from "@/utils/api/services";
import type { MatchRunLiveView, MatchStoreLiveView, MatchRunStatusView } from "@/utils/api/contracts";
import {
  elapsedSecondsSince,
  formatElapsedHhMmSs,
  isActiveMatchRunStatus,
  isTerminalMatchRunStatus,
  isValidActiveMatchRun,
  getMatchRunError,
  matchRunErrorReducer,
} from "@/utils/match-run";
import { startVisiblePolling } from "@/utils/visible-polling";
import { reconcileLiveSnapshot } from "@/utils/match-run-live";
import { playMatchCompleteSound } from "@/utils/match-sound";

const POLL_INTERVAL_MS = 4000;
const ELAPSED_TICK_MS = 1000;
const IDLE_POLL_INTERVAL_MS = 60_000;

/** Deduplica toast/som por run_id na sessão do browser. */
const toastedRunIds = new Set<string>();

function notifyTerminalTransition(run: MatchRunStatusView): void {
  if (toastedRunIds.has(run.id)) return;
  toastedRunIds.add(run.id);

  if (run.status === "completed") {
    const parts = [
      `Busca concluída: ${run.matches_found} oferta(s)`,
      run.errors > 0 ? `${run.errors} erro(s)` : null,
    ].filter(Boolean);
    toast.success(parts.join(" · "));
    void playMatchCompleteSound();
    return;
  }

  if (run.status === "failed") {
    if (run.failure_code === "worker_lost") {
      toast.error("Busca anterior foi interrompida.");
    } else {
      toast.error(run.failure_message || "A busca em outras lojas falhou.");
    }
    void playMatchCompleteSound();
  }
}

export type UseProductMatchRunResult = {
  activeRun: MatchRunStatusView | null;
  observedRun: MatchRunStatusView | null;
  liveResults: MatchStoreLiveView[];
  storeProgress: MatchStoreLiveView[];
  isRecovering: boolean;
  isActive: boolean;
  isStarting: boolean;
  elapsedLabel: string;
  error: string;
  start: () => Promise<void>;
  refreshActive: () => Promise<void>;
};

/** Cancela somente observação; a Run pertence ao worker PostgreSQL. */
export function useProductMatchRun(
  productId: string,
  options: { onTerminal?: (run: MatchRunStatusView) => void | Promise<void> } = {},
): UseProductMatchRunResult {
  const [snapshot, setSnapshot] = useState<MatchRunLiveView | null>(null);
  const [startingRequest, setStartingRequest] = useState<{ productId: string; epoch: number } | null>(null);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [errorState, dispatchError] = useReducer(matchRunErrorReducer, { startError: "", pollError: "" });
  const observedRef = useRef<MatchRunLiveView | null>(null);
  const watchingIdRef = useRef<string | null>(null);
  const recoveredHistoryRef = useRef(false);
  const startInFlightRef = useRef(false);
  const generationRef = useRef(0);
  const startEpochRef = useRef(0);
  const controllerRef = useRef<AbortController | null>(null);
  const terminalSeenRef = useRef(new Set<string>());
  const onTerminalRef = useRef(options.onTerminal);
  const pollingRef = useRef<ReturnType<typeof startVisiblePolling> | null>(null);
  useEffect(() => { onTerminalRef.current = options.onTerminal; }, [options.onTerminal]);

  const applySnapshot = useCallback((next: MatchRunLiveView) => {
    if (next.run.product_id !== productId) throw new Error("A API retornou uma busca de outro produto.");
    const generation = generationRef.current;
    const previous = observedRef.current;
    const merged = reconcileLiveSnapshot(previous, next);
    observedRef.current = merged;
    watchingIdRef.current = next.run.id;
    setSnapshot(merged);
    if (isTerminalMatchRunStatus(next.run.status) && !terminalSeenRef.current.has(next.run.id)) {
      terminalSeenRef.current.add(next.run.id);
      if (previous?.run.id === next.run.id && isActiveMatchRunStatus(previous.run.status)) notifyTerminalTransition(next.run);
      void Promise.resolve(onTerminalRef.current?.(next.run)).catch((err: unknown) => {
        if (generation !== generationRef.current) return;
        dispatchError({ type: "poll_failed", message: err instanceof Error ? err.message : "Não foi possível atualizar as ofertas persistidas." });
      });
    }
  }, [productId]);

  const poll = useCallback(async (signal: AbortSignal) => {
    if (!productId) return;
    const epoch = startEpochRef.current;
    const known = watchingIdRef.current;
    if (known && (!observedRef.current || !isTerminalMatchRunStatus(observedRef.current.run.status))) {
      const live = await catalogApi.getMatchRunLive(known, signal);
      signal.throwIfAborted();
      if (epoch !== startEpochRef.current) return;
      applySnapshot(live);
    } else {
      const active = await catalogApi.getActiveMatchRun(productId, signal);
      signal.throwIfAborted();
      let id = active?.id ?? null;
      if (active && !isValidActiveMatchRun(active, productId)) throw new Error("Estado inválido para busca ativa.");
      if (!id && !recoveredHistoryRef.current) {
        const history = await catalogApi.listMatchRuns(productId, { limit: 1, signal });
        signal.throwIfAborted();
        id = history.items[0]?.id ?? null;
        recoveredHistoryRef.current = true;
      }
      if (id) {
        const live = await catalogApi.getMatchRunLive(id, signal);
        signal.throwIfAborted();
        if (epoch !== startEpochRef.current) return;
      applySnapshot(live);
      }
    }
    dispatchError({ type: "poll_succeeded" });
  }, [applySnapshot, productId]);

  const refreshActive = useCallback(async () => { await pollingRef.current?.refresh(); }, []);
  const start = useCallback(async () => {
    if (!productId || startInFlightRef.current || observedRef.current?.is_effectively_active) return;
    const generation = generationRef.current;
    startEpochRef.current += 1;
    const epoch = startEpochRef.current;
    const signal = controllerRef.current?.signal;
    startInFlightRef.current = true;
    setStartingRequest({ productId, epoch });
    dispatchError({ type: "start_requested" });
    try {
      const run = await catalogApi.startMatchRun(productId, signal);
      signal?.throwIfAborted();
      if (generation !== generationRef.current) return;
      if (!isValidActiveMatchRun(run, productId)) throw new Error("A API não confirmou uma busca ativa válida.");
      watchingIdRef.current = run.id;
      const initial: MatchRunLiveView = { run, is_effectively_active: true, auto_matches_found: 0, stores: [] };
      if (observedRef.current?.run.id !== run.id) {
        observedRef.current = initial;
        setSnapshot(initial);
      }
      dispatchError({ type: "start_succeeded" });
      await pollingRef.current?.refresh();
      if (generation !== generationRef.current) return;
      toast.success(run.already_active ? "Já existe uma busca em andamento." : "Busca em outras lojas iniciada.");
    } catch (err) {
      if (signal?.aborted || generation !== generationRef.current) return;
      dispatchError({ type: "start_failed", message: err instanceof Error ? err.message : "Não foi possível iniciar a busca." });
    } finally {
      setStartingRequest((current) => current?.epoch === epoch ? null : current);
      if (generation === generationRef.current) {
        startInFlightRef.current = false;
      }
    }
  }, [productId]);

  useEffect(() => {
    generationRef.current += 1;
    observedRef.current = null;
    watchingIdRef.current = null;
    recoveredHistoryRef.current = false;
    startInFlightRef.current = false;
    terminalSeenRef.current.clear();
    const controller = new AbortController();
    controllerRef.current = controller;
    if (!productId) return () => controller.abort();
    const polling = startVisiblePolling(poll, () => {
      const current = observedRef.current;
      return watchingIdRef.current && (!current || isActiveMatchRunStatus(current.run.status))
        ? POLL_INTERVAL_MS : IDLE_POLL_INTERVAL_MS;
    }, (err) => {
      dispatchError({ type: "poll_failed", message: err instanceof Error ? err.message : "Não foi possível atualizar a busca." });
    });
    pollingRef.current = polling;
    return () => {
      generationRef.current += 1;
      controller.abort();
      polling.stop();
      if (pollingRef.current === polling) pollingRef.current = null;
    };
  }, [poll, productId]);

  const visible = snapshot?.run.product_id === productId ? snapshot : null;
  const isActive = Boolean(visible?.is_effectively_active);
  useEffect(() => {
    if (!isActive) return;
    const timer = window.setInterval(() => setNowMs(Date.now()), ELAPSED_TICK_MS);
    return () => window.clearInterval(timer);
  }, [isActive]);
  const liveResults = useMemo(() => visible?.stores.filter((store) => store.status === "match" && store.matched_decision === "auto_match") ?? [], [visible]);
  const anchor = visible?.run.active_since || visible?.run.claimed_at || visible?.run.started_at;
  return {
    activeRun: isActive ? visible!.run : null,
    observedRun: visible?.run ?? null,
    storeProgress: visible?.stores ?? [],
    liveResults,
    isRecovering: Boolean(visible && visible.run.status === "running" && !isActive),
    isActive, isStarting: startingRequest?.productId === productId,
    elapsedLabel: isActive && anchor ? formatElapsedHhMmSs(elapsedSecondsSince(anchor, nowMs)) : "00:00:00",
    error: getMatchRunError(errorState), start, refreshActive,
  };
}
