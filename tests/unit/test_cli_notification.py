"""test_cli_notification — 错误格式 + 异常审批展示 + 里程碑通知 CLI 测试（Story 4.4）。"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

from ato.cli import _format_cli_error, app
from ato.models.db import get_connection, init_db, insert_approval, insert_story
from ato.models.schemas import ApprovalRecord, StoryRecord

_NOW = datetime.now(tz=UTC)
runner = CliRunner()


def _init_db_sync(db_path: Path) -> None:
    asyncio.run(init_db(db_path))


def _setup_approval_sync(
    db_path: Path,
    *,
    approval_id: str = "aaaa1111-2222-3333-4444-555566667777",
    story_id: str = "test-story-1",
    approval_type: str = "regression_failure",
    risk_level: str | None = "high",
    recommended_action: str | None = "fix_forward",
    payload_dict: dict[str, object] | None = None,
) -> None:
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
                        current_phase="regression",
                        created_at=_NOW,
                        updated_at=_NOW,
                    ),
                )
            payload = json.dumps(payload_dict) if payload_dict else None
            await insert_approval(
                db,
                ApprovalRecord(
                    approval_id=approval_id,
                    story_id=story_id,
                    approval_type=approval_type,
                    status="pending",
                    payload=payload,
                    created_at=_NOW,
                    recommended_action=recommended_action,
                    risk_level=risk_level,  # type: ignore[arg-type]
                ),
            )
        finally:
            await db.close()

    asyncio.run(_inner())


class TestFormatCliError:
    """统一错误格式测试。"""

    def test_format_cli_error_string_options(self) -> None:
        """字符串选项格式正确。"""
        result = _format_cli_error("数据库不存在", "运行 `ato init`")
        assert "发生了什么：数据库不存在" in result
        assert "你的选项：运行 `ato init`" in result

    def test_format_cli_error_list_options(self) -> None:
        """列表选项用 / 连接。"""
        result = _format_cli_error("无效选项", ["retry", "skip", "abort"])
        assert "发生了什么：无效选项" in result
        assert "你的选项：retry / skip / abort" in result


class TestApprovalDetailRegressionFailure:
    """regression_failure 的三要素展示。"""

    def test_approval_detail_regression_failure(self, tmp_path: Path) -> None:
        db_path = tmp_path / ".ato" / "state.db"
        _init_db_sync(db_path)
        _setup_approval_sync(
            db_path,
            approval_type="regression_failure",
            risk_level="high",
            payload_dict={"blocked_stories": ["s1", "s2"]},
        )

        result = runner.invoke(app, ["approval-detail", "aaaa1111", "--db-path", str(db_path)])
        assert result.exit_code == 0
        assert "发生了什么" in result.output
        assert "影响范围" in result.output
        assert "你的选项" in result.output


class TestApprovalDetailBlockingAbnormal:
    """blocking_abnormal 的三要素展示。"""

    def test_approval_detail_blocking_abnormal(self, tmp_path: Path) -> None:
        db_path = tmp_path / ".ato" / "state.db"
        _init_db_sync(db_path)
        _setup_approval_sync(
            db_path,
            approval_id="bbbb2222-3333-4444-5555-666677778888",
            approval_type="blocking_abnormal",
            risk_level="medium",
            recommended_action="human_review",
            payload_dict={"blocking_count": 5, "threshold": 3},
        )

        result = runner.invoke(app, ["approval-detail", "bbbb2222", "--db-path", str(db_path)])
        assert result.exit_code == 0
        assert "发生了什么" in result.output
        assert "影响范围" in result.output


class TestApprovalDetailNotFound:
    """approval 不存在时的错误输出。"""

    def test_approval_detail_not_found(self, tmp_path: Path) -> None:
        db_path = tmp_path / ".ato" / "state.db"
        _init_db_sync(db_path)

        result = runner.invoke(app, ["approval-detail", "zzzz0000", "--db-path", str(db_path)])
        assert result.exit_code == 1
        assert "发生了什么" in result.output


class TestApprovalDetailAmbiguousPrefix:
    """approval ID 前缀歧义时报错。"""

    def test_approval_detail_ambiguous_prefix(self, tmp_path: Path) -> None:
        db_path = tmp_path / ".ato" / "state.db"
        _init_db_sync(db_path)
        # 插入两个相同前缀的 approval
        _setup_approval_sync(
            db_path,
            approval_id="aaaa1111-0000-0000-0000-000000000001",
            approval_type="timeout",
            risk_level=None,
            recommended_action="continue_waiting",
        )
        _setup_approval_sync(
            db_path,
            approval_id="aaaa1111-0000-0000-0000-000000000002",
            approval_type="timeout",
            risk_level=None,
            recommended_action="continue_waiting",
        )

        result = runner.invoke(app, ["approval-detail", "aaaa1111", "--db-path", str(db_path)])
        assert result.exit_code == 1


class TestApprovalDetailShortPrefixRejected:
    """少于 4 字符前缀被拒绝。"""

    def test_approval_detail_short_prefix_rejected(self, tmp_path: Path) -> None:
        db_path = tmp_path / ".ato" / "state.db"
        _init_db_sync(db_path)
        _setup_approval_sync(db_path)

        result = runner.invoke(app, ["approval-detail", "aaa", "--db-path", str(db_path)])
        assert result.exit_code == 1


class TestApprovalDetailNeedsHumanReviewFallback:
    """needs_human_review 推荐操作与 ato approve 合法选项一致（retry）。"""

    def test_approval_detail_needs_human_review_fallback(self, tmp_path: Path) -> None:
        db_path = tmp_path / ".ato" / "state.db"
        _init_db_sync(db_path)
        _setup_approval_sync(
            db_path,
            approval_id="cccc3333-4444-5555-6666-777788889999",
            approval_type="needs_human_review",
            risk_level=None,
            recommended_action="retry",
        )

        result = runner.invoke(app, ["approval-detail", "cccc3333", "--db-path", str(db_path)])
        assert result.exit_code == 0
        # needs_human_review 不在异常类型列表，使用简化展示
        assert "needs_human_review" in result.output


class TestApprovalDetailNormalType:
    """非异常类型的简化展示。"""

    def test_approval_detail_normal_type(self, tmp_path: Path) -> None:
        db_path = tmp_path / ".ato" / "state.db"
        _init_db_sync(db_path)
        _setup_approval_sync(
            db_path,
            approval_id="dddd4444-5555-6666-7777-888899990000",
            approval_type="merge_authorization",
            risk_level=None,
            recommended_action="approve",
        )

        result = runner.invoke(app, ["approval-detail", "dddd4444", "--db-path", str(db_path)])
        assert result.exit_code == 0
        assert "merge_authorization" in result.output
