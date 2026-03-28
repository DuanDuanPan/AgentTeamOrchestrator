# Story 9.1a: 修正 Designing 设计产物合同与 `.pen` 基线

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->
<!-- Depends on: Story 9.1 -->

## Story

As a 操作者,
I want `designing` 阶段的 prompt、模板与核心产物合同明确对齐 Pencil 的真实行为,
so that 后续实现不再依赖错误的“自动保存/加密格式”假设，设计阶段可以在正确的工程约束下落地。

## Acceptance Criteria

### AC1: Designing prompt 不再包含错误的保存与格式假设

```gherkin
Given 当前 `src/ato/recovery.py::_STRUCTURED_JOB_PROMPTS["designing"]`
When 修正 designing 阶段 prompt 合同
Then prompt 不再声明 `batch_design(filePath=...)` 会自动创建/保存 `.pen`
And prompt 不再声明 `.pen` 是“加密格式，只能通过 Pencil MCP 读写”
And prompt 明确要求：
  - 先准备现有 `.pen` 模板
  - 再用 Pencil MCP 打开并编辑该模板
  - 设计完成后进入“强制落盘”步骤
```

### AC2: 仓库中存在可复用的 `.pen` 模板基线

```gherkin
Given 当前仓库没有显式的 `.pen` 模板工件
When 新增设计模板基线
Then 仓库中新增一个可版本化的 JSON `.pen` 模板文件（如 `schemas/prototype-template.pen`）
And 该模板至少包含顶层字段 `version`、`children`、`variables`
And designing 阶段使用该模板派生 `{story_id}-ux/prototype.pen`
And 不依赖 Pencil 的 save-as / 首次保存行为
```

### AC3: 设计阶段的核心产物合同被代码化

```gherkin
Given designing 阶段需要稳定产出可验证工件
When 定义设计产物基线
Then 系统明确识别下列核心工件：
  - `ux-spec.md`
  - `prototype.pen`
  - `prototype.snapshot.json`
  - `prototype.save-report.json`
  - `exports/*.png`
And 这些路径由统一 helper 推导，而不是在 prompt、gate、测试中各自拼接
And 本 story 只定义路径合同与 helper 接口，文件的实际生成由 Story 9.1b 负责
```

### AC4: 路径与命名约定被集中管理

```gherkin
Given 当前 `{story_id}-ux/` 目录结构只存在于 prompt/gate 的隐式约定中
When 引入设计工件路径 helper
Then 系统有单一入口推导 story 的 UX 目录与文件路径
And helper 返回值至少覆盖：
  - UX 目录
  - `ux-spec.md`
  - `prototype.pen`
  - `prototype.snapshot.json`
  - `prototype.save-report.json`
  - `exports/`
And 后续 Story 9.1b / 9.1c / 9.1d 复用该 helper
```

### AC5: 测试与文档基线反映新合同

```gherkin
Given Story 9.1 当前把设计产物合同描述得过于宽松
When 引入修正后的合同
Then 单元测试新增或更新，覆盖：
  - `.pen` 模板可解析为 JSON
  - 设计工件路径 helper 返回正确路径
  - designing prompt 不再含“自动保存/加密格式”错误文案
And Story 9.1a 的 References / Dev Notes 显式记录本次 course correction 来源
```

## Tasks / Subtasks

- [ ] Task 1: 修正 designing prompt 合同 (AC: #1)
  - [ ] 1.1 更新 `src/ato/recovery.py::_STRUCTURED_JOB_PROMPTS["designing"]`
  - [ ] 1.2 删除“自动创建/保存”“加密格式”相关表述
  - [ ] 1.3 明确“模板 → MCP 编辑 → 强制落盘 → 导出 PNG”的执行顺序

- [ ] Task 2: 新增 `.pen` 模板基线 (AC: #2)
  - [ ] 2.1 新增 `schemas/prototype-template.pen`
  - [ ] 2.2 确认模板为可解析 JSON，顶层含 `version` / `children` / `variables`
  - [ ] 2.3 为后续 story 约定模板复制/派生方式

- [ ] Task 3: 设计工件路径 helper (AC: #3, #4)
  - [ ] 3.1 新增 `src/ato/design_artifacts.py` 或等价模块
  - [ ] 3.2 实现 `derive_design_artifact_paths(story_id, project_root)` 等 helper
  - [ ] 3.3 让 prompt / gate / 后续 story 都通过 helper 读取路径
  - [ ] 3.4 迁移 `recovery.py::_format_structured_job_prompt()` 中的内联路径拼接到新 helper

- [ ] Task 4: 测试与文档对齐 (AC: #5)
  - [ ] 4.1 新增 `tests/unit/test_design_artifacts.py`
  - [ ] 4.2 更新相关 prompt/gate 测试断言
  - [ ] 4.3 验证 `.pen` 模板 JSON 结构与 helper 输出

## Dev Notes

### 关键实现判断

- **这是 corrective foundation story，不直接实现强制落盘。** 它的职责是先修正错误合同，给 9.1b / 9.1c / 9.1d 建立可靠基线。
- **`.pen` 模板必须是 repo 内可版本化文件。** 不能把“首次由 Pencil 自动保存”当成系统契约。
- **路径推导必须集中。** 后续 gate、manifest、save-report 全部依赖同一套路径 helper，否则 prompt 和验证会再次漂移。
- **当前 9.1 的 phase insertion 资产可以保留。** 本 story 不回滚状态机/transition/replay/recovery 的已完成改动。

### Project Structure Notes

- 建议新增模块：`src/ato/design_artifacts.py`
- 建议新增模板：`schemas/prototype-template.pen`
- 现有主要受影响文件：
  - `src/ato/recovery.py`
  - `src/ato/core.py`
  - `tests/unit/test_core.py`

### References

- [Source: src/ato/recovery.py — designing prompt 当前仍含“自动保存 / 加密格式”假设]
- [Source: src/ato/core.py — design gate 当前仅检查任意 `.md/.pen/.png`]
- [Source: _bmad-output/implementation-artifacts/9-1-add-designing-phase.md]
- [Source: /Users/enjoyjavapan163.com/Public/SyncForAll/Doc/BidWise/.claude/skills/bmad-master-control/forbidden-list.md#F13]
- [Source: _bmad-output/project-context.md]

### Previous Story Intelligence

1. `239d913 feat(story-9.1): add designing phase to story lifecycle` 已完成 phase insertion，本 story 只修正设计产物合同，不回滚状态机。
2. BidWise 的 `bmad-master-control` 已经显式把“禁止依赖 Pencil MCP 自动保存 .pen 文件”列为 incident-driven forbidden rule，可作为直接先例。
3. 当前 `9.2` / `9.3` 已以 `designing` phase 存在为前提，因此 corrective story 应采用增量补强，而非回滚 9.1。

## Dev Agent Record

### Agent Model Used

待 dev-story 填写

### Debug Log References

### Completion Notes List

### File List
