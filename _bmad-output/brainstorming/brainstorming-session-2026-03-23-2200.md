---
stepsCompleted: [1, 2, 3, 4]
inputDocuments: ['docs/agent-team-orchestrator-system-design-input-2026-03-23.md']
session_topic: 'Agent Team Orchestrator 系统设计 — 架构技术方案、关键风险攻克、盲点与替代路径'
session_goals: '产出一组可执行的架构决策(ADR风格)，直接指导后续实现'
selected_approach: 'ai-recommended'
techniques_used: ['Assumption Reversal', 'Morphological Analysis', 'Chaos Engineering']
ideas_generated: [25]
session_active: false
workflow_completed: true
context_file: 'docs/agent-team-orchestrator-system-design-input-2026-03-23.md'
---

# Brainstorming Session Results

**Facilitator:** Enjoyjavapan163.com
**Date:** 2026-03-23

## Session Overview

**Topic:** Agent Team Orchestrator 系统设计 — 架构技术方案、关键风险攻克、盲点与替代路径
**Goals:** 产出一组可执行的架构决策(ADR风格)，直接指导后续实现

### Context Guidance

_基于系统设计输入文档，聚焦：(1) 五层架构各组件的技术选型与实现路径 (2) 7个关键风险的解决策略 (3) 设计盲点、隐含假设和替代架构方向_

### Session Setup

_三条线索交叉发散，目标是从技术方案、风险应对、盲点分析三个维度碰撞出可落地的架构决策_

## Technique Execution Results

### Phase 1: Assumption Reversal（假设反转）

**反转的假设与发现：**

**假设#1: 完全无状态per-task代理调用**
- 发现：冷启动成本真实存在，Developer在review-fix循环中、Architect在跨story审查中需要上下文延续
- 决策方向：**Context Briefing模式** — 系统从每次task输出中提取结构化工作记忆摘要，作为下次fresh session的输入artifact。代理仍然无状态，但"工作记忆"被外化为系统管理的artifact
- 用户确认：上下文爆炸（指挥官常驻poll）vs 冷启动成本 是真实张力点

**假设#2: Artifact schema设计是最关键的决策**
- 发现：BMAD已有成熟的schema体系，符合软件工程标准
- 决策方向：**确认现有BMAD schema足够**，不需要重新设计artifact结构

**假设#3: Review→Fix是两个独立task的简单序列**
- 发现：Codex review + Claude fix 三轮不收敛的根因是跨agent意图传递不精确 + 系统缺少finding级收敛追踪
- 决策方向：**引入Convergent Loop作为第三种task type**（除Structured Job和Interactive Session外）
- 通用协议：deterministic check → scoped agent review → system.diff(findings) → agent fix(targeted) → 收敛或escalate
- 关联发现：validate-create-story循环也有相同模式

**假设#4: Validation是一个agent的智能判断**
- 发现：autoresearch的核心启发——评估函数不可变，agent不可篡改评判标准
- 决策方向：**两层验证模型**
  - 第一层：系统级deterministic check（不可变validation contract，秒级完成）
  - 第二层：agent级qualitative review（每轮scope递减，严格收窄）

**假设#5: 需要完整代码系统 vs autoresearch极简主义**
- 发现：问题本身有6/8项超出shell script的自然能力边界（结构化查询、事务性状态、并发控制、崩溃恢复）
- 决策方向：**问题不可避免需要"真系统"，但不需要"复杂系统"**
- 估算：正确工具（Python + SQLite）下，核心代码约1000-1500行，远低于旧系统4000+行shell体系
- 旧系统教训：75%复杂度来自tmux（40%）+ LLM编排（35%），属于偶然复杂度

**假设#6: 旧系统的复杂度是问题本质决定的**
- 发现：旧系统40个文件中~30个在弥补tmux/bash/LLM编排的基础设施不足，仅~10个处理真正业务逻辑
- 决策方向：新设计砍掉tmux和LLM编排，直接消除75%偶然复杂度

**假设#7: 去掉tmux意味着放弃实时可视化监控**
- 发现：用户70%监控工作量是在监控LLM指挥官是否遵守规则，而非监控agent工作内容
- 典型偏离：指挥官创建错误tmux布局、指挥官在L1决策点不该询问人却询问
- 决策方向：编排者变为代码后，规则遵守由代码保证，监控需求大幅缩减
  - Structured Jobs → TUI状态仪表盘（状态/耗时/结果），不需要实时输出流
  - Interactive Sessions → 独立终端窗口，人直接参与，系统只注册/计时/收artifact

### Phase 2: Morphological Analysis（形态分析）

**核心组件选型决策：**

**关键约束条件（用户澄清）：**
- 通过CLI（claude/codex命令行）与agent交互，不使用SDK直调API
- Claude Code = 执行者（创建、实现、修复）；Codex = 审核者（验证、审查、疑难攻关）
- BMAD是开源仓库，不修改其工作流代码，通过适配层对接

**组件1: Orchestrator核心语言 → Python asyncio**
- asyncio.create_subprocess_exec 原生支持并发启动多个CLI进程
- SQLite原生支持（aiosqlite）
- Textual TUI框架是Python生态
- 无SDK依赖，纯subprocess编排

**组件2: 状态存储 → SQLite**
- 事务性解决并发写入
- 结构化查询支持finding级追踪
- WAL模式支持崩溃恢复
- 单文件无外部依赖

**组件3: Worker调用模式 → 按角色分化**
- Structured Job: subprocess.run + JSON/Markdown输出
- Interactive Session: 启动独立终端，系统只注册PID和worktree

**组件4: CLI调用方式**
- Claude Code: `claude --bare -p "prompt" --output-format json --json-schema` 或 `-w worktree` 模式
- Codex: `codex exec "prompt" --full-auto --output-schema schema.json -o result.json`
- 两者都支持：工具限制（--allowedTools/--disallowedTools）、执行限制（--max-turns/--max-budget-usd）、session续接（--resume）
- Claude内置 `--worktree` 支持，简化worktree管理
- 角色边界通过工具限制在系统层强制（reviewer不可Edit/Write）

**组件5: 结果收集 → 混合策略**
- 直接JSON输出：适用于自定义prompt的structured job
- BMAD适配层：BMAD skill输出Markdown → 二次轻量调用解析为schema JSON
  - BMAD原样不改，保持与开源仓库同步
  - 适配层负责：提取findings、分类severity、判定verdict
  - 成本极低（--max-turns 1，纯文本解析）

**组件6: TUI → Python Textual**
- 与Orchestrator同语言，直接读SQLite渲染状态

**关键架构模式：BMAD适配层**
```
BMAD Skill(原样) → Markdown文档 → 适配层(轻量LLM解析) → Schema JSON → SQLite
```
- 适用于所有BMAD skill输出：code-review, story-validation, architecture-review, QA-report
- BMAD负责专业方法论，适配层负责结构化翻译，Orchestrator负责状态追踪
- 三层职责完全分离，BMAD升级不受影响

**Convergent Loop完整流程（以code-review为例）：**
```
阶段1: codex exec + BMAD code-review --yolo → review-raw.md
阶段2: claude --bare --json-schema → findings JSON（severity + verdict）
阶段3: Orchestrator 入库 SQLite，追踪finding级状态
阶段4: 拼接open blocking findings → claude -w story-xxx 修复
阶段5: 重复阶段1-3（scope收窄为验证闭合）
阶段6: 收敛判定（all blocking closed → QA / round>=3 → escalate）
```

### Phase 3: Chaos Engineering（混沌工程）

**压力测试场景与应对策略：**

**场景#1: BMAD适配层解析失败（BMAD更新导致Markdown格式变化）**
- 评估：适配层是LLM语义解析+json-schema强制，对格式变化有天然鲁棒性
- 残余风险：BMAD改了review方法论本身导致findings质量变化
- 应对：适配层prompt不硬编码BMAD具体结构名称；BMAD升级时人工回归验收

**场景#2: Convergent Loop永不收敛（修一个bug引入新bug）**
- 三层防御：
  - 硬性上限：max_rounds=3，超限escalate
  - 收敛度指标：convergence_rate < 0.5连续两轮 → 提前escalate
  - 梯度降级：3轮Claude失败 → Codex攻坚修复 → 仍失败 → 人+agent在worktree协作

**场景#3: 并发worktree的SQLite写入冲突**
- 应对：所有状态转换通过单一async队列串行化
- subprocess回调可并发触发，但转换执行串行，无竞态

**场景#4: Orchestrator进程崩溃后恢复**
- SQLite WAL模式自动恢复数据
- 重启后扫描status=running的task，检查PID存活和artifact存在
- 进程还活 → 重新注册监听；进程已死有artifact → 继续流水线；无artifact → 重新调度

**场景#5: Merge后Regression失败**
- regression failure自动冻结Merge Queue
- 创建human approval（revert/fix forward/pause pipeline三选一）
- 绝不允许在broken main上继续merge

**场景#6: 适配层severity误判（suggestion误判为blocking）**
- 三层防护：prompt明确severity判定规则和示例；blocking数量异常时请求human快速确认；human可在TUI中override任何finding的severity

**场景#7: Worktree merge冲突**
- Claude自动解决冲突，冲突解决后重新进入review
- Claude解决失败 → escalate to human

**场景#8: BMAD skill执行超时**
- 不自动kill，通知human决策（继续等待/终止）
- 多个超时approval并行进入队列，互不覆盖

**场景#9: Agent成本失控**
- Story级成本累计追踪（Claude JSON输出含cost_usd）
- 超限触发budget_exceeded approval

## Idea Organization and Prioritization

### 架构决策记录（ADR）— 完整清单

#### 主题一：系统核心架构

**ADR-01: 编排者是代码，不是LLM**
- 决策：Orchestrator是Python asyncio程序，所有调度、状态转换、规则执行由代码逻辑完成
- 理由：旧系统75%复杂度来自LLM编排+tmux；LLM指挥官的规则违反（错误tmux布局、越级询问）需要人盯
- 影响：消除对tmux的依赖；消除forbidden-list式的规则累积；监控需求从"盯agent行为"降级为"看状态仪表盘"
- 来源：Phase1 假设#6, #7

**ADR-02: Python asyncio + SQLite 作为核心技术栈**
- 决策：Orchestrator用Python asyncio构建，运行时状态存储在SQLite（WAL模式）
- 理由：asyncio原生支持并发subprocess管理；SQLite提供事务性、结构化查询、崩溃恢复；单文件无外部依赖；估算核心代码1000-1500行
- 影响：不需要数据库服务器；不需要消息队列；所有状态可通过SQL查询
- 来源：Phase1 假设#5, Phase2 组件1/2

**ADR-03: TUI用Python Textual框架**
- 决策：第一交互界面使用Textual构建
- 理由：与Orchestrator同语言；直接读SQLite渲染状态；成熟的终端UI框架
- 来源：Phase2 组件6

**ADR-04: 状态转换通过单一async队列串行化**
- 决策：所有story的phase转换提交到一个TransitionQueue，串行执行
- 理由：防止多个worktree agent并发完成时的竞态条件；subprocess回调可并发触发，但转换执行原子化
- 实现：asyncio.Queue + 单消费者循环 + SQLite事务
- 来源：Phase3 场景#3

**ADR-05: 崩溃恢复基于SQLite + PID + Artifact检查**
- 决策：每个subprocess启动时将PID和预期artifact路径写入SQLite；Orchestrator重启后通过查表+检查PID存活+检查artifact存在来重建运行状态
- 理由：相比旧系统的generation fencing + event log replay + pane状态扫描，新方案极大简化恢复逻辑
- 来源：Phase3 场景#4

#### 主题二：Agent调用与协作模型

**ADR-06: Claude Code = 执行者，Codex = 审核者**
- 决策：Claude Code负责创建、实现、修复；Codex负责验证、审查、疑难攻关
- 理由：不可自我认证原则的架构级硬分离——创建者和验证者使用不同的模型和CLI
- 影响：Convergent Loop中reviewer和fixer天然是不同agent
- 来源：用户澄清

**ADR-07: 通过CLI subprocess调用agent，不使用SDK**
- 决策：通过 `claude` 和 `codex` 命令行工具调用agent，Orchestrator使用asyncio.create_subprocess_exec
- Claude: `claude --bare -p "prompt" --output-format json`
- Codex: `codex exec "prompt" --full-auto -o result.json`
- 理由：统一编排两种不同的CLI；Orchestrator独立于任何LLM SDK生态；CLI工具自带完整能力（文件读写、bash、tool use）
- 来源：用户澄清, Phase2 组件4

**ADR-08: 角色边界通过CLI工具限制在系统层强制**
- 决策：Reviewer角色使用 `--disallowedTools "Edit,Write,Bash"` 使其在系统层无法编辑文件；Developer角色使用 `--allowedTools "Read,Edit,Write,Bash"`
- 理由：替代旧系统中forbidden-list的人工规则约束；系统保证而非LLM自律
- 影响：旧系统中"Codex validator开始编辑文件"（F10）这类问题在架构层被消除
- 来源：Phase2 组件4

**ADR-09: 结构化输出通过CLI schema参数强制**
- 决策：使用Claude的 `--json-schema` 和Codex的 `--output-schema` 强制agent输出符合预定义的JSON Schema
- 理由：constrained decoding在API层保证JSON合法，不依赖prompt约束格式；Orchestrator可直接信任输出结构
- 影响：finding schema、verification schema等均由系统定义，agent无法绕过
- 来源：Phase2 组件5

**ADR-10: 三种Task Type — Structured Job / Interactive Session / Convergent Loop**
- 决策：
  - Structured Job：单次执行收结果（batch assessment, QA generation, regression等）
  - Interactive Session：人参与的独立终端会话（complex dev, exploratory debugging等）
  - Convergent Loop：多轮迭代收敛（review-fix, validate-create-story等）
- 理由：原设计文档区分了Structured Job和Interactive Session，但遗漏了多轮不收敛这个核心痛点
- 影响：Convergent Loop成为系统的一等公民概念，有专门的finding追踪和收敛判定机制
- 来源：Phase1 假设#3

**ADR-11: Interactive Session启动独立终端窗口**
- 决策：系统打开新终端窗口启动agent进程（如 `open -a Terminal`），系统只注册PID、worktree路径、启动时间
- 理由：不需要relay agent输出（消除agent-wrapper/FIFO/sentinel的全部复杂度）；人直接在终端中交互，想停就Ctrl-C
- 影响：旧系统中agent-wrapper.py、三层读取协议、task-monitor.sh全部不需要
- 来源：Phase1 假设#7

**ADR-12: Context Briefing模式替代长session**
- 决策：系统从每次task输出中提取结构化工作记忆摘要，作为下一次fresh session的输入artifact；短循环（≤3轮）可使用CLI的session resume功能
- 理由：长session导致上下文爆炸和规则遗忘（旧系统指挥官常驻的教训）；完全无状态则冷启动成本过高
- 混合策略：Convergent Loop内的fix轮次用 `--resume session-id`；跨task边界用Context Briefing
- 来源：Phase1 假设#1

#### 主题三：Convergent Loop与BMAD集成

**ADR-13: 两层验证 — deterministic contract + scoped agent review**
- 决策：
  - 第一层：系统级deterministic check（不可变validation contract，检查必填字段、结构完整性等，秒级完成）
  - 第二层：agent级qualitative review（语义质量审查，每轮scope递减）
- 理由：autoresearch的核心启发——评估函数不可变，agent不可篡改评判标准；先过第一层才进入消耗token的第二层
- 来源：Phase1 假设#4

**ADR-14: Finding级状态追踪**
- 决策：每个finding在SQLite中有独立记录（finding_id, story_id, round, severity, category, status），系统追踪其跨轮次闭合状态
- 理由：旧系统Codex review + Claude fix三轮不收敛的根因是系统不知道"哪个具体finding解决了、哪个没有"
- 实现：review产出findings → 入库open → fix后re-review → 更新每个finding的status(closed/still_open/new)
- 来源：Phase1 假设#3

**ADR-15: 每轮review scope严格收窄**
- 决策：第1轮全量review → 第2轮仅验证上轮open findings的闭合状态 + 检查新引入问题 → 第3轮仅验证仍未闭合的blocking
- 理由：防止每轮全量重审导致评判标准漂移和finding集合不断变化
- 实现：Orchestrator从SQLite查询open findings，拼接为re-review prompt的scope限定
- 来源：Phase1 假设#3, #4

**ADR-16: 梯度降级策略 — Claude fix → Codex攻坚 → 人+agent协作**
- 决策：3轮Claude修复未收敛 → Codex切换为fixer角色尝试修复（`--dangerously-bypass-approvals-and-sandbox`） → 仍失败 → 降级为Interactive Session，人+agent在worktree中协作
- 理由：不同agent可能有不同的问题解决能力；自动化尝试穷尽后再打断人
- 影响：Codex在前3轮是reviewer（只读sandbox），第3.5轮切换为fixer（可写sandbox）
- 来源：Phase3 场景#2, 用户决策

**ADR-17: BMAD skill原样不改，通过适配层对接**
- 决策：不修改BMAD开源仓库的任何工作流代码；在Orchestrator中构建薄适配层，将BMAD Markdown输出解析为结构化JSON
- 适配层调用：`claude --bare -p "解析以下review文档" --json-schema findings.schema.json --max-turns 1`
- 理由：BMAD是开源仓库持续更新，修改后无法与上游同步；LLM语义解析对Markdown格式变化有天然鲁棒性
- 影响：BMAD负责专业方法论，适配层负责结构化翻译，Orchestrator负责状态追踪——三层职责完全分离
- 来源：用户约束, Phase2 组件5

**ADR-18: BMAD code-review等skill用yolo模式运行**
- 决策：在codex exec非交互环境下，BMAD skill使用yolo模式（`--yolo`）跳过交互checkpoint，自动执行完整工作流并输出Markdown结果
- 理由：yolo模式跳过发现/引导阶段的checkpoint，在codex exec --full-auto下可无人执行；headless模式需要改BMAD代码（违反ADR-17）
- 来源：Phase2, 用户决策

**ADR-19: 适配层prompt包含明确的severity判定规则**
- 决策：severity分类规则硬编码在适配层prompt中——blocking仅限安全漏洞/逻辑错误/数据丢失/AC违反；性能/设计/可维护性→suggestion；存疑时降级为suggestion
- 理由：防止LLM判断漂移导致severity通胀；明确规则让判定可预测可审计
- 安全网：blocking数量>阈值时请求human快速确认；human可在TUI中override任何finding的severity
- 来源：Phase3 场景#6

**ADR-20: Approval Queue持久化在SQLite中**
- 决策：所有需要human决策的事项（batch选择、merge授权、超时处理、regression失败、成本超限等）作为独立记录写入SQLite approval表
- 理由：多个并发approval互不覆盖、不丢失；TUI渲染完整队列；旧系统中"两个pane同时弹出询问只能看到一个"的问题被消除
- 实现：每条approval有id, type, story_id, details, options, status, decision, decided_at
- 来源：Phase3 场景#5, #8

#### 主题四：运维与安全网

**ADR-21: Regression失败自动冻结Merge Queue**
- 决策：任何story的regression失败后，系统自动冻结整个merge queue，阻止后续story merge到broken main上
- 理由：在broken main上叠加变更会造成级联问题
- 恢复：human从三个选项中决策——revert并重试 / fix forward / 暂停流水线
- 来源：Phase3 场景#5

**ADR-22: Merge冲突由Claude自动解决，失败escalate**
- 决策：worktree rebase产生冲突时，Claude自动在worktree中解决；冲突解决后重新进入review流程
- 理由：大部分merge冲突是机械性的，agent可以处理；代码变更后需要重新review保证质量
- 安全网：Claude解决失败 → escalate to human（Interactive Session）
- 来源：Phase3 场景#7

**ADR-23: Task超时通知human决策，不自动kill**
- 决策：task执行超过预设时间阈值后，创建timeout approval通知human，由human选择继续等待或终止
- 理由：自动kill可能丢失有价值的进行中工作；人比系统更能判断"是真的卡住了还是在处理大量工作"
- 来源：Phase3 场景#8, 用户决策

**ADR-24: Story级成本追踪，超限触发approval**
- 决策：记录每次CLI调用的成本（Claude JSON输出含cost_usd），按story累计；超过预设上限时触发budget_exceeded approval
- 理由：防止agent在某个task上消耗大量token而无人察觉
- 实现：cost_log表记录每次调用，story维度聚合查询
- 来源：Phase3 场景#9

**ADR-25: Blocking数量异常时请求human快速确认**
- 决策：当单次review产出的blocking finding数量超过阈值时，不自动进入fix loop，而是先在TUI展示findings摘要请求human确认
- 理由：防止适配层severity误判（如把所有suggestion都标为blocking）导致无效的fix循环浪费资源
- 来源：Phase3 场景#6

## Session Summary and Insights

### 关键成果

- **25条可执行架构决策（ADR）**，覆盖系统核心、Agent协作、Convergent Loop、运维安全网四个维度
- **发现并解决了原始设计文档中的3个关键盲点**：Convergent Loop作为第三种task type、BMAD适配层模式、梯度降级策略
- **从旧系统（bmad-master-control）提取了关键教训**：75%复杂度是偶然复杂度、LLM编排者需要人盯的监控成本、tmux作为进程管理器的脆弱性
- **整合了外部启发（autoresearch）**：不可变评估函数原则、git-as-state-machine、评估函数与执行者的分离

### 核心架构创新

1. **Convergent Loop协议** — 解决了多轮不收敛的核心痛点，通用于review-fix、validate-create、QA-fix等所有迭代场景
2. **BMAD适配层** — 在不修改开源仓库的前提下实现结构化集成，LLM语义解析提供格式鲁棒性
3. **两层验证模型** — deterministic contract + scoped agent review，借鉴autoresearch的不可变评估函数思想
4. **梯度降级** — Claude fix → Codex攻坚 → 人+agent协作，自动化尽力后再打断人

### 下一步行动

1. **将25条ADR整理为独立的架构决策文档**，作为后续BMAD流程（PRD/Architecture）的输入
2. **设计SQLite schema**（stories, tasks, findings, approvals, cost_log表结构）
3. **原型验证关键路径**：单个Convergent Loop（code-review → fix → re-review）的端到端调用
4. **定义所有JSON Schema文件**（code-review-findings, finding-verification, story-validation等）
