# BMAD Code Review — Story 2B.1: Claude CLI Adapter Implementation

**Reviewer:** Code-Review Skill v6.2.0
**Date:** 2026-03-25
**Scope:** `src/ato/adapters/claude_cli.py`, `tests/unit/test_claude_cli.py`

---

## Defer

### 1. Pre-existing: Timeout configuration not externalized

The Claude CLI adapter uses a hardcoded 300-second timeout for subprocess execution. This value was inherited from the initial scaffolding and is not configurable via environment variable or configuration file. While acceptable for current story scope, this should be externalized before Epic 4 (multi-story parallel execution) where different task types may require different timeout values.

**Tracking:** Consider adding to Epic 4 backlog as a configuration story.

### 2. Pre-existing: No retry logic for transient CLI failures

The adapter does not implement retry logic for transient failures such as network timeouts or temporary OAuth token refresh failures. The `claude -p` command can occasionally fail with exit code 1 due to authentication token expiry mid-session. A simple exponential backoff retry (max 3 attempts) would improve reliability.

**Tracking:** This is a known limitation documented in the architecture decision log. Retry logic is planned for Epic 5 (resilience hardening).

---

**Summary:** 0 intent_gap, 0 bad_spec, 0 patch, 2 defer findings. 0 findings rejected as noise.
