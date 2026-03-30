---
title: 'Generic Git Failure Policy Engine'
slug: 'generic-git-failure-policy-engine'
created: '2026-03-30'
status: 'ready-for-dev'
stepsCompleted: [1, 2, 3, 4]
tech_stack:
  [
    'python>=3.11',
    'asyncio',
    'aiosqlite',
    'pydantic>=2.0',
    'structlog',
    'typer',
    'git',
    'codex-cli',
    'claude-cli',
  ]
files_to_modify:
  [
    'src/ato/git_failure_policy.py',
    'src/ato/merge_queue.py',
    'src/ato/transition_queue.py',
    'src/ato/core.py',
    'src/ato/models/schemas.py',
    'src/ato/approval_helpers.py',
    'tests/unit/test_git_failure_policy.py',
    'tests/unit/test_merge_queue.py',
    'tests/unit/test_transition_queue.py',
    'tests/unit/test_core.py',
  ]
code_patterns:
  [
    'WorktreeManager 统一封装 deterministic git 子进程，失败以 (success, stderr) 或 WorktreeError 暴露',
    'MergeQueue 目前只在 merge path 做局部 git failure 判断：CONFLICT -> rebase_conflict approval，其它失败直接 mark failed',
    'TransitionQueue._on_enter_dev_ready() 是 spec_batch git failure 入口，失败走 precommit_failure(scope=spec_batch)',
    'approval 创建统一走 create_approval(payload_dict=...)，实际消费统一在 Orchestrator._handle_approval_decision()',
    '后台 LLM 任务的稳定派发模式是 adapter + SubprocessManager.dispatch_with_retry() + workspace-aware cwd/limiter',
  ]
test_patterns:
  [
    'pytest + pytest-asyncio (asyncio_mode=auto)',
    '大量使用 AsyncMock / patch 隔离 git subprocess、WorktreeManager、SubprocessManager',
    'contract tests 关注 approval type / payload / options / recommended_action 是否稳定',
    'git helper 测试同时覆盖返回 (False, stderr) 与 WorktreeError 异常路径',
  ]
---

# Tech-Spec: Generic Git Failure Policy Engine

**Created:** 2026-03-30

## Overview

### Problem Statement

ATO 当前对 git 失败的处理是“多入口、分散判断”：

- 只读/探测类 git 操作承担状态判定与安全门职责，必须保持 deterministic
- merge 路径中的 git 失败主要散落在 `MergeQueue` 与 `WorktreeManager`
- batch spec commit 的 git 失败又单独散落在 `TransitionQueue`
- 当前代码没有一个统一、机器可消费的 policy 层来判断：
  - 失败属于哪一类
  - 是否允许自动重试
  - 是否允许派发 LLM 修复
  - 是否必须 fail-closed / freeze / approval / escalate

需要补上一层通用 git failure policy engine，把分散的 git 失败归一化为稳定决策，同时保持主干 merge / revert / probe 语义不漂移。

### Solution

在现有 deterministic git 基础设施之上增加一个通用 policy engine：

- git probe、merge、revert、状态验证继续由规则代码和 git 子进程负责
- engine 负责把各入口的 git 失败归一化为 machine-readable policy decision
- 对满足“repairable + workspace-safe”的失败，系统派发受控 LLM 修复，再由 deterministic 验证收口
- 对 non-repairable 或 workspace-unsafe 的失败，系统继续走 retry、freeze、approval 或 escalate

### Scope

**In Scope:**
- 定义通用 git failure 分类模型与 policy decision 模型
- 盘点并接入现有 git failure 入口：
  - merge rebase / merge path
  - worktree lifecycle / helper path
  - batch spec commit path
- 定义哪些失败是 `repairable`、哪些是 `non-repairable`
- 定义哪些失败虽然理论上可修，但因 workspace 不安全而禁止自动 LLM 修复
- 为首批允许自动修复的入口接入 LLM-assisted recovery
- 规定自动修复后的验证闭环：重跑 git / hook / 测试，失败再回退到现有 approval 路径
- 保持现有 approval type、merge queue、worktree 生命周期与 regression 恢复合同不漂移

**Out of Scope:**
- 改写只读/探测类 git 操作，让 LLM 参与事实判定
- 让 LLM 直接执行主仓库 `merge_to_main()`、`revert_merge_range()` 这类控制面写操作
- 引入 remote `push`、`pull`、认证、远端同步策略
- 修改 UAT / regression 在状态机中的先后顺序

## Context for Development

### Codebase Patterns

- `WorktreeManager` 是 deterministic git 边界：统一封装 `worktree add/remove`、`branch -d`、`fetch/rebase`、`checkout main`、`merge --ff-only`、`revert`、`git add/commit`，并通过 `_run_git()` 复用超时和清理协议。
- `MergeQueue._execute_merge()` 当前只做局部分类：
  - `stderr` 含 `CONFLICT` → `_handle_rebase_conflict()`
  - 其它 rebase/merge 失败 → 直接 `mark_merge_failed_and_release_lock()`
- `TransitionQueue._on_enter_dev_ready()` 是第二条 git failure 入口：`batch_spec_commit()` 失败或抛异常时创建 `precommit_failure(scope=spec_batch)` approval。
- `Orchestrator._handle_approval_decision()` 统一消费异常审批；`precommit_failure` 还会按 payload `scope` 区分 merge path 与 `spec_batch` 路径。
- 仓库已经有稳定的后台 LLM 派发模式：
  - `_create_adapter(cli_tool)`
  - `SubprocessManager.dispatch_with_retry()`
  - workspace-aware `cwd`
  - main-path limiter / worktree-path dispatch
- 现有实现已经明确：只读/探测失败应 fail-closed，例如 regression 前后 `git status --porcelain` 失败不会放行。

### Files to Reference

| File | Purpose |
| ---- | ------- |
| `src/ato/worktree_mgr.py` | deterministic git helper 边界与失败形态来源 |
| `src/ato/merge_queue.py` | merge path git failure 入口、conflict/precommit hook、regression fail-closed |
| `src/ato/transition_queue.py` | `spec_batch_commit` 失败入口与 `precommit_failure(scope=spec_batch)` 创建 |
| `src/ato/core.py` | approval 统一消费、`spec_batch` 与 merge path 分流 |
| `src/ato/recovery.py` | adapter 创建、phase config、workspace-aware LLM dispatch 模式 |
| `src/ato/subprocess_mgr.py` | 后台 LLM task 生命周期、task 复用、自动重试 |
| `src/ato/models/schemas.py` | approval type/options/recommended_action 以及 WorktreeError 合同 |
| `src/ato/approval_helpers.py` | approval 摘要、异常上下文、payload/options 契约 |
| `_bmad-output/project-context.md` | 项目级实现约束：subprocess、SQLite、Pydantic、测试、日志 |
| `tests/unit/test_worktree_mgr.py` | git helper 失败/超时/cleanup/merge/revert/commit 合同测试 |
| `tests/unit/test_merge_queue.py` | rebase_conflict / precommit_failure / regression fail-closed 测试 |
| `tests/unit/test_transition_queue.py` | spec_batch_commit 失败 approval 测试 |
| `tests/unit/test_core.py` | `precommit_failure(scope=spec_batch)` 审批消费语义 |

### Technical Decisions

- 通用 engine 应该集中成单独模块，而不是继续在 `merge_queue.py`、`transition_queue.py`、`core.py` 散落 `if/else`。
- policy engine 的输出必须是 machine-readable decision，而不是写给人的 prose。
- failure 分类至少需要同时考虑：
  - operation 类型
  - workspace 位置（`main` / `worktree`）
  - repairability
  - automation safety
  - fallback action
- `repairable` 的定义必须是“LLM 改文件后有机会让下一次 deterministic 验证通过”，而不是泛指所有 git 非零退出。
- `non-repairable` 的定义必须是“即使改文件也无法让系统安全前进”，例如仓库不存在、worktree 缺失、`pre_merge_head` 缺失、timeout、workspace 状态不可信。
- 自动 LLM 修复不只看 failure type，还要看 workspace safety：当前调查结论是自动修复只能安全发生在 story `worktree` 内，不能在 `main` workspace 上自由编辑。
- 因此 `precommit_failure(scope=spec_batch)` 即使技术上可能“可修”，在首版 engine 中也更适合判定为 `workspace_unsafe_for_auto_fix`，继续走现有人工路径。
- 自动修复是否成功，必须由 deterministic 验证收口，而不是由 LLM 自报成功。
- 首版不应发明新的 approval type；优先复用现有 `rebase_conflict` / `precommit_failure`，必要时只扩展 payload 中的 reason/action 元数据。
- 自动 LLM 修复必须作为显式持久化后台任务实现，而不是隐藏在临时 helper 调用中。首版仅覆盖 story `worktree` 中的 `rebase conflict`，并复用现有 `TaskRecord`、adapter、`SubprocessManager.dispatch_with_retry()` 与 crash recovery 语义。
- 自动修复任务至少需要持久化 `story_id`、`workspace="worktree"`、`worktree_path`、`reason_code`、`attempt_no` 等上下文，用于恢复、去重和 operator 可见性，而不是交给 LLM 自行推断。
- 自动修复任务运行期间，merge queue 必须保持对该 story 的串行 ownership：`current_merge_story_id` 不释放，merge entry 不得提前移出 merge 流程。只有 deterministic 验证收口后，才能进入成功推进或 fallback。
- 自动修复任务本身的失败，包括 dispatch 失败、adapter 失败、structured output 非法、进程崩溃、恢复后达到重试上限，都统一视为 `repair_attempt_failed`，而不是发明新的 approval type；调用方必须回退到现有 `abort_rebase()` + `rebase_conflict` approval 路径。
- policy engine 输出中的 `next_action` 应视为内部 `policy_action`。它是 orchestration 层的机器决策字段，不是 operator-facing decision vocabulary。
- `policy_action` 不得直接驱动 approval 按钮或 `recommended_action`；approval 的 `options`、`recommended_action` 与 `Orchestrator._handle_approval_decision()` 继续复用现有白名单语义，如 `manual_resolve`、`retry`、`skip`、`abandon`。
- 若需要将 policy 结果暴露给 CLI/TUI，`policy_action`、`reason_code`、`repairable`、`auto_fix_allowed` 只能作为只读上下文附加到 payload 中展示，不能改变现有 approval options 合同。

## Implementation Plan

### Tasks

- [ ] Task 1: 定义通用 git failure policy 数据模型与分类入口
  - File: `src/ato/models/schemas.py`
  - Action: 新增 git failure policy 相关的 machine-readable 模型与枚举/Literal，至少覆盖：
    - failure source / operation / workspace
    - `repairable`、`auto_fix_allowed`
    - `policy_action`
    - `reason_code`
    - `approval_type` / payload metadata
  - Notes:
    - 遵循项目规则，Pydantic model 统一放在 `models/schemas.py`
    - 不引入新的 approval type；policy 只生成决策和元数据
    - `policy_action` 是 orchestration 内部枚举，不直接作为 approval decision 对外暴露
  - File: `src/ato/git_failure_policy.py`
  - Action: 新建集中模块，实现纯函数式分类/决策 API
  - Notes:
    - 输入是 deterministic git 结果与上下文，不直接执行 git
    - 输出必须稳定、可测试，不依赖 LLM 返回 prose

- [ ] Task 2: 在 merge/worktree path 接入 policy engine，并对 worktree-safe repairable 失败执行自动修复
  - File: `src/ato/merge_queue.py`
  - Action: 将 `_execute_merge()` 与 `_handle_rebase_conflict()` 改为先走 policy engine，再决定：
    - 直接 fail-closed
    - 创建/复用 approval
    - 派发 LLM 自动修复
  - Notes:
    - 首版自动修复只允许发生在 story `worktree`
    - `stderr` 含 `CONFLICT` 的 rebase conflict 应优先走 auto-fix policy，而不是直接 abort + approval
    - 自动修复成功后必须调用 deterministic 验证，例如 `continue_rebase()`；失败则 `abort_rebase()` + approval fallback
    - 自动修复必须作为显式持久化后台任务派发，复用现有 `TaskRecord` + `dispatch_with_retry()` 模式，而不是在 helper 内做一次性 fire-and-forget 调用
    - 自动修复任务运行期间不得释放 `current_merge_story_id`；orchestrator 重启或 crash recovery 介入时必须恢复/收敛该任务，而不是重复派发第二个修复任务
    - 非冲突 rebase 失败、`merge_ff_failed`、`pre_merge_head_missing` 等必须通过 policy 明确标记为 non-repairable / fail-closed
  - File: `src/ato/core.py`
  - Action: 若 merge path 新增 policy payload 元数据，确保现有 approval 消费逻辑对新增字段保持兼容
  - Notes:
    - 不改变现有 `rebase_conflict` / `precommit_failure` 的 decision 集合

- [ ] Task 3: 在 spec_batch path 接入 policy engine，但保持 main workspace 不做自动修复
  - File: `src/ato/transition_queue.py`
  - Action: 将 `batch_spec_commit()` 失败或异常路径改为先经过 policy engine，再创建 `precommit_failure(scope=spec_batch)` approval
  - Notes:
    - 需要显式把 `workspace="main"` 传给 policy engine
    - 即使错误文本看起来“可修”，也要被 policy 标成 `workspace_unsafe_for_auto_fix`
    - approval payload 中补充 `reason_code`、`repairable`、`auto_fix_allowed`、`policy_action`
  - File: `src/ato/core.py`
  - Action: 确保 `_handle_spec_batch_precommit()` 对新增 payload 字段保持兼容，`retry/manual_fix/skip` 语义不漂移
  - Notes:
    - `retry` / `manual_fix` 重新创建 approval 时需要保留 policy 元数据，不能只回写最小 payload

- [ ] Task 4: 提供统一的自动修复派发与 operator-facing 上下文展示
  - File: `src/ato/git_failure_policy.py`
  - Action: 实现自动修复调度 helper，复用现有 adapter + `SubprocessManager.dispatch_with_retry()` 模式，并让纯分类与任务派发保持职责分离
  - Notes:
    - 分类 API 保持纯函数；任务派发 helper 负责创建/恢复显式 repair task，而不是把 LLM side effect 混进 classifier
    - 首版 repair task 仅覆盖 worktree 内的 `rebase conflict`，phase 命名应能与现有 recovery 逻辑稳定关联
    - 自动修复 prompt 必须限定修改范围、禁止 main workspace 编辑、要求只处理当前 failure
    - 自动修复结束后不能由 LLM 自报成功，必须回到调用方做 deterministic 验证
    - 不在该模块里直接执行控制面 git 写操作（`merge_to_main` / `revert`）
    - repair task dispatch 失败、adapter 失败、structured output 非法、任务 crash 或达到尝试上限，都必须统一回退到现有 `abort_rebase()` + `rebase_conflict` approval 路径
  - File: `src/ato/approval_helpers.py`
  - Action: 扩展异常审批上下文格式化，展示新增的 policy 元数据
  - Notes:
    - 至少允许展示 `reason_code`、`workspace`、`auto_fix_allowed`、`policy_action`
    - CLI/TUI 摘要与 options 现有合同保持不变

- [ ] Task 5: 补齐单元测试，锁定分类与 fallback 合同
  - File: `tests/unit/test_git_failure_policy.py`
  - Action: 新增 policy engine 纯分类测试
  - Notes:
    - 覆盖 repairable + worktree-safe
    - 覆盖 repairable but workspace-unsafe
    - 覆盖 non-repairable
    - 覆盖 unknown / timeout / missing-context
  - File: `tests/unit/test_merge_queue.py`
  - Action: 增加 merge path 集成测试
  - Notes:
    - rebase conflict auto-fix success
    - auto-fix failure → abort + `rebase_conflict` approval
    - non-conflict merge failure不触发 LLM
    - `git status` / pre-merge invariant failure 继续 fail-closed
  - File: `tests/unit/test_transition_queue.py`
  - Action: 增加 `spec_batch` classification 测试
  - Notes:
    - `precommit_failure(scope=spec_batch)` payload 应包含 policy 元数据
    - `retry` / `manual_fix` 重新生成 approval 时仍保留 policy 元数据
    - main workspace 不得触发 auto-fix dispatch
  - File: `tests/unit/test_core.py`
  - Action: 验证新增 payload 字段不会破坏 `precommit_failure(scope=spec_batch)` 审批消费
  - Notes:
    - `retry/manual_fix/skip` 语义必须保持不变
  - File: `tests/unit/test_recovery.py`
  - Action: 增加 auto-fix repair task 的恢复/去重测试
  - Notes:
    - orchestrator 重启或 crash recovery 后不得重复派发第二个 auto-fix 任务
    - 已存在 repair task 时必须基于持久化任务状态恢复或收敛

### Acceptance Criteria

- [ ] AC 1: Given 任一 git failure 入口提供了 operation、workspace、stderr/exception 等上下文，when 调用 policy engine，then 返回稳定的 machine-readable decision，至少包含 `reason_code`、`repairable`、`auto_fix_allowed`、`policy_action`。
- [ ] AC 2: Given merge path 中发生 `rebase conflict` 且 workspace 为 story worktree，when policy 判定为 `repairable + auto_fix_allowed`，then 系统创建显式持久化 auto-fix task，并在修复后仅以 deterministic `continue_rebase()` 验证成功作为继续 merge 流程的收口条件。
- [ ] AC 3: Given merge path 中发生 `rebase conflict` 且自动修复失败、验证失败或达到尝试上限，when merge worker 回退，then 系统执行 `abort_rebase()`，并创建现有 `rebase_conflict` approval，而不是让仓库停留在半完成 rebase 状态。
- [ ] AC 4: Given merge/worktree path 中出现非 repairable 失败，如 `worktree_missing`、`pre_merge_head_missing`、`checkout_main` 失败、`merge_ff_failed`、git timeout 或 workspace snapshot failure，when policy engine 分类，then 系统不得触发 LLM 修复，并保持当前 fail-closed / mark-failed / freeze 语义。
- [ ] AC 5: Given `batch_spec_commit()` 在 `main` workspace 上失败或抛异常，when `TransitionQueue` 走 policy engine，then 系统继续创建 `precommit_failure(scope=spec_batch)` approval，且 payload 补充 `reason_code`、`repairable`、`auto_fix_allowed=false`、`policy_action`。
- [ ] AC 6: Given operator 在 CLI/TUI 中查看 `rebase_conflict` 或 `precommit_failure` approval，when payload 携带 policy 元数据，then 摘要、图标、options 与现有合同保持一致，且额外上下文可显示 policy 分类结果。
- [ ] AC 7: Given regression/main-workspace 的只读探测路径，如 `git status --porcelain` 快照，when git 命令失败，then 系统继续 fail-closed，不通过 policy engine 把 probe 失败升级成 LLM 修复任务。
- [ ] AC 8: Given 新增的 policy engine 与入口集成测试，when 运行相关单元测试，then 分类结果、approval payload、fallback 路径与现有 decision 语义全部稳定通过。
- [ ] AC 9: Given merge path 中的 auto-fix task 已创建并持久化，when orchestrator 在任务执行期间重启或 crash recovery 介入，then 系统必须基于现有 task 记录恢复或收敛该任务，而不是重复派发第二个 auto-fix task，也不能丢失当前 merge ownership。
- [ ] AC 10: Given policy engine 产出 `policy_action`，when 系统创建或展示 `rebase_conflict` / `precommit_failure` approval，then `policy_action` 仅作为只读上下文存在，approval `options` 与 `recommended_action` 仍严格受现有 approval contract 白名单约束，不引入新的 operator decision 词汇。

## Additional Context

### Dependencies

- 无新增第三方依赖；复用现有：
  - `python>=3.11`
  - `asyncio`
  - `aiosqlite`
  - `pydantic>=2.0`
  - `structlog`
  - `git`
  - Codex / Claude adapter
  - `SubprocessManager.dispatch_with_retry()`
- 依赖现有 approval 合同：
  - `rebase_conflict`
  - `precommit_failure`
  - `precommit_failure(scope=spec_batch)`
- 依赖现有 merge/worktree helper 合同，不在本 story 中改写 `merge_to_main()`、`revert_merge_range()`、probe 语义

### Testing Strategy

- 以 unit tests 为主，新增 policy 分类测试与入口集成测试
- 复用现有 `tests/unit/test_merge_queue.py`、`tests/unit/test_transition_queue.py`、`tests/unit/test_worktree_mgr.py`、`tests/unit/test_core.py`
- 补充 `tests/unit/test_recovery.py`，锁定 auto-fix task 的 crash recovery / 去重语义
- 补充 CLI/TUI 展示相关测试，锁定新增 policy 元数据不会改变 approval options / recommended_action 合同
- 新测试矩阵至少区分：
  - repairable + worktree-safe
  - repairable but workspace-unsafe
  - non-repairable
  - probe failure fail-closed
  - approval fallback 不漂移
  - auto-fix task restart/recovery without duplicate dispatch
  - policy 元数据仅展示、不改变 operator decision vocabulary
- 建议执行：
  - `uv run pytest tests/unit/test_git_failure_policy.py -v`
  - `uv run pytest tests/unit/test_merge_queue.py tests/unit/test_transition_queue.py tests/unit/test_core.py -v`
  - `uv run pytest tests/unit/test_worktree_mgr.py -v`

### Notes

- 范围已按你的要求调整为“通用 git failure policy engine”。
- 当前调查没有发现现成的 policy/classifier 抽象；现状是多入口、分散判断。
- 首版的高风险点不是分类本身，而是误把 `main` workspace 的 failure 当成可自动修复；spec 已明确把这类情况保持为 approval/human path。
- 若自动修复实现没有严格的后验 deterministic 验证，就会把“LLM 自报修好”误当成状态真相；这是必须避免的设计错误。
- 未来可以扩展的方向：
  - 让 policy 尝试次数与 allowlist 配置化
  - 为更多 git 入口接入统一分类
  - 在 approval 面板中展示更细的 `reason_code` 解释
