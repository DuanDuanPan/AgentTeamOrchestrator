"""design_artifacts — 设计阶段工件路径推导与强制落盘 (Story 9.1a, 9.1b)。

所有 designing 阶段相关的路径命名约定集中于此，
同时提供结构化回写、快照/报告生成和落盘校验 helper。
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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

_PEN_REQUIRED_KEYS: frozenset[str] = frozenset({"version", "children", "variables"})
""".pen 文件必须包含的顶层键。"""


# ---------------------------------------------------------------------------
# 结果数据类 (Story 9.1b)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PenPersistResult:
    """强制落盘结果。"""

    success: bool
    pen_path: str
    children_count: int
    preserved_keys: tuple[str, ...]
    error: str | None = None


@dataclass(frozen=True)
class PenVerifyResult:
    """保存后校验结果。"""

    json_parse_ok: bool
    required_keys_present: bool
    children_count: int
    error: str | None = None


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


# ---------------------------------------------------------------------------
# .pen 文件读写 (Story 9.1b AC#1, AC#2)
# ---------------------------------------------------------------------------


def read_pen_file(pen_path: Path) -> dict[str, Any]:
    """从磁盘读取 .pen 文件并解析为 dict。

    Raises:
        FileNotFoundError: 文件不存在。
        json.JSONDecodeError: JSON 格式无效。
    """
    with open(pen_path, encoding="utf-8") as f:
        data: dict[str, Any] = json.load(f)
    return data


def _atomic_write_json(target: Path, data: Any) -> None:
    """通过临时文件 + os.replace 实现原子写入 JSON。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, str(target))
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise


def force_persist_pen(
    pen_path: Path,
    memory_children: list[dict[str, Any]],
) -> PenPersistResult:
    """结构化强制落盘：保留顶层字段，替换 children。

    1. 读取磁盘上的 .pen 文件
    2. 保留所有顶层字段（version / variables / 未知扩展字段），替换 ``children``
    3. 使用临时文件 + ``os.replace`` 原子写入

    Args:
        pen_path: 磁盘上 .pen 文件的路径。
        memory_children: Pencil MCP ``batch_get`` 返回的内存态节点树。

    Returns:
        PenPersistResult 包含成功/失败状态、写入的 children 数量、保留的顶层键。
    """
    try:
        disk_data = read_pen_file(pen_path)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        return PenPersistResult(
            success=False,
            pen_path=str(pen_path),
            children_count=0,
            preserved_keys=(),
            error=f"Failed to read pen file: {exc}",
        )

    preserved_keys = tuple(k for k in disk_data if k != "children")

    merged: dict[str, Any] = {k: v for k, v in disk_data.items() if k != "children"}
    merged["children"] = memory_children

    try:
        _atomic_write_json(pen_path, merged)
    except Exception as exc:
        return PenPersistResult(
            success=False,
            pen_path=str(pen_path),
            children_count=0,
            preserved_keys=preserved_keys,
            error=f"Atomic write failed: {exc}",
        )

    return PenPersistResult(
        success=True,
        pen_path=str(pen_path),
        children_count=len(memory_children),
        preserved_keys=preserved_keys,
    )


# ---------------------------------------------------------------------------
# 快照与保存报告 (Story 9.1b AC#3)
# ---------------------------------------------------------------------------


def write_design_snapshot(
    snapshot_path: Path,
    memory_tree: dict[str, Any],
) -> Path:
    """写入全量结构化快照。

    Args:
        snapshot_path: 快照文件路径。
        memory_tree: 完整内存态节点树。

    Returns:
        写入后的文件路径。
    """
    _atomic_write_json(snapshot_path, memory_tree)
    return snapshot_path


def write_save_report(
    report_path: Path,
    *,
    story_id: str,
    pen_file: str,
    snapshot_file: str,
    children_count: int,
    json_parse_verified: bool,
    reopen_verified: bool,
    exported_png_count: int = 0,
) -> Path:
    """写入保存证明报告。

    字段结构遵循 AC#3 规格要求，后续 Story 9.1c gate 依赖此报告。

    Returns:
        写入后的文件路径。
    """
    report: dict[str, Any] = {
        "story_id": story_id,
        "saved_at": datetime.now(UTC).isoformat(),
        "pen_file": pen_file,
        "snapshot_file": snapshot_file,
        "children_count": children_count,
        "json_parse_verified": json_parse_verified,
        "reopen_verified": reopen_verified,
        "exported_png_count": exported_png_count,
    }
    _atomic_write_json(report_path, report)
    return report_path


# ---------------------------------------------------------------------------
# 保存后校验 (Story 9.1b AC#4)
# ---------------------------------------------------------------------------

SAVE_REPORT_REQUIRED_KEYS: frozenset[str] = frozenset(
    {
        "story_id",
        "saved_at",
        "pen_file",
        "snapshot_file",
        "children_count",
        "json_parse_verified",
        "reopen_verified",
        "exported_png_count",
    }
)
"""save-report.json 必须包含的键。"""


def verify_pen_integrity(pen_path: Path) -> PenVerifyResult:
    """保存后校验：JSON 解析 + 必需顶层字段检查。

    Orchestrator 在 agent 完成 designing 任务后调用此函数验证
    .pen 文件确实被正确保存到磁盘。

    Returns:
        PenVerifyResult 包含各项校验结果。
    """
    try:
        data = read_pen_file(pen_path)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        return PenVerifyResult(
            json_parse_ok=False,
            required_keys_present=False,
            children_count=0,
            error=str(exc),
        )

    required_present = _PEN_REQUIRED_KEYS.issubset(data.keys())
    children_count = len(data.get("children", []))

    error = None
    if not required_present:
        missing = _PEN_REQUIRED_KEYS - set(data.keys())
        error = f"Missing required keys: {sorted(missing)}"

    return PenVerifyResult(
        json_parse_ok=True,
        required_keys_present=required_present,
        children_count=children_count,
        error=error,
    )


def verify_snapshot(snapshot_path: Path) -> bool:
    """验证 prototype.snapshot.json 为全量结构化快照。

    Returns:
        True 仅当文件存在、JSON 合法、且根对象包含可递归遍历的 children 节点树。
    """
    try:
        with open(snapshot_path, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return False

    if not isinstance(data, dict):
        return False

    return _has_structured_children_tree(data, require_children=True)


def _has_structured_children_tree(node: Any, *, require_children: bool) -> bool:
    """检查对象是否为递归可遍历的 children 节点树。

    Snapshot 来自 Pencil ``batch_get`` 的完整内存树。这里不强绑完整 schema，
    但至少要求：
    - 根节点是 JSON object
    - 根节点必须有 ``children`` 且为 list
    - 任意出现 ``children`` 的节点都必须继续满足 list[object]
    """
    if not isinstance(node, dict):
        return False

    children = node.get("children")
    if children is None:
        return not require_children

    if not isinstance(children, list):
        return False

    return all(_has_structured_children_tree(child, require_children=False) for child in children)


def verify_save_report(report_path: Path) -> bool:
    """验证 save-report.json 结构与语义。

    除结构完整性（所有必需键存在）外，还验证保存是否真正成功：
    ``json_parse_verified`` 和 ``reopen_verified`` 必须均为 ``True``。

    Returns:
        True 仅当文件存在、JSON 合法、包含所有必需键、且两项验证均为 True。
    """
    try:
        with open(report_path, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return False

    if not SAVE_REPORT_REQUIRED_KEYS.issubset(data.keys()):
        return False

    # 语义校验：验证标志必须均为 True，否则保存未成功
    if data.get("json_parse_verified") is not True:
        return False
    return data.get("reopen_verified") is True
