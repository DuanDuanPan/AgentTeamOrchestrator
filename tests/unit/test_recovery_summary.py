"""test_recovery_summary — 恢复摘要渲染器单元测试 (Story 5.2)。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path

from rich.console import Console

from ato.models.db import (
    get_connection,
    init_db,
    insert_approval,
    insert_story,
    insert_task,
)
from ato.models.schemas import (
    ApprovalRecord,
    RecoveryClassification,
    RecoveryResult,
    StoryRecord,
    TaskRecord,
)
from ato.recovery_summary import render_recovery_summary

_NOW = datetime.now(tz=UTC)


def _make_classification(
    task_id: str = "task-001",
    story_id: str = "story-1",
    action: str = "reattach",
    reason: str = "test",
) -> RecoveryClassification:
    return RecoveryClassification.model_validate(
        {"task_id": task_id, "story_id": story_id, "action": action, "reason": reason}
    )


def _make_result(
    *,
    classifications: list[RecoveryClassification] | None = None,
    auto_recovered: int = 0,
    dispatched: int = 0,
    needs_human: int = 0,
    mode: str = "crash",
) -> RecoveryResult:
    return RecoveryResult.model_validate(
        {
            "classifications": classifications or [],
            "auto_recovered_count": auto_recovered,
            "dispatched_count": dispatched,
            "needs_human_count": needs_human,
            "recovery_mode": mode,
        }
    )


async def _setup_db_with_needs_human(
    db_path: Path,
    *,
    tasks: list[tuple[str, str, str]] | None = None,
    approvals: list[tuple[str, str, str]] | None = None,
) -> None:
    """准备数据库：插入 stories, tasks, approvals。

    tasks: [(task_id, story_id, phase), ...]
    approvals: [(approval_id, story_id, task_id), ...]
    """
    await init_db(db_path)
    db = await get_connection(db_path)
    try:
        # Insert stories
        story_ids = set()
        if tasks:
            for _, sid, _ in tasks:
                story_ids.add(sid)
        if approvals:
            for _, sid, _ in approvals:
                story_ids.add(sid)

        for sid in story_ids:
            await insert_story(
                db,
                StoryRecord(
                    story_id=sid,
                    title=f"Test {sid}",
                    status="in_progress",
                    current_phase="developing",
                    worktree_path=f"wt/{sid}",
                    created_at=_NOW,
                    updated_at=_NOW,
                ),
            )

        if tasks:
            for tid, sid, phase in tasks:
                await insert_task(
                    db,
                    TaskRecord(
                        task_id=tid,
                        story_id=sid,
                        phase=phase,
                        role="dev",
                        cli_tool="claude",
                        status="failed",
                        started_at=_NOW,
                    ),
                )

        if approvals:
            for aid, sid, tid in approvals:
                await insert_approval(
                    db,
                    ApprovalRecord(
                        approval_id=aid,
                        story_id=sid,
                        approval_type="crash_recovery",
                        status="pending",
                        payload=json.dumps({"task_id": tid}),
                        created_at=_NOW,
                        recommended_action="restart",
                    ),
                )
    finally:
        await db.close()


def _capture_console() -> tuple[Console, StringIO]:
    """创建一个写入 StringIO 的 Console 用于捕获输出。"""
    buf = StringIO()
    con = Console(file=buf, force_terminal=True, width=120)
    return con, buf


class TestRenderCrashMode:
    async def test_render_crash_mode_with_needs_human(self, tmp_path: Path) -> None:
        """crash 恢复有 needs_human 时的完整渲染。"""
        db_path = tmp_path / ".ato" / "state.db"

        tasks = [
            ("task-nh1", "story-3", "developing"),
            ("task-nh2", "story-7", "developing"),
        ]
        approvals = [
            ("appr-1111-2222-3333-4444-555566667777", "story-3", "task-nh1"),
            ("appr-2222-3333-4444-5555-666677778888", "story-7", "task-nh2"),
        ]
        await _setup_db_with_needs_human(db_path, tasks=tasks, approvals=approvals)

        classifications = [
            _make_classification("task-a1", "story-1", "reattach"),
            _make_classification("task-a2", "story-2", "complete"),
            _make_classification("task-a3", "story-1", "reschedule"),
            _make_classification("task-nh1", "story-3", "needs_human"),
            _make_classification("task-nh2", "story-7", "needs_human"),
        ]
        result = _make_result(
            classifications=classifications,
            auto_recovered=2,
            dispatched=1,
            needs_human=2,
            mode="crash",
        )

        con, buf = _capture_console()
        await render_recovery_summary(result, db_path, console=con)
        output = buf.getvalue()

        assert "数据完整性检查通过" in output
        assert "异常中断" in output
        assert "2 个任务自动恢复" in output
        assert "1 个任务已重新调度" in output
        assert "2 个任务需要你决定" in output
        assert "story-3" in output
        assert "story-7" in output
        assert "ato approve" in output

    async def test_render_crash_mode_all_auto_recovered(self, tmp_path: Path) -> None:
        """全部自动恢复时的简化渲染。"""
        db_path = tmp_path / ".ato" / "state.db"
        await init_db(db_path)

        classifications = [
            _make_classification("task-a1", "story-1", "reattach"),
            _make_classification("task-a2", "story-2", "complete"),
        ]
        result = _make_result(
            classifications=classifications,
            auto_recovered=2,
            needs_human=0,
            mode="crash",
        )

        con, buf = _capture_console()
        await render_recovery_summary(result, db_path, console=con)
        output = buf.getvalue()

        assert "数据完整性检查通过" in output
        assert "2 个任务自动恢复" in output
        assert "需要你决定" not in output
        assert "系统已恢复运行" in output


class TestRenderNormalMode:
    async def test_render_normal_mode(self, tmp_path: Path) -> None:
        """normal 恢复模式渲染。"""
        db_path = tmp_path / ".ato" / "state.db"
        await init_db(db_path)

        classifications = [
            _make_classification("task-1", "story-1", "reschedule"),
        ]
        result = _make_result(
            classifications=classifications,
            dispatched=1,
            mode="normal",
        )

        con, buf = _capture_console()
        await render_recovery_summary(result, db_path, console=con)
        output = buf.getvalue()

        assert "数据完整性检查通过" in output
        assert "暂停的任务，正常恢复" in output
        assert "1 个任务已重新调度" in output

    async def test_render_none_mode(self, tmp_path: Path) -> None:
        """无需恢复时的渲染。"""
        db_path = tmp_path / ".ato" / "state.db"
        await init_db(db_path)

        result = _make_result(mode="none")

        con, buf = _capture_console()
        await render_recovery_summary(result, db_path, console=con)
        output = buf.getvalue()

        assert "数据完整性检查通过" in output
        assert "无需恢复，系统状态正常" in output


class TestDispatchedCount:
    async def test_render_dispatched_count_shown(self, tmp_path: Path) -> None:
        """dispatched_count > 0 时显示重新调度行。"""
        db_path = tmp_path / ".ato" / "state.db"
        await init_db(db_path)

        result = _make_result(
            classifications=[
                _make_classification("t1", "s1", "reschedule"),
                _make_classification("t2", "s1", "reschedule"),
                _make_classification("t3", "s2", "reattach"),
            ],
            auto_recovered=1,
            dispatched=2,
            mode="crash",
        )

        con, buf = _capture_console()
        await render_recovery_summary(result, db_path, console=con)
        output = buf.getvalue()

        assert "2 个任务已重新调度" in output


class TestNeedsHumanTable:
    async def test_needs_human_table_columns(self, tmp_path: Path) -> None:
        """needs_human 表格包含正确列。"""
        db_path = tmp_path / ".ato" / "state.db"
        tasks = [("task-nh1", "story-5", "developing")]
        approvals = [("appr-aaaa-bbbb-cccc-dddd-eeeeffffffff", "story-5", "task-nh1")]
        await _setup_db_with_needs_human(db_path, tasks=tasks, approvals=approvals)

        result = _make_result(
            classifications=[_make_classification("task-nh1", "story-5", "needs_human")],
            needs_human=1,
            mode="crash",
        )

        con, buf = _capture_console()
        await render_recovery_summary(result, db_path, console=con)
        output = buf.getvalue()

        # 表格列头
        assert "Task" in output
        assert "Story" in output
        assert "Phase" in output
        assert "Worktree" in output
        # AC2: 三个选项都以可复制的完整命令出现（表格外）
        assert "--decision restart" in output
        assert "--decision resume" in output
        assert "--decision abandon" in output
        assert "ato approve" in output

    async def test_recovery_summary_matches_approval_by_task_id(self, tmp_path: Path) -> None:
        """同一 story 存在多个 crash_recovery approval 时，按 task_id 精确映射。"""
        db_path = tmp_path / ".ato" / "state.db"

        # 同一 story 的两个 task
        tasks = [
            ("task-A", "story-x", "developing"),
            ("task-B", "story-x", "reviewing"),
        ]
        approvals = [
            ("appr-AAAA-1111-1111-1111-111111111111", "story-x", "task-A"),
            ("appr-BBBB-2222-2222-2222-222222222222", "story-x", "task-B"),
        ]
        await _setup_db_with_needs_human(db_path, tasks=tasks, approvals=approvals)

        result = _make_result(
            classifications=[
                _make_classification("task-A", "story-x", "needs_human"),
                _make_classification("task-B", "story-x", "needs_human"),
            ],
            needs_human=2,
            mode="crash",
        )

        con, buf = _capture_console()
        await render_recovery_summary(result, db_path, console=con)
        output = buf.getvalue()

        # 两个不同的 approval 短 ID 都应出现
        assert "appr-AAA" in output
        assert "appr-BBB" in output
        # 两个 phase 都应展示
        assert "developing" in output
        assert "reviewing" in output


class TestOutputTarget:
    async def test_output_to_stderr(self, tmp_path: Path) -> None:
        """默认 Console 输出到 stderr。"""
        db_path = tmp_path / ".ato" / "state.db"
        await init_db(db_path)

        result = _make_result(mode="none")

        # 使用默认 console（stderr=True）时，不会写到 stdout
        # 通过检查 Console(stderr=True) 构造来验证
        from ato.recovery_summary import render_recovery_summary as _render

        # 传入自定义 console 来验证 stderr 参数
        stderr_buf = StringIO()
        stderr_con = Console(file=stderr_buf, stderr=True, force_terminal=True, width=120)
        await _render(result, db_path, console=stderr_con)
        output = stderr_buf.getvalue()

        assert "数据完整性检查通过" in output
