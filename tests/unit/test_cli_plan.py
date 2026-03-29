"""test_cli_plan — CLI plan 命令测试与渲染输出测试。"""

from __future__ import annotations

import io
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from rich.console import Console
from typer.testing import CliRunner

from ato.cli import app, render_plan
from ato.config import PhaseDefinition
from ato.models.schemas import StoryRecord, StoryStatus

runner = CliRunner()
# Note: typer's CliRunner mixes stderr into output by default.
# For stderr assertions we check result.output for the error text.


# ---------------------------------------------------------------------------
# 辅助: 构建 StoryRecord
# ---------------------------------------------------------------------------


def _story(
    story_id: str = "story-001",
    title: str = "用户认证模块",
    status: StoryStatus = "in_progress",
    current_phase: str = "developing",
) -> StoryRecord:
    now = datetime.now(UTC)
    return StoryRecord(
        story_id=story_id,
        title=title,
        status=status,
        current_phase=current_phase,
        created_at=now,
        updated_at=now,
    )


def _make_phase_defs() -> list[PhaseDefinition]:
    """构建模拟的 PhaseDefinition 列表。"""
    phases_data = [
        ("creating", "creator", "structured_job"),
        ("designing", "ux_designer", "structured_job"),
        ("validating", "validator", "convergent_loop"),
        ("dev_ready", "developer", "structured_job"),
        ("developing", "developer", "structured_job"),
        ("reviewing", "reviewer", "convergent_loop"),
        ("fixing", "fixer", "structured_job"),
        ("qa_testing", "qa", "convergent_loop"),
        ("uat", "developer", "interactive_session"),
        ("merging", "developer", "structured_job"),
        ("regression", "qa", "structured_job"),
    ]
    # next_on_success/failure are not used by render_plan, use dummy values
    return [
        PhaseDefinition(
            name=name,
            role=role,
            cli_tool="claude",
            model="opus",
            sandbox=None,
            phase_type=ptype,
            next_on_success="done",
            next_on_failure=None,
            timeout_seconds=1800,
        )
        for name, role, ptype in phases_data
    ]


# ---------------------------------------------------------------------------
# Task 3: CLI 命令测试
# ---------------------------------------------------------------------------


class TestPlanNormalFlow:
    """3.2 正常流程：story 在 developing 阶段。"""

    def test_exit_code_0_and_story_id_shown(self, tmp_path: Path) -> None:
        db_file = tmp_path / "state.db"
        db_file.touch()

        story = _story(current_phase="developing")
        mock_db = AsyncMock()

        with (
            patch("ato.cli.get_connection", new=AsyncMock(return_value=mock_db)),
            patch("ato.cli.get_story", new=AsyncMock(return_value=story)),
            patch("ato.cli.load_config") as mock_cfg,
            patch("ato.cli.build_phase_definitions", return_value=_make_phase_defs()),
        ):
            mock_cfg.return_value = MagicMock()
            result = runner.invoke(app, ["plan", "story-001", "--db-path", str(db_file)])

        assert result.exit_code == 0
        assert "story-001" in result.output
        assert "AgentTeamOrchestrator" in result.output
        mock_db.close.assert_awaited_once()


class TestPlanStoryNotFound:
    """3.3 Story 不存在。"""

    def test_exit_code_1_and_error_message(self, tmp_path: Path) -> None:
        db_file = tmp_path / "state.db"
        db_file.touch()

        mock_db = AsyncMock()

        with (
            patch("ato.cli.get_connection", new=AsyncMock(return_value=mock_db)),
            patch("ato.cli.get_story", new=AsyncMock(return_value=None)),
        ):
            result = runner.invoke(app, ["plan", "nonexistent", "--db-path", str(db_file)])

        assert result.exit_code == 1
        assert "Story 不存在" in result.output
        mock_db.close.assert_awaited_once()


class TestPlanDbNotExist:
    """3.4 数据库不存在。"""

    def test_exit_code_2_and_error_message(self, tmp_path: Path) -> None:
        db_path = tmp_path / "nonexistent.db"

        result = runner.invoke(app, ["plan", "story-001", "--db-path", str(db_path)])

        assert result.exit_code == 2
        assert "数据库不存在" in result.output


class TestPlanConfigDegradation:
    """3.5 配置加载失败降级。"""

    def test_exit_code_0_with_warning_and_phases(self, tmp_path: Path) -> None:
        db_file = tmp_path / "state.db"
        db_file.touch()

        story = _story(current_phase="developing")
        mock_db = AsyncMock()

        with (
            patch("ato.cli.get_connection", new=AsyncMock(return_value=mock_db)),
            patch("ato.cli.get_story", new=AsyncMock(return_value=story)),
            patch("ato.cli.load_config", side_effect=Exception("file not found")),
        ):
            result = runner.invoke(app, ["plan", "story-001", "--db-path", str(db_file)])

        assert result.exit_code == 0
        assert "配置加载失败" in result.output
        assert "仅显示阶段序列" in result.output
        # 仍有阶段输出
        assert "developing" in result.output
        assert "queued" in result.output


class TestPlanDoneStatus:
    """3.6 done 状态：所有阶段显示 ✔。"""

    def test_all_phases_completed(self, tmp_path: Path) -> None:
        db_file = tmp_path / "state.db"
        db_file.touch()

        story = _story(status="done", current_phase="done")
        mock_db = AsyncMock()

        with (
            patch("ato.cli.get_connection", new=AsyncMock(return_value=mock_db)),
            patch("ato.cli.get_story", new=AsyncMock(return_value=story)),
            patch("ato.cli.load_config", side_effect=Exception("no config")),
        ):
            result = runner.invoke(app, ["plan", "story-001", "--db-path", str(db_file)])

        assert result.exit_code == 0
        # All phases should show ✔
        assert result.output.count("✔") >= 13
        assert "▶" not in result.output
        assert "○" not in result.output


class TestPlanQueuedStatus:
    """3.7 queued 状态：仅 queued 为当前。"""

    def test_queued_is_current(self, tmp_path: Path) -> None:
        db_file = tmp_path / "state.db"
        db_file.touch()

        story = _story(status="backlog", current_phase="queued")
        mock_db = AsyncMock()

        with (
            patch("ato.cli.get_connection", new=AsyncMock(return_value=mock_db)),
            patch("ato.cli.get_story", new=AsyncMock(return_value=story)),
            patch("ato.cli.load_config", side_effect=Exception("no config")),
        ):
            result = runner.invoke(app, ["plan", "story-001", "--db-path", str(db_file)])

        assert result.exit_code == 0
        assert "▶" in result.output
        assert "当前" in result.output
        # No completed phases
        assert "✔" not in result.output


class TestPlanBlockedStatus:
    """3.8 blocked 状态：不伪造任何已完成/当前阶段。"""

    def test_blocked_shows_warning(self, tmp_path: Path) -> None:
        db_file = tmp_path / "state.db"
        db_file.touch()

        story = _story(status="blocked", current_phase="blocked")
        mock_db = AsyncMock()

        with (
            patch("ato.cli.get_connection", new=AsyncMock(return_value=mock_db)),
            patch("ato.cli.get_story", new=AsyncMock(return_value=story)),
            patch("ato.cli.load_config", side_effect=Exception("no config")),
        ):
            result = runner.invoke(app, ["plan", "story-001", "--db-path", str(db_file)])

        assert result.exit_code == 0
        assert "blocked" in result.output
        assert "MVP" in result.output
        # No completed or current markers
        assert "✔" not in result.output
        assert "▶" not in result.output


# ---------------------------------------------------------------------------
# Task 4: 渲染输出测试
# ---------------------------------------------------------------------------


class TestRenderPlanOutput:
    """直接调用 render_plan 验证渲染输出。"""

    def _capture(self, story: StoryRecord, phase_defs: list[PhaseDefinition]) -> str:
        buf = io.StringIO()
        con = Console(file=buf, force_terminal=True, width=120)
        render_plan(story, phase_defs, console=con)
        return buf.getvalue()

    def test_title_displayed(self) -> None:
        output = self._capture(_story(), _make_phase_defs())
        assert "AgentTeamOrchestrator" in output
        assert "Story Plan" in output

    def test_all_13_phases_present(self) -> None:
        output = self._capture(_story(), _make_phase_defs())
        phases = [
            "queued",
            "creating",
            "designing",
            "validating",
            "dev_ready",
            "developing",
            "reviewing",
            "fixing",
            "qa_testing",
            "uat",
            "merging",
            "regression",
            "done",
        ]
        for phase in phases:
            assert phase in output, f"Phase '{phase}' missing from output"

    def test_completed_and_current_markers(self) -> None:
        """developing 阶段时，前4个应为 ✔，developing 为 ▶ + '当前'。"""
        output = self._capture(_story(current_phase="developing"), _make_phase_defs())
        assert "✔" in output
        assert "▶" in output
        assert "当前" in output

    def test_no_config_no_type_role(self) -> None:
        """无配置降级时不显示类型/角色信息。"""
        output = self._capture(_story(), [])
        assert "structured_job" not in output
        assert "convergent_loop" not in output
        assert "interactive_session" not in output
        assert "creator" not in output
        # 阶段名仍存在
        assert "developing" in output

    def test_with_config_shows_type_role(self) -> None:
        """有配置时各阶段类型标签正确显示，格式为 (type | role)。"""
        output = self._capture(_story(), _make_phase_defs())
        assert "(structured_job | developer)" in output
        assert "(convergent_loop | validator)" in output
        assert "(interactive_session | developer)" in output

    def test_done_all_checkmarks(self) -> None:
        output = self._capture(_story(status="done", current_phase="done"), [])
        assert output.count("✔") >= 13
        assert "▶" not in output

    def test_blocked_no_progress(self) -> None:
        output = self._capture(_story(status="blocked", current_phase="blocked"), [])
        assert "⚠" in output
        assert "blocked" in output
        assert "✔" not in output
        assert "▶" not in output

    def test_blocked_with_config_preserves_phase_type_colors(self) -> None:
        """blocked + 有配置时，阶段仍显示 (type | role) 格式。"""
        output = self._capture(
            _story(status="blocked", current_phase="blocked"),
            _make_phase_defs(),
        )
        assert "(structured_job | developer)" in output
        assert "(convergent_loop | validator)" in output
        assert "✔" not in output
        assert "▶" not in output
