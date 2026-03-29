# Agent Team Orchestrator — API 契约与数据模型参考

> **版本**: 基于 `SCHEMA_VERSION = 8` 的代码库快照  
> **生成日期**: 2026-03-29  
> **目标读者**: AI Agent / 开发者

---

## 1. 异常类层次

```python
ATOError                          # 基类
├── CLIAdapterError               # CLI 调用失败
│   attrs: category, stderr, exit_code, retryable
├── StateTransitionError          # 状态机转换非法
├── RecoveryError                 # 崩溃恢复 / 迁移失败
├── ConfigError                   # 配置解析错误
└── WorktreeError                 # Git worktree 操作失败
    attrs: stderr, story_id
```

### CLIAdapterError 错误分类

```python
class ErrorCategory(StrEnum):
    AUTH_EXPIRED = "auth_expired"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    PARSE_ERROR = "parse_error"
    UNKNOWN = "unknown"
```

---

## 2. Pydantic Record Models

> 所有 Record Model 继承 `_StrictBase`：`strict=True` (禁止隐式类型转换), `extra="forbid"` (拒绝未声明字段)。

### 2.1 StoryRecord

```python
class StoryRecord(_StrictBase):
    story_id: str                  # 唯一标识 (e.g. "2a-1-story-xxx")
    title: str                     # 标题
    status: StoryStatus            # 见状态定义
    current_phase: str             # 当前阶段名
    worktree_path: str | None      # Git Worktree 路径
    has_ui: bool = False           # 是否含 UI 组件
    created_at: datetime
    updated_at: datetime

StoryStatus = Literal[
    "backlog", "planning", "ready", "in_progress",
    "review", "uat", "done", "blocked"
]
```

### 2.2 TaskRecord

```python
class TaskRecord(_StrictBase):
    task_id: str                     # UUID
    story_id: str                    # 所属 Story
    phase: str                       # 阶段名
    role: str                        # 角色 (developer, reviewer, qa ...)
    cli_tool: Literal["claude", "codex"]
    status: TaskStatus               # 见下方
    pid: int | None                  # OS 进程 PID
    expected_artifact: str | None    # 预期产出物路径
    context_briefing: str | None     # JSON 工作记忆
    started_at: datetime | None
    completed_at: datetime | None
    exit_code: int | None
    cost_usd: float | None
    duration_ms: int | None
    error_message: str | None

TaskStatus = Literal["pending", "running", "paused", "completed", "failed"]
```

### 2.3 ApprovalRecord

```python
class ApprovalRecord(_StrictBase):
    approval_id: str                 # UUID
    story_id: str                    # 关联 Story
    approval_type: str               # 见 ApprovalType
    status: ApprovalStatus           # pending / approved / rejected
    payload: str | None              # JSON 序列化的上下文
    decision: str | None             # 具体决策选项
    decided_at: datetime | None
    created_at: datetime
    recommended_action: str | None   # 推荐操作
    risk_level: Literal["high", "medium", "low"] | None
    decision_reason: str | None
    consumed_at: datetime | None     # Orchestrator 消费时间

ApprovalStatus = Literal["pending", "approved", "rejected"]
```

### 2.4 FindingRecord

```python
class FindingRecord(_StrictBase):
    finding_id: str                  # UUID
    story_id: str
    round_num: int                   # 发现轮次
    severity: FindingSeverity        # blocking / suggestion
    description: str
    status: FindingStatus            # open / closed / still_open
    file_path: str
    rule_id: str
    dedup_hash: str                  # SHA256 去重哈希
    line_number: int | None
    fix_suggestion: str | None
    created_at: datetime

FindingSeverity = Literal["blocking", "suggestion"]
FindingStatus = Literal["open", "closed", "still_open"]
```

### 2.5 BatchRecord / BatchStoryLink

```python
class BatchRecord(_StrictBase):
    batch_id: str
    status: BatchStatus              # active / completed / cancelled
    created_at: datetime
    completed_at: datetime | None

class BatchStoryLink(_StrictBase):
    batch_id: str
    story_id: str
    sequence_no: int                 # 执行顺序号
```

### 2.6 MergeQueueEntry / MergeQueueState

```python
class MergeQueueEntry(_StrictBase):
    id: int                          # 自增主键
    story_id: str                    # UNIQUE
    approval_id: str
    approved_at: datetime
    enqueued_at: datetime
    status: MergeQueueStatus
    regression_task_id: str | None
    pre_merge_head: str | None       # revert 支点

MergeQueueStatus = Literal[
    "waiting", "merging", "regression_pending", "merged", "failed"
]

class MergeQueueState(_StrictBase):    # 单例行 (id=1)
    frozen: bool = False
    frozen_reason: str | None = None
    frozen_at: datetime | None = None
    current_merge_story_id: str | None = None  # 串行锁
```

### 2.7 TransitionEvent

```python
class TransitionEvent(_StrictBase):
    story_id: str
    event_name: str                  # 状态机事件名
    source: TransitionSource         # agent / tui / cli
    submitted_at: datetime

TransitionSource = Literal["agent", "tui", "cli"]
```

### 2.8 Adapter 输出模型

```python
class AdapterResult(BaseModel):     # 注意：不继承 _StrictBase（宽松解析）
    model_config = ConfigDict(extra="ignore")
    status: Literal["success", "failure", "timeout"]
    exit_code: int
    duration_ms: int = 0
    text_result: str = ""
    structured_output: dict[str, Any] | None = None
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    session_id: str | None = None
    error_category: str | None = None
    error_message: str | None = None

class ClaudeOutput(AdapterResult):
    cache_read_input_tokens: int = 0
    model_usage: dict[str, Any] | None = None

class CodexOutput(AdapterResult):
    cache_read_input_tokens: int = 0
    model_name: str | None = None
```

### 2.9 Recovery 模型

```python
RecoveryAction = Literal["reattach", "complete", "reschedule", "needs_human"]
RecoveryMode = Literal["crash", "normal", "none"]

class RecoveryClassification(_StrictBase):
    task_id: str
    story_id: str
    action: RecoveryAction
    reason: str

class RecoveryResult(_StrictBase):
    classifications: list[RecoveryClassification]
    auto_recovered_count: int
    dispatched_count: int = 0
    needs_human_count: int
    recovery_mode: RecoveryMode
```

### 2.10 BMAD 解析模型

```python
ParserMode = Literal["deterministic", "semantic_fallback", "failed"]
ParseVerdict = Literal["approved", "changes_requested", "parse_failed"]

class BmadFinding(_StrictBase):
    severity: FindingSeverity
    category: str
    description: str
    file_path: str
    line: int | None = None
    rule_id: str
    raw_location: str | None = None
    dedup_hash: str | None = None    # 自动计算

class BmadParseResult(_StrictBase):
    skill_type: BmadSkillType
    verdict: ParseVerdict
    findings: list[BmadFinding]
    parser_mode: ParserMode
    raw_markdown_hash: str
    raw_output_preview: str
    parse_error: str | None = None
    parsed_at: datetime
```

### 2.11 其他模型

```python
class ConvergentLoopResult(_StrictBase):
    story_id: str
    round_num: int
    converged: bool
    findings_total: int
    blocking_count: int
    suggestion_count: int
    open_count: int
    closed_count: int = 0
    new_count: int = 0

class ContextBriefing(_StrictBase):
    story_id: str
    phase: str
    task_type: str
    artifacts_produced: list[str]
    key_decisions: list[str]
    agent_notes: str
    created_at: datetime

class CheckResult(_StrictBase):
    layer: CheckLayer                # system / project / artifact
    check_item: str
    status: CheckStatus              # PASS / HALT / WARN / INFO
    message: str

class CostLogRecord(_StrictBase):
    cost_log_id: str
    story_id: str
    task_id: str | None
    cli_tool: Literal["claude", "codex"]
    model: str | None
    phase: str
    role: str | None
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int = 0
    cost_usd: float
    duration_ms: int | None
    session_id: str | None
    exit_code: int | None
    error_category: str | None
    created_at: datetime
```

---

## 3. Approval 类型完整列表

| 类型 | 图标 | 通知级别 | 推荐操作 | 合法选项 |
|------|------|----------|----------|----------|
| `merge_authorization` | 🔀 | normal | approve | approve, reject |
| `session_timeout` | ⏱ | normal | restart | restart, resume, abandon |
| `crash_recovery` | ↩ | normal | restart | restart, resume, abandon |
| `blocking_abnormal` | ⚠ | normal | human_review | confirm_fix, human_review |
| `budget_exceeded` | 💰 | normal | increase_budget | increase_budget, reject |
| `regression_failure` | ✖ | urgent | fix_forward | revert, fix_forward, pause |
| `convergent_loop_escalation` | 🔄 | normal | escalate | retry, skip, escalate |
| `batch_confirmation` | 📦 | normal | confirm | confirm, reject |
| `timeout` | ⏳ | normal | continue_waiting | continue_waiting, abandon |
| `precommit_failure` | 🔧 | normal | retry | retry, manual_fix, skip |
| `rebase_conflict` | ⚡ | normal | manual_resolve | manual_resolve, skip, abandon |
| `needs_human_review` | 👁 | normal | retry | retry, skip, escalate |

---

## 4. 核心函数接口

### 4.1 数据库 CRUD

#### Stories

```python
async def insert_story(db, story: StoryRecord) -> None
async def get_story(db, story_id: str) -> StoryRecord | None
async def update_story_status(
    db, story_id: str, status: str, phase: str, *, commit: bool = True
) -> None
async def update_story_worktree_path(
    db, story_id: str, worktree_path: str | None
) -> None
```

#### Tasks

```python
async def insert_task(db, task: TaskRecord) -> None
async def get_tasks_by_story(db, story_id: str) -> list[TaskRecord]
async def update_task_status(
    db, task_id: str, status: str, **kwargs  # pid, exit_code, cost_usd, ...
) -> None
async def get_tasks_by_status(db, status: str) -> list[TaskRecord]
async def get_running_tasks(db) -> list[TaskRecord]
async def get_paused_tasks(db) -> list[TaskRecord]
async def mark_running_tasks_paused(db) -> int          # 不自动 commit
async def count_tasks_by_status(db, status: str) -> int
```

#### Approvals

```python
async def insert_approval(db, approval: ApprovalRecord, *, commit: bool = True) -> None
async def get_pending_approvals(db) -> list[ApprovalRecord]
async def get_approval_by_id(db, approval_id_prefix: str) -> ApprovalRecord
async def update_approval_decision(
    db, approval_id: str, *, status: str, decision: str,
    decision_reason: str | None, decided_at: datetime
) -> None
async def get_decided_unconsumed_approvals(db) -> list[ApprovalRecord]
async def mark_approval_consumed(db, approval_id: str, consumed_at: datetime) -> None
```

#### Batches

```python
async def insert_batch(db, batch: BatchRecord) -> None
async def insert_batch_story_links(db, links: list[BatchStoryLink]) -> None
async def get_active_batch(db) -> BatchRecord | None
async def get_batch_stories(db, batch_id) -> list[tuple[BatchStoryLink, StoryRecord]]
async def get_batch_progress(db, batch_id) -> BatchProgress
```

#### Merge Queue

```python
async def enqueue_merge(db, story_id, approval_id, approved_at, enqueued_at) -> None
async def dequeue_next_merge(db) -> MergeQueueEntry | None
async def complete_merge(db, story_id, success: bool) -> None
async def get_merge_queue_state(db) -> MergeQueueState
async def set_merge_queue_frozen(db, frozen: bool, reason: str | None) -> None
async def set_current_merge_story(db, story_id: str | None) -> None
```

#### Findings

```python
async def insert_findings_batch(db, findings: list[FindingRecord]) -> None
async def get_findings_by_story(db, story_id: str) -> list[FindingRecord]
async def get_open_findings(db, story_id: str) -> list[FindingRecord]
async def update_finding_status(db, finding_id: str, status: str) -> None
```

### 4.2 Adapter 接口

```python
class BaseAdapter(ABC):
    @abstractmethod
    async def execute(
        self,
        prompt: str,
        options: dict[str, Any] | None = None,
        *,
        on_process_start: ProcessStartCallback | None = None,
    ) -> AdapterResult: ...

# 辅助函数
async def cleanup_process(proc: Process, timeout: int = 5) -> None
    """三阶段清理：SIGTERM → wait(timeout) → SIGKILL → wait"""
```

### 4.3 TransitionQueue

```python
class TransitionQueue:
    def __init__(self, db_path: Path, nudge: Nudge | None = None)
    async def start(self) -> None          # 启动后台消费协程
    async def stop(self) -> None           # 优雅停止
    async def submit(self, event: TransitionEvent) -> None  # 提交事件
```

### 4.4 SubprocessManager

```python
class SubprocessManager:
    def __init__(self, max_concurrent: int, adapter: BaseAdapter, db_path: Path)

    async def dispatch_with_retry(
        self,
        *,
        story_id: str,
        phase: str,
        role: str,
        cli_tool: str,
        prompt: str,
        options: dict | None = None,
        task_id: str | None = None,
        is_retry: bool = False,
    ) -> AdapterResult
```

### 4.5 Approval Helpers

```python
async def create_approval(
    db: Connection,
    *,
    story_id: str,
    approval_type: str,
    payload_dict: dict | None = None,
    nudge: Nudge | None = None,
) -> ApprovalRecord
```

### 4.6 Preflight

```python
async def run_preflight(
    project_path: Path,
    db_path: Path,
    include_auth: bool = True,
) -> list[CheckResult]
```

### 4.7 Convergent Loop

```python
class ConvergentLoop:
    async def run_loop(
        self, story_id: str, worktree_path: str | None = None,
        *, artifact_payload: dict | None = None,
    ) -> ConvergentLoopResult

    async def run_first_review(
        self, story_id: str, worktree_path: str | None = None,
        *, artifact_payload: dict | None = None,
        task_id: str | None = None, is_retry: bool = False,
    ) -> ConvergentLoopResult

    async def run_fix_dispatch(
        self, story_id: str, round_num: int, worktree_path: str | None = None,
    ) -> ConvergentLoopResult

    async def run_rereview(
        self, story_id: str, round_num: int, worktree_path: str | None = None,
        *, task_id: str | None = None, is_retry: bool = False,
    ) -> ConvergentLoopResult
```

### 4.8 MergeQueue

```python
class MergeQueue:
    async def recover_stale_lock(self) -> None
    async def enqueue(self, story_id: str, approval_id: str, approved_at: datetime) -> None
    async def process_next(self) -> bool
    async def check_regression_completion(self) -> None
```

### 4.9 WorktreeManager

```python
class WorktreeManager:
    async def create(self, story_id: str, base_ref: str) -> Path
    async def cleanup(self, story_id: str) -> None
    async def rebase_onto_main(self, story_id: str, timeout_seconds: int) -> tuple[bool, str]
    async def merge_to_main(self, story_id: str) -> tuple[bool, str]
    async def get_conflict_files(self, story_id: str) -> list[str]
    async def abort_rebase(self, story_id: str) -> None
    async def get_main_head(self) -> str | None
```

### 4.10 Design Artifacts

```python
def derive_design_artifact_paths(story_id: str, project_root: Path) -> dict[str, Path]
def force_persist_pen(pen_path: Path, memory_children: list[dict]) -> PenPersistResult
def write_design_snapshot(snapshot_path: Path, memory_tree: dict) -> Path
def write_save_report(report_path: Path, *, story_id, ...) -> Path
def verify_pen_integrity(pen_path: Path) -> PenVerifyResult
def verify_snapshot(snapshot_path: Path) -> bool
def verify_save_report(report_path: Path) -> bool
def write_prototype_manifest(story_id: str, project_root: Path) -> Path
def build_ux_context_from_manifest(story_id: str, project_root: Path) -> str
```

### 4.11 Validation

```python
def validate_artifact(payload: dict, schema_name: str) -> ValidationResult
    """JSON Schema 确定性验证。"""

async def maybe_create_blocking_abnormal_approval(
    db, story_id: str, round_num: int,
    threshold: int, nudge: Nudge | None = None,
) -> None
    """blocking findings ≥ threshold 时创建 blocking_abnormal approval。"""
```

### 4.12 Recovery Engine

```python
class RecoveryEngine:
    async def scan_running_tasks(self) -> list[TaskRecord]
    def classify_task(self, task: TaskRecord) -> RecoveryClassification
    async def run_recovery(self) -> RecoveryResult
    async def await_background_tasks(self) -> None
```

---

## 5. 状态机事件完整列表

| 事件名 | 源状态 → 目标状态 |
|--------|-------------------|
| `batch_start` | backlog → queued |
| `dispatch` | queued → planning |
| `plan_done` | planning → creating |
| `create_done` | creating → designing |
| `design_done` | designing → validating |
| `validate_pass` | validating → dev_ready |
| `validate_fail` | validating → creating |
| `start_dev` | dev_ready → developing |
| `dev_done` | developing → reviewing |
| `review_pass` | reviewing → qa_testing |
| `review_fail` | reviewing → fixing |
| `fix_done` | fixing → reviewing |
| `qa_pass` | qa_testing → uat |
| `qa_fail` | qa_testing → fixing |
| `uat_pass` | uat → merging |
| `uat_fail` | uat → fixing |
| `merge_done` | merging → regression |
| `regression_pass` | regression → done |
| `regression_fail` | regression → fixing |
| `block` | any → blocked |
| `unblock` | blocked → 之前的状态 |

---

## 6. CLI 命令列表

| 命令 | 描述 | 退出码 |
|------|------|--------|
| `ato init [path]` | 初始化项目，执行 Preflight | 0/1/2 |
| `ato start` | 启动 Orchestrator | 0/1/2 |
| `ato stop` | 优雅停止 Orchestrator | 0/1 |
| `ato plan <story-id>` | 预览阶段序列 | 0/1 |
| `ato batch select` | 选择 Story Batch | 0/1/2 |
| `ato batch status` | 查看 Batch 进度 | 0/1/2 |
| `ato approve <id> <decision>` | 提交审批决策 | 0/1 |
| `ato submit <story-id>` | 标记 interactive task 完成 | 0/1 |
| `ato uat <story-id> --result pass/fail` | UAT 结果提交 | 0/1 |
| `ato tui` | 启动 TUI Dashboard | 0 |

退出码约定：
- `0` (EXIT_SUCCESS): 成功
- `1` (EXIT_ERROR): 一般错误
- `2` (EXIT_ENV_ERROR): 环境/配置错误

---

## 7. Finding 去重算法

```python
def compute_dedup_hash(file_path, rule_id, severity, description) -> str:
    """
    1. 正则化 description: 空白压缩 + strip + lower
    2. SHA256("{file_path}|{rule_id}|{severity}|{normalized_desc}")
    """
```

跨轮次匹配规则：
- 同 `dedup_hash` → `still_open` / `closed`
- 新 `dedup_hash` → 新 `open` finding
- `closed / total` = convergence_rate

---

## 8. Design Gate V2 校验清单

| 序号 | 检查项 | failure_code |
|------|--------|--------------|
| 1 | Story spec 存在 | `STORY_SPEC_MISSING` |
| 2 | `ux-spec.md` 存在 | `UX_SPEC_MISSING` |
| 3 | `prototype.pen` 存在且 JSON 合法含 version+children | `PEN_MISSING` / `PEN_INVALID_JSON` / `PEN_MISSING_KEYS` |
| 4 | `prototype.snapshot.json` 存在且合法 | `SNAPSHOT_MISSING` / `SNAPSHOT_INVALID` |
| 5 | `prototype.save-report.json` 含必需键且验证通过 | `SAVE_REPORT_MISSING` / `SAVE_REPORT_INVALID_JSON` / `SAVE_REPORT_MISSING_KEYS` / `SAVE_REPORT_VERIFICATION_FAILED` |
| 6 | `exports/*.png` ≥ 1 | `EXPORTS_PNG_MISSING` |
| 7 | `prototype.manifest.yaml` 存在且路径有效 | `MANIFEST_MISSING` / `MANIFEST_INVALID` / `MANIFEST_STORY_ID_MISMATCH` / `MANIFEST_PATHS_MISSING` |
