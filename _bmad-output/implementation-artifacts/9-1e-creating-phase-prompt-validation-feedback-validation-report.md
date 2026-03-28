# Story 验证报告：9.1e validate_fail → creating 回退路径 prompt 与验证反馈注入

验证时间：2026-03-28  
Story 文件：`_bmad-output/implementation-artifacts/9-1e-creating-phase-prompt-validation-feedback.md`  
验证模式：`validate-create-story`  
结果：PASS（已应用修正）

## 摘要

原始 9.1e 草稿方向是对的，已经抓住了真正的问题点：`validate_fail` 回退到 `creating` 后，当前 generic retry prompt 既不会触发 `bmad-create-story`，也不会把已持久化的 validation findings 送回 agent。

但原稿仍有 3 个会继续误导 dev 的缺口：

1. 它只把测试落点写在 `tests/unit/test_recovery.py`，却没有把 `core.py::_dispatch_batch_restart()` 这条并行 runtime 路径纳入回归覆盖。
2. 它默认 `validate_fail` 总会有 findings 可注入，而当前代码里存在只提交 `validate_fail`、但不写 findings 的路径。
3. 它还没补齐当前仓库已经形成的 create-story 基线结构，后续 dev-story 会缺少边界、前序情报和变更追溯骨架。

本次验证后，story 已收敛成一个不会把开发者带偏的 corrective spec。

## 已核查证据

- 规划与变更工件：
  - `_bmad-output/planning-artifacts/sprint-change-proposal-2026-03-28.md`
  - `_bmad-output/implementation-artifacts/sprint-status.yaml`
- 当前代码：
  - `src/ato/recovery.py`
  - `src/ato/core.py`
  - `src/ato/convergent_loop.py`
  - `src/ato/models/db.py`
- 当前测试布局：
  - `tests/unit/test_recovery.py`
  - `tests/unit/test_core.py`
- 相关历史先例：
  - `git commit 30d8246`（interactive restart phase-aware prompt 修复）

## 发现的关键问题

### 1. 原稿没有把 `_dispatch_batch_restart()` 的回归覆盖写进 story

当前仓库对 structured_job creating phase 至少有两条运行时入口：

- `RecoveryEngine._dispatch_structured_job()`
- `Orchestrator._dispatch_batch_restart()`

原稿虽然在 AC4 里提到了两条路径都要调用 helper，但 Task 5 仍只要求改 `tests/unit/test_recovery.py`。这会让 dev 很容易只写 helper 单测和 recovery 路径断言，漏掉 core restart 路径。

已应用修正：

- AC5 显式要求 helper 与两条运行时路径都要有测试覆盖
- Tasks 中新增 `tests/unit/test_core.py` 的 creating restart 断言
- Project Structure Notes 将 `test_core.py` 提升为正式测试面

### 2. 原稿把 “validate_fail = 一定有 findings” 说得太绝对

当前代码里至少有两类 `validate_fail` 来源：

- `recovery.py` validating phase 解析出 story_validation findings，并写入 DB
- `convergent_loop.py::_run_validation_gate()` 直接提交 `validate_fail`，但不会写 findings

所以 helper 的正确合同不是“validate_fail 时必定追加反馈”，而是：

- 有当前 unresolved findings → 注入 JSON 反馈
- 没有 findings → 返回 base prompt 原样不变

已应用修正：

- AC3 明确把“无 findings passthrough”扩展为首次创建和未持久化反馈两类场景
- Dev Notes 中补充该运行时事实，避免开发者把 no-findings 当成异常
- Scope Boundary 明确本 story 不负责给无 findings 的 `validate_fail` 新增持久化机制

### 3. 原稿还没达到仓库当前 create-story 基线

与 Epic 9 近期已验证的 story 相比，原稿缺少：

- Scope Boundary
- Suggested Verification
- Previous Story Intelligence
- Change Log
- Dev Agent Record

这类缺口不会立刻让代码实现出错，但会让后续 dev-story 缺少清晰边界和追溯结构。

已应用修正：

- 补回上述所有基线结构
- References 改为基于真实文件/提交的 source 风格
- 删除易漂移的精确行号引用

## 已应用增强

- 把 9.1e 与 Story 9.1、Story 3.1 / 3.2c 和提交 `30d8246` 的上下文关系显式写清
- 将实现重点从“泛泛的 retry prompt 修复”收紧为“phase-aware skill trigger + DB unresolved findings feedback”
- 明确只有 `creating` phase 走新 helper，避免误伤其他 phase prompt 合同

## 剩余风险

- `_bmad-output/planning-artifacts/epics.md` 中仍没有正式的 Epic 9 分解；本次验证主要基于 sprint change proposal、当前代码、sprint-status 与相邻 stories 交叉校正。
- 本次只修订了 story 与 validation report，没有实现 Python 代码，也没有运行测试。

## 最终结论

修正后，9.1e 已经从“问题识别正确，但测试面和无-findings 合同仍然偏窄”的草稿，收敛成了一个可直接交给 dev-story 的 corrective story。最关键的误导已经解除：开发者不会再只修 recovery 路径，也不会把 no-findings 的 `validate_fail` 错当成 helper 失败。
