"""三重状态编码模块测试。

验证 StatusCode 完整性、所有展示语义有 icon/color/label，
以及领域状态（StoryStatus / ApprovalStatus / TaskStatus）
均能映射到合法展示语义。
"""

from __future__ import annotations

from typing import get_args

import pytest

from ato.models.schemas import ApprovalStatus, StoryStatus, TaskStatus
from ato.tui.theme import (
    STATUS_CODES,
    StatusCode,
    format_status,
    map_approval_to_visual_status,
    map_story_to_visual_status,
    map_task_to_visual_status,
)

# 所有展示语义
VISUAL_STATUSES = {"running", "active", "awaiting", "failed", "done", "frozen", "info"}


class TestStatusCodeCompleteness:
    """验证所有展示语义完整性。"""

    def test_all_visual_statuses_defined(self) -> None:
        """STATUS_CODES 包含所有展示语义。"""
        assert set(STATUS_CODES.keys()) == VISUAL_STATUSES

    def test_no_missing_icon(self) -> None:
        """每个 StatusCode 都有非空 icon。"""
        for name, code in STATUS_CODES.items():
            assert code.icon, f"{name} 缺少 icon"

    def test_no_missing_color_var(self) -> None:
        """每个 StatusCode 都有非空 color_var。"""
        for name, code in STATUS_CODES.items():
            assert code.color_var, f"{name} 缺少 color_var"

    def test_no_missing_label(self) -> None:
        """每个 StatusCode 都有非空 label。"""
        for name, code in STATUS_CODES.items():
            assert code.label, f"{name} 缺少 label"

    def test_status_code_is_frozen(self) -> None:
        """StatusCode 应为不可变。"""
        code = STATUS_CODES["running"]
        with pytest.raises(AttributeError):
            code.icon = "X"  # type: ignore[misc]


class TestFormatStatus:
    """验证 format_status 返回正确 StatusCode。"""

    def test_known_status(self) -> None:
        code = format_status("running")
        assert isinstance(code, StatusCode)
        assert code.icon == "●"
        assert code.color_var == "$success"

    def test_unknown_status_returns_info(self) -> None:
        code = format_status("unknown_status")
        assert code == STATUS_CODES["info"]


class TestStoryStatusMapping:
    """验证所有 StoryStatus 值都能映射到合法展示语义。"""

    @pytest.mark.parametrize("status", get_args(StoryStatus))
    def test_all_story_statuses_map(self, status: str) -> None:
        visual = map_story_to_visual_status(status)
        assert visual in VISUAL_STATUSES, f"StoryStatus '{status}' 映射到非法展示语义 '{visual}'"

    def test_in_progress_maps_to_running(self) -> None:
        assert map_story_to_visual_status("in_progress") == "running"

    def test_blocked_maps_to_frozen(self) -> None:
        assert map_story_to_visual_status("blocked") == "frozen"

    def test_done_maps_to_done(self) -> None:
        assert map_story_to_visual_status("done") == "done"

    def test_review_maps_to_active(self) -> None:
        assert map_story_to_visual_status("review") == "active"

    def test_ready_maps_to_awaiting(self) -> None:
        assert map_story_to_visual_status("ready") == "awaiting"


class TestApprovalStatusMapping:
    """验证所有 ApprovalStatus 值都能映射到合法展示语义。"""

    @pytest.mark.parametrize("status", get_args(ApprovalStatus))
    def test_all_approval_statuses_map(self, status: str) -> None:
        visual = map_approval_to_visual_status(status)
        assert visual in VISUAL_STATUSES, f"ApprovalStatus '{status}' 映射到非法展示语义 '{visual}'"

    def test_pending_maps_to_awaiting(self) -> None:
        assert map_approval_to_visual_status("pending") == "awaiting"

    def test_approved_maps_to_done(self) -> None:
        assert map_approval_to_visual_status("approved") == "done"

    def test_rejected_maps_to_failed(self) -> None:
        assert map_approval_to_visual_status("rejected") == "failed"


class TestTaskStatusMapping:
    """验证所有 TaskStatus 值都能映射到合法展示语义。"""

    @pytest.mark.parametrize("status", get_args(TaskStatus))
    def test_all_task_statuses_map(self, status: str) -> None:
        visual = map_task_to_visual_status(status)
        assert visual in VISUAL_STATUSES, f"TaskStatus '{status}' 映射到非法展示语义 '{visual}'"

    def test_running_maps_to_running(self) -> None:
        assert map_task_to_visual_status("running") == "running"

    def test_failed_maps_to_failed(self) -> None:
        assert map_task_to_visual_status("failed") == "failed"

    def test_completed_maps_to_done(self) -> None:
        assert map_task_to_visual_status("completed") == "done"

    def test_paused_maps_to_frozen(self) -> None:
        assert map_task_to_visual_status("paused") == "frozen"
