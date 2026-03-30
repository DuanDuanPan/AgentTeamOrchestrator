"""batch — Batch 选择核心逻辑。

Epics 解析、推荐算法、batch 确认流程。
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

import aiosqlite
import structlog

from ato.models.db import _dt_to_iso
from ato.models.schemas import (
    BatchRecord,
    ProgressCallback,
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
    has_ui: bool = False


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
# LLM 推荐 prompt 构建
# ---------------------------------------------------------------------------


def build_llm_recommend_prompt(
    max_stories: int,
    epics_path: str,
    sprint_status_path: str | None = None,
) -> str:
    """为 LLM batch 推荐构建 prompt。

    不传递候选列表，而是告诉 LLM 项目文件位置，
    让 LLM 自行阅读 epics、sprint status 和代码库来决定推荐。
    """
    lines: list[str] = [
        "你是一个项目编排助手。请分析当前项目环境，推荐最有价值的 stories 组成 batch。",
        "",
        "## 你需要做的",
        f"1. 阅读 epics 文件: `{epics_path}`",
    ]

    if sprint_status_path:
        lines.append(f"2. 阅读 sprint 状态文件: `{sprint_status_path}`")
        lines.append("3. 浏览项目代码库，判断哪些 story 已在代码中实质完成")
        lines.append("4. 分析 story 间的依赖关系")
        lines.append(f"5. 推荐最多 {max_stories} 个最有价值的待实现 stories")
    else:
        lines.append("2. 浏览项目代码库，判断哪些 story 已在代码中实质完成")
        lines.append("3. 分析 story 间的依赖关系")
        lines.append(f"4. 推荐最多 {max_stories} 个最有价值的待实现 stories")

    lines.extend(
        [
            "",
            "## 约束",
            f"- 最多选择 {max_stories} 个 stories",
            "- story_keys 必须使用 sprint-status.yaml 中的 canonical key 格式"
            "（如 `1-1-enabler-project-init`），若无 sprint-status 则使用"
            " epics 中的 short key 格式（如 `1-1`）",
            "- 排除已在代码中实质完成的 stories",
            "- 排除依赖未满足的 stories（前置 story 未完成）",
            "- 按推荐优先级排序返回",
            "- 判断每个推荐 story 是否涉及 UI/UX 工作"
            "（如 TUI 组件、界面交互、样式变更），在 has_ui_map 中标注 true/false",
            "",
            "## 输出要求",
            "严格按照 JSON schema 返回 structured output，"
            "包含 story_keys（有序列表）、has_ui_map（每个 story 的 UI 标志）"
            "和 reason（推荐理由）。",
        ]
    )

    return "\n".join(lines)


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
# LLM 推荐实现 (Story 2B.5a)
# ---------------------------------------------------------------------------


class LLMRecommendError(Exception):
    """LLM 推荐失败，调用方应回退到本地推荐。"""


class LLMBatchRecommender:
    """基于 Claude LLM 的智能 batch 推荐。

    让 Claude 自行阅读项目的 epics、sprint status 和代码库，
    自主判断哪些 stories 已完成、哪些依赖未满足，
    从而推荐最有价值的 batch。

    所有失败路径（adapter 错误、schema 不匹配、二次校验不通过）
    一律抛出 :class:`LLMRecommendError`，由 CLI 层统一 catch 并回退。
    """

    def __init__(
        self,
        adapter: object,  # ClaudeAdapter — 延迟类型引用避免循环导入
        project_root: Path,
        epics_path: Path,
        sprint_status_path: Path | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> None:
        self._adapter = adapter
        self._project_root = project_root
        self._epics_path = epics_path
        self._sprint_status_path = sprint_status_path
        self._on_progress = on_progress

    async def recommend(
        self,
        epics_info: list[EpicInfo],
        existing_stories: dict[str, StoryRecord],
        max_stories: int,
    ) -> BatchProposal:
        """LLM 推荐入口：让 Claude 自主分析项目环境并推荐。

        Raises:
            LLMRecommendError: 任何失败（Claude 调用、schema 校验、
                二次校验），调用方应 catch 并回退到本地推荐。
        """
        from ato.models.schemas import BATCH_RECOMMEND_JSON_SCHEMA, BatchRecommendOutput

        # 1. 构建 prompt — 只传文件路径，让 LLM 自己读
        sprint_status_str = (
            str(self._sprint_status_path)
            if self._sprint_status_path and self._sprint_status_path.exists()
            else None
        )
        prompt = build_llm_recommend_prompt(
            max_stories,
            epics_path=str(self._epics_path),
            sprint_status_path=sprint_status_str,
        )

        # 2. 调用 Claude — cwd 设为项目根目录，给足 turns 让 LLM 阅读文件
        try:
            result = await self._adapter.execute(  # type: ignore[attr-defined]
                prompt,
                {
                    "json_schema": BATCH_RECOMMEND_JSON_SCHEMA,
                    "cwd": str(self._project_root),
                },
                on_progress=self._on_progress,
            )
        except Exception as exc:
            logger.warning("llm_batch_recommend_call_failed", exc_info=True)
            raise LLMRecommendError("Claude CLI 调用失败") from exc

        # 3. 校验 structured_output
        raw = result.structured_output
        if raw is None:
            logger.warning("llm_batch_recommend_no_structured_output")
            raise LLMRecommendError("Claude 未返回 structured_output")

        try:
            output = BatchRecommendOutput.model_validate(raw)
        except Exception as exc:
            logger.warning(
                "llm_batch_recommend_schema_validation_failed",
                exc_info=True,
            )
            raise LLMRecommendError("structured_output schema 校验失败") from exc

        # 4. Python 侧二次校验
        #    构建合法 key 集合（canonical key + short key 均可接受）
        valid_keys = {info.story_key for info in epics_info}
        valid_keys |= {info.short_key for info in epics_info}
        key_to_epic: dict[str, EpicInfo] = {}
        for info in epics_info:
            key_to_epic[info.story_key] = info
            key_to_epic[info.short_key] = info

        seen: set[str] = set()
        seen_canonical: set[str] = set()
        for key in output.story_keys:
            if key in seen:
                logger.warning("llm_batch_recommend_duplicate_key", key=key)
                raise LLMRecommendError(f"LLM 返回重复 key: {key}")
            seen.add(key)
            if key not in valid_keys:
                logger.warning(
                    "llm_batch_recommend_unknown_key",
                    key=key,
                )
                raise LLMRecommendError(
                    f"LLM 返回未知 key: {key}",
                )
            canonical = key_to_epic[key].story_key
            if canonical in seen_canonical:
                logger.warning(
                    "llm_batch_recommend_canonical_duplicate",
                    key=key,
                    canonical=canonical,
                )
                raise LLMRecommendError(
                    f"LLM 返回重复 story（别名）: {key} → {canonical}",
                )
            seen_canonical.add(canonical)

        if len(output.story_keys) > max_stories:
            logger.warning(
                "llm_batch_recommend_exceeds_max",
                returned=len(output.story_keys),
                max_stories=max_stories,
            )
            raise LLMRecommendError(
                f"LLM 返回 {len(output.story_keys)} 个 stories，超过上限 {max_stories}",
            )

        if not output.story_keys:
            logger.warning("llm_batch_recommend_empty_result")
            raise LLMRecommendError("LLM 返回空 story_keys")

        # 5. 归一化 has_ui_map — 将任意 key 格式解析为 canonical story_key
        resolved_has_ui: dict[str, bool] = {}
        for ui_key, ui_val in output.has_ui_map.items():
            epic = key_to_epic.get(ui_key)
            if epic is None:
                logger.warning(
                    "llm_batch_recommend_has_ui_map_unknown_key",
                    key=ui_key,
                )
                raise LLMRecommendError(
                    f"has_ui_map 包含未知 key: {ui_key}",
                )
            canonical = epic.story_key
            if canonical in resolved_has_ui:
                logger.warning(
                    "llm_batch_recommend_has_ui_map_alias_conflict",
                    key=ui_key,
                    canonical=canonical,
                )
                raise LLMRecommendError(
                    f"has_ui_map 包含同一 story 的冲突别名: {ui_key} → {canonical}",
                )
            resolved_has_ui[canonical] = ui_val

        # 6. 映射回 EpicInfo，按 LLM 返回顺序，回写 has_ui
        selected = [
            replace(
                key_to_epic[key],
                has_ui=resolved_has_ui.get(key_to_epic[key].story_key, False),
            )
            for key in output.story_keys
        ]

        logger.info(
            "llm_batch_recommend_success",
            selected_count=len(selected),
            reason=output.reason,
        )
        return BatchProposal(
            stories=selected,
            reason=f"LLM 推荐: {output.reason}",
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
                    "worktree_path, has_ui, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        info.story_key,
                        info.title,
                        "backlog",
                        "queued" if seq > 0 else "creating",
                        None,
                        int(info.has_ui),
                        now_iso,
                        now_iso,
                    ),
                )
            else:
                # 更新已存在 story 的 has_ui（batch select 可能重新设置）
                await db.execute(
                    "UPDATE stories SET has_ui = ? WHERE story_id = ?",
                    (int(info.has_ui), info.story_key),
                )

            await db.execute(
                "INSERT INTO batch_stories (batch_id, story_id, sequence_no) VALUES (?, ?, ?)",
                (batch_id, info.story_key, seq),
            )

        # 5. 更新状态：seq=0 → planning(status) + creating(phase)，其余 → queued
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
