# Bug: blocking_abnormal 审批 approve 后触发 escalate → blocked，而非恢复 convergent loop

**发现时间:** 2026-04-02 12:21 JST
**严重性:** High
**影响范围:** 所有 convergent_loop 阶段（reviewing, qa_testing）的 blocking_abnormal 审批处理
**复现次数:** 2 次（reviewing round 4, qa_testing round 1）

## 现象

当 convergent loop 发现 blocking findings 数量超过 `blocking_threshold`（10）时，系统正确创建 `blocking_abnormal` 审批。但当操作者 approve 该审批后：

1. Orchestrator 消费审批，触发 `escalate` transition
2. Story 状态从 `fixing` → `blocked|blocked`
3. Convergent loop 完全停止
4. 必须手动修改 DB 恢复 `fixing|review` + 创建 paused task + 重启 orchestrator

### 关键日志

```
03:15:51 [info] state_exited  source=fixing target=blocked event_name=escalate
03:15:51 [info] approval_consumed  approval_type=blocking_abnormal decision=human_review
03:15:51 [info] story_state_saved  phase=blocked status=blocked
```

## 预期行为

approve `blocking_abnormal` 后应该：
1. 恢复 story 到 approve 前的 phase（fixing 或 qa_testing）
2. 继续 convergent loop 的 fix → re-review 循环
3. 不触发 `escalate` transition

## 根因分析

审批消费逻辑（`core.py` 中的 approval handler）将 `blocking_abnormal` 的 approve 映射到了 `escalate` transition event，而状态机中 `escalate` 的目标是 `blocked` 状态。

正确的逻辑应该是：
- `blocking_abnormal` + `approve` → **不提交任何 transition**，仅标记审批为已处理，让 convergent loop 继续执行
- `blocking_abnormal` + `reject` → 可以触发 `escalate` → `blocked`（操作者认为问题太多，需要人工介入）

## 临时解决方法

每次遇到时手动执行：
```sql
UPDATE stories SET current_phase='fixing', status='review', updated_at=datetime('now')
  WHERE story_id='xxx';
INSERT INTO tasks (...) VALUES (..., 'fixing', ..., 'paused', ...);
-- 然后重启 orchestrator
```

## 修复建议

在 `core.py` 的 approval 消费逻辑中：

```python
if approval.approval_type == "blocking_abnormal":
    if approval.decision in ("human_review", "approve"):
        # 仅消费审批，不提交 transition，让 convergent loop 自行恢复
        logger.info("blocking_abnormal_acknowledged", ...)
        return
    else:
        # reject → 真正 escalate
        await tq.submit(TransitionEvent(event_name="escalate", ...))
```
