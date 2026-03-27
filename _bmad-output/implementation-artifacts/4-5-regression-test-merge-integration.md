# Story 4.5: Regression 测试执行与 Merge Queue 集成

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a 操作者,
I want 系统在 merge 闭环中自动执行 regression 测试，失败时冻结 merge queue,
So that main 分支的质量不会因 merge 而退化。

## Acceptance Criteria (AC)

### AC1: Merge 完成后在主仓库 main 工作区执行 regression 测试

```gherkin
Given story 完成所有质量门控并进入 merge 准备阶段
When 获得操作者 merge 授权且 merge queue 完成顺序化 rebase + fast-forward merge
Then 在主仓库（repo root / main workspace，非 story worktree）执行项目配置的 regression_test_command
And 复用现有 tasks 合同记录 `phase="regression"`、`role="qa"`、`expected_artifact="regression_test"`
And regression 检测应以 `regression_task_id` / task 完成状态为锚点，不要求为本 story 额外发明 PID 依赖
```

### AC2: Merge + Regression 通过 → Done

```gherkin
Given merge queue entry 已完成 rebase + fast-forward merge 到 main
When regression 测试全部通过
Then merge_queue entry 状态更新为 "merged"
And story 状态机触发 `regression_pass` → `done`
And worktree 仅在 regression pass 闭环完成后清理（branch + metadata + 目录）
And 里程碑通知只在 story 状态真实持久化为 `done` 后，由 TransitionQueue 的 post-commit hook 触发
```

### AC3: Regression 失败 → 冻结 + 紧急通知 + Approval

```gherkin
Given regression 测试失败
When 系统检测到失败
Then 自动冻结 merge queue，阻止后续 merge（FR32, NFR10）
And 冻结原因记录为 "regression failed for {story_id}"
And 创建 approval（类型 regression_failure，risk_level=high）
And approval payload 包含失败的 test 输出摘要、story_id、选项列表
And approval 选项为 ["revert", "fix_forward", "pause"]
And approval helper 负责发出自包含的 URGENT 通知，避免 merge_queue 再额外发送重复 bell
```

### AC4: 操作者处理 regression 失败的三种决策

```gherkin
Given 操作者处理 regression_failure approval
When 选择 "revert"
Then 执行 git revert（使用 pre_merge_head 安全回滚）
And 仅在 revert 成功后 merge queue 解冻，恢复正常合并流程
And revert 成功后才允许清理对应 worktree

When 选择 "fix_forward"
Then 通过 `regression_fail` 状态机事件将 story 从 `regression` 退回 `fixing`
And 原 merge_queue entry / current lock 被清理，但原 worktree 保留为修复上下文
And merge queue 保持冻结，直到该 recovery story 再次 merge 并通过 regression

When 选择 "pause"
Then merge queue 保持冻结
And 不承诺通过同一条 approval 再次 `ato approve` 直接解冻
And 本 story 不隐式发明新的 unblock 命令或状态机捷径
```

### AC5: Rebase 冲突自动处理

```gherkin
Given worktree rebase 产生冲突
When 系统检测到冲突（FR52）
Then 创建 approval（类型 rebase_conflict），等待操作者决策
And 选项：manual_resolve / skip / abandon
And merge queue 不因 rebase 冲突而冻结（仅冻结于 regression 失败）
```

### AC6: Pre-commit Hook 失败自动修复（条件性合同）

```gherkin
Given merge 流程中存在会触发 pre-commit 的 commit-producing step
When 系统检测到 commit 失败（FR54）
Then 可基于项目配置的 lint / format / type-check 命令调度 agent 自动修复
And 修复后重新 commit
And 自动修复失败则 escalate 给操作者，创建 approval（类型 precommit_failure）
And 若当前 ff-only merge 路径并不产生该 commit，本 story 不得为了满足 FR54 伪造一条不可达执行路径
```

## Tasks / Subtasks

### ⚠️ 重要前提：Story 4-2 与 4-4 已经收敛的合同

Story 4-2 已完成 merge queue 核心基础设施，Story 4-4 已完成通知与 approval 展示合同。本 story 聚焦于 **端到端集成验证、语义补缝与测试补强**，不得重新定义这两条 story 已经收敛的边界。

以下模块已存在：

- `src/ato/merge_queue.py` — `MergeQueue` 类：`enqueue()`, `process_next()`, `_execute_merge()`, `_dispatch_regression_test()`, `_run_regression_test()`, `check_regression_completion()`, `_handle_regression_failure()`, `_handle_rebase_conflict()`, `unfreeze()`
- `src/ato/worktree_mgr.py` — `WorktreeManager` 类：`rebase_onto_main()`, `merge_to_main()`, `revert_merge_range()`, `cleanup()`
- `src/ato/models/db.py` — `merge_queue` 表、`merge_queue_state` 表、`regression_task_id` / `pre_merge_head` 等 CRUD
- `src/ato/state_machine.py` — `merging → regression → done/fixing` 转换链
- `src/ato/approval_helpers.py` — approval 创建 + 自包含 bell 通知
- `src/ato/transition_queue.py` — story `done` 的 post-commit 里程碑通知钩子

**本 story 的任务是验证这些组件的端到端协作，并补全尚未集成的环节。**

- [ ] Task 1: 审计与补全 Orchestrator 事件循环集成 (AC: #1, #2, #3)
  - [ ] 1.1 审计 `src/ato/core.py` 中 merge queue 的轮询集成：
    - 确认 `merge_queue.process_next()` 在主事件循环中被调用
    - 确认 `merge_queue.check_regression_completion()` 在主事件循环中被调用
    - 确认 `merge_queue.recover_stale_lock()` 在启动恢复中被调用
  - [ ] 1.2 审计 `src/ato/core.py` 与 `src/ato/transition_queue.py` 的职责边界：
    - `merge_authorization` / `regression_failure` / `rebase_conflict` approval 决策继续在 `Orchestrator._handle_approval_decision()` 中消费
    - `regression_pass` / `regression_fail` 继续作为 TransitionQueue 事件处理，不把 approval 路由塞进 `transition_queue.py`
  - [ ] 1.3 补全 regression pass 后的完整收尾流程：
    - 状态机 `regression_pass` → `done`
    - `complete_merge(db, story_id, success=True)` 更新 merge_queue entry
    - `worktree_mgr.cleanup(story_id)` 在 regression pass 之后清理 worktree
    - milestone 通知继续复用 `TransitionQueue._on_story_done()` 的单一 post-commit hook，不在 `merge_queue.py` 额外发 bell
  - [ ] 1.4 补全 regression fail 后的完整处理流程：
    - `_handle_regression_failure()` 冻结 + 创建 approval
    - 失败通知继续走 `create_approval()` / `approval_helpers.py` 的自包含消息合同，不重复直接调用 `send_user_notification("urgent", ...)`

- [ ] Task 2: Operator 决策处理流程（沿用现有 approval consumer） (AC: #4)
  - [ ] 2.1 在 `src/ato/core.py::_handle_approval_decision()` 中补齐 `regression_failure` 分支：
    - `"revert"` → 调用 `worktree_mgr.revert_merge_range(pre_merge_head)`；仅在成功后 `merge_queue.unfreeze("revert completed")` + cleanup worktree
    - `"fix_forward"` → 提交 `regression_fail` → `fixing`；移除旧 merge_queue entry / current lock；保留 queue frozen 与原 worktree
    - `"pause"` → 仅记录并保持 queue frozen；不伪造自动 unblock 路径
  - [ ] 2.2 `rebase_conflict` / `precommit_failure` 的决策路由继续留在同一个 approval consumer：
    - 不新增 `MergeQueue.handle_regression_decision()`
    - 不把 approval 决策消费塞进 `transition_queue.py`
  - [ ] 2.3 校验解冻后的恢复语义：
    - 明确由当前 poll cycle（必要时配合 nudge）恢复后续 merge
    - 不要求 `MergeQueue.unfreeze()` 自己直接调用 `process_next()`

- [ ] Task 3: Rebase 冲突与 Pre-commit 合同收敛 (AC: #5, #6)
  - [ ] 3.1 审计 `_handle_rebase_conflict()` 实现完整性：
    - 确认创建 `rebase_conflict` approval 包含冲突文件列表
    - 确认操作者决策路由正确（manual_resolve / skip / abandon）
  - [ ] 3.2 收敛 pre-commit hook 失败的真实范围（FR54）：
    - 当前 ff-only merge 路径不会天然产出新的 commit，不要把不可达路径当成本 story 的主交付
    - 至少保持 `_handle_precommit_failure()` approval 合同、选项与测试不漂移
    - 如果引入 commit-producing step，必须复用现有 adapter / settings / SubprocessManager 约定，而不是手写 CLI 调用
  - [ ] 3.3 确认 rebase 冲突不触发 merge queue 冻结（仅 regression 失败冻结）

- [ ] Task 4: 测试覆盖（优先扩展现有测试文件） (AC: #1-#6)
  - [ ] 4.1 追加 `tests/unit/test_merge_queue.py`：
    - `test_happy_path_merge_regression_pass_to_done` — merge → regression pass → merged → cleanup
    - `test_regression_failure_freezes_queue_and_creates_approval` — regression 失败冻结 queue + 创建 high-risk approval
    - `test_check_regression_completion_unfreezes_recovery_story` — recovery story 二次通过后才解冻
    - `test_rebase_conflict_does_not_freeze_queue` — rebase 冲突不冻结 merge queue
  - [ ] 4.2 追加 `tests/unit/test_core.py`：
    - `test_regression_failure_revert_only_unfreezes_after_successful_revert`
    - `test_regression_failure_fix_forward_submits_regression_fail_and_keeps_queue_frozen`
    - `test_regression_failure_pause_keeps_queue_frozen_without_fake_unblock_path`
  - [ ] 4.3 追加 `tests/integration/test_notification_flow.py` / `tests/unit/test_worktree_mgr.py`：
    - milestone 通知仍只来自 post-commit hook
    - regression_failure approval 仍触发 URGENT bell
    - `revert_merge_range()` / rebase helper 的 git 合同保持正确
  - [ ] 4.4 仅当现有测试承载不了真实跨模块路径时，才新增新的 integration 文件；默认先扩展 `test_merge_queue.py`、`test_core.py`、`test_notification_flow.py`

- [ ] Task 5: 崩溃恢复场景验证 (AC: #1, #3)
  - [ ] 5.1 审计 `merge_queue.recover_stale_lock()` 覆盖以下场景：
    - 崩溃发生在 rebase / merging 期间 → 移除 entry + 释放锁，等待 merge_authorization 重建
    - 崩溃发生在 regression 期间 → 依据 `regression_task_id` + task 状态 / exit_code 收敛
    - regression 结果未知时 → 冻结 queue + 创建 `regression_failure` approval
  - [ ] 5.2 优先扩展现有 `tests/unit/test_merge_queue.py` 与 `tests/unit/test_recovery.py`：
    - `test_crash_during_regression_recovers`
    - `test_crash_during_merge_releases_lock_without_duplicate_path`
    - `test_stale_lock_cleanup`

## Dev Notes

### 核心实现原则

1. **不重复造轮子** — Story 4-2 已实现 merge queue 核心（MergeQueue 类、DB 表、状态机转换），Story 4-4 已实现通知与 approval 输出合同。本 story 的重点是 **审计现有实现 → 发现集成缺口 → 补全端到端流程 → 验证测试覆盖**。

2. **Approval 决策消费继续留在 Orchestrator** — 本 story 不应新增 `handle_regression_decision()` 之类把 approval 语义拆到 `MergeQueue` 的新入口，也不应把 approval routing 移进 `transition_queue.py`。

3. **NFR10 是硬约束** — merge queue 冻结后，系统保证不会在 broken main 上继续 merge。`fix_forward` 和 `pause` 都不能提前解冻 queue。

4. **Regression 仍是 post-merge main safety gate** — Story 4-2 已明确 regression 在 main 上运行（非 worktree）。如果后续 planning 想改为 true pre-merge regression，必须显式重规划 4.2/4.5 合同链。

### 已存在的关键文件（禁止重建）

| 文件 | 关键内容 | Story 来源 |
|------|---------|-----------|
| `src/ato/merge_queue.py` | MergeQueue 类完整实现 | 4-2 |
| `src/ato/worktree_mgr.py` | WorktreeManager 含 rebase/merge/revert | 2b-4 |
| `src/ato/models/db.py` | merge_queue + merge_queue_state 表 | 4-2 |
| `src/ato/state_machine.py` | merging → regression → done/fixing | 2a-1 / 4-2 |
| `src/ato/approval_helpers.py` | regression_failure approval 创建 + bell 通知 | 4-1 / 4-4 |
| `src/ato/transition_queue.py` | story `done` post-commit milestone hook | 4-4 |

### 需要触碰的文件（预期修改）

| 文件 | 修改内容 |
|------|---------|
| `src/ato/core.py` | 审计/补全 merge queue 驱动与 `regression_failure` 决策分支 |
| `src/ato/merge_queue.py` | 审计 regression pass/fail / stale-lock 收敛逻辑，保持当前 dispatch 合同一致 |
| `src/ato/transition_queue.py` | 原则上不新增 approval routing；仅在 post-commit / `regression_fail` 覆盖缺口时调整 |
| `tests/unit/test_merge_queue.py` | 追加 merge/regression/recovery 主覆盖 |
| `tests/unit/test_core.py` | 追加 approval 决策分支覆盖 |
| `tests/unit/test_worktree_mgr.py` | 追加 revert / rebase git helper 覆盖 |
| `tests/integration/test_notification_flow.py` | 追加通知与 post-commit 一致性覆盖 |

### 技术约束

- **异步模式** — 所有 merge 操作在 asyncio 事件循环中执行，遵循三阶段 subprocess 清理协议
- **SQLite 写事务** — 写事务中不 await 外部 IO；先读数据 → 处理逻辑 → 单次写入 + commit
- **参数化查询** — 禁止手动拼接 SQL
- **Regression 运行位置** — regression 测试在主仓库 main 工作区执行，不在 story worktree 执行
- **通知单一出口** — story 完成里程碑通知只来自 `TransitionQueue._on_story_done()`；approval bell 通知只来自 `create_approval()` / `approval_helpers.py`
- **fix_forward 必须经过状态机** — 通过 `regression_fail` 回到 `fixing`；禁止直接写 DB 改 phase
- **测试布局** — 当前仓库已存在 `test_merge_queue.py`、`test_core.py`、`test_worktree_mgr.py`、`test_notification_flow.py`，优先扩展，不先拆新文件

### Story 4-2 / 4-4 的经验教训

- MergeQueue 不得阻塞 poll loop；完整 merge / regression 流程在后台 worker 中执行
- approval 通知消息自包含短 ID 与快捷命令，regression_failure 的 URGENT 通知格式已定义
- milestone 通知已经收敛为 post-commit 单一钩子，不能在 `merge_queue.py` 再次提前发送
- CLI 错误输出统一为"发生了什么 + 你的选项"格式（`_format_cli_error()`）

### Project Structure Notes

- 所有代码在 `src/ato/` 包内
- 测试按 `tests/unit/` 和 `tests/integration/` 分层
- regression / approval / worktree / notification 已各有专门测试文件，避免重复创建平行测试矩阵
- 使用 `uv run pytest` 运行测试

### References

- [Source: _bmad-output/planning-artifacts/prd.md — FR22, FR31, FR32, FR52, FR54, NFR10]
- [Source: _bmad-output/planning-artifacts/architecture.md — Decision 2 TUI↔Orchestrator 通信, Asyncio Subprocess 三阶段清理, SQLite 连接策略]
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md — Flow 5 异常处理, ExceptionApprovalPanel, Notification Patterns]
- [Source: _bmad-output/planning-artifacts/epics.md — Epic 4 Story 4.5]
- [Source: _bmad-output/implementation-artifacts/4-2-merge-queue-regression-safety.md — 已验证的 merge queue / regression 合同]
- [Source: _bmad-output/implementation-artifacts/4-4-notification-cli-quality.md — 通知体系与 post-commit hook 合同]

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

### Change Log

- 2026-03-27: create-story 创建 — 基于 Epic 4 / PRD / Architecture / UX spec / Story 4.2 + 4.4 上下文生成 4.5 初稿
- 2026-03-27: validate-create-story 修订 —— 对齐 4.2 已验证的 main-branch regression contract、4.4 单一 post-commit milestone hook、approval decision routing in `core.py`、`fix_forward`/`pause` 的冻结语义，以及现有测试文件布局

### File List
