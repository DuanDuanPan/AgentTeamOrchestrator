# BMAD Code Review — Story 2A.3: Orchestrator Event Loop Start/Stop

**Reviewer:** Code-Review Skill v6.2.0
**Date:** 2026-03-25
**Scope:** `src/ato/core.py`, `src/ato/state_machine.py`, `tests/unit/test_core.py`

---

## Intent Gaps

### 1. Missing graceful shutdown on SIGTERM

The orchestrator event loop registers a handler for SIGINT but does not handle SIGTERM. In production-like environments (e.g., systemd or container runtimes), SIGTERM is the standard shutdown signal. Without handling it, the process will be killed immediately, bypassing the graceful shutdown path and potentially leaving SQLite WAL files in an inconsistent state.

**Impact:** Orchestrator may lose in-flight state transitions during forced termination.

### 2. Recovery window not enforced on startup

The architecture document specifies a 30-second recovery window (NFR-08). The current implementation calls `recover_incomplete_tasks()` but does not enforce a timeout. If recovery takes longer than 30 seconds due to a large backlog of incomplete tasks, the system will block indefinitely on startup without logging a warning or proceeding with partial recovery.

**Impact:** Startup may hang under degraded conditions, violating the recovery SLA.

---

## Bad Spec

### 1. Ambiguous idle-poll interval specification

The PRD (FR-12) states the orchestrator should "poll for pending transitions at a reasonable interval" without defining bounds. The implementation uses a hardcoded 500ms interval, but the architecture document suggests 100ms for responsiveness. Neither document provides a definitive value or configuration mechanism.

**Recommendation:** Clarify the expected polling interval in the PRD or make it a configurable parameter with a documented default.

---

## Patch

### 1. Unguarded access to `_running` flag in `shutdown()` — `src/ato/core.py:42`

The `shutdown()` method reads `self._running` without holding the lock that protects it in `start()`. This creates a potential race condition if `shutdown()` is called from a signal handler while `start()` is still initializing.

```python
# Current (line 42):
if not self._running:
    return

# Suggested fix:
async with self._lock:
    if not self._running:
        return
    self._running = False
```

---

## Defer

### 1. Consider structured logging migration

The current implementation uses `logging.getLogger(__name__)` with basic string formatting. While functional, migrating to `structlog` would provide better JSON-serializable log output that aligns with the audit trail requirements (NFR-11). This is not blocking for the current story but should be considered for Epic 3.

---

**Summary:** 2 intent_gap, 1 bad_spec, 1 patch, 1 defer findings. 0 findings rejected as noise.
