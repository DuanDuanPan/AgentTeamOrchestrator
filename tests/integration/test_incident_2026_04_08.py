"""2026-04-08 事故回归测试套件。

固化事故链中"容易重犯的边界"，使后续修改不会重引入同类故障。
每个测试标注对应 BUG ID（见 docs/root-cause-analysis-2026-04-08.md）。

用 fake adapter / monkeypatch 模拟场景，不调用真实 CLI。

Targeted verification:
    uv run pytest tests/integration/test_incident_2026_04_08.py -v
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from ato.models.db import get_connection, init_db, insert_story
from ato.models.schemas import (
    AdapterResult,
    StoryRecord,
)
from ato.subprocess_mgr import RunningTask, SubprocessManager

_NOW = datetime.now(tz=UTC)


def _make_story(story_id: str = "story-incident") -> StoryRecord:
    return StoryRecord(
        story_id=story_id,
        title="Incident regression story",
        status="in_progress",
        current_phase="developing",
        created_at=_NOW,
        updated_at=_NOW,
    )


def _make_result(**overrides: Any) -> AdapterResult:
    defaults: dict[str, Any] = {
        "status": "success",
        "exit_code": 0,
        "duration_ms": 1000,
        "text_result": "ok",
        "cost_usd": 0.01,
        "input_tokens": 100,
        "output_tokens": 50,
        "session_id": "sess-incident",
    }
    defaults.update(overrides)
    return AdapterResult.model_validate(defaults)


@pytest.fixture()
async def db_ready(tmp_path: Path) -> Path:
    db_path = tmp_path / ".ato" / "state.db"
    await init_db(db_path)
    db = await get_connection(db_path)
    try:
        await insert_story(db, _make_story())
    finally:
        await db.close()
    return db_path


class FakeAdapter:
    def __init__(self, result: AdapterResult | None = None) -> None:
        self._result = result or _make_result()

    async def execute(
        self,
        prompt: str,
        options: Any = None,
        *,
        on_process_start: Any = None,
        on_progress: Any = None,
    ) -> AdapterResult:
        if on_process_start:
            proc = AsyncMock()
            proc.pid = 55555
            await on_process_start(proc)
        return self._result


# ---------------------------------------------------------------------------
# BUG-001: Post-result terminal finalizer stuck
# ---------------------------------------------------------------------------


class TestBUG001PostResultStuck:
    """BUG-001 P0: CLI result 返回后终态收敛边界不可卡死。"""

    async def test_db_hang_during_finalize_still_releases(
        self, db_ready: Path
    ) -> None:
        """dispatch 在 DB helper 卡住时有界退出，running 被清空。
        Fallback 保证 task 不永久 running。"""
        import ato.models.db as db_mod

        mgr = SubprocessManager(
            max_concurrent=4,
            adapter=FakeAdapter(),  # type: ignore[arg-type]
            db_path=db_ready,
        )

        orig_fn = db_mod.update_task_status

        async def _hanging_update(
            db: Any, task_id: str, status: str, **kw: Any
        ) -> None:
            if status in ("completed", "failed"):
                await asyncio.sleep(999)
            else:
                await orig_fn(db, task_id, status, **kw)

        db_mod.update_task_status = _hanging_update  # type: ignore[assignment]
        try:
            # dispatch 应有界退出（fallback 成功后正常返回）
            async with asyncio.timeout(15):
                await mgr.dispatch(
                    story_id="story-incident",
                    phase="dev",
                    role="developer",
                    cli_tool="claude",
                    prompt="test",
                )
        finally:
            db_mod.update_task_status = orig_fn

        assert len(mgr.running) == 0

    async def test_dead_pid_watchdog_cleans_up(self, db_ready: Path) -> None:
        """dead PID 被 watchdog sweep 检测并标记 failed。"""
        from ato.models.db import insert_task
        from ato.models.schemas import TaskRecord

        mgr = SubprocessManager(
            max_concurrent=4,
            adapter=FakeAdapter(),  # type: ignore[arg-type]
            db_path=db_ready,
        )

        dead_pid = 99998
        mgr._running[dead_pid] = RunningTask(
            task_id="task-dead-001",
            story_id="story-incident",
            phase="dev",
            pid=dead_pid,
        )

        db = await get_connection(db_ready)
        try:
            await insert_task(
                db,
                TaskRecord(
                    task_id="task-dead-001",
                    story_id="story-incident",
                    phase="dev",
                    role="developer",
                    cli_tool="claude",
                    status="running",
                    pid=dead_pid,
                    started_at=_NOW,
                ),
            )
        finally:
            await db.close()

        swept = await mgr.sweep_dead_workers()
        assert swept == 1
        assert dead_pid not in mgr.running


# ---------------------------------------------------------------------------
# BUG-002: Claude result+exit_code=1 误触发 crash recovery
# ---------------------------------------------------------------------------


class TestBUG002ResultExitCode1:
    """BUG-002 P0: result 存在时 exit_code=1 不应报错。"""

    async def test_result_with_nonzero_exit_returns_success(self) -> None:
        """ClaudeAdapter result-first: result 存在 + exit_code=1 = success。"""
        import json
        from unittest.mock import MagicMock, patch

        from ato.adapters.claude_cli import ClaudeAdapter
        from ato.models.schemas import ClaudeOutput

        result_line = json.dumps(
            {
                "type": "result",
                "result": "All tests pass.",
                "total_cost_usd": 0.01,
                "usage": {"input_tokens": 100, "output_tokens": 50},
                "session_id": "sess-bug002",
                "duration_ms": 1000,
            }
        ).encode() + b"\n"

        # Inline mock process (avoid cross-test imports)
        proc = MagicMock()
        proc.pid = 12345
        proc.returncode = 1
        proc.terminate = MagicMock()
        proc.kill = MagicMock()
        proc.wait = AsyncMock(return_value=1)

        lines = [result_line, b""]
        idx = 0

        async def _readline() -> bytes:
            nonlocal idx
            if idx < len(lines):
                line = lines[idx]
                idx += 1
                return line
            return b""

        stdout = MagicMock()
        stdout.readline = _readline

        stderr_read = False

        async def _read(n: int = 4096) -> bytes:
            nonlocal stderr_read
            if not stderr_read:
                stderr_read = True
                return b""
            return b""

        stderr = MagicMock()
        stderr.read = _read
        proc.stdout = stdout
        proc.stderr = stderr

        adapter = ClaudeAdapter()
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            output = await adapter.execute("test")

        assert isinstance(output, ClaudeOutput)
        assert output.status == "success"
        assert output.exit_code == 0


# ---------------------------------------------------------------------------
# BUG-003: Transition ack timeout 误判业务失败
# ---------------------------------------------------------------------------


class TestBUG003TransitionAckTimeout:
    """BUG-003 P1: transition ack timeout 不直接标记 task failed。"""

    async def test_slow_consumer_still_commits(self, tmp_path: Path) -> None:
        """consumer 慢但最终完成时，transition 仍被正确持久化。"""
        from ato.models.db import get_story
        from ato.models.schemas import TransitionEvent
        from ato.transition_queue import TransitionQueue

        db_path = tmp_path / ".ato" / "state.db"
        await init_db(db_path)

        db = await get_connection(db_path)
        try:
            await insert_story(
                db,
                StoryRecord(
                    story_id="s-slow-ack",
                    title="test",
                    status="backlog",
                    current_phase="queued",
                    created_at=_NOW,
                    updated_at=_NOW,
                ),
            )
        finally:
            await db.close()

        tq = TransitionQueue(db_path)
        await tq.start()

        with pytest.raises(TimeoutError):
            await tq.submit_and_wait(
                TransitionEvent(
                    story_id="s-slow-ack",
                    event_name="start_create",
                    source="agent",
                    submitted_at=_NOW,
                ),
                timeout_seconds=0.001,
            )

        await asyncio.sleep(0.5)

        db = await get_connection(db_path)
        try:
            story = await get_story(db, "s-slow-ack")
        finally:
            await db.close()

        assert story is not None
        assert story.current_phase == "creating"
        await tq.stop()


# ---------------------------------------------------------------------------
# BUG-005/007: BMAD PASS output deterministic fast-path
# ---------------------------------------------------------------------------


class TestBUG007BmadPassFastPath:
    """BUG-007 P2: PASS/Approve 输出不触发 semantic fallback。"""

    async def test_pass_verdict_skips_semantic_runner(self) -> None:
        """明确 PASS 输出走 deterministic fast-path。"""
        from ato.adapters.bmad_adapter import BmadAdapter

        class TrackingRunner:
            called = False

            async def parse_markdown(self, *a: Any, **kw: Any) -> list[dict[str, Any]]:
                self.called = True
                return []

        runner = TrackingRunner()
        adapter = BmadAdapter(semantic_runner=runner)

        from ato.models.schemas import BmadSkillType

        result = await adapter.parse(
            "# Review\n\nResult: PASS\n\nAll criteria met.",
            skill_type=BmadSkillType.STORY_VALIDATION,
            story_id="s-bmad",
        )
        assert result.verdict == "approved"
        assert result.parser_mode == "deterministic"
        assert not runner.called
