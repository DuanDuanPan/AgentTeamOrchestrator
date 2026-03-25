"""theme — 三重状态编码模块。

定义展示语义层的状态编码（颜色 + Unicode 图标 + 文字标签），
以及从领域状态（StoryStatus / ApprovalStatus / TaskStatus）到展示语义的映射函数。

展示语义（running/active/awaiting/failed/done/frozen/info）不是数据库状态值，
仅用于 TUI 视觉呈现。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StatusCode:
    """三重编码状态：icon + TCSS 颜色变量 + 文字标签。"""

    icon: str
    color_var: str
    label: str


# 展示语义 → 三重编码映射 (AC2)
STATUS_CODES: dict[str, StatusCode] = {
    "running": StatusCode(icon="●", color_var="$success", label="运行中"),
    "active": StatusCode(icon="◐", color_var="$info", label="活跃"),
    "awaiting": StatusCode(icon="◆", color_var="$warning", label="等待中"),
    "failed": StatusCode(icon="✖", color_var="$error", label="失败"),
    "done": StatusCode(icon="✔", color_var="$success", label="已完成"),
    "frozen": StatusCode(icon="⏸", color_var="$error", label="冻结"),
    "info": StatusCode(icon="ℹ", color_var="$muted", label="信息"),
}

_INFO_FALLBACK = STATUS_CODES["info"]


def format_status(visual_status: str) -> StatusCode:
    """返回展示语义对应的三重编码。未知状态回退到 info。"""
    return STATUS_CODES.get(visual_status, _INFO_FALLBACK)


# ---------------------------------------------------------------------------
# 领域状态 → 展示语义映射
# ---------------------------------------------------------------------------

_STORY_STATUS_MAP: dict[str, str] = {
    "backlog": "info",
    "planning": "active",
    "ready": "awaiting",
    "in_progress": "running",
    "review": "active",
    "uat": "awaiting",
    "done": "done",
    "blocked": "frozen",
}

_APPROVAL_STATUS_MAP: dict[str, str] = {
    "pending": "awaiting",
    "approved": "done",
    "rejected": "failed",
}

_TASK_STATUS_MAP: dict[str, str] = {
    "pending": "awaiting",
    "running": "running",
    "paused": "frozen",
    "completed": "done",
    "failed": "failed",
}


def map_story_to_visual_status(status: str) -> str:
    """将 StoryStatus 映射到展示语义。"""
    return _STORY_STATUS_MAP.get(status, "info")


def map_approval_to_visual_status(status: str) -> str:
    """将 ApprovalStatus 映射到展示语义。"""
    return _APPROVAL_STATUS_MAP.get(status, "info")


def map_task_to_visual_status(status: str) -> str:
    """将 TaskStatus 映射到展示语义。"""
    return _TASK_STATUS_MAP.get(status, "info")
