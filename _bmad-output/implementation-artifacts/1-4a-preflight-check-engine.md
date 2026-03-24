# Story 1.4a: Preflight 三层检查引擎

Status: review

## Story

As a 系统,
I want 提供分层前置检查引擎，验证环境、项目结构和编排前置 Artifact,
So that 启动编排前可确认所有前置条件满足。

## Acceptance Criteria

1. **Layer 1 — 系统环境检查**
   ```
   Given 系统环境
   When 调用 check_system_environment()
   Then 顺序检测：
     - Python ≥3.11
     - Claude CLI 已安装且认证有效
     - Codex CLI 已安装且认证有效
     - Git 已安装
   And 返回顺序稳定的 list[CheckResult]，每项包含 status (PASS / HALT / WARN / INFO) 和 message (人类可读详情)
   ```

2. **Layer 2 — 项目结构检查**
   ```
   Given Layer 1 通过
   When 调用 check_project_structure(project_path)
   Then 验证：
     - 目标路径是 git 仓库
     - BMAD 配置存在且有效 (_bmad/bmm/config.yaml)
     - BMAD skills 已部署（`.claude/skills/`、`.codex/skills/`、`.agents/skills/` 任一存在即可）
     - ato.yaml 存在
   And 非阻塞缺失项返回 WARN
   ```

3. **Layer 3 — 编排前置 Artifact 检查**
   ```
   Given Layer 2 通过
   When 调用 check_artifacts(project_path)
   Then 按 create-story 的 whole/sharded 发现规则检测：
     - Epic 文件（必须）— whole: `{planning_artifacts}/*epic*.md`；sharded: `{planning_artifacts}/*epic*/*.md`
     - PRD（推荐）— whole: `{planning_artifacts}/*prd*.md`；sharded: `{planning_artifacts}/*prd*/*.md`
     - 架构文档（推荐）— whole: `{planning_artifacts}/*architecture*.md`；sharded: `{planning_artifacts}/*architecture*/*.md`
     - UX 设计（可选）— whole: `{planning_artifacts}/*ux*.md`；sharded: `{planning_artifacts}/*ux*/*.md`
     - 项目上下文（可选）— 模式：**/project-context.md
     - implementation_artifacts 目录可写（必须）
   And 必须项缺失返回 HALT
   ```

4. **结果持久化**
   ```
   Given 检查结果列表
   When 持久化结果
   Then 创建 preflight_results 表（CREATE TABLE IF NOT EXISTS）并写入所有检查结果
   ```

## Tasks / Subtasks

- [x] Task 1: 定义 CheckResult 数据模型与 preflight_results 表 (AC: #4)
  - [x] 1.1 在 `src/ato/models/schemas.py` 中新增 `CheckStatus = Literal["PASS", "HALT", "WARN", "INFO"]` 和 `CheckLayer = Literal["system", "project", "artifact"]`
  - [x] 1.2 在 `src/ato/models/schemas.py` 中新增 `CheckResult(_StrictBase)` 模型：`layer: CheckLayer`, `check_item: str`, `status: CheckStatus`, `message: str`；`check_item` 使用稳定 snake_case 标识（如 `python_version`, `claude_auth`），便于后续 SQLite 查询和 TUI 展示
  - [x] 1.3 在 `src/ato/models/migrations.py` 中新增 v2→v3 迁移：创建 `preflight_results` 表（id INTEGER PK AUTOINCREMENT, run_id TEXT NOT NULL, layer TEXT NOT NULL, check_item TEXT NOT NULL, status TEXT NOT NULL, message TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP）+ idx_preflight_run_id 索引
  - [x] 1.4 更新 `src/ato/models/schemas.py` 中 `SCHEMA_VERSION = 3`
  - [x] 1.5 在 `src/ato/models/db.py` 中新增 `insert_preflight_results(db, run_id: str, results: list[CheckResult]) -> None`，使用 `executemany` 批量插入
  - [x] 1.6 编写单元测试 `tests/unit/test_preflight_schema.py`：CheckResult 模型验证（合法/非法 status 和 layer 值）、migration v2→v3、insert_preflight_results CRUD

- [x] Task 2: 实现 Layer 1 — check_system_environment() (AC: #1)
  - [x] 2.1 在 `src/ato/preflight.py` 中实现 `async def check_system_environment() -> list[CheckResult]`
  - [x] 2.2 实现 `_check_python_version() -> CheckResult`：使用 `sys.version_info`，< 3.11 返回 HALT
  - [x] 2.3 实现 `_check_cli_installed(cli_name: str, version_cmd: list[str]) -> CheckResult`：通用 CLI 版本检测，使用 `asyncio.create_subprocess_exec`（不使用 `shell=True`），超时 10 秒
  - [x] 2.4 实现 `_check_claude_auth() -> CheckResult`：仅在 Claude 已安装时执行 `claude -p "ping" --max-turns 1 --output-format json --no-session-persistence`，超时 30 秒，失败提示 `claude auth`
  - [x] 2.5 实现 `_check_codex_auth() -> CheckResult`：仅在 Codex 已安装时执行 `codex exec "ping" --json --skip-git-repo-check --ephemeral -s read-only`，超时 30 秒，失败提示认证
  - [x] 2.6 保持严格顺序与稳定输出：Python → Claude install → Claude auth → Codex install → Codex auth → Git；若安装失败则跳过对应 auth 检查，避免重复噪声与误导性错误
  - [x] 2.7 每个检查函数内使用 `structlog` 记录开始/结束/失败，绑定 `layer="system"`
  - [x] 2.8 编写单元测试 `tests/unit/test_preflight.py::TestLayer1`：mock subprocess 的 stdout/returncode，覆盖每项检查的 PASS/HALT 场景（Python 版本、CLI 未安装、CLI 认证失败、超时、Codex 非 git 目录 flag、生效的顺序稳定性）

- [x] Task 3: 实现 Layer 2 — check_project_structure() (AC: #2)
  - [x] 3.1 在 `src/ato/preflight.py` 中实现 `async def check_project_structure(project_path: Path) -> list[CheckResult]`
  - [x] 3.2 实现 git 仓库检测：`git -C <path> rev-parse --git-dir`，失败返回 HALT
  - [x] 3.3 实现 BMAD 配置检测：检查 `_bmad/bmm/config.yaml` 存在，Pydantic 验证 `project_name`、`planning_artifacts`、`implementation_artifacts` 必填字段；缺失/无效返回 HALT
  - [x] 3.4 实现 BMAD skills 目录检测：`.claude/skills/`、`.codex/skills/`、`.agents/skills/` 任一存在即 PASS；全部不存在返回 WARN
  - [x] 3.5 实现 ato.yaml 检测：不存在返回 HALT + 提示从 `ato.yaml.example` 复制
  - [x] 3.6 编写单元测试 `tests/unit/test_preflight.py::TestLayer2`：使用 `tmp_path` 构建各种项目结构场景（完整/缺 git/缺 BMAD/缺 ato.yaml/仅 `.claude/skills` /仅 `.codex/skills` /仅 `.agents/skills` /三者都缺），验证每个检查项的 PASS/HALT/WARN

- [x] Task 4: 实现 Layer 3 — check_artifacts() (AC: #3)
  - [x] 4.1 在 `src/ato/preflight.py` 中实现 `async def check_artifacts(project_path: Path) -> list[CheckResult]`
  - [x] 4.2 使用 BMAD config 中的 `planning_artifacts` 和 `implementation_artifacts` 路径解析 artifact 位置
  - [x] 4.3 实现 Epic 文件检测：同时支持 whole `{planning_artifacts}/*epic*.md` 与 sharded `{planning_artifacts}/*epic*/*.md`；两者都未找到返回 HALT
  - [x] 4.4 实现 PRD 检测：同时支持 whole `{planning_artifacts}/*prd*.md` 与 sharded `{planning_artifacts}/*prd*/*.md`；两者都未找到返回 WARN
  - [x] 4.5 实现架构文档检测：同时支持 whole `{planning_artifacts}/*architecture*.md` 与 sharded `{planning_artifacts}/*architecture*/*.md`；两者都未找到返回 WARN
  - [x] 4.6 实现 UX 设计检测：同时支持 whole `{planning_artifacts}/*ux*.md` 与 sharded `{planning_artifacts}/*ux*/*.md`；两者都未找到返回 INFO
  - [x] 4.7 实现 project-context 检测：glob `**/project-context.md`（从 project_path 起搜索），未找到返回 INFO
  - [x] 4.8 实现 implementation_artifacts 目录检测：不存在则自动创建，创建失败返回 HALT
  - [x] 4.9 编写单元测试 `tests/unit/test_preflight.py::TestLayer3`：使用 `tmp_path` 构建各种 artifact 场景（whole 文档、sharded 文档、混合模式、缺失 artifact），验证每项 glob 检测和状态码

- [x] Task 5: 实现编排函数与持久化 (AC: #1, #2, #3, #4)
  - [x] 5.1 实现 `async def run_preflight(project_path: Path, db_path: Path, *, include_auth: bool = True) -> list[CheckResult]`：顺序执行三层检查，每层有 HALT 则跳过后续层
  - [x] 5.2 `include_auth=True`（`ato init` 完整检查）时执行 CLI 认证测试；`include_auth=False`（`ato start` 快速检查）时跳过认证测试，但仍保留 Python / CLI 安装 / Git 检测
  - [x] 5.3 生成 `run_id = uuid4().hex` 标识本次检查
  - [x] 5.4 在所有 subprocess / glob 检查完成后再调用 `init_db()` / `get_connection()`；禁止持有 SQLite 连接等待外部 IO，避免违反项目的 DB 锁约束
  - [x] 5.5 调用 `init_db(db_path)` 确保数据库存在（会自动运行 migration 到 v3）
  - [x] 5.6 调用 `insert_preflight_results(db, run_id, results)` 持久化结果
  - [x] 5.7 返回完整 `list[CheckResult]`（供 Story 1.4b 的 CLI 渲染消费）
  - [x] 5.8 编写集成测试 `tests/integration/test_preflight_integration.py`：mock 所有 subprocess（不调用真实 CLI），验证三层编排顺序、HALT 短路逻辑、结果持久化到 SQLite、include_auth=False 跳过认证检查、SQLite 连接不跨越外部 IO

- [x] Task 6: 代码质量验证
  - [x] 6.1 `uv run ruff check src/ato/preflight.py src/ato/models/schemas.py src/ato/models/db.py src/ato/models/migrations.py` — 通过
  - [x] 6.2 `uv run mypy src/ato/preflight.py src/ato/models/schemas.py src/ato/models/db.py src/ato/models/migrations.py` — 通过
  - [x] 6.3 `uv run pytest tests/unit/test_preflight_schema.py tests/unit/test_preflight.py tests/integration/test_preflight_integration.py -v` — 全部通过
  - [x] 6.4 `uv run pytest` — 全部通过, 0 regressions

## Dev Notes

### 核心设计：三层顺序检查引擎

**Architecture Decision 10** 定义了 Preflight Check 三层协议。本 story 仅实现检查引擎（纯逻辑），CLI 渲染由 Story 1.4b 负责。

**分层职责：**
- **Layer 1（系统环境）**：检测运行环境（Python、CLI 工具、认证），全部 HALT 级别
- **Layer 2（项目结构）**：检测目标项目配置完整性，部分 WARN
- **Layer 3（编排 Artifact）**：检测 BMAD 技能消费的前置文档，按实际依赖分级

**短路逻辑**：任一层出现 HALT → 跳过后续层（不浪费时间检测依赖前一层的项目）

**双模式**：
- `include_auth=True`（`ato init`）：完整三层 + CLI 认证测试调用
- `include_auth=False`（`ato start`）：跳过 Layer 1 中的 CLI 认证测试，但仍保留 Python / CLI 安装 / Git 检测

### Layer 1 — CLI 检测方法详表

| 检查项 | 检测命令 | 超时 | HALT 提示 |
|--------|---------|------|----------|
| Python ≥3.11 | `sys.version_info` | — | 升级 Python |
| Claude CLI 已安装 | `claude --version` | 10s | 安装指引 |
| Claude CLI 认证有效 | `claude -p "ping" --max-turns 1 --output-format json --no-session-persistence` | 30s | 执行 `claude auth` |
| Codex CLI 已安装 | `codex --version` | 10s | 安装指引 |
| Codex CLI 认证有效 | `codex exec "ping" --json --skip-git-repo-check --ephemeral -s read-only` | 30s | 执行认证 |
| Git 已安装 | `git --version` | 10s | 安装 Git |

**顺序约束：**
- Layer 1 必须按 AC 顺序串行执行，返回结果顺序与执行顺序一致
- Claude / Codex 认证检查仅在对应 CLI 安装检查通过时才执行

### Layer 2 — 项目结构检查详表

| 检查项 | 检测方式 | 状态 |
|--------|---------|------|
| Git 仓库 | `git -C <path> rev-parse --git-dir` | HALT |
| BMAD 配置 | `_bmad/bmm/config.yaml` 存在 + Pydantic 验证三个必填字段 | HALT |
| BMAD Skills | `.claude/skills/`、`.codex/skills/`、`.agents/skills/` 任一目录存在 | WARN |
| ato.yaml | 项目根目录存在 | HALT |

### Layer 3 — Artifact 检查详表

| Artifact | Glob 模式 | 必要性 | 状态 | 消费者 |
|----------|----------|-------|------|--------|
| Epic 文件 | whole: `{planning_artifacts}/*epic*.md`；sharded: `{planning_artifacts}/*epic*/*.md` | 必须 | HALT | sprint-planning, create-story |
| PRD | whole: `{planning_artifacts}/*prd*.md`；sharded: `{planning_artifacts}/*prd*/*.md` | 推荐 | WARN | create-story |
| 架构文档 | whole: `{planning_artifacts}/*architecture*.md`；sharded: `{planning_artifacts}/*architecture*/*.md` | 推荐 | WARN | create-story |
| UX 设计 | whole: `{planning_artifacts}/*ux*.md`；sharded: `{planning_artifacts}/*ux*/*.md` | 可选 | INFO | create-story |
| 项目上下文 | `**/project-context.md` | 可选 | INFO | dev-story, create-story |
| impl 目录 | `{implementation_artifacts}/` 可写 | 必须 | HALT（创建失败时） | sprint-planning |

**Artifact 发现一致性：**
- Preflight 的存在性检测必须与 `bmad-create-story/discover-inputs.md` 的 whole/sharded 规则对齐，避免 story 已可消费但 preflight 误报缺失
- 对 sharded 文档，目录下任意匹配 `.md` 文件即视为 artifact 存在；若有 `index.md`，可在 message 中优先展示

### BMAD Config 验证逻辑

Layer 2 需要读取 `_bmad/bmm/config.yaml` 并验证三个必填字段。**不要**复用 `config.py` 的 `load_config()`（那是加载 `ato.yaml` 的）。应使用轻量 Pydantic 模型单独验证：

```python
class _BMadConfigCheck(BaseModel):
    model_config = ConfigDict(extra="ignore")  # BMAD config 有其他字段，只验证必填
    project_name: str
    planning_artifacts: str
    implementation_artifacts: str
```

### Subprocess 调用规则

- **绝对不用** `shell=True` — 使用 `asyncio.create_subprocess_exec`
- **设置超时** — CLI 版本检查 10s，认证测试 30s
- **超时处理**：先 `proc.terminate()`，等 5s，再 `proc.kill()`（三阶段清理）
- **捕获 FileNotFoundError** — CLI 未安装时 `create_subprocess_exec` 抛出此异常
- **Claude 探测避免污染 session 历史** — 使用 `--no-session-persistence`
- **Codex 探测避免依赖当前 cwd 是 git repo** — 使用 `--skip-git-repo-check`
- **Codex 探测避免写入本地状态** — 使用 `--ephemeral -s read-only`

### SQLite / 外部 IO 边界

- 先完成所有 CLI subprocess、glob、目录检查，再打开 SQLite 连接写入 `preflight_results`
- 不要在 `async with get_connection()` 或活跃事务期间等待外部 CLI / 长时间文件系统 IO
- `run_preflight()` 的数据库阶段应保持短小：`init_db()` → `get_connection()` → `insert_preflight_results()` → 关闭连接

### 数据库迁移

当前 `SCHEMA_VERSION = 2`（Story 2B.5 新增 batches 表）。本 story 新增 v2→v3 迁移：

```sql
CREATE TABLE IF NOT EXISTS preflight_results (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT    NOT NULL,
    layer       TEXT    NOT NULL,
    check_item  TEXT    NOT NULL,
    status      TEXT    NOT NULL,
    message     TEXT    NOT NULL,
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_preflight_run_id ON preflight_results(run_id);
```

### 已有代码复用

**直接复用（不修改）：**
- `src/ato/models/db.py` → `init_db(db_path)`：初始化数据库 + 运行迁移
- `src/ato/models/db.py` → `get_connection(db_path)`：获取配置好 PRAGMA 的连接
- `src/ato/models/schemas.py` → `_StrictBase`：CheckResult 基类
- `src/ato/models/schemas.py` → `ConfigError`：配置验证错误
- `tests/conftest.py` → `db_path`, `initialized_db_path` fixtures

**需要扩展：**
- `src/ato/models/schemas.py` → 新增 `CheckResult`, `CheckStatus`, `CheckLayer`
- `src/ato/models/migrations.py` → 新增 `_migrate_v2_to_v3` 迁移函数
- `src/ato/models/db.py` → 新增 `insert_preflight_results()`

**不要重复造轮：**
- ❌ 不要在 `preflight.py` 中自己写 SQL — 调用 `db.py` 的 CRUD
- ❌ 不要在 `preflight.py` 中定义 Pydantic 模型 — 放在 `schemas.py`
- ❌ 不要在 `preflight.py` 中渲染控制台输出 — 那是 Story 1.4b 的 `rich` 渲染
- ❌ 不要调用 `config.py` 的 `load_config()` 检查 BMAD config — 那是加载 `ato.yaml` 的
- ❌ 不要在持有 SQLite 连接时执行 CLI subprocess 或 artifact glob 扫描
- ❌ 不要用 `print()` — 使用 `structlog`

### 从前置 Story 学到的关键模式

**Story 1.1 教训：**
- 模块需要 docstring（mypy strict）
- Ruff 配置已排除 `_bmad/`, `.agent/`, `.claude/` 目录

**Story 1.2 教训：**
- 迁移使用 SAVEPOINT 事务隔离
- DateTime 字段在 SQLite 中是 TEXT，读回需要 `datetime.fromisoformat()` 反序列化
- PRAGMA 必须每个连接都设置
- 使用 `if/raise` 而非 `assert` 做验证

**Story 1.3 教训：**
- Pydantic `extra="forbid"` 捕获拼写错误
- 嵌套 config 模型都需要 `extra="forbid"`
- `YamlConfigSettingsSource` 使用 `yaml_file=` 参数

### 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/ato/preflight.py` | **重写** | 从 1 行 docstring 扩展为完整三层检查引擎 |
| `src/ato/models/schemas.py` | **修改** | 新增 CheckStatus、CheckLayer、CheckResult；SCHEMA_VERSION 2→3 |
| `src/ato/models/migrations.py` | **修改** | 新增 v2→v3 迁移（preflight_results 表） |
| `src/ato/models/db.py` | **修改** | 新增 insert_preflight_results() |
| `tests/unit/test_preflight_schema.py` | **新建** | CheckResult 模型 + 迁移 + CRUD 测试 |
| `tests/unit/test_preflight.py` | **新建** | Layer 1/2/3 单元测试（mock subprocess） |
| `tests/integration/test_preflight_integration.py` | **新建** | 三层编排 + 持久化集成测试 |

**不应修改的文件：**
- `src/ato/config.py` — 配置引擎不变
- `src/ato/cli.py` — CLI 命令由 Story 1.4b 实现
- `src/ato/state_machine.py` — 状态机不变
- `src/ato/models/__init__.py` — 评估是否需要导出新接口

### 依赖关系

**前置（已完成）：**
- ✅ Story 1.1：项目脚手架、structlog 配置
- ✅ Story 1.2：SQLite 持久化层、init_db()、迁移框架
- ✅ Story 1.3：配置引擎、ATOSettings、ConfigError

**后续依赖本 story：**
- Story 1.4b（`ato init` CLI + UX 渲染）调用本引擎并用 `rich` 渲染结果
- Story 1.5（`ato plan` 阶段预览）依赖 preflight 成功
- Epic 2A（编排核心）的 `ato start` 调用 `run_preflight(include_auth=False)` 做快速检查

### Project Structure Notes

- `src/ato/preflight.py` 已存在空 stub，本 story 负责完整实现
- 模块依赖方向：`preflight.py` 可依赖 `models/schemas.py`、`models/db.py`，但不依赖 `config.py`、`core.py`、`state_machine.py`、`adapters/`
- `preflight.py` 属于 bootstrap 层，允许内部封装最小 CLI probe helper；不要为了复用未来 adapter 而人为引入前置依赖循环
- Layer 2 的 BMAD config 验证使用模块内私有 Pydantic 模型，不引入外部依赖
- 测试文件遵循 `tests/unit/test_<module>.py` 和 `tests/integration/test_<feature>.py` 命名规范

### 关键技术注意事项

1. **asyncio.create_subprocess_exec** — 不使用 `shell=True`，参数列表传递
2. **FileNotFoundError** — CLI 未安装时 `create_subprocess_exec` 抛出此异常，需捕获并返回 HALT
3. **subprocess 超时** — 使用 `asyncio.wait_for(proc.communicate(), timeout=N)` + 三阶段清理
4. **glob 搜索** — 使用 `pathlib.Path.glob()` 同步方法（I/O 轻量，无需 async）
5. **pytest-asyncio auto mode** — `pyproject.toml` 已配置 `asyncio_mode=auto`
6. **mock subprocess** — 单元测试使用 `unittest.mock.patch("asyncio.create_subprocess_exec")` mock CLI 调用
7. **BMAD config 路径** — `{project-root}` 占位符需要用实际 `project_path` 替换后再 glob
8. **Codex 非交互探测** — 若不带 `--skip-git-repo-check`，在非 git cwd 下会产生假阴性；story 中必须显式规避
9. **结果可查询性** — `check_item` 需稳定命名，后续 Story 1.4b / TUI 若读取 SQLite 才能可靠聚合

### References

- [Source: _bmad-output/planning-artifacts/epics.md — Epic 1, Story 1.4a]
- [Source: _bmad-output/planning-artifacts/architecture.md — Decision 10: Preflight Check 三层协议]
- [Source: _bmad-output/planning-artifacts/prd.md — FR33 ato init, FR34 CLI 检测, NFR5 启动≤3s]
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md — UX-DR6 PreflightOutput 四级状态编码]
- [Source: _bmad/bmm/workflows/4-implementation/bmad-create-story/discover-inputs.md — whole/sharded artifact 发现规则]
- [Source: src/ato/models/db.py — init_db, get_connection, migrations pattern]
- [Source: src/ato/models/schemas.py — _StrictBase, SCHEMA_VERSION, ConfigError]
- [Source: src/ato/models/migrations.py — MIGRATIONS dict, @_register pattern]
- [Source: local CLI help — `claude --help`, `codex exec --help`；验证非交互认证探测 flag]

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6 (1M context)

### Debug Log References

### Completion Notes List

- ✅ Task 1: CheckStatus/CheckLayer/CheckResult 模型定义完成，SCHEMA_VERSION 升级到 3，v2→v3 迁移创建 preflight_results 表 + 索引，insert_preflight_results CRUD 实现，18 个单元测试全通过
- ✅ Task 2: Layer 1 系统环境检查实现完成 — _check_python_version (sys.version_info)、_check_cli_installed (通用 CLI 检测)、_check_claude_auth/codex_auth（含非交互探测 flag）、_run_subprocess（三阶段清理）、check_system_environment 编排函数（include_auth 支持、安装失败跳过 auth），15 个单元测试全通过
- ✅ Task 3: Layer 2 项目结构检查实现完成 — _check_git_repo (git rev-parse)、_check_bmad_config（Pydantic 验证必填字段）、_check_bmad_skills（三路径检测）、_check_ato_yaml，9 个单元测试全通过
- ✅ Task 4: Layer 3 编排前置 Artifact 检查实现完成 — _load_bmad_paths（从 BMAD config 解析路径 + {project-root} 占位符替换）、_check_artifact_glob（通用 whole/sharded glob 检测）、check_artifacts 编排函数（Epic/PRD/Arch/UX/project-context/impl 目录），10 个单元测试全通过
- ✅ Task 5: run_preflight 编排函数实现完成 — 三层顺序执行 + HALT 短路 + include_auth 双模式 + uuid4 run_id + SQLite 持久化（连接不跨越外部 IO），7 个集成测试全通过
- ✅ Task 6: ruff check 通过、mypy strict 通过（新增 types-PyYAML dev 依赖）、59 个新测试全通过、353 总测试全通过（0 regressions，原有 312 + 新增 41）
- ✅ Code Review Fix #1: impl_directory 检查新增 os.access(W_OK) 可写性验证，修复已存在只读目录误报 PASS 的问题
- ✅ Code Review Fix #2: _load_bmad_paths 相对路径现在相对于 project_path 解析，而非依赖 cwd
- ✅ Code Review Fix #3: 三个测试文件全部通过 ruff check（修复 21 个 lint 问题：unused imports、blind Exception、ambiguous variable names、line length 等）

### File List

- `src/ato/preflight.py` — **重写**：从 1 行 docstring 扩展为完整三层检查引擎（~590 行）
- `src/ato/models/schemas.py` — **修改**：新增 CheckStatus、CheckLayer、CheckResult；SCHEMA_VERSION 2→3
- `src/ato/models/migrations.py` — **修改**：新增 _migrate_v2_to_v3（preflight_results 表 + idx_preflight_run_id 索引）
- `src/ato/models/db.py` — **修改**：新增 insert_preflight_results()，import CheckResult
- `src/ato/models/__init__.py` — **修改**：导出 CheckResult、insert_preflight_results
- `tests/unit/test_preflight_schema.py` — **新建**：18 个测试（CheckResult 模型验证 + migration v3 + CRUD）
- `tests/unit/test_preflight.py` — **新建**：36 个测试（Layer 1/2/3 单元测试，含 readonly impl dir + 相对路径）
- `tests/integration/test_preflight_integration.py` — **新建**：7 个测试（三层编排 + 持久化集成测试）
- `pyproject.toml` — **修改**：新增 types-PyYAML dev 依赖
- `uv.lock` — **自动更新**：types-PyYAML 锁定

### Change Log

- 2026-03-24: validate-create-story 修订 —— 对齐 create-story 的 whole/sharded artifact 发现规则，补充 BMAD skills 多路径检测，修正 Claude/Codex 非交互认证探测 flag，并明确 SQLite/外部 IO 边界
- 2026-03-24: Story 实现完成 —— 实现 Preflight 三层检查引擎（系统环境/项目结构/编排前置 Artifact），含 HALT 短路逻辑、include_auth 双模式、SQLite 持久化，共 59 个新测试（18 schema + 34 preflight + 7 集成），353 总测试全通过
- 2026-03-24: Code Review 修复 —— 修复 3 项 review findings：impl_directory 可写性验证、BMAD config 相对路径解析、测试文件 ruff lint 合规。355 总测试全通过
