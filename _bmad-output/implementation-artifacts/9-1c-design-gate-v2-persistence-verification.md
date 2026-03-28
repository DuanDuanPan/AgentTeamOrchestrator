# Story 9.1c: Design Gate V2 与持久化验证

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->
<!-- Depends on: Story 9.1a, Story 9.1b -->

## Story

As a 操作者,
I want `design_done` 前的 design gate 升级为基于磁盘真相与内容校验的严格门控,
so that 空文件、假文件或只存在于内存中的设计状态不会被误判为已完成。

## Acceptance Criteria

### AC1: `check_design_gate()` 升级为严格的核心工件校验

```gherkin
Given designing 阶段 agent task 执行完成
When 系统准备提交 `design_done`
Then design gate 至少检查：
  - `ux-spec.md` 存在
  - `prototype.pen` 存在
  - `prototype.snapshot.json` 存在
  - `prototype.save-report.json` 存在
  - `exports/` 下至少存在 1 个 `.png`
And 不再使用“目录中任意 `.md/.pen/.png` 数量 > 0”作为通过条件
```

### AC2: `.pen` 与 save-report 的内容必须可验证

```gherkin
Given `prototype.pen` 与 `prototype.save-report.json` 已存在
When 执行 design gate
Then 系统额外验证：
  - `prototype.pen` 可被 `json.load`
  - 顶层至少含 `version` 与 `children`
  - `prototype.save-report.json` 中 `json_parse_verified == true`
  - `prototype.save-report.json` 中 `reopen_verified == true`
And 任一内容校验失败都导致 gate fail
```

### AC3: Gate failure payload 结构化且可操作

```gherkin
Given design gate 校验失败
When 创建 `needs_human_review` approval
Then payload 不仅包含 `task_id`
And 还包含：
  - `artifact_dir`
  - `failure_codes`
  - `missing_files`
  - `reason`
  - 若有 save-report，则包含其关键状态摘要
And 通知信息明确指出失败项，而不是笼统地说“artifact missing”
```

### AC4: Core 与 Recovery 两条路径保持一致

```gherkin
Given design gate 既会在正常 dispatch 路径执行，也会在 recovery 路径执行
When 升级 design gate
Then `src/ato/core.py` 与 `src/ato/recovery.py` 都复用同一套 gate helper
And 两条路径的 approval payload 结构一致
And 两条路径的日志事件字段一致
```

### AC5: 测试覆盖严格 gate 的通过/失败矩阵

```gherkin
Given 完整测试套件
When 运行 design gate 相关测试
Then 至少覆盖：
  - 核心文件齐全时通过
  - 缺 `prototype.pen` 失败
  - `prototype.pen` 非 JSON 失败
  - 缺 `prototype.save-report.json` 失败
  - `save-report.reopen_verified=false` 失败
  - 缺 PNG 失败
And 旧的宽松通过测试被移除或重写
```

## Tasks / Subtasks

- [ ] Task 1: 重构 design gate 结果模型 (AC: #1, #2, #3)
  - [ ] 1.1 扩展 `DesignGateResult`
  - [ ] 1.2 增加 `failure_codes` / `missing_files` / 内容校验字段
  - [ ] 1.3 保持 structlog 事件可追踪

- [ ] Task 2: 实现严格 gate 逻辑 (AC: #1, #2)
  - [ ] 2.1 在 `src/ato/core.py::check_design_gate()` 中读取核心工件
  - [ ] 2.2 验证 `.pen` JSON 结构
  - [ ] 2.3 验证 save-report 字段
  - [ ] 2.4 验证 PNG 导出数量

- [ ] Task 3: 对齐 core / recovery 调用路径 (AC: #4)
  - [ ] 3.1 更新 `src/ato/recovery.py::_check_design_gate()`
  - [ ] 3.2 更新 `src/ato/core.py` 正常 success-event 路径
  - [ ] 3.3 将 approval payload 构建提取为共享 helper（当前分散在 core.py 和 recovery.py 两处），core 和 recovery 都调用它

- [ ] Task 4: 重写相关测试 (AC: #5)
  - [ ] 4.1 更新 `tests/unit/test_core.py`
  - [ ] 4.2 如有需要补 `tests/unit/test_recovery.py`
  - [ ] 4.3 删除或重写旧的宽松通过断言

## Dev Notes

### 关键实现判断

- **Gate V2 的目标是验证“已保存且可消费”，不是验证“目录里有点东西”。**
- **严格 gate 应该建立在 Story 9.1b 的 save-report 上。** 没有 save-report，就无法证明 `.pen` 真经历过结构化保存与回读校验。
- **approval payload 必须可诊断。** 后续 TUI/CLI 需要据此直接告诉操作者“缺什么、坏在哪”。
- **core/recovery 不能分叉。** 设计 gate 是统一合同，两条路径必须共享实现。
- **approval payload 构建也必须共享。** 当前 approval payload 在 `core.py:1085-1106` 和 `recovery.py:912-940` 两处各有一份构建逻辑，应提取为共享 helper，确保 payload 结构不会分叉。

### Project Structure Notes

- 主要修改文件：
  - `src/ato/core.py`
  - `src/ato/recovery.py`
  - `src/ato/design_artifacts.py`
- 重点测试文件：
  - `tests/unit/test_core.py`
  - `tests/unit/test_recovery.py`

### References

- [Source: src/ato/core.py — `check_design_gate()` 当前宽松实现]
- [Source: src/ato/recovery.py — `_check_design_gate()`]
- [Source: _bmad-output/implementation-artifacts/9-1-add-designing-phase.md]
- [Source: _bmad-output/project-context.md]

### Previous Story Intelligence

1. Story 9.1 的 gate 已经放在正确层次：success-event 提交路径，而不是状态机 transition handler。这个落点应保持不变。
2. Story 9.1b 会产出 `prototype.snapshot.json` 与 `prototype.save-report.json`；本 story 负责把它们变成 gate 的硬性证据。
3. 当前单元测试把“空 `.pen` 文件”也视为通过，这正是本 story 需要清理的错误基线。

## Dev Agent Record

### Agent Model Used

待 dev-story 填写

### Debug Log References

### Completion Notes List

### File List
