# Story 2A.1: Story 状态机自动推进

Status: done

## Story

As a 操作者,
I want 看到 story 在状态机中按配置的阶段顺序自动推进,
So that 确认编排系统正确驱动 story 生命周期。

## Acceptance Criteria

1. **StoryLifecycle 状态机构建**
   ```
   Given Story 1.3 已交付 `ATOSettings`、`PhaseDefinition`、`build_phase_definitions()`，且 phase definitions 使用本 story 约定的规范阶段名
   When 调用 StoryLifecycle.from_config(phase_definitions) 构建状态机
   Then 生成包含所有配置阶段和转换的状态机实例
   And 由于 StoryLifecycle 定义了 async 回调，创建后执行 await sm.activate_initial_state()，初始状态为 queued
   ```

2. **状态转换与持久化**
   ```
   Given story 状态机处于某个阶段
   When 通过 await sm.send(event) 发送合法转换事件
   Then 状态机转移到下一阶段
   And 新阶段通过 save_story_state(db, story_id, sm.current_state_value) 持久化到 SQLite stories 表
   And get_story(db, story_id) 返回与状态机一致的 status 与 current_phase，供后续 CLI/TUI 状态展示复用
   ```

3. **非法转换拒绝**
   ```
   Given 尝试发送非法转换事件
   When 事件不在当前状态的合法转换列表中
   Then 状态机拒绝转换，状态不变
   And structlog 记录拒绝原因
   ```

4. **100% Transition 覆盖测试**
   ```
   Given 100% transition 覆盖测试（Decision 8）
   When 执行状态机单元测试
   Then 每个合法 transition 至少执行 1 次（~20 tests）
   ```

## Tasks / Subtasks

- [x] Task 1: 实现 StoryLifecycle 状态机 (AC: #1, #3)
  - [x] 1.1 在 `src/ato/state_machine.py` 中定义 `StoryLifecycle(StateMachine)` 类，包含规范阶段状态与转换
  - [x] 1.2 定义规范 State：queued(initial), creating, validating, dev_ready, developing, reviewing, fixing, qa_testing, uat, merging, regression, done(final), blocked(final/MVP sink)
  - [x] 1.3 定义与状态图一致的 Transition 事件：start_create, create_done, validate_pass, validate_fail, start_dev, dev_done, review_pass, review_fail, fix_done, qa_pass, qa_fail, uat_pass, merge_done, regression_pass, escalate
  - [x] 1.4 `from_config()` 使用 HasPhaseInfo Protocol 消费阶段定义，验证有序阶段名与 CANONICAL_PHASES 一致，并校验 next_on_success/next_on_failure 与 CANONICAL_TRANSITIONS 匹配
  - [x] 1.5 添加 async `on_enter_state` / `on_exit_state` 通用回调（structlog 记录状态变更）
  - [x] 1.6 在实现和测试中使用 `current_state_value` / `configuration`，不依赖已弃用的 `current_state`
  - [x] 1.7 确保非法 transition 抛出 `TransitionNotAllowed`，状态不变

- [x] Task 2: 实现 save_story_state 持久化桥接 (AC: #2)
  - [x] 2.1 在 `src/ato/state_machine.py` 中实现 `async save_story_state(db, story_id, phase_name: str)` 函数
  - [x] 2.2 实现状态机阶段 → StoryStatus 映射逻辑（PHASE_TO_STATUS 映射表）
  - [x] 2.3 为 `update_story_status()` 增加 `commit: bool = True` 参数，`save_story_state()` 使用 `commit=False`
  - [x] 2.4 保持 TransitionQueue 的事务边界为：`await sm.send(event)` → `await save_story_state(...)` → `await db.commit()`

- [x] Task 3: 明确高层状态与详细阶段边界 (AC: #2)
  - [x] 3.1 不扩展 `StoryStatus` Literal；高层状态保持现有 8 个值
  - [x] 3.2 详细生命周期阶段仅存入 `current_phase`（TEXT）
  - [x] 3.3 确保 `save_story_state()` 映射一致：`queued` → `"backlog"`，`creating`/`validating` → `"planning"`，`dev_ready` → `"ready"`，`developing`/`qa_testing`/`merging`/`regression` → `"in_progress"`，`reviewing`/`fixing` → `"review"`，`uat` → `"uat"`，`done` → `"done"`，`blocked` → `"blocked"`

- [x] Task 4: 单元测试——100% Transition 覆盖 (AC: #4)
  - [x] 4.1 创建 `tests/unit/test_state_machine.py`
  - [x] 4.2 测试 `activate_initial_state()` 后状态为 `queued`
  - [x] 4.3 为每个合法 transition 编写独立测试（14 个正向 + 11 个 escalate = 25 个）
  - [x] 4.4 为每个状态的非法 transition 编写拒绝测试（9 个），断言 `current_state_value` 不变
  - [x] 4.5 测试 Happy path 完整流程：`queued` → ... → `done`
  - [x] 4.6 测试 Convergent Loop 路径：`reviewing` → `fixing` → `reviewing` → `review_pass` 和 `validating` → `creating` → `validating`
  - [x] 4.7 测试 `from_config()` 构建与阶段名校验（顺序正确 / 缺阶段 / 多阶段 / 错名 / 旧名）
  - [x] 4.8 测试 `save_story_state()` 持久化（配合 `initialized_db_path` fixture），验证不自动 commit

- [x] Task 5: 集成测试——状态转换 + SQLite 持久化 (AC: #2)
  - [x] 5.1 创建 `tests/integration/test_state_persistence.py`
  - [x] 5.2 在测试中先插入一条 `StoryRecord`，再执行完整流程：创建状态机 → send 事件 → 调用 `save_story_state()` → `await db.commit()` → 读回 SQLite 验证
  - [x] 5.3 验证每次 transition 后 `get_story()` 返回正确的 status 和 current_phase

- [x] Task 6: 代码质量验证
  - [x] 6.1 `uv run ruff check src/ato/state_machine.py` — 通过
  - [x] 6.2 `uv run mypy src/ato/state_machine.py` — 通过
  - [x] 6.3 `uv run pytest tests/unit/test_state_machine.py tests/integration/test_state_persistence.py -v` — 60 passed
  - [x] 6.4 确认所有既有测试仍通过：`uv run pytest` — 225 passed, 0 regressions

## Dev Notes

### 核心实现模式

**python-statemachine 3.0 关键规则：**

1. **必须使用 `StateMachine` 基类**（非 `StateChart`）——`StateMachine` 在非法 transition 时自动抛 `TransitionNotAllowed`（`allow_event_without_transition=False`）
2. **Async 初始化模式**——本 story 会定义 async `on_enter_*` / `on_exit_*` 回调；在 async 上下文中 `__init__` 不会自动激活初始状态，创建后必须：
   ```python
   sm = StoryLifecycle()
   await sm.activate_initial_state()  # 必须！否则 current_state 无效
   ```
3. **PersistentModel 不直接写 SQLite**——只更新内存。持久化模式：
   ```python
   # TransitionQueue consumer 内部（Story 2A.2 实现）
   await sm.send(event)                                          # 内存状态更新
   await save_story_state(db, story_id, sm.current_state_value)  # 显式持久化
   await db.commit()
   ```
4. **回调命名约定自动绑定**——`on_enter_<state_id>()`, `on_exit_<state_id>()`, `on_<event>()`
5. **依赖注入**——回调参数声明 `event_data`, `source`, `target`, `state` 等，引擎自动注入
6. **`current_state` 已弃用**——持久化和断言统一使用 `current_state_value`；需要读取当前激活状态集合时使用 `configuration`

### 开始前置条件

- **必须先完成 Story 1.3：** 当前工作树中的 `src/ato/config.py` 仍是 stub。开始 2A.1 前，必须先有 `ATOSettings`、`PhaseDefinition`、`build_phase_definitions()` 可用。
- **先对齐 phase 名称：** Story 1.3 当前示例阶段序列使用 `review_passed` / `qa` 且缺少 `regression`；本 story 的规范阶段为 `creating → validating → dev_ready → developing → reviewing → fixing → qa_testing → uat → merging → regression → done`。若配置仍未对齐，不要在 2A.1 中引入兼容别名，先修正 1.3。

**StoryLifecycle 状态图设计参考：**

```
queued ──start_create──→ creating
creating ──create_done──→ validating
validating ──validate_pass──→ dev_ready
validating ──validate_fail──→ creating     ← Convergent Loop 回退
dev_ready ──start_dev──→ developing
developing ──dev_done──→ reviewing
reviewing ──review_pass──→ qa_testing
reviewing ──review_fail──→ fixing          ← Convergent Loop
fixing ──fix_done──→ reviewing             ← re-review
qa_testing ──qa_pass──→ uat
qa_testing ──qa_fail──→ fixing          ← QA Convergent Loop
uat ──uat_pass──→ merging
merging ──merge_done──→ regression
regression ──regression_pass──→ done
* ──escalate──→ blocked                    ← 多个状态可 escalate（MVP sink state）
```

注意：`blocked` 在本 story 中仅作为升级后的停泊状态，**不实现 `unblock` 或“回到前一状态”的 metadata 持久化**。审批驱动的恢复路径由 Epic 4 / Epic 5 统一定义。

**状态 → StoryStatus 映射表：**

| 状态机阶段 | StoryStatus (status 列) | 说明 |
|-----------|------------------------|------|
| `queued` | `"backlog"` | 等待启动 |
| `creating` | `"planning"` | 创建阶段 |
| `validating` | `"planning"` | 验证阶段 |
| `dev_ready` | `"ready"` | 就绪 |
| `developing` | `"in_progress"` | 开发中 |
| `reviewing` | `"review"` | 审查中 |
| `fixing` | `"review"` | 修复中（仍属审查） |
| `qa_testing` | `"in_progress"` | QA 中 |
| `uat` | `"uat"` | 用户验收 |
| `merging` | `"in_progress"` | 合并中 |
| `regression` | `"in_progress"` | 回归测试 |
| `done` | `"done"` | 完成 |
| `blocked` | `"blocked"` | 阻塞 |

### 已有代码复用

**直接复用（不修改）：**
- `src/ato/models/db.py` → `get_story(db, story_id)` 读回验证
- `src/ato/models/db.py` → `insert_story(db, story)` 创建初始 story 记录
- `src/ato/models/schemas.py` → `StoryRecord`, `StoryStatus`, `StateTransitionError`
- `tests/conftest.py` → `db_path`, `initialized_db_path` fixtures

**需要谨慎调整：**
- `src/ato/models/db.py` → `update_story_status()` 当前会立即 `commit()`；若继续直接调用，会破坏 TransitionQueue 统一事务边界。2A.1 需要补一个**不自动 commit** 的状态更新路径，而不是在状态机层重复写 SQL。

**不要重复造轮：**
- ❌ 不要在 state_machine.py 中自己写 SQLite 操作——调用 db.py 中已有的 CRUD
- ❌ 不要创建新的 Pydantic model——使用已有的 StoryRecord
- ❌ 不要自己实现日志——使用 structlog

### 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/ato/state_machine.py` | **重写** | 从 1 行 docstring 扩展为完整状态机实现 |
| `src/ato/models/db.py` | **修改** | 提供不自动 `commit()` 的 story 状态更新路径，供 `save_story_state()` 和后续 TransitionQueue 复用 |
| `tests/unit/test_state_machine.py` | **新建** | ~20+ 个 transition 单元测试 |
| `tests/integration/test_state_persistence.py` | **新建** | 状态转换 + SQLite 持久化集成测试 |

**不应修改的文件：**
- `src/ato/models/schemas.py` — StoryStatus 保持现有 8 个值不变（current_phase 列已是 TEXT 类型）
- `src/ato/models/__init__.py` — 需评估是否导出新的公共接口

### 依赖关系

**前置（已完成）：**
- ✅ Story 1.1：项目脚手架、structlog 配置、python-statemachine 依赖已安装
- ✅ Story 1.2：SQLite 持久化层、CRUD 函数、Pydantic models
- ⚠ Story 1.3：配置引擎定义了本 story 需要消费的 `PhaseDefinition`，当前必须先交付并对齐阶段命名

**后续依赖本 story：**
- Story 2A.2（TransitionQueue）需要 StoryLifecycle 实例和 save_story_state
- Story 2A.3（Orchestrator 事件循环）需要状态机驱动 story 推进
- Epic 3（Convergent Loop）需要 reviewing ↔ fixing 循环
- Epic 5（崩溃恢复）需要持久化的状态数据

### Project Structure Notes

- `state_machine.py` 已存在于 `src/ato/`，当前只有 1 行 docstring，本 story 负责完整实现
- 模块依赖方向：`state_machine.py` 可依赖 `config.py`、`models/db.py` 和 `models/schemas.py`，但不依赖 `core.py`、`transition_queue.py` 或 `adapters/`
- 测试文件遵循 `tests/unit/test_<module>.py` 和 `tests/integration/test_<feature>.py` 命名规范

### 关键技术注意事项

1. **asyncio 模式自动检测**——只要有任何 `async def` 回调，python-statemachine 3.0 自动进入 async 模式
2. **TransitionNotAllowed 异常**——由 `StateMachine` 基类自动抛出，可通过 `sm.TransitionNotAllowed` 访问（类属性）
3. **`allowed_events` 属性**——`sm.allowed_events` 返回当前状态的合法事件列表，可用于测试验证
4. **Listener 机制**——可附加 listener 审计所有 transition：`sm.add_listener(auditor)` 对测试极为有用
5. **asyncio.gather 禁用**——项目规则要求用 `asyncio.TaskGroup`，但本 story 中状态机是串行操作，不涉及并发
6. **pytest-asyncio auto mode**——`pyproject.toml` 已配置 `asyncio_mode=auto`，测试函数声明 `async def` 即可
7. **`from_config()` 不能忽略参数**——本 story 至少要验证 `phase_definitions` 的阶段序列与规范状态图一致；若配置阶段仍未对齐，应先修复 Story 1.3，而不是在 2A.1 中硬编码并忽略输入
8. **`ato status` 当前未实现**——本 story 的可验证交付物是 SQLite 中一致的 `status/current_phase`，供后续 CLI/TUI 状态展示直接消费

### References

- [Source: _bmad-output/planning-artifacts/epics.md — Epic 2A, Story 2A.1]
- [Source: _bmad-output/planning-artifacts/architecture.md — Decision 8: python-statemachine 3.0 Async 集成]
- [Source: _bmad-output/planning-artifacts/architecture.md — TransitionQueue 设计与连接策略]
- [Source: _bmad-output/planning-artifacts/prd.md — FR3 状态机自动推进, FR4 并发无冲突]
- [Source: _bmad-output/planning-artifacts/architecture.md — StoryLifecycle 状态图与阶段流]
- [Source: _bmad-output/project-context.md — python-statemachine 3.0 async 集成规则]
- [Source: src/ato/models/db.py — update_story_status, get_story, insert_story]
- [Source: src/ato/models/schemas.py — StoryStatus, StoryRecord, StateTransitionError]

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6 (1M context)

### Debug Log References

- `blocked` 状态设为 `State(final=True)` 以满足 python-statemachine 3.0 "非 final 状态必须有出站转换"的约束。MVP 中 blocked 为 sink state，Epic 4/5 实现 unblock 时需改回非 final 并添加出站转换。
- `from_config()` 使用 `HasPhaseInfo` Protocol 接收阶段定义（需 `name`、`next_on_success`、`next_on_failure` 三个属性），校验阶段名序列与 transition 均与规范一致。Story 1.3 的 `PhaseDefinition` dataclass 已验证兼容。
- `on_enter_state` / `on_exit_state` 使用通用回调（而非每个状态独立回调），减少样板代码。

### Completion Notes List

- ✅ 实现 StoryLifecycle 状态机：13 个状态、15 种转换事件（含 qa_fail + escalate 从 11 个状态到 blocked）
- ✅ 实现 save_story_state() 持久化桥接：PHASE_TO_STATUS 映射 + 不自动 commit
- ✅ 修改 update_story_status() 增加 `commit: bool = True` 参数 + rowcount 检查
- ✅ from_config() 校验阶段名 + transition（next_on_success / next_on_failure）与 CANONICAL_TRANSITIONS 一致
- ✅ send() override 记录非法 transition 拒绝日志（AC #3）
- ✅ 55 个单元测试 + 5 个集成测试（225 passed, 0 regressions）
- ✅ Story 1.3 合并后验证：真实 PhaseDefinition → from_config() 端到端通过

### File List

- `src/ato/state_machine.py` — **重写**：StoryLifecycle（13 状态、15 事件）、save_story_state、PHASE_TO_STATUS、CANONICAL_PHASES、CANONICAL_TRANSITIONS、HasPhaseInfo Protocol、send() rejection log
- `src/ato/models/db.py` — **修改**：update_story_status() 增加 `commit` 参数 + rowcount 检查
- `tests/unit/test_state_machine.py` — **新建**：57 个状态机单元测试
- `tests/integration/test_state_persistence.py` — **新建**：5 个状态转换+SQLite持久化集成测试
- `ato.yaml.example` — **修改**：阶段名对齐（review_passed→删除, qa→qa_testing, 新增 regression）
- `tests/unit/test_config.py` — **修改**：阶段名断言对齐
- `tests/integration/test_config_workflow.py` — **修改**：阶段名断言对齐
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — **修改**：2a-1 状态更新
- `_bmad-output/implementation-artifacts/2a-1-story-state-machine-progression.md` — **修改**：任务标记、状态图、Dev Agent Record

### Change Log

- 2026-03-24: Story 2A.1 完整实现——StoryLifecycle 状态机 + save_story_state 持久化桥接 + 100% transition 覆盖测试
- 2026-03-24: Code review R1 修复——rowcount 检查、transition 校验、rejection log
- 2026-03-24: Code review R2 修复——qa_fail transition、send() 参数透传、story spec 对齐
