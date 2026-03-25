# Story 验证报告：3.2b Fix Dispatch 与 Artifact 验证

验证时间：2026-03-25 11:40:04 CST
Story 文件：`_bmad-output/implementation-artifacts/3-2b-fix-dispatch-artifact-verification.md`
验证模式：`validate-create-story`
结果：PASS（已应用修正）

## 摘要

该 story 的主方向正确，但原稿里有 3 个会直接误导实现的缺口：

1. 它给 `_get_worktree_head()` 的示例直接做 `proc.communicate()`，没有 timeout / `cleanup_process()`，与当前仓库对 subprocess 的统一清理协议冲突。
2. 它在文字说明里承认 “git HEAD 读取失败要 warning 后继续”，却没有把这条非阻塞分支落实到 Tasks / 测试合同里，开发者很容易只覆盖 HEAD 变化 / 不变化两条路径。
3. 它的 References 引用了当前仓库里并不存在的 `Decision 3: Convergent Loop 质量门控`、`ADR-06`、`ADR-24` 等标签，还混入了易漂移的行数 / 全量测试数，降低了 story 的可追溯性。

## 已核查证据

- `_bmad-output/planning-artifacts/epics.md`
- `_bmad-output/planning-artifacts/architecture.md`
- `_bmad-output/planning-artifacts/prd.md`
- `_bmad-output/project-context.md`
- `_bmad-output/implementation-artifacts/3-2a-convergent-loop-full-review.md`
- `_bmad-output/implementation-artifacts/3-2b-fix-dispatch-artifact-verification.md`
- `src/ato/convergent_loop.py`
- `src/ato/subprocess_mgr.py`
- `src/ato/state_machine.py`
- `src/ato/models/db.py`
- `src/ato/models/schemas.py`
- `src/ato/adapters/base.py`
- `src/ato/worktree_mgr.py`
- `tests/unit/test_convergent_loop.py`
- `git log --oneline -5`

## 发现的关键问题

### 1. `_get_worktree_head()` 示例会把实现带离当前 subprocess 契约

原稿示例是：

- `create_subprocess_exec(...)`
- `await proc.communicate()`
- 失败时直接 `except OSError`

但当前仓库和项目上下文都已经明确：

- 所有 subprocess 调用都要有 timeout 边界
- 所有 subprocess 调用都要在 `finally` 里执行 `cleanup_process()`
- `worktree_mgr.py` 里的 `_run_git()` 已经给出了现成模式

如果沿着原稿实现，最常见的结果是：fix 阶段为了读一个 HEAD hash 写出一段不符合项目约束的裸 subprocess 代码。

已应用修正：

- Task 3 / Dev Notes / 示例代码统一改为 `wait_for(..., timeout=5)` + `finally cleanup_process()`
- 明确 `_get_worktree_head()` 返回 `None` 而不是在 helper 里吞掉更多流程语义
- References 补入 `src/ato/adapters/base.py` 和 `src/ato/worktree_mgr.py`

### 2. git HEAD 不可读的非阻塞分支原稿没有进入测试合同

原稿 Dev Notes 说：

- git 命令失败时 warning，不阻塞流程

但 Tasks / 测试只要求：

- HEAD 变化
- HEAD 不变化

这会把 “git 失败仍继续提交 `fix_done`” 留在口头约定层，开发者很容易漏掉最脆弱的分支。

已应用修正：

- Task 4.3 明确 `convergent_loop_fix_no_artifact` 同时覆盖 `head_unchanged` 与 `git_head_unavailable`
- Task 5 新增 `test_fix_dispatch_git_head_failure_still_continues`
- Task 3.3 明确 git 命令失败 / 超时 / `OSError` 时 `_get_worktree_head()` 返回 `None`，由调用方 warning 后继续

### 3. 引用链路使用了不存在或易漂移的来源

原稿 References 中包含：

- `Decision 3: Convergent Loop 质量门控`
- `ADR-06`
- `ADR-24`

但当前 `architecture.md` 里并没有这些标签；同时原稿还把：

- `convergent_loop.py` 行数
- 全套测试数

写成了硬编码事实，这些值已经开始漂移。

这类问题不一定会让代码立刻写错，但会显著降低 dev agent 对上下文的信任度，并诱导它去追不存在的文档锚点。

已应用修正：

- References 改为指向实际存在的来源：architecture 导言、Decision 6、subprocess cleanup 模式、PRD 核心流程
- `Project Structure Notes` 去掉了易过时的行数 / 测试总数
- `Git Intelligence` 改成当前仓库最近提交模式，而不是冻结旧的全量测试数字

## 已应用增强

- 为 story 增加了 `Change Log`，记录 create-story 与 validate-create-story 的修订内容
- `Fix Agent 类型` 的依据从不存在的 ADR 标签改成当前 PRD 核心流程约束

## 剩余风险

- 当前 story 仍把 “新 commit” 作为 fix artifact 的唯一存在性信号。如果后续 Structured Job 改成“允许未提交 diff”或“输出结构化 fix artifact”，这里的合同需要重新收敛。
- prompt 目前只要求 “修复后 commit 变更”。如果未来要统一 commit message、自动跑测试或写 Context Briefing，这些都应在后续 story 中显式建模，而不是隐含假设。

## 最终结论

修正后，Story 3.2b 已达到 `ready-for-dev` 的质量门槛。当前版本对 fix agent 类型、artifact 验证边界、git 失败的非阻塞处理，以及 subprocess 实现约束都已经给出足够明确且可测试的指导，不会再把 dev agent 带向错误的实现路径。
