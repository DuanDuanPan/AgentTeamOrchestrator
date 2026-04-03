# Bug: 无效的 approval status 值导致 Orchestrator 崩溃

## 发现时间
2026-04-01 13:38:53

## 触发方式
手动修改 SQLite approvals 表的 `status` 字段为 `'decided'`（无效值），导致 Pydantic ValidationError 未被捕获，Orchestrator 进程崩溃退出。

## 现象
1. 直接执行 `UPDATE approvals SET status='decided'` 
2. Orchestrator 3 秒 polling cycle 读取到该记录
3. `_row_to_approval()` 调用 `ApprovalRecord.model_validate()` 抛出 `ValidationError`
4. 异常未被 `_process_approval_decisions()` 的 try/except 捕获（因为错误发生在查询层 `get_decided_unconsumed_approvals` 中，不在 per-approval 的 try/except 内）
5. Orchestrator 主循环崩溃退出：`transition_queue_stopped` → `orchestrator_stopped`

## 根因
- `ApprovalRecord.status` 是 `Literal['pending', 'approved', 'rejected']` 类型
- `get_decided_unconsumed_approvals()` 在查询结果转换为 `ApprovalRecord` 时未对 Pydantic 验证错误做防御性处理
- 上层 `_process_approval_decisions()` 的 try/except 只包裹了 per-approval 的处理逻辑，不包含查询本身

## 影响
- **严重** — 一个损坏的 approval 记录可以让整个 Orchestrator 崩溃
- Orchestrator 重启后的 crash recovery 正常工作，可以恢复

## 正确的 approval 状态值
- `pending` — 初始状态
- `approved` — 已批准
- `rejected` — 已拒绝

## 建议修复
1. `get_decided_unconsumed_approvals()` 中添加 try/except 对 Pydantic 验证错误的捕获，跳过无效记录并 log warning
2. 或在 SQL 查询中加 `WHERE status IN ('approved', 'rejected')` 而不是 `status != 'pending'`

## 临时处理
- 修正 `status` 为 `'approved'`
- 通过 tmux send-keys 重启 `ato start`
- Orchestrator crash recovery 自动恢复 story 状态
- 注意：recovery 将 story 从 reviewing 回退到 fixing，因此又触发了一轮 fixing

## 教训
直接修改 SQLite 数据时必须确认 Pydantic model 的类型约束。更安全的做法是通过 ATO 的 TUI/CLI 接口操作。
