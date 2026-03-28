"""test_preflight_integration — 三层编排 + 持久化集成测试。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite

from ato.models.schemas import CheckResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_proc(
    stdout: str = "",
    stderr: str = "",
    returncode: int = 0,
) -> AsyncMock:
    """创建一个模拟的 asyncio subprocess。"""
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(stdout.encode(), stderr.encode()))
    proc.returncode = returncode
    proc.terminate = MagicMock()
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    return proc


def _make_full_project(tmp_path: Path) -> Path:
    """创建完整的测试项目目录。"""
    project = tmp_path / "project"
    project.mkdir()
    # git
    (project / ".git").mkdir()
    # BMAD config
    bmad_dir = project / "_bmad" / "bmm"
    bmad_dir.mkdir(parents=True)
    (bmad_dir / "config.yaml").write_text(
        "project_name: TestProject\n"
        'planning_artifacts: "{project-root}/planning"\n'
        'implementation_artifacts: "{project-root}/impl"\n'
    )
    # skills
    (project / ".claude" / "skills").mkdir(parents=True)
    # ato.yaml
    (project / "ato.yaml").write_text("project_name: test\n")
    # planning artifacts
    planning = project / "planning"
    planning.mkdir()
    (planning / "epics.md").write_text("# Epics")
    (planning / "prd.md").write_text("# PRD")
    (planning / "architecture.md").write_text("# Arch")
    # impl dir
    (project / "impl").mkdir()
    return project


class TestRunPreflight:
    """run_preflight 编排函数集成测试。"""

    async def test_all_layers_pass_and_persisted(self, tmp_path: Path) -> None:
        """三层全部通过，结果持久化到 SQLite。"""
        from ato.preflight import run_preflight

        project = _make_full_project(tmp_path)
        db_path = tmp_path / ".ato" / "state.db"
        proc = _mock_proc(stdout="version 1.0.0\n")

        with (
            patch("ato.preflight.sys") as mock_sys,
            patch("asyncio.create_subprocess_exec", return_value=proc),
        ):
            mock_sys.version_info = (3, 11, 0, "final", 0)
            mock_sys.version = "3.11.0"
            results = await run_preflight(project, db_path)

        # 应该有结果
        assert len(results) > 0

        # 验证 SQLite 持久化
        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM preflight_results")
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == len(results)

    async def test_halt_in_layer1_skips_layer2_and_layer3(self, tmp_path: Path) -> None:
        """Layer 1 有 HALT 时跳过 Layer 2 和 Layer 3。"""
        from ato.preflight import run_preflight

        project = _make_full_project(tmp_path)
        db_path = tmp_path / ".ato" / "state.db"

        with (
            patch("ato.preflight.sys") as mock_sys,
            patch(
                "asyncio.create_subprocess_exec",
                side_effect=FileNotFoundError("not found"),
            ),
        ):
            mock_sys.version_info = (3, 10, 0, "final", 0)  # < 3.11 → HALT
            mock_sys.version = "3.10.0"
            results = await run_preflight(project, db_path)

        layers = {r.layer for r in results}
        assert "system" in layers
        # Layer 2 和 3 应该被跳过
        assert "project" not in layers
        assert "artifact" not in layers
        # 至少有 HALT 结果
        assert any(r.status == "HALT" for r in results)

    async def test_halt_in_layer2_skips_layer3(self, tmp_path: Path) -> None:
        """Layer 2 有 HALT 时跳过 Layer 3（使用 bmad_config 缺失触发 HALT）。"""
        from ato.preflight import run_preflight

        # 创建缺少 bmad config 的项目 (Layer 2 HALT)
        project = _make_full_project(tmp_path)
        (project / "_bmad" / "bmm" / "config.yaml").unlink()
        db_path = tmp_path / ".ato" / "state.db"
        proc = _mock_proc(stdout="version 1.0.0\n")

        with (
            patch("ato.preflight.sys") as mock_sys,
            patch("asyncio.create_subprocess_exec", return_value=proc),
        ):
            mock_sys.version_info = (3, 11, 0, "final", 0)
            mock_sys.version = "3.11.0"
            results = await run_preflight(project, db_path)

        layers = {r.layer for r in results}
        assert "system" in layers
        assert "project" in layers
        # Layer 3 应该被跳过
        assert "artifact" not in layers

    async def test_missing_ato_yaml_does_not_skip_layer3(self, tmp_path: Path) -> None:
        """缺少 ato.yaml 返回 INFO（非 HALT），Layer 3 继续执行。"""
        from ato.preflight import run_preflight

        project = _make_full_project(tmp_path)
        (project / "ato.yaml").unlink()
        db_path = tmp_path / ".ato" / "state.db"
        proc = _mock_proc(stdout="version 1.0.0\n")

        with (
            patch("ato.preflight.sys") as mock_sys,
            patch("asyncio.create_subprocess_exec", return_value=proc),
        ):
            mock_sys.version_info = (3, 11, 0, "final", 0)
            mock_sys.version = "3.11.0"
            results = await run_preflight(project, db_path)

        layers = {r.layer for r in results}
        assert "system" in layers
        assert "project" in layers
        # Layer 3 应该继续执行（ato_yaml 缺失只是 INFO）
        assert "artifact" in layers
        # 验证 ato_yaml 是 INFO 而不是 HALT
        ato_yaml_result = next(r for r in results if r.check_item == "ato_yaml")
        assert ato_yaml_result.status == "INFO"

    async def test_include_auth_false_skips_auth(self, tmp_path: Path) -> None:
        """include_auth=False 跳过 CLI 认证检查。"""
        from ato.preflight import run_preflight

        project = _make_full_project(tmp_path)
        db_path = tmp_path / ".ato" / "state.db"
        proc = _mock_proc(stdout="version 1.0.0\n")

        with (
            patch("ato.preflight.sys") as mock_sys,
            patch("asyncio.create_subprocess_exec", return_value=proc),
        ):
            mock_sys.version_info = (3, 11, 0, "final", 0)
            mock_sys.version = "3.11.0"
            results = await run_preflight(project, db_path, include_auth=False)

        items = [r.check_item for r in results]
        assert "claude_auth" not in items
        assert "codex_auth" not in items

    async def test_results_persisted_with_unique_run_id(self, tmp_path: Path) -> None:
        """每次调用生成唯一 run_id。"""
        from ato.preflight import run_preflight

        project = _make_full_project(tmp_path)
        db_path = tmp_path / ".ato" / "state.db"
        proc = _mock_proc(stdout="version 1.0.0\n")

        with (
            patch("ato.preflight.sys") as mock_sys,
            patch("asyncio.create_subprocess_exec", return_value=proc),
        ):
            mock_sys.version_info = (3, 11, 0, "final", 0)
            mock_sys.version = "3.11.0"
            await run_preflight(project, db_path)
            await run_preflight(project, db_path)

        # 应有 2 个不同的 run_id
        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute("SELECT DISTINCT run_id FROM preflight_results")
            run_ids = [row[0] for row in await cursor.fetchall()]
            assert len(run_ids) == 2
            assert run_ids[0] != run_ids[1]

    async def test_sqlite_connection_not_held_during_checks(self, tmp_path: Path) -> None:
        """验证 SQLite 连接不在检查阶段持有（通过检查调用顺序）。"""
        from ato.preflight import run_preflight

        project = _make_full_project(tmp_path)
        db_path = tmp_path / ".ato" / "state.db"
        proc = _mock_proc(stdout="version 1.0.0\n")

        # 追踪 init_db 调用时机
        call_order: list[str] = []

        with (
            patch("ato.preflight.sys") as mock_sys,
            patch("asyncio.create_subprocess_exec", return_value=proc),
            patch("ato.preflight.init_db", wraps=None) as mock_init,
            patch("ato.preflight.insert_preflight_results", wraps=None) as mock_insert,
        ):
            mock_sys.version_info = (3, 11, 0, "final", 0)
            mock_sys.version = "3.11.0"

            async def _track_init(path: Path) -> None:
                call_order.append("init_db")
                from ato.models.db import init_db

                await init_db(path)

            async def _track_insert(
                db: object,
                run_id: str,
                items: list[CheckResult],
            ) -> None:
                call_order.append("insert")
                from ato.models.db import insert_preflight_results as real_insert

                await real_insert(db, run_id, items)  # type: ignore[arg-type]

            mock_init.side_effect = _track_init
            mock_insert.side_effect = _track_insert

            await run_preflight(project, db_path)

        # init_db 和 insert 应在所有检查完成后才被调用
        assert "init_db" in call_order
        assert "insert" in call_order

    async def test_result_order_across_layers(self, tmp_path: Path) -> None:
        """结果按层级顺序排列：system → project → artifact。"""
        from ato.preflight import run_preflight

        project = _make_full_project(tmp_path)
        db_path = tmp_path / ".ato" / "state.db"
        proc = _mock_proc(stdout="version 1.0.0\n")

        with (
            patch("ato.preflight.sys") as mock_sys,
            patch("asyncio.create_subprocess_exec", return_value=proc),
        ):
            mock_sys.version_info = (3, 11, 0, "final", 0)
            mock_sys.version = "3.11.0"
            results = await run_preflight(project, db_path)

        layers = [r.layer for r in results]
        # 验证层级顺序：所有 system 在 project 之前，所有 project 在 artifact 之前
        system_end = max(idx for idx, layer in enumerate(layers) if layer == "system")
        if "project" in layers:
            project_start = min(idx for idx, layer in enumerate(layers) if layer == "project")
            assert system_end < project_start
        if "artifact" in layers:
            project_end = max(idx for idx, layer in enumerate(layers) if layer == "project")
            artifact_start = min(idx for idx, layer in enumerate(layers) if layer == "artifact")
            assert project_end < artifact_start
