# Story 6.2a: ThreeQuestionHeader Widget

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a 操作者,
I want TUI 顶栏一眼回答"系统正常吗？需要我做什么？花了多少？",
So that 无需任何操作即可掌握全局状态。

## Acceptance Criteria

1. **AC1: 四区域顶栏显示** (UX-DR1, FR36)
   - Given TUI 渲染首屏
   - When ThreeQuestionHeader 组件加载
   - Then 显示四个区域：
     - ① 系统状态：`● N 项运行中` ($success) 或 `✖ N 项异常` ($error)
     - ② 审批计数：`◆ N 审批等待` ($warning) 或 `✔ 无待处理` ($success)
     - ③ 成本摘要：`$X.XX 今日`
     - ④ 更新时间：`更新 Ns前`
   - And 各区域间用 `│` 竖线分隔

2. **AC2: 响应式宽度适配** (UX-DR9)
   - Given 不同终端宽度
   - When 响应式适配
   - Then 三级显示：
     - 180+ 列：完整文字（`● 3 项运行中 │ ◆ 2 审批等待 │ $12.50 今日 │ 更新 2s前`）
     - 140-179 列：缩略标签（`● 3运行 │ ◆ 2审批 │ $12.50 │ 2s`）
     - 100-139 列：仅图标+数字（`● 3 ◆ 2 $12.50 2s`）

3. **AC3: 2 秒数据刷新** (NFR3)
   - Given 状态更新
   - When 每 2 秒刷新数据
   - Then 从 SQLite stories + approvals + cost_log 表聚合数据
   - And 单次刷新渲染 ≤500ms

4. **AC4: 可判定状态显示边界**
   - Given 不同系统状态
   - When 渲染系统状态区域
   - Then 按当前 SQLite 可直接判定的数据至少支持：
     - 全部正常：`● N 项运行中` ($success 绿)
     - 有异常：`✖ N 项异常` ($error 红)
     - 有审批：`◆ N 审批等待` ($warning 琥珀)
     - 无审批：`✔ 无待处理` ($success 绿)
   - And `⏸ 已暂停` 仅在存在明确 paused 信号（例如 `tasks.status='paused'` 或等价系统级 pause source）时显示；本 Story 不得用“无 running + 无 pending”推断暂停

5. **AC5: 与 ATOApp 数据集成**
   - Given ATOApp 通过 `_load_data()` 轮询 SQLite
   - When 数据更新
   - Then ThreeQuestionHeader 通过 ATOApp 的数据传递机制更新显示
   - And 不自行创建 SQLite 连接（数据由 ATOApp 提供）

## Tasks / Subtasks

- [x] Task 1: ThreeQuestionHeader Widget 核心实现 (AC: #1, #4)
  - [x] 1.1 在 `src/ato/tui/widgets/three_question_header.py` 创建 `ThreeQuestionHeader(Widget)` 类
  - [x] 1.2 定义 reactive 属性：`running_count: reactive[int]`、`error_count: reactive[int]`、`pending_approvals: reactive[int]`、`today_cost_usd: reactive[float]`、`seconds_ago: reactive[int]`
  - [x] 1.3 实现 `render()` 方法，使用 `Rich.Text` 组装四区域内容，各区域用 `│` 分隔
  - [x] 1.4 实现状态逻辑：`error_count > 0` 显示异常状态（$error 红），否则 `running_count > 0` 显示正常（$success 绿）；`pending_approvals > 0` 显示等待（$warning），否则显示已完成（$success）；若没有显式 paused 信号，`running_count == 0 && error_count == 0` 只渲染空闲/无待处理，不得伪造 `⏸ 已暂停`
  - [x] 1.5 实现 `update_data()` 公共方法接收数据参数并更新 reactive 属性
  - [x] 1.6 使用 `theme.py` 中的 `StatusCode` 获取图标和颜色变量名，但因 Textual `render()` 用 Rich markup 而非 TCSS 类，需将 `$success` 等变量名转换为 Rich color name 或 hex 值

- [x] Task 2: 响应式宽度适配 (AC: #2)
  - [x] 2.1 在 `ThreeQuestionHeader` 中添加 `display_mode: reactive[str]` 属性（值：`”full”` / `”compact”` / `”minimal”`）
  - [x] 2.2 `render()` 根据 `display_mode` 选择对应格式化模板
  - [x] 2.3 提供 `set_display_mode(mode: str)` 方法供外部调用（ATOApp 的 `on_resize` 或 `watch_layout_mode` 转发宽度信息）
  - [x] 2.4 “full” 模式（180+列）：`● 3 项运行中 │ ◆ 2 审批等待 │ $12.50 今日 │ 更新 2s前`
  - [x] 2.5 “compact” 模式（140-179列）：`● 3运行 │ ◆ 2审批 │ $12.50 │ 2s`
  - [x] 2.6 “minimal” 模式（100-139列）：`● 3 ◆ 2 $12.50 2s`

- [x] Task 3: ATOApp 数据集成 (AC: #3, #5)
  - [x] 3.1 扩展 `ATOApp._load_data()` 新增查询：`SELECT status, COUNT(*) FROM stories GROUP BY status`（获取各状态 story 计数），将 `in_progress` 计为 running_count，`blocked` 计为 error_count；同时继续维护现有 `story_count = sum(all grouped counts)`，避免破坏 `DashboardScreen.update_content(...)` 与现有 6.1a / 6.1b 测试契约
  - [x] 3.2 新增 reactive 属性 `running_count: reactive[int]` 和 `error_count: reactive[int]` 到 ATOApp；保留现有 `story_count`、`pending_approvals`、`today_cost_usd`、`last_updated`
  - [x] 3.3 在 `ATOApp.compose()` 中保持现有 `Header()`，并在其下方插入 `ThreeQuestionHeader()`；本 Story 不替换 Textual `Header`，也不把 header 逻辑塞回 `DashboardScreen`
  - [x] 3.4 在 `ATOApp._update_dashboard()` 中保留既有 `dashboard.update_content(...)` 调用，同时新增 `ThreeQuestionHeader.update_data()`，传入 running_count、error_count、pending_approvals、today_cost_usd、seconds_ago
  - [x] 3.5 `seconds_ago` 计算：在 `_load_data()` 中先基于”上一次” `_last_refresh_time` 计算 elapsed seconds，再用当前时间覆盖 `_last_refresh_time`；不要在刚写入新时间戳后立刻求差值，否则每轮都会接近 `0s`

- [x] Task 4: ATOApp 宽度感知转发 (AC: #2)
  - [x] 4.1 在 `ATOApp.on_resize()` 中根据终端宽度确定 ThreeQuestionHeader 的 display_mode（180+→full, 140-179→compact, <140→minimal；其中 `<100` 的 degraded 模式仍需显示 minimal header）
  - [x] 4.2 调用 `ThreeQuestionHeader.set_display_mode(mode)` 传递模式
  - [x] 4.3 `_apply_layout()` 已有宽度转发逻辑，在其中或 `on_resize` 中增加 header 模式更新

- [x] Task 5: TCSS 样式 (AC: #1)
  - [x] 5.1 在 `app.tcss` 添加 `ThreeQuestionHeader` 样式：固定高度 1 行、背景 `$surface`、水平居中/左对齐
  - [x] 5.2 确保 ThreeQuestionHeader 在所有布局模式（three-panel/tabbed/degraded）下始终可见

- [x] Task 6: 单元测试 (AC: #1, #2, #4)
  - [x] 6.1 在 `tests/unit/test_three_question_header.py` 新建测试
  - [x] 6.2 测试四区域内容正确渲染（running/error/approvals/cost/time）
  - [x] 6.3 测试可判定状态显示逻辑（全正常/有异常/有审批/无审批）；若后续显式接入 paused 信号，再为 `⏸ 已暂停` 增补测试
  - [x] 6.4 测试三种 display_mode 格式输出（full/compact/minimal）
  - [x] 6.5 测试 `update_data()` 方法正确更新 reactive 属性

- [x] Task 7: 集成测试 (AC: #3, #5)
  - [x] 7.1 在 `tests/integration/test_tui_pilot.py` 增加挂载 / 数据刷新集成测试，并在 `tests/integration/test_tui_responsive.py` 增加宽度切换相关测试
  - [x] 7.2 测试 ATOApp 启动后 ThreeQuestionHeader 显示初始数据
  - [x] 7.3 测试 mock SQLite 数据变化后 ThreeQuestionHeader 刷新显示
  - [x] 7.4 测试不同终端宽度下 ThreeQuestionHeader display_mode 切换，覆盖 `150 → 120 → 80 → 150` 的 three-panel / tabbed / degraded 往返路径

## Dev Notes

### 核心架构约束

- **Textual ≥2.0**——组件继承 `Widget`，使用 `render()` 返回 `Rich.Text` 或 `Rich.Table`
- **数据由 ATOApp 提供**——ThreeQuestionHeader 不自行创建 SQLite 连接，通过 `update_data()` 接口接收数据
- **ATOApp 轮询驱动**——`set_interval(2.0, self.refresh_data)` 已在 6.1a 实现，本 Story 扩展数据内容
- **CSS 文件 `tui/app.tcss`** 是全局主题唯一入口——组件布局样式在此定义
- **Textual 生命周期**——数据加载在 `on_mount()` 而非 `__init__()`

### ThreeQuestionHeader 数据源与 SQL 查询

从 UX 规范提取的四区域数据源：

| 区域 | 数据源 | SQL 查询 | 更新频率 |
|------|--------|---------|---------|
| 系统状态 | stories 表 | `SELECT status, COUNT(*) FROM stories GROUP BY status` | 2-5s |
| 审批计数 | approvals 表 | `SELECT COUNT(*) FROM approvals WHERE status='pending'` | 2-5s |
| 成本摘要 | cost_log 表 | `SELECT COALESCE(SUM(cost_usd), 0.0) FROM cost_log WHERE date(created_at)=date('now')` | 2-5s |
| 更新时间 | 客户端计算 | `elapsed = now - previous_last_refresh_time` | 每次刷新 |

**现有查询（ATOApp._load_data() 已有）：**
- `SELECT COUNT(*) FROM stories` → 需扩展为 `GROUP BY status` 版本以获取 running/error 分组计数，但仍要继续产出总量 `story_count`
- `SELECT COUNT(*) FROM approvals WHERE status = 'pending'` → 复用
- `SELECT COALESCE(SUM(cost_usd), 0.0) FROM cost_log WHERE date(created_at) = date('now')` → 复用

**需要新增的查询扩展：**
```python
# 替换现有的 COUNT(*) FROM stories 为分组查询
cursor = await db.execute(
    "SELECT status, COUNT(*) as cnt FROM stories GROUP BY status"
)
rows = await cursor.fetchall()
# 从 rows 计算:
#   story_count = sum of all counts
#   running_count = count where status = 'in_progress'
#   error_count = count where status = 'blocked'
# 保留 story_count，避免 DashboardScreen 现有占位与测试回归
```

### 状态到展示语义映射

| 系统状态 | 触发条件 | 系统状态区域显示 | 颜色 |
|---------|---------|----------------|------|
| 全部正常 | `error_count == 0 && running_count > 0` | `● N 项运行中` | `$success` 绿色 |
| 有异常 | `error_count > 0` | `✖ N 项异常` | `$error` 红色 |
| 空闲 | `running_count == 0 && error_count == 0` | `● 空闲` | `$muted` 灰色 |
| 系统暂停 | 仅在存在显式 paused 信号时 | `⏸ 已暂停` | `$muted` 灰色 |

| 审批状态 | 触发条件 | 审批区域显示 | 颜色 |
|---------|---------|------------|------|
| 有审批 | `pending_approvals > 0` | `◆ N 审批等待` | `$warning` 琥珀色 |
| 无审批 | `pending_approvals == 0` | `✔ 无待处理` | `$success` 绿色 |

**关键边界：**
- 当前 stories / approvals / cost_log 查询无法单独证明“系统暂停”；如果本 Story 不额外接入 `tasks.status='paused'` 或等价系统级 pause source，就只实现“正常 / 异常 / 空闲 / 审批”这些可判定状态
- 不要把“无 running + 无 pending”误写成 `⏸ 已暂停`，否则会把“空闲”错误显示成“暂停”

### Textual Header 集成方案

Textual 内置 `Header` widget 有自己的标题渲染。本 Story 的集成契约是：**保留 `Header()`，并把 `ThreeQuestionHeader` 放在它下方。**

```python
def compose(self) -> ComposeResult:
    yield Header()                    # Textual 内置标题栏
    yield ThreeQuestionHeader()       # 三问顶栏（紧跟 Header 下方）
    yield DashboardScreen()
    yield Footer()
```
- 优点：不破坏 Textual Header 的标题 / 时钟功能；ThreeQuestionHeader 独立管理；与 6.1a / 6.1b 已存在测试契约一致
- TCSS 设置 `ThreeQuestionHeader { height: 1; dock: top; }` 固定顶部
- 本 Story 不替换 `Header`，也不把顶栏职责回收到 `DashboardScreen`

### 响应式 display_mode 与 ATOApp 布局模式的关系

| 终端宽度 | ATOApp.layout_mode | ThreeQuestionHeader.display_mode |
|---------|-------------------|--------------------------------|
| 180+ 列 | "three-panel" | "full" |
| 140-179 列 | "three-panel" | "compact" |
| 100-139 列 | "tabbed" | "minimal" |
| <100 列 | "degraded" | "minimal"（降级模式仍显示摘要） |

注意：`layout_mode` 的断点（140 列）与 `display_mode` 的断点（180 列/140 列）不完全一致。ThreeQuestionHeader 需要独立的宽度断点判断，不能直接复用 `layout_mode`。

### Rich Text 颜色映射

ThreeQuestionHeader 使用 `render()` 返回 `Rich.Text`，Rich markup 不识别 TCSS 变量名。需要将 TCSS 颜色变量映射为 Rich 可用的颜色值：

```python
# TCSS 变量 → Rich hex 颜色映射
RICH_COLORS: dict[str, str] = {
    "$success": "#50fa7b",
    "$warning": "#f1fa8c",
    "$error": "#ff5555",
    "$info": "#8be9fd",
    "$accent": "#bd93f9",
    "$muted": "#8390b7",
    "$text": "#f8f8f2",
}
```

**注意**：这些值必须与 `app.tcss` 中的定义保持一致。如果 `$muted` 在对比度测试后微调了（Story 6.1b 已确定为 `#8390b7`），此处同步使用最终值。可考虑在 `theme.py` 中统一维护颜色常量，TCSS 和 Python 代码共享真实值源。

### 文件结构

```
src/ato/tui/
├── __init__.py              # 不修改
├── app.py                   # ← 修改：扩展 _load_data() 查询 + compose() 添加 ThreeQuestionHeader + on_resize 宽度转发
├── app.tcss                 # ← 修改：添加 ThreeQuestionHeader 样式
├── dashboard.py             # 不修改
├── theme.py                 # ← 可选修改：增加 RICH_COLORS 映射常量
├── approval.py              # 不修改
├── story_detail.py          # 不修改
└── widgets/
    ├── __init__.py           # ← 修改：导出 ThreeQuestionHeader
    └── three_question_header.py  # ← 新建：ThreeQuestionHeader Widget
```

测试文件：
- `tests/unit/test_three_question_header.py`（新建）— Widget 渲染逻辑和状态测试
- `tests/integration/test_tui_pilot.py`（修改）— ATOApp 挂载 / 数据刷新集成测试
- `tests/integration/test_tui_responsive.py`（修改）— display_mode 与窄终端可见性测试

### 需要复用的现有代码

- **`ATOApp._load_data()`** — `src/ato/tui/app.py` — 扩展查询，不重写
- **`ATOApp._update_dashboard()`** — `src/ato/tui/app.py` — 保留现有 dashboard 更新，同时增加 header 数据更新
- **`ATOApp.on_resize()`** — `src/ato/tui/app.py` — 增加 header display_mode 转发
- **`get_connection(db_path)`** — `src/ato/models/db.py` — 不在 Widget 中直接使用
- **`StatusCode` / `STATUS_CODES`** — `src/ato/tui/theme.py` — 图标和颜色变量名参考
- **`format_status()`** — `src/ato/tui/theme.py` — 获取状态的 icon/label

### 不要重新实现

- ❌ 不要在 ThreeQuestionHeader 中创建 SQLite 连接 — 数据由 ATOApp 提供
- ❌ 不要修改 `ATOApp` 的轮询间隔或 nudge 逻辑 — Story 6.1a 已完成
- ❌ 不要修改 `DashboardScreen` 的布局结构 — Story 6.1b 已完成
- ❌ 不要在 Python 代码中 hardcode 颜色值到多个位置 — 统一在 `theme.py` 维护 Rich 颜色映射
- ❌ 不要修改 SQLite schema — 本 Story 不涉及数据库变更
- ❌ 不要实现审批交互逻辑 — Story 6.3a 的职责
- ❌ 不要实现 story 列表数据填充 — Story 6.2b 的职责
- ❌ 不要使用 `asyncio.gather` — 用 `TaskGroup`（架构反模式清单）
- ❌ 不要在 `__init__()` 中读 SQLite — 用 `on_mount()`
- ❌ 不要为了接入 ThreeQuestionHeader 移除 `Header()`、移除 `story_count`，或破坏 `DashboardScreen.update_content(...)` 现有契约

### 本 Story 交付范围

| 交付 | 说明 |
|------|------|
| ✅ ThreeQuestionHeader Widget | 四区域显示 + 可判定状态（paused 仅在显式信号下启用） |
| ✅ 响应式 display_mode | full/compact/minimal 三级适配 |
| ✅ ATOApp 数据集成 | 扩展 _load_data() + 数据传递 |
| ✅ TCSS 样式 | 固定高度 + 背景色 + 顶部定位 |
| ✅ 单元测试 | Widget 渲染和状态逻辑 |
| ✅ 集成测试 | Textual pilot 集成验证 |

### 本 Story 不包含的内容（后续 Story 实现）

| 功能 | 目标 Story |
|------|-----------|
| DashboardScreen 三面板数据填充、story 列表排序 | 6.2b |
| 审批交互（y/n 快捷键、ApprovalCard） | 6.3a |
| 异常审批面板 | 6.3b |
| Story 详情钻入导航 | 6.4 |
| 搜索面板与 Tab 模式完善 | 6.5 |

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

**Story 6.1a 关键模式：**
- `ATOApp._load_data()` 使用短生命周期连接（打开→查询→关闭）
- `ATOApp.compose()` 输出 `Header + DashboardScreen + Footer`
- reactive 属性驱动 UI 更新，`_update_dashboard()` 中同步数据到子组件
- Textual `pilot` 集成测试模式：`async with app.run_test(size=(cols, rows))` + mock DB

**Story 6.1b 关键模式：**
- TCSS 完整主题已就位（9 语义色），`$muted` 最终值为 `#8390b7`
- `DashboardScreen` 是 `Widget` 而非 `Screen`，在 `ATOApp.compose()` 中直接 yield
- `_FocusablePanel` 需在 `__init__` 后显式设 `can_focus = True`
- `ContentSwitcher` 管理三种布局模式
- 响应式测试覆盖了 resize 后模式切换 + 焦点保持
- `app.tcss` 中 `DashboardScreen { height: 1fr; }` 占满剩余空间

### Git Intelligence

近期 commit 显示 Story 实现遵循以下模式：
- `feat: Story X.Y 标题完整实现` 作为 commit 消息格式
- 通过 merge commit 合并到 main
- 每个 Story 产出完整的 source + tests 改动

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 6.2a] — AC 定义
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md#ThreeQuestionHeader] — 组件规范
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md#Component Implementation Strategy] — 组件实现优先级与约束
- [Source: _bmad-output/planning-artifacts/architecture.md#Textual TUI 架构模式] — 架构约束
- [Source: _bmad-output/planning-artifacts/architecture.md#Enforcement — 反模式清单] — 实现边界
- [Source: _bmad-output/planning-artifacts/prd.md#NFR3] — TUI 刷新性能要求
- [Source: src/ato/tui/app.py] — ATOApp 现有实现
- [Source: src/ato/tui/dashboard.py] — DashboardScreen 现有实现
- [Source: src/ato/tui/theme.py] — 状态编码模块
- [Source: src/ato/tui/app.tcss] — 当前 TCSS 主题
- [Source: src/ato/models/db.py] — SQLite schema（stories/approvals/cost_log 表结构）

### Change Log

- 2026-03-25: validate-create-story 修订 —— 固定 Header 集成契约（保留 `Header()` + 在其下插入 `ThreeQuestionHeader()`）；明确 grouped query 仍需维护 `story_count` 与 `DashboardScreen.update_content(...)` 现有数据流；修正 `seconds_ago` 的计算时机避免每轮恒为 `0s`；禁止用“无 running + 无审批”伪造 `⏸ 已暂停`；补齐 `<100` 列 degraded 模式仍显示 minimal header；去除易漂移的行号引用

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6 (1M context)

### Debug Log References

- 全 1020 测试通过（0 failures），包含 18 新单元测试 + 12 新集成测试
- ruff check + ruff format 通过

### Completion Notes List

- ✅ ThreeQuestionHeader Widget 完整实现：四区域渲染 + 可判定状态逻辑（空闲/正常/异常/审批）
- ✅ 三级响应式 display_mode（full/compact/minimal）独立于 layout_mode 断点
- ✅ ATOApp 数据集成：GROUP BY status 查询保持 story_count 向后兼容
- ✅ seconds_ago 正确计算：先基于上次刷新时间求差值，再更新时间戳
- ✅ RICH_COLORS 统一在 theme.py 维护，TCSS 变量 → Rich hex 颜色映射
- ✅ TCSS 样式：height 1, dock top, background $surface
- ✅ 不伪造 ⏸ 已暂停（running=0 && error=0 显示"空闲"）
- ✅ 保留 Header()，ThreeQuestionHeader 在其下方，不破坏 6.1a/6.1b 测试契约

### File List

- `src/ato/tui/widgets/three_question_header.py` — 新建：ThreeQuestionHeader Widget
- `src/ato/tui/widgets/__init__.py` — 修改：导出 ThreeQuestionHeader
- `src/ato/tui/app.py` — 修改：compose() 添加 ThreeQuestionHeader、_load_data() 扩展 GROUP BY 查询、running_count/error_count reactive 属性、_update_dashboard() 推送 header 数据、_apply_header_mode() 宽度转发
- `src/ato/tui/theme.py` — 修改：新增 RICH_COLORS 映射常量
- `src/ato/tui/app.tcss` — 修改：添加 ThreeQuestionHeader 样式
- `tests/unit/test_three_question_header.py` — 新建：18 个单元测试
- `tests/integration/test_tui_pilot.py` — 修改：6 个 ThreeQuestionHeader 集成测试
- `tests/integration/test_tui_responsive.py` — 修改：6 个 display_mode 响应式测试

### Change Log

- 2026-03-25: Story 6.2a 完整实现 — ThreeQuestionHeader Widget + 响应式适配 + ATOApp 数据集成 + TCSS 样式 + 30 个新测试
