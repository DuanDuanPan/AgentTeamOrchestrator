"""task_artifacts — canonical task artifact path helpers."""

from __future__ import annotations

from pathlib import Path

from ato.models.schemas import TaskRecord


def derive_phase_artifact_path(story_id: str, phase: str, project_root: Path) -> Path | None:
    """Return the canonical on-disk artifact path for a story/phase when one exists."""
    from ato.design_artifacts import ARTIFACTS_REL, derive_design_artifact_paths_relative

    if phase == "creating":
        return project_root / ARTIFACTS_REL / f"{story_id}.md"
    if phase == "designing":
        rel = derive_design_artifact_paths_relative(story_id)
        return project_root / rel["prototype_pen"]
    return None


def task_artifact_path(task: TaskRecord, project_root: Path) -> Path | None:
    """Resolve the canonical artifact path for a task."""
    phase_path = derive_phase_artifact_path(task.story_id, task.phase, project_root)
    if phase_path is not None:
        return phase_path
    if not task.expected_artifact:
        return None
    return Path(task.expected_artifact)


def task_artifact_exists(task: TaskRecord, project_root: Path) -> bool:
    """Return whether a task's canonical artifact exists on disk."""
    artifact_path = task_artifact_path(task, project_root)
    return artifact_path is not None and artifact_path.exists()
