# Story 验证报告：6.4 Story 详情与渐进钻入导航

验证时间：2026-03-28
Story 文件：`_bmad-output/implementation-artifacts/6-4-story-detail-drill-navigation.md`
验证模式：`validate-create-story`
结果：PASS（已应用修正）

## 摘要

该 story 的方向是对的，但原稿里有 5 个会直接误导开发实现的缺口，已在 story 文件中修正：

1. 它把状态流写成 `queued → creating → reviewing → fixing → re_reviewing → uat_waiting → merging → done`，和当前仓库的真实 `CANONICAL_PHASES` 冲突，还发明了不存在的 phase 名。
2. 它把 `tasks.expected_artifact` 当成“文件变更列表”数据源，但当前仓库里这个字段经常只是控制标记；真正稳定的产物展示合同其实是 Story 5.2 的 `context_briefing.artifacts_produced` 优先、`expected_artifact` fallback。
3. 它把成本 / 历史子视图写成基于 `agent_role`、`task_type`、`agent` 之类当前 schema 根本不存在的字段，和 `TaskRecord` / `CostLogRecord` 的真实列名脱节。
4. 它把 detail 数据加载写成 “`ATOApp._load_data()` 或按需查询都可以”，还把 tabbed 模式写成 “push Screen 或替换 TabbedContent 都行”，这会同时引入性能回归和导航契约漂移。
5. 它给 ConvergentLoopProgress 提供了按原始 findings 行数直接累计的 SQL，会把同一 finding 在多轮里的历史重复累计成“当前 open/closed 数”，与已有 `get_story_findings_summary()` 的去重语义冲突。

## 已核查证据

- 规划与规范工件：
  - `_bmad-output/planning-artifacts/epics.md`
  - `_bmad-output/planning-artifacts/prd.md`
  - `_bmad-output/planning-artifacts/architecture.md`
  - `_bmad-output/planning-artifacts/ux-design-specification.md`
- 前序 story：
  - `_bmad-output/implementation-artifacts/6-2b-dashboard-story-list.md`
  - `_bmad-output/implementation-artifacts/6-3a-standard-approval-interaction.md`
  - `_bmad-output/implementation-artifacts/6-3b-exception-approval-multi-select.md`
  - `_bmad-output/implementation-artifacts/5-2-recovery-summary-execution-history.md`
- 当前代码：
  - `src/ato/state_machine.py`
  - `src/ato/models/schemas.py`
  - `src/ato/models/db.py`
  - `src/ato/tui/app.py`
  - `src/ato/tui/dashboard.py`
  - `src/ato/tui/story_detail.py`
  - `src/ato/cli.py`
  - `tests/integration/test_tui_responsive.py`
  - `tests/integration/test_tui_exception_approval.py`
  - `tests/unit/test_cli_history.py`
  - `tests/unit/test_db.py`

## 发现的关键问题

### 1. 状态流顺序漂移到了不存在的 phase 名

原 story 在 Dev Notes 里把状态流写成：

- `queued`
- `creating`
- `reviewing`
- `fixing`
- `re_reviewing`
- `uat_waiting`
- `merging`
- `done`

但当前仓库 `src/ato/state_machine.py` 的真实契约是：

- `queued`
- `creating`
- `validating`
- `dev_ready`
- `developing`
- `reviewing`
- `fixing`
- `qa_testing`
- `uat`
- `merging`
- `regression`
- `done`

如果按原稿实现，开发者会：

- 在 TUI 中发明不存在的 phase 名
- 漏掉真实的 `validating` / `dev_ready` / `developing` / `qa_testing` / `regression`
- 让 Story 详情和现有状态机、CLI、SQLite 持久化全部脱节

已应用修正：

- AC2 / Task 2.2 / Dev Notes 统一改成 `["queued", *CANONICAL_PHASES, "done"]`
- 明确禁止发明 `re_reviewing` / `uat_waiting` / `uat_running` 等假 phase

### 2. “文件变更列表”数据源写错了，容易逼着开发者造假

原 story 的 AC2 / Task 2.4 / Task 6.2 都把：

- `tasks.expected_artifact`

当成了“文件变更列表”的直接来源。

但当前仓库里：

- `expected_artifact` 经常只是控制标记或流程产物名
- Story 5.2 的 `ato history` 已明确建立展示合同：
  - 优先读取 `context_briefing.artifacts_produced`
  - 缺失时 fallback 到 `expected_artifact`

如果按原稿实现，开发者很容易：

- 把 `transition_submitted`、`restart_requested`、`regression_test` 这类控制值当成“文件变更”
- 或为了满足 story 文案，凭空拼出不存在的 git diff / changed-files 列表

已应用修正：

- AC2 / Task 2.4 / Task 6.2 改成“执行产物 / 相关文件”合同
- 明确 `context_briefing.artifacts_produced` 优先，`expected_artifact` 只做 fallback
- 明确当前 schema **没有**稳定的 per-story changed-files 数据源，禁止承诺真实 git diff

### 3. 成本 / 历史字段名和真实 schema 不一致

原 story 在 AC3 / SQL 示例里写了：

- `agent_role`
- `task_type`
- `agent`

但当前真实模型是：

- `CostLogRecord`: `phase`, `role`, `cli_tool`, `model`, `input_tokens`, `output_tokens`, `cache_read_input_tokens`, `cost_usd`
- `TaskRecord`: `phase`, `role`, `cli_tool`, `status`, `started_at`, `completed_at`, `duration_ms`, `context_briefing`, `expected_artifact`

也就是说，原稿要求开发者查询的几列并不存在。

已应用修正：

- AC2 / AC3 / Task 2.5 / Task 2.6 改成真实字段名
- Dev Notes 增加 `TaskRecord` / `CostLogRecord` 字段合同
- 明确不要查询不存在的 `agent_role` / `task_type` / `agent`

### 4. Detail 加载和 tabbed 导航方案过于模糊，会引入性能与键位回归

原 story 同时给出：

- “在 `ATOApp._load_data()` 或按需查询中增加 story detail 数据”
- “tabbed 模式：push StoryDetailView 作为覆盖视图或切换 TabbedContent”

这两个“二选一随便做”的写法都很危险：

- `_load_data()` 当前是 2s 一次的轻量轮询；如果把全量 tasks / cost_log / findings detail 都塞进去，会直接抬高常驻查询成本
- 当前 `ATOApp.action_switch_tab()` 已把 App 级 `[1]-[4]` 绑定给 tabbed 模式切页；如果随手 push 新 Screen 或替换整个 `TabbedContent`，很容易破坏现有数字键契约和响应式测试

已应用修正：

- Task 6.1 改成：保持 2s 轮询轻量，detail 数据必须**按需**查询
- Task 4.3 改成：tabbed 模式只在 `tab-stories` 内部做 list/detail 切换
- Task 4.4 明确保留 App 级 `[1]-[4]` 数字键切页
- Dev Notes 收敛到“局部 ContentSwitcher，不使用 Screen push”

### 5. ConvergentLoopProgress 的统计口径会重复累计跨轮历史

原 story 在 CL 组件数据源里给的是原始 SQL：

- `SELECT severity, status, count(*) FROM findings WHERE story_id=? GROUP BY severity, status`
- `closed_findings / total_findings`

但当前仓库已经在 `src/ato/models/db.py` 里有 `get_story_findings_summary()`，其关键语义是：

- 先按 `story_id + severity + dedup_hash` 找最新 round
- 仅统计该 hash 最新轮次上的记录
- `still_open` 视作 `open`

如果回退到原稿的 raw row aggregation，CL 详情会把同一 finding 在多轮里的历史都算进“当前状态”，导致 open/closed 数、收敛率、blocking 数全部被抬高。

已应用修正：

- AC4 / Task 3.2 / Task 6.4 / Dev Notes 统一改成：当前摘要与收敛率复用去重语义
- 仅在需要 round 维度展示时，才单独基于 `get_findings_by_story()` 做逐轮分组

## 已应用增强

- 补回了 create-story 模板中的 validation note 注释
- 增加了 `Change Log`，记录本次 validate-create-story 的修订点
- 去掉了易漂移的源码行号引用，改成 symbol / 文件级引用
- 把 `tabbed` 模式的实现边界写实：在 `tab-stories` 内切换详情，而不是替换整个 App 结构

## 剩余风险

- 当前 story 仍然把 `l` 维持为 placeholder；如果后续产品真的想支持“打开实时 agent 日志”，需要先定义稳定的 log path / session contract，再新增 story，而不是在实现阶段自行脑补。
- 当前 story 仍然允许在 detail 子视图中选择 `DataTable` / `Tree` / `VerticalScroll` 等不同控件；实现时仍需谨慎处理 focus 和键位传播，但关键边界已经写清楚，不再是“完全自由发挥”。
- 本次工作只修订了 story 文档和验证报告，没有实现 TUI 代码，也没有运行测试；目标是先把 dev-story 的实现合同收紧到和当前代码一致。

## 最终结论

修正后，该 story 已从“方向正确但实现合同漂移明显”收敛成“可以直接交给 dev-story 执行”的状态。高风险误导点已经移除：不会再围绕错误 phase 名写状态流，不会再把 `expected_artifact` 当真实 changed-files 列表，不会再查询不存在的 schema 字段，也不会再为了详情页去破坏当前 tabbed 模式的 `[1]-[4]` 契约。
