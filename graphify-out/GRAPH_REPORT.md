# Graph Report - .  (2026-04-12)

## Corpus Check
- 136 files · ~425,575 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 6047 nodes · 30581 edges · 62 communities detected
- Extraction: 29% EXTRACTED · 71% INFERRED · 0% AMBIGUOUS · INFERRED: 21722 edges (avg confidence: 0.5)
- Token cost: 0 input · 0 output

## God Nodes (most connected - your core abstractions)
1. `StoryRecord` - 1624 edges
2. `TaskRecord` - 1202 edges
3. `TransitionQueue` - 919 edges
4. `FindingRecord` - 801 edges
5. `ApprovalRecord` - 786 edges
6. `AdapterResult` - 755 edges
7. `ProgressEvent` - 719 edges
8. `TransitionEvent` - 708 edges
9. `BmadSkillType` - 628 edges
10. `RecoveryEngine` - 618 edges

## Surprising Connections (you probably didn't know these)
- `test_cli_init — CLI init 命令测试与渲染输出测试。` --uses--> `CheckResult`  [INFERRED]
  tests/unit/test_cli_init.py → src/ato/models/schemas.py
- `3.2 正常流程：全 PASS，退出码 0。` --uses--> `CheckResult`  [INFERRED]
  tests/unit/test_cli_init.py → src/ato/models/schemas.py
- `3.4 WARN 流程：退出码 0，摘要包含"警告"。` --uses--> `CheckResult`  [INFERRED]
  tests/unit/test_cli_init.py → src/ato/models/schemas.py
- `3.5 重新初始化检测：已有 db 时提示确认。` --uses--> `CheckResult`  [INFERRED]
  tests/unit/test_cli_init.py → src/ato/models/schemas.py
- `3.6 重新初始化拒绝：Click abort 行为保留。` --uses--> `CheckResult`  [INFERRED]
  tests/unit/test_cli_init.py → src/ato/models/schemas.py

## Communities

### Community 0 - "Community 0"
Cohesion: 0.01
Nodes (660): batch — Batch 选择核心逻辑。  Epics 解析、推荐算法、batch 确认流程。, 为 LLM batch 推荐构建 prompt。      不传递候选列表，而是告诉 LLM 项目文件位置，     让 LLM 自行阅读 epics、spri, Batch 推荐器协议 — 可插拔替换（本地推荐 vs AI 推荐）。, 生成推荐 batch。          策略：         1. 过滤出未完成且无阻塞依赖的 stories         2. 按 epics.md, LLM 推荐失败，调用方应回退到本地推荐。, 基于 Claude LLM 的智能 batch 推荐。      让 Claude 自行阅读项目的 epics、sprint status 和代码库，, LLM 推荐入口：让 Claude 自主分析项目环境并推荐。          Raises:             LLMRecommendError: 任, 从 epics.md 解析出的单个 story 信息。 (+652 more)

### Community 1 - "Community 1"
Cohesion: 0.01
Nodes (548): AgentActivityWidget, agent_activity — Agent 活动指示器 Widget。  实时展示 LLM agent 当前活动状态（初始化、文本、工具调用等）。 数据由外层, ATOApp, 将布局模式变化转发给 DashboardScreen。, 将终端宽度传给 DashboardScreen 以调整面板比例。, 根据终端宽度设置 ThreeQuestionHeader 的 display_mode。          独立断点（不复用 layout_mode）：, 数字键切换 Tab（tabbed 模式）或异常审批选择（three-panel 模式）。          搜索面板激活时短路，避免输入数字触发切页或审批。, 从 SQLite 加载仪表盘数据（短生命周期连接）。          每次独立打开/关闭连接，不复用，最小化写锁持有时间。         使用 ``get_ (+540 more)

### Community 2 - "Community 2"
Cohesion: 0.01
Nodes (461): BaseAdapter, BaseAdapter, BmadAdapter, build_interactive_command(), _classify_error(), ClaudeAdapter, _normalize_claude_event(), claude_cli — Claude CLI 适配器。  通过 Claude CLI 调用 Claude，并返回结构化结果。 BMAD skills 在 OA (+453 more)

### Community 3 - "Community 3"
Cohesion: 0.02
Nodes (343): 执行 CLI 命令并返回结构化结果。          Args:             prompt: 发送给 CLI 的提示文本。, BaseModel, BaseSettings, ATOSettings, CostConfig, PhaseTestPolicyConfig, RoleConfig, TestLayerConfig (+335 more)

### Community 4 - "Community 4"
Cohesion: 0.03
Nodes (357): ParseFailureNotifier, bmad_adapter — BMAD Markdown → JSON 语义解析。  将 BMAD skill 产生的 Markdown / text arti, 解析 story-validation 输出格式。, 解析 architecture-review 输出格式。, 解析 QA report 中 canonical `## Commands Executed` section。, 解析 QA/test-review 输出格式。, 解析 QA report 的 issue section（Critical Issues / Recommendations）。, BMAD Markdown → JSON 解析适配器。      推荐调用链：     ``ClaudeAdapter.execute()`` / ``Code (+349 more)

### Community 5 - "Community 5"
Cohesion: 0.02
Nodes (178): build_phase_definitions(), CLIDefaultsConfig, _command_is_node_bootstrap(), _command_uses_node_package_manager(), _default_discovery_priority(), evaluate_skip_condition(), _expand_policy_layers(), load_config() (+170 more)

### Community 6 - "Community 6"
Cohesion: 0.03
Nodes (184): ATOError, BatchProposal, BatchRecommender, build_canonical_key_map(), build_llm_recommend_prompt(), confirm_batch(), EpicInfo, LLMBatchRecommender (+176 more)

### Community 7 - "Community 7"
Cohesion: 0.02
Nodes (120): _column_exists(), _migrate_v0_to_v1(), _migrate_v10_to_v11(), _migrate_v11_to_v12(), _migrate_v12_to_v13(), _migrate_v13_to_v14(), _migrate_v1_to_v2(), _migrate_v2_to_v3() (+112 more)

### Community 8 - "Community 8"
Cohesion: 0.06
Nodes (100): _make_adapter_result(), _make_converged_result(), _make_finding(), _make_finding_record(), _make_finding_record_for_rereview(), _make_loop(), _make_not_converged_result(), _make_parse_result() (+92 more)

### Community 9 - "Community 9"
Cohesion: 0.02
Nodes (57): design_artifacts 模块单元测试 (Story 9.1a AC#3–5, Story 9.1b AC#1–5)。, DESIGN_ARTIFACT_NAMES 包含 5 个核心工件。, 验证 design gate 的路径约定与 helper 一致 (AC#5)。, 验证 derive_design_artifact_paths helper 返回正确路径。, gate 的 {story_id}-ux 约定与 helper 一致。, gate 接受的扩展名与核心工件合同一致。, prompt 格式化后的路径与 helper 输出一致。, helper 返回值覆盖所有核心工件路径。 (+49 more)

### Community 10 - "Community 10"
Cohesion: 0.03
Nodes (56): _advance_to(), _canonical_phase_defs(), FakePhaseDefinition, _make_sm(), test_state_machine — StoryLifecycle 状态机单元测试。  覆盖 100% transition（~20+ 测试）： - 每个合, 每个合法 transition 的独立测试。, Convergent Loop 回退：validating → creating。, Convergent Loop：reviewing → fixing。 (+48 more)

### Community 11 - "Community 11"
Cohesion: 0.03
Nodes (35): _mock_stream_process(), test_codex_adapter — Codex CLI 适配器单元测试。, R3: item.type != agent_message 应被跳过。, R1: -o 为纯文本时 text_result 应回填，不丢失。, model=None 时返回 0.0（安全降级）。, cached > input 时 uncached 为 0。, Create a mock process for streaming tests., Convert raw JSONL string to list of bytes lines for streaming mock. (+27 more)

### Community 12 - "Community 12"
Cohesion: 0.03
Nodes (50): db_ready(), _make_proc_mock(), _make_story(), mgr(), test_worktree_mgr — WorktreeManager 单元测试。  所有 git 命令通过 mock asyncio.create_subpr, revert 失败返回 (False, stderr)。, batch_spec_commit() 只提交 batch 自有 main-workspace 产物。, spec、validation report、UX 目录和 sprint-status 被正确 stage 并 commit。 (+42 more)

### Community 13 - "Community 13"
Cohesion: 0.06
Nodes (36): _artifact_exists(), _build_creating_prompt_with_findings(), _build_designing_group_body(), _build_developing_prompt_with_suggestion_findings(), _build_fix_resume_phase_context(), _build_fix_resume_phase_context_for_story(), _build_fixing_prompt_from_db(), build_group_prompt() (+28 more)

### Community 14 - "Community 14"
Cohesion: 0.05
Nodes (70): _apply_pragmas(), complete_batch(), complete_merge(), count_findings_by_severity(), count_tasks_by_status(), dequeue_next_merge(), _dt_to_iso(), enqueue_merge() (+62 more)

### Community 15 - "Community 15"
Cohesion: 0.05
Nodes (38): _all_pass_results(), _capture_render(), _cr(), _halt_results(), test_cli_init — CLI init 命令测试与渲染输出测试。, 3.4 WARN 流程：退出码 0，摘要包含"警告"。, 3.5 重新初始化检测：已有 db 时提示确认。, 3.6 重新初始化拒绝：Click abort 行为保留。 (+30 more)

### Community 16 - "Community 16"
Cohesion: 0.05
Nodes (30): _mock_proc(), test_preflight — Preflight 三层检查引擎单元测试。, CLI 未安装时跳过对应 auth 检查。, 创建一个模拟的 asyncio subprocess。, include_auth=False 时跳过认证检查。, 结果顺序与执行顺序一致：Python → Claude → Codex → Git。, Layer 2 — 项目结构检查单元测试。, BMAD 配置缺少必填字段返回 HALT。 (+22 more)

### Community 17 - "Community 17"
Cohesion: 0.05
Nodes (31): 三重状态编码模块测试。  验证 StatusCode 完整性、所有展示语义有 icon/color/label， 以及领域状态（StoryStatus / Ap, 验证所有 TaskStatus 值都能映射到合法展示语义。, STATUS_CODES 包含所有展示语义。, 每个 StatusCode 都有非空 icon。, 每个 StatusCode 都有非空 color_var。, 每个 StatusCode 都有非空 label。, 验证 format_status 返回正确 StatusCode。, 验证所有 StoryStatus 值都能映射到合法展示语义。 (+23 more)

### Community 18 - "Community 18"
Cohesion: 0.06
Nodes (28): _add_shanghai_timestamp(), _build_console_formatter(), _build_json_formatter(), configure_logging(), logging — ATO 结构化日志配置。, 构建面向终端的彩色 console formatter。, 为日志事件附加固定上海时区的 ISO 时间戳。, 配置 ATO 标准日志。      stderr 在交互式终端默认使用彩色 console 输出；非交互场景保留 JSON。     当 log_dir 非空时 (+20 more)

### Community 19 - "Community 19"
Cohesion: 0.07
Nodes (39): _atomic_write_json(), _atomic_write_yaml(), build_ux_context_from_manifest(), _collect_reference_exports(), derive_design_artifact_paths(), derive_design_artifact_paths_relative(), _extract_primary_frames(), force_persist_pen() (+31 more)

### Community 20 - "Community 20"
Cohesion: 0.14
Nodes (35): _build_parse_result(), _compute_effective_verdict(), _compute_verdict(), _detect_incomplete_review_output(), _deterministic_parse(), _extract_bold_list_section(), _extract_bold_section(), _extract_bullet_items() (+27 more)

### Community 21 - "Community 21"
Cohesion: 0.06
Nodes (16): test_preflight_schema — CheckResult 模型验证、migration v2→v3、insert_preflight_result, CheckStatus 类型包含 4 种状态。, CheckLayer 类型包含 3 种层。, MIGRATIONS[4] — preflight_results 表迁移测试。, SCHEMA_VERSION 至少为 4（Story 1.4a 引入 v4）。, v3→v4 迁移成功创建 preflight_results 表。, v3→v4 迁移创建 idx_preflight_run_id 索引。, preflight_results 表有正确的列。 (+8 more)

### Community 22 - "Community 22"
Cohesion: 0.06
Nodes (18): 标准 .ato/state.db 布局：即使 .ato/ 下有 ato.yaml 也应选项目根的。, 8.5 AC5: start --db-path 指向其他项目时，从该项目根做 preflight/config。, start 使用从 db_path 推导的项目根做 preflight 和配置加载，而非 cwd。, Orchestrator 已运行时拒绝重复启动，exit code 1。, 进程因默认 SIGTERM（handler 未注册）退出时，stop 也清理 PID 文件。          模拟：第一次 kill(pid, 0) 成功（进, 8.5 AC5: 从 db_path 推导项目根目录。, 标准 .ato/state.db 布局推导到祖父目录。, 自���义 db 同级目录有 ato.yaml 时推导到 db 所在目录。 (+10 more)

### Community 23 - "Community 23"
Cohesion: 0.07
Nodes (6): test_progress_event — ProgressEvent 归一化测试。, AC 6: command_execution → tool_use with command content., AC 5: tool_use 优先于 text。, TestNormalizeClaudeEvent, TestNormalizeCodexEvent, TestProgressEventModel

### Community 24 - "Community 24"
Cohesion: 0.12
Nodes (11): _init_db_sync(), _setup_story_and_approval_sync(), TestApprovalMetadataStory42, TestAtoApprovalDetail, TestAtoApprovalsEmpty, TestAtoApprovalsList, TestAtoApproveAlreadyDecided, TestAtoApproveAmbiguousPrefix (+3 more)

### Community 25 - "Community 25"
Cohesion: 0.15
Nodes (10): _make_phase_defs(), _story(), TestPlanBlockedStatus, TestPlanConfigDegradation, TestPlanDbNotExist, TestPlanDoneStatus, TestPlanNormalFlow, TestPlanQueuedStatus (+2 more)

### Community 26 - "Community 26"
Cohesion: 0.24
Nodes (25): _insert_story(), _insert_task_for_story(), _make_settings(), _make_story_record(), _setup_db(), test_completed_task_still_detected(), test_creating_initial_dispatch_reuses_structured_job_pipeline(), test_creating_story_without_task_detected() (+17 more)

### Community 27 - "Community 27"
Cohesion: 0.08
Nodes (15): git_repo(), _make_story(), test_worktree_lifecycle — Worktree 生命周期集成测试。  在 tmp 目录初始化真实 git repo，测试 create →, 测试完整 create → verify isolation → cleanup 生命周期。, 已存在的有效 worktree 调用 create() 应幂等返回。, create() 使用自定义分支名后，cleanup() 应删除该自定义分支。, worktree 目录被外部移除后，cleanup() 仍应正常完成并删除分支。, 自定义分支 + 外部移除 worktree 后，cleanup() 仍应删除自定义分支。 (+7 more)

### Community 28 - "Community 28"
Cohesion: 0.13
Nodes (25): _BMadConfigCheck, _check_artifact_glob(), check_artifacts(), _check_ato_yaml(), _check_bmad_config(), _check_bmad_skills(), _check_claude_auth(), _check_cli_installed() (+17 more)

### Community 29 - "Community 29"
Cohesion: 0.09
Nodes (23): _make_phase(), 辅助：在 directory 下写入一份合法 ato.yaml，返回文件路径。, 标准布局：.ato/state.db 的项目根有 ato.yaml，应正确加载。, 显式 --config 优先于所有自动发现。, 显式 --config 指向不存在的文件时应退出码非 0，而非静默降级。, 数据库不存在时退出码 2（环境错误），输出错误提示。, 自动发现全部失败时 TUI 仍以默认值启动（不报错）。, 自定义 db 路径：同目录有 ato.yaml 时应被发现。      复现场景：项目目录有 custom-state.db + ato.yaml(max_ro (+15 more)

### Community 30 - "Community 30"
Cohesion: 0.12
Nodes (20): _cancel_background_tasks(), 崩溃恢复性能基准测试。  NFR1 目标：run_recovery() 同步窗口 ≤30s（MVP）。 计时边界：SQLite 扫描 + PID/artifac, 10 → 100 → 500 tasks，验证四种分类分布正确。, 取消所有后台任务并等待清理完成。      reattach PID 监控循环会永久轮询（PID mock 返回 alive），     必须 cancel 而, 性能回归检测：记录基线到 structlog，hard assert 阈值。, run_recovery() 端到端计时。, test_100_tasks_classification_distribution(), test_100_tasks_hard_threshold() (+12 more)

### Community 31 - "Community 31"
Cohesion: 0.19
Nodes (19): _make_paused_task(), _make_running_task(), _make_story(), 崩溃恢复集成测试。  端到端测试：构造崩溃前数据库状态 → 运行 RecoveryEngine → 验证恢复结果。 纯数据库状态驱动，不杀真实进程（Archit, AC6: paused tasks → 正常恢复 → pending。, 验证正常恢复输出 recovery_mode='normal'。, test_complete_artifact_exists(), test_complete_calls_tq_submit() (+11 more)

### Community 32 - "Community 32"
Cohesion: 0.16
Nodes (13): _make_full_project(), _mock_proc(), test_preflight_integration — 三层编排 + 持久化集成测试。, Layer 2 有 HALT 时跳过 Layer 3（使用 bmad_config 缺失触发 HALT）。, 缺少 ato.yaml 返回 INFO（非 HALT），Layer 3 继续执行。, include_auth=False 跳过 CLI 认证检查。, 验证 SQLite 连接不在检查阶段持有（通过检查调用顺序）。, 创建一个模拟的 asyncio subprocess。 (+5 more)

### Community 33 - "Community 33"
Cohesion: 0.16
Nodes (8): _contrast_ratio(), WCAG AA 对比度验证测试。  纯 Python 计算语义色在 $background 上的相对亮度比值， 要求所有前景色 ≥ 4.5:1（WCAG AA, 验证所有语义前景色在 $background 上满足 WCAG AA 4.5:1。, 每个前景色与 $background 对比度 ≥ 4.5:1。, $muted 必须是可访问变体（非 Dracula 原值 #6272a4）。, 确认 Dracula 原值 #6272a4 确实不达标，验证我们需要可访问变体。, _relative_luminance(), TestWCAGContrast

### Community 34 - "Community 34"
Cohesion: 0.1
Nodes (6): Story 排序逻辑单元测试。  测试 awaiting → active → running → frozen → done → info 排序， 以及同状态, running 必须紧邻 active，不得落到 frozen 之后。, sort_stories_by_status 排序测试。, running 排在 frozen 之前。, TestSortOrderConstants, TestSortStoriesByStatus

### Community 35 - "Community 35"
Cohesion: 0.1
Nodes (19): create_approval(), format_approval_summary(), format_option_labels(), get_binary_approval_labels(), get_exception_context(), get_options_for_approval(), is_binary_approval(), approval_helpers — 统一 approval 创建 API + 共享审批决策辅助函数。  所有 approval 创建统一走此模块，避免通知 / (+11 more)

### Community 36 - "Community 36"
Cohesion: 0.17
Nodes (11): _setup_db(), test_fail_marks_task_failed(), test_fail_output_includes_reason(), test_fail_sends_nudge(), test_fail_sets_uat_fail_requested_marker(), test_pass_marks_task_completed(), test_pass_output_message(), test_pass_sends_nudge() (+3 more)

### Community 37 - "Community 37"
Cohesion: 0.22
Nodes (10): _evt(), _read_story(), _seed_story(), _seed_task(), TestOrchestratorTQCacheConsistency, TestSubmitDevelopingVerification, TestUatCrashRecoveryApproval, TestUatFailConvergentLoopReentry (+2 more)

### Community 38 - "Community 38"
Cohesion: 0.28
Nodes (5): _setup_db(), TestSubmitCommand, _write_ato_yaml(), _write_pid_file(), _write_sidecar()

### Community 39 - "Community 39"
Cohesion: 0.25
Nodes (7): _init_db_sync(), _insert_cost_data(), TestCostReportByStory, TestCostReportCacheTokens, TestCostReportNoData, TestCostReportOverview, TestCostReportPeriodAggregation

### Community 40 - "Community 40"
Cohesion: 0.18
Nodes (9): _create_story_in_db(), test_state_persistence — 状态转换 + SQLite 持久化集成测试。  验证完整流程：创建状态机 → send 事件 → save_s, escalate 到 blocked 的持久化验证。, 验证 save_story_state 不自动 commit：未 commit 前读回应为旧值。, 每次 save_story_state 后 updated_at 应推进。, 状态机转换 + SQLite 持久化端到端集成测试。, 完整 happy path：每次 transition 后持久化并读回验证。, Convergent Loop：reviewing ↔ fixing 循环的持久化。 (+1 more)

### Community 41 - "Community 41"
Cohesion: 0.22
Nodes (5): _make_policy(), test_test_command_harness — QA / regression harness ledger tests., _setup_task(), TestHarnessDbPathHandling, TestResolveCommandAuditFromLedger

### Community 42 - "Community 42"
Cohesion: 0.24
Nodes (13): build_test_command_env(), _canonicalize_trigger_reason(), _command_uses_harness(), ensure_test_command_runner(), format_harnessed_command(), main(), render_command_audit_line(), _require_env() (+5 more)

### Community 43 - "Community 43"
Cohesion: 0.24
Nodes (12): build_qa_protocol_invalid_payload(), _has_policy_domain_token(), _is_policy_domain_command(), _normalize_executable(), _raise_validation_error(), test_policy_audit — QA / regression 共享 command-audit 校验。, 基于 EffectiveTestPolicy 对 canonical command audit 执行 fail-closed 校验。, 构造 `needs_human_review(reason=qa_protocol_invalid)` payload。 (+4 more)

### Community 44 - "Community 44"
Cohesion: 0.27
Nodes (3): _make_settings(), TestOrchestratorLifecycle, TestOrchestratorShutdownIntegration

### Community 45 - "Community 45"
Cohesion: 0.33
Nodes (11): _create_story_worktree(), _git(), _insert_story(), preflight_repo(), Real-git coverage for worktree boundary preflight gates., _story(), test_changed_files_come_from_name_only_for_rename(), test_pre_merge_uses_local_main_when_origin_fetch_fails() (+3 more)

### Community 46 - "Community 46"
Cohesion: 0.17
Nodes (6): HasPhaseInfo, state_machine — StoryLifecycle 状态机。  基于 python-statemachine 3.0 async API 实现 Sto, 将状态机当前阶段持久化到 SQLite（不 commit）。      将 ``phase_name``（状态机 ``current_state_value``, from_config() 消费的阶段定义协议。, save_story_state(), StateMachine

### Community 47 - "Community 47"
Cohesion: 0.23
Nodes (11): _build_needs_human_table(), _build_renderable_group(), _extract_approval_options(), _get_crash_approval_map(), recovery_summary — 恢复摘要 CLI 渲染器。  崩溃恢复完成后，将 RecoveryResult 渲染为人话版摘要输出到 stderr。 使, 构建 needs_human 任务的决策表格和命令列表。      Returns:         (table, cmd_texts) — 表格仅含任务信息, 从 approval payload 提取决策选项列表。      优先从 payload.options 读取；缺失则 fallback 到 crash_re, 从 DB 查询 crash_recovery 类型的 pending approval，按 payload.task_id 建立映射。 (+3 more)

### Community 48 - "Community 48"
Cohesion: 0.42
Nodes (9): _entry(), _policy(), test_validate_command_audit_allows_qa_bounded_fallback_discovery(), test_validate_command_audit_ignores_auxiliary_inspection(), test_validate_command_audit_rejects_budget_exceeded(), test_validate_command_audit_rejects_discovery_when_disabled(), test_validate_command_audit_rejects_optional_priority_violation(), test_validate_command_audit_rejects_required_commands_incomplete() (+1 more)

### Community 49 - "Community 49"
Cohesion: 0.25
Nodes (4): _make_cost_log(), test_cost_log — cost_log 表 CRUD 与聚合测试。, TestGetCostSummary, TestInsertCostLog

### Community 50 - "Community 50"
Cohesion: 0.22
Nodes (8): ABC, cleanup_process(), drain_stderr(), _kill_process_group(), base — 适配器基类接口与 subprocess 工具函数。, Kill 整个进程组（含 orphan 子进程）。降级为 proc.kill()。      当进程以 ``start_new_session=True`` 启, 三阶段清理协议：SIGTERM → wait(timeout) → SIGKILL(pgid) → wait。      当进程以 ``start_new_se, 后台消费 stderr 全部内容，防止管道缓冲区满导致死锁。      最多保留 ``_STDERR_MAX_BYTES`` 字节，超出部分仍然读取（避免管道阻

### Community 51 - "Community 51"
Cohesion: 0.47
Nodes (3): _init_db_sync(), _setup_story_with_tasks(), TestHistoryCommand

### Community 52 - "Community 52"
Cohesion: 0.39
Nodes (3): sprint_status 同步逻辑单元测试。, TestSprintStatusSync, _write_sprint_status()

### Community 53 - "Community 53"
Cohesion: 0.32
Nodes (7): derive_phase_artifact_path(), task_artifacts — canonical task artifact path helpers., Return the canonical on-disk artifact path for a story/phase when one exists., Resolve the canonical artifact path for a task., Return whether a task's canonical artifact exists on disk., task_artifact_exists(), task_artifact_path()

### Community 54 - "Community 54"
Cohesion: 0.32
Nodes (7): sprint_status - 同步运行态 story 状态到 sprint-status.yaml。, Map runtime phase to sprint-status.yaml story status vocabulary., Return the canonical sprint-status.yaml path for a project root., Best-effort sync of a single story's phase into sprint-status.yaml.      Returns, _resolve_story_status(), sprint_status_path_for_project(), sync_story_phase_to_sprint_status()

### Community 55 - "Community 55"
Cohesion: 0.29
Nodes (7): format_notification_message(), nudge — 外部写入通知机制。  Orchestrator 轮询循环通过 ``Nudge.wait()`` 替代固定 sleep： - 进程内 writer, 供外部进程（TUI / ``ato submit``）调用，通知 Orchestrator 立即轮询。      当前 transport 为 ``SIGUSR, 根据通知级别格式化消息文本。      Args:         level: NotificationLevel 值。         message: 原, 发送用户可见通知。      行为矩阵：     - ``urgent`` → 连续两次 terminal bell + stderr 输出（带"⚠ 紧急"前缀, send_external_nudge(), send_user_notification()

### Community 56 - "Community 56"
Cohesion: 0.4
Nodes (3): Worktree boundary preflight schema and DB helper tests., _story(), test_save_worktree_preflight_result_serializes_changed_files()

### Community 57 - "Community 57"
Cohesion: 0.8
Nodes (4): _make_finding(), _make_story(), test_story_stage_detected_from_active_task_context(), test_story_stage_falls_back_to_pending_escalation_approval()

### Community 58 - "Community 58"
Cohesion: 1.0
Nodes (0): 

### Community 59 - "Community 59"
Cohesion: 1.0
Nodes (1): 从 Claude CLI stdout JSON 解析为验证后的模型。          字段映射遵循 ADR-09：         - ``result``

### Community 60 - "Community 60"
Cohesion: 1.0
Nodes (1): 从解析后的 JSONL 事件列表构建验证后的模型。          字段映射：         - ``item.completed`` → ``text_r

### Community 61 - "Community 61"
Cohesion: 1.0
Nodes (1): 从 workflow 名称/别名归一化为枚举值。          支持 kebab-case workflow 名称、精确枚举值等多种输入形态。

## Knowledge Gaps
- **236 isolated node(s):** `test_logging — 验证 configure_logging() 行为。`, `重置 logging 和 structlog 状态，防止测试间干扰。`, `configure_logging 测试套件。`, `验证 stderr 实际输出的是可解析的 JSON。`, `验证 JSON 输出直接保留中文，而非 \\uXXXX 转义。` (+231 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **Thin community `Community 58`** (1 nodes): `approval.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 59`** (1 nodes): `从 Claude CLI stdout JSON 解析为验证后的模型。          字段映射遵循 ADR-09：         - ``result```
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 60`** (1 nodes): `从解析后的 JSONL 事件列表构建验证后的模型。          字段映射：         - ``item.completed`` → ``text_r`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 61`** (1 nodes): `从 workflow 名称/别名归一化为枚举值。          支持 kebab-case workflow 名称、精确枚举值等多种输入形态。`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `StoryRecord` connect `Community 0` to `Community 2`, `Community 3`, `Community 4`, `Community 5`, `Community 6`, `Community 7`, `Community 10`, `Community 12`, `Community 24`, `Community 25`, `Community 27`, `Community 31`, `Community 36`, `Community 37`, `Community 38`, `Community 39`, `Community 40`, `Community 41`, `Community 44`, `Community 45`, `Community 51`, `Community 56`?**
  _High betweenness centrality (0.323) - this node is a cross-community bridge._
- **Why does `ATOApp` connect `Community 1` to `Community 0`, `Community 6`?**
  _High betweenness centrality (0.222) - this node is a cross-community bridge._
- **Why does `ProgressEvent` connect `Community 4` to `Community 0`, `Community 2`, `Community 3`, `Community 6`, `Community 11`, `Community 23`?**
  _High betweenness centrality (0.073) - this node is a cross-community bridge._
- **Are the 1621 inferred relationships involving `StoryRecord` (e.g. with `TestContextBriefing` and `TestClaudeAdapterInteractive`) actually correct?**
  _`StoryRecord` has 1621 INFERRED edges - model-reasoned connections that need verification._
- **Are the 1199 inferred relationships involving `TaskRecord` (e.g. with `TestContextBriefing` and `TestClaudeAdapterInteractive`) actually correct?**
  _`TaskRecord` has 1199 INFERRED edges - model-reasoned connections that need verification._
- **Are the 899 inferred relationships involving `TransitionQueue` (e.g. with `TestWritePidFile` and `TestReadPidFile`) actually correct?**
  _`TransitionQueue` has 899 INFERRED edges - model-reasoned connections that need verification._
- **Are the 798 inferred relationships involving `FindingRecord` (e.g. with `TestLoadSchema` and `TestValidateArtifact`) actually correct?**
  _`FindingRecord` has 798 INFERRED edges - model-reasoned connections that need verification._