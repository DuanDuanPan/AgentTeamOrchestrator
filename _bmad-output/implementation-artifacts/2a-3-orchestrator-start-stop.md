# Story 2A.3: Orchestrator 事件循环启停

Status: done

## Story

As a 操作者,
I want 通过 `ato start` 启动编排器、`ato stop` 优雅停止,
So that 系统在后台自动推进 story 流水线，可以随时安全停止。

## Acceptance Criteria

1. **正常启动序列**
   ```
   Given 操作者运行 ato start
   When Orchestrator 启动
   Then 写 PID 到 .ato/orchestrator.pid，加载配置（load_config），初始化 TransitionQueue consumer，注册 SIGUSR1 信号 handler，开始 asyncio 事件循环
   And 配置解析与状态机构建 ≤3 秒（NFR5）
   And structlog 输出启动日志（恢复模式信息）
   ```

2. **优雅停止序列**
   ```
   Given Orchestrator 正在运行
   When 操作者运行 ato stop
   Then 读取 .ato/orchestrator.pid 获取 PID
   And 向 Orchestrator 发送 SIGTERM
   And Orchestrator 将所有 status=running 的 task 标记为 paused（Decision 7）
   And 等待当前 CLI 调用完成（或超时后三阶段清理）
   And 停止 TransitionQueue consumer
   And 删除 .ato/orchestrator.pid 文件
   ```

3. **事件循环轮询**
   ```
   Given Orchestrator 事件循环运行中
   When 每 2-5 秒轮询间隔触发（或被 nudge 立即唤醒）
   Then 检测新的 transition 事件、检查 approval 状态、调度就绪的 agent 任务
   And 使用 Nudge.wait(timeout=polling_interval) 替代固定 sleep
   ```

4. **SIGUSR1 信号 handler 接入**
   ```
   Given Orchestrator 已启动并注册 SIGUSR1 handler
   When 外部进程（TUI / ato submit）通过 send_external_nudge() 发送 SIGUSR1
   Then 信号 handler 调用 nudge.notify()
   And Orchestrator 立即唤醒执行轮询，不等定期间隔
   ```

5. **重复启动防护**
   ```
   Given .ato/orchestrator.pid 文件已存在且对应进程存活
   When 操作者再次运行 ato start
   Then 拒绝启动并提示已有 Orchestrator 运行
   And 退出码 1
   ```

6. **启动时恢复检测（Decision 7）**
   ```
   Given ato start 启动时检测到 tasks 表
   When 存在 status=running 的 task
   Then 输出 "检测到 N 个 running task，进入崩溃恢复模式"（具体恢复逻辑由 Epic 5 实现，本 story 仅检测并 log）
   When 存在 status=paused 的 task
   Then 输出 "检测到 N 个 paused task，正常恢复"（具体恢复调度由后续 story 实现）
   When 无 running 或 paused task
   Then 输出 "全新启动，无待恢复任务"
   ```

## Tasks / Subtasks

- [x] Task 1: 实现 Orchestrator 核心类 (AC: #1, #3, #4, #6)
  - [x] 1.1 在 `src/ato/core.py` 中实现 `Orchestrator` 类，构造函数接收 `settings: ATOSettings` 和 `db_path: Path`
  - [x] 1.2 实现 `async run() -> None`——主事件循环：写 PID → 初始化组件 → 恢复检测 → 轮询循环 → 清理
  - [x] 1.3 实现轮询循环：`await nudge.wait(timeout=polling_interval)` 替代固定 sleep，每轮检测 transition 事件
  - [x] 1.4 实现 `async _startup() -> None`——消费已解析的 `settings`、初始化 TransitionQueue、注册 SIGTERM/SIGUSR1 handler、写 PID 文件
  - [x] 1.5 实现 `async _shutdown() -> None`——标记 running tasks 为 paused、停止 TransitionQueue、删除 PID 文件
  - [x] 1.6 注册 SIGUSR1 信号 handler，将信号转为 `nudge.notify()`（使用 `loop.add_signal_handler()`）
  - [x] 1.7 实现恢复检测日志：扫描 tasks 表 running/paused 状态，structlog 输出恢复模式信息

- [x] Task 2: 实现 PID 文件管理 (AC: #1, #2, #5)
  - [x] 2.1 实现 `write_pid_file(pid_path: Path) -> None`——写入当前进程 PID
  - [x] 2.2 实现 `read_pid_file(pid_path: Path) -> int | None`——读取 PID，文件不存在返回 None
  - [x] 2.3 实现 `is_orchestrator_running(pid_path: Path) -> bool`——读取 PID + `os.kill(pid, 0)` 检测存活；stale PID 视为未运行
  - [x] 2.4 实现 `remove_pid_file(pid_path: Path) -> None`——删除 PID 文件（幂等）

- [x] Task 3: 实现 `ato start` CLI 命令 (AC: #1, #5)
  - [x] 3.1 在 `src/ato/cli.py` 中添加 `ato start` 命令
  - [x] 3.2 接受可选参数：`--db-path`、`--config`（ato.yaml 路径）
  - [x] 3.3 启动前调用 `is_orchestrator_running()` 检测重复启动
  - [x] 3.4 调用 `configure_logging()` 初始化日志（使 preflight / orchestrator 启动日志可见）
  - [x] 3.5 调用 `run_preflight(project_path=Path.cwd(), db_path=resolved_db, include_auth=False)` 做快速 preflight；存在 HALT 时以环境错误退出
  - [x] 3.6 调用 `load_config()` 解析配置，并将 `ATOSettings` 注入 `Orchestrator`；不要在 `core.py` 二次读取 YAML
  - [x] 3.7 创建 Orchestrator 实例并 `asyncio.run(orchestrator.run())`

- [x] Task 4: 实现 `ato stop` CLI 命令 (AC: #2)
  - [x] 4.1 在 `src/ato/cli.py` 中添加 `ato stop` 命令
  - [x] 4.2 接受可选参数：`--pid-file`（默认 `.ato/orchestrator.pid`）
  - [x] 4.3 读取 PID 文件，发送 SIGTERM 到 Orchestrator 进程
  - [x] 4.4 轮询 `os.kill(pid, 0)` 等待进程退出；超时后给出提示，必要时升级为 `SIGKILL` 清理
  - [x] 4.5 PID 文件不存在或进程不存活时输出友好提示

- [x] Task 5: 单元测试——Orchestrator 核心行为 (AC: #1, #3, #4, #5, #6)
  - [x] 5.1 创建 `tests/unit/test_core.py`
  - [x] 5.2 测试 PID 文件写入/读取/删除/存活检测
  - [x] 5.3 测试重复启动防护（PID 存在 + 进程存活 → 拒绝）
  - [x] 5.4 测试恢复模式检测日志（running tasks → 崩溃恢复日志、paused tasks → 正常恢复日志、无 tasks → 全新启动日志）
  - [x] 5.5 测试 SIGUSR1 handler 注册与 nudge 触发（mock loop.add_signal_handler）
  - [x] 5.6 测试 shutdown 标记 running tasks 为 paused

- [x] Task 6: 集成测试——启停端到端 (AC: #1, #2, #3)
  - [x] 6.1 创建 `tests/integration/test_orchestrator_lifecycle.py`
  - [x] 6.2 测试完整启动→轮询→停止流程（使用短轮询间隔 + 快速触发停止）
  - [x] 6.3 测试启动后 PID 文件存在，停止后 PID 文件删除
  - [x] 6.4 测试启动延迟 ≤3 秒（NFR5）

- [x] Task 7: CLI 命令测试 (AC: #1, #2, #5)
  - [x] 7.1 创建 `tests/unit/test_cli_start_stop.py`
  - [x] 7.2 使用 `typer.testing.CliRunner` 测试 `ato start` 重复启动拒绝（exit code 1）
  - [x] 7.3 测试 `ato stop` PID 不存在时的友好提示

- [x] Task 8: 代码质量验证
  - [x] 8.1 `uv run ruff check src/ato/core.py src/ato/cli.py` — 通过
  - [x] 8.2 `uv run mypy src/ato/core.py src/ato/cli.py` — 通过
  - [x] 8.3 `uv run pytest tests/unit/test_core.py tests/unit/test_cli_start_stop.py tests/integration/test_orchestrator_lifecycle.py -v` — 全部通过
  - [x] 8.4 `uv run pytest` — 确认零回归（478 tests passed，基线从 454 增长到 478）

## Dev Notes

### 核心实现模式

**Orchestrator 事件循环设计（Decision 1 + Decision 2）：**

Orchestrator 是独立后台进程（headless），核心是 asyncio 事件循环 + 轮询/nudge 混合模式。

```python
class Orchestrator:
    def __init__(self, *, settings: ATOSettings, db_path: Path) -> None:
        self._settings = settings
        self._db_path = db_path
        self._nudge = Nudge()
        self._tq: TransitionQueue | None = None
        self._running = True
        self._pid_path = db_path.parent / "orchestrator.pid"

    async def run(self) -> None:
        """主入口——启动 → 轮询 → 停止。"""
        await self._startup()
        try:
            while self._running:
                await self._poll_cycle()
                await self._nudge.wait(timeout=self._settings.polling_interval)
        finally:
            await self._shutdown()

    async def _poll_cycle(self) -> None:
        """单次轮询：检测新事件、检查 approval 状态、调度就绪任务。
        MVP 阶段：仅记录日志。Agent 调度由 Epic 2B/3 接入。"""
        ...
```

**信号处理（SIGTERM + SIGUSR1）：**

```python
async def _startup(self) -> None:
    loop = asyncio.get_running_loop()
    # SIGTERM → 触发优雅停止
    loop.add_signal_handler(signal.SIGTERM, self._request_shutdown)
    # SIGUSR1 → 外部 nudge（TUI / ato submit 发送）
    loop.add_signal_handler(signal.SIGUSR1, self._nudge.notify)
    ...

def _request_shutdown(self) -> None:
    """SIGTERM handler：标记 _running = False，nudge 唤醒轮询循环以退出。"""
    self._running = False
    self._nudge.notify()  # 立即唤醒轮询循环
```

**优雅停止标记法（Decision 7）：**

```python
async def _shutdown(self) -> None:
    # 1. 标记所有 running tasks 为 paused
    db = await get_connection(self._db_path)
    try:
        count = await mark_running_tasks_paused(db)  # 需在 db.py 新增
        await db.commit()
        logger.info("shutdown_tasks_paused", count=count)
    finally:
        await db.close()

    # 2. 停止 TransitionQueue
    if self._tq is not None:
        await self._tq.stop()

    # 3. 删除 PID 文件
    remove_pid_file(self._pid_path)
    logger.info("orchestrator_stopped")
```

### PID 文件管理

```python
# 位置：.ato/orchestrator.pid（与 state.db 同目录）
# 写入：ato start 时写入当前 PID
# 删除：ato stop / 正常退出时删除
# 检测：ato start 前检查——文件存在 + os.kill(pid, 0) 存活 → 拒绝

def write_pid_file(pid_path: Path) -> None:
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(str(os.getpid()))

def is_orchestrator_running(pid_path: Path) -> bool:
    pid = read_pid_file(pid_path)
    if pid is None:
        return False
    try:
        os.kill(pid, 0)  # 不杀死，仅检测存活
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
```

### CLI 命令设计

```python
# ato start
@app.command("start")
def start_cmd(
    db_path: Path | None = typer.Option(None, "--db-path"),
    config_path: Path | None = typer.Option(None, "--config"),
) -> None:
    resolved_db = db_path or _DEFAULT_DB_PATH
    pid_path = resolved_db.parent / "orchestrator.pid"

    if is_orchestrator_running(pid_path):
        typer.echo("错误：Orchestrator 已在运行中。", err=True)
        raise typer.Exit(code=1)

    configure_logging(log_dir=str(resolved_db.parent / "logs"))
    preflight_results = asyncio.run(
        run_preflight(Path.cwd(), resolved_db, include_auth=False)
    )
    if any(r.status == "HALT" for r in preflight_results):
        raise typer.Exit(code=2)

    settings = load_config(config_path or Path("ato.yaml"))
    orchestrator = Orchestrator(settings=settings, db_path=resolved_db)
    asyncio.run(orchestrator.run())

# ato stop
@app.command("stop")
def stop_cmd(
    pid_file: Path | None = typer.Option(None, "--pid-file"),
) -> None:
    pid_path = pid_file or Path(".ato/orchestrator.pid")
    pid = read_pid_file(pid_path)
    if pid is None:
        typer.echo("Orchestrator 未在运行（无 PID 文件）。")
        return
    try:
        os.kill(pid, signal.SIGTERM)
        typer.echo(f"已向 Orchestrator (PID {pid}) 发送停止信号。")
    except ProcessLookupError:
        typer.echo("Orchestrator 进程已不存在，清理 PID 文件。")
        remove_pid_file(pid_path)
```

### 恢复检测逻辑（Decision 7 前置）

启动时扫描 tasks 表，仅做检测和 structlog 输出，不执行实际恢复（Epic 5 范畴）：

```python
async def _detect_recovery_mode(self, db: aiosqlite.Connection) -> None:
    running = await count_tasks_by_status(db, "running")
    paused = await count_tasks_by_status(db, "paused")

    if running > 0:
        logger.warning("crash_recovery_detected", running_tasks=running)
    elif paused > 0:
        logger.info("graceful_recovery_detected", paused_tasks=paused)
    else:
        logger.info("fresh_start", message="无待恢复任务")
```

### 已有代码复用

**直接复用（不修改）：**
- `src/ato/nudge.py` → `Nudge` 类（wait/notify）、`send_external_nudge()` — Story 2A.2 已交付
- `src/ato/transition_queue.py` → `TransitionQueue`（start/stop/submit）— Story 2A.2 已交付
- `src/ato/config.py` → `load_config()`、`ATOSettings` — Story 1.3 已交付
- `src/ato/logging.py` → `configure_logging()` — Story 1.1 已交付
- `src/ato/models/db.py` → `get_connection()`、`init_db()`、PRAGMA WAL — Story 1.2 已交付
- `src/ato/models/schemas.py` → `TaskStatus`、`TaskRecord`、`ATOError`
- `tests/conftest.py` → `db_path`、`initialized_db_path` fixtures

**需要新增的 db.py 辅助函数：**
- `mark_running_tasks_paused(db) -> int` — 批量标记 `status=running` → `paused`，返回影响行数
- `count_tasks_by_status(db, status) -> int` — 按状态计数 tasks

**复用时的边界：**
- `run_preflight(..., include_auth=False)` 已是 Story 1.4a 交付的 `ato start` 快速检查路径；不要在 `cli.py` 里重新拼 Layer 1/2/3 检查
- `update_task_status()` 是逐条更新且自动 `commit()` 的 helper，不适合 shutdown 的批量 pause；本 story 需要新的 bulk helper 保持事务边界清晰

**不要重复造轮：**
- ❌ 不要自己实现 nudge 通知——使用 `nudge.py` 已有的 `Nudge` 类
- ❌ 不要自己管理 TransitionQueue 连接——调用其 `start()` / `stop()`
- ❌ 不要自己写 SQLite PRAGMA 设置——`get_connection()` 已内置
- ❌ 不要在 core.py 中直接操作状态机——通过 TransitionQueue 提交事件
- ❌ 不要用 `asyncio.gather`——用 `asyncio.TaskGroup`
- ❌ 不要用 `print()` 输出日志——用 structlog
- ❌ 不要用 `sys.exit()`——用 `typer.Exit(code=N)`
- ❌ 不要用 `shell=True` 启动子进程

### 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/ato/core.py` | **重写** | 从 1 行 docstring 扩展为完整 Orchestrator 实现 |
| `src/ato/cli.py` | **修改** | 新增 `ato start` 和 `ato stop` 命令 |
| `src/ato/models/db.py` | **修改** | 新增 `mark_running_tasks_paused()` 和 `count_tasks_by_status()` |
| `tests/unit/test_core.py` | **新建** | Orchestrator 核心行为单元测试 |
| `tests/unit/test_cli_start_stop.py` | **新建** | CLI start/stop 命令测试 |
| `tests/integration/test_orchestrator_lifecycle.py` | **新建** | 启停端到端集成测试 |

**不应修改的文件：**
- `src/ato/nudge.py` — Nudge 机制已完整，本 story 仅消费
- `src/ato/transition_queue.py` — TransitionQueue 已完整，本 story 仅调用 start/stop
- `src/ato/state_machine.py` — 状态机不变
- `src/ato/subprocess_mgr.py` — Agent 调度是 Epic 2B/3 范畴
- `src/ato/recovery.py` — 崩溃恢复是 Epic 5 范畴

### 依赖关系

**前置（已完成）：**
- ✅ Story 1.1：项目脚手架、structlog 配置
- ✅ Story 1.2：SQLite 持久化层（DDL、CRUD、PRAGMA WAL）
- ✅ Story 1.3：声明式配置引擎（ATOSettings、load_config）
- ✅ Story 1.4a：Preflight 三层检查引擎
- ✅ Story 2A.1：StoryLifecycle 状态机（13 状态、15 事件）
- ✅ Story 2A.2：TransitionQueue + Nudge（串行化、信号 helper）

**后续依赖本 story：**
- Epic 2B（Agent 集成 stories）— 需要 Orchestrator 事件循环运行环境
- Epic 3（Convergent Loop）— 需要 Orchestrator 调度循环
- Epic 4（人机协作）— 需要 Orchestrator 轮询 approval 状态
- Epic 5（崩溃恢复）— 基于本 story 的恢复检测基础设施
- Epic 6（TUI）— TUI 通过 SIGUSR1 → nudge 与 Orchestrator 交互

### Project Structure Notes

- `core.py` 当前只有 1 行 docstring，本 story 负责完整实现
- 模块依赖方向：`core.py` 可依赖 `config.py`、`transition_queue.py`、`nudge.py`、`models/db.py`、`logging.py` — 不依赖 `adapters/`、`tui/`
- `cli.py` 已存在，有 `ato batch select` / `ato batch status` 命令；本 story 在同文件追加 `start` / `stop` 顶层命令
- PID 文件路径 `.ato/orchestrator.pid` 与 `state.db` 同目录（`.ato/`）

### 关键技术注意事项

1. **信号 handler 必须在主线程注册**——`loop.add_signal_handler()` 要求在 asyncio 事件循环中调用（`_startup()` 内部）
2. **SIGUSR1 在 macOS/Linux 均可用**——NFR13 兼容性要求；Windows 不支持但项目限定 macOS+Linux
3. **`loop.add_signal_handler()` 的回调必须是非 async 函数**——`self._nudge.notify()` 和 `self._request_shutdown()` 都是同步函数，适配
4. **PID 文件竞态**——`is_orchestrator_running()` 检测后到 `write_pid_file()` 之间理论有竞态窗口，但本地单用户场景下可接受
5. **`asyncio.run()` 会创建新事件循环**——CLI 命令中 `asyncio.run(orchestrator.run())`，与已有 `batch_select` 模式一致
6. **TransitionQueue.start() 必须在 run() 内调用**——需要活跃的事件循环
7. **shutdown 顺序**——先标记 tasks paused → 再停 TransitionQueue → 最后删 PID 文件；顺序不能反
8. **structlog.contextvars**——在 `_startup()` 入口绑定 `component="orchestrator"`，整个生命周期携带
9. **pytest-asyncio auto mode**——`pyproject.toml` 已配置 `asyncio_mode="auto"`
10. **Typer 退出码**——成功 0、一般错误 1、环境错误 2（与架构约定一致）
11. **Decision 7 的“停止时间戳”当前无对应 schema 列**——现有 `tasks` 表没有 `stopped_at`；本 story 以 `status=paused` 作为 MVP 停止信号，不额外引入 migration
12. **“等待当前 CLI 调用完成”在本阶段只需预留 shutdown hook**——真实的 agent subprocess drain 依赖后续 Epic 2B 的 `SubprocessManager` 接入，本 story 不提前实现调度/清理器

### 架构约束备忘

**Enforcement Rules（必须遵守）：**
- 所有公共函数有类型标注（参数和返回值）
- 新模块有对应的单元测试文件
- 状态机操作用 `structlog.contextvars` 在入口绑定上下文
- SQLite 写事务中不 await 外部 IO
- CLI 错误输出到 stderr：`typer.echo(msg, err=True)`
- 用 `typer.Exit(code=N)`，不用 `sys.exit()`

**Anti-Patterns（禁止）：**
- ❌ 不要用 `asyncio.gather`（用 `TaskGroup`）
- ❌ 不要用 `shell=True` 启动子进程
- ❌ 不要用 `print()` 输出日志（用 structlog）
- ❌ 不要在 `except` 中静默吞掉异常（至少 `structlog.warning`）
- ❌ 不要跨线程共享状态机实例
- ❌ 不要手动拼接 SQL（用参数化查询）
- ❌ MVP 不要使用 `model_construct`（用 `model_validate`）

### Story 2A.1 / 2A.2 关键学习

1. **`current_state_value`** 是获取状态机当前阶段的正确 API（`current_state` 已弃用）
2. **`save_story_state()` 不自动 commit**——调用方负责 `await db.commit()`
3. **TransitionQueue.start()** 打开长连接并启动单 consumer；**stop()** 发送哨兵并等待 consumer 退出
4. **`get_connection()` 返回已打开的连接**（非 context manager）——需要 `db = await get_connection(...)` + `try/finally: await db.close()`
5. **Nudge.wait()** 返回后自动 clear event，支持下次等待
6. **send_external_nudge()** 使用 `os.kill(pid, SIGUSR1)`，本 story 需注册对应 handler
7. **所有 PRAGMA** 设置由 `get_connection()` 内置，不需要手动设置
8. **测试集规模已增长**：2026-03-24 本地 `uv run pytest --collect-only -q` 为 454 tests；不要沿用 2A.2 交付时的旧基线

### MVP 轮询循环的边界

本 story 实现的轮询循环是**骨架**——核心启停、信号处理、nudge 集成完整可用，但：
- **不实现** agent 调度逻辑（Epic 2B 的 SubprocessManager.dispatch）
- **不实现** approval 检查逻辑（Epic 4）
- **不实现** 崩溃恢复逻辑（Epic 5）
- **不实现** `ato start --tui` 便捷模式（后续 Epic 6 TUI story 接入）
- `_poll_cycle()` 在 MVP 中仅做日志记录 + 预留 hook 点

### References

- [Source: _bmad-output/planning-artifacts/epics.md — Epic 2A, Story 2A.3]
- [Source: _bmad-output/planning-artifacts/architecture.md — Decision 1: 进程生命周期模型]
- [Source: _bmad-output/planning-artifacts/architecture.md — Decision 2: TUI↔Orchestrator 通信模型]
- [Source: _bmad-output/planning-artifacts/architecture.md — Decision 7: 优雅停止标记法]
- [Source: _bmad-output/planning-artifacts/architecture.md — Decision 6: structlog 配置]
- [Source: _bmad-output/planning-artifacts/architecture.md — Asyncio Subprocess 三阶段清理协议]
- [Source: _bmad-output/planning-artifacts/architecture.md — 模块依赖方向与禁止规则]
- [Source: _bmad-output/planning-artifacts/prd.md — FR39 ato start, FR40 ato stop]
- [Source: _bmad-output/planning-artifacts/prd.md — NFR5 ≤3s 启动, NFR2 ≤5s 转换延迟]
- [Source: _bmad-output/implementation-artifacts/2a-1-story-state-machine-progression.md — 状态机实现模式]
- [Source: _bmad-output/implementation-artifacts/2a-2-serial-transition-queue.md — TransitionQueue + Nudge 实现]
- [Source: src/ato/nudge.py — Nudge 类 + send_external_nudge]
- [Source: src/ato/transition_queue.py — TransitionQueue start/stop/submit]
- [Source: src/ato/config.py — load_config, ATOSettings]
- [Source: src/ato/logging.py — configure_logging]
- [Source: src/ato/models/db.py — get_connection, init_db]
- [Source: src/ato/cli.py — 现有 CLI 命令模式参考]

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6 (1M context)

### Debug Log References

无异常调试记录。所有实现一次通过。

### Completion Notes List

- ✅ Orchestrator 核心类完整实现：构造函数、run()、_startup()、_shutdown()、_poll_cycle()、_detect_recovery_mode()、_request_shutdown()
- ✅ PID 文件管理四函数：write_pid_file、read_pid_file、is_orchestrator_running、remove_pid_file
- ✅ SIGUSR1/SIGTERM 信号 handler 通过 loop.add_signal_handler() 注册
- ✅ 优雅停止标记法：shutdown 时 running tasks → paused（Decision 7）
- ✅ 恢复检测：启动时扫描 tasks 表，区分崩溃恢复/正常恢复/全新启动三种模式
- ✅ ato start 命令：preflight → load_config → Orchestrator.run()，重复启动检测（exit code 1）
- ✅ ato stop 命令：SIGTERM → 轮询等待 → 超时升级 SIGKILL
- ✅ ATOSettings 新增 polling_interval 字段（默认 3.0s）
- ✅ db.py 新增 mark_running_tasks_paused() 和 count_tasks_by_status() 辅助函数
- ✅ 19 个单元测试 + 7 个集成测试 + 4 个 CLI 测试 + 2 个 config 测试 = 32 个新测试全部通过
- ✅ 全量回归 486 tests passed（基线从 454 增长到 486），零回归
- ✅ ruff check + mypy strict 全部通过
- ✅ [Review R1] run() 将 _startup() 纳入 try/finally，异常时也执行 _shutdown() 清理
- ✅ [Review R1] _shutdown() 每个阶段独立 try/except，对部分初始化安全
- ✅ [Review R1] polling_interval 在 _validate_numeric_bounds() 中校验 > 0
- ✅ [Review R1] 新增真实 SIGUSR1 信号端到端集成测试
- ✅ [Review R1] 清理测试文件未使用 import 和排序问题
- ✅ [Review R2] ato stop 所有退出路径（poll/SIGKILL）均清理 PID 文件
- ✅ [Review R2] _shutdown() 区分 clean/dirty stop：paused 失败 → ERROR 日志 + re-raise
- ✅ [Review R2] 新增测试：stop 默认 SIGTERM 退出时清理 PID、dirty shutdown re-raise + 资源清理
- ✅ [Review R3] 消除启动窗口竞态：信号 handler 在写 PID 之前注册，SIGTERM 始终走优雅停止
- ✅ [Review R3] 新增集成测试：启动窗口内 SIGTERM 仍执行 _shutdown()，running→paused

### File List

- `src/ato/core.py` — **重写**：从 1 行 docstring 扩展为完整 Orchestrator + PID 管理实现
- `src/ato/cli.py` — **修改**：新增 `ato start` 和 `ato stop` 顶层命令
- `src/ato/models/db.py` — **修改**：新增 `mark_running_tasks_paused()` 和 `count_tasks_by_status()`
- `src/ato/config.py` — **修改**：ATOSettings 新增 `polling_interval: float = 3.0` + 边界校验
- `tests/unit/test_core.py` — **新建**：Orchestrator 核心行为单元测试（19 tests）
- `tests/unit/test_cli_start_stop.py` — **新建**：CLI start/stop 命令测试（4 tests）
- `tests/unit/test_config.py` — **修改**：新增 polling_interval 校验测试（2 tests）+ 边界值测试更新
- `tests/integration/test_orchestrator_lifecycle.py` — **新建**：启停端到端集成测试（7 tests，含真实 SIGUSR1 + 启动窗口 SIGTERM）
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — **修改**：状态 ready-for-dev → review
- `_bmad-output/implementation-artifacts/2a-3-orchestrator-start-stop.md` — **修改**：任务进度 + Dev Agent Record

### Change Log

- 2026-03-24: validate-create-story 修订 —— 明确 `ato start` 复用 `run_preflight(include_auth=False)`、消除 `cli.py`/`core.py` 的重复配置加载、移除错误的 `os.waitpid` 指引、补充 bulk task pause 边界，并更新本地测试基线到 454 collected tests
- 2026-03-24: Story 2A.3 实现完成 —— Orchestrator 事件循环启停全部功能实现，24 个新测试通过，478 tests 全量通过零回归
- 2026-03-24: Code Review R1 —— 修复 3 个 findings：(1) High: _startup 异常泄漏修复；(2) Medium: polling_interval 边界校验；(3) Low: 真实 SIGUSR1 端到端测试。482 tests 全量通过零回归
- 2026-03-24: Code Review R2 —— 修复 2 个 findings：(1) High: ato stop 所有路径清理 PID 文件；(2) Medium: _shutdown dirty stop 区分 + re-raise。485 tests 全量通过零回归
- 2026-03-24: Code Review R3 —— 修复 1 个 finding：High: 消除启动窗口 SIGTERM 竞态——信号 handler 移到 _startup() 最前面，在写 PID 之前注册。486 tests 全量通过零回归
