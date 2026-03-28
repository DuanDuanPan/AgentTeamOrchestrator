"""崩溃恢复集成测试。

端到端测试：构造崩溃前数据库状态 → 运行 RecoveryEngine → 验证恢复结果。
纯数据库状态驱动，不杀真实进程（Architecture Decision 8）。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import structlog

from ato.models.db import (
    get_connection,
    get_paused_tasks,
    get_pending_approvals,
    get_running_tasks,
    get_tasks_by_story,
    insert_story,
    insert_task,
)
from ato.models.schemas import AdapterResult, StoryRecord, TaskRecord
from ato.recovery import RecoveryEngine

_NOW = datetime.now(tz=UTC)

# ---------------------------------------------------------------------------
# autouse fixture: mock adapter 防止后台 dispatch 启动真实 CLI
# ---------------------------------------------------------------------------

_MOCK_RESULT = AdapterResult(
    status="success",
    exit_code=0,
    duration_ms=50,
    text_result="mock",
)


@pytest.fixture(autouse=True)
def _mock_adapter() -> object:
    mock = AsyncMock()
    mock.execute.return_value = _MOCK_RESULT
    with patch("ato.recovery._create_adapter", return_value=mock):
        yield mock


def _make_story(story_id: str, phase: str = "developing") -> StoryRecord:
    return StoryRecord(
        story_id=story_id,
        title=f"Test Story {story_id}",
        status="in_progress",
        current_phase=phase,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _make_running_task(
    task_id: str,
    story_id: str,
    *,
    pid: int = 12345,
    phase: str = "reviewing",
    expected_artifact: str | None = None,
) -> TaskRecord:
    return TaskRecord(
        task_id=task_id,
        story_id=story_id,
        phase=phase,
        role="reviewer",
        cli_tool="codex",
        status="running",
        pid=pid,
        expected_artifact=expected_artifact,
        started_at=_NOW,
    )


def _make_paused_task(
    task_id: str,
    story_id: str,
    *,
    phase: str = "reviewing",
) -> TaskRecord:
    return TaskRecord(
        task_id=task_id,
        story_id=story_id,
        phase=phase,
        role="reviewer",
        cli_tool="codex",
        status="paused",
        started_at=_NOW,
    )


# ---------------------------------------------------------------------------
# AC1: SQLite WAL 数据完好 (7.1)
# ---------------------------------------------------------------------------


class TestCrashRecoveryDatabase:
    """构造崩溃前数据库状态，验证 RecoveryEngine 正确分类。"""

    @patch("ato.recovery._artifact_exists", return_value=False)
    @patch("ato.recovery._is_pid_alive", return_value=False)
    async def test_crash_with_dead_pids(
        self,
        mock_alive: MagicMock,
        mock_artifact: MagicMock,
        initialized_db_path: Path,
    ) -> None:
        """崩溃前数据库有 status=running tasks，PID 不存在 → reschedule。"""
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("s1"))
            await insert_task(db, _make_running_task("t1", "s1", pid=99999))
            await insert_task(db, _make_running_task("t2", "s1", pid=99998, phase="creating"))
        finally:
            await db.close()

        engine = RecoveryEngine(
            db_path=initialized_db_path,
            subprocess_mgr=None,
            transition_queue=AsyncMock(),
            interactive_phases={"uat"},
            convergent_loop_phases={"reviewing"},
        )
        result = await engine.run_recovery()
        await engine.await_background_tasks()

        assert result.recovery_mode == "crash"
        assert result.dispatched_count == 2
        assert result.auto_recovered_count == 0
        assert result.needs_human_count == 0
        assert all(c.action == "reschedule" for c in result.classifications)


# ---------------------------------------------------------------------------
# AC2-5: 四种恢复场景端到端 (7.2)
# ---------------------------------------------------------------------------


class TestFourRecoveryScenarios:
    """四种恢复路径端到端集成测试。"""

    @patch("ato.recovery._is_pid_alive", return_value=True)
    async def test_reattach_pid_alive(
        self,
        mock_alive: MagicMock,
        initialized_db_path: Path,
    ) -> None:
        """AC2: PID 仍存活 → reattach。"""
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("s1"))
            await insert_task(db, _make_running_task("t1", "s1", pid=42))
        finally:
            await db.close()

        mock_mgr = MagicMock()
        mock_mgr.running = {}

        engine = RecoveryEngine(
            db_path=initialized_db_path,
            subprocess_mgr=mock_mgr,
            transition_queue=AsyncMock(),
        )
        result = await engine.run_recovery()

        assert result.recovery_mode == "crash"
        assert result.classifications[0].action == "reattach"
        assert 42 in mock_mgr.running

        # DB 中 task 仍为 running（reattach 不改变状态）
        db = await get_connection(initialized_db_path)
        try:
            tasks = await get_tasks_by_story(db, "s1")
            assert tasks[0].status == "running"
        finally:
            await db.close()

    @patch("ato.recovery._artifact_exists", return_value=True)
    @patch("ato.recovery._is_pid_alive", return_value=False)
    async def test_complete_artifact_exists(
        self,
        mock_alive: MagicMock,
        mock_artifact: MagicMock,
        initialized_db_path: Path,
    ) -> None:
        """AC3: PID 不存活但 artifact 存在 → complete。"""
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("s1"))
            await insert_task(
                db,
                _make_running_task("t1", "s1", pid=99999, expected_artifact="/tmp/output.json"),
            )
        finally:
            await db.close()

        engine = RecoveryEngine(
            db_path=initialized_db_path,
            subprocess_mgr=None,
            transition_queue=AsyncMock(),
        )
        result = await engine.run_recovery()

        assert result.classifications[0].action == "complete"

        # DB 中 task 已标记为 completed
        db = await get_connection(initialized_db_path)
        try:
            tasks = await get_tasks_by_story(db, "s1")
            assert tasks[0].status == "completed"
            assert tasks[0].completed_at is not None
        finally:
            await db.close()

    @patch("ato.recovery._artifact_exists", return_value=False)
    @patch("ato.recovery._is_pid_alive", return_value=False)
    async def test_reschedule_structured_job(
        self,
        mock_alive: MagicMock,
        mock_artifact: MagicMock,
        initialized_db_path: Path,
    ) -> None:
        """AC4: Structured Job 无 artifact → reschedule → 后台 dispatch + transition。"""
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("s1"))
            # 使用 creating（structured_job），不是 reviewing（convergent_loop）
            await insert_task(db, _make_running_task("t1", "s1", pid=99999, phase="creating"))
        finally:
            await db.close()

        mock_tq = AsyncMock()
        engine = RecoveryEngine(
            db_path=initialized_db_path,
            subprocess_mgr=None,
            transition_queue=mock_tq,
            interactive_phases={"uat", "developing"},
            convergent_loop_phases={"reviewing", "validating", "qa_testing"},
        )
        result = await engine.run_recovery()
        await engine.await_background_tasks()

        assert result.classifications[0].action == "reschedule"

        # structured_job: 后台 dispatch 完成后提交 transition
        mock_tq.submit.assert_called_once()
        event = mock_tq.submit.call_args[0][0]
        assert event.event_name == "create_done"
        assert event.story_id == "s1"

    @patch("ato.recovery._artifact_exists", return_value=False)
    @patch("ato.recovery._is_pid_alive", return_value=False)
    async def test_reschedule_planning_phase_submits_plan_done(
        self,
        mock_alive: MagicMock,
        mock_artifact: MagicMock,
        initialized_db_path: Path,
    ) -> None:
        """Story 8.2: planning phase（首阶段）reschedule 提交 plan_done 事件。"""
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("s1"))
            await insert_task(db, _make_running_task("t1", "s1", pid=99999, phase="planning"))
        finally:
            await db.close()

        mock_tq = AsyncMock()
        engine = RecoveryEngine(
            db_path=initialized_db_path,
            subprocess_mgr=None,
            transition_queue=mock_tq,
            interactive_phases={"uat", "developing"},
            convergent_loop_phases={"reviewing", "validating", "qa_testing"},
        )
        result = await engine.run_recovery()
        await engine.await_background_tasks()

        assert result.classifications[0].action == "reschedule"

        mock_tq.submit.assert_called_once()
        event = mock_tq.submit.call_args[0][0]
        assert event.event_name == "plan_done"
        assert event.story_id == "s1"

    @patch("ato.recovery._artifact_exists", return_value=False)
    @patch("ato.recovery._is_pid_alive", return_value=False)
    async def test_reschedule_convergent_loop_phase_aware(
        self,
        mock_alive: MagicMock,
        mock_artifact: MagicMock,
        initialized_db_path: Path,
    ) -> None:
        """convergent_loop phase: dispatch + BMAD parse + 正确 transition event。"""
        from ato.models.schemas import BmadParseResult, BmadSkillType

        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("s1", phase="reviewing"))
            from ato.models.db import update_story_worktree_path

            await update_story_worktree_path(db, "s1", "/tmp/wt")
            await insert_task(db, _make_running_task("t1", "s1", pid=99999, phase="reviewing"))
        finally:
            await db.close()

        mock_tq = AsyncMock()
        mock_parse = BmadParseResult(
            skill_type=BmadSkillType.CODE_REVIEW,
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
            transition_queue=mock_tq,
            interactive_phases={"uat", "developing"},
            convergent_loop_phases={"reviewing", "validating", "qa_testing"},
        )

        with patch("ato.adapters.bmad_adapter.BmadAdapter") as mock_bmad_cls:
            mock_bmad = AsyncMock()
            mock_bmad.parse.return_value = mock_parse
            mock_bmad_cls.return_value = mock_bmad

            result = await engine.run_recovery()
            await engine.await_background_tasks()

        assert result.classifications[0].action == "reschedule"
        assert result.dispatched_count == 1
        # BMAD parse + review_pass transition
        mock_bmad.parse.assert_called_once()
        mock_tq.submit.assert_called_once()
        assert mock_tq.submit.call_args[0][0].event_name == "review_pass"

    @patch("ato.recovery._artifact_exists", return_value=False)
    @patch("ato.recovery._is_pid_alive", return_value=False)
    async def test_needs_human_interactive_session(
        self,
        mock_alive: MagicMock,
        mock_artifact: MagicMock,
        initialized_db_path: Path,
    ) -> None:
        """AC5: Interactive Session PID 不存活 → needs_human + approval。"""
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("s1", phase="uat"))
            await insert_task(db, _make_running_task("t1", "s1", pid=99999, phase="uat"))
        finally:
            await db.close()

        engine = RecoveryEngine(
            db_path=initialized_db_path,
            subprocess_mgr=None,
            transition_queue=AsyncMock(),
            interactive_phases={"uat", "developing"},
        )
        result = await engine.run_recovery()

        assert result.classifications[0].action == "needs_human"
        assert result.needs_human_count == 1

        # 验证 approval 创建
        db = await get_connection(initialized_db_path)
        try:
            approvals = await get_pending_approvals(db)
            assert len(approvals) == 1
            assert approvals[0].approval_type == "crash_recovery"
            payload = json.loads(approvals[0].payload or "{}")
            assert payload["task_id"] == "t1"
            assert "restart" in payload["options"]

            # task 应被标记为 failed（防止 normal recovery 误恢复）
            tasks = await get_tasks_by_story(db, "s1")
            assert tasks[0].status == "failed"
        finally:
            await db.close()


# ---------------------------------------------------------------------------
# AC6: 正常重启（非崩溃）端到端 (7.3)
# ---------------------------------------------------------------------------


class TestNormalRestart:
    """ato stop 后正常重启路径。"""

    async def test_paused_tasks_normal_recovery(self, initialized_db_path: Path) -> None:
        """AC6: paused tasks → 正常恢复 → pending。"""
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("s1"))
            await insert_task(db, _make_paused_task("t1", "s1", phase="reviewing"))
            await insert_task(db, _make_paused_task("t2", "s1", phase="creating"))
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
        assert all(c.action == "reschedule" for c in result.classifications)
        assert all(
            "paused" in c.reason.lower() or "normal" in c.reason.lower()
            for c in result.classifications
        )

        # 验证 DB：all tasks → pending
        db = await get_connection(initialized_db_path)
        try:
            paused = await get_paused_tasks(db)
            assert len(paused) == 0
            running = await get_running_tasks(db)
            assert len(running) == 0
        finally:
            await db.close()


# ---------------------------------------------------------------------------
# AC7: structlog 输出验证 (7.4)
# ---------------------------------------------------------------------------


class TestRecoveryLogging:
    """验证 structlog 输出包含 recovery_action 字段。"""

    @patch("ato.recovery._artifact_exists", return_value=False)
    @patch("ato.recovery._is_pid_alive", return_value=False)
    async def test_structlog_contains_recovery_fields(
        self,
        mock_alive: MagicMock,
        mock_artifact: MagicMock,
        initialized_db_path: Path,
    ) -> None:
        """验证恢复日志包含 recovery_action 和 recovery_mode 字段。"""
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("s1"))
            await insert_task(db, _make_running_task("t1", "s1", pid=99999, phase="reviewing"))
        finally:
            await db.close()

        # 捕获 structlog 输出
        captured_events: list[dict[str, object]] = []

        def capture_event(
            _logger: structlog.types.WrappedLogger,
            method_name: str,
            event_dict: dict[str, object],
        ) -> dict[str, object]:
            captured_events.append(event_dict.copy())
            return event_dict

        # 临时添加 processor 来捕获日志
        original_config = structlog.get_config()
        processors = list(original_config.get("processors", []))
        processors.insert(0, capture_event)
        structlog.configure(processors=processors)

        try:
            engine = RecoveryEngine(
                db_path=initialized_db_path,
                subprocess_mgr=None,
                transition_queue=AsyncMock(),
                interactive_phases={"uat"},
            )
            await engine.run_recovery()
        finally:
            # 恢复原始配置
            structlog.configure(**original_config)

        # 验证 recovery_task_classified 事件包含 recovery_action 字段
        classify_events = [
            e for e in captured_events if e.get("event") == "recovery_task_classified"
        ]
        assert len(classify_events) >= 1
        for ev in classify_events:
            assert "recovery_action" in ev
            assert "task_id" in ev
            assert "story_id" in ev
            assert "pid" in ev
            assert "phase" in ev

        # 验证 recovery_complete 事件包含 recovery_mode 字段
        complete_events = [e for e in captured_events if e.get("event") == "recovery_complete"]
        assert len(complete_events) == 1
        assert complete_events[0]["recovery_mode"] == "crash"
        assert "auto_recovered" in complete_events[0]
        assert "dispatched" in complete_events[0]
        assert "needs_human" in complete_events[0]
        assert "duration_ms" in complete_events[0]

    async def test_normal_recovery_log_mode(self, initialized_db_path: Path) -> None:
        """验证正常恢复输出 recovery_mode='normal'。"""
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("s1"))
            await insert_task(db, _make_paused_task("t1", "s1"))
        finally:
            await db.close()

        captured_events: list[dict[str, object]] = []

        def capture_event(
            _logger: structlog.types.WrappedLogger,
            method_name: str,
            event_dict: dict[str, object],
        ) -> dict[str, object]:
            captured_events.append(event_dict.copy())
            return event_dict

        original_config = structlog.get_config()
        processors = list(original_config.get("processors", []))
        processors.insert(0, capture_event)
        structlog.configure(processors=processors)

        try:
            engine = RecoveryEngine(
                db_path=initialized_db_path,
                subprocess_mgr=None,
                transition_queue=AsyncMock(),
            )
            await engine.run_recovery()
        finally:
            structlog.configure(**original_config)

        complete_events = [e for e in captured_events if e.get("event") == "recovery_complete"]
        assert len(complete_events) == 1
        assert complete_events[0]["recovery_mode"] == "normal"


# ---------------------------------------------------------------------------
# 多 story 混合场景
# ---------------------------------------------------------------------------


class TestMultiStoryRecovery:
    """多个 story 下的 tasks 混合恢复。"""

    @patch("ato.recovery._artifact_exists", return_value=False)
    @patch("ato.recovery._is_pid_alive", return_value=False)
    async def test_multi_story_crash_recovery(
        self,
        mock_alive: MagicMock,
        mock_artifact: MagicMock,
        initialized_db_path: Path,
    ) -> None:
        """多个 story 各有 running task，正确分类。"""
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("s1"))
            await insert_story(db, _make_story("s2", phase="uat"))
            # s1: structured job
            await insert_task(db, _make_running_task("t1", "s1", pid=10001, phase="reviewing"))
            # s2: interactive session
            await insert_task(db, _make_running_task("t2", "s2", pid=10002, phase="uat"))
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

        assert result.recovery_mode == "crash"
        assert len(result.classifications) == 2

        # 按 task_id 排序验证
        by_id = {c.task_id: c for c in result.classifications}
        assert by_id["t1"].action == "reschedule"  # structured job
        assert by_id["t2"].action == "needs_human"  # interactive session
        assert result.dispatched_count == 1
        assert result.auto_recovered_count == 0
        assert result.needs_human_count == 1


# ---------------------------------------------------------------------------
# Fix F1: complete 提交 transition event 端到端
# ---------------------------------------------------------------------------


class TestCompleteTransitionEvent:
    """验证 complete 恢复动作提交 transition 推进 story。"""

    @patch("ato.recovery._artifact_exists", return_value=True)
    @patch("ato.recovery._is_pid_alive", return_value=False)
    async def test_complete_calls_tq_submit(
        self,
        mock_alive: MagicMock,
        mock_artifact: MagicMock,
        initialized_db_path: Path,
    ) -> None:
        """artifact 恢复后 TQ.submit 被调用且事件名正确。"""
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("s1"))
            await insert_task(
                db,
                _make_running_task(
                    "t1",
                    "s1",
                    pid=99999,
                    phase="reviewing",
                    expected_artifact="/tmp/out.json",
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
        result = await engine.run_recovery()

        assert result.classifications[0].action == "complete"
        mock_tq.submit.assert_called_once()
        event = mock_tq.submit.call_args[0][0]
        assert event.event_name == "review_pass"
        assert event.story_id == "s1"


# ---------------------------------------------------------------------------
# Fix F3: needs_human 二次启动不被绕过
# ---------------------------------------------------------------------------


class TestNeedsHumanSecondRestart:
    """复现 reviewer 场景：第一次 crash recovery → needs_human + approval；
    第二次启动不应自动 reschedule。"""

    @patch("ato.recovery._artifact_exists", return_value=False)
    @patch("ato.recovery._is_pid_alive", return_value=False)
    async def test_second_restart_preserves_needs_human(
        self,
        mock_alive: MagicMock,
        mock_artifact: MagicMock,
        initialized_db_path: Path,
    ) -> None:
        """第一次启动: crash→needs_human→failed+approval。
        第二次启动: normal→跳过有 approval 的 task。"""
        # --- 第一次启动（模拟 crash recovery 结果）---
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("s1", phase="uat"))
            await insert_task(
                db,
                _make_running_task("t1", "s1", pid=99999, phase="uat"),
            )
        finally:
            await db.close()

        engine1 = RecoveryEngine(
            db_path=initialized_db_path,
            subprocess_mgr=None,
            transition_queue=AsyncMock(),
            interactive_phases={"uat"},
        )
        result1 = await engine1.run_recovery()
        assert result1.recovery_mode == "crash"
        assert result1.needs_human_count == 1

        # 验证 task 是 failed（不是 paused）
        db = await get_connection(initialized_db_path)
        try:
            tasks = await get_tasks_by_story(db, "s1")
            assert tasks[0].status == "failed"

            # 验证 approval 已创建
            approvals = await get_pending_approvals(db)
            assert len(approvals) == 1
            assert approvals[0].approval_type == "crash_recovery"
        finally:
            await db.close()

        # --- 第二次启动 ---
        # failed task 不会被 get_paused_tasks 查到 → 无 paused → recovery_mode=none
        engine2 = RecoveryEngine(
            db_path=initialized_db_path,
            subprocess_mgr=None,
            transition_queue=AsyncMock(),
            interactive_phases={"uat"},
        )
        result2 = await engine2.run_recovery()

        # 第二次不应自动恢复
        assert result2.recovery_mode == "none"
        assert result2.auto_recovered_count == 0

        # approval 仍然挂着
        db = await get_connection(initialized_db_path)
        try:
            approvals = await get_pending_approvals(db)
            assert len(approvals) == 1
        finally:
            await db.close()
