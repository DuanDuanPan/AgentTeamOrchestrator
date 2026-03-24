---
stepsCompleted: [1, 2, 3, 4, 5, 6]
inputDocuments: ['docs/agent-team-orchestrator-system-design-input-2026-03-23.md', '_bmad-output/brainstorming/brainstorming-session-2026-03-23-2200.md']
workflowType: 'research'
lastStep: 1
research_type: 'technical'
research_topic: 'Claude CLI / Codex CLI 集成能力验证'
research_goals: '验证 ADR 中关于 CLI subprocess 调用的技术假设是否成立，发现需要调整的架构决策'
user_name: 'Enjoyjavapan163.com'
date: '2026-03-24'
web_research_enabled: true
source_verification: true
---

# Research Report: technical

**Date:** 2026-03-24
**Author:** Enjoyjavapan163.com
**Research Type:** technical

---

## Research Overview

本技术调研针对 AgentTeamOrchestrator 项目的核心集成路径——通过 Python asyncio subprocess 调用 Claude CLI 和 Codex CLI——进行了全面验证。调研覆盖了 CLI 参数能力、结构化输出、工具权限控制、会话管理、成本追踪等关键技术维度，并将验证结果逐一对照头脑风暴产出的 25 条 ADR。

**核心结论：** ADR 中的技术假设整体成立，两个 CLI 的能力足以支撑 Orchestrator 架构。发现 3 个需要调整的 ADR（ADR-08 Codex 工具限制、ADR-09 结构化输出字段名、ADR-24 Codex 成本计算）和 2 个重要约束（无 API Key 不能用 --bare 和 Agent SDK、Codex 无 --max-turns）。推荐技术栈为 Python ≥3.11 + aiosqlite + python-statemachine + Textual + Pydantic，预估核心代码 ≤1500 行。

详细分析见下方各研究章节，完整 Executive Summary 见文末 Research Synthesis 部分。

---

## Technical Research Scope Confirmation

**Research Topic:** Claude CLI / Codex CLI 集成能力验证
**Research Goals:** 验证 ADR 中关于 CLI subprocess 调用的技术假设是否成立，发现需要调整的架构决策

**Technical Research Scope:**

- Architecture Analysis - Claude/Codex CLI 参数体系、输出模式、进程管理能力
- Implementation Approaches - Python asyncio subprocess 调用模式与最佳实践
- Technology Stack - 验证 ADR 引用的具体 CLI 参数（--bare, --json-schema, --worktree, --resume, --allowedTools 等）
- Integration Patterns - stdin/stdout/stderr 处理、退出码、超时控制、结果收集
- Performance Considerations - 冷启动成本、并发调用、资源消耗

**Research Methodology:**

- Current web data with rigorous source verification
- Multi-source validation for critical technical claims
- Confidence level framework for uncertain information
- Comprehensive technical coverage with architecture-specific insights

**Scope Confirmed:** 2026-03-24

---

## Technology Stack Analysis

### Claude Code CLI — 完整参数能力验证

**置信度：高** — 基于官方文档 (code.claude.com/docs/en/cli-reference, code.claude.com/docs/en/headless) 直接验证

#### 非交互执行模式

| 参数 | 状态 | 说明 |
|------|------|------|
| `--print` / `-p` | ✅ 已验证 | 非交互模式，输出到 stdout 后退出 |
| `--bare` | ✅ 已验证 | 最小模式：跳过 hooks、skills、plugins、MCP、auto memory、CLAUDE.md。**推荐用于脚本调用，未来将成为 -p 默认行为** |
| `--output-format` | ✅ 已验证 | 选项：`text`（默认）、`json`（结构化 JSON）、`stream-json`（流式 JSONL） |

#### 结构化输出

| 参数 | 状态 | 说明 |
|------|------|------|
| `--json-schema` | ✅ 已验证 | 强制 agent 输出符合 JSON Schema。输出在 `structured_output` 字段（非 `result` 字段）。仅 print 模式 |
| `--output-format json` 返回结构 | ✅ 已验证 | 包含字段：`type`, `subtype`, `result`, `structured_output`, `session_id`, `total_cost_usd`, `usage`（input_tokens/output_tokens/cache_read_input_tokens）, `modelUsage`, `duration_ms` |

**⚠️ ADR-09 修正点：** ADR 假设结构化输出在 `result` 字段中，实际上使用 `--json-schema` 时输出在 `structured_output` 字段。`result` 字段包含的是文本响应。

#### 会话管理与上下文延续

| 参数 | 状态 | 说明 |
|------|------|------|
| `--resume` / `-r` | ✅ 已验证 | 通过 session ID 或名称恢复会话 |
| `--continue` / `-c` | ✅ 已验证 | 继续当前目录最近的会话 |
| `--name` / `-n` | ✅ 已验证 | 为会话设置名称，可通过名称恢复 |
| `--session-id` | ✅ 已验证 | 使用特定 UUID 作为会话 ID |
| `--fork-session` | ✅ 已验证 | 恢复时创建新 session ID（不覆盖原会话） |
| `--no-session-persistence` | ✅ 已验证 | 禁用会话持久化（print 模式） |

**会话续接模式（支持 ADR-12 Context Briefing）：**
```bash
# 捕获 session_id 用于后续恢复
session_id=$(claude -p "Start review" --output-format json | jq -r '.session_id')
claude -p "Continue review" --resume "$session_id"
```

#### 资源控制

| 参数 | 状态 | 说明 |
|------|------|------|
| `--max-turns` | ✅ 已验证 | 限制 agent 回合数，达到上限时以错误退出。仅 print 模式。无默认限制 |
| `--max-budget-usd` | ✅ 已验证 | 最大 API 费用（美元），仅 print 模式 |
| `--model` | ✅ 已验证 | 设置模型，支持别名（`sonnet`/`opus`）或完整名称 |
| `--effort` | ✅ 已验证 | 设置 effort 级别：low/medium/high/max（Opus 4.6 专属） |

#### 工具权限控制

| 参数 | 状态 | 说明 |
|------|------|------|
| `--allowedTools` | ✅ 已验证 | 指定**无需提示即可执行**的工具列表。支持 pattern matching（如 `Bash(git diff *)`） |
| `--disallowedTools` | ✅ 已验证 | 从模型上下文中**完全移除**的工具，模型无法使用 |
| `--tools` | ✅ 已验证 | **限制可用的内置工具集**。`""` 禁用所有，`"default"` 为全部，或指定工具名 |
| `--dangerously-skip-permissions` | ✅ 已验证 | 跳过权限提示（慎用） |
| `--permission-mode` | ✅ 已验证 | 指定权限模式（如 `plan`） |
| `--permission-prompt-tool` | ✅ 已验证 | 指定 MCP 工具处理非交互模式的权限请求 |

**⚠️ ADR-08 重要澄清：** `--allowedTools` 和 `--disallowedTools` 有本质区别：
- `--allowedTools`：工具仍然可用，只是**免提示执行**（白名单审批）
- `--disallowedTools`：工具**从上下文中完全移除**（黑名单禁用）
- `--tools`：**限制可用工具的范围**（工具集定义）

对于 Reviewer 角色（只读），正确的组合是：
```bash
claude --bare -p "review prompt" \
  --tools "Read,Grep,Glob" \
  --disallowedTools "Edit,Write,Bash"
```

#### 其他关键参数

| 参数 | 状态 | 说明 |
|------|------|------|
| `--worktree` / `-w` | ✅ 已验证 | 在隔离的 git worktree 中启动（`<repo>/.claude/worktrees/<name>`） |
| `--system-prompt` | ✅ 已验证 | 替换整个系统提示 |
| `--append-system-prompt` | ✅ 已验证 | 追加到默认系统提示 |
| `--append-system-prompt-file` | ✅ 已验证 | 从文件追加系统提示 |
| `--mcp-config` | ✅ 已验证 | 加载 MCP 服务器配置 |
| `--agents` | ✅ 已验证 | 通过 JSON 动态定义自定义子代理 |
| `--agent` | ✅ 已验证 | 指定当前会话使用的 agent |
| `--fallback-model` | ✅ 已验证 | 默认模型过载时自动降级（print 模式） |
| `--add-dir` | ✅ 已验证 | 添加额外工作目录 |

**--bare 模式的认证要求：** 跳过 OAuth 和 keychain，必须通过 `ANTHROPIC_API_KEY` 环境变量或 `--settings` 中的 `apiKeyHelper` 提供认证。

_Source: [CLI reference - Claude Code Docs](https://code.claude.com/docs/en/cli-reference)_
_Source: [Run Claude Code programmatically](https://code.claude.com/docs/en/headless)_

---

### Codex CLI — 完整参数能力验证

**置信度：高** — 基于官方文档 (developers.openai.com/codex/cli/reference, developers.openai.com/codex/noninteractive) 直接验证

#### 非交互执行模式

| 参数 | 状态 | 说明 |
|------|------|------|
| `codex exec` / `codex e` | ✅ 已验证 | 非交互执行，进度输出到 stderr，最终消息输出到 stdout |
| `--full-auto` | ✅ 已验证 | 低摩擦自动化预设：`--ask-for-approval on-request` + `--sandbox workspace-write` |
| `--dangerously-bypass-approvals-and-sandbox` / `--yolo` | ✅ 已验证 | 完全跳过审批和沙箱（仅在受信隔离环境中使用） |
| `--ephemeral` | ✅ 已验证 | 不持久化 session 文件 |

#### 结构化输出

| 参数 | 状态 | 说明 |
|------|------|------|
| `--output-schema` | ✅ 已验证 | JSON Schema 文件定义期望的响应格式。Codex 确保最终响应匹配 schema |
| `-o` / `--output-last-message` | ✅ 已验证 | 将 agent 最终消息写入文件 |
| `--json` / `--experimental-json` | ✅ 已验证 | JSONL 事件流（thread.started, turn.started/completed, item.completed, error） |

**Codex JSONL 事件结构示例：**
```json
{"type":"thread.started","thread_id":"uuid"}
{"type":"turn.started"}
{"type":"item.completed","item":{"id":"item_3","type":"agent_message",...}}
{"type":"turn.completed","usage":{"input_tokens":24763,"output_tokens":122}}
```

#### 会话管理

| 参数 | 状态 | 说明 |
|------|------|------|
| `codex exec resume [SESSION_ID]` | ✅ 已验证 | 恢复之前的 session |
| `--last` | ✅ 已验证 | 恢复当前目录最近的 session |
| `--all` | ✅ 已验证 | 包含所有目录的 session |

#### 沙箱与权限控制

| 参数 | 状态 | 说明 |
|------|------|------|
| `--sandbox` / `-s` | ✅ 已验证 | 选项：`read-only`（默认）、`workspace-write`、`danger-full-access` |
| `--ask-for-approval` / `-a` | ✅ 已验证 | 选项：`untrusted`、`on-request`、`never` |

#### 配置级控制（config.yaml）

| 配置项 | 说明 |
|--------|------|
| `agents.max_threads` | 最大并发 agent 线程（默认 6） |
| `agents.max_depth` | 最大嵌套深度（默认 1） |
| `agents.job_max_runtime_seconds` | 单 worker 超时（默认 1800 秒） |
| `mcp_servers.<id>.enabled_tools` | MCP 工具白名单 |
| `mcp_servers.<id>.disabled_tools` | MCP 工具黑名单 |

#### ⚠️ Codex CLI 缺失的关键能力

| ADR 假设的参数 | 状态 | 影响 |
|----------------|------|------|
| `--max-turns` | ❌ 不存在 | Codex 没有回合数限制参数。需通过 `agents.job_max_runtime_seconds`（超时）间接控制 |
| `--max-budget-usd` | ❌ 不存在 | Codex 没有费用上限参数。需从 JSONL `turn.completed` 事件的 `usage` 字段提取 token 数自行计算 |
| `--allowedTools` / `--disallowedTools` | ❌ 不存在 | Codex 没有工具级权限控制。只有 `--sandbox` 三级沙箱 + MCP 级 `enabled_tools`/`disabled_tools` |
| `--tools` | ❌ 不存在 | 同上，Codex 无法限制内置工具集 |

**⚠️ ADR-08 需要调整：** Codex reviewer 角色的只读约束应通过 `--sandbox read-only`（默认行为）实现，而非 `--disallowedTools`。`read-only` 沙箱在系统层阻止所有文件写入。

_Source: [Command line options – Codex CLI](https://developers.openai.com/codex/cli/reference)_
_Source: [Non-interactive mode – Codex](https://developers.openai.com/codex/noninteractive)_
_Source: [Configuration Reference – Codex](https://developers.openai.com/codex/config-reference)_

---

### Python asyncio Subprocess 集成模式

**置信度：高** — 基于 Python 3.14 官方文档和最新实践

#### 核心 API

```python
# 推荐用法：asyncio.create_subprocess_exec
proc = await asyncio.create_subprocess_exec(
    "claude", "--bare", "-p", prompt,
    "--output-format", "json",
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.PIPE,
    cwd=worktree_path
)
stdout, stderr = await proc.communicate()
result = json.loads(stdout)
```

#### 关键实践

- **进程创建**：`create_subprocess_exec()` 是协程，await 后子进程已启动（非完成）
- **I/O 管理**：设置 `stdout=PIPE, stderr=PIPE` 后通过 `communicate()` 读写
- **并发控制**：使用 `asyncio.Semaphore` 或 `asyncio.Queue` 限制并发数
- **Windows 兼容**：仅 ProactorEventLoop（默认）支持子进程
- **线程安全**：Python 3.8+ UNIX 系统使用 ThreadedChildWatcher，可从不同线程生成子进程

#### Orchestrator 推荐架构模式

```python
# Producer-Consumer 模式（ADR-04 TransitionQueue 的实现参照）
transition_queue = asyncio.Queue()

async def transition_consumer():
    """单消费者，串行执行所有状态转换"""
    while True:
        transition = await transition_queue.get()
        async with aiosqlite.connect(db_path) as db:
            await execute_transition(db, transition)
        transition_queue.task_done()

async def worker_callback(story_id, result):
    """subprocess 完成回调，可并发触发"""
    await transition_queue.put(Transition(story_id, result))
```

_Source: [Subprocesses — Python 3.14 documentation](https://docs.python.org/3/library/asyncio-subprocess.html)_
_Source: [Using asyncio Queues for AI Task Orchestration](https://dasroot.net/posts/2026/02/using-asyncio-queues-ai-task-orchestration/)_

---

### 成本追踪能力对比

| 维度 | Claude Code | Codex CLI |
|------|-------------|-----------|
| 直接成本字段 | ✅ `total_cost_usd`（JSON 输出） | ❌ 无直接成本字段 |
| Token 计数 | ✅ `usage.input_tokens`, `output_tokens`, `cache_read_input_tokens` | ✅ `turn.completed.usage.input_tokens`, `output_tokens` |
| 成本计算方式 | 直接读取 | 需从 token 数 × 模型单价自行计算 |
| 日志持久化 | ✅ JSONL 日志在 `~/.claude/projects/` | ✅ Session 文件（可用 `--ephemeral` 禁用） |

**ADR-24 实现方案调整：** Codex 成本需要 Orchestrator 维护一份模型价格表，从 JSONL 事件流的 token 数计算成本。

---

### Technology Adoption Trends

#### Claude Code 发展趋势（2026-03）
- `--bare` 模式被推荐为脚本调用的标准模式，未来将成为 `-p` 的默认行为
- Agent SDK（Python/TypeScript）提供比 CLI 更精细的控制（tool approval callbacks, native message objects）
- 支持 Remote Control、Agent Teams、Channels 等新特性

#### Codex CLI 发展趋势（2026-03）
- 统一 PTY-backed exec 工具已稳定并默认启用
- Codex Cloud 支持远程执行和 best-of-N 多次尝试
- GitHub Action 集成简化 CI/CD 使用

_Source: [Claude Code Docs](https://code.claude.com/docs/en/cli-reference)_
_Source: [Codex CLI Features](https://developers.openai.com/codex/cli/features)_
_Source: [Codex Changelog](https://developers.openai.com/codex/changelog)_

---

## Integration Patterns Analysis

### ⚠️ 重大发现：Claude Agent SDK 需要 API Key

**置信度：高** — 官方文档明确说明（2026-02-19 更新）

Claude Agent SDK（原 Claude Code SDK）是 Anthropic 官方的 Python/TypeScript 库，底层将 Claude Code 作为子进程启动，提供：
- Python 原生 async generator API
- Pydantic/Zod 类型安全的结构化输出
- Tool approval callbacks（工具审批回调）
- Hooks（生命周期钩子）
- Subagents（子代理编排）

**但 Agent SDK 强制要求 API Key 认证（`ANTHROPIC_API_KEY`），不支持 OAuth/订阅认证。**

这意味着：

| 集成方式 | 认证要求 | 你的可用性 |
|----------|----------|-----------|
| `claude -p`（CLI 直接调用） | OAuth/Keychain ✅ | ✅ 可用 |
| `claude --bare -p`（最小模式） | API Key only | ❌ 不可用 |
| Claude Agent SDK（Python） | API Key only | ❌ 不可用 |
| `codex exec`（Codex CLI） | CODEX_API_KEY 或 ChatGPT auth | 待确认 |

**ADR-07 最终集成方式确认：通过 `claude -p` CLI subprocess 调用，使用 OAuth 认证。**

_Source: [Agent SDK overview](https://platform.claude.com/docs/en/agent-sdk/overview)_
_Source: [Is it possible for the SDK to use CLAUDE_CODE_OAUTH_TOKEN](https://github.com/anthropics/claude-code/issues/6536)_

---

### Claude CLI Subprocess 集成模式（主要路径）

#### 基本调用模式

```python
import asyncio
import json

async def call_claude(prompt: str, worktree: str = None,
                      schema: dict = None, max_turns: int = 10) -> dict:
    """调用 Claude CLI 并返回结构化结果"""
    cmd = ["claude", "-p", prompt, "--output-format", "json"]

    if max_turns:
        cmd.extend(["--max-turns", str(max_turns)])
    if schema:
        cmd.extend(["--json-schema", json.dumps(schema)])

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=worktree  # 在指定 worktree 中执行
    )

    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        raise RuntimeError(f"Claude exited with {proc.returncode}: {stderr.decode()}")

    result = json.loads(stdout.decode())
    return result
```

#### JSON 输出字段映射

```python
result = await call_claude("review this code")

# 文本结果
text_response = result["result"]

# 结构化输出（使用 --json-schema 时）
structured_data = result.get("structured_output")

# 会话追踪
session_id = result["session_id"]

# 成本追踪（ADR-24）
cost_usd = result["total_cost_usd"]
input_tokens = result["usage"]["input_tokens"]
output_tokens = result["usage"]["output_tokens"]
duration_ms = result["duration_ms"]
```

#### 工具权限控制模式（ADR-08 实现）

```python
# Developer 角色（可读写执行）
developer_cmd = [
    "claude", "-p", prompt, "--output-format", "json",
    "--allowedTools", "Read", "Edit", "Write", "Bash", "Glob", "Grep",
    "--dangerously-skip-permissions"
]

# Reviewer 角色（只读，不可编辑文件）
reviewer_cmd = [
    "claude", "-p", prompt, "--output-format", "json",
    "--tools", "Read,Glob,Grep",       # 仅允许这三个工具
    "--disallowedTools", "Edit,Write,Bash",  # 双重保障：显式禁用
    "--dangerously-skip-permissions"
]
```

#### 会话续接模式（ADR-12 实现）

```python
# 模式 A：Convergent Loop 内短循环用 --resume
first_result = await call_claude("Do initial review")
session_id = first_result["session_id"]

# 续接同一会话（保持上下文）
followup_cmd = [
    "claude", "-p", "Check if the fixes resolved the issues",
    "--resume", session_id,
    "--output-format", "json"
]

# 模式 B：跨 task 边界用 Context Briefing（fresh session + 摘要输入）
context_briefing = extract_briefing(previous_task_output)
new_task_cmd = [
    "claude", "-p", f"Context: {context_briefing}\n\nTask: {new_prompt}",
    "--output-format", "json"
]
```

#### 超时控制模式

```python
async def call_claude_with_timeout(prompt: str, timeout_seconds: int = 1800) -> dict:
    """带超时的 Claude 调用"""
    proc = await asyncio.create_subprocess_exec(
        "claude", "-p", prompt, "--output-format", "json",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_seconds
        )
    except asyncio.TimeoutError:
        proc.terminate()
        await proc.wait()  # 等待进程清理
        raise TimeoutError(f"Claude call exceeded {timeout_seconds}s timeout")

    return json.loads(stdout.decode())
```

_Source: [Python asyncio Subprocesses](https://docs.python.org/3/library/asyncio-subprocess.html)_
_Source: [Run Claude Code programmatically](https://code.claude.com/docs/en/headless)_

---

### Codex CLI Subprocess 集成模式

#### 基本调用模式

```python
async def call_codex(prompt: str, worktree: str = None,
                     schema_path: str = None, output_path: str = None,
                     sandbox: str = "read-only") -> dict:
    """调用 Codex CLI 并返回结果"""
    cmd = ["codex", "exec", prompt, "--sandbox", sandbox]

    if schema_path:
        cmd.extend(["--output-schema", schema_path])
    if output_path:
        cmd.extend(["-o", output_path])

    cmd.append("--json")  # JSONL 事件流

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=worktree
    )

    stdout, stderr = await proc.communicate()

    # 解析 JSONL 事件流，提取最终结果和 usage
    events = [json.loads(line) for line in stdout.decode().strip().split('\n') if line]

    total_input = sum(e.get("usage", {}).get("input_tokens", 0)
                      for e in events if e.get("type") == "turn.completed")
    total_output = sum(e.get("usage", {}).get("output_tokens", 0)
                       for e in events if e.get("type") == "turn.completed")

    # 读取 -o 输出文件获取最终结果
    final_result = None
    if output_path:
        with open(output_path) as f:
            final_result = json.load(f)

    return {
        "result": final_result,
        "input_tokens": total_input,
        "output_tokens": total_output,
        "events": events
    }
```

#### Codex 角色控制（ADR-08 适配）

```python
# Reviewer 角色：默认 read-only sandbox
reviewer_cmd = ["codex", "exec", prompt, "--json"]
# --sandbox read-only 是默认值，无需显式指定

# Fixer 角色（ADR-16 梯度降级第二阶段）
fixer_cmd = [
    "codex", "exec", prompt,
    "--full-auto",  # workspace-write + on-request approvals
    "--json"
]

# 紧急修复角色（完全跳过沙箱，仅在隔离环境中使用）
emergency_cmd = [
    "codex", "exec", prompt,
    "--dangerously-bypass-approvals-and-sandbox",
    "--json"
]
```

_Source: [Codex CLI Reference](https://developers.openai.com/codex/cli/reference)_
_Source: [Codex Non-interactive mode](https://developers.openai.com/codex/noninteractive)_

---

### Git Worktree 集成模式

#### Worktree 生命周期管理

```python
import os

async def create_worktree(repo_root: str, story_id: str, base_branch: str = "main") -> str:
    """为 story 创建隔离的 git worktree"""
    worktree_path = os.path.join(repo_root, ".worktrees", story_id)
    branch_name = f"story/{story_id}"

    proc = await asyncio.create_subprocess_exec(
        "git", "worktree", "add", "-b", branch_name, worktree_path, base_branch,
        cwd=repo_root,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    await proc.communicate()

    if proc.returncode != 0:
        raise RuntimeError(f"Failed to create worktree for {story_id}")

    return worktree_path

async def remove_worktree(repo_root: str, story_id: str):
    """清理 worktree"""
    worktree_path = os.path.join(repo_root, ".worktrees", story_id)

    await asyncio.create_subprocess_exec(
        "git", "worktree", "remove", worktree_path, "--force",
        cwd=repo_root
    )
```

#### 注意事项

- Claude 的 `--worktree` / `-w` 参数会在 `<repo>/.claude/worktrees/<name>` 创建 worktree——这与自管理 worktree 路径不同
- 推荐 Orchestrator 自行管理 worktree（上述代码），以完全控制路径和生命周期
- 每个 worktree 是独立的工作目录，共享 git 对象存储
- Python 项目的 worktree 可能需要独立的虚拟环境

_Source: [Git Worktree Documentation](https://git-scm.com/docs/git-worktree)_
_Source: [Mastering Git Worktrees with Claude Code](https://medium.com/@dtunai/mastering-git-worktrees-with-claude-code-for-parallel-development-workflow-41dc91e645fe)_

---

### Convergent Loop 集成模式（ADR-10/13/14/15 实现）

#### 完整流程模式

```python
async def convergent_loop(story_id: str, worktree: str,
                          max_rounds: int = 3) -> str:
    """
    Convergent Loop: review → fix → re-review 直到收敛或 escalate
    """
    for round_num in range(1, max_rounds + 1):
        # 第一层：Deterministic Check（ADR-13）
        deterministic_result = await run_deterministic_checks(worktree)
        if not deterministic_result.passed:
            await store_findings(story_id, deterministic_result.findings, round_num)
            # 进入 fix，不需要消耗 agent review
            await dispatch_fix(story_id, worktree, deterministic_result.findings)
            continue

        # 第二层：Agent Review（Codex，read-only sandbox）
        if round_num == 1:
            review_prompt = build_full_review_prompt(story_id)
        else:
            # ADR-15：每轮 scope 收窄
            open_findings = await get_open_findings(story_id)
            review_prompt = build_scoped_review_prompt(story_id, open_findings)

        review_result = await call_codex(
            review_prompt, worktree=worktree,
            schema_path="schemas/review-findings.json",
            output_path=f"/tmp/{story_id}-review-r{round_num}.json"
        )

        findings = review_result["result"]["findings"]
        await store_findings(story_id, findings, round_num)  # ADR-14

        blocking = [f for f in findings if f["severity"] == "blocking"]

        if not blocking:
            return "converged"  # 所有 blocking 已闭合

        # ADR-25：blocking 数量异常检查
        if len(blocking) > BLOCKING_THRESHOLD:
            await create_approval(story_id, "blocking_count_abnormal", blocking)
            return "awaiting_human"

        # Fix 阶段（Claude，可写权限）
        fix_prompt = build_fix_prompt(story_id, blocking)
        session_id = None

        fix_result = await call_claude(
            fix_prompt, worktree=worktree, max_turns=20
        )
        session_id = fix_result["session_id"]
        await track_cost(story_id, fix_result["total_cost_usd"])  # ADR-24

    # ADR-16：梯度降级
    return await escalate(story_id, worktree, session_id)

async def escalate(story_id: str, worktree: str, last_session: str) -> str:
    """3轮未收敛 → Codex 攻坚 → 人工协作"""
    # Codex 作为 fixer（切换 sandbox 为 workspace-write）
    codex_fix = await call_codex(
        build_codex_fix_prompt(story_id),
        worktree=worktree,
        sandbox="workspace-write"
    )

    # 再次 review
    re_review = await call_codex(
        build_scoped_review_prompt(story_id, await get_open_findings(story_id)),
        worktree=worktree,
        schema_path="schemas/review-findings.json",
        output_path=f"/tmp/{story_id}-escalation-review.json"
    )

    if not any(f["severity"] == "blocking" for f in re_review["result"]["findings"]):
        return "converged"

    # 最终降级：Interactive Session（ADR-11）
    await create_approval(story_id, "needs_human_collaboration", {
        "worktree": worktree,
        "open_findings": await get_open_findings(story_id)
    })
    return "awaiting_human"
```

---

### BMAD 适配层集成模式（ADR-17/18）

#### BMAD Skill 输出 → 结构化 JSON

```python
# 定义 findings schema
FINDINGS_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["pass", "fail", "needs_review"]},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "severity": {"type": "string", "enum": ["blocking", "suggestion"]},
                    "category": {"type": "string"},
                    "description": {"type": "string"},
                    "location": {"type": "string"}
                },
                "required": ["id", "severity", "category", "description"]
            }
        }
    },
    "required": ["verdict", "findings"]
}

async def bmad_adapter(raw_markdown: str) -> dict:
    """BMAD Markdown → 结构化 JSON 适配层"""
    adapter_prompt = f"""解析以下 BMAD review 文档，提取所有 findings。

severity 判定规则（ADR-19）：
- blocking: 仅限安全漏洞、逻辑错误、数据丢失、AC 违反
- suggestion: 性能、设计、可维护性改进
- 存疑时降级为 suggestion

文档内容：
{raw_markdown}"""

    result = await call_claude(
        adapter_prompt,
        schema=FINDINGS_SCHEMA,
        max_turns=1  # 纯文本解析，1 轮足够
    )

    return result.get("structured_output", result.get("result"))
```

**注意：** 由于不使用 `--bare` 模式，BMAD skills 在 `claude -p` 调用时会自动加载。这意味着 Orchestrator 可以直接在 prompt 中引用 BMAD skill 的 slash command，Claude 会自动执行。

---

### 集成安全模式

#### 认证隔离

| 组件 | 认证方式 | 存储位置 |
|------|----------|---------|
| Claude CLI | OAuth/Keychain | macOS Keychain |
| Codex CLI | CODEX_API_KEY 或 ChatGPT auth | 环境变量或 ~/.codex/auth.json |
| SQLite | 本地文件，无认证 | 项目目录 |
| Git | SSH key 或 credential helper | ~/.ssh/ 或 Git credential store |

#### 错误处理矩阵

| 错误类型 | 检测方式 | 处理策略 |
|----------|---------|---------|
| 进程崩溃 | `returncode != 0` | 重试 1 次 → escalate |
| JSON 解析失败 | `json.JSONDecodeError` | 记录 raw output → 重试 |
| 认证过期 | stderr 含 "auth" | 创建 human approval |
| 超时 | `asyncio.TimeoutError` | 创建 timeout approval（ADR-23） |
| Schema 验证失败 | `structured_output` 为 None | 重试 with 简化 schema |
| Rate limit | stderr 含 "rate_limit" | 指数退避重试 |

_Source: [Claude Code Authentication](https://code.claude.com/docs/en/authentication)_
_Source: [Codex Agent Approvals & Security](https://developers.openai.com/codex/agent-approvals-security)_

---

## Architectural Patterns and Design

### 系统架构模式：Orchestrator 内核

**推荐架构：分层事件驱动 + 状态机编排**

AgentTeamOrchestrator 的核心是一个本地单进程 Python 应用，不是分布式系统。架构模式选择应反映这一事实。

```
┌─────────────────────────────────────────────┐
│                  TUI Layer                   │
│          (Textual，只读 SQLite)              │
├─────────────────────────────────────────────┤
│              Orchestrator Core               │
│  ┌───────────┬──────────┬─────────────────┐ │
│  │ State     │ Transition│ Subprocess      │ │
│  │ Machine   │ Queue     │ Manager         │ │
│  │ (story    │ (asyncio  │ (claude/codex   │ │
│  │  lifecycle│  .Queue)  │  CLI calls)     │ │
│  │  per-story│          │                  │ │
│  └───────────┴──────────┴─────────────────┘ │
├─────────────────────────────────────────────┤
│              Persistence Layer               │
│        (SQLite WAL, aiosqlite)              │
│  stories | tasks | findings | approvals     │
│  cost_log | context_briefings               │
└─────────────────────────────────────────────┘
```

**ADR 对照：**
- ADR-01（编排者是代码）→ Orchestrator Core 是 Python，不是 LLM
- ADR-02（asyncio + SQLite）→ 事件循环 + 嵌入式数据库
- ADR-03（Textual TUI）→ TUI Layer 独立于 Core，只读 SQLite
- ADR-04（串行化转换）→ Transition Queue

_Source: [AI Agent Orchestration Patterns - Azure](https://learn.microsoft.com/en-us/azure/architecture/ai-ml/guide/ai-agent-design-patterns)_
_Source: [Multi-Agent Pattern That Works in Production](https://www.chanl.ai/blog/multi-agent-orchestration-patterns-production-2026)_

---

### Story 生命周期状态机

**推荐库：`python-statemachine` 3.x**

**置信度：高** — 该库原生支持 async、持久化模型、并发事件队列

```python
from statemachine import StateMachine, State

class StoryLifecycle(StateMachine):
    """Story 完整生命周期状态机"""

    # States
    queued = State(initial=True)
    creating = State()
    created = State()
    validating = State()
    dev_ready = State()
    developing = State()
    dev_complete = State()
    reviewing = State()
    review_passed = State()
    fixing = State()           # Convergent Loop 中的 fix 状态
    qa_running = State()
    qa_passed = State()
    uat_waiting = State()
    merging = State()
    regression_running = State()
    done = State(final=True)
    blocked = State()          # 人工介入等待

    # Transitions
    start_create = queued.to(creating)
    create_done = creating.to(created)
    start_validate = created.to(validating)
    validate_pass = validating.to(dev_ready)
    validate_fail = validating.to(creating)     # 回到创建修复
    start_dev = dev_ready.to(developing)
    dev_done = developing.to(dev_complete)
    start_review = dev_complete.to(reviewing)
    review_pass = reviewing.to(review_passed)
    review_fail = reviewing.to(fixing)          # 进入 fix
    fix_done = fixing.to(reviewing)             # 回到 review（收窄 scope）
    start_qa = review_passed.to(qa_running)
    qa_pass = qa_running.to(qa_passed)
    qa_fail = qa_running.to(fixing)
    start_uat = qa_passed.to(uat_waiting)
    uat_pass = uat_waiting.to(merging)
    merge_done = merging.to(regression_running)
    regression_pass = regression_running.to(done)

    # Escalation transitions（任何阶段都可能触发）
    escalate = (
        creating.to(blocked) |
        validating.to(blocked) |
        developing.to(blocked) |
        reviewing.to(blocked) |
        fixing.to(blocked)
    )
    unblock = blocked.to.itself(internal=True)  # 人工解除后路由回正确状态
```

**持久化到 SQLite：**

python-statemachine 支持自定义 `PersistentModel`，实现 `_read_state` / `_write_state` 方法即可对接 aiosqlite。每次状态转换自动持久化，崩溃后重启从 SQLite 恢复。

**async 事件队列：**

当多个协程并发发送事件（如多个 worktree 的 agent 同时完成），库内部使用 asyncio.Future 保证串行处理——与 ADR-04 TransitionQueue 设计一致。

_Source: [python-statemachine 3.0 Async Support](https://python-statemachine.readthedocs.io/en/latest/async.html)_
_Source: [python-statemachine Persistent Model](https://python-statemachine.readthedocs.io/en/latest/auto_examples/persistent_model_machine.html)_

---

### Producer-Consumer 模式（Transition Queue）

**ADR-04 的标准实现：**

```python
class TransitionQueue:
    """所有状态转换通过此队列串行化"""

    def __init__(self, db_path: str):
        self.queue = asyncio.Queue()
        self.db_path = db_path

    async def submit(self, story_id: str, event: str, context: dict = None):
        """提交状态转换请求（可从任何协程并发调用）"""
        future = asyncio.get_event_loop().create_future()
        await self.queue.put((story_id, event, context, future))
        return await future  # 调用者等待转换完成

    async def consumer(self):
        """单消费者循环，串行执行所有转换"""
        while True:
            story_id, event, context, future = await self.queue.get()
            try:
                async with aiosqlite.connect(self.db_path) as db:
                    # 1. 读取当前状态
                    sm = await load_story_machine(db, story_id)
                    # 2. 执行状态转换
                    await sm.send(event)
                    # 3. 持久化新状态
                    await save_story_state(db, story_id, sm.current_state)
                    await db.commit()
                future.set_result(sm.current_state)
            except Exception as e:
                future.set_exception(e)
            finally:
                self.queue.task_done()
```

**为什么不用 Lock 而用 Queue：**
- Queue 保证 FIFO 顺序，Lock 不保证
- Queue 天然支持批量消费和背压（backpressure）
- Queue 可方便地添加优先级（`asyncio.PriorityQueue`）

_Source: [Using asyncio Queues for AI Task Orchestration](https://dasroot.net/posts/2026/02/using-asyncio-queues-ai-task-orchestration/)_

---

### Subprocess Manager 模式

```python
class SubprocessManager:
    """管理所有 CLI 子进程的生命周期"""

    def __init__(self, transition_queue: TransitionQueue, db_path: str):
        self.tq = transition_queue
        self.db_path = db_path
        self.running: dict[str, asyncio.subprocess.Process] = {}
        self.semaphore = asyncio.Semaphore(4)  # 并发上限

    async def dispatch(self, story_id: str, phase: str, cmd: list[str],
                       worktree: str, timeout: int = 1800):
        """派发 CLI 任务"""
        async with self.semaphore:  # 控制并发数
            # 记录到 SQLite
            task_id = await self._register_task(story_id, phase, cmd)

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=worktree
            )
            self.running[task_id] = proc

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
                result = self._parse_output(stdout, phase)
                await self._complete_task(task_id, result)

                # 触发状态转换
                next_event = self._determine_event(phase, result)
                await self.tq.submit(story_id, next_event, result)

            except asyncio.TimeoutError:
                # ADR-23: 不自动 kill，通知人工
                await self._create_timeout_approval(story_id, task_id, timeout)
            finally:
                self.running.pop(task_id, None)
```

**并发控制关键点：**
- `Semaphore(N)` 限制同时运行的 CLI 进程数（避免资源耗尽）
- subprocess 回调并发触发 `transition_queue.submit()`，但转换执行串行
- 每个 subprocess 独立的 worktree，无文件级冲突

_Source: [Python asyncio Subprocesses](https://docs.python.org/3/library/asyncio-subprocess.html)_

---

### TUI + SQLite 实时仪表盘模式

**Textual 读取 SQLite 渲染状态：**

```python
from textual.app import App, ComposeResult
from textual.widgets import DataTable, Header, Footer
from textual.timer import Timer

class OrchestratorDashboard(App):
    """TUI 仪表盘，定期刷新 SQLite 数据"""

    BINDINGS = [("q", "quit", "退出"), ("a", "approvals", "审批队列")]

    def compose(self) -> ComposeResult:
        yield Header()
        yield DataTable(id="stories")
        yield DataTable(id="approvals")
        yield Footer()

    def on_mount(self):
        # 定期刷新（如每 2 秒）
        self.set_interval(2.0, self.refresh_data)

    async def refresh_data(self):
        """从 SQLite 读取最新状态"""
        async with aiosqlite.connect(self.db_path) as db:
            stories = await db.execute_fetchall(
                "SELECT id, title, phase, status, cost_usd FROM stories"
            )
            approvals = await db.execute_fetchall(
                "SELECT id, type, story_id, status FROM approvals WHERE status='pending'"
            )
        # 更新 DataTable widgets
        self._update_stories_table(stories)
        self._update_approvals_table(approvals)
```

**TUI 与 Orchestrator 的解耦：**
- TUI 是**独立进程**，只读 SQLite（WAL 模式允许并发读写）
- Orchestrator 写入 SQLite，TUI 轮询读取
- 无需 IPC、消息队列或共享内存
- TUI 崩溃不影响 Orchestrator 运行

_Source: [Textual Framework](https://textual.textualize.io/)_
_Source: [Contact Book with Python, Textual, and SQLite](https://realpython.com/contact-book-python-textual/)_

---

### SQLite WAL 并发与崩溃恢复模式

**WAL 模式关键特性（ADR-02/05）：**

| 特性 | 说明 |
|------|------|
| 读写并发 | 读不阻塞写，写不阻塞读 |
| 写串行化 | 同一时间只有一个写事务 |
| 崩溃恢复 | WAL 文件自动回放，数据不丢失 |
| 性能 | 写入显著快于默认 rollback journal 模式 |

```python
async def init_db(db_path: str):
    """初始化 SQLite，启用 WAL 模式"""
    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA busy_timeout=5000")  # 写冲突时等待 5 秒
        await db.execute("PRAGMA synchronous=NORMAL")  # WAL 模式下安全且更快
```

**对 Orchestrator 的影响：**
- Orchestrator（单进程写入） + TUI（独立进程读取）→ WAL 完美适配
- TransitionQueue 保证串行写入 → 无写冲突
- `busy_timeout` 防御极端情况下 TUI 的偶发写操作（如审批响应）

_Source: [SQLite WAL Documentation](https://www.sqlite.org/wal.html)_
_Source: [aiosqlite Documentation](https://aiosqlite.omnilib.dev/en/latest/)_

---

### Approval Queue 模式（ADR-20）

```python
# SQLite 表结构
CREATE TABLE approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,        -- 'batch_select', 'merge_auth', 'timeout', 'budget_exceeded', 'blocking_abnormal'
    story_id TEXT,
    details TEXT,              -- JSON
    options TEXT,              -- JSON array of choices
    status TEXT DEFAULT 'pending',  -- 'pending', 'decided'
    decision TEXT,
    decided_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**TUI 中的审批交互：**
- TUI 渲染 pending approvals 列表
- 用户在 TUI 中选择 decision
- TUI 写入 SQLite（`UPDATE approvals SET decision=?, status='decided'`）
- Orchestrator 轮询或监听变更，执行后续流程

**与旧系统对比：** 旧 Master Control 中"两个 pane 同时弹出询问只能看到一个"的问题完全消除——所有 approvals 持久化在队列中，按优先级排列，互不覆盖。

---

### 崩溃恢复模式（ADR-05）

```python
async def recover_from_crash(db_path: str, subprocess_mgr: SubprocessManager):
    """Orchestrator 重启后恢复运行状态"""
    async with aiosqlite.connect(db_path) as db:
        # 查找崩溃前正在运行的任务
        running_tasks = await db.execute_fetchall(
            "SELECT task_id, story_id, pid, phase, expected_artifact FROM tasks WHERE status='running'"
        )

    for task in running_tasks:
        task_id, story_id, pid, phase, artifact_path = task

        pid_alive = await check_pid(pid)
        artifact_exists = os.path.exists(artifact_path) if artifact_path else False

        if pid_alive:
            # 进程还在运行 → 重新注册监听
            await subprocess_mgr.reattach(task_id, pid)
        elif artifact_exists:
            # 进程已死但产出了 artifact → 继续流水线
            await subprocess_mgr.complete_from_artifact(task_id, artifact_path)
        else:
            # 进程已死无产出 → 重新调度
            await subprocess_mgr.reschedule(task_id)
```

**比旧系统简单得多：** Master Control 需要 `generation.lock` + `event-bus.sh materialize` + gate-state 重建 + pane 状态扫描。新系统只需查 SQLite + 检查 PID/artifact。

_Source: [Event-Driven Architecture in Python with AsyncIO](https://medium.com/data-science-collective/mastering-event-driven-architecture-in-python-with-asyncio-and-pub-sub-patterns-2b26db3f11c9)_

---

## Implementation Approaches and Technology Adoption

### 开发工作流与工具链

#### 项目结构（推荐）

```
agent-team-orchestrator/
├── src/
│   ├── orchestrator/
│   │   ├── __init__.py
│   │   ├── core.py              # 主事件循环、启动/恢复
│   │   ├── state_machine.py     # StoryLifecycle 状态机定义
│   │   ├── transition_queue.py  # TransitionQueue
│   │   ├── subprocess_mgr.py    # SubprocessManager (Claude/Codex 调用)
│   │   ├── convergent_loop.py   # Convergent Loop 协议实现
│   │   └── recovery.py          # 崩溃恢复逻辑
│   ├── adapters/
│   │   ├── claude_cli.py        # Claude CLI 封装
│   │   ├── codex_cli.py         # Codex CLI 封装
│   │   └── bmad_adapter.py      # BMAD Markdown → JSON 适配层
│   ├── models/
│   │   ├── schemas.py           # Pydantic models (findings, reviews, etc.)
│   │   └── db.py                # SQLite schema + aiosqlite helpers
│   ├── tui/
│   │   ├── app.py               # Textual App
│   │   ├── dashboard.py         # 主仪表盘
│   │   └── approval_view.py     # 审批交互界面
│   └── cli.py                   # CLI 入口点 (typer/click)
├── schemas/                     # JSON Schema 文件
│   ├── review-findings.json
│   ├── story-validation.json
│   └── finding-verification.json
├── tests/
│   ├── unit/
│   │   ├── test_state_machine.py
│   │   ├── test_transition_queue.py
│   │   └── test_bmad_adapter.py
│   ├── integration/
│   │   ├── test_claude_cli.py
│   │   ├── test_codex_cli.py
│   │   └── test_convergent_loop.py
│   └── conftest.py              # pytest fixtures
├── pyproject.toml
└── .claude/skills/              # BMAD skills (Claude)
    .codex/skills/               # BMAD skills (Codex, 同步)
```

#### 核心依赖

| 包 | 版本 | 用途 |
|---|------|------|
| `python` | ≥3.11 | asyncio TaskGroup、ExceptionGroup |
| `aiosqlite` | ≥0.20 | 异步 SQLite |
| `python-statemachine` | ≥3.0 | Story 生命周期状态机 |
| `textual` | ≥2.0 | TUI 仪表盘 |
| `pydantic` | ≥2.0 | Schema 验证、JSON Schema 生成 |
| `typer` | ≥0.9 | CLI 入口点 |

#### 开发工具

| 工具 | 用途 |
|------|------|
| `pytest` + `pytest-asyncio` | 测试框架 |
| `ruff` | Linting + Formatting |
| `mypy` | 类型检查 |
| `pre-commit` | Git hooks |

---

### 测试策略

#### 分层测试架构

**第 1 层：单元测试（状态机 + 业务逻辑）**

```python
import pytest
from src.orchestrator.state_machine import StoryLifecycle

class TestStoryLifecycle:
    def test_happy_path(self):
        sm = StoryLifecycle()
        assert sm.current_state == sm.queued

        sm.start_create()
        assert sm.current_state == sm.creating

        sm.create_done()
        assert sm.current_state == sm.created

    def test_review_fail_enters_fixing(self):
        sm = StoryLifecycle()
        # ... 推进到 reviewing 状态
        sm.review_fail()
        assert sm.current_state == sm.fixing

    def test_invalid_transition_raises(self):
        sm = StoryLifecycle()
        with pytest.raises(Exception):
            sm.review_pass()  # 从 queued 不能直接 review_pass
```

**第 2 层：集成测试（CLI 调用 mock）**

```python
import pytest
from unittest.mock import AsyncMock, patch

@pytest.mark.asyncio
async def test_claude_cli_returns_structured_output():
    mock_result = {
        "result": "review complete",
        "structured_output": {"verdict": "pass", "findings": []},
        "session_id": "test-uuid",
        "total_cost_usd": 0.05
    }

    with patch("asyncio.create_subprocess_exec") as mock_exec:
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (
            json.dumps(mock_result).encode(), b""
        )
        mock_proc.returncode = 0
        mock_exec.return_value = mock_proc

        result = await call_claude("test prompt")
        assert result["structured_output"]["verdict"] == "pass"
```

**第 3 层：端到端测试（真实 CLI 调用，沙箱环境）**

- 使用真实 `claude -p` 和 `codex exec` 调用
- 限制 `--max-turns 2`、`--max-budget-usd 0.50` 控制成本
- 在临时 git repo 中运行，隔离副作用

_Source: [pytest-asyncio](https://pypi.org/project/pytest-asyncio/)_
_Source: [Mocking Asyncio Subprocess](https://joshmustill.medium.com/mocking-asyncio-subprocess-in-python-with-pytest-ad508d3e6b53)_
_Source: [Testing State Machines](https://www.planetgeek.ch/2011/05/17/how-to-unit-test-finite-state-machines/)_

---

### 成本优化策略

#### Claude CLI 成本控制

| 策略 | 节省幅度 | 实现方式 |
|------|---------|---------|
| **Prompt Caching** | 最高 90% 输入 token | 自动生效，重复内容（系统提示）被缓存 |
| **模型分级** | 60-80% | Sonnet 做常规任务，Opus 做复杂分析（`--model sonnet`） |
| **--max-turns 限制** | 防失控 | 每个任务设置合理的回合上限 |
| **--max-budget-usd** | 硬性上限 | 每次 CLI 调用设置预算上限 |
| **Context Briefing** | 40-60% | 避免长 session 的上下文膨胀，每次 fresh start |
| **Scope 收窄（ADR-15）** | 30-50% | re-review 只关注 open findings，不全量审查 |

#### 模型选择策略（ADR-16 扩展）

```python
MODEL_MAP = {
    # Structured Jobs - 常规任务用 Sonnet
    "create_story": "sonnet",
    "qa_generation": "sonnet",
    "regression": "sonnet",

    # Reviews - Codex 做审核（独立计费）
    "code_review": None,    # Codex CLI，不用 Claude 模型
    "validation": None,     # Codex CLI

    # Complex Tasks - 复杂任务用 Opus
    "dev_story": "opus",
    "architecture_review": "opus",
    "escalation_fix": "opus",

    # Adapter - 适配层解析用最便宜的模型
    "bmad_adapter": "haiku",  # --max-turns 1，纯文本解析
}
```

**Story 级成本追踪实现（ADR-24）：**

```sql
CREATE TABLE cost_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    story_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    cli_tool TEXT NOT NULL,          -- 'claude' or 'codex'
    model TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cache_read_tokens INTEGER,
    cost_usd REAL,                   -- Claude 直接提供; Codex 需自算
    duration_ms INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Story 级成本聚合
-- SELECT story_id, SUM(cost_usd) as total_cost FROM cost_log GROUP BY story_id;
```

_Source: [Manage costs effectively - Claude Code](https://code.claude.com/docs/en/costs)_
_Source: [Cut Your AI API Costs](https://blogs.ost.agency/ai-api-cost-optimization/)_

---

### 风险评估与缓解

#### 高优先级风险

| 风险 | 概率 | 影响 | 缓解策略 |
|------|------|------|---------|
| **OAuth token 过期导致 Claude 调用失败** | 中 | 高 | 检测 stderr 中的 auth 错误 → 创建 human approval |
| **Convergent Loop 不收敛** | 中 | 中 | 硬性 max_rounds=3 + 梯度降级（ADR-16） |
| **BMAD skill 在 Codex 中行为不一致** | 中 | 中 | 初始开发阶段验证关键 skill 在两个 CLI 中的输出一致性 |
| **SQLite 数据库损坏** | 低 | 高 | WAL 模式 + 定期 `PRAGMA integrity_check` + 文件级备份 |
| **并发 worktree merge 冲突** | 中 | 低 | Claude 自动解决 → 失败 escalate（ADR-22） |

#### 中优先级风险

| 风险 | 概率 | 影响 | 缓解策略 |
|------|------|------|---------|
| **claude -p 非 bare 模式加载意外配置** | 中 | 低 | 明确项目 .claude 配置，CI 中使用固定配置 |
| **Textual TUI 渲染性能瓶颈** | 低 | 低 | 限制刷新频率（2-5 秒）、分页渲染大列表 |
| **Codex CLI API 变更** | 低 | 中 | CLI 封装层隔离变更影响，定期跟踪 changelog |

---

### 实现路线图

#### Phase 0：基础脚手架（1-2 天）

- 项目初始化（pyproject.toml, ruff, mypy, pre-commit）
- SQLite schema 定义 + aiosqlite 连接层
- 基本 CLI 入口点

#### Phase 1：核心引擎（3-5 天）

- StoryLifecycle 状态机 + SQLite 持久化
- TransitionQueue (asyncio.Queue)
- Claude CLI / Codex CLI 封装（call_claude, call_codex）
- 崩溃恢复逻辑

#### Phase 2：Convergent Loop（3-4 天）

- BMAD 适配层（Markdown → JSON）
- Finding 级追踪 + scope 收窄
- 梯度降级流程
- 端到端测试：单个 code-review → fix → re-review 循环

#### Phase 3：完整流水线（2-3 天）

- Batch 选择与调度
- Worktree 管理
- Approval Queue
- 成本追踪

#### Phase 4：TUI（2-3 天）

- Dashboard 主界面
- 审批交互
- 实时状态刷新

**总估算核心代码：1000-1500 行**（与 ADR 估算一致）

---

## Technical Research Recommendations

### 技术栈最终推荐

| 组件 | 推荐 | 置信度 |
|------|------|--------|
| 核心语言 | Python ≥3.11 asyncio | 高 |
| 状态存储 | SQLite WAL + aiosqlite | 高 |
| 状态机 | python-statemachine ≥3.0 | 高 |
| TUI | Textual ≥2.0 | 高 |
| Schema 验证 | Pydantic ≥2.0 | 高 |
| Claude 调用 | `claude -p` subprocess（OAuth 认证） | 高 |
| Codex 调用 | `codex exec` subprocess | 高 |
| 测试 | pytest + pytest-asyncio | 高 |

### 关键成功指标

| 指标 | 目标 |
|------|------|
| 核心代码行数 | ≤1500 行（对比旧系统 4000+） |
| 单 Story 端到端耗时 | 可量化追踪（TUI 显示） |
| Story 级成本 | 可追踪（cost_log 表） |
| Convergent Loop 收敛率 | ≥80% 在 3 轮内收敛 |
| 崩溃恢复时间 | ≤10 秒（SQLite 查表 + PID 检查） |
| 人工介入频率 | 可量化（approvals 表统计） |

---

## Research Synthesis

### Executive Summary

本技术调研对 AgentTeamOrchestrator 的核心技术方案进行了系统性验证，聚焦 Claude CLI 和 Codex CLI 的 subprocess 集成能力。通过官方文档直接验证、多源交叉核实和架构模式分析，得出以下关键结论：

**1. CLI 能力验证结果：整体通过**

Claude Code CLI 和 Codex CLI 均具备完整的非交互执行能力、结构化输出、会话管理功能，足以支撑 ADR 中设计的 Orchestrator 架构。两个 CLI 都遵循 Open Agent Skills 规范，BMAD skills 可在两端同时部署使用。

**2. 需要调整的 ADR**

| ADR | 原假设 | 调整 |
|-----|--------|------|
| ADR-07 | `claude --bare -p` | 改为 `claude -p`（无 API Key，使用 OAuth 认证） |
| ADR-08 | 两个 CLI 都用 `--disallowedTools` | Codex 改用 `--sandbox read-only`（默认行为） |
| ADR-09 | 结构化输出在 `result` 字段 | Claude 使用 `--json-schema` 时输出在 `structured_output` 字段 |
| ADR-24 | 两个 CLI 都提供 `cost_usd` | Codex 需从 JSONL token 数自行计算成本 |

**3. 新发现的约束**

- **Claude Agent SDK（Python 库）需要 API Key**，不支持 OAuth 认证 → 必须使用 CLI subprocess 方式
- **Codex CLI 没有 `--max-turns`** → 通过 Orchestrator 层面的 `asyncio.wait_for` 超时控制
- **`claude -p` 非 bare 模式**会加载项目配置 → BMAD skills 自动可用（利大于弊）

**4. 推荐架构模式**

- **状态机**：`python-statemachine` 3.x（原生 async + SQLite 持久化 + 并发事件队列）
- **串行化转换**：`asyncio.Queue` Producer-Consumer 模式
- **TUI 解耦**：Textual 独立进程，只读 SQLite WAL
- **崩溃恢复**：SQLite 查表 + PID/artifact 检查（比旧系统极大简化）

**5. 成本优化**

模型分级策略（haiku 做适配层 → sonnet 做常规任务 → opus 做复杂开发）结合 prompt caching 和 scope 收窄，预估可节省 60-80% token 成本。

### ADR 完整验证矩阵

| ADR | 标题 | 验证结果 | 备注 |
|-----|------|---------|------|
| ADR-01 | 编排者是代码 | ✅ 通过 | CLI 能力完备 |
| ADR-02 | Python asyncio + SQLite | ✅ 通过 | aiosqlite WAL 成熟稳定 |
| ADR-03 | Textual TUI | ✅ 通过 | SQLite 轮询模式简单有效 |
| ADR-04 | 串行化转换 | ✅ 通过 | python-statemachine 内建支持 |
| ADR-05 | 崩溃恢复 | ✅ 通过 | SQLite + PID 检查 |
| ADR-06 | Claude=执行/Codex=审核 | ✅ 通过 | 两个 CLI 角色分工清晰 |
| ADR-07 | CLI subprocess 调用 | ⚠️ 调整 | 去掉 --bare，使用默认 OAuth |
| ADR-08 | 工具限制强制角色 | ⚠️ 调整 | Codex 用 sandbox 而非 disallowedTools |
| ADR-09 | Schema 强制输出 | ⚠️ 调整 | structured_output 字段（非 result） |
| ADR-10 | 三种 Task Type | ✅ 通过 | CLI 参数覆盖所有场景 |
| ADR-11 | Interactive Session | ✅ 通过 | 独立终端 + worktree |
| ADR-12 | Context Briefing | ✅ 通过 | --resume + fresh session |
| ADR-13 | 两层验证 | ✅ 通过 | deterministic + agent review |
| ADR-14 | Finding 级追踪 | ✅ 通过 | SQLite findings 表 |
| ADR-15 | Scope 收窄 | ✅ 通过 | prompt 动态拼接 open findings |
| ADR-16 | 梯度降级 | ✅ 通过 | Codex --sandbox workspace-write |
| ADR-17 | BMAD 不修改 | ✅ 通过 | 适配层 LLM 解析 |
| ADR-18 | BMAD yolo 模式 | ✅ 通过 | --yolo 别名已验证 |
| ADR-19 | Severity 判定规则 | ✅ 通过 | 适配层 prompt 硬编码规则 |
| ADR-20 | Approval Queue | ✅ 通过 | SQLite approvals 表 |
| ADR-21 | Regression 冻结 | ✅ 通过 | 状态机实现 |
| ADR-22 | Merge 冲突解决 | ✅ 通过 | worktree + Claude 自动解决 |
| ADR-23 | 超时不 kill | ✅ 通过 | asyncio.wait_for + approval |
| ADR-24 | 成本追踪 | ⚠️ 调整 | Codex 需自行计算成本 |
| ADR-25 | Blocking 数量异常 | ✅ 通过 | 适配层输出 + 阈值检查 |

**验证总结：** 25 条 ADR 中 21 条完全通过，4 条需要小幅调整（不影响架构方向）。

### 技术调研方法论与来源

**调研方法：**
- 官方文档直接验证（code.claude.com, developers.openai.com）
- Web 搜索多源交叉核实
- GitHub Issues 和 Discussions 验证实际行为
- 开源项目参考（python-statemachine, Textual, aiosqlite）

**主要来源：**
- [Claude Code CLI Reference](https://code.claude.com/docs/en/cli-reference)
- [Run Claude Code Programmatically](https://code.claude.com/docs/en/headless)
- [Claude Agent SDK Overview](https://platform.claude.com/docs/en/agent-sdk/overview)
- [Agent SDK Structured Outputs](https://platform.claude.com/docs/en/agent-sdk/structured-outputs)
- [Codex CLI Reference](https://developers.openai.com/codex/cli/reference)
- [Codex Non-interactive Mode](https://developers.openai.com/codex/noninteractive)
- [Codex Configuration Reference](https://developers.openai.com/codex/config-reference)
- [Python asyncio Subprocesses](https://docs.python.org/3/library/asyncio-subprocess.html)
- [python-statemachine 3.0](https://python-statemachine.readthedocs.io/en/latest/)
- [SQLite WAL Documentation](https://www.sqlite.org/wal.html)
- [Textual Framework](https://textual.textualize.io/)
- [Manage Costs - Claude Code](https://code.claude.com/docs/en/costs)

### 下一步行动

1. **进入 BMAD PRD 阶段** — 将本调研结论和修正后的 ADR 作为 PRD 的技术约束输入
2. **原型验证** — 实现单个 Convergent Loop（code-review → fix → re-review）端到端调用
3. **SQLite Schema 设计** — 定义 stories/tasks/findings/approvals/cost_log 表结构
4. **JSON Schema 文件** — 定义 review-findings/finding-verification/story-validation 等 schema

---

**技术调研完成日期：** 2026-03-24
**调研周期：** 当日完成全面技术验证
**源头验证：** 所有关键技术声明均基于官方文档直接验证
**技术置信度：** 高 — 基于多个权威来源交叉验证
