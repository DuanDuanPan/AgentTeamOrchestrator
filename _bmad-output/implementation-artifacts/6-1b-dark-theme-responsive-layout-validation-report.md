# Story 验证报告：6.1b Dark Theme + Responsive Layout

验证时间：2026-03-25
Story 文件：`_bmad-output/implementation-artifacts/6-1b-dark-theme-responsive-layout.md`
验证模式：`validate-create-story`
结果：PASS（已应用修正）

## 摘要

该 story 的方向是对的，但原稿里有 5 个会直接误导开发实现的缺口，已在 story 文件中修正：

1. 它把 `$muted: #6272a4` 和 “所有语义色对比度 ≥ 4.5:1” 同时写成硬要求，但这组条件在当前公式下并不成立。
2. 它要求把 `DashboardScreen` 从 `Static` 改成 `Screen`，同时又继续让 `ATOApp.compose()` 直接挂载 `DashboardScreen()`，这与 Textual 的 screen API 语义冲突。
3. 它把响应式布局职责拆成了两份，一部分放到 `ATOApp.compose()`，另一部分又要求 `DashboardScreen.compose()` 重建相同结构，开发时容易做成双份布局。
4. 它给出的 Textual pilot 示例是 `app.run(size=(...))`，但响应式测试真正应该走的是 `run_test(size=(...))`。
5. 它定义了 `running/active/awaiting/...` 这套视觉状态，却没有明确说明当前仓库实际持久化的是 `StoryStatus` / `ApprovalStatus` / `TaskStatus`，实现者很容易把展示语义误当成数据库状态值。

## 已核查证据

- 本地仓库工件：
  - `_bmad-output/planning-artifacts/epics.md`
  - `_bmad-output/planning-artifacts/architecture.md`
  - `_bmad-output/planning-artifacts/ux-design-specification.md`
  - `_bmad-output/implementation-artifacts/6-1a-tui-launch-sqlite-connection.md`
  - `src/ato/tui/app.py`
  - `src/ato/tui/dashboard.py`
  - `src/ato/tui/app.tcss`
  - `src/ato/models/schemas.py`
  - `tests/integration/test_tui_pilot.py`
- 官方文档：
  - Textual Screens: <https://textual.textualize.io/guide/screens/>
  - Textual Testing: <https://textual.textualize.io/guide/testing/>

## 发现的关键问题

### 1. `$muted` 色值与 WCAG AA 要求同时成立不了

原 story 把以下两件事都写成了硬约束：

- `$muted: #6272a4`
- 所有语义色在 `$background (#282a36)` 上对比度 ≥ 4.5:1

但按 story 自己提供的 WCAG 公式实测：

- `#6272a4` 对 `#282a36` 约为 `3.03:1`
- story 备注里给的 `#6878ad` 也只有约 `3.31:1`

这会让开发者陷入两难：要么照抄色值而让测试失败，要么偷偷改色值而偏离文档。

已应用修正：

- AC1 改为：`$muted` 保持 Dracula 语义，但最终取值以对比度测试通过为准
- 对比度表改成实测值，并明确 `#6272a4` 不达标
- 把示例可访问变体改为可通过边界值的 `#8390b7`
- Task 1.1 / 1.5 明确对比度测试是 `muted` 的最终裁决标准

### 2. `DashboardScreen` 升格为 `Screen` 与当前 App 结构冲突

原 story 同时要求：

- `DashboardScreen` 从 `Static` 改为 `Screen`
- `ATOApp.compose()` 继续渲染 `Header + DashboardScreen() + Footer`

这与 Textual 的 screen 模型不一致。官方 screen 文档明确说明：

- app 中 compose / mount 的普通 widget 会进入默认 screen
- `Screen` 需要通过 `push_screen()` / `install_screen()` 等 screen API 管理

如果照原稿实现，开发者会写出一个既不像 widget、也不像真正 screen stack 的中间态。

已应用修正：

- 4.1 改为把 `DashboardScreen` 升级为复合 widget 容器，而不是 Textual `Screen`
- 保持 `ATOApp.compose()` 骨架不变，只让 `DashboardScreen` 内部接管响应式布局
- 在 “不要重新实现” 中显式禁止 “把 `DashboardScreen` 升格为 `Screen` 后仍在 compose 里直接 yield”

### 3. 响应式布局职责重复，容易做成两套结构

原 story 的 Task 3 和 Task 4 同时声明：

- `ATOApp.compose()` 生成三面板 / Tab 布局
- `DashboardScreen.compose()` 再生成三面板 / Tab 布局

这会直接导致：

- 布局树 ownership 不清晰
- `watch_layout_mode()` 到底切 app 还是切 dashboard 不明确
- 后续 6.2b 接故事列表时，开发者不知道数据和布局应该落在哪一层

已应用修正：

- `ATOApp` 只负责 `layout_mode` reactive、`on_resize()`、以及把模式变化转发给 `DashboardScreen`
- `DashboardScreen` 独占 `ContentSwitcher` / three-panel / tabbed / degraded 三套内部布局
- 保留 `DashboardScreen.update_content()`，避免破坏 6.1a 已有 reactive 数据刷新路径

### 4. Textual pilot 测试 API 写错

原 story 的 Task 3.8 写的是：

- `app.run(size=(cols, rows))`

但官方测试文档明确：

- UI 交互测试应通过 `run_test()` 获取 `Pilot`
- 改变终端尺寸时使用 `run_test(size=(w, h))`

如果照原 story 实现，测试作者会落到真实运行 API，而不是 headless pilot 测试 API。

已应用修正：

- 把示例改为 `app.run_test(size=(cols, rows))`
- 增补 “响应式测试需覆盖 resize 后数据和焦点上下文不重置”

### 5. 展示状态与数据库状态没有解耦说明

原 story 让开发者创建：

- `running/active/awaiting/failed/done/frozen/info`

但当前仓库真实状态模型是：

- `StoryStatus = backlog/planning/ready/in_progress/review/uat/done/blocked`
- `ApprovalStatus = pending/approved/rejected`
- `TaskStatus = pending/running/paused/completed/failed`

如果没有明确映射层，开发者很容易：

- 直接把视觉状态写回 SQLite
- 在 widget 层散落 if/else，把领域状态和展示状态耦死
- 后续 6.2b / 6.3a 接入真实数据时重复返工

已应用修正：

- 新增“状态语义映射约束”章节
- Task 2.4 改为两层 helper：domain→visual 映射 + `format_status(visual_status)`
- Task 2.5 测试要求覆盖当前所有 `StoryStatus` / `ApprovalStatus` / `TaskStatus`

## 已应用增强

- 在 story references 中补入了 Textual 官方 `screens` / `testing` 文档
- 更新了 `DashboardScreen` 相关交付描述、项目结构说明和 previous story intelligence
- 在 Change Log 中记录了本次 validate-create-story 的具体修订点

## 剩余风险

- 上游 UX / Epic 文档仍保留了 Dracula `$muted: #6272a4` 与 WCAG 4.5:1 的源头冲突；当前 story 已做可实现化解，但后续如果回写 UX 基线文档，最好同步修正。
- 当前本机未安装 `textual` 依赖，无法在本地直接做 API 级 smoke import；不过这次变更仅修改 story 文档和验证报告，不涉及运行时代码。

## 最终结论

修正后，该 story 已从“方向正确但存在多个实现陷阱”变为“可以直接交给 dev-story 执行”的状态。高风险误导点已经移除：不会再把不达标的颜色当成硬约束，不会再诱导开发者把 `Screen` 当普通 widget 直接挂载，也不会再把视觉状态语义和数据库状态模型混在一起。
