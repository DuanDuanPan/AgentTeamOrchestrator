# Story 5.1a: 崩溃恢复自动恢复 (Crash Recovery Auto-Resume)

Status: review

## Story

As a 操作者,
I want 系统崩溃后运行 `ato start` 自动恢复所有可恢复的任务，不可恢复的任务等待我决策,
So that 意外中断不需要手动重建状态。

## Acceptance Criteria (BDD)

### AC1: SQLite WAL 数据完好

**Given** 系统意外终止后（进程崩溃 / 机器重启）
**When** 操作者运行 `ato start`
**Then** SQLite WAL 自动回放，数据完好（零数据丢失 — NFR6）

### AC2: PID 仍存活 → 自动恢复

**Given** running task 的 PID 仍存活
**When** 恢复引擎检测
**Then** 重新注册监听（re-attach）→ 自动恢复

### AC3: PID 不存活但 artifact 存在 → 自动恢复

**Given** running task 的 PID 不存活但 expected_artifact 存在
**When** 恢复引擎检测
**Then** 继续流水线（从 artifact 恢复 → 触发下一阶段 transition）→ 自动恢复

### AC4: Structured Job 无 artifact → 重新调度

**Given** running task 为 Structured Job，PID 不存活且无 artifact
**When** 恢复引擎检测
**Then** 重新调度该任务 → 自动恢复

### AC5: Interactive Session PID 不存活 → needs_human

**Given** running task 为 Interactive Session，PID 不存活
**When** 恢复引擎检测
**Then** 标记为 `needs_human`，创建 approval 等待操作者决策

### AC6: 正常重启（非崩溃）

**Given** `ato stop` 后正常重启（task 状态为 `paused`）
**When** 运行 `ato start`
**Then** 正常恢复：直接重调度 paused tasks，不触发崩溃恢复逻辑
**And** 启动日志明确输出恢复模式

### AC7: 恢复模式日志

**Given** 系统启动检测到需要恢复的 tasks
**When** 恢复引擎开始工作
**Then** structlog 输出恢复模式：
- 崩溃恢复："检测到 N 个 running task，进入崩溃恢复模式"
- 正常恢复："检测到 N 个 paused task，正常恢复"
- 无恢复：无特殊日志，正常启动

## Tasks / Subtasks

- [x] Task 1: 恢复引擎核心实现 (`recovery.py`) (AC: 1-5, 7)
  - [x] 1.1 实现 `RecoveryEngine` 类，接收 `db_path`, `subprocess_mgr`, `transition_queue` 依赖
  - [x] 1.2 实现 `scan_running_tasks()` — 查询所有 `status='running'` 的 tasks
  - [x] 1.3 实现 `classify_task()` — 对单个 running task 执行四路分类
  - [x] 1.4 实现 PID 存活检测 `_is_pid_alive(pid)` — 使用 `os.kill(pid, 0)`
  - [x] 1.5 实现 artifact 存在检测 `_artifact_exists(task)` — 检查 `expected_artifact` 路径
  - [x] 1.6 实现四种恢复动作：`_reattach()`, `_complete_from_artifact()`, `_reschedule()`, `_mark_needs_human()`
  - [x] 1.7 实现 `run_recovery()` — 主入口，返回 `RecoveryResult`
  - [x] 1.8 每个恢复动作用 structlog 记录 `recovery_action` 事件

- [x] Task 2: 正常重启路径 (`core.py` 修改) (AC: 6)
  - [x] 2.1 在 `ato start` 启动流程中区分崩溃恢复 vs 正常恢复
  - [x] 2.2 `status='running'` → 崩溃恢复路径（调用 RecoveryEngine）
  - [x] 2.3 `status='paused'` → 正常恢复路径（直接重调度）
  - [x] 2.4 两条路径互斥，基于 task 状态自动判断

- [x] Task 3: `ato stop` 优雅停止标记 (`core.py` 修改) (AC: 6)
  - [x] 3.1 `ato stop` 时将所有 `status='running'` 的 task 标记为 `paused`
  - [x] 3.2 记录停止时间戳到 structlog
  - [x] 3.3 复用已有 `mark_running_tasks_paused()` DB 函数

- [x] Task 4: Pydantic 模型 (`models/schemas.py`) (AC: 1-7)
  - [x] 4.1 新增 `RecoveryAction = Literal["reattach", "complete", "reschedule", "needs_human"]`
  - [x] 4.2 新增 `RecoveryClassification` 模型 (task_id, story_id, action, reason)
  - [x] 4.3 新增 `RecoveryResult` 模型 (classifications, auto_recovered_count, needs_human_count, recovery_mode)
  - [x] 4.4 新增 `RecoveryMode = Literal["crash", "normal", "none"]`

- [x] Task 5: DB 辅助函数 (`models/db.py`) (AC: 1-5)
  - [x] 5.1 新增 `get_running_tasks()` — 返回所有 `status='running'` 的 TaskRecord 列表
  - [x] 5.2 新增 `get_paused_tasks()` — 返回所有 `status='paused'` 的 TaskRecord 列表
  - [x] 5.3 确认已有 `mark_running_tasks_paused()` 满足 Task 3 需求；如不足则扩展

- [x] Task 6: 单元测试 (`tests/unit/test_recovery.py`) (AC: 1-7)
  - [x] 6.1 测试 PID 存活检测（mock `os.kill`）
  - [x] 6.2 测试 artifact 存在检测（mock `Path.exists`）
  - [x] 6.3 测试四种分类路径各一（构造 DB 状态 → 调用 classify → 验证动作）
  - [x] 6.4 测试正常恢复路径（paused tasks → 重调度）
  - [x] 6.5 测试无恢复场景（无 running/paused tasks → RecoveryMode.none）
  - [x] 6.6 测试混合场景（部分 running + 部分 paused → 正确分类）

- [x] Task 7: 集成测试 (`tests/integration/test_crash_recovery.py`) (AC: 1-7)
  - [x] 7.1 构造"崩溃前数据库状态"（插入 status=running tasks，PID 不存在）→ 调用 recovery → 验证分类
  - [x] 7.2 四种恢复场景端到端测试（纯数据库状态驱动，不杀真实进程）
  - [x] 7.3 正常重启路径端到端测试
  - [x] 7.4 验证 structlog 输出包含 recovery_action 字段

## Dev Notes

### 核心设计：优雅停止标记法 (Architecture Decision 7)

区分崩溃与正常重启的唯一判据是 task 状态：

| 场景 | task 状态 | `ato start` 行为 |
|------|----------|-----------------|
| `ato stop` 后重启 | `paused` | 正常恢复：直接重调度 |
| 意外崩溃 | `running`（未来得及标记） | 崩溃恢复：PID/artifact 四路分类 |
| SIGKILL | `running`（同崩溃） | 同崩溃恢复路径 |

**无需额外锁文件或标记文件** — task 状态本身就是判据。

### 四路分类算法

```
对每个 status='running' 的 task：
  1. PID 存活？ → reattach（重新注册监听）
  2. PID 不存活 + expected_artifact 存在？ → complete（继续流水线）
  3. PID 不存活 + 无 artifact + task 类型为 Structured Job？ → reschedule（重新调度）
  4. PID 不存活 + task 类型为 Interactive Session？ → needs_human（创建 approval）
```

### 已有基础设施（直接复用，不要重建）

| 组件 | 文件 | 复用点 |
|------|------|--------|
| PID 存活检测 | `core.py` | `is_orchestrator_running()` 用 `os.kill(pid, 0)` — 相同模式 |
| PID 追踪 | `subprocess_mgr.py` | `RunningTask(task_id, story_id, phase, pid, started_at)` 数据类 |
| DB task 操作 | `models/db.py` | `get_tasks_by_story()`, `update_task_status()`, `mark_running_tasks_paused()` |
| 状态机重放 | `transition_queue.py` | `_replay_to_phase()` — 恢复后重建 story 状态机 |
| WAL 配置 | `models/db.py` | `init_db()` 已设置 WAL + busy_timeout + synchronous=NORMAL |
| Approval 创建 | `models/db.py` | `insert_approval()` — 用于 needs_human 场景 |
| TransitionEvent | `models/schemas.py` | 恢复后提交 transition 用 |

### PID 存活检测模式

```python
import os, errno

def _is_pid_alive(pid: int) -> bool:
    """检测 PID 是否仍在运行。macOS/Linux 通用。"""
    try:
        os.kill(pid, 0)  # 信号 0 不杀进程，仅检查存活
        return True
    except OSError as e:
        if e.errno == errno.ESRCH:  # No such process
            return False
        if e.errno == errno.EPERM:  # Permission denied = process exists
            return True
        raise
```

### Artifact 存在检测

task 的 `expected_artifact` 字段存储期望的产出文件路径。检测逻辑：
- `expected_artifact` 为 None/空 → 视为无 artifact
- `Path(expected_artifact).exists()` → artifact 存在

### Interactive Session 判断

区分 Structured Job 与 Interactive Session：检查 `tasks.phase` 字段。Interactive Session 的 phase 包含 `interactive` 或通过 config 中的 `interactive_phases` 列表判断。参考 `core.py` 中 `_check_interactive_timeouts()` 的判断逻辑。

### structlog 日志字段规范

恢复相关的 structlog 必须包含以下字段（Architecture Decision 6）：

```python
logger.info(
    "recovery_task_classified",
    task_id=task.task_id,
    story_id=task.story_id,
    recovery_action="reattach",  # reattach/complete/reschedule/needs_human
    pid=task.pid,
    phase=task.phase,
)

logger.info(
    "recovery_complete",
    recovery_mode="crash",  # crash/normal/none
    auto_recovered=3,
    needs_human=2,
    duration_ms=...,
)
```

### 测试策略：纯数据库状态驱动（Architecture Decision 8）

**不需要真实杀进程。** 测试方法：
1. 构造崩溃前的 DB 状态（INSERT status=running tasks，设置不同的 PID/artifact 组合）
2. Mock `os.kill()` 控制 PID 存活返回值
3. Mock `Path.exists()` 控制 artifact 存在返回值
4. 调用 `RecoveryEngine.run_recovery()` → 验证分类结果

### 依赖注入模式（与 ConvergentLoop 一致）

```python
class RecoveryEngine:
    def __init__(
        self,
        db_path: Path,
        subprocess_mgr: SubprocessManager,
        transition_queue: TransitionQueue,
        nudge: Nudge | None = None,
    ) -> None:
        ...
```

### Project Structure Notes

- `recovery.py` 已存在但为空占位符 — 直接在此文件实现
- 新增 Pydantic 模型加入 `models/schemas.py`（遵循 `_StrictBase` 模式）
- 新增 DB 函数加入 `models/db.py`（遵循参数化查询 + `_dt_to_iso()` / `_iso_to_dt()` 模式）
- 单元测试放 `tests/unit/test_recovery.py`
- 集成测试放 `tests/integration/test_crash_recovery.py`
- **不要创建新模块** — 所有代码分布在已有文件中

### 不要做的事情

- **不要** 实现恢复摘要 CLI 输出 — 那是 Story 5.2 的范围
- **不要** 实现 `ato history` 或 `ato cost report` — Story 5.2 范围
- **不要** 实现恢复性能测试 — Story 5.1b 范围
- **不要** 创建 lock 文件或 marker 文件 — 用 task 状态判断恢复模式
- **不要** 修改 state_machine.py — 状态机已完备
- **不要** 修改 WAL 配置 — 已在 db.py 中正确设置
- **不要** 在 recovery 中直接操作状态机 — 通过 TransitionQueue 提交事件

### References

- [Source: _bmad-output/planning-artifacts/architecture.md — Decision 7: 正常重启 vs 崩溃恢复路径分离]
- [Source: _bmad-output/planning-artifacts/architecture.md — Decision 6: structlog 结构化日志]
- [Source: _bmad-output/planning-artifacts/architecture.md — Decision 8: 状态机测试覆盖]
- [Source: _bmad-output/planning-artifacts/prd.md — FR24, FR25, NFR1, NFR6, NFR7]
- [Source: _bmad-output/planning-artifacts/epics.md — Epic 5, Story 5.1a]
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md — Flow 3: 崩溃恢复]
- [Source: src/ato/core.py — PID 管理: write_pid_file(), read_pid_file(), is_orchestrator_running()]
- [Source: src/ato/subprocess_mgr.py — RunningTask 数据类, dispatch() PID 注册]
- [Source: src/ato/models/db.py — tasks 表 DDL, mark_running_tasks_paused(), WAL 配置]
- [Source: src/ato/transition_queue.py — _replay_to_phase() 恢复重放]
- [Source: src/ato/convergent_loop.py — 依赖注入模式参考]

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6 (1M context)

### Debug Log References

无调试问题。

### Completion Notes List

- 实现 `RecoveryEngine` 核心类（四路分类 + 恢复动作执行），遵循依赖注入模式
- `_is_pid_alive()` 使用 `os.kill(pid, 0)` 检测进程存活，处理 ESRCH/EPERM
- `_artifact_exists()` 检测 `expected_artifact` 路径文件存在
- 四种恢复动作及 Fix：
  - reattach: 注册 PID 到 SubprocessManager + 启动异步 PID 监控 asyncio task（Fix F2: 不依赖 SubprocessManager 可用性）
  - complete: 标记 completed + 提交 transition event 推进 story 到下一阶段（Fix F1: 通过 _PHASE_SUCCESS_EVENT 映射）
  - reschedule: 重置为 pending + nudge 唤醒 Orchestrator
  - needs_human: 标记 failed（非 paused）+ 创建 crash_recovery approval（Fix F3: 防止下次启动误恢复）
- Normal recovery 路径过滤掉有 pending crash_recovery approval 的 paused task（Fix F3）
- `_detect_recovery_mode()` 从仅日志检测升级为实际调用 `RecoveryEngine.run_recovery()`
- Task 3 已由已有 `_shutdown()` 实现（`mark_running_tasks_paused()` + structlog），仅增强了 `stopped_at` 时间戳字段
- 新增 Pydantic 模型：`RecoveryAction`, `RecoveryMode`, `RecoveryClassification`, `RecoveryResult`
- 新增 DB 辅助函数：`get_running_tasks()`, `get_paused_tasks()`（包装 `get_tasks_by_status()`）
- 25 个单元测试 + 12 个集成测试，全量 832 测试通过，零回归
- structlog 输出符合 Architecture Decision 6 规范（recovery_task_classified, recovery_complete 事件）

### File List

- `src/ato/recovery.py` — RecoveryEngine 核心实现（新内容，替换空占位符）
- `src/ato/models/schemas.py` — 新增 RecoveryAction, RecoveryMode, RecoveryClassification, RecoveryResult
- `src/ato/models/db.py` — 新增 get_running_tasks(), get_paused_tasks()
- `src/ato/core.py` — _detect_recovery_mode() 集成 RecoveryEngine; _shutdown() 增加 stopped_at; 移除未使用的 count_tasks_by_status 导入
- `tests/unit/test_recovery.py` — 21 个单元测试（新文件）
- `tests/integration/test_crash_recovery.py` — 9 个集成测试（新文件）
- `tests/unit/test_core.py` — 更新 2 个恢复检测测试以适配 RecoveryEngine 集成

## Change Log

- 2026-03-25: Story 5.1a 完整实现 — 崩溃恢复自动恢复引擎（RecoveryEngine 四路分类 + 正常/崩溃恢复双路径 + 30 个测试）
- 2026-03-25: Code Review R1 修复 3 个高危 finding：
  - F1: complete 恢复后提交 transition event 推进 story（AC3 违反修复）
  - F2: reattach 启动独立 asyncio PID 监控，不依赖 SubprocessManager（AC2 违反修复）
  - F3: needs_human 使用 failed 状态 + normal recovery 过滤 crash_recovery approval（AC5 违反修复）
  - 新增 5 个测试覆盖上述场景（含二次启动回归测试）
- 2026-03-25: Code Review R2 修复 3 个 finding（2 高危 + 1 中危）：
  - F1(reschedule闭环): _reschedule 后台创建 adapter + SubprocessManager re-dispatch task，完成后提交 transition event（AC4/AC6 闭环）
  - F2(原子性): _mark_needs_human 用 SAVEPOINT 包裹 task UPDATE + approval INSERT，全有或全无（恢复中再崩溃不丢任务）
  - F3(缺失phase): _PHASE_SUCCESS_EVENT 补充 dev_ready→start_dev 和 fixing→fix_done
  - 新增 3 个测试：reschedule dispatch+transition / needs_human 原子性故障注入 / dev_ready+fixing phase 完成事件
- 2026-03-25: Code Review R3 修复 2 个 finding（1 高危 + 1 高危）：
  - F1(质量门控绕过): _reschedule 按 phase 类型分流——convergent_loop 不 raw dispatch（只 pending+nudge，等 ConvergentLoop 接管）；structured_job 后台 dispatch+transition
  - F2(执行上下文丢失): _dispatch_recovery_task 读取 story.worktree_path 传入 options.cwd，codex 任务传 sandbox=workspace-write
  - 新增 convergent_loop_phases 参数区分 phase 类型；Orchestrator 从 config 构建并传入
  - 新增 4 个测试：convergent_loop 不 dispatch / structured_job dispatch+transition / convergent_loop 端到端 / dispatch options 验证
- 2026-03-25: Code Review R5 修复 2 个 finding（1 高危 + 1 中危）：
  - F1(convergent_loop语义错误): 不再调用 ConvergentLoop.run_first_review()（硬编码 reviewing 语义），改为 phase-aware 自建流程：_PHASE_FAIL_EVENT + _PHASE_BMAD_SKILL 映射，按 phase 使用正确的 role/event/skill dispatch→parse→evaluate→transition
  - F2(计数误报): 新增 RecoveryResult.dispatched_count 字段，reschedule 计入 dispatched（结果待定），只有 reattach/complete 计入 auto_recovered（同步完成）
  - 新增 3 个 phase-aware 测试：reviewing→review_pass / validating→validate_pass / qa_testing→qa_fail（blocking findings）
- 2026-03-25: Code Review R6 修复 2 个 finding（1 高危 + 1 中危）：
  - F1(通用prompt致parse_failed): 新增 _CONVERGENT_LOOP_PROMPTS 模板，每个 phase 输出指令匹配 BMAD 解析器期望（validation: 结果/摘要/关键问题; qa: Recommendation/Quality Score/Critical Issues）
  - F2(后台异常丢失task): _dispatch_structured_job 和 _dispatch_convergent_loop 的 except 兜底调用 _mark_dispatch_failed → 原子标记 failed + 创建 approval
  - 新增 3 个测试：validating prompt 标记验证 / qa prompt 标记验证 / dispatch RuntimeError 兜底（task→failed+approval）
