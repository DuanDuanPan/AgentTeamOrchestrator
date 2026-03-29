# Story 验证报告：2B.5a LLM 智能 batch 推荐增强

验证时间：2026-03-29T14:18:10+0800  
Story 文件：`_bmad-output/implementation-artifacts/2b-5a-llm-batch-recommend.md`  
验证模式：`validate-create-story`  
结果：PASS（已补建 story 并应用修正）

## 摘要

`sprint-status.yaml` 已把 `2b-5a-llm-batch-recommend` 标为 `ready-for-dev`，但仓库中缺少对应 story markdown，实际交付面是不完整的。与此同时，`_bmad-output/planning-artifacts/sprint-change-proposal-2026-03-29.md` 已经指出“LLM 推荐增强”的方向正确，但仍有几个会直接误导 dev 的实现缺口：

1. 没有真正的 story 文件，`dev-story` 无法消费。
2. 没有明确同步 `BatchRecommender` 与异步 Claude 调用的边界，容易把这条 corrective story 扩大成无必要的抽象重构。
3. 没有把“LLM 只能在 deterministic eligible candidate pool 上工作”写成硬约束，存在幻觉 story key、绕过依赖检查、推荐不可执行 story 的风险。
4. 没有把 project-root `cwd` 与 fallback 行为写成明确合同，LLM 路径容易在错误目录运行，或在失败时把 CLI 直接打断。

本次验证已直接补建完整的 `2b-5a-llm-batch-recommend.md`，并把以上 4 个风险收敛为可执行、可测试、不会把实现范围做歪的 story。

## 已核查证据

- 当前代码：
  - `src/ato/batch.py`
  - `src/ato/cli.py`
  - `src/ato/adapters/claude_cli.py`
  - `src/ato/models/schemas.py`
- 当前测试：
  - `tests/unit/test_batch.py`
  - `tests/unit/test_cli_batch.py`
  - `tests/unit/test_claude_adapter.py`
- 规划与上下文：
  - `_bmad-output/planning-artifacts/prd.md`
  - `_bmad-output/planning-artifacts/epics.md`
  - `_bmad-output/planning-artifacts/architecture.md`
  - `_bmad-output/planning-artifacts/sprint-change-proposal-2026-03-29.md`
  - `_bmad-output/implementation-artifacts/2b-5-batch-select-status.md`
  - `_bmad-output/implementation-artifacts/sprint-status.yaml`

## 发现的关键问题

### 1. Story 被标记为 ready-for-dev，但实际 story 文件缺失

`sprint-status.yaml` 已记录：

- `# Story 2B.5a created (correct-course: LLM batch recommend): 2026-03-29`
- `2b-5a-llm-batch-recommend: ready-for-dev`

但 `_bmad-output/implementation-artifacts/` 下不存在对应 `.md` 文件。这会让后续 `dev-story`、代码评审和实现追踪都缺少主 artifact。

已应用修正：

- 直接补建 `_bmad-output/implementation-artifacts/2b-5a-llm-batch-recommend.md`
- 按当前仓库 story 基线补齐 AC、Tasks、Dev Notes、References、Previous Story Intelligence、Change Log

### 2. 原始 proposal 没有解决同步 `BatchRecommender` 与异步 Claude 路径的边界

当前现实代码是：

- `src/ato/batch.py::BatchRecommender` 是同步 Protocol
- `LocalBatchRecommender.recommend()` 也是同步
- `src/ato/cli.py::_batch_select_async()` 则是异步流程
- `ClaudeAdapter.execute()` 必然是异步

如果 story 不先约束边界，dev 很容易误把 2B.5a 做成“整个 recommender 抽象全面 async 化”的重构，顺手打破 `tests/unit/test_batch.py` 现有协议测试，或把 corrective story 范围扩大到并不必要的层面。

已应用修正：

- 在 story 中明确写出：2B.5a 不做通用 recommender 抽象重构
- 保持当前同步 `BatchRecommender` / `LocalBatchRecommender` 作为基线
- 在 `_batch_select_async()` 中显式增加 `use_llm` 分支接入 async Claude 路径

### 3. 原始 proposal 没把 deterministic guardrail 写死，LLM 很容易推荐出“看起来聪明但不可执行”的结果

当前本地推荐至少有两个硬保障：

- 只推荐依赖已满足的 story
- 只推荐当前状态可回退/可排队的 story

如果 LLM 直接面对全量 epics 而没有 Python 先收敛 candidate pool，那么它可能：

- 推荐集合外或幻觉的 story key
- 推荐依赖未满足的 story
- 推荐已经 `done`、`review`、`uat` 或其他不该再入 batch 的 story

已应用修正：

- AC2 收紧为“先由 Python 生成 eligible candidate pool，再让 LLM 在该集合内做排序/筛选”
- AC4 明确任何未知/重复/集合外/超量结果都视为无效并 fallback
- Task 2 明确需要把 LLM 返回的 key 再映射回 `EpicInfo` 做二次验证

### 4. 原始 proposal 没把项目根 `cwd` 与失败降级路径写成确定性合同

2B.5a 的价值来自“让 Claude 看仓库当前实现”，而不是只看 epics 文本。如果 `cwd` 不绑定项目根，Claude 推荐会退化为更昂贵的纯文档排序器。另一方面，如果 Claude CLI 出错就直接让 `ato batch select --llm` 失败，操作者体验会比 2B.5 更差。

已应用修正：

- AC3 明确 Claude 调用必须以项目根作为 `cwd`
- AC4 明确 Claude 失败、超时、schema 校验失败或有效结果为空时，都自动回退到 `LocalBatchRecommender`
- Task 3.5 要求用户只看到一条简洁 fallback 提示，详细异常走 `structlog`

## 已应用增强

- 补入了 `--story-ids` 仍优先于推荐逻辑的 guardrail，避免新 flag 破坏已有非交互路径
- 把 `confirm_batch()`、batch status 聚合和 SQLite schema 明确划出本 story 范围外，防止 dev 顺手改动不相关稳定链路
- 增加了 Decision 9 对 fixture 的引用，要求新 Claude 推荐合同必须伴随 fixture 与测试更新

## 剩余风险

- 即使做了 candidate pool 收敛，Claude 仍会引入额外 token/延迟成本；实现时应保持 prompt 紧凑，只发送必要的候选与状态摘要。
- 若未来团队确实想把所有 recommender 统一为 async 协议，应另立 story 单独处理，而不是在 2B.5a 中顺手完成。
- 本次仅完成 story 与 validation artifact，没有实现 Python 代码，也没有运行测试。

## 最终结论

2B.5a 现在已经从“只有 change proposal、没有实际 story 文件”的半成品状态，收敛为一个可直接交给 `dev-story` 的 corrective story。最重要的误导点已经被移除：不会再把实现做成抽象层大改造，不会让 LLM 绕过确定性依赖/状态 guardrail，也不会因为 Claude 失败而把 `ato batch select` 体验做差。
