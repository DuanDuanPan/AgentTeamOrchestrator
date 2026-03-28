"""test_state_persistence — 状态转换 + SQLite 持久化集成测试。

验证完整流程：创建状态机 → send 事件 → save_story_state() → commit → 读回验证。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from ato.models.db import get_connection, get_story, insert_story
from ato.models.schemas import StoryRecord
from ato.state_machine import StoryLifecycle, save_story_state

_NOW = datetime.now(tz=UTC)
_STORY_ID = "integration-test-story"


async def _create_story_in_db(db: aiosqlite.Connection) -> None:
    """在数据库中插入初始 story 记录。"""
    story = StoryRecord(
        story_id=_STORY_ID,
        title="Integration Test Story",
        status="backlog",
        current_phase="queued",
        created_at=_NOW,
        updated_at=_NOW,
    )
    await insert_story(db, story)


class TestStatePersistenceIntegration:
    """状态机转换 + SQLite 持久化端到端集成测试。"""

    async def test_full_happy_path_persistence(self, initialized_db_path: Path) -> None:
        """完整 happy path：每次 transition 后持久化并读回验证。"""
        db = await get_connection(initialized_db_path)
        try:
            await _create_story_in_db(db)
            sm = await StoryLifecycle.create()

            events_and_expected = [
                ("start_create", "planning", "planning"),
                ("plan_done", "creating", "planning"),
                ("create_done", "validating", "planning"),
                ("validate_pass", "dev_ready", "ready"),
                ("start_dev", "developing", "in_progress"),
                ("dev_done", "reviewing", "review"),
                ("review_pass", "qa_testing", "in_progress"),
                ("qa_pass", "uat", "uat"),
                ("uat_pass", "merging", "in_progress"),
                ("merge_done", "regression", "in_progress"),
                ("regression_pass", "done", "done"),
            ]

            for event, expected_phase, expected_status in events_and_expected:
                # TransitionQueue 模拟：send → save → commit
                await sm.send(event)
                assert sm.current_state_value == expected_phase

                await save_story_state(db, _STORY_ID, sm.current_state_value)
                await db.commit()

                # 读回验证
                record = await get_story(db, _STORY_ID)
                assert record is not None
                assert record.current_phase == expected_phase
                assert record.status == expected_status
        finally:
            await db.close()

    async def test_convergent_loop_persistence(self, initialized_db_path: Path) -> None:
        """Convergent Loop：reviewing ↔ fixing 循环的持久化。"""
        db = await get_connection(initialized_db_path)
        try:
            await _create_story_in_db(db)
            sm = await StoryLifecycle.create()

            # 推进到 reviewing
            for event in ("start_create", "plan_done", "create_done", "validate_pass", "start_dev", "dev_done"):
                await sm.send(event)
                await save_story_state(db, _STORY_ID, sm.current_state_value)
                await db.commit()

            # review_fail → fixing
            await sm.send("review_fail")
            await save_story_state(db, _STORY_ID, sm.current_state_value)
            await db.commit()

            record = await get_story(db, _STORY_ID)
            assert record is not None
            assert record.current_phase == "fixing"
            assert record.status == "review"

            # fix_done → reviewing (re-review)
            await sm.send("fix_done")
            await save_story_state(db, _STORY_ID, sm.current_state_value)
            await db.commit()

            record = await get_story(db, _STORY_ID)
            assert record is not None
            assert record.current_phase == "reviewing"
            assert record.status == "review"

            # review_pass → qa_testing
            await sm.send("review_pass")
            await save_story_state(db, _STORY_ID, sm.current_state_value)
            await db.commit()

            record = await get_story(db, _STORY_ID)
            assert record is not None
            assert record.current_phase == "qa_testing"
            assert record.status == "in_progress"
        finally:
            await db.close()

    async def test_escalate_persistence(self, initialized_db_path: Path) -> None:
        """escalate 到 blocked 的持久化验证。"""
        db = await get_connection(initialized_db_path)
        try:
            await _create_story_in_db(db)
            sm = await StoryLifecycle.create()

            # 推进到 developing，然后 escalate
            for event in ("start_create", "plan_done", "create_done", "validate_pass", "start_dev"):
                await sm.send(event)
                await save_story_state(db, _STORY_ID, sm.current_state_value)
                await db.commit()

            await sm.send("escalate")
            await save_story_state(db, _STORY_ID, sm.current_state_value)
            await db.commit()

            record = await get_story(db, _STORY_ID)
            assert record is not None
            assert record.current_phase == "blocked"
            assert record.status == "blocked"
        finally:
            await db.close()

    async def test_transaction_boundary_respected(self, initialized_db_path: Path) -> None:
        """验证 save_story_state 不自动 commit：未 commit 前读回应为旧值。"""
        db = await get_connection(initialized_db_path)
        try:
            await _create_story_in_db(db)
            sm = await StoryLifecycle.create()

            await sm.send("start_create")
            await save_story_state(db, _STORY_ID, sm.current_state_value)
            # 不 commit

            # 开第二个连接读取 — 应看不到未 commit 的变更（WAL 隔离）
            db2 = await get_connection(initialized_db_path)
            try:
                record = await get_story(db2, _STORY_ID)
                assert record is not None
                assert record.current_phase == "queued"
                assert record.status == "backlog"
            finally:
                await db2.close()

            # 现在 commit
            await db.commit()

            # 再读应看到新值
            db3 = await get_connection(initialized_db_path)
            try:
                record = await get_story(db3, _STORY_ID)
                assert record is not None
                assert record.current_phase == "planning"
                assert record.status == "planning"
            finally:
                await db3.close()
        finally:
            await db.close()

    async def test_updated_at_advances(self, initialized_db_path: Path) -> None:
        """每次 save_story_state 后 updated_at 应推进。"""
        db = await get_connection(initialized_db_path)
        try:
            await _create_story_in_db(db)
            sm = await StoryLifecycle.create()

            await sm.send("start_create")
            await save_story_state(db, _STORY_ID, sm.current_state_value)
            await db.commit()

            record1 = await get_story(db, _STORY_ID)
            assert record1 is not None
            ts1 = record1.updated_at

            await sm.send("plan_done")
            await save_story_state(db, _STORY_ID, sm.current_state_value)
            await db.commit()

            record2 = await get_story(db, _STORY_ID)
            assert record2 is not None
            assert record2.updated_at >= ts1
        finally:
            await db.close()
