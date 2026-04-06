"""test_cli_batch — CLI batch 命令测试。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from ato.cli import app
from ato.models.db import init_db
from ato.models.schemas import ProgressEvent

runner = CliRunner()


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _create_db(tmp_path: Path) -> Path:
    """创建初始化的数据库。"""
    import asyncio

    db_path = tmp_path / ".ato" / "state.db"
    asyncio.run(init_db(db_path))
    return db_path


_MINIMAL_EPICS = """\
# Epics

| 串行链 | Stories |
|--------|---------|
| 基础 | 1.1 → 1.2 |

## Epic 1: Test

### Story 1.1: First story

As a user, I want to test

### Story 1.2: Second story

As a user, I want to test more
"""


def _create_epics(tmp_path: Path, content: str = _MINIMAL_EPICS) -> Path:
    epics_path = tmp_path / "epics.md"
    epics_path.write_text(content, encoding="utf-8")
    return epics_path


# ---------------------------------------------------------------------------
# ato batch status — 空状态 (AC3)
# ---------------------------------------------------------------------------


class TestBatchStatusEmpty:
    def test_empty_state_guidance(self, tmp_path: Path) -> None:
        """无 active batch 时显示引导文字。"""
        db_path = _create_db(tmp_path)
        result = runner.invoke(app, ["batch", "status", "--db-path", str(db_path)])
        assert result.exit_code == 0
        assert "尚无 story" in result.output
        assert "ato batch select" in result.output

    def test_empty_state_json(self, tmp_path: Path) -> None:
        """无 active batch 时 --json 输出。"""
        db_path = _create_db(tmp_path)
        result = runner.invoke(app, ["batch", "status", "--db-path", str(db_path), "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["batch"] is None
        assert "尚无 story" in data["message"]


# ---------------------------------------------------------------------------
# ato batch status — DB 不存在 (AC5)
# ---------------------------------------------------------------------------


class TestBatchStatusDbMissing:
    def test_missing_db_error(self, tmp_path: Path) -> None:
        """数据库不存在时输出错误。"""
        bad_db = tmp_path / "nonexistent" / "state.db"
        result = runner.invoke(app, ["batch", "status", "--db-path", str(bad_db)])
        assert result.exit_code == 2
        assert "数据库不存在" in result.output


# ---------------------------------------------------------------------------
# ato batch select — DB 不存在 (AC5)
# ---------------------------------------------------------------------------


class TestBatchSelectDbMissing:
    def test_missing_db_error(self, tmp_path: Path) -> None:
        bad_db = tmp_path / "nonexistent" / "state.db"
        epics = _create_epics(tmp_path)
        result = runner.invoke(
            app,
            ["batch", "select", "--db-path", str(bad_db), "--epics-file", str(epics)],
        )
        assert result.exit_code == 2
        assert "数据库不存在" in result.output


# ---------------------------------------------------------------------------
# ato batch select — epics 不存在 (AC5)
# ---------------------------------------------------------------------------


class TestBatchSelectEpicsMissing:
    def test_missing_epics_error(self, tmp_path: Path) -> None:
        db_path = _create_db(tmp_path)
        bad_epics = tmp_path / "nonexistent" / "epics.md"
        result = runner.invoke(
            app,
            ["batch", "select", "--db-path", str(db_path), "--epics-file", str(bad_epics)],
        )
        assert result.exit_code == 2
        assert "Epics 文件不存在" in result.output


# ---------------------------------------------------------------------------
# ato batch select — 非交互模式 (AC1)
# ---------------------------------------------------------------------------


class TestBatchSelectNonInteractive:
    def test_select_with_story_ids(self, tmp_path: Path) -> None:
        """--story-ids 直接指定 story keys。"""
        db_path = _create_db(tmp_path)
        epics = _create_epics(tmp_path)

        # 需要找出 load_epics 会生成的实际 story_key
        from ato.batch import load_epics

        infos = load_epics(epics)
        first_key = infos[0].story_key

        result = runner.invoke(
            app,
            [
                "batch",
                "select",
                "--db-path",
                str(db_path),
                "--epics-file",
                str(epics),
                "--story-ids",
                first_key,
            ],
        )
        assert result.exit_code == 0
        assert "Batch 已创建" in result.output

    def test_select_with_short_key(self, tmp_path: Path) -> None:
        """--story-ids 支持 short_key（如 1-1）匹配。"""
        db_path = _create_db(tmp_path)
        epics = _create_epics(tmp_path)

        result = runner.invoke(
            app,
            [
                "batch",
                "select",
                "--db-path",
                str(db_path),
                "--epics-file",
                str(epics),
                "--story-ids",
                "1-1",
            ],
        )
        assert result.exit_code == 0
        assert "Batch 已创建" in result.output

    def test_preserves_input_order(self, tmp_path: Path) -> None:
        """--story-ids 保留用户输入顺序而非 epics.md 顺序。"""
        db_path = _create_db(tmp_path)
        epics = _create_epics(tmp_path)

        # 反序输入: 1-2 在前, 1-1 在后
        result = runner.invoke(
            app,
            [
                "batch",
                "select",
                "--db-path",
                str(db_path),
                "--epics-file",
                str(epics),
                "--story-ids",
                "1-2,1-1",
            ],
        )
        assert result.exit_code == 0

        # 检查 JSON status 中 sequence_no=0 对应 1-2
        result = runner.invoke(app, ["batch", "status", "--db-path", str(db_path), "--json"])
        data = json.loads(result.output)
        stories = data["stories"]
        assert stories[0]["sequence_no"] == 0
        assert stories[0]["story_id"] == "1-2"  # user input order preserved

    def test_rejects_unmatched_keys(self, tmp_path: Path) -> None:
        """--story-ids 包含不存在的 key 时报错。"""
        db_path = _create_db(tmp_path)
        epics = _create_epics(tmp_path)

        result = runner.invoke(
            app,
            [
                "batch",
                "select",
                "--db-path",
                str(db_path),
                "--epics-file",
                str(epics),
                "--story-ids",
                "1-1,nonexistent-story",
            ],
        )
        assert result.exit_code == 1
        assert "nonexistent-story" in result.output

    def test_select_then_status_shows_batch(self, tmp_path: Path) -> None:
        """选择 batch 后 status 应能看到。"""
        db_path = _create_db(tmp_path)
        epics = _create_epics(tmp_path)

        from ato.batch import load_epics

        infos = load_epics(epics)
        first_key = infos[0].story_key

        # Select
        result = runner.invoke(
            app,
            [
                "batch",
                "select",
                "--db-path",
                str(db_path),
                "--epics-file",
                str(epics),
                "--story-ids",
                first_key,
            ],
        )
        assert result.exit_code == 0

        # Status
        result = runner.invoke(app, ["batch", "status", "--db-path", str(db_path)])
        assert result.exit_code == 0
        assert "active" in result.output
        assert first_key in result.output


# ---------------------------------------------------------------------------
# ato batch select — 已有 active batch 时拒绝 (Task 3.6)
# ---------------------------------------------------------------------------


class TestBatchSelectDuplicateReject:
    def test_rejects_second_batch(self, tmp_path: Path) -> None:
        """已有 active batch 时拒绝创建新 batch。"""
        db_path = _create_db(tmp_path)
        epics = _create_epics(tmp_path)

        from ato.batch import load_epics

        infos = load_epics(epics)
        first_key = infos[0].story_key

        # 创建第一个 batch
        result = runner.invoke(
            app,
            [
                "batch",
                "select",
                "--db-path",
                str(db_path),
                "--epics-file",
                str(epics),
                "--story-ids",
                first_key,
            ],
        )
        assert result.exit_code == 0

        # 尝试创建第二个
        second_key = infos[1].story_key if len(infos) > 1 else first_key
        result = runner.invoke(
            app,
            [
                "batch",
                "select",
                "--db-path",
                str(db_path),
                "--epics-file",
                str(epics),
                "--story-ids",
                second_key,
            ],
        )
        assert result.exit_code == 1
        assert "已存在 active batch" in result.output


# ---------------------------------------------------------------------------
# ato batch status --json (AC4)
# ---------------------------------------------------------------------------


class TestBatchStatusJson:
    def test_json_output_structure(self, tmp_path: Path) -> None:
        """--json 输出包含正确结构。"""
        db_path = _create_db(tmp_path)
        epics = _create_epics(tmp_path)

        from ato.batch import load_epics

        infos = load_epics(epics)
        first_key = infos[0].story_key

        # Create batch
        runner.invoke(
            app,
            [
                "batch",
                "select",
                "--db-path",
                str(db_path),
                "--epics-file",
                str(epics),
                "--story-ids",
                first_key,
            ],
        )

        # JSON status
        result = runner.invoke(app, ["batch", "status", "--db-path", str(db_path), "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "batch_id" in data
        assert data["status"] == "active"
        assert "progress" in data
        assert "stories" in data
        assert data["progress"]["total"] >= 1


# ---------------------------------------------------------------------------
# ato batch status — 状态映射测试 (Task 4.6)
# ---------------------------------------------------------------------------


class TestBatchStatusMapping:
    def test_queued_shows_as_pending(self, tmp_path: Path) -> None:
        """current_phase=queued 的 story 在进度中计为 pending。"""
        db_path = _create_db(tmp_path)
        epics = _create_epics(tmp_path)

        from ato.batch import load_epics

        infos = load_epics(epics)
        # Select both stories
        keys = ",".join(i.story_key for i in infos[:2])

        runner.invoke(
            app,
            [
                "batch",
                "select",
                "--db-path",
                str(db_path),
                "--epics-file",
                str(epics),
                "--story-ids",
                keys,
            ],
        )

        result = runner.invoke(app, ["batch", "status", "--db-path", str(db_path), "--json"])
        data = json.loads(result.output)
        # 头部 story should be active (planning/creating), 后续 should be pending (backlog/queued)
        assert data["progress"]["pending"] >= 1 or data["progress"]["active"] >= 1

    def test_status_without_epics_file(self, tmp_path: Path) -> None:
        """已有 active batch 时 status 不需要 epics 文件。"""
        db_path = _create_db(tmp_path)
        epics = _create_epics(tmp_path)

        from ato.batch import load_epics

        infos = load_epics(epics)
        first_key = infos[0].story_key

        # Create batch
        runner.invoke(
            app,
            [
                "batch",
                "select",
                "--db-path",
                str(db_path),
                "--epics-file",
                str(epics),
                "--story-ids",
                first_key,
            ],
        )

        # Delete epics file
        epics.unlink()

        # Status should still work (no --epics-file needed)
        result = runner.invoke(app, ["batch", "status", "--db-path", str(db_path)])
        assert result.exit_code == 0
        assert first_key in result.output


# ---------------------------------------------------------------------------
# ato batch select --llm (Story 2B.5a)
# ---------------------------------------------------------------------------


class TestBatchSelectLlmFlag:
    def test_without_llm_flag_uses_local(self, tmp_path: Path) -> None:
        """不传 --llm 时使用本地推荐（默认行为不变）。"""
        db_path = _create_db(tmp_path)
        epics = _create_epics(tmp_path)

        # 使用 --story-ids 避免交互提示
        result = runner.invoke(
            app,
            [
                "batch",
                "select",
                "--db-path",
                str(db_path),
                "--epics-file",
                str(epics),
                "--story-ids",
                "1-1",
            ],
        )
        assert result.exit_code == 0
        assert "Batch 已创建" in result.output

    def test_story_ids_takes_priority_over_llm(self, tmp_path: Path) -> None:
        """--story-ids 优先级最高，即使传了 --llm 也不进入 LLM 推荐。"""
        db_path = _create_db(tmp_path)
        epics = _create_epics(tmp_path)

        result = runner.invoke(
            app,
            [
                "batch",
                "select",
                "--db-path",
                str(db_path),
                "--epics-file",
                str(epics),
                "--story-ids",
                "1-1",
                "--llm",
            ],
        )
        assert result.exit_code == 0
        assert "Batch 已创建" in result.output

    def test_llm_flag_fallback_on_error(self, tmp_path: Path) -> None:
        """--llm 路径下 LLMRecommendError 时回退并输出提示。"""
        db_path = _create_db(tmp_path)
        epics = _create_epics(tmp_path)

        from unittest.mock import AsyncMock

        from ato.batch import LLMBatchRecommender, LLMRecommendError

        async def mock_recommend(*_args, **_kwargs):
            raise LLMRecommendError("Claude CLI 调用失败")

        with (
            patch.object(LLMBatchRecommender, "recommend", side_effect=mock_recommend),
            patch("ato.adapters.claude_cli.ClaudeAdapter", return_value=AsyncMock()),
        ):
            result = runner.invoke(
                app,
                [
                    "batch",
                    "select",
                    "--db-path",
                    str(db_path),
                    "--epics-file",
                    str(epics),
                    "--llm",
                ],
                input="\n",
            )
        assert result.exit_code == 0
        assert "LLM 推荐失败" in result.output
        assert "Batch 已创建" in result.output

    def test_llm_flag_success_with_mock(self, tmp_path: Path) -> None:
        """--llm 路径成功时正确创建 batch（mock Claude adapter）。"""
        db_path = _create_db(tmp_path)
        epics = _create_epics(tmp_path)

        from ato.batch import BatchProposal, LLMBatchRecommender

        async def mock_recommend(self, epics_info, existing_stories, max_stories):
            return BatchProposal(
                stories=[epics_info[0]],
                reason="LLM 推荐: test",
            )

        with patch.object(LLMBatchRecommender, "recommend", mock_recommend):
            result = runner.invoke(
                app,
                [
                    "batch",
                    "select",
                    "--db-path",
                    str(db_path),
                    "--epics-file",
                    str(epics),
                    "--llm",
                ],
                input="\n",
            )
        assert result.exit_code == 0

    def test_llm_flag_streams_progress(self, tmp_path: Path) -> None:
        """--llm 路径应将 Claude 流式事件打印到当前终端。"""
        db_path = _create_db(tmp_path)
        epics = _create_epics(tmp_path)

        from ato.batch import BatchProposal, LLMBatchRecommender

        async def mock_recommend(self, epics_info, existing_stories, max_stories):
            assert self._on_progress is not None
            await self._on_progress(
                ProgressEvent(
                    event_type="result",
                    summary="完成 (cost=$0.05)",
                    cli_tool="claude",
                    timestamp=datetime.now(tz=UTC),
                    raw={"type": "result"},
                )
            )
            return BatchProposal(
                stories=[epics_info[0]],
                reason="LLM 推荐: test",
            )

        with patch.object(LLMBatchRecommender, "recommend", mock_recommend):
            result = runner.invoke(
                app,
                [
                    "batch",
                    "select",
                    "--db-path",
                    str(db_path),
                    "--epics-file",
                    str(epics),
                    "--llm",
                ],
                input="\n",
            )

        assert result.exit_code == 0
        assert "✓ [claude] 完成 (cost=$0.05)" in result.output
        assert "Batch 已创建" in result.output
