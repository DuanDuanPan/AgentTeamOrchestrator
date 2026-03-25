# Story 验证报告：6.1a TUI Launch + SQLite Connection

验证时间：2026-03-25
Story 文件：`_bmad-output/implementation-artifacts/6-1a-tui-launch-sqlite-connection.md`
验证模式：`validate-create-story`
结果：PASS（已应用修正）

## 摘要

该 story 整体方向正确，但原稿里有 4 个会直接误导开发实现的缺口，已在 story 文件中修正：

1. 它把 “Orchestrator 未运行” 写成 TUI 只读降级，这和仓库现有 external writer 语义冲突。
2. 它提供了一份重复且语义错误的 PID helper，会把 `PermissionError` 误判成 “Orchestrator 未运行”。
3. 它把占位 UI 和测试文件位置写得与架构文档漂移，容易让实现先绕开 `dashboard.py` 和 Textual pilot 集成测试。
4. 它在查询示例里使用了 `execute_fetchone()`，但当前 `aiosqlite` API 并没有这个 connection helper。

## 已核查证据

- 本地仓库工件：
  - `_bmad-output/planning-artifacts/epics.md`
  - `_bmad-output/planning-artifacts/architecture.md`
  - `_bmad-output/planning-artifacts/ux-design-specification.md`
  - `_bmad-output/implementation-artifacts/2b-6-interactive-session.md`
  - `src/ato/core.py`
  - `src/ato/cli.py`
  - `src/ato/models/db.py`
- 官方文档：
  - `aiosqlite` API Reference: <https://aiosqlite.omnilib.dev/en/latest/api.html>

## 发现的关键问题

### 1. “未运行即只读” 与现有 external writer 模型冲突

原 story 的 AC4 写成：

- Orchestrator 未运行时，TUI 进入只读模式

这与仓库内已经落地的模式相冲突：

- `epics.md` 的 Story 6.1a AC1 已明确 TUI 是 “审批/UAT 写入（非只读）”
- Story 2B.6 明确 external writer 在 Orchestrator 未运行时是 “跳过 nudge，仅更新 DB”
- `src/ato/cli.py::_send_nudge_safe()` 也沿用了同样语义：PID 缺失/失效时只跳过 nudge，不回滚写入

这类冲突会直接诱导实现者把 TUI 做成错误的只读降级版本。

已应用修正：

- AC4 改为 “仍可启动并显示最近状态，后续写入仍先落库 SQLite（非只读）”
- 降级提示语改为 “写入已记录，需等待下次启动后处理”
- Dev Notes 显式补充 “nudge best-effort，不切换只读”

### 2. PID helper 重复实现，且 `PermissionError` 语义写错

原 story 自带的 `_read_orchestrator_pid()` 示例：

- 直接自己读 `.ato/orchestrator.pid`
- 将 `PermissionError` 与 `ProcessLookupError` 一起视为 `None`

这和当前代码事实冲突：

- `src/ato/core.py::read_pid_file()` 已是现成约定
- `src/ato/core.py::is_orchestrator_running()` 明确把 `PermissionError` 视为 “进程存在但无权发信号”，也就是 **存活**

如果照原 story 实现，TUI 会把一个真实存在但无权发信号的 Orchestrator 错判成未运行。

已应用修正：

- Task 2.4 改为复用 `read_pid_file()`
- PID 示例改为基于 `read_pid_file()` 包装，并把 `PermissionError` 视为存活
- “不要重新实现” 中新增禁止复制 PID 解析逻辑

### 3. 占位 Screen 与测试落点和架构文档漂移

原 story 一边说 `compose()` 直接放 `Static("Dashboard placeholder")`，另一边又说后续有 `DashboardScreen`，同时把 Textual pilot 测试写成 `tests/unit/test_tui_app.py`。

这与当前架构约定不够一致：

- `architecture.md` 的 TUI pattern 已明确 `DashboardScreen` 是 MVP screen 之一
- 项目结构中已存在 `src/ato/tui/dashboard.py`
- 架构目录树把 `test_tui_pilot.py` 放在 integration 测试层

这会让实现先把结构写进 `app.py`，后续再回头拆分，增加无意义返工。

已应用修正：

- Task 1.4 改为 `Header + DashboardScreen() + Footer`
- Task 5.2 明确在 `src/ato/tui/dashboard.py` 放占位 `DashboardScreen`
- 测试文件改为：
  - `tests/unit/test_cli_tui.py`
  - `tests/integration/test_tui_pilot.py`

### 4. `aiosqlite` 查询示例使用了不存在的 `execute_fetchone()`

原 story 的查询示例中两次使用：

- `await db.execute_fetchone(...)`

但当前 `aiosqlite` 官方 API 文档只列出：

- connection helper: `execute()`, `execute_fetchall()`, `execute_insert()`
- cursor method: `fetchone()`

也就是说，这段示例不是现有仓库代码风格问题，而是 API 级别的不成立。

已应用修正：

- 查询示例改为 `cursor = await db.execute(...); row = await cursor.fetchone()`
- 保留 `execute_fetchall()` 用于状态聚合，因为该 helper 在官方 API 中存在

## 已应用增强

- 把 `get_connection()` 的关闭责任前移到 Task 层，明确使用 `try/finally`
- 在 References 中补入 `src/ato/core.py#read_pid_file` 与 `src/ato/cli.py#_send_nudge_safe`
- 在 Change Log 中记录本次 validate-create-story 的具体修订点

## 剩余风险

- 当前环境未安装 `textual` 依赖，无法在本地直接跑 Textual pilot 做额外烟雾验证；不过这次变更仅修改 story 文档，不涉及运行时代码。
- `write_approval()` 仍是 6.1a 阶段的占位写入 helper；真正的审批交互语义会在 6.3a 继续细化。

## 最终结论

修正后，该 story 已与当前 external writer 模式、PID 处理约定、TUI 模块边界和 `aiosqlite` API 对齐，可以继续保持 `ready-for-dev`。高风险误导点已经移除，不会再诱导开发者把 TUI 做成错误的只读模式，也不会再把 PID / 查询 API 写成和当前代码事实不一致的实现。
