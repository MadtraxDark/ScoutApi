"""Structured hang / wall-timeout codes for Product Match runs."""

from __future__ import annotations

# Docker / process hard-exit when the hang watchdog fires.
# Documented; do not inline magic numbers at call sites.
MATCH_WORKER_HANG_EXIT_CODE = 78

# Store exceeded MATCH_STORE_WALL_TIMEOUT_SECONDS (cooperative).
# Never map to NO_MATCH.
FAILURE_CODE_STORE_WALL_TIMEOUT = "STORE_WALL_TIMEOUT"

# Run exceeded MATCH_RUN_WALL_TIMEOUT_SECONDS (cooperative).
# Never map to completed / success.
FAILURE_CODE_RUN_WALL_TIMEOUT = "RUN_WALL_TIMEOUT"
