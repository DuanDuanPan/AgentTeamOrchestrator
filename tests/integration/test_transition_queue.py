"""TransitionQueue 集成测试——并发 Transition 串行化。"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from pathlib import Path

from ato.models.db import get_connection, get_story, insert_story
from ato.models.schemas import StoryRecord, TransitionEvent
from ato.nudge import Nudge
from ato.transition_queue import TransitionQueue

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _evt(
    story_id: str,
    event_name: str,
    source: str = "agent",
) -> TransitionEvent:
    return TransitionEvent(
        story_id=story_id,
        event_name=event_name,
        source=source,  # type: ignore[arg-type]
        submitted_at=datetime.now(UTC),
    )


async def _seed_story(db_path: Path, story_id: str, phase: str = "queued") -> None:
    """插入一条 story 到 SQLite。"""
    status_map = {
        "queued": "backlog",
        "creating": "planning",
        "designing": "planning",
        "validating": "planning",
        "dev_ready": "ready",
        "developing": "in_progress",
        "reviewing": "review",
        "fixing": "review",
    }
    now = datetime.now(UTC)
    record = StoryRecord(
        story_id=story_id,
        title=f"Test {story_id}",
        status=status_map.get(phase, "in_progress"),  # type: ignore[arg-type]
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
    db = await get_connection(db_path)
    try:
        return await get_story(db, story_id)
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# BDD Scenario: 两个 story 几乎同时提交 transition
# ---------------------------------------------------------------------------


class TestBDDConcurrentTransitions:
    async def test_two_stories_creating_to_designing_serial(
        self, initialized_db_path: Path
    ) -> None:
        """BDD Scenario:

        Given Story A and Story B are in 'creating'
        When both submit 'create_done' simultaneously
        Then A transitions to 'designing' first, B second
        And both end up in 'designing' with no conflicts.

        通过记录 consumer 处理顺序证明串行性。
        """
        await _seed_story(initialized_db_path, "story-a", phase="creating")
        await _seed_story(initialized_db_path, "story-b", phase="creating")

        processed_order: list[str] = []
        from ato.transition_queue import TransitionQueue as _TransitionQueue

        # monkey-patch consumer 中间记录处理顺序
        orig_get = _TransitionQueue._get_or_create_machine

        async def recording_get(self: object, story_id: str, db: object) -> object:
            sm = await orig_get(self, story_id, db)  # type: ignore[arg-type]
            processed_order.append(story_id)
            return sm

        _TransitionQueue._get_or_create_machine = recording_get  # type: ignore[assignment]
        try:
            tq = TransitionQueue(initialized_db_path)
            await tq.start()

            # 顺序提交保证确定性 FIFO：A 先入队
            await tq.submit(_evt("story-a", "create_done"))
            await tq.submit(_evt("story-b", "create_done"))
            await tq._queue.join()
        finally:
            _TransitionQueue._get_or_create_machine = orig_get  # type: ignore[method-assign]

        # 断言精确处理顺序：A 先于 B
        assert processed_order == ["story-a", "story-b"]

        # 验证最终状态正确
        sa = await _read_story(initialized_db_path, "story-a")
        sb = await _read_story(initialized_db_path, "story-b")

        assert sa is not None
        assert sa.current_phase == "designing"
        assert sa.status == "planning"

        assert sb is not None
        assert sb.current_phase == "designing"
        assert sb.status == "planning"

        await tq.stop()

    async def test_serial_no_concurrent_execution(self, initialized_db_path: Path) -> None:
        """验证同一时刻只有一个 transition 在处理。"""
        await _seed_story(initialized_db_path, "sa", phase="creating")
        await _seed_story(initialized_db_path, "sb", phase="creating")

        concurrency_max = 0
        active_count = 0

        import ato.state_machine as sm_mod

        orig_sm_send = sm_mod.StoryLifecycle.send

        async def counting_send(self: object, *a: object, **kw: object) -> object:
            nonlocal concurrency_max, active_count
            active_count += 1
            if active_count > concurrency_max:
                concurrency_max = active_count
            try:
                return await orig_sm_send(self, *a, **kw)  # type: ignore[arg-type]
            finally:
                active_count -= 1

        sm_mod.StoryLifecycle.send = counting_send  # type: ignore[method-assign]
        try:
            tq = TransitionQueue(initialized_db_path)
            await tq.start()

            await asyncio.gather(
                tq.submit(_evt("sa", "create_done")),
                tq.submit(_evt("sb", "create_done")),
            )
            await tq._queue.join()
            await tq.stop()
        finally:
            sm_mod.StoryLifecycle.send = orig_sm_send  # type: ignore[method-assign]

        # 最大并发度应该为 1（串行处理）
        assert concurrency_max == 1


# ---------------------------------------------------------------------------
# NFR2: Transition 处理延迟 ≤5 秒
# ---------------------------------------------------------------------------


class TestTransitionLatency:
    async def test_transition_within_5_seconds(self, initialized_db_path: Path) -> None:
        """NFR2: 状态转换处理延迟 ≤5 秒。"""
        await _seed_story(initialized_db_path, "s1")

        tq = TransitionQueue(initialized_db_path)
        await tq.start()

        t0 = time.monotonic()
        await tq.submit(_evt("s1", "start_create"))
        await tq._queue.join()
        latency = time.monotonic() - t0

        assert latency < 5.0, f"Transition latency {latency:.2f}s exceeds 5s NFR2 threshold"

        story = await _read_story(initialized_db_path, "s1")
        assert story is not None
        assert story.current_phase == "creating"

        await tq.stop()


# ---------------------------------------------------------------------------
# 端到端: submit → queue → send → save_story_state → commit → 读回验证
# ---------------------------------------------------------------------------


class TestEndToEnd:
    async def test_full_pipeline(self, initialized_db_path: Path) -> None:
        """端到端: submit → queue → send → persist → commit → 读回。"""
        await _seed_story(initialized_db_path, "e2e")

        nudge = Nudge()
        tq = TransitionQueue(initialized_db_path, nudge=nudge)
        await tq.start()

        transitions = [
            ("start_create", "creating", "planning"),
            ("create_done", "designing", "planning"),
            ("design_done", "validating", "planning"),
            ("validate_pass", "dev_ready", "ready"),
            ("start_dev", "developing", "in_progress"),
            ("dev_done", "reviewing", "review"),
        ]

        for event_name, expected_phase, expected_status in transitions:
            await tq.submit(_evt("e2e", event_name))
            await tq._queue.join()

            story = await _read_story(initialized_db_path, "e2e")
            assert story is not None, f"Story not found after {event_name}"
            assert story.current_phase == expected_phase, (
                f"After {event_name}: expected phase={expected_phase}, got {story.current_phase}"
            )
            assert story.status == expected_status, (
                f"After {event_name}: expected status={expected_status}, got {story.status}"
            )

        await tq.stop()

    async def test_convergent_loop_review_fail_fix(self, initialized_db_path: Path) -> None:
        """端到端: reviewing → review_fail → fixing → fix_done → reviewing。"""
        await _seed_story(initialized_db_path, "cl", phase="reviewing")

        tq = TransitionQueue(initialized_db_path)
        await tq.start()

        await tq.submit(_evt("cl", "review_fail"))
        await tq._queue.join()

        story = await _read_story(initialized_db_path, "cl")
        assert story is not None
        assert story.current_phase == "fixing"

        await tq.submit(_evt("cl", "fix_done"))
        await tq._queue.join()

        story = await _read_story(initialized_db_path, "cl")
        assert story is not None
        assert story.current_phase == "reviewing"

        await tq.stop()

    async def test_nudge_contract(self, initialized_db_path: Path) -> None:
        """验证 Nudge 合约：submit 后 nudge.wait() 应立即返回。"""
        await _seed_story(initialized_db_path, "n1")
        nudge = Nudge()
        tq = TransitionQueue(initialized_db_path, nudge=nudge)
        await tq.start()

        async def do_submit() -> None:
            await asyncio.sleep(0.02)
            await tq.submit(_evt("n1", "start_create"))

        task = asyncio.create_task(do_submit())
        result = await nudge.wait(timeout=2.0)
        assert result is True
        await task
        await tq._queue.join()
        await tq.stop()
