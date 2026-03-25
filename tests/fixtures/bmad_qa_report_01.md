# Test Quality Review: test_core.py

**Quality Score**: 72/100 (Fair - Needs improvement)
**Review Date**: 2026-03-24
**Review Scope**: single
**Reviewer**: TEA Agent

---

## Executive Summary

**Overall Assessment**: Fair

**Recommendation**: Request Changes

### Key Strengths
✅ Correct use of pytest-asyncio markers
✅ Descriptive test function names following BDD conventions

### Key Weaknesses
❌ Hard-coded sleep calls in async tests instead of proper event-driven waiting
❌ Non-deterministic test ordering due to shared mutable state
❌ Insufficient edge case coverage for error paths

---

## Quality Criteria Assessment

| Criterion | Status | Violations | Notes |
| --- | --- | --- | --- |
| BDD Format | ✅ PASS | 0 | Good naming conventions |
| Hard Waits | ❌ FAIL | 1 | Uses asyncio.sleep() |
| Determinism | ❌ FAIL | 1 | Random-dependent logic |
| Isolation | ✅ PASS | 0 | Proper fixture scoping |
| Assertions | ✅ PASS | 0 | Clear assert messages |
| Test Coverage | ⚠️ WARN | 1 | Missing edge cases |

**Total Violations**: 2 Critical, 0 High, 1 Medium, 0 Low

---

## Critical Issues (Must Fix)

### 1. Hard-coded sleep in async event loop tests

**Severity**: P0 (Critical)
**Location**: `tests/unit/test_core.py:45`
**Criterion**: Hard Waits

**Issue Description**: The test `test_event_loop_processes_transitions` uses `await asyncio.sleep(2)` to wait for the event loop to process queued transitions. This introduces flakiness and unnecessarily slows down the test suite. Replace with an `asyncio.Event` or poll-based wait with a short timeout.

**Code Reference**:
```python
async def test_event_loop_processes_transitions(orchestrator):
    await orchestrator.enqueue_transition("story-1", "start_work")
    await asyncio.sleep(2)  # ← flaky wait
    assert orchestrator.get_state("story-1") == "in_progress"
```

**Suggested Fix**: Use `asyncio.wait_for()` with a condition check or introduce a completion callback.

### 2. Non-deterministic assertion due to random task selection

**Severity**: P0 (Critical)
**Location**: `tests/unit/test_core.py:92`
**Criterion**: Determinism

**Issue Description**: The test `test_parallel_story_scheduling` relies on `random.choice()` to select which story gets scheduled first, making the assertion order unpredictable. Tests must produce identical results on every run regardless of random seed.

**Code Reference**:
```python
async def test_parallel_story_scheduling(orchestrator, stories):
    scheduled = await orchestrator.schedule_next_batch(stories)
    # This assertion fails intermittently because scheduling order is random
    assert scheduled[0].id == "story-1"
```

**Suggested Fix**: Either mock `random.choice` with a fixed seed or assert on set membership instead of ordered index access.

---

## Recommendations (Should Fix)

### 1. Add error path coverage for invalid state transitions

**Severity**: P2 (Medium)
**Location**: `tests/unit/test_core.py:80`
**Criterion**: Test Coverage

**Issue Description**: No test covers the scenario where `enqueue_transition()` is called with an invalid transition name. The orchestrator should raise `InvalidTransitionError`, but this path is untested.

**Suggested Fix**: Add a test case that asserts the correct exception is raised for invalid transitions.

---

## Decision

**Recommendation**: Request Changes

The two critical issues (hard-coded sleep and non-deterministic assertions) must be resolved before this test file can be considered reliable for CI. The test coverage gap is a secondary concern but should be addressed in this iteration.
