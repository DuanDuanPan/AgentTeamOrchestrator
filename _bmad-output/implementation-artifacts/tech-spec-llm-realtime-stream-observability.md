---
title: 'LLM 调用实时流式可观测性'
slug: 'llm-realtime-stream-observability'
created: '2026-03-29'
status: 'implementation-complete'
stepsCompleted: [1, 2, 3, 4]
tech_stack: [asyncio, structlog, textual, pydantic]
files_to_modify:
  - src/ato/models/schemas.py
  - src/ato/adapters/base.py
  - src/ato/adapters/claude_cli.py
  - src/ato/adapters/codex_cli.py
  - src/ato/subprocess_mgr.py
  - src/ato/models/db.py
  - src/ato/models/migrations.py
  - src/ato/tui/widgets/agent_activity.py (new)
  - src/ato/tui/widgets/heartbeat_indicator.py
  - src/ato/tui/story_detail.py
  - src/ato/tui/dashboard.py
  - src/ato/tui/app.py
  - tests/unit/test_claude_adapter.py
  - tests/unit/test_codex_adapter.py
  - tests/unit/test_subprocess_mgr.py
  - tests/unit/test_progress_event.py (new)
  - tests/unit/test_migrations.py
  - tests/unit/test_db.py
  - tests/unit/test_story_status_line.py
  - tests/unit/test_heartbeat_indicator.py
  - tests/integration/test_tui_story_detail.py
  - tests/fixtures/claude_stream_success.jsonl (new)
  - tests/fixtures/claude_stream_tool_use.jsonl (new)
  - tests/fixtures/codex_stream_success.jsonl (new)
  - tests/fixtures/codex_stream_tool_use.jsonl (new)
code_patterns:
  - 'BaseAdapter.execute() 签名含可选 on_process_start callback'
  - 'Pydantic models 统一在 models/schemas.py'
  - 'Textual reactive 属性驱动 UI 更新'
  - 'structlog.contextvars.bind_contextvars() 绑定上下文'
  - 'asyncio.create_subprocess_exec（禁止 shell=True）'
  - 'CLI adapter 返回值必须经过 Pydantic model_validate'
  - 'tests/fixtures/ 下 snapshot fixture + adapter 解析测试'
  - '_column_exists() 幂等迁移检查'
test_patterns:
  - 'tests/unit/test_<module>.py 单元测试'
  - 'pytest-asyncio asyncio_mode=auto'
  - 'mock asyncio.create_subprocess_exec'
  - 'tests/fixtures/ 下 JSON/JSONL snapshot'
---

# Tech-Spec: LLM 调用实时流式可观测性

**Created:** 2026-03-29

## Overview

### Problem Statement

当前 ClaudeAdapter 和 CodexAdapter 使用 `asyncio.subprocess` 的 `communicate()` 一次性等待进程结束，执行过程（最长 30 分钟超时）完全黑盒。操作者无法知道 LLM 正在做什么——读文件、思考、调用工具还是卡住了。

技术验证已确认：
- Claude CLI `--output-format stream-json --verbose` 实时逐行输出 NDJSON 事件（验证跨度 5.91s）
- Codex CLI `--json` 实时逐行输出 JSONL 事件（验证跨度 40.16s）
- `asyncio.subprocess.Process.stdout.readline()` 可在进程运行中逐行读取

### Solution

两个 adapter 从 `communicate()` 切换到逐行 `readline()` 流式读取 stdout，通过统一的 `ProgressCallback` 协议上报归一化的 `ProgressEvent`，SubprocessManager 透传到 TUI 实时展示当前 LLM 活动状态。

**IPC 策略**：Orchestrator 和 TUI 是独立进程，通过 SQLite 解耦。在 tasks 表新增 `last_activity_type` 和 `last_activity_summary` 列。SubprocessManager 为每个 dispatch 维护一个**latest-only、串行化**的 activity writer：普通事件最多 1 次/秒后台刷新，`result/error` 事件在终态落库前强制 flush，且 activity 写入只更新 activity 列，绝不回写 `status`。

### Scope

**In Scope:**
- BaseAdapter 新增 `ProgressCallback` 协议
- ClaudeAdapter 切换到 `stream-json` + `--verbose` + 逐行读取
- CodexAdapter 切换到逐行读取（已有 `--json`）
- 两者事件归一化为统一 `ProgressEvent`
- SubprocessManager latest-only activity writer + 节流透传 callback + 异步更新 tasks activity 列
- DB schema 迁移（tasks 表新增两列，幂等）
- TUI AgentActivityWidget：主列表 `HeartbeatIndicator`/`StoryStatusLine` 展示 + 详情页展示（含 detail mode 刷新修复）
- 最终结果仍解析为 `ClaudeOutput` / `CodexOutput`（向后兼容）

**Out of Scope:**
- 完整事件审计日志（全量持久化到独立表 — Epic 5 后续）
- Token 级 partial text streaming
- 敏感信息过滤
- TUI 轮询频率调整（保持 2s）

## Context for Development

### Codebase Patterns

**Adapter 层模式：**
- `BaseAdapter` 定义抽象 `execute()` 方法，含可选 `on_process_start: ProcessStartCallback` 回调
- `ClaudeAdapter.execute()` 用 `asyncio.create_subprocess_exec` 启动 `claude -p ... --output-format json`，用 `communicate()` 等待完成，解析 JSON stdout 为 `ClaudeOutput.from_json()`
- `CodexAdapter.execute()` 同理启动 `codex exec ... --json`，用 `communicate()` 等待，然后 `_parse_jsonl()` 解析 JSONL 事件流为 `CodexOutput.from_events()`
- 两者共享三阶段 `cleanup_process()` 协议（SIGTERM → wait → SIGKILL → wait）
- 错误分类为 `ErrorCategory`，包装为 `CLIAdapterError`

**subprocess_mgr 模式：**
- `SubprocessManager.dispatch()` 获取 semaphore → 创建 TaskRecord → 调用 adapter.execute() → PID 注册（via on_process_start） → 持久化结果到 tasks + cost_log
- `dispatch_with_retry()` 封装重试逻辑（max_retries=1，retryable 错误重试）
- 重试场景复用 task_id，UPDATE 而非 INSERT

**TUI 数据流与 detail mode 行为：**
- `ATOApp` 用 `set_interval(2.0, refresh_data)` 定期轮询 SQLite
- `DashboardScreen.update_content()` 在 `_in_detail_mode=True` 时**跳过 `_update_detail_panel()`**（dashboard.py:1165-1169），避免轮询踢回概览
- `_enter_detail_mode()` 通过 `load_story_detail()` 一次性加载详情数据（app.py:363）
- 主列表中 `status == "in_progress"` 的 story 渲染为 `HeartbeatIndicator`，非运行态 story 才渲染为 `StoryStatusLine`
- `DashboardScreen` 同时挂载两个 `StoryDetailView`：`#right-top-detail`（three-panel）和 `#tab-story-detail`（tabbed）
- **结论：activity 展示不能只放在 detail mode**——detail mode 下的详情面板在轮询时不会刷新。需要两层展示：(1) 运行态 story 更新 `HeartbeatIndicator`，非运行态 story 更新 `StoryStatusLine`；(2) 详情页有独立的 activity-only 刷新路径，且必须按当前 layout 精确定位目标 `StoryDetailView`

**Story 级 task 选择约束：**
- 一个 story 可有多个 task（不同 phase、重试等）
- 现有 elapsed 查询已限定：`t.status='running' AND t.phase=s.current_phase`（app.py:250-258）
- **activity 查询必须使用相同条件 + 确定性 tie-break**，否则会展示旧 phase / 旧重试 / 已完成 task 的残留活动
- 当同一 story 存在多个满足条件的 running task 时，选择 `started_at` 最新者；若 `started_at` 相同，再以 `rowid` 最大者为准

**StoryDetailView._expanded_view 状态：**
- `update_detail()` 会重置 `_expanded_view = None`（story_detail.py:96）
- 若在 2s 轮询中调用 `update_detail()`，用户展开的 findings/cost/history 子视图会被折叠
- **结论：需要单独的 `update_activity_only()` 方法**，只更新 AgentActivityWidget，不触碰其他状态

**数据模型与迁移模式：**
- 所有 Pydantic models 在 `models/schemas.py`
- schema 迁移用 `@_register(version)` + `_column_exists()` 幂等检查（见 v6/v8 迁移）
- `update_task_status()` 支持可选 kwargs 字段白名单模式

### Files to Reference

| File | Purpose |
| ---- | ------- |
| `src/ato/adapters/base.py` | BaseAdapter 抽象接口 + ProcessStartCallback + cleanup_process |
| `src/ato/adapters/claude_cli.py` | ClaudeAdapter：命令构建 + execute + 错误分类 |
| `src/ato/adapters/codex_cli.py` | CodexAdapter：命令构建 + JSONL 解析 + execute + 成本计算 |
| `src/ato/subprocess_mgr.py` | SubprocessManager：并发调度 + PID 注册 + DB 持久化 |
| `src/ato/models/schemas.py` | AdapterResult / ClaudeOutput / CodexOutput / TaskRecord |
| `src/ato/models/db.py` | DDL + update_task_status + _row_to_task |
| `src/ato/models/migrations.py` | Schema 迁移注册表 + `_column_exists()` 幂等检查 |
| `src/ato/tui/dashboard.py:1165-1169` | `_update_detail_panel()` detail mode 跳过逻辑 |
| `src/ato/tui/dashboard.py:542-564` | `_enter_detail_mode()` 一次性加载 |
| `src/ato/tui/app.py:250-260` | story 级 running task 查询（phase 对齐） |
| `src/ato/tui/story_detail.py:71-97` | `update_detail()` 会重置 `_expanded_view` |
| `src/ato/tui/widgets/heartbeat_indicator.py` | running story 主列表组件 |
| `src/ato/tui/widgets/story_status_line.py` | 非 running story 主列表组件 |
| `src/ato/tui/widgets/convergent_loop_progress.py` | CL 进度组件（参考 widget 模式） |

### Technical Decisions

1. **Claude CLI `stream-json` 事件类型合同** — JSON `type` 字段值为 `"system"`, `"assistant"`, `"result"`, `"user"`, `"rate_limit_event"` 等（字面值）。`subtype` 是独立字段（如 `"init"`, `"success"`）。本 spec 中 `system.init` 符号仅为速记，代码中匹配 `event["type"]`，不拼接 subtype。
2. **`_normalize_*_event()` 返回单个 ProgressEvent** — 函数签名 `(raw: dict) -> ProgressEvent`。对于 Claude `assistant` 事件，`content[]` 数组中可能含多种类型（text + tool_use），函数扫描数组取**最有信息量**的一项（优先级：tool_use > text > 其他），返回对应的单个 ProgressEvent。这是摘要，不是过滤。
3. **`on_progress` 是可选参数** — 不传则行为与当前一致（向后兼容）
4. **stdout readline + stderr drain 并发** — `drain_stderr()` 作为独立 `asyncio.Task`；**所有退出路径（正常/超时/异常）必须在 finally 中 cancel + await stderr_task**
5. **SubprocessManager activity 写入必须与 task 状态写入解耦** — 新增 `update_task_activity()` 辅助函数，仅更新 `last_activity_type/last_activity_summary`，绝不通过 `update_task_status(..., "running", ...)` 回写状态。否则后台 activity flush 会把 `completed/failed` 覆盖回 `running`
6. **SubprocessManager 采用 latest-only、串行化 writer** — 普通 progress 事件只保留窗口内最新值；writer 任务按 1 秒节流周期串行 flush。若 flush 期间又到达新事件，writer 必须自动补跑下一轮，不能丢失窗口后的最新值。**终态 flush 保证由 `dispatch()` 在成功/失败路径里显式执行**：先 cancel delayed flush task，再同步调用 `_flush_latest_activity()`，然后才进入终态落库。这样无论 adapter 的最后一个事件类型是什么（Claude 的 `result` 或 Codex 的 `turn_end`），终态前 flush 都有保证。`_progress_wrapper` 中 `result/error` 的即时 flush 仍保留作为提前 flush 的优化路径。”不阻塞 stdout 主循环”的承诺仅针对 DB I/O（节流 writer）；`on_progress` 回调链本身在 readline 循环中同步 await，这是 design choice——TUI 消费者不做 I/O，延迟可忽略
7. **TUI 双层展示** — (1) 运行态 story 更新 `HeartbeatIndicator`，非运行态 story 更新 `StoryStatusLine`，两者都接收可选 activity 简报字段；(2) 详情页 AgentActivityWidget 通过**独立的 `_refresh_detail_activity()` 路径**更新，不走 `_update_detail_panel()`，不触碰 `_expanded_view`
8. **Task 选择规则** — activity 查询分两层：(1) 优先取 `tasks.status='running' AND tasks.phase=stories.current_phase AND tasks.started_at IS NOT NULL`，以 `started_at DESC, rowid DESC` 选最新一条；(2) 若当前 phase 无 running task，回退取 `tasks.status IN ('completed','failed') AND tasks.phase=stories.current_phase` 的最新终态 task（以 `completed_at DESC, rowid DESC`），确保 StoryStatusLine 能渲染终态 activity 摘要
9. **detail mode 刷新必须定位当前 layout 的详情视图** — three-panel 更新 `#right-top-detail`，tabbed 更新 `#tab-story-detail`；禁止使用无 selector 的 `query_one(StoryDetailView)`
10. **error 事件必须发出** — adapter 在抛出 CLIAdapterError 前，若 on_progress 存在，先发出 `event_type="error"` 的 ProgressEvent；此要求覆盖 timeout、非零退出码和 parse error 三类路径
11. **重试清空 activity** — `dispatch()` 的 `is_retry=True` 路径中 UPDATE 时一并清空 `last_activity_type` 和 `last_activity_summary` 为 NULL
12. **列表增量刷新必须感知 activity 变化** — 不依赖 snapshot 重建来显示 activity；而是给 `HeartbeatIndicator.update_heartbeat()` / `StoryStatusLine.update_data()` 扩展可选 activity 参数，使 `update_content()` 的增量刷新路径也能推送最新 activity

## Implementation Plan

### Tasks

- [x] **Task 1: ProgressEvent 数据模型**
  - File: `src/ato/models/schemas.py`
  - Action:
    - 新增 `ProgressEventType = Literal["init", "text", "tool_use", "tool_result", "turn_end", "result", "error", "other"]`
    - 新增 `ProgressEvent(_StrictBase)` 模型：
      ```python
      class ProgressEvent(_StrictBase):
          event_type: ProgressEventType
          summary: str           # 人类可读摘要，≤100 字符
          cli_tool: Literal["claude", "codex"]
          timestamp: datetime
          raw: dict[str, Any]    # 原始事件数据
      ```
    - 在 `TaskRecord` 中新增可选字段 `last_activity_type: str | None = None` 和 `last_activity_summary: str | None = None`
  - Notes: ProgressEvent 不继承 AdapterResult，是独立的进度通知模型

- [x] **Task 2: BaseAdapter 接口扩展 + stderr drain 工具函数**
  - File: `src/ato/adapters/base.py`
  - Action:
    - 新增类型别名：`ProgressCallback = Callable[[ProgressEvent], Awaitable[None]]`
    - 在 `BaseAdapter.execute()` 抽象方法签名新增：`on_progress: ProgressCallback | None = None`
    - 新增 `drain_stderr()` 异步函数：
      ```python
      async def drain_stderr(stderr: asyncio.StreamReader) -> str:
          """后台消费 stderr 全部内容，防止管道缓冲区满导致死锁。"""
          chunks: list[bytes] = []
          while True:
              chunk = await stderr.read(4096)
              if not chunk:
                  break
              chunks.append(chunk)
          return b"".join(chunks).decode("utf-8", errors="replace")
      ```

- [x] **Task 3: ClaudeAdapter 流式改造**
  - File: `src/ato/adapters/claude_cli.py`
  - Action:
    - **`_build_command()` 修改**：`--output-format json` → `--output-format stream-json`，新增 `--verbose`
    - **新增 `_normalize_claude_event(raw: dict[str, Any]) -> ProgressEvent`**：
      - 匹配 `raw["type"]` 字段（字面值 `"system"`, `"assistant"`, `"result"`, `"user"`, `"rate_limit_event"`）
      - 对 `type="assistant"`：扫描 `raw["message"]["content"]` 数组，按优先级 tool_use > text 取最有信息量的一项生成摘要
      - 映射表：

        | `raw["type"]` | event_type | summary |
        |---------------|-----------|---------|
        | `"system"` | `init` | `"会话初始化 (session={session_id[:8]})"` |
        | `"assistant"` (content 含 tool_use) | `tool_use` | `"调用工具: {name}"` |
        | `"assistant"` (content 含 text, 无 tool_use) | `text` | `text[:100]` |
        | `"assistant"` (content 为空或其他) | `other` | `"assistant"` |
        | `"user"` | `tool_result` | `"工具返回"` |
        | `"result"` | `result` | `"完成 (cost=${total_cost_usd:.4f})"` |
        | `"rate_limit_event"` | `other` | `"rate_limit_event"` |
        | 其他 | `other` | `raw["type"]` |

    - **`execute()` 重写**：
      ```python
      async def execute(self, prompt, options=None, *,
                        on_process_start=None, on_progress=None):
          cmd = self._build_command(prompt, options)
          cwd = (options or {}).get("cwd")
          timeout_seconds = (options or {}).get("timeout", 1800)

          proc = await asyncio.create_subprocess_exec(
              *cmd, stdout=PIPE, stderr=PIPE, cwd=cwd)
          stderr_task = asyncio.create_task(drain_stderr(proc.stderr))
          try:
              if on_process_start:
                  await on_process_start(proc)
              # timeout 覆盖整个子进程生命周期（stdout + stderr + wait）
              async with asyncio.timeout(timeout_seconds):
                  result_data = await self._consume_stream(proc.stdout, on_progress)
                  stderr = await stderr_task
                  await proc.wait()
          except TimeoutError:
              # 发出 error 事件后再 raise
              if on_progress:
                  try: await on_progress(ProgressEvent(
                      event_type="error", summary=f"超时 ({timeout_seconds}s)",
                      cli_tool="claude", timestamp=..., raw={}))
                  except Exception: pass
              await cleanup_process(proc)
              raise CLIAdapterError(...)
          except BaseException:
              await cleanup_process(proc)
              raise
          finally:
              # 确保 stderr_task 不悬挂
              if not stderr_task.done():
                  stderr_task.cancel()
                  with contextlib.suppress(asyncio.CancelledError):
                      await stderr_task

          exit_code = proc.returncode or 0
          if exit_code != 0:
              stderr = stderr_task.result() if stderr_task.done() else ""
              category, retryable = _classify_error(exit_code, stderr)
              # 发出 error 事件
              if on_progress:
                  try: await on_progress(ProgressEvent(
                      event_type="error", summary=f"退出码 {exit_code}: {category.value}",
                      cli_tool="claude", timestamp=..., raw={}))
                  except Exception: pass
              raise CLIAdapterError(...)
          if not result_data:
              if on_progress:
                  try: await on_progress(ProgressEvent(
                      event_type="error", summary="stream-json 未收到 result 事件",
                      cli_tool="claude", timestamp=..., raw={}))
                  except Exception: pass
              raise CLIAdapterError(
                  "stream-json 未收到 result 事件",
                  category=ErrorCategory.PARSE_ERROR, retryable=False)
          return ClaudeOutput.from_json(result_data, exit_code=exit_code)
      ```
    - **`_consume_stream()` 方法**：
      ```python
      async def _consume_stream(self, stdout, on_progress) -> dict[str, Any]:
          result_data: dict[str, Any] = {}
          while True:
              line = await stdout.readline()
              if not line:
                  break
              text = line.decode("utf-8", errors="replace").strip()
              if not text:
                  continue
              try:
                  event = json.loads(text)
              except json.JSONDecodeError:
                  logger.warning("claude_stream_json_parse_skip", line_preview=text[:100])
                  continue
              if event.get("type") == "result":
                  result_data = event
              if on_progress is not None:
                  try:
                      await on_progress(_normalize_claude_event(event))
                  except Exception:
                      logger.warning("progress_callback_error", exc_info=True)
          return result_data
      ```

- [x] **Task 4: CodexAdapter 流式改造**
  - File: `src/ato/adapters/codex_cli.py`
  - Action:
    - **新增 `_normalize_codex_event(raw: dict[str, Any]) -> ProgressEvent`**：

      | `raw["type"]` | event_type | summary |
      |---------------|-----------|---------|
      | `"thread.started"` | `init` | `"会话初始化 (thread={thread_id[:12]})"` |
      | `"turn.started"` | `other` | `"新回合开始"` |
      | `"item.completed"`, item.type=`"agent_message"` | `text` | `item["text"][:100]` |
      | `"item.completed"`, item.type=`"function_call"` | `tool_use` | `"调用函数: {item['name']}"` |
      | `"item.completed"`, item.type=`"function_call_output"` | `tool_result` | `"函数返回"` |
      | `"item.completed"`, item.type=`"command_execution"` | `tool_use` | `"执行命令: {item['call']['command'][:60]}"` |
      | `"item.started"` | `other` | `"item.started"` |
      | `"turn.completed"` | `turn_end` | `"回合结束 (in={input_tokens} out={output_tokens})"` |
      | 其他 | `other` | `raw["type"]` |

    - **`execute()` 重写**：与 Claude 对称结构——`stderr_task` + `asyncio.timeout()` 包裹 `_consume_stream()` + `stderr_task` + `proc.wait()` 整个生命周期 + **finally 中 cancel stderr_task** + 错误路径发 error 事件
    - **`_consume_stream()` 返回 `list[dict[str, Any]]`**（事件列表），后续校验和 `CodexOutput.from_events()` 完全复用现有代码
    - 现有 `_parse_jsonl()` 保留不删除（外部可能引用），但 `execute()` 不再使用

- [x] **Task 5: DB schema 迁移 v8→v9（幂等）**
  - File: `src/ato/models/schemas.py`
    - `SCHEMA_VERSION` 从 8 改为 9
  - File: `src/ato/models/db.py`
    - `_TASKS_DDL` 追加两列：`last_activity_type TEXT` 和 `last_activity_summary TEXT`
    - `update_task_status()` 的 `_field_types` 白名单新增：`"last_activity_type": str` 和 `"last_activity_summary": str`
    - **新增 `update_task_activity()` 辅助函数**：
      ```python
      async def update_task_activity(
          db: aiosqlite.Connection,
          task_id: str,
          *,
          activity_type: str | None,
          activity_summary: str | None,
          commit: bool = True,
      ) -> None:
          """仅更新 tasks.last_activity_* 列，不修改 status。"""
      ```
  - File: `src/ato/models/migrations.py`
    - 新增迁移函数（**使用 `_column_exists()` 幂等检查**，与 v6/v8 一致）：
      ```python
      @_register(9)
      async def _migrate_v8_to_v9(db: aiosqlite.Connection) -> None:
          """v8 → v9: tasks 表新增 last_activity 列（LLM 实时可观测性）。"""
          for col in ("last_activity_type", "last_activity_summary"):
              if not await _column_exists(db, "tasks", col):
                  await db.execute(f"ALTER TABLE tasks ADD COLUMN {col} TEXT")
      ```

- [x] **Task 6: SubprocessManager 节流进度回调 + DB 异步更新**
  - File: `src/ato/subprocess_mgr.py`
  - Action:
    - `dispatch()` 和 `dispatch_with_retry()` 签名新增：`on_progress: ProgressCallback | None = None`
    - **重试路径清空 activity**：`dispatch()` 的 `is_retry=True` 分支中 `update_task_status()` 新增 `last_activity_type=None, last_activity_summary=None`
    - 在 `dispatch()` 内部构造 `_progress_wrapper`，**核心设计：latest-only + 串行化 writer**
      ```python
      _THROTTLE_INTERVAL: float = 1.0
      _last_flush_mono: float = 0.0
      _latest_event: ProgressEvent | None = None
      _delayed_flush_task: asyncio.Task[None] | None = None

      async def _progress_wrapper(event: ProgressEvent) -> None:
          nonlocal _latest_event, _delayed_flush_task
          _latest_event = event
          if event.event_type in ("result", "error"):
              if _delayed_flush_task and not _delayed_flush_task.done():
                  _delayed_flush_task.cancel()
                  with contextlib.suppress(asyncio.CancelledError):
                      await _delayed_flush_task
              await _flush_latest_activity()
          elif _delayed_flush_task is None or _delayed_flush_task.done():
              _delayed_flush_task = asyncio.create_task(_delayed_flush())
          if on_progress is not None:
              await on_progress(event)

      async def _delayed_flush() -> None:
          while _latest_event is not None:
              delay = max(0.0, _THROTTLE_INTERVAL - (time.monotonic() - _last_flush_mono))
              if delay > 0:
                  await asyncio.sleep(delay)
              await _flush_latest_activity()

      async def _flush_latest_activity() -> None:
          nonlocal _latest_event, _last_flush_mono
          event = _latest_event
          if event is None:
              return
          _latest_event = None
          try:
              db_conn = await get_connection(self._db_path)
              try:
                  await update_task_activity(
                      db_conn,
                      task_id,
                      activity_type=event.event_type,
                      activity_summary=event.summary,
                  )
              finally:
                  await db_conn.close()
              _last_flush_mono = time.monotonic()
          except Exception:
              logger.warning("progress_db_write_failed", exc_info=True)
      ```
    - **终态顺序要求**：`dispatch()` 在成功路径（`else:`）和失败路径（`except CLIAdapterError`）中，在调用 `update_task_status()` 之前，必须先 cancel `_delayed_flush_task` 再显式调用 `_flush_latest_activity()`。这确保无论 adapter 的最后一个事件类型是什么（Claude 的 `result` 或 Codex 的 `turn_end`），终态前 flush 都有保证。`_progress_wrapper` 中 `result/error` 的即时 flush 仍保留作为提前 flush 的优化路径
    - **清理要求**：`dispatch()` 的 `finally` 块中若 `_delayed_flush_task` 仍存在，必须 cancel + await，作为兜底防止悬挂后台任务
    - 将 `_progress_wrapper` 作为 `on_progress` 传给 `self._adapter.execute()`

- [x] **Task 7: AgentActivityWidget 新增 TUI 组件**
  - File: `src/ato/tui/widgets/agent_activity.py` (新建)
  - Action:
    - 创建 `AgentActivityWidget(Widget)` 组件：
      ```python
      _ACTIVITY_ICONS: dict[str, str] = {
          "init": "◈", "text": "▸", "tool_use": "⚙",
          "tool_result": "✓", "turn_end": "↻",
          "result": "●", "error": "✗", "other": "·",
      }

      class AgentActivityWidget(Widget):
          """实时 Agent 活动指示器。"""
          DEFAULT_CSS = ""

          def __init__(self, **kwargs: Any) -> None:
              super().__init__(**kwargs)
              self._activity_type: str = ""
              self._activity_summary: str = ""

          def update_activity(self, *, activity_type: str,
                              activity_summary: str) -> None:
              self._activity_type = activity_type
              self._activity_summary = activity_summary
              self.refresh()

          def clear_activity(self) -> None:
              self._activity_type = ""
              self._activity_summary = ""
              self.refresh()

          def render(self) -> Text:
              if not self._activity_summary:
                  return Text("")
              icon = _ACTIVITY_ICONS.get(self._activity_type, "·")
              text = Text()
              text.append(f" {icon} ", style=RICH_COLORS["$accent"])
              text.append(self._activity_summary[:80], style=RICH_COLORS["$text"])
              return text
      ```
  - Notes: 组件不读 SQLite，数据由外层推送

- [x] **Task 8: TUI 集成——双层展示 + detail mode activity 刷新**
  - File: `src/ato/tui/app.py`
    - 在 `_load_data()` 中新增查询（**双层：running 优先，回退终态**）：
      ```sql
      -- 层 1：running task（与 elapsed 查询条件对齐）
      SELECT t.story_id, t.last_activity_type, t.last_activity_summary
      FROM tasks t
      JOIN stories s ON s.story_id = t.story_id
      WHERE t.status = 'running'
        AND t.phase = s.current_phase
        AND t.started_at IS NOT NULL
        AND t.last_activity_summary IS NOT NULL
        AND NOT EXISTS (
          SELECT 1 FROM tasks t2
          WHERE t2.story_id = t.story_id
            AND t2.status = 'running'
            AND t2.phase = t.phase
            AND t2.started_at IS NOT NULL
            AND (t2.started_at > t.started_at
              OR (t2.started_at = t.started_at AND t2.rowid > t.rowid))
        )
      UNION ALL
      -- 层 2：当前 phase 无 running task 时，取最新终态 task
      SELECT t.story_id, t.last_activity_type, t.last_activity_summary
      FROM tasks t
      JOIN stories s ON s.story_id = t.story_id
      WHERE t.status IN ('completed', 'failed')
        AND t.phase = s.current_phase
        AND t.last_activity_summary IS NOT NULL
        AND NOT EXISTS (
          SELECT 1 FROM tasks t3
          WHERE t3.story_id = t.story_id
            AND t3.status = 'running'
            AND t3.phase = s.current_phase
            AND t3.started_at IS NOT NULL
        )
        AND NOT EXISTS (
          SELECT 1 FROM tasks t4
          WHERE t4.story_id = t.story_id
            AND t4.status IN ('completed', 'failed')
            AND t4.phase = t.phase
            AND (t4.completed_at > t.completed_at
              OR (t4.completed_at = t.completed_at AND t4.rowid > t.rowid))
        )
      ```
    - UNION 可能对同一 story 返回多行（running 优先），Python 侧取第一条
    - 结果存入 `self._story_activity: dict[str, tuple[str, str]]`（story_id → (type, summary)）
    - 在 `update_content()` 调用中将 `_story_activity` 传递给 DashboardScreen
  - File: `src/ato/tui/dashboard.py`
    - `update_content()` 接收 `story_activity: dict[str, tuple[str, str]]` 参数，存入 `self._story_activity`
    - **主列表组件都要支持 activity**：
      - `HeartbeatIndicator.update_heartbeat(..., activity_type: str = "", activity_summary: str = "")`
      - `StoryStatusLine.update_data(..., activity_type: str = "", activity_summary: str = "")`
      - `DashboardScreen` 在增量更新路径中也必须把 activity 参数推送给两类组件，不能依赖 snapshot 变化触发重建
    - **新增 `_refresh_detail_activity()` 方法**：
      - 仅在 `_in_detail_mode=True` 时由 `update_content()` 调用
      - 只更新 detail 视图中的 AgentActivityWidget，**不调用 `update_detail()`**，不触碰 `_expanded_view`
      ```python
      def _refresh_detail_activity(self) -> None:
          """detail mode 下仅刷新 agent activity，不重置展开状态。"""
          if not self._in_detail_mode or not self._detail_story_id:
              return
          activity = self._story_activity.get(self._detail_story_id)
          layout = getattr(self.app, "layout_mode", "three-panel")
          selector = "#right-top-detail" if layout == "three-panel" else "#tab-story-detail"
          detail_view = self.query_one(selector, StoryDetailView)
          if activity:
              detail_view.update_activity_only(
                  activity_type=activity[0],
                  activity_summary=activity[1])
          else:
              detail_view.clear_activity_only()
      ```
    - 在 `update_content()` 末尾：`if self._in_detail_mode: self._refresh_detail_activity()`
  - File: `src/ato/tui/story_detail.py`
    - 在 `compose()` 中 `ConvergentLoopProgress` 之后添加 `AgentActivityWidget(id="detail-agent-activity")`
    - **新增 `update_activity_only()` 方法**（不触碰 `_expanded_view`）：
      ```python
      def update_activity_only(self, *, activity_type: str,
                               activity_summary: str) -> None:
          """仅更新 agent activity 指示器，不影响其他 UI 状态。"""
          self.query_one("#detail-agent-activity",
                         AgentActivityWidget).update_activity(
              activity_type=activity_type,
              activity_summary=activity_summary)
      ```
    - **新增 `clear_activity_only()` 方法**：
      ```python
      def clear_activity_only(self) -> None:
          self.query_one("#detail-agent-activity",
                         AgentActivityWidget).clear_activity()
      ```
    - `update_detail()` 中也调用 activity widget（首次加载时），但不新增对 `_expanded_view` 的额外重置
  - File: `src/ato/tui/widgets/heartbeat_indicator.py`
    - `update_heartbeat()` 新增可选 activity 参数
    - `render()` 在不破坏现有布局的前提下附加截断后的 activity 文本（仅当 activity_summary 非空）
  - File: `src/ato/tui/widgets/story_status_line.py`
    - `update_data()` 新增可选 activity 参数
    - `render()` 在现有一行格式末尾附加截断后的 activity 文本（仅当 activity_summary 非空）

- [x] **Task 9: 测试 fixtures + 单元测试**
  - File: `tests/fixtures/claude_stream_success.jsonl` (新建)
    - 保存技术验证中 Claude stream-json 的真实事件流（system → assistant[text] → result）
  - File: `tests/fixtures/claude_stream_tool_use.jsonl` (新建)
    - 含 tool_use + tool_result 的多轮对话流（assistant[tool_use] → user[tool_result] → assistant[text] → result）
  - File: `tests/fixtures/codex_stream_success.jsonl` (新建)
    - 保存 Codex 简单回答事件流（thread.started → turn.started → item.completed[agent_message] → turn.completed）
  - File: `tests/fixtures/codex_stream_tool_use.jsonl` (新建)
    - 含 command_execution + function_call 事件的多轮流（thread.started → item.completed[function_call] → item.completed[function_call_output] → item.completed[command_execution] → item.completed[agent_message] → turn.completed）
  - File: `tests/unit/test_progress_event.py` (新建)
    - `TestNormalizeClaudeEvent`：每种 Claude `type` 值的归一化测试，含 assistant 多 content 项优先级测试
    - `TestNormalizeCodexEvent`：每种 Codex 事件类型的归一化测试，含 command_execution 路径
    - `TestProgressEventModel`：ProgressEvent 创建和验证测试
  - File: `tests/unit/test_claude_adapter.py`
    - **改造 `_mock_process()`**：mock `stdout.readline` 逐行返回 fixture 行（`side_effect` 列表 + 空 bytes 结尾）；mock `stderr.read` 返回空字节
    - **新增 `TestClaudeAdapterStreaming`**：
      - `test_stream_success_with_progress`：on_progress 收到正确数量和类型的 ProgressEvent
      - `test_stream_success_without_progress`：不传 on_progress，ClaudeOutput 字段与改造前一致
    - `test_stream_no_result_event`：stdout 无 result 事件 → PARSE_ERROR
    - `test_stream_parse_error_emits_error_event`：parse error → on_progress 收到 error 事件
    - `test_stream_timeout_cleanup`：超时 → cleanup_process + stderr_task 被 cancel
    - `test_stream_error_emits_error_event`：非零退出码 → on_progress 收到 error 事件
    - **更新现有测试**：适配 readline 模式 mock
  - File: `tests/unit/test_codex_adapter.py`
    - 同上模式，含 command_execution 归一化测试
    - `test_parse_error_emits_error_event`：parse error → on_progress 收到 error 事件
  - File: `tests/unit/test_subprocess_mgr.py`
    - `test_dispatch_with_progress_throttle_keeps_latest`：验证连续 3 个事件 < 1s 内，DB 最终只保留最新事件
    - `test_turn_end_activity_flushed_on_success`：验证 Codex `turn_end` 这类非 `result` 终态前也会被显式 flush
    - `test_dispatch_retry_clears_activity`：重试路径验证 last_activity 被清空
    - `test_activity_writer_does_not_mutate_status`：activity flush 不得把 completed/failed 改回 running
    - `test_dispatch_without_progress_unchanged`：不传 on_progress 行为不变
  - File: `tests/unit/test_migrations.py`
    - 新增 `test_migrate_v8_to_v9`：验证两列存在
    - 新增 `test_migrate_v8_to_v9_idempotent`：重复执行不报错
  - File: `tests/unit/test_db.py`
    - 新增 `test_update_task_status_last_activity`：验证白名单接受新字段
    - 新增 `test_update_task_status_clear_activity`：验证设为 None
    - 新增 `test_update_task_activity_only_updates_activity_columns`：验证新 helper 不改 status
  - File: `tests/unit/test_story_status_line.py`
    - 新增 `test_render_with_activity_summary`
    - 新增 `test_update_data_accepts_activity_fields`
  - File: `tests/unit/test_heartbeat_indicator.py`
    - 新增 `test_render_with_activity_summary`
    - 新增 `test_update_heartbeat_accepts_activity_fields`
  - File: `tests/integration/test_tui_story_detail.py`
    - 新增 `test_detail_activity_refresh_targets_active_detail_view`
    - 新增 `test_detail_activity_refresh_preserves_expanded_subview`
    - 新增 `test_running_story_list_updates_activity_via_heartbeat_indicator`
    - 新增 `test_non_running_story_list_updates_activity_via_story_status_line`

### Acceptance Criteria

- [x] **AC 1**: Given ClaudeAdapter.execute() 被调用且传入 on_progress callback, when Claude CLI 输出 stream-json 事件, then callback 收到 ProgressEvent，event_type 和 summary 按映射表正确归一化
- [x] **AC 2**: Given CodexAdapter.execute() 被调用且传入 on_progress callback, when Codex CLI 输出 JSONL 事件, then callback 收到 ProgressEvent，event_type 和 summary 按映射表正确归一化
- [x] **AC 3**: Given ClaudeAdapter.execute() 被调用且**不传** on_progress, when Claude CLI 完成, then 返回的 ClaudeOutput 与改造前行为一致（字段值相同）
- [x] **AC 4**: Given CodexAdapter.execute() 被调用且**不传** on_progress, when Codex CLI 完成, then 返回的 CodexOutput 与改造前行为一致（字段值相同）
- [x] **AC 5**: Given Claude `assistant` 事件 content 数组同时含 text 和 tool_use, when 归一化, then ProgressEvent.event_type == "tool_use"（tool_use 优先于 text）
- [x] **AC 6**: Given Codex `item.completed` 事件含 item.type == "command_execution", when 归一化, then ProgressEvent.event_type == "tool_use" 且 summary 包含 command 内容
- [x] **AC 7**: Given SubprocessManager.dispatch() 被调用, when adapter 在 < 1s 内连续发出 3 个普通 ProgressEvent, then DB 不要求逐条持久化，但节流窗口结束后 `tasks.last_activity_*` 保存的是最新事件而不是最早事件
- [x] **AC 8**: Given TUI 用户在 detail mode 展开了 findings 子视图, when 2s 轮询到来且 tasks.last_activity 已更新, then AgentActivityWidget 显示最新活动，且 findings 子视图保持展开不被重置
- [x] **AC 9**: Given CLI 进程在 streaming 过程中超时, when timeout 触发, then (1) on_progress 收到 error 事件, (2) cleanup_process 正确调用, (3) stderr_task 被 cancel 无悬挂, (4) CLIAdapterError(TIMEOUT) 被 raise
- [x] **AC 10**: Given CLI 进程同时向 stdout 和 stderr 输出, when stdout 逐行读取, then stderr 被 drain_stderr 并发消费，无管道死锁
- [x] **AC 11**: Given 一个 story 有多个 task（不同 phase + 重试）, when TUI 查询 activity, then 优先展示 `status='running' AND phase=current_phase` 的最新 task 活动（`started_at DESC, rowid DESC`）；若当前 phase 无 running task，则回退展示 `status IN ('completed','failed') AND phase=current_phase` 的最新终态 activity（`completed_at DESC, rowid DESC`）
- [x] **AC 12**: Given dispatch_with_retry 第一次失败后重试, when 第二次 dispatch 开始, then tasks 表 last_activity_type 和 last_activity_summary 被清空为 NULL
- [x] **AC 13**: Given adapter 检测到非零退出码或 parse error, when on_progress 存在, then 在 raise CLIAdapterError 之前先发出 event_type="error" 的 ProgressEvent
- [x] **AC 14**: Given v8→v9 迁移已执行过, when 再次执行迁移, then 不报错（`_column_exists` 幂等检查）
- [x] **AC 15**: Given 普通 progress activity flush 在后台进行, when task 已被标记为 completed/failed, then 后到的 activity flush 不得把 tasks.status 改回 running
- [x] **AC 16**: Given running 状态的 story 出现在主列表, when story_activity 更新, then `HeartbeatIndicator` 渲染最新 activity；Given 非 running 状态的 story, then `StoryStatusLine` 渲染最新 activity
- [x] **AC 17**: Given tabbed 与 three-panel 两种 layout 都挂载了 `StoryDetailView`, when detail mode activity 刷新, then 只更新当前 layout 对应的 detail view

## Additional Context

### Dependencies

- 无新外部依赖——全部基于 asyncio、structlog、textual、pydantic 现有依赖
- Claude CLI ≥2.1.x（支持 `--output-format stream-json`，已验证 2.1.87）
- Codex CLI（`--json` 已验证可用）
- DB schema 迁移 v8→v9（ALTER TABLE，幂等，向前兼容）

### Testing Strategy

- **Snapshot fixtures**：4 个 JSONL 文件覆盖两个 CLI × 两种场景（简单 + 工具调用）
- **归一化纯函数测试**：`_normalize_claude_event()` 和 `_normalize_codex_event()` 覆盖所有事件类型 + assistant 多 content 项优先级
- **Adapter 流式测试**：mock readline 逐行返回 fixture + mock stderr.read，验证 ProgressEvent 数量/类型 + 最终 Output 兼容
- **超时/错误路径**：验证 stderr_task cancel、error 事件发出、cleanup_process 调用，且 parse error 路径也发 error 事件
- **SubprocessManager writer 测试**：验证 latest-only 语义、终态前 flush、后台 activity 写不会回写 status
- **重试清空测试**：验证 is_retry 路径清空 activity 列
- **迁移幂等测试**：验证重复执行不报错
- **DB helper 测试**：验证 update_task_status 接受新字段，且 `update_task_activity()` 仅修改 activity 列
- **TUI 组件测试**：验证 `HeartbeatIndicator` 与 `StoryStatusLine` 都能渲染 activity 文本
- **TUI 集成测试**：验证 detail mode activity 刷新命中当前 layout 对应视图，且不折叠已展开子视图
- **向后兼容回归**：现有 adapter 测试必须继续通过

### Notes

**技术验证数据（2026-03-29）：**
- Claude stream-json 事件 JSON `type` 字段值：`"system"`, `"assistant"`, `"user"`, `"rate_limit_event"`, `"result"`。`subtype` 为独立字段（`"init"`, `"success"` 等），归一化时不使用。
- Codex JSONL 事件 `type` 值：`"thread.started"`, `"turn.started"`, `"item.completed"`, `"item.started"`, `"turn.completed"`
- 事件频率范围广：简单回答 2-3 个事件/10s，工具密集回合可能 10+ 事件/5s。节流（1 次/秒）确保 DB 写入可控。

**已修复的审查 findings（共 16 条）：**
1. (Blocking) detail mode 轮询不刷新 → 双层展示 + `_refresh_detail_activity()` 独立路径
2. (Blocking) 多 task 选择未定义 → 查询条件对齐 `running AND phase=current_phase` + `started_at/rowid` tie-break
3. (High) DB 写在 readline 循环内造成背压 → latest-only 后台 writer
4. (High) 写频率假设脆弱无节流 → 1 次/秒节流 + latest-only 保留最新事件
5. (High) Claude 事件类型合同矛盾 → 明确 `type` 字面值，`system.init` 仅为速记
6. (High) normalize 签名与多 content 行为不一致 → 单返回值，tool_use > text 优先级
7. (High) 迁移缺幂等检查 → 使用 `_column_exists()`
8. (Medium) error 事件从未发出 → adapter 错误路径显式发出 error ProgressEvent
9. (Medium) stderr_task 未在错误路径收尾 → finally 中 cancel + await
10. (Medium) 重试不清空 activity → is_retry 路径清空两列
11. (Medium) update_detail 重置 _expanded_view → `update_activity_only()` 独立方法
12. (Medium) 测试覆盖不足 → 新增 migrations/db/codex-tool-use/TUI 测试
13. (Blocking) 后台 activity 写可能把终态改回 running → 新增 `update_task_activity()`，禁止 activity 写修改 status
14. (High) 节流窗口可能保留最早而不是最新事件 → writer 只 flush 窗口内最新事件
15. (High) 主列表组件选错 → running story 更新 `HeartbeatIndicator`，非 running story 更新 `StoryStatusLine`
16. (High) detail 刷新可能命中错误视图 → 按当前 layout 选择 `#right-top-detail` 或 `#tab-story-detail`
