# Story 9.1d: Prototype Manifest 与下游消费契约

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->
<!-- Depends on: Story 9.1b (save-report/snapshot), Story 9.1c (design gate V2) -->
<!-- Created: 2026-03-28 | Enhanced: 2026-03-28 -->

## Story

As a 操作者,
I want 每个 UI story 生成可供开发、验证、评审消费的 `prototype.manifest.yaml`,
so that 后续阶段有统一入口理解该 story 的设计文件、导出图、主 frame、查阅顺序与设计约束，而不是各自猜测 UX 工件如何使用。

## Acceptance Criteria

### AC1: 系统基于已保存设计工件生成 `prototype.manifest.yaml`

```gherkin
Given designing 阶段已经完成强制落盘、snapshot/save-report 写入与 PNG 导出
When 系统在 `design_done` gate 前整理最终设计交付物
Then 会在 `{story_id}-ux/` 下生成 `prototype.manifest.yaml`
And manifest 至少包含：
  - `story_id`
  - `story_file`
  - `ux_spec`
  - `pen_file`
  - `snapshot_file`
  - `save_report_file`
  - `reference_exports`
  - `primary_frames`
  - `dev_lookup_order`
  - `notes`
And `reference_exports` 来自磁盘上真实存在的 `exports/*.png`
And `primary_frames` 按确定性规则从 `prototype.snapshot.json` 提取，而不是依赖 agent 自由发挥
```

### AC2: Manifest 明确路径基准、查阅顺序与设计约束

```gherkin
Given 开发、验证、评审都需要消费同一份 UX 入口
When 生成 manifest
Then `story_file` 使用 project-root 相对路径
And `ux_spec`、`pen_file`、`snapshot_file`、`save_report_file` 与 `reference_exports` 使用 UX 目录相对路径
And `dev_lookup_order` 至少包含：
  - 先读 story 文件中的设计说明
  - 再读 manifest
  - 再看 PNG
  - 最后打开 `.pen`
And `notes` 说明：PNG 用于视觉对齐，`.pen` 用于结构与交互细节
```

### AC3: 下游 validating / developing / reviewing 在真实运行入口中显式消费 manifest

```gherkin
Given 某个 story 已经生成 `{story_id}-ux/prototype.manifest.yaml`
When 系统为 validating / developing / reviewing 构建 prompt 或上下文
Then 追加 UX 上下文的实际调用点至少包括：
  - `src/ato/recovery.py::_dispatch_convergent_loop()` 的 validating prompt
  - `src/ato/core.py::_build_interactive_prompt()` 的 developing prompt
  - `src/ato/convergent_loop.py` 的首轮 review / re-review prompt
And 附加内容显式带入：
  - manifest 路径
  - PNG 导出目录
  - `.pen` 路径
  - `dev_lookup_order` 摘要
And 不再只笼统地说"如果有 UX 目录请参考"
And manifest 不存在时保持当前行为不变（兼容无 UI story / 后续 Story 9.3 skip 路径）
```

### AC4: Manifest 成为 design gate 的硬性合同

```gherkin
Given Story 9.1c 已经建立 design gate V2
When 引入 manifest
Then gate 要求 `prototype.manifest.yaml` 存在
And manifest 的 `story_id` 与当前 story 一致
And `story_file` 会按 project_root 解析并验证真实存在
And `ux_spec`、`pen_file`、`snapshot_file`、`save_report_file` 会按 UX 目录解析并验证真实存在
And `reference_exports` 中列出的 PNG 文件都必须真实存在
And manifest 解析失败、story_id 不匹配、关键路径缺失会产生独立 failure codes
```

### AC5: 测试覆盖 manifest 生成、gate 与下游消费面

```gherkin
Given 更新后的单元测试
When 运行 manifest 相关测试
Then 至少覆盖：
  - manifest 基于 snapshot / exports 成功生成
  - manifest 缺失导致 gate fail
  - manifest 解析失败导致 gate fail
  - manifest story_id 不匹配导致 gate fail
  - manifest 中路径或 export 引用缺失导致 gate fail
  - validating prompt 包含 manifest / PNG / `.pen` 引用
  - developing prompt 包含 manifest / PNG / `.pen` 引用
  - reviewing 的首轮 review / re-review prompt 包含 manifest / PNG / `.pen` 引用
```

## Tasks / Subtasks

- [x] Task 1: 在 `design_artifacts.py` 中实现确定性的 manifest 生成与读取 helper (AC: #1, #2)
  - [x] 1.1 在 `derive_design_artifact_paths()` 和 `derive_design_artifact_paths_relative()` 中新增 `manifest_yaml` 键
  - [x] 1.2 在 `DESIGN_ARTIFACT_NAMES` 中纳入 `"prototype.manifest.yaml"`
  - [x] 1.3 新增 YAML 原子写入 helper 与 `write_prototype_manifest()`，由 Python 基于磁盘真相生成 manifest，而不是让 agent 手写 YAML
  - [x] 1.4 `write_prototype_manifest()` 通过 `exports/` 目录与 `prototype.snapshot.json` 确定性推导 `reference_exports` / `primary_frames`
  - [x] 1.5 新增读取 manifest 并构建 UX 上下文段落的共享 helper，供 validating / developing / reviewing 复用
  - [x] 1.6 复用当前仓库已存在的 `yaml` runtime（`pydantic-settings[yaml]` + 现有 `import yaml` 先例）；不要默认新增依赖，除非运行时验证证明缺失

- [x] Task 2: 在 `design_done` 前的 Python 路径中生成 manifest (AC: #1, #4)
  - [x] 2.1 在 `src/ato/core.py` 正常 success-event 路径中，`check_design_gate()` 之前调用共享 manifest 生成 helper
  - [x] 2.2 在 `src/ato/recovery.py::_check_design_gate()` 中调用同一 helper，保持 core / recovery 一致
  - [x] 2.3 保持 `check_design_gate()` 为纯验证逻辑，不在 gate 内隐式写文件

- [x] Task 3: 扩展 design gate 校验 manifest 合同 (AC: #4)
  - [x] 3.1 在 `src/ato/core.py::DesignGateResult` 中新增 `manifest_valid: bool = False`
  - [x] 3.2 在 `src/ato/core.py::check_design_gate()` 中新增 manifest 检查与 failure codes：`MANIFEST_MISSING`、`MANIFEST_INVALID`、`MANIFEST_STORY_ID_MISMATCH`、`MANIFEST_PATHS_MISSING`
  - [x] 3.3 校验混合路径基准：`story_file` 按 project_root 解析，其余 UX 工件按 UX 目录解析
  - [x] 3.4 校验 `reference_exports` 指向的 PNG 文件真实存在
  - [x] 3.5 继续复用 `build_design_gate_payload()` 现有 failure_codes / missing_files 结构，不再分叉新的 approval payload 形态

- [x] Task 4: 在真实 prompt 构建入口中消费 manifest (AC: #3)
  - [x] 4.1 在 `src/ato/recovery.py::_dispatch_convergent_loop()` 的 validating prompt 中附加共享 UX 上下文
  - [x] 4.2 在 `src/ato/core.py::_build_interactive_prompt()` 的 developing prompt 中附加共享 UX 上下文
  - [x] 4.3 在 `src/ato/convergent_loop.py` 的首轮 review 与 re-review prompt builder 中附加共享 UX 上下文
  - [x] 4.4 manifest 缺失时保持 prompt passthrough，不影响无 UI story 或 manifest 尚未生成的场景

- [x] Task 5: 测试与回归覆盖 (AC: #5)
  - [x] 5.1 在 `tests/unit/test_design_artifacts.py` 新增 manifest 生成 / 读取 / 上下文 helper 测试
  - [x] 5.2 在 `tests/unit/test_core.py::TestDesignGate` 中新增 manifest gate 测试，并补一条 developing prompt 上下文断言
  - [x] 5.3 在 `tests/unit/test_recovery.py` 中新增 validating prompt 的 manifest 注入断言
  - [x] 5.4 在 `tests/unit/test_convergent_loop.py` 中新增首轮 review / re-review prompt 的 manifest 注入断言

## Dev Notes

### 关键实现判断

1. **manifest 必须是基于磁盘真相的 Python 侧后处理产物。** 如果继续让 designing agent 自由手写 YAML，gate 无法把它视为可信合同，`write_prototype_manifest()` 也会沦为死 helper。
2. **AC3 涉及三个不同的 prompt 构建面。** `validating` 走 `recovery.py`，`developing` 走 `core.py::_build_interactive_prompt()`，`reviewing` 走 `convergent_loop.py`。仅修改 `_format_structured_job_prompt()` 无法满足 AC3。
3. **manifest 的路径基准是混合的。** `story_file` 是 project-root 相对路径；UX 工件与 PNG 引用是 UX 目录相对路径。gate 必须按不同基准解析，不能一律 `ux_dir / value`。
4. **`check_design_gate()` 应继续保持 validator 角色。** 生成 manifest 的动作应在 core / recovery 的 `design_done` 前置路径中完成，再由 gate 验证结果。
5. **只有 UI story 需要 manifest。** 非 UI story 在 Story 9.3 的 `skip_when` 生效后应通过"manifest 缺失时不注入 UX context"保持兼容。

### Manifest YAML 合同

```yaml
# prototype.manifest.yaml — per-story UX 设计消费索引
story_id: "9-1d-prototype-manifest-downstream-consumption"
story_file: "_bmad-output/implementation-artifacts/9-1d-prototype-manifest-downstream-consumption.md"
ux_spec: "ux-spec.md"
pen_file: "prototype.pen"
snapshot_file: "prototype.snapshot.json"
save_report_file: "prototype.save-report.json"
reference_exports:
  - "exports/frame-1.png"
primary_frames:
  - "Dashboard"
dev_lookup_order:
  - "Read story file design notes"
  - "Read this manifest"
  - "Open reference PNG for visual fidelity"
  - "Open .pen for structure and interaction detail"
notes: "PNG 用于视觉对齐参考，.pen 用于结构与交互细节查阅。"
```

路径语义：
- `story_file`：project-root 相对路径
- `ux_spec` / `pen_file` / `snapshot_file` / `save_report_file` / `reference_exports`：UX 目录相对路径

### Scope Boundary

- **IN:** manifest 生成与读取 helper、core / recovery 的 pre-gate 接线、design gate manifest 校验、validating / developing / reviewing prompt 注入、对应单元测试
- **OUT:** `fixing` prompt 重构
- **OUT:** Story 9.2 的 workspace 语义调整
- **OUT:** 修改 `BmadAdapter` 解析逻辑或 `ConvergentLoop` 状态机语义
- **OUT:** 让 designing agent 手写 / 维护 manifest 内容

### Project Structure Notes

- 主要修改文件：
  - `src/ato/design_artifacts.py`
  - `src/ato/core.py`
  - `src/ato/recovery.py`
  - `src/ato/convergent_loop.py`
- 重点测试文件：
  - `tests/unit/test_design_artifacts.py`
  - `tests/unit/test_core.py`
  - `tests/unit/test_recovery.py`
  - `tests/unit/test_convergent_loop.py`
- 只读证据文件：
  - `src/ato/preflight.py`
  - `pyproject.toml`

### Suggested Verification

```bash
uv run pytest tests/unit/test_design_artifacts.py tests/unit/test_core.py tests/unit/test_recovery.py tests/unit/test_convergent_loop.py -v
```

## References

- [Source: _bmad-output/planning-artifacts/epics.md]
- [Source: _bmad-output/planning-artifacts/sprint-change-proposal-2026-03-28-designing-artifact-persistence.md]
- [Source: src/ato/design_artifacts.py]
- [Source: src/ato/core.py]
- [Source: src/ato/recovery.py]
- [Source: src/ato/convergent_loop.py]
- [Source: src/ato/preflight.py]
- [Source: pyproject.toml]

### Previous Story Intelligence

1. **Story 9.1a** 已将 designing 相关路径推导集中到 `design_artifacts.py`；manifest 新增键应继续走这一入口，而不是散落在 prompt 或 gate 代码里手拼路径。
2. **Story 9.1b** 已经形成“agent 负责 Pencil/MCP 产物，Python 负责磁盘侧验证”的分层；manifest 作为设计交付索引，应落在 Python 这一侧，而不是再引入一个依赖 agent 自觉输出的新自由文本文件。
3. **Story 9.1c** 已把 gate 收紧为共享的 core / recovery 合同；manifest 生成也必须在这两条路径前保持一致，避免只修一侧。
4. **Story 9.1e / 9.1f** 证明了 prompt / context 修复必须落在真实运行入口上；抽象地修改一个某些 phase 根本不会经过的 helper，会再次制造“story 写对了但实现落不到运行面”的问题。

## Change Log

- 2026-03-28: Story 创建 — 基于 Epic 9 corrective planning 增补 manifest 与下游消费 story
- 2026-03-28: `validate-create-story` 修订 —— 将 manifest 生成为基于已保存工件的 Python 侧后处理；把下游消费面收紧到 `recovery.py` / `core.py` / `convergent_loop.py` 的真实 prompt builder；澄清现有 YAML runtime 与 mixed-base path 合同；补回 Scope Boundary、Suggested Verification 与 Change Log 结构
- 2026-03-28: `dev-story` 实现完成 — manifest 生成/读取/gate/prompt 注入全部实现
- 2026-03-28: Code review 修复 — gate 路径合同加固(绝对路径/越界/非png)、ruff 违规修复、validating 测试重写为真实路径调用。24 新增测试 + 1440 全量回归通过

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6 (1M context)

### Debug Log References

无 — 一次性实现通过，无需调试。

### Completion Notes List

- **Task 1:** 在 `design_artifacts.py` 新增 `_atomic_write_yaml()`、`_extract_primary_frames()`、`_collect_reference_exports()`、`write_prototype_manifest()`、`read_prototype_manifest()`、`build_ux_context_from_manifest()` 六个函数。复用现有 PyYAML runtime，无新增依赖。路径推导函数增加 `manifest_yaml` 键，`DESIGN_ARTIFACT_NAMES` 包含 manifest 文件名。
- **Task 2:** 在 `core.py` 和 `recovery.py` 的两条 `design_done` 前置路径中均调用 `write_prototype_manifest()`。recovery 侧抽出 `_generate_manifest_before_gate()` 方法复用。`check_design_gate()` 保持纯验证。
- **Task 3:** `DesignGateResult` 新增 `manifest_valid` 字段。`check_design_gate()` 新增 `MANIFEST_MISSING`/`MANIFEST_INVALID`/`MANIFEST_STORY_ID_MISMATCH`/`MANIFEST_PATHS_MISSING` 四个 failure codes。按混合路径基准验证 story_file (project-root) 和 UX 工件 (ux_dir)。Gate 校验拒绝绝对路径、`..` 越界和非 `.png` 后缀的 reference_exports。
- **Task 4:** 三个真实 prompt 入口均注入 UX 上下文：`recovery._dispatch_convergent_loop()` (validating)、`core._build_interactive_prompt()` (developing)、`convergent_loop.run_first_review/run_rereview` (reviewing)。`_append_ux_context()` 方法在 `ConvergentLoop` 内复用。manifest 缺失时全部 passthrough。
- **Task 5:** 新增 24 个测试（test_design_artifacts: 15, test_core: 10, test_recovery: 2, test_convergent_loop: 2），更新 1 个现有 fixture 和 1 个断言。全量 1440 单元测试通过，ruff/mypy clean。
- **Code review fixes:** (1) Gate 路径合同加固：拒绝绝对路径、`..` 越界、非 `.png` 后缀；(2) ruff 违规修复：补 Path 导入、移除未使用导入/变量；(3) validating 测试重写为调用真实 `_dispatch_convergent_loop` 路径。

### File List

- `src/ato/design_artifacts.py` — 新增 manifest 生成/读取/UX 上下文 helper
- `src/ato/core.py` — DesignGateResult +manifest_valid, check_design_gate +manifest 校验, _build_interactive_prompt +UX 上下文, design_done 前生成 manifest
- `src/ato/recovery.py` — _generate_manifest_before_gate 方法, _dispatch_convergent_loop +UX 上下文注入, design_done 两条路径均调用 manifest 生成
- `src/ato/convergent_loop.py` — _append_ux_context 方法, run_first_review +UX 上下文, run_rereview +UX 上下文
- `tests/unit/test_design_artifacts.py` — 新增 TestWritePrototypeManifest, TestReadPrototypeManifest, TestBuildUxContextFromManifest
- `tests/unit/test_core.py` — 新增 manifest gate 测试 (5 条), TestDevelopingPromptUxContext (2 条), 更新 _setup_full_prerequisites fixture
- `tests/unit/test_recovery.py` — 新增 TestValidatingPromptManifestInjection (2 条)
- `tests/unit/test_convergent_loop.py` — 新增 TestReviewPromptManifestInjection (2 条)
