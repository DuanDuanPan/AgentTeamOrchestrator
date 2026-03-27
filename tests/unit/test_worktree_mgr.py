"""test_worktree_mgr — WorktreeManager 单元测试。

所有 git 命令通过 mock asyncio.create_subprocess_exec 模拟，不调用真实 CLI。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ato.models.db import get_connection, get_story, init_db, insert_story
from ato.models.schemas import StoryRecord, WorktreeError
from ato.worktree_mgr import BRANCH_PREFIX, WORKTREE_BASE, WorktreeManager

# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

_NOW = datetime.now(tz=UTC)


def _make_story(story_id: str = "story-1", worktree_path: str | None = None) -> StoryRecord:
    return StoryRecord(
        story_id=story_id,
        title="测试 story",
        status="in_progress",
        current_phase="developing",
        worktree_path=worktree_path,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _make_proc_mock(
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> AsyncMock:
    """创建 mock Process 对象。"""
    proc = AsyncMock(spec=asyncio.subprocess.Process)
    proc.returncode = returncode
    proc.communicate = AsyncMock(
        return_value=(stdout.encode(), stderr.encode()),
    )
    proc.terminate = MagicMock()
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    return proc


@pytest.fixture()
async def db_ready(tmp_path: Path) -> Path:
    db_path = tmp_path / ".ato" / "state.db"
    await init_db(db_path)
    db = await get_connection(db_path)
    try:
        await insert_story(db, _make_story())
    finally:
        await db.close()
    return db_path


@pytest.fixture()
def mgr(tmp_path: Path, db_ready: Path) -> WorktreeManager:
    """创建一个指向 tmp_path 的 WorktreeManager。"""
    return WorktreeManager(project_root=tmp_path, db_path=db_ready)


# ---------------------------------------------------------------------------
# create() 成功路径
# ---------------------------------------------------------------------------


class TestCreateSuccess:
    async def test_create_executes_git_worktree_add(
        self,
        mgr: WorktreeManager,
        tmp_path: Path,
        db_ready: Path,
    ) -> None:
        """create() 成功时执行正确的 git worktree add 命令并更新 DB。"""
        proc = _make_proc_mock(returncode=0)

        patch_target = "ato.worktree_mgr.asyncio.create_subprocess_exec"
        with patch(patch_target, return_value=proc) as mock_exec:
            result = await mgr.create("story-1")

        expected_path = tmp_path / WORKTREE_BASE / "story-1"
        assert result == expected_path

        # 验证 git 命令参数
        call_args = mock_exec.call_args
        assert call_args[0] == (
            "git",
            "worktree",
            "add",
            "-b",
            f"{BRANCH_PREFIX}story-1",
            str(expected_path),
            "HEAD",
        )

        # 验证 DB 更新
        db = await get_connection(db_ready)
        try:
            story = await get_story(db, "story-1")
        finally:
            await db.close()
        assert story is not None
        assert story.worktree_path == str(expected_path)

    async def test_create_custom_branch_and_base_ref(
        self,
        mgr: WorktreeManager,
        tmp_path: Path,
    ) -> None:
        """create() 支持自定义 branch_name 和 base_ref。"""
        proc = _make_proc_mock(returncode=0)

        patch_target = "ato.worktree_mgr.asyncio.create_subprocess_exec"
        with patch(patch_target, return_value=proc) as mock_exec:
            await mgr.create("story-1", branch_name="custom-branch", base_ref="main")

        call_args = mock_exec.call_args
        assert call_args[0] == (
            "git",
            "worktree",
            "add",
            "-b",
            "custom-branch",
            str(tmp_path / WORKTREE_BASE / "story-1"),
            "main",
        )


# ---------------------------------------------------------------------------
# create() 幂等性
# ---------------------------------------------------------------------------


class TestCreateIdempotent:
    async def test_create_returns_existing_path_when_valid_worktree(
        self,
        mgr: WorktreeManager,
        tmp_path: Path,
    ) -> None:
        """路径已存在且是有效 worktree 时直接返回，不执行 git worktree add。"""
        worktree_path = tmp_path / WORKTREE_BASE / "story-1"
        worktree_path.mkdir(parents=True)

        # Mock _is_valid_worktree 返回 True（通过 mock git worktree list）
        resolved = str(worktree_path.resolve())
        list_output = f"worktree {resolved}\nHEAD abc123\nbranch refs/heads/test\n\n"
        proc = _make_proc_mock(returncode=0, stdout=list_output)

        patch_target = "ato.worktree_mgr.asyncio.create_subprocess_exec"
        with patch(patch_target, return_value=proc) as mock_exec:
            result = await mgr.create("story-1")

        assert result == worktree_path
        # 调用了 git worktree list（幂等检查 + 元数据修复），没有调用 git worktree add
        for call in mock_exec.call_args_list:
            assert call[0][1:3] == ("worktree", "list")

    async def test_create_idempotent_repairs_null_db(
        self,
        mgr: WorktreeManager,
        tmp_path: Path,
        db_ready: Path,
    ) -> None:
        """worktree 已存在但 DB worktree_path=NULL 时，create() 应补写 DB。"""
        worktree_path = tmp_path / WORKTREE_BASE / "story-1"
        worktree_path.mkdir(parents=True)

        # 验证 DB 中 worktree_path 为 None
        db = await get_connection(db_ready)
        try:
            story = await get_story(db, "story-1")
        finally:
            await db.close()
        assert story is not None
        assert story.worktree_path is None

        resolved = str(worktree_path.resolve())
        list_output = f"worktree {resolved}\nHEAD abc\nbranch refs/heads/test\n\n"
        proc = _make_proc_mock(returncode=0, stdout=list_output)

        patch_target = "ato.worktree_mgr.asyncio.create_subprocess_exec"
        with patch(patch_target, return_value=proc):
            result = await mgr.create("story-1")

        assert result == worktree_path

        # 验证 DB worktree_path 已被补写
        db = await get_connection(db_ready)
        try:
            story = await get_story(db, "story-1")
        finally:
            await db.close()
        assert story is not None
        assert story.worktree_path == str(worktree_path)


# ---------------------------------------------------------------------------
# create() 失败
# ---------------------------------------------------------------------------


class TestCreateFailure:
    async def test_create_raises_worktree_error_on_git_failure(
        self,
        mgr: WorktreeManager,
    ) -> None:
        """git 命令 exit_code≠0 时抛出 WorktreeError，携带 stderr。"""
        proc = _make_proc_mock(returncode=128, stderr="fatal: branch already exists")

        patch_target = "ato.worktree_mgr.asyncio.create_subprocess_exec"
        with patch(patch_target, return_value=proc), pytest.raises(WorktreeError) as exc_info:
            await mgr.create("story-1")

        assert "fatal: branch already exists" in exc_info.value.stderr
        assert exc_info.value.story_id == "story-1"


# ---------------------------------------------------------------------------
# cleanup() 成功路径
# ---------------------------------------------------------------------------


class TestCleanupSuccess:
    async def test_cleanup_removes_worktree_and_branch(
        self,
        mgr: WorktreeManager,
        db_ready: Path,
        tmp_path: Path,
    ) -> None:
        """cleanup() 执行 git worktree remove + git branch -d 并清空 DB。"""
        wt_path = tmp_path / WORKTREE_BASE / "story-1"
        wt_path.mkdir(parents=True)
        db = await get_connection(db_ready)
        try:
            from ato.models.db import update_story_worktree_path

            await update_story_worktree_path(db, "story-1", str(wt_path))
        finally:
            await db.close()

        resolved = str(wt_path.resolve())
        branch = f"{BRANCH_PREFIX}story-1"
        list_output = f"worktree {resolved}\nHEAD abc\nbranch refs/heads/{branch}\n\n"
        call_args_list: list[tuple[Any, ...]] = []

        async def fake_exec(*args: Any, **kwargs: Any) -> AsyncMock:
            call_args_list.append(args)
            if args[1] == "worktree" and args[2] == "list":
                return _make_proc_mock(returncode=0, stdout=list_output)
            return _make_proc_mock(returncode=0)

        patch_target = "ato.worktree_mgr.asyncio.create_subprocess_exec"
        with patch(patch_target, side_effect=fake_exec):
            await mgr.cleanup("story-1")

        # 验证调用了 git worktree remove 和 git branch -d
        git_commands = [(args[1], args[2]) for args in call_args_list]
        assert ("worktree", "remove") in git_commands
        assert ("branch", "-d") in git_commands

        # 验证删除的分支名正确
        branch_calls = [a for a in call_args_list if a[1] == "branch"]
        assert branch_calls[0][3] == branch

        # 验证 DB worktree_path 已清空
        db = await get_connection(db_ready)
        try:
            story = await get_story(db, "story-1")
        finally:
            await db.close()
        assert story is not None
        assert story.worktree_path is None

    async def test_cleanup_uses_actual_branch_name(
        self,
        mgr: WorktreeManager,
        db_ready: Path,
        tmp_path: Path,
    ) -> None:
        """cleanup() 应删除 git 报告的实际分支，而非假设默认分支名。"""
        wt_path = tmp_path / WORKTREE_BASE / "story-1"
        wt_path.mkdir(parents=True)
        db = await get_connection(db_ready)
        try:
            from ato.models.db import update_story_worktree_path

            await update_story_worktree_path(db, "story-1", str(wt_path))
        finally:
            await db.close()

        # git worktree list 返回自定义分支名
        resolved = str(wt_path.resolve())
        list_output = f"worktree {resolved}\nHEAD abc\nbranch refs/heads/custom-branch\n\n"
        call_args_list: list[tuple[Any, ...]] = []

        async def fake_exec(*args: Any, **kwargs: Any) -> AsyncMock:
            call_args_list.append(args)
            if args[1] == "worktree" and args[2] == "list":
                return _make_proc_mock(returncode=0, stdout=list_output)
            return _make_proc_mock(returncode=0)

        patch_target = "ato.worktree_mgr.asyncio.create_subprocess_exec"
        with patch(patch_target, side_effect=fake_exec):
            await mgr.cleanup("story-1")

        # 验证删除的是 custom-branch 而非默认分支名
        branch_calls = [a for a in call_args_list if a[1] == "branch"]
        assert len(branch_calls) == 1
        assert branch_calls[0][3] == "custom-branch"


# ---------------------------------------------------------------------------
# cleanup() 幂等性
# ---------------------------------------------------------------------------


class TestCleanupIdempotent:
    async def test_cleanup_skips_when_no_worktree_path(
        self,
        mgr: WorktreeManager,
    ) -> None:
        """worktree_path 为 None 时跳过，不执行 git 命令。"""
        with patch("ato.worktree_mgr.asyncio.create_subprocess_exec") as mock_exec:
            await mgr.cleanup("story-1")

        mock_exec.assert_not_called()

    async def test_cleanup_handles_externally_removed_directory(
        self,
        mgr: WorktreeManager,
        db_ready: Path,
        tmp_path: Path,
    ) -> None:
        """DB 有 worktree_path 但目录已被外部移除时，应幂等处理并用默认分支名删除。"""
        wt_path = str(tmp_path / WORKTREE_BASE / "story-1")
        db = await get_connection(db_ready)
        try:
            from ato.models.db import update_story_worktree_path

            await update_story_worktree_path(db, "story-1", wt_path)
        finally:
            await db.close()

        # git worktree list 返回空（该 worktree 不在列表中）
        call_args_list: list[tuple[Any, ...]] = []

        async def fake_exec(*args: Any, **kwargs: Any) -> AsyncMock:
            call_args_list.append(args)
            if args[1] == "worktree" and args[2] == "list":
                return _make_proc_mock(returncode=0, stdout="")
            return _make_proc_mock(returncode=0)

        patch_target = "ato.worktree_mgr.asyncio.create_subprocess_exec"
        with patch(patch_target, side_effect=fake_exec):
            await mgr.cleanup("story-1")

        # 验证回退到默认分支名进行删除
        branch_calls = [a for a in call_args_list if a[1] == "branch"]
        assert len(branch_calls) == 1
        assert branch_calls[0][3] == f"{BRANCH_PREFIX}story-1"

        # DB worktree_path 应被清空
        db = await get_connection(db_ready)
        try:
            story = await get_story(db, "story-1")
        finally:
            await db.close()
        assert story is not None
        assert story.worktree_path is None


# ---------------------------------------------------------------------------
# cleanup() 部分失败
# ---------------------------------------------------------------------------


class TestCleanupPartialFailure:
    async def test_cleanup_warns_on_branch_delete_failure(
        self,
        mgr: WorktreeManager,
        db_ready: Path,
        tmp_path: Path,
    ) -> None:
        """git branch -d 失败时仅 warning 不抛异常，且保留元数据文件。"""
        wt_path = tmp_path / WORKTREE_BASE / "story-1"
        wt_path.mkdir(parents=True)
        branch = f"{BRANCH_PREFIX}story-1"

        # 模拟 create() 写入的元数据
        mgr._save_branch_meta("story-1", branch)

        db = await get_connection(db_ready)
        try:
            from ato.models.db import update_story_worktree_path

            await update_story_worktree_path(db, "story-1", str(wt_path))
        finally:
            await db.close()

        resolved = str(wt_path.resolve())
        list_output = f"worktree {resolved}\nHEAD abc\nbranch refs/heads/{branch}\n\n"

        async def fake_exec(*args: Any, **kwargs: Any) -> AsyncMock:
            if args[1] == "worktree" and args[2] == "list":
                return _make_proc_mock(returncode=0, stdout=list_output)
            if args[1] == "branch":
                return _make_proc_mock(
                    returncode=1,
                    stderr="error: branch not merged",
                )
            return _make_proc_mock(returncode=0)

        patch_target = "ato.worktree_mgr.asyncio.create_subprocess_exec"
        with patch(patch_target, side_effect=fake_exec):
            # 不应该抛异常
            await mgr.cleanup("story-1")

        # DB worktree_path 仍应清空
        db = await get_connection(db_ready)
        try:
            story = await get_story(db, "story-1")
        finally:
            await db.close()
        assert story is not None
        assert story.worktree_path is None

        # 分支元数据应保留（供后续 merge/cleanup 流程识别）
        meta_path = mgr._branch_meta_path("story-1")
        assert meta_path.exists()
        assert meta_path.read_text().strip() == branch

    async def test_cleanup_deletes_branch_meta_on_success(
        self,
        mgr: WorktreeManager,
        db_ready: Path,
        tmp_path: Path,
    ) -> None:
        """git branch -d 成功时应删除元数据文件。"""
        wt_path = tmp_path / WORKTREE_BASE / "story-1"
        wt_path.mkdir(parents=True)
        db = await get_connection(db_ready)
        try:
            from ato.models.db import update_story_worktree_path

            await update_story_worktree_path(db, "story-1", str(wt_path))
        finally:
            await db.close()

        # 写入元数据文件
        mgr._save_branch_meta("story-1", "some-branch")
        assert mgr._branch_meta_path("story-1").exists()

        resolved = str(wt_path.resolve())
        list_output = f"worktree {resolved}\nHEAD abc\nbranch refs/heads/some-branch\n\n"

        async def fake_exec(*args: Any, **kwargs: Any) -> AsyncMock:
            if args[1] == "worktree" and args[2] == "list":
                return _make_proc_mock(returncode=0, stdout=list_output)
            return _make_proc_mock(returncode=0)

        patch_target = "ato.worktree_mgr.asyncio.create_subprocess_exec"
        with patch(patch_target, side_effect=fake_exec):
            await mgr.cleanup("story-1")

        # 元数据文件应被删除
        assert not mgr._branch_meta_path("story-1").exists()


# ---------------------------------------------------------------------------
# create() 幂等路径 — 元数据修复
# ---------------------------------------------------------------------------


class TestCreateIdempotentMetaRepair:
    async def test_create_idempotent_repairs_missing_branch_meta(
        self,
        mgr: WorktreeManager,
        tmp_path: Path,
        db_ready: Path,
    ) -> None:
        """worktree 存在但 .branch 元数据丢失时，幂等 create() 应从 git 恢复。"""
        worktree_path = tmp_path / WORKTREE_BASE / "story-1"
        worktree_path.mkdir(parents=True)

        # 确认元数据文件不存在
        assert not mgr._branch_meta_path("story-1").exists()

        resolved = str(worktree_path.resolve())
        list_output = f"worktree {resolved}\nHEAD abc\nbranch refs/heads/custom-branch\n\n"
        proc = _make_proc_mock(returncode=0, stdout=list_output)

        patch_target = "ato.worktree_mgr.asyncio.create_subprocess_exec"
        with patch(patch_target, return_value=proc):
            await mgr.create("story-1")

        # 元数据文件应被恢复，记录实际分支名
        meta_path = mgr._branch_meta_path("story-1")
        assert meta_path.exists()
        assert meta_path.read_text().strip() == "custom-branch"

    async def test_create_idempotent_keeps_existing_branch_meta(
        self,
        mgr: WorktreeManager,
        tmp_path: Path,
        db_ready: Path,
    ) -> None:
        """worktree 存在且 .branch 元数据完整时，不应覆盖。"""
        worktree_path = tmp_path / WORKTREE_BASE / "story-1"
        worktree_path.mkdir(parents=True)

        # 预写入元数据
        mgr._save_branch_meta("story-1", "original-branch")

        resolved = str(worktree_path.resolve())
        list_output = f"worktree {resolved}\nHEAD abc\nbranch refs/heads/original-branch\n\n"
        proc = _make_proc_mock(returncode=0, stdout=list_output)

        patch_target = "ato.worktree_mgr.asyncio.create_subprocess_exec"
        with patch(patch_target, return_value=proc):
            await mgr.create("story-1")

        # 元数据文件应保持不变
        assert mgr._branch_meta_path("story-1").read_text().strip() == "original-branch"


# ---------------------------------------------------------------------------
# get_path() 和 exists()
# ---------------------------------------------------------------------------


class TestCreateIdempotentCorruptMeta:
    async def test_create_idempotent_repairs_empty_branch_meta(
        self,
        mgr: WorktreeManager,
        tmp_path: Path,
        db_ready: Path,
    ) -> None:
        """worktree 存在但 .branch 文件为空时，幂等 create() 应从 git 恢复。"""
        worktree_path = tmp_path / WORKTREE_BASE / "story-1"
        worktree_path.mkdir(parents=True)

        # 写入空内容的元数据文件
        meta_path = mgr._branch_meta_path("story-1")
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text("")

        resolved = str(worktree_path.resolve())
        list_output = f"worktree {resolved}\nHEAD abc\nbranch refs/heads/custom-branch\n\n"
        proc = _make_proc_mock(returncode=0, stdout=list_output)

        patch_target = "ato.worktree_mgr.asyncio.create_subprocess_exec"
        with patch(patch_target, return_value=proc):
            await mgr.create("story-1")

        # 元数据文件应被修复
        assert meta_path.read_text().strip() == "custom-branch"


class TestGetPathAndExists:
    async def test_get_path_returns_path_when_set(
        self,
        mgr: WorktreeManager,
        db_ready: Path,
        tmp_path: Path,
    ) -> None:
        wt_path = str(tmp_path / WORKTREE_BASE / "story-1")
        db = await get_connection(db_ready)
        try:
            from ato.models.db import update_story_worktree_path

            await update_story_worktree_path(db, "story-1", wt_path)
        finally:
            await db.close()

        result = await mgr.get_path("story-1")
        assert result == Path(wt_path)

    async def test_get_path_returns_none_when_not_set(
        self,
        mgr: WorktreeManager,
    ) -> None:
        result = await mgr.get_path("story-1")
        assert result is None

    async def test_get_path_returns_none_for_unknown_story(
        self,
        mgr: WorktreeManager,
    ) -> None:
        result = await mgr.get_path("nonexistent")
        assert result is None

    async def test_exists_true_when_path_and_dir_exist(
        self,
        mgr: WorktreeManager,
        db_ready: Path,
        tmp_path: Path,
    ) -> None:
        wt_path = tmp_path / WORKTREE_BASE / "story-1"
        wt_path.mkdir(parents=True)
        db = await get_connection(db_ready)
        try:
            from ato.models.db import update_story_worktree_path

            await update_story_worktree_path(db, "story-1", str(wt_path))
        finally:
            await db.close()

        assert await mgr.exists("story-1") is True

    async def test_exists_false_when_path_set_but_dir_missing(
        self,
        mgr: WorktreeManager,
        db_ready: Path,
        tmp_path: Path,
    ) -> None:
        wt_path = tmp_path / WORKTREE_BASE / "story-1"
        # 不创建目录
        db = await get_connection(db_ready)
        try:
            from ato.models.db import update_story_worktree_path

            await update_story_worktree_path(db, "story-1", str(wt_path))
        finally:
            await db.close()

        assert await mgr.exists("story-1") is False

    async def test_exists_false_when_no_worktree(
        self,
        mgr: WorktreeManager,
    ) -> None:
        assert await mgr.exists("story-1") is False


# ---------------------------------------------------------------------------
# _run_git() 超时
# ---------------------------------------------------------------------------


class TestRunGitTimeout:
    async def test_timeout_triggers_cleanup_and_raises(
        self,
        mgr: WorktreeManager,
    ) -> None:
        """超时时触发三阶段清理协议并抛出 WorktreeError。"""
        proc = AsyncMock(spec=asyncio.subprocess.Process)
        proc.returncode = None  # 进程未结束

        async def slow_communicate() -> tuple[bytes, bytes]:
            await asyncio.sleep(60)
            return (b"", b"")

        proc.communicate = slow_communicate
        proc.terminate = MagicMock()
        proc.kill = MagicMock()
        proc.wait = AsyncMock()

        patch_target = "ato.worktree_mgr.asyncio.create_subprocess_exec"
        with (
            patch("ato.worktree_mgr._GIT_TIMEOUT_SECONDS", 0.1),
            patch(patch_target, return_value=proc),
            pytest.raises(WorktreeError, match="timed out"),
        ):
            await mgr._run_git("status")


# ---------------------------------------------------------------------------
# 路径构建
# ---------------------------------------------------------------------------


class TestPathConstruction:
    async def test_worktree_path_format(
        self,
        mgr: WorktreeManager,
        tmp_path: Path,
    ) -> None:
        """验证 .worktrees/{story_id} 路径格式正确。"""
        proc = _make_proc_mock(returncode=0)

        with patch("ato.worktree_mgr.asyncio.create_subprocess_exec", return_value=proc):
            result = await mgr.create("story-1")

        assert result == tmp_path / ".worktrees" / "story-1"
        assert WORKTREE_BASE in str(result)

    async def test_default_branch_name(
        self,
        mgr: WorktreeManager,
        tmp_path: Path,
    ) -> None:
        """验证默认分支名 worktree-story-{story_id}。"""
        proc = _make_proc_mock(returncode=0)

        patch_target = "ato.worktree_mgr.asyncio.create_subprocess_exec"
        with patch(patch_target, return_value=proc) as mock_exec:
            await mgr.create("story-1")

        call_args = mock_exec.call_args[0]
        branch_arg_index = list(call_args).index("-b") + 1
        assert call_args[branch_arg_index] == "worktree-story-story-1"


# ---------------------------------------------------------------------------
# Rebase / Merge 操作测试 (Story 4.2)
# ---------------------------------------------------------------------------


class TestRebaseOntoMain:
    """rebase_onto_main() 测试。"""

    async def test_rebase_success(
        self,
        initialized_db_path: Path,
        tmp_path: Path,
    ) -> None:
        """rebase 成功返回 (True, "")。"""
        project_root = tmp_path / "project"
        project_root.mkdir()
        mgr = WorktreeManager(project_root=project_root, db_path=initialized_db_path)

        # Insert story with worktree path
        story = _make_story("story-1", worktree_path=str(tmp_path / "wt"))
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, story)
        finally:
            await db.close()

        # Mock git commands: fetch + rebase both succeed
        fetch_proc = _make_proc_mock(returncode=0)
        rebase_proc = _make_proc_mock(returncode=0)
        call_count = 0

        async def mock_exec(*args: Any, **kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return fetch_proc
            return rebase_proc

        patch_target = "ato.worktree_mgr.asyncio.create_subprocess_exec"
        with patch(patch_target, side_effect=mock_exec):
            success, stderr = await mgr.rebase_onto_main("story-1")

        assert success is True
        assert stderr == ""

    async def test_rebase_conflict(
        self,
        initialized_db_path: Path,
        tmp_path: Path,
    ) -> None:
        """冲突返回 (False, stderr)。"""
        project_root = tmp_path / "project"
        project_root.mkdir()
        mgr = WorktreeManager(project_root=project_root, db_path=initialized_db_path)

        story = _make_story("story-1", worktree_path=str(tmp_path / "wt"))
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, story)
        finally:
            await db.close()

        fetch_proc = _make_proc_mock(returncode=0)
        rebase_proc = _make_proc_mock(returncode=1, stderr="CONFLICT in file.py")
        call_count = 0

        async def mock_exec(*args: Any, **kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return fetch_proc
            return rebase_proc

        with patch("ato.worktree_mgr.asyncio.create_subprocess_exec", side_effect=mock_exec):
            success, stderr = await mgr.rebase_onto_main("story-1")

        assert success is False
        assert "CONFLICT" in stderr


class TestMergeToMain:
    """merge_to_main() 测试。"""

    async def test_ff_merge_success(
        self,
        initialized_db_path: Path,
        tmp_path: Path,
    ) -> None:
        """Fast-forward merge 成功。"""
        project_root = tmp_path / "project"
        project_root.mkdir()
        mgr = WorktreeManager(project_root=project_root, db_path=initialized_db_path)

        # Save branch metadata
        mgr._save_branch_meta("story-1", "worktree-story-story-1")

        checkout_proc = _make_proc_mock(returncode=0)
        merge_proc = _make_proc_mock(returncode=0)
        call_count = 0

        async def mock_exec(*args: Any, **kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return checkout_proc
            return merge_proc

        with patch("ato.worktree_mgr.asyncio.create_subprocess_exec", side_effect=mock_exec):
            success, stderr = await mgr.merge_to_main("story-1")

        assert success is True
        assert stderr == ""

    async def test_non_ff_merge_fails(
        self,
        initialized_db_path: Path,
        tmp_path: Path,
    ) -> None:
        """非 fast-forward merge 失败。"""
        project_root = tmp_path / "project"
        project_root.mkdir()
        mgr = WorktreeManager(project_root=project_root, db_path=initialized_db_path)

        mgr._save_branch_meta("story-1", "worktree-story-story-1")

        checkout_proc = _make_proc_mock(returncode=0)
        merge_proc = _make_proc_mock(returncode=1, stderr="Not possible to fast-forward")
        call_count = 0

        async def mock_exec(*args: Any, **kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return checkout_proc
            return merge_proc

        with patch("ato.worktree_mgr.asyncio.create_subprocess_exec", side_effect=mock_exec):
            success, stderr = await mgr.merge_to_main("story-1")

        assert success is False
        assert "Fast-forward merge failed" in stderr


class TestContinueRebase:
    """continue_rebase() 测试。"""

    async def test_continue_success(
        self,
        initialized_db_path: Path,
        tmp_path: Path,
    ) -> None:
        project_root = tmp_path / "project"
        project_root.mkdir()
        mgr = WorktreeManager(project_root=project_root, db_path=initialized_db_path)

        story = _make_story("story-1", worktree_path=str(tmp_path / "wt"))
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, story)
        finally:
            await db.close()

        proc = _make_proc_mock(returncode=0)
        with patch("ato.worktree_mgr.asyncio.create_subprocess_exec", return_value=proc):
            success, _stderr = await mgr.continue_rebase("story-1")

        assert success is True


class TestGetConflictFiles:
    """get_conflict_files() 测试。"""

    async def test_parses_conflict_files(
        self,
        initialized_db_path: Path,
        tmp_path: Path,
    ) -> None:
        project_root = tmp_path / "project"
        project_root.mkdir()
        mgr = WorktreeManager(project_root=project_root, db_path=initialized_db_path)

        story = _make_story("story-1", worktree_path=str(tmp_path / "wt"))
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(db, story)
        finally:
            await db.close()

        proc = _make_proc_mock(returncode=0, stdout="file1.py\nfile2.py\n")
        with patch("ato.worktree_mgr.asyncio.create_subprocess_exec", return_value=proc):
            files = await mgr.get_conflict_files("story-1")

        assert files == ["file1.py", "file2.py"]
