# Agent Team Orchestrator — 开发指南

> **版本**: 基于 `SCHEMA_VERSION = 8` 的代码库快照  
> **生成日期**: 2026-03-29  
> **目标读者**: AI Agent / 开发者

---

## 1. 快速开始

### 1.1 环境搭建

```bash
# 安装依赖（推荐使用 uv）
uv sync --dev

# 验证安装
uv run ato --help

# 初始化项目
uv run ato init .

# 启动 Orchestrator
uv run ato start

# 打开 TUI Dashboard（新终端）
uv run ato tui
```

### 1.2 开发工具链验证

```bash
# 运行测试
uv run pytest

# 代码检查
uv run ruff check src tests
uv run ruff format src tests

# 类型检查（strict 模式）
uv run mypy src

# Pre-commit hooks
uv run pre-commit run --all-files
```

---

## 2. 项目结构

```
AgentTeamOrchestrator/
├── src/ato/
│   ├── __init__.py
│   ├── core.py                # Orchestrator 主类 + Poll Cycle
│   ├── cli.py                 # Typer CLI 入口 (2400+ 行)
│   ├── config.py              # 声明式配置引擎
│   ├── state_machine.py       # Story 生命周期状态机
│   ├── transition_queue.py    # FIFO 转换队列
│   ├── recovery.py            # 崩溃恢复引擎
│   ├── merge_queue.py         # Merge Queue 管理器
│   ├── convergent_loop.py     # 审查→修复→复审 质量门控
│   ├── subprocess_mgr.py      # Agent 并发调度
│   ├── worktree_mgr.py        # Git Worktree 管理
│   ├── preflight.py           # 三层预检引擎
│   ├── nudge.py               # 进程通知机制
│   ├── approval_helpers.py    # 审批统一 API
│   ├── batch.py               # Batch 选择与推荐
│   ├── validation.py          # JSON Schema 验证
│   ├── design_artifacts.py    # 设计工件管理
│   ├── logging.py             # structlog 配置
│   ├── recovery_summary.py    # 恢复摘要渲染
│   ├── models/
│   │   ├── schemas.py         # 所有 Pydantic 模型
│   │   ├── db.py              # SQLite CRUD
│   │   └── migrations.py      # Schema 迁移
│   ├── adapters/
│   │   ├── base.py            # BaseAdapter 抽象类
│   │   ├── claude_cli.py      # Claude CLI 适配器
│   │   ├── codex_cli.py       # Codex CLI 适配器
│   │   └── bmad_adapter.py    # BMAD 输出解析器
│   └── tui/
│       ├── app.py             # Textual 应用入口
│       ├── dashboard.py       # 主仪表盘
│       ├── story_detail.py    # Story 详情
│       ├── theme.py           # 主题配色
│       ├── app.tcss            # Textual CSS
│       └── widgets/           # 自定义控件
├── tests/
│   ├── unit/                  # 快速逻辑测试
│   ├── integration/           # SQLite/TUI 工作流测试
│   ├── smoke/                 # CLI 命令冒烟测试
│   └── performance/           # 性能基准测试
├── schemas/                   # JSON Schema 文件
├── _bmad-output/              # BMAD 规划产物
├── docs/                      # 项目文档
├── pyproject.toml             # 项目元数据
├── ato.yaml.example           # 配置模板
└── CLAUDE.md / AGENTS.md      # AI Agent 上下文
```

---

## 3. 编码规范

### 3.1 基本约定

| 规则 | 标准 |
|------|------|
| **Python 版本** | 3.11+ |
| **缩进** | 4 空格 |
| **行宽** | 100 字符（Ruff 强制） |
| **函数命名** | `snake_case` |
| **类命名** | `PascalCase` |
| **常量命名** | `UPPER_SNAKE_CASE` |
| **类型注解** | 公共 API 必须标注 |
| **私有前缀** | `_` 前缀表示模块/类内部 |

### 3.2 Pydantic 模型规范

```python
# Record Model 继承 _StrictBase
class MyRecord(_StrictBase):
    """表描述。"""
    my_field: str
    optional_field: int | None = None

# Adapter 输出使用宽松解析
class MyOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")  # 忽略未知字段
```

**关键规则**:
- Record Model 使用 `strict=True` + `extra="forbid"`
- Adapter 输出使用 `extra="ignore"`（外部 JSON 不可控）
- 永远不要在 DB 写入前跳过 Pydantic 验证

### 3.3 异步编程规范

```python
# ✅ 正确：使用 try/finally 确保连接关闭
db = await get_connection(db_path)
try:
    result = await some_operation(db)
finally:
    await db.close()

# ❌ 错误：连接泄漏风险
db = await get_connection(db_path)
result = await some_operation(db)
await db.close()  # 异常时不会执行

# ✅ 正确：subprocess 清理
proc = await asyncio.create_subprocess_exec(...)
try:
    await proc.wait()
finally:
    await cleanup_process(proc)  # 三阶段清理
```

### 3.4 日志规范

使用 `structlog` 结构化日志，不使用 `print()`：

```python
import structlog
logger: structlog.stdlib.BoundLogger = structlog.get_logger()

# ✅ 结构化事件
logger.info("story_dispatched", story_id=story_id, phase=phase)
logger.warning("interactive_session_timeout", elapsed_seconds=elapsed)
logger.error("recovery_failed", task_id=task_id, exc_info=True)

# ❌ 避免
print(f"Story {story_id} dispatched")  # 不要使用 print
logger.info(f"Task {task_id} completed")  # 不要在事件名中用 f-string
```

---

## 4. 开发工作流

### 4.1 新增阶段（Phase）

1. **状态机定义** (`state_machine.py`):
   ```python
   class StoryLifecycle(StateMachine):
       my_phase = State()
       # 添加转换
       my_phase_start = queued.to(my_phase)  # 进入事件
       my_phase_done = my_phase.to(next_phase)  # 退出事件
   ```

2. **Phase 定义** (`config.py`):
   ```python
   _DEFAULT_PHASE_DEFS = [
       ...,
       PhaseDefinition(
           name="my_phase",
           phase_type="structured_job",
           cli_tool="claude",
           roles=["developer"],
       ),
       ...,
   ]
   ```

3. **Recovery 映射** (`recovery.py`):
   ```python
   _PHASE_SUCCESS_EVENT["my_phase"] = "my_phase_done"
   ```

4. **CLI/TUI 支持**: 更新 `cli.py` 的 `_PHASE_ICONS` 和 `tui/dashboard.py`

### 4.2 新增 Approval 类型

1. **类型定义** (`schemas.py`):
   ```python
   ApprovalType = Literal[..., "my_new_type"]
   APPROVAL_TYPE_TO_NOTIFICATION["my_new_type"] = "normal"
   APPROVAL_RECOMMENDED_ACTIONS["my_new_type"] = "approve"
   APPROVAL_DEFAULT_VALID_OPTIONS["my_new_type"] = ["approve", "reject"]
   APPROVAL_TYPE_ICONS["my_new_type"] = "🆕"
   ```

2. **处理逻辑** (`core.py` 的 `_poll_cycle`):
   ```python
   elif approval.approval_type == "my_new_type":
       # 处理已决策的 approval
       ...
   ```

3. **创建触发**:
   ```python
   from ato.approval_helpers import create_approval
   await create_approval(db, story_id=..., approval_type="my_new_type", payload_dict={...})
   ```

### 4.3 新增 CLI 适配器

1. **实现适配器** (`adapters/`):
   ```python
   class MyAdapter(BaseAdapter):
       async def execute(self, prompt, options=None, *, on_process_start=None):
           # 启动 CLI 进程
           # 解析输出
           return AdapterResult(...)
   ```

2. **添加输出模型** (`schemas.py`):
   ```python
   class MyOutput(AdapterResult):
       my_specific_field: str = ""
   ```

3. **注册到 TaskRecord** (`schemas.py`):
   ```python
   cli_tool: Literal["claude", "codex", "my_tool"]
   ```

### 4.4 数据库 Schema 变更

1. **增加迁移** (`migrations.py`):
   ```python
   async def _migrate_8_to_9(db):
       await db.execute("ALTER TABLE my_table ADD COLUMN new_col TEXT")
   ```

2. **更新版本号** (`schemas.py`):
   ```python
   SCHEMA_VERSION = 9  # 从 8 增至 9
   ```

3. **更新 DDL** (`db.py`): 保持 DDL 与迁移后的最终状态一致

4. **更新模型** (`schemas.py`): 添加对应的 Pydantic 字段

---

## 5. 测试指南

### 5.1 测试文件组织

```
tests/
├── unit/
│   ├── test_config.py           # 配置解析
│   ├── test_state_machine.py    # 状态机转换
│   ├── test_transition_queue.py # 队列消费
│   ├── test_recovery.py         # 恢复分类
│   ├── test_approval.py         # 审批逻辑
│   ├── test_batch.py            # Batch 推荐
│   ├── test_schemas.py          # Pydantic 验证
│   └── test_validation.py       # JSON Schema
├── integration/
│   ├── test_db_operations.py    # SQLite CRUD
│   ├── test_worktree_flow.py    # Git Worktree
│   └── test_convergent_loop.py  # 质量门控
├── smoke/
│   ├── test_cli_commands.py     # CLI 冒烟
│   └── test_tui_startup.py      # TUI 启动
└── performance/
    └── test_throughput.py       # @pytest.mark.perf
```

### 5.2 测试编写规范

```python
# ✅ 使用 pytest-asyncio
import pytest

@pytest.mark.asyncio
async def test_insert_and_get_story(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    db = await get_connection(db_path)
    try:
        story = StoryRecord(
            story_id="test-1",
            title="Test Story",
            status="backlog",
            current_phase="queued",
            created_at=datetime.now(tz=UTC),
            updated_at=datetime.now(tz=UTC),
        )
        await insert_story(db, story)
        result = await get_story(db, "test-1")
        assert result is not None
        assert result.title == "Test Story"
    finally:
        await db.close()
```

### 5.3 运行测试

```bash
# 全量测试
uv run pytest

# 按层级
uv run pytest tests/unit/
uv run pytest tests/integration/
uv run pytest tests/smoke/

# 按模块
uv run pytest tests/unit/test_recovery.py -v

# 性能测试
uv run pytest tests/performance/ -m perf

# 覆盖率
uv run pytest --cov=ato --cov-report=term-missing
```

---

## 6. 常见开发场景

### 6.1 调试 Orchestrator

```bash
# 查看运行状态
cat .ato/orchestrator.pid

# 查看日志
tail -f .ato/logs/*.log | python -m json.tool

# 手动触发 Nudge（唤醒 Poll Cycle）
kill -USR1 $(cat .ato/orchestrator.pid)

# 检查数据库
sqlite3 .ato/state.db "SELECT story_id, status, current_phase FROM stories"
sqlite3 .ato/state.db "SELECT approval_id, approval_type, status FROM approvals WHERE status='pending'"
```

### 6.2 调试状态机

```python
from ato.state_machine import StoryLifecycle

sm = StoryLifecycle()
print(sm.current_state)     # State.queued
sm.send("dispatch")
print(sm.current_state)     # State.planning
sm.send("plan_done")
print(sm.current_state)     # State.creating

# 查看合法事件
print(sm.allowed_events)    # ['create_done', 'block']
```

### 6.3 手动恢复操作

```bash
# 查看阻塞的 tasks
sqlite3 .ato/state.db "SELECT task_id, story_id, phase, status FROM tasks WHERE status IN ('running', 'paused')"

# 手动标记 task
sqlite3 .ato/state.db "UPDATE tasks SET status='failed' WHERE task_id='xxx'"

# 清理 stale PID 文件
rm .ato/orchestrator.pid

# 解冻 Merge Queue
sqlite3 .ato/state.db "UPDATE merge_queue_state SET frozen=0, frozen_reason=NULL WHERE id=1"
```

---

## 7. 架构约束与陷阱

### 7.1 必须遵守的约束

1. **TransitionQueue 事务边界**: 状态机不自动 commit，`update_story_status(commit=False)` 由 TQ 统一 commit
2. **单 Active Batch**: SQLite partial unique index 保证同时仅 1 个 active batch
3. **Merge 串行锁**: `current_merge_story_id` 保证同一时刻仅 1 个 story 在 merge
4. **三阶段进程清理**: 所有 subprocess 必须在 `try/finally` 中调用 `cleanup_process()`
5. **WAL 模式持久性**: WAL 是数据库级设置，`get_connection()` 会验证

### 7.2 常见陷阱

| 陷阱 | 正确做法 |
|------|----------|
| 在 TUI 模块中写业务逻辑 | 保持 TUI 代码在 `tui/` 目录，业务逻辑放非 TUI 模块 |
| 直接 `db.commit()` 在 TQ callback 中 | 使用 `commit=False`，让 TQ 统一 commit |
| 用 `eval()` 解析 `skip_when` 表达式 | 使用自定义 Tokenizer + Parser |
| Copy 后更新 descendant node | 使用 Copy 的 `descendants` 参数 |
| 在 recovery 中重复创建 approval | 先幂等检查是否已有 pending approval |
| 在 `_poll_cycle` 中阻塞 | 长操作使用 `asyncio.create_task()` 后台执行 |

---

## 8. Commit 规范

```
# 前缀类型
feat:      新功能
feat(story-4.2):  关联具体 Story
fix:       缺陷修复
chore:     维护任务
docs:      文档更新
refactor:  重构（无功能变化）
test:      测试相关

# 示例
feat(story-4.2): implement merge queue regression testing
fix: prevent duplicate session_timeout approvals
chore: update schema version to 8
docs: add architecture overview documentation
```

### PR 要求

- 简短问题/方案摘要
- 关联 Story / Epic / Issue
- 测试证据：`pytest`, `ruff`, `mypy` 输出
- TUI 变更附截图

---

## 9. 关键文件路径约定

| 路径 | 用途 |
|------|------|
| `.ato/state.db` | SQLite 数据库 |
| `.ato/orchestrator.pid` | Orchestrator PID 文件 |
| `.ato/logs/` | JSON 日志目录 |
| `ato.yaml` | 运行时配置 |
| `ato.yaml.example` | 配置模板 |
| `_bmad-output/implementation-artifacts/` | Story spec 和设计工件 |
| `_bmad-output/planning-artifacts/epics.md` | Epic 定义文件 |
| `schemas/` | JSON Schema 验证规则 |
| `schemas/prototype-template.pen` | .pen 设计模板 |

---

## 10. 环境变量与外部依赖

| 依赖 | 用途 | 检测方式 |
|------|------|----------|
| Python ≥ 3.11 | 运行时 | Preflight Layer 1 |
| Git | Worktree 管理 | Preflight Layer 1 |
| Claude CLI | AI Agent 执行 | Preflight Layer 1 |
| Codex CLI | AI Agent 执行（reviewer） | Preflight Layer 1 |
| SQLite (内置) | 状态持久化 | 自动 |

> **注意**: 不需要手动安装 SQLite——Python 内置 `sqlite3` 模块，ATO 通过 `aiosqlite` 包装使用。
