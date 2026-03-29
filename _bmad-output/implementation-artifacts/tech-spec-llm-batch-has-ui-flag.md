---
title: 'LLM Batch 推荐增加 has_ui 标志'
slug: 'llm-batch-has-ui-flag'
created: '2026-03-29'
status: 'completed'
stepsCompleted: [1, 2, 3, 4]
tech_stack: ['Python ≥3.11', 'Pydantic ≥2.0', 'aiosqlite', 'structlog']
files_to_modify: ['src/ato/models/schemas.py', 'src/ato/batch.py', 'tests/unit/test_batch.py', 'tests/unit/test_schemas.py', 'tests/fixtures/claude_batch_recommend.json']
code_patterns: ['_StrictBase Pydantic model', 'BatchRecommendOutput JSON schema', 'LLMBatchRecommender two-stage validation']
test_patterns: ['pytest-asyncio', 'monkeypatch adapter mock', 'fixture JSON files']
---

# Tech-Spec: LLM Batch 推荐增加 has_ui 标志

**Created:** 2026-03-29

## Overview

### Problem Statement

LLM batch 推荐（Story 2B.5a）返回的 `BatchRecommendOutput` 只包含 `story_keys` 和 `reason`，不包含每个 story 是否涉及 UI 的判断。导致通过 LLM 推荐路径创建的 stories 入库时 `has_ui` 始终为 `False`，Story 9-3 引入的 `designing` 阶段条件跳过机制无法正确工作——纯后端 story 仍会执行不必要的 UX 设计阶段。

### Solution

扩展 `BatchRecommendOutput` 增加 `has_ui_map: dict[str, bool]` 字段，在 LLM prompt 中增加 UI 判断指令，在 `LLMBatchRecommender.recommend()` 中将 LLM 返回的 has_ui 信息回写到 `EpicInfo` 对象。数据通路 `EpicInfo.has_ui` → `confirm_batch()` → `stories.has_ui` 列已完全就绪，无需改动。

### Scope

**In Scope:**
- `BatchRecommendOutput` Pydantic model 增加 `has_ui_map` 字段
- `BATCH_RECOMMEND_JSON_SCHEMA` 增加对应 JSON schema 定义
- `build_llm_recommend_prompt()` 增加 UI 判断指令
- `LLMBatchRecommender.recommend()` 回写 has_ui 到 EpicInfo
- 相关单元测试和 fixture 更新

**Out of Scope:**
- `load_epics()` 从 epics.md 推断 has_ui（本地路径不改动）
- `LocalBatchRecommender` 改造（回退时 has_ui=False 作为安全默认值）
- `confirm_batch()` 逻辑（已支持 has_ui 写入）
- 数据库 schema 变更（has_ui 列已存在）

## Context for Development

### Codebase Patterns

- 所有 Pydantic record models 定义在 `models/schemas.py`，继承 `_StrictBase`（strict=True, extra="forbid"）
- `BATCH_RECOMMEND_JSON_SCHEMA` 是传给 Claude CLI `--json-schema` 的 dict，与 `BatchRecommendOutput` 必须保持同步
- `LLMBatchRecommender` 采用 fail-closed 策略：任何校验失败抛 `LLMRecommendError`，CLI 层 catch 后回退到 `LocalBatchRecommender`
- `EpicInfo` 是 frozen dataclass（`batch.py:36`），回写 `has_ui` 需要 `dataclasses.replace()` 创建新实例
- `_StrictBase` 的 `extra="forbid"` 意味着新增 `has_ui_map` 后旧格式输出会被拒绝——符合 fail-closed 设计
- `BATCH_RECOMMEND_JSON_SCHEMA` 有 `additionalProperties: False`，必须与 Pydantic model 同步
- 测试 helper `_make_epic()` 不含 `has_ui` 参数（`test_batch.py:251`），需扩展

### Files to Reference

| File | Purpose |
| ---- | ------- |
| `src/ato/models/schemas.py:408-435` | `BatchRecommendOutput` + `BATCH_RECOMMEND_JSON_SCHEMA` 定义 |
| `src/ato/batch.py:203-246` | `build_llm_recommend_prompt()` prompt 构建 |
| `src/ato/batch.py:337-461` | `LLMBatchRecommender` 完整实现（重点：第 5 步映射回 EpicInfo） |
| `src/ato/batch.py:36-46` | `EpicInfo` frozen dataclass（has_ui 字段已存在） |
| `src/ato/batch.py:547-575` | `confirm_batch()` 中 has_ui 写入（无需改动） |
| `tests/unit/test_batch.py:251-260` | `_make_epic()` helper（需加 has_ui 参数） |
| `tests/unit/test_batch.py:809-1036` | `TestLLMBatchRecommender` 全部 12 个测试 |
| `tests/unit/test_schemas.py:672-703` | `TestBatchRecommendOutput` schema 测试 |
| `tests/fixtures/claude_batch_recommend.json` | LLM 推荐 fixture JSON |

### Technical Decisions

1. **方案A: `has_ui_map: dict[str, bool]`** — 与 `story_keys` 平行的字典结构。优于对象数组方案，因为对 `story_keys` 的二次校验逻辑（重复检测、未知 key、数量上限）零改动
2. **`has_ui_map` 中缺失的 key 默认 `False`** — LLM 可能遗漏部分 story 的 has_ui 判断，安全降级为无 UI
3. **回退安全** — `LocalBatchRecommender` 不改动，回退时 has_ui 全部为 False，designing 阶段不会被跳过，这是安全的保守行为
4. **frozen dataclass 回写** — `EpicInfo` 是 frozen，需要 `dataclasses.replace(info, has_ui=...)` 创建新实例
5. **Pydantic model 使用 `default={}`** — `BatchRecommendOutput.has_ui_map` 默认空字典，现有测试不传此字段时不会破坏（Pydantic 填默认值）；JSON schema 中设为 required 确保 LLM 必须返回

## Implementation Plan

### Tasks

- [x] Task 1: 扩展 `BatchRecommendOutput` Pydantic model
  - File: `src/ato/models/schemas.py`
  - Action: 在 `BatchRecommendOutput` 类中 `reason` 字段之前添加 `has_ui_map: dict[str, bool] = {}`
  - Notes: 使用 `default_factory=dict` 不需要，因为 `{}` 是不可变空字面量在 Pydantic 中安全。`_StrictBase` 的 `strict=True` 确保 value 必须是 bool

- [x] Task 2: 同步更新 `BATCH_RECOMMEND_JSON_SCHEMA`
  - File: `src/ato/models/schemas.py`
  - Action: 在 `properties` 中添加 `has_ui_map` 定义；在 `required` 列表中添加 `"has_ui_map"`
  - Notes: JSON schema 定义如下：
    ```python
    "has_ui_map": {
        "type": "object",
        "additionalProperties": {"type": "boolean"},
        "description": "每个 story key 是否包含 UI 工作，true 表示有 UI",
    },
    ```

- [x] Task 3: 在 `build_llm_recommend_prompt()` 中增加 UI 判断指令
  - File: `src/ato/batch.py`
  - Action: 在 `## 约束` 段落末尾（`"- 按推荐优先级排序返回"` 之后）追加 has_ui 判断指令；在 `## 输出要求` 中补充 has_ui_map 描述
  - Notes: 新增 prompt 内容：
    ```
    - 判断每个推荐 story 是否涉及 UI/UX 工作（如 TUI 组件、界面交互、样式变更），在 has_ui_map 中标注 true/false
    ```
    输出要求补充：
    ```
    包含 story_keys（有序列表）、has_ui_map（每个 story 的 UI 标志）和 reason（推荐理由）。
    ```

- [x] Task 4: 在 `LLMBatchRecommender.recommend()` 中回写 has_ui 到 EpicInfo
  - File: `src/ato/batch.py`
  - Action: 在第 5 步（`# 5. 映射回 EpicInfo，按 LLM 返回顺序`）修改映射逻辑，使用 `dataclasses.replace()` 创建带 has_ui 的新 EpicInfo
  - Notes: 需要在文件顶部添加 `from dataclasses import replace`。修改后代码：
    ```python
    # 5. 映射回 EpicInfo，按 LLM 返回顺序，回写 has_ui
    selected = [
        replace(key_to_epic[key], has_ui=output.has_ui_map.get(key, False))
        for key in output.story_keys
    ]
    ```

- [x] Task 5: 更新 fixture 文件
  - File: `tests/fixtures/claude_batch_recommend.json`
  - Action: 在 `structured_output` 中添加 `has_ui_map` 字段
  - Notes: 与现有 `story_keys` 对应：
    ```json
    "has_ui_map": {"1-2-sqlite": false, "1-1-scaffolding": false}
    ```

- [x] Task 6: 更新 `_make_epic()` 测试 helper
  - File: `tests/unit/test_batch.py`
  - Action: 给 `_make_epic()` 函数签名添加 `has_ui: bool = False` 参数，传递给 `EpicInfo` 构造
  - Notes: 现有调用不传 `has_ui` 的测试不受影响（默认 False）

- [x] Task 7: 新增 schema 测试
  - File: `tests/unit/test_schemas.py`
  - Action: 在 `TestBatchRecommendOutput` 类中新增测试方法
  - Notes: 新增测试：
    - `test_valid_output_with_has_ui_map`: 验证带 `has_ui_map` 的正常构造
    - `test_default_empty_has_ui_map`: 验证不传 `has_ui_map` 时默认为空字典
    - `test_has_ui_map_non_bool_rejected`: strict 模式下 value 非 bool（如 int 1）被拒绝
    - `test_json_schema_has_has_ui_map`: 验证 JSON schema 包含 `has_ui_map` 字段

- [x] Task 8: 更新和新增 LLM recommender 测试
  - File: `tests/unit/test_batch.py`
  - Action: 在 `TestLLMBatchRecommender` 中新增 has_ui 相关测试；更新 `test_success_returns_llm_ordered_stories` 验证 has_ui 回写
  - Notes: 新增/修改测试：
    - 修改 `test_success_returns_llm_ordered_stories`: fixture `structured_output` 添加 `has_ui_map`，断言返回的 `proposal.stories[0].has_ui` 值正确
    - `test_has_ui_map_propagated_to_epic_info`: LLM 返回 `has_ui_map: {"key": true}` 时，对应 EpicInfo.has_ui 为 True
    - `test_missing_key_in_has_ui_map_defaults_false`: LLM 返回的 `has_ui_map` 缺少某个 story key 时，该 story 的 has_ui 默认 False
    - `test_empty_has_ui_map_all_default_false`: `has_ui_map: {}` 时所有 story 的 has_ui 为 False

- [x] Task 9: 更新 prompt 测试
  - File: `tests/unit/test_batch.py`
  - Action: 在 `TestBuildLlmRecommendPrompt` 中新增验证 has_ui 指令的测试
  - Notes: 新增测试：
    - `test_contains_has_ui_instruction`: 验证 prompt 中包含 `has_ui_map` 或 `UI` 相关判断指令

### Acceptance Criteria

- [x] AC 1: Given LLM 返回包含 `has_ui_map: {"story-a": true, "story-b": false}` 的 structured_output, when `LLMBatchRecommender.recommend()` 处理完成, then 返回的 `BatchProposal.stories` 中 story-a 的 `has_ui=True`、story-b 的 `has_ui=False`
- [x] AC 2: Given LLM 返回的 `has_ui_map` 缺少某个 story_key, when `recommend()` 处理, then 该 story 的 `has_ui` 默认为 `False`（安全降级）
- [x] AC 3: Given LLM 返回的 `has_ui_map` 为空字典 `{}`, when `recommend()` 处理, then 所有 story 的 `has_ui` 为 `False`
- [x] AC 4: Given `BatchRecommendOutput` 接收到 `has_ui_map` 中 value 为非 bool 类型（如 int 1）, when Pydantic 验证, then 抛出 `ValidationError`（strict 模式）
- [x] AC 5: Given `build_llm_recommend_prompt()` 被调用, when 生成 prompt, then prompt 文本中包含 UI 判断指令和 `has_ui_map` 输出要求
- [x] AC 6: Given LLM 推荐失败回退到 `LocalBatchRecommender`, when 生成 proposal, then 所有 story 的 `has_ui` 为 `False`（现有行为不变）
- [x] AC 7: Given `BATCH_RECOMMEND_JSON_SCHEMA`, when 检查 schema 定义, then `has_ui_map` 存在于 `properties` 和 `required` 中
- [x] AC 8: Given 现有不含 `has_ui_map` 的 `BatchRecommendOutput` 构造调用, when Pydantic 验证, then 使用默认值 `{}` 通过验证（向后兼容）

## Additional Context

### Dependencies

- 无外部依赖变更
- 依赖 Story 2B.5a 已完成（`BatchRecommendOutput`, `LLMBatchRecommender` 已存在）
- 依赖 Story 9-3 已完成（`has_ui` 列和 `EpicInfo.has_ui` 字段已存在）
- 与 Story 9-4（移除 planning phase）无冲突，可并行实施

### Testing Strategy

**单元测试（无需外部依赖）：**
- Schema 验证测试：4 个新增（`test_schemas.py`）
- Prompt 测试：1 个新增（`test_batch.py`）
- LLM Recommender 测试：1 个修改 + 3 个新增（`test_batch.py`）
- 现有 12 个 LLM recommender 测试必须全部通过（`has_ui_map` 默认 `{}` 保证兼容）

**验证命令：**
```bash
uv run pytest tests/unit/test_schemas.py::TestBatchRecommendOutput -v
uv run pytest tests/unit/test_batch.py::TestBuildLlmRecommendPrompt -v
uv run pytest tests/unit/test_batch.py::TestLLMBatchRecommender -v
uv run pytest  # 全量回归
```

### Notes

- `has_ui_map` 的 key 应与 `story_keys` 中的 key 使用相同格式（canonical 或 short），recommend() 中使用 `.get(key, False)` 容错
- 未来可考虑在 `load_epics()` 中通过 epics.md 内容关键词（如 "TUI"、"界面"、"UX"）推断 has_ui，作为 LLM 判断的补充
- `confirm_batch()` 已有 `has_ui` 写入逻辑（`batch.py:565` 和 `batch.py:573`），本次无需改动

## Review Notes

- Adversarial review completed
- Findings: 6 total, 4 fixed, 2 skipped
- Resolution approach: auto-fix
- F1 (skipped): JSON Schema required vs Pydantic default — 有意设计，向后兼容
- F2+F3+F5 (fixed): 添加 has_ui_map 与 story_keys 一致性 warning 日志
- F4 (fixed): 新增 key 格式不匹配降级测试
- F6 (skipped): strict 模式测试依赖——项目基础约定，无需调整
