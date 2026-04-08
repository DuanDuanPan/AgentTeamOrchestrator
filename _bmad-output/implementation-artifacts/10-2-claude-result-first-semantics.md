# Story 10.2: Claude Result-First Semantics

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->
<!-- Depends on: Story 10.1 should be implemented first; this story can be prepared now but should be dev-sequenced after 10.1. -->

## Story

As a 操作者,
I want Claude stream-json 中的 `type: result` 被视为业务完成信号,
so that `exit_code=1` 但结果完整时不会误触发 crash recovery 或重复调度。

## Acceptance Criteria

### AC1: `type: result` 优先于 process exit code

```gherkin
Given Claude stream-json 已收到 `type: result`
And process exit code 为 1
When `ClaudeAdapter.execute()` 完成
Then 返回 `ClaudeOutput` 业务成功结果
And 不抛 `CLIAdapterError`
And 记录 warning `claude_nonzero_exit_with_result`
```

### AC2: 无 result 的非零退出码仍按错误处理

```gherkin
Given Claude stream-json 未收到 `type: result`
And process exit code 非 0
When `ClaudeAdapter.execute()` 完成
Then 仍调用 `_classify_error(exit_code, stderr)`
And 抛出 `CLIAdapterError`
And 保留现有 retryable/error_category 语义
```

### AC3: 返回对象不能被标记为业务 failure

```gherkin
Given result 已存在但 process exit code 非 0
When 构造 `ClaudeOutput`
Then 返回对象的 `status` 对上层应表现为 `success`
And `SubprocessManager` 不应把 task 写成 failed
And 原始 process exit code 不应丢失，可记录为 warning metadata 或结构化日志
```

### AC4: stderr 不覆盖有效 result

```gherkin
Given result 存在且 stderr 非空
When adapter 完成
Then stderr 只作为 warning 记录
And `text_result`、`structured_output`、`total_cost_usd`、token usage 保持来自 result envelope
```

### AC5: fixture 与 stream 测试覆盖

```gherkin
Given mock stream 输出 result 后退出码为 1
When 单测执行 adapter
Then 测试断言返回 success、cost/text_result 保留、无 `CLIAdapterError`
```

## Tasks / Subtasks

- [x] Task 1: 修改 `ClaudeAdapter.execute()` result-first 控制流 (AC: #1, #2, #4)
  - [x] 1.1 在 `src/ato/adapters/claude_cli.py` 中将 `result_data is not None` 的判断放在非零 exit code 抛错之前
  - [x] 1.2 无 result 时保留当前 `exit_code != 0` 错误分类和 progress error 事件
  - [x] 1.3 result 存在且 exit code 非 0 时记录 warning，包含 `exit_code`、`stderr_preview`、`session_id`、`cost_usd`

- [x] Task 2: 明确 process exit code 持久化策略 (AC: #3)
  - [x] 2.1 优先评估在 `AdapterResult` / `ClaudeOutput` 中新增 `process_exit_code` 或 warnings 字段
  - [x] 2.2 本轮不扩 schema，对 result+nonzero path 调用 `ClaudeOutput.from_json(result_data, exit_code=0)` 并用日志保留原始 process exit code
  - [x] 2.3 不返回 `ClaudeOutput.from_json(result_data, exit_code=1)`，避免 `schemas.py` 把 status 设为 `failure`

- [x] Task 3: 更新测试 (AC: #1-#5)
  - [x] 3.1 在 `tests/unit/test_claude_adapter.py` 增加 result+exit_code=1 场景
  - [x] 3.2 增加 result+exit_code=1+stderr 场景，验证 warning 而非 error
  - [x] 3.3 保留无 result+exit_code=1 抛 `CLIAdapterError` 的测试
  - [x] 3.4 无新增 schema 字段，无需更新 test_schemas.py

## Dev Notes

### Root Cause Context

- 当前 `src/ato/adapters/claude_cli.py:352-379` 先看 `exit_code != 0`，再处理 result。这样会丢弃已解析的 `result_data`。
- 当前 `src/ato/models/schemas.py:621-645` 的 `ClaudeOutput.from_json(..., exit_code=1)` 会生成 `status="failure"`。实现时必须避免这个上层可见语义。
- 监控中多次出现 stderr 为空、result/cost 完整、exit_code=1，随后触发 crash recovery。

### Implementation Guardrails

- 不要把所有非零 exit code 都忽略；只有 `result_data is not None` 时才降级为 warning。
- 不要改变 auth/rate-limit/timeout/parse_error 的分类行为。
- 不要把 process exit code 混入业务 `exit_code`，除非同步调整所有下游消费者。
- 如果新增 `process_exit_code`，要检查 cost_log、TUI story detail、tests fixtures 的兼容性。

### Project Structure Notes

- 主要修改：`src/ato/adapters/claude_cli.py` 和可能的 `src/ato/models/schemas.py`。
- 测试集中在 `tests/unit/test_claude_adapter.py`；schema 字段变化才扩展 `tests/unit/test_schemas.py`。

### Suggested Verification

```bash
uv run pytest tests/unit/test_claude_adapter.py -v
uv run pytest tests/unit/test_schemas.py -v
uv run ruff check src/ato/adapters/claude_cli.py src/ato/models/schemas.py tests/unit/test_claude_adapter.py
uv run mypy src/ato
```

## References

- [Source: docs/root-cause-analysis-2026-04-08.md — BUG-002]
- [Source: docs/bug-report-2026-04-08.md — BUG-002]
- [Source: _bmad-output/planning-artifacts/sprint-change-proposal-2026-04-08.md — Story 10.2]
- [Source: _bmad-output/implementation-artifacts/2b-1-claude-agent-dispatch.md]
- [Source: src/ato/adapters/claude_cli.py]
- [Source: src/ato/models/schemas.py]

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6

### Debug Log References

### Completion Notes List

- 重构 `ClaudeAdapter.execute()` 终态控制流：`result_data is not None` 判断提前到 `exit_code != 0` 之前
- result+nonzero exit 路径：调用 `ClaudeOutput.from_json(result_data, exit_code=0)` 确保 `status="success"`
- 原始 process exit code 通过 structlog warning `claude_nonzero_exit_with_result` 保留审计
- 无 result+nonzero exit 路径保持不变：正常错误分类 + CLIAdapterError
- 更新原有 `test_stream_error_emits_error_event` → 改为无 result 场景
- 新增 5 个 result-first 测试，35 个总测试全部通过
- ruff 和 mypy strict 全部通过

### Change Log

- 2026-04-08: Story 10.2 完成 — Claude Result-First Semantics

### File List

- src/ato/adapters/claude_cli.py (modified)
- tests/unit/test_claude_adapter.py (modified)
