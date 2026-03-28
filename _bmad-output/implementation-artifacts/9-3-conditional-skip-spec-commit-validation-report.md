# Story 验证报告：9.3 条件阶段跳过 + Story 规格自动提交主分支

验证时间：2026-03-28  
Story 文件：`_bmad-output/implementation-artifacts/9-3-conditional-skip-spec-commit.md`  
验证模式：`validate-create-story`  
结果：PASS（已应用修正）

## 摘要

原始 9.3 草稿有两个明显会把实现带偏的方向：一是把规格文件放到并不存在的 `_bmad-output/stories/` 树里，二是无依据地把需求扩大成 `git push origin main`。此外，它还重复发明了一个 `spec_commit_failure` approval type，而仓库已经有可复用的 `precommit_failure` 基础设施。

本次验证后，story 已收敛为一个能落在当前代码基座上的 skip + local-main spec commit 合同，核心修正有 5 项：

1. 将规格文件真源收紧到 `_bmad-output/implementation-artifacts`。
2. 移除未建立既有合同的 remote push，仅要求提交到本地 `main`。
3. 复用现有 `precommit_failure` approval type，并在 payload 中标识 `scope: spec_batch`。
4. 明确复用 `get_active_batch()` / `get_batch_stories()`，避免重写 batch 扫描逻辑。
5. 将 skip 触发点收紧到 TransitionQueue 的 post-commit hook，而不是状态机 callback。

## 已核查证据

- 规划与规范工件：
  - `_bmad-output/planning-artifacts/prd.md`
  - `_bmad-output/planning-artifacts/architecture.md`
  - `_bmad-output/implementation-artifacts/sprint-status.yaml`
- 前序相关 story：
  - `_bmad-output/implementation-artifacts/9-1-add-designing-phase.md`
  - `_bmad-output/implementation-artifacts/9-2-workspace-concept-worktree-timing.md`
  - `_bmad-output/implementation-artifacts/4-2-merge-queue-regression-safety.md`
- 当前代码：
  - `src/ato/transition_queue.py`
  - `src/ato/core.py`
  - `src/ato/models/db.py`
  - `src/ato/models/migrations.py`
  - `src/ato/models/schemas.py`
  - `src/ato/worktree_mgr.py`
  - `src/ato/merge_queue.py`
  - `src/ato/approval_helpers.py`

## 发现的关键问题

### 1. 原稿使用了不存在的 `_bmad-output/stories/` 目录

当前仓库事实是：

- `sprint-status.yaml` 的 `story_location` 固定为 `_bmad-output/implementation-artifacts`
- create-story / validation report / sprint-status 全部围绕这棵目录工作

如果开发者照原稿去 stage `_bmad-output/stories/*/`，要么什么都提交不到，要么会被迫再造一套并行目录结构。

已应用修正：

- 将 spec commit 的目标路径改为 `_bmad-output/implementation-artifacts/{story_id}.md`
- 将 designing 产出对齐为可选的 `{story_id}-ux/`

### 2. 原稿把需求扩大到了 `git push origin main`，当前仓库没有这个合同

当前已存在的 main-branch Git 流程是：

- `WorktreeManager.merge_to_main()` 本地 ff merge
- `MergeQueue._run_regression_test()` 在本地 main 上跑 regression

没有现成的 remote push 恢复、认证、远端不存在等基础设施。把 push 突然拉进来，会显著扩大 story 体量。

已应用修正：

- AC5 / AC6 改为提交到本地 `main`
- 将 remote push 明确放到 Scope Boundary 外

### 3. 原稿新发明了 `spec_commit_failure` approval type，属于重复造轮子

当前仓库已经有：

- `precommit_failure` approval type
- 对应的推荐动作与 CLI/TUI 展示
- 现成的 approval helper / core decision handling

新增一个仅用于 spec commit 的新类型，会无端扩散到 schema、approval UI、decision handling 和测试矩阵。

已应用修正：

- 复用 `precommit_failure`
- 在 payload 中补 `scope: "spec_batch"`、`batch_id`、`story_ids` 来区分场景

### 4. 原稿想新增 `_check_batch_all_dev_ready()`，但仓库已经有现成 batch 查询 helper

当前 `src/ato/models/db.py` 已提供：

- `get_active_batch()`
- `get_batch_stories()`

如果再写一套平行 helper，开发者很容易把条件判断分散到两个地方。

已应用修正：

- Task 5 改为明确复用既有 DB helper
- 将“检测全部到达 dev_ready”的逻辑定位在现有 batch 读模型之上

### 5. 原稿对 skip 触发点的描述过于抽象，容易落到错误层

当前 `TransitionQueue._consumer()` 的结构是：

- `send()` → `save_story_state()` → `commit()`
- 之后只有一个 story-done post-commit hook

如果开发者把 skip 写进 `state_machine.py` callback，会拿不到 DB 中的 `has_ui` 和 phase config 上下文。

已应用修正：

- 将 skip 触发点收紧为 TransitionQueue post-commit hook
- 明确需要把现有“只处理 done”的 hook 泛化

## 已应用增强

- 将 9.3 与 9.2 的时序合同明确对齐：先本地 spec commit，后 `start_dev`，再创建 worktree
- 补入了对本地 commit 幂等性的要求，避免重复提交空 commit
- 补回了 validation note、Previous Story Intelligence、Dev Agent Record 结构

## 剩余风险

- Epic 9 仍缺正式 epics 文档；这次验证主要依据当前 story 草稿、PRD、architecture、sprint-status 与现有代码路径。
- 本次只修订了 story 与 validation report，没有实现代码，也没有运行测试。

## 最终结论

修正后，9.3 已经从“目标合理，但实现路径发散且重复造轮子”的草稿，收敛成了可直接交给 dev-story 的 story。最大的风险点已经移除：不会再误用不存在的 story 目录，不会再无端引入 remote push，也不会再为了 spec commit 单独扩展一整套 approval 类型体系。
