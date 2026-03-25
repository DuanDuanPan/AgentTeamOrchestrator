# Story 验证报告：2B.6 Interactive Session

验证时间：2026-03-25 09:43:16 CST
Story 文件：`_bmad-output/implementation-artifacts/2b-6-interactive-session.md`
验证模式：`validate-create-story`
结果：PASS（已应用修正）

## 摘要

该 story 原稿存在 6 个会直接误导开发实现的高风险缺口，已在 story 文件中修正：

1. 它要求把 `worktree_path`、`phase_type`、`task_type` 写入 `tasks`，但当前 schema 根本没有这些列。
2. 它把 Interactive Session 完成路径硬编码为 `developing` → `dev_done`，与当前配置/状态机的 phase-aware 设计不一致。
3. 它让 `ato submit` 这个外部 CLI 进程直接调用 `TransitionQueue`，违反了架构里已确定的 “SQLite write + nudge” 边界。
4. 它给出了一个与 `ApprovalRecord` 模型不兼容的 timeout approval 示例。
5. 它没有为“有新 commit”校验保存稳定基线，也没有为 session resume 说明可验证的元数据来源。
6. 它在 `subprocess_mgr.py` 中直接拼 `claude` 命令参数，违背了已有的 adapter isolation 约束。

## 已核查证据

- `_bmad-output/planning-artifacts/epics.md`
- `_bmad-output/planning-artifacts/architecture.md`
- `_bmad-output/planning-artifacts/prd.md`
- `ato.yaml.example`
- `docs/agent-team-orchestrator-system-design-input-2026-03-23.md`
- `src/ato/models/schemas.py`
- `src/ato/models/db.py`
- `src/ato/config.py`
- `src/ato/state_machine.py`
- `src/ato/subprocess_mgr.py`
- `src/ato/worktree_mgr.py`
- `src/ato/nudge.py`
- `src/ato/validation.py`
- `src/ato/adapters/bmad_adapter.py`
- `_bmad-output/implementation-artifacts/2a-2-serial-transition-queue.md`
- `_bmad-output/implementation-artifacts/2b-1-claude-agent-dispatch.md`
- `_bmad-output/implementation-artifacts/2b-4-worktree-isolation.md`
- `_bmad-output/implementation-artifacts/2b-5-batch-select-status.md`
- `tests/unit/test_config.py`
- `tests/unit/test_subprocess_mgr.py`
- `tests/unit/test_worktree_mgr.py`

## 发现的关键问题

### 1. story 发明了当前 schema 中不存在的 `tasks` 列

原稿同时要求：

- 把 `worktree_path` 注册到 `tasks` 表
- 设置 `phase_type="interactive_session"`
- 用 `task_type="interactive"` 标记 interactive task

但当前代码里：

- `worktree_path` 在 `stories` 表，不在 `tasks`
- `phase_type` 来自 `config.py` 的 `PhaseDefinition`
- `task_type` 只存在于 Context Briefing payload 语义，不是 DB 列

已应用修正：

- AC / Tasks 改为只在 `tasks` 中写 `pid` / `started_at`
- 明确复用 `stories.worktree_path`
- 把 interactive 专属元数据收敛到 `.ato/sessions/{story_id}.json` sidecar，而不是追加 DB 列

### 2. 完成路径被错误硬编码为 `developing` / `dev_done`

原稿把 `ato submit` 的合法 phase 写死为 `developing`，并要求提交固定的 `dev_done` event。

这与当前仓库已可验证的事实冲突：

- `ato.yaml.example` 把 `uat` 配置成 `interactive_session`
- `state_machine.py` 中 `uat` 的 success event 是 `uat_pass`
- phase 是否 interactive 应由 `PhaseDefinition.phase_type` 判定，而不是靠 phase 名硬编码

已应用修正：

- `ato submit` 改为校验 `story.current_phase` 是否属于配置中的 interactive phases
- 明确由 Orchestrator 根据当前 phase 派生正确 success event
- 在 story 中把 `uat_pass` / `dev_done` 都列为 phase-aware 示例

### 3. `ato submit` 被错误设计成跨进程直接调用 `TransitionQueue`

架构 Decision 2 已明确：

- TUI / `ato submit` 属于外部 writer
- 外部 writer 的路径是 “SQLite 直写 + nudge”
- `TransitionQueue.submit()` 不是外部进程调用入口

原稿却让 `ato submit` 直接 “提交 `dev_done` TransitionEvent 到 TransitionQueue”，这会误导开发者实现不存在的跨进程内存调用。

已应用修正：

- `ato submit` 只负责更新当前 interactive task 的 `completed` 状态与 `context_briefing`
- 然后调用 `send_external_nudge()`
- 由 Orchestrator `_poll_cycle()` 在本进程内检测 completed interactive task 并提交 `TransitionEvent`

### 4. timeout approval 示例与现有 `ApprovalRecord` 契约不兼容

原稿示例：

- 把 `payload` 直接写成 Python dict
- 使用不存在的 `recommended_action` 顶级字段
- 省略 `approval_id`、`status`、`created_at`

这与 `ApprovalRecord(_StrictBase)` 明显不符。

已应用修正：

- 示例改为创建完整 `ApprovalRecord`
- `payload` 改为 JSON 字符串
- `recommended_action` 与 `options` 收敛到 payload 内部

### 5. “有新 commit” 与 session resume 缺少稳定元数据基线

原稿只写 `git log base_ref..HEAD`，但没有说明：

- `base_ref` 从哪里来
- 为什么它能稳定代表“本次 interactive session 启动前”的基线
- 交互式 session 的 `session_id` 从哪里持久化

如果按原稿实现，开发者极容易把“当前分支的某个移动 ref”误当成基线，导致 submit 校验漂移；resume 也会因为没有可靠来源而被硬编。

已应用修正：

- 引入 `.ato/sessions/{story_id}.json` sidecar
- 要求在启动时记录 `base_commit`
- `has_new_commits()` 改为基于 `base_commit` 检测
- resume 仅在 sidecar 或显式参数中已有 `session_id` 时启用；否则降级为 fresh session / fork

### 6. CLI 命令构建位置违反 adapter isolation

原稿要求在 `subprocess_mgr.py` 中直接构建 `claude -p ... --resume ...` 命令。

但 Story 2B.1 和 architecture 已明确：

- CLI 参数构建属于 adapter 层
- orchestrator / subprocess manager 不直接接触具体 CLI flags

已应用修正：

- story 改为在 `claude_cli.py` 暴露或复用 interactive argv builder
- `dispatch_interactive()` 只负责终端窗口启动包装与元数据注册

## 已应用增强

- 引入 session sidecar 元数据约定：`pid` / `started_at` / `base_commit` / `session_id`
- 明确 `ContextBriefing` 以 JSON 序列化形式写入 `tasks.context_briefing`
- 明确 `task_type` 是 Context Briefing 的语义字段，不是 DB 列
- 明确 `_poll_cycle()` 需要按 `PhaseDefinition.phase_type` 识别 interactive phases
- 增补 `ApprovalRecord.payload` 为 JSON 字符串的现有契约说明
- 在 References 中补充 `ato.yaml.example`、`state_machine.py`、`2a-2-serial-transition-queue.md`

## 剩余风险

- 全新 interactive Claude session 的 `session_id` 如何在不解析完整交互输出的前提下稳定捕获，仍需在实现时通过 launcher / wrapper 方案实证。当前 story 已把“无 session_id 时降级为 fresh session”写清，因此这不是 blocker。
- 当前 story 只显式列出了 `uat` → `uat_pass` 和 `developing` → `dev_done` 两条 success event 映射。若未来把其他 phase 也标为 `interactive_session`，实现前必须先补齐 phase→event 映射 helper。

## 最终结论

修正后，这个 story 已经与当前仓库的 schema、状态机、配置模型和 Orchestrator 边界一致，可以继续保持 `ready-for-dev`。高风险的 schema 幻觉、跨进程 queue 调用、phase 硬编码和 approval payload 错配都已移除，开发者按当前版本实现时不会再被错误契约带偏。
