"""test_worktree_lifecycle — Worktree 生命周期集成测试。

在 tmp 目录初始化真实 git repo，测试 create → verify isolation → cleanup 完整周期。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from ato.models.db import get_connection, init_db, insert_story
from ato.models.schemas import StoryRecord
from ato.worktree_mgr import BRANCH_PREFIX, WORKTREE_BASE, WorktreeManager

# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

_NOW = datetime.now(tz=UTC)


def _make_story(story_id: str = "story-integ") -> StoryRecord:
    return StoryRecord(
        story_id=story_id,
        title="集成测试 story",
        status="in_progress",
        current_phase="developing",
        created_at=_NOW,
        updated_at=_NOW,
    )


@pytest.fixture()
async def git_repo(tmp_path: Path) -> Path:
    """在 tmp_path 下初始化一个真实的 git 仓库。"""
    import asyncio

    repo = tmp_path / "project"
    repo.mkdir()

    # git init
    proc = await asyncio.create_subprocess_exec(
        "git",
        "init",
        cwd=str(repo),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()

    # 配置 git user（CI 环境可能没有全局配置）
    for cmd in [
        ["git", "config", "user.email", "test@test.com"],
        ["git", "config", "user.name", "Test User"],
    ]:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(repo),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

    # 创建初始 commit（空仓库不能创建 worktree）
    readme = repo / "README.md"
    readme.write_text("# Test Project\n")
    proc = await asyncio.create_subprocess_exec(
        "git",
        "add",
        ".",
        cwd=str(repo),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()
    proc = await asyncio.create_subprocess_exec(
        "git",
        "commit",
        "-m",
        "Initial commit",
        cwd=str(repo),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()

    return repo


@pytest.fixture()
async def setup(git_repo: Path, tmp_path: Path) -> tuple[WorktreeManager, Path]:
    """设置 WorktreeManager + DB。"""
    db_path = tmp_path / ".ato" / "state.db"
    await init_db(db_path)
    db = await get_connection(db_path)
    try:
        await insert_story(db, _make_story())
    finally:
        await db.close()
    mgr = WorktreeManager(project_root=git_repo, db_path=db_path)
    return mgr, db_path


# ---------------------------------------------------------------------------
# 完整生命周期
# ---------------------------------------------------------------------------


class TestWorktreeLifecycle:
    async def test_create_verify_cleanup(
        self,
        setup: tuple[WorktreeManager, Path],
        git_repo: Path,
    ) -> None:
        """测试完整 create → verify isolation → cleanup 生命周期。"""
        mgr, _db_path = setup

        # === CREATE ===
        wt_path = await mgr.create("story-integ")

        # 验证 worktree 目录存在
        assert wt_path.exists()
        assert wt_path.is_dir()
        assert (wt_path / "README.md").exists()

        # 验证路径格式
        expected_path = git_repo / WORKTREE_BASE / "story-integ"
        assert wt_path == expected_path

        # 验证 DB 记录
        assert await mgr.exists("story-integ") is True
        path_result = await mgr.get_path("story-integ")
        assert path_result == wt_path

        # === VERIFY ISOLATION ===
        # 在 worktree 中创建文件
        test_file = wt_path / "worktree_only.txt"
        test_file.write_text("This file only exists in worktree\n")

        # 主仓库不应包含此文件
        assert not (git_repo / "worktree_only.txt").exists()

        # === CLEANUP ===
        await mgr.cleanup("story-integ")

        # 验证 worktree 目录已删除
        assert not wt_path.exists()

        # 验证 DB 记录已清空
        assert await mgr.exists("story-integ") is False
        assert await mgr.get_path("story-integ") is None

    async def test_cleanup_removes_merged_branch(
        self,
        setup: tuple[WorktreeManager, Path],
        git_repo: Path,
    ) -> None:
        """cleanup 后已合并分支被删除。"""
        import asyncio

        mgr, _db_path = setup
        branch_name = f"{BRANCH_PREFIX}story-integ"

        await mgr.create("story-integ")

        # 验证分支存在
        proc = await asyncio.create_subprocess_exec(
            "git",
            "branch",
            "--list",
            branch_name,
            cwd=str(git_repo),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        assert branch_name in stdout.decode()

        await mgr.cleanup("story-integ")

        # 验证分支已被删除（因为 worktree 分支基于 HEAD 创建，没有新 commit，-d 可以删除）
        proc = await asyncio.create_subprocess_exec(
            "git",
            "branch",
            "--list",
            branch_name,
            cwd=str(git_repo),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        assert branch_name not in stdout.decode()

    async def test_create_idempotent_with_real_git(
        self,
        setup: tuple[WorktreeManager, Path],
    ) -> None:
        """已存在的有效 worktree 调用 create() 应幂等返回。"""
        mgr, _db_path = setup

        path1 = await mgr.create("story-integ")
        path2 = await mgr.create("story-integ")

        assert path1 == path2

    async def test_cleanup_idempotent(
        self,
        setup: tuple[WorktreeManager, Path],
    ) -> None:
        """重复 cleanup 不应抛异常。"""
        mgr, _db_path = setup

        await mgr.create("story-integ")
        await mgr.cleanup("story-integ")
        # 第二次 cleanup 应跳过
        await mgr.cleanup("story-integ")

    async def test_cleanup_custom_branch_with_real_git(
        self,
        setup: tuple[WorktreeManager, Path],
        git_repo: Path,
    ) -> None:
        """create() 使用自定义分支名后，cleanup() 应删除该自定义分支。"""
        import asyncio

        mgr, _db_path = setup
        custom_branch = "custom-feature-branch"

        await mgr.create("story-integ", branch_name=custom_branch)

        # 验证自定义分支存在
        proc = await asyncio.create_subprocess_exec(
            "git",
            "branch",
            "--list",
            custom_branch,
            cwd=str(git_repo),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        assert custom_branch in stdout.decode()

        await mgr.cleanup("story-integ")

        # 验证自定义分支已被删除
        proc = await asyncio.create_subprocess_exec(
            "git",
            "branch",
            "--list",
            custom_branch,
            cwd=str(git_repo),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        assert custom_branch not in stdout.decode()

    async def test_cleanup_handles_externally_removed_worktree(
        self,
        setup: tuple[WorktreeManager, Path],
        git_repo: Path,
    ) -> None:
        """worktree 目录被外部移除后，cleanup() 仍应正常完成并删除分支。"""
        import asyncio
        import shutil

        mgr, _db_path = setup
        branch_name = f"{BRANCH_PREFIX}story-integ"

        wt_path = await mgr.create("story-integ")
        assert wt_path.exists()

        # 外部移除 worktree 目录（模拟半途失败或人工干预）
        shutil.rmtree(wt_path)
        assert not wt_path.exists()

        # cleanup 应正常完成，不抛异常
        await mgr.cleanup("story-integ")

        # DB 记录已清空
        assert await mgr.exists("story-integ") is False
        assert await mgr.get_path("story-integ") is None

        # 分支也应被删除（回退到默认分支名约定）
        proc = await asyncio.create_subprocess_exec(
            "git",
            "branch",
            "--list",
            branch_name,
            cwd=str(git_repo),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        assert branch_name not in stdout.decode()

    async def test_cleanup_custom_branch_after_external_removal(
        self,
        setup: tuple[WorktreeManager, Path],
        git_repo: Path,
    ) -> None:
        """自定义分支 + 外部移除 worktree 后，cleanup() 仍应删除自定义分支。"""
        import asyncio
        import shutil

        mgr, _db_path = setup
        custom_branch = "my-custom-branch"

        wt_path = await mgr.create("story-integ", branch_name=custom_branch)
        assert wt_path.exists()

        # 验证自定义分支已创建
        proc = await asyncio.create_subprocess_exec(
            "git",
            "branch",
            "--list",
            custom_branch,
            cwd=str(git_repo),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        assert custom_branch in stdout.decode()

        # 外部移除 worktree 目录（git 元数据丢失）
        shutil.rmtree(wt_path)
        assert not wt_path.exists()

        # cleanup 应正常完成
        await mgr.cleanup("story-integ")

        # 自定义分支应被删除（通过元数据文件恢复分支名）
        proc = await asyncio.create_subprocess_exec(
            "git",
            "branch",
            "--list",
            custom_branch,
            cwd=str(git_repo),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        assert custom_branch not in stdout.decode()

        # DB 也应清空
        assert await mgr.exists("story-integ") is False

    async def test_create_idempotent_repairs_meta_then_external_removal(
        self,
        setup: tuple[WorktreeManager, Path],
        git_repo: Path,
    ) -> None:
        """create(custom) → 删除 .branch 文件 → 再次 create() → 外部 remove → cleanup()。"""
        import asyncio
        import shutil

        mgr, _db_path = setup
        custom_branch = "repaired-branch"

        # 1. 创建 worktree
        wt_path = await mgr.create("story-integ", branch_name=custom_branch)
        assert wt_path.exists()

        # 2. 删除 .branch 元数据文件（模拟丢失）
        meta_path = mgr._branch_meta_path("story-integ")
        assert meta_path.exists()
        meta_path.unlink()
        assert not meta_path.exists()

        # 3. 再次 create()（幂等路径应恢复元数据）
        await mgr.create("story-integ")
        assert meta_path.exists()
        assert meta_path.read_text().strip() == custom_branch

        # 4. 外部移除 worktree
        shutil.rmtree(wt_path)

        # 5. cleanup 应通过恢复的元数据找到并删除自定义分支
        await mgr.cleanup("story-integ")

        proc = await asyncio.create_subprocess_exec(
            "git",
            "branch",
            "--list",
            custom_branch,
            cwd=str(git_repo),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        assert custom_branch not in stdout.decode()

    async def test_cleanup_preserves_meta_for_unmerged_branch(
        self,
        setup: tuple[WorktreeManager, Path],
        git_repo: Path,
    ) -> None:
        """未合并分支的 cleanup() 应保留 .branch 元数据文件。"""
        import asyncio

        mgr, _db_path = setup
        custom_branch = "unmerged-feature"

        wt_path = await mgr.create("story-integ", branch_name=custom_branch)

        # 在 worktree 中提交一个 commit（使分支无法用 -d 删除）
        test_file = wt_path / "new_file.txt"
        test_file.write_text("unmerged content\n")
        for cmd in [
            ["git", "add", "."],
            ["git", "commit", "-m", "unmerged commit"],
        ]:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(wt_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()

        await mgr.cleanup("story-integ")

        # 分支应保留（未合并）
        proc = await asyncio.create_subprocess_exec(
            "git",
            "branch",
            "--list",
            custom_branch,
            cwd=str(git_repo),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        assert custom_branch in stdout.decode()

        # 元数据文件也应保留
        assert mgr._branch_meta_path("story-integ").exists()
        assert mgr._branch_meta_path("story-integ").read_text().strip() == custom_branch

    async def test_worktree_changes_isolated_from_main(
        self,
        setup: tuple[WorktreeManager, Path],
        git_repo: Path,
    ) -> None:
        """worktree 中的文件变更不影响主仓库工作目录。"""
        mgr, _db_path = setup

        wt_path = await mgr.create("story-integ")

        # 在 worktree 中修改 README
        wt_readme = wt_path / "README.md"
        wt_readme.write_text("# Modified in worktree\n")

        # 主仓库的 README 应保持不变
        main_readme = git_repo / "README.md"
        assert main_readme.read_text() == "# Test Project\n"

        await mgr.cleanup("story-integ")
