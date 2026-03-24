---
stepsCompleted: ['step-01-init', 'step-02-discovery', 'step-02b-vision', 'step-02c-executive-summary', 'step-03-success', 'step-04-journeys', 'step-05-domain-skipped', 'step-06-innovation', 'step-07-project-type', 'step-08-scoping', 'step-09-functional', 'step-10-nonfunctional', 'step-11-polish', 'step-12-complete']
inputDocuments:
  - 'docs/agent-team-orchestrator-system-design-input-2026-03-23.md'
  - '_bmad-output/planning-artifacts/research/technical-claude-codex-cli-integration-research-2026-03-24.md'
  - '_bmad-output/brainstorming/brainstorming-session-2026-03-23-2200.md'
documentCounts:
  briefs: 0
  research: 1
  brainstorming: 1
  projectDocs: 1
workflowType: 'prd'
classification:
  projectType: 'AI 团队操作系统'
  domain: 'AI 团队编排（软件交付）'
  complexity: 'MVP: 中 / 完整系统: 高'
  projectContext: '受约束的绿地（部分约束可变）'
  positioning: '新品类 — 人类掌舵 + AI 团队协作 + 显式质量门控 + 自我改进'
  systemBoundary: '编排 + 教练（通过 Memory 驱动 prompt 优化）'
  coreCapabilities: '编排 + 质量门控 + 系统智能层 + 人机协作'
  coreDifferentiator: '自我改进闭环 — 感知→记忆→行动'
  coreWhy: '消除偶然复杂度，让人回到判断者角色'
---

# Product Requirements Document - AgentTeamOrchestrator

**Author:** Enjoyjavapan163.com
**Date:** 2026-03-24

## Executive Summary

一个人 + AgentTeamOrchestrator = 一支可同时推进多个项目的 AI 软件团队。

使用 AI agent 辅助开发的技术负责人面临一个核心矛盾：手动编排多角色协作（review、QA、regression、merge）消耗了大量带宽，导致同一时间只能推进一条流水线。AgentTeamOrchestrator 将这些可自动化的环节编码为自动化流水线，同时在关键节点保留人类判断权（批次选择、架构审批、UAT、异常处理），将人的带宽从线性扩展到并行。

这不是 AI 编码助手（单 agent 写代码），不是全自动 AI 工程师（无人监督），而是一个多角色 AI 团队的运营系统——人类掌舵，系统编排，agent 执行。

系统采用五层架构：人类控制层 → 编排控制面 → 无状态角色工作者 → Git/Worktree 执行面 → Artifact 与审计层。编排核心是本地单进程应用，通过嵌入式状态存储管理运行时状态，终端界面提供操作入口。

### What Makes This Special

**来自实战的人机边界。** 系统设计者已经手动操作这套流程很久，清楚知道哪些环节能自动化（review-fix 循环、QA、regression）、哪些必须人来做（架构判断、UAT、异常处理）。旧系统的教训证明 75% 的复杂度是偶然复杂度——tmux 编排、LLM 指挥官规则违反监控、terminal 输出解析——新系统通过"编排者是代码而非 LLM"从根本上消除这些偶然复杂度。

**自动化但质量不降。** 市场上多数 AI 编码工具追求速度而牺牲质量，或追求全自动而失去人类控制。本系统通过三重质量保障机制——不可变的 deterministic validation contract、agent 级 scoped review（每轮 scope 严格收窄）、梯度降级策略（Claude fix → Codex 攻坚 → 人+agent 协作）——确保自动化的上限由质量门控决定，而非无脑执行。

**自我改进闭环。** 系统智能层（可观测性 + Memory 统一）持续收集每次 agent 调用的结构化数据（成本、耗时、收敛轮次、finding 分布），从中提取可操作的模式，自动调整系统参数（模型选择、max_rounds、prompt 模板）。系统越用越精准——这是与所有现有编排工具的本质区别。

## Project Classification

| 维度 | 分类 |
|------|------|
| **项目类型** | AI 团队操作系统 |
| **领域** | AI 团队编排（软件交付） |
| **复杂度** | MVP: 中 / 完整系统: 高（三重本质复杂度 + 无行业先例） |
| **项目上下文** | 受约束的绿地（BMAD 不可改、CLI subprocess only、无 API Key 须用默认认证、双 CLI 能力差异） |
| **定位** | 新品类 — 人类掌舵的多 agent 协作运营系统 |
| **系统边界** | 编排 + 教练（系统管流程和参数优化，agent 管专业工作） |
| **核心差异化** | 自我改进闭环 — 感知→记忆→行动 |

## Success Criteria

### User Success

- **核心成功时刻：** 用户在 TUI 仪表盘上看到多个项目的 story 在并行推进，自己只需要处理审批队列中的判断性决策
- **效率感知：** 人花在判断上的时间 > 花在机械操作上的时间（反转旧系统的比例）
- **信任建立：** UAT 中发现的问题随时间持续减少，趋向于 0——用户对自动化流水线的质量逐步建立信任
- **恢复无痛：** 系统崩溃或 agent 超时后，用户不需要手动重建状态，重启即恢复
- **首次成功体验：** 从安装配置到第一个 story 端到端跑通 ≤ 半天

### Business Success

- **3 个月目标：** 系统成熟运行，能同时并行推进约 3 个项目的软件交付流水线（token 预算允许的前提下）
- **效率基线建立：** MVP 阶段先量化单项目手动操作的基线数据（周期、人工介入次数、耗时分布），再用并行阶段的数据计算真实效率倍数
- **成本可控：** 每个 story 的 token 成本可追踪、可预测，无失控消耗

### Technical Success

- **核心代码精简：** 核心编排逻辑 ≤1500 行（不含 TUI/CLI 交互层，对比旧系统 4000+），证明偶然复杂度被消除
- **测试质量：** 状态机 transition 覆盖率 100%，核心路径（story 端到端、Convergent Loop、崩溃恢复）集成测试覆盖
- **崩溃恢复：** ≤10 秒（SQLite 查表 + PID/artifact 检查）
- **角色边界可强制：** 通过 CLI 工具限制在系统层保证 reviewer 不可编辑、developer 不可自审
- **可扩展性：** 新增角色或流水线阶段不需要重写编排机制

### Measurable Outcomes

| 指标 | MVP 目标 | 成熟期目标 |
|------|---------|-----------|
| 并行项目数 | 1 个项目端到端跑通 | ~3 个项目并行 |
| UAT 缺陷趋势 | 建立基线 | 趋向于 0 |
| 人工介入频率 | 可量化追踪 | 持续下降 |
| 效率倍数 | 建立单项目手动操作基线 | 基于基线计算真实倍数 |
| Story 端到端耗时 | 可追踪 | 可预测 |
| Story 级成本 | 可追踪 | 可优化 |
| Convergent Loop 收敛率 | 收集数据建立基线 | ≥80%（3 轮内） |
| 崩溃恢复时间 | ≤30 秒 | ≤10 秒 |
| 首次端到端成功 | ≤ 半天（含配置） | — |
| 状态机测试覆盖 | transition 100% | 维持 100% |

## User Journeys

### 旅程一：日常运营 — "今天又是高效的一天"

**角色：** 李明，全栈技术负责人，同时维护 3 个项目，习惯用 BMAD 方法论管理开发流程。

**开场：** 周一早上，李明打开终端，启动 AgentTeamOrchestrator TUI。仪表盘上显示：项目 A 有 2 个 story 在 review-fix 循环中、项目 B 的 regression 全部通过等待 merge 授权、项目 C 有一个新 batch 等待选择。

**上升：** 他先处理审批队列——批准项目 B 的 3 个 story merge，30 秒完成。然后查看项目 A 的 review findings 摘要，发现一个 blocking 被适配层标为 suggestion 的误判，在 TUI 中 override severity。接着为项目 C 选择本周的 batch，确认 5 个 story 进入流水线。

**高潮：** 审批处理完毕，他切去写项目 D 的架构文档。一小时后，终端响起一声 bell 通知——项目 A 的一个 regression 失败，merge queue 已自动冻结，等待他决策（revert / fix forward / pause）。他回到 TUI，查看 regression 报告，选择 fix forward。同时注意到项目 C 的 3 个 story 已经自动创建并通过验证，worktree 已建好，开发 agent 正在工作。

**结局：** 下班前查看成本面板——今天 3 个项目总消耗 $12.50，符合预期。UAT 阶段只发现 1 个小问题，比上周的 4 个又少了。系统确实越用越好。

**揭示的能力需求：** TUI 仪表盘（多项目视图）、审批队列（批量处理）、severity override、batch 选择、成本面板、UAT 趋势追踪、紧急通知（terminal bell）

### 旅程二：首次体验 — "从零到第一个 story 跑通"

**角色：** 同一个李明，刚装好系统，第一次使用。

**开场：** 李明克隆了项目仓库，按文档安装了 Python 依赖。他有一个现成的项目，已经有 BMAD skills 配置和几个待开发的 story 文件。

**上升：** 运行 `ato init`，系统初始化 SQLite 数据库，检测到 Claude CLI 和 Codex CLI 已安装且认证有效。他先运行 `ato plan story-001`——系统输出此 story 将经历的完整阶段（creating → validating → dev_ready → developing → reviewing → ...），让他对即将发生的事情有了清晰的心理预期。然后运行 `ato batch select`，选择了 1 个包含 2 个 story 的小 batch 作为试水。

**高潮：** 启动 TUI，看到第一个 story 进入 creating 状态。Claude agent 开始生成 story 文档，几分钟后 Codex 进入验证。他全程在 TUI 上看状态变化——creating → validating → dev_ready → developing。第一次看到 review-fix Convergent Loop 自动运行时，他意识到这和手动操作完全不同——不需要自己切换终端、复制 findings、手动触发修复。

**结局：** 从安装到第一个 story 通过 QA，花了大约 3 小时。中间只需要处理 2 次审批（merge 授权和一次超时确认）。他现在理解了系统的节奏：等待 → 判断 → 等待 → 判断。

**揭示的能力需求：** CLI 初始化命令、环境检测（CLI 安装 + 认证）、计划预览（`ato plan`）、小 batch 试水能力、清晰的状态可视化、低门槛首次体验

### 旅程三：危机恢复 — "系统崩了，但没慌"

**角色：** 李明在并行跑 3 个项目时遭遇意外。

**开场：** 下午 3 点，笔记本电脑意外重启（macOS 更新）。所有进程被杀死——5 个正在运行的 agent subprocess 全部消失。

**上升：** 重启后运行 `ato start`，系统开始崩溃恢复流程。SQLite WAL 自动回放，数据完好。系统扫描 status=running 的 5 个 task：
- 2 个 agent 已经完成并产出了 artifact → 系统自动续接流水线
- 1 个 agent 正在 review，进程已死无产出 → 系统自动重新调度
- 2 个 agent 正在 Interactive Session → 系统标记为 needs_human，等待李明决定

**高潮：** TUI 显示恢复摘要：3 个 task 自动恢复，2 个需要人工决策。李明看了一眼 2 个 Interactive Session 的上下文（worktree 路径和最后已知状态），决定一个重新启动、一个从上次 session 续接（尽力而为——依赖 CLI 自身的 session 持久化机制）。全程不到 2 分钟。

**结局：** 对比旧系统——以前崩溃后需要手动检查每个 tmux pane 的状态、手动重建 generation.lock、手动重启每个 agent。现在只需要 `ato start` + 处理 2 个审批。恢复时间从 30 分钟缩短到 2 分钟。

**揭示的能力需求：** 崩溃恢复（SQLite WAL + PID/artifact 检查）、恢复摘要展示、自动续接 vs 人工决策分流、Interactive Session 上下文保留（尽力而为）

### 旅程四：并行扩展 — "从 1 个项目到 3 个项目"

**角色：** 李明已经用系统跑了 1 个月的单项目，准备扩展到并行。

**开场：** 单项目运行期间，Memory 层已经积累了数据：平均 Convergent Loop 1.8 轮收敛、review blocking 率 12%、story 平均耗时 45 分钟。李明对系统建立了信任，决定加入第二个项目。

**上升：** 他在 TUI 中注册第二个项目仓库，选择 batch，启动流水线。系统自动将并发 semaphore 分配给两个项目的 agent——当项目 A 有 agent 在运行时，项目 B 的 agent 排队等待。他发现审批队列开始更频繁地弹出通知，但都是判断性决策，每个 30 秒内处理完。

**高潮：** 第三周加入第三个项目。TUI 仪表盘现在显示三列，每列一个项目的流水线状态。他注意到 Memory 层开始发挥作用——系统根据历史数据自动将复杂 story 分配给 Opus 模型、简单 story 用 Sonnet。

**结局：** 一个月前他一天只能推进 3-4 个 story，现在 3 个项目并行，一天推进 10+ 个 story。审批队列每天约 15-20 个决策，平均每个 30 秒，总计不到 15 分钟的人工时间。剩下的时间他在做架构设计和产品规划——终于回到了判断者的角色。

**揭示的能力需求：** 多项目注册与管理、并发资源分配（semaphore）、Memory 驱动的模型选择、多项目 TUI 视图、成本优化自动化

### 旅程五：质量守卫 — "当 agent 让你失望时"

**角色：** 李明在 UAT 中发现了 agent 遗漏的问题。

**开场：** 一个 story 通过了 Codex review 和 QA，进入 UAT。李明手动测试时发现一个边界条件 bug——当输入为空数组时 API 返回 500 而非空响应。这个问题 Codex review 应该能发现但漏掉了。

**上升：** 李明在 TUI 中打开这个 story 的 findings 面板，手动添加了一个 blocking finding："空数组输入未处理，API 返回 500"。他同时标注了分类："边界条件 / 输入验证"。系统将此 finding 记录到 Memory 层。

**高潮：** 两周后，另一个项目的类似 story（处理列表输入的 API）进入 review 阶段。李明注意到 review prompt 中自动包含了一条来自 Memory 的检查提示："注意验证空集合/空数组的边界条件（基于历史 finding）"。这一次，Codex review 成功捕获了同类问题。

**结局：** 李明查看 UAT 趋势面板——边界条件类的 UAT 缺陷从过去 4 周的 5 个降到本周的 0 个。系统从他的一次手动标注中学会了一类问题的检查模式。这就是自我改进闭环的价值——agent 会犯错，但同类错误不会犯第二次。

**揭示的能力需求：** 手动 finding 添加与分类、Memory 层模式提取、review prompt 自动强化、UAT 趋势面板、自我改进闭环的可见证据

### Journey Requirements Summary

| 能力领域 | J1 | J2 | J3 | J4 | J5 | 范围 | 依赖 |
|---------|----|----|----|----|-----|------|------|
| TUI 仪表盘（多项目视图） | ✓ | ✓ | ✓ | ✓ | ✓ | MVP→Growth | — |
| 审批队列（批量处理） | ✓ | ✓ | ✓ | ✓ | | MVP | — |
| CLI 初始化与环境检测 | | ✓ | | | | MVP | — |
| 计划预览（`ato plan`） | | ✓ | | | | MVP | 状态机 |
| 状态机可视化 | ✓ | ✓ | ✓ | ✓ | | MVP | — |
| 崩溃恢复 | | | ✓ | | | MVP | SQLite WAL |
| 恢复摘要与人工决策分流 | | | ✓ | | | MVP | 崩溃恢复 |
| Severity override | ✓ | | | | | MVP | BMAD 适配层 |
| 成本面板与追踪 | ✓ | | | ✓ | | MVP | — |
| 紧急通知（terminal bell） | ✓ | | | | | MVP | TUI |
| UAT 数据收集 | ✓ | | | | ✓ | MVP | — |
| 手动 finding 添加与分类 | | | | | ✓ | MVP | findings 表 |
| Memory 驱动参数自适应 | | | | ✓ | ✓ | Growth | UAT 数据收集 |
| 多项目注册与管理 | | | | ✓ | | Growth | — |
| 并发资源分配 | | | | ✓ | | Growth | — |
| UAT 趋势可视化 | ✓ | | | | ✓ | Growth | UAT 数据收集 |
| Review prompt 自动强化 | | | | | ✓ | Growth | Memory 层 |

## Innovation & Novel Patterns

### Detected Innovation Areas

**1. Convergent Loop 协议 — 最核心的技术创新**

现有 AI 编码工具的 review-fix 循环是"全量重审 → 全量修复"的朴素循环，容易出现评判标准漂移和 finding 集合膨胀。Convergent Loop 引入 finding 级跨轮次状态追踪和每轮 scope 严格收窄，将不确定的迭代变为有边界的收敛过程。在已知的多 agent 系统中没有先例。这个协议不限于 code-review，可通用于 validate-create、QA-fix 等所有迭代场景。

**2. 自我改进闭环 — 从"工具"到"系统智能"**

现有编排工具（Airflow、Temporal）是静态的——运行 1000 次和运行 1 次的行为完全一样。本系统通过系统智能层（可观测性 + Memory 统一），将运行历史转化为可操作的模式，自动调整系统参数。系统从"执行命令的工具"进化为"积累经验的智能体"。

**3. 两层验证 + 不可自我认证 — autoresearch 原则在 AI agent 质量控制中的首次系统化应用**

"静态分析 + 人工审查"的两层模式已存在几十年。创新之处在于将此模式应用到 AI agent 输出的质量控制上：第一层 deterministic check 使用 JSON Schema 约束（而非传统 lint 规则）确保结构正确，第二层 agent review 由不同模型/CLI 执行（创建者和验证者物理分离）。这在 AI 编码工具领域是首次将"不可自我认证"作为架构级硬约束。

**4. "编排者是代码而非 LLM" — 反共识设计选择**

当前多 agent 系统（AutoGPT、CrewAI、MetaGPT）普遍使用 LLM 作为编排者/指挥官。本系统在 LLM agent 生态中坚持用代码编排，抵抗了用 LLM 编排 LLM 的诱惑。这不是技术突破，而是从旧系统教训中得出的关键设计决策——消除了"监控 LLM 是否遵守规则"这一整类问题。验证方式是"负向验证"：不是证明它能做什么，而是证明它不需要做什么（系统运行期间无需人工检查 agent 是否遵守工具限制）。

### Market Context & Competitive Landscape

| 维度 | Devin | Copilot Workspace | CrewAI/MetaGPT | **ATO** |
|------|-------|-------------------|----------------|---------|
| Agent 数量 | 单 agent | 单 agent | 多 agent | 多 agent |
| 编排者 | LLM | LLM | LLM | **代码** |
| 人类角色 | 被替代 | 协作者 | 配置者 | **指挥者** |
| 质量门控 | 无显式 | 基本 | 无 | **三重机制** |
| 自我改进 | 无 | 无 | 无 | **闭环** |
| 本地运行 | 云端 | 云端 | 本地/云 | **本地** |
| 成熟度 | 生产可用 | 生产可用 | 活跃社区 | **架构验证阶段** |

**定位差异：** 它们让 AI 替代开发者，ATO 让人指挥 AI 团队。

### Validation Approach

| 创新点 | 验证方法 | 成功标准 |
|--------|---------|---------|
| Convergent Loop | 给定已知 5-finding review 场景的端到端测试 | ≤3 轮内闭合所有 blocking findings |
| 自我改进闭环 | Growth 阶段 UAT 趋势追踪 | 同类 UAT 缺陷随时间显著下降 |
| 两层验证 | 尝试构造绕过 deterministic check 的输入 | agent 无法绕过第一层验证 |
| 编排者是代码 | 系统运行期间行为观察（负向验证） | 无需人工检查 agent 是否遵守工具限制 |

### Risk Mitigation

| 风险 | 影响 | 缓解 |
|------|------|------|
| Convergent Loop 在某些代码模式上不收敛 | 频繁 escalate，人工负担增加 | 硬性 max_rounds=3 + 梯度降级 |
| Memory 层积累错误模式 | 系统性能反向退化 | 人工标注 override + 模式有效期机制 |
| "编排者是代码"导致灵活性不足 | 无法处理未预见的工作流变体 | 状态机可扩展设计 + Interactive Session 作为逃生通道 |
| 两层验证增加延迟 | 单 story 端到端耗时增加 | deterministic check 秒级完成；agent review 仅在必要时触发 |
| CLI 版本升级导致行为变化 | 参数语义改变破坏编排逻辑 | CLI 版本锁定 + 升级前回归验证 + adapter 层隔离变更影响 |

## AI Team OS Specific Requirements

### Project-Type Overview

AgentTeamOrchestrator 是一个本地单进程 Python 应用，兼具 CLI 命令行接口和 TUI 交互界面。CLI 用于初始化、批量操作和脚本自动化；TUI 用于日常运营监控和审批交互。系统通过声明式配置定义工作流，通过 CLI subprocess 调用外部 AI agent。

### Technical Architecture Considerations

**五层架构：**

| 层 | 职责 | 技术选择 |
|---|------|---------|
| 人类控制层 | 审批、UAT、异常决策 | TUI（Textual）+ Approval Queue |
| 编排控制面 | 状态转换、任务调度、恢复 | Python asyncio + 状态机 + TransitionQueue |
| 无状态角色工作者 | 角色化 AI agent 执行 | Claude CLI / Codex CLI subprocess |
| Git/Worktree 执行面 | 隔离的代码工作空间 | git worktree + Orchestrator 管理 |
| Artifact 与审计层 | 持久化、证据链、成本追踪 | SQLite WAL + Git 管理的 artifact 文件 |

**声明式工作流配置：**

角色、阶段和状态转换通过配置文件定义，而非硬编码。Orchestrator 从配置动态构建状态机，新增角色或阶段只需修改配置文件。

```yaml
# 示例：ato.yaml 工作流配置
roles:
  creator:
    cli: claude
    model: sonnet
  reviewer:
    cli: codex
    sandbox: read-only      # agent 不可写文件，审查结果通过 -o 输出
  fixer_escalation:
    cli: codex
    sandbox: workspace-write

phases:
  - name: creating
    role: creator
    type: structured_job
    next_on_success: validating
  - name: validating
    role: reviewer
    type: convergent_loop
    max_rounds: 3
    next_on_success: dev_ready
    next_on_failure: creating
  # ...
```

**角色边界通过 CLI 本身的隔离机制保证：** Claude 和 Codex 是不同的 CLI 用于不同角色，物理分离即为边界。Codex reviewer 在 `read-only` 沙箱中运行，agent 不可写文件，审查报告通过 CLI 的 `-o` 参数输出（CLI 层面写入，不受沙箱限制）。不额外限定工具列表，减少配置复杂度。

### Command Structure

```
ato init                    # 初始化项目（SQLite + 环境检测）
ato batch select            # PM agent 推荐 batch 方案 → 人类确认/修改
ato batch status            # 查看 batch 状态
ato plan <story-id>         # 预览 story 将经历的阶段
ato start                   # 启动编排（含崩溃恢复）
ato stop                    # 优雅停止
ato tui                     # 启动 TUI 仪表盘
ato project add <path>      # 注册新项目（Growth）
ato project list            # 列出已注册项目
ato cost report             # 成本报告
```

### Configuration Schema

```
project-root/
├── ato.yaml                # 项目级配置（工作流定义、角色、阈值）
├── schemas/                # JSON Schema 文件（findings、validation 等）
├── .ato/
│   ├── state.db            # SQLite 运行时状态
│   ├── cost.log            # 成本日志
│   └── memory/             # Memory 层持久化（Growth）
```

**关键配置项：**

| 配置 | 说明 | 默认值 |
|------|------|--------|
| `max_concurrent_agents` | 并发 agent 上限 | 4 |
| `convergent_loop.max_rounds` | Convergent Loop 最大轮次 | 3 |
| `convergent_loop.convergence_threshold` | 收敛率阈值 | 0.5 |
| `timeout.structured_job` | Structured Job 超时（秒） | 1800 |
| `timeout.interactive_session` | Interactive Session 超时（秒） | 7200 |
| `cost.budget_per_story` | 单 story 成本上限（USD） | 5.0 |
| `cost.blocking_threshold` | blocking 数量异常阈值 | 10 |
| `model_map.*` | 各阶段模型选择 | 按角色默认 |

### Scripting Support

系统核心是 headless 的——TUI 是可选的交互层。所有操作可通过 CLI 命令完成：

- **非交互执行：** `ato start --headless` 后台运行，状态写入 SQLite
- **JSON 输出：** `ato status --json` 输出结构化状态供外部工具消费
- **事件钩子：** 配置文件中定义 `on_approval_needed`、`on_story_complete` 等事件的自定义脚本

### Implementation Considerations

- **Python ≥3.11：** 需要 asyncio TaskGroup、ExceptionGroup
- **核心依赖：** aiosqlite、python-statemachine ≥3.0、Textual ≥2.0、Pydantic ≥2.0、typer
- **CLI 版本管理：** adapter 层隔离 Claude/Codex CLI 的版本差异，升级前需回归验证
- **多项目隔离（Growth）：** 每个项目独立的 SQLite 数据库 + 共享 Memory 层

## Project Scoping & Phased Development

### MVP Strategy & Philosophy

**MVP 方式：** Problem-Solving MVP — 证明"单项目端到端自动化流水线 + 人类判断"模式可行。

**核心验证假设：**
1. CLI subprocess 编排 AI agent 足够可靠
2. Convergent Loop 能在 3 轮内收敛大部分 review-fix 循环
3. 声明式配置能灵活定义工作流
4. 人只需在审批节点介入，其余时间系统自主推进

**资源需求：** 单人开发，核心编排逻辑 ≤1500 行（不含 TUI/CLI 交互层），预估总代码量 ~1700-1900 行，核心依赖 6 个 Python 包。

### MVP Feature Set (Phase 1)

**支持的用户旅程：** J2（首次体验）、J3（危机恢复）的完整路径；J1（日常运营）的单项目子集。

**核心流程（单项目端到端）：**

```
batch select（PM agent 推荐 → 人类确认）
  → story creating（Claude, Structured Job）
  → story validating（Codex, Convergent Loop）
  → development（Interactive Session — 系统启动 worktree + agent session，人参与协作，完成后手动提交）
  → code review + fix（Codex review + Claude fix, Convergent Loop）
  → QA generation + execution（Structured Job）
  → UAT（人类在 worktree 中手动测试，通过 TUI 提交结果）
  → merge（需人类授权）
  → regression（Structured Job）
```

**Must-Have 能力：**

| 能力 | 说明 |
|------|------|
| 声明式配置引擎 | YAML schema 定义、配置解析与验证、配置→状态机动态构建 |
| 编排核心 | 声明式状态机 + TransitionQueue + SubprocessManager |
| Claude CLI adapter | `-p` 调用、JSON 输出解析、session 管理、成本提取 |
| Codex CLI adapter | `exec` 调用、JSONL 解析、`-o` 输出收集、sandbox 控制 |
| BMAD 适配层（全量） | code-review、story-validation、architecture-review、QA-report 四个 skill 的 Markdown → JSON 解析 |
| Convergent Loop | finding 级追踪、scope 收窄、收敛判定、escalate |
| Approval Queue | batch 选择、merge 授权、超时处理、异常 escalation、UAT 结果提交 |
| SQLite 持久化 | WAL 模式、stories/tasks/findings/approvals/cost_log 表 |
| 崩溃恢复 | PID/artifact 检查、自动续接/重调度/人工决策分流 |
| Worktree 管理 | 创建、验证、清理、merge queue |
| Interactive Session | 启动独立终端 + agent session、注册 PID/worktree、计时、收 artifact |
| TUI（最简版） | story 状态列表 + 审批交互（无成本面板、无趋势图、无 finding 详情） |
| CLI 命令 | init、batch select/status、plan、start、stop、tui |
| 计划预览 | `ato plan <story-id>` 输出阶段序列 |
| 基线数据收集 | 耗时、成本、收敛轮次、人工介入次数的结构化记录 |
| 紧急通知 | terminal bell |

### Post-MVP Features

**Phase 2 — Growth（按优先级排序）：**

| 优先级 | 能力 | Done 定义 | 依赖 |
|--------|------|-----------|------|
| 1 | 梯度降级完整实现 | Claude fix 3 轮未收敛 → Codex 攻坚自动触发 → 仍失败自动降级为 Interactive Session | MVP Convergent Loop |
| 2 | 并行执行 + 多项目支持 | ≥2 个项目各 ≥2 个 story 同时在不同 worktree 中并行推进，无状态冲突 | MVP 编排核心 |
| 3 | 系统智能层 Memory | 系统自动根据历史数据推荐模型选择，且推荐准确率被人工 override 比例验证 | 并行执行产生的数据积累 |
| 4 | TUI 增强 + 成本优化 | TUI 含成本面板、UAT 趋势图、finding 详情、多项目视图；模型分级策略生效并可量化成本节省 | Memory 层 |
| 5 | 手动 finding 添加与分类 | 用户可在 TUI 中为任意 story 添加 finding 并标注分类，finding 入库并可被 Memory 层消费 | MVP findings 表 |
| 6 | Severity override | 用户可在 TUI 中修改任意 finding 的 severity，修改后系统行为相应调整 | BMAD 适配层 |

**Phase 3 — Vision：**

| 能力 | 状态 |
|------|------|
| 系统智能成熟（UAT 缺陷趋向 0） | 需 Growth 数据验证 |
| 领域扩展（非软件交付场景） | 需后续调研 |
| CLI → SDK 升级路径 | 需获取 API Key |
| 团队协作（多用户） | 需后续调研 |
| headless 模式 + JSON 输出 + 事件钩子 | 基于 MVP headless 核心扩展 |

### Risk Mitigation Strategy

**技术风险：**

| 风险 | 概率 | 缓解 |
|------|------|------|
| CLI subprocess 调用不够可靠（超时、解析失败） | 中 | adapter 层重试 1 次 + 错误分类处理矩阵 |
| 声明式配置的表达力不足以覆盖复杂工作流 | 低 | 配置支持条件分支 + Interactive Session 作为逃生通道 |
| BMAD 适配层在某些 skill 输出上解析失败 | 中 | LLM 语义解析天然鲁棒 + 解析失败时降级为人工审阅 |
| Convergent Loop 收敛率低于预期 | 中 | 硬性 max_rounds + 梯度降级（Growth 补全） |

**市场风险：**

| 风险 | 缓解 |
|------|------|
| 只有设计者自己能用，不具通用性 | 声明式配置保证工作流可定制；MVP 先验证核心假设 |
| CLI 工具版本更新破坏兼容性 | adapter 层隔离 + 版本锁定 + 升级前回归验证 |

**资源风险：**

| 风险 | 缓解 |
|------|------|
| 单人开发进度不及预期 | MVP scope 已压缩到最小可用；Growth 按优先级增量交付 |
| Token 成本超出预算 | MVP 阶段收集成本基线；Growth 阶段实施模型分级优化 |

## Functional Requirements

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

## Non-Functional Requirements

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
