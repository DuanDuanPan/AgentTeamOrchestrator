# Sprint Change Proposal - Designing 阶段设计产物持久化与验收合同修正

**Author:** Enjoyjavapan163.com  
**Date:** 2026-03-28  
**Scope Classification:** Moderate  
**Affected Epic:** Epic 9

## 1. Issue Summary

Story 9.1 已经把 `designing` phase 作为真实生命周期阶段接入状态机、recovery、replay 与配置模板，但其设计产物合同与当前真实运行行为存在关键偏差，继续沿用会导致后续实现无法稳定落地。

本次 course correction 的触发问题有 4 个：

1. `claude -p` 能调用 Pencil MCP，但 Story 9.1 仍隐含把 Pencil 设计当作“自动保存到磁盘”的能力。
2. 当前 `batch_design(filePath=...)` 在成功返回后，并不保证磁盘上的 `.pen` 文件已经更新；设计内容可能只存在于 Pencil 进程内存。
3. 当前代码仍把 `.pen` 视为“加密格式，只能通过 MCP 读写”，但本地已验证的 `.pen` 样本实际为 UTF-8 JSON，至少包含 `version` / `children` / `variables` 顶层结构。
4. 当前 `check_design_gate()` 只验证 `_bmad-output/implementation-artifacts/{story_id}-ux/` 中是否存在任意 `.md/.pen/.png` 文件，无法证明 `.pen` 真正落盘，也无法保证后续开发有可消费的设计合同。

## 2. Impact Analysis

### Epic Impact

- **Epic 9 直接受影响。** Story 9.1 的“阶段插入”部分仍有效，但其“设计产物保存与 gate 合同”需要补强。
- **Story 9.2 不直接变更。** `workspace` 语义与 main/worktree 划分仍成立。
- **Story 9.3 不直接变更。** `skip_when` 与 batch spec commit 语义仍成立，但后续在 `dev_ready` 上提交规格时应能带上可靠的 UX 设计产物目录。

### Story Impact

- 保留现有 `9-1-add-designing-phase.md` 作为“phase insertion 已完成”的历史记录，不回滚。
- 新增一组 follow-up stories 承接修正工作：
  - `9.1a` 修正 Designing 设计产物合同与 `.pen` 基线
  - `9.1b` Designing 阶段强制落盘与设计快照链路
  - `9.1c` Design Gate V2 与持久化验证
  - `9.1d` Prototype Manifest 与下游消费契约

### Artifact Conflicts

- `src/ato/recovery.py`
  - 仍写有“`batch_design` 自动创建/保存”
  - 仍写有“.pen 文件是加密格式”
- `src/ato/core.py`
  - 当前 gate 仅统计任意 `.md/.pen/.png` 数量
- `tests/unit/test_core.py`
  - 当前测试把“只要有 `wireframe.md`”或“只要有空 `.pen` 文件”视为通过
- `_bmad-output/planning-artifacts/epics.md`
  - 当前没有正式的 Epic 9 分解，无法承接新 story 的 create-story 上下文

### Technical Impact

- 需要引入 `.pen` 模板文件，避免依赖 Pencil 的 save-as/save 行为。
- 需要在 designing 流程中增加“结构化强制落盘”步骤：
  - `batch_get(readDepth=99, includePathGeometry=true)` 抓取完整内存节点树
  - Python 结构化回写磁盘 `.pen`
  - 回读校验
- 需要增加两个新的设计工件层：
  - `prototype.snapshot.json`
  - `prototype.save-report.json`
- 需要在最终方案中增加 `prototype.manifest.yaml`，为开发、验证、评审提供稳定入口。

## 3. Recommended Approach

**推荐路径：Direct Adjustment（增量修正），而非回滚 Story 9.1。**

理由：

1. Story 9.1 的 phase insertion、状态机、recovery 事件映射、pre-worktree 串行控制已经是有效资产，推倒重来收益低。
2. 真正有问题的是“设计产物持久化与验收合同”，这可以通过后续 story 进行增量补强。
3. 采用追加 story 的方式，可以保留 9.1 的历史轨迹，同时让后续 dev-story 有清晰、可验证的实现边界。

**推荐拆分：1 条 corrective foundation + 3 条 implementation stories。**

| Story | 目标 | 风险控制 |
|------|------|---------|
| 9.1a | 修正合同与模板基线 | 先移除错误假设，避免后续 story 建立在错误 prompt 上 |
| 9.1b | 实现强制落盘与快照 | 把设计内存态转为可恢复磁盘真相 |
| 9.1c | 升级 Design Gate | 避免空文件/假文件通过 gate |
| 9.1d | 增加 Manifest 与下游消费 | 让开发/验证/评审真正消费设计产物 |

## 4. Detailed Change Proposals

### 4.1 Story 9.1 保持不动，但语义被收紧

**OLD**

- Story 9.1 被视为“designing 阶段的完整工程落地”

**NEW**

- Story 9.1 仅被视为“designing phase insertion 已完成”
- 设计产物保存、验证与消费合同由 `9.1a` ~ `9.1d` 继续完成

**Rationale**

保留已完成实现的有效部分，同时避免继续在错误设计产物合同上叠加实现。

### 4.2 `epics.md` 新增正式 Epic 9 分解

**OLD**

- `epics.md` 中无 Epic 9 正式章节

**NEW**

- 追加 Epic 9 正式分解，包含：
  - 9.1 已完成的 phase insertion
  - 9.1a ~ 9.1d corrective / implementation stories
  - 9.2 / 9.3 既有 follow-up stories

**Rationale**

使 create-story、后续 dev-story 与 sprint-status 拥有统一来源。

### 4.3 `sprint-status.yaml` 补入新 story

**OLD**

- Epic 9 只有 `9-1`、`9-2`、`9-3`

**NEW**

- 追加：
  - `9-1a-correct-designing-artifact-contract: ready-for-dev`
  - `9-1b-designing-force-save-snapshot-chain: ready-for-dev`
  - `9-1c-design-gate-v2-persistence-verification: ready-for-dev`
  - `9-1d-prototype-manifest-downstream-consumption: ready-for-dev`

**Rationale**

让 corrective 与 follow-up stories 进入标准 BMAD 执行链路。

## 5. Implementation Handoff

### Scope Classification

**Moderate**

原因：

- 不涉及 PRD 范围调整
- 不需要重写 9.1 已完成的 phase insertion
- 但需要新增多份 story，并影响 prompt/gate/工件合同

### Handoff Recipients

- **Scrum Master / PM**
  - 接受本次 sprint change proposal
  - 认可 Epic 9 story 拆分方式
- **后续 dev-story 执行者**
  - 按 `9.1a` → `9.1b` → `9.1c` → `9.1d` 顺序实现

### Success Criteria

1. Epic 9 在 `epics.md` 中有正式分解
2. 新增 story 文件全部为 `ready-for-dev`
3. `sprint-status.yaml` 正确追踪新 story
4. 原 `9.1` 保留历史，不再承担错误的设计产物合同

## 6. Final Recommendation

执行本次 sprint change proposal，并立即创建以下 4 个 story 工件：

1. `9.1a` 修正 Designing 设计产物合同与 `.pen` 基线
2. `9.1b` Designing 阶段强制落盘与设计快照链路
3. `9.1c` Design Gate V2 与持久化验证
4. `9.1d` Prototype Manifest 与下游消费契约

本 proposal 只修正文档工件与 story 规划，不直接修改 Python 代码。
