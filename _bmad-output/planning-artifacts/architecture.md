---
stepsCompleted: [1, 2, 3, 4, 5, 6, 7, 8]
lastStep: 8
status: 'complete'
completedAt: '2026-03-24'
inputDocuments:
  - '_bmad-output/planning-artifacts/prd.md'
  - '_bmad-output/planning-artifacts/research/technical-claude-codex-cli-integration-research-2026-03-24.md'
  - 'docs/agent-team-orchestrator-system-design-input-2026-03-23.md'
  - '_bmad-output/planning-artifacts/ux-design-specification.md'
workflowType: 'architecture'
project_name: 'AgentTeamOrchestrator'
user_name: 'Enjoyjavapan163.com'
date: '2026-03-24'
---

# Architecture Decision Document

_This document builds collaboratively through step-by-step discovery. Sections are appended as we work through each architectural decision together._

## Project Context Analysis

### Requirements Overview

**Functional Requirements:**

53 条功能需求覆盖 10 个领域。核心架构驱动力来自三个方面：

1. **编排引擎（FR1-FR5, FR24-FR28）：** 声明式 YAML 配置 → 状态机动态构建 → 自动推进 story 生命周期。所有运行时状态持久化到 SQLite WAL，支持崩溃后零数据丢失恢复。这决定了系统核心必须是一个 asyncio 事件循环 + 嵌入式状态存储的单进程架构。

2. **双 CLI 异构 Agent 调用（FR6-FR12, FR53）：** Claude CLI (`claude -p`) 和 Codex CLI (`codex exec`) 作为角色工作者，输出格式、权限模型、成本字段均不同。需要统一的 adapter 抽象层隔离差异。由于无 API Key，Claude 必须使用 OAuth 认证（非 `--bare` 模式），BMAD skills 在 `claude -p` 调用时自动加载。

3. **Convergent Loop 质量门控（FR13-FR18）：** review → finding 入库 → fix → re-review（scope 收窄）→ 收敛判定或 escalate。Finding 级跨轮次状态追踪是核心数据模型之一，直接影响 SQLite schema 设计和状态机 transition 路径。

**Finding 跨轮次匹配算法：**

- **匹配键：** `file_path` + `rule_id` + `severity`
- **去重哈希：** SHA256 of `(file_path, rule_id, severity, normalize(description))`
- **状态分类逻辑：**
  - 上轮 open + 本轮匹配到 → `still_open`
  - 上轮 open + 本轮未匹配到 → `closed`
  - 本轮存在 + 上轮无匹配 → `new`
- **Re-review scope narrowing：** 仅将上轮 `open` findings 的匹配键集合传入 re-review prompt，reviewer 只需验证这些 findings 的闭合状态和新引入问题

**Non-Functional Requirements:**

14 条 NFR 中，架构影响最大的是：
- **NFR1/NFR7（崩溃恢复）：** ≤30s 恢复时间 + 自动恢复所有可恢复 task → 要求 SQLite 记录每个 task 的 PID、expected_artifact、状态
- **NFR6（WAL 零数据丢失）：** 单进程写 + TUI 独立进程读 → WAL 模式完美适配（但 TUI 也有写入路径，见下文）
- **NFR11（CLI adapter 隔离）：** CLI 版本升级只影响 adapter 层 → 要求 adapter 接口稳定，编排核心不直接依赖 CLI 参数

**Scale & Complexity:**

- Primary domain: 后端编排系统（CLI subprocess + TUI + SQLite）
- Complexity level: 中-高（MVP 中等，三重本质复杂度无行业先例）
- Estimated architectural components: 8 个核心组件（Orchestrator Core、State Machine、Transition Queue、Subprocess Manager、CLI Adapters、Convergent Loop、Approval Queue、TUI）

### Technical Constraints & Dependencies

| 约束 | 来源 | 架构影响 |
|------|------|---------|
| 无 ANTHROPIC_API_KEY | 环境限制 | 不能用 `--bare` 和 Agent SDK，必须 `claude -p` (OAuth) |
| Codex 无 `--max-turns` | CLI 能力限制 | Orchestrator 用 `asyncio.wait_for` 超时控制 |
| Codex 无直接成本字段 | CLI 能力限制 | 需维护模型价格表，从 JSONL token 数计算成本 |
| Codex 无工具级权限控制 | CLI 能力限制 | 用 `--sandbox read-only`（默认）实现 reviewer 只读 |
| Python ≥3.11 | asyncio TaskGroup 依赖 | 限定最低运行时版本 |
| BMAD skills 不可修改 | 项目约束 | 适配层用 LLM 语义解析 Markdown → JSON |
| 本地单用户单进程 | 系统设计原则 | 不需要分布式协调，SQLite 足够 |
| `claude -p` 非 bare 模式加载项目配置 | 认证约束副作用 | BMAD skills 自动可用（利），但冷启动性能和 token 消耗影响需量化（弊） |

### Cross-Cutting Concerns Identified

1. **崩溃恢复与重启路径分离：** 贯穿所有组件——需明确区分"正常重启"（配置变更触发）和"崩溃恢复"（意外中断触发）两条路径。每个 subprocess 启动时注册 PID/artifact，状态转换串行化，SQLite WAL 保证一致性。
2. **成本追踪与 CLI 调用量化：** 贯穿所有 CLI 调用——Claude 直接读取 `total_cost_usd`，Codex 需从 token 计算。Convergent Loop 3 轮需 ~9 次 CLI 调用（每轮 review + adapter 解析 + fix），累积延迟和成本是关键约束。

**Codex 成本计算价格表：**

- 结构：Python dict 常量，位于 `adapters/codex_cli.py`
  ```python
  CODEX_PRICE_TABLE: dict[str, dict[str, Decimal]] = {
      "codex-mini-latest": {"input_per_1m": Decimal("1.50"), "output_per_1m": Decimal("6.00")},
      # 新模型在此添加
  }
  ```
- 成本计算：`cost = input_tokens * price["input_per_1m"] / 1_000_000 + output_tokens * price["output_per_1m"] / 1_000_000`
- 更新策略：模型定价变更时手动更新代码，通过 `pyproject.toml [tool.ato]` 记录已验证的 CLI 版本

3. **CLI Adapter 隔离与契约守护：** 两个 CLI 的参数体系、输出格式、权限模型完全不同，adapter 层是核心抽象边界。CLI 输出格式变更可能导致 mock 过时而测试仍绿——需要契约测试或 snapshot 验证守住边界。
4. **TUI 双向通信模型：** TUI 不是"只读 SQLite"——FR20/FR21 要求 TUI 写入审批决策和 UAT 结果。架构上需明确 TUI 写入路径（直接写 SQLite + `busy_timeout` 还是通过 IPC），这影响一致性保证。
5. **并发控制与进程清理：** Semaphore 限制 CLI 进程数 + TransitionQueue 串行化状态转换 + SQLite WAL 支持读写并发。`asyncio.wait_for` 超时后需显式 `proc.terminate()` + `proc.wait()` 清理，防止 zombie process 累积。
6. **错误分类与恢复策略：** 认证过期、超时、解析失败、rate limit 各有不同的恢复策略，需要统一的错误处理矩阵。
7. **Interactive Session 边界管理：** FR10 定义系统只"启动、注册、计时、收 artifact"，但 session 完成检测（"手动提交"触发什么状态转换？谁检测 session 完成？）是编排与人机交互的核心边界，需要独立的架构决策。
8. **结构化日志与可观测性：** 调试 Convergent Loop 不收敛等场景需要每轮 findings diff、prompt 内容、CLI 原始输出。结构化日志是架构级需求，非事后可加。
9. **系统初始化与 Onboarding 路径：** `ato init` 的环境检测流程（CLI 安装检测、认证验证、失败引导）是"首次成功体验 ≤ 半天"的架构支撑，需要预先设计检测和引导机制。

### Architecture Scope Clarifications Needed

以下问题需要在后续架构决策中明确：

1. **声明式配置的表达力边界：** 哪些行为（如 Convergent Loop 内的 scope 收窄、梯度降级）可配置，哪些硬编码在引擎中？过度配置化会导致配置引擎成为系统中最复杂的部分。
2. **状态机测试覆盖率的精确定义：** "100% transition 覆盖"是指每个 transition 至少执行一次，还是每条端到端状态路径？排列组合差异巨大，影响测试架构设计。
3. **`claude -p` 非 bare 模式的性能基线：** 需要量化每次 CLI 调用加载 BMAD skills 的冷启动时间和额外 token 消耗，确认是否在可接受范围内。

## Starter Template Evaluation

### Primary Technology Domain

Python CLI/TUI 后端编排应用。非 Web 框架项目，不适用传统 Web starter template。
项目脚手架聚焦于 Python 项目初始化、依赖管理和开发工具链配置。

### Starter Options Considered

| 方案 | 说明 | 结论 |
|------|------|------|
| uv init + 手动配置 | uv 生成 pyproject.toml，手动添加依赖和工具配置 | ✅ 推荐 |
| Poetry new | Poetry 生成项目骨架 | ❌ 生态不匹配（ruff 用 Astral） |
| cookiecutter-hypermodern-python | 全功能 Python 项目模板 | ❌ 过重，引入不需要的 CI/CD 配置 |
| 纯手写 pyproject.toml | 完全手动 | ❌ 不如 uv init 高效 |

### Selected Starter: uv init + 手动配置

**Rationale for Selection:**
- 与已选定的 ruff 同属 Astral 生态，工具链一致性最高
- `uv init` 生成标准 pyproject.toml，`uv.lock` 确保可复现构建
- 依赖解析速度是 Poetry 的 10-100 倍
- 2026 年 Python 社区推荐的项目管理标准
- 项目结构简单明确，不需要重量级模板

**Initialization Command:**

```bash
uv init agent-team-orchestrator --python ">=3.11"
cd agent-team-orchestrator
uv add aiosqlite "python-statemachine>=3.0" "textual>=2.0" "pydantic>=2.0" typer
uv add --group dev pytest pytest-asyncio ruff mypy pre-commit
```

**Architectural Decisions Provided by Starter:**

**Language & Runtime:**
- Python ≥3.11，pyproject.toml 标准配置
- uv.lock 锁定依赖版本，确保可复现构建

**Build Tooling:**
- uv 作为包管理器和虚拟环境管理器
- hatchling 作为构建后端（uv 默认推荐）

**Testing Framework:**
- pytest + pytest-asyncio（dev dependency group）
- 测试文件放在 tests/ 目录

**Code Quality:**
- ruff（lint + format，Astral 生态）
- mypy（类型检查）
- pre-commit hooks

**Code Organization:**

```
agent-team-orchestrator/
├── pyproject.toml              # 项目配置（uv init 生成）
├── uv.lock                     # 依赖锁定（自动生成，提交到 VCS）
├── src/
│   └── ato/                    # 主包（ato = Agent Team Orchestrator）
│       ├── __init__.py
│       ├── core.py             # 主事件循环、启动/恢复
│       ├── state_machine.py    # StoryLifecycle 状态机
│       ├── transition_queue.py # TransitionQueue
│       ├── subprocess_mgr.py   # SubprocessManager
│       ├── convergent_loop.py  # Convergent Loop 协议
│       ├── recovery.py         # 崩溃恢复
│       ├── adapters/
│       │   ├── claude_cli.py   # Claude CLI 封装
│       │   ├── codex_cli.py    # Codex CLI 封装
│       │   └── bmad_adapter.py # BMAD Markdown → JSON
│       ├── models/
│       │   ├── schemas.py      # Pydantic models
│       │   └── db.py           # SQLite schema + helpers
│       ├── tui/
│       │   ├── app.py          # Textual App
│       │   ├── dashboard.py    # 主仪表盘
│       │   └── approval_view.py# 审批交互
│       └── cli.py              # CLI 入口点 (typer)
├── schemas/                    # JSON Schema 文件
│   ├── review-findings.json
│   ├── story-validation.json
│   └── finding-verification.json
├── tests/
│   ├── unit/
│   ├── integration/
│   └── conftest.py
└── .pre-commit-config.yaml
```

**Development Experience:**
- `uv run` 自动激活虚拟环境执行命令
- `uv run pytest` 运行测试
- `uv run ruff check` / `uv run ruff format` 代码质量
- `uv run mypy src/` 类型检查

**Verified Dependency Versions (2026-03-24):**

| 包 | 最新版本 | 说明 |
|---|---------|------|
| python-statemachine | 3.0.0 (2026-02-24) | 全新发布：statechart + async + 持久化模型 |
| Pydantic | 2.12.5 | 稳定成熟 |
| aiosqlite | 0.22.1 | 稳定成熟 |
| Textual | ≥2.0 | 活跃维护 |
| typer | 最新稳定版 | 活跃维护 |

**Note:** 项目初始化（`uv init` + 依赖安装 + 目录结构创建 + pre-commit 配置）应作为第一个实现 story。

## Core Architectural Decisions

### Decision Priority Analysis

**Critical Decisions (Block Implementation):**
1. 进程生命周期模型（Orchestrator/TUI 关系）
2. TUI↔Orchestrator 通信模型（直写 SQLite + nudge）
3. 配置表达力边界（可配置 vs 硬编码）
4. Interactive Session 完成检测机制

**Important Decisions (Shape Architecture):**
5. SQLite Schema 迁移策略
6. 结构化日志框架
7. 正常重启 vs 崩溃恢复路径分离

**Testing Architecture Decisions:**
8. 状态机测试覆盖定义
9. CLI Adapter 契约守护策略

### Decision 1: 进程生命周期模型

**决策：Orchestrator 和 TUI 始终为独立进程**

| 命令 | 行为 |
|------|------|
| `ato start` | 启动 Orchestrator 后台进程（headless），写 PID 到 `.ato/orchestrator.pid` |
| `ato tui` | 启动 TUI 前台进程，连接已运行 Orchestrator 的 SQLite |
| `ato start --tui` | 便捷模式，同时启动两者 |
| `ato stop` | 优雅停止 Orchestrator（通过 PID 文件） |

**Rationale：**
- TUI 崩溃不影响编排运行
- Orchestrator 可 headless 运行（脚本自动化场景）
- 进程隔离简化故障排查
- Affects: CLI 命令设计、core.py 入口逻辑、TUI 启动逻辑

### Decision 2: TUI↔Orchestrator 通信模型

**决策：TUI 直接写 SQLite + 轻量 nudge 通知**

- **写入路径：** TUI 直接写 SQLite（审批决策、UAT 结果、`ato submit` 状态更新）
- **通知机制：** 写入后通过 `os.pipe()` 或 `SIGUSR1` 通知 Orchestrator 立即轮询
- **兜底：** Orchestrator 同时保持 2-5 秒间隔的定期轮询（nudge 丢失时的安全网）
- **统一路径：** 所有外部写入（TUI 审批、`ato submit` CLI）走同一个 nudge 机制
- **Approval 等待语义：** Orchestrator 在 approval 等待期间非阻塞——仅等待审批的 story 暂停在当前阶段，其他 stories 的生命周期继续正常推进。Orchestrator 事件循环每次轮询检查 approvals 表状态变更。

**Rationale：**
- SQLite WAL 天然支持并发读写，TUI 写入极低频
- `busy_timeout=5000` 覆盖极端情况
- nudge 解决审批响应延迟问题（紧急操作如 regression 失败的 revert 决策无需等 5 秒）
- 不引入完整 IPC 框架，保持架构简单
- Affects: core.py 事件循环、TUI 写入层、cli.py submit 命令

### 用户可见通知子系统

**MVP 范围：Terminal Bell**

- 通过 `\a` 转义序列发送 terminal bell（`print('\a', end='', flush=True)`）
- 由 `nudge.py` 统一管理，扩展现有 nudge 机制

**Growth 范围：macOS 系统通知**

- 通过 `osascript -e 'display notification ...'` 或 `pync` 库发送 macOS 通知
- 仅在 `platform.system() == 'Darwin'` 时可用

**NotificationLevel 枚举与触发规则：**

| Level | 触发条件 | MVP 行为 | Growth 行为 |
|-------|---------|---------|------------|
| `URGENT` | regression 失败、级联异常 | terminal bell + TUI 顶栏闪烁 | + macOS 通知 |
| `NORMAL` | 审批等待、超时 | terminal bell | + macOS 通知（可选） |
| `SILENT` | story 阶段推进 | 无外部通知，TUI 列表自动更新 | 同 MVP |
| `MILESTONE` | story 完成、batch 交付 | terminal bell（一声） | + macOS 通知 |

**实现位置：** `nudge.py`（扩展为同时负责内部进程 nudge 和用户可见通知）

### Decision 3: 配置表达力边界

**决策：配置决定"做什么"，引擎决定"怎么做"**

| 可配置（ato.yaml） | 硬编码（引擎内） |
|-------------------|----------------|
| 角色定义（CLI 类型、模型、sandbox） | Convergent Loop 内部协议（scope 收窄、finding 状态追踪） |
| 阶段序列和转换规则 | 崩溃恢复流程（PID/artifact 检查逻辑） |
| 超时阈值、并发上限、成本上限 | 错误分类与恢复矩阵 |
| Convergent Loop max_rounds、convergence_threshold | TransitionQueue 串行化保证 |
| 模型选择映射（阶段→模型） | TUI↔Orchestrator 通信模式 |
| 审批类型定义 | 状态机 transition 执行引擎 |

**Rationale：**
- 过度配置化会导致配置引擎成为系统中最复杂的部分
- 核心协议（Convergent Loop、崩溃恢复）需要确定性行为，不适合用户修改
- 角色/阶段/阈值天然因项目而异，必须可配置
- Affects: 配置解析模块、状态机构建逻辑、所有引擎模块的参数化方式

### Decision 4: Interactive Session 完成检测

**决策：双通道 — CLI 命令 + TUI 手动标记**

- **主路径：** `ato submit <story-id>` CLI 命令
  - 验证 story 存在且处于 `developing` 状态
  - 验证 worktree 有 commit（agent 确实做了工作）
  - 更新 SQLite story 状态
  - 通过 nudge 通知 Orchestrator
- **备选：** TUI 中手动标记完成（同一 nudge 路径）
- 两者最终都触发 TransitionQueue 的 `dev_done` 事件

**Rationale：**
- CLI 命令适合在终端中直接操作的开发场景
- TUI 标记适合同时监控多项目的运营场景
- 统一底层机制（SQLite 写 + nudge），双入口不增加复杂度
- Affects: cli.py submit 命令、TUI story 详情视图、TransitionQueue 事件定义

### Context Briefing Schema

跨 task 边界的结构化工作记忆摘要（FR53），作为 fresh session 的输入上下文：

```python
class ContextBriefing(BaseModel):
    story_id: str                    # 关联 story
    phase: str                       # 产生 briefing 的阶段
    task_type: str                   # 任务类型（creating/developing/reviewing/fixing）
    artifacts_produced: list[str]    # 产出的文件路径列表
    key_decisions: list[str]         # agent 做出的关键决策摘要
    agent_notes: str                 # agent 自由格式的工作笔记
    created_at: datetime             # 创建时间戳
```

- **提取时机：** 每个 agent task 完成后，由 SubprocessManager 从 agent 输出中提取
- **提取方式：** Claude CLI 的 JSON 输出中包含结构化字段；Codex CLI 需从 JSONL 事件流中聚合
- **消费方式：** 下一个 task 的 prompt 中注入 briefing 作为上下文前缀
- **存储：** 序列化为 JSON 存入 tasks 表的 `context_briefing` 列

### Decision 5: SQLite Schema 迁移策略

**决策：PRAGMA user_version + 启动时自动迁移**

- `PRAGMA user_version` 追踪当前 schema 版本号
- `ato start` 时检查版本号，按序执行迁移函数
- **迁移函数放在 `models/migrations.py`**（非 db.py），分离关注点
- db.py 只负责连接管理和当前 schema 定义
- `init_db()` 调用 `run_migrations(current_version, target_version)`

**Rationale：**
- 本地单用户应用不需要 Alembic 重量级方案
- PRAGMA user_version 是 SQLite 原生机制，零额外依赖
- 迁移函数独立文件避免 db.py 膨胀
- Affects: models/migrations.py（新增）、models/db.py、core.py 启动流程

### Decision 6: 结构化日志

**决策：structlog（核心依赖）**

- **依赖类型：** 核心依赖（非 dev），生产运行时必需
- **输出目标：** `.ato/logs/` 文件 + stderr
- **日志目录：** `ato init` 时创建
- **结构化字段：**
  - CLI 调用：story_id、phase、cli_tool、model、duration_ms、cost_usd、exit_code
  - Convergent Loop：round_num、findings_total、open_count、closed_count、new_count
  - 崩溃恢复：task_id、recovery_action（reattach/complete/reschedule/needs_human）
- **查看命令：** `ato logs <story-id>` 过滤查看特定 story 的日志

**Rationale：**
- 调试 Convergent Loop 不收敛需要每轮 findings diff 和 CLI 原始输出
- structlog 的 processor 链天然支持结构化字段绑定
- TUI 不消费日志（用 SQLite），日志面向开发者调试
- Affects: 所有模块（全局 logger 配置）、cli.py logs 命令（新增）、ato init 目录创建

### Decision 7: 正常重启 vs 崩溃恢复路径分离

**决策：优雅停止标记法**

| 场景 | task 状态 | `ato start` 行为 |
|------|----------|-----------------|
| `ato stop` 后重启 | `paused`（优雅标记） | 正常恢复：直接重调度 paused tasks |
| 意外崩溃后重启 | `running`（未来得及标记） | 崩溃恢复：PID/artifact 检查 → 分类处理 |
| SIGKILL 后重启 | `running`（同崩溃） | 同崩溃恢复路径 |

- `ato stop` → 将所有 `status=running` 的 task 标记为 `paused`，记录停止时间戳
- `ato start` → `status=running` = 崩溃恢复；`status=paused` = 正常恢复
- **启动日志明确输出恢复模式：** "检测到 N 个 running task，进入崩溃恢复模式" 或 "检测到 N 个 paused task，正常恢复"

**Rationale：**
- 简单、确定性强、无需额外文件或锁机制
- SIGKILL 场景自然归入崩溃恢复路径，无歧义
- 用户通过日志输出明确知道系统处于什么恢复模式
- Affects: core.py 启动/停止逻辑、recovery.py、structlog 输出

### Decision 8: 状态机测试覆盖

**决策：Transition 100% + 4 条关键路径**

**单元测试：**
- 每个 transition 至少执行一次（~20 个测试）

**集成测试（4 条关键路径）：**
1. **Happy path：** queued → ... → done
2. **Review-fix Convergent Loop：** reviewing → fixing → reviewing → review_passed
3. **崩溃恢复：** 构造"崩溃前的数据库状态"（插入 status=running task，PID 不存在）→ 调用 recovery → 验证分类行为（纯函数式测试，不需要杀进程）
4. **非法 transition 拒绝：** 从每个状态尝试不合法 transition → 验证异常捕获 → 状态不变 → structlog 记录

**Rationale：**
- Transition 100% 确保每条边至少被执行
- 4 条路径覆盖最高风险场景
- 崩溃恢复用函数式测试（构造数据库状态）而非真实杀进程，简单可靠
- 负向测试确保状态机异常不导致 Orchestrator 崩溃
- Affects: tests/unit/test_state_machine.py、tests/integration/

### Decision 9: CLI Adapter 契约守护

**决策：Snapshot fixture (CI) + 定期冒烟测试 (手动) + 版本追踪**

- **Snapshot fixture：** 保存 Claude/Codex 真实输出样本为 JSON fixture（`tests/fixtures/`），adapter 解析测试基于 fixture
- **冒烟测试：** 真实 CLI 最小调用（`--max-turns 1`、`--max-budget-usd 0.10`），验证输出格式未变，更新 fixture
- **版本追踪：** `pyproject.toml` 中 `[tool.ato]` 记录已验证的 CLI 版本号，冒烟测试比对当前版本与记录版本，不一致时高亮警告并强制更新 fixture
- **CLI 版本升级前必须先跑冒烟测试更新 fixture**

**Rationale：**
- Snapshot 测试在 CI 中快速运行，不消耗 API 成本
- 冒烟测试是 CLI 升级时的安全网
- 版本追踪让 fixture 过时变得可检测，而非静默失效
- Affects: tests/fixtures/（新增）、adapters/claude_cli.py、adapters/codex_cli.py、pyproject.toml

### Decision 10: Preflight Check 协议

**决策：分层前置检查 — `ato init <目标项目路径>` 验证运行条件**

用户必须指定一个目标项目目录，系统执行三层检查后才允许编排：

**第一层：系统环境**

| 检查项 | 检测方式 | 失败行为 |
|--------|---------|---------|
| Python ≥3.11 | `sys.version_info` | HALT + 升级提示 |
| Claude CLI 已安装 | `claude --version` | HALT + 安装指引 |
| Claude CLI 认证有效 | `claude -p "ping" --max-turns 1 --output-format json` | HALT + 提示 `claude auth` |
| Codex CLI 已安装 | `codex --version` | HALT + 安装指引 |
| Codex CLI 认证有效 | `codex exec "ping" --json` | HALT + 提示认证 |
| Git 已安装 | `git --version` | HALT + 安装指引 |

**第二层：目标项目结构**

| 检查项 | 检测方式 | 失败行为 |
|--------|---------|---------|
| 目标路径是 git repo | `git -C <path> rev-parse` | HALT |
| BMAD 配置 | `_bmad/bmm/config.yaml` 存在且有效 | HALT |
| config.yaml 必填字段 | Pydantic 验证 `project_name`, `planning_artifacts`, `implementation_artifacts` | HALT + 报告缺失 |
| BMAD skills (.claude/skills/) | 目录存在 | WARN（非阻塞） |
| ato.yaml | 项目根目录 | HALT + 从 example 复制引导 |

**第三层：编排前置 Artifact（基于 BMAD skill 实际需求）**

| 检查项 | 模式匹配 | 必要性 | 消费者 |
|--------|---------|--------|--------|
| Epic 文件 | `{planning_artifacts}/*epic*.md` 或 `*epic*/index.md` | **必须** | sprint-planning, create-story |
| PRD | `{planning_artifacts}/*prd*.md` | 推荐 | create-story（需求上下文） |
| 架构文档 | `{planning_artifacts}/*architecture*.md` | 推荐 | create-story（技术约束、命名规范） |
| UX 设计 | `{planning_artifacts}/*ux*.md` | 可选 | create-story（UI 指导） |
| 项目上下文 | `**/project-context.md` | 可选 | dev-story, create-story（编码标准） |
| impl 目录可写 | `{implementation_artifacts}/` | **必须** | sprint-planning（创建 sprint-status.yaml） |

**检查时机：**
- `ato init`：执行全部三层（含 CLI 认证测试调用）
- `ato start`：快速检查（跳过 CLI 认证调用，仅验证文件/配置）

**实现：** `preflight.py` — 每个检查项是独立 async 函数，返回 `CheckResult(status, message)`。结果持久化到 SQLite 供 TUI 展示。

**Rationale：**
- Epic 文件是 sprint-planning 的硬依赖（BMAD skill 无 epic 直接 HALT）
- Architecture 文档是 create-story 的 Dev Notes 来源（缺失会导致 story 质量下降）
- CLI 认证检查避免编排启动后才发现认证过期
- 分层检查让用户快速定位问题层级
- Affects: preflight.py（新增）、cli.py init 命令、models/db.py（preflight_results 表）

### Decision Impact Analysis

**实现顺序：**
1. 项目初始化（uv init + structlog 基础配置 + 目录结构）
2. SQLite schema + PRAGMA user_version 迁移机制 + models/migrations.py
3. 状态机 + TransitionQueue + nudge 机制
4. CLI Adapter + snapshot fixture
5. Convergent Loop
6. Approval Queue + TUI 直写 + nudge 集成
7. Interactive Session + `ato submit`
8. 崩溃恢复（recovery.py + 函数式测试）
9. TUI 仪表盘

**跨组件依赖：**

```
structlog ──────────────────────► 所有模块
                                    │
SQLite schema ──► migrations.py     │
      │                             │
      ▼                             ▼
TransitionQueue ◄── nudge 机制 ◄── TUI 直写 / ato submit
      │                             │
      ▼                             │
状态机 ◄──── CLI Adapter ◄──── snapshot fixture
      │           │
      ▼           ▼
Convergent Loop   崩溃恢复
      │
      ▼
Approval Queue
```

**关键约束：**
- structlog 必须最早配置（所有模块依赖）
- nudge 机制是 TUI 直写和 `ato submit` 的共同基础设施
- snapshot fixture 与 adapter 同步开发
- 崩溃恢复的函数式测试只依赖 SQLite schema，可独立开发

## Implementation Patterns & Consistency Rules

### Naming Patterns

| 范围 | 规则 | 示例 |
|------|------|------|
| SQLite 表名 | snake_case 复数 | `stories`, `findings`, `approvals`, `cost_log` |
| SQLite 列名 | snake_case | `story_id`, `created_at`, `cost_usd` |
| Python 模块/函数/变量 | PEP 8 snake_case（ruff 强制） | `transition_queue.py`, `def submit_transition()` |
| Python 类名 | PascalCase | `StoryLifecycle`, `TransitionQueue`, `SubprocessManager` |
| Pydantic 模型 | PascalCase + 用途后缀 | `FindingRecord`, `ApprovalRequest`, `ClaudeOutput` |
| 配置键 (ato.yaml) | snake_case | `max_concurrent_agents`, `convergent_loop.max_rounds` |
| structlog 字段 | snake_case | `story_id`, `round_num`, `cost_usd`, `exit_code` |
| CLI 命令 | kebab-case（typer 默认） | `ato batch-select`, `ato submit` |
| JSON Schema 属性 | snake_case | `"severity"`, `"finding_id"`, `"is_blocking"` |
| 自定义异常 | PascalCase + Error 后缀 | `CLIAdapterError`, `StateTransitionError` |

### Structure Patterns

| 范围 | 规则 |
|------|------|
| 测试文件 | `tests/unit/test_<module>.py`, `tests/integration/test_<feature>.py` |
| Fixture 文件 | `tests/fixtures/<cli>_<scenario>.json` |
| 公共接口 | 通过 `__init__.py` 显式导出，不导出内部函数 |
| 类型定义 | Pydantic models 统一在 `models/schemas.py` |
| 常量 | 模块级 `UPPER_SNAKE_CASE`，跨模块常量在 `models/schemas.py` |
| 配置访问 | 通过 Pydantic `Settings` 对象传递，不直接读 YAML |
| 迁移函数 | `models/migrations.py`（非 db.py） |

### Asyncio Subprocess 模式 — 三阶段清理协议

所有 adapter 层的 subprocess 调用必须遵循：

```python
async def _cleanup_process(proc, timeout=5):
    """三阶段清理：SIGTERM → wait → SIGKILL → wait"""
    if proc.returncode is not None:
        return
    proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout)
        return
    except asyncio.TimeoutError:
        pass
    proc.kill()
    await proc.wait()  # kill 后必须 wait，防止 zombie
```

- 所有 subprocess 调用在 `try/finally` 中，`finally` 调用 `_cleanup_process()`
- 不使用 `proc.send_signal()`，统一 `terminate()` / `kill()`
- subprocess_mgr 的 `running` 字典追踪活跃进程，崩溃恢复时批量检查

### SQLite 连接策略

| 场景 | 连接模式 | 理由 |
|------|---------|------|
| TransitionQueue consumer | 长连接（consumer 生命周期复用） | 串行写入，无并发冲突 |
| Orchestrator 轮询读取 | 短连接 | 读不阻塞写，确保最新 WAL 数据 |
| TUI 读取/写入 | 短连接 + 立即 commit | 独立进程，最小化写锁持有 |

**关键规则：**
- `PRAGMA busy_timeout=5000` 在每个连接上设置
- `PRAGMA journal_mode=WAL` 在 `init_db()` 和每次连接时检查
- `PRAGMA synchronous=NORMAL`（WAL 模式下安全且更快）
- 禁止在 `async with aiosqlite.connect()` 块内 await 外部 IO
- 写事务尽可能短——读数据、处理逻辑、然后单次写入 + commit
- 参数化查询，禁止手动拼接 SQL

### structlog 配置模式

```python
import structlog, logging

def configure_logging(log_dir: str, debug: bool = False):
    """ATO 标准配置 — ato start 时调用一次"""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,      # 协程级上下文
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),           # 统一 JSON 输出
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.DEBUG if debug else logging.INFO
        ),
        cache_logger_on_first_use=True,
    )
```

- **协程级上下文绑定：** 在每个 Task 入口处（`subprocess_mgr.dispatch` 内部）调用 `structlog.contextvars.bind_contextvars(story_id=..., phase=...)`
- structlog 做格式化，标准库 logging 做输出路由（文件 + stderr），通过 `structlog.stdlib.ProcessorFormatter` 桥接
- **MVP：** 单文件 `.ato/logs/ato.log` append 模式（不做轮转）
- **Growth：** 配合 `TimedRotatingFileHandler` 按日轮转

### python-statemachine 3.0 Async 集成模式

- async 模式下 `__init__` 不能 await → **创建后必须 `await sm.activate_initial_state()`**
- **PersistentModel setter 不直接写 SQLite** — 只更新内存状态
- **TransitionQueue consumer 在 `send()` 返回后显式持久化**（保持单写者模式）：

```python
# TransitionQueue consumer 内
await sm.send(event)                                      # 内存状态更新
await save_story_state(db, story_id, sm.current_state)    # 显式持久化
await db.commit()
```

- 单线程约束：所有状态机操作在同一事件循环线程，不跨线程共享 sm 实例
- 优先用 `StateMachine`（2.x 兼容默认值），除非需要 compound/parallel states

### Pydantic v2 验证模式

| 层 | 验证模式 | 说明 |
|---|---------|------|
| 外部输入（CLI 输出） | `model_validate(data)` | 严格验证外部边界 |
| 配置加载 | `model_validate(yaml_data)` + 自定义 validator | 宽松 + 领域规则 |
| 内部传递 | `model_validate(data)` | **MVP 全部走 validate（安全优先）** |

- **MVP 阶段不使用 `model_construct`**（Growth 阶段再按性能需求切换热路径）
- Field constraints 表达领域规则：`severity: Literal["blocking", "suggestion"]`
- 用 `model_json_schema()` 自动生成 `schemas/` 目录下的 JSON Schema 文件
- 禁止在 Pydantic validator 中做 IO 操作

### Textual TUI 架构模式

**MVP Screen（3 个）：**

| Screen | 职责 | 数据源 |
|--------|------|--------|
| `DashboardScreen` | story 列表 + 状态 + 审批快捷操作 | `stories` + `approvals` 表 |
| `ApprovalScreen` | 审批详情 + 决策交互 | `approvals` 表读写 |
| `StoryDetailScreen` | story 详情钻入 + findings/变更/成本/历史 | stories + findings + cost_log 表 |

- `compose()` 定义结构，`on_mount()` 初始化数据（不在 `__init__` 中读 SQLite）
- reactive 属性驱动 UI 更新
- CSS 文件与 Python 分离：`tui/app.tcss`
- `set_interval(2.0, self.refresh_data)` 定期轮询
- **TUI 测试：** Textual `pilot` + mock SQLite 数据

### Typer CLI 模式

| 退出码 | 含义 | 使用场景 |
|--------|------|---------|
| 0 | 成功 | 正常完成 |
| 1 | 一般错误 | 配置无效、story 不存在 |
| 2 | 环境错误 | CLI 未安装、认证过期 |

- 用 `typer.Exit(code=N)`，不用 `sys.exit()`
- 错误信息输出到 stderr：`typer.echo(msg, err=True)`
- `ato status --json` 结构化 JSON 输出到 stdout
- `ato init` 失败时输出明确的下一步指引
- Ctrl+C (130) 由 shell 自动处理，不手动捕获
- 测试用 `typer.testing.CliRunner` 验证退出码和输出

### 模块间错误传播规则

| 层 | 错误处理职责 |
|---|------------|
| adapter | 将 CLI 原始错误分类为 `ErrorCategory`，包装为 `CLIAdapterError` |
| subprocess_mgr | 捕获 `CLIAdapterError`，执行重试（1 次），仍失败则通过 TransitionQueue 触发 `escalate` |
| core (TransitionQueue consumer) | 接收 escalate → 创建 approval → 不 crash |
| TUI | 展示 approval，不直接处理错误 |

**原则：错误向上传播直到遇到能处理它的层，不在中间层静默吞掉。**

异常层次：`ATOError` → `CLIAdapterError`, `StateTransitionError`, `RecoveryError`, `ConfigError`

### Enforcement — 强制规则

**所有 AI Agent 必须：**
- `ruff check` + `ruff format` + `mypy` 全部通过后再提交
- 所有公共函数有类型标注（参数和返回值）
- 所有新模块有对应的单元测试文件
- subprocess 调用在 `try/finally` 中使用三阶段清理协议
- CLI adapter 返回值必须经过 Pydantic `model_validate`
- 状态机操作用 `structlog.contextvars` 在 Task 入口绑定上下文
- 异步状态机创建后 `await sm.activate_initial_state()`
- SQLite 写事务中不 await 外部 IO

### Enforcement — 反模式清单

- ❌ 不要用 `asyncio.gather`（用 `TaskGroup`）
- ❌ 不要用 `shell=True` 启动子进程
- ❌ 不要在 adapter 外直接拼接 CLI 命令
- ❌ 不要在非 `models/` 目录定义 Pydantic model
- ❌ 不要用 `print()` 输出日志（用 structlog）
- ❌ 不要在 `except` 中静默吞掉异常（至少 `structlog.warning`）
- ❌ 不要在测试中直接调用真实 CLI（用 fixture，冒烟测试除外）
- ❌ 不要在 SQLite 写事务中 await CLI 调用
- ❌ 不要跨线程共享状态机实例
- ❌ 不要手动拼接 SQL（用参数化查询）
- ❌ 不要在 Textual `__init__` 中读 SQLite（用 `on_mount`）
- ❌ 不要在 Pydantic validator 中做 IO 操作
- ❌ MVP 不要使用 `model_construct`（Growth 再评估）
- ❌ 不要在 PersistentModel setter 中直接写 SQLite（consumer 显式持久化）

## Project Structure & Boundaries

### Complete Project Directory Structure

```
agent-team-orchestrator/
├── pyproject.toml                    # 项目配置 + [tool.ato] CLI 版本追踪
├── uv.lock                           # 依赖锁定（提交到 VCS）
├── .python-version                   # Python ≥3.11
├── .pre-commit-config.yaml           # ruff + mypy hooks
├── .gitignore
├── ato.yaml.example                  # 配置模板（用户复制为 ato.yaml）
│
├── src/
│   └── ato/
│       ├── __init__.py               # 版本号 + 公共导出
│       ├── cli.py                    # typer CLI 入口
│       ├── core.py                   # Orchestrator 主事件循环
│       ├── state_machine.py          # StoryLifecycle 状态机
│       ├── transition_queue.py       # TransitionQueue
│       ├── subprocess_mgr.py         # SubprocessManager
│       ├── convergent_loop.py        # Convergent Loop 协议
│       ├── recovery.py               # 崩溃恢复
│       ├── config.py                 # Pydantic Settings：ato.yaml 解析
│       ├── nudge.py                  # nudge 通知机制
│       ├── preflight.py              # 运行前置检查（环境检测 + 就绪验证）
│       ├── logging.py                # structlog 配置
│       │
│       ├── adapters/
│       │   ├── __init__.py
│       │   ├── base.py               # AdapterResult + _cleanup_process + 公共接口
│       │   ├── claude_cli.py         # Claude CLI 封装
│       │   ├── codex_cli.py          # Codex CLI 封装
│       │   └── bmad_adapter.py       # BMAD Markdown → JSON
│       │
│       ├── models/
│       │   ├── __init__.py
│       │   ├── schemas.py            # 所有 Pydantic models
│       │   ├── db.py                 # SQLite 连接管理 + schema DDL
│       │   └── migrations.py         # PRAGMA user_version 迁移
│       │
│       └── tui/
│           ├── __init__.py
│           ├── app.py                # Textual App 入口
│           ├── app.tcss              # Textual CSS 样式
│           ├── dashboard.py          # DashboardScreen
│           ├── approval.py           # ApprovalScreen
│           ├── story_detail.py          # StoryDetailScreen
│           └── widgets/
│               ├── __init__.py
│               ├── three_question_header.py  # 三问首屏 Widget
│               ├── approval_card.py          # 审批卡片 Widget
│               ├── heartbeat_indicator.py    # 心跳指示器 Widget
│               ├── convergent_loop_progress.py  # CL 进度 Widget
│               └── story_status_line.py      # Story 状态行 Widget
│
├── schemas/                          # JSON Schema 文件
│   ├── review-findings.json
│   ├── story-validation.json
│   └── finding-verification.json
│
├── tests/
│   ├── conftest.py
│   ├── fixtures/                     # CLI 输出 snapshot
│   │   ├── claude_review_pass.json
│   │   ├── claude_review_fail.json
│   │   ├── claude_structured_output.json
│   │   ├── codex_review_jsonl.txt
│   │   └── codex_exec_output.json
│   ├── unit/
│   │   ├── test_state_machine.py
│   │   ├── test_transition_queue.py
│   │   ├── test_convergent_loop.py
│   │   ├── test_config.py
│   │   ├── test_claude_adapter.py
│   │   ├── test_codex_adapter.py
│   │   ├── test_bmad_adapter.py
│   │   ├── test_cleanup_process.py
│   │   ├── test_nudge.py
│   │   ├── test_migrations.py
│   │   └── test_preflight.py
│   ├── integration/
│   │   ├── test_happy_path.py
│   │   ├── test_convergent_loop_e2e.py
│   │   ├── test_crash_recovery.py
│   │   └── test_tui_pilot.py
│   └── smoke/
│       └── test_cli_contract.py
│
└── .ato/                             # 运行时目录（.gitignore）
    ├── state.db
    ├── state.db-wal
    ├── state.db-shm
    ├── orchestrator.pid
    └── logs/
        └── ato.log
```

### Architectural Boundaries

**进程边界：**

| 进程 | 入口 | 读写权限 |
|------|------|---------|
| Orchestrator | `ato start` → `core.py` | SQLite 读写（主写者）、nudge 接收 |
| TUI | `ato tui` → `tui/app.py` | SQLite 读 + 审批写、nudge 发送 |
| CLI 命令 | `ato submit/status/...` → `cli.py` | SQLite 读 + submit 写、nudge 发送 |
| CLI subprocess | `claude -p` / `codex exec` | 仅 worktree 内文件 |

**模块依赖方向（只允许向下依赖）：**

```
cli.py ──────────► models/schemas.py ◄──── config.py
    │                    ▲                      │
    ▼                    │                      ▼
tui/ ───► models/db.py ◄┤              core.py ◄── nudge.py
                         │                │
                         │    ┌───────────┤
                         │    ▼           ▼
                    transition_queue  subprocess_mgr
                         │                │
                         │    ┌───────────┤
                         │    ▼           ▼
                    state_machine   adapters/
                         │                │
                         │                ▼
                    convergent_loop  adapters/base.py
                         │
                         ▼
                    recovery.py
```

**禁止的依赖方向：**
- adapters/ 不依赖 core.py 或 transition_queue
- tui/ 不依赖 core.py（通过 SQLite 解耦）
- models/ 不依赖任何上层模块

### FR 到结构的映射

| FR 领域 | 主要文件 | 辅助文件 |
|---------|---------|---------|
| 工作流编排 (FR1-5) | `config.py`, `state_machine.py`, `transition_queue.py` | `models/schemas.py` |
| AI Agent 协作 (FR6-12, 53) | `adapters/*.py`, `subprocess_mgr.py` | `adapters/base.py` |
| 质量门控 (FR13-18) | `convergent_loop.py` | `adapters/bmad_adapter.py` |
| 人机协作 (FR19-23) | `tui/approval.py`, `cli.py`, `nudge.py` | `models/db.py` |
| 状态管理与恢复 (FR24-28) | `models/db.py`, `recovery.py`, `logging.py` | `models/migrations.py` |
| 工作空间管理 (FR29-32, 52) | `subprocess_mgr.py` | — |
| 配置与初始化 (FR33-35) | `cli.py`, `config.py`, `preflight.py` | `models/db.py` |
| 可视化与监控 (FR36-40) | `tui/dashboard.py`, `tui/app.py` | `tui/app.tcss` |

### 数据流

```
ato.yaml ──► config.py ──► state_machine 构建
                              │
用户指令 ──► cli.py ──► SQLite ──► core.py 轮询/nudge
                                    │
                              TransitionQueue
                                    │
                              subprocess_mgr
                               ╱          ╲
                     claude -p              codex exec
                        │                      │
                     JSON stdout          JSONL stdout + -o file
                        │                      │
                     adapter 解析 ──► Pydantic model_validate
                                          │
                                    TransitionQueue ──► SQLite 持久化
                                                           │
                                                    TUI 轮询展示
```

## Architecture Validation Results

### Coherence Validation ✅

**Decision Compatibility:** 10 项决策之间无矛盾。SQLite WAL + TransitionQueue 单写者 + PersistentModel 内存更新 + consumer 显式持久化形成一致的写入模型。进程生命周期 + nudge + TUI 直写构成完整的进程间通信链。Preflight Check 覆盖所有 BMAD skill 前置依赖。

**Pattern Consistency:** 命名统一（snake_case data/config/log，PascalCase classes/models）。错误传播链路清晰（adapter → subprocess_mgr → core → TUI）。外部边界 Pydantic validate，内部 MVP 同样 validate。

**Structure Alignment:** 模块依赖严格向下无循环。adapters/ 不依赖 core，tui/ 不依赖 core——通过 SQLite 解耦。

### Requirements Coverage ✅

**Functional Requirements:** 53/53 MVP FRs 有架构支撑。FR41-47（Growth）已标记延迟，架构已预留扩展点。

**Non-Functional Requirements:** 14/14 NFRs 已覆盖。关键 NFR 对照：
- NFR1 崩溃恢复 ≤30s → Decision 7 + recovery.py + SQLite WAL
- NFR6 零数据丢失 → WAL + 连接策略 + 短写事务
- NFR9 Convergent Loop 终止 → max_rounds 硬编码 + 梯度降级
- NFR11 CLI adapter 隔离 → adapters/base.py + snapshot 契约守护

### Implementation Readiness ✅

**Decision Completeness:** 10 项决策全部含版本号、Rationale、Affects。技术选型经 web 验证。
**Pattern Completeness:** 7 个技术领域有模式定义 + 代码示例。强制规则 9 条 + 反模式 14 条。
**Structure Completeness:** 完整目录树含文件注释。FR→文件映射完整。依赖方向图 + 禁止方向明确。

### Gap Analysis

**Critical Gaps:** 无

**Important Gaps（非阻塞，在实现 story 中解决）：**
1. SQLite 完整 DDL 未在架构文档中定义 — 在第一个 story 中与 migrations.py 一起定义
2. ato.yaml 完整 schema — 由 config.py 的 Pydantic Settings 定义即为 schema

**Deferred to Growth：**
- Memory 层详细架构
- 多项目并行的状态隔离方案
- 梯度降级完整实现
- TUI StoryDetailScreen（已提前到 MVP）

### Architecture Completeness Checklist

**✅ Requirements Analysis**
- [x] 53 FRs + 14 NFRs 全面分析
- [x] 项目复杂度评估（中-高）
- [x] 技术约束识别（无 API Key、Codex 能力差异）
- [x] 9 个跨切面关注点映射

**✅ Architectural Decisions**
- [x] 10 项决策文档化（含版本、Rationale、Affects）
- [x] 技术栈完整指定（Python 3.11+ / uv / aiosqlite / python-statemachine 3.0 / Textual / Pydantic / typer / structlog）
- [x] 进程生命周期模型
- [x] TUI 通信模型（直写 + nudge）
- [x] 配置表达力边界
- [x] Preflight Check 协议

**✅ Implementation Patterns**
- [x] 命名规范（10 个范围）
- [x] 结构模式（7 个规则）
- [x] 7 个技术领域深度模式（asyncio subprocess / SQLite / structlog / statemachine / Pydantic / Textual / typer）
- [x] 模块间错误传播规则
- [x] 强制规则 9 条 + 反模式 14 条

**✅ Project Structure**
- [x] 完整目录树（含所有文件注释）
- [x] 进程边界 + 模块依赖方向
- [x] FR→文件映射
- [x] 跨切面→文件映射
- [x] 数据流图

### Architecture Readiness Assessment

**Overall Status:** READY FOR IMPLEMENTATION

**Confidence Level:** HIGH — 基于 3 轮 Party Mode 团队审视 + 最佳实践 web 调研 + BMAD skill 前置依赖分析

**Key Strengths:**
1. "编排者是代码"设计消除了"监控 LLM 是否遵守规则"整类问题
2. 双 CLI adapter 抽象层隔离了 Claude/Codex 的差异，CLI 升级不影响核心
3. TransitionQueue 单 consumer 串行化保证了状态一致性，无需分布式锁
4. 三轮 Party Mode 审视覆盖了架构一致性、实现陷阱、测试完整性和 MVP 精简
5. Preflight Check 基于 BMAD skill 实际依赖而非假设

**Areas for Future Enhancement:**
1. Growth: Memory 层（系统智能 + 自我改进闭环）
2. Growth: 多项目并行 + 资源分配
3. Growth: TUI 成本面板 + UAT 趋势（StoryDetailScreen 已提前到 MVP）
4. Growth: model_construct 性能优化（热路径）
5. Growth: 日志轮转（TimedRotatingFileHandler）

### Implementation Handoff

**AI Agent Guidelines:**
- 严格遵循本文档中的所有架构决策
- 使用实现模式保持跨组件一致性
- 尊重模块依赖方向（只允许向下依赖）
- 提交前通过 ruff + mypy 全部检查
- 架构问题参照本文档，而非自行决定

**First Implementation Priority:**
1. `uv init` + 依赖安装 + 目录结构 + structlog 基础配置 + pre-commit
2. SQLite schema DDL + PRAGMA user_version 迁移 + models/migrations.py
3. StoryLifecycle 状态机 + TransitionQueue + 单元测试（transition 100%）
