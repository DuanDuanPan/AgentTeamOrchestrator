# Story 6.1b: 操作者可看到统一的深色主题和响应式布局

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a 操作者,
I want TUI 有一致的深色主题、三重状态编码和响应式布局,
So that 视觉体验专业统一，不同终端宽度都可用。

## Acceptance Criteria

1. **AC1: 9 个语义色彩 TCSS 变量** (UX-DR7)
   - Given TCSS 主题文件 `tui/app.tcss`
   - When 加载主题
   - Then 包含 9 个语义色彩变量：
     - `$success: #50fa7b` — 运行中、已完成、已通过
     - `$warning: #f1fa8c` — 等待人类决策、超时警告
     - `$error: #ff5555` — 失败、blocked、冻结
     - `$info: #8be9fd` — 活跃任务、进行中、提示信息
     - `$accent: #bd93f9` — 当前选中项、焦点元素、交互高亮
     - `$muted` — 次要信息、已归档、时间戳；优先保持 Dracula muted 语义，但最终取值必须以对比度测试通过为准（`#6272a4` 当前不达标，需上调到可访问变体，例如 `#8390b7`）
     - `$text: #f8f8f2` — 正文、标题
     - `$background: #282a36` — 主背景
     - `$surface: #44475a` — 面板、卡片、选中行背景
   - And 除 `$surface` 背景色外，所有会承载正文/图标/标签的语义色在 `$background` 上对比度 ≥ 4.5:1 (WCAG AA)

2. **AC2: 三重状态编码系统** (UX-DR8)
   - Given 状态编码系统
   - When 展示任何状态信息
   - Then 使用展示语义层的颜色 + Unicode 图标 + 文字标签三重编码：
     - `●` running (success/绿)
     - `◐` active (info/青)
     - `◆` awaiting (warning/琥珀)
     - `✖` failed (error/红)
     - `✔` done (success/绿)
     - `⏸` frozen (error/红)
     - `ℹ` info (muted/灰)
   - And 必须通过映射函数把现有 `StoryStatus` / `ApprovalStatus` / `TaskStatus` 转换为这些展示语义，不能把 `running/active/...` 当作数据库状态值

3. **AC3: 响应式布局断点** (UX-DR9)
   - Given 终端 resize 事件
   - When 终端宽度变化
   - Then 实时响应：
     - ≥140 列：三面板 lazygit 风格布局（左列表 + 右详情）
     - 100-139 列：Tab 视图（`TabbedContent` 切换）
     - <100 列：降级警告 + CLI-only 模式提示
   - And 切换时保持当前选中状态和焦点位置

4. **AC4: 面板结构与视觉层级**
   - Given TUI 在宽终端（≥140 列）
   - When 渲染主布局
   - Then 显示：固定顶栏（Header）+ 主区域（左面板 + 右面板）+ 固定底栏（Footer）
   - And 焦点面板有 `$accent` 色边框高亮
   - And 非焦点面板边框为 `$muted` 色

5. **AC5: 文字层级与间距系统**
   - Given TCSS 样式规则
   - When 渲染文字
   - Then 按层级区分：H1 全大写粗体 `$accent`、H2 粗体 `$text`、正文常规 `$text`、次要 `$muted`、强调 粗体+语义色
   - And 间距遵循终端字符系统：xs=0行/1字符、sm=1行/2字符、md=2行/4字符

## Tasks / Subtasks

- [ ] Task 1: 完整 TCSS 主题系统 (AC: #1, #5)
  - [ ] 1.1 在 `src/ato/tui/app.tcss` 扩展 9 个语义色彩变量（当前仅有 3 个：$background/$surface/$text），新增 $success/$warning/$error/$info/$accent/$muted；`$muted` 必须选择能通过对比度测试的可访问变体，不要直接照抄 `#6272a4`
  - [ ] 1.2 添加全局排版规则：Screen 背景色、默认文字色、焦点/选中样式
  - [ ] 1.3 添加面板基础样式：边框色（默认 `$muted`，焦点 `$accent`）、padding/margin 间距
  - [ ] 1.4 添加文字层级 CSS 类：`.h1`（大写粗体 accent）、`.h2`（粗体 text）、`.body`（常规 text）、`.secondary`（muted）、`.emphasis`（粗体+语义色）
  - [ ] 1.5 编写 WCAG 对比度验证测试：`tests/unit/test_theme_contrast.py`——使用纯 Python 计算 `$success/$warning/$error/$info/$accent/$muted/$text` 与 `$background` 的相对亮度比值 ≥ 4.5:1；若 Dracula 原值不满足，则以测试通过后的可访问变体为准

- [ ] Task 2: 三重状态编码模块 (AC: #2)
  - [ ] 2.1 在 `src/ato/tui/theme.py` 创建状态编码常量模块
  - [ ] 2.2 定义 `StatusCode` 数据类：`icon: str`、`color_var: str`、`label: str`
  - [ ] 2.3 定义 `STATUS_CODES: dict[str, StatusCode]` 映射（展示语义：running/active/awaiting/failed/done/frozen/info）
  - [ ] 2.4 提供两层 helper：`map_*_to_visual_status(...)` 负责把现有 `stories.status/current_phase`、`approvals.status`、必要时 `tasks.status` 映射到展示语义；`format_status(visual_status: str) -> StatusCode` 负责返回 icon/color/label
  - [ ] 2.5 在 `tests/unit/test_theme.py` 编写测试：验证所有展示语义完整性、无缺失图标/颜色/标签，且当前 `StoryStatus` / `ApprovalStatus` / `TaskStatus` 均能映射到合法展示语义

- [ ] Task 3: 响应式布局引擎 (AC: #3, #4)
  - [ ] 3.1 在 `src/ato/tui/app.py` 的 `ATOApp` 中添加 `layout_mode: reactive[str]` 属性（值："three-panel" / "tabbed" / "degraded"）
  - [ ] 3.2 实现 `on_resize(self, event: events.Resize)` 方法：根据 `event.size.width` 判断断点并更新 `layout_mode`
  - [ ] 3.3 实现 `watch_layout_mode(self, new_mode: str)` 方法：只负责把模式变化转发给已挂载的 `DashboardScreen`（例如 `set_layout_mode(new_mode)`），不要让 `ATOApp` 自己持有三面板/Tab 结构
  - [ ] 3.4 保持 `ATOApp.compose()` 继续渲染 `Header + DashboardScreen + Footer`；响应式内部布局全部由 `DashboardScreen` 管理
  - [ ] 3.5 在 `tests/integration/test_tui_responsive.py` 编写 Textual `pilot` 响应式测试：用 `app.run_test(size=(cols, rows))` 模拟不同终端宽度（80/120/150/200 列），验证正确的布局模式激活
  - [ ] 3.6 响应式测试需覆盖 resize 后模式切换时，当前展示数据和焦点上下文不会被重置

- [ ] Task 4: DashboardScreen 升级为布局容器 (AC: #3, #4)
  - [ ] 4.1 将 `src/ato/tui/dashboard.py` 的 `DashboardScreen` 从单行 `Static` 重构为复合 Widget/容器；本 Story 不要把它升格为 Textual `Screen`，以保持 `ATOApp.compose()` 可直接挂载
  - [ ] 4.2 `compose()` 中创建 `ContentSwitcher` 根容器，内部包含 `three-panel` / `tabbed` / `degraded` 三种子布局
  - [ ] 4.3 三面板模式（≥140 列）：`Horizontal(left_panel, right_panel)`；左面板 `width: 40%`，右面板 `width: 60%`（180+列时 30%/70%）
  - [ ] 4.4 左面板使用 `Static` 占位（显示 story 计数文字，后续 6.2b 替换为 DataTable）；右面板上下分区：右上 `Static`（联动详情占位）+ 右下 `Static`（操作区域占位）
  - [ ] 4.5 Tab 模式（100-139 列）：用 `TabbedContent` 包含多个 `TabPane`，Tab 标签：`[1]审批` `[2]Stories` `[3]成本` `[4]日志`
  - [ ] 4.6 降级模式（<100 列）：显示 `Static` 警告文字："终端宽度不足 100 列，请扩大终端窗口或使用 CLI 命令"
  - [ ] 4.7 保留并扩展 `DashboardScreen.update_content()`，让现有 reactive 数据仍能刷新占位内容；新增 `set_layout_mode()` 或等价接口承接 `ATOApp.watch_layout_mode`
  - [ ] 4.8 更新现有 `tests/integration/test_tui_pilot.py` 测试以适配新的 `DashboardScreen` 结构

- [ ] Task 5: 焦点管理与键盘导航 (AC: #3, #4)
  - [ ] 5.1 实现 `Tab`/`Shift-Tab` 在面板间循环切换焦点
  - [ ] 5.2 焦点面板边框动态切换：获得焦点时 `$accent`，失去焦点时 `$muted`
  - [ ] 5.3 窄终端 Tab 模式下数字键 `1`/`2`/`3`/`4` 切换 Tab
  - [ ] 5.4 `q` 退出和 `ESC` 返回上层导航保持不变
  - [ ] 5.5 在 `tests/integration/test_tui_responsive.py` 增加键盘导航测试

## Dev Notes

### 核心架构约束

- **Textual ≥2.0**——使用 Textual CSS (`TCSS`) 定义样式，CSS 文件与 Python 分离
- **Dracula 色板变体**——9 个语义色基于 Dracula 调色板，终端用户最熟悉的深色主题
- **三重编码原则**（UX-DR8）——所有状态必须同时用 颜色 + Unicode 图标 + 文字标签 编码，确保色盲友好
- **CSS 文件 `tui/app.tcss`** 是全局主题唯一入口——所有颜色变量在此定义，Python 代码中不 hardcode 颜色值
- **Textual 生命周期**——数据加载在 `on_mount()` 而非 `__init__()`

### 状态语义映射约束

- 数据库存储状态保持现有 schema：`StoryStatus = backlog/planning/ready/in_progress/review/uat/done/blocked`，`ApprovalStatus = pending/approved/rejected`，`TaskStatus = pending/running/paused/completed/failed`
- `theme.py` 中的 `running/active/awaiting/failed/done/frozen/info` 是**展示语义层**，不是数据库状态值
- 展示层必须通过映射 helper 把领域状态转换为统一视觉语义；不要修改 SQLite 中已有状态枚举，也不要把展示语义写回 DB

### 响应式断点设计

| 断点 | 宽度 | 布局模式 | 组件 | 场景 |
|------|------|---------|------|------|
| 宽终端 | 180+ 列 | three-panel (30%/70%) | `Horizontal` + panels | 宽屏/超宽屏 |
| 标准终端 | 140-179 列 | three-panel (40%/60%) | `Horizontal` + panels | 常规本地开发 |
| 窄终端 | 100-139 列 | tabbed | `TabbedContent` + `TabPane` | SSH 远程、分屏 |
| 低于最小 | <100 列 | degraded | `Static` 警告 | 提示扩大终端 |

**关键行为：**
- `on_resize` 实时响应 resize 事件（Textual `events.Resize`）
- 切换布局时保持 `DashboardScreen` 的数据状态（reactive 属性不重置）
- `ThreeQuestionHeader` 和 `Footer` 在所有断点下始终可见
- 窄终端 `ThreeQuestionHeader` 自动压缩（后续 Story 6.2a 实现具体内容）

### 面板焦点管理

- `Tab` / `Shift-Tab` 在面板间循环切换焦点
- 焦点面板有 `$accent` 色边框高亮——TCSS `border: solid $accent` when `:focus-within`
- 非焦点面板边框为 `$muted` 色——TCSS `border: solid $muted`
- 左面板选择变化时，右面板自动联动更新（后续 Story 6.2b 实现具体数据绑定）

### 文字层级系统

| 层级 | TCSS 类 | 格式 | 用途 |
|------|---------|------|------|
| H1 | `.h1` | 全大写 + 粗体 + `$accent` 色 | 区域标题 |
| H2 | `.h2` | 粗体 + `$text` 色 | 面板标题 |
| 正文 | `.body` | 常规 + `$text` 色 | 摘要文字 |
| 次要 | `.secondary` | 常规 + `$muted` 色 | 时间戳 |
| 强调 | `.emphasis` | 粗体 + 语义色 | 状态标签 |

### WCAG 对比度验证

语义色在 `#282a36` 背景上的实测对比度（使用 WCAG 相对亮度公式）：

| 颜色 | 值 | 预计比值 | 符合 AA |
|------|------|---------|---------|
| $success | #50fa7b | ~10.38:1 | ✔ |
| $warning | #f1fa8c | ~12.74:1 | ✔ |
| $error | #ff5555 | ~4.53:1 | ✔ (边界) |
| $info | #8be9fd | ~10.29:1 | ✔ |
| $accent | #bd93f9 | ~5.90:1 | ✔ |
| $muted（Dracula 原值） | #6272a4 | ~3.03:1 | ✖ |
| $muted（可访问变体示例） | #8390b7 | ~4.50:1 | ✔ |
| $text | #f8f8f2 | ~13.36:1 | ✔ |
| $surface | #44475a | — | 用作背景 |

**注意**：上游 UX 文档给出的 Dracula `$muted (#6272a4)` 与 WCAG AA 要求冲突，且此前建议的 `#6878ad` 仍不达标。本 Story 以 `tests/unit/test_theme_contrast.py` 为准，允许在保持同色相语义的前提下微调 `$muted` 到可访问变体（例如 `#8390b7`）。

### 本 Story 交付范围

| 交付 | 说明 |
|------|------|
| ✅ 完整 TCSS 主题 | 9 个语义色彩变量 + 文字层级 + 面板样式 |
| ✅ 状态编码模块 | `theme.py` 提供 StatusCode 映射和格式化函数 |
| ✅ 响应式布局 | `on_resize` + `ContentSwitcher` + 三种模式 |
| ✅ DashboardScreen 升级 | 从单行 `Static` 改为复合 widget 容器 + 面板结构占位 |
| ✅ 焦点管理 | Tab/Shift-Tab 切换 + 边框高亮 |
| ✅ 对比度测试 | 纯 Python 验证 WCAG AA |

### 本 Story 不包含的内容（后续 Story 实现）

| 功能 | 目标 Story |
|------|-----------|
| ThreeQuestionHeader Widget 具体内容与数据绑定 | 6.2a |
| DashboardScreen 三面板数据填充、story 列表排序 | 6.2b |
| 审批交互（y/n 快捷键、ApprovalCard） | 6.3a |
| 异常审批面板 | 6.3b |
| Story 详情钻入导航 | 6.4 |
| 搜索面板与 Tab 模式完善 | 6.5 |

本 Story 只需交付：**完整主题系统 + 状态编码模块 + 响应式布局框架 + DashboardScreen 面板结构占位 + 焦点管理**。

### Project Structure Notes

```
src/ato/tui/
├── __init__.py          # 已存在（空模块）— 不修改
├── app.py               # ← 修改：添加 reactive layout_mode + on_resize + watch_layout_mode（仅负责模式切换与转发）
├── app.tcss             # ← 重写：完整 9 色主题 + 文字层级 + 面板/焦点样式
├── dashboard.py         # ← 重写：单行 Static → 复合 widget 容器 + ContentSwitcher + 三面板/Tab/降级结构
├── theme.py             # ← 新建：StatusCode 数据类 + STATUS_CODES 映射 + domain→visual status helper
├── approval.py          # 已存在（空占位）— 不修改
├── story_detail.py      # 已存在（空占位）— 不修改
└── widgets/
    └── __init__.py      # 已存在（空占位）— 不修改
```

测试文件：
- `tests/unit/test_theme_contrast.py`（新建）— WCAG 对比度验证
- `tests/unit/test_theme.py`（新建）— StatusCode 完整性测试
- `tests/integration/test_tui_responsive.py`（新建）— Textual `pilot` 响应式布局测试
- `tests/integration/test_tui_pilot.py`（修改）— 适配 DashboardScreen 结构变化

**SCHEMA_VERSION 不变** — 无需 DB 迁移，本 Story 不涉及数据库变更。

### 需要复用的现有代码

- **`get_connection(db_path)`** — `src/ato/models/db.py` — 标准连接工厂（TUI 数据刷新用）
- **`ATOApp` 现有 reactive 属性** — `src/ato/tui/app.py` — story_count / pending_approvals / today_cost_usd / last_updated
- **`DashboardScreen.update_content()`** — 需要保留并扩展，避免破坏 6.1a 已有数据刷新路径
- **现有 `app.tcss`** — 保留 `$background/$surface/$text` 三个变量（值不变），在此基础上扩展

### 不要重新实现

- ❌ 不要修改 `ATOApp` 的 SQLite 连接/轮询/nudge 逻辑 — Story 6.1a 已完成
- ❌ 不要创建新的 CSS 文件 — 所有主题在 `app.tcss` 一个文件中
- ❌ 不要在 Python 代码中 hardcode 颜色值 — 统一通过 TCSS 变量引用
- ❌ 不要把 `running/active/...` 这些展示语义写回 SQLite，也不要替换现有 `StoryStatus` / `ApprovalStatus` / `TaskStatus`
- ❌ 不要把 `DashboardScreen` 升格为 Textual `Screen` 后仍在 `ATOApp.compose()` 中直接 `yield`
- ❌ 不要实现 ThreeQuestionHeader 的数据绑定 — Story 6.2a 的职责
- ❌ 不要实现 story 列表数据填充 — Story 6.2b 的职责
- ❌ 不要添加审批交互逻辑 — Story 6.3a 的职责

### 编码约定

- **Pydantic**: 继承 `_StrictBase` (strict=True, extra="forbid")
- **TCSS**: Textual CSS 语法，变量用 `$var` 引用，类选择器用 `.class`
- **Python dataclass**: `StatusCode` 用 `@dataclass(frozen=True, slots=True)`
- **日志**: `structlog.get_logger()`，snake_case 事件名
- **TUI 测试**: Textual `pilot` + mock SQLite（`tmp_path` fixture 创建临时 DB）
- **对比度测试**: 纯 Python 计算 WCAG 相对亮度，不依赖外部库

### Textual 响应式实现参考

```python
from textual import events
from textual.css.query import NoMatches

class ATOApp(App[None]):
    layout_mode: reactive[str] = reactive("three-panel")

    def on_resize(self, event: events.Resize) -> None:
        width = event.size.width
        if width >= 140:
            self.layout_mode = "three-panel"
        elif width >= 100:
            self.layout_mode = "tabbed"
        else:
            self.layout_mode = "degraded"

    def watch_layout_mode(self, new_mode: str) -> None:
        try:
            dashboard = self.query_one(DashboardScreen)
        except NoMatches:
            return
        dashboard.set_layout_mode(new_mode)
```

### WCAG 对比度计算参考

```python
def relative_luminance(hex_color: str) -> float:
    """WCAG 2.1 相对亮度计算。"""
    r, g, b = int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16)
    def linearize(c: int) -> float:
        s = c / 255.0
        return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4
    return 0.2126 * linearize(r) + 0.7152 * linearize(g) + 0.0722 * linearize(b)

def contrast_ratio(fg: str, bg: str) -> float:
    l1 = relative_luminance(fg)
    l2 = relative_luminance(bg)
    lighter, darker = max(l1, l2), min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)
```

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 6.1b] — 完整 AC 定义
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md#Color System] — 9 语义色、Dracula 色板、对比度要求
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md#Typography System] — 文字层级
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md#Spacing & Layout Foundation] — 面板布局、lazygit 三面板
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md#Responsive Strategy] — 4 断点、resize 行为
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md#Accessibility Strategy] — WCAG AA、三重编码、焦点指示
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md#Design Direction Decision] — 方向 C lazygit + 方向 D Tab 降级
- [Source: _bmad-output/planning-artifacts/architecture.md#Textual TUI Architecture Pattern] — MVP Screens、CSS 分离、reactive 属性
- [Source: _bmad-output/planning-artifacts/architecture.md#TUI Module Structure] — 文件结构
- [Source: _bmad-output/planning-artifacts/prd.md#NFR3] — TUI 状态刷新 ≤5s，单次渲染 ≤500ms
- [Source: https://textual.textualize.io/guide/screens/] — Screen 通过 screen API 管理；App 中 compose/mount 的 widget 会进入默认 screen
- [Source: https://textual.textualize.io/guide/testing/] — Textual pilot 测试应使用 `run_test(size=(w, h))`
- [Source: src/ato/tui/app.py] — 现有 ATOApp 类
- [Source: src/ato/tui/app.tcss] — 现有最小 TCSS（3 个变量）
- [Source: src/ato/tui/dashboard.py] — 现有 DashboardScreen(Static) 占位

### Previous Story Intelligence

**从 Story 6.1a 学到的关键模式：**
- `ATOApp` 已有 4 个 reactive 属性（story_count / pending_approvals / today_cost_usd / last_updated）驱动 UI 更新
- `DashboardScreen` 当前继承 `Static`，通过 `update_content()` 接收数据——本 Story 需要把它升级为复合 widget 容器，但仍保持可被 `ATOApp.compose()` 直接挂载
- `app.tcss` 当前仅有 3 个变量（$background/$surface/$text）和 2 条规则——本 Story 完全扩展
- `compose()` 当前布局：`Header` + `DashboardScreen()` + `Footer`——本 Story 保持该骨架不变，只把内部布局做成响应式
- `_load_data()` 和 `refresh_data()` 不需要修改——数据加载逻辑与布局解耦
- `write_approval()` 不需要修改——写入路径与主题/布局无关
- 现有 19 个测试（13 pilot + 6 CLI unit）——修改 DashboardScreen 后需要更新部分 pilot 测试

**从 Story 6.1a Code Review 学到的关键模式：**
- PID 每次重新读取（`_resolve_orchestrator_pid`）——不缓存
- `write_approval` 有 `WHERE status = 'pending'` 并发保护
- 测试使用 `monkeypatch.chdir(tmp_path)` 隔离 CWD

### Git Intelligence

最近提交聚焦于 Epic 3、5、6 并行推进：
- `d8656bd` feat: Story 6.1a TUI 启动与 SQLite 连接完整实现
- `14b0a6d` Merge story 6.1a: TUI 启动与 SQLite 连接完整实现
- TUI 骨架已就绪：ATOApp + DashboardScreen(Static) + 最小 TCSS + 轮询 + nudge

所有 TUI 前置基础设施已就绪。本 Story 在 6.1a 基础上扩展主题和布局，不涉及数据层变更。

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

### File List

### Change Log

- 2026-03-25: create-story 创建 — 基于 epics/architecture/PRD/UX-spec/前置 story 6.1a 分析生成完整开发上下文
- 2026-03-25: validate-create-story 修订 —— 解决 `$muted` 与 WCAG 约束冲突；移除 `DashboardScreen` 升格为 `Screen` 的错误实现方向；统一 ATOApp/DashboardScreen 响应式职责；把 Textual pilot 示例改为 `run_test(size=...)`；补齐领域状态到展示语义的映射约束
