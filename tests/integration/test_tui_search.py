"""TUI 搜索面板集成测试。

使用 Textual pilot 验证：
- `/` 激活搜索面板
- 输入搜索词 → 结果实时过滤
- 搜索输入中的数字/y/n/d 不触发 Tab 切换或审批提交
- Enter 跳转 + 面板关闭
- ESC 取消搜索 + 焦点恢复
- 搜索面板在 three-panel 和 tabbed 模式下均可用
- 成本 Tab / 日志 Tab 数据正确显示
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from ato.models.db import get_connection, init_db
from ato.tui.app import ATOApp
from ato.tui.dashboard import DashboardScreen
from ato.tui.widgets.search_panel import SearchPanel


@pytest.fixture()
async def tui_db_path(tmp_path: Path) -> Path:
    """返回已初始化的临时数据库路径。"""
    db_path = tmp_path / ".ato" / "state.db"
    await init_db(db_path)
    return db_path


@pytest.fixture()
async def tui_db_with_stories(tui_db_path: Path) -> Path:
    """返回包含多个 story 和审批的测试数据库。"""
    db = await get_connection(tui_db_path)
    try:
        now = datetime.now(tz=UTC).isoformat()
        # Stories
        for i in range(1, 4):
            await db.execute(
                "INSERT INTO stories (story_id, title, status, current_phase, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (f"story-00{i}", f"Test Story {i}", "in_progress", "developing", now, now),
            )
        # Approval
        await db.execute(
            "INSERT INTO approvals (approval_id, story_id, approval_type, "
            "status, created_at) VALUES (?, ?, ?, ?, ?)",
            ("a1", "story-001", "merge_authorization", "pending", now),
        )
        # Task (for log tab)
        await db.execute(
            "INSERT INTO tasks (task_id, story_id, phase, role, cli_tool, "
            "status, started_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("t1", "story-001", "developing", "dev", "claude", "running", now),
        )
        # Cost log (for cost tab)
        await db.execute(
            "INSERT INTO cost_log (cost_log_id, story_id, task_id, cli_tool, "
            "phase, cost_usd, input_tokens, output_tokens, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("c1", "story-001", "t1", "claude", "developing", 1.50, 1000, 500, now),
        )
        await db.commit()
    finally:
        await db.close()
    return tui_db_path


# ---------------------------------------------------------------------------
# AC1: `/` 激活搜索面板
# ---------------------------------------------------------------------------


async def test_slash_activates_search_panel(tui_db_with_stories: Path) -> None:
    """按 `/` 激活搜索面板。"""
    app = ATOApp(db_path=tui_db_with_stories)
    async with app.run_test(size=(150, 40)) as pilot:
        dashboard = app.query_one(DashboardScreen)
        assert not dashboard._search_active

        await pilot.press("/")
        assert dashboard._search_active

        # SearchPanel 应可见
        panel = dashboard.query_one("#search-panel", SearchPanel)
        assert panel.display is True


async def test_slash_does_not_activate_twice(tui_db_with_stories: Path) -> None:
    """搜索已激活时再按 `/` 不重复激活。"""
    app = ATOApp(db_path=tui_db_with_stories)
    async with app.run_test(size=(150, 40)) as pilot:
        await pilot.press("/")
        dashboard = app.query_one(DashboardScreen)
        assert dashboard._search_active

        # 第二次按 `/` — 应该是在 Input 中输入 `/` 字符
        await pilot.press("/")
        assert dashboard._search_active  # 仍然激活


# ---------------------------------------------------------------------------
# AC2: 搜索结果实时过滤与跳转
# ---------------------------------------------------------------------------


async def test_search_filters_results(tui_db_with_stories: Path) -> None:
    """输入搜索词后结果实时过滤。"""
    app = ATOApp(db_path=tui_db_with_stories)
    async with app.run_test(size=(150, 40)) as pilot:
        await pilot.press("/")
        # 输入 "001"
        await pilot.press("0", "0", "1")
        await pilot.pause()

        dashboard = app.query_one(DashboardScreen)
        panel = dashboard.query_one("#search-panel", SearchPanel)
        # 应至少有 story-001 匹配
        assert len(panel._results) >= 1
        assert any(r.item.item_id == "story-001" for r in panel._results)


async def test_enter_selects_and_closes(tui_db_with_stories: Path) -> None:
    """Enter 选中结果后关闭搜索面板。"""
    app = ATOApp(db_path=tui_db_with_stories)
    async with app.run_test(size=(150, 40)) as pilot:
        await pilot.press("/")
        await pilot.press("s", "t", "o", "r", "y", "-", "0", "0", "2")
        await pilot.pause()

        await pilot.press("enter")
        await pilot.pause()

        dashboard = app.query_one(DashboardScreen)
        assert not dashboard._search_active
        # story-002 应该被选中
        assert dashboard._selected_item_id == "story:story-002"


# ---------------------------------------------------------------------------
# AC3: ESC 取消搜索
# ---------------------------------------------------------------------------


async def test_escape_closes_search(tui_db_with_stories: Path) -> None:
    """ESC 关闭搜索面板。"""
    app = ATOApp(db_path=tui_db_with_stories)
    async with app.run_test(size=(150, 40)) as pilot:
        await pilot.press("/")
        dashboard = app.query_one(DashboardScreen)
        assert dashboard._search_active

        await pilot.press("escape")
        assert not dashboard._search_active

        panel = dashboard.query_one("#search-panel", SearchPanel)
        assert panel.display is False


# ---------------------------------------------------------------------------
# 搜索输入不触发全局快捷键 (AC1 + 兼容性)
# ---------------------------------------------------------------------------


async def test_number_keys_during_search_do_not_switch_tab(tui_db_with_stories: Path) -> None:
    """搜索输入中按数字键不触发 Tab 切换。"""
    app = ATOApp(db_path=tui_db_with_stories)
    async with app.run_test(size=(120, 40)) as pilot:
        # tabbed 模式（100-139 列）
        assert app.layout_mode == "tabbed"
        await pilot.press("/")

        dashboard = app.query_one(DashboardScreen)
        assert dashboard._search_active

        # 按数字 1-9 应该输入到搜索框，不触发 Tab 切换
        await pilot.press("1")
        await pilot.pause()

        # 搜索面板仍激活
        assert dashboard._search_active


async def test_y_n_keys_during_search_do_not_submit_approval(tui_db_with_stories: Path) -> None:
    """搜索输入中按 y/n 不触发审批提交。"""
    app = ATOApp(db_path=tui_db_with_stories)
    async with app.run_test(size=(150, 40)) as pilot:
        await pilot.press("/")
        dashboard = app.query_one(DashboardScreen)

        # 按 y 和 n 不应触发审批操作
        await pilot.press("y")
        await pilot.press("n")
        await pilot.pause()

        # 搜索面板仍激活，无审批已提交
        assert dashboard._search_active
        assert len(dashboard._submitted_approvals) == 0


# ---------------------------------------------------------------------------
# AC4: 成本 Tab / 日志 Tab
# ---------------------------------------------------------------------------


async def test_cost_tab_shows_data(tui_db_with_stories: Path) -> None:
    """成本 Tab 显示 story 级成本数据。"""
    app = ATOApp(db_path=tui_db_with_stories)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()  # 等待数据加载

        from textual.widgets import Static

        dashboard = app.query_one(DashboardScreen)
        cost_widget = dashboard.query_one("#tab-cost-content", Static)
        # 应该包含成本数据（非占位文本）
        rendered = cost_widget.render()
        plain = rendered.plain if hasattr(rendered, "plain") else str(rendered)
        assert "story-001" in plain or "$" in plain


async def test_log_tab_shows_events(tui_db_with_stories: Path) -> None:
    """日志 Tab 显示事件数据。"""
    app = ATOApp(db_path=tui_db_with_stories)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()  # 等待数据加载

        from textual.widgets import Static

        dashboard = app.query_one(DashboardScreen)
        log_widget = dashboard.query_one("#tab-log-content", Static)
        rendered = log_widget.render()
        plain = rendered.plain if hasattr(rendered, "plain") else str(rendered)
        # 应该包含事件数据
        assert "story-001" in plain or "→" in plain


# ---------------------------------------------------------------------------
# AC6: 搜索面板在不同布局模式下均可用
# ---------------------------------------------------------------------------


async def test_search_works_in_tabbed_mode(tui_db_with_stories: Path) -> None:
    """tabbed 模式下搜索面板可用。"""
    app = ATOApp(db_path=tui_db_with_stories)
    async with app.run_test(size=(120, 40)) as pilot:
        assert app.layout_mode == "tabbed"
        await pilot.press("/")

        dashboard = app.query_one(DashboardScreen)
        assert dashboard._search_active


async def test_search_works_in_three_panel_mode(tui_db_with_stories: Path) -> None:
    """three-panel 模式下搜索面板可用。"""
    app = ATOApp(db_path=tui_db_with_stories)
    async with app.run_test(size=(150, 40)) as pilot:
        assert app.layout_mode == "three-panel"
        await pilot.press("/")

        dashboard = app.query_one(DashboardScreen)
        assert dashboard._search_active


# ---------------------------------------------------------------------------
# 回归安全
# ---------------------------------------------------------------------------


async def test_regression_existing_bindings_work_without_search(tui_db_with_stories: Path) -> None:
    """搜索未激活时现有快捷键正常工作。"""
    app = ATOApp(db_path=tui_db_with_stories)
    async with app.run_test(size=(150, 40)) as pilot:
        dashboard = app.query_one(DashboardScreen)
        assert not dashboard._search_active

        # ↑↓ 应该正常工作（如果有数据）
        await pilot.press("down")
        await pilot.press("up")
        # 不应崩溃


async def test_tab_search_in_three_panel_does_not_submit_approval(
    tui_db_with_stories: Path,
) -> None:
    """three-panel 模式下搜索 Tab 目标不误提交审批且不切换布局。"""
    app = ATOApp(db_path=tui_db_with_stories)
    async with app.run_test(size=(150, 40)) as pilot:
        assert app.layout_mode == "three-panel"

        dashboard = app.query_one(DashboardScreen)
        submitted_before = len(dashboard._submitted_approvals)

        # 搜索"成本" → 选中 Tab 3
        await pilot.press("/")
        for c in "成本":
            await pilot.press(c)
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        # 不应有新的审批被提交
        assert len(dashboard._submitted_approvals) == submitted_before
        # 布局模式必须保持 three-panel，不被切到 tabbed
        assert app.layout_mode == "three-panel"


async def test_story_id_direct_hit_returns_story_not_approval(
    tui_db_with_stories: Path,
) -> None:
    """精确输入 story ID 时应命中 story 而非同 ID 的审批（Fix #2 回归）。"""
    app = ATOApp(db_path=tui_db_with_stories)
    async with app.run_test(size=(150, 40)) as pilot:
        await pilot.press("/")
        for c in "story-001":
            await pilot.press(c)
        await pilot.pause()

        dashboard = app.query_one(DashboardScreen)
        panel = dashboard.query_one("#search-panel", SearchPanel)

        # 第一个结果应该是 story 类型，不是 approval
        assert len(panel._results) >= 1
        assert panel._results[0].item.item_type == "story"
        assert panel._results[0].item.item_id == "story-001"


async def test_escape_restores_focus_in_tabbed_mode(
    tui_db_with_stories: Path,
) -> None:
    """tabbed 模式下 ESC 关闭搜索后焦点恢复（Fix #3 回归）。"""
    app = ATOApp(db_path=tui_db_with_stories)
    async with app.run_test(size=(120, 40)) as pilot:
        assert app.layout_mode == "tabbed"

        await pilot.press("/")
        dashboard = app.query_one(DashboardScreen)
        assert dashboard._search_active

        await pilot.press("escape")
        assert not dashboard._search_active

        # 焦点应恢复到某个 widget（不为 None）
        assert app.focused is not None
