# Story 8.3: 成本控制改为可选

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a 操作者,
I want `ato.yaml` 中的 `cost` 配置段变为可选，不配置时系统仍可正常加载与运行,
so that 早期开发阶段无需先配置预算上限，也不会因为缺少 cost 配置阻断主流程。

## Acceptance Criteria

1. **AC1: `cost` 配置段可省略**
   - Given `ato.yaml` 中完全没有 `cost` 配置段
   - When 调用 `load_config()` 加载配置
   - Then `ATOSettings.cost` 为 `None`
   - And 配置加载成功

2. **AC2: `cost=None` 时跳过 cost 数值校验**
   - Given `ATOSettings.cost is None`
   - When 执行 `_validate_numeric_bounds()`
   - Then 不访问 `cost.budget_per_story` / `cost.blocking_threshold`
   - And 其他数值字段仍按现有规则继续校验
   - And 当 `cost` 显式存在时，非法值仍按现有规则报错

3. **AC3: `cost=None` 下运行时保持兼容**
   - Given `settings.cost is None`
   - When 崩溃恢复或 Convergent Loop 路径需要 blocking 阈值
   - Then 使用兼容 fallback 阈值 `10`
   - And `blocking_abnormal` approval 流程仍可正常工作
   - And `cost_log` 继续照常记录与聚合
   - And 不新增任何 runtime `budget_per_story` 强制拦截逻辑

4. **AC4: 显式 `cost` 配置仍生效**
   - Given `ato.yaml` 中显式配置：
     ```yaml
     cost:
       budget_per_story: 5.0
       blocking_threshold: 7
     ```
   - When 调用 `load_config()` 并进入恢复路径
   - Then `ATOSettings.cost` 为有效的 `CostConfig`
   - And `budget_per_story` / `blocking_threshold` 按显式值生效
   - And 恢复路径向 `maybe_create_blocking_abnormal_approval()` 传递 `7`

5. **AC5: `ato.yaml.example` 明确 cost 为可选**
   - Given 更新后的 `ato.yaml.example`
   - When 用户查看模板
   - Then `cost` 配置段默认以注释形式保留
   - And 注释明确说明“不配置时不做预算上限控制；如需自定义预算或 blocking 阈值，再取消注释”

## Tasks / Subtasks

- [x] Task 1: 配置模型与校验改造 (AC: #1, #2, #4)
  - [x] 1.1 `src/ato/config.py` 中 `ATOSettings.cost: CostConfig = CostConfig()` 改为 `cost: CostConfig | None = None`
  - [x] 1.2 保持 `CostConfig` 模型本身不变；显式 `cost` 配置仍沿用现有字段与默认值
  - [x] 1.3 `src/ato/config.py::_validate_numeric_bounds()` 中对 cost 相关校验增加 `if config.cost is not None` 守卫

- [x] Task 2: 恢复路径兼容 `cost=None` (AC: #3, #4)
  - [x] 2.1 `src/ato/recovery.py` 中所有 `self._settings.cost.blocking_threshold` 读取点改为统一 helper 或显式守卫
  - [x] 2.2 当 `settings is None` 或 `settings.cost is None` 时，blocking 阈值 fallback 为 `10`
  - [x] 2.3 当 `settings.cost` 显式存在时，继续把配置中的 `blocking_threshold` 传给 `ConvergentLoop(...)` 与 `maybe_create_blocking_abnormal_approval(...)`
  - [x] 2.4 不修改 `src/ato/core.py` 的 merge approval `cost_usd` 聚合逻辑；它继续仅基于 `cost_log` 数据工作

- [x] Task 3: 更新配置模板 (AC: #5)
  - [x] 3.1 `ato.yaml.example` 中将 `cost` 配置段整体注释掉
  - [x] 3.2 注释说明”不配置时不做预算上限控制；如需自定义预算或 blocking 阈值，再取消注释”
  - [x] 3.3 保留模板可读性与拷贝即用属性，不覆盖 Epic 8 其他 story 对同一模板文件的改动

- [x] Task 4: 更新测试 (AC: #1-#4)
  - [x] 4.1 `tests/unit/test_config.py` 增加/更新用例：缺少 `cost` 配置段时 `load_config()` 成功且 `config.cost is None`
  - [x] 4.2 `tests/unit/test_config.py` 增加/更新用例：`cost: null` 与完全省略 `cost` 行为一致
  - [x] 4.3 `tests/unit/test_config.py` 保留显式 `cost` 配置的正向断言，以及非法 `budget_per_story` / `blocking_threshold` 的失败断言
  - [x] 4.4 `tests/unit/test_recovery.py` 增加用例：`settings.cost is None` 时恢复路径使用 fallback `10`
  - [x] 4.5 `tests/unit/test_recovery.py` 增加用例：显式 `blocking_threshold` 时恢复路径传递配置值

## Dev Notes

### 当前代码事实

- 当前仓库里 `budget_per_story` 只出现在 `src/ato/config.py` 的配置模型与校验中，没有现成的 runtime 预算拦截逻辑。
- `src/ato/core.py` 当前与 cost 相关的行为只有从 `cost_log` 聚合 `cost_usd` 写入 merge approval payload，并不会读取 `config.cost.budget_per_story`。
- 当前真正会因 `cost` 变为可选而受影响的 runtime 读取点是 `src/ato/recovery.py` 中两处 `self._settings.cost.blocking_threshold` 访问。
- `src/ato/validation.py::maybe_create_blocking_abnormal_approval()` 仍然要求显式 `threshold: int`；当 `cost` 缺失时必须由恢复路径提供 fallback `10`，以保持现有 blocking_abnormal 安全门控。
- `src/ato/subprocess_mgr.py`、CLI cost 汇总、TUI cost 展示都基于 `cost_log` 持久化数据，不依赖 `config.cost`，本 Story 不应修改这些路径。

### Previous Story Intelligence (from 8.1 / 8.4 / 8.5)

1. Story 8.1 也会修改 `src/ato/config.py`，把角色配置中的字段改为可选；实现 8.3 时必须在同一文件内合并而不是覆盖这些改动。
2. Story 8.4 计划在 `src/ato/config.py` 与 `ato.yaml.example` 中新增回归测试命令配置；实现 8.3 时要保留该 story 的字段和注释空间。
3. Story 8.5 会把 `ato.yaml.example` 作为 `ato init` 的复制源；因此 8.3 对模板的注释必须自解释、可直接复制，不应要求用户阅读额外文档才能理解默认行为。

### Scope Boundary

- **IN**: `cost` 配置段可选、`config.py` 守卫、`recovery.py` fallback、`ato.yaml.example` 注释更新、相关单元测试
- **OUT**: 新增 runtime 预算上限拦截、把 `blocking_threshold` 从 `cost` 段拆出、修改 `cost_log` 持久化/CLI/TUI 成本报表、调整 merge approval payload 结构

### Project Structure Notes

- 预期修改文件：
  - `src/ato/config.py`
  - `src/ato/recovery.py`
  - `ato.yaml.example`
  - `tests/unit/test_config.py`
  - `tests/unit/test_recovery.py`
- 预期不修改文件：
  - `src/ato/core.py`
  - `src/ato/subprocess_mgr.py`
  - `src/ato/tui/*`
  - `src/ato/cli.py`

### Suggested Verification

- `uv run pytest tests/unit/test_config.py tests/unit/test_recovery.py -v`

### References

- [Source: src/ato/config.py — `CostConfig`, `ATOSettings`, `_validate_numeric_bounds()`]
- [Source: src/ato/recovery.py — `RecoveryEngine` 中 blocking threshold 读取点]
- [Source: src/ato/validation.py — `maybe_create_blocking_abnormal_approval()` threshold 契约]
- [Source: src/ato/core.py — merge approval `cost_usd` 聚合仅依赖 `cost_log`]
- [Source: ato.yaml.example — 当前 `cost` 配置模板]
- [Source: tests/unit/test_config.py — 配置加载/模板回归测试]
- [Source: tests/unit/test_recovery.py — 恢复路径回归测试]
- [Source: _bmad-output/implementation-artifacts/8-1-role-config-simplify-model-sandbox-optional.md]
- [Source: _bmad-output/implementation-artifacts/8-4-regression-test-multi-command.md]
- [Source: _bmad-output/implementation-artifacts/8-5-init-auto-generate-config.md]

## Dev Agent Record

### Implementation Plan

- Task 1: `ATOSettings.cost` 类型从 `CostConfig = CostConfig()` 改为 `CostConfig | None = None`；`_validate_numeric_bounds()` 增加 `if config.cost is not None` 守卫，跳过 cost 字段校验
- Task 2: `recovery.py` 中两处 `self._settings.cost.blocking_threshold` 读取点增加双重守卫 `self._settings is not None and self._settings.cost is not None`，fallback 值为 `10`
- Task 3: `ato.yaml.example` 中 `cost` 配置段整体注释掉，新增中文注释说明可选性
- Task 4: `test_config.py` 新增 `TestCostOptional` 类（6 个用例），更新 2 个现有用例以适配 `cost=None` 默认值；`test_recovery.py` 新增 `TestRecoveryCostNoneFallback` 类（2 个用例）

### Completion Notes

- 全部 4 个 Task、17 个 Subtask 已完成
- 新增 8 个测试用例（6 config + 2 recovery），更新 2 个现有用例
- 全量回归测试 1487 passed，0 failed
- ruff check、mypy strict 全部通过
- `core.py`、`subprocess_mgr.py`、TUI、CLI 均未修改，符合 Scope Boundary

### Debug Log

无异常，一次通过

## File List

- `src/ato/config.py` — 修改：`ATOSettings.cost` 类型改为可选，`_validate_numeric_bounds()` 增加守卫
- `src/ato/recovery.py` — 修改：两处 `blocking_threshold` 读取点增加 `settings.cost is not None` 守卫
- `ato.yaml.example` — 修改：`cost` 配置段注释掉，增加可选性说明
- `tests/unit/test_config.py` — 修改：新增 `TestCostOptional` 类（6 用例），更新 2 个现有用例
- `tests/unit/test_recovery.py` — 修改：新增 `TestRecoveryCostNoneFallback` 类（2 用例）

## Change Log

- 2026-03-28: Story 实现完成 — cost 配置段改为可选（`CostConfig | None = None`），recovery.py blocking_threshold fallback 为 10，ato.yaml.example 注释化，8 个新测试 + 2 个更新测试，1487 全量回归通过
- 2026-03-28: `validate-create-story` 修订 —— 将实现目标从不存在的 runtime budget check 收紧到真实受影响的 `config.py` 与 `recovery.py`；补上 `blocking_threshold` 在 `cost=None` 下的 fallback `10` 兼容合同；明确 `core.py` / `cost_log` 路径无需改动；补充 Epic 8 共享文件冲突提示、Scope Boundary、validation note 与回归测试范围
