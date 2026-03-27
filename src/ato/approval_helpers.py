"""approval_helpers — 统一 approval 创建 API。

所有 approval 创建统一走此模块，避免通知 / nudge 逻辑散落在 models/db.py 中。
DB 写事务中不 await 外部 IO——先 commit 再 nudge / bell。
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any, Literal, cast

import aiosqlite
import structlog

from ato.models.db import insert_approval
from ato.models.schemas import (
    APPROVAL_DEFAULT_VALID_OPTIONS,
    APPROVAL_RECOMMENDED_ACTIONS,
    APPROVAL_TYPE_TO_NOTIFICATION,
    ApprovalRecord,
)
from ato.nudge import Nudge, send_user_notification

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


async def create_approval(
    db: aiosqlite.Connection,
    *,
    story_id: str,
    approval_type: str,
    payload_dict: dict[str, Any] | None = None,
    recommended_action: str | None = None,
    risk_level: str | None = None,
    nudge: Nudge | None = None,
    orchestrator_pid: int | None = None,
    commit: bool = True,
) -> ApprovalRecord:
    """统一创建 approval 记录。

    流程：生成 ID → 插入 DB → commit → nudge → bell 通知。
    DB 写事务中不 await 外部 IO。

    当 ``commit=False``（SAVEPOINT 内调用），nudge 和 bell 通知会被抑制——
    因为数据尚未对其他连接可见，此时通知会导致 poll loop 空转。
    调用方需在外层 commit 后自行发送 nudge / bell。

    Args:
        db: 活跃的 aiosqlite 连接。
        story_id: 关联的 Story ID。
        approval_type: Approval 类型。
        payload_dict: Approval 负载数据（序列化为 JSON）。
        recommended_action: 推荐操作。若为 None 则从映射表推导。
        risk_level: 风险级别。
        nudge: 进程内 Nudge 实例（可选）。
        orchestrator_pid: Orchestrator PID，进程外 nudge 用（可选）。
        commit: 是否自动 commit。False 时抑制通知，调用方负责 commit 后通知。

    Returns:
        创建的 ApprovalRecord。
    """
    now = datetime.now(tz=UTC)
    approval_id = str(uuid.uuid4())

    # 推导推荐操作
    if recommended_action is None:
        recommended_action = APPROVAL_RECOMMENDED_ACTIONS.get(approval_type)

    payload_json = json.dumps(payload_dict) if payload_dict else None

    _risk = cast(Literal["high", "medium", "low"] | None, risk_level)
    approval = ApprovalRecord(
        approval_id=approval_id,
        story_id=story_id,
        approval_type=approval_type,
        status="pending",
        payload=payload_json,
        created_at=now,
        recommended_action=recommended_action,
        risk_level=_risk,
    )

    # DB 写入（commit 由参数控制——SAVEPOINT 内调用时 commit=False）
    await insert_approval(db, approval, commit=commit)

    logger.info(
        "approval_created",
        approval_id=approval_id,
        story_id=story_id,
        approval_type=approval_type,
        recommended_action=recommended_action,
        risk_level=risk_level,
    )

    # commit=False 时数据尚未对其他连接可见，此时发 nudge 会导致
    # poll loop 空转。调用方需在 commit 后自行 nudge / bell。
    if not commit:
        return approval

    # commit 后再发 nudge / bell（不在写事务中 await 外部 IO）
    if nudge is not None:
        nudge.notify()
    elif orchestrator_pid is not None:
        from ato.nudge import send_external_nudge

        try:
            send_external_nudge(orchestrator_pid)
        except (ProcessLookupError, PermissionError):
            logger.warning(
                "approval_nudge_failed",
                orchestrator_pid=orchestrator_pid,
            )

    # 用户可见 bell 通知（自包含短 ID + 快捷命令）
    level = APPROVAL_TYPE_TO_NOTIFICATION.get(approval_type, "normal")
    short_id = approval_id[:8]
    notification_msg = f"[{short_id}] {approval_type} (story: {story_id})"
    if recommended_action:
        # 优先用 payload 中的 options（创建时可自定义），fallback 到默认表
        valid: list[str] = []
        if payload_dict:
            opts = payload_dict.get("options")
            if isinstance(opts, list) and all(isinstance(o, str) for o in opts):
                valid = opts
        if not valid:
            valid = APPROVAL_DEFAULT_VALID_OPTIONS.get(approval_type, [])
        if recommended_action in valid:
            notification_msg += f" → ato approve {short_id} --decision {recommended_action}"
    send_user_notification(level, notification_msg)

    return approval
