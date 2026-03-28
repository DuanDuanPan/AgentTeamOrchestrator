"""design_artifacts 模块单元测试 (Story 9.1a AC#3–5, Story 9.1b AC#1–5)。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


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


# ==========================================================================
# Story 9.1b — 强制落盘与保存校验
# ==========================================================================


class TestReadPenFile:
    """验证 .pen 文件读取 (9.1b AC#1)。"""

    def test_reads_valid_pen_file(self, tmp_path: Path) -> None:
        from ato.design_artifacts import read_pen_file

        pen = tmp_path / "test.pen"
        pen.write_text(
            json.dumps({"version": "1.0.0", "children": [{"id": "n1"}], "variables": {}})
        )
        data = read_pen_file(pen)
        assert data["version"] == "1.0.0"
        assert len(data["children"]) == 1

    def test_raises_on_missing_file(self, tmp_path: Path) -> None:
        from ato.design_artifacts import read_pen_file

        with pytest.raises(FileNotFoundError):
            read_pen_file(tmp_path / "missing.pen")

    def test_raises_on_invalid_json(self, tmp_path: Path) -> None:
        from ato.design_artifacts import read_pen_file

        pen = tmp_path / "bad.pen"
        pen.write_text("not valid json {{{")
        with pytest.raises(json.JSONDecodeError):
            read_pen_file(pen)


class TestForcePersistPen:
    """验证 .pen 结构化回写 (9.1b AC#1, AC#2)。"""

    def _make_pen(self, tmp_path: Path, data: dict[str, object]) -> Path:
        pen = tmp_path / "prototype.pen"
        pen.write_text(json.dumps(data))
        return pen

    def test_replaces_children_preserves_top_level(self, tmp_path: Path) -> None:
        """保留 version + variables，替换 children。"""
        from ato.design_artifacts import force_persist_pen

        pen = self._make_pen(
            tmp_path, {"version": "1.0.0", "children": [], "variables": {"color": "#fff"}}
        )
        new_children = [{"id": "node-1", "type": "frame"}]
        result = force_persist_pen(pen, new_children)

        assert result.success is True
        assert result.children_count == 1
        data = json.loads(pen.read_text())
        assert data["children"] == new_children
        assert data["version"] == "1.0.0"
        assert data["variables"] == {"color": "#fff"}

    def test_preserves_unknown_top_level_fields(self, tmp_path: Path) -> None:
        """未来扩展字段不被丢弃 (AC#2)。"""
        from ato.design_artifacts import force_persist_pen

        pen = self._make_pen(
            tmp_path,
            {
                "version": "2.0",
                "children": [],
                "variables": {},
                "metadata": {"author": "test"},
                "future_field": 42,
            },
        )
        result = force_persist_pen(pen, [{"id": "n1"}])

        assert result.success is True
        assert "metadata" in result.preserved_keys
        assert "future_field" in result.preserved_keys
        data = json.loads(pen.read_text())
        assert data["metadata"] == {"author": "test"}
        assert data["future_field"] == 42

    def test_result_is_valid_json(self, tmp_path: Path) -> None:
        """写回后的 .pen 仍可被 json.load 成功解析 (AC#2)。"""
        from ato.design_artifacts import force_persist_pen

        pen = self._make_pen(tmp_path, {"version": "1.0.0", "children": [], "variables": {}})
        force_persist_pen(pen, [{"id": "n1", "text": "你好"}])

        with open(pen) as f:
            data = json.load(f)
        assert isinstance(data, dict)
        assert data["children"][0]["text"] == "你好"

    def test_no_temp_files_remain_on_success(self, tmp_path: Path) -> None:
        """成功后不留下 .tmp 文件。"""
        from ato.design_artifacts import force_persist_pen

        pen = self._make_pen(tmp_path, {"version": "1.0.0", "children": [], "variables": {}})
        force_persist_pen(pen, [{"id": "n1"}])

        tmp_files = list(tmp_path.glob("*.tmp"))
        assert len(tmp_files) == 0

    def test_returns_error_on_missing_pen(self, tmp_path: Path) -> None:
        """pen 文件不存在时返回 error result (AC#5)。"""
        from ato.design_artifacts import force_persist_pen

        result = force_persist_pen(tmp_path / "missing.pen", [])
        assert result.success is False
        assert result.error is not None
        assert "Failed to read" in result.error

    def test_returns_error_on_invalid_json_pen(self, tmp_path: Path) -> None:
        """pen 文件 JSON 无效时返回 error result (AC#5)。"""
        from ato.design_artifacts import force_persist_pen

        pen = tmp_path / "bad.pen"
        pen.write_text("not json")
        result = force_persist_pen(pen, [])
        assert result.success is False
        assert result.error is not None

    def test_original_preserved_on_write_failure(self, tmp_path: Path) -> None:
        """原子写入失败时原文件不被损坏 (AC#5)。"""
        from unittest.mock import patch

        from ato.design_artifacts import force_persist_pen

        original = {"version": "1.0.0", "children": [{"id": "old"}], "variables": {}}
        pen = self._make_pen(tmp_path, original)

        with patch("ato.design_artifacts.os.replace", side_effect=OSError("disk full")):
            result = force_persist_pen(pen, [{"id": "new"}])

        assert result.success is False
        assert "Atomic write failed" in (result.error or "")
        # Original file should be unchanged
        data = json.loads(pen.read_text())
        assert data["children"] == [{"id": "old"}]

    def test_children_key_not_in_preserved_keys(self, tmp_path: Path) -> None:
        """preserved_keys 不包含 children。"""
        from ato.design_artifacts import force_persist_pen

        pen = self._make_pen(tmp_path, {"version": "1.0.0", "children": [], "variables": {}})
        result = force_persist_pen(pen, [])
        assert "children" not in result.preserved_keys
        assert "version" in result.preserved_keys
        assert "variables" in result.preserved_keys


class TestWriteDesignSnapshot:
    """验证快照写入 (9.1b AC#3)。"""

    def test_writes_valid_json(self, tmp_path: Path) -> None:
        from ato.design_artifacts import write_design_snapshot

        snap_path = tmp_path / "ux" / "prototype.snapshot.json"
        tree = {"version": "1.0.0", "children": [{"id": "n1"}]}
        result = write_design_snapshot(snap_path, tree)

        assert result == snap_path
        assert snap_path.is_file()
        data = json.loads(snap_path.read_text())
        assert data == tree

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        from ato.design_artifacts import write_design_snapshot

        snap_path = tmp_path / "deep" / "nested" / "snapshot.json"
        write_design_snapshot(snap_path, {"children": []})
        assert snap_path.is_file()


class TestWriteSaveReport:
    """验证保存报告写入 (9.1b AC#3)。"""

    def test_writes_all_required_fields(self, tmp_path: Path) -> None:
        from ato.design_artifacts import SAVE_REPORT_REQUIRED_KEYS, write_save_report

        report_path = tmp_path / "prototype.save-report.json"
        write_save_report(
            report_path,
            story_id="test-story",
            pen_file="prototype.pen",
            snapshot_file="prototype.snapshot.json",
            children_count=5,
            json_parse_verified=True,
            reopen_verified=True,
            exported_png_count=2,
        )

        data = json.loads(report_path.read_text())
        assert SAVE_REPORT_REQUIRED_KEYS.issubset(data.keys())
        assert data["story_id"] == "test-story"
        assert data["children_count"] == 5
        assert data["json_parse_verified"] is True
        assert data["reopen_verified"] is True
        assert data["exported_png_count"] == 2

    def test_saved_at_is_iso_timestamp(self, tmp_path: Path) -> None:
        from ato.design_artifacts import write_save_report

        report_path = tmp_path / "report.json"
        write_save_report(
            report_path,
            story_id="s1",
            pen_file="p.pen",
            snapshot_file="s.json",
            children_count=0,
            json_parse_verified=True,
            reopen_verified=False,
        )

        data = json.loads(report_path.read_text())
        # Should be ISO format parseable
        from datetime import datetime

        datetime.fromisoformat(data["saved_at"])

    def test_exported_png_count_defaults_to_zero(self, tmp_path: Path) -> None:
        from ato.design_artifacts import write_save_report

        report_path = tmp_path / "report.json"
        write_save_report(
            report_path,
            story_id="s1",
            pen_file="p.pen",
            snapshot_file="s.json",
            children_count=0,
            json_parse_verified=True,
            reopen_verified=True,
        )

        data = json.loads(report_path.read_text())
        assert data["exported_png_count"] == 0


class TestVerifyPenIntegrity:
    """验证保存后校验 (9.1b AC#4)。"""

    def test_valid_pen_passes(self, tmp_path: Path) -> None:
        from ato.design_artifacts import verify_pen_integrity

        pen = tmp_path / "ok.pen"
        pen.write_text(
            json.dumps({"version": "1.0.0", "children": [{"id": "n1"}], "variables": {}})
        )
        result = verify_pen_integrity(pen)
        assert result.json_parse_ok is True
        assert result.required_keys_present is True
        assert result.children_count == 1
        assert result.error is None

    def test_missing_file_fails(self, tmp_path: Path) -> None:
        from ato.design_artifacts import verify_pen_integrity

        result = verify_pen_integrity(tmp_path / "missing.pen")
        assert result.json_parse_ok is False
        assert result.required_keys_present is False

    def test_invalid_json_fails(self, tmp_path: Path) -> None:
        from ato.design_artifacts import verify_pen_integrity

        pen = tmp_path / "bad.pen"
        pen.write_text("{broken")
        result = verify_pen_integrity(pen)
        assert result.json_parse_ok is False
        assert result.error is not None

    def test_missing_required_keys_fails(self, tmp_path: Path) -> None:
        from ato.design_artifacts import verify_pen_integrity

        pen = tmp_path / "partial.pen"
        pen.write_text(json.dumps({"version": "1.0.0"}))
        result = verify_pen_integrity(pen)
        assert result.json_parse_ok is True
        assert result.required_keys_present is False
        assert result.error is not None
        assert "children" in result.error

    def test_empty_children_is_valid(self, tmp_path: Path) -> None:
        from ato.design_artifacts import verify_pen_integrity

        pen = tmp_path / "empty.pen"
        pen.write_text(json.dumps({"version": "1.0.0", "children": [], "variables": {}}))
        result = verify_pen_integrity(pen)
        assert result.json_parse_ok is True
        assert result.required_keys_present is True
        assert result.children_count == 0


class TestVerifySnapshot:
    """验证 snapshot 结构校验 (9.1b AC#3, AC#4)。"""

    def test_valid_snapshot_with_children_list_passes(self, tmp_path: Path) -> None:
        from ato.design_artifacts import verify_snapshot

        snapshot = tmp_path / "prototype.snapshot.json"
        snapshot.write_text(json.dumps({"version": "1.0.0", "children": [{"id": "n1"}]}))
        assert verify_snapshot(snapshot) is True

    @pytest.mark.parametrize(
        ("payload"),
        [
            [],
            True,
            {"foo": 1},
            {"children": {}},
            {"children": ["not-a-node"]},
        ],
    )
    def test_invalid_snapshot_shapes_fail(self, tmp_path: Path, payload: object) -> None:
        from ato.design_artifacts import verify_snapshot

        snapshot = tmp_path / "prototype.snapshot.json"
        snapshot.write_text(json.dumps(payload))
        assert verify_snapshot(snapshot) is False


class TestVerifySaveReport:
    """验证 save-report 校验 (9.1b AC#4)。"""

    def test_valid_report_passes(self, tmp_path: Path) -> None:
        from ato.design_artifacts import verify_save_report, write_save_report

        report_path = tmp_path / "report.json"
        write_save_report(
            report_path,
            story_id="s1",
            pen_file="p.pen",
            snapshot_file="s.json",
            children_count=3,
            json_parse_verified=True,
            reopen_verified=True,
        )
        assert verify_save_report(report_path) is True

    def test_missing_file_fails(self, tmp_path: Path) -> None:
        from ato.design_artifacts import verify_save_report

        assert verify_save_report(tmp_path / "missing.json") is False

    def test_incomplete_report_fails(self, tmp_path: Path) -> None:
        from ato.design_artifacts import verify_save_report

        report_path = tmp_path / "partial.json"
        report_path.write_text(json.dumps({"story_id": "s1"}))
        assert verify_save_report(report_path) is False

    def test_json_parse_verified_false_fails(self, tmp_path: Path) -> None:
        """json_parse_verified=false 的报告不应视为有效证据链。"""
        from ato.design_artifacts import verify_save_report, write_save_report

        report_path = tmp_path / "report.json"
        write_save_report(
            report_path,
            story_id="s1",
            pen_file="p.pen",
            snapshot_file="s.json",
            children_count=3,
            json_parse_verified=False,
            reopen_verified=True,
        )
        assert verify_save_report(report_path) is False

    def test_reopen_verified_false_fails(self, tmp_path: Path) -> None:
        """reopen_verified=false 的报告不应视为有效证据链。"""
        from ato.design_artifacts import verify_save_report, write_save_report

        report_path = tmp_path / "report.json"
        write_save_report(
            report_path,
            story_id="s1",
            pen_file="p.pen",
            snapshot_file="s.json",
            children_count=3,
            json_parse_verified=True,
            reopen_verified=False,
        )
        assert verify_save_report(report_path) is False

    def test_both_verified_false_fails(self, tmp_path: Path) -> None:
        """两项验证均失败的报告不应通过。"""
        from ato.design_artifacts import verify_save_report, write_save_report

        report_path = tmp_path / "report.json"
        write_save_report(
            report_path,
            story_id="s1",
            pen_file="p.pen",
            snapshot_file="s.json",
            children_count=0,
            json_parse_verified=False,
            reopen_verified=False,
        )
        assert verify_save_report(report_path) is False


class TestForcePersistAndVerifyIntegration:
    """端到端集成：强制落盘 → 校验 (9.1b AC#1–#5)。"""

    def test_persist_then_verify_roundtrip(self, tmp_path: Path) -> None:
        """完整链路：写入 → 校验通过。"""
        from ato.design_artifacts import (
            force_persist_pen,
            verify_pen_integrity,
            verify_save_report,
            write_design_snapshot,
            write_save_report,
        )

        pen = tmp_path / "prototype.pen"
        pen.write_text(json.dumps({"version": "1.0.0", "children": [], "variables": {}}))

        memory_children = [{"id": "frame-1", "type": "frame", "children": [{"id": "text-1"}]}]

        # Step 1: Force persist
        persist_result = force_persist_pen(pen, memory_children)
        assert persist_result.success is True

        # Step 2: Verify pen integrity
        verify_result = verify_pen_integrity(pen)
        assert verify_result.json_parse_ok is True
        assert verify_result.required_keys_present is True
        assert verify_result.children_count == 1

        # Step 3: Write snapshot
        snap = tmp_path / "prototype.snapshot.json"
        write_design_snapshot(snap, {"version": "1.0.0", "children": memory_children})
        assert snap.is_file()

        # Step 4: Write save report
        report = tmp_path / "prototype.save-report.json"
        write_save_report(
            report,
            story_id="test-story",
            pen_file=str(pen),
            snapshot_file=str(snap),
            children_count=persist_result.children_count,
            json_parse_verified=verify_result.json_parse_ok,
            reopen_verified=True,
            exported_png_count=0,
        )
        assert verify_save_report(report) is True
