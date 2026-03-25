# Story 验证报告：1.5 ATO Plan Phase Preview

验证时间：2026-03-25 15:05:00 CST
Story 文件：`_bmad-output/implementation-artifacts/1-5-ato-plan-phase-preview.md`
验证模式：`validate-create-story`
结果：PASS（已应用修正）

## 摘要

该 story 整体方向正确，但原稿里有 2 个会直接误导实现的缺口，外加 1 个应当提前收紧的实现边界：

1. 它要求在 `blocked` 状态下“按最后已知位置标记进度”，但当前模型根本没有保存 blocked 前的 phase，这会诱导开发者捏造不存在的状态恢复逻辑。
2. 它把配置加载失败写成 `structlog.warning` 后继续，却没有把用户可见的 stderr 警告写进任务要求，和 AC4 的“stderr 输出配置加载失败警告”不一致。
3. 它在 Tasks 中没有把 `get_connection()` 的关闭责任写成显式契约，容易让 CLI 实现漏掉 `await db.close()`。

## 已核查证据

- 本地仓库工件：
  - `_bmad-output/planning-artifacts/epics.md`
  - `_bmad-output/planning-artifacts/architecture.md`
  - `_bmad-output/project-context.md`
  - `_bmad-output/implementation-artifacts/2a-1-story-state-machine-progression.md`
- 当前代码：
  - `src/ato/cli.py`
  - `src/ato/state_machine.py`
  - `src/ato/config.py`
  - `src/ato/models/db.py`
  - `src/ato/models/schemas.py`
  - `tests/unit/test_batch.py`
  - `tests/integration/test_state_persistence.py`

## 发现的关键问题

### 1. `blocked` 进度反推在当前模型下不成立

原 story 的任务与 Dev Notes 建议在 `current_phase == "blocked"` 时“按最后已知位置标记”或通过 `PHASE_TO_STATUS` 反推阶段。

这与当前仓库事实冲突：

- `StoryRecord` 只有 `status` 和 `current_phase`，没有“blocked 前 phase”字段
- `state_machine.py` / `2a-1` 已明确 `blocked` 是 MVP sink state
- `2a-1` 还明确说明 MVP **不实现 blocked 前状态 metadata 持久化**
- `PHASE_TO_STATUS` 是多对一映射，根本不能可靠反推唯一 phase

已应用修正：
- 删除“最后已知阶段”反推指导
- 改为明确：`blocked` 时只显示额外提示，不伪造 ✔ / ▶ 进度
- 调整测试要求，验证 blocked 提示而不是虚构的进度位置

### 2. 配置降级路径缺少用户可见的 stderr 契约

AC4 写的是“stderr 输出配置加载失败的警告”，但原 Task 1.6 只要求 `structlog.warning` 记录并继续。

这会让开发者实现出“日志里有 warning，但 CLI 用户看不到任何降级提示”的版本，直接偏离 AC。

已应用修正：
- Task 1.6 明确要求同时：
  - `logger.warning(...)`
  - `typer.echo("⚠ 配置加载失败，仅显示阶段序列", err=True)`
  - `phase_definitions = []` 后继续渲染
- Task 3.5 增加 stderr 断言

### 3. 数据库连接关闭责任应前置到任务层

原 story 在经验总结里提到 `get_connection()` 需要 `try/finally` 关闭，但 Tasks 没把它写成明确实现要求。

对 CLI story 来说，这是低成本但高价值的 guardrail；否则开发者很容易把它留在“Dev Notes 建议”层面而漏做。

已应用修正：
- Task 1.4 明确要求 `_plan_async` 在 `try/finally` 中 `await db.close()`

## 已应用增强

- 增加了 blocked 状态的目标渲染示例，避免实现时自由发挥
- 在状态机经验总结里补入“blocked 不保存前一阶段”的事实约束
- 更新 Change Log，记录本次 validate-create-story 的具体修订内容

## 剩余风险

- `ato plan` 当前只规划了人类可读输出，没有 `--json` 模式；这不违反本 story 的 AC，但如果后续 TUI / 自动化脚本想复用该能力，可能需要在未来 story 单独扩展。
- 配置降级时只显示 canonical sequence，不展示角色 / 类型信息；如果后续 phase schema 增加更多 CLI 可视化元数据，仍应坚持“缺配置不猜测”的原则。

## 最终结论

修正后，该 story 已与当前状态机事实、CLI 错误输出约定和现有数据库使用模式对齐，可以继续保持 `ready-for-dev`。最大的误导点已经移除：不会再诱导开发者为 `blocked` 伪造进度恢复逻辑，也不会把配置降级提示悄悄藏进日志而非 stderr。
