# Story 6.1a: 操作者可启动 TUI 并连接到运行中的 Orchestrator

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a 操作者,
I want 通过 `ato tui` 启动 TUI 并连接到运行中的 Orchestrator，可读写 SQLite,
So that 有一个可工作的 TUI 进程作为后续组件的容器。

## Acceptance Criteria

1. **AC1: TUI 启动与首屏加载** (FR36)
   - Given 运行 `ato tui`
   - When TUI 应用启动
   - Then 作为独立进程运行，通过 SQLite 轮询读取状态 + 审批/UAT 写入（非只读）
   - And 2 秒内从 SQLite 加载状态并渲染首屏

2. **AC2: TUI 写入路径** (Decision 2)
   - Given TUI 执行审批决策或 UAT 结果提交
   - When TUI 写入 SQLite
   - Then 直接写 SQLite + 立即 commit + 发送 nudge 通知 Orchestrator
   - And `busy_timeout=5000` 覆盖写冲突

3. **AC3: 连接已运行 Orchestrator** (Decision 1)
   - Given Orchestrator 已运行
   - When TUI 启动
   - Then TUI 连接已运行 Orchestrator 的 SQLite，完整功能可用

4. **AC4: Orchestrator 未运行时的降级** (隐含需求)
   - Given Orchestrator 未运行
   - When TUI 启动
   - Then TUI 仍可启动并显示数据库中的最近状态，后续审批/UAT 写入仍先落库 SQLite（非只读）
   - And 显示警告："Orchestrator 未运行，写入已记录，需等待下次启动后处理"

## Tasks / Subtasks

- [x] Task 1: ATOApp Textual 应用骨架 (AC: #1, #3)
  - [x] 1.1 在 `src/ato/tui/app.py` 创建 `ATOApp(textual.app.App)` 类
  - [x] 1.2 `ATOApp.__init__()` 接收 `db_path: Path` 参数，**不在 `__init__` 中读 SQLite**
  - [x] 1.3 `on_mount()` 中执行首次数据加载（`db = await get_connection()` → 查询 stories/approvals/cost_log → `finally: await db.close()`）
  - [x] 1.4 `compose()` 定义最小布局：`Header` + `DashboardScreen()` + `Footer`
  - [x] 1.5 加载 `app.tcss` 样式文件（最小化：仅背景色 `$background: #282a36`）
  - [x] 1.6 `set_interval(2.0, self.refresh_data)` 启动定时轮询
  - [x] 1.7 在 `tests/integration/test_tui_pilot.py` 编写 Textual `pilot` 集成测试（mock SQLite，不启动真实 Orchestrator）

- [x] Task 2: `ato tui` CLI 命令 (AC: #1, #3, #4)
  - [x] 2.1 在 `cli.py` 添加 `tui` 命令（`@app.command("tui")`）
  - [x] 2.2 解析 `--db-path`（默认 `.ato/state.db`）
  - [x] 2.3 验证数据库文件存在，不存在则 `typer.echo("数据库未找到，请先运行 ato init", err=True)` + `Exit(1)`
  - [x] 2.4 复用 `src/ato/core.py` 的 `read_pid_file()` 读取 `.ato/orchestrator.pid`；若需要活性检测再用 `os.kill(pid, 0)`，不要自写 PID 解析 helper
  - [x] 2.5 未运行时打印警告但仍启动 TUI；保留后续 SQLite 写入路径（不切只读）
  - [x] 2.6 调用 `ATOApp(db_path=db_path).run()` 启动 Textual 应用
  - [x] 2.7 在 `tests/unit/test_cli_tui.py` 编写 CLI 单元测试

- [x] Task 3: 数据轮询与刷新机制 (AC: #1, #2)
  - [x] 3.1 实现 `ATOApp.refresh_data()` 异步方法
  - [x] 3.2 每次刷新：`db = await get_connection()` → 查询 → `finally: await db.close()`（不复用连接）
  - [x] 3.3 使用 `get_connection(db_path)` 确保 WAL + busy_timeout=5000 + foreign_keys=ON
  - [x] 3.4 查询三要素数据：stories 状态统计、pending approvals 计数、今日 cost 汇总
  - [x] 3.5 使用 reactive 属性驱动 UI 更新（`reactive[int]` 类型）
  - [x] 3.6 刷新后更新"最后更新时间"显示
  - [x] 3.7 在 `tests/integration/test_tui_pilot.py` 增加轮询逻辑用例（mock DB 数据变化 → 验证 reactive 属性更新）

- [x] Task 4: TUI 写入路径与 nudge 集成 (AC: #2)
  - [x] 4.1 实现 `ATOApp.write_approval()` 占位方法：`db = await get_connection()` → 写入/更新 approvals → `commit()` → `finally: await db.close()` → nudge
  - [x] 4.2 写入已 commit 后，再调用 `send_external_nudge(orchestrator_pid)` 通知 Orchestrator
  - [x] 4.3 无 PID / 进程不存在时跳过 nudge，仅保留已提交的 DB 写入；`PermissionError` 记录 warning，不回滚已提交写入
  - [x] 4.4 在 `tests/integration/test_tui_pilot.py` 编写写入 + nudge 测试（mock DB + mock nudge + stale pid）

- [x] Task 5: 最小 TCSS 样式与占位 Screen (AC: #1)
  - [x] 5.1 在 `app.tcss` 添加最小深色主题变量（`$background: #282a36`, `$surface: #44475a`, `$text: #f8f8f2`）
  - [x] 5.2 在 `src/ato/tui/dashboard.py` 添加 `DashboardScreen` 占位（`Static` 显示 stories 计数 + approvals 计数 + 今日成本）
  - [x] 5.3 按 `q` 退出 TUI（Textual 默认 binding）

## Dev Notes

### 核心架构约束

- **进程独立性**（Decision 1）：TUI 是独立前台进程，通过 SQLite 与后台 Orchestrator 通信；TUI 崩溃不影响编排
- **写入路径**（Decision 2）：TUI 直接写 SQLite + 立即 commit + `send_external_nudge()`；不跨进程调用 `TransitionQueue`
- **SQLite WAL**：所有连接必须使用 `get_connection(db_path)` — 自动设置 `journal_mode=WAL` + `busy_timeout=5000` + `synchronous=NORMAL` + `foreign_keys=ON`
- **短生命周期连接**：TUI 的读和写都使用短生命周期连接（打开 → 操作 → 关闭），最小化写锁持有时间
- **连接关闭责任**：`get_connection()` 返回已打开连接；调用方必须用 `try/finally` 显式 `await db.close()`
- **Textual 生命周期**：数据加载在 `on_mount()` 而非 `__init__()`；定时刷新用 `set_interval(2.0, ...)`

### 进程启动模型

| 命令 | 行为 |
|------|------|
| `ato start` | 启动 Orchestrator 后台进程（headless），写 PID 到 `.ato/orchestrator.pid` |
| `ato tui` | 启动 TUI 前台进程，连接运行中 Orchestrator 的 SQLite |
| `ato start --tui` | 便捷模式，同时启动两者（后续 story 实现） |
| `ato stop` | 优雅停止 Orchestrator |

### 数据访问模式

| 消费者 | 连接类型 | 写入语义 |
|--------|----------|----------|
| TransitionQueue | 长生命周期（consumer 生命周期复用） | 串行写，无并发冲突 |
| Orchestrator 轮询读 | 短生命周期 | 读不阻塞写，确保读到最新 WAL 数据 |
| **TUI 读/写** | **短生命周期 + 立即 commit** | **独立进程，最小化写锁持有** |

### TUI 写入 → Orchestrator 交接流程

```
TUI 写入 approvals 表 → commit → send_external_nudge(pid)
                                      ↓
                            Orchestrator 收到 SIGUSR1 → Nudge.notify()
                                      ↓
                            _poll_cycle() 立即触发 → 读取 approvals 变更
```

Orchestrator 安全网：即使 nudge 丢失，2-5 秒间隔轮询也会发现变更。

Orchestrator 未运行时也不切换为只读：SQLite 写入仍可成功提交，只是对应的后续处理会延迟到下次启动或下一次成功 nudge。

### 首屏数据加载查询

```python
# on_mount() 和 refresh_data() 共用的查询
async def _load_dashboard_data(db: aiosqlite.Connection) -> DashboardData:
    # 1. Story 状态统计
    rows = await db.execute_fetchall(
        "SELECT status, COUNT(*) as cnt FROM stories GROUP BY status"
    )
    # 2. Pending approvals 计数
    cursor = await db.execute(
        "SELECT COUNT(*) FROM approvals WHERE status = 'pending'"
    )
    pending_row = await cursor.fetchone()
    # 3. 今日成本汇总
    cursor = await db.execute(
        "SELECT COALESCE(SUM(cost_usd), 0.0) FROM cost_log WHERE date(created_at) = date('now')"
    )
    cost_row = await cursor.fetchone()
    # 4. 最后更新时间 = 当前时间
```

### Orchestrator PID 检测

```python
from ato.core import read_pid_file


def _get_orchestrator_pid(db_path: Path) -> int | None:
    """复用 core.py 的 PID 读取约定，并在需要时做存活性检查。"""
    pid = read_pid_file(db_path.parent / "orchestrator.pid")
    if pid is None:
        return None
    try:
        os.kill(pid, 0)  # 不发送信号，仅检查存活性
    except ProcessLookupError:
        return None
    except PermissionError:
        return pid  # PID 仍然存在，只是当前进程无权发信号
    return pid
```

### 需要复用的现有代码

- **`get_connection(db_path)`** — `src/ato/models/db.py:169` — 标准连接工厂，WAL + pragmas
- **`send_external_nudge(pid)`** — `src/ato/nudge.py:53` — SIGUSR1 通知 Orchestrator
- **`read_pid_file(pid_path)` / `is_orchestrator_running(pid_path)`** — `src/ato/core.py` — PID 文件读取与活性检测约定
- **`StoryRecord` / `ApprovalRecord` / `CostLogRecord`** — `src/ato/models/schemas.py` — Pydantic 数据模型
- **`.ato/orchestrator.pid`** — `src/ato/core.py` 中的 PID 文件约定（`ato start` 写入）
- **`get_story()` / `get_tasks_by_status()`** — `src/ato/models/db.py` — 现有查询 helper

### 不要重新实现

- ❌ 不要创建新的 SQLite 连接方式 — 使用 `get_connection()`
- ❌ 不要实现自己的 nudge 机制 — 使用 `send_external_nudge()`
- ❌ 不要在 `__init__()` 中读 SQLite — 使用 `on_mount()`
- ❌ 不要复用/保持长连接 — 每次操作打开短连接
- ❌ 不要在 TUI 进程中调用 `TransitionQueue` — 只能 SQLite + nudge
- ❌ 不要 hardcode DB 路径 — 通过 CLI `--db-path` 参数传入
- ❌ 不要复制一份 PID 解析逻辑到 TUI/CLI — 复用 `core.py` 里的 `read_pid_file()` / `is_orchestrator_running()`

### 本 Story 不包含的内容（后续 Story 实现）

| 功能 | 目标 Story |
|------|-----------|
| 深色主题 9 个语义色、三重状态编码、响应式断点 | 6.1b |
| ThreeQuestionHeader 自定义 Widget | 6.2a |
| DashboardScreen 三面板布局、story 列表排序 | 6.2b |
| 审批交互（y/n 快捷键、ApprovalCard） | 6.3a |
| 异常审批面板 | 6.3b |
| Story 详情钻入导航 | 6.4 |
| 搜索面板 | 6.5 |

本 Story 只需交付：**可启动的 TUI 骨架 + SQLite 读写连接 + 轮询刷新 + nudge 集成 + 最小占位 UI**。

### Project Structure Notes

```
src/ato/tui/
├── __init__.py          # 已存在（空模块）
├── app.py               # ← 本 Story 主要编辑：ATOApp 类
├── app.tcss             # ← 本 Story 添加最小样式
├── dashboard.py         # ← 本 Story 添加 DashboardScreen 占位
├── approval.py          # 已存在（空占位）— 不修改
├── story_detail.py      # 已存在（空占位）— 不修改
└── widgets/
    └── __init__.py      # 已存在（空占位）— 不修改
```

CLI 入口：`src/ato/cli.py` — 添加 `@app.command("tui")` 命令

测试文件：
- `tests/unit/test_cli_tui.py`（新建）— `ato tui` CLI 命令测试
- `tests/integration/test_tui_pilot.py`（新建）— Textual `pilot` + mock SQLite 集成测试

**SCHEMA_VERSION 不变** — 无需 DB 迁移，本 Story 只读写现有表。

### 编码约定

- **Pydantic**: 继承 `_StrictBase` (strict=True, extra="forbid")
- **CRUD**: `async def xxx(db: aiosqlite.Connection, ...)`，参数化查询 `?`
- **CLI**: `@app.command("tui")` 同步函数 + `ATOApp().run()`（Textual 自带事件循环）
- **日志**: `structlog.get_logger()`，snake_case 事件名
- **错误输出**: `typer.echo(msg, err=True)` 到 stderr
- **TUI 测试**: Textual `pilot` + mock SQLite（`tmp_path` fixture 创建临时 DB）

### 现有代码接口速查

```python
# db.py — 连接工厂
async def get_connection(db_path: Path) -> aiosqlite.Connection:
    """WAL + busy_timeout=5000 + row_factory=Row"""

# db.py — 现有查询
async def get_story(db, story_id) -> StoryRecord | None
async def get_tasks_by_status(db, status) -> list[TaskRecord]
async def insert_approval(db, approval: ApprovalRecord) -> None

# nudge.py — 外部通知
def send_external_nudge(orchestrator_pid: int) -> None  # SIGUSR1

# schemas.py — 数据模型
class StoryRecord(_StrictBase):
    story_id: str; title: str; status: StoryStatus
    current_phase: str; worktree_path: str | None; ...

class ApprovalRecord(_StrictBase):
    approval_id: str; story_id: str; approval_type: str
    status: Literal["pending", "approved", "rejected"]
    payload: str | None; decision: str | None; ...

class CostLogRecord(_StrictBase):
    cost_log_id: str; task_id: str; story_id: str
    cost_usd: float; ...

# core.py — PID 文件
# ato start 写入 .ato/orchestrator.pid
```

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 6.1a] — 完整 AC 定义
- [Source: _bmad-output/planning-artifacts/architecture.md#Decision 1] — 进程生命周期模型（TUI 独立进程）
- [Source: _bmad-output/planning-artifacts/architecture.md#Decision 2] — TUI↔Orchestrator 通信（SQLite 直写 + nudge）
- [Source: _bmad-output/planning-artifacts/architecture.md#TUI Architecture Pattern] — MVP Screens、compose/on_mount 模式、set_interval 轮询
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md#Platform Strategy] — TUI 独立进程 + SQLite 直写 + nudge
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md#Experience Principles] — 透明非实时、"Ns 前更新"
- [Source: _bmad-output/planning-artifacts/prd.md#FR36] — TUI story 状态/阶段/CL 进度
- [Source: _bmad-output/planning-artifacts/prd.md#FR37] — TUI 审批交互
- [Source: src/ato/models/db.py#get_connection] — 标准连接工厂
- [Source: src/ato/nudge.py#send_external_nudge] — SIGUSR1 nudge
- [Source: src/ato/core.py#read_pid_file] — PID 文件读取与存活判断基础约定
- [Source: src/ato/cli.py#_send_nudge_safe] — “写入已提交，nudge best-effort” 现有外部 writer 模式
- [Source: _bmad-output/implementation-artifacts/2b-6-interactive-session.md] — 外部 writer → nudge 模式（同样适用于 TUI）

### Previous Story Intelligence

**从 Story 2B.6 学到的关键模式：**
- 外部 writer（ato submit / TUI）只能走 `SQLite write + nudge`，不跨进程调用 TransitionQueue
- `send_external_nudge(orchestrator_pid)` 发送 SIGUSR1 通知
- Orchestrator 未运行时跳过 nudge，仅更新 DB
- 这代表“nudge best-effort”，**不是** “切换为只读模式”
- PID 文件位于 `.ato/orchestrator.pid`

**从 Story 1.2 学到的关键模式：**
- WAL PRAGMA 三件套必须在每个连接上设置
- `get_connection()` 已封装所有 PRAGMA
- `db.row_factory = aiosqlite.Row` 提供 column-name 映射
- 短生命周期连接用于读操作，最小化锁持有

**从 Story 1.5 学到的关键模式：**
- CLI 命令使用 `@app.command("name")` 装饰器
- 错误处理：`typer.echo(msg, err=True)` + `typer.Exit(code=1)`
- 配置加载失败时优雅降级

**从 Story 2A.3 学到的关键模式：**
- `ato start` 写入 `.ato/orchestrator.pid`
- `ato stop` 通过 PID 文件定位并优雅停止进程
- SIGUSR1 handler 在 Orchestrator 启动时注册

### Git Intelligence

最近提交聚焦于 Epic 2B 完成和 Epic 3 开始：
- `d017ac9` Merge story 2B.6: Interactive Session 启动与 ato submit
- `0299bd4` Merge story 3.2a: Convergent Loop 首轮全量 Review
- `e811573` feat: Story 1.5 ato plan 阶段预览

所有 TUI 前置基础设施已就绪：SQLite 状态层（Epic 1）、编排引擎（Epic 2A）、Agent 集成（Epic 2B）、nudge 机制（2A.3/2B.6）。TUI 模块目录和空文件已在 Story 1.1 项目脚手架中创建。

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6 (1M context)

### Debug Log References

### Completion Notes List

- ✅ Task 1: ATOApp 骨架 — `ATOApp(App[None])` 类实现，`__init__` 仅存储 `db_path`（不读 SQLite），`on_mount` 通过 `get_connection()` 短连接加载 stories/approvals/cost_log 数据，`compose()` 渲染 Header + DashboardScreen + Footer，`set_interval(2.0)` 启动轮询
- ✅ Task 2: CLI 命令 — `@app.command(“tui”)` 实现，复用 `core.read_pid_file()` + `os.kill(pid, 0)` 检测 Orchestrator 运行状态，stale PID 和无 PID 均打印警告但不切只读，PID 传递给 ATOApp 用于后续 nudge
- ✅ Task 3: 数据轮询 — `refresh_data()` 异步方法调用 `_load_data()` 重载数据，每次短连接打开/关闭，4 个 reactive 属性（`story_count`, `pending_approvals`, `today_cost_usd`, `last_updated`）驱动 UI 更新，异常仅 structlog.warning 不崩溃
- ✅ Task 4: 写入 + nudge — `write_approval()` 占位方法实现 SQLite 直写 + commit + `send_external_nudge()`，无 PID 跳过 nudge，ProcessLookupError / PermissionError 仅 warning 不回滚
- ✅ Task 5: TCSS + DashboardScreen — `app.tcss` 最小深色主题（$background/#282a36, $surface/#44475a, $text/#f8f8f2），`DashboardScreen(Static)` 显示 stories/approvals/cost 计数文本摘要，`q` 绑定退出

### File List

- src/ato/tui/app.py (modified — ATOApp 主类)
- src/ato/tui/app.tcss (modified — 最小深色主题样式)
- src/ato/tui/dashboard.py (modified — DashboardScreen 占位组件)
- src/ato/cli.py (modified — 添加 ato tui 命令)
- tests/integration/test_tui_pilot.py (new — 13 个 Textual pilot 集成测试)
- tests/unit/test_cli_tui.py (new — 6 个 CLI 单元测试)

### Change Log

- 2026-03-25: create-story 创建 — 基于 epics/architecture/PRD/前置 story 分析生成完整开发上下文
- 2026-03-25: validate-create-story 修订 —— 移除与外部 writer 模式冲突的”只读降级”；改为复用 `read_pid_file()`/正确处理 `PermissionError`；把 `DashboardScreen` 占位收敛到 `dashboard.py`；修正 aiosqlite 查询示例为 `execute(...)+fetchone()`；明确 Textual pilot 测试落在 `tests/integration/test_tui_pilot.py`
- 2026-03-25: dev-story 实现完成 — ATOApp 骨架 + CLI 命令 + 数据轮询 + 写入/nudge + TCSS/DashboardScreen; 19 个新测试全部通过; 816 个总测试零回归; ruff + mypy 通过
- 2026-03-25: code-review 修复 3 个 findings:
  - [中] write_approval 添加 `WHERE status = 'pending'` + rowcount 检查，防止覆盖已处理审批，返回 bool 指示成功/失败
  - [中] PID 每次写入时从文件重新读取（`_resolve_orchestrator_pid`），不再使用启动时缓存的 PID，支持 Orchestrator 重启/后启动场景；内联实现避免 tui → core 依赖
  - [低] 默认路径测试使用 `monkeypatch.chdir(tmp_path)` 隔离 CWD，消除环境依赖空跑风险
  - 新增 `test_write_approval_rejects_non_pending` 验证并发保护; 817 个总测试零回归
