---
title: 'TUI 原生 UAT 提交入口'
slug: 'tui-native-uat-submission'
created: '2026-03-30T00:00:00+08:00'
status: 'ready-for-dev'
stepsCompleted: [1, 2, 3, 4]
tech_stack: ['Python 3.11', 'Textual 8.1.1', 'Typer', 'SQLite/aiosqlite']
files_to_modify: ['src/ato/cli.py', 'src/ato/tui/app.py', 'src/ato/tui/dashboard.py', 'src/ato/tui/story_detail.py', 'src/ato/tui/app.tcss', 'tests/unit/test_story_detail_view.py', 'tests/integration/test_tui_story_detail.py']
code_patterns: ['TUI 通过 SQLite + nudge 与 orchestrator 解耦', 'ATOApp 负责短生命周期 DB 写入', 'DashboardScreen 用 run_worker 调度异步提交', 'StoryDetailView 通过 Message 向上请求动作']
test_patterns: ['pytest-asyncio', 'Textual pilot 集成测试', 'Typer CLI 现有 contract 保持不变']
---

# Tech-Spec: TUI 原生 UAT 提交入口

**Created:** 2026-03-30T00:00:00+08:00

## Overview

### Problem Statement

当前 UAT 结果只能通过 `ato uat <story_id> --result pass|fail [--reason ...]` 从 CLI 回填。TUI 中的 `y/n` 只服务审批卡，Story 详情页也没有 UAT pass/fail/reason 原生入口，导致 human-in-the-loop 的最终验收必须跳出 TUI。

### Solution

在 Story 详情页的 UAT 场景下新增原生提交流程：`p` 提交 pass，`f` 打开 fail 原因输入并回车提交。DB 更新逻辑从 CLI 中抽出为共享模块，由 CLI 与 TUI 共同复用；TUI 仍通过 SQLite 写入后发送 nudge 给 orchestrator，不直接依赖 core 状态机。

### Scope

**In Scope:**
- 抽取共享 UAT 提交逻辑，统一 CLI/TUI 的 DB 写入语义
- `ATOApp` 新增 TUI 用 `submit_uat_result()`
- `StoryDetailView` 为 UAT story 提供 `p/f` 交互和 fail 原因输入
- `DashboardScreen` 接住详情页消息并异步提交
- 补充 TUI 单元/集成回归测试

**Out of Scope:**
- 修改 Dashboard 审批卡 `y/n` 语义
- 改造 `ato uat` CLI 参数合同
- 引入新的全屏 Screen push 导航
- 增加除 Story 详情页以外的 UAT 提交入口

## Context for Development

### Codebase Patterns

- `src/ato/tui/app.py` 已有 `submit_approval_decision()`，模式是：短生命周期连接写库，随后 best-effort `send_external_nudge()`
- `src/ato/tui/dashboard.py` 已通过 `run_worker()` 提交审批写入，适合作为 StoryDetailView 消息的异步执行层
- `src/ato/tui/story_detail.py` 已负责详情页快捷键，不应直接写 DB；更适合发 Message 给父级处理
- `src/ato/cli.py` 里的 `_uat_async()` 已经是 UAT 提交的真实语义来源，但不应被 `tui/` 反向 import

### Files to Reference

| File | Purpose |
| ---- | ------- |
| `src/ato/cli.py` | 现有 `ato uat` 语义与用户提示文本 |
| `src/ato/tui/app.py` | TUI 写库 + nudge 统一入口 |
| `src/ato/tui/dashboard.py` | Story 详情消息路由与异步 worker 调度 |
| `src/ato/tui/story_detail.py` | UAT 快捷键、fail 输入和状态反馈 |
| `src/ato/tui/app.tcss` | Story detail 相关样式 |
| `tests/unit/test_cli_uat.py` | 现有 CLI UAT contract 回归 |
| `tests/unit/test_story_detail_view.py` | 详情页单元测试模式 |
| `tests/integration/test_tui_story_detail.py` | Textual pilot 详情页集成测试模式 |

### Technical Decisions

- 不让 `tui/` import `cli.py`；改为新增中性模块承载 UAT DB 更新逻辑
- 详情页不 push 新 screen；fail 原因输入以内嵌输入框/提示条实现，保持当前导航结构
- `f` 在 UAT phase 中切换为 fail 入口；为保留 findings 可达性，在 UAT phase 增加 `i` 作为 findings 快捷键
- 成功提交后依然依赖 orchestrator 消费 DB 状态并推进 phase，TUI 只负责写入与 nudge

## Implementation Plan

### Tasks

- [ ] Task 1: 抽出共享 UAT 提交逻辑
  - File: `src/ato/uat.py`
  - Action: 新增共享 async helper，封装 story/phase/running-task 校验、payload 构造和 `update_task_status()` 写入
  - Notes: 返回结构化 outcome；错误包含用户可读 message/hint

- [ ] Task 2: CLI 复用共享逻辑
  - File: `src/ato/cli.py`
  - Action: 让 `_uat_async()` 调用共享 helper，保留当前输出文案、参数验证和 `_send_nudge_safe()` 行为
  - Notes: 不能破坏 `tests/unit/test_cli_uat.py` 现有 contract

- [ ] Task 3: 增加 TUI 侧 UAT 提交 API
  - File: `src/ato/tui/app.py`
  - Action: 新增 `submit_uat_result()`，调用共享 helper 并 best-effort nudge orchestrator
  - Notes: 语义与 `submit_approval_decision()` 对齐

- [ ] Task 4: 扩展 StoryDetailView 的 UAT 交互
  - File: `src/ato/tui/story_detail.py`
  - Action: 新增 UAT message、`p/f` 行为、fail 原因输入和反馈状态
  - Notes: 非 UAT phase 保持现有 `f`=findings 行为；UAT phase 改为 `f`=fail，`i`=findings

- [ ] Task 5: 在 DashboardScreen 路由详情页 UAT 提交
  - File: `src/ato/tui/dashboard.py`
  - Action: 监听 StoryDetailView 消息，使用 `run_worker()` 调用 `ATOApp.submit_uat_result()`，提交后刷新数据/详情
  - Notes: 要处理失败回滚和详情页反馈，不阻塞主线程

- [ ] Task 6: 补样式与测试
  - File: `src/ato/tui/app.tcss`
  - Action: 增加 UAT 输入提示样式
  - Notes: 保持 three-panel/tabbed 两种布局可用

- [ ] Task 7: 补回归测试
  - File: `tests/unit/test_story_detail_view.py`
  - Action: 覆盖 UAT contextual key、fail prompt、原因必填逻辑
  - Notes: 以状态变化断言为主

- [ ] Task 8: 补 TUI 集成测试
  - File: `tests/integration/test_tui_story_detail.py`
  - Action: 覆盖 `p=pass`、`f=fail+reason`、UAT detail prompt 提交
  - Notes: 断言 DB task 状态、marker 和提示可见性

### Acceptance Criteria

- [ ] AC 1: Given story 当前 phase 为 `uat`，when 用户在 Story 详情页按 `p`，then 当前 running UAT task 被标记为 `completed`，`context_briefing.uat_result == "pass"`，并发送 orchestrator nudge
- [ ] AC 2: Given story 当前 phase 为 `uat`，when 用户在 Story 详情页按 `f`，then TUI 显示原因输入入口且焦点进入输入框
- [ ] AC 3: Given UAT fail 原因输入已打开，when 用户提交非空原因，then 当前 running UAT task 被标记为 `failed`，`expected_artifact == "uat_fail_requested"`，`error_message` 含原因，并发送 orchestrator nudge
- [ ] AC 4: Given UAT fail 原因输入已打开，when 用户直接回车提交空原因，then 不写 DB，且详情页显示“原因不能为空”之类的校验反馈
- [ ] AC 5: Given story 当前 phase 不是 `uat`，when 用户在 Story 详情页按 `f`，then 仍沿用原有 findings 展开行为，不触发 UAT 提交
- [ ] AC 6: Given 现有 CLI `ato uat` contract，when 执行现有 CLI 单元测试，then pass/fail 文案、reason 要求和 task 更新语义保持兼容

## Additional Context

### Dependencies

- Textual 8.1.1 `Input` 组件
- `ato.models.db.update_task_status()`
- `ato.nudge.send_external_nudge()`

### Testing Strategy

- 跑 `tests/unit/test_cli_uat.py` 验证 CLI contract 未回归
- 跑 `tests/unit/test_story_detail_view.py` 验证 UAT 详情交互状态机
- 跑 `tests/integration/test_tui_story_detail.py` 验证 Textual pilot 下真实键盘流程
- 如无额外回归，再补 `ruff check` / `mypy` 对相关文件的检查

### Notes

- 风险点 1：`f` 原本绑定 findings，UAT phase 中需要上下文化处理，避免破坏非 UAT 详情页
- 风险点 2：详情页输入框要避免抢占非输入态快捷键焦点
- 风险点 3：TUI 不应直接推进状态机，只应复用 CLI 的 DB marker 语义
