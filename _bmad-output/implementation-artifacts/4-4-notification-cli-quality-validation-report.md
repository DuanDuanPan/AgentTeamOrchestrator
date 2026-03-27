# Story 验证报告：4.4 通知体系与 CLI 交互质量

验证时间：2026-03-27 08:02:28 CST
Story 文件：`_bmad-output/implementation-artifacts/4-4-notification-cli-quality.md`
验证模式：`validate-create-story`
结果：PASS（已应用修正）

## 摘要

该 story 的方向与 Epic 4、FR22/FR50、Notification Patterns 和 Flow 5 一致，但原稿里仍有 5 个会直接把实现带偏的合同问题，已在 story 文件中修正：

1. 里程碑通知同时挂在 `core` 与 `merge_queue`，而 `merge_queue._complete_regression_pass()` 发生在 story 真正写成 `done` 之前，会造成 false positive / double bell。
2. batch 完成方案只用进程内 `_batch_complete_notified`，忽略已有 `batches.status/completed_at` 生命周期；重启后会重复通知，且 active batch 无法可靠收口。
3. AC4 写的是“所有 CLI 命令”，但任务和测试矩阵漏掉了 `ato batch status`、`ato plan`、`ato tui` 等入口，dev 可以做完清单却仍然不满足 AC。
4. 通知 contract 没有把 approval 短 ID 与快捷命令写进消息体，违背 UX 的“自包含通知”与 CLI quick path 原则。
5. approval detail 设计默认推荐动作一定可执行，但当前真实代码里 `needs_human_review -> review` 与合法选项 `retry/skip/escalate` 已经漂移，不先收敛会输出无效推荐。

## 已核查证据

- 规划与 story 工件：
  - `_bmad-output/planning-artifacts/epics.md`
  - `_bmad-output/planning-artifacts/prd.md`
  - `_bmad-output/planning-artifacts/architecture.md`
  - `_bmad-output/planning-artifacts/ux-design-specification.md`
  - `_bmad-output/project-context.md`
  - `_bmad-output/implementation-artifacts/4-3-uat-interactive-session-completion.md`
- 当前代码基线：
  - `src/ato/nudge.py`
  - `src/ato/approval_helpers.py`
  - `src/ato/cli.py`
  - `src/ato/transition_queue.py`
  - `src/ato/merge_queue.py`
  - `src/ato/models/db.py`
  - `src/ato/models/schemas.py`

## 发现的关键问题

### 1. 里程碑通知的接入点写错层了

原稿同时要求：

- 在 TransitionQueue consumer 成功处理 `regression_pass` 后发里程碑通知
- 在 `merge_queue._complete_regression_pass()` 中也发一次

但当前仓库里：

- `merge_queue._complete_regression_pass()` 只会 `submit(TransitionEvent("regression_pass"))`
- 真正把 story 写成 `done` 的动作发生在 `transition_queue.py` 里 `save_story_state(...); commit()`

如果 dev 按原稿双挂钩：

- 最轻会双响 bell
- 最坏会在状态机 commit 失败时先通知“Story 已完成”，制造假阳性

已应用修正：

- story 明确改成 **TransitionQueue commit 成功后的单一 post-commit hook**
- 显式禁止在 `merge_queue._complete_regression_pass()` 提前发 milestone bell

### 2. batch 完成不能只靠进程内 set 去重

原稿用 `_batch_complete_notified: set[str]` 防重复，但仓库已有：

- `BatchStatus = Literal["active", "completed", "cancelled"]`
- `batches.completed_at`
- `get_active_batch()` / `get_batch_progress()`

如果只靠内存 set：

- 重启后会再次通知同一个 batch
- `batches.status` 仍停在 `active`
- 后续 `ato batch select` 继续受“仅允许一个 active batch”约束阻塞

已应用修正：

- 在 story 中新增 `complete_batch()`（或等价 helper）要求
- 只有 `active -> completed` 的 DB 状态迁移成功时才发送 batch milestone 通知

### 3. CLI 错误格式的审计范围小于 AC4

AC4 写的是：

- “所有 CLI 命令的错误输出”

但原稿任务清单只点名了：

- `approvals`
- `approve`
- `uat`
- `submit`
- `init`
- `start/stop`
- `batch select`

实际仓库还有：

- `batch status`
- `plan`
- `tui`

并且这些命令当前确实还残留：

- DB 不存在返回 code=1
- 英文错误文案（如 `Story not found: ...`）
- 原始 `错误：{exc}` 风格输出

已应用修正：

- story 的任务矩阵扩展到全部 CLI 入口
- 测试矩阵补入 `batch status` / `plan` / `tui` 的退出码与文案校验

### 4. 通知内容缺少 approval ID 与快捷命令

UX 规范明确要求：

- 通知内容自包含审批 ID
- 用户可直接 `ato approve <id>` 走 CLI 快速路径

而当前 `create_approval()` 只调用：

- `send_user_notification(level, f"新审批: {approval_type} (story: {story_id})")`

如果 dev 按原稿只加 bell / prefix：

- bell 会响
- 但通知仍然不自包含，用户无法从提示直接做决策

已应用修正：

- story 增加 `approval_helpers.create_approval()` 消息内容要求
- 通知正文必须包含 `approval_id[:8]` 与合法快捷命令

### 5. approval recommendation 合同已有漂移，detail 视图必须先收敛

当前仓库里：

- `APPROVAL_RECOMMENDED_ACTIONS["needs_human_review"] == "review"`
- `_DEFAULT_VALID_OPTIONS["needs_human_review"] == ["retry", "skip", "escalate"]`

原稿的 detail 渲染直接假定：

- `recommended_action` 可直接加星
- 可直接拼成 `ato approve <id> --decision <recommended>`

这会让某些 approval 详情页直接展示无效推荐。

已应用修正：

- story 新增 recommendation vs valid-options 一致性审计
- 要求修正 drift 或在 renderer 中做安全 fallback

## 已应用增强

- 将 milestone 通知 seam 收敛为 `transition_queue.py` 的 post-commit hook
- 将 batch 完成从“内存去重”升级为 `batches.status = completed` 的持久化状态迁移
- 将 approval 通知升级为自包含消息，带短 ID 与 CLI 快捷命令
- 把 CLI 错误格式 / 退出码的覆盖范围扩展到所有命令入口
- 将 approval detail 的推荐动作渲染建立在“合法 decision 一致性”之上，避免展示不可执行命令

## 剩余风险

- 本次验证只修订了 story 文档，没有实现代码，也没有运行测试。
- `approval-detail` 最终采用独立命令还是 `ato approvals --detail` 子选项，story 允许二选一；实现时需要选一种主路径，避免 CLI 表面重复。
- batch 完成后是否立即触发下一批推荐，当前 story 仍未要求；本次只把“完成收口 + 里程碑通知”收敛清楚。

## 最终结论

修正后，Story 4.4 已达到 `ready-for-dev` 的质量门槛。当前版本已经和真实代码基线中的 TransitionQueue 提交语义、batch 生命周期模型、Notification UX、自包含 CLI 快速路径，以及 approval recommendation 合同对齐，不会再把 dev agent 带向提前通知、重复通知、漏掉 CLI 入口，或输出无效推荐命令的实现路径。
