"""test_batch — Batch 推荐逻辑单元测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from ato.batch import (
    BatchProposal,
    EpicInfo,
    LocalBatchRecommender,
    _normalize_short_key,
    _parse_dependency_table,
    build_canonical_key_map,
    confirm_batch,
    load_epics,
)
from ato.models.db import get_batch_stories, get_connection, get_story
from ato.models.schemas import StoryRecord, StoryStatus

_NOW = datetime.now(tz=UTC)


# ---------------------------------------------------------------------------
# _normalize_short_key
# ---------------------------------------------------------------------------


class TestNormalizeShortKey:
    def test_simple_number(self) -> None:
        assert _normalize_short_key("1.2") == "1-2"

    def test_alphanumeric(self) -> None:
        assert _normalize_short_key("2B.5") == "2b-5"

    def test_lowercase_passthrough(self) -> None:
        assert _normalize_short_key("2a-1") == "2a-1"

    def test_strips_whitespace(self) -> None:
        assert _normalize_short_key(" 3.1 ") == "3-1"


# ---------------------------------------------------------------------------
# _parse_dependency_table
# ---------------------------------------------------------------------------

_SAMPLE_TABLE = """\
**Story 级串行依赖链：**

| 串行链 | Stories |
|--------|---------|
| 基础设施 | 1.1 → 1.2 → 1.3 |
| 编排核心 | 1.2 → 2A.1 → 2A.2 |
| Batch | 1.2 → 2B.5（与编排核心并行） |
| BMAD/Worktree | 2A.1 → 2B.3, 2A.1 → 2B.4 |

**可并行分组：**
"""


class TestParseDependencyTable:
    def test_basic_chain(self) -> None:
        deps = _parse_dependency_table(_SAMPLE_TABLE)
        assert deps["1-2"] == ["1-1"]
        assert deps["1-3"] == ["1-2"]

    def test_cross_epic_dependency(self) -> None:
        deps = _parse_dependency_table(_SAMPLE_TABLE)
        assert deps["2a-1"] == ["1-2"]

    def test_parenthetical_notes_removed(self) -> None:
        deps = _parse_dependency_table(_SAMPLE_TABLE)
        assert deps["2b-5"] == ["1-2"]

    def test_comma_separated_chains(self) -> None:
        deps = _parse_dependency_table(_SAMPLE_TABLE)
        assert deps["2b-3"] == ["2a-1"]
        assert deps["2b-4"] == ["2a-1"]

    def test_no_deps_for_first_story(self) -> None:
        deps = _parse_dependency_table(_SAMPLE_TABLE)
        assert "1-1" not in deps


# ---------------------------------------------------------------------------
# load_epics
# ---------------------------------------------------------------------------


_SAMPLE_EPICS_MD = """\
# Epics

## Critical Path

| 串行链 | Stories |
|--------|---------|
| 基础 | 1.1 → 1.2 |

## Epic 1: 项目初始化

### Story 1.1: 项目脚手架与开发工具链

As a 开发者,
I want to setup scaffolding

### Story 1.2: SQLite 状态持久化

As a 操作者,
I want persistence

## Epic 2B: Agent 集成与工作空间

### Story 2B.5: 操作者可选择 story batch 并查看状态

As a 操作者,
I want batch selection
"""


class TestLoadEpics:
    def test_parses_stories(self, tmp_path: Path) -> None:
        epics_file = tmp_path / "epics.md"
        epics_file.write_text(_SAMPLE_EPICS_MD, encoding="utf-8")
        result = load_epics(epics_file)
        assert len(result) == 3

    def test_story_keys(self, tmp_path: Path) -> None:
        epics_file = tmp_path / "epics.md"
        epics_file.write_text(_SAMPLE_EPICS_MD, encoding="utf-8")
        result = load_epics(epics_file)
        short_keys = [s.short_key for s in result]
        assert "1-1" in short_keys
        assert "1-2" in short_keys
        assert "2b-5" in short_keys

    def test_epic_key_assignment(self, tmp_path: Path) -> None:
        epics_file = tmp_path / "epics.md"
        epics_file.write_text(_SAMPLE_EPICS_MD, encoding="utf-8")
        result = load_epics(epics_file)
        by_short = {s.short_key: s for s in result}
        assert by_short["1-1"].epic_key == "1"
        assert by_short["2b-5"].epic_key == "2b"

    def test_dependencies_populated(self, tmp_path: Path) -> None:
        epics_file = tmp_path / "epics.md"
        epics_file.write_text(_SAMPLE_EPICS_MD, encoding="utf-8")
        result = load_epics(epics_file)
        by_short = {s.short_key: s for s in result}
        assert "1-1" in by_short["1-2"].dependencies

    def test_title_extracted(self, tmp_path: Path) -> None:
        epics_file = tmp_path / "epics.md"
        epics_file.write_text(_SAMPLE_EPICS_MD, encoding="utf-8")
        result = load_epics(epics_file)
        by_short = {s.short_key: s for s in result}
        assert "脚手架" in by_short["1-1"].title or "scaffolding" in by_short["1-1"].title.lower()

    def test_canonical_key_map(self, tmp_path: Path) -> None:
        """提供 canonical_key_map 时 story_key 使用映射值。"""
        epics_file = tmp_path / "epics.md"
        epics_file.write_text(_SAMPLE_EPICS_MD, encoding="utf-8")
        key_map = {
            "1-1": "1-1-project-scaffolding",
            "1-2": "1-2-sqlite-state-persistence",
            "2b-5": "2b-5-batch-select-status",
        }
        result = load_epics(epics_file, canonical_key_map=key_map)
        by_short = {s.short_key: s for s in result}
        assert by_short["1-1"].story_key == "1-1-project-scaffolding"
        assert by_short["2b-5"].story_key == "2b-5-batch-select-status"

    def test_without_key_map_falls_back_to_short_key(self, tmp_path: Path) -> None:
        """无 canonical_key_map 时 story_key 退化为 short_key。"""
        epics_file = tmp_path / "epics.md"
        epics_file.write_text(_SAMPLE_EPICS_MD, encoding="utf-8")
        result = load_epics(epics_file)
        by_short = {s.short_key: s for s in result}
        assert by_short["1-1"].story_key == "1-1"

    def test_letter_suffix_story_numbers(self, tmp_path: Path) -> None:
        """Story 编号含字母后缀（如 1.4a）能正确解析。"""
        md = """\
## Epic 1: Test

### Story 1.4a: Preflight Check Engine

As a system, I want checks

### Story 1.4b: Init CLI UX

As a user, I want a CLI
"""
        epics_file = tmp_path / "epics.md"
        epics_file.write_text(md, encoding="utf-8")
        result = load_epics(epics_file)
        short_keys = [s.short_key for s in result]
        assert "1-4a" in short_keys
        assert "1-4b" in short_keys


# ---------------------------------------------------------------------------
# build_canonical_key_map
# ---------------------------------------------------------------------------


_SAMPLE_SPRINT_STATUS = """\
development_status:
  epic-1: in-progress
  1-1-project-scaffolding-dev-toolchain: done
  1-2-sqlite-state-persistence: done
  2b-5-batch-select-status: ready-for-dev
  epic-1-retrospective: optional
"""


class TestBuildCanonicalKeyMap:
    def test_parses_story_keys(self, tmp_path: Path) -> None:
        f = tmp_path / "sprint-status.yaml"
        f.write_text(_SAMPLE_SPRINT_STATUS, encoding="utf-8")
        result = build_canonical_key_map(f)
        assert result["1-1"] == "1-1-project-scaffolding-dev-toolchain"
        assert result["1-2"] == "1-2-sqlite-state-persistence"
        assert result["2b-5"] == "2b-5-batch-select-status"

    def test_excludes_epic_keys(self, tmp_path: Path) -> None:
        f = tmp_path / "sprint-status.yaml"
        f.write_text(_SAMPLE_SPRINT_STATUS, encoding="utf-8")
        result = build_canonical_key_map(f)
        # epic-1, epic-1-retrospective should not produce entries
        assert "epic" not in str(result.values())


# ---------------------------------------------------------------------------
# LocalBatchRecommender
# ---------------------------------------------------------------------------


def _make_epic(
    short_key: str, story_key: str, title: str, deps: list[str] | None = None
) -> EpicInfo:
    return EpicInfo(
        story_key=story_key,
        short_key=short_key,
        title=title,
        epic_key=short_key.split("-")[0],
        dependencies=deps or [],
    )


def _make_story(story_id: str, status: StoryStatus = "backlog", phase: str = "idle") -> StoryRecord:
    return StoryRecord(
        story_id=story_id,
        title="test",
        status=status,
        current_phase=phase,
        created_at=_NOW,
        updated_at=_NOW,
    )


class TestLocalBatchRecommender:
    def test_recommends_stories_with_no_deps(self) -> None:
        epics = [
            _make_epic("1-1", "1-1-scaffolding", "Scaffolding"),
            _make_epic("1-2", "1-2-sqlite", "SQLite", ["1-1"]),
        ]
        recommender = LocalBatchRecommender()
        proposal = recommender.recommend(epics, {}, 5)
        assert len(proposal.stories) == 1
        assert proposal.stories[0].short_key == "1-1"

    def test_includes_satisfied_deps(self) -> None:
        epics = [
            _make_epic("1-1", "1-1-scaffolding", "Scaffolding"),
            _make_epic("1-2", "1-2-sqlite", "SQLite", ["1-1"]),
        ]
        existing = {"1-1-scaffolding": _make_story("1-1-scaffolding", "done")}
        recommender = LocalBatchRecommender()
        proposal = recommender.recommend(epics, existing, 5)
        assert len(proposal.stories) == 1
        assert proposal.stories[0].short_key == "1-2"

    def test_skips_done_stories(self) -> None:
        epics = [_make_epic("1-1", "1-1-scaffolding", "Scaffolding")]
        existing = {"1-1-scaffolding": _make_story("1-1-scaffolding", "done")}
        recommender = LocalBatchRecommender()
        proposal = recommender.recommend(epics, existing, 5)
        assert len(proposal.stories) == 0

    def test_skips_blocked_stories(self) -> None:
        epics = [_make_epic("1-1", "1-1-scaffolding", "Scaffolding")]
        existing = {"1-1-scaffolding": _make_story("1-1-scaffolding", "blocked")}
        recommender = LocalBatchRecommender()
        proposal = recommender.recommend(epics, existing, 5)
        assert len(proposal.stories) == 0

    def test_respects_max_stories(self) -> None:
        epics = [
            _make_epic("1-1", "1-1-a", "A"),
            _make_epic("1-2", "1-2-b", "B"),
            _make_epic("1-3", "1-3-c", "C"),
        ]
        recommender = LocalBatchRecommender()
        proposal = recommender.recommend(epics, {}, 2)
        assert len(proposal.stories) == 2

    def test_skips_in_progress_stories(self) -> None:
        epics = [_make_epic("1-1", "1-1-scaffolding", "Scaffolding")]
        existing = {"1-1-scaffolding": _make_story("1-1-scaffolding", "in_progress", "dev")}
        recommender = LocalBatchRecommender()
        proposal = recommender.recommend(epics, existing, 5)
        assert len(proposal.stories) == 0

    def test_unsatisfied_deps_excluded(self) -> None:
        epics = [
            _make_epic("1-1", "1-1-scaffolding", "Scaffolding"),
            _make_epic("1-2", "1-2-sqlite", "SQLite", ["1-1"]),
        ]
        # 1-1 is backlog, not done — so 1-2 cannot be recommended
        existing = {"1-1-scaffolding": _make_story("1-1-scaffolding", "backlog")}
        recommender = LocalBatchRecommender()
        proposal = recommender.recommend(epics, existing, 5)
        # Should still include 1-1 (no deps, backlog) but not 1-2
        keys = [s.short_key for s in proposal.stories]
        assert "1-1" in keys
        assert "1-2" not in keys

    def test_empty_epics(self) -> None:
        recommender = LocalBatchRecommender()
        proposal = recommender.recommend([], {}, 5)
        assert len(proposal.stories) == 0

    def test_protocol_compliance(self) -> None:
        from ato.batch import BatchRecommender

        assert isinstance(LocalBatchRecommender(), BatchRecommender)


# ---------------------------------------------------------------------------
# confirm_batch — 事务性测试 (Task 4.5)
# ---------------------------------------------------------------------------


class TestConfirmBatch:
    async def test_creates_batch_and_stories(self, initialized_db_path: Path) -> None:
        """confirm_batch 创建 batch 记录和缺失的 stories。"""
        db = await get_connection(initialized_db_path)
        try:
            infos = [
                _make_epic("1-1", "1-1-scaffolding", "Scaffolding"),
                _make_epic("1-2", "1-2-sqlite", "SQLite"),
            ]
            proposal = BatchProposal(stories=infos, reason="test")
            batch, _ = await confirm_batch(db, proposal)

            assert batch.status == "active"

            # 验证 stories 被创建
            s1 = await get_story(db, "1-1-scaffolding")
            assert s1 is not None
            assert s1.status == "planning"
            assert s1.current_phase == "creating"

            s2 = await get_story(db, "1-2-sqlite")
            assert s2 is not None
            assert s2.status == "backlog"
            assert s2.current_phase == "queued"
        finally:
            await db.close()

    async def test_missing_stories_auto_created(self, initialized_db_path: Path) -> None:
        """缺失的 StoryRecord 自动补齐。"""
        db = await get_connection(initialized_db_path)
        try:
            infos = [_make_epic("1-1", "1-1-test", "Test")]
            proposal = BatchProposal(stories=infos)

            # 确认 story 不存在
            assert await get_story(db, "1-1-test") is None

            await confirm_batch(db, proposal)

            # 现在应该存在
            story = await get_story(db, "1-1-test")
            assert story is not None
            assert story.title == "Test"
        finally:
            await db.close()

    async def test_existing_story_not_duplicated(self, initialized_db_path: Path) -> None:
        """已存在的 story 不会重复创建。"""
        from ato.models.db import insert_story

        db = await get_connection(initialized_db_path)
        try:
            # 先手动创建 story
            existing = StoryRecord(
                story_id="1-1-existing",
                title="Existing",
                status="backlog",
                current_phase="idle",
                created_at=_NOW,
                updated_at=_NOW,
            )
            await insert_story(db, existing)

            infos = [_make_epic("1-1", "1-1-existing", "Existing")]
            proposal = BatchProposal(stories=infos)
            await confirm_batch(db, proposal)

            # Story 应该被更新状态但不重复创建
            story = await get_story(db, "1-1-existing")
            assert story is not None
            assert story.status == "planning"
            assert story.current_phase == "creating"
        finally:
            await db.close()

    async def test_rejects_second_active_batch(self, initialized_db_path: Path) -> None:
        """已存在 active batch 时拒绝创建新 batch。"""
        db = await get_connection(initialized_db_path)
        try:
            infos1 = [_make_epic("1-1", "1-1-first", "First")]
            proposal1 = BatchProposal(stories=infos1)
            await confirm_batch(db, proposal1)

            infos2 = [_make_epic("1-2", "1-2-second", "Second")]
            proposal2 = BatchProposal(stories=infos2)
            with pytest.raises(ValueError, match="已存在 active batch"):
                await confirm_batch(db, proposal2)
        finally:
            await db.close()

    async def test_rollback_on_failure(self, initialized_db_path: Path) -> None:
        """失败时整体回滚 — 无残留数据。"""
        db = await get_connection(initialized_db_path)
        try:
            # 先创建一个 active batch 使第二次 confirm 失败
            infos1 = [_make_epic("1-1", "1-1-first", "First")]
            proposal1 = BatchProposal(stories=infos1)
            await confirm_batch(db, proposal1)

            # 第二次 confirm 应该失败且不创建 "1-2-second" story
            infos2 = [_make_epic("1-2", "1-2-second", "Second")]
            proposal2 = BatchProposal(stories=infos2)
            with pytest.raises(ValueError):
                await confirm_batch(db, proposal2)

            # 验证 1-2-second 未被创建
            s = await get_story(db, "1-2-second")
            assert s is None
        finally:
            await db.close()

    async def test_batch_story_links_created(self, initialized_db_path: Path) -> None:
        """batch_stories 关联正确创建。"""
        db = await get_connection(initialized_db_path)
        try:
            infos = [
                _make_epic("1-1", "1-1-a", "A"),
                _make_epic("1-2", "1-2-b", "B"),
            ]
            proposal = BatchProposal(stories=infos)
            batch, _ = await confirm_batch(db, proposal)

            stories = await get_batch_stories(db, batch.batch_id)
            assert len(stories) == 2
            assert stories[0][0].sequence_no == 0
            assert stories[1][0].sequence_no == 1
        finally:
            await db.close()

    async def test_empty_proposal_rejected(self, initialized_db_path: Path) -> None:
        """空 proposal 被拒绝。"""
        db = await get_connection(initialized_db_path)
        try:
            proposal = BatchProposal(stories=[])
            with pytest.raises(ValueError, match="No stories selected"):
                await confirm_batch(db, proposal)
        finally:
            await db.close()

    async def test_selected_indices(self, initialized_db_path: Path) -> None:
        """selected_indices 仅选择部分 stories。"""
        db = await get_connection(initialized_db_path)
        try:
            infos = [
                _make_epic("1-1", "1-1-a", "A"),
                _make_epic("1-2", "1-2-b", "B"),
                _make_epic("1-3", "1-3-c", "C"),
            ]
            proposal = BatchProposal(stories=infos)
            batch, _ = await confirm_batch(db, proposal, selected_indices=[0, 2])

            stories = await get_batch_stories(db, batch.batch_id)
            assert len(stories) == 2
            story_ids = [s[1].story_id for s in stories]
            assert "1-1-a" in story_ids
            assert "1-3-c" in story_ids
            assert "1-2-b" not in story_ids
        finally:
            await db.close()

    async def test_done_story_excluded_from_batch(self, initialized_db_path: Path) -> None:
        """已完成的 story 不进入 batch，仅 actionable stories 有 batch_stories 行。"""
        from ato.models.db import insert_story

        db = await get_connection(initialized_db_path)
        try:
            await insert_story(
                db,
                StoryRecord(
                    story_id="1-1-done",
                    title="Done",
                    status="done",
                    current_phase="completed",
                    created_at=_NOW,
                    updated_at=_NOW,
                ),
            )

            infos = [
                _make_epic("1-1", "1-1-done", "Done"),
                _make_epic("1-2", "1-2-new", "New"),
            ]
            proposal = BatchProposal(stories=infos)
            batch, _ = await confirm_batch(db, proposal)

            # done story 未改变
            s = await get_story(db, "1-1-done")
            assert s is not None
            assert s.status == "done"

            # batch 只包含 actionable story
            links = await get_batch_stories(db, batch.batch_id)
            assert len(links) == 1
            assert links[0][1].story_id == "1-2-new"
            assert links[0][0].sequence_no == 0

            # 1-2 is seq=0 → creating
            assert links[0][1].status == "planning"
            assert links[0][1].current_phase == "creating"
        finally:
            await db.close()

    async def test_in_progress_story_excluded_from_batch(self, initialized_db_path: Path) -> None:
        """进行中的 story 不进入 batch。"""
        from ato.models.db import insert_story

        db = await get_connection(initialized_db_path)
        try:
            await insert_story(
                db,
                StoryRecord(
                    story_id="1-1-active",
                    title="Active",
                    status="in_progress",
                    current_phase="dev",
                    created_at=_NOW,
                    updated_at=_NOW,
                ),
            )

            infos = [
                _make_epic("1-1", "1-1-active", "Active"),
                _make_epic("1-2", "1-2-new", "New"),
            ]
            proposal = BatchProposal(stories=infos)
            batch, _ = await confirm_batch(db, proposal)

            # batch 只包含 1-2
            links = await get_batch_stories(db, batch.batch_id)
            assert len(links) == 1
            assert links[0][1].story_id == "1-2-new"

            # 1-1 未被改变
            s = await get_story(db, "1-1-active")
            assert s is not None
            assert s.status == "in_progress"
        finally:
            await db.close()

    async def test_all_immutable_stories_rejected(self, initialized_db_path: Path) -> None:
        """所有 stories 均不可回退时拒绝创建 batch。"""
        from ato.models.db import insert_story

        db = await get_connection(initialized_db_path)
        try:
            await insert_story(
                db,
                StoryRecord(
                    story_id="1-1-done",
                    title="Done",
                    status="done",
                    current_phase="completed",
                    created_at=_NOW,
                    updated_at=_NOW,
                ),
            )
            await insert_story(
                db,
                StoryRecord(
                    story_id="1-2-blocked",
                    title="Blocked",
                    status="blocked",
                    current_phase="blocked",
                    created_at=_NOW,
                    updated_at=_NOW,
                ),
            )

            infos = [
                _make_epic("1-1", "1-1-done", "Done"),
                _make_epic("1-2", "1-2-blocked", "Blocked"),
            ]
            proposal = BatchProposal(stories=infos)
            with pytest.raises(ValueError, match="不可回退状态"):
                await confirm_batch(db, proposal)
        finally:
            await db.close()

    async def test_immutable_excluded_sequence_contiguous(self, initialized_db_path: Path) -> None:
        """不可回退 story 不进 batch，sequence_no 连续无间隙。"""
        from ato.models.db import insert_story

        db = await get_connection(initialized_db_path)
        try:
            await insert_story(
                db,
                StoryRecord(
                    story_id="1-1-done",
                    title="Done",
                    status="done",
                    current_phase="completed",
                    created_at=_NOW,
                    updated_at=_NOW,
                ),
            )
            await insert_story(
                db,
                StoryRecord(
                    story_id="1-2-backlog",
                    title="Backlog",
                    status="backlog",
                    current_phase="idle",
                    created_at=_NOW,
                    updated_at=_NOW,
                ),
            )

            infos = [
                _make_epic("1-1", "1-1-done", "Done"),
                _make_epic("1-2", "1-2-backlog", "Backlog"),
                _make_epic("1-3", "1-3-new", "New"),
            ]
            proposal = BatchProposal(stories=infos)
            batch, _ = await confirm_batch(db, proposal)

            # 1-1 excluded, batch only has 1-2 and 1-3
            links = await get_batch_stories(db, batch.batch_id)
            assert len(links) == 2
            # sequence_no is contiguous: 0, 1
            assert links[0][0].sequence_no == 0
            assert links[1][0].sequence_no == 1
            # 1-2 is seq=0 → creating
            assert links[0][1].story_id == "1-2-backlog"
            assert links[0][1].status == "planning"
            assert links[0][1].current_phase == "creating"
            # 1-3 is seq=1 → queued
            assert links[1][1].story_id == "1-3-new"
            assert links[1][1].status == "backlog"
            assert links[1][1].current_phase == "queued"
        finally:
            await db.close()
