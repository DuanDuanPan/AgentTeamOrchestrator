"""semantic_parser — Claude CLI semantic fallback parser。

当 deterministic fast-path 无法解析 BMAD skill 输出时，
调用 Claude CLI (claude -p --json-schema) 提取结构化 findings。

实现 ``bmad_adapter.SemanticParserRunner`` 协议。
"""

from __future__ import annotations

import asyncio
import json
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
    """Semantic fallback parser using Claude CLI with structured output.

    Implements the ``SemanticParserRunner`` protocol defined in
    ``bmad_adapter.py``.
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
        """Extract findings from markdown using Claude CLI.

        Returns:
            List of finding dicts matching BmadFinding schema.

        Raises:
            Exception: on CLI failure or parse error (caught by BmadAdapter
            stage-2 fallback handler).
        """
        skill_context = _SKILL_CONTEXT.get(skill_type, "Extract all findings.")
        prompt = _EXTRACT_PROMPT_TEMPLATE.format(
            skill_context=skill_context,
            markdown=markdown,
        )

        cmd = [
            "claude",
            "--dangerously-skip-permissions",
            "-p",
            prompt,
            "--output-format",
            "json",
            "--model",
            self._model,
            "--json-schema",
            json.dumps(_FINDINGS_SCHEMA),
        ]

        logger.info(
            "semantic_parser_start",
            story_id=story_id,
            skill_type=skill_type.value,
            model=self._model,
            input_chars=len(markdown),
        )

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=self._timeout
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            logger.warning(
                "semantic_parser_timeout",
                story_id=story_id,
                timeout=self._timeout,
            )
            raise

        exit_code = proc.returncode or 0
        stdout_text = stdout_bytes.decode(errors="replace")
        stderr_text = stderr_bytes.decode(errors="replace")

        if exit_code != 0:
            logger.warning(
                "semantic_parser_cli_error",
                story_id=story_id,
                exit_code=exit_code,
                stderr_preview=stderr_text[:300],
            )
            raise RuntimeError(f"Claude CLI exited with code {exit_code}: {stderr_text[:200]}")

        # Parse JSON output — Claude --output-format json returns a JSON object
        # with a "result" field containing the text, or with --json-schema
        # returns the structured output directly.
        try:
            data = json.loads(stdout_text)
        except json.JSONDecodeError:
            # Try extracting JSON from stream-json or wrapped format
            data = _extract_json_from_output(stdout_text)

        # Navigate to findings list
        findings = _extract_findings_from_response(data)

        logger.info(
            "semantic_parser_success",
            story_id=story_id,
            skill_type=skill_type.value,
            findings_count=len(findings),
        )

        return findings


def _extract_json_from_output(raw: str) -> dict[str, Any]:
    """Try to extract JSON from various Claude output formats."""
    # Try line by line (stream-json format has one JSON per line)
    for line in raw.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
            if isinstance(parsed, dict):
                # stream-json: look for result event
                if parsed.get("type") == "result":
                    result_text = parsed.get("result", "")
                    if isinstance(result_text, str):
                        inner: dict[str, Any] = json.loads(result_text)
                        return inner
                    if isinstance(result_text, dict):
                        return result_text
                    return {"result": result_text}
                # Direct JSON object with findings
                if "findings" in parsed:
                    return parsed
        except json.JSONDecodeError:
            continue

    raise ValueError(f"Cannot extract JSON from Claude output: {raw[:300]}")


def _extract_findings_from_response(data: Any) -> list[dict[str, Any]]:
    """Navigate Claude response structure to get findings list."""
    if isinstance(data, dict):
        # Direct: {"findings": [...]}
        if "findings" in data:
            findings = data["findings"]
            if isinstance(findings, list):
                return list(findings)

        # Wrapped: {"result": "{\"findings\": [...]}"}
        result = data.get("result")
        if isinstance(result, str):
            try:
                inner = json.loads(result)
                if isinstance(inner, dict) and "findings" in inner:
                    return list(inner["findings"])
            except json.JSONDecodeError:
                pass

    if isinstance(data, list):
        return list(data)

    raise ValueError(f"Cannot extract findings from response: {str(data)[:300]}")
