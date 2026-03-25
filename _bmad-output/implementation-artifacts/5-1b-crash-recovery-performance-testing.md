# Story 5.1b: 崩溃恢复性能测试与验证

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a 操作者,
I want 崩溃恢复在 30 秒内完成，且恢复逻辑经过充分测试,
So that 恢复速度可预期，恢复行为可信赖。

## Acceptance Criteria

1. **AC1 — 性能目标 ≤30s**: 崩溃恢复完整流程（SQLite 扫描 + PID/artifact 检查 + 恢复决策）计时 ≤30 秒（NFR1 MVP 目标）。计量边界以 `RecoveryEngine.run_recovery()` 的同步执行窗口为准；后台 reattach 监控循环和 re-dispatch 后续 agent 实际执行时间不计入该 NFR。必须用自动化性能测试证明，不能仅靠手动验证。
2. **AC2 — 函数式测试策略**: 运行现有 `tests/integration/test_crash_recovery.py` 时，采用纯数据库状态驱动的函数式测试——构造"崩溃前的数据库状态"（插入 status=running task，PID 不存在）→ 调用 recovery → 验证分类行为。不需要真实杀进程。
3. **AC3 — 4 种恢复分类集成测试**: 分别构造 PID 存活、artifact 存在、Structured Job 无 artifact、Interactive Session 的场景，每种场景的恢复行为与 Story 5.1a 定义一致；若现有集成测试已覆盖，则只补齐缺口，不复制同一套功能 case 到性能测试文件中。

## Tasks / Subtasks

- [x] Task 1: 性能基准测试框架搭建 (AC: #1)
  - [x] 1.1 在 `tests/performance/` 下创建 `test_recovery_perf.py`
  - [x] 1.2 创建 `tests/performance/conftest.py`，复用 `insert_story()` / `insert_task()` 模式构造大规模数据库 fixture（10/100/500 个 running tasks，混合四种恢复分类）
  - [x] 1.3 使用 `time.perf_counter()` 仅计时 `await RecoveryEngine.run_recovery()` 的同步窗口
  - [x] 1.4 在计时结束后调用 `await engine.await_background_tasks()` 做清理，但不要把这段等待计入 AC1/NFR1 计时
  - [x] 1.5 为性能测试添加 `@pytest.mark.perf`
- [x] Task 2: 分层性能拆解测试 (AC: #1)
  - [x] 2.1 单独计时 SQLite 扫描阶段 (`get_running_tasks()` + `get_paused_tasks()`)
  - [x] 2.2 单独计时 PID 检查阶段（`_is_pid_alive()` × N tasks）
  - [x] 2.3 单独计时 artifact 检查阶段（`_artifact_exists()` × N tasks）
  - [x] 2.4 单独计时分类决策 + 恢复动作分派阶段（`_reattach()` / `_complete_from_artifact()` / `_reschedule()` / `_mark_needs_human()` 的同步入口，不等待后台 dispatch 完成）
  - [x] 2.5 添加 assert 检查各阶段耗时合理性，并验证分层测量结果与端到端 `run_recovery()` 量级一致
- [x] Task 3: 规模递增压力测试 (AC: #1)
  - [x] 3.1 10 tasks 场景 — 基线性能验证
  - [x] 3.2 100 tasks 场景 — 常规负载验证
  - [x] 3.3 500 tasks 场景 — 压力测试（超越 MVP 预期上限）
  - [x] 3.4 每个场景混合 4 种 recovery 分类（reattach/complete/reschedule/needs_human）
- [x] Task 4: 验证现有集成测试完整性 (AC: #2, #3)
  - [x] 4.1 审查现有 `tests/integration/test_crash_recovery.py` 是否已覆盖 4 种分类的完整性
  - [x] 4.2 如有遗漏分类场景，仅在该现有文件中补充测试；不要在 `tests/performance/` 中复制四类功能验证
  - [x] 4.3 验证每个场景的断言与 Story 5.1a AC 定义及当前 `_PHASE_SUCCESS_EVENT` 映射严格一致
  - [x] 4.4 验证 structlog 输出包含 `recovery_action`、`recovery_mode`、`duration_ms`、`dispatched` 等必要字段
- [x] Task 5: 性能回归检测机制 (AC: #1)
  - [x] 5.1 在性能测试中记录基线时间到 structlog
  - [x] 5.2 添加 hard assert: 100 tasks 场景的 `run_recovery()` 计时 ≤5s，500 tasks 场景 ≤30s
  - [x] 5.3 确保 `pyproject.toml` 注册 `perf` marker
  - [x] 5.4 明确性能测试的显式运行命令（如 `uv run pytest tests/performance/ -m perf`）；不要假设仅添加 marker 就会默认跳过该测试组
- [x] Task 6: 运行全量测试确认零回归 (AC: #1, #2, #3)
  - [x] 6.1 `uv run pytest` 全量通过
  - [x] 6.2 `uv run pytest tests/performance/ -m perf` 通过
  - [x] 6.3 `uv run ruff check src/ tests/`
  - [ ] 6.4 `uv run mypy src/` — 2 个预存 TUI 错误（src/ato/tui/app.py:21, src/ato/tui/dashboard.py:8 的 unused type:ignore），非本 story 引入，本 story 新增文件 mypy 通过

## Dev Notes

### 核心设计：性能测试而非功能重写

Story 5.1a 已实现完整的 RecoveryEngine（895 行）+ 单元测试（1375 行）+ 集成测试（686 行）+ WAL 测试（203 行）。本 story **不修改 recovery.py 本身**，专注于：
- 证明 NFR1 ≤30s 目标达成
- 确保测试覆盖 4 种分类的完整性
- 建立性能回归检测基线

本 story 的性能测量对象是 **恢复引擎的同步恢复窗口**，不是 agent 实际工作时长：
- 计时包含：SQLite 扫描、PID/artifact 检查、分类与恢复动作分派
- 不计入：`_monitor_reattached_pid()` 的长期轮询、后台 re-dispatch task 的 CLI 执行时间
- 为避免泄漏后台 task，性能测试可以在断言后调用 `await engine.await_background_tasks()` 做清理，但这一步不进入 NFR1 计时窗口

### 现有 Recovery 实现要点（不要重写！）

`src/ato/recovery.py` RecoveryEngine 已实现：
- 四路分类算法: `classify_task()` → reattach / complete / reschedule / needs_human
- PID 检测: `_is_pid_alive(pid)` → `os.kill(pid, 0)` + ESRCH/EPERM errno 处理
- Artifact 检测: `_artifact_exists(task: TaskRecord)` → `Path(task.expected_artifact).exists()`
- Phase → 成功事件映射: `_PHASE_SUCCESS_EVENT` 字典
- SAVEPOINT 原子操作: needs_human 路径用 `SAVEPOINT` 保证 task=failed + approval 创建的原子性
- 后台异步任务: `_background_tasks` 列表 + `await_background_tasks()`

### 现有测试清单（已有，先复用再补齐）

| 文件 | 当前覆盖范围 |
|------|-------------|
| `tests/unit/test_recovery.py` | PID/artifact 检测、4 路分类、正常恢复、边界用例 |
| `tests/integration/test_crash_recovery.py` | 端到端 4 分类场景、convergent loop、多 story、structlog |
| `tests/integration/test_wal_recovery.py` | WAL 模式验证、数据完整性、recovery 字段保留 |

性能 story 的目标不是再造一套四分类功能回归；先审查上面这些现有文件，只有确认缺口时才补测。

### 性能测试设计原则

1. **纯数据库状态驱动** — 与 5.1a 一致，mock `os.kill()` 和 `Path.exists()`，不杀真实进程
2. **分层计时** — 分别测量 DB 查询、PID 检查、artifact 检查、分类执行各阶段
3. **可重复** — 使用 `time.perf_counter()` 而非 wall clock，避免 CI 环境抖动
4. **渐进规模** — 10 → 100 → 500 tasks，验证线性增长不会爆炸
5. **计时边界固定** — NFR1 断言只围绕 `run_recovery()`；后台清理/监控只做资源回收，不算进 SLA

### 性能瓶颈预期分析

```
组件                          预期耗时（500 tasks）
SQLite SELECT running tasks   < 50ms（WAL + 索引）
os.kill() × 500              < 100ms（内核调用）
Path.exists() × 500          < 200ms（文件系统）
分类逻辑 × 500               < 10ms（纯内存计算）
恢复执行（mock adapter）      < 500ms
总计                          < 1s（远低于 30s 目标）
```

> 注意：真实环境中 reschedule 路径会启动后台 dispatch（adapter.execute），但性能测试中 adapter 已 mock，仅测量 recovery 引擎本身的开销。

### 数据库 Fixture 构造模式

复用 5.1a 集成测试的 fixture 模式：
```python
# 构造 N 个 running tasks（混合 4 种分类场景）
for i in range(n_tasks):
    task = TaskRecord(
        task_id=f"perf-task-{i}",
        story_id=story_id,
        status="running",
        pid=fake_pid(i),
        expected_artifact=f"/tmp/artifact-{i}.json" if has_artifact(i) else None,
        phase=random_phase(i),
        ...
    )
    await insert_task(db, task)
```

Mock 策略：
- `os.kill(pid, 0)` → 根据 task 分类返回不同结果（25% alive, 25% ESRCH, etc.）
- `Path.exists()` → 根据 expected_artifact 是否为 "有 artifact" 组返回 True/False

实现时优先复用现有测试辅助风格：
- 参考 `tests/integration/test_crash_recovery.py` 中的 `_make_story()` / `_make_running_task()` 数据形状
- 参考顶层 `tests/conftest.py` 的 `initialized_db_path` 初始化方式
- 不要把 `core.py` 内部用于 transition 提交的哨兵值 `expected_artifact=\"transition_submitted\"` 当作 complete-path artifact 证据；性能 fixture 应使用显式 artifact 路径或直接 patch `_artifact_exists()`

### 关键依赖（已安装）

| 包 | 版本 | 用途 |
|----|------|------|
| pytest | 已安装 | 测试框架 |
| pytest-asyncio | 已安装 | async 测试支持 |
| aiosqlite | 0.22.1 | 异步 SQLite |
| structlog | 已安装 | 结构化日志 |

> **不要引入新依赖**（如 pytest-benchmark）。使用 `time.perf_counter()` + `assert` 即可满足需求。

### 文件结构

```
tests/
├── performance/           # ← 新建目录
│   ├── __init__.py
│   ├── conftest.py        # 大规模 DB fixture + mock 配置
│   └── test_recovery_perf.py  # 性能基准测试
├── integration/
│   ├── test_crash_recovery.py  # 已有（审查完整性，按需补充）
│   └── test_wal_recovery.py    # 已有（不修改）
└── unit/
    └── test_recovery.py        # 已有（不修改）
```

### 不要做的事

- **不要修改 `src/ato/recovery.py`** — 除非性能测试发现真实瓶颈需要优化
- **不要重写 5.1a 的测试** — 只审查和按需补充
- **不要引入 pytest-benchmark** — `time.perf_counter()` + hard assert 足够
- **不要测试 adapter 真实执行时间** — adapter 是 mock 的，只测 recovery 引擎
- **不要修改 WAL 配置** — WAL + synchronous=NORMAL 已在 init_db() 正确配置
- **不要实现恢复摘要 CLI 输出** — 那是 Story 5.2 的范围

### 4 种恢复分类定义（来自 Story 5.1a）

| PID 存活? | Artifact 存在? | 分类 | 恢复动作 |
|-----------|---------------|------|---------|
| Yes | - | reattach | 重新注册 PID 监听 + 启动 async 监控任务 |
| No | Yes | complete | 标记完成 + 提交 transition event 推进流水线 |
| No | No (Structured Job) | reschedule | 重置为 pending + 后台重新 dispatch |
| No | - (Interactive Session) | needs_human | 标记 failed + 创建 crash_recovery approval |

Interactive Session 判定: `RecoveryEngine.__init__` 接收 `interactive_phases: set[str]` 参数，由 `core.py` 从 `phase_definitions` 中筛选 `phase_type == "interactive_session"` 后传入。recovery.py 内部不直接读取配置。

### Project Structure Notes

- 性能测试目录 `tests/performance/` 是新增路径，与现有 `tests/unit/` 和 `tests/integration/` 并列
- `pyproject.toml` 需注册 `perf` marker: `markers = ["perf: performance benchmark tests"]`
- marker 仅用于显式筛选；如果希望默认命令不跑 perf，需要额外命令约定或 CI 配置，而不是假设 marker 自动跳过
- 遵循项目命名规范: snake_case 模块名、PascalCase 类名

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Epic-5] — Story 5.1b AC 定义
- [Source: _bmad-output/planning-artifacts/prd.md#NFR1] — 崩溃恢复 ≤30s MVP / ≤10s 成熟期
- [Source: _bmad-output/planning-artifacts/architecture.md#Decision-7] — 优雅停止标记法
- [Source: _bmad-output/planning-artifacts/architecture.md#Decision-8] — 状态机测试覆盖 + 崩溃恢复测试策略
- [Source: _bmad-output/implementation-artifacts/5-1a-crash-recovery-auto-resume.md] — 前序 story 实现细节
- [Source: src/ato/recovery.py] — RecoveryEngine 实现（895 行）
- [Source: tests/integration/test_crash_recovery.py] — 现有集成测试（686 行）
- [Source: tests/unit/test_recovery.py] — 现有单元测试（1375 行）

### Previous Story Intelligence (5.1a)

**5.1a 实现成果:**
- RecoveryEngine 核心类：四路分类 + 双恢复路径（crash/normal）
- 6 轮 code review 修复要点：
  - R1: AC3/AC2/AC5 违规修复
  - R2: reschedule 闭包问题、SAVEPOINT 原子性、缺失 phase success events
  - R3: 质量门控绕过、执行上下文丢失（worktree + sandbox）
  - R5: convergent_loop 语义错误、计数不准确（新增 dispatched_count）
  - R6: 通用 prompt 导致解析失败（→ `_CONVERGENT_LOOP_PROMPTS`）、后台异常丢失（→ `_mark_dispatch_failed` 兜底）

**5.1a 测试模式（复用！）:**
- `autouse` fixture mock `_create_adapter` 防止真实 CLI 调用
- 复用 `tests/unit/test_recovery.py` 的 `_make_story()` / `_make_task()` 以及 `tests/integration/test_crash_recovery.py` 的 `_make_story()` / `_make_running_task()` 数据构造模式
- 顶层 `tests/conftest.py` 已提供 `initialized_db_path` fixture，可作为性能测试初始化 SQLite 的基线做法
- structlog 日志验证通过 `caplog` 或 structlog testing utilities

**5.1a 已知问题（验证报告）:**
- 验证报告指出 PID reattach 和 artifact 注册的合约问题，但实现已通过 6 轮 review 修复
- 性能测试应验证这些修复后的路径仍在 ≤30s 内完成

### Git Intelligence

最近提交:
- `c8c1bde` docs: 更新 epics/prd 添加 debugging-strategies skill 辅助修复说明
- `3662f9b` Merge story 5.1a: 崩溃恢复自动恢复完整实现
- `f4642d2` feat: Story 5.1a 崩溃恢复自动恢复完整实现

5.1a 已合入 main，recovery.py 和相关测试文件已稳定。

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6 (1M context)

### Debug Log References

- reattach PID 监控循环会永久轮询（PID mock 返回 alive），性能测试中改用 `_cancel_background_tasks()` 取消后台任务而非 `await_background_tasks()`

### Completion Notes List

- ✅ Task 1: 创建 `tests/performance/` 目录结构（`__init__.py` + `conftest.py` + `test_recovery_perf.py`），conftest 提供 10/100/500 tasks 的 fixture，按 index % 4 均匀分配四种恢复分类
- ✅ Task 2: TestLayeredPerformance 分别计时 SQLite 扫描、PID 检查、artifact 检查、分类决策四阶段，含分层-端到端一致性验证
- ✅ Task 3: TestScaleProgression 验证 10/100/500 tasks 的分类分布正确性（每类 25% 均匀分布）
- ✅ Task 4: 审查现有集成测试 — 四种分类已完整覆盖，仅补充 `dispatched` 字段断言缺口
- ✅ Task 5: TestPerformanceRegression 包含 structlog 基线记录 + hard assert 阈值（100 tasks ≤5s, 500 tasks ≤30s），pyproject.toml 注册 `perf` marker
- ✅ Task 6: 全量 891 测试通过，13 性能测试通过，ruff 零错误；mypy src/ 有 2 个预存 TUI 错误（非本 story 引入），本 story 新增文件 mypy 通过

### Change Log

- 2026-03-25: validate-create-story 修订 —— 明确 NFR1 的计时边界只覆盖 `run_recovery()` 同步窗口；要求 `await_background_tasks()` 仅做清理不计时；收敛 perf marker 的实际使用方式；避免在性能测试中复制现有四分类功能回归
- 2026-03-25: 完成全部 6 个 Task 实现 —— 13 个性能测试全部通过，NFR1 ≤30s 目标验证达成，集成测试补充 `dispatched` 字段断言
- 2026-03-25: Code Review R1 修复 3 项 —— (高) PID/artifact 分层测试改为通过模块属性调用被测函数; (中) 分类决策测试补充完整 dispatch 动作循环 + 双边一致性断言; (低) 6.4 mypy 标记修正为未完成（预存 TUI 错误）
- 2026-03-25: Code Review R2 修复 1 项 —— (中) PID/artifact 分层 benchmark 改为 mock os.kill/Path.exists 低层级，让 _is_pid_alive 的 errno 处理和 _artifact_exists 的 Path 构造逻辑完整执行；新增 _build_os_kill_mock + _build_path_exists_fn 到 conftest

### File List

- tests/performance/__init__.py (新增)
- tests/performance/conftest.py (新增)
- tests/performance/test_recovery_perf.py (新增)
- tests/integration/test_crash_recovery.py (修改 — 补充 `dispatched` 字段断言)
- pyproject.toml (修改 — 注册 `perf` marker)
- _bmad-output/implementation-artifacts/sprint-status.yaml (修改 — 状态更新)
- _bmad-output/implementation-artifacts/5-1b-crash-recovery-performance-testing.md (修改 — 任务完成标记)
