# Story 1.1: 项目脚手架与开发工具链

Status: done

## Story

As a 开发者,
I want 用 `uv init` 初始化项目并配置完整的开发工具链,
So that 项目结构就绪、依赖可安装、代码质量工具可运行。

## Acceptance Criteria

1. **AC1: 核心依赖安装**
   - Given 一个空的项目目录
   - When 执行 `uv init` 并安装所有依赖
   - Then pyproject.toml 包含所有核心依赖（aiosqlite, python-statemachine>=3.0, textual>=2.0, pydantic>=2.0, typer, structlog）和开发依赖（pytest, pytest-asyncio, ruff, mypy, pre-commit）
   - And `uv.lock` 生成并可提交到 VCS

2. **AC2: 目录结构完整**
   - Given 项目已初始化
   - When 检查目录结构
   - Then 存在完整的 `src/ato/` 包结构（含 `adapters/`, `models/`, `tui/` 子包及所有 `__init__.py`），`tests/`（含 `unit/`, `integration/`, `smoke/`, `fixtures/`），`schemas/` 目录

3. **AC3: 代码质量工具通过**
   - Given 开发工具链已配置
   - When 执行 `uv run ruff check src/` 和 `uv run mypy src/`
   - Then 所有检查通过，无错误

4. **AC4: Pre-commit Hooks**
   - Given pre-commit 已配置
   - When 执行 `uv run pre-commit run --all-files`
   - Then ruff check + ruff format + mypy hooks 全部通过

5. **AC5: 结构化日志配置**
   - Given structlog 已配置
   - When 导入 `ato.logging` 模块并调用 `configure_logging()`
   - Then structlog 以 JSON 格式输出到 stderr，包含 ISO 时间戳和日志级别

6. **AC6: pyproject.toml 工具配置完整**
   - Given pyproject.toml 配置完整性
   - When 检查工具配置
   - Then 包含 `[tool.ruff]`（规则集、行宽、目标 Python 版本）、`[tool.mypy]`（strict mode、忽略缺失 import）、`[tool.pytest.ini_options]`（asyncio_mode=auto）、`[tool.ato]`（CLI 版本追踪占位）
   - And `.pre-commit-config.yaml` 包含 ruff-pre-commit + mypy hooks 的具体版本锁定

## Tasks / Subtasks

- [x] Task 1: 项目初始化 (AC: #1, #6)
  - [x] 1.1 在仓库根目录执行 `uv init --package --build-backend hatch --name agent-team-orchestrator --python 3.11 .`，不要创建嵌套 `agent-team-orchestrator/` 子目录
  - [x] 1.2 保留 uv 生成的 `README.md` 与 `.python-version`，并将默认生成的 `src/agent_team_orchestrator/` 替换为 `src/ato/`
  - [x] 1.3 配置 `pyproject.toml`（保留 project 名称 `agent-team-orchestrator`，将 `[project.scripts]` 入口改为 `ato = "ato.cli:app"`）
  - [x] 1.4 安装核心依赖：`uv add aiosqlite "python-statemachine>=3.0" "textual>=2.0" "pydantic>=2.0" typer structlog`
  - [x] 1.5 安装开发依赖：`uv add --group dev pytest pytest-asyncio ruff mypy pre-commit`
  - [x] 1.6 配置 `[tool.ruff]`：line-length=100, target-version="py311", select 包含 E/F/W/I/N/UP/B/A/SIM/RUF
  - [x] 1.7 配置 `[tool.mypy]`：strict=true, warn_return_any=true, ignore_missing_imports=true
  - [x] 1.8 配置 `[tool.pytest.ini_options]`：asyncio_mode="auto"
  - [x] 1.9 添加 `[tool.ato]` 占位节（claude_cli_version = "", codex_cli_version = ""）
  - [x] 1.10 确认 `uv.lock` 已生成
  - [x] 1.11 确认 `uv run ato --help` 可执行并返回退出码 0

- [x] Task 2: 创建完整目录结构 (AC: #2)
  - [x] 2.1 创建 `src/ato/__init__.py`（含 `__version__ = "0.1.0"`）
  - [x] 2.2 创建 `src/ato/cli.py`（typer App 骨架，含 `app = typer.Typer(name="ato")`）
  - [x] 2.3 创建 `src/ato/core.py`（空模块，docstring 占位）
  - [x] 2.4 创建 `src/ato/state_machine.py`（空模块）
  - [x] 2.5 创建 `src/ato/transition_queue.py`（空模块）
  - [x] 2.6 创建 `src/ato/subprocess_mgr.py`（空模块）
  - [x] 2.7 创建 `src/ato/convergent_loop.py`（空模块）
  - [x] 2.8 创建 `src/ato/recovery.py`（空模块）
  - [x] 2.9 创建 `src/ato/config.py`（空模块）
  - [x] 2.10 创建 `src/ato/nudge.py`（空模块）
  - [x] 2.11 创建 `src/ato/preflight.py`（空模块）
  - [x] 2.12 创建 `src/ato/adapters/__init__.py`
  - [x] 2.13 创建 `src/ato/adapters/base.py`（空模块）
  - [x] 2.14 创建 `src/ato/adapters/claude_cli.py`（空模块）
  - [x] 2.15 创建 `src/ato/adapters/codex_cli.py`（空模块）
  - [x] 2.16 创建 `src/ato/adapters/bmad_adapter.py`（空模块）
  - [x] 2.17 创建 `src/ato/models/__init__.py`
  - [x] 2.18 创建 `src/ato/models/schemas.py`（空模块）
  - [x] 2.19 创建 `src/ato/models/db.py`（空模块）
  - [x] 2.20 创建 `src/ato/models/migrations.py`（空模块）
  - [x] 2.21 创建 `src/ato/tui/__init__.py`
  - [x] 2.22 创建 `src/ato/tui/app.py`（空模块）
  - [x] 2.23 创建 `src/ato/tui/app.tcss`（空 CSS）
  - [x] 2.24 创建 `src/ato/tui/dashboard.py`（空模块）
  - [x] 2.25 创建 `src/ato/tui/approval.py`（空模块）
  - [x] 2.26 创建 `src/ato/tui/story_detail.py`（空模块）
  - [x] 2.27 创建 `src/ato/tui/widgets/__init__.py`
  - [x] 2.28 创建 `tests/conftest.py`
  - [x] 2.29 创建 `tests/unit/`（空 `__init__.py`）
  - [x] 2.30 创建 `tests/integration/`（空 `__init__.py`）
  - [x] 2.31 创建 `tests/smoke/`（空 `__init__.py`）
  - [x] 2.32 创建 `tests/fixtures/`（空目录，含 `.gitkeep`）
  - [x] 2.33 创建 `schemas/`（空目录，含 `.gitkeep`）
  - [x] 2.34 更新 `.gitignore`（追加 `.ato/` 运行时目录、`__pycache__`、`.mypy_cache`、`.ruff_cache`）

- [x] Task 3: structlog 日志配置 (AC: #5)
  - [x] 3.1 创建 `src/ato/logging.py`，实现 `configure_logging(log_dir: str | None = None, debug: bool = False) -> None`
  - [x] 3.2 显式接入 Python `logging`，默认 JSON 日志输出到 stderr；不要依赖 structlog 默认 `PrintLoggerFactory`
  - [x] 3.3 当传入 `log_dir` 时，确保目录存在并追加写入 `<log_dir>/ato.log`
  - [x] 3.4 配置 processors 链：`merge_contextvars` → `add_log_level` → `TimeStamper(fmt="iso")` → `StackInfoRenderer` → `format_exc_info` → `JSONRenderer`
  - [x] 3.5 使用 `structlog.stdlib.LoggerFactory()`（或等价 stdlib 集成）确保处理器链与 handler 一致
  - [x] 3.6 使用 `structlog.make_filtering_bound_logger()` 作为 `wrapper_class`，根据 debug 参数选择 DEBUG/INFO 级别
  - [x] 3.7 设置 `cache_logger_on_first_use=True`

- [x] Task 4: Pre-commit 配置 (AC: #4)
  - [x] 4.1 创建 `.pre-commit-config.yaml`，包含 ruff-pre-commit hook（ruff check + ruff format）
  - [x] 4.2 添加 mypy hook（使用 mirrors-mypy，含 `additional_dependencies` 配置）
  - [x] 4.3 锁定 hook 具体版本号（2026-03-24 校验值：`ruff-pre-commit v0.14.10`、`mirrors-mypy v1.19.1`）
  - [x] 4.4 执行 `uv run pre-commit install` 安装 hooks

- [x] Task 5: 验证与测试 (AC: #1, #2, #3, #4, #5)
  - [x] 5.1 创建 `tests/unit/test_logging.py`：验证 `configure_logging()` 输出 JSON 到 stderr，且在传入 `log_dir` 时写入 `<log_dir>/ato.log`
  - [x] 5.2 执行 `uv run ato --help` 通过
  - [x] 5.3 执行 `uv run ruff check src/` 通过
  - [x] 5.4 执行 `uv run mypy src/` 通过
  - [x] 5.5 执行 `uv run pytest` 通过
  - [x] 5.6 执行 `uv run pre-commit run --all-files` 通过

## Dev Notes

### 关键架构约束

- **Python ≥3.11** 硬性要求（asyncio.TaskGroup 依赖）
- **uv** 作为唯一包管理器（Astral 生态系统，与 ruff 一致）
- **在当前仓库根目录初始化** — 使用 `uv init ... .`；不要在仓库内再创建 `agent-team-orchestrator/` 子目录
- **`uv init --package` 必须启用** — 否则不会生成 `src/` 布局，和本 story 目标结构冲突
- **hatchling** 作为构建后端（uv 默认推荐）
- **无 ANTHROPIC_API_KEY** — Claude CLI 必须使用 OAuth 模式（`claude -p`，不带 `--bare`）
- **BMAD skills 不可修改** — 适配层用 LLM 语义解析
- **本地单用户单进程** — 不需分布式协调

### 异常层次设计（占位，后续 story 实现）

```
ATOError (基类)
├── CLIAdapterError
├── StateTransitionError
├── RecoveryError
└── ConfigError
```

### 模块依赖规则

- 只允许向下依赖：`adapters/` 不依赖 `core`，`tui/` 不依赖 `core`（通过 SQLite 解耦）
- 公共接口通过 `__init__.py` 显式导出
- Pydantic 模型统一定义在 `models/schemas.py`
- 迁移函数在 `models/migrations.py`（不在 db.py）
- 常量：模块级 `UPPER_SNAKE_CASE`，跨模块常量在 `models/schemas.py`

### structlog 配置参考实现

```python
import structlog
import logging
import sys

def configure_logging(log_dir: str | None = None, debug: bool = False) -> None:
    """ATO 标准日志配置。"""
    logging.basicConfig(
        format="%(message)s",
        level=logging.DEBUG if debug else logging.INFO,
        stream=sys.stderr,
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,       # 协程级上下文
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),            # 统一 JSON 输出
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.DEBUG if debug else logging.INFO
        ),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
```

- 协程级上下文绑定：在 Task 入口调用 `structlog.contextvars.bind_contextvars(story_id=..., phase=...)`
- `log_dir` 非空时，再追加一个 `logging.FileHandler(Path(log_dir) / "ato.log", mode="a")`
- 不要依赖默认 `PrintLoggerFactory`；默认 print logger 走 stdout，不满足本 story 的 stderr 约束
- MVP：单文件 `.ato/logs/ato.log` append 模式（不做轮转）
- 禁止使用 `print()` 进行日志输出

### CLI 入口骨架（src/ato/cli.py）

```python
import typer

app = typer.Typer(name="ato", help="Agent Team Orchestrator")

# 后续 story 将添加：init, start, stop, tui, plan, batch, cost, logs 等子命令
```

- 顶层命令使用 kebab-case；分组子命令使用空格路径，如 `ato batch select`、`ato batch status`
- 退出码：0（成功）、1（一般错误）、2（环境错误）
- 错误输出到 stderr：`typer.echo(msg, err=True)`
- 使用 `typer.Exit(code=N)` 退出（不用 sys.exit）

### pyproject.toml 配置要点

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "agent-team-orchestrator"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "aiosqlite",
    "python-statemachine>=3.0",
    "textual>=2.0",
    "pydantic>=2.0",
    "typer",
    "structlog",
]

[project.scripts]
ato = "ato.cli:app"

[dependency-groups]
dev = [
    "pytest",
    "pytest-asyncio",
    "ruff",
    "mypy",
    "pre-commit",
]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "W", "I", "N", "UP", "B", "A", "SIM", "RUF"]

[tool.mypy]
strict = true
warn_return_any = true
ignore_missing_imports = true

[tool.pytest.ini_options]
asyncio_mode = "auto"

[tool.ato]
claude_cli_version = ""
codex_cli_version = ""
```

### 最新工具版本检查点（2026-03-24）

- 本地已验证 `uv 0.10.5`：`uv init [PATH]` 在当前目录或目标目录初始化项目；`--package` 才会生成 `src/` 布局；`--python` 选择解释器版本而不是写入 `">=3.11"` 这样的范围字符串
- `ruff-pre-commit` 当前可用 tag：`v0.14.10`
- `pre-commit/mirrors-mypy` 当前可用 tag：`v1.19.1`
- `Typer 0.24.1` 的命令树模型要求分组子命令按 `app group subcommand` 调用，因此本项目保持 `ato batch select` / `ato batch status`
- structlog 当前文档明确建议接入 stdlib logging；若只写最小 `structlog.configure(...)` 示例而不配 handler，无法稳定满足 stderr/file 输出要求

### .pre-commit-config.yaml 参考

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.14.10
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format
  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.19.1
    hooks:
      - id: mypy
        additional_dependencies: [pydantic>=2.0, typer, structlog]
```

### .gitignore 追加项

```
# ATO 运行时
.ato/

# Python 缓存
__pycache__/
*.py[cod]
*.egg-info/
dist/
build/

# 工具缓存
.mypy_cache/
.ruff_cache/
.pytest_cache/
```

### 命名约定速查

| 范围 | 规则 | 示例 |
|------|------|------|
| Python 模块/函数/变量 | snake_case (ruff) | `transition_queue.py`, `def submit_transition()` |
| Python 类 | PascalCase | `StoryLifecycle`, `TransitionQueue` |
| Pydantic 模型 | PascalCase + 用途后缀 | `FindingRecord`, `ApprovalRequest` |
| CLI 命令 | 顶层命令 kebab-case；分组命令用空格路径 | `ato start`, `ato batch select` |
| 自定义异常 | PascalCase + Error | `CLIAdapterError` |
| 常量 | UPPER_SNAKE_CASE | `MAX_CONCURRENT_AGENTS` |
| 配置键 | snake_case | `max_concurrent_agents` |
| SQLite 表 | snake_case 复数 | `stories`, `findings` |
| 测试文件 | `test_<module>.py` | `test_logging.py` |

### 反模式清单（本 story 相关）

- 禁止 `asyncio.gather`（用 `TaskGroup`）
- 禁止 `shell=True` 启动子进程
- 禁止在 `models/` 外定义 Pydantic 模型
- 禁止 `print()` 输出日志（用 structlog）
- 禁止静默吞异常（至少 `structlog.warning`）
- 禁止在 Pydantic validator 中做 IO

### 空模块文件内容模板

所有占位空模块应包含模块级 docstring 说明用途，符合 mypy strict 要求：

```python
"""<模块名> — <一句话描述模块职责>。"""
```

不需要添加多余的代码、import 或注释。保持精简。

### Project Structure Notes

- 完整目录结构对齐架构文档 Section 4（项目结构图）
- `src/ato/` 作为主包名（ato = Agent Team Orchestrator）
- uv 默认生成的 `src/agent_team_orchestrator/` 不应保留到最终结构中
- `README.md` 与 `.python-version` 由 `uv init` 生成，应保留并纳入仓库
- 运行时数据目录 `.ato/` 不提交到 git（含 state.db, orchestrator.pid, logs/）
- `schemas/` 存放 JSON Schema 文件（review-findings.json 等，后续 story 创建）
- `tests/fixtures/` 存放 CLI 输出快照文件（后续 story 创建）

### References

- [Source: _bmad-output/planning-artifacts/epics.md — Epic 1, Story 1.1]
- [Source: _bmad-output/planning-artifacts/architecture.md — Section 4 项目结构, Decision 6 structlog, Decision 9 CLI Adapter]
- [Source: _bmad-output/planning-artifacts/prd.md — FR33, FR34, FR35, NFR5, NFR6]
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md — CLI 命令设计, Preflight Check UX]
- [Source: _bmad-output/project-context.md — 全部 68 条规则]
- [External: https://docs.astral.sh/uv/concepts/projects/init/ — `uv init` package/target-directory 行为]
- [External: https://github.com/astral-sh/ruff-pre-commit/tags — hook rev 校验]
- [External: https://github.com/pre-commit/mirrors-mypy/tags — hook rev 校验]
- [External: https://pypi.org/project/typer/ — Typer 命令树示例与当前版本]
- [External: https://www.structlog.org/ — structlog stdlib 集成说明]

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6 (1M context)

### Debug Log References

- ruff RUF001/RUF002/RUF003 规则与中文 docstring 冲突，已在 pyproject.toml 中 ignore
- hatchling 无法自动发现 `src/ato/` 包（项目名为 agent-team-orchestrator），已添加 `[tool.hatch.build.targets.wheel] packages = ["src/ato"]`
- Typer 0.24.1 需要至少一个 command 或 callback 才能运行，已为 app 添加 `@app.callback(invoke_without_command=True)`
- pre-commit hooks 需要 exclude BMAD skill 目录（`.agent/`, `.agents/`, `.claude/`, `_bmad/`），否则 ruff 会检查不可修改的外部文件

### Completion Notes List

- ✅ Task 1: 项目通过 `uv init --package` 初始化，pyproject.toml 配置完整（ruff/mypy/pytest/ato 工具节），核心依赖 6 个 + 开发依赖 5 个全部安装，`uv run ato --help` 返回退出码 0
- ✅ Task 2: 创建完整 `src/ato/` 包结构（27 个文件），`tests/` 目录（4 个子目录），`schemas/` 目录，`.gitignore` 追加 ATO 运行时和工具缓存目录
- ✅ Task 3: `src/ato/logging.py` 实现 `configure_logging()`，通过 `ProcessorFormatter` 将 stdlib logging 统一接入 structlog JSON 链路，JSON 输出到 stderr，支持 log_dir 文件写入
- ✅ Task 4: `.pre-commit-config.yaml` 配置 ruff-pre-commit v0.14.10 + mirrors-mypy v1.19.1，hooks 已安装
- ✅ Task 5: 6 个 logging 单元测试全部通过（含 stderr JSON 端到端验证、ISO 时间戳格式断言、stdlib logging JSON 输出验证），ruff/mypy/pytest/pre-commit 全部通过，无回归

### Change Log

- 2026-03-24: Story 1.1 实现完成 — 项目脚手架与开发工具链全部就绪
- 2026-03-24: Code review 修复 — stdlib logging 通过 ProcessorFormatter 统一接入 JSON 链路；测试补充 stderr JSON 端到端断言、ISO 时间戳格式验证、stdlib logger JSON 输出测试

### File List

- pyproject.toml (新建)
- uv.lock (新建)
- .python-version (新建)
- README.md (新建)
- .gitignore (修改)
- .pre-commit-config.yaml (新建)
- src/ato/__init__.py (新建)
- src/ato/cli.py (新建)
- src/ato/core.py (新建)
- src/ato/state_machine.py (新建)
- src/ato/transition_queue.py (新建)
- src/ato/subprocess_mgr.py (新建)
- src/ato/convergent_loop.py (新建)
- src/ato/recovery.py (新建)
- src/ato/config.py (新建)
- src/ato/nudge.py (新建)
- src/ato/preflight.py (新建)
- src/ato/logging.py (新建)
- src/ato/adapters/__init__.py (新建)
- src/ato/adapters/base.py (新建)
- src/ato/adapters/claude_cli.py (新建)
- src/ato/adapters/codex_cli.py (新建)
- src/ato/adapters/bmad_adapter.py (新建)
- src/ato/models/__init__.py (新建)
- src/ato/models/schemas.py (新建)
- src/ato/models/db.py (新建)
- src/ato/models/migrations.py (新建)
- src/ato/tui/__init__.py (新建)
- src/ato/tui/app.py (新建)
- src/ato/tui/app.tcss (新建)
- src/ato/tui/dashboard.py (新建)
- src/ato/tui/approval.py (新建)
- src/ato/tui/story_detail.py (新建)
- src/ato/tui/widgets/__init__.py (新建)
- tests/conftest.py (新建)
- tests/unit/__init__.py (新建)
- tests/unit/test_logging.py (新建)
- tests/integration/__init__.py (新建)
- tests/smoke/__init__.py (新建)
- tests/fixtures/.gitkeep (新建)
- schemas/.gitkeep (新建)
