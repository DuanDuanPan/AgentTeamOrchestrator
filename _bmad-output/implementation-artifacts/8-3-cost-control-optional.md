# Story 8.3: 成本控制改为可选

Status: ready-for-dev

## Story

As a 操作者,
I want 成本控制配置为可选项，不配置时系统正常运行不做成本限制,
so that 早期开发阶段无需关注成本预算配置。

## Acceptance Criteria

1. **AC1: cost 字段可选**
   - Given ato.yaml 中未包含 cost 配置段
   - When 调用 `load_config()` 加载配置
   - Then `ATOSettings.cost` 为 None，配置加载成功

2. **AC2: cost=None 时跳过验证**
   - Given 配置中 cost 为 None
   - When 执行 `_validate_numeric_bounds()` 验证
   - Then 不对 cost 相关字段做数值校验

3. **AC3: 系统运行不受影响**
   - Given cost 配置为 None
   - When Orchestrator 正常运行（dispatch、merge、approval 等流程）
   - Then 所有功能正常，cost_log 正常记录但不做预算检查

4. **AC4: 显式配置仍生效**
   - Given ato.yaml 中包含 cost 配置段（budget_per_story: 5.0）
   - When 调用 `load_config()` 加载配置
   - Then `ATOSettings.cost` 为有效的 CostConfig 实例，验证正常执行

5. **AC5: ato.yaml.example 更新**
   - Given 更新后的 ato.yaml.example 模板
   - When 用户查看模板
   - Then cost 配置段被注释掉，注释说明为可选

## Tasks / Subtasks

- [ ] Task 1: CostConfig 改为可选 (AC: #1)
  - [ ] 1.1 `src/ato/config.py` 中 `ATOSettings.cost: CostConfig = CostConfig()` 改为 `cost: CostConfig | None = None`

- [ ] Task 2: 验证逻辑适配 (AC: #2)
  - [ ] 2.1 `src/ato/config.py` `_validate_numeric_bounds()` 中 cost 校验块加 `if config.cost is not None` 守卫

- [ ] Task 3: 运行时代码适配 (AC: #3)
  - [ ] 3.1 `src/ato/core.py` merge approval payload 中 cost_usd 查询逻辑：cost=None 时仍查询 cost_log 记录总额（记录不受影响），但不做预算超限判断
  - [ ] 3.2 全局搜索 `settings.cost` 或 `config.cost` 引用，确保所有访问点有 None 守卫

- [ ] Task 4: 更新 ato.yaml.example (AC: #5)
  - [ ] 4.1 cost 配置段注释掉，标注"可选，不配置时不做成本限制"

- [ ] Task 5: 更新测试 (AC: #1-#4)
  - [ ] 5.1 新增测试：cost=None 时 load_config 成功
  - [ ] 5.2 新增测试：cost=None 时 _validate_numeric_bounds 不报错
  - [ ] 5.3 修复现有测试中依赖 cost 必填的断言

## Dev Notes

- 当前 `core.py:999-1005` 在 merge approval payload 中查询 cost_log 汇总，与 CostConfig 无直接关系（cost_log 是 per-task 记录），cost=None 只影响预算上限判断
- `subprocess_mgr.py` 中 cost_log 记录逻辑不受影响（始终记录 token 和成本）
- 影响范围小，主要在 config.py 验证层

### Project Structure Notes

- 改动集中在 config.py 的类型声明和验证函数，波及面最小

### References

- [Source: src/ato/config.py#CostConfig] CostConfig 定义
- [Source: src/ato/config.py#_validate_numeric_bounds] 数值边界验证
- [Source: src/ato/core.py:999-1005] merge approval cost 查询
