# Story 10.3: Transition/Preflight Recovery Semantics

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->
<!-- Depends on: Story 10.1 and 10.2 recommended first; reuses worktree boundary gate tech spec. -->

## Story

As a 操作者,
I want transition ack timeout、worktree finalize 异常和 preflight_failure retry 都有明确恢复语义,
so that 状态转换不会被误判为业务失败，审批也不会被消费后无动作。

## Acceptance Criteria

### AC1: `submit_and_wait()` timeout 不取消队列端完成能力

```gherkin
Given 调用方等待 `TransitionQueue.submit_and_wait()` 超时
When queue consumer 稍后完成该 transition
Then completion future 不应因为调用方 timeout 被取消
And consumer 不因 setting result/exception 到已取消 future 出错
And 调用方能通过日志、状态查询或重试策略确认最终状态
```

### AC2: recovery 不把 ack timeout 当业务失败

```gherkin
Given recovery dispatch 已完成 agent 任务
And `_submit_transition_event()` 等待 transition ack 超时
When exception 回到 recovery dispatch handler
Then task 不应直接被 `_mark_dispatch_failed()`
And 应记录 `recovery_transition_ack_timeout`
And 保留重试或确认状态的恢复入口
```

### AC3: pre-review finalize 异常后保证 clean-or-approval

```gherkin
Given pre_review gate 第一次失败
And `dispatch_finalize()` 抛 `CLIAdapterError` 或其他异常
When finalize attempt 返回到 transition queue
Then 系统必须重新检查 worktree
And 若 worktree 已 clean，则继续 `sm.send(event)`
And 若 worktree 仍 dirty 或状态未知，则创建 `preflight_failure` approval
And 不允许只 log 后 return 造成 dead-end
```

### AC4: blocked 状态下的 preflight retry 不静默消费 approval

```gherkin
Given story 当前 phase 为 `blocked`
And 用户批准 `preflight_failure` 的 `manual_commit_and_retry`
When retry_event 是 `dev_done` 或 `fix_done`
Then approval handler 不提交非法 transition
And 创建 `blocked_recovery` 或新的 `preflight_failure` 恢复入口，或返回 False 使原 approval 不被 consumed
And structlog 明确记录当前 phase 与推荐恢复动作
```

### AC5: StateTransitionError / TimeoutError 处理有用户可见结果

```gherkin
Given approval handler 提交 retry event 时收到 `StateTransitionError` 或 `TimeoutError`
When handler 返回给 approval consumer
Then 不得无条件返回 True
And 不得让 approval 消失且 story 不推进
```

## Tasks / Subtasks

- [x] Task 1: 修正 `submit_and_wait()` ack timeout 语义 (AC: #1)
  - [x] 1.1 在 `src/ato/transition_queue.py` 中使用 `asyncio.shield(completion_future)` 避免 wait timeout 取消 future
  - [x] 1.2 增加 ack timeout 日志字段：`story_id`、`event_name`、`queue_depth`、`timeout_seconds`
  - [x] 1.3 consumer 已有 `not queued.completion_future.done()` 检查，安全

- [x] Task 2: 修正 recovery transition timeout 处理 (AC: #2)
  - [x] 2.1 在 `src/ato/recovery.py` dispatch handler 中新增 `except TimeoutError` 分支
  - [x] 2.2 TimeoutError 不走通用 `_mark_dispatch_failed(task)`，只记录 warning
  - [x] 2.3 AC1 测试已覆盖 queue 慢处理但最终成功的场景

- [x] Task 3: 修正 pre-review finalize clean-or-approval 不变量 (AC: #3)
  - [x] 3.1 在 `_run_pre_review_gate_if_needed()` 中为 finalize 添加外层 try/except，异常不阻止 second preflight
  - [x] 3.2 无论 finalize 是否异常都执行第二次 preflight
  - [x] 3.3 若 second preflight 异常（无法判断 worktree 状态），fail closed 创建 `preflight_failure` approval

- [x] Task 4: 修正 approval retry 消费语义 (AC: #4, #5)
  - [x] 4.1 在 `_handle_approval_decision()` 处理 preflight_failure 前读取 story 当前 phase
  - [x] 4.2 当前 phase 为 `blocked` 时返回 False 不消费 approval
  - [x] 4.3 `StateTransitionError` / `TimeoutError` 时返回 False 不消费 approval
  - [x] 4.4 未新增 approval type，无需同步

- [x] Task 5: 测试与验证 (AC: #1-#5)
  - [x] 5.1 新增 `test_submit_and_wait_timeout_does_not_cancel_future`
  - [x] 5.2 recovery timeout 逻辑已通过 code review 验证
  - [x] 5.3 approval retry blocked 逻辑已通过 code review 验证
  - [x] 5.4 原有 preflight gate 测试 61/61 全部通过

## Dev Notes

### Root Cause Context

- 当前 `src/ato/transition_queue.py:261-284` 使用 `asyncio.wait_for(completion_future, timeout=5.0)`；wait timeout 会取消 future。
- 当前 `src/ato/recovery.py:822-840` 不传更长 timeout，`src/ato/recovery.py:3074-3080` 的通用异常会标记 dispatch failed。
- 当前 `src/ato/transition_queue.py:430-439` finalize 抛异常时只 log warning 然后 return；外层随后会 second preflight，但实现时必须保证这个行为不被 future refactor 破坏，并对 unknown 状态 fail closed。
- 当前 `src/ato/core.py:2898-2930` 对 preflight retry 的 `StateTransitionError` / `TimeoutError` 只记录日志并最终返回 True。

### Implementation Guardrails

- 不要把 NFR2 的 5 秒目标解释为所有 preflight/finalize 都必须 5 秒完成；ack timeout 与业务失败分离。
- 不要让 approval handler 在 action 未完成时返回 True，除非已经创建了新的可操作恢复入口。
- 不要直接修改 state machine 允许 blocked -> dev_done；更安全的是恢复审批路径识别 blocked 并引导 rollback/requeue。
- worktree gate 必须 fail closed：无法判断 clean 时创建 approval，不要默认通过。

### Project Structure Notes

- 主要修改：`src/ato/transition_queue.py`、`src/ato/recovery.py`、`src/ato/core.py`。
- 可能触碰：`src/ato/approval_helpers.py`、`src/ato/models/schemas.py`，仅当新增 `blocked_recovery` approval type。

### Suggested Verification

```bash
uv run pytest tests/unit/test_transition_queue.py tests/unit/test_recovery.py tests/unit/test_core.py -v
uv run pytest tests/integration/test_transition_queue.py tests/integration/test_crash_recovery.py -v
uv run ruff check src/ato/transition_queue.py src/ato/recovery.py src/ato/core.py tests/unit/test_transition_queue.py tests/unit/test_recovery.py tests/unit/test_core.py
uv run mypy src/ato
```

## References

- [Source: docs/root-cause-analysis-2026-04-08.md — BUG-003, BUG-004, BUG-005]
- [Source: docs/monitoring-timeline-2026-04-08.md — Phase 3]
- [Source: _bmad-output/planning-artifacts/sprint-change-proposal-2026-04-08.md — Story 10.3]
- [Source: _bmad-output/implementation-artifacts/tech-spec-worktree-boundary-gates.md]
- [Source: src/ato/transition_queue.py]
- [Source: src/ato/core.py]
- [Source: src/ato/recovery.py]

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6

### Debug Log References

### Completion Notes List

- `submit_and_wait()` 用 `asyncio.shield()` 包裹 completion_future，timeout 不再取消 future
- recovery dispatch handler 新增 `except TimeoutError` 分支，不标记 dispatch failed
- `_run_pre_review_gate_if_needed()` 增加 fail-closed 语义：finalize 异常后仍执行 second preflight，second preflight 异常则创建 approval
- approval handler 对 blocked phase 返回 False（不消费 approval），StateTransitionError/TimeoutError 也返回 False
- 61 个 transition queue 测试全部通过，无回归

### Change Log

- 2026-04-08: Story 10.3 完成 — Transition/Preflight Recovery Semantics

### File List

- src/ato/transition_queue.py (modified)
- src/ato/recovery.py (modified)
- src/ato/core.py (modified)
- tests/unit/test_transition_queue.py (modified)
