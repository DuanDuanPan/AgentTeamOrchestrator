# Story 3.2b: Fix Dispatch 与 Artifact 验证

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a 系统,
I want 在 review 发现 blocking findings 后自动调度 fix agent,
So that 质量问题被自动修复。

## Acceptance Criteria

1. **AC1 — Fix Agent 调度**
   ```
   Given 第 N 轮 review 完成，存在 open blocking findings
   When 进入 fix 阶段
   Then 调度 fixer agent（Claude）修复所有 open blocking findings
   And fix prompt 中包含每个 open finding 的 description、file_path、severity
   ```

2. **AC2 — Fix 产出 Artifact 验证**
   ```
   Given fix agent 完成
   When 验证 fix 产出
   Then 确认 worktree 中有新 commit（artifact 存在性验证）
   And structlog 记录 fix 阶段耗时和成本
   ```

## Tasks / Subtasks

- [ ] Task 1: 实现 `run_fix_dispatch()` 方法 (AC: #1, #2)
  - [ ] 1.1 在 `src/ato/convergent_loop.py` 的 `ConvergentLoop` 类中新增 `async run_fix_dispatch(story_id: str, round_num: int, worktree_path: str | None = None) -> ConvergentLoopResult`
  - [ ] 1.2 方法流程：
    - 复用 `_resolve_worktree_path()` 解析 worktree 路径（与 `run_first_review` 一致，不允许退化到仓库根目录）
    - 从 SQLite 查询当前 story 的 open blocking findings（调用 `get_open_findings()`，过滤 `severity=="blocking"`）
    - 若无 open blocking findings → 直接提交 `fix_done`（提前返回，无需 dispatch），返回 `ConvergentLoopResult(converged=False, findings_total=0, ...)`（fix 阶段不判定收敛，交给 re-review）
    - 构建 fix prompt（含每个 finding 的 `file_path`、`severity`、`description`）
    - 记录 worktree HEAD commit hash（artifact 基线）
    - 调度 Claude agent：`subprocess_mgr.dispatch_with_retry(cli_tool="claude", role="developer", phase="fixing")`
    - 验证 worktree HEAD commit hash 已变化（artifact 存在性）
    - 提交 `fix_done` 事件到 TransitionQueue（fixing → reviewing）
    - 返回 `ConvergentLoopResult`（duration/cost/artifact_verified 仅通过 structlog 记录，不扩展模型——由 `AdapterResult` 提供，不需要回传给调用方，调用方通过 cost_log 表查询成本）
    - dispatch 失败时（`CLIAdapterError` 在所有重试后仍抛出）→ 让异常自然冒泡给调用方，不要在 `run_fix_dispatch` 内捕获

- [ ] Task 2: 实现 fix prompt 构建 (AC: #1)
  - [ ] 2.1 创建私有方法 `_build_fix_prompt(findings: list[FindingRecord], worktree_path: str) -> str`
  - [ ] 2.2 prompt 内容：
    - 指定 worktree 路径
    - 列出每个 open blocking finding 的 `file_path`、`severity`、`description`
    - 如果 finding 有 `line_number`，包含行号
    - 指示 agent 修复后 commit 变更

- [ ] Task 3: 实现 artifact 验证（worktree commit 检查）(AC: #2)
  - [ ] 3.1 创建私有方法 `_get_worktree_head(worktree_path: str) -> str | None`
  - [ ] 3.2 使用 `asyncio.create_subprocess_exec("git", "rev-parse", "HEAD", cwd=worktree_path)` + `asyncio.wait_for(proc.communicate(), timeout=5)` 获取当前 HEAD hash；在 `finally` 中调用 `cleanup_process()`（复用 `ato.adapters.base.cleanup_process` 的三阶段清理协议）
  - [ ] 3.3 fix dispatch 前后各调用一次，比较 hash：
    - hash 变化 → artifact 验证通过
    - hash 未变 → artifact 验证失败，记录 `convergent_loop_fix_no_artifact` warning log，仍然提交 `fix_done`（让 re-review 阶段判断是否真的修复了）
    - git 命令失败 / 超时 / `OSError` → `_get_worktree_head()` 返回 `None`；调用方同样记录 warning 并继续，不阻塞 fix 流程

- [ ] Task 4: structlog 结构化日志 (AC: #2)
  - [ ] 4.1 fix 启动：`convergent_loop_fix_start`，字段 `story_id`, `round_num`, `phase="fixing"`, `open_blocking_count`
  - [ ] 4.2 fix 完成：`convergent_loop_fix_complete`，字段 `story_id`, `round_num`, `duration_ms`, `cost_usd`, `artifact_verified`
  - [ ] 4.3 artifact 验证失败或 HEAD 不可读时额外记录：`convergent_loop_fix_no_artifact`，warning 级别，并带 `reason`（如 `head_unchanged` / `git_head_unavailable`）

- [ ] Task 5: 测试 (AC: #1, #2)
  - [ ] 5.1 创建测试类于 `tests/unit/test_convergent_loop.py`（追加到现有文件）：
    - `test_fix_dispatch_with_blocking_findings`——有 blocking findings → 调度 Claude fix agent，提交 fix_done
    - `test_fix_dispatch_prompt_contains_finding_details`——fix prompt 包含 file_path、severity、description
    - `test_fix_dispatch_no_blocking_findings_skips`——无 blocking findings → 不调度 agent，直接提交 fix_done
    - `test_fix_dispatch_artifact_verified`——worktree HEAD hash 变化 → artifact 验证通过
    - `test_fix_dispatch_artifact_not_verified_still_continues`——HEAD hash 未变 → warning log，仍提交 fix_done
    - `test_fix_dispatch_git_head_failure_still_continues`——HEAD 读取失败 / 超时 → warning log，仍提交 fix_done
    - `test_fix_dispatch_requires_worktree_path`——无 worktree_path 时报错 ValueError
    - `test_fix_dispatch_structlog_fields`——验证日志字段 story_id, round_num, duration_ms, cost_usd, artifact_verified
    - `test_fix_dispatch_uses_claude_not_codex`——验证 dispatch 调用 `cli_tool="claude"`
    - `test_fix_dispatch_transition_event`——fix_done 事件 source="agent", submitted_at 已填充
  - [ ] 5.2 mock `_get_worktree_head` 返回不同 hash 模拟 artifact 变化（使用 `unittest.mock.patch.object`）
  - [ ] 5.3 测试直接在调用 `run_fix_dispatch()` 时传入 `round_num` 参数，无需修改 `_make_loop` helper

## Dev Notes

### 架构定位

本 story 扩展 `src/ato/convergent_loop.py` 中已有的 `ConvergentLoop` 类，新增 `run_fix_dispatch()` 方法。这是 Convergent Loop 协议的第二步：
- Story 3.2a（已完成）：首轮全量 review → 发现 findings → 提交 `review_fail` 进入 fixing
- **Story 3.2b（本 story）：接收 open blocking findings → 调度 Claude fix → 验证 artifact → 提交 `fix_done` 回到 reviewing**
- Story 3.2c（后续）：re-review scope narrowing
- Story 3.2d（后续）：收敛判定与终止条件

`run_first_review()` 在 review 阶段结束后已将 story 转换为 `fixing` 状态（via `review_fail` 事件）。本 story 的 `run_fix_dispatch()` 在 `fixing` 状态中执行，完成后提交 `fix_done` 将 story 回到 `reviewing` 状态，交给后续的 re-review 处理。

### 关键设计约束

**Fix Agent 类型：Claude（不是 Codex）**
- 当前规划主流程已明确：`code review + fix = Codex review + Claude fix`
- dispatch 参数：`cli_tool="claude"`, `role="developer"`, `phase="fixing"`
- Codex 在 Convergent Loop 中仅做 review（read-only sandbox），不做 fix
- **MVP 不实现梯度降级**（Claude 未收敛 → Codex 攻坚），那是 Growth Phase（Story 7.1）

**SubprocessManager dispatch 调用：**
- 使用 `dispatch_with_retry()`，与 `run_first_review()` 一致
- Claude 不需要 `sandbox="read-only"`（fix 需要写权限）
- `cwd` 必须指向 worktree
- SubprocessManager 内部自动处理：TaskRecord 创建/更新、PID 注册、CostLogRecord 写入
- AdapterResult 返回后可从中提取 `duration_ms` 和 `cost_usd`

**fix prompt 构建规则：**
- prompt 需包含每个 open blocking finding 的 `file_path`、`severity`、`description`
- 如果 finding 有 `line_number`，一并包含
- 指定 worktree 路径让 agent 知道在哪修改代码
- 指示 agent 修复后 commit 变更（artifact 验证依赖此 commit）
- **不要过度设计 prompt 模板**——简洁可用即可，后续迭代优化

**Artifact 验证（worktree commit 检查）：**
- fix 前记录 worktree HEAD hash → fix 后再读 HEAD hash → 比较
- `_get_worktree_head()` 内部使用 `asyncio.create_subprocess_exec(...)` + `asyncio.wait_for(..., timeout=5)`，并在 `finally` 中调用 `cleanup_process()`
- hash 变化 = 有新 commit = artifact 存在 → 验证通过
- hash 未变 = agent 未 commit → 记录 `convergent_loop_fix_no_artifact` warning，**不阻塞流程**（让 re-review 判断实际修复效果）
- git 命令失败 / 超时 / `OSError` → `_get_worktree_head()` 返回 `None`，调用方记录 `reason="git_head_unavailable"` 的 warning，也不阻塞

**TransitionQueue 事件提交：**
- fix 完成后提交 `TransitionEvent(event_name="fix_done", source="agent", submitted_at=...)`
- `fix_done` 触发状态机 `fixing → reviewing`（transition 已在 `state_machine.py` 定义）
- 如果无 open blocking findings，也提交 `fix_done`（快速回到 reviewing 让 re-review 确认）

**Finding 查询：**
- 使用 `get_open_findings(db, story_id)` 查询 status IN ('open', 'still_open') 的 findings
- 从结果中过滤 `severity == "blocking"` 得到需要修复的 findings
- suggestion 类型的 findings 不需要修复（不阻塞收敛）

**round_num 确定：**
- `run_fix_dispatch()` 需要接收 `round_num` 参数（或从上一轮 review 推算）
- 推荐方案：作为参数传入，由调用方（Orchestrator core.py）管理轮次号
- fix 阶段的 round_num 与其所属的 review 轮次相同（round N review → round N fix → round N+1 re-review）

### 与已有代码的集成点

| 集成目标 | 文件 | 使用方式 |
|---------|------|---------|
| ConvergentLoop（已有类） | `convergent_loop.py` | 在此类中新增 `run_fix_dispatch()` 方法 |
| SubprocessManager.dispatch_with_retry() | `subprocess_mgr.py` | 调度 Claude fix agent |
| get_open_findings() | `models/db.py` | 查询 open/still_open findings |
| get_connection() | `models/db.py` | 获取数据库连接 |
| TransitionQueue.submit() | `transition_queue.py` | 提交 fix_done 事件 |
| TransitionEvent | `models/schemas.py` | 状态转换事件模型 |
| ConvergentLoopResult | `models/schemas.py` | 返回值模型（3.2a 已定义） |
| FindingRecord | `models/schemas.py` | finding 数据模型 |
| _resolve_worktree_path() | `convergent_loop.py` | 复用已有方法解析 worktree 路径 |
| AdapterResult.duration_ms / cost_usd | `models/schemas.py` | 从 dispatch 结果提取耗时和成本 |
| structlog | — | 结构化日志 |

### 不要做的事情

- **不要实现 re-review scope narrowing**——是 Story 3.2c
- **不要实现 max_rounds 终止和 escalation**——是 Story 3.2d
- **不要实现收敛率计算（convergence_threshold）**——是 Story 3.3
- **不要实现梯度降级（Claude → Codex → Interactive）**——是 Story 7.1（Growth Phase）
- **不要修改 state_machine.py**——`fixing → reviewing` via `fix_done` 转换已在 Story 2A.1 定义
- **不要修改 subprocess_mgr.py**——直接使用现有 `dispatch_with_retry()` API
- **不要修改 run_first_review()**——保持 3.2a 的实现不变
- **不要实现多轮循环编排**——本 story 只实现单次 fix dispatch，循环编排由 Orchestrator core.py 或后续 story 负责
- **不要在 prompt 中包含 suggestion findings**——只修复 blocking findings
- **不要在 convergent_loop.py 中导入 core.py**——保持模块隔离
- **不要在 run_fix_dispatch 中捕获 CLIAdapterError**——dispatch_with_retry 内部已处理重试，所有重试失败后让异常冒泡给调用方
- **不要扩展 ConvergentLoopResult 模型**——duration/cost/artifact_verified 仅通过 structlog 记录，调用方通过 cost_log 表查询成本

### 新增依赖

无新增依赖。`asyncio.create_subprocess_exec` 是标准库；git HEAD 读取复用现有 `ato.adapters.base.cleanup_process()` 契约。所有其他依赖（aiosqlite, structlog, pydantic 等）已在 pyproject.toml 中。

### 文件变更清单

| 操作 | 文件路径 | 说明 |
|------|---------|------|
| MODIFY | `src/ato/convergent_loop.py` | +`run_fix_dispatch()` 方法、+`_build_fix_prompt()`、+`_get_worktree_head()` |
| MODIFY | `tests/unit/test_convergent_loop.py` | +10 个 fix dispatch 测试用例（含 git HEAD 读取失败的非阻塞分支） |

### 已有代码模式参考

**SubprocessManager dispatch 调用风格**（fix 使用 Claude，不是 Codex）：
```python
result = await self._subprocess_mgr.dispatch_with_retry(
    story_id=story_id,
    phase="fixing",
    role="developer",
    cli_tool="claude",  # 当前流程约束：fix 使用 Claude，不要用 "codex"
    prompt=fix_prompt,
    options={"cwd": resolved_path},  # 不要加 sandbox — Claude fix 需要写权限
)
# result.duration_ms 和 result.cost_usd 可直接用于 structlog 记录
```

**run_fix_dispatch 返回值规格**（fix 阶段不判定收敛）：
```python
return ConvergentLoopResult(
    story_id=story_id,
    round_num=round_num,
    converged=False,  # fix 阶段永远返回 False，收敛判定由 re-review 负责
    findings_total=len(blocking_findings),  # 本轮传入修复的 blocking 数量
    blocking_count=len(blocking_findings),
    suggestion_count=0,  # fix 只处理 blocking，不涉及 suggestion
    open_count=len(blocking_findings),  # fix 后仍视为 open，等 re-review 确认
)
```

**fix prompt 构建风格**（使用 `FindingRecord.line_number`，不是 `BmadFinding.line`）：
```python
def _build_fix_prompt(
    self,
    findings: list[FindingRecord],
    worktree_path: str,
) -> str:
    lines = [
        f"Fix the following blocking issues in the worktree at {worktree_path}.",
        f"There are {len(findings)} blocking findings to fix.",
        "",
    ]
    for i, f in enumerate(findings, 1):
        loc = f.file_path
        if f.line_number is not None:
            loc += f":{f.line_number}"
        lines.append(f"{i}. [{f.severity}] {loc} — {f.description}")
    lines.append("")
    lines.append("After fixing, commit your changes.")
    return "\n".join(lines)
```

**open findings 查询风格**（参照 `models/db.py`）：
```python
from ato.models.db import get_connection, get_open_findings

db = await get_connection(self._db_path)
try:
    all_open = await get_open_findings(db, story_id)
finally:
    await db.close()

blocking_findings = [f for f in all_open if f.severity == "blocking"]
```

**TransitionEvent 提交风格**（参照 `run_first_review` 的 review_pass/review_fail）：
```python
await self._transition_queue.submit(
    TransitionEvent(
        story_id=story_id,
        event_name="fix_done",
        source="agent",
        submitted_at=datetime.now(tz=UTC),
    )
)
```

**git worktree HEAD hash 获取风格**：
```python
import asyncio
from ato.adapters.base import cleanup_process

async def _get_worktree_head(self, worktree_path: str) -> str | None:
    """获取 worktree 的当前 HEAD commit hash。"""
    proc: asyncio.subprocess.Process | None = None
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "rev-parse", "HEAD",
            cwd=worktree_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        if proc.returncode == 0:
            return stdout.decode().strip()
        return None
    except (OSError, TimeoutError):
        return None
    finally:
        if proc is not None:
            await cleanup_process(proc)
```

**structlog 使用风格**（参照 `convergent_loop.py` 已有）：
```python
logger.info(
    "convergent_loop_fix_start",
    story_id=story_id,
    round_num=round_num,
    phase="fixing",
    open_blocking_count=len(blocking_findings),
)

logger.info(
    "convergent_loop_fix_complete",
    story_id=story_id,
    round_num=round_num,
    duration_ms=result.duration_ms,
    cost_usd=result.cost_usd,
    artifact_verified=head_after != head_before,
)
```

**测试 helper 扩展**（现有 `_make_loop` 已返回 mock 四件套）：
```python
# 现有 helper 直接复用，fix dispatch 测试需要额外 mock _get_worktree_head
# 推荐使用 unittest.mock.patch.object 或在 _make_loop 中增加 worktree_head mock
```

### Project Structure Notes

- `src/ato/convergent_loop.py` 已有 `ConvergentLoop` 类，本 story 在同一文件追加方法
- `tests/unit/test_convergent_loop.py` 已有首轮 review 测试，本 story 追加 fix dispatch 测试
- 无需创建新文件，仅修改现有两个文件
- `run_fix_dispatch()` 与 `run_first_review()` 平级，遵循相同的 async 模式和错误处理风格

### Previous Story Intelligence

**从 Story 3.2a（直接前驱，已完成）的关键经验：**
- `ConvergentLoop.__init__` 接收 7 个依赖注入参数（db_path, subprocess_mgr, bmad_adapter, transition_queue, config, blocking_threshold, nudge）——`run_fix_dispatch()` 可直接使用所有已注入依赖
- `_resolve_worktree_path()` 已实现且验证通过——直接复用
- `_make_loop()` 测试 helper 返回 `(loop, mock_sub, mock_bmad, mock_tq)` 四件套——fix 测试可复用
- `ConvergentLoopResult` 模型已定义（9 字段）——fix dispatch 返回同一模型
- Python 3.11 不支持 `type` 语句（3.12+）——使用 `_BmadAdapter = Any` 赋值风格
- Review R1/R2 的经验：`blocking_threshold` 必须作为必传参数，不能有默认值
- TransitionEvent 的 `source` 字段固定为 `"agent"`（Convergent Loop 内部所有事件）
- `review_fail` 事件已正确提交并触发 `reviewing → fixing` 转换——本 story 的 `fix_done` 完成反向 `fixing → reviewing`

**从 Story 2B.1（Claude Agent Dispatch）的关键经验：**
- Claude 使用 `cli_tool="claude"`，Codex 使用 `cli_tool="codex"`——fix 必须用 `"claude"`
- Claude 不需要 `sandbox` 参数（默认有写权限）
- `AdapterResult` 返回 `duration_ms`（int）和 `cost_usd`（float），直接用于 structlog

**从 Story 2B.4（Worktree Isolation）的关键经验：**
- worktree 路径存储在 `stories.worktree_path` 列
- git 命令需要 `cwd=worktree_path` 来操作正确的 worktree

### Git Intelligence

最近 commit 模式：
- `Merge story 2B.6: Interactive Session 启动与 ato submit 完整实现` (d017ac9)
- `feat: Story 2B.6 Interactive Session 启动与 ato submit 完整实现` (b60d387)
- `feat: Story 3.2a Convergent Loop 首轮全量 Review 完整实现` (6cfa0b8)——本 story 的直接代码前置
- 每个 story 一个 feature commit + 一个 merge commit

### References

- [Source: _bmad-output/planning-artifacts/epics.md — Epic 3, Story 3.2b (line 801-817)]
- [Source: _bmad-output/planning-artifacts/epics.md — Story 3.2c (line 819-841)——后续 story 不要侵入]
- [Source: _bmad-output/planning-artifacts/epics.md — Story 3.2d (line 843-863)——后续 story 不要侵入]
- [Source: _bmad-output/planning-artifacts/architecture.md — 双 CLI 异构 Agent 调用 / Convergent Loop 质量门控（文档导言部分）]
- [Source: _bmad-output/planning-artifacts/architecture.md — Decision 6: 结构化日志]
- [Source: _bmad-output/planning-artifacts/architecture.md — Asyncio Subprocess 模式 — 三阶段清理协议]
- [Source: _bmad-output/planning-artifacts/prd.md — 核心流程：`code review + fix（Codex review + Claude fix, Convergent Loop）`]
- [Source: _bmad-output/planning-artifacts/prd.md — FR6, FR13, FR14, FR27, FR28, NFR9]
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md — ConvergentLoopProgress "fixing" 状态显示]
- [Source: src/ato/convergent_loop.py — ConvergentLoop 类、run_first_review()、_resolve_worktree_path()]
- [Source: src/ato/subprocess_mgr.py — dispatch_with_retry() API]
- [Source: src/ato/models/db.py — get_open_findings(), get_connection()]
- [Source: src/ato/models/schemas.py — FindingRecord, ConvergentLoopResult, TransitionEvent, AdapterResult]
- [Source: src/ato/adapters/base.py — cleanup_process() 三阶段清理协议]
- [Source: src/ato/worktree_mgr.py — _run_git() 的超时 + cleanup_process() 模式]
- [Source: src/ato/state_machine.py — fixing state, fix_done transition (line 149)]
- [Source: src/ato/transition_queue.py — TransitionQueue.submit()]
- [Source: tests/unit/test_convergent_loop.py — 测试 helpers (_make_loop, _make_finding 等)]
- [Source: _bmad-output/implementation-artifacts/3-2a-convergent-loop-full-review.md — Story 3.2a 完成记录]

### Change Log

- 2026-03-25: create-story 创建 — 基于 Epic 3 / PRD / 架构 / Story 3.2a 与 2B.1 / 2B.4 上下文生成 fix dispatch story
- 2026-03-25: validate-create-story 修订 —— 要求 `_get_worktree_head()` 遵循三阶段清理协议并补齐超时边界；补充 git HEAD 读取失败仍继续 `fix_done` 的测试覆盖；移除不存在的 architecture ADR/Decision 引用并清理易漂移的行数/测试总数

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

### File List
