"""base — 适配器基类接口与 subprocess 工具函数。"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any

from ato.models.schemas import AdapterResult

# 进程启动回调类型：SubprocessManager 用此回调在 subprocess 启动后注册 PID
ProcessStartCallback = Callable[[asyncio.subprocess.Process], Awaitable[None]]


async def cleanup_process(
    proc: asyncio.subprocess.Process,
    timeout: int = 5,
) -> None:
    """三阶段清理协议：SIGTERM → wait(timeout) → SIGKILL → wait。

    所有 subprocess 调用必须在 ``try/finally`` 中调用此函数。
    """
    if proc.returncode is not None:
        return
    proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout)
        return
    except TimeoutError:
        pass
    proc.kill()
    await proc.wait()  # kill 后必须 wait，防止 zombie


class BaseAdapter(ABC):
    """CLI 适配器抽象基类。"""

    @abstractmethod
    async def execute(
        self,
        prompt: str,
        options: dict[str, Any] | None = None,
        *,
        on_process_start: ProcessStartCallback | None = None,
    ) -> AdapterResult:
        """执行 CLI 命令并返回结构化结果。

        Args:
            prompt: 发送给 CLI 的提示文本。
            options: 额外参数（max_turns, cwd 等）。
            on_process_start: 进程启动后的回调，用于 PID 注册。
        """
