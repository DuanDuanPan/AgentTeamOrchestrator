# Story 9.4: 移除冗余 Planning 阶段

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->
<!-- Depends on: Story 8.2 (planning phase 引入了当前冗余), Story 9.1e (creating prompt 已成为首个 BMAD create-story 入口), Story 9.2 (pre-worktree workspace contract), Story 9.3 (dev_ready/main-path sequencing) -->

## Story

As a 操作者,
I want `planning` 阶段从生命周期中移除，使 `creating` 恢复为 batch 启动后的首个活跃阶段,
so that story 不再经历两个重复的 `/bmad-create-story` 调用，减少不必要的 agent 开销。

## Acceptance Criteria (AC)

### AC1: Canonical lifecycle 不再包含真实 `planning` phase，但高层 `StoryStatus` 语义保持不变

```gherkin
Given 当前代码因 Story 8.2 引入了真实 `planning` phase
When 本 story 完成 lifecycle 收敛
Then `CANONICAL_PHASES` 不再包含 `planning`
And canonical phase 顺序恢复为 `creating -> designing -> validating -> dev_ready -> developing -> reviewing -> fixing -> re_reviewing -> merging -> regression -> qa_testing`
And `PHASE_TO_STATUS` 中不再有 `planning` phase 条目
And `creating` / `designing` / `validating` 仍映射到高层状态 `"planning"`
And `src/ato/models/schemas.py` 中的 `StoryStatus` literal 保持不变（不新增也不删除 `"planning"` 状态值）
```

### AC2: 状态机与 happy-path / replay 事件收敛为 `queued -> creating`

```gherkin
Given `StoryLifecycle` 当前定义了 `planning = State()` 与 `plan_done`
When 移除冗余 phase
Then `start_create` 直接推进 `queued -> creating`
And `plan_done` 事件与 `planning.to(blocked)` 不再存在
And `create_done` 继续表示 `creating -> designing`
And `validate_fail` / `fix_done` / `merge_done` 等其余既有事件语义保持不变
And `TransitionQueue` 的 happy-path / replay 事件序列不再包含 `plan_done`
```

### AC3: 运行时 structured-job / recovery / initial-dispatch 以 `creating` 作为首个真实 phase

```gherkin
Given recovery / restart / initial dispatch 依赖 phase→success-event 与 prompt 模板
When story 首次进入活跃阶段或从 structured_job 重调度
Then `_PHASE_SUCCESS_EVENT` 不再包含 `planning: "plan_done"`
And `_STRUCTURED_JOB_PROMPTS` 不再包含 `planning` 条目
And `creating` 继续作为首个 `/bmad-create-story` structured_job phase
And `get_undispatched_stories()` 所发现的首个活跃 story 对应 `current_phase="creating"`，而不是 `planning`
And pre-worktree main-path gate 仍覆盖 `creating` / `designing` / `validating`，不因移除 `planning` 而退化
```

### AC4: Batch 初始化、配置模板与显示层对齐新的首阶段

```gherkin
Given batch 头部 story、phase config 与 CLI/TUI phase 视图
When batch confirm 写入首个 actionable story，或从 `ato.yaml.example` 构建默认 phase 定义
Then 头部 story 使用 `status="planning", current_phase="creating"`
And `ato.yaml.example` 中不存在 `planner` role 与 `planning` phase 定义
And `src/ato/config.py` 的已知 main-phase 集合不再包含 `planning`
And CLI/TUI phase order 不再把 `planning` 当作真实 canonical phase
And 若存在 planning-specific 图标或断言，它们要么被移除，要么仅保留给高层 status 展示，不再对应真实 phase
```

### AC5: 测试矩阵完整适配，覆盖 `planning` 移除后的真实影响面

```gherkin
Given 当前测试基线大量假设 `planning` 是首个真实 phase
When 本 story 完成
Then 相关单元 / 集成测试全部适配通过
And 至少覆盖以下回归面：
  - `tests/unit/test_state_machine.py`：phase 数量、state 数量、event 序列、`start_create` 目标
  - `tests/unit/test_transition_queue.py` 与 `tests/integration/test_transition_queue.py`：replay / happy-path 不再包含 `plan_done`
  - `tests/unit/test_recovery.py` 与 `tests/integration/test_crash_recovery.py`：不再调度 `planning` / `plan_done`
  - `tests/unit/test_batch.py`：batch 头部 story 初始 phase 为 `creating`
  - `tests/unit/test_initial_dispatch.py`：首个活跃 phase / role 从 `planning/planner` 改为 `creating/creator`
  - `tests/unit/test_config.py` / `tests/integration/test_config_workflow.py` / `tests/unit/test_cli_plan.py`：默认 phase 模板与阶段顺序更新
```

## Tasks / Subtasks

- [x] Task 1: 收敛状态机 canonical 合同，彻底移除真实 `planning` phase (AC: #1, #2)
  - [x] 1.1 更新 `src/ato/state_machine.py`：`CANONICAL_PHASES` 删除 `planning`
  - [x] 1.2 更新 `src/ato/state_machine.py`：`PHASE_TO_STATUS` 删除 `planning` phase 条目，但保留 `creating/designing/validating -> "planning"` 聚合映射
  - [x] 1.3 更新 `src/ato/state_machine.py`：`CANONICAL_TRANSITIONS` 删除 `planning -> creating`
  - [x] 1.4 更新 `src/ato/state_machine.py`：删除 `planning = State()`、`plan_done`、`planning.to(blocked)`，并将 `start_create` 改为 `queued.to(creating)`
  - [x] 1.5 更新 `tests/unit/test_state_machine.py` 与 `tests/integration/test_state_persistence.py` 的 phase/state/event 断言

- [x] Task 2: 对齐 recovery、queue replay 与初始 dispatch 的首阶段语义 (AC: #2, #3)
  - [x] 2.1 更新 `src/ato/recovery.py`：删除 `_PHASE_SUCCESS_EVENT["planning"]`
  - [x] 2.2 更新 `src/ato/recovery.py`：删除 `_STRUCTURED_JOB_PROMPTS["planning"]`，保持 `creating` 为首个 `/bmad-create-story` phase
  - [x] 2.3 更新 `src/ato/transition_queue.py`：删除 `plan_done` 与 `planning` 相关 happy-path / replay 表项
  - [x] 2.4 检查 `src/ato/core.py` 与 `src/ato/models/db.py::get_undispatched_stories()` 的首阶段消费合同，确保初始 dispatch 以 `creating` 为首个真实 phase
  - [x] 2.5 更新 `tests/unit/test_transition_queue.py`、`tests/integration/test_transition_queue.py`、`tests/unit/test_recovery.py`、`tests/integration/test_crash_recovery.py`、`tests/unit/test_initial_dispatch.py`

- [x] Task 3: 对齐 batch 初始化、配置模板与显示层 (AC: #3, #4)
  - [x] 3.1 更新 `src/ato/batch.py::confirm_batch()`：seq=0 story 写入 `status="planning", current_phase="creating"`
  - [x] 3.2 更新 `src/ato/config.py`：已知 main-phase 集合与注释不再包含 `planning`
  - [x] 3.3 更新 `ato.yaml.example`：移除 `planner` role 与 `planning` phase 定义
  - [x] 3.4 更新 `src/ato/cli.py` / `src/ato/tui/*` 中任何把 `planning` 当真实 phase 的顺序、图标或断言基线
  - [x] 3.5 更新 `tests/unit/test_batch.py`、`tests/unit/test_config.py`、`tests/integration/test_config_workflow.py`、`tests/unit/test_cli_plan.py`、`tests/unit/test_story_detail_view.py`

- [x] Task 4: 收紧回归边界，避免扩大到不必要的 schema / workflow churn (AC: #1, #4, #5)
  - [x] 4.1 确认 `src/ato/models/schemas.py::StoryStatus` 无需迁移或 schema version 变更
  - [x] 4.2 确认不修改 batch 推荐 / sprint-planning / preflight artifact 发现规则
  - [x] 4.3 确认不新增 planner 专用 approval type、DB 表或新的 structured_job phase
  - [x] 4.4 跑最小必要测试子集并记录结果

## Dev Notes

### 关键实现判断

- **这是 Story 8.2 的 corrective rollback，不是新功能扩张。** 目标是移除与 `creating` 完全重复的真实 `planning` phase，而不是重新设计 lifecycle。
- **高层 `StoryStatus` 的 `"planning"` 必须保留。** 当前 CLI/TUI/统计语义把 `creating/designing/validating` 聚合为 planning；移除真实 phase 不等于删除高层状态。
- **`creating` 已经是唯一正确的 create-story 入口。** Story 9.1e 已把 `creating` prompt 收敛到 `/bmad-create-story`；9.4 不能留下 `planning` prompt 或 `plan_done` 残影。
- **batch 头部 story 的正确合同是 `status="planning", current_phase="creating"`。** 这样既保持高层统计不变，也避免首个 story 再经历一个虚假的重复 structured_job phase。
- **`test_initial_dispatch.py` 属于真实运行面，不只是测试清理。** 初始调度发现逻辑默认首个活跃 story 处于非 queued/done/blocked phase；如果不把测试和相应 role/phase 假设一起改掉，就会留下首阶段语义分裂。
- **本 story 不需要 DB migration。** 没有新增/删除 persisted status literal，也不调整表结构；这里是 phase contract 收敛，不是 schema 演进。

### Scope Boundary

- **IN:** `state_machine.py`、`recovery.py`、`transition_queue.py`、`batch.py`、`config.py`、`cli.py`、`ato.yaml.example` 及相关测试中的 `planning` phase 移除
- **IN:** batch 头部 story 初始 phase 改为 `creating`
- **OUT:** 重做 batch 推荐 / sprint-planning 逻辑
- **OUT:** 修改 `StoryStatus` literal 或引入 DB migration
- **OUT:** 变更 remote/worktree/spec-commit 合同（继续沿用 9.2 / 9.3 既有语义）
- **OUT:** 文档体系整体重写

### Project Structure Notes

- 主要修改文件：
  - `src/ato/state_machine.py`
  - `src/ato/recovery.py`
  - `src/ato/transition_queue.py`
  - `src/ato/batch.py`
  - `src/ato/config.py`
  - `src/ato/cli.py`
  - `ato.yaml.example`
- 重点测试文件：
  - `tests/unit/test_state_machine.py`
  - `tests/unit/test_transition_queue.py`
  - `tests/integration/test_transition_queue.py`
  - `tests/unit/test_recovery.py`
  - `tests/integration/test_crash_recovery.py`
  - `tests/unit/test_batch.py`
  - `tests/unit/test_initial_dispatch.py`
  - `tests/unit/test_config.py`
  - `tests/integration/test_config_workflow.py`
  - `tests/unit/test_cli_plan.py`

### Suggested Verification

```bash
uv run pytest tests/unit/test_state_machine.py tests/unit/test_transition_queue.py tests/unit/test_recovery.py tests/unit/test_batch.py tests/unit/test_initial_dispatch.py tests/unit/test_config.py tests/unit/test_cli_plan.py -v
uv run pytest tests/integration/test_transition_queue.py tests/integration/test_state_persistence.py tests/integration/test_crash_recovery.py tests/integration/test_config_workflow.py -v
```

## References

- [Source: _bmad-output/planning-artifacts/epics.md — Story 9.4]
- [Source: _bmad-output/planning-artifacts/sprint-change-proposal-2026-03-29-remove-planning-phase.md]
- [Source: _bmad-output/planning-artifacts/prd.md — FR3 lifecycle 以 `creating` 开始]
- [Source: _bmad-output/implementation-artifacts/8-2-add-planning-phase.md]
- [Source: _bmad-output/implementation-artifacts/9-1e-creating-phase-prompt-validation-feedback.md]
- [Source: _bmad-output/implementation-artifacts/9-2-workspace-concept-worktree-timing.md]
- [Source: _bmad-output/implementation-artifacts/9-3-conditional-skip-spec-commit.md]
- [Source: src/ato/state_machine.py — `CANONICAL_PHASES`, `PHASE_TO_STATUS`, `CANONICAL_TRANSITIONS`, `StoryLifecycle`]
- [Source: src/ato/recovery.py — `_PHASE_SUCCESS_EVENT`, `_STRUCTURED_JOB_PROMPTS`]
- [Source: src/ato/transition_queue.py — happy-path / replay tables]
- [Source: src/ato/batch.py — `confirm_batch()`]
- [Source: src/ato/config.py — main-path workspace allowlist]
- [Source: src/ato/cli.py — phase-order / icon consumer]
- [Source: tests/unit/test_initial_dispatch.py — 首阶段 dispatch 测试基线]

### Previous Story Intelligence

1. **Story 8.2 以“最小事件改动”方式插入了 `planning`。** 因此 9.4 也必须把 `plan_done`、首阶段写入、replay 表和 recovery success-event 一起收回，不能只删 `state_machine.py` 里的 phase 名字。
2. **Story 9.1e 已明确 `creating` 才是 `/bmad-create-story` 的 phase-aware prompt。** 如果 9.4 只删 phase，却漏删 `planning` prompt / planner role，就会留下双入口和死代码。
3. **Story 9.2 / 9.3 已把 pre-worktree main-path 与 `dev_ready` 顺序收紧。** 9.4 只能把首 phase 改回 `creating`，不能顺手动 `creating/designing/validating → dev_ready` 之后的 workspace / commit 合同。

## Change Log

- 2026-03-29: Story 重建 —— `sprint-status.yaml` 已记录 9.4 被创建，但实现产物文件缺失；本次在 validate-create-story 过程中补建 story 文件
- 2026-03-29: `validate-create-story` 修订 —— 将影响面从”删 phase 名字”扩展到真实 runtime/test surface；明确保留高层 planning status、禁止 schema migration、补入 `test_initial_dispatch.py` 与 config/template 适配
- 2026-03-29: 实现完成 —— 移除 `planning` phase、`plan_done` event、`planner` role；全部 1731 测试通过，无回归
- 2026-03-29: Code review 修复 —— 添加 DB 旧数据向后兼容：`_SPECIAL_REPLAY["planning"]` 和 `_PHASE_SUCCESS_EVENT["planning"]`；新增 3 个向后兼容测试，全部 1734 测试通过

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6

### Debug Log References

1. recovery 测试中 `designing` phase 有 design gate 特殊逻辑，将测试用例调整为 `dev_ready` phase 以保持测试意图一致。
2. Code review 发现 DB 旧数据向后兼容缺失（`current_phase='planning'` 的 story 无法 replay 和 recovery）。已通过 `_SPECIAL_REPLAY` 和 `_PHASE_SUCCESS_EVENT` 映射修复。

### Completion Notes List

- ✅ `CANONICAL_PHASES` 从 12 → 11（移除 `planning`），`CANONICAL_TRANSITIONS` 移除 `planning -> creating`
- ✅ `StoryLifecycle` 从 15 → 14 状态（移除 `planning` State 和 `plan_done` event）
- ✅ `start_create` 改为 `queued.to(creating)` 直接跳过 planning
- ✅ `PHASE_TO_STATUS` 移除 `”planning”: “planning”` 条目，保留 `creating/designing/validating -> “planning”` 高层聚合
- ✅ `_STRUCTURED_JOB_PROMPTS` 移除 `”planning”` 条目
- ✅ happy-path 事件序列和 phase 列表移除 `plan_done` 和 `planning`
- ✅ 向后兼容：`_SPECIAL_REPLAY[“planning”] = [“start_create”]`（旧 DB replay 到 creating）
- ✅ 向后兼容：`_PHASE_SUCCESS_EVENT[“planning”] = “create_done”`（旧 task recovery 提交正确事件）
- ✅ `confirm_batch()` seq=0 story 写入 `status=”planning”, current_phase=”creating”`
- ✅ `_KNOWN_MAIN_PHASES` 移除 `”planning”`
- ✅ `ato.yaml.example` 移除 `planner` role 和 `planning` phase
- ✅ CLI `_PHASE_ICONS` 移除 `”planning”` 条目
- ✅ `StoryStatus` literal 保持不变，高层 `”planning”` 状态语义完整保留
- ✅ 全部 1734 测试通过（含 3 个新增向后兼容测试，0 回归），ruff 无新增 lint 问题

### File List

- src/ato/state_machine.py (modified)
- src/ato/recovery.py (modified)
- src/ato/transition_queue.py (modified)
- src/ato/batch.py (modified)
- src/ato/config.py (modified)
- src/ato/cli.py (modified)
- src/ato/core.py (modified — docstring only)
- ato.yaml.example (modified)
- tests/unit/test_state_machine.py (modified)
- tests/unit/test_transition_queue.py (modified)
- tests/unit/test_recovery.py (modified)
- tests/unit/test_batch.py (modified)
- tests/unit/test_initial_dispatch.py (modified)
- tests/unit/test_config.py (modified)
- tests/unit/test_cli_plan.py (modified)
- tests/unit/test_convergent_loop.py (modified)
- tests/unit/test_story_detail_view.py (modified)
- tests/integration/test_state_persistence.py (modified)
- tests/integration/test_transition_queue.py (modified)
- tests/integration/test_crash_recovery.py (modified)
- tests/integration/test_config_workflow.py (modified)
