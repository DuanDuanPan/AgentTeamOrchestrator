"""test_cli_exit_codes — 退出码规范测试（Story 4.4）。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

from typer.testing import CliRunner

from ato.cli import EXIT_ENV_ERROR, EXIT_ERROR, app
from ato.models.db import init_db

runner = CliRunner()


def _init_db_sync(db_path: Path) -> None:
    asyncio.run(init_db(db_path))


class TestExitCodeDbNotExist:
    """DB 不存在 → code=2（环境错误）。"""

    def test_exit_code_db_not_exist_approvals(self, tmp_path: Path) -> None:
        """ato approvals — DB 不存在返回 code=2。"""
        bad_db = tmp_path / "nonexistent" / "state.db"
        result = runner.invoke(app, ["approvals", "--db-path", str(bad_db)])
        assert result.exit_code == EXIT_ENV_ERROR

    def test_exit_code_db_not_exist_approve(self, tmp_path: Path) -> None:
        """ato approve — DB 不存在返回 code=2。"""
        bad_db = tmp_path / "nonexistent" / "state.db"
        result = runner.invoke(
            app, ["approve", "test1234", "--decision", "approve", "--db-path", str(bad_db)]
        )
        assert result.exit_code == EXIT_ENV_ERROR

    def test_exit_code_batch_status_db_not_exist(self, tmp_path: Path) -> None:
        """ato batch status — DB 不存在返回 code=2。"""
        bad_db = tmp_path / "nonexistent" / "state.db"
        result = runner.invoke(app, ["batch", "status", "--db-path", str(bad_db)])
        assert result.exit_code == EXIT_ENV_ERROR

    def test_exit_code_plan_db_not_exist(self, tmp_path: Path) -> None:
        """ato plan — DB 不存在返回 code=2。"""
        bad_db = tmp_path / "nonexistent" / "state.db"
        result = runner.invoke(app, ["plan", "story-1", "--db-path", str(bad_db)])
        assert result.exit_code == EXIT_ENV_ERROR

    def test_exit_code_tui_db_not_exist(self, tmp_path: Path) -> None:
        """ato tui — DB 不存在返回 code=2。"""
        bad_db = tmp_path / "nonexistent" / "state.db"
        result = runner.invoke(app, ["tui", "--db-path", str(bad_db)])
        assert result.exit_code == EXIT_ENV_ERROR

    def test_exit_code_uat_db_not_exist(self, tmp_path: Path) -> None:
        """ato uat — DB 不存在返回 code=2。"""
        bad_db = tmp_path / "nonexistent" / "state.db"
        result = runner.invoke(
            app, ["uat", "story-1", "--result", "pass", "--db-path", str(bad_db)]
        )
        assert result.exit_code == EXIT_ENV_ERROR

    def test_exit_code_submit_db_not_exist(self, tmp_path: Path) -> None:
        """ato submit — DB 不存在返回 code=2。"""
        bad_db = tmp_path / "nonexistent" / "state.db"
        result = runner.invoke(app, ["submit", "story-1", "--db-path", str(bad_db)])
        assert result.exit_code == EXIT_ENV_ERROR

    def test_exit_code_approval_detail_db_not_exist(self, tmp_path: Path) -> None:
        """ato approval-detail — DB 不存在返回 code=2。"""
        bad_db = tmp_path / "nonexistent" / "state.db"
        result = runner.invoke(app, ["approval-detail", "test1234", "--db-path", str(bad_db)])
        assert result.exit_code == EXIT_ENV_ERROR


class TestExitCodeBusinessErrors:
    """业务错误 → code=1。"""

    def test_exit_code_invalid_decision(self, tmp_path: Path) -> None:
        """无效决策选项返回 code=1。"""
        from ato.models.db import get_connection, insert_approval, insert_story
        from ato.models.schemas import ApprovalRecord, StoryRecord

        db_path = tmp_path / ".ato" / "state.db"
        _init_db_sync(db_path)

        from datetime import UTC, datetime

        now = datetime.now(tz=UTC)

        async def _setup() -> None:
            db = await get_connection(db_path)
            try:
                await insert_story(
                    db,
                    StoryRecord(
                        story_id="s1",
                        title="Test",
                        status="in_progress",
                        current_phase="developing",
                        created_at=now,
                        updated_at=now,
                    ),
                )
                await insert_approval(
                    db,
                    ApprovalRecord(
                        approval_id="aaaa1111-2222-3333-4444-555566667777",
                        story_id="s1",
                        approval_type="merge_authorization",
                        status="pending",
                        payload='{"options": ["approve", "reject"]}',
                        created_at=now,
                        recommended_action="approve",
                    ),
                )
            finally:
                await db.close()

        asyncio.run(_setup())

        result = runner.invoke(
            app,
            [
                "approve",
                "aaaa1111",
                "--decision",
                "invalid_option",
                "--db-path",
                str(db_path),
            ],
        )
        assert result.exit_code == EXIT_ERROR

    def test_exit_code_approval_not_found(self, tmp_path: Path) -> None:
        """审批不存在返回 code=1。"""
        db_path = tmp_path / ".ato" / "state.db"
        _init_db_sync(db_path)

        result = runner.invoke(
            app,
            ["approve", "zzzz0000", "--decision", "approve", "--db-path", str(db_path)],
        )
        assert result.exit_code == EXIT_ERROR


class TestExitCodeEnvErrorPreflight:
    """preflight 失败返回 code=2。"""

    def test_exit_code_env_error_preflight(self, tmp_path: Path) -> None:
        """ato start — preflight HALT 返回 code=2。"""
        from ato.models.schemas import CheckResult

        halt_result = CheckResult(
            check_item="test_check",
            status="HALT",
            message="Test halt",
            layer="system",
        )

        with (
            patch("ato.core.is_orchestrator_running", return_value=False),
            patch("ato.logging.configure_logging"),
            patch(
                "ato.preflight.run_preflight",
                new=AsyncMock(return_value=[halt_result]),
            ),
        ):
            result = runner.invoke(app, ["start", "--db-path", str(tmp_path / "state.db")])
        assert result.exit_code == EXIT_ENV_ERROR
