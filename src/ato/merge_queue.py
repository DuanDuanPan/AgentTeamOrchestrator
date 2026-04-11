"""merge_queue — Merge Queue 核心类。

管理 merge 入队/出队/冻结/解冻/merge 执行流程。
Merge 严格串行化——同一时刻只有一个 story 在 merge。
完整 merge / regression 流程在后台 worker 中执行，不阻塞 poll loop。
"""

from __future__ import annotations

import asyncio
import json
import shlex
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite
import structlog

from ato.config import (
    TEST_COMMAND_SOURCES,
    TEST_COMMAND_TRIGGER_REASONS,
    ATOSettings,
    EffectiveTestPolicy,
    resolve_effective_test_policy,
)
from ato.models.schemas import CLIAdapterError, TransitionEvent, WorktreePreflightResult
from ato.test_command_harness import (
    TEST_COMMAND_RUNNER_NAME,
    format_harnessed_command,
    resolve_command_audit_from_ledger,
)
from ato.test_policy_audit import validate_command_audit
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


# ---------------------------------------------------------------------------
# LLM-Assisted Regression Runner — Schema, Prompt, Helpers
# ---------------------------------------------------------------------------

_REGRESSION_RESULT_SCHEMA: str = json.dumps(
    {
        "type": "object",
        "properties": {
            "regression_status": {
                "type": "string",
                "enum": ["pass", "fail"],
                "description": "Overall regression result",
            },
            "summary": {
                "type": "string",
                "description": "Human-readable summary of the regression run",
            },
            "commands_attempted": {
                "type": ["array", "null"],
                "items": {"type": "string"},
                "description": (
                    "Legacy self-report of policy-domain shell commands executed during "
                    "regression. Use null when relying solely on the harness ledger. Do not "
                    "include auxiliary inspection commands."
                ),
            },
            "command_audit": {
                "type": ["array", "null"],
                "items": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                        "source": {
                            "type": "string",
                            "enum": list(TEST_COMMAND_SOURCES),
                        },
                        "trigger_reason": {
                            "type": "string",
                            "enum": list(TEST_COMMAND_TRIGGER_REASONS),
                        },
                        "exit_code": {
                            "type": ["integer", "null"],
                        },
                    },
                    "required": ["command", "source", "trigger_reason", "exit_code"],
                    "additionalProperties": False,
                },
                "description": (
                    "Legacy self-report of command provenance for each executed policy-domain "
                    "command; use null when relying solely on harness ledger data"
                ),
            },
            "skipped_command_reason": {
                "type": ["string", "null"],
                "description": "If any baseline command was skipped, explain why",
            },
            "discovery_notes": {
                "type": "string",
                "description": (
                    "Notes about how regression entrypoints were discovered and any auxiliary "
                    "inspection performed outside policy-domain audit"
                ),
            },
        },
        "required": [
            "regression_status",
            "summary",
            "commands_attempted",
            "command_audit",
            "skipped_command_reason",
            "discovery_notes",
        ],
        "additionalProperties": False,
    }
)

_REGRESSION_PROMPT_TEMPLATE = """\
You are a regression test runner for the repository at {repo_root}.

## CRITICAL CONSTRAINTS
- You MUST NOT intentionally edit tracked source files, change the git index,
  create commits, or switch branches.
- You MAY execute baseline commands that rebuild native dependencies or write
  temporary/ignored build artifacts when required to run regression.
- The repository must be git-clean relative to the starting snapshot when you
  finish. If a command leaves behind non-ignored changes, clean them up before
  reporting results.
- Execute every shell command through `{test_command_runner}`.
- Never run raw shell commands directly.

## YOUR TASK
Run the project's regression tests and report results.

### Step 1: Inspect the project
Check the project's test configuration, directory structure, and test framework setup.

### Step 2: Execute tests
{baseline_instructions}

### Step 3: Report results
Produce a structured JSON result matching the output_schema.
Also provide a brief natural-language summary suitable for human review.

If all tests pass, set regression_status to "pass".
If any test fails, set regression_status to "fail" and include failure details in summary.
"""

_BASELINE_WITH_COMMANDS = """\
The operator has provided these baseline regression commands. You MUST execute them first:
{commands}

If you determine that a command is clearly inapplicable (e.g., references a nonexistent \
test directory), you MAY skip it, but you MUST explain the reason in skipped_command_reason.
Do NOT skip a baseline command solely because it rebuilds native modules, reinstalls ignored \
artifacts, or writes normal build/test outputs that do not leave the repo git-dirty at the end.
After running the baseline commands, you may optionally run additional tests you discover."""

_BASELINE_WITHOUT_COMMANDS = """\
No baseline regression commands are configured. Discover the project's test framework \
and test suite autonomously. Look for pytest, unittest, jest, cargo test, go test, or \
other standard test runners. Execute the full test suite you discover."""


def _describe_allowed_when(allowed_when: str) -> str:
    if allowed_when == "never":
        return "Never run additional commands beyond the required set."
    if allowed_when == "after_required_commands":
        return "Additional commands are allowed only after all required commands finish."
    if allowed_when == "after_required_failure":
        return "Additional commands are allowed only if at least one required command fails."
    return "Additional commands are always allowed, subject to the configured budget."


def _format_policy_layer_commands(
    layer_entries: list[dict[str, Any]],
    *,
    trigger_reason: str,
) -> str:
    if not layer_entries:
        return "  - None"

    lines: list[str] = []
    for entry in layer_entries:
        layer_name = str(entry["layer"])
        commands = entry.get("commands", [])
        for command in commands:
            wrapped = format_harnessed_command(
                str(command),
                source="project_defined",
                trigger=f"{trigger_reason}:{layer_name}",
            )
            lines.append(f"  - `{wrapped}`")
    return "\n".join(lines)


def _format_project_defined_commands(commands: list[str]) -> str:
    if not commands:
        return "  - None"
    return "\n".join(f"  - `{command}`" for command in commands)


def _build_regression_policy_instructions(test_policy: dict[str, Any]) -> str:
    policy_source = str(test_policy.get("policy_source", "explicit"))
    required_layers = ", ".join(test_policy.get("required_layers", [])) or "None"
    optional_layers = ", ".join(test_policy.get("optional_layers", [])) or "None"
    allow_discovery = bool(test_policy.get("allow_discovery", False))
    additional_budget = int(test_policy.get("max_additional_commands", 0))
    allowed_when = str(test_policy.get("allowed_when", "never"))

    required_commands = _format_policy_layer_commands(
        test_policy.get("required_layer_commands", []),
        trigger_reason="legacy_baseline"
        if bool(test_policy.get("legacy_baseline", False))
        else "required_layer",
    )
    optional_commands = _format_policy_layer_commands(
        test_policy.get("optional_layer_commands", []),
        trigger_reason="optional_layer",
    )
    project_defined_commands = _format_project_defined_commands(
        [str(command) for command in test_policy.get("project_defined_commands", [])]
    )

    if policy_source == "legacy_regression":
        required_intro = (
            "The operator has provided these baseline regression commands. You MUST execute them "
            "first, in the exact order shown below:"
        )
    elif test_policy.get("required_commands"):
        required_intro = "Execute the required project-defined commands first, in the exact order:"
    else:
        required_intro = (
            "No project-defined required commands are configured. Use bounded discovery only."
        )

    discovery_rules = (
        "Discovery outside the project-defined command set is disabled.\n"
        if not allow_discovery
        else "If the additional-command gate is open, discovered commands must prefer standard "
        "test entrypoints such as `uv run pytest` / `pytest`, `npm test` / `pnpm test` / "
        "`yarn test`, `go test ./...`, `cargo test`, or `./gradlew test`.\n"
    )

    return (
        "## Execution Policy\n"
        f"- Policy source: {policy_source}\n"
        f"- Required layers: {required_layers}\n"
        f"- Optional layers: {optional_layers}\n"
        f"- allow_discovery: {str(allow_discovery).lower()}\n"
        f"- max_additional_commands: {additional_budget}\n"
        f"- allowed_when: {allowed_when}\n"
        f"- Gate semantics: {_describe_allowed_when(allowed_when)}\n\n"
        "## Project-defined Commands\n"
        "These are the raw project-defined commands that may appear in the authoritative audit:\n"
        f"{project_defined_commands}\n\n"
        "## Command Harness\n"
        f"- Execute every shell command through `{TEST_COMMAND_RUNNER_NAME}`.\n"
        "- Never run raw shell commands directly.\n"
        "- The harness records the authoritative command audit automatically.\n"
        f"- Use `{TEST_COMMAND_RUNNER_NAME} --source llm_discovered --trigger "
        "discovery_fallback --command '<RAW_COMMAND>'` for discovered commands.\n"
        f"- Use `{TEST_COMMAND_RUNNER_NAME} --source llm_diagnostic --trigger diagnostic "
        "--command '<RAW_COMMAND>'` for diagnostic commands.\n\n"
        "## Required Commands\n"
        f"{required_intro}\n"
        f"{required_commands}\n\n"
        "## Additional Commands\n"
        f"- You may spend at most {additional_budget} additional command slots after the required "
        "set, and only if the gate semantics allow it.\n"
        "- Use project-defined optional commands before any discovered or diagnostic command.\n"
        f"- Optional command candidates:\n{optional_commands}\n"
        f"- {discovery_rules}\n"
        "## Structured Result Contract\n"
        "- The harness ledger is the authoritative source of command execution facts.\n"
        "- `commands_attempted` and `command_audit` must always be present in the JSON output.\n"
        "- Set both fields to `null` when relying solely on the harness ledger.\n"
        "- When `commands_attempted` is not null, it must contain only raw shell command strings "
        "for executed policy-domain commands, in execution order.\n"
        "- When `command_audit` is not null, it must contain one entry per executed policy-domain "
        "command, in the same order as `commands_attempted`.\n"
        "- `command_audit.source` must be one of: "
        f"{', '.join(TEST_COMMAND_SOURCES)}.\n"
        "- `command_audit.trigger_reason` must be one of: "
        f"{', '.join(TEST_COMMAND_TRIGGER_REASONS)}.\n"
        "- `command_audit.exit_code` must be an integer when observed, otherwise null.\n"
        "- Do NOT include auxiliary inspection commands such as `git status`, `ls`, `pwd`, "
        "`rg`, or file-reading commands in `commands_attempted` / `command_audit`; summarize "
        "them in `discovery_notes` instead.\n"
        "- Keep `discovery_notes` as free text explaining how you chose or discovered policy "
        "commands, plus any auxiliary inspection performed outside the policy audit.\n"
        "- Do NOT add source labels inside `commands_attempted`.\n"
        "- If you skip a required legacy baseline command, explain it in "
        "`skipped_command_reason`.\n"
    )


def _is_auxiliary_inspection_command(command: str) -> bool:
    """Return True when a legacy self-reported command is clearly just workspace inspection."""
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return False

    first = ""
    for token in tokens:
        if "=" in token and not token.startswith("/") and token.index("=") > 0:
            continue
        first = token
        break

    if not first:
        return False

    if first in {"ls", "pwd", "rg", "grep", "sed", "cat", "head", "tail", "find", "nl"}:
        return True

    return first == "git"


def _validate_regression_command_audit(
    *,
    commands_attempted: list[str] | None,
    command_audit: list[Any],
    test_policy: EffectiveTestPolicy | None,
    skipped_command_reason: str | None,
) -> None:
    filtered_command_audit = [
        entry for entry in command_audit if not _is_auxiliary_inspection_command(entry.command)
    ]
    filtered_commands_attempted = (
        [command for command in commands_attempted if not _is_auxiliary_inspection_command(command)]
        if commands_attempted is not None
        else None
    )

    if filtered_commands_attempted is not None:
        audited_commands = [entry.command for entry in filtered_command_audit]
        if audited_commands != filtered_commands_attempted:
            raise ValueError("command_audit.command 必须与 commands_attempted 一一对应且顺序一致")

    if test_policy is None:
        return

    validate_command_audit(
        command_audit=filtered_command_audit,
        test_policy=test_policy,
        skipped_command_reason=skipped_command_reason,
    )


def _build_conflict_resolution_prompt(
    conflict_files: list[str],
    conflict_output: str,
    attempt: int,
) -> str:
    """构建 rebase 冲突解决的 Claude agent prompt。"""
    files_list = "\n".join(f"  - {f}" for f in conflict_files)
    retry_note = ""
    if attempt > 0:
        retry_note = (
            f"\n\nThis is retry attempt #{attempt + 1}. "
            "Previous attempt did not fully resolve all conflicts. "
            "Pay closer attention to the conflict markers and ensure "
            "every conflicted file is resolved correctly."
        )
    return f"""\
You are resolving git rebase merge conflicts in this worktree.

## Conflicted files
{files_list}

## Git output
```
{conflict_output[:2000]}
```

## Instructions

1. Read each conflicted file listed above.
2. Understand both sides of the conflict (HEAD vs incoming changes).
3. Resolve each conflict by editing the file to produce correct, working code
   that integrates both sides appropriately.
4. After resolving, run `git add <file>` for each resolved file.
5. Do NOT run `git rebase --continue` — the orchestrator will handle that.
6. Do NOT create new commits.
7. Do NOT run `git reset`, `git checkout`, `git switch`, `git stash`,
   `git clean`, or `git merge`.

Important:
- Remove ALL conflict markers (<<<<<<< , =======, >>>>>>>).
- Ensure the resolved code compiles/parses correctly.
- Prefer preserving both sides' intent when possible.
- If changes are incompatible, prefer the incoming (feature branch) version
  but adapt it to work with the current HEAD state.{retry_note}
"""


def _build_regression_prompt(repo_root: Path, settings: ATOSettings) -> str:
    """构建 regression runner 的 Codex prompt。"""
    policy = resolve_effective_test_policy(settings, "regression")
    if policy is None:
        baseline_instructions = _BASELINE_WITHOUT_COMMANDS
    else:
        baseline_instructions = _build_regression_policy_instructions(policy.model_dump())

    return _REGRESSION_PROMPT_TEMPLATE.format(
        repo_root=repo_root,
        baseline_instructions=baseline_instructions,
        test_command_runner=TEST_COMMAND_RUNNER_NAME,
    )


class _WorkspaceSnapshotError(Exception):
    """git status 失败，无法采集 workspace 变更快照。"""


async def _snapshot_workspace_changes(repo_root: Path) -> set[str]:
    """采集 repo root 的修改/暂存/untracked 文件集合。

    返回当前已有的变更路径集合，供调用后比较是否新增脏文件。
    不要求 repo 初始完全干净——只检测 regression 是否引入新变更。

    Raises:
        _WorkspaceSnapshotError: git status 非零退出时 fail-closed。
    """
    proc = await asyncio.create_subprocess_exec(
        "git",
        "status",
        "--porcelain",
        "-u",
        cwd=str(repo_root),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        stderr_text = stderr.decode(errors="replace").strip()[:200]
        raise _WorkspaceSnapshotError(f"git status failed (exit={proc.returncode}): {stderr_text}")
    paths: set[str] = set()
    for line in stdout.decode(errors="replace").splitlines():
        stripped = line.rstrip()
        if not stripped:
            continue
        # git status --porcelain: XY <path> or XY <old> -> <new>
        raw_path = stripped[3:]
        if " -> " in raw_path:
            raw_path = raw_path.split(" -> ", 1)[1]
        paths.add(raw_path)
    return paths


def _dirty_files_from_porcelain(porcelain_output: str) -> list[str]:
    """Extract file paths from git status --porcelain=v1 output for finalize context.

    Delegates to shared helper in worktree_mgr (Story 10.5 AC3).
    """
    from ato.worktree_mgr import dirty_files_from_porcelain

    return dirty_files_from_porcelain(porcelain_output)


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

    async def _submit_transition_event(self, story_id: str, event_name: str) -> None:
        """Submit a transition and wait for commit when supported by the queue."""
        event = TransitionEvent(
            story_id=story_id,
            event_name=event_name,
            source="agent",
            submitted_at=datetime.now(tz=UTC),
        )
        submit_and_wait = getattr(type(self._tq), "submit_and_wait", None)
        if callable(submit_and_wait):
            await self._tq.submit_and_wait(event)
            return
        await self._tq.submit(event)

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

        await self._submit_transition_event(story_id, "regression_pass")
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

    async def _run_pre_merge_gate(self, story_id: str) -> bool:
        """Run pre-merge worktree boundary gate before rebase/merge."""
        from ato.models.db import get_connection, save_worktree_preflight_result

        # Story 10.5 AC4: 防御性初始化 second_result
        second_result: WorktreePreflightResult | None = None
        db = await get_connection(self._db_path)
        try:
            first_result = await self._worktree_mgr.preflight_check(story_id, "pre_merge")
            await save_worktree_preflight_result(db, first_result, commit=True)
            if first_result.passed:
                return True

            await self._dispatch_finalize_for_preflight_failure(story_id, first_result)

            second_result = await self._worktree_mgr.preflight_check(story_id, "pre_merge")
            await save_worktree_preflight_result(db, second_result, commit=True)
            if second_result.passed:
                logger.info(
                    "merge_preflight_passed_after_finalize",
                    story_id=story_id,
                )
                return True
        finally:
            await db.close()

        # AC4: 若 second_result 未赋值（异常 unwind），fail closed
        if second_result is None:
            logger.error(
                "merge_preflight_second_result_unbound",
                story_id=story_id,
            )
            second_result = first_result
        await self._block_pre_merge_for_preflight_failure(story_id, second_result)
        return False

    async def _dispatch_finalize_for_preflight_failure(
        self,
        story_id: str,
        result: WorktreePreflightResult,
    ) -> None:
        """Run one finalize attempt after a failed pre-merge gate."""
        worktree_path = await self._worktree_mgr.get_path(story_id)
        if worktree_path is None:
            return

        from ato.adapters.claude_cli import ClaudeAdapter
        from ato.models.db import get_connection, get_story
        from ato.subprocess_mgr import SubprocessManager

        story = None
        db = await get_connection(self._db_path)
        try:
            story = await get_story(db, story_id)
        finally:
            await db.close()

        finalize_mgr = SubprocessManager(
            max_concurrent=1,
            adapter=ClaudeAdapter(),
            db_path=self._db_path,
        )
        try:
            finalize_result = await finalize_mgr.dispatch_finalize(
                story_id=story_id,
                worktree_path=str(worktree_path),
                story_summary=story.title if story is not None else story_id,
                dirty_files=_dirty_files_from_porcelain(result.porcelain_output),
            )
        except Exception:
            logger.warning("merge_preflight_finalize_failed", story_id=story_id, exc_info=True)
            return

        logger.info(
            "merge_preflight_finalize_completed",
            story_id=story_id,
            committed=finalize_result.committed,
            commit_sha=finalize_result.commit_sha,
            error=finalize_result.error,
        )

    async def _block_pre_merge_for_preflight_failure(
        self,
        story_id: str,
        result: WorktreePreflightResult,
    ) -> None:
        """Release merge queue lock, then create preflight_failure approval.

        Story 10.5 AC1: 先释放内部 lock，再暴露可操作 approval。
        """
        from ato.approval_helpers import create_approval
        from ato.models.db import complete_merge, get_connection, set_current_merge_story

        worktree_path = await self._worktree_mgr.get_path(story_id)
        db = await get_connection(self._db_path)
        try:
            # AC1: 先释放内部状态 lock
            try:
                await complete_merge(db, story_id, success=False)
            except Exception:
                logger.exception("merge_preflight_mark_failed_failed", story_id=story_id)
            await set_current_merge_story(db, None)

            # AC1: lock 释放后再创建可操作 approval
            await create_approval(
                db,
                story_id=story_id,
                approval_type="preflight_failure",
                payload_dict={
                    "gate_type": "pre_merge",
                    "retry_event": "merge_queue_retry",
                    "worktree_path": str(worktree_path) if worktree_path is not None else None,
                    "failure_reason": result.failure_reason,
                    "preflight_result": result.model_dump(mode="json"),
                    "options": ["manual_commit_and_retry", "escalate"],
                },
                recommended_action="manual_commit_and_retry",
                risk_level="medium",
            )
        finally:
            await db.close()

        logger.warning(
            "merge_preflight_blocked",
            story_id=story_id,
            failure_reason=result.failure_reason,
            base_ref=result.base_ref,
        )

    async def _execute_merge(self, story_id: str) -> None:
        """执行完整 merge 流程。"""
        from ato.models.db import get_connection, get_merge_queue_entry, mark_regression_dispatched

        resume_result = await self._resume_interrupted_rebase_if_needed(story_id)
        if resume_result is False:
            return
        if resume_result is None:
            gate_passed = await self._run_pre_merge_gate(story_id)
            if not gate_passed:
                return

            # Step 1: Rebase worktree onto main
            logger.info("merge_rebase_started", story_id=story_id)
            success, rebase_output = await self._worktree_mgr.rebase_onto_main(
                story_id,
                timeout_seconds=self._settings.merge_rebase_timeout,
            )

            if not success:
                # 检测冲突 — git 将 "CONFLICT" 输出到 stdout，
                # rebase_output 已合并 stdout+stderr。
                if "CONFLICT" in rebase_output:
                    logger.info("merge_rebase_conflict", story_id=story_id)
                    resolved = await self._handle_rebase_conflict(story_id, rebase_output)
                    if not resolved:
                        # escalate — approval 已创建，清理 current
                        await self._clear_current_merge_story_lock(
                            story_id,
                            context="rebase_conflict_escalated",
                        )
                        return
                else:
                    # 非冲突的 rebase 失败 — 尝试 abort 残留 rebase 状态
                    await self._worktree_mgr.abort_rebase(story_id)
                    logger.error("merge_rebase_failed", story_id=story_id, stderr=rebase_output)
                    await self._mark_merge_failed_and_release_lock(
                        story_id,
                        context="rebase_failed",
                    )
                    return

        # Step 2a: 记录首次 merge 前的 main HEAD（用于后续 recovery revert）
        try:
            db = await get_connection(self._db_path)
            try:
                entry = await get_merge_queue_entry(db, story_id)
            finally:
                await db.close()
        except Exception:
            logger.exception("merge_queue_entry_lookup_failed", story_id=story_id)
            await self._mark_merge_failed_and_release_lock(
                story_id,
                context="merge_queue_entry_lookup_failed",
            )
            return

        if entry is None:
            logger.error(
                "merge_queue_entry_missing",
                story_id=story_id,
                note="Cannot continue merge without queue tracking row",
            )
            await self._mark_merge_failed_and_release_lock(
                story_id,
                context="merge_queue_entry_missing",
            )
            return

        if entry.pre_merge_head is None:
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
        await self._submit_transition_event(story_id, "merge_done")

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

    async def _resume_interrupted_rebase_if_needed(self, story_id: str) -> bool | None:
        """Resume an already-started rebase before re-running merge boundary gates.

        Returns:
            ``None`` when no active rebase exists and normal merge flow should continue.
            ``True`` when an interrupted rebase was successfully resumed and merge can
            proceed to the fast-forward step.
            ``False`` when recovery already handled the failure/escalation and the
            caller should return.
        """
        in_progress, conflict_files, status_output = await self._worktree_mgr.inspect_rebase_state(
            story_id
        )
        if not in_progress:
            return None

        logger.warning(
            "merge_rebase_resume_detected",
            story_id=story_id,
            conflict_files=conflict_files,
        )

        if conflict_files:
            resolved = await self._handle_rebase_conflict(
                story_id,
                status_output or "Rebase already in progress.",
            )
            if not resolved:
                await self._clear_current_merge_story_lock(
                    story_id,
                    context="rebase_resume_conflict_escalated",
                )
                return False
            logger.info("merge_rebase_resumed_after_conflict", story_id=story_id)
            return True

        continue_success, continue_output = await self._worktree_mgr.continue_rebase(story_id)
        if not continue_success:
            if "CONFLICT" in continue_output:
                logger.info("merge_rebase_resume_conflict", story_id=story_id)
                resolved = await self._handle_rebase_conflict(story_id, continue_output)
                if not resolved:
                    await self._clear_current_merge_story_lock(
                        story_id,
                        context="rebase_resume_conflict_escalated",
                    )
                    return False
                logger.info("merge_rebase_resumed_after_conflict", story_id=story_id)
                return True

            await self._worktree_mgr.abort_rebase(story_id)
            logger.error("merge_rebase_resume_failed", story_id=story_id, stderr=continue_output)
            await self._mark_merge_failed_and_release_lock(
                story_id,
                context="rebase_resume_failed",
            )
            return False

        logger.info("merge_rebase_resumed", story_id=story_id)
        return True

    def _build_regression_dispatch_options(self) -> dict[str, Any]:
        """构建 Codex regression 调度选项，复用 phase 配置解析。"""
        from ato.recovery import RecoveryEngine

        phase_cfg = RecoveryEngine._resolve_phase_config_static(self._settings, "regression")

        opts: dict[str, Any] = {}

        # cwd: regression 在 main workspace 运行
        opts["cwd"] = str(self._worktree_mgr.project_root)

        # timeout
        timeout = phase_cfg.get("timeout_seconds")
        if timeout:
            opts["timeout"] = timeout

        # model
        if model := phase_cfg.get("model"):
            opts["model"] = model

        # reasoning
        if reasoning_effort := phase_cfg.get("reasoning_effort"):
            opts["reasoning_effort"] = reasoning_effort
        if reasoning_summary_format := phase_cfg.get("reasoning_summary_format"):
            opts["reasoning_summary_format"] = reasoning_summary_format

        # sandbox: 透传但不当安全保证
        if sandbox := phase_cfg.get("sandbox"):
            opts["sandbox"] = sandbox

        # output_schema for structured result
        opts["output_schema"] = _REGRESSION_RESULT_SCHEMA

        return opts

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

        # 启动后台 regression（Codex LLM runner）
        regression_task = asyncio.create_task(
            self._run_regression_via_codex(story_id, task_id),
            name=f"regression-{story_id}",
        )
        self._worker_tasks[f"regression-{story_id}"] = regression_task

        return task_id

    async def _run_regression_via_codex(self, story_id: str, task_id: str) -> None:
        """通过 Codex CLI 执行 regression 测试并归一化结构化结果。

        在 MainPathGate 独占模式内部调用 CodexAdapter + SubprocessManager，
        dispatch_with_retry(task_id=..., is_retry=True) 复用 _dispatch_regression_test
        预创建的 task 记录。
        """
        from ato.adapters.codex_cli import CodexAdapter
        from ato.core import get_main_path_gate
        from ato.models.db import get_connection, update_task_status
        from ato.subprocess_mgr import SubprocessManager

        repo_root = self._worktree_mgr.project_root
        gate = get_main_path_gate()

        try:
            async with gate.exclusive():
                # 采集 pre-run workspace 变更快照（fail-closed）
                try:
                    pre_snapshot = await _snapshot_workspace_changes(repo_root)
                except _WorkspaceSnapshotError as snap_err:
                    db = await get_connection(self._db_path)
                    try:
                        await update_task_status(
                            db,
                            task_id,
                            "failed",
                            exit_code=-1,
                            error_message=(f"Pre-run workspace snapshot failed: {snap_err}")[:500],
                            completed_at=datetime.now(tz=UTC),
                        )
                    finally:
                        await db.close()
                    return

                test_policy = resolve_effective_test_policy(self._settings, "regression")
                prompt = _build_regression_prompt(repo_root, self._settings)
                opts = self._build_regression_dispatch_options()

                adapter = CodexAdapter()
                mgr = SubprocessManager(
                    max_concurrent=1,
                    adapter=adapter,
                    db_path=self._db_path,
                )

                try:
                    result = await mgr.dispatch_with_retry(
                        story_id=story_id,
                        phase="regression",
                        role="qa",
                        cli_tool="codex",
                        prompt=prompt,
                        options=opts,
                        task_id=task_id,
                        is_retry=True,
                    )
                except CLIAdapterError:
                    # SubprocessManager 已写终态，只记日志
                    logger.warning(
                        "regression_codex_cli_error",
                        story_id=story_id,
                        task_id=task_id,
                        exc_info=True,
                    )
                    return

                # --- 归一化 structured result (Task 6) ---
                # Pydantic strict 校验：字段缺失、类型错误、非法枚举值
                # 一律 fail-closed
                from pydantic import ValidationError

                from ato.models.schemas import RegressionResult

                try:
                    reg_result = RegressionResult.model_validate(result.structured_output)
                    resolved_ledger_audit = (
                        await resolve_command_audit_from_ledger(
                            db_path=self._db_path,
                            task_id=task_id,
                            test_policy=test_policy,
                            skipped_command_reason=reg_result.skipped_command_reason,
                        )
                        if test_policy is not None
                        else None
                    )
                    if resolved_ledger_audit is not None:
                        if resolved_ledger_audit.audit_status is not None:
                            raise ValueError(
                                resolved_ledger_audit.detail
                                or "Regression command ledger validation failed"
                            )
                    elif reg_result.command_audit is None:
                        raise ValueError(
                            "Regression command audit missing: no harness ledger and no "
                            "legacy command_audit provided"
                        )
                    else:
                        _validate_regression_command_audit(
                            commands_attempted=reg_result.commands_attempted,
                            command_audit=reg_result.command_audit,
                            test_policy=test_policy,
                            skipped_command_reason=reg_result.skipped_command_reason,
                        )
                except (ValidationError, TypeError) as ve:
                    db = await get_connection(self._db_path)
                    try:
                        await update_task_status(
                            db,
                            task_id,
                            "completed",
                            exit_code=1,
                            error_message=(
                                f"Regression runner produced invalid structured result: {ve}"
                            )[:500],
                            completed_at=datetime.now(tz=UTC),
                        )
                    finally:
                        await db.close()
                    return
                except ValueError as ve:
                    db = await get_connection(self._db_path)
                    try:
                        await update_task_status(
                            db,
                            task_id,
                            "completed",
                            exit_code=1,
                            error_message=(f"Regression command audit validation failed: {ve}")[
                                :500
                            ],
                            completed_at=datetime.now(tz=UTC),
                        )
                    finally:
                        await db.close()
                    return

                if reg_result.regression_status == "fail":
                    db = await get_connection(self._db_path)
                    try:
                        await update_task_status(
                            db,
                            task_id,
                            "completed",
                            exit_code=1,
                            error_message=reg_result.summary[:500],
                            completed_at=datetime.now(tz=UTC),
                        )
                    finally:
                        await db.close()
                    return

                # --- workspace 新增脏文件保护 (Task 7) ---
                try:
                    post_snapshot = await _snapshot_workspace_changes(repo_root)
                except _WorkspaceSnapshotError as snap_err:
                    # git status 失败 → fail-closed
                    db = await get_connection(self._db_path)
                    try:
                        await update_task_status(
                            db,
                            task_id,
                            "completed",
                            exit_code=1,
                            error_message=(f"Workspace snapshot failed: {snap_err}")[:500],
                            completed_at=datetime.now(tz=UTC),
                        )
                    finally:
                        await db.close()
                    return

                new_dirty = post_snapshot - pre_snapshot
                if new_dirty:
                    dirty_list = ", ".join(sorted(new_dirty)[:10])
                    db = await get_connection(self._db_path)
                    try:
                        await update_task_status(
                            db,
                            task_id,
                            "completed",
                            exit_code=1,
                            error_message=(
                                f"Regression runner modified main workspace: {dirty_list}"
                            )[:500],
                            completed_at=datetime.now(tz=UTC),
                        )
                    finally:
                        await db.close()
                    return

                # regression_status == "pass" 且 workspace 干净 → 保持 exit_code=0
                # SubprocessManager 已写 exit_code=0，无需额外更新

        except CLIAdapterError:
            # 被 gate 外层捕获（不应发生，但防御性处理）
            logger.warning(
                "regression_codex_cli_error_outer",
                story_id=story_id,
                task_id=task_id,
                exc_info=True,
            )
        except Exception:
            logger.exception(
                "regression_codex_unexpected_error",
                story_id=story_id,
                task_id=task_id,
            )
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
                    # 读取 task error_message 作为测试输出摘要
                    # 回退：error_message 为空时截取 text_result
                    err_cursor = await db.execute(
                        "SELECT error_message, text_result FROM tasks WHERE task_id = ?",
                        (task_id,),
                    )
                    err_row = await err_cursor.fetchone()
                    test_output: str | None = None
                    if err_row:
                        test_output = err_row[0] if err_row[0] else None
                        if test_output is None and err_row[1]:
                            test_output = str(err_row[1])[:500]

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
        conflict_output: str,
    ) -> bool:
        """处理 rebase 冲突：调度 agent 修复，失败则 escalate。

        流程（FR52）：
        1. 获取冲突文件列表
        2. 循环尝试调度 Claude agent 解决冲突（最多 max_attempts 次）
        3. agent 解决后执行 ``git rebase --continue``
        4. 全部 commit 应用完毕 → 返回 True
        5. 所有尝试失败 → abort rebase，创建 approval escalate 给操作者

        Returns:
            True 表示冲突已解决，False 表示需要人工介入。
        """
        import uuid

        from ato.adapters.claude_cli import ClaudeAdapter
        from ato.subprocess_mgr import SubprocessManager

        conflict_files = await self._worktree_mgr.get_conflict_files(story_id)
        worktree_path = await self._worktree_mgr.get_path(story_id)

        max_attempts = self._settings.merge_conflict_resolution_max_attempts
        if max_attempts <= 0 or worktree_path is None:
            # 配置禁用自动解决或 worktree 不存在，直接 escalate
            logger.info(
                "merge_conflict_auto_resolve_disabled",
                story_id=story_id,
                max_attempts=max_attempts,
                conflict_files=conflict_files,
            )
            return await self._escalate_rebase_conflict(
                story_id,
                conflict_files,
                conflict_output,
            )

        adapter = ClaudeAdapter()
        mgr = SubprocessManager(
            max_concurrent=1,
            adapter=adapter,
            db_path=self._db_path,
        )

        for attempt in range(max_attempts):
            # 每轮重新获取冲突文件（上一轮可能部分解决）
            if attempt > 0:
                conflict_files = await self._worktree_mgr.get_conflict_files(story_id)
                if not conflict_files:
                    # 冲突已全部解决，尝试 continue
                    break

            logger.info(
                "merge_conflict_agent_dispatch",
                story_id=story_id,
                attempt=attempt + 1,
                max_attempts=max_attempts,
                conflict_files=conflict_files,
            )

            prompt = _build_conflict_resolution_prompt(
                conflict_files,
                conflict_output,
                attempt,
            )
            task_id = str(uuid.uuid4())
            opts: dict[str, Any] = {"cwd": str(worktree_path)}

            try:
                await mgr.dispatch_with_retry(
                    story_id=story_id,
                    phase="merge_conflict_resolution",
                    role="developer",
                    cli_tool="claude",
                    prompt=prompt,
                    options=opts,
                    task_id=task_id,
                    max_retries=0,
                )
            except CLIAdapterError:
                logger.warning(
                    "merge_conflict_agent_failed",
                    story_id=story_id,
                    attempt=attempt + 1,
                )
                continue

            # agent 完成后检查是否还有冲突文件
            remaining = await self._worktree_mgr.get_conflict_files(story_id)
            if remaining:
                logger.warning(
                    "merge_conflict_still_unresolved",
                    story_id=story_id,
                    remaining_files=remaining,
                    attempt=attempt + 1,
                )
                continue

            # 无冲突文件，尝试 rebase --continue
            break
        else:
            # 所有尝试用尽，escalate
            logger.error(
                "merge_conflict_auto_resolve_exhausted",
                story_id=story_id,
                attempts=max_attempts,
            )
            return await self._escalate_rebase_conflict(
                story_id,
                conflict_files,
                conflict_output,
            )

        # 冲突已解决，循环 rebase --continue 直到所有 commit 应用完毕
        # （rebase 可能在后续 commit 再次产生冲突）
        continue_success, continue_output = await self._worktree_mgr.continue_rebase(
            story_id,
        )
        if not continue_success:
            if "CONFLICT" in continue_output:
                # 后续 commit 又有冲突 — 递归处理
                logger.info(
                    "merge_conflict_subsequent_commit",
                    story_id=story_id,
                )
                return await self._handle_rebase_conflict(
                    story_id,
                    continue_output,
                )
            # 其他 rebase --continue 错误
            logger.error(
                "merge_rebase_continue_failed",
                story_id=story_id,
                stderr=continue_output,
            )
            return await self._escalate_rebase_conflict(
                story_id,
                conflict_files,
                continue_output,
            )

        logger.info("merge_conflict_resolved", story_id=story_id)
        return True

    async def _escalate_rebase_conflict(
        self,
        story_id: str,
        conflict_files: list[str],
        conflict_output: str,
    ) -> bool:
        """Abort rebase 并创建 approval escalate 给操作者。"""
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
                    "stderr": conflict_output[:500],
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
