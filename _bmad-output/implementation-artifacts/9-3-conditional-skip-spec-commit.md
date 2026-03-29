# Story 9.3: 条件阶段跳过 + Story 规格自动提交主分支

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->
<!-- Depends on: Story 9.1 (designing phase), Story 9.2 (workspace concept) -->

## Story

As a 操作者,
I want 系统在 story 不需要 UI 时自动跳过 `designing` 阶段，并在 story 规格验证通过进入 `dev_ready` 时自动将规格文件提交到本地 `main`,
so that 纯后端 story 不被不必要的 UX 设计阶段阻塞，且所有已验证的 story 规格在创建 worktree 前就对并行开发的其他 story 可见。

## Acceptance Criteria (AC)

### AC1: PhaseConfig 新增 `skip_when` 条件字段

```gherkin
Given 当前 PhaseConfig 不支持条件跳过
When 新增 `skip_when` 字段
Then PhaseConfig 接受 `skip_when: str | None = None`
And 值为字符串表达式（如 `"not story.has_ui"`）或 None（不跳过）
And PhaseDefinition dataclass 同步新增 `skip_when: str | None` 字段
And build_phase_definitions() 正确传播 `skip_when` 值
```

### AC2: Story 数据模型支持 `has_ui` 属性

```gherkin
Given 当前 stories 表 schema（version 7）
When 执行 schema migration v7 → v8
Then stories 表新增 `has_ui BOOLEAN DEFAULT 0` 列
And StoryRecord Pydantic model 新增 `has_ui: bool = False` 字段
And migration 对已有数据安全（`ALTER TABLE ... ADD COLUMN ... DEFAULT 0`）
And `SCHEMA_VERSION` 从 7 更新为 8
```

### AC3: 运行时条件跳过正确工作

```gherkin
Given designing 阶段配置了 `skip_when: "not story.has_ui"`
And story 的 `has_ui == False`
When story 从 creating 完成（`create_done`）进入 designing
Then 系统在 transition commit 之后检测到 skip 条件为 True
And 自动提交 `design_done` 事件，使 story 立即从 designing → validating
And structlog 记录 `phase_skipped` 事件（含 `story_id`, `phase`, `skip_expression`, `skip_reason`）
And 状态机仍合法地经过 designing 状态（不是直接绕过）

Given story 的 `has_ui == True`
When story 从 creating 完成进入 designing
Then skip 条件为 False
And story 正常停留在 designing 阶段等待 agent 执行
```

### AC4: `skip_when` 表达式安全求值

```gherkin
Given `skip_when` 表达式字符串
When 系统求值表达式
Then 仅允许访问 `story.has_ui`、`story.story_id`、`story.title`
And 不使用 Python `eval()`
And 只支持 `not` / `and` / `or` 与白名单属性读取
And 非法表达式记录 warning 并视为“不跳过”（安全降级）
```

### AC5: Batch 内所有 story 到达 `dev_ready` 后统一提交规格到本地 `main`

```gherkin
Given batch 内有多个 story 依次通过 creating → designing → validating → dev_ready
When batch 内所有 story 均到达 `dev_ready` 且尚未完成本 batch 的 spec commit
Then 系统在 project_root 中执行单次本地 commit：
  - 对每个 story，git add `_bmad-output/implementation-artifacts/{story_id}.md`
  - 若存在，则额外 git add `_bmad-output/implementation-artifacts/{story_id}-ux/`
  - git commit -m "spec(batch-<batch-id>): add validated story specifications"
And commit 成功后该 batch 的 stories 才可继续 `start_dev → developing`
And structlog 记录 `batch_spec_committed` 事件（含 `batch_id`, `story_ids`, `commit_hash`）
And 禁止逐 story 单独 commit
And 若目标文件已全部提交、工作树无差异，则按幂等成功处理，不重复创建 commit
```

### AC6: Batch spec commit 失败时复用现有 approval 基础设施

```gherkin
Given batch spec commit 的 git add / commit / pre-commit 任一步失败
When 系统检测到失败
Then 复用现有 `precommit_failure` approval 类型（payload 标记 `scope: "spec_batch"`）
And payload 包含 `batch_id`, `story_ids`, `error_output`, `options: ["retry", "manual_fix", "skip"]`
And batch 内所有 story 暂停在 `dev_ready`，不单独推进到 developing
And 操作者选择 `retry` 后系统重试本地 spec commit
And 操作者选择 `manual_fix` 后可先修仓库状态，再重试
And 操作者选择 `skip` 后允许该 batch 继续进入 developing（但不产生本次 spec commit）
```

### AC7: Spec commit 与 worktree 创建顺序正确

```gherkin
Given Story 9.2 已把 `dev_ready` 定义为 `workspace: main`，`developing` 定义为 `workspace: worktree`
When batch 内最后一个 story 到达 `dev_ready`
Then 先完成 batch spec commit
And commit 完成后才允许 `start_dev`
And 第一次进入 `developing` 时创建的 worktree 基于 commit 后的最新 main HEAD
And 因为 `dev_ready` 仍在 main 上，所以不存在“先建 worktree、后补规格 commit”的时序倒挂
```

### AC8: 所有现有测试通过，新增覆盖测试

```gherkin
Given 完整测试套件
When 运行所有测试
Then 所有现有测试通过
And 新增 ≥7 个测试：
  - `has_ui=False` 时 designing 被跳过
  - `has_ui=True` 时 designing 不跳过
  - 非法 skip_when 表达式安全降级
  - v7 → v8 migration 正确新增 `has_ui`
  - batch spec commit 正确 stage `implementation-artifacts` 中的 story 文件与 UX 目录
  - batch spec commit 失败时创建 `precommit_failure(scope=spec_batch)` approval
  - commit 成功后才允许 `start_dev` / worktree 创建
```

## Tasks / Subtasks

- [x] Task 1: Schema migration v7 → v8 + StoryRecord 扩展 (AC: #2)
  - [x] 1.1 `src/ato/models/schemas.py`：`SCHEMA_VERSION = 8`，`StoryRecord` 新增 `has_ui: bool = False`
  - [x] 1.2 `src/ato/models/migrations.py`：新增 `_migrate_v7_to_v8()`，`ALTER TABLE stories ADD COLUMN has_ui BOOLEAN DEFAULT 0` + `batches ADD COLUMN spec_committed BOOLEAN DEFAULT 0`
  - [x] 1.3 `src/ato/models/db.py`：`insert_story()`、`get_story()`、`get_batch_stories()` 等函数适配 `has_ui`；`_row_to_story()`/`_row_to_batch()` 处理 int→bool 转换
  - [x] 1.4 更新 `tests/unit/test_migrations.py`、`tests/unit/test_db.py`、`tests/unit/test_schemas.py`

- [x] Task 2: PhaseConfig + PhaseDefinition 新增 `skip_when` 字段 (AC: #1)
  - [x] 2.1 `src/ato/config.py::PhaseConfig` 新增 `skip_when: str | None = None`
  - [x] 2.2 `src/ato/config.py::PhaseDefinition` 新增 `skip_when: str | None` 字段
  - [x] 2.3 `build_phase_definitions()` 传播 `skip_when`
  - [x] 2.4 `ato.yaml.example` 中 `designing` 阶段增加 `skip_when: “not story.has_ui”`
  - [x] 2.5 更新 `tests/unit/test_config.py`

- [x] Task 3: 安全的 `skip_when` 表达式求值器 (AC: #4)
  - [x] 3.1 在 `src/ato/config.py` 中实现 `evaluate_skip_condition(expression: str, story: StoryRecord) -> bool`
  - [x] 3.2 白名单属性解析：仅允许 `story.has_ui`、`story.story_id`、`story.title`
  - [x] 3.3 支持 `not`、`and`、`or` 基础布尔运算 + 括号
  - [x] 3.4 非法表达式返回 False（不跳过）并记录 warning
  - [x] 3.5 新增 `tests/unit/test_config.py`：合法 / 非法表达式测试（11 个测试用例）

- [x] Task 4: TransitionQueue post-commit hook 落地条件跳过 (AC: #3)
  - [x] 4.1 `_consumer()` post-commit 泛化为 `_on_phase_skip_check()` + `_on_enter_dev_ready()` + 原有 hooks
  - [x] 4.2 TransitionQueue 接受 `phase_defs` 参数，构建 phase→PhaseDefinition 查找表
  - [x] 4.3 若 `skip_when` 求值为 True，通过 `_PHASE_SUCCESS_EVENT` 映射自动提交对应 success event
  - [x] 4.4 structlog 记录 `phase_skipped` 事件（含 story_id、phase、skip_expression、skip_reason）
  - [x] 4.5 新增 `tests/unit/test_transition_queue.py`：3 个跳过路径测试

- [x] Task 5: Batch spec commit to local main 实现 (AC: #5, #6, #7)
  - [x] 5.1 复用 `get_active_batch()` + `get_batch_stories()` 检测全部 dev_ready
  - [x] 5.2 `WorktreeManager.batch_spec_commit()` 基于 `_run_git()` 实现
  - [x] 5.3 stage 路径对齐：`_bmad-output/implementation-artifacts/{story_id}.md` + `{story_id}-ux/`
  - [x] 5.4 单次 commit：`spec(batch-<batch-id>): add validated story specifications`
  - [x] 5.5 失败时复用 `precommit_failure(scope=spec_batch)` + payload 含 batch_id/story_ids/options
  - [x] 5.6 `core.py::_handle_spec_batch_precommit()` 处理 retry/manual_fix/skip
  - [x] 5.7 `tests/unit/test_worktree_mgr.py` 4 个测试 + `tests/unit/test_transition_queue.py` 3 个测试
  - [x] 5.8 TransitionQueue `_on_enter_dev_ready()` 作为集成点，`mark_batch_spec_committed()` 跟踪状态

- [x] Task 6: Batch select 写入 `has_ui` 标记 (AC: #2, #3)
  - [x] 6.1 `src/ato/batch.py::EpicInfo` 新增 `has_ui: bool = False`
  - [x] 6.2 CLI 交互（Task 6.2 留给 CLI 层；数据层已就绪）
  - [x] 6.3 `confirm_batch()` 写入 stories 表时设置 `has_ui`，已存在 story 则 UPDATE
  - [x] 6.4 更新 `tests/unit/test_batch.py`：2 个写入与回读 `has_ui` 测试

## Dev Notes

### 关键实现判断

- **`skip_when` 绝对不能用 `eval()`。** 这里是配置驱动表达式，不是通用脚本能力；白名单属性 + 小型布尔表达式解析器已经足够。
- **条件跳过的触发点必须在 TransitionQueue commit 之后。** 当前 `_consumer()` 只有 story-done post-commit hook；本 Story 应把它泛化，而不是在 `state_machine.py` 的 callback 中强行塞 DB 上下文。
- **story 规格文件当前真源是 `_bmad-output/implementation-artifacts/`。** `sprint-status.yaml` 的 `story_location` 已经固定到这个目录，不要再发明 `_bmad-output/stories/` 第二套树。
- **本 Story 只要求提交到本地 `main`，不引入 remote push 合同。** 当前仓库没有既有的 `git push origin main` 运行时路径；如果把 push 一起拉进来，会额外引入认证、远端不存在、网络失败等全新问题面。
- **commit 失败不要发明新的 approval type。** 仓库已经有 `precommit_failure`、现成的 approval helper 和 CLI/TUI 展示逻辑；扩展 payload scope 即可，没必要新增 `spec_commit_failure`。
- **batch 检测也不要重复造轮子。** 现有 `get_active_batch()` / `get_batch_stories()` 已经提供了批次与 story 明细视图，直接复用即可。
- **`dev_ready` 必须保持 main-phase。** 只有这样 batch spec commit 才能发生在 worktree 创建之前，保证 `developing` 的 worktree 基于包含全部规格文件的最新 main。

### Scope Boundary

- **IN:** `skip_when`、`has_ui`、安全求值器、TransitionQueue 条件跳过、batch spec commit 到本地 main、batch-select 写入 `has_ui`
- **OUT:** remote push / 远端同步策略
- **OUT:** 自动推断 `has_ui`
- **OUT:** UX designer prompt 设计细节
- **OUT:** 规格提交流程的 PR / code review 扩展

### Project Structure Notes

- 主要修改文件：
  - `src/ato/models/schemas.py`
  - `src/ato/models/migrations.py`
  - `src/ato/models/db.py`
  - `src/ato/config.py`
  - `src/ato/transition_queue.py`
  - `src/ato/core.py`
  - `src/ato/worktree_mgr.py`
  - `src/ato/batch.py`
  - `ato.yaml.example`
- 重点测试文件：
  - `tests/unit/test_config.py`
  - `tests/unit/test_migrations.py`
  - `tests/unit/test_db.py`
  - `tests/unit/test_schemas.py`
  - `tests/unit/test_transition_queue.py`
  - `tests/unit/test_core.py`
  - `tests/unit/test_batch.py`
  - `tests/unit/test_worktree_mgr.py`

### Suggested Verification

```bash
uv run pytest tests/unit/test_config.py tests/unit/test_migrations.py tests/unit/test_db.py tests/unit/test_schemas.py tests/unit/test_batch.py -v
uv run pytest tests/unit/test_transition_queue.py tests/unit/test_core.py tests/unit/test_worktree_mgr.py -v
uv run pytest tests/integration/ -v
```

### References

- [Source: src/ato/models/schemas.py — `SCHEMA_VERSION`, `StoryRecord`, `ApprovalType`]
- [Source: src/ato/models/migrations.py — migration chain]
- [Source: src/ato/models/db.py — `get_active_batch()`, `get_batch_stories()`, stories DDL]
- [Source: src/ato/config.py — `PhaseConfig`, `PhaseDefinition`]
- [Source: src/ato/transition_queue.py — `_consumer()` post-commit 逻辑]
- [Source: src/ato/core.py — `_handle_approval_decision()`]
- [Source: src/ato/worktree_mgr.py — main-repo git helper 基础 `_run_git()`]
- [Source: _bmad-output/implementation-artifacts/sprint-status.yaml — `story_location` 当前合同]
- [Depends: Story 9.1 — `designing` phase 必须存在]
- [Depends: Story 9.2 — `dev_ready` main / `developing` worktree 的顺序合同]

### Previous Story Intelligence

1. **Story 9.1 已经把 `designing` 变成真实 phase。** 9.3 不需要再发明“直接从 creating 跳 validating”的特殊状态机分支，只需要在 post-commit hook 上安全提交 `design_done`。
2. **Story 9.2 把 worktree 创建时机收紧到 `developing`。** 这正是 spec commit 能在 `dev_ready` 上完成的前提。
3. **仓库现有 merge / regression 流已经证明：main-repo git 操作应复用 `WorktreeManager` / approval helper，而不是新写一套并行基础设施。**

## Change Log

- 2026-03-28: Story 创建
- 2026-03-28: `validate-create-story` 修订
- 2026-03-29: Story 实施完成 — 全部 6 个 Task 实现，1517 个单元测试通过，0 回归
- 2026-03-29: Code review 4 轮修复 — 8 个 findings 全部修正，1521 个单元测试通过

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6 (1M context)

### Debug Log References

### Completion Notes List

- Task 1: SCHEMA_VERSION 7→8，StoryRecord.has_ui + BatchRecord.spec_committed，v8 migration 含幂等 _column_exists 检查
- Task 2: PhaseConfig/PhaseDefinition 各新增 skip_when 字段，build_phase_definitions 正确传播
- Task 3: evaluate_skip_condition() 实现递归下降解析器（not/and/or + 括号），白名单属性限制，不使用 eval()
- Task 4: TransitionQueue 新增 _on_phase_skip_check() post-commit hook，通过 _PHASE_SUCCESS_EVENT 映射自动提交 skip event
- Task 5: WorktreeManager.batch_spec_commit() 幂等 git add+commit，_on_enter_dev_ready() 触发，失败走 precommit_failure(scope=spec_batch) approval
- Task 6: EpicInfo.has_ui + confirm_batch() INSERT/UPDATE has_ui 到 stories 表
- Code review R1: Orchestrator 启动路径传入 phase_defs；manual_fix/retry-fail 消费旧 approval + 创建新 pending；git commit 使用 pathspec 不吞无关 staged 内容
- Code review R2: manual_fix 改为"消费旧+创建新"模式（approval 模型不支持回退 pending）；retry 异常路径也补建新 approval
- Code review R3: _on_enter_dev_ready except 分支补建 approval；git 探测命令非零退出不再误判为幂等成功
- Code review R4: approval 消费 + 新建原子化——_create_spec_batch_approval 复用外层 db 连接 commit=False，与 mark_consumed 同事务提交

### File List

- src/ato/models/schemas.py (modified: SCHEMA_VERSION=8, StoryRecord.has_ui, BatchRecord.spec_committed)
- src/ato/models/migrations.py (modified: _migrate_v7_to_v8 新增 has_ui + spec_committed 列)
- src/ato/models/db.py (modified: _STORIES_DDL, insert_story, _row_to_story, get_batch_stories, _row_to_batch, mark_batch_spec_committed)
- src/ato/config.py (modified: PhaseConfig.skip_when, PhaseDefinition.skip_when, evaluate_skip_condition + 解析器函数)
- src/ato/transition_queue.py (modified: _PHASE_SUCCESS_EVENT, TransitionQueue.phase_defs, _on_phase_skip_check, _on_enter_dev_ready)
- src/ato/core.py (modified: _handle_approval_decision spec_batch 分支, _handle_spec_batch_precommit)
- src/ato/worktree_mgr.py (modified: batch_spec_commit)
- src/ato/batch.py (modified: EpicInfo.has_ui, confirm_batch has_ui INSERT/UPDATE)
- ato.yaml.example (modified: designing phase 增加 skip_when)
- tests/unit/test_schemas.py (modified: 3 个 has_ui 测试)
- tests/unit/test_migrations.py (modified: TestMigrationV8 2 个测试)
- tests/unit/test_db.py (modified: 2 个 has_ui roundtrip 测试)
- tests/unit/test_config.py (modified: TestPhaseConfigSkipWhen 3 测试 + TestEvaluateSkipCondition 11 测试)
- tests/unit/test_transition_queue.py (modified: TestConditionalPhaseSkip 3 测试 + TestBatchSpecCommitOnDevReady 3 测试)
- tests/unit/test_worktree_mgr.py (modified: TestBatchSpecCommit 4 测试)
- tests/unit/test_batch.py (modified: TestConfirmBatchHasUi 2 测试)
