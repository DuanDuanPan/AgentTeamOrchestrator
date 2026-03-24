"""Nudge 通知机制单元测试。"""

from __future__ import annotations

import asyncio
import os
import signal
import time

import pytest

from ato.nudge import Nudge, send_external_nudge


class TestNudgeNotifyAndWait:
    async def test_notify_then_wait_returns_true(self) -> None:
        """notify 后 wait 立即返回 True。"""
        nudge = Nudge()
        nudge.notify()
        result = await nudge.wait(timeout=1.0)
        assert result is True

    async def test_wait_timeout_returns_false(self) -> None:
        """无 notify 时 wait 超时返回 False。"""
        nudge = Nudge()
        t0 = time.monotonic()
        result = await nudge.wait(timeout=0.05)
        elapsed = time.monotonic() - t0
        assert result is False
        assert elapsed >= 0.04  # 至少等了接近 50ms

    async def test_event_auto_clears_after_wait(self) -> None:
        """wait 返回后 event 自动 clear，可继续下一轮等待。"""
        nudge = Nudge()
        nudge.notify()
        r1 = await nudge.wait(timeout=1.0)
        assert r1 is True
        # event 已 clear，再次 wait 应超时
        r2 = await nudge.wait(timeout=0.05)
        assert r2 is False

    async def test_multiple_notify_before_wait(self) -> None:
        """多次 notify 后只唤醒一次 wait（asyncio.Event 语义）。"""
        nudge = Nudge()
        nudge.notify()
        nudge.notify()
        nudge.notify()
        r1 = await nudge.wait(timeout=1.0)
        assert r1 is True
        # 后续 wait 超时——只有一个 event
        r2 = await nudge.wait(timeout=0.05)
        assert r2 is False

    async def test_concurrent_wait_and_notify(self) -> None:
        """先 wait 后 notify，验证 wait 能被唤醒。"""
        nudge = Nudge()

        async def delayed_notify() -> None:
            await asyncio.sleep(0.02)
            nudge.notify()

        task = asyncio.create_task(delayed_notify())
        result = await nudge.wait(timeout=1.0)
        assert result is True
        await task


class TestSendExternalNudge:
    def test_send_to_nonexistent_pid_raises(self) -> None:
        """发送给不存在的 PID 应抛出 ProcessLookupError。"""
        # 使用极大 PID，几乎不可能存在
        with pytest.raises(ProcessLookupError):
            send_external_nudge(999999999)

    def test_send_to_self(self) -> None:
        """发送给自身 PID（需要先注册 handler 忽略信号）。"""
        received = []
        original_handler = signal.getsignal(signal.SIGUSR1)

        def handler(signum: int, frame: object) -> None:
            received.append(signum)

        signal.signal(signal.SIGUSR1, handler)
        try:
            send_external_nudge(os.getpid())
            assert len(received) == 1
            assert received[0] == signal.SIGUSR1
        finally:
            signal.signal(signal.SIGUSR1, original_handler)
