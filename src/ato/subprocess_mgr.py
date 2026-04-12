"""subprocess_mgr — 子进程管理器。

管理 CLI agent 的并发调度、PID 注册、tasks/cost_log 持久化和自动重试。
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import structlog
import structlog.contextvars

from ato.adapters.base import BaseAdapter, cleanup_process
from ato.models.schemas import (
    AdapterResult,
    ClaudeOutput,
    CLIAdapterError,
    CodexOutput,
    CostLogRecord,
    ProgressCallback,
    ProgressEvent,
    TaskRecord,
    WorktreeFinalizeResult,
)

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

CLITool = Literal["claude", "codex"]

_SIDECAR_WAIT_TIMEOUT = 30
"""等待 sidecar 文件出现的最长秒数。"""

_SIDECAR_POLL_INTERVAL = 0.5
"""轮询 sidecar 文件的间隔秒数。"""

_FINALIZE_GIT_TIMEOUT = 30
"""Finalize 结果验证 git 命令超时时间。"""

_TERMINAL_FINALIZER_TIMEOUT = 10
"""Terminal finalizer 超时：adapter 返回后 DB 落库的最大允许秒数。"""

_ACTIVITY_FLUSH_TIMEOUT = 3
"""终态路径中 activity flush 的最大允许秒数。"""


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
        adapter: BaseAdapter | None = None,
        adapters: dict[CLITool, BaseAdapter] | None = None,
        db_path: Path,
    ) -> None:
        if adapter is None and not adapters:
            raise ValueError("SubprocessManager requires adapter or adapters")
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._adapter = adapter
        self._adapters = dict(adapters) if adapters is not None else None
        self._db_path = db_path
        self._running: dict[int, RunningTask] = {}

    @property
    def running(self) -> dict[int, RunningTask]:
        """当前运行中的 subprocess 信息（PID → RunningTask）。"""
        return self._running

    def _resolve_adapter(self, cli_tool: CLITool) -> BaseAdapter:
        """Return the adapter that should execute the given cli_tool."""
        if self._adapters is not None:
            try:
                return self._adapters[cli_tool]
            except KeyError as exc:
                raise ValueError(f"No adapter configured for cli_tool={cli_tool}") from exc
        assert self._adapter is not None
        return self._adapter

    async def dispatch(
        self,
        *,
        story_id: str,
        phase: str,
        role: str,
        cli_tool: CLITool,
        prompt: str,
        options: dict[str, Any] | None = None,
        context_briefing: str | None = None,
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
            clear_task_command_events,
            get_connection,
            insert_task,
            insert_task_command_event,
            update_task_activity,
            update_task_status,
        )
        from ato.test_command_harness import build_test_command_env

        if task_id is None:
            task_id = str(uuid.uuid4())

        dispatch_options = dict(options or {})
        harness_enabled = phase in {"qa_testing", "regression"}
        if harness_enabled:
            base_env = dispatch_options.get("env")
            normalized_env = (
                {str(key): str(value) for key, value in base_env.items()}
                if isinstance(base_env, dict)
                else None
            )
            dispatch_options["env"] = build_test_command_env(
                db_path=self._db_path,
                task_id=task_id,
                phase=phase,
                base_env=normalized_env,
            )

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
            if harness_enabled and cli_tool == "codex":
                raw = event.raw if isinstance(event.raw, dict) else None
                item = raw.get("item", {}) if raw and raw.get("type") == "item.completed" else {}
                call = item.get("call", {}) if isinstance(item, dict) else {}
                command = str(call.get("command", "")).strip() if isinstance(call, dict) else ""
                if item.get("type") == "command_execution" and command:
                    try:
                        db_conn = await get_connection(self._db_path)
                        try:
                            await insert_task_command_event(
                                db_conn,
                                task_id=task_id,
                                phase=phase,
                                record_type="observed",
                                command=command,
                                created_at=event.timestamp,
                            )
                        finally:
                            await db_conn.close()
                    except Exception:
                        logger.warning("command_ledger_observed_write_failed", exc_info=True)
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
                    context_briefing=context_briefing,
                    started_at=now,
                )
                db = await get_connection(self._db_path)
                try:
                    await insert_task(db, task)
                    if harness_enabled:
                        await clear_task_command_events(db, task_id, commit=False)
                    await db.commit()
                finally:
                    await db.close()
            else:
                # Fix #1: 重试场景——复用 task_id，重置为 running
                # 清空上一次失败留下的终态字段，保持 tasks 表为干净的当前态
                update_fields: dict[str, Any] = {
                    "started_at": now,
                    "pid": None,
                    "exit_code": None,
                    "error_message": None,
                    "completed_at": None,
                    "duration_ms": None,
                    "cost_usd": None,
                    "last_activity_type": None,
                    "last_activity_summary": None,
                    "text_result": None,
                }
                if context_briefing is not None:
                    update_fields["context_briefing"] = context_briefing
                db = await get_connection(self._db_path)
                try:
                    await update_task_status(
                        db,
                        task_id,
                        "running",
                        **update_fields,
                    )
                    if harness_enabled:
                        await clear_task_command_events(db, task_id, commit=False)
                    await db.commit()
                finally:
                    await db.close()

            logger.info("dispatch_started", story_id=story_id, phase=phase)

            # --- Adapter 调用 ---
            adapter_result: AdapterResult | None = None
            adapter_exc: CLIAdapterError | None = None
            try:
                try:
                    adapter_result = await self._resolve_adapter(cli_tool).execute(
                        prompt,
                        dispatch_options,
                        on_process_start=_on_process_start,
                        on_progress=_progress_wrapper,
                    )
                except CLIAdapterError as exc:
                    adapter_exc = exc

                # --- Terminal Finalizer (bounded) ---
                # 1. Cancel delayed activity flush (best-effort)
                if delayed_flush_task and not delayed_flush_task.done():
                    delayed_flush_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await delayed_flush_task

                # 2. Best-effort activity flush (AC2: 失败只记 warning，不阻塞终态)
                try:
                    await asyncio.wait_for(
                        _flush_latest_activity(), timeout=_ACTIVITY_FLUSH_TIMEOUT
                    )
                except Exception:
                    logger.warning("terminal_activity_flush_failed", exc_info=True)

                # 3. Terminal DB writes with timeout boundary (AC1)
                try:
                    async with asyncio.timeout(_TERMINAL_FINALIZER_TIMEOUT):
                        if adapter_exc is not None:
                            await self._finalize_failure(
                                task_id, story_id, phase, role, cli_tool, adapter_exc
                            )
                        else:
                            assert adapter_result is not None
                            await self._finalize_success(
                                task_id, story_id, phase, role, cli_tool, adapter_result
                            )
                except Exception as finalize_exc:
                    # AC3: Fallback raw SQL — 保证 task 从 running 收敛为终态
                    if not isinstance(finalize_exc, asyncio.CancelledError):
                        logger.error(
                            "terminal_finalizer_error",
                            task_id=task_id,
                            exc_info=True,
                        )
                        await self._fallback_update_task(task_id, adapter_exc, finalize_exc)
                    else:
                        # CancelledError: 仍尝试 fallback，然后重新抛出
                        await self._fallback_update_task(task_id, adapter_exc, finalize_exc)
                        raise
            finally:
                # AC1: _unregister_running 在 outer finally，不依赖 DB 写入成功
                self._unregister_running(task_id)
                # 清理后台 flush task
                if delayed_flush_task and not delayed_flush_task.done():
                    delayed_flush_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await delayed_flush_task

            if adapter_exc is not None:
                raise adapter_exc
            assert adapter_result is not None
            return adapter_result

    async def _finalize_success(
        self,
        task_id: str,
        story_id: str,
        phase: str,
        role: str,
        cli_tool: CLITool,
        result: AdapterResult,
    ) -> None:
        """成功路径的终态落库：update task + insert cost_log。"""
        from ato.models.db import get_connection, insert_cost_log, update_task_status

        completed_at = datetime.now(tz=UTC)
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

    async def _finalize_failure(
        self,
        task_id: str,
        story_id: str,
        phase: str,
        role: str,
        cli_tool: CLITool,
        exc: CLIAdapterError,
    ) -> None:
        """失败路径的终态落库：update task failed + insert cost_log。"""
        from ato.models.db import get_connection, insert_cost_log, update_task_status

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

    async def _fallback_update_task(
        self,
        task_id: str,
        adapter_exc: CLIAdapterError | None,
        finalize_exc: BaseException,
        *,
        force_status: str | None = None,
    ) -> None:
        """AC3: 最小 raw SQL fallback — 保证 task 从 running 收敛为终态。

        不创建 CostLogRecord，不等待外部 IO，用短事务写最少字段。
        """
        import aiosqlite

        if force_status is not None:
            status = force_status
        else:
            status = "failed" if adapter_exc is not None else "completed"
        if adapter_exc is not None:
            exit_code = adapter_exc.exit_code
        elif force_status == "failed":
            exit_code = -1  # dead PID / forced failure without adapter error
        else:
            exit_code = 0
        error_msg = f"finalizer_fallback: {finalize_exc!r}"
        completed_at = datetime.now(tz=UTC).isoformat()

        try:
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute("PRAGMA journal_mode=WAL")
                await db.execute("PRAGMA busy_timeout=5000")
                await db.execute(
                    "UPDATE tasks SET status = ?, exit_code = ?, error_message = ?, "
                    "completed_at = ? WHERE task_id = ? AND status = 'running'",
                    (status, exit_code, error_msg, completed_at, task_id),
                )
                await db.commit()
            logger.warning(
                "fallback_update_task_applied",
                task_id=task_id,
                status=status,
                original_error=repr(finalize_exc),
            )
        except Exception:
            logger.error(
                "fallback_update_task_failed",
                task_id=task_id,
                exc_info=True,
            )

    async def sweep_dead_workers(self) -> int:
        """运行期 dead PID watchdog：检测并清理已退出的 worker。

        复用 recovery 的 PID 检测语义：
        - ESRCH → dead（进程不存在）
        - EPERM → alive（权限不足但进程存在）

        Returns:
            清理的 dead worker 数量。
        """
        dead_pids: list[int] = []
        for pid in list(self._running):
            if not self._is_pid_alive(pid):
                dead_pids.append(pid)

        for pid in dead_pids:
            rt = self._running.pop(pid)
            logger.warning(
                "dead_worker_detected",
                task_id=rt.task_id,
                story_id=rt.story_id,
                phase=rt.phase,
                pid=pid,
            )
            # 更新 DB：将 task 从 running 收敛为 failed
            await self._fallback_update_task(
                rt.task_id,
                adapter_exc=None,
                finalize_exc=RuntimeError(f"dead worker PID {pid} detected by watchdog"),
                force_status="failed",
            )

        return len(dead_pids)

    @staticmethod
    def _is_pid_alive(pid: int) -> bool:
        """检测 PID 是否存活。复用 recovery._is_pid_alive 的语义。"""
        try:
            os.kill(pid, 0)
            return True
        except OSError as e:
            if e.errno == errno.ESRCH:
                return False
            if e.errno == errno.EPERM:
                return True  # 权限不足但进程存在
            raise

    async def dispatch_with_retry(
        self,
        *,
        story_id: str,
        phase: str,
        role: str,
        cli_tool: CLITool,
        prompt: str,
        options: dict[str, Any] | None = None,
        context_briefing: str | None = None,
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
                    context_briefing=context_briefing,
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

    async def dispatch_finalize(
        self,
        story_id: str,
        worktree_path: str,
        story_summary: str,
        *,
        dirty_files: list[str] | None = None,
    ) -> WorktreeFinalizeResult:
        """Dispatch a single finalize agent and verify the result with local git commands."""
        pre_rc, pre_head_out, pre_err = await self._run_finalize_git(
            worktree_path,
            "rev-parse",
            "HEAD",
        )
        pre_head = pre_head_out.strip() if pre_rc == 0 else None
        if pre_head is None:
            return WorktreeFinalizeResult.model_validate(
                {
                    "story_id": story_id,
                    "committed": False,
                    "pre_head_sha": None,
                    "error": f"git rev-parse HEAD failed before finalize: {pre_err}",
                }
            )

        dirty_block = "\n".join(f"- {path}" for path in (dirty_files or []))
        if not dirty_block:
            dirty_block = "- None reported"

        prompt = f"""\
You are finalizing the story worktree before an ATO boundary gate.

Story: {story_id}
Summary: {story_summary}
Worktree: {worktree_path}

Dirty files reported by the preflight gate:
{dirty_block}

## Required Outcome
Commit all legitimate story worktree changes so `git status --porcelain=v1 -uall`
is empty and the story branch has a non-empty committed diff against main.

## Allowed Git-Mutating Commands
- git add -A
- git commit

## Forbidden Commands
Do NOT run git reset, git checkout, git switch, git stash, git clean, git rebase,
or git merge.

The commit message MUST start with `{story_id}: `.
Do not edit files except if needed to fix clearly broken generated artifacts introduced
by this finalize attempt. The normal path is commit only.
"""

        dispatch_error: str | None = None
        try:
            await self.dispatch_with_retry(
                story_id=story_id,
                phase="worktree_finalize",
                role="developer",
                cli_tool="claude",
                prompt=prompt,
                options={"cwd": worktree_path},
                max_retries=0,
            )
        except CLIAdapterError as exc:
            dispatch_error = str(exc)

        post_rc, post_head_out, post_err = await self._run_finalize_git(
            worktree_path,
            "rev-parse",
            "HEAD",
        )
        post_head = post_head_out.strip() if post_rc == 0 else None
        if post_head is None:
            return WorktreeFinalizeResult.model_validate(
                {
                    "story_id": story_id,
                    "committed": False,
                    "pre_head_sha": pre_head,
                    "post_head_sha": None,
                    "error": (
                        dispatch_error or f"git rev-parse HEAD failed after finalize: {post_err}"
                    ),
                }
            )

        committed = pre_head != post_head
        commit_message: str | None = None
        files_changed: list[str] = []
        if committed:
            msg_rc, msg_out, msg_err = await self._run_finalize_git(
                worktree_path,
                "log",
                "-1",
                "--pretty=%B",
            )
            if msg_rc == 0:
                commit_message = msg_out.strip()
            elif dispatch_error is None:
                dispatch_error = f"git log -1 --pretty=%B failed: {msg_err}"

            diff_rc, diff_out, diff_err = await self._run_finalize_git(
                worktree_path,
                "diff",
                "--name-only",
                f"{pre_head}..{post_head}",
            )
            if diff_rc == 0:
                files_changed = [line for line in diff_out.splitlines() if line]
            elif dispatch_error is None:
                dispatch_error = f"git diff --name-only {pre_head}..{post_head} failed: {diff_err}"

        return WorktreeFinalizeResult.model_validate(
            {
                "story_id": story_id,
                "committed": committed,
                "pre_head_sha": pre_head,
                "post_head_sha": post_head,
                "commit_sha": post_head if committed else None,
                "commit_message": commit_message,
                "files_changed": files_changed,
                "error": dispatch_error,
            }
        )

    async def _run_finalize_git(
        self,
        worktree_path: str,
        *args: str,
    ) -> tuple[int, str, str]:
        """Run a git command in a finalize worktree for result verification."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "git",
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=worktree_path,
            )
        except OSError as exc:
            return 127, "", str(exc)

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=_FINALIZE_GIT_TIMEOUT,
            )
        except TimeoutError:
            await cleanup_process(proc)
            return 124, "", f"git {' '.join(args)} timed out"
        finally:
            await cleanup_process(proc)

        return (
            proc.returncode or 0,
            stdout_bytes.decode() if stdout_bytes else "",
            stderr_bytes.decode() if stderr_bytes else "",
        )

    async def dispatch_group(
        self,
        *,
        tasks: list[TaskRecord],
        prompt: str,
        cli_tool: CLITool,
        options: dict[str, Any] | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> AdapterResult:
        """单会话批量 dispatch：一个 CLI 会话处理多个 story。

        所有 tasks 共享同一个 group_id，使用一个 semaphore slot。
        第一个 task 作为"主 task"绑定 PID 和进度追踪。
        """
        from ato.models.db import (
            get_connection,
            insert_cost_log,
            update_task_status,
        )

        if not tasks:
            msg = "dispatch_group requires at least one task"
            raise ValueError(msg)

        primary_task = tasks[0]

        structlog.contextvars.bind_contextvars(
            group_id=primary_task.group_id,
            phase=primary_task.phase,
            cli_tool=cli_tool,
            story_count=len(tasks),
        )

        logger.info(
            "dispatch_group_waiting_semaphore",
            story_ids=[t.story_id for t in tasks],
            phase=primary_task.phase,
        )

        async def _on_process_start(proc: asyncio.subprocess.Process) -> None:
            pid = proc.pid
            if pid is not None:
                self._running[pid] = RunningTask(
                    task_id=primary_task.task_id,
                    story_id=primary_task.story_id,
                    phase=primary_task.phase,
                    pid=pid,
                )
                # 更新所有 tasks 的 PID
                for t in tasks:
                    db2 = await get_connection(self._db_path)
                    try:
                        await update_task_status(db2, t.task_id, "running", pid=pid)
                    finally:
                        await db2.close()
                logger.info(
                    "dispatch_group_pid_registered",
                    pid=pid,
                    task_ids=[t.task_id for t in tasks],
                )

        async with self._semaphore:
            # 标记所有 tasks 为 running
            now = datetime.now(tz=UTC)
            for t in tasks:
                db = await get_connection(self._db_path)
                try:
                    await update_task_status(
                        db,
                        t.task_id,
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

            logger.info(
                "dispatch_group_started",
                story_ids=[t.story_id for t in tasks],
                phase=primary_task.phase,
            )

            try:
                result = await self._resolve_adapter(cli_tool).execute(
                    prompt,
                    options,
                    on_process_start=_on_process_start,
                    on_progress=on_progress,
                )
            except CLIAdapterError:
                # 标记所有 tasks 失败
                completed_at = datetime.now(tz=UTC)
                for t in tasks:
                    db = await get_connection(self._db_path)
                    try:
                        await update_task_status(
                            db,
                            t.task_id,
                            "failed",
                            completed_at=completed_at,
                            error_message="group_dispatch_adapter_error",
                        )
                    finally:
                        await db.close()
                self._unregister_running(primary_task.task_id)
                raise

            # 会话完成：先持久化主 task 的结果和成本
            completed_at = datetime.now(tz=UTC)
            per_story_cost = result.cost_usd / len(tasks) if result.cost_usd else 0.0
            for t in tasks:
                db = await get_connection(self._db_path)
                try:
                    await update_task_status(
                        db,
                        t.task_id,
                        "completed" if result.status == "success" else "failed",
                        completed_at=completed_at,
                        exit_code=result.exit_code,
                        cost_usd=per_story_cost,
                        duration_ms=result.duration_ms,
                        error_message=result.error_message,
                        text_result=result.text_result,
                    )
                finally:
                    await db.close()

            # 记录成本日志（整体一条）
            cost_record = CostLogRecord(
                cost_log_id=str(uuid.uuid4()),
                story_id=primary_task.story_id,
                task_id=primary_task.task_id,
                phase=primary_task.phase,
                role=primary_task.role,
                cli_tool=cli_tool,
                model=None,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                cost_usd=result.cost_usd,
                duration_ms=result.duration_ms,
                created_at=completed_at,
            )
            db = await get_connection(self._db_path)
            try:
                await insert_cost_log(db, cost_record)
            finally:
                await db.close()

            self._unregister_running(primary_task.task_id)
            return result

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
