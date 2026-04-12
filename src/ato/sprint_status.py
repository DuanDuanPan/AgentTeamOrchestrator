"""sprint_status - 同步运行态 story 状态到 sprint-status.yaml。"""

from __future__ import annotations

import re
from datetime import UTC
from datetime import datetime as dt_cls
from pathlib import Path

import structlog

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

_SPRINT_STATUS_RELATIVE_PATH = Path("_bmad-output/implementation-artifacts/sprint-status.yaml")

_PHASE_TO_SPRINT_STATUS: dict[str, str | None] = {
    "queued": "backlog",
    "creating": "ready-for-dev",
    "designing": "ready-for-dev",
    "validating": "ready-for-dev",
    "dev_ready": "ready-for-dev",
    "developing": "in-progress",
    "reviewing": "review",
    "fixing": "review",
    "qa_testing": "in-progress",
    "uat": "in-progress",
    "merging": "in-progress",
    "regression": "in-progress",
    "done": "done",
    "blocked": None,
}

_LAST_UPDATED_RE = re.compile(r"^(\s*last_updated\s*:\s*)([^#\s]+)(?:\s*#.*)?$")


def _resolve_story_status(phase: str) -> str | None:
    """Map runtime phase to sprint-status.yaml story status vocabulary."""
    return _PHASE_TO_SPRINT_STATUS.get(phase)


def sprint_status_path_for_project(project_root: Path) -> Path:
    """Return the canonical sprint-status.yaml path for a project root."""
    return project_root / _SPRINT_STATUS_RELATIVE_PATH


def sync_story_phase_to_sprint_status(project_root: Path, story_id: str, phase: str) -> bool:
    """Best-effort sync of a single story's phase into sprint-status.yaml.

    Returns:
        ``True`` when the file was updated, ``False`` when no update was needed
        or the file/story key does not exist.
    """
    target_status = _resolve_story_status(phase)
    if target_status is None:
        return False

    sprint_status_path = sprint_status_path_for_project(project_root)
    if not sprint_status_path.is_file():
        return False

    content = sprint_status_path.read_text(encoding="utf-8")
    lines = content.splitlines(keepends=True)
    story_re = re.compile(
        rf"^(?P<indent>\s*)(?P<key>{re.escape(story_id)})(?P<sep>\s*:\s*)"
        r"(?P<value>[^\s#]+)(?P<suffix>\s*(?:#.*)?)$"
    )

    story_line_index: int | None = None
    story_line_changed = False

    for index, raw_line in enumerate(lines):
        line = raw_line.rstrip("\n")
        match = story_re.match(line)
        if match is None:
            continue
        story_line_index = index
        if match.group("value") == target_status:
            break
        newline = "\n" if raw_line.endswith("\n") else ""
        lines[index] = (
            f"{match.group('indent')}{match.group('key')}{match.group('sep')}"
            f"{target_status}{match.group('suffix')}{newline}"
        )
        story_line_changed = True
        break

    if story_line_index is None or not story_line_changed:
        return False

    updated_at = dt_cls.now(tz=UTC).date().isoformat()
    for index, raw_line in enumerate(lines):
        line = raw_line.rstrip("\n")
        match = _LAST_UPDATED_RE.match(line)
        if match is None:
            continue
        newline = "\n" if raw_line.endswith("\n") else ""
        lines[index] = f"{match.group(1)}{updated_at}{newline}"
        break

    sprint_status_path.write_text("".join(lines), encoding="utf-8")
    logger.info(
        "sprint_status_story_synced",
        story_id=story_id,
        phase=phase,
        sprint_status=target_status,
        path=str(sprint_status_path),
    )
    return True
