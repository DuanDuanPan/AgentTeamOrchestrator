"""TUI Textual pilot 集成测试。

ATOApp + mock SQLite 数据，验证 TUI 启动、数据加载、轮询刷新、
写入 + nudge 等核心行为。不启动真实 Orchestrator。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from ato.models.db import get_connection, init_db
from ato.tui.app import ATOApp
from ato.tui.dashboard import DashboardScreen


@pytest.fixture()
async def tui_db_path(tmp_path: Path) -> Path:
    """返回已初始化的临时数据库路径。"""
    db_path = tmp_path / ".ato" / "state.db"
    await init_db(db_path)
    return db_path


# ---------------------------------------------------------------------------
# Task 1: ATOApp 骨架
# ---------------------------------------------------------------------------


async def test_app_starts_and_renders(tui_db_path: Path) -> None:
    """AC1: ATOApp 启动后渲染 Header + DashboardScreen + Footer。"""
    app = ATOApp(db_path=tui_db_path)
    async with app.run_test():
        assert app.query_one("Header") is not None
        assert app.query_one("Footer") is not None
        assert app.query_one(DashboardScreen) is not None


async def test_app_title(tui_db_path: Path) -> None:
    """ATOApp title 正确设置。"""
    app = ATOApp(db_path=tui_db_path)
    async with app.run_test():
        assert app.title == "Agent Team Orchestrator"


async def test_app_stores_db_path(tui_db_path: Path) -> None:
    """ATOApp.__init__ 存储 db_path 但不在 __init__ 中读 SQLite。"""
    app = ATOApp(db_path=tui_db_path)
    assert app._db_path == tui_db_path


async def test_app_loads_data_on_mount_empty_db(tui_db_path: Path) -> None:
    """AC1: on_mount 加载空数据库，所有计数为 0。"""
    app = ATOApp(db_path=tui_db_path)
    async with app.run_test():
        assert app.story_count == 0
        assert app.pending_approvals == 0
        assert app.today_cost_usd == 0.0
        assert app.last_updated != ""


async def test_app_loads_data_on_mount_with_stories(tui_db_path: Path) -> None:
    """AC1: on_mount 加载有数据的数据库，计数正确反映。"""
    # 插入测试数据
    db = await get_connection(tui_db_path)
    try:
        now = datetime.now(tz=UTC).isoformat()
        await db.execute(
            "INSERT INTO stories (story_id, title, status, current_phase, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("s1", "Test Story 1", "in_progress", "developing", now, now),
        )
        await db.execute(
            "INSERT INTO stories (story_id, title, status, current_phase, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("s2", "Test Story 2", "ready", "queued", now, now),
        )
        await db.execute(
            "INSERT INTO approvals (approval_id, story_id, approval_type, status, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("a1", "s1", "code_review", "pending", now),
        )
        await db.commit()
    finally:
        await db.close()

    app = ATOApp(db_path=tui_db_path)
    async with app.run_test():
        assert app.story_count == 2
        assert app.pending_approvals == 1


async def test_q_quits_app(tui_db_path: Path) -> None:
    """Task 5.3: 按 q 退出 TUI。"""
    app = ATOApp(db_path=tui_db_path)
    async with app.run_test() as pilot:
        await pilot.press("q")


# ---------------------------------------------------------------------------
# Task 3: 数据轮询与刷新
# ---------------------------------------------------------------------------


async def test_refresh_data_updates_reactive(tui_db_path: Path) -> None:
    """Task 3: refresh_data 更新 reactive 属性。"""
    app = ATOApp(db_path=tui_db_path)
    async with app.run_test():
        assert app.story_count == 0

        # 在 DB 中插入数据
        db = await get_connection(tui_db_path)
        try:
            now = datetime.now(tz=UTC).isoformat()
            await db.execute(
                "INSERT INTO stories (story_id, title, status, current_phase, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                ("s1", "New Story", "in_progress", "developing", now, now),
            )
            await db.commit()
        finally:
            await db.close()

        # 手动触发刷新
        await app.refresh_data()
        assert app.story_count == 1


async def test_refresh_data_error_does_not_crash(tui_db_path: Path) -> None:
    """Task 3: refresh_data 异常不会崩溃 TUI。"""
    app = ATOApp(db_path=tui_db_path)
    async with app.run_test():
        # 用一个会抛异常的路径替换 db_path
        app._db_path = Path("/nonexistent/path/state.db")
        # 不应该抛异常
        await app.refresh_data()


async def test_dashboard_content_updated(tui_db_path: Path) -> None:
    """Task 3/5: DashboardScreen 内容随数据更新。"""
    app = ATOApp(db_path=tui_db_path)
    async with app.run_test():
        dashboard = app.query_one(DashboardScreen)
        # update_content 应已被调用，验证通过 reactive 属性
        assert app.story_count == 0
        assert app.last_updated != ""
        # DashboardScreen 应不再显示初始文本 "加载中..."
        # 通过 mock 验证 update_content 被调用
        with patch.object(dashboard, "update_content") as mock_update:
            await app.refresh_data()
            mock_update.assert_called_once()


# ---------------------------------------------------------------------------
# Task 4: 写入路径与 nudge
# ---------------------------------------------------------------------------


async def _insert_story_and_approval(
    tui_db_path: Path, *, approval_status: str = "pending"
) -> None:
    """测试辅助：插入 story + approval。"""
    db = await get_connection(tui_db_path)
    try:
        now = datetime.now(tz=UTC).isoformat()
        await db.execute(
            "INSERT OR IGNORE INTO stories "
            "(story_id, title, status, current_phase, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("s1", "Story", "in_progress", "developing", now, now),
        )
        await db.execute(
            "INSERT INTO approvals (approval_id, story_id, approval_type, status, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("a1", "s1", "code_review", approval_status, now),
        )
        await db.commit()
    finally:
        await db.close()


async def test_write_approval_updates_db(tui_db_path: Path) -> None:
    """AC2: write_approval 写入 SQLite + commit，返回 True。"""
    await _insert_story_and_approval(tui_db_path)

    app = ATOApp(db_path=tui_db_path)
    async with app.run_test():
        result = await app.write_approval(
            approval_id="a1",
            story_id="s1",
            approval_type="code_review",
            decision="approved",
        )
        assert result is True

    # 验证 DB 已更新
    db = await get_connection(tui_db_path)
    try:
        cursor = await db.execute(
            "SELECT status, decision FROM approvals WHERE approval_id = ?",
            ("a1",),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == "approved"
        assert row[1] == "approved"
    finally:
        await db.close()


async def test_write_approval_rejects_non_pending(tui_db_path: Path) -> None:
    """并发保护：已被处理的审批不会被覆盖，返回 False。"""
    await _insert_story_and_approval(tui_db_path, approval_status="approved")

    app = ATOApp(db_path=tui_db_path)
    async with app.run_test():
        result = await app.write_approval(
            approval_id="a1",
            story_id="s1",
            approval_type="code_review",
            decision="rejected",
        )
        assert result is False

    # DB 状态未被覆盖
    db = await get_connection(tui_db_path)
    try:
        cursor = await db.execute("SELECT status FROM approvals WHERE approval_id = ?", ("a1",))
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == "approved"  # 仍是原状态
    finally:
        await db.close()


async def test_write_approval_sends_nudge(tui_db_path: Path) -> None:
    """AC2: write_approval commit 后发送 nudge（PID 从文件重新读取）。"""
    import os

    await _insert_story_and_approval(tui_db_path)

    # 写入 PID 文件（指向当前进程）
    pid_path = tui_db_path.parent / "orchestrator.pid"
    pid_path.write_text(str(os.getpid()))

    app = ATOApp(db_path=tui_db_path)
    async with app.run_test():
        with patch("ato.nudge.send_external_nudge") as mock_nudge:
            await app.write_approval(
                approval_id="a1",
                story_id="s1",
                approval_type="code_review",
                decision="approved",
            )
            mock_nudge.assert_called_once_with(os.getpid())


async def test_write_approval_no_pid_file_skips_nudge(tui_db_path: Path) -> None:
    """AC2: 无 PID 文件时跳过 nudge，仅保留 DB 写入。"""
    await _insert_story_and_approval(tui_db_path)
    # 无 PID 文件 → _resolve_orchestrator_pid 返回 None

    app = ATOApp(db_path=tui_db_path)
    async with app.run_test():
        result = await app.write_approval(
            approval_id="a1",
            story_id="s1",
            approval_type="code_review",
            decision="rejected",
        )
        assert result is True

    # DB 写入仍然成功
    db = await get_connection(tui_db_path)
    try:
        cursor = await db.execute("SELECT status FROM approvals WHERE approval_id = ?", ("a1",))
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == "rejected"
    finally:
        await db.close()


async def test_write_approval_stale_pid_no_crash(tui_db_path: Path) -> None:
    """AC2: stale PID（进程不存在）时不回滚已提交写入。"""
    await _insert_story_and_approval(tui_db_path)

    # 写入不存在的 PID
    pid_path = tui_db_path.parent / "orchestrator.pid"
    pid_path.write_text("999999")

    app = ATOApp(db_path=tui_db_path)
    async with app.run_test():
        result = await app.write_approval(
            approval_id="a1",
            story_id="s1",
            approval_type="code_review",
            decision="approved",
        )
        assert result is True

    # DB 写入仍然成功（stale PID → nudge 跳过不回滚）
    db = await get_connection(tui_db_path)
    try:
        cursor = await db.execute("SELECT status FROM approvals WHERE approval_id = ?", ("a1",))
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == "approved"
    finally:
        await db.close()
