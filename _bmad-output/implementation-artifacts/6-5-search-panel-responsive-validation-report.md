# Story 验证报告：6.5 搜索面板与响应式布局完善

验证时间：2026-03-28
Story 文件：`_bmad-output/implementation-artifacts/6-5-search-panel-responsive.md`
验证模式：`validate-create-story`
结果：PASS（已应用修正）

## 摘要

该 story 的产品方向没有问题，但原稿里有 5 个会直接误导开发实现的合同缺口，已在 story 文件中修正：

1. 它把 `/` 搜索写成了“可执行命令”的通用 command palette，但当前 TUI 只有审批、切页、导航这些内部动作，并没有通用 CLI 命令执行入口。
2. 它假设 `Input` 获得焦点后，其他快捷键天然不会触发；但当前代码里 `ATOApp` 已经全局绑定了 `1-9`，`DashboardScreen.on_key()` 也会拦截数字键做异常审批决策，如果不显式门控，搜索输入会和现有热键冲突。
3. 它为成本 Tab 设计了新的 SQL / helper 方向，但当前 `src/ato/models/db.py` 已有 `get_cost_by_story()`，并且已经返回 `story_id + total_cost_usd + call_count`，直接复用更稳。
4. 它把日志 Tab 数据源写成 `event_log` 或 `tasks`，但当前 schema 里根本没有 `event_log` 表，继续保留这条指引会逼着开发时编造不存在的数据源。
5. 它把搜索排序和跳转说得过于抽象，没有对齐当前 `DashboardScreen` 的真实 selection / re-render 管道，以及 `sort_stories_by_status()` 的现有展示语义。

## 已核查证据

- 规划与规范工件：
  - `_bmad-output/planning-artifacts/epics.md`
  - `_bmad-output/planning-artifacts/ux-design-specification.md`
  - `_bmad-output/planning-artifacts/architecture.md`
- 前序 story：
  - `_bmad-output/implementation-artifacts/6-3b-exception-approval-multi-select.md`
  - `_bmad-output/implementation-artifacts/6-4-story-detail-drill-navigation.md`
- 当前代码：
  - `src/ato/tui/app.py`
  - `src/ato/tui/dashboard.py`
  - `src/ato/tui/theme.py`
  - `src/ato/models/db.py`
  - `src/ato/tui/widgets/__init__.py`
  - `tests/integration/test_tui_responsive.py`
  - `tests/integration/test_tui_pilot.py`

## 发现的关键问题

### 1. “命令搜索 / 命令执行”超出了当前 TUI 的真实能力边界

原 story 的 AC1 / AC2 写成了：

- 支持“命令搜索”
- Enter 后“执行对应命令”

但当前真实代码里：

- `src/ato/tui/app.py` 只有 `quit` 和 `switch_tab(1-9)` 这类 app 级动作
- `src/ato/tui/dashboard.py` 只有选择移动、审批提交、审批详情切换
- story 自己的 Scope Boundary 又明确写了“命令执行 OUT”

这意味着原稿把产品愿景、当前架构能力、story scope 三者写成了互相打架的状态。开发者如果照原稿实现，要么会硬塞一个并不存在的 command runner，要么会在 AC 和 Scope Boundary 之间来回猜。

已应用修正：

- AC1 / AC2 收敛为：搜索 story、approval、以及 TUI 内部导航目标（如 Tab / 视图切换）
- 明确“不执行外部 CLI 命令”
- Task 2.2 / 3.2 同步改成内部导航动作，而不是扩展出新的命令执行系统

### 2. “Input focus 天然隔离快捷键”的假设不成立

原 story 在 Dev Notes 中写道：

- `Input` 获得焦点时，其他 Widget 的快捷键不会触发

但当前仓库的真实合同是：

- `src/ato/tui/app.py` 的 `ATOApp.BINDINGS` 已经把 `1-9` 绑定到 `action_switch_tab()`
- `src/ato/tui/dashboard.py::on_key()` 在 three-panel 模式会消费数字键用于异常审批多选
- `y/n/d` 等审批动作也已经存在于 `DashboardScreen.BINDINGS`

如果不显式加门控，用户在搜索框输入 `1`、`2`、`y`、`n` 时，就可能切到别的 Tab 或误提交审批。这不是视觉细节问题，而是会直接破坏交互正确性的合同错误。

已应用修正：

- Task 4.3 / 4.4 改成必须显式短路 `ATOApp` / `DashboardScreen` 现有按键路径
- Dev Notes 补充“必须维护 `search_active` 状态，不能只靠 focus”
- Task 11 新增输入期间不触发 `1-9` / `y` / `n` / `d` 的集成测试要求

### 3. 成本 Tab 指引忽略了现成的 `get_cost_by_story()` helper

原 story 的成本设计部分写成了：

- 继续基于 `_story_costs`
- 调用次数再额外 `SELECT COUNT(*)`
- `db.py` 可能新增 `get_cost_call_counts`

但当前 `src/ato/models/db.py::get_cost_by_story()` 已经返回：

- `story_id`
- `total_cost_usd`
- `call_count`

而且 CLI 里的 cost 汇总也已经在复用这个 helper。若继续按原稿设计，开发者大概率会重复造一个 helper，或者让 TUI 代码再次手写 SQL，形成不必要的重复逻辑。

已应用修正：

- Task 5.1 / 5.3 改成优先复用 `get_cost_by_story()`
- Project Structure Notes 把“新增 cost helper”收敛为“仅 recent events 查询如有复用价值再加 helper”
- 成本 Tab 设计说明中明确 `call_count` 直接来自现有 helper

### 4. 日志 Tab 误引用了不存在的 `event_log` 表

原 story 在日志 Tab 设计里给了三个数据源选项：

1. `event_log`
2. `tasks`
3. `approvals`

但当前代码库里根本没有 `event_log` 表或对应 CRUD/helper。全仓搜索只在测试 fixture 文档中出现过这几个字。把它写成实现选项，会让开发者误以为 schema 已经存在这张表。

已应用修正：

- Task 6.1 改成只允许基于当前真实存在的 `tasks + approvals` 事件源
- 日志 Tab 设计说明删除 `event_log` 选项
- 仅保留“如果查询在多处复用，再新增 helper”的扩展口

### 5. 搜索排序与跳转没有对齐当前 selection / status 排序合同

原 story 只写了：

- 精确匹配 > 前缀匹配 > 子串匹配
- `AWAITING > ACTIVE > DONE`
- Enter 后更新 `_selected_item_id` + `_selected_index`

但当前真实合同更具体：

- `src/ato/tui/theme.py::sort_stories_by_status()` 还包含 `running`、`frozen` 等展示语义，不只是 `awaiting/active/done`
- `DashboardScreen` 的选择更新并不只是改两个字段，还会调用 `_sync_selected_story_id()`、`_highlight_selected()`、`_update_detail_panel()`、`_update_action_panel()`

如果按原稿实现，搜索跳转很容易出现“内部状态改了，但右侧详情、底部操作提示、左侧高亮没同步”的半更新问题。

已应用修正：

- Task 2.4 对齐到 `sort_stories_by_status()` / `VISUAL_STATUS_SORT_ORDER`
- Task 3.2 写清楚必须复用当前 selection 刷新链路
- 单元测试要求同步改为验证“匹配优先级 + 当前 visual status / approval 排序语义”

## 已应用增强

- 补回了 create-story 模板里的 validation note 注释
- 增加了 `Change Log`，记录本次 validate-create-story 的修订点
- 将“全量测试”从易漂移的精确数量改成现有 TUI 回归套件范围，避免后续再次因为测试总数变化导致 story 过期

## 剩余风险

- Story 6.4 的 detail mode 目前还是前序 story 合同，不是当前代码里的已落地能力；6.5 实现时需要对接“已验证的 6.4 合同”，而不是盲目依赖当前 `dashboard.py` 里还不存在的 detail 状态字段。
- 如果产品后续仍希望 `/` 搜索真的变成通用 command palette，应该单列成新 story：那会涉及动作注册表、权限边界、快捷键冲突和错误反馈设计，已经超出当前 6.5 的合理范围。
- 本次只修订了 story 和验证报告，没有实现 UI 代码，也没有运行 Textual 测试；目标是先把实现合同收紧到不会误导 dev-story。

## 最终结论

修正后，这个 story 已经从“方向正确但实现合同有多处漂移”收敛成“可以直接交给 dev-story 执行”的状态。高风险误导点已经移除：不会再逼着开发者扩展出不存在的命令执行器，不会再假设搜索输入天然隔离所有热键，不会再重复造成本聚合 helper，也不会再围绕并不存在的 `event_log` 表设计日志面板。
