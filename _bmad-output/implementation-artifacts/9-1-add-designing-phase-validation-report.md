# Story 验证报告：9.1 新增 Designing 阶段 — 可选的 UX 设计环节

验证时间：2026-03-28  
Story 文件：`_bmad-output/implementation-artifacts/9-1-add-designing-phase.md`  
验证模式：`validate-create-story`  
结果：PASS（已应用修正）

## 摘要

原始 9.1 草稿方向基本正确，但它把两个关键合同写偏了：一是凭空发明了 `_bmad-output/stories/` 设计树，二是把 design gate 写成了“transition handler 内检查 artifact”，这与当前仓库的事件提交路径不一致。

本次验证后，story 已收敛为一个可执行的 designing-phase 插入合同，核心修正有 4 项：

1. 将 UX 产出物路径收紧到当前仓库真实的 `implementation-artifacts` 体系，不再引入第二套 story 存储目录。
2. 将 design gate 的落点从模糊的“transition handler”改成真实的 success-event 提交路径，避免开发者去错误扩展 `state_machine.py`。
3. 将 main 串行控制从“也许改 `SubprocessManager` semaphore”收紧为共享 dispatch limiter，避免每次新建 manager 时串行保证失效。
4. 补回了 Scope Boundary、Previous Story Intelligence、Dev Agent Record 等 create-story 基线结构。

## 已核查证据

- 规划与规范工件：
  - `_bmad-output/planning-artifacts/prd.md`
  - `_bmad-output/planning-artifacts/architecture.md`
  - `_bmad-output/implementation-artifacts/sprint-status.yaml`
- 前序相关 story：
  - `_bmad-output/implementation-artifacts/8-2-add-planning-phase.md`
  - `_bmad-output/implementation-artifacts/2b-4-worktree-isolation.md`
- 当前代码：
  - `src/ato/state_machine.py`
  - `src/ato/transition_queue.py`
  - `src/ato/recovery.py`
  - `src/ato/core.py`
  - `ato.yaml.example`

## 发现的关键问题

### 1. 原稿发明了 `_bmad-output/stories/` 目录，与当前 story 真源冲突

当前仓库事实是：

- `sprint-status.yaml` 的 `story_location` 已固定到 `_bmad-output/implementation-artifacts`
- create-story / validate-create-story / sprint-status 的所有既有产物都在这个目录里

原稿把 design artifact 与 story spec 放到 `_bmad-output/stories/{story-id}/...`，会直接把开发者引到一条与现有 BMAD 工作流分叉的新路径。

已应用修正：

- 将 story spec 保持在 `_bmad-output/implementation-artifacts/{story_id}.md`
- 将 UX 设计产出收紧为同目录下的 `{story_id}-ux/`

### 2. 原稿把 design gate 写成了 transition handler 逻辑，实际没有上下文

当前代码里：

- `TransitionQueue` 只消费已经提交好的 `TransitionEvent`
- `state_machine.py` 自身不知道 task_id、artifact 路径或 approval helper
- success 事件是在 recovery / dispatch 路径上提交，不是在状态机内部生成

如果按原稿去“在 designing transition handler 中验证 artifact”，开发者会大概率改错层次。

已应用修正：

- 将 gate 落点改成“structured_job 成功后、提交 `design_done` 前的 success-event 路径”
- 明确不要把 gate 写进 `state_machine.py`

### 3. 原稿对 main 串行控制的实现位置不够严格，容易写成无效 semaphore

当前仓库的 core / recovery 路径都会按需新建 `SubprocessManager`。这意味着：

- 某个 `SubprocessManager` 自己的 semaphore 只约束它自己
- 不能提供跨 manager、跨 recovery/core 路径的全局串行保证

已应用修正：

- 将 AC 与 Task 收紧为共享 dispatch limiter
- 明确写出“不要把它只放在某个临时 `SubprocessManager` 实例里”

### 4. 原稿缺少当前仓库 create-story 基线结构

与仓库中近期已验证的 story 相比，原稿缺少：

- Previous Story Intelligence
- Dev Agent Record
- 更明确的 Scope Boundary

已应用修正：

- 补回上述结构，避免后续 dev-story 缺少统一的 traceability 骨架

## 已应用增强

- 增加了与 Story 8.2、Story 2B.4 的上下文联动
- 将 testing 面扩展到 replay、recovery、gate 两类新增风险点
- 收紧了 9.1 与 9.2 / 9.3 的边界，避免三份 story 互相重叠

## 剩余风险

- `_bmad-output/planning-artifacts/epics.md` 里目前仍没有正式的 Epic 9 分解；本次验证主要依赖当前 story 草稿、PRD、architecture、sprint-status 与现有代码交叉校正。
- 本次只修订了 story 与 validation report，没有实现 Python 代码，也没有运行测试。

## 最终结论

修正后，9.1 已从“方向对，但路径与落点会把开发者带偏”的草稿，收敛成了一个可直接交给 dev-story 的 phase-insertion story。最危险的误导点已经移除：不会再去新建第二套 story 目录，也不会再把 design gate 写进缺乏上下文的状态机层。
