"""test_policy_audit — QA / regression 共享 command-audit 校验。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from ato.config import EffectiveTestPolicy
from ato.models.schemas import BmadParseResult, RegressionCommandAuditEntry

CommandAuditViolationCode = Literal[
    "COMMANDS_EXECUTED_MISSING",
    "COMMANDS_EXECUTED_MALFORMED",
    "REQUIRED_ORDER_VIOLATION",
    "REQUIRED_COMMANDS_INCOMPLETE",
    "OPTIONAL_PRIORITY_VIOLATION",
    "ADDITIONAL_BUDGET_EXCEEDED",
    "ADDITIONAL_GATE_CLOSED",
    "DISCOVERY_DISABLED",
    "INVALID_COMMAND_SOURCE",
    "INVALID_TRIGGER_REASON",
]

CommandAuditStatus = Literal["missing", "malformed", "invalid"]

_DISCOVERY_SOURCES = {"llm_discovered", "llm_diagnostic"}
_QA_PROTOCOL_INVALID_OPTIONS = ["retry", "skip", "escalate"]
_PREVIEW_LINE_LIMIT = 5
_PREVIEW_LINE_MAX_CHARS = 200


class CommandAuditValidationError(ValueError):
    """共享 command-audit fail-closed 异常。"""

    __slots__ = ("detail", "violation_code")

    violation_code: CommandAuditViolationCode
    detail: str

    def __init__(self, violation_code: CommandAuditViolationCode, detail: str) -> None:
        self.violation_code = violation_code
        self.detail = detail
        super().__init__(detail)


def _raise_validation_error(
    violation_code: CommandAuditViolationCode,
    detail: str,
) -> None:
    raise CommandAuditValidationError(violation_code, detail)


def validate_command_audit(
    *,
    command_audit: Sequence[RegressionCommandAuditEntry],
    test_policy: EffectiveTestPolicy,
    skipped_command_reason: str | None = None,
) -> None:
    """基于 EffectiveTestPolicy 对 canonical command audit 执行 fail-closed 校验。"""

    required_commands = list(test_policy.required_commands)
    required_positions = {command: index for index, command in enumerate(required_commands)}
    optional_commands = list(test_policy.optional_commands)
    optional_commands_set = set(optional_commands)

    for entry in command_audit:
        if entry.command in required_positions:
            if entry.source != "project_defined":
                _raise_validation_error(
                    "INVALID_COMMAND_SOURCE",
                    "required command 必须标记为 project_defined，并使用正确的 trigger_reason",
                )
            expected_trigger = (
                "legacy_baseline" if test_policy.legacy_baseline else "required_layer"
            )
            if entry.trigger_reason != expected_trigger:
                _raise_validation_error(
                    "INVALID_TRIGGER_REASON",
                    "required command 必须标记为 project_defined，并使用正确的 trigger_reason",
                )
            continue

        if entry.command in optional_commands_set:
            if entry.source != "project_defined":
                _raise_validation_error(
                    "INVALID_COMMAND_SOURCE",
                    "optional command 必须标记为 project_defined，且 trigger_reason=optional_layer",
                )
            if entry.trigger_reason != "optional_layer":
                _raise_validation_error(
                    "INVALID_TRIGGER_REASON",
                    "optional command 必须标记为 project_defined，且 trigger_reason=optional_layer",
                )
            continue

        if entry.source == "project_defined":
            _raise_validation_error(
                "INVALID_COMMAND_SOURCE",
                "command_audit.source=project_defined 的命令必须来自已声明的 project-defined 集合",
            )

        if entry.source == "llm_discovered" and entry.trigger_reason != "discovery_fallback":
            _raise_validation_error(
                "INVALID_TRIGGER_REASON",
                "llm_discovered 命令必须使用 trigger_reason=discovery_fallback",
            )

        if entry.source == "llm_diagnostic" and entry.trigger_reason != "diagnostic":
            _raise_validation_error(
                "INVALID_TRIGGER_REASON",
                "llm_diagnostic 命令必须使用 trigger_reason=diagnostic",
            )

    prefix_required_count = 0
    for expected_command in required_commands:
        if prefix_required_count >= len(command_audit):
            break
        actual_command = command_audit[prefix_required_count].command
        if actual_command != expected_command:
            if actual_command in required_positions:
                _raise_validation_error(
                    "REQUIRED_ORDER_VIOLATION",
                    "required commands 必须保持声明顺序，且在 additional commands 之前执行",
                )
            break
        prefix_required_count += 1

    if any(entry.command in required_positions for entry in command_audit[prefix_required_count:]):
        _raise_validation_error(
            "REQUIRED_ORDER_VIOLATION",
            "required commands 必须先于所有 additional commands 完成",
        )

    executed_required_commands = [entry.command for entry in command_audit[:prefix_required_count]]
    missing_required_commands = [
        command for command in required_commands if command not in executed_required_commands
    ]

    if test_policy.legacy_baseline:
        if missing_required_commands and not skipped_command_reason:
            _raise_validation_error(
                "REQUIRED_COMMANDS_INCOMPLETE",
                "skipped legacy baseline commands 必须填写 skipped_command_reason",
            )
    elif missing_required_commands:
        _raise_validation_error(
            "REQUIRED_COMMANDS_INCOMPLETE",
            "explicit required commands 必须全部执行，且顺序必须与配置一致",
        )

    additional_entries = list(command_audit[prefix_required_count:])
    if len(additional_entries) > test_policy.max_additional_commands:
        _raise_validation_error(
            "ADDITIONAL_BUDGET_EXCEEDED",
            "executed additional commands 超过 max_additional_commands 限制",
        )

    if test_policy.allowed_when == "never" and additional_entries:
        _raise_validation_error(
            "ADDITIONAL_GATE_CLOSED",
            "allowed_when=never 时不允许执行 additional commands",
        )

    if test_policy.allowed_when == "after_required_failure" and additional_entries:
        has_required_failure = any(
            entry.exit_code not in (None, 0) for entry in command_audit[:prefix_required_count]
        )
        if not has_required_failure:
            _raise_validation_error(
                "ADDITIONAL_GATE_CLOSED",
                "allowed_when=after_required_failure 时，"
                "只有 required commands 失败后才允许追加命令",
            )

    if not test_policy.allow_discovery and any(
        entry.source in _DISCOVERY_SOURCES for entry in additional_entries
    ):
        _raise_validation_error(
            "DISCOVERY_DISABLED",
            "allow_discovery=false 时不允许执行 discovered 或 diagnostic commands",
        )

    remaining_optional = list(optional_commands)
    for entry in additional_entries:
        if entry.command in optional_commands_set:
            if entry.command in remaining_optional:
                remaining_optional.remove(entry.command)
            continue
        if entry.source in _DISCOVERY_SOURCES and remaining_optional:
            remaining = ", ".join(remaining_optional)
            _raise_validation_error(
                "OPTIONAL_PRIORITY_VIOLATION",
                "optional commands 必须先于 discovered/diagnostic commands 执行；"
                f"remaining optional commands: {remaining}",
            )


def build_qa_protocol_invalid_payload(
    *,
    task_id: str,
    parse_result: BmadParseResult,
    audit_status: CommandAuditStatus,
    violation_code: CommandAuditViolationCode,
    detail: str,
) -> dict[str, object]:
    """构造 `needs_human_review(reason=qa_protocol_invalid)` payload。"""

    raw_lines = parse_result.command_audit_raw_lines or []
    commands_executed_preview = [
        line[:_PREVIEW_LINE_MAX_CHARS] for line in raw_lines[:_PREVIEW_LINE_LIMIT]
    ]
    return {
        "reason": "qa_protocol_invalid",
        "task_id": task_id,
        "audit_status": audit_status,
        "violation_code": violation_code,
        "detail": detail,
        "raw_output_preview": parse_result.raw_output_preview,
        "commands_executed_preview": commands_executed_preview,
        "options": list(_QA_PROTOCOL_INVALID_OPTIONS),
    }
