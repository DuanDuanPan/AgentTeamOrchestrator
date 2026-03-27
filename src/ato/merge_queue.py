"""merge_queue — Merge Queue 核心类。

管理 merge 入队/出队/冻结/解冻/merge 执行流程。
Merge 严格串行化——同一时刻只有一个 story 在 merge。
完整 merge / regression 流程在后台 worker 中执行，不阻塞 poll loop。
"""

from __future__ import annotations

import asyncio
import shlex
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import structlog

from ato.config import ATOSettings
from ato.models.schemas import TransitionEvent
from ato.transition_queue import TransitionQueue
from ato.worktree_mgr import WorktreeManager

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

_REGRESSION_FAILURE_REASON_PREFIX = "regression failed for "
_REGRESSION_CRASH_REASON_PREFIX = "crash during regression for "


def get_regression_recovery_story_id(frozen_reason: str | None) -> str | None:
    """从 merge queue 的冻结原因中提取负责修复 main 的 story_id。"""
    if frozen_reason is None:
        return None

    for prefix in (
        _REGRESSION_FAILURE_REASON_PREFIX,
        _REGRESSION_CRASH_REASON_PREFIX,
    ):
        if frozen_reason.startswith(prefix):
            story_id = frozen_reason[len(prefix) :].strip()
            return story_id or None

    return None


class MergeQueue:
    """Merge queue 管理器。

    由 Orchestrator poll cycle 驱动，负责：
    - 将批准的 merge 请求入队
    - 按顺序（approved_at ASC, id ASC）出队并执行 merge
    - 管理 freeze/unfreeze 状态
    - 调度 regression 测试并检测完成

    Args:
        db_path: SQLite 数据库文件路径。
        worktree_mgr: Git worktree 管理器。
        transition_queue: 状态转换队列。
        settings: ATO 配置。
    """

    def __init__(
        self,
        db_path: Path,
        worktree_mgr: WorktreeManager,
        transition_queue: TransitionQueue,
        settings: ATOSettings,
    ) -> None:
        self._db_path = db_path
        self._worktree_mgr = worktree_mgr
        self._tq = transition_queue
        self._settings = settings
        self._worker_tasks: dict[str, asyncio.Task[None]] = {}

    async def _clear_current_merge_story_lock(
        self,
        story_id: str,
        *,
        context: str,
    ) -> None:
        """释放 merge queue 串行锁。"""
        from ato.models.db import get_connection, set_current_merge_story

        db = await get_connection(self._db_path)
        try:
            await set_current_merge_story(db, None)
        finally:
            await db.close()

        logger.info("merge_queue_lock_released", story_id=story_id, context=context)

    async def _mark_merge_failed_and_release_lock(
        self,
        story_id: str,
        *,
        context: str,
    ) -> None:
        """在异常路径下尽力将 merge 标记失败并释放锁。"""
        from ato.models.db import complete_merge, get_connection, set_current_merge_story

        db = await get_connection(self._db_path)
        try:
            try:
                await complete_merge(db, story_id, success=False)
            except Exception:
                logger.exception(
                    "merge_queue_mark_failed_failed",
                    story_id=story_id,
                    context=context,
                )

            try:
                await set_current_merge_story(db, None)
            except Exception:
                logger.exception(
                    "merge_queue_clear_current_story_failed",
                    story_id=story_id,
                    context=context,
                )
        finally:
            await db.close()

    async def _complete_regression_pass(
        self,
        db: aiosqlite.Connection,
        story_id: str,
    ) -> None:
        """收敛 regression pass 的闭环处理。"""
        from ato.models.db import (
            complete_merge,
            get_merge_queue_state,
            set_current_merge_story,
            set_merge_queue_frozen,
        )

        await self._tq.submit(
            TransitionEvent(
                story_id=story_id,
                event_name="regression_pass",
                source="agent",
                submitted_at=datetime.now(tz=UTC),
            )
        )
        await complete_merge(db, story_id, success=True)

        state = await get_merge_queue_state(db)
        if state.frozen and get_regression_recovery_story_id(state.frozen_reason) == story_id:
            await set_merge_queue_frozen(db, frozen=False, reason=None)
            logger.info(
                "merge_queue_unfrozen",
                reason="regression_pass",
                story_id=story_id,
            )

        await set_current_merge_story(db, None)
        logger.info("regression_completed", story_id=story_id, result="pass")
        await self._worktree_mgr.cleanup(story_id)

    async def recover_stale_lock(self) -> None:
        """启动时校正 stale current_merge_story_id。

        场景分治：
        - entry 不存在 / 已完成 / 已失败 → 仅释放锁
        - entry 在 merging → 移除 entry + 释放锁（story 仍在 merging phase，
          poll cycle 的 _create_merge_authorizations 会重新创建 approval）
        - entry 在 regression_pending 且 task 已完成 → 按真实 task 结果收敛
        - entry 在 regression_pending 且 task 仍未知/未完成 → 冻结 queue +
          创建 regression_failure approval + 释放锁（保持 freeze 安全语义）
        """
        from ato.models.db import (
            get_connection,
            get_merge_queue_entry,
            get_merge_queue_state,
            remove_from_merge_queue,
            set_current_merge_story,
        )

        db = await get_connection(self._db_path)
        try:
            state = await get_merge_queue_state(db)
            if state.current_merge_story_id is None:
                return

            sid = state.current_merge_story_id
            entry = await get_merge_queue_entry(db, sid)

            if entry is None or entry.status not in ("merging", "regression_pending"):
                # stale lock — 没有对应的活跃 entry
                await set_current_merge_story(db, None)
                logger.warning(
                    "merge_queue_stale_lock_cleared",
                    story_id=sid,
                    entry_status=entry.status if entry else "missing",
                )
                return

            if entry.status == "merging":
                # merge worker 崩溃，merge 未完成
                # 移除 entry（不是标记 failed），释放锁
                # story 仍在 merging phase → poll cycle 会重建 merge_authorization
                await remove_from_merge_queue(db, sid)
                await set_current_merge_story(db, None)
                logger.warning(
                    "merge_queue_crash_recovery_merging",
                    story_id=sid,
                    note="Entry removed, lock released. Approval will be recreated.",
                )
                return

            task_id = entry.regression_task_id
            if task_id is not None:
                task_cursor = await db.execute(
                    "SELECT status, exit_code FROM tasks WHERE task_id = ?",
                    (task_id,),
                )
                task_row = await task_cursor.fetchone()
                if task_row is not None and task_row[0] in ("completed", "failed"):
                    exit_code = task_row[1]
                    if exit_code == 0:
                        await self._complete_regression_pass(db, sid)
                        logger.warning(
                            "merge_queue_crash_recovery_regression_completed",
                            story_id=sid,
                            result="pass",
                        )
                        return

                    # 读取 task error_message
                    err_cursor = await db.execute(
                        "SELECT error_message FROM tasks WHERE task_id = ?",
                        (task_id,),
                    )
                    err_row = await err_cursor.fetchone()
                    test_output = err_row[0] if err_row and err_row[0] else None

                    await self._handle_regression_failure(
                        sid,
                        test_output_summary=test_output,
                    )
                    await set_current_merge_story(db, None)
                    logger.warning(
                        "merge_queue_crash_recovery_regression_completed",
                        story_id=sid,
                        result="fail",
                    )
                    return

            # regression_pending — regression 测试中途崩溃且结果未知
            # 不能简单释放锁让后续 merge 继续，需要 freeze + 让操作者决策
            # 复用 _handle_regression_failure 保证 payload 合同一致（AC3）
            # 注：_handle_regression_failure 内部会 complete_merge + freeze + create_approval
            await self._handle_regression_failure(
                sid,
                test_output_summary="Orchestrator crashed during regression test",
            )
            await set_current_merge_story(db, None)
            logger.warning(
                "merge_queue_crash_recovery_regression",
                story_id=sid,
                note="Queue frozen, regression_failure approval created.",
            )
        finally:
            await db.close()

    async def enqueue(
        self,
        story_id: str,
        approval_id: str,
        approved_at: datetime,
    ) -> None:
        """将 story 加入 merge queue。

        注意：queue 冻结时仍可记录 recovery story 的新授权，但不会在此处自动解冻。
        """
        from ato.models.db import enqueue_merge, get_connection

        now = datetime.now(tz=UTC)
        db = await get_connection(self._db_path)
        try:
            await enqueue_merge(db, story_id, approval_id, approved_at, now)
        finally:
            await db.close()

        logger.info(
            "merge_queue_enqueued",
            story_id=story_id,
            approval_id=approval_id,
        )

    async def process_next(self) -> bool:
        """尝试启动下一个 merge 操作。

        由 Orchestrator poll cycle 调用。
        queue 冻结时，仅允许负责修复 main 的 recovery story 继续 merge。

        Returns:
            True 表示已启动一个 merge worker，False 表示无操作。
        """
        from ato.models.db import (
            dequeue_next_merge,
            get_connection,
            get_merge_queue_state,
            set_current_merge_story,
        )

        story_id: str | None = None
        db = await get_connection(self._db_path)
        try:
            state = await get_merge_queue_state(db)

            # 有正在进行的 merge 时不处理新条目
            if state.current_merge_story_id is not None:
                return False

            recovery_story_id = get_regression_recovery_story_id(
                state.frozen_reason if state.frozen else None
            )
            if state.frozen:
                if recovery_story_id is None:
                    return False

                cursor = await db.execute(
                    "UPDATE merge_queue SET status = 'merging' "
                    "WHERE story_id = ? AND status = 'waiting'",
                    (recovery_story_id,),
                )
                if cursor.rowcount == 0:
                    return False
                story_id = recovery_story_id
            else:
                entry = await dequeue_next_merge(db)
                if entry is None:
                    return False
                story_id = entry.story_id

            # 设置当前 merge story
            await set_current_merge_story(db, story_id)
        finally:
            await db.close()

        if story_id is None:
            return False

        logger.info(
            "merge_queue_dequeued",
            story_id=story_id,
        )

        # 创建后台 worker task
        task = asyncio.create_task(
            self._run_merge_worker(story_id),
            name=f"merge-worker-{story_id}",
        )
        self._worker_tasks[story_id] = task
        # 清理回调
        sid = story_id
        task.add_done_callback(lambda _t: self._worker_tasks.pop(sid, None))

        return True

    async def _run_merge_worker(self, story_id: str) -> None:
        """后台 merge worker：rebase → merge → transition → dispatch regression。"""
        try:
            await self._execute_merge(story_id)
        except Exception:
            logger.exception("merge_worker_failed", story_id=story_id)
            await self._mark_merge_failed_and_release_lock(
                story_id,
                context="merge_worker_exception",
            )

    async def _execute_merge(self, story_id: str) -> None:
        """执行完整 merge 流程。"""
        from ato.models.db import get_connection, mark_regression_dispatched

        # Step 1: Rebase worktree onto main
        logger.info("merge_rebase_started", story_id=story_id)
        success, stderr = await self._worktree_mgr.rebase_onto_main(
            story_id,
            timeout_seconds=self._settings.merge_rebase_timeout,
        )

        if not success:
            # 检测冲突
            if "CONFLICT" in stderr:
                logger.info("merge_rebase_conflict", story_id=story_id)
                resolved = await self._handle_rebase_conflict(story_id, stderr)
                if not resolved:
                    # escalate — approval 已创建，清理 current
                    await self._clear_current_merge_story_lock(
                        story_id,
                        context="rebase_conflict_escalated",
                    )
                    return
            else:
                # 非冲突的 rebase 失败
                logger.error("merge_rebase_failed", story_id=story_id, stderr=stderr)
                await self._mark_merge_failed_and_release_lock(
                    story_id,
                    context="rebase_failed",
                )
                return

        # Step 2a: 记录 merge 前 main HEAD（用于精确 revert）
        pre_merge_head = await self._worktree_mgr.get_main_head()
        if not pre_merge_head:
            logger.error(
                "merge_pre_merge_head_missing",
                story_id=story_id,
                note="Cannot safely revert without pre-merge main HEAD",
            )
            await self._mark_merge_failed_and_release_lock(
                story_id,
                context="pre_merge_head_missing",
            )
            return

        from ato.models.db import set_pre_merge_head

        try:
            db = await get_connection(self._db_path)
            try:
                await set_pre_merge_head(db, story_id, pre_merge_head)
            finally:
                await db.close()
        except Exception:
            logger.exception(
                "merge_pre_merge_head_persist_failed",
                story_id=story_id,
            )
            await self._mark_merge_failed_and_release_lock(
                story_id,
                context="pre_merge_head_persist_failed",
            )
            return

        # Step 2b: Merge to main (fast-forward)
        success, stderr = await self._worktree_mgr.merge_to_main(story_id)
        if not success:
            logger.error("merge_ff_failed", story_id=story_id, stderr=stderr)
            await self._mark_merge_failed_and_release_lock(
                story_id,
                context="merge_ff_failed",
            )
            return

        logger.info("merge_ff_completed", story_id=story_id)

        # Step 3: Transition → regression
        await self._tq.submit(
            TransitionEvent(
                story_id=story_id,
                event_name="merge_done",
                source="agent",
                submitted_at=datetime.now(tz=UTC),
            )
        )

        # Step 4: Dispatch regression test
        task_id = await self._dispatch_regression_test(story_id)

        # Step 5: Mark regression dispatched
        # 注意：不清空 current_merge_story_id！保持到 regression 完成后
        # 才清空，防止 process_next() 在 regression 期间启动新 merge
        db = await get_connection(self._db_path)
        try:
            await mark_regression_dispatched(db, story_id, task_id)
        finally:
            await db.close()

        logger.info("regression_dispatched", story_id=story_id, task_id=task_id)

    async def _dispatch_regression_test(self, story_id: str) -> str:
        """调度 regression 测试作为 Structured Job。

        Returns:
            task_id 供后续 poll cycle 检测完成。
        """
        import uuid

        from ato.models.db import get_connection, insert_task
        from ato.models.schemas import TaskRecord

        task_id = str(uuid.uuid4())
        now = datetime.now(tz=UTC)

        # 创建 task record（status=running 以便 crash recovery 能识别）
        task = TaskRecord(
            task_id=task_id,
            story_id=story_id,
            phase="regression",
            role="qa",
            cli_tool="codex",
            status="running",
            expected_artifact="regression_test",
            started_at=now,
        )

        db = await get_connection(self._db_path)
        try:
            await insert_task(db, task)
        finally:
            await db.close()

        # 启动后台 regression 测试
        regression_task = asyncio.create_task(
            self._run_regression_test(story_id, task_id),
            name=f"regression-{story_id}",
        )
        self._worker_tasks[f"regression-{story_id}"] = regression_task

        return task_id

    async def _run_regression_test(self, story_id: str, task_id: str) -> None:
        """在 main 分支上执行 regression 测试命令。"""
        from ato.adapters.base import cleanup_process
        from ato.models.db import get_connection, update_task_status

        cmd = self._settings.regression_test_command
        repo_root = self._worktree_mgr.project_root

        try:
            argv = shlex.split(cmd)
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(repo_root),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                _stdout_bytes, _stderr_bytes = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=self._settings.timeout.structured_job,
                )
            except TimeoutError:
                await cleanup_process(proc)
                db = await get_connection(self._db_path)
                try:
                    await update_task_status(
                        db,
                        task_id,
                        "failed",
                        exit_code=-1,
                        error_message="Regression test timed out",
                        completed_at=datetime.now(tz=UTC),
                    )
                finally:
                    await db.close()
                return
            finally:
                await cleanup_process(proc)

            exit_code = proc.returncode or 0
            # 失败时保留 stdout + stderr 摘要供 approval payload 使用（AC3）
            # 很多测试框架（pytest 等）把失败详情输出到 stdout，不能只看 stderr
            error_msg: str | None = None
            if exit_code != 0:
                stdout_text = _stdout_bytes.decode(errors="replace") if _stdout_bytes else ""
                stderr_text = _stderr_bytes.decode(errors="replace") if _stderr_bytes else ""
                combined = ""
                if stderr_text.strip():
                    combined += stderr_text.strip()
                if stdout_text.strip():
                    if combined:
                        combined += "\n---\n"
                    combined += stdout_text.strip()
                error_msg = combined[:1000] or "Regression test failed"
            db = await get_connection(self._db_path)
            try:
                await update_task_status(
                    db,
                    task_id,
                    "completed",
                    exit_code=exit_code,
                    error_message=error_msg,
                    completed_at=datetime.now(tz=UTC),
                )
            finally:
                await db.close()

        except Exception:
            logger.exception("regression_test_error", story_id=story_id, task_id=task_id)
            db = await get_connection(self._db_path)
            try:
                await update_task_status(
                    db,
                    task_id,
                    "failed",
                    exit_code=-1,
                    error_message="Regression test execution error",
                    completed_at=datetime.now(tz=UTC),
                )
            finally:
                await db.close()

    async def check_regression_completion(self) -> None:
        """检测已完成的 regression 任务并处理结果。

        由 Orchestrator poll cycle 调用。
        regression 完成后释放 current_merge_story_id 锁，允许下一个 merge。
        """
        from ato.models.db import get_connection, set_current_merge_story

        db = await get_connection(self._db_path)
        try:
            # 查找 regression_pending 状态的 queue entries
            cursor = await db.execute(
                "SELECT * FROM merge_queue WHERE status = 'regression_pending' ORDER BY id ASC"
            )
            rows = list(await cursor.fetchall())
            if len(rows) > 1:
                logger.warning(
                    "merge_queue_multiple_regression_pending",
                    story_ids=[dict(row)["story_id"] for row in rows],
                )

            for row in rows:
                row_dict = dict(row)
                story_id = row_dict["story_id"]
                task_id = row_dict.get("regression_task_id")
                if task_id is None:
                    continue

                # 检查 task 是否已完成
                task_cursor = await db.execute(
                    "SELECT status, exit_code FROM tasks WHERE task_id = ?",
                    (task_id,),
                )
                task_row = await task_cursor.fetchone()
                if task_row is None:
                    continue

                task_status = task_row[0]
                if task_status not in ("completed", "failed"):
                    continue

                exit_code = task_row[1]
                if exit_code == 0:
                    await self._complete_regression_pass(db, story_id)
                    break
                else:
                    # 读取 task error_message 作为测试输出摘要（AC3）
                    err_cursor = await db.execute(
                        "SELECT error_message FROM tasks WHERE task_id = ?",
                        (task_id,),
                    )
                    err_row = await err_cursor.fetchone()
                    test_output = err_row[0] if err_row and err_row[0] else None

                    # Regression fail — 冻结 queue + 创建 approval
                    await self._handle_regression_failure(
                        story_id,
                        test_output_summary=test_output,
                    )
                    # 释放串行锁——后续由 approval 决策驱动
                    await set_current_merge_story(db, None)
                    logger.info("regression_completed", story_id=story_id, result="fail")
                    break
        finally:
            await db.close()

    async def _handle_rebase_conflict(
        self,
        story_id: str,
        conflict_stderr: str,
    ) -> bool:
        """处理 rebase 冲突：调度 agent 修复，失败则 escalate。

        Returns:
            True 表示冲突已解决，False 表示需要人工介入。
        """
        # 获取冲突文件列表
        conflict_files = await self._worktree_mgr.get_conflict_files(story_id)

        max_attempts = self._settings.merge_conflict_resolution_max_attempts
        if max_attempts > 0:
            logger.info(
                "merge_conflict_auto_resolve_skipped",
                story_id=story_id,
                configured_attempts=max_attempts,
                conflict_files=conflict_files,
                note="No automated conflict resolver is wired in the current MVP",
            )

        # 自动解决失败，abort rebase 并 escalate
        await self._worktree_mgr.abort_rebase(story_id)

        from ato.approval_helpers import create_approval
        from ato.models.db import get_connection

        db = await get_connection(self._db_path)
        try:
            await create_approval(
                db,
                story_id=story_id,
                approval_type="rebase_conflict",
                payload_dict={
                    "options": ["manual_resolve", "skip", "abandon"],
                    "conflict_files": conflict_files,
                    "stderr": conflict_stderr[:500],
                },
            )
        finally:
            await db.close()

        return False

    async def _handle_precommit_failure(
        self,
        story_id: str,
        error_output: str,
    ) -> bool:
        """处理 pre-commit hook 失败。

        注意：当前 ff-only merge 路径不产生新 commit，所以不会触发 pre-commit hook。
        此方法供后续扩展 merge 流程时使用（如 rebase 后需要 commit 修正的场景）。

        Returns:
            True 表示已修复，False 表示需要人工介入。
        """
        from ato.approval_helpers import create_approval
        from ato.models.db import get_connection

        # MVP: escalate 给操作者
        db = await get_connection(self._db_path)
        try:
            await create_approval(
                db,
                story_id=story_id,
                approval_type="precommit_failure",
                payload_dict={
                    "options": ["retry", "manual_fix", "skip"],
                    "error_output": error_output[:500],
                },
            )
        finally:
            await db.close()

        return False

    async def _handle_regression_failure(
        self,
        story_id: str,
        *,
        test_output_summary: str | None = None,
    ) -> None:
        """处理 regression 测试失败：冻结 queue + 创建紧急 approval。

        Args:
            story_id: 失败的 story ID。
            test_output_summary: 失败的测试输出摘要（AC3 要求）。
        """
        from ato.approval_helpers import create_approval
        from ato.models.db import (
            complete_merge,
            get_connection,
            set_merge_queue_frozen,
        )

        payload: dict[str, object] = {
            "options": ["revert", "fix_forward", "pause"],
            "story_id": story_id,
        }
        if test_output_summary:
            payload["test_output_summary"] = test_output_summary[:500]

        db = await get_connection(self._db_path)
        try:
            # 查询被阻塞的 waiting entries 数量，写入影响范围
            blocked_cursor = await db.execute(
                "SELECT COUNT(*) FROM merge_queue WHERE status = 'waiting' AND story_id != ?",
                (story_id,),
            )
            blocked_row = await blocked_cursor.fetchone()
            if blocked_row and blocked_row[0] > 0:
                payload["blocked_count"] = blocked_row[0]

            await set_merge_queue_frozen(
                db,
                frozen=True,
                reason=f"regression failed for {story_id}",
            )
            await create_approval(
                db,
                story_id=story_id,
                approval_type="regression_failure",
                payload_dict=payload,
                risk_level="high",
            )
            await complete_merge(db, story_id, success=False)
        finally:
            await db.close()

        logger.info("merge_queue_frozen", story_id=story_id, reason="regression_failed")

    async def unfreeze(self, reason: str) -> None:
        """解冻 merge queue。"""
        from ato.models.db import get_connection, set_merge_queue_frozen

        db = await get_connection(self._db_path)
        try:
            await set_merge_queue_frozen(db, frozen=False, reason=None)
        finally:
            await db.close()

        logger.info("merge_queue_unfrozen", reason=reason)
