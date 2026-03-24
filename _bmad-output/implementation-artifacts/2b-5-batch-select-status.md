# Story 2B.5: 操作者可选择 story batch 并查看状态

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a 操作者,
I want 通过 `ato batch select` 选择要执行的 story batch，通过 `ato batch status` 查看进度,
So that 可以按自己的节奏推进工作。

## Acceptance Criteria

1. **AC1 — Batch 选择（FR12）**
   - **Given** PM agent 分析 epic/story 的优先级和依赖关系
   - **When** 调用 `ato batch select`
   - **Then** 展示推荐的 batch 方案供操作者选择
   - **And** 操作者确认后，batch 中首个可执行 story 进入 `creating`，其余已选 story 以 `queued` 语义保留在当前 batch 中等待
   - **And** 若某个候选 story 尚未存在于 SQLite `stories` 表，系统先创建对应 `StoryRecord` 再写入 `batch_stories`

2. **AC2 — Batch 状态查看（FR38）**
   - **Given** batch 已选定并正在执行
   - **When** 操作者运行 `ato batch status`
   - **Then** 显示当前 batch 的整体进度（已完成/进行中/待执行/失败）
   - **And** 进度分类规则固定且可测试：`done` = 已完成，`planning` / `in_progress` / `review` / `uat` = 进行中，`backlog` / `ready` 或 `current_phase="queued"` = 待执行，`blocked` = 失败

3. **AC3 — 空状态引导（UX-DR13）**
   - **Given** 尚无 batch 被选择
   - **When** 操作者运行 `ato batch status`
   - **Then** 显示引导文字"尚无 story。运行 `ato batch select` 选择第一个 batch"

4. **AC4 — JSON 输出支持**
   - **Given** 操作者需要脚本化操作
   - **When** 运行 `ato batch status --json`
   - **Then** 以结构化 JSON 格式输出到 stdout

5. **AC5 — 错误处理**
   - **Given** 数据库未初始化
   - **When** 调用任何 batch 子命令
   - **Then** 输出明确错误信息到 stderr 并返回退出码 1
   - **Given** epics 文件不存在
   - **When** 调用 `ato batch select`
   - **Then** 输出明确错误信息到 stderr 并返回退出码 1

## Tasks / Subtasks

- [ ] Task 1: Schema 扩展 — 新增 `batches` 表和相关模型 (AC: #1, #2)
  - [ ] 1.1 在 `src/ato/models/schemas.py` 中新增 `BatchStatus` Literal 类型和 `BatchRecord` Pydantic 模型
  - [ ] 1.2 在 `src/ato/models/db.py` 中新增 `batches` 表 DDL、`batch_stories` 关联表 DDL、`sequence_no` 顺序列，以及“同一时间仅允许 1 个 active batch”的约束（partial unique index 或等价校验）
  - [ ] 1.3 在 `src/ato/models/migrations.py` 中注册 `MIGRATIONS[2]` — 创建 `batches` 和 `batch_stories` 两张表
  - [ ] 1.4 更新 `SCHEMA_VERSION = 2`
  - [ ] 1.5 在 `db.py` 中新增 Batch 相关 DB 辅助函数：`insert_batch`、`insert_batch_story_links`、`get_active_batch`、`get_batch_stories`、`get_batch_progress`
  - [ ] 1.6 更新 `src/ato/models/__init__.py` 导出新增公共接口

- [ ] Task 2: Batch 推荐引擎 — 纯逻辑的 story 分析与 batch 构建 (AC: #1)
  - [ ] 2.1 创建 `src/ato/batch.py` — batch 选择核心逻辑模块
  - [ ] 2.2 实现 `load_epics(epics_path) -> list[EpicInfo]` — 从 epics.md 解析 canonical story key（如 `2b-5-batch-select-status`）、标题、依赖关系、推荐顺序元数据
  - [ ] 2.3 实现 `recommend_batch(stories_state, epics_info, max_stories) -> BatchProposal` — 基于依赖图和当前状态生成推荐 batch 方案；`max_stories` 来自 CLI 显式参数或模块默认值 5，不依赖 Story 1.3 配置引擎
  - [ ] 2.4 实现 `confirm_batch(db, proposal, selected_ids) -> BatchRecord` — 用单个 SQLite 事务完成 batch 创建、缺失 `StoryRecord` 补齐、顺序化 `batch_stories` 写入，以及 story 状态/`current_phase` 更新
  - [ ] 2.5 明确定义批次状态聚合规则：头部 story 写入 `status="planning", current_phase="creating"`；后续 story 写入 `status="backlog", current_phase="queued"`；`ato batch status` 按 AC2 规则聚合 4 个进度桶
  - [ ] 2.6 在 `batch.py` 中定义 `BatchRecommender` 协议（Protocol），本地推荐为默认实现，后续 AI 推荐可插拔替换

- [ ] Task 3: CLI 命令实现 (AC: #1, #2, #3, #4, #5)
  - [ ] 3.1 在 `src/ato/cli.py` 中创建 `batch_app = typer.Typer()` 子命令组，注册到主 `app`
  - [ ] 3.2 实现 `ato batch select` 命令 — 通过显式 CLI 参数和约定默认路径工作（至少支持 `--epics-file`、`--db-path`、`--max-stories`、`--story-ids`），不要求 Story 1.3 的 `load_config()`
  - [ ] 3.3 实现 `ato batch status` 命令 — 仅依赖 SQLite 查询 active batch、汇总进度、格式化输出；已存在 active batch 时不需要 epics 文件
  - [ ] 3.4 支持 `--json` 标志输出结构化 JSON 到 stdout
  - [ ] 3.5 实现空状态处理和错误处理：缺 DB 对所有 batch 子命令报错；缺 epics 仅阻止 `select`
  - [ ] 3.6 若已有 active batch，`ato batch select` 默认拒绝创建新 batch，并输出明确提示（本 story 不实现 replace/cancel CLI）

- [ ] Task 4: 测试 (AC: #1-#5)
  - [ ] 4.1 `tests/unit/test_batch.py` — batch 推荐逻辑单元测试（epics 解析、依赖分析、推荐算法）
  - [ ] 4.2 `tests/unit/test_db.py` 追加 — batch 相关 CRUD 测试（insert_batch、insert_batch_story_links、顺序保持、单 active batch 约束、get_batch_progress）
  - [ ] 4.3 `tests/unit/test_migrations.py` 追加 — MIGRATIONS[2] 迁移测试
  - [ ] 4.4 `tests/unit/test_cli_batch.py` — CLI 命令测试，使用 `typer.testing.CliRunner` 验证退出码和输出格式
  - [ ] 4.5 为 `confirm_batch()` 编写事务性测试：缺失 story 自动补齐；任一步骤失败时 batch/link/story 更新整体回滚
  - [ ] 4.6 为 `ato batch status` 编写状态映射测试：`queued` 语义显示为待执行、`blocked` 显示为失败，且无 epics 文件时仍可读取已有 active batch

## Dev Notes

### 重要设计决策

**1. Batch 概念建模**

本 Story 引入 "batch" 作为一等实体。一个 batch 是操作者选定的一组 story 的集合，代表一个工作周期。数据模型：

```
batches 表：
  batch_id TEXT PRIMARY KEY          -- UUID
  status TEXT NOT NULL               -- active / completed / cancelled
  created_at TEXT NOT NULL
  completed_at TEXT

batch_stories 关联表：
  batch_id TEXT NOT NULL REFERENCES batches(batch_id)
  story_id TEXT NOT NULL REFERENCES stories(story_id)
  sequence_no INTEGER NOT NULL       -- batch 内串行顺序
  PRIMARY KEY (batch_id, story_id)
  UNIQUE(batch_id, sequence_no)
```

额外约束：

- 同一时间只允许 1 个 `active` batch；如 SQLite 版本允许，优先使用 partial unique index 固化该约束
- 由于 `batch_stories.story_id` 外键引用 `stories(story_id)`，`confirm_batch()` 必须先补齐缺失的 `StoryRecord`

**2. Story 状态映射**

当前 `StoryStatus` Literal 定义为：`"backlog" | "planning" | "ready" | "in_progress" | "review" | "uat" | "done" | "blocked"`。

AC 中提到 `queued → creating` 的转换，但这些状态值来自完整的状态机（Story 2A.1），当前可能尚未实现。为了让 2B.5 在仅依赖 Story 1.2 时仍可落地，详细阶段写入 `current_phase`，高层 `status` 继续复用现有 `StoryStatus`。

- batch 头部 story：`status="planning"`，`current_phase="creating"`
- batch 中后续 story：`status="backlog"`，`current_phase="queued"`
- `ato batch status` 聚合规则：
  - `done` → 已完成
  - `blocked` → 失败
  - `current_phase="queued"` 或 `status in {"backlog", "ready"}` → 待执行
  - 其余状态 → 进行中

**不要为本 Story 扩展 `StoryStatus` Literal。** 详细阶段差异通过 `current_phase` 表达，保持与 Story 2A.1 的高层状态映射兼容。

**3. PM Agent 推荐 vs 本地推荐**

FR12 描述 PM agent 分析依赖和优先级。但 2B.5 的唯一前置依赖是 1.2（SQLite），Claude CLI adapter（2B.1）可能尚未实现。实现策略：

- **MVP 实现**：`recommend_batch()` 使用纯 Python 逻辑分析 epics.md 中的依赖关系和优先级，生成推荐方案（不依赖 AI agent）
- **预留接口**：在 `batch.py` 中定义 `BatchRecommender` 协议（Protocol），本地推荐作为默认实现，后续 AI 推荐只需新增实现类
- 这样 2B.5 无需等待 2B.1 即可独立完成

**4. CLI 交互模式**

`ato batch select` 需要操作者交互（选择推荐方案），但 CLI 应保持非交互兼容。方案：

- 默认模式：`typer.prompt()` 让操作者从推荐列表中选择编号
- 非交互模式：`ato batch select --story-ids 2b-1-claude-agent-dispatch,2b-2-codex-agent-review` 直接指定 canonical story keys
- 路径与规模参数显式化：`--epics-file`、`--db-path`、`--max-stories`，避免对尚未实现的配置引擎形成隐式依赖
- 退出码：0 成功、1 一般错误、2 环境错误

**5. 事务边界**

`confirm_batch()` 不是简单串行调用多个已提交的 CRUD。它必须在一个短事务 / SAVEPOINT 内原子完成以下操作：

1. 校验当前不存在其他 active batch
2. 创建 batch 记录
3. 补齐缺失的 `stories` 行
4. 写入带顺序的 `batch_stories` 关联
5. 更新头部 story 为 `creating`，其余 selected stories 为 `queued`

任一步失败必须整体回滚，避免产生“有 batch 无 story link”或“部分 story 已激活”的脏状态。

### 架构约束

- **CLI 命令命名**：kebab-case，typer 子命令组模式。`batch` 是子 app，`select` / `status` 是子命令
- **Typer 退出码**：用 `typer.Exit(code=N)`，不用 `sys.exit()`
- **错误输出**：`typer.echo(msg, err=True)` 输出到 stderr
- **日志**：使用 `structlog.get_logger()` 记录操作日志，不用 `print()`
- **数据库写入**：短事务，写完立即 commit。不在 `async with` 写事务中 await 外部 IO
- **参数化查询**：所有 SQL 使用 `?` 占位符，禁止字符串拼接
- **Pydantic 验证**：所有新模型继承 `_StrictBase`（`strict=True, extra="forbid"`），所有外部输入经 `model_validate()`

### 已有代码模式（必须遵循）

**Pydantic 模型定义** — 参照 `src/ato/models/schemas.py:50-57`：
```python
class _StrictBase(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
```
新增 `BatchRecord` 必须继承 `_StrictBase`。

**CRUD 函数签名** — 参照 `src/ato/models/db.py:162-177`：
```python
async def insert_story(db: aiosqlite.Connection, story: StoryRecord) -> None:
    await db.execute("INSERT INTO ... VALUES (?, ?, ...)", (...))
    await db.commit()
```
- 第一参数永远是 `aiosqlite.Connection`
- 使用 `_dt_to_iso()` / `_iso_to_dt()` 处理 datetime 序列化
- 写入后显式 `await db.commit()`

**Schema 迁移** — 参照 `src/ato/models/migrations.py`：
```python
@_register(2)  # version 2
async def _migrate_v2(db: aiosqlite.Connection) -> None:
    await db.execute(_BATCHES_DDL)
    await db.execute(_BATCH_STORIES_DDL)
    await db.execute("PRAGMA user_version = 2")
```
使用 `@_register(N)` 装饰器注册迁移函数。迁移使用 SAVEPOINT 事务边界。

**CLI 骨架** — 参照 `src/ato/cli.py:1-12`：
```python
app = typer.Typer(name="ato", help="Agent Team Orchestrator")
# 子命令组
batch_app = typer.Typer(help="Batch 管理")
app.add_typer(batch_app, name="batch")
```

**模块导出** — 参照 `src/ato/models/__init__.py`：
使用 `__all__` 白名单导出所有公共接口。

**测试模式** — 参照 `tests/unit/test_db.py`：
- 使用 class 组织：`class TestBatchCrud:`
- 使用 `conftest.py` 中的 `db_path` / `initialized_db_path` fixtures
- `try/finally` 确保连接关闭
- 验证 roundtrip：insert → get → assert 字段一致

### 文件结构

**新增文件：**
- `src/ato/batch.py` — batch 选择核心逻辑（epics 解析、推荐算法、确认流程）
- `tests/unit/test_batch.py` — batch 推荐逻辑测试
- `tests/unit/test_cli_batch.py` — CLI batch 命令测试

**修改文件：**
- `src/ato/models/schemas.py` — 新增 `BatchStatus`、`BatchRecord`、`BatchStoryLink`
- `src/ato/models/db.py` — 新增 DDL、CRUD 函数
- `src/ato/models/migrations.py` — 新增 `MIGRATIONS[2]`
- `src/ato/models/__init__.py` — 更新导出
- `src/ato/cli.py` — 新增 `batch` 子命令组

### Epics 解析策略

`recommend_batch()` 需要分析 epics.md 中的依赖关系。解析规则：

- 读取 epics.md 文件，提取所有 story 的 ID、标题、依赖关系
- 依赖关系格式在 epics.md 的依赖表中：`| Batch | 1.2 → 2B.5（与编排核心并行） |`
- 推荐逻辑：
  1. 过滤出“尚未被 active batch 占用，且 DB 中不存在记录或处于 `backlog` / `ready` / `current_phase="queued"`”的 stories
  2. 按依赖关系拓扑排序
  3. 取前 N 个无阻塞依赖的 stories（N = CLI `--max-stories` 或默认 5）
  4. 输出推荐方案

### 输出格式参考

**`ato batch status` 输出示例（人类可读）：**
```
Batch #1 (2026-03-24 创建)  状态: active

  已完成  ██░░░░░░░░  1/5

  🔄 2b-1-claude-agent-dispatch      creating
  ⏳ 2b-2-codex-agent-review         queued
  ⏳ 2b-3-bmad-skill-parsing         queued
  ✅ 1-2-sqlite-state-persistence    done
  ✖ 2b-4-worktree-isolation          blocked
```

**`ato batch status --json` 输出示例：**
```json
{
  "batch_id": "...",
  "status": "active",
  "created_at": "2026-03-24T10:00:00+00:00",
  "progress": {"done": 1, "active": 1, "pending": 2, "failed": 1, "total": 5},
  "stories": [
    {"story_id": "2b-1-claude-agent-dispatch", "title": "Claude agent dispatch", "status": "planning", "current_phase": "creating"},
    ...
  ]
}
```

**空状态输出（UX-DR13）：**
```
尚无 story。运行 `ato batch select` 选择第一个 batch
```

### Project Structure Notes

- `src/ato/batch.py` 作为新模块放置在 `src/ato/` 顶层（与 `core.py`、`cli.py` 同级），因为 batch 逻辑是编排域的一等概念
- 所有 Pydantic model 定义于 `src/ato/models/schemas.py`（不在 `batch.py` 中定义）
- DDL 定义于 `src/ato/models/db.py`（遵循现有模式）
- CLI 命令注册于 `src/ato/cli.py`
- 本 Story 默认使用约定路径 `_bmad-output/planning-artifacts/epics.md` 与 `.ato/state.db`；Story 1.3 完成后再评估是否切换到 `load_config()`

### 反模式检查清单

- ❌ 不要在 `batch.py` 中定义 Pydantic model（统一放 `models/schemas.py`）
- ❌ 不要用 `print()` 输出（用 `typer.echo()` 给用户、`structlog` 给日志）
- ❌ 不要手动拼接 SQL
- ❌ 不要在非 models/ 目录定义 DDL
- ❌ 不要在 except 中静默吞掉异常
- ❌ 不要用 `sys.exit()`（用 `typer.Exit(code=N)`）
- ❌ 不要假设 StoryStatus 包含 `queued` / `creating` — 先检查当前定义
- ❌ 不要在已有 active batch 时静默覆盖旧 batch
- ❌ 不要在 `confirm_batch()` 中逐步 `commit()`，必须保证整批确认原子化

### References

- [Source: _bmad-output/planning-artifacts/epics.md — Epic 2B, Story 2B.5 (lines 693-708)]
- [Source: _bmad-output/planning-artifacts/epics.md — FR12 PM agent batch 推荐, FR38 ato batch status]
- [Source: _bmad-output/planning-artifacts/epics.md — 依赖关系表: 1.2 → 2B.5]
- [Source: _bmad-output/planning-artifacts/architecture.md — CLI 命名: kebab-case, typer 退出码]
- [Source: _bmad-output/planning-artifacts/architecture.md — SQLite 表名 snake_case 复数, 列名 snake_case]
- [Source: _bmad-output/planning-artifacts/architecture.md — Pydantic v2 验证模式: model_validate]
- [Source: _bmad-output/planning-artifacts/architecture.md — 模块依赖方向: cli → models/schemas, models/db]
- [Source: _bmad-output/planning-artifacts/architecture.md — Enforcement 强制规则和反模式清单]
- [Source: _bmad-output/planning-artifacts/prd.md — CLI 命令定义: ato batch select / status]
- [Source: _bmad-output/planning-artifacts/prd.md — FR19 审批队列包含 batch 选择]
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md — UX-DR13 空状态引导]
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md — 通知级别: MILESTONE batch 交付]
- [Source: _bmad-output/implementation-artifacts/1-2-sqlite-state-persistence.md — SQLite schema、CRUD 模式、迁移机制]
- [Source: _bmad-output/implementation-artifacts/2a-1-story-state-machine-progression.md — queued / creating 与 `current_phase` 映射]
- [Source: src/ato/models/schemas.py — 当前 StoryStatus Literal 定义、_StrictBase 基类]
- [Source: src/ato/models/db.py — CRUD 函数签名模式、_dt_to_iso/_iso_to_dt 辅助]
- [Source: src/ato/cli.py — typer app 骨架]

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

### File List
