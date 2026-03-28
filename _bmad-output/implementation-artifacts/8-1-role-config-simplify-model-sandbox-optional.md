# Story 8.1: 角色配置简化 — model/sandbox 改为可选

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a 操作者,
I want 角色配置中的 `model` 和 `sandbox` 字段为可选，由 CLI 工具自身决定默认行为,
so that 配置更简洁，且不绑定特定模型版本或重复声明 CLI 已有默认值。

## Acceptance Criteria

1. **AC1: `model` 字段可选**
   - **Given** `ato.yaml` 中某角色未指定 `model`
   - **When** 调用 `load_config()` 和 `build_phase_definitions()`
   - **Then** `RoleConfig.model` 为 `None`
   - **And** `PhaseDefinition.model` 为 `None`
   - **And** 配置加载成功，不因缺少 `model` 失败

2. **AC2: `sandbox` 字段可选且无硬编码默认**
   - **Given** `ato.yaml` 中某 Codex 角色未指定 `sandbox`
   - **When** 通过普通 Codex dispatch、Convergent Loop review/re-review、以及 crash recovery / restart 路径执行任务
   - **Then** 系统不在任何中间层硬编码补入 `sandbox`
   - **And** 最终传给 `codex exec` 的命令不包含 `--sandbox`
   - **And** Codex CLI 自身决定默认沙箱行为

3. **AC3: 显式指定仍生效**
   - **Given** `ato.yaml` 中角色显式指定了 `model: opus` 和 `sandbox: read-only`
   - **When** 走普通 dispatch、Convergent Loop、recovery re-dispatch 或 restart 路径构建 CLI 调用
   - **Then** 这些值被完整透传
   - **And** 最终命令包含 `--model opus` 与 `--sandbox read-only`

4. **AC4: `model_map` 覆盖仍生效**
   - **Given** `ato.yaml` 中 `model_map` 为某阶段指定了模型
   - **When** 构建该阶段的 `PhaseDefinition` 并下游执行 dispatch
   - **Then** `model_map` 的值优先于角色默认值（含 `None`）
   - **And** 下游收到的 `model` 与该覆盖值一致

5. **AC5: Codex 成本计算在 `model=None` 时安全降级**
   - **Given** 某次 Codex 调用未显式指定 `model`
   - **When** 适配器构建 `CodexOutput` 并计算成本
   - **Then** `model_name` 为 `None`
   - **And** `calculate_cost()` 返回 `0.0` 且记录 warning
   - **And** 不抛异常、不阻断 task 完成或 cost_log 写入

6. **AC6: `ato.yaml.example` 更新**
   - **Given** 更新后的 `ato.yaml.example`
   - **When** 用户查看模板
   - **Then** 角色配置示例默认不再包含 `model` / `sandbox`
   - **And** 注释明确说明这两个字段是可选项，仅在需要覆盖 CLI 默认行为时填写

## Tasks / Subtasks

- [ ] Task 1: 调整配置模型的可选性合同 (AC: #1, #4)
  - [ ] 1.1 在 `src/ato/config.py` 中将 `RoleConfig.model: str` 改为 `model: str | None = None`
  - [ ] 1.2 将 `PhaseDefinition.model: str` 改为 `model: str | None`
  - [ ] 1.3 `build_phase_definitions()` 保持 `model_map.get(phase.name, role_config.model)` 逻辑，但允许 fallback 为 `None`
  - [ ] 1.4 保持 `RoleConfig.sandbox` 的现有枚举约束不变；本 Story 只改变“可省略”，不引入新 sandbox 值

- [ ] Task 2: 移除 Codex adapter 的硬编码默认 (AC: #2, #3, #5)
  - [ ] 2.1 更新 `src/ato/adapters/codex_cli.py` 模块说明，删除“reviewer 默认 read-only”这类已过时表述
  - [ ] 2.2 `_build_command()` 中仅当 `options["sandbox"]` 非 `None` 时才追加 `--sandbox`
  - [ ] 2.3 `_build_command()` 中仅当 `options["model"]` 非 `None` 时才追加 `--model`
  - [ ] 2.4 `execute()` 中 `model_name` 改为 `opts.get("model")`，不再默认 `codex-mini-latest`
  - [ ] 2.5 `calculate_cost()` 签名改为接受 `str | None`；`None` 或未知模型都返回 `0.0` 并记录 warning

- [ ] Task 3: 对齐所有运行时 dispatch 路径，禁止在中间层补默认值 (AC: #2, #3, #4)
  - [ ] 3.1 `src/ato/convergent_loop.py` 首轮 review / scoped re-review 不能继续写死 `sandbox=\"read-only\"`
  - [ ] 3.2 若 Convergent Loop 需要显式 reviewer 选项，必须从调用方/phase config 注入，而不是在方法体内写字面量默认
  - [ ] 3.3 `src/ato/recovery.py::_build_dispatch_options()` 删除对 Codex 的 `workspace-write` fallback；只有 phase config 明确提供时才传 `sandbox`
  - [ ] 3.4 `src/ato/recovery.py::_dispatch_convergent_loop()` 删除 `phase_cfg.get(\"sandbox\", \"read-only\")` 这类默认值回填
  - [ ] 3.5 `src/ato/core.py::_dispatch_batch_restart()` 不能只传 `cwd`；需复用 phase-derived `model` / `sandbox` 选项，确保 restart 路径也满足 AC3 / AC4

- [ ] Task 4: 更新配置模板与注释 (AC: #6)
  - [ ] 4.1 `ato.yaml.example` 中 roles 示例默认去掉 `model` / `sandbox`
  - [ ] 4.2 注释说明：若要固定模型或覆盖 Codex 默认沙箱，可按角色显式填写这两个字段
  - [ ] 4.3 保留 `model_map` 示例段，继续展示阶段级模型覆盖能力

- [ ] Task 5: 更新配置与模板测试 (AC: #1, #4, #6)
  - [ ] 5.1 `tests/unit/test_config.py` 新增“角色缺少 `model` 仍可加载”与“`PhaseDefinition.model is None`”断言
  - [ ] 5.2 `tests/unit/test_config.py` 中与 `ato.yaml.example` 强绑定的断言要改为匹配新模板，不再要求示例里必须出现 `read-only sandbox`
  - [ ] 5.3 `tests/integration/test_config_workflow.py` 端到端用例改为验证“模板仍可加载 + 生命周期阶段不变”，而不是依赖示例 reviewer 必带 `sandbox`
  - [ ] 5.4 保留现有显式 `sandbox` / `model_map` 测试，确保 AC3 / AC4 不回退

- [ ] Task 6: 更新 Codex adapter 测试 (AC: #2, #3, #5)
  - [ ] 6.1 `tests/unit/test_codex_adapter.py` 中“basic/default command 自带 `--sandbox read-only`”的断言改为“不应默认包含 `--sandbox`”
  - [ ] 6.2 新增测试：显式传入 `sandbox` 时仍追加 `--sandbox`
  - [ ] 6.3 保留“无 model 参数时不加 `--model`”测试，并补充 `execute()` 在无 model 时返回 `model_name is None`
  - [ ] 6.4 更新成功执行测试：默认无 model 时 `cost_usd == 0.0`，显式传 model 的场景再验证成本计算为正值
  - [ ] 6.5 新增 `calculate_cost(None, ...)` 的降级测试

- [ ] Task 7: 更新 recovery / restart / review 路径测试 (AC: #2, #3)
  - [ ] 7.1 `tests/unit/test_recovery.py` 中“structured_job dispatch 默认传 `sandbox=workspace-write`”的断言需改为：未显式配置时不应传 `sandbox`
  - [ ] 7.2 为 recovery re-dispatch 新增显式 phase config 场景：若 phase 定义了 `sandbox` / `model`，options 必须透传
  - [ ] 7.3 `tests/unit/test_core.py` 新增/更新 restart 场景，验证 `_dispatch_batch_restart()` 会带上 phase-derived `model` / `sandbox`
  - [ ] 7.4 如为 Convergent Loop 增加 reviewer options 注入点，补对应单测，确保 review / re-review 不再依赖硬编码默认

## Dev Notes

### 核心校验结论

- **这不是只改 `config.py` 和 `codex_cli.py` 的小补丁。** 当前仓库至少还有 `convergent_loop.py` 和 `recovery.py` 两条路径在硬编码 `read-only` 或 `workspace-write`，如果不一起改，AC2 会在 review / recovery 场景失效。
- **显式配置仍生效意味着 restart / re-dispatch 也要对齐。** 当前 `_dispatch_batch_restart()` 只传 `cwd`，会让显式 `model` / `sandbox` 在重启路径失效。
- **`RoleConfig.sandbox` 已经是可选。** 本 Story 的核心是补齐 `model` 可选 + 删除中间层默认值，而不是重新设计 sandbox schema。

### 产品与架构约束

- **PRD 的配置示例已经允许 reviewer 角色不写 `model`。** 当前代码把 `RoleConfig.model` 设成必填，和 PRD 示例存在偏差；本 Story 是把实现收敛回产品合同，而不是新增一个全新能力。
- **Codex `read-only` 是 CLI 默认值。** 根据技术调研，`--sandbox read-only` 不需要显式传入；继续在代码里硬编码，只会让配置层看起来“可省略”，但运行时实际上仍被框死。
- **CLI adapter 隔离（NFR11）不能被破坏。** 默认值应由 adapter/CLI 边界统一处理，编排层不要在多个文件里散落重复默认值。

### 需要复用的现有组件

| 组件 | 文件 | 用途 |
|------|------|------|
| `RoleConfig` / `PhaseDefinition` | `src/ato/config.py` | 角色配置与阶段定义 |
| `build_phase_definitions()` | `src/ato/config.py` | phase → role/model/sandbox 合并入口 |
| `CodexAdapter._build_command()` | `src/ato/adapters/codex_cli.py` | `codex exec` 命令组装 |
| `calculate_cost()` | `src/ato/adapters/codex_cli.py` | Codex token → USD 计算 |
| `ConvergentLoop.run_first_review()` / `run_rereview()` | `src/ato/convergent_loop.py` | review / re-review dispatch |
| `RecoveryEngine._build_dispatch_options()` | `src/ato/recovery.py` | recovery 路径 options 拼装 |
| `Orchestrator._dispatch_batch_restart()` | `src/ato/core.py` | restart 后的 structured job re-dispatch |

### 实现护栏

- **不要把 `None` 强行转换成 `"codex-mini-latest"` 或 `"read-only"`。** 这会直接破坏 Story 目标。
- **不要改动 `model_map` 的 schema。** 它仍然是阶段级显式覆盖，只是 fallback 可以变成 `None`。
- **不要扩展新的 sandbox 枚举值。** 继续沿用 `read-only | workspace-write`，只是允许角色配置省略。
- **`calculate_cost()` 的降级必须是“安全且可观测”。** 返回 `0.0` 的同时保留 warning，方便后续发现未定价或未指定模型的调用。
- **优先抽共享 helper，而不是在 `core.py` / `recovery.py` / `convergent_loop.py` 三处各写一套 options 拼装逻辑。** 否则以后还会再次漂移。

### 测试策略

- **配置层：** 覆盖“`model` 缺失可加载”“显式 `sandbox` 仍透传”“`model_map` 仍优先”。
- **adapter 层：** 覆盖默认无 `--sandbox` / `--model`、显式有 flag、`model=None` 成本降级。
- **runtime 层：** 覆盖 Convergent Loop、recovery、restart 这三条路径不再硬编码默认值。
- **模板层：** 覆盖 `ato.yaml.example` 仍可成功加载，且注释描述与新合同一致。

### Scope Boundary

- **IN:** `config.py`、`codex_cli.py`、`convergent_loop.py`、`recovery.py`、`core.py`、`ato.yaml.example` 与相关测试
- **OUT:** 新增模型价格表条目或重新设计 cost_log schema
- **OUT:** Claude CLI 新增 `--model` 支持；当前 `claude_cli.py` 不消费 model，本 Story 不扩展该合同
- **OUT:** 新的配置文件格式或新的 CLI 工具类型

### Project Structure Notes

**主要修改文件：**
- `src/ato/config.py` — `RoleConfig.model` / `PhaseDefinition.model` 可选化
- `src/ato/adapters/codex_cli.py` — 删除 model/sandbox 硬编码默认
- `src/ato/convergent_loop.py` — review / re-review 不再写死 `sandbox`
- `src/ato/recovery.py` — recovery options 拼装与 convergent re-dispatch 对齐
- `src/ato/core.py` — batch restart 对齐 phase-derived options
- `ato.yaml.example` — 模板角色示例与注释更新

**测试文件：**
- `tests/unit/test_config.py`
- `tests/integration/test_config_workflow.py`
- `tests/unit/test_codex_adapter.py`
- `tests/unit/test_recovery.py`
- `tests/unit/test_core.py`

### References

- [Source: _bmad-output/planning-artifacts/prd.md — 声明式工作流配置示例 / 关键配置项]
- [Source: _bmad-output/planning-artifacts/architecture.md — Technical Constraints & Dependencies / CLI adapter 隔离]
- [Source: _bmad-output/planning-artifacts/research/technical-claude-codex-cli-integration-research-2026-03-24.md — Codex `--sandbox` 默认行为]
- [Source: _bmad-output/implementation-artifacts/1-3-declarative-config-engine.md — `RoleConfig` / `PhaseDefinition` / `model_map` 合同]
- [Source: src/ato/config.py — `RoleConfig`, `PhaseDefinition`, `build_phase_definitions()`]
- [Source: src/ato/adapters/codex_cli.py — `_build_command()`, `execute()`, `calculate_cost()`]
- [Source: src/ato/convergent_loop.py — 首轮 review / scoped re-review dispatch]
- [Source: src/ato/recovery.py — `_build_dispatch_options()`, `_dispatch_convergent_loop()`]
- [Source: src/ato/core.py — `_dispatch_batch_restart()`]

### Previous Story Intelligence (from 1.3 + 2b.2)

1. **Story 1.3 已把配置访问路径集中在 `load_config()` / `build_phase_definitions()`。** 本 Story 应延续这一入口，不要再在运行时拼裸 YAML dict。
2. **Story 2b.2 已把 Codex sandbox 作为角色配置的一部分引入。** 这次不是移除 sandbox 能力，而是把“是否显式传给 CLI”交还给配置和 CLI 默认值。
3. **已有测试大量使用真实 `ato.yaml.example` 与 adapter 命令断言。** 这次变更最容易漏掉的不是业务逻辑，而是测试基线和模板断言。

## Change Log

- 2026-03-28: `validate-create-story` 修订 —— 将 Story 范围从“只改 config + codex adapter”扩大到真实会受影响的 runtime 路径（Convergent Loop / recovery / restart）；补齐 `calculate_cost(model=None)` 合同；明确 `ato.yaml.example` 与现有测试基线的联动修改；补回模板 validation note、Change Log 与 Dev Agent Record 结构

## Dev Agent Record

### Agent Model Used

TBD

### Debug Log References

### Completion Notes List

### File List
