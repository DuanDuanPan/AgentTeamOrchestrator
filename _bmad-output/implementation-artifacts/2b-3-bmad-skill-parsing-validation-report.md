# Story 验证报告：2B.3 BMAD Skill Parsing

验证时间：2026-03-25 08:07:01 CST
Story 文件：`_bmad-output/implementation-artifacts/2b-3-bmad-skill-parsing.md`
验证模式：`validate-create-story`
结果：PASS（已应用修正）

## 摘要

该 story 原文存在若干会直接误导实现的高风险缺口：

1. `BmadAdapter.parse(markdown_output, skill_type)` 的契约无法支撑 AC3 中的 approval 创建，因为缺少 `story_id`、DB / helper 边界和 notifier 上下文。
2. 它把 BMAD 解析收窄成“regex + 少量固定标题”的问题，但 architecture 明确要求对不可修改的 BMAD 输出保持鲁棒，且当前仓库里的真实输出形态远比 story 原文假设的更复杂。
3. 它用 `location` 代替 `file_path`，并给出了与 Story 3.1 不一致的 dedup hash 正则化规则，这会破坏后续 Convergent Loop 的 finding 匹配键。
4. 它要求用 20 个 fixture 守护解析率，却让 fixture 建立在自造的简化格式上，而不是当前仓库里真实存在的 BMAD / TEA artifact 结构。
5. 它没有区分同进程 `Nudge.notify()` 和跨进程 `send_external_nudge()`，会让“解析失败通知 Orchestrator”的实现边界变得模糊。

## 已核查证据

- 本地仓库工件：
  - `_bmad-output/planning-artifacts/epics.md`
  - `_bmad-output/planning-artifacts/architecture.md`
  - `_bmad-output/planning-artifacts/prd.md`
  - `_bmad-output/implementation-artifacts/2b-2-codex-agent-review-validation-report.md`
  - `_bmad/bmm/workflows/4-implementation/bmad-code-review/steps/step-04-present.md`
  - `_bmad/bmm/workflows/3-solutioning/bmad-create-architecture/steps/step-07-validation.md`
  - `_bmad/tea/workflows/testarch/bmad-testarch-test-review/test-review-template.md`
- 当前代码：
  - `src/ato/adapters/bmad_adapter.py`
  - `src/ato/adapters/claude_cli.py`
  - `src/ato/models/schemas.py`
  - `src/ato/models/db.py`
  - `src/ato/nudge.py`
- 当前测试/目录现状：
  - `tests/fixtures/`
  - `tests/unit/test_schemas.py`
  - `schemas/.gitkeep`

## 发现的关键问题

### 1. 解析契约无法落地 approval / nudge 路径

story 原文把失败处理写进 AC3，但 `parse(markdown_output, skill_type)` 本身没有 `story_id`、数据库连接、notifier 或 helper 边界，无法合法完成：

- `approvals.story_id` 外键写入
- 同进程 / 跨进程的通知差异
- 失败上下文的持久化

已应用修正：
- 将 `parse()` 调整为返回 `BmadParseResult`
- 新增 `record_parse_failure(...)` / caller helper 边界
- 明确 approval / nudge 属于 orchestration concern，而非纯 parser core

### 2. regex-only 指导与 architecture 和真实 skill 输出冲突

architecture 已明确写出“BMAD skills 不可修改，因此适配层要对 Markdown 输出做鲁棒解析”。但原 story 假设的四类输出都是统一的 verdict + findings 列表，这与当前仓库中的真实结构不符：

- code-review 是 `Intent Gaps` / `Bad Spec` / `Patch` / `Defer`
- story-validation 是“验证报告”文档
- architecture validation 是 `Overall Status` / `Key Strengths`
- QA / test-review 是 `Recommendation` / `Critical Issues` / `Recommendations`

已应用修正：
- 改成“两阶段解析”设计：deterministic fast-path + semantic fallback
- fixture 来源改成当前仓库真实模板/样例，而不是自造标题

### 3. finding canonical model 缺少 `file_path`，dedup hash 规则也漂移

architecture / Story 3.1 的匹配键明确是 `file_path + rule_id + severity`。原 story 的 `BmadFinding` 只有 `location`，且 dedup hash 示例还额外去除了标点，这会让后续 matching 规则与 3.1 不一致。

已应用修正：
- `BmadFinding` 改为显式字段：`file_path`, `line`, `rule_id`, `raw_location`
- dedup hash 改为与 Story 3.1 对齐：空白压缩 + strip + lower

### 4. fixture 指导会制造“假绿”测试

原 story 要求 20 个样本，但样本结构取自 story 自己臆造的 headings。这样即使测试全绿，也无法证明 parser 能吃下真实 BMAD / TEA 输出。

已应用修正：
- fixture 改为基于当前仓库真实 artifact/template 结构
- 明确四大输出族群都要至少 5 个样本
- 加入 malformed / partial 样本，验证 semantic fallback 与 graceful degrade

### 5. 通知边界没有区分同进程和跨进程

当前代码库已经有：

- `Nudge.notify()`：同进程唤醒
- `send_external_nudge(pid)`：外部进程发信号

原 story 只说“通过 nudge 通知 Orchestrator”，但没说明选择规则。

已应用修正：
- helper 接受注入 notifier
- Dev Notes 明确：
  - 同进程优先 `Nudge.notify()`
  - 外部进程才使用 `send_external_nudge()`

## 已应用增强

- 把 semantic fallback 设计成依赖注入接口，避免 `bmad_adapter.py` 直接耦合 `SubprocessManager`
- 明确 `tests/unit/test_schemas.py` 也要覆盖新模型和 dedup hash
- 明确 `schemas/` 和 `findings` 表仍属于 Story 3.1，不把范围扩张到未来 story
- 增加了 `parser_mode`, `raw_output_preview`, `parse_error` 等字段，便于后续审查和人工兜底

## 剩余风险

- `convergent_loop.py` 目前仍是占位文件，因此本 story 只定义了解析契约和 failure helper 边界，没有补齐最终调用链集成。这是合理收边，不应在 2B.3 中硬塞完整质量门控实现。
- semantic fallback 若最终复用 Claude/Codex CLI，会带来额外成本追踪问题。当前 story 通过“注入 parser runner”保留边界，但没有把 cost_log 集成拉进本 story 范围。

## 最终结论

修正后，这个 story 已从“会把开发者带向错误 parser 设计”的状态，收敛为可执行的实现说明。它现在与当前仓库中的真实 artifact 结构、approval / nudge 边界以及后续 finding 匹配规则保持一致，适合继续维持 `ready-for-dev`。
