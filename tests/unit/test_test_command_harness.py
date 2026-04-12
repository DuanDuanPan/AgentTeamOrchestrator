"""test_test_command_harness — QA / regression harness ledger tests."""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

import ato.test_command_harness as harness_module
from ato.config import EffectiveTestPolicy
from ato.models.db import (
    get_connection,
    get_task_command_events,
    insert_story,
    insert_task,
    insert_task_command_event,
)
from ato.models.schemas import StoryRecord, TaskRecord
from ato.test_command_harness import (
    _sqlite_connect,
    build_test_command_env,
    resolve_command_audit_from_ledger,
)


def _make_policy() -> EffectiveTestPolicy:
    return EffectiveTestPolicy(
        phase="qa_testing",
        policy_source="explicit",
        required_layers=["unit"],
        optional_layers=["integration"],
        required_layer_commands=[],
        optional_layer_commands=[],
        missing_optional_layers=[],
        allow_discovery=True,
        max_additional_commands=2,
        allowed_when="after_required_commands",
        required_commands=["uv run pytest tests/unit/"],
        optional_commands=["uv run pytest tests/integration/"],
        project_defined_commands=[
            "uv run pytest tests/unit/",
            "uv run pytest tests/integration/",
        ],
        discovery_priority=[],
        legacy_baseline=False,
    )


async def _setup_task(db_path: Path, task_id: str = "task-harness") -> None:
    now = datetime.now(tz=UTC)
    story = StoryRecord(
        story_id="story-harness",
        title="Harness Story",
        status="in_progress",
        current_phase="qa_testing",
        created_at=now,
        updated_at=now,
    )
    task = TaskRecord(
        task_id=task_id,
        story_id="story-harness",
        phase="qa_testing",
        role="qa",
        cli_tool="codex",
        status="running",
        started_at=now,
    )
    db = await get_connection(db_path)
    try:
        await insert_story(db, story)
        await insert_task(db, task)
    finally:
        await db.close()


class TestResolveCommandAuditFromLedger:
    async def test_prefers_last_valid_attempt(self, initialized_db_path: Path) -> None:
        await _setup_task(initialized_db_path)
        db = await get_connection(initialized_db_path)
        try:
            await insert_task_command_event(
                db,
                task_id="task-harness",
                phase="qa_testing",
                record_type="audit",
                command="uv run pytest tests/unit/",
                source="project_defined",
                trigger_reason="required_layer:unit",
                exit_code=0,
            )
            await insert_task_command_event(
                db,
                task_id="task-harness",
                phase="qa_testing",
                record_type="audit",
                command="pytest tests/smoke/",
                source="llm_discovered",
                trigger_reason="fallback:pytest",
                exit_code=0,
            )
            await insert_task_command_event(
                db,
                task_id="task-harness",
                phase="qa_testing",
                record_type="audit",
                command="uv run pytest tests/unit/",
                source="project_defined",
                trigger_reason="required_layer:unit",
                exit_code=0,
            )
            await insert_task_command_event(
                db,
                task_id="task-harness",
                phase="qa_testing",
                record_type="audit",
                command="uv run pytest tests/integration/",
                source="project_defined",
                trigger_reason="optional_layer:integration",
                exit_code=0,
            )
        finally:
            await db.close()

        resolved = await resolve_command_audit_from_ledger(
            db_path=initialized_db_path,
            task_id="task-harness",
            test_policy=_make_policy(),
        )

        assert resolved is not None
        assert resolved.audit_status is None
        assert resolved.command_audit is not None
        assert [entry.command for entry in resolved.command_audit] == [
            "uv run pytest tests/unit/",
            "uv run pytest tests/integration/",
        ]

    async def test_detects_observed_command_bypass(self, initialized_db_path: Path) -> None:
        await _setup_task(initialized_db_path)
        db = await get_connection(initialized_db_path)
        try:
            await insert_task_command_event(
                db,
                task_id="task-harness",
                phase="qa_testing",
                record_type="observed",
                command="uv run pytest tests/unit/",
            )
        finally:
            await db.close()

        resolved = await resolve_command_audit_from_ledger(
            db_path=initialized_db_path,
            task_id="task-harness",
            test_policy=_make_policy(),
        )

        assert resolved is not None
        assert resolved.audit_status == "invalid"
        assert resolved.violation_code == "COMMANDS_EXECUTED_MALFORMED"
        assert resolved.detail is not None
        assert "outside ato-test-run" in resolved.detail

    async def test_invalid_raw_trigger_reason_fails_closed(self, initialized_db_path: Path) -> None:
        await _setup_task(initialized_db_path)
        db = await get_connection(initialized_db_path)
        try:
            await insert_task_command_event(
                db,
                task_id="task-harness",
                phase="qa_testing",
                record_type="audit",
                command="uv run pytest tests/unit/",
                source="project_defined",
                trigger_reason="unexpected_mode:unit",
                exit_code=0,
            )
        finally:
            await db.close()

        resolved = await resolve_command_audit_from_ledger(
            db_path=initialized_db_path,
            task_id="task-harness",
            test_policy=_make_policy(),
        )

        assert resolved is not None
        assert resolved.audit_status == "invalid"
        assert resolved.violation_code == "COMMANDS_EXECUTED_MALFORMED"
        assert resolved.detail is not None
        assert "Invalid harness command audit entry" in resolved.detail


class TestHarnessDbPathHandling:
    async def test_generated_runner_executes_main_and_records_audit(
        self,
        initialized_db_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        await _setup_task(initialized_db_path)
        runner_dir = tmp_path / "runner"
        runner_path = runner_dir / "ato-test-run"
        monkeypatch.setattr(harness_module, "TEST_COMMAND_RUNNER_DIR", runner_dir)
        monkeypatch.setattr(harness_module, "TEST_COMMAND_RUNNER_PATH", runner_path)

        env = build_test_command_env(
            db_path=initialized_db_path,
            task_id="task-harness",
            phase="qa_testing",
            base_env={},
        )
        result = subprocess.run(
            [
                str(runner_path),
                "--source",
                "project_defined",
                "--trigger",
                "required_layer:unit",
                "--command",
                "true",
            ],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 0
        db = await get_connection(initialized_db_path)
        try:
            events = await get_task_command_events(db, "task-harness", record_type="audit")
        finally:
            await db.close()
        assert len(events) == 1
        assert events[0].command == "true"
        assert events[0].exit_code == 0

    def test_build_test_command_env_writes_runner_bound_to_current_python(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        runner_dir = tmp_path / "runner dir"
        runner_path = runner_dir / "ato-test-run"
        monkeypatch.setattr(harness_module, "TEST_COMMAND_RUNNER_DIR", runner_dir)
        monkeypatch.setattr(harness_module, "TEST_COMMAND_RUNNER_PATH", runner_path)
        monkeypatch.setattr(sys, "executable", "/tmp/python with spaces/bin/python3")

        build_test_command_env(
            db_path=tmp_path / ".ato" / "state.db",
            task_id="task-harness",
            phase="qa_testing",
            base_env={},
        )

        content = runner_path.read_text(encoding="utf-8")
        assert content.startswith("#!/bin/sh\n")
        assert "env python3" not in content
        assert "RUNNER_PYTHON='/tmp/python with spaces/bin/python3'" in content
        assert 'exec "$RUNNER_PYTHON" -m ato.test_command_harness "$@"' in content

    def test_build_test_command_env_normalizes_relative_db_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        env = build_test_command_env(
            db_path=Path(".ato/state.db"),
            task_id="task-harness",
            phase="qa_testing",
            base_env={},
        )

        assert env["ATO_TEST_HARNESS_DB_PATH"] == str((tmp_path / ".ato" / "state.db").resolve())

    def test_sqlite_connect_rejects_relative_db_path(self) -> None:
        with pytest.raises(RuntimeError, match="must be absolute"):
            _sqlite_connect(".ato/state.db")

    def test_sqlite_connect_rejects_missing_parent(self, tmp_path: Path) -> None:
        missing_db = (tmp_path / "missing" / "state.db").resolve()

        with pytest.raises(RuntimeError, match="parent directory does not exist"):
            _sqlite_connect(str(missing_db))
