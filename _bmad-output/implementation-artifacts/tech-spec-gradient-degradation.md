---
title: '收敛循环梯度降级策略（Convergent Loop Gradient Degradation）'
slug: 'gradient-degradation'
created: '2026-03-31'
status: 'implementation-complete'
stepsCompleted: [1, 2, 3, 4]
tech_stack: ['python>=3.11', 'asyncio', 'aiosqlite', 'pydantic>=2.0', 'textual>=2.0', 'structlog', 'python-statemachine>=3.0']
files_to_modify: ['src/ato/models/schemas.py', 'src/ato/config.py', 'src/ato/convergent_loop.py', 'src/ato/recovery.py', 'src/ato/core.py', 'src/ato/approval_helpers.py', 'src/ato/tui/app.py', 'src/ato/tui/dashboard.py', 'src/ato/tui/story_detail.py', 'src/ato/tui/widgets/convergent_loop_progress.py', 'ato.yaml.example', 'tests/unit/test_convergent_loop.py', 'tests/unit/test_recovery.py', 'tests/unit/test_multi_decision.py', 'tests/unit/test_exception_approval_panel.py', 'tests/unit/test_convergent_loop_progress.py']
code_patterns: ['dispatch profile from role config', 'structlog 结构化日志', 'dispatch_with_retry 统一调度', 'TransitionQueue 串行转换', 'SHA256 dedup finding matching', 'stage-aware restart metadata']
test_patterns: ['pytest-asyncio', 'aiosqlite in-memory DB', 'mock SubprocessManager/BmadAdapter', 'restart task metadata assertions', 'patch structlog logger 验证日志事件']
---

# Tech-Spec: 收敛循环梯度降级策略（Convergent Loop Gradient Degradation）

**Created:** 2026-03-31

## Overview

### Problem Statement

当前 convergent loop 达到 max_rounds 不收敛时直接 escalate 给人工。缺少中间自动恢复手段，人工介入频率过高。

### Solution

在现有 3 轮（Codex review + Claude fix）不收敛后，自动进入 Phase 2 梯度降级。Phase 2 不重新做 full review，而是直接从一次升级版 fix 开始（Codex fix），随后由 Claude 做 scoped re-review；按同样的 fix → re-review 节奏最多再尝试 3 轮，仍不收敛才转入人工决策。

### Scope

**In Scope:**
- 角色互换逻辑：Phase 1（Codex review + Claude fix）→ Phase 2（Codex fix + Claude re-review）
- Phase 2 轮次独立配置（`max_rounds_escalated`，默认 3）
- Phase 2 从 fix 开始，而不是重新跑一轮 full review
- 沿用现有 `open/still_open` findings 连续追踪，不新增 findings phase 列
- Phase 2 不收敛时创建 escalation approval 让人决定
- escalation approval 使用显式动作：`restart_phase2` / `restart_loop` / `escalate`
- Codex fixer 使用 `workspace-write` sandbox
- TUI 进度展示区分两个阶段
- crash recovery / restart 能恢复到正确的降级阶段

**Out of Scope:**
- Interactive Session 自动启动（人工决定后的处理不在此 spec）
- 三阶段以上的降级链
- 不同 prompt 策略优化（使用现有 prompt 模板适配即可）
- findings schema 重构或历史数据回填

## Context for Development

### Codebase Patterns

**Convergent Loop 编排模式：**
- `run_loop()` (convergent_loop.py:107-239) 是核心编排入口，管理轮次计数和终止逻辑
- 子方法 `run_first_review()` / `run_fix_dispatch()` / `run_rereview()` 各自负责 transition event 和 round_complete 日志
- `run_loop()` 仅负责轮次控制、终止判断、escalation 创建

**角色-工具调度模式：**
- 当前硬编码：review → `cli_tool="codex"`, `role="reviewer"`；fix → `cli_tool="claude"`, `role="developer"`
- 通过 `subprocess_mgr.dispatch_with_retry()` 统一调度，参数包含 `cli_tool` 和 `role`
- 当前 `ConvergentLoop` 只直接消费 reviewer options，fix path 仍为硬编码；Phase 2 必须引入可复用的 dispatch profile，而不是只改 YAML
- Phase 2 需使用反转后的 profile：review → `cli_tool="claude"`, `role="reviewer_escalated"`；fix → `cli_tool="codex"`, `role="fixer_escalation"`

**Finding 跨轮次匹配：**
- 使用 SHA256(file_path|rule_id|severity|normalized_description) 去重
- 状态分类：open → still_open / closed；新发现 → new
- `_match_findings_across_rounds()` 负责跨轮匹配
- Phase 2 必须继续消费当前 `open/still_open` findings；进入 Phase 2 时不能批量关闭、复制或隔离旧 findings
- `findings.round_num` 在 DB 中保持单一、单调递增的全局语义；阶段区分通过内存结果、日志和 approval payload 的 `stage` 元数据表达

**Escalation 流程：**
- `_create_escalation_approval()` 创建 `convergent_loop_escalation` approval
- Payload 包含 `round_summaries` + `unresolved_findings` + `options`
- 当前 options: `["retry", "skip", "escalate"]`
- 新方案应改为 `["restart_phase2", "restart_loop", "escalate"]`，并给出机器可执行的 restart target
- 幂等检查：同一 story 若已有 pending escalation 则跳过

**TUI 进度组件：**
- `ConvergentLoopProgress` widget 渲染 `●/◐/○` 轮次指示器 + findings 统计 + 收敛率
- `update_progress()` 接受 `current_round`, `max_rounds`, `findings_summary`
- 当前 TUI 上游通过 `MAX(findings.round_num)` 推导当前轮次；若要展示 escalated stage，必须补充 stage 元数据来源，不能只改 widget

### Files to Reference

| File | Purpose |
| ---- | ------- |
| `src/ato/convergent_loop.py` | 核心 CL 编排（run_loop/review/fix/rereview），当前 review/fix dispatch 与 placeholder 仍有硬编码 |
| `src/ato/recovery.py` | reviewing phase 恢复路径，当前 hardcode Codex reviewer，需改为 stage-aware restart |
| `src/ato/core.py` | approval decision 消费与 pending restart task 调度链 |
| `src/ato/config.py` | ConvergentLoopConfig 与 role/cli_defaults 合并逻辑 |
| `src/ato/models/schemas.py` | Approval 选项/推荐动作常量、ConvergentLoopResult 模型 |
| `src/ato/models/db.py` | open findings / story findings summary 查询，需保持 schema 不变但理解其 round 语义 |
| `src/ato/approval_helpers.py` | option labels 与 escalation 展示文案 |
| `src/ato/tui/app.py` | 当前通过 `MAX(findings.round_num)` 推导 CL 轮次 |
| `src/ato/tui/story_detail.py` | 向进度 widget 传入当前 CL 展示数据 |
| `src/ato/tui/dashboard.py` | detail view / dashboard 的 CL 展示管线 |
| `src/ato/tui/widgets/convergent_loop_progress.py` | TUI 进度组件 |
| `ato.yaml.example` | escalated roles 与 convergent_loop 配置参数 |
| `tests/unit/test_convergent_loop.py` | CL 单元测试（mock dispatch 验证 role/cli_tool/phase entry） |
| `tests/unit/test_recovery.py` | restart / recovery 行为测试 |
| `tests/unit/test_multi_decision.py` | approval options 合同测试 |
| `tests/unit/test_exception_approval_panel.py` | escalation 文案与选项展示测试 |
| `tests/unit/test_convergent_loop_progress.py` | 进度组件测试 |

### Technical Decisions

1. **Single Findings Timeline**：findings 表和 `FindingRecord` 保持不变，不新增 `phase` 列。Phase 2 继续处理当前 `open/still_open` findings，Phase 2 rereview 可以把这些记录更新为 `closed` / `still_open`。`findings.round_num` 在 DB 中保持全局单调递增，不在 Phase 2 重置。

2. **Stage 元数据仅用于编排/展示**：新增 `LoopStage = Literal["standard", "escalated"]`，用于 `ConvergentLoopResult`、round summary entry、approval payload 和日志字段 `degradation_stage`。Stage 不进入 findings schema。

3. **Phase 2 从 Fix 开始**：`run_loop()` 在 standard phase 用尽后，进入 `_run_escalated_phase()`。Phase 2 的首个动作是 escalated fix（Codex），之后才是 escalated scoped re-review（Claude）；不再先跑一轮 full review。

4. **Dispatch Profile 由 role config 解析**：在 `ato.yaml.example` 中新增 `reviewer_escalated`（claude）和 `fixer_escalation`（codex, sandbox=`workspace-write`）。实现上必须新增 role-profile 解析辅助函数，并将 standard / escalated 的 review/fix profile 显式注入 `ConvergentLoop`，避免只改 YAML 而运行时仍走硬编码。

5. **Approval 动作显式化**：`convergent_loop_escalation` 的 options 改为 `["restart_phase2", "restart_loop", "escalate"]`，推荐动作为 `restart_phase2`。`restart_phase2` 表示从 escalated phase 的 fix 起点重新开始；`restart_loop` 表示从 standard phase round 1 全量重跑。

6. **Restart Target 必须机器可读**：approval payload 和 synthetic restart task 都要带 `restart_target` / `stage` 标记，使 `core.py` 和 `recovery.py` 能无歧义地恢复到正确分支，而不是从 DB findings 反推当前处于哪一阶段。

7. **No DB Migration**：不改 findings schema，不 bump `SCHEMA_VERSION`，不新增 migration。旧数据库和新数据库应共用同一 schema version 10。

## Implementation Plan

### Tasks

- [x] Task 1: 更新 schema 常量与结果模型（不改 DB schema）
  - File: `src/ato/models/schemas.py`
  - Action:
    - 新增 `LoopStage = Literal["standard", "escalated"]`
    - `ConvergentLoopResult` 新增字段 `stage: LoopStage = "standard"`
    - `APPROVAL_DEFAULT_VALID_OPTIONS["convergent_loop_escalation"]` 改为 `["restart_phase2", "restart_loop", "escalate"]`
    - `APPROVAL_RECOMMENDED_ACTIONS["convergent_loop_escalation"]` 改为 `"restart_phase2"`
    - `FindingRecord` 保持不变
  - Notes: 不 bump `SCHEMA_VERSION`，不引入 findings phase 字段

- [x] Task 2: 扩展配置模型并补齐 role-profile 解析
  - File: `src/ato/config.py`
  - Action:
    - `ConvergentLoopConfig` 新增 `max_rounds_escalated: int = 3`
    - 新增 `resolve_role_dispatch_config(settings, role_name)` 或等价 helper，返回合并了 `cli_defaults` 的 `cli_tool/model/sandbox/effort/reasoning_*`
  - File: `ato.yaml.example`
  - Action:
    - `convergent_loop` 部分新增 `max_rounds_escalated: 3`
    - `roles` 部分新增：
      ```yaml
      reviewer_escalated:
        cli: claude
      fixer_escalation:
        cli: codex
        sandbox: workspace-write
      ```
  - Notes: escalated role config 必须被运行时代码实际消费，而不是停留在文档示例

- [x] Task 3: 重构 `ConvergentLoop` 为 stage-aware dispatch profile
  - File: `src/ato/convergent_loop.py`
  - Action:
    - 扩展构造函数，显式接收 standard/escalated 的 review/fix dispatch profile，或接收一个可按 stage 解析 profile 的对象
    - `run_first_review()` / `run_fix_dispatch()` / `run_rereview()` 新增 `stage: LoopStage = "standard"` 参数
    - 避免复用 `phase` 这个名字表示降级阶段；`phase` 仍保留给 `reviewing/fixing` 等工作流 phase
    - 去掉 review/fix dispatch 中对 `role` / `cli_tool` 的硬编码
    - `_insert_fix_placeholder()` 按当前 stage 写入正确的 `role` / `cli_tool` / restart marker
    - 结构化日志增加 `degradation_stage`
    - `_build_escalation_payload()` 返回 `stage`、`standard_round_summaries`、`escalated_round_summaries` 和 `restart_target`
  - Notes: findings 查询和写入逻辑保持单一时间线，不加 phase filter

- [x] Task 4: 实现从 fix 开始的 Phase 2 编排
  - File: `src/ato/convergent_loop.py`
  - Action:
    - 新增私有方法 `async def _run_escalated_phase(self, story_id, worktree_path, *, standard_round_summaries) -> ConvergentLoopResult`
    - `_run_escalated_phase()` 的节奏是：
      1. 基于当前 `open/still_open` blocking findings 执行 escalated fix
      2. 执行 escalated scoped re-review
      3. 若未收敛且未达上限，则继续下一轮 fix → re-review
    - 修改 `run_loop()`：standard phase 用尽后不立即创建 approval，而是进入 `_run_escalated_phase()`
    - `max_rounds == 1` 时，首轮 full review 未收敛后也进入 escalated fix，而不是直接 approval
    - Phase 2 用尽后再创建 escalation approval
  - Notes: Phase 2 入口绝不能调用 `run_first_review()`；也不允许 `_close_phase1_findings()` 之类的批量清理

- [x] Task 5: 更新 approval 文案与决策消费逻辑
  - File: `src/ato/approval_helpers.py`
  - Action:
    - `convergent_loop_escalation` 展示文案改为区分 standard / escalated 两阶段摘要
    - `_OPTION_LABELS` 新增 `restart_phase2` / `restart_loop`
  - File: `src/ato/core.py`
  - Action:
    - `convergent_loop_escalation` 不再走 `_reschedule_interactive_task()`
    - 新增 `restart_phase2` / `restart_loop` 决策处理分支
    - 创建 synthetic pending restart task，并写入机器可读的 `restart_target` / `stage` marker，供 `_dispatch_pending_tasks()` 和 `_dispatch_convergent_restart()` 消费
  - Notes: `restart_phase2` 是推荐路径，必须重新进入 escalated phase 的 fix 起点

- [x] Task 6: 使 recovery / restart 真正理解 escalated stage
  - File: `src/ato/recovery.py`
  - Action:
    - `_dispatch_reviewing_convergent_loop()` 改为读取 restart marker / task metadata / role profile，区分：
      - standard full review
      - standard re-review
      - escalated phase restart
    - 去除 reviewing recovery 中默认使用 Codex reviewer 的硬编码
    - escalated restart 时调用 `_run_escalated_phase(start_with_fix=True)` 或等价入口
  - Notes: recovery 不能再靠“看数据库里有没有 open findings”去猜当前属于哪个 stage

- [x] Task 7: 更新 TUI 进度展示管线
  - File: `src/ato/tui/app.py`
  - Action:
    - 为 story detail / dashboard 补充 `stage` 元数据来源，优先读取 active task role / restart marker / pending escalation payload
  - File: `src/ato/tui/dashboard.py`
  - Action:
    - 继续传递 `cl_round` / `cl_max_rounds` 的同时，新增 stage 透传
  - File: `src/ato/tui/story_detail.py`
  - Action:
    - `update_detail()` / `_render_cl_progress()` 透传 `stage`
  - File: `src/ato/tui/widgets/convergent_loop_progress.py`
  - Action:
    - `update_progress()` 新增 `stage: LoopStage = "standard"`
    - `stage == "standard"` 时前缀保持 `CL:`
    - `stage == "escalated"` 时前缀显示 `CL↑:`
  - Notes: 因为 DB `round_num` 保持全局递增，TUI 的阶段区分依赖 stage 元数据而不是单独的 findings 列

- [x] Task 8: 单元测试与回归测试
  - File: `tests/unit/test_convergent_loop.py`
  - Action:
    - 新增 `TestGradientDegradation`：
      - `test_phase1_not_converged_enters_phase2_from_fix`
      - `test_phase2_entry_does_not_run_full_review`
      - `test_phase2_converged_returns_success`
      - `test_phase2_not_converged_creates_escalation`
      - `test_findings_timeline_remains_continuous`
      - `test_max_rounds_one_still_enters_escalated_fix`
      - `test_phase2_abnormal_result_aborts`
  - File: `tests/unit/test_recovery.py`
  - Action:
    - 新增 escalated restart / in-flight recovery 测试，验证使用正确的 role/cli_tool/profile
  - File: `tests/unit/test_multi_decision.py`
  - Action:
    - 更新 `convergent_loop_escalation` options 合同测试为 `restart_phase2` / `restart_loop` / `escalate`
  - File: `tests/unit/test_exception_approval_panel.py`
  - Action:
    - 更新 escalation 选项标签和阶段文案测试
  - File: `tests/unit/test_convergent_loop_progress.py`
  - Action:
    - 新增 `test_escalated_stage_prefix`
    - 保留并验证 `test_standard_stage_prefix_unchanged`

### Acceptance Criteria

- [x] AC 1: Given standard phase 按默认 `max_rounds=3` 仍未收敛，when `run_loop()` 结束 standard loop，then 系统自动进入 escalated phase，且 Phase 2 的首个动作是 fix，不创建 escalation approval
- [x] AC 2: Given 进入 escalated phase，when 调度 fix agent，then 使用 `cli_tool="codex"`, `role="fixer_escalation"`，并传入 `sandbox="workspace-write"`；when 调度 re-review agent，then 使用 `cli_tool="claude"`, `role="reviewer_escalated"`
- [x] AC 3: Given escalated phase 运行中，when rereview 判定 findings 状态，then 继续更新同一批 `open/still_open` findings 的 `closed` / `still_open` 状态，不新增 findings phase 列，也不在进入 Phase 2 时批量关闭旧 findings
- [x] AC 4: Given escalated phase 的 `max_rounds_escalated` 用尽仍不收敛，when 循环终止，then 创建 `convergent_loop_escalation` approval，payload 包含 `stage: "escalated"`、`standard_round_summaries`、`escalated_round_summaries`、`restart_target`
- [x] AC 5: Given 用户消费 `convergent_loop_escalation` approval，when 选择 `restart_phase2`，then 系统重启 escalated phase 并从 fix 起点开始；when 选择 `restart_loop`，then 系统从 standard phase round 1 全量重跑
- [x] AC 6: Given escalated phase 在运行中崩溃或被 restart，when recovery / restart 触发，then 使用正确的 escalated dispatch profile，而不是回退为默认 Codex reviewer + Claude fixer
- [x] AC 7: Given standard phase 在任一轮已收敛，when `run_loop()` 返回，then 不进入 escalated phase
- [x] AC 8: Given TUI 渲染 escalated phase，when 进度组件显示 CL 状态，then 前缀显示 `CL↑:`，且 stage 来源于 task / approval metadata，而不是依赖 findings phase 列
- [x] AC 9: Given `ato.yaml` 配置 `max_rounds_escalated: 5`，when escalated phase 执行，then 最多运行 5 轮 escalated re-review
- [x] AC 10: Given escalated re-review 返回 `parse_failed` 或其他 abnormal result，when 检测到异常，then escalated phase 中止，不继续下一轮 fix / re-review

## Additional Context

### Dependencies

- 无新外部库依赖——全部使用现有 stack
- 依赖 MVP Epic 3（Stories 3.1-3.3）的完整实现——已确认完成
- 依赖 `ato.yaml.example` 中新角色定义——用户需同步更新自己的 `ato.yaml`
- 无数据库迁移依赖——本方案显式保持 `SCHEMA_VERSION = 10`

### Testing Strategy

**单元测试（Task 8）：**
- 使用 in-memory aiosqlite 数据库，pytest-asyncio 驱动
- Mock `SubprocessManager.dispatch_with_retry` 验证 role/cli_tool 参数传递
- Mock `BmadAdapter.parse` 控制 findings 输出
- 使用 `patch("ato.convergent_loop.logger")` 验证 structlog 事件
- 断言进入 escalated phase 时不会再调用 full review
- 断言 DB 中 findings 保持连续时间线，不新增 phase 字段假设
- 断言 escalation approval 的 payload 结构、restart target 和 options
- 断言 recovery / restart 使用正确的 escalated dispatch profile

**集成测试（手动验证）：**
- 用测试用的 ato.yaml 启动系统，模拟一个 story 进入 reviewing 阶段
- 让 Phase 1 三轮不收敛，观察自动进入 Phase 2
- 验证 TUI 进度组件切换显示
- 让 Phase 2 也不收敛，验证 escalation approval 出现在审批队列

### Notes

**高风险项：**
- `restart_phase2` / `restart_loop` 的 task metadata 若未贯通到 `core.py` 和 `recovery.py`，审批动作会再次退化为“用户可点，但系统不会真的重启到正确分支”
- `_insert_fix_placeholder()` 在 Phase 2 中需使用正确的 role/cli_tool（fixer_escalation/codex），否则 orchestrator 主循环的 race condition 检测会误判
- TUI 若仍只依赖 `MAX(findings.round_num)`，将无法准确区分 standard / escalated stage

**已知限制：**
- Phase 2 的 prompt 与 Phase 1 相同（Out of Scope），后续可针对不同工具优化 prompt 策略
- 本方案保持单一 findings 时间线，因此 approval / TUI 中看到的 unresolved findings 会跨 standard 与 escalated 两阶段连续存在

**未来考量（不在本 spec）：**
- FR42 完整实现还包括第三阶段 Interactive Session 自动启动
- 后续可为 escalated phase 单独设计 prompt 策略，而不是仅复用现有 review/fix 模板
