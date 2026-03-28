# Story 验证报告：8.2 新增 Planning 阶段 — 使用 Claude 规划并行 Story

验证时间：2026-03-28  
Story 文件：`_bmad-output/implementation-artifacts/8-2-add-planning-phase.md`  
验证模式：`validate-create-story`  
结果：PASS（已应用修正）

## 摘要

原始 8.2 草稿有明显方向，但它把这次改动写成了“状态机 + schema + `_poll_cycle` 的小改动”，这会直接把开发者带偏。当前仓库里 `planning` 已经是高层状态、batch 头部 story 已经用 `status="planning"`、replay / recovery 也都围绕 `creating` 作为首个真实 phase 建立了大量合同。

本次验证后，story 已被收敛成一个真正可执行的 phase-insertion 合同，关键修正有 5 项：

1. 明确 `StoryStatus` 已经包含 `planning`，不再要求去 `schemas.py` 发明新的高层状态改动。
2. 把“重命名 `start_create` 为 `start_plan`”改成更稳妥的方案：保留 `start_create`，新增 `plan_done`，避免 repo-wide 事件名雪崩。
3. 补上 `batch.py`、`transition_queue.py`、`recovery.py` 这些真正会因首阶段变化而失效的运行时路径。
4. 移除把 `core.py::_poll_cycle()` 当成 planning 成功事件主落点的误导；该映射只服务 interactive phases，planning 应走 structured-job success path。
5. 增加 Scope Boundary，防止开发者顺手去重写 `LocalBatchRecommender`、新增 planner DB schema，或把 story 变成另一个 batch-planning 系统。

## 已核查证据

- 规划与规范工件：
  - `_bmad-output/planning-artifacts/prd.md`
  - `_bmad-output/planning-artifacts/research/technical-claude-codex-cli-integration-research-2026-03-24.md`
- 前序相关 story：
  - `_bmad-output/implementation-artifacts/1-5-ato-plan-phase-preview.md`
  - `_bmad-output/implementation-artifacts/2b-5-batch-select-status.md`
- 当前代码：
  - `src/ato/state_machine.py`
  - `src/ato/transition_queue.py`
  - `src/ato/recovery.py`
  - `src/ato/batch.py`
  - `src/ato/cli.py`
  - `src/ato/models/schemas.py`
  - `ato.yaml.example`
  - `tests/unit/test_state_machine.py`
  - `tests/unit/test_transition_queue.py`
  - `tests/unit/test_batch.py`
  - `tests/unit/test_recovery.py`
  - `tests/unit/test_cli_plan.py`

## 发现的关键问题

### 1. 原稿把已经存在的 `planning` 高层状态当成未实现能力

当前仓库事实是：

- `src/ato/models/schemas.py` 的 `StoryStatus` 已包含 `"planning"`
- `src/ato/state_machine.py` 目前把 `creating` / `validating` 映射到高层 `"planning"`
- batch 头部 story 也已经使用 `status="planning"`

原稿仍把“扩展 `StoryStatus`”写成待办，容易让开发者误以为要继续修改 schema / theme / DB validator。

已应用修正：

- 把 story 收紧为“新增真实 phase”，不是“新增高层 status”
- 明确 `StoryStatus` 不需要扩容，只需更新 canonical phase 与映射

### 2. 原稿要求重命名 `start_create`，会制造不必要的大面积 churn

当前仓库里 `start_create` 已被这些路径广泛依赖：

- `tests/unit/test_state_machine.py`
- `tests/unit/test_transition_queue.py`
- `tests/integration/test_transition_queue.py`
- `tests/integration/test_state_persistence.py`
- recovery / crash recovery fixture

如果直接改成 `start_plan`，开发者会被迫重写大量事件名断言与 replay 表，而真正新增的业务语义只有“在 `creating` 前插入一个 phase”。

已应用修正：

- 保留 `start_create = queued -> planning`
- 新增 `plan_done = planning -> creating`
- `create_done` 继续表示 `creating -> validating`

### 3. 原稿严重低估了 batch / replay / recovery 的真实影响面

当前真实代码中：

- `src/ato/batch.py::confirm_batch()` 把头部 story 直接写成 `current_phase="creating"`
- `src/ato/transition_queue.py` 的 `_HP_EVENTS` / `_HP_PHASES` 假定 `creating` 是首个 canonical phase
- `src/ato/recovery.py::_PHASE_SUCCESS_EVENT` 没有 `planning -> plan_done`

如果只按原稿改 `state_machine.py`，系统会在 batch 初始写入、phase replay、recovery success event 三处出现错位。

已应用修正：

- Story Task 3 明确纳入 `batch.py`、`transition_queue.py`、`recovery.py`
- 测试面扩展到 batch / replay / recovery 相关单元与集成测试

### 4. 原稿把 `_poll_cycle()` 当成 planning success event 的主落点，这是错位的

原稿要求：

- 在 `src/ato/core.py::_poll_cycle()` 里增加 `"planning": "plan_done"`

但当前代码事实是：

- `_poll_cycle()` 里的 `phase_success_event` 仅用于 `interactive_session`
- 现有 map 只有 `developing -> dev_done`、`uat -> uat_pass`
- `planning` 是 `structured_job`，其成功转换在 recovery / restart 路径上依赖 `_PHASE_SUCCESS_EVENT`

如果开发者按原稿走，会改错文件而遗漏真正需要的 success map。

已应用修正：

- Story 中移除 `_poll_cycle()` 作为主要落点
- 把 success-event 对齐点放回 `src/ato/recovery.py`

### 5. 原稿没有划清与现有 batch 推荐机制的边界

标题里写“使用 Claude 规划并行 Story”，但原稿没有说明：

- 是否替换 `LocalBatchRecommender`
- 是否新增 planner output schema
- 是否需要新的 approval / DB 表

缺少边界时，开发者很容易顺手把 8.2 扩成“另一个 batch planner 系统”，直接突破 story 体量。

已应用修正：

- 新增 AC5 与 Scope Boundary
- 明确本 story 不替换 `ato batch select` / `recommend_batch()`
- 明确不新增 planner 专用 DB schema / approval type

## 已应用增强

- 补回了 create-story 模板里的 validation note 注释
- 增加了 `Previous Story Intelligence`，明确引用 Story 1.5 / 2B.5 / Epic 8 共享模板变更
- 增加了 `Scope Boundary`，避免 planning phase 趁机扩成另一个 batch 引擎
- 增加了 `Suggested Verification`、`Change Log` 与 `Dev Agent Record`

## 剩余风险

- `_bmad-output/planning-artifacts/epics.md` 里当前没有正式的 Epic 8 / Story 8.2 章节；本次验证主要依赖当前 story 草稿、PRD、技术调研、前序 implementation artifacts 与现有代码交叉还原意图。
- Story 1.5 与 2B.5 的既有文档仍展示旧 phase 序列 / 旧 batch 头部 phase；后续若团队把这些文档继续当作唯一真源，仍建议补做一次上游文档同步。
- 本次只修订 story 与 validation report，没有实现 Python 代码，也没有运行测试；目标是先把 dev-story 的实现合同收紧到与当前仓库一致。

## 最终结论

修正后，8.2 已从“思路对，但改动面和落点都写偏了”的草稿，收敛为一个可以直接交给 dev-story 执行的 story。高风险误导点已经移除：不会再去重复修改 `StoryStatus`，不会再无谓重命名 `start_create`，也不会再漏掉 batch/replay/recovery 这些真正会因首 phase 改动而失效的运行时路径。
