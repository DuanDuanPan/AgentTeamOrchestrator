"""Real-git coverage for worktree boundary preflight gates."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ato.models.db import get_connection, init_db, insert_story
from ato.models.schemas import StoryRecord
from ato.worktree_mgr import WorktreeManager


async def _git(repo: Path, *args: str) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(repo),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return (
        proc.returncode or 0,
        stdout.decode() if stdout else "",
        stderr.decode() if stderr else "",
    )


def _story(story_id: str, *, worktree_path: str | None = None) -> StoryRecord:
    now = datetime.now(tz=UTC)
    return StoryRecord(
        story_id=story_id,
        title=f"Story {story_id}",
        status="in_progress",
        current_phase="developing",
        worktree_path=worktree_path,
        created_at=now,
        updated_at=now,
    )


@pytest.fixture()
async def preflight_repo(tmp_path: Path) -> tuple[Path, Path, WorktreeManager]:
    repo = tmp_path / "project"
    repo.mkdir()
    rc, _out, err = await _git(repo, "init")
    assert rc == 0, err
    await _git(repo, "branch", "-M", "main")
    await _git(repo, "config", "user.email", "test@test.com")
    await _git(repo, "config", "user.name", "Test User")
    (repo / "README.md").write_text("# Test Project\n")
    rc, _out, err = await _git(repo, "add", ".")
    assert rc == 0, err
    rc, _out, err = await _git(repo, "commit", "-m", "Initial commit")
    assert rc == 0, err

    db_path = tmp_path / ".ato" / "state.db"
    await init_db(db_path)
    mgr = WorktreeManager(project_root=repo, db_path=db_path)
    return repo, db_path, mgr


async def _insert_story(db_path: Path, story_id: str) -> None:
    db = await get_connection(db_path)
    try:
        await insert_story(db, _story(story_id))
    finally:
        await db.close()


async def _create_story_worktree(
    db_path: Path,
    mgr: WorktreeManager,
    story_id: str,
) -> Path:
    await _insert_story(db_path, story_id)
    return await mgr.create(story_id, base_ref="main")


async def test_pre_review_passes_for_clean_non_empty_committed_diff(
    preflight_repo: tuple[Path, Path, WorktreeManager],
) -> None:
    _repo, db_path, mgr = preflight_repo
    wt = await _create_story_worktree(db_path, mgr, "story-pass")

    (wt / "feature.txt").write_text("feature\n")
    rc, _out, err = await _git(wt, "add", "feature.txt")
    assert rc == 0, err
    rc, _out, err = await _git(wt, "commit", "-m", "story-pass: add feature")
    assert rc == 0, err

    result = await mgr.preflight_check("story-pass", "pre_review")

    assert result.passed is True
    assert result.base_ref == "main"
    assert result.changed_files == ["feature.txt"]
    assert result.porcelain_output == ""


async def test_pre_review_fails_for_dirty_worktree(
    preflight_repo: tuple[Path, Path, WorktreeManager],
) -> None:
    _repo, db_path, mgr = preflight_repo
    wt = await _create_story_worktree(db_path, mgr, "story-dirty")

    (wt / "untracked.txt").write_text("dirty\n")

    result = await mgr.preflight_check("story-dirty", "pre_review")

    assert result.passed is False
    assert result.failure_reason == "UNCOMMITTED_CHANGES"
    assert "untracked.txt" in result.porcelain_output


async def test_pre_review_fails_for_clean_empty_diff(
    preflight_repo: tuple[Path, Path, WorktreeManager],
) -> None:
    _repo, db_path, mgr = preflight_repo
    await _create_story_worktree(db_path, mgr, "story-empty")

    result = await mgr.preflight_check("story-empty", "pre_review")

    assert result.passed is False
    assert result.failure_reason == "EMPTY_DIFF"
    assert result.changed_files == []


async def test_changed_files_come_from_name_only_for_rename(
    preflight_repo: tuple[Path, Path, WorktreeManager],
) -> None:
    _repo, db_path, mgr = preflight_repo
    wt = await _create_story_worktree(db_path, mgr, "story-rename")

    rc, _out, err = await _git(wt, "mv", "README.md", "README-renamed.md")
    assert rc == 0, err
    rc, _out, err = await _git(wt, "commit", "-m", "story-rename: rename readme")
    assert rc == 0, err

    result = await mgr.preflight_check("story-rename", "pre_review")

    assert result.passed is True
    assert result.changed_files == ["README-renamed.md"]


async def test_pre_merge_uses_local_main_when_origin_fetch_fails(
    preflight_repo: tuple[Path, Path, WorktreeManager],
) -> None:
    _repo, db_path, mgr = preflight_repo
    wt = await _create_story_worktree(db_path, mgr, "story-pre-merge")

    (wt / "merge.txt").write_text("merge\n")
    rc, _out, err = await _git(wt, "add", "merge.txt")
    assert rc == 0, err
    rc, _out, err = await _git(wt, "commit", "-m", "story-pre-merge: add feature")
    assert rc == 0, err

    result = await mgr.preflight_check("story-pre-merge", "pre_merge")

    assert result.passed is True
    assert result.base_ref == "main"
    assert result.changed_files == ["merge.txt"]
