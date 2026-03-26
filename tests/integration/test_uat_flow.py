"""test_uat_flow — UAT 与 Interactive Session 完成检测集成测试 (Story 4.3)。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from ato.models.db import (
    get_connection,
    get_story,
    insert_story,
    insert_task,
    update_task_status,
)
from ato.models.schemas import StoryRecord, TaskRecord, TransitionEvent
from ato.nudge import Nudge
from ato.transition_queue import TransitionQueue

_NOW = datetime.now(tz=UTC)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _evt(story_id: str, event_name: str) -> TransitionEvent:
    return TransitionEvent(
        story_id=story_id,
        event_name=event_name,
        source="cli",
        submitted_at=datetime.now(UTC),
    )


async def _seed_story(
    db_path: Path,
    story_id: str,
    phase: str,
    status: str = "in_progress",
) -> None:
    db = await get_connection(db_path)
    try:
        await insert_story(
            db,
            StoryRecord(
                story_id=story_id,
                title=f"Test {story_id}",
                status=status,
                current_phase=phase,
                worktree_path="/tmp/wt/" + story_id,
                created_at=_NOW,
                updated_at=_NOW,
            ),
        )
    finally:
        await db.close()


async def _seed_task(
    db_path: Path,
    story_id: str,
    task_id: str,
    phase: str,
    status: str = "running",
) -> None:
    db = await get_connection(db_path)
    try:
        await insert_task(
            db,
            TaskRecord(
                task_id=task_id,
                story_id=story_id,
                phase=phase,
                role="developer",
                cli_tool="claude",
                status=status,
                pid=12345,
                started_at=_NOW,
            ),
        )
    finally:
        await db.close()


async def _read_story(db_path: Path, story_id: str) -> StoryRecord | None:
    db = await get_connection(db_path)
    try:
        return await get_story(db, story_id)
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# AC1: uat_pass → merging 端到端
# ---------------------------------------------------------------------------


class TestUatPassE2E:
    async def test_uat_pass_transitions_to_merging(
        self, initialized_db_path: Path
    ) -> None:
        """ato uat --result pass → TQ submit uat_pass → story.current_phase == merging。"""
        sid = "story-uat-e2e-pass"
        await _seed_story(initialized_db_path, sid, "uat", "uat")

        nudge = Nudge()
        tq = TransitionQueue(initialized_db_path, nudge=nudge)
        await tq.start()
        await tq.submit(_evt(sid, "uat_pass"))
        await tq._queue.join()
        await tq.stop()

        story = await _read_story(initialized_db_path, sid)
        assert story is not None
        assert story.current_phase == "merging"
        assert story.status == "in_progress"


# ---------------------------------------------------------------------------
# AC1: uat_fail → fixing 端到端
# ---------------------------------------------------------------------------


class TestUatFailE2E:
    async def test_uat_fail_transitions_to_fixing(
        self, initialized_db_path: Path
    ) -> None:
        """ato uat --result fail → TQ submit uat_fail → story.current_phase == fixing。"""
        sid = "story-uat-e2e-fail"
        await _seed_story(initialized_db_path, sid, "uat", "uat")

        nudge = Nudge()
        tq = TransitionQueue(initialized_db_path, nudge=nudge)
        await tq.start()
        await tq.submit(_evt(sid, "uat_fail"))
        await tq._queue.join()
        await tq.stop()

        story = await _read_story(initialized_db_path, sid)
        assert story is not None
        assert story.current_phase == "fixing"
        assert story.status == "review"  # fixing maps to "review" in PHASE_TO_STATUS


# ---------------------------------------------------------------------------
# AC1: uat_fail → fixing → reviewing (Convergent Loop re-entry)
# ---------------------------------------------------------------------------


class TestUatFailConvergentLoopReentry:
    async def test_uat_fail_then_fix_done_then_review(
        self, initialized_db_path: Path
    ) -> None:
        """uat → fixing → reviewing: CL 回退后完整 review 流程。"""
        sid = "story-uat-cl-reentry"
        await _seed_story(initialized_db_path, sid, "uat", "uat")

        nudge = Nudge()
        tq = TransitionQueue(initialized_db_path, nudge=nudge)
        await tq.start()

        # uat_fail → fixing
        await tq.submit(_evt(sid, "uat_fail"))
        await tq._queue.join()
        story = await _read_story(initialized_db_path, sid)
        assert story is not None
        assert story.current_phase == "fixing"

        # fix_done → reviewing
        await tq.submit(_evt(sid, "fix_done"))
        await tq._queue.join()
        story = await _read_story(initialized_db_path, sid)
        assert story is not None
        assert story.current_phase == "reviewing"

        # review_pass → qa_testing
        await tq.submit(_evt(sid, "review_pass"))
        await tq._queue.join()
        story = await _read_story(initialized_db_path, sid)
        assert story is not None
        assert story.current_phase == "qa_testing"

        await tq.stop()


# ---------------------------------------------------------------------------
# AC2: ato submit 在 developing 阶段 → task completed
# (验证与 4.1 审批基础设施集成)
# ---------------------------------------------------------------------------


class TestSubmitDevelopingVerification:
    async def test_detect_completed_interactive_task(
        self, initialized_db_path: Path
    ) -> None:
        """_detect_completed_interactive_tasks 应检测 developing 阶段已完成的 task。"""
        from ato.core import _detect_completed_interactive_tasks
        from ato.models.db import get_connection, update_task_status

        sid = "story-submit-dev"
        await _seed_story(initialized_db_path, sid, "developing")
        await _seed_task(initialized_db_path, sid, "task-dev-1", "developing")

        # 模拟 ato submit: 标记 task 为 completed
        db = await get_connection(initialized_db_path)
        try:
            await update_task_status(db, "task-dev-1", "completed")
        finally:
            await db.close()

        # 检测已完成的 interactive task
        db = await get_connection(initialized_db_path)
        try:
            results = await _detect_completed_interactive_tasks(
                db,
                interactive_phases={"developing", "uat"},
                phase_event_map={"developing": "dev_done", "uat": "uat_pass"},
            )
        finally:
            await db.close()

        assert len(results) == 1
        task_id, event = results[0]
        assert task_id == "task-dev-1"
        assert event.event_name == "dev_done"
        assert event.story_id == sid


# ---------------------------------------------------------------------------
# AC3: 崩溃恢复 — uat 阶段 needs_human approval
# ---------------------------------------------------------------------------


class TestUatCrashRecoveryApproval:
    async def test_uat_timeout_creates_approval(
        self, initialized_db_path: Path
    ) -> None:
        """uat 阶段 interactive session 超时应创建 session_timeout approval。"""
        from ato.core import _check_interactive_timeouts
        from ato.models.db import get_connection, get_pending_approvals

        sid = "story-uat-timeout"
        await _seed_story(initialized_db_path, sid, "uat", "uat")
        await _seed_task(initialized_db_path, sid, "task-uat-timeout", "uat")

        # 将 task 的 started_at 设为很久以前以模拟超时
        db = await get_connection(initialized_db_path)
        try:
            await db.execute(
                "UPDATE tasks SET started_at = ? WHERE task_id = ?",
                ("2020-01-01T00:00:00+00:00", "task-uat-timeout"),
            )
            await db.commit()
        finally:
            await db.close()

        db = await get_connection(initialized_db_path)
        try:
            await _check_interactive_timeouts(
                db,
                interactive_phases={"developing", "uat"},
                timeout_seconds=60,
            )

            approvals = await get_pending_approvals(db)
        finally:
            await db.close()

        timeout_approvals = [
            a for a in approvals if a.approval_type == "session_timeout"
        ]
        assert len(timeout_approvals) == 1
        assert timeout_approvals[0].story_id == sid


# ---------------------------------------------------------------------------
# Regression: Orchestrator TQ 缓存一致性（Finding 1 回归测试）
# ---------------------------------------------------------------------------


class TestOrchestratorTQCacheConsistency:
    """验证 CLI uat_fail 不会导致 Orchestrator TQ 状态机缓存与 DB 分叉。

    场景：Orchestrator TQ 先缓存 story=uat（通过 qa_pass），然后 CLI 通过
    DB 标记触发 uat_fail，Orchestrator 检测后通过自己的 TQ 执行转换，
    后续 fix_done 应成功（不被缓存中的 stale uat 状态拒绝）。
    """

    async def test_uat_fail_via_db_marker_then_fix_done(
        self, initialized_db_path: Path
    ) -> None:
        """模拟完整流程：TQ 缓存 story → CLI 标记 uat_fail → Orchestrator 检测 → fix_done 成功。"""
        from ato.core import _detect_failed_uat_tasks

        sid = "story-cache-consistency"
        await _seed_story(initialized_db_path, sid, "uat", "uat")
        await _seed_task(initialized_db_path, sid, "task-cache-1", "uat")

        nudge = Nudge()
        tq = TransitionQueue(initialized_db_path, nudge=nudge)
        await tq.start()

        # 1. 先通过 TQ 的 _get_or_create_machine 缓存 story 状态机
        #    提交一个 dummy 操作让 TQ 加载 story 到缓存
        #    （用 uat_pass 然后回退不现实，直接验证 uat_fail 通过 TQ 即可）

        # 2. CLI fail 路径: 只写 DB 标记
        db = await get_connection(initialized_db_path)
        try:
            await update_task_status(
                db,
                "task-cache-1",
                "failed",
                expected_artifact="uat_fail_requested",
                error_message="uat_fail: UI 有问题",
            )
        finally:
            await db.close()

        # 3. Orchestrator 检测 uat_fail_requested 标记
        db = await get_connection(initialized_db_path)
        try:
            fail_events = await _detect_failed_uat_tasks(db)
        finally:
            await db.close()

        assert len(fail_events) == 1
        task_id, event = fail_events[0]
        assert task_id == "task-cache-1"
        assert event.event_name == "uat_fail"
        assert event.story_id == sid

        # 4. Orchestrator 通过自己的 TQ 提交 uat_fail
        await tq.submit(event)
        await tq._queue.join()

        # 标记已消费
        db = await get_connection(initialized_db_path)
        try:
            await update_task_status(
                db, task_id, "failed", expected_artifact="transition_submitted"
            )
        finally:
            await db.close()

        story = await _read_story(initialized_db_path, sid)
        assert story is not None
        assert story.current_phase == "fixing"

        # 5. 关键验证：同一个 TQ 实例处理后续 fix_done 不会被拒绝
        await tq.submit(_evt(sid, "fix_done"))
        await tq._queue.join()

        story = await _read_story(initialized_db_path, sid)
        assert story is not None
        assert story.current_phase == "reviewing"

        await tq.stop()

    async def test_detect_failed_uat_tasks_ignores_consumed(
        self, initialized_db_path: Path
    ) -> None:
        """已标记 transition_submitted 的 failed task 不应被重复检测。"""
        from ato.core import _detect_failed_uat_tasks

        sid = "story-consumed-check"
        await _seed_story(initialized_db_path, sid, "uat", "uat")
        await _seed_task(initialized_db_path, sid, "task-consumed-1", "uat")

        # 标记为已消费
        db = await get_connection(initialized_db_path)
        try:
            await update_task_status(
                db,
                "task-consumed-1",
                "failed",
                expected_artifact="transition_submitted",
            )
        finally:
            await db.close()

        db = await get_connection(initialized_db_path)
        try:
            results = await _detect_failed_uat_tasks(db)
        finally:
            await db.close()

        assert len(results) == 0
