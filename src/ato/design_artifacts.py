"""design_artifacts — 设计阶段工件路径推导 (Story 9.1a)。

所有 designing 阶段相关的路径命名约定集中于此，
后续 Story 9.1b / 9.1c / 9.1d 以及 prompt / gate 均复用该 helper。
"""

from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

ARTIFACTS_REL = "_bmad-output/implementation-artifacts"
TEMPLATE_PEN_REL = "schemas/prototype-template.pen"

DESIGN_ARTIFACT_NAMES: frozenset[str] = frozenset(
    {
        "ux-spec.md",
        "prototype.pen",
        "prototype.snapshot.json",
        "prototype.save-report.json",
        "exports",
    }
)


# ---------------------------------------------------------------------------
# 路径推导 helper
# ---------------------------------------------------------------------------


def derive_design_artifact_paths(
    story_id: str,
    project_root: Path,
) -> dict[str, Path]:
    """推导 story 的全部设计工件绝对路径。

    Args:
        story_id: Story 标识符（如 ``2a-1-story-state-machine-progression``）。
        project_root: 项目根目录绝对路径。

    Returns:
        包含以下键的字典：
        - ``ux_dir``: UX 设计目录
        - ``ux_spec``: ``ux-spec.md``
        - ``prototype_pen``: ``prototype.pen``
        - ``snapshot_json``: ``prototype.snapshot.json``
        - ``save_report_json``: ``prototype.save-report.json``
        - ``exports_dir``: ``exports/``
        - ``template_pen``: 仓库内 .pen 模板路径
    """
    artifacts_dir = project_root / ARTIFACTS_REL
    ux_dir = artifacts_dir / f"{story_id}-ux"
    return {
        "ux_dir": ux_dir,
        "ux_spec": ux_dir / "ux-spec.md",
        "prototype_pen": ux_dir / "prototype.pen",
        "snapshot_json": ux_dir / "prototype.snapshot.json",
        "save_report_json": ux_dir / "prototype.save-report.json",
        "exports_dir": ux_dir / "exports",
        "template_pen": project_root / TEMPLATE_PEN_REL,
    }


def derive_design_artifact_paths_relative(story_id: str) -> dict[str, str]:
    """推导 story 的全部设计工件 project-root 相对路径（字符串）。

    用于 prompt 模板等不需要绝对路径的场景。
    """
    ux_dir = f"{ARTIFACTS_REL}/{story_id}-ux"
    return {
        "ux_dir": ux_dir,
        "ux_spec": f"{ux_dir}/ux-spec.md",
        "prototype_pen": f"{ux_dir}/prototype.pen",
        "snapshot_json": f"{ux_dir}/prototype.snapshot.json",
        "save_report_json": f"{ux_dir}/prototype.save-report.json",
        "exports_dir": f"{ux_dir}/exports",
        "template_pen": TEMPLATE_PEN_REL,
    }
