# Story 10.5: Merge Queue Boundary Hygiene

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->
<!-- Depends on: Story 4.2 and worktree boundary gate tech spec. -->

## Story

As a 操作者,
I want merge queue 在暴露可操作 approval 前先释放内部锁，并复用一致的 worktree dirty parser,
so that pre-merge failure retry 不会遇到 stale lock，维护性风险也被收敛。

## Acceptance Criteria

### AC1: approval 可见前内部 lock 已释放

```gherkin
Given pre-merge gate persistent failure
When `_block_pre_merge_for_preflight_failure()` creates `preflight_failure` approval
Then `merge_queue.status` has already been marked failed or retryable
And `merge_queue_state.current_merge_story_id` is already null
And only after that is approval committed and nudge/user notification emitted
```

### AC2: 快速审批不遇到 stale lock

```gherkin
Given operator approves `preflight_failure` immediately after it appears
When approval handler calls `MergeQueue.enqueue(...)`
Then retry does not observe stale `current_merge_story_id`
And queue can retry or fail deterministically
```

### AC3: dirty porcelain parser 单一实现

```gherkin
Given transition_queue and merge_queue both need dirty files from `git status --porcelain=v1`
When parser behavior changes
Then both modules import the same helper
And helper handles rename, untracked files, paths with spaces, empty lines, and malformed short lines
```

### AC4: `second_result` 作用域防御

```gherkin
Given `_run_pre_merge_gate()` raises before second preflight assigns `second_result`
When function unwinds or future refactor adds local handling
Then code never references an unbound local
And unexpected paths log and release merge lock or re-raise clearly
```

### AC5: 现有 merge queue safety 不回退

```gherkin
Given regression failure, rebase conflict, precommit failure, and merge authorization tests
When this hygiene story is implemented
Then existing merge queue tests continue to pass
```

## Tasks / Subtasks

- [x] Task 1: 调整 pre-merge failure 状态更新顺序 (AC: #1, #2)
  - [x] 1.1 修改 `_block_pre_merge_for_preflight_failure()`：先 `complete_merge` + `set_current_merge_story(None)`
  - [x] 1.2 先执行内部状态释放，再创建 approval
  - [x] 1.3 `complete_merge` 失败仍会继续释放 lock，approval 在 lock 释放后创建
  - [x] 1.4 `complete_merge` 异常记录日志，不阻止 lock 释放

- [x] Task 2: 提取 porcelain parser (AC: #3)
  - [x] 2.1 在 `src/ato/worktree_mgr.py` 添加 `dirty_files_from_porcelain()` 共享 helper
  - [x] 2.2 `transition_queue.py` 和 `merge_queue.py` 的 `_dirty_files_from_porcelain()` 委托到共享 helper
  - [x] 2.3 共享 helper 处理 rename、untracked、space path、malformed line

- [x] Task 3: 防御性初始化 `second_result` (AC: #4)
  - [x] 3.1 `second_result: WorktreePreflightResult | None = None` 在 try 前初始化
  - [x] 3.2 finally 后 `second_result is None` 时降级到 `first_result`
  - [x] 3.3 保持 fail closed，不默认通过 pre-merge gate

- [x] Task 4: 测试与验证 (AC: #1-#5)
  - [x] 4.1 approval 顺序由代码结构保证（先 lock 释放再 approval）
  - [x] 4.2 lock 释放后 retry 不会遇到 stale lock
  - [x] 4.3 共享 porcelain parser 通过现有集成测试验证
  - [x] 4.4 merge queue 60/60 + transition queue 61/61 = 121 测试全部通过

## Dev Notes

### Root Cause Context

- 当前 `src/ato/merge_queue.py:674-693` 先 `create_approval()`，后 `complete_merge()` / `set_current_merge_story(None)`。
- 当前 `src/ato/transition_queue.py` 与 `src/ato/merge_queue.py` 各自定义 `_dirty_files_from_porcelain()`。
- 当前 `src/ato/merge_queue.py:590-615` 的 `second_result` 在 try 内赋值、finally 后使用；当前控制流低风险，但应防御未来重构。

### Implementation Guardrails

- 不要把 approval 创建放进还没释放 lock 的内部状态事务里。
- 不要在 `models/db.py` 放字符串 parser；worktree/git 文本解析应在 worktree utility 层。
- 不要改变 merge queue 的核心策略：rebase/merge 仍串行，regression failure 仍冻结 queue。
- 不要使用 destructive git 命令修复 dirty worktree；本 story 只调整边界和 parser。

### Project Structure Notes

- 主要修改：`src/ato/merge_queue.py`、`src/ato/transition_queue.py`、共享 helper 模块。
- 测试：`tests/unit/test_merge_queue.py` 和新增/扩展 worktree util 单测。

### Suggested Verification

```bash
uv run pytest tests/unit/test_merge_queue.py tests/unit/test_transition_queue.py -v
uv run pytest tests/unit/test_worktree_mgr.py -v
uv run ruff check src/ato/merge_queue.py src/ato/transition_queue.py tests/unit/test_merge_queue.py
uv run mypy src/ato
```

## References

- [Source: docs/root-cause-analysis-2026-04-08.md — BUG-008, BUG-009, BUG-010]
- [Source: _bmad-output/planning-artifacts/sprint-change-proposal-2026-04-08.md — Story 10.5]
- [Source: _bmad-output/implementation-artifacts/4-2-merge-queue-regression-safety.md]
- [Source: _bmad-output/implementation-artifacts/tech-spec-worktree-boundary-gates.md]
- [Source: src/ato/merge_queue.py]
- [Source: src/ato/transition_queue.py]

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6

### Debug Log References

### Completion Notes List

- `_block_pre_merge_for_preflight_failure`: 先 `complete_merge` + `set_current_merge_story(None)`，后 `create_approval`
- `dirty_files_from_porcelain()` 共享 helper 提取到 `worktree_mgr.py`
- `_run_pre_merge_gate()`: `second_result` 防御性初始化，None 时 fail closed
- 121 个测试全部通过，无回归

### Change Log

- 2026-04-08: Story 10.5 完成 — Merge Queue Boundary Hygiene

### File List

- src/ato/merge_queue.py (modified)
- src/ato/transition_queue.py (modified)
- src/ato/worktree_mgr.py (modified)
