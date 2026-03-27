# Story 验证报告：3.3 收敛信任与 Escalation 通知

验证时间：2026-03-27
Story 文件：`_bmad-output/implementation-artifacts/3-3-convergence-trust-escalation.md`
验证模式：`validate-create-story`
结果：PASS（已应用修正）

## 摘要

该 story 的目标方向是对的，但原稿里有 5 个会直接把 dev 带偏的合同问题，已在 story 文件中修正：

1. 它要求从当前 SQLite findings 模型里还原“每个 finding 的完整逐轮轨迹”，但现有 schema 只有 `first_seen_round + current_status`，做不到精确恢复关闭轮次。
2. 它让 `_calculate_convergence_rate()` 自己再开 DB 连接，且示例没有强调必须在本轮 `still_open/closed/new` 写入之后计算，容易读到旧状态。
3. 它继续按 3.2d 时代的手写 `_create_escalation_approval()` 思路扩展 approval，却没有对齐 4.1 已落地的 `approval_helpers.create_approval()` 合同。
4. 它给非法 transition 测试写了不存在的状态机事件和旧阶段名（`start_plan` / `plan_done` / `PHASE_DEFINITIONS`），开发者照着写会直接撞到当前 `StoryLifecycle` API。
5. 它一边说要在 approval 里带“每轮 diff 历史”，一边又打算从 DB 逆向猜这些摘要；同时 5-finding 场景还把“5 个 blocking”与后文的“4 blocking + 1 suggestion”写冲突了。

这些问题如果不修，最常见的后果是：开发者会实现一个无法满足的 trajectory API、在错误时机计算收敛率、绕过统一 approval 辅助层、复制错误的状态机测试脚本，并且把 escalation payload 做成既不准确也不稳定的“伪历史”。

## 已核查证据

- 规划与 story 工件：
  - `_bmad-output/planning-artifacts/epics.md`
  - `_bmad-output/planning-artifacts/prd.md`
  - `_bmad-output/planning-artifacts/architecture.md`
  - `_bmad-output/planning-artifacts/ux-design-specification.md`
  - `_bmad-output/implementation-artifacts/3-1-deterministic-validation-finding-tracking.md`
  - `_bmad-output/implementation-artifacts/3-2c-re-review-scope-narrowing.md`
  - `_bmad-output/implementation-artifacts/3-2d-convergence-termination.md`
  - `_bmad-output/implementation-artifacts/4-1-approval-queue-nudge.md`
- 当前代码基线：
  - `src/ato/convergent_loop.py`
  - `src/ato/approval_helpers.py`
  - `src/ato/models/db.py`
  - `src/ato/models/schemas.py`
  - `src/ato/state_machine.py`
  - `tests/unit/test_convergent_loop.py`
  - `tests/unit/test_state_machine.py`

## 发现的关键问题

### 1. “完整 per-finding 逐轮轨迹”与当前 findings schema 不相容

原稿 Task 3 同时出现了三套互相打架的说法：

- 要返回每个 finding 的完整 `trajectory`
- 承认 `round_num` 只表示首次发现轮次、`status` 是原地更新
- 又把“只返回 `first_seen_round + current_status`”放成推荐简化方案

在当前模型下，`first_seen_round + current_status` 是真实能力边界；“精确在哪一轮 closed”并没有持久化。继续把完整 trajectory 写成必须交付，只会逼 dev 伪造数据。

已应用修正：

- AC3 改成“Finding 跨轮次状态摘要查询”
- Task 3 收敛为 `get_finding_trajectory()` 返回 `first_seen_round + current_status`
- 明确写出：不要从当前 SQLite schema 伪造“精确关闭轮次”

### 2. 收敛率计算的时序没写清，容易读到旧状态

原稿示例把 `_calculate_convergence_rate(story_id)` 写成独立开连接、再查 DB 的 helper，却没有强调它必须发生在本轮 `still_open/closed/new` 已写入之后。如果 dev 把这个 helper 放到写入前，或者 helper 自己开第二个连接去查，就会计算出错误的收敛率。

已应用修正：

- 把 helper 收敛为纯函数：`_calculate_convergence_rate(findings: Sequence[FindingRecord]) -> float`
- 明确要求在 `run_rereview()` 持久化本轮结果后，用同一轮更新后的 snapshot 计算收敛率
- structlog `convergent_loop_round_complete` 的 `convergence_rate` 也跟着对齐到这个时序

### 3. escalation approval 创建没有对齐 4.1 的统一 helper

Story 4.1 已经引入 `src/ato/approval_helpers.py:create_approval()` 作为统一创建入口，带上了推荐动作、nudge、bell 等审批合同。原稿却仍然围绕手写 `ApprovalRecord` / `insert_approval()` 展开，会让 3.3 再次分叉出一套旧路径。

已应用修正：

- Task 2 明确要求 `_create_escalation_approval()` 复用 `create_approval()`
- 保留现有 pending 幂等检查，但真正插入 approval 的路径统一走 helper
- payload 里补上 `options=["retry","skip","escalate"]`，与当前 CLI approval consumer 合同一致

### 4. 非法 transition 示例使用了不存在的旧状态机 API

原稿示例写的是：

- `start_plan`
- `plan_done`
- `PHASE_DEFINITIONS`

但当前 `StoryLifecycle` 真实事件是：

- `start_create`
- `create_done`
- `validate_pass`
- `start_dev`
- `dev_done`

而且仓库里已经有 `tests/unit/test_state_machine.py::test_fixing_rejects_review_pass` 这种现成覆盖。原稿不仅示例错，还会诱导开发者在 `test_convergent_loop.py` 复制一整套重复状态机路径。

已应用修正：

- 示例改成 `StoryLifecycle.create()` + `start_create/create_done/...`
- 明确禁止引用不存在的旧事件名
- Task 5 改成优先复用 / 扩展 `tests/unit/test_state_machine.py`

### 5. escalation “历史”应该来自 runtime round summaries，不该从 DB 反推

原稿想通过 `_build_finding_history_payload()` 查询全部 findings 再按 `round_num` 分组，拼出每轮 diff。这在当前模型下是不准确的，因为 `round_num` 是首次发现轮次，不是每轮状态快照。真正准确的每轮统计，已经存在于 `ConvergentLoopResult` 的 runtime 结果里。

已应用修正：

- Task 2 改成让 `run_loop()` 累积 `round_summaries`
- escalation payload 改为：
  - `final_convergence_rate`
  - `round_summaries`
  - `unresolved_findings`
  - `options`
- 5-finding 例子统一为“4 blocking + 1 suggestion”，不再自相矛盾

## 已应用增强

- 在 Dev Notes 里显式写出当前 schema 的能力边界，防止后续再有人把 FR14 误读成“必须有精确关闭轮次”
- 把 `unresolved_findings` 明确限制为 `get_open_findings()` 当前快照，避免把已关闭 finding 也塞进人工审批上下文
- 给 story 增加了 `Change Log`，记录本次 validate-create-story 的修订点

## 剩余风险

- 当前 story 仍然没有为“精确逐轮 per-finding 历史”引入新的事件存储。如果以后 TUI 真的要逐 finding 回放每一轮状态变化，需要单独的新 story 扩展 schema 或日志持久化，而不是继续在 3.3 范围里硬挤。
- `ato approvals` 目前的普通表格摘要不会把 `round_summaries` 全展开；本次修订的重点是把 payload 做成后续 CLI/TUI consumer 能可靠消费的结构，而不是提前实现新的审批详情 UI。

## 最终结论

修正后，Story 3.3 已达到 `ready-for-dev` 的质量门槛。当前版本已经和 3.1/3.2c/3.2d 的 findings 持久化合同、4.1 的 approval helper 路径、当前 `StoryLifecycle` 事件名，以及现有测试布局对齐，不会再把 dev 引向“伪造历史”“错时机算收敛率”或“照着旧状态机脚本写测试”的错误实现。
