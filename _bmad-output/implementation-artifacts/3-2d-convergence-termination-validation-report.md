# Story 验证报告：3.2d 收敛判定与终止条件

验证时间：2026-03-25 20:17:20 CST
Story 文件：`_bmad-output/implementation-artifacts/3-2d-convergence-termination.md`
验证模式：`validate-create-story`
结果：PASS（已应用修正）

## 摘要

该 story 的主题和主要实现方向是对的，但原稿里有 5 个会直接误导实现的合同问题：

1. `run_loop()` 伪代码把 `run_fix_dispatch()` 的 `round_num` 传成了 re-review 轮次，和 3.2b 已定下的 `round N review → round N fix → round N+1 rereview` 语义冲突。
2. 文档要求使用 `async with get_connection(self._db_path) as db:`，这和当前 `get_connection()` 的真实调用约定不符，开发者照写很容易直接写出错误模式。
3. escalation approval 写成了不存在的 `metadata` 字段，而当前 `ApprovalRecord` 的真实载荷字段是 `payload: str | None`。
4. 文档说 escalation 不需要新状态且 “story 停在 reviewing 等待人工”，但当前 `run_first_review()` / `run_rereview()` 在未收敛时都会先提交 `review_fail`，所以 max_rounds 终止路径实际会停在 review loop 的当前相位，通常是 `fixing`。
5. 文档一边要求 `run_loop()` 再发每轮 `convergent_loop_round_complete`，一边把 `remaining_blocking` 写成 `last_result.open_count`。前者会和 3.2a/3.2c 已有日志重复，后者又把 suggestion 也算进“剩余 blocking”。

这些问题如果不修，最常见的后果是：开发者会把 fix / re-review 轮次对错、按一个不存在的 approval 字段落库、照着无效的 DB 连接方式写代码、误判 max-rounds 终止后的 story 相位，以及重复发出相互冲突的 round log。

## 已核查证据

- 规划与 story 工件：
  - `_bmad-output/planning-artifacts/epics.md`
  - `_bmad-output/planning-artifacts/prd.md`
  - `_bmad-output/planning-artifacts/architecture.md`
  - `_bmad-output/planning-artifacts/ux-design-specification.md`
  - `_bmad-output/implementation-artifacts/3-2b-fix-dispatch-artifact-verification.md`
  - `_bmad-output/implementation-artifacts/3-2c-re-review-scope-narrowing.md`
- 当前代码基线：
  - `src/ato/convergent_loop.py`
  - `src/ato/models/schemas.py`
  - `src/ato/models/db.py`
  - `src/ato/validation.py`
  - `tests/unit/test_convergent_loop.py`
  - `tests/conftest.py`

## 发现的关键问题

### 1. `run_loop()` 的 `round_num` 编排和前序 story 合同冲突

原稿伪代码写成了：

- `for round_num in range(2, max_rounds + 1):`
- `await self.run_fix_dispatch(story_id, round_num, worktree_path)`
- `result = await self.run_rereview(story_id, round_num, worktree_path)`

但 3.2b 已明确 fix 阶段的 `round_num` 与其所属 review 相同，3.2c 又明确 re-review 是第 `N+1` 轮。也就是说，正确节奏应是：

- `round 1 review`
- `round 1 fix`
- `round 2 rereview`

如果不改，第一轮 fix 就会被误记为 round 2，后续日志、测试断言和操作员进度认知都会漂移。

已应用修正：

- 伪代码改为 `fix_round = rereview_round - 1`
- 关键约束中明确：`max_rounds` 统计的是 review / re-review 轮次，不是 fix 次数
- 任务描述同步改成 `run_fix_dispatch(fix_round) -> run_rereview(rereview_round)`

### 2. `async with get_connection(...)` 不符合当前 helper 合同

原稿写了：

- “所有方法内部通过 `async with get_connection(self._db_path) as db:` 获取数据库连接”

但当前 `get_connection()` 的真实签名是异步工厂函数，调用约定是：

- `db = await get_connection(...)`
- `try: ... finally: await db.close()`

当前代码库也整体采用这套模式，包括 `convergent_loop.py` 自己。

已应用修正：

- 改成显式说明：遵循当前代码库模式，`db = await get_connection(...); try/finally await db.close()`
- 明确禁止把它写成 `async with get_connection(...)`

### 3. escalation approval 的载荷字段写成了不存在的 `metadata`

原稿把 approval 字段写成：

- `metadata` 含：`rounds_completed`、`open_blocking_count`

但当前 `ApprovalRecord` 没有 `metadata` 字段，只有：

- `approval_type`
- `story_id`
- `payload`

如果不纠正，dev 很容易直接朝不存在的字段建构对象，或者误以为要扩 schema。

已应用修正：

- 改为 `approval.payload` 使用 JSON 字符串
- 明确 payload 中包含 `rounds_completed` 和 `open_blocking_count`
- 引用补充到 `ApprovalRecord` 的真实字段定义

### 4. max-rounds 终止后的相位说明与当前 transition 合同相冲突

原稿写了：

- “escalation 不需要新状态，story 停在 reviewing 等待人工”

但当前真实流程是：

- `run_first_review()` 未收敛时立即提交 `review_fail`
- `run_rereview()` 未收敛时也立即提交 `review_fail`

所以 max-rounds 终止的两条路径里，`run_loop()` 看到“不收敛”结果时，子方法已经把 story 推到 review loop 的下一个失败相位了。在现有状态机下，这通常就是 `fixing`，而不是 `reviewing`。

已应用修正：

- 把“停在 reviewing”等价说法移除
- 改成和真实合同一致的描述：终止后通过 approval 等待人工，不要为了 escalation 再新增状态机转换或回滚子方法已提交的 `review_fail`

### 5. 日志职责和 `remaining_blocking` 统计口径都写偏了

原稿同时存在两个问题：

- 要求 `run_loop()` 自己再发每轮 `convergent_loop_round_complete`
- escalation warning 中使用 `remaining_blocking=last_result.open_count`

这两点都会带偏实现：

- 3.2a / 3.2c 已经在子方法里发出了 per-round `convergent_loop_round_complete`，run_loop 再发一遍会制造重复日志
- `open_count` 包含所有 unresolved finding，不只 blocking；拿它做 `remaining_blocking` 会把 suggestion 也算进去

已应用修正：

- Task 3 改成只要求实现 loop 终止摘要日志 helper
- 明确每轮 diff 日志继续复用 `run_first_review()` / `run_rereview()` 已有输出
- `remaining_blocking` 改为取 `last_result.blocking_count`

## 已应用增强

- 在测试策略里把 `initialized_db_path` 的说明改成真实的“临时文件型 SQLite”，去掉误导性的 `:memory:`
- 在断言要点中增加 escalation approval `payload` JSON 字段校验
- 在 Change Log 中记录本次 validate-create-story 修订，方便后续 story traceability

## 剩余风险

- 当前 story 仍假设 `run_first_review()` / `run_rereview()` 的 transition 提交行为保持不变。如果未来把“是否提交 transition”提升到 `run_loop()` 统一管理，这个 story 需要再做一次合同重写，而不只是局部补丁。
- 终止摘要日志目前只要求记录终止事件，不要求新增跨轮历史 DTO。如果后续 TUI 或审批面板希望直接消费完整逐轮 diff 摘要，可能还需要在 3.3 或更后续 story 增加单独的聚合表示层。

## 最终结论

修正后，Story 3.2d 已达到 `ready-for-dev` 的质量门槛。当前版本已经和 3.2b / 3.2c 的轮次语义、`ApprovalRecord` / `get_connection()` 的真实代码合同、现有状态机行为，以及当前 structlog 职责边界对齐，不会再把 dev agent 带向错误的编排、错误的 approval 载荷或错误的终止相位假设。
