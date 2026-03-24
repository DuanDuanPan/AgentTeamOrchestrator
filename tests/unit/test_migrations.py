"""test_migrations — 迁移机制测试。"""

from __future__ import annotations

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
