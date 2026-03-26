"""test_cli_uat — ato uat CLI 命令单元测试。"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from ato.cli import app
from ato.models.db import (
    get_connection,
    get_story,
    get_tasks_by_story,
    init_db,
    insert_story,
    insert_task,
)
from ato.models.schemas import StoryRecord, TaskRecord

runner = CliRunner()

_NOW = datetime.now(tz=UTC)


def _make_story(
    story_id: str = "story-uat-1",
    phase: str = "uat",
) -> StoryRecord:
    return StoryRecord(
        story_id=story_id,
        title="测试 story",
        status="uat",
        current_phase=phase,
        worktree_path="/tmp/wt/story-uat-1",
        created_at=_NOW,
        updated_at=_NOW,
    )


def _make_task(
    story_id: str = "story-uat-1",
    task_id: str = "task-uat-1",
    phase: str = "uat",
    status: str = "running",
) -> TaskRecord:
    return TaskRecord(
        task_id=task_id,
        story_id=story_id,
        phase=phase,
        role="developer",
        cli_tool="claude",
        status=status,
        pid=12345,
        started_at=_NOW,
    )


def _setup_db(
    tmp_path: Path,
    story_id: str = "story-uat-1",
    phase: str = "uat",
    task_status: str = "running",
) -> Path:
    """初始化 DB 并插入 story + running task。"""
    db_path = tmp_path / ".ato" / "state.db"

    async def _init() -> None:
        await init_db(db_path)
        db = await get_connection(db_path)
        try:
            await insert_story(db, _make_story(story_id, phase))
            await insert_task(db, _make_task(story_id, phase=phase, status=task_status))
        finally:
            await db.close()

    asyncio.run(_init())
    return db_path


def _write_pid_file(ato_dir: Path, pid: int = 99999) -> None:
    """写入 orchestrator PID 文件。"""
    pid_file = ato_dir / "orchestrator.pid"
    pid_file.write_text(str(pid))


# ---------------------------------------------------------------------------
# 参数验证测试
# ---------------------------------------------------------------------------


class TestUatParameterValidation:
    """ato uat 参数验证。"""

    def test_missing_result_option(self, tmp_path: Path) -> None:
        """缺少 --result 选项应报错。"""
        db_path = _setup_db(tmp_path)
        result = runner.invoke(app, ["uat", "story-uat-1", "--db-path", str(db_path)])
        assert result.exit_code != 0

    def test_invalid_result_value(self, tmp_path: Path) -> None:
        """--result 非 pass/fail 应报错。"""
        db_path = _setup_db(tmp_path)
        result = runner.invoke(
            app, ["uat", "story-uat-1", "--result", "maybe", "--db-path", str(db_path)]
        )
        assert result.exit_code != 0
        assert "pass" in result.output or "fail" in result.output

    def test_fail_without_reason(self, tmp_path: Path) -> None:
        """fail 时缺少 --reason 应报错。"""
        db_path = _setup_db(tmp_path)
        result = runner.invoke(
            app, ["uat", "story-uat-1", "--result", "fail", "--db-path", str(db_path)]
        )
        assert result.exit_code != 0
        assert "reason" in result.output.lower() or "reason" in (result.stderr or "").lower()

    def test_db_not_exist(self, tmp_path: Path) -> None:
        """数据库不存在应报错。"""
        fake_db = tmp_path / ".ato" / "state.db"
        result = runner.invoke(
            app, ["uat", "story-1", "--result", "pass", "--db-path", str(fake_db)]
        )
        assert result.exit_code != 0
        assert "不存在" in result.output or "数据库" in result.output


# ---------------------------------------------------------------------------
# story 状态验证
# ---------------------------------------------------------------------------


class TestUatStoryValidation:
    """story 存在性和阶段验证。"""

    def test_story_not_found(self, tmp_path: Path) -> None:
        """story 不存在应报错。"""
        db_path = _setup_db(tmp_path)
        result = runner.invoke(
            app, ["uat", "nonexistent", "--result", "pass", "--db-path", str(db_path)]
        )
        assert result.exit_code != 0
        assert "不存在" in result.output

    def test_story_not_in_uat_phase(self, tmp_path: Path) -> None:
        """story 不在 uat 阶段应报错。"""
        db_path = _setup_db(tmp_path, phase="developing")
        result = runner.invoke(
            app, ["uat", "story-uat-1", "--result", "pass", "--db-path", str(db_path)]
        )
        assert result.exit_code != 0
        assert "UAT" in result.output or "uat" in result.output


# ---------------------------------------------------------------------------
# pass 路径
# ---------------------------------------------------------------------------


class TestUatPass:
    """ato uat --result pass 路径。"""

    @patch("ato.cli._send_nudge_safe")
    def test_pass_marks_task_completed(self, mock_nudge: object, tmp_path: Path) -> None:
        """pass 应标记 task 为 completed。"""
        db_path = _setup_db(tmp_path)
        _write_pid_file(db_path.parent)

        result = runner.invoke(
            app, ["uat", "story-uat-1", "--result", "pass", "--db-path", str(db_path)]
        )
        assert result.exit_code == 0
        assert "通过" in result.output

        # 验证 task 状态
        async def _check() -> None:
            db = await get_connection(db_path)
            try:
                tasks = await get_tasks_by_story(db, "story-uat-1")
                completed = [t for t in tasks if t.status == "completed"]
                assert len(completed) == 1
                assert completed[0].context_briefing is not None
                payload = json.loads(completed[0].context_briefing)
                assert payload["uat_result"] == "pass"
            finally:
                await db.close()

        asyncio.run(_check())

    @patch("ato.cli._send_nudge_safe")
    def test_pass_sends_nudge(self, mock_nudge: object, tmp_path: Path) -> None:
        """pass 路径应发送 nudge。"""
        db_path = _setup_db(tmp_path)
        _write_pid_file(db_path.parent)

        result = runner.invoke(
            app, ["uat", "story-uat-1", "--result", "pass", "--db-path", str(db_path)]
        )
        assert result.exit_code == 0
        mock_nudge.assert_called_once()  # type: ignore[union-attr]

    @patch("ato.cli._send_nudge_safe")
    def test_pass_output_message(self, mock_nudge: object, tmp_path: Path) -> None:
        """pass 路径的输出消息。"""
        db_path = _setup_db(tmp_path)
        result = runner.invoke(
            app, ["uat", "story-uat-1", "--result", "pass", "--db-path", str(db_path)]
        )
        assert result.exit_code == 0
        assert "merge" in result.output.lower() or "通过" in result.output

    def test_pass_no_running_task(self, tmp_path: Path) -> None:
        """没有 running task 时 pass 应报错。"""
        db_path = _setup_db(tmp_path, task_status="completed")
        result = runner.invoke(
            app, ["uat", "story-uat-1", "--result", "pass", "--db-path", str(db_path)]
        )
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# fail 路径
# ---------------------------------------------------------------------------


class TestUatFail:
    """ato uat --result fail 路径。"""

    @patch("ato.cli._send_nudge_safe")
    def test_fail_marks_task_failed(self, mock_nudge: object, tmp_path: Path) -> None:
        """fail 应标记 task 为 failed 并记录 reason。"""
        db_path = _setup_db(tmp_path)

        result = runner.invoke(
            app,
            [
                "uat", "story-uat-1",
                "--result", "fail",
                "--reason", "UI 不符合预期",
                "--db-path", str(db_path),
            ],
        )
        assert result.exit_code == 0
        assert "未通过" in result.output

        # 验证 task 状态
        async def _check() -> None:
            db = await get_connection(db_path)
            try:
                tasks = await get_tasks_by_story(db, "story-uat-1")
                failed = [t for t in tasks if t.status == "failed"]
                assert len(failed) == 1
                assert failed[0].error_message is not None
                assert "UI 不符合预期" in failed[0].error_message
            finally:
                await db.close()

        asyncio.run(_check())

    @patch("ato.cli._send_nudge_safe")
    def test_fail_sets_uat_fail_requested_marker(
        self, mock_nudge: object, tmp_path: Path
    ) -> None:
        """fail 应标记 task expected_artifact='uat_fail_requested'，
        由 Orchestrator 在 _poll_cycle 中检测并执行状态转换。"""
        db_path = _setup_db(tmp_path)

        result = runner.invoke(
            app,
            [
                "uat", "story-uat-1",
                "--result", "fail",
                "--reason", "发现严重 bug",
                "--db-path", str(db_path),
            ],
        )
        assert result.exit_code == 0

        # 验证 task 的 expected_artifact 标记
        async def _check() -> None:
            db = await get_connection(db_path)
            try:
                tasks = await get_tasks_by_story(db, "story-uat-1")
                failed = [t for t in tasks if t.status == "failed"]
                assert len(failed) == 1
                assert failed[0].expected_artifact == "uat_fail_requested"
            finally:
                await db.close()

        asyncio.run(_check())

        # story 仍在 uat 阶段（CLI 不直接做 transition，留给 Orchestrator）
        async def _check_story() -> None:
            db = await get_connection(db_path)
            try:
                story = await get_story(db, "story-uat-1")
                assert story is not None
                assert story.current_phase == "uat"
            finally:
                await db.close()

        asyncio.run(_check_story())

    def test_fail_no_running_task(self, tmp_path: Path) -> None:
        """没有 running task 时 fail 路径应报错。"""
        db_path = _setup_db(tmp_path, task_status="completed")
        result = runner.invoke(
            app,
            [
                "uat", "story-uat-1",
                "--result", "fail",
                "--reason", "理由",
                "--db-path", str(db_path),
            ],
        )
        assert result.exit_code != 0
        assert "运行中" in result.output or "task" in result.output.lower()

    @patch("ato.cli._send_nudge_safe")
    def test_fail_output_includes_reason(
        self, mock_nudge: object, tmp_path: Path
    ) -> None:
        """fail 路径的输出应包含失败原因。"""
        db_path = _setup_db(tmp_path)
        result = runner.invoke(
            app,
            [
                "uat", "story-uat-1",
                "--result", "fail",
                "--reason", "性能不达标",
                "--db-path", str(db_path),
            ],
        )
        assert result.exit_code == 0
        assert "性能不达标" in result.output

    @patch("ato.cli._send_nudge_safe")
    def test_fail_sends_nudge(self, mock_nudge: object, tmp_path: Path) -> None:
        """fail 路径应发送 nudge。"""
        db_path = _setup_db(tmp_path)
        result = runner.invoke(
            app,
            [
                "uat", "story-uat-1",
                "--result", "fail",
                "--reason", "回归问题",
                "--db-path", str(db_path),
            ],
        )
        assert result.exit_code == 0
        mock_nudge.assert_called_once()  # type: ignore[union-attr]
