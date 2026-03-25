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

    async def exists(self, story_id: str) -> bool:
        """检查 story 的 worktree 是否存在。

        同时验证 DB 记录和目录实际存在性。
        """
        path = await self.get_path(story_id)
        if path is None:
            return False
        return path.exists()

    async def _run_git(self, *args: str) -> tuple[int, str, str]:
        """执行 git 命令。

        通过 ``asyncio.create_subprocess_exec`` 异步执行，
        ``try/finally`` + ``cleanup_process()`` 三阶段清理。

        Args:
            *args: git 子命令及参数。

        Returns:
            (returncode, stdout, stderr) 元组。

        Raises:
            WorktreeError: 命令超时。
        """
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
                timeout=_GIT_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            await cleanup_process(proc)
            raise WorktreeError(
                f"Git command timed out after {_GIT_TIMEOUT_SECONDS}s: git {' '.join(args)}",
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
