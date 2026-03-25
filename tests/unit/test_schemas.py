"""test_schemas — Pydantic 模型验证测试。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from ato.models.schemas import (
    SCHEMA_VERSION,
    ApprovalRecord,
    ATOError,
    BmadFinding,
    BmadParseResult,
    BmadSkillType,
    CLIAdapterError,
    CodexOutput,
    ConfigError,
    RecoveryError,
    StateTransitionError,
    StoryRecord,
    TaskRecord,
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
# BmadSkillType
# ---------------------------------------------------------------------------


class TestBmadSkillType:
    def test_all_values(self) -> None:
        assert BmadSkillType.CODE_REVIEW.value == "code_review"
        assert BmadSkillType.STORY_VALIDATION.value == "story_validation"
        assert BmadSkillType.ARCHITECTURE_REVIEW.value == "architecture_review"
        assert BmadSkillType.QA_REPORT.value == "qa_report"

    def test_is_str_enum(self) -> None:
        assert isinstance(BmadSkillType.CODE_REVIEW, str)

    def test_from_alias_code_review(self) -> None:
        assert BmadSkillType.from_alias("code-review") == BmadSkillType.CODE_REVIEW
        assert BmadSkillType.from_alias("bmad-code-review") == BmadSkillType.CODE_REVIEW

    def test_from_alias_story_validation(self) -> None:
        assert BmadSkillType.from_alias("story-validation") == BmadSkillType.STORY_VALIDATION
        assert BmadSkillType.from_alias("validate-create-story") == BmadSkillType.STORY_VALIDATION

    def test_from_alias_architecture(self) -> None:
        assert BmadSkillType.from_alias("architecture-review") == BmadSkillType.ARCHITECTURE_REVIEW
        assert BmadSkillType.from_alias("create-architecture") == BmadSkillType.ARCHITECTURE_REVIEW

    def test_from_alias_architecture_frontmatter(self) -> None:
        """workflowType: 'architecture' from template frontmatter."""
        assert BmadSkillType.from_alias("architecture") == BmadSkillType.ARCHITECTURE_REVIEW

    def test_from_alias_qa(self) -> None:
        assert BmadSkillType.from_alias("qa-report") == BmadSkillType.QA_REPORT
        assert BmadSkillType.from_alias("test-review") == BmadSkillType.QA_REPORT
        assert BmadSkillType.from_alias("testarch-test-review") == BmadSkillType.QA_REPORT

    def test_from_alias_exact_value(self) -> None:
        assert BmadSkillType.from_alias("code_review") == BmadSkillType.CODE_REVIEW

    def test_from_alias_bmm_module_names(self) -> None:
        """Finding 4: 仓库 module-help.csv 中实际暴露的 bmm 别名。"""
        from_alias = BmadSkillType.from_alias
        assert from_alias("bmad-bmm-code-review") == BmadSkillType.CODE_REVIEW
        assert from_alias("bmad-bmm-create-story") == BmadSkillType.STORY_VALIDATION
        assert from_alias("bmad-bmm-create-architecture") == BmadSkillType.ARCHITECTURE_REVIEW

    def test_from_alias_tea_module_names(self) -> None:
        """Finding 4: 仓库 module-help.csv 中实际暴露的 tea 别名。"""
        from_alias = BmadSkillType.from_alias
        assert from_alias("bmad-tea-testarch-test-review") == BmadSkillType.QA_REPORT

    def test_from_alias_skill_protocol(self) -> None:
        """Finding 4: skill: 前缀形式。"""
        from_alias = BmadSkillType.from_alias
        assert from_alias("skill:bmad-code-review") == BmadSkillType.CODE_REVIEW
        assert from_alias("skill:bmad-create-story") == BmadSkillType.STORY_VALIDATION
        assert from_alias("skill:bmad-create-architecture") == BmadSkillType.ARCHITECTURE_REVIEW
        assert from_alias("skill:bmad-testarch-test-review") == BmadSkillType.QA_REPORT

    def test_from_alias_unknown_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown BMAD skill"):
            BmadSkillType.from_alias("unknown-skill")


# ---------------------------------------------------------------------------
# BmadFinding
# ---------------------------------------------------------------------------


def _valid_finding_data() -> dict[str, object]:
    return {
        "severity": "blocking",
        "category": "intent_gap",
        "description": "Missing error handling for timeout case",
        "file_path": "src/ato/core.py",
        "line": 42,
        "rule_id": "code_review.intent_gap",
        "raw_location": "src/ato/core.py:42",
    }


class TestBmadFinding:
    def test_valid_data_accepted(self) -> None:
        finding = BmadFinding.model_validate(_valid_finding_data())
        assert finding.severity == "blocking"
        assert finding.category == "intent_gap"
        assert finding.file_path == "src/ato/core.py"
        assert finding.line == 42

    def test_minimal_data_accepted(self) -> None:
        finding = BmadFinding.model_validate(
            {
                "severity": "suggestion",
                "category": "defer",
                "description": "Consider adding docstring",
                "file_path": "N/A",
                "rule_id": "code_review.defer",
            }
        )
        assert finding.line is None
        assert finding.raw_location is None

    def test_invalid_severity_rejected(self) -> None:
        data = _valid_finding_data()
        data["severity"] = "critical"
        with pytest.raises(ValidationError):
            BmadFinding.model_validate(data)

    def test_extra_field_rejected(self) -> None:
        data = _valid_finding_data()
        data["unknown"] = "extra"
        with pytest.raises(ValidationError):
            BmadFinding.model_validate(data)

    def test_missing_description_rejected(self) -> None:
        data = _valid_finding_data()
        del data["description"]
        with pytest.raises(ValidationError):
            BmadFinding.model_validate(data)

    def test_dedup_hash_populated(self) -> None:
        finding = BmadFinding.model_validate(_valid_finding_data())
        assert finding.dedup_hash is not None
        assert len(finding.dedup_hash) == 64  # SHA256 hex


# ---------------------------------------------------------------------------
# BmadParseResult
# ---------------------------------------------------------------------------


def _valid_parse_result_data() -> dict[str, object]:
    return {
        "skill_type": BmadSkillType.CODE_REVIEW,
        "verdict": "changes_requested",
        "findings": [BmadFinding.model_validate(_valid_finding_data())],
        "parser_mode": "deterministic",
        "raw_markdown_hash": "abc123def456",
        "raw_output_preview": "# Code Review Results...",
        "parse_error": None,
        "parsed_at": _NOW,
    }


class TestBmadParseResult:
    def test_valid_data_accepted(self) -> None:
        result = BmadParseResult.model_validate(_valid_parse_result_data())
        assert result.skill_type == BmadSkillType.CODE_REVIEW
        assert result.verdict == "changes_requested"
        assert len(result.findings) == 1
        assert result.parser_mode == "deterministic"

    def test_parse_failed_result(self) -> None:
        result = BmadParseResult.model_validate(
            {
                "skill_type": BmadSkillType.CODE_REVIEW,
                "verdict": "parse_failed",
                "findings": [],
                "parser_mode": "failed",
                "raw_markdown_hash": "abc",
                "raw_output_preview": "some raw text",
                "parse_error": "No recognizable structure found",
                "parsed_at": _NOW,
            }
        )
        assert result.verdict == "parse_failed"
        assert result.parser_mode == "failed"
        assert result.findings == []

    def test_clean_review_result(self) -> None:
        result = BmadParseResult.model_validate(
            {
                "skill_type": BmadSkillType.STORY_VALIDATION,
                "verdict": "approved",
                "findings": [],
                "parser_mode": "deterministic",
                "raw_markdown_hash": "hash",
                "raw_output_preview": "Story validated OK",
                "parse_error": None,
                "parsed_at": _NOW,
            }
        )
        assert result.verdict == "approved"
        assert result.findings == []

    def test_invalid_parser_mode_rejected(self) -> None:
        data = _valid_parse_result_data()
        data["parser_mode"] = "magic"
        with pytest.raises(ValidationError):
            BmadParseResult.model_validate(data)

    def test_extra_field_rejected(self) -> None:
        data = _valid_parse_result_data()
        data["extra"] = "oops"
        with pytest.raises(ValidationError):
            BmadParseResult.model_validate(data)


# ---------------------------------------------------------------------------
# compute_dedup_hash
# ---------------------------------------------------------------------------


class TestComputeDedupHash:
    def test_consistent_output(self) -> None:
        h1 = compute_dedup_hash("src/a.py", "rule.x", "blocking", "Some desc")
        h2 = compute_dedup_hash("src/a.py", "rule.x", "blocking", "Some desc")
        assert h1 == h2

    def test_different_inputs_differ(self) -> None:
        h1 = compute_dedup_hash("src/a.py", "rule.x", "blocking", "desc A")
        h2 = compute_dedup_hash("src/b.py", "rule.x", "blocking", "desc A")
        assert h1 != h2

    def test_whitespace_normalization(self) -> None:
        h1 = compute_dedup_hash("f.py", "r", "blocking", "  hello   world  ")
        h2 = compute_dedup_hash("f.py", "r", "blocking", "hello world")
        assert h1 == h2

    def test_case_normalization(self) -> None:
        h1 = compute_dedup_hash("f.py", "r", "blocking", "Hello World")
        h2 = compute_dedup_hash("f.py", "r", "blocking", "hello world")
        assert h1 == h2

    def test_punctuation_preserved(self) -> None:
        """去重哈希不应移除标点，否则后续 round matching 规则会漂移。"""
        h1 = compute_dedup_hash("f.py", "r", "blocking", "hello, world!")
        h2 = compute_dedup_hash("f.py", "r", "blocking", "hello world")
        assert h1 != h2

    def test_returns_sha256_hex(self) -> None:
        h = compute_dedup_hash("f.py", "r", "blocking", "desc")
        assert len(h) == 64  # SHA256 hex digest
