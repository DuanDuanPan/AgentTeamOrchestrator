"""test_history_cost_e2e — history/cost 命令集成测试 (Story 5.2)。

插入记录 → CLI 命令 → 验证输出。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

from ato.cli import app
from ato.models.db import (
    get_connection,
    init_db,
    insert_cost_log,
    insert_story,
    insert_task,
)
from ato.models.schemas import CostLogRecord, StoryRecord, TaskRecord

_NOW = datetime.now(tz=UTC)
runner = CliRunner()


def _setup_history_scenario(db_path: Path) -> None:
    """构造 history 测试场景：story + 多个 tasks。"""

    async def _inner() -> None:
        await init_db(db_path)
        db = await get_connection(db_path)
        try:
            await insert_story(
                db,
                StoryRecord(
                    story_id="story-e2e",
                    title="E2E Test Story",
                    status="in_progress",
                    current_phase="reviewing",
                    created_at=_NOW,
                    updated_at=_NOW,
                ),
            )
            for i, (phase, role, status, cost, dur) in enumerate(
                [
                    ("creating", "dev", "completed", 0.10, 5000),
                    ("developing", "dev", "completed", 1.50, 180000),
                    ("reviewing", "qa", "completed", 0.30, 20000),
                ],
                start=1,
            ):
                await insert_task(
                    db,
                    TaskRecord(
                        task_id=f"task-e2e-{i}",
                        story_id="story-e2e",
                        phase=phase,
                        role=role,
                        cli_tool="claude",
                        status=status,  # type: ignore[arg-type]
                        started_at=_NOW,
                        completed_at=_NOW,
                        cost_usd=cost,
                        duration_ms=dur,
                    ),
                )
        finally:
            await db.close()

    asyncio.run(_inner())


def _setup_cost_scenario(db_path: Path) -> None:
    """构造 cost 测试场景：story + 多个 cost_log。"""

    async def _inner() -> None:
        await init_db(db_path)
        db = await get_connection(db_path)
        try:
            await insert_story(
                db,
                StoryRecord(
                    story_id="story-cost-e2e",
                    title="Cost E2E Story",
                    status="in_progress",
                    current_phase="developing",
                    created_at=_NOW,
                    updated_at=_NOW,
                ),
            )
            for i, (phase, cost, inp, out) in enumerate(
                [
                    ("creating", 0.15, 1000, 500),
                    ("developing", 2.50, 15000, 8000),
                    ("reviewing", 0.45, 3000, 1500),
                ],
                start=1,
            ):
                await insert_cost_log(
                    db,
                    CostLogRecord.model_validate(
                        {
                            "cost_log_id": f"cl-e2e-{i}",
                            "story_id": "story-cost-e2e",
                            "cli_tool": "claude",
                            "phase": phase,
                            "input_tokens": inp,
                            "output_tokens": out,
                            "cost_usd": cost,
                            "created_at": _NOW,
                        }
                    ),
                )
        finally:
            await db.close()

    asyncio.run(_inner())


class TestHistoryE2E:
    def test_history_shows_task_timeline(self, tmp_path: Path) -> None:
        """插入任务记录 → ato history → 验证时间轴输出。"""
        db_path = tmp_path / ".ato" / "state.db"
        _setup_history_scenario(db_path)

        result = runner.invoke(app, ["history", "story-e2e", "--db-path", str(db_path)])
        assert result.exit_code == 0

        output = result.output
        assert "story-e2e" in output
        assert "creating" in output
        assert "reviewing" in output
        assert "$0.10" in output
        assert "$1.50" in output
        assert "汇总" in output
        assert "3 个任务" in output


class TestCostReportE2E:
    def test_cost_report_aggregates_correctly(self, tmp_path: Path) -> None:
        """插入成本记录 → ato cost report → 验证聚合金额。"""
        db_path = tmp_path / ".ato" / "state.db"
        _setup_cost_scenario(db_path)

        result = runner.invoke(app, ["cost", "report", "--db-path", str(db_path)])
        assert result.exit_code == 0

        output = result.output
        assert "成本报告" in output
        # 总成本 = 0.15 + 2.50 + 0.45 = $3.10
        assert "$3.10" in output
        assert "story-cost-e2e" in output
        # Token totals: 19000 input, 10000 output
        assert "19000" in output
        assert "10000" in output
