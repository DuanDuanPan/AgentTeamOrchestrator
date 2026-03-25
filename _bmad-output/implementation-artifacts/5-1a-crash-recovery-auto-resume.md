# Story 5.1a: 崩溃恢复自动恢复 (Crash Recovery Auto-Resume)

Status: ready-for-dev

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

- [ ] Task 1: 恢复引擎核心实现 (`recovery.py`) (AC: 1-5, 7)
  - [ ] 1.1 实现 `RecoveryEngine` 类，接收 `db_path`, `subprocess_mgr`, `transition_queue` 依赖
  - [ ] 1.2 实现 `scan_running_tasks()` — 查询所有 `status='running'` 的 tasks
  - [ ] 1.3 实现 `classify_task()` — 对单个 running task 执行四路分类
  - [ ] 1.4 实现 PID 存活检测 `_is_pid_alive(pid)` — 使用 `os.kill(pid, 0)`
  - [ ] 1.5 实现 artifact 存在检测 `_artifact_exists(task)` — 检查 `expected_artifact` 路径
  - [ ] 1.6 实现四种恢复动作：`_reattach()`, `_complete_from_artifact()`, `_reschedule()`, `_mark_needs_human()`
  - [ ] 1.7 实现 `run_recovery()` — 主入口，返回 `RecoveryResult`
  - [ ] 1.8 每个恢复动作用 structlog 记录 `recovery_action` 事件

- [ ] Task 2: 正常重启路径 (`core.py` 修改) (AC: 6)
  - [ ] 2.1 在 `ato start` 启动流程中区分崩溃恢复 vs 正常恢复
  - [ ] 2.2 `status='running'` → 崩溃恢复路径（调用 RecoveryEngine）
  - [ ] 2.3 `status='paused'` → 正常恢复路径（直接重调度）
  - [ ] 2.4 两条路径互斥，基于 task 状态自动判断

- [ ] Task 3: `ato stop` 优雅停止标记 (`core.py` 修改) (AC: 6)
  - [ ] 3.1 `ato stop` 时将所有 `status='running'` 的 task 标记为 `paused`
  - [ ] 3.2 记录停止时间戳到 structlog
  - [ ] 3.3 复用已有 `mark_running_tasks_paused()` DB 函数

- [ ] Task 4: Pydantic 模型 (`models/schemas.py`) (AC: 1-7)
  - [ ] 4.1 新增 `RecoveryAction = Literal["reattach", "complete", "reschedule", "needs_human"]`
  - [ ] 4.2 新增 `RecoveryClassification` 模型 (task_id, story_id, action, reason)
  - [ ] 4.3 新增 `RecoveryResult` 模型 (classifications, auto_recovered_count, needs_human_count, recovery_mode)
  - [ ] 4.4 新增 `RecoveryMode = Literal["crash", "normal", "none"]`

- [ ] Task 5: DB 辅助函数 (`models/db.py`) (AC: 1-5)
  - [ ] 5.1 新增 `get_running_tasks()` — 返回所有 `status='running'` 的 TaskRecord 列表
  - [ ] 5.2 新增 `get_paused_tasks()` — 返回所有 `status='paused'` 的 TaskRecord 列表
  - [ ] 5.3 确认已有 `mark_running_tasks_paused()` 满足 Task 3 需求；如不足则扩展

- [ ] Task 6: 单元测试 (`tests/unit/test_recovery.py`) (AC: 1-7)
  - [ ] 6.1 测试 PID 存活检测（mock `os.kill`）
  - [ ] 6.2 测试 artifact 存在检测（mock `Path.exists`）
  - [ ] 6.3 测试四种分类路径各一（构造 DB 状态 → 调用 classify → 验证动作）
  - [ ] 6.4 测试正常恢复路径（paused tasks → 重调度）
  - [ ] 6.5 测试无恢复场景（无 running/paused tasks → RecoveryMode.none）
  - [ ] 6.6 测试混合场景（部分 running + 部分 paused → 正确分类）

- [ ] Task 7: 集成测试 (`tests/integration/test_crash_recovery.py`) (AC: 1-7)
  - [ ] 7.1 构造"崩溃前数据库状态"（插入 status=running tasks，PID 不存在）→ 调用 recovery → 验证分类
  - [ ] 7.2 四种恢复场景端到端测试（纯数据库状态驱动，不杀真实进程）
  - [ ] 7.3 正常重启路径端到端测试
  - [ ] 7.4 验证 structlog 输出包含 recovery_action 字段

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

### Debug Log References

### Completion Notes List

### File List
