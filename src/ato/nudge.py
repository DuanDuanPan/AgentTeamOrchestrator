"""nudge — 外部写入通知机制。

Orchestrator 轮询循环通过 ``Nudge.wait()`` 替代固定 sleep：
- 进程内 writer（TransitionQueue.submit）调用 ``notify()`` 立即唤醒。
- 进程外 writer（TUI / ``ato submit``）通过 ``send_external_nudge()``
  向 Orchestrator PID 发送信号，由信号 handler 转为 ``notify()``。
  具体 transport 封装在本模块，调用点在 Story 2A.3 / 2B.6 接入。
"""

from __future__ import annotations

import asyncio
import os
import signal

import structlog

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


class Nudge:
    """进程内 nudge 通知。

    Orchestrator 端使用 ``wait(timeout)`` 替代 ``asyncio.sleep(interval)``；
    writer 端调用 ``notify()`` 可立即唤醒等待方。
    """

    def __init__(self) -> None:
        self._event = asyncio.Event()

    def notify(self) -> None:
        """唤醒正在 ``wait()`` 的 waiter（如果有）。"""
        self._event.set()

    async def wait(self, timeout: float) -> bool:
        """等待 nudge 或超时。

        Args:
            timeout: 最长等待秒数。

        Returns:
            ``True`` 表示被 ``notify()`` 唤醒，``False`` 表示超时。
        """
        try:
            await asyncio.wait_for(self._event.wait(), timeout=timeout)
            return True
        except TimeoutError:
            return False
        finally:
            self._event.clear()


def send_external_nudge(orchestrator_pid: int) -> None:
    """供外部进程（TUI / ``ato submit``）调用，通知 Orchestrator 立即轮询。

    当前 transport 为 ``SIGUSR1``。Orchestrator 在启动时需要注册对应的
    信号 handler（由 Story 2A.3 实现），将信号转为 ``Nudge.notify()``。

    Args:
        orchestrator_pid: Orchestrator 进程的 PID。

    Raises:
        ProcessLookupError: PID 不存在。
        PermissionError: 无权向目标进程发送信号。
    """
    os.kill(orchestrator_pid, signal.SIGUSR1)
    logger.info(
        "external_nudge_sent",
        orchestrator_pid=orchestrator_pid,
        signal="SIGUSR1",
    )
