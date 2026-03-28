# Story 8.5: ato init 自动生成配置文件并支持项目路径选择

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a 操作者,
I want `ato init` 在目标项目路径下自动生成 `ato.yaml`（如不存在），并让后续 `ato start --db-path ...` 能按同一项目根解析配置,
so that 初始化流程真正做到“零手动复制模板”，且指定项目路径后的后续启动链路不会因为 cwd 不同而失效。

## Acceptance Criteria (AC)

### AC1: 缺少 `ato.yaml` 在 `ato init` 中变为 INFO，而不是 HALT

```gherkin
Given 目标项目路径下没有 `ato.yaml`
When `run_preflight(project_path, db_path, include_auth=True)` 被 `ato init` 调用
Then `ato_yaml` 检查结果为 `INFO` 而不是 `HALT`
And 提示文本明确说明 init 将自动从 `ato.yaml.example` 生成配置
And Layer 3 artifact 检查继续执行，不因缺少 `ato.yaml` 提前跳过
```

### AC2: `ato init` 自动从目标项目中的 example 生成配置，且不覆盖已有文件

```gherkin
Given `<project_path>/ato.yaml` 不存在，且 `<project_path>/ato.yaml.example` 存在
When `_init_async()` 在 preflight 完成后继续执行
Then `ato.yaml.example` 被复制到 `<project_path>/ato.yaml`
And CLI 输出明确提示“已生成 ato.yaml，可按需调整后运行 `ato start`”
And 若 `<project_path>/ato.yaml` 已存在，则文件保持不变并输出“使用已有配置文件”
```

### AC3: 保持现有 reinit / DB 持久化合同，不重写 init 流程的时序

```gherkin
Given 当前 `init_command()` 与 `run_preflight()` 的实现
When 加入配置自动生成能力
Then 现有“检测到已有数据库时先确认是否重新初始化”的行为保持不变
And `.ato/state.db` 仍由 `run_preflight()` 负责创建与持久化检查结果
And 配置生成发生在 preflight 结果已落库之后、最终成功提示之前
```

### AC4: example 缺失时明确失败，但不引入额外模板来源

```gherkin
Given 目标项目既没有 `ato.yaml` 也没有 `ato.yaml.example`
When 执行 `ato init`
Then 命令以非零退出码失败，并提示缺少 example 模板
And 不输出“系统已初始化”
And 本 story 不引入打包内置模板或目标项目之外的 fallback 模板来源
```

### AC5: 指定项目路径后的 `ato start --db-path ...` 能按同一项目根工作

```gherkin
Given 操作者先执行 `ato init /path/to/project`
And 随后在另一个 cwd 中执行 `ato start --db-path /path/to/project/.ato/state.db`
When start 命令执行 preflight 与配置加载
Then project path 由 `db_path` 推导，而不是硬编码使用 `Path.cwd()`
And 配置发现优先级为显式 `--config`，其次是 db 所属项目根
And 自动生成的 `<project_path>/ato.yaml` 可直接被消费，无需先 `cd` 到项目目录
```

### AC6: 共享模板 / 提示 / 现有测试基线保持一致

```gherkin
Given `ato.yaml.example` 与 init/preflight 提示文本是 Epic 8 多个 story 的共享基线
When 实现本 story
Then `_HINTS["ato_yaml"]`、preflight message 与 init 成功提示都对齐“自动生成”语义
And `ato.yaml.example` 仍可被 `load_config()` 直接加载
And 回归覆盖通过扩展现有 `test_cli_init.py`、`test_preflight.py`、`test_preflight_integration.py` 与 start-path 测试完成
```

## Tasks / Subtasks

- [x] Task 1: 更新 preflight 的 `ato_yaml` 合同 (AC: #1, #6)
  - [x] 1.1 将 `src/ato/preflight.py::_check_ato_yaml()` 从 `HALT` 调整为 `INFO`，message 改为”`ato.yaml` 不存在，init 时将自动从 `ato.yaml.example` 生成”
  - [x] 1.2 更新 `src/ato/cli.py::_HINTS[“ato_yaml”]`，把引导从”手动复制”改为”init 将自动生成 / 若失败再检查 example”
  - [x] 1.3 更新 `tests/unit/test_preflight.py` 与 `tests/integration/test_preflight_integration.py`：缺少 `ato.yaml` 时不再导致 Layer 2 HALT，也不再跳过 Layer 3

- [x] Task 2: 在现有 `_init_async()` 时序中加入配置自动生成 (AC: #2, #3, #4)
  - [x] 2.1 在 `src/ato/cli.py::_init_async()` 中，保持 `run_preflight()` 与 `render_preflight_results()` 先执行
  - [x] 2.2 在 preflight 通过、最终成功提示之前，检测 `<project_path>/ato.yaml`
  - [x] 2.3 若缺失，则从 `<project_path>/ato.yaml.example` 复制生成；若 example 也缺失，则输出明确错误并 `raise typer.Exit`
  - [x] 2.4 若 `ato.yaml` 已存在，输出”使用已有配置文件”并继续
  - [x] 2.5 **不要**把 DB 初始化从 `run_preflight()` 挪到 CLI 层，也不要移除现有 reinit 确认逻辑

- [x] Task 3: 对齐 `ato start --db-path ...` 的项目路径解析 (AC: #5)
  - [x] 3.1 在 `src/ato/cli.py::start_cmd()` 中，不再把 `Path.cwd()` 当作唯一 project root
  - [x] 3.2 从 `resolved_db` 推导项目根（标准 `.ato/state.db` 布局，或同目录 custom db 场景），用于 start-time preflight
  - [x] 3.3 配置加载优先使用显式 `--config`；未显式指定时，优先从 db 推导出的项目根发现 `ato.yaml`
  - [x] 3.4 尽量复用或提取与 `_resolve_tui_config()` 一致的路径解析策略，避免 init/start/tui 各写一套互相漂移的逻辑

- [x] Task 4: 扩展现有回归测试，而不是新建平行测试矩阵 (AC: #1-#6)
  - [x] 4.1 在 `tests/unit/test_cli_init.py` 增加：无 `ato.yaml` 时自动生成、已有 `ato.yaml` 不覆盖、缺少 example 时失败
  - [x] 4.2 在 `tests/unit/test_preflight.py` / `tests/integration/test_preflight_integration.py` 中更新 `ato_yaml` 的 status/message 与层级跳过预期
  - [x] 4.3 在 `tests/unit/test_cli_start_stop.py`（或同级 start 测试）中增加”`--db-path` 指向其他项目时，start 仍能从该项目根做 preflight/config 解析”的断言
  - [x] 4.4 保留 `tests/integration/test_config_workflow.py` / `tests/unit/test_config.py` 对 `ato.yaml.example` 可直接加载的模板基线

## Dev Notes

### 关键实现判断

- **`project_path` 参数已经存在。** `src/ato/cli.py::init_command()` 早已支持 `project_path: Path` 与 `<project>/.ato/state.db` 默认路径；本 story 的重点不是“再加一个路径参数”，而是把自动生成与后续路径解析做成真正可用的端到端合同。
- **当前 `run_preflight()` 已经负责创建并持久化 `.ato/state.db`。** 因此“先生成配置、再初始化 DB”的说法与现有代码不符；正确做法是在 preflight 落库之后、最终成功提示之前生成配置。
- **真正的下游不一致在 `ato start`。** 当前 `start_cmd()` 仍用 `Path.cwd()` 做 preflight，用 `Path("ato.yaml")` 做默认配置发现；如果用户从别的目录执行 `ato start --db-path <project>/.ato/state.db`，自动生成的配置并不会被正确发现。
- **`tui` 已经有更合理的配置发现 precedent。** `src/ato/cli.py::_resolve_tui_config()` 已展示“显式 `--config` 优先，其次基于 db_path 推导项目根”的思路，start 应收敛到同类策略，而不是继续依赖 cwd。
- **本 story 不让 `ato.yaml` 对所有命令都变成可选。** `load_config()` 缺文件时报错的现有合同保持不变；这里只是让 `ato init` 主动生成它。

### Previous Story Intelligence (from 1.4a / 1.4b / Epic 8 shared files)

1. Story 1.4a / 1.4b 已把 init 流程定义成“三层 preflight -> 持久化结果 -> CLI 渲染 -> 最终确认/提示”；8.5 必须沿用这个时序，而不是重写成另一套 init 流程。
2. Story 1.4b 还建立了“即使最终 HALT，preflight 结果也会先持久化到 SQLite”的合同；8.5 不能破坏这条行为。
3. Story 8.1 / 8.3 / 8.4 都会修改 `ato.yaml.example` 或依赖它被直接复制 / 直接加载；8.5 的模板使用方式必须与这些 story 合并，而不是覆盖共享文件。

### Scope Boundary

- **IN:** `ato init` 中的 `ato.yaml` auto-generate、preflight `ato_yaml` 状态调整、`ato start --db-path` 的项目根解析一致性、相关现有测试扩展
- **OUT:** 让 `ato.yaml` 在所有 CLI 命令中都变成可选
- **OUT:** 引入 packaged/bundled fallback template，或从目标项目根之外复制模板
- **OUT:** 修改 `load_config()` 缺失配置时的通用错误合同，或新增 DB schema

### Project Structure Notes

- 主要修改文件：
  - `src/ato/preflight.py`
  - `src/ato/cli.py`
  - `ato.yaml.example`（仅在文案/模板可复制性需要联动时）
- 重点测试文件：
  - `tests/unit/test_preflight.py`
  - `tests/integration/test_preflight_integration.py`
  - `tests/unit/test_cli_init.py`
  - `tests/unit/test_cli_start_stop.py`
  - `tests/unit/test_config.py`
  - `tests/integration/test_config_workflow.py`

### Suggested Verification

- `uv run pytest tests/unit/test_preflight.py tests/integration/test_preflight_integration.py tests/unit/test_cli_init.py tests/unit/test_cli_start_stop.py -v`
- `uv run pytest tests/unit/test_config.py tests/integration/test_config_workflow.py -v`

### References

- [Source: src/ato/cli.py — `init_command()`, `_init_async()`, `start_cmd()`, `_resolve_tui_config()`, `_HINTS`]
- [Source: src/ato/preflight.py — `_check_ato_yaml()`, `check_project_structure()`, `run_preflight()`]
- [Source: src/ato/config.py — `load_config()` 缺失配置的现有合同]
- [Source: ato.yaml.example — 当前模板基线]
- [Source: tests/unit/test_cli_init.py]
- [Source: tests/unit/test_preflight.py]
- [Source: tests/integration/test_preflight_integration.py]
- [Source: tests/unit/test_cli_tui.py — db_path-based config discovery precedent]
- [Source: _bmad-output/implementation-artifacts/1-4a-preflight-check-engine.md]
- [Source: _bmad-output/implementation-artifacts/1-4b-ato-init-cli-ux.md]
- [Source: _bmad-output/implementation-artifacts/8-1-role-config-simplify-model-sandbox-optional.md]
- [Source: _bmad-output/implementation-artifacts/8-3-cost-control-optional.md]
- [Source: _bmad-output/implementation-artifacts/8-4-regression-test-multi-command.md]
- [Source: _bmad-output/planning-artifacts/architecture.md — Decision 10 Preflight Check 协议]
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md — Startup Readiness Check]

## Change Log

- 2026-03-28: `validate-create-story` 修订 —— 将 Story 从”重复声明已有 project_path 参数 + 错误描述 DB 初始化时序”收敛为真实的 init/preflight/start 合同；补齐 `ato start --db-path` 的项目根解析一致性；扩展 preflight/init/start 现有测试基线，并补回模板 validation note / Scope Boundary / Dev Agent Record 结构
- 2026-03-28: Story 实现完成 —— 全部 4 个 Task 完成，1479 tests passed, 0 failed

## Dev Agent Record

### Agent Model Used

claude-opus-4-6 (1M context)

### Debug Log References

无调试问题。

### Completion Notes List

- ✅ Task 1: `_check_ato_yaml()` 从 HALT 改为 INFO，message 对齐”自动生成”语义；`_HINTS[“ato_yaml”]` 更新；preflight 单元测试和集成测试更新以反映新行为（缺少 ato.yaml 不再跳过 Layer 3）
- ✅ Task 2: 新增 `_ensure_ato_yaml()` 函数，在 preflight 落库后、最终成功提示前自动从 `ato.yaml.example` 复制生成 `ato.yaml`；已有文件不覆盖；example 缺失时以非零退出码终止
- ✅ Task 3: 新增 `_derive_project_root()` 从 db_path 推导项目根，新增 `_resolve_config_path()` 统一配置发现逻辑；`start_cmd()` 不再用 `Path.cwd()` 做 preflight；`_resolve_tui_config()` 委托给共享实现，消除 init/start/tui 三套路径解析漂移
- ✅ Task 4: 在 `test_cli_init.py` 增加 3 个自动生成测试（生成/不覆盖/缺example）；在 `test_preflight.py` 更新 INFO 预期；在 `test_preflight_integration.py` 增加 missing_ato_yaml_does_not_skip_layer3 测试并修复 halt_in_layer2 触发方式（改用 bmad_config 缺失）；在 `test_cli_start_stop.py` 增加 8 个路径解析测试（_derive_project_root 3个 + _resolve_config_path 4个 + start 集成 1个）

### File List

- `src/ato/preflight.py` — 修改 `_check_ato_yaml()`: HALT → INFO
- `src/ato/cli.py` — 新增 `_ensure_ato_yaml()`, `_derive_project_root()`, `_resolve_config_path()`; 修改 `_init_async()`, `start_cmd()`, `_resolve_tui_config()`, `_HINTS[“ato_yaml”]`
- `tests/unit/test_preflight.py` — 更新 `test_missing_ato_yaml_returns_info`
- `tests/integration/test_preflight_integration.py` — 修改 `test_halt_in_layer2_skips_layer3` 触发方式, 新增 `test_missing_ato_yaml_does_not_skip_layer3`
- `tests/unit/test_cli_init.py` — 更新现有测试（增加 ato.yaml 到项目目录）, 新增 `TestInitAutoGenerateConfig` 类 (3 个测试)
- `tests/unit/test_cli_start_stop.py` — 新增 `TestDeriveProjectRoot` (3 个), `TestResolveConfigPath` (4 个), `TestStartDbPathProjectRoot` (1 个)
