# Bug: structured_job 超时配置未传递给 CLI Adapter，导致已完成任务被重复执行

**发现时间:** 2026-04-02 09:42 JST
**严重性:** High
**影响范围:** 所有执行时间超过 30 分钟的 structured_job（fixing / reviewing / developing 等阶段）

## 现象

Story `3-4-ai-chapter-generation` 的 fixing agent 在 28 分钟内成功完成工作（833 个测试通过、lint clean、build 成功、committed `56d57f8`、cost=$5.52），但 orchestrator 仍然将其判定为超时并触发 retry，导致同一修复工作被重复执行。

### 关键日志

```
00:11:49 [info ] dispatch_started    task=e9c2cd12  phase=fixing
00:39:54 [info ] 完成 (cost=$5.5237)  task=e9c2cd12  progress_type=result
00:41:54 [warn ] dispatch_retry      task=e9c2cd12  attempt=1 category=timeout  ← 距首次 dispatch 1804.8s ≈ 1800s + cleanup
00:41:54 [info ] dispatch_started    task=e9c2cd12  phase=fixing  ← 重复执行！
```

注意：`e9c2cd12` 没有 `claude_adapter_success` 日志。尽管 agent 通过 progress callback 报告了 `result` 事件，adapter 从未返回成功。

## 根因

### Bug 1（主要）：`options["timeout"]` 未设置

`core.py` 的 `_dispatch_batch_restart()` 和 `recovery.py` 的 `_build_dispatch_options()` 都未将 `settings.timeout.structured_job` 传递给 adapter options：

**core.py:1920-1938** (`_dispatch_batch_restart`):
```python
options: dict[str, object] = {}
# ... cwd, model, sandbox, effort 等都正确设置
options["idle_timeout"] = self._settings.timeout.idle_timeout      # ✅ 已设置
options["post_result_timeout"] = self._settings.timeout.post_result_timeout  # ✅ 已设置
# options["timeout"] = ???  ← ❌ 缺失！
```

**recovery.py:807-863** (`_build_dispatch_options`):
```python
timeout = phase_cfg.get("timeout_seconds")
if timeout and task.cli_tool == "claude":
    opts["max_turns"] = max(1, timeout // 60)  # 仅用于 max_turns 计算
# opts["timeout"] = ???  ← ❌ 同样缺失！
```

**claude_cli.py:274** (`execute`):
```python
timeout_seconds: int = (options or {}).get("timeout", 1800)  # 硬编码默认值 1800s = 30 分钟
```

用户在 `ato.yaml` 中配置了 `structured_job: 7200`（2 小时），但 adapter 永远使用 1800s 默认值。

### Bug 2（次要/设计缺陷）：result 被丢弃

`_consume_stream()` 接收到 `result` 事件后仍继续读取 stdout 直到 EOF：
```python
if event.get("type") == "result":
    result_data = event  # ✅ 保存了 result
# 但循环继续... ← 如果此后 idle_timeout 或 outer timeout 触发，result_data 随异常丢失
```

当 outer timeout（1800s）在 `_consume_stream` 仍在读取时触发，`TimeoutError` 使得已捕获的 `result_data` 永远不会返回给调用方。

## 触发时间线

```
T+0s     (00:11:49)  dispatch_started → asyncio.timeout(1800) 开始计时
T+1685s  (00:39:54)  _consume_stream 收到 result 事件（通过 progress callback 报告）
                      _consume_stream 继续读 stdout 等待 EOF...
T+1800s  (00:41:49)  asyncio.timeout(1800) 到期 → 取消 _consume_stream
T+1805s  (00:41:54)  CLIAdapterError(TIMEOUT, retryable=True) 抛出
                      dispatch_with_retry 捕获 → dispatch_retry attempt=1
                      全新 Claude CLI 进程启动，重复执行相同修复工作
```

## 影响

1. **资源浪费**：已完成的 $5.52 工作被丢弃并重新执行
2. **重复 commit**：retry agent 可能在 worktree 中产生重复或冲突的 commit
3. **隐性问题**：任何执行时间在 28-30 分钟的 agent 都可能触发此 bug，但由于大多数任务 <30 分钟完成，问题间歇性出现
4. **级联失败**：如果 retry 也 >30 分钟，会抛出不可重试的异常（max_retries=1），升级为 dispatch_failed

## 修复建议

### Fix 1：传递 structured_job timeout（必须）

**core.py:~1938** (`_dispatch_batch_restart`):
```python
options["idle_timeout"] = self._settings.timeout.idle_timeout
options["post_result_timeout"] = self._settings.timeout.post_result_timeout
options["timeout"] = self._settings.timeout.structured_job  # ← 添加此行
```

**recovery.py:~861** (`_build_dispatch_options`):
```python
# 在 return 前添加：
if self._settings:
    opts["idle_timeout"] = self._settings.timeout.idle_timeout
    opts["post_result_timeout"] = self._settings.timeout.post_result_timeout
    opts["timeout"] = self._settings.timeout.structured_job
```

### Fix 2：收到 result 后缩短 stream timeout（建议）

**claude_cli.py** `_consume_stream`:
```python
if event.get("type") == "result":
    result_data = event
    idle_timeout = min(idle_timeout, 30)  # result 之后最多再等 30s
```

### Fix 3：非零退出但有 result 时降级为 warning（建议）

**claude_cli.py** `execute` line 347-374:
```python
exit_code = proc.returncode or 0
if exit_code != 0:
    if result_data is not None:
        logger.warning("claude_nonzero_exit_with_result", exit_code=exit_code)
        # 有 result → 视为成功，不抛异常
    else:
        raise CLIAdapterError(...)
```

## 验证方法

1. 设置 `structured_job: 7200` 后，确认 adapter 日志中的 timeout 为 7200 而非 1800
2. 运行一个 >30 分钟的 fixing task，确认不再触发 dispatch_retry
3. 单元测试：mock adapter 并验证 options["timeout"] 正确传递
