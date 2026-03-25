"""schemas — Pydantic 数据模型定义。

所有 Pydantic record models、异常类层次、跨模块常量统一定义于此。
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, model_validator

# ---------------------------------------------------------------------------
# 跨模块常量
# ---------------------------------------------------------------------------

SCHEMA_VERSION: int = 4
"""当前数据库 schema 版本号，与 PRAGMA user_version 对应。"""

# ---------------------------------------------------------------------------
# 错误分类枚举
# ---------------------------------------------------------------------------


class ErrorCategory(StrEnum):
    """CLI 适配器错误分类。"""

    AUTH_EXPIRED = "auth_expired"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    PARSE_ERROR = "parse_error"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# 异常类层次
# ---------------------------------------------------------------------------


class ATOError(Exception):
    """ATO 所有自定义异常的基类。"""


class CLIAdapterError(ATOError):
    """CLI 调用失败，携带分类、stderr 和重试标记。"""

    def __init__(
        self,
        message: str,
        *,
        category: ErrorCategory = ErrorCategory.UNKNOWN,
        stderr: str = "",
        exit_code: int | None = None,
        retryable: bool = False,
    ) -> None:
        self.category = category
        self.stderr = stderr
        self.exit_code = exit_code
        self.retryable = retryable
        super().__init__(message)


class StateTransitionError(ATOError):
    """状态机转换非法。"""


class RecoveryError(ATOError):
    """崩溃恢复 / 迁移失败。"""


class ConfigError(ATOError):
    """配置解析错误。"""


# ---------------------------------------------------------------------------
# 共享 Pydantic 配置
# ---------------------------------------------------------------------------


class _StrictBase(BaseModel):
    """所有 record model 共享的严格校验基类。

    strict=True: 禁止隐式类型转换（如 ``"1"`` → ``1``）。
    extra="forbid": 拒绝未声明字段。
    """

    model_config = ConfigDict(strict=True, extra="forbid")


# ---------------------------------------------------------------------------
# Preflight 检查类型
# ---------------------------------------------------------------------------

CheckStatus = Literal["PASS", "HALT", "WARN", "INFO"]
"""Preflight 检查结果状态：PASS 通过 / HALT 阻塞 / WARN 警告 / INFO 信息。"""

CheckLayer = Literal["system", "project", "artifact"]
"""Preflight 检查层级：system 系统环境 / project 项目结构 / artifact 编排前置。"""


class CheckResult(_StrictBase):
    """单项 preflight 检查结果。"""

    layer: CheckLayer
    check_item: str
    status: CheckStatus
    message: str


# ---------------------------------------------------------------------------
# Record Models
# ---------------------------------------------------------------------------

# Story 生命周期状态
StoryStatus = Literal[
    "backlog",
    "planning",
    "ready",
    "in_progress",
    "review",
    "uat",
    "done",
    "blocked",
]


class StoryRecord(_StrictBase):
    """stories 表对应的 Pydantic 模型。"""

    story_id: str
    title: str
    status: StoryStatus
    current_phase: str
    worktree_path: str | None = None
    created_at: datetime
    updated_at: datetime


# Task 状态
TaskStatus = Literal["pending", "running", "paused", "completed", "failed"]


class TaskRecord(_StrictBase):
    """tasks 表对应的 Pydantic 模型。"""

    task_id: str
    story_id: str
    phase: str
    role: str
    cli_tool: Literal["claude", "codex"]
    status: TaskStatus
    pid: int | None = None
    expected_artifact: str | None = None
    context_briefing: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    exit_code: int | None = None
    cost_usd: float | None = None
    duration_ms: int | None = None
    error_message: str | None = None


# Approval 状态
ApprovalStatus = Literal["pending", "approved", "rejected"]


class ApprovalRecord(_StrictBase):
    """approvals 表对应的 Pydantic 模型。"""

    approval_id: str
    story_id: str
    approval_type: str
    status: ApprovalStatus
    payload: str | None = None
    decision: str | None = None
    decided_at: datetime | None = None
    created_at: datetime


# ---------------------------------------------------------------------------
# Transition 事件模型 (Story 2A.2)
# ---------------------------------------------------------------------------

# Transition 事件来源
TransitionSource = Literal["agent", "tui", "cli"]


class TransitionEvent(_StrictBase):
    """TransitionQueue 接收的状态转换事件。"""

    story_id: str
    event_name: str
    source: TransitionSource
    submitted_at: datetime


# ---------------------------------------------------------------------------
# Batch 相关模型 (Story 2B.5)
# ---------------------------------------------------------------------------

# Batch 生命周期状态
BatchStatus = Literal["active", "completed", "cancelled"]


class BatchRecord(_StrictBase):
    """batches 表对应的 Pydantic 模型。"""

    batch_id: str
    status: BatchStatus
    created_at: datetime
    completed_at: datetime | None = None


class BatchStoryLink(_StrictBase):
    """batch_stories 关联表对应的 Pydantic 模型。"""

    batch_id: str
    story_id: str
    sequence_no: int


# ---------------------------------------------------------------------------
# Adapter 输出模型 (Story 2B.1)
# ---------------------------------------------------------------------------


class AdapterResult(BaseModel):
    """CLI 适配器统一输出模型。

    不继承 _StrictBase——外部 CLI JSON 输出需要宽松解析（int/float 自动转换等）。
    """

    model_config = ConfigDict(extra="ignore")

    status: Literal["success", "failure", "timeout"]
    exit_code: int
    duration_ms: int = 0
    text_result: str = ""
    structured_output: dict[str, Any] | None = None
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    session_id: str | None = None
    error_category: str | None = None
    error_message: str | None = None


class ClaudeOutput(AdapterResult):
    """Claude CLI 专用输出模型。"""

    cache_read_input_tokens: int = 0
    model_usage: dict[str, Any] | None = None

    @classmethod
    def from_json(cls, json_data: dict[str, Any], *, exit_code: int = 0) -> ClaudeOutput:
        """从 Claude CLI stdout JSON 解析为验证后的模型。

        字段映射遵循 ADR-09：
        - ``result`` → ``text_result``
        - ``structured_output`` → ``structured_output``（独立字段，非嵌套在 result 中）
        - ``total_cost_usd`` → ``cost_usd``
        """
        usage = json_data.get("usage") or {}
        return cls.model_validate(
            {
                "status": "success" if exit_code == 0 else "failure",
                "exit_code": exit_code,
                "duration_ms": json_data.get("duration_ms", 0),
                "text_result": json_data.get("result", ""),
                "structured_output": json_data.get("structured_output"),
                "cost_usd": json_data.get("total_cost_usd", 0.0),
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
                "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
                "session_id": json_data.get("session_id"),
                "model_usage": json_data.get("modelUsage"),
            }
        )


class CodexOutput(AdapterResult):
    """Codex CLI 专用输出模型。"""

    cache_read_input_tokens: int = 0
    model_name: str | None = None

    @classmethod
    def from_events(
        cls,
        events: list[dict[str, Any]],
        *,
        exit_code: int = 0,
        output_file_content: str | None = None,
        model_name: str | None = None,
        cost_usd: float = 0.0,
    ) -> CodexOutput:
        """从解析后的 JSONL 事件列表构建验证后的模型。

        字段映射：
        - ``item.completed`` → ``text_result``（兼容 item.text 与 item.content[].text）
        - ``turn.completed.usage`` → token 聚合
        - ``output_file_content`` → ``structured_output``（JSON 解析成功时）
        - ``thread.started.thread_id`` → ``session_id``
        """
        from ato.adapters.codex_cli import _aggregate_usage, _extract_text_result

        input_tokens, cached_input_tokens, output_tokens = _aggregate_usage(events)
        text_result = _extract_text_result(events)

        # session_id from thread.started
        session_id: str | None = None
        for ev in events:
            if ev.get("type") == "thread.started":
                session_id = ev.get("thread_id")
                break

        # Parse output file content
        structured_output: dict[str, Any] | None = None
        if output_file_content is not None:
            from ato.adapters.codex_cli import _parse_output_file

            structured_output, parsed_text = _parse_output_file(output_file_content)
            if not text_result:
                text_result = parsed_text

        return cls.model_validate(
            {
                "status": "success" if exit_code == 0 else "failure",
                "exit_code": exit_code,
                "text_result": text_result,
                "structured_output": structured_output,
                "cost_usd": cost_usd,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_input_tokens": cached_input_tokens,
                "session_id": session_id,
                "model_name": model_name,
            }
        )


class CostLogRecord(_StrictBase):
    """cost_log 表对应的 Pydantic 模型。"""

    cost_log_id: str
    story_id: str
    task_id: str | None = None
    cli_tool: Literal["claude", "codex"]
    model: str | None = None
    phase: str
    role: str | None = None
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int = 0
    cost_usd: float
    duration_ms: int | None = None
    session_id: str | None = None
    exit_code: int | None = None
    error_category: str | None = None
    created_at: datetime


# ---------------------------------------------------------------------------
# BMAD 解析模型 (Story 2B.3)
# ---------------------------------------------------------------------------

# Finding 严重性
FindingSeverity = Literal["blocking", "suggestion"]

# 解析器模式
ParserMode = Literal["deterministic", "semantic_fallback", "failed"]

# 解析结果 verdict
ParseVerdict = Literal["approved", "changes_requested", "parse_failed"]


class BmadSkillType(StrEnum):
    """BMAD skill 类型枚举，支持 workflow 名称/别名归一化。"""

    CODE_REVIEW = "code_review"
    STORY_VALIDATION = "story_validation"
    ARCHITECTURE_REVIEW = "architecture_review"
    QA_REPORT = "qa_report"

    @classmethod
    def from_alias(cls, name: str) -> BmadSkillType:
        """从 workflow 名称/别名归一化为枚举值。

        支持 kebab-case workflow 名称、精确枚举值等多种输入形态。

        Raises:
            ValueError: 未知的 skill 名称。
        """
        # 精确匹配枚举值
        try:
            return cls(name)
        except ValueError:
            pass

        normalized = name.lower().replace("_", "-")
        alias_map: dict[str, BmadSkillType] = {
            # code_review — bmm module
            "code-review": cls.CODE_REVIEW,
            "bmad-code-review": cls.CODE_REVIEW,
            "bmad-bmm-code-review": cls.CODE_REVIEW,
            "skill:bmad-code-review": cls.CODE_REVIEW,
            # story_validation — bmm module
            "story-validation": cls.STORY_VALIDATION,
            "validate-create-story": cls.STORY_VALIDATION,
            "bmad-validate-story": cls.STORY_VALIDATION,
            "bmad-create-story": cls.STORY_VALIDATION,
            "bmad-bmm-create-story": cls.STORY_VALIDATION,
            "skill:bmad-create-story": cls.STORY_VALIDATION,
            "validate-story": cls.STORY_VALIDATION,
            # architecture_review — bmm module
            "architecture": cls.ARCHITECTURE_REVIEW,
            "architecture-review": cls.ARCHITECTURE_REVIEW,
            "create-architecture": cls.ARCHITECTURE_REVIEW,
            "bmad-create-architecture": cls.ARCHITECTURE_REVIEW,
            "bmad-bmm-create-architecture": cls.ARCHITECTURE_REVIEW,
            "skill:bmad-create-architecture": cls.ARCHITECTURE_REVIEW,
            # qa_report — tea module
            "qa-report": cls.QA_REPORT,
            "test-review": cls.QA_REPORT,
            "testarch-test-review": cls.QA_REPORT,
            "bmad-testarch-test-review": cls.QA_REPORT,
            "bmad-tea-testarch-test-review": cls.QA_REPORT,
            "skill:bmad-testarch-test-review": cls.QA_REPORT,
        }
        result = alias_map.get(normalized)
        if result is None:
            msg = f"Unknown BMAD skill: {name!r}"
            raise ValueError(msg)
        return result


def compute_dedup_hash(file_path: str, rule_id: str, severity: str, description: str) -> str:
    """SHA256(file_path|rule_id|severity|normalize(description))。

    算法与 Story 3.1 中的 finding 匹配键保持一致。
    不额外移除标点，只做空白压缩 + strip + lower。
    """
    normalized = " ".join(description.strip().lower().split())
    raw = f"{file_path}|{rule_id}|{severity}|{normalized}"
    return hashlib.sha256(raw.encode()).hexdigest()


class BmadFinding(_StrictBase):
    """BMAD 解析产出的单条 finding。"""

    severity: FindingSeverity
    category: str
    description: str
    file_path: str
    line: int | None = None
    rule_id: str
    raw_location: str | None = None
    dedup_hash: str | None = None

    @model_validator(mode="after")
    def _compute_hash(self) -> BmadFinding:
        if self.dedup_hash is None:
            self.dedup_hash = compute_dedup_hash(
                self.file_path, self.rule_id, self.severity, self.description
            )
        return self


class BmadParseResult(_StrictBase):
    """BmadAdapter.parse() 的返回结果。"""

    skill_type: BmadSkillType
    verdict: ParseVerdict
    findings: list[BmadFinding]
    parser_mode: ParserMode
    raw_markdown_hash: str
    raw_output_preview: str
    parse_error: str | None = None
    parsed_at: datetime
