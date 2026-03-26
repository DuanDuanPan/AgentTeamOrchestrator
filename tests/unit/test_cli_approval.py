"""test_cli_approval — ato approvals / ato approve CLI 命令测试（Story 4.1）。"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

from ato.cli import app
from ato.models.db import get_connection, init_db, insert_approval, insert_story
from ato.models.schemas import ApprovalRecord, StoryRecord

_NOW = datetime.now(tz=UTC)
runner = CliRunner()


def _init_db_sync(db_path: Path) -> None:
    asyncio.run(init_db(db_path))


def _setup_story_and_approval_sync(
    db_path: Path,
    *,
    story_id: str = "test-story-1",
    approval_id: str = "aaaa1111-2222-3333-4444-555566667777",
    approval_type: str = "session_timeout",
    status: str = "pending",
    recommended_action: str | None = "restart",
    risk_level: str | None = "medium",
) -> None:
    """同步创建 story + approval 的 helper。"""

    async def _inner() -> None:
        db = await get_connection(db_path)
        try:
            from ato.models.db import get_story

            existing = await get_story(db, story_id)
            if existing is None:
                await insert_story(
                    db,
                    StoryRecord(
                        story_id=story_id,
                        title="Test Story",
                        status="in_progress",
                        current_phase="developing",
                        created_at=_NOW,
                        updated_at=_NOW,
                    ),
                )
            await insert_approval(
                db,
                ApprovalRecord(
                    approval_id=approval_id,
                    story_id=story_id,
                    approval_type=approval_type,
                    status=status,
                    payload='{"task_id": "t1", "options": ["restart", "resume"]}',
                    created_at=_NOW,
                    recommended_action=recommended_action,
                    risk_level=risk_level,
                ),
            )
        finally:
            await db.close()

    asyncio.run(_inner())


class TestAtoApprovalsEmpty:
    def test_no_pending_shows_checkmark(self, tmp_path: Path) -> None:
        """无 pending 时输出 ✔。"""
        db_path = tmp_path / ".ato" / "state.db"
        _init_db_sync(db_path)

        result = runner.invoke(app, ["approvals", "--db-path", str(db_path)])
        assert result.exit_code == 0
        assert "✔ 无待处理审批" in result.stdout


class TestAtoApprovalsList:
    def test_pending_shows_table(self, tmp_path: Path) -> None:
        """有 pending 时 rich 表格输出。"""
        db_path = tmp_path / ".ato" / "state.db"
        _init_db_sync(db_path)
        _setup_story_and_approval_sync(db_path)

        result = runner.invoke(app, ["approvals", "--db-path", str(db_path)])
        assert result.exit_code == 0
        # rich 表格可能截断长文本，检查 ID 前缀和 story_id
        assert "aaaa11" in result.stdout
        assert "test-story-1" in result.stdout

    def test_json_output(self, tmp_path: Path) -> None:
        """--json 输出 JSON。"""
        db_path = tmp_path / ".ato" / "state.db"
        _init_db_sync(db_path)
        _setup_story_and_approval_sync(db_path)

        result = runner.invoke(app, ["approvals", "--db-path", str(db_path), "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert "approvals" in data
        assert len(data["approvals"]) == 1
        assert data["approvals"][0]["approval_type"] == "session_timeout"


class TestAtoApproveSuccess:
    def test_approve_success(self, tmp_path: Path) -> None:
        """正常审批流程。"""
        db_path = tmp_path / ".ato" / "state.db"
        _init_db_sync(db_path)
        _setup_story_and_approval_sync(db_path)

        result = runner.invoke(
            app,
            [
                "approve",
                "aaaa1111",
                "--decision",
                "restart",
                "--reason",
                "超时重启",
                "--db-path",
                str(db_path),
            ],
        )
        assert result.exit_code == 0
        assert "审批已提交" in result.stdout

        # 验证 DB 更新
        async def _verify() -> None:
            db = await get_connection(db_path)
            try:
                from ato.models.db import get_approval_by_id

                updated = await get_approval_by_id(db, "aaaa1111-2222-3333-4444-555566667777")
                assert updated.status == "approved"
                assert updated.decision == "restart"
                assert updated.decision_reason == "超时重启"
            finally:
                await db.close()

        asyncio.run(_verify())


class TestAtoApproveAmbiguousPrefix:
    def test_ambiguous_prefix(self, tmp_path: Path) -> None:
        """多个前缀命中时提示用户补长前缀。"""
        db_path = tmp_path / ".ato" / "state.db"
        _init_db_sync(db_path)
        _setup_story_and_approval_sync(
            db_path,
            approval_id="aaaa1111-0000-0000-0000-000000000001",
        )
        _setup_story_and_approval_sync(
            db_path,
            approval_id="aaaa1111-0000-0000-0000-000000000002",
        )

        result = runner.invoke(
            app,
            [
                "approve",
                "aaaa1111",
                "--decision",
                "restart",
                "--db-path",
                str(db_path),
            ],
        )
        assert result.exit_code == 1
        output = result.stdout + (result.stderr or "")
        assert "更长的前缀" in output


class TestAtoApproveNotFound:
    def test_not_found(self, tmp_path: Path) -> None:
        """approval 不存在时错误信息。"""
        db_path = tmp_path / ".ato" / "state.db"
        _init_db_sync(db_path)

        result = runner.invoke(
            app,
            [
                "approve",
                "zzzz9999",
                "--decision",
                "restart",
                "--db-path",
                str(db_path),
            ],
        )
        assert result.exit_code == 1
        output = result.stdout + (result.stderr or "")
        assert "未找到" in output


class TestAtoApproveAlreadyDecided:
    def test_already_decided(self, tmp_path: Path) -> None:
        """重复决策时错误信息。"""
        db_path = tmp_path / ".ato" / "state.db"
        _init_db_sync(db_path)
        _setup_story_and_approval_sync(
            db_path,
            status="approved",
        )

        result = runner.invoke(
            app,
            [
                "approve",
                "aaaa1111",
                "--decision",
                "restart",
                "--db-path",
                str(db_path),
            ],
        )
        assert result.exit_code == 1
        output = result.stdout + (result.stderr or "")
        assert "已处理" in output


class TestAtoApproveInvalidDecision:
    def test_invalid_decision_rejected(self, tmp_path: Path) -> None:
        """无效 decision 被拒绝，不写入 DB。"""
        db_path = tmp_path / ".ato" / "state.db"
        _init_db_sync(db_path)
        _setup_story_and_approval_sync(db_path)  # session_timeout, options=restart/resume

        result = runner.invoke(
            app,
            [
                "approve",
                "aaaa1111",
                "--decision",
                "foo_invalid",
                "--db-path",
                str(db_path),
            ],
        )
        assert result.exit_code == 1
        output = result.stdout + (result.stderr or "")
        assert "无效的决策选项" in output
        assert "restart" in output  # 应显示合法选项

        # 验证 approval 仍为 pending
        async def _verify() -> None:
            db = await get_connection(db_path)
            try:
                from ato.models.db import get_approval_by_id

                a = await get_approval_by_id(db, "aaaa1111")
                assert a.status == "pending"
            finally:
                await db.close()

        asyncio.run(_verify())
