"""TDD — BrowserScheduler: bounded queue, FIFO, cancel-while-queued, saturation.

Ordem TDD:
 1. Escrever testes (RED) — módulo ainda não existe
 2. Implementar BrowserScheduler (GREEN)
 3. Verificar que todos os testes passam
"""

from __future__ import annotations

import threading
import time

import pytest

from scout_api.modules.crawler.core.browser_scheduler import (
    BrowserScheduler,
    BrowserSlotLease,
    browser_queue_deadline,
)
from scout_api.modules.crawler.core.exceptions import RequestError


def _sched(
    capacity: int = 1,
    queue_capacity: int = 32,
    timeout_ms: int = 5_000,
) -> BrowserScheduler:
    return BrowserScheduler(
        capacity=capacity,
        queue_capacity=queue_capacity,
        queue_timeout_ms=timeout_ms,
    )


# ---------------------------------------------------------------------------
# Teste 1: capacity=1; segundo job entra na fila e aguarda
# ---------------------------------------------------------------------------

def test_capacity_one_second_job_waits_or_queues() -> None:
    """capacity=1 → segundo acquire bloqueia até o primeiro ser liberado."""
    sched = _sched(capacity=1)
    lease1 = sched.acquire()
    assert isinstance(lease1, BrowserSlotLease)

    acquired = threading.Event()
    done = threading.Event()

    def worker() -> None:
        lease = sched.acquire()
        acquired.set()
        sched.release(lease)
        done.set()

    t = threading.Thread(target=worker, daemon=True)
    t.start()

    # Segundo job ainda está esperando
    time.sleep(0.1)
    assert not acquired.is_set(), "Segundo job não deveria ter o slot ainda"

    # Libera o primeiro → segundo job deve obter o slot
    sched.release(lease1)
    assert acquired.wait(timeout=3.0), "Segundo job deveria ter obtido o slot"
    done.wait(timeout=3.0)
    t.join(timeout=3.0)


# ---------------------------------------------------------------------------
# Teste 2: fila saturada → falha controlada, não trava
# ---------------------------------------------------------------------------

def test_queue_saturated_fails_fast() -> None:
    """capacity=1, queue_capacity=1; terceiro acquire → RequestError imediato."""
    sched = _sched(capacity=1, queue_capacity=1)

    lease1 = sched.acquire()  # Ocupa o slot

    cancel_t1 = threading.Event()
    t1_queued = threading.Event()

    def worker1() -> None:
        t1_queued.set()
        try:
            lease = sched.acquire(cancel_event=cancel_t1)
            sched.release(lease)
        except RequestError:
            pass

    t1 = threading.Thread(target=worker1, daemon=True)
    t1.start()
    t1_queued.wait(timeout=1.0)
    time.sleep(0.05)  # Garante que t1 está na fila

    # Terceiro acquire com fila cheia deve falhar imediatamente
    with pytest.raises(RequestError) as exc_info:
        sched.acquire()
    assert exc_info.value.code == "BROWSER_QUEUE_SATURATED"
    assert exc_info.value.retryable is False

    # Limpeza
    cancel_t1.set()
    sched.release(lease1)
    t1.join(timeout=3.0)


# ---------------------------------------------------------------------------
# Teste 3: cancelamento enquanto enfileirado → nunca navega
# ---------------------------------------------------------------------------

def test_cancel_while_queued_does_not_get_slot() -> None:
    """Job com cancel_event na fila → sai da fila sem navegar."""
    sched = _sched(capacity=1, timeout_ms=10_000)

    lease1 = sched.acquire()
    cancel_event = threading.Event()
    result: dict[str, object] = {"got_slot": False, "error": None}

    def worker() -> None:
        try:
            lease = sched.acquire(cancel_event=cancel_event)
            result["got_slot"] = True
            sched.release(lease)
        except RequestError as exc:
            result["error"] = exc.code

    t = threading.Thread(target=worker, daemon=True)
    t.start()

    time.sleep(0.1)  # Garante que o worker está na fila
    cancel_event.set()
    t.join(timeout=3.0)

    assert not result["got_slot"], "Job cancelado não deve ter obtido o slot"
    assert result["error"] == "BROWSER_JOB_CANCELLED", (
        f"Esperava BROWSER_JOB_CANCELLED, obteve {result['error']!r}"
    )

    # Slot deve estar disponível após cancelamento
    sched.release(lease1)
    # Slot deve ter voltado ao pool
    snapshot = sched.snapshot()
    assert snapshot["active"] == 0


# ---------------------------------------------------------------------------
# Teste 4: fairness FIFO — três waiters obtêm slot na ordem de enqueue
# ---------------------------------------------------------------------------

def test_fifo_order() -> None:
    """Três waiters; slot é concedido na ordem de enqueue (FIFO)."""
    sched = _sched(capacity=1, queue_capacity=10, timeout_ms=10_000)
    results: list[int] = []
    holder_lease = sched.acquire()  # Segura o slot

    # Cada thread aguarda seu "start_event" antes de chamar acquire
    start_events = [threading.Event() for _ in range(3)]

    def worker(n: int, start_event: threading.Event) -> None:
        start_event.wait()
        lease = sched.acquire()
        results.append(n)
        time.sleep(0.01)  # Segura o slot brevemente
        sched.release(lease)

    threads = [
        threading.Thread(target=worker, args=(i, start_events[i]), daemon=True)
        for i in range(3)
    ]
    for t in threads:
        t.start()

    # Sinaliza em ordem com pequeno intervalo para garantir ordem de enqueue
    for evt in start_events:
        evt.set()
        time.sleep(0.03)  # Suficiente para entrar na fila antes do próximo

    time.sleep(0.05)  # Garante que todos estão na fila

    sched.release(holder_lease)

    for t in threads:
        t.join(timeout=10.0)

    assert results == [0, 1, 2], f"Ordem FIFO violada: {results}"


# ---------------------------------------------------------------------------
# Teste 5: snapshot() retorna métricas esperadas
# ---------------------------------------------------------------------------

def test_snapshot_reflects_state() -> None:
    """snapshot() deve refletir depth, capacity, active, queue_capacity."""
    sched = _sched(capacity=2, queue_capacity=5)
    snap = sched.snapshot()
    assert snap["capacity"] == 2
    assert snap["active"] == 0
    assert snap["depth"] == 0
    assert snap["queue_capacity"] == 5

    lease1 = sched.acquire()
    snap2 = sched.snapshot()
    assert snap2["active"] == 1

    sched.release(lease1)
    snap3 = sched.snapshot()
    assert snap3["active"] == 0


# ---------------------------------------------------------------------------
# Teste 6: slot_id é um int e é distinto por slot ativo
# ---------------------------------------------------------------------------

def test_slot_id_assigned() -> None:
    """BrowserSlotLease.slot_id deve ser int >= 0."""
    sched = _sched(capacity=1)
    lease = sched.acquire()
    assert isinstance(lease.slot_id, int)
    assert lease.slot_id >= 0
    sched.release(lease)


# ---------------------------------------------------------------------------
# Teste 7: BROWSER_QUEUE_TIMEOUT — caller aguarda além do limite
# ---------------------------------------------------------------------------

def test_queue_timeout() -> None:
    """Caller que aguarda além de queue_timeout_ms recebe BROWSER_QUEUE_TIMEOUT."""
    sched = BrowserScheduler(capacity=1, queue_capacity=5, queue_timeout_ms=120)
    lease1 = sched.acquire()  # Segura o único slot

    result: dict[str, object] = {"error": None}

    def worker() -> None:
        try:
            lease = sched.acquire()
            sched.release(lease)
        except RequestError as exc:
            result["error"] = exc.code

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    t.join(timeout=5.0)  # Muito além dos 120 ms de timeout

    assert result["error"] == "BROWSER_QUEUE_TIMEOUT", (
        f"Esperava BROWSER_QUEUE_TIMEOUT, obteve {result['error']!r}"
    )
    assert sched.snapshot()["browser_queue_timeout_count"] == 1, (
        "Contador de timeouts deveria ser 1"
    )
    assert sched.snapshot()["queue_timeout_count"] == 1, (
        "Chave legada queue_timeout_count também deve ser 1"
    )

    sched.release(lease1)


def test_match_queue_wait_uses_store_deadline_instead_of_global_queue_timeout() -> None:
    """Match pode esperar além de 60 s, mas só até seu deadline individual."""
    sched = BrowserScheduler(capacity=1, queue_capacity=2, queue_timeout_ms=40)
    active = sched.acquire()
    acquired = threading.Event()
    result: dict[str, str | None] = {"error": None}

    def worker() -> None:
        try:
            with browser_queue_deadline(time.monotonic() + 1.0):
                lease = sched.acquire()
            acquired.set()
            sched.release(lease)
        except RequestError as exc:
            result["error"] = exc.code

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    time.sleep(0.12)  # Excede o timeout padrão de 40 ms.
    assert not acquired.is_set()
    sched.release(active)
    assert acquired.wait(1.0)
    thread.join(1.0)
    assert result["error"] is None


def test_match_queue_deadline_removes_waiter_without_granting_browser() -> None:
    """Deadline do Match cancela espera enfileirada sem consumir o slot depois."""
    sched = BrowserScheduler(capacity=1, queue_capacity=2, queue_timeout_ms=1_000)
    active = sched.acquire()
    result: dict[str, str | None] = {"error": None}

    def worker() -> None:
        try:
            with browser_queue_deadline(time.monotonic() + 0.12):
                lease = sched.acquire()
            sched.release(lease)
        except RequestError as exc:
            result["error"] = exc.code

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(1.0)
    assert not thread.is_alive()
    assert result["error"] == "BROWSER_QUEUE_TIMEOUT"
    assert sched.snapshot()["depth"] == 0

    sched.release(active)
    lease = sched.acquire()
    sched.release(lease)


def test_expired_match_deadline_does_not_take_free_slot_or_enqueue() -> None:
    sched = BrowserScheduler(capacity=1, queue_capacity=2, queue_timeout_ms=1_000)
    with browser_queue_deadline(time.monotonic() - 0.01):
        with pytest.raises(RequestError) as excinfo:
            sched.acquire()

    assert excinfo.value.code == "BROWSER_QUEUE_TIMEOUT"
    snapshot = sched.snapshot()
    assert snapshot["active"] == 0
    assert snapshot["depth"] == 0
    assert snapshot["total_acquisitions"] == 0
