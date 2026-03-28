# Story 9.1b: Designing 阶段强制落盘与设计快照链路

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->
<!-- Depends on: Story 9.1a -->

## Story

As a 操作者,
I want designing 阶段在 Pencil 内存编辑完成后执行结构化强制落盘，并生成快照与保存报告,
so that `.pen` 设计稿真正存在于磁盘上，系统崩溃后仍可恢复，后续 gate 有可靠真相源。

## Acceptance Criteria

### AC1: Designing 成功路径执行“抓树 → 回写”强制落盘链

```gherkin
Given `designing` 阶段已经通过 Pencil MCP 完成内存态设计
When 系统进入设计保存步骤
Then 系统调用 `batch_get(filePath=..., readDepth=99, includePathGeometry=true)` 抓取完整内存节点树
And Python 读取磁盘上的 `prototype.pen`
And 在保留顶层字段（至少 `version`、`variables`）的前提下，用内存态 `children` 替换磁盘态 `children`
And 以原子写入方式回写 `prototype.pen`
```

### AC2: 结构化回写不破坏 `.pen` 顶层合同

```gherkin
Given 现有 `prototype.pen` 顶层可能含 `version`、`variables` 以及未来扩展字段
When 执行强制落盘
Then 系统不会用 `batch_get` 结果粗暴覆盖整个文件
And 会保留未知顶层字段
And 写回后的 `.pen` 仍可被 `json.load` 成功解析
```

### AC3: 系统生成设计快照与保存报告

```gherkin
Given 强制落盘完成
When 系统写入设计工件
Then 额外生成：
  - `prototype.snapshot.json`（全量结构化快照）
  - `prototype.save-report.json`（保存证明）
And `prototype.save-report.json` 至少记录：
  - `story_id`
  - `saved_at`
  - `pen_file`
  - `snapshot_file`
  - `children_count`
  - `json_parse_verified`
  - `reopen_verified`
  - `exported_png_count`（可先为 0，后续导出后更新）
```

### AC4: 保存后执行回读验证

```gherkin
Given 磁盘上的 `prototype.pen` 已写回
When 系统执行保存校验
Then 至少完成两类验证：
  - 本地 `json.load` 成功
  - 再次通过 Pencil MCP `batch_get(filePath=...)` 或等价方式重新打开并读取成功
And 任一验证失败都视为保存失败，不允许进入下一阶段
```

### AC5: 原子写入与失败恢复可测试

```gherkin
Given 强制落盘过程中任何一步可能失败
When 写入失败、JSON 不合法、回读失败或路径不存在
Then 系统不会留下半写入损坏的 `prototype.pen`
And 会把失败原因写入 `prototype.save-report.json` 或结构化异常
And 单元测试覆盖原子写入、回读失败和字段保留三类场景
```

## Tasks / Subtasks

- [x] Task 1: 实现 `.pen` 结构化回写 helper (AC: #1, #2)
  - [x] 1.1 在 `src/ato/design_artifacts.py` 中实现 `.pen` 读取与回写 helper
  - [x] 1.2 实现”保留顶层字段，只替换 `children`”逻辑
  - [x] 1.3 使用临时文件 + rename 的原子写入策略

- [x] Task 2: 实现 snapshot / save-report 生成 (AC: #3)
  - [x] 2.1 写入 `prototype.snapshot.json`
  - [x] 2.2 写入 `prototype.save-report.json`
  - [x] 2.3 约定 save-report 字段结构

- [x] Task 3: 集成 designing 保存后校验 (AC: #4)
  - [x] 3.1 在 designing 流程中接入 `batch_get(readDepth=99, includePathGeometry=true)`
  - [x] 3.2 接入 JSON parse 验证
  - [x] 3.3 接入 MCP reopen / 回读验证

- [x] Task 4: 测试失败恢复与字段保留 (AC: #5)
  - [x] 4.1 扩展 `tests/unit/test_design_artifacts.py`（由 Story 9.1a 创建）
  - [x] 4.2 覆盖顶层字段保留
  - [x] 4.3 覆盖写入失败与回读失败

## Dev Notes

### 关键实现判断

- **强制落盘的核心目标是把 Pencil 内存态转换为磁盘真相。** 不是模拟编辑器保存，而是用结构化数据重建 repo 中的 `.pen` 文件。
- **`batch_get` 结果不能直接当 `.pen` 文件原文写回。** 必须保留原模板中的顶层合同。
- **回读验证是强制链的一部分，不是附加优化。** 没有回读成功，保存就不算成功。
- **save-report 是后续 gate 的证据链。** 它不是调试日志，必须被后续 Story 9.1c 使用。
- **执行模型二层分离：** 强制落盘链的前半段（Pencil MCP `batch_get` 抓取 + 结构化回写 + `export_nodes` 导出 PNG）由 designing prompt 指示 agent 在 `claude -p` subprocess 内执行；后半段（磁盘产物验证：JSON parse、字段检查、save-report 合法性校验）由 orchestrator 在 agent task 完成后通过 `design_artifacts.py` helper 执行。Orchestrator 本身不直接调用 Pencil MCP。

### Project Structure Notes

- 主要修改文件：
  - `src/ato/design_artifacts.py`
  - `src/ato/recovery.py`
  - 视实现需要可能补充 `src/ato/core.py`
- 重点测试文件：
  - `tests/unit/test_design_artifacts.py`
  - `tests/unit/test_core.py`

### References

- [Source: /Users/enjoyjavapan163.com/Public/SyncForAll/Doc/BidWise/.claude/skills/bmad-master-control/steps/step-02-batch-prep.md#L74]
- [Source: /Users/enjoyjavapan163.com/Public/SyncForAll/Doc/BidWise/.claude/skills/bmad-master-control/forbidden-list.md#F13]
- [Source: src/ato/recovery.py — designing prompt]
- [Source: _bmad-output/project-context.md]

### Previous Story Intelligence

1. Story 9.1a 已负责修正合同与模板基线；本 story 只实现强制落盘链，不再讨论“是否自动保存”。
2. BidWise 的 prototype 流程已证明“`batch_get` 抓完整内存树 → Python 回写磁盘”是可行 workaround。
3. 当前 ATO 的 `check_design_gate()` 仍只检查“有无文件”，因此 save-report/snapshot 是后续 gate 升级的前提。

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6 (1M context)

### Debug Log References

- 全部 1391 单元测试通过（0 回归）
- ruff check + ruff format + mypy strict 全部通过

### Completion Notes List

- Task 1: 在 `design_artifacts.py` 新增 `read_pen_file()`、`_atomic_write_json()`、`force_persist_pen()` 三个函数，实现结构化回写（保留顶层字段，只替换 children），使用 `tempfile.mkstemp` + `os.replace` 原子写入策略。新增 `PenPersistResult` / `PenVerifyResult` frozen dataclass。
- Task 2: 新增 `write_design_snapshot()` 和 `write_save_report()` 函数，快照为全量 JSON 输出，save-report 包含 AC#3 规定的全部 8 个字段（story_id, saved_at, pen_file, snapshot_file, children_count, json_parse_verified, reopen_verified, exported_png_count）。新增 `SAVE_REPORT_REQUIRED_KEYS` 常量。
- Task 3: 新增 `verify_pen_integrity()` 和 `verify_save_report()` 校验函数。更新 `recovery.py` 中的 designing prompt，将步骤 5（强制落盘）扩展为详细的"抓树 → 回写 → 验证"链路，新增步骤 6（落盘验证：本地 json.load + MCP batch_get 回读），明确禁止直接覆盖 .pen 文件。
- Task 4: 在 `test_design_artifacts.py` 新增 8 个测试类、28 个测试用例，覆盖：读取成功/失败/无效 JSON、结构化回写保留顶层字段/未知字段/原子写入失败不损坏原文件、快照写入/父目录自动创建、save-report 全字段/ISO 时间戳/默认值/语义校验（json_parse_verified=false / reopen_verified=false 拒绝）、校验通过/失败/缺失键、端到端集成链路。
- Review Fix (AC#3/AC#4 gate 三轮迭代): gate 通过条件最终为 `story_spec + pen_integrity_ok + snapshot_valid + save_report_valid`。三个核心产出物（.pen / snapshot / save-report）均为强制前置，默认 False。`verify_save_report()` 验证 boolean 语义。新增 `verify_snapshot()` 验证 JSON 合法性。`DesignGateResult` 含 `pen_integrity_ok` / `snapshot_valid` / `save_report_valid` 字段。TestDesignGate 共 19 个测试。

### Change Log

- 2026-03-28: Story 9.1b 实现完成 — 强制落盘链、快照/报告生成、保存后校验、designing prompt 更新
- 2026-03-28: Review Fix — gate 强制要求 .pen + snapshot + save-report 三件产出物全部存在且通过验证

### File List

- `src/ato/design_artifacts.py` — 新增强制落盘/校验函数 + verify_snapshot + verify_save_report 语义校验（modified）
- `src/ato/core.py` — check_design_gate 强制三件持久化证据链（modified）
- `src/ato/recovery.py` — 更新 designing prompt 步骤 5-7（modified）
- `tests/unit/test_design_artifacts.py` — 新增 28 个测试（modified）
- `tests/unit/test_core.py` — 重写 TestDesignGate 19 个测试（modified）
