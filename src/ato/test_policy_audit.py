"""test_policy_audit — QA / regression 共享 command-audit 校验。"""

from __future__ import annotations

import re
import shlex
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
_AUXILIARY_EXECUTABLES = frozenset(
    {
        "cat",
        "find",
        "git",
        "head",
        "ls",
        "pwd",
        "readlink",
        "realpath",
        "rg",
        "ripgrep",
        "sed",
        "tail",
        "which",
    }
)
_POLICY_DOMAIN_WRAPPERS = frozenset(
    {
        "bash",
        "bun",
        "hatch",
        "just",
        "make",
        "npm",
        "nox",
        "pipenv",
        "pnpm",
        "poetry",
        "sh",
        "task",
        "tox",
        "uv",
        "yarn",
        "zsh",
    }
)
_POLICY_DOMAIN_DIRECT_EXECUTABLES = frozenset(
    {
        "cargo",
        "ctest",
        "dotnet",
        "electron-vite",
        "eslint",
        "go",
        "gradle",
        "gradlew",
        "jest",
        "mocha",
        "mvn",
        "mvnw",
        "mypy",
        "playwright",
        "pytest",
        "ruff",
        "tsc",
        "vite",
        "vitest",
    }
)
_POLICY_DOMAIN_TOKEN_RE = re.compile(
    r"(?i)(^|[:/_\-.])(build|check|clippy|e2e|integration|lint|smoke|test|typecheck|type-check|unit|verify|vet)([:/_\-.]|$)"
)


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


def _split_shell_segments(command: str) -> list[str]:
    return [
        segment.strip()
        for segment in re.split(r"\s*(?:&&|\|\||;)\s*", command)
        if segment.strip()
    ]


def _segment_tokens(segment: str) -> list[str]:
    try:
        return shlex.split(segment)
    except ValueError:
        return segment.split()


def _normalize_executable(token: str) -> str:
    return token.rsplit("/", 1)[-1].lower()


def _has_policy_domain_token(token: str) -> bool:
    return bool(_POLICY_DOMAIN_TOKEN_RE.search(token.lower()))


def _segment_executes_policy_domain(segment: str) -> bool:
    tokens = _segment_tokens(segment)
    if not tokens:
        return False

    executable = _normalize_executable(tokens[0])
    args = [token.lower() for token in tokens[1:]]

    if executable in _AUXILIARY_EXECUTABLES:
        return False

    if executable in {"python", "python3"}:
        if len(args) >= 2 and args[0] == "-m":
            module_name = args[1]
            return module_name in {"pytest", "unittest"} or _has_policy_domain_token(module_name)
        return False

    if executable == "node":
        return "--test" in args

    if executable in _POLICY_DOMAIN_WRAPPERS:
        return any(_has_policy_domain_token(arg) for arg in args)

    if executable in _POLICY_DOMAIN_DIRECT_EXECUTABLES:
        if executable == "playwright":
            return "test" in args
        if executable == "ruff":
            return "check" in args
        if executable in {"vite", "electron-vite"}:
            return "build" in args
        if executable == "cargo":
            return any(arg in {"test", "build", "check", "clippy"} for arg in args)
        if executable == "go":
            return any(arg in {"test", "build", "vet"} for arg in args)
        if executable == "dotnet":
            return any(arg in {"test", "build"} for arg in args)
        if executable in {"mvn", "mvnw", "gradle", "gradlew"}:
            return any(_has_policy_domain_token(arg) for arg in args)
        return True

    return False


def _is_policy_domain_command(command: str, test_policy: EffectiveTestPolicy) -> bool:
    normalized_command = command.strip()
    project_defined_commands = {cmd.strip() for cmd in test_policy.project_defined_commands}
    if normalized_command in project_defined_commands:
        return True

    return any(
        _segment_executes_policy_domain(segment)
        for segment in _split_shell_segments(normalized_command)
    )


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

    policy_entries = [
        entry for entry in command_audit if _is_policy_domain_command(entry.command, test_policy)
    ]

    prefix_required_count = 0
    for expected_command in required_commands:
        if prefix_required_count >= len(policy_entries):
            break
        actual_command = policy_entries[prefix_required_count].command
        if actual_command != expected_command:
            if actual_command in required_positions:
                _raise_validation_error(
                    "REQUIRED_ORDER_VIOLATION",
                    "required commands 必须保持声明顺序，且在 additional commands 之前执行",
                )
            break
        prefix_required_count += 1

    if any(entry.command in required_positions for entry in policy_entries[prefix_required_count:]):
        _raise_validation_error(
            "REQUIRED_ORDER_VIOLATION",
            "required commands 必须先于所有 additional commands 完成",
        )

    executed_required_commands = [entry.command for entry in policy_entries[:prefix_required_count]]
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

    additional_entries = list(policy_entries[prefix_required_count:])
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
            entry.exit_code not in (None, 0) for entry in policy_entries[:prefix_required_count]
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
    commands_executed_preview: Sequence[str] | None = None,
) -> dict[str, object]:
    """构造 `needs_human_review(reason=qa_protocol_invalid)` payload。"""

    preview_source = (
        list(commands_executed_preview)
        if commands_executed_preview is not None
        else (parse_result.command_audit_raw_lines or [])
    )
    preview_lines = [
        line[:_PREVIEW_LINE_MAX_CHARS] for line in preview_source[:_PREVIEW_LINE_LIMIT]
    ]
    return {
        "reason": "qa_protocol_invalid",
        "task_id": task_id,
        "audit_status": audit_status,
        "violation_code": violation_code,
        "detail": detail,
        "raw_output_preview": parse_result.raw_output_preview,
        "commands_executed_preview": preview_lines,
        "options": list(_QA_PROTOCOL_INVALID_OPTIONS),
    }
