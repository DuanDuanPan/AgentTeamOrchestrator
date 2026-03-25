"""validation — Deterministic validation 模块。

JSON Schema 快速验证层，在 agent review 之前拦截结构错误。
Blocking 阈值 escalation 通过 approval 记录通知操作者。
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite
import structlog
from jsonschema import Draft202012Validator

from ato.models.db import count_findings_by_severity, insert_approval
from ato.models.schemas import (
    ApprovalRecord,
    ConfigError,
    SchemaValidationIssue,
    ValidationResult,
)

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


def _get_schemas_dir() -> Path:
    """返回仓库根 schemas/ 目录路径。

    从当前模块路径向上推导仓库根目录。
    """
    # src/ato/validation.py → 仓库根
    module_dir = Path(__file__).resolve().parent
    repo_root = module_dir.parent.parent
    return repo_root / "schemas"


def load_schema(schema_name: str) -> dict[str, Any]:
    """从仓库根 schemas/ 目录加载 JSON Schema 文件。

    Args:
        schema_name: Schema 文件名（不含路径，如 "review-findings.json"）。

    Returns:
        解析后的 JSON Schema dict。

    Raises:
        ConfigError: 文件不存在或 JSON 解析失败。
    """
    schemas_dir = _get_schemas_dir()
    schema_path = schemas_dir / schema_name
    if not schema_path.exists():
        msg = f"Schema file not found: {schema_path}"
        raise ConfigError(msg)
    try:
        with schema_path.open(encoding="utf-8") as f:
            schema: dict[str, Any] = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        msg = f"Failed to load schema '{schema_name}': {exc}"
        raise ConfigError(msg) from exc
    return schema


def validate_artifact(
    artifact_data: dict[str, Any],
    schema_name: str,
) -> ValidationResult:
    """使用 JSON Schema 验证 artifact 数据。

    使用 Draft202012Validator.iter_errors() 收集完整错误列表。

    Args:
        artifact_data: 待验证的 artifact 数据。
        schema_name: Schema 文件名。

    Returns:
        ValidationResult(passed=True/False, errors=[...])。
    """
    schema = load_schema(schema_name)

    # 先验证 schema 本身的正确性
    Draft202012Validator.check_schema(schema)

    validator = Draft202012Validator(schema)
    errors: list[SchemaValidationIssue] = []
    for error in validator.iter_errors(artifact_data):
        errors.append(
            SchemaValidationIssue(
                path=".".join(str(p) for p in error.absolute_path) or "$",
                message=error.message,
                schema_path=".".join(str(p) for p in error.absolute_schema_path),
            )
        )

    return ValidationResult(
        passed=len(errors) == 0,
        errors=errors,
    )


# ---------------------------------------------------------------------------
# Blocking 阈值 escalation (AC4)
# ---------------------------------------------------------------------------


async def count_blocking_findings(
    db: aiosqlite.Connection,
    story_id: str,
    round_num: int,
) -> int:
    """统计当前轮次 blocking finding 数量。"""
    counts = await count_findings_by_severity(db, story_id, round_num)
    return counts.get("blocking", 0)


async def maybe_create_blocking_abnormal_approval(
    db: aiosqlite.Connection,
    story_id: str,
    round_num: int,
    threshold: int,
    *,
    nudge: Any | None = None,
    orchestrator_pid: int | None = None,
) -> bool:
    """当 blocking 数量超过阈值时创建 blocking_abnormal approval。

    Args:
        db: 数据库连接。
        story_id: Story ID。
        round_num: 当前 review 轮次。
        threshold: blocking 阈值。
        nudge: 进程内 Nudge 实例（可选）。
        orchestrator_pid: Orchestrator PID，进程外 nudge 用（可选）。

    Returns:
        True 表示创建了 approval（超阈值），False 表示未超。
    """
    blocking_count = await count_blocking_findings(db, story_id, round_num)

    if blocking_count <= threshold:
        logger.info(
            "blocking_below_threshold",
            story_id=story_id,
            round_num=round_num,
            blocking_count=blocking_count,
            threshold=threshold,
        )
        return False

    # 幂等检查：同一 story + round_num 不重复创建 blocking_abnormal approval
    cursor = await db.execute(
        "SELECT payload FROM approvals WHERE story_id = ? AND approval_type = ? AND status = ?",
        (story_id, "blocking_abnormal", "pending"),
    )
    for row in await cursor.fetchall():
        try:
            existing_payload = json.loads(row[0] or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        if existing_payload.get("round_num") == round_num:
            logger.info(
                "blocking_abnormal_approval_exists",
                story_id=story_id,
                round_num=round_num,
                blocking_count=blocking_count,
            )
            return True

    # 创建 blocking_abnormal approval
    approval = ApprovalRecord(
        approval_id=str(uuid.uuid4()),
        story_id=story_id,
        approval_type="blocking_abnormal",
        status="pending",
        payload=json.dumps(
            {
                "blocking_count": blocking_count,
                "threshold": threshold,
                "round_num": round_num,
            }
        ),
        created_at=datetime.now(tz=UTC),
    )
    await insert_approval(db, approval)
    logger.warning(
        "blocking_threshold_exceeded",
        story_id=story_id,
        round_num=round_num,
        blocking_count=blocking_count,
        threshold=threshold,
        approval_id=approval.approval_id,
    )

    # 按调用上下文发送 nudge
    if nudge is not None:
        nudge.notify()
    elif orchestrator_pid is not None:
        from ato.nudge import send_external_nudge

        send_external_nudge(orchestrator_pid)

    return True
