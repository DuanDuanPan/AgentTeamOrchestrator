# Worktree Review And Merge Risk Notes - 2026-04-08

## Context

This note records the discussion around the 2026-04-07 execution of the BidWise
stories:

- `3-7-drawio-embedded-editing`
- `4-3-smart-annotation-panel`

Primary log source:

- `/Users/enjoyjavapan/Downloads/ato.log`

Related repositories:

- ATO source: `/Volumes/Data/Work/Code/LLM/AgentTeamOrchestrator`
- BidWise project: `/Volumes/Data/Work/Code/StartUp/BidWise`

The log timestamps are UTC. The execution window discussed here maps to Asia/Shanghai
local time from 2026-04-07 evening to 2026-04-08 morning.

## Corrected Conclusion

The initial hypothesis "we should use isolated worktrees" was too broad. The current
system already uses story-level git worktrees for implementation phases.

Evidence:

- BidWise `ato.yaml` configures these phases as `workspace: worktree`:
  - `developing`
  - `reviewing`
  - `fixing`
  - `qa_testing`
  - `uat`
- ATO creates `.worktrees/<story-id>` when a story enters `developing`.
- The 3-7 and 4-3 logs show agent execution under:
  - `.worktrees/3-7-drawio-embedded-editing`
  - `.worktrees/4-3-smart-annotation-panel`

The more precise risk is:

> The story worktree exists, but dirty or partially committed worktree state can still
> cross review, QA, UAT, and merge boundaries.

## Risk 1: Review Can Miss Real Work

Current review behavior is too narrow. The review prompt tells the reviewer to use only:

```text
git diff main...HEAD
```

as the review scope, and to ignore uncommitted worktree changes.

Observed effect:

- For 3-7, review saw `main...HEAD` as empty and reported no findings.
- For 4-3, review also confirmed `HEAD`, `main`, and merge-base were the same commit,
  while the worktree still had dirty/untracked implementation files.

This means review was not reviewing the actual implementation if that implementation was
still uncommitted in the story worktree.

### Prompt Improvement

Changing the prompt is feasible and should be done as a first layer of defense. A safer
review prompt should require a preflight:

```text
Preflight before review:
1. Run `git status --porcelain`.
2. Run `git diff --stat main...HEAD`.
3. If `git status --porcelain` is non-empty, verdict must be Block with reason
   `UNCOMMITTED_WORKTREE_CHANGES`. Do not proceed to normal review.
4. If `git diff --stat main...HEAD` is empty and the story is not explicitly no-code,
   verdict must be Block with reason `EMPTY_COMMITTED_DIFF`.
5. Only when the worktree is clean and the committed diff is non-empty, review
   `git diff main...HEAD`.
```

However, prompt changes are not enough by themselves. This is a process invariant and
should be enforced by deterministic ATO code before dispatching review.

Recommended hard gate:

- Before `developing -> reviewing` and `fixing -> reviewing`, inspect the story worktree.
- Require:
  - `git status --porcelain` is empty.
  - `git diff --stat main...HEAD` is non-empty, unless the story is explicitly no-code.
- If the gate fails, do not dispatch review. Return the story to a finalize/fixing step
  that commits story-scoped changes first.

## Risk 2: Merge Rebase Failed Because Story Worktree Was Dirty

The merge failures were not caused by missing worktree isolation. Merge rebase runs in the
story worktree, but git refused to rebase because the story worktree had unstaged changes:

```text
error: cannot rebase: You have unstaged changes.
error: Please commit or stash them.
```

This happened for both 4-3 and 3-7.

### 4-3 Root Cause

For 4-3, the evidence points to this sequence:

1. The initial dev implementation completed without committing the whole feature.
2. Review saw the committed branch diff as empty while the worktree was dirty.
3. Later fixing agents committed only the files they directly changed:
   - one fix commit for E2E/layout resilience
   - one fix commit for CI/toolchain issues
4. Significant existing implementation files remained uncommitted.
5. At merge time, the merge agent found "2 commits plus significant uncommitted work" and
   committed all uncommitted implementation work before merging.

Current BidWise git history supports this:

- `d1deada` - fix commit for 4.2/4.3 E2E layout/status filter issues.
- `d8b4f27` - fix commit for 4.3 CI/toolchain issues.
- `d81cb03` - feature commit that finally committed the main 4.3 implementation.

The feature commit happened during the merge recovery path, which is too late.

### 3-7 Root Cause

For 3-7, the evidence points to a similar but worse sequence:

1. The initial dev implementation completed without a full story feature commit.
2. Review saw `main...HEAD` as empty and therefore reviewed no code.
3. Later fixing agents committed only partial changes:
   - a `PlateEditor.tsx` fix
   - a fix that included `DrawioElement.tsx`, the E2E test, ESLint ignore changes, and
     a 4.1 test race fix
   - an `ato.yaml` formatting fix
4. At merge time, the branch contained only three fix commits.
5. After the merge, current BidWise history shows an additional recovery commit:
   `9957414 fix: recover lost story 3-7 drawio integration files from stash`.

That recovery commit explicitly states that several 3-7 integration files were recovered
from an orphaned stash and had never been committed into the original story merge.

This confirms the key failure mode: dev/fix worktree state was not forced into a clean,
complete story branch before review and merge.

## Recommended Controls

### 1. Add A Finalize Checkpoint

Add a deterministic checkpoint between dev/fix and review:

```text
dev/fix agent finishes
-> ATO checks story worktree status
-> if dirty, dispatch finalize/commit step
-> run required gates
-> verify clean worktree and non-empty committed branch diff
-> then enter review
```

The finalize step should commit only story-scoped changes and record:

- base SHA
- HEAD SHA
- diffstat
- changed files
- gate commands and results

### 2. Make Review Fail Closed

Review should not pass if:

- the worktree is dirty
- the committed branch diff is empty for a code story
- the reviewer cannot determine the base and head commits

Prompt improvement is useful, but the state transition should also be blocked in code.

### 3. Make Merge Fail Closed On Dirty Worktree

Merge should not let a generic merge agent "commit all uncommitted work" in the merging
phase.

If `git status --porcelain` is non-empty before merge rebase:

- abort merge/rebase preparation
- create an approval or return the story to fixing/finalize
- do not enqueue another merge authorization until the story branch is clean and committed

### 4. Keep Worktree Isolation

The story-level worktree design is correct and should be retained. The missing piece is
not worktree isolation; it is boundary enforcement inside each isolated worktree.

## Short Version

Prompt hardening is feasible and should be done, but it is not sufficient.

The real invariant should be:

```text
No review or merge unless the story worktree is clean and the committed branch diff
represents the complete story implementation.
```

