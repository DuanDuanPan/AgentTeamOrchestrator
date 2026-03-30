"""subprocess_mgr — 子进程管理器。

管理 CLI agent 的并发调度、PID 注册、tasks/cost_log 持久化和自动重试。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
import time
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
    ProgressCallback,
    ProgressEvent,
    TaskRecord,
)

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

CLITool = Literal["claude", "codex"]

_SIDECAR_WAIT_TIMEOUT = 30
"""等待 sidecar 文件出现的最长秒数。"""

_SIDECAR_POLL_INTERVAL = 0.5
"""轮询 sidecar 文件的间隔秒数。"""


def _launch_terminal_session(
    cmd: list[str],
    worktree_path: Path,
    sidecar_path: Path,
    base_commit: str,
    *,
    session_id: str | None = None,
) -> None:
    """在独立终端窗口启动 interactive session。

    通过临时 shell 脚本文件启动，避免在 osascript/shell 字符串中内联
    prompt 文本（可能含引号、换行等特殊字符）。

    Args:
        cmd: 要执行的命令参数列表。
        worktree_path: 工作目录路径。
        sidecar_path: sidecar 元数据文件路径。
        base_commit: 启动前的 HEAD commit。
        session_id: 已知的 session_id（resume 场景），写入 sidecar 保留。
    """
    import shlex
    import stat
    import subprocess
    import tempfile

    sidecar_path.parent.mkdir(parents=True, exist_ok=True)

    # session_id JSON 值
    sid_json = "null" if session_id is None else json.dumps(session_id)

    # 使用 shlex.join 安全转义每个参数
    cmd_str = shlex.join(cmd)

    # 写临时 shell 脚本（避免在 osascript/shell 字符串中内联自由文本）
    script_dir = sidecar_path.parent
    script_dir.mkdir(parents=True, exist_ok=True)
    # 安全策略：外部输入（base_commit、session_id）通过 shlex.quote 包裹的
    # 单引号赋值给 shell 变量（阻止赋值时展开），然后在不带引号的 here-doc 中
    # 引用变量。shell 变量展开不递归——结果值不会被 re-parse 为命令替换，
    # 所以即使值中含 $(...) 也只会作为字面量写入。
    # 同时 $$ 和 $(date ...) 在 here-doc 原文中正常展开。
    base_json = json.dumps(base_commit)
    safe_base = shlex.quote(base_json)
    safe_sid = shlex.quote(sid_json)
    # sidecar_path / worktree_path 必须用绝对路径，否则 cd 后相对路径会指向错误位置
    abs_sidecar = str(sidecar_path.resolve())
    abs_worktree = str(worktree_path.resolve())
    script_content = (
        f"#!/bin/bash\n"
        f"_ato_bc={safe_base}\n"
        f"_ato_sid={safe_sid}\n"
        f"cd {shlex.quote(abs_worktree)}\n"
        f"cat > {shlex.quote(abs_sidecar)} <<SIDECAR_EOF\n"
        f'{{"pid": $$, '
        f'"started_at": "$(date -u +%Y-%m-%dT%H:%M:%S+00:00)", '
        f'"base_commit": $_ato_bc, '
        f'"session_id": $_ato_sid}}\n'
        f"SIDECAR_EOF\n"
        f"exec {cmd_str}\n"
    )
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".sh",
        dir=str(script_dir),
        delete=False,
        prefix="ato-session-",
    ) as script_file:
        script_file.write(script_content)
        script_path = script_file.name
    # 设置可执行权限
    Path(script_path).chmod(stat.S_IRWXU)

    if sys.platform == "darwin":
        subprocess.Popen(
            ["open", "-a", "Terminal", script_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        subprocess.Popen(
            ["xterm", "-e", script_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


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
        on_progress: ProgressCallback | None = None,
    ) -> AdapterResult:
        """调度一次 CLI agent 调用。

        获取 semaphore → 创建/更新 TaskRecord → 启动 adapter → PID 注册 → 持久化结果。

        Args:
            task_id: 外部提供的 task_id，用于重试场景复用同一逻辑任务。
                     为 None 时自动生成。
            is_retry: True 表示此次调用是对同一 task_id 的重试（UPDATE 而非 INSERT）。
            on_progress: 实时进度回调，透传给 adapter.execute()。
        """
        from ato.models.db import (
            get_connection,
            insert_cost_log,
            insert_task,
            update_task_activity,
            update_task_status,
        )

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

        # --- latest-only, serialized activity writer ---
        throttle_interval: float = 1.0
        last_flush_mono: float = 0.0
        latest_event: ProgressEvent | None = None
        delayed_flush_task: asyncio.Task[None] | None = None

        async def _flush_latest_activity() -> None:
            nonlocal latest_event, last_flush_mono
            event = latest_event
            if event is None:
                return
            latest_event = None
            try:
                db_conn = await get_connection(self._db_path)
                try:
                    await update_task_activity(
                        db_conn,
                        task_id,
                        activity_type=event.event_type,
                        activity_summary=event.summary,
                    )
                finally:
                    await db_conn.close()
                last_flush_mono = time.monotonic()
            except Exception:
                logger.warning("progress_db_write_failed", exc_info=True)

        async def _delayed_flush() -> None:
            nonlocal latest_event
            while latest_event is not None:
                delay = max(0.0, throttle_interval - (time.monotonic() - last_flush_mono))
                if delay > 0:
                    await asyncio.sleep(delay)
                await _flush_latest_activity()

        async def _progress_wrapper(event: ProgressEvent) -> None:
            nonlocal latest_event, delayed_flush_task
            latest_event = event
            if event.event_type in ("result", "error"):
                # Terminal events: cancel pending flush and force-flush now
                if delayed_flush_task and not delayed_flush_task.done():
                    delayed_flush_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await delayed_flush_task
                await _flush_latest_activity()
            elif delayed_flush_task is None or delayed_flush_task.done():
                delayed_flush_task = asyncio.create_task(_delayed_flush())
            if on_progress is not None:
                await on_progress(event)

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
                        last_activity_type=None,
                        last_activity_summary=None,
                        text_result=None,
                    )
                finally:
                    await db.close()

            logger.info("dispatch_started", story_id=story_id, phase=phase)

            try:
                result = await self._adapter.execute(
                    prompt,
                    options,
                    on_process_start=_on_process_start,
                    on_progress=_progress_wrapper,
                )
            except CLIAdapterError as exc:
                # 终态前强制 flush 最新 activity（Fix: Codex turn_end 不在终态集合）
                if delayed_flush_task and not delayed_flush_task.done():
                    delayed_flush_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await delayed_flush_task
                await _flush_latest_activity()

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
                # 终态前强制 flush 最新 activity（Fix: Codex turn_end 不在终态集合）
                if delayed_flush_task and not delayed_flush_task.done():
                    delayed_flush_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await delayed_flush_task
                await _flush_latest_activity()

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
                        text_result=result.text_result,
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
            finally:
                # 清理后台 flush task，防止悬挂
                if delayed_flush_task and not delayed_flush_task.done():
                    delayed_flush_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await delayed_flush_task

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
        task_id: str | None = None,
        is_retry: bool = False,
        on_progress: ProgressCallback | None = None,
    ) -> AdapterResult:
        """带自动重试的调度。retryable 错误最多重试 max_retries 次。

        Fix #1: task_id 在首次调用时生成，重试时传入同一 task_id。
        crash recovery 可传入既有 task_id，并从首次尝试起按 retry/update 语义续跑。
        一个逻辑任务 → 一条 tasks 记录 + N 条 cost_log 记录。
        """
        if task_id is None:
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
                    is_retry=is_retry or attempt > 0,
                    on_progress=on_progress,
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

    async def dispatch_interactive(
        self,
        *,
        story_id: str,
        phase: str,
        role: str,
        prompt: str,
        worktree_path: Path,
        base_commit: str,
        ato_dir: Path,
        session_id: str | None = None,
    ) -> str:
        """启动 Interactive Session，非阻塞返回 task_id。

        在独立终端窗口中启动 claude CLI，等待 sidecar 文件写入后
        注册 PID + started_at 到 tasks 表。

        Args:
            story_id: Story 唯一标识。
            phase: 当前阶段名。
            role: 角色名。
            prompt: 发送给 CLI 的提示文本。
            worktree_path: Story 的 worktree 路径。
            base_commit: 启动前 worktree 的 HEAD commit。
            ato_dir: .ato 目录路径。
            session_id: 若提供则使用 --resume 续接。

        Returns:
            注册到 DB 的 task_id。
        """
        from ato.adapters.claude_cli import build_interactive_command
        from ato.models.db import get_connection, insert_task

        task_id = str(uuid.uuid4())
        sidecar_path = ato_dir / "sessions" / f"{story_id}.json"

        structlog.contextvars.bind_contextvars(story_id=story_id, phase=phase, task_id=task_id)

        # 续接 fallback：显式参数 → 已有 sidecar 的 session_id → None（fresh）
        # 空字符串视为无效，降级为 fresh session
        effective_session_id = session_id or None
        if effective_session_id is None and sidecar_path.exists():
            try:
                existing = json.loads(sidecar_path.read_text())
                effective_session_id = existing.get("session_id") or None
            except (json.JSONDecodeError, OSError):
                pass  # sidecar 损坏或不可读，降级为 fresh session

        # 构建 interactive 命令
        cmd = build_interactive_command(prompt, session_id=effective_session_id)

        # 启动终端 session（保留 session_id 到 sidecar）
        _launch_terminal_session(
            cmd,
            worktree_path,
            sidecar_path,
            base_commit,
            session_id=effective_session_id,
        )

        # 等待 sidecar 文件出现并读取元数据
        sidecar_data = await self._wait_for_sidecar(sidecar_path)

        pid = sidecar_data.get("pid")
        started_at_str = sidecar_data.get("started_at")

        now = datetime.now(tz=UTC)
        started_at = datetime.fromisoformat(started_at_str) if started_at_str else now

        # 注册 task 到 DB
        task = TaskRecord(
            task_id=task_id,
            story_id=story_id,
            phase=phase,
            role=role,
            cli_tool="claude",
            status="running",
            pid=pid,
            started_at=started_at,
        )
        db = await get_connection(self._db_path)
        try:
            await insert_task(db, task)
        finally:
            await db.close()

        # 注册到 running 字典
        if pid is not None:
            self._running[pid] = RunningTask(
                task_id=task_id,
                story_id=story_id,
                phase=phase,
                pid=pid,
                started_at=started_at,
            )

        logger.info(
            "interactive_session_started",
            story_id=story_id,
            phase=phase,
            pid=pid,
            sidecar_path=str(sidecar_path),
        )
        return task_id

    async def _wait_for_sidecar(self, sidecar_path: Path) -> dict[str, Any]:
        """等待 sidecar 文件出现并读取 JSON 内容。

        Args:
            sidecar_path: sidecar 元数据文件路径。

        Returns:
            解析后的 sidecar JSON 数据。

        Raises:
            TimeoutError: 超过 _SIDECAR_WAIT_TIMEOUT 秒仍无文件。
        """
        deadline = asyncio.get_event_loop().time() + _SIDECAR_WAIT_TIMEOUT
        while asyncio.get_event_loop().time() < deadline:
            if sidecar_path.exists():
                try:
                    content = sidecar_path.read_text()
                    data: dict[str, Any] = json.loads(content)
                    return data
                except (json.JSONDecodeError, OSError):
                    pass  # 文件尚未完全写入
            await asyncio.sleep(_SIDECAR_POLL_INTERVAL)
        msg = f"Sidecar file not created within {_SIDECAR_WAIT_TIMEOUT}s: {sidecar_path}"
        raise TimeoutError(msg)

    def _unregister_running(self, task_id: str) -> None:
        """从 running 字典中移除已完成的 task。"""
        pids_to_remove = [pid for pid, rt in self._running.items() if rt.task_id == task_id]
        for pid in pids_to_remove:
            del self._running[pid]
