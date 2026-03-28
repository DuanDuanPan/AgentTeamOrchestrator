# Story 9.1b: Designing 阶段强制落盘与设计快照链路

Status: ready-for-dev

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

- [ ] Task 1: 实现 `.pen` 结构化回写 helper (AC: #1, #2)
  - [ ] 1.1 在 `src/ato/design_artifacts.py` 中实现 `.pen` 读取与回写 helper
  - [ ] 1.2 实现“保留顶层字段，只替换 `children`”逻辑
  - [ ] 1.3 使用临时文件 + rename 的原子写入策略

- [ ] Task 2: 实现 snapshot / save-report 生成 (AC: #3)
  - [ ] 2.1 写入 `prototype.snapshot.json`
  - [ ] 2.2 写入 `prototype.save-report.json`
  - [ ] 2.3 约定 save-report 字段结构

- [ ] Task 3: 集成 designing 保存后校验 (AC: #4)
  - [ ] 3.1 在 designing 流程中接入 `batch_get(readDepth=99, includePathGeometry=true)`
  - [ ] 3.2 接入 JSON parse 验证
  - [ ] 3.3 接入 MCP reopen / 回读验证

- [ ] Task 4: 测试失败恢复与字段保留 (AC: #5)
  - [ ] 4.1 扩展 `tests/unit/test_design_artifacts.py`（由 Story 9.1a 创建）
  - [ ] 4.2 覆盖顶层字段保留
  - [ ] 4.3 覆盖写入失败与回读失败

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

待 dev-story 填写

### Debug Log References

### Completion Notes List

### File List
