# Story 验证报告：8.5 ato init 自动生成配置文件并支持项目路径选择

验证时间：2026-03-28  
Story 文件：`_bmad-output/implementation-artifacts/8-5-init-auto-generate-config.md`  
验证模式：`validate-create-story`  
结果：PASS（已应用修正）

## 摘要

原始 8.5 草稿把方向说对了一半，但实现合同写得太“想当然”了。当前仓库里：

- `init_command()` 其实早就支持 `project_path`
- `run_preflight()` 其实早就负责创建 / 持久化 `.ato/state.db`
- 真正的 downstream 断裂点反而是 `ato start --db-path ...` 仍然按 `Path.cwd()` 找项目根和配置

本次验证后，story 已收敛为一个可直接执行的 init/preflight/start 合同，核心修正有 5 项：

1. 把“支持项目路径参数”从待开发项改成既有合同，避免开发者重复实现已经存在的功能。
2. 把“配置生成应在 DB 初始化之前”改正为符合当前代码的真实时序：preflight 已先落库，配置生成应发生在其后、成功提示之前。
3. 明确 `ato_yaml` 检查需要从 `HALT` 改为 `INFO`，否则 init 永远到不了自动生成逻辑，Layer 3 也会被错误跳过。
4. 补入 `ato start --db-path` 的项目根 / 配置发现一致性；否则用户即使成功自动生成了配置，也仍可能因为 cwd 不同而无法启动。
5. 把测试面从“补几个 init 测试”扩展到真实受影响的 preflight / integration / start-path 基线。

## 已核查证据

- 规划与规范工件：
  - `_bmad-output/planning-artifacts/architecture.md`
  - `_bmad-output/planning-artifacts/ux-design-specification.md`
- 前序相关 story：
  - `_bmad-output/implementation-artifacts/1-4a-preflight-check-engine.md`
  - `_bmad-output/implementation-artifacts/1-4b-ato-init-cli-ux.md`
  - `_bmad-output/implementation-artifacts/8-1-role-config-simplify-model-sandbox-optional.md`
  - `_bmad-output/implementation-artifacts/8-3-cost-control-optional.md`
  - `_bmad-output/implementation-artifacts/8-4-regression-test-multi-command.md`
- 当前代码：
  - `src/ato/cli.py`
  - `src/ato/preflight.py`
  - `src/ato/config.py`
  - `ato.yaml.example`
  - `tests/unit/test_cli_init.py`
  - `tests/unit/test_preflight.py`
  - `tests/integration/test_preflight_integration.py`
  - `tests/unit/test_cli_tui.py`

## 发现的关键问题

### 1. 原稿把 `project_path` 当成缺失能力，但它其实已经实现了

当前 `src/ato/cli.py::init_command()` 已经有：

- `project_path: Path = typer.Argument(".", ...)`
- 默认 DB 路径 `<project>/.ato/state.db`

原稿仍把“确认现有 `project_path` 参数已支持路径指定”写成主要任务，会误导开发者去重复实现现有能力，而忽略真正的断点。

已应用修正：

- 把 `project_path` 支持收敛成既有合同
- 把 story 重点转向 auto-generate 与 downstream path consistency

### 2. 原稿对 init 时序的描述与现有代码不符

原稿写：

- “配置生成应在 preflight 检查之后、DB 初始化之前”

但当前真实代码里：

- `run_preflight()` 在所有检查完成后就会 `init_db(db_path)` 并持久化 `preflight_results`
- `_init_async()` 只是调用 `run_preflight()`、渲染结果、判断 HALT、最后输出成功提示

因此如果开发者按原稿理解，很可能会试图把 DB 初始化从 `run_preflight()` 挪出去，直接破坏 Story 1.4a / 1.4b 的既有合同。

已应用修正：

- 明确 DB 初始化 / 持久化仍归 `run_preflight()` 所有
- 配置生成应放在 preflight 落库之后、成功提示之前

### 3. 原稿没有意识到：`ato_yaml` 仍是 HALT 时，自动生成逻辑根本到不了

当前代码中：

- `_check_ato_yaml()` 返回 `HALT`
- `run_preflight()` 在 Layer 2 出现 HALT 时会跳过 Layer 3
- `_init_async()` 看到任意 HALT 就直接 exit code 2

这意味着如果不先把 `ato_yaml` 从 `HALT` 调成 `INFO`，再多的“自动复制模板”逻辑也永远不会执行。

已应用修正：

- AC1 明确把 `ato_yaml` 改成 `INFO`
- Task 1 把 preflight status/message 与 integration test 一起纳入范围

### 4. 原稿漏掉了真正的 downstream 断裂点：`ato start --db-path` 仍按 cwd 找配置

当前 `src/ato/cli.py::start_cmd()` 仍然：

- `run_preflight(Path.cwd(), resolved_db, include_auth=False)`
- 默认 `resolved_config = Path("ato.yaml")`

这意味着用户即使执行了：

- `ato init /path/to/project`

只要后续在另一个 cwd 里跑：

- `ato start --db-path /path/to/project/.ato/state.db`

就仍可能找错项目根 / 找不到刚生成的 `ato.yaml`。

已应用修正：

- Story Task 3 明确把 start-time project root / config discovery 纳入合同
- 参考现有 `_resolve_tui_config()` 的 db_path-based 策略，而不是继续依赖 cwd

### 5. 原稿低估了测试与共享模板的真实联动面

原稿只列了几条 init 测试，但真实会受影响的基线包括：

- `tests/unit/test_preflight.py`：当前断言缺少 `ato.yaml` 返回 HALT
- `tests/integration/test_preflight_integration.py`：当前断言 Layer 2 HALT 会跳过 Layer 3
- `tests/unit/test_cli_init.py`：当前 fixture / summary 文案围绕 “ato.yaml 已找到”
- `ato.yaml.example`：它还是 8.1 / 8.3 / 8.4 的共享文件与加载基线

如果 story 不写清这些，dev 很容易只补新测试，不更新旧基线，最后在套件里才发现冲突。

已应用修正：

- Task 4 明确扩展 preflight / integration / init / start-path 现有测试
- Dev Notes 补充 Epic 8 共享模板文件冲突提示

## 已应用增强

- 补回了 create-story 模板里的 validation note 注释
- 增加了 `Scope Boundary`，明确这不是“让 ato.yaml 对所有命令都变成可选”
- 增加了 `Previous Story Intelligence`，把 Story 1.4a / 1.4b 的 init 时序合同显式写回 story
- 增加了 `Change Log`、`Suggested Verification` 与 `Dev Agent Record`

## 剩余风险

- 上游 architecture / UX 文档当前仍把“项目根缺少 `ato.yaml`”描述为 Layer 2 的 HALT；8.5 实现后，这些高层文档应补做一次同步，否则文档真源会出现轻微漂移。
- 本 story 只把 init 与 `ato start --db-path` 的项目根一致性补齐，并没有顺手扩展到所有 CLI 子命令；这是一条有意识的 scope boundary，而不是遗漏。
- 本次只修订了 story 与 validation report，没有实现 Python 代码，也没有运行测试；目标是先把 dev-story 的实现合同修正到与当前仓库真实行为一致。

## 最终结论

修正后，8.5 已从“方向正确但现有代码理解有偏差”的草稿，收敛成一个可直接交给 dev-story 执行的 story。高风险误导点已经移除：不会再重复实现已有的 `project_path` 参数，不会再错误改写 init 的 DB 持久化时序，也不会再漏掉 `ato start --db-path` 这条真正会让 auto-generated config 失效的 downstream 路径。
