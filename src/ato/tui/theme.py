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


# ---------------------------------------------------------------------------
# TCSS 变量 → Rich hex 颜色映射（ThreeQuestionHeader 等 render() 用）
# ---------------------------------------------------------------------------

RICH_COLORS: dict[str, str] = {
    "$success": "#50fa7b",
    "$warning": "#f1fa8c",
    "$error": "#ff5555",
    "$info": "#8be9fd",
    "$accent": "#bd93f9",
    "$muted": "#8390b7",
    "$text": "#f8f8f2",
}


# ---------------------------------------------------------------------------
# Story 列表排序 (Story 6.2b)
# ---------------------------------------------------------------------------

VISUAL_STATUS_SORT_ORDER: dict[str, int] = {
    "awaiting": 0,
    "active": 1,
    "running": 2,
    "frozen": 3,
    "done": 4,
    "info": 5,
}
"""展示语义排序优先级：awaiting 最高 → info 最低。running 紧邻 active。"""


def sort_stories_by_status(
    stories: list[dict[str, object]],
) -> list[dict[str, object]]:
    """按展示语义排序 story 列表。

    排序规则：
    1. 按 visual status 优先级升序（awaiting → active → running → frozen → done → info）
    2. 同一 visual status 内按 updated_at 降序（最近更新的在前）

    Args:
        stories: story 字典列表，至少包含 ``status`` 和 ``updated_at`` 键。

    Returns:
        排序后的新列表。
    """

    def _sort_key(story: dict[str, object]) -> tuple[int, str]:
        status = str(story.get("status", ""))
        visual = map_story_to_visual_status(status)
        priority = VISUAL_STATUS_SORT_ORDER.get(visual, 99)
        # updated_at 降序：反转字符串排序
        updated_at = str(story.get("updated_at", ""))
        return (priority, _invert_str(updated_at))

    return sorted(stories, key=_sort_key)


def _invert_str(s: str) -> str:
    """反转字符串用于降序排列——对 ISO 时间戳有效。"""
    # 用 chr 补码实现字符串反转排序
    return "".join(chr(0xFFFF - ord(c)) for c in s)


# ---------------------------------------------------------------------------
# Risk level → 颜色变量映射 (Story 6.3a)
# ---------------------------------------------------------------------------


_RISK_COLOR_MAP: dict[str, str] = {
    "high": "$error",
    "medium": "$warning",
    "low": "$success",
}


def map_risk_to_color(risk_level: str | None) -> str:
    """将 risk_level 映射到 TCSS 颜色变量名。"""
    return _RISK_COLOR_MAP.get(risk_level or "", "$muted")
