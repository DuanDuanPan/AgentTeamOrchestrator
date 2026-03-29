# Story 9.1f: validating 阶段 artifact-file fallback 解析

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->
<!-- Depends on: Story 9.1 (designing phase 引入了完整的 phase 链) -->
<!-- Related: Story 9.1e (creating 回退路径 prompt 修复 — 同一条 validate 链路的下游) -->

## Story

As a orchestrator 系统,
I want `validating` 阶段在 agent stdout 无法确定性解析时，回退到读取 agent 产出的验证报告文件并重新解析,
so that BMAD `validate-create-story` skill 将报告写文件（而非 stdout）的行为不会导致 story 卡死在 `validating` 状态。

## 问题背景

当前 `_dispatch_convergent_loop()` 对 `validating` 阶段的处理链：

```
dispatch agent (prompt 含 "Output format: 结果: PASS/FAIL ...")
    ↓
result.text_result = agent stdout（会话摘要）
    ↓
bmad_adapter.parse(markdown_output=result.text_result)
    ↓
_parse_story_validation() — 确定性 fast-path
    ↓
找不到 "结果: PASS/FAIL" 和已知 section headers → return None
    ↓
semantic fallback（如已注入）或 parse_failed
    ↓
parse_failed → 记录失败，return True，不提交任何 transition
    ↓
Story 卡死在 validating，人工收通知但状态机不动
```

**根因**：BMAD `validate-create-story` skill 天然把结构化验证报告写入文件（如 `{story-slug}-validation-report.md`），agent stdout 只输出会话摘要。`_CONVERGENT_LOOP_PROMPTS["validating"]` 虽然要求 agent 将格式化结果输出到 stdout，但 agent 不一定遵守——尤其是 Codex 倾向于简洁摘要。当前 `RecoveryEngine` 只能看到 `BmadAdapter.parse()` 的最终 `BmadParseResult`，看不到 deterministic fast-path 的内部中间态；因此 fallback 必须建立在公开返回合同上，而不是依赖 adapter 内部实现细节。

## Acceptance Criteria

### AC1: validating prompt 指定验证报告的确定性输出路径

```gherkin
Given `_CONVERGENT_LOOP_PROMPTS["validating"]` 的 prompt 模板
When 查看模板内容
Then 模板包含 `{validation_report_path}` 占位符
And 该占位符被解析为 `_bmad-output/implementation-artifacts/{story_id}-validation-report.md`
And prompt 仍然保留 "Output format: 结果: PASS/FAIL ..." 的 stdout 格式指令（不删除）
And prompt 新增一行指令："Also write the full validation report to {validation_report_path}"
```

### AC2: 最终解析失败时回退读取报告文件

```gherkin
Given agent dispatch 完成，result.text_result 已获取
When `bmad_adapter.parse()` 返回 `parse_result.verdict == "parse_failed"`
Then 系统尝试从 `validation_report_path` 读取文件内容
And Python 侧读取路径基于与 agent dispatch 相同的 `cwd` 解析，而不是基于 orchestrator 进程当前目录
And 当前 validating phase 下，该绝对路径等于 `Path(worktree_path) / validation_report_path`
And 将文件内容作为 markdown_output 再次调用 `bmad_adapter.parse()`
And 此次解析结果作为最终 parse_result
```

### AC3: 报告文件不存在时仍走现有 parse_failed 路径

```gherkin
Given agent dispatch 完成，stdout 解析失败
When 基于 dispatch `cwd` 解析出的报告文件不存在（agent 未产出该文件）
Then 行为与当前完全一致：verdict = "parse_failed"，记录失败，通知人工
And 不抛出异常，不影响其他 story 的处理
```

### AC4: stdout 解析成功时不触发文件回退

```gherkin
Given agent dispatch 完成，result.text_result 含 "结果: PASS" 或 "结果: FAIL"
When `bmad_adapter.parse()` 基于 stdout 返回 `verdict != "parse_failed"`
Then 不读取报告文件
And 后续 findings 入库和 transition 提交逻辑完全不变
```

### AC5: 文件回退解析的结果正确驱动状态机

```gherkin
Given 文件回退解析成功，verdict != "parse_failed"
When parse_result.findings 中有 blocking severity 的 finding
Then 提交 validate_fail 事件（story 回退到 creating）
And findings 正常写入 DB

When parse_result.findings 中没有 blocking severity 的 finding
Then 提交 validate_pass 事件（story 进入 dev_ready）
```

### AC6: 回归测试覆盖 fallback 链路

```gherkin
Given 更新后的单元测试
When 运行相关测试子集
Then 至少覆盖：
  - stdout 解析成功时不触发文件回退
  - stdout 解析失败 + 报告文件存在 → 文件回退解析成功
  - stdout 解析失败 + 报告文件不存在 → parse_failed
  - stdout 解析失败 + 报告文件存在但内容也无法解析 → parse_failed
  - 文件回退解析结果正确传递给 findings 入库和 transition 逻辑
```

## Tasks / Subtasks

- [x] Task 1: 更新 validating prompt 模板添加报告文件路径 (AC: #1)
  - [x] 1.1 在 `recovery.py` 的 `_CONVERGENT_LOOP_PROMPTS["validating"]` 中新增 `{validation_report_path}` 占位符和文件输出指令
  - [x] 1.2 在 prompt format 调用处补充 `validation_report_path` 参数，路径规则：`_bmad-output/implementation-artifacts/{story_id}-validation-report.md`
  - [x] 1.3 不删除现有 stdout 格式指令（作为第一优先级解析源）

- [x] Task 2: 在 `_dispatch_convergent_loop()` 中实现 artifact-file fallback (AC: #2, #3, #4, #5)
  - [x] 2.1 保持 `BmadAdapter` 为黑盒：仅在 `parse_result.verdict == "parse_failed"` 时触发 validating-only fallback，不依赖 deterministic fast-path 的内部返回值
  - [x] 2.2 构造与 prompt 一致的相对 `validation_report_path`，并基于 dispatch `cwd` 解析出绝对读取路径（当前实现为 `Path(worktree_path) / validation_report_path`）
  - [x] 2.3 检查绝对读取路径是否存在；存在则 `Path.read_text()` 读取并再次调用 `bmad_adapter.parse()`
  - [x] 2.4 文件不存在或文件内容也无法解析 → 保持原有 `parse_failed` 流程
  - [x] 2.5 文件解析成功 → 用新 `parse_result` 替换原值，继续走 findings 入库 + transition 逻辑
  - [x] 2.6 仅对 `validating` phase 启用此 fallback，不影响 `reviewing` / `qa_testing` 的解析流程
  - [x] 2.7 添加 structlog 日志标记 fallback 触发事件（`"convergent_loop_file_fallback_triggered"`）

- [x] Task 3: 增加 fallback 链路的回归测试 (AC: #6)
  - [x] 3.1 在 `tests/unit/test_recovery.py` 中新增 `test_validating_stdout_success_no_file_fallback`
  - [x] 3.2 新增 `test_validating_stdout_fail_file_exists_fallback_success`
  - [x] 3.3 新增 `test_validating_file_fallback_reads_from_dispatch_cwd`（验证 fallback 读取的是 worktree 相对路径，不是 orchestrator cwd）
  - [x] 3.4 新增 `test_validating_stdout_fail_file_missing_parse_failed`
  - [x] 3.5 新增 `test_validating_stdout_fail_file_unparseable_parse_failed`
  - [x] 3.6 新增 `test_validating_file_fallback_findings_drive_transition`（验证文件解析结果正确触发 validate_pass / validate_fail）

## Dev Notes

### 关键实现判断

- **Prompt 仍然优先要求 stdout 格式化输出。** 文件回退是防御性机制，不是替代 stdout 解析。如果 agent 遵守了 stdout 格式指令，文件回退永远不会触发。
- **只改 `validating` phase 的流程。** `reviewing` 和 `qa_testing` 使用 `bmad-code-review` 等 skill，这些 skill 的输出模式不同（通常直接输出到 stdout），不存在同类问题。如果后续发现这些 phase 也有同类问题，应单独立 story。
- **把 `BmadAdapter` 当作黑盒。** `RecoveryEngine` 不应依赖 deterministic fast-path 的内部返回值或 adapter 私有 helper；触发条件应建立在公开 `parse_result.verdict == "parse_failed"` 合同之上。
- **报告文件路径必须确定性可推导。** 不依赖 agent 返回路径（因为 stdout 可能就是解析不了）。路径规则 `{story_id}-validation-report.md` 与项目现有文件命名约定一致。
- **Prompt 路径与 Python 读取路径不是同一个语义层。** prompt 里给 agent 的应是相对 `cwd` 的可写路径；Python fallback 读取时必须基于同一个 dispatch `cwd` 解析绝对路径，不能直接按 orchestrator 进程 cwd 读取相对路径。
- **文件回退解析复用 `bmad_adapter.parse()` 全流程。** 不需要新的 parser——报告文件的内容就是 `_parse_story_validation()` 设计时预期的结构（含 "结果: PASS/FAIL"、section headers 等）。
- **fallback 不改变 `BmadAdapter` 接口。** 变更范围限制在 `_dispatch_convergent_loop()` 内部，adapter 层保持纯函数语义。

### Scope Boundary

- **IN:** `_CONVERGENT_LOOP_PROMPTS["validating"]` prompt 修订、`_dispatch_convergent_loop()` 内 validating-only 的文件回退逻辑、相关单元测试
- **OUT:** 修改 `BmadAdapter` 接口或 `_parse_story_validation()` 解析逻辑
- **OUT:** 为 `reviewing` / `qa_testing` 添加类似 fallback
- **OUT:** 修改 `convergent_loop.py` 中 `ConvergentLoop` 类的逻辑（那是 reviewing/fixing 循环）
- **OUT:** 修改 DB schema 或 findings 持久化逻辑

### Project Structure Notes

- 主要修改文件：
  - `src/ato/recovery.py` — prompt 模板 + `_dispatch_convergent_loop()` 内 fallback 逻辑
- 重点测试文件：
  - `tests/unit/test_recovery.py`
- 只读依赖：
  - `src/ato/adapters/bmad_adapter.py` — 复用 `parse()` 方法
  - `src/ato/design_artifacts.py` — 复用 `ARTIFACTS_REL` 常量

### Suggested Verification

```bash
uv run pytest tests/unit/test_recovery.py -v -k "validating"
```

## References

- [Source: src/ato/recovery.py — `_CONVERGENT_LOOP_PROMPTS["validating"]`, `_dispatch_convergent_loop()`]
- [Source: src/ato/adapters/bmad_adapter.py — `BmadAdapter.parse()`, `_parse_story_validation()`, `_SV_RESULT_RE`]
- [Source: src/ato/design_artifacts.py — `ARTIFACTS_REL` 常量]
- [Source: _bmad-output/implementation-artifacts/9-1e-creating-phase-prompt-validation-feedback.md — 同一 validate 链路的下游修复]
- [Source: git commit 2be02cc — `_SV_RESULT_RE` i18n 正则修复先例]

### Previous Story Intelligence

1. **Story 9.1 已建立 `creating → designing → validating` 链路。** 9.1f 不改链路顺序，只修 `validating` 阶段从 agent 获取解析内容的方式。
2. **Story 9.1e 修 `validate_fail → creating` 回退后的 prompt。** 9.1f 在上游：先让 `validating` 能正确出 `validate_pass` / `validate_fail`，9.1e 再让回退后的 `creating` 能带反馈重建。
3. **提交 `2be02cc` 已修复 `_SV_RESULT_RE` 英文正则。** 确认报告文件内容（含 "结果: PASS"）可被确定性解析器识别——文件回退的前提条件。
4. **`_dispatch_convergent_loop()` 的 `parse_failed` 分支已有 `record_parse_failure()` + 通知机制。** fallback 失败时直接复用，不需要新增错误处理。

## Change Log

- 2026-03-28: Story 创建 — 基于对 validate-create-story 输出不匹配解析器预期的分析，新增 artifact-file fallback corrective story
- 2026-03-28: `validate-create-story` 修订 —— 将 fallback 触发条件收紧到 `parse_result.verdict == “parse_failed”` 公开合同；补回”基于 dispatch cwd 解析报告文件绝对路径”的实现约束与测试覆盖；移除易漂移的行号引用
- 2026-03-29: 实现完成 — prompt 模板新增 `{validation_report_path}`，`_dispatch_convergent_loop()` 新增 validating-only 文件回退逻辑，6 个回归测试全部通过
- 2026-03-29: 修复 review findings — (1) fallback read_text 异常用 try/except 捕获，避免升级为 dispatch_failed；(2) 新增 AC1 prompt 回归测试锁住 validation_report_path 指令

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6 (1M context)

### Debug Log References

### Completion Notes List

- ✅ Task 1: 更新 `_CONVERGENT_LOOP_PROMPTS[“validating”]` 模板，新增 `{validation_report_path}` 占位符和 “Also write the full validation report to” 指令；在 `prompt_template.format()` 调用处补充路径参数（使用 `ARTIFACTS_REL` 常量）；保留现有 stdout 格式指令不变
- ✅ Task 2: 在 `_dispatch_convergent_loop()` 的 `parse_result.verdict == “parse_failed”` 分支中，对 `validating` phase 新增文件回退逻辑：构造 `Path(worktree_path) / ARTIFACTS_REL / {story_id}-validation-report.md` 绝对路径，存在则 `read_text` 并重新 `bmad.parse()`；成功则替换 `parse_result` 继续正常流程，失败则保持原 `parse_failed` 路径；仅限 `validating` phase，不影响其他 convergent_loop phase；添加 `convergent_loop_file_fallback_triggered` structlog 日志
- ✅ Task 3: 新增 `TestValidatingFileFallback` 测试类，包含 9 个测试用例覆盖所有 fallback 路径：stdout 成功不触发回退、文件回退成功、基于 dispatch cwd 读取、文件不存在、文件不可解析、findings 驱动 validate_pass/validate_fail、read_text 异常不升级为 dispatch_failed、prompt 模板包含占位符、格式化 prompt 包含具体路径

### File List

- src/ato/recovery.py（修改）— prompt 模板 + fallback 逻辑
- tests/unit/test_recovery.py（修改）— 新增 6 个回归测试
