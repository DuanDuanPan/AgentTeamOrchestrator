"""test_interactive_session — Interactive Session 单元测试。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ato.models.schemas import ContextBriefing, StoryRecord, TaskRecord

# ---------------------------------------------------------------------------
# Task 1: ContextBriefing 模型测试
# ---------------------------------------------------------------------------

_NOW = datetime.now(tz=UTC)


class TestContextBriefing:
    """ContextBriefing model_validate 与序列化测试。"""

    def test_minimal_valid(self) -> None:
        """最小合法输入应通过验证。"""
        briefing = ContextBriefing.model_validate(
            {
                "story_id": "story-1",
                "phase": "uat",
                "task_type": "uat",
                "artifacts_produced": ["src/main.py"],
                "key_decisions": [],
                "agent_notes": "",
                "created_at": _NOW,
            }
        )
        assert briefing.story_id == "story-1"
        assert briefing.phase == "uat"
        assert briefing.task_type == "uat"
        assert briefing.artifacts_produced == ["src/main.py"]
        assert briefing.key_decisions == []
        assert briefing.agent_notes == ""
        assert briefing.created_at == _NOW

    def test_full_fields(self) -> None:
        """所有字段填满应通过验证。"""
        briefing = ContextBriefing.model_validate(
            {
                "story_id": "story-2",
                "phase": "developing",
                "task_type": "developing",
                "artifacts_produced": ["src/a.py", "src/b.py"],
                "key_decisions": ["使用策略模式", "添加缓存"],
                "agent_notes": "实现完成，所有测试通过",
                "created_at": _NOW,
            }
        )
        assert len(briefing.artifacts_produced) == 2
        assert len(briefing.key_decisions) == 2
        assert briefing.agent_notes == "实现完成，所有测试通过"

    def test_serialization_roundtrip(self) -> None:
        """model_dump_json → model_validate_json 应可逆。"""
        original = ContextBriefing(
            story_id="story-3",
            phase="uat",
            task_type="uat",
            artifacts_produced=["file.py"],
            key_decisions=["决策1"],
            agent_notes="备注",
            created_at=_NOW,
        )
        json_str = original.model_dump_json()
        restored = ContextBriefing.model_validate_json(json_str)
        assert restored == original

    def test_strict_mode_rejects_extra_field(self) -> None:
        """extra="forbid" 应拒绝未声明字段。"""
        with pytest.raises(Exception):  # noqa: B017
            ContextBriefing.model_validate(
                {
                    "story_id": "story-4",
                    "phase": "uat",
                    "task_type": "uat",
                    "artifacts_produced": [],
                    "key_decisions": [],
                    "agent_notes": "",
                    "created_at": _NOW,
                    "extra_field": "should_fail",
                }
            )

    def test_missing_required_field(self) -> None:
        """缺少必填字段应报错。"""
        with pytest.raises(Exception):  # noqa: B017
            ContextBriefing.model_validate(
                {
                    "story_id": "story-5",
                    # missing phase, task_type, etc.
                }
            )

    def test_created_at_required(self) -> None:
        """created_at 是必填字段。"""
        with pytest.raises(Exception):  # noqa: B017
            ContextBriefing.model_validate(
                {
                    "story_id": "story-6",
                    "phase": "uat",
                    "task_type": "uat",
                    "artifacts_produced": [],
                    "key_decisions": [],
                    "agent_notes": "",
                    # missing created_at
                }
            )


# ---------------------------------------------------------------------------
# Task 2: Interactive Session 启动测试
# ---------------------------------------------------------------------------


def _make_story(story_id: str = "story-test") -> StoryRecord:
    return StoryRecord(
        story_id=story_id,
        title="测试 story",
        status="in_progress",
        current_phase="uat",
        worktree_path="/tmp/wt/story-test",
        created_at=_NOW,
        updated_at=_NOW,
    )


class TestClaudeAdapterInteractive:
    """Claude adapter interactive argv builder 测试。"""

    def test_build_interactive_command_basic(self) -> None:
        """基本 interactive 命令构建。"""
        from ato.adapters.claude_cli import build_interactive_command

        cmd = build_interactive_command(prompt="开始 UAT 测试")
        assert cmd[0] == "claude"
        assert "-p" not in cmd
        assert "--print" not in cmd
        assert cmd[-1] == "开始 UAT 测试"
        # interactive 不应有 --output-format json
        assert "--output-format" not in cmd

    def test_build_interactive_command_with_resume(self) -> None:
        """带 --resume 的 interactive 命令构建。"""
        from ato.adapters.claude_cli import build_interactive_command

        cmd = build_interactive_command(prompt="继续 UAT", session_id="sess-abc")
        assert "--resume" in cmd
        idx = cmd.index("--resume")
        assert cmd[idx + 1] == "sess-abc"

    def test_build_interactive_command_no_resume_without_session(self) -> None:
        """无 session_id 时不应有 --resume。"""
        from ato.adapters.claude_cli import build_interactive_command

        cmd = build_interactive_command(prompt="开始新 session")
        assert "--resume" not in cmd


class TestDispatchInteractive:
    """SubprocessManager.dispatch_interactive() 测试。"""

    async def test_dispatch_interactive_registers_task(self, initialized_db_path: Path) -> None:
        """dispatch_interactive 应注册 task 到 DB。"""
        from ato.models.db import get_connection, get_tasks_by_story, insert_story
        from ato.subprocess_mgr import SubprocessManager

        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("story-int-1"))
        finally:
            await db.close()

        mgr = SubprocessManager(
            max_concurrent=4,
            adapter=MagicMock(),
            db_path=initialized_db_path,
        )

        # Mock the terminal launch to avoid actually opening a terminal
        mock_sidecar_path = initialized_db_path.parent / "sessions" / "story-int-1.json"
        mock_sidecar_path.parent.mkdir(parents=True, exist_ok=True)
        sidecar_data = {
            "pid": 12345,
            "started_at": _NOW.isoformat(),
            "base_commit": "abc123",
            "session_id": None,
        }
        mock_sidecar_path.write_text(json.dumps(sidecar_data))

        with patch("ato.subprocess_mgr._launch_terminal_session") as mock_launch:
            mock_launch.return_value = None
            task_id = await mgr.dispatch_interactive(
                story_id="story-int-1",
                phase="uat",
                role="developer",
                prompt="开始 UAT 测试",
                worktree_path=Path("/tmp/wt/story-int-1"),
                base_commit="abc123",
                ato_dir=initialized_db_path.parent,
            )

        assert task_id is not None

        # Verify task was registered in DB
        db = await get_connection(initialized_db_path)
        try:
            tasks = await get_tasks_by_story(db, "story-int-1")
        finally:
            await db.close()
        assert len(tasks) == 1
        assert tasks[0].status == "running"
        assert tasks[0].phase == "uat"
        assert tasks[0].pid == 12345

    async def test_dispatch_interactive_writes_sidecar(self, initialized_db_path: Path) -> None:
        """dispatch_interactive 应写入 sidecar 元数据。"""
        from ato.models.db import get_connection, insert_story
        from ato.subprocess_mgr import SubprocessManager

        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("story-int-2"))
        finally:
            await db.close()

        mgr = SubprocessManager(
            max_concurrent=4,
            adapter=MagicMock(),
            db_path=initialized_db_path,
        )

        ato_dir = initialized_db_path.parent
        sidecar_path = ato_dir / "sessions" / "story-int-2.json"
        sidecar_path.parent.mkdir(parents=True, exist_ok=True)
        sidecar_data = {
            "pid": 99999,
            "started_at": _NOW.isoformat(),
            "base_commit": "def456",
            "session_id": None,
        }
        sidecar_path.write_text(json.dumps(sidecar_data))

        with patch("ato.subprocess_mgr._launch_terminal_session") as mock_launch:
            mock_launch.return_value = None
            await mgr.dispatch_interactive(
                story_id="story-int-2",
                phase="uat",
                role="developer",
                prompt="UAT",
                worktree_path=Path("/tmp/wt/story-int-2"),
                base_commit="def456",
                ato_dir=ato_dir,
            )

        # Verify sidecar exists and has correct data
        assert sidecar_path.exists()
        data = json.loads(sidecar_path.read_text())
        assert data["pid"] == 99999
        assert data["base_commit"] == "def456"


# ---------------------------------------------------------------------------
# Task 3: has_new_commits 测试
# ---------------------------------------------------------------------------


class TestHasNewCommits:
    """WorktreeManager.has_new_commits() 测试。"""

    async def test_has_new_commits_true(self, initialized_db_path: Path) -> None:
        """有新 commit 时应返回 True。"""
        from ato.worktree_mgr import WorktreeManager

        mgr = WorktreeManager(
            project_root=initialized_db_path.parent.parent,
            db_path=initialized_db_path,
        )

        # Mock _run_git to return commit output
        # has_new_commits calls: _run_git("-C", path, "log", ...)
        async def mock_run_git(*args: str) -> tuple[int, str, str]:
            if "log" in args:
                return (0, "abc1234 feat: new feature\ndef5678 fix: bug fix\n", "")
            return (0, "", "")

        mgr._run_git = mock_run_git  # type: ignore[assignment]
        result = await mgr.has_new_commits(
            worktree_path=Path("/tmp/wt/story-1"),
            since_rev="abc123",
        )
        assert result is True

    async def test_has_new_commits_false(self, initialized_db_path: Path) -> None:
        """无新 commit 时应返回 False。"""
        from ato.worktree_mgr import WorktreeManager

        mgr = WorktreeManager(
            project_root=initialized_db_path.parent.parent,
            db_path=initialized_db_path,
        )

        async def mock_run_git(*args: str) -> tuple[int, str, str]:
            if "log" in args:
                return (0, "", "")
            return (0, "", "")

        mgr._run_git = mock_run_git  # type: ignore[assignment]
        result = await mgr.has_new_commits(
            worktree_path=Path("/tmp/wt/story-1"),
            since_rev="abc123",
        )
        assert result is False

    async def test_has_new_commits_git_error(self, initialized_db_path: Path) -> None:
        """git 命令失败时应返回 False。"""
        from ato.worktree_mgr import WorktreeManager

        mgr = WorktreeManager(
            project_root=initialized_db_path.parent.parent,
            db_path=initialized_db_path,
        )

        async def mock_run_git(*args: str) -> tuple[int, str, str]:
            return (128, "", "fatal: bad revision")

        mgr._run_git = mock_run_git  # type: ignore[assignment]
        result = await mgr.has_new_commits(
            worktree_path=Path("/tmp/wt/story-1"),
            since_rev="bad-rev",
        )
        assert result is False


# ---------------------------------------------------------------------------
# Task 5: 超时监控与 Approval 创建测试
# ---------------------------------------------------------------------------


class TestInteractiveTimeoutDetection:
    """_poll_cycle() interactive session 超时检测测试。"""

    async def test_timeout_creates_approval(self, initialized_db_path: Path) -> None:
        """超时的 interactive task 应创建 approval 请求。"""
        from ato.models.db import (
            get_connection,
            get_pending_approvals,
            insert_story,
            insert_task,
        )

        # 设置一个 2 小时前启动的 interactive task
        old_time = datetime(2026, 3, 25, 0, 0, 0, tzinfo=UTC)
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(
                db,
                StoryRecord(
                    story_id="story-timeout",
                    title="timeout test",
                    status="in_progress",
                    current_phase="uat",
                    created_at=_NOW,
                    updated_at=_NOW,
                ),
            )
            await insert_task(
                db,
                TaskRecord(
                    task_id="task-timeout",
                    story_id="story-timeout",
                    phase="uat",
                    role="developer",
                    cli_tool="claude",
                    status="running",
                    pid=12345,
                    started_at=old_time,
                ),
            )
        finally:
            await db.close()

        # Import and call the timeout detection function
        from ato.core import _check_interactive_timeouts

        db = await get_connection(initialized_db_path)
        try:
            await _check_interactive_timeouts(
                db,
                interactive_phases={"uat"},
                timeout_seconds=7200,
            )
            approvals = await get_pending_approvals(db)
        finally:
            await db.close()

        assert len(approvals) == 1
        assert approvals[0].approval_type == "session_timeout"
        assert approvals[0].story_id == "story-timeout"
        payload = json.loads(approvals[0].payload or "{}")
        assert payload["task_id"] == "task-timeout"
        assert "options" in payload

    async def test_no_timeout_for_recent_task(self, initialized_db_path: Path) -> None:
        """未超时的 task 不应创建 approval。"""
        from ato.models.db import (
            get_connection,
            get_pending_approvals,
            insert_story,
            insert_task,
        )

        db = await get_connection(initialized_db_path)
        try:
            await insert_story(
                db,
                StoryRecord(
                    story_id="story-recent",
                    title="recent test",
                    status="in_progress",
                    current_phase="uat",
                    created_at=_NOW,
                    updated_at=_NOW,
                ),
            )
            await insert_task(
                db,
                TaskRecord(
                    task_id="task-recent",
                    story_id="story-recent",
                    phase="uat",
                    role="developer",
                    cli_tool="claude",
                    status="running",
                    pid=12345,
                    started_at=_NOW,  # 刚启动
                ),
            )
        finally:
            await db.close()

        from ato.core import _check_interactive_timeouts

        db = await get_connection(initialized_db_path)
        try:
            await _check_interactive_timeouts(
                db,
                interactive_phases={"uat"},
                timeout_seconds=7200,
            )
            approvals = await get_pending_approvals(db)
        finally:
            await db.close()

        assert len(approvals) == 0

    async def test_completed_task_triggers_transition(self, initialized_db_path: Path) -> None:
        """已完成的 interactive task 应触发 TransitionEvent 提交。"""
        from ato.models.db import (
            get_connection,
            insert_story,
            insert_task,
        )

        db = await get_connection(initialized_db_path)
        try:
            await insert_story(
                db,
                StoryRecord(
                    story_id="story-done",
                    title="done test",
                    status="in_progress",
                    current_phase="uat",
                    created_at=_NOW,
                    updated_at=_NOW,
                ),
            )
            await insert_task(
                db,
                TaskRecord(
                    task_id="task-done",
                    story_id="story-done",
                    phase="uat",
                    role="developer",
                    cli_tool="claude",
                    status="completed",
                    pid=12345,
                    started_at=_NOW,
                    completed_at=_NOW,
                ),
            )
        finally:
            await db.close()

        from ato.core import _detect_completed_interactive_tasks

        # Map phase→event for generating the success event
        phase_event_map = {"uat": "uat_pass", "developing": "dev_done"}

        db = await get_connection(initialized_db_path)
        try:
            events = await _detect_completed_interactive_tasks(
                db,
                interactive_phases={"uat"},
                phase_event_map=phase_event_map,
            )
        finally:
            await db.close()

        assert len(events) == 1
        task_id, ev = events[0]
        assert task_id == "task-done"
        assert ev.story_id == "story-done"
        assert ev.event_name == "uat_pass"
        assert ev.source == "cli"


# ---------------------------------------------------------------------------
# Task 6: Session 续接支持测试
# ---------------------------------------------------------------------------


class TestSessionResume:
    """dispatch_interactive() session 续接测试。"""

    def test_build_interactive_command_with_resume(self) -> None:
        """带 session_id 应生成 --resume 参数。"""
        from ato.adapters.claude_cli import build_interactive_command

        cmd = build_interactive_command(prompt="继续", session_id="sess-xyz")
        assert "--resume" in cmd
        idx = cmd.index("--resume")
        assert cmd[idx + 1] == "sess-xyz"

    def test_build_interactive_command_without_session(self) -> None:
        """无 session_id 应生成 fresh session（无 --resume）。"""
        from ato.adapters.claude_cli import build_interactive_command

        cmd = build_interactive_command(prompt="开始")
        assert "--resume" not in cmd
        assert "-p" not in cmd

    def test_build_interactive_command_empty_session_id(self) -> None:
        """空字符串 session_id 应降级为 fresh session（无 --resume）。"""
        from ato.adapters.claude_cli import build_interactive_command

        cmd = build_interactive_command(prompt="开始", session_id="")
        assert "--resume" not in cmd
        assert "-p" not in cmd

    async def test_dispatch_interactive_with_resume(self, initialized_db_path: Path) -> None:
        """dispatch_interactive 传入 session_id 应在 sidecar 中记录。"""
        from ato.models.db import get_connection, insert_story
        from ato.subprocess_mgr import SubprocessManager

        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("story-resume-1"))
        finally:
            await db.close()

        mgr = SubprocessManager(
            max_concurrent=4,
            adapter=MagicMock(),
            db_path=initialized_db_path,
        )

        ato_dir = initialized_db_path.parent
        sidecar_path = ato_dir / "sessions" / "story-resume-1.json"
        sidecar_path.parent.mkdir(parents=True, exist_ok=True)
        sidecar_data = {
            "pid": 55555,
            "started_at": _NOW.isoformat(),
            "base_commit": "resume-commit",
            "session_id": "sess-existing",
        }
        sidecar_path.write_text(json.dumps(sidecar_data))

        with patch("ato.subprocess_mgr._launch_terminal_session") as mock_launch:
            mock_launch.return_value = None
            task_id = await mgr.dispatch_interactive(
                story_id="story-resume-1",
                phase="uat",
                role="developer",
                prompt="继续 UAT",
                worktree_path=Path("/tmp/wt/story-resume-1"),
                base_commit="resume-commit",
                ato_dir=ato_dir,
                session_id="sess-existing",
            )

        assert task_id is not None
        # Verify _launch_terminal_session was called with resume cmd
        mock_launch.assert_called_once()
        call_args = mock_launch.call_args
        cmd = call_args[0][0]
        assert "--resume" in cmd
        assert "sess-existing" in cmd

    async def test_dispatch_interactive_no_session_fresh_start(
        self, initialized_db_path: Path
    ) -> None:
        """无 session_id 应降级为 fresh session。"""
        from ato.models.db import get_connection, insert_story
        from ato.subprocess_mgr import SubprocessManager

        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("story-fresh-1"))
        finally:
            await db.close()

        mgr = SubprocessManager(
            max_concurrent=4,
            adapter=MagicMock(),
            db_path=initialized_db_path,
        )

        ato_dir = initialized_db_path.parent
        sidecar_path = ato_dir / "sessions" / "story-fresh-1.json"
        sidecar_path.parent.mkdir(parents=True, exist_ok=True)
        sidecar_data = {
            "pid": 66666,
            "started_at": _NOW.isoformat(),
            "base_commit": "fresh-commit",
            "session_id": None,
        }
        sidecar_path.write_text(json.dumps(sidecar_data))

        with patch("ato.subprocess_mgr._launch_terminal_session") as mock_launch:
            mock_launch.return_value = None
            await mgr.dispatch_interactive(
                story_id="story-fresh-1",
                phase="uat",
                role="developer",
                prompt="开始新 session",
                worktree_path=Path("/tmp/wt/story-fresh-1"),
                base_commit="fresh-commit",
                ato_dir=ato_dir,
                # 无 session_id → fresh session
            )

        mock_launch.assert_called_once()
        call_args = mock_launch.call_args
        cmd = call_args[0][0]
        assert "--resume" not in cmd

    async def test_dispatch_interactive_reads_sidecar_session_id(
        self, initialized_db_path: Path
    ) -> None:
        """无显式 session_id 但 sidecar 中有值时应从 sidecar 读取。"""
        from ato.models.db import get_connection, insert_story
        from ato.subprocess_mgr import SubprocessManager

        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("story-sidecar-resume"))
        finally:
            await db.close()

        mgr = SubprocessManager(
            max_concurrent=4,
            adapter=MagicMock(),
            db_path=initialized_db_path,
        )

        ato_dir = initialized_db_path.parent
        sidecar_path = ato_dir / "sessions" / "story-sidecar-resume.json"
        sidecar_path.parent.mkdir(parents=True, exist_ok=True)
        # 已有 sidecar 带 session_id（上次 session 写入的）
        existing_sidecar = {
            "pid": 77777,
            "started_at": _NOW.isoformat(),
            "base_commit": "old-commit",
            "session_id": "sess-from-sidecar",
        }
        sidecar_path.write_text(json.dumps(existing_sidecar))

        with patch("ato.subprocess_mgr._launch_terminal_session") as mock_launch:
            # _launch_terminal_session 会覆盖 sidecar，所以需要在启动后重写
            def side_effect(cmd: list[str], *a: object, **kw: object) -> None:
                sidecar_path.write_text(
                    json.dumps(
                        {
                            "pid": 88888,
                            "started_at": _NOW.isoformat(),
                            "base_commit": "new-commit",
                            "session_id": "sess-from-sidecar",
                        }
                    )
                )

            mock_launch.side_effect = side_effect
            await mgr.dispatch_interactive(
                story_id="story-sidecar-resume",
                phase="uat",
                role="developer",
                prompt="继续上次工作",
                worktree_path=Path("/tmp/wt/story-sidecar-resume"),
                base_commit="new-commit",
                ato_dir=ato_dir,
                # 不传 session_id → 应从 sidecar 读取
            )

        mock_launch.assert_called_once()
        call_args = mock_launch.call_args
        cmd = call_args[0][0]
        # 应从已有 sidecar 读到 session_id 并加 --resume
        assert "--resume" in cmd
        assert "sess-from-sidecar" in cmd


# ---------------------------------------------------------------------------
# Fix 验证测试: 重复 poll 不重复派发
# ---------------------------------------------------------------------------


class TestNoDuplicateTransitions:
    """验证 _detect_completed_interactive_tasks 不会重复派发。"""

    async def test_second_call_returns_empty(self, initialized_db_path: Path) -> None:
        """第二次调用同一 completed task 应返回空。"""
        from ato.core import _detect_completed_interactive_tasks
        from ato.models.db import get_connection, insert_story, insert_task

        db = await get_connection(initialized_db_path)
        try:
            await insert_story(
                db,
                StoryRecord(
                    story_id="story-dedup",
                    title="dedup test",
                    status="in_progress",
                    current_phase="uat",
                    created_at=_NOW,
                    updated_at=_NOW,
                ),
            )
            await insert_task(
                db,
                TaskRecord(
                    task_id="task-dedup",
                    story_id="story-dedup",
                    phase="uat",
                    role="developer",
                    cli_tool="claude",
                    status="completed",
                    pid=12345,
                    started_at=_NOW,
                    completed_at=_NOW,
                ),
            )
        finally:
            await db.close()

        phase_event_map = {"uat": "uat_pass"}

        # 第一次调用
        db = await get_connection(initialized_db_path)
        try:
            events1 = await _detect_completed_interactive_tasks(
                db,
                interactive_phases={"uat"},
                phase_event_map=phase_event_map,
            )
            # 模拟 _poll_cycle: submit 成功后标记已消费
            from ato.models.db import update_task_status

            for task_id, _ev in events1:
                await update_task_status(
                    db, task_id, "completed", expected_artifact="transition_submitted"
                )
        finally:
            await db.close()

        assert len(events1) == 1

        # 第二次调用——应返回空（task 已标记为消费）
        db = await get_connection(initialized_db_path)
        try:
            events2 = await _detect_completed_interactive_tasks(
                db,
                interactive_phases={"uat"},
                phase_event_map=phase_event_map,
            )
        finally:
            await db.close()

        assert len(events2) == 0


class TestNoDuplicateTimeoutApproval:
    """验证 _check_interactive_timeouts 不会重复创建 approval。"""

    async def test_second_call_no_duplicate(self, initialized_db_path: Path) -> None:
        """已有 pending timeout approval 时不应再次创建。"""
        from ato.core import _check_interactive_timeouts
        from ato.models.db import (
            get_connection,
            get_pending_approvals,
            insert_story,
            insert_task,
        )

        old_time = datetime(2026, 3, 25, 0, 0, 0, tzinfo=UTC)
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(
                db,
                StoryRecord(
                    story_id="story-dup-timeout",
                    title="dup timeout",
                    status="in_progress",
                    current_phase="uat",
                    created_at=_NOW,
                    updated_at=_NOW,
                ),
            )
            await insert_task(
                db,
                TaskRecord(
                    task_id="task-dup-timeout",
                    story_id="story-dup-timeout",
                    phase="uat",
                    role="developer",
                    cli_tool="claude",
                    status="running",
                    pid=12345,
                    started_at=old_time,
                ),
            )
        finally:
            await db.close()

        # 第一次调用
        db = await get_connection(initialized_db_path)
        try:
            await _check_interactive_timeouts(db, interactive_phases={"uat"}, timeout_seconds=7200)
            approvals1 = await get_pending_approvals(db)
        finally:
            await db.close()
        assert len(approvals1) == 1

        # 第二次调用——不应创建新 approval
        db = await get_connection(initialized_db_path)
        try:
            await _check_interactive_timeouts(db, interactive_phases={"uat"}, timeout_seconds=7200)
            approvals2 = await get_pending_approvals(db)
        finally:
            await db.close()
        assert len(approvals2) == 1  # 仍然只有 1 条
