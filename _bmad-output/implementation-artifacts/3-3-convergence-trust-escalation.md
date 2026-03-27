# Story 3.3: 收敛信任与 Escalation 通知

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a 操作者,
I want 在 Convergent Loop 结束时看到确定性的收敛/escalation 判定，未收敛时收到通知并做出决策,
So that 可以信任自动化质量结果，不会被无限循环阻塞。

## Acceptance Criteria

1. **AC1: 收敛率计算与阈值判定**
   ```
   Given 一轮 re-review 完成后
   When 计算收敛率（closed_findings / total_findings）
   Then 收敛率 ≥ convergence_threshold 且无 open blocking → 判定为收敛
   And 操作者可在 ato status 或 TUI 中看到 story 自动进入下一阶段
   ```

2. **AC2: Escalation Approval 带可消费的轮次摘要与未收敛 Finding 快照**
   ```
   Given Convergent Loop 达到 max_rounds 仍未收敛
   When 执行 escalation（FR17）
   Then 写入 approval 记录（类型 convergent_loop_escalation），通知操作者人工介入
   And payload 包含 `final_convergence_rate`、`round_summaries`、`unresolved_findings` 与决策 `options`
   ```

3. **AC3: Finding 跨轮次状态摘要查询**
   ```
   Given 操作者想追踪某个 finding 的跨轮次变化（FR14）
   When 查询某个 story 的 findings
   Then 可看到每个 finding 的 `first_seen_round` 与 `current_status`
   And 不要求从当前 SQLite schema 伪造“每个 finding 的精确关闭轮次”
   ```

4. **AC4: 端到端集成测试**
   ```
   Given Convergent Loop 集成测试
   When 构造已知 5-finding review 场景
   Then ≤3 轮内闭合所有 blocking findings（端到端测试）
   ```

5. **AC5: 非法 Transition 拒绝测试**
   ```
   Given 非法 transition 测试
   When 尝试在 Convergent Loop 中跳过 fix 直接进入 re-review
   Then 状态机拒绝，状态不变，structlog 记录
   ```

## Tasks / Subtasks

- [x] Task 1: 实现收敛率计算与阈值判定逻辑 (AC: #1)
  - [x] 1.1 在 `src/ato/convergent_loop.py` 中新增 `_calculate_convergence_rate(findings: Sequence[FindingRecord]) -> float` 纯 helper，计算 `closed_findings / total_findings`
  - [x] 1.2 修改 `run_rereview()`：在持久化 `still_open / closed / new` 结果后，基于同一轮已更新的 findings snapshot 计算收敛率，避免第二个 DB 连接读到旧状态
  - [x] 1.3 收敛率判定规则：`convergence_rate >= self._config.convergence_threshold AND 无 open blocking findings` → converged=True
  - [x] 1.4 structlog 记录收敛率：在 `convergent_loop_round_complete` 日志中新增 `convergence_rate` 字段
  - [x] 1.5 `run_loop()` 不重新实现收敛判定，但允许它累积每轮摘要供 escalation payload 使用

- [x] Task 2: 增强 Escalation Approval 的 Finding 变化历史 (AC: #2)
  - [x] 2.1 在 `run_loop()` 中收集首轮 review 与每轮 re-review 的 `round_summaries`（直接使用 `ConvergentLoopResult` 字段，不从 DB 逆向猜测）
  - [x] 2.2 新增 `_build_escalation_payload()` helper：组装 `final_convergence_rate`、`round_summaries`、`unresolved_findings`
  - [x] 2.3 修改 `_create_escalation_approval()`：保留现有 pending 幂等检查，但插入 approval 时复用 `src/ato/approval_helpers.py:create_approval()`
  - [x] 2.4 payload 需包含 `options=["retry", "skip", "escalate"]`，供 CLI/TUI approval consumer 直接消费

- [x] Task 3: 实现 Finding 跨轮次状态摘要查询 (AC: #3)
  - [x] 3.1 在 `src/ato/models/db.py` 中新增 `get_finding_trajectory(db, story_id) -> list[dict[str, Any]]`：查询某 story 的所有 findings，返回每个 finding 的状态摘要
  - [x] 3.2 返回结构：`[{"finding_id": "...", "file_path": "...", "rule_id": "...", "severity": "...", "description": "...", "first_seen_round": 1, "current_status": "closed"}]`
  - [x] 3.3 直接复用 `get_findings_by_story()` 结果；不要对中间轮次做插值，也不要伪造“精确关闭于第几轮”
  - [x] 3.4 escalation payload 中的 `unresolved_findings` 只使用 `get_open_findings()` 当前快照，避免把已关闭 finding 再塞进人工决策摘要

- [x] Task 4: 端到端集成测试 — 5-finding 场景 (AC: #4)
  - [x] 4.1 创建集成测试：`test_integration_five_finding_convergence`
  - [x] 4.2 场景设计：4 个 blocking + 1 个 suggestion → 第 1 轮 review 发现全部 5 个 → fix 修复 3 个 blocking → 第 2 轮 re-review 闭合 3 个、1 个 blocking still_open → fix 修复剩余 1 个 blocking → 第 3 轮 re-review 全部闭合 → 收敛
  - [x] 4.3 验证：`run_loop()` 在 ≤3 轮内收敛，`result.converged == True`
  - [x] 4.4 验证所有 5 个 finding 最终 status=closed
  - [x] 4.5 验证 escalation approval 未创建（因为收敛了）
  - [x] 4.6 Mock 策略：mock `dispatch_with_retry` 和 `bmad_adapter.parse` 返回不同轮次的预设结果（模拟 reviewer 逐步确认 fix）

- [x] Task 5: 非法 Transition 拒绝测试 (AC: #5)
  - [x] 5.1 优先复用 / 扩展 `tests/unit/test_state_machine.py` 中现有非法 transition 覆盖；不要在 `tests/unit/test_convergent_loop.py` 复制一整套状态机 happy path
  - [x] 5.2 场景：`start_create` → `create_done` → `validate_pass` → `start_dev` → `dev_done` → `review_fail` → story 进入 `fixing` → **尝试跳过 fix_done 直接提交 review_pass**
  - [x] 5.3 验证：状态机拒绝非法 transition，story 状态不变（仍为 fixing）
  - [x] 5.4 验证：structlog 记录非法 transition 尝试

- [x] Task 6: 收敛率相关单元测试 (AC: #1)
  - [x] 6.1 `test_convergence_rate_calculation` — 验证 `_calculate_convergence_rate(findings)` 正确计算 `closed / total`
  - [x] 6.2 `test_convergence_rate_threshold_met` — 收敛率 ≥ threshold 且无 blocking → converged
  - [x] 6.3 `test_convergence_rate_threshold_not_met` — 收敛率 < threshold 即使无 blocking → 不收敛
  - [x] 6.4 `test_convergence_rate_with_open_blocking` — 收敛率 ≥ threshold 但有 open blocking → 不收敛
  - [x] 6.5 `test_convergence_rate_zero_findings` — 0 findings → 收敛率视为 1.0
  - [x] 6.6 `test_convergence_rate_logged_in_round_complete` — structlog 中含 convergence_rate 字段

- [x] Task 7: Escalation 摘要 / Finding 轨迹相关测试 (AC: #2, #3)
  - [x] 7.1 `test_escalation_payload_contains_round_summaries` — escalation approval payload 含 `round_summaries`
  - [x] 7.2 `test_escalation_payload_contains_unresolved_findings` — escalation approval payload 含 `unresolved_findings` 与 `options`
  - [x] 7.3 `test_get_finding_trajectory_returns_first_seen_and_current_status` — 查询结果返回 `first_seen_round` / `current_status`
  - [x] 7.4 `test_get_finding_trajectory_empty_story` — 无 findings 时返回空列表

## Dev Notes

### 核心实现：收敛率计算

**收敛率定义（来自 AC1 和 PRD 配置）：**
- `convergence_rate = closed_findings / total_findings`
- `total_findings` = 该 story 历史上所有独立 finding 的总数（去重后）
- `closed_findings` = 当前 `status="closed"` 的 finding 数量
- 当 `total_findings == 0` 时，收敛率视为 `1.0`（无 finding = 自然收敛）

**收敛判定双重条件：**
```python
converged = (
    convergence_rate >= self._config.convergence_threshold  # 默认 0.5
    and not has_blocking_open  # 无 open/still_open blocking
)
```

**关键约束：收敛率阈值不影响首轮判定。** Story 3.2a 的 `run_first_review()` 首轮收敛逻辑保持不变（0 blocking → converged），因为首轮无 closed findings，收敛率计算无意义。收敛率阈值仅在 `run_rereview()` 中生效。

**`convergence_threshold` 配置参数：**
- 定义在 `src/ato/config.py:65-72` 的 `ConvergentLoopConfig`
- 默认值 `0.5`
- 验证范围 `[0, 1]`（`src/ato/config.py:289-290`）
- 来自 `ato.yaml` 的 `convergent_loop.convergence_threshold`

**收敛率计算 helper：**
```python
def _calculate_convergence_rate(self, findings: Sequence[FindingRecord]) -> float:
    """基于当前已持久化的 findings snapshot 计算 closed / total。"""
    if not findings:
        return 1.0
    closed = sum(1 for f in findings if f.status == "closed")
    return closed / len(findings)
```

**为什么用纯 helper 而不是在 helper 里再开一个 DB 连接：**
- `run_rereview()` 需要先把 `still_open / closed / new` 写入当前轮次结果，再计算收敛率
- 如果 helper 自己重新打开第二个连接，开发者很容易在错误时机读到“更新前”的旧状态
- 纯 helper 可复用在 `run_rereview()` 和 escalation payload 构建里，测试也更直接

### 修改 `run_rereview()` 的收敛评估

**当前逻辑（Story 3.2c 实现，`src/ato/convergent_loop.py:949-957`）：**
```python
has_blocking_still_open = any(
    f.severity == "blocking"
    for f in previous_findings
    if f.finding_id in match_result.still_open_ids
)
has_blocking_new = any(f.severity == "blocking" for f in match_result.new_findings)
converged = not has_blocking_still_open and not has_blocking_new
```

**修改为（增加收敛率阈值检查，且在本轮写入后计算）：**
```python
db = await get_connection(self._db_path)
try:
    ...
    for fid in match_result.still_open_ids:
        await update_finding_status(db, fid, "still_open")
    for fid in match_result.closed_ids:
        await update_finding_status(db, fid, "closed")
    if match_result.new_findings:
        await insert_findings_batch(db, match_result.new_findings)

    all_findings = await get_findings_by_story(db, story_id)
    convergence_rate = self._calculate_convergence_rate(all_findings)

    await maybe_create_blocking_abnormal_approval(...)
finally:
    await db.close()

has_blocking_still_open = any(...)
has_blocking_new = any(...)
no_open_blocking = not has_blocking_still_open and not has_blocking_new
converged = no_open_blocking and convergence_rate >= self._config.convergence_threshold
```

**structlog 增强——在 `convergent_loop_round_complete` 中追加 `convergence_rate`：**
```python
logger.info(
    "convergent_loop_round_complete",
    story_id=story_id,
    round_num=round_num,
    findings_total=findings_total,
    open_count=current_open_count,
    closed_count=len(match_result.closed_ids),
    new_count=len(match_result.new_findings),
    still_open_count=len(match_result.still_open_ids),
    blocking_count=blocking_count,
    suggestion_count=suggestion_count,
    convergence_rate=convergence_rate,  # 新增 Story 3.3
)
```

### Escalation Approval 增强：轮次摘要 + 未收敛 Finding 快照

**当前代码基线：**
- Story 3.2d 的 `_create_escalation_approval()` 已有 pending 幂等检查
- Story 4.1 已引入 `src/ato/approval_helpers.py:create_approval()`，新的 approval 创建应复用该 helper，避免手写 `ApprovalRecord` / `insert_approval()` 而绕过推荐动作、bell、nudge 语义

**本 Story 推荐 payload：**
```python
payload_dict = {
    "rounds_completed": rounds_completed,
    "open_blocking_count": remaining_blocking,
    "final_convergence_rate": convergence_rate,
    "round_summaries": round_summaries,
    "unresolved_findings": unresolved_findings,
    "options": ["retry", "skip", "escalate"],
}
```

**`round_summaries` 的来源必须是 `run_loop()` 运行时已有结果，不要从 DB 逆向猜：**
```python
round_summaries.append(
    {
        "round": result.round_num,
        "findings_total": result.findings_total,
        "open_count": result.open_count,
        "closed_count": result.closed_count,
        "new_count": result.new_count,
        "blocking_count": result.blocking_count,
        "suggestion_count": result.suggestion_count,
    }
)
```

**`_build_escalation_payload()` 实现思路：**
```python
async def _build_escalation_payload(
    self,
    db: aiosqlite.Connection,
    *,
    story_id: str,
    rounds_completed: int,
    remaining_blocking: int,
    round_summaries: list[dict[str, Any]],
) -> dict[str, Any]:
    all_findings = await get_findings_by_story(db, story_id)
    unresolved = await get_open_findings(db, story_id)
    convergence_rate = self._calculate_convergence_rate(all_findings)
    unresolved_findings = [
        {
            "finding_id": f.finding_id,
            "file_path": f.file_path,
            "rule_id": f.rule_id,
            "severity": f.severity,
            "description": f.description,
            "first_seen_round": f.round_num,
            "current_status": f.status,
        }
        for f in unresolved
    ]
    return {
        "rounds_completed": rounds_completed,
        "open_blocking_count": remaining_blocking,
        "final_convergence_rate": convergence_rate,
        "round_summaries": round_summaries,
        "unresolved_findings": unresolved_findings,
        "options": ["retry", "skip", "escalate"],
    }
```

**注意：**
- `round_summaries` 提供“每轮发生了什么”的准确摘要
- `unresolved_findings` 提供“现在还剩什么”的人工决策快照
- 当前 findings schema 只有 `first_seen_round + current_status`，**无法**从 DB 单独还原“某个 finding 精确在哪一轮 closed”

### Finding 状态轨迹查询

**MVP 合同（推荐）：** 不做逐轮插值，只返回当前 schema 能可靠表达的摘要：

```python
async def get_finding_trajectory(
    db: aiosqlite.Connection,
    story_id: str,
) -> list[dict[str, Any]]:
    """返回每个 finding 的 first_seen_round + current_status 摘要。"""
    findings = await get_findings_by_story(db, story_id)
    return [
        {
            "finding_id": f.finding_id,
            "file_path": f.file_path,
            "rule_id": f.rule_id,
            "severity": f.severity,
            "description": f.description,
            "first_seen_round": f.round_num,
            "current_status": f.status,
        }
        for f in sorted(findings, key=lambda f: (f.round_num, f.file_path, f.rule_id))
    ]
```

这个方案满足 FR14 在当前持久化模型下的真实能力边界：操作者知道“何时首次发现”和“现在是否仍未解决”。如果未来需要**精确**逐轮 per-finding 轨迹，需要新增专门的历史事件存储；本 Story 不扩 schema。

### 端到端集成测试设计

**5-Finding 场景（AC4）：**

```
Round 1 (first_review):
  → 发现 F1(blocking), F2(blocking), F3(blocking), F4(blocking), F5(suggestion)
  → converged=False (4 blocking)

Round 1 fix:
  → Claude 修复 F1, F2, F3

Round 2 (rereview):
  → F1: closed, F2: closed, F3: closed
  → F4: still_open
  → F5: suggestion 不影响
  → convergence_rate = 3/5 = 0.6 ≥ 0.5
  → 但 F4 仍 open blocking → converged=False

Round 2 fix:
  → Claude 修复 F4

Round 3 (rereview):
  → F4: closed
  → convergence_rate = 4/5 = 0.8 ≥ 0.5
  → 无 open blocking → converged=True ✓
```

**Mock 策略（与 3.2a-d 一致）：**
- mock `dispatch_with_retry` 返回预设 `AdapterResult`
- mock `bmad_adapter.parse` 返回不同轮次的预设 `BmadParseResult`：
  - Round 1: 5 个 findings
  - Round 2: 只包含 F4（匹配 dedup_hash → still_open）
  - Round 3: 0 个 findings（全部 closed）
- 使用 `initialized_db_path` fixture 创建临时文件型 SQLite
- 通过 `side_effect` 控制 mock 每次调用返回不同结果

**重要：不能直接 mock `run_first_review` / `run_rereview` — 需要真实执行完整方法链才能验证端到端行为（finding 入库、状态更新、收敛率计算）。只 mock 底层 I/O（subprocess、bmad adapter）。**

### 非法 Transition 测试设计

**场景（AC5）：**
- 使用真实 `StoryLifecycle` 状态机（`src/ato/state_machine.py`）
- Story 初始状态 `reviewing` → `review_fail` → `fixing`
- 在 `fixing` 状态尝试 `review_pass` → 应被拒绝

**关键参考：** `state_machine.py` 中 `review_pass = reviewing.to(qa_testing)` 只允许从 `reviewing` 状态触发。从 `fixing` 状态发送 `review_pass` 会被 python-statemachine 框架拒绝。

```python
from ato.state_machine import StoryLifecycle
from statemachine.exceptions import TransitionNotAllowed

sm = await StoryLifecycle.create()
await sm.send("start_create")    # queued → creating
await sm.send("create_done")     # creating → validating
await sm.send("validate_pass")   # validating → dev_ready
await sm.send("start_dev")       # dev_ready → developing
await sm.send("dev_done")        # developing → reviewing
await sm.send("review_fail")     # reviewing → fixing
with pytest.raises(TransitionNotAllowed):
    await sm.send("review_pass")  # 非法：fixing 不能直接 review_pass
```

**注意：** 不要引用不存在的 `PHASE_DEFINITIONS`、`start_plan`、`plan_done` 或 backlog/planning 旧状态名。当前状态机契约是 `queued/create_done/...`。

### 关键设计约束（从前序 Story 继承）

1. **TransitionEvent 合同：** `source="agent"` + `submitted_at` 必填（但收敛率计算不涉及 transition 提交，由 run_rereview 内部处理）
2. **Finding 持久化模型：** `round_num` = 首次发现轮次（不变），`status` 原地更新 — 不按轮次复制
3. **ConvergentLoopResult.converged：** 由 `run_first_review()` 和 `run_rereview()` 各自计算，`run_loop()` 直接读取
4. **Blocking 判定：** 仅 `severity="blocking"` 的 finding 影响收敛判断，`suggestion` 不阻塞
5. **maybe_create_blocking_abnormal_approval()：** 已在 run_first_review/run_rereview 内调用，与本 story 的 convergence_threshold 判定独立
6. **ConvergentLoopConfig：** `max_rounds` 已有验证 `>= 1`，`convergence_threshold` 验证 `[0, 1]`——本 story 首次使用 `convergence_threshold`
7. **Approval helper 合同：** 新的 approval 创建应复用 `create_approval()`，而不是手写 `ApprovalRecord` / `insert_approval()` 跳过推荐动作、nudge、bell 语义
8. **Escalation 幂等保护：** `_create_escalation_approval()` 已有幂等检查（Story 3.2d），插入前检查 pending 记录
9. **数据库连接约定：** `db = await get_connection(...); try/finally await db.close()`；**不要**写成 `async with get_connection(...)`
10. **run_first_review() 不修改：** 首轮收敛逻辑（0 blocking → converged）保持不变，收敛率阈值仅在 re-review 中生效
11. **轨迹能力边界：** 现有 schema 只可靠表达 `first_seen_round + current_status`；若要精确逐轮 per-finding 历史，需要新存储模型，不属于本 Story

### 已实现的代码入口点

| 方法 | 文件位置 | 职责 | 本 story 变更 |
|------|---------|------|-------------|
| `run_loop()` | `convergent_loop.py:83-185` | 编排完整多轮循环 | **小改：累积 `round_summaries`，在 escalation 时传给 payload builder** |
| `run_first_review()` | `convergent_loop.py:301-498` | 第 1 轮全量 review | 不变 |
| `run_fix_dispatch()` | `convergent_loop.py:580-717` | Claude fix agent 调度 | 不变 |
| `run_rereview()` | `convergent_loop.py:790-1001` | 第 2+ 轮 scoped re-review | **修改：收敛评估增加收敛率阈值** |
| `_match_findings_across_rounds()` | `convergent_loop.py:1041-1111` | 跨轮次 finding 匹配 | 不变 |
| `_create_escalation_approval()` | `convergent_loop.py:211-265` | Escalation approval 创建 | **修改：复用 `create_approval()` + payload 包含 `round_summaries/unresolved_findings`** |
| `_get_remaining_blocking_count()` | `convergent_loop.py:196-209` | DB 查询 open blocking 数量 | 不变 |
| `_is_abnormal_result()` | `convergent_loop.py:187-194` | 异常结果检测 | 不变 |
| `_log_termination_summary()` | `convergent_loop.py:267-295` | 终止摘要日志 | 不变 |
| `create_approval()` | `approval_helpers.py` | 统一 approval 创建 API | **复用，不重建** |
| `get_findings_by_story()` | `models/db.py:991-1009` | 查询 story 全部 findings | 被新方法复用 |
| `get_open_findings()` | `models/db.py:1012-1022` | 查询 open/still_open findings | 被收敛率计算复用 |
| `get_finding_trajectory()` | `models/db.py` | 查询 finding 状态摘要 | **新增** |

### 不要做的事情（防灾清单）

1. **不要修改 `run_first_review()` 的收敛逻辑**——首轮只判断 blocking 有/无，不检查收敛率（首轮无 closed findings）
2. **不要试图从 `findings` 表单独反推出“某个 finding 精确在哪一轮 closed”**——当前模型做不到
3. **不要绕过 `approval_helpers.create_approval()` 手写 approval 创建**——否则会偏离当前审批合同
4. **不要修改 `_match_findings_across_rounds()` 的匹配算法**——Story 3.2c 已实现正确
5. **不要修改 `run_fix_dispatch()`**——fix dispatch 与收敛率判定无关
6. **不要为 finding 轨迹创建新的 SQLite 表或新列**——本 Story 不扩 schema
7. **不要修改 `_log_termination_summary()`**——只在 `convergent_loop_round_complete` 中追加 `convergence_rate`
8. **不要修改 `ConvergentLoopResult` 模型**——现有字段足够支撑 `round_summaries`
9. **不要在 `tests/unit/test_convergent_loop.py` 复制现有状态机非法 transition 覆盖**——应复用 `tests/unit/test_state_machine.py`
10. **不要在端到端测试中 mock `run_first_review` / `run_rereview`**——需要真实执行完整方法链验证 finding 入库和收敛率计算
11. **不要修改 `ConvergentLoopConfig` 模型**——`convergence_threshold` 已定义且有验证
12. **不要在 convergent_loop.py 中导入 core.py**——保持模块隔离

### 测试策略

**测试文件：**
- `tests/unit/test_convergent_loop.py`——收敛率、payload、端到端场景
- `tests/unit/test_state_machine.py`——非法 transition 覆盖（优先复用已有测试位置）

**Mock 策略（与 3.2a-d 一致）：**
- 复用现有测试工厂：`_make_loop()` 创建 ConvergentLoop 实例（带 mock deps），`_make_story()` / `_make_finding()` / `_make_finding_record()` 等创建测试数据
- 收敛率单元测试：预先构造不同状态的 `FindingRecord` 列表，验证 `_calculate_convergence_rate(findings)` 返回值；必要时再用 DB snapshot 覆盖调用点
- 端到端测试：mock 底层 I/O（`dispatch_with_retry` + `bmad_adapter.parse`），使用 `side_effect` 控制多次调用返回不同结果，让 `run_loop()` 真实编排全流程
- 非法 transition 测试：使用真实 `StoryLifecycle` 状态机实例，放在 `tests/unit/test_state_machine.py`
- 使用 `initialized_db_path` fixture 创建临时文件型 SQLite（需要真实 DB 操作）
- 使用 `@pytest.mark.asyncio` 装饰器
- `structlog` 测试使用 `structlog.testing.capture_logs()` 捕获日志

**关键测试场景：**

```python
class TestConvergenceRateCalculation:
    """收敛率计算正确性。"""

class TestConvergenceRateThreshold:
    """收敛率阈值判定逻辑。"""

class TestEscalationPayload:
    """Escalation approval payload 含 round_summaries / unresolved_findings。"""

class TestFindingTrajectory:
    """Finding first_seen_round / current_status 摘要查询。"""

class TestIntegrationFiveFindingConvergence:
    """端到端 5-finding 场景，≤3 轮收敛。"""

class TestIllegalTransitionRejection:
    """非法 transition 拒绝（放在 test_state_machine.py）。"""

class TestConvergenceRateStructlog:
    """structlog 中 convergence_rate 字段验证。"""
```

### Project Structure Notes

- **修改文件：**
  - `src/ato/convergent_loop.py` — 新增 `_calculate_convergence_rate()`、`_build_escalation_payload()`；修改 `run_rereview()` 收敛评估逻辑；让 `run_loop()` 累积 `round_summaries`；修改 `_create_escalation_approval()` 复用 `create_approval()`
  - `src/ato/models/db.py` — 新增 `get_finding_trajectory()` 函数
  - `tests/unit/test_convergent_loop.py` — 追加约 10-15 个新测试
  - `tests/unit/test_state_machine.py` — 复用 / 追加非法 transition 覆盖（若现有覆盖不足）

- **不需要修改的文件：**
  - `src/ato/config.py` — `convergence_threshold` 已定义
  - `src/ato/models/schemas.py` — `ConvergentLoopResult` 已有足够字段
  - `src/ato/state_machine.py` — transition 定义已完备
  - `src/ato/approval_helpers.py` — helper 已存在，应复用而非重写
  - `src/ato/models/migrations.py` — 不需要新表或新列

### References

- [Source: _bmad-output/planning-artifacts/epics.md — Epic 3, Story 3.3 (line 867-896)] — AC 原文
- [Source: _bmad-output/planning-artifacts/architecture.md — Decision 3: 配置边界（convergence_threshold 可配置）]
- [Source: _bmad-output/planning-artifacts/architecture.md — Decision 6: structlog 结构化日志（Convergent Loop: round_num, findings_total, open_count, closed_count）]
- [Source: _bmad-output/planning-artifacts/architecture.md — Finding 跨轮次匹配算法]
- [Source: _bmad-output/planning-artifacts/prd.md — FR13 Convergent Loop 协议、FR14 finding 跨轮次状态追踪、FR17 max_rounds escalation、FR18 blocking severity 判定、NFR9 终止保证]
- [Source: _bmad-output/planning-artifacts/prd.md — 配置项：convergent_loop.convergence_threshold 默认 0.5]
- [Source: _bmad-output/planning-artifacts/prd.md — 创新验证：给定已知 5-finding review 场景端到端测试，≤3 轮内闭合所有 blocking findings]
- [Source: src/ato/convergent_loop.py — ConvergentLoop 类完整实现（run_loop/run_first_review/run_fix_dispatch/run_rereview）]
- [Source: src/ato/config.py:65-72 — ConvergentLoopConfig（max_rounds=3, convergence_threshold=0.5）]
- [Source: src/ato/config.py:287-290 — convergence_threshold 验证规则]
- [Source: src/ato/approval_helpers.py — `create_approval()` 统一 approval 创建 API]
- [Source: src/ato/models/schemas.py:252-263 — ConvergentLoopResult 模型]
- [Source: src/ato/models/schemas.py:323-337 — ApprovalRecord 模型（含 payload 字段）]
- [Source: src/ato/models/schemas.py:194-208 — FindingRecord 模型]
- [Source: src/ato/models/schemas.py:211-224 — compute_dedup_hash() SHA256 去重算法]
- [Source: src/ato/models/db.py:991-1022 — get_findings_by_story() / get_open_findings()]
- [Source: src/ato/state_machine.py — reviewing/fixing 状态与 transition 定义]
- [Source: tests/unit/test_convergent_loop.py — 现有测试 helpers（_make_loop, _make_finding, _make_finding_record, _make_finding_record_for_rereview）]
- [Source: tests/unit/test_state_machine.py — 现有非法 transition 覆盖]
- [Source: tests/conftest.py:19-23 — initialized_db_path fixture]
- [Source: _bmad-output/implementation-artifacts/3-2d-convergence-termination.md — run_loop() + escalation 实现细节]
- [Source: _bmad-output/implementation-artifacts/3-2c-re-review-scope-narrowing.md — run_rereview() + _match_findings_across_rounds() 实现细节]
- [Source: _bmad-output/implementation-artifacts/3-2b-fix-dispatch-artifact-verification.md — run_fix_dispatch() 实现细节]
- [Source: _bmad-output/implementation-artifacts/3-2a-convergent-loop-full-review.md — run_first_review() 实现细节]
- [Source: _bmad-output/implementation-artifacts/3-1-deterministic-validation-finding-tracking.md — FindingRecord / findings 表 / compute_dedup_hash 基础设施]
- [Source: _bmad-output/implementation-artifacts/4-1-approval-queue-nudge.md — ApprovalRecord 扩展、approval_helpers.py 统一创建 API]
- [Source: _bmad-output/project-context.md — 项目上下文规则（asyncio 模式、测试规则、代码质量门控）]

### Change Log

- 2026-03-27: create-story 创建 — 基于 Epic 3 / PRD / 架构 / 前序 story 3.1-3.2d 生成 3.3 初稿
- 2026-03-27: dev-story 实现完成 — 收敛率计算 + 阈值判定、escalation payload 增强（round_summaries / unresolved_findings / options）、finding 轨迹查询、14 个新测试、1196 全量通过无回归
- 2026-03-27: validate-create-story 修订 —— 将“完整 per-finding 逐轮轨迹”收敛到当前 schema 可表达的 `first_seen_round + current_status`；把 escalation payload 改为 runtime `round_summaries + unresolved_findings + final_convergence_rate`；要求 `_create_escalation_approval()` 复用 `create_approval()`；修正非法 transition 示例里的旧状态机事件名；把 5-finding 场景统一为 4 blocking + 1 suggestion，避免 AC / 任务 / 例子互相打架

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6 (1M context)

### Debug Log References

无 debug issues。

### Completion Notes List

- ✅ Task 1: 实现 `_calculate_convergence_rate()` 纯 helper（closed/total，0 findings → 1.0），修改 `run_rereview()` 在 DB 写入后计算收敛率，收敛判定增加 `convergence_rate >= convergence_threshold` 条件，structlog `convergent_loop_round_complete` 新增 `convergence_rate` 字段
- ✅ Task 2: `run_loop()` 累积 `round_summaries`，新增 `_build_escalation_payload()` 组装 `final_convergence_rate / round_summaries / unresolved_findings / options`，`_create_escalation_approval()` 重写为复用 `create_approval()` 统一 API
- ✅ Task 3: `models/db.py` 新增 `get_finding_trajectory()`，返回 `first_seen_round + current_status` 摘要（不做逐轮插值）
- ✅ Task 4: 端到端 5-finding 集成测试（4 blocking + 1 suggestion），验证 ≤3 轮收敛、所有 findings closed、无 escalation
- ✅ Task 5: 复用 `test_state_machine.py` 中已有 `test_fixing_rejects_review_pass`，新增 `test_fixing_rejects_review_pass_logs_warning` 验证完整 AC5 链路 + structlog
- ✅ Task 6: 6 个收敛率单元测试（全 closed、全 open、partial、zero findings、threshold 判定 3 种场景、structlog 字段验证）
- ✅ Task 7: escalation payload 含 round_summaries / unresolved_findings / options 验证，`get_finding_trajectory` 正常/空 story 测试

### File List

- `src/ato/convergent_loop.py` — 新增 `_calculate_convergence_rate()`、`_build_escalation_payload()`；修改 `run_rereview()` 收敛评估；`run_loop()` 累积 `round_summaries`；`_create_escalation_approval()` 复用 `create_approval()`
- `src/ato/models/db.py` — 新增 `get_finding_trajectory()` 函数、`Any` import
- `tests/unit/test_convergent_loop.py` — 新增 13 个测试（7 个测试类）
- `tests/unit/test_state_machine.py` — 新增 1 个测试 `test_fixing_rejects_review_pass_logs_warning`
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — 状态更新
- `_bmad-output/implementation-artifacts/3-3-convergence-trust-escalation.md` — story 文件更新
