# Story 验证报告：3.1 Deterministic Validation & Finding Tracking

验证时间：2026-03-25 08:03:51 CST
Story 文件：`_bmad-output/implementation-artifacts/3-1-deterministic-validation-finding-tracking.md`
验证模式：`validate-create-story`
结果：PASS（已应用修正）

## 摘要

该 story 的主方向正确，但原文里有数个会直接误导 dev agent 的实现细节：

1. 它把 `blocking_threshold` 写到了错误的配置路径上。
2. 它把 nudge 写成了一个“随处可调的全局方法”，与当前仓库的进程内 / 进程外合同不一致。
3. 它要求“返回错误列表”，却指向 `jsonschema.validate()` 这种单异常 API。
4. 它让根目录 `schemas/` 同时走 `importlib.resources`，但当前 wheel 构建只打包 `src/ato`。
5. 它建议新增名为 `ValidationError` 的模型，和仓库里已大量使用的 `pydantic.ValidationError` 撞名。
6. 它要求用户能在 `ato status` 看到文案，但当前仓库尚未实现 `ato status` 命令，没有给出可落地的承载路径。

这些问题若不修，最常见的后果是：配置读错、nudge 调不通、schema 校验只报第一个错误、安装态找不到 schema 文件，以及 validation 模型命名混乱。

## 已核查证据

- 规划与 story 工件：
  - `_bmad-output/planning-artifacts/epics.md`
  - `_bmad-output/planning-artifacts/architecture.md`
  - `_bmad-output/planning-artifacts/prd.md`
  - `_bmad-output/implementation-artifacts/3-1-deterministic-validation-finding-tracking.md`
- 当前代码基线：
  - `src/ato/config.py`
  - `src/ato/models/schemas.py`
  - `src/ato/models/db.py`
  - `src/ato/models/migrations.py`
  - `src/ato/nudge.py`
  - `src/ato/state_machine.py`
  - `pyproject.toml`
- 本地环境检查：
  - `uv run python -c 'import jsonschema'` 当前失败，报 `ModuleNotFoundError`
- 外部主文档：
  - https://python-jsonschema.readthedocs.io/en/v4.13.0/validate/

## 发现的关键问题

### 1. 阈值配置路径写错

story 原文把阈值来源写成 `convergent_loop.blocking_threshold`。但当前代码中 `blocking_threshold` 定义在 `CostConfig`，实际路径是 `cost.blocking_threshold`。

已应用修正：
- 更新 AC4、关键设计约束、集成点，统一写为 `cost.blocking_threshold`。

### 2. approval / nudge 合同与当前仓库不一致

story 原文写“创建 approval 后调用 `nudge.notify()`”。但当前 `nudge.py` 明确区分：

- 进程内 writer：持有 `Nudge` 实例时调用 `notify()`
- 进程外 writer：调用 `send_external_nudge(orchestrator_pid)`

validation 模块本身并没有全局 nudge 实例，因此原文指导会把 dev 带向不可调用的接口。

已应用修正：
- 将 Task 5 改成“由调用方按上下文传入 `Nudge` 或 `orchestrator_pid`”。
- 更新 AC4 和集成点，明确进程内 / 进程外两条 transport。

### 3. JSON Schema API 指导错误

官方 `jsonschema` 文档中，`validate()` / `Validator.validate()` 在失败时抛单个 `ValidationError`；而 `iter_errors()` 才用于收集完整错误列表。

原 story 既要求“返回错误列表”，又指向 `jsonschema.validate()`，这会让实现和 AC 自相矛盾。

已应用修正：
- Task 2 改为 `Draft202012Validator.check_schema()` + `Draft202012Validator(schema).iter_errors()`。
- 单测要求也同步改成验证“返回完整 errors 列表”。

### 4. `schemas/` 路径与打包方式冲突

story 原文同时说：

- schema 文件放仓库根 `schemas/`
- 通过 `importlib.resources` 或相对路径加载

但当前 `pyproject.toml` 的 wheel 构建只打包 `src/ato`。这意味着根目录 `schemas/` 并不是 package resource，直接写 `importlib.resources` 会把 dev 带到一条走不通的路上。

已应用修正：
- Task 2 明确：MVP 按源码路径读取根目录 `schemas/`。
- 在 Project Structure Notes 中补充了后续若要支持安装态运行，需要再把 schema 收进包资源并调整 build 配置。

### 5. 新增 `ValidationError` 模型会与 Pydantic 撞名

仓库当前已经在多个模块和测试中直接导入 `pydantic.ValidationError`。如果再在 `models/schemas.py` 里新增内部模型也叫 `ValidationError`，会显著提高误导与导入混淆概率。

已应用修正：
- 将内部模型名改为 `SchemaValidationIssue`。
- 补充测试要求，显式守护这一命名边界。

### 6. “用户可见错误”缺少当前实现落点

story 原文要求用户能在 `ato status` 或 TUI 看到 `"Schema 验证失败，已退回修改"`，但当前仓库没有 `ato status` 命令。

如果不补充落地策略，dev 很可能会为了满足 AC 临时造新的状态字段或半成品 CLI。

已应用修正：
- AC1 补充了当前基线的实现要求：复用 validation task 的 `error_message`，并将 story 回退到 `creating`。
- Dev Notes 明确“不为此新增 story 表字段”。

## 已应用增强

- `review-findings.json` 的字段 shape 明确要求与 Story 2B.2 / 2B.3 输出保持一致，避免 adapter 与 validation 双份协议漂移。
- 测试组织调整为复用现有 `test_schemas.py` / `test_db.py` / `test_migrations.py`，避免为同一层职责再裂出一组平行测试文件。
- “不要创建 `convergent_loop.py`” 改成更准确的边界说明：该文件已存在占位，本 story 不负责实现其循环协议。

## 剩余风险

- 规划文档中仍有若干地方沿用“approvals 表由 Story 1.2 创建”的口径；当前 story 已按真实代码基线纠正，但同类表述在别的 story 中可能仍存在。
- 该 story 仍默认 schema 文件由人工维护。若后续发现 drift 风险高，建议把 boundary models 与 `model_json_schema()` 的生成链一并标准化。

## 最终结论

修正后，Story 3.1 已达到可交付给 dev agent 的质量门槛。关键实现歧义已被移除，当前版本能更准确地约束配置来源、schema 校验方式、nudge transport、错误承载路径以及测试落点。
