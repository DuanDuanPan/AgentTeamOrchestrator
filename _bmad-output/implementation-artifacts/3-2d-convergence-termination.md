# Story 3.2d: 收敛判定与终止条件

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a 系统,
I want 基于 finding 状态准确判定收敛或终止循环,
So that Convergent Loop 有确定性的结束条件，不会无限循环。

## Acceptance Criteria

1. **AC1: 收敛成功判定** — re-review 完成后，所有 blocking findings 为 closed → 收敛成功，提交 `review_pass` 事件，story 进入 qa_testing
2. **AC2: 继续循环** — 仍有 open blocking findings 且未达 max_rounds → 继续下一轮（回到 fix 阶段）
3. **AC3: 强制终止** — 轮次达到 `max_rounds` → 强制终止循环（NFR9），创建 approval 记录（类型 `convergent_loop_escalation`），通知操作者人工介入
4. **AC4: 完整历史日志** — 保留 `run_first_review()` / `run_rereview()` 已输出的每轮 findings diff 日志（`open_count`、`closed_count`、`new_count`、`still_open_count`），并在 loop 终止时补充终止摘要日志
5. **AC5: 编排方法** — 实现 `run_loop()` 方法，编排完整的 review→fix→rereview 多轮循环，统一管理轮次计数和终止逻辑

## Tasks / Subtasks

- [ ] Task 1: 实现 `run_loop()` 编排方法 (AC: #5, #1, #2, #3)
  - [ ] 1.1 首轮调用 `run_first_review()` 并评估结果
  - [ ] 1.2 循环体：`run_fix_dispatch(fix_round)` → `run_rereview(rereview_round)` → 评估收敛
  - [ ] 1.3 收敛成功路径：`converged=True` → 直接返回结果（`review_pass` 已由子方法提交）
  - [ ] 1.4 继续循环路径：`converged=False` 且 `rereview_round < max_rounds` → 继续下一轮 fix+rereview
  - [ ] 1.5 强制终止路径：最后一轮 review / re-review 仍未收敛 → 创建 escalation approval → 返回结果
- [ ] Task 2: 实现 `_create_escalation_approval()` (AC: #3)
  - [ ] 2.1 创建 approval 记录 `approval_type="convergent_loop_escalation"`
  - [ ] 2.2 `approval.payload` 使用 JSON，包含 `rounds_completed`、`open_blocking_count`
  - [ ] 2.3 注册 nudge 通知（复用现有 nudge 机制）
- [ ] Task 3: 实现 `_log_termination_summary()` 终止摘要日志 (AC: #4)
  - [ ] 3.1 **不要**重复记录子方法已输出的 `convergent_loop_round_complete`；每轮 diff 继续由 `run_first_review()` / `run_rereview()` 负责
  - [ ] 3.2 loop 终止时记录 `total_rounds`、`max_rounds`、`remaining_blocking` 等摘要字段
- [ ] Task 4: 编写单元测试 (AC: #1-#5)
  - [ ] 4.1 首轮 0 findings → 立即收敛
  - [ ] 4.2 首轮有 blocking → fix → rereview → 收敛
  - [ ] 4.3 多轮循环 → 第 N 轮收敛
  - [ ] 4.4 达到 max_rounds → 强制终止 + escalation approval
  - [ ] 4.5 max_rounds=1 边界情况
  - [ ] 4.6 loop 终止 structlog 输出验证（不重复断言 3.2a-c 已覆盖的 round_complete）
  - [ ] 4.7 escalation approval 字段验证

## Dev Notes

### 核心实现：`run_loop()` 编排方法

在 `ConvergentLoop` 类中新增 `run_loop()` 方法，编排完整的多轮 review-fix-rereview 循环。这是 Story 3.2a/3.2b/3.2c 已实现方法的上层编排器。

**方法签名（与现有方法一致，不传 db — 内部通过 `get_connection(self._db_path)` 获取）：**
```python
async def run_loop(
    self,
    story_id: str,
    worktree_path: str | None = None,
    *,
    artifact_payload: dict[str, Any] | None = None,
) -> ConvergentLoopResult:
```

**编排逻辑伪代码：**
```python
max_rounds = self._config.max_rounds  # ConvergentLoopConfig，默认 3

# 第 1 轮：全量 review（注意：run_first_review 不接受 round_num 参数，内部固定为 1）
result = await self.run_first_review(story_id, worktree_path, artifact_payload=artifact_payload)
if result.converged:
    self._log_termination_summary(
        story_id=story_id,
        total_rounds=1,
        max_rounds=max_rounds,
        last_result=result,
    )
    return result  # 0 blocking，直接收敛

# max_rounds=1：首轮 review 未收敛时直接 escalation（不再进入 fix / rereview）
if max_rounds == 1:
    await self._create_escalation_approval(story_id, 1, result)
    self._log_termination_summary(
        story_id=story_id,
        total_rounds=1,
        max_rounds=max_rounds,
        last_result=result,
    )
    return result

# 第 2+ 轮：上一轮 fix → 本轮 rereview
for rereview_round in range(2, max_rounds + 1):
    fix_round = rereview_round - 1  # round N review → round N fix → round N+1 rereview
    await self.run_fix_dispatch(story_id, fix_round, worktree_path)
    result = await self.run_rereview(story_id, rereview_round, worktree_path)
    if result.converged:
        self._log_termination_summary(
            story_id=story_id,
            total_rounds=rereview_round,
            max_rounds=max_rounds,
            last_result=result,
        )
        return result  # 所有 blocking closed

# 达到 max_rounds 仍未收敛 → 强制终止 + escalation
await self._create_escalation_approval(story_id, max_rounds, result)
self._log_termination_summary(
    story_id=story_id,
    total_rounds=max_rounds,
    max_rounds=max_rounds,
    last_result=result,
)
return result  # converged=False
```

**关键约束：**
- `run_first_review()` 内部已提交 `review_pass`/`review_fail` transition event — `run_loop()` **不要**重复提交
- `run_fix_dispatch()` 内部已提交 `fix_done` event — `run_loop()` **不要**重复提交
- `run_rereview()` 内部已提交 `review_pass`/`review_fail` event — `run_loop()` **不要**重复提交
- `run_loop()` 只负责轮次计数、终止判断、escalation 创建、日志记录
- `max_rounds` 统计的是 review / re-review 轮次，不是 fix 次数
- 轮次号从 1 开始（首轮 review）；fix 阶段 round_num 与其所属 review 相同（`round N review → round N fix → round N+1 rereview`）
- `run_first_review()` 的签名是 `(self, story_id, worktree_path=None, *, artifact_payload=None)` — **没有** `round_num` 参数（内部固定为 1）
- `run_fix_dispatch()` 和 `run_rereview()` 的签名是 `(self, story_id, round_num, worktree_path=None)` — **没有** `db` 参数
- 数据库连接遵循当前代码库模式：`db = await get_connection(...); try/finally await db.close()`；**不要**写成 `async with get_connection(...)`

### Escalation Approval 创建

**复用现有 approval 基础设施（Story 1.2 已建 approvals 表）：**

```python
async def _create_escalation_approval(
    self,
    story_id: str,
    rounds_completed: int,
    last_result: ConvergentLoopResult,
) -> None:
    """内部通过 get_connection(self._db_path) 获取 db 连接，与其他方法一致。"""
```

**approval 记录字段：**
- `approval_type = "convergent_loop_escalation"` — **不是** `blocking_abnormal`（那是阈值超标用的）
- `story_id` = 当前 story
- `payload` 是 JSON 字符串，含：`rounds_completed`、`open_blocking_count`（剩余未闭合 blocking 数）
- 通过 nudge 通知操作者

**参考现有 `maybe_create_blocking_abnormal_approval()` 实现模式：**
- 该函数在 `src/ato/validation.py:119-210` 中定义，被 `convergent_loop.py` 导入使用，可参考其 approval 插入 + nudge 注册模式
- 但 **不要复用** `blocking_abnormal` 类型 — 语义不同：`blocking_abnormal` 是单轮阈值超标，`convergent_loop_escalation` 是多轮未收敛

### structlog 日志需求

**每轮 diff 日志：复用已有子方法日志，不要重复发同名事件**
- `run_first_review()` 已记录首轮 `convergent_loop_round_complete`
- `run_rereview()` 已记录后续轮次 `convergent_loop_round_complete`
- `run_loop()` 只补充 loop 终止摘要，不要再次发送重复的 `convergent_loop_round_complete`

**终止时额外记录（收敛或 escalation）：**
```python
# 收敛成功
logger.info(
    "convergent_loop_converged",
    story_id=story_id,
    total_rounds=total_rounds,
    max_rounds=max_rounds,
)

# 强制终止
logger.warning(
    "convergent_loop_max_rounds_reached",
    story_id=story_id,
    total_rounds=total_rounds,
    max_rounds=max_rounds,
    remaining_blocking=last_result.blocking_count,
)
```

### 关键设计约束（从前序 Story 继承）

1. **TransitionEvent 合同：** `source="agent"` + `submitted_at` 必填（但 run_loop 不直接提交 transition，由子方法内部处理）
2. **Finding 持久化模型：** `round_num` = 首次发现轮次（不变），`status` 原地更新 — 不按轮次复制
3. **ConvergentLoopResult.converged：** 由 `run_first_review()` 和 `run_rereview()` 各自计算，`run_loop()` 直接读取
4. **Blocking 判定：** 仅 `severity="blocking"` 的 finding 影响收敛判断，`suggestion` 不阻塞
5. **maybe_create_blocking_abnormal_approval()：** 已在 run_first_review/run_rereview 内调用，run_loop 不需要再调用
6. **ConvergentLoopConfig：** `max_rounds` 已有验证 `>= 1`，`convergence_threshold` 当前留给 Story 3.3 使用
7. **Escalation 终止相位：** 由于 `run_first_review()` / `run_rereview()` 在未收敛时已提交 `review_fail`，`max_rounds` 终止路径下 story 会保持在当前 review-loop 相位；按现有状态机合同通常停在 `fixing` 并通过 approval 等待人工介入

### 已实现的代码入口点

| 方法 | 文件位置 | 职责 |
|------|---------|------|
| `run_first_review()` | `src/ato/convergent_loop.py:78-269` | 第 1 轮全量 review |
| `run_fix_dispatch()` | `src/ato/convergent_loop.py:351-488` | Claude fix agent 调度 |
| `run_rereview()` | `src/ato/convergent_loop.py:561-765` | 第 2+ 轮 scoped re-review |
| `_match_findings_across_rounds()` | `src/ato/convergent_loop.py:805-875` | 跨轮次 finding 匹配 |
| `_build_fix_prompt()` | `src/ato/convergent_loop.py` | Fix prompt 构建 |
| `_build_rereview_prompt()` | `src/ato/convergent_loop.py:767-803` | Re-review prompt 构建 |
| `_get_worktree_head()` | `src/ato/convergent_loop.py` | Git HEAD hash 获取 |
| `maybe_create_blocking_abnormal_approval()` | `src/ato/validation.py:119-210`（被 convergent_loop.py 导入）| Blocking 阈值 escalation |

### 不要做的事情（防灾清单）

1. **不要**在 `run_loop()` 中重复提交 transition event — 子方法已处理
2. **不要**复用 `blocking_abnormal` approval 类型做 escalation — 语义不同
3. **不要**在 `run_loop()` 中直接操作 findings 表 — 由子方法处理
4. **不要**修改 `ConvergentLoopResult` 模型 — 已有字段足够（converged、open_count 等）
5. **不要**为了 escalation 新增状态机 transition 或回滚子方法已提交的 `review_fail`——现有 reviewing↔fixing 路径已满足，终止后通过 approval 等待人工即可
6. **不要**修改 `run_first_review()`/`run_fix_dispatch()`/`run_rereview()` 的签名或内部逻辑
7. **不要**在 convergence_threshold 上做判断 — 该参数留给 Story 3.3

### Approval 类型注册

`ApprovalRecord.approval_type` 是 `str` 自由字符串（非枚举），直接使用 `"convergent_loop_escalation"` 即可。现有已用类型：`"blocking_abnormal"`。

**Approval 插入函数：** `src/ato/models/db.py:510-526` 的 `insert_approval(db, approval: ApprovalRecord)` — 接受完整的 `ApprovalRecord` 对象。`_create_escalation_approval()` 内部需通过 `get_connection()` 获取 db 连接后调用此函数。

**Nudge 注册：** 参考 `maybe_create_blocking_abnormal_approval()` 中的 nudge 调用模式，通过 `self._nudge`（构造函数注入）注册通知。

### 测试策略

**测试文件：** `tests/unit/test_convergent_loop.py`（追加新测试类）

**Mock 策略（与 3.2a-c 一致）：**
- 复用现有测试工厂：`_make_loop()` 创建 ConvergentLoop 实例（带 mock deps），`_make_story()` / `_make_finding()` 等创建测试数据
- 使用 `unittest.mock.AsyncMock` patch `self.run_first_review` / `self.run_fix_dispatch` / `self.run_rereview` 返回预设的 `ConvergentLoopResult`
- 验证 `run_loop()` 的编排逻辑：轮次控制、终止条件、escalation 创建
- 使用 `initialized_db_path` fixture 创建临时文件型 SQLite 验证 approval 记录写入（不是 `:memory:`）
- 使用 `@pytest.mark.asyncio` 装饰器

**关键测试场景：**

```python
class TestRunLoopConvergesFirstRound:
    """首轮 0 blocking → run_first_review returns converged=True → 直接返回"""

class TestRunLoopConvergesAfterFix:
    """首轮有 blocking → fix → rereview converged → 返回"""

class TestRunLoopMultipleRounds:
    """多轮 fix-rereview 后收敛"""

class TestRunLoopMaxRoundsEscalation:
    """达到 max_rounds → 强制终止 + escalation approval 创建"""

class TestRunLoopMaxRoundsOneEdge:
    """max_rounds=1 → 首轮 review 后若不收敛直接 escalation"""

class TestRunLoopStructlogOutput:
    """验证 run_loop 只补终止日志；每轮 round_complete 已由 3.2a-c 覆盖"""

class TestEscalationApprovalFields:
    """验证 escalation approval 记录的字段完整性"""
```

**断言要点：**
- `run_first_review` 调用次数 = 1（始终）
- `run_fix_dispatch` 调用次数 = 实际 fix 轮数
- `run_rereview` 调用次数 = 实际 rereview 轮数
- escalation 场景中 approval 表有 `convergent_loop_escalation` 记录
- escalation approval 的 `payload` JSON 含 `rounds_completed`、`open_blocking_count`
- 收敛场景中 approval 表无 escalation 记录
- run_loop 自身只补 `convergent_loop_converged` / `convergent_loop_max_rounds_reached` 等终止摘要日志

### Project Structure Notes

- 所有变更集中在 `src/ato/convergent_loop.py`（新增方法）和 `tests/unit/test_convergent_loop.py`（新增测试）
- `src/ato/models/db.py` 已有 `insert_approval(db, ApprovalRecord)` 函数（line 510-526），approval_type 为自由字符串，无需修改
- `src/ato/models/db.py` 已有 `get_pending_approvals(db)` 函数（line 529-536），可用于测试验证
- **不需要**修改状态机、schemas、config、db — 已有基础设施足够

### References

- [Source: _bmad-output/planning-artifacts/epics.md — Epic 3, Story 3.2d 收敛判定与终止条件]
- [Source: _bmad-output/planning-artifacts/architecture.md — Convergent Loop 质量门控设计、Decision 3 配置边界]
- [Source: _bmad-output/planning-artifacts/prd.md — FR13-FR18 质量门控需求、NFR9 终止保证]
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md — ConvergentLoopProgress 组件、ExceptionApprovalPanel]
- [Source: _bmad-output/implementation-artifacts/3-2a-convergent-loop-full-review.md — run_first_review() 实现细节]
- [Source: _bmad-output/implementation-artifacts/3-2b-fix-dispatch-artifact-verification.md — run_fix_dispatch() 实现细节]
- [Source: _bmad-output/implementation-artifacts/3-2c-re-review-scope-narrowing.md — run_rereview() + _match_findings_across_rounds() 实现细节]
- [Source: src/ato/convergent_loop.py — 当前实现代码]
- [Source: src/ato/config.py:65-72 — ConvergentLoopConfig]
- [Source: src/ato/models/schemas.py:197-209 — ConvergentLoopResult 模型]
- [Source: src/ato/models/schemas.py:268-276 — ApprovalRecord（`payload` 字段）]
- [Source: src/ato/models/db.py:169-189 — get_connection() 调用约定]

### Change Log

- 2026-03-25: validate-create-story 修订 —— 对齐 `round_num` 编排合同（`round N review → round N fix → round N+1 rereview`）；将 approval `metadata` 改为真实的 `payload` JSON；移除无效的 `async with get_connection(...)` 指导；改为复用 3.2a-c 的每轮日志并仅在 loop 终止时补摘要；修正 `max_rounds` 终止相位说明与 `remaining_blocking` 统计口径

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

### File List
