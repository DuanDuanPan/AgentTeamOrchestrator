---
title: '跨项目测试策略分层与受控发现'
slug: 'cross-project-test-policy-layering'
created: '2026-04-09'
status: 'Implementation Complete'
stepsCompleted: [1, 2, 3, 4, 5, 6, 7]
tech_stack:
  - 'Python 3.11+'
  - 'Pydantic Settings / YAML 配置'
  - 'asyncio structured jobs'
  - 'Codex CLI / Claude CLI adapters'
  - 'pytest + pytest-asyncio'
files_to_modify:
  - 'src/ato/config.py'
  - 'src/ato/models/schemas.py'
  - 'src/ato/recovery.py'
  - 'src/ato/merge_queue.py'
  - 'ato.yaml.example'
  - 'tests/unit/test_config.py'
  - 'tests/unit/test_recovery.py'
  - 'tests/unit/test_merge_queue.py'
  - 'tests/integration/test_config_workflow.py'
code_patterns:
  - 'ATOSettings 是唯一配置入口；阶段差异通过 settings + build_phase_definitions() + _resolve_phase_config_static() 传播'
  - 'RegressionResult 是 merge_queue fail-closed 归一化入口；结构化输出字段变更必须同步 schema 与测试'
  - 'qa_testing 属于 worktree 上的 structured job / convergent loop 语义，prompt 主导测试发现策略'
  - 'regression 属于 main workspace 上的 merge_queue 管理型 structured job，支持 operator baseline commands'
  - 'phase config 只负责 cli/model/sandbox/timeout/workspace，不承载测试层级策略'
  - 'merge_queue 与 recovery 共享 phase 解析与 dispatch option 约定，避免第二套执行协议'
test_patterns:
  - '配置相关回归集中在 tests/unit/test_config.py'
  - 'qa/recovery 行为相关回归集中在 tests/unit/test_recovery.py'
  - 'regression runner 合同与 merge_queue 回归集中在 tests/unit/test_merge_queue.py'
  - '跨流程配置工作流测试位于 tests/integration/test_config_workflow.py'
---

# Tech-Spec: 跨项目测试策略分层与受控发现

**Created:** 2026-04-09

## Overview

### Problem Statement

ATO 当前对测试执行采用了不对称模型：`regression` 支持操作者提供 baseline 命令，而 `qa_testing` 主要依赖 LLM 自主发现项目测试框架和命令。这在单一项目上已经暴露出两类问题：一类是过度自由发现导致选择到不稳定或错误的执行入口，另一类是将策略硬编码到单个项目命令后难以迁移到 Java、Python、Go、Rust 等其他项目。系统缺少一个跨项目、跨语言的统一测试策略抽象，无法在“硬性规定”与“LLM 自主判断”之间建立可配置、可演进的边界。

### Solution

引入“测试能力层 + phase 策略层 + 受控发现”的通用模型。ATO 核心只理解抽象测试层级与执行策略，项目通过配置声明可用测试能力与命令映射，LLM 仅在策略允许范围内做发现、补充执行和失败诊断；在未显式配置时，系统提供有上限的默认 discovery fallback，而不是继续依赖无边界自由探索。对需要机器验证的执行审计，优先通过现有 structured output 合同扩展实现，而不是修改 DB schema。

### Scope

**In Scope:**
- 为 `qa_testing` 与 `regression` 设计统一的测试策略抽象
- 定义跨语言可复用的测试能力层与项目命令映射模型
- 定义 phase 级必跑层、可选层、追加条件与受控发现边界
- 约束 LLM 在测试执行中的职责边界、追加测试条件与结果记录方式
- 规划 ATO 配置 schema、prompt 契约与回归测试覆盖方向

**Out of Scope:**
- 直接修改任意业务项目的具体测试脚本
- 一次性重写所有 phase 的执行逻辑
- 引入新的 TUI 交互面板
- 在本规格中直接实现代码改动

## Context for Development

### Codebase Patterns

- `ATOSettings` 当前只内建 `regression_test_command` / `regression_test_commands` 两个 regression 专用字段，没有面向 `qa_testing` 或通用 phase 的测试策略抽象，因此测试执行能力仍以 phase 特例形式散落在 `config.py`、`recovery.py`、`merge_queue.py` 中。
- `qa_testing` 的核心行为目前由 prompt 决定：`recovery.py` 中直接要求 agent “发现项目测试框架和命令”并执行 full test suite。这意味着 QA 的测试范围、顺序和额外诊断命令是 prompt 驱动而非配置驱动。
- `regression` 则采用不同模型：`merge_queue.py` 中 `_build_regression_prompt()` 会根据 `regression_test_commands` 是否存在，在“operator baseline commands”与“autonomous discovery”之间切换；这已经是受控发现的雏形，但仅限 regression。
- phase 级配置解析通过 `build_phase_definitions()` → `RecoveryEngine._resolve_phase_config_static()` 统一下沉，当前只覆盖 `cli_tool`、`model`、`sandbox`、`timeout`、`workspace`、reasoning 等 dispatch 元数据，不承载测试策略语义。
- `merge_queue._build_regression_dispatch_options()` 明确复用 phase 配置解析；这说明新设计若要成立，最佳落点仍应是现有配置模型与 phase dispatch 链路，而不是在某个 phase 内继续硬编码额外规则。
- 现有项目经验已经证明，项目稳定测试入口往往不是裸命令，而是包装过的 repo-native script；因此未来通用模型必须允许“能力层”映射到项目自定义入口，而不能要求 LLM 直接猜底层框架命令。

### Design Principles

- **Protocol over Commands**：ATO 核心应控制“需要产出的测试证据类型与最小保证”，而不是直接把具体命令文本硬编码进 phase 逻辑。
- **Project Mapping over Framework Inference**：项目配置应声明“我有哪些稳定测试入口”，而不是把“如何猜测底层框架”交给 LLM。
- **Bounded Discovery over Unrestricted Autonomy**：LLM 可以补充发现和诊断，但只能在 phase policy 明确授权的边界内行动。
- **Auditability over Opaque Heuristics**：每条执行命令都必须能追溯来源、触发原因与 phase 决策依据，不能让回归结论建立在不可审计的自由探索上。

### Files to Reference

| File | Purpose |
| ---- | ------- |
| `src/ato/recovery.py` | `qa_testing` prompt 与 structured job 行为定义 |
| `src/ato/merge_queue.py` | `regression` prompt、baseline 命令策略与执行路径 |
| `src/ato/config.py` | ATO 配置模型与 `regression_test_commands` 当前设计 |
| `src/ato/models/schemas.py` | regression structured output 的 strict schema |
| `ato.yaml.example` | 公开配置模板与现有 regression 配置语义 |
| `tests/unit/test_recovery.py` | QA / recovery 行为的现有单元测试落点 |
| `tests/unit/test_merge_queue.py` | regression runner 与 merge queue 合同测试落点 |
| `tests/unit/test_config.py` | 配置解析与向后兼容测试落点 |
| `tests/integration/test_config_workflow.py` | 跨流程配置集成验证 |
| `_bmad-output/implementation-artifacts/8-4-regression-test-multi-command.md` | 既有 regression 多命令能力的约束与边界 |
| `_bmad-output/implementation-artifacts/tech-spec-llm-regression-runner.md` | LLM-assisted regression runner 的已实现设计背景 |
| `_bmad-output/project-context.md` | 项目级实现规则、测试组织与配置惯例 |

### Technical Decisions

- 本规格优先解决“跨项目测试策略抽象”问题，而不是继续为单个 phase 添加更多特例配置。
- 第一阶段只覆盖 `qa_testing` 与 `regression`，避免范围失控。
- 测试层命名将作为能力/意图层，而不是绑定当前项目的框架名；第一版推荐采用 `bootstrap / lint / typecheck / unit / integration / system / build / smoke / package` 这类跨语言层级，避免把 `lint` 与 `typecheck` 过早压扁成单个 `static_analysis`。
- 新模型必须兼容“一个 layer 对应多条命令”和“某些 layer 在项目中不存在”两种现实情况。
- `regression_test_commands` 的现有 operator-baseline 语义不能被破坏；更通用的策略抽象应当向后兼容这条路径，而不是替换它。
- 新设计必须区分三层职责：ATO 定义 phase 协议、项目声明测试能力映射、LLM 在被授权的边界内做补充执行和失败分类。
- LLM 的角色将被限制为“在策略边界内补全、选择和诊断”，不直接决定核心执行协议。
- 第一版规格应优先复用现有文件与测试矩阵：`config.py`、`recovery.py`、`merge_queue.py`、`ato.yaml.example`、`tests/unit/test_config.py`、`tests/unit/test_recovery.py`、`tests/unit/test_merge_queue.py`。
- phase policy 必须有一组最小字段集，至少包括：`required_layers`、`optional_layers`、`allow_discovery`、`max_additional_commands`、`allowed_when`，以便把“可跑什么、何时能追加、能追加多少”从 prompt 文本变成配置协议。
- `allowed_when` 第一版必须使用 phase-neutral 固定枚举，并基于“required commands 是否已执行/失败”求值，而不是直接暴露 phase 特有术语。推荐最小集合：`never`、`after_required_commands`、`after_required_failure`、`always`。在 regression legacy 路径中，operator baseline commands 视为 required commands。
- `required_layers` 的声明顺序就是执行顺序，`optional_layers` 的声明顺序就是候选追加顺序；如果项目需要先跑 `bootstrap` 再跑 `unit`，必须通过 phase policy 顺序明确表达。
- `max_additional_commands` 的计数单位必须固定为“单次 phase 执行尝试”；对 `qa_testing` 这类 convergent loop，按每轮单独计数，而不是按 story 生命周期累计。
- 新设计必须定义命令来源审计字段，至少区分：`project_defined`、`llm_discovered`、`llm_diagnostic`，并要求执行记录携带来源与触发原因。
- 向后兼容必须是显式设计目标：`regression_test_commands` 需要映射到新模型，而不是要求所有项目先迁移到 `test_catalog` 或等价抽象后才能继续使用。
- legacy regression 兼容语义必须原样保留：`regression_test_commands is None` 且 singular 非默认值时仍视为 baseline；`regression_test_commands: []` 必须回退到 singular；plural 为 `None` 且 singular 为默认值 `"uv run pytest"` 时继续走 discovery fallback。
- 第一版不引入通用 CI DSL，不支持任意布尔表达式或复杂条件树；能力层与 policy 语义必须保持小而清晰。
- 统一的是“测试策略抽象”，不是 `qa_testing` 与 `regression` 的完整 phase 语义；两者现有的 workspace、调度器、结果消费链路必须保留。
- 第一版固定采用 `test_catalog` 与 `phase_test_policy` 作为 canonical 配置命名，避免在实现期再引入 `test_layers`、`test_capabilities` 等平行命名。
- effective test policy 只能由 `config.py` 中的单一 resolver 计算；`recovery.py` 与 `merge_queue.py` 只能消费解析结果，不能各自拼装策略。解析结果必须通过统一 runtime surface 向下传播，例如 `phase_cfg["test_policy"]` 或等价字段，而不是让各调用方再读原始 YAML。
- 第一版审计信息不新增 DB schema；但允许扩展现有 structured output / text output 合同。regression 应引入机器可读、schema-validated 的 `command_audit` 结构化字段，同时保留 `commands_attempted` 作为纯命令文本列表；QA 审计继续走文本 section，但格式必须稳定。
- 非 `qa_testing` / `regression` 的 phase 默认行为不变，不能在实现时顺手把策略抽象扩散到 `validating`、`reviewing` 等其他 phase。
- `qa_testing` 在无显式 policy 时仍必须“可运行”，但 fallback 不能继续是无上限自由发现。第一版应提供内建 bounded-discovery 默认策略：优先 repo-native wrapper scripts，其次标准 test entrypoints，并受默认 `max_additional_commands` 限制。
- `ato.yaml.example` 必须同时展示两层信息：phase policy 使用抽象 layer 名，`test_catalog` 使用项目真实命令映射。不能把“不要在 phase policy 中写死工具名”误写成“示例里完全不展示真实命令”。

### Clarifications

#### Clarification 1: `allowed_when` 采用“声明式配置 + 运行时求值”两层模型

- `config.py` 中的单一 resolver 负责产出 declaration-only 的 effective policy，包括 `required_layers`、`optional_layers`、`allow_discovery`、`max_additional_commands`、`allowed_when` 以及由 layer 展开的命令集合；resolver 不接收、也不推断运行时执行结果。
- `allowed_when` 的布尔求值发生在 phase execution 内，而不是 `load_config()` 或 `_resolve_phase_config_static()` 阶段。`recovery.py` 与 `merge_queue.py` 可以消费 `phase_cfg["test_policy"]` 并基于“本次 phase 执行尝试中 required commands 的完成/失败结果”决定是否允许 additional commands，但不能回读原始 YAML 或重新发明第二套策略语义。
- `after_required_commands` 表示：当前执行尝试中的 required commands 已全部完成（无论成功或失败）；`after_required_failure` 表示：当前执行尝试中的 required commands 至少有一条失败；`never` / `always` 按字面语义处理。
- 对 legacy regression 路径，operator baseline commands 在该执行模型下等价视为 required commands；其完成/失败结果直接作为 `allowed_when` 的输入信号。
- 对 `qa_testing` 这类 convergent loop，`allowed_when` 与 `max_additional_commands` 一样按每轮 execution attempt 重新求值、重新计数，不跨轮累计状态。

#### Clarification 2: regression `command_audit` 是 best-effort provenance，不是 deterministic proof

- `command_audit` 的目标是“机器可读、可测试、可审计的结构化记录”，不是对命令来源做完全确定性的系统证明。ATO 对其进行 schema 校验与有限一致性校验，但下游逻辑不得仅凭 `source` 字段做硬性判定。
- regression prompt 必须显式向 agent 提供 project-defined command 集合，包括 required commands、optional commands 与 legacy baseline commands（若存在），从而让 agent 能在输出时把命令来源标注为 `project_defined`、`llm_discovered` 或 `llm_diagnostic`。
- ATO 侧至少应校验：`commands_attempted` 仍是纯命令字符串列表；`command_audit.command` 与 `commands_attempted` 可对应；标记为 `project_defined` 的命令必须来自 prompt 中提供的 project-defined command 集合或 legacy baseline 集合。
- `exit_code` 采用 best-effort 语义：若 agent 能观察到退出码，则输出整数；若命令未启动、被跳过、或运行环境无法可靠提供退出码，则输出 `null`（或 schema 允许的等价空值），而不是臆造数值。
- `discovery_notes` 保留，用于补充“为何选择该命令/如何发现该入口”的自由文本说明；`command_audit` 不替代 `discovery_notes`，两者并存。

#### Clarification 3: QA bounded-discovery fallback 与文本审计格式固定为 canonical 合同

- 当 `qa_testing` 没有显式 `phase_test_policy` 时，ATO 必须使用内建 bounded-discovery fallback，而不是恢复到“执行全部可发现测试命令”的开放式 prompt。fallback 的固定优先顺序为：
  1. repo-native wrapper scripts / task entrypoints，例如 `package.json` scripts、`Makefile` targets、`justfile`、`Taskfile.yml`、`tox` / `nox` / `hatch` / `poetry` / `uv` 项目包装入口；
  2. 若未发现稳定 wrapper，再尝试标准 test entrypoints，例如 Python 的 `uv run pytest` / `pytest`，Node 的 `npm test` / `pnpm test` / `yarn test`，Go 的 `go test ./...`，Rust 的 `cargo test`，JVM 的 `./gradlew test`；
  3. 仅当 `allow_discovery=true` 且满足 `allowed_when` 时，才允许在上述集合之外追加诊断型命令，并受 `max_additional_commands` 限制。
- 上述优先顺序属于 prompt 合同的一部分，必须在 QA prompt 文本中固定表达，而不是交由实现者在不同 phase 各自发明。
- QA 文本审计继续使用稳定 markdown section，不引入新的 findings schema。第一版固定使用 `## Commands Executed` section，并要求每条命令按以下 canonical 单行格式记录：
  - ``- `COMMAND` | source=project_defined|llm_discovered|llm_diagnostic | trigger=required_layer:<name>|optional_layer:<name>|fallback:<kind>|diagnostic:<reason> | exit_code=<int|null>``
- 若命令未执行而被跳过，不写入 `Commands Executed` 条目；跳过原因应在相邻说明文本或总结段落中解释，但不得把来源标签直接拼进 regression `commands_attempted`。

## Implementation Plan

### Tasks

- [x] Task 1: 在配置模型中引入通用测试能力层与 phase 策略层，同时保留 legacy regression 路径
  - File: `src/ato/config.py`
  - Action: 新增面向跨项目测试策略的配置 DTO，例如 `TestLayerConfig` 与 `PhaseTestPolicyConfig`，并在 `ATOSettings` 中增加通用配置入口（如 `test_catalog`、`phase_test_policy` 或等价命名）。
  - Notes: 必须支持“单 layer 多命令”“layer 可缺省”“phase 按需声明 required/optional layers”。推荐内建 layer 名至少包含 `bootstrap / lint / typecheck / unit / integration / system / build / smoke / package`，但项目可只声明子集。`regression_test_command` / `regression_test_commands` 不能删除，需通过 helper 映射为新模型下的 effective regression policy。

- [x] Task 2: 为 phase 解析提供“有效测试策略”访问器，而不是让 prompt 直接读取原始 YAML
  - File: `src/ato/config.py`
  - Action: 增加 helper，用于根据 phase 名称返回有效测试策略，合并优先级至少覆盖：显式 `phase_test_policy` > legacy regression config > 无配置时的 discovery fallback。
  - Notes: 对非法策略引用做校验并抛 `ConfigError`；例如引用未知 layer、`max_additional_commands < 0`、`allowed_when` 非法枚举等。helper 必须同时固化三类关键语义：`required_layers` 顺序即执行顺序、`optional_layers` 顺序即候选追加顺序、`max_additional_commands` 按单次 phase 执行尝试计数。不要把该逻辑散落到 `recovery.py` 和 `merge_queue.py`。
  - Notes: 解析后的 effective policy 必须通过统一 runtime surface 向下传播，例如挂入 `_resolve_phase_config_static()` 返回值的 `test_policy` 字段，避免出现第二套配置读取路径。
  - Notes: resolver 只产出 declaration-only 的 effective policy；`allowed_when` 的布尔求值必须在 phase execution 内基于“本次执行尝试中的 required commands 结果”完成，而不是在 `load_config()` 时提前求值。

- [x] Task 3: 将 `qa_testing` prompt 改造成“策略驱动 + parser 兼容”
  - File: `src/ato/recovery.py`
  - Action: 改造 `qa_testing` prompt 模板或其构造路径，使其优先使用项目声明的测试 layer 与 phase policy，明确 required layers、optional layers、是否允许 discovery、额外命令上限及触发条件。
  - Notes: 必须保留 `qa_report` 解析器依赖的完整合同：`Recommendation`、`Quality Score`、`Critical Issues`、`Recommendations`、`Quality Criteria Assessment`、编号 issue block，以及每个 issue 内的 `Severity` / `Location` / `Criterion` 元数据。`Commands Executed` 可以增强，但不能破坏现有解析兼容性。
  - Notes: 当 `qa_testing` 没有显式 policy 时，不应继续使用“无限制发现并尽量全跑”的旧 prompt，而应使用内建 bounded-discovery fallback：优先 repo-native wrapper scripts / task entrypoints（如 `package.json` scripts、`Makefile`、`justfile`、`Taskfile.yml`、`tox` / `nox` / `hatch` / `poetry` / `uv` 包装入口），其次标准 test entrypoints（如 `uv run pytest` / `pytest`、`npm test` / `pnpm test` / `yarn test`、`go test ./...`、`cargo test`、`./gradlew test`），并受默认 `max_additional_commands` 约束。命令记录需要体现来源与触发原因，但 QA 侧继续使用稳定文本 section，而不是引入新 findings schema。
  - Notes: 第一版固定使用 `## Commands Executed` 文本 section，并要求每条命令采用 canonical 单行格式：``- `COMMAND` | source=... | trigger=... | exit_code=...``。

- [x] Task 4: 将 `regression` prompt 与 baseline 行为升级为通用策略模型的一个特例
  - File: `src/ato/merge_queue.py`
  - Action: 改造 `_build_regression_prompt()`，使其在显式 phase policy 存在时按通用策略生成指令；若不存在，则继续保留当前 `regression_test_commands` baseline contract。
  - Notes: 现有 “The operator has provided these baseline regression commands. You MUST execute them first” 语义必须保留。新增策略后，只允许在 `allowed_when` 满足时执行额外发现/诊断命令，并且 required commands 的求值必须覆盖 legacy baseline 语义。
  - File: `src/ato/models/schemas.py`
  - Action: 扩展 `RegressionResult` 及对应 JSON schema，使 regression structured output 同时提供向后兼容的 `commands_attempted: list[str]` 与机器可读、schema-validated 的 `command_audit` 条目列表。
  - Notes: `commands_attempted` 必须继续保持“纯 shell 命令文本”语义，不能把来源标签直接拼进字符串；来源、触发原因、exit code 等元数据应进入 `command_audit`。
  - Notes: `command_audit` 属于 best-effort provenance 记录。prompt 必须显式提供 project-defined command 集合，ATO 至少做 schema 校验与有限一致性校验；`source` 字段不得被下游当作 deterministic proof 使用。`discovery_notes` 保留，不与 `command_audit` 合并。

- [x] Task 5: 更新公开配置模板，给出跨语言友好的最小示例
  - File: `ato.yaml.example`
  - Action: 增加通用测试能力与 phase 策略示例，展示如何为 `qa_testing` 与 `regression` 分别声明 required layers、optional layers 和 discovery 边界。
  - Notes: phase policy 必须使用抽象 layer 名；`test_catalog` 必须展示这些 layer 如何映射到项目真实命令。可以用注释或双示例表现 Node/Python 等不同项目，但不能把真实命令完全藏掉。legacy `regression_test_command(s)` 注释仍需保留，并明确其迁移/兼容关系。

- [x] Task 6: 用现有测试矩阵覆盖配置、prompt 和 regression 合同
  - File: `tests/unit/test_config.py`
  - Action: 新增配置解析与校验测试，覆盖通用 `test_catalog` / `phase_test_policy` 加载、单 layer 多命令、缺省 layer、legacy regression 映射、非法 layer 引用拒绝、`required_layers` 执行顺序、`max_additional_commands` 计数语义。
  - Notes: 测试应直接断言 effective policy 的结果，而不是只断言原始字段存在。必须显式覆盖 `regression_test_commands: []` 回退 singular 与 singular 默认值触发 discovery 的 legacy 边界。
  - File: `tests/unit/test_recovery.py`
  - Action: 新增/扩展 `qa_testing` prompt 合同测试，验证 policy 驱动指令已注入，且 QA parser 依赖的完整结构仍保留。
  - Notes: 至少覆盖“显式 policy”“无 policy 时的 bounded-discovery fallback”“issue 编号/Severity/Location/Criterion 元数据”“Commands Executed 审计格式”四类场景。
  - File: `tests/unit/test_merge_queue.py`
  - Action: 扩展 regression prompt 与结构化输出测试，验证显式 policy、legacy baseline、autonomous discovery fallback 三条路径。
  - Notes: 继续保留现有 `_build_regression_prompt()` 与 `commands_attempted` 合同测试；新增 `command_audit` 结构、来源/触发原因断言，以及 required commands 顺序断言。
  - File: `tests/integration/test_config_workflow.py`
  - Action: 增加示例配置到 effective policy/runtime surface 的端到端测试，验证新增配置字段不会破坏模板加载与 phase cfg 传播。
  - Notes: 至少覆盖 `phase_cfg["test_policy"]` 或等价传播面，并验证未纳入范围的 phase 在加载后仍保持原行为。

- [x] Task 7: 固化枚举与文本合同，避免实现期二次发明
  - File: `src/ato/config.py`
  - Action: 为 `allowed_when` 提供固定枚举定义，为命令来源与触发原因提供固定枚举或常量集合。
  - Notes: 第一版要同时固定 QA 文本审计格式与 regression `command_audit` 结构字段名，避免测试、prompt 与 structured output 各写一套约定。
  - Notes: `command_audit.exit_code` 应采用 `int | null` 或等价固定合同；无法可靠观察退出码时必须输出空值，而不是伪造退出码。

### Acceptance Criteria

- [ ] AC 1: Given `ato.yaml` 中定义了通用测试能力映射与 `qa_testing`/`regression` 的 phase policy，when 调用 `load_config()`，then 配置加载成功且顺序保留，并且 ATO 能返回按 phase 合并后的 effective test policy。
- [ ] AC 2: Given 某个测试 layer 配置了多条命令，when 解析该 layer，then 命令按声明顺序保留；and `required_layers` 的声明顺序即执行顺序；and `optional_layers` 的声明顺序即候选追加顺序。
- [ ] AC 3: Given 仅配置 legacy `regression_test_commands` 而没有新的 regression phase policy，when 构建 regression prompt，then prompt 仍明确要求优先执行 operator baseline commands；and `regression_test_commands: []` 回退到 singular；and plural 为 `None` 且 singular 为默认值 `"uv run pytest"` 时继续走 discovery fallback。
- [ ] AC 4: Given `qa_testing` 存在显式 phase policy，when 构建 QA prompt，then prompt 优先使用声明的 required/optional layers；and 仅在 `allow_discovery=true` 且满足 `allowed_when` 条件时才允许追加命令；and prompt 仍包含 `Recommendation`、`Quality Score`、`Critical Issues`、`Recommendations`、`Quality Criteria Assessment`、编号 issue block，以及每个 issue 所需的 `Severity` / `Location` / `Criterion` 元数据。
- [ ] AC 5: Given `regression` 存在显式 phase policy，when 构建 regression prompt，then prompt 先按 required order 执行 required commands；and 只有满足策略条件时才允许额外发现/诊断；and structured output 同时包含向后兼容的 `commands_attempted` 与机器可读、schema-validated 的 `command_audit`。
- [ ] AC 6: Given phase policy 或测试能力映射引用了未知 layer、非法触发条件、或负数 `max_additional_commands`，when 调用 `load_config()`，then 抛出 `ConfigError`，并给出可定位到配置项的错误信息。
- [ ] AC 7: Given `qa_testing` 与 `regression` 都接入新模型，when 它们运行在各自现有链路中，then 仅统一测试策略抽象；and `qa_testing` 继续在 worktree 上运行、`regression` 继续由 merge queue 在 main workspace 上运行；and 不引入新的 DB schema。
- [ ] AC 8: Given 修改完成后运行相关测试，when 执行 `tests/unit/test_config.py`、`tests/unit/test_recovery.py`、`tests/unit/test_merge_queue.py`、`tests/integration/test_config_workflow.py`，then 新增策略逻辑与 legacy 路径均被覆盖；and 至少有一条测试验证 QA prompt 的 parser 兼容性未被破坏；and 至少有一条测试验证 effective policy 通过统一 runtime surface 传播。
- [ ] AC 9: Given `qa_testing` 未配置显式 `phase_test_policy`，when ATO 构建 QA prompt，then 仍可在无需迁移 `test_catalog` 的前提下运行；and fallback discovery 默认是 bounded 的；and 会优先选择 repo-native wrapper scripts，而不是无限制枚举底层框架命令。
- [ ] AC 10: Given `optional_layers` 引用了项目未声明的 layer，when 解析 effective policy，then 不报错并跳过该 layer；and Given `required_layers` 引用了未声明的 layer，then `load_config()` 抛出 `ConfigError`。
- [ ] AC 11: Given `allowed_when` 被配置为 `never`、`after_required_commands`、`after_required_failure` 或 `always` 之一，when 解析并执行 policy，then 其求值只依赖 required commands 的完成/失败状态；and regression legacy baseline 在该求值模型下被视为 required commands。
- [ ] AC 12: Given `max_additional_commands` 被设置为 `N`，when 单次 phase 执行尝试发生追加发现/诊断，then 该次执行最多运行 `N` 条 additional commands；and 对 `qa_testing` 的下一轮 loop，应重新开始该计数，而不是跨轮累计。
- [ ] AC 13: Given regression 返回带审计信息的 structured output，when ATO 验证结果，then `commands_attempted` 继续只包含纯命令字符串；and `command_audit` 承载 `command`、`source`、`trigger_reason`、`exit_code` 或等价固定字段；and 不允许把来源标签直接拼进 `commands_attempted`。
- [ ] AC 14: Given 更新后的 `ato.yaml.example` 被直接加载，when 执行端到端配置工作流测试，then 示例同时展示抽象 layer policy 与真实命令映射；and 示例可被 `load_config()` 成功加载。
- [ ] AC 15: Given 本 story 完成后运行未纳入范围的 phase 相关测试，when 执行现有 `validating` / `reviewing` / CLI 配置加载相关测试，then 行为保持不变，且本 story 不修改这些 phase 的 prompt 合同。

## Additional Context

### Dependencies

- 依赖现有 `ATOSettings` / `load_config()` / `build_phase_definitions()` 作为唯一配置入口，不能引入第二套独立配置加载路径。
- 依赖 `RecoveryEngine._resolve_phase_config_static()` 与 `merge_queue._build_regression_dispatch_options()` 的现有 phase-dispatch 合同，新方案必须复用而不是绕开；effective policy 必须能通过统一 phase cfg surface 向下传播。
- 依赖 `src/ato/adapters/bmad_adapter.py` 中 `qa_report` 解析约定；QA prompt 的增强不能破坏 `Critical Issues` / `Recommendations` / `Quality Criteria Assessment` 的解析语义。
- 依赖现有 regression structured output 合同、`RegressionResult` strict model 与 `commands_attempted` 字段；若增强审计信息，应通过 schema 扩展提供结构化审计字段，而不是先引入新 DB schema。
- 依赖现有 unit test 布局，不新增平行测试矩阵或新的测试目录层级。

### Testing Strategy

- 配置层测试：在 `tests/unit/test_config.py` 中覆盖 DTO 解析、legacy regression 映射、非法 layer 引用、单 layer 多命令、执行顺序与缺省 layer 场景。
- QA prompt 合同测试：在 `tests/unit/test_recovery.py` 中断言 `qa_testing` prompt 同时满足两件事：
  - 包含 policy 驱动的 required/optional/discovery 边界
  - 保留 `qa_report` parser 所需 marker、issue 编号结构及 `Severity` / `Location` / `Criterion` 元数据
- Regression prompt 合同测试：在 `tests/unit/test_merge_queue.py` 中覆盖三条路径：
  - 显式 phase policy
  - legacy `regression_test_commands`
  - 无配置时的 discovery fallback
- Fallback 行为测试：覆盖“QA 无 policy 时走 bounded-discovery 默认策略”“optional layer 缺失不报错”“required layer 缺失即配置错误”“legacy regression 空 plural 回退 singular”四类边界。
- 兼容性测试：保留现有 regression baseline 相关测试，不允许因为通用抽象的引入让已实现的 Story 8.4 / LLM regression runner 合同失效。
- Schema 合同测试：覆盖 `RegressionResult` 新增审计字段后的 strict validation，确保 `commands_attempted` 仍是纯命令字符串列表，`command_audit` 才承载来源/触发原因。
- 模板测试：更新 `ato.yaml.example` 后，确保 `tests/integration/test_config_workflow.py` 能直接加载示例配置，并验证新增 policy 可传播到 phase cfg。

### Notes

- 该规格需要兼容多语言项目，不应假设 `pnpm`、`pytest`、`vitest`、`gradle` 等任一具体工具恒定存在。
- 该规格应复用 ATO 现有 phase 配置与 prompt 生成路径，避免引入第二套配置系统。
- 当前实现中 `qa_testing` 和 `regression` 的职责差异不仅是 prompt 不同，还包括 workspace、调度器和结果消费链路不同；实现方案必须显式保留这些差异，只统一测试策略抽象。
- 该规格若失败，高概率不是编码错误，而是边界设计失控：把配置做成复杂 DSL、把 layer 定义成工具名、或者允许 LLM 无边界追加命令。Step 3 需要把这些失败模式转成明确 AC。
- Step 3 生成的 AC 必须至少覆盖：legacy 配置映射、layer 缺省、单 layer 多命令、phase 默认策略差异、LLM 追加命令上限与执行审计。
- 第一版只解决“如何平衡硬规则与受控发现”，不解决所有测试治理问题；例如 flaky 策略库、历史命令推荐、自动策略学习可以留到后续 story。
- 高风险项 1：把 `test_catalog` / phase policy 设计成通用 CI DSL，导致学习成本过高。第一版必须限制字段集，避免表达式求值、复杂依赖图和条件树。
- 高风险项 2：把抽象 layer 重新退化成工具名，或把 `lint` / `typecheck` 这类真实决策维度压扁成过粗的抽象。每个推荐 layer 在实现中都需要附带语义说明。
- 高风险项 3：为了审计而改动 QA parser 或 findings schema。第一版应优先保持 QA findings 解析不变，只在文本 section 中增强命令审计；需要机器可读、schema-validated 的审计信息优先放入 regression structured output。
- 高风险项 4：没有定义 `required_layers` 执行顺序、`allowed_when` 求值模型、`max_additional_commands` 计数单位，导致不同 phase 各自实现出不同语义。第一版必须把这三件事写成固定协议。
- 高风险项 5：把来源标签直接塞进 `commands_attempted`，破坏该字段原有“纯命令文本”语义。若需要机器可读、schema-validated 的审计，必须使用独立结构化字段。
- 第一版非目标：不自动学习历史命令、不做跨项目命令推荐、不引入 flaky policy registry，也不在本 story 中解决命令成功率统计与动态策略调优。
