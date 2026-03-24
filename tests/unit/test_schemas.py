"""test_schemas — Pydantic 模型验证测试。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from ato.models.schemas import (
    SCHEMA_VERSION,
    ApprovalRecord,
    ATOError,
    CLIAdapterError,
    ConfigError,
    RecoveryError,
    StateTransitionError,
    StoryRecord,
    TaskRecord,
)

# ---------------------------------------------------------------------------
# 测试辅助数据
# ---------------------------------------------------------------------------

_NOW = datetime.now(tz=UTC)


def _valid_story_data() -> dict[str, object]:
    return {
        "story_id": "story-001",
        "title": "测试用 story",
        "status": "in_progress",
        "current_phase": "dev",
        "worktree_path": None,
        "created_at": _NOW,
        "updated_at": _NOW,
    }


def _valid_task_data() -> dict[str, object]:
    return {
        "task_id": "task-001",
        "story_id": "story-001",
        "phase": "dev",
        "role": "developer",
        "cli_tool": "claude",
        "status": "pending",
        "pid": None,
        "expected_artifact": None,
        "context_briefing": None,
        "started_at": None,
        "completed_at": None,
        "exit_code": None,
        "cost_usd": None,
        "duration_ms": None,
        "error_message": None,
    }


def _valid_approval_data() -> dict[str, object]:
    return {
        "approval_id": "appr-001",
        "story_id": "story-001",
        "approval_type": "gate",
        "status": "pending",
        "payload": None,
        "decision": None,
        "decided_at": None,
        "created_at": _NOW,
    }


# ---------------------------------------------------------------------------
# SCHEMA_VERSION
# ---------------------------------------------------------------------------


class TestSchemaVersion:
    def test_schema_version_is_positive_int(self) -> None:
        assert isinstance(SCHEMA_VERSION, int)
        assert SCHEMA_VERSION >= 1


# ---------------------------------------------------------------------------
# 异常类层次
# ---------------------------------------------------------------------------


class TestExceptionHierarchy:
    def test_all_inherit_from_ato_error(self) -> None:
        for cls in (CLIAdapterError, StateTransitionError, RecoveryError, ConfigError):
            assert issubclass(cls, ATOError)

    def test_ato_error_is_exception(self) -> None:
        assert issubclass(ATOError, Exception)

    def test_exceptions_carry_message(self) -> None:
        err = RecoveryError("migration failed at v2")
        assert "migration failed at v2" in str(err)


# ---------------------------------------------------------------------------
# StoryRecord
# ---------------------------------------------------------------------------


class TestStoryRecord:
    def test_valid_data_accepted(self) -> None:
        story = StoryRecord.model_validate(_valid_story_data())
        assert story.story_id == "story-001"
        assert story.status == "in_progress"

    def test_invalid_status_rejected(self) -> None:
        data = _valid_story_data()
        data["status"] = "invalid_status"
        with pytest.raises(ValidationError):
            StoryRecord.model_validate(data)

    def test_missing_required_field_rejected(self) -> None:
        data = _valid_story_data()
        del data["title"]
        with pytest.raises(ValidationError):
            StoryRecord.model_validate(data)

    def test_extra_field_rejected(self) -> None:
        data = _valid_story_data()
        data["extra_field"] = "should fail"
        with pytest.raises(ValidationError):
            StoryRecord.model_validate(data)

    def test_strict_mode_rejects_string_for_datetime(self) -> None:
        """strict=True 下字符串不能隐式转为 datetime。"""
        data = _valid_story_data()
        data["created_at"] = "2026-03-24T00:00:00+00:00"
        with pytest.raises(ValidationError):
            StoryRecord.model_validate(data)

    def test_optional_worktree_path(self) -> None:
        data = _valid_story_data()
        data["worktree_path"] = "/tmp/worktree"
        story = StoryRecord.model_validate(data)
        assert story.worktree_path == "/tmp/worktree"


# ---------------------------------------------------------------------------
# TaskRecord
# ---------------------------------------------------------------------------


class TestTaskRecord:
    def test_valid_data_accepted(self) -> None:
        task = TaskRecord.model_validate(_valid_task_data())
        assert task.task_id == "task-001"
        assert task.cli_tool == "claude"

    def test_invalid_cli_tool_rejected(self) -> None:
        data = _valid_task_data()
        data["cli_tool"] = "gpt"
        with pytest.raises(ValidationError):
            TaskRecord.model_validate(data)

    def test_invalid_status_rejected(self) -> None:
        data = _valid_task_data()
        data["status"] = "cancelled"
        with pytest.raises(ValidationError):
            TaskRecord.model_validate(data)

    def test_strict_mode_rejects_string_for_int(self) -> None:
        """strict=True 下字符串 '42' 不能隐式转为 int。"""
        data = _valid_task_data()
        data["pid"] = "42"
        with pytest.raises(ValidationError):
            TaskRecord.model_validate(data)

    def test_strict_mode_rejects_string_for_float(self) -> None:
        """strict=True 下字符串 '1.5' 不能隐式转为 float。"""
        data = _valid_task_data()
        data["cost_usd"] = "1.5"
        with pytest.raises(ValidationError):
            TaskRecord.model_validate(data)

    def test_optional_fields_accept_values(self) -> None:
        data = _valid_task_data()
        data["pid"] = 12345
        data["exit_code"] = 0
        data["cost_usd"] = 0.05
        data["duration_ms"] = 3000
        data["expected_artifact"] = "output.json"
        data["error_message"] = None
        data["started_at"] = _NOW
        data["completed_at"] = _NOW
        task = TaskRecord.model_validate(data)
        assert task.pid == 12345
        assert task.cost_usd == 0.05


# ---------------------------------------------------------------------------
# ApprovalRecord
# ---------------------------------------------------------------------------


class TestApprovalRecord:
    def test_valid_data_accepted(self) -> None:
        approval = ApprovalRecord.model_validate(_valid_approval_data())
        assert approval.approval_id == "appr-001"

    def test_invalid_status_rejected(self) -> None:
        data = _valid_approval_data()
        data["status"] = "cancelled"
        with pytest.raises(ValidationError):
            ApprovalRecord.model_validate(data)

    def test_extra_field_rejected(self) -> None:
        data = _valid_approval_data()
        data["unknown"] = True
        with pytest.raises(ValidationError):
            ApprovalRecord.model_validate(data)

    def test_strict_mode_rejects_int_for_str(self) -> None:
        """strict=True 下 int 不能隐式转为 str。"""
        data = _valid_approval_data()
        data["approval_type"] = 123
        with pytest.raises(ValidationError):
            ApprovalRecord.model_validate(data)
