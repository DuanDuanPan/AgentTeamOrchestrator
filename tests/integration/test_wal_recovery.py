"""test_wal_recovery — WAL 崩溃恢复验证测试。

验证 WAL 模式下：
- 已提交事务的数据可恢复
- 未提交事务的数据不可见
- 恢复关键字段（status, pid, expected_artifact）完整保留
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from ato.models.db import (
    get_connection,
    get_story,
    get_tasks_by_story,
    insert_story,
    insert_task,
)
from ato.models.schemas import StoryRecord, TaskRecord

_NOW = datetime.now(tz=UTC)


# ---------------------------------------------------------------------------
# WAL 模式确认
# ---------------------------------------------------------------------------


class TestWalModeActive:
    async def test_journal_mode_is_wal(self, initialized_db_path: Path) -> None:
        db = await get_connection(initialized_db_path)
        try:
            cursor = await db.execute("PRAGMA journal_mode")
            row = await cursor.fetchone()
            assert row is not None
            assert str(row[0]).lower() == "wal"
        finally:
            await db.close()


# ---------------------------------------------------------------------------
# AC3: 已提交事务可恢复
# ---------------------------------------------------------------------------


class TestCommittedDataRecovery:
    async def test_committed_story_survives_reconnect(self, initialized_db_path: Path) -> None:
        """写入 story 并 commit → 重新打开连接 → 数据完整。"""
        # 写入并 commit
        db = await get_connection(initialized_db_path)
        story = _make_story("recover-s1")
        await insert_story(db, story)
        await db.close()

        # 重新打开
        db2 = await get_connection(initialized_db_path)
        try:
            result = await get_story(db2, "recover-s1")
            assert result is not None
            assert result.story_id == "recover-s1"
            assert result.title == "恢复测试 story"
            assert result.status == "in_progress"
        finally:
            await db2.close()

    async def test_committed_task_preserves_recovery_fields(
        self, initialized_db_path: Path
    ) -> None:
        """写入 task 并 commit → 重新打开 → status/pid/expected_artifact 完整。"""
        db = await get_connection(initialized_db_path)
        story = _make_story("recover-s2")
        await insert_story(db, story)
        task = TaskRecord(
            task_id="recover-t1",
            story_id="recover-s2",
            phase="dev",
            role="developer",
            cli_tool="claude",
            status="running",
            pid=54321,
            expected_artifact="/output/artifact.json",
        )
        await insert_task(db, task)
        await db.close()

        # 重新打开
        db2 = await get_connection(initialized_db_path)
        try:
            tasks = await get_tasks_by_story(db2, "recover-s2")
            assert len(tasks) == 1
            recovered = tasks[0]
            assert recovered.status == "running"
            assert recovered.pid == 54321
            assert recovered.expected_artifact == "/output/artifact.json"
        finally:
            await db2.close()

    async def test_multiple_committed_records(self, initialized_db_path: Path) -> None:
        """多条记录提交后全部可恢复。"""
        db = await get_connection(initialized_db_path)
        for i in range(5):
            story = _make_story(f"multi-s{i}")
            await insert_story(db, story)
        await db.close()

        db2 = await get_connection(initialized_db_path)
        try:
            for i in range(5):
                result = await get_story(db2, f"multi-s{i}")
                assert result is not None
        finally:
            await db2.close()


# ---------------------------------------------------------------------------
# AC3: 未提交事务不可见
# ---------------------------------------------------------------------------


class TestUncommittedDataInvisible:
    async def test_uncommitted_story_not_visible_after_rollback(
        self, initialized_db_path: Path
    ) -> None:
        """事务内写入但 rollback → 重新打开 → 数据不存在。"""
        db = await get_connection(initialized_db_path)

        # 手动控制事务：写入后 rollback（不 commit）
        await db.execute(
            "INSERT INTO stories (story_id, title, status, current_phase, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                "ghost-s1",
                "幽灵 story",
                "backlog",
                "planning",
                _NOW.isoformat(),
                _NOW.isoformat(),
            ),
        )
        await db.rollback()
        await db.close()

        # 重新打开
        db2 = await get_connection(initialized_db_path)
        try:
            result = await get_story(db2, "ghost-s1")
            assert result is None
        finally:
            await db2.close()

    async def test_uncommitted_not_visible_after_close_without_commit(
        self, initialized_db_path: Path
    ) -> None:
        """事务内写入后直接关闭连接（不 commit）→ 数据不可见。

        aiosqlite 在 close 时对未提交的事务执行隐式 rollback。
        """
        db_raw = await aiosqlite.connect(initialized_db_path)
        await db_raw.execute("PRAGMA foreign_keys = ON")

        # 写入但不 commit
        await db_raw.execute(
            "INSERT INTO stories (story_id, title, status, current_phase, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                "ghost-s2",
                "幽灵 story 2",
                "backlog",
                "planning",
                _NOW.isoformat(),
                _NOW.isoformat(),
            ),
        )
        # 直接关闭
        await db_raw.close()

        # 重新打开验证
        db2 = await get_connection(initialized_db_path)
        try:
            result = await get_story(db2, "ghost-s2")
            assert result is None
        finally:
            await db2.close()


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _make_story(story_id: str) -> StoryRecord:
    return StoryRecord(
        story_id=story_id,
        title="恢复测试 story",
        status="in_progress",
        current_phase="dev",
        created_at=_NOW,
        updated_at=_NOW,
    )
