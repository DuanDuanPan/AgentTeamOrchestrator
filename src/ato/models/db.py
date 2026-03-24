"""db — SQLite schema 与辅助函数。

连接管理、DDL 定义、CRUD 辅助函数。所有 SQL 使用参数化查询。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import structlog
from pydantic import TypeAdapter

from ato.models.migrations import run_migrations
from ato.models.schemas import (
    SCHEMA_VERSION,
    ApprovalRecord,
    BatchRecord,
    BatchStatus,
    BatchStoryLink,
    CheckResult,
    StoryRecord,
    StoryStatus,
    TaskRecord,
    TaskStatus,
)

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

# 写前校验用 TypeAdapter — 避免脏数据写入 SQLite
_story_status_validator: TypeAdapter[StoryStatus] = TypeAdapter(StoryStatus)
_task_status_validator: TypeAdapter[TaskStatus] = TypeAdapter(TaskStatus)
_batch_status_validator: TypeAdapter[BatchStatus] = TypeAdapter(BatchStatus)

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

_STORIES_DDL = """\
CREATE TABLE IF NOT EXISTS stories (
    story_id      TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    status        TEXT NOT NULL,
    current_phase TEXT NOT NULL,
    worktree_path TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
)"""

_TASKS_DDL = """\
CREATE TABLE IF NOT EXISTS tasks (
    task_id          TEXT PRIMARY KEY,
    story_id         TEXT NOT NULL REFERENCES stories(story_id),
    phase            TEXT NOT NULL,
    role             TEXT NOT NULL,
    cli_tool         TEXT NOT NULL,
    status           TEXT NOT NULL,
    pid              INTEGER,
    expected_artifact TEXT,
    context_briefing TEXT,
    started_at       TEXT,
    completed_at     TEXT,
    exit_code        INTEGER,
    cost_usd         REAL,
    duration_ms      INTEGER,
    error_message    TEXT
)"""

_APPROVALS_DDL = """\
CREATE TABLE IF NOT EXISTS approvals (
    approval_id   TEXT PRIMARY KEY,
    story_id      TEXT NOT NULL REFERENCES stories(story_id),
    approval_type TEXT NOT NULL,
    status        TEXT NOT NULL,
    payload       TEXT,
    decision      TEXT,
    decided_at    TEXT,
    created_at    TEXT NOT NULL
)"""

_BATCHES_DDL = """\
CREATE TABLE IF NOT EXISTS batches (
    batch_id     TEXT PRIMARY KEY,
    status       TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    completed_at TEXT
)"""

_BATCH_STORIES_DDL = """\
CREATE TABLE IF NOT EXISTS batch_stories (
    batch_id    TEXT NOT NULL REFERENCES batches(batch_id),
    story_id    TEXT NOT NULL REFERENCES stories(story_id),
    sequence_no INTEGER NOT NULL,
    PRIMARY KEY (batch_id, story_id),
    UNIQUE(batch_id, sequence_no)
)"""

# 同一时间仅允许 1 个 active batch — partial unique index
_BATCH_ACTIVE_UNIQUE_IDX = """\
CREATE UNIQUE INDEX IF NOT EXISTS idx_batches_single_active
ON batches(status) WHERE status = 'active'
"""


# ---------------------------------------------------------------------------
# 连接管理
# ---------------------------------------------------------------------------


async def _apply_pragmas(db: aiosqlite.Connection) -> None:
    """对连接应用标准 PRAGMA 设置。"""
    await db.execute("PRAGMA busy_timeout = 5000")
    await db.execute("PRAGMA synchronous = NORMAL")
    await db.execute("PRAGMA foreign_keys = ON")


async def init_db(db_path: Path) -> None:
    """初始化 SQLite 数据库（WAL 模式）。

    若 ``db_path.parent`` 不存在则自动创建。创建所有核心表并设置 ``user_version``。
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)

    async with aiosqlite.connect(db_path) as db:
        # WAL 必须在事务外设置
        await db.execute("PRAGMA journal_mode = WAL")
        await _apply_pragmas(db)

        # 检查当前版本并执行迁移
        cursor = await db.execute("PRAGMA user_version")
        row = await cursor.fetchone()
        current_version = int(row[0]) if row else 0

        if current_version < SCHEMA_VERSION:
            await run_migrations(db, current_version, SCHEMA_VERSION)

        logger.info("database_initialized", path=str(db_path), schema_version=SCHEMA_VERSION)


async def get_connection(db_path: Path) -> aiosqlite.Connection:
    """打开连接并应用标准 PRAGMA 设置。

    调用方负责关闭返回的连接（推荐 ``async with`` 或显式 ``await db.close()``）。
    """
    db = await aiosqlite.connect(db_path)
    try:
        db.row_factory = aiosqlite.Row

        await _apply_pragmas(db)

        # 确认 WAL 模式仍然生效（WAL 是数据库级持久设置，非连接级）
        # 不用 assert — assert 在 python -O 下被移除
        cursor = await db.execute("PRAGMA journal_mode")
        row = await cursor.fetchone()
        if row is None or str(row[0]).lower() != "wal":
            msg = f"Expected journal_mode=wal, got {row}"
            raise RuntimeError(msg)
    except BaseException:
        await db.close()
        raise

    return db


# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------


def _dt_to_iso(dt: datetime | None) -> str | None:
    """datetime → ISO 8601 字符串（用于 SQLite TEXT 列）。"""
    if dt is None:
        return None
    return dt.isoformat()


def _iso_to_dt(value: str | None) -> datetime | None:
    """ISO 8601 字符串 → datetime（用于 Pydantic model_validate 前的反序列化）。"""
    if value is None:
        return None
    return datetime.fromisoformat(value)


# ---------------------------------------------------------------------------
# CRUD — Stories
# ---------------------------------------------------------------------------


async def insert_story(db: aiosqlite.Connection, story: StoryRecord) -> None:
    """插入一条 story 记录。"""
    await db.execute(
        "INSERT INTO stories (story_id, title, status, current_phase, worktree_path, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            story.story_id,
            story.title,
            story.status,
            story.current_phase,
            story.worktree_path,
            _dt_to_iso(story.created_at),
            _dt_to_iso(story.updated_at),
        ),
    )
    await db.commit()


async def get_story(db: aiosqlite.Connection, story_id: str) -> StoryRecord | None:
    """按 story_id 查询 story，不存在返回 None。"""
    cursor = await db.execute("SELECT * FROM stories WHERE story_id = ?", (story_id,))
    row_data = await cursor.fetchone()
    if row_data is None:
        return None
    return _row_to_story(row_data)


async def update_story_status(
    db: aiosqlite.Connection,
    story_id: str,
    status: str,
    phase: str,
    *,
    commit: bool = True,
) -> None:
    """更新 story 的 status、current_phase 和 updated_at。

    Args:
        db: 活跃的 aiosqlite 连接。
        story_id: Story 唯一标识。
        status: 高层状态（必须是合法 StoryStatus 值）。
        phase: 详细阶段名（current_phase 列）。
        commit: 是否自动 commit。``False`` 时由调用方负责 commit，
            用于 TransitionQueue 统一事务边界。

    Raises:
        pydantic.ValidationError: status 值不在 StoryStatus Literal 范围内。
    """
    _story_status_validator.validate_python(status, strict=True)
    if not isinstance(phase, str):
        msg = f"phase must be str, got {type(phase).__name__}"
        raise TypeError(msg)
    now_iso = _dt_to_iso(datetime.now(tz=UTC))
    cursor = await db.execute(
        "UPDATE stories SET status = ?, current_phase = ?, updated_at = ? WHERE story_id = ?",
        (status, phase, now_iso, story_id),
    )
    if cursor.rowcount == 0:
        msg = f"Story '{story_id}' not found in database"
        raise ValueError(msg)
    if commit:
        await db.commit()


def _row_to_story(row: aiosqlite.Row) -> StoryRecord:
    """SQLite Row → StoryRecord（先反序列化 datetime 再 model_validate）。"""
    data = dict(row)
    data["created_at"] = _iso_to_dt(data["created_at"])
    data["updated_at"] = _iso_to_dt(data["updated_at"])
    return StoryRecord.model_validate(data)


# ---------------------------------------------------------------------------
# CRUD — Tasks
# ---------------------------------------------------------------------------

_TASK_COLUMNS = (
    "task_id, story_id, phase, role, cli_tool, status, pid, expected_artifact, "
    "context_briefing, started_at, completed_at, exit_code, cost_usd, duration_ms, error_message"
)


async def insert_task(db: aiosqlite.Connection, task: TaskRecord) -> None:
    """插入一条 task 记录。"""
    await db.execute(
        f"INSERT INTO tasks ({_TASK_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            task.task_id,
            task.story_id,
            task.phase,
            task.role,
            task.cli_tool,
            task.status,
            task.pid,
            task.expected_artifact,
            task.context_briefing,
            _dt_to_iso(task.started_at),
            _dt_to_iso(task.completed_at),
            task.exit_code,
            task.cost_usd,
            task.duration_ms,
            task.error_message,
        ),
    )
    await db.commit()


async def get_tasks_by_story(
    db: aiosqlite.Connection,
    story_id: str,
) -> list[TaskRecord]:
    """查询某个 story 下的所有 tasks。"""
    cursor = await db.execute(
        "SELECT * FROM tasks WHERE story_id = ? ORDER BY rowid",
        (story_id,),
    )
    rows = await cursor.fetchall()
    return [_row_to_task(r) for r in rows]


async def update_task_status(
    db: aiosqlite.Connection,
    task_id: str,
    status: str,
    **kwargs: object,
) -> None:
    """更新 task 状态，支持可选字段（pid、exit_code、cost_usd 等）。

    Raises:
        pydantic.ValidationError: status 值不在 TaskStatus Literal 范围内。
        ValueError: kwargs 包含不允许的字段。
        TypeError: datetime 字段类型不正确。
    """
    _task_status_validator.validate_python(status, strict=True)

    set_clauses = ["status = ?"]
    params: list[object] = [status]

    # 每个可更新字段对应的允许 Python 类型（None 始终允许）
    _field_types: dict[str, type | tuple[type, ...]] = {
        "pid": int,
        "exit_code": int,
        "cost_usd": (int, float),
        "duration_ms": int,
        "expected_artifact": str,
        "error_message": str,
        "context_briefing": str,
        "started_at": datetime,
        "completed_at": datetime,
    }

    for key, value in kwargs.items():
        if key not in _field_types:
            msg = f"update_task_status does not support field: {key}"
            raise ValueError(msg)
        # None 始终允许（所有 kwargs 字段在 TaskRecord 中都是 Optional）
        if value is not None:
            # bool 是 int 的子类，但 Pydantic strict mode 不接受 bool 作为 int/float
            if isinstance(value, bool):
                msg = f"{key} must not be bool"
                raise TypeError(msg)
            expected = _field_types[key]
            if not isinstance(value, expected):
                type_names = (
                    expected.__name__
                    if isinstance(expected, type)
                    else "/".join(t.__name__ for t in expected)
                )
                msg = f"{key} must be {type_names} or None, got {type(value).__name__}"
                raise TypeError(msg)
        # datetime 字段序列化为 ISO 字符串
        if key in ("started_at", "completed_at") and isinstance(value, datetime):
            value = _dt_to_iso(value)
        set_clauses.append(f"{key} = ?")
        params.append(value)

    params.append(task_id)
    sql = f"UPDATE tasks SET {', '.join(set_clauses)} WHERE task_id = ?"
    await db.execute(sql, params)
    await db.commit()


def _row_to_task(row: aiosqlite.Row) -> TaskRecord:
    """SQLite Row → TaskRecord（先反序列化 datetime 再 model_validate）。"""
    data = dict(row)
    for dt_field in ("started_at", "completed_at"):
        data[dt_field] = _iso_to_dt(data[dt_field])
    return TaskRecord.model_validate(data)


# ---------------------------------------------------------------------------
# CRUD — Approvals
# ---------------------------------------------------------------------------


async def insert_approval(db: aiosqlite.Connection, approval: ApprovalRecord) -> None:
    """插入一条 approval 记录。"""
    await db.execute(
        "INSERT INTO approvals (approval_id, story_id, approval_type, status, "
        "payload, decision, decided_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            approval.approval_id,
            approval.story_id,
            approval.approval_type,
            approval.status,
            approval.payload,
            approval.decision,
            _dt_to_iso(approval.decided_at),
            _dt_to_iso(approval.created_at),
        ),
    )
    await db.commit()


async def get_pending_approvals(db: aiosqlite.Connection) -> list[ApprovalRecord]:
    """查询所有 pending 状态的 approvals。"""
    cursor = await db.execute(
        "SELECT * FROM approvals WHERE status = ? ORDER BY rowid",
        ("pending",),
    )
    rows = await cursor.fetchall()
    return [_row_to_approval(r) for r in rows]


def _row_to_approval(row: aiosqlite.Row) -> ApprovalRecord:
    """SQLite Row → ApprovalRecord（先反序列化 datetime 再 model_validate）。"""
    data = dict(row)
    for dt_field in ("decided_at", "created_at"):
        data[dt_field] = _iso_to_dt(data[dt_field])
    return ApprovalRecord.model_validate(data)


# ---------------------------------------------------------------------------
# CRUD — Batches (Story 2B.5)
# ---------------------------------------------------------------------------


async def insert_batch(db: aiosqlite.Connection, batch: BatchRecord) -> None:
    """插入一条 batch 记录。"""
    await db.execute(
        "INSERT INTO batches (batch_id, status, created_at, completed_at) VALUES (?, ?, ?, ?)",
        (
            batch.batch_id,
            batch.status,
            _dt_to_iso(batch.created_at),
            _dt_to_iso(batch.completed_at),
        ),
    )
    await db.commit()


async def insert_batch_story_links(db: aiosqlite.Connection, links: list[BatchStoryLink]) -> None:
    """批量插入 batch_stories 关联记录。"""
    await db.executemany(
        "INSERT INTO batch_stories (batch_id, story_id, sequence_no) VALUES (?, ?, ?)",
        [(link.batch_id, link.story_id, link.sequence_no) for link in links],
    )
    await db.commit()


async def get_active_batch(db: aiosqlite.Connection) -> BatchRecord | None:
    """获取当前唯一的 active batch，不存在返回 None。"""
    cursor = await db.execute("SELECT * FROM batches WHERE status = ?", ("active",))
    row = await cursor.fetchone()
    if row is None:
        return None
    return _row_to_batch(row)


async def get_batch_stories(
    db: aiosqlite.Connection, batch_id: str
) -> list[tuple[BatchStoryLink, StoryRecord]]:
    """按 sequence_no 顺序获取 batch 中所有 story（含关联的 StoryRecord）。"""
    cursor = await db.execute(
        "SELECT bs.batch_id, bs.story_id, bs.sequence_no, "
        "s.story_id AS s_story_id, s.title, s.status, s.current_phase, "
        "s.worktree_path, s.created_at, s.updated_at "
        "FROM batch_stories bs "
        "JOIN stories s ON bs.story_id = s.story_id "
        "WHERE bs.batch_id = ? ORDER BY bs.sequence_no",
        (batch_id,),
    )
    rows = await cursor.fetchall()
    results: list[tuple[BatchStoryLink, StoryRecord]] = []
    for row in rows:
        data = dict(row)
        link = BatchStoryLink.model_validate(
            {
                "batch_id": data["batch_id"],
                "story_id": data["story_id"],
                "sequence_no": data["sequence_no"],
            }
        )
        story = StoryRecord.model_validate(
            {
                "story_id": data["s_story_id"],
                "title": data["title"],
                "status": data["status"],
                "current_phase": data["current_phase"],
                "worktree_path": data["worktree_path"],
                "created_at": _iso_to_dt(data["created_at"]),
                "updated_at": _iso_to_dt(data["updated_at"]),
            }
        )
        results.append((link, story))
    return results


class BatchProgress:
    """Batch 进度汇总（非 Pydantic 模型，仅用于返回聚合结果）。"""

    __slots__ = ("active", "done", "failed", "pending", "total")

    def __init__(
        self,
        *,
        done: int = 0,
        active: int = 0,
        pending: int = 0,
        failed: int = 0,
    ) -> None:
        self.done = done
        self.active = active
        self.pending = pending
        self.failed = failed
        self.total = done + active + pending + failed


async def get_batch_progress(db: aiosqlite.Connection, batch_id: str) -> BatchProgress:
    """按 AC2 规则聚合 batch 内各 story 的进度分类。

    分类规则：
      - done = status == "done"
      - failed = status == "blocked"
      - pending = current_phase == "queued" 或 status in {"backlog", "ready"}
      - active = 其余状态（planning, in_progress, review, uat）
    """
    cursor = await db.execute(
        "SELECT s.status, s.current_phase "
        "FROM batch_stories bs "
        "JOIN stories s ON bs.story_id = s.story_id "
        "WHERE bs.batch_id = ?",
        (batch_id,),
    )
    rows = await cursor.fetchall()
    done = active = pending = failed = 0
    for row in rows:
        status = row[0]
        phase = row[1]
        if status == "done":
            done += 1
        elif status == "blocked":
            failed += 1
        elif phase == "queued" or status in ("backlog", "ready"):
            pending += 1
        else:
            active += 1
    return BatchProgress(done=done, active=active, pending=pending, failed=failed)


def _row_to_batch(row: aiosqlite.Row) -> BatchRecord:
    """SQLite Row → BatchRecord。"""
    data = dict(row)
    data["created_at"] = _iso_to_dt(data["created_at"])
    data["completed_at"] = _iso_to_dt(data["completed_at"])
    return BatchRecord.model_validate(data)


# ---------------------------------------------------------------------------
# CRUD — Preflight Results (Story 1.4a)
# ---------------------------------------------------------------------------


async def insert_preflight_results(
    db: aiosqlite.Connection,
    run_id: str,
    results: list[CheckResult],
) -> None:
    """批量插入 preflight 检查结果。"""
    if not results:
        return
    await db.executemany(
        "INSERT INTO preflight_results (run_id, layer, check_item, status, message) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            (run_id, r.layer, r.check_item, r.status, r.message)
            for r in results
        ],
    )
    await db.commit()
