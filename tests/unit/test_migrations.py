"""test_migrations — 迁移机制测试。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest

from ato.models.migrations import MIGRATIONS, run_migrations
from ato.models.schemas import SCHEMA_VERSION, RecoveryError


class TestMigrations:
    async def test_migration_registry_covers_all_versions(self) -> None:
        """MIGRATIONS 注册表覆盖从 1 到 SCHEMA_VERSION 的所有版本。"""
        for v in range(1, SCHEMA_VERSION + 1):
            assert v in MIGRATIONS, f"Missing migration for version {v}"

    async def test_run_migrations_from_v0_to_current(self, db_path: Path) -> None:
        """从 v0 迁移到 SCHEMA_VERSION 成功执行。"""
        db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(db_path) as db:
            await db.execute("PRAGMA journal_mode = WAL")
            await db.execute("PRAGMA foreign_keys = ON")
            await run_migrations(db, 0, SCHEMA_VERSION)

            # 验证 user_version 已更新
            cursor = await db.execute("PRAGMA user_version")
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == SCHEMA_VERSION

            # 验证表已创建
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            tables = [r[0] for r in await cursor.fetchall()]
            assert "approvals" in tables
            assert "stories" in tables
            assert "tasks" in tables

    async def test_migration_failure_rolls_back_and_raises_recovery_error(
        self, db_path: Path
    ) -> None:
        """迁移失败时回滚并抛出 RecoveryError。"""
        db_path.parent.mkdir(parents=True, exist_ok=True)

        async def _failing_migration(db: aiosqlite.Connection) -> None:
            msg = "intentional failure"
            raise RuntimeError(msg)

        # 临时注入一个会失败的迁移
        original = MIGRATIONS.get(SCHEMA_VERSION + 1)
        MIGRATIONS[SCHEMA_VERSION + 1] = _failing_migration
        try:
            async with aiosqlite.connect(db_path) as db:
                await db.execute("PRAGMA journal_mode = WAL")
                await db.execute("PRAGMA foreign_keys = ON")

                # 先正常迁移到当前版本
                await run_migrations(db, 0, SCHEMA_VERSION)

                # 尝试迁移到 SCHEMA_VERSION+1 应该失败
                with pytest.raises(RecoveryError, match="intentional failure"):
                    await run_migrations(db, SCHEMA_VERSION, SCHEMA_VERSION + 1)

                # user_version 应停留在 SCHEMA_VERSION（失败步骤未提交）
                cursor = await db.execute("PRAGMA user_version")
                row = await cursor.fetchone()
                assert row is not None
                assert row[0] == SCHEMA_VERSION
        finally:
            if original is not None:
                MIGRATIONS[SCHEMA_VERSION + 1] = original
            else:
                MIGRATIONS.pop(SCHEMA_VERSION + 1, None)

    async def test_failed_migration_leaves_no_ddl_side_effects(self, db_path: Path) -> None:
        """迁移失败时不仅回滚 user_version，DDL 副作用（如新表）也不会残留。"""
        db_path.parent.mkdir(parents=True, exist_ok=True)

        async def _create_then_fail(db: aiosqlite.Connection) -> None:
            await db.execute("CREATE TABLE side_effect_table (id TEXT PRIMARY KEY)")
            msg = "boom after CREATE TABLE"
            raise RuntimeError(msg)

        original = MIGRATIONS.get(SCHEMA_VERSION + 1)
        MIGRATIONS[SCHEMA_VERSION + 1] = _create_then_fail
        try:
            async with aiosqlite.connect(db_path) as db:
                await db.execute("PRAGMA journal_mode = WAL")
                await db.execute("PRAGMA foreign_keys = ON")

                # 正常迁移到当前版本
                await run_migrations(db, 0, SCHEMA_VERSION)

                # 尝试 SCHEMA_VERSION+1 应失败
                with pytest.raises(RecoveryError, match="boom after CREATE TABLE"):
                    await run_migrations(db, SCHEMA_VERSION, SCHEMA_VERSION + 1)

                # 验证 side_effect_table 不存在（DDL 被回滚）
                cursor = await db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='side_effect_table'"
                )
                assert await cursor.fetchone() is None, (
                    "Failed migration left DDL side effect (table should not exist)"
                )
        finally:
            if original is not None:
                MIGRATIONS[SCHEMA_VERSION + 1] = original
            else:
                MIGRATIONS.pop(SCHEMA_VERSION + 1, None)

    async def test_missing_migration_raises_recovery_error(self, db_path: Path) -> None:
        """缺失迁移函数时抛出 RecoveryError。"""
        db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(db_path) as db:
            with pytest.raises(RecoveryError, match="Missing migration"):
                await run_migrations(db, 0, 999)

    async def test_idempotent_init_via_migrations(self, db_path: Path) -> None:
        """多次调用 run_migrations(0, SCHEMA_VERSION) 不报错（CREATE TABLE IF NOT EXISTS）。"""
        db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(db_path) as db:
            await db.execute("PRAGMA journal_mode = WAL")
            await db.execute("PRAGMA foreign_keys = ON")
            await run_migrations(db, 0, SCHEMA_VERSION)
            # 再次从 0 到 SCHEMA_VERSION（模拟重入场景）
            # 先重置 user_version
            await db.execute("PRAGMA user_version = 0")
            await db.commit()
            await run_migrations(db, 0, SCHEMA_VERSION)
            cursor = await db.execute("PRAGMA user_version")
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == SCHEMA_VERSION


class TestMigrationV2:
    """MIGRATIONS[2] — batches 和 batch_stories 表迁移测试。"""

    async def test_v2_creates_batches_table(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(db_path) as db:
            await db.execute("PRAGMA journal_mode = WAL")
            await db.execute("PRAGMA foreign_keys = ON")
            await run_migrations(db, 0, SCHEMA_VERSION)

            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='batches'"
            )
            assert await cursor.fetchone() is not None

    async def test_v2_creates_batch_stories_table(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(db_path) as db:
            await db.execute("PRAGMA journal_mode = WAL")
            await db.execute("PRAGMA foreign_keys = ON")
            await run_migrations(db, 0, SCHEMA_VERSION)

            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='batch_stories'"
            )
            assert await cursor.fetchone() is not None

    async def test_v2_creates_single_active_index(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(db_path) as db:
            await db.execute("PRAGMA journal_mode = WAL")
            await db.execute("PRAGMA foreign_keys = ON")
            await run_migrations(db, 0, SCHEMA_VERSION)

            cursor = await db.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='index' AND name='idx_batches_single_active'"
            )
            assert await cursor.fetchone() is not None

    async def test_v1_to_v2_incremental(self, db_path: Path) -> None:
        """从 v1 增量迁移到 v2 成功。"""
        db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(db_path) as db:
            await db.execute("PRAGMA journal_mode = WAL")
            await db.execute("PRAGMA foreign_keys = ON")
            # 先迁移到 v1
            await run_migrations(db, 0, 1)
            cursor = await db.execute("PRAGMA user_version")
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == 1

            # 再迁移到 v2
            await run_migrations(db, 1, 2)
            cursor = await db.execute("PRAGMA user_version")
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == 2

            # 验证新表存在
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            tables = [r[0] for r in await cursor.fetchall()]
            assert "batches" in tables
            assert "batch_stories" in tables


class TestMigrationV5:
    """MIGRATIONS[5] — findings 表迁移测试（Story 3.1）。"""

    async def test_migration_v4_to_v5(self, db_path: Path) -> None:
        """从 v4 数据库升级到 v5，findings 表存在。"""
        db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(db_path) as db:
            await db.execute("PRAGMA journal_mode = WAL")
            await db.execute("PRAGMA foreign_keys = ON")
            # 先迁移到 v4
            await run_migrations(db, 0, 4)
            cursor = await db.execute("PRAGMA user_version")
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == 4

            # 从 v4 迁移到 v5
            await run_migrations(db, 4, 5)
            cursor = await db.execute("PRAGMA user_version")
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == 5

            # 验证 findings 表存在
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='findings'"
            )
            assert await cursor.fetchone() is not None

    async def test_findings_indexes_exist(self, db_path: Path) -> None:
        """验证两个索引已创建。"""
        db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(db_path) as db:
            await db.execute("PRAGMA journal_mode = WAL")
            await db.execute("PRAGMA foreign_keys = ON")
            await run_migrations(db, 0, SCHEMA_VERSION)

            cursor = await db.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='index' AND name='idx_findings_story_round'"
            )
            assert await cursor.fetchone() is not None

            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_findings_dedup'"
            )
            assert await cursor.fetchone() is not None


class TestMigrationV8:
    """MIGRATIONS[8] — stories 表新增 has_ui 列测试（Story 9.3）。"""

    async def test_migration_v7_to_v8_adds_has_ui_column(self, db_path: Path) -> None:
        """从 v7 增量迁移到 v8，stories 表新增 has_ui 列。"""
        db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(db_path) as db:
            await db.execute("PRAGMA journal_mode = WAL")
            await db.execute("PRAGMA foreign_keys = ON")
            # 先迁移到 v7
            await run_migrations(db, 0, 7)
            cursor = await db.execute("PRAGMA user_version")
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == 7

            # 插入测试数据（v7 schema 无 has_ui / spec_committed）
            await db.execute(
                "INSERT INTO stories (story_id, title, status, current_phase, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                ("s1", "test", "backlog", "queued", "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
            )
            await db.execute(
                "INSERT INTO batches (batch_id, status, created_at) VALUES (?, ?, ?)",
                ("b1", "active", "2026-01-01T00:00:00"),
            )
            await db.commit()

            # 迁移到 v8
            await run_migrations(db, 7, 8)
            cursor = await db.execute("PRAGMA user_version")
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == 8

            # 验证 has_ui 列存在且默认为 0
            cursor = await db.execute("SELECT has_ui FROM stories WHERE story_id = ?", ("s1",))
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == 0

            # 验证 spec_committed 列存在且默认为 0
            cursor = await db.execute(
                "SELECT spec_committed FROM batches WHERE batch_id = ?", ("b1",)
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == 0

    async def test_migration_v8_idempotent(self, db_path: Path) -> None:
        """v8 迁移幂等——重复执行不报错。"""
        db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(db_path) as db:
            await db.execute("PRAGMA journal_mode = WAL")
            await db.execute("PRAGMA foreign_keys = ON")
            await run_migrations(db, 0, 8)
            # 重置 version 重跑
            await db.execute("PRAGMA user_version = 7")
            await db.commit()
            await run_migrations(db, 7, 8)
            cursor = await db.execute("PRAGMA user_version")
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == 8


class TestMigrationV9:
    """MIGRATIONS[9] — tasks 表新增 last_activity 列（LLM 实时可观测性）。"""

    async def test_migrate_v8_to_v9(self, db_path: Path) -> None:
        """AC 14: v8→v9 迁移新增两列。"""
        db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(db_path) as db:
            await db.execute("PRAGMA journal_mode = WAL")
            await db.execute("PRAGMA foreign_keys = ON")
            await run_migrations(db, 0, 9)
            cursor = await db.execute("PRAGMA table_info(tasks)")
            cols = {row[1] for row in await cursor.fetchall()}
            assert "last_activity_type" in cols
            assert "last_activity_summary" in cols

    async def test_migrate_v8_to_v9_idempotent(self, db_path: Path) -> None:
        """AC 14: 重复执行不报错。"""
        db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(db_path) as db:
            await db.execute("PRAGMA journal_mode = WAL")
            await db.execute("PRAGMA foreign_keys = ON")
            await run_migrations(db, 0, 9)
            # 重置 version 重跑
            await db.execute("PRAGMA user_version = 8")
            await db.commit()
            await run_migrations(db, 8, 9)
            cursor = await db.execute("PRAGMA user_version")
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == 9


class TestMigrationV10:
    """MIGRATIONS[10] — tasks 表新增 text_result 列（完整原始输出持久化）。"""

    async def test_migrate_v9_to_v10(self, db_path: Path) -> None:
        """v9→v10 迁移新增 text_result 列。"""
        db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(db_path) as db:
            await db.execute("PRAGMA journal_mode = WAL")
            await db.execute("PRAGMA foreign_keys = ON")
            await run_migrations(db, 0, 10)
            cursor = await db.execute("PRAGMA table_info(tasks)")
            cols = {row[1] for row in await cursor.fetchall()}
            assert "text_result" in cols

    async def test_migrate_v9_to_v10_idempotent(self, db_path: Path) -> None:
        """重复执行 v10 迁移不报错。"""
        db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(db_path) as db:
            await db.execute("PRAGMA journal_mode = WAL")
            await db.execute("PRAGMA foreign_keys = ON")
            await run_migrations(db, 0, 10)
            await db.execute("PRAGMA user_version = 9")
            await db.commit()
            await run_migrations(db, 9, 10)
            cursor = await db.execute("PRAGMA user_version")
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == 10


class TestMigrationV12:
    """MIGRATIONS[12] — findings 表新增 phase 列并回填历史。"""

    async def test_migrate_v11_to_v12_backfills_findings_phase(self, db_path: Path) -> None:
        """旧 findings 应根据最近的 convergent-loop task 回填 phase。"""
        db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(db_path) as db:
            await db.execute("PRAGMA journal_mode = WAL")
            await db.execute("PRAGMA foreign_keys = ON")
            await run_migrations(db, 0, 11)

            review_done = datetime.now(tz=UTC) - timedelta(minutes=10)
            qa_done = datetime.now(tz=UTC) - timedelta(minutes=1)
            story_id = "story-v12"
            await db.execute(
                "INSERT INTO stories (story_id, title, status, current_phase, worktree_path, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    story_id,
                    "Story V12",
                    "in_progress",
                    "reviewing",
                    "/tmp/wt",
                    review_done.isoformat(),
                    qa_done.isoformat(),
                ),
            )
            await db.execute(
                "INSERT INTO tasks (task_id, story_id, phase, role, cli_tool, status, "
                "started_at, completed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "t-review",
                    story_id,
                    "reviewing",
                    "reviewer",
                    "codex",
                    "completed",
                    (review_done - timedelta(minutes=1)).isoformat(),
                    review_done.isoformat(),
                ),
            )
            await db.execute(
                "INSERT INTO tasks (task_id, story_id, phase, role, cli_tool, status, "
                "started_at, completed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "t-qa",
                    story_id,
                    "qa_testing",
                    "qa",
                    "codex",
                    "completed",
                    (qa_done - timedelta(minutes=1)).isoformat(),
                    qa_done.isoformat(),
                ),
            )
            await db.execute(
                "INSERT INTO findings (finding_id, story_id, round_num, severity, description, "
                "status, file_path, rule_id, dedup_hash, line_number, fix_suggestion, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "f-review",
                    story_id,
                    3,
                    "blocking",
                    "old review issue",
                    "open",
                    "src/review.py",
                    "R-REVIEW",
                    "hash-review",
                    None,
                    None,
                    review_done.isoformat(),
                ),
            )
            await db.execute(
                "INSERT INTO findings (finding_id, story_id, round_num, severity, description, "
                "status, file_path, rule_id, dedup_hash, line_number, fix_suggestion, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "f-qa",
                    story_id,
                    1,
                    "blocking",
                    "qa issue",
                    "open",
                    "src/qa.py",
                    "R-QA",
                    "hash-qa",
                    None,
                    None,
                    qa_done.isoformat(),
                ),
            )
            await db.commit()

            await run_migrations(db, 11, 12)

            cursor = await db.execute("PRAGMA table_info(findings)")
            columns = {row[1] for row in await cursor.fetchall()}
            assert "phase" in columns

            cursor = await db.execute(
                "SELECT finding_id, phase FROM findings WHERE story_id = ? ORDER BY finding_id",
                (story_id,),
            )
            rows = await cursor.fetchall()
            assert [tuple(row) for row in rows] == [
                ("f-qa", "qa_testing"),
                ("f-review", "reviewing"),
            ]
