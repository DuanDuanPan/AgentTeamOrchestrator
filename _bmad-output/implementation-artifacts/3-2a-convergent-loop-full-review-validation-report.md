# Story 验证报告：3.2a Convergent Loop 首轮全量 Review

验证时间：2026-03-25 09:36:44 CST
Story 文件：`_bmad-output/implementation-artifacts/3-2a-convergent-loop-full-review.md`
验证模式：`validate-create-story`
结果：PASS（已应用修正）

## 摘要

该 story 的主方向正确，但原文里有几处会直接把 dev agent 带到错误实现上的合同问题：

1. 它构造了一个当前仓库不接受的 `TransitionEvent.source="convergent_loop"`，而且漏掉了必填的 `submitted_at`。
2. 它示例化了一个不存在的 `ParseVerdict.PARSE_FAILED` 常量，并把 `record_parse_failure()` 写成了错误的 positional 调用方式。
3. 它一边要求在首轮 review 前执行 deterministic validation，一边又承认当前 MVP 没有可验证的结构化 artifact，导致实现边界自相矛盾。
4. 它把 `worktree_path` 做成了可空输入，却没有明确空值时必须如何解析，容易让 review 退化到仓库根目录执行。

这些问题如果不修，最常见的后果是：`TransitionEvent` 在运行时直接校验失败、parse-failure 处理代码无法通过类型/运行时检查、dev 为了“满足 validation gate”硬造一套假 JSON 输入，以及 reviewer 在错误目录里执行审查。

## 已核查证据

- 规划与 story 工件：
  - `_bmad-output/planning-artifacts/epics.md`
  - `_bmad-output/planning-artifacts/architecture.md`
  - `_bmad-output/implementation-artifacts/3-2a-convergent-loop-full-review.md`
  - `_bmad-output/implementation-artifacts/3-1-deterministic-validation-finding-tracking.md`
- 当前代码基线：
  - `src/ato/models/schemas.py`
  - `src/ato/adapters/bmad_adapter.py`
  - `src/ato/transition_queue.py`
  - `src/ato/validation.py`
  - `src/ato/worktree_mgr.py`
  - `src/ato/subprocess_mgr.py`
  - `src/ato/cli.py`
- 本地仓库状态：
  - `src/ato/convergent_loop.py` 仍为空占位
  - `ato status` / `ato logs` 命令尚未落地到当前 CLI

## 发现的关键问题

### 1. TransitionEvent 合同写错

story 原文要求提交：

- `TransitionEvent(..., source="convergent_loop")`

但当前 `TransitionEvent.source` 在 `src/ato/models/schemas.py` 中被限制为：

- `"agent" | "tui" | "cli"`

同时模型还要求必填 `submitted_at: datetime`。原文示例会在真正构造模型时直接失败。

已应用修正：
- 将 story 中的 Convergent Loop 内部事件统一改为 `source="agent"`。
- 在所有事件示例中补上 `submitted_at=datetime.now(UTC)`。
- 测试要求补充为显式校验 `source` 与 `submitted_at`。

### 2. parse-failure 示例代码不可运行

story 原文示例使用了：

- `ParseVerdict.PARSE_FAILED`
- `record_parse_failure(parse_result, story_id, ...)`

但当前仓库中：

- `ParseVerdict` 只是 `Literal["approved", "changes_requested", "parse_failed"]`，不是枚举
- `record_parse_failure()` 使用 keyword-only 参数签名

这会把 dev 引向一段既不符合类型系统、也不符合真实函数签名的代码。

已应用修正：
- 示例改为 `if parse_result.verdict == "parse_failed":`
- `record_parse_failure()` 改为 keyword arguments 调用
- 补充了 `notifier=self._nudge.notify if self._nudge else None` 的正确用法

### 3. Deterministic validation gate 指导自相矛盾

story Task 3 原文要求在 `run_first_review()` 开头调用 `validate_artifact()`，但 Dev Notes 同时说明：

- 当前 MVP 的首轮 code review 直接审查 worktree
- 没有显式 artifact JSON 可供 schema 验证

如果不消解这个冲突，dev 最容易做出的错误实现就是伪造一份“为了过 gate 而存在”的 JSON 输入，或者硬加一条本 story 并不需要的 review-validation task 链路。

已应用修正：
- 明确 validation hook 仅在存在结构化 review artifact payload 时才执行
- 明确当前 MVP 默认安全跳过该 hook
- 明确本 story 不新增 review-validation task，也不要求为此接 `TaskRecord.error_message`
- 若未来 caller 提供 payload 且验证失败，只需提交 `validate_fail` 事件并提前返回

### 4. worktree 空值路径未定义

story 把 `run_first_review(..., worktree_path: str | None)` 做成了可空输入，但没有告诉 dev：

- `None` 时是否允许直接 review 仓库根目录
- 是否必须回查 `stories.worktree_path`

考虑到 Story 2B.4 已把 story 隔离执行明确建模到 `stories.worktree_path`，这里如果不写清楚，review 很容易跑错目录，直接破坏隔离边界。

已应用修正：
- 要求优先使用显式传入的 `worktree_path`
- 若为空，则回查 `stories.worktree_path`
- 若仍为空，则直接失败，不允许退化到 repo root
- 新增对应测试要求

## 已应用增强

- 将 TransitionQueue 交互测试从只校验 `review_pass` / `review_fail` 扩展为也校验 `validate_fail`
- 在 story 中把 “提交 validate_fail 状态” 改成更精确的 “提交 `TransitionEvent(event_name="validate_fail", ...)`”
- 把 review prompt / `cwd` 的约束统一改成基于 resolved worktree path，减少 dev 在 prompt 和工作目录之间做出不一致实现的机会

## 剩余风险

- 当前 story 仍把 Deterministic Validation Gate 保留为“预留位”。如果后续真要在 review 前验证结构化 artifact，需要先明确 artifact 的来源、schema 名称以及失败结果如何在 UI/CLI 中承载。
- `ConvergentLoopResult` 目前只覆盖单轮统计；后续 3.2c/3.2d 若引入跨轮 diff 或 convergence ratio，可能需要扩展结果模型或另建聚合 DTO。

## 最终结论

修正后，Story 3.2a 已达到可交付给 dev agent 的质量门槛。关键实现歧义已被移除，当前版本能够更准确地约束事件模型、parse-failure 合同、validation gate 边界，以及 review 必须运行的 worktree 位置。
