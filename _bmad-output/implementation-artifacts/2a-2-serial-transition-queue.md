# Story 2A.2: 串行状态转换队列 (Serial Transition Queue)

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a 操作者,
I want 确认并发完成的任务不会导致状态冲突,
So that 系统在多 story 并行时状态一致性有保障。

## Acceptance Criteria

1. **串行 Transition 处理**
   ```
   Given TransitionQueue 已启动
   When 多个 agent 同时完成任务并提交状态转换事件
   Then 事件按提交顺序串行处理，每个事件依次执行：状态机 send() → SQLite 持久化 → commit
   And 状态转换处理延迟 ≤5 秒（NFR2）
   ```

2. **队列阻塞**
   ```
   Given TransitionQueue 处理过程中
   When 前一个 transition 尚未完成
   Then 后续事件在 asyncio.Queue 中排队等待，不会并发执行
   ```

3. **Nudge 通知机制**
   ```
   Given nudge 通知机制
   When TUI 或 ato submit 写入 SQLite 后触发 nudge
   Then Orchestrator 立即轮询，不等 2-5 秒定期轮询间隔
   ```

4. **并发 Story 转换正确性**
   ```
   Given 操作者查看系统状态
   When 两个 stories 的 transition 几乎同时提交
   Then ato status 显示两个 stories 的状态均正确更新，无冲突
   ```

**BDD Scenario:**
```gherkin
Scenario: Serial processing of concurrent transitions
Given TransitionQueue is running
And Story A is in 'creating' state
And Story B is in 'creating' state
When Agent for Story A completes task and submits transition to 'validating'
And Agent for Story B completes task and submits transition to 'validating' simultaneously
Then Story A transitions to 'validating' first
And Story B transitions to 'validating' second
And ato status shows both stories correctly in 'validating'
And no state conflicts detected in SQLite
```

## Tasks / Subtasks

- [ ] Task 1: 定义 TransitionEvent Pydantic 模型 (AC: #1)
  - [ ] 1.1 在 `src/ato/models/schemas.py` 中定义 `TransitionEvent(_StrictBase)`，字段：`story_id: str`, `event_name: str`, `source: Literal["agent", "tui", "cli"]`, `submitted_at: datetime`
  - [ ] 1.2 在 `src/ato/models/__init__.py` 导出 `TransitionEvent`

- [ ] Task 2: 实现 TransitionQueue 核心 (AC: #1, #2)
  - [ ] 2.1 在 `src/ato/transition_queue.py` 中实现 `TransitionQueue` 类
  - [ ] 2.2 构造函数接收 `db_path: Path` 和可选 `nudge: Nudge | None = None`，内部创建 `asyncio.Queue[TransitionEvent | None]`、`_machines` 缓存和 `_consumer_task`
  - [ ] 2.3 实现 `async submit(event: TransitionEvent) -> None`——将事件放入 asyncio.Queue
  - [ ] 2.4 实现 `async start() -> None`——使用 `get_connection(db_path)` 打开长连接，启动单个 consumer 后台任务；若重复 `start()`，不得创建第二个 consumer
  - [ ] 2.5 实现 `async stop() -> None`——标记队列关闭，发送哨兵（`None`），等待 consumer 任务完成并关闭连接；`stop()` 后拒绝新的 `submit()`
  - [ ] 2.6 实现 `async _consumer() -> None`——循环 `queue.get()`，对每个事件执行三步序列：
    1. 获取/创建该 story_id 的 `StoryLifecycle` 实例
    2. `await sm.send(event.event_name)` — 内存状态更新
    3. `await save_story_state(db, event.story_id, sm.current_state_value)` — 显式持久化
    4. `await db.commit()` — 统一提交
  - [ ] 2.7 consumer 内单个事件失败不 crash 队列——捕获异常、`await db.rollback()`、structlog 记录、继续处理下一个事件
  - [ ] 2.8 若失败发生在 `sm.send()` 之后，必须驱逐该 story 的缓存状态机（下次从 SQLite 重建），避免内存状态与已回滚数据库分叉
  - [ ] 2.9 使用 `structlog.contextvars` 绑定 `story_id` / `event_name` / `source`，并记录 `queue_depth`、`processing_start`、`processing_end`、`latency_ms`

- [ ] Task 3: 实现状态机实例管理 (AC: #1, #4)
  - [ ] 3.1 TransitionQueue 内部维护 `_machines: dict[str, StoryLifecycle]`（story_id → 状态机实例）
  - [ ] 3.2 实现 `async _get_or_create_machine(story_id: str) -> StoryLifecycle`——缓存命中直接返回；缓存未命中时从 SQLite 读取 `current_phase`，创建状态机并恢复到对应状态；未知 phase 直接抛 `StateTransitionError`
  - [ ] 3.3 恢复逻辑：`StoryLifecycle.create()` → `queued` 无操作；happy path 使用 `CANONICAL_PHASES` replay；`fixing` / `blocked` / `done` 走显式特殊分支
  - [ ] 3.4 若 story 不存在于 SQLite，抛出 `StateTransitionError`
  - [ ] 3.5 恢复失败时不得缓存半初始化状态机实例

- [ ] Task 4: 实现 Nudge 基础设施 (AC: #3)
  - [ ] 4.1 在 `src/ato/nudge.py` 中实现 `Nudge` 抽象，作为 Orchestrator wait-side 和外部 writer send-side 的统一入口
  - [ ] 4.2 wait-side 基于 `asyncio.Event` 实现：`notify()` 设置本进程 event，`wait(timeout)` 等待 event 或超时
  - [ ] 4.3 在同一模块暴露未来给 TUI / `ato submit` 调用的外部 sender helper（封装 `SIGUSR1` 或等价 transport；调用点在 Story 2A.3 / 2B.6 接入）
  - [ ] 4.4 `TransitionQueue.submit()` 只负责触发 same-process nudge；不要把它当成 TUI / `ato submit` 路径的唯一实现
  - [ ] 4.5 `wait()` 返回后自动 clear event，支持下次等待

- [ ] Task 5: 单元测试——TransitionQueue 核心行为 (AC: #1, #2, #4)
  - [ ] 5.1 创建 `tests/unit/test_transition_queue.py`
  - [ ] 5.2 测试 FIFO 顺序：提交 N 个事件，验证处理顺序一致
  - [ ] 5.3 测试串行化：并发提交多个事件，验证同一时刻只有一个在处理（通过回调计时验证）
  - [ ] 5.4 测试错误隔离：覆盖非法 transition 和持久化 / commit 失败两类错误，验证 rollback、生存性，以及失败 story 的状态机缓存被驱逐
  - [ ] 5.5 测试 start/stop 生命周期：重复 `start()` 不得产生第二个 consumer；`stop()` 后 consumer 优雅退出且新 `submit()` 被拒绝
  - [ ] 5.6 测试状态机恢复：从 SQLite 恢复中间状态的状态机实例，覆盖 `fixing` / `blocked` / `done` 等边界 phase

- [ ] Task 6: 单元测试——Nudge 通知机制 (AC: #3)
  - [ ] 6.1 创建 `tests/unit/test_nudge.py`
  - [ ] 6.2 测试 notify 后 wait 立即返回
  - [ ] 6.3 测试无 notify 时 wait 超时返回
  - [ ] 6.4 测试多次 notify 后 event 自动 clear，可继续下一轮等待
  - [ ] 6.5 测试外部 sender helper 会委托给约定的进程通知 transport（用 monkeypatch / fake PID 验证，无需真实 TUI / CLI）

- [ ] Task 7: 集成测试——并发 Transition 串行化 (AC: #1, #2, #4)
  - [ ] 7.1 创建 `tests/integration/test_transition_queue.py`
  - [ ] 7.2 测试 BDD 场景：两个 story 几乎同时提交 transition，验证串行处理 + SQLite 最终状态正确
  - [ ] 7.3 测试延迟：transition 处理 ≤5 秒（NFR2）
  - [ ] 7.4 测试端到端：submit → queue → state machine send → save_story_state → commit → `get_story()` / SQLite 读回验证
  - [ ] 7.5 明确本 story 不新增 `ato status` / `ato submit` 命令；AC #3 / #4 的 CLI/TUI 观测以持久化状态和 nudge contract 作为可执行验收代理

- [ ] Task 8: 代码质量验证
  - [ ] 8.1 `uv run ruff check src/ato/transition_queue.py src/ato/nudge.py` — 通过
  - [ ] 8.2 `uv run mypy src/ato/transition_queue.py src/ato/nudge.py` — 通过
  - [ ] 8.3 `uv run pytest tests/unit/test_transition_queue.py tests/unit/test_nudge.py tests/integration/test_transition_queue.py -v` — 全部通过
  - [ ] 8.4 `uv run pytest` — 确认零回归（当前基线 225 passed）

## Dev Notes

### 核心实现模式

**TransitionQueue Consumer Pattern（架构决策核心）：**

TransitionQueue 是整个系统并发安全的关键——所有状态转换通过单个 consumer 串行化，保证无竞态条件。

```python
# TransitionQueue._consumer() 核心循环
while True:
    event = await self._queue.get()
    if event is None:  # 哨兵 → 退出
        break
    try:
        sm = await self._get_or_create_machine(event.story_id)
        await sm.send(event.event_name)                           # 1. 内存状态更新
        await save_story_state(db, event.story_id,
                               sm.current_state_value)            # 2. 显式持久化
        await db.commit()                                         # 3. 统一提交
    except Exception:
        logger.exception("transition_failed", story_id=event.story_id,
                         event=event.event_name)
        self._machines.pop(event.story_id, None)  # post-send 失败后丢弃缓存，避免内存/DB 分叉
        await db.rollback()  # 失败回滚，保持数据一致性
    finally:
        self._queue.task_done()
```

**关键约束：**
- consumer 使用 `get_connection()` 打开的**长连接**（生命周期复用），与 TUI 的短连接策略不同
- 三步序列（send → persist → commit）**不可拆分**——任何步骤失败都要 rollback
- `start()` 只能拥有**一个** consumer；重复启动不能破坏串行保证
- consumer 内部**不 await 外部 IO**（如 CLI 调用）——外部 IO 在 subprocess_mgr 中处理
- 只要失败发生在 `sm.send()` 之后，就必须驱逐该 story 的缓存状态机，避免内存状态快于 SQLite
- 单线程约束：所有状态机操作在同一事件循环线程，**不跨线程共享 sm 实例**

**SQLite 连接策略（架构文档 Decision 2）：**

| 场景 | 连接模式 | 理由 |
|------|---------|------|
| TransitionQueue consumer | 长连接（consumer 生命周期复用） | 串行写入，无并发冲突 |
| Orchestrator 轮询读取 | 短连接 | 读不阻塞写，确保最新 WAL 数据 |
| TUI 读取/写入 | 短连接 + 立即 commit | 独立进程，最小化写锁持有 |

**PRAGMA 设置（每个连接必须）：**
- `PRAGMA journal_mode=WAL`
- `PRAGMA busy_timeout=5000`
- `PRAGMA synchronous=NORMAL`
- `PRAGMA foreign_keys=ON`

### Nudge 机制设计

**目标：** TUI 或 `ato submit` 写入 SQLite 后立即通知 Orchestrator，避免等待 2-5 秒定期轮询。

**实现方案：** `nudge.py` 提供统一抽象。
- wait-side：Orchestrator 进程内通过 `asyncio.Event` 实现 `wait(timeout)`
- send-side：为后续 TUI / `ato submit` 预留外部 sender helper，把具体进程通知 transport 封装在 `nudge.py`

```python
class Nudge:
    def __init__(self) -> None:
        self._event = asyncio.Event()

    def notify(self) -> None:
        self._event.set()

    async def wait(self, timeout: float) -> bool:
        """等待 nudge 或超时。返回 True 表示被 nudge 唤醒。"""
        try:
            await asyncio.wait_for(self._event.wait(), timeout=timeout)
            return True
        except TimeoutError:
            return False
        finally:
            self._event.clear()

def send_external_nudge(orchestrator_pid: int) -> None:
    """供 TUI / CLI 等外部进程调用；具体 transport 封装在本模块。"""
```

**关键点：**
- `TransitionQueue.submit()` 调用 `nudge.notify()` 唤醒 same-process waiter，但这**不是**外部 writer 的唯一路径
- Story 2A.3 的 Orchestrator 轮询循环使用 `await nudge.wait(timeout=polling_interval)` 替代固定 sleep
- Story 2B.6 / 4.1 的 CLI/TUI 写入路径将复用 `nudge.py` 的外部 sender helper；本 story 先交付基础设施与 contract，不在 `cli.py` 中提前接线

### 状态机实例管理

**问题：** 多个 story 并行时，TransitionQueue 需要为每个 story 维护独立的 StoryLifecycle 实例。

**方案：** 内存缓存 + 按需创建/恢复。

```python
class TransitionQueue:
    _machines: dict[str, StoryLifecycle]  # story_id → 状态机

    async def _get_or_create_machine(self, story_id: str) -> StoryLifecycle:
        if story_id in self._machines:
            return self._machines[story_id]
        # 从 SQLite 恢复
        story = await get_story(self._db, story_id)
        if story is None:
            raise StateTransitionError(f"Story '{story_id}' not found in database")
        sm = await StoryLifecycle.create()
        # replay transitions to reach current phase
        await self._replay_to_phase(sm, story.current_phase)
        self._machines[story_id] = sm
        return sm
```

**Replay 逻辑：** 从 `queued` 出发，沿 happy path（每个阶段的 success transition）逐步推进到目标 phase。对于 `fixing` / `blocked` / `done` 等非 happy-path 或终态，需要特殊处理（`review_fail` / `qa_fail` → fixing, `escalate` → blocked, `regression_pass` → done）。

### 已有代码复用

**直接复用（不修改）：**
- `src/ato/state_machine.py` → `StoryLifecycle` 类、`save_story_state()` 函数、`PHASE_TO_STATUS` 映射
- `src/ato/models/db.py` → `get_story()` 读取当前状态、`get_connection()` 打开连接（PRAGMA 已内置）
- `src/ato/models/schemas.py` → `StateTransitionError`、`StoryRecord`、`StoryStatus`
- `tests/conftest.py` → `db_path`、`initialized_db_path` fixtures

**需要新增：**
- `src/ato/models/schemas.py` → 新增 `TransitionEvent` Pydantic 模型（不修改已有模型）
- `src/ato/models/__init__.py` → 导出 `TransitionEvent`

**不要重复造轮：**
- ❌ 不要在 transition_queue.py 中自己写 SQLite 操作——使用 `db.py` 已有的 `get_story()`、`get_connection()`
- ❌ 不要在 transition_queue.py 中实现自己的状态持久化——使用 `save_story_state()`
- ❌ 不要创建新的异常类——使用已有的 `StateTransitionError`
- ❌ 不要用 `asyncio.gather`——项目要求用 `asyncio.TaskGroup`（但本 story consumer 是串行的，通常不涉及）
- ❌ 不要用 `print()` 输出日志——使用 `structlog`
- ❌ 不要在 SQLite 写事务中 await 外部 IO

### 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/ato/transition_queue.py` | **重写** | 从 1 行 docstring 扩展为完整 TransitionQueue 实现 |
| `src/ato/nudge.py` | **重写** | 从 1 行 docstring 扩展为 Nudge 通知机制实现 |
| `src/ato/models/schemas.py` | **修改** | 新增 `TransitionEvent` Pydantic 模型 |
| `src/ato/models/__init__.py` | **修改** | 导出 `TransitionEvent` |
| `tests/unit/test_transition_queue.py` | **新建** | TransitionQueue 单元测试 |
| `tests/unit/test_nudge.py` | **新建** | Nudge 通知机制单元测试 |
| `tests/integration/test_transition_queue.py` | **新建** | 并发 transition 串行化集成测试 |

**不应修改的文件：**
- `src/ato/state_machine.py` — StoryLifecycle 和 save_story_state 保持不变
- `src/ato/models/db.py` — DDL 和 CRUD 函数无需修改
- `src/ato/core.py` — Orchestrator 事件循环是 Story 2A.3 的范畴
- `src/ato/cli.py` — CLI 命令无需修改

### 依赖关系

**前置（已完成）：**
- ✅ Story 1.1：项目脚手架、structlog 配置、python-statemachine 依赖
- ✅ Story 1.2：SQLite 持久化层（DDL、CRUD、PRAGMA WAL）
- ✅ Story 1.3：声明式配置引擎（PhaseDefinition、ATOSettings）
- ✅ Story 2A.1：StoryLifecycle 状态机（13 状态、15 事件、save_story_state()、100% transition 覆盖）

**后续依赖本 story：**
- Story 2A.3（Orchestrator 事件循环）— 需要消费 TransitionQueue + Nudge
- Epic 2B（Agent 集成）— 需要通过 TransitionQueue 提交 transition 事件
- Epic 3（Convergent Loop）— review ↔ fixing 循环需要 TransitionQueue 串行化
- Epic 5（崩溃恢复）— 依赖 TransitionQueue 保证的 SQLite 状态一致性

### Project Structure Notes

- `transition_queue.py` 已存在于 `src/ato/`，当前只有 1 行 docstring，本 story 负责完整实现
- `nudge.py` 同上，1 行 docstring
- 模块依赖方向：`transition_queue.py` 依赖 `state_machine.py`、`models/db.py`、`models/schemas.py`——不依赖 `core.py`、`adapters/` 或 `tui/`
- `nudge.py` 只依赖 `asyncio` + 少量标准库进程通知原语；不要直接依赖 `core.py`、`cli.py` 或 TUI 模块
- `ato status` / `ato submit` 尚未在当前代码树交付；本 story 以 SQLite 读回和 nudge contract 作为可执行验收代理
- 测试文件遵循 `tests/unit/test_<module>.py` 和 `tests/integration/test_<feature>.py` 命名规范

### 关键技术注意事项

1. **asyncio.Queue 是无界的**——默认不限大小，本 MVP 阶段不需要设置 maxsize（系统最多处理几十个 story）
2. **队列类型要包含哨兵**——实现上应为 `asyncio.Queue[TransitionEvent | None]`；`None` 作为停止信号。不要用 `task.cancel()`，避免处理中的 transition 被中断
3. **rollback 只回滚数据库，不回滚内存状态机**——因此一旦 `sm.send()` 后续失败，必须驱逐缓存状态机并在下次从 SQLite 重建
4. **状态机 replay 的边界情况**——`fixing` 状态只能通过 `review_fail` 或 `qa_fail` 到达；`blocked` 只能通过 `escalate` 到达；`done` 只能通过 `regression_pass` 到达
5. **`queue.task_done()` 必须调用**——即使处理失败也要调用，否则 `queue.join()` 永远阻塞
6. **单 consumer 约束必须测试**——重复 `start()` 如果产生第二个 consumer，会直接破坏 AC #1 的串行保证
7. **pytest-asyncio auto mode**——`pyproject.toml` 已配置 `asyncio_mode="auto"`，测试函数声明 `async def` 即可
8. **structlog.contextvars**——在 consumer 入口绑定 `story_id`、`event_name`、`source`，整个处理链自动携带上下文
9. **NFR2 性能目标**——≤5 秒衡量的是状态转换快路径；本 story 可验证到 commit / 持久化一致性，下一阶段 agent 启动由 Story 2A.3 的 Orchestrator 负责串接

### 架构约束备忘

**Enforcement Rules（必须遵守）：**
- 所有公共函数有类型标注（参数和返回值）
- 新模块有对应的单元测试文件
- 状态机操作用 `structlog.contextvars` 在入口绑定上下文
- SQLite 写事务中不 await 外部 IO
- 异步状态机创建后 `await sm.activate_initial_state()`

**Anti-Patterns（禁止）：**
- ❌ 不要用 `asyncio.gather`（用 `TaskGroup`）
- ❌ 不要用 `print()` 输出日志（用 structlog）
- ❌ 不要在 `except` 中静默吞掉异常（至少 `structlog.warning`）
- ❌ 不要跨线程共享状态机实例
- ❌ 不要手动拼接 SQL（用参数化查询）
- ❌ 不要在 PersistentModel setter 中直接写 SQLite（consumer 显式持久化）
- ❌ MVP 不要使用 `model_construct`（用 `model_validate`）

### Story 2A.1 关键学习

1. **`current_state_value`** 是获取状态机当前阶段的正确 API（`current_state` 已弃用）
2. **`save_story_state()` 不自动 commit**——调用方负责 `await db.commit()`
3. **`update_story_status()` 支持 `commit=False`**——Story 2A.1 已修改 db.py 增加此参数
4. **`StoryLifecycle.create()`** 创建无需配置验证的实例（适合测试和恢复场景）
5. **`from_config()` 验证阶段名与 transition**——生产环境使用，测试中可直接用 `create()`
6. **`TransitionNotAllowed`** 由 `StateMachine` 基类自动抛出，`send()` override 已记录拒绝日志
7. **55 个单元测试 + 5 个集成测试**全部通过，基线 225 passed

### References

- [Source: _bmad-output/planning-artifacts/epics.md — Epic 2A, Story 2A.2]
- [Source: _bmad-output/planning-artifacts/architecture.md — TransitionQueue 设计与连接策略]
- [Source: _bmad-output/planning-artifacts/architecture.md — Decision 2: TUI↔Orchestrator Communication]
- [Source: _bmad-output/planning-artifacts/architecture.md — Decision 7: 优雅停止标记法]
- [Source: _bmad-output/planning-artifacts/architecture.md — Decision 8: 状态机测试覆盖定义]
- [Source: _bmad-output/planning-artifacts/architecture.md — python-statemachine 3.0 Async 集成模式]
- [Source: _bmad-output/planning-artifacts/architecture.md — SQLite 连接策略]
- [Source: _bmad-output/planning-artifacts/architecture.md — Enforcement Rules & Anti-Patterns]
- [Source: _bmad-output/planning-artifacts/prd.md — FR3 状态机自动推进, FR4 并发无冲突, NFR2 ≤5s]
- [Source: _bmad-output/implementation-artifacts/2a-1-story-state-machine-progression.md — Story 2A.1 实现总结]
- [Source: src/ato/state_machine.py — StoryLifecycle, save_story_state, PHASE_TO_STATUS]
- [Source: src/ato/models/db.py — get_story, get_connection, update_story_status(commit=False)]
- [Source: src/ato/models/schemas.py — StateTransitionError, StoryRecord, StoryStatus]
- [Source: _bmad-output/project-context.md — python-statemachine 3.0 async 集成规则]

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

### File List
