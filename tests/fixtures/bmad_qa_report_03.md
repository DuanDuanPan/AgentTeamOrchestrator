# Test Quality Review: test_state_machine.py

**Quality Score**: 45/100 (Critical Issues - Needs significant rework)
**Review Date**: 2026-03-25
**Review Scope**: single
**Reviewer**: TEA Agent

---

## Executive Summary

**Overall Assessment**: Critical Issues

**Recommendation**: Block

### Key Strengths
✅ Test file is well-organized into logical groups

### Key Weaknesses
❌ Multiple tests use hard-coded sleep() calls
❌ Tests share mutable state between cases
❌ Missing cleanup in async fixture teardown

### Summary

This test file has fundamental quality issues that could lead to flaky CI runs and false positives. The use of hard-coded sleeps and shared mutable state across tests creates non-deterministic behavior. These must be addressed before merging.

---

## Critical Issues (Must Fix)

### 1. Hard-coded sleep in async state transition tests

**Severity**: P0 (Critical)
**Location**: `tests/unit/test_state_machine.py:67`
**Criterion**: Hard Waits

**Issue Description**: Tests use `await asyncio.sleep(2)` to wait for state transitions instead of using proper async event waiting or polling mechanisms.

### 2. Shared mutable state machine instance across tests

**Severity**: P0 (Critical)
**Location**: `tests/unit/test_state_machine.py:15`
**Criterion**: Isolation

**Issue Description**: A module-level `StateMachine` instance is shared across all test functions, causing test ordering dependencies and failures when run in parallel.

### 3. Missing async fixture cleanup

**Severity**: P0 (Critical)
**Location**: `tests/unit/test_state_machine.py:112`
**Criterion**: Isolation

**Issue Description**: The database connection fixture does not properly close connections in teardown, leading to resource leaks and potential SQLite locking issues in CI.

---

## Decision

**Recommendation**: Block

**Rationale**: Three P0 critical issues affect test reliability and CI stability. All must be resolved before this test file can be trusted.
