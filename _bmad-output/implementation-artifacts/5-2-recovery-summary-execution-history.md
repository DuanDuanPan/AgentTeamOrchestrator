# Story 5.2: 恢复摘要与执行历史查看

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a 操作者,
I want 恢复后看到人话版摘要，随时查看任意 story 的完整执行历史,
So that 恢复后安心无焦虑，系统行为可追溯审计。

## Acceptance Criteria (AC)

### AC1: 恢复摘要展示 (FR26)

```gherkin
Given 崩溃恢复完成
When 系统展示恢复摘要
Then 首行为"✔ 数据完整性检查通过"（消除焦虑）
And 显示"N 个任务自动恢复，M 个任务需要你决定"
And 使用 rich 库格式化 CLI 输出（Console + Panel/Table）
And 不使用 Textual（崩溃恢复是纯 CLI 场景）
```

### AC2: needs_human 决策列表

```gherkin
Given 有 needs_human 的任务
When 恢复摘要展示决策列表
Then 每个任务显示 worktree 路径 + 最后已知 phase + 三个选项（重启/续接/放弃）
And 使用 rich.Table 格式化任务列表
And 每个任务附带 CLI 快捷命令（如 ato approve <approval_id> --decision restart）
```

### AC3: 执行历史查看 (FR49)

```gherkin
Given 操作者想查看某个 story 的执行历史
When 运行 ato history <story-id>
Then 输出完整时间轴：哪个 agent 在什么时间执行了什么任务，产出了什么 artifact
And 使用 rich 库格式化，时间轴按时间排序
And 每条记录至少包含：时间、phase、role、cli_tool、状态、artifact、耗时、成本
And story 不存在时使用"发生了什么 + 你的选项"错误格式（Story 4-4 规范）
```

### AC4: 成本报告 (FR27, FR28)

```gherkin
Given 操作者想查看成本数据
When 运行 ato cost report
Then 输出今日/本周成本汇总（总 USD + 总 token 用量）
And 按 story 的成本明细（story_id + 总成本 + 调用次数）
And 使用 rich.Table 格式化
And 无成本数据时显示友好提示

When 运行 ato cost report --story <story-id>
Then 输出该 story 的详细成本明细（逐次 agent 调用）
And 包含 phase、role、cli_tool、model、token 数、cost_usd、时间
```

### AC5: 恢复摘要与 core.py 集成

```gherkin
Given ato start 启动检测到崩溃恢复模式
When RecoveryEngine.run_recovery() 返回 RecoveryResult
Then 调用 render_recovery_summary(result, db_path=...) 将摘要输出到 stderr
And 不阻塞后续 orchestrator 启动流程
And structlog 同时记录恢复摘要的结构化数据
```

## Tasks / Subtasks

### ⚠️ 重要前提：Story 5-1a/5-1b 已实现的基础设施

已存在的组件：
- `src/ato/recovery.py` — `RecoveryEngine` 完整实现（895 行），四路分类算法
- `src/ato/models/schemas.py` — `RecoveryResult`、`RecoveryClassification` 模型
- `src/ato/models/db.py` — `tasks` 表（含 started_at/completed_at/cost_usd/duration_ms）、`cost_log` 表、`get_cost_summary()` 函数、`get_tasks_by_story()` 函数
- `src/ato/core.py` — `_detect_recovery_mode()` 方法调用 RecoveryEngine 并返回 RecoveryResult
- `src/ato/cli.py` — 现有 CLI 命令基础，无 `history`/`cost` 命令

**本 story 新增三个 CLI 命令 + 恢复摘要渲染器，不修改 recovery.py 核心逻辑。**

- [x] Task 1: 恢复摘要渲染器 (AC: #1, #2, #5)
  - [x] 1.1 在 `src/ato/recovery_summary.py` 新建模块，实现 `render_recovery_summary(result: RecoveryResult, db_path: Path, console: Console | None = None) -> None`
  - [x] 1.2 渲染首行："✔ 数据完整性检查通过"（Rich `green` 样式，不使用 Textual TCSS 变量名）
  - [x] 1.3 渲染恢复模式标识：
    - crash 模式 → "检测到 N 个异常中断的任务，已自动分类处理"
    - normal 模式 → "检测到 N 个暂停的任务，正常恢复"
    - none 模式 → "无需恢复，系统状态正常"
  - [x] 1.4 渲染统计摘要：
    - "✔ {auto_recovered_count} 个任务自动恢复"
    - "🔄 {dispatched_count} 个任务已重新调度"（dispatched_count > 0 时）
    - "◆ {needs_human_count} 个任务需要你决定"（needs_human_count > 0 时，Rich `yellow` 样式）
  - [x] 1.5 渲染 needs_human 决策列表（使用 `rich.Table`）：
    - 渲染器内部自行查询 SQLite：从 `stories` 取 `worktree_path`，从 `approvals` 取 `approval_id/recommended_action`，不要改动 `RecoveryResult` 模型
    - crash_recovery approval 必须按 `payload.task_id` 建立映射，不要按 `story_id` 建映射（同一 story 可能存在多个 needs_human task）
    - 列：任务 ID（短）、Story、Phase、Worktree 路径、快捷命令
    - 每行附带 `ato approve <approval_id[:8]> --decision <recommended>` 提示；若 approval 缺失则显示 `-` 并记录 warning
  - [x] 1.6 所有输出写到 stderr（通过 `Console(stderr=True)` 或直接 `sys.stderr`）
  - [x] 1.7 渲染使用 `rich.panel.Panel` 包裹（标题："恢复摘要"），边框色为 `green`（无 needs_human）或 `yellow`（有 needs_human）

- [x] Task 2: core.py 集成恢复摘要输出 (AC: #5)
  - [x] 2.1 在 `src/ato/core.py` 的 `_detect_recovery_mode()` 返回 RecoveryResult 后调用 `render_recovery_summary(result, self._db_path)`
  - [x] 2.2 确保渲染在 RecoveryEngine 完成后、orchestrator 事件循环启动前执行
  - [x] 2.3 保留 `RecoveryEngine.run_recovery()` 现有 `recovery_complete` 作为恢复逻辑日志；若额外记录 `recovery_summary_rendered`，只记录渲染成功与行数，不重复分类逻辑

- [x] Task 3: ato history 命令 (AC: #3)
  - [x] 3.1 在 `src/ato/cli.py` 新增 `@app.command("history")` 命令：
    - 参数：`story_id: str`（位置参数）、`--db-path`（可选）
    - 实现 `_history_async(story_id, db_path)` 异步逻辑
  - [x] 3.2 查询数据源：
    - `get_tasks_by_story(db, story_id)` → 任务列表
    - artifact 展示优先读取 `tasks.context_briefing` 中的 `artifacts_produced`；若缺失则 fallback 到 `tasks.expected_artifact`
    - DB helper 改为稳定排序：`ORDER BY started_at IS NULL, started_at, rowid`
  - [x] 3.3 使用 `rich.Table` 渲染时间轴表格：
    - 列：时间、Phase、Role、CLI Tool、状态（带颜色图标）、Artifact、耗时、成本
    - 时间格式：`HH:MM:SS`（同日）或 `MM-DD HH:MM`（跨日）
    - 状态颜色使用 Rich 样式名：completed → `green`、failed → `red`、running → `cyan`
  - [x] 3.4 表格底部追加汇总行：总耗时、总成本、任务数
  - [x] 3.5 story 不存在时使用 `_format_cli_error()` 输出错误（复用 Story 4-4 规范）

- [x] Task 4: ato cost report 命令 (AC: #4)
  - [x] 4.1 在 `src/ato/cli.py` 新增 `cost_app = typer.Typer(...)` 并 `app.add_typer(cost_app, name="cost")`：
    - 子命令 `report`：`--story`（可选过滤）、`--db-path`
    - 实现 `_cost_report_async(story_id, db_path)` 异步逻辑
  - [x] 4.2 总览模式（无 `--story`）：
    - 使用 `cost_log` 表查询今日/本周/全部成本；时间边界按 UTC 计算，与当前仓库 datetime 存储方式保持一致
    - 新增 DB helper：`get_cost_by_period(db, since: datetime)` → `{total_cost_usd, total_input_tokens, total_output_tokens, call_count}`
    - 新增 DB helper：`get_cost_by_story(db, since: datetime | None)` → `list[{story_id, total_cost_usd, call_count}]`
    - 渲染两个 `rich.Table`：
      - 表 1：时间范围汇总（今日 / 本周 / 全部，列含总成本、输入 tokens、输出 tokens、调用次数）
      - 表 2：按 story 明细（story_id、总成本、调用次数）
  - [x] 4.3 Story 详情模式（`--story <id>`）：
    - 查询该 story 的所有 cost_log 记录
    - 新增 DB helper：`get_cost_logs_by_story(db, story_id)` → `list[CostLogRecord]`
    - 渲染 `rich.Table`：时间、Phase、Role、CLI Tool、Model、Input/Output Tokens、Cost USD（必要时附加 cache_read_input_tokens）
    - 底部汇总行
  - [x] 4.4 无成本数据时显示友好提示（"暂无成本数据。运行 story 后将自动记录。"）

- [x] Task 5: DB Helper 函数扩展 (AC: #3, #4)
  - [x] 5.1 在 `src/ato/models/db.py` 新增：
    - `get_cost_by_period(db, since: datetime) -> dict` — 按时间段聚合成本
    - `get_cost_by_story(db, since: datetime | None = None) -> list[dict]` — 按 story 聚合成本
    - `get_cost_logs_by_story(db, story_id: str) -> list[CostLogRecord]` — 获取 story 的全部成本记录
  - [x] 5.2 将 `get_tasks_by_story()` 改为稳定的 started_at 排序（`started_at` 为 NULL 的记录放最后，tie-breaker 用 `rowid`）

- [x] Task 6: 单元测试 (AC: #1-#4)
  - [x] 6.1 `tests/unit/test_recovery_summary.py`（新建文件）：
    - `test_render_crash_mode_with_needs_human` — crash 恢复有 needs_human 时的完整渲染
    - `test_render_crash_mode_all_auto_recovered` — 全部自动恢复时的简化渲染
    - `test_render_normal_mode` — normal 恢复模式渲染
    - `test_render_none_mode` — 无需恢复时的渲染
    - `test_render_dispatched_count_shown` — dispatched_count > 0 时显示重新调度行
    - `test_needs_human_table_columns` — needs_human 表格包含正确列
    - `test_recovery_summary_matches_approval_by_task_id` — 同一 story 存在多个 crash_recovery approval 时，不会串行映射错 approval
    - `test_output_to_stderr` — 输出写到 stderr 而非 stdout
  - [x] 6.2 `tests/unit/test_cli_history.py`（新建文件）：
    - `test_history_command_renders_table` — story 有任务时渲染时间轴表格
    - `test_history_command_story_not_found` — story 不存在时使用错误格式
    - `test_history_time_format_same_day` — 同日时间显示 HH:MM:SS
    - `test_history_shows_artifact_column` — 优先展示 `context_briefing.artifacts_produced`，fallback `expected_artifact`
    - `test_history_summary_row` — 表格底部汇总行正确
  - [x] 6.3 `tests/unit/test_cli_cost.py`（新建文件）：
    - `test_cost_report_overview` — 总览模式渲染两个表格
    - `test_cost_report_overview_includes_token_totals` — 今日/本周/全部表包含 token 汇总
    - `test_cost_report_by_story` — story 详情模式渲染逐条记录
    - `test_cost_report_no_data` — 无数据时显示友好提示
    - `test_cost_report_period_aggregation` — 今日/本周聚合正确

- [x] Task 7: 集成测试 (AC: #1, #3, #4, #5)
  - [x] 7.1 `tests/integration/test_recovery_summary_e2e.py`（新建文件）：
    - `test_recovery_summary_after_crash_recovery` — 构造崩溃场景 → 运行 recovery → 验证摘要输出到 stderr
    - `test_recovery_summary_includes_approval_commands` — needs_human 任务包含 CLI 快捷命令
  - [x] 7.2 `tests/integration/test_history_cost_e2e.py`（新建文件）：
    - `test_history_shows_task_timeline` — 插入任务记录 → ato history → 验证时间轴输出
    - `test_cost_report_aggregates_correctly` — 插入成本记录 → ato cost report → 验证聚合金额

## Dev Notes

### 核心设计：纯 CLI 输出，不涉及 TUI

本 story 的三个交付全部是 CLI 命令/输出：
1. **恢复摘要** — `ato start` 启动时自动输出到 stderr
2. **`ato history`** — 独立 CLI 命令
3. **`ato cost report`** — 独立 CLI 命令

全部使用 `rich` 库（不是 Textual）渲染：`Console`、`Table`、`Panel`、`Text`。

**重要边界：CLI Rich 输出使用真实 Rich 样式名（如 `green` / `yellow` / `red` / `cyan`），不要把 TUI 的 TCSS 变量名 `$success` / `$warning` / `$error` / `$info` 直接带进 CLI 代码。**

### 已存在的关键组件（复用，不重建）

| 组件 | 文件 | 说明 |
|------|------|------|
| `RecoveryEngine` | `src/ato/recovery.py` | 完整恢复引擎（895 行），不修改 |
| `RecoveryResult` | `src/ato/models/schemas.py` | 恢复结果模型（classifications + counts）|
| `RecoveryClassification` | `src/ato/models/schemas.py` | 单任务分类（task_id, story_id, action, reason）|
| `TaskRecord` | `src/ato/models/schemas.py` | 任务记录模型（含 timing/cost 字段）|
| `CostLogRecord` | `src/ato/models/schemas.py` | 成本日志模型 |
| `get_tasks_by_story()` | `src/ato/models/db.py` | 按 story 查询任务 |
| `get_cost_summary()` | `src/ato/models/db.py` | 成本聚合查询 |
| `_format_cli_error()` | `src/ato/cli.py` | 统一错误输出格式（Story 4-4）|
| `_detect_recovery_mode()` | `src/ato/core.py` | 崩溃检测入口 |

### 恢复摘要渲染示例

**全自动恢复场景：**
```
┌─ 恢复摘要 ────────────────────────────────────────────────┐
│                                                            │
│  ✔ 数据完整性检查通过                                      │
│                                                            │
│  检测到 5 个异常中断的任务，已自动分类处理                   │
│                                                            │
│  ✔ 3 个任务自动恢复                                        │
│  🔄 2 个任务已重新调度                                      │
│                                                            │
│  系统已恢复运行。                                           │
│                                                            │
└────────────────────────────────────────────────────────────┘
```

**有 needs_human 场景：**
```
┌─ 恢复摘要 ────────────────────────────────────────────────┐
│                                                            │
│  ✔ 数据完整性检查通过                                      │
│                                                            │
│  检测到 6 个异常中断的任务，已自动分类处理                   │
│                                                            │
│  ✔ 3 个任务自动恢复                                        │
│  🔄 1 个任务已重新调度                                      │
│  ◆ 2 个任务需要你决定                                      │
│                                                            │
│  ┌──────┬─────────┬────────────┬──────────────┬──────────────────────────┐ │
│  │ Task │ Story   │ Phase      │ Worktree     │ 快捷命令                  │ │
│  ├──────┼─────────┼────────────┼──────────────┼──────────────────────────┤ │
│  │ a1b2 │ story-3 │ developing │ wt/story-3   │ ato approve a1b2 --...   │ │
│  │ c3d4 │ story-7 │ developing │ wt/story-7   │ ato approve c3d4 --...   │ │
│  └──────┴─────────┴────────────┴──────────────┴──────────────────────────┘ │
│                                                            │
└────────────────────────────────────────────────────────────┘
```

### ato history 输出示例

```
Story story-005 执行历史
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 时间           Phase        Role    Tool    状态   Artifact          耗时    成本
───────────────────────────────────────────────────────────────────────────────────
 14:30:05       creating     dev     claude  ✔      plan.md           12s    $0.15
 14:30:20       developing   dev     claude  ✔      diff.patch        3m25s  $1.20
 14:34:00       reviewing    qa      codex   ✔      review.md         45s    $0.30
 14:35:00       fixing       dev     claude  ✔      fix-notes.md      1m10s  $0.45
 14:36:15       reviewing    qa      codex   ✔      rereview.md       30s    $0.25
 14:37:00       validating   qa      claude  ✔      validate.md       20s    $0.10
 14:37:25       qa_testing   qa      codex   ✔      qa-report.md      1m     $0.35
───────────────────────────────────────────────────────────────────────────────────
 汇总           7 个任务                                              7m22s  $2.80
```

### ato cost report 输出示例

```
成本报告
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

时间范围汇总
┌──────────┬──────────┬────────────┬────────────┬──────────┐
│ 时间范围 │ 总成本    │ 输入 Tokens │ 输出 Tokens │ 调用次数 │
├──────────┼──────────┼────────────┼────────────┼──────────┤
│ 今日     │ $12.50   │ 120000     │ 34000      │ 45       │
│ 本周     │ $38.70   │ 402000     │ 118000     │ 142      │
│ 全部     │ $38.70   │ 402000     │ 118000     │ 142      │
└──────────┴──────────┴────────────┴────────────┴──────────┘

按 Story 明细
┌───────────┬──────────┬──────────┐
│ Story     │ 总成本    │ 调用次数 │
├───────────┼──────────┼──────────┤
│ story-005 │ $5.20    │ 18       │
│ story-003 │ $4.10    │ 15       │
│ story-007 │ $3.20    │ 12       │
└───────────┴──────────┴──────────┘
```

### 技术约束

- **rich 库** — 已是项目依赖（Story 4-4 的 `ato approval-detail` 已使用 `rich.panel.Panel`）
- **输出到 stderr** — 恢复摘要写 stderr（不干扰 stdout 管道）；`ato history` / `ato cost` 写 stdout
- **typer 命令注册** — 简单命令用 `@app.command()`；子命令组用 `Typer()` + `app.add_typer(...)`（参考现有 `batch` 模式）
- **async 模式** — CLI 命令内部使用 `asyncio.run()` 包装 async 逻辑（现有模式）
- **退出码** — 遵循 Story 4-4 规范：0 成功 / 1 业务错误 / 2 环境错误
- **参数化查询** — 所有 SQL 使用参数化，禁止拼接
- **structlog** — 所有 CLI 命令执行记录结构化日志
- **时间边界** — `cost_log.created_at` 当前按 UTC ISO 存储；今日/本周聚合先按 UTC 实现，避免 CLI 侧再引入本地时区歧义

### Story 5-1b 的经验教训

- RecoveryEngine 已完全实现且测试充分（891 测试 + 13 性能测试全通过）
- 四路分类算法经过集成测试验证：reattach/complete/reschedule/needs_human
- RecoveryResult 的 counts 足够驱动摘要头部，但 needs_human 表格仍需补查 DB 才能拿到 `worktree_path` 和 `approval_id`
- 性能测试证明 500 tasks 恢复 < 1s，远低于 30s 目标
- 崩溃恢复的 needs_human 路径使用 SAVEPOINT 保证 task=failed + approval 创建的原子性

### needs_human 任务的 Approval ID 获取

RecoveryClassification 包含 `task_id` 和 `story_id`，但不直接包含 approval_id。needs_human 任务的 approval 由 `_mark_needs_human()` 创建，类型为 `crash_recovery`。恢复摘要渲染时需要查询对应的 pending approval：

```python
# 方案：从 DB 查询刚创建的 crash_recovery approval，
# 按 payload.task_id 建立映射；不要按 story_id 建映射
approvals = await get_pending_approvals(db)
crash_approvals: dict[str, ApprovalRecord] = {}
for approval in approvals:
    if approval.approval_type != "crash_recovery" or not approval.payload:
        continue
    payload = json.loads(approval.payload)
    task_id = payload.get("task_id")
    if isinstance(task_id, str):
        crash_approvals[task_id] = approval
```

同一 story 可能同时存在多个 `crash_recovery` approval；按 `story_id` 建映射会把多条审批错误合并成一条。

### `ato history` 的 artifact 来源

FR49 要求展示"产出了什么 artifact"。当前仓库最可信的数据源顺序应为：

1. `tasks.context_briefing`（若存在，解析 JSON 并读取 `artifacts_produced`）
2. `tasks.expected_artifact`（fallback；单值路径）
3. 都缺失时显示 `-`

不要把 finding 列表当成 artifact 时间轴的主数据源；history 主轴仍是 `tasks`。

### `ato cost report` 的聚合边界

- Overview 表必须把 AC 中要求的 token 汇总真正渲染出来，不能只显示 USD 与调用次数
- 当前仓库时间戳统一使用 UTC，`今日/本周` 聚合先以 UTC 边界实现；若未来产品引入用户时区配置，再单独扩展
- `get_cost_summary()` 已存在，可作为“全部 / 单 story 汇总”的复用入口；新增 helper 只补 period 和 story-list 聚合，不要重复实现已有总计逻辑

### Project Structure Notes

**新增文件：**
- `src/ato/recovery_summary.py` — 恢复摘要渲染器
- `tests/unit/test_recovery_summary.py` — 摘要渲染单元测试
- `tests/unit/test_cli_history.py` — history 命令单元测试
- `tests/unit/test_cli_cost.py` — cost 命令单元测试
- `tests/integration/test_recovery_summary_e2e.py` — 摘要集成测试
- `tests/integration/test_history_cost_e2e.py` — history/cost 集成测试

**修改文件：**
- `src/ato/cli.py` — 新增 `ato history` 和 `ato cost report` 命令
- `src/ato/core.py` — 恢复后调用 `render_recovery_summary()`
- `src/ato/models/db.py` — 新增 `get_cost_by_period()`, `get_cost_by_story()`, `get_cost_logs_by_story()`

### References

- [Source: _bmad-output/planning-artifacts/prd.md — FR26, FR27, FR28, FR49, NFR1, NFR6, NFR7]
- [Source: _bmad-output/planning-artifacts/architecture.md — Decision 6 结构化日志, Codex 成本计算价格表]
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md — Flow 3 崩溃恢复, PreflightOutput 视觉编码, Rich CLI 格式化]
- [Source: _bmad-output/planning-artifacts/epics.md — Epic 5 Story 5.2]
- [Source: _bmad-output/implementation-artifacts/5-1b-crash-recovery-performance-testing.md — RecoveryEngine 实现细节、性能基线]
- [Source: _bmad-output/implementation-artifacts/4-4-notification-cli-quality.md — _format_cli_error() 错误格式规范]

### Change Log

- 2026-03-27: 实现完成 — 新增 recovery_summary.py 渲染器 + ato history 命令 + ato cost report 子命令组 + 3 个 DB helpers + core.py 集成 + 23 个测试（19 单元 + 4 集成），全量 1330 测试通过
- 2026-03-27: validate-create-story 修订 —— 将 CLI 渲染色值从 Textual TCSS 变量收敛为真实 Rich 样式名；把恢复摘要渲染器签名改为显式接收 `db_path` 以查询 approval/worktree 细节；修正 crash_recovery approval 必须按 `payload.task_id` 建映射；补齐 `ato history` 的 artifact 数据源约束；把 `ato cost report` 收敛为 Typer 子命令组并补上 token 汇总与 UTC 聚合边界

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6 (1M context)

### Debug Log References

N/A

### Completion Notes List

- Task 5: 新增 `get_cost_by_period()`, `get_cost_by_story()`, `get_cost_logs_by_story()`, `_row_to_cost_log()` DB helpers。将 `get_tasks_by_story()` 排序改为 `ORDER BY started_at IS NULL, started_at, rowid`。
- Task 1: 新建 `src/ato/recovery_summary.py`，实现 `render_recovery_summary()`。支持 crash/normal/none 三种恢复模式渲染，needs_human 表格按 `payload.task_id` 映射 approval，所有输出到 stderr，使用 Rich Panel 包裹。
- Task 2: 在 `core.py` 的 `_startup()` 中，`_detect_recovery_mode()` 返回后调用 `render_recovery_summary()`，带 try/except 保证不阻塞启动。
- Task 3: 新增 `ato history <story-id>` 命令，Rich Table 渲染时间轴（时间/Phase/Role/Tool/状态/Artifact/耗时/成本），同日 HH:MM:SS 格式，底部汇总行，artifact 优先从 context_briefing 提取。
- Task 4: 新增 `ato cost report` 子命令组。总览模式渲染两表（时间范围汇总 + 按 Story 明细），story 详情模式渲染逐条 cost_log。无数据时显示友好提示。UTC 时间边界。
- Task 6: 19 个单元测试全部通过：8 个 recovery_summary + 5 个 cli_history + 6 个 cli_cost。
- Task 7: 4 个集成测试全部通过：2 个 recovery_summary_e2e + 2 个 history_cost_e2e。
- 全量回归：1330 tests passed, 0 failures。

### File List

**新增文件：**
- `src/ato/recovery_summary.py` — 恢复摘要渲染器
- `tests/unit/test_recovery_summary.py` — 恢复摘要单元测试（8 个）
- `tests/unit/test_cli_history.py` — history 命令单元测试（5 个）
- `tests/unit/test_cli_cost.py` — cost 命令单元测试（6 个）
- `tests/integration/test_recovery_summary_e2e.py` — 恢复摘要集成测试（2 个）
- `tests/integration/test_history_cost_e2e.py` — history/cost 集成测试（2 个）

**修改文件：**
- `src/ato/cli.py` — 新增 `ato history` 命令 + `ato cost report` 子命令组
- `src/ato/core.py` — `_startup()` 中调用 `render_recovery_summary()`
- `src/ato/models/db.py` — 新增 3 个 DB helpers + 修改 `get_tasks_by_story()` 排序
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — status: in-progress → review
