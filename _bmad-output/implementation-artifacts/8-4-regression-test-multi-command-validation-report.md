# Story 验证报告：8.4 回归测试支持多命令

验证时间：2026-03-28
Story 文件：`_bmad-output/implementation-artifacts/8-4-regression-test-multi-command.md`
验证模式：`validate-create-story`
结果：PASS（已应用修正）

## 摘要

该 story 的目标方向是对的，但原稿里有 4 个会直接把实现带偏的合同缺口，已在 story 文件中修正：

1. 它只讲“多命令顺序执行”，却没有钉死当前仓库的“单个 regression task / 单个 `regression_task_id`”合同，开发者很容易误解成“每条命令一条 task”甚至去改 DB schema。
2. 它把配置示例写成 `tests/e2e/`，但当前仓库根本没有这个目录；实际测试层级是 `tests/unit/`、`tests/integration/`、`tests/smoke/`、`tests/performance/`。
3. 它把超时策略写成“共享总预算或各自独立都可以”，这对 BMAD story 来说过于开放，足以让不同实现走出不同 runtime 合同。
4. 它没有把现有 `_run_regression_test()` 的关键 guardrail 写成显式验收项，包括 `shlex.split()`、stdout/stderr 摘要链路、以及“优先扩展现有 `test_config.py` / `test_merge_queue.py`”的测试落点。

## 已核查证据

- 规划与上下文工件：
  - `_bmad-output/planning-artifacts/implementation-readiness-report-2026-03-24.md`
  - `_bmad-output/project-context.md`
  - `_bmad-output/implementation-artifacts/4-5-regression-test-merge-integration.md`
  - `_bmad-output/implementation-artifacts/8-3-cost-control-optional.md`
  - `_bmad-output/implementation-artifacts/sprint-status.yaml`
- 当前代码基线：
  - `src/ato/config.py`
  - `src/ato/merge_queue.py`
  - `ato.yaml.example`
- 当前测试布局：
  - `tests/unit/test_config.py`
  - `tests/unit/test_merge_queue.py`
  - `tests/integration/test_config_workflow.py`
  - `tests/integration/test_history_cost_e2e.py`
  - `tests/smoke/`

## 发现的关键问题

### 1. 多命令 story 没有守住现有单 task / 单锚点合同

原稿只写：

- 增加 `regression_test_commands`
- `_run_regression_test()` 循环执行多条命令

但没有明确当前仓库已经存在的硬约束：

- `_dispatch_regression_test()` 只创建 1 条 `phase="regression"` task
- `merge_queue.regression_task_id` 只跟踪 1 个 task
- `check_regression_completion()` 也是围绕这 1 个 task 收敛 pass/fail / approval

如果不写清楚，开发者最容易走偏成：

- 每条命令单独插 1 条 task
- 给 `merge_queue` 新增“命令级 task_id 列表”
- 甚至修改 DB schema 来适配多命令

这些都会直接破坏 Story 4.5 已收敛的 merge queue / recovery 合同。

已应用修正：

- AC3 明确“整个命令链只写回同一条 regression task 记录”
- Task 2.1 明确“不新增 DB migration 或多 task 设计”
- Dev Notes 明确“多命令只是同一 task 内部的顺序步骤，不是 3 条独立任务”

### 2. 配置模板示例指向了不存在的 `tests/e2e/`

原稿 Task 3 的示例是：

- `tests/unit/`
- `tests/integration/`
- `tests/e2e/`

但当前仓库真实测试布局是：

- `tests/unit/`
- `tests/integration/`
- `tests/smoke/`
- `tests/performance/`

仓库里虽然有 `test_history_cost_e2e.py`，但它位于 `tests/integration/`，并不存在单独的 `tests/e2e/` 目录。

如果按原稿实现，最常见后果是：

- `ato.yaml.example` 直接给出一个默认跑不通的示例
- dev 额外发明新的测试目录，脱离项目既有分层

已应用修正：

- AC6 / Task 3 把示例改为 `unit / integration / smoke`
- AC6 / Task 4 显式禁止为本 story 发明 `tests/e2e/` 目录

### 3. 超时策略原稿过于含糊，会让 runtime 合同漂移

原稿 Dev Notes 写的是：

- “共享 `timeout.structured_job` 总预算，或各自独立超时——建议各自独立使用相同超时值”

这对 story 来说不是 guidance，而是歧义：

- 一种实现会给整条命令链 1 个总 stopwatch
- 另一种实现会给每条命令 1 个独立 timeout

两者在长命令链、失败诊断、用户预期上都不同。

已应用修正：

- AC5 明确“每条命令独立使用 `timeout.structured_job`”
- Task 2.4 把这一点写成必须实现的 runner 合同
- Dev Notes 去掉开放式选择，避免 dev 自行拍脑袋

### 4. 关键实现 guardrail 没有上升为验收项

原稿虽然在 Dev Notes 提到了：

- 当前用 `shlex.split(cmd)` 解析命令
- `error_message` 要适配多命令摘要

但没有把这些提升成明确的 AC / Task，也没有把测试落点写实到现有 suite。结果就是开发者很容易：

- 为了“多命令”改成 `shell=True`
- 漏掉带空格 / 引号命令的解析回归
- 另起一套新测试文件，而不是复用已经存在的 `test_config.py` / `test_merge_queue.py`

已应用修正：

- AC4 / AC5 明确写入失败摘要、`shlex.split()` 与独立 timeout 合同
- Task 4 明确优先扩展 `tests/unit/test_config.py` 与 `tests/unit/test_merge_queue.py`
- Dev Notes 补入“沿用现有 stdout/stderr → `error_message` → approval payload 链路”

## 已应用增强

- 补回了 create-story 模板自带的 validation note 注释。
- 把 story 标题与配置示例收敛成当前仓库真实存在的测试分层。
- 为 story 增加了 Dev Notes、预期修改文件、References 和 Change Log，减少 dev-story 阶段的自由发挥空间。

## 剩余风险

- 本次工作只修订了 story 文档和验证报告，没有修改实现代码，也没有运行测试。
- Epic 8 的细分故事当前主要存在于 `sprint-status.yaml` 与 implementation artifacts 中，尚未回填到 `planning-artifacts/epics.md`；如果团队后续把 `epics.md` 当作唯一真源，8.x 的追踪性仍有缺口。

## 最终结论

修正后，Story 8.4 已达到 `ready-for-dev` 的质量门槛。当前版本已经和现有 `ATOSettings`、`MergeQueue._dispatch_regression_test()`、`check_regression_completion()`、`ato.yaml.example` 的真实测试分层，以及仓库现有测试布局对齐，不会再把 dev agent 带向多 task schema 漂移、错误的 `tests/e2e/` 示例或含糊的 timeout 实现。
