# Story 6.4: Story 详情与渐进钻入导航

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a 操作者,
I want 从概览逐层钻入查看 story 详情、findings、成本、执行历史,
so that 任何信息 ≤3 层可达，按需查看不被强推。

## Acceptance Criteria

1. **AC1: 第 1 层 — 右上面板 Story 概览联动**
   - **Given** 左面板选中某 story
   - **When** 右上面板联动更新
   - **Then** 显示 story 概览：当前阶段、累计成本、当前阶段耗时、CL 当前轮次
   - **And** 补充当前 findings 摘要（blocking/suggestion × open/closed）
   - **And** 补充执行产物 / 执行记录的轻量摘要（例如 artifact 数、task 数）
   - **Note:** 这已在 6.2b / 6.3a 中部分实现（`DashboardScreen._render_story_detail()`），本 Story 只增强内容，不重写现有联动模式

2. **AC2: 第 2 层 — Enter 进入 Story 详情页** (UX-DR18)
   - **Given** 用户在左面板选中某 story
   - **When** 按 Enter
   - **Then** 进入 Story 详情页（第 2 层），显示：
     - 状态流可视化，阶段顺序严格对齐 `["queued", *CANONICAL_PHASES, "done"]`
     - Findings 摘要（基于当前状态去重后的 blocking/suggestion × open/closed）
     - 执行产物 / 相关文件列表：优先展示 `tasks.context_briefing.artifacts_produced`，缺失时 fallback 到 `tasks.expected_artifact`
     - 成本明细：每次 CLI 调用的 `phase + role + cli_tool + model + input/output tokens + cost_usd`
     - 执行历史：时间轴展示 `started_at + phase + role + cli_tool + status + artifact + duration`
   - **And** 不要求展示真实 git diff 或“本次改动文件列表”，因为当前 schema 没有稳定的 per-story changed-files 数据源

3. **AC3: 第 2.5 层 — 快捷键展开子视图** (UX-DR17)
   - **Given** Story 详情页中
   - **When** 用户按 `f` / `c` / `h`
   - **Then**
     - `f` 展开 Findings 列表（每个 finding 至少显示 severity + description + status + round_num）
     - `c` 展开成本明细（每次调用至少显示 phase + role + cli_tool + model + input_tokens + output_tokens + cost_usd；若存在则附加 `cache_read_input_tokens`）
     - `h` 展开执行历史（完整时间轴：started_at + phase + role + cli_tool + status + artifact + duration）
   - **Note:** `l` 属于第 3 层日志入口，但当前仓库还没有稳定的 per-story log path / tail contract；本 Story 只实现 placeholder 提示，不伪造 `agent.log`

4. **AC4: ConvergentLoopProgress 组件** (UX-DR4)
   - **Given** Convergent Loop 相关 story（有 CL 轮次数据）
   - **When** ConvergentLoopProgress 组件渲染
   - **Then** 显示：轮次可视化（●已完成 / ◐当前轮 / ○未执行）+ 当前去重后的 findings 统计 + 收敛率 + 当前状态
   - **And** `still_open` 视作 `open`
   - **And** 不把同一 finding 在多轮中的历史记录重复累计为“当前 open/closed 总数”

5. **AC5: ESC 返回导航**
   - **Given** 任意层级（详情页 / 展开子视图）
   - **When** 用户按 ESC
   - **Then** 返回上一层；从详情页 ESC 返回主屏；从展开子视图 ESC 返回详情页概览
   - **And** 返回时保持左面板选中状态不变
   - **And** 详情模式下 `y/n` 与异常审批数字键不应误消费审批动作

6. **AC6: 响应式兼容**
   - **Given** 不同终端宽度
   - **When** 详情页在不同布局模式下渲染
   - **Then** three-panel 模式：Enter 在右上面板 `ContentSwitcher` 内切换到 StoryDetailView
   - **And** tabbed 模式：只在 `tab-stories` 内部切换“列表 / 详情”，保留 App 级 `[1]-[4]` tab 切换契约
   - **And** `<100` 列 degraded 模式不要求支持详情页，只显示现有降级提示
   - **And** 所有支持的断点下 ESC 返回行为一致

## Tasks / Subtasks

- [ ] Task 1: 增强第 1 层 Story 概览 (AC: #1)
  - [ ] 1.1 修改 `DashboardScreen._render_story_detail()` 增加当前 findings 摘要
  - [ ] 1.2 增加执行产物 / 执行历史的轻量计数摘要（不要把全量 detail rows 塞进 2s 轮询快照）
  - [ ] 1.3 有 CL 数据的 story 显示简要收敛信息

- [ ] Task 2: StoryDetailView 组件创建 (AC: #2, #5, #6)
  - [ ] 2.1 将 `src/ato/tui/story_detail.py`（当前 stub）实现为完整的可聚焦 Widget / Container
  - [ ] 2.2 实现状态流可视化（StoryPhaseFlow），顺序严格使用 `["queued", *CANONICAL_PHASES, "done"]`
  - [ ] 2.3 实现 Findings 摘要区块（blocking/suggestion × open/closed）
  - [ ] 2.4 实现执行产物区块：优先解析 `context_briefing.artifacts_produced`，fallback 到 `expected_artifact`；不得把控制标记伪装成“文件变更”
  - [ ] 2.5 实现成本明细区块（使用 `cost_log` 的真实字段：`phase/role/cli_tool/model/input_tokens/output_tokens/cache_read_input_tokens/cost_usd`）
  - [ ] 2.6 实现执行历史区块（使用 `tasks` 的真实字段：`phase/role/cli_tool/status/started_at/completed_at/duration_ms/context_briefing/expected_artifact`）
  - [ ] 2.7 详情页绑定 `f/c/h/l/escape`，并在详情模式下禁用审批快捷键语义

- [ ] Task 3: ConvergentLoopProgress Widget (AC: #4)
  - [ ] 3.1 创建 `src/ato/tui/widgets/convergent_loop_progress.py`
  - [ ] 3.2 实现轮次可视化（●/◐/○）+ 当前去重后的 findings 统计 + 收敛率 + 状态文字
  - [ ] 3.3 在 StoryDetailView 中集成，仅对有 CL 数据的 story 显示

- [ ] Task 4: DashboardScreen Enter / ESC 导航集成 (AC: #2, #5, #6)
  - [ ] 4.1 在 `DashboardScreen` 增加 Enter 键绑定（`action_drill_in`）和 ESC 返回（`action_back`）
  - [ ] 4.2 three-panel 模式：给 `#right-top-switcher` 新增 `StoryDetailView` 槽位并切换显示
  - [ ] 4.3 tabbed 模式：在 `tab-stories` 内增加局部 list/detail 切换容器；不要 push 新 Screen，也不要替换整个 `TabbedContent`
  - [ ] 4.4 保持 `ATOApp.action_switch_tab()` 的 `[1]-[4]` 契约不变
  - [ ] 4.5 保持左面板选中状态和当前 focus 语义不变

- [ ] Task 5: 第 2.5 层快捷键子视图展开 (AC: #3)
  - [ ] 5.1 在 StoryDetailView 内实现 `f` / `c` / `h` 键绑定
  - [ ] 5.2 `f` 展开 Findings 详细列表（DataTable / Tree / VerticalScroll 任选其一，但不要和现有 focus 语义冲突）
  - [ ] 5.3 `c` 展开成本明细列表
  - [ ] 5.4 `h` 展开执行历史时间轴
  - [ ] 5.5 `l` 键显示 placeholder 提示（例如“日志查看将在后续版本提供”），不要尝试 `tail -f` 不存在的路径
  - [ ] 5.6 ESC 从展开子视图返回详情页概览

- [ ] Task 6: 数据查询扩展 (AC: #2, #3, #4)
  - [ ] 6.1 保持 `ATOApp._load_data()` 的 2s 轮询轻量；Story detail 数据改为**按需**查询，不在每轮轮询中加载全量 tasks/cost_log/findings 明细
  - [ ] 6.2 复用 `get_tasks_by_story()` 获取执行历史，并复用 Story 5.2 的 artifact 展示合同：`context_briefing.artifacts_produced` 优先，fallback `expected_artifact`
  - [ ] 6.3 复用 `get_cost_logs_by_story()` 获取成本明细
  - [ ] 6.4 复用 `get_findings_by_story()` 获取逐条 findings；当前状态摘要 / 收敛率复用 `get_story_findings_summary()` 或等价去重逻辑，避免跨轮重复累计
  - [ ] 6.5 通过 `ATOApp → DashboardScreen → StoryDetailView` 推送结构化 detail snapshot；如需提炼共享 helper，应放在中性模块，不要让 TUI 反向 import `cli.py`

- [ ] Task 7: TCSS 样式 (AC: #2, #4, #6)
  - [ ] 7.1 StoryDetailView 布局样式（VerticalScroll + 区块间距）
  - [ ] 7.2 StoryPhaseFlow 样式（phase 节点间距 + 高亮当前）
  - [ ] 7.3 ConvergentLoopProgress 样式（进度条 + 收敛率颜色编码）
  - [ ] 7.4 展开子视图样式（DataTable / Tree / Scroll 样式与全局主题一致）
  - [ ] 7.5 three-panel / tabbed 模式下的 detail 样式都可用

- [ ] Task 8: widgets 模块导出
  - [ ] 8.1 在 `src/ato/tui/widgets/__init__.py` 导出 `ConvergentLoopProgress`

- [ ] Task 9: 单元测试
  - [ ] 9.1 StoryDetailView 渲染测试（各区块内容正确性）
  - [ ] 9.2 StoryPhaseFlow 测试（实际 phase 顺序与当前阶段高亮正确）
  - [ ] 9.3 ConvergentLoopProgress 测试（轮次可视化、去重后 findings 统计、收敛率计算）
  - [ ] 9.4 Artifact 展示测试（`context_briefing.artifacts_produced` 优先，fallback `expected_artifact`）
  - [ ] 9.5 成本明细展开内容测试（真实 `CostLogRecord` 字段）
  - [ ] 9.6 执行历史展开内容测试（真实 `TaskRecord` 字段）

- [ ] Task 10: 集成测试
  - [ ] 10.1 Enter 键从主屏进入详情页（three-panel）
  - [ ] 10.2 ESC 从详情页返回主屏（three-panel）
  - [ ] 10.3 `f/c/h` 展开子视图 + ESC 返回
  - [ ] 10.4 ConvergentLoopProgress 在有 CL 数据 story 上显示
  - [ ] 10.5 tabbed 模式下 `tab-stories` 的 list/detail 切换生效
  - [ ] 10.6 tabbed 模式下 `[1]-[4]` 仍切换 Tab，不被详情页实现破坏
  - [ ] 10.7 导航后左面板选中状态保持
  - [ ] 10.8 相关 TUI 回归通过

## Dev Notes

### 核心架构约束

- **Textual ≥2.0**：Widget 继承 `Widget`，`render()` 返回 `Rich.Text` 或 `RenderableType`
- **数据由 ATOApp 提供**：StoryDetailView 不自行创建长期 SQLite 连接；详情数据由 ATOApp 通过短生命周期查询后推送
- **TUI↔Orchestrator 解耦**：`tui/` 不依赖 `core.py`，只通过 SQLite 读数据
- **CSS 与 Python 分离**：所有样式在 `src/ato/tui/app.tcss`
- **不在 `__init__` 中读 SQLite**：详情数据的首次加载放在明确的 action / async helper 中
- **轻轮询 + 按需 detail**：`ATOApp._load_data()` 继续维护轻量 dashboard 快照；不要把所有 story 的 tasks/cost_log/findings 明细塞进 2s 轮询

### 已存在关键组件（复用，不重建）

| 组件 | 文件 | 用途 |
|------|------|------|
| `ATOApp._load_data()` | `src/ato/tui/app.py` | 轻量轮询，已有 stories/costs/cl_rounds/findings_summary |
| `ATOApp._update_dashboard()` | `src/ato/tui/app.py` | 现有 push-based 更新入口 |
| `DashboardScreen` | `src/ato/tui/dashboard.py` | 统一选择管理、右面板 ContentSwitcher、响应式布局 |
| `DashboardScreen._render_story_detail()` | `src/ato/tui/dashboard.py` | 当前第 1 层概览渲染（需增强） |
| `ContentSwitcher (#right-top-switcher)` | `src/ato/tui/dashboard.py` | 右上区域切换（当前 Static / ExceptionApprovalPanel） |
| `StoryStatusLine` | `src/ato/tui/widgets/story_status_line.py` | 左面板 story 行渲染 |
| `HeartbeatIndicator` | `src/ato/tui/widgets/heartbeat_indicator.py` | 运行中 story 动画指示 |
| `sort_stories_by_status()` | `src/ato/tui/theme.py` | story 排序逻辑 |
| `get_tasks_by_story()` | `src/ato/models/db.py` | story 时间轴查询 |
| `get_cost_logs_by_story()` | `src/ato/models/db.py` | story 成本明细查询 |
| `get_findings_by_story()` | `src/ato/models/db.py` | story findings 明细查询 |
| `get_story_findings_summary()` | `src/ato/models/db.py` | 当前去重后的 findings 摘要 |
| `ContextBriefing.artifacts_produced` | `src/ato/models/schemas.py` | story 产物列表标准字段 |
| `story_detail.py` | `src/ato/tui/story_detail.py` | 已存在 stub 文件，在此实现 |

### 导航实现策略

**推荐方案：局部 ContentSwitcher，不使用 Screen push**

原因：
1. 当前 `DashboardScreen` 已用 `ContentSwitcher` 管理 three-panel / tabbed / degraded
2. 当前右上区域已经有 `#right-top-switcher`，继续扩展最符合 6.3a / 6.3b 模式
3. `ATOApp` 已把 `1`-`9` 绑定为全局数字键动作，push 新 Screen 容易打乱现有路由与 Footer 语义
4. 保持 `DashboardScreen` 内部切换更容易维持 `_selected_item_id`、focus 和响应式布局

**three-panel 实现路径：**
```text
ContentSwitcher (#right-top-switcher)
├── Static (#right-top-content)              — 现有：概览 / 常规审批上下文
├── ExceptionApprovalPanel (#right-top-exception) — 现有：异常审批面板
└── StoryDetailView (#right-top-detail)      — 新增：Story 详情钻入页
```

**tabbed 实现路径：**
- 仅在 `tab-stories` 内增加局部 list/detail 切换容器
- 保留整个 `TabbedContent` 与 App 级 `[1]-[4]` 数字键切页
- ESC 从 detail 返回 story list，而不是 pop 整个 TabbedContent

### 状态流可视化设计

阶段顺序以 `src/ato/state_machine.py` 为准：

```python
PHASE_ORDER = ["queued", *CANONICAL_PHASES, "done"]
```

其中当前仓库的 `CANONICAL_PHASES` 为：

```text
creating → validating → dev_ready → developing → reviewing → fixing
→ qa_testing → uat → merging → regression
```

不要发明 `re_reviewing`、`uat_waiting`、`uat_running` 之类不存在的 phase 名。

### 详情数据合同

**1. 执行产物 / 相关文件**

- 优先读取 `tasks.context_briefing` 里的 `artifacts_produced`
- 缺失时 fallback 到 `tasks.expected_artifact`
- `expected_artifact` 也可能是控制标记（如 transition / restart / regression gating），因此只能作为 fallback 展示字段，不能被当作“真实 git 文件变更列表”
- 如需共享这套逻辑，提炼到中性 helper；不要让 `src/ato/tui/` 直接 import `src/ato/cli.py`

**2. 成本明细**

`cost_log` 的真实字段见 `CostLogRecord`：

- `created_at`
- `phase`
- `role`
- `cli_tool`
- `model`
- `input_tokens`
- `output_tokens`
- `cache_read_input_tokens`
- `cost_usd`

不要查询不存在的 `agent_role` 字段。

**3. 执行历史**

`tasks` 的真实字段见 `TaskRecord`：

- `phase`
- `role`
- `cli_tool`
- `status`
- `expected_artifact`
- `context_briefing`
- `started_at`
- `completed_at`
- `duration_ms`

不要查询不存在的 `task_type` / `agent` 列。

### ConvergentLoopProgress 数据源

- **轮次可视化**：可以基于 `MAX(round_num)` 与 `convergent_loop.max_rounds`
- **当前 findings 摘要 / 收敛率**：应复用 `get_story_findings_summary()` 的去重语义
- **逐轮列表**：如需展示 round 维度明细，可单独基于 `get_findings_by_story()` 分组

不要直接用原始 findings 行数计算“当前 open/closed 总数”，否则会把同一 finding 在多轮中的历史重复累计。

### Footer / 键位语义

- `ATOApp` 的 `q` 与 `[1]-[4]` 继续保持全局绑定
- `DashboardScreen` 负责 `Enter` / `ESC`
- `StoryDetailView` 暴露 `f/c/h/l/escape` 详情内键位
- 不要通过运行时篡改 `ATOApp.BINDINGS` 解决详情页问题；优先使用焦点所在 widget 的 bindings 和局部 action

### Scope Boundary

- **IN**：第 1/2/2.5 层导航、ConvergentLoopProgress、ESC 返回、响应式兼容
- **OUT**：`l` 打开真实独立终端日志（只做 placeholder）
- **OUT**：Finding 单条详情 drill-in（UX 流程存在，但本 Story 先收敛到列表级）
- **OUT**：搜索面板（`/`）属于 Story 6.5
- **OUT**：修改左面板排序或 ThreeQuestionHeader 逻辑

### Project Structure Notes

**新增文件：**
- `src/ato/tui/widgets/convergent_loop_progress.py` — ConvergentLoopProgress Widget
- `tests/unit/test_story_detail_view.py` — StoryDetailView 单元测试
- `tests/unit/test_convergent_loop_progress.py` — CL 进度组件单元测试
- `tests/integration/test_tui_story_detail.py` — 钻入导航集成测试

**修改文件：**
- `src/ato/tui/story_detail.py` — StoryDetailView 主实现
- `src/ato/tui/dashboard.py` — Enter / ESC / right-top switcher / tab-stories list-detail 切换
- `src/ato/tui/app.py` — 详情按需查询入口与数据传递
- `src/ato/tui/app.tcss` — StoryDetailView / CL progress / 子视图样式
- `src/ato/tui/widgets/__init__.py` — 导出 `ConvergentLoopProgress`
- `src/ato/models/db.py` — 如确有必要，仅补充 detail 查询 helper；优先复用现有 helper

### References

- [Source: _bmad-output/planning-artifacts/epics.md — Story 6.4 定义]
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md — Flow 4: Story 详情钻入 + Agent 观察]
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md — Shortcut Key Hierarchy / Navigation Patterns]
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md — ConvergentLoopProgress 组件规范]
- [Source: _bmad-output/planning-artifacts/architecture.md — Textual TUI 架构模式]
- [Source: src/ato/state_machine.py] — `CANONICAL_PHASES`
- [Source: src/ato/models/schemas.py] — `TaskRecord`, `CostLogRecord`, `ContextBriefing`
- [Source: src/ato/models/db.py] — `get_tasks_by_story()`, `get_cost_logs_by_story()`, `get_findings_by_story()`, `get_story_findings_summary()`
- [Source: src/ato/tui/app.py] — App 级 `[1]-[4]` tab 切换绑定
- [Source: src/ato/tui/dashboard.py] — 当前 `#right-top-switcher` / 响应式布局 / 统一选择管理
- [Source: src/ato/cli.py] — Story 5.2 `ato history` 的 artifact 展示合同
- [Source: _bmad-output/implementation-artifacts/6-2b-dashboard-story-list.md] — story 列表与右面板联动基线
- [Source: _bmad-output/implementation-artifacts/6-3a-standard-approval-interaction.md] — 常规审批右面板交互基线
- [Source: _bmad-output/implementation-artifacts/6-3b-exception-approval-multi-select.md] — 异常审批 switcher / 数字键边界

### Previous Story Intelligence (from 6.2b + 6.3a + 6.3b)

1. **`DashboardScreen` 已经承担统一选择管理**：`_selected_item_id` 同时覆盖 approvals 和 stories，详情实现不要另造第二套 selection state。
2. **`#right-top-switcher` 是现成扩展点**：6.3b 已验证在右上区域切换真实 widget 是稳定路线。
3. **App 级 `[1]-[4]` 是已交付契约**：tabbed 模式数字键切页由 `ATOApp.action_switch_tab()` 负责，详情页不能抢走这组按键。
4. **轻量快照 + 局部重建是当前 TUI 模式**：`_rendered_snapshot` / `_rebuild_gen` 已用于避免不必要 rebuild 和 DuplicateIds，详情页接入需兼容这套模式。
5. **当前 findings 摘要已经有去重语义**：`get_story_findings_summary()` 会 collapse 跨轮历史并把 `still_open` 当 `open`，CL 组件不要回退成原始行数累计。
6. **集成测试基线已覆盖响应式和审批键位**：详情页测试必须在此基础上新增，不要破坏既有 `tests/integration/test_tui_responsive.py` / `test_tui_exception_approval.py`
7. **不要硬编码测试总数或源码行号**：引用契约以 symbol / file 为主，避免后续文档漂移。

### Change Log

- 2026-03-28: create-story 创建 — 基于 Epic 6 / PRD / 架构 / UX spec / 前序 Story 6.2b-6.3b 生成 6.4 初稿
- 2026-03-28: validate-create-story 修订 —— 将 phase 顺序收紧到真实 `CANONICAL_PHASES`；把“文件变更列表”改成与 Story 5.2 一致的 artifact 展示合同；将成本/历史字段名对齐 `TaskRecord` / `CostLogRecord`；明确 detail 数据必须按需查询而非塞入 2s 轮询；将 tabbed 模式收敛为 `tab-stories` 内部 list/detail 切换并保留 App 级 `[1]-[4]`

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

### File List
