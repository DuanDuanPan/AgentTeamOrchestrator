"""worktree_mgr — Git worktree 生命周期管理。

管理 story 级别的 git worktree 创建、清理与查询。
WorktreeManager 管理 git 基础设施（worktree 生命周期），
与 SubprocessManager（管理 agent CLI 调度）职责分离。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import structlog

from ato.adapters.base import cleanup_process
from ato.models.schemas import WorktreeError

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

WORKTREE_BASE = ".worktrees"
"""Worktree 存放子目录名（相对于 project_root）。"""

BRANCH_PREFIX = "worktree-story-"
"""自动生成的 worktree 分支名前缀。"""

_GIT_TIMEOUT_SECONDS = 30
"""Git 命令超时时间（秒）。"""


class WorktreeManager:
    """Git worktree 生命周期管理器。

    职责：
    - 为每个 story 创建独立的 git worktree
    - 清理已完成 story 的 worktree 和分支
    - 查询 worktree 路径和状态

    Args:
        project_root: 目标项目 git 仓库根路径。
        db_path: SQLite 数据库文件路径。
    """

    def __init__(self, *, project_root: Path, db_path: Path) -> None:
        self._project_root = project_root
        self._db_path = db_path

    async def create(
        self,
        story_id: str,
        branch_name: str | None = None,
        *,
        base_ref: str = "HEAD",
    ) -> Path:
        """创建 story 对应的 git worktree。

        Args:
            story_id: Story 唯一标识。
            branch_name: 分支名，默认 ``worktree-story-{story_id}``。
            base_ref: 基准引用（默认 HEAD）。

        Returns:
            Worktree 绝对路径。

        Raises:
            WorktreeError: git 命令执行失败。
        """
        if branch_name is None:
            branch_name = f"{BRANCH_PREFIX}{story_id}"

        worktree_path = self._project_root / WORKTREE_BASE / story_id

        # 幂等检查：路径已存在且是有效 worktree
        if worktree_path.exists() and await self._is_valid_worktree(worktree_path):
            # 补写 DB（崩溃恢复场景：worktree 存在但 DB 为 NULL）
            from ato.models.db import get_connection, update_story_worktree_path

            db = await get_connection(self._db_path)
            try:
                await update_story_worktree_path(db, story_id, str(worktree_path))
            finally:
                await db.close()

            # 补写分支元数据（文件丢失或内容损坏时从 git 元数据恢复）
            if not self._has_valid_branch_meta(story_id):
                actual_branch = await self._get_worktree_branch(worktree_path)
                if actual_branch is not None:
                    self._save_branch_meta(story_id, actual_branch)

            logger.info(
                "worktree_already_exists",
                story_id=story_id,
                path=str(worktree_path),
            )
            return worktree_path

        returncode, _stdout, stderr = await self._run_git(
            "worktree",
            "add",
            "-b",
            branch_name,
            str(worktree_path),
            base_ref,
        )
        if returncode != 0:
            raise WorktreeError(
                f"Failed to create worktree for story '{story_id}': {stderr}",
                stderr=stderr,
                story_id=story_id,
            )

        # 持久化分支名（外部移除 worktree 后仍可读回）
        self._save_branch_meta(story_id, branch_name)

        # 更新 DB
        from ato.models.db import get_connection, update_story_worktree_path

        db = await get_connection(self._db_path)
        try:
            await update_story_worktree_path(db, story_id, str(worktree_path))
        finally:
            await db.close()

        logger.info(
            "worktree_created",
            story_id=story_id,
            path=str(worktree_path),
            branch_name=branch_name,
        )
        return worktree_path

    async def cleanup(self, story_id: str) -> None:
        """清理 story 对应的 git worktree 和分支。

        幂等：若 worktree_path 为 None 则跳过。
        分支删除仅使用安全删除（``-d``），失败时仅记录 warning。

        Args:
            story_id: Story 唯一标识。

        Raises:
            WorktreeError: git worktree remove 失败。
        """
        from ato.models.db import get_connection, get_story, update_story_worktree_path

        db = await get_connection(self._db_path)
        try:
            story = await get_story(db, story_id)
        finally:
            await db.close()

        if story is None or story.worktree_path is None:
            logger.info("worktree_cleanup_skipped", story_id=story_id, reason="no_worktree_path")
            return

        worktree_path = story.worktree_path

        # 解析分支名：git 元数据 → 持久化文件 → 默认约定
        branch_name = await self._get_worktree_branch(Path(worktree_path))
        if branch_name is None:
            branch_name = self._load_branch_meta(story_id)

        # git worktree remove（幂等：目录已不存在时跳过）
        wt_path_obj = Path(worktree_path)
        if wt_path_obj.exists() or await self._is_valid_worktree(wt_path_obj):
            returncode, _stdout, stderr = await self._run_git(
                "worktree",
                "remove",
                worktree_path,
                "--force",
            )
            if returncode != 0:
                raise WorktreeError(
                    f"Failed to remove worktree for story '{story_id}': {stderr}",
                    stderr=stderr,
                    story_id=story_id,
                )
        else:
            # worktree 已被外部移除，执行 git worktree prune 清理残留元数据
            await self._run_git("worktree", "prune")
            logger.info(
                "worktree_already_removed",
                story_id=story_id,
                path=worktree_path,
            )

        # 安全删除分支（仅 -d，失败不抛异常）
        br_returncode, _br_stdout, br_stderr = await self._run_git(
            "branch",
            "-d",
            branch_name,
        )
        if br_returncode != 0:
            # 分支未合并时保留元数据，供后续 merge/cleanup 流程识别
            logger.warning(
                "worktree_branch_delete_failed",
                story_id=story_id,
                branch_name=branch_name,
                stderr=br_stderr,
            )
        else:
            # 分支已删除，清理元数据文件
            self._delete_branch_meta(story_id)

        # 清空 DB worktree_path
        db = await get_connection(self._db_path)
        try:
            await update_story_worktree_path(db, story_id, None)
        finally:
            await db.close()

        logger.info("worktree_cleaned", story_id=story_id)

    async def get_path(self, story_id: str) -> Path | None:
        """查询 story 当前 worktree 路径。

        Returns:
            Worktree 路径或 None。
        """
        from ato.models.db import get_connection, get_story

        db = await get_connection(self._db_path)
        try:
            story = await get_story(db, story_id)
        finally:
            await db.close()

        if story is None or story.worktree_path is None:
            return None
        return Path(story.worktree_path)

    async def has_new_commits(
        self,
        worktree_path: Path,
        since_rev: str,
    ) -> bool:
        """检测 worktree 中是否有新 commit。

        使用 ``git log <since_rev>..HEAD --oneline`` 检测。

        Args:
            worktree_path: Worktree 绝对路径。
            since_rev: 基准 commit（来自 session sidecar 的 base_commit）。

        Returns:
            True 表示有新 commit，False 表示无。
        """
        returncode, stdout, _stderr = await self._run_git(
            "-C",
            str(worktree_path),
            "log",
            f"{since_rev}..HEAD",
            "--oneline",
        )
        if returncode != 0:
            logger.warning(
                "has_new_commits_git_error",
                worktree_path=str(worktree_path),
                since_rev=since_rev,
                returncode=returncode,
            )
            return False
        return bool(stdout.strip())

    async def exists(self, story_id: str) -> bool:
        """检查 story 的 worktree 是否存在。

        同时验证 DB 记录和目录实际存在性。
        """
        path = await self.get_path(story_id)
        if path is None:
            return False
        return path.exists()

    @property
    def project_root(self) -> Path:
        """目标项目 git 仓库根路径（只读）。"""
        return self._project_root

    async def rebase_onto_main(
        self,
        story_id: str,
        *,
        timeout_seconds: int | None = None,
    ) -> tuple[bool, str]:
        """在 worktree 中 rebase 到 main 分支。

        Args:
            story_id: Story 唯一标识。
            timeout_seconds: 超时秒数，None 则使用默认值。

        Returns:
            (success, combined_output) 元组。combined_output 包含
            stdout + stderr，确保 git 输出的 ``CONFLICT`` 关键字
            （位于 stdout）不会丢失。
        """
        worktree_path = await self.get_path(story_id)
        if worktree_path is None:
            return False, f"No worktree found for story '{story_id}'"

        timeout = timeout_seconds or _GIT_TIMEOUT_SECONDS

        # fetch latest main (if remote exists)
        fetch_rc, _fetch_out, fetch_err = await self._run_git(
            "-C",
            str(worktree_path),
            "fetch",
            "origin",
            "main",
            timeout_seconds=timeout,
        )
        # fetch 成功 → rebase origin/main（最新远端）
        # fetch 失败 → 回退到本地 main（本地仓库或网络问题）
        if fetch_rc != 0:
            logger.warning(
                "merge_fetch_main_failed",
                story_id=story_id,
                stderr=fetch_err,
                note="Falling back to local main branch",
            )
            rebase_target = "main"
        else:
            rebase_target = "origin/main"

        # rebase onto target
        returncode, stdout, stderr = await self._run_git(
            "-C",
            str(worktree_path),
            "rebase",
            rebase_target,
            timeout_seconds=timeout,
        )
        if returncode != 0:
            # git rebase 将 "CONFLICT ..." 输出到 stdout，
            # "error: could not apply ..." 输出到 stderr。
            # 合并两者确保调用方能检测冲突关键字。
            combined = stdout + stderr
            return False, combined

        return True, ""

    async def continue_rebase(self, story_id: str) -> tuple[bool, str]:
        """在 worktree 中执行 git rebase --continue。

        Returns:
            (success, combined_output) — 合并 stdout+stderr
            确保 ``CONFLICT`` 关键字可被调用方检测。
        """
        worktree_path = await self.get_path(story_id)
        if worktree_path is None:
            return False, f"No worktree found for story '{story_id}'"

        returncode, stdout, stderr = await self._run_git(
            "-C",
            str(worktree_path),
            "rebase",
            "--continue",
        )
        if returncode != 0:
            return False, stdout + stderr
        return True, ""

    async def abort_rebase(self, story_id: str) -> None:
        """在 worktree 中执行 git rebase --abort。"""
        worktree_path = await self.get_path(story_id)
        if worktree_path is None:
            return

        await self._run_git("-C", str(worktree_path), "rebase", "--abort")

    async def merge_to_main(self, story_id: str) -> tuple[bool, str]:
        """将 story 分支 fast-forward merge 到 main。

        在主仓库（非 worktree）中执行。
        成功后不立刻 cleanup worktree——regression 闭环完成后才清理。

        Returns:
            (success, stderr) 元组。
        """
        branch_name = self._load_branch_meta(story_id)

        # checkout main
        returncode, _stdout, stderr = await self._run_git("checkout", "main")
        if returncode != 0:
            return False, f"Failed to checkout main: {stderr}"

        # fast-forward merge
        returncode, _stdout, stderr = await self._run_git(
            "merge",
            "--ff-only",
            branch_name,
        )
        if returncode != 0:
            return False, f"Fast-forward merge failed: {stderr}"

        return True, ""

    async def get_main_head(self) -> str | None:
        """获取主仓库 main 分支当前 HEAD commit hash。"""
        returncode, stdout, _stderr = await self._run_git("rev-parse", "main")
        if returncode != 0:
            return None
        return stdout.strip() or None

    async def revert_merge_range(self, pre_merge_head: str) -> tuple[bool, str]:
        """安全 revert ff merge 带入的所有 commit。

        使用 ``git revert --no-edit <pre_merge_head>..HEAD`` 创建新的 revert
        commit，保留完整历史（不丢弃任何 commit）。

        Args:
            pre_merge_head: merge 前 main 的 HEAD commit hash。

        Returns:
            (success, stderr) 元组。
        """
        returncode, _stdout, stderr = await self._run_git(
            "revert",
            "--no-edit",
            f"{pre_merge_head}..HEAD",
        )
        return returncode == 0, stderr

    async def get_conflict_files(self, story_id: str) -> list[str]:
        """获取 worktree 中的冲突文件列表。"""
        worktree_path = await self.get_path(story_id)
        if worktree_path is None:
            return []

        returncode, stdout, _stderr = await self._run_git(
            "-C",
            str(worktree_path),
            "diff",
            "--name-only",
            "--diff-filter=U",
        )
        if returncode != 0:
            return []
        return [f for f in stdout.strip().splitlines() if f]

    async def _run_git(
        self,
        *args: str,
        timeout_seconds: int | None = None,
    ) -> tuple[int, str, str]:
        """执行 git 命令。

        通过 ``asyncio.create_subprocess_exec`` 异步执行，
        ``try/finally`` + ``cleanup_process()`` 三阶段清理。

        Args:
            *args: git 子命令及参数。
            timeout_seconds: 超时秒数，None 则使用默认值。

        Returns:
            (returncode, stdout, stderr) 元组。

        Raises:
            WorktreeError: 命令超时。
        """
        timeout = timeout_seconds if timeout_seconds is not None else _GIT_TIMEOUT_SECONDS
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self._project_root),
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout,
            )
        except TimeoutError:
            await cleanup_process(proc)
            raise WorktreeError(
                f"Git command timed out after {timeout}s: git {' '.join(args)}",
            ) from None
        finally:
            await cleanup_process(proc)

        return (
            proc.returncode or 0,
            stdout_bytes.decode() if stdout_bytes else "",
            stderr_bytes.decode() if stderr_bytes else "",
        )

    def _branch_meta_path(self, story_id: str) -> Path:
        """分支名元数据文件路径：``{project_root}/.worktrees/{story_id}.branch``。"""
        return self._project_root / WORKTREE_BASE / f"{story_id}.branch"

    def _save_branch_meta(self, story_id: str, branch_name: str) -> None:
        """持久化分支名到元数据文件。"""
        meta_path = self._branch_meta_path(story_id)
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(branch_name)

    def _load_branch_meta(self, story_id: str) -> str:
        """读取持久化的分支名，文件不存在时回退到默认约定。"""
        meta_path = self._branch_meta_path(story_id)
        if meta_path.exists():
            content = meta_path.read_text().strip()
            if content:
                return content
        return f"{BRANCH_PREFIX}{story_id}"

    def _has_valid_branch_meta(self, story_id: str) -> bool:
        """检查分支名元数据文件是否存在且内容有效。"""
        meta_path = self._branch_meta_path(story_id)
        if not meta_path.exists():
            return False
        return bool(meta_path.read_text().strip())

    def _delete_branch_meta(self, story_id: str) -> None:
        """删除分支名元数据文件。"""
        meta_path = self._branch_meta_path(story_id)
        if meta_path.exists():
            meta_path.unlink()

    async def _get_worktree_branch(self, path: Path) -> str | None:
        """从 git worktree list --porcelain 中提取 worktree 关联的分支名。

        Returns:
            分支短名（如 ``worktree-story-1``），未找到则返回 None。
        """
        returncode, stdout, _stderr = await self._run_git(
            "worktree",
            "list",
            "--porcelain",
        )
        if returncode != 0:
            return None

        resolved = str(path.resolve())
        lines = stdout.splitlines()
        for i, line in enumerate(lines):
            if line.startswith("worktree ") and line[9:] == resolved:
                # 在后续行中找 branch 行
                for j in range(i + 1, len(lines)):
                    if lines[j] == "":
                        break  # 到了下一个 worktree 条目
                    if lines[j].startswith("branch "):
                        ref = lines[j][7:]  # e.g. "refs/heads/worktree-story-1"
                        # 提取短名
                        prefix = "refs/heads/"
                        if ref.startswith(prefix):
                            return ref[len(prefix) :]
                        return ref
                break
        return None

    async def _is_valid_worktree(self, path: Path) -> bool:
        """检查路径是否是当前仓库的有效 worktree。"""
        returncode, stdout, _stderr = await self._run_git(
            "worktree",
            "list",
            "--porcelain",
        )
        if returncode != 0:
            return False
        # 检查输出中是否包含该路径
        resolved = str(path.resolve())
        for line in stdout.splitlines():
            if line.startswith("worktree ") and line[9:] == resolved:
                return True
        return False

    # ------------------------------------------------------------------
    # Batch spec commit — Story 9.3
    # ------------------------------------------------------------------

    async def batch_spec_commit(
        self,
        batch_id: str,
        story_ids: list[str],
    ) -> tuple[bool, str]:
        """将 batch 内所有 story 的规格文件提交到本地 main。

        Stage 路径：
        - ``_bmad-output/implementation-artifacts/{story_id}.md``
        - ``_bmad-output/implementation-artifacts/{story_id}-ux/`` (若存在)

        Args:
            batch_id: Batch 唯一标识。
            story_ids: Batch 内的 story_id 列表。

        Returns:
            (success, message) 元组。success 为 True 时 message 是 commit hash，
            False 时 message 是错误信息。
        """
        spec_dir = "_bmad-output/implementation-artifacts"
        paths_to_add: list[str] = []
        for sid in story_ids:
            spec_file = f"{spec_dir}/{sid}.md"
            spec_ux_dir = f"{spec_dir}/{sid}-ux/"
            # 只 stage 存在的文件/目录
            full_spec = self._project_root / spec_file
            if full_spec.exists():
                paths_to_add.append(spec_file)
            full_ux = self._project_root / spec_ux_dir.rstrip("/")
            if full_ux.exists() and full_ux.is_dir():
                paths_to_add.append(spec_ux_dir)

        if not paths_to_add:
            return True, "no spec files to commit (idempotent)"

        # 幂等检查：如果工作树无差异，视为已提交。
        # 必须验证返回码——非零退出说明 git 状态异常，不能假设"无变更"。
        rc_diff, diff_out, stderr_diff = await self._run_git(
            "diff", "--name-only", "--", *paths_to_add
        )
        rc_staged, staged_out, stderr_staged = await self._run_git(
            "diff", "--cached", "--name-only", "--", *paths_to_add
        )
        # 检查是否有 untracked 的 spec files
        rc_ls, ls_out, stderr_ls = await self._run_git(
            "ls-files", "--others", "--exclude-standard", "--", *paths_to_add
        )

        # 任一 git 探测命令失败 → 不能做幂等假设，报错让上层处理
        if rc_diff != 0:
            return False, f"git diff failed (rc={rc_diff}): {stderr_diff}"
        if rc_staged != 0:
            return False, f"git diff --cached failed (rc={rc_staged}): {stderr_staged}"
        if rc_ls != 0:
            return False, f"git ls-files failed (rc={rc_ls}): {stderr_ls}"

        has_changes = bool(diff_out.strip() or staged_out.strip() or ls_out.strip())
        if not has_changes:
            return True, "all spec files already committed (idempotent)"

        # git add — 仅 stage spec paths
        rc_add, _, stderr_add = await self._run_git("add", "--", *paths_to_add)
        if rc_add != 0:
            return False, f"git add failed: {stderr_add}"

        # git commit — 使用 pathspec 限定只提交 spec 文件，
        # 不吞 index 中其他已暂存的无关变更。
        commit_msg = f"spec(batch-{batch_id}): add validated story specifications"
        rc_commit, _stdout_commit, stderr_commit = await self._run_git(
            "commit",
            "-m",
            commit_msg,
            "--",
            *paths_to_add,
        )
        if rc_commit != 0:
            return False, f"git commit failed: {stderr_commit}"

        # 获取 commit hash
        rc_rev, commit_hash, _ = await self._run_git("rev-parse", "HEAD")
        commit_hash = commit_hash.strip() if rc_rev == 0 else "unknown"

        logger.info(
            "batch_spec_committed",
            batch_id=batch_id,
            story_ids=story_ids,
            commit_hash=commit_hash,
        )
        return True, commit_hash
