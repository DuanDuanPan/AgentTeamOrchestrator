# Story 验证报告：3.2c Re-review Scope Narrowing

验证时间：2026-03-25 17:10:36 CST
Story 文件：`_bmad-output/implementation-artifacts/3-2c-re-review-scope-narrowing.md`
验证模式：`validate-create-story`
结果：PASS（已应用修正）

## 摘要

该 story 的主方向正确，但原稿里有 4 个会直接误导实现的合同问题：

1. 它把 `new` 同时当成“分类结果”和 SQLite `status` 值来写，而当前 `FindingStatus` 只有 `open | closed | still_open`。
2. 它要求按 `round_num = round_num - 1` 查询“上一轮 open findings”，这和当前 `findings` 表的持久化模型冲突，会让 round 3+ 丢失更早轮次遗留但仍未解决的 finding。
3. 它一边要求复用首轮的 blocking threshold 检查，一边又写“不要实现 escalation approval 创建”，把 `blocking_abnormal` 和后续 story 的 `convergent_loop_escalation` 混在了一起。
4. 它把 `open_count` 缩成“仅 blocking”，还给出了一个当前测试 helper 并不支持的 `_make_finding_record(round_num=...)` 示例，容易把实现和测试一起带偏。

这些问题如果不修，最常见的后果是：开发者会尝试写入非法 finding status、在第 3 轮起错误收窄 scope、误删本 story 应保留的 `blocking_abnormal` 检查，以及照着无效测试示例实现 fixture。

## 已核查证据

- 规划与 story 工件：
  - `_bmad-output/planning-artifacts/epics.md`
  - `_bmad-output/planning-artifacts/architecture.md`
  - `_bmad-output/planning-artifacts/prd.md`
  - `_bmad-output/implementation-artifacts/3-1-deterministic-validation-finding-tracking.md`
  - `_bmad-output/implementation-artifacts/3-2a-convergent-loop-full-review.md`
  - `_bmad-output/implementation-artifacts/3-2b-fix-dispatch-artifact-verification.md`
- 当前代码基线：
  - `src/ato/models/schemas.py`
  - `src/ato/models/db.py`
  - `src/ato/convergent_loop.py`
  - `tests/unit/test_convergent_loop.py`

## 发现的关键问题

### 1. `status="new"` 与当前 `FindingStatus` 模型不兼容

原稿 AC2 / AC3 同时写了：

- `new（status=open, round_num=N+1）`
- `新 finding 以 status=new 入库`

但当前仓库的真实合同是：

- `FindingStatus = Literal["open", "closed", "still_open"]`
- `open` 已承担“新发现 / 未修复”的存储语义

如果不纠正，dev 最容易做出的错误实现是：把 `new` 当成第四个数据库状态，结果在 Pydantic / DB helper 校验时直接失败。

已应用修正：

- AC3 改为：`new` 是匹配分类结果，落库仍使用 `status="open"`、`round_num=N+1`
- Task / Dev Notes / Change Log 一并统一为该语义

### 2. 仅按 `previous_round` 查询会破坏 round 3+ 的 scope narrowing

原稿要求：

- 用 `get_findings_by_story(db, story_id, round_num=previous_round)` 查“上一轮 open findings”
- 然后把这些 finding 里匹配到的更新成 `still_open`

这和当前持久化模型组合起来会出问题：

- `round_num` 在当前 schema 中表示 finding 的首次发现轮次
- `still_open` / `closed` 是对已有记录的原地状态更新
- 只有真正新出现的 finding 会以当前轮次新插入一条记录

也就是说，如果 round 1 的 finding 在 round 2 仍未修复，它依然是 `round_num=1, status="still_open"`；到了 round 3，再按 `round_num=2` 截断查询，就会把这条仍未解决的旧 finding 从 scope 里漏掉。

已应用修正：

- Scope 源集合改为 `get_open_findings(db, story_id)` 返回的当前 unresolved 集合
- Dev Notes 明确：不要把 `round_num` 当成“当前仍待复审集合”的主过滤条件
- 增补测试要求：round 3+ 仍应包含更早轮次遗留的 `still_open` finding

### 3. `blocking_abnormal` 与 `convergent_loop_escalation` 的边界被写混

原稿同时写了：

- 方法流程里要做 “Blocking threshold escalation（与首轮一致）”
- “不要实现 escalation approval 创建——是 Story 3.2d/3.3”

这会直接让开发者困惑：到底要不要调用 `maybe_create_blocking_abnormal_approval()`？

按当前规划与代码基线，这两者不是一回事：

- Story 3.2a 已落地并复用 `maybe_create_blocking_abnormal_approval()`，对应 FR18 的 blocking 数量异常
- Story 3.2d / 3.3 才负责 `max_rounds` / 未收敛时的 `convergent_loop_escalation`

已应用修正：

- 方法流程改为显式复用 `maybe_create_blocking_abnormal_approval()`
- “不要做的事情” 改成只禁止 `convergent_loop_escalation` / max_rounds approval

### 4. `open_count` 与测试示例都有语义漂移

原稿把：

- `open_count` 定义成“当前仍 open/still_open/new 的 blocking 数”

但在 3.2a 当前实现里：

- `open_count` 表示当前仍 open 的 finding 总数
- `blocking_count` / `suggestion_count` 已经是独立字段

如果在 3.2c 把 `open_count` 改成 blocking-only，会让 `open_count` 与 `blocking_count` 的区分变得模糊，也会让日志和结果模型前后不一致。

此外，原稿示例还写了：

- `_make_finding_record(..., round_num=1, ...)`

但当前 `tests/unit/test_convergent_loop.py` 中的 helper 并不接受 `round_num` 参数。

已应用修正：

- `open_count` 改为当前 unresolved finding 总数（`still_open + new`，不限 severity）
- structlog 示例中的变量名同步改正
- 测试示例改为使用现有 helper 形式，并注明如需非默认轮次 fixture，可直接构造 `FindingRecord` 或先扩展 helper

## 已应用增强

- 在 Dev Notes 中显式补入：当前 schema 下 `round_num` 表示首次发现轮次，旧 finding 在 re-review 中只更新状态，不复制到新轮次
- 在 Change Log 中记录本次 validate-create-story 的修订点，便于后续追溯
- 移除了前驱 story intelligence 里易漂移的“测试总数”表述，降低文档失真速度

## 剩余风险

- 当前 story 仍默认通过 `update_finding_status()` 逐条更新 `still_open/closed`。由于该 helper 目前是单条提交，如果后续实现要求“整轮 diff 原子落库”，可能需要在未来补一个批量更新 helper，或允许在同一事务中提交整轮状态变更。
- 规划文档对 FR14 的表述仍带有“round 1: open → round 2: closed”这类按轮次展示轨迹的口径；当前 story 已按现有 schema 选用了“首次发现轮次 + 当前状态”的实现模型。如果后续要把完整逐轮轨迹直接存在 SQLite 中，可能需要在 3.3 或更后续 story 扩展数据模型。

## 最终结论

修正后，Story 3.2c 已达到 `ready-for-dev` 的质量门槛。当前版本已经和真实的 `FindingStatus` 模型、`findings` 表持久化语义、首轮 blocking threshold 合同，以及现有测试 helper 边界对齐，不会再把 dev agent 带向错误的 scope narrowing 或非法 status 实现。
