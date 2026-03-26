# Story 4.3: UAT 与 Interactive Session 完成检测

Status: ready-for-dev

## Story

As a 操作者,
I want 在 UAT 阶段提交测试结果，通过 `ato submit` 标记 Interactive Session 完成,
So that 人工测试结果纳入流水线，开发协作有明确的完成信号。

## Acceptance Criteria (AC)

### AC1: UAT 结果提交 (FR21)

```gherkin
Given story 进入 UAT 阶段
When 操作者在 worktree 中手动测试完成
Then 通过 `ato uat <story-id> --result pass` 或 `--result fail --reason "描述"` 提交结果
And 通过 → story 进入 merge 阶段（uat_pass 事件）
And 不通过 → story 退回 fix 阶段重新进入 Convergent Loop（FR48, uat_fail 事件）
```

### AC2: Interactive Session 完成标记 (已实现，需验证集成)

```gherkin
Given story 处于 `developing` 阶段（Interactive Session）
When 操作者完成开发协作
Then 运行 `ato submit <story-id>` 标记完成
And 验证 story 存在且处于 `developing` 状态，验证 worktree 有 commit
And 更新 SQLite story 状态，通过 nudge 通知 Orchestrator 触发 `dev_done` 事件
```

### AC3: Interactive Session 恢复策略 (FR23，已实现，需验证)

```gherkin
Given 操作者需要选择 Interactive Session 恢复策略
When 系统崩溃后 Interactive Session 需要人工决策
Then 提供三个选项：重新启动 / 从上次 session 续接（--resume）/ 放弃
And 选项通过 approval 机制呈现
```

## Tasks / Subtasks

- [ ] Task 1: 添加 `uat_fail` 状态转换 (AC: #1)
  - [ ] 1.1 在 `state_machine.py` 添加 `uat_fail = uat.to(fixing)` 转换
  - [ ] 1.2 更新 `CANONICAL_PHASE_MAP` 中 uat 的 fail 分支映射: `"uat": ("merging", "fixing")`
  - [ ] 1.3 更新 `_PHASE_SUCCESS_EVENT` 在 recovery.py 中确认 uat 映射正确
  - [ ] 1.4 编写 `uat_fail` 转换单元测试（正向 + 非法状态拒绝）

- [ ] Task 2: 实现 `ato uat` CLI 命令 (AC: #1)
  - [ ] 2.1 在 `cli.py` 添加 `@app.command("uat")` 命令，参数: `story_id`, `--result pass|fail`, `--reason`
  - [ ] 2.2 实现 `_uat_async()` 异步逻辑：验证 story 在 uat 阶段、存储结果、触发事件
  - [ ] 2.3 pass 路径: 创建 TransitionEvent(event_name="uat_pass")，通过 nudge 通知 Orchestrator
  - [ ] 2.4 fail 路径: 创建 TransitionEvent(event_name="uat_fail")，携带 reason，通过 nudge 通知
  - [ ] 2.5 UAT 结果持久化：写入 tasks 表的 context_briefing（复用 ContextBriefing 结构）或 approval payload

- [ ] Task 3: Orchestrator 消费 `uat_fail` 事件 (AC: #1)
  - [ ] 3.1 在 `core.py` 的 `_detect_completed_interactive_tasks` 或 approval 消费路径中处理 uat_fail
  - [ ] 3.2 uat_fail → 转回 fixing 阶段，使 story 重新进入 Convergent Loop
  - [ ] 3.3 验证重入 CL 后 review → fix → re-review 流程正常运行

- [ ] Task 4: 验证 `ato submit` + `ato uat` 端到端集成 (AC: #1, #2, #3)
  - [ ] 4.1 `ato submit` 在 developing 阶段的完整流程测试（已有代码，验证与 4.1 approval 基础设施集成）
  - [ ] 4.2 `ato uat --result pass` 端到端：uat → merging 状态转换验证
  - [ ] 4.3 `ato uat --result fail` 端到端：uat → fixing 状态转换 + CL 重入验证
  - [ ] 4.4 崩溃恢复场景：uat 阶段 crash → needs_human approval → restart/resume/abandon 三选项验证

- [ ] Task 5: 测试覆盖 (AC: #1, #2, #3)
  - [ ] 5.1 `ato uat` 命令单元测试：pass/fail 两路径、参数验证、错误处理
  - [ ] 5.2 `uat_fail` 状态机转换测试：合法/非法转换
  - [ ] 5.3 Orchestrator uat_fail 消费测试
  - [ ] 5.4 集成测试：uat fail → fixing → reviewing 完整 Convergent Loop 回退路径

## Dev Notes

### 实现范围精确界定

本 story 有两个核心交付和两个验证交付：

**核心交付（需新写代码）：**
1. **`ato uat` CLI 命令** — 全新命令，不存在于现有代码
2. **`uat_fail` 状态转换** — 状态机当前只有 `uat_pass = uat.to(merging)`，缺少 fail 路径

**验证交付（代码已存在，需确认集成正确性）：**
3. **`ato submit` 命令** — 已在 Story 2b-6 实现（cli.py:908-1117），本 story 仅验证与 4.1 审批基础设施的端到端集成
4. **Interactive Session 恢复策略** — 已在 Story 5.1a 实现（recovery.py:795-824 + core.py:840-873），本 story 仅验证 uat 阶段的恢复流程

### 架构约束与模式遵循

**CLI 命令模式（必须遵循 cli.py 现有模式）：**
```python
# 参考 submit_cmd (cli.py:908) 和 approve_cmd (cli.py:1300) 的模式
@app.command("uat")
def uat_cmd(
    story_id: str = typer.Argument(..., help="Story ID"),
    result: str = typer.Option(..., "--result", help="pass 或 fail"),
    reason: str = typer.Option("", "--reason", help="失败原因描述"),
    db_path: Path | None = typer.Option(None, "--db-path", help="SQLite 数据库路径"),
) -> None:
    """提交 UAT 测试结果。"""
    ...
    asyncio.run(_uat_async(story_id, result, reason, resolved_db))
```

**关键实现逻辑（`_uat_async`）：**
1. 用 `get_connection(db_path)` 获取 DB 连接
2. 用 `get_story(db, story_id)` 验证 story 存在
3. 验证 `story.current_phase == "uat"`（不要用 interactive_phases 集合，UAT 有自己的 phase 判断）
4. 结果持久化：写入 tasks 表 context_briefing（或独立字段），携带 result + reason + timestamp
5. pass: 更新 task 为 completed，发送 nudge → Orchestrator 检测完成 → 触发 `uat_pass`
6. fail: 直接提交 `uat_fail` TransitionEvent 到 DB（类似 `ato approve` 的事件触发模式），发送 nudge

**状态机修改（state_machine.py:152 附近）：**
```python
# 现有
uat_pass = uat.to(merging)
# 新增
uat_fail = uat.to(fixing)  # FR48: UAT 失败退回 fix 阶段
```

**CANONICAL_PHASE_MAP 修改（state_machine.py:88）：**
```python
# 现有
"uat": ("merging", None),
# 修改为
"uat": ("merging", "fixing"),  # (success_target, fail_target)
```

**`ato uat --result fail` 的事件路由：**
- fail 路径不走 `_detect_completed_interactive_tasks`（那是 submit 完成检测）
- 直接通过 CLI 写入 TransitionEvent 到 transition_events 表或通过 nudge 触发
- 参考 `ato approve` 的事件派发模式（core.py:840-873）

### SQLite 操作规范

- `PRAGMA busy_timeout=5000` 在每个连接
- `PRAGMA journal_mode=WAL` 已在 init_db 确认
- 写事务尽量短：读数据 → 处理 → 单次写 + commit
- 使用 `get_connection()` 短连接模式（CLI 场景）
- 参数化查询，禁止 SQL 拼接

### CLI 输出规范

- 错误输出到 stderr: `typer.echo(msg, err=True)`
- 使用"发生了什么 + 你的选项"格式
- 退出码: 0 成功 / 1 一般错误 / 2 环境错误
- 成功输出示例: `✅ Story 'story-007' UAT 通过，进入 merge 阶段。`
- 失败输出示例: `✅ Story 'story-007' UAT 未通过，退回 fix 阶段重新进入质量门控。原因: {reason}`

### Nudge 通知机制

- UAT 结果提交后需通知 Orchestrator 立即轮询
- 使用 `_send_nudge_safe(pid_path)` 函数（cli.py 中已有）
- Orchestrator 接收 nudge 后在下一轮 `_poll_cycle` 中检测变更
- 安全网：即使 nudge 丢失，2-5 秒定期轮询兜底

### Project Structure Notes

**需要修改的文件：**
| 文件 | 修改内容 |
|------|---------|
| `src/ato/state_machine.py:152` | 添加 `uat_fail = uat.to(fixing)` |
| `src/ato/state_machine.py:88` | 更新 `"uat": ("merging", "fixing")` |
| `src/ato/cli.py` | 新增 `ato uat` 命令（在 submit_cmd 之后） |
| `src/ato/core.py:347` | 确认 phase_success_event 映射正确（可能需新增 uat_fail 处理） |

**需要新增的测试文件：**
| 文件 | 测试内容 |
|------|---------|
| `tests/unit/test_cli_uat.py` | `ato uat` 命令单元测试 |
| `tests/unit/test_state_machine.py` | 补充 `uat_fail` 转换测试（在已有文件中追加） |

**不需要修改的文件（已有实现）：**
- `src/ato/subprocess_mgr.py` — dispatch_interactive 已完整
- `src/ato/recovery.py` — needs_human 分类和 approval 创建已完整
- `src/ato/approval_helpers.py` — 统一创建 API 已完整
- `src/ato/nudge.py` — 通知机制已完整

### References

- [Source: _bmad-output/planning-artifacts/epics.md — Epic 4, Story 4.3 验收标准]
- [Source: _bmad-output/planning-artifacts/architecture.md — Decision 4: Interactive Session Completion Detection]
- [Source: _bmad-output/planning-artifacts/architecture.md — Decision 2: TUI Bidirectional Communication]
- [Source: _bmad-output/planning-artifacts/prd.md — FR21: UAT 结果提交, FR48: UAT 失败退回 fix, FR23: Session 恢复策略]
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md — UAT CLI 命令, 反馈模式, 错误输出格式]
- [Source: src/ato/cli.py:908-1117 — 现有 `ato submit` 实现]
- [Source: src/ato/state_machine.py:100-169 — StoryLifecycle 状态机定义]
- [Source: src/ato/core.py:82-187 — Interactive Session 超时/完成检测]
- [Source: src/ato/core.py:840-873 — Approval decision 处理 (session_timeout, crash_recovery)]
- [Source: src/ato/recovery.py:795-824 — _mark_needs_human 原子操作]
- [Source: _bmad-output/implementation-artifacts/4-1-approval-queue-nudge.md — Story 4.1 完整 dev notes]

### Story 4.1 关键经验（防止重复踩坑）

1. **Migration 幂等性:** SQLite 不支持 `ALTER TABLE ADD COLUMN IF NOT EXISTS`，使用 `PRAGMA table_info` 检测列是否存在
2. **Async/sync CLI 测试:** `CliRunner.invoke` 与 `asyncio.run()` 冲突，需用 sync test + async helpers 模式
3. **SAVEPOINT 事务:** `create_approval` 支持 `commit=False` 参数，在 SAVEPOINT 内使用
4. **Approval 创建统一入口:** 始终使用 `approval_helpers.create_approval()`，不要直接调 `insert_approval()`
5. **Prefix-based ID 匹配:** Approval ID 支持 ≥4 字符前缀匹配，减少用户摩擦
6. **DB 层消费标记:** 使用 `consumed_at` 字段实现跨重启安全的幂等消费

### Git Intelligence

最近 commit 模式：
- `959eb04` chore: fix type hints, variable shadowing, and simplify context managers
- `69f6dc4` Merge story 4.1: Approval Queue 与 Nudge 通知机制完整实现
- `e3a9bbb` feat: Story 4.1 Approval Queue 与 Nudge 通知机制完整实现

代码风格要点：
- commit message 格式: `feat: Story X.Y 描述` / `chore: 描述`
- 所有代码通过 ruff check + ruff format + mypy
- 测试命名: `test_<feature>_<scenario>`
- 异步测试使用 `pytest-asyncio`

### 技术栈版本确认

| 依赖 | 版本约束 | 备注 |
|------|---------|------|
| python-statemachine | ≥3.0 | async send() 支持 |
| typer | 已安装 | CLI 命令框架 |
| aiosqlite | 已安装 | 异步 SQLite |
| pydantic | ≥2.0 | model_validate / model_dump_json |
| structlog | 已安装 | 结构化日志 |

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List
