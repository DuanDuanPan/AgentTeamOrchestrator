---
title: 'qa_testing V1 protocol-invalid fail-closed'
slug: 'qa-testing-v1-protocol-invalid-fail-closed'
created: '2026-04-09'
status: 'ready-for-dev'
stepsCompleted: [1, 2, 3, 4]
tech_stack:
  - 'Python 3.11+'
  - 'Pydantic v2 strict models'
  - 'asyncio + aiosqlite recovery pipeline'
  - 'pytest + pytest-asyncio'
files_to_modify:
  - 'src/ato/models/schemas.py'
  - 'src/ato/adapters/bmad_adapter.py'
  - 'src/ato/recovery.py'
  - 'src/ato/merge_queue.py'
  - 'src/ato/approval_helpers.py'
  - 'src/ato/test_policy_audit.py'
  - 'tests/unit/test_bmad_adapter.py'
  - 'tests/unit/test_recovery.py'
  - 'tests/unit/test_merge_queue.py'
  - 'tests/unit/test_exception_approval_panel.py'
  - 'tests/unit/test_core.py'
  - 'tests/unit/test_test_policy_audit.py'
code_patterns:
  - 'BmadAdapter.parse() 只返回严格校验后的 BmadParseResult；parse 失败通过 record_parse_failure() 创建 needs_human_review'
  - 'qa_testing 的 prompt 与 convergent-loop 结果消费都在 RecoveryEngine._dispatch_convergent_loop() 中闭环'
  - 'regression 已有 fail-closed command_audit validator，可抽成共享 helper 后供 QA 与 regression 共用'
  - 'needs_human_review 的展示语义由 approval_helpers.get_exception_context() 按 payload 形状分流'
  - 'retry 是否能消费 approval 取决于 payload 是否带 task_id'
test_patterns:
  - 'QA parser 回归集中在 tests/unit/test_bmad_adapter.py'
  - 'qa_testing prompt 与 convergent-loop 收敛行为集中在 tests/unit/test_recovery.py'
  - 'regression command_audit 合同与 fail-closed 行为集中在 tests/unit/test_merge_queue.py'
  - 'exception approval 上下文渲染集中在 tests/unit/test_exception_approval_panel.py'
  - 'needs_human_review retry/requeue 语义集中在 tests/unit/test_core.py'
---

# Tech-Spec: qa_testing V1 protocol-invalid fail-closed

**Created:** 2026-04-09

## Overview

### Problem Statement

`qa_testing` 当前只有 prompt 级 test policy 约束，没有与 `regression` 对等的 machine-validated command audit gate。结果是当 LLM 在 QA 轮次中超出 additional budget、在 optional commands 尚未消费完时提前执行 diagnostic/discovered command、或根本没有提供稳定可解析的 `## Commands Executed` 审计信息时，这一轮 QA 结果仍可能被当作正常 `qa_fail` / `qa_pass` 消费，从而把协议治理失败误记为产品代码缺陷。

### Solution

为 `qa_testing` 增加 V1 fail-closed 协议门：解析 `## Commands Executed` 为结构化 command audit，复用统一 validator 校验预算、顺序、来源与 gate 语义；一旦审计缺失、格式错误或违反 policy，则将该轮结果分流为 `protocol-invalid`，复用 `needs_human_review` 创建审批并保留 retry 语义，而不是进入正常 `qa_fail` 消费链路。

### Scope

**In Scope:**
- 为 `qa_testing` 解析 `## Commands Executed` 的 canonical 文本格式
- 为 QA 与 regression 抽取共享 command-audit validator
- 为 QA 增加 `protocol-invalid` 分流与 fail-closed gate
- 复用 `needs_human_review`，新增 `reason=qa_protocol_invalid` payload 合同
- 增加 `violation_code`、`audit_status` 与最小测试矩阵
- 保证 `retry` 可重排同一 `task_id`

**Out of Scope:**
- 收回 LLM 在 `qa_testing` 中的 shell 执行权
- 新增 approval type
- 修改 DB schema
- 扩散到 `validating`、`reviewing` 等其他 convergent loop phase
- 改造业务项目自身的测试脚本或 `ato.yaml` 示例

## Context for Development

### Codebase Patterns

- `BmadParseResult` 当前只承载 `verdict/findings/parser_mode/raw_output_preview/parse_error`，尚未承载 QA command audit；若要让 QA 审计 fail-closed，必须扩结果模型或在 parser 边界新增结构化审计返回。
- QA prompt 中 `## Commands Executed` 的 trigger 文本格式与现有 `RegressionCommandAuditEntry.trigger_reason` 枚举并不一致：prompt 输出的是 `required_layer:<name>` / `optional_layer:<name>` / `fallback:<kind>` / `diagnostic:<reason>`，而 runtime 结构化模型只接受裸枚举值。因此 parser 必须承担“文本格式解析 + 归一化”职责，并在需要时保留原始行文本供 payload/调试使用。
- `record_parse_failure()` 已经定义了现成的 `needs_human_review` 创建模式：`reason` + `skill_type` + `parser_mode` + `raw_output_preview` + `task_id?` + `options`，适合成为 `qa_protocol_invalid` 的 payload 合同参考。
- `qa_testing` 的 prompt 已固定要求 `## Commands Executed` 使用 canonical 单行格式输出；真正缺的是 parser 消费和 recovery gate，而不是 prompt 注入本身。
- `RecoveryEngine._dispatch_convergent_loop()` 当前顺序是：parse → findings 入库/匹配 → 收敛判定 → 提交 `qa_pass/qa_fail`。`protocol-invalid` gate 必须插在 findings 入库之前，否则会把违规轮次当成正常 QA 轮次落库。
- `_validate_regression_command_audit()` 已经覆盖 required 顺序、gate、budget、discovery 开关等 fail-closed 语义，但尚未显式校验 “optional commands 必须先于 discovered/diagnostic” 这一条 UAT 暴露出的优先级约束。
- `needs_human_review` 当前仅通过 payload 形状区分 design gate 与 BMAD parse failure：`failure_codes` 存在时走 design gate 分支，否则走 parse failure 分支。因此 `qa_protocol_invalid` 不能复用 `failure_codes`，必须通过 `reason=qa_protocol_invalid` 新增第三条渲染分支。
- `needs_human_review + retry` 的消费依赖 payload 中存在 `task_id`；无 `task_id` 时 approval 会保留不消费。这使 `task_id` 成为 `protocol-invalid` payload 的强制字段。
- `src/ato/core.py` 中的 `build_design_gate_payload()` 已提供现成的共享 payload helper 形态：由 core/recovery 共用单一 helper，避免 payload 结构分叉。`qa_protocol_invalid` 应沿用这一模式，而不是在 recovery 中手写 dict。
- QA parser 不能只挂在 `_parse_qa_report()` 附近做局部逻辑；`BmadAdapter.parse()` 目前存在 deterministic、explicit-pass、semantic-fallback 三条成功路径，`skill_type=qa_report` 时必须在这些成功分支汇总后统一附加 command-audit 解析结果，否则 PASS 输出会绕过审计抽取。

### Files to Reference

| File | Purpose |
| ---- | ------- |
| `src/ato/recovery.py` | `qa_testing` prompt、解析后收敛判定与 transition 提交入口 |
| `src/ato/adapters/bmad_adapter.py` | QA markdown 解析与 parse failure approval 入口 |
| `src/ato/models/schemas.py` | `RegressionCommandAuditEntry` 与 `BmadParseResult` 数据结构 |
| `src/ato/merge_queue.py` | 现有 regression command-audit validator 语义 |
| `src/ato/approval_helpers.py` | `needs_human_review` 的文案与 payload 展示分支 |
| `src/ato/core.py` | 现有共享 payload helper 模式（`build_design_gate_payload()`）参考 |
| `tests/unit/test_recovery.py` | QA/recovery 行为回归测试落点 |
| `tests/unit/test_bmad_adapter.py` | QA parser 与 parse failure 测试落点 |
| `tests/unit/test_exception_approval_panel.py` | `needs_human_review` payload 展示测试落点 |
| `tests/unit/test_core.py` | `needs_human_review + retry` 是否消费 approval 的行为约束 |
| `_bmad-output/implementation-artifacts/tech-spec-cross-project-test-policy-layering.md` | 现有 test policy layering 设计背景 |
| `_bmad-output/implementation-artifacts/uat-bidwise-story-8-2-test-policy-findings.md` | 本次 V1 目标、payload 合同与测试矩阵来源 |

### Technical Decisions

- `budget` 的语义固定为 required commands 之后的 `additional actions` 配额，不是“测试命令数量”
- `optional project_defined`、`llm_discovered`、`llm_diagnostic` 都计入同一 additional budget
- 优先级固定为 `optional > discovered > diagnostic`
- `protocol-invalid` 继续复用 `needs_human_review`，不新建 approval type
- `reason=qa_protocol_invalid` 作为 payload 的 canonical 分流标识
- `task_id` 视为 `protocol-invalid` payload 必填字段
- `protocol-invalid` payload 必带 `options=["retry", "skip", "escalate"]`，保持与现有 `bmad_parse_failed` 的异常审批交互合同一致
- 缺少或不可解析的 `## Commands Executed` 统一视为 `protocol-invalid`，不回退成 `bmad_parse_failed`
- 允许在抽共享 validator 时顺手收紧 regression 与 QA 的一致语义
- 共享 helper 预计拆为新模块 `src/ato/test_policy_audit.py`
  - 承载共享 validator（仅负责基于 `command_audit` 与 `EffectiveTestPolicy` 的策略校验）
  - 承载 `violation_code` 与 `audit_status` 约定
  - 承载 `build_qa_protocol_invalid_payload()` 之类的协议 helper
- regression 保留一个轻量 wrapper 负责 `commands_attempted == [entry.command ...]` 的对齐校验，再调用共享 helper；QA 不构造虚假的 `commands_attempted`
- `BmadParseResult` 预计最小扩展为：
  - `command_audit: list[RegressionCommandAuditEntry] | None`
  - `command_audit_parse_status: Literal["missing", "malformed", "parsed"] | None`
  - `command_audit_parse_error: str | None`
  - `command_audit_raw_lines: list[str] | None`
  - parser 只负责“能否解析到 canonical command audit + 解析出了什么”，不直接决定 `protocol-invalid`
  - `invalid` 属于 validator/recovery 的结论，不进入 parser 模型
  - 非 `qa_report` 技能与 `parse_failed` 结果默认填充 `None`
- QA command line 的归一化规则固定为：
  - `trigger=required_layer:<name>` → `trigger_reason="required_layer"`
  - `trigger=optional_layer:<name>` → `trigger_reason="optional_layer"`
  - `trigger=fallback:<kind>` → `trigger_reason="discovery_fallback"`
  - `trigger=diagnostic:<reason>` → `trigger_reason="diagnostic"`
  - `<name>/<kind>/<reason>` 只保留在 `command_audit_raw_lines` 中，不进入当前共享结构化枚举
- `protocol-invalid` payload 中的 `audit_status` 保持面向分流结果的语义：
  - `missing`
  - `malformed`
  - `invalid`
- `violation_code` 仅在 validator/recovery 阶段生成，不由 parser 直接给出
- `violation_code` 的最小稳定枚举固定为：
  - `COMMANDS_EXECUTED_MISSING`
  - `COMMANDS_EXECUTED_MALFORMED`
  - `REQUIRED_ORDER_VIOLATION`
  - `REQUIRED_COMMANDS_INCOMPLETE`
  - `OPTIONAL_PRIORITY_VIOLATION`
  - `ADDITIONAL_BUDGET_EXCEEDED`
  - `ADDITIONAL_GATE_CLOSED`
  - `DISCOVERY_DISABLED`
  - `INVALID_COMMAND_SOURCE`
  - `INVALID_TRIGGER_REASON`
- `commands_executed_preview` 的生成规则固定为：优先使用 `command_audit_raw_lines` 的前 5 行，每行裁剪到 200 字符；缺少 section 时为空列表；不得从 `parse_result.findings` 反推
- `qa_testing` recovery 分支的目标行为应为：
  - parse failure → 现有 `record_parse_failure()`
  - parse 成功但 audit 缺失/错误/违规 → `qa_protocol_invalid` + `needs_human_review`
  - audit 合法 → 继续现有 findings match 与 `qa_pass/qa_fail`
- `qa_testing` 的 protocol-invalid 早退路径必须与现有 parse-failed 早退路径一样完成收尾：
  - 关闭 convergent post-processing
  - 在 approval 提交后发出 nudge / 等效通知
  - 记录结构化日志，避免 task 卡在半完成状态
- Step 3 产出的 AC 必须显式覆盖：
  - Given QA parse 成功但 command audit 缺失、格式错误或违反 policy
  - When recovery 消费该轮结果
  - Then 不入库 findings，不提交 `qa_pass/qa_fail`，而是创建 `needs_human_review(reason=qa_protocol_invalid)` 且 payload 含 `task_id`

## Implementation Plan

### Tasks

- [ ] Task 1: 提取共享 command-audit 协议与校验 helper
  - File: `src/ato/test_policy_audit.py`
  - Action: 新建共享模块，承载 QA/Regression 共用的 command-audit 策略校验入口、稳定 `violation_code` 集合、以及 `build_qa_protocol_invalid_payload()` helper。
  - Notes: 不要继续把 QA 审计规则塞在 `merge_queue.py`；建议定义带 `violation_code` 与 `detail` 的专用异常类型，供 recovery / merge queue fail-closed 分流。共享 helper 只校验 `command_audit` 与 `EffectiveTestPolicy`，不负责 regression 的 `commands_attempted` 对齐。
- [ ] Task 2: 扩展审计数据模型，明确 parser 与 validator 的职责边界
  - File: `src/ato/models/schemas.py`
  - Action: 在保留 `RegressionCommandAuditEntry` 作为 canonical 单条审计记录的前提下，扩展 `BmadParseResult`，新增 `command_audit`、`command_audit_parse_status`、`command_audit_parse_error` 与 `command_audit_raw_lines` 字段。
  - Notes: `command_audit_parse_status` 只允许 `missing|malformed|parsed`；`invalid` 属于 validator/recovery 的结论，不进入 parser 模型。非 `qa_report` 技能与 `parse_failed` 结果必须默认填 `None`，避免破坏现有严格模型构造点。
- [ ] Task 3: 为 QA report 解析 `## Commands Executed`
  - File: `src/ato/adapters/bmad_adapter.py`
  - Action: 在 `BmadAdapter.parse()` 的 QA 成功返回路径上统一追加 canonical `## Commands Executed` 解析逻辑，将命令行解析为 `RegressionCommandAuditEntry` 列表，并把 parse 状态、parse 错误与原始 section 行文本写入 `BmadParseResult`。
  - Notes: 对 Recommendation/Quality Score/findings 结构仍然合法的 QA 输出，缺失或格式错误的命令审计不应触发 `parse_failed`；此类情况必须保留到 recovery 阶段分流为 `protocol-invalid`。解析器必须按 spec 的归一化规则接受 `required_layer:<name>` / `optional_layer:<name>` / `fallback:<kind>` / `diagnostic:<reason>`。
- [ ] Task 4: 将 regression validator 迁移到共享 helper，并顺手收紧 optional 优先级
  - File: `src/ato/merge_queue.py`
  - File: `src/ato/test_policy_audit.py`
  - Action: 用共享 helper 替换 `_validate_regression_command_audit()` 的内联策略校验逻辑，同时保留 regression wrapper 对 `commands_attempted` 与 `command_audit` 顺序一致性的 fail-closed 检查。
  - Notes: 在迁移时补上 `OPTIONAL_PRIORITY_VIOLATION` 语义：当仍有 project-defined optional commands 未消费时，不允许先执行 discovered/diagnostic command。
- [ ] Task 5: 为 `qa_testing` 增加 `protocol-invalid` gate
  - File: `src/ato/recovery.py`
  - File: `src/ato/test_policy_audit.py`
  - Action: 在 `RecoveryEngine._dispatch_convergent_loop()` 中，仅对 `qa_testing` phase，在 parse 成功且 findings 入库之前执行 command-audit 校验。
  - Notes: 若 `command_audit_parse_status` 为 `missing|malformed`，或 shared validator 报告 policy violation，则必须：
    - 创建 `needs_human_review(reason=qa_protocol_invalid)` approval
    - payload 必带 `task_id`
    - payload 必带 `options=["retry", "skip", "escalate"]`
    - 不写入 QA findings
    - 不提交 `qa_pass/qa_fail`
    - 关闭 convergent post-processing 后再早退
    - 发出 nudge / 等效通知，保证审批可见
    - 保留 retry 可重排同一 task 的上下文
    - `qa_bounded_fallback`（无 required commands、`allowed_when=always`）仍需走同一 gate，只是 shared validator 应接受“直接消耗 additional budget”这一默认行为
- [ ] Task 6: 扩展 `needs_human_review` 展示分支，支持 `qa_protocol_invalid`
  - File: `src/ato/approval_helpers.py`
  - Action: 在 `get_exception_context()` 中新增 `reason=qa_protocol_invalid` 的专用文案与字段展示，渲染 `task_id`、`violation_code`、`detail`、`raw_output_preview`、`commands_executed_preview` 等上下文。
  - Notes: 不要复用 `failure_codes`，否则会误命中 design gate 分支。`commands_executed_preview` 必须来自 parser 捕获的原始 command lines，而不是运行时反推。
- [ ] Task 7: 补齐 parser / recovery / approval / retry 的单元测试
  - File: `tests/unit/test_bmad_adapter.py`
  - File: `tests/unit/test_recovery.py`
  - File: `tests/unit/test_merge_queue.py`
  - File: `tests/unit/test_exception_approval_panel.py`
  - File: `tests/unit/test_core.py`
  - File: `tests/unit/test_test_policy_audit.py`
  - Action: 为 parser 状态、shared validator、QA protocol-invalid 分流、approval 展示、retry 消费语义补充回归测试。
  - Notes: 测试必须覆盖 QA 与 regression 共用 helper 的一致语义，避免未来再次漂移。

### Acceptance Criteria

- [ ] AC 1: Given 一个包含 `Recommendation`、`Quality Score`、有效 issue block 和 canonical `## Commands Executed` 的 QA report，when `BmadAdapter.parse()` 以 `qa_report` 模式解析，then 返回的 `BmadParseResult` 包含按顺序解析出的 `command_audit`；and `command_audit_parse_status == "parsed"`；and `required_layer:<name>` / `optional_layer:<name>` / `fallback:<kind>` / `diagnostic:<reason>` 被分别归一化为共享枚举值；and 原始命令行保存在 `command_audit_raw_lines`；and 现有 findings / verdict 语义保持不变。
- [ ] AC 2: Given 一个仍然满足 QA findings 合同、但缺少 `## Commands Executed` section 的 QA report，when `BmadAdapter.parse()` 解析，then 结果不得变成 `parse_failed`；and `command_audit_parse_status == "missing"`；and findings 仍按现有规则提取。
- [ ] AC 3: Given 一个具有 `## Commands Executed` section、但命令行不符合 canonical 格式的 QA report，when `BmadAdapter.parse()` 解析，then 结果不得变成 `parse_failed`；and `command_audit_parse_status == "malformed"`；and `command_audit_parse_error` 包含稳定的人类可读原因；and parser 保留原始 command lines 供 recovery 生成 preview。
- [ ] AC 4: Given `qa_testing` 的 parse 结果为成功但 `command_audit_parse_status` 为 `missing` 或 `malformed`，when `RecoveryEngine` 消费该轮 QA 结果，then 不写入该轮 QA findings；and 不提交 `qa_pass` 或 `qa_fail`；and 创建 `needs_human_review` approval；and payload 中 `reason == "qa_protocol_invalid"`、`task_id` 与 `options == ["retry", "skip", "escalate"]` 存在；and 早退前完成 post-processing 清理。
- [ ] AC 5: Given `qa_testing` 的 parse 结果带有可解析 `command_audit`，但违反 test policy（包括 required 顺序错误、required 不完整、optional 尚未消费完时提前执行 discovered/diagnostic、additional budget 超限、discovery 被禁用仍执行 discovered/diagnostic、或 gate 条件未满足），when recovery 校验该轮结果，then 该轮被判定为 `protocol-invalid`；and `needs_human_review` payload 包含稳定的 `violation_code`、人类可读 `detail`、有限 `commands_executed_preview` 与 `raw_output_preview`；and 不入库 findings；and 不提交 QA transition。
- [ ] AC 6: Given `qa_testing` 运行在默认 `qa_bounded_fallback` policy（无 required commands、`allowed_when=always`），when QA 仅执行不超过 budget 的 discovered/diagnostic commands 且 command audit 结构合法，then shared validator 不应把该轮误判为 `protocol-invalid`；and recovery 继续现有 findings match 与 `qa_pass/qa_fail` 路径。
- [ ] AC 7: Given regression 返回的 `command_audit` 触发与 QA 相同的共享 violation 语义，when merge queue 校验 structured output，then regression 继续 fail-closed；and regression wrapper 仍先校验 `commands_attempted` 与 `command_audit.command` 一一对应；and task 错误信息仍标识为 command audit validation failure；and 不破坏现有 `commands_attempted` 纯字符串合同。
- [ ] AC 8: Given 一个 `needs_human_review(reason=qa_protocol_invalid)` approval 且 payload 含 `task_id`，when 操作者选择 `retry`，then approval 被消费；and 对应 task 重置为 `pending`；and 可重排同一 QA task。
- [ ] AC 9: Given `needs_human_review` 收到 `reason=qa_protocol_invalid` payload，when CLI/TUI 渲染异常审批上下文，then 展示协议违规文案；and 显示 `task_id`、`violation_code`、`detail`、有限 `raw_output_preview` 与 `commands_executed_preview`；and 不误显示为 design gate 或 BMAD parse failure。

## Additional Context

### Dependencies

- 依赖现有 `RegressionCommandAuditEntry`
- 依赖现有 `needs_human_review` consumer 与 retry/requeue 机制
- 不新增外部库
- 不新增 DB schema 或 migration

### Testing Strategy

- 单元测试
  - `tests/unit/test_bmad_adapter.py`
    - QA canonical `## Commands Executed` 成功解析
    - QA trigger 文本归一化到共享枚举
    - `command_audit_parse_status=missing`
    - `command_audit_parse_status=malformed`
    - `command_audit_parse_error` 与 `command_audit_raw_lines` 填充
    - explicit-pass / semantic-fallback 成功路径同样附带 QA command audit
  - `tests/unit/test_test_policy_audit.py`
    - required 顺序与完整性
    - `OPTIONAL_PRIORITY_VIOLATION`
    - `ADDITIONAL_BUDGET_EXCEEDED`
    - `DISCOVERY_DISABLED`
    - gate 条件不满足
    - `violation_code/detail` 输出稳定
    - `qa_bounded_fallback` 无 required commands 时的合法 discovered/diagnostic 路径
  - `tests/unit/test_merge_queue.py`
    - regression 继续 fail-closed
    - 迁移到 shared helper 后现有 `commands_attempted` 合同保持成立
    - regression 也覆盖 optional 优先级违规
  - `tests/unit/test_recovery.py`
    - `qa_testing` 在 missing/malformed audit 下创建 `needs_human_review`
    - `qa_testing` 在 policy-invalid audit 下不入库 findings、不提交 transition
    - `qa_testing` 的 protocol-invalid 早退会完成 post-processing 清理
    - `qa_bounded_fallback` 默认 policy 下，合法 audit 不会误报 protocol-invalid
    - 合法 audit 仍按现有路径进入 findings match 与 `qa_pass/qa_fail`
  - `tests/unit/test_exception_approval_panel.py`
    - `qa_protocol_invalid` 文案分支
    - `task_id`、`violation_code`、`detail`、preview 渲染
  - `tests/unit/test_core.py`
    - `needs_human_review(reason=qa_protocol_invalid) + retry` 消费 approval 并重置 task
- 建议执行命令
  - `uv run pytest tests/unit/test_bmad_adapter.py`
  - `uv run pytest tests/unit/test_recovery.py`
  - `uv run pytest tests/unit/test_merge_queue.py`
  - `uv run pytest tests/unit/test_exception_approval_panel.py`
  - `uv run pytest tests/unit/test_core.py`
  - `uv run pytest tests/unit/test_test_policy_audit.py`
  - `uv run ruff check src tests`
  - `uv run mypy src`
- 手动验证
  - 使用 mocked QA 输出模拟 `missing` / `malformed` / `invalid` 三类 protocol-invalid
  - 验证 TUI/CLI 审批面板展示为协议违规，而非 design gate / parse failure
  - 验证 `retry` 后同一 `task_id` 被重新排队

### Notes

- 该 quick spec 只覆盖 `qa_testing` V1 fail-closed；不处理 V2 的受控执行接口
- 该规格默认采纳“共享 validator 可同时收紧 regression 语义”的边界
- 高风险项
  - 若 gate 放在 findings 入库之后，会造成脏 QA findings 落库
  - 若 `task_id` 缺失，`retry` 不会消费 approval
  - 若继续让 QA / regression 各维护一套 validator，语义会再次漂移
  - parser 不得把 audit 问题直接升级成 `parse_failed`，否则会丢失 `protocol-invalid` 分流语义
- 已知限制
  - V1 只能保证“违规结果不被接受”，不能证明 LLM 实际执行面完全受控
  - V1 不处理 unrestricted shell 的执行权收束
