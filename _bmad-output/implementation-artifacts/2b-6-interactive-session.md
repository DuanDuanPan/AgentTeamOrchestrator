# Story 2B.6: 操作者可启动 Interactive Session 并通过 ato submit 完成

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a 操作者,
I want 在 Interactive Session 中与 agent 直接协作，完成后通过 `ato submit` 标记完成,
So that 复杂任务可以人机协作解决。

## Acceptance Criteria

1. **AC1: Interactive Session 启动与注册** (FR10)
   - Given story 需要 Interactive Session
   - When 系统启动 Interactive Session
   - Then 在独立终端窗口中启动
   - And 在 `tasks` 表登记 interactive task 的 `pid` 与 `started_at`
   - And 复用 `stories.worktree_path` 作为该 session 的 worktree 路径来源（不新增 `tasks.worktree_path` 列）
   - And 写入 `.ato/sessions/{story_id}.json` sidecar 元数据，至少包含 `pid`、`started_at`、`base_commit`，以及可选的 `session_id`

2. **AC2: ato submit 完成检测** (Decision 4)
   - Given 操作者在 Interactive Session 中完成工作
   - When 运行 `ato submit <story-id>`
   - Then 验证 story 存在且 `current_phase` 对应配置中的 `interactive_session` phase（而非硬编码某个 phase 名）
   - And 验证 sidecar 元数据中的 `base_commit` 之后 worktree 有新 commit
   - And 提取并验证 Context Briefing
   - And 仅更新 SQLite task 记录并触发 nudge 通知 Orchestrator
   - And 由 Orchestrator 在下一轮轮询中根据当前 phase 派生正确的 success event（如 `uat_pass` 或 `dev_done`）

3. **AC3: 超时处理与恢复策略** (FR23)
   - Given Interactive Session 超时（7200 秒）
   - When 超时触发
   - Then 创建 approval 请求操作者选择恢复策略（重新启动 / 续接 / 放弃）

4. **AC4: Context Briefing 提取** (FR53)
   - Given agent task 完成
   - When 提取 Context Briefing
   - Then 结构化工作记忆摘要包含：story_id, phase, task_type, artifacts_produced, key_decisions, agent_notes, created_at
   - And `task_type` 使用当前交互 phase 的语义标签（如 `developing` / `uat`），不限制为固定四个值
   - And 经 `ContextBriefing.model_validate()` 验证后，以 JSON 序列化形式存入 tasks 表 `context_briefing` 列

5. **AC5: Agent Session 管理** (FR9)
   - Given Convergent Loop 内需要短循环 session resume
   - When 系统需要续接或 fork session
   - Then 在已有 `session_id` 时使用 `--resume <session_id>` 续接
   - And 在无可用 `session_id` 时降级为 fresh session / fork，不得伪造 resume 参数

## Tasks / Subtasks

- [x] Task 1: ContextBriefing Pydantic 模型 (AC: #4)
  - [x] 1.1 在 `schemas.py` 添加 `ContextBriefing(_StrictBase)` 模型
  - [x] 1.2 添加 `created_at: datetime` 字段（存储时自动填充）
  - [x] 1.3 编写单元测试验证 model_validate() 和序列化

- [x] Task 2: Interactive Session 启动机制 (AC: #1)
  - [x] 2.1 在 `subprocess_mgr.py` 添加 `dispatch_interactive()` 方法
  - [x] 2.2 使用 `open` (macOS) 或 platform-aware 方式在新终端窗口启动
  - [x] 2.3 在 adapter 层暴露/复用 interactive argv builder，生成 `claude -p <prompt> [--resume <session_id>]` 命令（OAuth 模式）；`subprocess_mgr.py` 只负责终端启动包装，不直接拼 CLI flags
  - [x] 2.4 注册 PID + started_at 到 tasks 表，并写入 `.ato/sessions/{story_id}.json` sidecar 元数据（`pid` / `started_at` / `base_commit` / `session_id?`）
  - [x] 2.5 不新增 `tasks.phase_type`、`tasks.worktree_path` 或 `task_type` 列；interactive/structured 区分来自 `PhaseDefinition.phase_type` 和既有 `stories.worktree_path`
  - [x] 2.6 编写单元测试（mock subprocess + DB 验证）

- [x] Task 3: Worktree 新 commit 检测 (AC: #2)
  - [x] 3.1 在 `worktree_mgr.py` 添加 `has_new_commits(worktree_path, since_rev)` 方法
  - [x] 3.2 使用 `git log <since_rev>..HEAD --oneline` 检测 worktree 中的新提交，其中 `since_rev` 来自 session sidecar 的 `base_commit`
  - [x] 3.3 为 sidecar 元数据读取与 commit 检测编写单元测试（mock git subprocess）

- [x] Task 4: `ato submit` CLI 命令 (AC: #2, #4)
  - [x] 4.1 在 `cli.py` 添加 `submit` 命令
  - [x] 4.2 验证 story 存在，且 `story.current_phase` 属于配置中的 `interactive_session` phases（默认 example config 为 `uat`，不要硬编码 `developing`）
  - [x] 4.3 读取 `.ato/sessions/{story_id}.json`，调用 `WorktreeManager.has_new_commits(worktree_path, base_commit)` 验证有实际工作
  - [x] 4.4 构造 ContextBriefing 并 model_validate()
  - [x] 4.5 更新当前 interactive task 的 `status="completed"`、`context_briefing=briefing.model_dump_json()`、`completed_at`
  - [x] 4.6 不在 CLI 进程内直接调用 `TransitionQueue`；只执行 SQLite 写入 + `send_external_nudge(orchestrator_pid)`
  - [x] 4.7 编写 CLI 单元测试，覆盖 Orchestrator 运行/未运行两条路径
  - [x] 4.8 编写 CLI 单元测试，覆盖 `--briefing-file` / 交互式输入两条路径

- [x] Task 5: 超时监控与 Approval 创建 (AC: #3)
  - [x] 5.1 在 `core.py` 的 `_poll_cycle()` 中添加 interactive session 超时检测，并根据 `settings.phases` 识别 interactive phases（不读取不存在的 `task.phase_type`）；补充 `db.py` 的 `get_tasks_by_status()` 查询 helper
  - [x] 5.2 超时后创建 approval（type=`session_timeout`，payload 为 JSON 字符串，包含 `task_id`、`elapsed_seconds`、`options`、`recommended_action`）
  - [x] 5.3 `_poll_cycle()` 检测已由 `ato submit` 标记完成的 interactive task，并在 Orchestrator 进程内提交 phase-aware 的 success `TransitionEvent`
  - [x] 5.4 Approval 决策处理：根据操作者选择执行对应恢复策略
  - [x] 5.5 编写单元测试

- [x] Task 6: Session 续接支持 (AC: #5)
  - [x] 6.1 dispatch_interactive() 支持 `session_id` 参数用于 `--resume`
  - [x] 6.2 续接优先读取显式传入的 `session_id` 或 sidecar 元数据中的 `session_id`；若无值则降级为 fresh session / fork
  - [x] 6.3 编写续接场景的单元测试

## Dev Notes

### 核心架构约束

- **OAuth 模式**：必须使用 `claude -p`（OAuth），不能用 `--bare`（需要 ANTHROPIC_API_KEY）
- **适配器隔离**（ADR-08）：CLI 参数构建 100% 封装在 adapter 层，orchestrator core 不直接操作 CLI 参数
- **TransitionQueue 串行**：所有状态转换通过 TransitionQueue 串行处理，不能绕过
- **外部 writer 路径**（Decision 2）：`ato submit` / TUI 只能走 `SQLite write + nudge`；跨进程不能直接调用 Orchestrator 内存中的 `TransitionQueue`
- **SQLite WAL**：零数据丢失，`PRAGMA busy_timeout=5000` 处理并发
- **三阶段清理协议**：SIGTERM → wait(5s) → SIGKILL → wait — 所有 subprocess 必须遵守

### Interactive Session 与 Structured Job 的关键区别

| 维度 | Structured Job | Interactive Session |
|------|---------------|-------------------|
| 执行方式 | SubprocessManager.dispatch() 阻塞等待 | dispatch_interactive() 非阻塞，返回 PID |
| 完成检测 | 进程退出 → AdapterResult | `ato submit` CLI 命令 / TUI 手动标记 |
| 超时处理 | 1800 秒 → 进程 kill → 重试 | 7200 秒 → approval 请求 → 人工决策 |
| 输出收集 | stdout JSON 解析 | Context Briefing 手动/半自动提取 |
| 崩溃恢复 | PID 不存活 → 自动重调度 | PID 不存活 → needs_human，等待操作者决策 |

### 独立终端窗口启动方式

macOS 平台使用 `open -a Terminal <script>` 或 `osascript` 启动新终端窗口：
```python
# macOS: 使用 open 命令在新 Terminal 窗口启动
# 1. 写一个临时 shell script 到 worktree 路径
# 2. open -a Terminal script_path
# 或使用 osascript:
# osascript -e 'tell app "Terminal" to do script "cd /path && claude -p ..."'
```
**注意**：`open` 命令立即返回，实际 claude 进程的 PID 不能直接从 launcher 进程得到。推荐方案：启动脚本写入 `.ato/sessions/<story-id>.json` sidecar，至少包含：

- `pid`: 实际 interactive CLI 进程 PID
- `started_at`: ISO 时间戳
- `base_commit`: 启动 session 前 worktree 的 HEAD commit
- `session_id`: 若本次为 resume 或后续可获取，则写入；未知时允许为 `null`

`dispatch_interactive()` 等待 sidecar 出现后读取 PID/元数据并注册到 `tasks` 表。

### ato submit 命令规范

```
用法: ato submit <story-id> [--db-path PATH]

验证流程:
1. 读取 .ato/orchestrator.pid 获取 Orchestrator PID（用于 nudge）
2. get_story(db, story_id) → 确认存在
3. 通过 `load_config()` + `build_phase_definitions()` 构建 interactive phase 集合；`story.current_phase` 必须属于该集合
4. 读取 `.ato/sessions/{story_id}.json`，拿到 `base_commit`
5. WorktreeManager.has_new_commits(story_id, base_commit) → 确认有实际工作
6. 构造 ContextBriefing（可使用 --briefing-file 外部文件或交互式输入）
7. update_task_status(db, task_id, "completed", context_briefing=briefing_json, completed_at=...)
8. send_external_nudge(orchestrator_pid)
9. 由 Orchestrator `_poll_cycle()` 检测该 completed interactive task，并在进程内派生/提交正确的 success event

错误处理:
- story 不存在 → typer.echo("Story not found", err=True) + Exit(1)
- 当前 phase 非 interactive_session → typer.echo("Story not in interactive session phase", err=True) + Exit(1)
- 无新 commit → typer.echo("No commits found in worktree", err=True) + Exit(1)
- Orchestrator 未运行 → 跳过 nudge，仅更新 DB（下次启动时自动检测）
```

### Context Briefing 提取策略

Interactive Session 的 Context Briefing 不像 Structured Job 可以从 stdout JSON 自动提取。推荐混合策略：

1. **半自动提取**：`ato submit` 命令接受 `--briefing-file <path>` 参数，读取操作者/agent 在 session 中生成的摘要文件
2. **交互式输入**：无 `--briefing-file` 时，通过 `typer.prompt()` 请求操作者输入关键字段
3. **最小默认值**：`artifacts_produced` 从 worktree git diff 自动提取变更文件列表，`task_type` 使用当前 phase 名，`key_decisions` 和 `agent_notes` 可为空列表/空字符串

### 外部 writer → Orchestrator 交接

- `ato submit` 只负责把 interactive task 标记为 completed，并写入 `context_briefing`
- Orchestrator `_poll_cycle()` 负责查询“当前 story.phase 仍停留在 interactive phase、但对应 task 已 completed”的记录
- 检测到后，由 Orchestrator 在本进程内创建 `TransitionEvent`
- success event 必须按 phase 派生，不要硬编码：
  - `uat` → `uat_pass`
  - `developing` → `dev_done`
  - 其他 phase 若未来允许 interactive_session，必须显式补上映射后再实现

### 超时监控集成点

在 `core.py` 的 `_poll_cycle()` 中检测：
```python
# 在每次轮询时检查 interactive session 超时
interactive_phases = {
    phase.name
    for phase in build_phase_definitions(self._settings)
    if phase.phase_type == "interactive_session"
}

tasks = await get_tasks_by_status(db, "running")
for task in tasks:
    if task.phase in interactive_phases and task.started_at is not None:
        elapsed = (now - task.started_at).total_seconds()
        if elapsed > self._settings.timeout.interactive_session:
            approval = ApprovalRecord(
                approval_id=str(uuid.uuid4()),
                story_id=task.story_id,
                approval_type="session_timeout",
                status="pending",
                payload=json.dumps(
                    {
                        "task_id": task.task_id,
                        "elapsed_seconds": elapsed,
                        "options": ["restart", "resume", "abandon"],
                        "recommended_action": "restart",
                    }
                ),
                created_at=now,
            )
            await insert_approval(db, approval)
```

### 需要注意的代码复用

- **不要重新实现** cleanup_process() — 使用 `adapters/base.py` 中的现有实现
- **不要重新实现** PID 注册 — 使用 SubprocessManager 的 `_running` dict 和 `on_process_start` 回调
- **不要重新实现** nudge — 使用 `nudge.py` 中的 `send_external_nudge()`
- **不要重新实现** DB 操作 — 使用 `db.py` 中的 `insert_task()`, `update_task_status()`, `insert_approval()`
- **不要重新实现** worktree — 使用 `worktree_mgr.py` 中的 `WorktreeManager`
- **不要** 在 CLI 进程中直接调用 `TransitionQueue` — 外部 writer 只能 SQLite + nudge
- **不要** 在 `subprocess_mgr.py` / `core.py` 直接拼 `claude` flags — 复用 adapter helper
- **复用** `TransitionEvent` 模型，但只能由 Orchestrator 进程内提交 phase-aware 的 success event

### Project Structure Notes

- 所有新代码在 `src/ato/` 目录下
- **ContextBriefing** 模型添加到 `src/ato/models/schemas.py`（继承 `_StrictBase`，遵循现有模式）
- **interactive argv builder** 需要在 `src/ato/adapters/claude_cli.py` 暴露或复用，保持 adapter isolation
- **dispatch_interactive()** 添加到 `src/ato/subprocess_mgr.py`（SubprocessManager 方法）
- **has_new_commits()** 添加到 `src/ato/worktree_mgr.py`（WorktreeManager 方法）
- **submit** 命令添加到 `src/ato/cli.py`（与 init/batch/start/stop 同级）
- **超时检测** 集成到 `src/ato/core.py` 的 `_poll_cycle()`
- **session sidecar** 使用 `.ato/sessions/{story_id}.json`，避免为 interactive metadata 新增 DB 列
- 测试文件: `tests/unit/test_interactive_session.py`（新建）, `tests/unit/test_cli_submit.py`（新建）
- **SCHEMA_VERSION** 应保持不变（沿用现有 `tasks.context_briefing` 与 `stories.worktree_path`；interactive metadata 走 sidecar，无需迁移）

### 现有代码接口速查

```python
# schemas.py — 基类
class _StrictBase(BaseModel, strict=True, extra="forbid"): ...

# schemas.py — 任务记录（已有 context_briefing: str | None 字段）
class TaskRecord(_StrictBase):
    task_id: str; story_id: str; phase: str; role: str
    cli_tool: Literal["claude", "codex"]
    status: TaskStatus  # "pending"|"running"|"paused"|"completed"|"failed"
    pid: int | None = None
    context_briefing: str | None = None  # ← JSON 序列化的 ContextBriefing
    ...

# schemas.py — 审批记录
class ApprovalRecord(_StrictBase):
    approval_id: str; story_id: str; approval_type: str
    status: Literal["pending", "approved", "rejected"]
    payload: str | None = None  # ← JSON 字符串，不是 dict
    created_at: datetime

# schemas.py — 转换事件
class TransitionEvent(_StrictBase):
    story_id: str; event_name: str
    source: Literal["agent", "tui", "cli"]
    submitted_at: datetime

# config.py — phase 类型判定
class PhaseDefinition:
    name: str
    phase_type: str  # "structured_job" | "convergent_loop" | "interactive_session"

# db.py — 核心 CRUD
async def insert_task(db, task: TaskRecord) -> None
async def update_task_status(db, task_id: str, status: str, **kwargs) -> None
async def get_story(db, story_id: str) -> StoryRecord | None
async def get_tasks_by_status(db, status: str) -> list[TaskRecord]
async def insert_approval(db, approval: ApprovalRecord) -> None

# nudge.py — 进程外通知
def send_external_nudge(orchestrator_pid: int) -> None  # os.kill(pid, SIGUSR1)

# config.py — 超时配置
timeout.interactive_session: int = 7200  # 秒

# subprocess_mgr.py — 现有调度
class SubprocessManager:
    async def dispatch(story_id, phase, role, cli_tool, prompt, ...) -> AdapterResult
    async def dispatch_with_retry(...) -> AdapterResult

# worktree_mgr.py — 现有管理
class WorktreeManager:
    async def create(story_id, branch_name=None, base_ref="HEAD") -> Path
    async def cleanup(story_id) -> None
    async def has_new_commits(story_id, since_rev) -> bool
```

### 编码约定

- **Pydantic**: 继承 `_StrictBase` (strict=True, extra="forbid")
- **CRUD**: `async def xxx(db: aiosqlite.Connection, ...)`, 使用 `_dt_to_iso()/_iso_to_dt()`
- **CLI**: `typer.Typer()` 子应用, `typer.Exit(code=N)` (非 sys.exit())
- **错误输出**: `typer.echo(msg, err=True)` 输出到 stderr
- **日志**: `structlog.get_logger()`, snake_case 事件名
- **subprocess**: `asyncio.create_subprocess_exec()` (禁止 shell=True)
- **清理**: `try/finally` + `cleanup_process(proc)`

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 2B.6] — 完整 AC 与 Context Briefing schema
- [Source: _bmad-output/planning-artifacts/architecture.md#Decision 4] — Interactive Session 双通道完成检测
- [Source: _bmad-output/planning-artifacts/architecture.md#Decision 2] — TUI↔Orchestrator 通信模型（nudge 机制）
- [Source: _bmad-output/planning-artifacts/prd.md#FR10] — Interactive Session 启动与注册
- [Source: _bmad-output/planning-artifacts/prd.md#FR23] — Interactive Session 恢复策略
- [Source: _bmad-output/planning-artifacts/prd.md#FR53] — Context Briefing 提取
- [Source: _bmad-output/planning-artifacts/prd.md#FR9] — Agent session 管理
- [Source: ato.yaml.example] — 默认 `uat` phase 配置为 `interactive_session`
- [Source: src/ato/state_machine.py] — `current_phase` / success event 语义（`uat_pass`, `dev_done`）
- [Source: _bmad-output/implementation-artifacts/2b-1-claude-agent-dispatch.md] — 适配器模式、PID 注册、三阶段清理
- [Source: _bmad-output/implementation-artifacts/2b-4-worktree-isolation.md] — Worktree 路径约定、幂等性、分支元数据
- [Source: _bmad-output/implementation-artifacts/2a-2-serial-transition-queue.md] — 外部 writer 使用 SQLite + nudge，而非跨进程直接 submit queue
- [Source: _bmad-output/implementation-artifacts/2b-5-batch-select-status.md] — CLI 模式、Pydantic 约定、DB 事务

### Previous Story Intelligence

**从 Story 2B.5 学到的关键模式：**
- CLI 子命令使用 `typer.Typer()` sub-app 模式
- 默认交互式 + `--xxx` 参数支持非交互式
- 事务边界使用 `commit=False` + 调用方 `await db.commit()` 统一提交
- schema migration 使用 `@_register(N)` 装饰器

**从 Story 2B.4 学到的关键模式：**
- Worktree 路径: `.worktrees/{story_id}/`
- 分支命名: `worktree-story-{story_id}`
- 幂等性检查: 路径存在 → 补写 DB → 返回（不报错）
- git 命令: `asyncio.create_subprocess_exec()` + 30s timeout

**从 Story 2B.1 学到的关键模式：**
- AdapterResult 统一输出模型
- ErrorCategory 错误分类 → retryable 判断
- cost_log 每次调用独立记录（一个 task 可有多条 cost_log）
- PID 注册通过 `on_process_start` 回调（进程启动后立即注册）

### Git Intelligence

最近 5 次提交聚焦于 Epic 2B 和 3.1：
- `1555909` Merge story 2B.3: BMAD skill parsing adapter
- `91eb8da` feat: Story 2B.3 BMAD skill parsing adapter
- `66ad353` Merge story 2B.4: Worktree isolation
- `af8680a` feat: Story 2B.4 Worktree isolation
- `7ba6e7f` feat: Story 3.1 Deterministic validation + finding tracking

所有 Epic 2B 的前置 stories (2B.1-2B.5) 已完成或 review 中，基础设施就绪。

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6 (1M context)

### Debug Log References

- 无 debug 问题

### Completion Notes List

- ✅ Task 1: `ContextBriefing` Pydantic 模型添加到 `schemas.py`，含 `story_id`, `phase`, `task_type`, `artifacts_produced`, `key_decisions`, `agent_notes`, `created_at` 字段。6 个单元测试验证 model_validate()、序列化、strict 模式。
- ✅ Task 2: `dispatch_interactive()` 添加到 `SubprocessManager`，使用 `_launch_terminal_session()` 在新终端窗口启动 claude CLI。`build_interactive_command()` 函数在 `claude_cli.py` 暴露，保持 adapter 隔离。sidecar 元数据 `.ato/sessions/{story_id}.json` 写入 PID/started_at/base_commit/session_id。
- ✅ Task 3: `has_new_commits()` 添加到 `WorktreeManager`，使用 `git -C <path> log <since>..HEAD --oneline` 检测新提交。3 个单元测试覆盖成功/空/错误场景。
- ✅ Task 4: `ato submit` CLI 命令完整实现。验证 story 存在、phase 为 interactive_session（通过配置动态判断）、有新 commit、构造 ContextBriefing、更新 task 状态、发送 nudge。支持 `--briefing-file` 和交互式输入（自动提取 artifacts_produced）两种模式。6 个 CLI 单元测试。
- ✅ Task 5: `_check_interactive_timeouts()` 和 `_detect_completed_interactive_tasks()` 函数添加到 `core.py`。`_poll_cycle()` 集成超时检测和 completed task 检测。`get_tasks_by_status()` helper 添加到 `db.py`。3 个单元测试 + 2 个重复防护测试。
- ✅ Task 6: `dispatch_interactive()` 和 `build_interactive_command()` 均支持 `session_id` 参数用于 `--resume` 续接。无显式 session_id 时从已有 sidecar 读取 fallback；均无值则降级为 fresh session。5 个单元测试。
- ✅ Code Review Fix 1: `_detect_completed_interactive_tasks()` 增加 story.current_phase 校验 + task 消费标记（`expected_artifact='transition_submitted'`），防止重复 transition 派发。
- ✅ Code Review Fix 2: `_check_interactive_timeouts()` 增加已有 pending approval 查重，防止每次轮询重复创建 session_timeout approval。
- ✅ Code Review Fix 3: phase→event 映射改为显式 dict（`uat→uat_pass`, `developing→dev_done`），不再用 `f"{name}_pass"` 生成，防止非 uat phase 生成错误 event name。
- ✅ Code Review Fix 4: `dispatch_interactive()` 增加 sidecar session_id fallback 读取；`_launch_terminal_session()` 接受并保留 session_id 到 sidecar 文件。
- ✅ Code Review Fix 5: `ato submit` 交互输入分支调用 `_extract_changed_files()` 从 worktree git diff 提取变更文件列表，不再留空。

### File List

**新增文件：**
- `tests/unit/test_interactive_session.py` — Interactive Session 综合单元测试 (25 个测试)
- `tests/unit/test_cli_submit.py` — ato submit CLI 命令单元测试 (6 个测试)

**修改文件：**
- `src/ato/models/schemas.py` — 新增 `ContextBriefing` 模型
- `src/ato/adapters/claude_cli.py` — 新增 `build_interactive_command()` 函数
- `src/ato/subprocess_mgr.py` — 新增 `_launch_terminal_session()`, `dispatch_interactive()`, `_wait_for_sidecar()`
- `src/ato/worktree_mgr.py` — 新增 `has_new_commits()` 方法
- `src/ato/cli.py` — 新增 `submit` 命令、`_extract_changed_files()` 及相关辅助函数
- `src/ato/core.py` — 新增 `_check_interactive_timeouts()`, `_detect_completed_interactive_tasks()`；更新 `_poll_cycle()`
- `src/ato/models/db.py` — 新增 `get_tasks_by_status()` 查询函数
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — 状态更新

### Change Log

- 2026-03-25: Story 2B.6 Interactive Session 完整实现。新增 ContextBriefing 模型、dispatch_interactive 启动机制、has_new_commits 检测、ato submit CLI 命令、超时监控与 approval 创建、session 续接支持。26 个新测试，全部 720 个测试通过，零回归。
- 2026-03-25: 修复 Code Review R1 的 5 项 patch findings。防止重复 transition 派发和重复 timeout approval 创建；修正 phase→event 映射；完善 resume 契约（sidecar fallback + session_id 保留）；交互输入分支自动提取 artifacts_produced。
- 2026-03-25: 修复 Code Review R2 的 3 项 patch findings。(1) 消费标记移到 TQ.submit() 成功之后确保原子性，崩溃时下次轮询可重试；(2) _launch_terminal_session 改用临时 shell 脚本文件 + shlex.quote/json.dumps 安全转义，避免 prompt 中特殊字符破坏命令；(3) ato submit 用 sidecar PID 精确匹配 task，避免多 running task 时误标。
- 2026-03-25: 修复 Code Review R3 的 3 项 patch findings。(1) sidecar here-doc 从 `<<'SIDECAR_EOF'` 改为 `<<SIDECAR_EOF`，使 $$ 和 $(date) 正确展开生成合法 JSON；(2) --briefing-file 增加 story_id/phase 一致性校验；(3) 空字符串 session_id 统一降级为 fresh session。新增 2 个回归测试（33 个测试总计），全部 727 个测试通过。
