"""recovery — 崩溃恢复（≤30s 目标）。

Architecture Decision 7: 优雅停止标记法。
区分崩溃与正常重启的唯一判据是 task 状态：
- status='running' → 崩溃恢复（PID/artifact 四路分类）
- status='paused'  → 正常恢复（直接重调度）
- status='failed' 且有 pending crash_recovery approval → needs_human（不自动恢复）
"""

from __future__ import annotations

import asyncio
import errno
import json
import os
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import structlog

from ato.adapters.base import BaseAdapter
from ato.models.schemas import (
    RecoveryAction,
    RecoveryClassification,
    RecoveryMode,
    RecoveryResult,
    TaskRecord,
    TransitionEvent,
)
from ato.nudge import Nudge
from ato.subprocess_mgr import SubprocessManager
from ato.transition_queue import TransitionQueue

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Phase → success 事件映射（与 state_machine.py 一致）
# ---------------------------------------------------------------------------

_PHASE_SUCCESS_EVENT: dict[str, str] = {
    "creating": "create_done",
    "validating": "validate_pass",
    "dev_ready": "start_dev",
    "developing": "dev_done",
    "reviewing": "review_pass",
    "fixing": "fix_done",
    "qa_testing": "qa_pass",
    "uat": "uat_pass",
    "merging": "merge_done",
    "regression": "regression_pass",
}

_PHASE_FAIL_EVENT: dict[str, str] = {
    "validating": "validate_fail",
    "reviewing": "review_fail",
    "qa_testing": "qa_fail",
}

# Phase → BMAD skill type 映射（仅 convergent_loop phases）
_PHASE_BMAD_SKILL: dict[str, str] = {
    "validating": "story_validation",
    "reviewing": "code_review",
    "qa_testing": "qa_report",
}

# Phase-specific prompt 模板：确保 CLI 输出匹配 BMAD 解析器期望的结构
_CONVERGENT_LOOP_PROMPTS: dict[str, str] = {
    "reviewing": (
        "Review all code in the worktree at {worktree_path}. "
        "Story: {story_id}. Perform a full code review.\n\n"
        "Output format: Start with a summary line showing counts for "
        "intent_gap, bad_spec, patch, defer categories. "
        "Then list findings under ## Intent Gaps, ## Bad Spec, "
        "## Patch, ## Defer section headings. "
        "If no issues found, state: Clean review - no findings."
    ),
    "validating": (
        "Validate the story artifacts in the worktree at {worktree_path}. "
        "Story: {story_id}. Perform a full story validation.\n\n"
        "Output format: Start with 结果: PASS or 结果: FAIL. "
        "Include sections: ## 摘要, ## 发现的关键问题, "
        "## 已应用增强, ## 剩余风险, ## 最终结论. "
        "Number each issue as ## 1. Title, ## 2. Title etc."
    ),
    "qa_testing": (
        "Perform a test quality review on the worktree at {worktree_path}. "
        "Story: {story_id}. Evaluate test coverage and quality.\n\n"
        "Output format: Include **Recommendation**: Approve/Request Changes/Block "
        "and **Quality Score**: N/100. "
        "List findings under ## Critical Issues (Must Fix) and "
        "## Recommendations (Should Fix) sections. "
        "For each issue include **Severity**: P0-P3, "
        "**Location**: `file.py:line`, **Criterion**: criterion_name. "
        "Also include a Quality Criteria Assessment table."
    ),
}

_PID_MONITOR_INTERVAL = 5.0


def _is_pid_alive(pid: int) -> bool:
    """检测 PID 是否仍在运行。macOS/Linux 通用。"""
    try:
        os.kill(pid, 0)
        return True
    except OSError as e:
        if e.errno == errno.ESRCH:
            return False
        if e.errno == errno.EPERM:
            return True
        raise


def _artifact_exists(task: TaskRecord) -> bool:
    """检测 task 的 expected_artifact 是否存在。"""
    if not task.expected_artifact:
        return False
    return Path(task.expected_artifact).exists()


def _is_interactive_phase(phase: str) -> bool:
    """判断 phase 是否属于 Interactive Session（内置 fallback）。"""
    return phase in {"uat", "developing"}


def _create_adapter(cli_tool: Literal["claude", "codex"]) -> BaseAdapter:
    """按 cli_tool 创建对应的 CLI adapter 实例。"""
    if cli_tool == "claude":
        from ato.adapters.claude_cli import ClaudeAdapter

        return ClaudeAdapter()
    from ato.adapters.codex_cli import CodexAdapter

    return CodexAdapter()


class RecoveryEngine:
    """崩溃恢复引擎。

    四路分类（status='running'）：
    1. PID 存活 → reattach（异步 PID 监控）
    2. artifact 存在 → complete（标记完成 + transition 推进）
    3. 非 interactive → reschedule
       - structured_job: 后台 dispatch（遵守 config）+ transition
       - convergent_loop: 通过 ConvergentLoop 走完整质量门控流程
    4. Interactive Session → needs_human（原子标记 failed + approval）
    """

    def __init__(
        self,
        db_path: Path,
        subprocess_mgr: SubprocessManager | None,
        transition_queue: TransitionQueue,
        nudge: Nudge | None = None,
        *,
        interactive_phases: set[str] | None = None,
        convergent_loop_phases: set[str] | None = None,
        settings: Any | None = None,
    ) -> None:
        self._db_path = db_path
        self._subprocess_mgr = subprocess_mgr
        self._transition_queue = transition_queue
        self._nudge = nudge
        self._interactive_phases = interactive_phases or set()
        self._convergent_loop_phases = convergent_loop_phases or set()
        self._settings = settings  # ATOSettings, typed as Any to avoid circular import
        self._background_tasks: list[asyncio.Task[None]] = []

    async def await_background_tasks(self) -> None:
        """等待所有后台任务完成。供测试和 Orchestrator shutdown 使用。"""
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)

    # ------------------------------------------------------------------
    # 分类
    # ------------------------------------------------------------------

    async def scan_running_tasks(self) -> list[TaskRecord]:
        """查询所有 status='running' 的 tasks。"""
        from ato.models.db import get_connection, get_running_tasks

        db = await get_connection(self._db_path)
        try:
            return await get_running_tasks(db)
        finally:
            await db.close()

    def classify_task(self, task: TaskRecord) -> RecoveryClassification:
        """对单个 running task 执行四路分类。"""
        action: RecoveryAction
        if task.pid is not None and _is_pid_alive(task.pid):
            action = "reattach"
            reason = f"PID {task.pid} still alive"
        elif _artifact_exists(task):
            action = "complete"
            reason = f"Artifact exists: {task.expected_artifact}"
        else:
            is_interactive = (
                task.phase in self._interactive_phases
                if self._interactive_phases
                else _is_interactive_phase(task.phase)
            )
            if is_interactive:
                action = "needs_human"
                reason = f"Interactive session (phase={task.phase}), PID not alive"
            else:
                action = "reschedule"
                reason = f"Structured job (phase={task.phase}), PID not alive, no artifact"

        logger.info(
            "recovery_task_classified",
            task_id=task.task_id,
            story_id=task.story_id,
            recovery_action=action,
            pid=task.pid,
            phase=task.phase,
        )
        return RecoveryClassification(
            task_id=task.task_id,
            story_id=task.story_id,
            action=action,
            reason=reason,
        )

    # ------------------------------------------------------------------
    # 恢复动作
    # ------------------------------------------------------------------

    async def _reattach(self, task: TaskRecord) -> None:
        """重新注册 PID 监听并启动异步 PID 监控。"""
        if task.pid is not None and self._subprocess_mgr is not None:
            from ato.subprocess_mgr import RunningTask

            self._subprocess_mgr.running[task.pid] = RunningTask(
                task_id=task.task_id,
                story_id=task.story_id,
                phase=task.phase,
                pid=task.pid,
                started_at=task.started_at or datetime.now(tz=UTC),
            )

        if task.pid is not None:
            t = asyncio.create_task(
                self._monitor_reattached_pid(task),
                name=f"recovery-monitor-{task.task_id}",
            )
            self._background_tasks.append(t)

        logger.info(
            "recovery_action_reattach",
            task_id=task.task_id,
            story_id=task.story_id,
            pid=task.pid,
            monitor_started=task.pid is not None,
        )

    async def _monitor_reattached_pid(self, task: TaskRecord) -> None:
        """监控 reattach 的 PID，退出后自动执行后续恢复。"""
        pid = task.pid
        if pid is None:
            return
        try:
            while _is_pid_alive(pid):
                await asyncio.sleep(_PID_MONITOR_INTERVAL)
            logger.info("reattached_pid_exited", task_id=task.task_id, pid=pid)
            if _artifact_exists(task):
                await self._complete_from_artifact(task)
            else:
                is_interactive = (
                    task.phase in self._interactive_phases
                    if self._interactive_phases
                    else _is_interactive_phase(task.phase)
                )
                if is_interactive:
                    await self._mark_needs_human(task)
                else:
                    await self._reschedule(task)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("monitor_reattached_pid_error", task_id=task.task_id, pid=pid)

    async def _complete_from_artifact(self, task: TaskRecord) -> None:
        """标记 task 完成 + 提交 transition 推进 story。"""
        from ato.models.db import get_connection, update_task_status

        db = await get_connection(self._db_path)
        try:
            await update_task_status(
                db,
                task.task_id,
                "completed",
                completed_at=datetime.now(tz=UTC),
            )
        finally:
            await db.close()

        event_name = _PHASE_SUCCESS_EVENT.get(task.phase)
        if event_name is not None:
            await self._transition_queue.submit(
                TransitionEvent(
                    story_id=task.story_id,
                    event_name=event_name,
                    source="agent",
                    submitted_at=datetime.now(tz=UTC),
                )
            )
            logger.info(
                "recovery_action_complete",
                task_id=task.task_id,
                story_id=task.story_id,
                artifact=task.expected_artifact,
                transition_event=event_name,
            )
        else:
            logger.warning(
                "recovery_action_complete_no_event",
                task_id=task.task_id,
                story_id=task.story_id,
                phase=task.phase,
            )

    async def _reschedule(self, task: TaskRecord) -> None:
        """重新调度 task：重置 pending + 按 phase 类型 dispatch。

        - convergent_loop: 通过 ConvergentLoop.run_first_review() 走完整质量门控
        - structured_job: 后台 re-dispatch（遵守 config）+ transition
        """
        from ato.models.db import get_connection, update_task_status

        db = await get_connection(self._db_path)
        try:
            await update_task_status(
                db,
                task.task_id,
                "pending",
                pid=None,
                started_at=None,
                completed_at=None,
                exit_code=None,
                error_message=None,
            )
        finally:
            await db.close()

        is_convergent = task.phase in self._convergent_loop_phases

        if is_convergent:
            t = asyncio.create_task(
                self._dispatch_convergent_loop(task),
                name=f"recovery-convergent-{task.task_id}",
            )
            self._background_tasks.append(t)
            logger.info(
                "recovery_action_reschedule",
                task_id=task.task_id,
                story_id=task.story_id,
                phase=task.phase,
                dispatch="convergent_loop",
            )
        else:
            t = asyncio.create_task(
                self._dispatch_structured_job(task),
                name=f"recovery-dispatch-{task.task_id}",
            )
            self._background_tasks.append(t)
            logger.info(
                "recovery_action_reschedule",
                task_id=task.task_id,
                story_id=task.story_id,
                phase=task.phase,
                dispatch="structured_job",
            )

    # ------------------------------------------------------------------
    # Dispatch 策略
    # ------------------------------------------------------------------

    def _resolve_phase_config(self, phase: str) -> dict[str, Any]:
        """从 settings 读取 phase 级别的 model / timeout / sandbox / cli。"""
        if self._settings is None:
            return {}
        from ato.config import build_phase_definitions

        for pd in build_phase_definitions(self._settings):
            if pd.name == phase:
                return {
                    "cli_tool": pd.cli_tool,
                    "model": pd.model,
                    "sandbox": pd.sandbox,
                    "timeout_seconds": pd.timeout_seconds,
                    "max_concurrent": self._settings.max_concurrent_agents,
                }
        return {}

    def _build_dispatch_options(
        self,
        task: TaskRecord,
        worktree_path: str | None,
        phase_cfg: dict[str, Any],
    ) -> dict[str, Any] | None:
        """构建 dispatch options（cwd, sandbox, model, max_turns 等）。"""
        opts: dict[str, Any] = {}
        if worktree_path:
            opts["cwd"] = worktree_path

        # sandbox: 优先 phase config，fallback 按 cli_tool
        sandbox = phase_cfg.get("sandbox")
        if sandbox:
            opts["sandbox"] = sandbox
        elif task.cli_tool == "codex":
            opts["sandbox"] = "workspace-write"

        # model from phase config
        model = phase_cfg.get("model")
        if model:
            opts["model"] = model

        # max_turns from timeout (structured_job: timeout/60 作为粗估)
        timeout = phase_cfg.get("timeout_seconds")
        if timeout and task.cli_tool == "claude":
            opts["max_turns"] = max(1, timeout // 60)

        return opts or None

    async def _get_story_worktree(self, story_id: str) -> str | None:
        """读取 story 的 worktree_path。"""
        from ato.models.db import get_connection, get_story

        db = await get_connection(self._db_path)
        try:
            story = await get_story(db, story_id)
            return story.worktree_path if story else None
        finally:
            await db.close()

    async def _dispatch_convergent_loop(self, task: TaskRecord) -> None:
        """Phase-aware convergent loop dispatch：按 phase 使用正确的 role/event/skill。

        不调用 ConvergentLoop.run_first_review()（硬编码 reviewing 语义）。
        自行构建完整流程：dispatch CLI → BMAD parse → findings 入库 → 评估 → transition。
        """
        try:
            from ato.adapters.bmad_adapter import BmadAdapter, record_parse_failure
            from ato.models.db import get_connection, insert_findings_batch
            from ato.models.schemas import (
                BmadSkillType,
                FindingRecord,
                compute_dedup_hash,
            )
            from ato.validation import maybe_create_blocking_abnormal_approval

            worktree_path = await self._get_story_worktree(task.story_id)
            phase_cfg = self._resolve_phase_config(task.phase)
            max_concurrent = phase_cfg.get("max_concurrent", 4)

            # Phase-aware 配置
            success_event = _PHASE_SUCCESS_EVENT.get(task.phase)
            fail_event = _PHASE_FAIL_EVENT.get(task.phase)
            skill_name = _PHASE_BMAD_SKILL.get(task.phase, "code_review")
            skill_type = BmadSkillType(skill_name)

            if success_event is None:
                logger.error(
                    "recovery_convergent_loop_no_event",
                    task_id=task.task_id,
                    phase=task.phase,
                )
                return

            # 解析 worktree path
            if worktree_path is None:
                from ato.models.db import get_story as _gs

                db = await get_connection(self._db_path)
                try:
                    story = await _gs(db, task.story_id)
                    worktree_path = story.worktree_path if story else None
                finally:
                    await db.close()

            if worktree_path is None:
                logger.error(
                    "recovery_convergent_loop_no_worktree",
                    task_id=task.task_id,
                    story_id=task.story_id,
                )
                return

            # Dispatch CLI（使用 task 的原始 role 和 cli_tool）
            cli_tool = phase_cfg.get("cli_tool", task.cli_tool)
            role = task.role
            sandbox = phase_cfg.get("sandbox", "read-only")

            adapter = _create_adapter(cli_tool)
            mgr = SubprocessManager(
                max_concurrent=max_concurrent,
                adapter=adapter,
                db_path=self._db_path,
            )

            prompt_template = _CONVERGENT_LOOP_PROMPTS.get(task.phase)
            if prompt_template is not None:
                prompt = prompt_template.format(
                    worktree_path=worktree_path,
                    story_id=task.story_id,
                )
            else:
                prompt = (
                    f"Recovery re-dispatch for story {task.story_id}, "
                    f"phase {task.phase}. "
                    f"Perform a full {task.phase} on the worktree at {worktree_path}."
                )

            result = await mgr.dispatch_with_retry(
                story_id=task.story_id,
                phase=task.phase,
                role=role,
                cli_tool=cli_tool,
                prompt=prompt,
                options={"cwd": worktree_path, "sandbox": sandbox},
                task_id=task.task_id,
                is_retry=True,
            )

            # BMAD parse
            bmad = BmadAdapter()
            parse_result = await bmad.parse(
                markdown_output=result.text_result,
                skill_type=skill_type,
                story_id=task.story_id,
            )

            if parse_result.verdict == "parse_failed":
                db = await get_connection(self._db_path)
                try:
                    await record_parse_failure(
                        parse_result=parse_result,
                        story_id=task.story_id,
                        skill_type=skill_type,
                        db=db,
                        notifier=self._nudge.notify if self._nudge else None,
                    )
                finally:
                    await db.close()
                logger.warning(
                    "recovery_convergent_loop_parse_failed",
                    task_id=task.task_id,
                    phase=task.phase,
                )
                return

            # Findings → DB
            now = datetime.now(tz=UTC)
            records = [
                FindingRecord(
                    finding_id=str(uuid.uuid4()),
                    story_id=task.story_id,
                    round_num=1,
                    severity=f.severity,
                    description=f.description,
                    status="open",
                    file_path=f.file_path,
                    rule_id=f.rule_id,
                    dedup_hash=f.dedup_hash
                    or compute_dedup_hash(f.file_path, f.rule_id, f.severity, f.description),
                    line_number=f.line,
                    created_at=now,
                )
                for f in parse_result.findings
            ]

            blocking_threshold = (
                self._settings.cost.blocking_threshold if self._settings is not None else 10
            )

            db = await get_connection(self._db_path)
            try:
                await insert_findings_batch(db, records)
                await maybe_create_blocking_abnormal_approval(
                    db,
                    task.story_id,
                    1,
                    threshold=blocking_threshold,
                    nudge=self._nudge,
                )
            finally:
                await db.close()

            # 评估：blocking_count == 0 → pass，否则 fail
            blocking_count = sum(1 for r in records if r.severity == "blocking")
            converged = blocking_count == 0

            event_name = success_event if converged else fail_event
            if event_name is not None:
                await self._transition_queue.submit(
                    TransitionEvent(
                        story_id=task.story_id,
                        event_name=event_name,
                        source="agent",
                        submitted_at=datetime.now(tz=UTC),
                    )
                )

            logger.info(
                "recovery_convergent_loop_complete",
                task_id=task.task_id,
                story_id=task.story_id,
                phase=task.phase,
                converged=converged,
                blocking_count=blocking_count,
                transition_event=event_name,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "recovery_convergent_loop_error",
                task_id=task.task_id,
                story_id=task.story_id,
            )
            await self._mark_dispatch_failed(task)

    async def _dispatch_structured_job(self, task: TaskRecord) -> None:
        """后台 dispatch structured_job：遵守 config 的 model/timeout/sandbox/并发。"""
        try:
            worktree_path = await self._get_story_worktree(task.story_id)
            phase_cfg = self._resolve_phase_config(task.phase)
            max_concurrent = phase_cfg.get("max_concurrent", 4)
            options = self._build_dispatch_options(task, worktree_path, phase_cfg)

            adapter = _create_adapter(task.cli_tool)
            mgr = SubprocessManager(
                max_concurrent=max_concurrent,
                adapter=adapter,
                db_path=self._db_path,
            )

            story_ctx = ""
            if task.context_briefing:
                story_ctx = f"\n\nPrevious context: {task.context_briefing}"

            prompt = (
                f"Recovery re-dispatch for story {task.story_id}, "
                f"phase {task.phase}. "
                f"The previous task crashed without producing an artifact. "
                f"Please resume the work for this phase.{story_ctx}"
            )

            result = await mgr.dispatch_with_retry(
                story_id=task.story_id,
                phase=task.phase,
                role=task.role,
                cli_tool=task.cli_tool,
                prompt=prompt,
                options=options,
                task_id=task.task_id,
                is_retry=True,
            )

            if result.status == "success":
                event_name = _PHASE_SUCCESS_EVENT.get(task.phase)
                if event_name is not None:
                    await self._transition_queue.submit(
                        TransitionEvent(
                            story_id=task.story_id,
                            event_name=event_name,
                            source="agent",
                            submitted_at=datetime.now(tz=UTC),
                        )
                    )
                    logger.info(
                        "recovery_dispatch_complete",
                        task_id=task.task_id,
                        story_id=task.story_id,
                        transition_event=event_name,
                    )
            else:
                logger.warning(
                    "recovery_dispatch_failed",
                    task_id=task.task_id,
                    story_id=task.story_id,
                    status=result.status,
                    error=result.error_message,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "recovery_dispatch_error",
                task_id=task.task_id,
                story_id=task.story_id,
            )
            await self._mark_dispatch_failed(task)

    async def _mark_dispatch_failed(self, task: TaskRecord) -> None:
        """后台 dispatch 异常兜底：标记 task=failed + 创建 approval。

        防止 task 卡在 running/pending 无人处理。使用 _mark_needs_human
        的原子 SAVEPOINT 逻辑。
        """
        try:
            await self._mark_needs_human(task)
            logger.info(
                "recovery_dispatch_failed_marked",
                task_id=task.task_id,
                story_id=task.story_id,
                phase=task.phase,
            )
        except Exception:
            logger.exception(
                "recovery_mark_dispatch_failed_error",
                task_id=task.task_id,
            )

    async def _mark_needs_human(self, task: TaskRecord) -> None:
        """原子标记 task=failed + 创建 approval（SAVEPOINT 保证全有或全无）。"""
        from ato.models.db import _dt_to_iso, get_connection

        now = datetime.now(tz=UTC)
        approval_id = str(uuid.uuid4())

        db = await get_connection(self._db_path)
        try:
            await db.execute("SAVEPOINT needs_human")
            try:
                await db.execute(
                    "UPDATE tasks SET status = ?, error_message = ? WHERE task_id = ?",
                    ("failed", "crash_recovery:needs_human", task.task_id),
                )
                await db.execute(
                    "INSERT INTO approvals "
                    "(approval_id, story_id, approval_type, status, payload, "
                    "decision, decided_at, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        approval_id,
                        task.story_id,
                        "crash_recovery",
                        "pending",
                        json.dumps(
                            {
                                "task_id": task.task_id,
                                "phase": task.phase,
                                "options": ["restart", "resume", "abandon"],
                                "recommended_action": "restart",
                            }
                        ),
                        None,
                        None,
                        _dt_to_iso(now),
                    ),
                )
                await db.execute("RELEASE SAVEPOINT needs_human")
            except BaseException:
                await db.execute("ROLLBACK TO SAVEPOINT needs_human")
                await db.execute("RELEASE SAVEPOINT needs_human")
                raise
            await db.commit()
        finally:
            await db.close()

        if self._nudge is not None:
            self._nudge.notify()

        logger.info(
            "recovery_action_needs_human",
            task_id=task.task_id,
            story_id=task.story_id,
            phase=task.phase,
        )

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    async def run_recovery(self) -> RecoveryResult:
        """主入口：分类 → 执行恢复动作 → 返回摘要。"""
        from ato.models.db import get_connection, get_paused_tasks, get_running_tasks

        t_start = time.monotonic()

        db = await get_connection(self._db_path)
        try:
            running_tasks = await get_running_tasks(db)
            paused_tasks = await get_paused_tasks(db)
        finally:
            await db.close()

        recovery_mode: RecoveryMode
        if running_tasks:
            recovery_mode = "crash"
            logger.warning(
                "crash_recovery_mode",
                running_tasks=len(running_tasks),
                message=f"检测到 {len(running_tasks)} 个 running task，进入崩溃恢复模式",
            )
        elif paused_tasks:
            recovery_mode = "normal"
            logger.info(
                "normal_recovery_mode",
                paused_tasks=len(paused_tasks),
                message=f"检测到 {len(paused_tasks)} 个 paused task，正常恢复",
            )
        else:
            return RecoveryResult(
                classifications=[],
                auto_recovered_count=0,
                needs_human_count=0,
                recovery_mode="none",
            )

        classifications: list[RecoveryClassification] = []
        auto_recovered = 0
        dispatched = 0
        needs_human = 0

        if recovery_mode == "crash":
            for task in running_tasks:
                c = self.classify_task(task)
                classifications.append(c)
                if c.action == "reattach":
                    await self._reattach(task)
                    auto_recovered += 1
                elif c.action == "complete":
                    await self._complete_from_artifact(task)
                    auto_recovered += 1
                elif c.action == "reschedule":
                    await self._reschedule(task)
                    dispatched += 1
                elif c.action == "needs_human":
                    await self._mark_needs_human(task)
                    needs_human += 1

        elif recovery_mode == "normal":
            protected = await self._get_crash_recovery_story_ids()
            for task in paused_tasks:
                if task.story_id in protected:
                    logger.info(
                        "recovery_skip_protected_task",
                        task_id=task.task_id,
                        story_id=task.story_id,
                    )
                    needs_human += 1
                    classifications.append(
                        RecoveryClassification(
                            task_id=task.task_id,
                            story_id=task.story_id,
                            action="needs_human",
                            reason="Skipped: pending crash_recovery approval",
                        )
                    )
                    continue
                classifications.append(
                    RecoveryClassification(
                        task_id=task.task_id,
                        story_id=task.story_id,
                        action="reschedule",
                        reason="Normal restart: task was paused by ato stop",
                    )
                )
                await self._reschedule(task)
                dispatched += 1

        duration_ms = (time.monotonic() - t_start) * 1000
        logger.info(
            "recovery_complete",
            recovery_mode=recovery_mode,
            auto_recovered=auto_recovered,
            dispatched=dispatched,
            needs_human=needs_human,
            duration_ms=round(duration_ms, 1),
        )

        return RecoveryResult(
            classifications=classifications,
            auto_recovered_count=auto_recovered,
            dispatched_count=dispatched,
            needs_human_count=needs_human,
            recovery_mode=recovery_mode,
        )

    async def _get_crash_recovery_story_ids(self) -> set[str]:
        """查询有 pending crash_recovery approval 的 story_id 集合。"""
        from ato.models.db import get_connection, get_pending_approvals

        db = await get_connection(self._db_path)
        try:
            approvals = await get_pending_approvals(db)
            return {a.story_id for a in approvals if a.approval_type == "crash_recovery"}
        finally:
            await db.close()
