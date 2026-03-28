# Story 6.5: 搜索面板与响应式布局完善

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a 操作者,
I want 通过 `/` 搜索快速跳转到任意 story、审批或关键视图,
so that 在多 story 场景下导航效率不降。

## Acceptance Criteria

1. **AC1: `/` 激活搜索面板** (UX-DR16)
   - **Given** 用户在任意视图按 `/`
   - **When** 搜索面板激活
   - **Then** 显示 Input 搜索框，支持 story ID 直达、审批跳转、TUI 内部导航目标搜索（如 Tab / 视图切换）
   - **And** 输入时模糊匹配实时过滤结果

2. **AC2: 搜索结果跳转**
   - **Given** 搜索结果列表
   - **When** 用户用 ↑↓ 选择并按 Enter
   - **Then** 跳转到对应 story / 审批 / TUI 内部视图目标（左面板选中 + 右面板联动，或切换到目标 Tab）
   - **And** 不执行外部 CLI 命令
   - **And** 搜索面板自动关闭

3. **AC3: ESC 取消搜索**
   - **Given** 搜索面板处于激活状态
   - **When** 用户按 ESC
   - **Then** 取消搜索，返回之前视图，焦点恢复到之前位置

4. **AC4: 窄终端 Tab 视图模式** (UX-DR9)
   - **Given** 终端宽度 100-139 列
   - **When** Tab 视图模式渲染
   - **Then** 显示 Tab 切换：[1]审批 [2]Stories [3]成本 [4]日志
   - **And** ThreeQuestionHeader 压缩为最简模式（仅图标+数字）
   - **Note:** Tab 视图基础功能已在 6.1b 实现，本 Story 需完善 [3]成本 和 [4]日志 Tab 的实际内容

5. **AC5: TUI 与 Orchestrator 进程独立**
   - **Given** TUI 与 Orchestrator 的进程关系
   - **When** TUI 崩溃
   - **Then** Orchestrator 继续后台运行不受影响
   - **And** 重新运行 `ato tui` 即可恢复
   - **Note:** 进程隔离已在架构层实现（独立进程 + SQLite 通信），本 AC 主要确保本 Story 的改动不破坏此契约

6. **AC6: 响应式布局打磨**
   - **Given** 终端 resize 事件
   - **When** 终端宽度在断点间切换
   - **Then** 搜索面板在所有布局模式下均可用
   - **And** 搜索结果列表适配当前终端宽度
   - **And** 切换时搜索状态保持（如正在搜索不因 resize 中断）

## Tasks / Subtasks

- [ ] Task 1: SearchPanel 组件创建 (AC: #1, #3, #6)
  - [ ] 1.1 创建 `src/ato/tui/widgets/search_panel.py`
  - [ ] 1.2 实现 `Input` 搜索框 + 结果列表容器（`VerticalScroll` 或 `OptionList`）
  - [ ] 1.3 实现搜索面板的显示/隐藏切换（overlay / mount toggle；动画仅在不增加焦点与测试复杂度时实现）
  - [ ] 1.4 ESC 键关闭搜索面板并恢复焦点

- [ ] Task 2: 搜索引擎实现 (AC: #1, #2)
  - [ ] 2.1 实现模糊匹配算法（story ID 前缀匹配 + story title/phase 子串匹配）
  - [ ] 2.2 支持搜索类型：story ID 直达（输入 `"6.3b"` / `"story-007"` / `"007"` 等原始 ID 形式）、审批项跳转、TUI 内部视图目标（如 approvals / stories / cost / log Tab）
  - [ ] 2.3 实时过滤：每次 Input.Changed 事件触发重新过滤
  - [ ] 2.4 结果排序：精确匹配 > 前缀匹配 > 子串匹配；同优先级内 story 结果对齐 `sort_stories_by_status()` / `VISUAL_STATUS_SORT_ORDER`，approval 结果对齐 `_sort_approvals()`

- [ ] Task 3: 搜索结果交互 (AC: #2, #3)
  - [ ] 3.1 结果列表支持 ↑↓ 键盘导航
  - [ ] 3.2 Enter 选中结果：定位到对应 story / 审批时走现有选择刷新链路（更新 `_selected_item_id` + `_selected_index`，并调用 `_sync_selected_story_id()`、`_highlight_selected()`、`_update_detail_panel()`、`_update_action_panel()`）；内部视图目标委托到现有切页动作
  - [ ] 3.3 Enter 后自动关闭搜索面板，焦点回到左面板
  - [ ] 3.4 空搜索状态提示（"输入 story ID 或关键词搜索"）
  - [ ] 3.5 无匹配结果提示（"未找到匹配项"）

- [ ] Task 4: DashboardScreen `/` 键集成 (AC: #1, #3)
  - [ ] 4.1 在 DashboardScreen BINDINGS 增加 `/` → `action_search`
  - [ ] 4.2 `/` 激活搜索面板（mount overlay 或 toggle visibility）
  - [ ] 4.3 搜索面板激活时显式短路现有全局快捷键路径：`ATOApp` 的 `1-9` Tab 绑定、`DashboardScreen.on_key()` 异常审批数字键、以及 y/n/d/Enter 等审批/导航动作，避免输入查询时误切页或误提交审批
  - [ ] 4.4 ESC 关闭后恢复上述快捷键与原焦点上下文
  - [ ] 4.5 确保 6.4 详情页模式下 `/` 也可用（先返回主屏再搜索，或直接覆盖搜索）

- [ ] Task 5: 成本 Tab 内容完善 (AC: #4)
  - [ ] 5.1 实现 [3]成本 Tab 内容：按 story 聚合的成本表（story ID + 总成本 + 调用次数），优先复用现有 `get_cost_by_story()` helper
  - [ ] 5.2 底部显示今日总成本和累计总成本
  - [ ] 5.3 在 `ATOApp._load_data()` 中补充 story 级成本聚合/调用次数快照，并继续复用 `_story_costs` 给现有列表/详情视图

- [ ] Task 6: 日志 Tab 内容完善 (AC: #4)
  - [ ] 6.1 实现 [4]日志 Tab 内容：最近事件列表（基于当前 schema 中真实存在的 `tasks` + `approvals` 数据源组合，不依赖不存在的 `event_log` 表）
  - [ ] 6.2 显示格式：时间戳 + 事件类型 + story ID + 摘要
  - [ ] 6.3 自动滚动到最新事件

- [ ] Task 7: 响应式布局完善 (AC: #6)
  - [ ] 7.1 搜索面板在 three-panel 模式下作为顶部 overlay
  - [ ] 7.2 搜索面板在 tabbed 模式下作为全宽 overlay
  - [ ] 7.3 搜索面板宽度适配终端宽度（不超过可用宽度）
  - [ ] 7.4 resize 过程中搜索状态保持

- [ ] Task 8: TCSS 样式 (AC: #1, #4, #6)
  - [ ] 8.1 SearchPanel 样式（overlay 背景 + 搜索框 + 结果列表）
  - [ ] 8.2 搜索结果项样式（选中高亮 + 类型图标）
  - [ ] 8.3 成本 Tab / 日志 Tab 内容样式
  - [ ] 8.4 搜索面板在不同断点下的样式适配

- [ ] Task 9: widgets 模块导出
  - [ ] 9.1 在 `src/ato/tui/widgets/__init__.py` 导出 SearchPanel

- [ ] Task 10: 单元测试
  - [ ] 10.1 SearchPanel 渲染测试（搜索框 + 结果列表）
  - [ ] 10.2 模糊匹配算法测试（精确匹配、前缀匹配、子串匹配、无匹配）
  - [ ] 10.3 结果排序测试（匹配优先级 + 现有 visual status / approval 排序语义）
  - [ ] 10.4 成本 Tab 内容渲染测试
  - [ ] 10.5 日志 Tab 内容渲染测试

- [ ] Task 11: 集成测试
  - [ ] 11.1 `/` 键激活搜索面板
  - [ ] 11.2 输入搜索词 → 结果实时过滤
  - [ ] 11.3 搜索输入中的 `1-9` / `y` / `n` / `d` 不触发 Tab 切换或审批提交
  - [ ] 11.4 Enter 跳转到 story / 审批 / 目标视图 + 面板关闭
  - [ ] 11.5 ESC 取消搜索 + 焦点恢复
  - [ ] 11.6 搜索面板在 three-panel 和 tabbed 模式下均可用
  - [ ] 11.7 成本 Tab 数据正确显示
  - [ ] 11.8 日志 Tab 数据正确显示
  - [ ] 11.9 全量回归通过

## Dev Notes

### 核心架构约束

- **Textual ≥2.0**：Widget 继承 `Widget`，`render()` 返回 `Rich.Text` 或 `RenderableType`
- **数据由 ATOApp 提供**：SearchPanel 不自行创建 SQLite 连接，数据通过 ATOApp push
- **TUI↔Orchestrator 解耦**：tui/ 不依赖 core.py，只通过 SQLite 读数据
- **CSS 与 Python 分离**：所有样式在 `app.tcss`
- **push-based 数据流**：ATOApp → DashboardScreen → Widget
- **进程隔离**：TUI 是独立进程，通过 SQLite + nudge 与 Orchestrator 通信；本 Story 不改变此架构
- **全局快捷键已存在**：`ATOApp.BINDINGS` 已处理 `1-9`，`DashboardScreen.on_key()` 已处理异常审批数字键；搜索激活时必须显式短路这些路径，不能仅依赖 `Input` focus

### 已存在关键组件（复用，不重建）

| 组件 | 文件 | 用途 |
|------|------|------|
| `ATOApp._load_data()` | `src/ato/tui/app.py` | 数据轮询，stories/costs 已有 |
| `DashboardScreen` | `src/ato/tui/dashboard.py` | 统一选择管理、布局切换 |
| `_selected_item_id` | `dashboard.py` | 统一选择状态（"story:{id}" / "approval:{id}"） |
| `_sorted_item_ids` | `dashboard.py` | 排序后的全量列表，搜索结果需从此过滤 |
| `DashboardScreen.on_key()` | `src/ato/tui/dashboard.py` | three-panel 模式下异常审批数字键路由；搜索激活时必须短路 |
| `ContentSwitcher` | `dashboard.py` | 三模式响应式切换（three-panel / tabbed / degraded） |
| `TabbedContent` | `dashboard.py` | tabbed 模式 [1]-[4] Tab 容器 |
| `ATOApp.action_switch_tab()` | `src/ato/tui/app.py` | app 级 `1-9` 切页入口；搜索输入期间必须禁止误触发 |
| `_story_costs` | `app.py` | 每 story 累计成本，成本 Tab 直接复用 |
| `get_cost_by_story()` | `src/ato/models/db.py` | 已返回 `story_id + total_cost_usd + call_count`，成本 Tab 优先复用 |
| `Textual Input` | Textual 内置 | 搜索输入框组件 |
| `Textual OptionList` | Textual 内置 | 可选项列表，适合搜索结果 |
| `sort_stories_by_status()` | `tui/theme.py` | story 排序逻辑 |
| `VISUAL_STATUS_SORT_ORDER` | `tui/theme.py` | 状态排序优先级 |

### 搜索面板实现策略

**推荐方案：Textual Screen overlay / mount overlay 模式**

Textual 提供多种 overlay 方案：
1. **`Screen` modal push** — `app.push_screen(SearchScreen)` 覆盖当前屏幕
2. **Widget mount overlay** — 在 DashboardScreen compose 中预置隐藏的 SearchPanel，`/` 切换 display
3. **Textual `Input` with suggestions** — 使用 Textual 内置的 `Input` + `OptionList` 组合

**推荐方案 2（Widget mount overlay）**，原因：
- 搜索面板需要访问 DashboardScreen 的 story 列表数据
- 不需要 Screen push/pop 的复杂焦点管理
- 与 6.4 详情页 ContentSwitcher 模式一致
- 可以在 compose 中预置，`/` 键 toggle `display: none ↔ block`
- 但必须补一个显式 `search_active` 状态，统一门控 `ATOApp` 与 `DashboardScreen` 的现有全局按键处理，不能假设 `Input` focus 会自动屏蔽所有事件路径

**搜索面板布局：**
```
┌─────────────────────────────────────────────────────────┐
│ ThreeQuestionHeader                                     │
├─────────────────────────────────────────────────────────┤
│ 🔍 搜索: [________________]                             │  ← SearchPanel overlay
│   ◐ story-002  reviewing   Review 第 2 轮              │  ← 匹配结果
│   ◆ story-003  awaiting    Merge 授权                  │
│   ✔ story-001  done        已完成                      │
├─────────────────────────────────────────────────────────┤
│ [↑↓]选择 [Enter]跳转 [ESC]取消                         │
└─────────────────────────────────────────────────────────┘
```

### 模糊匹配算法

```python
def fuzzy_match(query: str, items: list[SearchableItem]) -> list[SearchResult]:
    """
    匹配优先级：
    1. story ID 精确匹配（"story-007" 或 "007"）
    2. story ID 前缀匹配（"story-0" 匹配 story-001, story-002）
    3. phase/title 子串匹配（"review" 匹配 reviewing 阶段的 story）
    4. approval story ID 匹配（审批关联的 story）

    排序规则：
    - 精确匹配 > 前缀匹配 > 子串匹配
    - 同优先级内 story 结果按 `sort_stories_by_status()` / `VISUAL_STATUS_SORT_ORDER`
    - approval 结果保持与左面板一致的 `_sort_approvals()` 语义
    """
```

可搜索字段：
| 数据源 | 可搜索字段 | 搜索命中显示 |
|--------|-----------|-------------|
| stories | story_id, title, current_phase, status | `{icon} {story_id}  {phase}  {title}` |
| approvals | story_id, approval_type | `◆ {story_id}  {type}  {summary}` |

### 成本 Tab 内容设计

```
┌──────────────────────────────────────────┐
│  [3] 成本概览                            │
│                                          │
│  Story          调用次数    累计成本      │
│  ─────────────────────────────────────── │
│  story-002      12         $3.80         │
│  story-004       8         $2.40         │
│  story-007       6         $2.60         │
│  story-001      15         $1.90         │
│  ─────────────────────────────────────── │
│  今日: $12.50    累计: $45.20            │
└──────────────────────────────────────────┘
```

数据源：已有 `ATOApp._story_costs: dict[str, float]` 和 `today_cost_usd`。
调用次数优先复用现有 `ato.models.db.get_cost_by_story()` 返回的 `call_count`，避免重复造 SQL / helper。

### 日志 Tab 内容设计

```
┌──────────────────────────────────────────┐
│  [4] 事件日志                            │
│                                          │
│  14:32:05  story-002  reviewing → fixing │
│  14:31:12  story-007  merge approved     │
│  14:30:45  story-004  task started       │
│  14:28:33  story-002  round 2 started    │
│  ...                                     │
└──────────────────────────────────────────┘
```

当前 schema 中没有 `event_log` 表。

推荐合并 `tasks` + `approvals` 事件流，按时间倒序显示最近 50 条；如果查询逻辑在 TUI/CLI 间重复，再考虑新增 DB helper，但数据源仍限定为当前真实表结构。

### Footer 快捷键与搜索面板

搜索面板激活时 Footer 显示：`[↑↓]选择 [Enter]跳转 [ESC]取消`

实现方式：
- 搜索面板获得焦点时，DashboardScreen / ATOApp 的其他快捷键路径都不应响应
- 由于当前已有 `ATOApp.BINDINGS (1-9)` 与 `DashboardScreen.on_key()` 的全局处理，必须显式检查 `search_active`
- 仍需确保 `/` 键在搜索框内不会递归激活搜索

### 与 Story 6.4 的兼容性

- 如果 6.4 的详情页模式激活（`_detail_mode == "detail"`），`/` 搜索应先返回主屏再打开搜索，或直接覆盖搜索
- 搜索结果跳转后，如果之前在详情页，应回到主屏（`_detail_mode = "overview"`）
- 搜索面板不影响 6.4 的 f/c/h/l 快捷键（搜索面板关闭后这些键恢复）

### 与 6.3a/6.3b 的兼容性

- 搜索面板激活时 y/n/1-9 键无效
- 搜索跳转到审批项时，应选中对应审批（`_selected_item_id = "approval:{id}"`）
- tabbed 模式下 [1]-[4] 键位与搜索面板不冲突（通过 `search_active` 显式门控 + focus 管理避免冲突）

### 性能要求

- **NFR3**: 搜索面板激活 ≤200ms（toggle display，无 SQLite 查询）
- 模糊匹配在内存中执行（story 列表已在 ATOApp 中缓存），不触发 SQLite 查询
- 实时过滤响应 ≤50ms（内存过滤小数据集）
- 成本 Tab / 日志 Tab 数据在 ATOApp 2s 轮询中加载，不额外查询

### Scope Boundary

- **IN**: `/` 搜索面板、模糊匹配、结果跳转、ESC 取消、成本 Tab、日志 Tab、响应式兼容
- **OUT**: 命令执行（搜索面板只做导航跳转，不执行 CLI 命令）
- **OUT**: 高级搜索语法（正则、过滤器等）— 保持简单的模糊匹配
- **OUT**: 搜索历史记录
- **OUT**: 修改 ThreeQuestionHeader 或左面板核心逻辑
- **OUT**: 进程隔离改动（已在架构层实现）

### Project Structure Notes

**新增文件：**
- `src/ato/tui/widgets/search_panel.py` — SearchPanel Widget（Input + 结果列表）
- `tests/unit/test_search_panel.py` — SearchPanel 单元测试
- `tests/unit/test_fuzzy_match.py` — 模糊匹配算法测试
- `tests/integration/test_tui_search.py` — 搜索交互集成测试

**修改文件：**
- `src/ato/tui/dashboard.py` — `/` 键绑定、SearchPanel 集成、搜索结果跳转、成本 Tab 内容、日志 Tab 内容
- `src/ato/tui/app.py` — 增加搜索激活门控，以及 story 聚合成本 / recent_events 数据加载
- `src/ato/tui/app.tcss` — SearchPanel 样式、overlay 样式、成本/日志 Tab 内容样式
- `src/ato/tui/widgets/__init__.py` — 导出 SearchPanel
- `src/ato/models/db.py` — 如 recent events 查询在多处复用，可新增 helper；成本聚合优先复用现有 `get_cost_by_story()`

### References

- [Source: _bmad-output/planning-artifacts/epics.md — Story 6.5 定义]
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md — Command Panel 搜索面板设计]
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md — 快捷键层级表 — `/` 搜索全局]
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md — 响应式断点行为]
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md — Tab 视图模式设计]
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md — Textual 组件使用映射 — Input / TabbedContent]
- [Source: _bmad-output/planning-artifacts/architecture.md — TUI 架构模式 / 进程隔离]
- [Source: _bmad-output/planning-artifacts/architecture.md — SQLite Reactive Query — Dashboard / Story Detail]
- [Source: _bmad-output/implementation-artifacts/6-3b-exception-approval-multi-select.md — tabbed 模式 [1]-[4] 契约]
- [Source: _bmad-output/implementation-artifacts/6-4-story-detail-drill-navigation.md — 详情页 _detail_mode 状态管理]

### Previous Story Intelligence (from 6.3a + 6.3b + 6.4)

**关键学习：**
1. **Widget mount + display toggle 比 Screen push 更简单** — 6.3b 的 ExceptionApprovalPanel 和 6.4 的 StoryDetailView 都使用 ContentSwitcher / mount 模式
2. **搜索态需要显式门控** — `Input` focus 有帮助，但当前仓库还有 `ATOApp` / `DashboardScreen` 的全局按键路径，必须配合 `search_active`
3. **统一选择管理** — `_selected_item_id` 支持 `"story:{id}"` 和 `"approval:{id}"` 前缀，搜索跳转需设置此值
4. **tabbed 模式 [1]-[4] 键位** — 6.3b 已确认 tabbed 模式下 plain 1-4 绑定给 switch_tab()，搜索面板不冲突
5. **snapshot-based 增量渲染** — `_rendered_snapshot` 对比机制，搜索跳转后的 UI 更新需兼容
6. **async 数据加载** — 所有 SQLite 查询通过 ATOApp async 方法，搜索过滤在内存中不需 async
7. **BINDINGS 动态更新** — 6.4 引入 `_detail_mode` 控制 Footer 显示，搜索面板需增加 "search" 模式
8. **全量测试** — 至少覆盖现有 `test_tui_responsive.py`、`test_tui_pilot.py`、dashboard / approval 相关回归套件，不引入响应式或审批快捷键回归

## Change Log

- 2026-03-28: `validate-create-story` 修订 —— 将“命令搜索/执行”收敛为当前 TUI 可支持的内部导航目标；补充 `search_active` 显式门控以避免与 `ATOApp` / `DashboardScreen` 现有全局按键冲突；将成本 Tab 数据源对齐到现有 `get_cost_by_story()` helper；移除不存在的 `event_log` 路径；把搜索结果排序与跳转流程对齐到当前 selection / status 排序合同；补回模板 validation note 并去除易漂移的精确测试总数

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

### File List
