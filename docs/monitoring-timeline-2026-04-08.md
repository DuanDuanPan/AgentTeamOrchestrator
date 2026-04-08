# ATO 执行完整时间线 — 2026-04-08

## 概览

| 项目 | 数据 |
|------|------|
| 监控时段 | 11:20 ~ 18:30+ CST（7h+） |
| 监控 Story | 3-8-mermaid-diagram-generation, 8-1-enabler-python-docx-engine |
| 最终状态 | 3-8: merging, 8-1: regression |
| 累计成本 | $225.90 |
| 总 Task 数 | 322 |
| 总审批数 | 65（其中监控期间手动处理 ~20+） |
| 发现 Bug | 11 个（2 CRITICAL, 3 HIGH, 4 MEDIUM, 2 LOW） |
| 手动干预 | 5 次 orchestrator 重启, 2 次手动 git commit, 2 次 rollback-story |

---

## 完整时间线

### Phase 1: 启动与首次故障 (11:20 ~ 12:34)

| 时间 (CST) | 事件 | 详情 |
|------------|------|------|
| 11:20 | 监控启动 | 设置 tmux pipe-pane 流式管道，创建 cron 每 10min 轮询 |
| 11:22 | `ato start` 启动 | PID 9017，两个 story (3-8, 8-1) 开始 developing |
| 11:23 | 两个 Claude worker 启动 | 3-8 task 14d26d3b, 8-1 task c451b226 并行开发 |
| 11:27 | **首次检查** | 发现单测失败 (#1)、worktree boundary gates 大提交 (#6) |
| 11:34 | 8-1 Claude 完成 | cost=$1.73，返回结果 |
| 11:35 | **8-1 post_result_timeout** | 30s 后处理超时 (#2) |
| 11:35 | 3-8 Claude 完成 | cost=$2.18 |
| 11:36 | **3-8 post_result_timeout** | 30s 后处理超时 |
| 11:36+ | **orchestrator 完全静默** | CLI 无任何输出，持续 65 分钟 (#7 CRITICAL) |
| 11:55 | 第三次检查发现卡死 | PID 9022/9023 已死，DB 仍显示 running |
| 12:05~12:30 | 连续 4 次确认卡死 | 无自愈迹象 |
| 12:34 | **手动重启 orchestrator** | Ctrl-C + ato start，recovery 5.8ms |

### Phase 2: 重启循环与 exit_code=1 (12:34 ~ 13:40)

| 时间 | 事件 | 详情 |
|------|------|------|
| 12:34 | Recovery 完成 | 2 个 task 重新调度（全新，非 resume） |
| 12:39 | **两个 task 完成但 exit_code=1** | cost=$1.87/$1.85，触发 crash_recovery (#8) |
| 12:39 | 2 个 crash_recovery 审批 | options: restart/resume/abandon |
| 12:46 | **批准两个 restart** | 第三次重新调度 |
| 12:48~13:03 | 开发进行中 | Agent 识别已有代码，从断点续作 |
| 13:03 | 3-8 完成 fix round | 37 tests pass, lint/build 通过 |
| 13:13 | 8-1 完成验证 | 1368 unit + 59 E2E 通过 |
| 13:33 | 两个 story 进入收尾 | 更新 story 文件 |
| 13:40 | **3-8 成功进入 reviewing** | cost=$1.17 |
| 13:40 | **8-1 再次 post_result_timeout** | 第 3 次复现 (#7) |

### Phase 3: Worktree Boundary Gate 问题爆发 (13:40 ~ 14:05)

| 时间 | 事件 | 详情 |
|------|------|------|
| 13:45 | 手动重启 orchestrator | Recovery 3ms |
| 13:53 | **transition_queue TimeoutError** | 新 bug #9，submit_and_wait 超时 |
| 13:53 | **worktree_finalize exit_code=1** | 3-8 进入 blocked 死胡同 (#10) |
| 13:53 | 8-1 preflight_failure | UNCOMMITTED_CHANGES |
| 13:54 | 处理 8-1 审批 | abandon stale + approve preflight_failure |
| 13:56 | **手动 rollback 3-8** | blocked → reviewing |
| 13:56 | 3-8 进入 reviewing | codex code-review round 1 启动 |
| 14:05 | **8-1 状态机拒绝转换** | TransitionNotAllowed: dev_done in Blocked (#11) |
| 14:05 | **手动 git commit 8-1** | 修复 prettier → commit |
| 14:05 | **手动 rollback 8-1** | blocked → reviewing |
| 14:06 | 8-1 进入 reviewing | codex code-review 启动 |

### Phase 4: Convergent Loop — Review/Fix 循环 (14:06 ~ 16:10)

| 时间 | 事件 | 详情 |
|------|------|------|
| 14:12 | 8-1 review R1 完成 | 3 blocking: Python interpreter, SIGTERM, validation errors |
| 14:12 | 8-1 → fixing | Claude systematic-debugging |
| 14:22 | 8-1 fix 完成 | commit 45febea, 10 tests pass |
| 14:22 | 8-1 review R3 | pre_review gate passed, scoped re-review |
| 14:24 | 3-8 needs_human_review | BMAD parser timeout (#12 recurring) |
| 14:24 | **批准 3-8 retry** | |
| 14:30 | 3-8 fixing R2 完成 | 37 tests pass, spec 更新 |
| 14:42 | 3-8 review R3 | 4 blocking 待验证 |
| 14:44 | **批准 8-1 needs_human_review** | |
| 14:48 | 3-8 BMAD parse 再次超时 | **批准 retry** |
| 14:54 | 3-8 review R4 | 2 blocking remaining |
| 15:00 | 8-1 review R3 完成 | review_fail → fixing |
| 15:02 | 3-8 fixing R3 | E2E + default template 修复 |
| 15:04 | **批准 3-8 retry** | |
| 15:13 | **批准 3-8 + 8-1 retry** | 两个同时 |

### Phase 5: QA Testing (16:10 ~ 17:37)

| 时间 | 事件 | 详情 |
|------|------|------|
| 16:06 | 3-8 review **PASS** | convergence_rate=1.0, 0 blocking |
| 16:06 | 3-8 → qa_testing | |
| 16:10 | 8-1 → qa_testing | review 收敛成功 |
| 16:12 | 8-1 QA R1 | Approve 92/100, 4 P3 suggestions |
| 16:16 | 3-8 QA codex 超时 | 300s idle → 1800s timeout |
| 16:22 | 8-1 QA R2 | Approve 93/100 |
| 16:26 | 3-8 crash_recovery | codex timeout, **approve restart** |
| 16:33 | 3-8 QA 重启 | 第三次尝试 |
| 16:38 | 3-8 unit test 失败 | delete flow mock 过时 |
| 16:41 | 8-1 review_pass → **qa_testing converged** | |
| 16:41 | 8-1 → **UAT** 🎉 | QA 全通过 |
| 16:42 | 3-8 QA fixing | 修复 delete mock |
| 16:47 | 3-8 QA 完成 | 68 E2E + cold-start 全通过 |
| 16:52 | 3-8 StateTransitionError | qa_fail in fixing state (#9 变体) |
| 16:59 | 3-8 QA restart + 再次全通过 | |
| 17:02 | 3-8 fixing (asar) | 修复 packaging hardening |
| 17:25 | 3-8 QA 最终通过 | Approve, 0 blocking |
| 17:37 | 3-8 → **UAT** 🎉 | QA converged |

### Phase 6: UAT (17:37 ~ 18:30+)

| 时间 | 事件 | 详情 |
|------|------|------|
| 17:37 | 两个 story 进入 UAT | Interactive session 启动 |
| 17:44 | 8-1 session_timeout | **approve restart** |
| 17:54 | 8-1 session_timeout | **approve restart** (superseded) |
| 17:37~18:30+ | UAT 空闲 | 等待用户手动验收 |

### Phase 7: UAT 完成 + 后续 (用户操作)

| 状态 | Story |
|------|-------|
| **merging** | 3-8-mermaid-diagram-generation |
| **regression** | 8-1-enabler-python-docx-engine |

用户在某个时间点完成了 UAT 验收，两个 story 继续推进到 merging/regression 阶段。

---

## 发现的 Bug 汇总

### CRITICAL (2)

| # | 描述 | 复现次数 | 影响 |
|---|------|---------|------|
| 7 | **post_result_timeout → orchestrator stuck** | 3+ 次 | 每次 Claude 任务完成后 orchestrator 卡死，需手动重启 |
| 10 | **worktree_finalize exit_code=1 → blocked 死胡同** | 1 次 | finalize 成功提交代码但 exit_code=1 导致无 approval 的 blocked 状态 |

### HIGH (3)

| # | 描述 | 文件 |
|---|------|------|
| 1 | 单测断言未同步更新 | `test_initial_dispatch.py:429` |
| 8 | Claude CLI exit_code=1（结果正常但退出码异常） | `claude_cli.py:373` |
| 9 | transition_queue TimeoutError | `transition_queue.py:284` |

### MEDIUM (4)

| # | 描述 | 文件 |
|---|------|------|
| 2 | claude_post_result_timeout 警告 | `subprocess_mgr.py` |
| 4 | merge_queue 竞态：approval 创建先于 lock 释放 | `merge_queue.py:674-693` |
| 11 | preflight_failure retry 在 Blocked 状态被拒绝 | `core.py` (approval handler) |
| — | BMAD semantic parser 60s 超时 (recurring) | `bmad_adapter.py` |

### LOW (2)

| # | 描述 |
|---|------|
| 3 | `second_result` 跨 try/finally 作用域 |
| 5 | `_dirty_files_from_porcelain` 重复定义 |

---

## 手动干预记录

| 次数 | 类型 | 原因 |
|------|------|------|
| 3 | orchestrator 重启 | post_result_timeout 卡死 (#7) |
| 2 | rollback-story | blocked 死胡同 (#10, #11) |
| 1 | 手动 git commit | worktree 未提交 + prettier fix |
| ~20 | 审批处理 | crash_recovery, needs_human_review, preflight_failure, session_timeout |

---

## 核心问题根因链

```
Claude CLI 任务完成
  └→ post_result_timeout (30s)          ← #7 CRITICAL
      └→ orchestrator stuck (死循环)
          └→ 手动重启
              └→ recovery 全新调度（非 resume）
                  └→ exit_code=1          ← #8
                      └→ crash_recovery 审批
                          └→ 重新调度
                              └→ worktree 有未提交代码
                                  └→ pre_review gate UNCOMMITTED_CHANGES
                                      └→ worktree_finalize
                                          └→ exit_code=1   ← #10
                                              └→ blocked 死胡同（无审批）
                                                  └→ 手动 rollback
```

**根本原因**: `subprocess_mgr.py` 在 Claude CLI 返回结果后的后处理路径中存在 await 死锁或异常吞没，导致 task 状态永远无法从 running 转为 completed。这个 bug 触发了整个级联故障链。

---

## 正面成果

尽管遇到多次故障，系统最终成功完成了两个 story 的完整流水线：

```
developing → reviewing → fixing → qa_testing → UAT → merging/regression
```

- **3-8**: 经历 6 轮 review convergent loop（最终收敛率 100%），修复了 AntD Modal.confirm 兼容性、caption 持久化、E2E 断言等深层问题
- **8-1**: QA 评分 92-93/100，1369 单测 + 59 E2E + 21 Python 测试全通过
- Convergent loop 质量门控有效发现并修复了 7+ 个 blocking issues
- Worktree boundary gate 正确拦截了未提交的代码
