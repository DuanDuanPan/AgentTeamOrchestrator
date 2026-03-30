"""semantic_parser — Claude CLI semantic fallback parser。

当 deterministic fast-path 无法解析 BMAD skill 输出时，
通过 ClaudeAdapter + --json-schema 提取结构化 findings。

实现 ``bmad_adapter.SemanticParserRunner`` 协议。
"""

from __future__ import annotations

from typing import Any

import structlog

from ato.models.schemas import BmadSkillType

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

# ---------------------------------------------------------------------------
# JSON Schema: 强制 Claude 返回固定格式
# ---------------------------------------------------------------------------

_FINDINGS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "severity": {
                        "type": "string",
                        "enum": ["blocking", "suggestion"],
                        "description": (
                            "blocking = P0/P1/critical/medium; suggestion = P2/P3/minor"
                        ),
                    },
                    "category": {
                        "type": "string",
                        "description": ("intent_gap, bad_spec, patch, defer, or general"),
                    },
                    "description": {
                        "type": "string",
                        "description": "Finding description",
                    },
                    "file_path": {
                        "type": "string",
                        "description": "Filename (no absolute path), or N/A",
                    },
                    "line": {
                        "type": ["integer", "null"],
                        "description": "Line number, or null if not available",
                    },
                    "rule_id": {
                        "type": "string",
                        "description": "Rule identifier, e.g. code_review.patch",
                    },
                },
                "required": ["severity", "description", "file_path"],
            },
        },
    },
    "required": ["findings"],
}

# ---------------------------------------------------------------------------
# Skill type → extraction context
# ---------------------------------------------------------------------------

_SKILL_CONTEXT: dict[BmadSkillType, str] = {
    BmadSkillType.CODE_REVIEW: (
        "This is a CODE REVIEW output. Extract all open/active findings. "
        "Ignore items marked as fixed/closed/resolved. "
        "Map priority: P0/P1/critical/medium/blocking → blocking; "
        "P2/P3/minor/suggestion → suggestion. "
        "Default category to 'patch' if not determinable."
    ),
    BmadSkillType.STORY_VALIDATION: (
        "This is a STORY VALIDATION output. Extract all unresolved issues. "
        "Items under '已应用增强' (applied enhancements) are NOT findings. "
        "Items under '发现的关键问题' or '剩余风险' that are actionable ARE findings."
    ),
    BmadSkillType.QA_REPORT: (
        "This is a QA TEST REPORT output. Extract all test failures and issues. "
        "Critical Issues → blocking; Recommendations → suggestion. "
        "Map Recommendation 'Request Changes'/'Block' → there are blocking findings."
    ),
    BmadSkillType.ARCHITECTURE_REVIEW: (
        "This is an ARCHITECTURE REVIEW output. Extract all findings. "
        "Items requiring changes → blocking; informational items → suggestion."
    ),
}

_EXTRACT_PROMPT_TEMPLATE = """\
You are a structured data extraction tool. \
Extract all findings from the following LLM review output.

Rules:
- ONLY extract, do NOT judge, modify, or supplement
- severity: "blocking" (P0/P1/critical/medium) or "suggestion" (P2/P3/minor)
- file_path: filename only (no absolute path), "N/A" if unavailable
- line: line number, null if unavailable
- category: "intent_gap"/"bad_spec"/"patch"/"defer"; default "patch"
- rule_id: "code_review.<category>" or "story_validation.<category>"
- IGNORE items marked as fixed/closed/resolved

{skill_context}

--- RAW OUTPUT ---
{markdown}
"""

# ---------------------------------------------------------------------------
# Timeout & process defaults
# ---------------------------------------------------------------------------

_DEFAULT_TIMEOUT = 60
_DEFAULT_MODEL = "sonnet"


# ---------------------------------------------------------------------------
# ClaudeSemanticParser
# ---------------------------------------------------------------------------


class ClaudeSemanticParser:
    """Semantic fallback parser using ClaudeAdapter with structured output.

    Implements the ``SemanticParserRunner`` protocol defined in
    ``bmad_adapter.py``.  Delegates to ``ClaudeAdapter.execute()`` so that
    ``--json-schema`` → ``structured_output`` handling is consistent with
    the rest of the codebase (e.g. ``batch.py`` LLM recommender).
    """

    def __init__(
        self,
        *,
        model: str = _DEFAULT_MODEL,
        timeout: int = _DEFAULT_TIMEOUT,
    ) -> None:
        self._model = model
        self._timeout = timeout

    async def parse_markdown(
        self,
        markdown: str,
        *,
        skill_type: BmadSkillType,
        story_id: str,
    ) -> list[dict[str, Any]]:
        """Extract findings from markdown using ClaudeAdapter.

        Returns:
            List of finding dicts matching BmadFinding schema.

        Raises:
            Exception: on CLI failure or parse error (caught by BmadAdapter
            stage-2 fallback handler).
        """
        from ato.adapters.claude_cli import ClaudeAdapter

        skill_context = _SKILL_CONTEXT.get(skill_type, "Extract all findings.")
        prompt = _EXTRACT_PROMPT_TEMPLATE.format(
            skill_context=skill_context,
            markdown=markdown,
        )

        logger.info(
            "semantic_parser_start",
            story_id=story_id,
            skill_type=skill_type.value,
            model=self._model,
            input_chars=len(markdown),
        )

        adapter = ClaudeAdapter()
        result = await adapter.execute(
            prompt,
            {
                "model": self._model,
                "json_schema": _FINDINGS_SCHEMA,
                "timeout": self._timeout,
            },
        )

        # structured_output is populated by ClaudeOutput.from_json()
        # from the "structured_output" field in Claude CLI's JSON envelope.
        raw = result.structured_output
        if raw is not None and isinstance(raw, dict):
            findings = raw.get("findings")
            if isinstance(findings, list):
                logger.info(
                    "semantic_parser_success",
                    story_id=story_id,
                    skill_type=skill_type.value,
                    findings_count=len(findings),
                )
                return list(findings)

        # Fallback: try parsing text_result as JSON (legacy path)
        if result.text_result:
            import json

            try:
                data = json.loads(result.text_result)
                if isinstance(data, dict) and "findings" in data:
                    findings = data["findings"]
                    if isinstance(findings, list):
                        logger.info(
                            "semantic_parser_success",
                            story_id=story_id,
                            skill_type=skill_type.value,
                            findings_count=len(findings),
                            source="text_result_fallback",
                        )
                        return list(findings)
            except (json.JSONDecodeError, TypeError):
                pass

        raise ValueError(
            f"Claude returned no structured_output with findings. "
            f"text_result preview: {result.text_result[:200]}"
        )
