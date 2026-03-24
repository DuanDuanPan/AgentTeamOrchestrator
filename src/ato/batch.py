"""batch — Batch 选择核心逻辑。

Epics 解析、推荐算法、batch 确认流程。
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

import aiosqlite
import structlog

from ato.models.db import _dt_to_iso
from ato.models.schemas import (
    BatchRecord,
    StoryRecord,
)

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

# ---------------------------------------------------------------------------
# 数据类 — epics 解析结果
# ---------------------------------------------------------------------------

# 将 epics 文件中的 story ID 格式（如 "2B.5", "1.4a"）转换为 canonical key
_STORY_ID_RE = re.compile(
    r"###\s+Story\s+(\d+[A-Za-z]?)\.(\d+[A-Za-z]?)\s*[:：]\s*(.+)",
)


@dataclass(frozen=True)
class EpicInfo:
    """从 epics.md 解析出的单个 story 信息。"""

    story_key: str  # canonical key, e.g. "2b-5-batch-select-status"
    short_key: str  # e.g. "2b-5"
    title: str
    epic_key: str  # e.g. "2b"
    dependencies: list[str] = field(default_factory=list)  # short_key list


@dataclass
class BatchProposal:
    """推荐的 batch 方案。"""

    stories: list[EpicInfo]
    reason: str = ""


# ---------------------------------------------------------------------------
# Epics 解析
# ---------------------------------------------------------------------------


def _normalize_short_key(raw: str) -> str:
    """将 epics story 标识转换为 short key。

    例: '2B.5' → '2b-5', '1.2' → '1-2', '2A.1' → '2a-1'
    """
    return raw.strip().lower().replace(".", "-")


def _parse_dependency_table(content: str) -> dict[str, list[str]]:
    """从 epics.md 的依赖表中解析 story 间的依赖关系。

    返回 {short_key: [依赖的 short_key 列表]}。
    """
    deps: dict[str, list[str]] = {}

    # 找到依赖表
    in_table = False
    for line in content.splitlines():
        stripped = line.strip()
        if "串行链" in stripped and "Stories" in stripped:
            in_table = True
            continue
        if in_table and stripped.startswith("|---"):
            continue
        if in_table and stripped.startswith("|"):
            # 提取 Stories 列
            parts = stripped.split("|")
            if len(parts) >= 3:
                chain_str = parts[2].strip()
                # 移除中文括号注释
                chain_str = re.sub(r"（[^）]*）", "", chain_str)
                # 处理逗号分隔的多条链
                for segment in chain_str.split(","):
                    segment = segment.strip()
                    if "→" not in segment:
                        continue
                    keys = [_normalize_short_key(k) for k in segment.split("→")]
                    for i in range(1, len(keys)):
                        target = keys[i]
                        dep = keys[i - 1]
                        if target not in deps:
                            deps[target] = []
                        if dep not in deps[target]:
                            deps[target].append(dep)
        elif in_table and not stripped.startswith("|"):
            break  # 表结束

    return deps


def load_epics(
    epics_path: Path,
    canonical_key_map: dict[str, str] | None = None,
) -> list[EpicInfo]:
    """从 epics.md 解析所有 story 信息。

    Args:
        epics_path: epics.md 文件路径。
        canonical_key_map: short_key → canonical story_key 映射。
            来源于 sprint-status.yaml 或数据库中的已知 keys。
            若未提供，story_key 退化为 short_key。

    返回按文件顺序排列的 EpicInfo 列表。
    """
    content = epics_path.read_text(encoding="utf-8")
    dep_map = _parse_dependency_table(content)
    key_map = canonical_key_map or {}

    stories: list[EpicInfo] = []
    current_epic = ""

    for line in content.splitlines():
        # 检测 Epic 标题
        epic_match = re.match(r"^##\s+Epic\s+(\d+[A-Za-z]?)\s*[:：]", line)
        if epic_match:
            current_epic = epic_match.group(1).lower()
            continue

        # 检测 Story 标题
        story_match = _STORY_ID_RE.match(line)
        if story_match:
            epic_part = story_match.group(1).lower()
            story_num = story_match.group(2)
            title = story_match.group(3).strip()
            short_key = f"{epic_part}-{story_num}"

            # 从 key_map 解析 canonical key，回退到 short_key
            story_key = key_map.get(short_key, short_key)

            stories.append(
                EpicInfo(
                    story_key=story_key,
                    short_key=short_key,
                    title=title,
                    epic_key=current_epic or epic_part,
                    dependencies=dep_map.get(short_key, []),
                )
            )

    return stories


def build_canonical_key_map(sprint_status_path: Path) -> dict[str, str]:
    """从 sprint-status.yaml 构建 short_key → canonical key 映射。

    sprint-status.yaml 中的 development_status key 格式为:
    ``1-2-sqlite-state-persistence``, ``2b-5-batch-select-status`` 等。
    short_key 是其数字前缀部分（如 ``1-2``, ``2b-5``）。
    """
    content = sprint_status_path.read_text(encoding="utf-8")
    key_map: dict[str, str] = {}

    # 匹配 YAML 中 "  key: value" 格式的 story 条目
    # 排除 epic-N, epic-N-retrospective 等非 story key
    story_key_re = re.compile(r"^\s+(\d+[a-z]?-\d+[a-z]?-[\w-]+)\s*:", re.MULTILINE)
    for match in story_key_re.finditer(content):
        canonical = match.group(1)
        # 提取 short_key: 取前两段数字部分
        # "2b-5-batch-select-status" → "2b-5"
        # "1-2-sqlite-state-persistence" → "1-2"
        parts = canonical.split("-")
        if len(parts) >= 2:
            # 找到第一个纯 slug 段（非数字非字母数字混合的 story ID 前缀）
            short_parts: list[str] = []
            for p in parts:
                # story short_key 段是数字或字母+数字混合（如 "2b", "5", "1"）
                if re.fullmatch(r"\d+[a-z]?", p):
                    short_parts.append(p)
                else:
                    break
            if len(short_parts) >= 2:
                short_key = "-".join(short_parts)
                key_map[short_key] = canonical

    return key_map


# ---------------------------------------------------------------------------
# BatchRecommender 协议
# ---------------------------------------------------------------------------


@runtime_checkable
class BatchRecommender(Protocol):
    """Batch 推荐器协议 — 可插拔替换（本地推荐 vs AI 推荐）。"""

    def recommend(
        self,
        epics_info: list[EpicInfo],
        existing_stories: dict[str, StoryRecord],
        max_stories: int,
    ) -> BatchProposal: ...


# ---------------------------------------------------------------------------
# 默认本地推荐实现
# ---------------------------------------------------------------------------


class LocalBatchRecommender:
    """基于依赖图和当前状态的本地推荐算法。"""

    def recommend(
        self,
        epics_info: list[EpicInfo],
        existing_stories: dict[str, StoryRecord],
        max_stories: int,
    ) -> BatchProposal:
        """生成推荐 batch。

        策略：
        1. 过滤出未完成且无阻塞依赖的 stories
        2. 按 epics.md 中的出现顺序（即优先级）排序
        3. 取前 max_stories 个
        """
        candidates: list[EpicInfo] = []

        for info in epics_info:
            story = existing_stories.get(info.story_key)

            # 已完成或已阻塞的跳过
            if story is not None and story.status in ("done", "blocked"):
                continue

            # 已在进行中的跳过（已被其他 batch 管理）
            if (
                story is not None
                and story.status not in ("backlog", "ready")
                and story.current_phase != "queued"
            ):
                continue

            # 检查依赖是否已满足（依赖 story 必须 done）
            deps_met = True
            for dep_short_key in info.dependencies:
                dep_done = False
                for other in epics_info:
                    if other.short_key == dep_short_key:
                        dep_story = existing_stories.get(other.story_key)
                        if dep_story is not None and dep_story.status == "done":
                            dep_done = True
                        break
                if not dep_done:
                    deps_met = False
                    break

            if deps_met:
                candidates.append(info)

        selected = candidates[:max_stories]
        return BatchProposal(
            stories=selected,
            reason=f"基于依赖分析，推荐 {len(selected)} 个可执行 stories",
        )


# ---------------------------------------------------------------------------
# Batch 确认（事务性写入）
# ---------------------------------------------------------------------------

DEFAULT_MAX_STORIES: int = 5


async def confirm_batch(
    db: aiosqlite.Connection,
    proposal: BatchProposal,
    selected_indices: list[int] | None = None,
) -> tuple[BatchRecord, int]:
    """在单个事务内原子完成 batch 创建。

    Returns:
        (BatchRecord, int): 创建的 batch 记录和实际写入 batch 的 story 数量。
        不可回退状态的 stories 会被排除，不计入数量。

    任一步骤失败整体回滚。
    """
    # 确定选中的 stories
    if selected_indices is not None:
        stories = [proposal.stories[i] for i in selected_indices]
    else:
        stories = proposal.stories

    if not stories:
        msg = "No stories selected for batch"
        raise ValueError(msg)

    now = datetime.now(tz=UTC)
    batch_id = str(uuid.uuid4())

    # 使用 BEGIN IMMEDIATE 确保原子性。
    # 不使用 SAVEPOINT，因为 aiosqlite 的 autocommit 行为会在 commit() 时释放 savepoint。
    # 整个事务通过 try/except + rollback 保证原子性。
    try:
        # 1. 校验无 active batch
        cursor = await db.execute("SELECT batch_id FROM batches WHERE status = ?", ("active",))
        row = await cursor.fetchone()
        if row is not None:
            msg = f"已存在 active batch: {row[0]}。请先完成或取消当前 batch。"
            raise ValueError(msg)

        # 2. 创建 batch 记录
        await db.execute(
            "INSERT INTO batches (batch_id, status, created_at, completed_at) VALUES (?, ?, ?, ?)",
            (batch_id, "active", _dt_to_iso(now), None),
        )

        now_iso = _dt_to_iso(now)

        # 3. 过滤不可回退的 stories — 只有 actionable stories 进入 batch
        immutable = ("done", "blocked", "in_progress", "review", "uat")
        actionable: list[EpicInfo] = []
        skipped: list[tuple[str, str]] = []  # (story_key, current_status)
        for info in stories:
            cursor = await db.execute(
                "SELECT status FROM stories WHERE story_id = ?",
                (info.story_key,),
            )
            row = await cursor.fetchone()
            if row is not None and row[0] in immutable:
                skipped.append((info.story_key, row[0]))
            else:
                actionable.append(info)

        if not actionable:
            msg = (
                "所有选中的 stories 均处于不可回退状态"
                f" ({', '.join(k for k, _ in skipped)})，"
                "无法创建有效 batch。"
            )
            raise ValueError(msg)

        for key, status in skipped:
            logger.info(
                "batch_exclude_immutable",
                story_id=key,
                current_status=status,
                reason="story 状态不可回退，未加入 batch",
            )

        # 4. 补齐 stories + 写入关联（仅 actionable，sequence_no 连续）
        for seq, info in enumerate(actionable):
            cursor = await db.execute(
                "SELECT story_id FROM stories WHERE story_id = ?",
                (info.story_key,),
            )
            exists = await cursor.fetchone()
            if exists is None:
                await db.execute(
                    "INSERT INTO stories (story_id, title, status, current_phase, "
                    "worktree_path, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        info.story_key,
                        info.title,
                        "backlog",
                        "queued" if seq > 0 else "creating",
                        None,
                        now_iso,
                        now_iso,
                    ),
                )

            await db.execute(
                "INSERT INTO batch_stories (batch_id, story_id, sequence_no) VALUES (?, ?, ?)",
                (batch_id, info.story_key, seq),
            )

        # 5. 更新状态：seq=0 → creating，其余 → queued
        for seq, info in enumerate(actionable):
            if seq == 0:
                await db.execute(
                    "UPDATE stories SET status = ?, current_phase = ?, "
                    "updated_at = ? WHERE story_id = ?",
                    ("planning", "creating", now_iso, info.story_key),
                )
            else:
                await db.execute(
                    "UPDATE stories SET status = ?, current_phase = ?, "
                    "updated_at = ? WHERE story_id = ?",
                    ("backlog", "queued", now_iso, info.story_key),
                )

        await db.commit()

    except BaseException:
        await db.rollback()
        raise

    batch = BatchRecord(
        batch_id=batch_id,
        status="active",
        created_at=now,
    )

    logger.info(
        "batch_confirmed",
        batch_id=batch_id,
        story_count=len(actionable),
        story_keys=[s.story_key for s in actionable],
        skipped=[k for k, _ in skipped] if skipped else None,
    )

    return batch, len(actionable)
