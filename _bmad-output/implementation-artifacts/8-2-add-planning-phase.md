# Story 8.2: 新增 Planning 阶段 — 使用 Claude 规划并行 Story

Status: ready-for-dev

## Story

As a 操作者,
I want 工作流在 creating 之前增加 planning 阶段，由 Claude 分析 epic/story 依赖关系并规划可并行开发的 story 批次,
so that 系统能智能地编排多 story 并行开发。

## Acceptance Criteria

1. **AC1: 状态机新增 planning 状态**
   - Given StoryLifecycle 状态机
   - When 启动一个新 story
   - Then 状态流转为 `queued → planning → creating → ...`
   - And `CANONICAL_PHASES` 首位为 `"planning"`

2. **AC2: Planning 阶段使用 Claude CLI**
   - Given ato.yaml 中定义了 planner 角色（cli: claude）
   - When planning 阶段被调度执行
   - Then 通过 Claude CLI 调用，由 Claude 分析 story 并生成并行批次建议

3. **AC3: 状态映射正确**
   - Given story 处于 planning 阶段
   - When 查询 story 状态
   - Then `PHASE_TO_STATUS` 将 planning 映射为 `"planning"` 高层状态

4. **AC4: Transition 完整**
   - Given planning 阶段完成
   - When 触发 plan_done 事件
   - Then 状态正确转换到 creating
   - And planning 阶段支持 escalate 到 blocked

5. **AC5: from_config 验证适配**
   - Given 包含 planning 阶段的 PhaseDefinition 列表
   - When 调用 `StoryLifecycle.from_config()` 验证
   - Then 验证通过（CANONICAL_PHASES 和 CANONICAL_TRANSITIONS 已更新）

6. **AC6: TUI/CLI 显示适配**
   - Given story 处于 planning 阶段
   - When 在 TUI 仪表盘或 CLI status 中查看
   - Then 正确显示 planning 阶段的图标和状态文本

## Tasks / Subtasks

- [ ] Task 1: 更新状态机定义 (AC: #1, #3, #4, #5)
  - [ ] 1.1 `src/ato/state_machine.py` `CANONICAL_PHASES` 首位插入 `"planning"`
  - [ ] 1.2 `CANONICAL_TRANSITIONS` 新增 `"planning": ("creating", None)`，原 `"creating"` 保持不变
  - [ ] 1.3 `PHASE_TO_STATUS` 新增 `"planning": "planning"`
  - [ ] 1.4 `StoryLifecycle` 类新增 `planning = State()`
  - [ ] 1.5 原 `start_create = queued.to(creating)` 改为 `start_plan = queued.to(planning)`
  - [ ] 1.6 新增 `plan_done = planning.to(creating)`
  - [ ] 1.7 `escalate` 联合 transition 加入 `planning.to(blocked)`

- [ ] Task 2: 更新 models/schemas.py (AC: #3)
  - [ ] 2.1 `StoryStatus` 类型如需扩展（若 "planning" 不在现有值中），添加 `"planning"` 值
  - [ ] 2.2 确认 `APPROVAL_TYPE_ICONS` 或其他 schema 常量无需变更

- [ ] Task 3: 更新配置模板 (AC: #2)
  - [ ] 3.1 `ato.yaml.example` roles 中新增 `planner` 角色（cli: claude）
  - [ ] 3.2 `ato.yaml.example` phases 首位插入 planning 阶段（role: planner, type: structured_job, next_on_success: creating）

- [ ] Task 4: TUI/CLI 显示适配 (AC: #6)
  - [ ] 4.1 `src/ato/cli.py` 中 `_STATUS_ICONS` / `_PHASE_ICONS` 补充 planning 阶段图标
  - [ ] 4.2 `src/ato/tui/widgets/story_status_line.py` 阶段映射补充 planning
  - [ ] 4.3 `src/ato/tui/dashboard.py` 阶段渲染适配
  - [ ] 4.4 `src/ato/tui/story_detail.py` 阶段详情适配

- [ ] Task 5: Orchestrator 事件循环适配 (AC: #2)
  - [ ] 5.1 `src/ato/core.py` `_poll_cycle()` 中 `phase_success_event` 映射补充 `"planning": "plan_done"`
  - [ ] 5.2 确认 `_dispatch_pending_tasks()` 对 structured_job 类型的 planning 阶段无需特殊处理

- [ ] Task 6: 更新测试 (AC: #1-#5)
  - [ ] 6.1 状态机 transition 测试覆盖 `queued → planning → creating` 完整路径
  - [ ] 6.2 `from_config()` 测试使用包含 planning 的 CANONICAL_PHASES
  - [ ] 6.3 修复所有引用旧 CANONICAL_PHASES / CANONICAL_TRANSITIONS 的测试

## Dev Notes

- 这是状态机核心变更，影响面较广。需确保所有引用 `CANONICAL_PHASES`、`CANONICAL_TRANSITIONS`、`PHASE_TO_STATUS` 的文件同步更新
- `from_config()` 做严格校验（phase_names == CANONICAL_PHASES），所以 CANONICAL 常量和 ato.yaml 必须同步
- planning 阶段属于 `structured_job` 类型，复用现有调度逻辑
- `start_create` transition 重命名为 `start_plan` 会影响所有通过事件名触发 transition 的代码

### Project Structure Notes

- 状态机变更波及：state_machine.py、core.py、cli.py、tui/ 多个 widget
- 需检查 `src/ato/cli.py` 中所有硬编码的阶段名引用

### References

- [Source: src/ato/state_machine.py] 完整状态机定义和 CANONICAL 常量
- [Source: src/ato/core.py#_poll_cycle] 事件循环阶段映射
- [Source: src/ato/tui/widgets/story_status_line.py] TUI 状态行阶段映射
