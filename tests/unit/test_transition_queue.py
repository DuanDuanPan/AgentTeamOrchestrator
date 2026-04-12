"""TransitionQueue 单元测试。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from ato.config import PhaseDefinition
from ato.models.db import get_connection, get_story, insert_story
from ato.models.schemas import (
    StateTransitionError,
    StoryRecord,
    TransitionEvent,
    WorktreePreflightResult,
)
from ato.nudge import Nudge
from ato.state_machine import StoryLifecycle
from ato.transition_queue import TransitionQueue, _gate_type_for_transition, _replay_to_phase

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(
    story_id: str = "story-1",
    event_name: str = "start_create",
    source: str = "agent",
) -> TransitionEvent:
    return TransitionEvent(
        story_id=story_id,
        event_name=event_name,
        source=source,  # type: ignore[arg-type]
        submitted_at=datetime.now(UTC),
    )


async def _insert_story_at_phase(
    db_path: Path,
    story_id: str,
    phase: str,
    status: str = "in_progress",
    worktree_path: str | None = None,
) -> None:
    """在 SQLite 中插入一个处于指定 phase 的 story。"""
    now = datetime.now(UTC)
    record = StoryRecord(
        story_id=story_id,
        title=f"Test {story_id}",
        status=status,  # type: ignore[arg-type]
        current_phase=phase,
        worktree_path=worktree_path,
        created_at=now,
        updated_at=now,
    )
    db = await get_connection(db_path)
    try:
        await insert_story(db, record)
        await db.commit()
    finally:
        await db.close()


def _preflight_result(
    story_id: str,
    *,
    passed: bool,
    failure_reason: str | None = None,
) -> WorktreePreflightResult:
    return WorktreePreflightResult.model_validate(
        {
            "story_id": story_id,
            "gate_type": "pre_review",
            "passed": passed,
            "base_ref": "main",
            "base_sha": "base",
            "head_sha": "head",
            "porcelain_output": "?? dirty.py\n" if failure_reason else "",
            "diffstat": " dirty.py | 1 +\n" if passed else "",
            "changed_files": ["dirty.py"] if passed else [],
            "failure_reason": failure_reason,
            "checked_at": datetime.now(UTC),
        }
    )


async def _read_story(db_path: Path, story_id: str) -> StoryRecord | None:
    """从 SQLite 读回 story。"""
    db = await get_connection(db_path)
    try:
        return await get_story(db, story_id)
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# Test: TransitionEvent model
# ---------------------------------------------------------------------------


class TestTransitionEvent:
    def test_valid_event(self) -> None:
        evt = _make_event()
        assert evt.story_id == "story-1"
        assert evt.event_name == "start_create"
        assert evt.source == "agent"

    def test_rejects_invalid_source(self) -> None:
        with pytest.raises(Exception):  # noqa: B017 — Pydantic ValidationError
            TransitionEvent(
                story_id="s",
                event_name="e",
                source="invalid",  # type: ignore[arg-type]
                submitted_at=datetime.now(UTC),
            )


# ---------------------------------------------------------------------------
# Test: Worktree gate mapping
# ---------------------------------------------------------------------------


class TestWorktreeGateMapping:
    def test_dev_done_and_fix_done_are_pre_review_gated(self) -> None:
        assert _gate_type_for_transition("dev_done") == "pre_review"
        assert _gate_type_for_transition("fix_done") == "pre_review"

    def test_non_gated_event_returns_none(self) -> None:
        assert _gate_type_for_transition("create_done") is None
        assert _gate_type_for_transition("uat_pass") is None


# ---------------------------------------------------------------------------
# Test: _replay_to_phase
# ---------------------------------------------------------------------------


class TestReplayToPhase:
    async def test_replay_to_queued(self) -> None:
        sm = await StoryLifecycle.create()
        await _replay_to_phase(sm, "queued")
        assert sm.current_state_value == "queued"

    async def test_replay_to_creating(self) -> None:
        sm = await StoryLifecycle.create()
        await _replay_to_phase(sm, "creating")
        assert sm.current_state_value == "creating"

    async def test_replay_to_planning_legacy_compat(self) -> None:
        """Story 9.4: DB 中残留 planning 的旧 story 应能 replay 到 creating（语义等价）。"""
        sm = await StoryLifecycle.create()
        await _replay_to_phase(sm, "planning")
        # planning 已移除，replay 经由 start_create 到达 creating
        assert sm.current_state_value == "creating"

    async def test_replay_to_developing(self) -> None:
        sm = await StoryLifecycle.create()
        await _replay_to_phase(sm, "developing")
        assert sm.current_state_value == "developing"

    async def test_replay_to_reviewing(self) -> None:
        sm = await StoryLifecycle.create()
        await _replay_to_phase(sm, "reviewing")
        assert sm.current_state_value == "reviewing"

    async def test_replay_to_fixing(self) -> None:
        sm = await StoryLifecycle.create()
        await _replay_to_phase(sm, "fixing")
        assert sm.current_state_value == "fixing"

    async def test_replay_to_done(self) -> None:
        sm = await StoryLifecycle.create()
        await _replay_to_phase(sm, "done")
        assert sm.current_state_value == "done"

    async def test_replay_to_blocked(self) -> None:
        sm = await StoryLifecycle.create()
        await _replay_to_phase(sm, "blocked")
        assert sm.current_state_value == "blocked"

    async def test_replay_to_qa_testing(self) -> None:
        sm = await StoryLifecycle.create()
        await _replay_to_phase(sm, "qa_testing")
        assert sm.current_state_value == "qa_testing"

    async def test_replay_to_regression(self) -> None:
        sm = await StoryLifecycle.create()
        await _replay_to_phase(sm, "regression")
        assert sm.current_state_value == "regression"

    async def test_replay_to_designing(self) -> None:
        """Story 9.1: replay 到 designing 阶段。"""
        sm = await StoryLifecycle.create()
        await _replay_to_phase(sm, "designing")
        assert sm.current_state_value == "designing"

    async def test_replay_unknown_phase_raises(self) -> None:
        sm = await StoryLifecycle.create()
        with pytest.raises(StateTransitionError, match="unknown phase"):
            await _replay_to_phase(sm, "nonexistent")


# ---------------------------------------------------------------------------
# Test: TransitionQueue FIFO ordering
# ---------------------------------------------------------------------------


class TestTransitionQueueFIFO:
    async def test_fifo_order(self, initialized_db_path: Path) -> None:
        """提交 5 个事件，验证处理顺序与提交顺序一致。"""
        await _insert_story_at_phase(initialized_db_path, "s1", "queued", "backlog")

        tq = TransitionQueue(initialized_db_path)
        await tq.start()

        events = [
            _make_event("s1", "start_create"),
            _make_event("s1", "create_done"),
            _make_event("s1", "design_done"),
            _make_event("s1", "validate_pass"),
        ]
        for evt in events:
            await tq.submit(evt)

        await tq._queue.join()

        story = await _read_story(initialized_db_path, "s1")
        assert story is not None
        assert story.current_phase == "developing"

        await tq.stop()


# ---------------------------------------------------------------------------
# Test: Serialization — no concurrent execution
# ---------------------------------------------------------------------------


class TestTransitionQueueSerialization:
    async def test_serial_processing(self, initialized_db_path: Path) -> None:
        """并发提交多个事件，验证同一时刻只有一个在处理。"""
        await _insert_story_at_phase(initialized_db_path, "sa", "queued", "backlog")
        await _insert_story_at_phase(initialized_db_path, "sb", "queued", "backlog")

        tq = TransitionQueue(initialized_db_path)
        await tq.start()

        await asyncio.gather(
            tq.submit(_make_event("sa", "start_create")),
            tq.submit(_make_event("sb", "start_create")),
        )
        await tq._queue.join()

        sa = await _read_story(initialized_db_path, "sa")
        sb = await _read_story(initialized_db_path, "sb")
        assert sa is not None and sa.current_phase == "creating"
        assert sb is not None and sb.current_phase == "creating"


class TestSprintStatusSyncOnTransition:
    async def test_regression_pass_updates_sprint_status_yaml(
        self, initialized_db_path: Path
    ) -> None:
        project_root = initialized_db_path.parent.parent
        sprint_status_path = (
            project_root / "_bmad-output" / "implementation-artifacts" / "sprint-status.yaml"
        )
        sprint_status_path.parent.mkdir(parents=True, exist_ok=True)
        sprint_status_path.write_text(
            """\
generated: 2026-03-18
last_updated: 2026-04-11
project: Demo

development_status:
  epic-7: in-progress
  7-1-mandatory-item-compliance-engine: in-progress
""",
            encoding="utf-8",
        )
        await _insert_story_at_phase(
            initialized_db_path,
            "7-1-mandatory-item-compliance-engine",
            "regression",
        )

        tq = TransitionQueue(initialized_db_path)
        await tq.start()

        await tq.submit(_make_event("7-1-mandatory-item-compliance-engine", "regression_pass"))
        await tq._queue.join()

        story = await _read_story(initialized_db_path, "7-1-mandatory-item-compliance-engine")
        assert story is not None
        assert story.current_phase == "done"
        content = sprint_status_path.read_text(encoding="utf-8")
        assert "7-1-mandatory-item-compliance-engine: done" in content

        await tq.stop()

    async def test_regression_pass_commits_sprint_status_on_done(
        self, initialized_db_path: Path
    ) -> None:
        from unittest.mock import AsyncMock, patch

        from ato.core import reset_main_path_gate

        await _insert_story_at_phase(
            initialized_db_path,
            "7-2-dynamic-adversarial-role-generation",
            "regression",
        )

        tq = TransitionQueue(initialized_db_path)
        await tq.start()

        reset_main_path_gate()
        try:
            with (
                patch("ato.core.derive_project_root", return_value=Path("/tmp/project")),
                patch("ato.worktree_mgr.WorktreeManager") as mock_wm_cls,
                patch("ato.transition_queue.send_user_notification"),
            ):
                mock_mgr = mock_wm_cls.return_value
                mock_mgr.cleanup = AsyncMock()
                mock_mgr.commit_sprint_status_update = AsyncMock(return_value=(True, "abc123"))

                await tq.submit(
                    _make_event(
                        "7-2-dynamic-adversarial-role-generation",
                        "regression_pass",
                    )
                )
                await tq._queue.join()

                mock_mgr.cleanup.assert_called_once_with("7-2-dynamic-adversarial-role-generation")
                mock_mgr.commit_sprint_status_update.assert_called_once_with(
                    "7-2-dynamic-adversarial-role-generation"
                )
        finally:
            reset_main_path_gate()
            await tq.stop()


# ---------------------------------------------------------------------------
# Test: submit_and_wait acknowledgment
# ---------------------------------------------------------------------------


class TestTransitionQueueSubmitAndWait:
    async def test_returns_after_transition_commits(self, initialized_db_path: Path) -> None:
        """submit_and_wait() 返回时，story phase 应已持久化。"""
        await _insert_story_at_phase(initialized_db_path, "s1", "queued", "backlog")

        tq = TransitionQueue(initialized_db_path)
        await tq.start()

        new_state = await tq.submit_and_wait(_make_event("s1", "start_create"))

        story = await _read_story(initialized_db_path, "s1")
        assert new_state == "creating"
        assert story is not None
        assert story.current_phase == "creating"

        await tq.stop()

    async def test_raises_for_failed_transition(self, initialized_db_path: Path) -> None:
        """submit_and_wait() 应将 transition 失败反馈给调用方。"""
        await _insert_story_at_phase(initialized_db_path, "s1", "queued", "backlog")

        tq = TransitionQueue(initialized_db_path)
        await tq.start()

        with pytest.raises(StateTransitionError, match="Transition failed"):
            await tq.submit_and_wait(_make_event("s1", "create_done"))

        story = await _read_story(initialized_db_path, "s1")
        assert story is not None
        assert story.current_phase == "queued"

        await tq.stop()

    async def test_fixing_resume_event_commits_target_phase(
        self, initialized_db_path: Path
    ) -> None:
        """qa_fix_done 应把 story 从 fixing 提交回 qa_testing。"""
        await _insert_story_at_phase(initialized_db_path, "s-fix", "fixing", "review")

        tq = TransitionQueue(initialized_db_path)
        await tq.start()

        new_state = await tq.submit_and_wait(_make_event("s-fix", "qa_fix_done"))

        story = await _read_story(initialized_db_path, "s-fix")
        assert new_state == "qa_testing"
        assert story is not None
        assert story.current_phase == "qa_testing"

        await tq.stop()

    async def test_submit_and_wait_timeout_does_not_cancel_future(
        self, initialized_db_path: Path
    ) -> None:
        """Story 10.3 AC1: submit_and_wait timeout 不取消 completion future。"""
        import asyncio

        await _insert_story_at_phase(initialized_db_path, "s-slow", "queued", "backlog")

        tq = TransitionQueue(initialized_db_path)
        await tq.start()

        # Submit event that will eventually succeed, but wait with very short timeout
        with pytest.raises(TimeoutError):
            await tq.submit_and_wait(
                _make_event("s-slow", "start_create"),
                timeout_seconds=0.001,  # Very short timeout
            )

        # Give the consumer time to process the event
        await asyncio.sleep(0.5)

        # The transition should still have been committed by the consumer
        story = await _read_story(initialized_db_path, "s-slow")
        assert story is not None
        assert story.current_phase == "creating"

        await tq.stop()

    async def test_submit_and_wait_uses_extended_timeout_for_pre_review_events(
        self,
        initialized_db_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """dev_done/fix_done 默认等待时间应覆盖 preflight/finalize 路径。"""
        tq = TransitionQueue(initialized_db_path)
        tq._running = True

        captured: dict[str, float] = {}

        async def fake_wait_for(awaitable: object, timeout: float) -> str:
            captured["timeout"] = timeout
            return "reviewing"

        monkeypatch.setattr("ato.transition_queue.asyncio.wait_for", fake_wait_for)

        result = await tq.submit_and_wait(_make_event("s-gated", "dev_done"))

        assert result == "reviewing"
        assert captured["timeout"] == 120.0

    async def test_submit_and_wait_explicit_timeout_overrides_pre_review_default(
        self,
        initialized_db_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """显式 timeout_seconds 必须覆盖 gated transition 的默认长等待。"""
        tq = TransitionQueue(initialized_db_path)
        tq._running = True

        captured: dict[str, float] = {}

        async def fake_wait_for(awaitable: object, timeout: float) -> str:
            captured["timeout"] = timeout
            return "reviewing"

        monkeypatch.setattr("ato.transition_queue.asyncio.wait_for", fake_wait_for)

        result = await tq.submit_and_wait(
            _make_event("s-gated-explicit", "fix_done"),
            timeout_seconds=7.5,
        )

        assert result == "reviewing"
        assert captured["timeout"] == 7.5


# ---------------------------------------------------------------------------
# Test: pre-review worktree gate
# ---------------------------------------------------------------------------


class TestTransitionQueuePreReviewGate:
    async def test_dev_done_preflight_pass_allows_transition(
        self,
        initialized_db_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        await _insert_story_at_phase(
            initialized_db_path,
            "s-preflight-pass",
            "developing",
            worktree_path="/tmp/wt",
        )

        from ato.worktree_mgr import WorktreeManager

        mock_preflight = AsyncMock(return_value=_preflight_result("s-preflight-pass", passed=True))
        monkeypatch.setattr(WorktreeManager, "preflight_check", mock_preflight)

        tq = TransitionQueue(initialized_db_path)
        await tq.start()
        try:
            new_state = await tq.submit_and_wait(_make_event("s-preflight-pass", "dev_done"))
        finally:
            await tq.stop()

        assert new_state == "reviewing"
        story = await _read_story(initialized_db_path, "s-preflight-pass")
        assert story is not None
        assert story.current_phase == "reviewing"
        mock_preflight.assert_awaited_once_with("s-preflight-pass", "pre_review")

    async def test_dev_done_preflight_failure_creates_approval(
        self,
        initialized_db_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        await _insert_story_at_phase(initialized_db_path, "s-preflight-fail", "developing")

        from ato.worktree_mgr import WorktreeManager

        mock_preflight = AsyncMock(
            return_value=_preflight_result(
                "s-preflight-fail",
                passed=False,
                failure_reason="UNCOMMITTED_CHANGES",
            )
        )
        monkeypatch.setattr(WorktreeManager, "preflight_check", mock_preflight)

        tq = TransitionQueue(initialized_db_path)
        await tq.start()
        try:
            with pytest.raises(StateTransitionError, match="Worktree preflight blocked"):
                await tq.submit_and_wait(_make_event("s-preflight-fail", "dev_done"))
        finally:
            await tq.stop()

        from ato.models.db import get_pending_approvals

        db = await get_connection(initialized_db_path)
        try:
            pending = await get_pending_approvals(db)
            approvals = [a for a in pending if a.approval_type == "preflight_failure"]
            assert len(approvals) == 1
            assert approvals[0].recommended_action == "manual_commit_and_retry"
        finally:
            await db.close()

        story = await _read_story(initialized_db_path, "s-preflight-fail")
        assert story is not None
        assert story.current_phase == "developing"


# ---------------------------------------------------------------------------
# Test: Error isolation
# ---------------------------------------------------------------------------


class TestTransitionQueueErrorIsolation:
    async def test_invalid_transition_does_not_crash_queue(self, initialized_db_path: Path) -> None:
        """非法 transition 不应 crash 队列，后续事件正常处理。"""
        await _insert_story_at_phase(initialized_db_path, "s1", "queued", "backlog")

        tq = TransitionQueue(initialized_db_path)
        await tq.start()

        await tq.submit(_make_event("s1", "create_done"))
        await tq._queue.join()

        await tq.submit(_make_event("s1", "start_create"))
        await tq._queue.join()

        story = await _read_story(initialized_db_path, "s1")
        assert story is not None
        assert story.current_phase == "creating"

        await tq.stop()

    async def test_nonexistent_story_does_not_crash_queue(self, initialized_db_path: Path) -> None:
        """不存在的 story 不 crash 队列。"""
        await _insert_story_at_phase(initialized_db_path, "real", "queued", "backlog")

        tq = TransitionQueue(initialized_db_path)
        await tq.start()

        await tq.submit(_make_event("ghost", "start_create"))
        await tq._queue.join()

        await tq.submit(_make_event("real", "start_create"))
        await tq._queue.join()

        story = await _read_story(initialized_db_path, "real")
        assert story is not None
        assert story.current_phase == "creating"

        await tq.stop()

    async def test_failed_transition_evicts_cached_machine(self, initialized_db_path: Path) -> None:
        """失败后缓存的状态机应被驱逐。"""
        await _insert_story_at_phase(initialized_db_path, "s1", "queued", "backlog")

        tq = TransitionQueue(initialized_db_path)
        await tq.start()

        await tq.submit(_make_event("s1", "start_create"))
        await tq._queue.join()
        assert "s1" in tq._machines

        await tq.submit(_make_event("s1", "validate_pass"))
        await tq._queue.join()
        assert "s1" not in tq._machines

        await tq.submit(_make_event("s1", "create_done"))
        await tq._queue.join()

        story = await _read_story(initialized_db_path, "s1")
        assert story is not None
        assert story.current_phase == "designing"

        await tq.stop()

    async def test_persist_failure_after_send_rollback_and_evict(
        self, initialized_db_path: Path
    ) -> None:
        """send() 成功但 save_story_state 失败 → rollback + 驱逐缓存。"""
        await _insert_story_at_phase(initialized_db_path, "s1", "queued", "backlog")

        tq = TransitionQueue(initialized_db_path)
        await tq.start()

        # 先让 s1 进入 creating 以填充缓存
        await tq.submit(_make_event("s1", "start_create"))
        await tq._queue.join()
        assert "s1" in tq._machines

        # 注入 save_story_state 失败
        import ato.transition_queue as tq_mod

        orig_save = tq_mod.save_story_state  # type: ignore[attr-defined]

        async def failing_save(*args: object, **kwargs: object) -> None:
            msg = "injected persist failure"
            raise RuntimeError(msg)

        tq_mod.save_story_state = failing_save  # type: ignore[attr-defined]
        try:
            # send 会成功（内存推进到 designing），但 persist 会失败
            await tq.submit(_make_event("s1", "create_done"))
            await tq._queue.join()
        finally:
            tq_mod.save_story_state = orig_save  # type: ignore[attr-defined]

        # 缓存应被驱逐（内存 vs DB 不一致）
        assert "s1" not in tq._machines

        # DB 中应该还是 creating（rollback 了）
        story = await _read_story(initialized_db_path, "s1")
        assert story is not None
        assert story.current_phase == "creating"

        # 队列仍然存活——后续合法事件正常处理
        await tq.submit(_make_event("s1", "create_done"))
        await tq._queue.join()

        story = await _read_story(initialized_db_path, "s1")
        assert story is not None
        assert story.current_phase == "designing"

        await tq.stop()

    async def test_commit_failure_after_send_rollback_and_evict(
        self, initialized_db_path: Path
    ) -> None:
        """send() + persist 成功但 commit 失败 → rollback + 驱逐缓存。"""
        await _insert_story_at_phase(initialized_db_path, "s1", "queued", "backlog")

        tq = TransitionQueue(initialized_db_path)
        await tq.start()

        await tq.submit(_make_event("s1", "start_create"))
        await tq._queue.join()
        assert "s1" in tq._machines

        # 注入 db.commit 失败
        import aiosqlite

        assert isinstance(tq._db, aiosqlite.Connection)
        orig_commit = tq._db.commit

        async def failing_commit() -> None:
            msg = "injected commit failure"
            raise RuntimeError(msg)

        tq._db.commit = failing_commit  # type: ignore[method-assign]
        try:
            await tq.submit(_make_event("s1", "create_done"))
            await tq._queue.join()
        finally:
            tq._db.commit = orig_commit  # type: ignore[method-assign]

        # 缓存被驱逐
        assert "s1" not in tq._machines

        # 后续合法事件正常处理
        await tq.submit(_make_event("s1", "create_done"))
        await tq._queue.join()

        story = await _read_story(initialized_db_path, "s1")
        assert story is not None
        assert story.current_phase == "designing"

        await tq.stop()


# ---------------------------------------------------------------------------
# Test: Start/Stop lifecycle
# ---------------------------------------------------------------------------


class TestTransitionQueueLifecycle:
    async def test_duplicate_start_no_second_consumer(self, initialized_db_path: Path) -> None:
        """重复 start() 不得创建第二个 consumer。"""
        tq = TransitionQueue(initialized_db_path)
        await tq.start()
        task1 = tq._consumer_task
        await tq.start()
        task2 = tq._consumer_task
        assert task1 is task2
        await tq.stop()

    async def test_submit_before_start_rejected(self, initialized_db_path: Path) -> None:
        """未 start 就 submit 应抛出 StateTransitionError。"""
        tq = TransitionQueue(initialized_db_path)
        with pytest.raises(StateTransitionError, match="not running"):
            await tq.submit(_make_event("s1", "start_create"))

    async def test_stop_then_submit_rejected(self, initialized_db_path: Path) -> None:
        """stop() 后 submit() 应抛出 StateTransitionError。"""
        tq = TransitionQueue(initialized_db_path)
        await tq.start()
        await tq.stop()
        with pytest.raises(StateTransitionError, match="not running"):
            await tq.submit(_make_event("s1", "start_create"))

    async def test_stop_idempotent(self, initialized_db_path: Path) -> None:
        """stop() 是幂等的——重复调用不出错。"""
        tq = TransitionQueue(initialized_db_path)
        await tq.start()
        await tq.stop()
        await tq.stop()  # 第二次 stop 不应报错

    async def test_stop_before_start_is_safe(self, initialized_db_path: Path) -> None:
        """未 start 就 stop 不出错。"""
        tq = TransitionQueue(initialized_db_path)
        await tq.stop()  # 应无副作用

    async def test_stop_start_submit_processes(self, initialized_db_path: Path) -> None:
        """stop → start → submit：第二轮 consumer 正常处理事件。"""
        await _insert_story_at_phase(initialized_db_path, "s1", "queued", "backlog")
        tq = TransitionQueue(initialized_db_path)
        await tq.start()
        await tq.stop()

        # 重新 start
        await tq.start()
        await tq.submit(_make_event("s1", "start_create"))
        await asyncio.wait_for(tq._queue.join(), timeout=5.0)

        story = await _read_story(initialized_db_path, "s1")
        assert story is not None
        assert story.current_phase == "creating"
        await tq.stop()

    async def test_stop_clears_machines_cache(self, initialized_db_path: Path) -> None:
        """stop() 应清空状态机缓存。"""
        await _insert_story_at_phase(initialized_db_path, "s1", "queued", "backlog")
        tq = TransitionQueue(initialized_db_path)
        await tq.start()
        await tq.submit(_make_event("s1", "start_create"))
        await tq._queue.join()
        assert len(tq._machines) > 0
        await tq.stop()
        assert len(tq._machines) == 0


# ---------------------------------------------------------------------------
# Test: State machine restore from various phases
# ---------------------------------------------------------------------------


class TestMachineRestore:
    @pytest.mark.parametrize(
        "phase",
        [
            "queued",
            "creating",
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
            "blocked",
        ],
    )
    async def test_restore_from_phase(self, initialized_db_path: Path, phase: str) -> None:
        """从各个 phase 恢复状态机实例。"""
        status_map = {
            "queued": "backlog",
            "creating": "planning",
            "validating": "planning",
            "dev_ready": "ready",
            "developing": "in_progress",
            "reviewing": "review",
            "fixing": "review",
            "qa_testing": "in_progress",
            "uat": "uat",
            "merging": "in_progress",
            "regression": "in_progress",
            "done": "done",
            "blocked": "blocked",
        }
        await _insert_story_at_phase(initialized_db_path, "s1", phase, status_map[phase])

        tq = TransitionQueue(initialized_db_path)
        await tq.start()

        # 使用 TQ 内部的 DB 连接来验证恢复
        import aiosqlite

        assert isinstance(tq._db, aiosqlite.Connection)
        sm = await tq._get_or_create_machine("s1", tq._db)
        assert sm.current_state_value == phase

        await tq.stop()

    async def test_restore_from_legacy_planning_phase(self, initialized_db_path: Path) -> None:
        """Story 9.4: DB 残留 planning phase 的 story 应能恢复到 creating（向后兼容）。"""
        await _insert_story_at_phase(initialized_db_path, "s-legacy", "planning", "planning")

        tq = TransitionQueue(initialized_db_path)
        await tq.start()

        import aiosqlite

        assert isinstance(tq._db, aiosqlite.Connection)
        sm = await tq._get_or_create_machine("s-legacy", tq._db)
        # planning 已移除，replay 到等价的 creating
        assert sm.current_state_value == "creating"

        await tq.stop()


# ---------------------------------------------------------------------------
# Test: Nudge integration
# ---------------------------------------------------------------------------


class TestTransitionQueueNudge:
    async def test_submit_triggers_nudge(self, initialized_db_path: Path) -> None:
        """submit() 应触发 nudge.notify()。"""
        await _insert_story_at_phase(initialized_db_path, "s1", "queued", "backlog")
        nudge = Nudge()
        tq = TransitionQueue(initialized_db_path, nudge=nudge)
        await tq.start()

        assert not nudge._event.is_set()
        await tq.submit(_make_event("s1", "start_create"))
        # notify() 在 submit 中同步调用，此时 event 应被设置
        # （consumer 可能已经在处理，但 notify 发生在 put 后立即执行）
        await tq._queue.join()
        await tq.stop()


# ---------------------------------------------------------------------------
# Story 9.2: Worktree creation on developing
# ---------------------------------------------------------------------------


class TestWorktreeCreationOnDeveloping:
    async def test_start_dev_creates_worktree_once(self, initialized_db_path: Path) -> None:
        """start_dev → developing 时调用 WorktreeManager.create()（首次进入）。"""
        from unittest.mock import AsyncMock, patch

        await _insert_story_at_phase(initialized_db_path, "s1", "dev_ready")
        tq = TransitionQueue(initialized_db_path)
        await tq.start()

        mock_create = AsyncMock(return_value=Path("/tmp/.worktrees/s1"))
        with patch("ato.worktree_mgr.WorktreeManager") as mock_wm_cls:
            mock_wm_cls.return_value.create = mock_create
            await tq.submit(_make_event("s1", "start_dev"))
            await tq._queue.join()

        # 验证 create 被调用
        mock_create.assert_called_once_with("s1", base_ref="HEAD")
        await tq.stop()

    async def test_start_dev_skips_if_worktree_exists(self, initialized_db_path: Path) -> None:
        """story 已有 worktree_path 时不再重复创建。"""
        from unittest.mock import AsyncMock, patch

        from ato.models.db import update_story_worktree_path

        await _insert_story_at_phase(initialized_db_path, "s2", "dev_ready")

        # 预设 worktree_path
        db = await get_connection(initialized_db_path)
        try:
            await update_story_worktree_path(db, "s2", "/existing/worktree")
        finally:
            await db.close()

        tq = TransitionQueue(initialized_db_path)
        await tq.start()

        mock_create = AsyncMock()
        with patch("ato.worktree_mgr.WorktreeManager") as mock_wm_cls:
            mock_wm_cls.return_value.create = mock_create
            await tq.submit(_make_event("s2", "start_dev"))
            await tq._queue.join()

        # 不应创建
        mock_create.assert_not_called()
        await tq.stop()


# ---------------------------------------------------------------------------
# Story 9.3: Conditional phase skip
# ---------------------------------------------------------------------------


def _make_designing_phase_defs() -> list[PhaseDefinition]:
    """创建包含 skip_when 的 designing 阶段定义。"""

    return [
        PhaseDefinition(
            name="designing",
            role="ux_designer",
            cli_tool="claude",
            model=None,
            sandbox=None,
            phase_type="structured_job",
            next_on_success="validating",
            next_on_failure=None,
            timeout_seconds=1800,
            workspace="main",
            skip_when="not story.has_ui",
        ),
    ]


class TestConditionalPhaseSkip:
    """Story 9.3 AC3: 条件跳过 designing 阶段。"""

    async def test_designing_skipped_when_no_ui(self, initialized_db_path: Path) -> None:
        """has_ui=False 时 designing 被自动跳过，story 进入 validating。"""
        # 插入 story at creating (has_ui=False by default)
        await _insert_story_at_phase(initialized_db_path, "s-noui", "creating")

        phase_defs = _make_designing_phase_defs()
        tq = TransitionQueue(initialized_db_path, phase_defs=phase_defs)
        await tq.start()

        # create_done → designing (skip_when triggers) → auto design_done → validating
        await tq.submit(_make_event("s-noui", "create_done"))
        # Regression guard: auto-skip must not crash the consumer or hang join().
        await asyncio.wait_for(tq._queue.join(), timeout=1.0)
        assert tq._consumer_task is not None
        assert not tq._consumer_task.done()

        story = await _read_story(initialized_db_path, "s-noui")
        assert story is not None
        assert story.current_phase == "validating"

        await tq.stop()

    async def test_designing_not_skipped_when_has_ui(self, initialized_db_path: Path) -> None:
        """has_ui=True 时 designing 不被跳过，story 停留在 designing。"""
        # 插入 has_ui=True 的 story at creating
        now = datetime.now(UTC)
        record = StoryRecord(
            story_id="s-ui",
            title="UI story",
            status="in_progress",
            current_phase="creating",
            has_ui=True,
            created_at=now,
            updated_at=now,
        )
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, record)
            await db.commit()
        finally:
            await db.close()

        phase_defs = _make_designing_phase_defs()
        tq = TransitionQueue(initialized_db_path, phase_defs=phase_defs)
        await tq.start()

        await tq.submit(_make_event("s-ui", "create_done"))
        await asyncio.sleep(0.1)
        await tq._queue.join()

        story = await _read_story(initialized_db_path, "s-ui")
        assert story is not None
        assert story.current_phase == "designing"

        await tq.stop()

    async def test_no_phase_defs_no_skip(self, initialized_db_path: Path) -> None:
        """没有 phase_defs 时不触发任何跳过。"""
        await _insert_story_at_phase(initialized_db_path, "s-nopd", "creating")

        tq = TransitionQueue(initialized_db_path)  # no phase_defs
        await tq.start()

        await tq.submit(_make_event("s-nopd", "create_done"))
        await asyncio.sleep(0.1)
        await tq._queue.join()

        story = await _read_story(initialized_db_path, "s-nopd")
        assert story is not None
        assert story.current_phase == "designing"

        await tq.stop()


# ---------------------------------------------------------------------------
# Story 9.3: Batch spec commit on dev_ready
# ---------------------------------------------------------------------------


class TestBatchSpecCommitOnDevReady:
    """Story 9.3 AC5/AC7: batch spec commit 在 dev_ready 时触发。"""

    async def test_batch_spec_commit_triggered_when_all_dev_ready(
        self, initialized_db_path: Path
    ) -> None:
        """batch 内所有 story 到达 dev_ready 时触发 spec commit。"""
        from unittest.mock import AsyncMock, patch

        from ato.models.db import get_active_batch, insert_batch, insert_batch_story_links
        from ato.models.schemas import BatchRecord, BatchStoryLink

        # 创建 batch 和两个 story
        now = datetime.now(UTC)
        batch = BatchRecord(batch_id="b1", status="active", created_at=now)
        db = await get_connection(initialized_db_path)
        try:
            await insert_batch(db, batch)
            await _insert_story_at_phase(initialized_db_path, "s1", "validating")
            await _insert_story_at_phase(initialized_db_path, "s2", "dev_ready")
            await insert_batch_story_links(
                db,
                [
                    BatchStoryLink(batch_id="b1", story_id="s1", sequence_no=0),
                    BatchStoryLink(batch_id="b1", story_id="s2", sequence_no=1),
                ],
            )
        finally:
            await db.close()

        mock_commit = AsyncMock(return_value=(True, "abc123"))
        tq = TransitionQueue(initialized_db_path)
        await tq.start()

        with (
            patch("ato.worktree_mgr.WorktreeManager") as mock_wm_cls,
            patch("ato.core.derive_project_root", return_value=Path("/tmp/project")),
        ):
            mock_wm_cls.return_value.batch_spec_commit = mock_commit

            # s1: validate_pass → dev_ready (now both at dev_ready → trigger commit)
            await tq.submit(_make_event("s1", "validate_pass"))
            await asyncio.sleep(0.2)
            await tq._queue.join()

        mock_commit.assert_called_once()
        call_args = mock_commit.call_args
        assert call_args[0][0] == "b1"  # batch_id
        assert set(call_args[0][1]) == {"s1", "s2"}  # story_ids

        # Verify spec_committed is True
        db = await get_connection(initialized_db_path)
        try:
            batch_record = await get_active_batch(db)
            assert batch_record is not None
            assert batch_record.spec_committed is True
        finally:
            await db.close()

        await tq.stop()

    async def test_no_commit_when_not_all_dev_ready(self, initialized_db_path: Path) -> None:
        """batch 内部分 story 未到达 dev_ready 时不触发 commit。"""
        from unittest.mock import AsyncMock, patch

        from ato.models.db import insert_batch, insert_batch_story_links
        from ato.models.schemas import BatchRecord, BatchStoryLink

        now = datetime.now(UTC)
        batch = BatchRecord(batch_id="b2", status="active", created_at=now)
        db = await get_connection(initialized_db_path)
        try:
            await insert_batch(db, batch)
            await _insert_story_at_phase(initialized_db_path, "s3", "validating")
            await _insert_story_at_phase(initialized_db_path, "s4", "creating")
            await insert_batch_story_links(
                db,
                [
                    BatchStoryLink(batch_id="b2", story_id="s3", sequence_no=0),
                    BatchStoryLink(batch_id="b2", story_id="s4", sequence_no=1),
                ],
            )
        finally:
            await db.close()

        mock_commit = AsyncMock(return_value=(True, "abc123"))
        tq = TransitionQueue(initialized_db_path)
        await tq.start()

        with (
            patch("ato.worktree_mgr.WorktreeManager") as mock_wm_cls,
            patch("ato.core.derive_project_root", return_value=Path("/tmp/project")),
        ):
            mock_wm_cls.return_value.batch_spec_commit = mock_commit
            await tq.submit(_make_event("s3", "validate_pass"))
            await asyncio.sleep(0.2)
            await tq._queue.join()

        # s4 还在 creating → 不触发 commit
        mock_commit.assert_not_called()
        await tq.stop()

    async def test_batch_spec_commit_waits_for_main_path_gate(
        self, initialized_db_path: Path
    ) -> None:
        """spec commit 应等待 gate 独占释放后才执行。"""
        from unittest.mock import AsyncMock, patch

        from ato.core import get_main_path_gate, reset_main_path_gate
        from ato.models.db import insert_batch, insert_batch_story_links
        from ato.models.schemas import BatchRecord, BatchStoryLink

        now = datetime.now(UTC)
        batch = BatchRecord(batch_id="b-lock", status="active", created_at=now)
        db = await get_connection(initialized_db_path)
        try:
            await insert_batch(db, batch)
            await _insert_story_at_phase(initialized_db_path, "s-lock", "validating")
            await insert_batch_story_links(
                db,
                [BatchStoryLink(batch_id="b-lock", story_id="s-lock", sequence_no=0)],
            )
        finally:
            await db.close()

        mock_commit = AsyncMock(return_value=(True, "abc123"))
        tq = TransitionQueue(initialized_db_path)
        await tq.start()

        reset_main_path_gate()
        gate = get_main_path_gate()
        await gate.acquire_exclusive()
        acquired = True
        try:
            with (
                patch("ato.worktree_mgr.WorktreeManager") as mock_wm_cls,
                patch("ato.core.derive_project_root", return_value=Path("/tmp/project")),
            ):
                mock_wm_cls.return_value.batch_spec_commit = mock_commit
                await tq.submit(_make_event("s-lock", "validate_pass"))
                await asyncio.sleep(0.05)
                mock_commit.assert_not_called()

                await gate.release_exclusive()
                acquired = False
                await asyncio.wait_for(tq._queue.join(), timeout=1.0)

            mock_commit.assert_called_once()
        finally:
            if acquired:
                await gate.release_exclusive()
            reset_main_path_gate()
            await tq.stop()

    async def test_precommit_failure_creates_approval(self, initialized_db_path: Path) -> None:
        """batch spec commit 失败时创建 precommit_failure(scope=spec_batch) approval。"""
        import json
        from unittest.mock import AsyncMock, patch

        from ato.models.db import get_pending_approvals, insert_batch, insert_batch_story_links
        from ato.models.schemas import BatchRecord, BatchStoryLink

        now = datetime.now(UTC)
        batch = BatchRecord(batch_id="b3", status="active", created_at=now)
        db = await get_connection(initialized_db_path)
        try:
            await insert_batch(db, batch)
            await _insert_story_at_phase(initialized_db_path, "s5", "validating")
            await insert_batch_story_links(
                db,
                [BatchStoryLink(batch_id="b3", story_id="s5", sequence_no=0)],
            )
        finally:
            await db.close()

        mock_commit = AsyncMock(return_value=(False, "pre-commit hook failed"))
        tq = TransitionQueue(initialized_db_path)
        await tq.start()

        with (
            patch("ato.worktree_mgr.WorktreeManager") as mock_wm_cls,
            patch("ato.core.derive_project_root", return_value=Path("/tmp/project")),
        ):
            mock_wm_cls.return_value.batch_spec_commit = mock_commit
            await tq.submit(_make_event("s5", "validate_pass"))
            await asyncio.sleep(0.2)
            await tq._queue.join()

        # Verify precommit_failure approval created
        db = await get_connection(initialized_db_path)
        try:
            approvals = await get_pending_approvals(db)
            spec_approvals = [a for a in approvals if a.approval_type == "precommit_failure"]
            assert len(spec_approvals) == 1
            payload = json.loads(spec_approvals[0].payload or "{}")
            assert payload["scope"] == "spec_batch"
            assert payload["batch_id"] == "b3"
            assert "s5" in payload["story_ids"]
            assert "retry" in payload["options"]
        finally:
            await db.close()

        await tq.stop()

    async def test_existing_spec_batch_precommit_failure_blocks_repeat_creation(
        self, initialized_db_path: Path
    ) -> None:
        """同一 batch 已有 pending precommit_failure 时不应重复尝试 / 重复建单。"""
        import json
        from unittest.mock import AsyncMock, patch

        from ato.models.db import (
            get_pending_approvals,
            insert_approval,
            insert_batch,
            insert_batch_story_links,
        )
        from ato.models.schemas import ApprovalRecord, BatchRecord, BatchStoryLink

        now = datetime.now(UTC)
        batch = BatchRecord(batch_id="b3-repeat", status="active", created_at=now)
        db = await get_connection(initialized_db_path)
        try:
            await insert_batch(db, batch)
            await _insert_story_at_phase(initialized_db_path, "s5-repeat", "dev_ready", "ready")
            await insert_batch_story_links(
                db,
                [BatchStoryLink(batch_id="b3-repeat", story_id="s5-repeat", sequence_no=0)],
            )
            await insert_approval(
                db,
                ApprovalRecord(
                    approval_id="precommit-existing-1",
                    story_id="s5-repeat",
                    approval_type="precommit_failure",
                    status="pending",
                    created_at=now,
                    payload=json.dumps(
                        {
                            "scope": "spec_batch",
                            "batch_id": "b3-repeat",
                            "story_ids": ["s5-repeat"],
                            "error_output": "main workspace has foreign dirty files: ato.yaml",
                            "options": ["retry", "manual_fix", "skip"],
                        }
                    ),
                ),
            )
        finally:
            await db.close()

        mock_commit = AsyncMock(return_value=(False, "should not run"))
        tq = TransitionQueue(initialized_db_path)

        with (
            patch("ato.worktree_mgr.WorktreeManager") as mock_wm_cls,
            patch("ato.core.derive_project_root", return_value=Path("/tmp/project")),
        ):
            mock_wm_cls.return_value.batch_spec_commit = mock_commit
            await tq.ensure_dev_ready_progress("s5-repeat")

        mock_commit.assert_not_called()

        db = await get_connection(initialized_db_path)
        try:
            approvals = await get_pending_approvals(db)
            spec_approvals = [a for a in approvals if a.approval_type == "precommit_failure"]
            assert len(spec_approvals) == 1
            assert spec_approvals[0].approval_id == "precommit-existing-1"
        finally:
            await db.close()

    async def test_exception_in_spec_commit_creates_approval(
        self, initialized_db_path: Path
    ) -> None:
        """batch_spec_commit() 抛异常时仍创建 approval（不吞掉异常导致卡死）。"""
        import json
        from unittest.mock import AsyncMock, patch

        from ato.models.db import get_pending_approvals, insert_batch, insert_batch_story_links
        from ato.models.schemas import BatchRecord, BatchStoryLink

        now = datetime.now(UTC)
        batch = BatchRecord(batch_id="b4", status="active", created_at=now)
        db = await get_connection(initialized_db_path)
        try:
            await insert_batch(db, batch)
            await _insert_story_at_phase(initialized_db_path, "s6", "validating")
            await insert_batch_story_links(
                db,
                [BatchStoryLink(batch_id="b4", story_id="s6", sequence_no=0)],
            )
        finally:
            await db.close()

        # batch_spec_commit 抛 WorktreeError
        from ato.models.schemas import WorktreeError

        mock_commit = AsyncMock(side_effect=WorktreeError("git timeout"))
        tq = TransitionQueue(initialized_db_path)
        await tq.start()

        with (
            patch("ato.worktree_mgr.WorktreeManager") as mock_wm_cls,
            patch("ato.core.derive_project_root", return_value=Path("/tmp/project")),
        ):
            mock_wm_cls.return_value.batch_spec_commit = mock_commit
            await tq.submit(_make_event("s6", "validate_pass"))
            await asyncio.sleep(0.2)
            await tq._queue.join()

        # 验证异常路径仍创建了 approval
        db = await get_connection(initialized_db_path)
        try:
            approvals = await get_pending_approvals(db)
            spec_approvals = [a for a in approvals if a.approval_type == "precommit_failure"]
            assert len(spec_approvals) == 1
            payload = json.loads(spec_approvals[0].payload or "{}")
            assert payload["scope"] == "spec_batch"
            assert "git timeout" in payload["error_output"]
        finally:
            await db.close()

        await tq.stop()

    async def test_spec_committed_batch_revalidates_main_workspace_before_start_dev(
        self, initialized_db_path: Path
    ) -> None:
        """batch.spec_committed=True 时仍需重做 main workspace gate。"""
        from unittest.mock import AsyncMock, patch

        from ato.models.db import get_story, insert_batch, insert_batch_story_links
        from ato.models.schemas import BatchRecord, BatchStoryLink

        now = datetime.now(UTC)
        batch = BatchRecord(
            batch_id="b5",
            status="active",
            created_at=now,
            spec_committed=True,
        )
        db = await get_connection(initialized_db_path)
        try:
            await insert_batch(db, batch)
            await _insert_story_at_phase(initialized_db_path, "s7", "validating")
            await insert_batch_story_links(
                db,
                [BatchStoryLink(batch_id="b5", story_id="s7", sequence_no=0)],
            )
        finally:
            await db.close()

        mock_commit = AsyncMock(return_value=(True, "all owned main artifacts already committed"))
        tq = TransitionQueue(initialized_db_path)
        await tq.start()

        with (
            patch("ato.worktree_mgr.WorktreeManager") as mock_wm_cls,
            patch("ato.core.derive_project_root", return_value=Path("/tmp/project")),
        ):
            mock_wm_cls.return_value.batch_spec_commit = mock_commit
            await tq.submit(_make_event("s7", "validate_pass"))
            await asyncio.sleep(0.2)
            await tq._queue.join()

        mock_commit.assert_called_once_with("b5", ["s7"])
        db = await get_connection(initialized_db_path)
        try:
            story = await get_story(db, "s7")
            assert story is not None
            assert story.current_phase == "developing"
        finally:
            await db.close()

        await tq.stop()

    async def test_spec_committed_revalidation_allows_foreign_dirty_workspace(
        self, initialized_db_path: Path
    ) -> None:
        """batch.spec_committed=True 时 foreign dirty 不再阻止 start_dev。"""
        from unittest.mock import AsyncMock, patch

        from ato.models.db import (
            get_story,
            insert_batch,
            insert_batch_story_links,
        )
        from ato.models.schemas import BatchRecord, BatchStoryLink

        now = datetime.now(UTC)
        batch = BatchRecord(
            batch_id="b6",
            status="active",
            created_at=now,
            spec_committed=True,
        )
        db = await get_connection(initialized_db_path)
        try:
            await insert_batch(db, batch)
            await _insert_story_at_phase(initialized_db_path, "s8", "validating")
            await insert_batch_story_links(
                db,
                [BatchStoryLink(batch_id="b6", story_id="s8", sequence_no=0)],
            )
        finally:
            await db.close()

        mock_commit = AsyncMock(
            return_value=(True, "all owned main artifacts already committed (idempotent)")
        )
        tq = TransitionQueue(initialized_db_path)
        await tq.start()

        with (
            patch("ato.worktree_mgr.WorktreeManager") as mock_wm_cls,
            patch("ato.core.derive_project_root", return_value=Path("/tmp/project")),
        ):
            mock_wm_cls.return_value.batch_spec_commit = mock_commit
            await tq.submit(_make_event("s8", "validate_pass"))
            await asyncio.sleep(0.2)
            await tq._queue.join()

        db = await get_connection(initialized_db_path)
        try:
            story = await get_story(db, "s8")
            assert story is not None
            assert story.current_phase == "developing"
        finally:
            await db.close()

        await tq.stop()
