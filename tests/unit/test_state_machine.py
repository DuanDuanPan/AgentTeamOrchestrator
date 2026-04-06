"""test_state_machine — StoryLifecycle 状态机单元测试。

覆盖 100% transition（~20+ 测试）：
- 每个合法 transition 独立测试
- 非法 transition 拒绝
- Happy path 完整流程
- Convergent Loop 路径
- from_config() 阶段名校验
- save_story_state() 持久化（不自动 commit）
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

import aiosqlite
import pytest
from statemachine.exceptions import TransitionNotAllowed

from ato.models.db import get_story, insert_story
from ato.models.schemas import StateTransitionError, StoryRecord
from ato.state_machine import (
    CANONICAL_PHASES,
    CANONICAL_TRANSITIONS,
    PHASE_TO_STATUS,
    StoryLifecycle,
    save_story_state,
)

_NOW = datetime.now(tz=UTC)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FakePhaseDefinition:
    """硬编码 PhaseDefinition fixture — 满足 HasPhaseInfo Protocol。"""

    name: str
    next_on_success: str = "done"
    next_on_failure: str | None = None


def _canonical_phase_defs() -> list[FakePhaseDefinition]:
    """返回与 CANONICAL_PHASES + CANONICAL_TRANSITIONS 一致的 fixture 列表。"""
    defs: list[FakePhaseDefinition] = []
    for name in CANONICAL_PHASES:
        success, failure = CANONICAL_TRANSITIONS[name]
        defs.append(
            FakePhaseDefinition(name=name, next_on_success=success, next_on_failure=failure)
        )
    return defs


async def _make_sm() -> StoryLifecycle:
    """创建并激活状态机（测试辅助）。"""
    return await StoryLifecycle.create()


async def _advance_to(sm: StoryLifecycle, target_state: str) -> None:
    """将状态机推进到指定状态（沿 happy path）。"""
    happy_path: list[str] = [
        "start_create",
        "create_done",
        "design_done",
        "validate_pass",
        "start_dev",
        "dev_done",
        "review_pass",
        "qa_pass",
        "uat_pass",
        "merge_done",
        "regression_pass",
    ]
    state_after_event: dict[str, str] = {
        "start_create": "creating",
        "create_done": "designing",
        "design_done": "validating",
        "validate_pass": "dev_ready",
        "start_dev": "developing",
        "dev_done": "reviewing",
        "review_pass": "qa_testing",
        "qa_pass": "uat",
        "uat_pass": "merging",
        "merge_done": "regression",
        "regression_pass": "done",
    }
    for event in happy_path:
        if sm.current_state_value == target_state:
            return
        await sm.send(event)
        if state_after_event[event] == target_state:
            return


# ---------------------------------------------------------------------------
# activate_initial_state
# ---------------------------------------------------------------------------


class TestActivateInitialState:
    async def test_initial_state_is_queued(self) -> None:
        sm = await _make_sm()
        assert sm.current_state_value == "queued"

    async def test_configuration_contains_queued(self) -> None:
        sm = await _make_sm()
        config_values = {s.id for s in sm.configuration}
        assert "queued" in config_values


# ---------------------------------------------------------------------------
# 合法 Transition 独立测试（每个 transition 至少 1 次）
# ---------------------------------------------------------------------------


class TestLegalTransitions:
    """每个合法 transition 的独立测试。"""

    async def test_start_create(self) -> None:
        sm = await _make_sm()
        await sm.send("start_create")
        assert sm.current_state_value == "creating"

    async def test_create_done(self) -> None:
        sm = await _make_sm()
        await _advance_to(sm, "creating")
        await sm.send("create_done")
        assert sm.current_state_value == "designing"

    async def test_validate_pass(self) -> None:
        sm = await _make_sm()
        await _advance_to(sm, "validating")
        await sm.send("validate_pass")
        assert sm.current_state_value == "dev_ready"

    async def test_validate_fail(self) -> None:
        """Convergent Loop 回退：validating → creating。"""
        sm = await _make_sm()
        await _advance_to(sm, "validating")
        await sm.send("validate_fail")
        assert sm.current_state_value == "creating"

    async def test_start_dev(self) -> None:
        sm = await _make_sm()
        await _advance_to(sm, "dev_ready")
        await sm.send("start_dev")
        assert sm.current_state_value == "developing"

    async def test_dev_done(self) -> None:
        sm = await _make_sm()
        await _advance_to(sm, "developing")
        await sm.send("dev_done")
        assert sm.current_state_value == "reviewing"

    async def test_review_pass(self) -> None:
        sm = await _make_sm()
        await _advance_to(sm, "reviewing")
        await sm.send("review_pass")
        assert sm.current_state_value == "qa_testing"

    async def test_review_fail(self) -> None:
        """Convergent Loop：reviewing → fixing。"""
        sm = await _make_sm()
        await _advance_to(sm, "reviewing")
        await sm.send("review_fail")
        assert sm.current_state_value == "fixing"

    async def test_fix_done(self) -> None:
        """Re-review：fixing → reviewing。"""
        sm = await _make_sm()
        await _advance_to(sm, "reviewing")
        await sm.send("review_fail")
        await sm.send("fix_done")
        assert sm.current_state_value == "reviewing"

    async def test_qa_fix_done(self) -> None:
        """QA-origin fixing 成功后应回到 qa_testing。"""
        sm = await _make_sm()
        await _advance_to(sm, "qa_testing")
        await sm.send("qa_fail")
        await sm.send("qa_fix_done")
        assert sm.current_state_value == "qa_testing"

    async def test_uat_fix_done(self) -> None:
        """UAT-origin fixing 成功后应回到 uat。"""
        sm = await _make_sm()
        await _advance_to(sm, "uat")
        await sm.send("uat_fail")
        await sm.send("uat_fix_done")
        assert sm.current_state_value == "uat"

    async def test_regression_fix_done(self) -> None:
        """Regression-origin fixing 成功后应回到 regression。"""
        sm = await _make_sm()
        await _advance_to(sm, "regression")
        await sm.send("regression_fail")
        await sm.send("regression_fix_done")
        assert sm.current_state_value == "regression"

    async def test_qa_pass(self) -> None:
        sm = await _make_sm()
        await _advance_to(sm, "qa_testing")
        await sm.send("qa_pass")
        assert sm.current_state_value == "uat"

    async def test_qa_fail(self) -> None:
        """QA Convergent Loop：qa_testing → fixing。"""
        sm = await _make_sm()
        await _advance_to(sm, "qa_testing")
        await sm.send("qa_fail")
        assert sm.current_state_value == "fixing"

    async def test_uat_pass(self) -> None:
        sm = await _make_sm()
        await _advance_to(sm, "uat")
        await sm.send("uat_pass")
        assert sm.current_state_value == "merging"

    async def test_merge_done(self) -> None:
        sm = await _make_sm()
        await _advance_to(sm, "merging")
        await sm.send("merge_done")
        assert sm.current_state_value == "regression"

    async def test_regression_pass(self) -> None:
        sm = await _make_sm()
        await _advance_to(sm, "regression")
        await sm.send("regression_pass")
        assert sm.current_state_value == "done"


# ---------------------------------------------------------------------------
# escalate Transition（从各个状态到 blocked）
# ---------------------------------------------------------------------------


class TestEscalateTransitions:
    """escalate 从各状态到 blocked 的测试。"""

    _ESCALATABLE_STATES: ClassVar[list[str]] = [
        "queued",
        "creating",
        "designing",
        "validating",
        "dev_ready",
        "developing",
        "reviewing",
        "fixing",
        "qa_testing",
        "uat",
        "merging",
        "regression",
    ]

    @pytest.mark.parametrize("state", _ESCALATABLE_STATES)
    async def test_escalate_from_state(self, state: str) -> None:
        sm = await _make_sm()
        if state == "fixing":
            await _advance_to(sm, "reviewing")
            await sm.send("review_fail")
        else:
            await _advance_to(sm, state)
        assert sm.current_state_value == state
        await sm.send("escalate")
        assert sm.current_state_value == "blocked"


# ---------------------------------------------------------------------------
# 非法 Transition 拒绝测试
# ---------------------------------------------------------------------------


class TestIllegalTransitions:
    """非法 transition 拒绝：状态不变，抛出 TransitionNotAllowed。"""

    async def test_queued_rejects_create_done(self) -> None:
        sm = await _make_sm()
        with pytest.raises(TransitionNotAllowed):
            await sm.send("create_done")
        assert sm.current_state_value == "queued"

    async def test_queued_rejects_validate_pass(self) -> None:
        sm = await _make_sm()
        with pytest.raises(TransitionNotAllowed):
            await sm.send("validate_pass")
        assert sm.current_state_value == "queued"

    async def test_creating_rejects_start_dev(self) -> None:
        sm = await _make_sm()
        await _advance_to(sm, "creating")
        with pytest.raises(TransitionNotAllowed):
            await sm.send("start_dev")
        assert sm.current_state_value == "creating"

    async def test_validating_rejects_dev_done(self) -> None:
        sm = await _make_sm()
        await _advance_to(sm, "validating")
        with pytest.raises(TransitionNotAllowed):
            await sm.send("dev_done")
        assert sm.current_state_value == "validating"

    async def test_developing_rejects_review_pass(self) -> None:
        sm = await _make_sm()
        await _advance_to(sm, "developing")
        with pytest.raises(TransitionNotAllowed):
            await sm.send("review_pass")
        assert sm.current_state_value == "developing"

    async def test_reviewing_rejects_qa_pass(self) -> None:
        sm = await _make_sm()
        await _advance_to(sm, "reviewing")
        with pytest.raises(TransitionNotAllowed):
            await sm.send("qa_pass")
        assert sm.current_state_value == "reviewing"

    async def test_done_rejects_all_events(self) -> None:
        """done 是 final state，拒绝所有事件。"""
        sm = await _make_sm()
        await _advance_to(sm, "done")
        for event in ("start_create", "escalate", "dev_done"):
            with pytest.raises(TransitionNotAllowed):
                await sm.send(event)
        assert sm.current_state_value == "done"

    async def test_blocked_rejects_all_events(self) -> None:
        """blocked 是 final state (MVP sink)，拒绝所有事件。"""
        sm = await _make_sm()
        await sm.send("escalate")
        assert sm.current_state_value == "blocked"
        for event in ("start_create", "escalate", "dev_done"):
            with pytest.raises(TransitionNotAllowed):
                await sm.send(event)
        assert sm.current_state_value == "blocked"

    async def test_fixing_rejects_review_pass(self) -> None:
        sm = await _make_sm()
        await _advance_to(sm, "reviewing")
        await sm.send("review_fail")
        assert sm.current_state_value == "fixing"
        with pytest.raises(TransitionNotAllowed):
            await sm.send("review_pass")
        assert sm.current_state_value == "fixing"

    async def test_rejection_logs_warning(self, capfd: pytest.CaptureFixture[str]) -> None:
        """非法 transition 应记录 structlog warning（AC #3）。"""
        sm = await _make_sm()
        with pytest.raises(TransitionNotAllowed):
            await sm.send("dev_done")
        captured = capfd.readouterr()
        output = captured.out + captured.err
        assert "transition_rejected" in output
        assert "rejected_event=dev_done" in output
        assert "current_state=queued" in output

    async def test_fixing_rejects_review_pass_logs_warning(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """Story 3.3 AC5: fixing 中跳过 fix_done 直接 review_pass → 拒绝 + structlog。"""
        sm = await _make_sm()
        await _advance_to(sm, "reviewing")
        await sm.send("review_fail")
        assert sm.current_state_value == "fixing"
        with pytest.raises(TransitionNotAllowed):
            await sm.send("review_pass")
        assert sm.current_state_value == "fixing"
        captured = capfd.readouterr()
        output = captured.out + captured.err
        assert "transition_rejected" in output
        assert "rejected_event=review_pass" in output
        assert "current_state=fixing" in output


# ---------------------------------------------------------------------------
# Happy Path 完整流程
# ---------------------------------------------------------------------------


class TestHappyPath:
    async def test_full_lifecycle_queued_to_done(self) -> None:
        """完整 happy path：queued → ... → done。"""
        sm = await _make_sm()
        events = [
            ("start_create", "creating"),
            ("create_done", "designing"),
            ("design_done", "validating"),
            ("validate_pass", "dev_ready"),
            ("start_dev", "developing"),
            ("dev_done", "reviewing"),
            ("review_pass", "qa_testing"),
            ("qa_pass", "uat"),
            ("uat_pass", "merging"),
            ("merge_done", "regression"),
            ("regression_pass", "done"),
        ]
        for event, expected_state in events:
            await sm.send(event)
            assert sm.current_state_value == expected_state


# ---------------------------------------------------------------------------
# Convergent Loop 路径
# ---------------------------------------------------------------------------


class TestConvergentLoop:
    async def test_review_fix_review_cycle(self) -> None:
        """reviewing → fixing → reviewing → review_pass → qa_testing。"""
        sm = await _make_sm()
        await _advance_to(sm, "reviewing")

        # Round 1: review_fail → fixing
        await sm.send("review_fail")
        assert sm.current_state_value == "fixing"

        # fix_done → back to reviewing
        await sm.send("fix_done")
        assert sm.current_state_value == "reviewing"

        # Round 2: another cycle
        await sm.send("review_fail")
        assert sm.current_state_value == "fixing"
        await sm.send("fix_done")
        assert sm.current_state_value == "reviewing"

        # Finally pass
        await sm.send("review_pass")
        assert sm.current_state_value == "qa_testing"

    async def test_validate_fail_retry(self) -> None:
        """validating → creating → designing → validating → validate_pass。"""
        sm = await _make_sm()
        await _advance_to(sm, "validating")

        await sm.send("validate_fail")
        assert sm.current_state_value == "creating"

        await sm.send("create_done")
        assert sm.current_state_value == "designing"

        await sm.send("design_done")
        assert sm.current_state_value == "validating"

        await sm.send("validate_pass")
        assert sm.current_state_value == "dev_ready"


# ---------------------------------------------------------------------------
# from_config() 构建与阶段名校验
# ---------------------------------------------------------------------------


class TestFromConfig:
    async def test_correct_phases_creates_sm(self) -> None:
        phases = _canonical_phase_defs()
        sm = await StoryLifecycle.from_config(phases)
        assert sm.current_state_value == "queued"

    async def test_wrong_phase_names_rejected(self) -> None:
        wrong = [FakePhaseDefinition(name="wrong_phase")]
        with pytest.raises(StateTransitionError, match="do not match canonical"):
            await StoryLifecycle.from_config(wrong)

    async def test_missing_phase_rejected(self) -> None:
        """缺少阶段。"""
        incomplete = [FakePhaseDefinition(name=n) for n in CANONICAL_PHASES[:-1]]
        with pytest.raises(StateTransitionError, match="do not match canonical"):
            await StoryLifecycle.from_config(incomplete)

    async def test_extra_phase_rejected(self) -> None:
        """多余阶段。"""
        extra = [*_canonical_phase_defs(), FakePhaseDefinition(name="extra")]
        with pytest.raises(StateTransitionError, match="do not match canonical"):
            await StoryLifecycle.from_config(extra)

    async def test_wrong_order_rejected(self) -> None:
        """阶段顺序错误。"""
        reordered = list(reversed(_canonical_phase_defs()))
        with pytest.raises(StateTransitionError, match="do not match canonical"):
            await StoryLifecycle.from_config(reordered)

    async def test_old_names_rejected(self) -> None:
        """旧阶段名（review_passed / qa）被拒绝。"""
        old_names = [
            "creating",
            "validating",
            "dev_ready",
            "developing",
            "reviewing",
            "fixing",
            "review_passed",
            "qa",
            "uat",
            "merging",
        ]
        old_defs = [FakePhaseDefinition(name=n) for n in old_names]
        with pytest.raises(StateTransitionError, match="do not match canonical"):
            await StoryLifecycle.from_config(old_defs)

    async def test_wrong_next_on_success_rejected(self) -> None:
        """next_on_success 与规范不一致时被拒绝。"""
        defs = _canonical_phase_defs()
        # reviewing.next_on_success 应为 qa_testing，改成 uat
        tampered = [
            FakePhaseDefinition(
                name=d.name,
                next_on_success="uat",
                next_on_failure=d.next_on_failure,
            )
            if d.name == "reviewing"
            else d
            for d in defs
        ]
        with pytest.raises(StateTransitionError, match="next_on_success mismatch"):
            await StoryLifecycle.from_config(tampered)

    async def test_wrong_next_on_failure_rejected(self) -> None:
        """next_on_failure 与规范不一致时被拒绝。"""
        defs = _canonical_phase_defs()
        # validating.next_on_failure 应为 creating，改成 dev_ready
        tampered = [
            FakePhaseDefinition(
                name=d.name,
                next_on_success=d.next_on_success,
                next_on_failure="dev_ready",
            )
            if d.name == "validating"
            else d
            for d in defs
        ]
        with pytest.raises(StateTransitionError, match="next_on_failure mismatch"):
            await StoryLifecycle.from_config(tampered)

    async def test_real_config_from_config(self) -> None:
        """真实 ato.yaml.example → build_phase_definitions → from_config 端到端。"""
        from pathlib import Path

        from ato.config import build_phase_definitions, load_config

        config = load_config(Path("ato.yaml.example"))
        phase_defs = build_phase_definitions(config)
        sm = await StoryLifecycle.from_config(phase_defs)
        assert sm.current_state_value == "queued"


# ---------------------------------------------------------------------------
# save_story_state() 持久化测试
# ---------------------------------------------------------------------------


class TestSaveStoryState:
    async def _insert_test_story(self, db: aiosqlite.Connection) -> str:
        story_id = "test-story-001"
        story = StoryRecord(
            story_id=story_id,
            title="Test Story",
            status="backlog",
            current_phase="queued",
            created_at=_NOW,
            updated_at=_NOW,
        )
        await insert_story(db, story)
        return story_id

    async def test_saves_phase_and_status(self, initialized_db_path: Path) -> None:
        """save_story_state 写入正确的 status 和 current_phase。"""
        async with aiosqlite.connect(initialized_db_path) as db:
            db.row_factory = aiosqlite.Row
            story_id = await self._insert_test_story(db)

            await save_story_state(db, story_id, "developing")
            await db.commit()

            record = await get_story(db, story_id)
            assert record is not None
            assert record.status == "in_progress"
            assert record.current_phase == "developing"

    async def test_does_not_auto_commit(self, initialized_db_path: Path) -> None:
        """save_story_state 不自动 commit — 回滚后数据应消失。"""
        async with aiosqlite.connect(initialized_db_path) as db:
            db.row_factory = aiosqlite.Row
            story_id = await self._insert_test_story(db)

            await save_story_state(db, story_id, "developing")
            # 不 commit，直接回滚
            await db.rollback()

            record = await get_story(db, story_id)
            assert record is not None
            # insert_story 有自己的 commit，所以 story 仍存在，但状态应为原始值
            assert record.status == "backlog"
            assert record.current_phase == "queued"

    async def test_unknown_phase_raises(self, initialized_db_path: Path) -> None:
        """未知阶段名应抛出 StateTransitionError。"""
        async with aiosqlite.connect(initialized_db_path) as db:
            with pytest.raises(StateTransitionError, match="Unknown phase"):
                await save_story_state(db, "any-id", "nonexistent_phase")

    async def test_nonexistent_story_raises(self, initialized_db_path: Path) -> None:
        """对不存在的 story_id 调用 save_story_state 应抛出 ValueError。"""
        async with aiosqlite.connect(initialized_db_path) as db:
            db.row_factory = aiosqlite.Row
            with pytest.raises(ValueError, match="not found"):
                await save_story_state(db, "nonexistent-story", "developing")

    async def test_all_phases_mapped(self) -> None:
        """PHASE_TO_STATUS 覆盖所有 13 个状态 + done + blocked。"""
        sm = await _make_sm()
        all_states = {s.id for s in sm.states}
        mapped = set(PHASE_TO_STATUS.keys())
        assert all_states == mapped, f"Unmapped states: {all_states - mapped}"

    async def test_phase_to_status_values(self) -> None:
        """验证映射表与 story spec 一致。"""
        expected = {
            "queued": "backlog",
            "creating": "planning",
            "designing": "planning",
            "validating": "planning",
            "dev_ready": "ready",
            "developing": "in_progress",
            "reviewing": "review",
            "fixing": "review",
            "qa_testing": "in_progress",
            "uat": "uat",
            "merging": "in_progress",
            "regression": "in_progress",
            "done": "done",
            "blocked": "blocked",
        }
        assert expected == PHASE_TO_STATUS


# ---------------------------------------------------------------------------
# uat_fail Transition (Story 4.3)
# ---------------------------------------------------------------------------


class TestUatFailTransition:
    """uat_fail: uat → fixing (FR48)。"""

    async def test_uat_fail_transitions_to_fixing(self) -> None:
        """uat_fail 应将 story 从 uat 推进到 fixing。"""
        sm = await _make_sm()
        await _advance_to(sm, "uat")
        await sm.send("uat_fail")
        assert sm.current_state_value == "fixing"

    async def test_uat_fail_from_non_uat_rejected(self) -> None:
        """uat_fail 在非 uat 状态下应被拒绝。"""
        sm = await _make_sm()
        await _advance_to(sm, "reviewing")
        with pytest.raises(TransitionNotAllowed):
            await sm.send("uat_fail")
        assert sm.current_state_value == "reviewing"

    async def test_uat_fail_then_convergent_loop(self) -> None:
        """uat → fixing → reviewing → ... 完整 CL 回退路径。"""
        sm = await _make_sm()
        await _advance_to(sm, "uat")

        # uat_fail → fixing
        await sm.send("uat_fail")
        assert sm.current_state_value == "fixing"

        # fixing → reviewing (re-review)
        await sm.send("fix_done")
        assert sm.current_state_value == "reviewing"

        # reviewing → qa_testing
        await sm.send("review_pass")
        assert sm.current_state_value == "qa_testing"

    async def test_canonical_transitions_includes_uat_fail(self) -> None:
        """CANONICAL_TRANSITIONS 中 uat 应有 fail 分支指向 fixing。"""
        success, failure = CANONICAL_TRANSITIONS["uat"]
        assert success == "merging"
        assert failure == "fixing"


# ---------------------------------------------------------------------------
# regression_fail 转换测试 (Story 4.2)
# ---------------------------------------------------------------------------


class TestRegressionFail:
    """regression → fixing 转换测试。"""

    async def test_regression_fail_returns_to_fixing(self) -> None:
        """regression_fail 将状态从 regression 回退到 fixing。"""
        sm = await StoryLifecycle.create()

        # Navigate to regression state
        await sm.send("start_create")
        await sm.send("create_done")
        await sm.send("design_done")
        await sm.send("validate_pass")
        await sm.send("start_dev")
        await sm.send("dev_done")
        await sm.send("review_pass")
        await sm.send("qa_pass")
        await sm.send("uat_pass")
        await sm.send("merge_done")

        assert sm.current_state.id == "regression"  # type: ignore[union-attr]

        # regression_fail → fixing
        await sm.send("regression_fail")
        assert sm.current_state.id == "fixing"  # type: ignore[union-attr]

    async def test_canonical_transitions_regression_has_failure(self) -> None:
        """CANONICAL_TRANSITIONS 中 regression 应该有 fixing 作为 failure 目标。"""
        from ato.state_machine import CANONICAL_TRANSITIONS

        success, failure = CANONICAL_TRANSITIONS["regression"]
        assert success == "done"
        assert failure == "fixing"


# ---------------------------------------------------------------------------
# designing phase 覆盖测试 (Story 9.1 AC#7)
# ---------------------------------------------------------------------------


class TestDesigningPhase:
    """designing 阶段的状态机转换测试。"""

    async def test_designing_to_validating(self) -> None:
        """designing → validating (design_done)。"""
        sm = await _make_sm()
        await _advance_to(sm, "designing")
        assert sm.current_state_value == "designing"
        await sm.send("design_done")
        assert sm.current_state_value == "validating"

    async def test_creating_to_designing(self) -> None:
        """creating → designing (create_done 新目标)。"""
        sm = await _make_sm()
        await _advance_to(sm, "creating")
        assert sm.current_state_value == "creating"
        await sm.send("create_done")
        assert sm.current_state_value == "designing"

    async def test_designing_escalate(self) -> None:
        """designing → blocked (escalate)。"""
        sm = await _make_sm()
        await _advance_to(sm, "designing")
        assert sm.current_state_value == "designing"
        await sm.send("escalate")
        assert sm.current_state_value == "blocked"

    async def test_designing_maps_to_planning_status(self) -> None:
        """designing 映射到 planning 高层状态。"""
        assert PHASE_TO_STATUS["designing"] == "planning"

    async def test_canonical_transitions_creating_points_to_designing(self) -> None:
        """CANONICAL_TRANSITIONS 中 creating.success 应指向 designing。"""
        success, failure = CANONICAL_TRANSITIONS["creating"]
        assert success == "designing"
        assert failure is None

    async def test_canonical_transitions_designing_points_to_validating(self) -> None:
        """CANONICAL_TRANSITIONS 中 designing.success 应指向 validating。"""
        success, failure = CANONICAL_TRANSITIONS["designing"]
        assert success == "validating"
        assert failure is None
