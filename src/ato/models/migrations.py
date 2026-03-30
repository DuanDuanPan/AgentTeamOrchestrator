"""migrations — SQLite schema 迁移函数。

使用 ``PRAGMA user_version`` 追踪 schema 版本号。
迁移函数按序执行，每一步在独立事务中完成。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import aiosqlite
import structlog

from ato.models.schemas import RecoveryError

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

# ---------------------------------------------------------------------------
# 迁移注册表
# ---------------------------------------------------------------------------

# key = 目标版本号, value = 迁移函数 (from version key-1 to version key)
MIGRATIONS: dict[int, Callable[[aiosqlite.Connection], Awaitable[None]]] = {}


def _register(
    version: int,
) -> Callable[
    [Callable[[aiosqlite.Connection], Awaitable[None]]],
    Callable[[aiosqlite.Connection], Awaitable[None]],
]:
    """注册迁移函数的装饰器。"""

    def decorator(
        fn: Callable[[aiosqlite.Connection], Awaitable[None]],
    ) -> Callable[[aiosqlite.Connection], Awaitable[None]]:
        MIGRATIONS[version] = fn
        return fn

    return decorator


# ---------------------------------------------------------------------------
# 迁移函数
# ---------------------------------------------------------------------------


@_register(1)
async def _migrate_v0_to_v1(db: aiosqlite.Connection) -> None:
    """v0 → v1: 创建核心表（stories, tasks, approvals）。

    注意：init_db 已经用 CREATE TABLE IF NOT EXISTS 创建了表，
    所以这里对新数据库是幂等的。对于从旧版本升级的数据库，
    这里确保表结构存在。
    """
    await db.execute(
        """\
        CREATE TABLE IF NOT EXISTS stories (
            story_id      TEXT PRIMARY KEY,
            title         TEXT NOT NULL,
            status        TEXT NOT NULL,
            current_phase TEXT NOT NULL,
            worktree_path TEXT,
            created_at    TEXT NOT NULL,
            updated_at    TEXT NOT NULL
        )"""
    )
    await db.execute(
        """\
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
    )
    await db.execute(
        """\
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
    )


@_register(2)
async def _migrate_v1_to_v2(db: aiosqlite.Connection) -> None:
    """v1 → v2: 新增 batches 和 batch_stories 表（Story 2B.5）。"""
    await db.execute(
        """\
        CREATE TABLE IF NOT EXISTS batches (
            batch_id     TEXT PRIMARY KEY,
            status       TEXT NOT NULL,
            created_at   TEXT NOT NULL,
            completed_at TEXT
        )"""
    )
    await db.execute(
        """\
        CREATE TABLE IF NOT EXISTS batch_stories (
            batch_id    TEXT NOT NULL REFERENCES batches(batch_id),
            story_id    TEXT NOT NULL REFERENCES stories(story_id),
            sequence_no INTEGER NOT NULL,
            PRIMARY KEY (batch_id, story_id),
            UNIQUE(batch_id, sequence_no)
        )"""
    )
    # 同一时间仅允许 1 个 active batch — partial unique index
    await db.execute(
        """\
        CREATE UNIQUE INDEX IF NOT EXISTS idx_batches_single_active
        ON batches(status) WHERE status = 'active'
        """
    )


@_register(3)
async def _migrate_v2_to_v3(db: aiosqlite.Connection) -> None:
    """v2 → v3: 新增 cost_log 表（Story 2B.1）。"""
    await db.execute(
        """\
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
    )


@_register(4)
async def _migrate_v3_to_v4(db: aiosqlite.Connection) -> None:
    """v3 → v4: 新增 preflight_results 表（Story 1.4a）。"""
    await db.execute(
        """\
        CREATE TABLE IF NOT EXISTS preflight_results (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id      TEXT    NOT NULL,
            layer       TEXT    NOT NULL,
            check_item  TEXT    NOT NULL,
            status      TEXT    NOT NULL,
            message     TEXT    NOT NULL,
            created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now'))
        )"""
    )
    await db.execute("CREATE INDEX IF NOT EXISTS idx_preflight_run_id ON preflight_results(run_id)")


@_register(5)
async def _migrate_v4_to_v5(db: aiosqlite.Connection) -> None:
    """v4 → v5: 新增 findings 表 + 索引（Story 3.1）。"""
    await db.execute(
        """\
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
        )"""
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_findings_story_round ON findings(story_id, round_num)"
    )
    await db.execute("CREATE INDEX IF NOT EXISTS idx_findings_dedup ON findings(dedup_hash)")


async def _column_exists(db: aiosqlite.Connection, table: str, column: str) -> bool:
    """检查 SQLite 表中是否存在指定列。"""
    cursor = await db.execute(f"PRAGMA table_info({table})")
    rows = await cursor.fetchall()
    return any(row[1] == column for row in rows)


@_register(6)
async def _migrate_v5_to_v6(db: aiosqlite.Connection) -> None:
    """v5 → v6: approvals 表新增 4 列（Story 4.1）。"""
    for col in ("recommended_action", "risk_level", "decision_reason", "consumed_at"):
        if not await _column_exists(db, "approvals", col):
            await db.execute(f"ALTER TABLE approvals ADD COLUMN {col} TEXT")


@_register(7)
async def _migrate_v6_to_v7(db: aiosqlite.Connection) -> None:
    """v6 → v7: 新增 merge_queue 和 merge_queue_state 表（Story 4.2）。"""
    await db.execute(
        """\
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
    )
    await db.execute(
        """\
        CREATE TABLE IF NOT EXISTS merge_queue_state (
            id                      INTEGER PRIMARY KEY CHECK (id = 1),
            frozen                  INTEGER NOT NULL DEFAULT 0,
            frozen_reason           TEXT,
            frozen_at               TEXT,
            current_merge_story_id  TEXT
        )"""
    )
    await db.execute("INSERT OR IGNORE INTO merge_queue_state (id, frozen) VALUES (1, 0)")


@_register(8)
async def _migrate_v7_to_v8(db: aiosqlite.Connection) -> None:
    """v7 → v8: stories.has_ui + batches.spec_committed（Story 9.3）。"""
    if not await _column_exists(db, "stories", "has_ui"):
        await db.execute("ALTER TABLE stories ADD COLUMN has_ui BOOLEAN DEFAULT 0")
    if not await _column_exists(db, "batches", "spec_committed"):
        await db.execute("ALTER TABLE batches ADD COLUMN spec_committed BOOLEAN DEFAULT 0")


@_register(9)
async def _migrate_v8_to_v9(db: aiosqlite.Connection) -> None:
    """v8 → v9: tasks 表新增 last_activity 列 + running task 索引（LLM 实时可观测性）。"""
    for col in ("last_activity_type", "last_activity_summary"):
        if not await _column_exists(db, "tasks", col):
            await db.execute(f"ALTER TABLE tasks ADD COLUMN {col} TEXT")
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_tasks_running_activity "
        "ON tasks(story_id, status, phase, started_at) "
        "WHERE status = 'running'"
    )


@_register(10)
async def _migrate_v9_to_v10(db: aiosqlite.Connection) -> None:
    """v9 → v10: tasks 表新增 text_result 列（保留完整 agent 原始输出）。"""
    if not await _column_exists(db, "tasks", "text_result"):
        await db.execute("ALTER TABLE tasks ADD COLUMN text_result TEXT")


# ---------------------------------------------------------------------------
# 迁移执行器
# ---------------------------------------------------------------------------


async def run_migrations(
    db: aiosqlite.Connection,
    current_version: int,
    target_version: int,
) -> None:
    """按序执行迁移函数。

    每个版本步骤在独立事务中完成，成功后更新 ``user_version``。
    单步迁移失败时回滚该步事务并抛出 :class:`RecoveryError`。
    """
    for version in range(current_version + 1, target_version + 1):
        migrate_fn = MIGRATIONS.get(version)
        if migrate_fn is None:
            msg = f"Missing migration function for version {version}"
            raise RecoveryError(msg)

        try:
            # 用 SAVEPOINT 包裹每步迁移，确保失败时能回滚 DDL 副作用。
            # SQLite 在 autocommit=off 时 DDL 和 DML 共享同一事务，
            # SAVEPOINT 允许局部回滚而不影响外层连接状态。
            await db.execute(f"SAVEPOINT migration_v{version}")
            await migrate_fn(db)
            await db.execute(f"RELEASE SAVEPOINT migration_v{version}")
            await db.execute(f"PRAGMA user_version = {version}")
            await db.commit()
            logger.info("migration_applied", version=version)
        except Exception as exc:
            await db.execute(f"ROLLBACK TO SAVEPOINT migration_v{version}")
            await db.execute(f"RELEASE SAVEPOINT migration_v{version}")
            if isinstance(exc, RecoveryError):
                raise
            msg = f"Migration to version {version} failed: {exc}"
            raise RecoveryError(msg) from exc
