---
stepsCompleted: ['step-01-validate-prerequisites', 'step-02-design-epics', 'step-03-create-stories', 'step-04-final-validation']
inputDocuments:
  - '_bmad-output/planning-artifacts/prd.md'
  - '_bmad-output/planning-artifacts/architecture.md'
  - '_bmad-output/planning-artifacts/ux-design-specification.md'
---

# AgentTeamOrchestrator - Epic Breakdown

## Overview

This document provides the complete epic and story breakdown for AgentTeamOrchestrator, decomposing the requirements from the PRD, UX Design if it exists, and Architecture requirements into implementable stories.

## Requirements Inventory

### Functional Requirements

**工作流编排:**

- FR1: 系统可从声明式配置文件（YAML）动态构建工作流状态机，定义角色、阶段、转换规则
- FR2: 系统可验证配置文件的正确性，拒绝无效的工作流定义（如循环依赖、缺失阶段）
- FR3: 系统可按配置的阶段顺序自动推进 story 的生命周期（creating → validating → ... → done）
- FR4: 系统可保证并发完成的任务不会导致状态冲突
- FR5: 操作者可通过 `ato plan <story-id>` 预览某个 story 将经历的完整阶段序列

**AI Agent 协作:**

- FR6: 系统可通过 CLI subprocess 调用 Claude Code 执行创建、实现、修复等任务，并收集 JSON 格式的结构化输出
- FR7: 系统可通过 CLI subprocess 调用 Codex CLI 执行审查、验证等任务（prompt 指示 agent 使用对应 BMAD skill），并从 JSONL 事件流捕获 agent 输出（text_result）
- FR8: 系统可根据配置为不同角色指定 CLI 类型、沙箱级别和模型选择
- FR9: 系统可管理 agent session（创建、续接、fork），支持 Convergent Loop 内短循环的 session resume
- FR10: 系统可启动 Interactive Session（独立终端窗口），注册其 PID、worktree 路径和启动时间，人与 agent 直接在终端中协作
- FR11: 系统可将 agent 执行 BMAD skill 后的 Markdown 输出（通过 CLI 流捕获的 text_result）经 BmadAdapter 解析为结构化 JSON（覆盖 code-review、story-validation、architecture-review、QA-report 四个 skill）
- FR12: PM agent 可分析 epic/story 的优先级和依赖关系，生成推荐的 batch 方案供操作者选择
- FR53: 系统可从 task 输出中提取结构化工作记忆摘要（Context Briefing），作为跨 task 边界的 fresh session 输入

**质量门控:**

- FR13: 系统可执行 Convergent Loop 协议：review（agent 执行 bmad-code-review skill）→ BmadAdapter 解析 → finding 入库 → fix → re-review（scope 收窄，agent 仍使用 bmad-code-review skill）→ 收敛判定或 escalate
- FR14: 系统可在 SQLite 中追踪每个 finding 的跨轮次状态（open → closed / still_open / new）
- FR15: 系统可在每轮 re-review 时自动收窄 scope，仅验证上轮 open findings 的闭合状态和新引入问题
- FR16: 系统可执行 deterministic validation check（JSON Schema 结构验证），作为 agent review 之前的第一层验证
- FR17: 系统可在 Convergent Loop 达到 max_rounds 后自动 escalate（MVP: 通知人工；Growth: 梯度降级）
- FR18: 系统可按配置的 severity 判定规则（blocking vs suggestion）分类 findings，blocking 数量超阈值时请求人工确认

**人机协作:**

- FR19: 操作者可在审批队列中查看所有待决策事项（batch 选择、merge 授权、超时处理、异常 escalation、UAT 结果）
- FR20: 操作者可对每个待审批事项做出决策，决策记录持久化（含时间戳和选择理由）
- FR21: 操作者可在 UAT 阶段通过 TUI 提交测试结果（通过/不通过 + 描述）
- FR22: 系统可在需要紧急人工介入时发出 terminal bell 通知（如 regression 失败冻结 merge queue）
- FR23: 操作者可选择 Interactive Session 的恢复策略（重新启动 / 从上次 session 续接 / 放弃）

**状态管理与恢复:**

- FR24: 系统可将所有运行时状态（stories、tasks、findings、approvals、cost_log）持久化到 SQLite（WAL 模式）
- FR25: 系统可在进程崩溃后自动恢复：扫描 running 状态的 task，根据 PID 存活和 artifact 存在情况分类处理（自动续接 / 重新调度 / 请求人工决策）
- FR26: 系统可在恢复后向操作者展示恢复摘要（自动恢复数量 + 需人工决策数量）
- FR27: 系统可记录每次 agent 调用的结构化数据（耗时、成本、token 用量、收敛轮次），用于基线数据收集
- FR28: 系统可记录每次 agent 调用的成本（Claude: 直接读取 total_cost_usd；Codex: 从 token 数计算）

**工作空间管理:**

- FR29: 系统可为每个 story 创建独立的 git worktree，在 worktree 中执行 agent 任务
- FR30: 系统可在 story 完成后清理 worktree
- FR31: 系统可管理 merge queue，按顺序执行 rebase 和 merge（需人类授权）
- FR32: 系统可在 regression 失败时自动冻结 merge queue，阻止后续 merge
- FR52: 系统可在 worktree rebase 产生冲突时调度 agent 自动解决，解决后重新进入 review 流程；解决失败 escalate 给操作者

**配置与初始化:**

- FR33: 操作者可通过 `ato init` 初始化项目（创建 SQLite 数据库、检测 CLI 安装和认证状态）
- FR34: 系统可检测 Claude CLI 和 Codex CLI 的安装状态和认证有效性，报告环境就绪情况
- FR35: 操作者可通过配置文件设置系统参数（并发上限、超时阈值、成本上限、Convergent Loop 参数等）

**可视化与监控:**

- FR36: 操作者可通过 TUI 查看所有 story 的当前状态、所在阶段及 Convergent Loop 进度信息（当前轮次、open findings 数量）
- FR37: 操作者可通过 TUI 与审批队列交互（查看详情、做出决策）
- FR38: 操作者可通过 `ato batch status` 查看当前 batch 的整体进度
- FR39: 操作者可通过 `ato start` 启动编排系统（含自动崩溃恢复）
- FR40: 操作者可通过 `ato stop` 优雅停止编排系统

**质量闭环与审计:**

- FR48: 系统可在 UAT 不通过时自动将 story 退回到 fix 阶段，重新进入 Convergent Loop
- FR49: 操作者可查看任意 story 的完整执行历史（哪个 agent 在什么时间执行了什么任务，产出了什么 artifact）
- FR50: 系统可向操作者展示任务失败的原因（认证过期、超时、解析错误等）和可选的恢复操作（重试、跳过、escalate）
- FR51: 配置变更需重启系统生效（MVP）

**Growth 阶段能力（Phase 2）:**

- FR41: 系统可同时编排多个项目的流水线，每个项目独立的状态存储（Growth P2）
- FR42: 系统可执行梯度降级：Claude fix 未收敛 → Codex 攻坚 → Interactive Session（Growth P1）
- FR43: 系统可从历史运行数据中提取模式，自动调整系统参数（Growth P3）
- FR44: 操作者可手动添加 finding 并标注分类，供 Memory 层消费（Growth P4）
- FR45: 操作者可在 TUI 中 override 任意 finding 的 severity（Growth P4）
- FR46: 系统可在 review prompt 中自动注入来自 Memory 的历史检查提示（Growth P3）
- FR47: 操作者可通过 TUI 查看成本面板、UAT 趋势图和 finding 详情（Growth P4）

### NonFunctional Requirements

**性能:**

- NFR1: 崩溃恢复（SQLite 扫描 + PID/artifact 检查 + 恢复决策）在 MVP 阶段 ≤30 秒，成熟期 ≤10 秒
- NFR2: 状态转换处理（从 agent 完成到下一阶段 agent 启动）≤5 秒
- NFR3: TUI 状态刷新间隔 ≤5 秒，单次刷新渲染 ≤500ms
- NFR4: Deterministic validation check（JSON Schema 验证）≤1 秒
- NFR5: 配置解析与状态机构建（`ato start` 启动时间）≤3 秒

**可靠性:**

- NFR6: SQLite WAL 模式保证进程崩溃后数据零丢失
- NFR7: 系统重启后可自动恢复所有可恢复的 task（有 artifact 或 PID 存活的），无需人工重建状态
- NFR8: 单次 agent CLI 调用失败时，系统自动重试 1 次后再 escalate
- NFR9: Convergent Loop 在任何情况下 ≤max_rounds 轮后终止（不会无限循环）
- NFR10: Merge queue 冻结后，系统保证不会在 broken main 上继续 merge

**集成:**

- NFR11: Claude CLI adapter 和 Codex CLI adapter 通过隔离层封装，CLI 版本升级只影响 adapter 层，不影响编排核心
- NFR12: BMAD 适配层基于 LLM 语义解析，对 BMAD skill 输出格式的小幅变化具有鲁棒性
- NFR13: 系统兼容 macOS 和 Linux 环境下的 git worktree 操作
- NFR14: 系统正确处理 CLI 的各类退出码和错误输出（认证过期、rate limit、超时等），分类到对应的恢复策略

### Additional Requirements

**Starter Template 与项目初始化:**

- 使用 `uv init` + 手动配置初始化项目（Python ≥3.11, uv 包管理, hatchling 构建后端）
- 项目初始化（uv init + 依赖安装 + 目录结构 + pre-commit 配置）作为第一个实现 story
- 核心依赖：aiosqlite、python-statemachine ≥3.0、Textual ≥2.0、Pydantic ≥2.0、typer、structlog
- 开发依赖：pytest、pytest-asyncio、ruff、mypy、pre-commit

**架构决策驱动的技术需求:**

- Decision 1: Orchestrator 和 TUI 始终为独立进程（`ato start` 启动后台 Orchestrator，`ato tui` 启动前台 TUI）
- Decision 2: TUI 直接写 SQLite + 轻量 nudge 通知（os.pipe() 或 SIGUSR1）+ 2-5 秒定期轮询兜底
- Decision 3: 配置决定"做什么"（角色/阶段/阈值），引擎决定"怎么做"（CL 协议/恢复流程/错误矩阵）
- Decision 4: Interactive Session 双通道完成检测 — `ato submit <story-id>` CLI 命令 + TUI 手动标记
- Decision 5: PRAGMA user_version + 启动时自动迁移，迁移函数在 `models/migrations.py`
- Decision 6: structlog 作为核心依赖，JSON 输出到 `.ato/logs/ato.log`，协程级上下文绑定
- Decision 7: 优雅停止标记法 — `ato stop` 标记 paused，崩溃后 status=running 触发恢复
- Decision 8: 状态机测试覆盖 — transition 100% 单元测试 + 4 条关键路径集成测试
- Decision 9: CLI Adapter 契约守护 — Snapshot fixture + 定期冒烟测试 + 版本追踪
- Decision 10: 分层 Preflight Check 协议 — 系统环境/项目结构/编排前置 Artifact 三层检查

**实现模式与强制规则:**

- asyncio subprocess 三阶段清理协议（SIGTERM → wait → SIGKILL → wait）
- SQLite 连接策略（TransitionQueue 长连接、Orchestrator 轮询短连接、TUI 短连接 + 立即 commit）
- python-statemachine 3.0 async 集成（创建后 await activate_initial_state()、PersistentModel 不直写 SQLite）
- Pydantic v2 验证模式（MVP 全部走 model_validate，不用 model_construct）
- 模块间错误传播：adapter → subprocess_mgr → core → TUI，错误不静默吞掉
- 异常层次：ATOError → CLIAdapterError / StateTransitionError / RecoveryError / ConfigError
- 模块依赖只允许向下：adapters/ 不依赖 core，tui/ 不依赖 core（通过 SQLite 解耦）

**项目结构规范:**

- 代码组织：src/ato/ 主包，adapters/、models/、tui/ 子包
- 测试组织：tests/unit/、tests/integration/、tests/smoke/、tests/fixtures/
- 运行时目录：.ato/（state.db、orchestrator.pid、logs/）
- 配置文件：ato.yaml（项目级）、schemas/（JSON Schema）

### UX Design Requirements

- UX-DR1: 实现 ThreeQuestionHeader 自定义组件 — 固定顶栏回答"系统正常吗？需要我做什么？花了多少？"，含系统状态/审批计数/成本摘要/更新时间四个区域，支持三种终端宽度的响应式适配（180+/140-179/100-139列）
- UX-DR2: 实现 ApprovalCard 自定义组件 — 审批项紧凑展示（类型图标 + story ID + 一句话摘要 + 推荐操作 + 风险指示），支持折叠/展开态，5 种审批类型→推荐操作映射（merge/timeout/budget/blocking/regression）
- UX-DR3: 实现 HeartbeatIndicator 自定义组件 — 动态 spinner（◐◓◑◒循环）+ 经过时间 + CL 轮次进度 + 进度条 + 成本，四种状态颜色（活跃青色/即将超时琥珀/已超时红/已完成绿）
- UX-DR4: 实现 ConvergentLoopProgress 自定义组件 — 轮次可视化（●已完成/◐当前/○未执行）+ findings 统计 + 收敛率百分比 + 当前状态描述
- UX-DR5: 实现 ExceptionApprovalPanel 自定义组件 — 异常审批专用面板，$error 红色边框，多选一选项（1/2/3），支持 regression_failure/critical_timeout/cascade_failure 三种异常类型
- UX-DR6: 实现 PreflightOutput CLI 组件 — 使用 rich 库（非 Textual）渲染 `ato init` 的三层检查结果，四级状态编码（✔通过绿/✖阻断红/⚠警告琥珀/ℹ信息灰）
- UX-DR7: 实现 TCSS 深色主题 — 基于 Dracula 色板变体的 9 个语义色彩变量（$success #50fa7b / $warning #f1fa8c / $error #ff5555 / $info #8be9fd / $accent #bd93f9 / $muted #6272a4 / $text #f8f8f2 / $background #282a36 / $surface #44475a），所有语义色对比度 ≥ 4.5:1
- UX-DR8: 实现三色信号灯状态编码系统 — 颜色 + Unicode 图标 + 文字标签三重编码（●running / ◐active / ◆awaiting / ✖failed / ✔done / ⏸frozen / ℹinfo），色盲友好不依赖单一通道
- UX-DR9: 实现响应式面板布局 — lazygit 三面板（左列表+右上详情+右下操作）用于宽终端（140+列）+ Tab 视图降级用于窄终端（100-139列）+ <100列降级提示，实时响应 resize 事件
- UX-DR10: 实现快捷键体系 — y/n 审批、1/2/3 多选、Enter/ESC 钻入返回、Tab/Shift-Tab 面板切换、f/c/h/l 子视图展开、/ 搜索、q 退出；Footer 始终显示当前上下文可用快捷键
- UX-DR11: 实现审批排序规则 — 左面板按 AWAITING → ACTIVE → BLOCKED → DONE 自动排序，审批项自动浮顶，选中项右面板自动联动
- UX-DR12: 实现通知体系 — 四级优先级（紧急=regression失败bell+macOS+顶栏闪烁 / 常规=审批等待bell / 静默=阶段推进无通知 / 里程碑=story完成bell一声），自包含通知含决策信息和快捷命令
- UX-DR13: 实现空状态与引导设计 — 所有空状态提供下一步操作指引文字，如"尚无 story。运行 `ato batch select` 选择第一个 batch"
- UX-DR14: 实现反馈模式 — 审批提交即时中间状态"已提交，等待处理"、状态更新 1s 闪烁高亮、成功 ✔ 绿色 2s 消失、失败 ✖ 红色+恢复建议需用户按键消除
- UX-DR15: 实现 StoryStatusLine 自定义组件 — 一行浓缩 story 全部关键信息（状态图标 + ID + 阶段 + 进度条 + 耗时 + 成本），控制在 100 列内不换行
- UX-DR16: 实现 `/` 搜索命令面板 — 支持 story ID 直达、命令搜索、审批跳转、模糊匹配，Enter 跳转 / ESC 取消
- UX-DR17: 实现渐进钻入导航模式 — 主屏概览(第1层) → Enter 进入 Story 详情(第2层) → f/c/h/l 展开子视图(第2.5层) → l 弹出独立终端查看 agent 实时日志(第3层，有意摩擦)，≤3 层可达任何信息
- UX-DR18: 实现 Story 详情页 — 状态流可视化 + findings 摘要/文件变更列表/成本明细/执行历史四个子视图，通过 f/c/h/l 快捷键切换
- UX-DR19: 实现崩溃恢复 CLI 输出 — 人话版恢复摘要，首行"✔ 数据完整性检查通过"消除焦虑 + 自动恢复数量 + 需人工决策数量及选项（重启/续接/放弃）
- UX-DR20: 实现异常审批界面 — 四种异常类型（regression 失败/成本超限/agent 超时/blocking 数量异常）各有专用面板，含"发生了什么 + 影响范围 + 你的选项"三要素

### FR Coverage Map

| FR | Epic | 简述 |
|----|------|------|
| FR1 | Epic 1 | 声明式配置→状态机构建 |
| FR2 | Epic 1 | 配置验证 |
| FR3 | Epic 2A | 自动推进 story 生命周期 |
| FR4 | Epic 2A | 并发任务状态不冲突 |
| FR5 | Epic 1 | ato plan 预览阶段序列 |
| FR6 | Epic 2B | Claude CLI subprocess 调用 |
| FR7 | Epic 2B | Codex CLI subprocess 调用 |
| FR8 | Epic 2B | 角色→CLI/沙箱/模型映射 |
| FR9 | Epic 2B | Agent session 管理 |
| FR10 | Epic 2B | Interactive Session 启动与注册 |
| FR11 | Epic 2B | BMAD Markdown→JSON 适配层 |
| FR12 | Epic 2B | PM agent batch 推荐 |
| FR13 | Epic 3 | Convergent Loop 协议执行 |
| FR14 | Epic 3 | Finding 跨轮次状态追踪 |
| FR15 | Epic 3 | Re-review scope 收窄 |
| FR16 | Epic 3 | Deterministic validation (JSON Schema) |
| FR17 | Epic 3 | Max_rounds escalate |
| FR18 | Epic 3 | Severity 分类 + blocking 阈值 |
| FR19 | Epic 4 | 审批队列展示 |
| FR20 | Epic 4 | 审批决策持久化 |
| FR21 | Epic 4 | UAT 结果提交 |
| FR22 | Epic 4 | Terminal bell 紧急通知 |
| FR23 | Epic 4 | Interactive Session 恢复策略 |
| FR24 | Epic 1 | SQLite WAL 持久化 |
| FR25 | Epic 5 | 崩溃后自动恢复 |
| FR26 | Epic 5 | 恢复摘要展示 |
| FR27 | Epic 2B | Agent 调用结构化数据记录 |
| FR28 | Epic 2B | Agent 调用成本记录 |
| FR29 | Epic 2B | Git worktree 创建 |
| FR30 | Epic 2B | Worktree 清理 |
| FR31 | Epic 4 | Merge queue 管理 |
| FR32 | Epic 4 | Regression 失败冻结 merge queue |
| FR33 | Epic 1 | ato init 初始化 |
| FR34 | Epic 1 | CLI 安装/认证检测 |
| FR35 | Epic 1 | 配置文件参数设置 |
| FR36 | Epic 6 | TUI story 状态/阶段/CL 进度 |
| FR37 | Epic 6 | TUI 审批交互 |
| FR38 | Epic 2B | ato batch status |
| FR39 | Epic 2A | ato start 启动编排 |
| FR40 | Epic 2A | ato stop 优雅停止 |
| FR41 | Epic 7 | 多项目并行 (Growth) |
| FR42 | Epic 7 | 梯度降级 (Growth) |
| FR43 | Epic 7 | Memory 层参数自适应 (Growth) |
| FR44 | Epic 7 | 手动 finding 添加 (Growth) |
| FR45 | Epic 7 | Severity override (Growth) |
| FR46 | Epic 7 | Review prompt 自动强化 (Growth) |
| FR47 | Epic 7 | TUI 增强 (Growth) |
| FR48 | Epic 4 | UAT 不通过退回 fix |
| FR49 | Epic 5 | Story 执行历史查看 |
| FR50 | Epic 4 | 任务失败原因+恢复选项 |
| FR51 | Epic 1 | 配置变更需重启 |
| FR52 | Epic 4 | Worktree rebase 冲突解决 |
| FR53 | Epic 2B | Context Briefing 提取 |

**覆盖统计：** 53/53 FRs 全部映射，无遗漏。

### Critical Path & Dependency DAG

```
Epic 1 → Epic 2A → Epic 2B ──→ Epic 3 → Epic 4 → Epic 5
                    ↘                              ↗
                     Epic 6 (TUI, depends on 2A+4)
Epic 7 (Growth, independent)
```

**Story 级串行依赖链：**

| 串行链 | Stories |
|--------|---------|
| 基础设施 | 1.1 → 1.2 → 1.3 → 1.4a → 1.4b → 1.5 |
| 编排核心 | 1.2 → 2A.1 → 2A.2 → 2A.3 |
| Agent 集成 | 2A.1 → 2B.1 → 2B.2 |
| BMAD/Worktree | 2A.1 → 2B.3, 2A.1 → 2B.4 |
| Batch | 1.2 → 2B.5（与编排核心并行） |
| Interactive | 2B.1 → 2B.6 |
| 质量门控 | 3.1 → 3.2a → 3.2b → 3.2c → 3.2d → 3.3 |
| 人机协作 | 4.1 → 4.2 → 4.3 → 4.4 → 4.5 |
| 崩溃恢复 | 5.1a → 5.1b → 5.2 |
| TUI | 6.1a → 6.1b → 6.2a → 6.2b → 6.3a → 6.3b → 6.4 → 6.5 |

**可并行分组：**
- Epic 2B stories (2B.3, 2B.4, 2B.5) 可在 2A.1 完成后并行
- Epic 3 和 Epic 6.1 可在 Epic 2 完成后并行
- Epic 5 可在 Epic 2A 完成后独立进行

## Epic List

### Epic 1: 项目初始化与配置引擎
用户可以通过 `ato init` 初始化项目，通过 `ato plan` 预览工作流阶段，配置工作流参数，验证环境（CLI、认证、前置 artifact）完全就绪，获得清晰的就绪/缺失反馈。
**FRs:** FR1, FR2, FR5, FR24, FR33, FR34, FR35, FR51
**NFRs:** NFR5, NFR6
**UX:** UX-DR6
**附加需求:** Starter template (uv init + 依赖)、SQLite schema + migrations、structlog 基础配置、Preflight Check 三层协议

### Epic 2A: 编排引擎核心
操作者可以启动/停止编排器，观察 story 在状态机中自动推进，状态转换按序串行执行且不冲突。
**FRs:** FR3, FR4, FR39, FR40
**NFRs:** NFR2
**附加需求:** StoryLifecycle 状态机 + TransitionQueue + Orchestrator 事件循环 + ato start/stop

### Epic 2B: Agent 集成与工作空间
操作者可以看到 AI agent 被自动调度执行任务，结果被结构化收集，story 在独立 worktree 中隔离执行，batch 选择和 Interactive Session 完整可用。
**FRs:** FR6, FR7, FR8, FR9, FR10, FR11, FR12, FR27, FR28, FR29, FR30, FR38, FR53
**NFRs:** NFR8, NFR11, NFR12, NFR13, NFR14
**附加需求:** SubprocessManager、Claude/Codex CLI Adapters (含 snapshot fixture)、BMAD 适配层、Worktree 管理、Batch 选择、Interactive Session + Context Briefing

### Epic 3: Convergent Loop 质量门控
系统自动执行 review-fix 收敛循环，追踪每个 finding 的跨轮次状态，确保代码质量在 max_rounds 内收敛或 escalate，用户可以信任自动化质量结果。
**FRs:** FR13, FR14, FR15, FR16, FR17, FR18
**NFRs:** NFR4, NFR9
**附加需求:** Convergent Loop 协议、finding 级状态追踪、scope 收窄机制、JSON Schema deterministic validation、severity 分类、最小 approval 写入能力（自包含 escalation）

### Epic 4: 人机协作与审批队列
用户可以在审批队列中高效处理所有判断性决策（batch 选择确认、merge 授权、UAT 结果、超时/异常处理），系统在需要时通过通知打断用户，merge queue 安全管理。
**FRs:** FR19, FR20, FR21, FR22, FR23, FR31, FR32, FR48, FR50, FR52
**NFRs:** NFR10
**UX:** UX-DR12, UX-DR19 (部分), UX-DR20 (部分)
**附加需求:** Approval Queue、nudge 通知机制、merge queue（含 regression 冻结 + regression 测试执行）、Interactive Session 完成检测（ato submit）、UAT 结果提交

### Epic 5: 崩溃恢复与可观测性
系统意外终止后，用户运行 `ato start` 即可自动恢复，数据零丢失，获得人话版恢复摘要，可查看任意 story 的完整执行历史和成本数据。
**FRs:** FR25, FR26, FR49
**NFRs:** NFR1, NFR7
**UX:** UX-DR19
**附加需求:** PID/artifact 检查与分类处理（自动续接/重调度/人工决策）、优雅停止标记法、恢复摘要、执行历史查看

### Epic 6: TUI 指挥台
用户拥有一个信息密集的终端仪表盘——三问首屏一眼掌握全局，lazygit 三面板布局高效导航，快捷键驱动所有操作，审批/状态/成本/详情在一个界面内完成。
**FRs:** FR36, FR37
**NFRs:** NFR3
**UX:** UX-DR1 至 UX-DR5, UX-DR7 至 UX-DR18
**附加需求:** 6 个自定义 Textual 组件、TCSS 深色主题、响应式布局（宽/窄终端）、快捷键体系、搜索命令面板、渐进钻入导航、Story 详情页

### Epic 7: Growth — 多项目并行与系统智能 (Phase 2)
用户可以同时编排多个项目的流水线，系统从历史数据中学习并自动优化参数，梯度降级确保复杂问题不阻塞流程。
**FRs:** FR41, FR42, FR43, FR44, FR45, FR46, FR47
**附加需求:** 多项目注册与状态隔离、梯度降级完整实现、Memory 层、TUI 增强（成本面板、UAT 趋势图、finding 详情）、手动 finding 添加与 severity override

### Epic 10: Runtime Reliability Hardening
系统在 CLI 已返回 result、worker PID 已退出、worktree gate 失败、transition ack timeout 或 BMAD parser fallback 超时时，仍能把任务收敛到 completed / failed / retryable approval / needs_human_review 的可恢复状态，避免 2026-04-08 事故中的静默卡死和审批死胡同。
**FRs:** FR19, FR20, FR24, FR25, FR27, FR28, FR31, FR50, FR52
**NFRs:** NFR1, NFR2, NFR7, NFR8, NFR11, NFR12, NFR14
**附加需求:** Terminal finalizer、dead PID watchdog、Claude result-first adapter 语义、TransitionQueue ack timeout 语义、preflight clean-or-approval 不变量、BMAD deterministic fast-path、merge queue approval/lock 顺序、事故回归测试

## Epic 1: 项目初始化与配置引擎

用户可以通过 `ato init` 初始化项目，通过 `ato plan` 预览工作流阶段，配置工作流参数，验证环境（CLI、认证、前置 artifact）完全就绪，获得清晰的就绪/缺失反馈。

### Story 1.1: 项目脚手架与开发工具链

As a 开发者,
I want 用 `uv init` 初始化项目并配置完整的开发工具链,
So that 项目结构就绪、依赖可安装、代码质量工具可运行。

**Acceptance Criteria:**

**Given** 一个空的项目目录
**When** 执行 `uv init` 并安装所有依赖
**Then** pyproject.toml 包含所有核心依赖（aiosqlite, python-statemachine>=3.0, textual>=2.0, pydantic>=2.0, typer, structlog）和开发依赖（pytest, pytest-asyncio, ruff, mypy, pre-commit）
**And** `uv.lock` 生成并可提交到 VCS

**Given** 项目已初始化
**When** 检查目录结构
**Then** 存在完整的 `src/ato/` 包结构（含 `adapters/`, `models/`, `tui/` 子包及所有 `__init__.py`），`tests/`（含 `unit/`, `integration/`, `smoke/`, `fixtures/`），`schemas/` 目录

**Given** 开发工具链已配置
**When** 执行 `uv run ruff check src/` 和 `uv run mypy src/`
**Then** 所有检查通过，无错误

**Given** pre-commit 已配置
**When** 执行 `pre-commit run --all-files`
**Then** ruff check + ruff format + mypy hooks 全部通过

**Given** structlog 已配置
**When** 导入 `ato.logging` 模块并调用 `configure_logging()`
**Then** structlog 以 JSON 格式输出到 stderr，包含 ISO 时间戳和日志级别

**Given** pyproject.toml 配置完整性
**When** 检查工具配置
**Then** 包含 `[tool.ruff]`（规则集、行宽、目标 Python 版本）、`[tool.mypy]`（strict mode、忽略缺失 import）、`[tool.pytest.ini_options]`（asyncio_mode=auto）、`[tool.ato]`（CLI 版本追踪占位）
**And** `.pre-commit-config.yaml` 包含 ruff-pre-commit + mypy hooks 的具体版本锁定

### Story 1.2: 操作者可确认 story 和 task 状态在崩溃后零丢失

As a 操作者,
I want 系统将 story 和 task 的运行时状态持久化到 SQLite（WAL 模式），崩溃后数据零丢失,
So that 意外中断后无需手动重建状态，系统可自动恢复。

**Acceptance Criteria:**

**Given** 数据库尚未创建
**When** 调用 `init_db(db_path)` 函数
**Then** 创建 SQLite 数据库并设置 `journal_mode=WAL`、`busy_timeout=5000`、`synchronous=NORMAL`
**And** 创建 `stories` 和 `tasks` 表，以及通用 `approvals` 表（供后续 story 按需写入 approval 记录）
**And** `PRAGMA user_version` 设置为当前 schema 版本号

**Given** 数据库 schema 版本低于代码版本
**When** 调用 `run_migrations(db, current_version, target_version)`
**Then** 按序执行迁移函数，每步更新 `user_version`
**And** 迁移失败时抛出 `RecoveryError`，不破坏已有数据

**Given** SQLite WAL 模式已启用
**When** 写入操作进行到一半时进程被杀死
**Then** 重启后 WAL 自动回放，已提交事务的数据完整存在
**And** 操作者运行 `ato start` 后可看到崩溃前的 story/task 状态完好

**Given** models/schemas.py 中的 Pydantic models
**When** 对 StoryRecord、TaskRecord、ApprovalRecord 调用 `model_validate()`
**Then** 外部输入经过严格类型验证，非法数据被拒绝并给出清晰错误信息

**Given** 任意数据库操作
**When** 使用参数化查询执行 SQL
**Then** 不存在手动拼接 SQL 的代码路径

### Story 1.3: 声明式配置引擎

As a 操作者,
I want 通过 ato.yaml 声明式定义工作流（角色、阶段、转换规则、阈值参数）,
So that 系统行为可通过配置文件定制而非修改代码。

**Acceptance Criteria:**

**Given** 项目根目录存在 `ato.yaml`
**When** 调用 `load_config(config_path)` 函数
**Then** 返回经 Pydantic Settings 验证的配置对象，包含角色定义、阶段序列、转换规则、超时阈值、并发上限、Convergent Loop 参数、成本上限
**And** 配置解析耗时 ≤3 秒（NFR5）

**Given** ato.yaml 包含无效定义（如循环依赖的阶段转换、缺失的必填字段、引用不存在的角色）
**When** 调用 `load_config()`
**Then** 抛出 `ConfigError`，错误信息明确指出无效位置和原因
**And** 系统拒绝启动

**Given** 配置已加载
**When** 调用 `build_phase_definitions(config)` 函数
**Then** 返回阶段定义列表（含名称、角色、类型、转换规则），可供状态机构建器消费
**And** 不包含 StoryLifecycle 状态机类的实例化（留给 Epic 2）

**Given** 项目中不存在 `ato.yaml`
**When** 调用 `load_config()` 函数
**Then** 抛出 `ConfigError`，错误信息提示用户从 `ato.yaml.example` 复制配置文件

**Given** `ato.yaml.example` 模板文件
**When** 用户查看模板
**Then** 包含所有配置项的说明注释和合理默认值

### Story 1.4a: Preflight 三层检查引擎

As a 系统,
I want 提供分层前置检查引擎，验证环境、项目结构和编排前置 Artifact,
So that 启动编排前可确认所有前置条件满足。

**Acceptance Criteria:**

**Given** 系统环境
**When** 调用第一层环境检查 `check_system_environment()`
**Then** 依次检测 Python ≥3.11、Claude CLI 安装与认证、Codex CLI 安装与认证、Git 安装
**And** 返回 `list[CheckResult]`，每项含 status（PASS/HALT/WARN/INFO）和消息

**Given** 第一层通过
**When** 调用第二层项目结构检查 `check_project_structure(project_path)`
**Then** 验证目标路径是 git repo、BMAD 配置存在且有效、ato.yaml 存在
**And** 缺失非阻断项返回 WARN

**Given** 第二层通过
**When** 调用第三层 Artifact 检查 `check_artifacts(project_path)`
**Then** 检测 Epic 文件（必须）、PRD（推荐）、架构文档（推荐）、UX 设计（可选）
**And** 缺失必须项返回 HALT

**Given** 检查结果列表
**When** 将结果持久化
**Then** 创建 `preflight_results` 表并写入所有检查结果（CREATE TABLE IF NOT EXISTS）

### Story 1.4b: ato init CLI 命令与 UX 渲染

As a 操作者,
I want 通过 `ato init <项目路径>` 初始化项目并看到格式化的就绪报告,
So that 获得清晰的就绪/缺失反馈，确认可以开始编排。

**Acceptance Criteria:**

**Given** 操作者运行 `ato init /path/to/project`
**When** CLI 调用 Preflight 引擎（Story 1.4a）执行三层检查
**Then** 每项检查结果实时显示（✔通过绿色 / ✖阻断红色 / ⚠警告琥珀 / ℹ信息灰）（UX-DR6）
**And** 任一项 HALT 时显示具体修复指引并停止

**Given** 所有检查完成且无阻断
**When** 显示检查摘要
**Then** 使用 rich 库渲染格式化输出，包含分层结果和底部摘要（"就绪（N 警告, M 信息）"）
**And** 等待用户 Enter 确认后创建 `.ato/` 目录和 SQLite 数据库

**Given** `ato init` 已成功完成
**When** 再次运行 `ato init`
**Then** 检测到已有数据库，提示用户确认是否重新初始化

**Given** CLI 退出码规范
**When** 环境检查失败时
**Then** 退出码为 2（环境错误），错误信息输出到 stderr

### Story 1.5: ato plan 阶段预览

As a 操作者,
I want 通过 `ato plan <story-id>` 预览某个 story 将经历的完整阶段序列,
So that 在启动编排前对即将发生的事有清晰的心理预期。

**Acceptance Criteria:**

**Given** 配置已加载且 story 存在于数据库中
**When** 执行 `ato plan <story-id>`
**Then** 输出该 story 将经历的完整阶段序列（如 `creating → validating → dev_ready → developing → reviewing → ... → done`），使用 rich 库格式化（颜色编码不同阶段类型）

**Given** 配置已加载且 story 当前处于某个中间阶段
**When** 执行 `ato plan <story-id>`
**Then** 已完成的阶段标记 ✔，当前阶段高亮，未来阶段正常显示

**Given** story-id 不存在
**When** 执行 `ato plan <story-id>`
**Then** 退出码为 1，stderr 输出 "Story not found: <story-id>"

## Epic 2A: 编排引擎核心

操作者可以启动/停止编排器，观察 story 在状态机中自动推进，状态转换按序串行执行且不冲突。

### Story 2A.1: 操作者可观察到 story 在状态机中自动推进

As a 操作者,
I want 看到 story 在状态机中按配置的阶段顺序自动推进,
So that 确认编排系统正确驱动 story 生命周期。

**Acceptance Criteria:**

**Given** Epic 1 的配置引擎已就绪
**When** 调用 `StoryLifecycle.from_config(phase_definitions)` 构建状态机
**Then** 生成包含所有配置阶段和转换的状态机实例
**And** 创建后执行 `await sm.activate_initial_state()`，初始状态为 `queued`

**Given** story 状态机处于某个阶段
**When** 通过 `await sm.send(event)` 发送合法转换事件
**Then** 状态机转移到下一阶段
**And** 新状态通过 `save_story_state(db, story_id, sm.current_state)` 持久化到 SQLite stories 表
**And** 操作者通过 `ato status` 可看到状态变更

**Given** 尝试发送非法转换事件
**When** 事件不在当前状态的合法转换列表中
**Then** 状态机拒绝转换，状态不变
**And** structlog 记录拒绝原因

**Given** 100% transition 覆盖测试（Decision 8）
**When** 执行状态机单元测试
**Then** 每个合法 transition 至少执行 1 次（~20 tests）

### Story 2A.2: 操作者可观察到状态转换按序串行执行且不冲突

As a 操作者,
I want 确认并发完成的任务不会导致状态冲突,
So that 系统在多 story 并行时状态一致性有保障。

**Acceptance Criteria:**

**Given** TransitionQueue 已启动
**When** 多个 agent 同时完成任务并提交状态转换事件
**Then** 事件按提交顺序串行处理，每个事件依次执行：状态机 `send()` → SQLite 持久化 → commit
**And** 状态转换处理延迟 ≤5 秒（NFR2）

**Given** TransitionQueue 处理过程中
**When** 前一个 transition 尚未完成
**Then** 后续事件在 asyncio.Queue 中排队等待，不会并发执行

**Given** nudge 通知机制
**When** TUI 或 `ato submit` 写入 SQLite 后触发 nudge
**Then** Orchestrator 立即轮询，不等 2-5 秒定期轮询间隔

**Given** 操作者查看系统状态
**When** 两个 stories 的 transition 几乎同时提交
**Then** `ato status` 显示两个 stories 的状态均正确更新，无冲突

### Story 2A.3: 操作者可启动/停止 Orchestrator 事件循环

As a 操作者,
I want 通过 `ato start` 启动编排器、`ato stop` 优雅停止,
So that 系统在后台自动推进 story 流水线，可以随时安全停止。

**Acceptance Criteria:**

**Given** 操作者运行 `ato start`
**When** Orchestrator 启动
**Then** 写 PID 到 `.ato/orchestrator.pid`，加载配置，初始化 TransitionQueue consumer，开始 asyncio 事件循环
**And** 配置解析与状态机构建 ≤3 秒（NFR5）

**Given** Orchestrator 正在运行
**When** 操作者运行 `ato stop`
**Then** 将所有 `status=running` 的 task 标记为 `paused`，等待当前 CLI 调用完成（或超时后清理），然后退出
**And** 删除 `.ato/orchestrator.pid` 文件

**Given** Orchestrator 运行中
**When** 事件循环每 2-5 秒轮询 SQLite
**Then** 检测新的 transition 事件、检查 approval 状态、调度就绪的 agent 任务

**Given** agent 完成任务
**When** adapter 解析完成
**Then** 通过 TransitionQueue 提交状态转换事件

## Epic 2B: Agent 集成与工作空间

操作者可以看到 AI agent 被自动调度执行任务，结果被结构化收集，story 在独立 worktree 中隔离执行，batch 选择和 Interactive Session 完整可用。

### Story 2B.1: 操作者可看到 Claude agent 被调度执行任务并返回结构化结果

As a 操作者,
I want 看到 Claude agent 被自动调度执行创建、开发、修复等任务，结果被结构化记录,
So that 确认 AI agent 集成正确工作。

**Acceptance Criteria:**

**Given** SubprocessManager 收到任务调度请求
**When** 并发 agent 数未超过 `max_concurrent_agents` 配置
**Then** 通过 `asyncio.create_subprocess_exec` 启动 CLI 进程（不使用 `shell=True`）
**And** 在 `running` 字典中注册 PID、story_id、phase、启动时间

**Given** 需要调用 Claude CLI 执行任务
**When** 调用 `ClaudeAdapter.execute(prompt, options)`
**Then** 构建 `claude -p "<prompt>" --output-format json --max-turns <N>` 命令
**And** 使用 OAuth 模式（非 `--bare`），BMAD skills 自动加载

**Given** Claude CLI 调用完成
**When** 解析 stdout JSON 输出
**Then** 经 `AdapterResult.model_validate()` 验证输出结构
**And** 创建 `cost_log` 表（CREATE TABLE IF NOT EXISTS）并记录结构化数据（耗时、成本 total_cost_usd、token 用量）（FR27, FR28）
**And** 操作者通过 `ato status` 可看到任务完成状态

**Given** CLI 进程超时或异常退出
**When** 错误被 `CLIAdapterError` 分类（认证过期 / rate limit / 超时 / 未知）
**Then** 自动重试 1 次（NFR8），重试仍失败则 escalate
**And** 三阶段清理协议：SIGTERM → wait(5s) → SIGKILL → wait(2s)

**Given** Snapshot fixture 测试（Decision 9）
**When** 用 `tests/fixtures/claude_output_*.json` 执行解析
**Then** 解析结果与 fixture 预期一致

### Story 2B.2: 操作者可看到 Codex agent 执行审查并返回 findings

As a 操作者,
I want 看到 Codex agent 被调度执行审查任务，findings 被结构化收集和记录,
So that 确认双 CLI 异构 agent 调用均正常工作。

**Acceptance Criteria:**

**Given** 需要调用 Codex CLI 执行审查任务
**When** 调用 `CodexAdapter.execute(prompt, options)`
**Then** 构建 `codex exec "<prompt>" --json` 命令，prompt 中指示 agent 使用 `bmad-code-review` skill（项目 `.claude/skills/` 下的 skill 在项目目录运行时自动可发现）
**And** reviewer 角色使用 `--sandbox read-only`，审查结果通过 CLI 输出流捕获（`text_result`）

**Given** Codex CLI 调用完成
**When** 解析 JSONL 事件流和 `-o` 输出文件
**Then** 经 `AdapterResult.model_validate()` 验证输出结构
**And** 使用 CODEX_PRICE_TABLE 从 token 数计算成本，记录到 cost_log（FR28）

**Given** Codex 价格表（Architecture: Codex 成本价格表结构）
**When** 计算成本
**Then** `cost = input_tokens * price["input_per_1m"] / 1_000_000 + output_tokens * price["output_per_1m"] / 1_000_000`

**Given** Snapshot fixture 测试
**When** 用 `tests/fixtures/codex_output_*.json` 执行解析
**Then** 解析结果与 fixture 预期一致

### Story 2B.3: 操作者可看到 BMAD skill 输出被解析为结构化 JSON

As a 操作者,
I want 看到 BMAD skill 的 Markdown 输出被可靠地解析为结构化 JSON,
So that 质量门控和审查流程可以消费结构化数据。

**Acceptance Criteria:**

**Given** BMAD skill 产出 Markdown 格式的审查结果（code-review、story-validation、architecture-review、QA-report）
**When** 调用 `BmadAdapter.parse(markdown_output, skill_type)`
**Then** 返回结构化 JSON，经 Pydantic model_validate() 验证

**Given** tests/fixtures/ 中的 20 个已知 BMAD skill 输出样本
**When** 批量执行解析
**Then** 成功率 ≥ 95%（至少 19/20 成功解析为结构化 JSON）

**Given** 解析失败
**When** 无法从 Markdown 提取结构化数据
**Then** 标记任务为 `needs_human_review`，写入 approval 记录（类型 `needs_human_review`）到 approvals 表，通过 nudge 通知 Orchestrator
**And** 操作者可在审批队列中看到该请求并决定后续操作
**And** structlog 记录失败原因和原始输出摘要

### Story 2B.4: 操作者可看到 story 在独立 worktree 中执行

As a 操作者,
I want 看到每个 story 在独立的 git worktree 中执行 agent 任务,
So that story 之间的代码变更互相隔离。

**Acceptance Criteria:**

**Given** story 进入需要代码变更的阶段（creating / developing / fixing）
**When** 调用 `WorktreeManager.create(story_id, branch_name)`
**Then** 创建独立的 git worktree，路径注册到 stories 表

**Given** story 完成所有阶段
**When** 调用 `WorktreeManager.cleanup(story_id)`
**Then** 清理 worktree 目录和 git 分支记录

**Given** macOS 和 Linux 环境（NFR13）
**When** 执行 worktree 操作
**Then** 两种平台行为一致

### Story 2B.5: 操作者可选择 story batch 并查看状态

As a 操作者,
I want 通过 `ato batch select` 选择要执行的 story batch，通过 `ato batch status` 查看进度,
So that 可以按自己的节奏推进工作。

**Acceptance Criteria:**

**Given** PM agent 分析 epic/story 的优先级和依赖关系
**When** 调用 `ato batch select`
**Then** 展示推荐的 batch 方案供操作者选择（FR12）
**And** 操作者确认后，选中的 stories 状态从 `queued` 转为 `creating`

**Given** batch 已选定并正在执行
**When** 操作者运行 `ato batch status`
**Then** 显示当前 batch 的整体进度（已完成/进行中/待执行/失败）（FR38）

### Story 2B.6: 操作者可启动 Interactive Session 并通过 ato submit 完成

As a 操作者,
I want 在 Interactive Session 中与 agent 直接协作，完成后通过 `ato submit` 标记完成,
So that 复杂任务可以人机协作解决。

**Acceptance Criteria:**

**Given** story 需要 Interactive Session
**When** 系统启动 Interactive Session
**Then** 在独立终端窗口中启动，注册 PID、worktree 路径和启动时间到 tasks 表（FR10）

**Given** 操作者在 Interactive Session 中完成工作
**When** 运行 `ato submit <story-id>`
**Then** 验证 story 状态合法、worktree 中有新 commit
**And** 提取 Context Briefing（见下方 schema）
**And** 更新 SQLite 并触发 nudge 通知 Orchestrator

**Given** Interactive Session 超时（7200 秒）
**When** 超时触发
**Then** 创建 approval 请求操作者选择恢复策略（重新启动 / 续接 / 放弃）（FR23）

**Given** Context Briefing 提取（FR53）
**When** agent task 完成
**Then** 提取结构化工作记忆摘要，包含以下字段：
  - `story_id`: str — 关联 story
  - `phase`: str — 产生 briefing 的阶段
  - `task_type`: str — 任务类型（creating/developing/reviewing/fixing）
  - `artifacts_produced`: list[str] — 产出的文件路径列表
  - `key_decisions`: list[str] — agent 做出的关键决策摘要
  - `agent_notes`: str — agent 自由格式的工作笔记
**And** 经 `ContextBriefing.model_validate()` 验证后存入 tasks 表 `context_briefing` 列

**Given** agent session 管理（FR9）
**When** Convergent Loop 内需要短循环 session resume
**Then** 系统支持 session 续接和 fork

## Epic 3: Convergent Loop 质量门控

系统自动执行 review-fix 收敛循环，追踪每个 finding 的跨轮次状态，确保代码质量在 max_rounds 内收敛或 escalate，用户可以信任自动化质量结果。

### Story 3.1: 操作者可看到明显结构错误被秒级拦截，findings 被独立追踪

As a 操作者,
I want 在 agent review 之前看到明显的结构错误被秒级拦截，review 后的每个 finding 可被独立追踪和查询,
So that 不用等 agent 审查就能发现低级问题，每个质量问题有清晰的生命周期。

**Acceptance Criteria:**

**Given** agent 产出的 artifact（story 文档、代码变更等）
**When** 进入 review 阶段前
**Then** 先执行 deterministic validation（JSON Schema 验证），耗时 ≤1 秒（NFR4）
**And** 验证通过才进入 agent review，验证失败直接退回修改
**And** 操作者可在 `ato status` 或 TUI 中看到 "Schema 验证失败，已退回修改"

**Given** schemas/ 目录下的 JSON Schema 文件（review-findings.json, story-validation.json, finding-verification.json）
**When** 对 artifact 执行 Schema 验证
**Then** 返回 pass/fail + 具体验证错误列表

**Given** review 产出 findings
**When** findings 入库
**Then** 创建 `findings` 表（CREATE TABLE IF NOT EXISTS），每个 finding 包含：finding_id、story_id、round_num、severity（blocking/suggestion）、description、status（open）、created_at
**And** 经 Pydantic FindingRecord `model_validate()` 验证后写入 SQLite findings 表

**Given** finding 的 severity 分类
**When** blocking findings 数量超过配置的 `blocking_threshold`
**Then** 写入 approval 记录（类型 `blocking_abnormal`）到 approvals 表（Story 1.2 已创建），通过 nudge 通知 Orchestrator（FR18）
**And** 操作者可在审批队列或 `ato status` 中看到该 blocking 通知并决定是否继续

### Story 3.2a: Convergent Loop 首轮全量 Review

As a 系统,
I want 在 story 进入 review 阶段时执行首轮全量 review,
So that 获得完整的质量基线。

**Acceptance Criteria:**

**Given** story 进入 review 阶段
**When** Convergent Loop 启动第 1 轮
**Then** 调度 reviewer agent（Codex read-only），prompt 指示 agent 使用 `bmad-code-review` skill 执行全量 review
**And** 审查结果通过 CLI 输出流捕获（text_result），经 BmadAdapter 解析为结构化 findings，入库到 SQLite findings 表

**Given** 第 1 轮 review 返回 0 个 finding
**When** 评估收敛条件
**Then** 直接判定为收敛（无需 fix），story 进入下一阶段

**Given** findings 入库
**When** 每个 finding 写入 SQLite
**Then** 包含：finding_id、story_id、round_num=1、severity、description、status=open、file_path、rule_id、dedup_hash
**And** structlog 记录 round_num=1、findings_total、open_count

### Story 3.2b: Fix Dispatch 与 Artifact 验证

As a 系统,
I want 在 review 发现 blocking findings 后自动调度 fix agent,
So that 质量问题被自动修复。

**Acceptance Criteria:**

**Given** 第 N 轮 review 完成，存在 open blocking findings
**When** 进入 fix 阶段
**Then** 调度 fixer agent（Claude）修复所有 open blocking findings
**And** fix prompt 中包含每个 open finding 的 description、file_path、severity
**And** fix agent 可显式指定使用 `debugging-strategies` skill（`/debugging-strategies`）进行系统化根因分析与修复策略制定

**Given** fix agent 完成
**When** 验证 fix 产出
**Then** 确认 worktree 中有新 commit（artifact 存在性验证）
**And** structlog 记录 fix 阶段耗时和成本

### Story 3.2c: Re-review Scope Narrowing

As a 系统,
I want 在 re-review 时自动收窄 scope，仅验证上轮 open findings,
So that 每轮 review 聚焦于变更影响，效率递增。

**Acceptance Criteria:**

**Given** fix 完成后进入第 N+1 轮 re-review
**When** 构建 re-review scope
**Then** 仅包含上轮 open findings 的匹配键集合（file_path + rule_id + severity）
**And** re-review prompt 指示 agent 使用 `bmad-code-review` skill，并明确这是 scoped re-review：仅需验证这些 findings 的闭合状态和新引入问题
**And** 审查结果通过 CLI 输出流捕获（text_result），经 BmadAdapter 解析

**Given** re-review 完成
**When** 匹配 findings 状态
**Then** 使用 dedup_hash（SHA256 of file_path + rule_id + severity + normalized description）匹配跨轮次 findings
**And** 上轮 open + 本轮匹配到 → `still_open`
**And** 上轮 open + 本轮未匹配 → `closed`
**And** 本轮存在 + 上轮无匹配 → `new`（status=open, round_num=N+1）

**Given** fix agent 修复过程中引入了新的 blocking finding
**When** re-review 检测到新 finding
**Then** 新 finding 以 status=new 入库，纳入下一轮 scope，不与已 closed 的 finding 混淆

### Story 3.2d: 收敛判定与终止条件

As a 系统,
I want 基于 finding 状态准确判定收敛或终止循环,
So that Convergent Loop 有确定性的结束条件。

**Acceptance Criteria:**

**Given** re-review 完成后
**When** 评估收敛条件
**Then** 所有 blocking findings 为 closed → 收敛成功，story 进入下一阶段

**Given** 仍有 open blocking findings
**When** 未达 max_rounds
**Then** 继续下一轮（回到 Story 3.2b fix 阶段）

**Given** Convergent Loop 在任何情况下
**When** 轮次达到 `max_rounds`
**Then** 强制终止循环（NFR9），不会无限循环
**And** 创建 approval 记录（类型 `convergent_loop_escalation`），通知操作者人工介入
**And** structlog 记录完整的 findings 变化历史（每轮 diff）

### Story 3.3: 操作者可信任收敛判定结果，未收敛时收到 escalation 通知

As a 操作者,
I want 在 Convergent Loop 结束时看到确定性的收敛/escalation 判定，未收敛时收到通知并做出决策,
So that 可以信任自动化质量结果，不会被无限循环阻塞。

**Acceptance Criteria:**

**Given** 一轮 re-review 完成后
**When** 计算收敛率（closed_findings / total_findings）
**Then** 收敛率 ≥ `convergence_threshold` 且无 open blocking → 判定为收敛
**And** 操作者可在 `ato status` 或 TUI 中看到 story 自动进入下一阶段

**Given** Convergent Loop 达到 max_rounds 仍未收敛
**When** 执行 escalation（FR17）
**Then** 写入 approval 记录（类型 `convergent_loop_escalation`），通知操作者人工介入
**And** 操作者可在审批队列中看到 escalation，含完整的 findings 变化历史（每轮 diff）

**Given** 操作者想追踪某个 finding 的跨轮次变化（FR14）
**When** 查询某个 story 的 findings
**Then** 可看到每个 finding 在每轮中的状态变化轨迹（round 1: open → round 2: closed）

**Given** Convergent Loop 集成测试
**When** 构造已知 5-finding review 场景
**Then** ≤3 轮内闭合所有 blocking findings（端到端测试）

**Given** 非法 transition 测试
**When** 尝试在 Convergent Loop 中跳过 fix 直接进入 re-review
**Then** 状态机拒绝，状态不变，structlog 记录

## Epic 4: 人机协作与审批队列

用户可以在审批队列中高效处理所有判断性决策（batch 选择确认、merge 授权、UAT 结果、超时/异常处理），系统在需要时通过通知打断用户，merge queue 安全管理。

### Story 4.1: Approval Queue 与 nudge 通知机制

As a 操作者,
I want 在审批队列中查看所有待决策事项并做出决策,
So that 所有判断性决策集中管理，决策记录持久化。

**Acceptance Criteria:**

**Given** 系统检测到需要人工决策的事件（merge 授权、超时、escalation、batch 确认）
**When** 创建 approval 记录
**Then** 写入 SQLite approvals 表，包含类型、story_id、details、推荐操作、风险级别、created_at
**And** 通过 nudge 通知 Orchestrator 立即轮询

**Given** 操作者通过 CLI 查看审批队列
**When** 运行 `ato approvals`
**Then** 使用 rich 库格式化输出所有 pending approvals（类型图标 + story ID + 摘要 + 推荐操作）

**Given** 操作者做出决策
**When** 运行 `ato approve <approval-id> --decision <选项>`
**Then** 决策记录持久化（含时间戳、选择理由）（FR20）
**And** 通过 nudge 通知 Orchestrator，触发对应的状态转换

**Given** nudge 通知机制
**When** TUI 或 CLI 写入 SQLite 后发送 nudge
**Then** Orchestrator 立即轮询，响应延迟 <1 秒
**And** nudge 丢失时，2-5 秒定期轮询兜底

**Given** 任务失败（认证过期、超时、解析错误等）
**When** 系统创建 escalation approval（FR50）
**Then** approval 包含失败原因和可选恢复操作（重试 / 跳过 / escalate）

### Story 4.2: Merge Queue 与 Regression 安全管理

As a 操作者,
I want merge queue 按顺序安全合并代码，regression 失败时自动冻结,
So that main 分支始终保持可用状态。

**Acceptance Criteria:**

**Given** story 通过 UAT 等待 merge
**When** 操作者批准 merge
**Then** 系统按顺序执行 rebase 和 merge，一次只处理一个（FR31）

**Given** 多个 story 几乎同时通过 UAT 并被批准 merge
**When** merge 请求并发到达
**Then** merge queue 严格串行化处理，按 approval 时间排序，不出现竞争条件

**Given** merge 完成后
**When** 系统自动执行 regression 测试（Structured Job）
**Then** regression 全部通过 → story 标记为 `done`

**Given** regression 测试失败
**When** 系统检测到失败
**Then** 自动冻结 merge queue，阻止后续 merge（FR32, NFR10）
**And** 创建紧急 approval（类型 `regression_failure`），选项：revert / fix forward / pause

**Given** worktree rebase 产生冲突
**When** 系统检测到冲突（FR52）
**Then** 调度 agent 自动解决冲突，解决后重新进入 review 流程
**And** 解决失败则 escalate 给操作者

**Given** merge 流程中 pre-commit hook 失败（lint/format/type check）
**When** 系统检测到 commit 失败
**Then** 调度 agent 自动修复（基于项目配置的 lint/format/type-check 命令），修复后重新 commit
**And** 自动修复失败则 escalate 给操作者，创建 approval（类型 `precommit_failure`）

**Given** merge queue 被冻结
**When** 操作者处理完异常（revert 或 fix forward 成功）
**Then** merge queue 解冻，恢复正常合并流程

### Story 4.3: UAT 与 Interactive Session 完成检测

As a 操作者,
I want 在 UAT 阶段提交测试结果，通过 `ato submit` 标记 Interactive Session 完成,
So that 人工测试结果纳入流水线，开发协作有明确的完成信号。

**Acceptance Criteria:**

**Given** story 进入 UAT 阶段
**When** 操作者在 worktree 中手动测试完成
**Then** 通过 `ato uat <story-id> --result pass` 或 `--result fail --reason "描述"` 提交结果（FR21）
**And** 通过 → story 进入 merge 阶段；不通过 → story 退回 fix 阶段重新进入 Convergent Loop（FR48）

**Given** story 处于 `developing` 阶段（Interactive Session）
**When** 操作者完成开发协作
**Then** 运行 `ato submit <story-id>` 标记完成
**And** 验证 story 存在且处于 `developing` 状态，验证 worktree 有 commit
**And** 更新 SQLite story 状态，通过 nudge 通知 Orchestrator 触发 `dev_done` 事件

**Given** 操作者需要选择 Interactive Session 恢复策略（FR23）
**When** 系统崩溃后 Interactive Session 需要人工决策
**Then** 提供三个选项：重新启动 / 从上次 session 续接（`--resume`）/ 放弃
**And** 选项通过 approval 机制呈现

### Story 4.4: 通知体系与 CLI 交互质量

As a 操作者,
I want 系统在需要我介入时通过通知打断我，CLI 输出清晰友好,
So that 我不会错过重要决策，CLI 交互体验不需要等 TUI。

**Acceptance Criteria:**

**Given** 新的 approval 创建
**When** approval 类型为常规（merge、timeout、budget）
**Then** 发出 terminal bell 通知（FR22）

**Given** regression 失败
**When** merge queue 被冻结
**Then** 发出 terminal bell + 标注"紧急"（UX-DR12）

**Given** story 完成或 batch 交付
**When** 里程碑达成
**Then** 发出 terminal bell 一声（里程碑级别通知）

**Given** 所有 CLI 命令的错误输出
**When** 发生错误
**Then** 使用"发生了什么 + 你的选项"格式（非技术堆栈），输出到 stderr
**And** 退出码遵循规范（0 成功 / 1 一般错误 / 2 环境错误）

**Given** 异常审批的 CLI 展示
**When** 操作者查看异常类型 approval
**Then** rich 格式化输出包含"发生了什么 + 影响范围 + 你的选项"三要素（UX-DR20 部分落地）

### Story 4.5: Regression 测试执行与 Merge Queue 集成

As a 操作者,
I want 系统在 merge 前自动执行 regression 测试，失败时冻结 merge queue,
So that main 分支的质量不会因 merge 而退化。

**Acceptance Criteria:**

**Given** story 完成所有质量门控并进入 merge 准备阶段
**When** 获得操作者 merge 授权后
**Then** 在 worktree 中执行 regression 测试套件（项目配置的测试命令）

**Given** regression 测试全部通过
**When** 执行 merge
**Then** 按 merge queue 顺序 rebase 并 merge 到 main
**And** story 标记为 done

**Given** regression 测试失败
**When** 检测到失败
**Then** 自动冻结 merge queue，阻止后续 merge（FR32）
**And** 发送 URGENT 级通知（terminal bell + TUI 顶栏闪烁）（FR22）
**And** 创建 approval（类型 `regression_failure`），等待操作者决策

**Given** 操作者处理 regression 失败
**When** 选择决策
**Then** 支持：① 退回 fix 重新进入 CL ② 人工介入 worktree ③ 从 merge queue 移除

## Epic 5: 崩溃恢复与可观测性

系统意外终止后，用户运行 `ato start` 即可自动恢复，数据零丢失，获得人话版恢复摘要，可查看任意 story 的完整执行历史和成本数据。

### Story 5.1a: 操作者可在崩溃后运行 ato start 自动恢复可恢复的任务

As a 操作者,
I want 系统崩溃后运行 `ato start` 自动恢复所有可恢复的任务，不可恢复的任务等待我决策,
So that 意外中断不需要手动重建状态。

**Acceptance Criteria:**

**Given** 系统意外终止后（进程崩溃 / 机器重启）
**When** 操作者运行 `ato start`
**Then** SQLite WAL 自动回放，数据完好
**And** 扫描所有 `status=running` 的 task 进行分类处理

**Given** running task 的 PID 仍存活
**When** 恢复引擎检测
**Then** 重新注册监听 → 自动恢复

**Given** running task 的 PID 不存活但 artifact 存在
**When** 恢复引擎检测
**Then** 继续流水线（从 artifact 恢复）→ 自动恢复

**Given** running task 为 Structured Job，PID 不存活且无 artifact
**When** 恢复引擎检测
**Then** 重新调度该任务 → 自动恢复

**Given** running task 为 Interactive Session，PID 不存活
**When** 恢复引擎检测
**Then** 标记为 `needs_human`，等待操作者决策

**Given** `ato stop` 后正常重启（task 状态为 `paused`）
**When** 运行 `ato start`
**Then** 正常恢复：直接重调度 paused tasks，不触发崩溃恢复逻辑
**And** 启动日志明确输出恢复模式

### Story 5.1b: 崩溃恢复在 30 秒内完成且测试可验证

As a 操作者,
I want 崩溃恢复在 30 秒内完成，且恢复逻辑经过充分测试,
So that 恢复速度可预期，恢复行为可信赖。

**Acceptance Criteria:**

**Given** 崩溃恢复完整流程
**When** 计时
**Then** 恢复耗时 ≤30 秒（NFR1 MVP 目标）

**Given** 崩溃恢复测试策略
**When** 运行 `test_crash_recovery.py`
**Then** 采用函数式测试——构造"崩溃前的数据库状态"（插入 status=running task，PID 不存在）→ 调用 recovery → 验证分类行为
**And** 不需要真实杀进程，纯数据库状态驱动测试

**Given** 4 种恢复分类的集成测试
**When** 分别构造 PID 存活、artifact 存在、Structured Job 无 artifact、Interactive Session 的场景
**Then** 每种场景的恢复行为与 Story 5.1a 定义一致

### Story 5.2: 恢复摘要与执行历史查看

As a 操作者,
I want 恢复后看到人话版摘要，随时查看任意 story 的完整执行历史,
So that 恢复后安心无焦虑，系统行为可追溯审计。

**Acceptance Criteria:**

**Given** 崩溃恢复完成
**When** 系统展示恢复摘要（FR26）
**Then** 首行为"✔ 数据完整性检查通过"（消除焦虑）
**And** 显示"N 个任务自动恢复，M 个任务需要你决定"
**And** 使用 rich 库格式化 CLI 输出（UX-DR19）

**Given** 有 `needs_human` 的任务
**When** 恢复摘要展示决策列表
**Then** 每个任务显示 worktree 路径 + 最后已知状态 + 三个选项（重启/续接/放弃）

**Given** 操作者想查看某个 story 的执行历史
**When** 运行 `ato history <story-id>`（FR49）
**Then** 输出完整时间轴：哪个 agent 在什么时间执行了什么任务，产出了什么 artifact
**And** 使用 rich 库格式化，时间轴按时间排序

**Given** 操作者想查看成本数据
**When** 运行 `ato cost report`
**Then** 输出今日/本周成本汇总 + 按 story 的成本明细

## Epic 6: TUI 指挥台

用户拥有一个信息密集的终端仪表盘——三问首屏一眼掌握全局，lazygit 三面板布局高效导航，快捷键驱动所有操作，审批/状态/成本/详情在一个界面内完成。

### Story 6.1a: 操作者可启动 TUI 并连接到运行中的 Orchestrator

As a 操作者,
I want 通过 `ato tui` 启动 TUI 并连接到运行中的 Orchestrator，可读写 SQLite,
So that 有一个可工作的 TUI 进程作为后续组件的容器。

**Acceptance Criteria:**

**Given** 运行 `ato tui`
**When** TUI 应用启动
**Then** 作为独立进程运行，通过 SQLite 轮询读取状态 + 审批/UAT 写入（非只读）
**And** 2 秒内从 SQLite 加载状态并渲染首屏

**Given** TUI 写入路径
**When** TUI 执行审批决策或 UAT 结果提交
**Then** 直接写 SQLite + 立即 commit + 发送 nudge 通知 Orchestrator
**And** `busy_timeout=5000` 覆盖写冲突

**Given** Orchestrator 已运行
**When** TUI 启动
**Then** TUI 连接已运行 Orchestrator 的 SQLite，完整功能可用

### Story 6.1b: 操作者可看到统一的深色主题和响应式布局

As a 操作者,
I want TUI 有一致的深色主题、三重状态编码和响应式布局,
So that 视觉体验专业统一，不同终端宽度都可用。

**Acceptance Criteria:**

**Given** TCSS 主题文件 `tui/app.tcss`
**When** 加载主题
**Then** 包含 9 个语义色彩变量（$success #50fa7b / $warning #f1fa8c / $error #ff5555 / $info #8be9fd / $accent #bd93f9 / $muted #6272a4 / $text #f8f8f2 / $background #282a36 / $surface #44475a）（UX-DR7）
**And** 所有语义色在 $background 上对比度 ≥ 4.5:1

**Given** 状态编码系统
**When** 展示任何状态信息
**Then** 使用颜色 + Unicode 图标 + 文字标签三重编码（UX-DR8）：●running / ◐active / ◆awaiting / ✖failed / ✔done / ⏸frozen / ℹinfo

**Given** 终端 resize 事件
**When** 终端宽度变化
**Then** 实时响应：≥140 列三面板 / 100-139 列 Tab 视图 / <100 列降级警告 + CLI-only 模式（UX-DR9）
**And** 切换时保持当前选中状态和焦点位置

### Story 6.2a: ThreeQuestionHeader Widget

As a 操作者,
I want TUI 顶栏一眼回答"系统正常吗？需要我做什么？花了多少？",
So that 无需任何操作即可掌握全局状态。

**Acceptance Criteria:**

**Given** TUI 渲染首屏
**When** ThreeQuestionHeader 组件加载
**Then** 显示四个区域：① 系统状态（● N 项运行中 / ✖ N 项异常）② 审批计数（◆ N 审批等待 / ✔ 无待处理）③ 成本摘要（$X.XX 今日）④ 更新时间（更新 Ns前）（UX-DR1）

**Given** 不同终端宽度
**When** 响应式适配
**Then** 180+ 列完整文字 / 140-179 列缩略标签 / 100-139 列仅图标+数字

**Given** 状态更新
**When** 每 2 秒刷新数据
**Then** 从 SQLite stories + approvals + cost_log 表聚合数据，刷新渲染 ≤500ms（NFR3）

### Story 6.2b: DashboardScreen 与 Story 列表

As a 操作者,
I want 在 DashboardScreen 中看到所有 story 的状态列表，lazygit 三面板布局高效导航,
So that 可以快速定位需要关注的 story。

**Acceptance Criteria:**

**Given** DashboardScreen 加载
**When** 渲染主布局
**Then** lazygit 三面板：左面板 story 列表 + 右上面板联动详情 + 右下面板操作区域（UX-DR9）

**Given** story 列表渲染
**When** 显示每个 story
**Then** 使用 StoryStatusLine 组件（UX-DR15）：状态图标 + ID + 阶段 + 进度条 + 耗时 + 成本
**And** 按 AWAITING → ACTIVE → BLOCKED → DONE 自动排序（UX-DR11）

**Given** 活跃 story
**When** HeartbeatIndicator 渲染
**Then** 显示动画 spinner + 经过时间（客户端计时器）+ CL 轮次 + 成本（UX-DR3）

**Given** 无 story 数据
**When** 空状态显示
**Then** 提示"尚无 story。运行 `ato batch select` 选择第一个 batch"（UX-DR13）

### Story 6.3a: 常规审批交互

As a 操作者,
I want 通过 TUI 快速处理常规审批（merge/timeout/budget/blocking）,
So that 审批决策在 30 秒内完成。

**Acceptance Criteria:**

**Given** 左面板选中一个 AWAITING 状态的 story
**When** ApprovalCard 渲染
**Then** 折叠态单行显示：类型图标 + story ID + 一句话摘要（模板拼接）+ 推荐操作 + 风险指示（UX-DR2）

**Given** 操作者按 `d` 展开 ApprovalCard
**When** 详情面板渲染
**Then** 显示：审批上下文（阶段转换详情、review 轮次、QA 结果、成本、耗时）

**Given** 操作者按 `y` 或 `n`
**When** 提交审批决策
**Then** 直接写入 SQLite approvals 表（决策 + 时间戳 + 选择理由）
**And** 触发 nudge 通知 Orchestrator
**And** 显示即时反馈"已提交，等待处理"（UX-DR14）

### Story 6.3b: 异常审批与多选交互

As a 操作者,
I want 在异常情况下通过专用面板做出多选决策,
So that 复杂异常可以精确处理。

**Acceptance Criteria:**

**Given** 异常审批类型（regression_failure / critical_timeout / cascade_failure）
**When** ExceptionApprovalPanel 渲染
**Then** 显示 $error 红色边框 + "发生了什么 + 影响范围 + 你的选项"三要素（UX-DR5, UX-DR20）

**Given** 多选一选项
**When** 操作者按 `1` / `2` / `3` 数字键
**Then** 选择对应恢复策略，直接写入 SQLite approvals 表
**And** 触发 nudge 通知 Orchestrator

### Story 6.4: Story 详情与渐进钻入导航

As a 操作者,
I want 从概览逐层钻入查看 story 详情、findings、成本、执行历史,
So that 任何信息 ≤3 层可达，按需查看不被强推。

**Acceptance Criteria:**

**Given** 左面板选中某 story
**When** 右上面板联动更新
**Then** 显示 story 概览：阶段、成本、耗时、CL 轮次（第 1 层）

**Given** 用户按 Enter
**When** 进入 Story 详情页（第 2 层）（UX-DR18）
**Then** 显示：状态流可视化 + findings 摘要 + 文件变更列表 + 成本明细 + 执行历史

**Given** Story 详情页中
**When** 用户按 `f` / `c` / `h` / `l`（UX-DR17）
**Then** `f` 展开 Findings 列表 / `c` 展开成本明细 / `h` 展开执行历史 / `l` 弹出独立终端查看 agent 实时日志（第 3 层，有意摩擦）

**Given** Convergent Loop 相关 story
**When** ConvergentLoopProgress 组件渲染（UX-DR4）
**Then** 显示轮次可视化（●/◐/○）+ findings 统计 + 收敛率 + 当前状态

**Given** 任意层级
**When** 用户按 ESC
**Then** 返回上一层；`q` 退出 TUI

### Story 6.5: 搜索面板与响应式布局完善

As a 操作者,
I want 通过 `/` 搜索快速跳转到任何 story 或命令,
So that 在多 story 场景下导航效率不降。

**Acceptance Criteria:**

**Given** 用户在任意视图按 `/`
**When** 搜索面板激活（UX-DR16）
**Then** 显示 Input 搜索框，支持 story ID 直达、命令搜索、审批跳转
**And** 输入时模糊匹配实时过滤结果

**Given** 搜索结果
**When** 用户按 Enter
**Then** 跳转到对应 story 或执行对应命令

**Given** 搜索面板
**When** 用户按 ESC
**Then** 取消搜索，返回之前视图

**Given** 窄终端（100-139 列）
**When** Tab 视图模式渲染
**Then** 显示 Tab 切换：[1]审批 [2]Stories [3]成本 [4]日志
**And** ThreeQuestionHeader 压缩为最简模式（仅图标+数字）

**Given** TUI 与 Orchestrator 的进程关系
**When** TUI 崩溃
**Then** Orchestrator 继续后台运行不受影响
**And** 重新运行 `ato tui` 即可恢复

## Epic 7: Growth — 多项目并行与系统智能 (Phase 2)

用户可以同时编排多个项目的流水线，系统从历史数据中学习并自动优化参数，梯度降级确保复杂问题不阻塞流程。

### Story 7.1: 梯度降级

As a 系统,
I want 在 Claude fix 未收敛时自动降级到 Codex 攻坚，再失败降级为 Interactive Session,
So that 复杂问题不阻塞流水线，始终有人机协作兜底。

**Acceptance Criteria:**

**Given** Convergent Loop 中 Claude fix 达到 max_rounds 仍未收敛
**When** 触发梯度降级（FR42）
**Then** 自动切换到 Codex（workspace-write 沙箱）重新尝试修复

**Given** Codex 攻坚仍未收敛
**When** 第二级降级触发
**Then** 自动降级为 Interactive Session，人与 agent 协作解决

### Story 7.2: 多项目并行与资源分配

As a 操作者,
I want 同时编排多个项目的流水线，每个项目独立的状态存储,
So that 从单项目扩展到 ~3 个项目并行推进。

**Acceptance Criteria:**

**Given** 操作者运行 `ato project add <path>`
**When** 注册新项目
**Then** 每个项目独立的 SQLite 数据库，共享 Memory 层（FR41）

**Given** 多项目并行运行
**When** 并发 semaphore 分配
**Then** 系统在多项目间公平分配 agent 并发资源，无状态冲突

**Given** TUI 多项目视图
**When** 仪表盘渲染
**Then** 支持多项目切换或多列展示

### Story 7.3a: Memory 层与参数推荐

As a 系统,
I want 从历史运行数据提取模式并自动推荐参数,
So that 系统越用越精准。

**Acceptance Criteria:**

**Given** 系统积累了足够的运行数据
**When** Memory 层分析历史数据（FR43）
**Then** 自动推荐模型选择（复杂 story → Opus，简单 → Sonnet）
**And** 在 review prompt 中自动注入来自 Memory 的历史检查提示（FR46）

**Given** 推荐准确率验证
**When** 操作者使用系统 4 周后
**Then** 人工 override 比例 ≤ 30%（即推荐接受率 ≥ 70%）

### Story 7.3b: 手动 Finding 与 Severity Override

As a 操作者,
I want 手动添加 finding 并 override severity,
So that 可以将人类洞察注入质量体系。

**Acceptance Criteria:**

**Given** 操作者手动添加 finding（FR44）
**When** 标注分类
**Then** finding 入库并可被 Memory 层消费

**Given** 操作者在 TUI 中 override severity（FR45）
**When** 修改 finding severity
**Then** 系统行为（blocking/suggestion 判定）相应调整
**And** override 记录持久化，供 Memory 层分析

### Story 7.3c: TUI 成本面板与 UAT 趋势

As a 操作者,
I want 在 TUI 中查看成本面板和 UAT 趋势图,
So that 可以量化系统改进效果。

**Acceptance Criteria:**

**Given** TUI 增强（FR47）
**When** 渲染增强视图
**Then** 包含成本面板（按 story/阶段/模型分类）、UAT 趋势图（通过率随时间变化）、finding 详情面板（分类统计+趋势）

## Epic 9: 工作流阶段重构与设计产物持久化

在现有 phase/workspace 重构基础上，将 `designing` 从“只存在于状态机中的阶段”扩展为“有稳定设计产物合同、可持久化、可 gate、可被后续开发消费”的完整交付链。

### Story 9.1: 新增 Designing 阶段 — 可选的 UX 设计环节

As a 操作者,  
I want 工作流在 `creating` 之后、`validating` 之前显式增加可选的 `designing` 阶段，由 UX Designer 角色执行,  
So that 涉及 UI 的 story 在进入 validate 之前有一个专门的 UX 设计环节，而纯后端 story 可以在后续 story 中被安全跳过。

**Acceptance Criteria:**

- `CANONICAL_PHASES` 顺序变为 `planning → creating → designing → validating → ...`
- `create_done` 推进 `creating → designing`，新增 `design_done` 推进 `designing → validating`
- `designing` 复用高层 `planning` 状态，不新增 StoryStatus
- replay / recovery / config / tests 对齐 `designing`

### Story 9.1a: 修正 Designing 设计产物合同与 `.pen` 基线

As a 操作者,  
I want `designing` 阶段的 prompt、模板与核心产物合同明确对齐 Pencil 的真实行为,  
So that 后续实现不再依赖错误的“自动保存/加密格式”假设，设计阶段可以在正确的工程约束下落地。

**Acceptance Criteria:**

- designing prompt 不再宣称 `batch_design` 自动保存，也不再把 `.pen` 视为加密格式
- 仓库中新增可版本化的 `.pen` 模板基线
- 设计阶段核心工件路径被统一 helper 管理
- 测试覆盖模板、路径 helper 与 prompt 合同修正

### Story 9.1b: Designing 阶段强制落盘与设计快照链路

As a 操作者,  
I want designing 阶段在 Pencil 内存编辑完成后执行结构化强制落盘，并生成快照与保存报告,  
So that `.pen` 设计稿真正存在于磁盘上，系统崩溃后仍可恢复，后续 gate 有可靠真相源。

**Acceptance Criteria:**

- 设计完成后通过 `batch_get(readDepth=99, includePathGeometry=true)` 抓取完整内存节点树
- Python 在保留 `.pen` 顶层合同的前提下回写 `children`
- 生成 `prototype.snapshot.json` 与 `prototype.save-report.json`
- 保存后必须通过 JSON parse 与 MCP 回读验证

### Story 9.1c: Design Gate V2 与持久化验证

As a 操作者,  
I want `design_done` 前的 design gate 升级为基于磁盘真相与内容校验的严格门控,  
So that 空文件、假文件或只存在于内存中的设计状态不会被误判为已完成。

**Acceptance Criteria:**

- design gate 至少要求：`ux-spec.md`、`prototype.pen`、`prototype.snapshot.json`、`prototype.save-report.json`、至少 1 张 PNG
- `.pen` 必须可被 `json.load`，且 `save-report` 中 `json_parse_verified` 与 `reopen_verified` 为真
- failure payload 包含结构化 failure codes / missing files
- core / recovery 两条路径复用同一套 gate helper

### Story 9.1d: Prototype Manifest 与下游消费契约

As a 操作者,  
I want 每个 UI story 生成可供开发、验证、评审消费的 `prototype.manifest.yaml`,  
So that 后续阶段有统一入口理解该 story 的设计文件、导出图、主 frame、查阅顺序与设计约束，而不是各自猜测 UX 工件如何使用。

**Acceptance Criteria:**

- designing 阶段生成 `prototype.manifest.yaml`
- manifest 至少记录 story、spec、`.pen`、snapshot、save-report、PNG 导出、主 frame 与查阅顺序
- 下游 validating / developing / reviewing prompt 或上下文显式带入 manifest / PNG / `.pen`
- design gate 最终要求 manifest 存在且内容可校验

### Story 9.2: Workspace 概念引入 — 区分 Main 与 Worktree 执行环境

As a 操作者,  
I want 每个工作流阶段明确标注其执行环境（main 分支 vs worktree 分支），系统根据 workspace 类型决定是否创建 worktree,  
So that story 规格创建与主仓库控制阶段在 main 上执行，而真正修改代码的阶段在隔离 worktree 中执行，worktree 只在真正需要时才创建。

**Acceptance Criteria:**

- `PhaseConfig` / `PhaseDefinition` 新增 `workspace`
- main/worktree 阶段归属在 `ato.yaml.example` 中显式标注
- validating 走 `project_root`，reviewing / qa 继续走 `worktree`
- worktree 仅在首次进入 `developing` 时创建

### Story 9.3: 条件阶段跳过 + Story 规格自动提交主分支

As a 操作者,  
I want 系统在 story 不需要 UI 时自动跳过 `designing` 阶段，并在 story 规格验证通过进入 `dev_ready` 时自动将规格文件提交到本地 `main`,  
So that 纯后端 story 不被不必要的 UX 设计阶段阻塞，且所有已验证的 story 规格在创建 worktree 前就对并行开发的其他 story 可见。

**Acceptance Criteria:**

- `PhaseConfig` / `PhaseDefinition` 新增 `skip_when`
- `stories` 表新增 `has_ui`
- `designing` 可在 post-commit hook 中被安全跳过
- batch 内所有 story 到达 `dev_ready` 后执行单次本地 spec commit

### Story 9.4: 移除冗余 Planning 阶段

As a 操作者,
I want `planning` 阶段从生命周期中移除，使 `creating` 恢复为 batch 启动后的首个活跃阶段,
So that story 不再经历两个重复的 `/bmad-create-story` 调用，减少不必要的 agent 开销。

**Acceptance Criteria:**

- `CANONICAL_PHASES` 不再包含 `planning`（12 → 11 phases）
- `start_create` 事件直接推进 `queued → creating`
- `plan_done` 事件和 `planner` 角色被移除
- Batch 头部 story 初始化为 `current_phase="creating"`, `status="planning"`
- `StoryStatus` 高层 "planning" 聚合保持不变（映射 creating/designing/validating）
- 所有现有测试适配通过
- `ato.yaml.example` 无 planning phase 定义

**触发:** Sprint Change Proposal 2026-03-29 (remove-planning-phase)

## Epic 10: Runtime Reliability Hardening

系统在 CLI 已返回 result、worker PID 已退出、worktree gate 失败、transition ack timeout 或 BMAD parser fallback 超时时，仍能把任务收敛到 completed / failed / retryable approval / needs_human_review 的可恢复状态，避免 2026-04-08 事故中的静默卡死和审批死胡同。

**触发:** Sprint Change Proposal 2026-04-08 (终态收敛与恢复可靠性补强)
**FRs:** FR19, FR20, FR24, FR25, FR27, FR28, FR31, FR50, FR52
**NFRs:** NFR1, NFR2, NFR7, NFR8, NFR11, NFR12, NFR14

### Story 10.1: Terminal Finalizer 与 Dead PID Watchdog

As a 操作者,
I want CLI result 返回后的 task/cost/running 状态总能在有限时间内收敛,
So that worker PID 已退出或终态后处理卡住时，ATO 不会永久停在 running。

**Acceptance Criteria:**

- Given adapter 已返回 `AdapterResult`，when activity flush 或 DB helper 卡住/超时，then dispatch 在终态总超时内退出，task 最终为 `completed` 或 `failed`，`SubprocessManager.running` 注销对应 PID。
- Given cost_log 正常写入失败，when result 含 cost/token，then fallback 至少保证 task status、exit_code、cost_usd、text_result/error_message 可见。
- Given worker PID 已死但 DB task 仍为 `running`，when watchdog poll 运行，then task 被分类恢复、标记失败或创建 needs_human/recovery 入口，且记录结构化日志。
- Given finalizer fallback 执行，then structlog 包含 `terminal_finalizer_timeout`、`fallback_used`、`task_id`、`story_id`、`phase`。

### Story 10.2: Claude Result-First Semantics

As a 操作者,
I want Claude stream-json 中的 `type: result` 被视为业务完成信号,
So that `exit_code=1` 但结果完整时不会误触发 crash recovery 或重复调度。

**Acceptance Criteria:**

- Given Claude stream-json 已收到 `type: result` 且 process exit code 为 1，when adapter 完成，then 返回业务成功结果，不抛 `CLIAdapterError`。
- Given 无 result 且 exit code 非 0，then 仍按现有错误分类抛 `CLIAdapterError`。
- Given result 存在但 stderr 非空，then stderr/process exit code 记录为 warning metadata，不覆盖业务 result。
- Given schema 尚未支持 `process_exit_code`，then 短期返回对象必须避免被标为 `status="failure"`。

### Story 10.3: Transition/Preflight Recovery Semantics

As a 操作者,
I want transition ack timeout、worktree finalize 异常和 preflight_failure retry 都有明确恢复语义,
So that 状态转换不会被误判为业务失败，审批也不会被消费后无动作。

**Acceptance Criteria:**

- Given `submit_and_wait()` 调用方等待超时，then completion future 不被取消，后续 consumer 完成后结果仍可记录或查询。
- Given timeout 来自 ack 等待，then recovery/core 不把 task 直接标记为 failed。
- Given finalize 抛 `CLIAdapterError` 但 worktree 已 clean，then preflight gate 继续推进 transition。
- Given finalize 抛非 CLI 异常且 worktree 仍 dirty/unknown，then 创建 `preflight_failure` approval。
- Given story 已是 `blocked`，when 用户批准 `manual_commit_and_retry`，then 不提交非法 `dev_done/fix_done`，而是创建可操作恢复 approval 或保留原 approval 未消费。

### Story 10.4: BMAD Parser Reliability

As a 操作者,
I want 常见 PASS/Approve BMAD 输出走 deterministic fast-path，semantic fallback timeout 可配置,
So that review/QA 明确通过时不会因为 60s parser fallback 超时而进入不必要人工审批。

**Acceptance Criteria:**

- Given output contains `Verdict: PASS`、`STATUS: PASS`、`Recommendation: Approve`、`No blocking findings`、`0 blocking` or `0 patch`，then deterministic parser returns pass/approved without semantic fallback。
- Given deterministic parser misses and semantic fallback is needed，then timeout comes from settings, not a hard-coded 60s。
- Given semantic fallback times out，then parse_failed payload includes `skill_type`、`input_length`、`timeout_seconds`、`parser_mode`、preview。
- Given parse_failed creates approval，then approval summary explains parser infrastructure failure, not code quality failure。

### Story 10.5: Merge Queue Boundary Hygiene

As a 操作者,
I want merge queue 在暴露可操作 approval 前先释放内部锁，并复用一致的 worktree dirty parser,
So that pre-merge failure retry 不会遇到 stale lock，维护性风险也被收敛。

**Acceptance Criteria:**

- Given pre-merge gate persistent failure，when approval is visible，then merge queue entry has already been marked failed/retryable and `current_merge_story_id` is released。
- Given `preflight_failure` approval is decided quickly after creation，then retry does not observe stale merge lock。
- Given porcelain dirty parser handles rename/untracked/space path/malformed line，then transition_queue and merge_queue use the same implementation。
- Given `_run_pre_merge_gate()` exits through unexpected exception paths，then `second_result` cannot be referenced before assignment。

### Story 10.6: Incident Regression Suite

As a 操作者,
I want 2026-04-08 事故链被固化为自动化回归测试,
So that 后续修改 subprocess、transition、approval、worktree gate 或 BMAD parser 时不会重引入同类故障。

**Acceptance Criteria:**

- Given a simulated Claude result followed by post-result DB/flush timeout，then Orchestrator does not remain silently stuck。
- Given result+exit_code=1 during finalize，then worktree git verification decides clean-or-approval。
- Given transition ack timeout，then task is not marked failed without confirmation。
- Given blocked story receives `manual_commit_and_retry`，then recovery remains actionable。
- Given BMAD PASS output，then no semantic fallback subprocess is invoked。
- Given `creating` initial dispatch writes a canonical artifact path，then `test_initial_dispatch.py` no longer expects the legacy `"initial_dispatch_requested"` placeholder。
