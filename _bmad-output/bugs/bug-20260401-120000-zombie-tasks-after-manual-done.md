# Bug: 手动标记 story 为 done 后 zombie 任务和审批残留

## 发现时间
2026-04-01 12:00

## 影响 Story
2-7-strategy-seed-generation

## 现象
1. Story 2-7 通过直接修改 SQLite DB 标记为 `done/done`
2. Orchestrator 内存中的状态未同步，仍然认为 story 在 `fixing` 阶段
3. 已有的 qa_testing 任务继续运行直到超时 (1800s timeout at 11:59:20)
4. 超时后触发 `recovery_convergent_loop_error`，创建新的 `crash_recovery` 审批 (7a8698dc)
5. 尝试重启时因 worktree `.worktrees/2-7-strategy-seed-generation` 已被 merge 阶段清理而报 `FileNotFoundError`
6. 审批一直挂着无人处理

## 根因
直接修改 SQLite `stories` 表的 `current_phase` 和 `status` 字段只更新了持久化层，但：
- **Orchestrator 的 in-memory 状态机未收到 transition 事件** — `StoryLifecycle` 实例仍在旧状态
- **正在运行的 task 未被 cancel** — PID 注册表中的进程继续执行
- **Convergent loop 未终止** — reviewing/qa_testing 的循环逻辑未检查 DB 中的 story 状态
- **Approval 未自动清理** — story 变成 done 后，残留的 pending approval 无人消费

## 影响
- 中等 — 仅在手动干预 DB 时触发，正常流程不受影响
- 但如果 TUI 提供了 "force done" 功能，也需要同样的清理逻辑

## 建议修复方案
1. **添加 `force_complete(story_id)` API** — 一个操作同时：
   - 提交 `done` transition 到 TransitionQueue
   - Cancel 所有 running tasks（kill PID）
   - 消费所有 pending approvals（标记 abandon）
   - 清理 worktree（如果存在）
2. **在 dispatch 入口检查 story 状态** — 如果 story 已 done，跳过 dispatch 并 log warning
3. **定期 DB/内存状态一致性检查** — recovery 模块在 reconciliation 时比较 DB 和内存状态

## 临时处理
已手动清理：
- `UPDATE approvals SET decision='abandon' WHERE approval_id='7a8698dc-...'`
- 2-7 无 running tasks（自然超时退出）

## 相关日志
```
2026-04-01T11:59:20 [error/recovery_convergent_loop_error] story=2-7 phase=qa_testing
2026-04-01T11:59:20 [approval_created] approval_type=crash_recovery story=2-7
2026-04-01T11:59:20 [recovery_dispatch_failed_marked] story=2-7 phase=qa_testing
FileNotFoundError: [Errno 2] No such file or directory: '.worktrees/2-7-strategy-seed-generation'
```

## 关联 Bug
与之前发现的 "regression 失败后回退到 fixing，但 worktree 已被 merge 清理" bug 相关联。
