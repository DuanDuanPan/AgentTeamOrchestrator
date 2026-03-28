# Story 9.1d: Prototype Manifest 与下游消费契约

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->
<!-- Depends on: Story 9.1b, Story 9.1c -->

## Story

As a 操作者,
I want 每个 UI story 生成可供开发、验证、评审消费的 `prototype.manifest.yaml`,
so that 后续阶段有统一入口理解该 story 的设计文件、导出图、主 frame、查阅顺序与设计约束，而不是各自猜测 UX 产物如何使用。

## Acceptance Criteria

### AC1: Designing 阶段生成 `prototype.manifest.yaml`

```gherkin
Given designing 阶段已完成强制落盘与 PNG 导出
When 系统写入最终设计工件
Then 额外生成 `prototype.manifest.yaml`
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
```

### AC2: Manifest 记录设计消费顺序与工程约束

```gherkin
Given 后续开发与验证都需要消费设计产物
When 生成 manifest
Then `dev_lookup_order` 明确至少包含：
  - 先读 story 文件中的设计说明
  - 再读 manifest
  - 再看 PNG
  - 最后打开 `.pen`
And `notes` 或等价字段说明：PNG 用于视觉对齐，`.pen` 用于结构与交互细节
```

### AC3: 下游阶段显式消费 manifest / PNG / `.pen`

```gherkin
Given 后续还有 `validating` / `developing` / `reviewing` 等阶段
When 系统为这些阶段构建 prompt 或上下文
Then 若 `{story_id}-ux/prototype.manifest.yaml` 存在
And prompt / context 中显式带入：
  - manifest 路径
  - PNG 导出目录
  - `.pen` 路径
And 不再只笼统地说“如果有 UX 目录请参考”
```

### AC4: Manifest 成为设计交付的一部分

```gherkin
Given Story 9.1c 已升级 design gate
When 引入 manifest
Then design gate 扩展为要求 `prototype.manifest.yaml` 存在
And manifest 的 `story_id` 与当前 story 一致
And manifest 中列出的关键文件路径在磁盘上真实存在
```

### AC5: 测试覆盖 manifest 生成与消费

```gherkin
Given 完整测试套件
When 运行 manifest 相关测试
Then 至少覆盖：
  - manifest 生成成功
  - manifest 缺失导致 gate fail
  - manifest 中路径缺失导致 gate fail
  - 下游 prompt/context 包含 manifest / PNG / `.pen` 引用
```

## Tasks / Subtasks

- [ ] Task 1: 定义 manifest 结构与生成逻辑 (AC: #1, #2)
  - [ ] 1.1 在 `src/ato/design_artifacts.py` 中新增 manifest 生成 helper
  - [ ] 1.2 约定 manifest YAML 结构
  - [ ] 1.3 从快照/导出结果中提取 `primary_frames`、`reference_exports`

- [ ] Task 2: 把 manifest 纳入设计交付合同 (AC: #4)
  - [ ] 2.1 更新 design gate，使 manifest 成为硬性工件
  - [ ] 2.2 校验 manifest 的 `story_id` 与文件路径真实性

- [ ] Task 3: 下游阶段消费 manifest (AC: #3)
  - [ ] 3.1 更新相关 prompt/context builder
  - [ ] 3.2 在 validating / developing / reviewing 的上下文中显式带入 manifest / PNG / `.pen`（若这些阶段的 prompt builder 尚未实现，则在 `_format_structured_job_prompt` 中预留可选 manifest path 变量）
  - [ ] 3.3 保持无 UX story 的兼容行为

- [ ] Task 4: 测试与回归覆盖 (AC: #5)
  - [ ] 4.1 新增或更新 `tests/unit/test_design_artifacts.py`
  - [ ] 4.2 新增 prompt/context 断言测试
  - [ ] 4.3 更新 gate 相关测试

## Dev Notes

### 关键实现判断

- **manifest 不是锦上添花，而是下游消费入口。** 没有 manifest，开发与验证只能自己猜 UX 工件怎么用。
- **manifest 的职责是“索引 + 消费顺序 + 设计约束”。** 它不是 `.pen` 的替代品，也不是 save-report 的重复品。
- **只有 UI story 才需要 manifest。** 纯后端 story 在 Story 9.3 的 `skip_when` 生效后应兼容 manifest 缺失路径。
- **一旦引入 manifest，gate 应同步升级。** 否则 manifest 只是可选文件，无法形成真正的交付合同。
- **下游阶段 prompt 尚未全部实现。** 当前 `_STRUCTURED_JOB_PROMPTS` 只有 `designing` 一个条目。若 validating / developing / reviewing 的 prompt builder 尚未实现，只需在 `_format_structured_job_prompt` 中预留可选的 manifest path 变量，不需要在本 story 中完整实现所有下游阶段的 prompt。

### Project Structure Notes

- 主要修改文件：
  - `src/ato/design_artifacts.py`
  - `src/ato/core.py`
  - 相关 prompt/context builder 模块
- 重点测试文件：
  - `tests/unit/test_design_artifacts.py`
  - `tests/unit/test_core.py`
  - 相关 prompt/context 测试

### References

- [Source: /Users/enjoyjavapan163.com/Public/SyncForAll/Doc/BidWise/_bmad-output/implementation-artifacts/prototypes/prototype-manifest.yaml]
- [Source: /Users/enjoyjavapan163.com/Public/SyncForAll/Doc/BidWise/.claude/skills/bmad-master-control/steps/step-02-batch-prep.md#L90]
- [Source: src/ato/core.py — design gate]
- [Source: _bmad-output/project-context.md]

### Previous Story Intelligence

1. BidWise 的 `prototype-manifest.yaml` 已证明 manifest 可以同时承担“设计索引 + 开发查找顺序 + 设计细节补充”三类职责。
2. Story 9.1b / 9.1c 已经把 `.pen` 落盘与 gate 证据链建立起来；本 story 负责把这些证据转化为下游可消费合同。
3. Story 9.3 会在 `dev_ready` 上提交规格文件到 main；manifest 应该与 story spec、UX 目录一起进入可见的规格集合。

## Dev Agent Record

### Agent Model Used

待 dev-story 填写

### Debug Log References

### Completion Notes List

### File List
