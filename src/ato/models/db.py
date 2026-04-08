"""db — SQLite schema 与辅助函数。

连接管理、DDL 定义、CRUD 辅助函数。所有 SQL 使用参数化查询。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, get_args

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
    CostLogRecord,
    FindingRecord,
    FindingStatus,
    MergeQueueEntry,
    MergeQueueState,
    StoryRecord,
    StoryStatus,
    TaskRecord,
    TaskStatus,
    WorktreePreflightResult,
)

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

# 写前校验用 TypeAdapter — 避免脏数据写入 SQLite
_story_status_validator: TypeAdapter[StoryStatus] = TypeAdapter(StoryStatus)
_task_status_validator: TypeAdapter[TaskStatus] = TypeAdapter(TaskStatus)
_batch_status_validator: TypeAdapter[BatchStatus] = TypeAdapter(BatchStatus)
_finding_status_validator: TypeAdapter[FindingStatus] = TypeAdapter(FindingStatus)
_VALID_TASK_STATUSES = frozenset(get_args(TaskStatus))
_VALID_FINDING_STATUSES = frozenset(get_args(FindingStatus))

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
    has_ui        BOOLEAN DEFAULT 0,
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
    error_message    TEXT,
    text_result      TEXT,
    last_activity_type    TEXT,
    last_activity_summary TEXT,
    group_id              TEXT
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

_FINDINGS_DDL = """\
CREATE TABLE IF NOT EXISTS findings (
    finding_id  TEXT PRIMARY KEY,
    story_id    TEXT NOT NULL REFERENCES stories(story_id),
    phase       TEXT NOT NULL DEFAULT 'reviewing',
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
)"""

_FINDINGS_STORY_ROUND_IDX = """\
CREATE INDEX IF NOT EXISTS idx_findings_story_round
ON findings(story_id, round_num)"""

_FINDINGS_STORY_PHASE_ROUND_IDX = """\
CREATE INDEX IF NOT EXISTS idx_findings_story_phase_round
ON findings(story_id, phase, round_num)"""

_FINDINGS_DEDUP_IDX = """\
CREATE INDEX IF NOT EXISTS idx_findings_dedup
ON findings(dedup_hash)"""

_MERGE_QUEUE_DDL = """\
CREATE TABLE IF NOT EXISTS merge_queue (
    id          INTEGER PRIMARY KEY,
    story_id    TEXT NOT NULL UNIQUE,
    approval_id TEXT NOT NULL,
    approved_at TEXT NOT NULL,
    enqueued_at TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'waiting',
    regression_task_id TEXT,
    pre_merge_head TEXT
)"""

_MERGE_QUEUE_STATE_DDL = """\
CREATE TABLE IF NOT EXISTS merge_queue_state (
    id                      INTEGER PRIMARY KEY CHECK (id = 1),
    frozen                  INTEGER NOT NULL DEFAULT 0,
    frozen_reason           TEXT,
    frozen_at               TEXT,
    current_merge_story_id  TEXT
)"""

_WORKTREE_PREFLIGHT_RESULTS_DDL = """\
CREATE TABLE IF NOT EXISTS worktree_preflight_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    story_id TEXT NOT NULL,
    gate_type TEXT NOT NULL,
    passed INTEGER NOT NULL,
    base_ref TEXT NOT NULL,
    base_sha TEXT,
    head_sha TEXT,
    porcelain_output TEXT NOT NULL DEFAULT '',
    diffstat TEXT NOT NULL DEFAULT '',
    changed_files TEXT NOT NULL DEFAULT '[]',
    failure_reason TEXT,
    error_output TEXT,
    checked_at TEXT NOT NULL
)"""

_WORKTREE_PREFLIGHT_STORY_IDX = """\
CREATE INDEX IF NOT EXISTS idx_worktree_preflight_story
ON worktree_preflight_results(story_id)"""

_WORKTREE_PREFLIGHT_GATE_IDX = """\
CREATE INDEX IF NOT EXISTS idx_worktree_preflight_gate
ON worktree_preflight_results(story_id, gate_type, checked_at)"""


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
        "has_ui, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            story.story_id,
            story.title,
            story.status,
            story.current_phase,
            story.worktree_path,
            int(story.has_ui),
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


async def update_story_worktree_path(
    db: aiosqlite.Connection,
    story_id: str,
    worktree_path: str | None,
) -> None:
    """更新 story 的 worktree_path 和 updated_at。

    Args:
        db: 活跃的 aiosqlite 连接。
        story_id: Story 唯一标识。
        worktree_path: Worktree 绝对路径，None 表示清空。

    Raises:
        ValueError: story_id 不存在。
    """
    now_iso = _dt_to_iso(datetime.now(tz=UTC))
    cursor = await db.execute(
        "UPDATE stories SET worktree_path = ?, updated_at = ? WHERE story_id = ?",
        (worktree_path, now_iso, story_id),
    )
    if cursor.rowcount == 0:
        msg = f"Story '{story_id}' not found in database"
        raise ValueError(msg)
    await db.commit()


async def rollback_story(
    db: aiosqlite.Connection,
    story_id: str,
    target_phase: str,
    *,
    reason: str,
    commit: bool = True,
) -> dict[str, object]:
    """Safely roll a story back to a target phase and normalize invalid child rows.

    This helper intentionally uses raw SQL updates so it can repair rows that already
    contain invalid enum values from manual DB edits.
    """
    from ato.state_machine import PHASE_TO_STATUS

    target_status = PHASE_TO_STATUS.get(target_phase)
    if target_status is None or target_phase in {"done", "blocked"}:
        msg = f"Unsupported rollback target phase: {target_phase}"
        raise ValueError(msg)

    cursor = await db.execute(
        "SELECT current_phase, status, worktree_path FROM stories WHERE story_id = ?",
        (story_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        msg = f"Story '{story_id}' not found in database"
        raise ValueError(msg)

    previous_phase = str(row["current_phase"])
    previous_status = str(row["status"])
    now = datetime.now(tz=UTC)
    now_iso = _dt_to_iso(now)
    reason_suffix = f"{reason} -> {target_phase}"

    await db.execute("SAVEPOINT story_rollback")
    try:
        await db.execute(
            "UPDATE stories SET status = ?, current_phase = ?, updated_at = ? WHERE story_id = ?",
            (target_status, target_phase, now_iso, story_id),
        )

        task_cursor = await db.execute(
            """
            UPDATE tasks
            SET status = ?,
                pid = NULL,
                completed_at = COALESCE(completed_at, ?),
                error_message = CASE
                    WHEN error_message IS NULL OR error_message = '' THEN ?
                    ELSE error_message || '; ' || ?
                END
            WHERE story_id = ?
              AND status NOT IN (?, ?)
            """,
            (
                "failed",
                now_iso,
                reason_suffix,
                reason_suffix,
                story_id,
                "completed",
                "failed",
            ),
        )

        if target_phase == "fixing":
            finding_cursor = await db.execute(
                """
                UPDATE findings
                SET status = ?
                WHERE story_id = ?
                  AND status NOT IN (?, ?, ?)
                """,
                (
                    "closed",
                    story_id,
                    *_VALID_FINDING_STATUSES,
                ),
            )
        else:
            # Manual rollback should produce a clean re-run surface. Preserving
            # historical findings would keep convergent-loop round counters alive
            # and can immediately force escalated recovery after a rollback.
            finding_cursor = await db.execute(
                "DELETE FROM findings WHERE story_id = ?",
                (story_id,),
            )

        approval_cursor = await db.execute(
            """
            UPDATE approvals
            SET status = ?,
                decision = ?,
                decision_reason = ?,
                decided_at = ?,
                consumed_at = ?
            WHERE story_id = ?
              AND status = ?
            """,
            (
                "rejected",
                "manual_rollback",
                reason_suffix,
                now_iso,
                now_iso,
                story_id,
                "pending",
            ),
        )

        await db.execute("RELEASE SAVEPOINT story_rollback")
    except BaseException:
        await db.execute("ROLLBACK TO SAVEPOINT story_rollback")
        await db.execute("RELEASE SAVEPOINT story_rollback")
        raise

    if commit:
        await db.commit()

    return {
        "story_id": story_id,
        "previous_phase": previous_phase,
        "previous_status": previous_status,
        "target_phase": target_phase,
        "target_status": target_status,
        "normalized_tasks": task_cursor.rowcount,
        "normalized_findings": finding_cursor.rowcount,
        "cleared_pending_approvals": approval_cursor.rowcount,
    }


def _row_to_story(row: aiosqlite.Row) -> StoryRecord:
    """SQLite Row → StoryRecord（先反序列化 datetime 再 model_validate）。"""
    data = dict(row)
    data["created_at"] = _iso_to_dt(data["created_at"])
    data["updated_at"] = _iso_to_dt(data["updated_at"])
    # SQLite stores BOOLEAN as 0/1 integer; Pydantic strict mode requires native bool
    if "has_ui" in data:
        data["has_ui"] = bool(data["has_ui"])
    return StoryRecord.model_validate(data)


# ---------------------------------------------------------------------------
# CRUD — Tasks
# ---------------------------------------------------------------------------

_TASK_COLUMNS = (
    "task_id, story_id, phase, role, cli_tool, status, pid, expected_artifact, "
    "context_briefing, started_at, completed_at, exit_code, cost_usd, duration_ms, "
    "error_message, text_result, group_id"
)


async def insert_task(db: aiosqlite.Connection, task: TaskRecord) -> None:
    """插入一条 task 记录。"""
    await db.execute(
        f"INSERT INTO tasks ({_TASK_COLUMNS}) VALUES ("
        "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
            task.text_result,
            task.group_id,
        ),
    )
    await db.commit()


async def get_tasks_by_group(
    db: aiosqlite.Connection,
    group_id: str,
) -> list[TaskRecord]:
    """查询同一 group_id 下的所有 tasks。"""
    cursor = await db.execute(
        "SELECT * FROM tasks WHERE group_id = ? ORDER BY rowid",
        (group_id,),
    )
    rows = await cursor.fetchall()
    return [_row_to_task(r) for r in rows]


async def get_tasks_by_story(
    db: aiosqlite.Connection,
    story_id: str,
) -> list[TaskRecord]:
    """查询某个 story 下的所有 tasks，按 started_at 稳定排序。

    排序策略：started_at 非 NULL 的按时间正序，NULL 的放最后，tie-breaker 用 rowid。
    """
    cursor = await db.execute(
        "SELECT * FROM tasks WHERE story_id = ? ORDER BY started_at IS NULL, started_at, rowid",
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
        "text_result": str,
        "context_briefing": str,
        "started_at": datetime,
        "completed_at": datetime,
        "last_activity_type": str,
        "last_activity_summary": str,
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


async def update_task_activity(
    db: aiosqlite.Connection,
    task_id: str,
    *,
    activity_type: str | None,
    activity_summary: str | None,
    commit: bool = True,
) -> None:
    """仅更新 tasks.last_activity_* 列，不修改 status。"""
    await db.execute(
        "UPDATE tasks SET last_activity_type = ?, last_activity_summary = ? WHERE task_id = ?",
        (activity_type, activity_summary, task_id),
    )
    if commit:
        await db.commit()


def _row_to_task(row: aiosqlite.Row) -> TaskRecord:
    """SQLite Row → TaskRecord（先反序列化 datetime 再 model_validate）。"""
    data = dict(row)
    for dt_field in ("started_at", "completed_at"):
        data[dt_field] = _iso_to_dt(data[dt_field])
    return TaskRecord.model_validate(data)


async def get_tasks_by_status(
    db: aiosqlite.Connection,
    status: str,
) -> list[TaskRecord]:
    """查询指定状态的所有 tasks。

    Args:
        db: 活跃的 aiosqlite 连接。
        status: 要查询的 task 状态值。

    Returns:
        匹配状态的 TaskRecord 列表。
    """
    _task_status_validator.validate_python(status, strict=True)
    cursor = await db.execute(
        "SELECT * FROM tasks WHERE status = ? ORDER BY rowid",
        (status,),
    )
    rows = await cursor.fetchall()
    return [_row_to_task(r) for r in rows]


async def mark_running_tasks_paused(db: aiosqlite.Connection) -> int:
    """批量将所有 status='running' 或 'pending' 的 task 标记为 'paused'。

    不自动 commit——调用方负责事务边界。

    Returns:
        受影响的行数。
    """
    cursor = await db.execute(
        "UPDATE tasks SET status = ? WHERE status IN (?, ?)",
        ("paused", "running", "pending"),
    )
    return cursor.rowcount


async def get_running_tasks(db: aiosqlite.Connection) -> list[TaskRecord]:
    """返回所有 status='running' 的 tasks。

    崩溃恢复引擎使用此函数发现需要恢复的 tasks。
    """
    return await get_tasks_by_status(db, "running")


async def get_paused_tasks(db: aiosqlite.Connection) -> list[TaskRecord]:
    """返回所有 status='paused' 的 tasks。

    正常重启引擎使用此函数发现需要重调度的 tasks。
    """
    return await get_tasks_by_status(db, "paused")


async def get_undispatched_stories(db: aiosqlite.Connection) -> list[StoryRecord]:
    """返回 active batch 中处于活跃阶段且可被自动调度的 stories。

    用于检测需要初始调度的 stories（batch confirm 后首次 dispatch）。
    存在 pending blocking approval（``crash_recovery`` 或 ``needs_human_review``）
    的 story 会被排除，避免在等待人工决策期间被初始调度路径再次补发 task。

    ``fixing`` 不能被全局排除：除 convergent loop 之外，UAT / regression
    也会进入 fixing，并依赖初始调度恢复 owner task。对于 convergent loop
    产生的 fixing，pending placeholder/task 会自然把它挡在查询结果之外。
    """
    cursor = await db.execute(
        """
        SELECT s.story_id, s.title, s.status, s.current_phase,
               s.worktree_path, s.created_at, s.updated_at, s.has_ui
        FROM stories s
        JOIN batch_stories bs ON s.story_id = bs.story_id
        JOIN batches b ON bs.batch_id = b.batch_id
        WHERE b.status = 'active'
          AND s.current_phase NOT IN ('queued', 'done', 'blocked')
          AND NOT EXISTS (
            SELECT 1 FROM tasks t
            WHERE t.story_id = s.story_id
              AND t.status IN ('running', 'pending', 'paused')
          )
          AND NOT EXISTS (
            SELECT 1 FROM approvals a
            WHERE a.story_id = s.story_id
              AND a.approval_type IN (
                'crash_recovery', 'needs_human_review',
                'merge_authorization', 'convergent_loop_escalation',
                'regression_failure', 'preflight_failure'
              )
              AND a.status = 'pending'
          )
        """,
    )
    rows = await cursor.fetchall()
    return [_row_to_story(row) for row in rows]


async def count_tasks_by_status(db: aiosqlite.Connection, status: str) -> int:
    """按状态计数 tasks。

    Args:
        db: 活跃的 aiosqlite 连接。
        status: 要计数的 task 状态值。

    Returns:
        匹配状态的 task 数量。
    """
    cursor = await db.execute(
        "SELECT COUNT(*) FROM tasks WHERE status = ?",
        (status,),
    )
    row = await cursor.fetchone()
    return int(row[0]) if row else 0


# ---------------------------------------------------------------------------
# CRUD — Approvals
# ---------------------------------------------------------------------------


async def insert_approval(
    db: aiosqlite.Connection,
    approval: ApprovalRecord,
    *,
    commit: bool = True,
) -> None:
    """插入一条 approval 记录。

    Args:
        db: 活跃的 aiosqlite 连接。
        approval: 待插入的 ApprovalRecord。
        commit: 是否自动 commit。``False`` 时由调用方负责 commit，
            用于 SAVEPOINT 事务内调用。
    """
    await db.execute(
        "INSERT INTO approvals (approval_id, story_id, approval_type, status, "
        "payload, decision, decided_at, created_at, "
        "recommended_action, risk_level, decision_reason, consumed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            approval.approval_id,
            approval.story_id,
            approval.approval_type,
            approval.status,
            approval.payload,
            approval.decision,
            _dt_to_iso(approval.decided_at),
            _dt_to_iso(approval.created_at),
            approval.recommended_action,
            approval.risk_level,
            approval.decision_reason,
            _dt_to_iso(approval.consumed_at),
        ),
    )
    if commit:
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
    for dt_field in ("decided_at", "created_at", "consumed_at"):
        if dt_field in data:
            data[dt_field] = _iso_to_dt(data[dt_field])
    return ApprovalRecord.model_validate(data)


async def update_approval_decision(
    db: aiosqlite.Connection,
    approval_id: str,
    *,
    status: str,
    decision: str,
    decision_reason: str | None = None,
    decided_at: datetime,
) -> None:
    """更新审批决策。

    Args:
        db: 活跃的 aiosqlite 连接。
        approval_id: Approval 唯一标识。
        status: 新状态（approved / rejected）。
        decision: 具体决策选项（如 restart / resume / approve）。
        decision_reason: 可选决策理由。
        decided_at: 决策时间戳。

    Raises:
        ValueError: approval_id 不存在。
    """
    cursor = await db.execute(
        "UPDATE approvals SET status = ?, decision = ?, decision_reason = ?, decided_at = ? "
        "WHERE approval_id = ? AND status = 'pending'",
        (status, decision, decision_reason, _dt_to_iso(decided_at), approval_id),
    )
    if cursor.rowcount == 0:
        msg = f"Approval '{approval_id}' not found or already decided"
        raise ValueError(msg)


async def get_approval_by_id(
    db: aiosqlite.Connection,
    approval_id_prefix: str,
) -> ApprovalRecord:
    """按 ID 或前缀查询单条 approval。

    Args:
        db: 活跃的 aiosqlite 连接。
        approval_id_prefix: 完整 ID 或 ≥4 字符前缀。

    Returns:
        匹配的 ApprovalRecord。

    Raises:
        ValueError: 未找到、前缀过短或多个匹配。
    """
    if len(approval_id_prefix) < 4:
        msg = "approval_id 前缀至少需要 4 个字符"
        raise ValueError(msg)

    cursor = await db.execute(
        "SELECT * FROM approvals WHERE approval_id LIKE ? || '%'",
        (approval_id_prefix,),
    )
    rows = list(await cursor.fetchall())
    if len(rows) == 0:
        msg = f"未找到匹配的 approval: {approval_id_prefix}"
        raise ValueError(msg)
    if len(rows) > 1:
        msg = f"前缀 '{approval_id_prefix}' 匹配到 {len(rows)} 条记录，请提供更长的前缀"
        raise ValueError(msg)
    return _row_to_approval(rows[0])


async def get_decided_unconsumed_approvals(
    db: aiosqlite.Connection,
) -> list[ApprovalRecord]:
    """查询已决策但未消费的 approvals（供 Orchestrator poll cycle 使用）。"""
    cursor = await db.execute(
        "SELECT * FROM approvals WHERE status != ? AND consumed_at IS NULL ORDER BY rowid",
        ("pending",),
    )
    rows = await cursor.fetchall()
    return [_row_to_approval(r) for r in rows]


async def mark_approval_consumed(
    db: aiosqlite.Connection,
    approval_id: str,
    consumed_at: datetime,
) -> None:
    """标记 approval 已消费（仅在处理成功后调用）。

    Raises:
        ValueError: approval_id 不存在。
    """
    cursor = await db.execute(
        "UPDATE approvals SET consumed_at = ? WHERE approval_id = ?",
        (_dt_to_iso(consumed_at), approval_id),
    )
    if cursor.rowcount == 0:
        msg = f"Approval '{approval_id}' not found in database"
        raise ValueError(msg)
    await db.commit()


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
        "s.worktree_path, s.has_ui, s.created_at, s.updated_at "
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
                "has_ui": bool(data["has_ui"]) if data.get("has_ui") is not None else False,
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


async def mark_batch_spec_committed(db: aiosqlite.Connection, batch_id: str) -> bool:
    """标记 batch 的 spec 文件已提交到 main。

    仅当 spec_committed 为 0 时执行更新，返回是否实际更新。
    """
    cursor = await db.execute(
        "UPDATE batches SET spec_committed = 1 WHERE batch_id = ? AND spec_committed = 0",
        (batch_id,),
    )
    updated = cursor.rowcount > 0
    if updated:
        await db.commit()
    return updated


async def complete_batch(db: aiosqlite.Connection, batch_id: str) -> bool:
    """将 batch 从 active 收敛为 completed。

    仅当 status 为 active 时执行更新，返回是否实际更新。
    提供跨重启幂等性——已 completed 的 batch 不会重复更新。
    """
    now = datetime.now(tz=UTC)
    cursor = await db.execute(
        "UPDATE batches SET status = ?, completed_at = ? WHERE batch_id = ? AND status = ?",
        ("completed", _dt_to_iso(now), batch_id, "active"),
    )
    updated = cursor.rowcount > 0
    if updated:
        await db.commit()
    return updated


def _row_to_batch(row: aiosqlite.Row) -> BatchRecord:
    """SQLite Row → BatchRecord。"""
    data = dict(row)
    data["created_at"] = _iso_to_dt(data["created_at"])
    data["completed_at"] = _iso_to_dt(data["completed_at"])
    # SQLite stores BOOLEAN as 0/1 integer; Pydantic strict mode requires native bool
    if "spec_committed" in data:
        data["spec_committed"] = bool(data["spec_committed"])
    return BatchRecord.model_validate(data)


# ---------------------------------------------------------------------------
# CRUD — Cost Log (Story 2B.1)
# ---------------------------------------------------------------------------

_COST_LOG_DDL = """\
CREATE TABLE IF NOT EXISTS cost_log (
    cost_log_id TEXT PRIMARY KEY,
    story_id    TEXT NOT NULL,
    task_id     TEXT,
    cli_tool    TEXT NOT NULL,
    model       TEXT,
    phase       TEXT NOT NULL,
    role        TEXT,
    input_tokens   INTEGER NOT NULL,
    output_tokens  INTEGER NOT NULL,
    cache_read_input_tokens INTEGER DEFAULT 0,
    cost_usd    REAL NOT NULL,
    duration_ms INTEGER,
    session_id  TEXT,
    exit_code   INTEGER,
    error_category TEXT,
    created_at  TEXT NOT NULL
)"""


async def insert_cost_log(db: aiosqlite.Connection, record: CostLogRecord) -> None:
    """插入一条 cost_log 记录。"""
    await db.execute(
        "INSERT INTO cost_log (cost_log_id, story_id, task_id, cli_tool, model, "
        "phase, role, input_tokens, output_tokens, cache_read_input_tokens, "
        "cost_usd, duration_ms, session_id, exit_code, error_category, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            record.cost_log_id,
            record.story_id,
            record.task_id,
            record.cli_tool,
            record.model,
            record.phase,
            record.role,
            record.input_tokens,
            record.output_tokens,
            record.cache_read_input_tokens,
            record.cost_usd,
            record.duration_ms,
            record.session_id,
            record.exit_code,
            record.error_category,
            _dt_to_iso(record.created_at),
        ),
    )
    await db.commit()


async def get_cost_summary(
    db: aiosqlite.Connection,
    *,
    story_id: str | None = None,
) -> dict[str, float | int]:
    """聚合成本摘要。

    Returns:
        包含 total_cost_usd, total_input_tokens, total_output_tokens, call_count 的字典。
    """
    if story_id:
        cursor = await db.execute(
            "SELECT COALESCE(SUM(cost_usd), 0), "
            "COALESCE(SUM(input_tokens), 0), "
            "COALESCE(SUM(output_tokens), 0), "
            "COUNT(*) "
            "FROM cost_log WHERE story_id = ?",
            (story_id,),
        )
    else:
        cursor = await db.execute(
            "SELECT COALESCE(SUM(cost_usd), 0), "
            "COALESCE(SUM(input_tokens), 0), "
            "COALESCE(SUM(output_tokens), 0), "
            "COUNT(*) "
            "FROM cost_log",
        )
    row = await cursor.fetchone()
    assert row is not None
    return {
        "total_cost_usd": float(row[0]),
        "total_input_tokens": int(row[1]),
        "total_output_tokens": int(row[2]),
        "call_count": int(row[3]),
    }


async def get_cost_by_period(
    db: aiosqlite.Connection,
    since: datetime,
) -> dict[str, float | int]:
    """按时间段聚合成本（since 之后的所有 cost_log）。

    Returns:
        包含 total_cost_usd, total_input_tokens, total_output_tokens, call_count 的字典。
    """
    cursor = await db.execute(
        "SELECT COALESCE(SUM(cost_usd), 0), "
        "COALESCE(SUM(input_tokens), 0), "
        "COALESCE(SUM(output_tokens), 0), "
        "COUNT(*) "
        "FROM cost_log WHERE created_at >= ?",
        (_dt_to_iso(since),),
    )
    row = await cursor.fetchone()
    assert row is not None
    return {
        "total_cost_usd": float(row[0]),
        "total_input_tokens": int(row[1]),
        "total_output_tokens": int(row[2]),
        "call_count": int(row[3]),
    }


async def get_cost_by_story(
    db: aiosqlite.Connection,
    since: datetime | None = None,
) -> list[dict[str, object]]:
    """按 story 聚合成本。

    Args:
        db: 活跃的 aiosqlite 连接。
        since: 可选时间下界，仅聚合该时间之后的记录。

    Returns:
        每个 story 一条 dict: {story_id, total_cost_usd, call_count}。
    """
    if since is not None:
        cursor = await db.execute(
            "SELECT story_id, COALESCE(SUM(cost_usd), 0), COUNT(*) "
            "FROM cost_log WHERE created_at >= ? "
            "GROUP BY story_id ORDER BY SUM(cost_usd) DESC",
            (_dt_to_iso(since),),
        )
    else:
        cursor = await db.execute(
            "SELECT story_id, COALESCE(SUM(cost_usd), 0), COUNT(*) "
            "FROM cost_log GROUP BY story_id ORDER BY SUM(cost_usd) DESC",
        )
    rows = await cursor.fetchall()
    return [
        {
            "story_id": row[0],
            "total_cost_usd": float(row[1]),
            "call_count": int(row[2]),
        }
        for row in rows
    ]


async def get_cost_logs_by_story(
    db: aiosqlite.Connection,
    story_id: str,
) -> list[CostLogRecord]:
    """获取某个 story 的全部 cost_log 记录，按创建时间排序。"""
    cursor = await db.execute(
        "SELECT * FROM cost_log WHERE story_id = ? ORDER BY created_at",
        (story_id,),
    )
    rows = await cursor.fetchall()
    return [_row_to_cost_log(r) for r in rows]


def _row_to_cost_log(row: aiosqlite.Row) -> CostLogRecord:
    """SQLite Row → CostLogRecord。"""
    data = dict(row)
    data["created_at"] = _iso_to_dt(data["created_at"])
    return CostLogRecord.model_validate(data)


# ---------------------------------------------------------------------------
# CRUD — Findings (Story 3.1)
# ---------------------------------------------------------------------------

_FINDING_COLUMNS = (
    "finding_id, story_id, phase, round_num, severity, description, status, "
    "file_path, rule_id, dedup_hash, line_number, fix_suggestion, created_at"
)


async def insert_finding(db: aiosqlite.Connection, record: FindingRecord) -> None:
    """插入一条 finding 记录。写前通过 model_validate 校验。"""
    FindingRecord.model_validate(record.model_dump())
    await db.execute(
        f"INSERT INTO findings ({_FINDING_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            record.finding_id,
            record.story_id,
            record.phase,
            record.round_num,
            record.severity,
            record.description,
            record.status,
            record.file_path,
            record.rule_id,
            record.dedup_hash,
            record.line_number,
            record.fix_suggestion,
            _dt_to_iso(record.created_at),
        ),
    )
    await db.commit()


async def insert_findings_batch(
    db: aiosqlite.Connection,
    records: list[FindingRecord],
) -> None:
    """批量插入 findings，SAVEPOINT 保证原子性。

    全部成功才 commit；任何一条失败则整批回滚，不留半成品。
    """
    if not records:
        return
    for record in records:
        FindingRecord.model_validate(record.model_dump())
    await db.execute("SAVEPOINT findings_batch")
    try:
        await db.executemany(
            f"INSERT INTO findings ({_FINDING_COLUMNS}) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    r.finding_id,
                    r.story_id,
                    r.phase,
                    r.round_num,
                    r.severity,
                    r.description,
                    r.status,
                    r.file_path,
                    r.rule_id,
                    r.dedup_hash,
                    r.line_number,
                    r.fix_suggestion,
                    _dt_to_iso(r.created_at),
                )
                for r in records
            ],
        )
        await db.execute("RELEASE SAVEPOINT findings_batch")
    except BaseException:
        await db.execute("ROLLBACK TO SAVEPOINT findings_batch")
        await db.execute("RELEASE SAVEPOINT findings_batch")
        raise
    await db.commit()


async def get_findings_by_story(
    db: aiosqlite.Connection,
    story_id: str,
    *,
    created_after: datetime | None = None,
    phase: str | None = None,
    round_num: int | None = None,
) -> list[FindingRecord]:
    """查询某个 story 的 findings，可按 phase / round_num 过滤。"""
    conditions = ["story_id = ?"]
    params: list[str | int] = [story_id]
    if phase is not None:
        conditions.append("phase = ?")
        params.append(phase)
    if created_after is not None:
        conditions.append("created_at > ?")
        params.append(_dt_to_iso(created_after) or "")
    if round_num is not None:
        conditions.append("round_num = ?")
        params.append(round_num)
    cursor = await db.execute(
        f"SELECT * FROM findings WHERE {' AND '.join(conditions)} ORDER BY rowid",
        tuple(params),
    )
    rows = await cursor.fetchall()
    return [_row_to_finding(r) for r in rows]


async def get_open_findings(
    db: aiosqlite.Connection,
    story_id: str,
    *,
    created_after: datetime | None = None,
    phase: str | None = None,
) -> list[FindingRecord]:
    """查询 status IN ('open', 'still_open') 的 findings，可按 phase 过滤。"""
    conditions = ["story_id = ?", "status IN (?, ?)"]
    params: list[str] = [story_id, "open", "still_open"]
    if phase is not None:
        conditions.append("phase = ?")
        params.append(phase)
    if created_after is not None:
        conditions.append("created_at > ?")
        params.append(_dt_to_iso(created_after) or "")
    cursor = await db.execute(
        f"SELECT * FROM findings WHERE {' AND '.join(conditions)} ORDER BY rowid",
        tuple(params),
    )
    rows = await cursor.fetchall()
    return [_row_to_finding(r) for r in rows]


async def get_finding_trajectory(
    db: aiosqlite.Connection,
    story_id: str,
) -> list[dict[str, Any]]:
    """返回每个 finding 的 first_seen_round + current_status 摘要。

    Story 3.3: MVP 合同——不做逐轮插值，只返回当前 schema 能可靠表达的摘要。
    排序：round_num ASC, file_path ASC, rule_id ASC。
    """
    findings = await get_findings_by_story(db, story_id)
    return [
        {
            "finding_id": f.finding_id,
            "file_path": f.file_path,
            "rule_id": f.rule_id,
            "severity": f.severity,
            "description": f.description,
            "first_seen_round": f.round_num,
            "current_status": f.status,
        }
        for f in sorted(findings, key=lambda f: (f.round_num, f.file_path, f.rule_id))
    ]


async def update_finding_status(
    db: aiosqlite.Connection,
    finding_id: str,
    new_status: FindingStatus,
) -> None:
    """更新 finding 状态。"""
    _finding_status_validator.validate_python(new_status, strict=True)
    cursor = await db.execute(
        "UPDATE findings SET status = ? WHERE finding_id = ?",
        (new_status, finding_id),
    )
    if cursor.rowcount == 0:
        msg = f"Finding '{finding_id}' not found in database"
        raise ValueError(msg)
    await db.commit()


async def count_findings_by_severity(
    db: aiosqlite.Connection,
    story_id: str,
    round_num: int,
    *,
    created_after: datetime | None = None,
    phase: str | None = None,
) -> dict[str, int]:
    """按 severity 统计 findings 数量，返回 {"blocking": N, "suggestion": M}。"""
    conditions = ["story_id = ?", "round_num = ?"]
    params: list[str | int] = [story_id, round_num]
    if phase is not None:
        conditions.append("phase = ?")
        params.append(phase)
    if created_after is not None:
        conditions.append("created_at > ?")
        params.append(_dt_to_iso(created_after) or "")
    cursor = await db.execute(
        "SELECT severity, COUNT(*) FROM findings "
        f"WHERE {' AND '.join(conditions)} GROUP BY severity",
        tuple(params),
    )
    rows = await cursor.fetchall()
    result: dict[str, int] = {"blocking": 0, "suggestion": 0}
    for row in rows:
        severity = row[0]
        if severity in result:
            result[severity] = int(row[1])
    return result


async def get_story_findings_summary(
    db: aiosqlite.Connection,
) -> dict[str, dict[str, int]]:
    """按 story 聚合 Review Findings 摘要。

    TUI 的审批详情需要展示“当前逻辑 issue 摘要”，而不是把同一
    ``dedup_hash`` 在多轮中的历史记录直接累加。

    聚合规则：
    - 先按 ``story_id + severity + dedup_hash`` 找到该 hash 的最新 ``round_num``
    - 仅统计该最新轮次上的记录，避免跨轮次重复累计
    - ``still_open`` 视作 ``open`` 展示
    - 同一轮内的重复记录保留其数量，不做额外压扁
    """
    cursor = await db.execute(
        """
        WITH latest_hash_rounds AS (
            SELECT
                story_id,
                severity,
                dedup_hash,
                MAX(round_num) AS latest_round
            FROM findings
            GROUP BY story_id, severity, dedup_hash
        )
        SELECT
            f.story_id,
            f.severity,
            f.status,
            COUNT(*) AS cnt
        FROM findings AS f
        JOIN latest_hash_rounds AS lhr
          ON f.story_id = lhr.story_id
         AND f.severity = lhr.severity
         AND f.dedup_hash = lhr.dedup_hash
         AND f.round_num = lhr.latest_round
        GROUP BY f.story_id, f.severity, f.status
        """
    )
    rows = await cursor.fetchall()
    summary: dict[str, dict[str, int]] = {}
    for row in rows:
        sid_key = str(row[0])
        severity = str(row[1])
        status = str(row[2])
        count = int(row[3])
        if sid_key not in summary:
            summary[sid_key] = {}
        bucket = "open" if status in {"open", "still_open"} else "closed"
        key = f"{severity}_{bucket}"
        summary[sid_key][key] = summary[sid_key].get(key, 0) + count
    return summary


def _row_to_finding(row: aiosqlite.Row) -> FindingRecord:
    """SQLite Row → FindingRecord。"""
    data = dict(row)
    data["created_at"] = _iso_to_dt(data["created_at"])
    return FindingRecord.model_validate(data)


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
        [(run_id, r.layer, r.check_item, r.status, r.message) for r in results],
    )
    await db.commit()


async def save_worktree_preflight_result(
    db: aiosqlite.Connection,
    result: WorktreePreflightResult,
    *,
    commit: bool = False,
) -> int:
    """插入一条 worktree 边界 preflight 审计结果。

    ``commit=False`` 是默认值，让 TransitionQueue / MergeQueue 控制事务边界。
    """
    validated = WorktreePreflightResult.model_validate(result.model_dump())
    cursor = await db.execute(
        "INSERT INTO worktree_preflight_results ("
        "story_id, gate_type, passed, base_ref, base_sha, head_sha, "
        "porcelain_output, diffstat, changed_files, failure_reason, error_output, checked_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            validated.story_id,
            validated.gate_type,
            int(validated.passed),
            validated.base_ref,
            validated.base_sha,
            validated.head_sha,
            validated.porcelain_output,
            validated.diffstat,
            json.dumps(validated.changed_files),
            validated.failure_reason,
            validated.error_output,
            _dt_to_iso(validated.checked_at),
        ),
    )
    if commit:
        await db.commit()
    row_id = cursor.lastrowid
    if row_id is None:
        msg = "SQLite did not return a row id for worktree_preflight_results insert"
        raise RuntimeError(msg)
    return row_id


# ---------------------------------------------------------------------------
# CRUD — Merge Queue (Story 4.2)
# ---------------------------------------------------------------------------


async def enqueue_merge(
    db: aiosqlite.Connection,
    story_id: str,
    approval_id: str,
    approved_at: datetime,
    enqueued_at: datetime,
) -> None:
    """将 story 加入 merge queue。

    若同一 story 已有 ``failed`` 状态的记录（retry 场景），
    则更新为 ``waiting`` 而非插入新行，避免 UNIQUE 约束冲突。
    """
    # 先尝试将已有的 failed 记录重置为 waiting
    cursor = await db.execute(
        "UPDATE merge_queue SET approval_id = ?, approved_at = ?, "
        "enqueued_at = ?, status = 'waiting' "
        "WHERE story_id = ? AND status = 'failed'",
        (approval_id, _dt_to_iso(approved_at), _dt_to_iso(enqueued_at), story_id),
    )
    if cursor.rowcount > 0:
        await db.commit()
        return

    await db.execute(
        "INSERT INTO merge_queue (story_id, approval_id, approved_at, enqueued_at, status) "
        "VALUES (?, ?, ?, ?, 'waiting')",
        (story_id, approval_id, _dt_to_iso(approved_at), _dt_to_iso(enqueued_at)),
    )
    await db.commit()


async def dequeue_next_merge(db: aiosqlite.Connection) -> MergeQueueEntry | None:
    """取出下一个待 merge 的条目（按 approved_at ASC, id ASC），并将其 status 更新为 'merging'。"""
    cursor = await db.execute(
        "SELECT * FROM merge_queue WHERE status = 'waiting' "
        "ORDER BY approved_at ASC, id ASC LIMIT 1"
    )
    row = await cursor.fetchone()
    if row is None:
        return None

    entry = _row_to_merge_queue_entry(row)
    await db.execute(
        "UPDATE merge_queue SET status = 'merging' WHERE id = ?",
        (entry.id,),
    )
    await db.commit()
    return MergeQueueEntry.model_validate(
        {**entry.model_dump(), "status": "merging"},
    )


async def mark_regression_dispatched(
    db: aiosqlite.Connection,
    story_id: str,
    task_id: str,
) -> None:
    """记录 regression task_id 并将 status 更新为 'regression_pending'。"""
    cursor = await db.execute(
        "UPDATE merge_queue SET status = 'regression_pending', regression_task_id = ? "
        "WHERE story_id = ?",
        (task_id, story_id),
    )
    if cursor.rowcount == 0:
        msg = f"Merge queue entry for story '{story_id}' not found"
        raise ValueError(msg)
    await db.commit()


async def complete_merge(
    db: aiosqlite.Connection,
    story_id: str,
    *,
    success: bool,
) -> None:
    """更新 merge queue entry status 为 'merged' 或 'failed'。"""
    new_status = "merged" if success else "failed"
    cursor = await db.execute(
        "UPDATE merge_queue SET status = ? WHERE story_id = ?",
        (new_status, story_id),
    )
    if cursor.rowcount == 0:
        msg = f"Merge queue entry for story '{story_id}' not found"
        raise ValueError(msg)
    await db.commit()


async def get_merge_queue_state(db: aiosqlite.Connection) -> MergeQueueState:
    """读取 merge queue 全局状态（单例行）。"""
    cursor = await db.execute("SELECT * FROM merge_queue_state WHERE id = 1")
    row = await cursor.fetchone()
    if row is None:
        return MergeQueueState()
    data = dict(row)
    return MergeQueueState(
        frozen=bool(data["frozen"]),
        frozen_reason=data.get("frozen_reason"),
        frozen_at=_iso_to_dt(data.get("frozen_at")),
        current_merge_story_id=data.get("current_merge_story_id"),
    )


async def set_current_merge_story(
    db: aiosqlite.Connection,
    story_id: str | None,
) -> None:
    """设置当前正在 merge 的 story ID（None 表示清除）。"""
    await db.execute(
        "UPDATE merge_queue_state SET current_merge_story_id = ? WHERE id = 1",
        (story_id,),
    )
    await db.commit()


async def set_merge_queue_frozen(
    db: aiosqlite.Connection,
    *,
    frozen: bool,
    reason: str | None,
) -> None:
    """设置 merge queue 冻结状态。"""
    frozen_at = _dt_to_iso(datetime.now(tz=UTC)) if frozen else None
    await db.execute(
        "UPDATE merge_queue_state SET frozen = ?, frozen_reason = ?, frozen_at = ? WHERE id = 1",
        (int(frozen), reason, frozen_at),
    )
    await db.commit()


async def get_pending_merges(db: aiosqlite.Connection) -> list[MergeQueueEntry]:
    """返回所有 status='waiting' 的 merge queue 条目。"""
    cursor = await db.execute(
        "SELECT * FROM merge_queue WHERE status = 'waiting' ORDER BY approved_at ASC, id ASC"
    )
    rows = await cursor.fetchall()
    return [_row_to_merge_queue_entry(r) for r in rows]


async def remove_from_merge_queue(
    db: aiosqlite.Connection,
    story_id: str,
) -> None:
    """从 merge queue 移除指定 story。"""
    await db.execute(
        "DELETE FROM merge_queue WHERE story_id = ?",
        (story_id,),
    )
    await db.commit()


async def set_pre_merge_head(
    db: aiosqlite.Connection,
    story_id: str,
    commit_hash: str,
) -> None:
    """记录 merge 前 main 分支的 HEAD commit hash。"""
    await db.execute(
        "UPDATE merge_queue SET pre_merge_head = ? WHERE story_id = ?",
        (commit_hash, story_id),
    )
    await db.commit()


async def get_merge_queue_entry(
    db: aiosqlite.Connection,
    story_id: str,
) -> MergeQueueEntry | None:
    """按 story_id 查询 merge queue entry。"""
    cursor = await db.execute(
        "SELECT * FROM merge_queue WHERE story_id = ?",
        (story_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    return _row_to_merge_queue_entry(row)


def _row_to_merge_queue_entry(row: aiosqlite.Row) -> MergeQueueEntry:
    """SQLite Row → MergeQueueEntry。"""
    data = dict(row)
    data["approved_at"] = _iso_to_dt(data["approved_at"])
    data["enqueued_at"] = _iso_to_dt(data["enqueued_at"])
    return MergeQueueEntry.model_validate(data)
