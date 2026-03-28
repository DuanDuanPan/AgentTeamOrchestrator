# Story 验证报告：8.1 角色配置简化 — model/sandbox 改为可选

验证时间：2026-03-28  
Story 文件：`_bmad-output/implementation-artifacts/8-1-role-config-simplify-model-sandbox-optional.md`  
验证模式：`validate-create-story`  
结果：PASS（已应用修正）

## 摘要

原始 8.1 草稿方向是对的，但它把这次变更当成了“`config.py` + `codex_cli.py` 的局部调整”，遗漏了多个真实运行路径，直接导致 story 还不能安全交给 dev-story。

本次验证后，已将 story 收敛为一个真正可执行的实现合同，核心修正有 4 项：

1. 补上了 `convergent_loop.py`、`recovery.py`、`core.py` 这些会继续偷偷注入默认 `sandbox` / 忽略显式 `model` 的路径。
2. 把测试面从“只改 `test_config.py`”扩展到真实会失败的 `test_codex_adapter.py`、`test_recovery.py`、`test_core.py`、`test_config_workflow.py`。
3. 明确了 `calculate_cost(model=None)` 的安全降级合同，避免开发时把 `None` 又偷偷补回 `"codex-mini-latest"`。
4. 补回了 create-story 模板要求的 validation note、Change Log、Dev Agent Record 结构，避免 story 本身继续低于仓库的已验证基线。

## 已核查证据

- 规划与规范工件：
  - `_bmad-output/planning-artifacts/prd.md`
  - `_bmad-output/planning-artifacts/architecture.md`
  - `_bmad-output/planning-artifacts/research/technical-claude-codex-cli-integration-research-2026-03-24.md`
- 前序相关 story：
  - `_bmad-output/implementation-artifacts/1-3-declarative-config-engine.md`
  - `_bmad-output/implementation-artifacts/2b-2-codex-agent-review.md`
- 当前代码：
  - `src/ato/config.py`
  - `src/ato/adapters/codex_cli.py`
  - `src/ato/convergent_loop.py`
  - `src/ato/recovery.py`
  - `src/ato/core.py`
  - `ato.yaml.example`
  - `tests/unit/test_config.py`
  - `tests/integration/test_config_workflow.py`
  - `tests/unit/test_codex_adapter.py`
  - `tests/unit/test_recovery.py`

## 发现的关键问题

### 1. 原稿忽略了 Convergent Loop 与 recovery 的硬编码 sandbox

原 story 只要求修改：

- `src/ato/config.py`
- `src/ato/adapters/codex_cli.py`

但当前真实代码里还有这些硬编码路径：

- `src/ato/convergent_loop.py` 首轮 review / re-review 写死 `sandbox="read-only"`
- `src/ato/recovery.py::_build_dispatch_options()` 对 Codex fallback 到 `workspace-write`
- `src/ato/recovery.py::_dispatch_convergent_loop()` 继续做 `phase_cfg.get("sandbox", "read-only")`

这意味着开发者如果按原稿实现，平时配置加载看起来已经支持“sandbox 可省略”，但一到 review / recovery 路径，系统还是会继续偷偷塞默认值，AC2 会在真实运行时失败。

已应用修正：

- Story Task 3 明确扩展到 `convergent_loop.py`、`recovery.py`
- AC2 / AC3 改成覆盖普通 dispatch、Convergent Loop、recovery 和 restart 路径，而不是只盯着 adapter

### 2. 原稿没有覆盖 restart 路径，AC3 会在重启场景失效

当前 `src/ato/core.py::_dispatch_batch_restart()` 只传 `cwd`，根本不传 phase-derived `model` / `sandbox`。

因此如果用户显式配置了：

- `model: opus`
- `sandbox: read-only`

原稿里的“显式指定仍生效”在 restart 场景下并不成立。开发者若只看原稿，很容易把这个漏掉。

已应用修正：

- AC3 明确把 restart 路径纳入合同
- Task 3.5 明确要求 `_dispatch_batch_restart()` 复用 phase-derived options，而不是继续只传 `cwd`

### 3. 原稿低估了测试回归面

原 story 只提到：

- `test_config.py`
- 新增少量命令参数测试

但当前仓库实际会受影响的测试至少包括：

- `tests/unit/test_codex_adapter.py` 目前还断言默认命令包含 `--sandbox read-only`
- `tests/unit/test_codex_adapter.py` 成功执行测试默认期待 `model_name == "codex-mini-latest"` 且 `cost_usd > 0`
- `tests/unit/test_recovery.py` 还断言 structured job recovery 默认传 `sandbox=workspace-write`
- `tests/unit/test_config.py` / `tests/integration/test_config_workflow.py` 还把 `ato.yaml.example` 中出现 reviewer `read-only sandbox` 当作模板合同

如果 story 不明确这些测试会跟着变，dev 很容易以为只是补几个新测试，结果跑套件时才发现基线本身要改。

已应用修正：

- Story Task 5/6/7 按文件列出了要改的现有测试与新增断言
- 特别写清了 `test_codex_adapter.py` 的默认成本断言也要跟着调整

### 4. 原稿没有把 `model=None` 的成本合同讲清楚

原稿虽然提到：

- `calculate_cost()` 处理 `model=None`

但没有把这件事提升为 acceptance contract，也没有说明 `CodexOutput.model_name` 在这种场景下应为 `None`。

缺少这层说明，开发者很容易为了“让现有测试继续绿”而再次把 `None` 映射回 `"codex-mini-latest"`，从而破坏 Story 的真实目标。

已应用修正：

- 新增 AC5，明确 `model_name is None + cost_usd == 0.0 + warning`
- Task 2 / Task 6 对齐到 adapter 行为与测试合同

### 5. 原稿本身还没达到仓库当前 create-story 质量基线

和仓库中已验证的 story 相比，原稿缺少：

- 模板 validation note 注释
- Change Log
- Dev Agent Record
- 更完整的架构护栏与 scope boundary

这会让后续 dev-story 缺少统一的工作记录结构，也不利于 traceability。

已应用修正：

- 已补回 validation note
- 已新增 Change Log
- 已补全 Dev Agent Record 骨架
- 已补充 Project Structure / References / Scope Boundary / Previous Story Intelligence

## 已应用增强

- 将 story 从“局部代码修改清单”升级为“跨 config / adapter / runtime / recovery / restart 的一致性合同”
- 将 PRD、架构、技术调研、Story 1.3 的信息合并进 Dev Notes，降低开发误判概率
- 明确建议抽取共享 options builder，避免未来再次在多处写出不一致默认值

## 剩余风险

- `_bmad-output/planning-artifacts/epics.md` 中当前没有正式的 Epic 8 / Story 8.1 章节；本次验证是根据 PRD、architecture、技术调研、`sprint-status.yaml` 和现有 story 草稿交叉还原需求意图的。这个 story 现在已经足够 dev-ready，但 Epic 8 的上游规划文档仍建议后续补齐。
- 本次只修订了 story 和 validation report，没有实现 Python 代码，也没有运行测试；目标是先把实现合同修正到不会误导开发。

## 最终结论

修正后，8.1 已经从“意图正确但实现面严重缩窄”的草稿，收敛成了一个可直接交给 dev-story 执行的 story。高风险误导点已移除：不会再让开发者误以为只改 `config.py` / `codex_cli.py` 就够，也不会再漏掉 recovery / restart / Convergent Loop 这些会继续注入默认值的路径。
