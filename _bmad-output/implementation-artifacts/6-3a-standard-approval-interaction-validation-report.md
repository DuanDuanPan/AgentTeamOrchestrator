# Story 验证报告：6.3a 常规审批交互

验证时间：2026-03-27
Story 文件：`_bmad-output/implementation-artifacts/6-3a-standard-approval-interaction.md`
验证模式：`validate-create-story`
结果：PASS（已应用修正）

## 摘要

该 story 的目标方向正确，但原稿里有 4 个会直接误导开发实现的缺口，已在 story 文件中修正：

1. 它把所有常规审批都写成了统一的 `y=recommended_action`、`n=reject`，这和当前仓库的真实审批合同冲突，尤其会把 `blocking_abnormal` 的 `y/n` 语义写反。
2. 它把展开态审批上下文放到了右下面板，和现有 Dashboard 的面板分工以及 UX 规范“上看上下文、下做决策”相冲突。
3. 它在 AC 里要求持久化“时间戳 + 选择理由”，但 Tasks/Test 并没有要求落 `decision_reason`，还建议用短暂 Toast 代替主反馈路径，无法满足 UX 里的行内中间状态。
4. 它没有处理当前仓库已经存在的多选审批类型，容易让开发者把 `y/n` 错误地作用到 `session_timeout` / `crash_recovery` / `precommit_failure` / `rebase_conflict` 等多选审批上。

## 已核查证据

- 规划与规范工件：
  - `_bmad-output/planning-artifacts/epics.md`
  - `_bmad-output/planning-artifacts/prd.md`
  - `_bmad-output/planning-artifacts/architecture.md`
  - `_bmad-output/planning-artifacts/ux-design-specification.md`
- 前置 story：
  - `_bmad-output/implementation-artifacts/4-1-approval-queue-nudge.md`
  - `_bmad-output/implementation-artifacts/6-1a-tui-launch-sqlite-connection.md`
  - `_bmad-output/implementation-artifacts/6-2a-three-question-header.md`
  - `_bmad-output/implementation-artifacts/6-2b-dashboard-story-list.md`
- 当前代码：
  - `src/ato/tui/app.py`
  - `src/ato/tui/dashboard.py`
  - `src/ato/tui/theme.py`
  - `src/ato/cli.py`
  - `src/ato/models/db.py`
  - `src/ato/models/schemas.py`
  - `src/ato/approval_helpers.py`
  - `src/ato/core.py`
  - `src/ato/validation.py`
  - `src/ato/merge_queue.py`
  - `tests/integration/test_tui_pilot.py`
  - `tests/unit/test_approval.py`
  - `tests/unit/test_cli_approval.py`

## 发现的关键问题

### 1. `y/n` 被误写成统一的“推荐 / reject”，和真实审批合同冲突

原 story 写的是：

- `y` → `decision=recommended_action`, `status="approved"`
- `n` → `decision="reject"`, `status="rejected"`

但当前仓库的真实合同并不是这样：

- `src/ato/cli.py` 的 `_DEFAULT_VALID_OPTIONS` 定义了按 `approval_type` 区分的合法 decision
- `src/ato/core.py::_handle_approval_decision()` 也按类型消费具体 decision，而不是消费“推荐/拒绝”抽象层
- `src/ato/models/schemas.py` 里 `APPROVAL_RECOMMENDED_ACTIONS["blocking_abnormal"] == "human_review"`
- UX 规范里却明确写了 `blocking_abnormal` 的快捷键语义是 `y=confirm_fix`, `n=human_review`

如果按原稿实现，开发者极有可能把 `blocking_abnormal` 的 `y` 键写成 `human_review`，直接和 UX / consumer 逻辑对撞。

已应用修正：

- AC3 改成“`y/n` 必须由 `approval_type + payload.options` 的确定性映射解析”
- 增加了明确的 `merge_authorization` / `blocking_abnormal` / `budget_exceeded` / `timeout` / `batch_confirmation` 对照表
- Dev Notes 中新增共享 helper 约束，禁止继续把 `y` 简化成 `recommended_action`

### 2. 展开态详情面板落点错误，违背当前 Dashboard 面板契约

原 story 的 AC2 / Task 2 写成：

- 展开内容在 `#right-bottom-content` 渲染

但前置 story 6.2b 已经把右侧面板职责固定为：

- 右上：上下文 / 详情
- 右下：动作区域

架构 / UX 也一致强调“上看上下文、下做决策”。如果按原稿实现，会导致：

- 右上面板仍保留旧 story 详情
- 右下面板塞入多行上下文，动作提示反而没地方放
- 交互模型和 6.2b 已建立的结构相互打架

已应用修正：

- AC2 / Task 2 改为右上面板显示审批上下文
- 右下面板固定保留动作标签 / 中间状态
- 示例草图同步改成“右上详情 + 右下快捷键”

### 3. FR20 要求的 `decision_reason` 与 UX 要求的行内中间状态都没落进执行任务

原 story 在 AC3 写了：

- 写入“决策 + 时间戳 + 选择理由”

但 Task 3 和测试只关注了 status / decision，没有要求：

- `decision_reason` 如何持久化
- `update_approval_decision()` 的正确调用方式
- TUI 无输入框前提下如何生成 deterministic reason

同时 Dev Notes 还推荐用 `notify()` 做 2 秒 Toast，这和 UX 规范“右下面板显示 `$muted 已提交，等待处理`，直到 SQLite 状态更新”冲突。

已应用修正：

- Task 3 明确要求 `decision_reason` 至少包含 `tui` 来源、按键和值
- 即时反馈改成右下面板行内状态，直到轮询移除
- `notify()` 被降级为可选增强，不再作为主反馈路径
- 集成测试示例同步改为断言 `decision_reason`

### 4. 没有给多选审批任何边界，容易把 6.3a 实现成“错误消费一切 pending approval”

当前仓库已经会创建多种 `payload.options > 2` 的审批：

- `session_timeout`
- `crash_recovery`
- `regression_failure`
- `precommit_failure`
- `rebase_conflict`
- `needs_human_review`
- `convergent_loop_escalation`

原稿没有给出边界，导致开发者很可能：

- 把所有 pending approval 都挂上 `y/n`
- 对 3 选 / 多选审批做错误写入
- 让 TUI 先于 Story 6.3b 抢掉异常审批交互的职责

已应用修正：

- 新增 AC7：多选审批降级
- Scope Boundary 明确 6.3a 只处理二选一 / `y/n` 语义明确的审批
- 其余审批只显示卡片与 CLI / 6.3b fallback，不在本 Story 错误消费
- 测试要求新增“多选审批禁用 `y/n`”与 fallback 用例

## 已应用增强

- 图标映射示例补回了当前代码里已经存在的 `rebase_conflict`
- 去掉了大量易漂移的源码行号引用，改成文件 / 章节级引用
- 增加了“不能让 `tui/` 直接 import `cli.py` 私有 helper”的架构约束，避免后续为复用逻辑引入错误依赖

## 剩余风险

- 当前代码里真正创建的 timeout 类审批以 `session_timeout` 为主，而 UX 文档中的 `timeout` 是二选一语义；后续进入实现阶段时，需要根据真实业务期望确认 `session_timeout` 是否继续保留在 6.3b fallback，还是拆出单独的两选一 TUI 路径。
- 本次工作只修订了 story 文档和验证报告，没有实现 UI 代码，也没有运行 Textual 测试；目标是先把实现契约收紧，避免开发阶段走错路。

## 最终结论

修正后，该 story 已从“看起来合理但会把审批语义做错”收敛成“可以直接交给 dev-story 执行”的状态。高风险误导点已经移除：不会再把 `y` 误绑到 `recommended_action`、不会再把详情塞进错误面板、不会再漏掉 `decision_reason` 和行内确认状态，也不会再把多选审批错误地塞进 6.3a 的 `y/n` 路径。
