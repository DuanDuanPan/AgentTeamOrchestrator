# Story 8.2: 新增 Planning 阶段 — 使用 Claude 规划并行 Story

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a 操作者,
I want 工作流在 `creating` 之前显式增加 `planning` 阶段，并由 Claude planner 角色执行该阶段,
so that batch 中头部 story 会先完成规划再进入 create-story 生成阶段，同时系统为后续并行编排保留一个真实可追踪的 planning phase。

## Acceptance Criteria (AC)

### AC1: Canonical lifecycle 新增真实 `planning` phase，但不扩张高层 `StoryStatus`

```gherkin
Given 当前代码已经存在高层 StoryStatus 值 `planning`
When 更新生命周期 canonical phase 顺序与 phase->status 映射
Then `CANONICAL_PHASES` 的顺序变为 `planning -> creating -> validating -> ...`
And `PHASE_TO_STATUS` 将 `planning` / `creating` / `validating` 都映射到高层状态 `planning`
And 不新增新的 `StoryStatus` literal 值
```

### AC2: 状态机以最小事件改动插入 `planning`

```gherkin
Given 某个 story 当前处于 `queued`
When 流水线被启动
Then 现有 `start_create` 事件推进 `queued -> planning`
And 新增 `plan_done` 事件推进 `planning -> creating`
And 现有 `create_done` 仍表示 `creating -> validating`
And `planning` 与其他非 final phase 一样支持 `escalate -> blocked`
```

### AC3: 配置模板、PhaseDefinition 与阶段预览保持一致

```gherkin
Given `ato.yaml.example`、`build_phase_definitions()` 与 `StoryLifecycle.from_config()`
When 加载更新后的工作流配置
Then 第一个 phase definition 是 `planning`
And 该 phase 通过正常 `structured_job` 路径使用 Claude-based `planner` 角色
And `ato plan` / TUI phase-order 视图按新顺序显示完整生命周期
```

### AC4: Batch 激活、phase replay 与 recovery 都理解新的首阶段

```gherkin
Given active batch 的头部 story，或从 SQLite 恢复的 story
When batch confirm 写入初始 phase、TransitionQueue replay phase、或 RecoveryEngine 重启该 structured_job
Then 头部 story 使用 `status="planning", current_phase="planning"`
And replay 可以恢复 `planning` 与其后的所有 phase
And structured_job success 映射包含 `planning -> plan_done`
```

### AC5: 新 planning phase 不替换现有 batch 选择 / 推荐机制

```gherkin
Given 当前仓库已有 `LocalBatchRecommender`、`BatchProposal` 与 `ato batch select`
When 实现新的 `planning` phase
Then 本 story 不替换 `recommend_batch()` / `ato batch select`
And 不新增 planner 专用 DB schema、approval type 或新的 batch persistence 模型
And planner 执行复用现有 structured_job task / artifact 路径
```

## Tasks / Subtasks

- [ ] Task 1: 更新状态机 canonical 合同 (AC: #1, #2)
  - [ ] 1.1 在 `src/ato/state_machine.py` 中把 `planning` 插入 `CANONICAL_PHASES` 首位
  - [ ] 1.2 在 `PHASE_TO_STATUS` 中新增 `planning: "planning"`，并保持 `creating` / `validating` 继续映射到高层 `planning`
  - [ ] 1.3 在 `CANONICAL_TRANSITIONS` 中新增 `planning -> creating`，保留 `creating -> validating`
  - [ ] 1.4 `StoryLifecycle` 新增 `planning = State()`
  - [ ] 1.5 保留现有事件名 `start_create`，但其目标改为 `queued.to(planning)`；新增 `plan_done = planning.to(creating)`
  - [ ] 1.6 `escalate` 联合 transition 补入 `planning.to(blocked)`
  - [ ] 1.7 **不要**修改 `src/ato/models/schemas.py` 中的 `StoryStatus` literal；该高层状态已存在

- [ ] Task 2: 对齐配置模板与 phase-order 消费方 (AC: #1, #3)
  - [ ] 2.1 在 `ato.yaml.example` 中新增 `planner` 角色，并在 `phases:` 首位加入 `planning`
  - [ ] 2.2 更新 `src/ato/config.py` / `tests/integration/test_config_workflow.py` / `tests/unit/test_state_machine.py` 对 phase 顺序的期望
  - [ ] 2.3 更新 `src/ato/cli.py::render_plan()` 与 `tests/unit/test_cli_plan.py` 的阶段序列基线，从 12 个阶段调整为包含 `planning` 的新序列
  - [ ] 2.4 合并 `ato.yaml.example` 改动时兼容 Story 8.1 的 role-field 可选化方向，避免把邻近 story 的模板改动覆盖掉

- [ ] Task 3: 对齐 batch / replay / recovery 路径 (AC: #4, #5)
  - [ ] 3.1 在 `src/ato/batch.py::confirm_batch()` 中把头部 story 的初始写入改为 `status="planning", current_phase="planning"`
  - [ ] 3.2 在 `src/ato/transition_queue.py` 中更新 `_HP_EVENTS`、`_HP_PHASES`、`_HAPPY_PATH_EVENTS` 与相关 replay 测试，使 `planning` 成为可恢复 phase
  - [ ] 3.3 在 `src/ato/recovery.py::_PHASE_SUCCESS_EVENT` 中新增 `planning: "plan_done"`，并更新 structured-job recovery / restart 相关测试
  - [ ] 3.4 更新 `tests/unit/test_batch.py`、`tests/unit/test_transition_queue.py`、`tests/integration/test_transition_queue.py`、`tests/integration/test_state_persistence.py`、`tests/unit/test_recovery.py`、`tests/integration/test_crash_recovery.py` 中对首阶段与 success event 的既有断言

- [ ] Task 4: 收紧显示与回归测试范围 (AC: #3, #4)
  - [ ] 4.1 若 CLI phase icon 需要显式区分 `planning`，更新 `src/ato/cli.py::_PHASE_ICONS` 与相关 batch CLI 断言
  - [ ] 4.2 更新 `tests/unit/test_story_status_line.py`、`tests/unit/test_story_detail_view.py`、`tests/unit/test_db.py` 等依赖 phase-count / first-active-phase 假设的测试
  - [ ] 4.3 对 TUI 代码遵循“优先复用 `CANONICAL_PHASES`”原则；除非确有硬编码逻辑失败，不要为 dashboard / story detail 发明额外 phase 映射分支

## Dev Notes

### 关键实现判断

- **`planning` 高层状态已经存在。** `src/ato/models/schemas.py` 与 TUI theme 已支持 `planning`，本 story 的核心是把它从“creating/validating 的聚合状态”提升为一个真实 phase，而不是再扩一个新 status。
- **不要把 `start_create` 全仓改名为 `start_plan`。** 当前仓库已有大量 `start_create` fixture / replay / transition test；最小、最稳的方案是保留 `start_create` 作为 `queued -> planning` 事件，再新增 `plan_done`。
- **真正会受影响的不只是状态机。** `batch.py` 现在直接把头部 story 写成 `current_phase="creating"`；`transition_queue.py` 的 replay 表；`recovery.py` 的 `_PHASE_SUCCESS_EVENT`；以及大量单元/集成测试都会一起变。
- **`core.py::_poll_cycle()` 不是 planning 成功事件的主修改点。** 那里的 `phase_success_event` 只服务 `interactive_session`；新的 `planning` phase 是 `structured_job`，其 success 事件应走 recovery / restart 的 structured-job success map，而不是往 interactive 映射里硬塞。
- **本 story 不重写现有 batch 推荐器。** `LocalBatchRecommender` 与 `ato batch select` 已是现有 batch 入口；planning phase 只是在 story 生命周期中显式补入规划阶段，不要趁机发明新的 planner-only schema。

### Previous Story Intelligence (from 1.5 / 2B.5 / Epic 8 shared files)

1. Story 1.5 (`ato plan`) 与其测试基线仍然写死 12 个阶段；新增 `planning` 后，plan 预览、story detail phase-order 与相关断言都要同步更新。
2. Story 2B.5 当前把 batch 头部 story 定义为 `status="planning", current_phase="creating"`；8.2 会显式修改这个合同，因此 batch 进度统计与 CLI/TUI 用例必须跟着调整。
3. Story 8.1 / 8.3 / 8.4 也会修改 `config.py` / `ato.yaml.example`；实现 8.2 时要合并 planner role 与首 phase 改动，不能覆盖邻近 story 的模板/配置修改。

### Scope Boundary

- **IN:** 真实 `planning` phase、planner role、batch/replay/recovery 对齐、phase-order / test baseline 更新
- **OUT:** 替换 `LocalBatchRecommender`、新增 planner 专用 DB 表、planner 审批流、自动 batch 重新排序
- **OUT:** 新增 `StoryStatus` 值或重设计高层 status 语义

### Project Structure Notes

- 主要修改文件：
  - `src/ato/state_machine.py`
  - `src/ato/batch.py`
  - `src/ato/transition_queue.py`
  - `src/ato/recovery.py`
  - `src/ato/cli.py`
  - `ato.yaml.example`
- 重点测试文件：
  - `tests/unit/test_state_machine.py`
  - `tests/unit/test_transition_queue.py`
  - `tests/integration/test_transition_queue.py`
  - `tests/integration/test_state_persistence.py`
  - `tests/unit/test_batch.py`
  - `tests/unit/test_recovery.py`
  - `tests/integration/test_crash_recovery.py`
  - `tests/unit/test_cli_plan.py`

### Suggested Verification

- `uv run pytest tests/unit/test_state_machine.py tests/unit/test_transition_queue.py tests/unit/test_batch.py tests/unit/test_recovery.py tests/unit/test_cli_plan.py -v`
- `uv run pytest tests/integration/test_transition_queue.py tests/integration/test_state_persistence.py tests/integration/test_crash_recovery.py -v`

### References

- [Source: src/ato/state_machine.py — `CANONICAL_PHASES`, `PHASE_TO_STATUS`, `CANONICAL_TRANSITIONS`, `StoryLifecycle`]
- [Source: src/ato/transition_queue.py — replay tables / `_replay_to_phase()`]
- [Source: src/ato/recovery.py — `_PHASE_SUCCESS_EVENT`]
- [Source: src/ato/batch.py — `confirm_batch()` 初始 phase 写入]
- [Source: src/ato/cli.py — `render_plan()`, `_PHASE_ICONS`]
- [Source: ato.yaml.example — 当前 phase / role 模板]
- [Source: _bmad-output/implementation-artifacts/1-5-ato-plan-phase-preview.md]
- [Source: _bmad-output/implementation-artifacts/2b-5-batch-select-status.md]
- [Source: _bmad-output/planning-artifacts/prd.md — 并行推进与计划预览目标]
- [Source: _bmad-output/planning-artifacts/research/technical-claude-codex-cli-integration-research-2026-03-24.md — Claude CLI headless/structured execution能力]

## Change Log

- 2026-03-28: `validate-create-story` 修订 —— 将 Story 从“只改状态机 + schema + `_poll_cycle`”收敛为真实的 phase insertion 合同；保留 `start_create`、新增 `plan_done` 以减少 repo-wide churn；补齐 `batch.py` / `transition_queue.py` / `recovery.py` 的影响面，并补回模板 baseline / Scope Boundary / Dev Agent Record 结构

## Dev Agent Record

### Agent Model Used

TBD

### Debug Log References

### Completion Notes List

### File List
