# ATO 根因分析与修复方案 — 2026-04-08

**状态**: 最终分析版  
**方法**: Systematic Debugging，基于监控文档与当前源码逐项复核  
**输入文档**:
- `docs/bug-report-2026-04-08.md`
- `docs/monitoring-log-2026-04-08.md`
- `docs/monitoring-timeline-2026-04-08.md`

---

## 一、最终结论

这次事故不是单点 bug，而是 **CLI 已返回结果之后，ATO 没有一个不可卡死、可恢复、可观测的终态收敛边界**。

最核心的根因是：

1. `SubprocessManager.dispatch()` 在 adapter 返回后，要执行 activity flush、task 终态写入、cost_log 写入，然后才 `_unregister_running()`；这些 await 没有总超时和兜底路径。一旦其中任意一步挂起，task 会永久停在 `running`，PID 已死也不会被回收。
2. `ClaudeAdapter.execute()` 当前先看进程退出码，再看是否已收到 `type: result`。这会把“Claude 已返回有效 result 但进程清理/退出码异常”的情况误判为失败。
3. `TransitionQueue.submit_and_wait()` 默认 5 秒 timeout 太短，且 timeout 后调用方会把“等待提交确认失败”混同为“业务转换失败”，造成 recovery/approval 级联误判。
4. preflight/retry 路径缺少状态感知恢复：story 已经在 `blocked` 时，审批处理仍直接提交 `dev_done/fix_done`，状态机拒绝后 approval 仍被当成已成功处理。

因此，根本修复必须围绕 **终态收敛的幂等性和超时边界**，而不是只增大某一个 timeout 或只在某个异常处补 log。

---

## 二、级联链路

监控中出现的关键链路为：

```text
Claude CLI 返回 result
  -> claude_post_result_timeout
  -> SubprocessManager 后处理未完成
  -> task 仍为 running，PID 已退出，cost 未写入
  -> orchestrator 静默卡住，需要重启
  -> recovery 重新调度
  -> Claude result + exit_code=1 被误判为 CLIAdapterError
  -> crash_recovery / preflight_failure 审批增加
  -> worktree boundary gate / transition_queue timeout 放大故障
  -> blocked 状态下 retry 被状态机拒绝，人工 rollback
```

其中 `post_result_timeout` 是触发点，但不是完整根因。它只说明 Claude 子进程在 result 后没有按预期快速退出；真正造成系统停滞的是 result 之后的 ATO 终态落库和资源注销路径不具备兜底能力。

---

## 三、逐项分析

### BUG-001: post_result_timeout 后 orchestrator stuck [P0]

**分析状态**: 已完成，根因确认，但修正了原 RCA 的部分推断。

**监控证据**:
- 8-1 和 3-8 在 `03:35:17Z` / `03:36:12Z` 出现 `claude_post_result_timeout`。
- 对应 worker PID 已死亡，但 DB 中 task 仍是 `running`，`cost_usd=0.00`。
- orchestrator 主进程仍存活但无输出、无子进程、无自愈。

**源码证据**:
- `src/ato/adapters/claude_cli.py:313-324` 中 `post_result_timeout` 只包住 result 后等待 stderr/proc.wait 的阶段；超时后会 cleanup 并继续返回 result。
- `src/ato/subprocess_mgr.py:390-446` 的成功路径中，`_flush_latest_activity()`、`get_connection()`、`update_task_status()`、`insert_cost_log()` 都在 `_unregister_running()` 之前。
- `src/ato/subprocess_mgr.py:447-452` 只清理 delayed flush task，不负责把 running task 从内存或 DB 中兜底收敛为终态。
- `src/ato/models/db.py:232-247` 每次 `get_connection()` 都创建独立 aiosqlite 连接；当前源码没有连接池。

**根因**:

终态后处理没有总超时和强制回收边界。任何一个 await 卡住，都会导致：

- DB 终态不写入，task 永久 `running`
- `_unregister_running()` 不执行，dead PID 留在内存中
- semaphore slot 不释放
- 上层调度等待 dispatch 完成，表现为 orchestrator 静默

**对原 RCA 的修正**:

原文把具体机制写成“DB 连接池耗尽或 WAL 锁竞争”。当前源码可证明的是“终态后处理链缺少总超时和 finally 级资源注销”；不能把“连接池耗尽”作为已验证事实，因为当前 `get_connection()` 没有连接池。

**根本修复方向**:

- 抽出 `_finalize_success()` / `_finalize_failure()`，用 `asyncio.timeout()` 包住终态落库。
- `_unregister_running(task_id)` 必须进入 outer `finally`，不应位于终态 DB 写入之后。
- 增加 raw SQL fallback：正常 `update_task_status()` 失败时，仍尽力把 task 置为 `completed` 或 `failed` 并写入已知 cost/text。
- 增加 dead PID watchdog，定期检测 `_running` 中 PID 已死但 DB 仍 running 的 task。
- activity flush 失败不得阻塞终态收敛；flush 最多影响 last_activity，不应影响 completed/failed。

---

### BUG-002: Claude CLI exit_code=1 误报 [P0]

**分析状态**: 已完成，根因确认。

**监控证据**:
- 多次出现 “Claude 完成，含 cost/text_result，但 exit_code=1，stderr 为空”。
- 上层进入 `crash_recovery`，导致重复调度和成本增加。

**源码证据**:
- `src/ato/adapters/claude_cli.py:352-379` 先检查 `exit_code != 0` 并直接抛 `CLIAdapterError`。
- `result_data` 在 `src/ato/adapters/claude_cli.py:305-309` 已经可能由 `_consume_stream()` 返回，但非零退出码分支没有先使用它。
- `src/ato/models/schemas.py:622-638` 的 `ClaudeOutput.from_json(..., exit_code=1)` 会把 `status` 设为 `failure`。

**根因**:

Adapter 混淆了“业务 result 事件”和“进程退出码”。对 stream-json 模式而言，`type: result` 是 Claude CLI 已完成 agent 工作的显式信号；非零退出码更可能代表 post-task cleanup、hook 或 session 状态异常。

**关键实现注意事项**:

修复不能简单写成 `ClaudeOutput.from_json(result_data, exit_code=1)` 后返回，因为这会生成 `status="failure"`，上层仍可能按失败处理。应明确建模：

- task 业务结果为 success
- 原始 process exit code 为 warning/metadata

如果不扩 schema，短期可在“已有 result_data 且非零退出码”时把返回对象的 `exit_code` 规范化为 0，同时记录 warning log 和 cost_log warning 字段；更长期应新增 `process_exit_code` 或 adapter warning metadata。

**根本修复方向**:

- `result_data is not None` 时优先返回业务结果。
- `result_data is None and exit_code != 0` 才抛 `CLIAdapterError`。
- 测试覆盖五类场景：正常 result+0、result+1、result+stderr、无 result+1、timeout/kill。

---

### BUG-003: transition_queue submit_and_wait timeout [P1]

**分析状态**: 已完成，确认为放大器和恢复路径缺陷。

**监控证据**:
- `submit_and_wait` 在提交 `dev_done` 时超时，随后 task 被误导向失败/恢复路径。

**源码证据**:
- `src/ato/transition_queue.py:261-284` 默认 `timeout_seconds=5.0`，直接 `asyncio.wait_for(completion_future, timeout=timeout_seconds)`。
- `src/ato/recovery.py:822-840` 的 `_submit_transition_event()` 调用 `submit_and_wait(event)`，没有传更长 timeout。
- `src/ato/recovery.py:3072-3080` 的通用异常处理会调用 `_mark_dispatch_failed(task)`。
- `src/ato/core.py:2909-2930` 中 preflight approval retry 捕获 `TimeoutError` 后只 log warning，最后仍 `return True`。

**根因**:

这是“状态转换提交确认 timeout”而不是业务失败。5 秒对包含 worktree preflight、git status/diff、finalize 的队列任务过短。更严重的是 `asyncio.wait_for()` 超时会取消 `completion_future`；队列 consumer 之后即使完成事件，也无法把结果反馈给调用方。调用方因此不知道 transition 是未执行、执行中，还是已提交。

**根本修复方向**:

- 把默认 timeout 提高到能覆盖 preflight gate 的实际耗时，例如 30 秒起。
- 对 completion future 使用 `asyncio.shield()` 或改成可查询的 ack record，避免等待方 timeout 取消队列端完成通知。
- 调用方区分 `TimeoutError`、`StateTransitionError`、普通异常；timeout 应创建可重试/待确认状态，而不是直接标记 task failed。
- 增加 `queue_wait_duration`/`transition_processing_duration` 日志或 metrics，避免增大 timeout 后掩盖真正卡死。

---

### BUG-004: worktree_finalize exit_code=1 -> blocked 死胡同 [P0/P1]

**分析状态**: 事故现象成立，但原 RCA 对当前源码的解释需要修正。

**监控证据**:
- pre_review gate 发现 `UNCOMMITTED_CHANGES` 后触发 finalize。
- finalize 实际提交了 commit，但 exit_code=1。
- story 进入 `blocked` 且没有有效 pending approval，需要手动 rollback。

**当前源码证据**:
- `src/ato/subprocess_mgr.py:562-574` 中 `dispatch_finalize()` 捕获 `CLIAdapterError` 后继续做本地 git 验证。
- `src/ato/subprocess_mgr.py:576-620` 会比较 finalize 前后的 HEAD，并返回 `WorktreeFinalizeResult`。
- `src/ato/transition_queue.py:383-400` 在 finalize 后会二次 preflight，仍失败才创建 `preflight_failure` approval。
- `src/ato/transition_queue.py:430-439` 只有 `finalize_mgr.dispatch_finalize()` 抛出非预期异常时才静默 return。

**修正后的判断**:

在当前源码下，单纯的 `CLIAdapterError(exit_code=1)` 不应直接导致“无 approval 的 blocked 死胡同”，因为 `dispatch_finalize()` 已经捕获该异常并用 git 结果兜底。因此原 RCA 的“exit_code=1 被 transition_queue except 捕获后静默返回”只适用于旧代码或非 `CLIAdapterError` 的异常/挂起路径。

这并不否定 BUG-004 的事故现象。更准确的根因是：preflight/finalize 的恢复不变量不够强。系统没有明确保证以下两件事之一必然发生：

- worktree 已干净 -> transition 继续
- worktree 仍脏或 finalize 不确定 -> 创建可操作的 approval

**根本修复方向**:

- `_dispatch_finalize_for_preflight_failure()` 不应只 log 后 return。即使 finalize 抛异常，也要回到“重新检查 worktree / 创建 approval”的收敛路径。
- 如果 finalize 后 worktree clean，直接允许 gate 继续；如果 dirty 或无法判断，创建 `preflight_failure` approval，payload 包含异常、dirty files、gate_type、retry_event。
- 对 merge_queue 的 pre-merge finalize 使用同一不变量。
- 测试要覆盖：finalize CLIAdapterError 但 commit 成功、finalize 非 CLI 异常但 worktree clean、finalize 异常且 worktree dirty。

---

### BUG-005: preflight_failure 审批在 Blocked 状态下被状态机拒绝 [P1]

**分析状态**: 已完成，根因确认。

**监控证据**:
- 审批 `manual_commit_and_retry` 后出现 `TransitionNotAllowed: Can't dev_done when in Blocked.`。
- approval 被 consumed，但 retry 没有推进。

**源码证据**:
- `src/ato/core.py:2898-2930` 中审批处理器直接构造 `TransitionEvent` 并提交 `retry_event`。
- `StateTransitionError` 在 `src/ato/core.py:2914-2920` 被捕获后只 log，不创建恢复 approval。
- 方法最后仍 `return True`，调用方会认为审批处理成功。

**根因**:

审批处理没有检查 story 当前 phase，也没有把“retry 被状态机拒绝”转成新的人工恢复入口。

**根本修复方向**:

- 处理 `manual_commit_and_retry` 前读取 story 当前 phase。
- 如果 story 已 `blocked`，创建 `blocked_recovery` 或复用 `preflight_failure` approval，推荐 `rollback_to_previous_phase` / `manual_requeue`，不要提交 `dev_done`。
- 如果 `submit_and_wait()` 抛 `StateTransitionError` 或 `TimeoutError`，不要静默返回 True；应创建新的待处理恢复项，或保留原 approval 未消费。

---

### BUG-006: 单元测试断言未同步更新 [P1]

**分析状态**: 已完成，根因确认。

**源码证据**:
- `src/ato/task_artifacts.py:14-15` 对 `creating` 返回真实 artifact path。
- `src/ato/core.py:1852-1863` 使用 walrus 表达式，有 path 时优先写入真实路径，否则 fallback 到 `"initial_dispatch_requested"`。
- `tests/unit/test_initial_dispatch.py:429` 仍断言 `expected_artifact == "initial_dispatch_requested"`。

**根因**:

运行时行为已从“占位 expected artifact”变为“真实 canonical artifact path”，测试没有同步更新。

**根本修复方向**:

- 测试应断言 `expected_artifact` 指向 `ARTIFACTS_REL / "s-create.md"`，而不是旧占位字符串。
- 如果产品语义仍要求 placeholder，则应回滚 `derive_phase_artifact_path("creating")` 的行为；但从当前代码看，真实路径更符合 recovery/artifact 统一处理。

---

### BUG-007: BMAD semantic parser 60s timeout [P2]

**分析状态**: 已完成，根因确认。

**监控证据**:
- 多次 `bmad_semantic_fallback_failed: Claude CLI timed out after 60s`。
- 随后 `bmad_parse_failed: Both deterministic and semantic parsing failed`，触发 `needs_human_review`。

**源码证据**:
- `src/ato/adapters/bmad_adapter.py:178-221` 先走 deterministic fast-path；返回 `None` 时才进入 semantic fallback。
- `src/ato/adapters/bmad_adapter.py:221-275` semantic fallback 抛异常后只记录 warning，最终返回 `parse_failed`。
- `src/ato/adapters/semantic_parser.py:117-142` 默认 `_DEFAULT_TIMEOUT = 60`，构造器默认 timeout=60。
- `src/ato/adapters/semantic_parser.py:176-184` 调用 `ClaudeAdapter.execute(..., {"timeout": self._timeout})`。
- `src/ato/recovery.py:762-767` `_create_bmad_adapter()` 固定返回 `ClaudeSemanticParser()`，没有从 settings 传入更长 timeout。
- deterministic parser 对 QA 的 `Recommendation: Approve` 已有 fast path，但 code-review 的显式 verdict 主要依赖 summary/category section；对一些自然语言 PASS/Approve 输出仍会触发 semantic fallback。

**根因**:

格式漂移导致 deterministic parser 未命中后，系统依赖一个固定 60 秒的 LLM semantic fallback。这个 fallback 不是配置驱动，也没有针对常见“显式通过/无阻塞项”的 deterministic 快速路径补强，因此在 3000-4000 字符输入或 Claude 繁忙时容易超时并升级为人工审批。

**根本修复方向**:

- semantic parser timeout 配置化，不要硬编码 60 秒。
- 增加 deterministic 快速路径，覆盖常见明确结论：
  - `Verdict: PASS`
  - `STATUS: PASS`
  - `Recommendation: Approve`
  - `No blocking findings`
  - `0 blocking` / `0 patch` 等稳定模式
- 对 parse_failed 的 payload 保留原始 parser_mode、输入长度、timeout、skill_type，便于后续区分“格式不识别”和“LLM 超时”。
- 对 review/QA 的最终输出模板增加机器可解析 footer，减少 semantic fallback 调用。

---

### BUG-008: merge_queue approval 创建先于 lock 释放 [P2]

**分析状态**: 已完成，确认为竞态风险；监控未证明一定触发。

**源码证据**:
- `src/ato/merge_queue.py:674-688` 先创建 `preflight_failure` approval。
- `src/ato/merge_queue.py:689-693` 后执行 `complete_merge()` 和 `set_current_merge_story(db, None)`。

**根因**:

approval 一旦 commit 就对其他 poll/CLI handler 可见，但 merge queue lock 仍可能尚未释放。若审批被快速处理，可能在旧 lock 还存在时尝试重新入队或推进，形成队列状态不一致。

**边界说明**:

这是代码审查发现的竞态窗口，不是监控日志中已直接复现的主故障。它仍应修，因为 preflight failure 正是事故中的高频路径。

**根本修复方向**:

- 先把 merge entry 标记 failed / 释放 `current_merge_story`，再创建用户可见 approval。
- 更理想：用单事务更新内部状态，commit 后再发 nudge；确保用户可见 approval 时系统内部 lock 已处于可重试状态。

---

### BUG-009: `_dirty_files_from_porcelain` 重复定义 [P3]

**分析状态**: 已完成，确认为维护性问题。

**源码证据**:
- `src/ato/transition_queue.py:111-122` 定义 `_dirty_files_from_porcelain()`。
- `src/ato/merge_queue.py:243-254` 定义同名同逻辑函数。

**根因**:

pre-review gate 和 pre-merge gate 并行演进时复制了 porcelain 解析逻辑。当前两处逻辑一致，因此不是本次运行时故障根因；但未来修改 rename、quoted path、untracked path 解析时容易一处漏改。

**根本修复方向**:

- 提取到共享模块，例如 `src/ato/worktree_utils.py`。
- 给该函数增加单测覆盖 rename、space path、short malformed line、empty line。

---

### BUG-010: `second_result` 跨 try/finally 作用域 [P3]

**分析状态**: 已完成，确认为低风险代码质量问题。

**源码证据**:
- `src/ato/merge_queue.py:594-612` 在 `try/finally` 中计算 `second_result`。
- `src/ato/merge_queue.py:614` 在 `finally` 之后使用 `second_result`。

**判断**:

当前控制流下，到达 line 614 的路径必须经过 line 603 对 `second_result` 的赋值；如果 `preflight_check()` 或 `save_worktree_preflight_result()` 抛异常，会跳出函数，不会执行 line 614。因此当前不构成已知运行时 bug。

**根本修复方向**:

- 初始化 `second_result: WorktreePreflightResult | None = None`。
- `finally` 后显式检查 `if second_result is None: ...`。
- 这能降低未来重构引入 `UnboundLocalError` 的风险。

---

## 四、修复优先级

### P0: 必须先修

1. **BUG-001**: `SubprocessManager` 终态后处理加总超时、兜底落库、finally 注销、dead PID watchdog。
2. **BUG-002**: `ClaudeAdapter` result 优先，非零进程退出码降级为 warning，不再丢弃有效 result。

这两项直接消除“已完成任务永久 running”和“大量有效结果误判失败”。

### P1: 紧随其后

3. **BUG-003**: `submit_and_wait()` timeout/ack 语义修复；不要把 ack timeout 当业务失败。
4. **BUG-004**: preflight finalize 失败路径保证 worktree clean 或 approval 二选一。
5. **BUG-005**: blocked 状态下的 retry 不应消费 approval 后静默失败。
6. **BUG-006**: 修正初始 dispatch 测试断言，解除 CI 噪音。

### P2/P3: 后续治理

7. **BUG-007**: BMAD parser deterministic 快速路径 + semantic timeout 配置化。
8. **BUG-008**: merge_queue 内部 lock 释放先于用户可见 approval。
9. **BUG-009**: porcelain parser 去重。
10. **BUG-010**: `second_result` 显式初始化。

---

## 五、验证计划

| Bug | 测试/验证 | 期望 |
| --- | --- | --- |
| BUG-001 | 注入 `_flush_latest_activity()` 卡住或 DB 写入超时 | dispatch 不永久挂起，task 最终 completed/failed，running PID 被清理 |
| BUG-001 | kill worker 后保留 running 记录 | watchdog 下一个周期识别 dead PID 并恢复 |
| BUG-002 | Claude stream 有 `type: result` 且 process exit_code=1 | 返回成功业务结果，记录 warning，不抛 `CLIAdapterError` |
| BUG-002 | 无 `result` 且 exit_code=1 | 抛 `CLIAdapterError`，保留原错误分类 |
| BUG-003 | transition queue 处理时间超过等待 timeout | caller 不把 task 标记 failed；事件完成状态可被确认或重试 |
| BUG-004 | finalize 抛异常且 worktree clean | gate 继续，不创建 dead-end blocked |
| BUG-004 | finalize 抛异常且 worktree dirty | 创建 `preflight_failure` approval |
| BUG-005 | story phase=`blocked` 时批准 `manual_commit_and_retry` | 创建恢复 approval，不提交非法 `dev_done` |
| BUG-006 | `uv run pytest tests/unit/test_initial_dispatch.py -k creating` | 断言真实 artifact path |
| BUG-007 | `Verdict: PASS` / `Recommendation: Approve` / `0 blocking` 输出 | deterministic parser 直接 approved，不调用 semantic runner |
| BUG-008 | approval 创建与 lock 释放顺序测试 | approval 可见时 merge lock 已释放 |
| BUG-009 | porcelain parser 参数化单测 | 两个调用方共享同一实现 |
| BUG-010 | preflight 异常路径单测 | 不出现未绑定变量，错误路径清晰 |

---

## 六、风险与注意事项

- **不要只增大 timeout**：增大 timeout 能降低误报，但不能解决 BUG-001 的永久挂起。必须有终态 finalizer 和 watchdog。
- **不要把非零 exit code 完全丢弃**：应降级为 warning 并持久化原始 process exit code，方便排查 Claude CLI cleanup/hook 问题。
- **不要在 approval 可见前保留内部锁**：approval 是用户操作入口，创建它之前系统内部状态必须已经处于可操作状态。
- **不要把 parser timeout 当 review fail**：parser timeout 是解析基础设施失败，应进入 parse_failed/needs_human_review 或重试语义，而不是视作代码质量不合格。

---

## 七、本次复核对原 RCA 的修正

1. **BUG-001**: “连接池耗尽”不是当前源码可证明事实。应改为“终态后处理缺少总超时和 finally 级资源注销；DB/WAL 锁只是可能触发源之一”。
2. **BUG-004**: 当前 `dispatch_finalize()` 已捕获 `CLIAdapterError` 并用 git 验证提交结果，因此“exit_code=1 直接导致 transition_queue 静默 return”不是当前源码下的完整解释。应表述为“preflight/finalize 恢复不变量不够强，必须保证 clean 或 approval 二选一”。
3. **BUG-007**: 已补齐源码证据。60s timeout 来自 `ClaudeSemanticParser` 默认值，且 `_create_bmad_adapter()` 没有传配置。
4. **BUG-008/009/010**: 已完成源码分析。它们不是主故障根因，但都是边界门控和维护性风险，应纳入后续修复。

---

## 八、完成状态

| Bug | 分析状态 | 根因类型 |
| --- | --- | --- |
| BUG-001 | 完成 | 主根因 |
| BUG-002 | 完成 | 主触发器 |
| BUG-003 | 完成 | 放大器 / recovery 语义缺陷 |
| BUG-004 | 完成，已修正原 RCA 表述 | 恢复不变量缺失 |
| BUG-005 | 完成 | approval 状态感知缺失 |
| BUG-006 | 完成 | 测试未同步 |
| BUG-007 | 完成 | parser fallback 超时与 deterministic 覆盖不足 |
| BUG-008 | 完成 | 竞态风险 |
| BUG-009 | 完成 | 维护性风险 |
| BUG-010 | 完成 | 维护性风险 |
