# Story 6.2b: DashboardScreen 与 Story 列表

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a 操作者,
I want 在 DashboardScreen 中看到所有 story 的状态列表，lazygit 三面板布局高效导航,
So that 可以快速定位需要关注的 story。

## Acceptance Criteria

1. **AC1: lazygit 三面板布局数据填充** (UX-DR9, FR36)
   - Given DashboardScreen 加载
   - When 渲染主布局
   - Then 左面板显示 story 列表（替换占位文字）
   - And 右上面板显示选中 story 的联动详情
   - And 右下面板显示操作区域（审批推荐/快捷键提示占位）
   - And 在 tabbed 模式下，`[2]Stories` Tab 也显示 story 列表

2. **AC2: StoryStatusLine 组件** (UX-DR15, UX-DR11, FR36)
   - Given story 列表渲染
   - When 显示每个 story
   - Then 使用 StoryStatusLine 组件：状态图标 + story ID + 阶段 + 进度条 + 耗时 + 成本
   - And 按 AWAITING → ACTIVE → BLOCKED → DONE 自动排序
   - And 状态使用三重编码（颜色 + Unicode 图标 + 文字标签）

3. **AC3: HeartbeatIndicator** (UX-DR3)
   - Given 活跃 story（status = `in_progress`）
   - When HeartbeatIndicator 渲染
   - Then 显示动画 spinner（◐◓◑◒ 循环，1s 间隔）+ 经过时间（客户端计时器）+ CL 轮次 + 成本
   - And 经过时间从 tasks 表 started_at 时间戳起本地计算，每秒更新
   - And spinner 是增强信号，经过时间文字始终可见（无障碍要求）

4. **AC4: 空状态** (UX-DR13)
   - Given 无 story 数据
   - When 空状态显示
   - Then 提示"尚无 story。运行 `ato batch select` 选择第一个 batch"
   - And 空状态给出下一步操作指引，不显示空白页面

5. **AC5: 左面板选择联动**
   - Given 左面板 story 列表
   - When 用户按 ↑↓ 选择不同 story
   - Then 右上面板自动更新为选中 story 的概览（阶段、成本、耗时、CL 轮次）
   - And 右下面板显示该 story 的上下文操作提示

6. **AC6: 2 秒数据刷新** (NFR3)
   - Given ATOApp 轮询
   - When 每 2 秒刷新数据
   - Then story 列表数据从 SQLite 加载并更新
   - And 保持当前选中 story 的焦点位置
   - And 单次刷新渲染 ≤500ms

## Tasks / Subtasks

- [ ] Task 1: StoryStatusLine Widget (AC: #2)
  - [ ] 1.1 在 `src/ato/tui/widgets/story_status_line.py` 创建 `StoryStatusLine(Widget)` 类
  - [ ] 1.2 定义 reactive 属性：`story_id: reactive[str]`、`status: reactive[str]`、`current_phase: reactive[str]`、`cost_usd: reactive[float]`、`elapsed_seconds: reactive[int]`、`cl_round: reactive[int]`、`cl_max_rounds: reactive[int]`
  - [ ] 1.3 实现 `render()` 方法：渲染格式 `{icon} {story_id}  {phase}  {progress_bar}  {elapsed}  ${cost}`
  - [ ] 1.4 进度条基于真实状态机阶段顺序计算；`PHASE_ORDER` 必须与 `src/ato/state_machine.py` 的 `["queued", *CANONICAL_PHASES, "done"]` 对齐，不得自创 `re_reviewing` / `uat_waiting` / `uat_running` 等仓库不存在的 phase
  - [ ] 1.5 实现 `update_data()` 方法批量更新 reactive 属性
  - [ ] 1.6 使用 `theme.py` 的 `map_story_to_visual_status()` + `format_status()` + `RICH_COLORS` 获取图标和颜色

- [ ] Task 2: HeartbeatIndicator Widget (AC: #3)
  - [ ] 2.1 在 `src/ato/tui/widgets/heartbeat_indicator.py` 创建 `HeartbeatIndicator(Widget)` 类
  - [ ] 2.2 实现 spinner 动画：`◐◓◑◒` 循环，通过 `set_interval(1.0, ...)` 驱动
  - [ ] 2.3 经过时间客户端计时器：从 `started_at` 本地计算，每秒更新（与 spinner 共享定时器）
  - [ ] 2.4 渲染格式：`◐ {story_id}  {phase}  R{round}/{max}  {progress}  ${cost}  {elapsed} ◐`
  - [ ] 2.5 提供 `update_heartbeat()` 方法接收 `story_id`、`phase`、`round_num`、`max_rounds`、`cost_usd`、`started_at`

- [ ] Task 3: Story 排序逻辑 (AC: #2)
  - [ ] 3.1 在 `src/ato/tui/theme.py` 新增 `VISUAL_STATUS_SORT_ORDER` 常量定义排序优先级
  - [ ] 3.2 排序规则：`awaiting(0) → active(1) → running(2) → frozen(3) → done(4) → info(5)`；其中 `running` 必须紧邻 `active`，不得落到 `blocked/frozen` 之后
  - [ ] 3.3 新增 `sort_stories_by_status(stories: list) -> list` 函数，使用 `map_story_to_visual_status()` 映射后排序
  - [ ] 3.4 同一 visual status 内按 `updated_at` 降序排列（最近更新的在前）

- [ ] Task 4: ATOApp 数据扩展 (AC: #6)
  - [ ] 4.1 扩展 `ATOApp._load_data()`：获取全部 story 的完整记录列表 + 每个 story 的累计成本 + 当前 phase 对应的最新 running task `started_at` + 每个 story 的最新 `round_num`
  - [ ] 4.2 在 `ATOApp` 存储 app 级数据快照（`stories` / `story_costs` / `story_started_at` / `story_cl_rounds`）；若使用 reactive 容器，刷新时必须整包替换，不得原地 mutate
  - [ ] 4.3 扩展 `ato tui` 启动路径与 `ATOApp.__init__()`，把 `settings.convergent_loop.max_rounds` 注入 TUI；`HeartbeatIndicator` 不得 hardcode `3`
  - [ ] 4.4 在 `_update_dashboard()` 中传递 `stories`、`story_costs`、`story_started_at`、`story_cl_rounds`、`convergent_loop_max_rounds` 到 `DashboardScreen`

- [ ] Task 5: DashboardScreen Story 列表渲染 (AC: #1, #4, #5)
  - [ ] 5.1 将左面板 `Static(id="left-panel-content")` 替换为 `VerticalScroll` 容器 + 动态 story row 列表（`StoryStatusLine` / `HeartbeatIndicator`），并为每个 row 保持稳定的 `story_id` 标识
  - [ ] 5.2 实现 `update_content()` 扩展：接收 `stories` 参数，排序后渲染到左面板
  - [ ] 5.3 实现空状态逻辑：stories 为空时显示 `Static` 提示信息
  - [ ] 5.4 实现选中联动：当左侧 `_FocusablePanel` 获焦时，↑↓ 只改变当前选中 story；Tab/Shift-Tab 仍保持既有 panel 焦点切换；右上面板按 `selected_story_id` 联动更新
  - [ ] 5.5 在 tabbed 模式 `[2]Stories` Tab 同步显示 story 列表
  - [ ] 5.6 `_refresh_placeholders()` 中保留右下面板操作区域（审批相关留给 Story 6.3a）

- [ ] Task 6: 右上面板 Story 概览 (AC: #5)
  - [ ] 6.1 将右上面板 `Static(id="right-top-content")` 替换为 story 概览视图
  - [ ] 6.2 显示选中 story 的：阶段（current_phase）、成本（cost_usd）、耗时（elapsed）、CL 轮次
  - [ ] 6.3 使用 `Static` + `Rich.Text` 渲染结构化概览
  - [ ] 6.4 无选中 story 时显示默认提示"选择左面板的 story 查看详情"

- [ ] Task 7: TCSS 样式 (AC: #1, #2)
  - [ ] 7.1 在 `app.tcss` 添加 `StoryStatusLine` / `HeartbeatIndicator` 样式：高度 1 行、selected/focus 高亮（不是 hover-only）
  - [ ] 7.2 添加 `HeartbeatIndicator` 样式
  - [ ] 7.3 添加选中 story 的 `$accent` 高亮样式
  - [ ] 7.4 空状态 `Static` 居中显示、`$muted` 颜色

- [ ] Task 8: widgets 模块导出 (AC: #2, #3)
  - [ ] 8.1 在 `widgets/__init__.py` 导出 `StoryStatusLine` 和 `HeartbeatIndicator`

- [ ] Task 9: 单元测试 (AC: #2, #3, #4)
  - [ ] 9.1 `tests/unit/test_story_status_line.py`：状态图标/颜色正确渲染、进度条计算、耗时/成本格式
  - [ ] 9.2 `tests/unit/test_heartbeat_indicator.py`：spinner 循环、经过时间计算、CL 轮次显示
  - [ ] 9.3 `tests/unit/test_story_sort.py`：排序逻辑（`awaiting → active → running → frozen → done → info`）、同状态内按 `updated_at` 排序，并覆盖 `running` 不得落到 `frozen` 之后

- [ ] Task 10: 集成测试 (AC: #1, #5, #6)
  - [ ] 10.1 `tests/integration/test_tui_pilot.py`（扩展）：DashboardScreen 挂载后 story 列表正确显示
  - [ ] 10.2 测试空状态下显示引导提示
  - [ ] 10.3 测试 mock SQLite 数据变化后 story 列表刷新
  - [ ] 10.4 测试左面板选择联动右上面板更新，并在 refresh 后保持当前 `selected_story_id`
  - [ ] 10.5 `tests/integration/test_tui_responsive.py`（扩展）：不同宽度下 story 列表显示正常，`Tab/Shift-Tab` 与 `↑↓` 语义不冲突

## Dev Notes

### 核心架构约束

- **Textual ≥2.0**——组件继承 `Widget`，使用 `render()` 返回 `Rich.Text`
- **数据由 ATOApp 提供**——所有 Widget 不自行创建 SQLite 连接，通过 `update_data()`/`update_content()` 接口接收数据
- **ATOApp 轮询驱动**——`set_interval(2.0, self.refresh_data)` 已在 6.1a 实现，本 Story 扩展数据内容
- **CSS 文件 `tui/app.tcss`** 是全局主题唯一入口
- **Textual 生命周期**——数据加载在 `on_mount()` 而非 `__init__()`
- **HeartbeatIndicator 的 1 秒 spinner 定时器**——由组件自身 `set_interval(1.0, ...)` 在 `on_mount()` 中创建

### Story 数据获取 SQL

```python
# 获取所有 story 完整记录
cursor = await db.execute(
    "SELECT story_id, title, status, current_phase, worktree_path, "
    "created_at, updated_at FROM stories ORDER BY updated_at DESC"
)
stories = await cursor.fetchall()

# 获取每个 story 的累计成本
cursor = await db.execute(
    "SELECT story_id, COALESCE(SUM(cost_usd), 0.0) as total_cost "
    "FROM cost_log GROUP BY story_id"
)
story_costs = dict(await cursor.fetchall())

# 获取与 story.current_phase 对齐的最新 running task started_at（用于 HeartbeatIndicator）
cursor = await db.execute(
    "SELECT t.story_id, MAX(t.started_at) AS started_at "
    "FROM tasks t "
    "JOIN stories s ON s.story_id = t.story_id "
    "WHERE t.status = 'running' "
    "AND t.phase = s.current_phase "
    "AND t.started_at IS NOT NULL "
    "GROUP BY t.story_id"
)
story_started_at = dict(await cursor.fetchall())

# 获取 CL 当前轮次（仅 current round；max_rounds 不在 SQLite）
cursor = await db.execute(
    "SELECT story_id, MAX(round_num) as current_round "
    "FROM findings GROUP BY story_id"
)
story_cl_rounds = dict(await cursor.fetchall())

# max_rounds 必须来自配置注入，不得在 TUI 中硬编码
convergent_loop_max_rounds = settings.convergent_loop.max_rounds
```

**重要**：这些查询必须添加到 `ATOApp._load_data()` 中，与现有查询共享同一短生命周期连接。不要在 Widget 中直接访问 SQLite。`max_rounds` 不在数据库里，必须在 `ato tui` 启动时 `load_config(...)` 后注入 `ATOApp`。

### 排序规则详解

Story 列表排序核心逻辑：先将 `StoryStatus` 通过 `map_story_to_visual_status()` 转换为展示语义，再按优先级排序：

| 展示语义 | 对应 StoryStatus | 排序优先级 | 含义 |
|---------|-----------------|----------|------|
| awaiting | ready, uat | 0（最高） | 等待人类操作 |
| active | planning, review | 1 | 正在活跃处理 |
| running | in_progress | 2 | 正常运行中（应与 active 相邻） |
| frozen | blocked | 3 | 需要关注的异常 |
| done | done | 4 | 已完成 |
| info | backlog | 5（最低） | 未开始 |

同一 visual status 内按 `updated_at` 降序排列。

### StoryStatusLine 渲染格式

```
◆ story-007  uat        ████████░░  18m  $2.60
◐ story-002  reviewing  ██████░░░░   8m  $1.80
✖ story-005  blocked    ████░░░░░░  42m  $5.10
✔ story-001  done       ██████████  25m  $3.20
```

**进度条阶段映射**——基于状态机流程的阶段顺序：

```python
PHASE_ORDER: list[str] = [
    "queued",
    "creating",
    "validating",
    "dev_ready",
    "developing",
    "reviewing",
    "fixing",
    "qa_testing",
    "uat",
    "merging",
    "regression",
    "done",
]
```

进度条使用 `█` (filled) 和 `░` (empty)，总宽度 10 字符。填充比例按 `phase_index / (len(PHASE_ORDER) - 1)` 计算，不要把“一个 phase = 一个字符块”写死。

### HeartbeatIndicator Spinner 实现

```python
_SPINNER_FRAMES = "◐◓◑◒"

def on_mount(self) -> None:
    self._spinner_index = 0
    self.set_interval(1.0, self._tick)

def _tick(self) -> None:
    self._spinner_index = (self._spinner_index + 1) % len(_SPINNER_FRAMES)
    self._elapsed_seconds += 1
    self.refresh()  # 触发重新渲染
```

- HeartbeatIndicator 采用**独立行 Widget**实现：当 story 为 `in_progress` 且存在匹配 `current_phase` 的 running task 时，用 `HeartbeatIndicator` 替代 `StoryStatusLine`
- 其余状态统一使用 `StoryStatusLine`
- 不保留“既可嵌入 StoryStatusLine、也可做独立 Widget”两条路线，避免实现分叉

### DashboardScreen 左面板改造

当前占位：
```python
yield Static("Stories 列表（占位）", id="left-panel-content")
```

替换为可滚动容器 + 动态组件：
```python
yield VerticalScroll(id="story-list-container")
```

`update_content()` 扩展后更新逻辑：
1. 清空 `story-list-container` 中的子组件
2. 对 stories 按排序规则排序
3. 为每个 story 创建 StoryStatusLine 或 HeartbeatIndicator（根据状态）
4. `mount()` 到容器
5. 恢复之前选中的 story（通过 `selected_story_id` 匹配）

**注意**：
- 每次刷新不要全部销毁重建——如果 story 列表未变化（story_id 集合和状态相同），跳过重建，仅更新数据。这对 ≤500ms 渲染性能至关重要。
- `Tab/Shift-Tab` 继续在 `_FocusablePanel` 之间切换；`↑↓` 只在左面板已获焦时改变 `selected_story_id`

### 右上面板联动

选中联动通过 Textual 的消息机制或 `watch_` 实现：

```python
# 在 DashboardScreen 中
def on_story_selected(self, story_id: str) -> None:
    """左面板选择变化时更新右上面板。"""
    story = self._stories_by_id.get(story_id)
    if story:
        self._update_detail_panel(story)
```

右上面板显示内容：
- Story ID 和标题
- 当前阶段（current_phase）
- 状态图标 + 颜色标签
- 成本：`$X.XX`
- 耗时：`Xm` 或 `Xh Xm`
- CL 轮次：`R{n}/{max}`（如果有 findings 数据）

### 空状态处理

```python
if not stories:
    yield Static(
        "尚无 story。运行 `ato batch select` 选择第一个 batch",
        id="empty-state",
        classes="empty-state",
    )
```

TCSS:
```tcss
.empty-state {
    content-align: center middle;
    color: $muted;
    text-style: italic;
    height: 1fr;
}
```

### Tabbed 模式下的 Story 列表

`[2]Stories` Tab 中复用同一数据源，但渲染为简化版列表（无右面板联动）。替换现有 `Static(id="tab-stories-content")`。three-panel 与 tabbed 之间共享同一个 `selected_story_id`，避免 resize 后丢失当前上下文。

### 文件结构

``` 
src/ato/
├── cli.py                   # ← 修改：`ato tui` 先加载配置，再把 `convergent_loop.max_rounds` 注入 `ATOApp`
└── tui/
    ├── __init__.py              # 不修改
    ├── app.py                   # ← 修改：_load_data() 新增 story 列表/成本/CL 查询 + _update_dashboard() 传递数据
    ├── app.tcss                 # ← 修改：StoryStatusLine/HeartbeatIndicator/空状态 样式
    ├── dashboard.py             # ← 修改：左面板替换为 story 列表 + 右上面板联动 + 空状态 + tabbed 模式
    ├── theme.py                 # ← 修改：新增 VISUAL_STATUS_SORT_ORDER + sort_stories_by_status()
    ├── approval.py              # 不修改
    ├── story_detail.py          # 不修改
    └── widgets/
        ├── __init__.py           # ← 修改：导出 StoryStatusLine + HeartbeatIndicator
        ├── three_question_header.py  # 不修改
        ├── story_status_line.py      # ← 新建：StoryStatusLine Widget
        └── heartbeat_indicator.py    # ← 新建：HeartbeatIndicator Widget
```

测试文件：
- `tests/unit/test_story_status_line.py`（新建）— StoryStatusLine 渲染和数据更新
- `tests/unit/test_heartbeat_indicator.py`（新建）— Spinner 动画和经过时间计算
- `tests/unit/test_story_sort.py`（新建）— 排序逻辑
- `tests/integration/test_tui_pilot.py`（修改）— story 列表集成测试
- `tests/integration/test_tui_responsive.py`（修改）— 响应式布局下 story 列表测试

### 需要复用的现有代码

- **`ATOApp._load_data()`** — `src/ato/tui/app.py` — 扩展查询，不重写
- **`ATOApp._update_dashboard()`** — `src/ato/tui/app.py` — 保留现有数据传递，新增 stories 和 costs
- **`DashboardScreen.update_content()`** — `src/ato/tui/dashboard.py` — 扩展参数签名
- **`DashboardScreen._refresh_placeholders()`** — `src/ato/tui/dashboard.py` — 改为更新 story 列表
- **`map_story_to_visual_status()`** — `src/ato/tui/theme.py` — 映射 StoryStatus → 展示语义
- **`format_status()`** — `src/ato/tui/theme.py` — 获取 StatusCode（icon、color_var、label）
- **`RICH_COLORS`** — `src/ato/tui/theme.py` — Rich 颜色映射
- **`_FocusablePanel`** — `src/ato/tui/dashboard.py` — 面板容器组件
- **`load_config()`** — `src/ato/config.py` — `ato tui` 启动前读取 `convergent_loop.max_rounds`
- **`get_batch_stories()`** — `src/ato/models/db.py` — 可参考但本 Story 查询全部 stories
- **`get_cost_summary()`** — `src/ato/models/db.py` — 可参考查询模式
- **`StoryRecord`** — `src/ato/models/schemas.py` — Pydantic 模型
- **`CANONICAL_PHASES` / `PHASE_TO_STATUS`** — `src/ato/state_machine.py` — 阶段顺序与阶段→状态映射参考

### 不要重新实现

- ❌ 不要在 Widget 中创建 SQLite 连接 — 数据由 ATOApp 提供
- ❌ 不要修改 ATOApp 的轮询间隔或 nudge 逻辑 — Story 6.1a 已完成
- ❌ 不要修改 ThreeQuestionHeader — Story 6.2a 已完成
- ❌ 不要修改 SQLite schema — 本 Story 不涉及数据库变更
- ❌ 不要实现审批交互逻辑 — Story 6.3a 的职责；右下面板保留占位
- ❌ 不要实现 story 详情钻入（Enter 进入详情页）— Story 6.4 的职责
- ❌ 不要实现搜索面板 — Story 6.5 的职责
- ❌ 不要使用 `asyncio.gather` — 用 `TaskGroup`（架构反模式清单）
- ❌ 不要在 `__init__()` 中读 SQLite — 用 `on_mount()`
- ❌ 不要在 Python 代码中 hardcode 颜色值到多个位置 — 统一使用 `theme.py` 的 `RICH_COLORS`
- ❌ 不要把 `convergent_loop.max_rounds` 硬编码成 `3` — 由 `ato tui` 启动前加载配置并注入
- ❌ 不要破坏 `DashboardScreen.update_content(...)` 现有契约 — 扩展参数签名，保留原有 story_count、pending_approvals 等参数向后兼容
- ❌ 不要破坏 `DashboardScreen.set_layout_mode()` 和焦点管理 — 已有的 ContentSwitcher/focus 链保持不变
- ❌ 不要在 StoryStatusLine 中手动拼接 SQL — 用参数化查询
- ❌ 不要使用 `print()` 输出日志 — 用 `structlog`

### 本 Story 交付范围

| 交付 | 说明 |
|------|------|
| ✅ StoryStatusLine Widget | 一行浓缩 story 关键信息 |
| ✅ HeartbeatIndicator Widget | 活跃 story 动画心跳 |
| ✅ Story 排序逻辑 | awaiting → active → running → frozen → done → info |
| ✅ 左面板 Story 列表渲染 | 替换占位，可滚动列表 |
| ✅ 右上面板联动详情 | 选中 story 概览 |
| ✅ 空状态处理 | 引导提示 |
| ✅ Tabbed 模式 Story Tab | 窄终端下同步列表 |
| ✅ TCSS 样式 | 列表项 + 选中高亮 + 空状态 |
| ✅ 单元测试 | Widget 渲染和排序逻辑 |
| ✅ 集成测试 | Textual pilot 集成验证 |

### 本 Story 不包含的内容（后续 Story 实现）

| 功能 | 目标 Story |
|------|-----------|
| 审批交互（y/n 快捷键、ApprovalCard）| 6.3a |
| 异常审批面板 | 6.3b |
| Story 详情钻入导航（Enter → 详情页） | 6.4 |
| 搜索面板（`/` 快捷键） | 6.5 |
| CL 进度可视化组件 | 6.4（ConvergentLoopProgress） |

### 编码约定

- **Widget 类**: 继承 `textual.widget.Widget`，实现 `render()` 返回 `Rich.Text`
- **TCSS**: Textual CSS 语法，变量用 `$var` 引用
- **Python dataclass**: `@dataclass(frozen=True, slots=True)` for immutable data
- **日志**: `structlog.get_logger()`，snake_case 事件名
- **TUI 测试**: Textual `pilot` + mock SQLite（`tmp_path` fixture 创建临时 DB）
- **类型标注**: 所有公共函数有参数和返回值类型标注
- **Pydantic**: 如需新 model 继承 `_StrictBase` (strict=True, extra="forbid")

**SCHEMA_VERSION 不变** — 无需 DB 迁移，本 Story 不涉及数据库变更。

### Previous Story Intelligence

**Story 6.2a 关键模式与经验：**
- `ThreeQuestionHeader` 使用 `render()` 返回 `Rich.Text`，通过 `RICH_COLORS` 映射 TCSS 颜色
- reactive 属性变化自动触发 `render()` 重绘
- `update_data()` 方法作为数据注入接口，ATOApp 在 `_update_dashboard()` 中调用
- `set_display_mode()` 处理响应式宽度适配
- `ATOApp._load_data()` 使用 `SELECT status, COUNT(*) as cnt FROM stories GROUP BY status` 分组查询
- `seconds_ago` 计算：先基于上次刷新时间求差值，再更新时间戳
- 保留 `story_count` 向后兼容 `DashboardScreen.update_content(...)`
- Textual `Header()` + `ThreeQuestionHeader()` + `DashboardScreen()` + `Footer()` 的 compose 顺序

**Story 6.1b 关键模式：**
- `DashboardScreen` 是 `Widget` 而非 `Screen`（在 `ATOApp.compose()` 中直接 yield）
- `_FocusablePanel` 需在 `__init__` 后显式设 `can_focus = True`（Textual 8.1.1 workaround）
- `ContentSwitcher` 管理三种布局模式（three-panel/tabbed/degraded）
- 焦点管理：`_saved_focus` 字典按模式保存/恢复焦点
- `_sync_focus_chain()` 在模式切换时禁用/启用控件
- 响应式测试覆盖了 resize 后模式切换 + 焦点保持

**Story 6.1a 关键模式：**
- `ATOApp._load_data()` 使用短生命周期连接（打开→查询→关闭）
- `set_interval(2.0, self.refresh_data)` 驱动轮询
- Textual `pilot` 集成测试：`async with app.run_test(size=(cols, rows))` + mock DB

### Git Intelligence

近期 commit 模式：
- `feat: Story X.Y 标题完整实现` 作为 commit 消息格式
- Story 6.2a 已成功实现并合并到 main
- 最近的 `chore: fix type hints, variable shadowing, and simplify context managers` 表明有代码质量修复

### Project Structure Notes

- 与 `src/ato/tui/widgets/` 目录结构对齐——新 Widget 放在此目录下
- Story 列表数据流：`ato tui` → `load_config(...)` → `ATOApp(..., convergent_loop_max_rounds=...)` → `ATOApp._load_data()` 聚合快照 → `ATOApp._update_dashboard()` → `DashboardScreen.update_content(...)`
- 遵循现有 compose 层级：`ATOApp` → `DashboardScreen` → `_FocusablePanel` → 内容 Widget

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 6.2b] — AC 定义
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md#DashboardScreen] — 三面板布局规范
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md#StoryStatusLine] — 组件规范
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md#HeartbeatIndicator] — 心跳指示器规范
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md#Empty & Loading States] — 空状态设计
- [Source: _bmad-output/planning-artifacts/architecture.md#Textual TUI 架构模式] — 架构约束
- [Source: _bmad-output/planning-artifacts/architecture.md#SQLite 连接策略] — TUI 短连接策略
- [Source: _bmad-output/planning-artifacts/architecture.md#Enforcement — 反模式清单] — 禁止操作
- [Source: _bmad-output/planning-artifacts/prd.md#NFR3] — TUI 刷新性能要求
- [Source: _bmad-output/planning-artifacts/prd.md#FR36] — TUI story 状态/阶段/CL 进度
- [Source: src/ato/cli.py] — `ato tui` 当前启动路径
- [Source: src/ato/config.py] — `load_config()` 与 `convergent_loop.max_rounds`
- [Source: src/ato/tui/app.py] — ATOApp 现有实现
- [Source: src/ato/tui/dashboard.py] — DashboardScreen 现有实现（占位结构）
- [Source: src/ato/tui/theme.py] — 状态编码 + RICH_COLORS
- [Source: src/ato/tui/app.tcss] — 当前 TCSS 主题
- [Source: src/ato/models/db.py] — SQLite schema 和现有查询函数
- [Source: src/ato/models/schemas.py#StoryRecord] — Story Pydantic 模型
- [Source: src/ato/state_machine.py#CANONICAL_PHASES] — 真实阶段顺序
- [Source: src/ato/state_machine.py#PHASE_TO_STATUS] — 阶段到状态映射
- [Source: _bmad-output/implementation-artifacts/6-2a-three-question-header.md] — 前序 Story 实现记录

### Change Log

- 2026-03-26: create-story 创建 — 基于 epics / architecture / PRD / UX spec / 前序 story 6.1a-6.2a 生成 6.2b 初稿
- 2026-03-26: validate-create-story 修订 —— 将进度条 phase 顺序对齐真实状态机；修正 Heartbeat SQL 只取与 `current_phase` 对齐的最新 running task；补上 `convergent_loop.max_rounds` 的 CLI→ATOApp 注入契约；把 `running` 排序移到 `frozen` 之前；明确 `Tab/Shift-Tab` 与 `↑↓` 的键盘语义分工，去除 hover-only 与双实现路线歧义

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

### File List
