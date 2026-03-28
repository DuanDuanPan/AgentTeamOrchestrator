# Story 验证报告：9.1f validating 阶段 artifact-file fallback 解析

验证时间：2026-03-28  
Story 文件：`_bmad-output/implementation-artifacts/9-1f-validating-artifact-file-fallback-parse.md`  
验证模式：`validate-create-story`  
结果：PASS（已应用修正）

## 摘要

原始 9.1f 草稿已经抓住了真正故障面：`validate-create-story` 往往把完整报告写到文件，stdout 只有会话摘要，导致 `RecoveryEngine._dispatch_convergent_loop()` 在 `validating` 阶段走到 `parse_failed` 后卡住在人工介入路径。

但原稿仍有 3 个会继续把 dev 带偏的实现缺口：

1. 它把 fallback 触发条件写成了“deterministic fast-path 返回 None”，而当前 `RecoveryEngine` 只能看到 `BmadAdapter.parse()` 的最终 `BmadParseResult`，看不到 adapter 内部中间态。
2. 它把 prompt 里的相对 `validation_report_path` 直接当成 Python 读取路径来描述，却没有明确该路径必须基于 agent dispatch 的 `cwd` 解析；否则 validating 当前在 worktree 中执行时，读取会落到 orchestrator 进程目录。
3. 它的 References 仍带精确行号，和仓库近几轮 validate-create-story 已收紧的“避免易漂移引用”基线不一致。

本次验证后，story 已收敛为一个基于公开接口、不会把实现写偏到错误目录解析或 adapter 私有细节上的 corrective spec。

## 已核查证据

- 当前代码：
  - `src/ato/recovery.py`
  - `src/ato/adapters/bmad_adapter.py`
  - `src/ato/design_artifacts.py`
- 当前测试布局：
  - `tests/unit/test_recovery.py`
  - `tests/unit/test_bmad_adapter.py`
- 相关上下文：
  - `_bmad-output/implementation-artifacts/9-1e-creating-phase-prompt-validation-feedback.md`
  - `_bmad-output/implementation-artifacts/sprint-status.yaml`
- 近期提交：
  - `f086063`（9.1b 设计持久化链路）
  - `bceb455`（9.1a 合同修正）
  - `2be02cc`（story validation 英文 `Result:` regex 修复）

## 发现的关键问题

### 1. 原稿依赖了 `BmadAdapter` 的内部中间态，当前 recovery 根本拿不到

当前 `src/ato/adapters/bmad_adapter.py` 的公开合同是：

- `BmadAdapter.parse()` 返回最终 `BmadParseResult`
- `parser_mode` 只暴露 `deterministic` / `semantic_fallback` / `failed`
- `RecoveryEngine` 并不能直接观察 “deterministic fast-path 返回 None” 这个内部瞬间

如果按原稿去实现“在 `parse_result.verdict == "parse_failed"` 之前检查 deterministic fast-path 是否失败”，开发者要么会错误侵入 adapter 内部，要么会新增不必要的返回信号，扩大本 story 范围。

已应用修正：

- AC2 改成基于公开 `parse_result.verdict == "parse_failed"` 合同触发 fallback
- Task 2.1 明确要求把 `BmadAdapter` 当黑盒，不依赖内部 helper / 中间态
- Dev Notes 补充该实现边界

### 2. 原稿没有把“agent 写文件的相对路径”和“Python 读取文件的绝对路径”区分清楚

当前 `recovery.py::_dispatch_convergent_loop()` 对 `validating` 的 dispatch 是：

- prompt 传给 agent 的路径是相对其 `cwd` 的字符串
- `dispatch_opts["cwd"]` 当前等于 `worktree_path`

这意味着 agent 写出的 `_bmad-output/implementation-artifacts/{story_id}-validation-report.md` 位于 worktree 内，而不是按 orchestrator 进程当前目录解析。

如果实现时直接 `Path(validation_report_path).exists()`，就会把 fallback 读路径落到错误目录，尤其在 validating 仍运行于 worktree 的当前仓库版本下会直接失效。

已应用修正：

- AC2 明确 Python 读取路径必须基于与 agent dispatch 相同的 `cwd` 解析
- Task 2.2/2.3 收紧为“prompt 用相对路径，fallback 用绝对读取路径”
- Tasks 中新增 `test_validating_file_fallback_reads_from_dispatch_cwd`

### 3. 原稿 References 仍使用精确行号，低于当前仓库 story 基线

近期已验证的 corrective stories 已经反复收紧到：

- 引用真实文件/函数
- 避免写死易漂移的行号

9.1f 原稿还在 References 里使用精确行段，会让后续 traceability 很快失效。

已应用修正：

- References 改为文件 + 符号级引用
- Change Log 记录本次 validate-create-story 的修订点

## 已应用增强

- 将 fallback 语义从“内部 parser 细节驱动”收紧为“公开 parse 结果驱动”
- 把当前 validating phase 的 worktree `cwd` 现实约束补入 story，避免路径解析类伪实现
- 增补针对 dispatch `cwd` 的回归测试要求，防止未来 9.2 workspace 重构前后再次回归

## 剩余风险

- 当前 story 仍以“validating 运行在 worktree”这一现状写约束；若后续 Story 9.2 改变 validating workspace，dev 实现应复用“按 dispatch cwd 解析”的抽象，而不是把逻辑永久写死到 `worktree_path`。
- 本次只修订了 story 与 validation report，没有实现 Python 代码，也没有运行测试。

## 最终结论

修正后，9.1f 已从“问题识别正确，但实现条件和路径解析语义仍会误导开发者”的草稿，收敛成了一个可直接交给 dev-story 的 corrective story。最关键的偏差已经移除：不会再去窥探 `BmadAdapter` 内部状态，也不会再把 report fallback 读到错误目录。
