# Story 6.3a: 常规审批交互

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a 操作者,
I want 通过 TUI 快速处理常规审批（merge/timeout/budget/blocking）,
so that 审批决策在 30 秒内完成。

## Acceptance Criteria

1. **AC1 — ApprovalCard 折叠态渲染** (UX 设计规范 ApprovalCard, FR37)
   ```
   Given 左面板选中一个 pending approval 项
   When ApprovalCard 渲染
   Then 折叠态单行显示：类型图标 + story ID + 一句话摘要（模板拼接）+ 推荐操作 + 风险指示
   And 类型图标与摘要模板复用现有共享常量/函数；如需复用 CLI 里的私有 helper，必须先提取到共享模块，禁止 `tui/` 直接 import `cli.py`
   And 摘要由 approval_type + payload 的确定性模板函数生成（与 CLI 对齐）
   And 风险指示使用 risk_level 对应颜色编码：high=$error, medium=$warning, low=$success
   ```

2. **AC2 — ApprovalCard 展开态详情** (FR37)
   ```
   Given 操作者按 `d` 展开 ApprovalCard
   When 详情面板渲染
   Then 显示：审批上下文（阶段转换详情、review 轮次、QA 结果、成本、耗时）
   And 展开内容在右上面板（right-top-content）渲染
   And 右下面板继续保留该审批的快捷键提示或“已提交，等待处理”中间状态
   And 再按 `d` 收起详情回到折叠态
   ```

3. **AC3 — 审批决策提交** (FR20, FR37)
   ```
   Given 操作者按 `y` 或 `n` 且当前审批属于本 Story 支持的二选一语义
   When 提交审批决策
   Then 通过 `update_approval_decision()` 或等价 TUI wrapper 写入 SQLite approvals 表（status + decision + 时间戳 + 选择理由）
   And 触发 nudge 通知 Orchestrator
   And 在右下面板显示行内反馈"已提交，等待处理"
   And 2-5 秒后审批项从队列消失（下一轮 SQLite 轮询更新）
   And `y`/`n` 的具体 decision 必须由 `approval_type + payload.options` 的确定性映射解析，不得一律套用 `recommended_action` / `reject`
   And 至少对齐以下语义：`merge_authorization` → `y=approve`, `n=reject`；`blocking_abnormal` → `y=confirm_fix`, `n=human_review`；`budget_exceeded` → `y=increase_budget`, `n=reject`；`timeout` → `y=continue_waiting`, `n=abandon`；`batch_confirmation` → `y=confirm`, `n=reject`
   ```

4. **AC4 — 审批列表与排序**
   ```
   Given DashboardScreen 左面板
   When 存在 pending 审批
   Then 审批 ApprovalCard 显示在 story 列表上方（审批优先原则）
   And 审批项使用 $warning 色 ◆ 图标标识
   And 无 pending 审批时，左面板仅显示 story 列表（现有行为）
   ```

5. **AC5 — 右下面板操作区域**
   ```
   Given 左面板选中审批项
   When 右下面板渲染
   Then 显示当前审批的动作标签 + 快捷键提示，例如 "[y] 合并  [n] 拒绝  [d] 详情" 或 "[y] 进入 fix  [n] 人工审阅  [d] 详情"
   And 无选中审批时，右下面板显示通用操作提示
   And 审批已提交但尚未被下一轮轮询移除时，该区域保持 "$muted 已提交，等待处理" 中间状态
   ```

6. **AC6 — 数据轮询与审批加载** (NFR3)
   ```
   Given ATOApp 每 2 秒轮询
   When 加载 pending approvals 数据
   Then 从 SQLite approvals 表查询 pending 记录
   And 传递到 DashboardScreen 用于渲染 ApprovalCard
   And 单次刷新渲染 ≤500ms
   ```

7. **AC7 — 多选审批降级**
   ```
   Given 当前选中审批项的 `payload.options` 超过 2 个，或属于 Story 6.3b 的多选/异常审批范围
   When 右下面板渲染
   Then 显示“此审批需多选，请先使用 CLI 或等待 6.3b”提示
   And `y`/`n` 在该项上不写 SQLite，避免错误消费
   ```

## Tasks / Subtasks

- [x] Task 1: ApprovalCard Widget (AC: #1)
  - [x] 1.1 在 `src/ato/tui/widgets/approval_card.py` 创建 `ApprovalCard(Widget)` 类
  - [x] 1.2 定义 reactive 属性：`approval_id: reactive[str]`、`story_id: reactive[str]`、`approval_type: reactive[str]`、`summary: reactive[str]`、`recommended_action: reactive[str]`、`risk_level: reactive[str]`
  - [x] 1.3 实现 `render()` 方法——折叠态单行渲染：`{类型图标} {story_id}  {摘要}  [{推荐}] [{风险}]`
  - [x] 1.4 复用现有审批图标/摘要模板；如当前实现仍在 `cli.py` 私有函数中，先提取到共享 helper，再供 CLI/TUI 共用；提取时必须保留已存在的 `rebase_conflict` 等当前 approval type
  - [x] 1.5 风险指示颜色编码：`high` → `$error`、`medium` → `$warning`、`low` → `$success`、`None` → `$muted`
  - [x] 1.6 实现 `update_data()` 方法批量更新 reactive 属性

- [x] Task 2: ApprovalDetailPanel 展开态 (AC: #2)
  - [x] 2.1 在 `ApprovalCard` 或 `DashboardScreen` 中实现展开/折叠状态管理
  - [x] 2.2 展开态在右上面板（`#right-top-content`）渲染审批详情；右下面板保留动作提示/提交反馈
  - [x] 2.3 详情内容：审批类型说明、阶段转换上下文、成本/耗时信息、CL 轮次、payload 解析的结构化数据
  - [x] 2.4 使用 `Rich.Text` 渲染结构化详情，遵循现有 `_update_detail_panel()` 模式
  - [x] 2.5 `d` 键切换展开/折叠：展开时右上面板切到审批上下文，折叠时恢复原有联动内容

- [x] Task 3: 键位绑定与审批决策提交 (AC: #3, #5, #7)
  - [x] 3.1 在 `DashboardScreen` 中添加 `BINDINGS`：`("d", "toggle_detail", "展开/折叠")`、`("y", "approve", "批准")`、`("n", "reject", "拒绝")`
  - [x] 3.2 实现 `action_approve()` 方法：
    - 检查当前选中的是审批项（非普通 story）
    - 仅对本 Story 支持的二选一审批生效；`payload.options > 2` 或 Story 6.3b 范围的审批在此禁用
    - 通过共享 helper 按 `approval_type + payload.options` 解析 `y` 对应的具体 decision，不得直接把 `recommended_action` 当作唯一真值
    - 调用 `update_approval_decision()`，或先重构 `ATOApp.write_approval()` / 新增 `submit_approval_decision()` 以接收分离的 `status`、`decision`、`decision_reason`
    - `decision_reason` 至少记录 `tui` 来源、按键和值（如 `tui:y -> approve`）
    - 在右下面板显示 "$muted 已提交，等待处理"，直到下一轮轮询移除该项
  - [x] 3.3 实现 `action_reject()` 方法：
    - 与 `action_approve()` 使用同一套共享映射/写入封装，解析 `n` 对应的具体 decision（例如 `reject` / `human_review` / `abandon`）
    - 按 Story 4.1 规则写入正确的 `status` 与 `decision_reason`
    - 在右下面板显示 "$muted 已提交，等待处理"，直到下一轮轮询移除该项
  - [x] 3.4 实现 `action_toggle_detail()` 方法：切换选中审批项的展开/折叠态
  - [x] 3.5 `y`/`n` 仅在当前选中项为审批且属于本 Story 支持的二选一语义时生效；选中普通 story 或多选审批时无写入，仅显示 fallback 提示
  - [x] 3.6 即时反馈以右下面板行内状态为主，持续到 SQLite 状态更新；`notify()` 最多作为增强提示，不可替代主反馈路径

- [x] Task 4: ATOApp 数据扩展——审批数据加载 (AC: #6)
  - [x] 4.1 扩展 `ATOApp._load_data()`：查询 pending approvals 完整记录列表（复用 `get_pending_approvals()`）
  - [x] 4.2 在 `ATOApp` 存储 `_pending_approval_records: list[ApprovalRecord]` 快照
  - [x] 4.3 在 `_update_dashboard()` 中传递 `pending_approval_records` 到 `DashboardScreen.update_content()`
  - [x] 4.4 扩展 `DashboardScreen.update_content()` 接口：新增 `pending_approval_records` 可选参数

- [x] Task 5: DashboardScreen 审批列表集成 (AC: #4, #5)
  - [x] 5.1 修改 `_update_story_list()` 方法：在 story 列表上方渲染 pending 审批的 `ApprovalCard`
  - [x] 5.2 审批项参与排序逻辑：始终排在所有 story 之前（审批优先原则）
  - [x] 5.3 审批项参与 ↑↓ 选择导航：`_selected_index` 统一管理审批+story 列表
  - [x] 5.4 选中审批项时：右上面板联动显示审批上下文详情，右下面板显示该审批的动作标签/快捷键/提交中间状态
  - [x] 5.5 替换右下面板 `"操作区域（占位）"` 为审批相关操作提示
  - [x] 5.6 Tab 模式 `[1]审批` Tab 同步渲染 ApprovalCard 列表（替换占位文字）；遇到多选审批时显示 CLI / 6.3b fallback 提示，而不是错误启用 `y`/`n`

- [x] Task 6: TCSS 样式 (AC: #1, #4)
  - [x] 6.1 在 `app.tcss` 添加 `ApprovalCard` 样式：高度 1 行、`$warning` 背景暗色
  - [x] 6.2 选中审批项使用 `selected-story` 复用或新增 `selected-approval` 高亮样式
  - [x] 6.3 审批详情展开态样式
  - [x] 6.4 即时反馈样式采用 `$muted` 行内中间状态，与 UX 中“已提交，等待处理”保持一致；不要只做短暂 toast

- [x] Task 7: widgets 模块导出 (AC: #1)
  - [x] 7.1 在 `widgets/__init__.py` 导出 `ApprovalCard`
  - [x] 7.2 更新 `__all__` 列表

- [x] Task 8: 单元测试 (AC: #1, #3)
  - [x] 8.1 `tests/unit/test_approval_card.py`（新文件）：
    - `test_approval_card_render_collapsed` — 折叠态单行渲染包含图标、story ID、摘要、推荐、风险
    - `test_approval_card_risk_level_colors` — high/medium/low/None 颜色映射正确
    - `test_approval_card_type_icons` — 各审批类型图标映射正确（含当前已存在的 `rebase_conflict`）
    - `test_approval_card_summary_generation` — 摘要模板拼接正确（各审批类型）
    - `test_approval_card_update_data` — 批量更新 reactive 属性正确反映
  - [x] 8.2 `tests/unit/test_dashboard.py`（扩展或新建）：
    - `test_approval_items_sort_before_stories` — 审批项排在 story 之前
    - `test_y_key_maps_merge_authorization_to_approve` — `y` 键对 `merge_authorization` 写入 `decision="approve"`
    - `test_blocking_abnormal_y_n_mapping` — `blocking_abnormal` 的 `y/n` 分别映射到 `confirm_fix` / `human_review`
    - `test_multi_option_approval_disables_y_n` — `payload.options > 2` 的审批不会被 `y/n` 误消费
    - `test_decision_reason_persisted_for_tui_action` — TUI 写入会持久化 deterministic `decision_reason`
    - `test_d_key_toggles_detail` — `d` 键切换展开/折叠
    - `test_y_key_ignored_on_story_selection` — 选中 story 时 `y` 无效

- [x] Task 9: 集成测试 (AC: #1, #3, #4, #6, #7)
  - [x] 9.1 `tests/integration/test_tui_pilot.py`（扩展）：
    - `test_dashboard_renders_pending_approvals` — 有 pending 审批时 ApprovalCard 正确渲染
    - `test_dashboard_merge_authorization_y_writes_sqlite` — `y` 键对 merge 授权写入 `status="approved"`, `decision="approve"` 并触发 nudge
    - `test_dashboard_blocking_abnormal_n_writes_sqlite` — `n` 键对 blocking 异常写入 `decision="human_review"`
    - `test_dashboard_approval_disappears_after_decision` — 决策后审批项在下一轮刷新消失
    - `test_dashboard_approval_priority_over_stories` — 审批项始终排在 story 之前
    - `test_tabbed_mode_approvals_tab` — Tab 模式审批 Tab 渲染 ApprovalCard 列表
    - `test_multi_option_approval_shows_cli_fallback` — 多选审批在 TUI 中只显示 fallback 提示，不误启用 `y/n`

## Dev Notes

### 核心架构约束

- **Textual ≥2.0**——组件继承 `Widget`，使用 `render()` 返回 `Rich.Text`
- **数据由 ATOApp 提供**——所有 Widget 不自行创建 SQLite 连接，通过 `update_data()` / `update_content()` 接口接收数据
- **ATOApp 轮询驱动**——`set_interval(2.0, self.refresh_data)` 已在 6.1a 实现，本 Story 扩展数据内容加入 pending approvals
- **TUI 写入路径**——`ATOApp.write_approval()` 已在 Story 6.1a 实现（`src/ato/tui/app.py`），直接写 SQLite + commit + send_external_nudge；本 Story 复用此方法
- **TUI↔Orchestrator 解耦**——`tui/` 不依赖 `core.py`，通过 SQLite 通信。审批写入 = SQLite + nudge，不走 IPC
- **SQLite WAL**——`busy_timeout=5000` 覆盖并发写入极端情况
- **进程边界**——TUI 直写 SQLite + `send_external_nudge()`，与 CLI `ato approve` 模式一致
- **CSS 与 Python 分离**——`tui/app.tcss` 是全局主题唯一入口
- **Textual 生命周期**——数据加载在 `on_mount()` 而非 `__init__()`
- **CLI/TUI 审批语义对齐**——TUI 不能把 `y/n` 硬编码成 `approved/rejected`；如需复用 CLI 的合法选项推导逻辑，必须先提取到共享 helper，不能让 `tui/` 直接 import `cli.py`

### `write_approval()` 现有实现分析

`ATOApp.write_approval()` 在 `src/ato/tui/app.py` 中已完整实现：
- 参数：`approval_id`, `story_id`, `approval_type`, `decision`
- 行为：UPDATE approvals SET status=decision, decision=decision WHERE approval_id=? AND status='pending'
- 返回：`True` 成功，`False` 已被处理

**注意**：现有实现将 `status` 直接设为传入的 `decision` 值。但根据 Story 4.1 AC3 的规则：
- 二元审批：`approve` → `status="approved"`，`reject` → `status="rejected"`
- 多选审批：具体选项写入 `decision`，`status="approved"`

本 Story 不能直接把 `y` 映射到 `recommended_action`。需要一个共享 helper（建议放在 `src/ato/approval_helpers.py` 或新的共享契约模块）完成：
- 判断当前审批是否属于 6.3a 支持的二选一审批
- 从 `approval_type + payload.options` 解析 `y` / `n` 对应的具体 `decision`
- 生成与 Story 4.1 对齐的 `status`（`approved` / `rejected`）与 deterministic `decision_reason`

**推荐方案**：新增 `submit_approval_decision()` TUI helper，内部统一调用 `update_approval_decision()` + nudge；如保留 `write_approval()`，则先把它重构成接收分离的 `status`、`decision`、`decision_reason`，避免继续沿用 `status=decision` 的旧捷径。

### ApprovalCard 渲染格式

折叠态单行：
```
🔀 story-007  QA 全通过，建议合并            [approve] [低]
⚠  story-003  Blocking 4/2，建议人工审阅    [human_review] [中]
💰 story-009  累计成本 $15.20 超过阈值      [increase_budget] [高]
```

展开态（右上面板）：
```
story-007 — merge 授权

阶段: uat_waiting → merging
Convergent Loop: 2轮收敛 (0 blocking)
成本: $2.60 │ 耗时: 18m

最近 Review Findings:
  ✔ 2 suggestions (closed)
  ✔ 1 blocking (closed in round 2)

推荐: 合并 [低风险]
QA 全通过, 0 blocking findings

右下面板： [y] 合并  [n] 拒绝  [d] 收起详情
```

### 审批类型图标映射（复用自 CLI Story 4.1）

```python
_APPROVAL_TYPE_ICONS: dict[str, str] = {
    "merge_authorization": "🔀",
    "session_timeout": "⏱",
    "crash_recovery": "↩",
    "blocking_abnormal": "⚠",
    "budget_exceeded": "💰",
    "regression_failure": "✖",
    "convergent_loop_escalation": "🔄",
    "batch_confirmation": "📦",
    "timeout": "⏳",
    "precommit_failure": "🔧",
    "rebase_conflict": "⚡",
    "needs_human_review": "👁",
}
```

**建议将此映射提取到 `src/ato/tui/theme.py` 或 `src/ato/models/schemas.py` 中作为共享常量**，避免在 `cli.py` 和 `tui/widgets/approval_card.py` 两处维护。

### 摘要生成逻辑（复用自 CLI Story 4.1）

`cli.py` 中的 `_approval_summary()` 根据 `approval_type` 和 `payload` JSON 生成确定性摘要。建议提取为共享函数（如 `src/ato/approval_helpers.py` 中新增 `format_approval_summary()`），TUI 和 CLI 共用。

### `y` / `n` 决策映射（与 CLI / UX 对齐）

本 Story 不把 `y` 简化为“总是推荐动作”，也不把 `n` 简化为“总是 reject”。建议在共享 helper 中固定以下二选一语义：

| approval_type | `y` | `n` | 说明 |
|---|---|---|---|
| `merge_authorization` | `approve` | `reject` | 标准二元审批 |
| `blocking_abnormal` | `confirm_fix` | `human_review` | UX 明确规定 `y/n` 不是推荐动作镜像 |
| `budget_exceeded` | `increase_budget` | `reject` | 预算类常规审批 |
| `timeout` | `continue_waiting` | `abandon` | 双选超时审批 |
| `batch_confirmation` | `confirm` | `reject` | 批次确认 |

若 `payload.options` 超过 2 个，或 approval_type 属于 `session_timeout` / `crash_recovery` / `regression_failure` / `precommit_failure` / `rebase_conflict` / `needs_human_review` / `convergent_loop_escalation` 等多选审批，则本 Story 只负责可见化与 fallback，不在 TUI 中错误消费。

### 审批数据获取 SQL

```python
# 在 ATOApp._load_data() 中追加
from ato.models.db import get_pending_approvals

# 复用已有函数
pending_records = await get_pending_approvals(db)
self._pending_approval_records = pending_records
```

**重要**：`get_pending_approvals()` 已在 `src/ato/models/db.py` 实现，返回 `list[ApprovalRecord]`。不需要新建查询。

### DashboardScreen 审批列表集成策略

当前 `_update_story_list()` 方法管理 `#story-list-container` 中的 `StoryStatusLine` / `HeartbeatIndicator` widget。集成审批的推荐方式：

1. **在 story 列表上方插入 ApprovalCard**——审批项始终排在顶部
2. **统一选择索引**——`_sorted_story_ids` 扩展为 `_sorted_item_ids`，包含 `approval:{id}` 和 `story:{id}` 两种前缀
3. **选中类型判断**——通过 ID 前缀区分当前选中的是审批还是 story，决定右面板渲染内容和键位行为

```python
# 推荐的选择索引管理
self._sorted_item_ids: list[str] = []
# 审批在前
for approval in pending_approvals:
    self._sorted_item_ids.append(f"approval:{approval.approval_id}")
# story 在后（按现有排序逻辑）
for story in sorted_stories:
    self._sorted_item_ids.append(f"story:{story.get('story_id', '')}")
```

### 右面板联动逻辑

```python
def _update_detail_panel(self) -> None:
    """右上面板联动——区分审批和 story。"""
    if not self._selected_item_id:
        # 默认提示
        return

    if self._selected_item_id.startswith("approval:"):
        # 渲染审批上下文
        aid = self._selected_item_id.removeprefix("approval:")
        approval = self._approvals_by_id.get(aid)
        if approval:
            self._render_approval_context(approval)
    else:
        # 现有 story 详情逻辑
        sid = self._selected_item_id.removeprefix("story:")
        self._render_story_detail(sid)
```

### 即时反馈实现

审批提交后的即时反馈方式选择：
- **方案 A**：在右下面板显示 "$muted 已提交，等待处理" 行内状态，并保持到下一轮 SQLite 轮询移除该项
- **方案 B**：使用 Textual 的 `notify()` 方法弹出 Toast 通知（Textual ≥2.0 支持）
- **推荐方案 A**：行内状态是主反馈路径；`notify()` 最多作为补充提示，不能替代右下面板的确认语义

### 已有依赖（复用，不重建）

| 组件 | 文件 | 现状 |
|------|------|------|
| `ATOApp.write_approval()` | `src/ato/tui/app.py` | SQLite 写入 + nudge ✅ |
| `get_pending_approvals()` | `src/ato/models/db.py` | 查询 pending approvals ✅ |
| `ApprovalRecord` | `src/ato/models/schemas.py` | 完整模型（含 recommended_action, risk_level）✅ |
| `_APPROVAL_TYPE_ICONS` | `src/ato/cli.py` | 审批类型图标映射 ✅ |
| `_approval_summary()` | `src/ato/cli.py` | 确定性摘要模板 ✅ |
| `send_external_nudge()` | `src/ato/nudge.py` | SIGUSR1 信号发送 ✅ |
| `update_approval_decision()` | `src/ato/models/db.py` | 审批决策更新 ✅ |
| `DashboardScreen` | `src/ato/tui/dashboard.py` | 三面板布局 + story 列表 + 选择联动 ✅ |
| `theme.py` 三重编码 | `src/ato/tui/theme.py` | STATUS_CODES + RICH_COLORS ✅ |
| `StoryStatusLine` | `src/ato/tui/widgets/story_status_line.py` | Story 状态行渲染 ✅ |
| `HeartbeatIndicator` | `src/ato/tui/widgets/heartbeat_indicator.py` | 活跃度心跳 ✅ |
| `sort_stories_by_status()` | `src/ato/tui/theme.py` | Story 排序逻辑 ✅ |

### 缺失功能（本 Story 必须实现）

| 缺失 | 说明 |
|------|------|
| `ApprovalCard` Widget | TUI 中无审批卡片组件——只有 `src/ato/tui/approval.py` 空文件 |
| DashboardScreen 审批集成 | 左面板无审批渲染，右下面板仍是 `"操作区域（占位）"` |
| `y`/`n`/`d` 键位绑定 | DashboardScreen 无审批操作键位 |
| ATOApp 审批数据加载 | `_load_data()` 不加载完整 approval 记录列表 |
| 审批/Story 统一选择管理 | 当前 `_sorted_story_ids` 只包含 story ID |

### Scope Boundary

- 本 Story 只交付**二选一 / `y/n` 语义明确**的常规审批 TUI 交互；若审批的 `payload.options` 超过 2 个，则本 Story 只显示卡片与 fallback 提示，不在 TUI 中错误消费
- **多选/异常审批**（如 `session_timeout`、`crash_recovery`、`regression_failure`、`precommit_failure`、`rebase_conflict`、`needs_human_review`、`convergent_loop_escalation` 等）的完整交互属于 Story 6.3b
- **Story 详情**（Enter 进入详情页、f/c/h/l 快捷键）属于 Story 6.4
- **搜索面板**（`/` 搜索）属于 Story 6.5
- 本 Story 不修改 CLI 审批命令（`ato approvals` / `ato approve`，已在 Story 4.1 完成）
- 如果重构 `write_approval()` 或提取共享常量，需确保 Story 4.1 的 CLI 测试继续通过

### 性能要求

- **NFR3**：单次刷新渲染 ≤500ms——ApprovalCard 渲染是简单文本拼接，不涉及复杂计算
- 审批数据与 story 数据共享同一 `_load_data()` 轮询周期，不额外增加 SQLite 查询开销（`get_pending_approvals()` 单次查询）
- ApprovalCard 折叠态固定 1 行高度，不影响 VerticalScroll 性能

### 测试策略

**单元测试模式**（参考 `tests/unit/test_story_status_line.py`）：
- 直接实例化 Widget，调用 `update_data()` 后验证 `render()` 输出
- 使用 `Rich.Text.plain` 属性提取纯文本内容进行断言
- 不需要 Textual `pilot`——纯渲染逻辑测试

**集成测试模式**（参考 `tests/integration/test_tui_pilot.py`）：
- 使用 `ATOApp(db_path=...)` + `app.run_test()` 启动完整 TUI
- 通过 `pilot.press("y")` / `pilot.press("n")` / `pilot.press("d")` 模拟键位
- 插入 mock SQLite 数据后验证 Widget 渲染
- 验证 SQLite 写入结果（审批决策持久化）

```python
# 集成测试示例
async def test_dashboard_merge_authorization_y_writes_sqlite(
    tui_db_path: Path,
) -> None:
    """y 键写入 merge 授权审批决策。"""
    # 插入 pending approval
    db = await get_connection(tui_db_path)
    try:
        now = datetime.now(tz=UTC).isoformat()
        await db.execute(
            "INSERT INTO stories (...) VALUES (...)", (...)
        )
        await db.execute(
            "INSERT INTO approvals (...) VALUES (...)", (...)
        )
        await db.commit()
    finally:
        await db.close()

    app = ATOApp(db_path=tui_db_path)
    async with app.run_test() as pilot:
        # 等待数据加载
        await pilot.pause()
        # 按 y 批准
        await pilot.press("y")
        await pilot.pause()
        # 验证 SQLite 写入
        db = await get_connection(tui_db_path)
        try:
            cursor = await db.execute(
                "SELECT status, decision, decision_reason FROM approvals WHERE approval_id = ?",
                (approval_id,),
            )
            row = await cursor.fetchone()
            assert row[0] == "approved"
            assert row[1] == "approve"
            assert row[2] == "tui:y -> approve"
        finally:
            await db.close()
```

### Project Structure Notes

**新增文件：**
- `src/ato/tui/widgets/approval_card.py` — ApprovalCard Widget（折叠态渲染）
- `tests/unit/test_approval_card.py` — ApprovalCard 单元测试

**修改文件：**
- `src/ato/tui/app.py` — `_load_data()` 追加审批数据加载、`_update_dashboard()` 传递审批数据；可能重构 `write_approval()` 方法
- `src/ato/tui/dashboard.py` — `update_content()` 新增 `pending_approval_records` 参数、`_update_story_list()` 集成 ApprovalCard、新增 `y`/`n`/`d` 键位绑定和 action 方法
- `src/ato/tui/widgets/__init__.py` — 导出 `ApprovalCard`
- `src/ato/tui/app.tcss` — 新增 ApprovalCard 样式
- `src/ato/tui/theme.py` — 可选：新增 `APPROVAL_TYPE_ICONS` 共享常量 + `map_risk_to_color()` 辅助函数

**可选提取文件（减少代码重复）：**
- `src/ato/approval_helpers.py` — 可新增 `format_approval_summary()` 共享函数（从 `cli.py._approval_summary()` 提取）

**扩展测试文件：**
- `tests/integration/test_tui_pilot.py` — 追加审批交互集成测试

**路径和命名完全符合架构规范** [Source: architecture.md 文件结构图 + 实际代码结构]

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 6.3a] — AC 原文
- [Source: _bmad-output/planning-artifacts/architecture.md#Decision 2] — TUI↔Orchestrator 通信模型（直写 SQLite + nudge）
- [Source: _bmad-output/planning-artifacts/architecture.md#FR到结构的映射] — FR19-23 → `tui/approval.py`, `cli.py`, `nudge.py`
- [Source: _bmad-output/planning-artifacts/architecture.md#用户可见通知子系统] — NotificationLevel + 触发规则
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md#ApprovalCard] — 审批卡片：类型图标 + story ID + 一句话摘要 + 推荐操作 + 风险指示
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md#Effortless Interactions] — 常规审批 `y/n/d` 一键操作
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md#Experience Mechanics] — 决策流程 + 确认反馈
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md#Design Direction] — lazygit 三面板 + 右面板分工
- [Source: _bmad-output/planning-artifacts/prd.md#FR19] — 审批队列查看
- [Source: _bmad-output/planning-artifacts/prd.md#FR20] — 审批决策持久化（含时间戳和选择理由）
- [Source: _bmad-output/planning-artifacts/prd.md#FR37] — TUI 审批队列交互
- [Source: _bmad-output/planning-artifacts/prd.md#NFR3] — 单次刷新渲染 ≤500ms
- [Source: _bmad-output/implementation-artifacts/4-1-approval-queue-nudge.md] — Approval 模型、DB 函数、CLI 审批命令完整实现
- [Source: _bmad-output/implementation-artifacts/6-2b-dashboard-story-list.md] — DashboardScreen 三面板布局、story 列表渲染、选择联动
- [Source: _bmad-output/project-context.md] — 项目实现规则（68 条）
- [Source: src/ato/tui/app.py] — `ATOApp.write_approval()` 现状与 `_load_data()` / `_update_dashboard()` 契约
- [Source: src/ato/tui/dashboard.py] — DashboardScreen 完整实现（三面板 + story 列表 + 选择联动 + 数据更新）
- [Source: src/ato/tui/theme.py] — 三重状态编码 + RICH_COLORS + 排序逻辑
- [Source: src/ato/tui/widgets/story_status_line.py] — StoryStatusLine 实现模式参考
- [Source: src/ato/tui/widgets/__init__.py] — Widget 导出模式
- [Source: src/ato/tui/app.tcss] — 全局 TCSS 主题
- [Source: src/ato/models/schemas.py] — `ApprovalRecord`、`APPROVAL_RECOMMENDED_ACTIONS`
- [Source: src/ato/models/db.py] — `get_pending_approvals()`、`update_approval_decision()`
- [Source: src/ato/approval_helpers.py] — 统一 approval 创建 API
- [Source: src/ato/nudge.py] — nudge 机制（进程内 + 外部 + bell）
- [Source: src/ato/cli.py] — `_approval_summary()`、`_extract_valid_options()`、`ato approvals/approve` 语义参考
- [Source: tests/integration/test_tui_pilot.py] — TUI 集成测试模式参考
- [Source: tests/unit/test_approval.py] — Approval 单元测试模式参考

### Change Log

- 2026-03-27: create-story 创建 — 基于 Epic 6 / PRD FR19-20,FR37 / 架构 Decision 2 / UX 设计规范 / Story 4.1 Approval 基础 / Story 6.2b Dashboard 实现生成完整开发上下文
- 2026-03-27: validate-create-story 修订 —— 把审批上下文固定到右上面板、动作/确认固定到右下面板；收紧 `y/n` 只适用于二选一审批并对齐 `blocking_abnormal` 等真实 decision 映射；补齐 `decision_reason` 持久化与行内”已提交，等待处理”中间状态；为多选审批增加 CLI / 6.3b fallback；去除易漂移的行号引用并补回现有 `rebase_conflict` 图标映射
- 2026-03-27: dev-story 实现完成 — 完整实现 9 个 Task，提取共享审批 helpers，创建 ApprovalCard Widget，集成到 DashboardScreen 统一选择管理，22 单元测试 + 7 集成测试全通过

## Dev Agent Record

### Agent Model Used
Claude Opus 4.6 (1M context)

### Debug Log References
N/A

### Completion Notes List
- 从 `cli.py` 提取 `_APPROVAL_TYPE_ICONS` 到 `tui/theme.py` 作为共享常量 `APPROVAL_TYPE_ICONS`
- 从 `cli.py` 提取 `_approval_summary()` 到 `approval_helpers.py` 作为 `format_approval_summary()`，CLI 委托调用
- 新增二选一审批决策映射：`is_binary_approval()`, `resolve_binary_decision()`, `get_binary_approval_labels()`
- 创建 `ApprovalCard` Widget：折叠态单行渲染（图标 + story ID + 摘要 + 推荐 + 风险）
- `DashboardScreen` 重构为统一选择管理（`_sorted_item_ids` = 审批 + story），向后兼容 `_selected_story_id`
- 新增 `y`/`n`/`d` 键位绑定：`action_approve`, `action_reject`, `action_toggle_detail`
- `ATOApp` 新增 `submit_approval_decision()` 方法（分离 status/decision/decision_reason）
- 右下面板：二选一审批显示动作标签 + 快捷键，已提交显示"已提交，等待处理"，多选审批显示 CLI/6.3b fallback
- 解决 Textual async removal + mount DuplicateIds 问题：引入 rebuild generation counter
- 22 个新增单元测试 + 7 个集成测试，全量 1251 个测试通过
- Tab 模式 [1]审批 Tab 从占位 Static 改为 VerticalScroll + ApprovalCard 列表

### File List
**新增文件：**
- `src/ato/tui/widgets/approval_card.py` — ApprovalCard Widget
- `tests/unit/test_approval_card.py` — ApprovalCard 单元测试（22 tests）
- `tests/unit/test_dashboard_approval.py` — Dashboard 审批单元测试（7 tests）

**修改文件：**
- `src/ato/approval_helpers.py` — 新增 format_approval_summary, is_binary_approval, resolve_binary_decision, get_binary_approval_labels
- `src/ato/cli.py` — _APPROVAL_TYPE_ICONS 委托到 theme.py, _approval_summary 委托到 approval_helpers
- `src/ato/tui/app.py` — _load_data 加载 pending_approval_records, 新增 submit_approval_decision()
- `src/ato/tui/dashboard.py` — 统一选择管理、审批列表集成、y/n/d 键位、右面板联动、Tab 审批 Tab
- `src/ato/tui/theme.py` — 新增 APPROVAL_TYPE_ICONS, map_risk_to_color
- `src/ato/tui/widgets/__init__.py` — 导出 ApprovalCard
- `src/ato/tui/app.tcss` — 新增 ApprovalCard / selected-approval / approval-submitted 样式
- `tests/integration/test_tui_pilot.py` — 新增 7 个审批交互集成测试
- `tests/integration/test_tui_responsive.py` — 适配 tab-approvals-container 变更
