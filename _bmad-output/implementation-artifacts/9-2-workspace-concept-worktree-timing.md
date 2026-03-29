# Story 9.2: Workspace 概念引入 — 区分 Main 与 Worktree 执行环境

Status: done

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
Then PhaseConfig 接受 `workspace: Literal["main", "worktree"] | None = None`
And 省略时由 `build_phase_definitions()` 按 phase 名推断（已知 main phase → "main"，其余 → "worktree"）
And PhaseDefinition dataclass 同步新增 `workspace: str` 字段（始终为 "main" 或 "worktree"）
And build_phase_definitions() 通过 `_resolve_workspace()` 将 None 解析为具体值后传播
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
  - PhaseConfig workspace 省略时原始值为 `None`，并由 `build_phase_definitions()` 按 phase 名推断
  - PhaseConfig / PhaseDefinition 正确解析 `workspace: main`
  - workspace: main dispatch 不要求 worktree
  - validating convergent loop 使用 project_root
  - 第一次进入 developing 时创建 worktree
  - regression main-path recovery / dispatch 保持在 project_root
```

## Tasks / Subtasks

- [x] Task 1: PhaseConfig + PhaseDefinition 新增 workspace 字段 (AC: #1)
  - [x] 1.1 `src/ato/config.py::PhaseConfig` 新增 `workspace: Literal["main", "worktree"] | None = None`
  - [x] 1.2 `src/ato/config.py::PhaseDefinition` 新增 `workspace: str` 字段
  - [x] 1.3 `build_phase_definitions()` 传播 `phase.workspace` 到 `PhaseDefinition`
  - [x] 1.4 更新 `tests/unit/test_config.py`：新字段解析、默认值断言

- [x] Task 2: 配置模板标注真实的 workspace 划分 (AC: #2)
  - [x] 2.1 `ato.yaml.example` 为每个 phase 增加 `workspace`
  - [x] 2.2 纠正阶段归属：`dev_ready`、`merging`、`regression` 必须标为 `main`
  - [x] 2.3 更新 `tests/integration/test_config_workflow.py`、`tests/unit/test_cli_plan.py`：阶段顺序与 workspace 展示基线

- [x] Task 3: Recovery / restart 路径支持 workspace-aware cwd (AC: #3, #4)
  - [x] 3.1 `src/ato/recovery.py::_resolve_phase_config_static()` 返回值增加 `workspace`
  - [x] 3.2 `src/ato/recovery.py::_build_dispatch_options()` 根据 workspace 决定 cwd：main → project_root，worktree → worktree_path
  - [x] 3.3 `src/ato/recovery.py::_dispatch_structured_job()` 对 main phase 不要求 worktree；对 worktree phase 继续要求
  - [x] 3.4 `src/ato/core.py::_dispatch_batch_restart()` 复用相同 workspace-aware options
  - [x] 3.5 更新 `tests/unit/test_recovery.py`、`tests/unit/test_core.py`

- [x] Task 4: validating 的正常 / recovery dispatch 改为 main-path，review / qa 仍走 worktree (AC: #3, #4, #6)
  - [x] 4.1 `src/ato/convergent_loop.py` 中将 validating 的路径解析从”总是 worktree”改为 workspace-aware；不要影响 reviewing / qa_testing 的 worktree 语义
  - [x] 4.2 `src/ato/recovery.py::_dispatch_convergent_loop()` 对 validating 不再因 `worktree_path is None` 直接失败
  - [x] 4.3 更新 `_CONVERGENT_LOOP_PROMPTS` / 相关 prompt builder，使 validating 使用 `project_root`，reviewing / qa_testing 继续使用 `worktree_path`
  - [x] 4.4 更新 `tests/unit/test_convergent_loop.py`、`tests/unit/test_recovery.py`

- [x] Task 5: Worktree 创建时机调整为 `start_dev → developing` (AC: #5)
  - [x] 5.1 在 TransitionQueue post-commit hook 或真正的 phase-entry dispatch 路径中，当 story 首次进入 `developing` 且 `worktree_path is None` 时调用 `WorktreeManager.create()`
  - [x] 5.2 recovery / restart 路径下，如果 phase 已是 worktree phase 且 `worktree_path is None`，先尝试 `WorktreeManager.create()` 再 dispatch
  - [x] 5.3 确保幂等性：已有 worktree 不重复创建（`WorktreeManager.create()` 已有幂等逻辑）
  - [x] 5.4 更新 `tests/unit/test_worktree_mgr.py`、`tests/unit/test_transition_queue.py`

- [x] Task 6: 新增 workspace 覆盖测试 (AC: #7)
  - [x] 6.1 `tests/unit/test_config.py`：`test_phase_config_workspace_omitted_is_none`
  - [x] 6.2 `tests/unit/test_config.py`：`test_phase_config_workspace_main_parsed`
  - [x] 6.3 `tests/unit/test_recovery.py`：`test_main_workspace_dispatch_without_worktree`
  - [x] 6.4 `tests/unit/test_convergent_loop.py`：`test_validating_uses_project_root`
  - [x] 6.5 `tests/unit/test_transition_queue.py`：`test_start_dev_creates_worktree_once`
  - [x] 6.6 `tests/unit/test_recovery.py` 或 `tests/integration/test_crash_recovery.py`：regression main-path recovery 保持 project_root

## Dev Notes

### 关键实现判断

- **workspace 字段可选（None），由 `_resolve_workspace()` 按 phase 名推断。** 已知 main phase（planning/creating/designing/validating/dev_ready/merging/regression）→ "main"，其余 → "worktree"。显式值覆盖推断。旧项目不写 workspace 时每个 phase 自动获得正确语义。
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
- 2026-03-29: 实现完成 — workspace 字段、workspace-aware cwd 分流、validating main-path 化、worktree 创建时机调整为 developing
- 2026-03-29: Code review patch #1 — 修复 3 个 finding：(1) workspace: worktree 缺 worktree 时不再静默回退，改为尝试创建 + dispatch_failed；(2) PRE_WORKTREE_PHASES 硬编码移除，改用 workspace config 驱动 limiter；(3) 测试断言改为精确匹配 project_root + 新增 fixing 丢 worktree 测试
- 2026-03-29: Code review patch #2 — 新增 TestBatchRestartWorkspaceBranches（3 个测试）直接覆盖 _dispatch_batch_restart 的 workspace 分支
- 2026-03-29: Code review patch #3 — workspace 改为 Optional + 按名推断：PhaseConfig.workspace 默认 None，build_phase_definitions 通过 _resolve_workspace() 和 _KNOWN_MAIN_PHASES 映射表按 phase 名推断（已知 main phase → "main"，其余 → "worktree"，显式值优先）。修复 story AC1/Task1/Dev Notes 使规格与代码一致。1663 全套测试通过零回归

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6 (1M context)

### Debug Log References

### Completion Notes List

1. PhaseConfig 新增 `workspace: Literal["main", "worktree"] | None = None` 字段，PhaseDefinition 新增 `workspace: str` 字段，`build_phase_definitions()` 通过 `_resolve_workspace()` 将省略值解析后传播
2. ato.yaml.example 为所有 12 个 phase 标注了 workspace：planning/creating/designing/validating/dev_ready/merging/regression → main，developing/reviewing/fixing/qa_testing/uat → worktree
3. RecoveryEngine._resolve_phase_config_static() 返回值包含 workspace 字段
4. _build_dispatch_options() 根据 workspace 决定 cwd：main → project_root，worktree → worktree_path
5. _dispatch_convergent_loop() 对 workspace: main 阶段不因 worktree_path is None 而 fail，使用 effective_path 分流
6. core.py::_dispatch_batch_restart() 使用相同的 workspace-aware cwd 逻辑
7. convergent_loop.py::_resolve_worktree_path() 新增 allow_project_root 参数，支持 workspace: main 回退到 project_root
8. validating prompt 模板更新为不再引用 "worktree"
9. TransitionQueue 新增 _on_enter_developing() post-commit hook：story 首次进入 developing 且 worktree_path=None 时创建 worktree
10. recovery.py 新增 _try_create_worktree()：recovery 路径下 worktree phase 缺失 worktree 时尝试创建
11. 新增 workspace 相关测试全部通过
12. Code review patch: workspace: worktree 缺 worktree 不再静默回退 project_root，改为 _try_create_worktree → dispatch_failed
13. Code review patch: PRE_WORKTREE_PHASES 硬编码常量移除，limiter 改由 phase_cfg["workspace"] == "main" 驱动
14. Code review patch: 测试断言由"非空/不等于"改为 derive_project_root() 精确匹配；新增 fixing 丢 worktree 的 recovery 路径测试
15. 新增 TestBatchRestartWorkspaceBranches（3 个测试）直接覆盖 _dispatch_batch_restart 的 workspace 分支
16. workspace 改为 Optional + 按名推断架构：PhaseConfig.workspace = None（省略），build_phase_definitions 通过 _resolve_workspace() 按 _KNOWN_MAIN_PHASES 映射推断；显式值覆盖推断；PhaseDefinition.workspace 始终为 "main" 或 "worktree"
17. 旧 YAML 省略 workspace 时：planning/creating/designing/validating/dev_ready/merging/regression → main，reviewing/fixing/developing/qa_testing/uat → worktree（精确复现旧行为）
18. story AC1/Task1/Dev Notes 修正为与实现一致

### File List

- src/ato/config.py — PhaseConfig.workspace 字段、PhaseDefinition.workspace 字段、build_phase_definitions() 传播
- src/ato/recovery.py — _resolve_phase_config_static() workspace、_build_dispatch_options() workspace-aware cwd、_dispatch_convergent_loop() effective_path、_try_create_worktree()、_CONVERGENT_LOOP_PROMPTS validating 模板
- src/ato/core.py — _dispatch_batch_restart() workspace-aware cwd
- src/ato/convergent_loop.py — _resolve_worktree_path() allow_project_root 参数
- src/ato/transition_queue.py — _on_enter_developing() worktree 创建 hook
- ato.yaml.example — 所有 phase 增加 workspace 标注
- tests/unit/test_config.py — TestPhaseConfigWorkspace（5 个测试）
- tests/unit/test_recovery.py — TestWorkspaceAwareDispatch（4 个测试）
- tests/unit/test_convergent_loop.py — TestResolveWorktreePathWorkspace（4 个测试）
- tests/unit/test_transition_queue.py — TestWorktreeCreationOnDeveloping（2 个测试）
