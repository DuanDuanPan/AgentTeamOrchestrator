# Story 10.1: Terminal Finalizer 与 Dead PID Watchdog

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->
<!-- Depends on: Story 2B.1 (SubprocessManager / cost_log), Story 5.1a (crash recovery), Sprint Change Proposal 2026-04-08 -->

## Story

As a 操作者,
I want CLI result 返回后的 task/cost/running 状态总能在有限时间内收敛,
so that worker PID 已退出或终态后处理卡住时，ATO 不会永久停在 running。

## Acceptance Criteria

### AC1: Adapter 返回后的终态 finalizer 有总超时

```gherkin
Given `SubprocessManager.dispatch()` 已收到 adapter 返回的 `AdapterResult`
When activity flush、task 状态落库或 cost_log 落库任一步卡住或超时
Then dispatch 不得永久挂起
And task 必须在 bounded terminal finalizer 内尽力收敛为 `completed` 或 `failed`
And `_unregister_running(task_id)` 必须在 outer `finally` 中执行
And semaphore slot 必须被释放，后续任务可继续调度
```

### AC2: activity flush 不阻塞终态落库

```gherkin
Given delayed activity flush task 未完成或 `_flush_latest_activity()` 抛异常
When adapter 已经返回业务结果
Then activity flush 失败只记录 warning
And 不阻止 `tasks.status`、`completed_at`、`exit_code`、`text_result` 和 cost 字段落库
```

### AC3: cost_log 正常写入失败时有最小 fallback

```gherkin
Given `insert_cost_log()` 或正常 `update_task_status()` 失败
When result 中仍有 task_id、story_id、phase、exit_code、cost_usd、text_result
Then fallback 至少保证 `tasks` 表从 `running` 收敛为终态
And fallback error 被写入 `error_message` 或 structlog
And 不在 fallback 路径中再次等待外部 IO
```

### AC4: 运行期 dead PID watchdog

```gherkin
Given `SubprocessManager.running` 中存在 PID
And 该 PID 已不存在
And DB 中 task 仍为 `running`
When watchdog poll 运行
Then 该 task 被标记为 `failed`、交给 recovery 分类，或创建可操作恢复入口
And structlog 记录 `dead_worker_detected`
And `_running` 中对应 PID 被注销
```

### AC5: 回归测试覆盖 2026-04-08 主故障

```gherkin
Given 模拟 Claude result 已返回但终态 DB helper 卡住
When 调用 `SubprocessManager.dispatch()`
Then 测试能证明 dispatch 有界退出
And DB 不再永久停留在 `running`
And `mgr.running` 为空
```

## Tasks / Subtasks

- [x] Task 1: 引入 terminal finalizer 边界 (AC: #1, #2, #3)
  - [x] 1.1 在 `src/ato/subprocess_mgr.py` 中抽出 `_finalize_success()` / `_finalize_failure()` 或等价 helper
  - [x] 1.2 用 `asyncio.timeout()` 包住 activity flush + task/cost 落库的终态流程
  - [x] 1.3 将 `_unregister_running(task_id)` 移到 adapter 调用外层 `finally`，不能依赖 DB 写入成功
  - [x] 1.4 确保 `delayed_flush_task` 的 cancel/await 也不会阻塞终态收敛

- [x] Task 2: 增加最小 raw SQL fallback (AC: #3)
  - [x] 2.1 在正常 DB helper 失败时，用短事务更新 `tasks.status`、`completed_at`、`exit_code`、`cost_usd`、`duration_ms`、`text_result` / `error_message`
  - [x] 2.2 fallback 不重复创建复杂 `CostLogRecord`，优先保证 task 不再 running
  - [x] 2.3 structlog 记录 fallback 使用原因与失败链路

- [x] Task 3: 增加运行期 dead PID watchdog (AC: #4)
  - [x] 3.1 在 `SubprocessManager` 中添加 `sweep_dead_workers()` 或等价 async 方法
  - [x] 3.2 复用 recovery 的 PID 检测模式；不要重复写不一致的 `os.kill(pid, 0)` 语义
  - [x] 3.3 在 Orchestrator poll cycle 或 dispatch manager 驱动点调用 watchdog
  - [x] 3.4 dead PID 处理必须避免把仍存活但权限不足的 PID 误判为 dead

- [x] Task 4: 测试与验证 (AC: #1-#5)
  - [x] 4.1 更新 `tests/unit/test_subprocess_mgr.py`：activity flush hang、DB helper timeout、fallback 落库、finally 注销
  - [x] 4.2 更新 `tests/unit/test_subprocess_mgr.py`：dead PID sweep（marks failed、skips alive、handles EPERM）
  - [x] 4.3 验证原有 retry、cost_log、progress callback 测试仍通过（28/28 passed）

## Dev Notes

### Root Cause Context

- `docs/root-cause-analysis-2026-04-08.md` 将主根因收敛为：CLI result 返回后的终态收敛边界不可卡死性不足。
- 当前 `src/ato/subprocess_mgr.py:350-388` 的失败路径和 `src/ato/subprocess_mgr.py:390-446` 的成功路径，都在 `_unregister_running()` 前 await activity flush / DB 连接 / task 更新 / cost_log 写入。
- `src/ato/models/db.py` 的 `get_connection()` 是独立 `aiosqlite` 连接，不是连接池；不要把本 story 实现成“调连接池参数”。

### Implementation Guardrails

- 不要在 fallback 中做复杂业务恢复；本 story 的优先级是“不要永久 running”。
- 不要吞掉 `CancelledError`；取消应继续向外传播，但仍要走 `_unregister_running()`。
- 不要把 activity flush 作为 task completed/failed 的前置硬依赖。
- 不要在 SQLite 写事务中 await 外部 IO。
- 保持 `SubprocessManager` 和 `RecoveryEngine` 边界清晰：watchdog 可以调用 recovery 分类 helper 或创建可恢复状态，但不要复制完整 recovery engine。

### Project Structure Notes

- 主要修改：`src/ato/subprocess_mgr.py`，必要时少量触碰 `src/ato/core.py` 和 `src/ato/recovery.py`。
- 测试优先放在 `tests/unit/test_subprocess_mgr.py`；跨 DB 状态验证可放 `tests/integration/test_crash_recovery.py`。

### Suggested Verification

```bash
uv run pytest tests/unit/test_subprocess_mgr.py -v
uv run pytest tests/integration/test_crash_recovery.py -v
uv run ruff check src/ato/subprocess_mgr.py tests/unit/test_subprocess_mgr.py tests/integration/test_crash_recovery.py
uv run mypy src/ato
```

## References

- [Source: docs/root-cause-analysis-2026-04-08.md — BUG-001]
- [Source: docs/monitoring-log-2026-04-08.md — 11:55 CST stuck evidence]
- [Source: _bmad-output/planning-artifacts/sprint-change-proposal-2026-04-08.md — Story 10.1]
- [Source: _bmad-output/planning-artifacts/architecture.md — Decision 7, Asyncio Subprocess 模式]
- [Source: _bmad-output/implementation-artifacts/2b-1-claude-agent-dispatch.md]
- [Source: _bmad-output/implementation-artifacts/5-1a-crash-recovery-auto-resume.md]
- [Source: src/ato/subprocess_mgr.py]
- [Source: src/ato/recovery.py]

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6

### Debug Log References

### Completion Notes List

- 重构 `dispatch()` 终态路径：将 activity flush + DB 写入包裹在 `asyncio.timeout(_TERMINAL_FINALIZER_TIMEOUT)` 内
- 抽取 `_finalize_success()` / `_finalize_failure()` helper，终态 DB 逻辑清晰分离
- `_unregister_running(task_id)` 移至 outer `finally`，不再依赖 DB 写入成功
- 新增 `_fallback_update_task()` — 当正常 DB helper 失败时，用 raw SQL 短事务保证 task 从 running 收敛
- activity flush 用 `asyncio.wait_for(..., timeout=_ACTIVITY_FLUSH_TIMEOUT)` 包裹，失败只记 warning
- 新增 `sweep_dead_workers()` — 运行期 dead PID watchdog，复用 `_is_pid_alive` 的 ESRCH/EPERM 语义
- 新增 `_is_pid_alive()` 静态方法，与 recovery.py 的 `_is_pid_alive` 保持语义一致
- 8 个新测试（5 terminal finalizer + 3 dead PID watchdog），28 个总测试全部通过
- ruff 和 mypy strict 全部通过

### Change Log

- 2026-04-08: Story 10.1 完成 — Terminal Finalizer 与 Dead PID Watchdog

### File List

- src/ato/subprocess_mgr.py (modified)
- tests/unit/test_subprocess_mgr.py (modified)
