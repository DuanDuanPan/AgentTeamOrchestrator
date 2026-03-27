# Story 验证报告：4.5 Regression 测试执行与 Merge Queue 集成

验证时间：2026-03-27 10:32:32 CST
Story 文件：`_bmad-output/implementation-artifacts/4-5-regression-test-merge-integration.md`
验证模式：`validate-create-story`
结果：PASS（已应用修正）

## 摘要

该 story 的主题与 Epic 4、FR22/FR31/FR32/FR52/FR54 以及现有 merge queue 代码基线一致，但原稿里有 5 个会直接把实现带偏的合同问题，已在 story 文件中修正：

1. 它把 regression 又写回了 story worktree / `subprocess_mgr` / PID 路径，和 Story 4.2 已验证的“main 分支 post-merge safety gate”合同冲突。
2. 它要求在 `merge_queue.py` 里直接发送 milestone / urgent 通知，会和 Story 4.4 已收敛的 post-commit hook 与 approval helper 通知模型打架，最容易制造 double bell 与 false positive。
3. 它试图新增 `MergeQueue.handle_regression_decision()` 并把 approval routing 塞进 `transition_queue.py`，但当前架构里 approval 决策明确由 `Orchestrator._handle_approval_decision()` 消费。
4. 它把 `fix_forward` 写成“立即解冻 queue”，还承诺 `pause` 之后可以再 `ato approve` 解冻，这既违反 NFR10，也和当前 approval 生命周期不相容。
5. 它把 FR54 的 pre-commit 自动修复与一整套全新测试文件当成主交付，但当前 merge 路径是 ff-only，且仓库已经有 `test_merge_queue.py` / `test_core.py` / `test_notification_flow.py` / `test_worktree_mgr.py` 等现成落点。

## 已核查证据

- 规划与 story 工件：
  - `_bmad-output/planning-artifacts/epics.md`
  - `_bmad-output/planning-artifacts/prd.md`
  - `_bmad-output/planning-artifacts/architecture.md`
  - `_bmad-output/planning-artifacts/ux-design-specification.md`
  - `_bmad-output/project-context.md`
  - `_bmad-output/implementation-artifacts/4-2-merge-queue-regression-safety.md`
  - `_bmad-output/implementation-artifacts/4-4-notification-cli-quality.md`
- 当前代码基线：
  - `src/ato/core.py`
  - `src/ato/merge_queue.py`
  - `src/ato/transition_queue.py`
  - `src/ato/worktree_mgr.py`
  - `src/ato/approval_helpers.py`
  - `src/ato/nudge.py`
  - `src/ato/models/db.py`
  - `src/ato/models/schemas.py`
- 当前测试布局：
  - `tests/unit/test_merge_queue.py`
  - `tests/unit/test_core.py`
  - `tests/unit/test_worktree_mgr.py`
  - `tests/integration/test_notification_flow.py`

## 发现的关键问题

### 1. Regression 执行位置与 4.2 已验证合同冲突

原稿 AC1 写成：

- 在 worktree 中执行 regression
- 通过 `subprocess_mgr` 派发
- task 必须以 PID 为恢复锚点

但 Story 4.2 的已验证合同与当前代码基线是：

- merge 完成后 regression 在主仓库 main 工作区执行
- 当前恢复锚点是 `merge_queue.regression_task_id` + tasks 表中的完成状态 / exit_code
- `_run_regression_test()` 现在是专用 subprocess 路径，不应被 4.5 在没有显式重规划的情况下强行改写

如果不修，最常见的后果就是：开发者把 regression 又挪回 pre-merge 语义，实际测到的不是 main 上的已合入代码；同时 recovery 又被错误地写成依赖 PID，和现有 `regression_task_id` 合同漂移。

已应用修正：

- AC1 改成“主仓库 main workspace，非 story worktree”
- 恢复锚点收敛到 `regression_task_id` / task 状态，而不是强制 PID
- Dev Notes 显式写明：若后续 planning 想改成 true pre-merge regression，必须显式重规划 4.2/4.5

### 2. 通知出口被写错层，会造成重复 bell 和假阳性

原稿把下面两件事都写进了 `merge_queue.py`：

- regression pass 后直接发送 milestone 通知
- regression fail 后直接发送 urgent 通知

但 Story 4.4 已经明确收敛：

- story `done` 的 milestone 通知只来自 `TransitionQueue` 的单一 post-commit hook
- approval 的 bell 通知只来自 `create_approval()` / `approval_helpers.py`

如果 dev 按原稿实现，最直接的问题就是：

- `regression_pass` 时 merge queue 先响一次，commit 成功后 post-commit hook 再响一次
- failure 路径可能既在 `_handle_regression_failure()` 里直接响一次，又在创建 approval 时响第二次

已应用修正：

- AC2 / Task 1.3 改为“milestone 继续复用 `TransitionQueue._on_story_done()`”
- AC3 / Task 1.4 改为“urgent 通知继续走 approval helper，自带短 ID + 快捷命令”

### 3. Approval 决策被放到了错误层

原稿要新增：

- `MergeQueue.handle_regression_decision()`
- 在 `transition_queue.py` 中处理 `regression_failure` approval 决策

这和当前仓库的边界相反：

- `transition_queue.py` 只处理状态机事件，不消费 approval
- approval 决策在 `core.py::_handle_approval_decision()` 中统一消费

如果照原稿做，最容易出现的就是 approval 语义被拆成两条并行路径：一条在 core，一条在 merge_queue / transition_queue，后续再加 `rebase_conflict` / `precommit_failure` 时持续漂移。

已应用修正：

- Task 2 改成“沿用现有 approval consumer”
- 明确 `regression_failure` / `rebase_conflict` / `precommit_failure` 都继续在 `core.py` 处理
- Dev Notes 明确禁止把 approval routing 塞进 `transition_queue.py`

### 4. `fix_forward` / `pause` 的冻结语义原稿会打破 NFR10

原稿写了两个高风险承诺：

- `fix_forward` 后立即 `unfreeze()`
- `pause` 后“操作者手动解决后通过 ato approve 解冻”

这两个承诺都有问题：

- `fix_forward` 时 main 仍然是 regression-failed 状态，提前解冻会违反 NFR10
- approval 一旦被消费，就不存在“再对同一条 approval 做一次 ato approve 解冻”的路径

Story 4.2 已验证的安全语义是：

- `revert` 成功后才允许解冻
- `fix_forward` 通过 `regression_fail` 回到 `fixing`，但 queue 继续冻结，直到 recovery story 再次 merge 并通过 regression
- `pause` 只是保持冻结，不应虚构自动 unblock 路径

已应用修正：

- AC4 / Task 2 统一收敛为上述语义
- 移除了“pause 后通过同一 approval 再次解冻”的错误承诺

### 5. FR54 与测试计划的范围过大且落点不对

原稿把这两件事当成主交付：

- 完整 pre-commit 自动修复链路
- 多个全新 integration / unit 文件

当前仓库现实是：

- 现有 merge 路径是 ff-only，不天然产生新的 commit，也就不会自然命中 pre-commit hook
- merge / approval / notification / worktree 已经分别有成熟测试文件，盲目新建平行测试文件只会让覆盖分散

已应用修正：

- AC6 改成“条件性合同”：只有 merge 路径真的出现 commit-producing step 时，才把 FR54 变成实做主目标
- Task 3.2 改为“保持 `precommit_failure` 合同不漂移；若真的引入 commit step，再走 adapter/settings/SubprocessManager 正路”
- Task 4 / Task 5 改成优先扩展 `test_merge_queue.py`、`test_core.py`、`test_worktree_mgr.py`、`test_notification_flow.py`

## 已应用增强

- 为 story 补回了 create-story 模板自带的 validation note 注释。
- 在 Dev Notes 中显式补入“Regression 仍是 post-merge main safety gate”的 guardrail。
- 把测试策略从“默认新建文件”收敛为“优先扩展现有 suites”。
- 增加 Change Log，记录本次 validate-create-story 的具体修订点。

## 剩余风险

- 这次验证只修订了 story 文档，没有修改实现代码，也没有运行测试；当前代码中某些细节仍可能与修订后的目标状态存在差距，例如 `revert` 后是否还需要回到 `fixing` 的产品语义。
- FR54 的完整自动修复链路仍取决于未来 merge 流程是否真的引入会触发 pre-commit 的 commit-producing step；在那之前，本 story 不应被误读为“必须为不可达路径写完整自动化”。
- 如果后续 planning 想把 regression 改成真正的 merge 前 gate，而不是当前的 post-merge safety gate，必须同步更新 Story 4.2 与 4.5，不能只改其中一篇。

## 最终结论

修正后，Story 4.5 已达到 `ready-for-dev` 的质量门槛。当前版本已经和 Story 4.2 已验证的 merge/regression 合同、Story 4.4 已验证的通知出口、`core.py` 的 approval 消费边界，以及当前测试布局对齐，不会再把 dev agent 带向错误的 regression 执行位置、重复通知、错误的解冻语义或平行测试矩阵。
