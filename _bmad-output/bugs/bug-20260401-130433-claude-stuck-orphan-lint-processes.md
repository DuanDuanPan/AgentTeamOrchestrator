# Bug: Claude CLI 完成后因 orphan pnpm lint 子进程无法退出

## 发现时间
2026-04-01 13:04:33 (结果产出) → 13:08+ (进程仍未退出)

## 影响 Story
3-4-ai-chapter-generation (fixing 第二轮)

## 现象
1. Claude fixer agent 在 13:04:33 完成所有工作并产出 result 事件（cost=$1.29）
2. 代码已 commit 成功（pre-commit hooks passed）
3. 但 Claude CLI 进程 (PID 91839 → 91840) **无法退出**
4. 原因：两个 `pnpm lint` 子进程未终止，阻塞进程树退出
   - PID 93219: `pnpm lint` running 9+ minutes, 0% CPU, Ss state
   - PID 94315: `pnpm lint` running 5+ minutes, 0% CPU, Ss state
5. ATO adapter 等待进程退出才能触发 `claude_adapter_success` → `transition_submitted`
6. 整个 pipeline 被阻塞

## 进程树
```
91839 claude (wrapper)
└── 91840 claude (main, S+, 0.1% CPU)
    ├── 91903 npx @upstash/context7-mcp
    ├── 91906 npx @kazuph/mcp-fetch
    ├── 91920 npx @modelcontextprotocol/server-sequential-thinking
    ├── 91951 pencil mcp-server
    ├── 93219 /bin/zsh -c ... 'pnpm lint 2>&1 | tail -30'  ← STUCK (9+ min)
    └── 94315 /bin/zsh -c ... 'pnpm lint 2>&1 | tail -20'  ← STUCK (5+ min)
```

## 根因分析
- Claude CLI 在验证阶段调用了 `pnpm lint` 但子进程在 pipe 中挂起
- `pnpm lint 2>&1 | tail -30` 中 `tail` 可能已退出但 `pnpm lint` 还在等待 pipe 写入
- 或者 `pnpm lint` 本身在等待锁文件/端口
- Claude CLI 产出 result 后并未 kill 子进程树
- MCP servers 也未被清理（但它们不是主要阻塞原因）

## 影响
- **高** — 完全阻塞 pipeline 推进，fixing 完成但无法 transition 到 reviewing
- 如果不干预，需等 30 分钟超时

## 建议修复方案
1. **Claude adapter 应在收到 result 事件后设置短超时**（如 30s），超时后主动 kill 进程树
2. **Claude CLI 应在产出 result 后清理子进程树**
3. **ATO adapter 应有 "result received but process hung" 检测** — 如果 result 已收到但 5 分钟内进程未退出，主动终止

## 临时处理
可以 kill 这两个 stuck lint 进程释放 Claude CLI：
```bash
kill 93219 94315
```
