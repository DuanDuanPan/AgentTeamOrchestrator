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
    task_id: str | None = None,
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
        task_id: 关联的 task ID（可选）。提供时写入 payload 以便 retry 能定位目标 task。
        notifier: 可选的通知回调。

    Returns:
        创建的 ApprovalRecord。
    """
    from ato.approval_helpers import create_approval

    payload: dict[str, Any] = {
        "reason": "bmad_parse_failed",
        "skill_type": skill_type.value,
        "parser_mode": parse_result.parser_mode,
        "error": parse_result.parse_error,
        "raw_output_preview": parse_result.raw_output_preview,
        "options": ["retry", "skip", "escalate"],
    }
    if task_id is not None:
        payload["task_id"] = task_id

    approval = await create_approval(
        db,
        story_id=story_id,
        approval_type="needs_human_review",
        payload_dict=payload,
    )

    logger.warning(
        "bmad_parse_failure_recorded",
        story_id=story_id,
        skill_type=skill_type.value,
        parser_mode=parse_result.parser_mode,
        error=parse_result.parse_error,
        raw_output_preview=parse_result.raw_output_preview[:_PREVIEW_MAX_CHARS],
    )

    # 兼容原有 notifier callback（进程内 nudge 或 mock）
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
            incomplete_reason = _detect_incomplete_review_output(
                markdown_output,
                skill_type=skill_type,
                findings=findings,
            )
            if incomplete_reason is not None:
                logger.warning(
                    "bmad_parse_incomplete_output",
                    story_id=story_id,
                    skill_type=skill_type.value,
                    parser_mode="deterministic",
                    reason=incomplete_reason,
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
                        "parse_error": incomplete_reason,
                        "parsed_at": now,
                    }
                )
            verdict = _compute_effective_verdict(markdown_output, skill_type, findings)
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

        # Stage 1.5: explicit verdict fast-path (Story 10.4 AC1)
        # Deterministic parser 无法识别结构时，检查 explicit verdict 是否明确通过。
        # 避免不必要的 semantic fallback 超时。
        if _is_clearly_passing_output(markdown_output):
            logger.info(
                "bmad_parse_explicit_pass_fast_path",
                story_id=story_id,
                skill_type=skill_type.value,
            )
            return BmadParseResult.model_validate(
                {
                    "skill_type": skill_type,
                    "verdict": "approved",
                    "findings": [],
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
                incomplete_reason = _detect_incomplete_review_output(
                    markdown_output,
                    skill_type=skill_type,
                    findings=findings_objs,
                )
                if incomplete_reason is not None:
                    logger.warning(
                        "bmad_parse_incomplete_output",
                        story_id=story_id,
                        skill_type=skill_type.value,
                        parser_mode="semantic_fallback",
                        reason=incomplete_reason,
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
                            "parse_error": incomplete_reason,
                            "parsed_at": now,
                        }
                    )
                verdict = _compute_effective_verdict(markdown_output, skill_type, findings_objs)
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
                # Story 10.4 AC3: 区分 deterministic miss 和 semantic timeout
                is_timeout = "timed out" in str(exc).lower()
                logger.warning(
                    "bmad_semantic_fallback_failed",
                    story_id=story_id,
                    skill_type=skill_type.value,
                    error=str(exc),
                    input_length=len(markdown_output),
                    timeout_related=is_timeout,
                    parser_mode="semantic_fallback",
                )

        # Stage 3: 全部失败
        # AC3: parse_error 包含诊断信息
        error_msg = (
            f"Both deterministic and semantic parsing failed"
            f" (skill_type={skill_type.value},"
            f" input_length={len(markdown_output)},"
            f" semantic_runner={'available' if self._semantic_runner else 'none'})"
        )
        logger.warning(
            "bmad_parse_failed",
            story_id=story_id,
            skill_type=skill_type.value,
            parser_mode="failed",
            error=error_msg,
            input_length=len(markdown_output),
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

# Story 10.4 AC1: 明确通过输出的 fast-path 检测
_CLEARLY_PASSING_RE = re.compile(
    r"(?:"
    r"(?:verdict|status)\s*:\s*pass"
    r"|recommendation\s*:\s*approve"
    r"|no\s+blocking\s+findings?"
    r"|0\s+blocking"
    r"|0\s+patch"
    r")",
    re.IGNORECASE,
)

# 否定语境：确保不把 "not approved" / "no pass" 误判为通过
_NEGATION_PASS_RE = re.compile(
    r"(?:not?\s+(?:pass|approved?)|(?:did|does)\s+not\s+pass|fail)",
    re.IGNORECASE,
)


def _is_clearly_passing_output(markdown: str) -> bool:
    """检测输出是否明确为 PASS/Approve，且无否定语境。"""
    if not _CLEARLY_PASSING_RE.search(markdown):
        return False
    return not _NEGATION_PASS_RE.search(markdown)


def _compute_verdict(findings: list[BmadFinding]) -> ParseVerdict:
    """根据 findings 计算 verdict。"""
    if not findings:
        return "approved"
    if any(f.severity == "blocking" for f in findings):
        return "changes_requested"
    return "approved"


def _compute_effective_verdict(
    markdown_output: str,
    skill_type: BmadSkillType,
    findings: list[BmadFinding],
) -> ParseVerdict:
    """计算最终 verdict，必要时保留原始文本中的显式判定。"""
    computed = _compute_verdict(findings)
    explicit = _extract_explicit_verdict(markdown_output, skill_type)
    if explicit == "changes_requested":
        return "changes_requested"
    if explicit == "approved":
        return "changes_requested" if computed == "changes_requested" else "approved"
    return computed


_INCOMPLETE_CODE_REVIEW_RE = re.compile(
    r"("  # explicit request for follow-up/confirmation
    r"请确认|确认继续|继续执行\s*step|continue\s+executing\s+step|"
    r"confirm\s+to\s+continue|which\s+mode\s+should|"
    r"还是改为|是否包含未提交|include\s+uncommitted|"
    r"before\s+continuing|awaiting\s+confirmation"
    r")",
    re.IGNORECASE,
)

_CHECKPOINT_RE = re.compile(r"\bcheckpoint\b|检查点", re.IGNORECASE)


def _detect_incomplete_review_output(
    markdown_output: str,
    *,
    skill_type: BmadSkillType,
    findings: list[BmadFinding],
) -> str | None:
    """Return a parse-failure reason when output is clearly not a final review result."""
    if skill_type != BmadSkillType.CODE_REVIEW or findings:
        return None
    if _extract_explicit_verdict(markdown_output, skill_type) is not None:
        return None

    has_checkpoint = _CHECKPOINT_RE.search(markdown_output) is not None
    asks_for_confirmation = _INCOMPLETE_CODE_REVIEW_RE.search(markdown_output) is not None
    asks_question = "?" in markdown_output or "？" in markdown_output

    if asks_for_confirmation and (has_checkpoint or asks_question):
        return "Code review output is incomplete and asks for confirmation"
    return None


def _extract_explicit_verdict(
    markdown_output: str,
    skill_type: BmadSkillType,
) -> ParseVerdict | None:
    """从原始文本中提取 agent 的显式判定，用于 findings 缺失时兜底。"""
    if skill_type == BmadSkillType.STORY_VALIDATION:
        result_match = _SV_RESULT_RE.search(markdown_output)
        if result_match is not None:
            explicit_result = result_match.group(1).upper()
            if explicit_result in ("FAIL", "INVALID"):
                return "changes_requested"
            if explicit_result == "PASS":
                return "approved"
        return None

    if skill_type == BmadSkillType.QA_REPORT:
        recommendation_match = _QA_RECOMMENDATION_RE.search(markdown_output)
        if recommendation_match is not None:
            recommendation = recommendation_match.group(1).lower()
            if "request changes" in recommendation or "block" in recommendation:
                return "changes_requested"
            if "approve" in recommendation:
                return "approved"
        return None

    if skill_type == BmadSkillType.CODE_REVIEW:
        summary_match = _SUMMARY_RE.search(markdown_output)
        if summary_match is not None:
            blocking_total = sum(int(summary_match.group(idx)) for idx in (1, 2, 3))
            if blocking_total > 0:
                return "changes_requested"
            if int(summary_match.group(4)) >= 0:
                return "approved"

        for pattern in ("intent\\s+gaps?", "bad\\s+spec", "patch"):
            section_body = _extract_named_section(markdown_output, pattern)
            if not section_body:
                section_body = _extract_bold_list_section(markdown_output, pattern)
            if not section_body:
                section_body = _extract_bold_section(markdown_output, pattern)
            if section_body and _section_has_findings(section_body):
                return "changes_requested"
        return None

    return None


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
# 兼容反引号包裹数字的格式（如 `5` patch）
_SUMMARY_RE = re.compile(
    r"`?(\d+)`?\s+intent.gap.*?`?(\d+)`?\s+bad.spec.*?`?(\d+)`?\s+patch.*?`?(\d+)`?\s+defer",
    re.IGNORECASE,
)

# Clean review patterns
_CLEAN_REVIEW_RE = re.compile(
    r"(clean review|no findings|zero findings|all.*classified as noise|"
    r"no.*issues.*found|no.*findings.*raised)",
    re.IGNORECASE,
)

_BULLET_RE = re.compile(r"^\s*[-*]\s+", re.MULTILINE)

# Flat findings list: **Findings** heading + numbered items with `P0`/`P1`/`P2` priority
_FINDINGS_HEADING_RE = re.compile(
    r"^\*\*Findings\*\*\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# Re-review format: **Open Findings** heading + bullet list with `severity` prefix
_OPEN_FINDINGS_HEADING_RE = re.compile(
    r"^\*\*Open\s+Findings\*\*\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# Severity marker in re-review bullet: `blocking`, `medium`, `suggestion`
_REREVIEW_SEVERITY_RE = re.compile(r"^-\s+`(\w+)`[：:]", re.MULTILINE)

# Priority marker: `P0`, `P1`, `P2` etc.
_PRIORITY_RE = re.compile(r"`P(\d+)`")

# Priority → severity mapping
_PRIORITY_SEVERITY: dict[int, FindingSeverity] = {
    0: "blocking",
    1: "blocking",
    2: "suggestion",
    3: "suggestion",
}


def _parse_code_review(markdown: str) -> list[BmadFinding] | None:
    """解析 code-review 输出格式。"""
    # 检查是否有 code-review 特征标记
    has_summary = _SUMMARY_RE.search(markdown) is not None
    has_sections = _SECTION_RE.search(markdown) is not None
    has_clean = _CLEAN_REVIEW_RE.search(markdown) is not None
    has_findings_heading = _FINDINGS_HEADING_RE.search(markdown) is not None
    has_open_findings = _OPEN_FINDINGS_HEADING_RE.search(markdown) is not None

    if not (has_summary or has_sections or has_clean or has_findings_heading or has_open_findings):
        return None

    if has_clean and not has_sections and not has_findings_heading and not has_open_findings:
        return []

    findings: list[BmadFinding] = []

    # Path A: flat **Findings** list with `P0`/`P1`/`P2` priority markers
    if has_findings_heading and not has_sections:
        flat = _parse_flat_findings_list(markdown)
        if flat is not None:
            return flat

    # Path C: re-review **Open Findings** + `severity` bullet list
    if has_open_findings:
        rereview = _parse_open_findings_list(markdown)
        if rereview is not None:
            return rereview

    # Path B: category-section 格式（原有逻辑）
    # 按 category 提取 section：支持 heading 形式 和 bold-label 列表形式
    # 真实模板用 `- **Intent Gaps**: "..."` 后跟子 bullet
    for category, severity, pattern in _CODE_REVIEW_SECTIONS:
        # 优先 heading 形式（## Intent Gaps）
        section_body = _extract_named_section(markdown, pattern)
        # 其次 bold-label 列表形式（- **Intent Gaps**: ...）
        if not section_body:
            section_body = _extract_bold_list_section(markdown, pattern)
        # 最后支持 standalone bold section（**Patch** 换行后接编号项）
        if not section_body:
            section_body = _extract_bold_section(markdown, pattern)
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

    # If we detected a **Findings** heading but extracted nothing,
    # the output format is unrecognized — return None to trigger
    # semantic fallback rather than falsely reporting a clean review.
    if not findings and (has_findings_heading or has_open_findings):
        return None

    return findings


def _parse_flat_findings_list(markdown: str) -> list[BmadFinding] | None:
    """解析 **Findings** + 编号列表 + `P0`/`P1`/`P2` 格式。

    Codex 的 code-review 输出有时不按 category section 分组，
    而是用一个统一的编号列表，每项以 `P0`/`P1`/`P2` 标注优先级。
    """
    m = _FINDINGS_HEADING_RE.search(markdown)
    if m is None:
        return None

    # 提取 **Findings** 标题之后的内容（到 summary 行、下一个 heading 或文档尾）
    after_heading = markdown[m.end() :]
    # 截止到 summary 行（总结：/ X intent_gap）或下一个 bold heading 或 ## heading
    summary_cut = _SUMMARY_RE.search(after_heading)
    next_section = re.search(r"^\*\*[A-Z]", after_heading, re.MULTILINE)
    cut_pos = len(after_heading)
    if summary_cut:
        # 回退到 summary 所在行的行首
        line_start = after_heading.rfind("\n", 0, summary_cut.start())
        cut_pos = min(cut_pos, line_start if line_start >= 0 else summary_cut.start())
    if next_section:
        cut_pos = min(cut_pos, next_section.start())
    after_heading = after_heading[:cut_pos]

    # 提取编号项
    blocks = _extract_numbered_blocks(after_heading)
    if not blocks:
        return None

    findings: list[BmadFinding] = []
    for title, body in blocks:
        full_text = f"{title}\n{body}" if body else title

        # 提取 priority
        pm = _PRIORITY_RE.search(title)
        if pm:
            priority = int(pm.group(1))
            severity = _PRIORITY_SEVERITY.get(priority, "suggestion")
            # 从 title 中移除 priority marker 得到干净描述
            clean_title = _PRIORITY_RE.sub("", title).strip()
        else:
            severity = "blocking"
            clean_title = title

        desc = f"{clean_title}: {body[:200]}" if body else clean_title

        # 提取 file location
        fp, ln = _extract_location_from_body(full_text)

        findings.append(
            BmadFinding.model_validate(
                {
                    "severity": severity,
                    "category": "patch",
                    "description": desc,
                    "file_path": fp or "N/A",
                    "line": ln,
                    "rule_id": "code_review.patch",
                    "raw_location": (f"{fp}:{ln}" if fp and ln else None),
                }
            )
        )
    return findings


# ---------------------------------------------------------------------------
# Story Validation 解析
# ---------------------------------------------------------------------------

_SV_RESULT_RE = re.compile(
    r"(?:结果|Result)[：:]\s*(PASS|FAIL|INVALID)",
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


def _parse_open_findings_list(markdown: str) -> list[BmadFinding] | None:
    """解析 re-review 格式：**Open Findings** + `severity` bullet list。

    Codex re-review 输出格式：
        **Open Findings**
        - `blocking`：[`file.ts:64`](...) description
        - `medium`：...
        - `suggestion`：...
    """
    m = _OPEN_FINDINGS_HEADING_RE.search(markdown)
    if m is None:
        return None

    after_heading = markdown[m.end() :]
    # 截止到下一个 bold heading
    next_section = re.search(r"^\*\*[A-Z]", after_heading, re.MULTILINE)
    if next_section:
        after_heading = after_heading[: next_section.start()]

    # 按 bullet 提取
    severity_map: dict[str, FindingSeverity] = {
        "blocking": "blocking",
        "critical": "blocking",
        "p0": "blocking",
        "p1": "blocking",
        "medium": "blocking",
        "suggestion": "suggestion",
        "minor": "suggestion",
        "p2": "suggestion",
        "p3": "suggestion",
    }

    findings: list[BmadFinding] = []
    for bm in _REREVIEW_SEVERITY_RE.finditer(after_heading):
        raw_severity = bm.group(1).lower()
        severity = severity_map.get(raw_severity, "blocking")

        # 提取该 bullet 的完整内容（到下一个 bullet 或段落结束）
        start = bm.end()
        next_bullet = _REREVIEW_SEVERITY_RE.search(after_heading[start:])
        end = start + next_bullet.start() if next_bullet else len(after_heading)
        body = after_heading[start:end].strip()

        fp, ln = _extract_location_from_body(body)

        findings.append(
            BmadFinding.model_validate(
                {
                    "severity": severity,
                    "category": "patch",
                    "description": body[:500],
                    "file_path": fp or "N/A",
                    "line": ln,
                    "rule_id": "code_review.patch",
                    "raw_location": (f"{fp}:{ln}" if fp and ln else None),
                }
            )
        )
    return findings if findings else None


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
    r"(?:\*\*)?Recommendation(?:\*\*)?\s*[：:]\s*(Approve|Approve\s+with\s+Comments|"
    r"Request\s+Changes|Block)",
    re.IGNORECASE,
)

_QA_SCORE_RE = re.compile(
    r"(?:\*\*)?Quality\s+Score(?:\*\*)?\s*[：:]\s*(\d+)/100",
    re.IGNORECASE,
)

_QA_ISSUE_HEADING_RE = re.compile(
    r"^#{2,4}\s+(\d+)[.、]\s*(.+)$",
    re.MULTILINE,
)

_QA_SEVERITY_RE = re.compile(
    r"(?:\*\*)?Severity(?:\*\*)?\s*[：:]\s*(P0|P1|P2|P3)(?:\s*\(([^)]+)\))?",
    re.IGNORECASE,
)

_QA_LOCATION_RE = re.compile(
    r"(?:\*\*)?Location(?:\*\*)?\s*[：:]\s*`?([^`\n]+)`?",
    re.IGNORECASE,
)

_QA_CRITERION_RE = re.compile(
    r"(?:\*\*)?Criterion(?:\*\*)?\s*[：:]\s*([^\n]+)",
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
    critical_section = _extract_named_section(markdown, r"critical\s+issues(?:\s*\(must\s+fix\))?")
    if not critical_section:
        critical_section = _extract_bold_section(
            markdown, r"critical\s+issues(?:\s*\(must\s+fix\))?"
        )
    if critical_section:
        findings.extend(_parse_qa_issue_section(critical_section, default_severity="blocking"))

    # Parse "Recommendations (Should Fix)" section
    rec_section = _extract_named_section(markdown, r"recommendations?(?:\s*\(should\s+fix\))?")
    if not rec_section:
        rec_section = _extract_bold_section(markdown, r"recommendations?(?:\s*\(should\s+fix\))?")
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

    # If we detected issue sections but extracted nothing, the output format
    # is unrecognized — return None to trigger semantic fallback.
    has_sections = critical_section is not None or rec_section is not None
    if not findings and has_sections:
        return None

    return findings


def _parse_qa_issue_section(
    section: str,
    *,
    default_severity: FindingSeverity,
) -> list[BmadFinding]:
    """解析 QA report 的 issue section（Critical Issues / Recommendations）。"""
    findings: list[BmadFinding] = []
    numbered_blocks = _extract_numbered_blocks(section)

    for title, body in numbered_blocks:
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

    numbered = _extract_numbered_blocks(section)
    if numbered:
        for title, body in numbered:
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


def _extract_numbered_blocks(section: str) -> list[tuple[str, str]]:
    """提取 `1. title` / `### 1. title` 形式的块。"""
    numbered_re = re.compile(r"^(?:\s*#{3,5}\s+)?(\d+)[.)、]\s*(.+)$", re.MULTILINE)
    matches = list(numbered_re.finditer(section))
    blocks: list[tuple[str, str]] = []
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(section)
        title = match.group(2).strip()
        body = section[match.end() : end].strip()
        blocks.append((title, body))
    return blocks


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


def _section_has_findings(section: str) -> bool:
    """判断 section 是否包含非空 finding 内容。"""
    if _extract_numbered_blocks(section):
        return True
    if _extract_bullet_items(section):
        return True
    stripped = [
        line.strip()
        for line in section.splitlines()
        if line.strip() and not line.strip().startswith(("**Summary", "Summary"))
    ]
    if not stripped:
        return False
    lowered = " ".join(stripped).lower()
    return not lowered.startswith(("none", "no findings", "no issues"))


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
    # 匹配 Markdown link 格式 [file.tsx:49]( url )
    link_re = re.compile(r"\[([^\]]+?):(\d+)\]\(")
    m2 = link_re.search(body)
    if m2:
        return m2.group(1), int(m2.group(2))
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
