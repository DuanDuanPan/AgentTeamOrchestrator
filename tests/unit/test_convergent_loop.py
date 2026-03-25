"""test_convergent_loop — ConvergentLoop 首轮 review 单元测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from ato.config import ConvergentLoopConfig
from ato.convergent_loop import ConvergentLoop
from ato.models.db import (
    get_connection,
    get_findings_by_story,
    get_pending_approvals,
    insert_story,
)
from ato.models.schemas import (
    AdapterResult,
    BmadFinding,
    BmadParseResult,
    BmadSkillType,
    ConvergentLoopResult,
    StoryRecord,
    TransitionEvent,
)

_NOW = datetime.now(tz=UTC)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_story(
    story_id: str = "story-cl-test",
    worktree_path: str | None = "/tmp/wt",
) -> StoryRecord:
    return StoryRecord(
        story_id=story_id,
        title="CL test story",
        status="in_progress",
        current_phase="reviewing",
        worktree_path=worktree_path,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _make_adapter_result(**overrides: Any) -> AdapterResult:
    defaults: dict[str, Any] = {
        "status": "success",
        "exit_code": 0,
        "duration_ms": 500,
        "text_result": "## Review\nNo issues found.",
        "cost_usd": 0.02,
        "input_tokens": 200,
        "output_tokens": 100,
    }
    defaults.update(overrides)
    return AdapterResult.model_validate(defaults)


def _make_finding(
    *,
    severity: str = "blocking",
    description: str = "test issue",
    file_path: str = "src/foo.py",
    rule_id: str = "R001",
    line: int | None = None,
) -> BmadFinding:
    return BmadFinding(
        severity=severity,
        category="test",
        description=description,
        file_path=file_path,
        rule_id=rule_id,
        line=line,
    )


def _make_parse_result(
    *,
    verdict: str = "approved",
    findings: list[BmadFinding] | None = None,
) -> BmadParseResult:
    return BmadParseResult(
        skill_type=BmadSkillType.CODE_REVIEW,
        verdict=verdict,
        findings=findings or [],
        parser_mode="deterministic",
        raw_markdown_hash="abc123",
        raw_output_preview="preview...",
        parsed_at=_NOW,
    )


def _make_loop(
    db_path: Any,
    *,
    subprocess_result: AdapterResult | None = None,
    parse_result: BmadParseResult | None = None,
    nudge: Any = None,
    blocking_threshold: int = 10,
) -> tuple[ConvergentLoop, AsyncMock, AsyncMock, AsyncMock]:
    """Create a ConvergentLoop with mock dependencies.

    Returns (loop, mock_subprocess_mgr, mock_bmad_adapter, mock_transition_queue).
    """
    mock_sub = AsyncMock()
    mock_sub.dispatch_with_retry = AsyncMock(
        return_value=subprocess_result or _make_adapter_result()
    )

    mock_bmad = AsyncMock()
    mock_bmad.parse = AsyncMock(
        return_value=parse_result or _make_parse_result()
    )

    mock_tq = AsyncMock()
    mock_tq.submit = AsyncMock()

    loop = ConvergentLoop(
        db_path=db_path,
        subprocess_mgr=mock_sub,
        bmad_adapter=mock_bmad,
        transition_queue=mock_tq,
        config=ConvergentLoopConfig(),
        blocking_threshold=blocking_threshold,
        nudge=nudge,
    )
    return loop, mock_sub, mock_bmad, mock_tq


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestConvergentLoopResultModel:
    """test_convergent_loop_result_model — ConvergentLoopResult 构建和验证。"""

    def test_basic_construction(self) -> None:
        r = ConvergentLoopResult(
            story_id="s1",
            round_num=1,
            converged=True,
            findings_total=0,
            blocking_count=0,
            suggestion_count=0,
            open_count=0,
        )
        assert r.story_id == "s1"
        assert r.round_num == 1
        assert r.converged is True
        assert r.closed_count == 0
        assert r.new_count == 0

    def test_with_findings(self) -> None:
        r = ConvergentLoopResult(
            story_id="s2",
            round_num=1,
            converged=False,
            findings_total=5,
            blocking_count=3,
            suggestion_count=2,
            open_count=5,
            new_count=5,
        )
        assert r.findings_total == 5
        assert r.blocking_count == 3

    def test_strict_validation_rejects_wrong_types(self) -> None:
        with pytest.raises((TypeError, ValueError)):
            ConvergentLoopResult(
                story_id=123,  # type: ignore[arg-type]
                round_num=1,
                converged=True,
                findings_total=0,
                blocking_count=0,
                suggestion_count=0,
                open_count=0,
            )


class TestFirstReviewZeroFindings:
    """0 findings → converged=True，提交 review_pass。"""

    @pytest.mark.asyncio
    async def test_converges(self, initialized_db_path: Any) -> None:
        story = _make_story()
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, story)
        finally:
            await db.close()

        parse_result = _make_parse_result(verdict="approved", findings=[])
        loop, _sub, _bmad, mock_tq = _make_loop(
            initialized_db_path, parse_result=parse_result
        )

        result = await loop.run_first_review(story.story_id, "/tmp/wt")

        assert result.converged is True
        assert result.round_num == 1
        assert result.findings_total == 0
        assert result.blocking_count == 0

        # Verify review_pass event submitted
        mock_tq.submit.assert_called_once()
        event: TransitionEvent = mock_tq.submit.call_args[0][0]
        assert event.event_name == "review_pass"
        assert event.source == "agent"
        assert event.submitted_at is not None


class TestFirstReviewBlockingFindings:
    """有 blocking → converged=False，提交 review_fail。"""

    @pytest.mark.asyncio
    async def test_not_converged(self, initialized_db_path: Any) -> None:
        story = _make_story()
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, story)
        finally:
            await db.close()

        findings = [
            _make_finding(severity="blocking", description="missing null check"),
            _make_finding(severity="suggestion", description="consider renaming"),
        ]
        parse_result = _make_parse_result(
            verdict="changes_requested", findings=findings,
        )
        loop, _sub, _bmad, mock_tq = _make_loop(
            initialized_db_path, parse_result=parse_result
        )

        result = await loop.run_first_review(story.story_id, "/tmp/wt")

        assert result.converged is False
        assert result.findings_total == 2
        assert result.blocking_count == 1
        assert result.suggestion_count == 1
        assert result.open_count == 2

        # Verify review_fail event submitted
        mock_tq.submit.assert_called_once()
        event: TransitionEvent = mock_tq.submit.call_args[0][0]
        assert event.event_name == "review_fail"
        assert event.source == "agent"


class TestFirstReviewOnlySuggestions:
    """test_first_review_only_suggestions_converges — 仅 suggestion → converged=True。"""

    @pytest.mark.asyncio
    async def test_converges_with_suggestions(self, initialized_db_path: Any) -> None:
        story = _make_story()
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, story)
        finally:
            await db.close()

        findings = [
            _make_finding(severity="suggestion", description="naming convention"),
            _make_finding(severity="suggestion", description="add docstring", rule_id="R002"),
        ]
        parse_result = _make_parse_result(
            verdict="changes_requested", findings=findings,
        )
        loop, _sub, _bmad, mock_tq = _make_loop(
            initialized_db_path, parse_result=parse_result
        )

        result = await loop.run_first_review(story.story_id, "/tmp/wt")

        assert result.converged is True
        assert result.blocking_count == 0
        assert result.suggestion_count == 2
        assert result.findings_total == 2

        # review_pass because suggestions don't block convergence
        event: TransitionEvent = mock_tq.submit.call_args[0][0]
        assert event.event_name == "review_pass"


class TestFirstReviewRequiresWorktreePath:
    """test_first_review_requires_resolved_worktree_path — 无 worktree_path 时直接失败。"""

    @pytest.mark.asyncio
    async def test_fails_without_worktree(self, initialized_db_path: Any) -> None:
        # Story with no worktree_path
        story = _make_story(worktree_path=None)
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, story)
        finally:
            await db.close()

        loop, mock_sub, _bmad, _tq = _make_loop(initialized_db_path)

        with pytest.raises(ValueError, match="Cannot resolve worktree path"):
            await loop.run_first_review(story.story_id, None)

        # No subprocess should have been dispatched
        mock_sub.dispatch_with_retry.assert_not_called()


class TestFirstReviewFindingsPersisted:
    """findings 正确写入 SQLite，round_num=1, status=open。"""

    @pytest.mark.asyncio
    async def test_findings_in_db(self, initialized_db_path: Any) -> None:
        story = _make_story()
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, story)
        finally:
            await db.close()

        findings = [
            _make_finding(
                severity="blocking", description="null pointer",
                file_path="src/a.py", rule_id="NP01",
            ),
            _make_finding(
                severity="suggestion", description="naming",
                file_path="src/b.py", rule_id="NM01",
            ),
        ]
        parse_result = _make_parse_result(verdict="changes_requested", findings=findings)
        loop, _, _, _ = _make_loop(initialized_db_path, parse_result=parse_result)

        await loop.run_first_review(story.story_id, "/tmp/wt")

        # Verify findings in database
        db = await get_connection(initialized_db_path)
        try:
            persisted = await get_findings_by_story(db, story.story_id, round_num=1)
        finally:
            await db.close()

        assert len(persisted) == 2
        for f in persisted:
            assert f.round_num == 1
            assert f.status == "open"
            assert f.story_id == story.story_id


class TestFirstReviewDedupHash:
    """test_first_review_dedup_hash_computed — 每个 finding 的 dedup_hash 非空。"""

    @pytest.mark.asyncio
    async def test_dedup_hash_nonempty(self, initialized_db_path: Any) -> None:
        story = _make_story()
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, story)
        finally:
            await db.close()

        findings = [
            _make_finding(severity="blocking", description="issue one", rule_id="R1"),
        ]
        parse_result = _make_parse_result(verdict="changes_requested", findings=findings)
        loop, _, _, _ = _make_loop(initialized_db_path, parse_result=parse_result)

        await loop.run_first_review(story.story_id, "/tmp/wt")

        db = await get_connection(initialized_db_path)
        try:
            persisted = await get_findings_by_story(db, story.story_id)
        finally:
            await db.close()

        assert len(persisted) == 1
        assert persisted[0].dedup_hash != ""
        assert len(persisted[0].dedup_hash) == 64  # SHA256 hex


class TestFirstReviewBlockingThresholdEscalation:
    """blocking 数量超阈值 → approval 创建。"""

    @pytest.mark.asyncio
    async def test_escalation_default_threshold(
        self, initialized_db_path: Any,
    ) -> None:
        story = _make_story()
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, story)
        finally:
            await db.close()

        # Create 11 blocking findings (threshold default = 10)
        findings = [
            _make_finding(
                severity="blocking",
                description=f"issue {i}",
                file_path=f"src/f{i}.py",
                rule_id=f"R{i:03d}",
            )
            for i in range(11)
        ]
        parse_result = _make_parse_result(
            verdict="changes_requested", findings=findings,
        )
        loop, _, _, _ = _make_loop(
            initialized_db_path, parse_result=parse_result,
        )

        result = await loop.run_first_review(story.story_id, "/tmp/wt")

        assert result.blocking_count == 11
        assert result.converged is False

        # Verify blocking_abnormal approval was created
        db = await get_connection(initialized_db_path)
        try:
            approvals = await get_pending_approvals(db)
        finally:
            await db.close()

        blocking_approvals = [
            a for a in approvals if a.approval_type == "blocking_abnormal"
        ]
        assert len(blocking_approvals) == 1

    @pytest.mark.asyncio
    async def test_custom_threshold_respected(
        self, initialized_db_path: Any,
    ) -> None:
        """blocking_threshold 配置实际生效，而非硬编码。"""
        story = _make_story(story_id="story-custom-thresh")
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, story)
        finally:
            await db.close()

        # 4 blocking findings with threshold=3 → should escalate
        findings = [
            _make_finding(
                severity="blocking",
                description=f"custom {i}",
                file_path=f"src/c{i}.py",
                rule_id=f"C{i:03d}",
            )
            for i in range(4)
        ]
        parse_result = _make_parse_result(
            verdict="changes_requested", findings=findings,
        )
        loop, _, _, _ = _make_loop(
            initialized_db_path,
            parse_result=parse_result,
            blocking_threshold=3,
        )

        result = await loop.run_first_review(
            story.story_id, "/tmp/wt",
        )
        assert result.blocking_count == 4

        db = await get_connection(initialized_db_path)
        try:
            approvals = await get_pending_approvals(db)
        finally:
            await db.close()

        blocking_approvals = [
            a for a in approvals if a.approval_type == "blocking_abnormal"
        ]
        assert len(blocking_approvals) == 1

    @pytest.mark.asyncio
    async def test_below_custom_threshold_no_escalation(
        self, initialized_db_path: Any,
    ) -> None:
        """blocking 数量 <= 自定义阈值时不创建 approval。"""
        story = _make_story(story_id="story-below-thresh")
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, story)
        finally:
            await db.close()

        # 3 blocking findings with threshold=5 → should NOT escalate
        findings = [
            _make_finding(
                severity="blocking",
                description=f"below {i}",
                file_path=f"src/b{i}.py",
                rule_id=f"B{i:03d}",
            )
            for i in range(3)
        ]
        parse_result = _make_parse_result(
            verdict="changes_requested", findings=findings,
        )
        loop, _, _, _ = _make_loop(
            initialized_db_path,
            parse_result=parse_result,
            blocking_threshold=5,
        )

        await loop.run_first_review(story.story_id, "/tmp/wt")

        db = await get_connection(initialized_db_path)
        try:
            approvals = await get_pending_approvals(db)
        finally:
            await db.close()

        blocking_approvals = [
            a for a in approvals if a.approval_type == "blocking_abnormal"
        ]
        assert len(blocking_approvals) == 0


class TestFirstReviewParseFailure:
    """test_first_review_parse_failure_creates_approval — BMAD 解析失败 → 创建人工审批。"""

    @pytest.mark.asyncio
    async def test_parse_failure_approval(self, initialized_db_path: Any) -> None:
        story = _make_story()
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, story)
        finally:
            await db.close()

        parse_result = _make_parse_result(verdict="parse_failed")
        parse_result = BmadParseResult(
            skill_type=BmadSkillType.CODE_REVIEW,
            verdict="parse_failed",
            findings=[],
            parser_mode="failed",
            raw_markdown_hash="xyz",
            raw_output_preview="garbled output...",
            parse_error="Could not parse review output",
            parsed_at=_NOW,
        )
        loop, _, _, mock_tq = _make_loop(initialized_db_path, parse_result=parse_result)

        result = await loop.run_first_review(story.story_id, "/tmp/wt")

        # Should not converge
        assert result.converged is False
        assert result.findings_total == 0

        # Should create needs_human_review approval
        db = await get_connection(initialized_db_path)
        try:
            approvals = await get_pending_approvals(db)
        finally:
            await db.close()

        human_review = [a for a in approvals if a.approval_type == "needs_human_review"]
        assert len(human_review) == 1

        # Should NOT submit any transition event (parse failure = manual handling)
        mock_tq.submit.assert_not_called()


class TestValidationHookSkipsWithoutPayload:
    """test_first_review_validation_hook_skips_without_artifact_payload —
    当前 MVP 无结构化 artifact 时不调用 validate_artifact()。
    """

    @pytest.mark.asyncio
    async def test_skips_validation(self, initialized_db_path: Any) -> None:
        story = _make_story()
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, story)
        finally:
            await db.close()

        parse_result = _make_parse_result(verdict="approved", findings=[])
        loop, mock_sub, _, _ = _make_loop(initialized_db_path, parse_result=parse_result)

        # No artifact_payload → validation gate should be skipped
        result = await loop.run_first_review(story.story_id, "/tmp/wt")

        assert result.converged is True
        # Subprocess was dispatched (validation gate didn't block)
        mock_sub.dispatch_with_retry.assert_called_once()


class TestValidationFailureSubmitsValidateFail:
    """显式提供无效 artifact payload 时提交 validate_fail 回退到 creating。"""

    @pytest.mark.asyncio
    async def test_validation_failure_early_return(
        self, initialized_db_path: Any,
    ) -> None:
        story = _make_story()
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, story)
        finally:
            await db.close()

        loop, mock_sub, _, mock_tq = _make_loop(initialized_db_path)

        # Invalid artifact payload (missing required fields)
        bad_payload: dict[str, Any] = {"not_valid": True}
        result = await loop.run_first_review(
            story.story_id, "/tmp/wt", artifact_payload=bad_payload
        )

        # Should NOT converge and should return early
        assert result.converged is False
        assert result.findings_total == 0

        # Subprocess should NOT have been dispatched (early return)
        mock_sub.dispatch_with_retry.assert_not_called()

        # validate_fail event → story rolls back to creating
        mock_tq.submit.assert_called_once()
        event: TransitionEvent = mock_tq.submit.call_args[0][0]
        assert event.event_name == "validate_fail"
        assert event.source == "agent"


class TestFirstReviewStructlogFields:
    """test_first_review_structlog_fields — 验证日志包含 round_num, findings_total, open_count。"""

    @pytest.mark.asyncio
    async def test_structlog_output(self, initialized_db_path: Any, caplog: Any) -> None:
        story = _make_story()
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, story)
        finally:
            await db.close()

        findings = [
            _make_finding(severity="blocking", description="log test issue"),
        ]
        parse_result = _make_parse_result(verdict="changes_requested", findings=findings)
        loop, _, _, _ = _make_loop(initialized_db_path, parse_result=parse_result)

        # Configure structlog to capture to a list for assertion
        captured: list[dict[str, Any]] = []

        class CapturingLogger:
            """Logger that captures all events."""

            def _log(self, event: str, **kwargs: Any) -> None:
                captured.append({"event": event, **kwargs})

            def info(self, event: str, **kwargs: Any) -> None:
                self._log(event, **kwargs)

            def warning(self, event: str, **kwargs: Any) -> None:
                self._log(event, **kwargs)

            def error(self, event: str, **kwargs: Any) -> None:
                self._log(event, **kwargs)

            def bind(self, **kwargs: Any) -> CapturingLogger:
                return self

        # Patch the module-level logger
        import ato.convergent_loop as cl_module

        old_logger = cl_module.logger
        cl_module.logger = CapturingLogger()  # type: ignore[assignment]
        try:
            await loop.run_first_review(story.story_id, "/tmp/wt")
        finally:
            cl_module.logger = old_logger

        # Verify key log events
        events_by_name = {c["event"]: c for c in captured}

        # round_start
        assert "convergent_loop_round_start" in events_by_name
        start = events_by_name["convergent_loop_round_start"]
        assert start["round_num"] == 1
        assert start["story_id"] == story.story_id

        # round_complete
        assert "convergent_loop_round_complete" in events_by_name
        complete = events_by_name["convergent_loop_round_complete"]
        assert complete["round_num"] == 1
        assert complete["findings_total"] == 1
        assert complete["open_count"] == 1
        assert complete["blocking_count"] == 1

        # needs_fix (because there's a blocking finding)
        assert "convergent_loop_needs_fix" in events_by_name


class TestTransitionQueueInteraction:
    """test mock TransitionQueue interaction — 验证正确事件提交。"""

    @pytest.mark.asyncio
    async def test_review_pass_event(self, initialized_db_path: Any) -> None:
        """review_pass 事件：source=agent, submitted_at 已填充。"""
        story = _make_story()
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, story)
        finally:
            await db.close()

        parse_result = _make_parse_result(verdict="approved", findings=[])
        loop, _, _, mock_tq = _make_loop(initialized_db_path, parse_result=parse_result)

        await loop.run_first_review(story.story_id, "/tmp/wt")

        mock_tq.submit.assert_called_once()
        event: TransitionEvent = mock_tq.submit.call_args[0][0]
        assert event.event_name == "review_pass"
        assert event.source == "agent"
        assert isinstance(event.submitted_at, datetime)

    @pytest.mark.asyncio
    async def test_review_fail_event(self, initialized_db_path: Any) -> None:
        """review_fail 事件：source=agent, submitted_at 已填充。"""
        story = _make_story()
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, story)
        finally:
            await db.close()

        findings = [_make_finding(severity="blocking")]
        parse_result = _make_parse_result(verdict="changes_requested", findings=findings)
        loop, _, _, mock_tq = _make_loop(initialized_db_path, parse_result=parse_result)

        await loop.run_first_review(story.story_id, "/tmp/wt")

        mock_tq.submit.assert_called_once()
        event: TransitionEvent = mock_tq.submit.call_args[0][0]
        assert event.event_name == "review_fail"
        assert event.source == "agent"
        assert isinstance(event.submitted_at, datetime)

    @pytest.mark.asyncio
    async def test_validate_fail_event(
        self, initialized_db_path: Any,
    ) -> None:
        """validation gate 失败提交 validate_fail 回退到 creating。"""
        story = _make_story()
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, story)
        finally:
            await db.close()

        loop, _, _, mock_tq = _make_loop(initialized_db_path)

        await loop.run_first_review(
            story.story_id, "/tmp/wt", artifact_payload={"bad": True}
        )

        mock_tq.submit.assert_called_once()
        event: TransitionEvent = mock_tq.submit.call_args[0][0]
        assert event.event_name == "validate_fail"
        assert event.source == "agent"
        assert isinstance(event.submitted_at, datetime)


class TestValidateFailFromReviewingStateMachine:
    """用真实状态机验证 validate_fail 从 reviewing 回退到 creating。"""

    @pytest.mark.asyncio
    async def test_validate_fail_transitions_reviewing_to_creating(self) -> None:
        """状态机接受 reviewing → creating via validate_fail。"""
        from ato.state_machine import StoryLifecycle

        sm = await StoryLifecycle.create()
        # Advance to reviewing
        await sm.send("start_create")
        await sm.send("create_done")
        await sm.send("validate_pass")
        await sm.send("start_dev")
        await sm.send("dev_done")
        assert sm.current_state_value == "reviewing"

        # validate_fail should transition to creating
        await sm.send("validate_fail")
        assert sm.current_state_value == "creating"
