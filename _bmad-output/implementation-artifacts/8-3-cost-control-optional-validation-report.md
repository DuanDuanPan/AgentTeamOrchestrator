# Story 验证报告：8.3 成本控制改为可选

验证时间：2026-03-28
Story 文件：`_bmad-output/implementation-artifacts/8-3-cost-control-optional.md`
验证模式：`validate-create-story`
结果：PASS（已应用修正）

## 摘要

该 story 的目标方向是对的，但原稿里有 5 个会直接误导开发实现的缺口，已在 story 文件中修正：

1. 它把运行时影响点写成了 `core.py` 的“预算超限判断”，但当前仓库并不存在这条 runtime 预算拦截逻辑；真实受影响点是 `config.py` 的数值校验和 `recovery.py` 中对 `cost.blocking_threshold` 的直接读取。
2. 它没有定义 `cost=None` 时 `blocking_threshold` 的兼容语义；如果按原稿直接把 `ATOSettings.cost` 改成可选，`RecoveryEngine` 会在恢复路径上解引用 `None`。
3. 它把“cost=None 只影响预算上限判断”写成既有事实，但当前代码库根本没有 `budget_per_story` 的 runtime 执行逻辑，这会诱导开发者去补做一个并不存在的功能。
4. 它没有把 `tests/unit/test_recovery.py` 纳入回归面，只提到了 `config.py` 断言修复，漏掉了真正需要验证 fallback `10` 的运行时路径。
5. 它忽略了 Epic 8 邻近 story 对 `src/ato/config.py` 和 `ato.yaml.example` 的共享修改面，容易让后续实现覆盖 8.1 / 8.4 / 8.5 的并行改动。

## 已核查证据

- 规划与规范工件：
  - `_bmad-output/planning-artifacts/prd.md`
- 邻近 story：
  - `_bmad-output/implementation-artifacts/8-1-role-config-simplify-model-sandbox-optional.md`
  - `_bmad-output/implementation-artifacts/8-4-regression-test-multi-command.md`
  - `_bmad-output/implementation-artifacts/8-5-init-auto-generate-config.md`
- 当前代码：
  - `src/ato/config.py`
  - `src/ato/recovery.py`
  - `src/ato/validation.py`
  - `src/ato/core.py`
  - `ato.yaml.example`
  - `tests/unit/test_config.py`
  - `tests/unit/test_recovery.py`

## 发现的关键问题

### 1. 原 story 把真实影响点写错成了不存在的 runtime budget check

原稿把 Task 3.1 写成：

- `src/ato/core.py` merge approval payload 中做 `cost=None` 兼容
- `cost=None` 只影响预算超限判断

但当前仓库事实是：

- `src/ato/core.py` 只会从 `cost_log` 聚合 `cost_usd` 写入 merge approval payload
- 全仓库并没有对 `budget_per_story` 的 runtime 拦截或 budget gate
- 真正会因 `cost` 变成可选而受影响的代码，是 `src/ato/config.py::_validate_numeric_bounds()` 与 `src/ato/recovery.py` 两处 `self._settings.cost.blocking_threshold`

如果不修正，开发者很可能会：

- 去 `core.py` 添加一个新 budget gate，凭空扩张 scope
- 反而漏掉 `recovery.py` 中真实会崩溃的 `None` 解引用

已应用修正：

- 任务收敛到 `src/ato/config.py` 与 `src/ato/recovery.py`
- 明确 `src/ato/core.py` 的 `cost_log` 聚合逻辑无需修改
- Dev Notes 明确当前代码库没有现成的 runtime `budget_per_story` 执行逻辑

### 2. `cost=None` 缺少 `blocking_threshold` fallback 合同，会直接破坏恢复路径

当前 `src/ato/recovery.py` 有两处直接读取：

- `self._settings.cost.blocking_threshold`

而原稿只写：

- `ATOSettings.cost` 改成可选
- `_validate_numeric_bounds()` 跳过校验

这还不够。因为一旦 `cost` 为 `None`，恢复路径在创建 `ConvergentLoop` 和 `blocking_abnormal` approval 时就会直接访问空值。

已应用修正：

- AC3 明确要求当 `settings.cost is None` 时使用 fallback `10`
- Task 2 明确要求统一 helper 或显式守卫处理 `recovery.py` 的所有读取点
- AC4 明确要求显式配置时仍把用户给定的 `blocking_threshold` 透传到运行时

### 3. 原 story 把“预算上限逻辑已存在”写成事实，容易诱导无谓扩 scope

原稿 Dev Notes 里写：

- “cost=None 只影响预算上限判断”

但当前仓库中：

- `budget_per_story` 只存在于 `CostConfig` 和数值校验
- 并没有任何 core / merge / regression 运行时代码去执行该预算上限判断

这类表述会把一个“让配置字段可省略”的小改动，误导成“顺手补 runtime budget enforcement”的大改动。

已应用修正：

- Scope Boundary 明确把“新增 runtime 预算上限拦截”列为 OUT
- Dev Notes 明确 `cost_log`、CLI/TUI 成本展示继续基于持久化数据，不属于本 story

### 4. 测试面原稿不完整，漏掉了最关键的 recovery 回归

原稿测试任务只写了：

- `load_config()` 成功
- `_validate_numeric_bounds()` 不报错
- 修复依赖 cost 必填的断言

但真正高风险的回归点还包括：

- `tests/unit/test_recovery.py` 中 `settings.cost is None` 时 fallback `10`
- 显式配置 `blocking_threshold` 时恢复路径仍传递配置值
- `ato.yaml.example` 注释掉 `cost` 后，模板仍能被 `load_config()` 成功解析

已应用修正：

- Task 4 增加 `tests/unit/test_recovery.py` 两个明确回归方向
- Task 4 保留 `tests/unit/test_config.py` 的显式值正向断言与非法值失败断言
- Story 中补充建议回归命令：`uv run pytest tests/unit/test_config.py tests/unit/test_recovery.py -v`

### 5. 原稿忽略了 Epic 8 的共享文件冲突，容易覆盖邻近 story 的并行改动

当前 Epic 8 中：

- Story 8.1 会修改 `src/ato/config.py`
- Story 8.4 会修改 `src/ato/config.py` 与 `ato.yaml.example`
- Story 8.5 会依赖 `ato.yaml.example` 作为 `ato init` 的复制源

原稿完全没有写这些共享文件边界。开发者如果按单 story 视角实现，很容易在 `config.py` 或模板里覆盖其他已落地修改。

已应用修正：

- 新增 `Previous Story Intelligence (from 8.1 / 8.4 / 8.5)`
- 明确要求在共享文件中合并而不是回退邻近 story 的改动
- `ato.yaml.example` 的注释要求改成“可直接复制、可直接理解”，对齐 8.5 的 init 场景

## 已应用增强

- 补回了 create-story 模板里的 validation note 注释
- 增加了 `Scope Boundary`，防止开发时顺手发明新的 runtime budget gate
- 增加了 `Change Log`，记录本次 validate-create-story 的修订点
- 把 References 从漂移的行号引用收敛为文件 / symbol 级合同

## 剩余风险

- 当前 `blocking_threshold` 仍然逻辑上挂在 `cost` 段下，这在语义上并不完全理想；如果后续产品要把“预算控制”和“质量异常阈值”解耦，应另开 story 做配置结构重整，而不是在 8.3 顺手处理。
- 本次只修订了 story 和验证报告，没有实现 Python 代码，也没有运行测试；目标是先把 `dev-story` 的实现合同收紧到与当前仓库一致。

## 最终结论

修正后，这个 story 已经从“方向正确但技术合同写偏了”收敛成“可以直接交给 dev-story 执行”的状态。高风险误导点已经移除：不会再去 `core.py` 发明不存在的 budget gate，不会再漏掉 `recovery.py` 的 `None` 解引用，也不会再忽略 Epic 8 共享文件上的并行改动。
