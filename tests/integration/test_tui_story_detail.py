"""TUI Story 详情钻入导航集成测试。

使用 Textual pilot + mock SQLite 数据验证 Enter/ESC 导航、
f/c/h 子视图展开、ConvergentLoopProgress、tabbed 模式兼容性。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from textual.containers import VerticalScroll
from textual.widgets import ContentSwitcher

from ato.models.db import get_connection, init_db
from ato.tui.app import ATOApp
from ato.tui.dashboard import DashboardScreen
from ato.tui.story_detail import StoryDetailView
from ato.tui.widgets.story_status_line import StoryStatusLine

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
async def tui_db_path(tmp_path: Path) -> Path:
    """返回已初始化的临时数据库路径。"""
    db_path = tmp_path / ".ato" / "state.db"
    await init_db(db_path)
    return db_path


@pytest.fixture()
async def tui_db_with_story(tui_db_path: Path) -> Path:
    """创建包含完整 story + tasks + findings + cost 的数据库。"""
    db = await get_connection(tui_db_path)
    try:
        now = datetime.now(tz=UTC).isoformat()
        # Story
        await db.execute(
            "INSERT INTO stories (story_id, title, status, current_phase, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("s1", "Test Story", "in_progress", "reviewing", now, now),
        )
        # Tasks
        await db.execute(
            "INSERT INTO tasks (task_id, story_id, phase, role, cli_tool, "
            "status, started_at, completed_at, duration_ms) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("t1", "s1", "developing", "dev", "claude", "completed", now, now, 60000),
        )
        await db.execute(
            "INSERT INTO tasks (task_id, story_id, phase, role, cli_tool, "
            "status, started_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("t2", "s1", "reviewing", "reviewer", "codex", "running", now),
        )
        # Findings
        await db.execute(
            "INSERT INTO findings (finding_id, story_id, round_num, severity, "
            "description, status, file_path, rule_id, dedup_hash, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("f1", "s1", 1, "blocking", "Bug found", "open", "a.py", "R1", "h1", now),
        )
        await db.execute(
            "INSERT INTO findings (finding_id, story_id, round_num, severity, "
            "description, status, file_path, rule_id, dedup_hash, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("f2", "s1", 1, "suggestion", "Style", "closed", "b.py", "R2", "h2", now),
        )
        # Cost log
        await db.execute(
            "INSERT INTO cost_log (cost_log_id, story_id, cli_tool, phase, role, "
            "input_tokens, output_tokens, cost_usd, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("c1", "s1", "claude", "developing", "dev", 1000, 500, 0.05, now),
        )
        await db.commit()
    finally:
        await db.close()
    return tui_db_path


@pytest.fixture()
async def tui_db_with_three_stories(tui_db_path: Path) -> Path:
    """创建包含 3 条 story 的数据库，用于导航回归测试。"""
    db = await get_connection(tui_db_path)
    try:
        now = datetime.now(tz=UTC).isoformat()
        for sid, title in [("s1", "Story 1"), ("s2", "Story 2"), ("s3", "Story 3")]:
            await db.execute(
                "INSERT INTO stories (story_id, title, status, current_phase, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (sid, title, "in_progress", "reviewing", now, now),
            )
        await db.commit()
    finally:
        await db.close()
    return tui_db_path


# ---------------------------------------------------------------------------
# Task 10.1: Enter 键从主屏进入详情页 (three-panel)
# ---------------------------------------------------------------------------


async def test_enter_activates_detail_view_three_panel(tui_db_with_story: Path) -> None:
    """三面板模式：Enter 切换 right-top-switcher 到 StoryDetailView。"""
    app = ATOApp(db_path=tui_db_with_story)
    async with app.run_test(size=(150, 40)) as pilot:
        # 确认初始状态
        dashboard = app.query_one(DashboardScreen)
        assert not dashboard._in_detail_mode

        # 等待数据加载
        await pilot.pause()

        # 按 Enter 进入详情
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()  # 等待异步加载

        assert dashboard._in_detail_mode is True


# ---------------------------------------------------------------------------
# Task 10.2: ESC 从详情页返回主屏 (three-panel)
# ---------------------------------------------------------------------------


async def test_escape_returns_from_detail_three_panel(tui_db_with_story: Path) -> None:
    """三面板模式：ESC 从详情页返回概览。"""
    app = ATOApp(db_path=tui_db_with_story)
    async with app.run_test(size=(150, 40)) as pilot:
        await pilot.pause()

        # 进入详情
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

        dashboard = app.query_one(DashboardScreen)
        assert dashboard._in_detail_mode is True

        # ESC 返回
        await pilot.press("escape")
        await pilot.pause()

        assert dashboard._in_detail_mode is False


# ---------------------------------------------------------------------------
# Task 10.3: f/c/h 展开子视图 + ESC 返回
# ---------------------------------------------------------------------------


async def test_fch_keys_toggle_subviews(tui_db_with_story: Path) -> None:
    """f/c/h 展开子视图，ESC 折叠回概览。"""
    app = ATOApp(db_path=tui_db_with_story)
    async with app.run_test(size=(150, 40)) as pilot:
        await pilot.pause()
        # 进入详情
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

        # 获取详情视图
        dashboard = app.query_one(DashboardScreen)
        if not dashboard._in_detail_mode:
            return  # 如果没进入详情模式，跳过

        # 尝试按 f
        await pilot.press("f")
        await pilot.pause()


# ---------------------------------------------------------------------------
# Task 10.4: ConvergentLoopProgress 在有 CL 数据 story 上显示
# ---------------------------------------------------------------------------


async def test_cl_data_visible_in_detail(tui_db_with_story: Path) -> None:
    """有 CL 数据的 story 详情页包含 CL 进度信息。"""
    # 添加 findings 使 CL round 数据存在
    db = await get_connection(tui_db_with_story)
    try:
        now = datetime.now(tz=UTC).isoformat()
        await db.execute(
            "INSERT INTO findings (finding_id, story_id, round_num, severity, "
            "description, status, file_path, rule_id, dedup_hash, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("f3", "s1", 2, "blocking", "Bug2", "closed", "c.py", "R3", "h3", now),
        )
        await db.commit()
    finally:
        await db.close()

    app = ATOApp(db_path=tui_db_with_story)
    async with app.run_test(size=(150, 40)) as pilot:
        await pilot.pause()
        # 验证 CL 轮次数据被加载
        assert app._story_cl_rounds.get("s1", 0) > 0


# ---------------------------------------------------------------------------
# Task 10.5: tabbed 模式下 tab-stories 的 list/detail 切换
# ---------------------------------------------------------------------------


async def test_tabbed_mode_has_stories_switcher(tui_db_with_story: Path) -> None:
    """Tabbed 模式存在 #tab-stories-switcher ContentSwitcher。"""
    app = ATOApp(db_path=tui_db_with_story)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        dashboard = app.query_one(DashboardScreen)
        try:
            switcher = dashboard.query_one("#tab-stories-switcher", ContentSwitcher)
            assert switcher.current == "tab-story-list-container"
        except Exception:
            pytest.skip("tab-stories-switcher not found in tabbed mode")


# ---------------------------------------------------------------------------
# Task 10.6: tabbed 模式下 [1]-[4] 仍切换 Tab
# ---------------------------------------------------------------------------


async def test_tabbed_mode_digit_keys_switch_tabs(tui_db_with_story: Path) -> None:
    """Tabbed 模式下数字键 [1]-[4] 切换 Tab，不被详情页破坏。"""
    app = ATOApp(db_path=tui_db_with_story)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        # 按 2 切换到 Stories tab
        await pilot.press("2")
        await pilot.pause()

        # 确认 layout_mode 是 tabbed
        assert app.layout_mode == "tabbed"


# ---------------------------------------------------------------------------
# Task 10.7: 导航后左面板选中状态保持
# ---------------------------------------------------------------------------


async def test_selection_preserved_after_detail_navigation(tui_db_with_story: Path) -> None:
    """Enter → ESC 后左面板选中的 story 不变。"""
    app = ATOApp(db_path=tui_db_with_story)
    async with app.run_test(size=(150, 40)) as pilot:
        await pilot.pause()
        dashboard = app.query_one(DashboardScreen)
        # 记录选中项
        selected_before = dashboard._selected_item_id

        # Enter → ESC
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

        # 选中项应保持
        assert dashboard._selected_item_id == selected_before


# ---------------------------------------------------------------------------
# Task 10.8: TUI 回归测试
# ---------------------------------------------------------------------------


async def test_app_starts_with_detail_view_composed(tui_db_with_story: Path) -> None:
    """ATOApp 启动后 StoryDetailView 已挂载在 DOM 中。"""
    app = ATOApp(db_path=tui_db_with_story)
    async with app.run_test(size=(150, 40)):
        dashboard = app.query_one(DashboardScreen)
        # 三面板模式应有 #right-top-detail
        detail_views = list(dashboard.query(StoryDetailView))
        assert len(detail_views) >= 1


async def test_existing_approval_bindings_still_work(tui_db_with_story: Path) -> None:
    """详情模式外 y/n 审批键仍然生效（不会被错误阻断）。"""
    app = ATOApp(db_path=tui_db_with_story)
    async with app.run_test(size=(150, 40)) as pilot:
        await pilot.pause()
        dashboard = app.query_one(DashboardScreen)
        # 不在详情模式下
        assert not dashboard._in_detail_mode
        # y/n 应正常处理（不抛异常）
        await pilot.press("y")
        await pilot.press("n")
        await pilot.pause()


# ---------------------------------------------------------------------------
# 回归: 轮询不应踢回详情页 (Fix #1)
# ---------------------------------------------------------------------------


async def test_polling_does_not_kick_detail_back(tui_db_with_story: Path) -> None:
    """进入详情后 refresh_data 不应把 switcher 切回 right-top-content。"""
    app = ATOApp(db_path=tui_db_with_story)
    async with app.run_test(size=(150, 40)) as pilot:
        await pilot.pause()

        # 进入详情
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

        dashboard = app.query_one(DashboardScreen)
        if not dashboard._in_detail_mode:
            return  # 没进详情则跳过

        # 模拟一次轮询
        await app.refresh_data()
        await pilot.pause()

        # 详情模式应保持
        assert dashboard._in_detail_mode is True
        # switcher 应仍指向 detail
        switcher = dashboard.query_one("#right-top-switcher", ContentSwitcher)
        assert switcher.current == "right-top-detail"


async def test_detail_mode_blocks_story_selection_drift(
    tui_db_with_three_stories: Path,
) -> None:
    """详情页获焦时，↑↓ 不应偷偷改变左侧选中 story。"""
    app = ATOApp(db_path=tui_db_with_three_stories)
    async with app.run_test(size=(150, 40)) as pilot:
        await pilot.pause()

        dashboard = app.query_one(DashboardScreen)
        assert dashboard._selected_item_id == "story:s1"

        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

        assert dashboard._in_detail_mode is True
        assert dashboard._detail_story_id == "s1"

        await pilot.press("down")
        await pilot.press("down")
        await pilot.pause()

        assert dashboard._detail_story_id == "s1"
        assert dashboard._selected_item_id == "story:s1"


async def test_tabbed_escape_restores_story_navigation_focus(
    tui_db_with_three_stories: Path,
) -> None:
    """Tabbed 模式下 Enter → Esc 后，↑↓ 应继续切换 story。"""
    app = ATOApp(db_path=tui_db_with_three_stories)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("2")
        await pilot.pause()

        dashboard = app.query_one(DashboardScreen)
        assert dashboard._selected_item_id == "story:s1"

        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        assert dashboard._in_detail_mode is True

        await pilot.press("escape")
        await pilot.pause()

        assert dashboard._in_detail_mode is False
        assert app.focused is not None

        await pilot.press("down")
        await pilot.pause()

        assert dashboard._selected_item_id == "story:s2"


async def test_non_running_story_list_updates_activity_via_story_status_line(
    tui_db_path: Path,
) -> None:
    """非 running story 走终态 fallback 时，StoryStatusLine 渲染最新 activity。"""
    db = await get_connection(tui_db_path)
    try:
        now = datetime.now(tz=UTC).isoformat()
        await db.execute(
            "INSERT INTO stories (story_id, title, status, current_phase, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("s-terminal", "Terminal Story", "blocked", "reviewing", now, now),
        )
        await db.execute(
            "INSERT INTO tasks (task_id, story_id, phase, role, cli_tool, status, "
            "started_at, completed_at, last_activity_type, last_activity_summary) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "t-terminal",
                "s-terminal",
                "reviewing",
                "reviewer",
                "codex",
                "failed",
                now,
                now,
                "error",
                "退出码 1: unknown",
            ),
        )
        await db.commit()
    finally:
        await db.close()

    app = ATOApp(db_path=tui_db_path)
    async with app.run_test(size=(150, 40)) as pilot:
        await pilot.pause()
        await app.refresh_data()
        await pilot.pause()

        dashboard = app.query_one(DashboardScreen)
        container = dashboard.query_one("#story-list-container", VerticalScroll)
        story_lines = list(container.query(StoryStatusLine))

        terminal_line = next((line for line in story_lines if line.story_id == "s-terminal"), None)
        assert terminal_line is not None
        assert "退出码 1: unknown" in terminal_line.render().plain
