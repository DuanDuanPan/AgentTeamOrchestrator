# Story 8.4: 回归测试支持多命令（unit / integration / smoke）

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a 操作者,
I want 在 merge 后的 regression 阶段配置并顺序执行多条测试命令,
so that main 分支上的回归验证可以覆盖单元、集成与 smoke 场景，同时保持现有 merge queue / recovery 合同不变。

## Acceptance Criteria (AC)

### AC1: 多命令配置加载与优先级

```gherkin
Given ato.yaml 中配置了 `regression_test_commands` 列表
When 调用 `load_config()` 加载配置
Then 配置加载成功
And commands 列表按声明顺序保留
And `ATOSettings.get_regression_commands()` 返回同顺序的命令列表
And 当 `regression_test_commands` 与 `regression_test_command` 同时存在时，优先使用前者
```

### AC2: 单命令配置向后兼容

```gherkin
Given ato.yaml 中仅配置了 `regression_test_command`
When 调用 `ATOSettings.get_regression_commands()`
Then 返回值为仅包含该单命令的列表
And 当前单命令 regression 行为保持不变
```

### AC3: 维持单个 regression task 合同并顺序执行

```gherkin
Given merge queue 已为某个 story 创建了 1 条 `phase="regression"` task 记录
When `_run_regression_test()` 执行 regression 命令链
Then 在主仓库 repo root / main workspace 中按顺序执行 `get_regression_commands()` 返回的每条命令
And 整个命令链只写回同一条 regression task 记录
And `merge_queue.regression_task_id` / `check_regression_completion()` / approval payload 合同保持不变
And 本 story 不引入新的 regression task 行，也不引入新的 DB schema 字段
```

### AC4: 失败即中止并汇总失败命令

```gherkin
Given regression_test_commands 中第 N 条命令返回非 0 exit code 或超时
When regression 命令链执行到该命令
Then 后续命令不再启动
And task `error_message` 包含失败命令的 1-based 序号、命令文本、stdout/stderr 摘要
And 失败摘要继续遵循当前 `<=1000` 字符截断合同
```

### AC5: 每条命令独立解析、独立超时，全部成功则通过

```gherkin
Given 命令字符串中包含引号或空格
When 每条 regression 命令执行
Then 对每条命令分别使用 `shlex.split(cmd)` 解析
And 对每条命令分别使用 `timeout.structured_job` 作为独立超时预算
And 所有命令均成功时，同一条 regression task 被更新为 `status="completed"` 且 `exit_code=0`
```

### AC6: 配置模板与测试布局对齐当前仓库

```gherkin
Given 用户查看 `ato.yaml.example`
When 查看 regression 配置示例
Then `regression_test_commands` 使用当前仓库真实存在的测试层级示例：`tests/unit/`、`tests/integration/`、`tests/smoke/`
And 保留 `regression_test_command` 注释说明其向后兼容用途
And 测试计划优先扩展现有 `tests/unit/test_config.py` 与 `tests/unit/test_merge_queue.py`
And 本 story 不要求新增不存在的 `tests/e2e/` 目录
```

## Tasks / Subtasks

- [ ] Task 1: 扩展配置模型并保持向后兼容 (AC: #1, #2)
  - [ ] 1.1 在 `src/ato/config.py` 中为 `ATOSettings` 新增 `regression_test_commands` 字段；如需可变默认值，使用 Pydantic v2 推荐写法而非共享可变默认对象
  - [ ] 1.2 保留现有 `regression_test_command: str = "uv run pytest"` 字段，避免破坏已有配置与测试
  - [ ] 1.3 新增 `get_regression_commands() -> list[str]`：优先返回 `regression_test_commands`，否则回退为 `[regression_test_command]`

- [ ] Task 2: 改造 regression runner，但保持现有 task / merge-queue 合同 (AC: #3, #4, #5)
  - [ ] 2.1 保持 `src/ato/merge_queue.py::_dispatch_regression_test()` 与 `mark_regression_dispatched()` 的单 task 合同不变；不要新增 DB migration 或多 task 设计
  - [ ] 2.2 将 `_run_regression_test()` 改为循环执行 `self._settings.get_regression_commands()`
  - [ ] 2.3 每条命令都使用当前已有的 `shlex.split(cmd)`、`asyncio.create_subprocess_exec(..., cwd=repo_root)` 与三阶段清理协议
  - [ ] 2.4 每条命令独立使用 `timeout.structured_job`；任一命令非 0 或超时立即中止后续命令
  - [ ] 2.5 失败时 `error_message` 包含失败命令序号、命令文本和 stdout/stderr 摘要，并继续遵循 `<=1000` 字符截断
  - [ ] 2.6 全部命令成功时，仅更新现有 regression task 为 `status="completed"`、`exit_code=0`；`check_regression_completion()` 与 approval payload 路径无需改 schema

- [ ] Task 3: 更新配置模板，使用真实存在的测试层级示例 (AC: #6)
  - [ ] 3.1 将 `ato.yaml.example` 的 regression 示例改为多命令格式：
    ```yaml
    regression_test_commands:
      - "uv run pytest tests/unit/"
      - "uv run pytest tests/integration/"
      - "uv run pytest tests/smoke/"
    ```
  - [ ] 3.2 保留 `regression_test_command` 的注释说明，明确其仅用于向后兼容旧配置

- [ ] Task 4: 扩展现有测试，而不是新建平行测试矩阵 (AC: #1-#6)
  - [ ] 4.1 扩展 `tests/unit/test_config.py`：覆盖多命令加载顺序、plural 优先于 singular、仅 singular 时的 fallback
  - [ ] 4.2 扩展 `tests/unit/test_merge_queue.py`：覆盖多命令全部成功、第二条失败即 short-circuit、每条命令独立 `shlex.split()`、仅 singular 时 fallback
  - [ ] 4.3 如模板断言需要更新，优先在现有 `test_config.py` / `test_config_workflow.py` 中修正；不要为本 story 发明 `tests/e2e/` 目录

## Dev Notes

### 核心约束

- **保持 Story 4.5 的 post-merge contract**：Regression 继续在主仓库 main workspace 执行，不改成 story worktree，也不改成新的 pre-merge gate。
- **保持单个 regression task 合同**：`_dispatch_regression_test()` 只创建 1 条 `TaskRecord`，`merge_queue.regression_task_id` 也只跟踪 1 个 task；多命令只是该 task 内部的顺序步骤，不是 3 条独立任务。
- **沿用现有 subprocess 模式**：继续使用 `asyncio.create_subprocess_exec`、`shlex.split()`、`cleanup_process()` 三阶段清理协议；不要引入 `shell=True` 或额外 wrapper shell。
- **沿用现有失败摘要链路**：`_run_regression_test()` 的 `error_message` 会被 `check_regression_completion()` 读取并传给 `regression_failure` approval payload，多命令场景必须保留这条链路。
- **超时选择必须明确**：不要留下“共享总预算还是独立超时都可以”这类开放式实现选择；本 story 明确要求“每条命令独立使用 `timeout.structured_job`”。
- **测试层级以当前仓库为准**：仓库当前测试目录是 `tests/unit/`、`tests/integration/`、`tests/smoke/`、`tests/performance/`；没有 `tests/e2e/` 目录。

### 预期修改文件

- `src/ato/config.py` — 新增 plural 配置字段与 `get_regression_commands()` helper
- `src/ato/merge_queue.py` — 在现有 `_run_regression_test()` 内实现多命令顺序执行
- `ato.yaml.example` — 更新 regression 配置示例
- `tests/unit/test_config.py` — 配置加载 / 优先级 / fallback 测试
- `tests/unit/test_merge_queue.py` — regression runner 的多命令回归测试

### Project Structure Notes

- 本 story 不需要改动 `models/db.py`、`models/migrations.py`、`state_machine.py`、`core.py` 的 schema / phase / approval 类型
- Regression 相关实现仍集中在 `src/ato/merge_queue.py`，不要把多命令逻辑扩散到新的 orchestration 层
- 配置加载仍统一走 `load_config()`，不要在其他模块直接读取 YAML

### References

- [Source: _bmad-output/planning-artifacts/implementation-readiness-report-2026-03-24.md — C8 Regression 测试执行缺口]
- [Source: _bmad-output/implementation-artifacts/4-5-regression-test-merge-integration.md — 当前 post-merge regression contract]
- [Source: _bmad-output/project-context.md — 测试组织 / subprocess / Pydantic v2 规则]
- [Source: src/ato/config.py — `ATOSettings`, `load_config()`]
- [Source: src/ato/merge_queue.py — `_dispatch_regression_test()`, `_run_regression_test()`, `check_regression_completion()`]
- [Source: ato.yaml.example — 当前 regression 配置模板]
- [Source: tests/unit/test_config.py — 现有 config/template 测试]
- [Source: tests/unit/test_merge_queue.py — 现有 regression runner 测试]

## Dev Agent Record

### Agent Model Used

Codex GPT-5

### Debug Log References

### Completion Notes List

### Change Log

- 2026-03-28: validate-create-story 修订 —— 保持单 `regression_task_id` / 单 task 合同；把样例命令改为当前仓库真实存在的 `unit/integration/smoke`；收紧为“每条命令独立超时、失败即中止”；将测试落点固定到现有 `test_config.py` / `test_merge_queue.py`

### File List
