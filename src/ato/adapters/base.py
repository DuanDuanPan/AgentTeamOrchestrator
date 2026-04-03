"""base — 适配器基类接口与 subprocess 工具函数。"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any

from ato.models.schemas import AdapterResult, ProgressCallback

# 进程启动回调类型：SubprocessManager 用此回调在 subprocess 启动后注册 PID
ProcessStartCallback = Callable[[asyncio.subprocess.Process], Awaitable[None]]


def _kill_process_group(proc: asyncio.subprocess.Process) -> None:
    """Kill 整个进程组（含 orphan 子进程）。降级为 proc.kill()。

    当进程以 ``start_new_session=True`` 启动时，PID == PGID，
    ``os.killpg`` 可杀死整个进程组树。
    """
    pid = proc.pid
    if pid is None:
        proc.kill()
        return
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        with contextlib.suppress(ProcessLookupError):
            proc.kill()


async def cleanup_process(
    proc: asyncio.subprocess.Process,
    timeout: int = 5,
) -> None:
    """三阶段清理协议：SIGTERM → wait(timeout) → SIGKILL(pgid) → wait。

    当进程以 ``start_new_session=True`` 启动时，SIGKILL 阶段通过
    ``os.killpg`` 杀死整个进程组（含孤儿子进程），防止 orphan grandchild
    导致 ``proc.wait()`` 永久阻塞。
    """
    if proc.returncode is not None:
        return
    proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout)
        return
    except TimeoutError:
        pass
    _kill_process_group(proc)
    await proc.wait()  # kill 后必须 wait，防止 zombie


_STDERR_MAX_BYTES = 1_048_576  # 1 MB


async def drain_stderr(stderr: asyncio.StreamReader) -> str:
    """后台消费 stderr 全部内容，防止管道缓冲区满导致死锁。

    最多保留 ``_STDERR_MAX_BYTES`` 字节，超出部分仍然读取（避免管道阻塞）但丢弃。
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await stderr.read(4096)
        if not chunk:
            break
        if total < _STDERR_MAX_BYTES:
            keep = chunk[: _STDERR_MAX_BYTES - total]
            chunks.append(keep)
            total += len(keep)
    return b"".join(chunks).decode("utf-8", errors="replace")


class BaseAdapter(ABC):
    """CLI 适配器抽象基类。"""

    @abstractmethod
    async def execute(
        self,
        prompt: str,
        options: dict[str, Any] | None = None,
        *,
        on_process_start: ProcessStartCallback | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> AdapterResult:
        """执行 CLI 命令并返回结构化结果。

        Args:
            prompt: 发送给 CLI 的提示文本。
            options: 额外参数（max_turns, cwd 等）。
            on_process_start: 进程启动后的回调，用于 PID 注册。
            on_progress: 实时进度回调，每个流式事件归一化后调用。
        """
