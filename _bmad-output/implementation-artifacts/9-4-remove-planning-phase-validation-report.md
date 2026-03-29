# Story 验证报告：9.4 移除冗余 Planning 阶段

验证时间：2026-03-29  
Story 文件：`_bmad-output/implementation-artifacts/9-4-remove-planning-phase.md`  
验证模式：`validate-create-story`  
结果：PASS（已补建缺失 story 文件并应用修正）

## 摘要

原始 9.4 并不是“质量欠佳的草稿”，而是更基础的失败态：`sprint-status.yaml` 顶部注释已经写明 “Story 9-4 created”，但对应的 story artifact 根本不存在，状态条目也仍停在 `backlog`。在这种状态下，`dev-story` 和后续 sprint 跟踪都没有可消费的规格文件。

本次验证先补建了缺失的 story 文件，再把内容收敛到当前仓库真实基线。核心修正有 3 项：

1. 将 “缺失 artifact + sprint-status 不一致” 修正为一个正式的 `ready-for-dev` story。
2. 将实现影响面从“删掉 `state_machine.py` 里的 planning”扩展到 recovery、batch init、queue replay、config allowlist、CLI/TUI 顺序与 `test_initial_dispatch.py`。
3. 明确这是 phase contract rollback，而不是 status/schema 重构：高层 `"planning"` 状态保留，不做 DB migration。

## 已核查证据

- 规划与变更工件：
  - `_bmad-output/planning-artifacts/epics.md`
  - `_bmad-output/planning-artifacts/sprint-change-proposal-2026-03-29-remove-planning-phase.md`
  - `_bmad-output/planning-artifacts/prd.md`
  - `_bmad-output/implementation-artifacts/sprint-status.yaml`
- 前序相关 story：
  - `_bmad-output/implementation-artifacts/8-2-add-planning-phase.md`
  - `_bmad-output/implementation-artifacts/9-1e-creating-phase-prompt-validation-feedback.md`
  - `_bmad-output/implementation-artifacts/9-2-workspace-concept-worktree-timing.md`
  - `_bmad-output/implementation-artifacts/9-3-conditional-skip-spec-commit.md`
- 当前代码与测试：
  - `src/ato/state_machine.py`
  - `src/ato/recovery.py`
  - `src/ato/transition_queue.py`
  - `src/ato/batch.py`
  - `src/ato/config.py`
  - `src/ato/cli.py`
  - `src/ato/models/db.py`
  - `ato.yaml.example`
  - `tests/unit/test_initial_dispatch.py`

## 发现的关键问题

### 1. 9.4 实际上没有 story 文件，导致 “已创建” 只是账面状态

当前仓库事实是：

- `sprint-status.yaml` 注释已写明 “Story 9-4 created”
- `development_status` 中 `9-4-remove-planning-phase` 仍是 `backlog`
- `_bmad-output/implementation-artifacts/` 下不存在 `9-4-remove-planning-phase.md`

这会直接导致：

- `dev-story` 没有 story artifact 可读
- sprint tracking 与真实产物脱节
- validate-create-story 本身没有目标文件可验证

已应用修正：

- 补建 `_bmad-output/implementation-artifacts/9-4-remove-planning-phase.md`
- 将 `sprint-status.yaml` 中 `9-4-remove-planning-phase` 调整为 `ready-for-dev`

### 2. 仅依据 change proposal 很容易低估真实改动面

提案已经指出 state machine、recovery、batch、transition_queue、config、`ato.yaml.example` 和测试要改，但如果 story 不把运行面写实，dev 仍很容易漏掉：

- `tests/unit/test_initial_dispatch.py` 中首阶段默认仍是 `planning/planner`
- `src/ato/config.py` 的 main-phase allowlist 仍包含 `planning`
- CLI/TUI 侧任何把 `planning` 当真实 canonical phase 的断言或图标消费

已应用修正：

- Story 的 AC / Tasks / Project Structure Notes 已显式纳入这些路径
- Suggested Verification 也对准了这些最容易漏掉的测试子集

### 3. 这个 corrective story 最容易出现“过度修复”

如果故事写得不够清楚，开发者很可能会做两种错误扩张：

- 把高层 `"planning"` 状态一起删掉，连带破坏 `creating/designing/validating` 的聚合语义
- 顺手做 DB migration 或 schema 改造，把 phase rollback 误做成数据模型重构

而当前正确合同是：

- 删除真实 `planning` phase
- 保留高层 planning status
- 不修改 `StoryStatus` literal
- 不做 DB migration

已应用修正：

- AC1 明确要求 `PHASE_TO_STATUS` 仅删除 `planning` phase 条目，不删除 `"planning"` status
- Dev Notes / Scope Boundary 明确写出 “无 schema migration”

## 已应用增强

- 将 9.4 与 8.2、9.1e、9.2、9.3 的依赖关系写清，避免 dev 脱离上下文做局部删除
- 补回 create-story 基线结构：Scope Boundary、Suggested Verification、Previous Story Intelligence、Change Log、Dev Agent Record
- 将 story 聚焦为 “phase rollback + surface alignment”，而不是泛泛的 “remove planning everywhere”

## 剩余风险

- 当前代码工作区已在相关文件上有未提交改动；本次验证基于现有源文件和已批准的 sprint change proposal 重建 story，但没有介入实现代码本身。
- 本次只修订了 story artifact 与 sprint-status，没有运行测试，也没有验证实现代码是否已完成。

## 最终结论

修正后，9.4 已从“sprint-status 声称已创建、但实际上没有 story artifact 的空洞条目”，收敛为一个可直接交给 `dev-story` 的 corrective story。最关键的误导已经解除：开发者不会把工作理解成单点删除 `planning`，也不会误删高层 planning status 或错误引入 schema 迁移。
