# Story 验证报告：4.2 Merge Queue 与 Regression 安全管理

验证时间：2026-03-26 20:17:03 CST
Story 文件：`_bmad-output/implementation-artifacts/4-2-merge-queue-regression-safety.md`
验证模式：`validate-create-story`
结果：PASS（已应用修正）

## 摘要

该 story 的方向与 Epic 4、FR31/FR32/FR52 以及当前 approval 基础设施一致，但原稿里仍有 6 个会直接把实现带偏的合同问题，已在 story 文件中修正：

1. `MergeQueue.process_next()` 直接等待完整 merge / regression 流程，会阻塞 Orchestrator poll loop，违背“仅等待当前审批 story，其他 story 继续推进”的运行语义。
2. queue entry 没有记录 `regression_task_id`，`_poll_cycle()` 无法可靠判断哪个 completed regression 属于当前 merge。
3. `merge_to_main()` 在 merge 成功后立刻 cleanup worktree，会让 `regression_failure` 的 `fix_forward` / `manual_resolve` 失去 branch 上下文。
4. story 仍把 `MergeQueue` 设计成持有单个固定 `SubprocessManager`，但当前仓库的 manager 是 adapter-specific；同时示例代码还残留 `create_approval(payload=...)` 这类不存在的接口写法。
5. 新 approval 类型/选项没有完整落到 `cli.py` 的图标、摘要与决策校验合同里，`ato approve` 会接受不了 `rebase_conflict` / 新版 `regression_failure` 语义。
6. story 要求 rebase 120 秒，但当前 `WorktreeManager._run_git()` 默认固定 30 秒；如果不显式扩展 timeout 合同，实现会在大仓库上误超时。

## 已核查证据

- 规划与 story 工件：
  - `_bmad-output/planning-artifacts/epics.md`
  - `_bmad-output/planning-artifacts/prd.md`
  - `_bmad-output/planning-artifacts/architecture.md`
  - `_bmad-output/planning-artifacts/ux-design-specification.md`
  - `_bmad-output/project-context.md`
- 当前代码基线：
  - `src/ato/core.py`
  - `src/ato/state_machine.py`
  - `src/ato/worktree_mgr.py`
  - `src/ato/subprocess_mgr.py`
  - `src/ato/approval_helpers.py`
  - `src/ato/config.py`
  - `src/ato/cli.py`
  - `src/ato/models/db.py`
  - `src/ato/models/schemas.py`

## 发现的关键问题

### 1. merge queue 原设计会把整个 poll loop 卡住

原稿让 `process_next()` 在 poll cycle 中直接调用 `_execute_merge()`，而 `_execute_merge()` 又等待 regression 结束。这会导致：

- approval 消费、interactive timeout 检测、recovery 事件处理都被当前 merge 长时间阻塞
- “queue 串行化”变成“整个 orchestrator 单线程卡死到 regression 完成”

已应用修正：

- `process_next()` 收敛为 claim + schedule 后台 merge worker，再立即返回
- 将 regression 完成/失败的收敛职责明确放回 `_poll_cycle()` 的完成检测
- 在架构约束里显式写明 `MergeQueue` 不得阻塞 poll loop

### 2. regression 完成检测缺少稳定锚点

原稿只写“通过 task completion 检测等待 regression 结果”，但没有任何字段把 queue entry 和具体 regression task 绑定起来。当前仓库里：

- tasks 表会不断累积 `phase="regression"` 的 completed 记录
- `_poll_cycle()` 必须知道“当前 merge 对应的是哪一个 regression task”

已应用修正：

- `merge_queue` 表新增 `regression_task_id`
- 新增 `mark_regression_dispatched(...)`
- `_poll_cycle()` 的 regression detector 只处理 `merge_queue.regression_task_id` 对应、且尚未提交 transition 的 completed task

### 3. merge 后立即 cleanup 与 `fix_forward` 语义冲突

原稿同时要求：

- merge 成功后 `cleanup(story_id)`
- regression 失败时支持 `fix_forward` / `manual_resolve`

这两件事不能同时成立。若 branch/worktree 已删，fix-forward 就没有可继续修复的上下文。

已应用修正：

- `merge_to_main()` 改为**不在 merge 成功时立刻 cleanup**
- cleanup 延后到 `regression_pass`、成功 `revert` 或明确 `abandon`
- 在架构约束中补入“保留 worktree 直到 regression 闭环完成”

### 4. story 的 API / 组件假设与真实代码面不一致

原稿虽然已经改正了一部分命名，但关键实现面仍有 drift：

- `SubprocessManager` 在当前仓库里绑定单一 adapter，不适合被 `MergeQueue` 当成全能调度器长驻持有
- approval helper 的真实接口是 `create_approval(..., payload_dict=...)`
- structured job 的既有约定是 `dispatch_with_retry()`，不是再发明一条新路径

已应用修正：

- `MergeQueue` 构造参数移除固定 `subprocess_mgr`
- 明确要求按 role / cli_tool 动态创建 adapter + manager
- 将示例代码统一改成 `create_approval(..., payload_dict=...)`

### 5. CLI approval 合同没有跟上 4.2 的新语义

4.2 新增或改写了这些 decision 集：

- `regression_failure`: `revert / fix_forward / pause`
- `rebase_conflict`: `manual_resolve / skip / abandon`
- `precommit_failure`: `retry / manual_fix / skip`

如果不同时修改 `cli.py`：

- `ato approve` 的默认选项校验会拒绝合法 decision
- `ato approvals` 的摘要与图标会失真

已应用修正：

- 在 story 中显式加入 `src/ato/cli.py` 变更项
- 在测试计划中补入 `tests/unit/test_cli_approval.py`

### 6. 120 秒 rebase 需求没有落到实际 git helper

原稿写了 `rebase_onto_main()` 要支持 120 秒超时，但当前 `WorktreeManager._run_git()` 固定 30 秒。若不显式扩展：

- 文档要求与真实 helper 能力矛盾
- dev 很容易绕过 `_run_git()` 另写裸 subprocess，破坏三阶段清理协议

已应用修正：

- story 改为要求给 `_run_git()` 增加可选 `timeout_seconds`
- 继续复用统一清理协议，而不是单点逃逸

## 已应用增强

- 将 merge worker 的职责边界收敛为“merge + dispatch regression”，把 regression 收敛逻辑交回 `_poll_cycle()`
- 为 `merge_queue` 增加 `regression_pending` / `regression_task_id` 语义
- 明确 `revert` 成功后才 cleanup worktree；`fix_forward` 保留 branch 上下文
- 将 `MergeQueue` 的 subprocess 依赖改成“按 adapter 动态创建”，对齐当前仓库的 manager 设计
- 把 `create_approval()` 的伪代码改为真实 `payload_dict` 签名
- 在 `Change Log` 中记录本次 validate-create-story 修订点

## 剩余风险

- 本次验证只修订了 story 文档，没有实现代码，也没有运行测试。
- 文档当前采用 `regression_fail = regression.to(fixing)` 方案来建模 `fix_forward`。这与 Epic 4 的目标一致，但真正落地时仍需要确保 main 分支上的 fix-forward 流程、后续再 merge/再 regression 的闭环在代码上是自洽的。
- Epic 4.5 对 regression/merge 顺序仍有进一步演化空间；如果后续 planning 要改成 merge 前测试，必须显式更新 story 链路，不能静默漂移。

## 最终结论

修正后，Story 4.2 已达到 `ready-for-dev` 的质量门槛。当前版本已经和现有 `ATOSettings`、`SubprocessManager.dispatch_with_retry()`、`create_approval(payload_dict=...)`、CLI approval 校验合同、以及 Orchestrator 非阻塞轮询模型对齐，不会再把 dev agent 带向阻塞式 poll loop、失效的 regression 检测、或 cleanup 过早导致无法 fix-forward 的实现路径。
