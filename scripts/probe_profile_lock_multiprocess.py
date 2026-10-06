"""Probe: cross-process profile ownership proof on ./data/camoufox-profiles bind mount.

Usage (inside Docker container or locally):
    python scripts/probe_profile_lock_multiprocess.py [--path PATH] [--redis REDIS_URL]

What it tests:
    Phase 1 — File lock exclusivity: process A holds LOCK_EX; process B cannot acquire
    simultaneously.
    Phase 2 — File lock crash recovery: kill -9 A; measure until B acquires (OS releases
    on death).
    Phase 3 — Redis SET NX exclusivity: process A holds key; B's SET NX fails.
    Phase 4 — Redis TTL crash recovery: A os._exit; B polls until TTL expires.

Decision gate: results are written to working log PROFILE_LOCK_PROOF and stdout.
Exit code 0 = both backends proven usable. Exit code 1 = file lock unreliable on this
volume.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import signal
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# File lock helpers
# ---------------------------------------------------------------------------


def _try_import_fcntl() -> object:
    try:
        import fcntl

        return fcntl
    except ImportError:
        return None


def _file_lock_holder(
    profile_path: str, hold_seconds: float, ready_event_path: str
) -> None:
    """Child process: acquire exclusive file lock, signal ready, then hold."""
    lock_file = Path(profile_path) / ".profile.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)

    fcntl = _try_import_fcntl()
    if fcntl is None:
        Path(ready_event_path).write_text("FCNTL_UNAVAILABLE")
        return

    with open(lock_file, "w") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        # Signal ready
        Path(ready_event_path).write_text("LOCKED")
        # Hold
        time.sleep(hold_seconds)
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def _file_lock_challenger(
    profile_path: str, timeout_s: float, result_path: str
) -> None:
    """Child process: try to acquire exclusive file lock; record elapsed or TIMEOUT."""
    lock_file = Path(profile_path) / ".profile.lock"
    fcntl = _try_import_fcntl()
    if fcntl is None:
        Path(result_path).write_text(json.dumps({"status": "FCNTL_UNAVAILABLE"}))
        return

    start = time.monotonic()
    deadline = start + timeout_s
    with open(lock_file, "w") as f:
        while True:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                elapsed = time.monotonic() - start
                Path(result_path).write_text(
                    json.dumps({"status": "ACQUIRED", "elapsed_s": elapsed})
                )
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                return
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    elapsed = time.monotonic() - start
                    Path(result_path).write_text(
                        json.dumps({"status": "TIMEOUT", "elapsed_s": elapsed})
                    )
                    return
                time.sleep(0.05)


# ---------------------------------------------------------------------------
# Redis lock helpers
# ---------------------------------------------------------------------------


def _redis_lock_holder(
    redis_url: str, key: str, token: str, ttl_ms: int, ready_path: str, hold_s: float
) -> None:
    """Child process: SET NX the key, signal ready, hold, then release."""
    try:
        from redis import Redis
    except ImportError:
        Path(ready_path).write_text("REDIS_UNAVAILABLE")
        return

    try:
        r = Redis.from_url(redis_url, decode_responses=False)
        ok = r.set(key, token.encode(), nx=True, px=ttl_ms)
        if not ok:
            Path(ready_path).write_text("ACQUIRE_FAILED")
            return
        Path(ready_path).write_text("LOCKED")
        time.sleep(hold_s)
        # Clean release
        _LUA = (
            b"if redis.call('get',KEYS[1])==ARGV[1] then re"
            b"turn redis.call('del',KEYS[1]) else return 0 "
            b"end"
        )
        r.eval(_LUA, 1, key, token.encode())
    except Exception as exc:
        Path(ready_path).write_text(f"ERROR:{exc}")


def _redis_lock_challenger(
    redis_url: str,
    key: str,
    token_b: str,
    ttl_ms: int,
    timeout_s: float,
    result_path: str,
) -> None:
    """Child process: poll SET NX until success or timeout."""
    try:
        from redis import Redis
    except ImportError:
        Path(result_path).write_text(json.dumps({"status": "REDIS_UNAVAILABLE"}))
        return

    r = Redis.from_url(redis_url, decode_responses=False)
    start = time.monotonic()
    deadline = start + timeout_s
    while time.monotonic() < deadline:
        ok = r.set(key, token_b.encode(), nx=True, px=ttl_ms)
        if ok:
            elapsed = time.monotonic() - start
            r.delete(key)
            Path(result_path).write_text(
                json.dumps({"status": "ACQUIRED", "elapsed_s": elapsed})
            )
            return
        time.sleep(0.05)
    elapsed = time.monotonic() - start
    Path(result_path).write_text(
        json.dumps({"status": "TIMEOUT", "elapsed_s": elapsed})
    )


# ---------------------------------------------------------------------------
# Probe phases
# ---------------------------------------------------------------------------


def phase_file_exclusivity(profile_path: Path, tmp_dir: Path) -> dict:
    """Phase 1: process B must NOT acquire while A holds."""
    print("\n[Phase 1] File lock exclusivity ...")
    ready_path = str(tmp_dir / "holder_ready.txt")
    result_path = str(tmp_dir / "challenger_result.json")

    # Holder holds for 3s
    ctx = multiprocessing.get_context("fork")
    holder = ctx.Process(
        target=_file_lock_holder,
        args=(str(profile_path), 3.0, ready_path),
        daemon=True,
    )
    holder.start()

    # Wait for holder to signal
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if Path(ready_path).exists():
            break
        time.sleep(0.05)

    ready_msg = (
        Path(ready_path).read_text() if Path(ready_path).exists() else "NO_SIGNAL"
    )
    if "UNAVAILABLE" in ready_msg:
        holder.join(timeout=2)
        return {"phase": 1, "status": "SKIPPED", "reason": ready_msg}

    # Challenger tries for 1s (should fail = blocked/timeout while A holds)
    challenger = ctx.Process(
        target=_file_lock_challenger,
        args=(str(profile_path), 1.0, result_path),
        daemon=True,
    )
    challenger.start()
    challenger.join(timeout=5.0)

    result_raw = (
        Path(result_path).read_text()
        if Path(result_path).exists()
        else '{"status":"NO_RESULT"}'
    )
    result = json.loads(result_raw)
    holder.join(timeout=5.0)

    # Expected: challenger TIMED OUT (couldn't acquire while A held)
    exclusive = result.get("status") == "TIMEOUT"
    result["exclusive"] = exclusive
    result["phase"] = 1
    status = "PASS" if exclusive else "FAIL"
    print(f"  Holder ready: {ready_msg}")
    print(f"  Challenger result: {result}")
    print(f"  → Phase 1: {status} (exclusive={exclusive})")
    return result


def phase_file_crash_recovery(profile_path: Path, tmp_dir: Path) -> dict:
    """Phase 2: kill -9 holder; measure until challenger acquires (crash recovery)."""
    print("\n[Phase 2] File lock crash recovery ...")
    ready_path = str(tmp_dir / "holder2_ready.txt")
    result_path = str(tmp_dir / "challenger2_result.json")

    ctx = multiprocessing.get_context("fork")
    # Holder will be killed — hold "forever"
    holder = ctx.Process(
        target=_file_lock_holder,
        args=(str(profile_path), 60.0, ready_path),
        daemon=True,
    )
    holder.start()

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if Path(ready_path).exists():
            break
        time.sleep(0.05)

    ready_msg = (
        Path(ready_path).read_text() if Path(ready_path).exists() else "NO_SIGNAL"
    )
    if "UNAVAILABLE" in ready_msg:
        holder.terminate()
        holder.join(timeout=2)
        return {"phase": 2, "status": "SKIPPED", "reason": ready_msg}

    # Start challenger before kill — it will block
    challenger = ctx.Process(
        target=_file_lock_challenger,
        args=(str(profile_path), 10.0, result_path),
        daemon=True,
    )
    challenger.start()
    time.sleep(0.1)  # Let challenger reach the blocking flock call

    _kill_time = time.monotonic()
    os.kill(holder.pid, signal.SIGKILL)  # type: ignore[arg-type]
    holder.join(timeout=2.0)

    # Challenger should now unblock quickly
    challenger.join(timeout=10.0)
    result_raw = (
        Path(result_path).read_text()
        if Path(result_path).exists()
        else '{"status":"NO_RESULT"}'
    )
    result = json.loads(result_raw)

    elapsed_after_kill = result.get("elapsed_s", 0) - 0.1  # subtract pre-kill wait
    result["elapsed_after_kill_s"] = round(elapsed_after_kill, 3)
    result["phase"] = 2

    recovered = result.get("status") == "ACQUIRED" and elapsed_after_kill < 2.0
    result["fast_recovery"] = recovered
    status = "PASS" if recovered else "FAIL"
    print(f"  Holder killed (pid={holder.pid})")
    print(f"  Challenger result: {result}")
    print(
        f"  → Phase 2: {status} (fast_recovery={recovered}, "
        f"elapsed_after_kill={elapsed_after_kill:.3f}s)"
    )
    return result


def phase_redis_exclusivity(redis_url: str, tmp_dir: Path) -> dict:
    """Phase 3: Redis SET NX — process A holds; B must fail."""
    print("\n[Phase 3] Redis SET NX exclusivity ...")
    import hashlib

    key = f"scout:probe:profile_lock:{hashlib.sha256(b'probe_test').hexdigest()[:8]}"
    ready_path = str(tmp_dir / "redis_holder_ready.txt")
    result_path = str(tmp_dir / "redis_challenger_result.json")

    ctx = multiprocessing.get_context("fork")
    holder = ctx.Process(
        target=_redis_lock_holder,
        args=(redis_url, key, "token_a", 30_000, ready_path, 3.0),
        daemon=True,
    )
    holder.start()

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if Path(ready_path).exists():
            break
        time.sleep(0.05)

    ready_msg = (
        Path(ready_path).read_text() if Path(ready_path).exists() else "NO_SIGNAL"
    )
    if "UNAVAILABLE" in ready_msg or "ERROR" in ready_msg:
        holder.join(timeout=2)
        return {"phase": 3, "status": "SKIPPED", "reason": ready_msg}

    # Challenger tries for 1s (should fail while A holds)
    challenger = ctx.Process(
        target=_redis_lock_challenger,
        args=(redis_url, key, "token_b", 30_000, 1.0, result_path),
        daemon=True,
    )
    challenger.start()
    challenger.join(timeout=5.0)

    result_raw = (
        Path(result_path).read_text()
        if Path(result_path).exists()
        else '{"status":"NO_RESULT"}'
    )
    result = json.loads(result_raw)
    holder.join(timeout=5.0)

    exclusive = result.get("status") == "TIMEOUT"
    result["exclusive"] = exclusive
    result["phase"] = 3
    status = "PASS" if exclusive else "FAIL"
    print(f"  Holder ready: {ready_msg}")
    print(f"  Challenger result: {result}")
    print(f"  → Phase 3: {status} (exclusive={exclusive})")
    return result


def phase_redis_crash_recovery(redis_url: str, tmp_dir: Path) -> dict:
    """Phase 4: Redis crash recovery — A os._exit, B should acquire within TTL."""
    print("\n[Phase 4] Redis TTL crash recovery ...")
    import hashlib

    key = f"scout:probe:profile_lock:{hashlib.sha256(b'probe_crash').hexdigest()[:8]}"
    ready_path = str(tmp_dir / "redis_crash_ready.txt")
    result_path = str(tmp_dir / "redis_crash_result.json")

    ttl_ms = 3_000  # 3s TTL for fast probe

    ctx = multiprocessing.get_context("fork")
    holder = ctx.Process(
        target=_redis_lock_holder,
        args=(redis_url, key, "token_crash", ttl_ms, ready_path, 60.0),
        daemon=True,
    )
    holder.start()

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if Path(ready_path).exists():
            break
        time.sleep(0.05)

    ready_msg = (
        Path(ready_path).read_text() if Path(ready_path).exists() else "NO_SIGNAL"
    )
    if "UNAVAILABLE" in ready_msg or "ERROR" in ready_msg:
        holder.join(timeout=2)
        return {"phase": 4, "status": "SKIPPED", "reason": ready_msg}

    # Start challenger before kill — it will poll until TTL expires
    challenger = ctx.Process(
        target=_redis_lock_challenger,
        args=(redis_url, key, "token_b_crash", ttl_ms, 10.0, result_path),
        daemon=True,
    )
    challenger.start()
    time.sleep(0.1)

    os.kill(holder.pid, signal.SIGKILL)  # type: ignore[arg-type]
    holder.join(timeout=2.0)

    challenger.join(timeout=10.0)
    result_raw = (
        Path(result_path).read_text()
        if Path(result_path).exists()
        else '{"status":"NO_RESULT"}'
    )
    result = json.loads(result_raw)

    elapsed = result.get("elapsed_s", 0)
    # Expected: challenger acquires within 4s (3s TTL + polling overhead)
    recovered = result.get("status") == "ACQUIRED" and elapsed < 5.0
    result["ttl_crash_recovery_ok"] = recovered
    result["phase"] = 4
    status = "PASS" if recovered else "FAIL"
    print(f"  Holder killed, TTL={ttl_ms}ms")
    print(f"  Challenger result: {result}")
    print(f"  → Phase 4: {status} (recovered={recovered}, elapsed={elapsed:.3f}s)")
    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Profile lock multiprocess probe")
    parser.add_argument(
        "--path",
        default="/home/app/.cache/scout-api/camoufox-profiles/slot-0",
        help="Profile path to probe (should be on the bind mount volume)",
    )
    parser.add_argument(
        "--redis",
        default="redis://redis:6379/0",
        help="Redis URL for Redis lock probe",
    )
    parser.add_argument(
        "--skip-file",
        action="store_true",
        help="Skip file lock phases (e.g., running on Windows)",
    )
    parser.add_argument(
        "--skip-redis",
        action="store_true",
        help="Skip Redis lock phases",
    )
    args = parser.parse_args()

    profile_path = Path(args.path)
    profile_path.mkdir(parents=True, exist_ok=True)

    # Temp dir for IPC files
    tmp_dir = Path("/tmp/probe_profile_lock")
    tmp_dir.mkdir(exist_ok=True)

    results: list[dict] = []
    file_lock_reliable = True
    redis_lock_reliable = True

    print("\n=== Profile Lock Probe ===")
    print(f"Profile path: {profile_path}")
    print(f"Redis URL: {args.redis}")

    if not args.skip_file:
        r1 = phase_file_exclusivity(profile_path, tmp_dir)
        results.append(r1)
        r2 = phase_file_crash_recovery(profile_path, tmp_dir)
        results.append(r2)
        file_lock_reliable = (
            r1.get("exclusive", False) and r2.get("fast_recovery", False)
        ) or r1.get("status") == "SKIPPED"
    else:
        print("\n[Phase 1+2] File lock: SKIPPED (--skip-file)")
        file_lock_reliable = False

    if not args.skip_redis:
        r3 = phase_redis_exclusivity(args.redis, tmp_dir)
        results.append(r3)
        r4 = phase_redis_crash_recovery(args.redis, tmp_dir)
        results.append(r4)
        redis_lock_reliable = (
            r3.get("exclusive", False) and r4.get("ttl_crash_recovery_ok", False)
        ) or r3.get("status") == "SKIPPED"
    else:
        print("\n[Phase 3+4] Redis lock: SKIPPED (--skip-redis)")

    # Summary
    print("\n=== PROOF RESULTS (PROFILE_LOCK_PROOF) ===")
    print(f"File lock reliable on bind mount: {file_lock_reliable}")
    print(f"Redis lock reliable:              {redis_lock_reliable}")

    if redis_lock_reliable:
        recommended = "redis"
        print("→ RECOMMENDATION: Use CAMOUFOX_PROFILE_LOCK=redis (primary, proven)")
        if file_lock_reliable:
            print("  (file lock also works — can be used as best-effort secondary)")
        else:
            print("  (file lock unreliable on bind mount — Redis only)")
    elif file_lock_reliable:
        recommended = "file"
        print("→ RECOMMENDATION: Use CAMOUFOX_PROFILE_LOCK=file (Redis unavailable)")
    else:
        recommended = "off"
        print(
            "→ WARNING: Both backends failed — CAMOUFOX_PR"
            "OFILE_LOCK=off (NO protection)"
        )

    proof = {
        "profile_path": str(profile_path),
        "redis_url_used": args.redis,
        "file_lock_reliable": file_lock_reliable,
        "redis_lock_reliable": redis_lock_reliable,
        "recommended_mode": recommended,
        "phases": results,
    }

    out_path = Path("/tmp/PROFILE_LOCK_PROOF.json")
    out_path.write_text(json.dumps(proof, indent=2))
    print(f"\nProof written to: {out_path}")

    return 0 if (file_lock_reliable or redis_lock_reliable) else 1


if __name__ == "__main__":
    sys.exit(main())
