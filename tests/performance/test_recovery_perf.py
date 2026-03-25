"""崩溃恢复性能基准测试。

NFR1 目标：run_recovery() 同步窗口 ≤30s（MVP）。
计时边界：SQLite 扫描 + PID/artifact 检查 + 分类 + 恢复动作分派。
不计入：后台 PID 监控、re-dispatch CLI 执行。

纯数据库状态驱动，mock os.kill() 和 Path.exists()。
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import patch

import pytest
import structlog

import ato.recovery as _recovery_mod
from ato.models.db import get_connection, get_paused_tasks, get_running_tasks
from ato.recovery import RecoveryEngine

from .conftest import (
    _build_artifact_mock,
    _build_os_kill_mock,
    _build_path_exists_fn,
    _build_pid_mock,
    _create_engine,
)


async def _cancel_background_tasks(engine: RecoveryEngine) -> None:
    """取消所有后台任务并等待清理完成。

    reattach PID 监控循环会永久轮询（PID mock 返回 alive），
    必须 cancel 而非 await，否则测试挂起。
    """
    for task in engine._background_tasks:
        task.cancel()
    await asyncio.gather(*engine._background_tasks, return_exceptions=True)


# ---------------------------------------------------------------------------
# Task 1: 端到端性能基准 (AC1)
# ---------------------------------------------------------------------------


class TestEndToEndRecoveryPerf:
    """run_recovery() 端到端计时。"""

    @pytest.mark.perf
    async def test_e2e_10_tasks(
        self,
        perf_db_10: tuple[Path, dict[str, set[int]]],
    ) -> None:
        """10 tasks 基线性能。"""
        db_path, buckets = perf_db_10
        engine = _create_engine(db_path)

        with (
            patch("ato.recovery._is_pid_alive", new=_build_pid_mock(buckets)),
            patch("ato.recovery._artifact_exists", new=_build_artifact_mock(buckets)),
        ):
            t0 = time.perf_counter()
            result = await engine.run_recovery()
            elapsed = time.perf_counter() - t0

        await _cancel_background_tasks(engine)

        assert result.recovery_mode == "crash"
        assert len(result.classifications) == 10
        assert elapsed < 5.0, f"10 tasks: {elapsed:.3f}s > 5s"

    @pytest.mark.perf
    async def test_e2e_100_tasks(
        self,
        perf_db_100: tuple[Path, dict[str, set[int]]],
    ) -> None:
        """100 tasks 常规负载。"""
        db_path, buckets = perf_db_100
        engine = _create_engine(db_path)

        with (
            patch("ato.recovery._is_pid_alive", new=_build_pid_mock(buckets)),
            patch("ato.recovery._artifact_exists", new=_build_artifact_mock(buckets)),
        ):
            t0 = time.perf_counter()
            result = await engine.run_recovery()
            elapsed = time.perf_counter() - t0

        await _cancel_background_tasks(engine)

        assert result.recovery_mode == "crash"
        assert len(result.classifications) == 100
        # AC1 hard assert: 100 tasks ≤5s
        assert elapsed < 5.0, f"100 tasks: {elapsed:.3f}s > 5s"

    @pytest.mark.perf
    async def test_e2e_500_tasks(
        self,
        perf_db_500: tuple[Path, dict[str, set[int]]],
    ) -> None:
        """500 tasks 压力测试（超越 MVP 预期上限）。"""
        db_path, buckets = perf_db_500
        engine = _create_engine(db_path)

        with (
            patch("ato.recovery._is_pid_alive", new=_build_pid_mock(buckets)),
            patch("ato.recovery._artifact_exists", new=_build_artifact_mock(buckets)),
        ):
            t0 = time.perf_counter()
            result = await engine.run_recovery()
            elapsed = time.perf_counter() - t0

        await _cancel_background_tasks(engine)

        assert result.recovery_mode == "crash"
        assert len(result.classifications) == 500
        # AC1 hard assert: 500 tasks ≤30s（NFR1 MVP 目标）
        assert elapsed < 30.0, f"500 tasks: {elapsed:.3f}s > 30s"


# ---------------------------------------------------------------------------
# Task 2: 分层性能拆解 (AC1)
# ---------------------------------------------------------------------------


class TestLayeredPerformance:
    """分别计时各阶段，验证瓶颈分布。"""

    @pytest.mark.perf
    async def test_sqlite_scan_phase(
        self,
        perf_db_500: tuple[Path, dict[str, set[int]]],
    ) -> None:
        """单独计时 SQLite 扫描（get_running_tasks + get_paused_tasks）。"""
        db_path, _ = perf_db_500
        db = await get_connection(db_path)
        try:
            t0 = time.perf_counter()
            running = await get_running_tasks(db)
            paused = await get_paused_tasks(db)
            elapsed = time.perf_counter() - t0
        finally:
            await db.close()

        assert len(running) == 500
        assert len(paused) == 0
        # SQLite WAL 扫描应极快
        assert elapsed < 1.0, f"SQLite scan: {elapsed:.3f}s > 1s"

    @pytest.mark.perf
    async def test_pid_check_phase(
        self,
        perf_db_500: tuple[Path, dict[str, set[int]]],
    ) -> None:
        """单独计时 PID 检查阶段（_is_pid_alive × N tasks）。

        mock os.kill（非 _is_pid_alive 本身），让 errno 处理逻辑完整执行。
        """
        db_path, buckets = perf_db_500
        os_kill_mock = _build_os_kill_mock(buckets)

        db = await get_connection(db_path)
        try:
            tasks = await get_running_tasks(db)
        finally:
            await db.close()

        with patch("os.kill", new=os_kill_mock):
            t0 = time.perf_counter()
            results = [
                _recovery_mod._is_pid_alive(task.pid)
                for task in tasks
                if task.pid is not None
            ]
            elapsed = time.perf_counter() - t0

        # 验证真实 helper 逻辑：125 个 reattach PID → True，375 个 → False
        assert os_kill_mock.call_count == 500
        assert sum(results) == 125  # reattach 桶 = 25% of 500
        assert elapsed < 1.0, f"PID check ×500: {elapsed:.3f}s > 1s"

    @pytest.mark.perf
    async def test_artifact_check_phase(
        self,
        perf_db_500: tuple[Path, dict[str, set[int]]],
    ) -> None:
        """单独计时 artifact 检查阶段（_artifact_exists × N tasks）。

        mock Path.exists（非 _artifact_exists 本身），
        让 expected_artifact None 检查 + Path 构造逻辑完整执行。
        """
        db_path, buckets = perf_db_500

        db = await get_connection(db_path)
        try:
            tasks = await get_running_tasks(db)
        finally:
            await db.close()

        with patch.object(Path, "exists", _build_path_exists_fn(buckets)):
            t0 = time.perf_counter()
            results = [_recovery_mod._artifact_exists(task) for task in tasks]
            elapsed = time.perf_counter() - t0

        # 验证真实 helper 逻辑：
        # - 375 个 task 没有 expected_artifact → 早返回 False（不触发 Path.exists）
        # - 125 个 complete task 有 artifact → Path 构造 + exists() → True
        assert sum(results) == 125  # complete 桶 = 25% of 500
        assert elapsed < 1.0, f"Artifact check ×500: {elapsed:.3f}s > 1s"

    @pytest.mark.perf
    async def test_classify_and_dispatch_phase(
        self,
        perf_db_500: tuple[Path, dict[str, set[int]]],
    ) -> None:
        """单独计时分类决策 + 恢复动作分派的同步入口。

        复现 run_recovery() 在 DB 扫描之后的完整循环：
        classify → _reattach / _complete_from_artifact / _reschedule / _mark_needs_human。
        后台 dispatch 任务不等待，但同步入口（DB 写、task 创建）纳入计时。
        """
        db_path, buckets = perf_db_500
        engine = _create_engine(db_path)

        db = await get_connection(db_path)
        try:
            tasks = await get_running_tasks(db)
        finally:
            await db.close()

        with (
            patch("ato.recovery._is_pid_alive", new=_build_pid_mock(buckets)),
            patch("ato.recovery._artifact_exists", new=_build_artifact_mock(buckets)),
        ):
            t0 = time.perf_counter()
            for task in tasks:
                c = engine.classify_task(task)
                if c.action == "reattach":
                    await engine._reattach(task)
                elif c.action == "complete":
                    await engine._complete_from_artifact(task)
                elif c.action == "reschedule":
                    await engine._reschedule(task)
                elif c.action == "needs_human":
                    await engine._mark_needs_human(task)
            elapsed = time.perf_counter() - t0

        await _cancel_background_tasks(engine)

        # 500 tasks 分类 + 分派同步入口应远低于 30s
        assert elapsed < 10.0, f"Classify+dispatch ×500: {elapsed:.3f}s > 10s"

    @pytest.mark.perf
    async def test_layered_sum_matches_e2e(
        self,
        tmp_path: Path,
    ) -> None:
        """验证分层测量（DB 扫描 + 分类分派）与端到端 run_recovery() 量级一致。

        分层和 E2E 使用独立数据库，避免分派动作修改 DB 状态导致 E2E 看到空结果。
        """
        from .conftest import _populate_db

        # --- 独立 DB: 分层计时 ---
        db_path_layer = tmp_path / "layer" / "state.db"
        buckets = await _populate_db(db_path_layer, 500)

        db = await get_connection(db_path_layer)
        try:
            t0 = time.perf_counter()
            tasks = await get_running_tasks(db)
            _ = await get_paused_tasks(db)
            t_scan = time.perf_counter() - t0
        finally:
            await db.close()

        engine_layer = _create_engine(db_path_layer)
        with (
            patch("ato.recovery._is_pid_alive", new=_build_pid_mock(buckets)),
            patch("ato.recovery._artifact_exists", new=_build_artifact_mock(buckets)),
        ):
            t0 = time.perf_counter()
            for task in tasks:
                c = engine_layer.classify_task(task)
                if c.action == "reattach":
                    await engine_layer._reattach(task)
                elif c.action == "complete":
                    await engine_layer._complete_from_artifact(task)
                elif c.action == "reschedule":
                    await engine_layer._reschedule(task)
                elif c.action == "needs_human":
                    await engine_layer._mark_needs_human(task)
            t_dispatch = time.perf_counter() - t0

        await _cancel_background_tasks(engine_layer)
        layered_total = t_scan + t_dispatch

        # --- 独立 DB: E2E 计时 ---
        db_path_e2e = tmp_path / "e2e" / "state.db"
        buckets_e2e = await _populate_db(db_path_e2e, 500)

        engine_e2e = _create_engine(db_path_e2e)
        with (
            patch("ato.recovery._is_pid_alive", new=_build_pid_mock(buckets_e2e)),
            patch("ato.recovery._artifact_exists", new=_build_artifact_mock(buckets_e2e)),
        ):
            t0 = time.perf_counter()
            await engine_e2e.run_recovery()
            t_e2e = time.perf_counter() - t0

        await _cancel_background_tasks(engine_e2e)

        # 双边断言：分层总和与 E2E 在同一量级
        assert layered_total < t_e2e * 3 + 0.5, (
            f"Layered {layered_total:.3f}s >> E2E {t_e2e:.3f}s"
        )
        assert t_e2e < layered_total * 3 + 0.5, (
            f"E2E {t_e2e:.3f}s >> Layered {layered_total:.3f}s"
        )


# ---------------------------------------------------------------------------
# Task 3: 规模递增压力测试 (AC1)
# ---------------------------------------------------------------------------


class TestScaleProgression:
    """10 → 100 → 500 tasks，验证四种分类分布正确。"""

    @pytest.mark.perf
    async def test_10_tasks_classification_distribution(
        self,
        perf_db_10: tuple[Path, dict[str, set[int]]],
    ) -> None:
        """10 tasks：验证四种分类均有出现。"""
        db_path, buckets = perf_db_10
        engine = _create_engine(db_path)

        with (
            patch("ato.recovery._is_pid_alive", new=_build_pid_mock(buckets)),
            patch("ato.recovery._artifact_exists", new=_build_artifact_mock(buckets)),
        ):
            result = await engine.run_recovery()

        await _cancel_background_tasks(engine)

        actions = {c.action for c in result.classifications}
        # 10 tasks = 索引 0-9，每种分类至少 2 个
        assert "reattach" in actions
        assert "complete" in actions
        assert "reschedule" in actions
        assert "needs_human" in actions

        # 验证计数
        by_action: dict[str, int] = {}
        for c in result.classifications:
            by_action[c.action] = by_action.get(c.action, 0) + 1
        assert by_action["reattach"] >= 2
        assert by_action["complete"] >= 2
        assert by_action["reschedule"] >= 2
        assert by_action["needs_human"] >= 2

    @pytest.mark.perf
    async def test_100_tasks_classification_distribution(
        self,
        perf_db_100: tuple[Path, dict[str, set[int]]],
    ) -> None:
        """100 tasks：验证分布均匀（每类 25 个）。"""
        db_path, buckets = perf_db_100
        engine = _create_engine(db_path)

        with (
            patch("ato.recovery._is_pid_alive", new=_build_pid_mock(buckets)),
            patch("ato.recovery._artifact_exists", new=_build_artifact_mock(buckets)),
        ):
            result = await engine.run_recovery()

        await _cancel_background_tasks(engine)

        by_action: dict[str, int] = {}
        for c in result.classifications:
            by_action[c.action] = by_action.get(c.action, 0) + 1

        assert by_action["reattach"] == 25
        assert by_action["complete"] == 25
        assert by_action["reschedule"] == 25
        assert by_action["needs_human"] == 25

    @pytest.mark.perf
    async def test_500_tasks_classification_distribution(
        self,
        perf_db_500: tuple[Path, dict[str, set[int]]],
    ) -> None:
        """500 tasks：验证分布均匀（每类 125 个）。"""
        db_path, buckets = perf_db_500
        engine = _create_engine(db_path)

        with (
            patch("ato.recovery._is_pid_alive", new=_build_pid_mock(buckets)),
            patch("ato.recovery._artifact_exists", new=_build_artifact_mock(buckets)),
        ):
            result = await engine.run_recovery()

        await _cancel_background_tasks(engine)

        by_action: dict[str, int] = {}
        for c in result.classifications:
            by_action[c.action] = by_action.get(c.action, 0) + 1

        assert by_action["reattach"] == 125
        assert by_action["complete"] == 125
        assert by_action["reschedule"] == 125
        assert by_action["needs_human"] == 125

        # 结果摘要验证
        assert result.auto_recovered_count == 250  # reattach + complete
        assert result.dispatched_count == 125  # reschedule
        assert result.needs_human_count == 125


# ---------------------------------------------------------------------------
# Task 5: 性能回归检测 — structlog 基线 + hard assert (AC1)
# ---------------------------------------------------------------------------


class TestPerformanceRegression:
    """性能回归检测：记录基线到 structlog，hard assert 阈值。"""

    @pytest.mark.perf
    async def test_100_tasks_hard_threshold(
        self,
        perf_db_100: tuple[Path, dict[str, set[int]]],
    ) -> None:
        """100 tasks 场景 run_recovery() ≤5s hard assert。"""
        db_path, buckets = perf_db_100

        captured_events: list[dict[str, object]] = []

        def capture_event(
            _logger: structlog.types.WrappedLogger,
            method_name: str,
            event_dict: dict[str, object],
        ) -> dict[str, object]:
            captured_events.append(event_dict.copy())
            return event_dict

        original_config = structlog.get_config()
        processors = list(original_config.get("processors", []))
        processors.insert(0, capture_event)
        structlog.configure(processors=processors)

        try:
            engine = _create_engine(db_path)
            with (
                patch("ato.recovery._is_pid_alive", new=_build_pid_mock(buckets)),
                patch("ato.recovery._artifact_exists", new=_build_artifact_mock(buckets)),
            ):
                t0 = time.perf_counter()
                await engine.run_recovery()
                elapsed = time.perf_counter() - t0

            await _cancel_background_tasks(engine)
        finally:
            structlog.configure(**original_config)

        # Hard assert: ≤5s
        assert elapsed < 5.0, f"100 tasks: {elapsed:.3f}s > 5s"

        # 验证 structlog 包含 duration_ms
        complete_events = [
            e for e in captured_events if e.get("event") == "recovery_complete"
        ]
        assert len(complete_events) == 1
        assert "duration_ms" in complete_events[0]

    @pytest.mark.perf
    async def test_500_tasks_hard_threshold(
        self,
        perf_db_500: tuple[Path, dict[str, set[int]]],
    ) -> None:
        """500 tasks 场景 run_recovery() ≤30s hard assert（NFR1 MVP）。"""
        db_path, buckets = perf_db_500

        captured_events: list[dict[str, object]] = []

        def capture_event(
            _logger: structlog.types.WrappedLogger,
            method_name: str,
            event_dict: dict[str, object],
        ) -> dict[str, object]:
            captured_events.append(event_dict.copy())
            return event_dict

        original_config = structlog.get_config()
        processors = list(original_config.get("processors", []))
        processors.insert(0, capture_event)
        structlog.configure(processors=processors)

        try:
            engine = _create_engine(db_path)
            with (
                patch("ato.recovery._is_pid_alive", new=_build_pid_mock(buckets)),
                patch("ato.recovery._artifact_exists", new=_build_artifact_mock(buckets)),
            ):
                t0 = time.perf_counter()
                await engine.run_recovery()
                elapsed = time.perf_counter() - t0

            await _cancel_background_tasks(engine)
        finally:
            structlog.configure(**original_config)

        # Hard assert: ≤30s (NFR1 MVP)
        assert elapsed < 30.0, f"500 tasks: {elapsed:.3f}s > 30s"

        # 验证 structlog 基线记录
        complete_events = [
            e for e in captured_events if e.get("event") == "recovery_complete"
        ]
        assert len(complete_events) == 1
        event = complete_events[0]
        assert event["recovery_mode"] == "crash"
        assert "duration_ms" in event
        assert "auto_recovered" in event
        assert "dispatched" in event
        assert "needs_human" in event
