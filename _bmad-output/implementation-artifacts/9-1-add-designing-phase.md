# Story 9.1: 新增 Designing 阶段 — 可选的 UX 设计环节

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a 操作者,
I want 工作流在 `creating` 之后、`validating` 之前显式增加可选的 `designing` 阶段，由 UX Designer 角色执行,
so that 涉及 UI 的 story 在进入 validate 之前有一个专门的 UX 设计环节，而纯后端 story 可以在后续 Story 9.3 中被安全跳过。

## Acceptance Criteria (AC)

### AC1: Canonical lifecycle 新增 `designing` phase，复用高层 `planning` StoryStatus

```gherkin
Given 当前 CANONICAL_PHASES 序列为 planning → creating → validating → ...
When 更新生命周期 canonical phase 顺序
Then `CANONICAL_PHASES` 的顺序变为 `planning → creating → designing → validating → ...`
And `PHASE_TO_STATUS` 将 `designing` 映射到高层状态 `planning`
And 不新增新的 `StoryStatus` literal 值
```

### AC2: 状态机以最小事件改动插入 `designing`

```gherkin
Given 某个 story 当前处于 `creating`
When creating 阶段完成
Then 现有 `create_done` 事件推进 `creating → designing`（原来是 creating → validating）
And 新增 `design_done` 事件推进 `designing → validating`
And `designing` 与其他非 final phase 一样支持 `escalate → blocked`
And `validating` 的 `validate_fail` 仍回退到 `creating`（不变）
```

### AC3: 配置模板、PhaseDefinition 与阶段预览保持一致

```gherkin
Given `ato.yaml.example`、`build_phase_definitions()` 与 `StoryLifecycle.from_config()`
When 加载更新后的工作流配置
Then phases 列表在 `creating` 之后包含 `designing`
And 该 phase 使用 `structured_job` 类型、`ux_designer` 角色
And `ato plan` / TUI phase-order 视图按新顺序显示完整生命周期
```

### AC4: Recovery / replay 路径理解新阶段

```gherkin
Given active story，或从 SQLite 恢复的 story
When story 处于 `designing` phase 时系统崩溃并恢复
Then `_PHASE_SUCCESS_EVENT` 包含 `designing: "design_done"`
And TransitionQueue replay 表包含 `designing` / `design_done`
And RecoveryEngine 可正确 reschedule designing phase 的 structured_job
```

### AC5: Pre-worktree structured_job 阶段 story 级串行执行

```gherkin
Given batch 内有多个 story 需要先通过尚未创建 worktree 的阶段（planning / creating / designing）
When orchestrator 或 recovery dispatch 这些阶段的 structured_job
Then 同一时刻最多只有 1 个 story 在 project_root 上执行这类 agent task
And 串行控制是共享的 dispatch 限流，而不是某个临时 `SubprocessManager` 实例自己的 semaphore
And 后续 Story 9.2 可在同一限流机制上继续接入 `workspace: main` 的其他阶段
```

### AC6: Designing 完成前验证 UX 产出物存在性（Gate 门控）

```gherkin
Given designing 阶段的 agent task 执行完毕
When 系统准备提交 `design_done` 事件
Then 先验证设计产出物存在：
  - story 规格仍位于 `_bmad-output/implementation-artifacts/{story_id}.md`
  - UX 设计目录位于 `_bmad-output/implementation-artifacts/{story_id}-ux/`
  - 该目录下至少存在 1 个设计 artifact（`.md` / `.pen` / `.png`）
And 验证通过 → 提交 `design_done`
And 验证失败 → 创建 approval（类型 `needs_human_review`，payload 含 `task_id`），不自动推进
And structlog 记录 `design_gate_check` 事件（含 `story_id`, `task_id`, `artifact_dir`, `artifact_count`, `result`）
```

### AC7: 所有现有测试通过，新增 designing 覆盖测试

```gherkin
Given 所有状态机、配置、recovery、TUI 相关测试
When 运行完整测试套件
Then 所有现有测试通过（修改后的断言反映新阶段序列）
And 新增 ≥6 个测试：
  - designing → validating (`design_done`)
  - creating → designing (`create_done` 新目标)
  - designing → blocked (`escalate`)
  - replay 到 designing
  - designing phase crash-recovery reschedule
  - design gate 通过 / 缺失两种路径
```

## Tasks / Subtasks

- [x] Task 1: 更新状态机 canonical 合同 (AC: #1, #2)
  - [x] 1.1 在 `src/ato/state_machine.py` 中把 `designing` 插入 `CANONICAL_PHASES`，位于 `creating` 之后、`validating` 之前
  - [x] 1.2 在 `PHASE_TO_STATUS` 中新增 `designing: "planning"`
  - [x] 1.3 在 `CANONICAL_TRANSITIONS` 中：`creating` 的 success 从 `validating` 改为 `designing`；新增 `designing: ("validating", None)`
  - [x] 1.4 `StoryLifecycle` 新增 `designing = State()`
  - [x] 1.5 现有 `create_done` 目标改为 `creating.to(designing)`；新增 `design_done = designing.to(validating)`
  - [x] 1.6 `escalate` 联合 transition 补入 `designing.to(blocked)`

- [x] Task 2: 对齐配置模板与 phase-order 消费方 (AC: #3)
  - [x] 2.1 在 `ato.yaml.example` 中新增 `ux_designer` 角色（`cli: claude`），并在 `phases:` 中 `creating` 之后加入 `designing`
  - [x] 2.2 更新 `src/ato/cli.py::_PHASE_ICONS` 新增 `designing` 图标
  - [x] 2.3 更新 `tests/unit/test_state_machine.py`：happy path 事件序列、`_canonical_phase_defs()`、`PHASE_TO_STATUS` 断言、escalatable states 集合
  - [x] 2.4 更新 `tests/unit/test_config.py`：`len(config.phases)` 断言、`ato.yaml.example` 加载测试
  - [x] 2.5 更新 `tests/unit/test_cli_plan.py`：阶段序列基线
  - [x] 2.6 更新 `tests/unit/test_story_detail_view.py`、`tests/unit/test_story_status_line.py`：阶段计数断言

- [x] Task 3: 对齐 recovery / replay 路径 (AC: #4)
  - [x] 3.1 在 `src/ato/recovery.py::_PHASE_SUCCESS_EVENT` 中新增 `designing: "design_done"`
  - [x] 3.2 在 `src/ato/transition_queue.py` 中更新 `_HP_EVENTS`、`_HP_PHASES`、`_HAPPY_PATH_EVENTS`，插入 `designing` / `design_done`
  - [x] 3.3 更新 `tests/unit/test_recovery.py`、`tests/integration/test_crash_recovery.py` 中 phase 枚举断言
  - [x] 3.4 更新 `tests/unit/test_transition_queue.py`、`tests/integration/test_transition_queue.py`、`tests/integration/test_state_persistence.py`

- [x] Task 4: Pre-worktree structured_job 串行控制 (AC: #5)
  - [x] 4.1 在 `src/ato/core.py` / `src/ato/recovery.py` 的共享 dispatch 路径上增加单实例 main-path limiter（max=1）
  - [x] 4.2 明确不要把该限流只放在某个临时 `SubprocessManager` 实例里，因为现有代码会在不同路径上不断新建 manager
  - [x] 4.3 新增 `tests/unit/test_core.py` 或 `tests/unit/test_recovery.py`：同一时刻仅允许 1 个 pre-worktree structured_job 执行

- [x] Task 5: Designing artifact gate 验证 (AC: #6)
  - [x] 5.1 在 structured_job 成功后、提交 `design_done` 前的 success-event 路径增加 gate helper，不放在 `state_machine.py` 的 transition handler 中
  - [x] 5.2 设计产出物路径与当前 `story_location` 对齐：story 文件仍在 `_bmad-output/implementation-artifacts/{story_id}.md`，设计目录为 `_bmad-output/implementation-artifacts/{story_id}-ux/`
  - [x] 5.3 验证失败时创建 `needs_human_review` approval，payload 含 `task_id`、`artifact_dir`、`artifact_count`
  - [x] 5.4 新增 `tests/unit/test_core.py` 或 `tests/unit/test_recovery.py`：gate 通过 / 缺失两种路径

- [x] Task 6: 新增 designing 覆盖测试 (AC: #7)
  - [x] 6.1 `tests/unit/test_state_machine.py`：`test_designing_to_validating`
  - [x] 6.2 `tests/unit/test_state_machine.py`：`test_creating_to_designing`
  - [x] 6.3 `tests/unit/test_state_machine.py`：`test_designing_escalate`
  - [x] 6.4 `tests/unit/test_transition_queue.py`：`test_replay_to_designing`
  - [x] 6.5 `tests/unit/test_recovery.py`：designing phase crash-recovery reschedule 断言
  - [x] 6.6 `tests/unit/test_core.py` 或 `tests/unit/test_recovery.py`：design gate 断言

## Dev Notes

### 关键实现判断

- **遵循 Story 8.2 的 phase-insertion 模式。** 这次仍然是“在 `CANONICAL_PHASES` 中插入真实 phase”，不需要扩展 `StoryStatus`，也不应发明新的高层状态。
- **`create_done` 改向 + `design_done` 新增是最小改动。** 保留现有 `start_create` / `plan_done` 语义，避免 repo-wide event rename churn。
- **artifact gate 必须放在 success-event 提交路径，不是 transition handler。** 当前 `TransitionQueue` 只消费已经生成好的事件；`state_machine.py` 本身没有 task/artifact 上下文，也无法安全创建 approval。
- **设计文件路径必须与当前 story 存储位置对齐。** `sprint-status.yaml` 的 `story_location` 已固定为 `_bmad-output/implementation-artifacts`；不要另起 `_bmad-output/stories/` 第二套树。
- **pre-worktree 串行控制不能只依赖 `SubprocessManager` 自身 semaphore。** 现有 core/recovery 会按路径不断新建 `SubprocessManager`，实例级 semaphore 无法提供全局串行保证。
- **运行时条件跳过留给 Story 9.3。** 9.1 只负责让 `designing` 真实存在，并给后续 skip 提供明确的 state / event 落点。

### Scope Boundary

- **IN:** `designing` phase 插入 canonical lifecycle、配置模板、replay/recovery 对齐、pre-worktree structured_job 串行控制、design artifact gate、测试 baseline 更新
- **OUT:** `skip_when` 条件跳过（Story 9.3）
- **OUT:** 显式 workspace 字段与 main/worktree 全量划分（Story 9.2）
- **OUT:** UX Designer prompt 内容优化、Pencil MCP 设计模板细化
- **OUT:** 新增 `StoryStatus` 值或重设计高层状态语义

### Project Structure Notes

- 主要修改文件：
  - `src/ato/state_machine.py`
  - `src/ato/transition_queue.py`
  - `src/ato/recovery.py`
  - `src/ato/core.py`
  - `src/ato/cli.py`
  - `ato.yaml.example`
- 重点测试文件：
  - `tests/unit/test_state_machine.py`
  - `tests/unit/test_config.py`
  - `tests/unit/test_transition_queue.py`
  - `tests/integration/test_transition_queue.py`
  - `tests/integration/test_state_persistence.py`
  - `tests/unit/test_recovery.py`
  - `tests/integration/test_crash_recovery.py`
  - `tests/unit/test_core.py`
  - `tests/unit/test_cli_plan.py`
  - `tests/unit/test_story_detail_view.py`
  - `tests/unit/test_story_status_line.py`

### Suggested Verification

```bash
uv run pytest tests/unit/test_state_machine.py tests/unit/test_config.py tests/unit/test_transition_queue.py tests/unit/test_recovery.py tests/unit/test_core.py tests/unit/test_cli_plan.py tests/unit/test_story_detail_view.py tests/unit/test_story_status_line.py -v
uv run pytest tests/integration/test_transition_queue.py tests/integration/test_state_persistence.py tests/integration/test_crash_recovery.py -v
```

### References

- [Source: src/ato/state_machine.py — `CANONICAL_PHASES`, `PHASE_TO_STATUS`, `CANONICAL_TRANSITIONS`, `StoryLifecycle`]
- [Source: src/ato/transition_queue.py — replay tables / `_replay_to_phase()` / `_consumer()`]
- [Source: src/ato/recovery.py — `_PHASE_SUCCESS_EVENT`, `_dispatch_structured_job()`]
- [Source: src/ato/core.py — restart dispatch 路径 / approval 处理]
- [Source: ato.yaml.example — 当前 phase / role 模板]
- [Source: _bmad-output/implementation-artifacts/sprint-status.yaml — `story_location` 当前合同]
- [Precedent: _bmad-output/implementation-artifacts/8-2-add-planning-phase.md — 同类 phase insertion 的成功模式]

### Previous Story Intelligence

1. **Story 8.2 已证明 phase insertion 的真实影响面不止状态机。** replay、recovery、plan 预览和测试基线都会跟着变。
2. **Story 2B.4 已把 worktree 路径固定为 `.worktrees/{story_id}`，也说明 worktree 是后续阶段基础设施，不应提前混入 story spec 存储。**
3. **当前 `TransitionQueue` 只有 `done` 的 post-commit hook。** 这次如需在 `design_done` 前做 gate，应当扩展这一类 post-commit / pre-submit 机制，而不是把逻辑塞进状态机类本身。

## Change Log

- 2026-03-28: Story 创建
- 2026-03-28: `validate-create-story` 修订 —— 去除与当前仓库不一致的 `_bmad-output/stories/` 路径；将 design gate 落点从”transition handler”收紧到真实 success-event 提交路径；将 main 串行控制收敛为共享 dispatch limiter；补回 Scope Boundary、Previous Story Intelligence 与 Dev Agent Record 结构
- 2026-03-28: 实现完成 — 6 个 task 全部交付，1505 测试全部通过，17 个新测试覆盖 designing 阶段各路径

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6 (1M context)

### Debug Log References

无调试问题。

### Completion Notes List

- 在 `state_machine.py` 中插入 `designing` phase，复用 `planning` 高层状态，遵循 Story 8.2 的 phase-insertion 模式
- `create_done` 改向 `designing`，新增 `design_done` 事件推进 `designing → validating`，最小改动策略
- `ato.yaml.example` 新增 `ux_designer` 角色和 `designing` structured_job 阶段
- replay/recovery 路径完整对齐：`_HP_EVENTS`、`_HP_PHASES`、`_PHASE_SUCCESS_EVENT` 均已更新
- Pre-worktree 串行控制：在 `core.py` 中新增共享 `asyncio.Semaphore(1)` limiter（`PRE_WORKTREE_PHASES = {planning, creating, designing}`），core.py 和 recovery.py 的 dispatch 路径均 acquire/release
- Design artifact gate：`check_design_gate()` 检查 `{story_id}-ux/` 目录下是否有 `.md/.pen/.png` 文件，失败时创建 `needs_human_review` approval
- 全部 1505 个测试通过（0 回归），新增 17 个 designing 相关测试
- 也修复了 `test_convergent_loop.py` 和 `test_config_workflow.py` 中遗漏的 designing 对齐

### File List

**修改的源文件：**
- `src/ato/state_machine.py` — 插入 designing state/transitions
- `src/ato/transition_queue.py` — replay tables 更新
- `src/ato/recovery.py` — _PHASE_SUCCESS_EVENT + design gate + pre-worktree limiter
- `src/ato/core.py` — PRE_WORKTREE_PHASES + get_main_path_limiter() + check_design_gate() + dispatch limiter
- `src/ato/cli.py` — _PHASE_ICONS 新增 designing
- `ato.yaml.example` — ux_designer 角色 + designing phase

**修改的测试文件：**
- `tests/unit/test_state_machine.py` — happy path/escalate/PHASE_TO_STATUS 更新 + TestDesigningPhase 新增
- `tests/unit/test_config.py` — phase list 断言更新
- `tests/unit/test_cli_plan.py` — phase list/count 更新
- `tests/unit/test_story_detail_view.py` — PHASE_ORDER 长度更新
- `tests/unit/test_transition_queue.py` — FIFO test + replay_to_designing 新增
- `tests/unit/test_recovery.py` — TestDesigningPhaseRecovery 新增
- `tests/unit/test_core.py` — TestPreWorktreeSerialControl + TestDesignGate 新增
- `tests/unit/test_convergent_loop.py` — advance-to-reviewing path 更新
- `tests/integration/test_transition_queue.py` — create_done → designing 更新
- `tests/integration/test_state_persistence.py` — happy path + event sequences 更新
- `tests/integration/test_config_workflow.py` — phase list 断言更新

**修改的配置/状态文件：**
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — story status 更新
- `_bmad-output/implementation-artifacts/9-1-add-designing-phase.md` — story file 更新
