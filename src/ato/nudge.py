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


def format_notification_message(level: str, message: str) -> str:
    """根据通知级别格式化消息文本。

    Args:
        level: NotificationLevel 值。
        message: 原始消息文本。

    Returns:
        格式化后的单行消息字符串。
    """
    if level == "urgent":
        return f"⚠ 紧急: {message}"
    if level == "milestone":
        return f"🎉 {message}"
    # normal / silent: 不加前缀
    return message


def send_user_notification(level: str, message: str) -> None:
    """发送用户可见通知。

    行为矩阵：
    - ``urgent`` → 连续两次 terminal bell + stderr 输出（带"⚠ 紧急"前缀）
    - ``normal`` → 单次 terminal bell + stderr 输出
    - ``milestone`` → 单次 terminal bell + stderr 输出（带"🎉"前缀）
    - ``silent`` → 无 bell、无 stderr 输出，仅 structlog 日志

    Args:
        level: NotificationLevel 值。
        message: 通知消息文本。
    """
    import sys

    formatted = format_notification_message(level, message)

    if level == "urgent":
        sys.stderr.write("\a\a")
        sys.stderr.flush()
        sys.stderr.write(formatted + "\n")
        sys.stderr.flush()
    elif level in ("normal", "milestone"):
        sys.stderr.write("\a")
        sys.stderr.flush()
        sys.stderr.write(formatted + "\n")
        sys.stderr.flush()
    # silent: 无 bell、无 stderr 输出

    logger.info("notification_sent", level=level, message=formatted)


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
