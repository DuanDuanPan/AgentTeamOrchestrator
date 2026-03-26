# Story 4.2: Merge Queue 与 Regression 安全管理

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a 操作者,
I want merge queue 按顺序安全合并代码，regression 失败时自动冻结,
So that main 分支始终保持可用状态。

## Acceptance Criteria

1. **AC1 — Merge 授权创建与顺序执行**
   ```
   Given story 通过 UAT 进入 `merging`
   When Orchestrator 发现该 story 尚无 pending `merge_authorization` approval 且未在 merge_queue 中
   Then 创建 approval（approval_type=merge_authorization, options=approve/reject）
   And 在 approval 决策前不直接执行 git rebase/merge，也不启动普通 `merging` structured_job
   When 操作者批准 merge（approval_type=merge_authorization, decision=approve）
   Then 系统按顺序执行 rebase 和 merge，一次只处理一个（FR31）
   And merge 操作在 story 对应的 worktree 中执行
   ```

2. **AC2 — 并发 Merge 请求串行化**
   ```
   Given 多个 story 几乎同时通过 UAT 并被批准 merge
   When merge 请求并发到达
   Then merge queue 严格串行化处理，按 approval decided_at 时间排序，不出现竞争条件
   And 若 decided_at 相同则按 queue entry 自增 id 作为稳定 tie-breaker
   And 当前正在 merge 的 story 完成前，后续 story 排队等待
   ```

3. **AC3 — Regression 测试自动触发**
   ```
   Given merge 完成后（rebase + fast-forward merge 到 main 成功）
   When 系统自动触发 regression 测试（作为 Structured Job 调度）
   Then 在 main 分支上执行项目配置的回归测试命令
   And regression 全部通过 → 提交 TransitionEvent(regression_pass) → story 标记为 done
   ```

4. **AC4 — Regression 失败冻结 Queue**
   ```
   Given regression 测试失败
   When 系统检测到失败（exit_code != 0）
   Then 自动冻结 merge queue（设置 frozen=True），阻止后续 merge（FR32, NFR10）
   And 创建紧急 approval（approval_type=regression_failure, risk_level=high）
   And approval 选项：revert / fix_forward / pause
   And 触发 URGENT 级通知（terminal bell）
   ```

5. **AC5 — Worktree Rebase 冲突处理**
   ```
   Given worktree rebase 产生冲突（FR52）
   When git rebase 返回非零退出码且 stderr 包含 "CONFLICT"
   Then 通过现有 adapter + SubprocessManager 调度项目配置中的修复 agent 自动解决冲突
   And 解决后执行 git rebase --continue，成功则继续 merge 流程
   And agent 解决失败（第二次 rebase 仍冲突）则 escalate 给操作者
   And escalation 创建 approval（approval_type=rebase_conflict, 选项：manual_resolve / skip / abandon）
   ```

6. **AC6 — Pre-commit Hook 失败自动修复**
   ```
   Given merge 流程中 pre-commit hook 失败（lint/format/type check）
   When git commit 返回非零退出码
   Then 调度 agent 自动修复（基于项目 ato.yaml 中配置的 lint/format/type-check 命令）
   And 修复后重新 commit
   And 自动修复失败则 escalate 给操作者，创建 approval（approval_type=precommit_failure）
   ```

7. **AC7 — Queue 解冻恢复**
   ```
   Given merge queue 被冻结（frozen=True）
   When 操作者处理完异常（regression_failure approval 的 decision 为 revert 或 fix_forward 且后续操作成功）
   Then merge queue 解冻（frozen=False），恢复正常合并流程
   And 排队中的 story 按原顺序继续处理
   ```

## Tasks / Subtasks

- [x] Task 1: Merge Queue 数据模型与 DB 层 (AC: #1, #2, #4, #7)
  - [x]1.1 在 `src/ato/models/schemas.py` 中新增：
    - `MergeQueueEntry(BaseModel)`: `story_id: str`, `approval_id: str`, `approved_at: datetime`, `enqueued_at: datetime`, `status: Literal["waiting", "merging", "regression_pending", "merged", "failed"]`, `regression_task_id: str | None = None`
    - `MergeQueueState(BaseModel)`: `frozen: bool = False`, `frozen_reason: str | None = None`, `frozen_at: datetime | None = None`, `current_merge_story_id: str | None = None`
    - 在 `ApprovalType` 中追加 `"rebase_conflict"` 类型
    - 在 `APPROVAL_TYPE_TO_NOTIFICATION` 中追加 `"rebase_conflict": "normal"`
    - 在 `APPROVAL_RECOMMENDED_ACTIONS` 中追加 `"rebase_conflict": "manual_resolve"`
  - [x]1.2 在 `src/ato/models/db.py` 中新增 DDL 和 CRUD：
    - `merge_queue` 表：`id INTEGER PRIMARY KEY`, `story_id TEXT NOT NULL UNIQUE`, `approval_id TEXT NOT NULL`, `approved_at TEXT NOT NULL`, `enqueued_at TEXT NOT NULL`, `status TEXT NOT NULL DEFAULT 'waiting'`（waiting / merging / regression_pending / merged / failed）, `regression_task_id TEXT`
    - `merge_queue_state` 表：`id INTEGER PRIMARY KEY CHECK (id = 1)`, `frozen INTEGER NOT NULL DEFAULT 0`, `frozen_reason TEXT`, `frozen_at TEXT`, `current_merge_story_id TEXT`（单例行，只有 1 条记录）
    - `enqueue_merge(db, story_id, approval_id, approved_at, enqueued_at) -> None`
    - `dequeue_next_merge(db) -> MergeQueueEntry | None` — 返回 status='waiting' 中 `ORDER BY approved_at ASC, id ASC` 的第一条，将其 status 更新为 'merging'
    - `mark_regression_dispatched(db, story_id, task_id) -> None` — 记录 `regression_task_id` 并将 status 更新为 `regression_pending`
    - `complete_merge(db, story_id, success: bool) -> None` — 更新 status 为 'merged' 或 'failed'
    - `get_merge_queue_state(db) -> MergeQueueState`
    - `set_current_merge_story(db, story_id: str | None) -> None`
    - `set_merge_queue_frozen(db, frozen: bool, reason: str | None) -> None`
    - `get_pending_merges(db) -> list[MergeQueueEntry]` — 返回 status='waiting' 的所有条目
    - `remove_from_merge_queue(db, story_id) -> None`
  - [x]1.3 在 `src/ato/models/migrations.py` 中新增迁移（v6→v7）：
    - `CREATE TABLE IF NOT EXISTS merge_queue (...)`
    - `CREATE TABLE IF NOT EXISTS merge_queue_state (...)`
    - `INSERT OR IGNORE INTO merge_queue_state (id, frozen) VALUES (1, 0)` — 初始化单例行
    - 递增 `PRAGMA user_version` 到 7

- [x] Task 2: MergeQueue 核心类实现 (AC: #1, #2, #3, #4, #5, #6, #7)
  - [x]2.1 新建 `src/ato/merge_queue.py`，实现 `MergeQueue` 类：
    - 构造参数：`db_path: Path`, `worktree_mgr: WorktreeManager`, `transition_queue: TransitionQueue`, `settings: ATOSettings`
    - 注意：当前仓库的 `SubprocessManager` 绑定单一 adapter；`MergeQueue` 不应持有一个固定 `subprocess_mgr` 覆盖所有 role，而应按 role / cli_tool 动态创建对应 adapter + manager
    - repo root 通过 `WorktreeManager` 暴露只读属性 / helper（或等价公共接口）获取，不要在 `MergeQueue` 中直接碰私有属性
    - **`async def enqueue(self, story_id: str, approval_id: str, approved_at: datetime) -> None`**
      - 写入 merge_queue 表（approved_at=approval.decided_at, enqueued_at=now）
      - structlog `merge_queue_enqueued`
    - **`async def process_next(self) -> bool`**（核心方法，由 Orchestrator poll cycle 调用）
      - 检查 frozen → True 则 return False
      - 检查 current_merge_story_id → 非 None 则 return False（上一个还在处理）
      - `dequeue_next_merge()` 取下一个 → None 则 return False
      - 设置 `current_merge_story_id`
      - 创建后台 merge worker task（例如 `_run_merge_worker(story_id)`）并立即返回
      - 返回 True（表示已成功启动一个 merge worker，**不要在 poll cycle 中同步阻塞等待完整 merge / regression 流程**）
    - **`async def _execute_merge(self, story_id: str) -> None`**
      - Step 1: Rebase worktree onto main（调用 worktree_mgr）
      - Step 2: 如果 rebase 冲突 → `_handle_rebase_conflict()`
      - Step 3: 合并到 main（fast-forward merge）
      - Step 4: 提交 `merge_done` transition → story 进入 `regression` 状态
      - Step 5: 调度 regression 测试（Structured Job）
      - Step 6: `mark_regression_dispatched(db, story_id, task_id)`，将 queue entry 切到 `regression_pending`
      - Step 7: 清除 `current_merge_story_id`；regression pass / fail 由 `_poll_cycle()` 的完成检测异步收敛
    - **`async def _rebase_worktree(self, story_id: str) -> tuple[bool, str]`**
      - 在 worktree 中执行 `git fetch origin main && git rebase origin/main`
      - 返回 (success, stderr)
      - 检测 "CONFLICT" 关键字判断冲突
    - **`async def _merge_to_main(self, story_id: str) -> bool`**
      - checkout main → `git merge --ff-only <branch>`
      - fast-forward only，非 ff 则报错（rebase 后应始终可 ff）
    - **`async def _dispatch_regression_test(self, story_id: str) -> str`**
      - 从 ato.yaml 读取 `regression_test_command`（默认 `uv run pytest`）
      - 通过 `SubprocessManager.dispatch_with_retry()` 调度 `regression` phase 的 Structured Job
      - `options["cwd"]` 指向 repo root（main 分支工作区），不要引入不存在的 `task_type` 字段
      - 返回 task_id
    - **`async def _handle_rebase_conflict(self, story_id: str, conflict_stderr: str) -> bool`**
      - 解析冲突文件列表（从 stderr）
      - 通过现有 adapter/role 配置调度修复 agent；禁止在 `merge_queue.py` 里直接拼 `claude -p`
      - 执行 `git rebase --continue`
      - 成功返回 True，仍失败则创建 rebase_conflict approval → 返回 False
    - **`async def _handle_precommit_failure(self, story_id: str, error_output: str) -> bool`**
      - 解析失败的 hook 类型
      - 调度 agent 自动修复（基于 ato.yaml 中 lint/format 命令）
      - 重新 commit
      - 成功返回 True，仍失败则创建 precommit_failure approval → 返回 False
    - **`async def _handle_regression_failure(self, story_id: str, task_result) -> None`**
      - `set_merge_queue_frozen(db, True, reason=f"regression failed for {story_id}")`
      - `create_approval(db, story_id=..., approval_type="regression_failure", payload_dict={"options": ["revert", "fix_forward", "pause"], ...}, risk_level="high", nudge=..., orchestrator_pid=...)`
      - `complete_merge(db, story_id, success=False)`
      - structlog `merge_queue_frozen`
    - **`async def unfreeze(self, reason: str) -> None`**
      - `set_merge_queue_frozen(db, False, None)`
      - structlog `merge_queue_unfrozen`

- [x] Task 3: Worktree Rebase 与 Merge 操作扩展 (AC: #1, #5)
  - [x]3.1 在 `src/ato/worktree_mgr.py` 中新增方法：
    - **`async def rebase_onto_main(self, story_id: str) -> tuple[bool, str]`**
      - 获取 worktree_path（从 DB 或 `.worktrees/{story_id}.branch`）
      - 在 worktree 中执行 `git fetch origin main`（如有 remote）或直接 `git rebase main`
      - cwd 设为 worktree_path
      - 返回 (success: bool, stderr: str)
      - 超时 120 秒（rebase 可能涉及大量文件）
    - **`async def continue_rebase(self, story_id: str) -> tuple[bool, str]`**
      - 在 worktree 中执行 `git rebase --continue`
      - 用于 agent 解决冲突后调用
    - **`async def abort_rebase(self, story_id: str) -> None`**
      - `git rebase --abort` — 失败回退
    - **`async def merge_to_main(self, story_id: str) -> tuple[bool, str]`**
      - 获取 worktree branch name
      - 在主仓库（非 worktree）执行 `git checkout main && git merge --ff-only <branch>`
      - fast-forward only 保证 main 线性历史
      - 成功后**不要立刻 cleanup**；`fix_forward` / `manual_resolve` 仍需要保留 branch 与 worktree，上下文只在 `regression_pass`、成功 `revert` 或明确 `abandon` 后清理
      - 返回 (success, stderr)
    - **`async def get_conflict_files(self, story_id: str) -> list[str]`**
      - 在 worktree 中执行 `git diff --name-only --diff-filter=U`
      - 返回冲突文件路径列表
  - [x]3.2 为 `WorktreeManager._run_git()` 增加可选 `timeout_seconds` 参数（默认仍为 30s）
    - `rebase_onto_main()` 使用 `settings.merge_rebase_timeout`
    - 其他 git 调用继续复用默认超时
    - 所有新 git 命令仍遵循三阶段清理协议

- [x] Task 4: Orchestrator / 状态机 / CLI 集成 (AC: #1, #2, #4, #7)
  - [x]4.1 在 story 进入 `merging` 阶段时创建 merge 授权 approval：
    - 复用 `create_approval()` 创建 `merge_authorization`
    - payload 显式包含 `options=["approve", "reject"]`
    - 幂等：已有 pending/decided 未消费 approval 或 queue entry 时不重复创建
    - `merging` 在本 Story 中是 approval-gated 的系统阶段，不启动普通 developer structured job
  - [x]4.2 在 `src/ato/core.py` 的 `_process_approval_decisions()` 中扩展：
    - 当 `approval_type == "merge_authorization"` 且 `decision == "approve"` 时：
      - 调用 `self._merge_queue.enqueue(story_id, approval_id, approval.decided_at)`
      - structlog `merge_authorization_consumed`
    - 当 `approval_type == "merge_authorization"` 且 `decision == "reject"` 时：
      - 通过 `TransitionQueue.submit(TransitionEvent(event_name="escalate"))` 将 story 置为 `blocked`
    - 当 `approval_type == "regression_failure"` 时：
      - `decision == "revert"` → 调度 agent 执行 `git revert`，成功后 `merge_queue.unfreeze("revert completed")` + cleanup worktree
      - `decision == "fix_forward"` → 通过 `TransitionQueue.submit(TransitionEvent(event_name="regression_fail"))` 将 story 从 `regression` 退回 `fixing`
      - `decision == "pause"` → 仅 log，queue 保持冻结状态
    - 当 `approval_type == "rebase_conflict"` 时：
      - `decision == "manual_resolve"` → 启动 Interactive Session 让操作者在 worktree 中手动解决
      - `decision == "skip"` → 从 merge queue 移除该 story
      - `decision == "abandon"` → 从 merge queue 移除 + story escalate
    - 当 `approval_type == "precommit_failure"` 时：
      - `decision == "retry"` → 重新调度 merge 流程
      - `decision == "manual_fix"` → 启动 Interactive Session
  - [x]4.3 在 `src/ato/state_machine.py` 中新增 `regression_fail = regression.to(fixing)` 转换
    - `fix_forward` 必须通过状态机事件进入 `fixing`，禁止直接写 `stories.current_phase`
    - 补齐相应状态机/TransitionQueue 测试
  - [x]4.4 在 `_poll_cycle()` 中新增 merge queue 驱动调用：
    - 在 approval 消费之后调用 `await self._merge_queue.process_next()`
    - 仅在 queue 非冻结且无正在进行的 merge 时执行下一个
    - `process_next()` 只负责 claim + schedule；完整 merge / regression 执行在后台 task 中进行，避免阻塞其他 story 的 approval、timeout 和 recovery 轮询
  - [x]4.5 在 Orchestrator `__init__` 或 `_startup()` 中初始化 MergeQueue 实例：
    - `self._merge_queue = MergeQueue(db_path, worktree_mgr, transition_queue, settings)`
  - [x]4.6 在 `src/ato/cli.py` 中补齐新 approval 类型/选项的 CLI 合同：
    - `_APPROVAL_TYPE_ICONS` / `_approval_summary()` 补入 `rebase_conflict`
    - `_DEFAULT_VALID_OPTIONS["regression_failure"]` 对齐为 `["revert", "fix_forward", "pause"]`
    - `_DEFAULT_VALID_OPTIONS["precommit_failure"]` 对齐为 `["retry", "manual_fix", "skip"]`
    - `_DEFAULT_VALID_OPTIONS["rebase_conflict"]` 设为 `["manual_resolve", "skip", "abandon"]`

- [x] Task 5: Regression 测试调度与结果处理 (AC: #3, #4)
  - [x]5.1 在 `MergeQueue._dispatch_regression_test()` 中：
    - 从 config 读取 `regression_test_command`（`ato.yaml` 中新增配置项，默认 `uv run pytest`）
    - 通过 `SubprocessManager.dispatch_with_retry()` 调度 `phase="regression"` 的任务
    - 返回 task_id 供后续 poll cycle 检测完成
  - [x]5.2 在 `_poll_cycle()` 中增加对 regression 任务完成的检测：
    - 新增 regression completion detector：只处理 `merge_queue.regression_task_id` 对应、且尚未提交 transition 的 completed tasks
    - 当检测到 regression phase 的 task completed 时：
      - exit_code == 0 → `transition_queue.submit(TransitionEvent("regression_pass", story_id))` → complete_merge(success=True)
      - exit_code != 0 → `_handle_regression_failure()`
  - [x]5.3 regression_test_command 配置：
    - 在 `src/ato/config.py` 的 `ATOSettings` 中新增字段 `regression_test_command: str = "uv run pytest"`
    - 该命令在 main 分支的仓库根目录下执行（非 worktree，因为 merge 已完成）

- [x] Task 6: 测试 (AC: #1-#7)
  - [x]6.1 `tests/unit/test_merge_queue.py`（新文件）：
    - `test_enqueue_adds_to_queue` — 入队写入 DB
    - `test_process_next_dequeues_by_approval_decided_at` — 按 approval.decided_at 排序；同时间按 id 稳定排序
    - `test_process_next_frozen_returns_false` — 冻结时不处理
    - `test_process_next_busy_returns_false` — 正在 merge 时不处理新条目
    - `test_rebase_success_merges_to_main` — rebase 成功后 ff merge
    - `test_rebase_conflict_dispatches_agent` — 冲突时调度 agent
    - `test_rebase_conflict_escalates_on_second_failure` — agent 解决失败后 escalate
    - `test_regression_pass_marks_done` — regression 通过标记 done
    - `test_regression_fail_freezes_queue` — regression 失败冻结 queue
    - `test_regression_fail_creates_urgent_approval` — 创建紧急 approval
    - `test_unfreeze_restores_processing` — 解冻后恢复处理
    - `test_precommit_failure_dispatches_fix` — pre-commit 失败调度修复
    - `test_precommit_failure_escalates_on_retry_failure` — 修复失败 escalate
    - `test_concurrent_enqueue_serialized` — 并发入队串行化
  - [x]6.2 `tests/unit/test_worktree_mgr.py`（追加）：
    - `test_rebase_onto_main_success` — rebase 成功返回 (True, "")
    - `test_rebase_onto_main_conflict` — 冲突返回 (False, stderr)
    - `test_continue_rebase_success` — --continue 成功
    - `test_merge_to_main_ff_only` — fast-forward merge 成功
    - `test_merge_to_main_not_ff_fails` — 非 ff 失败
    - `test_get_conflict_files` — 冲突文件列表正确解析
  - [x]6.3 `tests/unit/test_core.py`（追加）：
    - `test_merging_phase_creates_merge_authorization_once` — 进入 merging 时创建 approval，且幂等
    - `test_process_merge_authorization_enqueues` — merge_authorization 消费后入队
    - `test_process_merge_authorization_reject_escalates` — reject 决策将 story 置为 blocked
    - `test_process_regression_failure_revert` — revert 决策触发 git revert
    - `test_process_regression_failure_fix_forward_submits_regression_fail` — fix_forward 通过状态机事件退回 fixing
    - `test_poll_cycle_drives_merge_queue` — poll cycle 调用 process_next
  - [x]6.4 `tests/unit/test_db.py`（追加）：
    - `test_merge_queue_crud` — 入队/出队/完成/移除
    - `test_merge_queue_state_singleton` — 单例行 frozen 状态管理
    - `test_dequeue_order_uses_approved_at_then_id` — 按 approved_at / id 排序
  - [x]6.5 所有测试使用 mock git 命令（不调用真实 git）和内存 SQLite
  - [x]6.6 `tests/unit/test_cli_approval.py`（追加）：
    - `test_approval_summary_rebase_conflict` — 新 approval 类型摘要稳定
    - `test_regression_failure_default_options_align_story_4_2` — revert/fix_forward/pause 可被校验
    - `test_rebase_conflict_default_options` — manual_resolve/skip/abandon 可被校验
  - [x]6.7 `tests/unit/test_state_machine.py`（追加）：
    - `test_regression_fail_returns_to_fixing` — `regression` → `fixing`

- [x] Task 7: 配置扩展 (AC: #3)
  - [x]7.1 在 `src/ato/config.py` 中新增配置字段：
    - `regression_test_command: str = "uv run pytest"` — 回归测试命令
    - `merge_rebase_timeout: int = 120` — rebase 超时秒数
    - `merge_conflict_resolution_max_attempts: int = 1` — agent 解决冲突最大重试次数
  - [x]7.2 在 `ato.yaml.example` 中增加 merge 相关配置说明

## Dev Notes

### 已有基础设施（复用，不重建）

| 组件 | 文件 | 现状 |
|------|------|------|
| 状态机 merging/regression 状态 | `src/ato/state_machine.py:135-136` | `merging`/`regression` 状态已定义，`merge_done`/`regression_pass` transition 已存在 |
| WorktreeManager | `src/ato/worktree_mgr.py` | create/cleanup/has_new_commits 已实现，`_run_git()` 含三阶段清理 |
| TransitionQueue | `src/ato/transition_queue.py` | FIFO 串行消费，replay 支持 merging/regression 阶段 |
| SubprocessManager | `src/ato/subprocess_mgr.py` | dispatch_with_retry 支持 Structured Job 调度 |
| approval_helpers | `src/ato/approval_helpers.py` | `create_approval()` 统一 API，含 nudge + bell |
| ApprovalType | `src/ato/models/schemas.py:27-39` | 已含 `merge_authorization`/`regression_failure`/`precommit_failure` |
| NotificationLevel 映射 | `src/ato/models/schemas.py` | `regression_failure` → `urgent` 已配置 |
| Orchestrator poll cycle | `src/ato/core.py` | `_process_approval_decisions()` 已有 merge_authorization 的占位 log |
| RecoveryEngine | `src/ato/recovery.py:42-59` | `_PHASE_SUCCESS_EVENT["merging"]="merge_done"`, `["regression"]="regression_pass"` 已注册 |
| send_user_notification | `src/ato/nudge.py` | urgent/normal bell 已实现 |
| CLI approve 命令 | `src/ato/cli.py` | `ato approve` 支持前缀匹配，nudge 通知 |

### 缺失功能（本 Story 必须实现）

| 缺失 | 说明 |
|------|------|
| `MergeQueue` 类 | 核心——管理入队/出队/冻结/解冻/merge 执行流程 |
| `merge_queue` DB 表 | 持久化 merge 排队状态 |
| `merge_queue_state` DB 表 | 持久化 frozen 状态（单例行） |
| regression task 跟踪 | queue entry 需要记住 `regression_task_id`，否则 poll cycle 无法可靠识别哪一个 completed regression 属于当前 merge |
| `merge_authorization` 创建逻辑 | story 进入 `merging` 后必须真正创建 approval，否则队列永远不会收到请求 |
| `rebase_onto_main()` | WorktreeManager 缺少 rebase 操作 |
| `merge_to_main()` | WorktreeManager 缺少 ff merge 到 main 操作 |
| `get_conflict_files()` | 冲突文件列表解析 |
| `continue_rebase()` / `abort_rebase()` | rebase 流程控制 |
| regression 测试调度逻辑 | merge 后自动触发 regression Structured Job |
| regression 结果处理 | 成功→done / 失败→freeze + approval |
| 冲突自动解决调度 | agent dispatch 解决 rebase 冲突 |
| pre-commit 自动修复调度 | agent dispatch 修复 lint/format 错误 |
| Orchestrator merge queue 驱动 | poll cycle 中调用 process_next() |
| regression_failure 决策消费 | revert/fix_forward/pause 分支处理 |
| `regression_fail` 状态机事件 | 当前状态机只有 `regression_pass`，没有 regression → fixing 的回退路径 |
| `rebase_conflict` approval 类型 | 新增 approval 类型 |
| `rebase_conflict` / `precommit_failure` CLI 合同 | approval 图标 / 摘要 / 默认选项需补齐 |
| `regression_test_command` 配置 | ato.yaml 新增配置项 |

### 架构约束

1. **Merge 严格串行化**：merge_queue 保证同一时刻只有一个 story 在 merge。TransitionQueue 保证状态转换原子性，merge_queue 保证 merge 操作串行化——两者职责不同，不要合并
2. **授权时间排序是主合同**：队列顺序必须以 `approval.decided_at` 为准；`enqueued_at` 仅用于审计，不可替代排序键
3. **Fast-forward only**：merge 到 main 必须 `--ff-only`，保证线性历史。如果 ff 失败说明 rebase 有问题，应 abort 重试
4. **Regression 在 main 上运行**：merge 完成后 regression 测试在 main 分支执行（已合入的代码），不在 worktree 执行。本 Story 先交付这个 post-merge safety gate；后续 story 如要改顺序必须显式重规划
5. **`merging` 是 approval-gated 系统阶段**：进入 `merging` 后先创建 `merge_authorization` approval，由 MergeQueue 驱动真实 git 操作；不要把它当作普通 developer prompt phase
6. **fix_forward 必须经过状态机**：`regression_failure.fix_forward` 通过 `TransitionQueue.submit("regression_fail")` 回到 `fixing`；禁止直接写 DB 改 phase
7. **Agent 调度走 adapter / settings**：冲突修复与 pre-commit 自动修复都必须复用现有 adapter + SubprocessManager；禁止在新模块中手写 `claude -p`
8. **Frozen 状态持久化到 DB**：冻结状态必须在 `merge_queue_state` 表中持久化，crash 后恢复时 frozen 状态不丢失
9. **Approval 驱动解冻**：只有 regression_failure approval 的决策处理成功后才能解冻，不能自动解冻
10. **进程边界**：MergeQueue 生命周期绑定 Orchestrator 进程，不在 TUI/CLI 进程中实例化
11. **三阶段清理**：所有 git subprocess 必须使用 `_run_git()` 或遵循三阶段清理协议；rebase 调用要支持比默认 30s 更长的 timeout override
12. **禁止在 SQLite 写事务中 await 外部 IO**：merge 操作（git subprocess）与 DB 写入分开——先执行 git 操作，成功后再写 DB + commit
13. **MergeQueue 不得阻塞 poll loop**：完整 merge / regression 流程必须跑在后台 worker 中；Orchestrator 轮询仍要继续处理其他 story 的 approval、timeout 和恢复事件
14. **保留 worktree 直到 regression 闭环完成**：不要在 merge 成功时立刻 cleanup；否则 `fix_forward` / `manual_resolve` 会失去分支上下文
15. **structlog 事件命名**：`merge_queue_enqueued`, `merge_queue_dequeued`, `merge_queue_frozen`, `merge_queue_unfrozen`, `merge_rebase_started`, `merge_rebase_conflict`, `merge_ff_completed`, `regression_dispatched`, `regression_completed`
16. **ruff check + ruff format + mypy 全部通过后再提交**

### Scope Boundary

- **本 Story 交付范围**：MergeQueue 核心类 + DB 层 + merge_authorization 创建/消费 + Worktree merge 操作 + regression 调度与结果处理 + Orchestrator / CLI / 状态机集成 + 冲突/pre-commit 自动处理
- **不交付**：TUI merge 状态展示（Story 6.x）、更细粒度的 regression suite 选择策略（MVP 运行完整命令）、额外的 merge telemetry 面板
- **Story 4.5 关系**：4.2 先交付可运行的 post-merge regression freeze path；4.5 可以扩展 suite 策略、操作者处理选项和更精细的 merge 流程，但不能默默绕开 4.2 的授权/冻结安全合同

### 与 Story 4.1 的衔接

Story 4.1 已完成的 approval 系统是 4.2 的基础：
- `create_approval()` 统一 API 直接复用
- `_process_approval_decisions()` 中 `merge_authorization` 分支从 "仅 log" 升级为 "调用 merge_queue.enqueue()"
- `regression_failure` 分支从 "仅 log" 升级为具体的 revert/fix_forward/pause 处理
- `ato approve` CLI 命令已可用于操作者审批 merge 和处理 regression 失败
- 4.1 的 CLI approval 元数据（图标 / 摘要 / 默认 options）是 4.2 新 approval 类型必须继续对齐的入口

### Git 操作模式

**Rebase 流程：**
```bash
# 在 worktree 中执行
cd <worktree_path>
git fetch origin main        # 如果有 remote
git rebase main              # 本地仓库直接 rebase main
# 冲突时 → agent 解决 → git rebase --continue
# 仍冲突 → git rebase --abort → escalate
```

**Merge 流程：**
```bash
# 在主仓库中执行（非 worktree）
cd <repo_root>
git checkout main
git merge --ff-only <story-branch>
# ff 失败说明 rebase 不完整，属于 bug
```

**Regression 流程：**
```bash
# 在主仓库 main 分支执行
cd <repo_root>
# 确保在 main 分支
uv run pytest  # 或 ato.yaml 中配置的命令
```

### DB 迁移注意事项

- 使用 `CREATE TABLE IF NOT EXISTS` 创建新表（非 ALTER TABLE）
- `merge_queue_state` 表使用 `CHECK (id = 1)` 约束确保单例行
- `INSERT OR IGNORE` 初始化单例行，避免重复插入
- 迁移 v6→v7 在 `models/migrations.py` 中注册
- 所有新表的 TEXT 时间字段使用 ISO 8601 格式（与现有 approvals 表一致）

### Previous Story Intelligence

**来自 Story 4.1 的关键经验：**
- `asyncio.run()` + `get_connection()` 模式用于 CLI 端 DB 操作（不要退回同步 sqlite3）
- approval 创建后先 commit 再 nudge（禁止在写事务中 await 外部 IO）
- `consumed_at` 幂等模型防止跨重启重复消费
- `_send_nudge_safe()` best-effort 语义：nudge 失败只告警不回滚
- 统一 approval 创建 API（`create_approval()`）已重构了 4 处创建点，本 Story 新增的 approval 创建必须继续使用此 API

**来自 Story 3.2d（Convergent Loop）的经验：**
- ConvergentLoop 的 escalation approval 创建模式可参考：`create_approval(db, story_id=..., approval_type=..., payload_dict=...)`
- 找到后 fix → re-review 的循环模式类似于冲突解决 → 重试 rebase 的循环

**来自 Story 2b-4（Worktree Isolation）的经验：**
- `WorktreeManager._run_git()` 已内置 30s 超时和三阶段清理
- worktree branch 元数据持久化到 `.worktrees/{story_id}.branch`
- `cleanup()` 使用 `git branch -d`（安全删除），unmerged 分支会失败——merge 后分支一定可以安全删除
- 若 rebase 需要更长耗时，应该扩展 `_run_git(timeout_seconds=...)`，而不是绕过 `_run_git()` 另写裸 subprocess

**来自最近 git commits 的模式：**
- `feat: Story X.Y 描述` 的 commit 格式
- 代码变更聚焦单一职责
- ruff + mypy 必须全绿

### Project Structure Notes

- 新增文件：`src/ato/merge_queue.py`（核心模块）
- 新增文件：`tests/unit/test_merge_queue.py`
- 修改文件：
  - `src/ato/models/schemas.py` — 新增 MergeQueueEntry, MergeQueueState, rebase_conflict ApprovalType
  - `src/ato/models/db.py` — 新增 merge_queue/merge_queue_state DDL 和 CRUD
  - `src/ato/models/migrations.py` — v6→v7 迁移
  - `src/ato/worktree_mgr.py` — 新增 rebase_onto_main, merge_to_main, continue_rebase, abort_rebase, get_conflict_files
  - `src/ato/core.py` — 创建/消费 merge_authorization + `_process_approval_decisions()` + `_poll_cycle()` merge queue 驱动 + 初始化 MergeQueue
  - `src/ato/state_machine.py` — 新增 `regression_fail`
  - `src/ato/cli.py` — approval 图标 / 摘要 / 默认 options 对齐新 merge approvals
  - `src/ato/config.py` — 新增 regression_test_command, merge_rebase_timeout, merge_conflict_resolution_max_attempts
  - `ato.yaml.example` — merge / regression 配置项示例
  - `tests/unit/test_worktree_mgr.py` — 追加 rebase/merge 测试
  - `tests/unit/test_core.py` — 追加 merge queue 集成测试
  - `tests/unit/test_db.py` — 追加 merge queue CRUD 测试
  - `tests/unit/test_cli_approval.py` — 追加 approval metadata / options 测试
  - `tests/unit/test_state_machine.py` — 追加 `regression_fail` 测试
- 路径和命名完全符合架构规范 [Source: architecture.md 文件结构图, project-context.md]

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 4.2] — AC 原文与业务价值
- [Source: _bmad-output/planning-artifacts/epics.md#Story 4.5] — 相关的 Regression 测试执行故事
- [Source: _bmad-output/planning-artifacts/architecture.md#Decision 2] — TUI↔Orchestrator 通信模型（SQLite + nudge）
- [Source: _bmad-output/planning-artifacts/architecture.md#用户可见通知子系统] — NotificationLevel 枚举（regression_failure → URGENT）
- [Source: _bmad-output/planning-artifacts/architecture.md#Decision 8] — 状态机测试覆盖策略
- [Source: _bmad-output/planning-artifacts/architecture.md#Asyncio Subprocess 模式] — 三阶段清理协议
- [Source: _bmad-output/planning-artifacts/architecture.md#SQLite 连接策略] — WAL + busy_timeout + 短写事务
- [Source: _bmad-output/planning-artifacts/prd.md#FR31] — Merge queue 按顺序执行
- [Source: _bmad-output/planning-artifacts/prd.md#FR32] — Regression 失败冻结 merge queue
- [Source: _bmad-output/planning-artifacts/prd.md#FR52] — Worktree rebase 冲突检测
- [Source: _bmad-output/planning-artifacts/prd.md#NFR10] — Merge queue 安全保证
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md#ExceptionApprovalPanel] — regression_failure 面板（revert/fix forward/pause）
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md#Status Display Patterns] — 已冻结 `⏸` `$error` frozen
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md#Notification Patterns] — 紧急: regression 失败
- [Source: _bmad-output/project-context.md] — 全局开发规则
- [Source: src/ato/state_machine.py:135-154] — merging/regression 状态与 transitions
- [Source: src/ato/worktree_mgr.py] — WorktreeManager 现有接口
- [Source: src/ato/subprocess_mgr.py] — SubprocessManager dispatch 模式
- [Source: src/ato/cli.py] — approval 图标 / 摘要 / 默认 decision 选项
- [Source: src/ato/config.py] — `ATOSettings` 配置模型
- [Source: ato.yaml.example] — merging/regression phase 定义与 timeout 配置
- [Source: src/ato/transition_queue.py] — TransitionQueue 串行消费模式
- [Source: src/ato/approval_helpers.py] — create_approval() 统一 API
- [Source: src/ato/core.py] — Orchestrator poll cycle 与 approval 消费
- [Source: src/ato/recovery.py:42-59] — merge/regression 阶段恢复事件注册
- [Source: src/ato/models/schemas.py:27-39] — ApprovalType 定义
- [Source: src/ato/adapters/base.py:16-33] — cleanup_process() 三阶段协议
- [Source: _bmad-output/implementation-artifacts/4-1-approval-queue-nudge.md] — Story 4.1 完整 dev notes 与 patch 记录

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6 (1M context)

### Debug Log References

- 无 debug issue

### Completion Notes List

- ✅ Task 1: 新增 MergeQueueEntry / MergeQueueState Pydantic 模型，rebase_conflict ApprovalType，SCHEMA_VERSION 升级到 7，merge_queue / merge_queue_state DDL + 10 个 CRUD 函数，v6→v7 迁移
- ✅ Task 2: 新建 merge_queue.py，实现 MergeQueue 类（enqueue, process_next, _execute_merge, _dispatch_regression_test, _run_regression_test, check_regression_completion, _handle_rebase_conflict, _handle_precommit_failure, _handle_regression_failure, unfreeze）
- ✅ Task 3: WorktreeManager 新增 rebase_onto_main, continue_rebase, abort_rebase, merge_to_main, get_conflict_files + project_root 只读属性 + _run_git timeout_seconds 参数化
- ✅ Task 4: state_machine 新增 regression_fail 转换 + CANONICAL_TRANSITIONS 更新；core.py 初始化 MergeQueue/WorktreeManager + _create_merge_authorizations 幂等逻辑 + _handle_approval_decision 完整 merge_authorization/regression_failure/rebase_conflict/precommit_failure 消费 + poll cycle 驱动 merge queue + regression 检测；cli.py 补齐 rebase_conflict 图标/摘要/options，对齐 regression_failure/precommit_failure options
- ✅ Task 5: regression 调度已集成在 MergeQueue._dispatch_regression_test 和 check_regression_completion 中
- ✅ Task 6: 21 个 test_merge_queue 测试 + 6 个 test_worktree_mgr 追加 + 2 个 test_state_machine 追加 + 5 个 test_cli_approval 追加 + 3 个 test_core 追加 = 共 37 个新测试，全部通过
- ✅ Task 7: ATOSettings 新增 regression_test_command / merge_rebase_timeout / merge_conflict_resolution_max_attempts + ato.yaml.example 更新（含 regression next_on_failure: fixing）

### File List

新增文件：
- `src/ato/merge_queue.py` — MergeQueue 核心类
- `tests/unit/test_merge_queue.py` — 21 个新测试

修改文件：
- `src/ato/models/schemas.py` — SCHEMA_VERSION=7, rebase_conflict ApprovalType, MergeQueueEntry/MergeQueueState 模型
- `src/ato/models/db.py` — merge_queue/merge_queue_state DDL + 10 个 CRUD 函数
- `src/ato/models/migrations.py` — v6→v7 迁移
- `src/ato/worktree_mgr.py` — rebase_onto_main, merge_to_main, continue_rebase, abort_rebase, get_conflict_files, project_root, _run_git timeout_seconds
- `src/ato/core.py` — MergeQueue/WorktreeManager 初始化 + merge_authorization 创建 + approval 消费 + poll cycle 集成
- `src/ato/state_machine.py` — regression_fail 转换 + CANONICAL_TRANSITIONS 更新
- `src/ato/cli.py` — rebase_conflict 图标/摘要/options + regression_failure/precommit_failure options 对齐
- `src/ato/config.py` — regression_test_command, merge_rebase_timeout, merge_conflict_resolution_max_attempts
- `ato.yaml.example` — merge/regression 配置项 + regression next_on_failure
- `tests/unit/test_worktree_mgr.py` — 6 个追加测试
- `tests/unit/test_state_machine.py` — 2 个追加测试
- `tests/unit/test_cli_approval.py` — 5 个追加测试
- `tests/unit/test_core.py` — 3 个追加测试

### Change Log

- 2026-03-26: create-story 创建 — 基于 Epic 4 / PRD / 架构 / UX spec / Story 4.1 上下文生成 merge queue 与 regression safety story
- 2026-03-26: validate-create-story 修订 —— 补回 `merge_authorization` 创建与 `merging` 阶段 gating；将 queue 排序收敛到 `approval.decided_at`；把 merge 执行改为后台 worker + `regression_task_id` 跟踪，避免阻塞 poll loop；修正 `ATOConfig` / `dispatch()` / `task_type` / `create_approval(payload_dict=...)` 等与现有代码不符的接口；要求 conflict fix 复用 adapter + SubprocessManager 而非手写 `claude -p`；延后 worktree cleanup 直到 regression 闭环完成；补齐 `regression_fail` 状态机回退与 CLI approval 元数据合同
- 2026-03-26: dev-story 实现完成 — MergeQueue 核心类 + DB 层 + WorktreeManager rebase/merge 扩展 + Orchestrator/状态机/CLI 完整集成 + regression 调度与结果处理 + 37 个新测试，全部 958 单元测试通过，ruff + mypy 全绿
