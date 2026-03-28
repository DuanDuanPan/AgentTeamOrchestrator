# Story 验证报告：9.2 Workspace 概念引入 — 区分 Main 与 Worktree 执行环境

验证时间：2026-03-28  
Story 文件：`_bmad-output/implementation-artifacts/9-2-workspace-concept-worktree-timing.md`  
验证模式：`validate-create-story`  
结果：PASS（已应用修正）

## 摘要

原始 9.2 草稿最严重的问题不是“漏了几个文件”，而是 workspace 划分本身就写错了。它把 `dev_ready`、`merging`、`regression` 都划到了 worktree，这与当前 merge queue / regression 合同和 Story 9.3 的 spec commit 顺序直接冲突。

本次验证后，story 已被收敛为一个与当前代码和 Epic 9 内部依赖都一致的 workspace 合同，核心修正有 4 项：

1. 将 `dev_ready`、`merging`、`regression` 改回 `workspace: main`。
2. 补入 `convergent_loop.py` 这个真实影响面，因为当前 validating 正常路径仍假设 worktree。
3. 将 worktree 创建时机从“dev_ready 或 developing”收紧为首次进入 `developing`。
4. 移除对 `interactive_restart` main-path 的误导，把注意力收回当前真正存在的 main/worktree phase。

## 已核查证据

- 规划与规范工件：
  - `_bmad-output/planning-artifacts/prd.md`
  - `_bmad-output/planning-artifacts/architecture.md`
- 前序相关 story：
  - `_bmad-output/implementation-artifacts/2b-4-worktree-isolation.md`
  - `_bmad-output/implementation-artifacts/8-2-add-planning-phase.md`
  - `_bmad-output/implementation-artifacts/9-1-add-designing-phase.md`
- 当前代码：
  - `src/ato/config.py`
  - `src/ato/recovery.py`
  - `src/ato/core.py`
  - `src/ato/convergent_loop.py`
  - `src/ato/merge_queue.py`
  - `src/ato/worktree_mgr.py`
  - `ato.yaml.example`

## 发现的关键问题

### 1. 原稿把 `dev_ready`、`merging`、`regression` 错划为 worktree

当前仓库事实是：

- `merge_queue.py::_execute_merge()` 和 `_run_regression_test()` 明确在 main / project_root 上执行
- Story 9.3 需要在 `dev_ready` 这个 main-phase 窗口上完成 batch spec commit

如果把这三个阶段放进 worktree，会直接打破后续 Epic 9 的时序。

已应用修正：

- AC2 改为 `planning / creating / designing / validating / dev_ready / merging / regression = main`
- `developing / reviewing / fixing / qa_testing / uat = worktree`

### 2. 原稿遗漏了 `convergent_loop.py` 这个 validating 的真实运行面

当前 validating 的正常运行路径并不只在 recovery：

- `ConvergentLoop.run_first_review()` / `_resolve_worktree_path()` 仍假设 validating 运行在 worktree

如果只改 recovery.py，正常 validating 路径仍会继续用错 cwd。

已应用修正：

- Task 4 明确要求把 validating 的正常路径一起 main-path 化
- 将测试面扩展到 `tests/unit/test_convergent_loop.py`

### 3. 原稿把 worktree 创建时机写成“dev_ready 或 developing”，会和 9.3 冲突

一旦在 `dev_ready` 就创建 worktree：

- Story 9.3 的 batch spec commit 就会发生在 worktree 创建之后
- worktree 可能基于尚未包含规格提交的旧 main HEAD

已应用修正：

- 收紧为首次进入 `developing` 时创建 worktree
- 明确这是 9.3 spec commit 顺序成立的前提

### 4. 原稿把注意力放在 `_dispatch_interactive_restart()` 的 main-path 上，偏离当前现实

当前 interactive phases 仍是：

- `developing`
- `uat`

它们都应保留 worktree 语义。真正需要修改的是：

- structured_job recovery / restart
- validating convergent loop
- worktree 创建时机

已应用修正：

- 从 story 主体中移除了“main interactive phase”方向
- 将 runtime 重点收回当前真实存在的 phase 集合

## 已应用增强

- 增加了 merge queue / regression 作为 workspace 设计的反向校验基线
- 明确了 9.2 与 9.3 的接口合同：`dev_ready` 仍在 main，`developing` 才创建 worktree
- 补回了 validation note、Previous Story Intelligence、Dev Agent Record 结构

## 剩余风险

- Epic 9 的上游正式 epics 文档仍未出现在 `_bmad-output/planning-artifacts/epics.md`；这次验证主要基于代码、PRD、architecture 和相邻 stories 的交叉校正。
- 本次只修订了 story 与 validation report，没有实现代码，也没有运行测试。

## 最终结论

修正后，9.2 已经从“workspace 概念对，但阶段划分与真实运行面都错位”的草稿，收敛成了与当前 merge / regression / worktree 合同一致的 workspace story。最关键的冲突已经解除：`dev_ready` 不会再被误判为 worktree phase，validating 的正常路径也不会再被遗漏。
