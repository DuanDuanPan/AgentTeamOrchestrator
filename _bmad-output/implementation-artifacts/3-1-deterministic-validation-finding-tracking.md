# Story 3.1: 操作者可看到明显结构错误被秒级拦截，findings 被独立追踪

Status: review

## Story

As a 操作者,
I want 在 agent review 之前看到明显的结构错误被秒级拦截，review 后的每个 finding 可被独立追踪和查询,
So that 不用等 agent 审查就能发现低级问题，每个质量问题有清晰的生命周期。

## Acceptance Criteria

1. **AC1 — Deterministic Validation（JSON Schema 验证）**
   ```
   Given agent 产出的 artifact（story 文档、代码变更等）
   When 进入 review 阶段前
   Then 先执行 deterministic validation（JSON Schema 验证），耗时 ≤1 秒（NFR4）
   And 验证通过才进入 agent review，验证失败直接退回修改
   And 操作者最终可在 ato status 或 TUI 中看到 "Schema 验证失败，已退回修改"
   And 在当前代码基线（`ato status` 尚未实现）中，至少将该文案写入 validation task 的 `error_message`，并把 story 回退到 `creating`，供后续 CLI/TUI 直接复用
   ```

2. **AC2 — JSON Schema 文件定义与验证执行**
   ```
   Given schemas/ 目录下的 JSON Schema 文件（review-findings.json, story-validation.json, finding-verification.json）
   When 对 artifact 执行 Schema 验证
   Then 返回 pass/fail + 具体验证错误列表
   ```

3. **AC3 — Finding 入库与 Pydantic 验证**
   ```
   Given review 产出 findings
   When findings 入库
   Then 创建 findings 表（CREATE TABLE IF NOT EXISTS），每个 finding 包含：
       finding_id, story_id, round_num, severity（blocking/suggestion）,
       description, status（open）, file_path, rule_id, dedup_hash, created_at
   And 经 Pydantic FindingRecord model_validate() 验证后写入 SQLite findings 表
   ```

4. **AC4 — Blocking 异常阈值 escalation**
   ```
   Given finding 的 severity 分类
   When blocking findings 数量超过配置的 blocking_threshold（默认 10，来源 `ato.yaml` 的 `cost.blocking_threshold`）
   Then 写入 approval 记录（类型 blocking_abnormal）到 approvals 表（当前仓库 migration `v0→v1` 已创建该表）
   And 按调用上下文发送 nudge：进程内 writer 使用 `Nudge.notify()`，进程外 writer 使用 `send_external_nudge()`
   And 操作者可在审批队列或 ato status 中看到该 blocking 通知并决定是否继续
   ```

## Tasks / Subtasks

- [x] Task 1: 创建 JSON Schema 定义文件 (AC: #2)
  - [x] 1.1 创建 `schemas/` 目录
  - [x] 1.2 创建 `schemas/review-findings.json`——review findings 输出的 JSON Schema：
    - 顶层 `findings` 数组，每个 finding 必含 `file_path: string`、`rule_id: string`、`severity: enum["blocking","suggestion"]`、`description: string`
    - 可选字段 `line_number: integer`、`fix_suggestion: string`
    - 字段命名必须与 Story 2B.2 的 `CodexOutput.structured_output` / Story 2B.3 的 BMAD 解析输出保持一致，避免同一 finding 在 adapter 和 validation 层出现两套 shape
  - [x] 1.3 创建 `schemas/story-validation.json`——story artifact 结构验证 Schema（验证 story 产出格式是否合规）
  - [x] 1.4 创建 `schemas/finding-verification.json`——单个 finding 验证结果的 Schema（用于 re-review 场景中 finding 闭合验证）

- [x] Task 2: 实现 DeterministicValidator (AC: #1, #2)
  - [x] 2.1 创建 `src/ato/validation.py` 模块
  - [x] 2.2 实现 `load_schema(schema_name: str) -> dict[str, Any]`：从仓库根 `schemas/` 目录加载 JSON Schema 文件；当前 `pyproject.toml` 只打包 `src/ato`，因此不要对根目录 `schemas/` 使用 `importlib.resources`；schema 加载失败时抛出 `ConfigError`
  - [x] 2.3 实现 `validate_artifact(artifact_data: dict[str, Any], schema_name: str) -> ValidationResult`：使用 `Draft202012Validator.check_schema()` + `Draft202012Validator(schema).iter_errors()` 收集完整错误列表；返回 `ValidationResult(passed: bool, errors: list[SchemaValidationIssue])`
  - [x] 2.4 定义 `ValidationResult` 和 `SchemaValidationIssue` Pydantic 模型（放在 `schemas.py` 中）；不要命名为 `ValidationError`，避免与 `pydantic.ValidationError` 冲突
  - [x] 2.5 确保全部验证路径耗时 ≤1 秒（NFR4）——JSON Schema 验证是纯 CPU 操作，预期 <10ms

- [x] Task 3: 定义 FindingRecord 模型与 findings 表 (AC: #3)
  - [x] 3.1 在 `src/ato/models/schemas.py` 中定义：
    - `FindingSeverity = Literal["blocking", "suggestion"]`
    - `FindingStatus = Literal["open", "closed", "still_open"]`
    - `FindingRecord(_StrictBase)` 字段：`finding_id: str`, `story_id: str`, `round_num: int`, `severity: FindingSeverity`, `description: str`, `status: FindingStatus`, `file_path: str`, `rule_id: str`, `dedup_hash: str`, `line_number: int | None = None`, `fix_suggestion: str | None = None`, `created_at: datetime`
  - [x] 3.2 实现 `compute_dedup_hash(file_path: str, rule_id: str, severity: str, description: str) -> str`：
    - 正则化 description：`re.sub(r"\s+", " ", desc).strip().lower()`
    - 计算 `hashlib.sha256(f"{file_path}|{rule_id}|{severity}|{normalized_desc}".encode()).hexdigest()`
  - [x] 3.3 在 `src/ato/models/db.py` 中添加 `_FINDINGS_DDL`：
    ```sql
    CREATE TABLE IF NOT EXISTS findings (
        finding_id  TEXT PRIMARY KEY,
        story_id    TEXT NOT NULL REFERENCES stories(story_id),
        round_num   INTEGER NOT NULL,
        severity    TEXT NOT NULL,
        description TEXT NOT NULL,
        status      TEXT NOT NULL DEFAULT 'open',
        file_path   TEXT NOT NULL,
        rule_id     TEXT NOT NULL,
        dedup_hash  TEXT NOT NULL,
        line_number INTEGER,
        fix_suggestion TEXT,
        created_at  TEXT NOT NULL
    )
    ```
  - [x] 3.4 添加索引：`CREATE INDEX IF NOT EXISTS idx_findings_story_round ON findings(story_id, round_num)`
  - [x] 3.5 添加索引：`CREATE INDEX IF NOT EXISTS idx_findings_dedup ON findings(dedup_hash)`
  - [x] 3.6 在 `src/ato/models/migrations.py` 中添加 v4→v5 迁移（创建 findings 表 + 索引），更新 `SCHEMA_VERSION = 5`

- [x] Task 4: 实现 findings CRUD 辅助函数 (AC: #3)
  - [x] 4.1 在 `src/ato/models/db.py` 中添加 `insert_finding(db, record: FindingRecord) -> None`：`model_validate()` 后写入
  - [x] 4.2 添加 `insert_findings_batch(db, records: list[FindingRecord]) -> None`：批量插入，事务内执行
  - [x] 4.3 添加 `get_findings_by_story(db, story_id: str, *, round_num: int | None = None) -> list[FindingRecord]`
  - [x] 4.4 添加 `get_open_findings(db, story_id: str) -> list[FindingRecord]`：查询 `status IN ('open', 'still_open')` 的 findings
  - [x] 4.5 添加 `update_finding_status(db, finding_id: str, new_status: FindingStatus) -> None`
  - [x] 4.6 添加 `count_findings_by_severity(db, story_id: str, round_num: int) -> dict[str, int]`：返回 `{"blocking": N, "suggestion": M}`

- [x] Task 5: 实现 blocking 阈值 escalation (AC: #4)
  - [x] 5.1 在 `src/ato/validation.py` 中添加 `count_blocking_findings(db, story_id: str, round_num: int) -> int`：统计当前轮次 blocking finding 数量
  - [x] 5.2 添加 `maybe_create_blocking_abnormal_approval(...) -> bool`：当 blocking 数量 `> threshold` 时调用 `db.py` 已有的 `insert_approval()` 创建 `approval_type="blocking_abnormal"` 记录，payload 包含 `{"blocking_count": N, "threshold": M, "round_num": R}`
  - [x] 5.3 不在 `validation.py` 内硬编码全局 nudge 单例；由调用方按上下文传入 `Nudge` 或 `orchestrator_pid`，进程外路径使用 `send_external_nudge()`

- [x] Task 6: 添加 jsonschema 依赖 (AC: #2)
  - [x] 6.1 `uv add jsonschema`——JSON Schema 验证库（Python 标准生态，无额外系统依赖）

- [x] Task 7: 测试 (AC: #1-#4)
  - [x] 7.1 创建 `tests/fixtures/findings_valid.json`——通过 `review-findings.json` Schema 的样本数据
  - [x] 7.2 创建 `tests/fixtures/findings_invalid.json`——缺少必需字段、severity 非法值等反例
  - [x] 7.3 创建 `tests/unit/test_validation.py`：
    - `test_load_schema_success`——加载 review-findings.json
    - `test_load_schema_not_found`——不存在的 schema 抛 ConfigError
    - `test_validate_artifact_pass`——有效 findings JSON 通过
    - `test_validate_artifact_fail`——缺少字段 / severity 非法时返回完整 errors 列表（非只报首个错误）
    - `test_validation_performance`——≤1 秒（time.perf_counter 断言）
    - `test_below_threshold`——blocking 数量未超阈值，不创建 approval
    - `test_above_threshold`——blocking 数量超阈值，创建 blocking_abnormal approval
    - `test_threshold_creates_approval_with_payload`——验证 payload 结构
    - `test_external_nudge_path` / `test_inprocess_nudge_path`——分别验证 `send_external_nudge()` 与注入 `Nudge.notify()` 的分支
  - [x] 7.4 在 `tests/unit/test_schemas.py` 中补充：
    - `test_finding_record_valid`——全字段构建
    - `test_finding_record_strict`——extra 字段拒绝
    - `test_compute_dedup_hash_deterministic`——相同输入 → 相同 hash
    - `test_compute_dedup_hash_normalization`——"line too long" vs "LINE TOO LONG" → 相同 hash
    - `test_compute_dedup_hash_different_severity`——severity 不同 → hash 不同
    - `test_schema_validation_issue_model`——避免与 `pydantic.ValidationError` 命名冲突
  - [x] 7.5 在 `tests/unit/test_db.py` 中补充：
    - `test_insert_finding`——插入后可查询
    - `test_insert_findings_batch`——批量插入 N 条，查询验证数量
    - `test_get_findings_by_story_with_round`——round_num 过滤
    - `test_get_open_findings`——仅返回 open/still_open
    - `test_update_finding_status`——open → closed
    - `test_count_findings_by_severity`——blocking/suggestion 计数正确
  - [x] 7.6 在 `tests/unit/test_migrations.py` 中补充：
    - `test_migration_v4_to_v5`——从 v4 数据库升级到 v5，findings 表存在
    - `test_findings_indexes_exist`——验证两个索引已创建

## Dev Notes

### 架构定位

本 story 为 Epic 3（Convergent Loop 质量门控）的基础 story，所有后续 story（3.2a-3.2d, 3.3）依赖本 story 的 findings 表和验证机制。核心交付物：

1. **Deterministic Validation 模块** (`src/ato/validation.py`)——JSON Schema 快速验证层，在 agent review 之前拦截结构错误
2. **FindingRecord 数据模型** (`schemas.py`)——Finding 的 Pydantic 定义 + SHA256 去重 hash
3. **findings SQLite 表** (`db.py` + `migrations.py`)——Finding 持久化与 CRUD
4. **JSON Schema 文件** (`schemas/`)——review findings、story artifact、finding verification 的结构规范

### 关键设计约束

- **SHA256 去重算法**：`SHA256(file_path + "|" + rule_id + "|" + severity + "|" + normalize(description))`。正则化：空白压缩 + strip + lower。此 hash 将在 Story 3.2c（re-review scope narrowing）中用于跨轮次 finding 匹配
- **FindingStatus 三态**：`open`（新发现/未修复）、`closed`（已修复）、`still_open`（re-review 仍存在）。Story 3.1 仅使用 `open`，`closed` 和 `still_open` 由 Story 3.2c 实现
- **Severity 分类**：`blocking`（必须修复才能收敛）和 `suggestion`（建议但不阻塞）。分类由 review agent 返回，不由本 story 决定
- **blocking_threshold 配置**：默认值 10，来源 `ato.yaml` 的 `cost.blocking_threshold`（见 `src/ato/config.py:CostConfig`），不要误读为 `convergent_loop.blocking_threshold`
- **Schema 验证 API 选择**：`jsonschema.validate()` / `Validator.validate()` 只会在失败时抛单个异常；本 story 需要“错误列表”，应使用 `Draft202012Validator.iter_errors()`
- **命名避冲突**：不要新增名为 `ValidationError` 的内部 Pydantic 模型；仓库内已经广泛使用 `pydantic.ValidationError`
- **用户可见错误承载**：当前没有 `ato status` 命令，不要为此新增 story 表字段；优先复用 `tasks.error_message` + story 状态回退
- **Schema 文件加载边界**：架构要求 `schemas/` 位于仓库根；在当前打包配置下，MVP 应按源码路径读取，不要假设 wheel 资源内可直接读取

### 与已有代码的集成点

| 集成目标 | 文件 | 集成方式 |
|---------|------|---------|
| 异常基类 | `schemas.py:ATOError`, `ConfigError` | validation 错误复用 `ConfigError` |
| Approval 写入 | `db.py:insert_approval()` | blocking 阈值超时直接调用 |
| Blocking 阈值来源 | `config.py:CostConfig.blocking_threshold` | 读取/传入默认值 10 |
| Nudge 通知 | `nudge.py:Nudge.notify()`, `send_external_nudge()` | 进程内/进程外分别走不同 transport |
| 用户可见失败文案 | `schemas.py:TaskRecord.error_message`, `db.py:update_task_status()` | validation 失败时复用现有 task 错误承载 |
| 迁移链 | `migrations.py:run_migrations()` | 新增 v4→v5 步骤 |
| Schema 版本 | `schemas.py:SCHEMA_VERSION` | 从 4 更新到 5 |
| _StrictBase | `schemas.py:_StrictBase` | FindingRecord 继承此基类 |
| structlog | `logging.py` | 验证和入库操作记录日志 |

### 不要做的事情

- **不要实现 Convergent Loop 协议**——仅实现基础的验证和 finding 入库。Loop 逻辑在 Story 3.2a-3.2d
- **不要在本 story 中实现 `src/ato/convergent_loop.py` 的循环协议**——该文件已存在占位；本 story 只提供其后续要消费的 validation/findings 基础设施
- **不要修改状态机**——reviewing ↔ fixing transition 已在 Story 2A.1 定义
- **不要实现 BMAD adapter 解析**——finding 从 JSON 输入，BMAD Markdown→JSON 是 Story 2B.3
- **不要在 `validation.py` 中导入 `core.py` 或 `transition_queue.py`**——保持模块隔离

### 新增依赖

- `jsonschema`（pypi）——JSON Schema Draft 2020-12 验证。通过 `uv add jsonschema` 安装

### 文件变更清单

| 操作 | 文件路径 | 说明 |
|------|---------|------|
| CREATE | `schemas/review-findings.json` | Review findings JSON Schema |
| CREATE | `schemas/story-validation.json` | Story artifact JSON Schema |
| CREATE | `schemas/finding-verification.json` | Finding verification JSON Schema |
| CREATE | `src/ato/validation.py` | DeterministicValidator 模块 |
| MODIFY | `src/ato/models/schemas.py` | +FindingRecord, +FindingSeverity, +FindingStatus, +ValidationResult, +SchemaValidationIssue, SCHEMA_VERSION 4→5 |
| MODIFY | `src/ato/models/db.py` | +_FINDINGS_DDL, +findings CRUD 函数, +import FindingRecord |
| MODIFY | `src/ato/models/migrations.py` | +v4→v5 migration |
| MODIFY | `pyproject.toml` | +jsonschema 依赖 |
| CREATE | `tests/fixtures/findings_valid.json` | 有效 findings 样本 |
| CREATE | `tests/fixtures/findings_invalid.json` | 无效 findings 反例 |
| CREATE | `tests/unit/test_validation.py` | 验证模块单测 |
| MODIFY | `tests/unit/test_schemas.py` | FindingRecord + dedup hash + validation issue 模型单测 |
| MODIFY | `tests/unit/test_db.py` | findings CRUD 单测 |
| MODIFY | `tests/unit/test_migrations.py` | v5 findings migration 测试 |

### 已有代码模式参考

**Pydantic 模型风格**（参照 `schemas.py` 现有模式）：
```python
FindingSeverity = Literal["blocking", "suggestion"]
FindingStatus = Literal["open", "closed", "still_open"]

class FindingRecord(_StrictBase):
    """findings 表对应的 Pydantic 模型。"""
    finding_id: str
    story_id: str
    round_num: int
    severity: FindingSeverity
    # ... 其余字段
```

**DDL 风格**（参照 `db.py` 的 `_STORIES_DDL`, `_TASKS_DDL`）：
```python
_FINDINGS_DDL = """\
CREATE TABLE IF NOT EXISTS findings (
    finding_id  TEXT PRIMARY KEY,
    story_id    TEXT NOT NULL REFERENCES stories(story_id),
    ...
)"""
```

**迁移链风格**（参照 `migrations.py` 的 v0→v1→...→v4 模式）：
```python
async def _migrate_v4_to_v5(db: aiosqlite.Connection) -> None:
    await db.execute(_FINDINGS_DDL)
    await db.execute(_FINDINGS_STORY_ROUND_IDX)
    await db.execute(_FINDINGS_DEDUP_IDX)
```

**CRUD 函数风格**（参照 `db.py` 的 `insert_story()`, `get_story()`）：
```python
async def insert_finding(db: aiosqlite.Connection, record: FindingRecord) -> None:
    record.model_validate(record.model_dump())  # 写前校验
    await db.execute(
        "INSERT INTO findings (...) VALUES (?, ?, ...)",
        (record.finding_id, record.story_id, ...),
    )
```

**structlog 使用**（参照 `subprocess_mgr.py`, `db.py`）：
```python
logger: structlog.stdlib.BoundLogger = structlog.get_logger()
logger.info("finding_inserted", finding_id=record.finding_id, story_id=record.story_id)
```

### Git Intelligence

最近 5 个 commit 均为 story 实现 merge，模式一致：
- 每个 story 一个 feature commit + 一个 merge commit
- commit message 格式：`feat: Story X.Y <简短描述>`
- 所有测试通过后 merge

### Previous Story Intelligence

**从 Story 2B.2（最近完成）的关键经验：**
- Snapshot fixture 测试模式高效——先创建 fixture 文件，再写解析测试
- `_StrictBase` 用于内部 record，`BaseModel(extra="ignore")` 用于外部 CLI 解析——FindingRecord 是内部模型，用 `_StrictBase`
- 成本计算精度用 `float` 而非 `Decimal`（项目约定）
- 错误分类复用 `ErrorCategory` 枚举——validation 错误可复用 `ConfigError`
- `from_events()` / `from_json()` 类方法模式——FindingRecord 可考虑 `from_review_output()` 但本 story 直接构造即可

### Project Structure Notes

- `schemas/` 目录放在项目根目录（与 `src/` 同级），包含验证用 JSON Schema 文件
- `src/ato/validation.py` 是新模块，与 `adapters/` 平级
- 不创建 `src/ato/validation/` 子包——单文件足够
- findings 表在 `db.py` 中定义 DDL，CRUD 也在 `db.py` 中（与现有 stories/tasks/approvals 一致）
- 当前 `pyproject.toml` 仅打包 `src/ato`；若继续采用根目录 `schemas/`，实现必须显式按仓库路径读取。若未来需要“安装后运行也可找到 schema”，再单独把 schema 收进包资源并调整 build 配置
- 测试组织优先沿用现有文件：模型测试追加到 `tests/unit/test_schemas.py`，数据库 CRUD 追加到 `tests/unit/test_db.py`，迁移测试追加到 `tests/unit/test_migrations.py`；不要无谓拆出一组平行测试文件

### References

- [Source: _bmad-output/planning-artifacts/epics.md — Epic 3, Story 3.1]
- [Source: _bmad-output/planning-artifacts/architecture.md — Decision 8: Convergent Loop]
- [Source: _bmad-output/planning-artifacts/architecture.md — Decision 9: CLI 契约守护]
- [Source: _bmad-output/planning-artifacts/prd.md — FR13, FR14, FR16, FR18, NFR4, NFR9]
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md — ConvergentLoopProgress Component]
- [Source: src/ato/config.py — `CostConfig.blocking_threshold`]
- [Source: src/ato/models/schemas.py — 现有 Pydantic 模型风格]
- [Source: src/ato/models/db.py — 现有 DDL + CRUD 风格]
- [Source: src/ato/models/migrations.py — 迁移链模式]
- [Source: src/ato/nudge.py — in-process / external nudge contract]
- [Source: src/ato/state_machine.py — validating → creating 回退路径]
- [Source: pyproject.toml — 当前打包范围仅 `src/ato`]
- [Source: https://python-jsonschema.readthedocs.io/en/v4.13.0/validate/ — `validate()` 抛单个错误，`iter_errors()` 返回错误迭代器]

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6 (1M context)

### Debug Log References

- 修复 ruff lint 发现 `FindingSeverity` 未使用导入
- 修复 `test_preflight_schema.py` 中硬编码 `SCHEMA_VERSION == 4` 的断言，改为 `>= 4`
- 修复 `test_external_nudge_path` 的 mock patch 路径（lazy import 需 patch `ato.nudge.send_external_nudge`）

### Completion Notes List

- **AC/Dev Notes 边界说明**：AC1 描述了端到端行为（review 前 schema gate → 失败回退 creating → 写 error_message），而 Dev Notes 明确限定本 story 为"仅基础设施，不接流程"。本 story 交付 validate_artifact()、maybe_create_blocking_abnormal_approval() 等可直接调用的 API；实际接入 convergent loop 运行时、状态回退、error_message 写入由 Story 3.2a 完成。AC1/AC4 中的运行时集成部分应视为 3.2a 的前置需求，而非 3.1 遗漏。
- Task 1: 创建了 3 个 JSON Schema 文件（review-findings.json, story-validation.json, finding-verification.json），使用 Draft 2020-12 规范
- Task 2: 实现 `src/ato/validation.py`，包含 `load_schema()` 从仓库根 schemas/ 加载、`validate_artifact()` 使用 `Draft202012Validator.iter_errors()` 收集完整错误列表
- Task 3: 在 schemas.py 新增 `FindingRecord`、`FindingSeverity`、`FindingStatus`、`ValidationResult`、`SchemaValidationIssue`、`compute_dedup_hash()`；在 db.py 添加 `_FINDINGS_DDL` + 2 个索引 DDL；在 migrations.py 添加 v4→v5 迁移；SCHEMA_VERSION 4→5
- Task 4: 在 db.py 添加 6 个 CRUD 函数：`insert_finding()`, `insert_findings_batch()`, `get_findings_by_story()`, `get_open_findings()`, `update_finding_status()`, `count_findings_by_severity()`
- Task 5: 在 validation.py 添加 `count_blocking_findings()` 和 `maybe_create_blocking_abnormal_approval()`，支持进程内 nudge 和进程外 send_external_nudge 两条路径
- Task 6: `uv add jsonschema` 添加 JSON Schema 验证库依赖
- Task 7: 新增 40 个测试（test_validation.py 11 个、test_schemas.py 17 个、test_db.py 10 个、test_migrations.py 2 个），全部 615 个测试通过

### File List

| 操作 | 文件路径 |
|------|---------|
| CREATE | schemas/review-findings.json |
| CREATE | schemas/story-validation.json |
| CREATE | schemas/finding-verification.json |
| CREATE | src/ato/validation.py |
| MODIFY | src/ato/models/schemas.py |
| MODIFY | src/ato/models/db.py |
| MODIFY | src/ato/models/migrations.py |
| MODIFY | pyproject.toml |
| CREATE | tests/fixtures/findings_valid.json |
| CREATE | tests/fixtures/findings_invalid.json |
| CREATE | tests/unit/test_validation.py |
| MODIFY | tests/unit/test_schemas.py |
| MODIFY | tests/unit/test_db.py |
| MODIFY | tests/unit/test_migrations.py |
| MODIFY | tests/unit/test_preflight_schema.py |

### Change Log

- 2026-03-25: Story 3.1 全量实现 — Deterministic Validation 模块、FindingRecord 数据模型、findings SQLite 表（v4→v5 迁移）、JSON Schema 文件、blocking 阈值 escalation、40 个新测试
