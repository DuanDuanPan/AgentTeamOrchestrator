# Story 验证报告：5.1a 崩溃恢复自动恢复

验证时间：2026-03-25 11:37:48 CST
Story 文件：`_bmad-output/implementation-artifacts/5-1a-crash-recovery-auto-resume.md`
验证模式：`validate-create-story`
结果：FAIL

## 摘要

该 story 的目标方向与 Epic 5 一致，但当前版本仍有 3 个会直接阻断实现的缺口：

1. 它要求“PID 仍存活时重新注册监听”，但现有运行时只保存 PID 元数据，没有可恢复的进程句柄、输出文件契约或 session 恢复契约，开发者按现稿无法实现这个路径。
2. 它把“artifact 存在”写成恢复分类依据，却没有要求在 dispatch 阶段实际注册 `expected_artifact` 或其他可判定的 artifact 证据，导致 AC3 在当前仓库里没有输入数据可用。
3. 它对“Interactive Session 判断”和“从 artifact 继续流水线”的技术指引不够准确，容易诱导开发者用错误的 phase 判断或事件名拼接方式实现恢复逻辑。

## 已核查证据

- 本地仓库工件：
  - `_bmad-output/planning-artifacts/epics.md`
  - `_bmad-output/planning-artifacts/architecture.md`
  - `_bmad-output/planning-artifacts/prd.md`
  - `_bmad-output/planning-artifacts/ux-design-specification.md`
  - `_bmad-output/project-context.md`
  - `_bmad-output/planning-artifacts/research/technical-claude-codex-cli-integration-research-2026-03-24.md`
- 当前代码：
  - `src/ato/core.py`
  - `src/ato/recovery.py`
  - `src/ato/subprocess_mgr.py`
  - `src/ato/adapters/base.py`
  - `src/ato/adapters/claude_cli.py`
  - `src/ato/adapters/codex_cli.py`
  - `src/ato/transition_queue.py`
  - `src/ato/state_machine.py`
  - `src/ato/config.py`
  - `src/ato/models/db.py`
  - `src/ato/models/schemas.py`
  - `tests/unit/test_core.py`
  - `tests/integration/test_orchestrator_lifecycle.py`
  - `tests/integration/test_wal_recovery.py`

## 发现的关键问题

### 1. “PID 存活 → 重新注册监听”在当前运行时契约下不可直接实现

原 story 把 AC2 和 Task 1.6 写成：

- PID 存活 → `_reattach()` → 自动恢复
- `RecoveryEngine` 只需拿到 `subprocess_mgr` 即可恢复监听

这与当前仓库事实不匹配：

- `SubprocessManager.running` 只保存 `RunningTask(task_id, story_id, phase, pid, started_at)` 元数据，不保存 `asyncio.subprocess.Process` 句柄。
- `dispatch()` 里对结构化任务的结果收集依赖 live process handle + `proc.communicate()`；Orchestrator 崩溃后，这个等待协程已经不存在。
- `ClaudeAdapter` / `CodexAdapter` 也没有持久化 stdout/stderr/result 文件或“重连后继续收集结果”的恢复协议。
- `Orchestrator` 当前甚至没有 `SubprocessManager` 成员或启动期 wiring；story 只在 `RecoveryEngine.__init__` 里提到了 `subprocess_mgr`，但没有把这个依赖如何进入 `core.py` 写成明确任务。

这意味着开发者即使照着 story 实现 `_reattach()`，也只能把 PID 塞回内存字典，无法在进程结束后拿到 adapter 结果、写回 task 终态、写 cost_log 或提交 TransitionEvent。

**阻断原因：**
这不是“实现细节可自由发挥”的问题，而是恢复契约本身缺失。story 必须先明确以下之一：

- 为结构化任务新增持久化恢复元数据（例如 output file / recovery manifest / session handle）并把 adapter / subprocess_mgr 一起纳入改动范围；或
- 明确 AC2 的可恢复范围只限当前已有可恢复契约的任务类型，并把不满足契约的活跃 PID 降级到其他处理路径。

在没有这个前置收敛前，story 还不能进入开发。

### 2. AC3 依赖 `expected_artifact`，但 story 没要求任何地方真正注册它

story 当前把 artifact 分类完全建立在：

- `TaskRecord.expected_artifact`
- `_artifact_exists(task)` → `Path(expected_artifact).exists()`

但现有代码里并没有真实任务在 dispatch 阶段写入 `expected_artifact`：

- `tasks` 表和 `TaskRecord` 确实有这个字段。
- 当前仓库对它的唯一真实使用是 interactive transition 提交后的哨兵值 `transition_submitted`。
- `SubprocessManager.dispatch()` / `dispatch_with_retry()` / `dispatch_interactive()` 都没有为恢复场景注册 artifact 路径或其他等价证据。

这与 Architecture 的约束直接冲突：

- `architecture.md` 明确要求“每个 subprocess 启动时注册 PID/artifact”。
- `project-context.md` 也把“artifact 存在 → 自动续接”作为崩溃恢复的基础分类。

**阻断原因：**
如果 story 不把“artifact 注册契约”写进任务层，开发者实现 `RecoveryEngine` 时根本拿不到可判定输入，AC3 只能沦为假逻辑。至少需要：

- 明确哪个模块负责在 task 创建/启动时注册 `expected_artifact` 或等价恢复证据；
- 明确不同 phase 的 artifact 判定来源，避免把 `transition_submitted` 这种消费哨兵误判为真实恢复 artifact；
- 给出对应测试入口，验证 dispatch 侧确实把恢复所需字段写进 SQLite。

### 3. phase / event 指引不精确，足以误导恢复实现

story 当前 Dev Notes 里有两处会误导实现：

1. **Interactive Session 判断**
   - 原文写的是“phase 包含 `interactive` 或通过 config 中的 `interactive_phases` 列表判断”。
   - 但当前代码没有任何“phase 名称包含 interactive”这一契约。
   - 仓库内的真实判定方式是 `build_phase_definitions(settings)` 后检查 `phase_type == "interactive_session"`。

2. **从 artifact 继续流水线**
   - story 要求 `_complete_from_artifact()` “触发下一阶段 transition”，但没有定义 phase 到 success event 的明确映射。
   - 当前状态机事件名并不规则，不能靠字符串拼接猜：
     - `creating -> create_done`
     - `validating -> validate_pass`
     - `developing -> dev_done`
     - `reviewing -> review_pass`
     - `qa_testing -> qa_pass`
     - `uat -> uat_pass`
     - `merging -> merge_done`
     - `regression -> regression_pass`

**阻断原因：**
如果不把这两个 guardrail 写进 story，开发者很容易：

- 用 phase 名字模式匹配去识别 interactive task；
- 在恢复路径里自行拼接 `"{phase}_done"` 之类的事件名；
- 最后得到一个“能跑部分 case、但状态机行为错误”的恢复实现。

这部分必须前移到 story Tasks / Dev Notes，不能只留在开发者自由推断层。

## 已应用增强

（无——验证未通过，不应用增强）

## 剩余风险

- Story 5.1a 当前仍偏宽：它同时覆盖恢复模式检测、任务分类、正常重启恢复、approval 创建、日志、模型、DB helpers 与集成测试。如果按正确的恢复契约补齐，工作量可能进一步上升；必要时应考虑把“活跃 PID 恢复契约”与“artifact/paused 恢复编排”拆开。
- `ato stop` 在 Epic 2A.3 的 AC 中承诺“等待当前 CLI 调用完成（或超时后清理）”，但当前 `core.py` 尚未体现对子进程生命周期的显式拥有关系。5.1a 如果不把这条历史欠账纳入恢复设计，正常重启路径仍可能出现重复调度风险。

## 最终结论

Story 未通过验证。当前版本最大的问题不是文案，而是恢复契约缺失：`PID alive -> reattach` 与 `artifact exists -> continue` 这两条主路径在现有仓库里都没有被 story 写成可落地的实现前提。建议先重写 story 的技术任务，显式补齐：

- Orchestrator / SubprocessManager 的恢复依赖 wiring
- dispatch 阶段的 artifact / recovery metadata 注册契约
- phase → success event 的固定映射
- interactive session 的唯一判定来源

这些补齐后，再重新运行 `validate-create-story`。
