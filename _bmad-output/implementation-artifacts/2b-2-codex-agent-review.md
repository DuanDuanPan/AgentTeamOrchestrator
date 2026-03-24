# Story 2B.2: 操作者可看到 Codex agent 执行审查并返回 findings

Status: ready-for-dev

## Story

As a 操作者,
I want 看到 Codex agent 被调度执行审查任务，findings 被结构化收集和记录,
So that 确认双 CLI 异构 agent 调用均正常工作。

## Acceptance Criteria

1. **AC1 — Codex CLI 命令构建**
   ```
   Given 需要调用 Codex CLI 执行审查任务
   When 调用 CodexAdapter.execute(prompt, options)
   Then 构建 codex exec "<prompt>" --json 命令
   And reviewer 角色默认使用 --sandbox read-only
   And 当调用方提供 schema 路径时追加 --output-schema <path>，最终消息通过 -o / --output-last-message 输出到文件
   And 通过 cwd=options["cwd"] 在指定 repo / worktree 中执行
   ```

2. **AC2 — JSONL 事件流解析与结构化验证**
   ```
   Given Codex CLI 调用完成
   When 解析 JSONL 事件流和 -o 输出文件
   Then 从 item.completed.item.text 提取最终文本结果，并兼容旧事件形态 item.content[].text
   And 从 turn.completed.usage 聚合 input_tokens / cached_input_tokens / output_tokens
   And 若 -o 输出文件为 JSON 则写入 structured_output，否则作为纯文本 fallback
   And 经 CodexOutput.model_validate() 验证输出结构
   And 使用 CODEX_PRICE_TABLE 从 token 数计算成本，记录到 cost_log（FR28）
   ```

3. **AC3 — Codex 成本计算**
   ```
   Given Codex 价格表（Architecture: Codex 成本价格表结构）
   When 计算成本
   Then uncached_input_tokens = max(input_tokens - cached_input_tokens, 0)
   And cost = uncached_input_tokens * price["input_per_1m"] / 1_000_000 + cached_input_tokens * price["cached_input_per_1m"] / 1_000_000 + output_tokens * price["output_per_1m"] / 1_000_000
   ```

4. **AC4 — Snapshot fixture 测试**
   ```
   Given Snapshot fixture 测试
   When 用 tests/fixtures/codex_output_*.json 和 codex_events_*.jsonl 执行解析
   Then 解析结果与 fixture 预期一致
   ```

5. **AC5 — 错误分类与重试**
   ```
   Given Codex CLI 进程超时或异常退出
   When 错误被 CLIAdapterError 分类
   Then 复用 ErrorCategory 分类体系（auth_expired / rate_limit / timeout / parse_error / unknown）
   And 自动重试 1 次（NFR8），重试仍失败则 escalate
   And 三阶段清理协议：SIGTERM → wait(5s) → SIGKILL → wait(2s)
   ```

## Tasks / Subtasks

- [ ] Task 1: 定义 CodexOutput 模型与价格表 (AC: #2, #3)
  - [ ] 1.1 在 `src/ato/models/schemas.py` 中定义 `CodexOutput(AdapterResult)` 子类：无 `model_usage`；添加 `cache_read_input_tokens: int = 0`（映射 Codex JSONL 的 `cached_input_tokens`，复用现有 telemetry 字段）和 `model_name: str | None = None`（从 JSONL 或 options 提取）
  - [ ] 1.2 实现 `CodexOutput.from_events(events, *, exit_code, output_file_content, model_name, cost_usd)` 类方法：从解析后的 JSONL 事件列表构建验证后的模型；兼容 `item.text` 与旧版 `item.content[].text` 两种消息结构；若 `output_file_content` 为 JSON 则映射到 `structured_output`
  - [ ] 1.3 在 `src/ato/adapters/codex_cli.py` 中定义 `CODEX_PRICE_TABLE: dict[str, dict[str, float]]` 常量，初始包含 `"codex-mini-latest"` 的 `input_per_1m` / `cached_input_per_1m` / `output_per_1m`
  - [ ] 1.4 实现 `calculate_cost(model: str, input_tokens: int, output_tokens: int, *, cached_input_tokens: int = 0) -> float` 函数：查表计算成本，未知模型返回 0.0 并 structlog 警告

- [ ] Task 2: 实现 CodexAdapter (AC: #1, #2, #5)
  - [ ] 2.1 在 `src/ato/adapters/codex_cli.py` 中实现 `CodexAdapter(BaseAdapter)` 类
  - [ ] 2.2 实现 `_build_command(prompt, options) -> list[str]`：构建 `codex exec <prompt> --json` 命令；支持 `sandbox`（默认 "read-only"）、`output_file`（`-o` 路径）、`output_schema`（`--output-schema` 路径）、`ephemeral`
  - [ ] 2.3 实现 `async execute(prompt, options, *, on_process_start) -> CodexOutput`：通过 `asyncio.create_subprocess_exec` 执行，支持 `cwd=(options or {}).get("cwd")`；解析 stdout JSONL，读取 `-o` 输出文件，计算成本；成功场景忽略 stderr 中的进度日志
  - [ ] 2.4 实现 `_parse_jsonl(stdout: str) -> list[dict]`：逐行 `json.loads`，跳过空行和非 JSON 行
  - [ ] 2.5 实现 `_aggregate_usage(events) -> tuple[int, int, int]`：从 `turn.completed` 事件聚合 `input_tokens`、`cached_input_tokens` 和 `output_tokens`
  - [ ] 2.6 实现 `_extract_text_result(events) -> str`：从最后一个 `item.completed`（type=agent_message）提取文本结果；优先使用当前 CLI 的 `item.text`，并兼容旧版 `item.content[].text`
  - [ ] 2.7 实现 `_parse_output_file(content: str) -> tuple[dict[str, Any] | None, str]`：`json.loads` 成功时返回结构化结果与文本；失败时返回 `structured_output=None` 与原始文本
  - [ ] 2.8 实现错误分类逻辑 `_classify_error(exit_code, stderr)`：复用与 Claude adapter 相同的分类策略；仅在非 0 exit 或解析失败时分类，不把成功场景的 stderr 进度输出误判为错误
  - [ ] 2.9 subprocess 调用全部在 `try/finally` 中执行，进程启动后触发 `on_process_start(proc)` 完成 PID 注册，超时/异常时调用 `cleanup_process()`

- [ ] Task 3: 创建 Snapshot fixture 与测试 (AC: #4)
  - [ ] 3.1 创建 `tests/fixtures/codex_events_success.jsonl`——成功审查的 JSONL 事件流 fixture（含 thread.started、turn.started、item.completed、turn.completed 事件；覆盖当前 CLI 的 `item.text` 与 `cached_input_tokens` 字段）
  - [ ] 3.2 创建 `tests/fixtures/codex_output_success.json`——成功审查的 `-o` 输出文件 fixture（含 findings 结构）
  - [ ] 3.3 创建 `tests/fixtures/codex_events_error.jsonl`——错误场景的 JSONL fixture
  - [ ] 3.4 实现 `tests/unit/test_codex_adapter.py`：fixture 解析测试、命令构建测试（含 `--output-schema`）、当前/旧版消息事件形态兼容测试、错误分类测试、execute mock 测试、cleanup 协议测试、缓存输入成本计算测试
  - [ ] 3.5 修改 `tests/unit/test_subprocess_mgr.py`：覆盖 CodexOutput 成功路径将 `model_name` 与 `cache_read_input_tokens` 正确写入 `cost_log`
  - [ ] 3.6 修改 `tests/unit/test_schemas.py`：补充 `CodexOutput` 的 model_validate / 默认值 / 结构化输出映射测试

- [ ] Task 4: 代码质量验证
  - [ ] 4.1 `uv run ruff check src/ato tests` — 0 errors
  - [ ] 4.2 `uv run mypy src/ato` — Success
  - [ ] 4.3 新模块测试全部通过
  - [ ] 4.4 `uv run pytest` — 全量通过，0 regressions

## Dev Notes

### 核心实现模式

**适配器隔离原则（ADR-08, NFR11）：**

CLI 参数构建 100% 封装在 adapter 层，与 ClaudeAdapter 完全对称。CodexAdapter 实现 BaseAdapter 接口，orchestrator core 通过 `SubprocessManager.dispatch(cli_tool="codex")` 调用，无需知道 Codex CLI 的具体参数。

**与 ClaudeAdapter 的关键差异：**

| 维度 | ClaudeAdapter | CodexAdapter |
|------|---------------|--------------|
| 命令格式 | `claude -p <prompt> --output-format json` | `codex exec <prompt> --json` |
| stdout 格式 | 单个 JSON 对象 | JSONL 事件流（多行，每行一个 JSON） |
| 结构化输出 | `--json-schema` + `structured_output` 字段 | `--output-schema` + `-o/--output-last-message` 输出文件 |
| 成本获取 | 直接读取 `total_cost_usd` | 从 token 数 × 价格表计算 |
| 回合控制 | `--max-turns N` | 无此参数，用 `asyncio.wait_for` 超时 |
| 权限控制 | `--allowedTools` / `--disallowedTools` | `--sandbox read-only\|workspace-write` |
| 缓存 token | `cache_read_input_tokens` | `cached_input_tokens`（映射到统一 telemetry 字段） |

**Codex JSONL 事件流格式（技术调研 + 2026-03-24 本机 `codex-cli 0.116.0` 实测）：**

```jsonl
{"type":"thread.started","thread_id":"uuid-xxx"}
{"type":"turn.started"}
{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"{\"ok\":true}"}}
{"type":"turn.completed","usage":{"input_tokens":26024,"cached_input_tokens":10624,"output_tokens":29}}
```

关键事件类型：
- `thread.started` — 包含 `thread_id`（可用作 session_id）
- `turn.completed` — 包含 `usage.input_tokens`、`usage.cached_input_tokens` 和 `usage.output_tokens`（**成本计算的唯一来源**）
- `item.completed` — 当前 CLI 在 `item.text` 提供最终消息；为兼容旧研究样本，adapter 也应支持 `item.content[].text`
- `error` — 错误事件

**成本计算实现（ADR-24 调整）：**

```python
CODEX_PRICE_TABLE: dict[str, dict[str, float]] = {
    "codex-mini-latest": {
        "input_per_1m": 1.50,
        "cached_input_per_1m": 0.375,
        "output_per_1m": 6.00,
    },
}

def calculate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    *,
    cached_input_tokens: int = 0,
) -> float:
    prices = CODEX_PRICE_TABLE.get(model)
    if prices is None:
        logger.warning("codex_unknown_model_price", model=model)
        return 0.0
    uncached_input_tokens = max(input_tokens - cached_input_tokens, 0)
    return (
        uncached_input_tokens * prices["input_per_1m"] / 1_000_000
        + cached_input_tokens * prices["cached_input_per_1m"] / 1_000_000
        + output_tokens * prices["output_per_1m"] / 1_000_000
    )
```

注意：
- 使用 `float` 而非 `Decimal`——与 `TaskRecord.cost_usd`、`CostLogRecord.cost_usd`、SQLite `REAL` 类型保持一致（Story 2B.1 已建立此约定）
- `uncached_input_tokens = input_tokens - cached_input_tokens` 是基于官方文档同时暴露 input/cached input 计价与 CLI 事件字段的实现推断；若未来 CLI 语义变化，应以 snapshot fixture 和官方文档为准调整

**命令构建参考：**

```python
cmd = ["codex", "exec", prompt, "--json"]
if sandbox:
    cmd.extend(["--sandbox", sandbox])  # "read-only" | "workspace-write"
if output_schema:
    cmd.extend(["--output-schema", str(output_schema)])
if output_file:
    cmd.extend(["-o", str(output_file)])  # alias of --output-last-message
# 不使用 shell=True！必须用 asyncio.create_subprocess_exec(*cmd, ...)
```

**`-o` 输出文件处理：**

`-o` 参数让 Codex 将 agent 最终消息写入指定文件。当需要结构化输出时，应与 `--output-schema` 配合使用，通过 `-o` 收集稳定 JSON。若 `output_file` 未指定但当前调用需要持久化最终消息，adapter 应在临时目录中创建目标文件并在解析后清理；不要使用 `tempfile.mktemp()`，优先 `tempfile.TemporaryDirectory()` / `tempfile.mkdtemp()` + 手动清理。

**错误分类逻辑：**

复用与 ClaudeAdapter 相同的 `_classify_error` 模式。Codex CLI 的 stderr 错误模式与 Claude 类似（认证/rate limit/超时），使用相同的关键字匹配策略。注意：Codex 成功执行时 stderr 会输出进度信息，不能因为 stderr 非空就判错；仅在非 0 exit、timeout 或解析失败时进入错误分类。可以直接在 `codex_cli.py` 中定义独立的 `_classify_error` 函数，或考虑提取到 `base.py`。推荐先在 `codex_cli.py` 中独立实现（与 Claude 保持结构对称），后续如果需要统一再重构。

**三阶段清理协议：**

直接复用 `from ato.adapters.base import cleanup_process`，与 ClaudeAdapter 完全一致。

**SubprocessManager 集成：**

SubprocessManager 已支持 `cli_tool="codex"` 的调度骨架，但成功路径仍需补齐 Codex telemetry 提取：
- `dispatch()` 的 `cli_tool` 参数接受 `Literal["claude", "codex"]`
- `CostLogRecord.cli_tool` 已支持 `"codex"`
- `TaskRecord.cli_tool` 已支持 `"codex"`
- 需要新增 `isinstance(result, CodexOutput)` 分支：提取 `model_name` 与 `cache_read_input_tokens`（来源于 Codex 的 `cached_input_tokens`）

**CodexOutput 模型需要提取 model_name：**

SubprocessManager.dispatch 成功路径会从 ClaudeOutput 提取 model_name：
```python
if isinstance(result, ClaudeOutput):
    cache_tokens = result.cache_read_input_tokens
    if result.model_usage and isinstance(result.model_usage, dict):
        model_name = result.model_usage.get("model")
```

对于 CodexOutput，需要类似机制。方案：在 CodexOutput 中添加 `model_name: str | None = None` 和 `cache_read_input_tokens: int = 0`，SubprocessManager 中添加对应的 `isinstance(result, CodexOutput)` 分支。模型名优先从事件读取，缺失时从 `options.get("model", "codex-mini-latest")` 回填；缓存 token 从 `turn.completed.usage.cached_input_tokens` 读取并映射到统一 telemetry 字段。

### 已有代码复用

**直接复用（不修改）：**
- `src/ato/adapters/base.py` → `BaseAdapter`, `ProcessStartCallback`, `cleanup_process`
- `src/ato/models/schemas.py` → `AdapterResult`（基类）, `CLIAdapterError`, `ErrorCategory`, `CostLogRecord`
- `src/ato/models/db.py` → `insert_cost_log()`, `get_cost_summary()`, `insert_task()`, `update_task_status()`
- `src/ato/subprocess_mgr.py` → `SubprocessManager`（已支持 `cli_tool="codex"`）
- `src/ato/config.py` → `RoleConfig`（含 `sandbox` 字段）, `TimeoutConfig.structured_job`
- `tests/conftest.py` → `db_path`, `initialized_db_path` fixtures

**需要扩展：**
- `src/ato/models/schemas.py` → 添加 `CodexOutput(AdapterResult)` 子类
- `src/ato/adapters/codex_cli.py` → 从 1 行 docstring 扩展为完整 Codex 适配器 + 价格表
- `src/ato/subprocess_mgr.py` → 在成功路径添加 `isinstance(result, CodexOutput)` 分支，提取 `model_name` 与 `cache_read_input_tokens`

**不要重复造轮：**
- ❌ 不要在 adapter 中自己写 SQLite 操作——cost_log 持久化由 SubprocessManager 负责
- ❌ 不要创建新的 Task model——使用已有的 `TaskRecord`
- ❌ 不要自己实现日志——使用 `structlog`
- ❌ 不要在 codex_cli.py 中直接调用 db.py——adapter 只负责 CLI 调用和输出解析
- ❌ 不要使用 `Decimal` 存储成本——统一使用 `float`（与 TaskRecord.cost_usd 一致）
- ❌ 不要修改 `ErrorCategory` 枚举——现有 5 个分类已覆盖 Codex 场景
- ❌ 不要修改 `BaseAdapter` 接口——CodexAdapter 必须实现完全相同的 `execute()` 签名

### Codex CLI 参数速查

| 参数 | 用途 | 本 story 使用场景 |
|------|------|-------------------|
| `codex exec <prompt>` | 非交互执行 | 所有调用 |
| `--json` | JSONL 事件流输出到 stdout | 所有调用（用于 token 统计和结果提取） |
| `--sandbox <mode>` | 沙箱模式 | `read-only`（reviewer）/ `workspace-write`（fixer） |
| `--output-schema <file>` | 约束最终消息符合 JSON Schema | 结构化 findings 输出 |
| `-o <path>` | 最终消息写入文件 | 收集 `--output-schema` 产出的结构化 review 输出 |
| `--full-auto` | 低摩擦自动化预设 | fixer 角色（等价于 `--ask-for-approval on-request --sandbox workspace-write`） |
| `--ephemeral` | 不持久化 session | 可选，减少磁盘写入 |

**Codex CLI 不支持的关键参数（必须了解）：**
- ❌ `--max-turns` → 用 `asyncio.wait_for(timeout)` 替代
- ❌ `--max-budget-usd` → 从 JSONL token 数自行计算
- ❌ `--allowedTools` / `--disallowedTools` → 用 `--sandbox` 三级沙箱替代
- ❌ `--resume` → Codex 用 `codex exec resume [SESSION_ID]`（不同语法）

### 测试策略

**测试文件结构与 ClaudeAdapter 对称：**

```
tests/
├── fixtures/
│   ├── codex_events_success.jsonl    # 成功审查的 JSONL 事件流
│   ├── codex_output_success.json     # 成功审查的 -o 输出
│   └── codex_events_error.jsonl      # 错误场景的 JSONL
├── unit/
│   └── test_codex_adapter.py         # Codex 适配器测试
```

**测试类结构（参照 test_claude_adapter.py）：**

| 测试类 | 覆盖范围 | 预估测试数 |
|--------|---------|-----------|
| `TestCodexOutputFromEvents` | JSONL 解析 → CodexOutput 构建 | 4 |
| `TestClassifyError` | 错误分类（如与 Claude 共用逻辑则可跳过） | 4-8 |
| `TestBuildCommand` | 命令构建（sandbox、`--output-schema`、-o、--json） | 4-6 |
| `TestCodexAdapterExecute` | execute() mock 测试（成功/失败/超时/回调） | 5 |
| `TestCalculateCost` | 价格表计算 + 未知模型 fallback | 3 |
| `TestParseJsonl` | JSONL 逐行解析 + 空行/非 JSON 容错 | 3 |
| `TestParseOutputFile` | JSON / 文本 fallback | 2-3 |
| 合计 | | ~24-30 |

**Mock 模式：**
- mock `asyncio.create_subprocess_exec` 返回 fixture 数据
- mock `-o` 输出文件通过 `tmp_path` fixture
- 不启动真实 Codex CLI

### 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/ato/adapters/codex_cli.py` | **重写** | CodexAdapter 完整实现 + CODEX_PRICE_TABLE + calculate_cost |
| `src/ato/models/schemas.py` | **修改** | 添加 `CodexOutput(AdapterResult)` 子类 |
| `src/ato/subprocess_mgr.py` | **修改** | 添加 `isinstance(result, CodexOutput)` 分支，提取 model / cache tokens 持久化到 `cost_log` |
| `tests/fixtures/codex_events_success.jsonl` | **新建** | 成功审查 JSONL fixture |
| `tests/fixtures/codex_output_success.json` | **新建** | 成功审查 -o 输出 fixture |
| `tests/fixtures/codex_events_error.jsonl` | **新建** | 错误场景 JSONL fixture |
| `tests/unit/test_codex_adapter.py` | **新建** | Codex 适配器单元测试 |
| `tests/unit/test_subprocess_mgr.py` | **修改** | Codex telemetry 持久化测试 |
| `tests/unit/test_schemas.py` | **修改** | `CodexOutput` 模型测试 |

**不应修改的文件：**
- `src/ato/adapters/base.py` — BaseAdapter 接口已满足需求
- `src/ato/adapters/claude_cli.py` — Claude 适配器与本 story 无关
- `src/ato/config.py` — `RoleConfig.sandbox` 已就绪
- `src/ato/state_machine.py` — 状态机与 adapter 无直接耦合
- `src/ato/models/db.py` — 无新表，cost_log 已支持 cli_tool="codex"
- `src/ato/models/migrations.py` — 无 schema 变更，SCHEMA_VERSION 保持 4

### JSONL 解析容错要求

Codex CLI 的 JSONL stdout 可能包含非 JSON 行（进度信息输出到 stderr，但实测偶有混入 stdout 的情况）。解析逻辑必须：
1. 逐行 `json.loads`
2. 空行跳过
3. 解析失败的行 structlog 警告后跳过（不中断整体解析）
4. 对 `item.completed` 同时兼容当前 `item.text` 与旧版 `item.content[].text` 结构
5. 返回成功解析的事件列表

### `-o` 输出文件生命周期

1. **创建**：adapter 在 execute 前生成临时目录并创建输出文件路径（`tempfile.TemporaryDirectory()` / `tempfile.mkdtemp()` + `Path(...) / "codex_output.json"`）；禁止直接使用 `tempfile.mktemp()`
2. **写入**：Codex CLI 进程将最终消息写入该文件
3. **读取**：进程完成后 adapter 读取文件内容
4. **解析**：尝试 `json.loads`，失败则作为纯文本
5. **清理**：adapter 在 `finally` 块中删除临时文件（即使解析失败也必须清理）

### Project Structure Notes

- `src/ato/adapters/codex_cli.py` 从 1 行 docstring stub 扩展为完整实现
- 模块依赖方向与 ClaudeAdapter 对称：`subprocess_mgr.py` → `adapters/codex_cli.py` → `adapters/base.py`
- 无 schema 变更，SCHEMA_VERSION 保持 4（上次由 Story 1-4a Preflight 升级到 4）
- `tests/fixtures/` 目录已存在（含 3 个 Claude fixture），本 story 补充 Codex fixture 文件
- `CodexOutput` 放在 `models/schemas.py`（与 `ClaudeOutput` 同级），不要在 `codex_cli.py` 中定义 Pydantic model

### 关键技术注意事项

1. **asyncio.create_subprocess_exec 不是 shell**——传参数列表，不拼接字符串，不用 `shell=True`
2. **structlog.contextvars 绑定**——由 SubprocessManager 在 dispatch 入口处理，adapter 内部不重复绑定
3. **pytest-asyncio auto mode**——`pyproject.toml` 已配置 `asyncio_mode=auto`
4. **Pydantic 宽松解析**——`CodexOutput` 继承 `AdapterResult`（`extra="ignore"`），不继承 `_StrictBase`
5. **临时文件不要用 `tempfile.mktemp()`**——存在竞态风险；优先 `tempfile.TemporaryDirectory()` / `tempfile.mkdtemp()` + 手动清理，或接收 options 中的路径
6. **JSONL 不是 JSON**——stdout 是多行，每行一个 JSON 对象，不要尝试 `json.loads(entire_stdout)`
7. **typing 兼容**——使用 `from __future__ import annotations`，类型注解用 `str | None` 格式
8. **成功场景的 stderr 不是错误信号**——Codex 会把进度写到 stderr；只用 exit code / timeout / 解析失败决定是否报错
9. **Codex CLI 认证**——自动化优先 `CODEX_API_KEY`；高级场景可复用 `~/.codex/auth.json`。adapter 不处理认证生命周期（由 preflight check 验证）
10. **模型名称**——Codex JSONL 事件流中不一定包含模型名，通过 `options.get("model", "codex-mini-latest")` 传入；价格表查找使用此值

### 依赖关系

**前置（已完成）：**
- ✅ Story 1.1：项目脚手架、structlog 配置
- ✅ Story 1.2：SQLite 持久化层、tasks/cost_log 表 CRUD
- ✅ Story 2B.1：BaseAdapter 接口、cleanup_process、ClaudeAdapter（提供完整参照模式）、SubprocessManager、AdapterResult 基类、ErrorCategory、CLIAdapterError、CostLogRecord

**后续依赖本 story：**
- Story 2B.3（BMAD 解析）消费 Codex 输出的 review Markdown/JSON
- Story 3.x（Convergent Loop）调度 Codex reviewer 执行全量 review
- Story 7.1（梯度降级）Claude fix 未收敛时自动切换到 Codex workspace-write 模式

**边界说明：**
- 本 story 实现 Codex adapter 对 `--output-schema` 的能力支持，但不负责创建 `schemas/review-findings.json` 正式产物；测试可用 `tmp_path` 下的临时 schema 文件验证命令与输出路径
- 生产工作流后续可在 Story 3.x 将真实 `schemas/review-findings.json` 传给 Codex reviewer

### References

- [Source: _bmad-output/planning-artifacts/epics.md — Epic 2B, Story 2B.2 (lines 625-649)]
- [Source: _bmad-output/planning-artifacts/architecture.md — Codex 约束与价格表 (lines 63-82)]
- [Source: _bmad-output/planning-artifacts/architecture.md — CLI subprocess 架构图 (lines 880-900)]
- [Source: _bmad-output/planning-artifacts/architecture.md — Decision 9: CLI Adapter 契约守护 (lines 425-434)]
- [Source: _bmad-output/planning-artifacts/architecture.md — 计划 fixture 文件: codex_review_jsonl.txt, codex_exec_output.json (lines 791-792)]
- [Source: _bmad-output/planning-artifacts/research/technical-claude-codex-cli-integration-research-2026-03-24.md — Codex CLI 参数验证、JSONL 事件结构、沙箱模式、成本计算]
- [Source: OpenAI Developers — Non-interactive mode (`codex exec`) — JSONL 事件流、stderr 进度、read-only 默认沙箱、`-o/--output-last-message`、`--output-schema`、`resume` 语法]
- [Source: OpenAI Developers — `codex-mini-latest` model page — `input` / `cached input` / `output` 定价]
- [Source: _bmad-output/planning-artifacts/prd.md — FR7 Codex CLI 子进程调用, FR28 成本记录, NFR8 自动重试, NFR11 Adapter 隔离, NFR14 错误处理]
- [Source: src/ato/adapters/base.py — BaseAdapter, ProcessStartCallback, cleanup_process]
- [Source: src/ato/adapters/claude_cli.py — ClaudeAdapter 完整实现（参照模式）]
- [Source: src/ato/models/schemas.py — AdapterResult, ClaudeOutput, ErrorCategory, CLIAdapterError, CostLogRecord]
- [Source: src/ato/subprocess_mgr.py — SubprocessManager dispatch/dispatch_with_retry, CLITool Literal]
- [Source: src/ato/config.py — RoleConfig.sandbox, TimeoutConfig.structured_job]
- [Source: _bmad-output/implementation-artifacts/2b-1-claude-agent-dispatch.md — 前置 story 完整实现细节与 dev notes]

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

### File List
