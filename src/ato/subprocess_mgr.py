"""subprocess_mgr — 子进程管理器。

管理 CLI agent 的并发调度、PID 注册、tasks/cost_log 持久化和自动重试。
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import structlog
import structlog.contextvars

from ato.adapters.base import BaseAdapter
from ato.models.schemas import (
    AdapterResult,
    ClaudeOutput,
    CLIAdapterError,
    CodexOutput,
    CostLogRecord,
    TaskRecord,
)

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

CLITool = Literal["claude", "codex"]


@dataclass
class RunningTask:
    """正在运行的 subprocess 元数据。"""

    task_id: str
    story_id: str
    phase: str
    pid: int
    started_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))


class SubprocessManager:
    """CLI agent 子进程管理器。

    职责：
    - 通过 asyncio.Semaphore 控制并发 agent 数
    - 维护 running 字典用于崩溃恢复
    - 持久化 tasks / cost_log 到 SQLite
    - 自动重试 retryable 错误（最多 1 次）
    """

    def __init__(
        self,
        *,
        max_concurrent: int,
        adapter: BaseAdapter,
        db_path: Path,
    ) -> None:
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._adapter = adapter
        self._db_path = db_path
        self._running: dict[int, RunningTask] = {}

    @property
    def running(self) -> dict[int, RunningTask]:
        """当前运行中的 subprocess 信息（PID → RunningTask）。"""
        return self._running

    async def dispatch(
        self,
        *,
        story_id: str,
        phase: str,
        role: str,
        cli_tool: CLITool,
        prompt: str,
        options: dict[str, Any] | None = None,
        task_id: str | None = None,
        is_retry: bool = False,
    ) -> AdapterResult:
        """调度一次 CLI agent 调用。

        获取 semaphore → 创建/更新 TaskRecord → 启动 adapter → PID 注册 → 持久化结果。

        Args:
            task_id: 外部提供的 task_id，用于重试场景复用同一逻辑任务。
                     为 None 时自动生成。
            is_retry: True 表示此次调用是对同一 task_id 的重试（UPDATE 而非 INSERT）。
        """
        from ato.models.db import get_connection, insert_cost_log, insert_task, update_task_status

        if task_id is None:
            task_id = str(uuid.uuid4())

        structlog.contextvars.bind_contextvars(
            story_id=story_id, phase=phase, cli_tool=cli_tool, task_id=task_id
        )

        logger.info("dispatch_waiting_semaphore", story_id=story_id, phase=phase)

        async def _on_process_start(proc: asyncio.subprocess.Process) -> None:
            pid = proc.pid
            if pid is not None:
                self._running[pid] = RunningTask(
                    task_id=task_id,
                    story_id=story_id,
                    phase=phase,
                    pid=pid,
                )
                db2 = await get_connection(self._db_path)
                try:
                    await update_task_status(db2, task_id, "running", pid=pid)
                finally:
                    await db2.close()
                logger.info("pid_registered", pid=pid)

        # Fix #2: 先获取 semaphore，再创建/更新 TaskRecord 为 running
        async with self._semaphore:
            now = datetime.now(tz=UTC)
            if not is_retry:
                task = TaskRecord(
                    task_id=task_id,
                    story_id=story_id,
                    phase=phase,
                    role=role,
                    cli_tool=cli_tool,
                    status="running",
                    started_at=now,
                )
                db = await get_connection(self._db_path)
                try:
                    await insert_task(db, task)
                finally:
                    await db.close()
            else:
                # Fix #1: 重试场景——复用 task_id，重置为 running
                # 清空上一次失败留下的终态字段，保持 tasks 表为干净的当前态
                db = await get_connection(self._db_path)
                try:
                    await update_task_status(
                        db,
                        task_id,
                        "running",
                        started_at=now,
                        pid=None,
                        exit_code=None,
                        error_message=None,
                        completed_at=None,
                        duration_ms=None,
                        cost_usd=None,
                    )
                finally:
                    await db.close()

            logger.info("dispatch_started", story_id=story_id, phase=phase)

            try:
                result = await self._adapter.execute(
                    prompt,
                    options,
                    on_process_start=_on_process_start,
                )
            except CLIAdapterError as exc:
                completed_at = datetime.now(tz=UTC)
                db = await get_connection(self._db_path)
                try:
                    await update_task_status(
                        db,
                        task_id,
                        "failed",
                        exit_code=exc.exit_code,
                        error_message=str(exc),
                        completed_at=completed_at,
                    )
                    await insert_cost_log(
                        db,
                        CostLogRecord(
                            cost_log_id=str(uuid.uuid4()),
                            story_id=story_id,
                            task_id=task_id,
                            cli_tool=cli_tool,
                            phase=phase,
                            role=role,
                            input_tokens=0,
                            output_tokens=0,
                            cost_usd=0.0,
                            exit_code=exc.exit_code,
                            error_category=exc.category.value,
                            created_at=completed_at,
                        ),
                    )
                finally:
                    await db.close()
                self._unregister_running(task_id)
                raise
            else:
                completed_at = datetime.now(tz=UTC)
                # Fix #4: 从 ClaudeOutput/CodexOutput 提取 cache_read_input_tokens 和 model
                cache_tokens = 0
                model_name: str | None = None
                if isinstance(result, ClaudeOutput):
                    cache_tokens = result.cache_read_input_tokens
                    if result.model_usage and isinstance(result.model_usage, dict):
                        model_name = result.model_usage.get("model")
                elif isinstance(result, CodexOutput):
                    cache_tokens = result.cache_read_input_tokens
                    model_name = result.model_name

                db = await get_connection(self._db_path)
                try:
                    await update_task_status(
                        db,
                        task_id,
                        "completed",
                        exit_code=result.exit_code,
                        cost_usd=result.cost_usd,
                        duration_ms=result.duration_ms,
                        completed_at=completed_at,
                        error_message=None,
                    )
                    await insert_cost_log(
                        db,
                        CostLogRecord(
                            cost_log_id=str(uuid.uuid4()),
                            story_id=story_id,
                            task_id=task_id,
                            cli_tool=cli_tool,
                            model=model_name,
                            phase=phase,
                            role=role,
                            input_tokens=result.input_tokens,
                            output_tokens=result.output_tokens,
                            cache_read_input_tokens=cache_tokens,
                            cost_usd=result.cost_usd,
                            duration_ms=result.duration_ms,
                            session_id=result.session_id,
                            exit_code=result.exit_code,
                            created_at=completed_at,
                        ),
                    )
                finally:
                    await db.close()
                self._unregister_running(task_id)
                return result

    async def dispatch_with_retry(
        self,
        *,
        story_id: str,
        phase: str,
        role: str,
        cli_tool: CLITool,
        prompt: str,
        options: dict[str, Any] | None = None,
        max_retries: int = 1,
    ) -> AdapterResult:
        """带自动重试的调度。retryable 错误最多重试 max_retries 次。

        Fix #1: task_id 在首次调用时生成，重试时传入同一 task_id。
        一个逻辑任务 → 一条 tasks 记录 + N 条 cost_log 记录。
        """
        task_id = str(uuid.uuid4())
        last_exc: CLIAdapterError | None = None
        for attempt in range(max_retries + 1):
            try:
                return await self.dispatch(
                    story_id=story_id,
                    phase=phase,
                    role=role,
                    cli_tool=cli_tool,
                    prompt=prompt,
                    options=options,
                    task_id=task_id,
                    is_retry=attempt > 0,
                )
            except CLIAdapterError as exc:
                last_exc = exc
                if not exc.retryable or attempt >= max_retries:
                    raise
                logger.warning(
                    "dispatch_retry",
                    attempt=attempt + 1,
                    category=exc.category.value,
                    task_id=task_id,
                )
        # 理论上不会到这里，但 mypy 需要
        assert last_exc is not None
        raise last_exc

    def _unregister_running(self, task_id: str) -> None:
        """从 running 字典中移除已完成的 task。"""
        pids_to_remove = [pid for pid, rt in self._running.items() if rt.task_id == task_id]
        for pid in pids_to_remove:
            del self._running[pid]
