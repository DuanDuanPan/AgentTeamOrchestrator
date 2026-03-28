# Story 验证报告：9.1d Prototype Manifest 与下游消费契约

验证时间：2026-03-28  
Story 文件：`_bmad-output/implementation-artifacts/9-1d-prototype-manifest-downstream-consumption.md`  
验证模式：`validate-create-story`  
结果：PASS（已应用修正）

## 摘要

原始 9.1d 草稿抓住了真正的目标：在 designing 产物之上新增一个稳定的 `prototype.manifest.yaml`，让开发、验证、评审不再猜 UX 工件怎么消费。

但原稿仍有 3 个会把 dev 明显带偏的实现缺口：

1. 它把 AC3 的下游消费几乎全部压到 `_format_structured_job_prompt()`，但当前 validating / developing / reviewing 实际走的是三套不同的 prompt 构建面。
2. 它一边新增 `write_prototype_manifest()` helper，一边又让 designing agent 手写 manifest，没有给出可信的 Python 调用落点，最终会让 helper 变成摆设，gate 也无法把 manifest 当成可靠合同。
3. 它仍带着已经过时的依赖与引用假设，例如默认要 `uv add pyyaml`、继续使用易漂移的精确代码行号，以及没有把 mixed-base path 校验讲清楚。

本次验证后，9.1d 已收敛为一个能直接指导实现的 corrective story：manifest 由 Python 基于已保存工件确定性生成，下游消费落在真实运行入口，gate 合同也和当前代码结构对齐了。

## 已核查证据

- 当前代码：
  - `src/ato/design_artifacts.py`
  - `src/ato/core.py`
  - `src/ato/recovery.py`
  - `src/ato/convergent_loop.py`
  - `src/ato/preflight.py`
  - `pyproject.toml`
- 当前测试布局：
  - `tests/unit/test_design_artifacts.py`
  - `tests/unit/test_core.py`
  - `tests/unit/test_recovery.py`
  - `tests/unit/test_convergent_loop.py`
- 相关前序 story：
  - `_bmad-output/implementation-artifacts/9-1b-designing-force-save-snapshot-chain.md`
  - `_bmad-output/implementation-artifacts/9-1c-design-gate-v2-persistence-verification.md`
  - `_bmad-output/implementation-artifacts/9-1e-creating-phase-prompt-validation-feedback.md`
  - `_bmad-output/implementation-artifacts/9-1f-validating-artifact-file-fallback-parse.md`
- 规划来源：
  - `_bmad-output/planning-artifacts/epics.md`
  - `_bmad-output/planning-artifacts/sprint-change-proposal-2026-03-28-designing-artifact-persistence.md`

## 发现的关键问题

### 1. 原稿选错了 AC3 的实现落点，改 `_format_structured_job_prompt()` 不能覆盖真实下游阶段

当前仓库里，9.1d 要求覆盖的三个下游消费面并不共用同一个 helper：

- `validating` 走 `src/ato/recovery.py::_dispatch_convergent_loop()`
- `developing` 走 `src/ato/core.py::_build_interactive_prompt()`
- `reviewing` 走 `src/ato/convergent_loop.py` 的首轮 review / re-review prompt builder

原稿把方案收敛成“给 `_format_structured_job_prompt()` 增加 phase 参数并统一附加 UX context”，但这个 helper 只服务 structured_job 模板，根本碰不到 reviewing 和 interactive developing 的实际 prompt。

已应用修正：

- AC3 改为直接点名三个真实调用面
- Tasks 中新增 `src/ato/convergent_loop.py` 与 `src/ato/core.py::_build_interactive_prompt()` 的接线要求
- 测试面扩展到 `tests/unit/test_convergent_loop.py` 与 `tests/unit/test_core.py`

### 2. 原稿把 manifest 既写成 Python helper，又写成 agent 手工产物，导致生成责任模糊

原稿一方面要求：

- 在 `design_artifacts.py` 中新增 `write_prototype_manifest()`

另一方面又要求：

- 在 designing prompt 中让 agent 自己汇总并写 `prototype.manifest.yaml`

这会留下两个问题：

- dev 很可能只实现 prompt，让 agent 手写 YAML，导致 Python helper 无落点
- gate 将不得不信任 agent 自由生成的 manifest，而不是基于磁盘真相的确定性产物

结合 Story 9.1b 已建立的分层，manifest 更适合放在 Python 侧后处理：

- agent 负责 `.pen` / snapshot / save-report / exports
- Python 基于这些已保存工件确定性生成 manifest
- gate 再验证 manifest

已应用修正：

- AC1 改成“系统基于已保存设计工件生成 manifest”
- Tasks 改为在 core / recovery 的 `design_done` 前置路径中调用共享 helper
- Dev Notes 明确 `check_design_gate()` 应保持 validator 角色，manifest 生成先于 gate

### 3. 原稿仍低于当前仓库 story 基线，并带着过时的依赖 / 路径假设

原稿还有几类会误导实现的细节：

- 默认建议 `uv add pyyaml`，但当前仓库已经通过 `pydantic-settings[yaml]` 和 `src/ato/preflight.py` 的现有 `import yaml` 使用 YAML runtime
- manifest 校验示例没有区分 `story_file` 的 project-root 相对路径和 UX 工件的 UX-dir 相对路径
- References 带精确代码行号，后续非常容易漂移
- 缺少 Scope Boundary、Suggested Verification、Change Log，低于最近几轮 validate-create-story 的 story 基线

已应用修正：

- 改为“复用现有 YAML runtime，除非运行时验证证明缺失，否则不新增依赖”
- AC4 / Dev Notes 明确 mixed-base path 合同
- References 改为文件级引用，不再写死行号
- 补回 Scope Boundary、Suggested Verification、Change Log

## 已应用增强

- 将 manifest 设计从“agent 自由手写的附加说明文件”收紧为“基于 snapshot / exports / save-report 的确定性索引合同”
- 把 reviewing 消费面显式纳入 `convergent_loop.py`，避免只修 recovery / structured_job 路径
- 将测试计划扩展到四个真实影响面：artifact helper、gate、validating prompt、reviewing prompt
- 让 story 和 9.1b / 9.1c / 9.1e / 9.1f 的 corrective 风格保持一致，便于后续 dev-story 延续

## 剩余风险

- 本次只修订了 story 与 validation report，没有实现 Python 代码，也没有运行测试。
- 若后续 Story 9.2 调整 validating / reviewing 的 workspace 语义，实现时应保持“按真实 dispatch 根路径解析 manifest / UX 相对路径”的抽象，不要把路径逻辑写死到当前 main/worktree 现状。

## 最终结论

修正后，9.1d 已从“目标正确，但实现落点和产物责任仍然模糊”的草稿，收敛成一个可直接交给 dev-story 的 corrective story。最关键的偏差已经移除：不会再把 AC3 错压到 `_format_structured_job_prompt()`，也不会再让 gate 去赌 designing agent 是否手写出了可靠 manifest。
