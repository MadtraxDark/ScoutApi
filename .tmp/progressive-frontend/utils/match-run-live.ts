import type { MatchRunLiveView } from "./api/contracts";

/** Retém objetos inalterados; o timer de elapsed não altera resultados. */
export function reconcileLiveSnapshot(previous: MatchRunLiveView | null, next: MatchRunLiveView): MatchRunLiveView {
  if (!previous || previous.run.id !== next.run.id) return next;
  const byId = new Map(previous.stores.map((store) => [store.id, store]));
  const stores = next.stores.map((store) => {
    const old = byId.get(store.id);
    return old && JSON.stringify(old) === JSON.stringify(store) ? old : store;
  });
  const snapshot = { ...next, stores };
  return JSON.stringify(previous) === JSON.stringify(snapshot) ? previous : snapshot;
}
