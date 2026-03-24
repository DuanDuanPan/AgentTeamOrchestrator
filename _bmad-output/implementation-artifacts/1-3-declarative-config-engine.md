# Story 1.3: 声明式配置引擎

Status: ready-for-dev

## Story

As a 操作者,
I want 通过 ato.yaml 声明式定义工作流（角色、阶段、转换规则、阈值参数）,
so that 系统行为可通过配置文件定制而非修改代码。

## Acceptance Criteria

1. **AC1: 配置加载与验证**
   - Given 项目根目录存在 `ato.yaml`
   - When 调用 `load_config(config_path)` 函数
   - Then 返回经 Pydantic Settings 验证的 `ATOSettings` 配置对象，包含角色定义、阶段序列、转换规则、超时阈值、并发上限、Convergent Loop 参数、成本上限
   - And 配置解析耗时 ≤3 秒（NFR5）

2. **AC2: 无效配置拒绝**
   - Given ato.yaml 包含无效定义（如循环依赖的阶段转换、缺失的必填字段、引用不存在的角色）
   - When 调用 `load_config()`
   - Then 抛出 `ConfigError`，错误信息明确指出无效位置和原因
   - And 系统拒绝启动

3. **AC3: 阶段定义生成**
   - Given 配置已加载
   - When 调用 `build_phase_definitions(config)` 函数
   - Then 返回阶段定义列表（含名称、角色、类型、转换规则），可供后续 Epic 2A 的状态机构建器消费
   - And 不包含 StoryLifecycle 状态机类的实例化（留给 Epic 2A）

4. **AC4: 配置缺失引导**
   - Given 项目中不存在 `ato.yaml`
   - When 调用 `load_config()` 函数
   - Then 抛出 `ConfigError`，错误信息提示用户从 `ato.yaml.example` 复制配置文件

5. **AC5: 配置模板**
   - Given `ato.yaml.example` 模板文件
   - When 用户查看模板
   - Then 包含所有配置项的说明注释和合理默认值

## Tasks / Subtasks

- [ ] Task 0: 安装配置依赖 (AC: 前提)
  - [ ] 0.1 执行 `uv add "pydantic-settings[yaml]"` 安装依赖并更新 `pyproject.toml` / `uv.lock`
  - [ ] 0.2 统一通过 `pydantic-settings` 的 YAML source 读取配置，不新增手写 `import yaml` 解析路径

- [ ] Task 1: 定义外部 YAML schema 对应的 Pydantic 模型 (AC: #1, #5)
  - [ ] 1.1 在 `src/ato/config.py` 中定义嵌套配置模型（继承 `BaseModel`，主配置类继承 `BaseSettings`）
  - [ ] 1.2 `RoleConfig` 保持 PRD 示例中的外部键名：`cli: Literal["claude", "codex"]`、`model: str`、`sandbox: Literal["read-only", "workspace-write"] | None = None`
  - [ ] 1.3 `PhaseConfig` 至少包含：`name: str`、`role: str`、`type: Literal["structured_job", "convergent_loop", "interactive_session"]`、`next_on_success: str`、`next_on_failure: str | None = None`
  - [ ] 1.4 定义 `ConvergentLoopConfig`：`max_rounds: int = 3`、`convergence_threshold: float = 0.5`
  - [ ] 1.5 定义 `TimeoutConfig`：`structured_job: int = 1800`、`interactive_session: int = 7200`
  - [ ] 1.6 定义 `CostConfig`：`budget_per_story: float = 5.0`、`blocking_threshold: int = 10`
  - [ ] 1.7 定义 `ATOSettings(BaseSettings)`，字段至少包含：
    - `roles: dict[str, RoleConfig]`
    - `phases: list[PhaseConfig]`
    - `max_concurrent_agents: int = 4`
    - `convergent_loop: ConvergentLoopConfig`
    - `timeout: TimeoutConfig`
    - `cost: CostConfig`
    - `model_map: dict[str, str] = {}`
  - [ ] 1.8 配置 `model_config = SettingsConfigDict(yaml_file="ato.yaml", yaml_file_encoding="utf-8")`
  - [ ] 1.9 实现 `settings_customise_sources`，明确以 init 参数 + YAML 文件为主配置来源；MVP 不依赖隐式 env/dotenv override

- [ ] Task 2: 实现配置加载函数 (AC: #1, #2, #4)
  - [ ] 2.1 实现 `def load_config(config_path: Path) -> ATOSettings`
  - [ ] 2.2 检查 `config_path` 是否存在；不存在时抛出 `ConfigError`，消息包含从 `ato.yaml.example` 复制的指引
  - [ ] 2.3 通过 `YamlConfigSettingsSource(settings_cls, yaml_file=config_path, yaml_file_encoding="utf-8")` 绑定显式路径；不要依赖不存在的 `_yaml_file` init 参数
  - [ ] 2.4 捕获 `ValidationError` 并转换为 `ConfigError`，保留定位信息
  - [ ] 2.5 加载成功后调用 `_validate_config(settings)` 做领域级校验
  - [ ] 2.6 保持 `load_config()` 无模块级缓存，避免 FR51 之外的隐式热更新行为

- [ ] Task 3: 实现领域级配置验证 (AC: #2)
  - [ ] 3.1 实现 `def _validate_config(config: ATOSettings) -> None`，验证失败时抛出 `ConfigError`
  - [ ] 3.2 验证阶段角色引用：每个 `PhaseConfig.role` 必须存在于 `config.roles`
  - [ ] 3.3 验证阶段转换引用：每个 `next_on_success` 和 `next_on_failure`（非 `None` 时）必须引用已定义 phase 或特殊值 `"done"`
  - [ ] 3.4 验证 `phases` 非空，且 phase 名唯一
  - [ ] 3.5 用图遍历验证不存在“只有回环、没有出口”的配置：从入口 phase 与所有 failure 跳转目标出发，最终都必须存在通向 `"done"` 的路径；允许 review/fix 这类受控回环
  - [ ] 3.6 验证数值边界：`max_rounds >= 1`、`0 <= convergence_threshold <= 1`、`max_concurrent_agents >= 1`、所有 timeout > 0、`budget_per_story > 0`、`blocking_threshold >= 0`
  - [ ] 3.7 验证 `model_map` 的 key 必须引用已定义 phase 名

- [ ] Task 4: 实现阶段定义生成 (AC: #3)
  - [ ] 4.1 定义 `PhaseDefinition` 数据类（放 `config.py`，非 Pydantic model）：`name`、`role`、`cli_tool`、`model`、`sandbox`、`phase_type`、`next_on_success`、`next_on_failure`、`timeout_seconds`
  - [ ] 4.2 实现 `def build_phase_definitions(config: ATOSettings) -> list[PhaseDefinition]`
  - [ ] 4.3 将 `PhaseConfig` + 对应 `RoleConfig` 合并为 `PhaseDefinition`
  - [ ] 4.4 `model` 解析规则：`model_map[phase.name]` 优先，否则回退到 `roles[phase.role].model`
  - [ ] 4.5 `timeout_seconds` 由 `phase.type` 映射到 `timeout.structured_job` / `timeout.interactive_session`；`convergent_loop` 视为非交互阶段
  - [ ] 4.6 保持阶段顺序与 `config.phases` 一致，不实例化状态机类

- [ ] Task 5: 创建 `ato.yaml.example` 模板 (AC: #5)
  - [ ] 5.1 在项目根目录创建 `ato.yaml.example`，包含完整配置示例和中文注释
  - [ ] 5.2 外部 YAML 键名与 PRD 示例一致：`roles.*.cli`、`phases[].type`、`max_concurrent_agents`、`convergent_loop`、`timeout`、`cost`、`model_map`
  - [ ] 5.3 roles 部分至少包含 `creator`、`validator`、`developer`、`reviewer`、`fixer`、`qa` 六个角色，并为只读 Codex 角色示例化 `sandbox: read-only`
  - [ ] 5.4 phases 部分包含完整 story 生命周期阶段序列：`creating → validating → dev_ready → developing → reviewing → fixing → review_passed → qa → uat → merging → done`
  - [ ] 5.5 模板显式说明配置变更需重启系统生效（FR51）

- [ ] Task 6: 更新模块导出 (AC: #1)
  - [ ] 6.1 在 `src/ato/config.py` 顶部定义 `__all__`
  - [ ] 6.2 导出 `ATOSettings`、`RoleConfig`、`PhaseConfig`、`ConvergentLoopConfig`、`TimeoutConfig`、`CostConfig`、`PhaseDefinition`、`load_config`、`build_phase_definitions`

- [ ] Task 7: 编写单元测试 (AC: #1, #2, #3, #4, #5)
  - [ ] 7.1 创建 `tests/unit/test_config.py`
  - [ ] 7.2 测试有效配置加载：从 `ato.yaml.example` 复制有效配置到 `tmp_path`，调用 `load_config()` 成功返回 `ATOSettings`
  - [ ] 7.3 测试配置缺失：`config_path` 不存在时抛出 `ConfigError`，消息包含 `ato.yaml.example`
  - [ ] 7.4 测试必填字段缺失：YAML 缺少 `roles` 或 `phases` 时抛出 `ConfigError`
  - [ ] 7.5 测试角色 / phase / `model_map` 引用错误时抛出 `ConfigError`
  - [ ] 7.6 测试死循环或无出口配置时抛出 `ConfigError`
  - [ ] 7.7 测试数值边界：`max_rounds=0`、`convergence_threshold=1.5`、`max_concurrent_agents=0`、非正 timeout / budget 时抛出 `ConfigError`
  - [ ] 7.8 测试 `build_phase_definitions()` 正确解析 `cli_tool`、`model`、`sandbox`、`timeout_seconds`
  - [ ] 7.9 测试显式路径加载：`tmp_path/custom.yaml` 也可被 `load_config()` 读取，覆盖 story 原稿中错误的 `_yaml_file` 假设
  - [ ] 7.10 测试配置解析性能：加载合理大小配置无明显性能退化

- [ ] Task 8: 质量验证 (AC: 全部)
  - [ ] 8.1 执行 `uv run ruff check src/ato/config.py tests/unit/test_config.py`
  - [ ] 8.2 执行 `uv run mypy src/ato/config.py tests/unit/test_config.py`
  - [ ] 8.3 执行 `uv run pytest tests/unit/test_config.py -v`
  - [ ] 8.4 执行 `uv run pytest`
  - [ ] 8.5 执行 `uv run pre-commit run --all-files`

## Dev Notes

### 关键校验结论

- **必须保持外部 YAML schema 与 PRD 示例一致。** 原稿中的 `cli_tool`、`phase_type`、`timeout_seconds`、`story_budget_usd` 等键名会让 `ato.yaml.example` 偏离对外契约。
- **`BaseSettings` 没有 `_yaml_file` init 参数。** 显式路径加载必须通过 `YamlConfigSettingsSource(..., yaml_file=config_path)` 或等价 source 绑定实现。
- **本 story 只交付配置解析与 phase definition 生成。** 状态机实例化、TransitionQueue、Convergent Loop 执行协议仍留给后续 epic。

### 关键架构约束

- **Decision 3：配置决定“做什么”，引擎决定“怎么做”** — 配置仅定义角色 / 阶段 / 转换 / 阈值；CL 内部协议、崩溃恢复流程、TransitionQueue 串行化等留在引擎内
- **FR51：配置变更需重启系统生效（MVP）** — 不实现运行时热更新
- **NFR5：配置解析与状态机构建 ≤3 秒** — 本 story 负责配置解析部分
- **模块依赖方向：** `config.py` 可以依赖 `models/schemas.py`（使用 `ConfigError`），不要反向依赖 `core.py` / `state_machine.py`
- **配置访问模式：** 通过 `ATOSettings` / `PhaseDefinition` 传递，不在模块间传裸 YAML dict

### Pydantic Settings 实现护栏

- 需要安装 `pydantic-settings[yaml]`；当前 PyPI 最新稳定版为 `2.13.1`（2026-02-19 发布）
- `BaseSettings.__init__` 官方参数列表不包含 `_yaml_file`; 原稿这条实现指导应视为错误
- `YamlConfigSettingsSource(settings_cls, yaml_file=..., yaml_file_encoding=...)` 官方 API 支持显式文件路径
- 配置加载不要启用 record model 那套 `strict=True`; 按架构要求走“宽松字段解析 + 自定义领域验证”
- 如果内部想保留 `cli_tool` / `phase_type` 这类命名，只能作为内部 `PhaseDefinition` 字段，不要把它们暴露成 YAML 键

### 与当前代码库对齐

- `src/ato/config.py` 当前只有模块 docstring，是本 story 的主实现面
- `src/ato/models/schemas.py` 已提供 `ConfigError`
- `src/ato/logging.py` 已存在 structlog 配置；错误路径用 logger，不要 `print()`
- `tests/unit/` 目前已有 `test_schemas.py`、`test_db.py`、`test_migrations.py`、`test_logging.py`; `test_config.py` 应遵循同一 pytest 风格
- 最近提交节奏：
  - `f7f97f1` — Story 1.1 脚手架与开发工具链
  - `c0901a9` — Story 1.2 SQLite 持久化层
- `src/ato/__init__.py` 当前只导出版本号；本 story 只需保证 `config.py` 自身公共接口清晰，不要求新增根包 re-export

### Public YAML Schema Guardrails

建议将 `ato.yaml.example` 设计成以下外部结构，避免后续 `ato init` / `ato plan` / 状态机构建再做破坏性调整：

```yaml
roles:
  creator:
    cli: claude
    model: sonnet
  reviewer:
    cli: codex
    model: codex-mini-latest
    sandbox: read-only

phases:
  - name: creating
    role: creator
    type: structured_job
    next_on_success: validating
  - name: validating
    role: reviewer
    type: convergent_loop
    next_on_success: dev_ready
    next_on_failure: creating

max_concurrent_agents: 4

convergent_loop:
  max_rounds: 3
  convergence_threshold: 0.5

timeout:
  structured_job: 1800
  interactive_session: 7200

cost:
  budget_per_story: 5.0
  blocking_threshold: 10

model_map: {}
```

### 验证算法提示

- 不要只沿 `next_on_success` 单链做循环检测
- 更稳妥的规则是：
  - 入口 phase 必须存在
  - 每个可达 phase 的 success / failure 边都必须指向已定义节点或 `"done"`
  - 从每个可达节点都应存在一条到 `"done"` 的路径
  - 允许受控回环（如 review/fix），但禁止“只有环、没有出口”的配置

### 后续 Story 依赖本 story 的接口

| 消费者 | 使用的接口 | Story |
|--------|-----------|-------|
| Story 1.4a Preflight | `load_config()` 验证 `ato.yaml` 存在且可解析 | Epic 1 |
| Story 1.4b ato init | `load_config()` 在初始化流程中调用 | Epic 1 |
| Story 1.5 ato plan | `build_phase_definitions()` 生成阶段预览 | Epic 1 |
| Story 2A.1 状态机 | `ATOSettings` + `build_phase_definitions()` 构建状态机输入 | Epic 2A |
| Story 2A.3 ato start | `load_config()` 启动时加载配置 | Epic 2A |
| Story 2B.1 Claude dispatch | `RoleConfig` / `PhaseDefinition` 提供 CLI 类型与模型 | Epic 2B |
| Story 2B.2 Codex review | `RoleConfig` / `PhaseDefinition` 提供 sandbox 设置 | Epic 2B |

### 测试策略

- **测试文件位置：** `tests/unit/test_config.py`
- **配置 fixture 创建方式：** 在 `tmp_path` 中动态写入 YAML 文件，避免依赖仓库根目录
- **第一份 golden fixture：** `ato.yaml.example` 本身必须可被 `load_config()` 直接解析
- **无效配置测试：** 分别覆盖缺字段、错引用、死循环、非法阈值、非法 `type`、非法 `sandbox`
- **不需要 mock：** 配置加载是纯文件读取 + Pydantic 验证，无外部依赖

### 命名约定速查

| 范围 | 规则 | 示例 |
|------|------|------|
| 配置键 (ato.yaml) | snake_case | `max_concurrent_agents`, `convergent_loop.max_rounds` |
| 配置模型类 | PascalCase + Config 后缀 | `RoleConfig`, `PhaseConfig`, `TimeoutConfig` |
| 主配置类 | PascalCase | `ATOSettings` |
| 公共函数 | snake_case | `load_config()`, `build_phase_definitions()` |
| 内部函数 | `_snake_case` | `_validate_config()` |
| 内部 DTO | PascalCase + Definition 后缀 | `PhaseDefinition` |

### 反模式清单（本 story 相关）

- ❌ 不要把 PRD 的外部 YAML 键重命名成 story 私有命名
- ❌ 不要依赖不存在的 `_yaml_file` BaseSettings init 参数
- ❌ 不要在 `config.py` 中实例化状态机
- ❌ 不要在 Pydantic validator 中做 IO
- ❌ 不要手写 `yaml.safe_load()` + 裸 dict 在系统其他模块间流转
- ❌ 不要使用 `print()` 输出错误；使用 structlog + `ConfigError`

### Project Structure Notes

- `src/ato/config.py` — 配置模型 + 加载函数 + 验证函数 + `PhaseDefinition`（**本 story 主要修改文件**）
- `ato.yaml.example` — 配置模板（**新建**，项目根目录）
- `tests/unit/test_config.py` — 配置单元测试（**新建**）
- `src/ato/models/schemas.py` — `ConfigError` 已存在，无需修改
- `pyproject.toml` / `uv.lock` — 安装 `pydantic-settings[yaml]` 时会更新

### References

- [Source: _bmad-output/planning-artifacts/epics.md — Story 1.3 Acceptance Criteria]
- [Source: _bmad-output/planning-artifacts/prd.md — 声明式工作流配置示例, 关键配置项, FR1, FR2, FR35, FR51, NFR5]
- [Source: _bmad-output/planning-artifacts/architecture.md — Decision 3 配置表达力边界, Pydantic v2 验证模式, 项目结构]
- [Source: _bmad-output/project-context.md — 配置访问模式, structlog / pytest / mypy 规则]
- [Source: _bmad-output/implementation-artifacts/1-2-sqlite-state-persistence.md — `ConfigError` 已定义, strict mypy/testing conventions]
- [Source: https://docs.pydantic.dev/latest/api/pydantic_settings/ — `BaseSettings.__init__`, `settings_customise_sources`, `YamlConfigSettingsSource`]
- [Source: https://pypi.org/project/pydantic-settings/ — `pydantic-settings` 2.13.1, `yaml` extra]

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

### File List
