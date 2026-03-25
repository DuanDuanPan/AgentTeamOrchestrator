"""TUI 响应式布局集成测试。

使用 Textual pilot + run_test(size=...) 模拟不同终端宽度，
验证正确的布局模式激活、可见布局、焦点语义和 resize 行为。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from ato.models.db import get_connection, init_db
from ato.tui.app import ATOApp
from ato.tui.dashboard import DashboardScreen, _FocusablePanel


@pytest.fixture()
async def tui_db_path(tmp_path: Path) -> Path:
    """返回已初始化的临时数据库路径。"""
    db_path = tmp_path / ".ato" / "state.db"
    await init_db(db_path)
    return db_path


@pytest.fixture()
async def tui_db_with_data(tui_db_path: Path) -> Path:
    """返回包含测试数据的临时数据库路径。"""
    db = await get_connection(tui_db_path)
    try:
        now = datetime.now(tz=UTC).isoformat()
        await db.execute(
            "INSERT INTO stories (story_id, title, status, current_phase, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("s1", "Test Story", "in_progress", "developing", now, now),
        )
        await db.execute(
            "INSERT INTO approvals (approval_id, story_id, approval_type, "
            "status, created_at) VALUES (?, ?, ?, ?, ?)",
            ("a1", "s1", "code_review", "pending", now),
        )
        await db.commit()
    finally:
        await db.close()
    return tui_db_path


# ---------------------------------------------------------------------------
# 断点布局模式 + ContentSwitcher 可见性验证
# ---------------------------------------------------------------------------


async def _get_visible_mode(app: ATOApp) -> str:
    """返回 ContentSwitcher 当前可见的布局 ID。"""
    from textual.widgets import ContentSwitcher

    dashboard = app.query_one(DashboardScreen)
    switcher = dashboard.query_one(ContentSwitcher)
    return switcher.current or ""


async def test_wide_terminal_three_panel_visible(tui_db_path: Path) -> None:
    """≥140 列 → three-panel 可见（ContentSwitcher.current 确认）。"""
    app = ATOApp(db_path=tui_db_path)
    async with app.run_test(size=(150, 40)):
        assert app.layout_mode == "three-panel"
        assert await _get_visible_mode(app) == "three-panel"


async def test_boundary_140_three_panel_visible(tui_db_path: Path) -> None:
    """140 列边界 → three-panel 可见。"""
    app = ATOApp(db_path=tui_db_path)
    async with app.run_test(size=(140, 40)):
        assert app.layout_mode == "three-panel"
        assert await _get_visible_mode(app) == "three-panel"


async def test_narrow_terminal_tabbed_visible(tui_db_path: Path) -> None:
    """100-139 列 → tabbed 可见。"""
    app = ATOApp(db_path=tui_db_path)
    async with app.run_test(size=(120, 40)):
        assert app.layout_mode == "tabbed"
        assert await _get_visible_mode(app) == "tabbed"


async def test_boundary_100_tabbed_visible(tui_db_path: Path) -> None:
    """100 列边界 → tabbed 可见。"""
    app = ATOApp(db_path=tui_db_path)
    async with app.run_test(size=(100, 40)):
        assert app.layout_mode == "tabbed"
        assert await _get_visible_mode(app) == "tabbed"


async def test_very_narrow_degraded_visible(tui_db_path: Path) -> None:
    """<100 列 → degraded 可见。"""
    app = ATOApp(db_path=tui_db_path)
    async with app.run_test(size=(80, 40)):
        assert app.layout_mode == "degraded"
        assert await _get_visible_mode(app) == "degraded"


async def test_ultra_wide_three_panel_visible(tui_db_path: Path) -> None:
    """200 列 → three-panel 可见。"""
    app = ATOApp(db_path=tui_db_path)
    async with app.run_test(size=(200, 40)):
        assert app.layout_mode == "three-panel"
        assert await _get_visible_mode(app) == "three-panel"


# ---------------------------------------------------------------------------
# 180+ 列超宽屏面板比例
# ---------------------------------------------------------------------------


async def test_ultra_wide_panel_ratio_30_70(tui_db_path: Path) -> None:
    """200 列 → 左面板约 30%、右面板约 70%。"""
    app = ATOApp(db_path=tui_db_path)
    async with app.run_test(size=(200, 40)):
        left = app.query_one(".left-panel")
        right = app.query_one(".right-panel")
        total = left.size.width + right.size.width
        left_pct = left.size.width / total * 100
        assert left_pct < 35, f"超宽屏左面板比例应 ≈30%，实际 {left_pct:.0f}%"


async def test_standard_wide_panel_ratio_40_60(tui_db_path: Path) -> None:
    """150 列 → 左面板约 40%、右面板约 60%。"""
    app = ATOApp(db_path=tui_db_path)
    async with app.run_test(size=(150, 40)):
        left = app.query_one(".left-panel")
        right = app.query_one(".right-panel")
        total = left.size.width + right.size.width
        left_pct = left.size.width / total * 100
        assert 35 <= left_pct <= 45, f"标准宽终端左面板比例应 ≈40%，实际 {left_pct:.0f}%"


# ---------------------------------------------------------------------------
# Resize 切换 + ContentSwitcher 可见性 + 数据保持
# ---------------------------------------------------------------------------


async def test_resize_changes_visible_mode(tui_db_path: Path) -> None:
    """resize 后 ContentSwitcher.current 正确切换到新布局。"""
    app = ATOApp(db_path=tui_db_path)
    async with app.run_test(size=(150, 40)) as pilot:
        assert await _get_visible_mode(app) == "three-panel"
        await pilot.resize_terminal(120, 40)
        assert await _get_visible_mode(app) == "tabbed"


async def test_resize_preserves_data(tui_db_path: Path) -> None:
    """resize 后 reactive 数据不被重置。"""
    app = ATOApp(db_path=tui_db_path)
    async with app.run_test(size=(150, 40)) as pilot:
        original_last_updated = app.last_updated
        assert original_last_updated != ""

        await pilot.resize_terminal(120, 40)
        assert app.last_updated == original_last_updated
        assert app.layout_mode == "tabbed"


async def test_resize_from_degraded_to_wide(tui_db_path: Path) -> None:
    """从 degraded → wide 后三面板可见。"""
    app = ATOApp(db_path=tui_db_path)
    async with app.run_test(size=(80, 40)) as pilot:
        assert await _get_visible_mode(app) == "degraded"
        await pilot.resize_terminal(150, 40)
        assert await _get_visible_mode(app) == "three-panel"


async def test_resize_preserves_focus_three_panel_round_trip(tui_db_path: Path) -> None:
    """宽→窄→宽后焦点自动恢复到之前的面板（无需再按键）。"""
    app = ATOApp(db_path=tui_db_path)
    async with app.run_test(size=(150, 40)) as pilot:
        # 先聚焦到面板并记录
        await pilot.press("tab")
        original_focused = app.focused
        assert isinstance(original_focused, _FocusablePanel)
        original_id = original_focused.id

        # 切到 tabbed 再切回
        await pilot.resize_terminal(120, 40)
        await pilot.resize_terminal(150, 40)

        # resize 完成后立即断言焦点已恢复，不按额外键
        restored = app.focused
        assert isinstance(restored, _FocusablePanel), (
            f"resize 后焦点应自动恢复到 _FocusablePanel，"
            f"实际为 {type(restored).__name__ if restored else None}"
        )
        assert restored.id == original_id, f"焦点应恢复到 {original_id}，实际恢复到 {restored.id}"


async def test_resize_tabbed_gets_focus(tui_db_path: Path) -> None:
    """切到 tabbed 模式后应有焦点（不为 None）。"""
    app = ATOApp(db_path=tui_db_path)
    async with app.run_test(size=(150, 40)) as pilot:
        await pilot.press("tab")
        await pilot.resize_terminal(120, 40)
        # 切换后立即断言焦点不为 None
        assert app.focused is not None, "切到 tabbed 后焦点不应为 None"


async def test_resize_tabbed_degraded_tabbed_preserves_focus(tui_db_path: Path) -> None:
    """tabbed→degraded→tabbed 后焦点恢复。"""
    app = ATOApp(db_path=tui_db_path)
    async with app.run_test(size=(120, 40)) as pilot:
        # 切到 degraded 再切回
        await pilot.resize_terminal(80, 40)
        assert app.focused is None  # degraded 无可聚焦控件

        await pilot.resize_terminal(120, 40)
        assert app.focused is not None, "从 degraded 切回 tabbed 后焦点应恢复"


# ---------------------------------------------------------------------------
# 焦点管理与键盘导航（语义验证）
# ---------------------------------------------------------------------------


async def test_tab_focuses_panel_not_hidden_widget(tui_db_path: Path) -> None:
    """Tab 聚焦的是可见的 _FocusablePanel，不是隐藏的 ContentTabs。"""
    app = ATOApp(db_path=tui_db_path)
    async with app.run_test(size=(150, 40)) as pilot:
        await pilot.press("tab")
        focused = app.focused
        assert isinstance(focused, _FocusablePanel), (
            f"Tab 应聚焦 _FocusablePanel，实际聚焦 {type(focused).__name__}"
        )


async def test_tab_cycles_between_both_panels(tui_db_path: Path) -> None:
    """Tab 在左右面板间循环切换。"""
    app = ATOApp(db_path=tui_db_path)
    async with app.run_test(size=(150, 40)) as pilot:
        await pilot.press("tab")
        first = app.focused
        assert isinstance(first, _FocusablePanel)
        first_classes = set(first.classes)

        await pilot.press("tab")
        second = app.focused
        assert isinstance(second, _FocusablePanel)
        second_classes = set(second.classes)

        # 两次 Tab 聚焦的面板不同
        assert first_classes != second_classes, "Tab 应在不同面板间切换"


async def test_shift_tab_reverse_focus(tui_db_path: Path) -> None:
    """Shift-Tab 反向切换焦点回到原面板。"""
    app = ATOApp(db_path=tui_db_path)
    async with app.run_test(size=(150, 40)) as pilot:
        await pilot.press("tab")
        first_focused = app.focused
        await pilot.press("tab")
        await pilot.press("shift+tab")
        back_focused = app.focused
        assert first_focused == back_focused


async def test_q_still_quits(tui_db_path: Path) -> None:
    """焦点管理不影响 q 退出。"""
    app = ATOApp(db_path=tui_db_path)
    async with app.run_test(size=(150, 40)) as pilot:
        await pilot.press("q")


async def test_number_keys_switch_tabs_active_pane(tui_db_path: Path) -> None:
    """Tab 模式下数字键切换 active TabPane。"""
    from textual.widgets import TabbedContent

    app = ATOApp(db_path=tui_db_path)
    async with app.run_test(size=(120, 40)) as pilot:
        assert app.layout_mode == "tabbed"
        dashboard = app.query_one(DashboardScreen)
        tabbed = dashboard.query_one(TabbedContent)

        await pilot.press("2")
        assert tabbed.active == "tab-stories"

        await pilot.press("1")
        assert tabbed.active == "tab-approvals"


# ---------------------------------------------------------------------------
# 数据同步到所有布局模式
# ---------------------------------------------------------------------------


async def test_tab_mode_shows_data(tui_db_with_data: Path) -> None:
    """Tab 模式下数据正确显示在各 TabPane 中。"""
    from textual.widgets import Static

    app = ATOApp(db_path=tui_db_with_data)
    async with app.run_test(size=(120, 40)):
        assert app.story_count == 1
        assert app.pending_approvals == 1

        dashboard = app.query_one(DashboardScreen)
        approvals = dashboard.query_one("#tab-approvals-content", Static)
        assert "1" in str(approvals.render())

        stories = dashboard.query_one("#tab-stories-content", Static)
        assert "1" in str(stories.render())


async def test_degraded_mode_shows_data(tui_db_with_data: Path) -> None:
    """降级模式下也应显示数据摘要。"""
    from textual.widgets import Static

    app = ATOApp(db_path=tui_db_with_data)
    async with app.run_test(size=(80, 40)):
        dashboard = app.query_one(DashboardScreen)
        degraded = dashboard.query_one("#degraded", Static)
        text = str(degraded.render())
        assert "Stories: 1" in text


async def test_three_panel_shows_data(tui_db_with_data: Path) -> None:
    """三面板模式下数据正确显示。"""
    from textual.widgets import Static

    app = ATOApp(db_path=tui_db_with_data)
    async with app.run_test(size=(150, 40)):
        dashboard = app.query_one(DashboardScreen)
        left = dashboard.query_one("#left-panel-content", Static)
        text = str(left.render())
        assert "Stories: 1" in text
        assert "待审批: 1" in text


# ---------------------------------------------------------------------------
# Header/Footer 始终可见
# ---------------------------------------------------------------------------


async def test_header_footer_always_visible(tui_db_path: Path) -> None:
    """所有断点下 Header/Footer 始终可见。"""
    for width in (80, 120, 150, 200):
        app = ATOApp(db_path=tui_db_path)
        async with app.run_test(size=(width, 40)):
            assert app.query_one("Header") is not None
            assert app.query_one("Footer") is not None
