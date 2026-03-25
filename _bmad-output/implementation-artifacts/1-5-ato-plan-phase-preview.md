# Story 1.5: ato plan 阶段预览

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a 操作者,
I want 通过 `ato plan <story-id>` 预览某个 story 将经历的完整阶段序列,
So that 在启动编排前对即将发生的事有清晰的心理预期。

## Acceptance Criteria

1. **完整阶段序列输出**
   ```
   Given 配置已加载且 story 存在于数据库中
   When 执行 `ato plan <story-id>`
   Then 输出该 story 将经历的完整阶段序列
     （queued → creating → validating → dev_ready → developing → reviewing → fixing → qa_testing → uat → merging → regression → done）
   And 使用 rich 库格式化（颜色编码不同阶段类型）
   ```

2. **当前进度高亮**
   ```
   Given 配置已加载且 story 当前处于某个中间阶段
   When 执行 `ato plan <story-id>`
   Then 已完成的阶段标记 ✔，当前阶段高亮 ▶，未来阶段正常显示
   ```

3. **Story 不存在处理**
   ```
   Given story-id 不存在于数据库中
   When 执行 `ato plan <story-id>`
   Then 退出码为 1，stderr 输出 "Story not found: <story-id>"
   ```

4. **配置缺失降级**
   ```
   Given ato.yaml 不存在或配置加载失败
   When 执行 `ato plan <story-id>`
   Then 仍然显示阶段序列（从 state_machine.py 常量获取），但不显示阶段类型/角色信息
   And stderr 输出配置加载失败的警告
   ```

## Tasks / Subtasks

- [x] Task 1: 实现 `ato plan` CLI 命令入口 (AC: #1, #3, #4)
  - [x] 1.1 在 `src/ato/cli.py` 中新增 `@app.command(“plan”)` 顶级命令，接受 `story_id: str` 位置参数、`--db-path` 可选参数（默认 `.ato/state.db`）、`--config` 可选参数（默认 `ato.yaml`）
  - [x] 1.2 数据库存在性检查：若 `db_path` 不存在，`typer.echo(“错误：数据库不存在: {db_path}。请先运行 `ato init`。”, err=True)` + `raise typer.Exit(code=1)`
  - [x] 1.3 调用 `asyncio.run(_plan_async(story_id, db_path, config_path))` 执行异步逻辑
  - [x] 1.4 在 `_plan_async` 中：通过 `get_connection(db_path)` 打开连接，`get_story(db, story_id)` 查询 story，并在 `try/finally` 中 `await db.close()`
  - [x] 1.5 Story 不存在时：`typer.echo(f”Story not found: {story_id}”, err=True)` + `raise typer.Exit(code=1)`
  - [x] 1.6 配置加载（可选降级）：尝试 `load_config(config_path)` + `build_phase_definitions(settings)`，失败时同时执行：
    - `logger.warning(“plan_config_load_failed”, config_path=str(config_path), error=str(exc))`
    - `typer.echo(“⚠ 配置加载失败，仅显示阶段序列”, err=True)`
    - 继续渲染（`phase_definitions = []`）
  - [x] 1.7 调用 `render_plan(story, phase_definitions, console=con)` 渲染输出
  - [x] 1.8 异常处理：`click.exceptions.Exit` / `click.exceptions.Abort` 直接 re-raise；其他异常 `typer.echo(str(exc), err=True)` + `raise typer.Exit(code=1)`

- [x] Task 2: 实现 plan 渲染函数 (AC: #1, #2)
  - [x] 2.1 在 `src/ato/cli.py` 中实现 `render_plan(story: StoryRecord, phase_defs: list[PhaseDefinition], *, console: Console | None = None) -> None`
  - [x] 2.2 构建完整阶段序列：`[“queued”] + list(CANONICAL_PHASES) + [“done”]`（共 12 个阶段）
  - [x] 2.3 从 `phase_defs` 构建 `phase_info: dict[str, tuple[str, str]]`（name → (phase_type, role)）映射，无配置时映射为空
  - [x] 2.4 确定阶段进度状态：
    - 从 `story.current_phase` 在完整序列中的位置 idx 分割
    - idx 之前：completed（✔）
    - idx 位置：current（▶ 高亮）
    - idx 之后：future（正常显示）
    - `blocked` 特殊处理：**不要尝试反推”最后已知阶段”**（当前模型未持久化 blocked 之前的 phase）
    - `blocked` 时输出额外提示：`⚠ 当前状态: blocked（MVP 不显示 blocked 前进度）`
    - `blocked` 时完整阶段序列仍照常显示，但全部按普通未激活阶段渲染，不伪造 ✔ / ▶ 进度
    - `done` 特殊处理：所有阶段标记 ✔
  - [x] 2.5 阶段类型颜色映射：
    - `structured_job` → `cyan`
    - `convergent_loop` → `magenta`
    - `interactive_session` → `green`
    - 系统状态（queued, done）→ `dim`
  - [x] 2.6 渲染标题：`”AgentTeamOrchestrator — Story Plan”` + story_id + 标题
  - [x] 2.7 逐行渲染每个阶段：`{状态图标} {阶段名:<16} ({类型} | {角色})`
    - completed: `✔` + `green` 样式
    - current: `▶` + `bold` + 阶段类型颜色 + `← 当前`
    - future: `○` + 阶段类型颜色
  - [x] 2.8 若 `phase_info` 为空（配置缺失降级），省略类型/角色信息，仅显示阶段名

- [x] Task 3: 编写 CLI 命令测试 (AC: #1, #2, #3, #4)
  - [x] 3.1 新建 `tests/unit/test_cli_plan.py`，使用 `typer.testing.CliRunner`
  - [x] 3.2 测试正常流程（story 在 developing 阶段）：mock `get_story` 返回 StoryRecord，mock `load_config` + `build_phase_definitions`，验证退出码 0 + 输出包含 story_id
  - [x] 3.3 测试 Story 不存在：mock `get_story` 返回 None，验证退出码 1 + stderr 包含 “Story not found”
  - [x] 3.4 测试数据库不存在：传入不存在的 db_path，验证退出码 1 + stderr 包含”数据库不存在”
  - [x] 3.5 测试配置加载失败降级：mock `load_config` 抛异常，验证退出码 0 + stderr 包含”配置加载失败，仅显示阶段序列” + 仍有阶段输出（无类型/角色）
  - [x] 3.6 测试 done 状态 story：所有阶段显示 ✔
  - [x] 3.7 测试 queued 状态 story：仅 queued 为当前，其余为 future
  - [x] 3.8 测试 blocked 状态 story：显示 blocked 提示，且不伪造任意已完成/当前阶段

- [x] Task 4: 编写渲染输出测试 (AC: #1, #2)
  - [x] 4.1 在 `tests/unit/test_cli_plan.py` 中测试 `render_plan` 输出
  - [x] 4.2 使用 `rich.console.Console(file=io.StringIO(), force_terminal=True)` 捕获渲染输出
  - [x] 4.3 验证标题 “AgentTeamOrchestrator — Story Plan” 显示
  - [x] 4.4 验证完整序列 12 个阶段全部出现
  - [x] 4.5 验证已完成阶段包含 “✔”，当前阶段包含 “▶” 和 “当前”
  - [x] 4.6 验证无配置降级时不显示类型/角色信息
  - [x] 4.7 验证有配置时各阶段类型标签正确显示

- [x] Task 5: 代码质量验证
  - [x] 5.1 `uv run ruff check src/ato/cli.py` — 通过
  - [x] 5.2 `uv run mypy src/ato/cli.py` — 通过
  - [x] 5.3 `uv run pytest tests/unit/test_cli_plan.py -v` — 全部通过
  - [x] 5.4 `uv run pytest` — 全部通过, 0 regressions

## Dev Notes

### 核心设计：状态机常量 + 配置类型的 CLI 可视化

本 story 是状态机（Story 2A.1）和配置引擎（Story 1.3）的 **只读消费层**。仅读取已有的状态和配置数据，不做任何写操作。

**核心逻辑：**
1. 从 `state_machine.py` 的 `CANONICAL_PHASES` 常量获取阶段序列
2. 从 `config.py` 的 `build_phase_definitions()` 获取每个阶段的类型和角色（可选）
3. 从 SQLite `stories` 表读取 story 当前阶段
4. 用 rich 渲染格式化的阶段进度视图

**绝对不要修改 `state_machine.py`、`config.py`、`models/schemas.py`、`models/db.py`** — 只消费其公共 API。

### 完整阶段序列（从 state_machine.py）

```python
# src/ato/state_machine.py — 直接导入使用
CANONICAL_PHASES: tuple[str, ...] = (
    "creating", "validating", "dev_ready", "developing",
    "reviewing", "fixing", "qa_testing", "uat",
    "merging", "regression",
)

# 完整显示序列（手动加 queued + done）：
FULL_SEQUENCE = ["queued"] + list(CANONICAL_PHASES) + ["done"]
# = ["queued", "creating", "validating", "dev_ready", "developing",
#    "reviewing", "fixing", "qa_testing", "uat", "merging", "regression", "done"]
```

### 阶段进度判定逻辑

```python
# story.current_phase 在 FULL_SEQUENCE 中的位置决定显示状态
idx = FULL_SEQUENCE.index(story.current_phase)
# idx 之前 → completed (✔)
# idx 位置 → current (▶)
# idx 之后 → future (○)

# 特殊情况：
# - current_phase == "done" → 所有阶段都是 completed
# - current_phase == "blocked" → 不在序列中，且当前模型未保存 blocked 之前的 phase
#   → 不做“最后已知位置”推断，避免伪造进度
#   → 额外输出 "⚠ 当前状态: blocked（MVP 不显示 blocked 前进度）"
#   → 完整阶段序列仍显示，但全部按普通未激活阶段渲染
```

### 配置引擎 API（Story 1.3 已实现）

```python
# src/ato/config.py
from ato.config import load_config, build_phase_definitions, PhaseDefinition

settings = load_config(Path("ato.yaml"))  # 抛 ConfigError
phase_defs = build_phase_definitions(settings)

# PhaseDefinition 属性：
# .name: str — 阶段名（如 "creating"）
# .role: str — 角色名（如 "creator"）
# .phase_type: str — "structured_job" | "convergent_loop" | "interactive_session"
# .cli_tool: str — "claude" | "codex"
```

### 数据库 API（Story 1.2 已实现）

```python
# src/ato/models/db.py
from ato.models.db import get_connection, get_story

db = await get_connection(db_path)
story = await get_story(db, story_id)  # StoryRecord | None
await db.close()

# StoryRecord 属性：
# .story_id: str
# .title: str
# .status: StoryStatus  # "backlog"|"planning"|"ready"|"in_progress"|"review"|"uat"|"done"|"blocked"
# .current_phase: str   # "queued"|"creating"|...|"done"|"blocked"
```

### 阶段类型颜色映射

```python
_PHASE_TYPE_STYLES: dict[str, str] = {
    "structured_job": "cyan",
    "convergent_loop": "magenta",
    "interactive_session": "green",
}
_SYSTEM_PHASE_STYLE = "dim"  # queued, done
```

### 目标渲染效果

**Story 在 developing 阶段时：**
```
AgentTeamOrchestrator — Story Plan

Story: story-001 — 用户认证模块
当前阶段: developing (in_progress)

  ✔ queued
  ✔ creating         structured_job    | creator
  ✔ validating       convergent_loop   | validator
  ✔ dev_ready        structured_job    | developer
  ▶ developing       structured_job    | developer    ← 当前
  ○ reviewing        convergent_loop   | reviewer
  ○ fixing           structured_job    | fixer
  ○ qa_testing       convergent_loop   | qa
  ○ uat              interactive_session | developer
  ○ merging          structured_job    | developer
  ○ regression       structured_job    | qa
  ○ done
```

**配置缺失降级时：**
```
AgentTeamOrchestrator — Story Plan

⚠ 配置加载失败，仅显示阶段序列

Story: story-001 — 用户认证模块
当前阶段: developing (in_progress)

  ✔ queued
  ✔ creating
  ✔ validating
  ✔ dev_ready
  ▶ developing    ← 当前
  ○ reviewing
  ○ fixing
  ○ qa_testing
  ○ uat
  ○ merging
  ○ regression
  ○ done
```

**blocked 状态时：**
```
AgentTeamOrchestrator — Story Plan

Story: story-001 — 用户认证模块
⚠ 当前状态: blocked（MVP 不显示 blocked 前进度）

  ○ queued
  ○ creating
  ○ validating
  ○ dev_ready
  ○ developing
  ○ reviewing
  ○ fixing
  ○ qa_testing
  ○ uat
  ○ merging
  ○ regression
  ○ done
```

### CLI 命令注册模式

沿用现有模式（参考 `init_command`、`start_cmd`）：
- 顶级命令 `@app.command("plan")`
- 同步 typer 函数 → `asyncio.run(_plan_async(...))`
- `click.exceptions.Exit` / `click.exceptions.Abort` re-raise
- 错误信息用 `typer.echo(msg, err=True)` 到 stderr
- 格式化输出用 `rich.Console` 到 stdout

```python
@app.command("plan")
def plan_command(
    story_id: str = typer.Argument(..., help="Story ID"),
    db_path: Path | None = typer.Option(None, "--db-path", help="SQLite 数据库路径"),
    config_path: Path | None = typer.Option(None, "--config", help="ato.yaml 配置文件路径"),
) -> None:
    """预览 story 将经历的完整阶段序列。"""
```

### 测试策略

**CLI 命令测试**（`tests/unit/test_cli_plan.py`）：
- 使用 `typer.testing.CliRunner` 调用 `app`
- mock 对象：
  - `ato.cli.get_story` — 控制 story 查询结果
  - `ato.cli.get_connection` — 注入 mock db 连接
  - `ato.cli.load_config` / `ato.cli.build_phase_definitions` — 控制配置加载
- 使用 `tmp_path` 创建假的 db 文件（触发数据库检查通过）
- 注意：`_plan_async` 中从 `ato.models.db` 和 `ato.config` 延迟导入，或模块级导入——取决于实现方式。若用延迟导入，patch 路径为 `ato.cli.xxx`；若模块级导入，也 patch `ato.cli.xxx`

**渲染输出测试**：
- 直接调用 `render_plan(story, phase_defs, console=test_console)` 注入测试 console
- 使用 `Console(file=io.StringIO(), force_terminal=True)` 捕获输出
- 验证关键文本（标题、图标、阶段名、"当前"标记）

**CliRunner 注意事项**：
- CliRunner 的 `output` 属性捕获 stdout，`result.stderr` 需 `mix_stderr=False` 或检查 `result.output` 中的 stderr 内容
- rich 的 ANSI 转义码可能干扰字符串匹配——验证关键文本而非精确 ANSI 序列

### 从前置 Story 学到的关键模式

**Story 1.4b 教训（ato init CLI）：**
- 使用 `rich.text.Text` 对象构建输出，避免 rich markup 误解析
- `highlight=False` 避免 `[]` 字符被误解析
- 测试中用 `Console(file=io.StringIO(), force_terminal=True)` 捕获 rich 输出
- `click.exceptions.Exit` 和 `click.exceptions.Abort` 必须 re-raise，不能被通用 except 捕获
- 模块级 `_console = Console()` 实例，测试时通过参数注入替代

**Story 1.3 教训（配置引擎）：**
- `load_config()` 抛 `ConfigError`（继承 `ATOError`）
- `build_phase_definitions()` 是纯函数，不做 IO

**Story 2A.1 教训（状态机）：**
- `CANONICAL_PHASES` 不含 queued/done/blocked 系统状态
- `PHASE_TO_STATUS` 映射所有阶段（含系统状态）到高层 StoryStatus
- `blocked` 在 MVP 中是 sink state，**未持久化 blocked 前的最后阶段**
- `state_machine.py` 的常量可直接导入使用

**Story 2B.5 教训（batch select/status CLI）：**
- `get_connection(db_path)` 返回连接，需在 `try/finally` 中 `await db.close()`
- `get_story(db, story_id)` 返回 `StoryRecord | None`
- JSON 输出用 `typer.echo(json.dumps(data, ensure_ascii=False))`

### 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/ato/cli.py` | **修改** | 新增 `plan_command` + `_plan_async` + `render_plan` + 阶段类型颜色常量 |
| `tests/unit/test_cli_plan.py` | **新建** | CLI plan 命令测试 + 渲染输出测试 |

**不应修改的文件：**
- `src/ato/state_machine.py` — 状态机常量只消费，不修改
- `src/ato/config.py` — 配置引擎只消费，不修改
- `src/ato/models/schemas.py` — 数据模型不变
- `src/ato/models/db.py` — 数据库 CRUD 不变
- `src/ato/preflight.py` — 与本 story 无关
- `pyproject.toml` — 无新依赖（rich 已作为传递依赖可用）

### 已有代码复用

**直接复用（不修改）：**
- `src/ato/state_machine.py` → `CANONICAL_PHASES`：阶段序列常量
- `src/ato/state_machine.py` → `PHASE_TO_STATUS`：阶段→高层状态映射
- `src/ato/config.py` → `load_config(path)` + `build_phase_definitions(settings)`：阶段类型/角色信息
- `src/ato/config.py` → `PhaseDefinition`：阶段定义 DTO
- `src/ato/models/db.py` → `get_connection(db_path)` + `get_story(db, story_id)`：数据库查询
- `src/ato/models/schemas.py` → `StoryRecord`：Story 数据模型
- `src/ato/cli.py` → `app` typer 实例：命令注册目标
- `src/ato/cli.py` → `_console` Console 实例：rich 输出

**不要重复造轮：**
- ❌ 不要硬编码阶段列表 — 导入 `CANONICAL_PHASES`
- ❌ 不要自行实现配置解析 — 调用 `load_config()` + `build_phase_definitions()`
- ❌ 不要自行打开数据库连接 — 调用 `get_connection()`
- ❌ 不要用 `print()` — 使用 `rich.Console`
- ❌ 不要用 `sys.exit()` — 使用 `typer.Exit(code=N)`

### Project Structure Notes

- `ato plan` 命令注册在 `app` 上（顶级命令），与 `init`、`start`、`stop` 同级
- 渲染函数在 `cli.py` 内实现（预计 ~40-60 行），不需要单独文件
- 测试文件遵循 `tests/unit/test_cli_<feature>.py` 命名规范

### 依赖关系

**前置（已完成）：**
- ✅ Story 1.1：项目脚手架
- ✅ Story 1.2：SQLite 持久化层、get_connection()、get_story()
- ✅ Story 1.3：配置引擎、load_config()、build_phase_definitions()
- ✅ Story 1.4a + 1.4b：ato init 创建数据库
- ✅ Story 2A.1：StoryLifecycle 状态机、CANONICAL_PHASES、PHASE_TO_STATUS
- ✅ Story 2B.5：batch select/status CLI 模式（可参考）

**后续依赖本 story：**
- Epic 6 TUI 仪表盘可复用阶段进度渲染逻辑

### References

- [Source: _bmad-output/planning-artifacts/epics.md — Epic 1, Story 1.5]
- [Source: _bmad-output/planning-artifacts/prd.md — FR5: ato plan 预览阶段序列]
- [Source: src/ato/state_machine.py — CANONICAL_PHASES, PHASE_TO_STATUS, StoryLifecycle]
- [Source: src/ato/config.py — load_config(), build_phase_definitions(), PhaseDefinition]
- [Source: src/ato/models/db.py — get_connection(), get_story()]
- [Source: src/ato/models/schemas.py — StoryRecord, StoryStatus]
- [Source: src/ato/cli.py — 现有 CLI 命令模式（init, start, stop, batch select/status）]
- [Source: _bmad-output/implementation-artifacts/1-4b-ato-init-cli-ux.md — 前置 story 完整上下文]

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6 (1M context)

### Debug Log References

无调试问题。

### Completion Notes List

- ✅ 实现 `plan_command` + `_plan_async` + `render_plan`，遵循现有 CLI 模式（`init`, `start`, `batch select` 等）
- ✅ 完整阶段序列从 `CANONICAL_PHASES` 导入，加上 queued/done 系统状态组成 12 阶段
- ✅ 阶段进度判定逻辑：completed(✔) / current(▶) / future(○)，支持 blocked/done 特殊处理
- ✅ 配置加载可选降级：失败时 stderr 告警，仅显示阶段序列（无类型/角色信息）
- ✅ DB 连接在 try/finally 中正确关闭
- ✅ 使用 `rich.text.Text` 对象构建输出，`highlight=False` 避免误解析
- ✅ 14 个测试全部通过（7 个 CLI 命令测试 + 7 个渲染输出测试）
- ✅ 全量回归 741 测试通过，0 regressions

### File List

- `src/ato/cli.py` — 修改：新增 `plan_command` + `_plan_async` + `render_plan` + `_PHASE_TYPE_STYLES` / `_SYSTEM_PHASE_STYLE` 常量；顶层新增 `from ato.config import PhaseDefinition, build_phase_definitions, load_config` 和 `from ato.models.db import get_connection, get_story`
- `tests/unit/test_cli_plan.py` — 新建：CLI plan 命令测试（7 个）+ 渲染输出测试（7 个）

### Change Log

- 2026-03-25: create-story 创建 — 基于 epics/architecture/PRD/前置 story 分析生成完整开发上下文
- 2026-03-25: validate-create-story 修订 —— 明确配置加载失败必须 stderr 告警并继续降级渲染；移除 blocked 场景中无法成立的”最后已知阶段”反推逻辑；补充 `_plan_async` 连接关闭要求与对应测试断言
- 2026-03-25: dev-story 实现 — 完成 plan_command + render_plan + 14 个测试，全量 741 测试通过
- 2026-03-25: code-review follow-up — 修复 3 个 findings：(1) blocked 分支复用 phase-type 颜色映射 (2) 类型/角色输出格式改为 `(type | role)` (3) 测试文件 list 泛型标注修复 mypy strict
- 2026-03-25: code-review patch — 补强渲染契约回归保护：精确断言 `(structured_job | developer)` 格式 + 新增 blocked+有配置分支测试，共 15 测试
