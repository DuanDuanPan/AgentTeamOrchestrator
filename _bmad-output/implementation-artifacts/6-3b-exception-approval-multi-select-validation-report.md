# Story 验证报告：6.3b 异常审批与多选交互

验证时间：2026-03-27
Story 文件：`_bmad-output/implementation-artifacts/6-3b-exception-approval-multi-select.md`
验证模式：`validate-create-story`
结果：PASS（已应用修正）

## 摘要

该 story 的方向是对的，但原稿里有 4 个会直接误导开发实现的缺口，已在 story 文件中修正：

1. 它把 UX 草图里的 `critical_timeout` / `cascade_failure` 当成了当前仓库的真实 approval_type，同时还把 `needs_human_review` / `convergent_loop_escalation` 写成了错误的 decision 集，和 `schemas.py` / `core.py` 的真实合同冲突。
2. 它要求 ExceptionApprovalPanel 展示 `failed_test` / `blocked_count` / `worktree_path` 之类当前 payload 并不会稳定提供的字段，容易逼着开发者伪造上下文或引入错误的数据依赖。
3. 它直接要求在 `DashboardScreen.BINDINGS` 绑定 `1`-`9`，但当前 `ATOApp` 在 tabbed 模式已经把 plain `1`-`4` 绑定给切页；如果照原稿实现，会破坏响应式导航契约。
4. 它把 regression 决策链路指向了并不存在的 `handle_regression_decision()` / `transition_queue.py` approval consumer，真实消费逻辑其实在 `src/ato/core.py::_handle_approval_decision()`。

## 已核查证据

- 规划与规范工件：
  - `_bmad-output/planning-artifacts/epics.md`
  - `_bmad-output/planning-artifacts/prd.md`
  - `_bmad-output/planning-artifacts/architecture.md`
  - `_bmad-output/planning-artifacts/ux-design-specification.md`
- 前序 story：
  - `_bmad-output/implementation-artifacts/6-3a-standard-approval-interaction.md`
  - `_bmad-output/implementation-artifacts/4-4-notification-cli-quality.md`
  - `_bmad-output/implementation-artifacts/4-5-regression-test-merge-integration.md`
- 当前代码：
  - `src/ato/tui/app.py`
  - `src/ato/tui/dashboard.py`
  - `src/ato/tui/widgets/approval_card.py`
  - `src/ato/tui/app.tcss`
  - `src/ato/approval_helpers.py`
  - `src/ato/models/schemas.py`
  - `src/ato/models/db.py`
  - `src/ato/core.py`
  - `src/ato/merge_queue.py`
  - `src/ato/convergent_loop.py`
  - `src/ato/adapters/bmad_adapter.py`
  - `tests/integration/test_tui_responsive.py`
  - `tests/integration/test_tui_pilot.py`
  - `tests/unit/test_dashboard_approval.py`
  - `tests/unit/test_cli_notification.py`
  - `tests/unit/test_core.py`
  - `tests/unit/test_convergent_loop.py`

## 发现的关键问题

### 1. 类型列表和 decision 映射漂移到了 UX 草图，而不是当前 schema / consumer

原 story 把以下内容写成了实现合同：

- `critical_timeout`
- `cascade_failure`
- `needs_human_review -> review / skip / escalate`
- `convergent_loop_escalation -> manual_review / force_pass / abort_story`

但当前仓库的真实合同是：

- `src/ato/models/schemas.py` 中没有 `critical_timeout` / `cascade_failure`
- `APPROVAL_DEFAULT_VALID_OPTIONS["needs_human_review"] == ["retry", "skip", "escalate"]`
- `APPROVAL_DEFAULT_VALID_OPTIONS["convergent_loop_escalation"] == ["retry", "skip", "escalate"]`
- `src/ato/core.py::_handle_approval_decision()` 也只识别 `retry / skip / escalate`

如果按原稿实现，TUI 会把合法 decision 写错，approval 将无法被消费，或者永远停留在 unconsumed 状态。

已应用修正：

- AC1 / AC3 改成只覆盖当前真实多选异常审批类型
- `needs_human_review` / `convergent_loop_escalation` 的 options 对齐到 `retry / skip / escalate`
- Dev Notes 增加“当前真实多选异常审批合同”表，直接列出各类型的创建方和 payload 合同

### 2. 上下文字段要求脱离当前 payload 现实，容易逼着开发者“编数据”

原 story 的 AC5 / 示例代码里要求展示：

- `failed_test`
- `blocked_count`
- `worktree_path`
- `threshold`
- `agent 角色`
- `受影响 story 列表`

但当前真实 payload 里：

- `regression_failure` 常见 payload 只有 `options`，有些路径附带 `reason`
- `rebase_conflict` 有 `conflict_files` 和 `stderr`，没有稳定的 `worktree_path`
- `precommit_failure` 有 `error_output`
- `session_timeout` 有 `task_id` 和 `elapsed_seconds`
- `needs_human_review` / `convergent_loop_escalation` 的 payload 结构与原稿示例完全不同

如果照原稿实现，开发者只能：

- 伪造文本
- 额外查不存在的字段
- 或把 panel 逻辑写成一堆不可达分支

已应用修正：

- AC5 改成严格基于当前真实 payload 合同
- 明确缺失字段必须优雅降级，不得伪造 `failed_test` / `blocked_count` / `worktree_path`
- `d` 键的 richer context 改成优先展示真实 payload 中已有的 `stderr` / `error_output` / `raw_output_preview` / `round_summaries` / `unresolved_findings`

### 3. 数字键绑定方案会破坏当前 tabbed 模式 `[1]-[4]` 导航契约

原 story Task 2 写成：

- 在 `DashboardScreen.BINDINGS` 新增 `("1", "option_1")` 到 `("9", "option_9")`

但当前仓库里：

- `src/ato/tui/app.py` 已经把 plain `1`-`4` 绑定到 `switch_tab(1..4)`
- `tests/integration/test_tui_responsive.py` 明确断言 tabbed 模式下 `1` / `2` 切换 `[1]审批` / `[2]Stories`

也就是说，原稿会直接和已有响应式行为抢同一组按键。

已应用修正：

- AC2 / AC6 / Task 2 改成：three-panel 模式下支持 plain 数字键提交
- tabbed 模式明确保留现有 `[1]-[4]` 切页契约
- Task 4.3 / Task 10 增加 tabbed 模式边界测试，避免后续回归

### 4. regression 决策链路引用了错误的实现点

原 story 写的是：

- Story 4-5 的 `handle_regression_decision()`
- `transition_queue.py` 的 approval 消费路由

但当前真实链路是：

- `src/ato/merge_queue.py` 创建 `regression_failure` approval
- `src/ato/core.py::_handle_approval_decision()` 消费 `revert / fix_forward / pause`

如果开发者按原稿找实现点，会浪费时间在不存在的方法或错误的文件上，甚至把决策处理错误地塞进 transition queue。

已应用修正：

- Dev Notes 把 regression / timeout / crash recovery 的真实创建方和消费方写清楚
- References 中补入 `src/ato/core.py`、`src/ato/merge_queue.py`、`src/ato/convergent_loop.py`、`src/ato/adapters/bmad_adapter.py`

## 已应用增强

- 补回了 create-story 模板里的 validation note 注释
- 增加了 `Change Log`，记录本次 validate-create-story 的修订点
- 把 `APPROVAL_TYPE_ICONS` 的来源纠正为 `src/ato/models/schemas.py`
- 把 tabbed 模式的交付边界写清楚，避免 Story 6.3b 抢掉 6.2b 已交付的响应式导航契约

## 剩余风险

- 当前 story 明确把“直接数字键提交”收敛到 three-panel 模式；如果后续产品希望 tabbed 模式也支持直接多选决策，需要额外设计新的快捷键方案或 focus 模式，不能在实现阶段临时拍脑袋。
- 当前 `regression_failure` payload 仍然比较薄，只能展示“queue 已冻结 + reason(若有)”级别的信息；如果后续 Epic 4 想补入失败测试名、blocked merge 数等 richer context，需要先修改 payload producer，再回写 story。
- 本次工作只修订了 story 文档和验证报告，没有实现 UI 代码，也没有运行 Textual 测试；目标是先把实现契约收紧，避免开发时走错方向。

## 最终结论

修正后，该 story 已从“概念上合理但实现合同漂移严重”收敛成“可以直接交给 dev-story 执行”的状态。高风险误导点已经移除：不会再围绕不存在的 approval_type 写代码，不会再把 decision 写成 consumer 不识别的值，不会再为了展示 panel 去伪造 payload 字段，也不会再意外破坏 tabbed 模式的 `[1]-[4]` 导航。
