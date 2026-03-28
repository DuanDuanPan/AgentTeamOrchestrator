# Story 9.1a: 修正 Designing 设计产物合同与 `.pen` 基线

Status: done

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

- [x] Task 1: 修正 designing prompt 合同 (AC: #1)
  - [x] 1.1 更新 `src/ato/recovery.py::_STRUCTURED_JOB_PROMPTS[“designing”]`
  - [x] 1.2 删除”自动创建/保存””加密格式”相关表述
  - [x] 1.3 明确”模板 → MCP 编辑 → 强制落盘 → 导出 PNG”的执行顺序

- [x] Task 2: 新增 `.pen` 模板基线 (AC: #2)
  - [x] 2.1 新增 `schemas/prototype-template.pen`
  - [x] 2.2 确认模板为可解析 JSON，顶层含 `version` / `children` / `variables`
  - [x] 2.3 为后续 story 约定模板复制/派生方式

- [x] Task 3: 设计工件路径 helper (AC: #3, #4)
  - [x] 3.1 新增 `src/ato/design_artifacts.py` 或等价模块
  - [x] 3.2 实现 `derive_design_artifact_paths(story_id, project_root)` 等 helper
  - [x] 3.3 让 prompt / gate / 后续 story 都通过 helper 读取路径
  - [x] 3.4 迁移 `recovery.py::_format_structured_job_prompt()` 中的内联路径拼接到新 helper

- [x] Task 4: 测试与文档对齐 (AC: #5)
  - [x] 4.1 新增 `tests/unit/test_design_artifacts.py`
  - [x] 4.2 更新相关 prompt/gate 测试断言
  - [x] 4.3 验证 `.pen` 模板 JSON 结构与 helper 输出

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

Claude Opus 4.6 (1M context)

### Debug Log References

- 无异常/调试事件

### Completion Notes List

- **Task 1**: 重写 `_STRUCTURED_JOB_PROMPTS["designing"]`，删除"自动创建/保存"与"加密格式"错误表述，新增 7 步流程（读 spec → UX 规格 → 准备模板 → MCP 编辑 → 强制落盘 → 导出 PNG → 可选 frontend-design）
- **Task 2**: 新增 `schemas/prototype-template.pen` 作为可版本化 JSON 模板（含 version/children/variables 顶层字段）
- **Task 3**: 新增 `src/ato/design_artifacts.py` 模块，实现 `derive_design_artifact_paths()` 和 `derive_design_artifact_paths_relative()` 两个 helper；迁移 `_format_structured_job_prompt()` 使用 helper 推导路径
- **Task 4**: 新增 `tests/unit/test_design_artifacts.py`（13 个测试），在 `test_recovery.py` 追加 9 个测试（prompt 合同 6 + 模板基线 3），总计 22 个新测试全部通过
- **Review R1 [HIGH]**: `save_report_json` 加入 prompt 模板 + 格式化参数
- **Review R1 [LOW]**: 清理 ruff 违规
- **Review R2 [MED]**: `check_design_gate` 签名从 `artifacts_dir` 改为 `project_root`，内部使用 `derive_design_artifact_paths` helper 推导所有路径，按 `DESIGN_ARTIFACT_NAMES` 已知工件名匹配（不再按扩展名），`debug.json` 等无关文件不再误放行。更新 core.py + recovery.py 调用方。
- **Review R2 [MED]**: gate + recovery 调用方不再手工拼 `"_bmad-output" / "implementation-artifacts"`，全部通过 helper 或直接传 `project_root`，AC3/AC4"统一 helper 推导"落地到 gate 代码路径。
- **Review R3 [LOW]**: prompt 中 `{ux_dir}/ux-spec.md` 改为 `{ux_spec}` 占位符，`_format_structured_job_prompt` 注入 `ux_spec` 路径键，消除最后一处手工拼接。

### Change Log

- 2026-03-28: Story 9.1a 完成 — 修正 designing 阶段产物合同与 .pen 基线
- 2026-03-28: R1 修复 3 项 findings（save-report 合同补全 + lint 清理）
- 2026-03-28: R2 修复 2 项 findings（gate 接入 helper + 已知工件名匹配）
- 2026-03-28: R3 修复 1 项 finding（ux_spec 路径注入 helper）

### File List

- `src/ato/design_artifacts.py` — 新增：设计工件路径推导 helper（ARTIFACTS_REL / DESIGN_ARTIFACT_NAMES / derive helpers）
- `src/ato/recovery.py` — 修改：designing prompt 重写 + _format_structured_job_prompt 使用 helper + _check_design_gate 传 project_root
- `src/ato/core.py` — 修改：check_design_gate 签名改为 project_root，内部消费 helper，按已知工件名匹配
- `schemas/prototype-template.pen` — 新增：.pen JSON 模板基线
- `tests/unit/test_design_artifacts.py` — 新增：13 个测试覆盖 helper/常量/gate 路径对齐
- `tests/unit/test_recovery.py` — 修改：追加 9 个测试（prompt 合同 6 + 模板基线 3）
- `tests/unit/test_core.py` — 修改：重写 TestDesignGate（10 个测试覆盖已知工件名匹配 + unknown json 拒绝 + exports 递归）
- `_bmad-output/implementation-artifacts/9-1a-correct-designing-artifact-contract.md` — 修改：story 进度更新
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — 修改：story 状态更新
