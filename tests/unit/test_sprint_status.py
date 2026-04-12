"""sprint_status 同步逻辑单元测试。"""

from __future__ import annotations

from pathlib import Path

from ato.sprint_status import (
    sprint_status_path_for_project,
    sync_story_phase_to_sprint_status,
)

_SAMPLE_SPRINT_STATUS = """\
generated: 2026-03-18
last_updated: 2026-04-11  # stories created
project: Demo

development_status:
  epic-5: in-progress
  5-1-asset-search-tag-management: ready-for-dev
  5-2-asset-recommendation-one-click-import: review
"""


def _write_sprint_status(project_root: Path, content: str = _SAMPLE_SPRINT_STATUS) -> Path:
    sprint_status_path = sprint_status_path_for_project(project_root)
    sprint_status_path.parent.mkdir(parents=True, exist_ok=True)
    sprint_status_path.write_text(content, encoding="utf-8")
    return sprint_status_path


class TestSprintStatusSync:
    def test_updates_story_line_and_last_updated(self, tmp_path: Path) -> None:
        sprint_status_path = _write_sprint_status(tmp_path)

        changed = sync_story_phase_to_sprint_status(
            tmp_path,
            "5-1-asset-search-tag-management",
            "done",
        )

        assert changed is True
        content = sprint_status_path.read_text(encoding="utf-8")
        assert "5-1-asset-search-tag-management: done" in content
        assert "5-2-asset-recommendation-one-click-import: review" in content
        assert "last_updated: 2026-04-11  # stories created" not in content
        assert "last_updated:" in content

    def test_noop_when_story_status_already_matches(self, tmp_path: Path) -> None:
        sprint_status_path = _write_sprint_status(
            tmp_path,
            _SAMPLE_SPRINT_STATUS.replace(
                "5-2-asset-recommendation-one-click-import: review",
                "5-2-asset-recommendation-one-click-import: done",
            ),
        )
        before = sprint_status_path.read_text(encoding="utf-8")

        changed = sync_story_phase_to_sprint_status(
            tmp_path,
            "5-2-asset-recommendation-one-click-import",
            "done",
        )

        assert changed is False
        assert sprint_status_path.read_text(encoding="utf-8") == before

    def test_noop_when_story_key_missing(self, tmp_path: Path) -> None:
        sprint_status_path = _write_sprint_status(tmp_path)
        before = sprint_status_path.read_text(encoding="utf-8")

        changed = sync_story_phase_to_sprint_status(
            tmp_path,
            "7-1-mandatory-item-compliance-engine",
            "done",
        )

        assert changed is False
        assert sprint_status_path.read_text(encoding="utf-8") == before

    def test_noop_when_phase_is_not_representable(self, tmp_path: Path) -> None:
        sprint_status_path = _write_sprint_status(tmp_path)
        before = sprint_status_path.read_text(encoding="utf-8")

        changed = sync_story_phase_to_sprint_status(
            tmp_path,
            "5-1-asset-search-tag-management",
            "blocked",
        )

        assert changed is False
        assert sprint_status_path.read_text(encoding="utf-8") == before
