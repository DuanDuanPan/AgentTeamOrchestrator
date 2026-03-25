# Story 验证报告：5.1b 崩溃恢复性能测试与验证

验证时间：2026-03-25 17:18:27 CST
Story 文件：`_bmad-output/implementation-artifacts/5-1b-crash-recovery-performance-testing.md`
验证模式：`validate-create-story`
结果：PASS（已应用修正）

## 摘要

该 story 的方向与 Epic 5、NFR1 以及当前 `RecoveryEngine` 实现一致，但原稿里有 3 个会误导开发实现的缺口，已在 story 文件中修正：

1. 原稿没有明确 NFR1 的计时边界，而当前 `run_recovery()` 会启动后台恢复任务并提前返回，容易把不属于 SLA 的异步阶段错误地算进性能指标。
2. 原稿把 `@pytest.mark.perf` 写成“CI 可选运行”，但当前仓库的 `pyproject.toml` 既未注册该 marker，也没有任何默认排除规则；仅加 marker 并不会自动变成“可选”。
3. 原稿要求再次验证四分类集成测试，却没有指出仓库已经有较完整的 `tests/integration/test_crash_recovery.py`，容易诱导开发者在新建的 performance suite 里复制已有功能回归。

## 已核查证据

- 本地仓库工件：
  - `_bmad-output/planning-artifacts/epics.md`
  - `_bmad-output/planning-artifacts/prd.md`
  - `_bmad-output/planning-artifacts/architecture.md`
  - `_bmad-output/implementation-artifacts/5-1a-crash-recovery-auto-resume.md`
- 当前代码与测试：
  - `src/ato/recovery.py`
  - `tests/integration/test_crash_recovery.py`
  - `tests/unit/test_recovery.py`
  - `tests/conftest.py`
  - `pyproject.toml`

## 发现的关键问题

### 1. NFR1 计时边界未锚定到当前 `RecoveryEngine` 的同步恢复窗口

原稿把 AC1 写成“崩溃恢复完整流程 ≤30 秒”，但没有进一步限定如何计量。对当前仓库来说，这会直接误导实现：

- `src/ato/recovery.py::run_recovery()` 在 crash 路径中会对 `reschedule`/`reattach` 创建后台 task。
- `run_recovery()` 自身会记录 `duration_ms`，但并不等待这些后台 task 实际完成。
- `src/ato/recovery.py::await_background_tasks()` 明确是额外的清理/等待入口。

如果 story 不把这个边界写清楚，开发者很容易把 reattach 监控循环或后台 re-dispatch 的 agent 执行时间一并算进性能 SLA，得到既不稳定、也不符合 Epic 5.1b 原始意图的测试。

已应用修正：

- AC1 明确以 `RecoveryEngine.run_recovery()` 的同步执行窗口为 NFR1 计时边界。
- Tasks 中新增要求：`await_background_tasks()` 只能在计时结束后用于清理，不进入性能断言窗口。
- Dev Notes 补充“计时包含/不包含”的边界说明。

### 2. `perf` marker 的使用方式与当前 pytest 配置不一致

原稿在 Task 1.4 中写“添加 `@pytest.mark.perf` 标记，CI 可选运行”，但当前代码事实是：

- `pyproject.toml` 的 `[tool.pytest.ini_options]` 里目前只有 `asyncio_mode = "auto"`。
- 还没有 `markers = [...]` 对 `perf` 进行注册。
- 也没有 `addopts = -m "not perf"` 或 CI 命令约定来默认排除该测试组。

这意味着“仅添加 marker 就可选运行”在当前仓库里并不成立。pytest 默认仍会收集并执行这些测试；未注册 marker 还会带来配置噪音。

已应用修正：

- Task 5.3 明确要求在 `pyproject.toml` 注册 `perf` marker。
- Task 5.4 明确要求写出显式运行命令，例如 `uv run pytest tests/performance/ -m perf`。
- Project Structure Notes 补充说明：marker 只负责筛选，不会自动把测试组变成“默认跳过”。

### 3. 性能 story 有复制现有四分类功能回归的风险

原稿要求“验证现有集成测试完整性”，但没有进一步约束补测位置。结合当前仓库，很容易演变为错误的实现方向：

- `tests/integration/test_crash_recovery.py` 已经覆盖 PID 存活、artifact 存在、structured job 无 artifact、interactive session 需要人工处理等四类路径。
- 该文件还覆盖了 structlog 字段、多 story 场景、normal recovery 以及 convergent loop 恢复分支。
- `tests/unit/test_recovery.py` 已经覆盖大量边界条件和辅助函数行为。

因此 5.1b 的主要增量应该是性能验证和必要的缺口补测，而不是在 `tests/performance/` 再复制一次功能验证矩阵。

已应用修正：

- AC3 明确写成“若现有集成测试已覆盖，则只补齐缺口，不复制同一套功能 case 到性能测试文件中”。
- Task 4.2 改为“仅在现有 `tests/integration/test_crash_recovery.py` 中补缺口”。
- Dev Notes 新增“现有测试清单（已有，先复用再补齐）”并明确性能 story 的职责边界。

## 已应用增强

- 为 story 增加了 create-story 模板中的 validation note 注释。
- 在 Tasks / Dev Notes 中补入 `await_background_tasks()` 的非计时清理约束。
- 将 perf marker 的实施要求收敛到 `pyproject.toml` 注册 + 显式 pytest 命令。
- 增加“不要把 `expected_artifact=\"transition_submitted\"` 当作 complete-path artifact 证据”的 guardrail，避免性能 fixture 误用当前 `core.py` 内部哨兵值。
- 在 Change Log 中记录本次 validate-create-story 的具体修订点。

## 剩余风险

- 这次验证只修订了 story 文档，没有实际实现 `tests/performance/` 或运行新的 perf 断言；真实阈值是否在共享 CI 环境中稳定，还需要实现后再用实测数据确认。
- story 中保留了少量“当前代码规模/最近提交”的信息性说明；这些内容不影响实现路径，但后续如果仓库继续演进，可能出现文档漂移，届时应以当前代码为准。

## 最终结论

修正后，该 story 已与 Epic 5.1b 的性能目标、当前 `RecoveryEngine` 的异步边界、现有 recovery 测试布局以及 pytest 配置现实对齐，可以继续保持 `ready-for-dev`。高风险误导点已经移除，开发实现会更聚焦于真正的增量工作：性能基准、分层计时、marker 注册和缺口补测，而不是重复已有功能测试或误测后台 agent 执行时长。
