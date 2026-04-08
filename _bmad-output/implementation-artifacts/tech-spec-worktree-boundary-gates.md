---
title: 'Worktree Boundary Enforcement Gates'
slug: 'worktree-boundary-gates'
created: '2026-04-08'
status: 'ready-for-dev'
stepsCompleted: [1, 2, 3, 4]
tech_stack: ['Python >=3.11', 'asyncio', 'python-statemachine >=3.0', 'aiosqlite', 'Pydantic >=2.0', 'structlog']
files_to_modify: ['src/ato/models/schemas.py', 'src/ato/models/db.py', 'src/ato/models/migrations.py', 'src/ato/worktree_mgr.py', 'src/ato/transition_queue.py', 'src/ato/subprocess_mgr.py', 'src/ato/merge_queue.py', 'src/ato/core.py', 'src/ato/approval_helpers.py', 'src/ato/convergent_loop.py', 'src/ato/recovery.py']
code_patterns: ['TransitionQueue producer-consumer', 'MergeQueue worker preflight', 'Pydantic model_validate', 'aiosqlite migrations', 'ClaudeAdapter stream-json via SubprocessManager', 'structlog contextvars']
test_patterns: ['pytest-asyncio auto mode', 'real git integration fixtures for boundary parsing', 'mock finalize interface for unit tests', 'approval decision handler tests']
---

# Tech-Spec: Worktree Boundary Enforcement Gates

**Created:** 2026-04-08

## Overview

### Problem Statement

ATO 的状态转换边界（dev→review, fix→review, merge queue → rebase/merge）缺乏 worktree 清洁性检查。BidWise 实际执行中暴露了三类真实故障：

1. **Review 看到空 diff**：dev agent 完成实现但未 commit，review 基于 `git diff main...HEAD` 看到空 diff，直接 pass
2. **Merge 因脏 worktree 失败**：merge rebase 时 git 拒绝操作，因为 worktree 有未暂存变更
3. **实现文件丢失**：主要实现文件从未被 commit 到 story 分支，merge 后需要从 stash 恢复

根本原因：dev/fix agent 完成工作后未强制 commit 全部变更，系统缺乏确定性的边界检查来拦截这一状态。

**关键区分**："Clean worktree" != "Commit complete"。`git status` 干净只证明没有遗漏文件，不证明所有 story 预期产出都已提交。当前设计的硬约束覆盖条件 1（无未提交/未跟踪文件）+ 条件 2（相对 base 有 committed diff）。Expected artifacts 验证继续作为未来增强方向。

### Solution

双层防护设计：

- **硬约束（代码门控）**：在 `WorktreeManager` 中新增 worktree boundary preflight 方法，在实际边界执行检查：
  - `TransitionQueue` 在 `dev_done` 和 `fix_done` 发送给状态机之前执行 `pre_review`
  - `MergeQueue._execute_merge()` 在 `rebase_onto_main()` 之前执行 `pre_merge`
- **软约束（Prompt 硬化）**：调整 review prompt，让 reviewer 在正式 review 前主动检查 dirty worktree 和 empty diff。Prompt 层只用于降低硬门控触发频率，不承担正确性保证。

核心原则：**Prompt 作为软约束指导 LLM 工作，软件工程作为硬约束确保结果正确。**

**核心不变量：**

```text
INVARIANT: 在进入 review dispatch 或 merge rebase/merge 之前：
1. git status --porcelain=v1 -uall == empty
2. git diff --name-only {base_ref}...HEAD != empty
```

`base_ref` 必须由代码记录到 preflight result 中：

- `pre_review`: 使用 `main`，与当前 review scope 保持一致
- `pre_merge`: 使用与 `rebase_onto_main()` 相同的 resolver，优先 `origin/main`（fetch 成功时），否则 fallback 到本地 `main`

### Scope

**In Scope:**

- `WorktreeManager.preflight_check()` 检查 worktree 清洁性 + committed diff 非空性
- `WorktreePreflightResult` / `WorktreeFinalizeResult` 数据模型
- 新增 SQLite 表 `worktree_preflight_results`，避免与现有系统级 `preflight_results` 表冲突
- `TransitionQueue` 对 `dev_done`、`fix_done` 执行 `pre_review` hard gate
- `MergeQueue._execute_merge()` 在 rebase 前执行 `pre_merge` hard gate
- 门控失败时派发一次 finalize agent，finalize 后必须重新执行 preflight
- Finalize 后仍失败时创建 `preflight_failure` approval，并阻止边界推进
- `preflight_failure` approval 的 CLI/TUI 展示、默认选项、notification、decision handler
- Review prompt 硬化：preflight 检查 + 代码对比逻辑调整
- Gate fail-closed：任何 git 命令执行异常、worktree 缺失、base/head 解析失败时，门控结果为 FAIL

**Out of Scope:**

- `ato.yaml` 配置化门控规则
- no-code story 特殊处理
- Expected artifacts 清单验证
- `.gitignore` 盲区处理
- 对已 ignore 文件的追踪
- 用 LLM 判断哪些文件应该 stage

## Context for Development

### Brownfield Codebase State

本规格针对现有代码库扩展，不是 greenfield 实现。当前已有以下关键模块：

| Area | Existing File | Current Reality |
| ---- | ------------- | --------------- |
| 状态机 | `src/ato/state_machine.py` | `developing -> reviewing` 的事件名是 `dev_done`，不是 `start_review` |
| TransitionQueue | `src/ato/transition_queue.py` | `_consumer()` 当前执行 `sm.send(event)` 后持久化状态，并通过 `_replay_to_phase()` 恢复状态机 |
| Worktree 管理 | `src/ato/worktree_mgr.py` | 已有 `create()`、`cleanup()`、`rebase_onto_main()`、`merge_to_main()`、`has_new_commits()` |
| Merge Queue | `src/ato/merge_queue.py` | `uat_pass` 只进入 `merging`；实际 rebase/merge 在 approval 通过后的 merge worker 中执行 |
| Approval | `src/ato/approval_helpers.py` | 使用 `create_approval(... approval_type, payload_dict, recommended_action ...)`，不存在 `ApprovalRequest` 模型 |
| System preflight | `src/ato/preflight.py` + `preflight_results` | 已有系统级 preflight 表，不能复用为 worktree gate 审计表 |

### Codebase Patterns

**状态机模式（python-statemachine 3.0 async）：**

- `StoryLifecycle.create()` / `StoryLifecycle.from_config()` 显式 `await sm.activate_initial_state()`
- `_replay_to_phase()` 通过裸 `sm.send(event_name)` 重放事件恢复缓存状态机
- 因此本规格**不在状态机 transition 上新增 fail-closed condition**，避免破坏 replay 恢复

**TransitionQueue consumer 模式：**

- Producer-consumer，单 consumer 串行写入
- 当前顺序是 `await sm.send(event)` -> `await save_story_state(...)` -> `await db.commit()`
- 本规格只在 gated event 的 `sm.send()` 前插入 preflight/finalize/escalate 流程

**MergeQueue 模式：**

- `uat_pass` 进入 `merging`
- Orchestrator 创建 `merge_authorization`
- approval 通过后 `MergeQueue.enqueue()`
- `MergeQueue.process_next()` 启动 `_run_merge_worker()`
- `_execute_merge()` 执行 `rebase_onto_main()` -> `merge_to_main()` -> `merge_done` -> regression
- `pre_merge` 必须放在 `_execute_merge()` 调用 `rebase_onto_main()` 之前，而不是放在 `uat_pass`

**Subprocess 模式：**

- `SubprocessManager.dispatch_with_retry()` 使用 `ClaudeAdapter`/`CodexAdapter`
- `ClaudeAdapter` 当前固定 `--output-format stream-json`，并通过 options 透传 `cwd`、`max_turns`、`model` 等
- 新 finalize 接口不要手写 `claude -p ... --output-format json`，应复用现有 adapter 管道

### Gated Boundaries

| Boundary | Code Hook | Gate Type | Action |
| -------- | --------- | --------- | ------ |
| `dev_done` | `TransitionQueue._consumer()` before `sm.send("dev_done")` | `pre_review` | Dev agent 完成后进入 review 前检查 |
| `fix_done` | `TransitionQueue._consumer()` before `sm.send("fix_done")` | `pre_review` | Review-origin CL fix 完成后重新 review 前检查 |
| merge worker start | `MergeQueue._execute_merge()` before `rebase_onto_main()` | `pre_merge` | merge queue 已获授权、实际 rebase/merge 前检查 |

Non-gated events:

- `uat_pass` 不再作为 hard gate 位置。它只表示 UAT 通过并进入 `merging`，之后仍有 approval 等待窗口。
- `qa_fix_done`、`uat_fix_done`、`regression_fix_done` 不纳入本规格，因为它们不是 fix→review 边界。

## Technical Decisions

### ADR-1: 门控位置 - consumer/merge worker 显式门控，不使用状态机 condition

决策：**门控逻辑在 `WorktreeManager`，调用时机由实际执行边界显式控制。**

原因：

- 当前 `_replay_to_phase()` 会裸 `sm.send(event_name)`；给状态机加 fail-closed condition 会让已存在 story 无法恢复到 `reviewing`/`merging`
- `pre_merge` 的真实危险点不是 `uat_pass`，而是 approval 后 merge worker 调用 `rebase_onto_main()` 的瞬间
- 显式门控虽然需要在 `TransitionQueue` 和 `MergeQueue` 各接一次，但更符合现有代码结构

Implementation rule:

- `TransitionQueue` 只对 `_gate_type_for_transition(event_name)` 返回非 None 的事件执行 preflight
- `MergeQueue` 使用独立 helper `_run_pre_merge_gate(story_id)`，在 rebase 前执行
- 状态机 transition 定义不新增 condition

### ADR-2: SQLite 表名 - 使用 `worktree_preflight_results`

现有 `preflight_results` 表已经用于系统级 preflight，列为 `run_id/layer/check_item/status/message`。本规格必须新增独立表：

```sql
CREATE TABLE IF NOT EXISTS worktree_preflight_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    story_id TEXT NOT NULL,
    gate_type TEXT NOT NULL,
    passed INTEGER NOT NULL,
    base_ref TEXT NOT NULL,
    base_sha TEXT,
    head_sha TEXT,
    porcelain_output TEXT NOT NULL DEFAULT '',
    diffstat TEXT NOT NULL DEFAULT '',
    changed_files TEXT NOT NULL DEFAULT '[]',
    failure_reason TEXT,
    error_output TEXT,
    checked_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_worktree_preflight_story
ON worktree_preflight_results(story_id);

CREATE INDEX IF NOT EXISTS idx_worktree_preflight_gate
ON worktree_preflight_results(story_id, gate_type, checked_at);
```

Migration requirements:

- Increment `SCHEMA_VERSION` from current value to the next integer
- Add a new migration in `src/ato/models/migrations.py`
- Do not alter or reuse existing `preflight_results`

### ADR-3: Preflight result model

```python
WorktreeGateType = Literal["pre_review", "pre_merge"]
WorktreePreflightFailureReason = Literal[
    "NO_WORKTREE",
    "UNCOMMITTED_CHANGES",
    "EMPTY_DIFF",
    "GIT_ERROR",
]

class WorktreePreflightResult(_StrictBase):
    story_id: str
    gate_type: WorktreeGateType
    passed: bool
    base_ref: str
    base_sha: str | None = None
    head_sha: str | None = None
    porcelain_output: str = ""
    diffstat: str = ""
    changed_files: list[str] = Field(default_factory=list)
    failure_reason: WorktreePreflightFailureReason | None = None
    error_output: str | None = None
    checked_at: datetime
```

Use `WorktreePreflightResult.model_validate(...)`; do not use `model_construct`.
Add `Field` to the existing Pydantic imports in `schemas.py`.

### ADR-4: Git command behavior

`WorktreeManager.preflight_check()` must execute all git commands with `cwd=str(worktree_path)` or `git -C str(worktree_path)`.

Required commands:

```text
git status --porcelain=v1 -uall
git rev-parse HEAD
git rev-parse {base_ref}
git diff --stat {base_ref}...HEAD
git diff --name-only {base_ref}...HEAD
```

Rules:

- Use `git diff --name-only` for `changed_files`; do not parse filenames from `--stat`
- Use `--stat` only as human-readable audit data
- Any non-zero exit code, timeout, missing story, missing worktree path, or missing worktree directory returns `passed=False`
- Do not raise on normal gate failure; return a failed `WorktreePreflightResult`
- It is acceptable to raise only for programming errors outside git/preflight execution, but callers should treat unexpected exceptions as fail-closed

### ADR-5: Finalize strategy

Finalize remains a single LLM-assisted attempt, but the LLM output is not trusted as the source of truth.

`SubprocessManager.dispatch_finalize()` contract:

```python
class WorktreeFinalizeResult(_StrictBase):
    story_id: str
    committed: bool
    pre_head_sha: str | None = None
    post_head_sha: str | None = None
    commit_sha: str | None = None
    commit_message: str | None = None
    files_changed: list[str] = Field(default_factory=list)
    error: str | None = None
```

Implementation rules:

- Use existing `ClaudeAdapter` through `dispatch_with_retry()`
- Options must include `{"cwd": str(worktree_path), "max_turns": 3}`
- The prompt may instruct the agent to run `git add -A` + `git commit`, but code must verify results with git after the agent exits
- `commit_sha`, `commit_message`, and `files_changed` must come from local git commands such as `git rev-parse HEAD`, `git log -1 --pretty=%B`, and `git diff --name-only {pre_head}..{post_head}`
- If pre-head == post-head, `committed=False`
- After finalize, caller must run `preflight_check()` again. `WorktreeFinalizeResult.committed=True` alone is not sufficient to proceed

Accepted limitation:

- `git add -A` inside story worktree can still commit generated or accidental files. This is accepted for this scope but must be visible in `files_changed` and in the follow-up preflight audit record.

### ADR-6: `preflight_failure` approval

Add `preflight_failure` to:

- `ApprovalType`
- `APPROVAL_TYPE_TO_NOTIFICATION`
- `APPROVAL_RECOMMENDED_ACTIONS`
- `APPROVAL_DEFAULT_VALID_OPTIONS`
- `APPROVAL_TYPE_ICONS`
- `approval_helpers.format_approval_summary()`
- `approval_helpers._EXCEPTION_TYPE_TITLES`
- `approval_helpers._OPTION_LABELS`
- `approval_helpers.get_exception_context()`
- `models.db.get_undispatched_stories()` pending approval exclusion list
- Orchestrator approval decision handling in `core.py`

Recommended payload:

```json
{
  "gate_type": "pre_review",
  "retry_event": "dev_done",
  "worktree_path": "/abs/path/to/worktree",
  "failure_reason": "UNCOMMITTED_CHANGES",
  "preflight_result": {"...": "..."},
  "options": ["manual_commit_and_retry", "escalate"]
}
```

For `pre_merge`, use:

```json
{
  "gate_type": "pre_merge",
  "retry_event": "merge_queue_retry",
  "worktree_path": "/abs/path/to/worktree",
  "failure_reason": "EMPTY_DIFF",
  "preflight_result": {"...": "..."},
  "options": ["manual_commit_and_retry", "escalate"]
}
```

Decision handling:

- `manual_commit_and_retry` + `pre_review`: resubmit the original transition event from payload (`dev_done` or `fix_done`)
- `manual_commit_and_retry` + `pre_merge`: re-enqueue/reset the merge queue entry for the story by calling `MergeQueue.enqueue(story_id, approval.approval_id, approval.decided_at)`
- `escalate`: submit `escalate` through TransitionQueue when possible

## Implementation Plan

### Task 1: Define models and approval constants

Files:

- `src/ato/models/schemas.py`
- `src/ato/approval_helpers.py`

Actions:

- Add `WorktreeGateType`, `WorktreePreflightFailureReason`, `WorktreePreflightResult`, `WorktreeFinalizeResult`
- Add `preflight_failure` approval constants and display helpers
- Add default options `["manual_commit_and_retry", "escalate"]`
- Add recommended action `"manual_commit_and_retry"`

### Task 2: Add `worktree_preflight_results` migration and CRUD helper

Files:

- `src/ato/models/schemas.py`
- `src/ato/models/migrations.py`
- `src/ato/models/db.py`
- `tests/unit/test_migrations.py`
- `tests/unit/test_preflight_schema.py` or a new `tests/unit/test_worktree_preflight_schema.py`

Actions:

- Increment `SCHEMA_VERSION`
- Add migration that creates `worktree_preflight_results`
- Add helper:

```python
async def save_worktree_preflight_result(
    db: aiosqlite.Connection,
    result: WorktreePreflightResult,
    *,
    commit: bool = False,
) -> int:
    ...
```

Notes:

- Store `changed_files` as JSON using `json.dumps(result.changed_files)`
- Keep default `commit=False` so TransitionQueue/MergeQueue can own transaction boundaries
- Do not change existing `insert_preflight_results()`

### Task 3: Implement `WorktreeManager.preflight_check()`

File:

- `src/ato/worktree_mgr.py`

Actions:

- Add:

```python
async def preflight_check(
    self,
    story_id: str,
    gate_type: WorktreeGateType,
) -> WorktreePreflightResult:
    ...
```

- Add helper to resolve `base_ref`:
  - `pre_review` -> `"main"`
  - `pre_merge` -> use the same fetch/fallback logic as `rebase_onto_main()`
- Refactor `rebase_onto_main()` to use the same `pre_merge` target resolver so preflight and rebase cannot diverge
- Add helper `_run_git_in_worktree(worktree_path, *args, timeout_seconds=...)`
- Use `git diff --name-only` for `changed_files`
- Log `story_id`, `gate_type`, `passed`, `failure_reason`, `base_ref`, `base_sha`, `head_sha`

### Task 4: Implement finalize dispatch

File:

- `src/ato/subprocess_mgr.py`

Actions:

- Add:

```python
async def dispatch_finalize(
    self,
    story_id: str,
    worktree_path: str,
    story_summary: str,
    *,
    dirty_files: list[str] | None = None,
) -> WorktreeFinalizeResult:
    ...
```

- Use existing adapter pipeline:

```python
await self.dispatch_with_retry(
    story_id=story_id,
    phase="worktree_finalize",
    role="developer",
    cli_tool="claude",
    prompt=prompt,
    options={"cwd": worktree_path, "max_turns": 3},
    max_retries=0,
)
```

- Before dispatch, collect `pre_head_sha`
- After dispatch, collect `post_head_sha`, `commit_message`, and `files_changed` via git
- Return `committed=False` with `error` on `CLIAdapterError` or if HEAD did not change

Prompt constraints:

- Only allowed git-mutating commands: `git add -A`, `git commit`
- Forbidden: `git reset`, `git checkout`, `git switch`, `git stash`, `git clean`, `git rebase`, `git merge`
- Commit message must start with `"{story_id}: "`
- Do not edit files except if needed to fix clearly broken generated artifacts introduced by the finalize attempt; normal path should be commit only

### Task 5: Integrate `pre_review` gate in TransitionQueue

File:

- `src/ato/transition_queue.py`

Actions:

- Add helper:

```python
def _gate_type_for_transition(event_name: str) -> WorktreeGateType | None:
    if event_name in {"dev_done", "fix_done"}:
        return "pre_review"
    return None
```

- In `_consumer()`, before `sm.send(event.event_name)`, run a helper like:

```python
preflight_blocked = await self._run_pre_review_gate_if_needed(db, event)
if preflight_blocked:
    # set completion_future exception or result marker as appropriate
    continue
```

- If preflight passes, continue to `sm.send()`
- If preflight fails:
  - Persist failed preflight result
  - Dispatch finalize once
  - Run second preflight and persist it
  - If second preflight passes, continue to `sm.send()`
  - If still failing, create `preflight_failure` approval and do not call `sm.send()`

Important:

- Do not add state machine conditions
- Do not call preflight when `_gate_type_for_transition()` returns None
- Do not pass `None` into `preflight_check()`
- If story has no worktree path for a gated transition, create failed preflight result with `NO_WORKTREE` and escalate

### Task 6: Integrate `pre_merge` gate in MergeQueue

File:

- `src/ato/merge_queue.py`

Actions:

- In `_execute_merge()`, before `rebase_onto_main()`, run:

```python
gate_passed = await self._run_pre_merge_gate(story_id)
if not gate_passed:
    return
```

- `_run_pre_merge_gate()` mirrors the pre-review flow:
  - preflight
  - save result
  - finalize once on failure
  - second preflight
  - if still failing, create `preflight_failure`
  - mark merge queue entry failed or reset it to a retryable state, and release `current_merge_story_id`

Required behavior on persistent failure:

- Do not call `rebase_onto_main()`
- Do not call `merge_to_main()`
- Do not leave `merge_queue.status = 'merging'` with `current_merge_story_id` held forever
- Create an approval whose payload contains `gate_type="pre_merge"` and `retry_event="merge_queue_retry"`

### Task 7: Add `preflight_failure` approval handling

File:

- `src/ato/core.py`
- `src/ato/models/db.py`

Actions:

- In `_handle_approval_decision()`:
  - `manual_commit_and_retry` + `pre_review` -> submit payload `retry_event`
  - `manual_commit_and_retry` + `pre_merge` -> call `self._merge_queue.enqueue(...)`
  - `escalate` -> submit `escalate` when `self._tq` exists
- In `get_undispatched_stories()`, exclude pending `preflight_failure` approvals so the story does not continue dispatching while waiting for manual action
- Add tests for decided-but-unconsumed `preflight_failure` approvals

### Task 8: Harden review prompts

Files:

- `src/ato/convergent_loop.py`
- `src/ato/recovery.py`
- relevant prompt assertion tests

Update all review entry points:

- First review prompt in `ConvergentLoop.run_first_review()`
- Scoped re-review prompt in `ConvergentLoop._build_rereview_prompt()`
- Recovery reviewing prompt in `_CONVERGENT_LOOP_PROMPTS["reviewing"]`

Required prompt block:

```text
## Review Preflight (mandatory before code review)

1. Run `git status --porcelain=v1 -uall` in the story worktree.
2. Run `git diff --stat main...HEAD`.
3. If status output is non-empty:
   - Stop before reviewing.
   - Report verdict: BLOCK.
   - Reason: UNCOMMITTED_WORKTREE_CHANGES.
   - List the dirty files.
4. If `git diff --stat main...HEAD` is empty:
   - Stop before reviewing.
   - Report verdict: BLOCK.
   - Reason: EMPTY_COMMITTED_DIFF.
5. Only when the worktree is clean and committed diff is non-empty, review `git diff main...HEAD`.
```

Notes:

- This is a soft constraint. The hard gate already runs before the reviewing phase dispatch.
- Update tests that currently assert only `git diff main...HEAD` appears so they also assert the preflight block.

### Task 9: Merge prompt clarification

There is no general "merge agent prompt" in the current flow. Rebase/merge is deterministic in `WorktreeManager`; only conflict resolution uses an LLM prompt.

Files:

- `src/ato/merge_queue.py`
- tests for `_build_conflict_resolution_prompt()`

Actions:

- Keep `_build_conflict_resolution_prompt()` prohibition on commits
- Add explicit prohibition on `git reset`, `git checkout`, `git switch`, `git stash`, `git clean`, `git merge`
- Do not add a dirty-worktree preflight block to the conflict prompt; conflict resolution happens during an intentionally dirty rebase state
- The actual pre-merge dirty check belongs to Task 6

### Task 10: Unit tests

Files:

- `tests/unit/test_worktree_mgr.py`
- `tests/unit/test_transition_queue.py`
- `tests/unit/test_merge_queue.py`
- `tests/unit/test_approval.py`
- `tests/unit/test_convergent_loop.py`
- `tests/unit/test_migrations.py`

Required coverage:

- `preflight_check()` pass path
- dirty worktree -> `UNCOMMITTED_CHANGES`
- clean worktree + empty diff -> `EMPTY_DIFF`
- missing worktree -> `NO_WORKTREE`
- git command failure / timeout -> `GIT_ERROR`
- `changed_files` comes from `git diff --name-only`, not stat parsing
- `_gate_type_for_transition("dev_done") == "pre_review"`
- `_gate_type_for_transition("fix_done") == "pre_review"`
- non-gated event such as `create_done` skips preflight
- TransitionQueue dirty pre-review -> finalize -> retry pass -> transition succeeds
- TransitionQueue dirty pre-review -> finalize -> retry fail -> `preflight_failure` approval
- MergeQueue pre-merge fail -> no rebase/merge call and queue lock released
- `preflight_failure` approval options and summaries render
- approval decision `manual_commit_and_retry` resubmits correct event or re-enqueues merge

### Task 11: Integration tests with real git

Files:

- `tests/integration/test_worktree_boundary_preflight.py`
- `tests/integration/test_preflight_gate.py`
- `tests/integration/test_merge_queue.py`

Required coverage:

- Create a temporary git repo with `main`, a story worktree branch, and a committed change; verify `preflight_check(..., "pre_review")` passes
- Add an untracked file; verify dirty failure
- Create a clean branch with no diff from `main`; verify empty diff failure
- Rename a file; verify `changed_files` is correct from `--name-only`
- Simulate `pre_merge` base resolver fallback when `origin/main` fetch fails
- Verify existing system `preflight_results` still supports `insert_preflight_results()`
- Verify new `worktree_preflight_results` table coexists with old table

## Acceptance Criteria

**Happy Path:**

- [ ] AC-1: Given story worktree is clean and `git diff --name-only main...HEAD` is non-empty, when `dev_done` is processed, then pre-review gate passes, `worktree_preflight_results` records a pass, and story enters `reviewing`.
- [ ] AC-2: Given story worktree is clean and committed diff is non-empty, when `fix_done` is processed, then pre-review gate passes and story enters `reviewing`.
- [ ] AC-3: Given story is approved for merge and worktree is clean with non-empty diff against the resolved merge base, when merge worker starts, then pre-merge gate passes and `rebase_onto_main()` is called.

**Dirty Worktree - Finalize Success:**

- [ ] AC-4: Given story worktree has uncommitted changes, when `dev_done` is processed, then first preflight returns `UNCOMMITTED_CHANGES`, finalize agent runs once, second preflight passes, and story enters `reviewing`.
- [ ] AC-5: Given finalize creates a commit, then `WorktreeFinalizeResult` uses local git commands to record `pre_head_sha`, `post_head_sha`, `commit_sha`, `commit_message`, and `files_changed`.

**Dirty Worktree - Finalize Failure:**

- [ ] AC-6: Given finalize does not resolve the gate failure, then a `preflight_failure` approval is created, the original transition or merge does not proceed, and the story is not dispatched further while the approval is pending.

**Empty Diff:**

- [ ] AC-7: Given story worktree is clean but committed diff is empty, when `dev_done` or `fix_done` is processed, then preflight returns `EMPTY_DIFF`, finalize runs once, and persistent failure creates `preflight_failure`.

**Pre-merge:**

- [ ] AC-8: Given merge approval has been granted but the worktree is dirty before rebase, when `_execute_merge()` starts, then pre-merge gate blocks before `rebase_onto_main()` and creates audit records.
- [ ] AC-9: Given pre-merge gate still fails after finalize, then merge queue does not remain locked in `merging`; the operator can retry through `preflight_failure` approval.

**Git Error - Fail Closed:**

- [ ] AC-10: Given worktree path is missing, base ref cannot be resolved, or git command fails/times out, then preflight returns `passed=False` with `NO_WORKTREE` or `GIT_ERROR` and never defaults to pass.

**Prompt Hardening:**

- [ ] AC-11: Review prompts instruct the reviewer to run `git status --porcelain=v1 -uall` and block on dirty worktree or empty committed diff before reviewing.
- [ ] AC-12: Conflict resolution prompt continues to forbid commits and additionally forbids reset/checkout/switch/stash/clean/merge.

**Audit Tracking:**

- [ ] AC-13: Every worktree boundary preflight writes one row to `worktree_preflight_results` with `base_ref`, `base_sha`, `head_sha`, `diffstat`, `changed_files`, `failure_reason`, and `checked_at`.
- [ ] AC-14: Existing system-level `preflight_results` and `insert_preflight_results()` continue to work unchanged.

**Non-gated Transition:**

- [ ] AC-15: Given transition event is not `dev_done` or `fix_done`, when TransitionQueue processes it, then it skips worktree preflight unless another module explicitly gates it.

## Additional Context

### Dependencies

| Dependency | Existing Area | Relationship |
| ---------- | ------------- | ------------ |
| WorktreeManager | `src/ato/worktree_mgr.py` | Add preflight and shared merge base resolver |
| TransitionQueue | `src/ato/transition_queue.py` | Add pre-review hard gate before `sm.send()` |
| MergeQueue | `src/ato/merge_queue.py` | Add pre-merge hard gate before `rebase_onto_main()` |
| SubprocessManager | `src/ato/subprocess_mgr.py` | Add finalize dispatch using existing adapter pipeline |
| Approval Queue | `src/ato/approval_helpers.py`, `src/ato/core.py` | Add `preflight_failure` creation, display, and decision handling |
| Convergent Loop | `src/ato/convergent_loop.py`, `src/ato/recovery.py` | Harden review prompts |
| SQLite migrations | `src/ato/models/migrations.py` | Add new audit table without touching existing `preflight_results` |

### Notes

- Do not implement `start_review`; the correct event is `dev_done`.
- Do not gate `uat_pass`; gate actual merge execution in `MergeQueue`.
- Do not add state machine conditions for this feature; replay recovery depends on bare `sm.send()` calls.
- Do not parse filenames from `git diff --stat`.
- Do not trust finalize agent text as proof of commit; always verify with git.
- Keep `ato submit` behavior separate. Submit validates that dev produced commits; worktree boundary preflight validates cleanliness and committed diff at orchestration boundaries.
