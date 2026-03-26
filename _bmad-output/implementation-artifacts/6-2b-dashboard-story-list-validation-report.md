# Story 验证报告：6.2b DashboardScreen 与 Story 列表

验证时间：2026-03-26
Story 文件：`_bmad-output/implementation-artifacts/6-2b-dashboard-story-list.md`
验证模式：`validate-create-story`
结果：PASS（已应用修正）

## 摘要

该 story 的方向是对的，但原稿里有 5 个会直接误导开发实现的缺口，已在 story 文件中修正：

1. 它把进度条 phase 顺序写成了 `re_reviewing` / `uat_waiting` / `uat_running` 这类仓库里并不存在的阶段，和真实状态机 `CANONICAL_PHASES` 冲突。
2. 它给 HeartbeatIndicator 的 `started_at` 查询过于宽松，只按 `tasks.status='running'` 抓数据，没有约束 `task.phase == story.current_phase`，也没有按 story 聚合最新一条，容易把历史 task 或错 phase task 误显示成当前心跳。
3. 它要求渲染 `R{round}/{max}`，却只给了 `MAX(round_num)` 的 DB 查询，没有说明 `max_rounds` 来自配置；而当前 `ato tui` 启动路径也没有把配置传给 `ATOApp`。
4. 它把 `running` 排序放在 `blocked/frozen` 之后，会把正常运行中的 story 排到异常 story 后面，和现有 `theme.py` 展示语义以及 UX 预期不一致。
5. 它把交互写成 `VerticalScroll + ↑↓ + hover 高亮`，但没有明确 `Tab/Shift-Tab` 与 `_FocusablePanel` 现有焦点链的分工，容易破坏 6.1b 已建立的键盘导航契约。

## 已核查证据

- 本地仓库工件：
  - `_bmad-output/planning-artifacts/epics.md`
  - `_bmad-output/planning-artifacts/ux-design-specification.md`
  - `_bmad-output/planning-artifacts/architecture.md`
  - `_bmad-output/planning-artifacts/prd.md`
  - `_bmad-output/implementation-artifacts/6-1b-dark-theme-responsive-layout.md`
  - `_bmad-output/implementation-artifacts/6-2a-three-question-header.md`
- 当前代码：
  - `src/ato/cli.py`
  - `src/ato/config.py`
  - `src/ato/state_machine.py`
  - `src/ato/models/db.py`
  - `src/ato/models/schemas.py`
  - `src/ato/tui/app.py`
  - `src/ato/tui/dashboard.py`
  - `src/ato/tui/theme.py`
  - `src/ato/tui/app.tcss`
  - `tests/integration/test_tui_pilot.py`
  - `tests/integration/test_tui_responsive.py`

## 发现的关键问题

### 1. 进度条 phase 顺序和真实状态机不一致

原 story 在 `PHASE_ORDER` 中写了：

- `re_reviewing`
- `uat_waiting`
- `uat_running`

但当前仓库的真实状态机定义在 `src/ato/state_machine.py`，规范顺序是：

- `queued`
- `creating`
- `validating`
- `dev_ready`
- `developing`
- `reviewing`
- `fixing`
- `qa_testing`
- `uat`
- `merging`
- `regression`
- `done`

如果按原稿实现，开发者会：

- 在 TUI 里发明不存在的阶段名
- 漏掉真实的 `dev_ready` / `qa_testing` / `regression`
- 让 progress bar 和 CLI / 状态机 / 持久化状态彻底脱节

已应用修正：

- Task 1.4 改为强制对齐 `["queued", *CANONICAL_PHASES, "done"]`
- Dev Notes 的 `PHASE_ORDER` 改成真实状态机顺序
- 补充说明 progress bar 应按 phase index 比例计算，而不是把 phase 数量硬塞成 10 等分

### 2. Heartbeat 的 started_at 查询会误抓非当前 phase 的任务

原稿 SQL 是：

- `SELECT story_id, started_at FROM tasks WHERE status = 'running' ORDER BY started_at DESC`

这有两个直接问题：

- 没有限制 `task.phase == story.current_phase`
- 同一个 story 可能出现多条 running / 历史记录时，没有按 story 聚合最新时间

这会把错误的 task 显示成当前 story 的心跳来源，导致 elapsed time 和 spinner 状态都不可信。

已应用修正：

- SQL 改为 `tasks JOIN stories`
- 仅保留 `t.status='running' AND t.phase=s.current_phase`
- 用 `MAX(t.started_at)` 按 `story_id` 聚合，得到每个 story 当前 phase 对应的最新 running task

### 3. `R{round}/{max}` 缺少 `max_rounds` 来源契约

原 story 只定义了：

- `MAX(round_num)` 来自 findings

但 `max_rounds` 并不在 SQLite 里。当前 `ato tui` 的代码路径是：

- CLI 直接 `ATOApp(db_path=..., orchestrator_pid=...).run()`

并没有把配置注入进 TUI。也就是说，原稿要求显示 `R{round}/{max}`，却没有给出 `max` 的可信来源。

已应用修正：

- Task 4.3 明确：`ato tui` 启动前先 `load_config(...)`
- 将 `settings.convergent_loop.max_rounds` 注入 `ATOApp.__init__()`
- “不要重新实现”增加了“不得把 `max_rounds` 硬编码成 `3`”
- 文件结构和引用列表补上了 `src/ato/cli.py` 与 `src/ato/config.py`

### 4. 排序规则把 `running` 放到 `frozen` 后面，优先级失真

原稿把排序写成：

- `awaiting → active → blocked/frozen → running → done → info`

这会把正常运行中的 story 排到异常 story 后面。对操作者来说，这个优先级很奇怪：

- `awaiting` 应该最前
- `active/running` 是当前系统主工作流
- `frozen/blocked` 是异常，但不该把所有正在推进的 story 都挤到后面

已应用修正：

- 排序改成 `awaiting → active → running → frozen → done → info`
- 单元测试要求明确覆盖“`running` 不得落到 `frozen` 之后”
- 交付范围里的排序说明同步更新

### 5. 键盘交互契约不清，容易破坏 6.1b 的焦点链

当前仓库里，`DashboardScreen` 已有明确的焦点结构：

- `Tab/Shift-Tab` 在 `_FocusablePanel` 间切换
- `_saved_focus` / `_restore_focus` 负责模式切换时的恢复
- `tests/integration/test_tui_responsive.py` 已围绕这套契约建立验证

原稿只说：

- 左面板用 `VerticalScroll`
- 用户按 `↑↓` 选择 story
- 样式做 hover 高亮

但它没有说明：

- `↑↓` 什么时候生效
- 是否会抢走 panel focus
- `Tab/Shift-Tab` 与 `↑↓` 如何分工
- 终端 TUI 里“hover”并不是主要交互通道

已应用修正：

- Task 5.4 明确：只有左侧 `_FocusablePanel` 获焦时，`↑↓` 才改变 `selected_story_id`
- `Tab/Shift-Tab` 继续保留 panel 级焦点切换语义
- Task 7.1 把 hover-only 样式改成 selected/focus 高亮
- 集成测试补成“不同宽度下 story 列表显示正常，`Tab/Shift-Tab` 与 `↑↓` 语义不冲突”

## 已应用增强

- 把数据流说明从“`ATOApp.stories` reactive 自动传递”改成更接近当前代码的显式快照传递链路
- 在 References 中补上 `src/ato/cli.py`、`src/ato/config.py`、`CANONICAL_PHASES`
- 为 story 增加了 `Change Log`，记录 create-story 与 validate-create-story 的修订点

## 剩余风险

- 当前 story 仍选择 `VerticalScroll + 自定义 row widget` 方案，而不是直接切到 `ListView` / `OptionList`；这在实现时需要谨慎处理 row 选择与消息分发，但现在至少把键盘语义和焦点边界写清楚了，不再是“自由发挥”。
- 这次工作只修改了 story 文档与验证报告，没有运行 Textual 级别代码或测试；本次目标是收紧实现契约，而不是提前实现功能。

## 最终结论

修正后，该 story 已从“方向正确但存在多个实现岔路”变成“可以直接交给 dev-story 执行”的状态。高风险误导点已经移除：不会再围绕错误 phase 名实现 progress bar，不会再从错 phase task 读取 heartbeat，不会再凭空猜 `max_rounds`，也不会再让 `↑↓` 和 `Tab/Shift-Tab` 抢同一层焦点语义。
