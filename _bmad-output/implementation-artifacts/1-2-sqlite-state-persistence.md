# Story 1.2: 操作者可确认 story 和 task 状态在崩溃后零丢失

Status: done

## Story

As a 操作者,
I want 系统将 story 和 task 的运行时状态持久化到 SQLite（WAL 模式），崩溃后数据零丢失,
so that 意外中断后无需手动重建状态，系统可自动恢复。

## Acceptance Criteria

1. **AC1: 数据库初始化、连接约束与 WAL 模式**
   - Given 数据库尚未创建
   - When 调用 `init_db(db_path)` 函数
   - Then 创建 SQLite 数据库并设置 `journal_mode=WAL`、`busy_timeout=5000`、`synchronous=NORMAL`
   - And 若 `db_path.parent` 不存在则自动创建
   - And 初始化连接显式启用 `foreign_keys=ON`
   - And 创建 `stories` 和 `tasks` 表，以及通用 `approvals` 表（供后续 story 按需写入 approval 记录）
   - And `PRAGMA user_version` 设置为当前 schema 版本号
   - And 后续通过 `get_connection(db_path)` 打开的连接仍显式启用 `busy_timeout=5000`、`synchronous=NORMAL`、`foreign_keys=ON`，并确认数据库保持 `journal_mode=wal`

2. **AC2: Schema 迁移机制**
   - Given 数据库 schema 版本低于代码版本
   - When 调用 `run_migrations(db, current_version, target_version)`
   - Then 按序执行迁移函数，且每一步只在成功后更新 `user_version`
   - And 单步迁移失败时回滚该步事务并抛出 `RecoveryError`，不破坏已有已提交数据

3. **AC3: WAL 崩溃恢复零数据丢失**
   - Given SQLite WAL 模式已启用，且 `stories` / `tasks` 中已存在已提交记录
   - When 写入完成 `commit` 后数据库被重新打开（不要求实现完整 recovery orchestration）
   - Then 新连接可读取到提交前的 story/task 状态，已提交事务的数据完整存在
   - And 未提交事务的数据不会泄漏到恢复后的读取结果中
   - And 持久化的 `tasks` 记录保留后续恢复所需的 `status`、`pid`、`expected_artifact` 字段

4. **AC4: Pydantic 模型严格验证**
   - Given models/schemas.py 中的 Pydantic models
   - When 对 StoryRecord、TaskRecord、ApprovalRecord 调用 `model_validate()`
   - Then 外部输入经过严格类型验证（禁止隐式类型宽松转换），非法数据被拒绝并给出清晰错误信息

5. **AC5: SQL 注入与外键完整性防护**
   - Given 任意数据库操作
   - When 使用参数化查询执行 SQL
   - Then 不存在手动拼接 SQL 的代码路径
   - And 写入 orphan `tasks` / `approvals` 记录时会因外键约束失败，而不是静默写入

## Tasks / Subtasks

- [x] Task 1: 实现 Pydantic 数据模型 (AC: #4)
  - [x] 1.1 在 `src/ato/models/schemas.py` 中定义 `StoryRecord` — 字段：`story_id: str`（主键）、`title: str`、`status: Literal[...]`（story 生命周期状态）、`current_phase: str`、`worktree_path: str | None`、`created_at: datetime`、`updated_at: datetime`
  - [x] 1.2 定义 `TaskRecord` — 字段：`task_id: str`（主键，UUID）、`story_id: str`（外键）、`phase: str`、`role: str`、`cli_tool: Literal[“claude”, “codex”]`、`status: Literal[“pending”, “running”, “paused”, “completed”, “failed”]`、`pid: int | None`、`expected_artifact: str | None`、`context_briefing: str | None`（JSON 序列化的 ContextBriefing）、`started_at: datetime | None`、`completed_at: datetime | None`、`exit_code: int | None`、`cost_usd: float | None`、`duration_ms: int | None`、`error_message: str | None`
  - [x] 1.3 定义 `ApprovalRecord` — 字段：`approval_id: str`（主键，UUID）、`story_id: str`（外键）、`approval_type: str`、`status: Literal[“pending”, “approved”, “rejected”]`、`payload: str | None`（JSON）、`decision: str | None`、`decided_at: datetime | None`、`created_at: datetime`
  - [x] 1.4 定义异常类层次在 `src/ato/models/schemas.py`：`ATOError`（基类）→ `CLIAdapterError` / `StateTransitionError` / `RecoveryError` / `ConfigError`
  - [x] 1.5 定义 `SCHEMA_VERSION: int = 1` 常量（跨模块常量放 schemas.py）
  - [x] 1.6 为所有 record model 配置严格校验（如共享基类或 `ConfigDict(strict=True, extra=”forbid”)`），避免 `”1”`→`1` 之类的隐式转换
  - [x] 1.7 约定 SQLite 读取路径先将 ISO 8601 字符串反序列化为 `datetime`，再调用 `model_validate()`，保证”严格校验”和”数据库 round-trip”同时成立

- [x] Task 2: 实现 SQLite 连接管理与 schema DDL (AC: #1, #5)
  - [x] 2.1 在 `src/ato/models/db.py` 中实现 `async def init_db(db_path: Path) -> None`：必要时创建父目录、创建数据库、设置 PRAGMA（WAL + busy_timeout + synchronous + foreign_keys）、创建 tables、设置 user_version
  - [x] 2.2 定义 `stories` 表 DDL：`story_id TEXT PRIMARY KEY, title TEXT NOT NULL, status TEXT NOT NULL, current_phase TEXT NOT NULL, worktree_path TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL`
  - [x] 2.3 定义 `tasks` 表 DDL：包含所有 TaskRecord 对应列，`story_id` 外键引用 `stories(story_id)`
  - [x] 2.4 定义 `approvals` 表 DDL：包含所有 ApprovalRecord 对应列，`story_id` 外键引用 `stories(story_id)`
  - [x] 2.5 实现 `async def get_connection(db_path: Path) -> aiosqlite.Connection` 辅助函数：每次连接设置 `busy_timeout=5000`、`synchronous=NORMAL`、`foreign_keys=ON`，确认 `journal_mode=wal`，并设置 `db.row_factory = aiosqlite.Row`
  - [x] 2.6 所有 SQL 使用参数化查询（`?` 占位符），禁止字符串拼接
  - [x] 2.7 初始化后执行一次外键完整性自检（至少在测试中覆盖 orphan insert 失败），避免”DDL 写了 FOREIGN KEY 但运行时未开启约束”的伪安全状态

- [x] Task 3: 实现 CRUD 辅助函数 (AC: #1, #4, #5)
  - [x] 3.1 在 `src/ato/models/db.py` 中实现 `async def insert_story(db, story: StoryRecord) -> None`
  - [x] 3.2 实现 `async def get_story(db, story_id: str) -> StoryRecord | None`
  - [x] 3.3 实现 `async def update_story_status(db, story_id: str, status: str, phase: str) -> None`（更新 status + current_phase + updated_at）
  - [x] 3.4 实现 `async def insert_task(db, task: TaskRecord) -> None`
  - [x] 3.5 实现 `async def get_tasks_by_story(db, story_id: str) -> list[TaskRecord]`
  - [x] 3.6 实现 `async def update_task_status(db, task_id: str, status: str, **kwargs) -> None`（支持更新 pid、exit_code、cost_usd 等可选字段）
  - [x] 3.7 实现 `async def insert_approval(db, approval: ApprovalRecord) -> None`
  - [x] 3.8 实现 `async def get_pending_approvals(db) -> list[ApprovalRecord]`
  - [x] 3.9 所有读取操作返回 Pydantic 模型（先做 SQLite 行到 Python 类型的反序列化，再通过 `model_validate()` 构造）

- [x] Task 4: 实现 Schema 迁移机制 (AC: #2)
  - [x] 4.1 在 `src/ato/models/migrations.py` 中实现 `async def run_migrations(db: aiosqlite.Connection, current_version: int, target_version: int) -> None`
  - [x] 4.2 实现迁移注册机制：`MIGRATIONS: dict[int, Callable]` 映射版本号到迁移函数
  - [x] 4.3 迁移函数按序执行；每个版本步骤在独立事务中完成，并且只在成功后更新 `PRAGMA user_version`
  - [x] 4.4 迁移失败时回滚当前步骤并抛出 `RecoveryError`，包含失败的版本号和原因
  - [x] 4.5 在 `init_db()` 中调用 `run_migrations()` — 新建数据库时从 0 迁移到 SCHEMA_VERSION

- [x] Task 5: 更新 `__init__.py` 导出与模块集成 (AC: #1)
  - [x] 5.1 在 `src/ato/models/__init__.py` 中导出公共接口：`StoryRecord`, `TaskRecord`, `ApprovalRecord`, `init_db`, `get_connection`, `SCHEMA_VERSION`
  - [x] 5.2 在 `src/ato/models/schemas.py` 中导出异常类：`ATOError`, `CLIAdapterError`, `StateTransitionError`, `RecoveryError`, `ConfigError`

- [x] Task 6: 编写单元测试 (AC: #1, #2, #3, #4, #5)
  - [x] 6.1 创建 `tests/unit/test_schemas.py`：验证 StoryRecord/TaskRecord/ApprovalRecord 的正确输入验证和非法输入拒绝，并覆盖 strict mode 下的类型拒绝
  - [x] 6.2 创建 `tests/unit/test_db.py`：验证 `init_db()` 创建数据库、自动创建父目录、设置 PRAGMA、创建表、写入 `user_version`
  - [x] 6.3 在 `test_db.py` 中测试 `get_connection()` 为每个新连接重新应用 `busy_timeout`、`synchronous`、`foreign_keys`，并保持 `journal_mode=wal`
  - [x] 6.4 在 `test_db.py` 中测试 CRUD 操作的完整往返（insert → get → update → get），并验证 datetime round-trip 后仍通过严格 `model_validate()`
  - [x] 6.5 在 `test_db.py` 中用包含引号/SQL 关键字的输入做行为测试，验证数据被当作普通值保存，表结构未受影响
  - [x] 6.6 在 `test_db.py` 中测试 orphan `tasks` / `approvals` 写入会因外键约束失败
  - [x] 6.7 创建 `tests/unit/test_migrations.py`：验证迁移从 v0 到 SCHEMA_VERSION 成功执行
  - [x] 6.8 在 `test_migrations.py` 中测试迁移失败时回滚当前步骤并抛出 `RecoveryError`

- [x] Task 7: WAL 崩溃恢复验证测试 (AC: #3)
  - [x] 7.1 创建 `tests/integration/test_wal_recovery.py`：构造数据库状态，验证 WAL 模式下”已提交可恢复、未提交不可见”的持久化语义
  - [x] 7.2 测试场景：写入 story/task 并 `commit` → 重新打开连接 → 验证数据完整、恢复关键字段仍存在
  - [x] 7.3 测试场景：事务内写入但不 `commit` → 关闭连接/回滚 → 重新打开 → 验证未提交数据不存在
  - [x] 7.4 验证 `PRAGMA journal_mode` 返回 `wal`

- [x] Task 8: 质量验证 (AC: 全部)
  - [x] 8.1 执行 `uv run ruff check src/ato/models/` 通过
  - [x] 8.2 执行 `uv run mypy src/ato/models/` 通过（strict mode）
  - [x] 8.3 执行 `uv run pytest tests/unit/test_schemas.py tests/unit/test_db.py tests/unit/test_migrations.py tests/integration/test_wal_recovery.py` 全部通过
  - [x] 8.4 执行 `uv run pre-commit run --all-files` 通过

## Dev Notes

### 关键架构约束

- **SQLite WAL 模式**是系统崩溃恢复的基础（NFR6）——本 story 建立这个基础
- **单进程写 + TUI 独立进程读** — WAL 天然支持并发读写
- **PRAGMA 三件套**必须在每个连接上设置：`journal_mode=WAL` + `busy_timeout=5000` + `synchronous=NORMAL`
- **SQLite 外键约束不是“写了 DDL 就自动生效”** — `foreign_keys=ON` 必须在每个连接上显式启用，否则 `tasks.story_id` / `approvals.story_id` 约束不会真正执行
- **写事务尽可能短** — 读数据、处理逻辑、然后单次写入 + commit
- **禁止在 `async with aiosqlite.connect()` 块内 await 外部 IO**（防写锁长期持有）
- **参数化查询** — 所有 SQL 使用 `?` 占位符，禁止字符串拼接（安全规则）
- **本 story 只交付“恢复所需状态已可靠落盘”** — `ato start` 的恢复编排、PID 分类和摘要展示在后续 story 实现，此处不把未来恢复流程塞进当前验收范围

### 连接策略（来自架构 Decision）

| 场景 | 连接模式 | 理由 |
|------|---------|------|
| TransitionQueue consumer | 长连接（consumer 生命周期复用） | 串行写入，无并发冲突 |
| Orchestrator 轮询读取 | 短连接 | 读不阻塞写，确保最新 WAL 数据 |
| TUI 读取/写入 | 短连接 + 立即 commit | 独立进程，最小化写锁持有 |

本 story 实现 `get_connection()` 辅助函数，设置 PRAGMA 并返回连接。上层消费者（TransitionQueue、TUI 等）在后续 story 中决定长/短连接策略。
为避免 CRUD 层把 tuple 下标写死，连接应配置 `db.row_factory = aiosqlite.Row`，让读取路径按列名映射到 Pydantic 模型。

### Schema 设计要点

**表按需创建策略：** `stories`、`tasks`、`approvals` 在本 story 创建。`findings`、`cost_log`、`preflight_results` 等表在首次使用的 story 中用 `CREATE TABLE IF NOT EXISTS` 创建。

**`tasks` 表关键列（为崩溃恢复设计）：**
- `pid: INTEGER` — 子进程 PID，崩溃恢复时用于检查进程存活
- `expected_artifact: TEXT` — 预期产出文件路径，崩溃恢复时用于检查产出是否存在
- `status` 状态值含义：
  - `pending` — 已创建未启动
  - `running` — 正在执行（崩溃恢复时若发现此状态 = 异常中断）
  - `paused` — `ato stop` 优雅停止标记（正常恢复：直接重调度）
  - `completed` — 正常完成
  - `failed` — 执行失败
- `context_briefing: TEXT` — JSON 序列化的 ContextBriefing（FR53），作为跨 task fresh session 输入

**datetime 存储格式：** ISO 8601 字符串（`TEXT` 类型），因为 SQLite 无原生 datetime。Pydantic 模型中使用 `datetime` 类型，CRUD 函数负责序列化/反序列化。

**严格校验与数据库 round-trip：**
- Pydantic record model 应启用 strict mode，避免 `"1"`、`"true"` 之类的宽松输入被静默接受
- 但 SQLite 读取出来的 datetime 是 `TEXT`，因此 CRUD 读取路径必须先做反序列化，再调用 `model_validate()`
- 目标是同时保证“外部输入严格验证”与“数据库往返后模型仍可正确构造”

### 迁移策略（架构 Decision 5）

- `PRAGMA user_version` 追踪 schema 版本号 — SQLite 原生机制，零额外依赖
- `init_db()` 调用 `run_migrations(current_version=0, target_version=SCHEMA_VERSION)`
- 后续 `ato start` 时检查版本号，按序执行增量迁移
- 迁移函数放 `models/migrations.py`，`db.py` 只负责连接管理和当前 schema DDL

### 异常类层次

```
ATOError (基类)
├── CLIAdapterError      — CLI 调用失败
├── StateTransitionError — 状态机转换非法
├── RecoveryError        — 崩溃恢复/迁移失败
└── ConfigError          — 配置解析错误
```

定义在 `models/schemas.py`（跨模块常量和类型的统一位置）。异常类应该是简单的继承，不需要额外逻辑。

### Pydantic v2 使用规范

- **MVP 全部走 `model_validate()`** — 禁止使用 `model_construct`（Growth 再评估热路径）
- 用 `Literal` 表达领域枚举值，例如 `status: Literal["pending", "running", "paused", "completed", "failed"]`
- 禁止在 validator 中做 IO 操作
- `model_json_schema()` 可用于自动生成 JSON Schema 文件（本 story 不需要）

### 最新技术核对（2026-03-24）

- SQLite 官方文档明确说明 `PRAGMA foreign_keys` 默认不能假定为开启，且必须对每个数据库连接单独启用；不能在事务中途切换
- SQLite WAL 官方文档强调：恢复语义的核心边界是“已提交事务保留、未提交事务不可见”，因此测试必须分开覆盖这两类场景
- Pydantic 官方文档说明：若要真正做到“严格类型验证”，应显式启用 strict mode（如 `ConfigDict(strict=True)`）；这要求数据库读取路径先把 ISO 时间字符串转回 `datetime`

### 后续 Story 依赖本 story 的接口

| 消费者 | 使用的接口 | Story |
|--------|-----------|-------|
| Story 2A.1 状态机 | `update_story_status()`, `get_story()` | Epic 2A |
| Story 2A.2 TransitionQueue | `get_connection()` 长连接 | Epic 2A |
| Story 1.4a Preflight | `init_db()` | Epic 1 |
| Story 5.1 崩溃恢复 | `tasks` 表 `pid`/`expected_artifact`/`status` 列 | Epic 5 |
| Story 3.1 Finding 追踪 | `CREATE TABLE IF NOT EXISTS findings` | Epic 3 |
| TUI 仪表盘 | `get_connection()` 短连接读取 | Epic 6 |

### 测试策略

- **单元测试**使用 `tmp_path` fixture 创建临时数据库，不需要真实文件系统路径
- **WAL 恢复测试**是函数式测试（构造数据库状态验证恢复），不需要真实杀进程；重点验证“已提交可恢复、未提交不可见”
- **conftest.py** 中创建 `db_path` fixture：`tmp_path / ".ato" / "state.db"`
- `pytest-asyncio` 已配置 `asyncio_mode=auto`，async 测试函数直接用 `async def test_*()`

### 反模式清单（本 story 相关）

- ❌ 禁止手动拼接 SQL（用 `?` 参数化查询）
- ❌ 禁止在 `models/` 外定义 Pydantic model
- ❌ 禁止在 Pydantic validator 中做 IO
- ❌ MVP 禁止使用 `model_construct`
- ❌ 禁止在 SQLite 写事务中 await 外部 IO
- ❌ 禁止静默吞异常（至少 `structlog.warning`）
- ❌ 禁止 `print()` 输出日志（用 structlog）

### Story 1.1 遗留的关键上下文

- 所有目标文件（`db.py`、`schemas.py`、`migrations.py`）已作为空模块存在，仅含 docstring
- `aiosqlite>=0.22.1` 已作为核心依赖安装
- `pydantic>=2.0` 已作为核心依赖安装
- `structlog` 已配置（`src/ato/logging.py`），可用于数据库操作日志
- mypy strict mode 已启用 — 所有公共函数必须有完整类型标注
- `tests/conftest.py` 已存在（空模块）
- hatchling 已配置 `packages = ["src/ato"]`
- ruff 已排除 BMAD 目录

### 命名约定速查

| 范围 | 规则 | 示例 |
|------|------|------|
| SQLite 表名 | snake_case 复数 | `stories`, `tasks`, `approvals` |
| SQLite 列名 | snake_case | `story_id`, `created_at` |
| Pydantic 模型 | PascalCase + 用途后缀 | `StoryRecord`, `TaskRecord` |
| 自定义异常 | PascalCase + Error | `RecoveryError` |
| 常量 | UPPER_SNAKE_CASE | `SCHEMA_VERSION` |
| 测试文件 | `test_<module>.py` | `test_db.py` |

### Project Structure Notes

- `src/ato/models/db.py` — SQLite 连接管理 + schema DDL + CRUD 辅助函数
- `src/ato/models/schemas.py` — 所有 Pydantic models + 异常类 + 跨模块常量
- `src/ato/models/migrations.py` — PRAGMA user_version 迁移函数
- `src/ato/models/__init__.py` — 公共接口导出
- 运行时数据库路径：`.ato/state.db`（不提交到 git）
- `tests/unit/test_schemas.py` — Pydantic 模型验证测试
- `tests/unit/test_db.py` — 数据库初始化与 CRUD 测试
- `tests/unit/test_migrations.py` — 迁移机制测试
- `tests/integration/test_wal_recovery.py` — WAL 崩溃恢复验证

### References

- [Source: _bmad-output/planning-artifacts/epics.md — Epic 1, Story 1.2]
- [Source: _bmad-output/planning-artifacts/architecture.md — Decision 5 SQLite 迁移策略, Decision 7 正常重启 vs 崩溃恢复, SQLite 连接策略]
- [Source: _bmad-output/planning-artifacts/prd.md — FR24 状态持久化, NFR6 WAL 零数据丢失, NFR7 自动恢复]
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md — ato init 初始化流程, 崩溃恢复摘要显示]
- [Source: _bmad-output/project-context.md — SQLite/aiosqlite 规则, Pydantic v2 规则, 异常层次]
- [Source: _bmad-output/implementation-artifacts/1-1-project-scaffolding-dev-toolchain.md — 已建立的文件结构与工具链]
- [Source: https://www.sqlite.org/foreignkeys.html — `foreign_keys` 按连接启用规则]
- [Source: https://www.sqlite.org/wal.html — WAL 提交事务恢复语义]
- [Source: https://docs.pydantic.dev/latest/concepts/strict_mode/ — strict mode / `ConfigDict(strict=True)`]

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6 (1M context)

### Debug Log References

无需 debug — 全部测试一次通过。

### Completion Notes List

- 实现 Pydantic 数据模型（StoryRecord、TaskRecord、ApprovalRecord），使用 `_StrictBase` 共享基类启用 `strict=True` + `extra="forbid"`
- 实现异常类层次：ATOError → CLIAdapterError / StateTransitionError / RecoveryError / ConfigError
- 实现 SQLite 连接管理（init_db + get_connection），每连接设置 PRAGMA 三件套 + foreign_keys=ON
- 实现 3 张表 DDL（stories、tasks、approvals），外键约束引用 stories(story_id)
- 实现 8 个 CRUD 辅助函数，所有 SQL 使用参数化查询，读取路径先反序列化 datetime 再 model_validate
- 实现 Schema 迁移机制（MIGRATIONS 注册表 + run_migrations 执行器），init_db 通过迁移创建表
- 编写 20 个 schema 验证测试、30 个 db 测试、6 个迁移测试、6 个 WAL 恢复测试，共 62 个新测试
- 全部 101 测试通过（62 新 + 39 既有），零回归
- ruff check、ruff format、mypy strict、pre-commit 全部通过
- ✅ 修复 code review Patch 1: 迁移 SAVEPOINT 事务边界，失败时 DDL 副作用被回滚
- ✅ 修复 code review Patch 2: update_story_status / update_task_status 写前 Pydantic 校验，拒绝非法 status 和 datetime 值
- ✅ 修复 code review Patch 3: get_connection 用 if+raise 替代 assert，失败时先 close 连接
- ✅ 修复 code review Patch 4: update_story_status 校验 phase 类型；update_task_status 对所有 kwargs 做严格类型检查（pid/exit_code/cost_usd/duration_ms/expected_artifact/context_briefing/error_message）

### Change Log

- 2026-03-24: 实现 Story 1.2 SQLite 状态持久化层 — Pydantic 模型、连接管理、CRUD、迁移机制、全量测试
- 2026-03-24: 修复 code review 3 个 patch findings — 迁移事务边界、update 写前校验、WAL 检查可靠性
- 2026-03-24: 修复 code review Patch 4 — update_* 全字段写前类型校验，堵住 SQLite 隐式类型转换
- 2026-03-24: 修复 code review Round 2 — bool 子类漏洞修复 + lint 修复（timezone.utc→UTC, unused import）

### File List

- src/ato/models/schemas.py (新增内容)
- src/ato/models/db.py (新增内容)
- src/ato/models/migrations.py (新增内容)
- src/ato/models/__init__.py (更新导出)
- tests/conftest.py (新增 db_path / initialized_db_path fixtures)
- tests/unit/test_schemas.py (新增)
- tests/unit/test_db.py (新增)
- tests/unit/test_migrations.py (新增)
- tests/integration/test_wal_recovery.py (新增)
