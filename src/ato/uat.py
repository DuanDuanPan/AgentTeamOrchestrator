"""uat — CLI / TUI 共享的 UAT 结果提交逻辑。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from ato.models.db import get_connection, get_story, get_tasks_by_story, update_task_status
from ato.models.schemas import ATOError, TaskRecord

UATResult = Literal["pass", "fail"]


class UATSubmissionError(ATOError):
    """UAT 提交失败。"""

    def __init__(self, message: str, *, hint: str = "") -> None:
        self.hint = hint
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class UATSubmissionOutcome:
    """UAT 提交结果。"""

    story_id: str
    result: UATResult
    reason: str
    message: str


def _find_running_uat_task(tasks: list[TaskRecord]) -> TaskRecord | None:
    """查找当前 story 的 running UAT task。"""
    for task in tasks:
        if task.status == "running" and task.phase == "uat":
            return task
    return None


async def submit_uat_result(
    *,
    db_path: Path,
    story_id: str,
    result: UATResult,
    reason: str = "",
) -> UATSubmissionOutcome:
    """提交 UAT 结果并更新当前 running UAT task。"""
    normalized_reason = reason.strip()
    if result not in ("pass", "fail"):
        raise UATSubmissionError(
            f"--result 必须是 'pass' 或 'fail'，收到: '{result}'",
            hint="--result pass / --result fail",
        )
    if result == "fail" and not normalized_reason:
        raise UATSubmissionError(
            "UAT 失败时必须提供原因",
            hint="添加失败原因后重试",
        )

    now = datetime.now(tz=UTC)
    db = await get_connection(db_path)
    try:
        story = await get_story(db, story_id)
        if story is None:
            raise UATSubmissionError(
                f"Story 不存在: {story_id}",
                hint="运行 `ato batch status` 查看可用 stories",
            )

        if story.current_phase != "uat":
            raise UATSubmissionError(
                f"Story '{story_id}' 不在 UAT 阶段（当前: {story.current_phase}）",
                hint="等待 story 进入 UAT 阶段后重试",
            )

        tasks = await get_tasks_by_story(db, story_id)
        running_task = _find_running_uat_task(tasks)
        if running_task is None:
            raise UATSubmissionError(
                "未找到运行中的 UAT task",
                hint="确认 Orchestrator 已启动且 story 在 UAT 阶段",
            )

        uat_payload = {
            "uat_result": result,
            "reason": normalized_reason,
            "submitted_at": now.isoformat(),
        }
        payload_text = json.dumps(uat_payload, ensure_ascii=False)

        if result == "pass":
            await update_task_status(
                db,
                running_task.task_id,
                "completed",
                context_briefing=payload_text,
                completed_at=now,
            )
            message = f"✅ Story '{story_id}' UAT 通过，进入 merge 阶段。"
        else:
            await update_task_status(
                db,
                running_task.task_id,
                "failed",
                context_briefing=payload_text,
                error_message=f"uat_fail: {normalized_reason}",
                expected_artifact="uat_fail_requested",
                completed_at=now,
            )
            message = (
                f"✅ Story '{story_id}' UAT 未通过，退回 fix 阶段重新进入质量门控。"
                f"原因: {normalized_reason}"
            )
    finally:
        await db.close()

    return UATSubmissionOutcome(
        story_id=story_id,
        result=result,
        reason=normalized_reason,
        message=message,
    )
