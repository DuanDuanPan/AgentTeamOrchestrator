# Agent Team Orchestrator — 文档索引

> **最后更新**: 2026-03-29

---

## 📚 核心文档

| 文档 | 描述 | 目标读者 |
|------|------|----------|
| [架构概览](architecture.md) | 系统愿景、高层架构、模块职责、数据流、安全设计 | AI Agent / 架构师 |
| [API 契约](api-contracts.md) | Pydantic 模型、数据库接口、状态机事件、CLI 命令 | AI Agent / 开发者 |
| [开发指南](dev-guide.md) | 环境搭建、编码规范、开发工作流、调试技巧 | 开发者 |

---

## 📋 项目元数据

| 文档 | 描述 |
|------|------|
| [系统设计输入](agent-team-orchestrator-system-design-input-2026-03-23.md) | 原始系统设计需求文档 |

---

## 📊 扫描与分析

| 文档 | 描述 |
|------|------|
| [扫描状态报告](project-scan-report.json) | bmad-document-project 扫描进度跟踪 |

---

## 🏗️ 架构覆盖范围

### 核心编排层 ✅
- `core.py` — Orchestrator 主类、Poll Cycle、启动/恢复/关闭
- `state_machine.py` — Story 生命周期状态机 (12+ 状态, 20+ 事件)
- `transition_queue.py` — FIFO 事件队列、串行消费
- `recovery.py` — 四路崩溃恢复引擎
- `merge_queue.py` — 串行化 Merge Queue

### 执行与质量层 ✅
- `subprocess_mgr.py` — Agent 并发调度
- `convergent_loop.py` — 审查→修复→复审 质量门控
- `validation.py` — JSON Schema 确定性验证
- `preflight.py` — 三层预检引擎
- `design_artifacts.py` — 设计工件管理

### 基础设施层 ✅
- `config.py` — 声明式配置
- `worktree_mgr.py` — Git Worktree 生命周期
- `nudge.py` — 进程通知
- `approval_helpers.py` — 审批 API
- `batch.py` — Batch 管理
- `logging.py` — 结构化日志

### 数据层 ✅
- `models/schemas.py` — 所有 Pydantic 模型 + 异常 + 常量
- `models/db.py` — SQLite DDL + CRUD

### 适配器层 ✅
- `adapters/base.py` — BaseAdapter 抽象接口
- `adapters/claude_cli.py` — Claude CLI 适配器
- `adapters/codex_cli.py` — Codex CLI 适配器
- `adapters/bmad_adapter.py` — BMAD 输出解析器

### CLI / TUI 层 ✅
- `cli.py` — Typer CLI 入口
- `tui/` — Textual Dashboard

---

## 📝 文档生成说明

本文档集由 `bmad-document-project` 技能自动生成，基于对整个 `src/ato/` 源码树的穷尽式扫描。所有技术细节已通过源码逐行验证。

### 文档质量保证

- ✅ 所有模块均已扫描并纳入文档
- ✅ Pydantic 模型字段完整对齐源码
- ✅ 状态机事件列表与 `state_machine.py` 定义一致
- ✅ 数据库 DDL 与 `db.py` 定义一致
- ✅ CLI 命令列表与 `cli.py` 定义一致
- ✅ Approval 类型完整列表与 `schemas.py` 常量对齐
