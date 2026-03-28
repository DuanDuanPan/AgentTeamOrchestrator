"""TransitionQueue 单元测试。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ato.models.db import get_connection, get_story, insert_story
from ato.models.schemas import StateTransitionError, StoryRecord, TransitionEvent
from ato.nudge import Nudge
from ato.state_machine import StoryLifecycle
from ato.transition_queue import TransitionQueue, _replay_to_phase

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
) -> None:
    """在 SQLite 中插入一个处于指定 phase 的 story。"""
    now = datetime.now(UTC)
    record = StoryRecord(
        story_id=story_id,
        title=f"Test {story_id}",
        status=status,  # type: ignore[arg-type]
        current_phase=phase,
        created_at=now,
        updated_at=now,
    )
    db = await get_connection(db_path)
    try:
        await insert_story(db, record)
        await db.commit()
    finally:
        await db.close()


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
# Test: _replay_to_phase
# ---------------------------------------------------------------------------


class TestReplayToPhase:
    async def test_replay_to_queued(self) -> None:
        sm = await StoryLifecycle.create()
        await _replay_to_phase(sm, "queued")
        assert sm.current_state_value == "queued"

    async def test_replay_to_planning(self) -> None:
        sm = await StoryLifecycle.create()
        await _replay_to_phase(sm, "planning")
        assert sm.current_state_value == "planning"

    async def test_replay_to_creating(self) -> None:
        sm = await StoryLifecycle.create()
        await _replay_to_phase(sm, "creating")
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
            _make_event("s1", "plan_done"),
            _make_event("s1", "create_done"),
            _make_event("s1", "design_done"),
            _make_event("s1", "validate_pass"),
        ]
        for evt in events:
            await tq.submit(evt)

        await tq._queue.join()

        story = await _read_story(initialized_db_path, "s1")
        assert story is not None
        assert story.current_phase == "dev_ready"

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
        assert sa is not None and sa.current_phase == "planning"
        assert sb is not None and sb.current_phase == "planning"

        await tq.stop()


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
        assert story.current_phase == "planning"

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
        assert story.current_phase == "planning"

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

        await tq.submit(_make_event("s1", "plan_done"))
        await tq._queue.join()

        story = await _read_story(initialized_db_path, "s1")
        assert story is not None
        assert story.current_phase == "creating"

        await tq.stop()

    async def test_persist_failure_after_send_rollback_and_evict(
        self, initialized_db_path: Path
    ) -> None:
        """send() 成功但 save_story_state 失败 → rollback + 驱逐缓存。"""
        await _insert_story_at_phase(initialized_db_path, "s1", "queued", "backlog")

        tq = TransitionQueue(initialized_db_path)
        await tq.start()

        # 先让 s1 进入 planning 以填充缓存
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
            # send 会成功（内存推进到 creating），但 persist 会失败
            await tq.submit(_make_event("s1", "plan_done"))
            await tq._queue.join()
        finally:
            tq_mod.save_story_state = orig_save  # type: ignore[attr-defined]

        # 缓存应被驱逐（内存 vs DB 不一致）
        assert "s1" not in tq._machines

        # DB 中应该还是 planning（rollback 了）
        story = await _read_story(initialized_db_path, "s1")
        assert story is not None
        assert story.current_phase == "planning"

        # 队列仍然存活——后续合法事件正常处理
        await tq.submit(_make_event("s1", "plan_done"))
        await tq._queue.join()

        story = await _read_story(initialized_db_path, "s1")
        assert story is not None
        assert story.current_phase == "creating"

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
            await tq.submit(_make_event("s1", "plan_done"))
            await tq._queue.join()
        finally:
            tq._db.commit = orig_commit  # type: ignore[method-assign]

        # 缓存被驱逐
        assert "s1" not in tq._machines

        # 后续合法事件正常处理
        await tq.submit(_make_event("s1", "plan_done"))
        await tq._queue.join()

        story = await _read_story(initialized_db_path, "s1")
        assert story is not None
        assert story.current_phase == "creating"

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
        assert story.current_phase == "planning"
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
            "planning",
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
            "planning": "planning",
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
