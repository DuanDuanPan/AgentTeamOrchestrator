# Story 验证报告：5.2 恢复摘要与执行历史查看

验证时间：2026-03-27 10:40:28 CST
Story 文件：`_bmad-output/implementation-artifacts/5-2-recovery-summary-execution-history.md`
验证模式：`validate-create-story`
结果：PASS（已应用修正）

## 摘要

该 story 的方向与 Epic 5、FR26/FR27/FR28/FR49 以及当前 recovery / CLI / cost_log 基础设施一致，但原稿里有 6 个会直接误导开发实现的缺口，已在 story 文件中修正：

1. 它把 TUI 的 TCSS 颜色变量 `$success` / `$warning` / `$error` / `$info` 直接写进了纯 Rich CLI 渲染规范，当前仓库没有对应 Rich Theme，开发者照做会得到错误或不可控样式。
2. 它把恢复摘要渲染器签名定义成 `render_recovery_summary(result)`，但 `RecoveryResult` 本身不包含 approval_id、worktree_path 等 AC2 所需字段，函数签名不足以实现 needs_human 表格。
3. 它给 crash_recovery approval 的查找方案按 `story_id` 建映射，这在同一 story 同时有多个 needs_human task 时会把审批串错。
4. 它的 `ato history` 任务清单没有把 FR49 要求的 artifact 数据源写清楚，容易让开发者只渲染 task 元信息而漏掉“产出了什么 artifact”。
5. 它的 `ato cost report` AC 明确要求 token 汇总，但 overview 表格任务只写了总 USD + 调用次数，和 AC 自己打架。
6. 它把 `ato cost report` 写成了 `@app.command("cost")` 的“命令组”，但当前仓库的 Typer 分组模式是 `Typer()` + `app.add_typer(...)`，原稿会把 CLI 结构带偏。

## 已核查证据

- 本地仓库工件：
  - `_bmad-output/planning-artifacts/epics.md`
  - `_bmad-output/planning-artifacts/prd.md`
  - `_bmad-output/planning-artifacts/architecture.md`
  - `_bmad-output/planning-artifacts/ux-design-specification.md`
  - `_bmad-output/implementation-artifacts/5-1a-crash-recovery-auto-resume.md`
  - `_bmad-output/implementation-artifacts/5-1b-crash-recovery-performance-testing.md`
- 当前代码：
  - `src/ato/core.py`
  - `src/ato/recovery.py`
  - `src/ato/approval_helpers.py`
  - `src/ato/cli.py`
  - `src/ato/subprocess_mgr.py`
  - `src/ato/models/db.py`
  - `src/ato/models/schemas.py`
  - `tests/unit/test_core.py`
  - `tests/unit/test_cost_log.py`
  - `tests/unit/test_cli_notification.py`
  - `tests/unit/test_cli_exit_codes.py`

## 发现的关键问题

### 1. CLI Rich 规范错误复用了 Textual TCSS 变量名

原稿在恢复摘要、history 状态色和 panel 边框里写了：

- `$success`
- `$warning`
- `$error`
- `$info`

这套命名来自 TUI 的 TCSS 主题，不是当前 CLI Rich 层的真实样式契约。现有 CLI Rich 代码（如 `approval-detail`）直接使用：

- `green`
- `yellow`
- `red`
- `default`

如果开发者照原稿把 `$success` 塞进 `rich.Text(..., style=...)` 或 `Panel(border_style=...)`，实现要么失败，要么依赖一个 story 根本没要求配置的自定义 Rich Theme。

已应用修正：

- 将恢复摘要 / history 中的颜色规范全部收敛为真实 Rich 样式名
- 在 Dev Notes 明确“CLI Rich 不使用 TCSS 变量”

### 2. `render_recovery_summary(result)` 无法满足 AC2 的数据需求

当前仓库里：

- `RecoveryResult` 只有 classifications + counts + mode
- `RecoveryClassification` 只有 `task_id / story_id / action / reason`
- worktree 路径在 `stories.worktree_path`
- approval_id / recommended_action 在 `approvals`

这意味着原稿定义的：

- `render_recovery_summary(result: RecoveryResult, console: Console | None = None)`

并不足以渲染 needs_human 列表中的：

- worktree 路径
- approval 快捷命令
- recommended_action

已应用修正：

- 渲染器签名改为 `render_recovery_summary(result, db_path, console=None)`
- 明确渲染器内部可补查 SQLite，但不反向修改 `RecoveryResult` 模型
- `core.py` 集成点同步改为传入 `self._db_path`

### 3. crash_recovery approval 按 `story_id` 建映射会串错审批

原稿示例是：

```python
crash_approvals = {a.story_id: a for a in approvals if a.approval_type == "crash_recovery"}
```

这和当前 recovery 合同冲突。`_mark_needs_human()` 是按 task 创建 approval，payload 中明确写入：

- `task_id`
- `phase`
- `options`

同一 story 可以同时存在多个 needs_human task，按 `story_id` 建映射只会留下最后一条 approval，导致：

- 快捷命令指向错误 approval
- 多行 needs_human 表格复用同一个 approval_id

已应用修正：

- 改为按 `payload.task_id` 建映射
- 增加对应测试要求，防止一个 story 多审批时回归

### 4. `ato history` 没把 artifact 数据源写成实现契约

FR49 要求“哪个 agent 在什么时间执行了什么任务，产出了什么 artifact”。但原稿 Task 3 只写了：

- 查询 tasks
- 渲染时间、phase、role、tool、状态、耗时、成本

这会直接诱导开发者漏掉 artifact 展示。当前仓库可用的数据源其实已经存在：

- `tasks.context_briefing` 里的 `artifacts_produced`
- `tasks.expected_artifact` fallback

已应用修正：

- AC3 / Task 3.2 / Task 3.3 / 示例输出全部补入 artifact 展示
- 明确优先 `context_briefing.artifacts_produced`，fallback `expected_artifact`

### 5. `ato cost report` 的 overview 任务和 AC 对 token 汇总不一致

原稿 AC4 明确写：

- 总 USD + 总 token 用量

但 Task 4.2 里 overview 表格只给了：

- 时间范围
- 总成本
- 调用次数

这会让实现通过 task 清单但违背 AC。当前 `cost_log` 与 `get_cost_summary()` 已有：

- `input_tokens`
- `output_tokens`
- `call_count`

story 不需要“自由发挥”，应该把它写死。

已应用修正：

- Overview 表要求补上输入/输出 token 汇总
- cost report 示例同步更新
- 单元测试任务补上 token totals 断言

### 6. `ato cost report` 的 Typer 结构写法和当前仓库模式不符

当前 `src/ato/cli.py` 的命令分组模式是：

- `batch_app = typer.Typer(...)`
- `app.add_typer(batch_app, name="batch")`

原稿却写成：

- `@app.command("cost")` 命令组

这不是当前仓库的子命令组织方式。若开发者照字面实现，要么得到一个假“组命令”，要么重走不符合现有文件结构的 CLI 路线。

已应用修正：

- Task 4.1 改为 `cost_app = typer.Typer(...)` + `app.add_typer(cost_app, name="cost")`
- Technical Constraints 也同步说明“简单命令 vs 子命令组”的 Typer 用法

## 已应用增强

- 补入 create-story 模板中的 validation note 注释。
- 把 `get_tasks_by_story()` 的稳定排序约束写实到 `ORDER BY started_at IS NULL, started_at, rowid`，避免 history 输出在 NULL `started_at` 场景下漂移。
- 增加 UTC 聚合边界说明，避免 `cost report` 在当前全 UTC 仓库里再引入“本地日界线”的隐式歧义。
- 在 Change Log 记录本次 validate-create-story 修订点，方便后续追溯。

## 剩余风险

- 这次验证只修改了 story 文档与验证报告，没有实现 `recovery_summary.py` / `ato history` / `ato cost report`，也没有新增测试执行结果；真正的 CLI 交互细节仍需在开发实现中验证。
- `ato history` 的 artifact 展示目前仍依赖已有 `context_briefing` / `expected_artifact` 数据质量；如果未来某些 phase 两者都不写，CLI 只能展示 `-`，这属于运行时数据完整性问题，不是本文档契约问题。

## 最终结论

修正后，该 story 已从“需求方向正确，但若按原稿实现会在 CLI 样式、approval 关联、artifact 展示和 Typer 结构上走偏”收敛为“可直接交给 dev-story 实施”的状态。高风险误导点已经移除：不会再把 TCSS 变量带进 Rich CLI，不会再用错误签名渲染 recovery summary，也不会再把多个 crash_recovery approval 错绑到同一 story 上。
