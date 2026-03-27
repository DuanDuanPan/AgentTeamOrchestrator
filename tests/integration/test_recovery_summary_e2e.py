"""test_recovery_summary_e2e — 恢复摘要集成测试 (Story 5.2)。

构造崩溃场景 → 运行 recovery → 验证摘要输出。
"""

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
    RecoveryResult,
    StoryRecord,
    TaskRecord,
)
from ato.recovery_summary import render_recovery_summary

_NOW = datetime.now(tz=UTC)


async def _setup_crash_scenario(db_path: Path) -> None:
    """构造崩溃场景：运行中的 task + crash_recovery approval。"""
    await init_db(db_path)
    db = await get_connection(db_path)
    try:
        await insert_story(
            db,
            StoryRecord(
                story_id="story-crash-1",
                title="Crash Story 1",
                status="in_progress",
                current_phase="developing",
                worktree_path="wt/story-crash-1",
                created_at=_NOW,
                updated_at=_NOW,
            ),
        )
        await insert_task(
            db,
            TaskRecord(
                task_id="task-crash-A",
                story_id="story-crash-1",
                phase="developing",
                role="dev",
                cli_tool="claude",
                status="failed",
                started_at=_NOW,
            ),
        )
        # 模拟 RecoveryEngine 创建的 crash_recovery approval
        await insert_approval(
            db,
            ApprovalRecord(
                approval_id="recovery-appr-1111-2222-3333-444455556666",
                story_id="story-crash-1",
                approval_type="crash_recovery",
                status="pending",
                payload=json.dumps(
                    {
                        "task_id": "task-crash-A",
                        "options": ["restart", "resume", "abandon"],
                    }
                ),
                created_at=_NOW,
                recommended_action="restart",
            ),
        )
    finally:
        await db.close()


class TestRecoverySummaryAfterCrash:
    async def test_recovery_summary_after_crash_recovery(self, tmp_path: Path) -> None:
        """构造崩溃场景 → 渲染摘要 → 验证输出到 stderr。"""
        db_path = tmp_path / ".ato" / "state.db"
        await _setup_crash_scenario(db_path)

        # 构造 RecoveryResult（模拟 RecoveryEngine 的输出）
        result = RecoveryResult.model_validate(
            {
                "classifications": [
                    {
                        "task_id": "task-crash-A",
                        "story_id": "story-crash-1",
                        "action": "needs_human",
                        "reason": "interactive session needs decision",
                    },
                ],
                "auto_recovered_count": 0,
                "dispatched_count": 0,
                "needs_human_count": 1,
                "recovery_mode": "crash",
            }
        )

        buf = StringIO()
        con = Console(file=buf, force_terminal=True, width=120)
        await render_recovery_summary(result, db_path, console=con)
        output = buf.getvalue()

        # 验证摘要内容
        assert "数据完整性检查通过" in output
        assert "异常中断" in output
        assert "1 个任务需要你决定" in output
        assert "story-crash-1" in output
        assert "developing" in output

    async def test_recovery_summary_includes_approval_commands(self, tmp_path: Path) -> None:
        """needs_human 任务包含 CLI 快捷命令。"""
        db_path = tmp_path / ".ato" / "state.db"
        await _setup_crash_scenario(db_path)

        result = RecoveryResult.model_validate(
            {
                "classifications": [
                    {
                        "task_id": "task-crash-A",
                        "story_id": "story-crash-1",
                        "action": "needs_human",
                        "reason": "needs decision",
                    },
                ],
                "auto_recovered_count": 0,
                "dispatched_count": 0,
                "needs_human_count": 1,
                "recovery_mode": "crash",
            }
        )

        buf = StringIO()
        con = Console(file=buf, force_terminal=True, width=120)
        await render_recovery_summary(result, db_path, console=con)
        output = buf.getvalue()

        # 验证快捷命令（approval_id[:8] = "recovery"）
        assert "ato approve" in output
        assert "recovery" in output
        assert "--decision restart" in output
        # 验证三个决策选项可见（AC2: 重启/续接/放弃）
        assert "restart" in output
        assert "resume" in output
        assert "abandon" in output
