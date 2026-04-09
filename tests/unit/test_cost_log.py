"""test_cost_log — cost_log 表 CRUD 与聚合测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from ato.models.db import get_connection, get_cost_summary, init_db, insert_cost_log
from ato.models.schemas import CostLogRecord

_NOW = datetime.now(tz=UTC)


@pytest.fixture()
async def db_ready(tmp_path: Path) -> Path:
    db_path = tmp_path / ".ato" / "state.db"
    await init_db(db_path)
    return db_path


def _make_cost_log(
    *,
    cost_log_id: str = "cl-001",
    story_id: str = "story-1",
    cost_usd: float = 0.01,
    input_tokens: int = 100,
    output_tokens: int = 50,
    **overrides: object,
) -> CostLogRecord:
    defaults = {
        "cost_log_id": cost_log_id,
        "story_id": story_id,
        "cli_tool": "claude",
        "phase": "developing",
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": cost_usd,
        "created_at": _NOW,
    }
    defaults.update(overrides)
    return CostLogRecord.model_validate(defaults)


class TestInsertCostLog:
    async def test_insert_and_query(self, db_ready: Path) -> None:
        record = _make_cost_log()
        db = await get_connection(db_ready)
        await insert_cost_log(db, record)
        cursor = await db.execute("SELECT * FROM cost_log WHERE cost_log_id = ?", ("cl-001",))
        row = await cursor.fetchone()
        await db.close()
        assert row is not None
        assert dict(row)["cli_tool"] == "claude"
        assert dict(row)["input_tokens"] == 100

    async def test_insert_with_all_fields(self, db_ready: Path) -> None:
        record = _make_cost_log(
            cost_log_id="cl-full",
            task_id="task-001",
            model="claude-opus-4-6",
            role="developer",
            cache_read_input_tokens=200,
            duration_ms=5000,
            session_id="sess-123",
            exit_code=0,
            error_category=None,
        )
        db = await get_connection(db_ready)
        await insert_cost_log(db, record)
        cursor = await db.execute("SELECT * FROM cost_log WHERE cost_log_id = ?", ("cl-full",))
        row = await cursor.fetchone()
        await db.close()
        assert row is not None
        data = dict(row)
        assert data["model"] == "claude-opus-4-6"
        assert data["session_id"] == "sess-123"
        assert data["cache_read_input_tokens"] == 200


class TestGetCostSummary:
    async def test_empty_table(self, db_ready: Path) -> None:
        db = await get_connection(db_ready)
        summary = await get_cost_summary(db)
        await db.close()
        assert summary["total_cost_usd"] == 0.0
        assert summary["call_count"] == 0

    async def test_aggregate_all(self, db_ready: Path) -> None:
        db = await get_connection(db_ready)
        await insert_cost_log(db, _make_cost_log(cost_log_id="a", cost_usd=0.01))
        await insert_cost_log(db, _make_cost_log(cost_log_id="b", cost_usd=0.02))
        summary = await get_cost_summary(db)
        await db.close()
        assert summary["total_cost_usd"] == pytest.approx(0.03)
        assert summary["call_count"] == 2

    async def test_filter_by_story(self, db_ready: Path) -> None:
        db = await get_connection(db_ready)
        await insert_cost_log(db, _make_cost_log(cost_log_id="a", story_id="s1", cost_usd=0.01))
        await insert_cost_log(db, _make_cost_log(cost_log_id="b", story_id="s2", cost_usd=0.05))
        summary = await get_cost_summary(db, story_id="s1")
        await db.close()
        assert summary["total_cost_usd"] == pytest.approx(0.01)
        assert summary["call_count"] == 1
        assert summary["total_input_tokens"] == 100
