"""test_command_harness — QA / regression command ledger and runner helpers."""

from __future__ import annotations

import argparse
import os
import shlex
import sqlite3
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from pydantic import ValidationError

from ato.config import EffectiveTestPolicy
from ato.models.db import get_connection, get_task_command_events
from ato.models.schemas import RegressionCommandAuditEntry
from ato.test_policy_audit import (
    CommandAuditStatus,
    CommandAuditValidationError,
    CommandAuditViolationCode,
    validate_command_audit,
)

TEST_COMMAND_RUNNER_NAME: Final[str] = "ato-test-run"
TEST_COMMAND_RUNNER_DIR: Final[Path] = Path(tempfile.gettempdir()) / "ato-test-runner"
TEST_COMMAND_RUNNER_PATH: Final[Path] = TEST_COMMAND_RUNNER_DIR / TEST_COMMAND_RUNNER_NAME
TEST_COMMAND_DB_ENV: Final[str] = "ATO_TEST_HARNESS_DB_PATH"
TEST_COMMAND_TASK_ENV: Final[str] = "ATO_TEST_HARNESS_TASK_ID"
TEST_COMMAND_PHASE_ENV: Final[str] = "ATO_TEST_HARNESS_PHASE"


@dataclass(slots=True)
class ResolvedCommandAudit:
    """Resolved authoritative command audit for a task."""

    command_audit: list[RegressionCommandAuditEntry] | None
    audit_status: CommandAuditStatus | None
    violation_code: CommandAuditViolationCode | None
    detail: str | None
    preview_lines: list[str]


def _runner_script_text(src_root: Path) -> str:
    runner_python = str(Path(sys.executable))
    return f"""#!/bin/sh
RUNNER_PYTHON={shlex.quote(runner_python)}
SRC_ROOT={shlex.quote(str(src_root))}

if [ -n "${{PYTHONPATH:-}}" ]; then
    export PYTHONPATH="$SRC_ROOT:$PYTHONPATH"
else
    export PYTHONPATH="$SRC_ROOT"
fi

exec "$RUNNER_PYTHON" -m ato.test_command_harness "$@"
"""


def ensure_test_command_runner() -> Path:
    """Create the `ato-test-run` helper script if needed and return its path."""
    TEST_COMMAND_RUNNER_DIR.mkdir(parents=True, exist_ok=True)
    src_root = Path(__file__).resolve().parents[1]
    content = _runner_script_text(src_root)
    if not TEST_COMMAND_RUNNER_PATH.exists() or TEST_COMMAND_RUNNER_PATH.read_text() != content:
        TEST_COMMAND_RUNNER_PATH.write_text(content, encoding="utf-8")
        TEST_COMMAND_RUNNER_PATH.chmod(0o755)
    return TEST_COMMAND_RUNNER_PATH


def build_test_command_env(
    *,
    db_path: Path,
    task_id: str,
    phase: str,
    base_env: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build child-process env so Codex can call `ato-test-run` from any worktree."""
    runner_path = ensure_test_command_runner()
    resolved_db_path = db_path.expanduser().resolve()
    env = dict(base_env or os.environ)
    runner_dir = str(runner_path.parent)
    path_parts = [runner_dir]
    if env.get("PATH"):
        path_parts.append(env["PATH"])
    env["PATH"] = os.pathsep.join(path_parts)
    env[TEST_COMMAND_DB_ENV] = str(resolved_db_path)
    env[TEST_COMMAND_TASK_ENV] = task_id
    env[TEST_COMMAND_PHASE_ENV] = phase
    return env


def format_harnessed_command(
    command: str,
    *,
    source: str,
    trigger: str,
) -> str:
    """Format a prompt-ready `ato-test-run` shell command."""
    return (
        f"{TEST_COMMAND_RUNNER_NAME} --source {shlex.quote(source)} "
        f"--trigger {shlex.quote(trigger)} --command {shlex.quote(command)}"
    )


def render_command_audit_line(entry: RegressionCommandAuditEntry) -> str:
    """Render a canonical command-audit line preview."""
    trigger_map = {
        "required_layer": "required_layer:auto",
        "optional_layer": "optional_layer:auto",
        "discovery_fallback": "fallback:auto",
        "diagnostic": "diagnostic:auto",
        "legacy_baseline": "required_layer:legacy",
    }
    trigger = trigger_map.get(entry.trigger_reason, entry.trigger_reason)
    exit_code = "null" if entry.exit_code is None else str(entry.exit_code)
    return (
        f"- `{entry.command}` | source={entry.source} | trigger={trigger} | exit_code={exit_code}"
    )


def _command_uses_harness(command: str) -> bool:
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return TEST_COMMAND_RUNNER_NAME in command
    if not tokens:
        return False
    first = ""
    for token in tokens:
        if "=" in token and not token.startswith("/") and token.index("=") > 0:
            continue
        first = token
        break
    if not first:
        return False
    return first == TEST_COMMAND_RUNNER_NAME or first.endswith(f"/{TEST_COMMAND_RUNNER_NAME}")


def _canonicalize_trigger_reason(trigger_reason: str | None) -> str | None:
    """Convert raw harness trigger strings into canonical audit trigger reasons."""
    if trigger_reason is None:
        return None
    prefix = trigger_reason.split(":", 1)[0]
    if prefix == "fallback":
        return "discovery_fallback"
    if prefix in {
        "required_layer",
        "optional_layer",
        "discovery_fallback",
        "diagnostic",
        "legacy_baseline",
    }:
        return prefix
    return trigger_reason


def _split_attempts(
    entries: list[RegressionCommandAuditEntry],
    test_policy: EffectiveTestPolicy,
) -> list[list[RegressionCommandAuditEntry]]:
    if not entries:
        return []
    required_commands = list(test_policy.required_commands)
    if not required_commands:
        return [entries]
    first_required = required_commands[0]
    starts = [0]
    for idx in range(1, len(entries)):
        if entries[idx].command == first_required:
            starts.append(idx)
    if len(starts) == 1:
        return [entries]
    attempts: list[list[RegressionCommandAuditEntry]] = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(entries)
        attempts.append(entries[start:end])
    return attempts


def _validate_attempts(
    entries: list[RegressionCommandAuditEntry],
    test_policy: EffectiveTestPolicy,
    *,
    skipped_command_reason: str | None = None,
) -> ResolvedCommandAudit:
    attempts = _split_attempts(entries, test_policy)
    if not attempts:
        return ResolvedCommandAudit(
            command_audit=None,
            audit_status="missing",
            violation_code="COMMANDS_EXECUTED_MISSING",
            detail="No harness-tracked commands were recorded",
            preview_lines=[],
        )

    last_error: CommandAuditValidationError | None = None
    for attempt in reversed(attempts):
        try:
            validate_command_audit(
                command_audit=attempt,
                test_policy=test_policy,
                skipped_command_reason=skipped_command_reason,
            )
            return ResolvedCommandAudit(
                command_audit=attempt,
                audit_status=None,
                violation_code=None,
                detail=None,
                preview_lines=[render_command_audit_line(entry) for entry in attempt],
            )
        except CommandAuditValidationError as exc:
            last_error = exc

    detail = last_error.detail if last_error is not None else "Command audit validation failed"
    violation_code = (
        last_error.violation_code if last_error is not None else "COMMANDS_EXECUTED_MALFORMED"
    )
    return ResolvedCommandAudit(
        command_audit=None,
        audit_status="invalid",
        violation_code=violation_code,
        detail=detail,
        preview_lines=[render_command_audit_line(entry) for entry in attempts[-1]],
    )


async def resolve_command_audit_from_ledger(
    *,
    db_path: Path,
    task_id: str,
    test_policy: EffectiveTestPolicy,
    skipped_command_reason: str | None = None,
) -> ResolvedCommandAudit | None:
    """Resolve authoritative command audit from task_command_events ledger."""
    db = await get_connection(db_path)
    try:
        observed = await get_task_command_events(db, task_id, record_type="observed")
        audited = await get_task_command_events(db, task_id, record_type="audit")
    finally:
        await db.close()

    if not observed and not audited:
        return None

    if observed:
        bypassed = [entry.command for entry in observed if not _command_uses_harness(entry.command)]
        if bypassed:
            return ResolvedCommandAudit(
                command_audit=None,
                audit_status="invalid",
                violation_code="COMMANDS_EXECUTED_MALFORMED",
                detail=(
                    "Found shell commands executed outside ato-test-run: " + ", ".join(bypassed[:3])
                ),
                preview_lines=bypassed[:5],
            )

    if observed and len(observed) != len(audited):
        preview = [entry.command for entry in observed[:5]]
        return ResolvedCommandAudit(
            command_audit=None,
            audit_status="invalid",
            violation_code="COMMANDS_EXECUTED_MALFORMED",
            detail=(
                "Harness ledger mismatch: observed wrapper invocations "
                f"({len(observed)}) != audited commands ({len(audited)})"
            ),
            preview_lines=preview,
        )

    try:
        audited_entries = [
            RegressionCommandAuditEntry.model_validate(
                {
                    "command": entry.command,
                    "source": entry.source,
                    "trigger_reason": _canonicalize_trigger_reason(entry.trigger_reason),
                    "exit_code": entry.exit_code,
                }
            )
            for entry in audited
        ]
    except ValidationError as exc:
        preview = [
            (
                f"{entry.command} | source={entry.source} | "
                f"trigger={entry.trigger_reason} | exit_code={entry.exit_code}"
            )
            for entry in audited[:5]
        ]
        return ResolvedCommandAudit(
            command_audit=None,
            audit_status="invalid",
            violation_code="COMMANDS_EXECUTED_MALFORMED",
            detail=f"Invalid harness command audit entry: {exc.errors()[0]['msg']}",
            preview_lines=preview,
        )
    return _validate_attempts(
        audited_entries,
        test_policy,
        skipped_command_reason=skipped_command_reason,
    )


def _sqlite_connect(db_path: str) -> sqlite3.Connection:
    db_file = Path(db_path).expanduser()
    if not db_file.is_absolute():
        msg = f"Harness DB path must be absolute, got: {db_path}"
        raise RuntimeError(msg)
    if not db_file.parent.exists():
        msg = f"Harness DB parent directory does not exist: {db_file.parent}"
        raise RuntimeError(msg)
    conn = sqlite3.connect(str(db_file), timeout=5.0)
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        msg = f"Missing required environment variable: {name}"
        raise RuntimeError(msg)
    return value


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for `ato-test-run`."""
    parser = argparse.ArgumentParser(prog=TEST_COMMAND_RUNNER_NAME)
    parser.add_argument("--source", required=True)
    parser.add_argument("--trigger", required=True)
    parser.add_argument("--command", required=True)
    args = parser.parse_args(argv)

    db_path = _require_env(TEST_COMMAND_DB_ENV)
    task_id = _require_env(TEST_COMMAND_TASK_ENV)
    phase = _require_env(TEST_COMMAND_PHASE_ENV)
    created_at = datetime.now(tz=UTC)

    conn = _sqlite_connect(db_path)
    try:
        cursor = conn.execute(
            "INSERT INTO task_command_events (task_id, phase, record_type, command, source, "
            "trigger_reason, exit_code, created_at, completed_at) "
            "VALUES (?, ?, 'audit', ?, ?, ?, NULL, ?, NULL)",
            (
                task_id,
                phase,
                args.command,
                args.source,
                args.trigger,
                created_at.isoformat(),
            ),
        )
        if cursor.lastrowid is None:
            msg = "Failed to create task_command_events row"
            raise RuntimeError(msg)
        event_id = int(cursor.lastrowid)
        conn.commit()

        completed = subprocess.run(args.command, shell=True)
        conn.execute(
            "UPDATE task_command_events SET exit_code = ?, completed_at = ? WHERE event_id = ?",
            (
                int(completed.returncode),
                datetime.now(tz=UTC).isoformat(),
                event_id,
            ),
        )
        conn.commit()
        return int(completed.returncode)
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
