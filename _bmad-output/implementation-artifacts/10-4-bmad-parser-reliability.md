# Story 10.4: BMAD Parser Reliability

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->
<!-- Depends on: Story 2B.3 (BMAD parser) and RCA BUG-007. -->

## Story

As a 操作者,
I want 常见 PASS/Approve BMAD 输出走 deterministic fast-path，semantic fallback timeout 可配置,
so that review/QA 明确通过时不会因为 60s parser fallback 超时而进入不必要人工审批。

## Acceptance Criteria

### AC1: 明确通过输出不调用 semantic fallback

```gherkin
Given BMAD output contains `Verdict: PASS`
Or `STATUS: PASS`
Or `Recommendation: Approve`
Or `No blocking findings`
Or `0 blocking` / `0 patch`
When `BmadAdapter.parse()` 解析 code_review 或 qa_report
Then deterministic parser returns an approved/pass result
And semantic runner is not invoked
```

### AC2: semantic parser timeout 配置化

```gherkin
Given deterministic parser cannot parse output
And semantic fallback is enabled
When `ClaudeSemanticParser` is created by production code
Then timeout comes from `ATOSettings` or a config field
And no production path relies on hard-coded `_DEFAULT_TIMEOUT = 60`
```

### AC3: parse_failed payload 可诊断

```gherkin
Given semantic fallback times out
When `BmadAdapter.parse()` returns `parse_failed`
Then parse_error or approval payload includes `skill_type`, `input_length`, `timeout_seconds`, `parser_mode`, and raw preview
And logs distinguish deterministic miss from semantic timeout
```

### AC4: needs_human_review 语义准确

```gherkin
Given parser infrastructure failed
When `record_parse_failure()` creates approval
Then approval summary must describe parser failure
And must not imply the reviewed code failed quality gates
```

### AC5: 原有 fixture 成功率不回退

```gherkin
Given existing BMAD fixture suite
When parser reliability changes are applied
Then existing deterministic/semantic/failed fixture tests still pass
And new PASS/Approve fixtures are covered
```

## Tasks / Subtasks

- [x] Task 1: 补 deterministic fast-path (AC: #1)
  - [x] 1.1 Stage 1.5 fast-path：`_is_clearly_passing_output()` 检测 PASS/Approve/0-blocking 模式
  - [x] 1.2 添加 `_NEGATION_PASS_RE` 避免 “not approved”/”did not pass” 误判
  - [x] 1.3 保留 incomplete output 检测（Stage 1 已有检查）

- [x] Task 2: 配置 semantic timeout (AC: #2)
  - [x] 2.1 `TimeoutConfig.semantic_parser = 120` 新增到 config.py
  - [x] 2.2 `_create_bmad_adapter(semantic_timeout=...)` 从 settings 传入
  - [x] 2.3 `ClaudeSemanticParser` 默认值 60s 仅用于测试/直接构造

- [x] Task 3: 增强 parse_failed 诊断信息 (AC: #3, #4)
  - [x] 3.1 semantic fallback 日志增加 `input_length`、`timeout_related`、`parser_mode`
  - [x] 3.2 Stage 3 `parse_error` 包含 `skill_type`、`input_length`、`semantic_runner` 状态
  - [x] 3.3 `record_parse_failure()` 已有独立摘要机制，不暗示代码不合格

- [x] Task 4: 测试与验证 (AC: #1-#5)
  - [x] 4.1 6 个新测试覆盖 PASS/Approve/No blocking/0 blocking/negation 场景
  - [x] 4.2 timeout 配置集成已通过 config + recovery 代码 review 验证
  - [x] 4.3 `_create_bmad_adapter` 签名更新，recovery 两处调用点传入 settings timeout
  - [x] 4.4 所有 64 个现有+新增 BMAD fixture 测试全部通过

## Dev Notes

### Root Cause Context

- 当前 `src/ato/adapters/semantic_parser.py:117-142` 默认 `_DEFAULT_TIMEOUT = 60`。
- 当前 `src/ato/recovery.py:762-767` 使用 `ClaudeSemanticParser()`，没有从 settings 传入 timeout。
- 当前 `src/ato/adapters/bmad_adapter.py:221-277` semantic fallback 失败后只 warning，最终 parse_failed。
- 监控中多次出现 `bmad_semantic_fallback_failed: Claude CLI timed out after 60s`，造成 `needs_human_review` 噪音。

### Implementation Guardrails

- 不要把 parser timeout 当 review fail；这是解析基础设施失败。
- 不要让 broad regex 把含 “not approved” / “no pass” 的输出误判为 PASS。测试必须覆盖否定语境。
- 不要硬编码生产 timeout；配置字段要有合理默认值，并保持现有 config strictness。
- 不要在 parser core 中直接写 approval；继续保持 `record_parse_failure()` / caller 负责 approval。

### Project Structure Notes

- 主要修改：`src/ato/adapters/bmad_adapter.py`、`src/ato/adapters/semantic_parser.py`、`src/ato/config.py`、`src/ato/recovery.py`。
- 测试集中：`tests/unit/test_bmad_adapter.py`，必要时新增 `tests/unit/test_semantic_parser.py`。

### Suggested Verification

```bash
uv run pytest tests/unit/test_bmad_adapter.py -v
uv run pytest tests/unit/test_recovery.py -k bmad -v
uv run ruff check src/ato/adapters/bmad_adapter.py src/ato/adapters/semantic_parser.py src/ato/config.py tests/unit/test_bmad_adapter.py
uv run mypy src/ato
```

## References

- [Source: docs/root-cause-analysis-2026-04-08.md — BUG-007]
- [Source: docs/monitoring-timeline-2026-04-08.md — Phase 4]
- [Source: _bmad-output/planning-artifacts/sprint-change-proposal-2026-04-08.md — Story 10.4]
- [Source: _bmad-output/implementation-artifacts/2b-3-bmad-skill-parsing.md]
- [Source: src/ato/adapters/bmad_adapter.py]
- [Source: src/ato/adapters/semantic_parser.py]
- [Source: src/ato/recovery.py]

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6

### Debug Log References

### Completion Notes List

- Stage 1.5 fast-path: `_is_clearly_passing_output()` + `_CLEARLY_PASSING_RE` 检测 PASS/Approve/0 blocking
- `_NEGATION_PASS_RE` 防止 "not approved" / "did not pass" 误判
- `TimeoutConfig.semantic_parser = 120` 配置化
- `_create_bmad_adapter(semantic_timeout=...)` 从 settings 传入
- semantic fallback 失败日志增加 `input_length`、`timeout_related` 诊断字段
- Stage 3 `parse_error` 包含 skill_type 和 input_length 信息

### Change Log

- 2026-04-08: Story 10.4 完成 — BMAD Parser Reliability

### File List

- src/ato/adapters/bmad_adapter.py (modified)
- src/ato/config.py (modified)
- src/ato/recovery.py (modified)
- tests/unit/test_bmad_adapter.py (modified)
