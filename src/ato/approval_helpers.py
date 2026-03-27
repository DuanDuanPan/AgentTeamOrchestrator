"""approval_helpers — 统一 approval 创建 API + 共享审批决策辅助函数。

所有 approval 创建统一走此模块，避免通知 / nudge 逻辑散落在 models/db.py 中。
DB 写事务中不 await 外部 IO——先 commit 再 nudge / bell。

Story 6.3a 新增：format_approval_summary()、resolve_binary_decision()、
is_binary_approval() 供 CLI 和 TUI 共用。
"""

from __future__ import annotations

import contextlib
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


# ---------------------------------------------------------------------------
# 共享审批摘要生成 (Story 6.3a — 从 cli.py 提取)
# ---------------------------------------------------------------------------


def format_approval_summary(approval_type: str, payload: str | None) -> str:
    """从 approval_type + payload 生成确定性摘要。

    CLI 和 TUI 共用，保证两处摘要完全一致。
    """
    payload_dict: dict[str, object] = {}
    if payload:
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            payload_dict = json.loads(payload)

    templates: dict[str, str] = {
        "merge_authorization": "Merge 授权请求",
        "session_timeout": "Interactive session 超时",
        "crash_recovery": "崩溃恢复决策",
        "blocking_abnormal": "Blocking 异常数量超阈值",
        "budget_exceeded": "预算超限",
        "regression_failure": "回归测试失败",
        "convergent_loop_escalation": "Convergent Loop 需人工介入",
        "batch_confirmation": "Batch 确认",
        "timeout": "任务超时",
        "precommit_failure": "Pre-commit 检查失败",
        "rebase_conflict": "Rebase 冲突需处理",
        "needs_human_review": "需要人工审阅",
    }
    summary = templates.get(approval_type, approval_type)

    # 附加关键 payload 信息
    if approval_type == "session_timeout" and "elapsed_seconds" in payload_dict:
        elapsed = payload_dict["elapsed_seconds"]
        summary += f" ({elapsed}s)"
    elif approval_type == "blocking_abnormal" and "blocking_count" in payload_dict:
        count = payload_dict["blocking_count"]
        threshold = payload_dict.get("threshold", "?")
        summary += f" ({count}/{threshold})"
    elif approval_type == "crash_recovery" and "phase" in payload_dict:
        summary += f" (phase: {payload_dict['phase']})"

    return summary


# ---------------------------------------------------------------------------
# 二选一审批 y/n 决策映射 (Story 6.3a)
# ---------------------------------------------------------------------------

# 本 Story 支持的二选一审批类型及其 y/n 对应的具体 decision
_BINARY_APPROVAL_DECISIONS: dict[str, tuple[str, str]] = {
    # approval_type: (y_decision, n_decision)
    "merge_authorization": ("approve", "reject"),
    "blocking_abnormal": ("confirm_fix", "human_review"),
    "budget_exceeded": ("increase_budget", "reject"),
    "timeout": ("continue_waiting", "abandon"),
    "batch_confirmation": ("confirm", "reject"),
}

# y/n 对应的操作标签（用于右下面板提示）
_BINARY_APPROVAL_LABELS: dict[str, tuple[str, str]] = {
    "merge_authorization": ("合并", "拒绝"),
    "blocking_abnormal": ("确认修复", "人工审阅"),
    "budget_exceeded": ("增加预算", "拒绝"),
    "timeout": ("继续等待", "放弃"),
    "batch_confirmation": ("确认", "拒绝"),
}

# 二元 decisions → status 映射
_BINARY_DECISIONS = {"approve", "reject"}


def is_binary_approval(approval_type: str, payload: str | None = None) -> bool:
    """判断审批是否属于 6.3a 支持的二选一审批。

    条件：
    1. approval_type 在 _BINARY_APPROVAL_DECISIONS 中
    2. 如果 payload.options 存在，选项数 ≤ 2
    """
    if approval_type not in _BINARY_APPROVAL_DECISIONS:
        return False
    # 检查 payload.options 是否超过 2
    if payload:
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            pd = json.loads(payload)
            options = pd.get("options")
            if isinstance(options, list) and len(options) > 2:
                return False
    return True


def resolve_binary_decision(approval_type: str, key: Literal["y", "n"]) -> tuple[str, str] | None:
    """从 approval_type + y/n 键解析具体 decision 和写入 status。

    Returns:
        (decision, status) 元组。None 表示该类型不支持二选一。
    """
    mapping = _BINARY_APPROVAL_DECISIONS.get(approval_type)
    if mapping is None:
        return None
    decision = mapping[0] if key == "y" else mapping[1]
    # status 规则：approve → "approved", reject → "rejected", 其它 → "approved"
    if decision in _BINARY_DECISIONS:
        status = "approved" if decision == "approve" else "rejected"
    else:
        status = "approved"
    return (decision, status)


def get_binary_approval_labels(approval_type: str) -> tuple[str, str] | None:
    """返回 (y_label, n_label) 用于右下面板显示。"""
    return _BINARY_APPROVAL_LABELS.get(approval_type)


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
