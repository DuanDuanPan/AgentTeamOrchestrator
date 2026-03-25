# Story 3.2a: Convergent Loop 首轮全量 Review

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a 系统,
I want 在 story 进入 review 阶段时执行首轮全量 review,
So that 获得完整的质量基线。

## Acceptance Criteria

1. **AC1 — 首轮全量 Review 调度**
   ```
   Given story 进入 review 阶段
   When Convergent Loop 启动第 1 轮
   Then 调度 reviewer agent（Codex read-only）执行全量 review
   And 解析 findings 通过 BMAD adapter，入库到 SQLite findings 表
   ```

2. **AC2 — 零 Finding 快速收敛**
   ```
   Given 第 1 轮 review 返回 0 个 finding
   When 评估收敛条件
   Then 直接判定为收敛（无需 fix），story 进入下一阶段（qa_testing）
   ```

3. **AC3 — Finding 入库完整性**
   ```
   Given findings 入库
   When 每个 finding 写入 SQLite
   Then 包含：finding_id、story_id、round_num=1、severity、description、status=open、file_path、rule_id、dedup_hash
   And structlog 记录 round_num=1、findings_total、open_count
   ```

## Tasks / Subtasks

- [x] Task 1: 实现 ConvergentLoop 核心类 (AC: #1, #2, #3)
  - [x] 1.1 在 `src/ato/convergent_loop.py` 中实现 `ConvergentLoop` 类，接收依赖注入参数：
    - `db_path: Path`——数据库路径
    - `subprocess_mgr: SubprocessManager`——用于 dispatch review agent
    - `bmad_adapter: BmadAdapter`——用于解析 review Markdown 输出
    - `transition_queue: TransitionQueue`——用于提交状态转换事件
    - `config: ConvergentLoopConfig`——`max_rounds` 和 `convergence_threshold`
    - `nudge: Nudge | None = None`——可选通知
  - [x] 1.2 实现 `async run_first_review(story_id: str, worktree_path: str | None) -> ConvergentLoopResult`：
    - 调用 `subprocess_mgr.dispatch_with_retry()` 执行 Codex review（`cli_tool=”codex”`, `role=”reviewer”`, `phase=”reviewing”`）
    - review 必须在 story 的独立 worktree 内执行：优先使用传入的 `worktree_path`；若为 `None`，则从 `stories.worktree_path` 读取；若仍为空则直接报错，不要退化到仓库根目录
    - prompt 应包含 resolved worktree path（review 目标代码路径）
    - options 中设置 `cwd=<resolved_worktree_path>` 和 `sandbox=”read-only”`
    - 返回 `ConvergentLoopResult` 包含 `round_num=1`、converged 状态、finding 统计
  - [x] 1.3 实现 review 结果解析流程：
    - 从 `AdapterResult.text_result`（Codex 的 review Markdown 输出）调用 `bmad_adapter.parse(markdown, skill_type=BmadSkillType.CODE_REVIEW, story_id=story_id)`
    - 处理 `BmadParseResult`：verdict 为 `approved` 或 `changes_requested` 或 `parse_failed`
    - `parse_failed` 时以 **keyword arguments** 调用 `record_parse_failure()` 创建人工审批记录；如 `self._nudge` 存在，可传入 `notifier=self._nudge.notify`
  - [x] 1.4 实现 finding 入库流程：
    - 将 `BmadFinding` 列表转换为 `FindingRecord` 列表（生成 `finding_id=uuid4()`、`round_num=1`、`status=”open”`、计算 `dedup_hash`）
    - 调用 `insert_findings_batch(db, records)` 批量入库
    - 调用 `maybe_create_blocking_abnormal_approval()` 检查 blocking 阈值
  - [x] 1.5 实现收敛评估（首轮特化逻辑）：
    - 0 个 finding → converged=True，提交 `TransitionEvent(event_name=”review_pass”, source=”agent”, submitted_at=...)` 到 TransitionQueue
    - ≥1 个 blocking finding → converged=False，提交 `TransitionEvent(event_name=”review_fail”, source=”agent”, submitted_at=...)`
    - 仅 suggestion 无 blocking → converged=True（suggestion 不阻塞收敛）

- [x] Task 2: 定义 ConvergentLoopResult 模型 (AC: #1, #2, #3)
  - [x] 2.1 在 `src/ato/models/schemas.py` 中添加：
    ```python
    class ConvergentLoopResult(_StrictBase):
        “””单轮 Convergent Loop 结果。”””
        story_id: str
        round_num: int
        converged: bool
        findings_total: int
        blocking_count: int
        suggestion_count: int
        open_count: int
        closed_count: int = 0
        new_count: int = 0
    ```

- [x] Task 3: 实现 Deterministic Validation Gate (AC: #1)
  - [x] 3.1 仅在存在**明确的结构化 review artifact payload** 时调用 `validate_artifact()` 执行 JSON Schema 前置验证；当前 MVP 的首轮 code review 直接审查 worktree，默认无 artifact JSON，因此该 hook 必须安全跳过
  - [x] 3.2 若未来 caller 提供 artifact payload 且验证失败：提交 `TransitionEvent(event_name=”validate_fail”, source=”agent”, submitted_at=...)`（story 回退到 creating），记录 validation errors 到 structlog / 返回结果，并提前返回，不进入 agent review
  - [x] 3.3 不要为本 story 新增”review validation task”或伪造 schema 输入；`TaskRecord.error_message` 的承载留给后续显式 review-validation task 接入时处理

- [x] Task 4: structlog 结构化日志 (AC: #3)
  - [x] 4.1 review 启动时记录：`convergent_loop_round_start`，字段 `story_id`, `round_num=1`, `phase=”reviewing”`
  - [x] 4.2 findings 入库后记录：`convergent_loop_round_complete`，字段 `round_num=1`, `findings_total`, `open_count`, `blocking_count`, `suggestion_count`
  - [x] 4.3 收敛判定后记录：`convergent_loop_converged` 或 `convergent_loop_needs_fix`

- [x] Task 5: 测试 (AC: #1, #2, #3)
  - [x] 5.1 创建 `tests/unit/test_convergent_loop.py`：
    - `test_first_review_zero_findings_converges`——0 findings → converged=True，提交 review_pass
    - `test_first_review_blocking_findings_not_converged`——有 blocking → converged=False，提交 review_fail
    - `test_first_review_only_suggestions_converges`——仅 suggestion → converged=True
    - `test_first_review_requires_resolved_worktree_path`——无显式/持久化 worktree_path 时直接失败，不在仓库根目录执行 review
    - `test_first_review_findings_persisted`——findings 正确写入 SQLite，round_num=1, status=open
    - `test_first_review_dedup_hash_computed`——每个 finding 的 dedup_hash 非空
    - `test_first_review_blocking_threshold_escalation`——blocking 数量超阈值 → approval 创建
    - `test_first_review_parse_failure_creates_approval`——BMAD 解析失败 → 创建人工审批
    - `test_first_review_validation_hook_skips_without_artifact_payload`——当前 MVP 无结构化 artifact 时不调用 `validate_artifact()`
    - `test_first_review_validation_failure_submits_validate_fail`——显式提供无效 artifact payload 时提交 `validate_fail` 并提前返回
    - `test_first_review_structlog_fields`——验证日志包含 round_num, findings_total, open_count
    - `test_convergent_loop_result_model`——ConvergentLoopResult 构建和验证
  - [x] 5.2 测试使用 mock SubprocessManager 和 mock BmadAdapter——不实际调用 CLI
  - [x] 5.3 测试 TransitionQueue 交互使用 mock——验证正确事件提交（`review_pass` / `review_fail` / `validate_fail`），且 `source=”agent”`、`submitted_at` 已填充

## Dev Notes

### 架构定位

本 story 是 `src/ato/convergent_loop.py` 的首次实现，将空占位文件填充为 Convergent Loop 协议的核心代码。Story 3.2a 只实现**第 1 轮全量 review** 逻辑，后续 story 逐步扩展：
- Story 3.2b: fix dispatch（调度 Claude 修复 blocking findings）
- Story 3.2c: re-review scope narrowing（第 2+ 轮窄域复审）
- Story 3.2d: 收敛判定与终止条件（max_rounds + escalation）

本 story 交付后，`convergent_loop.py` 应包含可独立运行的"首轮 review + 收敛评估"流程，后续 story 在此基础上追加 fix/re-review 循环。

### 关键设计约束

**Review Agent 调度方式：**
- 使用 `subprocess_mgr.dispatch_with_retry()`，不直接创建 subprocess
- `cli_tool="codex"`, `role="reviewer"`，Codex 以 read-only sandbox 模式执行 review
- prompt 需包含 resolved worktree path（story 代码所在的 git worktree 目录路径）
- `cwd` 必须指向该 worktree；若调用方未传入 path，则先从 `stories.worktree_path` 读取，缺失时直接失败，不允许在仓库根目录 review
- SubprocessManager 内部已处理：TaskRecord 创建/更新、PID 注册、CostLogRecord 写入、重试逻辑

**BMAD Adapter 解析流程：**
- Codex review 输出是 Markdown 格式（通过 `code-review` BMAD skill）
- 调用 `BmadAdapter.parse(markdown, skill_type=BmadSkillType.CODE_REVIEW, story_id=story_id)` 解析
- BmadAdapter 已实现 3 阶段解析（deterministic → semantic_fallback → failed）
- 返回 `BmadParseResult`，其中 `findings: list[BmadFinding]` 已包含 severity、file_path、rule_id、dedup_hash
- `verdict` 决定 review 结果：`approved`（无问题）、`changes_requested`（有 blocking）、`parse_failed`（解析失败）

**BmadFinding → FindingRecord 转换：**
- `BmadFinding` 已有 `severity`, `description`, `file_path`, `rule_id`, `dedup_hash`
- 需补充：`finding_id`（uuid4）、`story_id`、`round_num=1`、`status="open"`、`created_at`
- `BmadFinding.line` → `FindingRecord.line_number`（可选）
- `BmadFinding.fix_suggestion` → `FindingRecord.fix_suggestion`（可选，BmadFinding 无此字段，设为 None）

**Deterministic Validation Gate：**
- 在 agent review 之前执行 JSON Schema 前置验证（`validate_artifact()` from `validation.py`）
- Story 3.2a 的上下文：review 的输入 artifact 是 story 文档或代码变更。当前 MVP 中 review 直接在 worktree 中执行代码审查，没有显式 artifact JSON 需要 schema 验证
- **实现建议**：如果没有明确的 artifact JSON 可供 schema 验证，则跳过 deterministic validation gate，直接进入 agent review。不要为了“满足 gate”而伪造 JSON 或额外创建 review-validation task。
- 若未来 caller 提供结构化 artifact payload，则验证失败路径只需提交 `validate_fail` 事件并返回；当前 story 不新增 `TaskRecord.error_message` 的专用接线

**TransitionQueue 事件提交：**
- `TransitionEvent.source` 当前仅接受 `"agent" | "tui" | "cli"`；Convergent Loop 内部提交统一使用 `"agent"`
- 收敛（0 blocking）→ `TransitionEvent(story_id=story_id, event_name="review_pass", source="agent", submitted_at=...)`
- 未收敛（有 blocking）→ `TransitionEvent(story_id=story_id, event_name="review_fail", source="agent", submitted_at=...)`
- 事件提交后 TransitionQueue 自动推进状态机：`review_pass` → qa_testing，`review_fail` → fixing

**收敛判定规则（首轮特化）：**
- 0 个 finding → 直接收敛，无需后续轮次
- 仅 suggestion（0 blocking）→ 收敛，suggestion 不阻塞进度
- ≥1 blocking → 不收敛，进入 fix 阶段
- 注意：Story 3.2d 会实现更完整的收敛率计算（`convergence_threshold`），本 story 首轮只需简单的 blocking 有/无判定

### 与已有代码的集成点

| 集成目标 | 文件 | 使用方式 |
|---------|------|---------|
| SubprocessManager.dispatch_with_retry() | `subprocess_mgr.py` | 调度 Codex review agent |
| BmadAdapter.parse() | `adapters/bmad_adapter.py` | 解析 review Markdown → BmadFinding 列表 |
| BmadSkillType.CODE_REVIEW | `models/schemas.py` | skill 类型标识 |
| BmadParseResult.verdict | `models/schemas.py` | 判断 review 结果 |
| record_parse_failure() | `adapters/bmad_adapter.py` | 解析失败时创建人工审批 |
| insert_findings_batch() | `models/db.py` | 批量写入 findings |
| get_open_findings() | `models/db.py` | 查询 open findings（后续 story 用，此处预留） |
| count_findings_by_severity() | `models/db.py` | 统计 blocking/suggestion 数量 |
| maybe_create_blocking_abnormal_approval() | `validation.py` | blocking 阈值检查 |
| compute_dedup_hash() | `models/schemas.py` | 计算 finding 去重 hash |
| TransitionQueue.submit() | `transition_queue.py` | 提交 review_pass/review_fail 事件 |
| TransitionEvent | `models/schemas.py` | 状态转换事件模型 |
| ConvergentLoopConfig | `config.py` | max_rounds、convergence_threshold 配置 |
| CostConfig.blocking_threshold | `config.py` | blocking 异常阈值（默认 10） |
| Nudge | `nudge.py` | 可选通知 |
| validate_artifact() | `validation.py` | Deterministic validation gate（预留位） |
| get_connection() | `models/db.py` | 数据库连接 |
| structlog | — | 结构化日志 |

### 不要做的事情

- **不要实现 fix dispatch**——fix agent 调度是 Story 3.2b
- **不要实现 re-review scope narrowing**——是 Story 3.2c
- **不要实现 max_rounds 终止和 escalation**——是 Story 3.2d
- **不要实现收敛率计算（convergence_threshold）**——是 Story 3.3
- **不要修改 state_machine.py**——reviewing → fixing → reviewing transition 已在 Story 2A.1 定义
- **不要修改 subprocess_mgr.py**——直接使用现有 dispatch_with_retry() API
- **不要修改 bmad_adapter.py**——直接使用现有 parse() API
- **不要实现 Convergent Loop 的多轮循环**——本 story 只实现第 1 轮逻辑，多轮由后续 story 逐步扩展
- **不要在 convergent_loop.py 中导入 core.py**——保持模块隔离，Orchestrator 消费 ConvergentLoop，不反向依赖
- **不要直接构建 review prompt**——构建 review prompt 的具体文案不在本 story 范围，使用简洁占位即可，后续迭代优化

### 新增依赖

无新增依赖。所有需要的库（aiosqlite, structlog, pydantic 等）已在 pyproject.toml 中。

### 文件变更清单

| 操作 | 文件路径 | 说明 |
|------|---------|------|
| MODIFY | `src/ato/convergent_loop.py` | 从空占位变为 ConvergentLoop 核心类实现 |
| MODIFY | `src/ato/models/schemas.py` | +ConvergentLoopResult 模型 |
| CREATE | `tests/unit/test_convergent_loop.py` | ConvergentLoop 首轮 review 单测 |

### 已有代码模式参考

**SubprocessManager dispatch 调用风格**（参照 `subprocess_mgr.py`）：
```python
result = await self._subprocess_mgr.dispatch_with_retry(
    story_id=story_id,
    phase="reviewing",
    role="reviewer",
    cli_tool="codex",
    prompt=review_prompt,
    options={"cwd": worktree_path, "sandbox": "read-only"},
)
```

**BmadAdapter 解析调用风格**（参照 `adapters/bmad_adapter.py`）：
```python
parse_result = await self._bmad_adapter.parse(
    markdown_output=result.text_result,
    skill_type=BmadSkillType.CODE_REVIEW,
    story_id=story_id,
)
if parse_result.verdict == "parse_failed":
    await record_parse_failure(
        parse_result=parse_result,
        story_id=story_id,
        skill_type=BmadSkillType.CODE_REVIEW,
        db=db,
        notifier=self._nudge.notify if self._nudge else None,
    )
```

**BmadFinding → FindingRecord 转换风格**：
```python
import uuid
from datetime import datetime, timezone

records = [
    FindingRecord(
        finding_id=str(uuid.uuid4()),
        story_id=story_id,
        round_num=1,
        severity=f.severity,
        description=f.description,
        status="open",
        file_path=f.file_path,
        rule_id=f.rule_id,
        dedup_hash=f.dedup_hash or compute_dedup_hash(f.file_path, f.rule_id, f.severity, f.description),
        line_number=f.line,
        created_at=datetime.now(timezone.utc),
    )
    for f in parse_result.findings
]
```

**TransitionEvent 提交风格**（参照 `transition_queue.py`）：
```python
from datetime import UTC, datetime

await self._transition_queue.submit(
    TransitionEvent(
        story_id=story_id,
        event_name="review_pass",  # 或 "review_fail"
        source="agent",
        submitted_at=datetime.now(UTC),
    )
)
```

**structlog 使用风格**（参照 `subprocess_mgr.py`）：
```python
logger: structlog.stdlib.BoundLogger = structlog.get_logger()
logger.info(
    "convergent_loop_round_complete",
    story_id=story_id,
    round_num=1,
    findings_total=len(records),
    open_count=len(records),
    blocking_count=blocking_count,
    suggestion_count=suggestion_count,
)
```

### Project Structure Notes

- `src/ato/convergent_loop.py` 已存在为空占位文件，本 story 填充内容
- ConvergentLoop 类与 `subprocess_mgr.py`、`transition_queue.py` 平级，被 `core.py`（Orchestrator）消费
- 测试文件 `tests/unit/test_convergent_loop.py` 为新建
- `ConvergentLoopResult` 模型追加到 `models/schemas.py`（与 FindingRecord、ApprovalRecord 等同级）

### Previous Story Intelligence

**从 Story 3.1（前一个 story，已完成）的关键经验：**
- **FindingRecord 已定义完整**——`finding_id, story_id, round_num, severity, description, status, file_path, rule_id, dedup_hash, line_number, fix_suggestion, created_at`，直接使用
- **findings CRUD 已就绪**——`insert_finding()`, `insert_findings_batch()`, `get_findings_by_story()`, `get_open_findings()`, `update_finding_status()`, `count_findings_by_severity()` 都已实现
- **`maybe_create_blocking_abnormal_approval()` 已实现**——直接调用，传入 `db, story_id, round_num, threshold`
- **`compute_dedup_hash()` 已实现**——SHA256 of `file_path|rule_id|severity|normalize(description)`
- **JSON Schema 验证已实现**——`validate_artifact()` 返回 `ValidationResult(passed, errors)`
- **SCHEMA_VERSION = 5**——findings 表已存在，v4→v5 迁移已实现
- **AC 边界说明**：Story 3.1 明确"不接流程"，本 story（3.2a）是首次将 validation/findings 基础设施接入运行时 convergent loop 协议

**从 Story 2B.2/2B.3（BMAD adapter 相关）的关键经验：**
- BmadAdapter 的 `parse()` 返回 `BmadParseResult`，其 `findings` 是 `list[BmadFinding]`
- `BmadFinding` 已自动计算 `dedup_hash`（via `@model_validator`），可直接使用
- `record_parse_failure()` 是独立函数（非 BmadAdapter 方法），需从 `bmad_adapter` 模块导入

### Git Intelligence

最近 commit 模式：
- `feat: Story 3.1 Deterministic validation + finding 追踪完整实现` (7ba6e7f)——本 story 的直接前置
- Story 2B.3/2B.4 已合并——BMAD adapter 和 worktree isolation 已就绪
- 每个 story 一个 feature commit + 一个 merge commit
- 全部 615 个测试通过

### References

- [Source: _bmad-output/planning-artifacts/epics.md — Epic 3, Story 3.2a]
- [Source: _bmad-output/planning-artifacts/architecture.md — Decision 3: Convergent Loop 质量门控]
- [Source: _bmad-output/planning-artifacts/architecture.md — Decision 6: structlog 结构化日志]
- [Source: _bmad-output/planning-artifacts/architecture.md — Decision 8: 状态机测试覆盖]
- [Source: _bmad-output/planning-artifacts/architecture.md — Decision 9: CLI Adapter 契约守护]
- [Source: _bmad-output/planning-artifacts/prd.md — FR13, FR14, FR16, FR18, NFR4, NFR9]
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md — ConvergentLoopProgress Component]
- [Source: src/ato/convergent_loop.py — 空占位文件]
- [Source: src/ato/subprocess_mgr.py — dispatch_with_retry() API]
- [Source: src/ato/adapters/bmad_adapter.py — BmadAdapter.parse() + record_parse_failure()]
- [Source: src/ato/transition_queue.py — TransitionQueue.submit()]
- [Source: src/ato/validation.py — validate_artifact(), maybe_create_blocking_abnormal_approval()]
- [Source: src/ato/models/schemas.py — FindingRecord, BmadFinding, BmadParseResult, TransitionEvent, AdapterResult]
- [Source: src/ato/models/db.py — insert_findings_batch(), count_findings_by_severity()]
- [Source: src/ato/config.py — ConvergentLoopConfig, CostConfig]
- [Source: _bmad-output/implementation-artifacts/3-1-deterministic-validation-finding-tracking.md — Story 3.1 完成记录]

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6 (1M context)

### Debug Log References

- Python 3.11 不支持 `type` 语句（Python 3.12+），改用 `_BmadAdapter = Any` 赋值
- 全部 20 个新测试通过
- 全套 747 测试零回归

### Completion Notes List

- ✅ Task 2: `ConvergentLoopResult` 模型添加到 `schemas.py`，包含 9 个字段
- ✅ Task 1: `ConvergentLoop` 核心类实现于 `convergent_loop.py`，包含 `run_first_review()` 主流程、`_resolve_worktree_path()` 路径解析、`_run_validation_gate()` 验证门
- ✅ Task 3: Deterministic Validation Gate — 无 artifact_payload 时安全跳过，有时调用 `validate_artifact()` 并在失败时提交 `validate_fail` 事件回退到 creating
- ✅ Task 4: structlog 三个日志点 — `convergent_loop_round_start`、`convergent_loop_round_complete`、`convergent_loop_converged` / `convergent_loop_needs_fix`
- ✅ Task 5: 20 个测试用例覆盖全部场景 + TransitionQueue 交互验证 + 真实状态机集成测试
- ✅ Review R1 [高]: 状态机增加 `reviewing → creating` via `validate_fail` 转换路径，convergent_loop 正确提交 `validate_fail`（而非错误的 `review_fail`），新增真实状态机集成测试验证
- ✅ Review R1 [中]: `blocking_threshold` 改为必传参数（无默认值），强制调用方从 `CostConfig.blocking_threshold` 显式传入，杜绝静默退回硬编码值
- ✅ Review R2 [高]: 修正 R1 的错误方向——`validate_fail` 才是 story 要求的正确事件；给状态机补上 `reviewing.to(creating)` 路径而非偷换事件名
- ✅ Review R2 [中]: `blocking_threshold` 去掉默认值变为 required，测试 helper 显式传入

### Implementation Plan

1. 先定义 `ConvergentLoopResult` 模型（Task 2）作为返回值类型
2. 实现 `ConvergentLoop` 核心类（Task 1），一体化包含 validation gate（Task 3）和 structlog（Task 4）
3. 编写 20 个单元测试覆盖所有 AC（Task 5）
4. 收敛逻辑简化为 blocking 有/无判定，不实现 convergence_threshold 计算（留给 Story 3.2d）

### File List

| 操作 | 文件路径 |
|------|---------|
| MODIFY | `src/ato/convergent_loop.py` |
| MODIFY | `src/ato/models/schemas.py` |
| MODIFY | `src/ato/state_machine.py` |
| CREATE | `tests/unit/test_convergent_loop.py` |
| MODIFY | `_bmad-output/implementation-artifacts/sprint-status.yaml` |
| MODIFY | `_bmad-output/implementation-artifacts/3-2a-convergent-loop-full-review.md` |

### Change Log

- 2026-03-25: Story 3.2a 完整实现 — ConvergentLoop 首轮全量 review（17 测试，744 全套通过）
- 2026-03-25: R1 修复 — validation gate 事件改为 review_fail + blocking_threshold 配置化（19 测试，746 全套通过）
- 2026-03-25: R2 修复 — 状态机补 reviewing→creating via validate_fail + blocking_threshold 改为 required（20 测试，747 全套通过）
