# Story 9.2: Workspace 概念引入 — 区分 Main 与 Worktree 执行环境

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->
<!-- Depends on: Story 9.1 (designing phase must exist before assigning workspace) -->

## Story

As a 操作者,
I want 每个工作流阶段明确标注其执行环境（main 分支 vs worktree 分支），系统根据 workspace 类型决定是否创建 worktree,
so that story 规格创建与主仓库控制阶段在 main 上执行，而真正修改代码的阶段在隔离 worktree 中执行，worktree 只在真正需要时才创建。

## Acceptance Criteria (AC)

### AC1: PhaseConfig 新增 workspace 字段

```gherkin
Given 当前 PhaseConfig 模型只有 name / role / type / next_on_success / next_on_failure
When 新增 workspace 字段
Then PhaseConfig 接受 `workspace: Literal["main", "worktree"]`
And 默认值为 `"worktree"`（向后兼容现有配置）
And PhaseDefinition dataclass 同步新增 `workspace: str` 字段
And build_phase_definitions() 正确传播 workspace 值
```

### AC2: 配置模板正确标注各阶段的 workspace

```gherkin
Given ato.yaml.example 中的阶段定义
When 加载更新后的配置
Then planning / creating / designing / validating / dev_ready / merging / regression 标记为 `workspace: main`
And developing / reviewing / fixing / qa_testing / uat 标记为 `workspace: worktree`
And 配置验证通过
```

### AC3: workspace: main 的阶段不要求 worktree 存在

```gherkin
Given story 处于 planning / creating / designing / validating / dev_ready 等 main 阶段
When RecoveryEngine 或 Orchestrator dispatch 该阶段的 task
Then cwd 设置为 project_root（而非 worktree_path）
And 不要求 story.worktree_path 有值
And dispatch 不因 worktree 缺失而 fail
```

### AC4: workspace: worktree 的阶段仍要求 worktree 存在

```gherkin
Given story 处于 developing / reviewing / fixing / qa_testing / uat 等 worktree 阶段
When dispatch 该阶段的 task
Then cwd 设置为 worktree_path
And worktree_path 为 None 时系统会先尝试创建（若这是首次进入 worktree）或标记 dispatch_failed 并创建 approval
And 行为保持当前 worktree 阶段的语义，不把这些 phase 回退到 main
```

### AC5: WorktreeManager.create() 仅在首次进入 `developing` 时调用

```gherkin
Given story 从 validating → dev_ready → developing 推进
When story 首次进入 workspace: worktree 的阶段（即 `developing`）
Then 系统调用 WorktreeManager.create(story_id, base_ref="HEAD") 创建 worktree
And 该 HEAD 已包含 Story 9.3 中在 main 上完成的最新 spec commit
And 后续 reviewing / fixing / qa_testing / uat 复用已有 worktree，不重复创建
```

### AC6: validating / reviewing / regression 的真实运行路径与 workspace 一致

```gherkin
Given `validating` 是 convergent_loop phase，`reviewing` / `qa_testing` 也是 convergent_loop phase
When 系统 dispatch validating
Then validating 的 cwd / prompt 上下文使用 project_root（workspace: main）
And reviewing / qa_testing 仍使用 worktree_path（workspace: worktree）

Given merging / regression 属于 main 仓库控制流
When merge queue 执行 fast-forward merge 与 regression
Then 继续在 project_root / main 上执行，不被错误改造成 worktree-phase
```

### AC7: 所有现有测试通过，新增 workspace 覆盖测试

```gherkin
Given 完整测试套件
When 运行所有测试
Then 所有现有测试通过（修改后）
And 新增 ≥6 个 workspace 相关测试：
  - PhaseConfig workspace 默认值为 `worktree`
  - PhaseConfig / PhaseDefinition 正确解析 `workspace: main`
  - workspace: main dispatch 不要求 worktree
  - validating convergent loop 使用 project_root
  - 第一次进入 developing 时创建 worktree
  - regression main-path recovery / dispatch 保持在 project_root
```

## Tasks / Subtasks

- [ ] Task 1: PhaseConfig + PhaseDefinition 新增 workspace 字段 (AC: #1)
  - [ ] 1.1 `src/ato/config.py::PhaseConfig` 新增 `workspace: Literal["main", "worktree"] = "worktree"`
  - [ ] 1.2 `src/ato/config.py::PhaseDefinition` 新增 `workspace: str` 字段
  - [ ] 1.3 `build_phase_definitions()` 传播 `phase.workspace` 到 `PhaseDefinition`
  - [ ] 1.4 更新 `tests/unit/test_config.py`：新字段解析、默认值断言

- [ ] Task 2: 配置模板标注真实的 workspace 划分 (AC: #2)
  - [ ] 2.1 `ato.yaml.example` 为每个 phase 增加 `workspace`
  - [ ] 2.2 纠正阶段归属：`dev_ready`、`merging`、`regression` 必须标为 `main`
  - [ ] 2.3 更新 `tests/integration/test_config_workflow.py`、`tests/unit/test_cli_plan.py`：阶段顺序与 workspace 展示基线

- [ ] Task 3: Recovery / restart 路径支持 workspace-aware cwd (AC: #3, #4)
  - [ ] 3.1 `src/ato/recovery.py::_resolve_phase_config_static()` 返回值增加 `workspace`
  - [ ] 3.2 `src/ato/recovery.py::_build_dispatch_options()` 根据 workspace 决定 cwd：main → project_root，worktree → worktree_path
  - [ ] 3.3 `src/ato/recovery.py::_dispatch_structured_job()` 对 main phase 不要求 worktree；对 worktree phase 继续要求
  - [ ] 3.4 `src/ato/core.py::_dispatch_batch_restart()` 复用相同 workspace-aware options
  - [ ] 3.5 更新 `tests/unit/test_recovery.py`、`tests/unit/test_core.py`

- [ ] Task 4: validating 的正常 / recovery dispatch 改为 main-path，review / qa 仍走 worktree (AC: #3, #4, #6)
  - [ ] 4.1 `src/ato/convergent_loop.py` 中将 validating 的路径解析从“总是 worktree”改为 workspace-aware；不要影响 reviewing / qa_testing 的 worktree 语义
  - [ ] 4.2 `src/ato/recovery.py::_dispatch_convergent_loop()` 对 validating 不再因 `worktree_path is None` 直接失败
  - [ ] 4.3 更新 `_CONVERGENT_LOOP_PROMPTS` / 相关 prompt builder，使 validating 使用 `project_root`，reviewing / qa_testing 继续使用 `worktree_path`
  - [ ] 4.4 更新 `tests/unit/test_convergent_loop.py`、`tests/unit/test_recovery.py`

- [ ] Task 5: Worktree 创建时机调整为 `start_dev → developing` (AC: #5)
  - [ ] 5.1 在 TransitionQueue post-commit hook 或真正的 phase-entry dispatch 路径中，当 story 首次进入 `developing` 且 `worktree_path is None` 时调用 `WorktreeManager.create()`
  - [ ] 5.2 recovery / restart 路径下，如果 phase 已是 worktree phase 且 `worktree_path is None`，先尝试 `WorktreeManager.create()` 再 dispatch
  - [ ] 5.3 确保幂等性：已有 worktree 不重复创建（`WorktreeManager.create()` 已有幂等逻辑）
  - [ ] 5.4 更新 `tests/unit/test_worktree_mgr.py`、`tests/unit/test_transition_queue.py`

- [ ] Task 6: 新增 workspace 覆盖测试 (AC: #7)
  - [ ] 6.1 `tests/unit/test_config.py`：`test_phase_config_workspace_default_worktree`
  - [ ] 6.2 `tests/unit/test_config.py`：`test_phase_config_workspace_main_parsed`
  - [ ] 6.3 `tests/unit/test_recovery.py`：`test_main_workspace_dispatch_without_worktree`
  - [ ] 6.4 `tests/unit/test_convergent_loop.py`：`test_validating_uses_project_root`
  - [ ] 6.5 `tests/unit/test_transition_queue.py`：`test_start_dev_creates_worktree_once`
  - [ ] 6.6 `tests/unit/test_recovery.py` 或 `tests/integration/test_crash_recovery.py`：regression main-path recovery 保持 project_root

## Dev Notes

### 关键实现判断

- **workspace 默认 `"worktree"` 可保持向后兼容，但 Epic 9 这批 phase 需要显式覆写。** 不在 `ato.yaml` 中显式写出的旧项目仍沿用现状。
- **`dev_ready` 必须是 `main`，不是 `worktree`。** 否则 Story 9.3 的 batch spec commit 无法在创建 worktree 之前完成，也会与当前 `validate_pass → dev_ready → start_dev` 的交接语义冲突。
- **`merging` / `regression` 已经是 main 仓库控制流。** 当前 `merge_queue.py` 的 fast-forward merge 与 `_run_regression_test()` 都直接在 project_root 上执行；不要把它们错误迁回 worktree。
- **当前 validating 正常路径仍假设 worktree。** `ConvergentLoop.run_first_review()` / `_resolve_worktree_path()` 需要被 generalize，否则仅改 recovery 仍会让正常 validating 路径继续跑错 cwd。
- **interactive phases 当前全部是 worktree。** `developing` / `uat` 仍然需要 worktree，不需要发明 `workspace: main` 的 interactive restart 语义。
- **worktree 创建触发点应该是进入 `developing`。** 这与 PRD 里的“验证通过后，开发在独立 worktree 中进行”一致，也给 Story 9.3 留出在 `dev_ready` 上做 main-branch spec commit 的窗口。

### Scope Boundary

- **IN:** workspace 字段、workspace-aware cwd 分流、validating main-path 化、worktree 创建时机调整、测试基线更新
- **OUT:** batch spec commit（Story 9.3）
- **OUT:** 条件跳过（Story 9.3）
- **OUT:** 修改 `WorktreeManager` 的 create/cleanup/rebase 内部 Git 语义
- **OUT:** remote push / 远端同步策略

### Project Structure Notes

- 主要修改文件：
  - `src/ato/config.py`
  - `src/ato/recovery.py`
  - `src/ato/core.py`
  - `src/ato/convergent_loop.py`
  - `src/ato/transition_queue.py`
  - `ato.yaml.example`
- 重点测试文件：
  - `tests/unit/test_config.py`
  - `tests/integration/test_config_workflow.py`
  - `tests/unit/test_recovery.py`
  - `tests/unit/test_core.py`
  - `tests/unit/test_convergent_loop.py`
  - `tests/unit/test_transition_queue.py`
  - `tests/unit/test_worktree_mgr.py`
  - `tests/integration/test_crash_recovery.py`

### Suggested Verification

```bash
uv run pytest tests/unit/test_config.py tests/unit/test_recovery.py tests/unit/test_core.py tests/unit/test_convergent_loop.py tests/unit/test_transition_queue.py tests/unit/test_worktree_mgr.py -v
uv run pytest tests/integration/test_config_workflow.py tests/integration/test_crash_recovery.py -v
```

### References

- [Source: src/ato/config.py — `PhaseConfig`, `PhaseDefinition`, `build_phase_definitions()`]
- [Source: src/ato/recovery.py — `_build_dispatch_options()`, `_dispatch_structured_job()`, `_dispatch_convergent_loop()`]
- [Source: src/ato/core.py — `_dispatch_batch_restart()` / restart dispatch 路径]
- [Source: src/ato/convergent_loop.py — `run_first_review()`, `_resolve_worktree_path()`]
- [Source: src/ato/worktree_mgr.py — `WorktreeManager.create()`]
- [Source: src/ato/merge_queue.py — `_execute_merge()`, `_run_regression_test()`]
- [Depends: Story 9.1 — designing phase 必须已存在才能标注 workspace]

### Previous Story Intelligence

1. **Story 2B.4 已把 worktree 生命周期基础设施做完，但当时把“何时创建”留给后续集成。** 9.2 正是把这个触发时机补完整。
2. **Story 8.2 让 `planning` 成为首个真实 phase；Story 9.1 再插入 `designing`。** 这意味着 pre-worktree/main-path 的阶段现在不止一个，workspace 概念已经不能再靠隐式约定。
3. **当前 merge queue 已经证明 merge / regression 必须在 main 上运行。** 9.2 不能把 workspace 设计成“developing 及之后一律 worktree”这种过度简化版本。

## Change Log

- 2026-03-28: Story 创建
- 2026-03-28: `validate-create-story` 修订 —— 纠正 `dev_ready` / `merging` / `regression` 的 workspace 归属；把 validating main-path 的真实改动面扩展到 `convergent_loop.py`；将 worktree 创建时机收紧为首次进入 `developing`；补回 validation note、Scope Boundary、Previous Story Intelligence 与 Dev Agent Record 结构

## Dev Agent Record

### Agent Model Used

待 dev-story 填写

### Debug Log References

### Completion Notes List

### File List
