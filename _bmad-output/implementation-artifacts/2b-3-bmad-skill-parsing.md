# Story 2B.3: BMAD Skill Markdown 输出解析为结构化 JSON

Status: ready-for-dev

## Story

As a 操作者,
I want 看到 BMAD skill 的 Markdown 输出被可靠地解析为结构化 JSON,
So that 质量门控和审查流程可以消费结构化数据。

## Acceptance Criteria (BDD)

**AC1: 真实 BMAD/TEA 输出被归一化为 canonical JSON**
**Given** 当前仓库中实际存在的 BMAD/TEA 输出形态（`code-review`、`story-validation`、`architecture-review`、`qa-report`）
**When** 调用 `await BmadAdapter.parse(markdown_output, *, skill_type, story_id, parser_context)`
**Then** 返回 `BmadParseResult`，并经 Pydantic `model_validate()` 验证
**And** 每条 finding 被归一化为稳定字段：`severity`、`category`、`description`、`file_path`、`line`、`rule_id`
**And** 结果明确标记 `parser_mode`（`deterministic` 或 `semantic_fallback`）

**AC2: 基于真实样本的批量解析成功率 ≥ 95%**
**Given** `tests/fixtures/` 中 20 个派生自当前仓库模板/样例的 BMAD/TEA 输出样本
**When** 批量执行解析
**Then** 总体成功率 ≥ 95%（至少 19/20 成功解析为结构化 JSON）
**And** 测试同时覆盖 deterministic fast-path 与 semantic fallback

**AC3: 双阶段失败的 graceful 降级**
**Given** deterministic extraction 与 semantic fallback 都失败
**When** 无法把 Markdown 可靠归一化为结构化 finding 集合
**Then** 返回 `BmadParseResult(verdict="parse_failed", parser_mode="failed", findings=[])`
**And** 由 caller / helper 创建 `needs_human_review` approval 记录并通过注入的 notifier 通知 Orchestrator
**And** 操作者可在审批队列中看到该请求并决定后续操作
**And** structlog 记录 `story_id`、`skill_type`、失败原因和原始输出摘要（截断至 500 字符）

## Tasks / Subtasks

- [ ] Task 1: 定义 BMAD 解析相关 Pydantic 模型 (AC: #1)
  - [ ] 1.1 `BmadSkillType` 枚举（`code_review`, `story_validation`, `architecture_review`, `qa_report`），并支持 workflow 名称/别名归一化
  - [ ] 1.2 `BmadFinding` 模型（`severity`, `category`, `description`, `file_path`, `line`, `rule_id`, `raw_location`）
  - [ ] 1.3 `BmadParseResult` 模型（`skill_type`, `verdict`, `findings`, `parser_mode`, `raw_markdown_hash`, `raw_output_preview`, `parse_error`, `parsed_at`）
  - [ ] 1.4 `compute_dedup_hash(file_path, rule_id, severity, description)` 辅助函数，算法与 Story 3.1 保持一致
- [ ] Task 2: 实现 `BmadAdapter.parse()` 核心解析逻辑 (AC: #1, #2)
  - [ ] 2.1 实现 `async BmadAdapter.parse()`，保持 parsing core 纯函数化，并通过依赖注入接入 semantic parser runner
  - [ ] 2.2 实现 deterministic fast-path：支持当前仓库真实输出族群
    - code-review 最终呈现格式（`Intent Gaps` / `Bad Spec` / `Patch` / `Defer`）
    - story-validation 报告格式（摘要 / 关键问题 / 已应用修正 / 最终结论）
    - architecture validation 摘要格式（`Overall Status` / `Key Strengths` / `Areas for Future Enhancement`）
    - TEA QA / test-review 报告格式（`Recommendation` / `Critical Issues` / `Recommendations`）
    - JSON array fast-path（Edge Case Hunter / 子评审 JSON）
  - [ ] 2.3 实现 canonical normalization：抽取/推导 `severity`、`rule_id`、`file_path`、`line`，缺失位置时使用 `file_path="N/A"`、`line=None`
  - [ ] 2.4 当 deterministic 结果不完整或无法可靠归一化时，调用已有 structured-output 能力的 semantic fallback（优先复用现有 CLI adapter；测试中通过 fake runner/mock 注入）
  - [ ] 2.5 对最终结果执行 `BmadParseResult.model_validate()`
- [ ] Task 3: 实现解析失败降级路径 (AC: #3)
  - [ ] 3.1 实现 `record_parse_failure(...)`（或等价 helper），由它而不是纯 `parse()` 逻辑负责写入 `ApprovalRecord`
  - [ ] 3.2 helper 接受注入 notifier（同进程 `Nudge.notify()`、跨进程 `send_external_nudge()` 或普通 callback），禁止在 parser core 中硬编码信号发送
  - [ ] 3.3 记录 structlog 事件：`story_id`、`skill_type`、`parser_mode`、失败原因、预览摘要
  - [ ] 3.4 返回标记失败的 `BmadParseResult`（`findings=[]`, `verdict="parse_failed"`）
- [ ] Task 4: 创建 20 个 fixture 样本文件 (AC: #2)
  - [ ] 4.1 每种 skill_type 至少 5 个样本，样本必须基于当前仓库中的真实模板/报告结构，而非自造简化标题
  - [ ] 4.2 fixtures 命名：`bmad_{skill_type}_{nn}.md`
  - [ ] 4.3 对应期望输出 JSON：`bmad_{skill_type}_{nn}_expected.json`
  - [ ] 4.4 至少覆盖 1 个 malformed / partial 格式样本，用于触发 semantic fallback 或 graceful degrade
- [ ] Task 5: 编写测试 (AC: #1, #2, #3)
  - [ ] 5.1 `tests/unit/test_bmad_adapter.py`：fixture 批量解析测试（参数化 20 个样本）
  - [ ] 5.2 deterministic fast-path 单测：四类 skill 输出 + JSON array fast-path
  - [ ] 5.3 semantic fallback 单测：mock / fake parser runner 返回结构化输出并通过 schema 验证
  - [ ] 5.4 失败路径测试：seed story 后创建 approval、触发注入 notifier、校验 structlog 预览截断
  - [ ] 5.5 `tests/unit/test_schemas.py`：`BmadFinding` / `BmadParseResult` / dedup hash 一致性测试
  - [ ] 5.6 边界情况：空输入、纯文本、clean review、缺失 location、未知标题

## Dev Notes

### 核心设计决策

**BMAD 适配层不是 `BaseAdapter` 子类，但它也不是纯 regex 工具。** `BmadAdapter` 负责把已产生的 Markdown / text artifact 归一化为 canonical JSON；它不直接实现 CLI 进程生命周期，因此不继承 `BaseAdapter`。但为了满足 Architecture/PRD 中对鲁棒性的要求，它应支持：
- deterministic fast-path（零额外调用，处理已知稳定结构）
- semantic fallback（通过注入的 parser runner 复用现有 structured-output 能力）

推荐调用链：
`ClaudeAdapter.execute()` / `CodexAdapter.execute()` → `AdapterResult.text_result` → `await BmadAdapter.parse(...)` → `BmadParseResult`

**Approval / nudge 是 orchestration concern，不要塞进纯 parser core。**
- `parse()` 本身应尽量保持纯逻辑：输入 Markdown，输出 `BmadParseResult`
- approval 写入、外部通知应由 `record_parse_failure(...)` 或 caller 完成
- 这是当前代码库的一致边界：adapter 负责解析，SQLite / 通知由更高层协调

### 当前仓库中应支持的真实输出族群

**1. `code-review` 最终输出**（`_bmad/bmm/workflows/4-implementation/bmad-code-review/steps/step-04-present.md`）
- 常见结构是 `Intent Gaps` / `Bad Spec` / `Patch` / `Defer` 分组，而不是统一 verdict 表格
- 部分 finding 可能有 location，部分只有标题 + detail
- 中间层子评审（如 Edge Case Hunter）可能是 JSON array；支持它作为 fast-path 有助于未来兼容

**2. `story-validation` 输出**
- 当前仓库已有真实样例：`_bmad-output/implementation-artifacts/2b-2-codex-agent-review-validation-report.md`
- 结构包含元信息行（验证时间 / 模式 / 结果）以及 `摘要`、`发现的关键问题`、`已应用增强`、`最终结论` 等章节

**3. `architecture-review` / architecture validation 输出**
- 参考 `_bmad/bmm/workflows/3-solutioning/bmad-create-architecture/steps/step-07-validation.md`
- 结构围绕 `Overall Status`、`Confidence Level`、`Key Strengths`、`Areas for Future Enhancement`

**4. `qa-report` / TEA test-review 输出**
- 参考 `_bmad/tea/workflows/testarch/bmad-testarch-test-review/test-review-template.md`
- 结构包含 `Recommendation`、`Critical Issues (Must Fix)`、`Recommendations (Should Fix)`、表格化 criteria 结果

### 解析与归一化策略

**推荐解析顺序：**
1. JSON fast-path：若输出本身是 JSON object / JSON array，直接走结构化提取
2. section-aware parser：识别 code-review / story-validation / architecture / QA 的真实 heading 家族
3. table/list parser：处理 criteria 表、bullet list、numbered list
4. semantic fallback：当 deterministic 结果无法稳定产出 canonical fields 时，调用 structured-output parser runner
5. 全部失败：返回 `parse_failed`，交由 helper / caller 创建 approval

**semantic fallback 的实现边界：**
- 优先复用当前已存在的 structured-output 能力，而非重新发明新的 subprocess 协议
- 推荐通过注入 callable / protocol 接口解耦，例如：
  - 生产：包装 `ClaudeAdapter.execute(... structured_output=...)`
  - 测试：fake runner / mock runner
- 不要在 `bmad_adapter.py` 中直接硬编码 `SubprocessManager` 依赖，避免循环耦合和 DB 写入副作用

### Finding 去重哈希

```python
def compute_dedup_hash(file_path: str, rule_id: str, severity: str, description: str) -> str:
    """SHA256(file_path + '|' + rule_id + '|' + severity + '|' + normalize(description))"""
    normalized = " ".join(description.strip().lower().split())
    raw = f"{file_path}|{rule_id}|{severity}|{normalized}"
    return hashlib.sha256(raw.encode()).hexdigest()
```

此算法必须与 Story 3.1 中的 finding 匹配键保持一致。不要额外移除标点，否则后续 round matching 规则会漂移。

### Severity / rule_id / location 归一化规则

**severity 归一化：**
- `blocking`：会阻止继续推进的 finding，例如：
  - code-review 的 `Intent Gaps` / `Bad Spec` / `Patch`
  - story-validation 的关键问题 / INVALID / FAIL
  - architecture validation 的 NOT READY / blocking concern
  - QA 报告中的 `Critical Issues`, `Request Changes`, `Block`
- `suggestion`：不阻塞当前推进的 finding，例如：
  - `Defer`
  - `Areas for Future Enhancement`
  - `Recommendations (Should Fix)`
  - `Approve with Comments`

**rule_id 生成：**
- 不要直接使用自由文本 description 作为 rule_id
- 优先从稳定来源推导：section 名、criterion 名、workflow bucket、报告类别
- 推荐样式：
  - `code_review.patch`
  - `story_validation.critical_issue`
  - `architecture.future_enhancement`
  - `qa.hard_waits`

**location 归一化：**
- finding model 必须有独立 `file_path` 字段，不能只存 `location` 字符串
- 若文本中出现 `path:line`，解析为 `file_path` + `line`
- 若只有模糊位置（例如某个 criterion / 某段摘要），保存到 `raw_location`，同时设置 `file_path="N/A"`、`line=None`

### Approval 记录写入（解析失败时）

使用已有的 `approvals` 表和 `insert_approval()` 函数，但请保持边界清晰：这是 helper / caller 的职责，不是纯 `parse()` 的职责。

**重要约束：**
- `approvals.story_id` 有外键约束，测试必须先 seed 对应 `stories` 行
- 若当前上下文在同一事件循环/进程内，优先注入 `Nudge.notify`
- 若调用方是外部进程，再使用 `send_external_nudge(orchestrator_pid)`

```python
from ato.models.schemas import ApprovalRecord
from ato.models.db import insert_approval
from ato.nudge import send_external_nudge

approval = ApprovalRecord(
    approval_id=str(uuid4()),
    story_id=story_id,
    approval_type="needs_human_review",
    status="pending",
    payload=json.dumps({
        "reason": "bmad_parse_failed",
        "skill_type": skill_type,
        "parser_mode": parse_result.parser_mode,
        "error": parse_result.parse_error,
        "raw_output_preview": parse_result.raw_output_preview,
    }),
    created_at=datetime.now(UTC),
)
await insert_approval(db, approval)
# same-process: notifier()
# cross-process: send_external_nudge(orchestrator_pid)
```

### 现有代码复用清单

| 已有模块 | 复用点 | 路径 |
|---------|--------|------|
| `ApprovalRecord` | 解析失败写 approval | `src/ato/models/schemas.py:168` |
| `insert_approval()` | 插入 approval 记录 | `src/ato/models/db.py:419` |
| `AdapterResult.text_result` | 输入来源 | `src/ato/models/schemas.py` |
| `ClaudeAdapter` | semantic fallback 的 structured-output 能力（推荐复用） | `src/ato/adapters/claude_cli.py` |
| `Nudge` / `send_external_nudge()` | 失败后的 Orchestrator 通知 | `src/ato/nudge.py` |
| structlog | 结构化日志 | 全局 `structlog.get_logger()` |

**不要重复创建：**
- approvals 表已存在（Story 1.2 创建），不需要 DDL 或 migration
- `findings` 表与 `schemas/*.json` 仍属于 Story 3.1，不在本 story 范围
- 不要在 `bmad_adapter.py` 中直接写 `SubprocessManager` 或 cost_log 逻辑

### 文件结构

```
src/ato/adapters/bmad_adapter.py                 # 主实现（替换现有占位文件）
src/ato/models/schemas.py                        # 新增 BmadSkillType, BmadFinding, BmadParseResult
tests/unit/test_bmad_adapter.py                  # BmadAdapter 与 failure helper 测试
tests/unit/test_schemas.py                       # 新模型与 dedup hash 测试
tests/fixtures/bmad_code_review_01.md            # 真实结构 fixture（20 个）
tests/fixtures/bmad_code_review_01_expected.json
tests/fixtures/bmad_story_validation_01.md
tests/fixtures/bmad_story_validation_01_expected.json
...（每种 skill_type 5 个样本 × 2 文件 = 40 个 fixture 文件）
```

**不要创建的文件：**
- 不需要 `schemas/` 目录的 JSON Schema 文件（那是 Story 3.1 的工作）
- 不需要 `findings` 表 DDL（那是 Story 3.1 的工作）
- 不需要 SCHEMA_VERSION 变更（本 story 不新增表）
- 不需要 migration 文件

### 依赖与约束

- **不需要新增任何 pip 依赖。** 只使用标准库 + 已有依赖（pydantic, structlog, 已有 CLI adapter）
- **SCHEMA_VERSION 保持 4** — 本 story 不新增数据库表，不需要 migration
- **不要在纯 parser 中硬编码外部通知路径。** notifier 必须通过参数/依赖注入
- **不要在 parser core 里直接操作 SQLite。** approval 写入走 helper / caller
- Python ≥ 3.11，使用 `StrEnum`、`match` 等现代语法

### 测试策略

遵循项目已建立的 fixture + mock 模式：

1. **Fixture 参数化测试**：`@pytest.mark.parametrize` 遍历 20 个 fixture 文件
2. **成功路径**：验证 canonical fields + `model_validate()` + `parser_mode`
3. **semantic fallback**：通过 fake runner / mock 返回结构化 JSON，避免真实 CLI 调用
4. **失败路径**：先 seed story，再验证 approval 创建、notifier 调用和 structlog 输出
5. **dedup_hash**：相同输入产生相同哈希、不同输入产生不同哈希；normalize 只做空白压缩 + strip + lower
6. **边界情况**：空字符串、纯文本无 Markdown 结构、clean review、缺失 location

测试文件命名：
- `tests/unit/test_bmad_adapter.py`
- `tests/unit/test_schemas.py`

pytest-asyncio auto mode（与项目现有测试一致）

### Project Structure Notes

- 所有适配器位于 `src/ato/adapters/` — `bmad_adapter.py` 已有占位文件
- Pydantic 模型集中在 `src/ato/models/schemas.py` — 新模型追加到文件末尾
- Fixture 文件统一放在 `tests/fixtures/` — 与 Claude/Codex fixture 同级
- 测试统一在 `tests/unit/`
- `schemas/` 目录当前只有 `.gitkeep`，本 story 不负责填充正式 schema 工件

### 前序 Story 关键学习

**来自 Story 2B.1（Claude Adapter）：**
- AdapterResult 使用 `extra="ignore"` 处理外部 JSON 灵活性
- 内部模型使用 `_StrictBase`（extra="forbid", strict=True）
- structured-output 能力已存在，可作为 semantic fallback 的首选复用点

**来自 Story 2B.2（Codex Adapter）：**
- JSONL 解析必须容错：空行跳过，非 JSON 行 structlog 警告后跳过
- Fixture 测试是验证解析正确性的最有效方式
- 新旧格式兼容（codex `item.text` vs `item.content[].text`）— 启示：BMAD 解析也需多 path fallback
- adapter 不应直接承担 SQLite 持久化职责

**来自 Story 2B.5（Batch Select）：**
- `_StrictBase` 用于所有内部记录模型
- Transaction boundary 保证原子性
- Protocol / dependency injection 适合隔离 parser runner 与 notifier

### References

- [Source: _bmad-output/planning-artifacts/epics.md — Story 2B.3 定义, lines 651-671]
- [Source: _bmad-output/planning-artifacts/architecture.md — BMAD 适配层设计, NFR11/NFR12, finding 匹配键]
- [Source: _bmad-output/planning-artifacts/prd.md — FR11 BMAD Markdown→JSON 解析]
- [Source: _bmad-output/planning-artifacts/prd.md — FR18 Severity 分类规则]
- [Source: _bmad/bmm/workflows/4-implementation/bmad-code-review/steps/step-04-present.md — code-review 最终输出结构]
- [Source: _bmad-output/implementation-artifacts/2b-2-codex-agent-review-validation-report.md — story-validation 真实样例]
- [Source: _bmad/bmm/workflows/3-solutioning/bmad-create-architecture/steps/step-07-validation.md — architecture validation 输出结构]
- [Source: _bmad/tea/workflows/testarch/bmad-testarch-test-review/test-review-template.md — QA / test-review 输出结构]
- [Source: src/ato/adapters/claude_cli.py — structured-output 能力复用]
- [Source: src/ato/models/schemas.py — ApprovalRecord, AdapterResult, `_StrictBase`]
- [Source: src/ato/models/db.py — insert_approval(), approvals 表 DDL]
- [Source: src/ato/nudge.py — `Nudge.notify()` / `send_external_nudge()`]

## Dev Agent Record

### Agent Model Used

（dev 阶段填写）

### Debug Log References

### Completion Notes List

### File List
