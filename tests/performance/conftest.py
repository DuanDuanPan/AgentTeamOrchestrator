"""性能测试共享 fixture。

构造大规模数据库状态，混合四种恢复分类，供性能基准测试使用。
"""

from __future__ import annotations

import errno
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ato.models.db import get_connection, init_db, insert_story, insert_task
from ato.models.schemas import AdapterResult, StoryRecord, TaskRecord
from ato.recovery import RecoveryEngine

_NOW = datetime.now(tz=UTC)

# ---------------------------------------------------------------------------
# 恢复分类分布：25% reattach / 25% complete / 25% reschedule / 25% needs_human
# ---------------------------------------------------------------------------

_STRUCTURED_PHASES = ("creating", "reviewing", "merging", "qa_testing")
_INTERACTIVE_PHASES = ("uat", "developing")


def _classify_bucket(index: int) -> str:
    """按 index % 4 确定恢复分类桶。"""
    return ("reattach", "complete", "reschedule", "needs_human")[index % 4]


def _make_perf_story(story_id: str, phase: str = "developing") -> StoryRecord:
    return StoryRecord(
        story_id=story_id,
        title=f"Perf Story {story_id}",
        status="in_progress",
        current_phase=phase,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _make_perf_task(
    task_id: str,
    story_id: str,
    *,
    pid: int,
    phase: str,
    expected_artifact: str | None = None,
) -> TaskRecord:
    return TaskRecord(
        task_id=task_id,
        story_id=story_id,
        phase=phase,
        role="reviewer",
        cli_tool="codex",
        status="running",
        pid=pid,
        expected_artifact=expected_artifact,
        started_at=_NOW,
    )


async def _populate_db(db_path: Path, n_tasks: int) -> dict[str, set[int]]:
    """向数据库插入 n_tasks 个 running tasks，混合四种恢复分类。

    返回 {bucket_name: {pid_set}} 用于 mock 配置。
    """
    await init_db(db_path)
    db = await get_connection(db_path)
    try:
        # 所有 task 挂在同一个 story 下（简化，性能特征不变）
        await insert_story(db, _make_perf_story("perf-story-1"))

        buckets: dict[str, set[int]] = {
            "reattach": set(),
            "complete": set(),
            "reschedule": set(),
            "needs_human": set(),
        }

        for i in range(n_tasks):
            bucket = _classify_bucket(i)
            pid = 10000 + i
            buckets[bucket].add(pid)

            if bucket == "reattach":
                phase = "reviewing"
                artifact = None
            elif bucket == "complete":
                phase = "reviewing"
                artifact = f"/tmp/perf-artifact-{i}.json"
            elif bucket == "reschedule":
                phase = "creating"  # structured job
                artifact = None
            else:  # needs_human
                phase = "uat"
                artifact = None

            await insert_task(
                db,
                _make_perf_task(
                    f"perf-task-{i}",
                    "perf-story-1",
                    pid=pid,
                    phase=phase,
                    expected_artifact=artifact,
                ),
            )
    finally:
        await db.close()

    return buckets


def _build_pid_mock(buckets: dict[str, set[int]]) -> MagicMock:
    """构造 _is_pid_alive mock：reattach 桶返回 True，其余 False。"""
    alive_pids = buckets["reattach"]

    def side_effect(pid: int) -> bool:
        return pid in alive_pids

    mock = MagicMock(side_effect=side_effect)
    return mock


def _build_artifact_mock(buckets: dict[str, set[int]]) -> MagicMock:
    """构造 _artifact_exists mock：complete 桶返回 True，其余 False。"""
    complete_pids = buckets["complete"]

    def side_effect(task: TaskRecord) -> bool:
        return task.pid is not None and task.pid in complete_pids

    mock = MagicMock(side_effect=side_effect)
    return mock


# ---------------------------------------------------------------------------
# 低层级 mock：mock os.kill / Path.exists，让 _is_pid_alive / _artifact_exists 本体执行
# 用于分层 benchmark，验证 helper 真实的 errno 处理和 Path 构造开销
# ---------------------------------------------------------------------------


def _build_os_kill_mock(buckets: dict[str, set[int]]) -> MagicMock:
    """Mock os.kill：reattach 桶 PID 不抛异常（存活），其余抛 ESRCH（死亡）。

    _is_pid_alive 内部 try/except OSError + errno 判断逻辑会完整执行。
    """
    alive_pids = buckets["reattach"]

    def side_effect(pid: int, sig: int) -> None:
        if pid in alive_pids:
            return  # PID alive — os.kill 正常返回
        raise OSError(errno.ESRCH, "No such process")

    return MagicMock(side_effect=side_effect)


def _build_path_exists_fn(buckets: dict[str, set[int]]) -> Callable[[Path], bool]:
    """构造 Path.exists 替换函数：complete 桶的 artifact 路径返回 True，其余 False。

    _artifact_exists 内部 `if not task.expected_artifact` 早返回 +
    `Path(task.expected_artifact).exists()` 调用路径会完整执行。

    返回签名为 (self: Path) -> bool 的函数，供 patch.object(Path, "exists", ...) 使用。
    """
    # 从 PID 反推索引，再映射到 artifact 路径
    complete_indices = {pid - 10000 for pid in buckets["complete"]}
    valid_paths = {f"/tmp/perf-artifact-{i}.json" for i in complete_indices}

    def _fake_exists(self: Path) -> bool:
        return str(self) in valid_paths

    return _fake_exists


def _create_engine(db_path: Path) -> RecoveryEngine:
    """构造 RecoveryEngine，配置 interactive_phases 和 convergent_loop_phases。"""
    mock_mgr = MagicMock()
    mock_mgr.running = {}

    return RecoveryEngine(
        db_path=db_path,
        subprocess_mgr=mock_mgr,
        transition_queue=AsyncMock(),
        interactive_phases={"uat", "developing"},
        convergent_loop_phases={"reviewing", "validating", "qa_testing"},
    )


# ---------------------------------------------------------------------------
# Mock adapter (autouse) — 防止后台 dispatch 启动真实 CLI
# ---------------------------------------------------------------------------

_MOCK_RESULT = AdapterResult(
    status="success",
    exit_code=0,
    duration_ms=50,
    text_result="mock",
)


@pytest.fixture(autouse=True)
def _mock_adapter() -> object:
    mock = AsyncMock()
    mock.execute.return_value = _MOCK_RESULT
    with patch("ato.recovery._create_adapter", return_value=mock):
        yield mock


# ---------------------------------------------------------------------------
# 参数化 fixture：10 / 100 / 500 tasks
# ---------------------------------------------------------------------------


@pytest.fixture()
async def perf_db_10(tmp_path: Path) -> tuple[Path, dict[str, set[int]]]:
    """10 个 running tasks（基线）。"""
    db_path = tmp_path / ".ato" / "state.db"
    buckets = await _populate_db(db_path, 10)
    return db_path, buckets


@pytest.fixture()
async def perf_db_100(tmp_path: Path) -> tuple[Path, dict[str, set[int]]]:
    """100 个 running tasks（常规负载）。"""
    db_path = tmp_path / ".ato" / "state.db"
    buckets = await _populate_db(db_path, 100)
    return db_path, buckets


@pytest.fixture()
async def perf_db_500(tmp_path: Path) -> tuple[Path, dict[str, set[int]]]:
    """500 个 running tasks（压力测试）。"""
    db_path = tmp_path / ".ato" / "state.db"
    buckets = await _populate_db(db_path, 500)
    return db_path, buckets
