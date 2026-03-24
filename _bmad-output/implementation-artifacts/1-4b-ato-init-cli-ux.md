# Story 1.4b: ato init CLI 命令与 UX 渲染

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a 操作者,
I want 通过 `ato init <项目路径>` 初始化项目并看到格式化的就绪报告,
So that 获得清晰的就绪/缺失反馈，确认可以开始编排。

## Acceptance Criteria

1. **Preflight 检查实时渲染**
   ```
   Given 操作者运行 `ato init /path/to/project`
   When CLI 调用 Preflight 引擎（Story 1.4a）执行三层检查
   Then 每项检查结果按层分组显示，四级状态编码：
     - ✔ 通过（绿色）
     - ✖ 阻断（红色）
     - ⚠ 警告（琥珀色）
     - ℹ 信息（灰色）
   And 任一项 HALT 时显示具体修复指引并停止
   ```

2. **成功摘要与确认**
   ```
   Given 所有检查完成且无阻断
   When 显示检查摘要
   Then 使用 rich 库渲染格式化输出，包含分层结果和底部摘要（"就绪（N 警告, M 信息）"）
   And 等待用户 Enter 确认后创建 `.ato/` 目录和 SQLite 数据库
   ```

3. **重新初始化检测**
   ```
   Given `ato init` 已成功完成
   When 再次运行 `ato init`
   Then 检测到已有数据库，提示用户确认是否重新初始化
   ```

4. **退出码规范**
   ```
   Given CLI 退出码规范
   When 环境检查失败时
   Then 退出码为 2（环境错误），错误信息输出到 stderr
   And 一般错误退出码为 1
   And 成功退出码为 0
   ```

## Tasks / Subtasks

- [ ] Task 1: 实现 `ato init` CLI 命令入口 (AC: #1, #3, #4)
  - [ ] 1.1 在 `src/ato/cli.py` 中新增 `@app.command("init")` 命令，接受 `project_path: Path` 位置参数（默认 `.`）和 `--db-path` 可选参数（默认 `{project_path}/.ato/state.db`）
  - [ ] 1.2 实现重新初始化检测：若 `db_path` 已存在，使用 `typer.confirm("已检测到现有数据库，是否重新初始化？", abort=True)` 提示确认
  - [ ] 1.3 调用 `asyncio.run(_init_async(project_path, db_path))` 执行异步流程
  - [ ] 1.4 在 `src/ato/cli.py` 模块级导入 `run_preflight` / `CheckResult`；`_init_async` 内部调用 `run_preflight(project_path, db_path, include_auth=True)` 获取完整 `list[CheckResult]`
  - [ ] 1.5 调用渲染函数 `render_preflight_results(results)` 显示格式化输出
  - [ ] 1.6 若结果含 HALT：渲染结果后 `raise typer.Exit(code=2)`
  - [ ] 1.7 若无 HALT：显示摘要，等待 Enter 确认（`typer.prompt("按 Enter 继续初始化，或 Ctrl-C 取消", default="", show_default=False)`）
  - [ ] 1.8 确认后显示完成信息："✔ 系统已初始化\n运行 `ato start` 开始编排\n运行 `ato tui` 打开仪表盘"（注意：`run_preflight()` 内部已调用 `init_db(db_path)` 创建 `.ato/` 目录和数据库，CLI 层无需重复调用）
  - [ ] 1.9 若用户在重新初始化确认时拒绝，保留 `click.exceptions.Abort` 的默认行为（不要吞掉 `Aborted!` 提示，也不要包装成通用异常）
  - [ ] 1.10 异常处理：`click.exceptions.Exit` 与 `click.exceptions.Abort` 直接 re-raise；其他异常 `typer.echo(str(exc), err=True)` + `raise typer.Exit(code=1)`

- [ ] Task 2: 实现 rich 渲染模块 (AC: #1, #2)
  - [ ] 2.1 在 `src/ato/cli.py` 中（或若代码量过大则提取到 `src/ato/cli_render.py`）实现 `render_preflight_results(results: list[CheckResult], *, console: Console | None = None) -> None`
  - [ ] 2.2 使用 `rich.console.Console` 创建模块级默认 console；测试时可注入 `Console(file=io.StringIO())`
  - [ ] 2.3 实现四级状态图标与颜色映射：
    - `PASS` → `✔` + `green`
    - `HALT` → `✖` + `red bold`
    - `WARN` → `⚠` + `yellow`（rich 中琥珀色用 yellow 近似）
    - `INFO` → `ℹ` + `dim`（灰色用 dim 样式）
  - [ ] 2.4 先打印标题 `AgentTeamOrchestrator — Preflight Check`，再按 `layer` 字段分组渲染：`system` → "第一层：系统环境"、`project` → "第二层：项目结构"、`artifact` → "第三层：编排前置 Artifact"
  - [ ] 2.5 逐行渲染 `run_preflight()` 实际返回的检查项；**不要**为了贴近 UX 示例而合成额外行（如单独的“config.yaml 字段完整”或 “Sprint status”）
  - [ ] 2.6 每层标题用 `console.print()` + 加粗，每个检查项缩进两格显示 `{icon} {message}`；使用 `highlight=False` 或 `rich.text.Text` 对象避免 message 中 `[`/`]` 被误解析为 rich markup
  - [ ] 2.7 WARN/HALT 项的第二行修复指引必须基于 `check_item` → hint 映射生成，不要解析 `message` 文本拼建议；`INFO` / `PASS` 不显示建议行
  - [ ] 2.8 渲染分隔线 `console.rule()`
  - [ ] 2.9 实现底部摘要渲染 `_render_summary(results, console)`：
    - 统计 HALT/WARN/INFO 数量
    - 全部通过：`✔ 就绪` 绿色
    - 有 WARN 无 HALT：`就绪（N 警告, M 信息）` 黄色
    - 有 HALT：`✖ 未就绪（N 阻断）` 红色

- [ ] Task 3: 编写 CLI 命令测试 (AC: #1, #2, #3, #4)
  - [ ] 3.1 新建 `tests/unit/test_cli_init.py`，使用 `typer.testing.CliRunner`；若 `cli.py` 为模块级导入，则 patch `ato.cli.run_preflight`（不要额外 mock `init_db`，因为 `run_preflight` 已被替换）
  - [ ] 3.2 测试正常流程：mock `run_preflight` 返回全 PASS，验证退出码 0
  - [ ] 3.3 测试 HALT 流程：mock `run_preflight` 返回含 HALT 结果，验证退出码 2
  - [ ] 3.4 测试 WARN 流程：mock `run_preflight` 返回 WARN + PASS，验证退出码 0 + 摘要包含"警告"
  - [ ] 3.5 测试重新初始化检测：预先创建 db 文件，验证出现确认提示
  - [ ] 3.6 测试重新初始化拒绝：模拟用户拒绝确认，验证 Click 默认 abort 行为仍保留（退出且不破坏现有数据库）
  - [ ] 3.7 测试 `--db-path` 自定义路径参数
  - [ ] 3.8 测试默认 project_path 为当前目录（`.`）

- [ ] Task 4: 编写渲染输出测试 (AC: #1, #2)
  - [ ] 4.1 在 `tests/unit/test_cli_init.py` 中测试 `render_preflight_results` 输出
  - [ ] 4.2 使用 `rich.console.Console(file=io.StringIO(), force_terminal=True)` 捕获渲染输出
  - [ ] 4.3 验证层标题正确显示（"第一层"、"第二层"、"第三层"）
  - [ ] 4.4 验证标题 `AgentTeamOrchestrator — Preflight Check` 显示
  - [ ] 4.5 验证只渲染输入 `results` 中实际存在的检查项，不合成额外行
  - [ ] 4.6 验证 WARN/HALT 的建议行来自 `check_item` 映射，`INFO` / `PASS` 不显示建议行
  - [ ] 4.7 验证四种状态图标（✔/✖/⚠/ℹ）正确渲染
  - [ ] 4.8 验证摘要文本：全通过 → "就绪"、有 WARN → "警告"、有 HALT → "未就绪"

- [ ] Task 5: 代码质量验证
  - [ ] 5.1 `uv run ruff check src/ato/cli.py` — 通过
  - [ ] 5.2 `uv run mypy src/ato/cli.py` — 通过
  - [ ] 5.3 `uv run pytest tests/unit/test_cli_init.py -v` — 全部通过
  - [ ] 5.4 `uv run pytest` — 全部通过, 0 regressions

## Dev Notes

### 核心设计：Preflight 引擎的 CLI 消费层

本 story 是 Story 1.4a（Preflight 三层检查引擎）的 CLI 消费层。**引擎逻辑已完整实现**，本 story 仅负责：
1. 在 `cli.py` 中注册 `ato init` 命令
2. 用 `rich` 库渲染检查结果
3. 处理用户交互（Enter 确认、重新初始化确认）
4. 管理退出码

**绝对不要修改 `preflight.py`** — 那里的三层检查引擎已经完成并通过 code review。

### Preflight 引擎 API（已由 Story 1.4a 实现）

```python
# src/ato/preflight.py
async def run_preflight(
    project_path: Path,
    db_path: Path,
    *,
    include_auth: bool = True,
) -> list[CheckResult]:
    """执行三层检查，持久化结果到 SQLite，返回完整结果列表。

    include_auth=True (ato init)：完整三层 + CLI 认证测试
    include_auth=False (ato start)：跳过认证测试
    """

# src/ato/models/schemas.py
CheckStatus = Literal["PASS", "HALT", "WARN", "INFO"]
CheckLayer = Literal["system", "project", "artifact"]

class CheckResult(_StrictBase):
    layer: CheckLayer        # "system" | "project" | "artifact"
    check_item: str          # 稳定 snake_case 标识，如 "python_version"
    status: CheckStatus      # "PASS" | "HALT" | "WARN" | "INFO"
    message: str             # 人类可读详情
```

### Story 边界澄清：不做 streaming，也不扩展引擎输出

- `run_preflight()` 当前是**批量返回** `list[CheckResult]` 的 API，不提供逐项 callback / async generator
- AC 中“实时显示”在本 story 中解释为：`ato init` 同一次调用完成检查后立即输出完整报告；**不是**边检查边 streaming
- 因为 1.4a 已明确完工，**不要**为满足“实时”措辞去修改 `preflight.py` 的返回协议
- CLI 层只渲染引擎已返回的 `CheckResult`；**不要**从 UX 示例反推并新增虚构行（例如把 `bmad_config` 拆成两行，或额外补一个 `Sprint status` 行）

### UX-DR6 PreflightOutput 视觉规范

使用 `rich` 库（非 Textual）的 `Console` + 颜色标记渲染到 stdout。

**实现取舍：** UX 文档中提到 `Console` + `Table`，但本 story 以“分层标题 + 逐行结果 + 可选建议行”的文本布局为准。不要强行使用 `Table`，否则 WARN/HALT 的二级建议行会变笨重，且更难贴近 UX 示例。

**目标渲染效果：**
```
  AgentTeamOrchestrator — Preflight Check

  第一层：系统环境
    ✔ Python 3.12.1 (≥3.11)
    ✔ Claude CLI v4.6.2
    ✔ Claude CLI 认证有效
    ✔ Codex CLI v1.3.0
    ✔ Codex CLI 认证有效
    ✔ Git v2.44.0

  第二层：项目结构
    ✔ Git 仓库有效 (main, clean)
    ✔ BMAD 配置 (_bmad/bmm/config.yaml)
    ✔ config.yaml 字段完整
    ⚠ BMAD skills 未部署 (.claude/skills/ 不存在)
      → 部分角色无法使用 BMAD，建议运行 BMAD 安装
    ✔ ato.yaml 已加载

  第三层：编排前置 Artifact
    ✔ Epic 文件 (2 epics)
    ⚠ PRD 未找到 — create-story 将缺少需求上下文
      → 建议运行: /bmad-create-prd
    ⚠ 架构文档未找到 — create-story 将缺少技术约束
      → 建议运行: /bmad-create-architecture
    ℹ UX 设计未找到 — 跳过
    ℹ 项目上下文未找到 — 跳过
    ✔ implementation_artifacts 目录可写

  ─────────────────────────────────────
  结果: 就绪（2 警告, 3 信息）
  按 Enter 继续初始化，或 Ctrl-C 取消...
```

**四级状态编码映射（rich 样式）：**

| CheckStatus | 图标 | rich style | 含义 |
|-------------|------|-----------|------|
| `PASS` | `✔` | `green` | 检查通过 |
| `HALT` | `✖` | `red bold` | 必须修复才能继续 |
| `WARN` | `⚠` | `yellow` | 可继续但建议处理 |
| `INFO` | `ℹ` | `dim` | 静默跳过 |

**底部摘要状态：**

| 结果 | 摘要文本 | 行为 |
|------|---------|------|
| 全部通过 | `✔ 就绪` 绿色 | 等待 Enter |
| 有 WARN 无 HALT | `就绪（N 警告, M 信息）` 黄色 | 等待 Enter |
| 有 HALT | `✖ 未就绪（N 阻断）` 红色 | 自动退出（code=2） |

### 层名称映射

```python
_LAYER_TITLES: dict[str, str] = {
    "system": "第一层：系统环境",
    "project": "第二层：项目结构",
    "artifact": "第三层：编排前置 Artifact",
}
```

### 修复指引渲染契约

`CheckResult` 只有 `message`，**没有**独立 `hint` 字段。因此第二行 `→ 建议：...` 不能靠 schema 提供，必须在 CLI 层维护稳定的 `check_item` → hint 映射。

```python
_HINTS: dict[str, str] = {
    "claude_installed": "安装 Claude CLI 后重新运行 `ato init`",
    "claude_auth": "执行 `claude auth` 完成登录",
    "codex_installed": "安装 Codex CLI 后重新运行 `ato init`",
    "codex_auth": "完成 Codex CLI 认证后重试",
    "git_installed": "安装 Git 后重试",
    "git_repo": "在目标目录执行 `git init`，或切换到已有仓库",
    "bmad_config": "补齐 `_bmad/bmm/config.yaml` 的必填字段",
    "bmad_skills": "运行 BMAD 安装流程以部署 skills 目录",
    "ato_yaml": "从 `ato.yaml.example` 复制并补全配置",
    "epic_files": "补齐 epics 文档，否则 `sprint-planning` / `create-story` 无法运行",
    "prd_files": "建议运行 `/bmad-create-prd`",
    "architecture_files": "建议运行 `/bmad-create-architecture`",
    "impl_directory": "修复目录权限，或检查 BMAD config 中 implementation_artifacts 路径",
}
```

- 仅 `WARN` / `HALT` 且映射存在时显示建议行
- `INFO` / `PASS` 不显示建议行
- **不要**从 `message` 里解析中文文案来拼建议；那样会让渲染逻辑脆弱且难测

### 退出码规范（Architecture Decision）

| 退出码 | 含义 | 使用场景 |
|--------|------|---------|
| 0 | 成功 | 初始化完成 |
| 1 | 一般错误 | 配置无效、意外异常 |
| 2 | 环境错误 | Preflight HALT（CLI 未安装、认证过期等） |

- 用 `typer.Exit(code=N)`，不用 `sys.exit()`
- 错误信息输出到 stderr：`typer.echo(msg, err=True)`
- `ato init` 失败时输出明确的下一步指引

### rich 库依赖说明

`rich` 未在 `pyproject.toml` 中显式声明，但作为 `textual>=2.0` 和 `typer>=0.24.1` 的传递依赖已可用。若需显式声明可 `uv add rich`，但通常不必要。

### CLI 命令注册模式

参考现有 `batch_select` 和 `batch_status` 的模式：
- 同步 typer 命令函数 → `asyncio.run(_xxx_async(...))` 调用异步逻辑
- `click.exceptions.Exit` / `click.exceptions.Abort` 需要 re-raise（避免被通用 except 捕获）
- structlog 记录关键操作

```python
@app.command("init")
def init_command(
    project_path: Path = typer.Argument(
        ".", help="目标项目路径", exists=True, file_okay=False, resolve_path=True,
    ),
    db_path: Path | None = typer.Option(
        None, "--db-path", help="SQLite 数据库路径（默认 <project>/.ato/state.db）",
    ),
) -> None:
    """初始化项目环境，执行 Preflight 检查。"""
    resolved_db = db_path or (project_path / ".ato" / "state.db")
    # ... 重新初始化检测、asyncio.run、异常处理
```

### 重新初始化流程

```
if db_path.exists():
    typer.confirm("已检测到现有数据库，是否重新初始化？", abort=True)
    # abort=True → 用户拒绝时自动 raise Abort (exit code 1)
```

**注意**：
- `typer.confirm` 在 `CliRunner` 测试中需要通过 `input` 参数传入 `"y\n"` 或 `"n\n"`
- `Abort` 必须绕过通用异常处理，让 Click 维持默认 abort 提示

### 数据库初始化流程

**`run_preflight()` 在所有检查完成后无条件调用 `init_db(db_path)` + `insert_preflight_results()`**（包括有 HALT 的情况也会持久化结果，见 `preflight.py:614-622`）。因此 CLI 层**不需要调用 `init_db()`**。

CLI 实际流程：
1. 检测 `db_path` 是否已存在 → 若存在则提示重新初始化确认
2. `run_preflight(project_path, db_path)` → 引擎执行三层检查 → 创建 `.ato/` 目录 + 数据库 → 持久化结果
3. `render_preflight_results(results)` → 渲染格式化输出
4. 若有 HALT → `raise typer.Exit(code=2)`
5. 若无 HALT → 等待 Enter → 显示成功信息

**副作用**：即使有 HALT 导致 exit(2)，数据库文件已被创建（用于持久化检查结果）。下次运行 `ato init` 时会触发重新初始化确认。这是预期行为——用户可以看到之前的检查结果。

### 测试策略

**CLI 命令测试**（`tests/unit/test_cli_init.py`）：
- 使用 `typer.testing.CliRunner` 调用 `app`
- 若 `cli.py` 采用模块级导入，patch `ato.cli.run_preflight` 避免真实 CLI/subprocess 调用
- 不要额外 mock `init_db`；只要 `run_preflight` 被替换，数据库副作用就不会发生
- 使用 `tmp_path` 构建项目目录

**渲染输出测试**：
- 直接向 `render_preflight_results(..., console=test_console)` 注入测试 console，避免 monkeypatch 全局 console
- 验证标题、图标、层标题、建议行、摘要文本正确

**CliRunner 的 `input` 参数**：
- Enter 确认：`runner.invoke(app, ["init", str(path)], input="\n")`
- 拒绝重新初始化：`runner.invoke(app, ["init", str(path)], input="n\n")`

### 从前置 Story 学到的关键模式

**Story 1.4a 教训：**
- `run_preflight()` 返回 `list[CheckResult]`，按层顺序排列（system → project → artifact）
- HALT 短路：Layer 1 有 HALT 则不执行 Layer 2/3，但已检查项仍在结果中
- `check_item` 使用稳定 snake_case 标识，便于查询
- 数据库连接不跨越外部 IO

**Story 1.1 教训：**
- 模块需要 docstring（mypy strict）
- Ruff 配置已排除 `_bmad/`, `.agent/`, `.claude/` 目录

**Story 1.3 教训：**
- Pydantic `extra="forbid"` 捕获拼写错误

**CLI 现有模式（batch 命令）：**
- 同步 typer 函数 + `asyncio.run()` 调用异步逻辑
- `click.exceptions.Exit` / `click.exceptions.Abort` 需要特殊 re-raise
- 错误信息用 `typer.echo(msg, err=True)` 输出到 stderr

### 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/ato/cli.py` | **修改** | 新增 `init_command` + `_init_async` + `render_preflight_results` + `_render_summary` |
| `tests/unit/test_cli_init.py` | **新建** | CLI init 命令测试 + 渲染输出测试 |

**不应修改的文件：**
- `src/ato/preflight.py` — 三层检查引擎已完成，不修改
- `src/ato/models/schemas.py` — CheckResult 模型已定义
- `src/ato/models/db.py` — 数据库 CRUD 已完成
- `src/ato/config.py` — 配置引擎不变
- `pyproject.toml` — rich 作为传递依赖已可用，无需新增

### 已有代码复用

**直接复用（不修改）：**
- `src/ato/preflight.py` → `run_preflight(project_path, db_path, include_auth=True)`：执行三层检查并持久化
- `src/ato/models/schemas.py` → `CheckResult`, `CheckStatus`, `CheckLayer`：数据模型
- `src/ato/models/db.py` → `init_db(db_path)`：数据库初始化（`run_preflight` 内部调用）
- `src/ato/cli.py` → `app` typer 实例：命令注册目标
- 现有 CLI 模式：`asyncio.run()` + Click 异常透传

**不要重复造轮：**
- ❌ 不要在 CLI 层重新实现检查逻辑 — 调用 `run_preflight()`
- ❌ 不要用 `print()` — 使用 `rich.console.Console` 渲染
- ❌ 不要用 `sys.exit()` — 使用 `typer.Exit(code=N)`
- ❌ 不要手动创建数据库表 — `run_preflight()` 内部调用 `init_db()`
- ❌ 不要用 `typer.echo()` 渲染彩色输出 — 用 `rich`（typer.echo 不支持样式）
- ❌ 不要在 `CliRunner` 测试中调用真实 subprocess — mock `run_preflight`

### 关键技术注意事项

1. **rich Console 创建** — `Console(stderr=False)` 输出到 stdout；错误信息仍用 `typer.echo(msg, err=True)` 到 stderr
2. **rich 与 typer 共存** — 正常输出用 `rich.Console`，错误/提示用 `typer.echo`/`typer.confirm`/`typer.prompt`
3. **CliRunner 捕获 rich 输出** — CliRunner 的 `output` 属性能捕获 stdout，但 rich 的 ANSI 转义码可能干扰字符串匹配；测试中验证关键文本（如 "就绪"、"未就绪"）而非精确 ANSI 序列
4. **typer.Argument 的 resolve_path=True** — 自动将相对路径解析为绝对路径
5. **typer.Argument 的 exists=True** — 自动验证路径存在，不存在时 typer 自动报错退出
6. **asyncio.run 在 CliRunner 中** — CliRunner 在同一线程中运行，`asyncio.run()` 可正常工作
7. **CheckResult.layer 的顺序** — `run_preflight` 返回结果按层顺序排列（system → project → artifact），可直接按出现顺序分组
8. **rich markup 安全** — `message` 字段来自 preflight 引擎，可能包含 `[` `]` 字符；使用 `console.print(text, highlight=False)` 或 `Text` 对象避免意外 markup 解析

### Project Structure Notes

- `ato init` 命令直接注册在 `app` 上（顶级命令），不在子命令组中
- 渲染函数在 `cli.py` 内实现（预计 ~60-80 行），除非代码量明显超出则提取到 `cli_render.py`
- 测试文件遵循 `tests/unit/test_cli_<feature>.py` 命名规范（参考现有 `test_cli_batch.py`）

### 依赖关系

**前置（已完成）：**
- ✅ Story 1.1：项目脚手架、structlog 配置
- ✅ Story 1.2：SQLite 持久化层、init_db()、迁移框架
- ✅ Story 1.3：配置引擎、ATOSettings
- ✅ Story 1.4a：Preflight 三层检查引擎、run_preflight() API

**后续依赖本 story：**
- Story 1.5（`ato plan` 阶段预览）依赖 `ato init` 成功后的数据库
- Epic 2A（编排核心）的 `ato start` 将复用 Preflight 引擎（`include_auth=False`）

### References

- [Source: _bmad-output/planning-artifacts/epics.md — Epic 1, Story 1.4b]
- [Source: _bmad-output/planning-artifacts/architecture.md — Decision 10: Preflight Check 三层协议]
- [Source: _bmad-output/planning-artifacts/architecture.md — Typer CLI 模式: 退出码规范]
- [Source: _bmad-output/planning-artifacts/prd.md — FR33 ato init, FR34 CLI 检测]
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md — UX-DR6 PreflightOutput 四级状态编码]
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md — Flow 1: Preflight → 首次启动]
- [Source: src/ato/cli.py — 现有 CLI 命令模式（batch select/status）]
- [Source: src/ato/preflight.py — run_preflight() API、CheckResult 模型]
- [Source: _bmad-output/implementation-artifacts/1-4a-preflight-check-engine.md — 前置 story 完整上下文]

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

### File List

### Change Log

- 2026-03-24: validate-create-story 修订 —— 明确 1.4b 只消费批量 `run_preflight()` 结果而不修改引擎做 streaming；补充 `check_item` → hint 渲染契约；修正 `Abort` 透传规则；统一 `run_preflight` mock/console 注入测试策略；禁止根据 UX 示例合成不存在的检查行
