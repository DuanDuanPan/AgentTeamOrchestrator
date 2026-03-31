"""test_subprocess_mgr — SubprocessManager 单元测试。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from ato.models.db import get_connection, get_cost_summary, init_db
from ato.models.schemas import (
    AdapterResult,
    CLIAdapterError,
    ErrorCategory,
    StoryRecord,
)
from ato.subprocess_mgr import SubprocessManager

# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

_NOW = datetime.now(tz=UTC)


def _make_story(story_id: str = "story-test") -> StoryRecord:
    return StoryRecord(
        story_id=story_id,
        title="测试 story",
        status="in_progress",
        current_phase="developing",
        created_at=_NOW,
        updated_at=_NOW,
    )


def _make_adapter_result(**overrides: Any) -> AdapterResult:
    defaults: dict[str, Any] = {
        "status": "success",
        "exit_code": 0,
        "duration_ms": 1000,
        "text_result": "ok",
        "cost_usd": 0.01,
        "input_tokens": 100,
        "output_tokens": 50,
        "session_id": "sess-123",
    }
    defaults.update(overrides)
    return AdapterResult.model_validate(defaults)


class FakeAdapter:
    """测试用假适配器。"""

    def __init__(self, result: AdapterResult | None = None, error: CLIAdapterError | None = None):
        self._result = result or _make_adapter_result()
        self._error = error
        self.call_count = 0

    async def execute(
        self,
        prompt: str,
        options: dict[str, Any] | None = None,
        *,
        on_process_start: Any = None,
        on_progress: Any = None,
    ) -> AdapterResult:
        self.call_count += 1
        if on_process_start:
            proc = AsyncMock()
            proc.pid = 10000 + self.call_count
            await on_process_start(proc)
        if self._error:
            raise self._error
        return self._result


@pytest.fixture()
async def db_ready(tmp_path: Path) -> Path:
    db_path = tmp_path / ".ato" / "state.db"
    await init_db(db_path)
    # 插入一条 story 供 task 的 foreign key 引用
    from ato.models.db import insert_story

    db = await get_connection(db_path)
    try:
        await insert_story(db, _make_story())
    finally:
        await db.close()
    return db_path


# ---------------------------------------------------------------------------
# 并发控制
# ---------------------------------------------------------------------------


class TestConcurrencyControl:
    async def test_semaphore_limits_concurrent(self, db_ready: Path) -> None:
        """max_concurrent=1 时，两个 dispatch 串行执行。"""
        call_order: list[int] = []
        result = _make_adapter_result()

        class SlowAdapter:
            call_count = 0

            async def execute(self, prompt: str, options: Any = None, **kw: Any) -> AdapterResult:
                self.call_count += 1
                idx = self.call_count
                call_order.append(idx)
                await asyncio.sleep(0.05)
                call_order.append(-idx)  # 负数表示完成
                return result

        mgr = SubprocessManager(max_concurrent=1, adapter=SlowAdapter(), db_path=db_ready)  # type: ignore[arg-type]
        async with asyncio.TaskGroup() as tg:
            tg.create_task(
                mgr.dispatch(
                    story_id="story-test",
                    phase="dev",
                    role="developer",
                    cli_tool="claude",
                    prompt="p1",
                )
            )
            tg.create_task(
                mgr.dispatch(
                    story_id="story-test",
                    phase="dev",
                    role="developer",
                    cli_tool="claude",
                    prompt="p2",
                )
            )
        # 串行执行：第一个开始和完成，然后第二个开始和完成
        assert call_order[0] > 0  # 第一个开始
        assert call_order[1] < 0  # 第一个完成
        assert call_order[2] > 0  # 第二个开始
        assert call_order[3] < 0  # 第二个完成


# ---------------------------------------------------------------------------
# PID 注册
# ---------------------------------------------------------------------------


class TestPIDRegistration:
    async def test_pid_registered_during_dispatch(self, db_ready: Path) -> None:
        adapter = FakeAdapter()
        mgr = SubprocessManager(max_concurrent=4, adapter=adapter, db_path=db_ready)  # type: ignore[arg-type]
        await mgr.dispatch(
            story_id="story-test",
            phase="dev",
            role="developer",
            cli_tool="claude",
            prompt="test",
        )
        # dispatch 完成后 PID 已取消注册
        assert len(mgr.running) == 0

    async def test_pid_registered_then_unregistered(self, db_ready: Path) -> None:
        class TrackAdapter:
            async def execute(self, prompt: str, options: Any = None, **kw: Any) -> AdapterResult:
                if cb := kw.get("on_process_start"):
                    proc = AsyncMock()
                    proc.pid = 42
                    await cb(proc)
                    # 在 execute 中检查 running 状态
                return _make_adapter_result()

        mgr = SubprocessManager(max_concurrent=4, adapter=TrackAdapter(), db_path=db_ready)  # type: ignore[arg-type]
        await mgr.dispatch(
            story_id="story-test",
            phase="dev",
            role="developer",
            cli_tool="claude",
            prompt="test",
        )
        assert len(mgr.running) == 0


# ---------------------------------------------------------------------------
# CLI routing
# ---------------------------------------------------------------------------


class TestAdapterRouting:
    async def test_dispatch_routes_to_adapter_matching_cli_tool(self, db_ready: Path) -> None:
        """adapters 映射存在时，应按 cli_tool 选择对应 adapter。"""
        claude_adapter = FakeAdapter(result=_make_adapter_result(text_result="claude-ok"))
        codex_adapter = FakeAdapter(result=_make_adapter_result(text_result="codex-ok"))
        mgr = SubprocessManager(
            max_concurrent=4,
            adapters={"claude": claude_adapter, "codex": codex_adapter},  # type: ignore[dict-item]
            db_path=db_ready,
        )

        codex_result = await mgr.dispatch(
            story_id="story-test",
            phase="reviewing",
            role="reviewer",
            cli_tool="codex",
            prompt="review",
        )
        claude_result = await mgr.dispatch(
            story_id="story-test",
            phase="fixing",
            role="developer",
            cli_tool="claude",
            prompt="fix",
        )

        assert codex_result.text_result == "codex-ok"
        assert claude_result.text_result == "claude-ok"
        assert codex_adapter.call_count == 1
        assert claude_adapter.call_count == 1


# ---------------------------------------------------------------------------
# 重试
# ---------------------------------------------------------------------------


class TestRetry:
    async def test_retryable_error_retried_once(self, db_ready: Path) -> None:
        error = CLIAdapterError(
            "rate limited",
            category=ErrorCategory.RATE_LIMIT,
            retryable=True,
        )

        call_count = 0
        result = _make_adapter_result()

        class FailOnceAdapter:
            async def execute(self, prompt: str, options: Any = None, **kw: Any) -> AdapterResult:
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise error
                return result

        mgr = SubprocessManager(max_concurrent=4, adapter=FailOnceAdapter(), db_path=db_ready)  # type: ignore[arg-type]
        res = await mgr.dispatch_with_retry(
            story_id="story-test",
            phase="dev",
            role="developer",
            cli_tool="claude",
            prompt="test",
        )
        assert res.status == "success"
        assert call_count == 2

    async def test_retry_reuses_single_task_id(self, db_ready: Path) -> None:
        """Fix #1: 重试复用同一 task_id，只产生一条 tasks 记录。"""
        error = CLIAdapterError(
            "rate limited",
            category=ErrorCategory.RATE_LIMIT,
            retryable=True,
            exit_code=429,
        )
        call_count = 0
        result = _make_adapter_result()

        class FailOnceAdapter:
            async def execute(self, prompt: str, options: Any = None, **kw: Any) -> AdapterResult:
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise error
                return result

        mgr = SubprocessManager(max_concurrent=4, adapter=FailOnceAdapter(), db_path=db_ready)  # type: ignore[arg-type]
        await mgr.dispatch_with_retry(
            story_id="story-test",
            phase="dev",
            role="developer",
            cli_tool="claude",
            prompt="test",
        )
        from ato.models.db import get_tasks_by_story

        db = await get_connection(db_ready)
        tasks = await get_tasks_by_story(db, "story-test")
        await db.close()
        # 一个逻辑任务 → 一条 tasks 记录（最终 completed）
        assert len(tasks) == 1
        task = tasks[0]
        assert task.status == "completed"
        # R2 review: 上轮失败的残留字段必须被清除
        assert task.error_message is None
        assert task.exit_code == 0  # 成功时覆盖为 0，而非残留 429
        assert task.text_result == "ok"

    async def test_retry_produces_multiple_cost_log_entries(self, db_ready: Path) -> None:
        """Fix #1: 重试时每次尝试都生成独立的 cost_log 条目。"""
        error = CLIAdapterError("rate limited", category=ErrorCategory.RATE_LIMIT, retryable=True)
        call_count = 0
        result = _make_adapter_result(cost_usd=0.01)

        class FailOnceAdapter:
            async def execute(self, prompt: str, options: Any = None, **kw: Any) -> AdapterResult:
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise error
                return result

        mgr = SubprocessManager(max_concurrent=4, adapter=FailOnceAdapter(), db_path=db_ready)  # type: ignore[arg-type]
        await mgr.dispatch_with_retry(
            story_id="story-test",
            phase="dev",
            role="developer",
            cli_tool="claude",
            prompt="test",
        )
        db = await get_connection(db_ready)
        summary = await get_cost_summary(db, story_id="story-test")
        await db.close()
        # 2 次尝试 → 2 条 cost_log 记录
        assert summary["call_count"] == 2

    async def test_retry_can_resume_existing_task_id(self, db_ready: Path) -> None:
        """crash recovery 可复用既有 task_id，并从首次尝试就走 update/retry 语义。"""
        from ato.models.db import get_tasks_by_story, insert_task
        from ato.models.schemas import TaskRecord

        error = CLIAdapterError(
            "rate limited",
            category=ErrorCategory.RATE_LIMIT,
            retryable=True,
            exit_code=429,
        )
        call_count = 0
        result = _make_adapter_result()

        class FailOnceAdapter:
            async def execute(self, prompt: str, options: Any = None, **kw: Any) -> AdapterResult:
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise error
                return result

        db = await get_connection(db_ready)
        try:
            await insert_task(
                db,
                TaskRecord(
                    task_id="task-existing",
                    story_id="story-test",
                    phase="dev",
                    role="developer",
                    cli_tool="claude",
                    status="pending",
                    started_at=_NOW,
                ),
            )
        finally:
            await db.close()

        mgr = SubprocessManager(max_concurrent=4, adapter=FailOnceAdapter(), db_path=db_ready)  # type: ignore[arg-type]
        await mgr.dispatch_with_retry(
            story_id="story-test",
            phase="dev",
            role="developer",
            cli_tool="claude",
            prompt="test",
            task_id="task-existing",
            is_retry=True,
        )

        db = await get_connection(db_ready)
        tasks = await get_tasks_by_story(db, "story-test")
        await db.close()
        assert call_count == 2
        assert len(tasks) == 1
        assert tasks[0].task_id == "task-existing"
        assert tasks[0].status == "completed"
        assert tasks[0].error_message is None

    async def test_non_retryable_error_not_retried(self, db_ready: Path) -> None:
        error = CLIAdapterError(
            "parse failed",
            category=ErrorCategory.PARSE_ERROR,
            retryable=False,
        )
        adapter = FakeAdapter(error=error)
        mgr = SubprocessManager(max_concurrent=4, adapter=adapter, db_path=db_ready)  # type: ignore[arg-type]
        with pytest.raises(CLIAdapterError) as exc_info:
            await mgr.dispatch_with_retry(
                story_id="story-test",
                phase="dev",
                role="developer",
                cli_tool="claude",
                prompt="test",
            )
        assert exc_info.value.category == ErrorCategory.PARSE_ERROR
        assert adapter.call_count == 1

    async def test_retryable_error_exhausted(self, db_ready: Path) -> None:
        error = CLIAdapterError(
            "timeout",
            category=ErrorCategory.TIMEOUT,
            retryable=True,
        )
        adapter = FakeAdapter(error=error)
        mgr = SubprocessManager(max_concurrent=4, adapter=adapter, db_path=db_ready)  # type: ignore[arg-type]
        with pytest.raises(CLIAdapterError):
            await mgr.dispatch_with_retry(
                story_id="story-test",
                phase="dev",
                role="developer",
                cli_tool="claude",
                prompt="test",
            )
        assert adapter.call_count == 2  # 1 original + 1 retry


# ---------------------------------------------------------------------------
# cost_log 持久化
# ---------------------------------------------------------------------------


class TestCostLogPersistence:
    async def test_success_writes_cost_log(self, db_ready: Path) -> None:
        adapter = FakeAdapter(result=_make_adapter_result(cost_usd=0.05))
        mgr = SubprocessManager(max_concurrent=4, adapter=adapter, db_path=db_ready)  # type: ignore[arg-type]
        await mgr.dispatch(
            story_id="story-test",
            phase="dev",
            role="developer",
            cli_tool="claude",
            prompt="test",
        )
        db = await get_connection(db_ready)
        summary = await get_cost_summary(db, story_id="story-test")
        await db.close()
        assert summary["total_cost_usd"] == pytest.approx(0.05)
        assert summary["call_count"] == 1

    async def test_failure_writes_cost_log(self, db_ready: Path) -> None:
        error = CLIAdapterError(
            "failed",
            category=ErrorCategory.UNKNOWN,
            retryable=False,
        )
        adapter = FakeAdapter(error=error)
        mgr = SubprocessManager(max_concurrent=4, adapter=adapter, db_path=db_ready)  # type: ignore[arg-type]
        with pytest.raises(CLIAdapterError):
            await mgr.dispatch(
                story_id="story-test",
                phase="dev",
                role="developer",
                cli_tool="claude",
                prompt="test",
            )
        db = await get_connection(db_ready)
        summary = await get_cost_summary(db, story_id="story-test")
        await db.close()
        assert summary["call_count"] == 1
        assert summary["total_cost_usd"] == 0.0


# ---------------------------------------------------------------------------
# Task 表持久化
# ---------------------------------------------------------------------------


class TestTaskPersistence:
    async def test_success_creates_completed_task(self, db_ready: Path) -> None:
        adapter = FakeAdapter()
        mgr = SubprocessManager(max_concurrent=4, adapter=adapter, db_path=db_ready)  # type: ignore[arg-type]
        await mgr.dispatch(
            story_id="story-test",
            phase="dev",
            role="developer",
            cli_tool="claude",
            prompt="test",
        )
        from ato.models.db import get_tasks_by_story

        db = await get_connection(db_ready)
        tasks = await get_tasks_by_story(db, "story-test")
        await db.close()
        assert len(tasks) == 1
        assert tasks[0].status == "completed"
        assert tasks[0].cli_tool == "claude"

    async def test_failure_creates_failed_task(self, db_ready: Path) -> None:
        error = CLIAdapterError("boom", category=ErrorCategory.UNKNOWN, retryable=False)
        adapter = FakeAdapter(error=error)
        mgr = SubprocessManager(max_concurrent=4, adapter=adapter, db_path=db_ready)  # type: ignore[arg-type]
        with pytest.raises(CLIAdapterError):
            await mgr.dispatch(
                story_id="story-test",
                phase="dev",
                role="developer",
                cli_tool="claude",
                prompt="test",
            )
        from ato.models.db import get_tasks_by_story

        db = await get_connection(db_ready)
        tasks = await get_tasks_by_story(db, "story-test")
        await db.close()
        assert len(tasks) == 1
        assert tasks[0].status == "failed"
        assert tasks[0].error_message is not None

    async def test_no_task_before_semaphore(self, db_ready: Path) -> None:
        """Fix #2: 排队期间不产生 running 记录。"""
        from ato.models.db import get_tasks_by_story

        blocker = asyncio.Event()
        result = _make_adapter_result()

        class BlockingAdapter:
            async def execute(self, prompt: str, options: Any = None, **kw: Any) -> AdapterResult:
                await blocker.wait()
                return result

        mgr = SubprocessManager(max_concurrent=1, adapter=BlockingAdapter(), db_path=db_ready)  # type: ignore[arg-type]
        # 启动第一个占位任务
        task1 = asyncio.create_task(
            mgr.dispatch(
                story_id="story-test",
                phase="dev",
                role="developer",
                cli_tool="claude",
                prompt="p1",
            )
        )
        await asyncio.sleep(0.05)  # 让 task1 进入 semaphore

        # 启动第二个排队任务
        task2 = asyncio.create_task(
            mgr.dispatch(
                story_id="story-test",
                phase="dev",
                role="developer",
                cli_tool="claude",
                prompt="p2",
            )
        )
        await asyncio.sleep(0.05)  # 给 task2 时间进入排队

        # task2 还在排队，不应该有两条 running 记录
        db = await get_connection(db_ready)
        tasks = await get_tasks_by_story(db, "story-test")
        await db.close()
        # 只有 task1 在 running（task2 尚未创建 TaskRecord）
        assert len(tasks) == 1

        blocker.set()
        await task1
        await task2


# ---------------------------------------------------------------------------
# Fix #4: Claude telemetry 落库
# ---------------------------------------------------------------------------


class TestTelemetryPersistence:
    async def test_claude_output_cache_tokens_and_model_persisted(self, db_ready: Path) -> None:
        """Fix #4: cache_read_input_tokens 和 model 正确写入 cost_log。"""
        from ato.models.schemas import ClaudeOutput

        claude_result = ClaudeOutput.model_validate(
            {
                "status": "success",
                "exit_code": 0,
                "duration_ms": 2000,
                "text_result": "ok",
                "cost_usd": 0.02,
                "input_tokens": 500,
                "output_tokens": 100,
                "cache_read_input_tokens": 300,
                "session_id": "sess-1",
                "model_usage": {"model": "claude-opus-4-6", "inputTokens": 500},
            }
        )

        class ClaudeResultAdapter:
            async def execute(self, prompt: str, options: Any = None, **kw: Any) -> ClaudeOutput:
                return claude_result

        mgr = SubprocessManager(max_concurrent=4, adapter=ClaudeResultAdapter(), db_path=db_ready)  # type: ignore[arg-type]
        await mgr.dispatch(
            story_id="story-test",
            phase="dev",
            role="developer",
            cli_tool="claude",
            prompt="test",
        )

        db = await get_connection(db_ready)
        cursor = await db.execute("SELECT model, cache_read_input_tokens FROM cost_log")
        row = await cursor.fetchone()
        await db.close()
        assert row is not None
        data = dict(row)
        assert data["model"] == "claude-opus-4-6"
        assert data["cache_read_input_tokens"] == 300

    async def test_codex_output_model_and_cache_persisted(self, db_ready: Path) -> None:
        """CodexOutput 的 model_name 与 cache_read_input_tokens 正确写入 cost_log。"""
        from ato.models.schemas import CodexOutput

        codex_result = CodexOutput.model_validate(
            {
                "status": "success",
                "exit_code": 0,
                "duration_ms": 1500,
                "text_result": "review complete",
                "cost_usd": 0.03,
                "input_tokens": 26024,
                "output_tokens": 29,
                "cache_read_input_tokens": 10624,
                "session_id": "thread-abc",
                "model_name": "codex-mini-latest",
            }
        )

        class CodexResultAdapter:
            async def execute(self, prompt: str, options: Any = None, **kw: Any) -> CodexOutput:
                return codex_result

        mgr = SubprocessManager(max_concurrent=4, adapter=CodexResultAdapter(), db_path=db_ready)  # type: ignore[arg-type]
        await mgr.dispatch(
            story_id="story-test",
            phase="review",
            role="reviewer",
            cli_tool="codex",
            prompt="test",
        )

        db = await get_connection(db_ready)
        cursor = await db.execute(
            "SELECT model, cache_read_input_tokens, cost_usd, cli_tool FROM cost_log"
        )
        row = await cursor.fetchone()
        await db.close()
        assert row is not None
        data = dict(row)
        assert data["model"] == "codex-mini-latest"
        assert data["cache_read_input_tokens"] == 10624
        assert data["cli_tool"] == "codex"
        assert data["cost_usd"] == pytest.approx(0.03)


# ---------------------------------------------------------------------------
# Activity flush — 终态前显式 flush
# ---------------------------------------------------------------------------


class TestActivityFlush:
    async def test_dispatch_persists_full_text_result(self, db_ready: Path) -> None:
        """完整 agent 输出应落库到 tasks.text_result，供审计追溯。"""
        from ato.models.db import get_tasks_by_story

        full_output = "# Review\n\n" + ("finding details\n" * 200)
        mgr = SubprocessManager(
            max_concurrent=4,
            adapter=FakeAdapter(result=_make_adapter_result(text_result=full_output)),  # type: ignore[arg-type]
            db_path=db_ready,
        )
        await mgr.dispatch(
            story_id="story-test",
            phase="dev",
            role="reviewer",
            cli_tool="codex",
            prompt="test",
        )

        db = await get_connection(db_ready)
        try:
            tasks = await get_tasks_by_story(db, "story-test")
        finally:
            await db.close()

        assert len(tasks) == 1
        assert tasks[0].text_result == full_output

    async def test_turn_end_activity_flushed_on_success(self, db_ready: Path) -> None:
        """Fix: Codex turn_end 不在 _progress_wrapper 终态集合，
        但 dispatch() 成功路径的显式 flush 仍能把最新 activity 落库。"""
        from ato.models.schemas import ProgressEvent

        result = _make_adapter_result()

        class EmitTurnEndAdapter:
            async def execute(self, prompt: str, options: Any = None, **kw: Any) -> AdapterResult:
                on_progress = kw.get("on_progress")
                if on_progress:
                    # 模拟 Codex 的最后事件：turn_end（非 result/error）
                    await on_progress(
                        ProgressEvent(
                            event_type="turn_end",
                            summary="回合结束 (in=100 out=50)",
                            cli_tool="codex",
                            timestamp=datetime.now(tz=UTC),
                            raw={"type": "turn.completed"},
                        )
                    )
                return result

        mgr = SubprocessManager(max_concurrent=4, adapter=EmitTurnEndAdapter(), db_path=db_ready)  # type: ignore[arg-type]
        await mgr.dispatch(
            story_id="story-test",
            phase="dev",
            role="developer",
            cli_tool="codex",
            prompt="test",
        )

        db = await get_connection(db_ready)
        cursor = await db.execute(
            "SELECT last_activity_type, last_activity_summary "
            "FROM tasks WHERE story_id = 'story-test'"
        )
        row = await cursor.fetchone()
        await db.close()
        assert row is not None
        assert row[0] == "turn_end"
        assert "回合结束" in row[1]

    async def test_activity_flushed_on_failure(self, db_ready: Path) -> None:
        """失败路径中，最后一条 activity 也被显式 flush 到 DB。"""
        from ato.models.schemas import ProgressEvent

        error = CLIAdapterError("boom", category=ErrorCategory.UNKNOWN, retryable=False)

        class EmitThenFailAdapter:
            async def execute(self, prompt: str, options: Any = None, **kw: Any) -> AdapterResult:
                on_progress = kw.get("on_progress")
                if on_progress:
                    await on_progress(
                        ProgressEvent(
                            event_type="text",
                            summary="正在处理...",
                            cli_tool="claude",
                            timestamp=datetime.now(tz=UTC),
                            raw={"type": "assistant"},
                        )
                    )
                raise error

        mgr = SubprocessManager(max_concurrent=4, adapter=EmitThenFailAdapter(), db_path=db_ready)  # type: ignore[arg-type]
        with pytest.raises(CLIAdapterError):
            await mgr.dispatch(
                story_id="story-test",
                phase="dev",
                role="developer",
                cli_tool="claude",
                prompt="test",
            )

        db = await get_connection(db_ready)
        cursor = await db.execute(
            "SELECT last_activity_type, last_activity_summary "
            "FROM tasks WHERE story_id = 'story-test'"
        )
        row = await cursor.fetchone()
        await db.close()
        assert row is not None
        assert row[0] == "text"
        assert "正在处理" in row[1]
