# Story 4.1: Approval Queue 与 Nudge 通知机制

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a 操作者,
I want 在审批队列中查看所有待决策事项并做出决策,
So that 所有判断性决策集中管理，决策记录持久化。

## Acceptance Criteria

1. **AC1 — Approval 记录创建与持久化**
   ```
   Given 系统检测到需要人工决策的事件（merge 授权、超时、escalation、batch 确认）
   When 创建 approval 记录
   Then 写入 SQLite approvals 表，包含 approval_type、story_id、details（payload）、推荐操作、风险级别、created_at
   And 通过 nudge 通知 Orchestrator 立即轮询
   ```

2. **AC2 — CLI 审批队列查看**
   ```
   Given 操作者通过 CLI 查看审批队列
   When 运行 `ato approvals`
   Then 使用 rich 库格式化输出所有 pending approvals（类型图标 + story ID + 摘要 + 推荐操作）
   And 无 pending 时输出 "✔ 无待处理审批"
   ```

3. **AC3 — CLI 审批决策提交**
   ```
   Given 操作者做出决策
   When 运行 `ato approve <approval-id> --decision <选项>`
   Then 决策记录持久化（含时间戳、选择理由）（FR20）
   And `decision` 保存具体选项（如 `restart` / `resume` / `abandon`）
   And `status` 仅表示审批已处理：二元审批写 `approved` / `rejected`，多选审批统一写 `approved`
   And 通过 nudge 通知 Orchestrator，触发对应的恢复动作或状态转换
   ```

4. **AC4 — Nudge 通知机制增强**
   ```
   Given nudge 通知机制
   When TUI 或 CLI 写入 SQLite 后发送 nudge
   Then Orchestrator 立即轮询，响应延迟 <1 秒
   And nudge 丢失时，2-5 秒定期轮询兜底（已有）
   ```

5. **AC5 — approval 触发的用户可见通知基础**
   ```
   Given 新的 approval 创建
   When approval 类型映射到 NotificationLevel
   Then `regression_failure` → `urgent` → terminal bell（\a）
   And 常规 approval（merge / timeout / budget / blocking / crash recovery）→ `normal` → terminal bell
   And 本 Story 只接入 approval 创建路径；`silent` / `milestone` 的非 approval 场景留给 Story 4.4
   ```

6. **AC6 — Escalation Approval 支持**
   ```
   Given 任务失败（认证过期、超时、解析错误等）
   When 系统创建 escalation approval（FR50）
   Then approval payload 包含失败原因和可选恢复操作（重试 / 跳过 / escalate）
   And 恢复操作作为 decision 选项呈现
   ```

7. **AC7 — Orchestrator 审批消费**
   ```
   Given 操作者提交审批决策
   When Orchestrator 在 poll cycle 中检测到 approval 状态变更
   Then 根据 approval_type 和 decision 触发对应的恢复动作或 TransitionQueue 事件
   And 成功处理后记录 `consumed_at`，避免重复消费
   And 仅等待审批的 story 暂停，其他 stories 正常推进（非阻塞）
   ```

## Tasks / Subtasks

- [ ] Task 1: 扩展 ApprovalRecord 模型与 DB 层 (AC: #1, #3, #6)
  - [ ] 1.1 在 `src/ato/models/schemas.py` 中增加：
    - `ApprovalType = Literal["merge_authorization", "session_timeout", "crash_recovery", "blocking_abnormal", "budget_exceeded", "regression_failure", "precommit_failure", "convergent_loop_escalation", "batch_confirmation", "timeout", "needs_human_review"]`
    - `NotificationLevel = Literal["urgent", "normal", "silent", "milestone"]`
    - `APPROVAL_TYPE_TO_NOTIFICATION: dict[str, NotificationLevel]` 映射表
    - 扩展 `ApprovalRecord` 增加 `recommended_action: str | None = None`、`risk_level: Literal["high", "medium", "low"] | None = None`、`decision_reason: str | None = None`、`consumed_at: datetime | None = None`
  - [ ] 1.2 在 `src/ato/models/db.py` 中增加：
    - `update_approval_decision(db, approval_id, status, decision, decision_reason, decided_at)` — 更新审批决策
    - `get_approval_by_id(db, approval_id_prefix)` — 按 ID / 前缀查询单条 approval；多个命中时报“前缀不够长”
    - `get_decided_unconsumed_approvals(db)` — 查询 `status != 'pending' AND consumed_at IS NULL` 的 approvals（供 poll cycle 消费）
    - `mark_approval_consumed(db, approval_id, consumed_at)` — 仅在处理成功后标记消费
  - [ ] 1.3 在 `src/ato/models/migrations.py` 中：
    - 新增迁移：ALTER TABLE approvals ADD COLUMN `recommended_action TEXT`
    - 新增迁移：ALTER TABLE approvals ADD COLUMN `risk_level TEXT`
    - 新增迁移：ALTER TABLE approvals ADD COLUMN `decision_reason TEXT`
    - 新增迁移：ALTER TABLE approvals ADD COLUMN `consumed_at TEXT`
    - 递增 `PRAGMA user_version`

- [ ] Task 2: 扩展 nudge.py 为通知子系统 (AC: #4, #5)
  - [ ] 2.1 在 `src/ato/nudge.py` 中增加：
    - `NotificationLevel`（从 `schemas.py` 导入）
    - `send_user_notification(level: NotificationLevel, message: str)` 函数
    - MVP 实现：approval 创建路径只需覆盖 `urgent` / `normal` bell；`silent` 无动作，`milestone` 暂仅保留共享枚举
    - structlog 记录通知事件 `notification_sent`（含 level、message）
  - [ ] 2.2 保持现有 `Nudge` 类和 `send_external_nudge()` 不变

- [ ] Task 3: 实现 `ato approvals` CLI 命令 (AC: #2)
  - [ ] 3.1 在 `src/ato/cli.py` 中新增 `approvals` 命令：
    - 参数：`--db-path`（复用现有 pattern）、`--json`（可选 JSON 输出）
    - 调用 `get_pending_approvals(db)` 查询
    - 使用 `rich.table.Table` 格式化输出：类型图标、approval_id（前 8 位）、story_id、摘要、推荐操作、风险级别、创建时间
    - 摘要必须由 `approval_type + payload` 的确定性模板函数生成；不要直接回显原始 JSON
    - 无 pending 时输出 `✔ 无待处理审批`
  - [ ] 3.2 审批类型图标映射：
    - `merge_authorization` → `🔀`、`session_timeout` → `⏱`、`crash_recovery` → `↩`、`blocking_abnormal` → `⚠`、`budget_exceeded` → `💰`、`regression_failure` → `✖`、`convergent_loop_escalation` → `🔄`、`batch_confirmation` → `📦`、`timeout` → `⏳`、`precommit_failure` → `🔧`、`needs_human_review` → `👁`
    - 未知类型使用稳定 fallback（如 `?`），避免 CLI 因新类型崩溃

- [ ] Task 4: 实现 `ato approve` CLI 命令 (AC: #3)
  - [ ] 4.1 在 `src/ato/cli.py` 中新增 `approve` 命令：
    - 参数：`approval_id: str`（位置参数）、`--decision: str`（必填）、`--reason: str`（可选理由）、`--db-path`
    - 流程：
      1. 复用 `submit` 命令模式：`asyncio.run(_approve_async(...))` + `get_connection(db_path)`；不要退回同步 `sqlite3.connect`
      2. 调用 `get_approval_by_id(db, approval_id)` 查询（支持前缀匹配，≥4 字符即可；多个命中时报错）
      3. 验证 approval 存在且 status == "pending"
      4. 解析状态写入规则：
         - 二元审批：`approve` / `reject` → `status="approved"` / `status="rejected"`
         - 多选审批：`restart` / `resume` / `abandon` 等 → `status="approved"`，具体分支写入 `decision`
      5. 调用 `update_approval_decision(db, approval_id, status=..., decision=decision, decision_reason=reason, decided_at=now)`
      6. `db.commit()`
      7. 调用 `_send_nudge_safe(pid_path)` 通知 Orchestrator
      8. rich 格式化输出确认信息（控制在 80 列内）
    - 错误处理："发生了什么 + 你的选项"格式输出到 stderr

- [ ] Task 5: Orchestrator poll cycle 审批消费 (AC: #7)
  - [ ] 5.1 在 `src/ato/core.py` 中新增 `_process_approval_decisions()` 方法：
    - 在 `_poll_cycle()` 中调用，位于现有检查之后
    - 查询已决策但未消费的 approvals（`get_decided_unconsumed_approvals(db)`），不要仅依赖内存时间窗
    - 根据 `approval_type` + `decision` 映射处理：
      - `session_timeout` / `crash_recovery` + `"restart"` → 调用 interactive task 重调度 helper
      - `session_timeout` / `crash_recovery` + `"resume"` → 调用 interactive resume helper（复用 sidecar / `--resume` 约定）
      - `session_timeout` / `crash_recovery` + `"abandon"` → `TransitionEvent(event_name="escalate")`
      - `blocking_abnormal` + `"confirm_fix"` → `TransitionEvent(event_name="review_fail")`
      - `blocking_abnormal` + `"human_review"` → `TransitionEvent(event_name="escalate")`
      - `merge_authorization` + `"approve"` → 触发 merge 流程（Story 4.2 范围，此处仅 log）
      - `regression_failure` → Story 4.5 范围，此处仅 log
    - 对每条 approval：只有在动作 / 事件提交成功后才 `mark_approval_consumed(...)`
  - [ ] 5.2 不使用 `_last_approval_check` 作为唯一幂等来源；跨重启去重必须落在 DB 的 `consumed_at`

- [ ] Task 6: 统一 approval 创建辅助函数 (AC: #1, #6)
  - [ ] 6.1 在 `src/ato/models/db.py` 或新建 `src/ato/approval_helpers.py` 中创建：
    - 推荐新建 `src/ato/approval_helpers.py`，避免把通知 / nudge 逻辑塞进 `models/db.py`
    - `create_approval(db, story_id, approval_type, payload_dict, recommended_action, risk_level, nudge, orchestrator_pid) -> ApprovalRecord`
    - 自动生成 `approval_id`（UUID4）、`created_at`
    - 插入 DB 并 commit 后，再发送 nudge / bell；不要在 SQLite 写事务中 await 外部 IO
    - structlog 记录 `approval_created` 事件
  - [ ] 6.2 重构现有 approval 创建点：
    - `core.py._check_interactive_timeouts()` → 调用 `create_approval()`
    - `validation.py.maybe_create_blocking_abnormal_approval()` → 调用 `create_approval()`
    - `recovery.py` 的 `crash_recovery` approval 创建 → 调用 `create_approval()`
    - `adapters/bmad_adapter.py.record_parse_failure()` → 调用 `create_approval()`

- [ ] Task 7: 测试 (AC: #1-#7)
  - [ ] 7.1 `tests/unit/test_approval.py`（新文件）：
    - `test_create_approval_inserts_and_nudges` — 创建 approval 写入 DB + 触发 nudge
    - `test_update_approval_decision` — 更新决策后 status / decision / decision_reason / decided_at 正确
    - `test_get_pending_approvals_filters_decided` — 仅返回 pending
    - `test_get_approval_by_id_prefix_match` — 前缀匹配查询
    - `test_get_decided_unconsumed_approvals_and_mark_consumed` — DB 幂等消费
    - `test_notification_level_mapping` — 已存在 approval 类型（含 `crash_recovery`）正确映射到通知级别
    - `test_send_user_notification_bell` — `urgent` / `normal` 触发 bell，`silent` 无动作
  - [ ] 7.2 `tests/unit/test_cli_approval.py`（新文件）：
    - `test_ato_approvals_empty` — 无 pending 时输出 ✔
    - `test_ato_approvals_list` — 有 pending 时 rich 表格输出
    - `test_ato_approve_success` — 正常审批流程
    - `test_ato_approve_ambiguous_prefix` — 多个前缀命中时提示用户补长前缀
    - `test_ato_approve_not_found` — approval 不存在时错误信息
    - `test_ato_approve_already_decided` — 重复决策时错误信息
  - [ ] 7.3 `tests/unit/test_core.py`（追加）：
    - `test_process_approval_decisions_session_timeout` — 超时审批消费
    - `test_process_approval_decisions_crash_recovery` — 现有 crash recovery approval 可被统一消费
    - `test_process_approval_decisions_blocking_abnormal` — blocking 审批消费
    - `test_approval_non_blocking_other_stories` — 审批等待不阻塞其他 story

## Dev Notes

### 已有基础设施（复用，不重建）

| 组件 | 文件 | 现状 |
|------|------|------|
| `Nudge` 类 | `src/ato/nudge.py:14-52` | asyncio.Event + SIGUSR1 外部 nudge ✅ |
| `send_external_nudge()` | `src/ato/nudge.py:55-72` | SIGUSR1 信号发送 ✅ |
| `approvals` 表 DDL | `src/ato/models/db.py:74-84` | 基础 schema 已存在 ✅ |
| `ApprovalRecord` | `src/ato/models/schemas.py:264-279` | 基础 Pydantic 模型 ✅ |
| `insert_approval()` | `src/ato/models/db.py:506-525` | 插入函数 ✅ |
| `get_pending_approvals()` | `src/ato/models/db.py:527-545` | 查询 pending ✅ |
| `_send_nudge_safe()` | `src/ato/cli.py` | CLI 端安全 nudge 发送 ✅ |
| `_check_interactive_timeouts()` | `src/ato/core.py` | 自动创建 session_timeout approval ✅ |
| `maybe_create_blocking_abnormal_approval()` | `src/ato/validation.py:119-210` | blocking 异常 approval 创建 ✅ |
| crash recovery approval | `src/ato/recovery.py` | 已存在 `crash_recovery` 类型与 `restart/resume/abandon` 选项 ✅ |
| parse failure approval | `src/ato/adapters/bmad_adapter.py` | 已存在 `needs_human_review` 创建路径 ✅ |

### 缺失功能（本 Story 必须实现）

| 缺失 | 说明 |
|------|------|
| `update_approval_decision()` | **approvals 是 insert-only**，无更新函数 — 必须新增 |
| `get_approval_by_id()` | 无按 ID 查询函数 — approve 命令需要 |
| `ato approvals` 命令 | CLI 中无审批列表命令 |
| `ato approve` 命令 | CLI 中无审批决策命令 |
| approval 触发 bell | nudge.py 仅有进程间 nudge，无 terminal bell |
| Orchestrator 审批消费 | poll cycle 不检查已决策的 approvals |
| 统一 approval 创建 API | 四处创建代码各自独立，未统一 |

### 架构约束

1. **模块依赖方向**：`tui/` 不依赖 `core.py`，通过 SQLite 解耦。approval 写入 = SQLite + nudge，不走 IPC
2. **进程边界**：CLI 命令（`ato approve`）直写 SQLite + `send_external_nudge()`，与 `ato submit` 模式一致
3. **SQLite WAL**：`busy_timeout=5000` 覆盖并发写入极端情况，TUI/CLI 写入极低频
4. **Approval 等待语义**：Orchestrator 非阻塞——仅被审批的 story 暂停，其他 stories 继续推进
5. **错误输出格式**：CLI 错误使用"发生了什么 + 你的选项"格式，输出到 stderr
6. **退出码**：0 成功 / 1 一般错误 / 2 环境错误
7. **命名规范**：CLI 命令 kebab-case（typer 默认），Python snake_case，Pydantic PascalCase
8. **禁止在 Pydantic validator 中做 IO**
9. **SQLite 写事务中不 await 外部 IO**
10. **ruff check + ruff format + mypy 全部通过后再提交**

### Scope Boundary

- 本 Story 只交付 CLI 审批队列 + approval plumbing + approval-triggered bell；不要提前实现 `tui/approval.py` 的完整交互
- `ApprovalCard` / `y,n,d` 键位交互属于 Story 6.3a
- 更广义的通知体系与 CLI 交互打磨（里程碑 bell、异常审批 CLI polish）属于 Story 4.4

### approval_type → NotificationLevel 映射（来自架构 Decision 2 子节"用户可见通知子系统"）

```python
APPROVAL_TYPE_TO_NOTIFICATION: dict[str, NotificationLevel] = {
    "regression_failure": "urgent",
    "merge_authorization": "normal",
    "session_timeout": "normal",
    "crash_recovery": "normal",
    "blocking_abnormal": "normal",
    "budget_exceeded": "normal",
    "timeout": "normal",
    "convergent_loop_escalation": "normal",
    "batch_confirmation": "normal",
    "precommit_failure": "normal",
    "needs_human_review": "normal",
}
```

### approval_type → 推荐操作映射（来自 UX 设计规范）

```python
APPROVAL_RECOMMENDED_ACTIONS: dict[str, str] = {
    "merge_authorization": "approve",          # 大多数情况直接批准
    "session_timeout": "restart",             # 推荐重启
    "crash_recovery": "restart",              # 恢复策略默认重启
    "blocking_abnormal": "human_review",      # UX 规范：默认建议人工审阅
    "budget_exceeded": "increase_budget",     # 推荐增加预算
    "regression_failure": "fix_forward",      # 推荐修复而非 revert
    "timeout": "continue_waiting",            # 推荐继续等待
    "convergent_loop_escalation": "escalate", # 推荐人工介入
    "batch_confirmation": "confirm",          # 推荐确认
    "precommit_failure": "retry",             # 推荐重试
    "needs_human_review": "review",           # 推荐人工审阅
}
```

### DB 迁移注意事项

- 使用 `ALTER TABLE ... ADD COLUMN` 方式（SQLite 支持），不重建表
- `PRAGMA user_version` 递增（参考 `src/ato/models/migrations.py` 现有模式）
- 新列 `recommended_action TEXT`、`risk_level TEXT`、`decision_reason TEXT`、`consumed_at TEXT` 允许 NULL（向后兼容）
- 现有的 `payload` JSON 字段已包含部分上下文信息，新字段用于标准化高频访问属性

### CLI 命令实现参考

`ato approve` 命令的实现应遵循 `ato submit` 的模式 [Source: src/ato/cli.py]：
- typer 参数定义 + `--db-path` 选项
- `asyncio.run()` 驱动异步 helper；数据库连接复用 `get_connection()`（aiosqlite），不要另起同步 `sqlite3.connect`
- `_send_nudge_safe()` 发送 nudge
- rich 格式化输出

**前缀匹配**：approval_id 为 UUID4（36 字符），用户输入 ≥4 字符前缀即可匹配，使用 `WHERE approval_id LIKE ? || '%'` 查询。多个匹配时提示用户提供更长前缀。

### Project Structure Notes

- 新增文件：`tests/unit/test_approval.py`
- 新增文件：`tests/unit/test_cli_approval.py`
- 修改文件：
  - `src/ato/models/schemas.py` — 增加 ApprovalType、NotificationLevel、扩展 ApprovalRecord
  - `src/ato/models/db.py` — 增加 update/get_by_id 函数
  - `src/ato/models/migrations.py` — 新列迁移
  - `src/ato/nudge.py` — 增加 send_user_notification()
  - `src/ato/cli.py` — 增加 approvals/approve 命令
  - `src/ato/core.py` — 增加 _process_approval_decisions()
- `src/ato/recovery.py` / `src/ato/adapters/bmad_adapter.py` — 对齐统一 approval helper
- 推荐新增：`src/ato/approval_helpers.py`（统一创建 API）
- `src/ato/tui/app.py` — 当前 `write_approval()` 是 6.1a 占位实现；若本 Story 收紧 approval 语义，需要同步保持兼容
- 路径和命名完全符合架构规范 [Source: architecture.md 文件结构图]

### Previous Story Intelligence

**来自 Story 3.2c（Re-review Scope Narrowing）的经验：**
- `maybe_create_blocking_abnormal_approval()` 已正确使用幂等性检查（按 story_id + approval_type + status + round_num 去重）— 本 Story 的统一创建 API 应保留此模式
- convergent_loop.py 和 validation.py 中的 approval 创建代码是重构目标
- structlog 日志事件命名遵循 `模块_动作` 格式（如 `convergent_loop_round_complete`）

**来自 Story 6.1a（TUI launch + SQLite 写入）的经验：**
- `ATOApp.write_approval()` 当前只是占位 helper，默认假设 `status=decision`；本 Story 明确 approval 语义后，不能让 CLI / TUI 两条路径各自漂移
- `send_external_nudge()` 的 best-effort 语义已经在 TUI 侧验证过：commit 成功后再 nudge，`ProcessLookupError` / `PermissionError` 只告警不回滚

**来自最近 git commits 的模式：**
- 最近 commits 遵循 `feat: Story X.Y 描述` 格式
- 代码组织清晰，每个 story 聚焦于单一职责
- 测试与实现同步完成

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 4.1] — AC 原文
- [Source: _bmad-output/planning-artifacts/architecture.md#Decision 2] — TUI↔Orchestrator 通信模型
- [Source: _bmad-output/planning-artifacts/architecture.md#用户可见通知子系统] — NotificationLevel 枚举与触发规则
- [Source: _bmad-output/planning-artifacts/architecture.md#Decision 4] — Interactive Session 完成检测
- [Source: _bmad-output/planning-artifacts/architecture.md#FR到结构的映射] — 人机协作 FR19-23 → tui/approval.py, cli.py, nudge.py
- [Source: _bmad-output/planning-artifacts/prd.md#FR19-FR23, FR50] — 功能需求原文
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md#ApprovalCard] — 审批卡片设计规范
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md#Flow 2] — 日常审批循环用户流
- [Source: src/ato/nudge.py] — 现有 nudge 实现
- [Source: src/ato/models/db.py:506-545] — 现有 approval CRUD
- [Source: src/ato/validation.py:119-210] — blocking abnormal approval 创建
- [Source: src/ato/recovery.py] — 现有 `crash_recovery` approval 创建与恢复语义
- [Source: src/ato/adapters/bmad_adapter.py] — 现有 `needs_human_review` approval 创建
- [Source: src/ato/core.py] — Orchestrator 事件循环与 poll cycle
- [Source: src/ato/cli.py] — CLI 命令结构（ato submit 模式参考）

### Change Log

- 2026-03-25: create-story 创建 — 基于 Epic 4 / PRD / 架构 / UX spec 与现有 approval 代码路径生成完整开发上下文
- 2026-03-25: validate-create-story 修订 —— 补回现有 `crash_recovery` approval 类型与创建路径；为 FR20 补齐 `decision_reason` 持久化；把审批消费改为 `consumed_at` 幂等模型而非错误复用 `expected_artifact` / 内存时间窗；纠正 `blocking_abnormal` 推荐动作与 UX 规范冲突；改正 `ato approve` 的 SQLite 接入方式并新增 CLI/TUI 语义对齐约束

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List
