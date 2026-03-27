# Story 6.3b: 异常审批与多选交互

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a 操作者,
I want 在异常情况下通过 TUI 专用面板做出多选决策,
So that 复杂异常可以在 TUI 内精确处理，而不是默认退到 CLI。

## Acceptance Criteria (AC)

### AC1: ExceptionApprovalPanel 渲染 (UX-DR5, UX-DR20)

```gherkin
Given 当前仓库的多选异常审批类型（session_timeout / crash_recovery / regression_failure / precommit_failure / rebase_conflict / needs_human_review / convergent_loop_escalation）
When 选中 pending approval 且 DashboardScreen 在 three-panel 模式渲染右上面板
Then ExceptionApprovalPanel 显示 risk_level 对应边框：high → $error，medium → $warning，其他 → $surface
And 面板标题使用共享类型图标 + 人类可读的大写标题（如 "✖ REGRESSION FAILURE"）
And 面板内容包含"发生了什么 + 影响范围 + 你的选项"三要素
And 每个选项前标注数字键（1/2/3/...）
And 面板默认始终展开；`d` 键只切换“更多上下文”而不是回到 ApprovalCard 折叠态
```

### AC2: 数字键多选一决策 (FR20)

```gherkin
Given three-panel 模式下当前选中的是多选异常审批
When 操作者按 1 / 2 / 3 / ... 数字键
Then 从 `payload.options` 或 `APPROVAL_DEFAULT_VALID_OPTIONS` 解析对应 option key
And 通过 `submit_approval_decision()` 写入 SQLite approvals 表
And decision 为对应选项的 key（如 "restart" / "resume" / "abandon" 或 "revert" / "fix_forward" / "pause"）
And status 为 "approved"（多选审批统一写入 approved）
And decision_reason 记录为 `tui:{digit} -> {decision}` 的 deterministic 格式
And 触发 nudge 通知 Orchestrator
And 右下面板显示 "$muted 已提交，等待处理" 即时反馈
And 数字键超出选项范围时不写 SQLite、不给假成功提示
```

### AC3: 异常审批类型覆盖（必须对齐当前 schema / consumer）

```gherkin
Given 各异常审批类型
When ExceptionApprovalPanel 渲染
Then 按以下映射展示标题与合法选项：

| 类型 | 标题 | 选项 |
|------|------|------|
| regression_failure | ✖ REGRESSION FAILURE | [1] revert  [2] fix_forward  [3] pause |
| session_timeout | ⏱ SESSION TIMEOUT | [1] restart  [2] resume  [3] abandon |
| crash_recovery | ↩ CRASH RECOVERY | [1] restart  [2] resume  [3] abandon |
| precommit_failure | 🔧 PRE-COMMIT FAILURE | [1] retry  [2] manual_fix  [3] skip |
| rebase_conflict | ⚡ REBASE CONFLICT | [1] manual_resolve  [2] skip  [3] abandon |
| needs_human_review | 👁 NEEDS HUMAN REVIEW | [1] retry  [2] skip  [3] escalate |
| convergent_loop_escalation | 🔄 CONVERGENT LOOP ESCALATION | [1] retry  [2] skip  [3] escalate |
```

### AC4: 左面板异常审批排序与高亮

```gherkin
Given 左面板审批列表中包含异常审批
When ApprovalCard 渲染异常审批项
Then 异常审批使用 `$error` 风格图标和专用行样式，区别于 6.3a 的常规审批
And risk_level=high 时行背景使用暗红色强调
And 异常审批排在常规审批之前，常规审批继续排在 story 之前
And 在异常审批组与常规审批组内部，保留 `get_pending_approvals()` 当前顺序，避免刷新时列表跳动
```

### AC5: 详情面板上下文信息（必须基于真实 payload 合同）

```gherkin
Given 选中异常审批项
When 右上面板渲染 ExceptionApprovalPanel
Then 根据 approval_type 和当前真实 payload 展示结构化上下文：
  - regression_failure: queue 已冻结 + story_id + payload.reason（若存在）
  - session_timeout: task_id + elapsed_seconds
  - crash_recovery: phase + task_id
  - rebase_conflict: conflict_files + stderr 摘要 + story.worktree_path（若 dashboard 已知）
  - precommit_failure: error_output 摘要
  - convergent_loop_escalation: rounds_completed + open_blocking_count + final_convergence_rate + unresolved_findings 数量
  - needs_human_review: skill_type + parser_mode + raw_output_preview + task_id（若存在）
And 缺失字段时省略对应行，而不是伪造 `failed_test` / `blocked_count` / `worktree_path`
And 底部显示 `[d] 查看更多上下文`；若 payload 中没有现成报告路径，则 `d` 键展示原始 payload / stderr / unresolved_findings 详情
```

### AC6: 6.3a fallback 文案收敛与响应式边界

```gherkin
Given Story 6.3a 中多选审批的 CLI/6.3b fallback 提示
When 当前布局为 three-panel 且选中多选异常审批
Then 右下面板改为数字键动作提示，不再显示旧的 CLI / 6.3b fallback 文案
And y/n 键在异常审批上无效（仅数字键生效）
And tabbed 模式继续保留现有 `[1]-[4]` 切页契约，不把 plain `1`-`4` 重新绑定为异常审批提交
And tabbed 模式至少要可见化异常审批摘要与风险样式，但不要求在该模式下直接提交多选决策
```

## Tasks / Subtasks

### ⚠️ 重要前提：Story 6.3a 已建立的基础设施

Story 6.3a 已实现：
- `ApprovalCard` Widget（折叠态单行渲染）
- DashboardScreen 统一选择管理（`_sorted_item_ids` 含 `approval:` 和 `story:` 前缀）
- `y`/`n`/`d` 键位绑定 + `action_approve`/`action_reject`/`action_toggle_detail`
- `ATOApp.submit_approval_decision()` 方法
- `is_binary_approval()` / `resolve_binary_decision()` 共享 helper
- 多选审批的 fallback 提示（"此审批需多选，请使用 CLI 或等待 6.3b"）
- 审批数据加载（`ATOApp._load_data()` → `get_pending_approvals()`）

**本 story 只新增 ExceptionApprovalPanel 与多选异常审批交互，不重建 6.3a 已有的 binary 审批路径。**

- [x] Task 1: ExceptionApprovalPanel Widget (AC: #1, #5)
  - [x] 1.1 在 `src/ato/tui/widgets/exception_approval_panel.py` 创建 `ExceptionApprovalPanel(Widget)` 类
  - [x] 1.2 定义属性：`approval_id`, `story_id`, `approval_type`, `risk_level`, `payload_dict`（解析后的 dict）, `options`（选项列表）, `expanded_context`
  - [x] 1.3 实现 `render()` 方法——多行块渲染：
    - 标题行：类型图标 + 异常类型描述 + story_id
    - "发生了什么"：来自 `get_exception_context()` 的 what 文本
    - "影响范围"：来自 `get_exception_context()` 的 impact 文本
    - "你的选项"：每行一个 `[N] 选项描述`
    - 底部：`按 1/2/3 选择，[d] 查看更多上下文`
  - [x] 1.4 使用 TCSS class / variant 表达边框颜色：`high` → `$error`，`medium` → `$warning`，其他 → `$surface`
  - [x] 1.5 实现 `_format_context(approval_type, payload_dict)` 方法——严格对齐 AC5 的真实 payload 字段，字段缺失时优雅降级
  - [x] 1.6 实现 `_format_options(options)` 方法——生成带数字键前缀的选项列表
  - [x] 1.7 实现 `update_data()` 方法批量更新属性

- [x] Task 2: DashboardScreen 数字键处理与异常审批提交 (AC: #2, #6)
  - [x] 2.1 为 `1`-`9` 增加异常审批数字键处理入口（可用 binding 或 key handler），但必须满足：
    - three-panel 模式下，选中多选异常审批时 plain 数字键可提交
    - tabbed 模式下，不得破坏 `ATOApp` 已有的 `[1]-[4]` 切页快捷键
  - [x] 2.2 实现 `_handle_option_key(index)` 或等价方法：
    - 检查当前选中项是否为异常审批（`is_binary_approval() == False`）
    - 从 `payload.options` 或 `APPROVAL_DEFAULT_VALID_OPTIONS` 获取选项列表
    - 验证数字键对应的索引在选项范围内
    - 调用 `ATOApp.submit_approval_decision(approval_id, status="approved", decision=option_key, decision_reason=f"tui:{N} -> {option_key}")`
    - 成功前先加入 `_submitted_approvals` 集合，失败时回滚
  - [x] 2.3 确保 `y`/`n` 在异常审批上无效（复用 6.3a 的 `is_binary_approval()` 边界）
  - [x] 2.4 数字键在常规审批、story 选中、无选中项、超出范围时全部 no-op

- [x] Task 3: 右面板联动——异常审批展示 (AC: #1, #5, #6)
  - [x] 3.1 修改 `DashboardScreen._update_detail_panel()`：
    - 当选中的审批 `is_binary_approval() == False` 且布局为 three-panel 时，渲染 ExceptionApprovalPanel 到 `#right-top-content`
    - 当选中的审批 `is_binary_approval() == True` 时，保持现有 `_render_approval_context()` 行为
  - [x] 3.2 修改 `DashboardScreen._update_action_panel()`：
    - three-panel 异常审批时，右下面板显示数字键动作标签 + `[d] 更多上下文`
    - 提交后显示 "$muted 已提交，等待处理"
  - [x] 3.3 `d` 键在异常审批上切换 richer context 视图：
    - 若 payload 已有 `stderr` / `error_output` / `unresolved_findings` / `raw_output_preview` / `round_summaries`，优先展开这些真实字段
    - 若 payload 没有报告路径，不要伪造 `agent 输出日志路径`

- [x] Task 4: 左面板异常审批排序与高亮 (AC: #4, #6)
  - [x] 4.1 修改 `DashboardScreen._update_story_list()` 排序逻辑：
    - 异常审批（`is_binary_approval() == False`）排在常规审批之前
    - 常规审批排在 story 之前
    - 异常审批组、常规审批组内部保持当前 pending approval 顺序
  - [x] 4.2 修改 `ApprovalCard` 渲染 / 样式：
    - 异常审批使用 `$error` 风格图标（覆盖 6.3a 常规审批的 `$warning` 风格）
    - risk_level=high 时附加暗红背景 class
  - [x] 4.3 tabbed 模式 `[1]审批` Tab 保留异常审批列表可见化与增强样式，删除旧 CLI fallback 文案，但不改变 `[1]-[4]` 切页契约

- [x] Task 5: 6.3a fallback 文案退场 (AC: #6)
  - [x] 5.1 在 three-panel 的 `DashboardScreen._update_action_panel()` 中删除多选审批旧 fallback 提示文本
  - [x] 5.2 在 `[1]审批` Tab 中删除 `↳ 此审批需多选，请使用 CLI 或等待 6.3b` 子文本
  - [x] 5.3 确保多选异常审批现在有专用 detail/action 路径，而不是继续沿用 CLI fallback copy

- [x] Task 6: 共享 Helper 扩展 (AC: #2, #3, #5)
  - [x] 6.1 在 `src/ato/approval_helpers.py` 新增 `resolve_multi_decision(approval_type, index, payload)` 方法：
    - 优先使用 `payload.options`
    - payload 无 options 时回退到 `APPROVAL_DEFAULT_VALID_OPTIONS`
    - 返回 `(decision_key, status="approved")`
  - [x] 6.2 新增 `get_exception_context(approval_type, payload_dict)` 方法：
    - 返回结构化的 `(what, impact)` 文本
    - 严格对齐当前真实 payload 合同，字段缺失时优雅降级
  - [x] 6.3 新增 `format_option_labels(approval_type, options)` 方法：
    - 返回用户可读的中文标签列表（供 ExceptionApprovalPanel 展示）

- [x] Task 7: TCSS 样式 (AC: #1, #4)
  - [x] 7.1 在 `src/ato/tui/app.tcss` 新增 `ExceptionApprovalPanel` 样式：
    - 最小高度 8 行，内边距 1
    - 默认 `border: solid $surface`
  - [x] 7.2 新增 risk variant class（如 `.exception-approval-high`, `.exception-approval-medium`）控制边框颜色
  - [x] 7.3 新增 `.approval-exception-row` / `.approval-exception-high` 样式，用于左面板异常审批高亮
  - [x] 7.4 新增 `.exception-approval-option` 样式：数字键高亮

- [x] Task 8: widgets 模块导出 (AC: #1)
  - [x] 8.1 在 `src/ato/tui/widgets/__init__.py` 导出 `ExceptionApprovalPanel`
  - [x] 8.2 更新 `__all__` 列表

- [x] Task 9: 单元测试 (AC: #1-#5)
  - [x] 9.1 `tests/unit/test_exception_approval_panel.py`（新建文件）：
    - `test_regression_failure_panel_renders_three_elements` — 三要素渲染：发生了什么 + 影响范围 + 选项
    - `test_regression_failure_panel_red_border` — risk_level=high → $error 边框
    - `test_session_timeout_panel_yellow_border` — risk_level=medium → $warning 边框
    - `test_panel_title_includes_icon_and_type` — 标题包含图标 + 类型描述
    - `test_options_numbered_correctly` — 选项带正确数字键前缀
    - `test_format_context_rebase_conflict_uses_conflict_files_and_stderr` — rebase 冲突上下文使用真实 payload 字段
    - `test_format_context_convergent_loop_uses_round_payload` — escalation 上下文含 rounds/open_blocking/convergence
    - `test_format_context_gracefully_handles_missing_fields` — 不伪造不存在的 payload 字段
    - `test_all_current_exception_types_covered` — 所有当前多选异常类型都有 context 格式化
  - [x] 9.2 `tests/unit/test_multi_decision.py`（新建文件）：
    - `test_resolve_multi_decision_valid_index` — 有效索引返回正确 decision
    - `test_resolve_multi_decision_out_of_range` — 超出范围抛出 ValueError
    - `test_resolve_multi_decision_uses_payload_options` — 优先使用 payload.options
    - `test_resolve_multi_decision_falls_back_to_defaults` — payload 无 options 时使用默认
    - `test_needs_human_review_options_align_schema` — `needs_human_review` 对齐 `retry/skip/escalate`
    - `test_convergent_loop_escalation_options_align_schema` — `convergent_loop_escalation` 对齐 `retry/skip/escalate`

- [x] Task 10: 集成测试 (AC: #1-#6)
  - [x] 10.1 `tests/integration/test_tui_exception_approval.py`（新建文件）：
    - `test_regression_failure_renders_exception_panel_in_three_panel` — regression_failure 审批在右上面板渲染 ExceptionApprovalPanel
    - `test_number_key_1_selects_revert_in_three_panel` — 按 `1` 写入 decision="revert" + status="approved"
    - `test_number_key_on_binary_approval_ignored` — 数字键在常规审批上无效
    - `test_y_n_on_exception_approval_ignored` — y/n 在异常审批上无效
    - `test_exception_approval_sorts_before_normal` — 异常审批排在常规审批之前
    - `test_three_panel_fallback_message_removed` — 三面板不再显示旧 CLI / 6.3b fallback 文案
    - `test_submitted_exception_shows_feedback` — 提交后显示即时反馈
    - `test_exception_approval_disappears_after_decision` — 决策后下一轮轮询消失
    - `test_tab_mode_number_keys_still_switch_tabs` — tabbed 模式仍保留 `[1]-[4]` 切页契约
    - `test_tab_mode_exception_visible_without_old_fallback_copy` — `[1]审批` Tab 仍可见异常审批且不显示旧 fallback 子文本

## Dev Notes

### 核心架构约束（同 6.3a）

- **Textual ≥2.0** — 组件继承 `Widget`，使用 `render()` 返回 `Rich.Text`
- **数据由 ATOApp 提供** — Widget 不自行创建 SQLite 连接
- **TUI 写入路径** — `submit_approval_decision()` 已在 6.3a 实现，直接复用
- **TUI↔Orchestrator 解耦** — 通过 SQLite + nudge 通信
- **CSS 与 Python 分离** — `src/ato/tui/app.tcss` 是全局主题入口
- **响应式约束** — 当前 `ATOApp` 在 tabbed 模式把 plain `1`-`4` 绑定给 `switch_tab()`；6.3b 不能悄悄破坏这个已存在契约

### 已存在的关键组件（复用，不重建）

| 组件 | 文件 | 说明 |
|------|------|------|
| `ApprovalCard` | `src/ato/tui/widgets/approval_card.py` | 折叠态单行渲染，需增强异常审批样式 |
| `DashboardScreen` | `src/ato/tui/dashboard.py` | 统一选择管理 + y/n/d 键位，需扩展异常审批数字键处理 |
| `submit_approval_decision()` | `src/ato/tui/app.py` | SQLite 写入 + nudge，直接复用 |
| `is_binary_approval()` | `src/ato/approval_helpers.py` | 判断是否二选一，用于区分交互模式 |
| `format_approval_summary()` | `src/ato/approval_helpers.py` | 摘要生成，ApprovalCard 使用 |
| `APPROVAL_TYPE_ICONS` | `src/ato/models/schemas.py` | 类型图标映射 |
| `APPROVAL_DEFAULT_VALID_OPTIONS` | `src/ato/models/schemas.py` | 默认合法选项 |
| `_submitted_approvals` | `src/ato/tui/dashboard.py` | 即时反馈集合 |
| `map_risk_to_color()` | `src/ato/tui/theme.py` | 风险 → 颜色映射 |

### 当前真实多选异常审批合同

| approval_type | 创建方 | 当前 payload 合同 |
|---|---|---|
| `session_timeout` | `src/ato/core.py::_check_interactive_timeouts()` | `task_id`, `elapsed_seconds`, `options=["restart","resume","abandon"]` |
| `crash_recovery` | `src/ato/core.py::_mark_dispatch_failed()` | `task_id`, `phase`, `options=["restart","resume","abandon"]` |
| `regression_failure` | `src/ato/merge_queue.py::_handle_regression_failure()` / crash recovery path | `options=["revert","fix_forward","pause"]`，有些路径会附带 `reason` |
| `rebase_conflict` | `src/ato/merge_queue.py::_handle_rebase_conflict()` | `options`, `conflict_files`, `stderr` |
| `precommit_failure` | `src/ato/merge_queue.py::_handle_precommit_failure()` | `options`, `error_output` |
| `needs_human_review` | `src/ato/adapters/bmad_adapter.py::record_parse_failure_approval()` | `reason`, `skill_type`, `parser_mode`, `error`, `raw_output_preview`, `task_id?`, `options=["retry","skip","escalate"]` |
| `convergent_loop_escalation` | `src/ato/convergent_loop.py::_build_escalation_payload()` | `rounds_completed`, `open_blocking_count`, `final_convergence_rate`, `round_summaries`, `unresolved_findings`, `options=["retry","skip","escalate"]` |

### ExceptionApprovalPanel 渲染格式（对齐 UX 方向，但必须允许真实字段缺失）

```
┌──────────────────────────────────────────────────────────────┐
│  ✖ REGRESSION FAILURE                         story-005     │
│                                                              │
│  Regression 在 main 上失败。Merge queue 已自动冻结。         │
│                                                              │
│  原因: Orchestrator crashed during regression test           │
│  Story: story-005                                            │
│                                                              │
│  [1] Revert — 回滚当前 merge                                 │
│  [2] Fix forward — 保持 queue 冻结并创建修复路径              │
│  [3] Pause — 保持冻结，等待人工处理                          │
│                                                              │
│  按 1/2/3 选择，[d] 查看更多上下文                           │
└──────────────────────────────────────────────────────────────┘
```

如上游 payload 后续增加 `failed_test`、`blocked_count` 等字段，可作为增强显示；**当前 story 不得把这些字段写成实现必需前提**。

### 异常类型上下文生成逻辑

```python
def get_exception_context(approval_type: str, payload: dict[str, object]) -> tuple[str, str]:
    """返回 (what, impact) 文本；字段缺失时优雅降级。"""
    match approval_type:
        case "session_timeout":
            elapsed = payload.get("elapsed_seconds")
            task_id = payload.get("task_id", "未知 task")
            what = "Interactive session 已超过阈值，正在等待操作者决策。"
            impact = f"task_id: {task_id}\nelapsed_seconds: {elapsed}" if elapsed else f"task_id: {task_id}"
        case "crash_recovery":
            what = "任务在 dispatch 或执行过程中失败，需要决定如何恢复。"
            impact = f"phase: {payload.get('phase', '未知')}\ntask_id: {payload.get('task_id', '未知 task')}"
        case "rebase_conflict":
            files = payload.get("conflict_files", [])
            what = "Worktree rebase 到 main 时产生合并冲突。"
            impact = f"conflict_files: {files}\nstderr: {payload.get('stderr', '')}"
        case "precommit_failure":
            what = "Pre-commit 检查失败，需要决定重试、人工修复或跳过。"
            impact = f"error_output: {payload.get('error_output', '')}"
        case "needs_human_review":
            what = "BMAD 解析失败，需要人工决定是否重试或升级。"
            impact = (
                f"skill_type: {payload.get('skill_type', '未知')}\n"
                f"parser_mode: {payload.get('parser_mode', '未知')}\n"
                f"preview: {payload.get('raw_output_preview', '')}"
            )
        case "convergent_loop_escalation":
            what = "Convergent Loop 达到上限仍未收敛。"
            impact = (
                f"rounds_completed: {payload.get('rounds_completed', '?')}\n"
                f"open_blocking_count: {payload.get('open_blocking_count', '?')}\n"
                f"final_convergence_rate: {payload.get('final_convergence_rate', '?')}"
            )
        case "regression_failure":
            what = "Regression 在 main 上失败，merge queue 已冻结。"
            impact = f"reason: {payload.get('reason', '见 queue / payload 上下文')}"
        case _:
            what = approval_type
            impact = ""
    return (what, impact)
```

### 与 Epic 4 / current consumer 的交互点

- `regression_failure` approval 当前由 `src/ato/merge_queue.py` 创建
- 多选 decision 当前由 `src/ato/core.py::_handle_approval_decision()` 消费，而不是 `transition_queue.py`
- `session_timeout` / `crash_recovery` 的 restart/resume 决策也由 `src/ato/core.py::_handle_approval_decision()` 消费
- 因此 6.3b 的 option key 必须严格对齐 `APPROVAL_DEFAULT_VALID_OPTIONS` 与 `_handle_approval_decision()` 的实际分支

### Scope Boundary

- 本 Story 只交付 **three-panel 模式** 下的多选异常审批 TUI 提交路径
- **常规二选一审批**（y/n）已在 Story 6.3a 完成，不修改
- **tabbed 模式** 在本 Story 中只要求异常审批可见化与样式增强；plain `1`-`4` 继续保留给切页
- **Story 详情钻入**（Enter/f/c/h/l）属于 Story 6.4
- **搜索面板**（`/`）属于 Story 6.5
- 本 Story 不修改 CLI 路径；现有 `ato approval-detail` 仅作为 parity / 调试参考

### 性能要求

- **NFR3**：单次刷新渲染 ≤500ms — ExceptionApprovalPanel 只做 Rich Text 拼接与轻量 payload 格式化
- 异常审批数据与常规审批共享同一 `_load_data()` 轮询，不额外增加 SQLite 查询

### 测试策略

- **单元测试**：直接实例化 Widget，验证 `render()` 输出、context 格式化和 option 映射
- **集成测试**：使用 `ATOApp(db_path=...)` + `app.run_test()` 验证 three-panel 提交路径与 tabbed 模式快捷键边界
- 参考 `tests/unit/test_approval_card.py`、`tests/unit/test_dashboard_approval.py`、`tests/integration/test_tui_pilot.py`、`tests/integration/test_tui_responsive.py`

### Project Structure Notes

**新增文件：**
- `src/ato/tui/widgets/exception_approval_panel.py` — ExceptionApprovalPanel Widget
- `tests/unit/test_exception_approval_panel.py` — 面板渲染单元测试
- `tests/unit/test_multi_decision.py` — 多选决策 helper 单元测试
- `tests/integration/test_tui_exception_approval.py` — 异常审批集成测试

**修改文件：**
- `src/ato/tui/dashboard.py` — 异常审批数字键处理 + three-panel detail/action panel + 排序增强 + fallback 文案移除
- `src/ato/tui/widgets/approval_card.py` — 异常审批行样式增强
- `src/ato/tui/widgets/__init__.py` — 导出 ExceptionApprovalPanel
- `src/ato/tui/app.tcss` — ExceptionApprovalPanel 样式 + 异常审批行样式
- `src/ato/approval_helpers.py` — 新增 `resolve_multi_decision()`, `get_exception_context()`, `format_option_labels()`

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 6.3b] — Epic 原始 AC
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md#Flow 5: 异常处理（Regression 失败 / 成本超限 / 超时）] — 异常处理流与 UI 方向
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md#5. ExceptionApprovalPanel] — ExceptionApprovalPanel / ApprovalCard 区别
- [Source: _bmad-output/planning-artifacts/prd.md#FR19, FR20] — 审批查看与决策持久化
- [Source: _bmad-output/planning-artifacts/architecture.md#Decision 2] — TUI↔Orchestrator 通信（SQLite + nudge）
- [Source: _bmad-output/implementation-artifacts/6-3a-standard-approval-interaction.md] — 6.3a 已交付的 binary 审批基础设施
- [Source: _bmad-output/implementation-artifacts/4-4-notification-cli-quality.md] — CLI 异常审批 detail 展示基线
- [Source: src/ato/core.py] — approval decision 实际消费逻辑
- [Source: src/ato/merge_queue.py] — regression / rebase / precommit approval 实际创建逻辑
- [Source: src/ato/convergent_loop.py] — convergent loop escalation payload 合同
- [Source: src/ato/adapters/bmad_adapter.py] — needs_human_review payload 合同
- [Source: src/ato/models/schemas.py] — approval type / icon / valid options 常量
- [Source: src/ato/models/db.py] — `get_pending_approvals()` 当前排序语义
- [Source: src/ato/tui/app.py] — tabbed 模式 `[1]-[4]` 切页绑定
- [Source: src/ato/tui/dashboard.py] — 当前 detail/action panel 与 fallback 实现
- [Source: tests/integration/test_tui_responsive.py] — tabbed 模式数字键切页现有测试契约

### Change Log

- 2026-03-27: create-story 创建 — 基于 Epic 6 / PRD / 架构 / UX spec / 前序 Story 6.3a 生成 6.3b 初稿
- 2026-03-27: validate-create-story 修订 —— 删除 UX-only `critical_timeout` / `cascade_failure` 实现合同；将 `needs_human_review` 与 `convergent_loop_escalation` 选项对齐当前 schema / core consumer；收紧上下文字段到真实 payload 合同；明确 three-panel 数字键与 tabbed `[1]-[4]` 切页的边界；修正 regression flow 的实际创建/消费代码引用；补回模板 validation note 与 Change Log
- 2026-03-27: dev-story 实现 — 全部 10 个 Task 完成，新增 4 个文件 + 修改 8 个文件，24 个单元测试 + 10 个集成测试通过，全量回归 1308 passed

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6 (1M context)

### Debug Log References

### Completion Notes List

- ✅ Task 1: ExceptionApprovalPanel Widget — 新建 widget 支持多行块渲染（标题 + 三要素 + 数字键选项），CSS class 驱动 risk 边框颜色
- ✅ Task 2: DashboardScreen 数字键处理 — on_key + ATOApp.action_switch_tab 委托实现 three-panel 模式数字键选择，tabbed 模式保持 [1]-[4] 切页
- ✅ Task 3: 右面板联动 — _render_exception_approval_context() 渲染异常审批上下文，_update_action_panel() 显示数字键动作提示
- ✅ Task 4: 左面板排序与高亮 — _sort_approvals() 异常审批排前，ApprovalCard 增加 $error 风格图标和 approval-exception-high 背景
- ✅ Task 5: fallback 文案退场 — 删除所有 "此审批需多选，请使用 CLI 或等待 6.3b" 文案，y/n 在异常审批上静默 no-op
- ✅ Task 6: 共享 helper — resolve_multi_decision, get_exception_context, format_option_labels, get_options_for_approval, get_exception_type_title
- ✅ Task 7: TCSS 样式 — ExceptionApprovalPanel 基础样式 + risk variant + 左面板异常行高亮
- ✅ Task 8: Widget 导出 — ExceptionApprovalPanel 加入 widgets/__init__.py
- ✅ Task 9: 13 + 11 = 24 个单元测试全部通过
- ✅ Task 10: 10 个集成测试全部通过
- ✅ 全量回归：1308 passed, 0 failed

### Implementation Plan

- 先实现共享 helpers（Task 6），因为 ExceptionApprovalPanel 和 DashboardScreen 都依赖它们
- ExceptionApprovalPanel 使用 Rich.Text 直接渲染（不是 DOM 挂载），保持与 ApprovalCard 一致的轻量设计
- 数字键路由通过 ATOApp.action_switch_tab 委托到 DashboardScreen._handle_option_key，避免 App 绑定吞掉按键事件
- 为 5-9 键增加 App 绑定（无 Tab 描述标签），覆盖所有可能的异常审批选项数
- 已更新 6.3a 旧 fallback 测试 test_multi_option_approval_shows_cli_fallback → test_multi_option_approval_shows_digit_key_options

### File List

**新增文件：**
- src/ato/tui/widgets/exception_approval_panel.py — ExceptionApprovalPanel Widget
- tests/unit/test_exception_approval_panel.py — 面板渲染单元测试（13 tests）
- tests/unit/test_multi_decision.py — 多选决策 helper 单元测试（11 tests）
- tests/integration/test_tui_exception_approval.py — 异常审批集成测试（10 tests）

**修改文件：**
- src/ato/approval_helpers.py — 新增 resolve_multi_decision, get_exception_context, format_option_labels, get_options_for_approval, get_exception_type_title
- src/ato/tui/dashboard.py — 数字键处理 + exception panel 渲染 + 排序 + fallback 移除
- src/ato/tui/widgets/approval_card.py — 异常审批行样式增强（$error 图标 + high-risk 背景 class）
- src/ato/tui/widgets/__init__.py — 导出 ExceptionApprovalPanel
- src/ato/tui/app.py — action_switch_tab 增加 three-panel 委托 + 5-9 键绑定
- src/ato/tui/app.tcss — ExceptionApprovalPanel 样式 + risk variant + 异常行高亮
- tests/integration/test_tui_pilot.py — 更新旧 fallback 测试为 digit key 测试
- _bmad-output/implementation-artifacts/sprint-status.yaml — 6-3b status → review
