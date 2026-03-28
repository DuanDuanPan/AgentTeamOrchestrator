"""test_bmad_adapter — BmadAdapter 与 failure helper 测试。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from ato.adapters.bmad_adapter import (
    BmadAdapter,
    record_parse_failure,
)
from ato.models.schemas import (
    BmadParseResult,
    BmadSkillType,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"

# ---------------------------------------------------------------------------
# 测试辅助
# ---------------------------------------------------------------------------

_SKILL_MAP = {
    "code_review": BmadSkillType.CODE_REVIEW,
    "story_validation": BmadSkillType.STORY_VALIDATION,
    "architecture_review": BmadSkillType.ARCHITECTURE_REVIEW,
    "qa_report": BmadSkillType.QA_REPORT,
}


def _load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _load_expected(name: str) -> dict[str, Any]:
    result: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return result


def _fixture_pairs() -> list[tuple[str, str, BmadSkillType]]:
    """收集所有 bmad fixture 对 (md, expected_json, skill_type)。"""
    pairs: list[tuple[str, str, BmadSkillType]] = []
    for skill_key, skill_type in _SKILL_MAP.items():
        for i in range(1, 6):
            md_name = f"bmad_{skill_key}_{i:02d}.md"
            json_name = f"bmad_{skill_key}_{i:02d}_expected.json"
            if (FIXTURES / md_name).exists() and (FIXTURES / json_name).exists():
                pairs.append((md_name, json_name, skill_type))
    return pairs


class _FakeSemanticRunner:
    """Fake semantic parser runner for testing."""

    def __init__(self, findings: list[dict[str, Any]]) -> None:
        self._findings = findings
        self.called = False

    async def parse_markdown(
        self,
        markdown: str,
        *,
        skill_type: BmadSkillType,
        story_id: str,
    ) -> list[dict[str, Any]]:
        self.called = True
        return self._findings


class _FailingSemanticRunner:
    """Semantic runner that always raises."""

    async def parse_markdown(
        self,
        markdown: str,
        *,
        skill_type: BmadSkillType,
        story_id: str,
    ) -> list[dict[str, Any]]:
        msg = "LLM call failed"
        raise RuntimeError(msg)


# ---------------------------------------------------------------------------
# Fixture 批量参数化测试 (Task 5.1)
# ---------------------------------------------------------------------------


class TestFixtureBatchParsing:
    """参数化测试：20 个 fixture 的批量解析。"""

    @pytest.mark.parametrize(
        ("md_name", "json_name", "skill_type"),
        _fixture_pairs(),
        ids=[p[0].replace(".md", "") for p in _fixture_pairs()],
    )
    async def test_fixture_parsing(
        self, md_name: str, json_name: str, skill_type: BmadSkillType
    ) -> None:
        markdown = _load_fixture(md_name)
        expected = _load_expected(json_name)

        adapter = BmadAdapter()
        result = await adapter.parse(markdown, skill_type=skill_type, story_id="test-story")

        # 基本字段验证
        assert result.skill_type == skill_type
        assert result.verdict == expected["verdict"]
        assert result.parser_mode == expected["parser_mode"]
        assert len(result.findings) == expected["finding_count"]

        # 逐条 finding 验证
        for actual_f, expected_f in zip(result.findings, expected["findings"], strict=True):
            assert actual_f.severity == expected_f["severity"]
            assert actual_f.category == expected_f["category"]
            assert actual_f.rule_id == expected_f["rule_id"]
            if "file_path" in expected_f:
                assert actual_f.file_path == expected_f["file_path"]
            if "line" in expected_f:
                assert actual_f.line == expected_f["line"]

    async def test_batch_success_rate_above_95_percent(self) -> None:
        """AC2: 批量解析成功率 ≥ 95%。"""
        pairs = _fixture_pairs()
        assert len(pairs) >= 20, f"Need at least 20 fixtures, found {len(pairs)}"

        successes = 0
        adapter = BmadAdapter()
        for md_name, _, skill_type in pairs:
            markdown = _load_fixture(md_name)
            result = await adapter.parse(markdown, skill_type=skill_type, story_id="test-story")
            if result.parser_mode != "failed":
                successes += 1

        rate = successes / len(pairs)
        assert rate >= 0.95, f"Success rate {rate:.0%} < 95%"


# ---------------------------------------------------------------------------
# Deterministic fast-path 单测 (Task 5.2)
# ---------------------------------------------------------------------------


class TestDeterministicFastPath:
    async def test_code_review_with_findings(self) -> None:
        md = _load_fixture("bmad_code_review_01.md")
        adapter = BmadAdapter()
        result = await adapter.parse(md, skill_type=BmadSkillType.CODE_REVIEW, story_id="s1")
        assert result.parser_mode == "deterministic"
        assert result.verdict == "changes_requested"
        assert any(f.severity == "blocking" for f in result.findings)

    async def test_code_review_clean(self) -> None:
        md = _load_fixture("bmad_code_review_02.md")
        adapter = BmadAdapter()
        result = await adapter.parse(md, skill_type=BmadSkillType.CODE_REVIEW, story_id="s1")
        assert result.parser_mode == "deterministic"
        assert result.verdict == "approved"
        assert result.findings == []

    async def test_story_validation_pass(self) -> None:
        md = _load_fixture("bmad_story_validation_01.md")
        adapter = BmadAdapter()
        result = await adapter.parse(md, skill_type=BmadSkillType.STORY_VALIDATION, story_id="s1")
        assert result.parser_mode == "deterministic"
        assert result.verdict == "approved"

    async def test_story_validation_fail(self) -> None:
        md = _load_fixture("bmad_story_validation_02.md")
        adapter = BmadAdapter()
        result = await adapter.parse(md, skill_type=BmadSkillType.STORY_VALIDATION, story_id="s1")
        assert result.parser_mode == "deterministic"
        assert result.verdict == "changes_requested"

    async def test_story_validation_pass_english(self) -> None:
        md = _load_fixture("bmad_story_validation_06.md")
        adapter = BmadAdapter()
        result = await adapter.parse(md, skill_type=BmadSkillType.STORY_VALIDATION, story_id="s1")
        assert result.parser_mode == "deterministic"
        assert result.verdict == "approved"
        assert all(f.severity == "suggestion" for f in result.findings)

    async def test_story_validation_fail_english(self) -> None:
        md = _load_fixture("bmad_story_validation_07.md")
        adapter = BmadAdapter()
        result = await adapter.parse(md, skill_type=BmadSkillType.STORY_VALIDATION, story_id="s1")
        assert result.parser_mode == "deterministic"
        assert result.verdict == "changes_requested"
        assert all(f.severity == "blocking" for f in result.findings)

    async def test_architecture_review_ready(self) -> None:
        md = _load_fixture("bmad_architecture_review_01.md")
        adapter = BmadAdapter()
        result = await adapter.parse(
            md, skill_type=BmadSkillType.ARCHITECTURE_REVIEW, story_id="s1"
        )
        assert result.parser_mode == "deterministic"
        assert result.verdict == "approved"

    async def test_qa_report_with_issues(self) -> None:
        md = _load_fixture("bmad_qa_report_01.md")
        adapter = BmadAdapter()
        result = await adapter.parse(md, skill_type=BmadSkillType.QA_REPORT, story_id="s1")
        assert result.parser_mode == "deterministic"
        assert result.verdict == "changes_requested"

    async def test_json_array_fast_path(self) -> None:
        md = _load_fixture("bmad_code_review_05.md")
        adapter = BmadAdapter()
        result = await adapter.parse(md, skill_type=BmadSkillType.CODE_REVIEW, story_id="s1")
        assert result.parser_mode == "deterministic"
        assert len(result.findings) == 2

    async def test_json_object_fast_path(self) -> None:
        json_input = json.dumps(
            {
                "findings": [
                    {
                        "severity": "blocking",
                        "category": "bug",
                        "description": "Null pointer",
                        "file_path": "src/main.py",
                        "line": 10,
                        "rule_id": "code_review.bug",
                    }
                ]
            }
        )
        adapter = BmadAdapter()
        result = await adapter.parse(
            json_input, skill_type=BmadSkillType.CODE_REVIEW, story_id="s1"
        )
        assert result.parser_mode == "deterministic"
        assert len(result.findings) == 1
        assert result.findings[0].file_path == "src/main.py"


# ---------------------------------------------------------------------------
# Semantic fallback 单测 (Task 5.3)
# ---------------------------------------------------------------------------


class TestSemanticFallback:
    async def test_fallback_returns_structured_output(self) -> None:
        fake_findings = [
            {
                "severity": "blocking",
                "category": "missing_test",
                "description": "No unit test for parse()",
                "file_path": "src/ato/adapters/bmad_adapter.py",
                "rule_id": "qa.missing_test",
            }
        ]
        runner = _FakeSemanticRunner(fake_findings)
        adapter = BmadAdapter(semantic_runner=runner)

        # Unrecognizable Markdown triggers fallback
        result = await adapter.parse(
            "This is completely unstructured text with no markers.",
            skill_type=BmadSkillType.CODE_REVIEW,
            story_id="s1",
        )
        assert runner.called
        assert result.parser_mode == "semantic_fallback"
        assert result.verdict == "changes_requested"
        assert len(result.findings) == 1
        assert result.findings[0].file_path == "src/ato/adapters/bmad_adapter.py"

    async def test_fallback_validates_through_schema(self) -> None:
        runner = _FakeSemanticRunner(
            [
                {"severity": "suggestion", "category": "style", "description": "Use snake_case"},
            ]
        )
        adapter = BmadAdapter(semantic_runner=runner)
        result = await adapter.parse(
            "Random unstructured text.",
            skill_type=BmadSkillType.QA_REPORT,
            story_id="s1",
        )
        # Should pass model_validate
        assert isinstance(result, BmadParseResult)
        assert result.findings[0].file_path == "N/A"

    async def test_fallback_failure_returns_parse_failed(self) -> None:
        runner = _FailingSemanticRunner()
        adapter = BmadAdapter(semantic_runner=runner)
        result = await adapter.parse(
            "Unrecognizable content.",
            skill_type=BmadSkillType.CODE_REVIEW,
            story_id="s1",
        )
        assert result.parser_mode == "failed"
        assert result.verdict == "parse_failed"
        assert result.findings == []

    async def test_no_runner_returns_parse_failed(self) -> None:
        adapter = BmadAdapter()
        result = await adapter.parse(
            "Completely unrecognizable gibberish.",
            skill_type=BmadSkillType.CODE_REVIEW,
            story_id="s1",
        )
        assert result.parser_mode == "failed"
        assert result.verdict == "parse_failed"


# ---------------------------------------------------------------------------
# 失败路径测试 (Task 5.4)
# ---------------------------------------------------------------------------


class TestRecordParseFailure:
    async def test_creates_approval_record(self, initialized_db_path: Path) -> None:
        import aiosqlite

        from ato.models.db import get_pending_approvals, insert_story

        async with aiosqlite.connect(str(initialized_db_path)) as db:
            db.row_factory = aiosqlite.Row
            # Seed story (FK constraint)
            from ato.models.schemas import StoryRecord

            now = datetime.now(tz=UTC)
            story = StoryRecord.model_validate(
                {
                    "story_id": "test-story-1",
                    "title": "Test",
                    "status": "in_progress",
                    "current_phase": "dev",
                    "worktree_path": None,
                    "created_at": now,
                    "updated_at": now,
                }
            )
            await insert_story(db, story)

            # Create failed parse result
            parse_result = BmadParseResult.model_validate(
                {
                    "skill_type": BmadSkillType.CODE_REVIEW,
                    "verdict": "parse_failed",
                    "findings": [],
                    "parser_mode": "failed",
                    "raw_markdown_hash": "abc",
                    "raw_output_preview": "raw text preview",
                    "parse_error": "No structure found",
                    "parsed_at": now,
                }
            )

            approval = await record_parse_failure(
                parse_result=parse_result,
                story_id="test-story-1",
                skill_type=BmadSkillType.CODE_REVIEW,
                db=db,
            )

            assert approval.approval_type == "needs_human_review"
            assert approval.status == "pending"
            assert "bmad_parse_failed" in (approval.payload or "")

            # Verify in DB
            pending = await get_pending_approvals(db)
            assert len(pending) == 1
            assert pending[0].approval_id == approval.approval_id

    async def test_calls_notifier(self, initialized_db_path: Path) -> None:
        import aiosqlite

        from ato.models.db import insert_story
        from ato.models.schemas import StoryRecord

        async with aiosqlite.connect(str(initialized_db_path)) as db:
            now = datetime.now(tz=UTC)
            story = StoryRecord.model_validate(
                {
                    "story_id": "test-story-2",
                    "title": "Test",
                    "status": "in_progress",
                    "current_phase": "dev",
                    "worktree_path": None,
                    "created_at": now,
                    "updated_at": now,
                }
            )
            await insert_story(db, story)

            parse_result = BmadParseResult.model_validate(
                {
                    "skill_type": BmadSkillType.CODE_REVIEW,
                    "verdict": "parse_failed",
                    "findings": [],
                    "parser_mode": "failed",
                    "raw_markdown_hash": "abc",
                    "raw_output_preview": "preview",
                    "parse_error": "Failed",
                    "parsed_at": now,
                }
            )

            notified = False

            def fake_notifier() -> None:
                nonlocal notified
                notified = True

            await record_parse_failure(
                parse_result=parse_result,
                story_id="test-story-2",
                skill_type=BmadSkillType.CODE_REVIEW,
                db=db,
                notifier=fake_notifier,
            )
            assert notified

    async def test_structlog_records_preview(
        self, initialized_db_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        import aiosqlite

        from ato.models.db import insert_story
        from ato.models.schemas import StoryRecord

        async with aiosqlite.connect(str(initialized_db_path)) as db:
            now = datetime.now(tz=UTC)
            story = StoryRecord.model_validate(
                {
                    "story_id": "test-story-3",
                    "title": "Test",
                    "status": "in_progress",
                    "current_phase": "dev",
                    "worktree_path": None,
                    "created_at": now,
                    "updated_at": now,
                }
            )
            await insert_story(db, story)

            long_preview = "x" * 600
            parse_result = BmadParseResult.model_validate(
                {
                    "skill_type": BmadSkillType.CODE_REVIEW,
                    "verdict": "parse_failed",
                    "findings": [],
                    "parser_mode": "failed",
                    "raw_markdown_hash": "abc",
                    "raw_output_preview": long_preview[:500],
                    "parse_error": "Parse failed",
                    "parsed_at": now,
                }
            )

            await record_parse_failure(
                parse_result=parse_result,
                story_id="test-story-3",
                skill_type=BmadSkillType.CODE_REVIEW,
                db=db,
            )
            # structlog warning was emitted (test that it doesn't crash)


# ---------------------------------------------------------------------------
# 边界情况 (Task 5.6)
# ---------------------------------------------------------------------------


class TestEdgeCases:
    async def test_empty_input(self) -> None:
        adapter = BmadAdapter()
        result = await adapter.parse("", skill_type=BmadSkillType.CODE_REVIEW, story_id="s1")
        # Empty string won't match any deterministic pattern
        assert result.parser_mode == "failed"
        assert result.verdict == "parse_failed"

    async def test_plain_text_no_structure(self) -> None:
        adapter = BmadAdapter()
        result = await adapter.parse(
            "Just some plain text without any markdown structure or patterns.",
            skill_type=BmadSkillType.STORY_VALIDATION,
            story_id="s1",
        )
        assert result.parser_mode == "failed"

    async def test_clean_review_no_findings(self) -> None:
        md = (
            "## Summary\n\nThis is a clean review. No findings were raised."
            "\n\n0 intent_gap, 0 bad_spec, 0 patch, 0 defer findings."
        )
        adapter = BmadAdapter()
        result = await adapter.parse(md, skill_type=BmadSkillType.CODE_REVIEW, story_id="s1")
        assert result.verdict == "approved"
        assert result.findings == []

    async def test_missing_location_defaults_to_na(self) -> None:
        """Finding without file_path should default to 'N/A'."""
        runner = _FakeSemanticRunner(
            [
                {"severity": "suggestion", "category": "style", "description": "Fix indent"},
            ]
        )
        adapter = BmadAdapter(semantic_runner=runner)
        result = await adapter.parse(
            "Unstructured.", skill_type=BmadSkillType.CODE_REVIEW, story_id="s1"
        )
        assert result.findings[0].file_path == "N/A"
        assert result.findings[0].line is None

    async def test_unknown_heading_ignored(self) -> None:
        """Non-category headings in code review should not produce findings."""
        md = """## Next Steps

- Consider running the planning workflow.

0 intent_gap, 0 bad_spec, 0 patch, 0 defer findings. 2 findings rejected as noise."""
        adapter = BmadAdapter()
        result = await adapter.parse(md, skill_type=BmadSkillType.CODE_REVIEW, story_id="s1")
        assert result.findings == []

    async def test_raw_markdown_hash_computed(self) -> None:
        adapter = BmadAdapter()
        md = "test content"
        result = await adapter.parse(md, skill_type=BmadSkillType.CODE_REVIEW, story_id="s1")
        assert len(result.raw_markdown_hash) == 64

    async def test_raw_output_preview_truncated(self) -> None:
        long_md = "x" * 1000
        adapter = BmadAdapter()
        result = await adapter.parse(long_md, skill_type=BmadSkillType.CODE_REVIEW, story_id="s1")
        assert len(result.raw_output_preview) == 500

    async def test_model_validate_on_result(self) -> None:
        """AC1: 结果经 Pydantic model_validate() 验证。"""
        md = _load_fixture("bmad_code_review_01.md")
        adapter = BmadAdapter()
        result = await adapter.parse(md, skill_type=BmadSkillType.CODE_REVIEW, story_id="s1")
        # Re-validate the result
        validated = BmadParseResult.model_validate(result.model_dump())
        assert validated.skill_type == result.skill_type

    async def test_parser_mode_field_present(self) -> None:
        """AC1: 结果明确标记 parser_mode。"""
        md = _load_fixture("bmad_code_review_01.md")
        adapter = BmadAdapter()
        result = await adapter.parse(md, skill_type=BmadSkillType.CODE_REVIEW, story_id="s1")
        assert result.parser_mode in ("deterministic", "semantic_fallback", "failed")


# ---------------------------------------------------------------------------
# Review finding 回归测试 (对抗性样例)
# ---------------------------------------------------------------------------


class TestReviewFinding1BoldLabelCodeReview:
    """Finding 1: bold-label 列表形式的 code-review 被误判为 clean。"""

    async def test_bold_label_format_parsed(self) -> None:
        """真实 BMAD code-review 模板用 `- **Intent Gaps**: ...` 格式。"""
        md = (
            "# Code Review Results\n\n"
            '- **Intent Gaps**: "These findings suggest the captured intent '
            'is incomplete."\n'
            "  - Missing retry logic for CLI timeouts\n"
            "  - No fallback when structured output is empty\n\n"
            '- **Patch**: "These are fixable code issues:"\n'
            "  - Unguarded `self._running` — `src/ato/core.py:42`\n\n"
            '- **Defer**: "Pre-existing issues:"\n'
            "  - Consider structured logging migration\n\n"
            "**Summary:** 2 intent_gap, 0 bad_spec, 1 patch, 1 defer "
            "findings. 0 findings rejected as noise.\n"
        )
        adapter = BmadAdapter()
        result = await adapter.parse(md, skill_type=BmadSkillType.CODE_REVIEW, story_id="s1")
        assert result.parser_mode == "deterministic"
        assert result.verdict == "changes_requested"
        assert len(result.findings) == 4
        cats = [f.category for f in result.findings]
        assert cats.count("intent_gap") == 2
        assert cats.count("patch") == 1
        assert cats.count("defer") == 1

    async def test_bold_label_no_false_clean(self) -> None:
        """有 findings 的 bold-label 报告不应返回 approved。"""
        md = (
            '- **Bad Spec**: "The spec should be amended:"\n'
            "  - Missing concurrency model specification\n\n"
            "**Summary:** 0 intent_gap, 1 bad_spec, 0 patch, 0 defer.\n"
        )
        adapter = BmadAdapter()
        result = await adapter.parse(md, skill_type=BmadSkillType.CODE_REVIEW, story_id="s1")
        assert result.verdict == "changes_requested"
        assert len(result.findings) == 1
        assert result.findings[0].category == "bad_spec"


class TestReviewFinding2NeedsWork:
    """Finding 2: NEEDS WORK 被误判为通过。"""

    async def test_needs_work_is_blocking(self) -> None:
        md = (
            "## Architecture Validation Results\n\n"
            "### Architecture Readiness Assessment\n"
            "**Overall Status:** NEEDS WORK\n"
            "**Confidence Level:** Low\n"
        )
        adapter = BmadAdapter()
        result = await adapter.parse(
            md, skill_type=BmadSkillType.ARCHITECTURE_REVIEW, story_id="s1"
        )
        assert result.verdict == "changes_requested"
        blocking = [f for f in result.findings if f.severity == "blocking"]
        assert len(blocking) >= 1
        assert any("NEEDS WORK" in f.description for f in blocking)

    async def test_ready_is_not_blocking(self) -> None:
        md = (
            "## Architecture Validation Results\n\n"
            "### Architecture Readiness Assessment\n"
            "**Overall Status:** READY FOR IMPLEMENTATION\n"
        )
        adapter = BmadAdapter()
        result = await adapter.parse(
            md, skill_type=BmadSkillType.ARCHITECTURE_REVIEW, story_id="s1"
        )
        status_findings = [f for f in result.findings if f.category == "status"]
        assert len(status_findings) == 0


class TestReviewFinding3QATableMerge:
    """Finding 3: QA table findings 在有详细 section 时被丢弃。"""

    async def test_table_warn_not_dropped_when_issues_exist(self) -> None:
        md = (
            "# Test Quality Review: test_core.py\n\n"
            "**Quality Score**: 72/100\n"
            "**Recommendation**: Request Changes\n\n"
            "## Quality Criteria Assessment\n\n"
            "| Criterion | Status | Violations | Notes |\n"
            "| --- | --- | --- | --- |\n"
            "| Hard Waits | ❌ FAIL | 2 | Uses sleep |\n"
            "| Flakiness | ⚠️ WARN | 1 | Timing-dependent |\n\n"
            "## Critical Issues (Must Fix)\n\n"
            "### 1. Hard-coded sleep calls\n\n"
            "**Severity**: P0 (Critical)\n"
            "**Location**: `tests/unit/test_core.py:45`\n"
            "**Criterion**: Hard Waits\n\n"
        )
        adapter = BmadAdapter()
        result = await adapter.parse(md, skill_type=BmadSkillType.QA_REPORT, story_id="s1")
        # Should have the critical issue AND the WARN from table
        cats = [f.category for f in result.findings]
        assert "hard_waits" in cats
        # flakiness is only in the table and must not be dropped
        flakiness = [f for f in result.findings if "flakiness" in f.category.lower()]
        assert len(flakiness) >= 1
        assert flakiness[0].severity == "suggestion"


class TestReviewFinding5BlankLineBetweenBullets:
    """空行不应截断 bold section 提取。"""

    async def test_bold_list_with_blank_between_bullets(self) -> None:
        md = (
            '- **Patch**: "Fixable issues:"\n'
            "  - First patch issue — `src/a.py:10`\n"
            "\n"
            "  - Second patch issue — `src/b.py:20`\n"
            "\n"
            "**Summary:** 0 intent_gap, 0 bad_spec, 2 patch, 0 defer.\n"
        )
        adapter = BmadAdapter()
        result = await adapter.parse(md, skill_type=BmadSkillType.CODE_REVIEW, story_id="s1")
        assert len(result.findings) == 2

    async def test_bold_section_with_blank_between_bullets(self) -> None:
        md = (
            "## Architecture Validation Results\n\n"
            "### Architecture Readiness Assessment\n"
            "**Overall Status:** READY FOR IMPLEMENTATION\n"
            "**Areas for Future Enhancement:**\n"
            "- First enhancement\n"
            "\n"
            "- Second enhancement\n"
            "\n"
            "- Third enhancement\n"
        )
        adapter = BmadAdapter()
        result = await adapter.parse(
            md,
            skill_type=BmadSkillType.ARCHITECTURE_REVIEW,
            story_id="s1",
        )
        assert len(result.findings) == 3
