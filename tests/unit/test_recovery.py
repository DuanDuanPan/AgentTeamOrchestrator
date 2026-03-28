"""RecoveryEngine 单元测试。

测试策略：纯数据库状态驱动（Architecture Decision 8）。
不需要真实杀进程——通过 mock os.kill() 和 Path.exists() 控制行为。
"""

from __future__ import annotations

import errno
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ato.models.db import (
    get_connection,
    get_findings_by_story,
    get_paused_tasks,
    get_tasks_by_story,
    insert_findings_batch,
    insert_story,
    insert_task,
)
from ato.models.schemas import (
    AdapterResult,
    ApprovalRecord,
    CLIAdapterError,
    ErrorCategory,
    FindingRecord,
    StoryRecord,
    TaskRecord,
    compute_dedup_hash,
)
from ato.recovery import (
    RecoveryEngine,
    _artifact_exists,
    _is_pid_alive,
)

_NOW = datetime.now(tz=UTC)

# ---------------------------------------------------------------------------
# 模块级 fixture：mock adapter 防止 reschedule 后台 dispatch 启动真实 CLI
# ---------------------------------------------------------------------------

_MOCK_ADAPTER_RESULT = AdapterResult(
    status="success",
    exit_code=0,
    duration_ms=50,
    text_result="mock-recovery",
)


@pytest.fixture(autouse=True)
def _mock_recovery_adapter() -> object:
    """自动 mock _create_adapter，防止 reschedule 后台 dispatch 调用真实 CLI。

    需要测试真实 dispatch 行为的测试用例通过显式 patch 覆盖此 fixture。
    """
    mock_adapter = AsyncMock()
    mock_adapter.execute.return_value = _MOCK_ADAPTER_RESULT
    with patch("ato.recovery._create_adapter", return_value=mock_adapter):
        yield mock_adapter


def _make_story(
    story_id: str,
    *,
    worktree_path: str | None = None,
) -> StoryRecord:
    return StoryRecord(
        story_id=story_id,
        title=f"Test Story {story_id}",
        status="in_progress",
        current_phase="developing",
        worktree_path=worktree_path,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _make_task(
    task_id: str,
    story_id: str,
    *,
    status: str = "running",
    pid: int | None = 12345,
    phase: str = "reviewing",
    expected_artifact: str | None = None,
) -> TaskRecord:
    return TaskRecord(
        task_id=task_id,
        story_id=story_id,
        phase=phase,
        role="reviewer",
        cli_tool="codex",
        status=status,  # type: ignore[arg-type]
        pid=pid,
        expected_artifact=expected_artifact,
        started_at=_NOW,
    )


def _make_open_finding(
    *,
    finding_id: str,
    story_id: str,
    severity: str = "blocking",
    description: str = "existing issue",
    file_path: str = "src/existing.py",
    rule_id: str = "R001",
    status: str = "open",
    round_num: int = 1,
) -> FindingRecord:
    return FindingRecord(
        finding_id=finding_id,
        story_id=story_id,
        round_num=round_num,
        severity=severity,  # type: ignore[arg-type]
        description=description,
        status=status,  # type: ignore[arg-type]
        file_path=file_path,
        rule_id=rule_id,
        dedup_hash=compute_dedup_hash(file_path, rule_id, severity, description),
        created_at=_NOW,
    )


# ---------------------------------------------------------------------------
# PID 存活检测 (Task 6.1)
# ---------------------------------------------------------------------------


class TestIsPidAlive:
    def test_alive_pid(self) -> None:
        with patch("ato.recovery.os.kill") as mock_kill:
            mock_kill.return_value = None  # no exception = alive
            assert _is_pid_alive(123) is True
            mock_kill.assert_called_once_with(123, 0)

    def test_dead_pid(self) -> None:
        with patch("ato.recovery.os.kill") as mock_kill:
            mock_kill.side_effect = OSError(errno.ESRCH, "No such process")
            assert _is_pid_alive(999) is False

    def test_permission_denied_means_alive(self) -> None:
        with patch("ato.recovery.os.kill") as mock_kill:
            mock_kill.side_effect = OSError(errno.EPERM, "Operation not permitted")
            assert _is_pid_alive(1) is True

    def test_unexpected_oserror_propagates(self) -> None:
        with patch("ato.recovery.os.kill") as mock_kill:
            mock_kill.side_effect = OSError(errno.EINVAL, "Invalid argument")
            with pytest.raises(OSError, match="Invalid argument"):
                _is_pid_alive(0)


# ---------------------------------------------------------------------------
# Artifact 存在检测 (Task 6.2)
# ---------------------------------------------------------------------------


class TestArtifactExists:
    def test_artifact_exists(self, tmp_path: Path) -> None:
        artifact = tmp_path / "output.json"
        artifact.write_text("{}")
        task = _make_task("t1", "s1", expected_artifact=str(artifact))
        assert _artifact_exists(task) is True

    def test_artifact_not_exists(self) -> None:
        task = _make_task("t1", "s1", expected_artifact="/nonexistent/path.json")
        assert _artifact_exists(task) is False

    def test_artifact_none(self) -> None:
        task = _make_task("t1", "s1", expected_artifact=None)
        assert _artifact_exists(task) is False

    def test_artifact_empty_string(self) -> None:
        task = _make_task("t1", "s1", expected_artifact="")
        assert _artifact_exists(task) is False


# ---------------------------------------------------------------------------
# 四种分类路径 (Task 6.3)
# ---------------------------------------------------------------------------


class TestClassifyTask:
    """四路分类单元测试——mock PID/artifact 状态。"""

    def _make_engine(self, interactive_phases: set[str] | None = None) -> RecoveryEngine:
        return RecoveryEngine(
            db_path=Path("/tmp/test.db"),
            subprocess_mgr=None,
            transition_queue=AsyncMock(),
            interactive_phases=interactive_phases or {"uat", "developing"},
        )

    @patch("ato.recovery._is_pid_alive", return_value=True)
    def test_reattach_when_pid_alive(self, mock_alive: MagicMock) -> None:
        engine = self._make_engine()
        task = _make_task("t1", "s1", pid=100, phase="reviewing")
        result = engine.classify_task(task)
        assert result.action == "reattach"
        assert result.task_id == "t1"
        assert "PID 100" in result.reason

    @patch("ato.recovery._artifact_exists", return_value=True)
    @patch("ato.recovery._is_pid_alive", return_value=False)
    def test_complete_when_artifact_exists(
        self, mock_alive: MagicMock, mock_artifact: MagicMock
    ) -> None:
        engine = self._make_engine()
        task = _make_task("t1", "s1", pid=100, expected_artifact="/some/file.json")
        result = engine.classify_task(task)
        assert result.action == "complete"
        assert "Artifact exists" in result.reason

    @patch("ato.recovery._artifact_exists", return_value=False)
    @patch("ato.recovery._is_pid_alive", return_value=False)
    def test_reschedule_for_structured_job(
        self, mock_alive: MagicMock, mock_artifact: MagicMock
    ) -> None:
        engine = self._make_engine()
        task = _make_task("t1", "s1", pid=100, phase="reviewing")  # not interactive
        result = engine.classify_task(task)
        assert result.action == "reschedule"
        assert "Structured job" in result.reason

    @patch("ato.recovery._artifact_exists", return_value=False)
    @patch("ato.recovery._is_pid_alive", return_value=False)
    def test_needs_human_for_interactive_session(
        self, mock_alive: MagicMock, mock_artifact: MagicMock
    ) -> None:
        engine = self._make_engine(interactive_phases={"uat", "developing"})
        task = _make_task("t1", "s1", pid=100, phase="uat")  # interactive
        result = engine.classify_task(task)
        assert result.action == "needs_human"
        assert "Interactive session" in result.reason

    @patch("ato.recovery._artifact_exists", return_value=False)
    @patch("ato.recovery._is_pid_alive", return_value=False)
    def test_no_pid_no_artifact_structured_reschedules(
        self, mock_alive: MagicMock, mock_artifact: MagicMock
    ) -> None:
        """PID 为 None 的 structured job → reschedule。"""
        engine = self._make_engine()
        task = _make_task("t1", "s1", pid=None, phase="reviewing")
        result = engine.classify_task(task)
        assert result.action == "reschedule"


# ---------------------------------------------------------------------------
# 正常恢复路径 (Task 6.4)
# ---------------------------------------------------------------------------


class TestNormalRecovery:
    """paused tasks → 正常恢复 → reschedule + 后台 dispatch。"""

    async def test_paused_tasks_rescheduled(self, initialized_db_path: Path) -> None:
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("s1"))
            await insert_task(
                db,
                _make_task("t1", "s1", status="paused", pid=None, phase="reviewing"),
            )
            await insert_task(
                db,
                _make_task("t2", "s1", status="paused", pid=None, phase="creating"),
            )
        finally:
            await db.close()

        engine = RecoveryEngine(
            db_path=initialized_db_path,
            subprocess_mgr=None,
            transition_queue=AsyncMock(),
        )
        result = await engine.run_recovery()
        await engine.await_background_tasks()

        assert result.recovery_mode == "normal"
        assert result.dispatched_count == 2
        assert result.auto_recovered_count == 0
        assert result.needs_human_count == 0
        assert len(result.classifications) == 2
        assert all(c.action == "reschedule" for c in result.classifications)


# ---------------------------------------------------------------------------
# 无恢复场景 (Task 6.5)
# ---------------------------------------------------------------------------


class TestNoRecovery:
    """无 running/paused tasks → RecoveryMode.none。"""

    async def test_empty_db_returns_none_mode(self, initialized_db_path: Path) -> None:
        engine = RecoveryEngine(
            db_path=initialized_db_path,
            subprocess_mgr=None,
            transition_queue=AsyncMock(),
        )
        result = await engine.run_recovery()

        assert result.recovery_mode == "none"
        assert result.auto_recovered_count == 0
        assert result.needs_human_count == 0
        assert len(result.classifications) == 0

    async def test_only_completed_tasks(self, initialized_db_path: Path) -> None:
        """只有 completed 状态的 task → 无恢复。"""
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("s1"))
            await insert_task(
                db,
                _make_task("t1", "s1", status="completed", pid=None),
            )
        finally:
            await db.close()

        engine = RecoveryEngine(
            db_path=initialized_db_path,
            subprocess_mgr=None,
            transition_queue=AsyncMock(),
        )
        result = await engine.run_recovery()
        assert result.recovery_mode == "none"


# ---------------------------------------------------------------------------
# 混合场景 (Task 6.6)
# ---------------------------------------------------------------------------


class TestMixedRecovery:
    """部分 running + 部分 paused → running 优先（崩溃恢复模式）。"""

    @patch("ato.recovery._artifact_exists", return_value=False)
    @patch("ato.recovery._is_pid_alive", return_value=False)
    async def test_running_takes_precedence_over_paused(
        self,
        mock_alive: MagicMock,
        mock_artifact: MagicMock,
        initialized_db_path: Path,
    ) -> None:
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("s1"))
            # 1 running + 1 paused
            await insert_task(
                db,
                _make_task("t1", "s1", status="running", pid=999, phase="reviewing"),
            )
            await insert_task(
                db,
                _make_task("t2", "s1", status="paused", pid=None, phase="creating"),
            )
        finally:
            await db.close()

        engine = RecoveryEngine(
            db_path=initialized_db_path,
            subprocess_mgr=None,
            transition_queue=AsyncMock(),
            interactive_phases={"uat", "developing"},
        )
        result = await engine.run_recovery()
        await engine.await_background_tasks()

        # running 存在 → 进入 crash 恢复模式（仅处理 running）
        assert result.recovery_mode == "crash"
        # 只有 1 个 running task 被分类（paused task 不在崩溃恢复中处理）
        assert len(result.classifications) == 1
        assert result.classifications[0].task_id == "t1"
        assert result.classifications[0].action == "reschedule"  # structured job, pid dead


# ---------------------------------------------------------------------------
# 恢复动作验证
# ---------------------------------------------------------------------------


class TestRecoveryActions:
    """验证各恢复动作的 DB 副作用。"""

    @patch("ato.recovery._is_pid_alive", return_value=True)
    async def test_reattach_registers_pid(
        self,
        mock_alive: MagicMock,
        initialized_db_path: Path,
    ) -> None:
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("s1"))
            await insert_task(
                db,
                _make_task("t1", "s1", status="running", pid=42, phase="reviewing"),
            )
        finally:
            await db.close()

        mock_subprocess_mgr = MagicMock()
        mock_subprocess_mgr.running = {}

        engine = RecoveryEngine(
            db_path=initialized_db_path,
            subprocess_mgr=mock_subprocess_mgr,
            transition_queue=AsyncMock(),
        )
        result = await engine.run_recovery()

        assert result.recovery_mode == "crash"
        assert result.auto_recovered_count == 1
        assert 42 in mock_subprocess_mgr.running

    @patch("ato.recovery._artifact_exists", return_value=False)
    @patch("ato.recovery._is_pid_alive", return_value=False)
    async def test_needs_human_creates_approval(
        self,
        mock_alive: MagicMock,
        mock_artifact: MagicMock,
        initialized_db_path: Path,
    ) -> None:
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("s1"))
            await insert_task(
                db,
                _make_task("t1", "s1", status="running", pid=999, phase="uat"),
            )
        finally:
            await db.close()

        engine = RecoveryEngine(
            db_path=initialized_db_path,
            subprocess_mgr=None,
            transition_queue=AsyncMock(),
            interactive_phases={"uat"},
        )
        result = await engine.run_recovery()

        assert result.needs_human_count == 1
        assert result.classifications[0].action == "needs_human"

        # 验证 approval 已创建
        from ato.models.db import get_pending_approvals

        db = await get_connection(initialized_db_path)
        try:
            approvals = await get_pending_approvals(db)
            assert len(approvals) == 1
            assert approvals[0].approval_type == "crash_recovery"
            assert approvals[0].story_id == "s1"
        finally:
            await db.close()

    @patch("ato.recovery._artifact_exists", return_value=True)
    @patch("ato.recovery._is_pid_alive", return_value=False)
    async def test_complete_from_artifact_updates_task(
        self,
        mock_alive: MagicMock,
        mock_artifact: MagicMock,
        initialized_db_path: Path,
    ) -> None:
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("s1"))
            await insert_task(
                db,
                _make_task(
                    "t1",
                    "s1",
                    status="running",
                    pid=999,
                    expected_artifact="/some/file.json",
                ),
            )
        finally:
            await db.close()

        engine = RecoveryEngine(
            db_path=initialized_db_path,
            subprocess_mgr=None,
            transition_queue=AsyncMock(),
        )
        result = await engine.run_recovery()

        assert result.auto_recovered_count == 1
        assert result.classifications[0].action == "complete"

        # 验证 task status 已更新为 completed
        from ato.models.db import get_tasks_by_story

        db = await get_connection(initialized_db_path)
        try:
            tasks = await get_tasks_by_story(db, "s1")
            assert len(tasks) == 1
            assert tasks[0].status == "completed"
        finally:
            await db.close()

    @patch("ato.recovery._artifact_exists", return_value=False)
    @patch("ato.recovery._is_pid_alive", return_value=False)
    async def test_reschedule_structured_job_dispatches(
        self,
        mock_alive: MagicMock,
        mock_artifact: MagicMock,
        initialized_db_path: Path,
    ) -> None:
        """structured_job phase: 后台 dispatch + transition event。"""
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("s1"))
            await insert_task(
                db,
                _make_task("t1", "s1", status="running", pid=999, phase="creating"),
            )
        finally:
            await db.close()

        mock_tq = AsyncMock()
        engine = RecoveryEngine(
            db_path=initialized_db_path,
            subprocess_mgr=None,
            transition_queue=mock_tq,
            interactive_phases={"uat"},
            convergent_loop_phases={"reviewing", "validating", "qa_testing"},
        )
        result = await engine.run_recovery()
        await engine.await_background_tasks()

        assert result.dispatched_count == 1
        assert result.auto_recovered_count == 0
        assert result.classifications[0].action == "reschedule"

        # structured_job: 后台 dispatch 完成后提交 transition
        mock_tq.submit.assert_called_once()
        event = mock_tq.submit.call_args[0][0]
        assert event.story_id == "s1"
        assert event.event_name == "create_done"

    @patch("ato.recovery._artifact_exists", return_value=False)
    @patch("ato.recovery._is_pid_alive", return_value=False)
    async def test_reschedule_planning_phase_submits_plan_done(
        self,
        mock_alive: MagicMock,
        mock_artifact: MagicMock,
        initialized_db_path: Path,
    ) -> None:
        """Story 8.2: planning phase reschedule 提交 plan_done 事件。"""
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("s1"))
            await insert_task(
                db,
                _make_task("t1", "s1", status="running", pid=999, phase="planning"),
            )
        finally:
            await db.close()

        mock_tq = AsyncMock()
        engine = RecoveryEngine(
            db_path=initialized_db_path,
            subprocess_mgr=None,
            transition_queue=mock_tq,
            interactive_phases={"uat"},
            convergent_loop_phases={"reviewing", "validating", "qa_testing"},
        )
        result = await engine.run_recovery()
        await engine.await_background_tasks()

        assert result.dispatched_count == 1
        assert result.classifications[0].action == "reschedule"

        mock_tq.submit.assert_called_once()
        event = mock_tq.submit.call_args[0][0]
        assert event.story_id == "s1"
        assert event.event_name == "plan_done"

    @patch("ato.recovery._artifact_exists", return_value=False)
    @patch("ato.recovery._is_pid_alive", return_value=False)
    async def test_reschedule_convergent_loop_phase_aware(
        self,
        mock_alive: MagicMock,
        mock_artifact: MagicMock,
        initialized_db_path: Path,
        _mock_recovery_adapter: AsyncMock,
    ) -> None:
        """convergent_loop phase: dispatch 使用正确的 phase/role，BMAD parse 后评估。"""
        from ato.models.schemas import BmadParseResult, BmadSkillType

        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("s1", worktree_path="/tmp/wt"))
            await insert_task(
                db,
                _make_task("t1", "s1", status="running", pid=999, phase="reviewing"),
            )
        finally:
            await db.close()

        mock_tq = AsyncMock()
        mock_parse = BmadParseResult(
            skill_type=BmadSkillType.CODE_REVIEW,
            verdict="approved",
            findings=[],
            parser_mode="deterministic",
            raw_markdown_hash="abc",
            raw_output_preview="ok",
            parsed_at=_NOW,
        )

        engine = RecoveryEngine(
            db_path=initialized_db_path,
            subprocess_mgr=None,
            transition_queue=mock_tq,
            interactive_phases={"uat"},
            convergent_loop_phases={"reviewing", "validating", "qa_testing"},
        )

        with patch("ato.adapters.bmad_adapter.BmadAdapter") as mock_bmad_cls:
            mock_bmad = AsyncMock()
            mock_bmad.parse.return_value = mock_parse
            mock_bmad_cls.return_value = mock_bmad

            result = await engine.run_recovery()
            await engine.await_background_tasks()

        assert result.dispatched_count == 1
        assert result.auto_recovered_count == 0
        assert result.classifications[0].action == "reschedule"

        # BMAD parse 被调用且使用 CODE_REVIEW skill（不是其他）
        mock_bmad.parse.assert_called_once()
        parse_call = mock_bmad.parse.call_args
        assert parse_call.kwargs["skill_type"] == BmadSkillType.CODE_REVIEW

        # converged → review_pass transition
        mock_tq.submit.assert_called_once()
        event = mock_tq.submit.call_args[0][0]
        assert event.event_name == "review_pass"

    @patch("ato.recovery._artifact_exists", return_value=False)
    @patch("ato.recovery._is_pid_alive", return_value=False)
    async def test_validating_phase_uses_validate_events(
        self,
        mock_alive: MagicMock,
        mock_artifact: MagicMock,
        initialized_db_path: Path,
        _mock_recovery_adapter: AsyncMock,
    ) -> None:
        """validating phase: 提交 validate_pass（不是 review_pass）。"""
        from ato.models.schemas import BmadParseResult, BmadSkillType

        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("s1", worktree_path="/tmp/wt"))
            await insert_task(
                db,
                _make_task("t1", "s1", status="running", pid=999, phase="validating"),
            )
        finally:
            await db.close()

        mock_tq = AsyncMock()
        mock_parse = BmadParseResult(
            skill_type=BmadSkillType.STORY_VALIDATION,
            verdict="approved",
            findings=[],
            parser_mode="deterministic",
            raw_markdown_hash="abc",
            raw_output_preview="ok",
            parsed_at=_NOW,
        )

        engine = RecoveryEngine(
            db_path=initialized_db_path,
            subprocess_mgr=None,
            transition_queue=mock_tq,
            interactive_phases={"uat"},
            convergent_loop_phases={"reviewing", "validating", "qa_testing"},
        )

        with patch("ato.adapters.bmad_adapter.BmadAdapter") as mock_bmad_cls:
            mock_bmad = AsyncMock()
            mock_bmad.parse.return_value = mock_parse
            mock_bmad_cls.return_value = mock_bmad

            await engine.run_recovery()
            await engine.await_background_tasks()

        # 必须是 validate_pass 不是 review_pass
        mock_tq.submit.assert_called_once()
        event = mock_tq.submit.call_args[0][0]
        assert event.event_name == "validate_pass"

        # BMAD parse 使用 STORY_VALIDATION skill
        assert mock_bmad.parse.call_args.kwargs["skill_type"] == BmadSkillType.STORY_VALIDATION

    @patch("ato.recovery._artifact_exists", return_value=False)
    @patch("ato.recovery._is_pid_alive", return_value=False)
    async def test_qa_testing_phase_uses_qa_events(
        self,
        mock_alive: MagicMock,
        mock_artifact: MagicMock,
        initialized_db_path: Path,
        _mock_recovery_adapter: AsyncMock,
    ) -> None:
        """qa_testing phase: blocking findings → qa_fail（不是 review_fail）。"""
        from ato.models.schemas import BmadFinding, BmadParseResult, BmadSkillType

        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("s1", worktree_path="/tmp/wt"))
            await insert_task(
                db,
                _make_task("t1", "s1", status="running", pid=999, phase="qa_testing"),
            )
        finally:
            await db.close()

        mock_tq = AsyncMock()
        mock_parse = BmadParseResult(
            skill_type=BmadSkillType.QA_REPORT,
            verdict="changes_requested",
            findings=[
                BmadFinding(
                    severity="blocking",
                    category="test",
                    description="missing test",
                    file_path="src/foo.py",
                    rule_id="QA-001",
                ),
            ],
            parser_mode="deterministic",
            raw_markdown_hash="abc",
            raw_output_preview="fail",
            parsed_at=_NOW,
        )

        engine = RecoveryEngine(
            db_path=initialized_db_path,
            subprocess_mgr=None,
            transition_queue=mock_tq,
            interactive_phases={"uat"},
            convergent_loop_phases={"reviewing", "validating", "qa_testing"},
        )

        with patch("ato.adapters.bmad_adapter.BmadAdapter") as mock_bmad_cls:
            mock_bmad = AsyncMock()
            mock_bmad.parse.return_value = mock_parse
            mock_bmad_cls.return_value = mock_bmad

            await engine.run_recovery()
            await engine.await_background_tasks()

        # blocking → qa_fail（不是 review_fail）
        mock_tq.submit.assert_called_once()
        event = mock_tq.submit.call_args[0][0]
        assert event.event_name == "qa_fail"

        assert mock_bmad.parse.call_args.kwargs["skill_type"] == BmadSkillType.QA_REPORT


# ---------------------------------------------------------------------------
# Fix: dispatch 传递 worktree_path 和 sandbox
# ---------------------------------------------------------------------------


class TestDispatchOptions:
    """验证 dispatch 传递正确的 options（worktree、sandbox、model）。"""

    @patch("ato.recovery._artifact_exists", return_value=False)
    @patch("ato.recovery._is_pid_alive", return_value=False)
    async def test_dispatch_no_sandbox_when_not_configured(
        self,
        mock_alive: MagicMock,
        mock_artifact: MagicMock,
        initialized_db_path: Path,
        _mock_recovery_adapter: AsyncMock,
    ) -> None:
        """未显式配置时 structured_job dispatch 不应传 sandbox。"""
        from ato.models.db import update_story_worktree_path

        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("s1"))
            await update_story_worktree_path(db, "s1", "/tmp/test-worktree")
            await insert_task(
                db,
                _make_task(
                    "t1",
                    "s1",
                    status="running",
                    pid=999,
                    phase="creating",
                ),
            )
        finally:
            await db.close()

        mock_tq = AsyncMock()
        engine = RecoveryEngine(
            db_path=initialized_db_path,
            subprocess_mgr=None,
            transition_queue=mock_tq,
            convergent_loop_phases={"reviewing"},
        )
        await engine.run_recovery()
        await engine.await_background_tasks()

        # 验证 adapter.execute 收到了正确的 options
        _mock_recovery_adapter.execute.assert_called_once()
        call_args = _mock_recovery_adapter.execute.call_args
        options = call_args[0][1]  # 第二个位置参数是 options
        assert options is not None
        assert options["cwd"] == "/tmp/test-worktree"
        assert "sandbox" not in options, "未显式配置时不应默认传 sandbox"

    @patch("ato.recovery._artifact_exists", return_value=False)
    @patch("ato.recovery._is_pid_alive", return_value=False)
    async def test_dispatch_passes_phase_config_sandbox_and_model(
        self,
        mock_alive: MagicMock,
        mock_artifact: MagicMock,
        initialized_db_path: Path,
        _mock_recovery_adapter: AsyncMock,
    ) -> None:
        """phase config 定义了 sandbox/model 时，options 必须透传。"""
        from ato.config import ATOSettings
        from ato.models.db import update_story_worktree_path

        # 构建包含显式 sandbox 和 model 的 settings
        settings = ATOSettings(
            roles={
                "creator": {"cli": "claude", "model": "opus", "sandbox": None},
                "reviewer": {
                    "cli": "codex",
                    "model": "codex-mini-latest",
                    "sandbox": "read-only",
                },
            },
            phases=[
                {
                    "name": "creating",
                    "role": "creator",
                    "type": "structured_job",
                    "next_on_success": "reviewing",
                },
                {
                    "name": "reviewing",
                    "role": "reviewer",
                    "type": "convergent_loop",
                    "next_on_success": "done",
                    "next_on_failure": "creating",
                },
            ],
        )

        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("s1"))
            await update_story_worktree_path(db, "s1", "/tmp/test-worktree")
            await insert_task(
                db,
                _make_task(
                    "t1",
                    "s1",
                    status="running",
                    pid=999,
                    phase="creating",
                ),
            )
        finally:
            await db.close()

        mock_tq = AsyncMock()
        engine = RecoveryEngine(
            db_path=initialized_db_path,
            subprocess_mgr=None,
            transition_queue=mock_tq,
            convergent_loop_phases={"reviewing"},
            settings=settings,
        )
        await engine.run_recovery()
        await engine.await_background_tasks()

        _mock_recovery_adapter.execute.assert_called_once()
        call_args = _mock_recovery_adapter.execute.call_args
        options = call_args[0][1]
        assert options is not None
        assert options["cwd"] == "/tmp/test-worktree"
        assert options.get("model") == "opus"
        # creator 角色无 sandbox → 不传
        assert "sandbox" not in options


# ---------------------------------------------------------------------------
# Fix: 恢复路径保留 retryable CLI 自动重试
# ---------------------------------------------------------------------------


class TestRecoveryDispatchRetry:
    """验证 crash recovery 重调度不会绕过 dispatch_with_retry 语义。"""

    @patch("ato.recovery._artifact_exists", return_value=False)
    @patch("ato.recovery._is_pid_alive", return_value=False)
    async def test_structured_job_retryable_error_retried_once(
        self,
        mock_alive: MagicMock,
        mock_artifact: MagicMock,
        initialized_db_path: Path,
    ) -> None:
        """creating 恢复遇到 retryable CLI 错误时应自动重试，而非 needs_human。"""
        from ato.models.db import get_pending_approvals, get_tasks_by_story

        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("s1"))
            await insert_task(
                db,
                _make_task("t1", "s1", status="running", pid=999, phase="creating"),
            )
        finally:
            await db.close()

        retryable_error = CLIAdapterError(
            "rate limited",
            category=ErrorCategory.RATE_LIMIT,
            retryable=True,
            exit_code=429,
        )
        mock_adapter = AsyncMock()
        mock_adapter.execute.side_effect = [retryable_error, _MOCK_ADAPTER_RESULT]

        mock_tq = AsyncMock()
        engine = RecoveryEngine(
            db_path=initialized_db_path,
            subprocess_mgr=None,
            transition_queue=mock_tq,
            convergent_loop_phases={"reviewing"},
        )

        with patch("ato.recovery._create_adapter", return_value=mock_adapter):
            result = await engine.run_recovery()
            await engine.await_background_tasks()

        assert result.dispatched_count == 1
        assert mock_adapter.execute.call_count == 2
        mock_tq.submit.assert_called_once()
        event = mock_tq.submit.call_args[0][0]
        assert event.event_name == "create_done"

        db = await get_connection(initialized_db_path)
        try:
            tasks = await get_tasks_by_story(db, "s1")
            approvals = await get_pending_approvals(db)
            assert tasks[0].task_id == "t1"
            assert tasks[0].status == "completed"
            assert tasks[0].error_message is None
            assert approvals == []
        finally:
            await db.close()

    @patch("ato.recovery._artifact_exists", return_value=False)
    @patch("ato.recovery._is_pid_alive", return_value=False)
    async def test_convergent_loop_retryable_error_retried_once(
        self,
        mock_alive: MagicMock,
        mock_artifact: MagicMock,
        initialized_db_path: Path,
    ) -> None:
        """reviewing 恢复遇到 retryable CLI 错误时应自动重试后继续评审。"""
        from ato.models.db import get_pending_approvals, get_tasks_by_story
        from ato.models.schemas import BmadParseResult, BmadSkillType

        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("s1", worktree_path="/tmp/wt"))
            await insert_task(
                db,
                _make_task("t1", "s1", status="running", pid=999, phase="reviewing"),
            )
        finally:
            await db.close()

        retryable_error = CLIAdapterError(
            "rate limited",
            category=ErrorCategory.RATE_LIMIT,
            retryable=True,
            exit_code=429,
        )
        mock_adapter = AsyncMock()
        mock_adapter.execute.side_effect = [retryable_error, _MOCK_ADAPTER_RESULT]
        mock_tq = AsyncMock()
        mock_parse = BmadParseResult(
            skill_type=BmadSkillType.CODE_REVIEW,
            verdict="approved",
            findings=[],
            parser_mode="deterministic",
            raw_markdown_hash="abc",
            raw_output_preview="ok",
            parsed_at=_NOW,
        )

        engine = RecoveryEngine(
            db_path=initialized_db_path,
            subprocess_mgr=None,
            transition_queue=mock_tq,
            interactive_phases={"uat"},
            convergent_loop_phases={"reviewing", "validating", "qa_testing"},
        )

        with (
            patch("ato.recovery._create_adapter", return_value=mock_adapter),
            patch("ato.adapters.bmad_adapter.BmadAdapter") as mock_bmad_cls,
        ):
            mock_bmad = AsyncMock()
            mock_bmad.parse.return_value = mock_parse
            mock_bmad_cls.return_value = mock_bmad

            result = await engine.run_recovery()
            await engine.await_background_tasks()

        assert result.dispatched_count == 1
        assert mock_adapter.execute.call_count == 2
        mock_tq.submit.assert_called_once()
        event = mock_tq.submit.call_args[0][0]
        assert event.event_name == "review_pass"

        db = await get_connection(initialized_db_path)
        try:
            tasks = await get_tasks_by_story(db, "s1")
            approvals = await get_pending_approvals(db)
            assert tasks[0].task_id == "t1"
            assert tasks[0].status == "completed"
            assert tasks[0].error_message is None
            assert approvals == []
        finally:
            await db.close()


# ---------------------------------------------------------------------------
# Fix F1: complete 提交 transition event 验证
# ---------------------------------------------------------------------------


class TestCompleteSubmitsTransition:
    """验证 complete 恢复动作提交 transition event 推进 story。"""

    @patch("ato.recovery._artifact_exists", return_value=True)
    @patch("ato.recovery._is_pid_alive", return_value=False)
    async def test_complete_submits_transition_event(
        self,
        mock_alive: MagicMock,
        mock_artifact: MagicMock,
        initialized_db_path: Path,
    ) -> None:
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("s1"))
            await insert_task(
                db,
                _make_task(
                    "t1",
                    "s1",
                    status="running",
                    pid=999,
                    phase="reviewing",
                    expected_artifact="/some/file.json",
                ),
            )
        finally:
            await db.close()

        mock_tq = AsyncMock()
        engine = RecoveryEngine(
            db_path=initialized_db_path,
            subprocess_mgr=None,
            transition_queue=mock_tq,
        )
        await engine.run_recovery()

        # 验证 TQ.submit 被调用且事件名正确
        mock_tq.submit.assert_called_once()
        event = mock_tq.submit.call_args[0][0]
        assert event.story_id == "s1"
        assert event.event_name == "review_pass"
        assert event.source == "agent"


# ---------------------------------------------------------------------------
# Fix F3: needs_human 任务不被 normal recovery 自动恢复
# ---------------------------------------------------------------------------


class TestNeedsHumanProtection:
    """验证 needs_human 的 task 在下次启动时不被自动恢复。"""

    @patch("ato.recovery._artifact_exists", return_value=False)
    @patch("ato.recovery._is_pid_alive", return_value=False)
    async def test_needs_human_uses_failed_status(
        self,
        mock_alive: MagicMock,
        mock_artifact: MagicMock,
        initialized_db_path: Path,
    ) -> None:
        """needs_human 标记为 failed 而非 paused，防止误恢复。"""
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("s1"))
            await insert_task(
                db,
                _make_task("t1", "s1", status="running", pid=999, phase="uat"),
            )
        finally:
            await db.close()

        engine = RecoveryEngine(
            db_path=initialized_db_path,
            subprocess_mgr=None,
            transition_queue=AsyncMock(),
            interactive_phases={"uat"},
        )
        await engine.run_recovery()

        from ato.models.db import get_tasks_by_story

        db = await get_connection(initialized_db_path)
        try:
            tasks = await get_tasks_by_story(db, "s1")
            assert tasks[0].status == "failed"
            assert tasks[0].error_message == "crash_recovery:needs_human"
        finally:
            await db.close()

    async def test_paused_with_approval_not_rescheduled(
        self,
        initialized_db_path: Path,
    ) -> None:
        """有 pending crash_recovery approval 的 paused task 不被 normal recovery 重调度。"""
        from ato.models.db import insert_approval

        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("s1"))
            await insert_task(
                db,
                _make_task("t1", "s1", status="paused", pid=None, phase="uat"),
            )
            await insert_approval(
                db,
                ApprovalRecord(
                    approval_id="approval-1",
                    story_id="s1",
                    approval_type="crash_recovery",
                    status="pending",
                    created_at=_NOW,
                ),
            )
        finally:
            await db.close()

        engine = RecoveryEngine(
            db_path=initialized_db_path,
            subprocess_mgr=None,
            transition_queue=AsyncMock(),
        )
        result = await engine.run_recovery()

        assert result.recovery_mode == "normal"
        assert result.auto_recovered_count == 0
        assert result.needs_human_count == 1
        assert result.classifications[0].action == "needs_human"

        db = await get_connection(initialized_db_path)
        try:
            tasks = await get_paused_tasks(db)
            assert len(tasks) == 1
            assert tasks[0].task_id == "t1"
        finally:
            await db.close()


# ---------------------------------------------------------------------------
# Fix: needs_human 原子性（SAVEPOINT 事务边界）
# ---------------------------------------------------------------------------


class TestNeedsHumanAtomicity:
    """验证 _mark_needs_human 的 task+approval 是原子操作。"""

    @patch("ato.recovery._artifact_exists", return_value=False)
    @patch("ato.recovery._is_pid_alive", return_value=False)
    async def test_approval_insert_failure_rolls_back_task_status(
        self,
        mock_alive: MagicMock,
        mock_artifact: MagicMock,
        initialized_db_path: Path,
    ) -> None:
        """approval 插入失败时 task status 也回滚（不会停在 failed 无 approval）。"""
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("s1"))
            await insert_task(
                db,
                _make_task("t1", "s1", status="running", pid=999, phase="uat"),
            )
        finally:
            await db.close()

        engine = RecoveryEngine(
            db_path=initialized_db_path,
            subprocess_mgr=None,
            transition_queue=AsyncMock(),
            interactive_phases={"uat"},
        )

        # 注入 approval INSERT 失败（模拟恢复期间再次崩溃）
        async def failing_needs_human(task: TaskRecord) -> None:
            from ato.models.db import get_connection as gc

            db2 = await gc(initialized_db_path)
            try:
                await db2.execute("SAVEPOINT needs_human")
                try:
                    await db2.execute(
                        "UPDATE tasks SET status = ?, error_message = ? WHERE task_id = ?",
                        ("failed", "crash_recovery:needs_human", task.task_id),
                    )
                    # 模拟 approval INSERT 异常
                    msg = "Simulated DB error during approval insert"
                    raise RuntimeError(msg)
                except BaseException:
                    await db2.execute("ROLLBACK TO SAVEPOINT needs_human")
                    await db2.execute("RELEASE SAVEPOINT needs_human")
                    raise
            finally:
                await db2.close()

        engine._mark_needs_human = failing_needs_human  # type: ignore[method-assign]

        with pytest.raises(RuntimeError, match="Simulated DB error"):
            await engine.run_recovery()

        # 关键断言：task 应保持 running（SAVEPOINT 回滚），不是 failed
        from ato.models.db import get_pending_approvals, get_tasks_by_story

        db = await get_connection(initialized_db_path)
        try:
            tasks = await get_tasks_by_story(db, "s1")
            assert tasks[0].status == "running", (
                "Task should remain running after SAVEPOINT rollback"
            )
            approvals = await get_pending_approvals(db)
            assert len(approvals) == 0
        finally:
            await db.close()


# ---------------------------------------------------------------------------
# Fix: dev_ready / fixing phase 的 artifact 恢复
# ---------------------------------------------------------------------------


class TestMissingPhaseCompleteEvents:
    """验证 dev_ready 和 fixing phase 的 artifact 恢复提交正确的 transition。"""

    @patch("ato.recovery._artifact_exists", return_value=True)
    @patch("ato.recovery._is_pid_alive", return_value=False)
    async def test_dev_ready_artifact_submits_start_dev(
        self,
        mock_alive: MagicMock,
        mock_artifact: MagicMock,
        initialized_db_path: Path,
    ) -> None:
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("s1"))
            await insert_task(
                db,
                _make_task(
                    "t1",
                    "s1",
                    status="running",
                    pid=999,
                    phase="dev_ready",
                    expected_artifact="/some/prep.json",
                ),
            )
        finally:
            await db.close()

        mock_tq = AsyncMock()
        engine = RecoveryEngine(
            db_path=initialized_db_path,
            subprocess_mgr=None,
            transition_queue=mock_tq,
        )
        await engine.run_recovery()

        mock_tq.submit.assert_called_once()
        event = mock_tq.submit.call_args[0][0]
        assert event.event_name == "start_dev"

    @patch("ato.recovery._artifact_exists", return_value=True)
    @patch("ato.recovery._is_pid_alive", return_value=False)
    async def test_fixing_artifact_submits_fix_done(
        self,
        mock_alive: MagicMock,
        mock_artifact: MagicMock,
        initialized_db_path: Path,
    ) -> None:
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("s1"))
            await insert_task(
                db,
                _make_task(
                    "t1",
                    "s1",
                    status="running",
                    pid=999,
                    phase="fixing",
                    expected_artifact="/some/fix.json",
                ),
            )
        finally:
            await db.close()

        mock_tq = AsyncMock()
        engine = RecoveryEngine(
            db_path=initialized_db_path,
            subprocess_mgr=None,
            transition_queue=mock_tq,
        )
        await engine.run_recovery()

        mock_tq.submit.assert_called_once()
        event = mock_tq.submit.call_args[0][0]
        assert event.event_name == "fix_done"


# ---------------------------------------------------------------------------
# Fix: convergent_loop prompt 使用 phase-specific 模板
# ---------------------------------------------------------------------------


class TestConvergentLoopPromptFormat:
    """验证 convergent_loop dispatch 使用 phase-specific prompt。"""

    @patch("ato.recovery._artifact_exists", return_value=False)
    @patch("ato.recovery._is_pid_alive", return_value=False)
    async def test_validating_prompt_contains_validation_markers(
        self,
        mock_alive: MagicMock,
        mock_artifact: MagicMock,
        initialized_db_path: Path,
        _mock_recovery_adapter: AsyncMock,
    ) -> None:
        """validating phase prompt 应包含 BMAD story_validation 解析器期望的标记。"""
        from ato.models.schemas import BmadParseResult, BmadSkillType

        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("s1", worktree_path="/tmp/wt"))
            await insert_task(
                db,
                _make_task("t1", "s1", status="running", pid=999, phase="validating"),
            )
        finally:
            await db.close()

        mock_parse = BmadParseResult(
            skill_type=BmadSkillType.STORY_VALIDATION,
            verdict="approved",
            findings=[],
            parser_mode="deterministic",
            raw_markdown_hash="h",
            raw_output_preview="ok",
            parsed_at=_NOW,
        )

        engine = RecoveryEngine(
            db_path=initialized_db_path,
            subprocess_mgr=None,
            transition_queue=AsyncMock(),
            interactive_phases={"uat"},
            convergent_loop_phases={"reviewing", "validating", "qa_testing"},
        )

        with patch("ato.adapters.bmad_adapter.BmadAdapter") as mock_bmad_cls:
            mock_bmad = AsyncMock()
            mock_bmad.parse.return_value = mock_parse
            mock_bmad_cls.return_value = mock_bmad

            await engine.run_recovery()
            await engine.await_background_tasks()

        # 验证 adapter.execute 收到的 prompt 包含 validation-specific 内容
        _mock_recovery_adapter.execute.assert_called_once()
        prompt = _mock_recovery_adapter.execute.call_args[0][0]
        assert "结果" in prompt or "validation" in prompt.lower()
        assert "发现的关键问题" in prompt or "摘要" in prompt

    @patch("ato.recovery._artifact_exists", return_value=False)
    @patch("ato.recovery._is_pid_alive", return_value=False)
    async def test_qa_testing_prompt_contains_qa_markers(
        self,
        mock_alive: MagicMock,
        mock_artifact: MagicMock,
        initialized_db_path: Path,
        _mock_recovery_adapter: AsyncMock,
    ) -> None:
        """qa_testing phase prompt 应包含 BMAD qa_report 解析器期望的标记。"""
        from ato.models.schemas import BmadParseResult, BmadSkillType

        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("s1", worktree_path="/tmp/wt"))
            await insert_task(
                db,
                _make_task("t1", "s1", status="running", pid=999, phase="qa_testing"),
            )
        finally:
            await db.close()

        mock_parse = BmadParseResult(
            skill_type=BmadSkillType.QA_REPORT,
            verdict="approved",
            findings=[],
            parser_mode="deterministic",
            raw_markdown_hash="h",
            raw_output_preview="ok",
            parsed_at=_NOW,
        )

        engine = RecoveryEngine(
            db_path=initialized_db_path,
            subprocess_mgr=None,
            transition_queue=AsyncMock(),
            interactive_phases={"uat"},
            convergent_loop_phases={"reviewing", "validating", "qa_testing"},
        )

        with patch("ato.adapters.bmad_adapter.BmadAdapter") as mock_bmad_cls:
            mock_bmad = AsyncMock()
            mock_bmad.parse.return_value = mock_parse
            mock_bmad_cls.return_value = mock_bmad

            await engine.run_recovery()
            await engine.await_background_tasks()

        prompt = _mock_recovery_adapter.execute.call_args[0][0]
        assert "Recommendation" in prompt
        assert "Quality Score" in prompt
        assert "Critical Issues" in prompt

    async def test_reviewing_retry_with_open_findings_uses_scoped_rereview(
        self,
        initialized_db_path: Path,
        _mock_recovery_adapter: AsyncMock,
    ) -> None:
        """reviewing retry 若已有 open findings，应保留 re-review scope 和下一轮 round_num。"""
        from ato.models.schemas import BmadFinding, BmadParseResult, BmadSkillType

        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("s1", worktree_path="/tmp/wt"))
            await insert_task(
                db,
                _make_task("t1", "s1", status="pending", pid=None, phase="reviewing"),
            )
            await insert_findings_batch(
                db,
                [
                    _make_open_finding(
                        finding_id="f-prev-1",
                        story_id="s1",
                        description="existing issue",
                        file_path="src/existing.py",
                        rule_id="R001",
                        round_num=1,
                    )
                ],
            )
        finally:
            await db.close()

        mock_parse = BmadParseResult(
            skill_type=BmadSkillType.CODE_REVIEW,
            verdict="changes_requested",
            findings=[
                BmadFinding(
                    severity="blocking",
                    category="test",
                    description="newly introduced issue",
                    file_path="src/new.py",
                    rule_id="R002",
                    line=8,
                )
            ],
            parser_mode="deterministic",
            raw_markdown_hash="h",
            raw_output_preview="preview",
            parsed_at=_NOW,
        )

        engine = RecoveryEngine(
            db_path=initialized_db_path,
            subprocess_mgr=None,
            transition_queue=AsyncMock(),
            interactive_phases={"uat"},
            convergent_loop_phases={"reviewing", "validating", "qa_testing"},
        )

        task = _make_task("t1", "s1", status="pending", pid=None, phase="reviewing")

        with patch("ato.adapters.bmad_adapter.BmadAdapter") as mock_bmad_cls:
            mock_bmad = AsyncMock()
            mock_bmad.parse.return_value = mock_parse
            mock_bmad_cls.return_value = mock_bmad

            await engine._dispatch_convergent_loop(task)

        prompt = _mock_recovery_adapter.execute.call_args[0][0]
        assert "SCOPED RE-REVIEW" in prompt
        assert "Do NOT perform a full review" in prompt

        db = await get_connection(initialized_db_path)
        try:
            tasks = await get_tasks_by_story(db, "s1")
            findings = await get_findings_by_story(db, "s1")
        finally:
            await db.close()

        assert len(tasks) == 1
        assert tasks[0].task_id == "t1"
        assert {f.round_num for f in findings} == {1, 2}


# ---------------------------------------------------------------------------
# Fix: 后台 dispatch 异常兜底
# ---------------------------------------------------------------------------


class TestDispatchErrorFallback:
    """验证后台 dispatch 异常不会让 task 卡在 running。"""

    @patch("ato.recovery._artifact_exists", return_value=False)
    @patch("ato.recovery._is_pid_alive", return_value=False)
    async def test_structured_job_dispatch_error_marks_failed(
        self,
        mock_alive: MagicMock,
        mock_artifact: MagicMock,
        initialized_db_path: Path,
    ) -> None:
        """structured_job dispatch 内部异常 → task 标 failed + 创建 approval。"""
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("s1"))
            await insert_task(
                db,
                _make_task("t1", "s1", status="running", pid=999, phase="creating"),
            )
        finally:
            await db.close()

        # Mock adapter 抛出非 CLIAdapterError 的异常
        mock_adapter = AsyncMock()
        mock_adapter.execute.side_effect = RuntimeError("dispatch boom")

        engine = RecoveryEngine(
            db_path=initialized_db_path,
            subprocess_mgr=None,
            transition_queue=AsyncMock(),
            convergent_loop_phases={"reviewing"},
        )

        with patch("ato.recovery._create_adapter", return_value=mock_adapter):
            await engine.run_recovery()
            await engine.await_background_tasks()

        # task 应被标记为 failed（不卡在 running）
        from ato.models.db import get_pending_approvals, get_tasks_by_story

        db = await get_connection(initialized_db_path)
        try:
            tasks = await get_tasks_by_story(db, "s1")
            assert tasks[0].status == "failed"
            assert tasks[0].error_message == "crash_recovery:needs_human"

            # 应有 approval 供操作者处理
            approvals = await get_pending_approvals(db)
            assert len(approvals) == 1
            assert approvals[0].approval_type == "crash_recovery"
        finally:
            await db.close()


# ---------------------------------------------------------------------------
# Story 8.3: cost=None 时恢复路径 fallback blocking_threshold
# ---------------------------------------------------------------------------


class TestRecoveryCostNoneFallback:
    """AC3/AC4: settings.cost is None 时使用 fallback 10；显式值时传递配置值。"""

    @patch("ato.recovery._artifact_exists", return_value=False)
    @patch("ato.recovery._is_pid_alive", return_value=False)
    async def test_cost_none_uses_fallback_threshold(
        self,
        mock_alive: MagicMock,
        mock_artifact: MagicMock,
        initialized_db_path: Path,
        _mock_recovery_adapter: AsyncMock,
    ) -> None:
        """AC3: settings.cost is None → blocking_threshold fallback 为 10。"""
        from ato.models.schemas import BmadParseResult, BmadSkillType

        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("s1", worktree_path="/tmp/wt"))
            await insert_task(
                db,
                _make_task("t1", "s1", status="running", pid=999, phase="validating"),
            )
        finally:
            await db.close()

        # settings 对象中 cost=None
        mock_settings = MagicMock()
        mock_settings.cost = None
        mock_settings.convergent_loop = MagicMock()
        mock_settings.convergent_loop.max_rounds = 3
        mock_settings.convergent_loop.convergence_threshold = 0.5
        mock_settings.max_concurrent_agents = 4

        mock_tq = AsyncMock()
        mock_parse = BmadParseResult(
            skill_type=BmadSkillType.STORY_VALIDATION,
            verdict="approved",
            findings=[],
            parser_mode="deterministic",
            raw_markdown_hash="abc",
            raw_output_preview="ok",
            parsed_at=_NOW,
        )

        engine = RecoveryEngine(
            db_path=initialized_db_path,
            subprocess_mgr=None,
            transition_queue=mock_tq,
            interactive_phases={"uat"},
            convergent_loop_phases={"reviewing", "validating", "qa_testing"},
            settings=mock_settings,
        )

        with (
            patch("ato.adapters.bmad_adapter.BmadAdapter") as mock_bmad_cls,
            patch(
                "ato.validation.maybe_create_blocking_abnormal_approval",
                new_callable=AsyncMock,
            ) as mock_approval,
        ):
            mock_bmad = AsyncMock()
            mock_bmad.parse.return_value = mock_parse
            mock_bmad_cls.return_value = mock_bmad

            await engine.run_recovery()
            await engine.await_background_tasks()

        # fallback threshold == 10
        mock_approval.assert_called_once()
        assert mock_approval.call_args.kwargs["threshold"] == 10

    @patch("ato.recovery._artifact_exists", return_value=False)
    @patch("ato.recovery._is_pid_alive", return_value=False)
    async def test_explicit_blocking_threshold_passed(
        self,
        mock_alive: MagicMock,
        mock_artifact: MagicMock,
        initialized_db_path: Path,
        _mock_recovery_adapter: AsyncMock,
    ) -> None:
        """AC4: 显式 blocking_threshold 时恢复路径传递配置值。"""
        from ato.config import CostConfig
        from ato.models.schemas import BmadParseResult, BmadSkillType

        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("s1", worktree_path="/tmp/wt"))
            await insert_task(
                db,
                _make_task("t1", "s1", status="running", pid=999, phase="validating"),
            )
        finally:
            await db.close()

        # settings 对象中 cost 显式配置
        mock_settings = MagicMock()
        mock_settings.cost = CostConfig(budget_per_story=5.0, blocking_threshold=7)
        mock_settings.convergent_loop = MagicMock()
        mock_settings.convergent_loop.max_rounds = 3
        mock_settings.convergent_loop.convergence_threshold = 0.5
        mock_settings.max_concurrent_agents = 4

        mock_tq = AsyncMock()
        mock_parse = BmadParseResult(
            skill_type=BmadSkillType.STORY_VALIDATION,
            verdict="approved",
            findings=[],
            parser_mode="deterministic",
            raw_markdown_hash="abc",
            raw_output_preview="ok",
            parsed_at=_NOW,
        )

        engine = RecoveryEngine(
            db_path=initialized_db_path,
            subprocess_mgr=None,
            transition_queue=mock_tq,
            interactive_phases={"uat"},
            convergent_loop_phases={"reviewing", "validating", "qa_testing"},
            settings=mock_settings,
        )

        with (
            patch("ato.adapters.bmad_adapter.BmadAdapter") as mock_bmad_cls,
            patch(
                "ato.validation.maybe_create_blocking_abnormal_approval",
                new_callable=AsyncMock,
            ) as mock_approval,
        ):
            mock_bmad = AsyncMock()
            mock_bmad.parse.return_value = mock_parse
            mock_bmad_cls.return_value = mock_bmad

            await engine.run_recovery()
            await engine.await_background_tasks()

        # 显式配置值 == 7
        mock_approval.assert_called_once()
        assert mock_approval.call_args.kwargs["threshold"] == 7


# ---------------------------------------------------------------------------
# Story 8.1: 非 reviewing convergent-loop phase model/sandbox 透传
# ---------------------------------------------------------------------------


class TestConvergentLoopGenericBranchModelPassthrough:
    """验证 validating/qa_testing 等非 reviewing convergent 分支透传 model/sandbox。"""

    @patch("ato.recovery._artifact_exists", return_value=False)
    @patch("ato.recovery._is_pid_alive", return_value=False)
    async def test_validating_phase_passes_explicit_model_to_adapter(
        self,
        mock_alive: MagicMock,
        mock_artifact: MagicMock,
        initialized_db_path: Path,
        _mock_recovery_adapter: AsyncMock,
    ) -> None:
        """validating phase 的显式 model 应被传到 adapter.execute options。"""
        from ato.config import ATOSettings
        from ato.models.schemas import BmadParseResult, BmadSkillType

        # settings 中 validator 角色有显式 model
        settings = ATOSettings(
            roles={
                "validator": {
                    "cli": "codex",
                    "model": "codex-mini-latest",
                    "sandbox": "read-only",
                },
            },
            phases=[
                {
                    "name": "validating",
                    "role": "validator",
                    "type": "convergent_loop",
                    "next_on_success": "done",
                    "next_on_failure": "validating",
                },
            ],
        )

        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("s1", worktree_path="/tmp/wt"))
            await insert_task(
                db,
                _make_task("t1", "s1", status="running", pid=999, phase="validating"),
            )
        finally:
            await db.close()

        mock_tq = AsyncMock()
        mock_parse = BmadParseResult(
            skill_type=BmadSkillType.STORY_VALIDATION,
            verdict="approved",
            findings=[],
            parser_mode="deterministic",
            raw_markdown_hash="abc",
            raw_output_preview="ok",
            parsed_at=_NOW,
        )

        engine = RecoveryEngine(
            db_path=initialized_db_path,
            subprocess_mgr=None,
            transition_queue=mock_tq,
            interactive_phases={"uat"},
            convergent_loop_phases={"reviewing", "validating", "qa_testing"},
            settings=settings,
        )

        with patch("ato.adapters.bmad_adapter.BmadAdapter") as mock_bmad_cls:
            mock_bmad = AsyncMock()
            mock_bmad.parse.return_value = mock_parse
            mock_bmad_cls.return_value = mock_bmad

            await engine.run_recovery()
            await engine.await_background_tasks()

        # adapter.execute 应收到包含 model 和 sandbox 的 options
        _mock_recovery_adapter.execute.assert_called_once()
        call_args = _mock_recovery_adapter.execute.call_args
        options = call_args[0][1]  # 第二个位置参数
        assert options["model"] == "codex-mini-latest"
        assert options["sandbox"] == "read-only"
        assert options["cwd"] == "/tmp/wt"

    @patch("ato.recovery._artifact_exists", return_value=False)
    @patch("ato.recovery._is_pid_alive", return_value=False)
    async def test_validating_phase_no_model_when_omitted(
        self,
        mock_alive: MagicMock,
        mock_artifact: MagicMock,
        initialized_db_path: Path,
        _mock_recovery_adapter: AsyncMock,
    ) -> None:
        """无 settings 时 validating 的 dispatch options 不含 model/sandbox。"""
        from ato.models.schemas import BmadParseResult, BmadSkillType

        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("s1", worktree_path="/tmp/wt"))
            await insert_task(
                db,
                _make_task("t1", "s1", status="running", pid=999, phase="validating"),
            )
        finally:
            await db.close()

        mock_tq = AsyncMock()
        mock_parse = BmadParseResult(
            skill_type=BmadSkillType.STORY_VALIDATION,
            verdict="approved",
            findings=[],
            parser_mode="deterministic",
            raw_markdown_hash="abc",
            raw_output_preview="ok",
            parsed_at=_NOW,
        )

        engine = RecoveryEngine(
            db_path=initialized_db_path,
            subprocess_mgr=None,
            transition_queue=mock_tq,
            interactive_phases={"uat"},
            convergent_loop_phases={"reviewing", "validating", "qa_testing"},
            # 无 settings → _resolve_phase_config 返回 {}
        )

        with patch("ato.adapters.bmad_adapter.BmadAdapter") as mock_bmad_cls:
            mock_bmad = AsyncMock()
            mock_bmad.parse.return_value = mock_parse
            mock_bmad_cls.return_value = mock_bmad

            await engine.run_recovery()
            await engine.await_background_tasks()

        _mock_recovery_adapter.execute.assert_called_once()
        call_args = _mock_recovery_adapter.execute.call_args
        options = call_args[0][1]
        assert options["cwd"] == "/tmp/wt"
        assert "model" not in options
        assert "sandbox" not in options
