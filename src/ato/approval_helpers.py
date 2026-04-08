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
        "preflight_failure": "Worktree 边界门控失败",
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


# ---------------------------------------------------------------------------
# 多选异常审批辅助函数 (Story 6.3b)
# ---------------------------------------------------------------------------

# 异常审批类型人类可读标题（AC3 映射表）
_EXCEPTION_TYPE_TITLES: dict[str, str] = {
    "regression_failure": "REGRESSION FAILURE",
    "session_timeout": "SESSION TIMEOUT",
    "crash_recovery": "CRASH RECOVERY",
    "precommit_failure": "PRE-COMMIT FAILURE",
    "rebase_conflict": "REBASE CONFLICT",
    "needs_human_review": "NEEDS HUMAN REVIEW",
    "preflight_failure": "WORKTREE PREFLIGHT FAILURE",
    "convergent_loop_escalation": "CONVERGENT LOOP ESCALATION",
}

# 选项的中文标签（供 ExceptionApprovalPanel 展示）
_OPTION_LABELS: dict[str, str] = {
    "revert": "回滚当前 merge",
    "fix_forward": "保持 queue 冻结并创建修复路径",
    "pause": "保持冻结，等待人工处理",
    "restart": "重新启动",
    "resume": "从中断处继续",
    "abandon": "放弃",
    "retry": "重试",
    "manual_fix": "人工修复",
    "skip": "跳过",
    "manual_resolve": "人工解决冲突",
    "manual_commit_and_retry": "人工提交后重试",
    "escalate": "升级处理",
    "restart_phase2": "从 Phase 2（梯度降级）重新开始",
    "restart_loop": "从 Phase 1 全量重跑",
}


def resolve_multi_decision(
    approval_type: str, index: int, payload: str | None = None
) -> tuple[str, str]:
    """从 approval_type + 数字索引解析多选决策。

    优先使用 payload.options，但每个 option 必须存在于
    ``APPROVAL_DEFAULT_VALID_OPTIONS[approval_type]`` 白名单中——
    Orchestrator ``_handle_approval_decision()`` 只识别白名单 decision，
    非法 decision 会导致审批卡在"已决定但永远无法消费"的坏状态。
    payload.options 包含任何白名单外的值时整体 fallback 到默认选项。

    Args:
        approval_type: 审批类型。
        index: 0-based 选项索引。
        payload: JSON 编码的 payload 字符串。

    Returns:
        (decision_key, status="approved") 元组。

    Raises:
        ValueError: 索引超出选项范围或无可用选项。
    """
    options = get_options_for_approval(approval_type, payload)

    if not options:
        msg = f"No options available for approval type: {approval_type}"
        raise ValueError(msg)

    if index < 0 or index >= len(options):
        msg = f"Index {index} out of range for {len(options)} options"
        raise ValueError(msg)

    return (options[index], "approved")


def get_exception_context(approval_type: str, payload: dict[str, object]) -> tuple[str, str]:
    """返回 (what, impact) 文本；字段缺失时省略对应行（AC5）。

    严格遵循"缺失即省略"原则——不输出占位符或空值行。
    """
    parts: list[str] = []

    match approval_type:
        case "session_timeout":
            what = "Interactive session 已超过阈值，正在等待操作者决策。"
            if "task_id" in payload:
                parts.append(f"task_id: {payload['task_id']}")
            if "elapsed_seconds" in payload:
                parts.append(f"elapsed_seconds: {payload['elapsed_seconds']}")
        case "crash_recovery":
            what = "任务在 dispatch 或执行过程中失败，需要决定如何恢复。"
            if "phase" in payload:
                parts.append(f"phase: {payload['phase']}")
            if "task_id" in payload:
                parts.append(f"task_id: {payload['task_id']}")
        case "rebase_conflict":
            what = "Worktree rebase 到 main 时产生合并冲突。"
            if "conflict_files" in payload:
                parts.append(f"conflict_files: {payload['conflict_files']}")
            if "worktree_path" in payload:
                parts.append(f"worktree_path: {payload['worktree_path']}")
            if payload.get("stderr"):
                parts.append(f"stderr: {payload['stderr']}")
        case "precommit_failure":
            what = "Pre-commit 检查失败，需要决定重试、人工修复或跳过。"
            if payload.get("error_output"):
                parts.append(f"error_output: {payload['error_output']}")
        case "needs_human_review":
            # Design gate 失败 vs BMAD 解析失败：通过 failure_codes 区分
            if "failure_codes" in payload:
                what = "Design gate 校验失败，设计阶段产出物不完整或无效。"
                if "task_id" in payload:
                    parts.append(f"task_id: {payload['task_id']}")
                if payload.get("reason"):
                    parts.append(f"reason: {payload['reason']}")
                if "artifact_dir" in payload:
                    parts.append(f"artifact_dir: {payload['artifact_dir']}")
                fc = payload.get("failure_codes")
                if fc:
                    parts.append(f"failure_codes: {', '.join(fc) if isinstance(fc, list) else fc}")
                mf = payload.get("missing_files")
                if mf:
                    parts.append(f"missing_files: {', '.join(mf) if isinstance(mf, list) else mf}")
                sr = payload.get("save_report_summary")
                if isinstance(sr, dict):
                    parts.append(f"save_report_summary: {sr}")
            else:
                what = "BMAD 解析失败，需要人工决定是否重试或升级。"
                if "skill_type" in payload:
                    parts.append(f"skill_type: {payload['skill_type']}")
                if "parser_mode" in payload:
                    parts.append(f"parser_mode: {payload['parser_mode']}")
                if payload.get("raw_output_preview"):
                    parts.append(f"preview: {payload['raw_output_preview']}")
                if "task_id" in payload:
                    parts.append(f"task_id: {payload['task_id']}")
        case "convergent_loop_escalation":
            stage = payload.get("stage", "standard")
            if stage == "escalated":
                what = "Convergent Loop 梯度降级（Phase 2）仍未收敛。"
            else:
                what = "Convergent Loop 达到上限仍未收敛。"
            if "stage" in payload:
                parts.append(f"stage: {payload['stage']}")
            if "rounds_completed" in payload:
                parts.append(f"rounds_completed: {payload['rounds_completed']}")
            if "open_blocking_count" in payload:
                parts.append(f"open_blocking_count: {payload['open_blocking_count']}")
            if "final_convergence_rate" in payload:
                parts.append(f"final_convergence_rate: {payload['final_convergence_rate']}")
            if "unresolved_findings" in payload:
                findings = payload["unresolved_findings"]
                count = len(findings) if isinstance(findings, list) else findings
                parts.append(f"unresolved_findings: {count}")
            if "standard_round_summaries" in payload:
                srs = payload["standard_round_summaries"]
                parts.append(f"standard_rounds: {len(srs) if isinstance(srs, list) else srs}")
            if "escalated_round_summaries" in payload:
                ers = payload["escalated_round_summaries"]
                parts.append(f"escalated_rounds: {len(ers) if isinstance(ers, list) else ers}")
        case "regression_failure":
            what = "Regression 在 main 上失败，merge queue 已冻结。"
            if payload.get("reason"):
                parts.append(f"reason: {payload['reason']}")
        case "preflight_failure":
            what = "Worktree 边界门控失败，原边界转换或 merge 已阻止。"
            for key in (
                "gate_type",
                "retry_event",
                "worktree_path",
                "failure_reason",
            ):
                if payload.get(key):
                    parts.append(f"{key}: {payload[key]}")
        case _:
            what = approval_type

    return (what, "\n".join(parts))


def get_exception_type_title(approval_type: str) -> str:
    """返回异常审批类型的人类可读标题。"""
    return _EXCEPTION_TYPE_TITLES.get(approval_type, approval_type.upper().replace("_", " "))


def format_option_labels(approval_type: str, options: list[str]) -> list[str]:
    """返回用户可读的中文标签列表（供 ExceptionApprovalPanel 展示）。"""
    return [_OPTION_LABELS.get(opt, opt) for opt in options]


def get_options_for_approval(approval_type: str, payload: str | None = None) -> list[str]:
    """获取审批的选项列表（优先 payload.options，fallback 到默认）。

    payload.options 中的每个值必须存在于白名单中，否则整体 fallback。
    """
    allowed = set(APPROVAL_DEFAULT_VALID_OPTIONS.get(approval_type, []))
    if payload and allowed:
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            pd = json.loads(payload)
            opts = pd.get("options")
            if (
                isinstance(opts, list)
                and all(isinstance(o, str) for o in opts)
                and all(o in allowed for o in opts)
            ):
                return opts
    return APPROVAL_DEFAULT_VALID_OPTIONS.get(approval_type, [])


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
