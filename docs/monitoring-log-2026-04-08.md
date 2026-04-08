# ATO 执行监控日志 — 2026-04-08

## 监控目标
- tmux session2 右侧面板：ato cli
- tmux session1：ato tui

## 问题记录

---

### [11:27 CST] 首次检查 — 发现问题

#### 1. BUG：单元测试失败 `test_initial_dispatch.py:429`

**严重度**: HIGH  
**文件**: `tests/unit/test_initial_dispatch.py:429`  
**现象**: `test_creating_initial_dispatch_reuses_structured_job_pipeline` 断言失败  
```
assert dispatched_task.expected_artifact == "initial_dispatch_requested"
实际值: '/private/.../s-create.md'（文件路径）
```
**根因**: `src/ato/task_artifacts.py:14-15` 中 `derive_phase_artifact_path` 对 `"creating"` 阶段返回了真实路径 `project_root / ARTIFACTS_REL / f"{story_id}.md"`，导致 `core.py:1852-1863` 的 walrus 赋值选择了路径分支而非 fallback `"initial_dispatch_requested"`。测试断言未随代码更新同步。  
**影响**: 1 个单元测试失败，但不影响运行时功能。  
**建议修复**: 更新测试断言以匹配实际行为（检查路径以 `s-create.md` 结尾）。

---

#### 2. WARNING：`claude_post_result_timeout` 超时警告

**严重度**: MEDIUM  
**时间**: 03:35:17Z / 03:36:12Z  
**现象**: 两个 story 均出现 `claude_post_result_timeout`：
- `8-1-enabler-python-docx-engine` — 完成 (cost=$1.73)，30s 后超时
- `3-8-mermaid-diagram-generation` — 完成 (cost=$2.18)，30s 后超时  
**分析**: Claude CLI 返回结果后，后处理（artifact 采集/状态更新）未在 30s 内完成。可能是 I/O 延迟或 orchestrator 端处理瓶颈。  
**影响**: 不影响结果正确性，但可能延迟状态转换。

---

#### 3. WARNING：`merge_queue.py` 代码质量 — `_run_pre_merge_gate` 变量作用域

**严重度**: LOW（非崩溃，但代码可维护性差）  
**文件**: `src/ato/merge_queue.py:590-615`（提交 `e5ad28b`）  
**现象**: `second_result` 在 `try` 块内赋值，但在 `finally` 之后的 614 行使用。  
**分析**: 经验证 **不会** 产生 `UnboundLocalError` — 到达 614 行的唯一代码路径必然经过 603 行赋值。但变量跨 `try/finally` 使用是 bad practice，未来重构可能引入 bug。  
**建议**: 在 try 之前初始化 `second_result = None`，614 行加 null check。

---

#### 4. WARNING：`merge_queue.py` 竞态条件 — 审批创建先于锁释放

**严重度**: MEDIUM  
**文件**: `src/ato/merge_queue.py:674-693`（提交 `e5ad28b`）  
**现象**: `_block_pre_merge_for_preflight_failure` 中先 `create_approval`（674 行）再 `set_current_merge_story(None)`（693 行）。如果 orchestrator 快速轮询发现 approval 并处理，在 lock 释放前可能重新入队，导致队列状态不一致。  
**建议**: 将 lock 释放移到 approval 创建之前，或用事务包装。

---

#### 5. INFO：代码重复 — `_dirty_files_from_porcelain`

**严重度**: LOW  
**文件**: `src/ato/transition_queue.py:111` 和 `src/ato/merge_queue.py:243`  
**现象**: 同一函数在两个模块中重复定义。如果解析逻辑需要修改，必须同步更新两处。  
**建议**: 提取到共享工具模块。

---

#### 6. INFO：新提交 `e5ad28b feat: enforce worktree boundary gates`

大规模提交（+2900 行），涵盖 worktree preflight 检查、pre-review/pre-merge 边界门控、preflight_failure 审批流程等。已自动提交到 main 分支。

---

### 当前状态摘要

| 项目 | 状态 |
|------|------|
| 运行中 Story | 3-8 (developing), 8-1 (developing) |
| 待审批 | 无 |
| 累计成本 | $15.64 |
| 测试 | 1001 passed, 1 failed |
| 错误/异常 | 2x post_result_timeout (WARNING) |

---

### [11:55 CST] 第三次检查 — 发现严重状态卡死

#### 7. BUG (CRITICAL)：Orchestrator 任务状态卡死 — 已完成的 task 仍显示 running

**严重度**: CRITICAL  
**影响**: 两个 story 的开发流程完全卡住，无法进入下一阶段

**现象**:
- CLI 日志在 03:36:12Z（11:36 本地时间）后 **完全静默**，已超 20 分钟无输出
- DB 中 task `c451b226`（8-1）和 `14d26d3b`（3-8）状态为 `running`
- 对应 PID 9022/9023 **已死亡**（ps 查无此进程）
- DB 中 cost_usd 仍为 $0.00，未写入实际费用（$1.73 / $2.18）
- Orchestrator 进程 PID 9017 仍存活（STAT=S+），但无子进程，无输出

**时间线**:
```
03:34:47Z  8-1 Claude 返回结果 (cost=$1.7283)
03:35:17Z  8-1 post_result_timeout (30s)
03:35:42Z  3-8 Claude 返回结果 (cost=$2.1803)
03:36:12Z  3-8 post_result_timeout (30s)
03:36:12Z+ 完全静默至今（25+ 分钟）
```

**根因分析**:
- `claude_post_result_timeout` 表明 Claude CLI 返回了结果，但 orchestrator 的后处理（artifact 采集、状态更新）未在 30s 内完成
- 超时后 orchestrator 未能将 task 从 `running` 转为 `completed`
- Worker PID 已退出但 DB 未更新，说明 post-result 处理路径发生异常或死锁
- Orchestrator 主循环可能在等待已死的子进程或卡在某个 await 上

**建议**:
1. 检查 `src/ato/subprocess_mgr.py` 中 post_result_timeout 后的恢复逻辑
2. 检查 `src/ato/recovery.py` 是否应检测 dead PID 并自动回收
3. 短期：可能需要手动重启 `ato start` 触发 crash recovery
4. 长期：post_result_timeout 后应强制将 task 标记为 completed/failed 并继续

**处置**：卡死持续 65 分钟无自愈，于 04:33:53Z 手动 Ctrl-C 终止 PID 9017 并重新启动 `ato start`。

---

### [12:34 CST] 手动重启恢复成功

Crash recovery 在 5.8ms 内完成：
- `✔ 数据完整性检查通过`
- 检测到 2 个暂停任务（3-8, 8-1），正常恢复
- 2 个任务已重新调度（PID 63497 / 63498）
- 注意：recovery 将任务作为**全新调度**而非 resume，之前的开发成果（$1.73 + $2.18）需重做
- Orchestrator 以 3.0s 轮询间隔恢复运行

---

### [12:39 CST] 重做任务 Claude CLI exit_code=1 + crash_recovery 审批

#### 8. BUG：重启后 Claude CLI exit_code=1（两个 story 均受影响）

**严重度**: MEDIUM  
**现象**:
- 8-1 完成 (cost=$1.87) 但 exit_code=1 → `CLIAdapterError: Claude CLI exited with code 1`
- 3-8 完成 (cost=$1.85) 但 exit_code=1 → 同上
- stderr_preview 为空，error category=unknown
- Traceback: `recovery.py:2850 → subprocess_mgr.py:480 → subprocess_mgr.py:344 → claude_cli.py:373`

**分析**: Claude CLI 成功返回了结果（含 cost），但进程退出码为 1。可能原因：
- worktree 中残留了上一次运行的状态导致冲突
- Claude CLI 的 `--dangerously-skip-permissions` 在特定条件下返回非零码
- 或 adapter 在解析结果后遇到了一个非致命但导致退出码为 1 的问题

**处置**: 
- 04:45:58Z 批准两个 `crash_recovery` 审批（decision=restart）
- 两个 story 再次重新调度（PID 72220 / 72335），继续 developing 阶段

**累计影响**: 同一批任务已重试 3 次，消耗约 $1.73+$2.18+$1.87+$1.85 = ~$7.63 额外开发成本

---

### [13:40 CST] 3-8 完成开发进入 reviewing / 8-1 再次卡死

**3-8 状态变化**: developing → reviewing
- 开发成功完成 (cost=$1.17)，59 个测试 + lint/typecheck/build 全通过
- 但同时产生了一个 stale `crash_recovery` 审批（旧 task 残留）
- 已 abandon 该审批（story 已在 reviewing 阶段）
- 有一条 `dispatch_batch_restart_success_superseded` 警告：story phase 提前推进

**8-1 再次触发 post_result_timeout**（问题 #7 复现，第 3 次）:
- 05:39:46Z 完成 (cost=$4.00)
- 05:40:16Z post_result_timeout (30s)
- PID 95428 已死，DB 仍为 running
- 05:45:34Z 手动重启 orchestrator，recovery 3ms 完成
- 8-1 重新调度（PID 23646）

**结论**: `post_result_timeout → stuck` 是一个系统性 bug，每次 Claude CLI 任务完成后都会复现。累计成本 $21.17。

---

### [13:53 CST] 新 bug 发现 + 3-8/8-1 多重问题处理

#### 9. BUG：transition_queue TimeoutError 导致 crash_recovery

**严重度**: HIGH  
**文件**: `transition_queue.py:284`, `recovery.py:698`  
**现象**: 8-1 task c451b226 完成后提交 `dev_done` 转换事件时，`submit_and_wait` 在 `asyncio.wait_for` 超时，抛出 `TimeoutError`  
**Traceback**: `recovery.py:2914 → recovery.py:698 → transition_queue.py:284`  
**分析**: 与 `post_result_timeout`（问题 #7）不同根因。这里是 transition queue 处理转换事件的 await 超时，可能因为 queue worker 在处理前一个事件（worktree_preflight pre_review gate）时阻塞。

#### 10. BUG：worktree_finalize exit_code=1 导致 3-8 进入 blocked 死循环

**严重度**: HIGH  
**现象**: 
- 3-8 pre_review gate 发现 UNCOMMITTED_CHANGES → 正确触发 worktree_finalize
- finalize 任务（52bb030b）Claude CLI 返回 exit_code=1（同问题 #8）
- finalize 失败后 story 变为 `blocked`，且无 pending approval → **死胡同**
- 实际上 finalize 已成功提交了代码（commit 4f62530，+2953 行），只是退出码错误

**处置**: 
- 手动 `rollback-story` 到 reviewing
- 3-8 成功进入 reviewing（convergent_loop round 1，codex code-review 已启动）

**处置 8-1 审批**:
- abandon 旧 crash_recovery（003f，stale task）
- approve preflight_failure（e7c9，manual_commit_and_retry）
- 8-1 新 developing task（358fa504）仍在运行

**当前状态**: 3-8 reviewing (codex), 8-1 developing, 累计 $22.46

---

### [14:05 CST] 8-1 手动提交 + rollback 到 reviewing

#### 11. BUG：preflight_failure retry 在 Blocked 状态下被状态机拒绝

**严重度**: MEDIUM  
**现象**: `TransitionNotAllowed: Can't dev_done when in Blocked.`  
**分析**: 批准 preflight_failure (manual_commit_and_retry) 后，orchestrator 尝试发送 `dev_done` 事件，但 story 已在 Blocked 状态，状态机不允许此转换。审批虽 consumed 但 retry 无效。  
**根因**: preflight_failure 审批处理器没有考虑 story 可能已经变为 Blocked 的情况

**处置**:
1. 手动在 8-1 worktree 执行 `git add -A && git commit`（修复 prettier 后）
2. 手动 `rollback-story 8-1 --phase reviewing`
3. 8-1 成功进入 reviewing（codex code-review PID 56546）

**当前状态**: 3-8 fixing (review round 1 完成，5 issues), 8-1 reviewing (codex), 累计 $22.46
