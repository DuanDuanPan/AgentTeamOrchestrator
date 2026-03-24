---
stepsCompleted:
  - 'step-01-document-discovery'
  - 'step-02-prd-analysis'
  - 'step-02b-prd-clarity-gaps'
  - 'step-03-epic-coverage-validation'
  - 'step-03b-architecture-analysis'
  - 'step-04-ux-alignment'
  - 'step-05-epic-quality-review'
  - 'step-05b-cross-document-consistency'
  - 'step-06-final-assessment'
includedFiles:
  prd: '_bmad-output/planning-artifacts/prd.md'
  architecture: '_bmad-output/planning-artifacts/architecture.md'
  epics: '_bmad-output/planning-artifacts/epics.md'
  ux: '_bmad-output/planning-artifacts/ux-design-specification.md'
documentInventory:
  prd:
    whole:
      - '_bmad-output/planning-artifacts/prd.md'
    sharded: []
  architecture:
    whole:
      - '_bmad-output/planning-artifacts/architecture.md'
    sharded: []
  epics:
    whole:
      - '_bmad-output/planning-artifacts/epics.md'
    sharded: []
  ux:
    whole:
      - '_bmad-output/planning-artifacts/ux-design-specification.md'
    sharded: []
---

# Implementation Readiness Assessment Report

**Date:** 2026-03-24
**Project:** AgentTeamOrchestrator

## Document Discovery

### PRD Files Found

**Whole Documents:**
- `_bmad-output/planning-artifacts/prd.md` (37919 bytes, 2026-03-24 09:49:55)

**Sharded Documents:**
- None

### Architecture Files Found

**Whole Documents:**
- `_bmad-output/planning-artifacts/architecture.md` (44822 bytes, 2026-03-24 10:28:38)

**Sharded Documents:**
- None

### Epics & Stories Files Found

**Whole Documents:**
- `_bmad-output/planning-artifacts/epics.md` (61667 bytes, 2026-03-24 11:15:41)

**Sharded Documents:**
- None

### UX Design Files Found

**Whole Documents:**
- `_bmad-output/planning-artifacts/ux-design-specification.md` (71836 bytes, 2026-03-24 10:42:40)

**Sharded Documents:**
- None

### Discovery Assessment

- No duplicate whole/sharded document sets were found.
- No required core planning artifacts were missing.
- Assessment will use the single whole-document versions of PRD, Architecture, Epics, and UX specification.

## PRD Analysis

### Functional Requirements

### 工作流编排

- **FR1:** 系统可从声明式配置文件（YAML）动态构建工作流状态机，定义角色、阶段、转换规则
- **FR2:** 系统可验证配置文件的正确性，拒绝无效的工作流定义（如循环依赖、缺失阶段）
- **FR3:** 系统可按配置的阶段顺序自动推进 story 的生命周期（creating → validating → ... → done）
- **FR4:** 系统可保证并发完成的任务不会导致状态冲突
- **FR5:** 操作者可通过 `ato plan <story-id>` 预览某个 story 将经历的完整阶段序列

### AI Agent 协作

- **FR6:** 系统可通过 CLI subprocess 调用 Claude Code 执行创建、实现、修复等任务，并收集 JSON 格式的结构化输出
- **FR7:** 系统可通过 CLI subprocess 调用 Codex CLI 执行审查、验证等任务，并从 JSONL 事件流和 `-o` 输出文件收集结果
- **FR8:** 系统可根据配置为不同角色指定 CLI 类型、沙箱级别和模型选择
- **FR9:** 系统可管理 agent session（创建、续接、fork），支持 Convergent Loop 内短循环的 session resume
- **FR53:** 系统可从 task 输出中提取结构化工作记忆摘要（Context Briefing），作为跨 task 边界的 fresh session 输入
- **FR10:** 系统可启动 Interactive Session（独立终端窗口），注册其 PID、worktree 路径和启动时间，人与 agent 直接在终端中协作
- **FR11:** 系统可将 BMAD skill 的 Markdown 输出通过适配层解析为结构化 JSON（覆盖 code-review、story-validation、architecture-review、QA-report 四个 skill）
- **FR12:** PM agent 可分析 epic/story 的优先级和依赖关系，生成推荐的 batch 方案供操作者选择

### 质量门控

- **FR13:** 系统可执行 Convergent Loop 协议：review → finding 入库 → fix → re-review（scope 收窄）→ 收敛判定或 escalate
- **FR14:** 系统可在 SQLite 中追踪每个 finding 的跨轮次状态（open → closed / still_open / new）
- **FR15:** 系统可在每轮 re-review 时自动收窄 scope，仅验证上轮 open findings 的闭合状态和新引入问题
- **FR16:** 系统可执行 deterministic validation check（JSON Schema 结构验证），作为 agent review 之前的第一层验证
- **FR17:** 系统可在 Convergent Loop 达到 max_rounds 后自动 escalate（MVP: 通知人工；Growth: 梯度降级）
- **FR18:** 系统可按配置的 severity 判定规则（blocking vs suggestion）分类 findings，blocking 数量超阈值时请求人工确认

### 人机协作

- **FR19:** 操作者可在审批队列中查看所有待决策事项（batch 选择、merge 授权、超时处理、异常 escalation、UAT 结果）
- **FR20:** 操作者可对每个待审批事项做出决策，决策记录持久化（含时间戳和选择理由）
- **FR21:** 操作者可在 UAT 阶段通过 TUI 提交测试结果（通过/不通过 + 描述）
- **FR22:** 系统可在需要紧急人工介入时发出 terminal bell 通知（如 regression 失败冻结 merge queue）
- **FR23:** 操作者可选择 Interactive Session 的恢复策略（重新启动 / 从上次 session 续接 / 放弃）

### 状态管理与恢复

- **FR24:** 系统可将所有运行时状态（stories、tasks、findings、approvals、cost_log）持久化到 SQLite（WAL 模式）
- **FR25:** 系统可在进程崩溃后自动恢复：扫描 running 状态的 task，根据 PID 存活和 artifact 存在情况分类处理（自动续接 / 重新调度 / 请求人工决策）
- **FR26:** 系统可在恢复后向操作者展示恢复摘要（自动恢复数量 + 需人工决策数量）
- **FR27:** 系统可记录每次 agent 调用的结构化数据（耗时、成本、token 用量、收敛轮次），用于基线数据收集
- **FR28:** 系统可记录每次 agent 调用的成本（Claude: 直接读取 total_cost_usd；Codex: 从 token 数计算）

### 工作空间管理

- **FR29:** 系统可为每个 story 创建独立的 git worktree，在 worktree 中执行 agent 任务
- **FR30:** 系统可在 story 完成后清理 worktree
- **FR31:** 系统可管理 merge queue，按顺序执行 rebase 和 merge（需人类授权）
- **FR32:** 系统可在 regression 失败时自动冻结 merge queue，阻止后续 merge
- **FR52:** 系统可在 worktree rebase 产生冲突时调度 agent 自动解决，解决后重新进入 review 流程；解决失败 escalate 给操作者

### 配置与初始化

- **FR33:** 操作者可通过 `ato init` 初始化项目（创建 SQLite 数据库、检测 CLI 安装和认证状态）
- **FR34:** 系统可检测 Claude CLI 和 Codex CLI 的安装状态和认证有效性，报告环境就绪情况
- **FR35:** 操作者可通过配置文件设置系统参数（并发上限、超时阈值、成本上限、Convergent Loop 参数等）

### 可视化与监控

- **FR36:** 操作者可通过 TUI 查看所有 story 的当前状态、所在阶段及 Convergent Loop 进度信息（当前轮次、open findings 数量）
- **FR37:** 操作者可通过 TUI 与审批队列交互（查看详情、做出决策）
- **FR38:** 操作者可通过 `ato batch status` 查看当前 batch 的整体进度
- **FR39:** 操作者可通过 `ato start` 启动编排系统（含自动崩溃恢复）
- **FR40:** 操作者可通过 `ato stop` 优雅停止编排系统

### 质量闭环与审计

- **FR48:** 系统可在 UAT 不通过时自动将 story 退回到 fix 阶段，重新进入 Convergent Loop
- **FR49:** 操作者可查看任意 story 的完整执行历史（哪个 agent 在什么时间执行了什么任务，产出了什么 artifact）
- **FR50:** 系统可向操作者展示任务失败的原因（认证过期、超时、解析错误等）和可选的恢复操作（重试、跳过、escalate）
- **FR51:** 配置变更需重启系统生效（MVP）

### Growth 阶段能力（Phase 2）

- **FR41:** 系统可同时编排多个项目的流水线，每个项目独立的状态存储（Growth P2: 并行+多项目）
- **FR42:** 系统可执行梯度降级：Claude fix 未收敛 → Codex 攻坚 → Interactive Session（Growth P1: 梯度降级）
- **FR43:** 系统可从历史运行数据中提取模式，自动调整系统参数（Growth P3: Memory 层）
- **FR44:** 操作者可手动添加 finding 并标注分类，供 Memory 层消费（Growth P4: 手动 finding）
- **FR45:** 操作者可在 TUI 中 override 任意 finding 的 severity（Growth P4: severity override）
- **FR46:** 系统可在 review prompt 中自动注入来自 Memory 的历史检查提示（Growth P3: prompt 强化）
- **FR47:** 操作者可通过 TUI 查看成本面板、UAT 趋势图和 finding 详情（Growth P4: TUI 增强）

**Total FRs:** 53

### Non-Functional Requirements

### Performance

- **NFR1:** 崩溃恢复（SQLite 扫描 + PID/artifact 检查 + 恢复决策）在 MVP 阶段 ≤30 秒，成熟期 ≤10 秒
- **NFR2:** 状态转换处理（从 agent 完成到下一阶段 agent 启动）≤5 秒
- **NFR3:** TUI 状态刷新间隔 ≤5 秒，单次刷新渲染 ≤500ms
- **NFR4:** Deterministic validation check（JSON Schema 验证）≤1 秒
- **NFR5:** 配置解析与状态机构建（`ato start` 启动时间）≤3 秒

### Reliability

- **NFR6:** SQLite WAL 模式保证进程崩溃后数据零丢失
- **NFR7:** 系统重启后可自动恢复所有可恢复的 task（有 artifact 或 PID 存活的），无需人工重建状态
- **NFR8:** 单次 agent CLI 调用失败时，系统自动重试 1 次后再 escalate
- **NFR9:** Convergent Loop 在任何情况下 ≤max_rounds 轮后终止（不会无限循环）
- **NFR10:** Merge queue 冻结后，系统保证不会在 broken main 上继续 merge

### Integration

- **NFR11:** Claude CLI adapter 和 Codex CLI adapter 通过隔离层封装，CLI 版本升级只影响 adapter 层，不影响编排核心
- **NFR12:** BMAD 适配层基于 LLM 语义解析，对 BMAD skill 输出格式的小幅变化具有鲁棒性
- **NFR13:** 系统兼容 macOS 和 Linux 环境下的 git worktree 操作
- **NFR14:** 系统正确处理 CLI 的各类退出码和错误输出（认证过期、rate limit、超时等），分类到对应的恢复策略

**Total NFRs:** 14

### Additional Requirements

- 项目上下文存在明确边界条件：BMAD 不可改、CLI subprocess only、无 API Key 须用默认认证、双 CLI 能力差异。
- 系统被定义为本地单进程 Python 应用，提供 CLI 与 TUI 两种入口，并以声明式工作流配置驱动状态机构建。
- 核心持久化与恢复依赖 SQLite WAL，工作执行隔离依赖 git worktree，说明实现层已经隐含了明确的平台和基础设施要求。
- 系统核心支持 headless 运行、JSON 输出和事件钩子，说明后续需要保留自动化集成和脚本化扩展接口。
- 运行时基础约束明确要求 Python ≥3.11，核心依赖包含 `aiosqlite`、`python-statemachine >=3.0`、`Textual >=2.0`、`Pydantic >=2.0`、`typer`。
- MVP 范围强调单项目端到端可行性，Growth 范围引入多项目并行、Memory、自适应优化和 richer TUI，说明需求中同时混合了 MVP 与后续阶段目标。

### PRD Completeness Assessment

- PRD 在产品定位、用户旅程、范围划分、成功指标、FR/NFR 和技术约束上都相当完整，足以支撑后续 traceability 检查。
- FR/NFR 粒度总体可实施，且已经把 MVP 与 Growth 做了分层，便于后续核对 epic 是否覆盖当前阶段目标。
- 仍存在两个轻微的可追踪性问题：一是 FR 编号在文档中的出现顺序并非严格递增；二是部分 Growth 能力与 MVP 能力被放在同一主需求清单中，后续做 epic 覆盖映射时必须显式标记 phase，避免把后续能力误判为 MVP 漏项。

### PRD Implementation Clarity Gaps

以下 FR 虽然已编号且可追踪，但在设计细节上存在模糊性，直接进入实现会导致歧义：

1. **FR53 Context Briefing 提取机制未定义**
   - 描述为"从 task 输出中提取结构化工作记忆摘要"，但未说明提取方式（LLM 语义解析 vs. 结构化 schema 匹配），也未定义 Briefing 的字段结构。
   - Impact: 跨 task 边界的 session 输入格式不确定，影响 adapter 层和 prompt 组装逻辑。

2. **FR52 Rebase 冲突解决失败的 escalation 路径不完整**
   - "解决失败 escalate 给操作者"但未定义 escalate 的具体行为：是回滚 rebase？保留冲突态 worktree？还是创建 approval 等待人工解决？
   - Impact: worktree 生命周期管理在异常路径上存在歧义。

3. **FR16 Deterministic Validation 缺少 Schema 演化策略**
   - JSON Schema 被标记为 "immutable"，但 BMAD skill 输出格式可能随版本变化。未说明 schema 版本管理和向后兼容策略。
   - Impact: BMAD 版本升级时可能导致 validation 层全面失败。

4. **FR10 Interactive Session 的会话恢复机制未规范**
   - 超时阈值硬编码为 7200s，但 "尽力而为" 的 session resume 依赖 Claude CLI 未公开文档化的行为。
   - Impact: 会话恢复的可靠性无法在实现前验证。

5. **Regression 测试触发机制缺失**
   - Story 生命周期包含 "regression" 阶段，但无 FR 指定测试套件管理、触发时机和结果判定规则。
   - Impact: merge queue 的 regression 冻结（FR32）依赖于未定义的测试执行能力。

6. **Batch 推荐方案的 PM Agent 权限边界未定义（FR12）**
   - PM agent "分析优先级和依赖关系，生成推荐的 batch 方案"，但未说明 PM agent 分析失败、分析结果与操作者意图冲突时的处理方式。
   - Impact: PM agent 的自主权和回退策略需要在实现前明确。

### PRD Testing Strategy Gap

PRD 成功指标中要求 "100% 状态机转换覆盖"，但缺乏以下关键测试维度的规范：

- **集成测试**：CLI adapter 对真实 Claude/Codex 输出的端到端验证未提及。
- **Convergent Loop 正确性**：收敛假设（"≥80% 在 3 轮内收敛"）缺乏形式化证明或基于真实数据的验证计划。
- **Worktree 冲突场景**：多 story 并发 rebase 的异常路径测试未规划。
- **BMAD 解析鲁棒性**：BMAD skill 输出格式变化时的 adapter 降级测试未定义。

## Epic Coverage Validation

### Coverage Matrix

| FR Number | PRD Requirement | Epic Coverage | Status |
| --------- | --------------- | ------------- | ------ |
| FR1 | 系统可从声明式配置文件（YAML）动态构建工作流状态机，定义角色、阶段、转换规则 | Epic 1 (声明式配置→状态机构建) | ✓ Covered |
| FR2 | 系统可验证配置文件的正确性，拒绝无效的工作流定义（如循环依赖、缺失阶段） | Epic 1 (配置验证) | ✓ Covered |
| FR3 | 系统可按配置的阶段顺序自动推进 story 的生命周期（creating → validating → ... → done） | Epic 2 (自动推进 story 生命周期) | ✓ Covered |
| FR4 | 系统可保证并发完成的任务不会导致状态冲突 | Epic 2 (并发任务状态不冲突) | ✓ Covered |
| FR5 | 操作者可通过 `ato plan <story-id>` 预览某个 story 将经历的完整阶段序列 | Epic 1 (ato plan 预览阶段序列) | ✓ Covered |
| FR6 | 系统可通过 CLI subprocess 调用 Claude Code 执行创建、实现、修复等任务，并收集 JSON 格式的结构化输出 | Epic 2 (Claude CLI subprocess 调用) | ✓ Covered |
| FR7 | 系统可通过 CLI subprocess 调用 Codex CLI 执行审查、验证等任务，并从 JSONL 事件流和 `-o` 输出文件收集结果 | Epic 2 (Codex CLI subprocess 调用) | ✓ Covered |
| FR8 | 系统可根据配置为不同角色指定 CLI 类型、沙箱级别和模型选择 | Epic 2 (角色→CLI/沙箱/模型映射) | ✓ Covered |
| FR9 | 系统可管理 agent session（创建、续接、fork），支持 Convergent Loop 内短循环的 session resume | Epic 2 (Agent session 管理) | ✓ Covered |
| FR10 | 系统可启动 Interactive Session（独立终端窗口），注册其 PID、worktree 路径和启动时间，人与 agent 直接在终端中协作 | Epic 2 (Interactive Session 启动与注册) | ✓ Covered |
| FR11 | 系统可将 BMAD skill 的 Markdown 输出通过适配层解析为结构化 JSON（覆盖 code-review、story-validation、architecture-review、QA-report 四个 skill） | Epic 2 (BMAD Markdown→JSON 适配层) | ✓ Covered |
| FR12 | PM agent 可分析 epic/story 的优先级和依赖关系，生成推荐的 batch 方案供操作者选择 | Epic 2 (PM agent batch 推荐) | ✓ Covered |
| FR13 | 系统可执行 Convergent Loop 协议：review → finding 入库 → fix → re-review（scope 收窄）→ 收敛判定或 escalate | Epic 3 (Convergent Loop 协议执行) | ✓ Covered |
| FR14 | 系统可在 SQLite 中追踪每个 finding 的跨轮次状态（open → closed / still_open / new） | Epic 3 (Finding 跨轮次状态追踪) | ✓ Covered |
| FR15 | 系统可在每轮 re-review 时自动收窄 scope，仅验证上轮 open findings 的闭合状态和新引入问题 | Epic 3 (Re-review scope 收窄) | ✓ Covered |
| FR16 | 系统可执行 deterministic validation check（JSON Schema 结构验证），作为 agent review 之前的第一层验证 | Epic 3 (Deterministic validation (JSON Schema)) | ✓ Covered |
| FR17 | 系统可在 Convergent Loop 达到 max_rounds 后自动 escalate（MVP: 通知人工；Growth: 梯度降级） | Epic 3 (Max_rounds escalate) | ✓ Covered |
| FR18 | 系统可按配置的 severity 判定规则（blocking vs suggestion）分类 findings，blocking 数量超阈值时请求人工确认 | Epic 3 (Severity 分类 + blocking 阈值) | ✓ Covered |
| FR19 | 操作者可在审批队列中查看所有待决策事项（batch 选择、merge 授权、超时处理、异常 escalation、UAT 结果） | Epic 4 (审批队列展示) | ✓ Covered |
| FR20 | 操作者可对每个待审批事项做出决策，决策记录持久化（含时间戳和选择理由） | Epic 4 (审批决策持久化) | ✓ Covered |
| FR21 | 操作者可在 UAT 阶段通过 TUI 提交测试结果（通过/不通过 + 描述） | Epic 4 (UAT 结果提交) | ✓ Covered |
| FR22 | 系统可在需要紧急人工介入时发出 terminal bell 通知（如 regression 失败冻结 merge queue） | Epic 4 (Terminal bell 紧急通知) | ✓ Covered |
| FR23 | 操作者可选择 Interactive Session 的恢复策略（重新启动 / 从上次 session 续接 / 放弃） | Epic 4 (Interactive Session 恢复策略) | ✓ Covered |
| FR24 | 系统可将所有运行时状态（stories、tasks、findings、approvals、cost_log）持久化到 SQLite（WAL 模式） | Epic 1 (SQLite WAL 持久化) | ✓ Covered |
| FR25 | 系统可在进程崩溃后自动恢复：扫描 running 状态的 task，根据 PID 存活和 artifact 存在情况分类处理（自动续接 / 重新调度 / 请求人工决策） | Epic 5 (崩溃后自动恢复) | ✓ Covered |
| FR26 | 系统可在恢复后向操作者展示恢复摘要（自动恢复数量 + 需人工决策数量） | Epic 5 (恢复摘要展示) | ✓ Covered |
| FR27 | 系统可记录每次 agent 调用的结构化数据（耗时、成本、token 用量、收敛轮次），用于基线数据收集 | Epic 2 (Agent 调用结构化数据记录) | ✓ Covered |
| FR28 | 系统可记录每次 agent 调用的成本（Claude: 直接读取 total_cost_usd；Codex: 从 token 数计算） | Epic 2 (Agent 调用成本记录) | ✓ Covered |
| FR29 | 系统可为每个 story 创建独立的 git worktree，在 worktree 中执行 agent 任务 | Epic 2 (Git worktree 创建) | ✓ Covered |
| FR30 | 系统可在 story 完成后清理 worktree | Epic 2 (Worktree 清理) | ✓ Covered |
| FR31 | 系统可管理 merge queue，按顺序执行 rebase 和 merge（需人类授权） | Epic 4 (Merge queue 管理) | ✓ Covered |
| FR32 | 系统可在 regression 失败时自动冻结 merge queue，阻止后续 merge | Epic 4 (Regression 失败冻结 merge queue) | ✓ Covered |
| FR33 | 操作者可通过 `ato init` 初始化项目（创建 SQLite 数据库、检测 CLI 安装和认证状态） | Epic 1 (ato init 初始化) | ✓ Covered |
| FR34 | 系统可检测 Claude CLI 和 Codex CLI 的安装状态和认证有效性，报告环境就绪情况 | Epic 1 (CLI 安装/认证检测) | ✓ Covered |
| FR35 | 操作者可通过配置文件设置系统参数（并发上限、超时阈值、成本上限、Convergent Loop 参数等） | Epic 1 (配置文件参数设置) | ✓ Covered |
| FR36 | 操作者可通过 TUI 查看所有 story 的当前状态、所在阶段及 Convergent Loop 进度信息（当前轮次、open findings 数量） | Epic 6 (TUI story 状态/阶段/CL 进度) | ✓ Covered |
| FR37 | 操作者可通过 TUI 与审批队列交互（查看详情、做出决策） | Epic 6 (TUI 审批交互) | ✓ Covered |
| FR38 | 操作者可通过 `ato batch status` 查看当前 batch 的整体进度 | Epic 6 (ato batch status) | ✓ Covered |
| FR39 | 操作者可通过 `ato start` 启动编排系统（含自动崩溃恢复） | Epic 2 (ato start 启动编排) | ✓ Covered |
| FR40 | 操作者可通过 `ato stop` 优雅停止编排系统 | Epic 2 (ato stop 优雅停止) | ✓ Covered |
| FR41 | 系统可同时编排多个项目的流水线，每个项目独立的状态存储（Growth P2: 并行+多项目） | Epic 7 (多项目并行 (Growth)) | ✓ Covered |
| FR42 | 系统可执行梯度降级：Claude fix 未收敛 → Codex 攻坚 → Interactive Session（Growth P1: 梯度降级） | Epic 7 (梯度降级 (Growth)) | ✓ Covered |
| FR43 | 系统可从历史运行数据中提取模式，自动调整系统参数（Growth P3: Memory 层） | Epic 7 (Memory 层参数自适应 (Growth)) | ✓ Covered |
| FR44 | 操作者可手动添加 finding 并标注分类，供 Memory 层消费（Growth P4: 手动 finding） | Epic 7 (手动 finding 添加 (Growth)) | ✓ Covered |
| FR45 | 操作者可在 TUI 中 override 任意 finding 的 severity（Growth P4: severity override） | Epic 7 (Severity override (Growth)) | ✓ Covered |
| FR46 | 系统可在 review prompt 中自动注入来自 Memory 的历史检查提示（Growth P3: prompt 强化） | Epic 7 (Review prompt 自动强化 (Growth)) | ✓ Covered |
| FR47 | 操作者可通过 TUI 查看成本面板、UAT 趋势图和 finding 详情（Growth P4: TUI 增强） | Epic 7 (TUI 增强 (Growth)) | ✓ Covered |
| FR48 | 系统可在 UAT 不通过时自动将 story 退回到 fix 阶段，重新进入 Convergent Loop | Epic 4 (UAT 不通过退回 fix) | ✓ Covered |
| FR49 | 操作者可查看任意 story 的完整执行历史（哪个 agent 在什么时间执行了什么任务，产出了什么 artifact） | Epic 5 (Story 执行历史查看) | ✓ Covered |
| FR50 | 系统可向操作者展示任务失败的原因（认证过期、超时、解析错误等）和可选的恢复操作（重试、跳过、escalate） | Epic 4 (任务失败原因+恢复选项) | ✓ Covered |
| FR51 | 配置变更需重启系统生效（MVP） | Epic 1 (配置变更需重启) | ✓ Covered |
| FR52 | 系统可在 worktree rebase 产生冲突时调度 agent 自动解决，解决后重新进入 review 流程；解决失败 escalate 给操作者 | Epic 4 (Worktree rebase 冲突解决) | ✓ Covered |
| FR53 | 系统可从 task 输出中提取结构化工作记忆摘要（Context Briefing），作为跨 task 边界的 fresh session 输入 | Epic 2 (Context Briefing 提取) | ✓ Covered |

### Missing Requirements

- No uncovered PRD FRs were found.
- No extra epics-only FRs were found outside the PRD.
- The epics document contains an explicit `FR Coverage Map` and a mirrored requirements inventory, so epic-level traceability is mechanically complete at the FR numbering level.

### Coverage Statistics

- Total PRD FRs: 53
- FRs covered in epics: 53
- Coverage percentage: 100%

## Architecture Analysis

### Architecture Completeness Assessment

架构文档包含 10 个显式决策记录（含 Rationale + Affects），覆盖了进程模型、TUI 通信、配置边界、会话检测、Schema 迁移、结构化日志、崩溃恢复、状态机测试、CLI 适配器保护和 Preflight 检查协议。总体技术一致性强。

### Key Architecture Decisions Summary

| # | Decision | Chosen Approach | Impact |
|---|----------|----------------|--------|
| 1 | 进程生命周期 | Orchestrator + TUI 始终独立进程 | headless 运行、TUI 崩溃隔离 |
| 2 | TUI↔Orchestrator 通信 | SQLite 直写 + nudge 通知 | 低频写场景下的简洁方案 |
| 3 | 配置表达边界 | Config 定义 "what"，Engine 定义 "how" | 防止配置膨胀为最复杂组件 |
| 4 | Interactive Session 完成检测 | `ato submit` + TUI 手动标记双通道 | 可靠的人工操作闭环 |
| 5 | Schema 迁移 | `PRAGMA user_version` + 启动自动迁移 | 零外部依赖 |
| 6 | 结构化日志 | structlog 作为核心依赖 | Convergent Loop 调试的基础能力 |
| 7 | 正常重启 vs 崩溃恢复 | 基于 task status 字段区分 | 无需额外锁文件 |
| 8 | 状态机测试 | 100% 转换 + 4 关键路径集成测试 | 核心正确性保障 |
| 9 | CLI Adapter 契约保护 | 快照 fixture + smoke test + 版本追踪 | CLI 升级不会静默破坏 adapter |
| 10 | Preflight 检查 | 三层验证（系统→项目→制品） | 环境就绪的结构化保证 |

### Data Model Readiness

- **状态**：部分就绪。表名和概念列已确定（stories、tasks、findings、approvals、cost_log、preflight_results），但完整 DDL（主键、外键、索引、约束）显式推迟到首个 story 实现。
- **WAL 配置**：已明确 `journal_mode=WAL`、`busy_timeout=5000`、`synchronous=NORMAL`。
- **迁移机制**：`PRAGMA user_version` + `models/migrations.py`，无外部依赖。
- **Gap**：findings 表的跨轮次匹配算法未定义 — 如何判定两轮之间的 finding 是"同一个"？需要明确匹配字段或哈希策略。

### Architecture Test Strategy Assessment

架构文档定义了完整的测试分层：

| Layer | Coverage | Notes |
|-------|----------|-------|
| Unit (state_machine) | 100% transition | ~20 tests, 每个转换至少执行 1 次 |
| Unit (adapters) | Snapshot fixture | 真实 CLI 输出样本 + 解析验证 |
| Integration | 4 关键路径 | Happy path、CL E2E、崩溃恢复、TUI |
| Smoke | CLI contract | 真实 CLI 最小调用，验证输出格式未变 |
| TUI | Textual pilot | Mock SQLite 数据，不调用真实 Orchestrator |

**Gap**：
- 无 Convergent Loop scope narrowing 的单元测试规划（仅有 E2E 集成）。
- 崩溃恢复测试使用 "function-style"（构造 DB 状态）而非真实进程 kill，macOS/Linux 差异的 PID 检测行为未覆盖。
- 无并发 worktree rebase 冲突的测试场景规划。

### Architecture Gaps

1. **SQLite 完整 DDL 推迟** — 不阻塞但增加首个 story 的实现风险，建议在 Story 1.2 AC 中明确 DDL 设计产出。
2. **Convergent Loop scope narrowing 算法未详述** — "仅验证上轮 open findings 的闭合状态"是行为描述而非算法设计，需要定义 finding 匹配和 scope 计算逻辑。
3. **Codex 成本计算的模型价格表结构未定义** — 需维护 model→price 映射，但格式和更新策略未说明。
4. **Approval 异步处理的 SLA 未定义** — SubprocessManager escalate 后创建 approval 行，但 Orchestrator 是阻塞等待还是继续处理其他 story？隐含为异步但未显式说明。
5. **ato.yaml 完整 Schema 推迟** — 配置边界（Decision 3）明确了可配置 vs. 硬编码的划分，但 YAML 的精确字段定义推迟到 `config.py` 实现。

## UX Alignment Assessment

### UX Document Status

- Found: `_bmad-output/planning-artifacts/ux-design-specification.md`
- UX scope is explicit and detailed, covering operating model, emotional goals, navigation depth, component strategy, responsive behavior, notification design, and CLI/TUI parity.

### Alignment Issues

1. **UX document and architecture disagree on TUI write semantics.**
   - UX `Platform Strategy` says the TUI is an independent process that "通过 SQLite 只读轮询 Orchestrator 状态".
   - Architecture `Decision 2` explicitly defines TUI as a direct SQLite writer for approval decisions, UAT results, and `ato submit` state updates, with `nudge` notification to Orchestrator.
   - Impact: this is a real boundary mismatch for TUI implementation and concurrency expectations.
   - Required fix: normalize the UX document to the same direct-write + nudge model used by PRD, epics, and architecture.

2. **UX/Epics treat Story detail and drill-in navigation as current-scope TUI capability, but architecture defers `StoryDetailScreen` to Growth.**
   - UX requires progressive drill-in navigation, story detail view, findings/cost/history subviews, and `/` search-driven navigation.
   - Epic 6 contains `Story 6.4` and `Story 6.5` for Story detail, drill-in, and search within the current planning set.
   - Architecture `Textual TUI 架构模式` says MVP only has `DashboardScreen` and `ApprovalScreen`, and explicitly notes `StoryDetailScreen` deferred to Growth.
   - Impact: implementation teams will not know whether detailed story inspection is MVP or Growth; this affects file structure, screen design, and acceptance criteria.
   - Required fix: choose one scope baseline and update architecture or epics/UX accordingly.

3. **UX notification design is richer than the architecture definition.**
   - UX specifies priority-based notifications, terminal bell, optional macOS notifications, and self-contained decision payloads.
   - Architecture documents internal `nudge` and CLI/TUI coordination, but does not define a user-facing notification subsystem, delivery abstraction, or OS-specific integration boundary.
   - Impact: notification behavior may be underdesigned and implemented ad hoc.
   - Required fix: add an explicit notification architecture note or narrow UX scope to terminal bell only for MVP.

4. **HeartbeatIndicator 实时更新与 TUI 轮询模型矛盾。**
   - UX 要求 HeartbeatIndicator "实时更新"（动画 spinner、已用时间、进度条）。
   - 但 TUI 整体架构基于 2-5 秒 SQLite 轮询。
   - 若 elapsed time 为客户端计算（从 task 启动时间推算），则可行但需明确这一实现策略；若依赖 DB 更新则与轮询间隔矛盾。
   - Required fix: 明确 HeartbeatIndicator 的数据源是客户端计算还是 DB 轮询，前者需在架构中标注例外。

5. **Approval Card 的推荐操作和摘要的生成来源未定义。**
   - UX 要求每个 ApprovalCard 包含 "一句话摘要" 和 "推荐操作"。
   - 但无文档说明这些内容由谁生成：是 Orchestrator 在创建 approval 时 template 化生成？还是 LLM 实时生成？
   - Impact: 影响 approval 表 schema 设计和创建流程。
   - Required fix: 在架构或 UX 中明确 approval 摘要/推荐的生成机制。

6. **终端宽度 <100 列的支持策略自相矛盾。**
   - Platform Strategy 声明 "<100 列时信息密度不足以兑现'一眼可判'承诺"，暗示 <100 为不支持。
   - 但 Responsive Strategy 为 <100 列定义了降级提示行为，且 CLI 输出约束为 80 列兼容。
   - Impact: MVP 发布时是否强制 100 列最小宽度？不明确。
   - Required fix: 明确 <100 列是 "警告但继续" 还是 "拒绝启动 TUI"。

### Warnings

- UX ↔ PRD overall alignment is strong: both documents agree on TUI + CLI dual interface, approval-first workflow, three-question first screen, crash recovery reassurance, cost visibility, and keyboard-first operation.
- UX ↔ Architecture also aligns well on major foundations: Textual-based TUI, independent TUI process, SQLite-backed state, 2-5 second refresh cadence, and crash recovery support.
- There is a terminal-width tension that should be clarified before implementation:
  - UX target-user context includes narrow SSH sessions.
  - UX `Platform Strategy` says `<100` columns cannot fulfill the core promise.
  - UX responsive sections later define degraded behavior for `<100` columns.
  - Architecture does not codify an enforcement rule for width breakpoints.
- The architecture project structure only names `dashboard.py` and `approval.py`; it does not yet allocate explicit modules/widgets for the custom UX components (`ThreeQuestionHeader`, `ApprovalCard`, `ConvergentLoopProgress`, `ExceptionApprovalPanel`, search panel). This is not a blocker by itself, but it increases interpretation risk for Epic 6。
- UX 未定义 Story 列表在同类别内的排序稳定性（按 created_at？按 story_id？），每次轮询刷新时排序跳动会破坏用户体验。
- UX 中 "已提交，等待处理" 的视觉状态在 TUI 2-5 秒轮询间隔下可能出现 10 秒以上的 stale 态，无 optimistic update 机制。

## Epic Quality Review

### 🔴 Critical Violations

1. **The story set is dominated by technical implementation tasks instead of user-value slices.**
   - Clear examples include Story 1.2 `SQLite 数据层与迁移机制`, Story 2.1 `StoryLifecycle 状态机`, Story 2.2 `TransitionQueue 串行化引擎`, Story 2.3 `SubprocessManager 与进程生命周期`, Story 2.4 `Claude CLI Adapter`, and Story 2.5 `Codex CLI Adapter`.
   - These match the anti-patterns explicitly called out by the create-epics-and-stories workflow, such as "Set up database" and "Create all models".
   - Impact: stories are less independently valuable, harder to prioritize by business outcome, and more likely to become implementation tasks rather than shippable slices.
   - Recommendation: keep the epics, but rewrite many stories as thin vertical capabilities that deliver an observable operator outcome, with technical component work moved into implementation notes or subtasks.

2. **Epic 3 is not independent; it relies on Epic 4 to function on non-happy paths.**
   - Story 3.1 creates an approval when blocking findings exceed threshold.
   - Story 3.2 escalates to manual handling by creating an approval.
   - Story 3.3 defines `convergent_loop_escalation` as an approval-driven path.
   - But the approval mechanism is only implemented in Epic 4.
   - Impact: Epic 3 cannot fully stand alone, violating the "Epic N cannot require Epic N+1" rule.
   - Recommendation: either move minimal approval handling into Epic 3, or move the human-escalation path into Epic 4 and keep Epic 3 strictly self-contained.

### 🟠 Major Issues

1. **Story 1.2 creates all core tables upfront, violating the database/entity timing rule.**
   - The workflow standard says tables/entities should be created only when first needed by a story.
   - Story 1.2 currently provisions `stories`, `tasks`, `findings`, `approvals`, `cost_log`, and `preflight_results` in one go.
   - Recommendation: split schema work so each story introduces only the tables it needs, or redefine Story 1.2 around the smallest independently useful persistence slice.

2. **Story 1.3 contains an explicit forward dependency on Story 1.4.**
   - The acceptance criteria reference "`ato init` (Story 1.4)" from within Story 1.3.
   - This is a direct violation of the "no future story dependency" rule.
   - Recommendation: invert the order, merge the relevant behavior into one story, or restate the acceptance criteria without relying on future story completion.

3. **Story 2.8 contains a partial implementation that is only "fully validated" in a future epic.**
   - The `ato start --tui` acceptance criteria only require the CLI to accept and record the flag, while stating full TUI start logic is validated later in Epic 6.
   - This weakens story independence and introduces a deferred-completion smell.
   - Recommendation: either remove `--tui` from Story 2.8 until Epic 6, or make Story 2.8 fully complete without depending on future TUI work.

4. **Several stories are likely too large for a single dev agent.**
   - Story 1.4 bundles three-layer preflight checking, rich rendering, persistence, reinitialization handling, and exit-code policy.
   - Story 2.8 bundles orchestrator lifecycle, polling, transition dispatch, cost logging, and `--tui` mode.
   - Story 6.2 bundles first-screen information architecture, custom widgets, sorting, heartbeat, empty states, and performance targets.
   - Story 6.3 bundles normal approvals, exception approvals, interaction feedback, and state transitions.
   - Story 7.3 bundles Memory recommendations, manual finding entry, severity override, and multiple enhanced TUI views.
   - Recommendation: split these into narrower slices with one primary capability each.

5. **Epic 2 承载了 16 个 FR（FR3-4, FR6-12, FR27-30, FR39-40, FR53），是最重的 epic。**
   - 10 个 stories 跨越了状态机、适配器、worktree、事件循环、batch 选择和 Interactive Session 等不同关注点。
   - 内部 story 依赖链（2.1→2.2→2.3）隐式但未在文档中显式标注。
   - Recommendation: 至少将 Epic 2 拆分为 "编排引擎核心"（2.1-2.3, 2.8）和 "Agent 集成层"（2.4-2.7, 2.9-2.10）两个独立 epic。

6. **Story 3.2（Convergent Loop 协议核心）过于密集，应拆分为 4-5 个 stories。**
   - 包含 10+ AC，覆盖首轮 review dispatch、fix dispatch、re-review scope narrowing、收敛判定算法和 finding 状态追踪等多个独立能力。
   - Recommendation: 拆分为 "首轮 review 执行"、"fix dispatch + artifact 验证"、"re-review scope narrowing"、"收敛判定 + 终止条件" 等独立 stories。

7. **Story 2.6（BMAD 适配层）的规模和风险被低估。**
   - AC 中 "LLM 语义解析保持鲁棒性" 无可量化指标，"鲁棒性" 如何测试和验证未定义。
   - LLM 解析的不确定性意味着此 story 的工期可能是估算值的 2-5 倍。
   - Recommendation: 明确 "鲁棒性" 的量化指标（如：95% 解析成功率对已知 fixture），并定义解析失败时的回退路径。

8. **缺少 Regression 测试执行的显式 story。**
   - Story 生命周期包含 "regression" 阶段，merge queue 冻结（FR32）依赖 regression 结果，但无 story 定义谁执行 regression、测试套件管理和结果判定逻辑。
   - Recommendation: 在 Epic 4 或独立 epic 中增加 regression 测试执行 story。

9. **缺少关键路径文档。**
   - Epic 依赖关系（Epic 1 → Epic 2 → Epics 3-6）和 story 内依赖链未以可视化或显式列表形式呈现。
   - 开发团队可能错误地尝试并行开发存在依赖的 stories。
   - Recommendation: 增加 "Epic/Story 关键路径" 章节，明确串行和可并行的 story 分组。

### 🟡 Minor Concerns

1. **Epic titles remain somewhat technical even when the goal statements are user-facing.**
   - Example: `项目初始化与配置引擎`, `编排核心与 Agent 执行`, `Convergent Loop 质量门控`.
   - The goal statements usually recover the user-value framing, so this is not blocking.
   - Recommendation: rename epics in a more outcome-oriented way if you want the plan to be easier to review operationally.

2. **Acceptance criteria quality is generally strong, but many ACs describe implementation mechanics more than operator-visible outcomes.**
   - BDD structure is mostly consistent and testable.
   - The main quality issue is not formatting; it is that many stories are component-oriented, so the ACs inherit that technical focus.

3. **No explicit early CI/CD or repository automation story appears in the greenfield plan.**
   - This is not always mandatory for a local-first MVP, but it does leave implementation governance weaker than typical greenfield standards.
   - Recommendation: either add a lightweight CI story early, or explicitly document why local quality gates are sufficient for this phase.

4. **Growth 阶段 Epic 7 严重欠分解。**
   - 7 个 FR（FR41-47）仅由 3 个 stories 覆盖，Story 7.3 同时捆绑 Memory 层参数学习（FR43）、手动 finding（FR44）、severity override（FR45）、prompt 强化（FR46）和 TUI 增强（FR47）。
   - 无可量化的成功指标（"推荐准确率" 的目标值未定义）。
   - Recommendation: 即使 Growth 不在当前 sprint，也应拆分为可独立验证的 stories 并定义成功指标。

5. **Story 2.10（Context Briefing）的 schema 未定义。**
   - AC 要求 "提取结构化工作记忆摘要" 但未定义摘要的字段结构（story_id, task_type, artifacts_summary, agent_notes？）。
   - Recommendation: 在 AC 中增加 Briefing 的 Pydantic schema 定义或引用。

### Recommendations

- Rewrite technical stories into user-observable vertical slices before implementation starts.
- Remove all forward dependencies, especially Story 1.3 → Story 1.4 and Epic 3 → Epic 4 escalation coupling.
- Split oversized stories, especially Story 1.4, Story 2.8, Story 3.2, Story 6.2, Story 6.3, and Story 7.3.
- Split Epic 2 into at least two independent epics by concern (engine core vs. agent integration).
- Add regression test execution story with explicit test suite management scope.
- Add critical path documentation showing serial vs. parallelizable story groups.
- Define quantifiable success criteria for Story 2.6 BMAD robustness and Epic 7 Memory layer.
- Re-run epic/story validation after the structural fixes; the current document is traceable, but not yet cleanly implementation-ready under BMAD story-quality standards.

## Cross-Document Consistency Analysis

本节汇总四份核心制品之间的一致性问题，按影响严重性排序。

### 🔴 一致性冲突（必须在实现前解决）

| # | 冲突 | 涉及文档 | 具体矛盾 |
|---|------|---------|----------|
| C1 | TUI 写语义 | UX vs Architecture | UX: "SQLite 只读轮询"；Architecture Decision 2: "SQLite 直写 + nudge" |
| C2 | StoryDetailScreen 范围 | UX + Epics vs Architecture | UX 要求渐进钻入导航，Epic 6.4/6.5 在 MVP scope；Architecture 将 `StoryDetailScreen` 推迟到 Growth |
| C3 | 通知架构边界 | UX vs Architecture | UX 定义优先级矩阵 + macOS 通知 + 自包含决策载荷；Architecture 仅定义内部 nudge 机制 |

### 🟠 一致性偏差（应在实现前对齐）

| # | 偏差 | 涉及文档 | 具体差异 |
|---|------|---------|----------|
| C4 | HeartbeatIndicator 更新模型 | UX vs Architecture | UX 要求 "实时更新"；Architecture 整体 TUI 为 2-5 秒轮询 |
| C5 | <100 列终端支持 | UX 内部 | Platform Strategy 声明不支持；Responsive Strategy 定义降级行为 |
| C6 | Approval 摘要生成 | UX vs Epics + Architecture | UX 要求 ApprovalCard 含 "一句话摘要 + 推荐操作"；无文档定义生成机制 |
| C7 | 自定义组件模块分配 | UX vs Architecture | UX 定义 5 个自定义组件；Architecture 项目结构仅列 `dashboard.py` + `approval.py` |
| C8 | Regression 测试执行 | PRD vs Epics | PRD 生命周期含 regression 阶段 + merge queue 冻结依赖 regression 结果；Epics 无 regression 执行 story |
| C9 | Context Briefing schema | PRD (FR53) vs Epics (2.10) vs Architecture | 三份文档均提及但无一定义 Briefing 的字段结构 |

### 🟢 已对齐的核心领域

- PRD ↔ Architecture: 进程模型、配置驱动、SQLite WAL、声明式工作流、CLI adapter 隔离、崩溃恢复策略。
- PRD ↔ Epics: 53/53 FR 完整覆盖，MVP/Growth 分层一致。
- UX ↔ PRD: TUI + CLI 双入口、approval-first 工作流、三问首屏、成本可视化、键盘优先。
- Architecture ↔ Epics: 组件映射总体一致，模块粒度匹配 story 边界。

## Summary and Recommendations

### Overall Readiness Status

**NOT READY — 需完成 P0 级修复后方可进入实现**

### Readiness Scorecard

| 维度 | 状态 | 说明 |
|------|------|------|
| PRD 完整性 | ✅ 充分 | 53 FR + 14 NFR，MVP/Growth 边界清晰，成功指标可量化 |
| PRD → Epic 可追踪性 | ✅ 完整 | 53/53 FR 映射，无遗漏 |
| Architecture 技术一致性 | ✅ 强 | 10 个决策记录，组件/模块/测试分层完整 |
| Epic/Story 结构质量 | 🔴 不达标 | 技术任务导向、前向依赖、超大 stories、Epic 2 过载 |
| 跨文档一致性 | 🔴 不达标 | 3 个冲突 + 6 个偏差需解决 |
| UX ↔ Architecture 对齐 | 🟠 部分 | TUI 写模型、通知、StoryDetail 范围存在冲突 |
| 实现细节清晰度 | 🟠 部分 | DDL 推迟、CL scope narrowing 算法、Context Briefing schema 未定义 |
| 测试策略完整性 | 🟠 部分 | 状态机 100% 覆盖，但 CL 正确性证明、并发 worktree、BMAD 降级测试缺失 |

### P0: 阻塞实现的必修项

> 以下问题必须在**任何 story 进入 dev 之前**解决。

1. **解决 TUI 写语义冲突 (C1)**
   - 将 UX Platform Strategy 中 "SQLite 只读轮询" 修正为 Architecture Decision 2 的 "直写 + nudge" 模型。
   - 交付物：更新后的 `ux-design-specification.md`。

2. **确定 StoryDetailScreen 的 MVP 范围 (C2)**
   - 选择方案：
     - A) 将 Epic 6.4/6.5 移入 Growth，UX 文档标注 MVP 仅支持列表视图。
     - B) 将 Architecture 中 `StoryDetailScreen` 提前到 MVP，补充模块分配。
   - 交付物：更新后的 `architecture.md` 和 `ux-design-specification.md`（或 `epics.md`）。

3. **消除 Epic/Story 前向依赖**
   - Story 1.3 → 1.4: 调整顺序或合并。
   - Epic 3 → Epic 4 escalation: 在 Epic 3 中增加最小 approval 处理，或将 escalation 路径移入 Epic 4。
   - Story 2.8 → Epic 6 `--tui`: 移除 `--tui` flag 直到 Epic 6。
   - 交付物：更新后的 `epics.md`。

4. **将技术任务 stories 重写为用户价值切片**
   - 重点对象：Story 1.2 (SQLite)、2.1-2.3 (状态机/队列/进程管理)、2.4-2.5 (CLI adapters)。
   - 标准：每个 story 的 AC 至少包含一个操作者可观察的行为验证点。
   - 交付物：更新后的 `epics.md`。

### P1: 显著降低实现风险的推荐修复

> 建议在**首个 sprint 完成前**解决。

5. **拆分超大 stories**
   - Story 3.2 → 4-5 stories（首轮 review、fix dispatch、re-review scope narrowing、收敛判定）。
   - Story 2.8 → 2-3 stories（事件循环骨架、transition dispatch、CLI 命令绑定）。
   - Story 1.4 → 2 stories（preflight 三层检查、`ato init` CLI 命令）。
   - Story 6.2, 6.3, 7.3 各拆分。

6. **拆分 Epic 2**
   - 当前 16 FR + 10 stories 过于庞大。
   - 建议拆为 "编排引擎核心"（状态机 + TransitionQueue + 事件循环）和 "Agent 集成层"（CLI adapters + BMAD + worktree + batch + interactive session）。

7. **定义通知架构边界 (C3)**
   - 在 Architecture 中增加用户可见通知子系统设计，或在 UX 中将 MVP 范围收窄到仅 terminal bell。
   - 明确 macOS 通知是 MVP 还是 Growth。

8. **补充 Regression 测试执行 story (C8)**
   - 定义测试套件管理、触发时机、结果判定和 merge queue 集成。

9. **定义 Context Briefing schema (C9)**
   - 在 `schemas/` 目录规划或 Story 2.10 AC 中增加 Pydantic model 定义。

10. **增加关键路径文档**
    - 显式列出 story 串行依赖链和可并行分组，防止开发团队在存在依赖的 stories 上并行工作。

### P2: 建议改进但不阻塞实现

11. 将 Story 1.2 的 "一次性创建所有表" 改为增量 schema 引入。
12. 为 Story 2.6 BMAD 适配层定义 "鲁棒性" 的量化指标和解析失败回退路径。
13. 明确 HeartbeatIndicator 的数据源策略（客户端计算 vs. DB 轮询）(C4)。
14. 在 Architecture 项目结构中为 5 个 UX 自定义组件分配模块文件 (C7)。
15. 解决 UX 内部 <100 列支持策略矛盾 (C5)。
16. 定义 Approval 摘要/推荐操作的生成机制 (C6)。
17. 为 Convergent Loop scope narrowing 定义 finding 匹配算法。
18. 为 Codex 成本计算定义模型价格表结构。
19. 为 Epic 7 Growth stories 定义可量化的成功指标。
20. 重命名 epic 标题为更面向用户结果的表述。

### Recommended Workflow

```
Step 1: 解决 P0-1 ~ P0-4（文档对齐 + story 重写）
   ↓
Step 2: 解决 P1-5 ~ P1-6（story 拆分 + epic 拆分）
   ↓
Step 3: 重新运行 bmad-check-implementation-readiness
   ↓
Step 4: 确认 READY 后进入 Sprint 1
   ↓
Step 5: Sprint 1 期间并行解决 P1-7 ~ P1-10 和 P2 项
```

### Issue Summary

| 类别 | 🔴 Critical | 🟠 Major | 🟡 Minor | Total |
|------|-----------|---------|---------|-------|
| PRD 实现清晰度 | 0 | 6 | 0 | 6 |
| Architecture 缺口 | 0 | 5 | 0 | 5 |
| Epic/Story 结构 | 2 | 9 | 5 | 16 |
| UX 对齐 | 3 | 3 | 2 | 8 |
| 跨文档一致性 | 3 | 6 | 0 | 9 |
| **Total** | **8** | **29** | **7** | **44** |

### Final Assessment

制品集在**覆盖率和文档深度**上表现出色 — PRD 53/53 FR 完整覆盖、Architecture 10 个决策记录技术一致性强、UX 设计细节丰富。但在**结构质量和跨文档一致性**上存在显著债务：技术任务导向的 story 分解、前向依赖违规、UX/Architecture 的 TUI 写模型和 StoryDetail 范围冲突，以及多个实现细节的模糊地带。

基础是坚实的，直接推进实现则会把这些歧义和结构债务压入 story 执行阶段，导致 agent 实现时的返工和 escalation 频率显著上升。建议按 P0 → P1 → 重新验证的路径修复后再启动 Sprint 1。

## Re-Assessment (Second Pass)

**Assessor:** Codex
**Date:** 2026-03-24
**Mode:** Re-review after claimed fixes

### Scope Checked

- `_bmad-output/planning-artifacts/prd.md`
- `_bmad-output/planning-artifacts/architecture.md`
- `_bmad-output/planning-artifacts/epics.md`
- `_bmad-output/planning-artifacts/ux-design-specification.md`

### Re-Review Outcome

当前源规划文档并没有体现出已消除上轮阻塞项的结果。二次审查直接在源文件中仍能定位到同类问题，因此 gate 结论不变。

### Findings Still Present in Source Artifacts

1. **PRD 与 FR 覆盖率状态未变**
   - PRD 仍为 53 FR / 14 NFR。
   - Epics 仍为 53/53 FR 全映射。
   - 说明：traceability 依旧强，但这从来不是阻塞实施的主问题。

2. **UX ↔ Architecture 的 TUI 写模型冲突仍然存在**
   - UX 仍写明 TUI 通过 SQLite 只读轮询。
   - Architecture 仍明确规定 TUI 直写 SQLite + `nudge`。
   - 该边界冲突未在源文档中消失。

3. **StoryDetail / TUI 范围冲突仍然存在**
   - Architecture 仍把 `StoryDetailScreen` 视为 Growth。
   - UX 与 Epic 6 仍把 story detail、drill-in、search 视为当前范围能力。
   - 该范围分裂未解决。

4. **Story 集仍然以技术实现任务为主**
   - `SQLite 数据层与迁移机制`、`StoryLifecycle 状态机`、`TransitionQueue 串行化引擎`、CLI adapter 等 story 仍是技术任务导向，而非操作者可感知的垂直价值切片。

5. **数据库一次性建全表的反模式仍然存在**
   - Story 1.2 仍一次性创建 `stories`、`tasks`、`findings`、`approvals`、`cost_log`、`preflight_results`。

6. **Story 1.3 的前向依赖仍然存在**
   - 仍然在 AC 中直接引用 Story 1.4。

7. **Epic 3 → Epic 4 的 approval 依赖仍然存在**
   - Story 3.1 / 3.2 / 3.3 的失败与 escalation 路径仍依赖 Epic 4 的 approval 机制。

8. **Story 2.8 的部分前向依赖仍然存在**
   - `ato start --tui` 仍然只是“接受 flag 并记录”，完整验证仍留给 Epic 6。

9. **超大 story 仍然存在**
   - Story 1.4、Story 3.2、Story 5.1、Story 6.1、Story 6.2、Story 6.3 仍然承载过宽的验收范围。

### Second-Pass Readiness Status

**NOT READY**

### Why The Gate Did Not Change

- 上轮指出的关键阻塞项在当前源文档里仍然可见。
- 没有新增 FR 覆盖缺口，但也没有移除结构性阻塞项。
- 因此这次不是“修复后通过”，而是“在源文档层面仍未完成修复”。

### Direct Next Action

在进入 Sprint Planning 之前，先更新源规划文档本身：

1. 把技术型 story 重写为面向操作者结果的垂直切片。
2. 去掉 Story 1.3 → 1.4 和 Epic 3 → Epic 4 的前向依赖。
3. 统一 UX 与 Architecture 对 TUI 写模型、StoryDetail 范围、通知边界的定义。
4. 完成这些源文档修改后，再重新运行 implementation readiness。

## Re-Assessment (Third Pass)

**Assessor:** Codex
**Date:** 2026-03-24
**Mode:** Re-review after source-document fixes

### Improvements Verified

1. **UX ↔ Architecture 的 TUI 写模型已经对齐**
   - UX 已改为“SQLite 直写 + nudge 通知，状态读取 2-5 秒轮询”。
   - 这条上轮的核心冲突已关闭。

2. **通知边界已收敛到 MVP 可实现范围**
   - UX 已将 macOS 通知下调为 Growth，MVP 以 terminal bell 为主。
   - 这消除了此前“UX 要求过宽、Architecture 无承接”的主要矛盾。

3. **<100 列终端策略已明确**
   - UX 已从矛盾表述改为“降级警告 + CLI-only 模式”。
   - 这一点现在具备可实现性。

4. **StoryDetail 范围冲突已关闭**
   - Architecture 已将 `StoryDetailScreen` 提前到 MVP。
   - UX / Epic 6 / Architecture 现在在这一点上是一致的。

5. **epics.md 的 story 分解明显改善**
   - Epic 2 已拆成 2A / 2B。
   - Epic 3 已拆成 3.2a / 3.2b / 3.2c / 3.2d。
   - Epic 6 与 Epic 7 也拆成更细的 story。
   - 这比上一轮更接近单 dev agent 可执行粒度。

### Remaining Material Issues

1. **Story 1.2 仍然带有未来 story 引用**
   - 它在 AC 中显式指向 Story 3.1、Story 2B.1、Story 1.4a 的建表职责。
   - 这虽然比“一次性建全表”更好，但仍然把当前 story 写成了一个依赖未来实现的占位说明，而不是自洽交付。

2. **approval 表/能力的引入时机仍未完全理顺**
   - Story 2B.3 在解析失败时就要“创建 approval 请求操作者人工审阅”。
   - 但 `approvals` 表的创建职责仍被放在 Story 3.1 的 AC 注释里。
   - 这说明依赖链已经改善，但 approval 的最小可用基础设施仍未落在最早需要它的位置。

3. **仍有一部分 story 保持系统/技术导向，而不是用户结果导向**
   - 典型例子是 Story 1.2、Story 3.1、Story 3.3。
   - 这已经不是“全盘技术施工单”，但还没完全达到 BMAD 的 user-value-first 标准。

4. **少数 story 仍偏大**
   - Story 5.1、Story 6.1 仍承载较宽的验收范围。
   - 不一定阻塞，但会提高实现时的返工概率。

### Third-Pass Readiness Status

**NEEDS WORK**

### Gate Change From Previous Pass

相比上一轮，状态已经从“核心边界冲突未收敛”改善到“剩余依赖链与 story 质量问题待清理”。

这意味着：
- 上一轮的多个 `NOT READY` 级阻塞项已被修掉。
- 当前更像是进入 Sprint Planning 前的最后一轮结构整理，而不是从根上重做规划。

### Direct Next Action

在进入 Sprint Planning 之前，再做一轮小范围源文档修补：

1. 把 approval 的最小建表/写入能力前移到首次真正需要它的 story。
2. 去掉 Story 1.2 中对未来 story 的显式引用，把依赖改写为当前 story 可交付的边界。
3. 再把 Story 1.2、Story 3.1、Story 3.3 往用户结果导向改一层。
4. 完成后可以再做一次快速 gate；如果这几项收敛，我预期可以进入 `Sprint Planning`。

## Re-Assessment (Fourth Pass)

**Assessor:** Codex
**Date:** 2026-03-24
**Mode:** Re-review after targeted dependency and story-structure fixes

### Improvements Verified

1. **Story 1.2 已从“技术建表任务”改成操作者可感知的恢复价值 story**
   - 现在的表述围绕“story/task 状态零丢失”和“自动恢复”展开。
   - 同时已把 `approvals` 通用表前移到这里创建，解决了上轮 approval 基础设施落点过晚的问题。

2. **approval 引入时机已经理顺**
   - Story 2B.3 和 Story 3.1 现在都可以合法写入 `approvals` 表。
   - 不再存在“先写 approval，再在未来 story 中创建 approvals 表”的硬依赖。

3. **前向依赖问题已基本消除**
   - 这轮复查未再发现 Story 1.3 → Story 1.4 这类前向引用。
   - 当前文档中的 story 引用主要为后向引用或同链条回指，属于可接受范围。

4. **UX ↔ Architecture 关键边界已保持一致**
   - TUI 写模型一致。
   - StoryDetail 已一致纳入 MVP。
   - 通知边界已收敛到 MVP 可实现范围。

5. **story 粒度进一步接近可执行状态**
   - Epic 5 已拆成 5.1a / 5.1b。
   - Epic 6 已拆成 6.1a / 6.1b、6.2a / 6.2b、6.3a / 6.3b。
   - 这些拆分明显降低了单 story 的实现风险。

### Residual Concerns (Non-Blocking)

1. **仍有少数 story 使用 `As a 系统` 视角**
   - 例如 1.4a、3.2a、3.2b、3.2c、3.2d、7.1、7.3a。
   - 这些 story 仍偏系统行为驱动，而不是纯操作者结果驱动。
   - 但它们大多已位于合理的实现边界内，不再构成当前 gate 的阻塞项。

2. **个别 story 仍偏宽**
   - Story 4.2、5.1a、6.4、6.5 仍承载较多验收内容。
   - 这更像实现期风险管理问题，而不是规划阶段的结构性失败。

3. **epic 命名仍偏技术化**
   - 但 epic goal statements 已足够清楚地表达用户结果，因此不构成 gate 阻塞。

### Fourth-Pass Readiness Status

**READY**

### Gate Change From Previous Pass

相比第三轮，本轮已经完成了最后几项实质性结构修补：

- approval 基础设施落点已前移
- Story 1.2 的未来引用已消除
- story 分解进一步细化
- 源文档中的主要硬阻塞项已不再存在

因此，readiness gate 从 `NEEDS WORK` 提升为 `READY`。

### Recommended Next Step

可以进入 `Sprint Planning`。

建议做法：

1. 进入 `bmad-bmm-sprint-planning` 生成 Sprint 1 执行计划。
2. 在 Sprint 1 执行中，把 residual concerns 作为实现期质量关注点，而不是再阻塞规划阶段。
3. 对 Epic 4.2、5.1a、6.4、6.5 保持实现期 scope 控制，避免 story 执行中再次膨胀。
