# BidWise Story 8.2 Test Policy UAT Findings

> 日期: 2026-04-09
> 目标项目: `BidWise`
> 验证对象: cross-project test policy layering
> 验证 story: `8-2-export-preview`

## 1. 结论摘要

本轮 UAT 证明了新的 test policy layering 已经成功进入目标项目 `qa_testing` prompt surface，并能驱动 required/optional test layers 的执行顺序；但同时暴露出两个不同层级的问题：

1. **ATO 编排缺陷**: convergent-loop 在 QA CLI 退出后过早将 task 标记为 `completed`，导致 story 仍停留在 `qa_testing` 时被 poll cycle 误判为“无活跃 task”，进而重复派发第二个 QA task。
2. **QA 协议治理缺口**: `qa_testing` 目前只有 prompt 约束，没有像 `regression` 一样的 command-audit fail-closed 校验，因此 LLM 可以在 additional command budget 内错误地优先执行 diagnostic 命令，甚至超出预算。

其中第 1 个问题已在 ATO 中修复并补充回归测试；第 2 个问题仍是未完成的架构收口项。

## 2. 本轮 UAT 中确认的现象

### 2.1 Policy prompt 注入符合预期

`qa_testing` 阶段实际下发给 Codex 的 prompt 包含以下显式策略要素：

- `Policy source: explicit`
- `Required layers: lint, typecheck, unit`
- `Optional layers: integration, build, smoke`
- `allow_discovery: true`
- `max_additional_commands: 3`
- `allowed_when: after_required_commands`

这说明目标项目 `ato.yaml` 中的 `phase_test_policy` 已被 ATO 正确读取并渲染进 QA prompt。

### 2.2 第一轮 QA 的有效表现

第一轮 QA 的以下行为符合 spec 预期：

- required commands 按配置顺序执行
- additional-command gate 在 required set 完成后才打开
- QA 输出合同完整，包含 `Recommendation`、`Quality Score`、`## Commands Executed`、findings 等结构化内容
- QA 找到的首要 root cause 合理: worktree 缺少 Node install，属于环境阻断，不是已确认的 story 功能回归

### 2.3 第一轮 QA 的 spec 偏离

第一轮 QA 也出现了两处明确偏离：

1. **超出 additional-command 预算**
   - 配置与 prompt 均要求 `max_additional_commands=3`
   - 实际记录了 4 条 additional commands:
     - `pnpm python:setup && pnpm test:python`
     - `pnpm build`
     - `pwd`
     - 读取 `systematic-debugging/SKILL.md`

2. **diagnostic 命令优先级错误**
   - spec 要求先按 optional 顺序消费项目定义测试命令，再允许 discovered/diagnostic 命令
   - 实际并未执行 `smoke`
   - 却先消耗了两个 diagnostic slots 在 `pwd` 与读取 skill 文件上

这里的 **diagnostic slot** 指的是: required set 执行完成后，LLM 在 additional budget 内额外执行的一条诊断型命令配额。`pwd` 和读取 skill 文件都属于 `llm_diagnostic`，不应在尚有 optional test command 未消费时优先出现。

## 3. 重复 QA 派发问题

### 3.1 外部表现

TUI 已显示 story 回到 `fixing`，但右侧 CLI pane 仍继续执行 `qa_testing`，并再次尝试提交 `qa_fail`。最终第二个 QA task 在提交 transition 时被状态机拒绝，报出：

`TransitionNotAllowed: Can't qa_fail when in Fixing`

这不是界面延迟，而是真实存在的 stale QA task。

### 3.2 根因

根因是 convergent-loop 的 task 生命周期与 phase transition 提交之间存在竞态窗口：

1. QA CLI 子进程退出
2. `SubprocessManager` 立即把 task 标记为 `completed`
3. 但 BMAD parse、findings 入库、`qa_fail` transition 提交仍未完成
4. poll cycle 调用 `get_undispatched_stories()` 时，看到：
   - story 仍在 `qa_testing`
   - 当前 phase 没有 `running/pending/paused` task
5. `_dispatch_undispatched_stories()` 因此误判为“未调度 story”，又插入并启动第二个 QA task

### 3.3 修复

修复策略是将 convergent-loop 的 post-processing 也纳入 task 的活跃窗口：

- CLI 成功返回后，不再立刻把非 `reviewing` 的 convergent-loop task 终态收口为 `completed`
- 在 parse / findings / transition 处理期间，将 task 保持为 `running`
- 仅在 post-processing 全部完成后，才最终标记为 `completed`

### 3.4 已补充验证

ATO 已补充回归测试，覆盖以下语义：

- convergent-loop 在 post-processing 期间，story 不应重新进入 initial dispatch
- task 在 parse 未完成时应保持 `running`
- post-processing 结束后 task 才能转为 `completed`

## 4. 仍未解决的根本问题

### 4.1 当前 QA 只能“提示约束”，不能“执行约束”

`qa_testing` 目前的 test policy enforcement 主要发生在 prompt 层：

- prompt 会告诉 LLM required/optional layers、预算、顺序、是否允许 discovery
- 但 ATO 在消费 QA 结果时，并不会严格校验 `## Commands Executed` 是否遵守这些规则

因此当前系统最多只能做到：

- 事前引导 LLM
- 事后人工审阅发现其违反策略

它还做不到：

- 自动拒绝超 budget 或越级 diagnostic 的 QA 结果
- 自动保证 LLM 严格先跑 optional test commands，再跑 diagnostic

### 4.2 regression 与 qa_testing 的治理强度不一致

`regression` 路径已有结构化 command audit 校验，能够 fail-closed；
`qa_testing` 路径仍主要依赖 prompt 合同与 BMAD findings parse。

这意味着当前系统在 test-policy layering 上存在不一致：

- `regression`: 机器校验为主
- `qa_testing`: prompt 约束为主

## 5. 建议的后续收口方向

### 5.1 近期建议

为 `qa_testing` 增加与 `regression` 同等级的 command-audit validator，至少校验：

- required commands 是否按顺序执行
- additional commands 是否超出预算
- 在仍有 optional commands 未执行时，是否过早出现 `llm_diagnostic`
- `allow_discovery=false` 时，是否仍出现 discovered/diagnostic command

一旦违反协议，应将该轮 QA report 视为 **protocol-invalid**，而不是当作正常 `qa_fail` 结果消费。

#### 5.1.1 `protocol-invalid` 的审批语义

`protocol-invalid` 在近期方案中**不建议新增 approval_type**。更稳妥的做法是：

- 继续复用现有 `needs_human_review`
- 通过 payload 中的 `reason=qa_protocol_invalid` 区分它与
  - `bmad_parse_failed`
  - design gate 失败
- 默认决策仍保持现有 `retry / skip / escalate`

这样做的原因是：

- 现有 orchestrator 已经为 `needs_human_review` 打通了 retry/requeue 语义
- `protocol-invalid` 的本质是“该轮 QA 结果不可自动消费”，而不是“产品代码已确认失败”
- 若直接把它落为普通 `qa_fail`，会把协议治理失败误记为产品缺陷

因此，近期 V1 的正确处理链路应为：

1. QA markdown 解析出 `## Commands Executed`
2. command-audit validator 校验 policy
3. 若校验失败：
   - 不提交 `qa_fail`
   - 不将该轮 findings 当作有效 QA findings 入库
   - 将该轮结果标记为 `protocol-invalid`
   - 创建 `needs_human_review` approval，推荐动作保持 `retry`

#### 5.1.2 `protocol-invalid` payload 合同

建议新增一个共享 helper（例如 `build_qa_protocol_invalid_payload()`），由 recovery / core 共用，避免 payload 结构分叉。

建议 payload 最小字段集如下：

```json
{
  "reason": "qa_protocol_invalid",
  "task_id": "t-qa-123",
  "phase": "qa_testing",
  "skill_type": "qa_report",
  "audit_status": "missing|malformed|invalid",
  "violation_code": "ADDITIONAL_BUDGET_EXCEEDED",
  "detail": "executed additional commands 超过 max_additional_commands 限制",
  "policy_source": "explicit",
  "allow_discovery": true,
  "allowed_when": "after_required_commands",
  "max_additional_commands": 3,
  "required_commands": ["uv run pytest"],
  "optional_commands": ["pnpm build", "pnpm smoke"],
  "commands_executed_preview": [
    "`uv run pytest` | source=project_defined | trigger=required_layer:unit | exit_code=0",
    "`pwd` | source=llm_diagnostic | trigger=diagnostic:cwd_check | exit_code=0"
  ],
  "raw_output_preview": "QA markdown preview...",
  "options": ["retry", "skip", "escalate"]
}
```

建议同时固定以下约束：

- `task_id` 必填
  - 否则 `needs_human_review + retry` 无法精确重调度原 QA task
- `reason` 固定为 `qa_protocol_invalid`
- `audit_status` 仅表示审计阶段
  - `missing`: `## Commands Executed` 缺失
  - `malformed`: section 存在但格式不可解析
  - `invalid`: 能解析，但违反 policy
- `violation_code` 使用固定枚举，而不是直接依赖自然语言错误串
- `detail` 保存单行人类可读原因，供 CLI/TUI 直接展示
- `commands_executed_preview` 仅保存有限条目，避免 payload 过大
- 不复用 design gate 的 `failure_codes`
  - 否则会和现有 `needs_human_review` 的 design gate 渲染分支冲突

建议第一版固定的 `violation_code` 最小集合：

- `COMMANDS_EXECUTED_MISSING`
- `COMMANDS_EXECUTED_MALFORMED`
- `REQUIRED_ORDER_VIOLATION`
- `REQUIRED_COMMANDS_INCOMPLETE`
- `OPTIONAL_PRIORITY_VIOLATION`
- `ADDITIONAL_BUDGET_EXCEEDED`
- `ADDITIONAL_GATE_CLOSED`
- `DISCOVERY_DISABLED`
- `INVALID_COMMAND_SOURCE`
- `INVALID_TRIGGER_REASON`

#### 5.1.3 测试矩阵

建议至少覆盖以下 5 层测试：

1. payload helper
   - `build_qa_protocol_invalid_payload` 包含 `task_id`、`reason`、`violation_code`
   - `commands_executed_preview` 会截断
   - 空字段遵循“缺失即省略”，不输出占位字段

2. approval 展示
   - `needs_human_review` 收到 `reason=qa_protocol_invalid` 时，TUI/CLI 展示协议违规文案
   - 展示 `task_id`、`violation_code`、`detail`
   - 缺失可选字段时不展示空行

3. QA parser
   - 缺少 `## Commands Executed` → `audit_status=missing`
   - 命令行格式错误 → `audit_status=malformed`
   - canonical 格式输入 → 成功解析为结构化 command audit

4. shared validator
   - required 未完成或顺序错误
   - optional 尚未消费完时提前执行 diagnostic/discovered
   - additional commands 超出预算
   - `allow_discovery=false` 仍执行 discovered/diagnostic
   - `allowed_when=after_required_failure` 但 required 全成功仍追加命令
   - source / trigger_reason 非法

5. recovery 集成
   - `protocol-invalid` 时创建 `needs_human_review`
   - 不提交 `qa_pass/qa_fail`
   - 不把该轮 findings 当作有效 QA findings 入库
   - task 标记为 failed / non-consumable result，并保留 retry 上下文
   - `needs_human_review + retry` 可重排同一 `task_id`

### 5.2 中期建议

将 QA 的命令执行面从“开放 shell 自由执行”收回到“受控执行接口”：

- orchestrator 决定 required/optional commands 的实际执行序列
- LLM 只负责分析结果
- 如需额外诊断命令，LLM 只能发起结构化请求，由 orchestrator 决定是否放行

### 5.3 长期建议

若目标是“LLM 必须按约定执行测试策略”，则需要进一步收权：

- required/optional test execution 由 orchestrator 直接执行
- LLM 不再拥有 unrestricted shell 作为 QA 主执行面

否则，prompt 与 fail-closed validator 只能做到“违规不被接受”，而不能从根本上保证“LLM 一开始就按协议执行”。

## 6. 当前状态

- `BidWise` 侧 test policy prompt 注入: 已验证
- convergent-loop 重复 QA 派发竞态: 已修复
- QA 对 `Commands Executed` 的机器校验: 未实现
- QA 对 additional budget / diagnostic priority 的 fail-closed enforcement: 未实现
