# Story 验证报告：4.1 Approval Queue 与 Nudge 通知机制

验证时间：2026-03-25 20:18:03 CST
Story 文件：`_bmad-output/implementation-artifacts/4-1-approval-queue-nudge.md`
验证模式：`validate-create-story`
结果：PASS（已应用修正）

## 摘要

该 story 的主方向正确，但原稿里有 6 个会直接把实现带偏的合同问题：

1. 它收窄了 `ApprovalType`，却漏掉仓库里已经真实存在并在运行的 `crash_recovery` approval。
2. AC3 要求持久化“选择理由”，但任务和 schema 设计没有任何 `reason` 落点。
3. 它试图用 `expected_artifact` 或仅内存时间窗给 approval 去重；前者属于 `tasks` 表字段，后者跨重启不可靠。
4. 它把 `blocking_abnormal` 的推荐动作写成“继续进入 fix”，和 UX 规范的默认推荐“人工审阅”冲突。
5. 它要求 `ato approve` 走同步 `sqlite3.connect`，和当前 CLI 模式、`get_connection()` 的 WAL/busy-timeout 约定冲突。
6. 它把 4.4 / 6.3a 的范围提前混进 4.1，包括里程碑通知和完整 TUI 审批交互语义，容易造成跨 story 返工。

这些问题如果不修，最常见的后果是：现有 `crash_recovery` approval 在新 CLI 中变成“未知类型”，`--reason` 形同虚设，Orchestrator 审批消费重复或漏消费，`blocking_abnormal` 默认推荐与 UX 相反，以及 CLI / TUI 两条审批写入路径继续各自漂移。

## 已核查证据

- 规划与 story 工件：
  - `_bmad-output/planning-artifacts/epics.md`
  - `_bmad-output/planning-artifacts/architecture.md`
  - `_bmad-output/planning-artifacts/prd.md`
  - `_bmad-output/planning-artifacts/ux-design-specification.md`
  - `_bmad-output/project-context.md`
- 当前代码基线：
  - `src/ato/models/schemas.py`
  - `src/ato/models/db.py`
  - `src/ato/cli.py`
  - `src/ato/core.py`
  - `src/ato/recovery.py`
  - `src/ato/validation.py`
  - `src/ato/adapters/bmad_adapter.py`
  - `src/ato/tui/app.py`

## 发现的关键问题

### 1. `crash_recovery` 被漏出 `ApprovalType`，会直接打断现有审批生态

当前仓库已经真实创建并消费：

- `session_timeout` approval：`src/ato/core.py`
- `crash_recovery` approval：`src/ato/recovery.py`
- `needs_human_review` approval：`src/ato/adapters/bmad_adapter.py`

原稿的 `ApprovalType` 只列了前两类里的一部分，却漏掉 `crash_recovery`。如果 dev 按原稿把 `approval_type` 收紧成 `Literal`，现有恢复路径和对应测试会立刻失配。

已应用修正：

- 补回 `crash_recovery`
- 通知映射、图标映射、审批消费、统一 helper 重构范围一并补齐 `crash_recovery`

### 2. FR20 的“选择理由”没有持久化载体

原稿 AC3 写了：

- 决策记录持久化（含时间戳、选择理由）

但任务设计只有：

- `decision`
- `decided_at`

没有任何 `reason` / `decision_reason` 字段，也没有“写回 payload”的显式约定。结果就是 CLI 接了 `--reason` 也无处落库。

已应用修正：

- `ApprovalRecord` 增加 `decision_reason`
- 迁移增加 `decision_reason TEXT`
- `update_approval_decision()` 显式接收 `decision_reason`

### 3. approval 去重/消费原方案是错层的

原稿写了：

- “标记已消费的 approval（通过 `expected_artifact` 或独立标记字段）”
- “增加 `_last_approval_check` 时间戳，避免重复扫描”

这里有两个问题：

- `expected_artifact` 是 `tasks` 表字段，不是 `approvals` 表语义
- 纯内存 `_last_approval_check` 在 Orchestrator 重启后无法保证幂等

如果照此实现，最容易出现的就是：提交成功但进程在标记前崩溃，重启后重复消费或直接漏消费。

已应用修正：

- 改为 DB 层显式 `consumed_at`
- 使用 `get_decided_unconsumed_approvals()` + `mark_approval_consumed()`
- 明确“成功处理后才标记 consumed”

### 4. `blocking_abnormal` 推荐动作与 UX 规范冲突

UX 规范对常规审批给出的默认推荐是：

- `blocking_abnormal` → 默认推荐“人工审阅”

原稿却写成：

- 推荐继续进入 fix
- decision 示例是 `continue` / `escalate`

这会同时带偏 CLI 文案、默认按钮、以及后续 TUI 的推荐语义。

已应用修正：

- 推荐动作改为 `human_review`
- decision 名称改成更接近真实交互语义的 `confirm_fix` / `human_review`
- Orchestrator 侧再把 `human_review` 映射为 `escalate`

### 5. `ato approve` 的 DB 接入方式引用错了当前项目模式

原稿要求：

- “数据库连接管理（同步 `sqlite3.connect`，非 aiosqlite）”

但当前仓库真实模式是：

- CLI 异步 helper + `asyncio.run(...)`
- SQLite 连接统一走 `get_connection()`
- `get_connection()` 负责 WAL / `busy_timeout=5000` / `foreign_keys=ON`

如果 4.1 另起一条同步连接路径，不仅重复实现，还容易绕开项目已有 pragma 约定。

已应用修正：

- 改成复用 `submit` 模式：`asyncio.run(_approve_async(...))`
- 明确复用 `get_connection()`

### 6. Story 范围向 4.4 / 6.3a 漂移

原稿把这些内容直接写进 4.1：

- `silent` / `milestone` 非 approval 通知
- 完整 TUI 审批交互的语义延伸

但按 epics 与已有 6.1a/6.1b 基线：

- 更广义通知体系与 CLI polish 属于 Story 4.4
- `ApprovalCard` / `y,n,d` 交互属于 Story 6.3a

已应用修正：

- 将 AC5 收敛为“approval 创建路径的 bell foundation”
- 增加 Scope Boundary，明确 4.1 不提前实现 `tui/approval.py` 完整交互

## 已应用增强

- 明确 `status` 与 `decision` 的职责分离：
  - `status` 仅表示是否已处理
  - `decision` 保存具体动作分支
- 为 `ato approvals` 补入“摘要必须由 `approval_type + payload` 的确定性模板函数生成”，避免直接输出原始 JSON
- 补充未知 approval 类型 fallback 图标，避免 CLI 因未来新增类型崩溃
- 在 Dev Notes 中显式补入 6.1a 的 TUI 占位 helper 语义，防止 CLI / TUI 两条审批写入路径继续漂移
- 增加 Change Log，记录本次 validate-create-story 的具体修订点

## 剩余风险

- `session_timeout` / `crash_recovery` 的 `restart` / `resume` 仍需要后续实现明确复用哪一个 dispatch/resume helper；本次 story 已把方向收敛为“不要伪装成普通状态机事件”，但具体 helper 细节仍要落在实现阶段。
- `ApprovalCard` 与异常审批多选面板的最终 TUI 表达仍在 6.3a / 6.3b；4.1 目前只交付 CLI + approval plumbing，不应被误读为“审批 UX 全部完成”。

## 最终结论

修正后，Story 4.1 已达到 `ready-for-dev` 的质量门槛。当前版本已经和真实代码基线中的 `session_timeout` / `crash_recovery` / `needs_human_review` approval 类型、FR20 的理由持久化、SQLite 连接约定、以及 approval 消费幂等模型对齐，不会再把 dev agent 带向错误的 DB 访问方式、失效的去重策略或与 UX 规范相反的默认推荐。
