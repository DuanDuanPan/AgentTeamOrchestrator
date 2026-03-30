---
title: 'LLM-Assisted Regression Runner'
slug: 'llm-regression-runner'
created: '2026-03-30'
status: 'implementation-complete'
stepsCompleted: [1, 2, 3, 4]
tech_stack:
  [
    'python>=3.11',
    'codex-cli',
    'asyncio',
    'aiosqlite',
    'structlog',
    'pydantic>=2.0',
    'typer',
    'git',
  ]
files_to_modify:
  - 'src/ato/merge_queue.py'
  - 'ato.yaml.example'
  - 'tests/unit/test_merge_queue.py'
code_patterns:
  - '_dispatch_regression_test() 预插入单条 regression task（status=running, expected_artifact=regression_test）并立即返回 task_id'
  - 'SubprocessManager.dispatch_with_retry() 会阻塞直到 CLI 完成；传入 task_id + is_retry=True 时复用已有 task 记录'
  - 'CodexOutput 已支持 text_result + structured_output；merge queue 可在 dispatch 返回后基于 structured_output 归一化 task.exit_code'
  - 'RecoveryEngine._resolve_phase_config_static() + get_main_path_limiter() 是当前 main-workspace structured_job 的配置与串行化入口'
  - 'check_regression_completion() / _handle_regression_failure() 只消费 tasks.status + exit_code + error_message'
test_patterns:
  - 'pytest + pytest-asyncio (asyncio_mode=auto)'
  - 'tests/unit/test_merge_queue.py 已覆盖 regression task 调度、超时、approval summary 等路径'
  - '使用 AsyncMock 隔离 SubprocessManager.dispatch_with_retry()、limiter 和 DB 写回'
---

# Tech-Spec: LLM-Assisted Regression Runner

**Created:** 2026-03-30

## Overview

### Problem Statement

ATO 当前的 regression 阶段在 `merge_queue.py` 中直接顺序执行硬编码 shell 命令链
（`regression_test_commands` / `regression_test_command`）。这条路径有两个现实问题：

1. 当目标项目没有预先维护好回归命令时，ATO 无法自主发现测试体系。
2. 当前实现虽然稳定，但把“如何发现并组织回归测试”的责任全部留给操作者，
   无法覆盖陌生语言/框架项目。

需要引入 Codex 作为 regression runner，但不能破坏现有 merge queue 合同：

- 仍然只有 1 条 regression task
- `_dispatch_regression_test()` 必须立即返回 `task_id`
- `merge_queue.regression_task_id` 仍是 crash recovery / poll cycle 的稳定锚点
- regression 仍然发生在 **merge 完成后的 main workspace**

### Solution

保留当前 merge queue 的外部合同不变，只替换后台执行器：

- `_dispatch_regression_test()` 继续先插入 1 条 `TaskRecord(status="running")`
  并立即返回 `task_id`
- 后台协程从 `_run_regression_test()` 改为 `_run_regression_via_codex()`
- `_run_regression_via_codex()` 在共享 `main` workspace limiter 内部，通过
  `CodexAdapter` + `SubprocessManager.dispatch_with_retry(task_id=..., is_retry=True)`
  调用 Codex CLI
- Codex 不再依赖“CLI 进程退出码 == 测试通过/失败”来表达 regression 结果，
  而是通过现有 `output_schema` 能力返回结构化结果
- merge queue 在 Codex 调用结束后，把结构化结果归一化为 tasks 表中的
  `exit_code` / `error_message`

核心归一化规则：

- `regression_status == "pass"` 且 main workspace 未新增脏文件 → `exit_code=0`
- `regression_status == "fail"` → `status="completed"`, `exit_code=1`,
  `error_message=summary`
- CLI 超时 / transport error / parse error → 仍由 `SubprocessManager` 标记为
  `status="failed"`，merge queue 按现有失败路径冻结

`tasks.text_result` 在本 story 中保存的是 **Codex 最终摘要文本**，不是完整 JSONL
transcript。实时过程可见性继续通过 `ProgressEvent` / `last_activity_*` 暴露。

### Scope

**In Scope:**

- 保持单 task / 单 `regression_task_id` 合同不变
- 用后台 `_run_regression_via_codex()` 替换 shell 命令链
- 为 Codex regression 调用增加 `output_schema`，显式产出
  `regression_status / summary / commands_attempted / skipped_command_reason`
- `regression_test_commands` 在配置存在时仍然是 **操作者提供的基线命令**
  ：Codex 必须优先执行；若跳过，必须在结构化结果里说明原因
- 未配置回归命令时，Codex 可完全自主发现项目测试体系
- 复用 phase 配置解析：model / reasoning / timeout / workspace 语义必须与现有
  `structured_job` 一致
- 复用共享 `main` workspace limiter，避免和其他 main-path job 并发踩仓库根目录
- 增加 main workspace “无新脏文件”保护：regression 结束后若发现新增变更，视为失败
- approval payload 继续从 `tasks.error_message` 取 `test_output_summary`

**Out of Scope:**

- 改变 regression 在状态机中的位置
- 改成 pre-merge regression
- 引入新的 DB schema
- 保存完整原始 JSONL transcript 到 SQLite
- 测试生成、修复或补写
- 修改 `CodexAdapter` 的底层 launch 模式

## Context for Development

### Codebase Patterns

**当前 regression 调度合同（`merge_queue.py`）：**

1. `_dispatch_regression_test()` 生成 `task_id`
2. 立即插入 1 条 `TaskRecord(phase="regression", role="qa", cli_tool="codex",
   status="running", expected_artifact="regression_test")`
3. 后台 `asyncio.create_task(...)`
4. 返回 `task_id`
5. merge worker 继续调用 `mark_regression_dispatched(db, story_id, task_id)`

这个顺序必须保留。因为 `mark_regression_dispatched()` 和 crash recovery 都依赖
“task_id 立即存在且可查询”。

**SubprocessManager 真实合同（`subprocess_mgr.py`）：**

- `dispatch_with_retry()` 返回的是 `AdapterResult`，不是 `task_id`
- 首次调用（`is_retry=False`）会 `INSERT` 新 task
- 重用已有 task 时必须传 `task_id=...` + `is_retry=True`
- 调用完成后，`SubprocessManager` 已经把 `text_result / cost / duration / exit_code`
  写入 tasks 表

因此 regression 新方案必须继续走：

- 外层先建 task
- 后台再用 `task_id + is_retry=True` 复用该 task

**Codex result 合同（`codex_cli.py` + `schemas.py`）：**

- `CodexAdapter` 已支持 `output_schema` / `output_file`
- `CodexOutput` 已有 `structured_output` 字段
- 这意味着 regression 可以用结构化结果表达“测试失败”，不必赌 Codex CLI 进程非零退出

**Main workspace 串行化（`core.py` / `recovery.py`）：**

- main-path structured job 当前通过 `get_main_path_limiter()` 串行化
- regression 虽然不走 `core` 的 phase dispatcher，但仍在 main workspace 运行
- 所以新路径必须显式复用同一个 limiter，而不是仅依赖 `SubprocessManager(max_concurrent=1)`

**Failure summary 消费路径：**

- `check_regression_completion()` 最终把 `tasks.error_message` 传入
  `_handle_regression_failure()`
- CLI 面板展示 `payload.test_output_summary`
- 所以 regression 失败的 operator-facing 摘要必须最终落在 `tasks.error_message`

### Files to Reference

| File | Purpose |
| ---- | ------- |
| `src/ato/merge_queue.py` | 主改造目标：dispatch 合同、后台 runner、result 归一化 |
| `src/ato/recovery.py` | 复用 phase 配置解析模式（`_resolve_phase_config_static`） |
| `src/ato/core.py` | 复用 `get_main_path_limiter()` |
| `src/ato/adapters/codex_cli.py` | 现有 `output_schema` / `structured_output` 能力 |
| `src/ato/models/schemas.py` | `AdapterResult` / `CodexOutput` 数据合同 |
| `ato.yaml.example` | regression 配置注释语义 |
| `tests/unit/test_merge_queue.py` | regression 调度与归一化测试落点 |

### Technical Decisions

1. **保留 dispatch 合同，不做阻塞式重构**
   `_dispatch_regression_test()` 继续“立即创建 task + 启动后台协程 + 返回 task_id”。
   不把 `dispatch_with_retry()` 直接塞进 `_dispatch_regression_test()`。

2. **Regression 语义由 structured output 决定**
   downstream 仍然只看 tasks 表里的 `exit_code`，但这个 `exit_code` 由
   `_run_regression_via_codex()` 基于 `structured_output` 归一化，而不是直接信任
   Codex CLI 进程退出码。

3. **`regression_test_commands` 不退化为“普通 hint”**
   若配置存在，它们仍然是操作者给出的回归基线命令。Codex 必须优先尝试这些命令；
   只有在检测到命令明显不适用时才允许跳过，并在结构化输出里说明原因。

4. **复用 phase 配置，不发明 regression 特例配置源**
   regression 的 model / reasoning / timeout / workspace 语义必须从现有 settings
   派生，不允许在 `merge_queue.py` 内再硬编码第二套配置规则。

5. **不宣称 sandbox 安全性**
   当前 `CodexAdapter` 启动模式不是只读沙箱；本 story 不修改该事实。
   安全边界由两层保证：
   - prompt 明确禁止修改文件 / git 状态
   - runner 在调用前后比较 main workspace 变更集合，发现新增脏文件即判失败

6. **`text_result` 只承诺保存最终摘要**
   本 story 不承诺把完整 JSONL transcript 落库。若未来需要完整 transcript，
   应另开 story 设计存储模型。

## Implementation Plan

### Tasks

- [x] Task 1: 新增 regression 结构化结果 schema 与 prompt 模板
  - File: `src/ato/merge_queue.py`
  - Action:
    - 新增 `_REGRESSION_RESULT_SCHEMA`
    - 新增 `_REGRESSION_PROMPT_TEMPLATE`
    - 新增 `_build_regression_prompt(repo_root: Path, settings: ATOSettings) -> str`
  - Requirements:
    - prompt 必须要求 Codex：
      - 不修改任何文件、git index、branch 或 commit
      - 先检查项目测试配置与目录
      - 若配置了 `regression_test_commands`，优先执行这些命令；若跳过必须说明原因
      - 最终输出匹配 schema 的 JSON
      - 额外给出适合写入 `text_result` 的简短自然语言总结
    - schema 至少包含：
      - `regression_status: "pass" | "fail"`
      - `summary: str`
      - `commands_attempted: list[str]`
      - `skipped_command_reason: str | null`
      - `discovery_notes: str`

- [x] Task 2: 新增 regression dispatch option helper
  - File: `src/ato/merge_queue.py`
  - Action: 新增 `_build_regression_dispatch_options() -> dict[str, object]`
  - Requirements:
    - 使用 `RecoveryEngine._resolve_phase_config_static(self._settings, "regression")`
    - 复用现有 phase-derived 字段：
      - `cwd`
      - `timeout`
      - `model`
      - `reasoning_effort`
      - `reasoning_summary_format`
      - `sandbox`（若 phase config 显式提供则透传，但不把它当作安全保证）
    - 追加 `output_schema=_REGRESSION_RESULT_SCHEMA`

- [x] Task 3: 新增 main workspace 变更快照 helper
  - File: `src/ato/merge_queue.py`
  - Action: 新增辅助函数，采集 repo root 的修改/暂存/untracked 文件集合
  - Requirements:
    - 使用 git 探测命令，不使用 shell 拼接
    - 返回“调用前已有变更集合”，供调用后比较“是否新增脏文件”
    - 不要求 repo 初始完全干净；只要求 regression 不引入新增变更

- [x] Task 4: 保留 `_dispatch_regression_test()` 的单 task 合同
  - File: `src/ato/merge_queue.py`
  - Action:
    - 继续由 `_dispatch_regression_test()` 预插入 `status="running"` 的 regression task
    - 保留 `expected_artifact="regression_test"`
    - 后台任务名继续使用 `regression-{story_id}`
    - 将后台执行器改为 `_run_regression_via_codex(story_id, task_id)`
  - Notes:
    - 不删除“预插入 task”这一步
    - 不在这里直接 await `dispatch_with_retry()`

- [x] Task 5: 用 `_run_regression_via_codex()` 替换 shell runner
  - File: `src/ato/merge_queue.py`
  - Action: 新增 `async def _run_regression_via_codex(self, story_id: str, task_id: str) -> None`
  - Requirements:
    - 获取共享 `get_main_path_limiter()`
    - 采集 pre-run workspace 变更快照
    - 创建 `CodexAdapter` + `SubprocessManager`
    - 调用 `dispatch_with_retry(..., task_id=task_id, is_retry=True)`
    - `CLIAdapterError` 只记录日志并返回，因为 `SubprocessManager` 已写终态
    - 其他异常按现有 catch-all 方式标记 task failed

- [x] Task 6: 在 `_run_regression_via_codex()` 中归一化 structured result
  - File: `src/ato/merge_queue.py`
  - Action: `dispatch_with_retry()` 返回后检查 `result.structured_output`
  - Requirements:
    - 若缺失或不满足预期字段，覆写 task 为失败：
      `status="completed"`, `exit_code=1`,
      `error_message="Regression runner produced no valid structured result"`
    - 若 `regression_status == "fail"`：
      - 覆写 task 为 `status="completed"`, `exit_code=1`
      - `error_message = summary[:500]`
      - 保留 `text_result` 为 Codex 最终摘要
    - 若 `regression_status == "pass"`：
      - 继续检查 workspace 是否新增脏文件
      - 无新增变更时保持 `exit_code=0`

- [x] Task 7: 增加 main workspace 新增脏文件保护
  - File: `src/ato/merge_queue.py`
  - Action: 比较 pre/post workspace 变更快照
  - Requirements:
    - 如果 post-run 比 pre-run 多出新路径：
      - 覆写 task 为 `status="completed"`, `exit_code=1`
      - `error_message` 写为可操作摘要，例如
        `"Regression runner modified main workspace: <paths>"`
    - 这条保护优先级高于 `regression_status == "pass"`

- [x] Task 8: 保持 failure summary 链路稳定
  - File: `src/ato/merge_queue.py`
  - Action: 视需要微调 `check_regression_completion()`
  - Requirements:
    - 继续优先读取 `tasks.error_message` 作为 `test_output_summary`
    - 若 `error_message` 为空而 `text_result` 非空，可回退为截断后的 `text_result`
    - 不修改 `_handle_regression_failure()` 的 approval 类型、选项或冻结语义

- [x] Task 9: 更新配置注释
  - File: `ato.yaml.example`
  - Action: 更新 regression 配置说明
  - Requirements:
    - 明确 `regression_test_commands` 是“操作者提供的基线命令”
    - Codex 会优先执行这些命令；未配置时才完全自主发现
    - 不改 `ATOSettings` 字段结构

- [x] Task 10: 更新单元测试
  - File: `tests/unit/test_merge_queue.py`
  - Action:
    - 删除针对 `create_subprocess_exec` 命令链的 shell-runner 用例
    - 新增 Codex runner 测试
  - Required cases:
    - `test_build_regression_prompt_with_baseline_commands`
    - `test_build_regression_prompt_without_baseline_commands`
    - `test_dispatch_regression_preserves_single_task_contract`
    - `test_run_regression_via_codex_pass_normalizes_to_exit_code_zero`
    - `test_run_regression_via_codex_fail_normalizes_to_completed_exit_code_one`
    - `test_run_regression_via_codex_invalid_structured_output_fails_closed`
    - `test_run_regression_via_codex_workspace_dirty_fails_closed`
    - `test_run_regression_via_codex_cli_error_leaves_subprocess_mgr_terminal_state`

### Acceptance Criteria

- [x] AC 1: Given merge worker 触发 regression，When 调用 `_dispatch_regression_test()`，
  Then 立即返回 `task_id`，且 tasks 表中已存在 1 条
  `phase="regression"`、`status="running"`、`expected_artifact="regression_test"`
  的记录

- [x] AC 2: Given regression 后台 runner 启动，When 调用 Codex，
  Then 使用 `SubprocessManager.dispatch_with_retry(task_id=..., is_retry=True)`，
  且 options 包含 `output_schema` 与 phase-derived 配置

- [x] AC 3: Given Codex 结构化结果为
  `{regression_status: "pass"}` 且 main workspace 未新增脏文件，
  When poll cycle 检测 task 完成，Then `_complete_regression_pass()` 被调用，
  story 进入 `done`

- [x] AC 4: Given Codex 结构化结果为
  `{regression_status: "fail", summary: "..."}`，
  When `_run_regression_via_codex()` 归一化结果，
  Then task 被覆写为 `status="completed"`, `exit_code=1`,
  `error_message=summary`，后续 approval payload 中包含 `test_output_summary`

- [x] AC 5: Given Codex CLI transport failure / timeout / parse error，
  When `dispatch_with_retry()` 抛出 `CLIAdapterError`，
  Then tasks 表保留 `SubprocessManager` 写入的失败终态，merge queue 按现有失败路径冻结

- [x] AC 6: Given `ato.yaml` 中配置了 `regression_test_commands`，
  When regression prompt 构建完成，
  Then prompt 明确要求这些命令作为回归基线优先执行；若 Codex 跳过，结构化结果必须给出原因

- [x] AC 7: Given `ato.yaml` 中未配置 `regression_test_commands`，
  When regression 触发，
  Then Codex 仍可自主发现并运行测试体系

- [x] AC 8: Given regression 运行前后对比发现 main workspace 新增脏文件，
  When `_run_regression_via_codex()` 完成归一化，
  Then task 被视为失败，绝不能把该次 regression 标记为 pass

- [x] AC 9: Given regression 结束，
  When 查看 tasks 表，
  Then `text_result` 保存 Codex 的最终自然语言摘要；
  本 story 不要求保存完整 JSONL transcript

## Additional Context

### Dependencies

- `codex` CLI（已安装）
- 现有 `CodexAdapter` + `SubprocessManager`
- 现有 `output_schema` / `structured_output` 支持
- 现有 `tasks.text_result` 列（v10 migration）

### Testing Strategy

**单元测试：**

- 重点覆盖 prompt 构建、single-task contract、structured result 归一化、
  workspace dirty guard、CLI error passthrough
- 使用 `AsyncMock` mock `SubprocessManager.dispatch_with_retry()`
- 使用假 limiter 验证 main-path 串行入口被调用
- 使用假 git 快照 helper 验证“新增变更”而不是“仓库必须初始干净”

**集成测试：**

- `check_regression_completion()` / `_complete_regression_pass()` /
  `_handle_regression_failure()` 的既有测试大体可保留
- 如需补充，只补“error_message 为空时回退 text_result”这种窄回归

**手动验证：**

- 在真实仓库上跑一次 merge → regression
- 验证：
  - task 先被创建再后台运行
  - Codex 最终写回结构化 pass/fail
  - 回归失败时 approval 面板能看到清晰摘要
  - main workspace 未被意外修改

### Notes

- **高风险项已显式收敛**：本稿不再依赖“Codex CLI 非零退出码 == 测试失败”的隐式假设
- **与现有 story 合同对齐**：不破坏 Story 8.4 的单 task / main workspace / command baseline 语义
- **未来扩展点**：若后续需要保存完整 transcript，可单独扩展 tasks 表或新增 transcript 存储，不应在本 story 中临时塞进 `text_result`
