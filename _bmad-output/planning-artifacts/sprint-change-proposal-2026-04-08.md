# Sprint Change Proposal — 终态收敛与恢复可靠性补强

**日期:** 2026-04-08  
**提案人:** Enjoyjavapan163.com  
**变更范围:** Moderate — 需要补充 corrective stories，并由 Dev/QA 分阶段实现  
**状态:** 已批准 (2026-04-08)  
**触发来源:** 2026-04-08 ATO 监控事故与 RCA 复核

---

## 1. 问题摘要

### 触发事件

2026-04-08 监控 `3-8-mermaid-diagram-generation` 与
`8-1-enabler-python-docx-engine` 的实际流水线时，系统多次出现：

- Claude CLI 已返回 result，但 Orchestrator 在 post-result 阶段静默卡住；
- worker PID 已退出，SQLite `tasks.status` 仍为 `running`，`cost_usd=0.00`；
- 重启后 recovery 重调度，进一步触发 `exit_code=1` 误判、crash recovery 审批、preflight
  dead-end、transition timeout、BMAD parse timeout；
- 用户需要 5 次 Orchestrator 重启、2 次 rollback-story、2 次手动 git commit 和约 20+ 次审批处理。

证据来源：

- `docs/bug-report-2026-04-08.md`
- `docs/monitoring-log-2026-04-08.md`
- `docs/monitoring-timeline-2026-04-08.md`
- `docs/root-cause-analysis-2026-04-08.md`

### 核心问题

事故不是单一 timeout，而是 **CLI 已返回结果之后，ATO 缺少不可卡死、可恢复、可观测的终态收敛边界**。

当前实现违反了多个已定义目标：

- PRD FR24/FR27/FR28 要求 task 与 cost 结构化持久化，但 post-result 后处理挂起会让 task 永久
  `running` 且 cost 丢失。
- PRD FR25/NFR7 要求恢复所有可恢复 task，但 dead PID + running task 在运行期没有 watchdog 收敛。
- PRD NFR14 要求正确处理 CLI 退出码和错误输出，但 Claude `type: result` 已存在时仍被
  `exit_code=1` 判为失败。
- PRD FR19/FR20/FR50 要求审批可恢复，但 blocked 状态下的 `preflight_failure` retry 会被状态机拒绝，
  approval 仍被消费。
- PRD NFR12 要求 BMAD parser 对输出格式漂移具备鲁棒性，但 semantic fallback 固定 60s 且 deterministic
  fast-path 覆盖不足。

### 问题分类

失败路径补强和恢复语义修正。不是 MVP 范围变化，也不需要回滚近期功能；需要在既有 Epic 2B、Epic 4、Epic 5
和 worktree boundary gate 的基础上新增 corrective stories。

---

## 2. 影响分析

### Epic 影响

| Epic | 影响 | 需要调整 |
| --- | --- | --- |
| Epic 2B: Agent 集成与工作空间 | 已交付代码存在 P0 缺陷 | 修正 `ClaudeAdapter` result/exit-code 语义；补 terminal finalizer 与 cost/task 落库测试 |
| Epic 3: Convergent Loop | 间接受影响 | 不改收敛算法；需确保 review/fix 任务完成后的状态转换不会被终态收敛问题阻断 |
| Epic 4: 人机协作与审批队列 | 已交付恢复路径存在缺口 | 修正 `preflight_failure` approval 在 blocked/timeout 下的消费语义；修正 merge queue lock/approval 顺序 |
| Epic 5: 崩溃恢复与可观测性 | 已交付恢复模型不完整 | 增加运行期 dead PID watchdog 与 post-result finalizer，可减少必须靠重启触发 recovery 的情况 |
| Epic 9: 工作流阶段重构与 Workspace 分离 | 间接受影响 | 维持现有 worktree boundary gate；补强 finalize 失败后的 clean-or-approval 不变量 |
| Epic 7: Growth | 无需提前 | 不需要把可靠性修复推到 Growth，属于 MVP 可运行性缺陷 |

### Story 影响

| Story | 当前状态 | 影响 |
| --- | --- | --- |
| 2B.1 `claude-agent-dispatch` | done | 需要 corrective story 修正 result 优先语义、terminal finalizer、cost/task 落库兜底 |
| 2B.3 `bmad-skill-parsing` | sprint-status=done，story 文件=review | 需要 corrective story 补 deterministic fast-path 与 semantic timeout 配置化 |
| 3.3 `convergence-trust-escalation` | sprint-status=done，story 文件=review | 不直接修改，但回归测试需覆盖 review/fix 完成后的 transition ack 语义 |
| 4.1 `approval-queue-nudge` | done | 需要 corrective story 修正 approval 消费失败时不能静默标记 consumed |
| 4.2 `merge-queue-regression-safety` | sprint-status=done，story 文件=review | 需要 corrective story 修正 pre-merge approval/lock 顺序与 `second_result` 防御 |
| 5.1a `crash-recovery-auto-resume` | sprint-status=done，story 文件=review | 需要 corrective story 增加运行期 watchdog，而不是只在重启时恢复 |
| `tech-spec-worktree-boundary-gates` | tech spec | 需要补充 finalize 异常时仍要重新 preflight 或创建 approval 的不变量 |

### Artifact 冲突

| Artifact | 冲突 | 建议 |
| --- | --- | --- |
| PRD | 不冲突，但 NFR2/NFR7/NFR14 需要澄清实现语义 | 增加“terminal convergence boundary”与“result 优先”说明 |
| Epics | 需要新增 corrective stories | 推荐新增 Epic 10：Runtime Reliability Hardening，或将 stories 分散挂回 Epic 2B/4/5 |
| Architecture | 需要新增架构决策 | 增加 Decision 11：CLI Terminal Convergence Boundary |
| UX Design | 轻微影响 | 异常审批摘要需能表达“等待确认/恢复入口”，不要制造 consumed-but-no-action |
| Implementation artifacts | 需要补充故事和测试计划 | 用 `bmad-create-story` 逐个生成 story，再用 `bmad-dev-story` 实现 |

### 技术影响

| Area | 影响 |
| --- | --- |
| `src/ato/subprocess_mgr.py` | 增加 terminal finalizer、总超时、fallback 落库、finally 注销、dead PID watchdog |
| `src/ato/adapters/claude_cli.py` | result 事件优先；非零 process exit code 降级为 warning/metadata |
| `src/ato/models/schemas.py` | 可能增加 `process_exit_code` / `warnings` 字段，或短期规范化返回 exit_code |
| `src/ato/transition_queue.py` | 修正 `submit_and_wait` ack timeout 语义；pre-review finalize 异常必须 clean-or-approval |
| `src/ato/core.py` | approval 决策处理要状态感知；处理失败不能静默 consumed |
| `src/ato/merge_queue.py` | pre-merge gate 先释放内部 lock 再暴露 approval；修正低风险作用域问题 |
| `src/ato/adapters/bmad_adapter.py` / `semantic_parser.py` | deterministic fast-path 补强；semantic timeout 配置化 |
| tests | 需要 P0/P1 回归测试优先，再补 P2/P3 单元测试 |

---

## 3. 推荐方案

### 选择: Direct Adjustment，新增 corrective reliability stories

不建议回滚 `e5ad28b` 或最近的 worktree boundary gate，因为 gate 本身发现了真实的未提交代码风险；问题在 gate 与终态恢复边界没有做强。

不建议只增大 timeout，因为这无法解决 dead PID、running task 永久残留、approval 被误消费等根因。

推荐新增一个纠偏 mini-epic：

**Epic 10: Runtime Reliability Hardening**

实现顺序：

1. **Story 10.1 — Terminal Finalizer 与 Dead PID Watchdog**
   - 覆盖 BUG-001 主根因。
   - 先保证 CLI result 之后 task/cost/running/semaphore 必然收敛。
2. **Story 10.2 — Claude Result-First Semantics**
   - 覆盖 BUG-002。
   - 把有效 result 与 process exit warning 解耦。
3. **Story 10.3 — Transition/Preflight Recovery Semantics**
   - 覆盖 BUG-003/004/005。
   - `submit_and_wait` timeout 不等于业务失败；finalize 必须 clean-or-approval；blocked retry 不静默消费 approval。
4. **Story 10.4 — BMAD Parser Reliability**
   - 覆盖 BUG-007。
   - semantic timeout 配置化，常见 PASS/Approve 输出 deterministic 解析。
5. **Story 10.5 — Merge Queue Boundary Hygiene**
   - 覆盖 BUG-008/009/010。
   - release-lock-before-approval、共享 porcelain parser、防御性初始化 `second_result`。
6. **Story 10.6 — Incident Regression Suite**
   - 汇总关键事故链为 integration regression tests。
   - 可与 10.1-10.5 分散实现；若时间紧，至少把 P0/P1 场景并入前 3 个 story。

### 工作量与风险

| 项 | 评估 |
| --- | --- |
| 工作量 | Medium |
| 技术风险 | Medium-High，涉及 asyncio、SQLite、subprocess、state machine 与 approval |
| 时间线影响 | 建议暂停新的 story 批量推进，先完成 10.1-10.3 |
| MVP 范围影响 | 无需缩减 MVP；这是已有 MVP 可靠性目标的补强 |

---

## 4. 详细变更提案

### 4.1 PRD 修改提案

#### NFR7 — 恢复语义补充

**OLD:**

```markdown
**NFR7:** 系统重启后可自动恢复所有可恢复的 task（有 artifact 或 PID 存活的），无需人工重建状态
```

**NEW:**

```markdown
**NFR7:** 系统重启后可自动恢复所有可恢复的 task（有 artifact 或 PID 存活的），无需人工重建状态。
系统运行期间也必须检测 dead PID + running task，并将其收敛到 completed、failed、rescheduled 或
needs_human_review，避免必须依赖人工重启触发恢复。
```

**理由:** BUG-001 证明只在 `ato start` 做 recovery 不够，运行期也需要 watchdog。

#### NFR14 — CLI result 与 process exit code 语义补充

**OLD:**

```markdown
**NFR14:** 系统正确处理 CLI 的各类退出码和错误输出（认证过期、rate limit、超时等），分类到对应的恢复策略
```

**NEW:**

```markdown
**NFR14:** 系统正确处理 CLI 的各类退出码和错误输出（认证过期、rate limit、超时等），分类到对应的恢复策略。
对支持结构化 result 事件的 CLI，已收到有效业务 result 时，以 result 为业务完成信号；非零 process exit code
应记录为 warning/metadata，除非没有可用 result 或 result 解析失败。
```

**理由:** BUG-002 的根因是 adapter 把 process exit code 当成业务成功/失败的唯一来源。

#### NFR2 — Transition ack 语义补充

**OLD:**

```markdown
**NFR2:** 状态转换处理（从 agent 完成到下一阶段 agent 启动）≤5 秒
```

**NEW:**

```markdown
**NFR2:** 状态转换处理（从 agent 完成到下一阶段 agent 启动）目标 ≤5 秒；当 worktree preflight、
finalize 或 git I/O 导致队列确认超过等待阈值时，调用方不得把 ack timeout 直接视为业务失败，必须保留
可查询、可重试、可恢复的 transition 状态。
```

**理由:** BUG-003 证明等待提交确认 timeout 与业务失败需要分离。

### 4.2 Architecture 修改提案

#### 新增 Decision 11: CLI Terminal Convergence Boundary

**NEW:**

```markdown
### Decision 11: CLI Terminal Convergence Boundary

每次 CLI adapter 返回 result 或抛出错误后，SubprocessManager 必须进入 bounded terminal finalizer：

1. terminal finalizer 有总超时，覆盖 activity flush、task 终态落库、cost_log 落库；
2. activity flush 不得阻塞 task completed/failed 的落库；
3. 正常 DB helper 失败时，执行最小 raw SQL fallback，尽力把 task 从 running 收敛为终态；
4. `_unregister_running(task_id)` 必须位于 outer finally，不能依赖 DB 写入成功；
5. dead PID watchdog 定期检查 `_running` 中 PID 已退出但 DB 仍 running 的 task，并按 recovery 规则收敛；
6. adapter 应区分业务 result status 与 process exit code；`type: result` 存在时非零 process exit code
   记录为 warning，不直接触发 CLIAdapterError；
7. TransitionQueue ack timeout 不代表 transition 失败；调用方必须能确认队列事件最终结果或创建恢复入口；
8. 用户可见 approval 创建前，内部 lock/queue 状态必须已进入可操作状态。
```

**理由:** 现有 Decision 7 覆盖崩溃后恢复，但没有定义“CLI 完成到系统状态收敛”这一关键事务边界。

### 4.3 Epics 修改提案

#### 新增 Epic 10: Runtime Reliability Hardening

**NEW:**

```markdown
## Epic 10: Runtime Reliability Hardening

**目标:** 修复 2026-04-08 监控事故暴露的终态收敛、adapter result 语义、transition ack、approval 恢复、
BMAD parser fallback 与 merge queue 边界问题，使 ATO 在 CLI 已返回 result、worker PID 已退出、
worktree gate 失败或 parser fallback 超时时都能进入可恢复、可观测的状态。

**覆盖需求:** FR19, FR20, FR24, FR25, FR27, FR28, FR31, FR50, FR52, NFR1, NFR2, NFR7, NFR8,
NFR11, NFR12, NFR14

**Stories:**
- 10.1 Terminal Finalizer 与 Dead PID Watchdog
- 10.2 Claude Result-First Semantics
- 10.3 Transition/Preflight Recovery Semantics
- 10.4 BMAD Parser Reliability
- 10.5 Merge Queue Boundary Hygiene
- 10.6 Incident Regression Suite
```

**理由:** 问题跨越 2B/4/5/9，集中成 corrective epic 更容易排序、验收和回归。

### 4.4 Story 级修改提案

#### Story 10.1 — Terminal Finalizer 与 Dead PID Watchdog

**Acceptance Criteria:**

- Given adapter 已返回 `AdapterResult`，when activity flush 或 DB helper 卡住/超时，then dispatch 在终态总超时内退出，task 最终为 `completed` 或 `failed`，`_running` 注销。
- Given cost_log 正常写入失败，when result 含 cost/token，then fallback 至少保证 task status/cost/error_message 可见。
- Given worker PID 已死但 DB task 仍 `running`，when watchdog poll 运行，then task 被分类恢复或创建 needs_human/recovery 入口。
- Given finalizer fallback 执行，then structlog 包含 `terminal_finalizer_timeout`、`fallback_used`、`task_id`、`story_id`、`phase`。

**主要文件:**

- `src/ato/subprocess_mgr.py`
- `src/ato/models/db.py`
- `tests/unit/test_subprocess_mgr.py`
- `tests/integration/test_crash_recovery.py`

**验证命令:**

```bash
uv run pytest tests/unit/test_subprocess_mgr.py tests/integration/test_crash_recovery.py -v
```

#### Story 10.2 — Claude Result-First Semantics

**Acceptance Criteria:**

- Given Claude stream-json 已收到 `type: result` 且 process exit code 为 1，when adapter 完成，then 返回业务成功结果，不抛 `CLIAdapterError`。
- Given 无 result 且 exit code 非 0，then 仍按现有错误分类抛 `CLIAdapterError`。
- Given result 存在但 stderr 非空，then stderr/process exit code 记录为 warning metadata，不覆盖业务 result。
- Given schema 尚未支持 `process_exit_code`，then 短期返回对象必须避免被标为 `status="failure"`。

**主要文件:**

- `src/ato/adapters/claude_cli.py`
- `src/ato/models/schemas.py`
- `tests/unit/test_claude_adapter.py`
- `tests/fixtures/claude_output_*.json`

**验证命令:**

```bash
uv run pytest tests/unit/test_claude_adapter.py -v
```

#### Story 10.3 — Transition/Preflight Recovery Semantics

**Acceptance Criteria:**

- Given `submit_and_wait()` 调用方等待超时，then completion future 不被取消，后续 consumer 完成后结果仍可记录或查询。
- Given timeout 来自 ack 等待，then recovery/core 不把 task 直接标记为 failed。
- Given finalize 抛 `CLIAdapterError` 但 worktree 已 clean，then preflight gate 继续推进 transition。
- Given finalize 抛非 CLI 异常且 worktree 仍 dirty/unknown，then 创建 `preflight_failure` approval。
- Given story 已是 `blocked`，when 用户批准 `manual_commit_and_retry`，then 不提交非法 `dev_done/fix_done`，而是创建可操作恢复 approval 或保留原 approval 未消费。

**主要文件:**

- `src/ato/transition_queue.py`
- `src/ato/core.py`
- `src/ato/recovery.py`
- `src/ato/subprocess_mgr.py`
- `tests/unit/test_transition_queue.py`
- `tests/unit/test_core.py`

**验证命令:**

```bash
uv run pytest tests/unit/test_transition_queue.py tests/unit/test_core.py tests/unit/test_recovery.py -v
```

#### Story 10.4 — BMAD Parser Reliability

**Acceptance Criteria:**

- Given output contains `Verdict: PASS`、`STATUS: PASS`、`Recommendation: Approve`、
  `No blocking findings`、`0 blocking` or `0 patch`，then deterministic parser returns pass/approved without semantic fallback.
- Given deterministic parser misses and semantic fallback is needed，then timeout comes from settings, not a hard-coded 60s.
- Given semantic fallback times out，then parse_failed payload includes `skill_type`、`input_length`、`timeout_seconds`、`parser_mode`、preview。
- Given parse_failed creates approval，then approval summary explains parser infrastructure failure, not code quality failure.

**主要文件:**

- `src/ato/adapters/bmad_adapter.py`
- `src/ato/adapters/semantic_parser.py`
- `src/ato/config.py`
- `tests/unit/test_bmad_adapter.py`
- `tests/unit/test_semantic_parser.py`

**验证命令:**

```bash
uv run pytest tests/unit/test_bmad_adapter.py tests/unit/test_semantic_parser.py -v
```

#### Story 10.5 — Merge Queue Boundary Hygiene

**Acceptance Criteria:**

- Given pre-merge gate persistent failure，when approval is visible，then merge queue entry has already been marked failed/retryable and `current_merge_story_id` is released.
- Given `preflight_failure` approval is decided quickly after creation，then retry does not observe stale merge lock.
- Given porcelain dirty parser handles rename/untracked/space path/malformed line，then transition_queue and merge_queue use the same implementation.
- Given `_run_pre_merge_gate()` exits through unexpected exception paths，then `second_result` cannot be referenced before assignment.

**主要文件:**

- `src/ato/merge_queue.py`
- `src/ato/transition_queue.py`
- `src/ato/worktree_utils.py` 或等价共享模块
- `tests/unit/test_merge_queue.py`
- `tests/unit/test_transition_queue.py`

**验证命令:**

```bash
uv run pytest tests/unit/test_merge_queue.py tests/unit/test_transition_queue.py -v
```

#### Story 10.6 — Incident Regression Suite

**Acceptance Criteria:**

- Given a simulated Claude result followed by post-result DB/flush timeout，then Orchestrator does not remain silently stuck.
- Given result+exit_code=1 during finalize，then worktree git verification decides clean-or-approval.
- Given transition ack timeout，then task is not marked failed without confirmation.
- Given blocked story receives `manual_commit_and_retry`，then recovery remains actionable.
- Given BMAD PASS output，then no semantic fallback subprocess is invoked.
- Given `creating` initial dispatch writes a canonical artifact path，then `test_initial_dispatch.py` no longer expects the legacy `"initial_dispatch_requested"` placeholder.

**主要文件:**

- `tests/integration/test_incident_2026_04_08.py` 或拆入现有 integration suites
- `tests/unit/test_subprocess_mgr.py`
- `tests/unit/test_claude_adapter.py`
- `tests/unit/test_transition_queue.py`
- `tests/unit/test_core.py`
- `tests/unit/test_initial_dispatch.py`

**验证命令:**

```bash
uv run pytest tests/unit/test_subprocess_mgr.py tests/unit/test_claude_adapter.py tests/unit/test_transition_queue.py tests/unit/test_core.py -v
uv run pytest tests/unit/test_initial_dispatch.py -k creating -v
uv run pytest tests/integration/test_crash_recovery.py tests/integration/test_worktree_boundary_preflight.py -v
```

### 4.5 UX 修改提案

UX 不需要新增界面，但审批文案需要遵守现有“发生了什么 + 你的选项”规则：

```markdown
preflight_failure / blocked_recovery 摘要必须说明：
- 当前 story phase；
- retry_event 是否可直接执行；
- 如果不能执行，推荐的恢复操作；
- 不得在 action 失败后把 approval 从用户视角消失。
```

**理由:** BUG-005 的用户体验问题不是缺少按钮，而是审批被消费后没有实际恢复路径。

---

## 5. Implementation Handoff

### 变更范围分类

**Moderate** — 不需要 PM/Architect 重新定义 MVP，但需要 SM/PO 调整 backlog，并由 Dev/QA 分阶段实现。

### 角色与职责

| 角色 | 责任 |
| --- | --- |
| SM / PO | 批准本提案后，使用 `bmad-create-story` 创建 10.1-10.6 stories，并更新 `sprint-status.yaml` |
| Architect | 审核 Decision 11、schema 字段选择（是否新增 `process_exit_code` / warning metadata） |
| Dev | 使用 `bmad-dev-story` 按顺序实现 10.1 → 10.2 → 10.3，再实现 10.4/10.5 |
| QA / Test Architect | 用 `bmad-testarch-test-design` 或直接补 integration regression suite，验证事故链不复现 |
| Reviewer | 使用 `bmad-code-review` 对 P0/P1 修复做 adversarial review，重点检查 asyncio cancellation、SQLite 事务和 approval 消费语义 |

### 推荐实施顺序

1. 先执行 10.1 和 10.2，避免继续把完成任务卡成 running 或误判失败。
2. 再执行 10.3，消除 preflight/transition/approval 的死胡同。
3. 再执行 10.4，降低 BMAD parse timeout 带来的人工审批噪音。
4. 最后执行 10.5/10.6，收敛竞态、维护性问题和事故回归测试。

### 暂停/继续建议

在 10.1-10.3 完成前，不建议继续大规模并行运行新的自动化 story；如果必须运行，应限制并发并人工监控
`running` task、dead PID、pending approvals 和 cost_log。

### 成功标准

- `post_result_timeout` 后不会出现 worker PID 已死但 task 永久 `running`。
- Claude result+exit_code=1 不再触发 crash_recovery。
- transition ack timeout 不再直接把成功任务标记 failed。
- preflight/finalize 失败必然进入 clean-or-approval 状态。
- blocked 状态下审批 retry 不会被静默消费。
- BMAD 明确 PASS/Approve 输出不再调用 semantic fallback。
- merge queue 在 approval 可见前已释放内部 retry lock。

### 必跑验证

```bash
uv run pytest tests/unit/test_subprocess_mgr.py tests/unit/test_claude_adapter.py tests/unit/test_transition_queue.py tests/unit/test_core.py -v
uv run pytest tests/unit/test_bmad_adapter.py tests/unit/test_merge_queue.py -v
uv run pytest tests/integration/test_crash_recovery.py tests/integration/test_worktree_boundary_preflight.py -v
uv run ruff check src tests
uv run mypy src
```

---

## 6. Checklist 结果

| Checklist | 状态 | 结论 |
| --- | --- | --- |
| 1. Trigger and Context | Done | 触发源为 2026-04-08 监控事故，证据充分 |
| 2. Epic Impact Assessment | Done | 影响 Epic 2B/4/5/9，推荐新增 corrective Epic 10 |
| 3. Artifact Conflict Analysis | Done | PRD/Architecture 需补语义澄清，UX 仅需审批文案约束 |
| 4. Path Forward Evaluation | Done | Direct Adjustment 最合适；rollback 和 MVP scope review 不推荐 |
| 5. Sprint Change Proposal Components | Done | 已给出 issue、impact、approach、detailed proposals 和 handoff |
| 6. Final Review and Handoff | Done | 用户已批准；本轮将更新 sprint-status 并一次性创建 10.1-10.6 stories |

---

## 7. 执行事项

批准后执行：

1. 更新 `_bmad-output/implementation-artifacts/sprint-status.yaml`，新增 Epic 10 与 10.1-10.6 条目。
2. 使用 `bmad-create-story` 先创建 Story 10.1。
3. 在 Story 10.1 完成并通过 review 后，再创建/执行 Story 10.2 和 10.3。

**执行更新:** 用户已批准，并要求一次性创建 10.1-10.6 的所有 story 文件。本轮将 10.1-10.6 标记为 `ready-for-dev`，但仍建议按 10.1 → 10.2 → 10.3 → 10.4 → 10.5 → 10.6 顺序实现。

本提案未修改运行时代码。
