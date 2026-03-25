"""test_cli_submit — ato submit CLI 命令单元测试。"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

from typer.testing import CliRunner

from ato.cli import app
from ato.models.db import (
    get_connection,
    get_tasks_by_story,
    init_db,
    insert_story,
    insert_task,
)
from ato.models.schemas import StoryRecord, TaskRecord

runner = CliRunner()

_NOW = datetime.now(tz=UTC)


def _make_story(story_id: str = "story-submit-1", phase: str = "uat") -> StoryRecord:
    return StoryRecord(
        story_id=story_id,
        title="测试 story",
        status="in_progress",
        current_phase=phase,
        worktree_path="/tmp/wt/story-submit-1",
        created_at=_NOW,
        updated_at=_NOW,
    )


def _make_task(
    story_id: str = "story-submit-1",
    task_id: str = "task-1",
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
    story_id: str = "story-submit-1",
    phase: str = "uat",
) -> Path:
    """初始化 DB 并插入 story + running task。"""
    db_path = tmp_path / ".ato" / "state.db"

    async def _init() -> None:
        await init_db(db_path)
        db = await get_connection(db_path)
        try:
            await insert_story(db, _make_story(story_id, phase))
            await insert_task(db, _make_task(story_id, phase=phase))
        finally:
            await db.close()

    asyncio.run(_init())
    return db_path


def _write_sidecar(ato_dir: Path, story_id: str, base_commit: str = "abc123") -> None:
    """写入 sidecar 元数据文件。"""
    session_dir = ato_dir / "sessions"
    session_dir.mkdir(parents=True, exist_ok=True)
    sidecar = {
        "pid": 12345,
        "started_at": _NOW.isoformat(),
        "base_commit": base_commit,
        "session_id": None,
    }
    (session_dir / f"{story_id}.json").write_text(json.dumps(sidecar))


def _write_pid_file(ato_dir: Path, pid: int = 99999) -> None:
    """写入 orchestrator PID 文件。"""
    pid_file = ato_dir / "orchestrator.pid"
    pid_file.write_text(str(pid))


def _write_ato_yaml(tmp_path: Path) -> Path:
    """写入最小 ato.yaml 配置（含 uat 为 interactive_session）。"""
    config = {
        "roles": {
            "developer": {"cli": "claude", "model": "sonnet"},
        },
        "phases": [
            {
                "name": "uat",
                "role": "developer",
                "type": "interactive_session",
                "next_on_success": "done",
            },
        ],
    }
    config_path = tmp_path / "ato.yaml"
    # YAML-compatible JSON
    import yaml

    config_path.write_text(yaml.dump(config, allow_unicode=True))
    return config_path


# ---------------------------------------------------------------------------
# 正常路径测试
# ---------------------------------------------------------------------------


class TestSubmitCommand:
    """ato submit 命令测试。"""

    def test_submit_success_with_briefing_file(self, tmp_path: Path) -> None:
        """--briefing-file 正常提交。"""
        db_path = _setup_db(tmp_path)
        ato_dir = tmp_path / ".ato"
        _write_sidecar(ato_dir, "story-submit-1")
        _write_pid_file(ato_dir)
        config_path = _write_ato_yaml(tmp_path)

        # 创建 briefing 文件
        briefing = {
            "story_id": "story-submit-1",
            "phase": "uat",
            "task_type": "uat",
            "artifacts_produced": ["file.py"],
            "key_decisions": ["decision1"],
            "agent_notes": "done",
            "created_at": _NOW.isoformat(),
        }
        briefing_file = tmp_path / "briefing.json"
        briefing_file.write_text(json.dumps(briefing))

        with (
            patch("ato.cli._check_new_commits", new_callable=AsyncMock, return_value=True),
            patch("ato.cli._send_nudge_safe"),
        ):
            result = runner.invoke(
                app,
                [
                    "submit",
                    "story-submit-1",
                    "--db-path",
                    str(db_path),
                    "--briefing-file",
                    str(briefing_file),
                    "--config",
                    str(config_path),
                ],
            )

        assert result.exit_code == 0, result.output

        # Verify task status updated
        async def _check() -> None:
            db = await get_connection(db_path)
            try:
                tasks = await get_tasks_by_story(db, "story-submit-1")
            finally:
                await db.close()
            assert tasks[0].status == "completed"
            assert tasks[0].context_briefing is not None

        asyncio.run(_check())

    def test_submit_story_not_found(self, tmp_path: Path) -> None:
        """story 不存在时应失败。"""
        db_path = tmp_path / ".ato" / "state.db"
        asyncio.run(init_db(db_path))
        config_path = _write_ato_yaml(tmp_path)

        result = runner.invoke(
            app,
            [
                "submit",
                "nonexistent-story",
                "--db-path",
                str(db_path),
                "--config",
                str(config_path),
            ],
        )
        assert result.exit_code == 1

    def test_submit_wrong_phase(self, tmp_path: Path) -> None:
        """story 不在 interactive phase 时应失败。"""
        # Create story in "developing" phase (not interactive_session)
        db_path = tmp_path / ".ato" / "state.db"
        config_path = _write_ato_yaml(tmp_path)

        async def _init() -> None:
            await init_db(db_path)
            db = await get_connection(db_path)
            try:
                await insert_story(
                    db,
                    StoryRecord(
                        story_id="story-wrong-phase",
                        title="wrong phase",
                        status="in_progress",
                        current_phase="developing",
                        created_at=_NOW,
                        updated_at=_NOW,
                    ),
                )
            finally:
                await db.close()

        asyncio.run(_init())

        result = runner.invoke(
            app,
            [
                "submit",
                "story-wrong-phase",
                "--db-path",
                str(db_path),
                "--config",
                str(config_path),
            ],
        )
        assert result.exit_code == 1

    def test_submit_no_new_commits(self, tmp_path: Path) -> None:
        """无新 commit 时应失败。"""
        db_path = _setup_db(tmp_path)
        ato_dir = tmp_path / ".ato"
        _write_sidecar(ato_dir, "story-submit-1")
        config_path = _write_ato_yaml(tmp_path)

        with patch("ato.cli._check_new_commits", new_callable=AsyncMock, return_value=False):
            result = runner.invoke(
                app,
                [
                    "submit",
                    "story-submit-1",
                    "--db-path",
                    str(db_path),
                    "--config",
                    str(config_path),
                ],
            )
        assert result.exit_code == 1

    def test_submit_orchestrator_not_running(self, tmp_path: Path) -> None:
        """Orchestrator 未运行时应跳过 nudge 但仍成功。"""
        db_path = _setup_db(tmp_path)
        ato_dir = tmp_path / ".ato"
        _write_sidecar(ato_dir, "story-submit-1")
        config_path = _write_ato_yaml(tmp_path)
        # 不写 PID 文件 → Orchestrator 未运行

        briefing = {
            "story_id": "story-submit-1",
            "phase": "uat",
            "task_type": "uat",
            "artifacts_produced": [],
            "key_decisions": [],
            "agent_notes": "",
            "created_at": _NOW.isoformat(),
        }
        briefing_file = tmp_path / "briefing.json"
        briefing_file.write_text(json.dumps(briefing))

        with patch("ato.cli._check_new_commits", new_callable=AsyncMock, return_value=True):
            result = runner.invoke(
                app,
                [
                    "submit",
                    "story-submit-1",
                    "--db-path",
                    str(db_path),
                    "--briefing-file",
                    str(briefing_file),
                    "--config",
                    str(config_path),
                ],
            )
        assert result.exit_code == 0, result.output

    def test_submit_interactive_input_extracts_artifacts(self, tmp_path: Path) -> None:
        """交互式输入时应自动提取 artifacts_produced。"""
        db_path = _setup_db(tmp_path)
        ato_dir = tmp_path / ".ato"
        _write_sidecar(ato_dir, "story-submit-1")
        _write_pid_file(ato_dir)
        config_path = _write_ato_yaml(tmp_path)

        with (
            patch("ato.cli._check_new_commits", new_callable=AsyncMock, return_value=True),
            patch(
                "ato.cli._extract_changed_files",
                new_callable=AsyncMock,
                return_value=["src/main.py", "tests/test_main.py"],
            ),
            patch("ato.cli._send_nudge_safe"),
        ):
            result = runner.invoke(
                app,
                [
                    "submit",
                    "story-submit-1",
                    "--db-path",
                    str(db_path),
                    "--config",
                    str(config_path),
                ],
                input="\n\n",  # 两次回车跳过交互式输入
            )

        assert result.exit_code == 0, result.output

        # Verify artifacts_produced was populated
        async def _check() -> None:
            db = await get_connection(db_path)
            try:
                tasks = await get_tasks_by_story(db, "story-submit-1")
            finally:
                await db.close()
            assert tasks[0].context_briefing is not None
            briefing = json.loads(tasks[0].context_briefing)
            assert briefing["artifacts_produced"] == ["src/main.py", "tests/test_main.py"]

        asyncio.run(_check())

    def test_submit_matches_task_by_sidecar_pid(self, tmp_path: Path) -> None:
        """多个 running task 时应用 sidecar PID 精确匹配当前 session 的 task。"""
        db_path = tmp_path / ".ato" / "state.db"
        config_path = _write_ato_yaml(tmp_path)

        # 插入 story + 两个 running task（不同 PID）
        async def _init() -> None:
            await init_db(db_path)
            db = await get_connection(db_path)
            try:
                await insert_story(
                    db,
                    StoryRecord(
                        story_id="story-multi",
                        title="multi task",
                        status="in_progress",
                        current_phase="uat",
                        worktree_path="/tmp/wt/story-multi",
                        created_at=_NOW,
                        updated_at=_NOW,
                    ),
                )
                # 旧 task（PID 11111）
                await insert_task(
                    db,
                    TaskRecord(
                        task_id="task-old",
                        story_id="story-multi",
                        phase="uat",
                        role="developer",
                        cli_tool="claude",
                        status="running",
                        pid=11111,
                        started_at=_NOW,
                    ),
                )
                # 当前 task（PID 22222）——sidecar 中的 PID
                await insert_task(
                    db,
                    TaskRecord(
                        task_id="task-current",
                        story_id="story-multi",
                        phase="uat",
                        role="developer",
                        cli_tool="claude",
                        status="running",
                        pid=22222,
                        started_at=_NOW,
                    ),
                )
            finally:
                await db.close()

        asyncio.run(_init())

        ato_dir = tmp_path / ".ato"
        # sidecar 中 PID 是 22222
        session_dir = ato_dir / "sessions"
        session_dir.mkdir(parents=True, exist_ok=True)
        sidecar = {
            "pid": 22222,
            "started_at": _NOW.isoformat(),
            "base_commit": "abc",
            "session_id": None,
        }
        (session_dir / "story-multi.json").write_text(json.dumps(sidecar))

        _write_pid_file(ato_dir)

        briefing = {
            "story_id": "story-multi",
            "phase": "uat",
            "task_type": "uat",
            "artifacts_produced": [],
            "key_decisions": [],
            "agent_notes": "",
            "created_at": _NOW.isoformat(),
        }
        bf = tmp_path / "briefing.json"
        bf.write_text(json.dumps(briefing))

        with (
            patch("ato.cli._check_new_commits", new_callable=AsyncMock, return_value=True),
            patch("ato.cli._send_nudge_safe"),
        ):
            result = runner.invoke(
                app,
                [
                    "submit",
                    "story-multi",
                    "--db-path",
                    str(db_path),
                    "--briefing-file",
                    str(bf),
                    "--config",
                    str(config_path),
                ],
            )

        assert result.exit_code == 0, result.output

        # 验证：PID 22222 的 task-current 被标记完成，PID 11111 的 task-old 仍为 running
        async def _verify() -> None:
            db = await get_connection(db_path)
            try:
                tasks = await get_tasks_by_story(db, "story-multi")
            finally:
                await db.close()
            by_id = {t.task_id: t for t in tasks}
            assert by_id["task-current"].status == "completed"
            assert by_id["task-old"].status == "running"

        asyncio.run(_verify())

    def test_submit_rejects_mismatched_briefing_story_id(self, tmp_path: Path) -> None:
        """briefing 的 story_id 与目标 story 不一致时应拒绝。"""
        db_path = _setup_db(tmp_path)
        ato_dir = tmp_path / ".ato"
        _write_sidecar(ato_dir, "story-submit-1")
        config_path = _write_ato_yaml(tmp_path)

        briefing = {
            "story_id": "wrong-story-id",
            "phase": "uat",
            "task_type": "uat",
            "artifacts_produced": [],
            "key_decisions": [],
            "agent_notes": "",
            "created_at": _NOW.isoformat(),
        }
        bf = tmp_path / "briefing.json"
        bf.write_text(json.dumps(briefing))

        with patch("ato.cli._check_new_commits", new_callable=AsyncMock, return_value=True):
            result = runner.invoke(
                app,
                [
                    "submit",
                    "story-submit-1",
                    "--db-path",
                    str(db_path),
                    "--briefing-file",
                    str(bf),
                    "--config",
                    str(config_path),
                ],
            )
        assert result.exit_code == 1
        assert "不匹配" in result.output or "mismatch" in result.output.lower()

    def test_submit_rejects_mismatched_briefing_task_type(self, tmp_path: Path) -> None:
        """briefing 的 task_type 与当前 phase 不一致时应拒绝。"""
        db_path = _setup_db(tmp_path)
        ato_dir = tmp_path / ".ato"
        _write_sidecar(ato_dir, "story-submit-1")
        config_path = _write_ato_yaml(tmp_path)

        briefing = {
            "story_id": "story-submit-1",
            "phase": "uat",
            "task_type": "developing",  # 与当前 phase "uat" 不匹配
            "artifacts_produced": [],
            "key_decisions": [],
            "agent_notes": "",
            "created_at": _NOW.isoformat(),
        }
        bf = tmp_path / "briefing.json"
        bf.write_text(json.dumps(briefing))

        with patch("ato.cli._check_new_commits", new_callable=AsyncMock, return_value=True):
            result = runner.invoke(
                app,
                [
                    "submit",
                    "story-submit-1",
                    "--db-path",
                    str(db_path),
                    "--briefing-file",
                    str(bf),
                    "--config",
                    str(config_path),
                ],
            )
        assert result.exit_code == 1
        assert "task_type" in result.output

    def test_submit_errors_on_multi_task_pid_mismatch(self, tmp_path: Path) -> None:
        """多个 running task + PID 不匹配时应报错而非静默 fallback。"""
        db_path = tmp_path / ".ato" / "state.db"
        config_path = _write_ato_yaml(tmp_path)

        async def _init() -> None:
            await init_db(db_path)
            db = await get_connection(db_path)
            try:
                await insert_story(
                    db,
                    StoryRecord(
                        story_id="story-ambig",
                        title="ambiguous",
                        status="in_progress",
                        current_phase="uat",
                        worktree_path="/tmp/wt/story-ambig",
                        created_at=_NOW,
                        updated_at=_NOW,
                    ),
                )
                for tid, pid in [("t1", 11111), ("t2", 22222)]:
                    await insert_task(
                        db,
                        TaskRecord(
                            task_id=tid,
                            story_id="story-ambig",
                            phase="uat",
                            role="developer",
                            cli_tool="claude",
                            status="running",
                            pid=pid,
                            started_at=_NOW,
                        ),
                    )
            finally:
                await db.close()

        asyncio.run(_init())

        ato_dir = tmp_path / ".ato"
        # sidecar PID 99999 不匹配任何 task
        session_dir = ato_dir / "sessions"
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "story-ambig.json").write_text(
            json.dumps(
                {
                    "pid": 99999,
                    "started_at": _NOW.isoformat(),
                    "base_commit": "x",
                    "session_id": None,
                }
            )
        )
        _write_pid_file(ato_dir)

        briefing = {
            "story_id": "story-ambig",
            "phase": "uat",
            "task_type": "uat",
            "artifacts_produced": [],
            "key_decisions": [],
            "agent_notes": "",
            "created_at": _NOW.isoformat(),
        }
        bf = tmp_path / "briefing.json"
        bf.write_text(json.dumps(briefing))

        with (
            patch("ato.cli._check_new_commits", new_callable=AsyncMock, return_value=True),
            patch("ato.cli._send_nudge_safe"),
        ):
            result = runner.invoke(
                app,
                [
                    "submit",
                    "story-ambig",
                    "--db-path",
                    str(db_path),
                    "--briefing-file",
                    str(bf),
                    "--config",
                    str(config_path),
                ],
            )
        assert result.exit_code == 1
        assert "99999" in result.output  # 应提示 sidecar PID
