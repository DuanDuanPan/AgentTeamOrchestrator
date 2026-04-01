"""Orchestrator 启停端到端集成测试。"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ato.core import Orchestrator
from ato.models.db import get_connection, init_db, insert_story, insert_task
from ato.models.schemas import StoryRecord, TaskRecord

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
async def setup_db(tmp_path: Path) -> Path:
    """初始化数据库并返回路径。"""
    db_path = tmp_path / ".ato" / "state.db"
    await init_db(db_path)
    return db_path


def _make_settings(polling_interval: float = 0.1) -> MagicMock:
    settings = MagicMock()
    settings.polling_interval = polling_interval
    settings.max_planning_concurrent = 3
    return settings


# ---------------------------------------------------------------------------
# 完整启动→轮询→停止流程
# ---------------------------------------------------------------------------


class TestOrchestratorLifecycle:
    async def test_start_poll_stop(self, setup_db: Path) -> None:
        """完整启动→轮询几次→停止流程。"""
        settings = _make_settings(polling_interval=0.05)
        orchestrator = Orchestrator(settings=settings, db_path=setup_db)
        pid_path = setup_db.parent / "orchestrator.pid"

        poll_count = 0
        original_poll = orchestrator._poll_cycle

        async def counting_poll() -> None:
            nonlocal poll_count
            poll_count += 1
            await original_poll()
            if poll_count >= 3:
                orchestrator._request_shutdown()

        orchestrator._poll_cycle = counting_poll  # type: ignore[method-assign]

        await orchestrator.run()

        assert poll_count >= 3
        # PID 文件应已删除
        assert not pid_path.exists()

    async def test_pid_file_exists_during_run(self, setup_db: Path) -> None:
        """启动后 PID 文件存在，停止后删除。"""
        settings = _make_settings(polling_interval=0.05)
        orchestrator = Orchestrator(settings=settings, db_path=setup_db)
        pid_path = setup_db.parent / "orchestrator.pid"

        pid_seen = False

        async def check_and_stop() -> None:
            nonlocal pid_seen
            pid_seen = pid_path.exists()
            orchestrator._request_shutdown()

        orchestrator._poll_cycle = check_and_stop  # type: ignore[method-assign]

        await orchestrator.run()

        assert pid_seen is True
        assert not pid_path.exists()

    async def test_startup_latency_under_3s(self, setup_db: Path) -> None:
        """启动延迟 ≤3 秒（NFR5）。"""
        settings = _make_settings(polling_interval=0.05)
        orchestrator = Orchestrator(settings=settings, db_path=setup_db)

        t0 = time.monotonic()

        async def stop_immediately() -> None:
            orchestrator._request_shutdown()

        orchestrator._poll_cycle = stop_immediately  # type: ignore[method-assign]
        await orchestrator.run()

        elapsed = time.monotonic() - t0
        assert elapsed < 3.0, f"启动延迟 {elapsed:.2f}s 超过 3 秒限制"


class TestOrchestratorShutdownIntegration:
    async def test_sigterm_triggers_graceful_shutdown(self, setup_db: Path) -> None:
        """通过 _request_shutdown（模拟 SIGTERM）触发优雅停止。

        注：跳过 recovery 阶段，在 startup 完成后插入 running task，
        专注测试 shutdown 的 mark_running_tasks_paused 行为。
        """
        settings = _make_settings(polling_interval=0.1)
        orchestrator = Orchestrator(settings=settings, db_path=setup_db)

        now = datetime.now(tz=UTC)
        story = StoryRecord(
            story_id="sig-story-1",
            title="Signal Test",
            status="in_progress",
            current_phase="developing",
            created_at=now,
            updated_at=now,
        )
        task = TaskRecord(
            task_id="sig-task-1",
            story_id="sig-story-1",
            phase="developing",
            role="developer",
            cli_tool="claude",
            status="running",
            started_at=now,
        )

        poll_count = 0

        async def poll_then_stop() -> None:
            nonlocal poll_count
            poll_count += 1
            if poll_count == 1:
                # 在首次轮询时插入 running task（recovery 已完成）
                db = await get_connection(setup_db)
                try:
                    await insert_story(db, story)
                    await insert_task(db, task)
                finally:
                    await db.close()
            elif poll_count >= 3:
                orchestrator._request_shutdown()

        orchestrator._poll_cycle = poll_then_stop  # type: ignore[method-assign]

        await orchestrator.run()

        # 验证 running task 被标记为 paused
        db = await get_connection(setup_db)
        try:
            from ato.models.db import count_tasks_by_status

            assert await count_tasks_by_status(db, "running") == 0
            assert await count_tasks_by_status(db, "paused") == 1
        finally:
            await db.close()

    async def test_nudge_wakes_up_orchestrator(self, setup_db: Path) -> None:
        """nudge.notify() 能立即唤醒轮询循环，不等定期间隔。"""
        settings = _make_settings(polling_interval=10.0)  # 很长的间隔
        orchestrator = Orchestrator(settings=settings, db_path=setup_db)

        poll_count = 0

        async def poll_with_nudge() -> None:
            nonlocal poll_count
            poll_count += 1
            if poll_count == 1:
                # 首次轮询后立即 nudge 唤醒
                orchestrator._nudge.notify()
            elif poll_count >= 2:
                orchestrator._request_shutdown()

        orchestrator._poll_cycle = poll_with_nudge  # type: ignore[method-assign]

        t0 = time.monotonic()
        await orchestrator.run()
        elapsed = time.monotonic() - t0

        assert poll_count >= 2
        # 如果 nudge 没生效，会等 10 秒；nudge 生效则应远小于 10 秒
        assert elapsed < 3.0, f"nudge 未唤醒轮询循环，耗时 {elapsed:.2f}s"

    async def test_real_sigusr1_wakes_up_orchestrator(self, setup_db: Path) -> None:
        """真实 SIGUSR1 信号通过 send_external_nudge() 投递，验证完整信号→handler→nudge 通路。"""
        settings = _make_settings(polling_interval=10.0)  # 很长的间隔
        orchestrator = Orchestrator(settings=settings, db_path=setup_db)

        poll_count = 0

        async def poll_with_real_signal() -> None:
            nonlocal poll_count
            poll_count += 1
            if poll_count == 1:
                # 首次轮询后通过真实信号唤醒（send_external_nudge → SIGUSR1 → handler → nudge）
                from ato.nudge import send_external_nudge

                send_external_nudge(os.getpid())
            elif poll_count >= 2:
                orchestrator._request_shutdown()

        orchestrator._poll_cycle = poll_with_real_signal  # type: ignore[method-assign]

        t0 = time.monotonic()
        await orchestrator.run()
        elapsed = time.monotonic() - t0

        assert poll_count >= 2
        # 真实信号投递应在 < 3s 内完成唤醒（不等 10s 轮询间隔）
        assert elapsed < 3.0, f"SIGUSR1 信号未唤醒轮询循环，耗时 {elapsed:.2f}s"

    async def test_sigterm_during_startup_still_pauses_tasks(self, setup_db: Path) -> None:
        """SIGTERM 在启动窗口内到达时仍执行 _shutdown()，标记 running→paused。

        注：在 startup 完成后（recovery 已运行）插入 running task，
        然后立即触发 SIGTERM，验证 shutdown 仍能正确 pause。
        """
        settings = _make_settings(polling_interval=0.05)
        orchestrator = Orchestrator(settings=settings, db_path=setup_db)

        original_startup = orchestrator._startup
        now = datetime.now(tz=UTC)

        async def startup_with_task_and_sigterm() -> None:
            await original_startup()
            # startup 完成后插入 running task（recovery 已完成）
            db = await get_connection(setup_db)
            try:
                story = StoryRecord(
                    story_id="startup-sig-story",
                    title="Startup Signal Test",
                    status="in_progress",
                    current_phase="developing",
                    created_at=now,
                    updated_at=now,
                )
                await insert_story(db, story)
                task = TaskRecord(
                    task_id="startup-sig-task",
                    story_id="startup-sig-story",
                    phase="developing",
                    role="developer",
                    cli_tool="claude",
                    status="running",
                    started_at=now,
                )
                await insert_task(db, task)
            finally:
                await db.close()
            # 立即触发 SIGTERM
            orchestrator._request_shutdown()

        orchestrator._startup = startup_with_task_and_sigterm  # type: ignore[method-assign]

        await orchestrator.run()

        # 关键断言：running task 必须被标记为 paused
        db = await get_connection(setup_db)
        try:
            from ato.models.db import count_tasks_by_status

            assert await count_tasks_by_status(db, "running") == 0
            assert await count_tasks_by_status(db, "paused") == 1
        finally:
            await db.close()

        # PID 文件应被清理
        pid_path = setup_db.parent / "orchestrator.pid"
        assert not pid_path.exists()
