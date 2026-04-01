---
title: 'Planning 阶段跨 Story 并行调度（Main-Path Parallel Dispatch）'
slug: 'planning-phase-parallel-dispatch'
created: '2026-03-31'
status: 'complete-with-known-gaps'
stepsCompleted: ['task-1', 'task-2', 'task-3', 'task-4', 'task-5', 'task-6', 'task-7', 'task-8']
tech_stack: ['python>=3.11', 'asyncio', 'aiosqlite', 'pydantic>=2.0', 'structlog']
files_to_modify: ['src/ato/core.py', 'src/ato/config.py', 'src/ato/recovery.py', 'src/ato/transition_queue.py', 'src/ato/merge_queue.py', 'ato.yaml.example', 'tests/unit/test_core.py', 'tests/unit/test_recovery.py', 'tests/unit/test_transition_queue.py', 'tests/unit/test_merge_queue.py', 'tests/unit/test_config.py', 'tests/integration/test_config_workflow.py', 'tests/integration/test_tui_pilot.py', 'tests/integration/test_orchestrator_lifecycle.py']
code_patterns: ['asyncio.Condition 共享-独占门控', 'PhaseConfig → PhaseDefinition → _resolve_phase_config_static 字段传播', 'configure_main_path_gate 启动期就地配置']
test_patterns: ['pytest-asyncio', 'asyncio.gather 并发竞争验证', 'reset_main_path_gate 测试辅助函数', 'asyncio.wait_for 超时断言']
---

# Tech-Spec: Planning 阶段跨 Story 并行调度

**Created:** 2026-03-31

## Overview

### Problem Statement

当前所有 `workspace: main` 的 planning 阶段（creating、designing、validating）以及 main 分支上的独占操作（batch_spec_commit、merging、regression）都被同一个 `_main_path_limiter = asyncio.Semaphore(1)` 串行化。结果是多个 story 即使写入各自隔离的 planning 工件路径，也只能一个接一个运行：

```text
Story A creating -> A designing -> A validating -> Story B creating -> B designing -> ...
```

这在 3-5 个 story 的 batch 中会引入明显的排队时间。

补充说明：`dev_ready` 虽配置为 `workspace: main`，但实际在 dispatch 入口处直接 reconcile 并返回，不进入 limiter 保护的 LLM dispatch 路径。

### Solution

将单一 `Semaphore(1)` 替换为 **共享-独占门控（MainPathGate）**：

- 共享模式：供 `parallel_safe: true` 的 planning 阶段使用，允许最多 `max_planning_concurrent` 个跨 story 并发持有者。
- 独占模式：供 batch spec commit、merge、regression 使用，等待所有共享持有者释放后独占，并在等待期间阻止新的共享获取。

目标行为：

```text
Story A creating ─┐
Story B creating ─┼─ 同时运行（受 max_planning_concurrent 约束）
Story C designing ┘
                    -> batch_spec_commit / merge / regression 等待全部完成后独占执行
```

## Scope

**In Scope:**

- 新增 `parallel_safe` 配置并打通 `PhaseConfig -> PhaseDefinition -> _resolve_phase_config_static()` 传播链
- 引入 `MainPathGate`，替换 `_main_path_limiter`
- 适配全部 8 个生产代码调用点
- 更新相关测试与配置模板
- 为 `max_planning_concurrent` 增加配置校验

**Out of Scope:**

- 单 story 内部阶段并行化
- 状态机拓扑变更
- 数据模型 / DB schema 改动

**前提约束（Precondition — 非本 spec 实现但必须保证）：**

Planning agent 在 `workspace: main` 上运行时 **不得执行 git 写操作**（add、commit、rebase、checkout 等）。当前仅通过 prompt 约束实现，这不是可执行保证。如果此前提被违反，MainPathGate 的共享模式假设（多个 planning 并发读写各自隔离路径）将失效，后果不是"性能退化"而是仓库状态污染。

**fail-closed 要求：** 后续版本必须通过以下至少一种机制将此前提升级为可执行约束：

1. **命令级 denylist**：CLI adapter 在 planning 阶段拦截 `git add/commit/rebase/checkout/reset` 等写操作
2. **主工作区变更快照**：dispatch 前记录 `git status --porcelain` + HEAD sha，完成后比较，不一致则 fail + 告警
3. **planning 改用 worktree 执行**：彻底消除对 main workspace 的共享假设

在上述可执行约束落地前，本 spec 的并行安全性存在已知缺口，实施者须知晓此风险。

## Context for Development

### 为什么 Planning Phase 可以跨 Story 并行

| Phase | 产出路径 | 行为 | Story 隔离? |
|-------|---------|------|------------|
| creating | `_bmad-output/implementation-artifacts/{story_id}.md` | 写 story spec | ✅ story-scoped |
| designing | `_bmad-output/implementation-artifacts/{story_id}-ux/*` | 写 UX 工件 | ✅ story-scoped |
| validating | `{story_id}.md` + `{story_id}-validation-report.md` | 读写本 story 的工件 | ✅ story-scoped |
| batch_spec_commit | git add + git commit | 修改共享 git index | ❌ |
| merging | git rebase / merge | 修改共享 git index | ❌ |
| regression | 运行共享测试环境 | 使用共享仓库状态 | ❌ |

关键点：

- validating 不是只读阶段。prompt 明确允许直接修 story spec，并写 validation report。
- 但 validating 的写入仍然是 story-scoped，因此不同 story 的 validating 可以彼此并行。
- batch spec commit、merge、regression 都必须保持对 main workspace 的独占。

### 当前并发控制现状

`SubprocessManager._semaphore` 不是全局并发阀门，而是实例级 semaphore。各条 dispatch 路径都会新建 `SubprocessManager`，因此不能依赖它来限制跨 dispatch 的总并发。

当前真正阻止 planning 跨 story 并行的唯一机制就是 `_main_path_limiter = Semaphore(1)`。

### 完整调用点清单

**条件获取：只有 `workspace == "main"` 时进入 gate。**

| # | 文件 | 函数 | 场景 |
|---|------|------|------|
| 1 | `src/ato/core.py` | `_dispatch_batch_restart` | structured_job restart dispatch |
| 2 | `src/ato/recovery.py` | `_dispatch_convergent_loop` | convergent_loop recovery |
| 3 | `src/ato/recovery.py` | `_dispatch_structured_job_recovery` | structured_job recovery |

**无条件独占：始终独占 main workspace。**

| # | 文件 | 函数 | 场景 |
|---|------|------|------|
| 4 | `src/ato/core.py` | `_handle_spec_batch_precommit` | spec batch retry |
| 5 | `src/ato/transition_queue.py` | `_on_enter_dev_ready` | batch spec commit |
| 6 | `src/ato/merge_queue.py` | `_run_regression_via_codex` | regression execution |
| 7 | `src/ato/merge_queue.py` | `_run_merge_worker` / `_execute_merge` | merge to main（`checkout main` + `git merge --ff-only`，经由 `worktree_mgr.merge_to_main()`） |
| 8 | `src/ato/core.py` | `_handle_approval_decision` | regression_failure → revert（经由 `worktree_mgr.revert_merge_range()`） |

**⚠️ 注意：** 调用点 #7 和 #8 在本 spec 首次编写时被遗漏。`_execute_merge()` 执行 `git checkout main` + `git merge --ff-only`，`revert_merge_range()` 执行 `git revert`，两者都直接修改 main 仓库状态，必须在 gate 独占保护下执行。如果遗漏，AC#2 的互斥保证不成立。

### 配置字段传播链

```text
ato.yaml
  -> PhaseConfig.parallel_safe
  -> build_phase_definitions()
  -> PhaseDefinition.parallel_safe
  -> RecoveryEngine._resolve_phase_config_static()
  -> phase_cfg["parallel_safe"]
  -> runtime gate mode selection
```

如果 `settings is None`，`_resolve_phase_config_static()` 返回 `{}`，运行时 `phase_cfg.get("parallel_safe", False)` 会保守地回退到独占模式。

## Design

### MainPathGate API

`MainPathGate` 是模块级单例对象，内部基于 `asyncio.Condition` 实现。

设计约束：

- 不再保留 `Semaphore` 风格的伪兼容接口。
- `release_*()` 使用真正的异步释放，不用 `call_soon()` 或后台 task。
- 不允许在系统运行中通过“替换单例对象”切换 gate 配置，避免 split-brain。

伪代码：

```python
class MainPathGate:
    def __init__(self, max_shared: int = 1) -> None:
        if max_shared < 1:
            raise ValueError("max_shared must be >= 1")
        self._max_shared = max_shared
        self._shared_holders = 0
        self._shared_waiters = 0
        self._exclusive_held = False
        self._exclusive_waiters = 0
        self._cond = asyncio.Condition()

    def configure(self, max_shared: int) -> None:
        if max_shared < 1:
            raise ValueError("max_shared must be >= 1")
        if (
            self._shared_holders > 0
            or self._exclusive_held
            or self._shared_waiters > 0
            or self._exclusive_waiters > 0
        ):
            raise RuntimeError("cannot reconfigure a busy MainPathGate")
        self._max_shared = max_shared

    async def acquire_shared(self) -> None:
        async with self._cond:
            self._shared_waiters += 1
            try:
                while (
                    self._exclusive_held
                    or self._exclusive_waiters > 0
                    or self._shared_holders >= self._max_shared
                ):
                    await self._cond.wait()
                self._shared_holders += 1
            finally:
                self._shared_waiters -= 1

    async def release_shared(self) -> None:
        async with self._cond:
            if self._shared_holders < 1:
                raise RuntimeError("release_shared without holder")
            self._shared_holders -= 1
            self._cond.notify_all()

    async def acquire_exclusive(self) -> None:
        async with self._cond:
            self._exclusive_waiters += 1
            try:
                while self._exclusive_held or self._shared_holders > 0:
                    await self._cond.wait()
                self._exclusive_held = True
            finally:
                self._exclusive_waiters -= 1

    async def release_exclusive(self) -> None:
        async with self._cond:
            if not self._exclusive_held:
                raise RuntimeError("release_exclusive without holder")
            self._exclusive_held = False
            self._cond.notify_all()

    @contextlib.asynccontextmanager
    async def shared(self):
        await self.acquire_shared()
        try:
            yield
        finally:
            await self.release_shared()

    @contextlib.asynccontextmanager
    async def exclusive(self):
        await self.acquire_exclusive()
        try:
            yield
        finally:
            await self.release_exclusive()
```

### Singleton Lifecycle

使用单个长期存在的 gate 实例，不做懒替换：

```python
_main_path_gate = MainPathGate(max_shared=1)

def get_main_path_gate() -> MainPathGate:
    return _main_path_gate

def configure_main_path_gate(max_shared: int) -> MainPathGate:
    _main_path_gate.configure(max_shared)
    return _main_path_gate

def reset_main_path_gate(max_shared: int = 1) -> None:
    global _main_path_gate
    _main_path_gate = MainPathGate(max_shared=max_shared)
```

含义：

- 模块导入后永远只有一个 gate 实例。
- 启动期通过 `configure_main_path_gate()` 就地更新 `max_shared`。
- 测试通过 `reset_main_path_gate()` 在测试边界重建 gate。
- 不存在“先拿到 fallback gate，后面又被 init 替换”的 split-brain 风险。

### Fairness Policy

采用 **写优先**：

- 一旦有独占等待者，新共享请求会被阻塞。
- 这样可以保证 batch spec commit、merge、regression 不会被连续 planning 永久饿死。

这不是完全公平的 FIFO 锁。理论上如果独占请求持续涌入，planning 可能被延迟；这里接受这个 trade-off，因为独占操作在系统中是稀疏且有界的。

## Technical Decisions

1. 使用共享-独占门控，而不是两个独立 semaphore。两个独立 semaphore 无法表达“planning 与 git/merge/regression 互斥”。
2. `release_*()` 明确为 async API。这样语义和状态更新是即时的，不引入 `call_soon()` 带来的隐藏任务和时序差异。
3. `MainPathGate` 不提供 `Semaphore` 风格兼容别名。此次改动是仓库内部重构，应一次性把调用点和测试迁移到 gate API，而不是保留误导性的旧名字。
4. gate 单例不做惰性替换，只允许启动前就地配置，避免 split-brain。
5. 新增 `ATOSettings.max_planning_concurrent: int = 3`，并要求校验 `>= 1`。
6. `parallel_safe` 默认 `False`，保持后向兼容和 `settings=None` 时的保守回退。
7. 当 `max_planning_concurrent > max_concurrent_agents` 时发出配置警告（`config_planning_exceeds_agents`）。**注意：** SubprocessManager 是实例级的（每次 dispatch 新建），其 `_semaphore` 不构成跨 dispatch 的全局上限（见"当前并发控制现状"章节）。因此 `max_concurrent_agents` 只限制单个 dispatch 内的子进程并发，不限制 MainPathGate 放行的跨 dispatch 总并发。警告的意义在于提示操作者：并发 planning dispatch 数量超过单 dispatch 子进程上限时，系统总资源消耗可能超出预期。**代码中的 warning 文案（`config.py`）须同步修正**，移除"超出部分将在 SubprocessManager 层排队"的错误表述。
8. 当 `parallel_safe: true` 出现在非 `workspace: main` 的 phase 上时发出配置警告，因为该标志仅影响 MainPathGate 的获取模式，对 worktree 执行的 phase 无意义。

## Implementation Plan

### Task 1: 扩展配置模型与校验

- Files: `src/ato/config.py`, `ato.yaml.example`
- Action:
  - `PhaseConfig` 新增 `parallel_safe: bool = False`
  - `PhaseDefinition` 新增 `parallel_safe: bool = False`
  - `ATOSettings` 新增 `max_planning_concurrent: int = 3`
  - 在配置校验逻辑中新增 `max_planning_concurrent >= 1`
  - `ato.yaml.example` 为 creating/designing/validating 增加 `parallel_safe: true`
  - `ato.yaml.example` 增加 `max_planning_concurrent: 3`

示例：

```python
class PhaseConfig(BaseModel):
    # ...
    parallel_safe: bool = False

@dataclass(frozen=True)
class PhaseDefinition:
    # ...
    parallel_safe: bool = False

class ATOSettings(BaseSettings):
    # ...
    max_planning_concurrent: int = 3

if config.max_planning_concurrent < 1:
    raise ConfigError("配置错误：max_planning_concurrent 必须 >= 1")
```

### Task 2: 传播 `parallel_safe`

- File: `src/ato/config.py`, `src/ato/recovery.py`
- Action:
  - `build_phase_definitions()` 构造 `PhaseDefinition` 时传入 `parallel_safe=phase.parallel_safe`
  - `_resolve_phase_config_static()` 返回 dict 时增加 `"parallel_safe": pd.parallel_safe`

### Task 3: 引入 MainPathGate

- File: `src/ato/core.py`
- Action:
  - 删除 `_main_path_limiter`、`get_main_path_limiter()`、`reset_main_path_limiter()`
  - 新增 `MainPathGate`
  - 新增 `get_main_path_gate()`、`configure_main_path_gate()`、`reset_main_path_gate()`

说明：

- 不再保留 limiter 别名函数。
- `release_*()` 必须是 async。
- `reset_main_path_gate()` 仅用于测试。

### Task 4: 启动期配置 gate

- File: `src/ato/core.py`
- Action:
  - 在 `Orchestrator._startup()` 的早期调用 `configure_main_path_gate(self._settings.max_planning_concurrent)`
  - 调用位置应早于 `TransitionQueue`、`MergeQueue`、recovery dispatch 参与运行

约束：

- 生产代码不应依赖“未初始化 fallback”
- 独立测试如果需要非默认 `max_shared`，在测试 setup 中显式调用 `reset_main_path_gate(n)`

### Task 5: 条件调用点适配

- Files: `src/ato/core.py`, `src/ato/recovery.py`
- Action:
  - 根据 `workspace == "main"` 决定是否进入 gate
  - 根据 `phase_cfg.get("parallel_safe", False)` 选择共享或独占模式

模式：

```python
gate = get_main_path_gate() if workspace == "main" else None
is_shared = bool(phase_cfg.get("parallel_safe", False))

if gate is not None:
    if is_shared:
        await gate.acquire_shared()
    else:
        await gate.acquire_exclusive()
try:
    ...
finally:
    if gate is not None:
        if is_shared:
            await gate.release_shared()
        else:
            await gate.release_exclusive()
```

涉及调用点：

- `src/ato/core.py::_dispatch_batch_restart`
- `src/ato/recovery.py::_dispatch_convergent_loop`
- `src/ato/recovery.py::_dispatch_structured_job_recovery`

### Task 6: 无条件独占调用点适配

- Files: `src/ato/core.py`, `src/ato/transition_queue.py`, `src/ato/merge_queue.py`
- Action:
  - batch spec retry / batch spec commit / regression / **merge to main / revert** 全部改为 gate 独占模式

推荐写法：

```python
gate = get_main_path_gate()
async with gate.exclusive():
    ...
```

涉及调用点：

- `src/ato/core.py::_handle_spec_batch_precommit`
- `src/ato/transition_queue.py::_on_enter_dev_ready`
- `src/ato/merge_queue.py::_run_regression_via_codex`
- `src/ato/merge_queue.py::_run_merge_worker` — gate.exclusive() 应包裹整个 `_execute_merge()` 调用，因为 `merge_to_main()` 执行 `git checkout main` + `git merge --ff-only`
- `src/ato/core.py::_handle_approval_decision` — 当 `atype == "regression_failure"` 且 `decision == "revert"` 时，`revert_merge_range()` 执行 `git revert`，必须在 gate.exclusive() 内

**注意 gate 嵌套：** `_run_regression_via_codex` 已在内部获取 gate.exclusive()。当 `_run_merge_worker` 获取 gate 后调用 regression 时，regression 内部的 gate 获取会死锁。解决方式：将 merge worker 的 gate 范围设为仅覆盖 `_execute_merge()`，regression 保持自己的独立 gate 获取。

### Task 7: 测试适配

- Files: `tests/unit/test_core.py`, `tests/unit/test_recovery.py`, `tests/unit/test_transition_queue.py`, `tests/unit/test_merge_queue.py`
- Action:
  - 所有导入改为 `get_main_path_gate` / `reset_main_path_gate`
  - 所有 `limiter.acquire()` / `limiter.release()` 迁移到 gate API
  - 所有 `async with limiter:` 改为 `async with gate.exclusive():`
  - 删除对 `locked()` 的依赖，改为测试内本地 `acquired` 标志或直接使用 context manager

特别说明：

- 现有 `test_transition_queue.py` 和 `test_recovery.py` 的 cleanup 使用 `limiter.locked()`，这里必须一起重写。
- 不要保留“limiter”命名，以免把 `Semaphore` 语义误带入 gate。

### Task 8: 新增并发与配置测试

- Files: `tests/unit/test_core.py`, `tests/unit/test_config.py`, `tests/integration/test_config_workflow.py`, `tests/integration/test_tui_pilot.py`, `tests/integration/test_orchestrator_lifecycle.py`
- Action:
  - gate 并发语义测试
  - `parallel_safe` 配置传播测试
  - `max_planning_concurrent` 校验测试
  - YAML -> settings -> phase definitions -> phase_cfg 的 round-trip 测试
  - **TUI/CLI 回归验证**（对应 AC#10）：在 `test_tui_pilot.py` 中新增或扩展测试，验证并行 planning 场景下 dashboard 的等待态、审批可见性和恢复态展示不回归；在现有 CLI runner 测试中验证配置变更后的命令行输出一致性

建议新增测试：

**Gate 并发语义（必测）：**

- `test_shared_mode_allows_concurrent`
- `test_shared_mode_respects_max_cap` — 饱和测试：N > max_shared 个任务并发，验证同时持有数不超过 max_shared（如 5 任务 / max_shared=3 → 同时运行最多 3）
- `test_exclusive_blocked_by_shared`
- `test_shared_blocked_by_exclusive`
- `test_shared_blocked_by_waiting_exclusive`
- `test_exclusive_mutual_exclusion`
- `test_gate_context_managers`
- `test_busy_gate_rejects_reconfigure` — 当 gate 有持有者或等待者时，`configure()` 必须抛出 `RuntimeError`（对应 AC#7）

**配置传播（必测）：**

- `test_parallel_safe_field_in_phase_config`
- `test_parallel_safe_default_false`
- `test_parallel_safe_propagated_to_phase_definition`
- `test_max_planning_concurrent_in_settings`
- `test_invalid_max_planning_concurrent_rejected`
- `test_parallel_safe_round_trip`

**交叉校验（必测，对应 Review F-02/F-03）：**

- `test_max_planning_concurrent_exceeds_agents_warns` — `max_planning_concurrent > max_concurrent_agents` 时发出 `config_planning_exceeds_agents` 级别警告
- `test_parallel_safe_on_non_main_workspace_warns` — `parallel_safe: true` 配置在非 main workspace 的 phase 上时发出警告

## Acceptance Criteria

1. `parallel_safe: true` 的 planning phases 可以跨 story 并行，最大并发数受 `max_planning_concurrent` 控制。
2. planning 与 batch spec commit / merging / regression 互斥，不会同时占用 main workspace。
3. 独占操作之间互斥，保持原有 serial 行为。
4. 一旦有独占等待者，新共享请求会阻塞，避免独占长期饥饿。
5. `parallel_safe` 能完整经过 `PhaseConfig -> PhaseDefinition -> _resolve_phase_config_static() -> phase_cfg` 传播。
6. `max_planning_concurrent < 1` 会在配置加载阶段被拒绝。
7. `MainPathGate` 不会在运行中被新的单例对象替换。
8. `release_*()` 为 async，状态更新即时生效，不需要 `asyncio.sleep(0)` 或后台 task 才能解锁后继 waiter。
9. `uv run pytest tests/unit/ tests/integration/` 无回归。
10. 涉及 approval 队列可见性、recovery 恢复态、convergent-loop 状态的变更须通过 TUI pilot 测试（`tests/integration/test_tui_pilot.py`）和 CLI runner 测试（`typer.testing.CliRunner`）验证 dashboard 的等待态、恢复态、审批可见性不回归。

## Risks & Mitigations

| 风险 | 概率 | 缓解措施 |
|------|------|---------|
| planning agent 意外在 main workspace 上执行 git 操作 | 中 | **当前仅 prompt 约束，非可执行保证。** 后续版本须落地 fail-closed 机制（命令 denylist / 变更快照 / worktree 隔离，见 Scope 前提约束章节）。在此之前并行安全性有已知缺口 |
| 写优先会让 planning 在独占请求密集时延迟 | 低 | 接受该 trade-off；独占操作稀疏且有界，若线上观察到问题再升级为 FIFO 公平门控 |
| `max_planning_concurrent` 设置过高导致资源压力 | 中 | 默认值 3；通过配置文档提示按机器资源调优 |
| 测试迁移遗漏旧 `Semaphore` 语义残留 | 中 | 全量 grep `get_main_path_limiter`, `reset_main_path_limiter`, `.locked()`, `async with limiter` 并逐一清理 |
| startup 前未按约定配置 gate | 低 | 在 `Orchestrator._startup()` 早期统一调用 `configure_main_path_gate()`；独立测试显式 `reset_main_path_gate(n)` |

## Review Notes

- 对抗性代码审查已完成
- 发现：8 条总计，6 条 real 已修复，2 条 noise 跳过
- 解决方式：auto-fix
- **以下修复已合并到正文对应章节（Technical Decisions #7/#8, 建议测试矩阵）：**
  - F-01: `configure()` 添加安全注释说明同步读取在启动序下的安全性
  - F-02: 添加 `max_planning_concurrent > max_concurrent_agents` 的交叉校验警告 → 已写入 Technical Decisions #7 + 测试矩阵
  - F-03: 添加 `parallel_safe: true` 在非 main workspace phase 上的配置警告 → 已写入 Technical Decisions #8 + 测试矩阵
  - F-04: 新增 shared 并发上限测试（5 任务 / max_shared=3 → cap=3）→ 已写入测试矩阵
  - F-05: MainPathGate docstring 记录写优先饥饿 trade-off
  - F-06: 确认 `reset_main_path_gate()` 已标注仅测试用

### Spec 修补记录 Round 1（2026-04-01）

针对 spec 后审查发现的 4 项问题：

1. **（高）Git 安全 fail-closed 约束**：在 Scope 新增"前提约束"章节，明确 prompt 约束的不充分性和 3 种 fail-closed 升级路径
2. **（中）AC 扩展 CLI/TUI 联动验证**：新增 AC#10，要求 approval/recovery/convergent-loop 变更通过 TUI pilot + CLI runner 验证
3. **（中）补充核心不变量必测项**：测试矩阵新增 `test_shared_mode_respects_max_cap`（饱和）、`test_busy_gate_rejects_reconfigure`（lifecycle）、交叉校验警告测试
4. **（低）Review Notes 回写正文**：F-02/F-03/F-04 的内容已合并到 Technical Decisions 和测试矩阵，消除正文/注释不一致

### Spec 修补记录 Round 2（2026-04-01）

针对第二轮审查发现的 4 项问题：

1. **（高）遗漏 merge/revert 调用点**：调用点清单从 6 个扩展到 8 个，新增 `_run_merge_worker`（#7，`merge_to_main()` 执行 `git checkout main` + `git merge --ff-only`）和 `_handle_approval_decision` revert 路径（#8，`revert_merge_range()` 执行 `git revert`）。Task 6 同步更新，并补充 gate 嵌套注意事项（regression 内部已有独立 gate 获取，merge worker 的 gate 范围不能包裹 regression）
2. **（中）Technical Decision #7 错误心智模型**：修正"超出部分将在 SubprocessManager 层排队"的表述。SubprocessManager 是实例级限流（每次 dispatch 新建），不构成跨 dispatch 全局上限。同步修正 `src/ato/config.py` 中的 warning 文案
3. **（中）files_to_modify / Task 8 未覆盖 AC#10**：frontmatter 和 Task 8 补入 `tests/integration/test_tui_pilot.py` + `tests/integration/test_orchestrator_lifecycle.py`，消除实施计划与验收标准的 traceability 断裂
4. **（中）status 降级**：从 `complete` 改为 `complete-with-known-gaps`，反映 Precondition 中声明的 fail-closed 约束尚未闭合
