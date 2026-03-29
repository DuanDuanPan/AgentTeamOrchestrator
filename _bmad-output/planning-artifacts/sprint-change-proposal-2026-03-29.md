# Sprint Change Proposal: Batch 推荐算法 LLM 增强

**日期:** 2026-03-29
**触发来源:** `ato batch select` 在真实项目中推荐结果不可靠
**变更范围:** Minor — 开发团队直接实施

---

## 1. 问题摘要

### 问题陈述

`ato batch select` 当前使用 `LocalBatchRecommender` 进行 story 推荐，算法仅基于 epics.md 依赖链和 DB 中的 story status 做机械过滤。在真实项目中，部分特性可能已通过其他途径部分实现（手动开发、跨 story 附带完成、外部贡献等），纯依赖图 + 状态过滤无法识别这些情况，导致：

1. **推荐已实质完成的 stories** — status 仍为 `backlog` 但功能已存在
2. **忽略项目实际上下文** — 不考虑代码库当前状态、技术风险、实现复杂度
3. **未实现 FR12 原始意图** — FR12 要求 "PM agent 分析 epic/story 的优先级和依赖关系"，当前实现无 agent 参与

### 发现过程

在实际项目（部分 epic 已完成、部分特性跨 story 实现）中使用 `ato batch select` 时，推荐结果不符合预期。

### 证据

- `batch.py:220-273` — `LocalBatchRecommender.recommend()` 仅做 status 过滤 + 依赖检查，无语义分析
- `batch.py:204-211` — `BatchRecommender` Protocol 已设计为可插拔接口，预留了扩展点
- `cli.py:438` — 推荐模式硬编码使用 `LocalBatchRecommender()`，无切换机制
- FR12 原文："PM agent 可分析 epic/story 的优先级和依赖关系，生成推荐的 batch 方案供操作者选择"

---

## 2. 影响分析

### Epic 影响

| Epic | 影响 | 详情 |
|------|------|------|
| Epic 2B（Agent 集成） | 已交付 story 2B.5 需增强 | 推荐算法增加 LLM 路径 |
| 其他 Epic | 无影响 | — |

### Story 影响

- 不需要修改已完成的 story 2B.5 代码
- 在 Epic 2B 中新增 **Story 2B.5a**（LLM Batch 推荐增强）

### Artifact 冲突

- **PRD:** 无冲突 — 变更更完整地实现 FR12
- **Architecture:** 无冲突 — `BatchRecommender` Protocol 已预留可插拔设计，遵循 NFR11 adapter 隔离
- **UX:** 无冲突 — CLI 交互模式不变（推荐 → 展示 → 确认），仅推荐来源变化

### 技术影响

- 新增 `LLMBatchRecommender` 类（async），复用 `ClaudeAdapter`
- CLI 新增 `--llm` flag
- 需按 Decision 9 补充 snapshot fixture
- `LocalBatchRecommender` 保留为默认 fallback

---

## 3. 推荐方案

### 选择：直接调整 — 新增修正 Story 2B.5a

**理由：**
- 架构已预留 `BatchRecommender` Protocol 扩展点，这是最自然的演进方向
- 实现成本低，纯新增代码路径，不修改现有逻辑
- `LocalBatchRecommender` 保留为无 LLM 环境下的 fallback，向后兼容
- 与 Epic 7 Growth 路线（Story 7.3a Memory 层）正交，未来可叠加

**工作量：** Low
**风险：** Low
**时间线影响：** 无

---

## 4. 详细变更提案

### Change 1: 新增 `LLMBatchRecommender`

**文件:** `src/ato/batch.py`

**新增内容：**

```python
class LLMBatchRecommender:
    """基于 LLM（Claude Code）的智能 batch 推荐器。

    通过 claude -p 调用 LLM，结合 epics 信息、sprint 状态、
    代码库上下文等进行语义分析，给出更鲁棒的推荐。
    """

    def __init__(self, adapter: ClaudeAdapter) -> None:
        self._adapter = adapter

    async def recommend(
        self,
        epics_info: list[EpicInfo],
        existing_stories: dict[str, StoryRecord],
        max_stories: int,
    ) -> BatchProposal:
        # 1. 构建 prompt（包含 epics + 状态 + 约束）
        # 2. 通过 ClaudeAdapter.execute() 调用 claude -p --json-schema
        # 3. 解析结构化输出为 BatchProposal
        ...
```

**要点：**
- 遵循 NFR11：通过 `ClaudeAdapter` 调用，不直接拼 CLI 命令
- 使用 `--json-schema` 获取结构化输出，确保可靠解析
- prompt 包含：epics 依赖关系、各 story 当前状态、已完成 story 列表、max_stories 约束
- LLM 需在 prompt 中被指示评估项目实际状态，排除已实质完成的 stories

### Change 2: CLI 新增 `--llm` flag

**文件:** `src/ato/cli.py`
**位置:** `batch_select()` 函数

**OLD:**
```python
@batch_app.command("select")
def batch_select(
    epics_file: Path | None = ...,
    db_path: Path | None = ...,
    max_stories: int = ...,
    story_ids: str | None = ...,
) -> None:
```

**NEW:**
```python
@batch_app.command("select")
def batch_select(
    epics_file: Path | None = ...,
    db_path: Path | None = ...,
    max_stories: int = ...,
    story_ids: str | None = ...,
    use_llm: bool = typer.Option(False, "--llm", help="使用 LLM 智能推荐（需 Claude CLI）"),
) -> None:
```

**推荐模式分支：**

**OLD:**
```python
recommender = LocalBatchRecommender()
proposal = recommender.recommend(epics_info, existing_stories, max_stories)
```

**NEW:**
```python
if use_llm:
    from ato.adapters.claude_cli import ClaudeAdapter
    adapter = ClaudeAdapter()
    recommender = LLMBatchRecommender(adapter)
    proposal = await recommender.recommend(epics_info, existing_stories, max_stories)
else:
    local = LocalBatchRecommender()
    proposal = local.recommend(epics_info, existing_stories, max_stories)
```

**Rationale:** `--llm` 是显式 opt-in，默认行为不变，向后兼容。

### Change 3: Snapshot Fixture 补充

**文件:** `tests/fixtures/claude_batch_recommend.json`（新增）

按 Decision 9 要求，保存 LLM 推荐的真实 Claude CLI 输出样本，用于单元测试。

### Change 4: 单元测试

**文件:** `tests/unit/test_batch.py`（扩展）

新增测试：
- `test_llm_recommender_parses_structured_output` — mock ClaudeAdapter，验证结构化输出解析
- `test_llm_recommender_fallback_on_error` — LLM 调用失败时的错误处理
- `test_llm_recommender_respects_max_stories` — 验证 max_stories 约束

---

## 5. 实施移交

### 变更分类：Minor

**移交对象:** Dev

**职责：**
1. 创建 Story 2B.5a spec 文件
2. 实现 `LLMBatchRecommender` + `--llm` CLI flag
3. 补充 snapshot fixture 和单元测试
4. 更新 sprint-status.yaml

### 成功标准

- `ato batch select --llm` 返回语义合理的推荐（考虑项目实际状态）
- `ato batch select`（无 `--llm`）行为完全不变
- 单元测试全部通过，不依赖真实 CLI 调用
- mypy strict + ruff 通过
