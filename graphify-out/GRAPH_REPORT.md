# Graph Report - .  (2026-04-09)

## Corpus Check
- Large corpus: 294 files · ~409,396 words. Semantic extraction will be expensive (many Claude tokens). Consider running on a subfolder, or use --no-semantic to run AST-only.

## Summary
- 6380 nodes · 29158 edges · 88 communities detected
- Extraction: 22% EXTRACTED · 78% INFERRED · 0% AMBIGUOUS · INFERRED: 22830 edges (avg confidence: 0.5)
- Token cost: 0 input · 0 output

## God Nodes (most connected - your core abstractions)
1. `StoryRecord` - 1587 edges
2. `TaskRecord` - 1169 edges
3. `TransitionQueue` - 892 edges
4. `FindingRecord` - 782 edges
5. `AdapterResult` - 743 edges
6. `ApprovalRecord` - 715 edges
7. `ProgressEvent` - 713 edges
8. `TransitionEvent` - 688 edges
9. `BmadSkillType` - 615 edges
10. `RecoveryEngine` - 605 edges

## Surprising Connections (you probably didn't know these)
- `Test Fixture: BMAD Code Review 04 (Story 2B.1 Claude CLI Adapter, 2 defer findings)` --semantically_similar_to--> `BUG-002: Claude CLI exit_code=1 False Report P1 HIGH`  [INFERRED] [semantically similar]
  tests/fixtures/bmad_code_review_04.md → docs/bug-report-2026-04-08.md
- `test_cli_init — CLI init 命令测试与渲染输出测试。` --uses--> `CheckResult`  [INFERRED]
  tests/unit/test_cli_init.py → src/ato/models/schemas.py
- `3.2 正常流程：全 PASS，退出码 0。` --uses--> `CheckResult`  [INFERRED]
  tests/unit/test_cli_init.py → src/ato/models/schemas.py
- `3.4 WARN 流程：退出码 0，摘要包含"警告"。` --uses--> `CheckResult`  [INFERRED]
  tests/unit/test_cli_init.py → src/ato/models/schemas.py
- `3.5 重新初始化检测：已有 db 时提示确认。` --uses--> `CheckResult`  [INFERRED]
  tests/unit/test_cli_init.py → src/ato/models/schemas.py

## Hyperedges (group relationships)
- **Convergent Loop Execution Pipeline (SubprocessMgr + BmadAdapter + SQLite findings + TransitionQueue)** — concept_convergent_loop, concept_subprocess_mgr, concept_bmad_adapter, concept_finding_record, concept_transition_queue [EXTRACTED 0.95]
- **Crash Recovery Triad (SQLite WAL + PID Registry + Expected Artifact)** — concept_crash_recovery, concept_sqlite_wal, concept_subprocess_mgr [EXTRACTED 0.90]
- **Approval Decision Flow (ApprovalRecord + Nudge + TransitionQueue + Orchestrator)** — concept_approval_record, concept_nudge_mechanism, concept_transition_queue, concept_orchestrator_control_plane [EXTRACTED 0.88]
- **Gradient Degradation: Convergent Loop + Stage Metadata + Dispatch Profile Form Phase 2 Escalation** — concept_convergent_loop, concept_gradient_degradation, concept_loop_stage, concept_dispatch_profile [EXTRACTED 0.95]
- **Orchestrator Core: TransitionQueue + Nudge + PID Management Form Event Loop** — concept_orchestrator, concept_transition_queue, concept_nudge, concept_pid_file_management [EXTRACTED 0.92]
- **Merge Queue Hygiene: Lock Release + Shared Porcelain Parser + Defensive second_result** — concept_merge_queue, concept_porcelain_parser, story_10_5_merge_queue_boundary_hygiene [EXTRACTED 0.90]
- **Prototype Manifest Pipeline: Generation → Gate → Prompt Injection** — concept_write_prototype_manifest, concept_design_gate, concept_ux_context_injection [EXTRACTED 0.95]
- **Git Failure Policy: Classify → Decide → Auto-Fix or Escalate** — concept_git_failure_policy_engine, concept_repairable_vs_non_repairable, concept_auto_fix_dispatch [EXTRACTED 0.90]
- **Test Policy Layering: Catalog + Phase Policy + Bounded Discovery** — concept_test_policy_layering, concept_bounded_discovery, concept_command_audit [EXTRACTED 0.90]
- **Worktree Boundary Gate Enforcement (TransitionQueue + MergeQueue + WorktreeManager)** — concept_transition_queue, concept_merge_queue, concept_worktree_manager [EXTRACTED 0.95]
- **TUI Display System (ATOApp + DashboardScreen + Dark Theme)** — concept_ato_app, concept_dashboard_screen, concept_dark_theme_tcss [EXTRACTED 0.90]
- **Planning Phase Removal Surface (StateMachine + RecoveryEngine + TransitionQueue)** — concept_story_lifecycle, concept_recovery_engine, concept_transition_queue [EXTRACTED 0.90]
- **Convergent Loop Quality Gate System** — 3_3_convergence_rate_calc, 3_3_escalation_approval, 3_2c_get_open_findings_scope, 3_2c_finding_status_model [INFERRED 0.85]
- **Designing Phase Persistence & Gate Chain** — 9_1_design_gate_check, 9_1b_force_persist_pen, 9_1b_design_gate_result, 9_1b_save_report [EXTRACTED 0.90]
- **Batch has_ui Flag → Conditional Designing Phase Skip Chain** — batch_recommend_has_ui_map, 9_3_has_ui_story_flag, 9_3_skip_when_field, 9_1_designing_phase [EXTRACTED 0.92]
- **Convergent Loop Quality Gate: Validator + FindingRecord + Fix Dispatch** — concept_deterministic_validator, concept_finding_record, concept_fix_dispatch [INFERRED 0.90]
- **Runtime Reliability: RecoveryEngine + TerminalFinalizer + DeadPIDWatchdog** — concept_recovery_engine, concept_terminal_finalizer, concept_dead_pid_watchdog [INFERRED 0.90]
- **BMAD Parsing Pipeline: DeterministicFastPath + SemanticFallback + FileFallback** — concept_deterministic_fastpath, concept_semantic_fallback, concept_validation_report_file_fallback [INFERRED 0.85]
- **Primary Bug Cascade: BUG-001 and BUG-002 jointly cause BUG-004 blocked dead-end** — bugrep_bug001, bugrep_bug002, bugrep_bug004 [EXTRACTED 0.95]
- **Worktree Boundary Controls: finalize checkpoint + review fail-closed + merge fail-closed enforce worktree invariant** — worktree_finalize_checkpoint, worktree_review_fail_closed, worktree_invariant [EXTRACTED 0.90]
- **Orchestrator Coordination Core: TransitionQueue + StateMachine + RecoveryEngine form the state management subsystem** — architecture_transition_queue, architecture_state_machine, architecture_recovery_engine [EXTRACTED 0.95]
- **TransitionQueue Serialization: ADR-003 Mandates Single-Consumer Pattern, Conflicts With Concurrent-Write AC** — story_val_02_adr003, story_val_02_issue_ac_conflict, story_val_02_asyncio_queue, story_val_07_issue_ac_conflict [EXTRACTED 1.00]
- **Crash Recovery Integration: NFR-03 Requires TransitionQueue–recovery.py Integration with drain() and SQLite Replay** — story_val_02_nfr03, story_val_02_issue_missing_recovery, story_val_02_drain_method [EXTRACTED 1.00]
- **test_state_machine.py Received Both Excellent (95/100) and Critical (45/100) QA Reviews from TEA Agent** — qa_report_02_test_state_machine, qa_report_03_test_state_machine_critical, qa_report_02_tea_agent [INFERRED 0.85]

## Communities

### Community 0 - "TUI Application & Widgets"
Cohesion: 0.01
Nodes (553): AgentActivityWidget, agent_activity — Agent 活动指示器 Widget。  实时展示 LLM agent 当前活动状态（初始化、文本、工具调用等）。 数据由外层, ATOApp, 将布局模式变化转发给 DashboardScreen。, 将终端宽度传给 DashboardScreen 以调整面板比例。, 根据终端宽度设置 ThreeQuestionHeader 的 display_mode。          独立断点（不复用 layout_mode）：, 数字键切换 Tab（tabbed 模式）或异常审批选择（three-panel 模式）。          搜索面板激活时短路，避免输入数字触发切页或审批。, 从 SQLite 加载仪表盘数据（短生命周期连接）。          每次独立打开/关闭连接，不复用，最小化写锁持有时间。         使用 ``get_ (+545 more)

### Community 1 - "Orchestrator Core & DB"
Cohesion: 0.01
Nodes (516): batch — Batch 选择核心逻辑。  Epics 解析、推荐算法、batch 确认流程。, 从 epics.md 解析所有 story 信息。      Args:         epics_path: epics.md 文件路径。, 从 sprint-status.yaml 构建 short_key → canonical key 映射。      sprint-status.yaml 中的, 为 LLM batch 推荐构建 prompt。      不传递候选列表，而是告诉 LLM 项目文件位置，     让 LLM 自行阅读 epics、spri, Batch 推荐器协议 — 可插拔替换（本地推荐 vs AI 推荐）。, 生成推荐 batch。          策略：         1. 过滤出未完成且无阻塞依赖的 stories         2. 按 epics.md, LLM 推荐失败，调用方应回退到本地推荐。, 基于 Claude LLM 的智能 batch 推荐。      让 Claude 自行阅读项目的 epics、sprint status 和代码库， (+508 more)

### Community 2 - "CLI Adapters (Claude/Codex)"
Cohesion: 0.02
Nodes (570): 执行 CLI 命令并返回结构化结果。          Args:             prompt: 发送给 CLI 的提示文本。, BaseSettings, claude_cli — Claude CLI 适配器。  通过 Claude CLI 调用 Claude，并返回结构化结果。 BMAD skills 在 OA, 构建 interactive session 的 claude CLI 命令参数列表。      Claude CLI 默认就是 interactive ses, Claude CLI 适配器。      通过 ``asyncio.create_subprocess_exec`` 执行 ``claude -p`` 命令，, 构建 claude CLI 命令参数列表。, 逐行读取 stream-json stdout，收集 result 事件并透传 ProgressEvent。          Args:, 执行 Claude CLI 并返回结构化结果。 (+562 more)

### Community 3 - "Approval System"
Cohesion: 0.01
Nodes (368): create_approval(), format_approval_summary(), format_option_labels(), get_binary_approval_labels(), get_exception_context(), get_options_for_approval(), is_binary_approval(), approval_helpers — 统一 approval 创建 API + 共享审批决策辅助函数。  所有 approval 创建统一走此模块，避免通知 / (+360 more)

### Community 4 - "BMAD Adapter & Parsing"
Cohesion: 0.03
Nodes (380): _compute_effective_verdict(), _compute_verdict(), _detect_incomplete_review_output(), _deterministic_parse(), _extract_bold_list_section(), _extract_bold_section(), _extract_bullet_items(), _extract_explicit_verdict() (+372 more)

### Community 5 - "Config Engine"
Cohesion: 0.02
Nodes (196): BaseModel, build_phase_definitions(), CLIDefaultsConfig, _default_discovery_priority(), EffectiveTestPolicy, evaluate_skip_condition(), _expand_policy_layers(), load_config() (+188 more)

### Community 6 - "ADR & Bug Reports"
Cohesion: 0.01
Nodes (307): ADR-01: Orchestrator is Code, not LLM, ADR-02: Python asyncio + SQLite Core Stack, ADR-04: TransitionQueue Serializes All Transitions, ADR-05: Crash Recovery via SQLite + PID + Artifact Check, ADR-06: Claude=Executor, Codex=Reviewer (Non-self-certification), ADR-10: Three Task Types (Structured Job, Interactive Session, Convergent Loop), ADR-13: Two-Layer Validation (Deterministic + Agent Review), ADR-14: Finding-Level State Tracking in SQLite (+299 more)

### Community 7 - "Batch Operations"
Cohesion: 0.02
Nodes (199): ATOError, BatchProposal, BatchRecommender, build_canonical_key_map(), build_llm_recommend_prompt(), confirm_batch(), EpicInfo, LLMBatchRecommender (+191 more)

### Community 8 - "DB Migrations"
Cohesion: 0.02
Nodes (118): _column_exists(), _migrate_v0_to_v1(), _migrate_v10_to_v11(), _migrate_v11_to_v12(), _migrate_v12_to_v13(), _migrate_v1_to_v2(), _migrate_v2_to_v3(), _migrate_v3_to_v4() (+110 more)

### Community 9 - "Unit Tests: State Machine"
Cohesion: 0.03
Nodes (62): _advance_to(), _canonical_phase_defs(), FakePhaseDefinition, _make_sm(), test_state_machine — StoryLifecycle 状态机单元测试。  覆盖 100% transition（~20+ 测试）： - 每个合, 每个合法 transition 的独立测试。, Convergent Loop 回退：validating → creating。, Convergent Loop：reviewing → fixing。 (+54 more)

### Community 10 - "Unit Tests: Config"
Cohesion: 0.06
Nodes (100): _make_adapter_result(), _make_converged_result(), _make_finding(), _make_finding_record(), _make_finding_record_for_rereview(), _make_loop(), _make_not_converged_result(), _make_parse_result() (+92 more)

### Community 11 - "Unit Tests: CLI"
Cohesion: 0.02
Nodes (57): design_artifacts 模块单元测试 (Story 9.1a AC#3–5, Story 9.1b AC#1–5)。, DESIGN_ARTIFACT_NAMES 包含 5 个核心工件。, 验证 design gate 的路径约定与 helper 一致 (AC#5)。, 验证 derive_design_artifact_paths helper 返回正确路径。, gate 的 {story_id}-ux 约定与 helper 一致。, gate 接受的扩展名与核心工件合同一致。, prompt 格式化后的路径与 helper 输出一致。, helper 返回值覆盖所有核心工件路径。 (+49 more)

### Community 12 - "Unit Tests: Core"
Cohesion: 0.05
Nodes (67): _make_group_settings(), _make_open_finding(), _make_story(), _make_task(), _mock_recovery_adapter(), _setup_project_with_manifest(), test_approval_insert_failure_rolls_back_task_status(), test_complete_from_artifact_updates_task() (+59 more)

### Community 13 - "Unit Tests: Recovery"
Cohesion: 0.06
Nodes (9): _insert_test_story(), _preflight_result(), TestCodexRegressionRunner, TestCrashRecoveryScenarios, TestMergeQueueClass, TestMergeQueueCRUD, TestRegressionFailurePayloadContent, TestRegressionTestExecution (+1 more)

### Community 14 - "Architecture & API Docs"
Cohesion: 0.03
Nodes (77): ApprovalRecord Pydantic Model, ATO API Contracts and Data Model Reference, ATOError Exception Hierarchy (CLIAdapterError, StateTransitionError, RecoveryError, ConfigError, WorktreeError), StoryRecord Pydantic Model, TaskRecord Pydantic Model, ADR-07: Graceful Stop Marking (task.status distinguishes crash vs normal), Agent Team Orchestrator Architecture Overview, BmadAdapter (BMAD Markdown to JSON Parser) (+69 more)

### Community 15 - "Unit Tests: Schemas"
Cohesion: 0.05
Nodes (38): _all_pass_results(), _capture_render(), _cr(), _halt_results(), test_cli_init — CLI init 命令测试与渲染输出测试。, 3.4 WARN 流程：退出码 0，摘要包含"警告"。, 3.5 重新初始化检测：已有 db 时提示确认。, 3.6 重新初始化拒绝：Click abort 行为保留。 (+30 more)

### Community 16 - "Unit Tests: Worktree"
Cohesion: 0.05
Nodes (30): _mock_proc(), test_preflight — Preflight 三层检查引擎单元测试。, CLI 未安装时跳过对应 auth 检查。, 创建一个模拟的 asyncio subprocess。, include_auth=False 时跳过认证检查。, 结果顺序与执行顺序一致：Python → Claude → Codex → Git。, Layer 2 — 项目结构检查单元测试。, BMAD 配置缺少必填字段返回 HALT。 (+22 more)

### Community 17 - "Unit Tests: Convergent Loop"
Cohesion: 0.04
Nodes (11): _mock_stream_process(), _raw_to_lines(), TestAggregateUsage, TestCalculateCost, TestClassifyError, TestCodexAdapterExecute, TestCodexAdapterStreaming, TestCodexOutputFromEvents (+3 more)

### Community 18 - "Unit Tests: Approval"
Cohesion: 0.05
Nodes (31): 三重状态编码模块测试。  验证 StatusCode 完整性、所有展示语义有 icon/color/label， 以及领域状态（StoryStatus / Ap, 验证所有 TaskStatus 值都能映射到合法展示语义。, STATUS_CODES 包含所有展示语义。, 每个 StatusCode 都有非空 icon。, 每个 StatusCode 都有非空 color_var。, 每个 StatusCode 都有非空 label。, 验证 format_status 返回正确 StatusCode。, 验证所有 StoryStatus 值都能映射到合法展示语义。 (+23 more)

### Community 19 - "Unit Tests: Merge Queue"
Cohesion: 0.05
Nodes (9): _mock_stream_process(), stream_success_lines(), stream_tool_use_lines(), TestBuildCommand, TestClassifyError, TestClaudeAdapterStreaming, TestClaudeOutputFromJson, TestCleanupProcess (+1 more)

### Community 20 - "Test Fixtures & Conftest"
Cohesion: 0.07
Nodes (32): _build_artifact_mock(), _build_os_kill_mock(), _build_path_exists_fn(), _build_pid_mock(), _classify_bucket(), _create_engine(), _make_perf_story(), _make_perf_task() (+24 more)

### Community 21 - "Design Artifacts"
Cohesion: 0.07
Nodes (39): _atomic_write_json(), _atomic_write_yaml(), build_ux_context_from_manifest(), _collect_reference_exports(), derive_design_artifact_paths(), derive_design_artifact_paths_relative(), _extract_primary_frames(), force_persist_pen() (+31 more)

### Community 22 - "Logging System"
Cohesion: 0.06
Nodes (26): _build_console_formatter(), _build_json_formatter(), configure_logging(), logging — ATO 结构化日志配置。, 构建面向终端的彩色 console formatter。, 配置 ATO 标准日志。      stderr 在交互式终端默认使用彩色 console 输出；非交互场景保留 JSON。     当 log_dir 非空时, 根据配置和终端类型决定 stderr 渲染格式。, 将结构化事件整理为更适合人类阅读的控制台输出。 (+18 more)

### Community 23 - "Unit Tests: TUI"
Cohesion: 0.09
Nodes (23): test_cli_uat — ato uat CLI 命令单元测试。, --result 非 pass/fail 应报错。, fail 时缺少 --reason 应报错。, 数据库不存在应报错（环境错误，exit_code == 2）。, ato uat --result pass 路径。, 没有 running task 时 pass 应报错。, ato uat --result fail 路径。, 没有 running task 时 fail 路径应报错。 (+15 more)

### Community 24 - "Unit Tests: Dashboard"
Cohesion: 0.09
Nodes (17): _init_db_sync(), test_cli_approval — ato approvals / ato approve CLI 命令测试（Story 4.1）。, preflight_failure 使用异常详情面板展示门控字段。, 无效 decision 被拒绝，不写入 DB。, 验证 Story 4.2 新增 approval 类型的 CLI 元数据。, 同步创建 story + approval 的 helper。, 有 pending 时 rich 表格输出。, _setup_story_and_approval_sync() (+9 more)

### Community 25 - "Unit Tests: BMAD Adapter"
Cohesion: 0.06
Nodes (18): 标准 .ato/state.db 布局：即使 .ato/ 下有 ato.yaml 也应选项目根的。, 8.5 AC5: start --db-path 指向其他项目时，从该项目根做 preflight/config。, start 使用从 db_path 推导的项目根做 preflight 和配置加载，而非 cwd。, Orchestrator 已运行时拒绝重复启动，exit code 1。, 进程因默认 SIGTERM（handler 未注册）退出时，stop 也清理 PID 文件。          模拟：第一次 kill(pid, 0) 成功（进, 8.5 AC5: 从 db_path 推导项目根目录。, 标准 .ato/state.db 布局推导到祖父目录。, 自���义 db 同级目录有 ato.yaml 时推导到 db 所在目录。 (+10 more)

### Community 26 - "Designing Phase Stories"
Cohesion: 0.07
Nodes (31): ATOSettings (BaseSettings, Pydantic), build_phase_definitions() Function, Story 1.3: Declarative Config Engine, load_config() Function, PhaseDefinition Dataclass, BatchRecord Pydantic Model & batches/batch_stories DB Tables, Story 2B.5: Operator Selects Story Batch & Views Status, confirm_batch() — Single SQLite Transaction Batch Creation (+23 more)

### Community 27 - "Unit Tests: Batch"
Cohesion: 0.07
Nodes (6): test_progress_event — ProgressEvent 归一化测试。, AC 6: command_execution → tool_use with command content., AC 5: tool_use 优先于 text。, TestNormalizeClaudeEvent, TestNormalizeCodexEvent, TestProgressEventModel

### Community 28 - "Unit Tests: Cost"
Cohesion: 0.13
Nodes (17): test_cli_submit — ato submit CLI 命令单元测试。, --briefing-file 正常提交。, story 不在 interactive phase 时应失败。, Orchestrator 未运行时应跳过 nudge 但仍成功。, 交互式输入时应自动提取 artifacts_produced。, 多个 running task 时应用 sidecar PID 精确匹配当前 session 的 task。, briefing 的 story_id 与目标 story 不一致时应拒绝。, briefing 的 task_type 与当前 phase 不一致时应拒绝。 (+9 more)

### Community 29 - "Unit Tests: Subprocess"
Cohesion: 0.12
Nodes (16): _init_db_sync(), test_cli_notification — 错误格式 + 异常审批展示 + 里程碑通知 CLI 测试（Story 4.4）。, regression_failure + test_output_summary 必须在 Panel 中可见。, blocking_abnormal 的三要素展示。, needs_human_review 推荐操作与 ato approve 合法选项一致（retry）。, regression_failure 的三要素展示。, _setup_approval_sync(), TestApprovalDetailAmbiguousPrefix (+8 more)

### Community 30 - "Preflight Engine"
Cohesion: 0.13
Nodes (25): _BMadConfigCheck, _check_artifact_glob(), check_artifacts(), _check_ato_yaml(), _check_bmad_config(), _check_bmad_skills(), _check_claude_auth(), _check_cli_installed() (+17 more)

### Community 31 - "Unit Tests: Preflight"
Cohesion: 0.09
Nodes (23): _make_phase(), 辅助：在 directory 下写入一份合法 ato.yaml，返回文件路径。, 标准布局：.ato/state.db 的项目根有 ato.yaml，应正确加载。, 显式 --config 优先于所有自动发现。, 显式 --config 指向不存在的文件时应退出码非 0，而非静默降级。, 数据库不存在时退出码 2（环境错误），输出错误提示。, 自动发现全部失败时 TUI 仍以默认值启动（不报错）。, 自定义 db 路径：同目录有 ato.yaml 时应被发现。      复现场景：项目目录有 custom-state.db + ato.yaml(max_ro (+15 more)

### Community 32 - "Integration Tests: Recovery"
Cohesion: 0.26
Nodes (23): _insert_story(), _insert_task_for_story(), _make_settings(), _make_story_record(), _setup_db(), test_completed_task_still_detected(), test_creating_initial_dispatch_reuses_structured_job_pipeline(), test_creating_story_without_task_detected() (+15 more)

### Community 33 - "Recovery & Execution History"
Cohesion: 0.09
Nodes (23): FindingStatus Model (open|closed|still_open — not 'new'), get_open_findings() as Scope Source (not round_num filtered query), Validation Report: Story 3.2c Re-review Scope Narrowing, Correction: ApprovalRecord payload field (not metadata) for escalation data, Round Number Semantics Contract (fix_round vs rereview_round), Validation Report: Story 3.2d Convergence Termination Conditions, Convergence Rate Calculation (_calculate_convergence_rate), Story 3.3: Convergence Trust & Escalation Notification (+15 more)

### Community 34 - "Integration Tests: TUI"
Cohesion: 0.16
Nodes (13): _make_full_project(), _mock_proc(), test_preflight_integration — 三层编排 + 持久化集成测试。, Layer 2 有 HALT 时跳过 Layer 3（使用 bmad_config 缺失触发 HALT）。, 缺少 ato.yaml 返回 INFO（非 HALT），Layer 3 继续执行。, include_auth=False 跳过 CLI 认证检查。, 验证 SQLite 连接不在检查阶段持有（通过检查调用顺序）。, 创建一个模拟的 asyncio subprocess。 (+5 more)

### Community 35 - "Unit Tests: Nudge"
Cohesion: 0.16
Nodes (8): _contrast_ratio(), WCAG AA 对比度验证测试。  纯 Python 计算语义色在 $background 上的相对亮度比值， 要求所有前景色 ≥ 4.5:1（WCAG AA, 验证所有语义前景色在 $background 上满足 WCAG AA 4.5:1。, 每个前景色与 $background 对比度 ≥ 4.5:1。, $muted 必须是可访问变体（非 Dracula 原值 #6272a4）。, 确认 Dracula 原值 #6272a4 确实不达标，验证我们需要可访问变体。, _relative_luminance(), TestWCAGContrast

### Community 36 - "Unit Tests: Progress"
Cohesion: 0.1
Nodes (6): Story 排序逻辑单元测试。  测试 awaiting → active → running → frozen → done → info 排序， 以及同状态, running 必须紧邻 active，不得落到 frozen 之后。, sort_stories_by_status 排序测试。, running 排在 frozen 之前。, TestSortOrderConstants, TestSortStoriesByStatus

### Community 37 - "Integration Tests: WAL"
Cohesion: 0.24
Nodes (16): _make_paused_task(), _make_running_task(), _make_story(), test_complete_artifact_exists(), test_complete_calls_tq_submit(), test_crash_with_dead_pids(), test_multi_story_crash_recovery(), test_needs_human_interactive_session() (+8 more)

### Community 38 - "Integration Tests: Lifecycle"
Cohesion: 0.17
Nodes (8): _make_settings(), Orchestrator 启停端到端集成测试。, 通过 _request_shutdown（模拟 SIGTERM）触发优雅停止。          注：跳过 recovery 阶段，在 startup 完成后插, nudge.notify() 能立即唤醒轮询循环，不等定期间隔。, 真实 SIGUSR1 信号通过 send_external_nudge() 投递，验证完整信号→handler→nudge 通路。, SIGTERM 在启动窗口内到达时仍执行 _shutdown()，标记 running→paused。          注：在 startup 完成后（rec, TestOrchestratorLifecycle, TestOrchestratorShutdownIntegration

### Community 39 - "LLM Batch Recommendation"
Cohesion: 0.14
Nodes (16): CodexAdapter (codex_cli.py), Story 2B.2: Codex Agent Executes Review and Returns Findings, CodexOutput Pydantic Model, CODEX_PRICE_TABLE Constant, Rationale: Adapter Isolation Principle (ADR-08) — CLI Params 100% Encapsulated in Adapter Layer, Validation Report: Story 3.2b Fix Dispatch & Artifact Verification, git HEAD Failure Non-Blocking Path (warning then continue, _get_worktree_head returns None), Subprocess Cleanup Protocol (SIGTERM → wait(5s) → SIGKILL + cleanup_process()) (+8 more)

### Community 40 - "Story Status & Sorting"
Cohesion: 0.21
Nodes (13): ADR-003 TransitionQueue Serialization Decision, asyncio.Queue with Single Consumer Coroutine (Serialization Pattern), TransitionQueue drain() Method for Graceful Shutdown, Issue: AC-2 Contradicts ADR-003 (Concurrent vs Serial Writes), Issue: Missing TransitionQueue–Recovery Module Integration Point, NFR-03: 30-Second Crash Recovery Requirement, NFR-07 Concurrency Safety Requirement, Story Validation Result: FAIL (Transition Queue) (+5 more)

### Community 41 - "Integration Tests: Worktree"
Cohesion: 0.33
Nodes (11): _create_story_worktree(), _git(), _insert_story(), preflight_repo(), Real-git coverage for worktree boundary preflight gates., _story(), test_changed_files_come_from_name_only_for_rename(), test_pre_merge_uses_local_main_when_origin_fetch_fails() (+3 more)

### Community 42 - "State Machine"
Cohesion: 0.17
Nodes (6): HasPhaseInfo, state_machine — StoryLifecycle 状态机。  基于 python-statemachine 3.0 async API 实现 Sto, 将状态机当前阶段持久化到 SQLite（不 commit）。      将 ``phase_name``（状态机 ``current_state_value``, from_config() 消费的阶段定义协议。, save_story_state(), StateMachine

### Community 43 - "Codex Adapter Internals"
Cohesion: 0.21
Nodes (8): _aggregate_usage(), calculate_cost(), _classify_error(), _extract_text_result(), _normalize_codex_event(), _parse_jsonl(), _parse_output_file(), 解析 -o 输出文件内容。      JSON 解析成功时返回 (structured_output, text)；失败时返回 (None, raw_text)

### Community 44 - "Unit Tests: Transition Queue"
Cohesion: 0.35
Nodes (5): _init_db_sync(), test_cli_history — ato history 命令单元测试 (Story 5.2)。, 优先展示 context_briefing.artifacts_produced，fallback expected_artifact。, _setup_story_with_tasks(), TestHistoryCommand

### Community 45 - "Integration Tests: Search"
Cohesion: 0.25
Nodes (4): _make_cost_log(), test_cost_log — cost_log 表 CRUD 与聚合测试。, TestGetCostSummary, TestInsertCostLog

### Community 46 - "Adapter Base"
Cohesion: 0.22
Nodes (8): ABC, cleanup_process(), drain_stderr(), _kill_process_group(), base — 适配器基类接口与 subprocess 工具函数。, Kill 整个进程组（含 orphan 子进程）。降级为 proc.kill()。      当进程以 ``start_new_session=True`` 启, 三阶段清理协议：SIGTERM → wait(timeout) → SIGKILL(pgid) → wait。      当进程以 ``start_new_se, 后台消费 stderr 全部内容，防止管道缓冲区满导致死锁。      最多保留 ``_STDERR_MAX_BYTES`` 字节，超出部分仍然读取（避免管道阻

### Community 47 - "Unit Tests: Heartbeat"
Cohesion: 0.28
Nodes (6): test_recovery_summary_e2e — 恢复摘要集成测试 (Story 5.2)。  构造崩溃场景 → 运行 recovery → 验证摘要输出, needs_human 任务包含 CLI 快捷命令。, 构造崩溃场景：运行中的 task + crash_recovery approval。, 构造崩溃场景 → 渲染摘要 → 验证输出到 stderr。, _setup_crash_scenario(), TestRecoverySummaryAfterCrash

### Community 48 - "QA Fixture Reviews"
Cohesion: 0.25
Nodes (9): Async Event-Driven Waits (No Hard Sleeps), BDD-Style Test Naming Convention, Low-Priority Naming Convention Inconsistency (test_sm_ vs test_state_machine_), TEA Agent (Test Quality Reviewer), Test Quality Review: test_state_machine.py (Score 95/100), Critical Issue: Missing Async Fixture Teardown (DB Connection Leak), Critical Issue: Hard-Coded asyncio.sleep() in State Transition Tests, Critical Issue: Shared Mutable StateMachine Instance Across Tests (+1 more)

### Community 49 - "Architecture Reviews"
Cohesion: 0.22
Nodes (9): Clarification: claude -p Uses OAuth (Not API Key), PID-Based Registration for Crash Recovery, Future Enhancement: Plugin System for Third-Party Agent Integrations, Architecture Readiness: READY FOR IMPLEMENTATION (Review 01), SHA256-Based Finding Deduplication Algorithm, Architecture Coherence Validation (Review 05), Issue: Finding Deduplication Hash Salt Mismatch Between Sections, Issue: 3-Phase Shutdown Documented as 2-Phase in Architecture (+1 more)

### Community 50 - "Task Artifacts"
Cohesion: 0.32
Nodes (7): derive_phase_artifact_path(), task_artifacts — canonical task artifact path helpers., Return the canonical on-disk artifact path for a story/phase when one exists., Resolve the canonical artifact path for a task., Return whether a task's canonical artifact exists on disk., task_artifact_exists(), task_artifact_path()

### Community 51 - "Nudge Mechanism"
Cohesion: 0.29
Nodes (7): format_notification_message(), nudge — 外部写入通知机制。  Orchestrator 轮询循环通过 ``Nudge.wait()`` 替代固定 sleep： - 进程内 writer, 供外部进程（TUI / ``ato submit``）调用，通知 Orchestrator 立即轮询。      当前 transport 为 ``SIGUSR, 根据通知级别格式化消息文本。      Args:         level: NotificationLevel 值。         message: 原, 发送用户可见通知。      行为矩阵：     - ``urgent`` → 连续两次 terminal bell + stderr 输出（带"⚠ 紧急"前缀, send_external_nudge(), send_user_notification()

### Community 52 - "Architecture & Stories"
Cohesion: 0.25
Nodes (8): Adapter Pattern for CLI Isolation, Architecture Coherence Validation (Review 04), Five-Layer Architecture, SQLite WAL Mode Crash Recovery, ADR-001 SQLite WAL Mode and ADR-002 Embedded Database Selection, PRD Functional Requirements FR-03 and FR-04, Story Validation Result: PASS (SQLite WAL), Story: Implement SQLite WAL Mode State Store

### Community 53 - "TUI Story Detail View"
Cohesion: 0.29
Nodes (7): Real Approval Consumer: core.py::_handle_approval_decision() (not transition_queue.py), Validation Report: Story 6.3b Exception Approval Multi-Select Interaction, Correction: Plain 1-9 Key Bindings Conflict With Tabbed Mode [1]-[4] Navigation, Fuzzy Match Search Engine (story ID prefix + title substring), Story 6.5: Search Panel & Responsive Layout Refinement, SearchPanel TUI Widget (search_panel.py), Search Mode Shortcut Short-Circuit (disables Tab/approval keys while searching)

### Community 54 - "Sprint Change Proposals"
Cohesion: 0.29
Nodes (7): ADR-005 Adapter Pattern Decision, Story: Implement CLI Adapter Abstraction Layer, PRD Functional Requirements FR-12 and FR-13, Issue: Incomplete Dependency Declaration (Missing Story 1A.2), Story Validation Result: PASS (CLI Adapter), Risk: Claude CLI JSON Output Parsing Stability, Risk: OAuth Token Refresh Not Covered

### Community 55 - "Performance Tests"
Cohesion: 0.47
Nodes (3): _init_story_with_invalid_rows(), test_cli_rollback_story — ato rollback-story CLI tests., TestRollbackStoryCli

### Community 56 - "Integration Tests: Config"
Cohesion: 0.5
Nodes (2): _story(), test_save_worktree_preflight_result_serializes_changed_files()

### Community 57 - "Code Review Fixtures"
Cohesion: 0.4
Nodes (5): CI Workflow: Ruff + Mypy + Pytest (Python 3.11, 3.12), Clean Review Result (0 Findings), conftest.py Async Fixtures (pytest-asyncio auto mode), pyproject.toml Dependency Declarations, Code Review: Story 1.2 Project Scaffolding and CI Setup

### Community 58 - "Progress Tracking"
Cohesion: 0.5
Nodes (3): build_agent_progress_callback(), progress — Background agent progress logging helpers., Build a logger-backed callback for normalized agent progress events.

### Community 59 - "Story Validation Fixtures"
Cohesion: 0.67
Nodes (3): Rationale: Manifest Must Be Python-Side Post-Processing, Story 9.1d: Prototype Manifest & Downstream Consumption Contract, Validation Report: Story 9.1d Prototype Manifest

### Community 60 - "Runtime Reliability (Epic 10)"
Cohesion: 0.67
Nodes (3): BUG-006: Test Assertion Baseline Drift (initial dispatch artifact), Story 10.6: Incident Regression Suite, Integration Test: test_incident_2026_04_08.py

### Community 61 - "Designing Phase Workflow"
Cohesion: 0.67
Nodes (3): Correction: _dispatch_batch_restart() Must Also Be Covered in Regression Tests, No-Findings Passthrough Semantic (validate_fail without findings returns base prompt unchanged), Validation Report: Story 9.1e validate_fail → creating Retry Prompt & Feedback Injection

### Community 62 - "Architecture Reviews (Fixtures)"
Cohesion: 0.67
Nodes (3): Gap: Missing Codex Error Retry Budget, Architecture Readiness: READY FOR IMPLEMENTATION (Review 04), Requirements Coverage Validation (Review 04)

### Community 63 - "Architecture Coherence"
Cohesion: 0.67
Nodes (3): 14 Non-Functional Requirements Coverage, 53 Functional Requirements Coverage, Five-Layer Architecture (Review 01)

### Community 64 - "Story Validation Reports"
Cohesion: 1.0
Nodes (2): Rationale: Map crash_recovery Approvals by task_id Not story_id, Validation Report: Story 5.2 Recovery Summary & Execution History

### Community 65 - "Project Scaffolding"
Cohesion: 1.0
Nodes (2): Correction: blocked State Has No Prior Phase Metadata (must not infer from PHASE_TO_STATUS), Validation Report: Story 1.5 ato plan Phase Preview

### Community 66 - "Architecture Overview"
Cohesion: 1.0
Nodes (2): Schema Version 8 (SCHEMA_VERSION=8), Database Schema Change Workflow (migration + version bump + DDL + model)

### Community 67 - "Sprint Planning"
Cohesion: 1.0
Nodes (2): AdapterError on Non-Zero Exit Code, Issue: Missing Error Handling AC in CLI Adapter Story

### Community 68 - "Approval Routing"
Cohesion: 1.0
Nodes (0): 

### Community 69 - "Data Models"
Cohesion: 1.0
Nodes (1): 从 Claude CLI stdout JSON 解析为验证后的模型。          字段映射遵循 ADR-09：         - ``result``

### Community 70 - "Data Models (Schemas)"
Cohesion: 1.0
Nodes (1): 从解析后的 JSONL 事件列表构建验证后的模型。          字段映射：         - ``item.completed`` → ``text_r

### Community 71 - "Data Models (Records)"
Cohesion: 1.0
Nodes (1): 从 workflow 名称/别名归一化为枚举值。          支持 kebab-case workflow 名称、精确枚举值等多种输入形态。

### Community 72 - "Project README"
Cohesion: 1.0
Nodes (1): Agent Team Orchestrator Project

### Community 73 - "Agent Guidelines"
Cohesion: 1.0
Nodes (1): Repository Development Guidelines

### Community 74 - "Project Context"
Cohesion: 1.0
Nodes (1): Project Context for AI Agents

### Community 75 - "Architecture Decisions"
Cohesion: 1.0
Nodes (1): CodexAdapter (Codex CLI Wrapper)

### Community 76 - "Architecture Patterns"
Cohesion: 1.0
Nodes (1): ADR-09: Claude/Codex Output Field Mapping

### Community 77 - "Dev Guide: Standards"
Cohesion: 1.0
Nodes (1): ATO Coding Standards (Python 3.11+, Ruff, Mypy strict)

### Community 78 - "Dev Guide: Workflows"
Cohesion: 1.0
Nodes (1): Async Programming Conventions (try/finally subprocess cleanup)

### Community 79 - "Monitoring Log"
Cohesion: 1.0
Nodes (1): BUG: claude_post_result_timeout Warning (MEDIUM)

### Community 80 - "Monitoring Bugs"
Cohesion: 1.0
Nodes (1): BUG: merge_queue _run_pre_merge_gate Variable Scope (LOW)

### Community 81 - "Monitoring Recovery"
Cohesion: 1.0
Nodes (1): BUG: merge_queue Race Condition approval before lock release (MEDIUM)

### Community 82 - "Monitoring Timeline"
Cohesion: 1.0
Nodes (1): BUG: _dirty_files_from_porcelain Code Duplication (LOW)

### Community 83 - "Bug Reports: Critical"
Cohesion: 1.0
Nodes (1): BUG-006: Unit Test Assertion Not Updated P1 HIGH

### Community 84 - "Bug Reports: Medium"
Cohesion: 1.0
Nodes (1): BUG-009: _dirty_files_from_porcelain Duplicated Definition P3 LOW

### Community 85 - "Bug Reports: Low"
Cohesion: 1.0
Nodes (1): BUG-010: second_result Cross try/finally Scope P3 LOW

### Community 86 - "Architecture Validation"
Cohesion: 1.0
Nodes (1): Gap: TUI Color Theme Accessibility Contrast

### Community 87 - "Architecture Readiness"
Cohesion: 1.0
Nodes (1): Gap: Orphaned Worktree Cleanup Strategy

## Knowledge Gaps
- **421 isolated node(s):** `test_logging — 验证 configure_logging() 行为。`, `重置 logging 和 structlog 状态，防止测试间干扰。`, `configure_logging 测试套件。`, `验证 stderr 实际输出的是可解析的 JSON。`, `验证 JSON 输出直接保留中文，而非 \\uXXXX 转义。` (+416 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **Thin community `Story Validation Reports`** (2 nodes): `Rationale: Map crash_recovery Approvals by task_id Not story_id`, `Validation Report: Story 5.2 Recovery Summary & Execution History`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Project Scaffolding`** (2 nodes): `Correction: blocked State Has No Prior Phase Metadata (must not infer from PHASE_TO_STATUS)`, `Validation Report: Story 1.5 ato plan Phase Preview`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Architecture Overview`** (2 nodes): `Schema Version 8 (SCHEMA_VERSION=8)`, `Database Schema Change Workflow (migration + version bump + DDL + model)`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Sprint Planning`** (2 nodes): `AdapterError on Non-Zero Exit Code`, `Issue: Missing Error Handling AC in CLI Adapter Story`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Approval Routing`** (1 nodes): `approval.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Data Models`** (1 nodes): `从 Claude CLI stdout JSON 解析为验证后的模型。          字段映射遵循 ADR-09：         - ``result```
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Data Models (Schemas)`** (1 nodes): `从解析后的 JSONL 事件列表构建验证后的模型。          字段映射：         - ``item.completed`` → ``text_r`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Data Models (Records)`** (1 nodes): `从 workflow 名称/别名归一化为枚举值。          支持 kebab-case workflow 名称、精确枚举值等多种输入形态。`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Project README`** (1 nodes): `Agent Team Orchestrator Project`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Agent Guidelines`** (1 nodes): `Repository Development Guidelines`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Project Context`** (1 nodes): `Project Context for AI Agents`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Architecture Decisions`** (1 nodes): `CodexAdapter (Codex CLI Wrapper)`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Architecture Patterns`** (1 nodes): `ADR-09: Claude/Codex Output Field Mapping`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Dev Guide: Standards`** (1 nodes): `ATO Coding Standards (Python 3.11+, Ruff, Mypy strict)`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Dev Guide: Workflows`** (1 nodes): `Async Programming Conventions (try/finally subprocess cleanup)`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Monitoring Log`** (1 nodes): `BUG: claude_post_result_timeout Warning (MEDIUM)`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Monitoring Bugs`** (1 nodes): `BUG: merge_queue _run_pre_merge_gate Variable Scope (LOW)`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Monitoring Recovery`** (1 nodes): `BUG: merge_queue Race Condition approval before lock release (MEDIUM)`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Monitoring Timeline`** (1 nodes): `BUG: _dirty_files_from_porcelain Code Duplication (LOW)`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Bug Reports: Critical`** (1 nodes): `BUG-006: Unit Test Assertion Not Updated P1 HIGH`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Bug Reports: Medium`** (1 nodes): `BUG-009: _dirty_files_from_porcelain Duplicated Definition P3 LOW`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Bug Reports: Low`** (1 nodes): `BUG-010: second_result Cross try/finally Scope P3 LOW`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Architecture Validation`** (1 nodes): `Gap: TUI Color Theme Accessibility Contrast`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Architecture Readiness`** (1 nodes): `Gap: Orphaned Worktree Cleanup Strategy`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `StoryRecord` connect `Orchestrator Core & DB` to `TUI Application & Widgets`, `CLI Adapters (Claude/Codex)`, `Approval System`, `BMAD Adapter & Parsing`, `Config Engine`, `Batch Operations`, `DB Migrations`, `Unit Tests: State Machine`, `Unit Tests: Core`, `Unit Tests: Recovery`, `Unit Tests: TUI`, `Unit Tests: Dashboard`, `Unit Tests: Cost`, `Unit Tests: Subprocess`, `Integration Tests: WAL`, `Integration Tests: Lifecycle`, `Integration Tests: Worktree`, `Unit Tests: Transition Queue`, `Unit Tests: Heartbeat`, `Performance Tests`?**
  _High betweenness centrality (0.272) - this node is a cross-community bridge._
- **Why does `ATOApp` connect `TUI Application & Widgets` to `Batch Operations`?**
  _High betweenness centrality (0.191) - this node is a cross-community bridge._
- **Why does `ProgressEvent` connect `CLI Adapters (Claude/Codex)` to `Orchestrator Core & DB`, `Approval System`, `BMAD Adapter & Parsing`, `Batch Operations`, `Unit Tests: Core`, `Unit Tests: Convergent Loop`, `Unit Tests: Merge Queue`, `Progress Tracking`, `Unit Tests: Batch`?**
  _High betweenness centrality (0.064) - this node is a cross-community bridge._
- **Are the 1584 inferred relationships involving `StoryRecord` (e.g. with `TestContextBriefing` and `TestClaudeAdapterInteractive`) actually correct?**
  _`StoryRecord` has 1584 INFERRED edges - model-reasoned connections that need verification._
- **Are the 1166 inferred relationships involving `TaskRecord` (e.g. with `TestContextBriefing` and `TestClaudeAdapterInteractive`) actually correct?**
  _`TaskRecord` has 1166 INFERRED edges - model-reasoned connections that need verification._
- **Are the 874 inferred relationships involving `TransitionQueue` (e.g. with `TestWritePidFile` and `TestReadPidFile`) actually correct?**
  _`TransitionQueue` has 874 INFERRED edges - model-reasoned connections that need verification._
- **Are the 779 inferred relationships involving `FindingRecord` (e.g. with `TestLoadSchema` and `TestValidateArtifact`) actually correct?**
  _`FindingRecord` has 779 INFERRED edges - model-reasoned connections that need verification._