# Story 2B.4: 操作者可看到 story 在独立 worktree 中执行

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a 操作者,
I want 看到每个 story 在独立的 git worktree 中执行 agent 任务,
So that story 之间的代码变更互相隔离。

## Acceptance Criteria

1. **AC1 — Worktree 创建与路径注册**
   ```
   Given story 进入需要代码变更的阶段（creating / developing / fixing）
   When 调用 WorktreeManager.create(story_id, branch_name=None, base_ref="HEAD")
   Then 执行 git worktree add -b <branch_name> <path> <base_ref> 创建独立 worktree
   And 自管理 worktree 路径为 {project_root}/.worktrees/{story_id}
   And 不复用 Claude CLI 的 `.claude/worktrees/*` 内建约定
   And worktree_path 写入 stories 表
   And structlog 记录 worktree_created 事件（含 story_id, path, branch_name）
   ```

2. **AC2 — Worktree 清理**
   ```
   Given story 完成所有阶段（done）
   When 调用 WorktreeManager.cleanup(story_id)
   Then 执行 git worktree remove <path> --force 清理目录
   And 仅尝试执行 git branch -d <branch_name> 做安全删除
   And 若分支尚未合并则记录 warning 并保留分支，强制删除留给后续 merge / cleanup 流程决策
   And 清空 stories 表的 worktree_path 字段
   And structlog 记录 worktree_cleaned 事件
   ```

3. **AC3 — 跨平台一致性（NFR13）**
   ```
   Given macOS 或 Linux 环境
   When 执行 worktree 创建/清理操作
   Then 两种平台行为一致
   And 路径使用 pathlib.Path 处理（不硬编码分隔符）
   And git 命令通过 asyncio.create_subprocess_exec 执行（不使用 shell=True）
   ```

4. **AC4 — 幂等性与错误处理**
   ```
   Given worktree 已存在（重复创建）或不存在（重复清理）
   When 调用 create() 或 cleanup()
   Then 幂等处理：已存在返回现有路径，不存在则跳过清理
   And git 命令失败时抛出 ATOError 子类，携带 stderr 信息
   And 三阶段清理协议：SIGTERM → wait(5s) → SIGKILL → wait
   ```

5. **AC5 — 查询与状态集成**
   ```
   Given story 在 worktree 中执行
   When 查询 story 状态
   Then stories.worktree_path 反映当前 worktree 路径（有值=活跃，None=未创建或已清理）
   And WorktreeManager.get_path(story_id) 返回当前 worktree 路径或 None
   ```

## Tasks / Subtasks

- [x] Task 1: 实现 WorktreeManager 核心类 (AC: #1, #2, #3, #4)
  - [x] 1.1 创建 `src/ato/worktree_mgr.py`，定义 `WorktreeManager` 类
    - 构造参数：`project_root: Path`（目标项目 git 仓库根路径），`db_path: Path`
    - 内部常量：`WORKTREE_BASE = ".worktrees"`，`BRANCH_PREFIX = "worktree-story-"`
  - [x] 1.2 实现 `async def create(self, story_id: str, branch_name: str | None = None, *, base_ref: str = "HEAD") -> Path`
    - 默认 `branch_name = f"worktree-story-{story_id}"`
    - 目标路径 = `project_root / WORKTREE_BASE / story_id`
    - 幂等检查：路径已存在 + 是 valid worktree → 直接返回路径并 structlog.info
    - 执行 `git worktree add -b <branch_name> <path> <base_ref>` 通过 `asyncio.create_subprocess_exec`
    - 三阶段清理：`try/finally` 中调用 `cleanup_process(proc)`
    - 成功后更新 `stories.worktree_path`（调用 `update_story_worktree_path()`）
    - structlog 记录 `worktree_created` 事件
    - 返回 worktree 绝对路径
  - [x] 1.3 实现 `async def cleanup(self, story_id: str) -> None`
    - 从 DB 读取 `stories.worktree_path`；为 None 则跳过（幂等）
    - 执行 `git worktree remove <path> --force`
    - 仅执行 `git branch -d <branch_name>`（安全删除已合并分支）；失败时 structlog.warning 但不抛出
    - 清空 `stories.worktree_path = None`
    - structlog 记录 `worktree_cleaned` 事件
  - [x] 1.4 实现 `async def get_path(self, story_id: str) -> Path | None`
    - 复用现有 `get_story()` 读取 `stories.worktree_path`，返回 Path 或 None
  - [x] 1.5 实现 `async def exists(self, story_id: str) -> bool`
    - 检查 DB 中 worktree_path 非空 且 目录实际存在
  - [x] 1.6 实现辅助方法 `async def _run_git(self, *args: str) -> tuple[int, str, str]`
    - 通过 `asyncio.create_subprocess_exec("git", *args, cwd=self._project_root)` 执行
    - `try/finally` + `cleanup_process(proc)` 三阶段清理
    - 返回 `(returncode, stdout, stderr)`
    - 超时 30 秒（`asyncio.wait_for`）

- [x] Task 2: 新增 DB 辅助函数 (AC: #1, #2, #5)
  - [x] 2.1 在 `src/ato/models/db.py` 中新增 `update_story_worktree_path(db, story_id, worktree_path)` 函数
    - 更新 `stories.worktree_path` 和 `updated_at`
    - 参数化查询，自动 commit

- [x] Task 3: 定义异常类型 (AC: #4)
  - [x] 3.1 在 `src/ato/models/schemas.py` 中新增 `WorktreeError(ATOError)` 异常类
    - 签名：`__init__(message: str, *, stderr: str = "", story_id: str | None = None)`
    - 风格对齐 `CLIAdapterError`：保存属性后 `super().__init__(message)`

- [x] Task 4: 单元测试 (AC: #1, #2, #3, #4, #5)
  - [x] 4.1 创建 `tests/unit/test_worktree_mgr.py`
  - [x] 4.2 测试 create() 成功路径：mock `asyncio.create_subprocess_exec` 返回 exit_code=0，验证 git worktree add `-b <branch> <path> <base_ref>` 参数正确，验证 DB worktree_path 已更新
  - [x] 4.3 测试 create() 幂等性：路径已存在时直接返回，不执行 git 命令
  - [x] 4.4 测试 create() 失败：git 命令 exit_code≠0 时抛出 WorktreeError，携带 stderr
  - [x] 4.5 测试 cleanup() 成功路径：验证 git worktree remove + git branch -d 命令正确执行，DB worktree_path 清空
  - [x] 4.6 测试 cleanup() 幂等性：worktree_path 为 None 时跳过，不执行 git 命令
  - [x] 4.7 测试 cleanup() 部分失败：git branch -d 失败时仅 warning 不抛异常
  - [x] 4.8 测试 get_path() 和 exists() 正确查询 DB
  - [x] 4.9 测试 _run_git() 超时场景：验证三阶段清理协议触发
  - [x] 4.10 测试路径构建：验证 `.worktrees/{story_id}` 格式正确

- [x] Task 5: 集成测试 (AC: #1, #2)
  - [x] 5.1 创建 `tests/integration/test_worktree_lifecycle.py`
  - [x] 5.2 在 tmp 目录初始化真实 git repo，测试完整 create → verify isolation → cleanup 生命周期
  - [x] 5.3 验证 worktree 中的文件变更不影响主仓库工作目录
  - [x] 5.4 验证 cleanup 后目录和分支均被删除

## Dev Notes

### 核心设计决策

- **独立模块 `worktree_mgr.py`**：不放在 `subprocess_mgr.py` 中，因为 WorktreeManager 管理 git 基础设施（worktree 生命周期），而 SubprocessManager 管理 agent CLI 调度——职责不同。WorktreeManager 的输出（worktree path）作为 SubprocessManager.dispatch() 的 `options["cwd"]` 输入。
- **不需要 schema 迁移**：`stories.worktree_path` 列已在 v1 DDL 中定义（`_STORIES_DDL`），StoryRecord 模型已有 `worktree_path: str | None = None`。SCHEMA_VERSION 保持 4 不变。
- **路径约定**（来自技术调研而非 memory）：自管理 worktree 路径 = `{project_root}/.worktrees/{story_id}`，分支名默认 `worktree-story-{story_id}`。不要复用 Claude CLI `--worktree` 的 `.claude/worktrees/*` 目录。
- **显式 base ref**：`create()` 接收 `base_ref="HEAD"`，避免 `git worktree add -b ...` 隐式依赖当前命令执行环境的分支上下文；后续 merge / queue story 如需从 `main` / `trunk` 派生，可由调用方显式传入。

### 与现有代码的集成点

1. **SubprocessManager.dispatch()**（`subprocess_mgr.py:71`）：调用方在 dispatch 前先调用 `WorktreeManager.create()`，然后在 `options={"cwd": str(worktree_path)}` 中传入 worktree 路径。ClaudeAdapter 和 CodexAdapter 已支持 `cwd` 参数。
2. **TransitionQueue**（`transition_queue.py`）：消费者在处理 creating/developing/fixing 转换时调用 `WorktreeManager.create()`，在 done 转换时调用 `cleanup()`。（具体集成在 Epic 3+ 完成，本 story 仅提供 WorktreeManager 基础设施）
3. **Orchestrator._poll_cycle()**（`core.py:175`）：当前为 MVP 空实现，后续 Epic 接入时将协调 WorktreeManager 与 SubprocessManager。
4. **StoryRecord**（`schemas.py:128`）：已有 `worktree_path: str | None = None` 字段。
5. **get_story() / insert_story()**（`db.py:191-215`）：已支持 worktree_path 的读写；读取路径时应优先复用 `get_story()`，避免新增只读重复 helper。

### 已建立的代码模式（必须遵循）

| 模式 | 示例出处 | 要求 |
|------|---------|------|
| Subprocess 三阶段清理 | `adapters/base.py:16-33` `cleanup_process()` | 所有 git subprocess 必须在 `try/finally` 中调用 |
| structlog 日志 | 全项目 `structlog.get_logger()` | 绝不用 `print()`；事件名 snake_case |
| 异常层次 | `schemas.py:41-61` `ATOError` / `CLIAdapterError` | 新异常遵循 `ATOError` + 显式 `__init__` 风格，不要把异常做成 Pydantic 模型 |
| DB 连接管理 | `db.py:142-164` `get_connection()` | 短连接 + `try/finally` close |
| 参数化 SQL | 全项目 `?` 占位符 | 绝不拼接 SQL 字符串 |
| asyncio subprocess | `adapters/claude_cli.py` | 使用 `asyncio.create_subprocess_exec`，绝不用 `shell=True` |
| 测试 mock 模式 | `tests/unit/test_subprocess_mgr.py` | mock `asyncio.create_subprocess_exec`，不调用真实 CLI |
| datetime 处理 | `db.py:172-183` `_dt_to_iso()/_iso_to_dt()` | 统一 ISO 8601 格式 |

### 技术约束

- **Python ≥3.11**：使用 `asyncio.TaskGroup`（若需并发 git 命令）
- **不使用 `shell=True`**：git 命令通过 `create_subprocess_exec("git", "worktree", "add", ...)` 逐参数传递
- **不使用 `asyncio.gather`**：使用 `TaskGroup`
- **git worktree 命令参考**：
  - 创建：`git worktree add -b <branch> <path> <base_ref>`（默认 `base_ref="HEAD"`，但由调用方显式传入）
  - 列表：`git worktree list --porcelain`（用于幂等检查）
  - 删除：`git worktree remove <path> --force`
  - 分支清理：`git branch -d <branch>`（仅安全删除；强制删除不在本 story 范围内）
- **异步安全**：所有 git 操作通过 `asyncio.create_subprocess_exec` 异步执行，不阻塞事件循环

### Project Structure Notes

- 新文件：`src/ato/worktree_mgr.py`（WorktreeManager 类）
- 修改文件：
  - `src/ato/models/schemas.py` — 新增 `WorktreeError` 异常类
  - `src/ato/models/db.py` — 新增 `update_story_worktree_path()` 函数
- 新测试：
  - `tests/unit/test_worktree_mgr.py`
  - `tests/integration/test_worktree_lifecycle.py`
- 不需要 schema 迁移（SCHEMA_VERSION 保持 4）
- 不需要修改 `__init__.py` 中的导出（按需 import）

### 前序 Story 关键学习

1. **Story 2B.1**（Claude dispatch）：建立了 SubprocessManager + adapter 分离模式，ProcessStartCallback 回调注册 PID。WorktreeManager 不需要 PID 注册——git 命令是短暂的（秒级），不是长时间运行的 agent。
2. **Story 2B.2**（Codex review）：确认 `cwd=options["cwd"]` 已被 CodexAdapter 支持。验证报告发现 `TemporaryDirectory()` 比 `mktemp()` 更安全——WorktreeManager 中若需临时文件亦应遵循。
3. **Story 2B.5**（batch select）：建立了原子事务模式（多步操作在单个 DB 事务中完成）。WorktreeManager.create() 中的 DB 更新应在 git 操作成功后立即执行。
4. **Story 2A.2**（TransitionQueue）：建立了 SQLite 写入策略——TransitionQueue consumer 使用长连接，其他用短连接。WorktreeManager 应使用短连接模式。

### Git Intelligence

最近 5 次提交模式：
```
3165e6c Merge story 2B.2
e3d8595 feat: Story 2B.2
04eb736 Merge story 2A.3
9facd73 feat: Story 2A.3
c0183a0 Merge story 1-4b
```

命名惯例：`feat: Story {id} {中文描述}`

### References

- [Source: _bmad-output/planning-artifacts/epics.md — Epic 2B, Story 2B.4]
- [Source: _bmad-output/planning-artifacts/architecture.md — FR29, FR30, NFR13]
- [Source: _bmad-output/planning-artifacts/architecture.md — Asyncio Subprocess 三阶段清理协议]
- [Source: _bmad-output/planning-artifacts/architecture.md — SQLite 连接策略]
- [Source: _bmad-output/planning-artifacts/prd.md — FR29, FR30, FR52, NFR13]
- [Source: _bmad-output/planning-artifacts/research/technical-claude-codex-cli-integration-research-2026-03-24.md — Worktree 生命周期管理 / 注意事项]
- [Source: docs/agent-team-orchestrator-system-design-input-2026-03-23.md — System Shape / Recommended Architecture]
- [Source: src/ato/adapters/base.py — cleanup_process() 参考实现]
- [Source: src/ato/subprocess_mgr.py — SubprocessManager dispatch 模式]
- [Source: src/ato/models/db.py — stories DDL 已含 worktree_path 列]
- [Source: src/ato/models/schemas.py — StoryRecord.worktree_path 已定义]

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6 (1M context)

### Debug Log References

- 全量回归测试 601 passed, 0 failed
- ruff check / ruff format / mypy strict 全部通过
- 0 warnings（修复了超时测试的 RuntimeWarning）

### Completion Notes List

- ✅ Task 3: 在 `schemas.py` 新增 `WorktreeError(ATOError)` 异常类，含 `stderr` 和 `story_id` 属性
- ✅ Task 2: 在 `db.py` 新增 `update_story_worktree_path()` 函数，参数化查询 + 自动 commit
- ✅ Task 1: 实现 `WorktreeManager` 核心类（create/cleanup/get_path/exists/_run_git/_get_worktree_branch）
  - `create()` 支持幂等检查（通过 `git worktree list --porcelain` 验证有效性）+ 幂等 DB 补写
  - `cleanup()` 通过 `_get_worktree_branch()` 查询实际分支名，安全删除分支（`-d`），失败仅 warning
  - `cleanup()` 对目录已被外部移除的场景幂等处理（prune 后继续清理 DB）
  - `_run_git()` 30 秒超时 + 三阶段清理协议
  - 所有 git 命令通过 `asyncio.create_subprocess_exec` 执行，无 `shell=True`
- ✅ Task 4: 19 个单元测试全部通过，覆盖 create 成功/幂等/幂等DB补写/失败、cleanup 成功/自定义分支/幂等/外部移除幂等/部分失败、get_path/exists 查询、超时清理、路径构建
- ✅ Task 5: 7 个集成测试全部通过，使用真实 git repo 验证完整 worktree 生命周期、文件隔离性、分支清理、自定义分支清理、外部移除幂等

### Code Review Fixes

- ✅ [高] cleanup() 幂等性：目录已被外部移除时执行 `git worktree prune` 而非抛错
- ✅ [高] cleanup() 自定义分支：新增 `_get_worktree_branch()` 方法从 `git worktree list --porcelain` 解析实际分支名
- ✅ [中] create() 幂等 DB 补写：worktree 已存在但 DB 为 NULL 时补写 `worktree_path`
- ✅ [低] 超时测试 mock 修复：`proc.terminate()` 和 `proc.kill()` 使用 `MagicMock`（同步调用），消除 RuntimeWarning

### Implementation Plan

严格遵循 story 任务顺序，先建异常类和 DB helper（Task 3, 2），再实现核心 WorktreeManager（Task 1），最后编写单元测试（Task 4）和集成测试（Task 5）。

### File List

- `src/ato/worktree_mgr.py` — 新增：WorktreeManager 核心类
- `src/ato/models/schemas.py` — 修改：新增 WorktreeError 异常类
- `src/ato/models/db.py` — 修改：新增 update_story_worktree_path() 函数
- `tests/unit/test_worktree_mgr.py` — 新增：19 个单元测试
- `tests/integration/test_worktree_lifecycle.py` — 新增：7 个集成测试
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — 修改：story 状态更新
- `_bmad-output/implementation-artifacts/2b-4-worktree-isolation.md` — 修改：任务完成记录

## Change Log

- 2026-03-25: Story 2B.4 实现完成 — WorktreeManager 核心类、WorktreeError 异常、DB helper、16 个单元测试 + 5 个集成测试
- 2026-03-25: 修复代码评审 4 项发现 — cleanup 幂等性、自定义分支清理、create DB 补写、超时测试 mock（+5 新测试）
- 2026-03-25: 修复代码评审 R2 — cleanup() 外部移除场景分支泄漏：_get_worktree_branch() 返回 None 时回退默认分支名
- 2026-03-25: 修复代码评审 R3 — 自定义分支+外部移除组合场景：新增 .branch 元数据文件持久化分支名，三级回退链：git 元数据 → 文件 → 默认约定
- 2026-03-25: 修复代码评审 R4 — 幂等 create() 补写丢失的 .branch 元数据；branch -d 失败时保留元数据供后续流程使用（+5 新测试）
- 2026-03-25: 修复代码评审 R5 — 幂等 create() 元数据修复从"文件不存在"扩展为"文件不存在或内容为空"（_has_valid_branch_meta）
