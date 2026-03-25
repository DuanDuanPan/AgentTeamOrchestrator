"""test_db — 数据库初始化与 CRUD 测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import pytest

from ato.models.db import (
    count_findings_by_severity,
    get_active_batch,
    get_batch_progress,
    get_batch_stories,
    get_connection,
    get_findings_by_story,
    get_open_findings,
    get_pending_approvals,
    get_story,
    get_tasks_by_story,
    init_db,
    insert_approval,
    insert_batch,
    insert_batch_story_links,
    insert_finding,
    insert_findings_batch,
    insert_story,
    insert_task,
    update_finding_status,
    update_story_status,
    update_task_status,
)
from ato.models.schemas import (
    SCHEMA_VERSION,
    ApprovalRecord,
    BatchRecord,
    BatchStatus,
    BatchStoryLink,
    FindingRecord,
    FindingSeverity,
    FindingStatus,
    StoryRecord,
    StoryStatus,
    TaskRecord,
    compute_dedup_hash,
)

_NOW = datetime.now(tz=UTC)


# ---------------------------------------------------------------------------
# init_db
# ---------------------------------------------------------------------------


class TestInitDb:
    async def test_creates_database_file(self, db_path: Path) -> None:
        await init_db(db_path)
        assert db_path.exists()

    async def test_creates_parent_directories(self, tmp_path: Path) -> None:
        db_path = tmp_path / "deep" / "nested" / "state.db"
        await init_db(db_path)
        assert db_path.exists()

    async def test_sets_wal_mode(self, initialized_db_path: Path) -> None:
        async with aiosqlite.connect(initialized_db_path) as db:
            cursor = await db.execute("PRAGMA journal_mode")
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == "wal"

    async def test_sets_user_version(self, initialized_db_path: Path) -> None:
        async with aiosqlite.connect(initialized_db_path) as db:
            cursor = await db.execute("PRAGMA user_version")
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == SCHEMA_VERSION

    async def test_creates_stories_table(self, initialized_db_path: Path) -> None:
        async with aiosqlite.connect(initialized_db_path) as db:
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='stories'"
            )
            assert await cursor.fetchone() is not None

    async def test_creates_tasks_table(self, initialized_db_path: Path) -> None:
        async with aiosqlite.connect(initialized_db_path) as db:
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='tasks'"
            )
            assert await cursor.fetchone() is not None

    async def test_creates_approvals_table(self, initialized_db_path: Path) -> None:
        async with aiosqlite.connect(initialized_db_path) as db:
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='approvals'"
            )
            assert await cursor.fetchone() is not None

    async def test_idempotent(self, db_path: Path) -> None:
        """多次调用 init_db 不报错。"""
        await init_db(db_path)
        await init_db(db_path)
        assert db_path.exists()


# ---------------------------------------------------------------------------
# get_connection
# ---------------------------------------------------------------------------


class TestGetConnection:
    async def test_returns_connection(self, initialized_db_path: Path) -> None:
        db = await get_connection(initialized_db_path)
        try:
            assert db is not None
        finally:
            await db.close()

    async def test_sets_busy_timeout(self, initialized_db_path: Path) -> None:
        db = await get_connection(initialized_db_path)
        try:
            cursor = await db.execute("PRAGMA busy_timeout")
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == 5000
        finally:
            await db.close()

    async def test_sets_synchronous_normal(self, initialized_db_path: Path) -> None:
        db = await get_connection(initialized_db_path)
        try:
            cursor = await db.execute("PRAGMA synchronous")
            row = await cursor.fetchone()
            assert row is not None
            # NORMAL = 1
            assert row[0] == 1
        finally:
            await db.close()

    async def test_enables_foreign_keys(self, initialized_db_path: Path) -> None:
        db = await get_connection(initialized_db_path)
        try:
            cursor = await db.execute("PRAGMA foreign_keys")
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == 1
        finally:
            await db.close()

    async def test_confirms_wal_mode(self, initialized_db_path: Path) -> None:
        db = await get_connection(initialized_db_path)
        try:
            cursor = await db.execute("PRAGMA journal_mode")
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == "wal"
        finally:
            await db.close()

    async def test_row_factory_returns_dict_like(self, initialized_db_path: Path) -> None:
        """验证 row_factory 设置后可以按列名访问。"""
        db = await get_connection(initialized_db_path)
        try:
            story = _make_story("s1")
            await insert_story(db, story)
            cursor = await db.execute("SELECT * FROM stories WHERE story_id = ?", ("s1",))
            row = await cursor.fetchone()
            assert row is not None
            assert row["story_id"] == "s1"
        finally:
            await db.close()


# ---------------------------------------------------------------------------
# CRUD — Story round-trip
# ---------------------------------------------------------------------------


class TestStoryCrud:
    async def test_insert_and_get(self, initialized_db_path: Path) -> None:
        db = await get_connection(initialized_db_path)
        try:
            story = _make_story("story-001")
            await insert_story(db, story)
            result = await get_story(db, "story-001")
            assert result is not None
            assert result.story_id == "story-001"
            assert result.title == "测试 story"
            assert result.status == "in_progress"
        finally:
            await db.close()

    async def test_get_nonexistent_returns_none(self, initialized_db_path: Path) -> None:
        db = await get_connection(initialized_db_path)
        try:
            result = await get_story(db, "nonexistent")
            assert result is None
        finally:
            await db.close()

    async def test_update_status(self, initialized_db_path: Path) -> None:
        db = await get_connection(initialized_db_path)
        try:
            story = _make_story("story-002")
            await insert_story(db, story)
            await update_story_status(db, "story-002", "review", "review_phase")
            result = await get_story(db, "story-002")
            assert result is not None
            assert result.status == "review"
            assert result.current_phase == "review_phase"
            # updated_at 应该被更新
            assert result.updated_at >= story.updated_at
        finally:
            await db.close()

    async def test_datetime_roundtrip(self, initialized_db_path: Path) -> None:
        """验证 datetime 存储为 ISO 8601 后 round-trip 仍通过 model_validate。"""
        db = await get_connection(initialized_db_path)
        try:
            story = _make_story("story-dt")
            await insert_story(db, story)
            result = await get_story(db, "story-dt")
            assert result is not None
            assert isinstance(result.created_at, datetime)
            assert isinstance(result.updated_at, datetime)
        finally:
            await db.close()


# ---------------------------------------------------------------------------
# CRUD — Task round-trip
# ---------------------------------------------------------------------------


class TestTaskCrud:
    async def test_insert_and_get(self, initialized_db_path: Path) -> None:
        db = await get_connection(initialized_db_path)
        try:
            story = _make_story("s1")
            await insert_story(db, story)
            task = _make_task("t1", "s1")
            await insert_task(db, task)
            results = await get_tasks_by_story(db, "s1")
            assert len(results) == 1
            assert results[0].task_id == "t1"
            assert results[0].cli_tool == "claude"
        finally:
            await db.close()

    async def test_update_status_with_kwargs(self, initialized_db_path: Path) -> None:
        db = await get_connection(initialized_db_path)
        try:
            story = _make_story("s2")
            await insert_story(db, story)
            task = _make_task("t2", "s2")
            await insert_task(db, task)
            await update_task_status(db, "t2", "running", pid=12345)
            results = await get_tasks_by_story(db, "s2")
            assert results[0].status == "running"
            assert results[0].pid == 12345
        finally:
            await db.close()

    async def test_update_task_rejects_unknown_field(self, initialized_db_path: Path) -> None:
        db = await get_connection(initialized_db_path)
        try:
            story = _make_story("s3")
            await insert_story(db, story)
            task = _make_task("t3", "s3")
            await insert_task(db, task)
            with pytest.raises(ValueError, match="does not support field"):
                await update_task_status(db, "t3", "running", bad_field="x")
        finally:
            await db.close()

    async def test_recovery_fields_roundtrip(self, initialized_db_path: Path) -> None:
        """验证恢复关键字段 pid, expected_artifact, status 能正确往返。"""
        db = await get_connection(initialized_db_path)
        try:
            story = _make_story("s-rec")
            await insert_story(db, story)
            task = _make_task("t-rec", "s-rec", pid=9999, expected_artifact="/out/report.md")
            await insert_task(db, task)
            results = await get_tasks_by_story(db, "s-rec")
            assert results[0].pid == 9999
            assert results[0].expected_artifact == "/out/report.md"
            assert results[0].status == "pending"
        finally:
            await db.close()


# ---------------------------------------------------------------------------
# CRUD — Approval round-trip
# ---------------------------------------------------------------------------


class TestApprovalCrud:
    async def test_insert_and_get_pending(self, initialized_db_path: Path) -> None:
        db = await get_connection(initialized_db_path)
        try:
            story = _make_story("s-appr")
            await insert_story(db, story)
            approval = _make_approval("a1", "s-appr")
            await insert_approval(db, approval)
            results = await get_pending_approvals(db)
            assert len(results) == 1
            assert results[0].approval_id == "a1"
        finally:
            await db.close()

    async def test_approved_not_in_pending(self, initialized_db_path: Path) -> None:
        db = await get_connection(initialized_db_path)
        try:
            story = _make_story("s-appr2")
            await insert_story(db, story)
            approval = ApprovalRecord(
                approval_id="a2",
                story_id="s-appr2",
                approval_type="gate",
                status="approved",
                created_at=_NOW,
            )
            await insert_approval(db, approval)
            results = await get_pending_approvals(db)
            assert len(results) == 0
        finally:
            await db.close()


# ---------------------------------------------------------------------------
# SQL 注入行为测试 (AC5)
# ---------------------------------------------------------------------------


class TestSqlInjectionSafety:
    async def test_story_with_sql_keywords_in_title(self, initialized_db_path: Path) -> None:
        """包含引号/SQL 关键字的输入被当作普通值保存。"""
        db = await get_connection(initialized_db_path)
        try:
            malicious_title = "'; DROP TABLE stories; --"
            story = StoryRecord(
                story_id="evil-1",
                title=malicious_title,
                status="backlog",
                current_phase="planning",
                created_at=_NOW,
                updated_at=_NOW,
            )
            await insert_story(db, story)
            result = await get_story(db, "evil-1")
            assert result is not None
            assert result.title == malicious_title

            # 表未被破坏
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='stories'"
            )
            assert await cursor.fetchone() is not None
        finally:
            await db.close()


# ---------------------------------------------------------------------------
# 外键约束测试 (AC5)
# ---------------------------------------------------------------------------


class TestForeignKeyConstraints:
    async def test_orphan_task_insert_fails(self, initialized_db_path: Path) -> None:
        """无对应 story 的 task 插入因外键约束失败。"""
        db = await get_connection(initialized_db_path)
        try:
            task = _make_task("orphan-t", "nonexistent-story")
            with pytest.raises(aiosqlite.IntegrityError):
                await insert_task(db, task)
        finally:
            await db.close()

    async def test_orphan_approval_insert_fails(self, initialized_db_path: Path) -> None:
        """无对应 story 的 approval 插入因外键约束失败。"""
        db = await get_connection(initialized_db_path)
        try:
            approval = _make_approval("orphan-a", "nonexistent-story")
            with pytest.raises(aiosqlite.IntegrityError):
                await insert_approval(db, approval)
        finally:
            await db.close()


# ---------------------------------------------------------------------------
# Patch 2: update 接口写前校验 (AC4)
# ---------------------------------------------------------------------------


class TestUpdateValidation:
    async def test_update_story_status_rejects_invalid_status(
        self, initialized_db_path: Path
    ) -> None:
        """update_story_status 拒绝非法 status 值，不会写入脏数据。"""
        from pydantic import ValidationError

        db = await get_connection(initialized_db_path)
        try:
            story = _make_story("s-val1")
            await insert_story(db, story)
            with pytest.raises(ValidationError):
                await update_story_status(db, "s-val1", "not_a_real_status", "dev")
            # 验证原始数据未被破坏
            result = await get_story(db, "s-val1")
            assert result is not None
            assert result.status == "in_progress"
        finally:
            await db.close()

    async def test_update_task_status_rejects_invalid_status(
        self, initialized_db_path: Path
    ) -> None:
        """update_task_status 拒绝非法 status 值。"""
        from pydantic import ValidationError

        db = await get_connection(initialized_db_path)
        try:
            story = _make_story("s-val2")
            await insert_story(db, story)
            task = _make_task("t-val2", "s-val2")
            await insert_task(db, task)
            with pytest.raises(ValidationError):
                await update_task_status(db, "t-val2", "not_a_real_status")
            # 验证原始数据未被破坏
            results = await get_tasks_by_story(db, "s-val2")
            assert results[0].status == "pending"
        finally:
            await db.close()

    async def test_update_task_rejects_string_for_datetime_kwarg(
        self, initialized_db_path: Path
    ) -> None:
        """update_task_status 拒绝 completed_at='not-a-date' 等非法 datetime 值。"""
        db = await get_connection(initialized_db_path)
        try:
            story = _make_story("s-val3")
            await insert_story(db, story)
            task = _make_task("t-val3", "s-val3")
            await insert_task(db, task)
            with pytest.raises(TypeError, match="must be datetime or None"):
                await update_task_status(db, "t-val3", "running", completed_at="not-a-date")
        finally:
            await db.close()

    async def test_update_story_status_rejects_non_str_phase(
        self, initialized_db_path: Path
    ) -> None:
        """update_story_status 拒绝 phase=123 等非 str 值。"""
        db = await get_connection(initialized_db_path)
        try:
            story = _make_story("s-val-phase")
            await insert_story(db, story)
            with pytest.raises(TypeError, match="phase must be str"):
                await update_story_status(db, "s-val-phase", "review", 123)  # type: ignore[arg-type]
            result = await get_story(db, "s-val-phase")
            assert result is not None
            assert result.current_phase == "dev"
        finally:
            await db.close()

    async def test_update_task_rejects_string_for_pid(self, initialized_db_path: Path) -> None:
        """update_task_status 拒绝 pid='42'。"""
        db = await get_connection(initialized_db_path)
        try:
            story = _make_story("s-val-pid")
            await insert_story(db, story)
            task = _make_task("t-val-pid", "s-val-pid")
            await insert_task(db, task)
            with pytest.raises(TypeError, match="pid must be int or None"):
                await update_task_status(db, "t-val-pid", "running", pid="42")
        finally:
            await db.close()

    async def test_update_task_rejects_string_for_cost_usd(self, initialized_db_path: Path) -> None:
        """update_task_status 拒绝 cost_usd='1.5'。"""
        db = await get_connection(initialized_db_path)
        try:
            story = _make_story("s-val-cost")
            await insert_story(db, story)
            task = _make_task("t-val-cost", "s-val-cost")
            await insert_task(db, task)
            with pytest.raises(TypeError, match="cost_usd must be int/float or None"):
                await update_task_status(db, "t-val-cost", "running", cost_usd="1.5")
        finally:
            await db.close()

    async def test_update_task_rejects_bool_for_pid(self, initialized_db_path: Path) -> None:
        """update_task_status 拒绝 pid=True（bool 是 int 子类，需显式排除）。"""
        db = await get_connection(initialized_db_path)
        try:
            story = _make_story("s-val-bpid")
            await insert_story(db, story)
            task = _make_task("t-val-bpid", "s-val-bpid")
            await insert_task(db, task)
            with pytest.raises(TypeError, match="must not be bool"):
                await update_task_status(db, "t-val-bpid", "running", pid=True)
        finally:
            await db.close()

    async def test_update_task_rejects_bool_for_cost_usd(self, initialized_db_path: Path) -> None:
        """update_task_status 拒绝 cost_usd=False（bool 是 int 子类，需显式排除）。"""
        db = await get_connection(initialized_db_path)
        try:
            story = _make_story("s-val-bcost")
            await insert_story(db, story)
            task = _make_task("t-val-bcost", "s-val-bcost")
            await insert_task(db, task)
            with pytest.raises(TypeError, match="must not be bool"):
                await update_task_status(db, "t-val-bcost", "running", cost_usd=False)
        finally:
            await db.close()

    async def test_update_task_rejects_int_for_expected_artifact(
        self, initialized_db_path: Path
    ) -> None:
        """update_task_status 拒绝 expected_artifact=123。"""
        db = await get_connection(initialized_db_path)
        try:
            story = _make_story("s-val-art")
            await insert_story(db, story)
            task = _make_task("t-val-art", "s-val-art")
            await insert_task(db, task)
            with pytest.raises(TypeError, match="expected_artifact must be str or None"):
                await update_task_status(db, "t-val-art", "running", expected_artifact=123)
        finally:
            await db.close()

    async def test_update_task_accepts_valid_typed_kwargs(self, initialized_db_path: Path) -> None:
        """合法类型的 kwargs 仍然可以正常更新。"""
        db = await get_connection(initialized_db_path)
        try:
            story = _make_story("s-val-ok")
            await insert_story(db, story)
            task = _make_task("t-val-ok", "s-val-ok")
            await insert_task(db, task)
            await update_task_status(
                db,
                "t-val-ok",
                "running",
                pid=999,
                cost_usd=0.05,
                exit_code=None,
                expected_artifact="/out/file.json",
                error_message=None,
            )
            results = await get_tasks_by_story(db, "s-val-ok")
            assert results[0].pid == 999
            assert results[0].cost_usd == 0.05
            assert results[0].expected_artifact == "/out/file.json"
        finally:
            await db.close()


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _make_story(
    story_id: str,
    status: StoryStatus = "in_progress",
) -> StoryRecord:
    return StoryRecord(
        story_id=story_id,
        title="测试 story",
        status=status,
        current_phase="dev",
        created_at=_NOW,
        updated_at=_NOW,
    )


def _make_task(
    task_id: str,
    story_id: str,
    pid: int | None = None,
    expected_artifact: str | None = None,
) -> TaskRecord:
    return TaskRecord(
        task_id=task_id,
        story_id=story_id,
        phase="dev",
        role="developer",
        cli_tool="claude",
        status="pending",
        pid=pid,
        expected_artifact=expected_artifact,
    )


def _make_approval(approval_id: str, story_id: str) -> ApprovalRecord:
    return ApprovalRecord(
        approval_id=approval_id,
        story_id=story_id,
        approval_type="gate",
        status="pending",
        created_at=_NOW,
    )


def _make_batch(batch_id: str, status: BatchStatus = "active") -> BatchRecord:
    return BatchRecord(
        batch_id=batch_id,
        status=status,
        created_at=_NOW,
    )


def _make_batch_link(batch_id: str, story_id: str, seq: int) -> BatchStoryLink:
    return BatchStoryLink(batch_id=batch_id, story_id=story_id, sequence_no=seq)


# ---------------------------------------------------------------------------
# CRUD — Batch round-trip (Story 2B.5)
# ---------------------------------------------------------------------------


class TestBatchCrud:
    async def test_insert_and_get_active(self, initialized_db_path: Path) -> None:
        db = await get_connection(initialized_db_path)
        try:
            batch = _make_batch("batch-001")
            await insert_batch(db, batch)
            result = await get_active_batch(db)
            assert result is not None
            assert result.batch_id == "batch-001"
            assert result.status == "active"
        finally:
            await db.close()

    async def test_get_active_returns_none_when_empty(self, initialized_db_path: Path) -> None:
        db = await get_connection(initialized_db_path)
        try:
            result = await get_active_batch(db)
            assert result is None
        finally:
            await db.close()

    async def test_single_active_batch_constraint(self, initialized_db_path: Path) -> None:
        """同一时间仅允许 1 个 active batch。"""
        import aiosqlite as aiosqlite_mod

        db = await get_connection(initialized_db_path)
        try:
            batch1 = _make_batch("batch-001")
            await insert_batch(db, batch1)
            batch2 = _make_batch("batch-002")
            with pytest.raises(aiosqlite_mod.IntegrityError):
                await insert_batch(db, batch2)
        finally:
            await db.close()

    async def test_completed_batch_allows_new_active(self, initialized_db_path: Path) -> None:
        """已完成的 batch 不阻止新 active batch。"""
        db = await get_connection(initialized_db_path)
        try:
            batch1 = _make_batch("batch-old", status="completed")
            await insert_batch(db, batch1)
            batch2 = _make_batch("batch-new", status="active")
            await insert_batch(db, batch2)
            result = await get_active_batch(db)
            assert result is not None
            assert result.batch_id == "batch-new"
        finally:
            await db.close()

    async def test_batch_datetime_roundtrip(self, initialized_db_path: Path) -> None:
        db = await get_connection(initialized_db_path)
        try:
            batch = _make_batch("batch-dt")
            await insert_batch(db, batch)
            result = await get_active_batch(db)
            assert result is not None
            assert isinstance(result.created_at, datetime)
        finally:
            await db.close()


class TestBatchStoryLinkCrud:
    async def test_insert_and_get_stories(self, initialized_db_path: Path) -> None:
        db = await get_connection(initialized_db_path)
        try:
            # 先创建 story 和 batch
            await insert_story(db, _make_story("s1"))
            await insert_story(db, _make_story("s2"))
            batch = _make_batch("b1")
            await insert_batch(db, batch)

            links = [
                _make_batch_link("b1", "s1", 0),
                _make_batch_link("b1", "s2", 1),
            ]
            await insert_batch_story_links(db, links)

            results = await get_batch_stories(db, "b1")
            assert len(results) == 2
            assert results[0][0].sequence_no == 0
            assert results[1][0].sequence_no == 1
            assert results[0][1].story_id == "s1"
            assert results[1][1].story_id == "s2"
        finally:
            await db.close()

    async def test_sequence_order_preserved(self, initialized_db_path: Path) -> None:
        """batch_stories 按 sequence_no 排序返回。"""
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("s-a"))
            await insert_story(db, _make_story("s-b"))
            await insert_story(db, _make_story("s-c"))
            batch = _make_batch("b-seq")
            await insert_batch(db, batch)

            links = [
                _make_batch_link("b-seq", "s-c", 0),
                _make_batch_link("b-seq", "s-a", 1),
                _make_batch_link("b-seq", "s-b", 2),
            ]
            await insert_batch_story_links(db, links)

            results = await get_batch_stories(db, "b-seq")
            story_ids = [r[1].story_id for r in results]
            assert story_ids == ["s-c", "s-a", "s-b"]
        finally:
            await db.close()

    async def test_foreign_key_batch(self, initialized_db_path: Path) -> None:
        """batch_stories 外键约束：无对应 batch 插入失败。"""
        import aiosqlite as aiosqlite_mod

        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("s-fk"))
            links = [_make_batch_link("nonexistent-batch", "s-fk", 0)]
            with pytest.raises(aiosqlite_mod.IntegrityError):
                await insert_batch_story_links(db, links)
        finally:
            await db.close()

    async def test_foreign_key_story(self, initialized_db_path: Path) -> None:
        """batch_stories 外键约束：无对应 story 插入失败。"""
        import aiosqlite as aiosqlite_mod

        db = await get_connection(initialized_db_path)
        try:
            batch = _make_batch("b-fk")
            await insert_batch(db, batch)
            links = [_make_batch_link("b-fk", "nonexistent-story", 0)]
            with pytest.raises(aiosqlite_mod.IntegrityError):
                await insert_batch_story_links(db, links)
        finally:
            await db.close()


class TestBatchProgress:
    async def test_progress_aggregation(self, initialized_db_path: Path) -> None:
        """AC2: 进度分类规则。"""
        db = await get_connection(initialized_db_path)
        try:
            # 创建不同状态的 stories
            await insert_story(db, _make_story("s-done", status="done"))
            await insert_story(db, _make_story("s-blocked", status="blocked"))
            await insert_story(
                db,
                StoryRecord(
                    story_id="s-queued",
                    title="t",
                    status="backlog",
                    current_phase="queued",
                    created_at=_NOW,
                    updated_at=_NOW,
                ),
            )
            await insert_story(
                db,
                StoryRecord(
                    story_id="s-active",
                    title="t",
                    status="planning",
                    current_phase="creating",
                    created_at=_NOW,
                    updated_at=_NOW,
                ),
            )
            await insert_story(db, _make_story("s-ready", status="ready"))

            batch = _make_batch("b-prog")
            await insert_batch(db, batch)
            links = [
                _make_batch_link("b-prog", "s-done", 0),
                _make_batch_link("b-prog", "s-blocked", 1),
                _make_batch_link("b-prog", "s-queued", 2),
                _make_batch_link("b-prog", "s-active", 3),
                _make_batch_link("b-prog", "s-ready", 4),
            ]
            await insert_batch_story_links(db, links)

            progress = await get_batch_progress(db, "b-prog")
            assert progress.done == 1
            assert progress.failed == 1
            assert progress.pending == 2  # queued + ready
            assert progress.active == 1  # planning/creating
            assert progress.total == 5
        finally:
            await db.close()

    async def test_empty_batch_progress(self, initialized_db_path: Path) -> None:
        db = await get_connection(initialized_db_path)
        try:
            batch = _make_batch("b-empty")
            await insert_batch(db, batch)
            progress = await get_batch_progress(db, "b-empty")
            assert progress.total == 0
        finally:
            await db.close()


# ---------------------------------------------------------------------------
# Findings 辅助函数
# ---------------------------------------------------------------------------


def _make_finding(
    finding_id: str,
    story_id: str,
    *,
    severity: FindingSeverity = "blocking",
    status: FindingStatus = "open",
    round_num: int = 1,
) -> FindingRecord:
    return FindingRecord(
        finding_id=finding_id,
        story_id=story_id,
        round_num=round_num,
        severity=severity,
        description=f"Finding {finding_id}",
        status=status,
        file_path="src/ato/core.py",
        rule_id="E001",
        dedup_hash=compute_dedup_hash("src/ato/core.py", "E001", severity, f"Finding {finding_id}"),
        created_at=_NOW,
    )


# ---------------------------------------------------------------------------
# CRUD — Findings round-trip (Story 3.1)
# ---------------------------------------------------------------------------


class TestFindingCrud:
    async def test_insert_finding(self, initialized_db_path: Path) -> None:
        """插入后可查询。"""
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("s-f1"))
            finding = _make_finding("f-001", "s-f1")
            await insert_finding(db, finding)
            results = await get_findings_by_story(db, "s-f1")
            assert len(results) == 1
            assert results[0].finding_id == "f-001"
            assert results[0].severity == "blocking"
        finally:
            await db.close()

    async def test_insert_findings_batch(self, initialized_db_path: Path) -> None:
        """批量插入 N 条，查询验证数量。"""
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("s-fb"))
            findings = [_make_finding(f"f-b-{i}", "s-fb") for i in range(5)]
            await insert_findings_batch(db, findings)
            results = await get_findings_by_story(db, "s-fb")
            assert len(results) == 5
        finally:
            await db.close()

    async def test_insert_findings_batch_atomic_on_failure(self, initialized_db_path: Path) -> None:
        """批量插入含重复 ID 时整批回滚，不留半成品。"""
        import aiosqlite as aiosqlite_mod

        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("s-fba"))
            # 第一条正常，第二条与第一条 finding_id 重复
            findings = [
                _make_finding("f-dup", "s-fba"),
                _make_finding("f-dup", "s-fba"),  # 重复 PK
            ]
            with pytest.raises(aiosqlite_mod.IntegrityError):
                await insert_findings_batch(db, findings)
            # 确认整批回滚：0 条被持久化
            results = await get_findings_by_story(db, "s-fba")
            assert len(results) == 0
        finally:
            await db.close()

    async def test_get_findings_by_story_with_round(self, initialized_db_path: Path) -> None:
        """round_num 过滤。"""
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("s-fr"))
            await insert_finding(db, _make_finding("f-r1", "s-fr", round_num=1))
            await insert_finding(db, _make_finding("f-r2", "s-fr", round_num=2))
            await insert_finding(db, _make_finding("f-r3", "s-fr", round_num=1))
            # 仅查 round 1
            results = await get_findings_by_story(db, "s-fr", round_num=1)
            assert len(results) == 2
            # 查 round 2
            results = await get_findings_by_story(db, "s-fr", round_num=2)
            assert len(results) == 1
        finally:
            await db.close()

    async def test_get_open_findings(self, initialized_db_path: Path) -> None:
        """仅返回 open/still_open。"""
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("s-fo"))
            await insert_finding(db, _make_finding("f-o1", "s-fo", status="open"))
            await insert_finding(db, _make_finding("f-o2", "s-fo", status="closed"))
            await insert_finding(db, _make_finding("f-o3", "s-fo", status="still_open"))
            results = await get_open_findings(db, "s-fo")
            assert len(results) == 2
            ids = {r.finding_id for r in results}
            assert ids == {"f-o1", "f-o3"}
        finally:
            await db.close()

    async def test_update_finding_status(self, initialized_db_path: Path) -> None:
        """open → closed。"""
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("s-fu"))
            await insert_finding(db, _make_finding("f-u1", "s-fu"))
            await update_finding_status(db, "f-u1", "closed")
            results = await get_findings_by_story(db, "s-fu")
            assert results[0].status == "closed"
        finally:
            await db.close()

    async def test_update_finding_status_not_found(self, initialized_db_path: Path) -> None:
        """更新不存在的 finding 抛 ValueError。"""
        db = await get_connection(initialized_db_path)
        try:
            with pytest.raises(ValueError, match="not found"):
                await update_finding_status(db, "nonexistent", "closed")
        finally:
            await db.close()

    async def test_count_findings_by_severity(self, initialized_db_path: Path) -> None:
        """blocking/suggestion 计数正确。"""
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("s-fc"))
            # 3 blocking + 2 suggestion (round 1)
            for i in range(3):
                await insert_finding(db, _make_finding(f"f-c-b-{i}", "s-fc", severity="blocking"))
            for i in range(2):
                await insert_finding(db, _make_finding(f"f-c-s-{i}", "s-fc", severity="suggestion"))
            counts = await count_findings_by_severity(db, "s-fc", 1)
            assert counts["blocking"] == 3
            assert counts["suggestion"] == 2
        finally:
            await db.close()

    async def test_count_findings_empty(self, initialized_db_path: Path) -> None:
        """无 findings 时返回 0/0。"""
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("s-ce"))
            counts = await count_findings_by_severity(db, "s-ce", 1)
            assert counts["blocking"] == 0
            assert counts["suggestion"] == 0
        finally:
            await db.close()

    async def test_finding_datetime_roundtrip(self, initialized_db_path: Path) -> None:
        """datetime 存储 ISO 8601 后 round-trip 仍可 model_validate。"""
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("s-fdt"))
            await insert_finding(db, _make_finding("f-dt", "s-fdt"))
            results = await get_findings_by_story(db, "s-fdt")
            assert isinstance(results[0].created_at, datetime)
        finally:
            await db.close()
