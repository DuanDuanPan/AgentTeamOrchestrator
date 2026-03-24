"""test_preflight_schema — CheckResult 模型验证、migration v2→v3、insert_preflight_results CRUD。"""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest
from pydantic import ValidationError

from ato.models.db import get_connection, insert_preflight_results
from ato.models.migrations import MIGRATIONS, run_migrations
from ato.models.schemas import SCHEMA_VERSION, CheckResult


class TestCheckResultModel:
    """CheckResult Pydantic 模型验证。"""

    def test_valid_check_result(self) -> None:
        """合法 CheckResult 创建成功。"""
        result = CheckResult(
            layer="system",
            check_item="python_version",
            status="PASS",
            message="Python 3.11.0",
        )
        assert result.layer == "system"
        assert result.check_item == "python_version"
        assert result.status == "PASS"
        assert result.message == "Python 3.11.0"

    def test_all_valid_statuses(self) -> None:
        """所有合法 status 值都能通过验证。"""
        for status in ("PASS", "HALT", "WARN", "INFO"):
            result = CheckResult(
                layer="system",
                check_item="test",
                status=status,  # type: ignore[arg-type]
                message="ok",
            )
            assert result.status == status

    def test_all_valid_layers(self) -> None:
        """所有合法 layer 值都能通过验证。"""
        for layer in ("system", "project", "artifact"):
            result = CheckResult(
                layer=layer,  # type: ignore[arg-type]
                check_item="test",
                status="PASS",
                message="ok",
            )
            assert result.layer == layer

    def test_invalid_status_rejected(self) -> None:
        """非法 status 值被拒绝。"""
        with pytest.raises(ValidationError):
            CheckResult(
                layer="system",
                check_item="test",
                status="INVALID",  # type: ignore[arg-type]
                message="bad",
            )

    def test_invalid_layer_rejected(self) -> None:
        """非法 layer 值被拒绝。"""
        with pytest.raises(ValidationError):
            CheckResult(
                layer="unknown",  # type: ignore[arg-type]
                check_item="test",
                status="PASS",
                message="bad",
            )

    def test_extra_fields_rejected(self) -> None:
        """未声明字段被拒绝（extra='forbid'）。"""
        with pytest.raises(ValidationError):
            CheckResult(
                layer="system",
                check_item="test",
                status="PASS",
                message="ok",
                extra_field="bad",  # type: ignore[call-arg]
            )

    def test_missing_required_field_rejected(self) -> None:
        """缺少必填字段被拒绝。"""
        with pytest.raises(ValidationError):
            CheckResult(  # type: ignore[call-arg]
                layer="system",
                check_item="test",
                status="PASS",
                # missing message
            )


class TestCheckStatusAndLayerTypes:
    """CheckStatus 和 CheckLayer 类型别名验证。"""

    def test_check_status_type_alias(self) -> None:
        """CheckStatus 类型包含 4 种状态。"""
        # 通过 Literal 可用于类型注解；运行时直接验证字符串
        result = CheckResult(
            layer="system", check_item="x", status="PASS", message="ok"
        )
        assert result.status == "PASS"

    def test_check_layer_type_alias(self) -> None:
        """CheckLayer 类型包含 3 种层。"""
        result = CheckResult(
            layer="artifact", check_item="x", status="INFO", message="ok"
        )
        assert result.layer == "artifact"


class TestMigrationV3:
    """MIGRATIONS[3] — preflight_results 表迁移测试。"""

    async def test_v3_migration_registered(self) -> None:
        """v3 迁移函数已注册。"""
        assert 3 in MIGRATIONS

    async def test_schema_version_is_3(self) -> None:
        """SCHEMA_VERSION 已更新为 3。"""
        assert SCHEMA_VERSION == 3

    async def test_v2_to_v3_creates_preflight_results_table(self, db_path: Path) -> None:
        """v2→v3 迁移成功创建 preflight_results 表。"""
        db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(db_path) as db:
            await db.execute("PRAGMA journal_mode = WAL")
            await db.execute("PRAGMA foreign_keys = ON")
            await run_migrations(db, 0, 3)

            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='preflight_results'"
            )
            assert await cursor.fetchone() is not None

    async def test_v2_to_v3_creates_run_id_index(self, db_path: Path) -> None:
        """v2→v3 迁移创建 idx_preflight_run_id 索引。"""
        db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(db_path) as db:
            await db.execute("PRAGMA journal_mode = WAL")
            await db.execute("PRAGMA foreign_keys = ON")
            await run_migrations(db, 0, 3)

            cursor = await db.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='index' AND name='idx_preflight_run_id'"
            )
            assert await cursor.fetchone() is not None

    async def test_v2_to_v3_incremental(self, db_path: Path) -> None:
        """从 v2 增量迁移到 v3 成功。"""
        db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(db_path) as db:
            await db.execute("PRAGMA journal_mode = WAL")
            await db.execute("PRAGMA foreign_keys = ON")
            # 先迁移到 v2
            await run_migrations(db, 0, 2)
            cursor = await db.execute("PRAGMA user_version")
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == 2

            # 再迁移到 v3
            await run_migrations(db, 2, 3)
            cursor = await db.execute("PRAGMA user_version")
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == 3

    async def test_preflight_results_table_schema(self, db_path: Path) -> None:
        """preflight_results 表有正确的列。"""
        db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(db_path) as db:
            await db.execute("PRAGMA journal_mode = WAL")
            await db.execute("PRAGMA foreign_keys = ON")
            await run_migrations(db, 0, 3)

            cursor = await db.execute("PRAGMA table_info(preflight_results)")
            columns = {row[1] for row in await cursor.fetchall()}
            expected = {"id", "run_id", "layer", "check_item", "status", "message", "created_at"}
            assert columns == expected


class TestInsertPreflightResults:
    """insert_preflight_results CRUD 测试。"""

    async def test_insert_and_query(self, initialized_db_path: Path) -> None:
        """插入后能查询到结果。"""
        results = [
            CheckResult(
                layer="system",
                check_item="python_version",
                status="PASS",
                message="Python 3.11.0",
            ),
            CheckResult(
                layer="system",
                check_item="claude_cli",
                status="HALT",
                message="Claude CLI not found",
            ),
        ]
        db = await get_connection(initialized_db_path)
        try:
            await insert_preflight_results(db, "run-001", results)

            cursor = await db.execute(
                "SELECT run_id, layer, check_item, status, message "
                "FROM preflight_results WHERE run_id = ? ORDER BY id",
                ("run-001",),
            )
            rows = await cursor.fetchall()
            assert len(rows) == 2
            assert rows[0][0] == "run-001"
            assert rows[0][1] == "system"
            assert rows[0][2] == "python_version"
            assert rows[0][3] == "PASS"
            assert rows[0][4] == "Python 3.11.0"
            assert rows[1][2] == "claude_cli"
            assert rows[1][3] == "HALT"
        finally:
            await db.close()

    async def test_insert_empty_list(self, initialized_db_path: Path) -> None:
        """插入空列表不报错。"""
        db = await get_connection(initialized_db_path)
        try:
            await insert_preflight_results(db, "run-empty", [])
            cursor = await db.execute(
                "SELECT COUNT(*) FROM preflight_results WHERE run_id = ?",
                ("run-empty",),
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == 0
        finally:
            await db.close()

    async def test_multiple_runs_isolated(self, initialized_db_path: Path) -> None:
        """不同 run_id 的结果互相隔离。"""
        r1 = [
            CheckResult(layer="system", check_item="a", status="PASS", message="ok"),
        ]
        r2 = [
            CheckResult(layer="project", check_item="b", status="WARN", message="warn"),
            CheckResult(layer="artifact", check_item="c", status="INFO", message="info"),
        ]
        db = await get_connection(initialized_db_path)
        try:
            await insert_preflight_results(db, "run-1", r1)
            await insert_preflight_results(db, "run-2", r2)

            cursor = await db.execute(
                "SELECT COUNT(*) FROM preflight_results WHERE run_id = ?", ("run-1",)
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == 1

            cursor = await db.execute(
                "SELECT COUNT(*) FROM preflight_results WHERE run_id = ?", ("run-2",)
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == 2
        finally:
            await db.close()
