# Story 8.1: 角色配置简化 — model/sandbox 改为可选

Status: ready-for-dev

## Story

As a 操作者,
I want 角色配置中的 model 和 sandbox 字段为可选，由 CLI 工具自身决定默认行为,
so that 配置更简洁，且不绑定特定模型版本。

## Acceptance Criteria

1. **AC1: model 字段可选**
   - Given ato.yaml 中角色定义未指定 model 字段
   - When 调用 `load_config()` 加载配置
   - Then `RoleConfig.model` 为 None，配置加载成功
   - And `PhaseDefinition.model` 为 None，不向 CLI 传递 `--model` 参数

2. **AC2: sandbox 字段可选且无硬编码默认**
   - Given ato.yaml 中角色定义未指定 sandbox 字段
   - When 通过 Codex CLI 适配器执行任务
   - Then 不向 codex CLI 传递 `--sandbox` 参数（由 codex 自身决定默认行为）

3. **AC3: 显式指定仍生效**
   - Given ato.yaml 中角色定义显式指定了 `model: opus` 和 `sandbox: read-only`
   - When 构建 CLI 命令
   - Then 命令参数中包含 `--model opus` 和 `--sandbox read-only`

4. **AC4: model_map 覆盖仍生效**
   - Given ato.yaml 中 `model_map` 为某阶段指定了模型
   - When 构建该阶段的 PhaseDefinition
   - Then model_map 中的值优先于角色默认值（含 None）

5. **AC5: ato.yaml.example 更新**
   - Given 更新后的 ato.yaml.example 模板
   - When 用户查看模板
   - Then 角色配置示例中不包含 model 和 sandbox 字段，注释说明为可选

## Tasks / Subtasks

- [ ] Task 1: RoleConfig model 改为可选 (AC: #1)
  - [ ] 1.1 `src/ato/config.py` 中 `RoleConfig.model: str` 改为 `model: str | None = None`
  - [ ] 1.2 `PhaseDefinition.model: str` 改为 `model: str | None`
  - [ ] 1.3 `build_phase_definitions()` 中 `model_map.get()` fallback 到 `role_config.model`（可能为 None），保持 None 透传

- [ ] Task 2: Codex 适配器移除硬编码默认 (AC: #2, #3)
  - [ ] 2.1 `src/ato/adapters/codex_cli.py` `_build_command()` 中 `sandbox = options.get("sandbox")` 改为无默认值；仅当 sandbox 非 None 时追加 `--sandbox` 参数
  - [ ] 2.2 `model_name` 从 `opts.get("model", "codex-mini-latest")` 改为 `opts.get("model")`；仅当 model_name 非 None 时追加 `--model` 参数
  - [ ] 2.3 `calculate_cost()` 处理 model 为 None 时返回 0.0 并 warn

- [ ] Task 3: Claude 适配器适配 (AC: #3)
  - [ ] 3.1 `src/ato/adapters/claude_cli.py` 确认 `_build_command()` 不传 model 参数（当前已不传，仅确认）

- [ ] Task 4: 更新 ato.yaml.example (AC: #5)
  - [ ] 4.1 角色定义去掉 model 和 sandbox 字段
  - [ ] 4.2 添加注释说明 model 和 sandbox 为可选字段及其含义

- [ ] Task 5: 更新测试 (AC: #1-#4)
  - [ ] 5.1 修复现有测试中依赖 model 必填的断言
  - [ ] 5.2 新增测试：model=None 时 PhaseDefinition 正确构建
  - [ ] 5.3 新增测试：sandbox=None 时 codex 命令不含 --sandbox 参数
  - [ ] 5.4 新增测试：显式指定 model/sandbox 时命令参数正确

## Dev Notes

- `codex_cli.py:178` 当前硬编码 `sandbox="read-only"`，是本次改动的关键点
- `codex_cli.py:203` 硬编码 `model_name="codex-mini-latest"`，用于成本计算，改为可选后需处理 None
- `claude_cli.py` 当前不传 model 参数，无需改动命令构建逻辑
- `subprocess_mgr.py` 不直接处理 model/sandbox，通过 options dict 传递，无需改动

### Project Structure Notes

- 改动集中在配置层（config.py）和适配器层（adapters/），不影响状态机和编排核心

### References

- [Source: src/ato/config.py#RoleConfig] RoleConfig 定义
- [Source: src/ato/config.py#build_phase_definitions] 阶段定义生成
- [Source: src/ato/adapters/codex_cli.py#_build_command] Codex 命令构建
- [Source: src/ato/adapters/claude_cli.py#_build_command] Claude 命令构建
