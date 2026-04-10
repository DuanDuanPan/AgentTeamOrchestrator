"""test_test_policy_audit — QA / regression 共享 command-audit 校验测试。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ato.config import (
    ATOSettings,
    PhaseTestPolicyConfig,
    TestLayerConfig,
    resolve_effective_test_policy,
)
from ato.models.schemas import BmadParseResult, BmadSkillType, RegressionCommandAuditEntry
from ato.test_policy_audit import (
    CommandAuditValidationError,
    build_qa_protocol_invalid_payload,
    validate_command_audit,
)

_NOW = datetime.now(tz=UTC)


def _entry(
    command: str,
    *,
    source: str,
    trigger_reason: str,
    exit_code: int | None,
) -> RegressionCommandAuditEntry:
    return RegressionCommandAuditEntry(
        command=command,
        source=source,  # type: ignore[arg-type]
        trigger_reason=trigger_reason,  # type: ignore[arg-type]
        exit_code=exit_code,
    )


def _policy(
    *,
    phase: str = "qa_testing",
    required_layers: list[str] | None = None,
    optional_layers: list[str] | None = None,
    allow_discovery: bool = True,
    max_additional_commands: int = 2,
    allowed_when: str = "after_required_commands",
) -> object:
    settings = ATOSettings(
        roles={"qa": {"cli": "codex"}},  # type: ignore[dict-item]
        phases=[
            {  # type: ignore[list-item]
                "name": phase,
                "role": "qa",
                "type": "convergent_loop" if phase == "qa_testing" else "structured_job",
                "next_on_success": "done",
                "next_on_failure": "done",
            },
        ],
        test_catalog={
            "lint": TestLayerConfig(commands=["uv run ruff check src tests"]),
            "unit": TestLayerConfig(commands=["uv run pytest tests/unit/"]),
            "integration": TestLayerConfig(commands=["uv run pytest tests/integration/"]),
        },
        phase_test_policy={
            phase: PhaseTestPolicyConfig(
                required_layers=required_layers or [],
                optional_layers=optional_layers or [],
                allow_discovery=allow_discovery,
                max_additional_commands=max_additional_commands,
                allowed_when=allowed_when,  # type: ignore[arg-type]
            )
        },
    )
    policy = resolve_effective_test_policy(settings, phase)
    assert policy is not None
    return policy


def test_validate_command_audit_rejects_required_order_violation() -> None:
    policy = _policy(required_layers=["lint", "unit"], optional_layers=[])
    entries = [
        _entry(
            "uv run pytest tests/unit/",
            source="project_defined",
            trigger_reason="required_layer",
            exit_code=0,
        ),
        _entry(
            "uv run ruff check src tests",
            source="project_defined",
            trigger_reason="required_layer",
            exit_code=0,
        ),
    ]

    with pytest.raises(CommandAuditValidationError) as exc:
        validate_command_audit(command_audit=entries, test_policy=policy)

    assert exc.value.violation_code == "REQUIRED_ORDER_VIOLATION"


def test_validate_command_audit_rejects_required_commands_incomplete() -> None:
    policy = _policy(required_layers=["lint", "unit"], optional_layers=[])
    entries = [
        _entry(
            "uv run ruff check src tests",
            source="project_defined",
            trigger_reason="required_layer",
            exit_code=0,
        )
    ]

    with pytest.raises(CommandAuditValidationError) as exc:
        validate_command_audit(command_audit=entries, test_policy=policy)

    assert exc.value.violation_code == "REQUIRED_COMMANDS_INCOMPLETE"


def test_validate_command_audit_rejects_optional_priority_violation() -> None:
    policy = _policy(required_layers=["unit"], optional_layers=["integration"])
    entries = [
        _entry(
            "uv run pytest tests/unit/",
            source="project_defined",
            trigger_reason="required_layer",
            exit_code=0,
        ),
        _entry(
            "uv run pytest tests/smoke/",
            source="llm_discovered",
            trigger_reason="discovery_fallback",
            exit_code=0,
        ),
    ]

    with pytest.raises(CommandAuditValidationError) as exc:
        validate_command_audit(command_audit=entries, test_policy=policy)

    assert exc.value.violation_code == "OPTIONAL_PRIORITY_VIOLATION"
    assert "remaining optional commands" in exc.value.detail


def test_validate_command_audit_rejects_budget_exceeded() -> None:
    policy = _policy(
        required_layers=["unit"],
        optional_layers=[],
        max_additional_commands=1,
    )
    entries = [
        _entry(
            "uv run pytest tests/unit/",
            source="project_defined",
            trigger_reason="required_layer",
            exit_code=0,
        ),
        _entry(
            "uv run pytest tests/integration/",
            source="llm_discovered",
            trigger_reason="discovery_fallback",
            exit_code=1,
        ),
        _entry(
            "uv run pytest tests/smoke/",
            source="llm_diagnostic",
            trigger_reason="diagnostic",
            exit_code=1,
        ),
    ]

    with pytest.raises(CommandAuditValidationError) as exc:
        validate_command_audit(command_audit=entries, test_policy=policy)

    assert exc.value.violation_code == "ADDITIONAL_BUDGET_EXCEEDED"


def test_validate_command_audit_rejects_discovery_when_disabled() -> None:
    policy = _policy(
        required_layers=["unit"],
        optional_layers=[],
        allow_discovery=False,
        max_additional_commands=1,
    )
    entries = [
        _entry(
            "uv run pytest tests/unit/",
            source="project_defined",
            trigger_reason="required_layer",
            exit_code=0,
        ),
        _entry(
            "uv run pytest tests/integration/",
            source="llm_discovered",
            trigger_reason="discovery_fallback",
            exit_code=1,
        ),
    ]

    with pytest.raises(CommandAuditValidationError) as exc:
        validate_command_audit(command_audit=entries, test_policy=policy)

    assert exc.value.violation_code == "DISCOVERY_DISABLED"


def test_validate_command_audit_ignores_auxiliary_inspection() -> None:
    policy = _policy(
        phase="regression",
        required_layers=["unit"],
        optional_layers=["integration"],
        allow_discovery=False,
        max_additional_commands=1,
        allowed_when="after_required_commands",
    )
    entries = [
        _entry(
            "git status --short",
            source="llm_diagnostic",
            trigger_reason="diagnostic",
            exit_code=0,
        ),
        _entry(
            "sed -n '1,220p' package.json",
            source="llm_diagnostic",
            trigger_reason="diagnostic",
            exit_code=0,
        ),
        _entry(
            "uv run pytest tests/unit/",
            source="project_defined",
            trigger_reason="required_layer",
            exit_code=0,
        ),
        _entry(
            "rg --files -g 'package.json' .",
            source="llm_diagnostic",
            trigger_reason="diagnostic",
            exit_code=0,
        ),
        _entry(
            "uv run pytest tests/integration/",
            source="project_defined",
            trigger_reason="optional_layer",
            exit_code=0,
        ),
    ]

    validate_command_audit(command_audit=entries, test_policy=policy)


def test_validate_command_audit_allows_qa_bounded_fallback_discovery() -> None:
    settings = ATOSettings(
        roles={"qa": {"cli": "codex"}},  # type: ignore[dict-item]
        phases=[
            {  # type: ignore[list-item]
                "name": "qa_testing",
                "role": "qa",
                "type": "convergent_loop",
                "next_on_success": "done",
                "next_on_failure": "done",
            },
        ],
    )
    policy = resolve_effective_test_policy(settings, "qa_testing")
    assert policy is not None

    entries = [
        _entry(
            "uv run pytest tests/unit/",
            source="llm_discovered",
            trigger_reason="discovery_fallback",
            exit_code=1,
        ),
        _entry(
            "uv run pytest tests/integration/",
            source="llm_diagnostic",
            trigger_reason="diagnostic",
            exit_code=1,
        ),
    ]

    validate_command_audit(command_audit=entries, test_policy=policy)


def test_build_qa_protocol_invalid_payload_clips_preview() -> None:
    parse_result = BmadParseResult(
        skill_type=BmadSkillType.QA_REPORT,
        verdict="changes_requested",
        findings=[],
        parser_mode="deterministic",
        raw_markdown_hash="hash",
        raw_output_preview="preview",
        command_audit=[],
        command_audit_parse_status="parsed",
        command_audit_parse_error=None,
        command_audit_raw_lines=[
            "x" * 220,
            "line-2",
            "line-3",
            "line-4",
            "line-5",
            "line-6",
        ],
        parsed_at=_NOW,
    )

    payload = build_qa_protocol_invalid_payload(
        task_id="task-1",
        parse_result=parse_result,
        audit_status="invalid",
        violation_code="OPTIONAL_PRIORITY_VIOLATION",
        detail="detail",
    )

    assert payload["reason"] == "qa_protocol_invalid"
    assert payload["task_id"] == "task-1"
    assert payload["options"] == ["retry", "skip", "escalate"]
    preview = payload["commands_executed_preview"]
    assert isinstance(preview, list)
    assert len(preview) == 5
    assert len(preview[0]) == 200
