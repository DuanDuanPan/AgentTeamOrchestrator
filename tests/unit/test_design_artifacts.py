"""design_artifacts 模块单元测试 (Story 9.1a AC#3, AC#4, AC#5)。"""

from __future__ import annotations

from pathlib import Path


class TestDeriveDesignArtifactPaths:
    """验证 derive_design_artifact_paths helper 返回正确路径。"""

    def test_returns_all_required_keys(self) -> None:
        """helper 返回值覆盖所有核心工件路径。"""
        from ato.design_artifacts import derive_design_artifact_paths

        paths = derive_design_artifact_paths("s1", Path("/project"))
        required_keys = {
            "ux_dir",
            "ux_spec",
            "prototype_pen",
            "snapshot_json",
            "save_report_json",
            "exports_dir",
            "template_pen",
        }
        assert required_keys.issubset(paths.keys())

    def test_ux_dir_path(self) -> None:
        """UX 目录路径正确。"""
        from ato.design_artifacts import derive_design_artifact_paths

        paths = derive_design_artifact_paths("my-story", Path("/proj"))
        expected = Path("/proj/_bmad-output/implementation-artifacts/my-story-ux")
        assert paths["ux_dir"] == expected

    def test_ux_spec_path(self) -> None:
        """ux-spec.md 路径正确。"""
        from ato.design_artifacts import derive_design_artifact_paths

        paths = derive_design_artifact_paths("my-story", Path("/proj"))
        expected = Path("/proj/_bmad-output/implementation-artifacts/my-story-ux/ux-spec.md")
        assert paths["ux_spec"] == expected

    def test_prototype_pen_path(self) -> None:
        """prototype.pen 路径正确。"""
        from ato.design_artifacts import derive_design_artifact_paths

        paths = derive_design_artifact_paths("my-story", Path("/proj"))
        expected = Path("/proj/_bmad-output/implementation-artifacts/my-story-ux/prototype.pen")
        assert paths["prototype_pen"] == expected

    def test_snapshot_json_path(self) -> None:
        """prototype.snapshot.json 路径正确。"""
        from ato.design_artifacts import derive_design_artifact_paths

        paths = derive_design_artifact_paths("my-story", Path("/proj"))
        assert paths["snapshot_json"] == Path(
            "/proj/_bmad-output/implementation-artifacts/my-story-ux/prototype.snapshot.json"
        )

    def test_save_report_json_path(self) -> None:
        """prototype.save-report.json 路径正确。"""
        from ato.design_artifacts import derive_design_artifact_paths

        paths = derive_design_artifact_paths("my-story", Path("/proj"))
        assert paths["save_report_json"] == Path(
            "/proj/_bmad-output/implementation-artifacts/my-story-ux/prototype.save-report.json"
        )

    def test_exports_dir_path(self) -> None:
        """exports/ 目录路径正确。"""
        from ato.design_artifacts import derive_design_artifact_paths

        paths = derive_design_artifact_paths("my-story", Path("/proj"))
        expected = Path("/proj/_bmad-output/implementation-artifacts/my-story-ux/exports")
        assert paths["exports_dir"] == expected

    def test_template_pen_path(self) -> None:
        """模板路径指向 schemas/prototype-template.pen。"""
        from ato.design_artifacts import derive_design_artifact_paths

        paths = derive_design_artifact_paths("my-story", Path("/proj"))
        expected = Path("/proj/schemas/prototype-template.pen")
        assert paths["template_pen"] == expected

    def test_relative_paths(self) -> None:
        """derive_design_artifact_paths_relative 返回相对路径。"""
        from ato.design_artifacts import (
            derive_design_artifact_paths_relative,
        )

        paths = derive_design_artifact_paths_relative("s1")
        assert paths["ux_dir"] == ("_bmad-output/implementation-artifacts/s1-ux")
        assert paths["prototype_pen"] == (
            "_bmad-output/implementation-artifacts/s1-ux/prototype.pen"
        )
        assert paths["template_pen"] == "schemas/prototype-template.pen"


class TestDesignArtifactNames:
    """验证核心工件名称常量。"""

    def test_artifact_names_constant(self) -> None:
        """DESIGN_ARTIFACT_NAMES 包含 5 个核心工件。"""
        from ato.design_artifacts import DESIGN_ARTIFACT_NAMES

        assert "ux-spec.md" in DESIGN_ARTIFACT_NAMES
        assert "prototype.pen" in DESIGN_ARTIFACT_NAMES
        assert "prototype.snapshot.json" in DESIGN_ARTIFACT_NAMES
        assert "prototype.save-report.json" in DESIGN_ARTIFACT_NAMES
        assert "exports" in DESIGN_ARTIFACT_NAMES


class TestGatePathAlignment:
    """验证 design gate 的路径约定与 helper 一致 (AC#5)。"""

    def test_gate_ux_dir_convention_matches_helper(self) -> None:
        """gate 的 {story_id}-ux 约定与 helper 一致。"""
        from ato.design_artifacts import derive_design_artifact_paths

        paths = derive_design_artifact_paths("s1", Path("/proj"))
        expected = Path("/proj/_bmad-output/implementation-artifacts/s1-ux")
        assert paths["ux_dir"] == expected

    def test_gate_accepted_extensions_covered(self) -> None:
        """gate 接受的扩展名与核心工件合同一致。"""
        from ato.design_artifacts import DESIGN_ARTIFACT_NAMES

        artifact_extensions = set()
        for name in DESIGN_ARTIFACT_NAMES:
            if "." in name:
                artifact_extensions.add("." + name.rsplit(".", 1)[1])
        # .md / .pen / .json 必须在核心工件中有对应
        assert ".md" in artifact_extensions
        assert ".pen" in artifact_extensions
        assert ".json" in artifact_extensions

    def test_prompt_paths_use_helper_relative(self) -> None:
        """prompt 格式化后的路径与 helper 输出一致。"""
        from ato.design_artifacts import (
            derive_design_artifact_paths_relative,
        )
        from ato.recovery import (
            _STRUCTURED_JOB_PROMPTS,
            _format_structured_job_prompt,
        )

        formatted = _format_structured_job_prompt(_STRUCTURED_JOB_PROMPTS["designing"], "test-s1")
        rel = derive_design_artifact_paths_relative("test-s1")
        assert rel["ux_spec"] in formatted
        assert rel["prototype_pen"] in formatted
        assert rel["snapshot_json"] in formatted
        assert rel["save_report_json"] in formatted
        assert rel["template_pen"] in formatted
        assert rel["exports_dir"] in formatted
