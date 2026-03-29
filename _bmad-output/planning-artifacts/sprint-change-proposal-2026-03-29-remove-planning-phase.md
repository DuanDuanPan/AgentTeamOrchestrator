# Sprint Change Proposal — 移除冗余 planning 阶段

**日期:** 2026-03-29
**提案人:** Enjoyjavapan163.com
**变更范围:** Minor
**状态:** ✅ 已批准 (2026-03-29)

---

## 1. 问题摘要

### 触发事件

Story 8-2 (add-planning-phase) 于 2026-03-28 完成后，在实际运行分析中发现 `planning` 阶段与 `creating` 阶段存在完全的工作重复。

### 核心问题

`planning` 和 `creating` 两个阶段的 agent prompt 均调用相同的 BMAD skill (`/bmad-create-story`)，产出相同的 artifact（story 规格文件），使用相同的 CLI 工具 (Claude)、相同的 workspace (main)、相同的 phase_type (structured_job)。

**planning prompt** (`recovery.py:108-112`):
```
为 story {story_id} 执行规划阶段。
请运行 /bmad-create-story 来分析 story 需求并生成完整的 story 规格文件。
```

**creating prompt** (`recovery.py:114-118`):
```
为 story {story_id} 创建 story 规格文件。
请运行 /bmad-create-story 来创建或修正该 story 的完整规格。
```

### 额外冗余

LLM batch 推荐 (Story 2B.5a) 在 batch 选择阶段已经完成了项目级的"规划"分析——阅读 epics、sprint status、分析依赖关系、判断代码完成度。Story 级别的"规划"实际上就是 `/bmad-create-story` 的工作，即 `creating` 阶段本身。

### 问题分类

原始需求理解偏差 — Story 8-2 设计时将 `planning` 设想为独立的规划阶段，但实际实现中与 `creating` 做了完全相同的事情。

---

## 2. 影响分析

### Epic 影响

| Epic | 影响 | 说明 |
|------|------|------|
| Epic 8 (Workflow Phase Enhancement) | 局部修改 | Story 8-2 的 planning phase 部分被回退，其余 stories 不受影响 |
| Epic 9 (Workflow Phase Restructure) | 无影响 | 9-1e 仅涉及 creating phase prompt，9-3 涉及 designing skip |
| Epic 1-7 | 无影响 | 不涉及 phase 定义 |

### Story 影响

| Story | 当前状态 | 影响 |
|-------|---------|------|
| 8-2-add-planning-phase | done | 部分回退：移除 planning phase，保留其他基础设施 |
| 新 corrective story (9-4) | — | 执行 planning phase 移除的实现工作 |

### 产物冲突

| 产物 | 冲突 | 说明 |
|------|------|------|
| PRD (prd.md) | 无冲突 | FR3 原始定义以 `creating` 开始，回归原始设计 |
| Architecture (architecture.md) | 无冲突 | 未显式定义 canonical phase 列表 |
| UX Design | 不涉及 | 无 UI 变更 |
| 源代码 | 10 个文件 | 详见第 4 节变更明细 |
| 测试 | 18 个文件 | Phase 数量断言 + setup 更新 |
| 配置 | 1 个文件 | ato.yaml.example |

### 技术影响

- **生命周期简化:** 12 个 canonical phases → 11 个
- **状态数减少:** 15 states → 14 states (含 queued/done/blocked)
- **Transition 事件减少:** 移除 `plan_done` 事件
- **Agent 调用减少:** 每个 story 少一次 Claude CLI 调用
- **StoryStatus 不变:** "planning" 仍为 creating/designing/validating 的高层聚合状态

---

## 3. 推荐方案

### 选择: 直接调整

移除 `planning` phase，使 `creating` 恢复为首个活跃阶段（回归 PRD 原始设计）。

### 理由

1. **零功能损失** — planning 和 creating 执行完全相同的工作
2. **回归原始设计** — PRD FR3 定义的生命周期以 `creating` 开始
3. **减少 agent 开销** — 每个 story 少一次 Claude CLI 调用
4. **低风险** — 属于简化而非新增，所有现有测试框架可适配

### 排除方案

- **Git 回滚 Story 8-2**: 不可行，后续 stories (9-1e, 9-3) 依赖其基础设施变更
- **MVP 重新审视**: 不适用，这是简化而非范围变更

### 工作量评估

- 工作量: **Low**
- 风险: **Low**
- 时间线影响: 无

---

## 4. 详细变更提案

### 4.1 `src/ato/state_machine.py`

**CANONICAL_PHASES:**
```python
# OLD
CANONICAL_PHASES = ("planning", "creating", "designing", ...)

# NEW
CANONICAL_PHASES = ("creating", "designing", ...)
```

**PHASE_TO_STATUS:**
```python
# OLD
"planning": "planning",
"creating": "planning",

# NEW
"creating": "planning",
# (移除 planning 条目)
```

**CANONICAL_TRANSITIONS:**
```python
# OLD
"planning": ("creating", None),
"creating": ("designing", None),

# NEW
"creating": ("designing", None),
# (移除 planning 条目)
```

**States & Transitions:**
```python
# OLD
planning = State()
start_create = queued.to(planning)
plan_done = planning.to(creating)
escalate = ... | planning.to(blocked) | ...

# NEW
start_create = queued.to(creating)
# (移除 planning State, plan_done transition, planning.to(blocked))
```

### 4.2 `src/ato/recovery.py`

```python
# OLD
_PHASE_SUCCESS_EVENT = {
    "planning": "plan_done",
    "creating": "create_done",
    ...
}
_STRUCTURED_JOB_PROMPTS = {
    "planning": "...运行 /bmad-create-story...",
    "creating": "...运行 /bmad-create-story...",
    ...
}

# NEW — 移除 planning 条目
_PHASE_SUCCESS_EVENT = {
    "creating": "create_done",
    ...
}
_STRUCTURED_JOB_PROMPTS = {
    "creating": "...运行 /bmad-create-story...",
    ...
}
```

### 4.3 `src/ato/batch.py`

```python
# OLD (confirm_batch, seq==0)
status="planning", current_phase="planning"

# NEW
status="planning", current_phase="creating"
```

### 4.4 `src/ato/transition_queue.py`

```python
# OLD
_HP_EVENTS = [
    "start_create",   # queued → planning
    "plan_done",      # planning → creating
    "create_done",    # creating → designing
    ...
]

# NEW
_HP_EVENTS = [
    "start_create",   # queued → creating
    "create_done",    # creating → designing
    ...
]
```

### 4.5 `src/ato/config.py`

```python
# OLD
_KNOWN_MAIN_PHASES = frozenset(
    {"planning", "creating", "designing", ...}
)

# NEW
_KNOWN_MAIN_PHASES = frozenset(
    {"creating", "designing", ...}
)
```

### 4.6 `ato.yaml.example`

```yaml
# OLD
roles:
  planner:
    cli: claude
  creator:
    cli: claude
phases:
  - name: planning
    role: planner
    type: structured_job
    next_on_success: creating
    workspace: main
  - name: creating
    role: creator
    ...

# NEW — 移除 planner role 和 planning phase
roles:
  creator:
    cli: claude
phases:
  - name: creating
    role: creator
    ...
```

### 4.7 `src/ato/tui/theme.py`

```python
# 移除 planning phase 图标条目
```

### 4.8 `src/ato/models/schemas.py`

```python
# StoryStatus 保持不变
# "planning" 仍为 creating/designing/validating 的高层聚合状态
```

### 4.9 测试文件 (18 files)

- Phase 数量断言: 12 → 11 (CANONICAL_PHASES), 15 → 14 (states)
- Event 数量断言: 移除 plan_done 相关
- 测试 setup 中 `current_phase="planning"` → `"creating"`
- `test_initial_dispatch.py` 全部更新为 creating phase
- Replay 事件序列测试更新

---

## 5. 实施交接

### 变更范围: Minor

可由 Dev agent 直接实施。

### 建议实施方式

创建 corrective story **9-4-remove-planning-phase**，归入 Epic 9 (Workflow Phase Restructure & Workspace Separation)。

### 交接内容

| 角色 | 职责 |
|------|------|
| Dev agent | 执行所有代码变更 + 测试适配 |
| Human (Tech Lead) | 最终代码审查 + 批准合并 |

### 成功标准

1. `planning` phase 从 CANONICAL_PHASES 中完全移除
2. 生命周期恢复为: `queued → creating → designing → validating → ... → done`
3. 所有现有测试适配通过（`uv run pytest` 全绿）
4. `ato.yaml.example` 无 planning phase 定义
5. Batch 头部 story 初始化为 `current_phase="creating"`
