"""schemas — Pydantic 数据模型定义。

所有 Pydantic record models、异常类层次、跨模块常量统一定义于此。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

# ---------------------------------------------------------------------------
# 跨模块常量
# ---------------------------------------------------------------------------

SCHEMA_VERSION: int = 1
"""当前数据库 schema 版本号，与 PRAGMA user_version 对应。"""

# ---------------------------------------------------------------------------
# 异常类层次
# ---------------------------------------------------------------------------


class ATOError(Exception):
    """ATO 所有自定义异常的基类。"""


class CLIAdapterError(ATOError):
    """CLI 调用失败。"""


class StateTransitionError(ATOError):
    """状态机转换非法。"""


class RecoveryError(ATOError):
    """崩溃恢复 / 迁移失败。"""


class ConfigError(ATOError):
    """配置解析错误。"""


# ---------------------------------------------------------------------------
# 共享 Pydantic 配置
# ---------------------------------------------------------------------------


class _StrictBase(BaseModel):
    """所有 record model 共享的严格校验基类。

    strict=True: 禁止隐式类型转换（如 ``"1"`` → ``1``）。
    extra="forbid": 拒绝未声明字段。
    """

    model_config = ConfigDict(strict=True, extra="forbid")


# ---------------------------------------------------------------------------
# Record Models
# ---------------------------------------------------------------------------

# Story 生命周期状态
StoryStatus = Literal[
    "backlog",
    "planning",
    "ready",
    "in_progress",
    "review",
    "uat",
    "done",
    "blocked",
]


class StoryRecord(_StrictBase):
    """stories 表对应的 Pydantic 模型。"""

    story_id: str
    title: str
    status: StoryStatus
    current_phase: str
    worktree_path: str | None = None
    created_at: datetime
    updated_at: datetime


# Task 状态
TaskStatus = Literal["pending", "running", "paused", "completed", "failed"]


class TaskRecord(_StrictBase):
    """tasks 表对应的 Pydantic 模型。"""

    task_id: str
    story_id: str
    phase: str
    role: str
    cli_tool: Literal["claude", "codex"]
    status: TaskStatus
    pid: int | None = None
    expected_artifact: str | None = None
    context_briefing: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    exit_code: int | None = None
    cost_usd: float | None = None
    duration_ms: int | None = None
    error_message: str | None = None


# Approval 状态
ApprovalStatus = Literal["pending", "approved", "rejected"]


class ApprovalRecord(_StrictBase):
    """approvals 表对应的 Pydantic 模型。"""

    approval_id: str
    story_id: str
    approval_type: str
    status: ApprovalStatus
    payload: str | None = None
    decision: str | None = None
    decided_at: datetime | None = None
    created_at: datetime
