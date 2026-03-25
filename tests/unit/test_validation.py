"""test_validation — DeterministicValidator 与 blocking 阈值 escalation 测试。"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ato.models.db import (
    get_connection,
    get_pending_approvals,
    insert_finding,
    insert_story,
)
from ato.models.schemas import (
    ConfigError,
    FindingRecord,
    FindingSeverity,
    StoryRecord,
    compute_dedup_hash,
)
from ato.validation import (
    count_blocking_findings,
    load_schema,
    maybe_create_blocking_abnormal_approval,
    validate_artifact,
)

_NOW = datetime.now(tz=UTC)


# ---------------------------------------------------------------------------
# load_schema
# ---------------------------------------------------------------------------


class TestLoadSchema:
    def test_load_schema_success(self) -> None:
        """加载 review-findings.json 成功。"""
        schema = load_schema("review-findings.json")
        assert isinstance(schema, dict)
        assert schema["title"] == "Review Findings"
        assert "properties" in schema

    def test_load_schema_not_found(self) -> None:
        """不存在的 schema 抛 ConfigError。"""
        with pytest.raises(ConfigError, match="Schema file not found"):
            load_schema("nonexistent-schema.json")


# ---------------------------------------------------------------------------
# validate_artifact
# ---------------------------------------------------------------------------


class TestValidateArtifact:
    def test_validate_artifact_pass(self) -> None:
        """有效 findings JSON 通过验证。"""
        fixture_path = Path(__file__).parent.parent / "fixtures" / "findings_valid.json"
        with fixture_path.open() as f:
            data = json.load(f)
        result = validate_artifact(data, "review-findings.json")
        assert result.passed is True
        assert len(result.errors) == 0

    def test_validate_artifact_fail(self) -> None:
        """缺少字段 / severity 非法时返回完整 errors 列表。"""
        fixture_path = Path(__file__).parent.parent / "fixtures" / "findings_invalid.json"
        with fixture_path.open() as f:
            data = json.load(f)
        result = validate_artifact(data, "review-findings.json")
        assert result.passed is False
        # 第一个 finding 缺少 rule_id + severity 非法 = 至少 2 个错误
        # 第二个 finding 缺少 file_path = 至少 1 个错误
        assert len(result.errors) >= 3

    def test_validate_artifact_empty_findings(self) -> None:
        """空 findings 数组通过验证。"""
        result = validate_artifact({"findings": []}, "review-findings.json")
        assert result.passed is True

    def test_validate_artifact_missing_findings_key(self) -> None:
        """缺少 findings 键时验证失败。"""
        result = validate_artifact({}, "review-findings.json")
        assert result.passed is False
        assert len(result.errors) >= 1

    def test_validation_performance(self) -> None:
        """验证耗时 ≤1 秒（NFR4）。"""
        data = {
            "findings": [
                {
                    "file_path": f"src/file_{i}.py",
                    "rule_id": f"E{i:03d}",
                    "severity": "blocking" if i % 2 == 0 else "suggestion",
                    "description": f"Finding {i}",
                }
                for i in range(100)
            ]
        }
        start = time.perf_counter()
        result = validate_artifact(data, "review-findings.json")
        elapsed = time.perf_counter() - start
        assert result.passed is True
        assert elapsed <= 1.0, f"Validation took {elapsed:.3f}s, exceeds 1s threshold"


# ---------------------------------------------------------------------------
# Blocking 阈值 escalation
# ---------------------------------------------------------------------------


def _make_story(story_id: str) -> StoryRecord:
    return StoryRecord(
        story_id=story_id,
        title="测试 story",
        status="in_progress",
        current_phase="reviewing",
        created_at=_NOW,
        updated_at=_NOW,
    )


def _make_finding(
    finding_id: str,
    story_id: str,
    *,
    severity: FindingSeverity = "blocking",
    round_num: int = 1,
) -> FindingRecord:
    return FindingRecord(
        finding_id=finding_id,
        story_id=story_id,
        round_num=round_num,
        severity=severity,
        description=f"Finding {finding_id}",
        status="open",
        file_path="src/ato/core.py",
        rule_id="E001",
        dedup_hash=compute_dedup_hash("src/ato/core.py", "E001", severity, f"Finding {finding_id}"),
        created_at=_NOW,
    )


class TestBlockingThreshold:
    async def test_below_threshold(self, initialized_db_path: Path) -> None:
        """blocking 数量未超阈值，不创建 approval。"""
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("s-bt"))
            # 插入 3 个 blocking findings（阈值 10）
            for i in range(3):
                await insert_finding(db, _make_finding(f"f-bt-{i}", "s-bt"))
            result = await maybe_create_blocking_abnormal_approval(db, "s-bt", 1, threshold=10)
            assert result is False
            approvals = await get_pending_approvals(db)
            assert len(approvals) == 0
        finally:
            await db.close()

    async def test_above_threshold(self, initialized_db_path: Path) -> None:
        """blocking 数量超阈值，创建 blocking_abnormal approval。"""
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("s-at"))
            # 插入 11 个 blocking findings（阈值 10）
            for i in range(11):
                await insert_finding(db, _make_finding(f"f-at-{i}", "s-at"))
            result = await maybe_create_blocking_abnormal_approval(db, "s-at", 1, threshold=10)
            assert result is True
            approvals = await get_pending_approvals(db)
            assert len(approvals) == 1
            assert approvals[0].approval_type == "blocking_abnormal"
        finally:
            await db.close()

    async def test_threshold_creates_approval_with_payload(self, initialized_db_path: Path) -> None:
        """验证 payload 结构：blocking_count, threshold, round_num。"""
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("s-pay"))
            for i in range(5):
                await insert_finding(db, _make_finding(f"f-pay-{i}", "s-pay"))
            result = await maybe_create_blocking_abnormal_approval(db, "s-pay", 1, threshold=3)
            assert result is True
            approvals = await get_pending_approvals(db)
            assert len(approvals) == 1
            payload = json.loads(approvals[0].payload or "{}")
            assert payload["blocking_count"] == 5
            assert payload["threshold"] == 3
            assert payload["round_num"] == 1
        finally:
            await db.close()

    async def test_inprocess_nudge_path(self, initialized_db_path: Path) -> None:
        """进程内 Nudge.notify() 分支验证。"""
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("s-nudge"))
            for i in range(5):
                await insert_finding(db, _make_finding(f"f-nudge-{i}", "s-nudge"))
            mock_nudge = MagicMock()
            result = await maybe_create_blocking_abnormal_approval(
                db, "s-nudge", 1, threshold=3, nudge=mock_nudge
            )
            assert result is True
            mock_nudge.notify.assert_called_once()
        finally:
            await db.close()

    async def test_external_nudge_path(self, initialized_db_path: Path) -> None:
        """进程外 send_external_nudge() 分支验证。"""
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("s-ext"))
            for i in range(5):
                await insert_finding(db, _make_finding(f"f-ext-{i}", "s-ext"))
            with patch("ato.nudge.send_external_nudge") as mock_send:
                result = await maybe_create_blocking_abnormal_approval(
                    db, "s-ext", 1, threshold=3, orchestrator_pid=12345
                )
                assert result is True
                mock_send.assert_called_once_with(12345)
        finally:
            await db.close()

    async def test_idempotent_no_duplicate_approval(self, initialized_db_path: Path) -> None:
        """同一 story 连续调用两次不产生重复 pending approval。"""
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("s-idem"))
            for i in range(5):
                await insert_finding(db, _make_finding(f"f-idem-{i}", "s-idem"))
            # 第一次调用
            r1 = await maybe_create_blocking_abnormal_approval(db, "s-idem", 1, threshold=3)
            assert r1 is True
            # 第二次调用——应幂等，不新增 approval
            r2 = await maybe_create_blocking_abnormal_approval(db, "s-idem", 1, threshold=3)
            assert r2 is True
            approvals = await get_pending_approvals(db)
            blocking_approvals = [a for a in approvals if a.approval_type == "blocking_abnormal"]
            assert len(blocking_approvals) == 1
        finally:
            await db.close()

    async def test_different_round_creates_separate_approval(
        self, initialized_db_path: Path
    ) -> None:
        """不同轮次各自独立创建 approval，不被幂等逻辑吞掉。"""
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("s-rnd"))
            # round 1: 5 blocking findings
            for i in range(5):
                await insert_finding(db, _make_finding(f"f-rnd1-{i}", "s-rnd", round_num=1))
            r1 = await maybe_create_blocking_abnormal_approval(db, "s-rnd", 1, threshold=3)
            assert r1 is True

            # round 2: 4 blocking findings
            for i in range(4):
                await insert_finding(db, _make_finding(f"f-rnd2-{i}", "s-rnd", round_num=2))
            r2 = await maybe_create_blocking_abnormal_approval(db, "s-rnd", 2, threshold=3)
            assert r2 is True

            # 应该有 2 条独立的 pending blocking_abnormal
            approvals = await get_pending_approvals(db)
            blocking_approvals = [a for a in approvals if a.approval_type == "blocking_abnormal"]
            assert len(blocking_approvals) == 2
            round_nums = {json.loads(a.payload or "{}")["round_num"] for a in blocking_approvals}
            assert round_nums == {1, 2}
        finally:
            await db.close()

    async def test_count_blocking_findings(self, initialized_db_path: Path) -> None:
        """count_blocking_findings 正确统计 blocking 数量。"""
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, _make_story("s-cnt"))
            # 3 blocking + 2 suggestion
            for i in range(3):
                await insert_finding(
                    db, _make_finding(f"f-cnt-b-{i}", "s-cnt", severity="blocking")
                )
            for i in range(2):
                await insert_finding(
                    db, _make_finding(f"f-cnt-s-{i}", "s-cnt", severity="suggestion")
                )
            count = await count_blocking_findings(db, "s-cnt", 1)
            assert count == 3
        finally:
            await db.close()
