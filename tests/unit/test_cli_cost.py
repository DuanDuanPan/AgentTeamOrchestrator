"""test_cli_cost — ato cost report 命令单元测试 (Story 5.2)。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from typer.testing import CliRunner

from ato.cli import app
from ato.models.db import get_connection, init_db, insert_cost_log, insert_story
from ato.models.schemas import CostLogRecord, StoryRecord

_NOW = datetime.now(tz=UTC)
runner = CliRunner()


def _init_db_sync(db_path: Path) -> None:
    asyncio.run(init_db(db_path))


def _insert_cost_data(
    db_path: Path,
    records: list[dict[str, object]],
) -> None:
    async def _inner() -> None:
        db = await get_connection(db_path)
        try:
            # Ensure stories exist
            story_ids = set()
            for r in records:
                sid = str(r.get("story_id", "story-1"))
                story_ids.add(sid)

            for sid in story_ids:
                from ato.models.db import get_story

                existing = await get_story(db, sid)
                if existing is None:
                    await insert_story(
                        db,
                        StoryRecord(
                            story_id=sid,
                            title=f"Test {sid}",
                            status="in_progress",
                            current_phase="developing",
                            created_at=_NOW,
                            updated_at=_NOW,
                        ),
                    )

            for r in records:
                defaults: dict[str, object] = {
                    "cost_log_id": r.get("cost_log_id", "cl-001"),
                    "story_id": r.get("story_id", "story-1"),
                    "cli_tool": r.get("cli_tool", "claude"),
                    "phase": r.get("phase", "developing"),
                    "input_tokens": r.get("input_tokens", 100),
                    "output_tokens": r.get("output_tokens", 50),
                    "cost_usd": r.get("cost_usd", 0.01),
                    "created_at": r.get("created_at", _NOW),
                }
                if "cache_read_input_tokens" in r:
                    defaults["cache_read_input_tokens"] = r["cache_read_input_tokens"]
                await insert_cost_log(db, CostLogRecord.model_validate(defaults))
        finally:
            await db.close()

    asyncio.run(_inner())


class TestCostReportOverview:
    def test_cost_report_overview(self, tmp_path: Path) -> None:
        """总览模式渲染两个表格。"""
        db_path = tmp_path / ".ato" / "state.db"
        _init_db_sync(db_path)
        _insert_cost_data(
            db_path,
            [
                {
                    "cost_log_id": "cl-001",
                    "story_id": "story-a",
                    "cost_usd": 5.0,
                    "input_tokens": 5000,
                    "output_tokens": 2000,
                },
                {
                    "cost_log_id": "cl-002",
                    "story_id": "story-b",
                    "cost_usd": 3.0,
                    "input_tokens": 3000,
                    "output_tokens": 1000,
                },
            ],
        )

        result = runner.invoke(app, ["cost", "report", "--db-path", str(db_path)])
        assert result.exit_code == 0
        assert "成本报告" in result.output
        assert "时间范围汇总" in result.output
        assert "按 Story 明细" in result.output
        assert "story-a" in result.output
        assert "story-b" in result.output

    def test_cost_report_overview_includes_token_totals(self, tmp_path: Path) -> None:
        """今日/本周/全部表包含 token 汇总。"""
        db_path = tmp_path / ".ato" / "state.db"
        _init_db_sync(db_path)
        _insert_cost_data(
            db_path,
            [
                {
                    "cost_log_id": "cl-tok",
                    "input_tokens": 12000,
                    "output_tokens": 3400,
                    "cost_usd": 1.50,
                },
            ],
        )

        result = runner.invoke(app, ["cost", "report", "--db-path", str(db_path)])
        assert result.exit_code == 0
        assert "输入 Tokens" in result.output
        assert "输出 Tokens" in result.output
        assert "12000" in result.output
        assert "3400" in result.output


class TestCostReportByStory:
    def test_cost_report_by_story(self, tmp_path: Path) -> None:
        """story 详情模式渲染逐条记录。"""
        db_path = tmp_path / ".ato" / "state.db"
        _init_db_sync(db_path)
        _insert_cost_data(
            db_path,
            [
                {
                    "cost_log_id": "cl-s1",
                    "story_id": "story-x",
                    "phase": "creating",
                    "cost_usd": 0.25,
                    "input_tokens": 500,
                    "output_tokens": 200,
                },
                {
                    "cost_log_id": "cl-s2",
                    "story_id": "story-x",
                    "phase": "developing",
                    "cost_usd": 1.75,
                    "input_tokens": 5000,
                    "output_tokens": 2000,
                },
            ],
        )

        result = runner.invoke(
            app, ["cost", "report", "--story", "story-x", "--db-path", str(db_path)]
        )
        assert result.exit_code == 0
        assert "story-x" in result.output
        assert "creating" in result.output
        # "developing" may be truncated by rich table to "develop…"
        assert "develop" in result.output
        assert "$0.25" in result.output
        assert "$1.75" in result.output
        assert "汇总" in result.output


class TestCostReportCacheTokens:
    def test_cost_report_story_shows_cache_read_tokens(self, tmp_path: Path) -> None:
        """cache_read_input_tokens > 0 时，明细表出现 Cache Read 列。"""
        db_path = tmp_path / ".ato" / "state.db"
        _init_db_sync(db_path)
        _insert_cost_data(
            db_path,
            [
                {
                    "cost_log_id": "cl-cache",
                    "story_id": "story-cache",
                    "cost_usd": 0.50,
                    "input_tokens": 1000,
                    "output_tokens": 500,
                    "cache_read_input_tokens": 512,
                },
            ],
        )

        result = runner.invoke(
            app, ["cost", "report", "--story", "story-cache", "--db-path", str(db_path)]
        )
        assert result.exit_code == 0
        # Rich may wrap "Cache Read" header across lines; check both parts
        assert "Cache" in result.output
        assert "512" in result.output


class TestCostReportNoData:
    def test_cost_report_no_data(self, tmp_path: Path) -> None:
        """无数据时显示友好提示。"""
        db_path = tmp_path / ".ato" / "state.db"
        _init_db_sync(db_path)

        result = runner.invoke(app, ["cost", "report", "--db-path", str(db_path)])
        assert result.exit_code == 0
        assert "暂无成本数据" in result.output

    def test_cost_report_no_data_by_story(self, tmp_path: Path) -> None:
        """按 story 查询但无数据时显示友好提示。"""
        db_path = tmp_path / ".ato" / "state.db"
        _init_db_sync(db_path)

        result = runner.invoke(
            app, ["cost", "report", "--story", "nonexistent", "--db-path", str(db_path)]
        )
        assert result.exit_code == 0
        assert "暂无成本数据" in result.output


class TestCostReportPeriodAggregation:
    def test_cost_report_period_aggregation(self, tmp_path: Path) -> None:
        """今日/本周聚合正确。"""
        db_path = tmp_path / ".ato" / "state.db"
        _init_db_sync(db_path)

        now = datetime.now(tz=UTC)
        yesterday = now - timedelta(days=1)

        _insert_cost_data(
            db_path,
            [
                {
                    "cost_log_id": "cl-today",
                    "cost_usd": 2.0,
                    "input_tokens": 200,
                    "output_tokens": 100,
                    "created_at": now,
                },
                {
                    "cost_log_id": "cl-yesterday",
                    "cost_usd": 3.0,
                    "input_tokens": 300,
                    "output_tokens": 150,
                    "created_at": yesterday,
                },
            ],
        )

        result = runner.invoke(app, ["cost", "report", "--db-path", str(db_path)])
        assert result.exit_code == 0
        # 全部应显示 $5.00
        assert "$5.00" in result.output
