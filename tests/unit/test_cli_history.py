"""test_cli_history — ato history 命令单元测试 (Story 5.2)。"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

from ato.cli import app
from ato.models.db import get_connection, init_db, insert_story, insert_task
from ato.models.schemas import StoryRecord, TaskRecord

_NOW = datetime.now(tz=UTC)
runner = CliRunner()


def _init_db_sync(db_path: Path) -> None:
    asyncio.run(init_db(db_path))


def _setup_story_with_tasks(
    db_path: Path,
    *,
    story_id: str = "story-005",
    tasks: list[dict[str, object]] | None = None,
) -> None:
    async def _inner() -> None:
        db = await get_connection(db_path)
        try:
            await insert_story(
                db,
                StoryRecord(
                    story_id=story_id,
                    title=f"Test {story_id}",
                    status="in_progress",
                    current_phase="developing",
                    created_at=_NOW,
                    updated_at=_NOW,
                ),
            )
            if tasks:
                for t in tasks:
                    await insert_task(
                        db,
                        TaskRecord.model_validate(
                            {
                                "task_id": t.get("task_id", "t-001"),
                                "story_id": story_id,
                                "phase": t.get("phase", "developing"),
                                "role": t.get("role", "dev"),
                                "cli_tool": t.get("cli_tool", "claude"),
                                "status": t.get("status", "completed"),
                                "started_at": t.get("started_at", _NOW),
                                "completed_at": t.get("completed_at", _NOW),
                                "cost_usd": t.get("cost_usd", 0.15),
                                "duration_ms": t.get("duration_ms", 12000),
                                "expected_artifact": t.get("expected_artifact"),
                                "context_briefing": t.get("context_briefing"),
                            }
                        ),
                    )
        finally:
            await db.close()

    asyncio.run(_inner())


class TestHistoryCommand:
    def test_history_command_renders_table(self, tmp_path: Path) -> None:
        """story 有任务时渲染时间轴表格。"""
        db_path = tmp_path / ".ato" / "state.db"
        _init_db_sync(db_path)
        _setup_story_with_tasks(
            db_path,
            tasks=[
                {
                    "task_id": "t-001",
                    "phase": "creating",
                    "role": "dev",
                    "status": "completed",
                    "cost_usd": 0.15,
                    "duration_ms": 12000,
                },
                {
                    "task_id": "t-002",
                    "phase": "developing",
                    "role": "dev",
                    "status": "completed",
                    "cost_usd": 1.20,
                    "duration_ms": 205000,
                },
            ],
        )

        result = runner.invoke(app, ["history", "story-005", "--db-path", str(db_path)])
        assert result.exit_code == 0
        assert "story-005" in result.output
        assert "creating" in result.output
        assert "developing" in result.output
        assert "$0.15" in result.output
        assert "$1.20" in result.output
        assert "汇总" in result.output
        assert "2 个任务" in result.output

    def test_history_command_story_not_found(self, tmp_path: Path) -> None:
        """story 不存在时使用错误格式。"""
        db_path = tmp_path / ".ato" / "state.db"
        _init_db_sync(db_path)

        result = runner.invoke(app, ["history", "nonexistent", "--db-path", str(db_path)])
        assert result.exit_code == 1
        assert "Story 不存在" in result.output

    def test_history_time_format_same_day(self, tmp_path: Path) -> None:
        """同日时间显示 HH:MM:SS 格式。"""
        db_path = tmp_path / ".ato" / "state.db"
        _init_db_sync(db_path)
        now = datetime.now(tz=UTC)
        _setup_story_with_tasks(
            db_path,
            tasks=[
                {
                    "task_id": "t-time",
                    "started_at": now,
                    "completed_at": now,
                },
            ],
        )

        result = runner.invoke(app, ["history", "story-005", "--db-path", str(db_path)])
        assert result.exit_code == 0
        # 应包含 HH:MM:SS 格式
        time_str = now.strftime("%H:%M:%S")
        assert time_str in result.output

    def test_history_shows_artifact_column(self, tmp_path: Path) -> None:
        """优先展示 context_briefing.artifacts_produced，fallback expected_artifact。"""
        db_path = tmp_path / ".ato" / "state.db"
        _init_db_sync(db_path)

        briefing = json.dumps({"artifacts_produced": ["plan.md", "diff.patch"]})
        _setup_story_with_tasks(
            db_path,
            tasks=[
                {
                    "task_id": "t-art1",
                    "context_briefing": briefing,
                    "expected_artifact": "fallback.md",
                },
                {
                    "task_id": "t-art2",
                    "expected_artifact": "review.md",
                },
            ],
        )

        result = runner.invoke(app, ["history", "story-005", "--db-path", str(db_path)])
        assert result.exit_code == 0
        # context_briefing 优先
        assert "plan.md" in result.output
        # 第二个 task 应 fallback 到 expected_artifact
        assert "review.md" in result.output

    def test_history_summary_row(self, tmp_path: Path) -> None:
        """表格底部汇总行正确。"""
        db_path = tmp_path / ".ato" / "state.db"
        _init_db_sync(db_path)
        _setup_story_with_tasks(
            db_path,
            tasks=[
                {"task_id": "t-s1", "cost_usd": 0.50, "duration_ms": 30000},
                {"task_id": "t-s2", "cost_usd": 1.50, "duration_ms": 90000},
            ],
        )

        result = runner.invoke(app, ["history", "story-005", "--db-path", str(db_path)])
        assert result.exit_code == 0
        assert "汇总" in result.output
        assert "$2.00" in result.output
        assert "2m00s" in result.output
