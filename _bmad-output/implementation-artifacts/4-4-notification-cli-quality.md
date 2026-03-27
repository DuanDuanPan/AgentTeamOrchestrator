# Story 4.4: 通知体系与 CLI 交互质量

Status: ready-for-dev

## Story

As a 操作者,
I want 系统在需要我介入时通过通知打断我，CLI 输出清晰友好,
So that 我不会错过重要决策，CLI 交互体验不需要等 TUI。

## Acceptance Criteria (AC)

### AC1: 常规 Approval 触发 Terminal Bell (FR22)

```gherkin
Given 新的 approval 创建
When approval 类型为常规（merge、timeout、budget 等 NORMAL 级别的 approval_type）
Then 发出 terminal bell 通知（\a 转义序列写入 stderr）
And 通知文本自包含 approval 短 ID 与快捷命令（如 `ato approve <id> --decision <recommended>`）
And structlog 记录 notification_sent 事件（level=normal）
```

### AC2: Regression 失败触发紧急通知 (FR22, UX 设计规范: Notification Patterns)

```gherkin
Given regression 测试失败
When merge queue 被冻结
Then 发出 terminal bell（\a 写入 stderr）
And 通知消息标注"紧急"——在 CLI 输出中明确显示"⚠ 紧急"前缀
And 通知文本自包含 approval 短 ID 与快捷命令（如 `ato approve <id> --decision <recommended>`）
And structlog 记录 notification_sent 事件（level=urgent）
And 紧急通知的 bell 连续发出两次（与 normal 单次区分）
```

### AC3: 里程碑通知 (UX 设计规范: Notification Patterns)

```gherkin
Given story 完成（transition 到 done）或 batch 全部 story 交付
When 里程碑达成
Then 发出 terminal bell 一声（milestone 级别通知）
And structlog 记录 notification_sent 事件（level=milestone）
And 若当前 active batch 的全部 story 已完成，则 `batches.status` 原子更新为 `completed` 并写入 `completed_at`
```

### AC4: 统一错误输出格式 (UX 设计规范: Emotional Design Principle #5)

```gherkin
Given 所有 CLI 命令的错误输出
When 发生错误
Then 使用"发生了什么 + 你的选项"格式（非技术堆栈），输出到 stderr
And 格式为两行：
  第一行：「发生了什么：<简明错误描述>」
  第二行：「你的选项：<可选恢复操作>」
And 退出码遵循规范：0 成功 / 1 一般错误 / 2 环境错误
```

### AC5: 异常审批 CLI Rich 格式化展示 (UX 设计规范: Flow 5 异常处理)

```gherkin
Given 异常类型 approval（regression_failure、blocking_abnormal、budget_exceeded、timeout、precommit_failure、rebase_conflict）
When 操作者通过 CLI 查看审批详情（ato approvals --detail <id> 或 ato approval-detail <id>）
Then rich 格式化输出包含"发生了什么 + 影响范围 + 你的选项"三要素
And 高风险 approval 使用红色边框 + ✖ 图标
And 输出选项列表并标注推荐操作
And 附带快捷命令提示（如 `ato approve <id> --decision <推荐>`）
```

## Tasks / Subtasks

- [ ] Task 1: NotificationService 增强 — 支持四级通知 (AC: #1, #2, #3)
  - [ ] 1.1 在 `src/ato/nudge.py` 中增强 `send_user_notification()`：
    - `urgent`: 连续发出两次 terminal bell（`\a\a`），并向 stderr 输出带"⚠ 紧急"前缀的单行通知
    - `normal`: 单次 terminal bell（`\a`），并向 stderr 输出单行通知
    - `milestone`: 单次 terminal bell（`\a`），并向 stderr 输出带"🎉"前缀的单行通知
    - `silent`: 无 bell、无 stderr 输出，仅保留结构化日志
    - 所有级别均通过 structlog 记录 `notification_sent` 事件
  - [ ] 1.2 新增 `format_notification_message(level: str, message: str) -> str` 辅助函数：
    - 根据 level 添加前缀：urgent → "⚠ 紧急: "，milestone → "🎉 "
    - normal / silent 不加前缀
    - 返回格式化后的单行消息字符串（供 stderr、日志和未来 macOS 通知复用）
  - [ ] 1.3 在 `src/ato/approval_helpers.py` 中补齐 approval 通知消息内容：
    - 消息包含 `approval_id[:8]`、`approval_type`、`story_id`
    - 若存在合法 recommended_action，附带快捷命令 `ato approve <id> --decision <recommended>`
  - [ ] 1.4 确认 `APPROVAL_TYPE_TO_NOTIFICATION` 与 `APPROVAL_RECOMMENDED_ACTIONS` 对当前所有 approval_type 覆盖完整且语义一致

- [ ] Task 2: 里程碑通知与 batch 完成收敛 (AC: #3)
  - [ ] 2.1 在 `src/ato/transition_queue.py` 中，把 story 完成通知挂在 `save_story_state()` commit 成功之后、且 `new_state == "done"` 的单一 post-commit hook：
    - 这是 story 完成通知的唯一触发点
    - 不在 `merge_queue._complete_regression_pass()` 中提前发送，避免 false positive / double bell
  - [ ] 2.2 复用现有 `get_active_batch()` / `get_batch_progress()`，在 story 进入 `done` 后检查当前 active batch 是否已全部交付：
    - 若 `progress.done == progress.total`，则进入 batch 完成收敛
  - [ ] 2.3 在 `src/ato/models/db.py` 中新增 `complete_batch()`（或等价 helper）：
    - 将 `batches.status` 从 `active` 更新为 `completed`
    - 写入 `completed_at`
    - 仅当 `active -> completed` 更新成功时发送一次 `Batch 全部交付完成！` 里程碑通知

- [ ] Task 3: 统一错误输出格式 — CLI Error Formatter (AC: #4)
  - [ ] 3.1 在 `src/ato/cli.py` 中新增 `_format_cli_error(what: str, options: str | list[str]) -> str` 辅助函数：
    - 输入："发生了什么"描述 + "你的选项"描述（字符串或列表）
    - 输出格式化的两行错误消息
    - 列表形式的 options 用 ` / ` 连接
  - [ ] 3.2 审计并重构所有 CLI 入口的错误输出，统一使用 `_format_cli_error()` 格式，至少覆盖：
    - `ato init`
    - `ato batch select`
    - `ato batch status`
    - `ato start`
    - `ato stop`
    - `ato plan`
    - `ato tui`
    - `ato submit`
    - `ato approvals`
    - `ato approve`
    - `ato uat`
  - [ ] 3.3 确认所有错误路径的退出码符合规范：
    - `typer.Exit(code=0)` — 成功
    - `typer.Exit(code=1)` — 一般业务错误（approval 不存在、参数无效、story 状态不对）
    - `typer.Exit(code=2)` — 环境错误（DB 不存在→改为 code=2、CLI 未安装、认证失败、配置加载失败）

- [ ] Task 4: 异常审批 CLI Rich 展示 — `ato approval-detail` (AC: #5)
  - [ ] 4.1 在 `src/ato/cli.py` 中新增 `approval-detail` 命令（或增强 `ato approvals` 的 `--detail` 选项）：
    - 参数：`approval_id: str`（位置参数，沿用 `get_approval_by_id()` 的前缀匹配规则：≥4 字符、歧义时报错）、`--db-path`
    - 实现 `_approval_detail_async()` 异步逻辑
  - [ ] 4.2 实现 `_render_exception_approval(approval: ApprovalRecord) -> None` 辅助函数：
    - 使用 `rich.panel.Panel` 构建三要素展示：
      - **发生了什么**：从 `approval_type` + `payload` 生成人话描述
      - **影响范围**：从 `payload` 提取（如 regression_failure → "后续 N 个 merge 被阻塞"；blocking_abnormal → "blocking 数 X 超阈值 Y"）
      - **你的选项**：从 `payload.options` 或 `_DEFAULT_VALID_OPTIONS` 提取，标注推荐操作（`★`）
    - Panel 边框颜色：`risk_level=high` → 红色（`red`），`medium` → 黄色（`yellow`），`low/None` → 默认
    - Panel title 使用类型图标 + approval_type 描述
  - [ ] 4.3 输出底部附带快捷命令提示：
    - 仅当 `recommended` 是合法 decision 时才展示 `ato approve {approval.approval_id[:8]} --decision {recommended}`
  - [ ] 4.4 审计 `APPROVAL_RECOMMENDED_ACTIONS` 与 `_DEFAULT_VALID_OPTIONS` / `payload.options` 的一致性：
    - 推荐操作必须是该 approval 的合法 decision 之一
    - 当前 `needs_human_review -> review` 与 `retry/skip/escalate` 存在 drift，需要修正或提供明确 fallback
  - [ ] 4.5 对所有 approval_type 支持基本展示（非异常类型使用简化版本）

- [ ] Task 5: 退出码规范审计与修正 (AC: #4)
  - [ ] 5.1 审计所有 `typer.Exit(code=N)` 调用，分类为业务错误(1) vs 环境错误(2)：
    - DB 不存在 → code=2（环境未初始化）
    - Preflight HALT → code=2
    - 配置加载失败 → code=2
    - 进程启动失败（已在运行、端口占用等）→ code=1
    - 审批操作错误（不存在、已处理、无效选项）→ code=1
    - Story 操作错误（不存在、状态不对）→ code=1
  - [ ] 5.2 将不符合规范的退出码修正为正确值
  - [ ] 5.3 为 CLI 退出码建立常量定义（可选）：
    ```python
    EXIT_SUCCESS = 0
    EXIT_ERROR = 1
    EXIT_ENV_ERROR = 2
    ```

- [ ] Task 6: 测试覆盖 (AC: #1-#5)
  - [ ] 6.1 `tests/unit/test_nudge.py`（追加测试）：
    - `test_send_user_notification_urgent_double_bell` — urgent 级别发送两次 bell
    - `test_send_user_notification_milestone_bell` — milestone 级别发送单次 bell
    - `test_format_notification_message_prefixes` — 各级别前缀正确
  - [ ] 6.2 `tests/unit/test_approval.py`（追加测试）：
    - `test_create_approval_notification_contains_short_id_and_quick_command` — approval 通知正文自包含短 ID 与 CLI 快捷命令
    - `test_recommended_action_aligns_with_valid_options` — 推荐操作必须落在合法 decision 集内
  - [ ] 6.3 `tests/unit/test_cli_notification.py`（新建文件）：
    - `test_format_cli_error_string_options` — 字符串选项格式正确
    - `test_format_cli_error_list_options` — 列表选项格式正确
    - `test_approval_detail_regression_failure` — regression_failure 的三要素展示
    - `test_approval_detail_blocking_abnormal` — blocking_abnormal 的三要素展示
    - `test_approval_detail_not_found` — approval 不存在时的错误输出
    - `test_approval_detail_ambiguous_prefix` — approval ID 前缀歧义时报错
    - `test_approval_detail_short_prefix_rejected` — 少于 4 字符前缀被拒绝
    - `test_approval_detail_needs_human_review_fallback` — 推荐操作 drift 时使用安全 fallback
    - `test_approval_detail_normal_type` — 非异常类型的简化展示
  - [ ] 6.4 `tests/unit/test_cli_exit_codes.py`（新建文件）：
    - `test_exit_code_db_not_exist` — DB 不存在返回 code=2
    - `test_exit_code_invalid_decision` — 无效选项返回 code=1
    - `test_exit_code_approval_not_found` — 审批不存在返回 code=1
    - `test_exit_code_batch_status_db_not_exist` — `ato batch status` 的 DB 缺失返回 code=2
    - `test_exit_code_plan_db_not_exist` — `ato plan` 的 DB 缺失返回 code=2
    - `test_exit_code_tui_db_not_exist` — `ato tui` 的 DB 缺失返回 code=2
    - `test_exit_code_env_error_preflight` — preflight 失败返回 code=2
  - [ ] 6.5 `tests/integration/test_notification_flow.py`（新建文件）：
    - `test_milestone_notification_on_story_done_post_commit` — story 完成通知仅在状态 commit 后触发
    - `test_batch_completion_marks_completed_and_notifies_once` — batch 完成时持久化为 `completed` 且仅通知一次
    - `test_urgent_notification_on_regression_failure` — regression 失败时触发 urgent bell
    - `test_normal_notification_on_approval_creation` — 常规 approval 创建时触发 normal bell

## Dev Notes

### 实现范围精确界定

本 story 有四个核心交付和一个审计交付：

**核心交付（需新写/增强代码）：**
1. **通知体系增强** — 现有 `send_user_notification()` 已支持 `urgent` / `normal` bell，需要增加 `milestone` 级别 bell 支持、urgent 双重 bell、消息前缀格式化
2. **自包含 approval 通知内容** — 通知正文包含 approval 短 ID 与快捷命令，支持 CLI 快速决策路径
3. **异常审批 CLI Rich 展示** — 全新 `ato approval-detail` 命令，三要素格式化输出
4. **统一错误输出格式** — 全局重构 CLI 错误消息为"发生了什么 + 你的选项"格式

**审计交付（修正现有代码）：**
4. **退出码规范修正** — 审计所有 exit code 调用，将环境错误从 code=1 修正为 code=2

### 现有基础设施（复用，不重建）

| 组件 | 文件 | 现状 |
|------|------|------|
| `send_user_notification()` | `src/ato/nudge.py:53-71` | urgent/normal → bell(单次)，silent → 无动作，milestone → 仅 log（本 story 需增强） |
| `_APPROVAL_TYPE_ICONS` | `src/ato/cli.py:1178-1191` | 12 种 approval 类型图标映射 ✅ |
| `_approval_summary()` | `src/ato/cli.py:1194-1228` | 确定性摘要模板 ✅ |
| `_extract_valid_options()` | `src/ato/cli.py:1337-1354` | 从 payload.options 提取合法选项 ✅ |
| `_DEFAULT_VALID_OPTIONS` | `src/ato/cli.py:1321-1334` | 各 approval_type 的默认选项映射 ✅ |
| `APPROVAL_TYPE_TO_NOTIFICATION` | `src/ato/models/schemas.py:46-59` | approval_type → NotificationLevel 映射 ✅ |
| `APPROVAL_RECOMMENDED_ACTIONS` | `src/ato/models/schemas.py:62-73` | approval_type → 推荐操作映射；当前 `needs_human_review -> review` 与合法选项存在 drift，本 story 需收敛 |
| `ApprovalRecord` | `src/ato/models/schemas.py:323-337` | 包含 payload、risk_level、recommended_action 等完整字段 ✅ |
| `create_approval()` | `src/ato/approval_helpers.py:28-119` | 统一创建 API，已自动触发 bell 通知 ✅ |
| `BatchRecord` | `src/ato/models/schemas.py:360-371` | `BatchStatus` 已支持 `active/completed/cancelled` ✅ |
| `get_active_batch()` / `get_batch_progress()` | `src/ato/models/db.py:716-813` | batch 生命周期与进度聚合 helper 已存在，可直接复用 ✅ |
| `TransitionQueue._consumer()` | `src/ato/transition_queue.py` | `save_story_state()` commit 成功后可作为 story 完成 post-commit hook ✅ |
| `_console = Console()` | `src/ato/cli.py:65` | 全局 Rich Console 实例 ✅ |
| `_send_nudge_safe()` | `src/ato/cli.py` | CLI 端安全 nudge 发送 ✅ |

### 架构约束与模式遵循

**CLI 命令模式（必须遵循 cli.py 现有模式）：**
```python
# 参考 approvals_cmd (cli.py:1232) 和 approve_cmd (cli.py:1358) 的模式
@app.command("approval-detail")
def approval_detail_cmd(
    approval_id: str = typer.Argument(..., help="Approval ID（前缀 ≥4 字符）"),
    db_path: Path | None = typer.Option(None, "--db-path", help="SQLite 数据库路径"),
) -> None:
    """查看审批详情（三要素展示）。"""
    ...
    asyncio.run(_approval_detail_async(resolved_db, approval_id))
```

**统一错误格式参考：**
```python
def _format_cli_error(what: str, options: str | list[str]) -> str:
    """生成统一 CLI 错误消息。

    格式：
      发生了什么：<描述>
      你的选项：<恢复操作>
    """
    opts = " / ".join(options) if isinstance(options, list) else options
    return f"发生了什么：{what}\n你的选项：{opts}"
```

**错误输出到 stderr 的现有模式：**
```python
# 重构前（现有代码）
typer.echo(f"错误：数据库不存在: {resolved_db}。请先运行 `ato init`。", err=True)
raise typer.Exit(code=1)

# 重构后（新格式）
typer.echo(_format_cli_error(
    f"数据库不存在: {resolved_db}",
    "运行 `ato init` 初始化项目"
), err=True)
raise typer.Exit(code=2)  # 环境错误用 code=2
```

**异常审批 Rich 展示参考设计（来自 UX 设计规范 Flow 5）：**
```python
from rich.panel import Panel
from rich.text import Text

def _render_exception_approval(approval: ApprovalRecord) -> None:
    """Rich 格式化异常审批三要素展示。"""
    # 解析 payload
    payload = json.loads(approval.payload) if approval.payload else {}
    options = payload.get("options", _DEFAULT_VALID_OPTIONS.get(approval.approval_type, []))
    recommended = approval.recommended_action or APPROVAL_RECOMMENDED_ACTIONS.get(approval.approval_type, "")

    # 构建内容
    content = Text()
    content.append("发生了什么\n", style="bold")
    content.append(f"  {_approval_summary(approval.approval_type, approval.payload)}\n\n")

    content.append("影响范围\n", style="bold")
    content.append(f"  {_extract_impact(approval)}\n\n")

    content.append("你的选项\n", style="bold")
    for i, opt in enumerate(options, 1):
        marker = "★ " if opt == recommended else "  "
        content.append(f"  {marker}[{i}] {opt}\n")

    # Panel 边框颜色
    border = "red" if approval.risk_level == "high" else "yellow" if approval.risk_level == "medium" else "default"
    icon = _APPROVAL_TYPE_ICONS.get(approval.approval_type, "?")

    panel = Panel(content, title=f"{icon} {approval.approval_type}", border_style=border)
    _console.print(panel)
```

### 通知级别行为矩阵（UX 设计规范 Notification Patterns）

| Level | Bell 行为 | 消息前缀 | 触发场景 |
|-------|----------|---------|---------|
| `urgent` | `\a\a`（两次） | `⚠ 紧急: ` | regression_failure 冻结 merge queue |
| `normal` | `\a`（一次） | 无前缀 | 常规 approval 创建（merge, timeout, budget 等） |
| `milestone` | `\a`（一次） | `🎉 ` | story 完成（done）、batch 全部交付 |
| `silent` | 无 | 无前缀 | story 阶段推进 |

**设计原则：** "regression 失败是唯一会在通知中标注'紧急'的异常类型"——来自 UX 设计规范关键交互细节。其他异常（超时、成本超限）均为 normal 优先级。

### 里程碑通知触发点分析

**Story 完成（→ done）触发路径：**
1. `merge_queue.py: _complete_regression_pass()` —— 这里只是提交 `regression_pass` 事件，story 尚未持久化为 `done`
2. `transition_queue.py` consumer —— `sm.send("regression_pass")` 后执行 `save_story_state(..., "done")` 并 commit

最佳接入点是 `transition_queue.py` 中 **commit 成功后的单一 post-commit hook**，因为只有这里才能保证：
- story 状态已经真实写入为 `done`
- 不会因 `merge_queue.py` 提前通知而产生 false positive
- 不会在 `merge_queue.py` 和 TransitionQueue 两处重复发 bell

**Batch 交付完成检测：**
- 复用 `get_active_batch()` / `get_batch_progress()` 判断 active batch 是否全部完成
- 完成后应持久化 `batches.status = completed` 且写入 `completed_at`
- 由 `active -> completed` 的 DB 状态迁移提供跨重启幂等性，而不是依赖进程内 set

### 错误输出改造范围审计

现有 CLI 命令的错误输出审计不能只覆盖 approvals / approve / uat / start，必须覆盖全部入口：

- `ato batch select`
- `ato batch status`
- `ato init`
- `ato start`
- `ato stop`
- `ato plan`
- `ato tui`
- `ato submit`
- `ato approvals`
- `ato approve`
- `ato uat`

**当前已知 drift：**
- 多个“数据库不存在”分支仍返回 code=1，和 project-context 的环境错误规范不一致
- `ato plan` 仍输出 `Story not found: ...` 英文文案，不符合统一人话错误格式
- `ato tui` / `ato submit` / `ato start` / `ato stop` 仍残留 `错误：...` 或原始异常字符串，未统一到两行格式

### SQLite 操作规范

- `PRAGMA busy_timeout=5000` 在每个连接
- `PRAGMA journal_mode=WAL` 已在 init_db 确认
- 写事务尽量短：读数据 → 处理 → 单次写 + commit
- 使用 `get_connection()` 短连接模式（CLI 场景）
- 参数化查询，禁止 SQL 拼接

### CLI 输出规范

- 错误输出到 stderr: `typer.echo(msg, err=True)`
- 成功输出到 stdout: `typer.echo(msg)` 或 `_console.print()`
- JSON 输出到 stdout: `typer.echo(json.dumps(data))`
- 退出码: 0 成功 / 1 一般错误 / 2 环境错误
- Rich 格式化使用全局 `_console = Console()` 实例

### Nudge 通知机制

- 里程碑通知是"发给用户"的，与进程间 nudge 无关
- `send_user_notification()` 负责用户可见通知（bell + 日志）
- `Nudge.notify()` 和 `send_external_nudge()` 负责进程间通信
- 两者用途不同，不混淆

### 测试模式

**CLI 命令测试模式（参考 test_cli_approval.py）：**
```python
from typer.testing import CliRunner
from ato.cli import app

runner = CliRunner()

def test_approval_detail_regression_failure(tmp_path: Path) -> None:
    """regression_failure 的三要素展示。"""
    db_path = tmp_path / "state.db"
    # 初始化 DB + 插入测试数据
    asyncio.run(_setup_db_with_approval(db_path, "regression_failure", risk_level="high"))

    result = runner.invoke(app, ["approval-detail", "test-id", "--db-path", str(db_path)])
    assert result.exit_code == 0
    assert "发生了什么" in result.output
    assert "影响范围" in result.output
    assert "你的选项" in result.output
```

**Notification 测试模式（参考 test_approval.py:380）：**
```python
def test_send_user_notification_urgent_double_bell() -> None:
    """urgent 级别应发送两次 bell。"""
    from ato.nudge import send_user_notification
    with patch("sys.stderr") as mock_stderr:
        send_user_notification("urgent", "test")
        # 验证两次 \a 写入
        calls = mock_stderr.write.call_args_list
        assert any("\a" in str(c) for c in calls)
```

### Project Structure Notes

**需要修改的文件：**
| 文件 | 修改内容 |
|------|---------|
| `src/ato/nudge.py` | 增强 `send_user_notification()`：milestone bell + urgent 双次 bell + `format_notification_message()` |
| `src/ato/approval_helpers.py` | approval 通知消息补充短 ID + 快捷命令 |
| `src/ato/cli.py` | 新增 `approval-detail` 命令 + `_format_cli_error()` + `_render_exception_approval()` + 重构错误输出格式 + 修正退出码 |
| `src/ato/transition_queue.py` | story → done 的 post-commit 里程碑通知钩子 |
| `src/ato/models/db.py` | `complete_batch()`（或等价 helper），将 batch 从 `active` 收敛为 `completed` |

**需要新增的测试文件：**
| 文件 | 测试内容 |
|------|---------|
| `tests/unit/test_cli_notification.py` | 错误格式 + 异常审批展示 + 里程碑通知 |
| `tests/unit/test_cli_exit_codes.py` | 退出码规范测试 |
| `tests/integration/test_notification_flow.py` | 端到端通知触发测试 |

**需要修改的测试文件：**
| 文件 | 修改内容 |
|------|---------|
| `tests/unit/test_nudge.py` | 追加 milestone bell + urgent 双次 bell 测试 |
| `tests/unit/test_approval.py` | 追加 approval 通知正文 / recommended_action 一致性测试 |
| `tests/unit/test_cli_approval.py` | 更新受影响的错误消息断言 |

### References

- [Source: _bmad-output/planning-artifacts/epics.md:995-1023 — Story 4.4 验收标准原文]
- [Source: _bmad-output/planning-artifacts/architecture.md:266-287 — 用户可见通知子系统，NotificationLevel 枚举与触发规则]
- [Source: _bmad-output/planning-artifacts/prd.md:491 — FR22: 系统可在需要紧急人工介入时发出 terminal bell 通知]
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md:1347-1356 — Notification Patterns（紧急/常规/静默/里程碑）]
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md:211 — Emotional Design Principle #5: "发生了什么 + 你的选项"格式]
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md:288 — Anti-Pattern: 错误信息是技术堆栈 → "发生了什么 + 你的选项"格式]
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md:957-1022 — Flow 5 异常处理流程及界面设计]
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md:1021 — "regression 失败是唯一会在通知中标注'紧急'的异常类型"]
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md:72-73 — 多渠道通知设计 + 自包含通知原则]
- [Source: _bmad-output/project-context.md:104-107 — Typer CLI 退出码规范 / 错误输出到 stderr]
- [Source: src/ato/nudge.py:53-71 — 现有 send_user_notification() 实现]
- [Source: src/ato/cli.py:1178-1191 — _APPROVAL_TYPE_ICONS 映射]
- [Source: src/ato/cli.py:1194-1228 — _approval_summary() 确定性摘要模板]
- [Source: src/ato/cli.py:1337-1354 — _extract_valid_options() 选项提取]
- [Source: src/ato/approval_helpers.py:28-119 — create_approval() 统一创建 API（含 bell 通知调用）]
- [Source: src/ato/models/schemas.py:43-59 — NotificationLevel, APPROVAL_TYPE_TO_NOTIFICATION]
- [Source: src/ato/merge_queue.py:717-746 — _handle_regression_failure() 紧急 approval 创建]
- [Source: _bmad-output/implementation-artifacts/4-1-approval-queue-nudge.md — Story 4.1 实现参考（approval 基础设施 + CLI 命令模式）]
- [Source: _bmad-output/implementation-artifacts/4-2-merge-queue-regression-safety.md — Story 4.2 实现参考（regression 失败 + merge queue 冻结）]
- [Source: _bmad-output/implementation-artifacts/4-3-uat-interactive-session-completion.md — Story 4.3 实现参考（UAT 命令 + 错误输出模式）]

### Story 4.1-4.3 关键经验（防止重复踩坑）

1. **Migration 幂等性:** SQLite 不支持 `ALTER TABLE ADD COLUMN IF NOT EXISTS`，使用 `PRAGMA table_info` 检测列是否存在
2. **Async/sync CLI 测试:** `CliRunner.invoke` 与 `asyncio.run()` 冲突，需用 sync test + async helpers 模式
3. **SAVEPOINT 事务:** `create_approval` 支持 `commit=False` 参数，在 SAVEPOINT 内使用
4. **Approval 创建统一入口:** 始终使用 `approval_helpers.create_approval()`，不要直接调 `insert_approval()`
5. **CLI 进程不创建 TransitionQueue:** CLI 命令不应创建独立 TQ 实例（4.3 的教训），使用 DB marker 模式
6. **Bell 输出到 stderr:** `sys.stderr.write("\a")` 而非 `print("\a")`，避免污染 stdout 的 JSON 输出
7. **ruff check + ruff format + mypy 全部通过后再提交**

### Git Intelligence

最近 commit 模式：
- `0e9ad35` Merge story/4.2-merge-queue-regression-safety: Merge Queue & Regression Safety
- `dc5fa85` chore: mark story 4.2 as done
- `d78076c` feat(story-4.2): Merge Queue & Regression Safety
- `68704b1` feat: Story 4.3 UAT 与 Interactive Session 完成检测实现

代码风格要点：
- commit message 格式: `feat: Story X.Y 描述` / `chore: 描述`
- 所有代码通过 ruff check + ruff format + mypy
- 测试命名: `test_<feature>_<scenario>`
- 异步测试使用 `pytest-asyncio`

### 技术栈版本确认

| 依赖 | 版本约束 | 备注 |
|------|---------|------|
| python-statemachine | ≥3.0 | async send() 支持 |
| typer | 已安装 | CLI 命令框架 |
| rich | 已安装（typer 依赖） | Panel, Table, Text, Console |
| aiosqlite | 已安装 | 异步 SQLite |
| pydantic | ≥2.0 | model_validate / model_dump_json |
| structlog | 已安装 | 结构化日志 |

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### Change Log

- 2026-03-27: `validate-create-story` 修订 —— 将里程碑通知收敛为 post-commit 单一钩子，补入 batch `completed` 持久化、自包含 approval 通知、全 CLI 错误覆盖和 recommended_action 一致性要求

### File List
