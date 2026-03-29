"""progress — Background agent progress logging helpers."""

from __future__ import annotations

from typing import Literal

import structlog

from ato.models.schemas import ProgressCallback, ProgressEvent


def build_agent_progress_callback(
    *,
    logger: structlog.stdlib.BoundLogger,
    task_id: str | None,
    story_id: str,
    phase: str,
    role: str,
    cli_tool: Literal["claude", "codex"],
) -> ProgressCallback:
    """Build a logger-backed callback for normalized agent progress events."""

    async def _on_progress(event: ProgressEvent) -> None:
        logger.info(
            "agent_progress",
            task_id=task_id,
            story_id=story_id,
            phase=phase,
            role=role,
            cli_tool=cli_tool,
            progress_cli_tool=event.cli_tool,
            progress_type=event.event_type,
            progress_summary=event.summary,
            progress_at=event.timestamp.isoformat(),
        )

    return _on_progress
