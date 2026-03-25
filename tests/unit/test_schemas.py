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
    CodexOutput,
    ConfigError,
    FindingRecord,
    RecoveryError,
    SchemaValidationIssue,
    StateTransitionError,
    StoryRecord,
    TaskRecord,
    ValidationResult,
    compute_dedup_hash,
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


# ---------------------------------------------------------------------------
# CodexOutput
# ---------------------------------------------------------------------------


class TestCodexOutput:
    def test_model_validate_defaults(self) -> None:
        output = CodexOutput.model_validate(
            {
                "status": "success",
                "exit_code": 0,
            }
        )
        assert output.cache_read_input_tokens == 0
        assert output.model_name is None
        assert output.text_result == ""
        assert output.cost_usd == 0.0

    def test_model_validate_full(self) -> None:
        output = CodexOutput.model_validate(
            {
                "status": "success",
                "exit_code": 0,
                "text_result": "review done",
                "cost_usd": 0.05,
                "input_tokens": 1000,
                "output_tokens": 50,
                "cache_read_input_tokens": 400,
                "model_name": "codex-mini-latest",
                "session_id": "thread-123",
            }
        )
        assert output.cache_read_input_tokens == 400
        assert output.model_name == "codex-mini-latest"
        assert output.input_tokens == 1000

    def test_structured_output_mapping(self) -> None:
        output = CodexOutput.model_validate(
            {
                "status": "success",
                "exit_code": 0,
                "structured_output": {"findings": [{"severity": "blocking"}]},
            }
        )
        assert output.structured_output is not None
        assert output.structured_output["findings"][0]["severity"] == "blocking"

    def test_extra_fields_ignored(self) -> None:
        """CodexOutput 继承 AdapterResult (extra='ignore')，额外字段不报错。"""
        output = CodexOutput.model_validate(
            {
                "status": "success",
                "exit_code": 0,
                "unknown_field": "should be ignored",
            }
        )
        assert output.status == "success"


# ---------------------------------------------------------------------------
# FindingRecord (Story 3.1)
# ---------------------------------------------------------------------------


def _valid_finding_data() -> dict[str, object]:
    return {
        "finding_id": "f-001",
        "story_id": "story-001",
        "round_num": 1,
        "severity": "blocking",
        "description": "Missing error handling",
        "status": "open",
        "file_path": "src/ato/core.py",
        "rule_id": "E001",
        "dedup_hash": "abc123",
        "line_number": None,
        "fix_suggestion": None,
        "created_at": _NOW,
    }


class TestFindingRecord:
    def test_finding_record_valid(self) -> None:
        """全字段构建成功。"""
        record = FindingRecord.model_validate(_valid_finding_data())
        assert record.finding_id == "f-001"
        assert record.severity == "blocking"
        assert record.status == "open"
        assert record.round_num == 1

    def test_finding_record_strict(self) -> None:
        """extra 字段被拒绝（_StrictBase extra='forbid'）。"""
        data = _valid_finding_data()
        data["unknown_field"] = "should fail"
        with pytest.raises(ValidationError):
            FindingRecord.model_validate(data)

    def test_finding_record_invalid_severity(self) -> None:
        """非法 severity 被拒绝。"""
        data = _valid_finding_data()
        data["severity"] = "critical"
        with pytest.raises(ValidationError):
            FindingRecord.model_validate(data)

    def test_finding_record_invalid_status(self) -> None:
        """非法 status 被拒绝。"""
        data = _valid_finding_data()
        data["status"] = "resolved"
        with pytest.raises(ValidationError):
            FindingRecord.model_validate(data)

    def test_finding_record_optional_fields(self) -> None:
        """line_number 和 fix_suggestion 可选字段接受值。"""
        data = _valid_finding_data()
        data["line_number"] = 42
        data["fix_suggestion"] = "Add try/except"
        record = FindingRecord.model_validate(data)
        assert record.line_number == 42
        assert record.fix_suggestion == "Add try/except"


# ---------------------------------------------------------------------------
# compute_dedup_hash (Story 3.1)
# ---------------------------------------------------------------------------


class TestComputeDedupHash:
    def test_deterministic(self) -> None:
        """相同输入 → 相同 hash。"""
        h1 = compute_dedup_hash("src/a.py", "E001", "blocking", "line too long")
        h2 = compute_dedup_hash("src/a.py", "E001", "blocking", "line too long")
        assert h1 == h2

    def test_normalization(self) -> None:
        """\"line too long\" vs \"LINE TOO LONG\" → 相同 hash。"""
        h1 = compute_dedup_hash("src/a.py", "E001", "blocking", "line too long")
        h2 = compute_dedup_hash("src/a.py", "E001", "blocking", "LINE TOO LONG")
        assert h1 == h2

    def test_whitespace_normalization(self) -> None:
        """多余空白被压缩为单个空格。"""
        h1 = compute_dedup_hash("src/a.py", "E001", "blocking", "line too long")
        h2 = compute_dedup_hash("src/a.py", "E001", "blocking", "line  too\n\tlong")
        assert h1 == h2

    def test_different_severity(self) -> None:
        """severity 不同 → hash 不同。"""
        h1 = compute_dedup_hash("src/a.py", "E001", "blocking", "line too long")
        h2 = compute_dedup_hash("src/a.py", "E001", "suggestion", "line too long")
        assert h1 != h2

    def test_different_file_path(self) -> None:
        """file_path 不同 → hash 不同。"""
        h1 = compute_dedup_hash("src/a.py", "E001", "blocking", "desc")
        h2 = compute_dedup_hash("src/b.py", "E001", "blocking", "desc")
        assert h1 != h2

    def test_hash_is_sha256_hex(self) -> None:
        """返回值是 64 字符的十六进制字符串。"""
        h = compute_dedup_hash("file.py", "R001", "blocking", "test")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)


# ---------------------------------------------------------------------------
# SchemaValidationIssue (Story 3.1)
# ---------------------------------------------------------------------------


class TestSchemaValidationIssue:
    def test_model_valid(self) -> None:
        """基本构建验证。"""
        issue = SchemaValidationIssue(
            path="findings.0.severity",
            message="'critical' is not one of ['blocking', 'suggestion']",
        )
        assert issue.path == "findings.0.severity"
        assert "critical" in issue.message

    def test_default_schema_path(self) -> None:
        """schema_path 默认为空字符串。"""
        issue = SchemaValidationIssue(path="$", message="error")
        assert issue.schema_path == ""

    def test_extra_field_rejected(self) -> None:
        """extra 字段被拒绝。"""
        with pytest.raises(ValidationError):
            SchemaValidationIssue(path="$", message="error", extra_field="bad")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# ValidationResult (Story 3.1)
# ---------------------------------------------------------------------------


class TestValidationResult:
    def test_passed_result(self) -> None:
        result = ValidationResult(passed=True)
        assert result.passed is True
        assert result.errors == []

    def test_failed_result(self) -> None:
        errors = [SchemaValidationIssue(path="$", message="missing required")]
        result = ValidationResult(passed=False, errors=errors)
        assert result.passed is False
        assert len(result.errors) == 1
