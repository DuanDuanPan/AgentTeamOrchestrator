# Story 10.1-10.6 Code Review Report

Date: 2026-04-09
Reviewer: Codex
Method: `bmad-code-review` workflow
Scope:
- Story 10.1 `terminal-finalizer-dead-pid-watchdog`
- Story 10.2 `claude-result-first-semantics`
- Story 10.3 `transition-preflight-recovery-semantics`
- Story 10.4 `bmad-parser-reliability`
- Story 10.5 `merge-queue-boundary-hygiene`
- Story 10.6 `incident-regression-suite`

## Findings

### Patch

1. Dead PID watchdog only exists as a helper and is not wired into any production polling path.
   - Severity: patch
   - Story/AC: 10.1 AC4
   - Evidence:
     - Implementation exists in `src/ato/subprocess_mgr.py:569`
     - No production caller exists for `sweep_dead_workers()`
     - Current references are tests only
   - Risk:
     - Dead worker PIDs are never detected at runtime
     - Tasks can still remain stale without explicit manual intervention

2. BMAD explicit-pass fast-path can approve incomplete checkpoint output.
   - Severity: patch
   - Story/AC: 10.4 AC1
   - Evidence:
     - Fast-path runs in `src/ato/adapters/bmad_adapter.py:221`
     - Local reproduction:
       - Input: `No blocking findings. Checkpoint: continue to next step?`
       - Output: `approved`, `deterministic`, `[]`
   - Risk:
     - Partial review output can bypass confirmation and be treated as final approval

3. BMAD explicit-pass fast-path drops suggestion findings.
   - Severity: patch
   - Story/AC: 10.4 AC1, 10.4 AC5
   - Evidence:
     - Fast-path returns `approved` with empty findings in `src/ato/adapters/bmad_adapter.py:230`
     - Local reproduction:
       - Input: `Recommendation: Approve` plus a suggestion bullet
       - Output: zero findings
   - Risk:
     - Suggestion findings disappear from persisted review results and downstream prompts

4. `submit_and_wait()` timeout can leave an unretrieved future exception behind.
   - Severity: patch
   - Story/AC: 10.3 AC1
   - Evidence:
     - Wait path uses `asyncio.shield()` in `src/ato/transition_queue.py:283`
     - Consumer later calls `set_exception()` in `src/ato/transition_queue.py:352`
     - Local reproduction produced: `Future exception was never retrieved`
   - Risk:
     - Runtime logs are polluted after ack timeout when the eventual transition fails

5. Dead PID fallback writes failed tasks with `exit_code = 0`.
   - Severity: patch
   - Story/AC: 10.1 AC4
   - Evidence:
     - `_fallback_update_task()` sets `exit_code = 0` whenever `adapter_exc is None` in `src/ato/subprocess_mgr.py:542`
     - `sweep_dead_workers()` forces failed status while passing `adapter_exc=None` in `src/ato/subprocess_mgr.py:594`
     - Local reproduction stored `('failed', 0)` in SQLite
   - Risk:
     - Failure diagnostics and any exit-code-based reporting become inconsistent

6. Shared porcelain parser mishandles paths with spaces.
   - Severity: patch
   - Story/AC: 10.5 AC3
   - Evidence:
     - Parser implementation in `src/ato/worktree_mgr.py:35`
     - Real porcelain output for untracked file with spaces is quoted
     - Local reproduction returned `"file with space.txt"` including quotes
   - Risk:
     - Finalize prompts and dirty-file reporting can carry malformed paths

7. Parse-failed diagnostics still omit `timeout_seconds`.
   - Severity: patch
   - Story/AC: 10.4 AC3
   - Evidence:
     - Failure log in `src/ato/adapters/bmad_adapter.py:294` includes `input_length` and `timeout_related`, but not `timeout_seconds`
     - Stage 3 parse error in `src/ato/adapters/bmad_adapter.py:306` omits `timeout_seconds`
     - Approval payload in `src/ato/adapters/bmad_adapter.py:99` omits `timeout_seconds`
   - Risk:
     - Parser timeout incidents remain harder to diagnose from approval payloads and logs

8. Story 10.3 behavior change is not reflected in the unit-test baseline.
   - Severity: patch
   - Story/AC: 10.3 AC5
   - Evidence:
     - Production logic returns `False` on blocked retry / transition error / timeout in `src/ato/core.py:2917`, `src/ato/core.py:2940`, `src/ato/core.py:2949`
     - Existing test still asserts `result is True` in `tests/unit/test_core.py:1994`
     - Local run:
       - `uv run pytest tests/unit/test_core.py -k 'consumes_when_retry_blocks_again or consumes_when_retry_submit_times_out' -q`
       - One test fails
   - Risk:
     - Test suite no longer matches the implemented contract

9. Incident regression suite does not include all scenarios claimed in the story notes.
   - Severity: patch
   - Story/AC: 10.6 AC2, 10.6 AC4
   - Evidence:
     - Regression module covers BUG001/002/003/007 in `tests/integration/test_incident_2026_04_08.py`
     - It does not include the `finalize + git truth` regression path or the `blocked preflight retry` regression path
   - Risk:
     - Story notes overstate automated coverage
     - Future regressions in those paths can slip through targeted incident verification

## Validation

Executed:

```bash
uv run pytest tests/unit/test_subprocess_mgr.py tests/unit/test_claude_adapter.py tests/unit/test_transition_queue.py tests/unit/test_bmad_adapter.py tests/unit/test_initial_dispatch.py tests/integration/test_incident_2026_04_08.py
uv run ruff check src/ato/adapters/bmad_adapter.py src/ato/adapters/claude_cli.py src/ato/config.py src/ato/core.py src/ato/merge_queue.py src/ato/recovery.py src/ato/subprocess_mgr.py src/ato/transition_queue.py src/ato/worktree_mgr.py tests/unit/test_bmad_adapter.py tests/unit/test_claude_adapter.py tests/unit/test_initial_dispatch.py tests/unit/test_subprocess_mgr.py tests/unit/test_transition_queue.py tests/integration/test_incident_2026_04_08.py
uv run pytest tests/unit/test_core.py -k 'consumes_when_retry_blocks_again or consumes_when_retry_submit_times_out' -q
```

Results:
- Targeted story test set passed: `209 passed`
- Ruff passed
- Additional focused `test_core.py` check exposed a stale expectation failure

## Summary

- intent_gap: 0
- bad_spec: 0
- patch: 9
- defer: 0
- rejected as noise: 2
