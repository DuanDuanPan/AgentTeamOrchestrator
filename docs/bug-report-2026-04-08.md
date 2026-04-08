# ATO Bug 报告 — 2026-04-08 监控期间发现

**报告人**: 自动监控系统  
**日期**: 2026-04-08  
**监控时段**: 11:20 ~ 18:30+ CST  
**涉及版本**: commit `e5ad28b` (enforce worktree boundary gates) 及后续  
**环境**: macOS Darwin 24.6.0, Python 3.11, asyncio

---

## BUG-001: post_result_timeout 后 Orchestrator 死锁 [CRITICAL]

**复现次数**: 3 次（100% 复现率）  
**影响**: 每次 Claude CLI 任务完成后 orchestrator 卡死，所有 story 停滞

### 现象

Claude CLI 返回结果后，orchestrator 在 30s 内未完成后处理，触发 `claude_post_result_timeout` 警告后 **完全静默**。Worker PID 已退出，但 DB 中 task 状态永久停留在 `running`，cost_usd 为 $0.00。Orchestrator 主进程存活但无子进程、无输出、无轮询。

### 复现步骤

1. 启动 `ato start`，调度任何 Claude CLI developing 任务
2. 等待 Claude CLI 返回结果（通常 5-15 分钟）
3. 观察 `claude_post_result_timeout` 日志（30s 后出现）
4. 此后 orchestrator 不再产生任何输出

### 时间线证据

```
第 1 次: 03:35:17Z post_result_timeout → 65 分钟卡死 → 手动重启
第 2 次: 05:40:16Z post_result_timeout → 手动重启
第 3 次: (同 session 内再次复现)
```

### 根因分析

**文件**: `src/ato/subprocess_mgr.py`

post_result_timeout 在 adapter 的 `execute()` 返回后触发。关键路径：

```python
# subprocess_mgr.py:412-422 — 应在此处将 task 标记为 completed
db = await get_connection(self._db_path)
try:
    await update_task_status(db, task_id, "completed", ...)
```

但在到达此处之前，中间的 `await` 链上某个操作阻塞或异常被吞没，导致：
- `update_task_status` 从未执行（DB 停留在 running）
- `_unregister_running()` 从未调用（内存中保留 dead PID）
- 主循环的 poll 逻辑不会重新检查 dead PID

**可能的死锁点**: `recovery.py:2914` 中 `_submit_transition_event` 调用 `transition_queue.submit_and_wait`（默认 5s 超时），如果 transition queue worker 正忙于处理 worktree_preflight gate（涉及磁盘 I/O + git 命令），5s 超时会抛出 `TimeoutError`，被 generic Exception handler 捕获后仅 log warning，不做状态回滚。

### 建议修复

```python
# 方案 1: post_result_timeout 后强制标记 task 状态
async def _handle_post_result_timeout(self, task_id, result):
    """post_result_timeout 后强制 completed/failed，不依赖后处理链。"""
    db = await get_connection(self._db_path)
    try:
        await update_task_status(db, task_id, "completed",
            cost_usd=result.cost_usd,
            error_message="post_result_timeout: result received but post-processing failed"
        )
    finally:
        await db.close()
    self._unregister_running(task_id)

# 方案 2: poll 循环检测 dead PID 并自动回收
async def _detect_dead_workers(self):
    for pid, task in list(self._running.items()):
        if not _pid_alive(pid):
            logger.warning("dead_worker_detected", pid=pid, task_id=task.task_id)
            await self._handle_dead_worker(task)
```

**优先级**: P0 — 阻塞所有流程

---

## BUG-002: Claude CLI exit_code=1 误报（结果正常但退出码异常）[HIGH]

**复现次数**: 4+ 次  
**影响**: 已完成的开发工作被标记为 failed，触发不必要的 crash_recovery 和重试

### 现象

Claude CLI 成功返回结果（含 cost 和 text_result），但进程退出码为 1。stderr 为空。Adapter 将其分类为 `ErrorCategory.UNKNOWN`，抛出 `CLIAdapterError`。

### 根因分析

**文件**: `src/ato/adapters/claude_cli.py:352-379`

```python
exit_code = proc.returncode or 0
if exit_code != 0:                    # ← 仅看退出码
    ...
    raise CLIAdapterError(...)        # ← 即使有有效结果也抛异常
```

判断成功/失败 **完全依赖退出码**，不考虑是否已经成功解析了结果。Claude CLI 的 `--dangerously-skip-permissions` 模式在某些情况下（如 hooks 触发、session 状态等）返回 exit_code=1 但结果完整。

### 建议修复

```python
# 如果已成功解析 result 且包含有效 text_result，降级为 warning 而非 error
if exit_code != 0:
    if result is not None and result.text_result:
        logger.warning("claude_nonzero_exit_with_result",
            exit_code=exit_code, cost=result.cost_usd)
        # 不抛异常，返回 result
        return result
    else:
        raise CLIAdapterError(...)
```

**优先级**: P1

---

## BUG-003: transition_queue submit_and_wait 超时导致级联故障 [HIGH]

**复现次数**: 2 次  
**影响**: 状态转换丢失，task 标记为 failed

### 现象

`recovery.py:2914` 中提交 `dev_done` 转换事件时，`submit_and_wait` 的 `asyncio.wait_for` 超时（默认 5s），抛出 `TimeoutError`。

### 根因分析

**文件**: `src/ato/transition_queue.py:261-284`

```python
async def submit_and_wait(self, event, *, timeout_seconds: float = 5.0):
    ...
    return await asyncio.wait_for(completion_future, timeout=timeout_seconds)
```

默认 5s 超时过于激进。当 transition queue worker 正在处理 worktree_preflight_check（涉及 `git status --porcelain` + `git diff --stat` 磁盘 I/O），5s 不够完成。

**文件**: `src/ato/recovery.py:2932-2940`

```python
except Exception:  # ← TimeoutError 被吞没
    logger.exception("recovery_dispatch_error", ...)
    await self._mark_dispatch_failed(task)  # ← 本应成功的 task 被标记为 failed
```

### 建议修复

```python
# 1. 增加 submit_and_wait 默认超时
async def submit_and_wait(self, event, *, timeout_seconds: float = 30.0):  # 5s → 30s

# 2. recovery 中区分 TimeoutError 和其他异常
except TimeoutError:
    logger.warning("recovery_transition_timeout", ...)
    # 不标记 failed，等下一个 poll cycle 重试
except Exception:
    logger.exception("recovery_dispatch_error", ...)
    await self._mark_dispatch_failed(task)
```

**优先级**: P1

---

## BUG-004: worktree_finalize exit_code=1 → blocked 死胡同（无审批）[CRITICAL]

**复现次数**: 1 次  
**影响**: Story 进入 blocked 状态，无 pending approval，无法自动恢复

### 现象

1. pre_review gate 发现 UNCOMMITTED_CHANGES → 触发 worktree_finalize
2. finalize 任务 **成功提交代码**（commit 4f62530，+2953 行）
3. 但 Claude CLI 退出码为 1（同 BUG-002）
4. Adapter 抛出 CLIAdapterError
5. Story 变为 `blocked` 状态
6. **没有创建任何 approval** → 死胡同

### 根因分析

**文件**: `src/ato/transition_queue.py` 中 pre_review gate 的 finalize 失败处理

当 finalize dispatch 在 `_dispatch_finalize_for_preflight_failure` 中抛出异常：

```python
except Exception:
    logger.warning("finalize_failed", ...)
    return  # ← 静默返回，不创建 approval
```

随后第二次 preflight_check 仍然失败（因为异常发生在 finalize 之后但返回码检查之前），调用 `_block_pre_merge_for_preflight_failure` 但因为 gate_type 不匹配（pre_review vs pre_merge），跳过了 approval 创建。

### 验证方法

```bash
# 检查 worktree 状态 — 代码已提交，干净
git -C .worktrees/3-8-mermaid-diagram-generation status --porcelain  # 空
git -C .worktrees/3-8-mermaid-diagram-generation log --oneline -1
# 4f62530 3-8-mermaid-diagram-generation: implement Mermaid diagram generation
```

### 建议修复

```python
# 1. finalize 失败时必须创建 preflight_failure approval
except Exception as exc:
    logger.warning("finalize_failed", ...)
    await create_approval(
        db, story_id=story_id,
        approval_type="preflight_failure",
        payload_dict={"gate_type": gate_type, "failure_reason": "FINALIZE_FAILED", ...},
        recommended_action="manual_commit_and_retry",
    )

# 2. 或者在 finalize 后重新检查 worktree 状态
#    如果 worktree 已干净（finalize 实际成功），直接通过 gate
```

**优先级**: P0 — 无恢复路径

---

## BUG-005: preflight_failure 审批在 Blocked 状态下被状态机拒绝 [MEDIUM]

**复现次数**: 1 次  
**影响**: 审批被 consumed 但 retry 无效

### 现象

批准 `preflight_failure` (manual_commit_and_retry) 后，orchestrator 尝试发送 `dev_done` 事件，但 story 已在 Blocked 状态：

```
TransitionNotAllowed: Can't dev_done when in Blocked.
```

审批被标记为 consumed，但 retry 从未执行。

### 根因分析

**文件**: `src/ato/core.py:2883-2930`

```python
if decision == "manual_commit_and_retry":
    if gate_type == "pre_review" and retry_event:
        if self._tq is not None:
            try:
                await self._tq.submit_and_wait(event, ...)
            except StateTransitionError:
                logger.info("preflight_failure_retry_blocked", ...)
                # ← 仅 log info，不做任何恢复
            return True  # ← 返回 True 表示"处理成功"
```

**问题**:
1. 不检查 story 当前状态再提交转换
2. `StateTransitionError` 被静默吞没
3. 返回 `True` 欺骗调用方认为 retry 已成功

### 建议修复

```python
# 1. 提交前检查 story 状态
story = await get_story(db, approval.story_id)
if story.current_phase == "blocked":
    logger.warning("preflight_retry_story_blocked",
        story_id=approval.story_id,
        suggested_action="rollback-story to previous phase first")
    # 创建新的 escalation approval
    await create_approval(db, story_id=approval.story_id,
        approval_type="blocked_recovery", ...)
    return True

# 2. 或者让 Blocked 状态接受 dev_done 转换（状态机层面修复）
```

**优先级**: P2

---

## BUG-006: 单元测试断言未同步更新 [HIGH]

**复现次数**: 持续  
**影响**: 1 个单测失败（1001 passed, 1 failed）

### 现象

```
tests/unit/test_initial_dispatch.py:429
assert dispatched_task.expected_artifact == "initial_dispatch_requested"
实际值: '/private/.../s-create.md'
```

### 根因

`src/ato/task_artifacts.py:14-15` 新增了 `"creating"` 阶段的 artifact path：

```python
if phase == "creating":
    return project_root / ARTIFACTS_REL / f"{story_id}.md"
```

`core.py:1852-1863` 的 walrus 赋值选择了真实路径而非 fallback：

```python
expected_artifact=(
    str(path) if (path := derive_phase_artifact_path(...)) is not None
    else "initial_dispatch_requested"
)
```

测试断言未随代码更新。

### 建议修复

```python
# test_initial_dispatch.py:429
assert dispatched_task.expected_artifact.endswith("s-create.md")
```

**优先级**: P1

---

## BUG-007: BMAD semantic parser 60s 超时（recurring）[MEDIUM]

**复现次数**: 6+ 次  
**影响**: review/QA 结果解析失败，触发 needs_human_review 审批，延迟流程

### 现象

codex 返回 review/QA 结果后，orchestrator 使用 Claude CLI 进行 semantic parsing（结构化数据提取）。Claude CLI 在 60s 内未返回，触发超时：

```
bmad_semantic_fallback_failed: 'Claude CLI timed out after 60s'
bmad_parse_failed: 'Both deterministic and semantic parsing failed'
```

### 根因分析

Semantic parser 使用 `claude -p` 进行 LLM 提取。当输入文本较长（3000-4000 chars）且 Claude 忙碌时，60s 不够完成。

### 建议修复

1. 增加 semantic parser 超时到 120s
2. 优化：对 "Verdict: PASS" / "Recommendation: Approve" 等明确结论做 deterministic 快速路径匹配，跳过 LLM 提取
3. 缓存已成功解析的模式

**优先级**: P2

---

## BUG-008: merge_queue 竞态条件 — approval 创建先于 lock 释放 [MEDIUM]

**复现次数**: 未直接触发，代码审查发现  
**影响**: 理论上可导致 merge queue 状态不一致

### 现象（潜在）

**文件**: `src/ato/merge_queue.py:674-693`

```python
async def _block_pre_merge_for_preflight_failure(self, story_id, result):
    await create_approval(...)                    # 674: approval 已可见
    await complete_merge(db, story_id, False)     # 690
    await set_current_merge_story(db, None)       # 693: lock 此时才释放
```

如果 orchestrator 快速轮询在 674~693 之间发现 approval 并处理，在 lock 释放前重新入队。

### 建议修复

```python
# 先释放 lock，再创建 approval
await set_current_merge_story(db, None)
await complete_merge(db, story_id, success=False)
await create_approval(...)
```

**优先级**: P2

---

## BUG-009: `_dirty_files_from_porcelain` 代码重复 [LOW]

**文件**: `src/ato/transition_queue.py:111` 和 `src/ato/merge_queue.py:243`

同一函数在两个模块中重复定义。

### 建议修复

提取到 `src/ato/worktree_utils.py`，两处 import。

**优先级**: P3

---

## BUG-010: `_run_pre_merge_gate` 变量跨 try/finally 作用域 [LOW]

**文件**: `src/ato/merge_queue.py:590-615`

```python
try:
    ...
    second_result = await ...   # 603
    ...
finally:
    await db.close()            # 612

await ...(story_id, second_result)  # 614: 跨 finally 使用
```

经验证不会产生 UnboundLocalError（所有到达 614 的路径必经 603），但属于 bad practice。

### 建议修复

```python
second_result = None  # try 之前初始化
try:
    ...
finally:
    await db.close()
if second_result is not None:
    await ...(story_id, second_result)
```

**优先级**: P3

---

## 统计摘要

| 优先级 | 数量 | Bug 编号 |
|--------|------|----------|
| P0 (CRITICAL) | 2 | BUG-001, BUG-004 |
| P1 (HIGH) | 3 | BUG-002, BUG-003, BUG-006 |
| P2 (MEDIUM) | 3 | BUG-005, BUG-007, BUG-008 |
| P3 (LOW) | 2 | BUG-009, BUG-010 |

### 修复优先顺序建议

1. **BUG-001** (P0): post_result_timeout 死锁 — 根本原因，阻塞所有流程
2. **BUG-004** (P0): finalize blocked 死胡同 — 无恢复路径
3. **BUG-002** (P1): exit_code=1 误报 — 修复后可消除 BUG-004 的触发条件
4. **BUG-003** (P1): transition_queue 超时 — 增加默认超时即可
5. **BUG-006** (P1): 单测断言 — 简单修复
6. 其余 P2/P3 按迭代修复

### 根因关系图

```
BUG-002 (exit_code=1 误报)
  ├→ BUG-004 (finalize "失败" → blocked 死胡同)
  └→ crash_recovery 审批泛滥

BUG-001 (post_result_timeout 死锁)
  └→ 手动重启 → recovery 全新调度 → 重复开发成本
      └→ BUG-003 (transition_queue 超时 → task 误标 failed)
          └→ BUG-005 (preflight retry 在 Blocked 状态被拒)
```

**修复 BUG-001 + BUG-002 可消除 ~80% 的级联问题。**
