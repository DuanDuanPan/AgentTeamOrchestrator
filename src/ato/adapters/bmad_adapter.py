"""bmad_adapter — BMAD Markdown → JSON 语义解析。

将 BMAD skill 产生的 Markdown / text artifact 归一化为 canonical JSON。
不继承 BaseAdapter——不负责 CLI 进程生命周期。

支持双阶段解析：
1. deterministic fast-path（零额外调用，处理已知稳定结构）
2. semantic fallback（通过注入的 parser runner 复用 structured-output 能力）
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

import structlog

from ato.models.schemas import (
    ApprovalRecord,
    BmadFinding,
    BmadParseResult,
    BmadSkillType,
    FindingSeverity,
    ParseVerdict,
)

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Parser runner protocol (semantic fallback 注入点)
# ---------------------------------------------------------------------------

_PREVIEW_MAX_CHARS = 500


class SemanticParserRunner(Protocol):
    """Semantic fallback parser runner 接口。

    生产环境中可包装 ClaudeAdapter.execute(structured_output=...)。
    测试中通过 fake runner / mock 注入。
    """

    async def parse_markdown(
        self,
        markdown: str,
        *,
        skill_type: BmadSkillType,
        story_id: str,
    ) -> list[dict[str, Any]]:
        """将 Markdown 解析为 finding dict 列表。

        Returns:
            每个 dict 应包含 BmadFinding 字段：
            severity, category, description, file_path, rule_id 等。
        """
        ...  # pragma: no cover


class ParseFailureNotifier(Protocol):
    """解析失败通知接口。

    同进程内注入 ``Nudge.notify``，跨进程注入 ``send_external_nudge(pid)``，
    测试中注入 mock callback。
    """

    def __call__(self) -> None: ...  # pragma: no cover


async def record_parse_failure(
    *,
    parse_result: BmadParseResult,
    story_id: str,
    skill_type: BmadSkillType,
    db: Any,
    notifier: ParseFailureNotifier | None = None,
) -> ApprovalRecord:
    """记录解析失败，创建 needs_human_review approval 并通知 Orchestrator。

    这是 orchestration concern，不在纯 parser core 中。
    由 caller / helper 调用，不由 ``BmadAdapter.parse()`` 直接调用。

    Args:
        parse_result: 失败的解析结果。
        story_id: 关联的 story ID。
        skill_type: BMAD skill 类型。
        db: aiosqlite 连接。
        notifier: 可选的通知回调。

    Returns:
        创建的 ApprovalRecord。
    """
    from ato.models.db import insert_approval

    now = datetime.now(tz=UTC)
    payload = json.dumps(
        {
            "reason": "bmad_parse_failed",
            "skill_type": skill_type.value,
            "parser_mode": parse_result.parser_mode,
            "error": parse_result.parse_error,
            "raw_output_preview": parse_result.raw_output_preview,
        }
    )
    approval = ApprovalRecord.model_validate(
        {
            "approval_id": str(uuid4()),
            "story_id": story_id,
            "approval_type": "needs_human_review",
            "status": "pending",
            "payload": payload,
            "decision": None,
            "decided_at": None,
            "created_at": now,
        }
    )

    await insert_approval(db, approval)

    logger.warning(
        "bmad_parse_failure_recorded",
        story_id=story_id,
        skill_type=skill_type.value,
        parser_mode=parse_result.parser_mode,
        error=parse_result.parse_error,
        raw_output_preview=parse_result.raw_output_preview[:_PREVIEW_MAX_CHARS],
    )

    if notifier is not None:
        notifier()

    return approval


# ---------------------------------------------------------------------------
# BmadAdapter
# ---------------------------------------------------------------------------


class BmadAdapter:
    """BMAD Markdown → JSON 解析适配器。

    推荐调用链：
    ``ClaudeAdapter.execute()`` / ``CodexAdapter.execute()``
    → ``AdapterResult.text_result``
    → ``await BmadAdapter.parse(...)``
    → ``BmadParseResult``
    """

    def __init__(
        self,
        *,
        semantic_runner: SemanticParserRunner | None = None,
    ) -> None:
        self._semantic_runner = semantic_runner

    async def parse(
        self,
        markdown_output: str,
        *,
        skill_type: BmadSkillType,
        story_id: str,
        parser_context: dict[str, Any] | None = None,
    ) -> BmadParseResult:
        """将 BMAD skill 的 Markdown 输出解析为结构化 JSON。

        Args:
            markdown_output: 原始 Markdown 文本。
            skill_type: BMAD skill 类型。
            story_id: 关联的 story ID。
            parser_context: 可选的额外解析上下文。

        Returns:
            BmadParseResult，经 Pydantic model_validate() 验证。
        """
        raw_hash = hashlib.sha256(markdown_output.encode()).hexdigest()
        preview = markdown_output[:_PREVIEW_MAX_CHARS]
        now = datetime.now(tz=UTC)

        # Stage 1: deterministic fast-path
        findings = _deterministic_parse(markdown_output, skill_type=skill_type)
        if findings is not None:
            verdict = _compute_verdict(findings)
            return BmadParseResult.model_validate(
                {
                    "skill_type": skill_type,
                    "verdict": verdict,
                    "findings": findings,
                    "parser_mode": "deterministic",
                    "raw_markdown_hash": raw_hash,
                    "raw_output_preview": preview,
                    "parse_error": None,
                    "parsed_at": now,
                }
            )

        # Stage 2: semantic fallback
        if self._semantic_runner is not None:
            try:
                raw_findings = await self._semantic_runner.parse_markdown(
                    markdown_output,
                    skill_type=skill_type,
                    story_id=story_id,
                )
                findings_objs = _normalize_raw_findings(raw_findings, skill_type)
                verdict = _compute_verdict(findings_objs)
                return BmadParseResult.model_validate(
                    {
                        "skill_type": skill_type,
                        "verdict": verdict,
                        "findings": findings_objs,
                        "parser_mode": "semantic_fallback",
                        "raw_markdown_hash": raw_hash,
                        "raw_output_preview": preview,
                        "parse_error": None,
                        "parsed_at": now,
                    }
                )
            except Exception as exc:
                logger.warning(
                    "bmad_semantic_fallback_failed",
                    story_id=story_id,
                    skill_type=skill_type.value,
                    error=str(exc),
                )

        # Stage 3: 全部失败
        error_msg = "Both deterministic and semantic parsing failed"
        logger.warning(
            "bmad_parse_failed",
            story_id=story_id,
            skill_type=skill_type.value,
            parser_mode="failed",
            error=error_msg,
            raw_output_preview=preview,
        )
        return BmadParseResult.model_validate(
            {
                "skill_type": skill_type,
                "verdict": "parse_failed",
                "findings": [],
                "parser_mode": "failed",
                "raw_markdown_hash": raw_hash,
                "raw_output_preview": preview,
                "parse_error": error_msg,
                "parsed_at": now,
            }
        )


# ---------------------------------------------------------------------------
# Verdict 计算
# ---------------------------------------------------------------------------


def _compute_verdict(findings: list[BmadFinding]) -> ParseVerdict:
    """根据 findings 计算 verdict。"""
    if not findings:
        return "approved"
    if any(f.severity == "blocking" for f in findings):
        return "changes_requested"
    return "approved"


# ---------------------------------------------------------------------------
# Raw findings 归一化
# ---------------------------------------------------------------------------


def _normalize_raw_findings(
    raw_findings: list[dict[str, Any]],
    skill_type: BmadSkillType,
) -> list[BmadFinding]:
    """将 raw dict 列表归一化为 BmadFinding 列表。"""
    results: list[BmadFinding] = []
    for raw in raw_findings:
        severity = _normalize_severity(raw.get("severity", "suggestion"), skill_type)
        file_path = raw.get("file_path", "N/A")
        line = raw.get("line")
        if isinstance(line, str) and line.isdigit():
            line = int(line)
        elif not isinstance(line, int):
            line = None

        rule_id = raw.get("rule_id", _generate_rule_id(skill_type, raw.get("category", "general")))
        category = raw.get("category", "general")
        description = raw.get("description", "")
        raw_location = raw.get("raw_location")

        results.append(
            BmadFinding.model_validate(
                {
                    "severity": severity,
                    "category": category,
                    "description": description,
                    "file_path": file_path,
                    "line": line,
                    "rule_id": rule_id,
                    "raw_location": raw_location,
                }
            )
        )
    return results


# ---------------------------------------------------------------------------
# Deterministic fast-path 解析
# ---------------------------------------------------------------------------


def _deterministic_parse(
    markdown: str,
    *,
    skill_type: BmadSkillType,
) -> list[BmadFinding] | None:
    """尝试 deterministic fast-path 解析。

    Returns:
        成功时返回 BmadFinding 列表（可能为空——表示 clean review）；
        无法识别结构时返回 None（触发 semantic fallback）。
    """
    stripped = markdown.strip()

    # JSON fast-path
    if stripped.startswith("{") or stripped.startswith("["):
        result = _parse_json_fast_path(stripped, skill_type)
        if result is not None:
            return result

    match skill_type:
        case BmadSkillType.CODE_REVIEW:
            return _parse_code_review(markdown)
        case BmadSkillType.STORY_VALIDATION:
            return _parse_story_validation(markdown)
        case BmadSkillType.ARCHITECTURE_REVIEW:
            return _parse_architecture_review(markdown)
        case BmadSkillType.QA_REPORT:
            return _parse_qa_report(markdown)

    return None  # pragma: no cover


# ---------------------------------------------------------------------------
# JSON fast-path
# ---------------------------------------------------------------------------


def _parse_json_fast_path(
    text: str,
    skill_type: BmadSkillType,
) -> list[BmadFinding] | None:
    """解析 JSON array/object 形态的输出。"""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None

    items: list[dict[str, Any]]
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        # 尝试从常见 key 提取 finding 列表
        raw = data.get("findings", data.get("items", data.get("results", [])))
        items = raw if isinstance(raw, list) else []
        if not items:
            items = [data]
    else:
        return None

    return _normalize_raw_findings(items, skill_type)


# ---------------------------------------------------------------------------
# Code Review 解析
# ---------------------------------------------------------------------------

# 唯一的 category 模式 → (category_slug, severity, section_regex_pattern)
_CODE_REVIEW_SECTIONS: list[tuple[str, FindingSeverity, str]] = [
    ("intent_gap", "blocking", r"intent\s+gaps?"),
    ("bad_spec", "blocking", r"bad\s+spec"),
    ("patch", "blocking", r"patch"),
    ("defer", "suggestion", r"defer"),
]

# 匹配 ## Category Name、**Category Name**:、或 - **Category Name**: 等
_SECTION_RE = re.compile(
    r"^(?:#{2,4}\s+|-\s+\*\*|\*\*)\s*(?:"
    r"intent\s+gaps?|bad\s+spec|patch|defer"
    r")\s*(?:\*\*)?:?",
    re.IGNORECASE | re.MULTILINE,
)

# Summary line: X intent_gap, Y bad_spec, Z patch, W defer
_SUMMARY_RE = re.compile(
    r"(\d+)\s+intent.gap.*?(\d+)\s+bad.spec.*?(\d+)\s+patch.*?(\d+)\s+defer",
    re.IGNORECASE,
)

# Clean review patterns
_CLEAN_REVIEW_RE = re.compile(
    r"(clean review|no findings|zero findings|all.*classified as noise|"
    r"no.*issues.*found|no.*findings.*raised)",
    re.IGNORECASE,
)

_BULLET_RE = re.compile(r"^\s*[-*]\s+", re.MULTILINE)


def _parse_code_review(markdown: str) -> list[BmadFinding] | None:
    """解析 code-review 输出格式。"""
    # 检查是否有 code-review 特征标记
    has_summary = _SUMMARY_RE.search(markdown) is not None
    has_sections = _SECTION_RE.search(markdown) is not None
    has_clean = _CLEAN_REVIEW_RE.search(markdown) is not None

    if not (has_summary or has_sections or has_clean):
        return None

    if has_clean and not has_sections:
        return []

    findings: list[BmadFinding] = []

    # 按 category 提取 section：支持 heading 形式 和 bold-label 列表形式
    # 真实模板用 `- **Intent Gaps**: "..."` 后跟子 bullet
    for category, severity, pattern in _CODE_REVIEW_SECTIONS:
        # 优先 heading 形式（## Intent Gaps）
        section_body = _extract_named_section(markdown, pattern)
        # 其次 bold-label 列表形式（- **Intent Gaps**: ...）
        if not section_body:
            section_body = _extract_bold_list_section(markdown, pattern)
        if not section_body:
            continue

        items = _extract_items_from_section(section_body)
        for title, detail, loc_path, loc_line in items:
            desc = f"{title}: {detail}" if detail else title
            findings.append(
                BmadFinding.model_validate(
                    {
                        "severity": severity,
                        "category": category,
                        "description": desc,
                        "file_path": loc_path or "N/A",
                        "line": loc_line,
                        "rule_id": f"code_review.{category}",
                        "raw_location": (
                            f"{loc_path}:{loc_line}" if loc_path and loc_line else None
                        ),
                    }
                )
            )
    return findings


# ---------------------------------------------------------------------------
# Story Validation 解析
# ---------------------------------------------------------------------------

_SV_RESULT_RE = re.compile(
    r"结果[：:]\s*(PASS|FAIL|INVALID)",
    re.IGNORECASE,
)

_SV_ISSUE_HEADING_RE = re.compile(
    r"^#{2,4}\s+(\d+)[.、]\s*(.+)$",
    re.MULTILINE,
)

_SV_SECTION_RE = re.compile(
    r"(摘要|发现的关键问题|已应用增强|剩余风险|最终结论|"
    r"summary|key issues|enhancements|remaining risks|final conclusion)",
    re.IGNORECASE,
)


def _parse_story_validation(markdown: str) -> list[BmadFinding] | None:
    """解析 story-validation 输出格式。"""
    has_result = _SV_RESULT_RE.search(markdown) is not None
    has_section = _SV_SECTION_RE.search(markdown) is not None
    has_validation = "验证" in markdown or "validation" in markdown.lower()

    if not (has_result or (has_section and has_validation)):
        return None

    findings: list[BmadFinding] = []

    # 提取 result verdict
    result_match = _SV_RESULT_RE.search(markdown)
    result_str = result_match.group(1).upper() if result_match else ""

    # 提取 "发现的关键问题" section 内的编号问题
    issues_section = _extract_named_section(
        markdown, r"发现的关键问题|key issues found|critical issues"
    )
    if issues_section:
        issue_headings = list(_SV_ISSUE_HEADING_RE.finditer(issues_section))
        for i, m in enumerate(issue_headings):
            next_start = issue_headings[i + 1].start() if i + 1 < len(issue_headings) else None
            end = next_start if next_start is not None else len(issues_section)
            body = issues_section[m.end() : end].strip()
            title = m.group(2).strip()
            severity: FindingSeverity = (
                "blocking" if result_str in ("FAIL", "INVALID") else "suggestion"
            )
            findings.append(
                BmadFinding.model_validate(
                    {
                        "severity": severity,
                        "category": "critical_issue",
                        "description": title,
                        "file_path": _extract_file_ref(body) or "N/A",
                        "line": None,
                        "rule_id": "story_validation.critical_issue",
                        "raw_location": None,
                    }
                )
            )

    # 提取 "剩余风险" section
    risks_section = _extract_named_section(markdown, r"剩余风险|remaining risks")
    if risks_section:
        for item in _extract_bullet_items(risks_section):
            findings.append(
                BmadFinding.model_validate(
                    {
                        "severity": "suggestion",
                        "category": "remaining_risk",
                        "description": item,
                        "file_path": "N/A",
                        "line": None,
                        "rule_id": "story_validation.remaining_risk",
                        "raw_location": None,
                    }
                )
            )

    return findings


# ---------------------------------------------------------------------------
# Architecture Review 解析
# ---------------------------------------------------------------------------

_ARCH_STATUS_RE = re.compile(
    r"\*\*overall\s+status[：:]*\*\*[：:]*\s*(READY\s+FOR\s+IMPLEMENTATION|NOT\s+READY(?:\s+FOR\s+IMPLEMENTATION)?|NEEDS\s+WORK)",
    re.IGNORECASE,
)

_ARCH_CONFIDENCE_RE = re.compile(
    r"confidence\s+level[：:]\s*(high|medium|low)",
    re.IGNORECASE,
)

_ARCH_VALIDATION_RE = re.compile(
    r"(architecture\s+validation|coherence\s+validation|requirements\s+coverage|"
    r"implementation\s+readiness|gap\s+analysis|architecture\s+completeness|"
    r"readiness\s+assessment)",
    re.IGNORECASE,
)


def _parse_architecture_review(markdown: str) -> list[BmadFinding] | None:
    """解析 architecture-review 输出格式。"""
    if not _ARCH_VALIDATION_RE.search(markdown):
        return None

    findings: list[BmadFinding] = []

    # Gap Analysis findings
    gap_section = _extract_named_section(markdown, r"gap\s+analysis")
    if gap_section:
        # Critical gaps → blocking (may be heading or bold section)
        critical_sub = _extract_named_section(gap_section, r"critical") or _extract_bold_section(
            gap_section, r"critical\s+gaps?"
        )
        if critical_sub:
            for item in _extract_bullet_items(critical_sub):
                if item.lower().startswith("none"):
                    continue
                findings.append(
                    BmadFinding.model_validate(
                        {
                            "severity": "blocking",
                            "category": "critical_gap",
                            "description": item,
                            "file_path": "N/A",
                            "line": None,
                            "rule_id": "architecture.critical_gap",
                            "raw_location": None,
                        }
                    )
                )
        # Important/Nice-to-Have → suggestion
        for label_re, label_bold in [
            (r"important", r"important\s+gaps?"),
            (r"nice.to.have", r"nice.to.have\s+gaps?"),
        ]:
            sub = _extract_named_section(gap_section, label_re) or _extract_bold_section(
                gap_section, label_bold
            )
            if sub:
                for item in _extract_bullet_items(sub):
                    if item.lower().startswith("none"):
                        continue
                    findings.append(
                        BmadFinding.model_validate(
                            {
                                "severity": "suggestion",
                                "category": "improvement",
                                "description": item,
                                "file_path": "N/A",
                                "line": None,
                                "rule_id": "architecture.future_enhancement",
                                "raw_location": None,
                            }
                        )
                    )

    # "Areas for Future Enhancement" → suggestion
    # Can appear as heading or bold text
    enhancement_section = _extract_named_section(
        markdown, r"areas\s+for\s+future\s+enhancement|future\s+enhancement"
    )
    if not enhancement_section:
        enhancement_section = _extract_bold_section(markdown, r"areas\s+for\s+future\s+enhancement")
    if enhancement_section:
        for item in _extract_bullet_items(enhancement_section):
            if item.lower().startswith("none"):
                continue
            findings.append(
                BmadFinding.model_validate(
                    {
                        "severity": "suggestion",
                        "category": "future_enhancement",
                        "description": item,
                        "file_path": "N/A",
                        "line": None,
                        "rule_id": "architecture.future_enhancement",
                        "raw_location": None,
                    }
                )
            )

    # Validation Issues → blocking only if NOT resolved/addressed
    # Check for "addressed" in heading context by searching around the heading
    _vi_heading_re = re.compile(
        r"^#{2,4}\s+.*validation\s+issues\s*(addressed)?.*$",
        re.MULTILINE | re.IGNORECASE,
    )
    _vi_match = _vi_heading_re.search(markdown)
    _vi_is_addressed = _vi_match is not None and _vi_match.group(1) is not None
    issues_section = _extract_named_section(markdown, r"validation\s+issues")
    # If heading says "Addressed" but body says "unresolved"/"remain", treat as NOT addressed
    _body_contradicts = issues_section is not None and (
        "unresolved" in (issues_section or "").lower()[:200]
        or "remain" in (issues_section or "").lower()[:200]
    )
    if issues_section and (not _vi_is_addressed or _body_contradicts):
        for item in _extract_bullet_items(issues_section):
            findings.append(
                BmadFinding.model_validate(
                    {
                        "severity": "blocking",
                        "category": "validation_issue",
                        "description": item,
                        "file_path": "N/A",
                        "line": None,
                        "rule_id": "architecture.validation_issue",
                        "raw_location": None,
                    }
                )
            )

    # Overall Status check — "NOT READY" and "NEEDS WORK" are both blocking
    status_match = _ARCH_STATUS_RE.search(markdown)
    status_text = status_match.group(1).upper() if status_match else ""
    _is_blocking_status = "NOT" in status_text or "NEEDS" in status_text
    if status_match and _is_blocking_status:
        findings.append(
            BmadFinding.model_validate(
                {
                    "severity": "blocking",
                    "category": "status",
                    "description": f"Architecture status: {status_match.group(1)}",
                    "file_path": "N/A",
                    "line": None,
                    "rule_id": "architecture.status",
                    "raw_location": None,
                }
            )
        )

    return findings


# ---------------------------------------------------------------------------
# QA / Test Review 解析
# ---------------------------------------------------------------------------

_QA_RECOMMENDATION_RE = re.compile(
    r"\*\*Recommendation\*\*[：:]\s*(Approve|Approve\s+with\s+Comments|"
    r"Request\s+Changes|Block)",
    re.IGNORECASE,
)

_QA_SCORE_RE = re.compile(
    r"\*\*Quality\s+Score\*\*[：:]\s*(\d+)/100",
    re.IGNORECASE,
)

_QA_ISSUE_HEADING_RE = re.compile(
    r"^#{2,4}\s+(\d+)[.、]\s*(.+)$",
    re.MULTILINE,
)

_QA_SEVERITY_RE = re.compile(
    r"\*\*Severity\*\*[：:]\s*(P0|P1|P2|P3)\s*\(([^)]+)\)",
    re.IGNORECASE,
)

_QA_LOCATION_RE = re.compile(
    r"\*\*Location\*\*[：:]\s*`([^`]+)`",
    re.IGNORECASE,
)

_QA_CRITERION_RE = re.compile(
    r"\*\*Criterion\*\*[：:]\s*(.+)",
    re.IGNORECASE,
)


def _parse_qa_report(markdown: str) -> list[BmadFinding] | None:
    """解析 QA/test-review 输出格式。"""
    has_recommendation = _QA_RECOMMENDATION_RE.search(markdown) is not None
    has_score = _QA_SCORE_RE.search(markdown) is not None
    md_lower = markdown.lower()
    has_test_review = "test quality review" in md_lower or "quality criteria" in md_lower

    if not (has_recommendation or has_score or has_test_review):
        return None

    findings: list[BmadFinding] = []

    # Parse "Critical Issues (Must Fix)" section
    critical_section = _extract_named_section(markdown, r"critical\s+issues\s*\(must\s+fix\)")
    if critical_section:
        findings.extend(_parse_qa_issue_section(critical_section, default_severity="blocking"))

    # Parse "Recommendations (Should Fix)" section
    rec_section = _extract_named_section(markdown, r"recommendations?\s*\(should\s+fix\)")
    if rec_section:
        findings.extend(_parse_qa_issue_section(rec_section, default_severity="suggestion"))

    # Parse table violations — always merge, deduplicate by criterion slug overlap
    table_findings = _parse_criteria_table(markdown)
    if table_findings:
        existing_slugs = {f.category for f in findings}
        for tf in table_findings:
            # Skip if any existing finding's category contains or is contained
            # by the table finding's category (e.g. "naming" ⊂ "naming_convention")
            is_dup = any(tf.category in slug or slug in tf.category for slug in existing_slugs)
            if not is_dup:
                findings.append(tf)

    return findings


def _parse_qa_issue_section(
    section: str,
    *,
    default_severity: FindingSeverity,
) -> list[BmadFinding]:
    """解析 QA report 的 issue section（Critical Issues / Recommendations）。"""
    findings: list[BmadFinding] = []
    issue_headings = list(_QA_ISSUE_HEADING_RE.finditer(section))

    for i, m in enumerate(issue_headings):
        end = issue_headings[i + 1].start() if i + 1 < len(issue_headings) else len(section)
        body = section[m.end() : end].strip()
        title = m.group(2).strip()

        # Extract severity from body
        severity = default_severity
        sev_match = _QA_SEVERITY_RE.search(body)
        if sev_match:
            p_level = sev_match.group(1).upper()
            severity = "blocking" if p_level in ("P0", "P1") else "suggestion"

        # Extract location
        file_path = "N/A"
        line: int | None = None
        loc_match = _QA_LOCATION_RE.search(body)
        if loc_match:
            loc_str = loc_match.group(1)
            fp, ln = _parse_file_line(loc_str)
            file_path = fp
            line = ln

        # Extract criterion for rule_id
        criterion = "general"
        crit_match = _QA_CRITERION_RE.search(body)
        if crit_match:
            criterion = _slugify(crit_match.group(1).strip())

        findings.append(
            BmadFinding.model_validate(
                {
                    "severity": severity,
                    "category": criterion,
                    "description": title,
                    "file_path": file_path,
                    "line": line,
                    "rule_id": f"qa.{criterion}",
                    "raw_location": loc_match.group(1) if loc_match else None,
                }
            )
        )
    return findings


def _parse_criteria_table(markdown: str) -> list[BmadFinding]:
    """从 Quality Criteria Assessment 表格中提取 FAIL/WARN 条目。"""
    findings: list[BmadFinding] = []
    table_section = _extract_named_section(markdown, r"quality\s+criteria\s+assessment")
    if not table_section:
        return findings

    # Parse markdown table rows
    for line in table_section.split("\n"):
        line = line.strip()
        if not line.startswith("|") or "---" in line:
            continue
        cells = [c.strip() for c in line.split("|")[1:-1]]
        if len(cells) < 4:
            continue

        criterion_name = cells[0].strip()
        status = cells[1].strip()
        notes = cells[3].strip() if len(cells) > 3 else ""

        if "FAIL" in status:
            severity: FindingSeverity = "blocking"
        elif "WARN" in status:
            severity = "suggestion"
        else:
            continue  # PASS — skip

        findings.append(
            BmadFinding.model_validate(
                {
                    "severity": severity,
                    "category": _slugify(criterion_name),
                    "description": f"{criterion_name}: {notes}" if notes else criterion_name,
                    "file_path": "N/A",
                    "line": None,
                    "rule_id": f"qa.{_slugify(criterion_name)}",
                    "raw_location": None,
                }
            )
        )
    return findings


# ---------------------------------------------------------------------------
# 通用辅助函数
# ---------------------------------------------------------------------------


def _normalize_severity(
    raw: str,
    skill_type: BmadSkillType,
) -> FindingSeverity:
    """将各种严重性表达归一化为 blocking / suggestion。"""
    lower = raw.lower().strip()
    blocking_keywords = {
        "blocking",
        "critical",
        "p0",
        "p1",
        "high",
        "fail",
        "invalid",
        "block",
        "must fix",
        "intent_gap",
        "bad_spec",
        "patch",
        "request changes",
        "not ready",
    }
    for kw in blocking_keywords:
        if kw in lower:
            return "blocking"
    return "suggestion"


def _generate_rule_id(skill_type: BmadSkillType, category: str) -> str:
    """从 skill_type 和 category 生成 rule_id。"""
    prefix = {
        BmadSkillType.CODE_REVIEW: "code_review",
        BmadSkillType.STORY_VALIDATION: "story_validation",
        BmadSkillType.ARCHITECTURE_REVIEW: "architecture",
        BmadSkillType.QA_REPORT: "qa",
    }.get(skill_type, "unknown")
    return f"{prefix}.{_slugify(category)}"


def _slugify(text: str) -> str:
    """将文本转换为 snake_case slug。"""
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower().strip())
    return slug.strip("_") or "general"


def _split_sections(markdown: str) -> list[tuple[str, str]]:
    """按 Markdown heading 分割为 (heading, body) 列表。"""
    heading_re = re.compile(r"^(#{2,4})\s+(.+)$", re.MULTILINE)
    matches = list(heading_re.finditer(markdown))
    sections: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        body = markdown[m.end() : end].strip()
        sections.append((m.group(2).strip(), body))
    return sections


_CODE_FENCE_RE = re.compile(r"^```.*?^```", re.MULTILINE | re.DOTALL)


def _strip_code_fences(text: str) -> str:
    """移除 Markdown 代码围栏块，防止代码内 # 注释被误识别为 heading。"""
    return _CODE_FENCE_RE.sub("", text)


def _extract_named_section(markdown: str, pattern: str) -> str | None:
    """从 Markdown 中提取指定 heading 下的内容。"""
    heading_re = re.compile(
        r"^(#{2,4})\s+.*?(?:" + pattern + r").*$",
        re.MULTILINE | re.IGNORECASE,
    )
    m = heading_re.search(markdown)
    if not m:
        return None

    level = len(m.group(1))
    start = m.end()
    # 在去除代码围栏后的文本中寻找同级或更高级 heading
    remaining = markdown[start:]
    stripped = _strip_code_fences(remaining)
    end_re = re.compile(r"^#{1," + str(level) + r"}\s+", re.MULTILINE)
    end_match = end_re.search(stripped)
    if end_match:
        # 在原始文本中找到对应位置（stripped 可能移除了代码块内容）
        snippet = stripped[end_match.start() : end_match.start() + 100]
        orig_pos = remaining.find(snippet)
        end = start + orig_pos if orig_pos >= 0 else len(markdown)
    else:
        end = len(markdown)
    body = markdown[start:end].strip()
    return body if body else None


def _extract_items_from_section(
    section: str,
) -> list[tuple[str, str, str | None, int | None]]:
    """从 section 中提取 (title, detail, file_path, line) 项。"""
    items: list[tuple[str, str, str | None, int | None]] = []

    # 尝试按编号子标题分割
    numbered_re = re.compile(r"^(?:#{3,5})\s+(\d+)[.、]\s*(.+)$", re.MULTILINE)
    numbered = list(numbered_re.finditer(section))
    if numbered:
        for i, m in enumerate(numbered):
            end = numbered[i + 1].start() if i + 1 < len(numbered) else len(section)
            body = section[m.end() : end].strip()
            title = m.group(2).strip()
            # 先从 title 提取 location，再从 body 提取
            fp, ln = _extract_location_from_body(title)
            if fp is None:
                fp, ln = _extract_location_from_body(body)
            items.append((title, body[:200], fp, ln))
        return items

    # 尝试按 bullet 分割
    bullets = _extract_bullet_items(section)
    for bullet in bullets:
        # 尝试提取 title: detail 格式
        colon_idx = bullet.find(":")
        if colon_idx > 0 and colon_idx < 80:
            title = bullet[:colon_idx].strip()
            detail = bullet[colon_idx + 1 :].strip()
        else:
            title = bullet
            detail = ""

        fp, ln = _extract_location_from_body(bullet)
        items.append((title, detail, fp, ln))
    return items


def _extract_bold_list_section(markdown: str, pattern: str) -> str | None:
    """从 `- **Bold Label**: ...` 列表项格式中提取子 bullet 内容。

    真实 BMAD code-review 模板使用这种格式：
    ``- **Intent Gaps**: "These findings suggest..."``
    ``  - Finding title + detail``
    ``  - Another finding``
    """
    # 匹配 `- **Label**: ...` 或 `- **Label**:` 形式
    bold_list_re = re.compile(
        r"^\s*-\s+\*\*.*?(?:" + pattern + r").*?\*\*:?.*$",
        re.MULTILINE | re.IGNORECASE,
    )
    m = bold_list_re.search(markdown)
    if not m:
        return None
    start = m.end()
    remaining = markdown[start:]
    lines: list[str] = []
    for line in remaining.split("\n"):
        stripped = line.strip()
        # 空行：跳过，不截断（LLM 常在 bullet 间插入空行）
        if not stripped:
            continue
        # 同级 bullet 以 `- **` 开头 → 下一个 category，停止
        if re.match(r"^\s*-\s+\*\*", line):
            break
        # 其他 section 标记 → 停止
        if stripped.startswith("#") or (
            stripped.startswith("**") and not stripped.startswith("**Severity")
        ):
            break
        # 缩进内容或 bullet → 保留为列表项（保留 `- ` 前缀供后续解析）
        lines.append(f"- {stripped}" if not stripped.startswith("- ") else stripped)
    body = "\n".join(lines).strip()
    return body if body else None


def _extract_bold_section(markdown: str, pattern: str) -> str | None:
    """从 Markdown 中提取 **Bold Label:** 后面的内容。"""
    bold_re = re.compile(
        r"^\*\*.*?(?:" + pattern + r").*?\*\*:?\s*$",
        re.MULTILINE | re.IGNORECASE,
    )
    m = bold_re.search(markdown)
    if not m:
        return None
    start = m.end()
    remaining = markdown[start:]
    lines: list[str] = []
    for line in remaining.split("\n"):
        stripped = line.strip()
        # 空行：跳过（LLM 常在 bullet 间插入空行）
        if not stripped:
            continue
        if stripped.startswith("**") or re.match(r"^#{1,4}\s+", stripped):
            break
        lines.append(line)
    body = "\n".join(lines).strip()
    return body if body else None


def _extract_bullet_items(text: str) -> list[str]:
    """提取 bullet list 条目。"""
    items: list[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("- ") or stripped.startswith("* "):
            items.append(stripped[2:].strip())
    return items


def _extract_file_ref(text: str) -> str | None:
    """从文本中提取文件路径引用。"""
    # 匹配反引号中的路径
    backtick = re.search(r"`([^`]+\.\w+(?::\d+)?)`", text)
    if backtick:
        path = backtick.group(1)
        if "/" in path or path.endswith((".py", ".ts", ".js", ".md", ".yaml", ".json")):
            return path.split(":")[0]
    return None


def _extract_location_from_body(body: str) -> tuple[str | None, int | None]:
    """从 body 文本中提取 file_path 和 line。"""
    # 匹配 `path:line` 或 **Location**: `path:line`
    loc_re = re.compile(r"`([^`]+?):(\d+)`")
    m = loc_re.search(body)
    if m:
        return m.group(1), int(m.group(2))
    # 匹配单独的文件路径
    fp = _extract_file_ref(body)
    return fp, None


def _parse_file_line(loc_str: str) -> tuple[str, int | None]:
    """解析 'path:line' 格式。"""
    if ":" in loc_str:
        parts = loc_str.rsplit(":", 1)
        if parts[1].isdigit():
            return parts[0], int(parts[1])
    return loc_str, None
