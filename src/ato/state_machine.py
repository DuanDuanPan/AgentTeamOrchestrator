"""state_machine — StoryLifecycle 状态机。

基于 python-statemachine 3.0 async API 实现 Story 生命周期状态推进。
状态机定义 13 个规范阶段与所有合法 transition，配合 save_story_state()
将阶段变更持久化到 SQLite（不自动 commit，由 TransitionQueue 统一事务边界）。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

import aiosqlite
import structlog
from statemachine import State, StateMachine

from ato.models.schemas import StateTransitionError, StoryStatus

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

# ---------------------------------------------------------------------------
# 规范阶段序列 — from_config() 验证的基准
# ---------------------------------------------------------------------------

CANONICAL_PHASES: tuple[str, ...] = (
    "creating",
    "validating",
    "dev_ready",
    "developing",
    "reviewing",
    "fixing",
    "qa_testing",
    "uat",
    "merging",
    "regression",
)
"""配置文件中定义的有序工作流阶段（不含 queued/done/blocked 系统状态）。"""

# ---------------------------------------------------------------------------
# 状态机阶段 → StoryStatus 高层映射
# ---------------------------------------------------------------------------

PHASE_TO_STATUS: dict[str, StoryStatus] = {
    "queued": "backlog",
    "creating": "planning",
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


# ---------------------------------------------------------------------------
# from_config() 的类型协议 — Story 1.3 交付 PhaseDefinition 后自动兼容
# ---------------------------------------------------------------------------


@runtime_checkable
class HasPhaseInfo(Protocol):
    """from_config() 消费的阶段定义协议。"""

    @property
    def name(self) -> str: ...

    @property
    def next_on_success(self) -> str: ...

    @property
    def next_on_failure(self) -> str | None: ...


# 规范 transition 映射：phase_name → (next_on_success, next_on_failure | None)
CANONICAL_TRANSITIONS: dict[str, tuple[str, str | None]] = {
    "creating": ("validating", None),
    "validating": ("dev_ready", "creating"),
    "dev_ready": ("developing", None),
    "developing": ("reviewing", None),
    "reviewing": ("qa_testing", "fixing"),
    "fixing": ("reviewing", None),
    "qa_testing": ("uat", "fixing"),
    "uat": ("merging", None),
    "merging": ("regression", None),
    "regression": ("done", None),
}


# ---------------------------------------------------------------------------
# StoryLifecycle 状态机
# ---------------------------------------------------------------------------


class StoryLifecycle(StateMachine):  # type: ignore[misc]
    """Story 生命周期状态机。

    13 个规范状态，覆盖从 queued（等待启动）到 done（完成）的完整流程，
    以及 blocked（升级阻塞）的 sink state。

    状态图::

        queued ──start_create──→ creating
        creating ──create_done──→ validating
        validating ──validate_pass──→ dev_ready
        validating ──validate_fail──→ creating      ← Convergent Loop 回退
        dev_ready ──start_dev──→ developing
        developing ──dev_done──→ reviewing
        reviewing ──review_pass──→ qa_testing
        reviewing ──review_fail──→ fixing           ← Convergent Loop
        fixing ──fix_done──→ reviewing              ← re-review
        qa_testing ──qa_pass──→ uat
        qa_testing ──qa_fail──→ fixing              ← QA Convergent Loop
        uat ──uat_pass──→ merging
        merging ──merge_done──→ regression
        regression ──regression_pass──→ done
        * ──escalate──→ blocked                     ← 多状态可 escalate（MVP sink）
    """

    # --- States ---
    queued = State(initial=True)
    creating = State()
    validating = State()
    dev_ready = State()
    developing = State()
    reviewing = State()
    fixing = State()
    qa_testing = State()
    uat = State()
    merging = State()
    regression = State()
    done = State(final=True)
    blocked = State(final=True)

    # --- Transitions ---
    start_create = queued.to(creating)
    create_done = creating.to(validating)
    validate_pass = validating.to(dev_ready)
    validate_fail = validating.to(creating)
    start_dev = dev_ready.to(developing)
    dev_done = developing.to(reviewing)
    review_pass = reviewing.to(qa_testing)
    review_fail = reviewing.to(fixing)
    fix_done = fixing.to(reviewing)
    qa_pass = qa_testing.to(uat)
    qa_fail = qa_testing.to(fixing)
    uat_pass = uat.to(merging)
    merge_done = merging.to(regression)
    regression_pass = regression.to(done)

    # escalate: 任何非 final / 非 blocked 状态可升级到 blocked
    escalate = (
        queued.to(blocked)
        | creating.to(blocked)
        | validating.to(blocked)
        | dev_ready.to(blocked)
        | developing.to(blocked)
        | reviewing.to(blocked)
        | fixing.to(blocked)
        | qa_testing.to(blocked)
        | uat.to(blocked)
        | merging.to(blocked)
        | regression.to(blocked)
    )

    # --- Async callbacks（structlog 记录状态变更） ---

    async def on_enter_state(self, target: State, source: State | None = None) -> None:
        """进入任意状态时记录日志。"""
        source_id = source.id if source is not None else "none"
        logger.info(
            "state_entered",
            story_lifecycle="transition",
            source=source_id,
            target=target.id,
        )

    async def on_exit_state(self, source: State, target: State) -> None:
        """离开任意状态时记录日志。"""
        logger.info(
            "state_exited",
            story_lifecycle="transition",
            source=source.id,
            target=target.id,
        )

    async def send(self, event: str, *args: object, **kwargs: object) -> object:
        """发送事件，非法 transition 时记录拒绝日志后重新抛出（AC #3）。"""
        from statemachine.exceptions import TransitionNotAllowed

        try:
            result: object = await super().send(event, *args, **kwargs)
            return result
        except TransitionNotAllowed:
            logger.warning(
                "transition_rejected",
                story_lifecycle="rejection",
                rejected_event=event,
                current_state=self.current_state_value,
            )
            raise

    # --- 工厂方法 ---

    @classmethod
    async def from_config(
        cls,
        phase_definitions: Sequence[HasPhaseInfo],
    ) -> StoryLifecycle:
        """从配置的阶段定义构建并激活状态机。

        验证 ``phase_definitions`` 的有序阶段名与 :data:`CANONICAL_PHASES` 一致，
        并校验每个阶段的 ``next_on_success`` / ``next_on_failure`` 与规范 transition 匹配。

        Args:
            phase_definitions: 有序阶段定义列表，每项须具有
                ``name``、``next_on_success``、``next_on_failure`` 属性。

        Returns:
            已激活初始状态（queued）的 StoryLifecycle 实例。

        Raises:
            StateTransitionError: 阶段序列或 transition 与规范不一致。
        """
        phase_names = tuple(pd.name for pd in phase_definitions)
        if phase_names != CANONICAL_PHASES:
            msg = (
                f"Phase definitions do not match canonical sequence. "
                f"Expected {list(CANONICAL_PHASES)}, got {list(phase_names)}"
            )
            raise StateTransitionError(msg)

        # 校验每个阶段的 transition 与规范状态机一致
        for pd in phase_definitions:
            expected = CANONICAL_TRANSITIONS.get(pd.name)
            if expected is None:
                continue
            expected_success, expected_failure = expected
            if pd.next_on_success != expected_success:
                msg = (
                    f"Phase '{pd.name}' next_on_success mismatch: "
                    f"expected '{expected_success}', got '{pd.next_on_success}'"
                )
                raise StateTransitionError(msg)
            if pd.next_on_failure != expected_failure:
                msg = (
                    f"Phase '{pd.name}' next_on_failure mismatch: "
                    f"expected {expected_failure!r}, got {pd.next_on_failure!r}"
                )
                raise StateTransitionError(msg)

        sm = cls()
        await sm.activate_initial_state()
        return sm

    @classmethod
    async def create(cls) -> StoryLifecycle:
        """创建并激活状态机（不验证配置，用于测试或独立使用）。

        Returns:
            已激活初始状态（queued）的 StoryLifecycle 实例。
        """
        sm = cls()
        await sm.activate_initial_state()
        return sm


# ---------------------------------------------------------------------------
# 持久化桥接
# ---------------------------------------------------------------------------


async def save_story_state(
    db: aiosqlite.Connection,
    story_id: str,
    phase_name: str,
) -> None:
    """将状态机当前阶段持久化到 SQLite（不 commit）。

    将 ``phase_name``（状态机 ``current_state_value``）映射为高层
    ``StoryStatus``，更新 stories 表的 ``status`` 和 ``current_phase`` 列。

    **不会执行 ``db.commit()``**——调用方（TransitionQueue consumer）
    负责在 ``send() → save_story_state() → commit()`` 序列中统一提交。

    Args:
        db: 活跃的 aiosqlite 连接。
        story_id: Story 唯一标识。
        phase_name: 状态机 ``current_state_value``（如 ``"developing"``）。

    Raises:
        StateTransitionError: ``phase_name`` 不在 PHASE_TO_STATUS 映射中。
    """
    status = PHASE_TO_STATUS.get(phase_name)
    if status is None:
        msg = f"Unknown phase '{phase_name}', not in PHASE_TO_STATUS mapping"
        raise StateTransitionError(msg)

    # 使用 db.py 的不自动 commit 路径
    from ato.models.db import update_story_status

    await update_story_status(db, story_id, status, phase_name, commit=False)

    logger.info(
        "story_state_saved",
        story_id=story_id,
        phase=phase_name,
        status=status,
        committed=False,
    )
