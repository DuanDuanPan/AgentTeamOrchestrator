# ATO 全流程 UAT 测试计划

> **版本**: 1.0 | **日期**: 2026-03-27 | **范围**: Phase 1 (Epic 1–6)
> **测试环境**: macOS, Python ≥3.11, Claude CLI (OAuth), Codex CLI, Git

---

## 目录

1. [测试策略概述](#1-测试策略概述)
2. [前置条件与环境准备](#2-前置条件与环境准备)
3. [UAT-1: 项目初始化流程](#uat-1-项目初始化流程)
4. [UAT-2: Batch 选择与计划预览](#uat-2-batch-选择与计划预览)
5. [UAT-3: 编排引擎启停与生命周期](#uat-3-编排引擎启停与生命周期)
6. [UAT-4: Story 全生命周期 Happy Path](#uat-4-story-全生命周期-happy-path)
7. [UAT-5: Convergent Loop 质量门控](#uat-5-convergent-loop-质量门控)
8. [UAT-6: 审批队列与人机协作](#uat-6-审批队列与人机协作)
9. [UAT-7: Merge Queue 与回归测试](#uat-7-merge-queue-与回归测试)
10. [UAT-8: 崩溃恢复与数据完整性](#uat-8-崩溃恢复与数据完整性)
11. [UAT-9: Interactive Session 交互式会话](#uat-9-interactive-session-交互式会话)
12. [UAT-10: TUI 指挥台全功能](#uat-10-tui-指挥台全功能)
13. [UAT-11: CLI 命令完整性](#uat-11-cli-命令完整性)
14. [UAT-12: 性能与非功能验收](#uat-12-性能与非功能验收)
15. [UAT-13: 异常与边界场景](#uat-13-异常与边界场景)
16. [验收标准汇总矩阵](#验收标准汇总矩阵)

---

## 1. 测试策略概述

### 1.1 测试层级

```
┌──────────────────────────────────────────────────────┐
│  UAT 全流程验收 (本文档)                               │
│  ┌──────────────────────────────────────────────────┐ │
│  │  E2E 集成路径 (tests/integration/)               │ │
│  │  ┌──────────────────────────────────────────────┐│ │
│  │  │  单元测试 (tests/unit/) — 57个测试文件        ││ │
│  │  └──────────────────────────────────────────────┘│ │
│  └──────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────┘
```

### 1.2 UAT 三大验证域

| 验证域 | 核心目标 | 关键指标 |
|--------|---------|---------|
| **编排域** | 状态机可靠性、任务调度正确性、崩溃恢复完备性 | 状态转换 ≤5s, 恢复 ≤30s |
| **质量域** | Convergent Loop 收敛率、Finding 准确性、严重性分类 | ≥80% 在 3 轮内收敛 |
| **UX 域** | 审批摩擦度、TUI 响应性、三问题 Header 清晰度 | 90% 审批单键完成 |

### 1.3 测试执行原则

- **人驱动，系统执行**: 每个 UAT 场景由人工触发，观察系统行为
- **端到端**: 从 CLI 命令输入 → 观察 DB 状态 → TUI 显示 → 审批交互 → 最终产物
- **可重复**: 每个场景包含环境重置步骤
- **证据链**: 每个 Pass/Fail 需附截图或日志片段

---

## 2. 前置条件与环境准备

### 2.1 环境检查清单

| # | 检查项 | 验证命令 | 期望结果 |
|---|--------|---------|---------|
| E1 | Python 版本 | `python --version` | ≥3.11 |
| E2 | uv 安装 | `uv --version` | 可用 |
| E3 | 依赖安装 | `uv sync` | 无报错 |
| E4 | Claude CLI | `claude --version` | 已安装 |
| E5 | Claude 认证 | `claude auth status` | OAuth 有效 |
| E6 | Codex CLI | `codex --version` | 已安装 |
| E7 | Git | `git --version` | ≥2.20 |
| E8 | 测试项目 | 含 `_bmad/`, `ato.yaml`, epics | 完整 |

### 2.2 测试数据准备

```bash
# 克隆干净的测试项目 (或使用本项目)
git clone <test-repo> /tmp/ato-uat-project
cd /tmp/ato-uat-project

# 确保有 BMAD 配置和 epic 文件
ls _bmad/config.yaml
ls _bmad-output/planning-artifacts/epics.md

# 准备 ato.yaml 配置 (使用低成本参数)
cp ato.yaml.example ato.yaml
# 编辑: max_concurrent_agents: 2, budget_per_story: 2.0, max_rounds: 2
```

### 2.3 环境重置脚本

```bash
# 每个 UAT 场景开始前执行
rm -rf .ato/state.db .worktrees/
# 确认无残留 Orchestrator 进程
ps aux | grep "ato start" | grep -v grep
```

---

## UAT-1: 项目初始化流程

> **覆盖**: FR33, FR34, FR35, NFR5 | **Epic**: 1

### UAT-1.1 首次 `ato init` 成功路径

| 步骤 | 操作 | 预期结果 |
|------|------|---------|
| 1 | `uv run ato init .` | 启动三层 Preflight 检查 |
| 2 | 观察 Layer 1 (系统) 输出 | ✔ Python ≥3.11, ✔ Claude CLI + Auth, ✔ Codex CLI, ✔ Git |
| 3 | 观察 Layer 2 (项目) 输出 | ✔ Git repo, ✔ BMAD config, ✔ ato.yaml |
| 4 | 观察 Layer 3 (产物) 输出 | ✔ Epic 文件存在, ⚠/✔ PRD (推荐) |
| 5 | 检查 `.ato/state.db` | SQLite 文件已创建, WAL 模式 |
| 6 | 计时: 从启动到完成 | **≤3 秒** (NFR5) |

**验证命令**:
```bash
sqlite3 .ato/state.db "PRAGMA journal_mode;"      # 期望: wal
sqlite3 .ato/state.db ".tables"                     # 期望: stories, tasks, findings, approvals, cost_log, ...
sqlite3 .ato/state.db "SELECT * FROM preflight_results;" # 期望: 所有检查记录
```

### UAT-1.2 缺失依赖的降级处理

| 步骤 | 操作 | 预期结果 |
|------|------|---------|
| 1 | 临时重命名 codex binary (模拟缺失) | - |
| 2 | `uv run ato init .` | Layer 1: ✖ Codex CLI 未找到 |
| 3 | 观察错误消息 | 包含恢复建议 (安装命令) |
| 4 | 观察 Exit Code | Exit Code = 3 (CLI tool missing) |
| 5 | 恢复 codex binary | - |

### UAT-1.3 认证过期处理

| 步骤 | 操作 | 预期结果 |
|------|------|---------|
| 1 | 模拟 Claude Auth 过期 (注销) | - |
| 2 | `uv run ato init .` | ✖ Claude 认证失败 |
| 3 | 观察 Exit Code | Exit Code = 4 (Authentication error) |
| 4 | 观察错误消息 | 包含重新认证步骤 |

### UAT-1.4 重复初始化幂等性

| 步骤 | 操作 | 预期结果 |
|------|------|---------|
| 1 | `uv run ato init .` (第一次) | 成功创建 DB |
| 2 | `uv run ato init .` (第二次) | 不覆盖已有数据，报告已存在 |
| 3 | 检查 DB 内容 | 数据完整无损 |

---

## UAT-2: Batch 选择与计划预览

> **覆盖**: FR5, FR12, FR38 | **Epic**: 1, 2B

### UAT-2.1 `ato plan` 阶段预览

| 步骤 | 操作 | 预期结果 |
|------|------|---------|
| 1 | `uv run ato init .` | 初始化完成 |
| 2 | `uv run ato plan story-001` | 显示完整阶段序列 |
| 3 | 验证输出内容 | 包含: 阶段名、CLI 工具、超时值、成功/失败路径 |
| 4 | 验证阶段顺序 | queued → creating → validating → dev_ready → developing → reviewing → qa_testing → uat → merging → regression → done |

### UAT-2.2 `ato batch select` 交互式选择

| 步骤 | 操作 | 预期结果 |
|------|------|---------|
| 1 | `uv run ato batch select` | PM Agent 分析 epic/story 优先级 |
| 2 | 观察推荐列表 | 显示推荐的 story 集合 + 理由 |
| 3 | 确认选择 | 选中 story 状态变为 `queued` |
| 4 | `uv run ato batch status` | 显示当前 batch 进度 |

### UAT-2.3 `ato batch status` 输出格式

| 步骤 | 操作 | 预期结果 |
|------|------|---------|
| 1 | `uv run ato batch status` | 显示格式化的进度表 |
| 2 | `uv run ato batch status --json` | JSON 格式输出 |
| 3 | 验证 JSON 结构 | 包含 story_id, status, phase, cost 字段 |

---

## UAT-3: 编排引擎启停与生命周期

> **覆盖**: FR39, FR40, NFR2 | **Epic**: 2A

### UAT-3.1 正常启动

| 步骤 | 操作 | 预期结果 |
|------|------|---------|
| 1 | `uv run ato init .` + batch select | 有 queued stories |
| 2 | `uv run ato start` | Orchestrator 后台启动 |
| 3 | 检查 PID 文件 | `.ato/orchestrator.pid` 存在 |
| 4 | 观察日志 | "Orchestrator started" + recovery summary |
| 5 | 验证 queued story 开始处理 | story 状态自动推进: queued → creating |

### UAT-3.2 正常停止

| 步骤 | 操作 | 预期结果 |
|------|------|---------|
| 1 | Orchestrator 运行中 | 有 story 在处理 |
| 2 | `uv run ato stop` | 发送 graceful shutdown |
| 3 | 观察进行中任务 | 标记为 `paused` (非 `running`) |
| 4 | 检查 PID 文件 | 已清理 |
| 5 | 检查 DB | 所有运行中任务状态 = paused |

### UAT-3.3 重复启动保护

| 步骤 | 操作 | 预期结果 |
|------|------|---------|
| 1 | `uv run ato start` (已在运行) | 检测到已存在实例 |
| 2 | 观察输出 | 错误消息: "Orchestrator already running (PID: xxx)" |
| 3 | Exit Code | ≠ 0 |

### UAT-3.4 状态转换性能

| 步骤 | 操作 | 预期结果 |
|------|------|---------|
| 1 | Orchestrator 运行中 | - |
| 2 | 观察 story 从一个阶段转到下一阶段 | - |
| 3 | 计时: 转换延迟 | **≤5 秒** (NFR2) |

---

## UAT-4: Story 全生命周期 Happy Path

> **覆盖**: FR1–FR5, FR6–FR11, FR29–FR31 | **Epic**: 1, 2A, 2B, 3, 4
>
> **这是最关键的 UAT 场景 — 单个 Story 从 queued 到 done 的完整旅程**

### UAT-4.1 端到端 Happy Path

```
queued → creating → validating → dev_ready → developing → reviewing
  → fixing(如有) → review_passed → qa_testing → uat → merging → regression → done
```

| 步骤 | 操作 | 预期结果 | 验证方法 |
|------|------|---------|---------|
| **阶段 1: 启动** | | | |
| 1 | `ato init` + `ato batch select` (选1个story) | story 进入 queued | DB: status=queued |
| 2 | `ato start` | Orchestrator 开始处理 | 日志输出 |
| **阶段 2: 创建** | | | |
| 3 | 观察 creating 阶段 | Claude Agent 被调度, Worktree 创建 | DB: tasks 表有 phase=creating, worktree_path 非空 |
| 4 | 等待 creating 完成 | 产物文件写入 worktree | 检查 worktree 目录 |
| **阶段 3: 验证** | | | |
| 5 | 自动进入 validating | 收敛循环第一轮: BMAD story-validation skill | DB: phase=validating |
| 6 | 如有 blocking findings | 自动进入 fixing → 修复 → re-review | DB: findings 表有记录, round_num 递增 |
| 7 | 验证通过 (0 blocking) | 自动转 dev_ready | DB: phase=dev_ready |
| **阶段 4: 开发** | | | |
| 8 | dev_ready → developing | Claude Agent 在 worktree 中开发 | 新 task 记录 |
| 9 | 开发完成 | 代码产物写入 worktree | 检查 git diff in worktree |
| **阶段 5: 代码审查** | | | |
| 10 | 自动进入 reviewing | Codex Agent 执行 bmad-code-review | DB: phase=reviewing |
| 11 | 观察 Finding 记录 | blocking / suggestion 分类存储 | DB: findings 表 severity 字段 |
| 12a | 如 0 blocking | 直接 review_passed → qa_testing | DB: phase=qa_testing |
| 12b | 如有 blocking | → fixing → re-review (收敛循环) | DB: round_num 递增 |
| **阶段 6: QA 测试** | | | |
| 13 | qa_testing 阶段 | Agent 执行 QA 检查 | DB: phase=qa_testing |
| 14 | QA 通过 | → uat | DB: phase=uat |
| **阶段 7: UAT** | | | |
| 15 | uat 阶段 | Interactive Session 启动 (独立终端) | 新终端窗口打开 |
| 16 | 人工验收 | 在终端中测试功能 | - |
| 17 | `ato submit <story-id>` 或 TUI 提交 | UAT pass | DB: uat 结果记录 |
| **阶段 8: 合并** | | | |
| 18 | → merging | 进入 Merge Queue | DB: merge_queue 表 |
| 19 | Merge 审批 | 审批队列弹出 merge_authorization | TUI/CLI 显示 |
| 20 | 批准合并 | Worktree rebase → merge to main | git log main |
| **阶段 9: 回归** | | | |
| 21 | → regression | 自动执行回归测试 | DB: phase=regression, task 记录 |
| 22 | 回归通过 | → done | DB: status=done |
| **阶段 10: 清理** | | | |
| 23 | Story 完成 | Worktree 自动清理 | `.worktrees/` 中无残留 |
| 24 | 检查成本记录 | cost_log 有完整记录 | `ato cost <story-id>` |
| 25 | 检查执行历史 | 全链路 task 记录 | `ato history <story-id>` |

**关键验证点**:
```bash
# 全程可用以下命令追踪状态
watch -n 2 'sqlite3 .ato/state.db "SELECT story_id, current_phase, status FROM stories;"'
watch -n 2 'sqlite3 .ato/state.db "SELECT task_id, phase, status FROM tasks ORDER BY started_at DESC LIMIT 5;"'
```

### UAT-4.2 并行 Story 执行

| 步骤 | 操作 | 预期结果 |
|------|------|---------|
| 1 | Batch select 3 个 story | 3 个 story 进入 queued |
| 2 | `ato start` | 并行处理 (受 max_concurrent_agents 限制) |
| 3 | 观察 DB | 多个 story 同时在不同阶段 |
| 4 | 观察 worktrees | 每个 story 独立 worktree |
| 5 | 验证无状态冲突 | TransitionQueue 序列化保证 (FR4) |

---

## UAT-5: Convergent Loop 质量门控

> **覆盖**: FR13–FR18, NFR9 | **Epic**: 3

### UAT-5.1 收敛成功路径 (≤3 轮)

| 步骤 | 操作 | 预期结果 |
|------|------|---------|
| 1 | Story 进入 reviewing 阶段 | Round 1: 全量代码审查 |
| 2 | Round 1 发现 blocking findings | findings 表: severity=blocking, status=open |
| 3 | 自动进入 fixing | Claude Agent 修复 |
| 4 | Round 2: 缩窄范围 re-review | **仅审查上轮 open findings + 新问题** (FR15) |
| 5 | 验证 finding 状态转换 | open → closed / still_open / new (FR14) |
| 6 | Round 2 blocking = 0 | → review_passed |
| 7 | 验证收敛 | **≤3 轮** (NFR9) |

**验证命令**:
```bash
sqlite3 .ato/state.db "
  SELECT round_num, severity, status, COUNT(*)
  FROM findings WHERE story_id='<story>'
  GROUP BY round_num, severity, status;"
```

### UAT-5.2 收敛失败 → 升级

| 步骤 | 操作 | 预期结果 |
|------|------|---------|
| 1 | 设置 `max_rounds: 2` (低值) | - |
| 2 | Story 代码问题持续存在 | Round 1 → fix → Round 2 仍有 blocking |
| 3 | 达到 max_rounds | **自动升级**: 创建 convergent_loop_escalation 审批 (FR17) |
| 4 | TUI 显示升级通知 | Amber ◆ 标记, 包含影响评估 |
| 5 | 人工决策: 继续/放弃/手动介入 | 决策持久化 |

### UAT-5.3 确定性验证 (JSON Schema 层)

| 步骤 | 操作 | 预期结果 |
|------|------|---------|
| 1 | Agent 产出结构化输出 | - |
| 2 | 系统执行 JSON Schema 验证 (FR16) | **≤1 秒** (NFR4) |
| 3 | Schema 不合格 | 不进入 Agent 审查, 直接报错 |
| 4 | Schema 合格 | 继续进入 Agent 审查 |

### UAT-5.4 Finding 去重验证

| 步骤 | 操作 | 预期结果 |
|------|------|---------|
| 1 | 多轮 review 产出相似 finding | - |
| 2 | 检查 findings 表 | SHA256 去重: 同一 (file_path, rule_id, severity) 不重复创建 |
| 3 | 去重 hash 验证 | `dedup_hash` 字段唯一 |

---

## UAT-6: 审批队列与人机协作

> **覆盖**: FR19–FR23 | **Epic**: 4

### UAT-6.1 标准审批流程 (单键完成)

| 步骤 | 操作 | 预期结果 |
|------|------|---------|
| 1 | Story 触发 merge_authorization 审批 | 审批记录创建 |
| 2 | TUI 中观察 | ◆ 标记, 审批详情 + 推荐操作 |
| 3 | 按 `y` (采纳推荐) | **即时反馈**: "已提交，等待处理" |
| 4 | 验证 DB | approval status=approved, decision 时间戳 + 理由 (FR20) |
| 5 | Orchestrator 响应 | 2–5 秒内状态更新 |

### UAT-6.2 五种审批类型完整验证

| 审批类型 | 触发条件 | 推荐操作 | 测试方法 |
|----------|---------|---------|---------|
| `batch_select` | Batch 选择确认 | 采纳 PM 推荐 | `ato batch select` |
| `merge_authorization` | Story 通过 QA → merge | 合并 | 等待 story 到 merging |
| `session_timeout` | Interactive Session 超时 | 续期/终止 | 设置短 timeout 触发 |
| `budget_exceeded` | 成本超 budget_per_story | 继续/终止 | 设置低 budget 触发 |
| `blocking_abnormal` | Blocking findings 超阈值 | 人工介入 | 代码质量差的 story |
| `regression_failure` | 回归测试失败 | 修复/回滚 | 引入破坏性变更 |

### UAT-6.3 异常审批 (Multi-select)

| 步骤 | 操作 | 预期结果 |
|------|------|---------|
| 1 | 触发 regression_failure | Red $error 边框审批卡 |
| 2 | TUI 显示选项 | 1: 修复 / 2: 回滚 / 3: 手动介入 |
| 3 | 按 `1` 选择 | 选项高亮确认 |
| 4 | 按 Enter 提交 | 决策持久化, 系统执行对应操作 |

### UAT-6.4 审批 CLI 快捷路径

| 步骤 | 操作 | 预期结果 |
|------|------|---------|
| 1 | `uv run ato approvals` | 列出所有 pending 审批 |
| 2 | `uv run ato approval-detail <id>` | 显示完整审批上下文 |
| 3 | `uv run ato approve <id> --decision approve --reason "LGTM"` | 审批提交成功 |

### UAT-6.5 通知机制 (FR22)

| 步骤 | 操作 | 预期结果 |
|------|------|---------|
| 1 | 触发 urgent 级别事件 (regression failure) | Terminal bell `\a` 响铃 |
| 2 | 检查通知内容 | 包含: story ID, 操作类型, 风险级别, CLI 快捷命令 |
| 3 | 触发 routine 级别 (普通审批) | Terminal bell (无 macOS 通知) |

### UAT-6.6 UAT 结果提交 (FR21)

| 步骤 | 操作 | 预期结果 |
|------|------|---------|
| 1 | Story 在 uat 阶段 | Interactive Session 已完成 |
| 2 | `uv run ato uat <story-id> --result pass --description "验收通过"` | UAT 结果记录 |
| 3 | 验证状态转换 | uat → merging |
| 4 | (失败路径) `--result fail --description "边界bug"` | uat → fixing (FR48) |

---

## UAT-7: Merge Queue 与回归测试

> **覆盖**: FR29–FR32, FR52, FR54, NFR10 | **Epic**: 4

### UAT-7.1 正常合并流程

| 步骤 | 操作 | 预期结果 |
|------|------|---------|
| 1 | Story 收到 merge_authorization 审批 | 进入 Merge Queue |
| 2 | 审批通过 | Queue 开始处理 |
| 3 | 执行 rebase onto main | Worktree rebase 成功 |
| 4 | Fast-forward merge | main 分支更新 |
| 5 | `git log main` | 包含 story 的 commits |
| 6 | 自动触发 regression 测试 | task 记录: phase=regression |
| 7 | 回归通过 | → done, queue 解锁 |

### UAT-7.2 Merge Queue 串行保证

| 步骤 | 操作 | 预期结果 |
|------|------|---------|
| 1 | 同时 2 个 story 等待合并 | - |
| 2 | 观察 merge_queue 表 | 第一个 status=merging, 第二个 status=waiting |
| 3 | 第一个完成 | 第二个自动开始 |
| 4 | **绝不同时合并两个 story** | current_merge_story_id 锁机制 |

### UAT-7.3 回归失败 → 队列冻结

| 步骤 | 操作 | 预期结果 |
|------|------|---------|
| 1 | Story 合并后回归测试失败 | regression 任务 exit_code ≠ 0 |
| 2 | 队列自动冻结 | merge_queue_state: frozen=true, frozen_reason |
| 3 | 创建 regression_failure 审批 | 紧急通知 (bell + 异常审批面板) |
| 4 | **后续 story 不能合并** | NFR10: 不在 broken main 上合并 |
| 5 | 修复 story 回归通过 | 队列解冻, 后续 story 恢复 |

### UAT-7.4 Rebase 冲突处理 (FR52)

| 步骤 | 操作 | 预期结果 |
|------|------|---------|
| 1 | 故意在 main 和 worktree 修改同一文件 | - |
| 2 | Merge Queue 执行 rebase | 检测到冲突 |
| 3 | Agent 自动尝试解决 | 冲突文件标记 + 自动修复 |
| 4a | 自动解决成功 | 继续合并流程 |
| 4b | 自动解决失败 | 创建 rebase_conflict 审批, 升级人工 |

### UAT-7.5 Pre-commit Hook 失败 (FR54)

| 步骤 | 操作 | 预期结果 |
|------|------|---------|
| 1 | Agent 产出不合规代码 (lint 失败) | pre-commit hook 拦截 |
| 2 | 系统自动修复 | Agent 执行 lint/format/type-check 修复 |
| 3a | 修复成功 | 重新提交, 继续流程 |
| 3b | 修复失败 | 创建 precommit_failure 审批, 升级人工 |

---

## UAT-8: 崩溃恢复与数据完整性

> **覆盖**: FR24–FR28, NFR1, NFR6, NFR7 | **Epic**: 5

### UAT-8.1 正常崩溃恢复 (≤30 秒)

| 步骤 | 操作 | 预期结果 |
|------|------|---------|
| 1 | Orchestrator 运行中, 有 2+ story 在处理 | - |
| 2 | `kill -9 <orchestrator-pid>` (模拟崩溃) | 进程立即终止 |
| 3 | `uv run ato start` | 启动恢复流程 |
| 4 | 计时: 恢复开始 → 恢复完成 | **≤30 秒** (NFR1) |
| 5 | 观察恢复摘要 | "✔ 数据完整性已验证, ✔ N 个任务自动恢复, ◆ M 个任务需要决策" |

### UAT-8.2 四种恢复分类验证

| 分类 | 条件 | 预期行为 | 验证方法 |
|------|------|---------|---------|
| **reattach** | PID 仍存活 | 重新挂载监控 | 子进程未被 kill |
| **complete** | 产物已存在 | 标记完成, 触发转换 | 检查 worktree 中产物 |
| **reschedule** | 无 PID 无产物 (非交互阶段) | 重新调度任务 | DB: task 重置为 pending |
| **needs_human** | 交互阶段 (uat/developing) | 创建 crash_recovery 审批 | 审批队列出现 |

### UAT-8.3 WAL 数据零丢失 (NFR6)

| 步骤 | 操作 | 预期结果 |
|------|------|---------|
| 1 | 在高频状态转换中 kill 进程 | - |
| 2 | 重启后检查 DB | **所有已提交的写入完好** |
| 3 | 比对 kill 前后的 task 记录 | 无数据丢失 |

**验证命令**:
```bash
# kill 前快照
sqlite3 .ato/state.db "SELECT COUNT(*) FROM tasks;" > /tmp/before.txt

# kill -9 + restart
kill -9 $(cat .ato/orchestrator.pid)
uv run ato start

# 恢复后比对
sqlite3 .ato/state.db "SELECT COUNT(*) FROM tasks;" > /tmp/after.txt
diff /tmp/before.txt /tmp/after.txt  # 期望: after ≥ before
```

### UAT-8.4 恢复摘要与执行历史 (FR26, FR49)

| 步骤 | 操作 | 预期结果 |
|------|------|---------|
| 1 | 崩溃恢复完成 | - |
| 2 | 观察恢复摘要输出 | 自动恢复数 + 需决策数 + 选项 |
| 3 | `uv run ato history <story-id>` | 完整 task 执行历史 |
| 4 | 验证历史内容 | 时间戳、agent、阶段、产物路径、持续时间 |
| 5 | 检查失败原因 | task 失败原因 + 恢复选项 (FR50) |

### UAT-8.5 `ato stop` → `ato start` (正常重启)

| 步骤 | 操作 | 预期结果 |
|------|------|---------|
| 1 | `uv run ato stop` | 优雅关闭, 任务标记 paused |
| 2 | `uv run ato start` | 检测 paused (非 crash) → 正常恢复 |
| 3 | 验证: 不创建 crash_recovery 审批 | **与崩溃恢复路径不同** |

---

## UAT-9: Interactive Session 交互式会话

> **覆盖**: FR10, FR23 | **Epic**: 2B, 4

### UAT-9.1 Interactive Session 启动与完成

| 步骤 | 操作 | 预期结果 |
|------|------|---------|
| 1 | Story 进入 developing 或 uat 阶段 | - |
| 2 | 系统启动 Interactive Session | 独立终端窗口打开 |
| 3 | 验证 sidecar 文件 | PID, worktree_path, start_time, session_id |
| 4 | 在终端中工作 | 人工驱动 session |
| 5 | `uv run ato submit <story-id>` | Session 标记完成 |
| 6 | 验证状态转换 | developing → reviewing 或 uat → merging |

### UAT-9.2 Session 超时处理

| 步骤 | 操作 | 预期结果 |
|------|------|---------|
| 1 | 设置 `interactive_session` timeout = 60s (短值) | - |
| 2 | 启动 Interactive Session 后不操作 | - |
| 3 | 超时触发 | session_timeout 审批创建 |
| 4 | 选择: 续期 | timeout 重置, session 继续 |
| 5 | (替代) 选择: 终止 | session 结束, 需要决策 |

### UAT-9.3 崩溃后的 Session 恢复 (FR23)

| 步骤 | 操作 | 预期结果 |
|------|------|---------|
| 1 | Interactive Session 运行中 | - |
| 2 | Kill Orchestrator (模拟崩溃) | - |
| 3 | `ato start` 恢复 | 检测到 developing 阶段的 running task |
| 4 | 分类为 needs_human | crash_recovery 审批 |
| 5 | 审批选项 | restart (重启) / resume (恢复) / abandon (放弃) |
| 6 | 选择 resume | 恢复已有 session |

---

## UAT-10: TUI 指挥台全功能

> **覆盖**: FR36–FR40 | **Epic**: 6

### UAT-10.1 TUI 启动与数据连接

| 步骤 | 操作 | 预期结果 |
|------|------|---------|
| 1 | `uv run ato tui` | Textual TUI 启动 |
| 2 | SQLite 连接 | 读取最新数据 (独立进程) |
| 3 | Orchestrator 未运行时 | 优雅降级提示 (非崩溃) |

### UAT-10.2 三问题 Header (FR36)

| 步骤 | 操作 | 预期结果 |
|------|------|---------|
| 1 | 观察 Header 区域 | 系统健康? / 需要操作? / 花了多少? |
| 2 | 系统状态指示器 | ◐ running (绿) / ✔ idle / ✖ error (红) |
| 3 | 审批计数 | ◆ N 审批 (amber) |
| 4 | 成本摘要 | $XX.XX (今日) |
| 5 | 最后更新时间 | HH:MM:SS |

### UAT-10.3 Story 列表与排序

| 步骤 | 操作 | 预期结果 |
|------|------|---------|
| 1 | 观察 Story 列表 | 所有 story 显示 |
| 2 | 验证排序 | **AWAITING → ACTIVE → BLOCKED → DONE** |
| 3 | 审批项自动浮顶 | 需要审批的 story 在最上方 |
| 4 | 状态图标 | ● running / ◐ active / ◆ awaiting / ✖ failed / ✔ done |

### UAT-10.4 响应式布局

| 步骤 | 操作 | 预期结果 |
|------|------|---------|
| 1 | 终端宽度 ≥140 列 | **三面板模式**: 左列表 + 右详情 + 推荐操作 |
| 2 | 缩小到 100–139 列 | **Tabbed 模式**: [1]审批 [2]Story [3]成本 |
| 3 | 缩小到 <100 列 | **降级模式**: 仅核心信息 |
| 4 | 调整窗口大小 | 实时响应, **渲染 ≤500ms** (NFR3) |

### UAT-10.5 审批交互 (TUI 内)

| 步骤 | 操作 | 预期结果 |
|------|------|---------|
| 1 | `↑↓` 导航审批项 | 光标移动, 详情更新 |
| 2 | `y` 采纳推荐 | 即时反馈: "已提交" |
| 3 | `n` 拒绝 | 拒绝记录 |
| 4 | `d` 展开详情 | findings / changes / cost 详情 |
| 5 | 异常审批 `1/2/3` 选择 | Red 边框, 选项高亮 |

### UAT-10.6 色彩无障碍

| 步骤 | 操作 | 预期结果 |
|------|------|---------|
| 1 | 观察所有状态信息 | **三层编码**: 颜色 + 图标 + 文字 |
| 2 | 对比度检查 | ≥4.5:1 (Dracula 主题) |
| 3 | 色盲友好 | 不依赖颜色单一通道区分信息 |

### UAT-10.7 TUI 数据刷新

| 步骤 | 操作 | 预期结果 |
|------|------|---------|
| 1 | Orchestrator 处理中, TUI 打开 | - |
| 2 | 观察 story 状态变化 | **2 秒轮询间隔** 自动更新 |
| 3 | 提交审批后 | 即时反馈 + nudge 通知 Orchestrator |
| 4 | **TUI 刷新 ≤5 秒** (NFR3) | 从 DB 变化到 UI 更新 |

---

## UAT-11: CLI 命令完整性

> **覆盖**: 所有 CLI 命令 | **Epic**: 1, 2B, 4, 5

### UAT-11.1 全命令矩阵

| 命令 | 测试操作 | 预期输出 | Exit Code |
|------|---------|---------|-----------|
| `ato init .` | 项目初始化 | Preflight 结果 + DB 创建 | 0 |
| `ato plan <story>` | 阶段预览 | 完整阶段序列表 | 0 |
| `ato batch select` | Batch 选择 | 推荐列表 + 交互选择 | 0 |
| `ato batch status` | Batch 进度 | 格式化进度表 | 0 |
| `ato batch status --json` | JSON 输出 | 合法 JSON | 0 |
| `ato start` | 启动编排 | Orchestrator 启动 + 恢复摘要 | 0 |
| `ato stop` | 停止编排 | Graceful shutdown 确认 | 0 |
| `ato tui` | TUI 启动 | Textual 界面 | 0 |
| `ato submit <story>` | 提交 Session | 完成确认 | 0 |
| `ato approvals` | 列出审批 | 审批列表 | 0 |
| `ato approval-detail <id>` | 审批详情 | 完整上下文 | 0 |
| `ato approve <id>` | 提交决策 | 决策确认 | 0 |
| `ato findings <story>` | Finding 列表 | 按轮次分组 | 0 |
| `ato findings <story> --json` | JSON 输出 | 合法 JSON | 0 |
| `ato uat <story>` | UAT 结果 | 结果记录 | 0 |
| `ato history <story>` | 执行历史 | 时间线 + task 详情 | 0 |
| `ato cost <story>` | 成本报告 | token + USD 分解 | 0 |

### UAT-11.2 错误 Exit Code 验证

| 场景 | 命令 | 预期 Exit Code |
|------|------|---------------|
| 无效 story ID | `ato plan nonexistent` | 1 (General error) |
| 无效配置 | `ato start` (ato.yaml 缺失) | 2 (Config error) |
| CLI 缺失 | `ato start` (Claude 未安装) | 3 (CLI missing) |
| Auth 过期 | `ato start` (认证失效) | 4 (Auth error) |

---

## UAT-12: 性能与非功能验收

> **覆盖**: NFR1–NFR14

### UAT-12.1 性能指标矩阵

| ID | 指标 | 目标值 | 测试方法 | 通过标准 |
|----|------|--------|---------|---------|
| NFR1 | 崩溃恢复时间 | ≤30s (MVP) | kill -9 + ato start + 计时 | 恢复完成 ≤30s |
| NFR2 | 状态转换延迟 | ≤5s | 监控 transition 日志时间戳 | 每次 ≤5s |
| NFR3 | TUI 刷新间隔 | ≤5s | 观察 DB 变化 → UI 更新 | ≤5s |
| NFR3 | TUI 渲染 | ≤500ms | 调整窗口大小测量 | ≤500ms |
| NFR4 | JSON Schema 验证 | ≤1s | 大型输出验证计时 | ≤1s |
| NFR5 | Config 加载 + SM 构建 | ≤3s | ato start 启动到就绪 | ≤3s |

### UAT-12.2 可靠性验证

| ID | 要求 | 测试方法 | 通过标准 |
|----|------|---------|---------|
| NFR6 | SQLite WAL 零数据丢失 | 高频写入中 kill → 恢复 → 检查 | 无数据丢失 |
| NFR7 | 可恢复任务全部自动恢复 | 多种 running 任务 → 崩溃 → 恢复 | 100% 分类正确 |
| NFR8 | CLI 调用自动重试 1 次 | 模拟 CLI 首次失败 | 自动重试 + 成功 |
| NFR9 | 收敛循环 ≤max_rounds 终止 | 设置 max_rounds=2 + 持续问题 | 2 轮后必定终止 |
| NFR10 | 回归失败不合并 | 回归测试失败场景 | 队列冻结, 无新合并 |

### UAT-12.3 集成兼容性

| ID | 要求 | 测试方法 | 通过标准 |
|----|------|---------|---------|
| NFR11 | CLI 适配层隔离 | 升级 Claude CLI 版本 → 仅适配器变化 | 核心不变 |
| NFR12 | BMAD 适配器鲁棒性 | 不同格式的 skill 输出 | 解析成功或优雅降级 |
| NFR13 | macOS git worktree | 在 macOS 上完整流程 | 全部通过 |
| NFR14 | CLI exit code 分类 | 各种 exit code 场景 | 正确路由恢复策略 |

---

## UAT-13: 异常与边界场景

> **覆盖**: 全面的边界条件测试

### UAT-13.1 Agent 执行异常

| 场景 | 模拟方法 | 预期行为 |
|------|---------|---------|
| Claude CLI 超时 | 设置极短 timeout | session_timeout 审批创建 |
| Claude CLI 返回非 JSON | 模拟输出异常 | 解析失败 → 重试 1 次 → 升级 |
| Codex CLI 崩溃 (exit ≠ 0) | 异常代码触发 | NFR14 exit code 分类 → 重试/升级 |
| BMAD 输出格式异常 | 非标准 Markdown | BmadAdapter 降级: 语义解析 → 失败 → 人工审查 |

### UAT-13.2 Git 操作异常

| 场景 | 模拟方法 | 预期行为 |
|------|---------|---------|
| Worktree 创建失败 | 权限/磁盘空间 | 错误上报 + 任务失败 |
| Rebase 冲突 (多文件) | 同时修改 3+ 文件 | Agent 尝试解决 → 失败 → 审批 |
| Merge 非 fast-forward | 分支分叉 | rebase 先执行 → 然后 FF merge |
| Worktree 残留 (未清理) | 手动终止 story | 下次启动时检测 + 清理 |

### UAT-13.3 并发边界

| 场景 | 模拟方法 | 预期行为 |
|------|---------|---------|
| max_concurrent_agents 饱和 | 设置 =2, 启动 4 story | Semaphore 排队, 最多 2 个并发 |
| TransitionQueue 堆积 | 快速触发多个转换 | 严格串行处理, 无丢失 |
| TUI + CLI 同时写审批 | 两终端同时提交 | SQLite 事务隔离, 仅一个成功 |

### UAT-13.4 成本控制

| 场景 | 模拟方法 | 预期行为 |
|------|---------|---------|
| 单 story 成本超预算 | 设置 budget_per_story=0.50 | budget_exceeded 审批触发 |
| Token 计算准确性 | 检查 cost_log 表 | Claude: 直接读取; Codex: token * price_table |
| `ato cost` 报告 | 多 story 完成后 | 正确汇总: 按 story / 按 agent / 按阶段 |

### UAT-13.5 配置变更

| 场景 | 模拟方法 | 预期行为 |
|------|---------|---------|
| 运行中修改 ato.yaml | 编辑配置文件 | **需要重启生效** (FR51) |
| 无效配置 (循环依赖) | phases 互相引用 | Config 验证拒绝 + 明确错误 |
| 缺失 role 引用 | phase 引用不存在的 role | Config 验证拒绝 |

---

## 验收标准汇总矩阵

### FR 覆盖矩阵

| FR | 描述 | UAT 场景 | 优先级 |
|----|------|---------|--------|
| FR1 | 动态状态机构建 | UAT-3, UAT-4 | P0 |
| FR2 | 配置验证 (循环依赖) | UAT-1, UAT-13.5 | P0 |
| FR3 | 自动生命周期推进 | UAT-4.1 | P0 |
| FR4 | 并发任务无冲突 | UAT-4.2, UAT-13.3 | P0 |
| FR5 | Plan 预览 | UAT-2.1 | P1 |
| FR6 | Claude CLI 执行 | UAT-4.1 (阶段2,4) | P0 |
| FR7 | Codex CLI 执行 | UAT-4.1 (阶段5) | P0 |
| FR8 | CLI 类型/模型选择 | UAT-4.1 | P1 |
| FR9 | Agent Session 管理 | UAT-5, UAT-9 | P0 |
| FR10 | Interactive Session | UAT-9.1 | P0 |
| FR11 | BMAD 输出解析 | UAT-5, UAT-13.1 | P0 |
| FR12 | PM Batch 分析 | UAT-2.2 | P1 |
| FR13 | Convergent Loop 协议 | UAT-5.1 | P0 |
| FR14 | Finding 状态跟踪 | UAT-5.1 | P0 |
| FR15 | Re-review 范围缩窄 | UAT-5.1 (步骤4) | P0 |
| FR16 | 确定性验证 | UAT-5.3 | P1 |
| FR17 | max_rounds 后升级 | UAT-5.2 | P0 |
| FR18 | Finding 严重性分类 | UAT-5.1, UAT-6.2 | P0 |
| FR19 | 审批队列显示 | UAT-6.1, UAT-10.5 | P0 |
| FR20 | 决策持久化 | UAT-6.1 (步骤4) | P0 |
| FR21 | UAT 结果提交 | UAT-6.6 | P0 |
| FR22 | Terminal bell 通知 | UAT-6.5 | P1 |
| FR23 | Interactive Session 恢复 | UAT-9.3 | P0 |
| FR24 | SQLite WAL 持久化 | UAT-8.3 | P0 |
| FR25 | 崩溃恢复分类 | UAT-8.2 | P0 |
| FR26 | 恢复摘要显示 | UAT-8.4 | P1 |
| FR27 | 结构化指标 | UAT-4.1 (步骤24-25) | P1 |
| FR28 | 成本跟踪 | UAT-13.4 | P1 |
| FR29 | 独立 Worktree | UAT-4.1 (步骤3) | P0 |
| FR30 | Worktree 清理 | UAT-4.1 (步骤23) | P1 |
| FR31 | Merge Queue 管理 | UAT-7.1 | P0 |
| FR32 | 回归失败冻结 | UAT-7.3 | P0 |
| FR33 | `ato init` | UAT-1.1 | P0 |
| FR34 | CLI 检测 + Auth | UAT-1.1 (步骤2) | P0 |
| FR35 | 参数配置 | UAT-1.1 | P1 |
| FR36 | TUI Story 显示 | UAT-10.3 | P0 |
| FR37 | TUI 审批交互 | UAT-10.5 | P0 |
| FR38 | Batch status | UAT-2.3 | P1 |
| FR39 | `ato start` + 恢复 | UAT-3.1, UAT-8 | P0 |
| FR40 | `ato stop` 优雅关闭 | UAT-3.2 | P0 |
| FR48 | UAT 失败回退 fixing | UAT-6.6 (步骤4) | P0 |
| FR49 | 执行历史审计 | UAT-8.4 | P1 |
| FR50 | 任务失败原因 + 恢复 | UAT-8.4 (步骤5) | P1 |
| FR51 | 配置变更需重启 | UAT-13.5 | P2 |
| FR52 | Rebase 冲突自动解决 | UAT-7.4 | P1 |
| FR54 | Pre-commit hook 修复 | UAT-7.5 | P1 |

### NFR 覆盖矩阵

| NFR | 描述 | UAT 场景 | 通过标准 |
|-----|------|---------|---------|
| NFR1 | 崩溃恢复 ≤30s | UAT-12.1 | 计时验证 |
| NFR2 | 状态转换 ≤5s | UAT-12.1 | 日志时间戳 |
| NFR3 | TUI 刷新 ≤5s, 渲染 ≤500ms | UAT-12.1 | 观察 + 计时 |
| NFR4 | Schema 验证 ≤1s | UAT-12.1 | 计时验证 |
| NFR5 | Config 加载 ≤3s | UAT-12.1 | 计时验证 |
| NFR6 | WAL 零数据丢失 | UAT-8.3, UAT-12.2 | 数据比对 |
| NFR7 | 可恢复任务全恢复 | UAT-8.2, UAT-12.2 | 分类验证 |
| NFR8 | CLI 自动重试 1x | UAT-12.2 | 日志验证 |
| NFR9 | CL ≤max_rounds | UAT-5.2, UAT-12.2 | DB 验证 |
| NFR10 | 不在 broken main 合并 | UAT-7.3, UAT-12.2 | 队列冻结验证 |
| NFR11 | CLI 适配层隔离 | UAT-12.3 | 接口不变 |
| NFR12 | BMAD 适配器鲁棒性 | UAT-13.1, UAT-12.3 | 降级正确 |
| NFR13 | macOS worktree | UAT-12.3 | 全流程通过 |
| NFR14 | Exit code 分类 | UAT-13.1, UAT-12.3 | 路由正确 |

---

## 执行检查清单

### 阶段一: 基础验收 (Day 1)

- [ ] UAT-1: 项目初始化 (全部 4 个子场景)
- [ ] UAT-2: Batch 选择与 Plan 预览
- [ ] UAT-3: 编排引擎启停
- [ ] UAT-11: CLI 命令完整性

### 阶段二: 核心流程 (Day 2–3)

- [ ] UAT-4: Story 全生命周期 Happy Path (最关键)
- [ ] UAT-5: Convergent Loop 质量门控
- [ ] UAT-9: Interactive Session

### 阶段三: 人机协作 (Day 3–4)

- [ ] UAT-6: 审批队列与人机协作 (全部 6 个子场景)
- [ ] UAT-7: Merge Queue 与回归测试

### 阶段四: 韧性与体验 (Day 4–5)

- [ ] UAT-8: 崩溃恢复与数据完整性
- [ ] UAT-10: TUI 指挥台全功能
- [ ] UAT-12: 性能与非功能验收
- [ ] UAT-13: 异常与边界场景

### 完成标准

| 等级 | 条件 |
|------|------|
| **Phase 1 发布** | 所有 P0 场景通过, P1 通过率 ≥80%, 无 P0 遗留 |
| **生产就绪** | 全部 UAT 场景通过, NFR 全部达标 |
| **回归基线** | 将通过的 UAT 场景转化为自动化回归测试 |
