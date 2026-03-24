# Story 验证报告：2B.2 Codex Agent Review

验证时间：2026-03-24 22:21:26 CST
Story 文件：`_bmad-output/implementation-artifacts/2b-2-codex-agent-review.md`
验证模式：`validate-create-story`
结果：PASS（已应用修正）

## 摘要

该 story 存在若干有证据支撑的缺口，若按原文实现，极可能产出错误的 Codex adapter：

1. 它假设了旧版 JSONL 事件形态（`item.content[].text`），与当前已安装 Codex CLI 不符。
2. 它把 Codex 当作“不提供 cache token telemetry”，但当前 CLI 实际会输出 `cached_input_tokens`。
3. 它忽略了 cached input 定价，会导致成本高估。
4. 它遗漏了 `--output-schema` 指导，而架构调研和官方文档都建议用它生成稳定的 machine-readable 输出。
5. 它遗漏了 `cwd` / worktree 执行约束，并给出了不安全的临时文件建议（`tempfile.mktemp()`）。
6. 它没有要求在 `SubprocessManager` 中补充 Codex telemetry 的持久化测试。

## 已核查证据

- 本地仓库工件：
  - `_bmad-output/planning-artifacts/epics.md`
  - `_bmad-output/planning-artifacts/architecture.md`
  - `_bmad-output/planning-artifacts/prd.md`
  - `_bmad-output/planning-artifacts/research/technical-claude-codex-cli-integration-research-2026-03-24.md`
  - `src/ato/models/schemas.py`
  - `src/ato/subprocess_mgr.py`
  - `src/ato/adapters/claude_cli.py`
  - `tests/unit/test_subprocess_mgr.py`
  - `tests/unit/test_schemas.py`
- 本地 Codex CLI 验证：
  - `codex --version` → `codex-cli 0.116.0`
  - `codex exec --help`
  - 真实执行探针：`codex exec --json --ephemeral --sandbox read-only -o ...`
- OpenAI 官方资料：
  - https://developers.openai.com/codex/noninteractive
  - https://developers.openai.com/api/docs/models/codex-mini-latest

## 发现的关键问题

### 1. 事件结构指导过期

story 原文要求从 `item.content[].text` 读取最终文本。但对已安装 CLI 的实时探针返回：

```json
{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"{\"ok\":true}"}}
```

若按原文实现，`_extract_text_result()` 在当前 CLI 上会漏掉最终消息。

已应用修正：
- 更新 AC、Tasks 和 Dev Notes，优先使用 `item.text`，同时保留对旧版 `item.content[].text` 样本的向后兼容。

### 2. 缺失 cached token telemetry 与定价

story 原文声称 Codex 不暴露 cache token。但当前 CLI 实际输出：

```json
{"type":"turn.completed","usage":{"input_tokens":26024,"cached_input_tokens":10624,"output_tokens":29}}
```

官方 `codex-mini-latest` 页面也明确列出了 cached input 独立定价。若忽略这一点，会同时造成 telemetry 和成本核算错误。

已应用修正：
- 在 `CodexOutput` 中新增 `cache_read_input_tokens` 映射
- 更新 usage 聚合要求
- 更新成本公式和价格表，纳入 `cached_input_per_1m`
- 要求为 Codex cache/model telemetry 增补 `SubprocessManager` 持久化测试

### 3. 缺失 structured-output 契约

story 原文只依赖 `-o` 生成结构化 findings。官方 Codex 文档明确建议使用 `--output-schema` 以获得稳定的 machine-readable 输出。

已应用修正：
- 在 AC、Tasks、Dev Notes 和命令构建测试中加入 `output_schema` / `--output-schema` 支持
- 明确本 story 只负责 adapter 能力，不负责交付生产用 schema 文件；正式 schema 工件可由后续 story 提供

### 4. 缺失 repo / worktree 执行约束

story 没有要求 `cwd` 支持；但现有 Claude adapter 已使用该能力，且架构文档明确要求 agent subprocess 在目标 repo/worktree 内运行。

已应用修正：
- 在 AC 和 execute 任务说明中加入 `cwd=options["cwd"]`。

## 已应用增强

- 明确成功路径下的 stderr 可能包含进度输出，不能仅因 stderr 非空就判定失败
- 用 `TemporaryDirectory()` / `mkdtemp()` 替换不安全的 `tempfile.mktemp()` 指导
- 扩展测试要求，覆盖当前事件结构、旧版 fallback、structured output 解析以及 `SubprocessManager` 持久化

## 剩余风险

- 仓库内尚不存在正式的 `schemas/review-findings.json` 文件。当前 story 已正确收敛边界：这里只实现 adapter 对 `--output-schema` 的支持，生产 schema 工件留给后续 story 交付。
- cached input 成本公式使用了“文档支持但仍属推断”的关系：`input_tokens` 包含 cached input，因此用减法推导 uncached input。应通过 snapshot fixture 持续守护这一假设。

## 最终结论

修正完成后，该 story 已明显增强，仍适合保持 `ready-for-dev`。高风险实现歧义已被移除，story 现已反映 2026-03-24 观察到的当前 Codex CLI 行为。
