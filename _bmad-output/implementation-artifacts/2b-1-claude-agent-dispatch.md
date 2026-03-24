# Story 2B.1: 操作者可看到 Claude agent 被调度执行任务并返回结构化结果

Status: ready-for-dev

## Story

As a 操作者,
I want 看到 Claude agent 被自动调度执行创建、开发、修复等任务，结果被结构化记录,
So that 确认 AI agent 集成正确工作。

## Acceptance Criteria

1. **子进程调度与并发控制**
   ```
   Given SubprocessManager 收到任务调度请求
   When 并发 agent 数未超过 max_concurrent_agents 配置
   Then 通过 asyncio.create_subprocess_exec 启动 CLI 进程（不使用 shell=True）
   And 在 running 字典中注册 PID、story_id、phase、启动时间
   ```

2. **Claude CLI 命令构建**
   ```
   Given 需要调用 Claude CLI 执行任务
   When 调用 ClaudeAdapter.execute(prompt, options)
   Then 构建 claude -p "<prompt>" --output-format json --max-turns <N> 命令
   And 使用 OAuth 模式（非 --bare），BMAD skills 自动加载
   ```

3. **结果解析与结构化记录**
   ```
   Given Claude CLI 调用完成
   When 解析 stdout JSON 输出
   Then 经 AdapterResult.model_validate() 验证输出结构
   And 创建 cost_log 表（CREATE TABLE IF NOT EXISTS）并记录结构化数据（耗时、成本 total_cost_usd、token 用量）（FR27, FR28）
   And tasks / cost_log 表中持久化的任务状态可供后续 `ato status` / TUI 直接消费
   ```

4. **错误分类与重试**
   ```
   Given CLI 进程超时或异常退出
   When 错误被 CLIAdapterError 分类（认证过期 / rate limit / 超时 / 未知）
   Then 自动重试 1 次（NFR8），重试仍失败则 escalate
   And 三阶段清理协议：SIGTERM → wait(5s) → SIGKILL → wait(2s)
   ```

5. **Snapshot fixture 测试**
   ```
   Given Snapshot fixture 测试（Decision 9）
   When 用 tests/fixtures/claude_output_*.json 执行解析
   Then 解析结果与 fixture 预期一致
   ```

## Tasks / Subtasks

- [ ] Task 1: 实现共享类型、适配器基类与错误体系 (AC: #4)
  - [ ] 1.1 在 `src/ato/models/schemas.py` 中定义 `ErrorCategory(str, Enum)`：`auth_expired`, `rate_limit`, `timeout`, `parse_error`, `unknown`
  - [ ] 1.2 扩展 `src/ato/models/schemas.py` 中的 `CLIAdapterError`：添加 `category: ErrorCategory`, `stderr: str`, `exit_code: int | None`, `retryable: bool` 属性
  - [ ] 1.3 在 `src/ato/models/schemas.py` 中定义 `AdapterResult(BaseModel)`：`status`, `exit_code`, `duration_ms`, `text_result`, `structured_output`, `cost_usd`, `input_tokens`, `output_tokens`, `session_id`, `error_category`, `error_message`
  - [ ] 1.4 在 `src/ato/models/schemas.py` 中定义 `ClaudeOutput(AdapterResult)` 子类：添加 `cache_read_input_tokens`、`model_usage` 字段；`from_json()` 负责把 Claude 原始 JSON 的 `total_cost_usd` / `modelUsage` 映射到内部字段
  - [ ] 1.5 在 `src/ato/adapters/base.py` 中实现适配器抽象接口、可选的进程启动回调契约，以及 `async _cleanup_process(proc, timeout=5)` 三阶段清理函数

- [ ] Task 2: 实现 ClaudeAdapter (AC: #2, #3)
  - [ ] 2.1 在 `src/ato/adapters/claude_cli.py` 中实现 `ClaudeAdapter` 类
  - [ ] 2.2 实现 `async execute(prompt, options, *, on_process_start=None) -> ClaudeOutput` 方法：构建 `claude -p <prompt> --output-format json --max-turns <N>` 命令，并通过 `asyncio.create_subprocess_exec(..., stdout=PIPE, stderr=PIPE, cwd=...)` 执行
  - [ ] 2.3 实现 stdout JSON 解析逻辑：`result` 字段为文本输出，`structured_output` 字段为结构化数据；保留 `session_id`、`usage.*`、`duration_ms`，并把 Claude 原始 `modelUsage` 映射到 `model_usage`
  - [ ] 2.4 实现错误分类逻辑：根据 exit code + stderr 模式匹配归类为 `ErrorCategory`
  - [ ] 2.5 subprocess 调用全部在 `try/finally` 中执行，进程启动后先触发 `on_process_start(proc)` 完成 PID 注册，再在 `finally` 调用 `_cleanup_process()`

- [ ] Task 3: 实现 SubprocessManager (AC: #1, #4)
  - [ ] 3.1 在 `src/ato/subprocess_mgr.py` 中实现 `SubprocessManager` 类
  - [ ] 3.2 使用 `asyncio.Semaphore(max_concurrent_agents)` 控制并发
  - [ ] 3.3 维护 `running: dict[int, RunningTask]` 字典（PID → task info）
  - [ ] 3.4 实现 `async dispatch(story_id, phase, prompt, options) -> AdapterResult`：获取 semaphore → `bind_contextvars(story_id, phase, cli_tool)` → 创建 `TaskRecord(status="running")` → 启动 adapter → 在 `on_process_start` 中注册 PID 并更新 tasks 表 → 等待结果 → 更新 tasks 表为 `completed` / `failed`
  - [ ] 3.5 实现 `async dispatch_with_retry(story_id, phase, prompt, options, max_retries=1)`：捕获 `CLIAdapterError`，retryable 错误自动重试 1 次

- [ ] Task 4: 实现 cost_log 表与记录 (AC: #3)
  - [ ] 4.1 在 `src/ato/models/schemas.py` 中添加 `CostLogRecord(BaseModel)`
  - [ ] 4.2 在 `src/ato/models/db.py` 中添加 `_COST_LOG_DDL` 和 `insert_cost_log()` 函数
  - [ ] 4.3 在 `src/ato/models/migrations.py` 中注册 v2→v3 迁移（创建 cost_log 表）
  - [ ] 4.4 更新 `SCHEMA_VERSION` 为 3
  - [ ] 4.5 在 `SubprocessManager.dispatch()` 中，调用成功/失败后均写入 cost_log；每次尝试都关联同一个 `task_id`，确保重试场景可追踪

- [ ] Task 5: 创建 Snapshot fixture 与测试 (AC: #5)
  - [ ] 5.1 创建 `tests/fixtures/claude_output_success.json`——成功返回的 JSON fixture
  - [ ] 5.2 创建 `tests/fixtures/claude_output_structured.json`——含 structured_output 的 fixture
  - [ ] 5.3 创建 `tests/fixtures/claude_output_error.json`——错误输出的 fixture
  - [ ] 5.4 实现 `tests/unit/test_claude_adapter.py`：fixture 解析测试、命令构建测试、错误分类测试
  - [ ] 5.5 实现 `tests/unit/test_subprocess_mgr.py`：并发控制测试、重试测试、清理协议测试
  - [ ] 5.6 扩展 `tests/unit/test_schemas.py`：覆盖 `ErrorCategory`、扩展后的 `CLIAdapterError`、`AdapterResult` / `ClaudeOutput` / `CostLogRecord`
  - [ ] 5.7 扩展 `tests/unit/test_db.py` 与 `tests/unit/test_migrations.py`：覆盖 `cost_log` 表初始化、CRUD、`SCHEMA_VERSION=3`、v2→v3 增量迁移
  - [ ] 5.8 实现 `tests/unit/test_cost_log.py`：cost_log 聚合/查询辅助函数测试（如 `get_cost_summary()`）

- [ ] Task 6: 代码质量验证
  - [ ] 6.1 `uv run ruff check src/ato tests` — 通过
  - [ ] 6.2 `uv run mypy src/ato` — 通过
  - [ ] 6.3 `uv run pytest tests/unit/test_schemas.py tests/unit/test_db.py tests/unit/test_migrations.py tests/unit/test_claude_adapter.py tests/unit/test_subprocess_mgr.py tests/unit/test_cost_log.py` — 通过
  - [ ] 6.4 `uv run pytest` — 全量测试通过，0 regressions

## Dev Notes

### 核心实现模式

**适配器隔离原则（ADR-08, NFR11）：**

CLI 参数构建 100% 封装在 adapter 层，orchestrator core 永远不直接接触 CLI 参数。adapter 接口是抽象边界。

**边界分工（本 story 必须明确）：**

- `ClaudeAdapter` 负责：命令构建、subprocess 生命周期、stdout/stderr 解析、错误分类
- `SubprocessManager` 负责：并发控制、`running` 映射、tasks / cost_log 持久化、重试、structlog 上下文绑定
- PID 注册通过 `ClaudeAdapter.execute(..., on_process_start=...)` 回调完成，避免 `SubprocessManager` 看不到真实 `proc.pid`

**三阶段清理协议（必须遵循）：**

```python
async def _cleanup_process(proc: asyncio.subprocess.Process, timeout: int = 5) -> None:
    """SIGTERM → wait(timeout) → SIGKILL → wait(2s)"""
    if proc.returncode is not None:
        return
    proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout)
        return
    except asyncio.TimeoutError:
        pass
    proc.kill()
    await proc.wait()  # kill 后必须 wait，防止 zombie
```

所有 subprocess 调用必须在 `try/finally` 中执行 cleanup。

**Claude CLI 输出字段映射（ADR-09 关键修正）：**

| JSON 字段 | 用途 | 注意 |
|-----------|------|------|
| `result` | 文本响应 | 始终是字符串 |
| `structured_output` | 结构化数据 | 仅当使用 `--json-schema` 时存在，**不在 `result` 中** |
| `session_id` | 会话 ID | Convergent Loop 短路复用 |
| `total_cost_usd` | 成本 | 浮点数，直接使用 |
| `usage.input_tokens` | 输入 token | 整数 |
| `usage.output_tokens` | 输出 token | 整数 |
| `usage.cache_read_input_tokens` | 缓存命中 token | 可选，默认 0 |
| `modelUsage` | 模型使用元数据 | 原样映射为内部 `model_usage`，不要虚构 `model_used` 字段 |
| `duration_ms` | 执行时间 | 毫秒 |

**OAuth 模式约束：**
- 必须使用 `claude -p`（OAuth 模式），**禁止** `--bare`（需要 ANTHROPIC_API_KEY）
- `claude -p` 会自动加载项目中的 BMAD skills（通过 `.claude/` 目录发现）
- 不需要在命令中显式指定 OAuth 参数

**命令构建参考：**

```python
cmd = ["claude", "-p", prompt, "--output-format", "json"]
if max_turns:
    cmd.extend(["--max-turns", str(max_turns)])
# 不使用 shell=True！必须用 asyncio.create_subprocess_exec(*cmd, ...)
```

### 已有代码复用

**直接复用（不修改）：**
- `src/ato/models/schemas.py` → `CLIAdapterError`（基类已存在，本 story 扩展其属性）
- `src/ato/models/schemas.py` → `TaskRecord`（已有 pid, cli_tool, cost_usd, duration_ms 字段）
- `src/ato/models/db.py` → `insert_task()`, `update_task_status()` 函数
- `src/ato/config.py` → `ATOSettings.max_concurrent_agents`（默认 4）、`TimeoutConfig.structured_job`（默认 1800s）
- `src/ato/config.py` → `PhaseDefinition`（含 cli_tool, model, timeout_seconds）
- `tests/conftest.py` → `db_path`, `initialized_db_path` fixtures

**需要扩展：**
- `src/ato/models/schemas.py` → 添加 `ErrorCategory`, `AdapterResult`, `ClaudeOutput`, `CostLogRecord`；扩展 `CLIAdapterError` 属性
- `src/ato/models/db.py` → 添加 `_COST_LOG_DDL`, `insert_cost_log()`, `get_cost_summary()`
- `src/ato/models/migrations.py` → 添加 v2→v3 迁移
- `src/ato/adapters/base.py` → 从 1 行 docstring 扩展为适配器基类 + 进程启动回调接口 + `_cleanup_process()`
- `src/ato/adapters/claude_cli.py` → 从 1 行 docstring 扩展为完整 Claude 适配器
- `src/ato/subprocess_mgr.py` → 从 1 行 docstring 扩展为完整子进程管理器
- `src/ato/models/__init__.py` / `src/ato/adapters/__init__.py` → 若新增公共类型/类被包级导入使用，则同步更新显式导出

**不要重复造轮：**
- ❌ 不要在 adapter 中自己写 SQLite 操作——调用 `db.py` 已有 CRUD
- ❌ 不要创建新的 Task model——使用已有的 `TaskRecord`
- ❌ 不要自己实现日志——使用 `structlog`
- ❌ 不要在 subprocess_mgr 中直接构建 CLI 命令——通过 adapter 接口
- ❌ 不要使用 `Decimal` 存储成本——SQLite 用 `REAL`，Pydantic 用 `float`（与 TaskRecord.cost_usd 一致）

### 错误分类逻辑

```python
def _classify_error(exit_code: int | None, stderr: str) -> tuple[ErrorCategory, bool]:
    """返回 (category, retryable)"""
    stderr_lower = stderr.lower()
    if "auth" in stderr_lower or "credential" in stderr_lower or exit_code == 401:
        return ErrorCategory.AUTH_EXPIRED, True
    if "rate limit" in stderr_lower or "too many" in stderr_lower or exit_code == 429:
        return ErrorCategory.RATE_LIMIT, True
    if isinstance(exit_code, int) and exit_code == -15:  # SIGTERM
        return ErrorCategory.TIMEOUT, True
    # parse_error 和 unknown 不重试
    return ErrorCategory.UNKNOWN, False
```

超时由 `asyncio.wait_for()` 处理，捕获 `asyncio.TimeoutError` 后分类为 `ErrorCategory.TIMEOUT`（retryable）。

### cost_log 表设计

```sql
CREATE TABLE IF NOT EXISTS cost_log (
    cost_log_id TEXT PRIMARY KEY,
    story_id    TEXT NOT NULL,
    task_id     TEXT,
    cli_tool    TEXT NOT NULL,      -- "claude" 或 "codex"
    model       TEXT,
    phase       TEXT NOT NULL,
    role        TEXT,
    input_tokens   INTEGER NOT NULL,
    output_tokens  INTEGER NOT NULL,
    cache_read_input_tokens INTEGER DEFAULT 0,
    cost_usd    REAL NOT NULL,
    duration_ms INTEGER,
    session_id  TEXT,
    exit_code   INTEGER,
    error_category TEXT,
    created_at  TEXT NOT NULL
);
```

此表与 tasks 表互补：tasks 记录任务生命周期，cost_log 记录每次 CLI 调用的详细成本和 token 数据。一个 task 可能有多条 cost_log（重试场景）。

### tasks 表生命周期要求

- `dispatch()` 进入后即创建 `TaskRecord(status="running")`，并写入 `started_at`
- 进程启动成功后立即回填 `pid`
- 正常完成时更新为 `completed`，写入 `exit_code`, `duration_ms`, `completed_at`
- 失败或重试耗尽时更新为 `failed`，写入 `exit_code`, `duration_ms`, `error_message`
- `cost_log` 记录的是“每次 CLI 调用尝试”；`tasks` 记录的是“编排层任务生命周期”

### SubprocessManager 并发控制

```python
class SubprocessManager:
    def __init__(self, max_concurrent: int, adapter: ClaudeAdapter, db_path: Path):
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._running: dict[int, RunningTask] = {}  # PID → task info
        self._adapter = adapter
        self._db_path = db_path

    async def dispatch(self, story_id, phase, prompt, options) -> AdapterResult:
        async with self._semaphore:
            structlog.contextvars.bind_contextvars(story_id=story_id, phase=phase, cli_tool="claude")
            result = await self._adapter.execute(
                prompt,
                options,
                on_process_start=self._register_running_process,
            )
            # 更新 tasks 表并写入 cost_log
            return result
```

`running` 字典的 PID 注册发生在真实 subprocess 启动之后，而不是在 adapter 返回结果之后；否则会丢失崩溃恢复所需的实时 PID 信息。

### 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/ato/adapters/base.py` | **重写** | 适配器基类 + 进程启动回调接口 + `_cleanup_process()` |
| `src/ato/adapters/claude_cli.py` | **重写** | ClaudeAdapter 完整实现 |
| `src/ato/subprocess_mgr.py` | **重写** | SubprocessManager 完整实现 |
| `src/ato/models/schemas.py` | **修改** | 添加 `ErrorCategory`, `AdapterResult`, `ClaudeOutput`, `CostLogRecord`；扩展 `CLIAdapterError` |
| `src/ato/models/db.py` | **修改** | 添加 cost_log DDL + CRUD |
| `src/ato/models/migrations.py` | **修改** | 添加 v2→v3 迁移 |
| `src/ato/models/__init__.py` | **可能修改** | 如新增类型通过包级导入暴露，则更新 `__all__` |
| `src/ato/adapters/__init__.py` | **可能修改** | 如新增适配器公共导出，则更新 `__all__` |
| `tests/fixtures/claude_output_success.json` | **新建** | 成功输出 fixture |
| `tests/fixtures/claude_output_structured.json` | **新建** | 含 structured_output 的 fixture |
| `tests/fixtures/claude_output_error.json` | **新建** | 错误输出 fixture |
| `tests/unit/test_claude_adapter.py` | **新建** | 适配器单元测试 |
| `tests/unit/test_subprocess_mgr.py` | **新建** | 子进程管理器单元测试 |
| `tests/unit/test_cost_log.py` | **新建** | cost_log CRUD / 聚合测试 |
| `tests/unit/test_schemas.py` | **修改** | 新 enum / model / error 属性测试 |
| `tests/unit/test_db.py` | **修改** | cost_log CRUD + task 生命周期测试 |
| `tests/unit/test_migrations.py` | **修改** | `SCHEMA_VERSION=3` 与 v2→v3 迁移测试 |

**不应修改的文件：**
- `src/ato/config.py` — `max_concurrent_agents` 已就绪，无需改动
- `src/ato/state_machine.py` — 状态机与 adapter 无直接耦合
- `src/ato/adapters/codex_cli.py` — Codex 适配器由 Story 2B.2 实现
- `src/ato/adapters/bmad_adapter.py` — BMAD 解析由 Story 2B.3 实现

### Project Structure Notes

- `adapters/base.py`, `adapters/claude_cli.py`, `subprocess_mgr.py` 均为 1 行 docstring stub，本 story 负责完整实现
- 模块依赖方向：`subprocess_mgr.py` → `adapters/claude_cli.py` → `adapters/base.py`；adapter 可依赖 `models/schemas.py`，但不依赖 `core.py`、`state_machine.py`、`transition_queue.py`
- `SCHEMA_VERSION` 从 2 升级到 3（新增 cost_log 表）
- `tests/fixtures/` 目录已存在（含 `.gitkeep`），本 story 只需补充 Claude fixture 文件
- 按当前仓库约定，Pydantic 类型统一放在 `models/schemas.py`；`adapters/base.py` 仅承载抽象接口和 subprocess helper。若实现时发现 `architecture.md` 文件树注释与此不一致，以仓库现有约定为准并在代码注释中说明

### 关键技术注意事项

1. **asyncio.create_subprocess_exec 不是 shell**——传参数列表，不拼接字符串，不用 `shell=True`
2. **structlog.contextvars 绑定**——在 dispatch 入口绑定 `story_id`, `phase`, `cli_tool`，所有子调用自动携带上下文
3. **pytest-asyncio auto mode**——`pyproject.toml` 已配置 `asyncio_mode=auto`，声明 `async def` 即可
4. **Pydantic strict mode**——`AdapterResult` 不继承 `_StrictBase`（外部 JSON 输入需要宽松解析），使用 `model_validate()` 而非 `model_construct()`
5. **SQLite 事务**——`insert_cost_log()` 自动 commit（与 `insert_task()` 模式一致）
6. **subprocess mock 模式**——单元测试 mock `asyncio.create_subprocess_exec`，返回 fixture 数据；不启动真实 CLI
7. **typing 兼容**——使用 `from __future__ import annotations`，类型注解用 `str | None` 格式
8. **`ato status` 当前未实现**——本 story 的可验证交付物是 cost_log + tasks 表中正确的结构化数据，供后续 CLI/TUI 直接消费
9. **CLI 版本追踪占位已存在**——`pyproject.toml` 已有 `[tool.ato]` 下的 `claude_cli_version` / `codex_cli_version` 占位，本 story 不额外设计新配置键

### 依赖关系

**前置（已完成）：**
- ✅ Story 1.1：项目脚手架、structlog 配置、所有依赖已安装
- ✅ Story 1.2：SQLite 持久化层、tasks/stories 表 CRUD、迁移机制
- ✅ Story 1.3：配置引擎、`ATOSettings.max_concurrent_agents`、`PhaseDefinition`
- ✅ Story 2A.1：StoryLifecycle 状态机（状态驱动由 orchestrator core 处理，adapter 不依赖）
- ✅ Story 2B.5：Batch 选择（SCHEMA_VERSION=2，本 story 升级到 3）

**后续依赖本 story：**
- Story 2B.2（Codex 适配器）复用 `AdapterResult` 基类和 `_cleanup_process()`
- Story 2B.3（BMAD 解析）消费 Claude 输出的 Markdown
- Story 2A.2（TransitionQueue）在 transition consumer 中调用 `SubprocessManager.dispatch()`
- Story 2A.3（Orchestrator 事件循环）整合 SubprocessManager 到主循环
- Epic 3（Convergent Loop）使用 `session_id` 短路复用 Claude 会话
- Epic 5（崩溃恢复）使用 `running` 字典的 PID 注册信息

### References

- [Source: _bmad-output/planning-artifacts/epics.md — Epic 2B, Story 2B.1]
- [Source: _bmad-output/planning-artifacts/architecture.md — Asyncio Subprocess 模式 — 三阶段清理协议]
- [Source: _bmad-output/planning-artifacts/architecture.md — structlog 配置模式]
- [Source: _bmad-output/planning-artifacts/architecture.md — FR 到结构的映射]
- [Source: _bmad-output/planning-artifacts/research/technical-claude-codex-cli-integration-research-2026-03-24.md — 结构化输出 / ADR-09 修正点]
- [Source: _bmad-output/planning-artifacts/prd.md — FR6 Claude CLI 子进程调用, FR27 结构化数据记录, FR28 成本记录]
- [Source: _bmad-output/planning-artifacts/prd.md — NFR8 自动重试, NFR11 CLI Adapter 隔离, NFR14 CLI 错误处理]
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md — ThreeQuestionHeader 成本展示, Story Detail 成本明细]
- [Source: src/ato/models/schemas.py — CLIAdapterError, TaskRecord, SCHEMA_VERSION]
- [Source: src/ato/models/db.py — insert_task, update_task_status, tasks DDL]
- [Source: src/ato/config.py — ATOSettings.max_concurrent_agents, PhaseDefinition]
- [Source: pyproject.toml — [tool.pytest.ini_options], [tool.ato]]

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

### File List
