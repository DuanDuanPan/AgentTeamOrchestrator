# Bug: Codex exec 模式首次 reviewing 会话 30 分钟无活动超时

## 发现时间
2026-04-01 12:24:43

## 影响 Story
3-4-ai-chapter-generation (reviewing 阶段)

## 现象
1. Codex reviewer (PID 65446) 于 11:54:43 启动，使用 `codex --dangerously-bypass-approvals-and-sandbox exec` 模式
2. 会话初始化成功（11:54:47 "新回合开始"），但之后 **30 分钟内无任何进度事件**
3. 进程持续 alive（S+ state），0% CPU，无 TCP 连接到外部 API
4. 9 个 MCP 子进程（context7, playwright, pencil 等）全部存活但空闲
5. Worktree 无文件被读取或修改
6. 30 分钟 timeout 触发后自动 retry，第二次尝试 **立即开始正常工作**（12:25:22 有 progress 事件）

## 根因分析（推测）
- **可能性 1:** Codex API 首次请求卡在队列中或遇到 API 端服务端问题，retry 时 API 已恢复
- **可能性 2:** Codex `exec` 模式在 MCP server 初始化阶段某个 server 超时挂起（如 serena、21st-dev/magic），阻塞了主线程
- **可能性 3:** 长 prompt（包含 6 个 blocking findings 的完整 JSON + UX 设计引用）导致 API 处理超时

## 影响
- 中等 — 30 分钟的等待时间浪费，但自动 retry 机制正常工作
- 如果 retry 也遇到同样问题，则需 60 分钟才能 escalate

## 建议
1. **添加 heartbeat 检测** — 如果 Codex 进程 5 分钟无 progress 事件，主动 kill 并立即 retry，而不是等满 30 分钟
2. **Codex exec 超时分级** — 区分"会话初始化后无活动"（短超时）和"正在产出 progress 事件"（长超时）
3. **记录 MCP server 初始化耗时** — 如果某个 MCP server 启动慢或卡死，能快速定位

## 时间线
```
11:54:43 codex_adapter_execute — Codex reviewer 启动
11:54:47 agent_progress/init — 会话初始化成功
11:54:47 agent_progress/other — "新回合开始"（最后一条进度事件）
... 30 分钟无活动 ...
12:24:43 agent_progress/error — 超时 (1800s)
12:24:43 dispatch_retry — 自动 retry
12:24:47 agent_progress/init — 新会话初始化（第二次）
12:25:22 agent_progress/text — "Using bmad-code-review..." (正常工作)
12:25:29 agent_progress/text — "confirmed worktree is clean"
```
