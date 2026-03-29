# Story 2B.5a: LLM 智能 batch 推荐增强

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->
<!-- Depends on: Story 2B.5 (batch 选择基线), Story 2B.1 (Claude adapter JSON 合同) -->
<!-- Related: _bmad-output/planning-artifacts/sprint-change-proposal-2026-03-29.md -->

## Story

As a 操作者,
I want 在运行 `ato batch select --llm` 时由 Claude 基于 epics、当前 story 状态与仓库实际实现情况推荐 batch,
So that 系统不会继续机械推荐已实质完成、当前不值得优先执行或与项目现状不匹配的 stories。

## 问题背景

当前 `src/ato/batch.py` 中的 `LocalBatchRecommender` 只基于 epics 依赖链与 SQLite 中的 story 状态做确定性过滤；`src/ato/cli.py::_batch_select_async()` 也固定走本地推荐路径。这满足了 Story 2B.5 的 MVP，但没有完整兑现 FR12 中“PM agent 分析 epic/story 的优先级和依赖关系”的原始意图。

在真实项目里，部分工作可能已通过手动开发、跨 story 顺手实现、外部贡献或设计/实现链路提前落地，导致 story 仍是 `backlog`，但继续推荐它其实没有价值。2B.5a 的目标不是替换现有确定性 guardrails，而是在保留依赖/状态正确性的前提下，引入一个显式 opt-in 的 LLM 推荐路径。

## Acceptance Criteria

### AC1: `--llm` 为显式 opt-in，默认行为保持不变

```gherkin
Given 当前 `ato batch select` 已有本地推荐与 `--story-ids` 直选两条路径
When 操作者未传入 `--llm`
Then CLI 行为与 Story 2B.5 完全一致
And 仍使用 `LocalBatchRecommender`
And `confirm_batch()`、`ato batch status`、交互式编号选择等后续流程不变

Given 操作者传入 `ato batch select --llm`
When 命令进入推荐模式
Then CLI 先尝试 LLM 推荐路径
And `--story-ids` 仍然优先，直接跳过所有推荐逻辑
```

### AC2: LLM 只能在 Python 已确认可执行的候选集合上做语义排序/筛选

```gherkin
Given epics 解析结果与当前 SQLite story 状态
When 系统准备 LLM 推荐输入
Then Python 先基于当前确定性规则生成 eligible candidate pool
And 该候选集合与现有 `LocalBatchRecommender` 的依赖/状态约束保持一致
And LLM 只能从该候选集合中选择或重排 stories，不能引入集合外的 story

Given 没有任何 eligible candidate
When 运行 `ato batch select --llm`
Then 行为与当前一致：输出“没有可推荐的 stories（所有 stories 已完成或依赖未满足）”
And 不发起 Claude CLI 调用
```

### AC3: Claude 调用必须复用现有 adapter 合同并使用严格结构化输出

```gherkin
Given `src/ato/adapters/claude_cli.py::ClaudeAdapter.execute()` 已支持 `json_schema`、`cwd`、`max_turns`
When `LLMBatchRecommender` 调用 Claude
Then 必须通过 `ClaudeAdapter.execute()` 发起调用
And 不允许在 batch/cli 层手写 `claude` shell 命令
And 使用严格 JSON schema 约束输出至少包含：有序 story keys 列表、recommendation reason
And Python 通过严格 schema 再次验证 `ClaudeOutput.structured_output`

Given `--llm` 推荐需要考虑仓库当前实现状态
When Claude 被调用
Then adapter `cwd` 必须解析到项目根目录，而不是依赖调用命令时的随机当前目录
And 推荐 prompt 必须同时包含：eligible candidates、各 story 当前状态、`max_stories` 约束、明确要求排除“已实质完成”的 stories
```

### AC4: LLM 输出必须经过 Python 侧二次校验，任何异常都回退到本地推荐

```gherkin
Given `--llm` 推荐已返回结构化输出
When 输出中包含未知 story key、重复 key、超过 `max_stories` 的数量、集合外 key，或最终映射后为空
Then 系统将该次 LLM 结果视为无效
And 记录结构化日志
And 自动回退到 `LocalBatchRecommender`

Given Claude CLI 调用失败、超时、结构化输出缺失或 schema 校验失败
When `ato batch select --llm` 继续执行
Then CLI 向操作者输出一条简洁提示，说明“LLM 推荐失败，已回退到本地推荐”
And 命令整体不因该错误直接失败
And 后续展示、交互选择、`confirm_batch()` 流程保持不变
```

### AC5: 测试与 fixture 覆盖 LLM 推荐合同，不依赖真实 Claude CLI

```gherkin
Given Story 2B.1 与 Decision 9 已建立 Claude snapshot fixture 基线
When 本 story 完成
Then 新增一个面向 batch 推荐的 Claude 输出 fixture（如 `tests/fixtures/claude_batch_recommend.json`）
And 单元测试覆盖：
  - LLM structured output 解析成功
  - 返回未知/重复/集合外 story key 时回退本地推荐
  - ClaudeAdapter 抛错或超时时回退本地推荐
  - `--llm` flag 正确接线
  - 未传 `--llm` 时默认路径保持原行为
And 所有测试均通过 mock / fixture 完成，不依赖真实 `claude` 可执行文件
```

## Tasks / Subtasks

- [x] Task 1: 定义 LLM 推荐的严格输出合同与 prompt 构建 (AC: #2, #3)
  - [x] 1.1 在 `src/ato/models/schemas.py` 中新增 batch 推荐 structured output 的严格 Pydantic 模型（至少包含 `story_keys: list[str]`、`reason: str`），并更新 `src/ato/models/__init__.py` 导出
  - [x] 1.2 在 `src/ato/batch.py` 中新增 prompt builder，序列化 eligible candidates、当前状态、`max_stories` 与”排除已实质完成工作”的指令
  - [x] 1.3 复用共享 project-root 解析逻辑为 Claude 调用设置 `cwd`，不要在 batch 模块里重新发明路径推导
  - [x] 1.4 不把本 story 扩大为”通用 recommender 抽象重构”；现有同步 `BatchRecommender` / `LocalBatchRecommender` 继续作为本地基线保留

- [x] Task 2: 在 `src/ato/batch.py` 中实现 `LLMBatchRecommender` (AC: #2, #3, #4)
  - [x] 2.1 注入 `ClaudeAdapter`，通过 `execute(prompt, options={...})` 调用 Claude
  - [x] 2.2 先生成 deterministic eligible candidate pool，再把该集合交给 LLM 做语义排序/筛选
  - [x] 2.3 将 LLM 返回的 `story_keys` 映射回 `EpicInfo`，并按返回顺序构造 `BatchProposal`
  - [x] 2.4 对未知 key、重复 key、集合外 key、超量结果执行 Python 侧校验；任何无效结果都触发 fallback
  - [x] 2.5 Claude 调用失败、超时、schema 校验失败或有效结果为空时，记录日志并回退到 `LocalBatchRecommender`
  - [x] 2.6 不修改 `confirm_batch()` 的事务行为，也不改 batch status 聚合规则；本 story 只影响”proposal 如何生成”

- [x] Task 3: 在 CLI 中增加 `--llm` 路径接线 (AC: #1, #3, #4)
  - [x] 3.1 在 `src/ato/cli.py::batch_select()` 中新增 `use_llm: bool = typer.Option(False, “--llm”, ...)`
  - [x] 3.2 更新 `_batch_select_async()` 签名并向下传递 `use_llm`
  - [x] 3.3 在推荐模式中：`use_llm=True` 时调用 `LLMBatchRecommender`，否则保持 `LocalBatchRecommender`
  - [x] 3.4 保持 `--story-ids` 直选优先级最高，不进入 LLM 或本地推荐逻辑
  - [x] 3.5 回退发生时只向用户显示一条简洁提示；详细异常留给 `structlog`

- [x] Task 4: 补充 fixture 与测试 (AC: #5)
  - [x] 4.1 新增 `tests/fixtures/claude_batch_recommend.json`
  - [x] 4.2 在 `tests/unit/test_batch.py` 中新增 `LLMBatchRecommender` 的成功、无效结果、fallback 场景测试
  - [x] 4.3 在 `tests/unit/test_cli_batch.py` 中新增 `--llm` flag 路径测试与”默认无 flag 不变”回归测试
  - [x] 4.4 若新增了 schema 模型，在 `tests/unit/test_schemas.py` 中增加严格校验测试
  - [x] 4.5 所有测试通过 mock `ClaudeAdapter` 或加载 fixture 完成，不进行真实 Claude 调用

## Dev Notes

### 关键设计决策

1. **LLM 只负责“更聪明地选”，不负责“放宽规则”。**
   当前 Python 里的依赖/状态约束仍是硬 guardrail。LLM 的职责是在“已确认可执行”的候选集合中，根据仓库真实状态与上下文做排序/剔除，而不是重新定义依赖是否满足。

2. **不要把本 story 误做成全量 async recommender 抽象重构。**
   当前 `BatchRecommender` Protocol 是同步的，且只服务于 `LocalBatchRecommender` 与现有测试。对 2B.5a 来说，最小且清晰的实现是在 `_batch_select_async()` 中显式分支：本地路径保持同步；LLM 路径新增 async 调用。若未来确实要统一为 async recommender 协议，另立 story 处理。

3. **必须消费 `ClaudeOutput.structured_output`，不要解析自由文本。**
   `ClaudeAdapter.execute()` 已支持 `json_schema`，`ClaudeOutput.from_json()` 也已把 `structured_output` 映射为独立字段。本 story 应直接复用这一合同，避免再引入脆弱的文本解析。

4. **Claude 的 `cwd` 必须显式对齐项目根。**
   2B.5a 的价值来自“看仓库现状”而不是只看 epics 文本。如果 `cwd` 没有绑定到项目根，Claude 可能看不到真实代码上下文，推荐将退化为更昂贵的本地排序器。

5. **失败策略是降级，不是中断。**
   操作者选择 `--llm` 是想获得更合理的推荐，而不是承担更多失败模式。只要本地推荐仍可工作，Claude 路径的任何失败都应自动回退。

### 当前代码接缝

- `src/ato/batch.py`
  - `load_epics()`：epics 解析入口
  - `LocalBatchRecommender.recommend()`：当前确定性推荐逻辑
  - `confirm_batch()`：事务性写入 batch，保持不动
- `src/ato/cli.py`
  - `batch_select()`：Typer 入口，需要新增 `--llm`
  - `_batch_select_async()`：当前唯一的推荐 dispatch 点，适合作为接线位置
  - `_derive_project_root()`：可复用的项目根解析 helper
- `src/ato/adapters/claude_cli.py`
  - `ClaudeAdapter.execute()`：已支持 `json_schema`、`cwd`、`max_turns`
- `src/ato/models/schemas.py`
  - `ClaudeOutput`：`structured_output` 已有稳定映射

### Scope Boundary

- **IN**
  - `ato batch select --llm` flag
  - `LLMBatchRecommender`
  - Claude structured output schema / prompt / fallback
  - 相关 fixture 与单元测试
- **OUT**
  - 修改 SQLite schema
  - 修改 `confirm_batch()` 事务语义
  - 修改 `ato batch status` 输出格式
  - 修改 generic agent dispatch/recovery 流程
  - 把 batch 推荐变成默认必须依赖 LLM 的功能

### Suggested Verification

```bash
uv run pytest tests/unit/test_batch.py tests/unit/test_cli_batch.py tests/unit/test_schemas.py
uv run ruff check src tests
uv run mypy src
```

## References

- [Source: _bmad-output/planning-artifacts/prd.md — FR12]
- [Source: _bmad-output/planning-artifacts/architecture.md — NFR11, Decision 9]
- [Source: _bmad-output/planning-artifacts/epics.md — Epic 2B, Story 2B.5]
- [Source: _bmad-output/planning-artifacts/sprint-change-proposal-2026-03-29.md]
- [Source: _bmad-output/implementation-artifacts/2b-5-batch-select-status.md]
- [Source: _bmad-output/implementation-artifacts/2b-1-claude-agent-dispatch.md]
- [Source: src/ato/batch.py — `BatchRecommender`, `LocalBatchRecommender`, `confirm_batch()`]
- [Source: src/ato/cli.py — `batch_select()`, `_batch_select_async()`, `_derive_project_root()`]
- [Source: src/ato/adapters/claude_cli.py — `ClaudeAdapter.execute()`]
- [Source: src/ato/models/schemas.py — `ClaudeOutput`]

## Previous Story Intelligence

1. **Story 2B.5 已经把“batch 创建/确认/状态查看”做成确定性流程。**
   2B.5a 只应改变“推荐 proposal 从哪里来”，不应回头重写 batch 持久化或状态聚合。

2. **Story 2B.1 已经定义了 Claude adapter 的 JSON + snapshot fixture 契约。**
   2B.5a 应站在这个合同之上新增一个 batch 推荐专用 structured output，而不是重新发明新的 Claude 调用模式。

3. **Decision 9 明确要求 adapter 变更必须伴随 fixture 更新。**
   本 story 若新增 Claude 推荐输出合同，必须同步补 fixture 与测试，否则未来 CLI 升级时很难发现推荐结构漂移。

## Change Log

- 2026-03-29: 基于 sprint change proposal 创建 Story 2B.5a 初稿
- 2026-03-29: `validate-create-story` 修订，补入缺失 story 文件，并明确同步/异步边界、candidate pool guardrail、project-root `cwd` 与 fallback 合同
- 2026-03-29: 实现完成，新增 BatchRecommendOutput schema、LLMBatchRecommender、--llm CLI flag、23 个测试
- 2026-03-29: Code review 修复 3 个 findings — (1) eligible pool 不再被 max_stories 截断; (2) 二次校验改为 fail-closed; (3) 所有 fallback 路径统一抛 LLMRecommendError 由 CLI 打印提示

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6 (1M context)

### Debug Log References

无阻塞问题，全流程一次通过。

### Completion Notes List

- 在 `schemas.py` 新增 `BatchRecommendOutput` 严格 Pydantic 模型和 `BATCH_RECOMMEND_JSON_SCHEMA` 常量
- 在 `batch.py` 新增 `build_llm_recommend_prompt()` 和 `LLMBatchRecommender` 类
- `LLMBatchRecommender` 先通过 `LocalBatchRecommender` 生成 eligible candidate pool，再调用 Claude 做语义排序/筛选
- Python 侧二次校验：过滤未知 key、重复 key、集合外 key；超量截断；空结果回退
- Claude 调用失败/超时/schema 不匹配时自动回退到本地推荐（降级不中断）
- CLI 层 `batch_select()` 新增 `--llm` flag，`--story-ids` 优先级最高不受影响
- 回退时仅向用户显示一条简洁提示，详细异常留给 structlog
- 新增 fixture `claude_batch_recommend.json` 和 23 个新测试（schema 6 + prompt 3 + LLM recommender 10 + CLI flag 4）
- 全部 1739 测试通过，0 回归，ruff + mypy 通过
- Code review 修复: eligible pool 传完整候选集（不截断）; 二次校验改为 fail-closed（任何异常 key 即整体无效）; 新增 `LLMRecommendError` 异常类，所有失败路径抛异常由 CLI 统一 catch 并输出回退提示

### File List

- src/ato/models/schemas.py (modified)
- src/ato/models/__init__.py (modified)
- src/ato/batch.py (modified)
- src/ato/cli.py (modified)
- tests/fixtures/claude_batch_recommend.json (new)
- tests/unit/test_batch.py (modified)
- tests/unit/test_cli_batch.py (modified)
- tests/unit/test_schemas.py (modified)
