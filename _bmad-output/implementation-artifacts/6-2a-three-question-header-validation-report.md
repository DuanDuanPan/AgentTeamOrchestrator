# Story 验证报告：6.2a ThreeQuestionHeader Widget

验证时间：2026-03-25
Story 文件：`_bmad-output/implementation-artifacts/6-2a-three-question-header.md`
验证模式：`validate-create-story`
结果：PASS（已应用修正）

## 摘要

该 story 的方向是对的，但原稿里有 5 个会直接误导开发实现的缺口，已在 story 文件中修正：

1. 它把 `Header` 集成写成“替换 / 内嵌 / 下方放置”三选一，同时 full 模式示例又额外加了 `ATO` 前缀，和当前 `Header + DashboardScreen + Footer` 骨架契约冲突。
2. 它要求把 `stories` 查询改成 `GROUP BY status`，却没有把“继续维护 `story_count`”写成硬约束，开发时很容易把现有 `DashboardScreen.update_content(...)` 数据流一起打断。
3. 它把 `seconds_ago` 的计算写成“记录 `_last_refresh_time` 后再立即求差值”，这会让 UI 在每轮刷新后几乎恒为 `0s`。
4. 它要求显示 `⏸ 已暂停`，但当前 story 自己声明的数据源只有 `stories + approvals + cost_log`，这组数据并不能区分“系统暂停”和“当前空闲”。
5. 它没有把 `<100` 列 degraded 模式下 ThreeQuestionHeader 仍需可见写成明确实现要求，测试落点也只写到了 `test_tui_pilot.py`，容易漏掉 `test_tui_responsive.py` 的往返断点验证。

## 已核查证据

- 本地仓库工件：
  - `_bmad-output/planning-artifacts/epics.md`
  - `_bmad-output/planning-artifacts/ux-design-specification.md`
  - `_bmad-output/planning-artifacts/architecture.md`
  - `_bmad-output/planning-artifacts/prd.md`
  - `_bmad-output/implementation-artifacts/6-1a-tui-launch-sqlite-connection.md`
  - `_bmad-output/implementation-artifacts/6-1b-dark-theme-responsive-layout.md`
- 当前代码：
  - `src/ato/tui/app.py`
  - `src/ato/tui/dashboard.py`
  - `src/ato/tui/theme.py`
  - `src/ato/tui/app.tcss`
  - `src/ato/models/db.py`
  - `tests/integration/test_tui_pilot.py`
  - `tests/integration/test_tui_responsive.py`

## 发现的关键问题

### 1. Header 集成契约不清，且 full 模式示例与四区域 AC 自相矛盾

原 story 同时给了三种方向：

- 替换 `Header()`
- 把 ThreeQuestionHeader 内嵌到 Header
- 放在 Header 之下

但当前仓库事实是：

- `ATOApp.compose()` 已稳定为 `Header + DashboardScreen + Footer`
- 6.1a / 6.1b 都围绕这个骨架写了 story 与测试
- AC1 明确说顶栏只有四个区域，而原 Task 2.4 却写成 `ATO │ ● 3 项运行中 │ ...`

这会直接把实现者带到两条错误路径之一：

- 为了接入三问顶栏去替换 Textual `Header`，破坏现有骨架和测试
- 在保留 `Header` 的同时，再在 full 模式里重复显示项目名，做出“五段式”而不是“四区域”顶栏

已应用修正：

- Task 3.3 改为明确契约：保留 `Header()`，并在其下方插入 `ThreeQuestionHeader()`
- Dev Notes 的 Header 集成方案改成单一路径，不再保留“替换 Header”的开放选项
- Task 2.4 的 full 模式示例移除了冗余的 `ATO │`

### 2. grouped query 变更没有把 `story_count` 保持写成硬约束

当前 `ATOApp._load_data()` / `DashboardScreen.update_content(...)` 仍在使用总 story 数。原 story 虽然在 Dev Notes 的 SQL 注释里提到：

- `story_count = sum of all counts`

但 Tasks 没把它写成必须保留的契约，只写了新增 `running_stories` / `error_stories`。这很容易让开发者：

- 删除现有 `story_count`
- 只服务于新 header
- 结果把 6.1a / 6.1b 现有 dashboard 占位和测试一起带崩

已应用修正：

- Task 3.1 明确要求 grouped query 仍需产出 `story_count = sum(all grouped counts)`
- Task 3.2 改为新增 `running_count` / `error_count`，同时保留现有 `story_count`、`pending_approvals`、`today_cost_usd`、`last_updated`
- Task 3.4 明确 `_update_dashboard()` 既要保留 `dashboard.update_content(...)`，又要新增 header 更新
- “不要重新实现”增加了“不移除 `story_count` / 不破坏 `DashboardScreen.update_content(...)` 契约”的禁止项

### 3. `seconds_ago` 计算时机会让每轮刷新接近 `0s`

原 Task 3.5 的写法是：

- 在 `_load_data()` 记录 `_last_refresh_time = datetime.now(UTC)`
- `update_data()` 时计算 `(now - _last_refresh_time).seconds`

如果按这个顺序做，开发者会在刚写入新时间戳后立刻求差值，结果每轮几乎都是 `0s`。这和 AC / UX 示例里的 `更新 2s前` 不一致，也会让顶栏时间区域没有信息价值。

已应用修正：

- Task 3.5 改为先基于“上一次” `_last_refresh_time` 计算 elapsed seconds，再用当前时间覆盖 `_last_refresh_time`
- 数据源说明里的更新时间公式同步改成 `elapsed = now - previous_last_refresh_time`

### 4. `⏸ 已暂停` 在当前声明的数据源下不可判定

原 story 的 AC4 和 Task 6.3 把“系统暂停”当成要直接实现的状态，但它自己声明的数据源只有：

- `stories`
- `approvals`
- `cost_log`

这三类数据最多能区分：

- 有运行中的 story
- 有 blocked story
- 有无 pending approval
- 当下空闲

它们不能单独证明“系统暂停”。如果按原稿实现，开发者很可能会把：

- “无 running + 无 pending”

错误等同于：

- `⏸ 已暂停`

这会把“系统空闲”错误渲染成“暂停”。

已应用修正：

- AC4 改成“可判定状态显示边界”
- 状态映射表把 `⏸ 已暂停` 改成“仅在存在显式 paused 信号时”
- 新增关键边界说明：不接入 `tasks.status='paused'` 或等价 pause source 时，只实现正常 / 异常 / 空闲 / 审批
- Task 1.4 和 Task 6.3 一并改成“没有显式 paused 信号时不得伪造暂停”

### 5. degraded 模式和响应式测试落点写得不够完整

6.1b 已明确：

- `ThreeQuestionHeader` 在所有断点下始终可见
- `<100` 列是 degraded 模式

但原 Task 4.1 只写到了 `100-139 → minimal`，没有把 `<100` 的 degraded 模式写成硬要求。Task 7 也只点名了 `test_tui_pilot.py`，很容易让开发者漏掉 `test_tui_responsive.py` 中对断点切换和往返路径的验证。

已应用修正：

- Task 4.1 明确 `<140 → minimal`，并点名 `<100` 的 degraded 模式仍需显示 minimal header
- Task 7.1 改成同时覆盖 `test_tui_pilot.py` 和 `test_tui_responsive.py`
- Task 7.4 增加 `150 → 120 → 80 → 150` 的 three-panel / tabbed / degraded 往返路径要求

## 已应用增强

- 移除了 `src/ato/tui/app.py:107`、UX 行号等易漂移引用，减少 story 老化速度
- 将 `running_stories` / `error_stories` 命名统一为与 widget 对齐的 `running_count` / `error_count`
- 在 Change Log 中记录了本次 validate-create-story 的具体修订点

## 剩余风险

- 如果产品仍坚持在本 Story 的 MVP 范围内显示 `⏸ 已暂停`，那就必须先补一个明确的数据源契约，例如 `tasks.status='paused'` 聚合或单独的 orchestrator pause signal；当前 story 只是把“不许猜 paused”写清楚了，并没有凭空创造该信号。
- 当前本次工作只修改了 story 文档与验证报告，没有运行 Textual 级 smoke 验证；不过这次修订的目标是收紧实现契约，不涉及运行时代码。

## 最终结论

修正后，该 story 已从“方向正确但存在多个实现岔路”变成“可以直接交给 dev-story 执行”的状态。高风险误导点已经移除：不会再为了三问顶栏去替换 `Header`，不会再因 grouped query 丢掉现有 `story_count` 数据流，不会再把每轮刷新都算成 `0s`，也不会再把“空闲”误报成“暂停”。
