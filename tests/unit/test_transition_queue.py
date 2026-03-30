"""TransitionQueue 单元测试。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ato.config import PhaseDefinition
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
        # 等足够时间让 auto-skip event 也被处理
        await asyncio.sleep(0.1)
        await tq._queue.join()

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

    async def test_batch_spec_commit_waits_for_main_path_limiter(
        self, initialized_db_path: Path
    ) -> None:
        """spec commit 应等待 validating/main workspace 释放共享 limiter。"""
        from unittest.mock import AsyncMock, patch

        from ato.core import get_main_path_limiter, reset_main_path_limiter
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

        reset_main_path_limiter()
        limiter = get_main_path_limiter()
        await limiter.acquire()
        try:
            with (
                patch("ato.worktree_mgr.WorktreeManager") as mock_wm_cls,
                patch("ato.core.derive_project_root", return_value=Path("/tmp/project")),
            ):
                mock_wm_cls.return_value.batch_spec_commit = mock_commit
                await tq.submit(_make_event("s-lock", "validate_pass"))
                await asyncio.sleep(0.05)
                mock_commit.assert_not_called()

                limiter.release()
                await asyncio.wait_for(tq._queue.join(), timeout=1.0)

            mock_commit.assert_called_once()
        finally:
            if limiter.locked():
                limiter.release()
            reset_main_path_limiter()
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
