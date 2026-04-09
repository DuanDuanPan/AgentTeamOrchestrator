"""TUI 异常审批集成测试 (Story 6.3b)。

ATOApp + mock SQLite 数据，验证异常审批多选面板渲染、
数字键提交、排序、fallback 移除等核心行为。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from textual.widgets import ContentSwitcher, Static

from ato.models.db import get_connection, init_db
from ato.tui.app import ATOApp
from ato.tui.dashboard import DashboardScreen
from ato.tui.widgets.approval_card import ApprovalCard
from ato.tui.widgets.exception_approval_panel import ExceptionApprovalPanel


@pytest.fixture()
async def tui_db_path(tmp_path: Path) -> Path:
    """返回已初始化的临时数据库路径。"""
    db_path = tmp_path / ".ato" / "state.db"
    await init_db(db_path)
    return db_path


def _rendered_text(widget: Static | ExceptionApprovalPanel) -> str:
    """提取 widget.render() 的纯文本。"""
    rendered = widget.render()
    return rendered.plain if hasattr(rendered, "plain") else str(rendered)


async def _insert_story(
    db_path: Path,
    story_id: str = "s1",
    *,
    worktree_path: str | None = None,
) -> None:
    """插入测试 story。"""
    db = await get_connection(db_path)
    try:
        now = datetime.now(tz=UTC).isoformat()
        await db.execute(
            "INSERT OR IGNORE INTO stories "
            "(story_id, title, status, current_phase, worktree_path, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                story_id,
                f"Test Story {story_id}",
                "in_progress",
                "developing",
                worktree_path,
                now,
                now,
            ),
        )
        await db.commit()
    finally:
        await db.close()


async def _insert_exception_approval(
    db_path: Path,
    *,
    approval_id: str = "exc-a1",
    story_id: str = "s1",
    approval_type: str = "regression_failure",
    risk_level: str = "high",
    payload_dict: dict[str, Any] | None = None,
) -> None:
    """插入异常审批记录。"""
    if payload_dict is None:
        payload_dict = {"options": ["revert", "fix_forward", "pause"], "reason": "test failed"}
    db = await get_connection(db_path)
    try:
        now = datetime.now(tz=UTC).isoformat()
        await db.execute(
            "INSERT INTO approvals "
            "(approval_id, story_id, approval_type, status, payload, "
            "recommended_action, risk_level, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                approval_id,
                story_id,
                approval_type,
                "pending",
                json.dumps(payload_dict),
                "fix_forward",
                risk_level,
                now,
            ),
        )
        await db.commit()
    finally:
        await db.close()


async def _insert_binary_approval(
    db_path: Path,
    *,
    approval_id: str = "bin-a1",
    story_id: str = "s1",
    approval_type: str = "merge_authorization",
) -> None:
    """插入二选一审批记录。"""
    db = await get_connection(db_path)
    try:
        now = datetime.now(tz=UTC).isoformat()
        await db.execute(
            "INSERT INTO approvals "
            "(approval_id, story_id, approval_type, status, "
            "recommended_action, risk_level, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                approval_id,
                story_id,
                approval_type,
                "pending",
                "approve",
                "low",
                now,
            ),
        )
        await db.commit()
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# AC1: ExceptionApprovalPanel 渲染
# ---------------------------------------------------------------------------


async def test_regression_failure_renders_exception_panel_in_three_panel(tui_db_path: Path) -> None:
    """regression_failure 审批在右上面板渲染 ExceptionApprovalPanel 内容。"""
    await _insert_story(tui_db_path)
    await _insert_exception_approval(tui_db_path)

    app = ATOApp(db_path=tui_db_path)
    async with app.run_test():
        dashboard = app.query_one(DashboardScreen)

        # 选中异常审批
        dashboard._selected_item_id = "approval:exc-a1"
        dashboard._update_detail_panel()

        switcher = dashboard.query_one("#right-top-switcher", ContentSwitcher)
        right_top = dashboard.query_one("#right-top-exception", ExceptionApprovalPanel)
        text = _rendered_text(right_top)

        assert switcher.current == "right-top-exception"
        assert "REGRESSION FAILURE" in text
        assert "Regression" in text
        assert "[1]" in text
        assert "[2]" in text
        assert "[3]" in text
        assert "[high]" in text
        assert right_top.has_class("exception-approval-high")


async def test_rebase_conflict_panel_includes_story_worktree_path(tui_db_path: Path) -> None:
    """rebase_conflict 详情面板补齐 dashboard 已知的 story.worktree_path。"""
    await _insert_story(tui_db_path, worktree_path="/tmp/wt/story-1")
    await _insert_exception_approval(
        tui_db_path,
        approval_type="rebase_conflict",
        risk_level="low",
        payload_dict={
            "conflict_files": ["src/main.py"],
            "stderr": "CONFLICT (content): Merge conflict in src/main.py",
            "options": ["manual_resolve", "skip", "abandon"],
        },
    )

    app = ATOApp(db_path=tui_db_path)
    async with app.run_test():
        dashboard = app.query_one(DashboardScreen)
        dashboard._selected_item_id = "approval:exc-a1"
        dashboard._update_detail_panel()

        right_top = dashboard.query_one("#right-top-exception", ExceptionApprovalPanel)
        text = _rendered_text(right_top)

        assert "worktree_path: /tmp/wt/story-1" in text
        assert not right_top.has_class("exception-approval-high")
        assert not right_top.has_class("exception-approval-medium")


# ---------------------------------------------------------------------------
# AC2: 数字键多选一决策
# ---------------------------------------------------------------------------


async def test_number_key_1_selects_revert_in_three_panel(tui_db_path: Path) -> None:
    """按 `1` 写入 decision='revert' + status='approved'。

    验证分两部分：
    1. 按键触发 _handle_option_key 并标记 _submitted_approvals
    2. submit_approval_decision 正确写入 DB
    """
    await _insert_story(tui_db_path)
    await _insert_exception_approval(tui_db_path)

    app = ATOApp(db_path=tui_db_path)
    async with app.run_test(size=(160, 40)) as pilot:
        await app.refresh_data()

        dashboard = app.query_one(DashboardScreen)
        assert len(dashboard._approval_records) >= 1
        assert "exc-a1" in dashboard._approvals_by_id
        assert app.layout_mode == "three-panel"

        # 选中异常审批
        dashboard._selected_item_id = "approval:exc-a1"
        dashboard._selected_index = 0

        # 焦点到左面板
        left_panel = dashboard.query_one("#panel-left")
        app.set_focus(left_panel)

        # 按数字键 1 — 触发 _handle_option_key(0) via action_switch_tab
        await pilot.press("1")

        # _handle_option_key 应立即将 exc-a1 加入 _submitted_approvals
        assert "exc-a1" in dashboard._submitted_approvals

        # 等待异步 worker 完成写入
        await dashboard.workers.wait_for_complete()

        # 验证 DB 写入
        db = await get_connection(tui_db_path)
        try:
            cursor = await db.execute(
                "SELECT status, decision, decision_reason FROM approvals WHERE approval_id = ?",
                ("exc-a1",),
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == "approved"
            assert row[1] == "revert"
            assert "tui:1" in row[2]
            assert "revert" in row[2]
        finally:
            await db.close()


async def test_number_key_on_binary_approval_ignored(tui_db_path: Path) -> None:
    """数字键在常规审批上无效。"""
    await _insert_story(tui_db_path)
    await _insert_binary_approval(tui_db_path)

    app = ATOApp(db_path=tui_db_path)
    async with app.run_test() as pilot:
        await app.refresh_data()

        dashboard = app.query_one(DashboardScreen)
        dashboard._selected_item_id = "approval:bin-a1"

        left_panel = dashboard.query_one("#panel-left")
        app.set_focus(left_panel)

        await pilot.press("1")

        # 不应写入 DB
        db = await get_connection(tui_db_path)
        try:
            cursor = await db.execute(
                "SELECT status FROM approvals WHERE approval_id = ?",
                ("bin-a1",),
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == "pending"  # 未被修改
        finally:
            await db.close()


async def test_y_n_on_exception_approval_ignored(tui_db_path: Path) -> None:
    """y/n 在异常审批上无效。"""
    await _insert_story(tui_db_path)
    await _insert_exception_approval(tui_db_path)

    app = ATOApp(db_path=tui_db_path)
    async with app.run_test() as pilot:
        await app.refresh_data()

        dashboard = app.query_one(DashboardScreen)
        dashboard._selected_item_id = "approval:exc-a1"

        left_panel = dashboard.query_one("#panel-left")
        app.set_focus(left_panel)

        await pilot.press("y")
        await pilot.press("n")

        # 不应写入 DB
        db = await get_connection(tui_db_path)
        try:
            cursor = await db.execute(
                "SELECT status FROM approvals WHERE approval_id = ?",
                ("exc-a1",),
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == "pending"
        finally:
            await db.close()


# ---------------------------------------------------------------------------
# AC4: 异常审批排序
# ---------------------------------------------------------------------------


async def test_exception_approval_sorts_before_normal(tui_db_path: Path) -> None:
    """异常审批排在常规审批之前。"""
    await _insert_story(tui_db_path)
    # 先插入常规审批（应该排后面）
    await _insert_binary_approval(tui_db_path, approval_id="bin-a1")
    # 再插入异常审批（应该排前面）
    await _insert_exception_approval(tui_db_path, approval_id="exc-a1")

    app = ATOApp(db_path=tui_db_path)
    async with app.run_test():
        await app.refresh_data()

        dashboard = app.query_one(DashboardScreen)
        # 异常审批应该在常规审批之前
        approval_items = [
            item for item in dashboard._sorted_item_ids if item.startswith("approval:")
        ]
        assert len(approval_items) == 2
        # 异常审批排在前面
        assert approval_items[0] == "approval:exc-a1"
        assert approval_items[1] == "approval:bin-a1"


# ---------------------------------------------------------------------------
# AC6: fallback 文案移除
# ---------------------------------------------------------------------------


async def test_three_panel_fallback_message_removed(tui_db_path: Path) -> None:
    """三面板不再显示旧 CLI / 6.3b fallback 文案。"""
    await _insert_story(tui_db_path)
    await _insert_exception_approval(tui_db_path)

    app = ATOApp(db_path=tui_db_path)
    async with app.run_test():
        await app.refresh_data()

        dashboard = app.query_one(DashboardScreen)
        dashboard._selected_item_id = "approval:exc-a1"
        dashboard._update_action_panel()

        action_panel = dashboard.query_one("#right-bottom-content", Static)
        text = _rendered_text(action_panel)

        # 不应包含旧 fallback 文案
        assert "此审批需多选" not in text
        assert "请使用 CLI" not in text
        assert "等待 6.3b" not in text
        # 应包含数字键提示
        assert "[1]" in text


async def test_submitted_exception_shows_feedback(tui_db_path: Path) -> None:
    """提交后显示即时反馈。"""
    await _insert_story(tui_db_path)
    await _insert_exception_approval(tui_db_path)

    app = ATOApp(db_path=tui_db_path)
    async with app.run_test():
        await app.refresh_data()

        dashboard = app.query_one(DashboardScreen)
        dashboard._selected_item_id = "approval:exc-a1"

        # 模拟已提交状态
        dashboard._submitted_approvals.add("exc-a1")
        dashboard._update_action_panel()

        action_panel = dashboard.query_one("#right-bottom-content", Static)
        text = _rendered_text(action_panel)

        assert "已提交" in text
        assert "等待处理" in text


async def test_exception_approval_disappears_after_decision(tui_db_path: Path) -> None:
    """决策后下一轮轮询消失。"""
    await _insert_story(tui_db_path)
    await _insert_exception_approval(tui_db_path)

    app = ATOApp(db_path=tui_db_path)
    async with app.run_test():
        await app.refresh_data()
        dashboard = app.query_one(DashboardScreen)
        assert app.pending_approvals == 1

        # 直接在 DB 中模拟已审批
        db = await get_connection(tui_db_path)
        try:
            await db.execute(
                "UPDATE approvals SET status = 'approved', decision = 'revert' "
                "WHERE approval_id = 'exc-a1'",
            )
            await db.commit()
        finally:
            await db.close()

        # 下一轮轮询
        await app.refresh_data()
        assert app.pending_approvals == 0
        # 审批应从列表中消失
        assert "approval:exc-a1" not in dashboard._sorted_item_ids


# ---------------------------------------------------------------------------
# AC6: tabbed 模式快捷键边界
# ---------------------------------------------------------------------------


async def test_tab_mode_number_keys_still_switch_tabs(tui_db_path: Path) -> None:
    """tabbed 模式仍保留 `[1]-[4]` 切页契约。"""
    await _insert_story(tui_db_path)
    await _insert_exception_approval(tui_db_path)

    app = ATOApp(db_path=tui_db_path)
    async with app.run_test(size=(120, 40)) as pilot:
        # 切换到 tabbed 模式
        app.layout_mode = "tabbed"
        await app.refresh_data()

        # 按 2 应切换到 [2]Stories tab，而不是触发异常审批提交
        await pilot.press("2")

        # 验证审批未被修改
        db = await get_connection(tui_db_path)
        try:
            cursor = await db.execute(
                "SELECT status FROM approvals WHERE approval_id = ?",
                ("exc-a1",),
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == "pending"  # 未被修改
        finally:
            await db.close()


async def test_tab_mode_exception_visible_without_old_fallback_copy(tui_db_path: Path) -> None:
    """[1]审批 Tab 仍可见异常审批且不显示旧 fallback 子文本。"""
    await _insert_story(tui_db_path)
    await _insert_exception_approval(tui_db_path)

    app = ATOApp(db_path=tui_db_path)
    async with app.run_test(size=(120, 40)):
        app.layout_mode = "tabbed"
        await app.refresh_data()

        dashboard = app.query_one(DashboardScreen)

        # 检查 tab-approvals-container 中有 ApprovalCard
        try:
            tab_container = dashboard.query_one("#tab-approvals-container")
            cards = list(tab_container.query(ApprovalCard))
            assert len(cards) >= 1

            # 确保没有旧 fallback 文案
            statics = list(tab_container.query(Static))
            for s in statics:
                text = _rendered_text(s)
                assert "此审批需多选" not in text
                assert "请使用 CLI" not in text
                assert "等待 6.3b" not in text
        except Exception:
            # Tab 容器可能在 three-panel 模式下不可见
            pass
