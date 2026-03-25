# Story 验证报告：2B.4 Worktree Isolation

验证时间：2026-03-25 08:04:22 CST
Story 文件：`_bmad-output/implementation-artifacts/2b-4-worktree-isolation.md`
验证模式：`validate-create-story`
结果：PASS（已应用修正）

## 摘要

该 story 原稿存在 3 个会明显误导开发实现的缺口，已在 story 文件中修正：

1. 它把自管理 worktree 的路径硬编码为 `.claude/worktrees/story-{id}`，并引用了一个仓库中不存在的 memory 文件作为依据。
2. 它让 `git worktree add` 隐式依赖当前 `HEAD`，没有把 base ref 作为显式契约，导致实现和测试都容易漂移。
3. 它新增了一个重复的 DB 只读 helper，并且 AC/Tasks 对分支清理语义不一致。

## 已核查证据

- 本地仓库工件：
  - `_bmad-output/planning-artifacts/epics.md`
  - `_bmad-output/planning-artifacts/architecture.md`
  - `_bmad-output/planning-artifacts/prd.md`
  - `_bmad-output/planning-artifacts/research/technical-claude-codex-cli-integration-research-2026-03-24.md`
  - `docs/agent-team-orchestrator-system-design-input-2026-03-23.md`
  - `src/ato/models/db.py`
  - `src/ato/models/migrations.py`
  - `src/ato/models/schemas.py`
  - `src/ato/adapters/base.py`
  - `src/ato/adapters/claude_cli.py`
  - `src/ato/adapters/codex_cli.py`
  - `src/ato/subprocess_mgr.py`
  - `src/ato/transition_queue.py`
  - `src/ato/core.py`
  - `tests/unit/test_db.py`
  - `tests/unit/test_schemas.py`
  - `tests/unit/test_subprocess_mgr.py`

## 发现的关键问题

### 1. worktree 路径约定错误，且引用了不存在的来源

原 story 把路径写成 `.claude/worktrees/story-{id}`，并在 References 中引用 `memory/feedback_worktree_location.md`。仓库内不存在这个 memory 文件。

相反，现有技术调研明确区分了两种路径：

- Claude CLI 自带 `--worktree` 时会使用 `<repo>/.claude/worktrees/<name>`
- Orchestrator 自管理 worktree 时，示例与建议都使用 `<repo>/.worktrees/<story_id>`

已应用修正：
- 把路径约定改为 `{project_root}/.worktrees/{story_id}`
- 明确“不要复用 Claude CLI 的 `.claude/worktrees/*` 内建约定”
- 用技术调研文档替换失效的 memory 引用

### 2. `git worktree add` 契约缺少显式 base ref

原 story 只要求：

```text
git worktree add <path> -b <branch_name>
```

这会把实现细节偷偷绑定到当前命令执行环境的分支上下文。技术调研中的参考实现则显式传入 base branch / ref：

```text
git worktree add -b <branch_name> <path> <base_branch>
```

已应用修正：
- `create()` 改为 `create(story_id, branch_name=None, *, base_ref="HEAD")`
- AC / Tasks / 技术约束统一为 `git worktree add -b <branch_name> <path> <base_ref>`

这样实现默认行为仍然等价于当前 HEAD，但调用契约更明确，可测试性也更好。

### 3. 重复 helper 与分支清理语义不一致

原 story 同时：

- 要求新增 `get_story_worktree_path()`，但仓库已经有 `get_story()` 和 `StoryRecord.worktree_path`
- 在 AC 中允许 `git branch -D`
- 在 Tasks 中又规定 cleanup 只做 `git branch -d`

这会让开发者不清楚应该：

- 继续复用现有 `get_story()`，还是创建一个只读重复接口
- 把“强制删分支”做进本 story，还是只做安全删除

已应用修正：
- 删除 `get_story_worktree_path()` 任务，改为显式复用 `get_story()`
- 把 cleanup 契约收敛为“只尝试 `git branch -d`；未合并则 warning 并保留分支”

## 已应用增强

- 补回了 create-story 模板自带的 validation note 注释
- 将 `WorktreeError` 的定义方式收敛到现有 `ATOError` / `CLIAdapterError` 风格，避免把异常误写成 Pydantic 模型
- 在 References 中补充系统设计输入文档，用于支撑“单独的 Worktree Manager 组件”这一设计决策

## 剩余风险

- `architecture.md` 的 FR 到结构映射把“工作空间管理”主文件写成了 `subprocess_mgr.py`，而系统设计输入又明确列出独立的 Worktree Manager 组件。当前 story 保留独立 `worktree_mgr.py` 方案，但建议后续在 architecture artifact 中补一条说明，避免文档间继续分叉。
- branch naming 目前仍采用 `worktree-story-{story_id}` 这一 story 级约定；仓库里暂无统一分支命名标准。它不构成 blocker，但如果后续 merge queue 要求 `story/<id>` 之类的命名，应统一在 FR31/FR52 相关 story 中收敛。

## 最终结论

修正后，这个 story 已经足够清晰，可以继续保持 `ready-for-dev`。高风险的路径约定错误、重复 helper 以及 cleanup 契约歧义都已移除，当前版本与仓库内可验证的技术来源保持一致。
