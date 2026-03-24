---
project_name: 'AgentTeamOrchestrator'
user_name: 'Enjoyjavapan163.com'
date: '2026-03-24'
sections_completed: ['technology_stack', 'language_rules', 'framework_rules', 'testing_rules', 'code_quality', 'workflow_rules', 'critical_dont_miss']
status: 'complete'
rule_count: 68
optimized_for_llm: true
---

# Project Context for AI Agents

_本文件包含 AI agent 在本项目中实现代码时必须遵循的关键规则和模式。聚焦于 agent 容易遗漏的非显而易见的细节。_

---

## 技术栈与版本

| 技术 | 版本约束 | 关键说明 |
|------|---------|---------|
| Python | ≥3.11 | 硬性要求，依赖 asyncio.TaskGroup |
| uv | 最新 | 包管理器，`uv run` 执行所有命令 |
| hatchling | 默认 | uv 推荐的构建后端 |
| python-statemachine | ≥3.0 | 3.0 全新 API（statechart + async + PersistentModel） |
| Pydantic | ≥2.0 | v2 API（model_validate，非 parse_obj） |
| aiosqlite | ≥0.22 | SQLite WAL 异步访问 |
| Textual | ≥2.0 | TUI 框架 |
| typer | 最新 | CLI 入口 |
| structlog | 最新 | 核心依赖（非 dev），JSON 结构化日志 |
| ruff | 最新 | lint + format（Astral 生态） |
| mypy | 最新 | strict mode 类型检查 |
| pre-commit | 最新 | ruff + mypy hooks |

### 版本陷阱

- python-statemachine 3.0 与 2.x API 不兼容——网上大量 2.x 示例，agent 必须使用 3.0 async API
- Pydantic v2 使用 `model_validate()` 而非 v1 的 `parse_obj()` / `.from_orm()`
- 无 ANTHROPIC_API_KEY——Claude CLI 必须使用 OAuth 模式（非 `--bare`），`claude -p` 调用时 BMAD skills 自动加载
- Codex CLI 无 `--max-turns`、无直接成本字段、无工具级权限控制

## 关键实现规则

### Python 语言规则

**asyncio 模式：**
- 用 `asyncio.TaskGroup` 管理并发任务，禁止用 `asyncio.gather`
- 用 `asyncio.create_subprocess_exec` 启动子进程，禁止 `shell=True`
- 用 `asyncio.wait_for` 控制超时（Codex 无 `--max-turns`）
- subprocess 调用必须在 `try/finally` 中使用三阶段清理协议：`proc.terminate()` → `wait(5s)` → `proc.kill()` → `wait()`
- 禁止在 SQLite 写事务中 await 外部 IO

**类型系统：**
- 所有公共函数必须有完整类型标注（参数 + 返回值）
- mypy strict mode 启用
- 用 `Literal` 表达领域枚举：`severity: Literal["blocking", "suggestion"]`

**错误处理：**
- 异常层次：`ATOError` → `CLIAdapterError` / `StateTransitionError` / `RecoveryError` / `ConfigError`
- 错误向上传播直到遇到能处理它的层，禁止中间层静默吞掉异常（至少 `structlog.warning`）
- adapter 层将 CLI 原始错误分类为 `ErrorCategory`，包装为 `CLIAdapterError`
- subprocess_mgr 捕获后重试 1 次，仍失败则通过 TransitionQueue escalate
- 用 `typer.Exit(code=N)` 退出，禁止 `sys.exit()`

**导入与模块：**
- 模块依赖只允许向下：`adapters/` 不依赖 `core`，`tui/` 不依赖 `core`（通过 SQLite 解耦）
- 公共接口通过 `__init__.py` 显式导出，不导出内部函数
- Pydantic models 统一定义在 `models/schemas.py`，禁止在 `models/` 外定义
- 迁移函数放 `models/migrations.py`（非 `db.py`）

### 框架特定规则

**python-statemachine 3.0 async 集成：**
- 创建后必须 `await sm.activate_initial_state()`（async 模式 `__init__` 不能 await）
- PersistentModel setter 不直接写 SQLite——只更新内存状态
- TransitionQueue consumer 在 `send()` 返回后显式持久化：`await sm.send(event)` → `await save_story_state(db, story_id, sm.current_state)` → `await db.commit()`
- 单线程约束：所有状态机操作在同一事件循环线程，不跨线程共享 sm 实例
- 优先用 `StateMachine`（2.x 兼容默认值），除非需要 compound/parallel states

**Pydantic v2 验证模式：**
- MVP 全部走 `model_validate()`，禁止使用 `model_construct`（Growth 再评估热路径）
- 外部输入（CLI 输出）严格验证，配置加载宽松 + 自定义 validator
- 用 `model_json_schema()` 自动生成 `schemas/` 下的 JSON Schema 文件
- 禁止在 Pydantic validator 中做 IO 操作

**SQLite / aiosqlite：**
- `PRAGMA journal_mode=WAL` + `busy_timeout=5000` + `synchronous=NORMAL` 在每个连接上设置
- TransitionQueue consumer 用长连接，Orchestrator 轮询和 TUI 用短连接 + 立即 commit
- 写事务尽可能短——读数据、处理逻辑、然后单次写入 + commit
- 参数化查询，禁止手动拼接 SQL

**structlog：**
- 核心依赖（非 dev），JSON 输出到 `.ato/logs/ato.log`
- 用 `structlog.contextvars.bind_contextvars(story_id=..., phase=...)` 在每个 Task 入口绑定上下文
- 禁止用 `print()` 输出日志
- MVP 单文件 append 模式，不做轮转

**Textual TUI：**
- `compose()` 定义结构，`on_mount()` 初始化数据——禁止在 `__init__` 中读 SQLite
- reactive 属性驱动 UI 更新
- CSS 与 Python 分离：`tui/app.tcss`
- `set_interval(2.0, self.refresh_data)` 定期轮询
- TUI 写入 SQLite 后立即 commit + 发送 nudge

**Typer CLI：**
- 退出码：0 成功 / 1 一般错误 / 2 环境错误
- 错误信息输出到 stderr：`typer.echo(msg, err=True)`
- `ato status --json` 结构化 JSON 输出到 stdout

### 测试规则

**测试组织：**
- `tests/unit/test_<module>.py` — 单元测试
- `tests/integration/test_<feature>.py` — 集成测试
- `tests/smoke/test_cli_contract.py` — CLI 冒烟测试（真实 CLI 调用）
- `tests/fixtures/<cli>_<scenario>.json` — CLI 输出 snapshot

**测试策略：**
- 单元测试禁止调用真实 CLI——使用 `tests/fixtures/` 下的 snapshot fixture
- 冒烟测试用于 CLI 升级前验证输出格式未变，更新 fixture
- `pyproject.toml [tool.ato]` 记录已验证的 CLI 版本号，冒烟测试比对当前版本
- `pytest-asyncio` 配置 `asyncio_mode=auto`

**状态机测试覆盖（Decision 8）：**
- 每个 transition 至少执行 1 次（~20 个单元测试）
- 4 条关键路径集成测试：Happy path / CL review-fix 循环 / 崩溃恢复 / 非法 transition 拒绝
- 崩溃恢复用函数式测试（构造数据库状态），不需要真实杀进程

**CLI Adapter 契约守护（Decision 9）：**
- Snapshot fixture（CI 快速运行）：保存真实 CLI 输出为 JSON fixture，adapter 解析测试基于 fixture
- 冒烟测试（手动触发）：`--max-turns 1`、`--max-budget-usd 0.10` 最小 CLI 调用
- CLI 版本升级前必须先跑冒烟测试更新 fixture

**TUI 测试：**
- 使用 Textual `pilot` + mock SQLite 数据
- CLI 命令测试用 `typer.testing.CliRunner` 验证退出码和输出

### 代码质量与风格规则

**命名规范：**

| 范围 | 规则 | 示例 |
|------|------|------|
| SQLite 表名 | snake_case 复数 | `stories`, `findings`, `approvals`, `cost_log` |
| SQLite 列名 | snake_case | `story_id`, `created_at`, `cost_usd` |
| Python 模块/函数/变量 | PEP 8 snake_case（ruff 强制） | `transition_queue.py`, `def submit_transition()` |
| Python 类名 | PascalCase | `StoryLifecycle`, `TransitionQueue` |
| Pydantic 模型 | PascalCase + 用途后缀 | `FindingRecord`, `ApprovalRequest`, `ClaudeOutput` |
| 配置键 (ato.yaml) | snake_case | `max_concurrent_agents`, `convergent_loop.max_rounds` |
| structlog 字段 | snake_case | `story_id`, `round_num`, `cost_usd` |
| CLI 命令 | kebab-case（typer 默认） | `ato batch-select`, `ato submit` |
| JSON Schema 属性 | snake_case | `"severity"`, `"finding_id"` |
| 自定义异常 | PascalCase + Error 后缀 | `CLIAdapterError`, `StateTransitionError` |

**代码组织：**
- 常量：模块级 `UPPER_SNAKE_CASE`，跨模块常量在 `models/schemas.py`
- 配置访问：通过 Pydantic `Settings` 对象传递，不直接读 YAML
- 公共接口通过 `__init__.py` 显式导出

**质量门控：**
- `ruff check` + `ruff format` + `mypy` 全部通过后再提交
- 所有新模块必须有对应的单元测试文件
- CLI adapter 返回值必须经过 Pydantic `model_validate`

### 开发工作流规则

**项目结构：**
- 主包：`src/ato/`（ato = Agent Team Orchestrator）
- 子包：`adapters/`、`models/`、`tui/`（含 `widgets/` 子目录）
- 运行时目录：`.ato/`（state.db、orchestrator.pid、logs/）——加入 `.gitignore`
- 配置文件：`ato.yaml`（项目级），`ato.yaml.example`（模板）
- JSON Schema：`schemas/` 目录

**进程模型：**
- `ato start` → Orchestrator 后台进程，写 PID 到 `.ato/orchestrator.pid`
- `ato tui` → TUI 前台进程，独立于 Orchestrator
- TUI 崩溃不影响 Orchestrator 运行
- 所有外部写入（TUI 审批、`ato submit`）走统一 nudge 机制

**SQLite Schema 迁移：**
- `PRAGMA user_version` 追踪 schema 版本号
- `ato start` 时自动检查并执行迁移
- 迁移函数在 `models/migrations.py`，按序执行
- 表按需创建：`CREATE TABLE IF NOT EXISTS`（findings、cost_log、preflight_results 等在首次使用的 story 中创建）

**CLI Adapter 模式：**
- Claude CLI：`claude -p "<prompt>" --output-format json --max-turns <N>`（OAuth 模式）
- Codex CLI：`codex exec "<prompt>" --json`（reviewer 用 `--sandbox read-only`，结果通过 `-o` 输出）
- Codex 成本计算：`CODEX_PRICE_TABLE` dict 常量在 `adapters/codex_cli.py`
- 所有 CLI 命令构建在 adapter 内部，禁止在 adapter 外拼接

**优雅停止与崩溃恢复（Decision 7）：**
- `ato stop` → 将 running tasks 标记为 `paused`
- `ato start` 检测 `status=running` → 崩溃恢复；`status=paused` → 正常恢复
- 崩溃恢复分类：PID 存活→重新监听 / artifact 存在→继续流水线 / Structured Job 无 artifact→重调度 / Interactive Session→需人工决策

### 反模式清单（禁止事项）

**asyncio / subprocess：**
- ❌ 不要用 `asyncio.gather`（用 `TaskGroup`）
- ❌ 不要用 `shell=True` 启动子进程
- ❌ 不要在 adapter 外直接拼接 CLI 命令
- ❌ 不要在 SQLite 写事务中 await CLI 调用
- ❌ 不要跨线程共享状态机实例

**数据层：**
- ❌ 不要在非 `models/` 目录定义 Pydantic model
- ❌ 不要手动拼接 SQL（用参数化查询）
- ❌ 不要在 PersistentModel setter 中直接写 SQLite（consumer 显式持久化）
- ❌ 不要在 Pydantic validator 中做 IO 操作
- ❌ MVP 不要使用 `model_construct`（Growth 再评估）

**日志 / UI：**
- ❌ 不要用 `print()` 输出日志（用 structlog）
- ❌ 不要在 `except` 中静默吞掉异常（至少 `structlog.warning`）
- ❌ 不要在 Textual `__init__` 中读 SQLite（用 `on_mount`）

**测试：**
- ❌ 不要在测试中直接调用真实 CLI（用 fixture，冒烟测试除外）

### 安全规则

- subprocess 必须用 `asyncio.create_subprocess_exec`，禁止 `shell=True`（防命令注入）
- SQLite 全部参数化查询（防 SQL 注入）
- CLI adapter 输出经 Pydantic 验证后才消费（防畸形输入）
- `async with aiosqlite.connect()` 块内禁止 await 外部 IO（防写锁长期持有）

---

## 使用指南

**AI Agent：**
- 实现任何代码前先读本文件
- 严格遵循所有规则
- 有疑问时选择更严格的选项
- 发现新模式时更新本文件

**人类维护者：**
- 保持本文件精简，聚焦 agent 需要的信息
- 技术栈变更时同步更新
- 定期审查移除过时规则

最后更新：2026-03-24

