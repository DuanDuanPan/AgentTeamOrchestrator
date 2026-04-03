"""test_config — 声明式配置引擎单元测试。"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from ato.config import (
    ATOSettings,
    ConvergentLoopConfig,
    PhaseDefinition,
    TimeoutConfig,
    build_phase_definitions,
    evaluate_skip_condition,
    load_config,
)
from ato.models.schemas import ConfigError, StoryRecord

# ---------------------------------------------------------------------------
# 测试辅助
# ---------------------------------------------------------------------------

_MINIMAL_VALID_YAML = """\
roles:
  dev:
    cli: claude
    model: sonnet

phases:
  - name: working
    role: dev
    type: structured_job
    next_on_success: done
"""


def _write_yaml(tmp_path: Path, content: str, filename: str = "ato.yaml") -> Path:
    """在 tmp_path 中写入 YAML 文件并返回路径。"""
    p = tmp_path / filename
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# AC1: 配置加载与验证
# ---------------------------------------------------------------------------


class TestValidConfigLoad:
    def test_load_from_example_template(self, tmp_path: Path) -> None:
        """ato.yaml.example 本身必须可被 load_config 直接解析。"""
        example = Path("ato.yaml.example")
        if not example.exists():
            pytest.skip("ato.yaml.example not found in project root")
        config = load_config(example)
        assert isinstance(config, ATOSettings)
        assert len(config.roles) >= 6
        assert len(config.phases) >= 10

    def test_load_minimal_valid_config(self, tmp_path: Path) -> None:
        """最小有效配置加载成功。"""
        p = _write_yaml(tmp_path, _MINIMAL_VALID_YAML)
        config = load_config(p)
        assert isinstance(config, ATOSettings)
        assert "dev" in config.roles
        assert config.phases[0].name == "working"

    def test_returns_ato_settings_with_all_fields(self, tmp_path: Path) -> None:
        """加载成功后返回完整 ATOSettings 对象。"""
        p = _write_yaml(tmp_path, _MINIMAL_VALID_YAML)
        config = load_config(p)
        assert isinstance(config.roles, dict)
        assert isinstance(config.phases, list)
        assert isinstance(config.convergent_loop, ConvergentLoopConfig)
        assert isinstance(config.timeout, TimeoutConfig)
        assert config.cost is None
        assert isinstance(config.model_map, dict)
        assert config.max_concurrent_agents == 4

    def test_default_values_applied(self, tmp_path: Path) -> None:
        """未指定的可选字段使用默认值。"""
        p = _write_yaml(tmp_path, _MINIMAL_VALID_YAML)
        config = load_config(p)
        assert config.convergent_loop.max_rounds == 3
        assert config.convergent_loop.convergence_threshold == 0.5
        assert config.timeout.structured_job == 3600
        assert config.timeout.interactive_session == 7200
        assert config.timeout.idle_timeout == 300
        assert config.timeout.post_result_timeout == 30
        assert config.cost is None
        assert config.model_map == {}

    def test_custom_values_override_defaults(self, tmp_path: Path) -> None:
        """显式指定的值覆盖默认值。"""
        yaml_content = """\
roles:
  dev:
    cli: claude
    model: opus
phases:
  - name: working
    role: dev
    type: structured_job
    next_on_success: done
max_concurrent_agents: 8
convergent_loop:
  max_rounds: 5
  convergence_threshold: 0.8
timeout:
  structured_job: 3600
  interactive_session: 14400
cost:
  budget_per_story: 10.0
  blocking_threshold: 20
"""
        p = _write_yaml(tmp_path, yaml_content)
        config = load_config(p)
        assert config.max_concurrent_agents == 8
        assert config.convergent_loop.max_rounds == 5
        assert config.convergent_loop.convergence_threshold == 0.8
        assert config.timeout.structured_job == 3600
        assert config.timeout.interactive_session == 14400
        assert config.cost is not None
        assert config.cost.budget_per_story == 10.0
        assert config.cost.blocking_threshold == 20

    def test_config_parse_performance(self, tmp_path: Path) -> None:
        """配置解析耗时 <= 3 秒（NFR5）。"""
        p = _write_yaml(tmp_path, _MINIMAL_VALID_YAML)
        start = time.monotonic()
        load_config(p)
        elapsed = time.monotonic() - start
        assert elapsed <= 3.0, f"配置解析耗时 {elapsed:.2f}s，超过 3s 限制"


# ---------------------------------------------------------------------------
# AC1/AC2/AC4: cost 配置段可选
# ---------------------------------------------------------------------------


class TestCostOptional:
    """Story 8.3: cost 配置段可省略。"""

    def test_no_cost_section_loads_successfully(self, tmp_path: Path) -> None:
        """AC1: 完全没有 cost 配置段时 load_config 成功且 cost 为 None。"""
        p = _write_yaml(tmp_path, _MINIMAL_VALID_YAML)
        config = load_config(p)
        assert config.cost is None

    def test_cost_null_equivalent_to_omitted(self, tmp_path: Path) -> None:
        """AC1: cost: null 与完全省略 cost 行为一致。"""
        yaml_content = """\
roles:
  dev:
    cli: claude
    model: sonnet
phases:
  - name: working
    role: dev
    type: structured_job
    next_on_success: done
cost: null
"""
        p = _write_yaml(tmp_path, yaml_content)
        config = load_config(p)
        assert config.cost is None

    def test_explicit_cost_still_works(self, tmp_path: Path) -> None:
        """AC4: 显式 cost 配置仍生效。"""
        yaml_content = """\
roles:
  dev:
    cli: claude
    model: sonnet
phases:
  - name: working
    role: dev
    type: structured_job
    next_on_success: done
cost:
  budget_per_story: 5.0
  blocking_threshold: 7
"""
        p = _write_yaml(tmp_path, yaml_content)
        config = load_config(p)
        assert config.cost is not None
        assert config.cost.budget_per_story == 5.0
        assert config.cost.blocking_threshold == 7

    def test_no_cost_skips_numeric_validation(self, tmp_path: Path) -> None:
        """AC2: cost=None 时跳过 cost 数值校验，其他字段仍校验。"""
        p = _write_yaml(tmp_path, _MINIMAL_VALID_YAML)
        config = load_config(p)
        assert config.cost is None
        # 其他字段正常校验（通过加载成功即可证明）

    def test_explicit_cost_invalid_budget_still_rejected(self, tmp_path: Path) -> None:
        """AC2: 当 cost 显式存在时，非法值仍按现有规则报错。"""
        yaml_content = """\
roles:
  dev:
    cli: claude
    model: sonnet
phases:
  - name: working
    role: dev
    type: structured_job
    next_on_success: done
cost:
  budget_per_story: 0
"""
        p = _write_yaml(tmp_path, yaml_content)
        with pytest.raises(ConfigError, match=r"budget_per_story.*> 0"):
            load_config(p)

    def test_explicit_cost_invalid_threshold_still_rejected(self, tmp_path: Path) -> None:
        """AC2: 当 cost 显式存在时，非法 blocking_threshold 仍按现有规则报错。"""
        yaml_content = """\
roles:
  dev:
    cli: claude
    model: sonnet
phases:
  - name: working
    role: dev
    type: structured_job
    next_on_success: done
cost:
  blocking_threshold: -1
"""
        p = _write_yaml(tmp_path, yaml_content)
        with pytest.raises(ConfigError, match=r"blocking_threshold.*>= 0"):
            load_config(p)


# ---------------------------------------------------------------------------
# AC2: 无效配置拒绝
# ---------------------------------------------------------------------------


class TestInvalidConfigRejected:
    def test_missing_roles_field(self, tmp_path: Path) -> None:
        """缺少 roles 时抛出 ConfigError。"""
        yaml_content = """\
phases:
  - name: working
    role: dev
    type: structured_job
    next_on_success: done
"""
        p = _write_yaml(tmp_path, yaml_content)
        with pytest.raises(ConfigError, match="配置验证失败"):
            load_config(p)

    def test_missing_phases_field(self, tmp_path: Path) -> None:
        """缺少 phases 时抛出 ConfigError。"""
        yaml_content = """\
roles:
  dev:
    cli: claude
    model: sonnet
"""
        p = _write_yaml(tmp_path, yaml_content)
        with pytest.raises(ConfigError, match="配置验证失败"):
            load_config(p)

    def test_invalid_role_reference(self, tmp_path: Path) -> None:
        """阶段引用不存在的角色时抛出 ConfigError。"""
        yaml_content = """\
roles:
  dev:
    cli: claude
    model: sonnet
phases:
  - name: working
    role: nonexistent_role
    type: structured_job
    next_on_success: done
"""
        p = _write_yaml(tmp_path, yaml_content)
        with pytest.raises(ConfigError, match=r"未定义的角色.*nonexistent_role"):
            load_config(p)

    def test_invalid_next_on_success_reference(self, tmp_path: Path) -> None:
        """next_on_success 引用不存在的阶段时抛出 ConfigError。"""
        yaml_content = """\
roles:
  dev:
    cli: claude
    model: sonnet
phases:
  - name: working
    role: dev
    type: structured_job
    next_on_success: nonexistent_phase
"""
        p = _write_yaml(tmp_path, yaml_content)
        with pytest.raises(ConfigError, match=r"未定义的目标.*nonexistent_phase"):
            load_config(p)

    def test_invalid_next_on_failure_reference(self, tmp_path: Path) -> None:
        """next_on_failure 引用不存在的阶段时抛出 ConfigError。"""
        yaml_content = """\
roles:
  dev:
    cli: claude
    model: sonnet
phases:
  - name: working
    role: dev
    type: structured_job
    next_on_success: done
    next_on_failure: nonexistent_phase
"""
        p = _write_yaml(tmp_path, yaml_content)
        with pytest.raises(ConfigError, match=r"未定义的目标.*nonexistent_phase"):
            load_config(p)

    def test_dead_loop_no_exit(self, tmp_path: Path) -> None:
        """只有回环没有出口的配置抛出 ConfigError。"""
        yaml_content = """\
roles:
  dev:
    cli: claude
    model: sonnet
phases:
  - name: phase_a
    role: dev
    type: structured_job
    next_on_success: phase_b
  - name: phase_b
    role: dev
    type: structured_job
    next_on_success: phase_a
"""
        p = _write_yaml(tmp_path, yaml_content)
        with pytest.raises(ConfigError, match=r"不存在通向.*done.*的路径"):
            load_config(p)

    def test_self_loop_no_exit(self, tmp_path: Path) -> None:
        """自循环无出口的阶段抛出 ConfigError。"""
        yaml_content = """\
roles:
  dev:
    cli: claude
    model: sonnet
phases:
  - name: stuck
    role: dev
    type: structured_job
    next_on_success: stuck
"""
        p = _write_yaml(tmp_path, yaml_content)
        with pytest.raises(ConfigError, match=r"不存在通向.*done.*的路径"):
            load_config(p)

    def test_controlled_loop_with_exit_allowed(self, tmp_path: Path) -> None:
        """受控回环（如 review/fix）但有出口的配置应被接受。"""
        yaml_content = """\
roles:
  dev:
    cli: claude
    model: sonnet
phases:
  - name: reviewing
    role: dev
    type: convergent_loop
    next_on_success: done
    next_on_failure: fixing
  - name: fixing
    role: dev
    type: structured_job
    next_on_success: reviewing
"""
        p = _write_yaml(tmp_path, yaml_content)
        config = load_config(p)
        assert len(config.phases) == 2

    def test_duplicate_phase_name(self, tmp_path: Path) -> None:
        """重复的阶段名抛出 ConfigError。"""
        yaml_content = """\
roles:
  dev:
    cli: claude
    model: sonnet
phases:
  - name: working
    role: dev
    type: structured_job
    next_on_success: done
  - name: working
    role: dev
    type: structured_job
    next_on_success: done
"""
        p = _write_yaml(tmp_path, yaml_content)
        with pytest.raises(ConfigError, match="重复定义"):
            load_config(p)

    def test_empty_phases_list(self, tmp_path: Path) -> None:
        """空的 phases 列表抛出 ConfigError。"""
        yaml_content = """\
roles:
  dev:
    cli: claude
    model: sonnet
phases: []
"""
        p = _write_yaml(tmp_path, yaml_content)
        with pytest.raises(ConfigError, match="phases 不能为空"):
            load_config(p)

    def test_invalid_cli_value(self, tmp_path: Path) -> None:
        """无效的 cli 值抛出 ConfigError。"""
        yaml_content = """\
roles:
  dev:
    cli: gpt
    model: sonnet
phases:
  - name: working
    role: dev
    type: structured_job
    next_on_success: done
"""
        p = _write_yaml(tmp_path, yaml_content)
        with pytest.raises(ConfigError):
            load_config(p)

    def test_invalid_phase_type(self, tmp_path: Path) -> None:
        """无效的 phase type 抛出 ConfigError。"""
        yaml_content = """\
roles:
  dev:
    cli: claude
    model: sonnet
phases:
  - name: working
    role: dev
    type: invalid_type
    next_on_success: done
"""
        p = _write_yaml(tmp_path, yaml_content)
        with pytest.raises(ConfigError):
            load_config(p)

    def test_invalid_sandbox_value(self, tmp_path: Path) -> None:
        """无效的 sandbox 值抛出 ConfigError。"""
        yaml_content = """\
roles:
  dev:
    cli: claude
    model: sonnet
    sandbox: full-access
phases:
  - name: working
    role: dev
    type: structured_job
    next_on_success: done
"""
        p = _write_yaml(tmp_path, yaml_content)
        with pytest.raises(ConfigError):
            load_config(p)

    def test_role_typo_field_rejected(self, tmp_path: Path) -> None:
        """角色配置中的 typo 字段（如 sanbox）被拒绝而非静默忽略。"""
        yaml_content = """\
roles:
  dev:
    cli: claude
    model: sonnet
    sanbox: read-only
phases:
  - name: working
    role: dev
    type: structured_job
    next_on_success: done
"""
        p = _write_yaml(tmp_path, yaml_content)
        with pytest.raises(ConfigError):
            load_config(p)

    def test_phase_typo_field_rejected(self, tmp_path: Path) -> None:
        """阶段配置中的 typo 字段（如 next_on_failure_typo）被拒绝而非静默忽略。"""
        yaml_content = """\
roles:
  dev:
    cli: claude
    model: sonnet
phases:
  - name: reviewing
    role: dev
    type: convergent_loop
    next_on_success: done
    next_on_failure_typo: fixing
"""
        p = _write_yaml(tmp_path, yaml_content)
        with pytest.raises(ConfigError):
            load_config(p)

    def test_convergent_loop_typo_field_rejected(self, tmp_path: Path) -> None:
        """convergent_loop 配置中的 typo 字段被拒绝。"""
        yaml_content = """\
roles:
  dev:
    cli: claude
    model: sonnet
phases:
  - name: working
    role: dev
    type: structured_job
    next_on_success: done
convergent_loop:
  max_round: 5
"""
        p = _write_yaml(tmp_path, yaml_content)
        with pytest.raises(ConfigError):
            load_config(p)

    def test_done_as_phase_name_rejected(self, tmp_path: Path) -> None:
        """保留终态 'done' 不能用作普通阶段名。"""
        yaml_content = """\
roles:
  dev:
    cli: claude
    model: sonnet
phases:
  - name: done
    role: dev
    type: structured_job
    next_on_success: done
"""
        p = _write_yaml(tmp_path, yaml_content)
        with pytest.raises(ConfigError, match=r"done.*保留.*终态"):
            load_config(p)

    def test_model_map_invalid_key(self, tmp_path: Path) -> None:
        """model_map key 引用不存在的阶段时抛出 ConfigError。"""
        yaml_content = """\
roles:
  dev:
    cli: claude
    model: sonnet
phases:
  - name: working
    role: dev
    type: structured_job
    next_on_success: done
model_map:
  nonexistent_phase: opus
"""
        p = _write_yaml(tmp_path, yaml_content)
        with pytest.raises(ConfigError, match=r"model_map.*nonexistent_phase.*未定义的阶段"):
            load_config(p)


# ---------------------------------------------------------------------------
# AC2: 数值边界验证
# ---------------------------------------------------------------------------


class TestNumericBoundaries:
    def test_max_rounds_zero(self, tmp_path: Path) -> None:
        yaml_content = """\
roles:
  dev:
    cli: claude
    model: sonnet
phases:
  - name: working
    role: dev
    type: structured_job
    next_on_success: done
convergent_loop:
  max_rounds: 0
"""
        p = _write_yaml(tmp_path, yaml_content)
        with pytest.raises(ConfigError, match=r"max_rounds.*>= 1"):
            load_config(p)

    def test_convergence_threshold_above_one(self, tmp_path: Path) -> None:
        yaml_content = """\
roles:
  dev:
    cli: claude
    model: sonnet
phases:
  - name: working
    role: dev
    type: structured_job
    next_on_success: done
convergent_loop:
  convergence_threshold: 1.5
"""
        p = _write_yaml(tmp_path, yaml_content)
        with pytest.raises(ConfigError, match=r"convergence_threshold.*\[0, 1\]"):
            load_config(p)

    def test_convergence_threshold_negative(self, tmp_path: Path) -> None:
        yaml_content = """\
roles:
  dev:
    cli: claude
    model: sonnet
phases:
  - name: working
    role: dev
    type: structured_job
    next_on_success: done
convergent_loop:
  convergence_threshold: -0.1
"""
        p = _write_yaml(tmp_path, yaml_content)
        with pytest.raises(ConfigError, match=r"convergence_threshold.*\[0, 1\]"):
            load_config(p)

    def test_max_concurrent_agents_zero(self, tmp_path: Path) -> None:
        yaml_content = """\
roles:
  dev:
    cli: claude
    model: sonnet
phases:
  - name: working
    role: dev
    type: structured_job
    next_on_success: done
max_concurrent_agents: 0
"""
        p = _write_yaml(tmp_path, yaml_content)
        with pytest.raises(ConfigError, match=r"max_concurrent_agents.*>= 1"):
            load_config(p)

    def test_negative_structured_job_timeout(self, tmp_path: Path) -> None:
        yaml_content = """\
roles:
  dev:
    cli: claude
    model: sonnet
phases:
  - name: working
    role: dev
    type: structured_job
    next_on_success: done
timeout:
  structured_job: -1
"""
        p = _write_yaml(tmp_path, yaml_content)
        with pytest.raises(ConfigError, match=r"structured_job.*> 0"):
            load_config(p)

    def test_zero_interactive_session_timeout(self, tmp_path: Path) -> None:
        yaml_content = """\
roles:
  dev:
    cli: claude
    model: sonnet
phases:
  - name: working
    role: dev
    type: structured_job
    next_on_success: done
timeout:
  interactive_session: 0
"""
        p = _write_yaml(tmp_path, yaml_content)
        with pytest.raises(ConfigError, match=r"interactive_session.*> 0"):
            load_config(p)

    def test_zero_budget_per_story(self, tmp_path: Path) -> None:
        yaml_content = """\
roles:
  dev:
    cli: claude
    model: sonnet
phases:
  - name: working
    role: dev
    type: structured_job
    next_on_success: done
cost:
  budget_per_story: 0
"""
        p = _write_yaml(tmp_path, yaml_content)
        with pytest.raises(ConfigError, match=r"budget_per_story.*> 0"):
            load_config(p)

    def test_negative_blocking_threshold(self, tmp_path: Path) -> None:
        yaml_content = """\
roles:
  dev:
    cli: claude
    model: sonnet
phases:
  - name: working
    role: dev
    type: structured_job
    next_on_success: done
cost:
  blocking_threshold: -1
"""
        p = _write_yaml(tmp_path, yaml_content)
        with pytest.raises(ConfigError, match=r"blocking_threshold.*>= 0"):
            load_config(p)

    def test_zero_polling_interval(self, tmp_path: Path) -> None:
        yaml_content = """\
roles:
  dev:
    cli: claude
    model: sonnet
phases:
  - name: working
    role: dev
    type: structured_job
    next_on_success: done
polling_interval: 0
"""
        p = _write_yaml(tmp_path, yaml_content)
        with pytest.raises(ConfigError, match=r"polling_interval.*> 0"):
            load_config(p)

    def test_negative_polling_interval(self, tmp_path: Path) -> None:
        yaml_content = """\
roles:
  dev:
    cli: claude
    model: sonnet
phases:
  - name: working
    role: dev
    type: structured_job
    next_on_success: done
polling_interval: -1
"""
        p = _write_yaml(tmp_path, yaml_content)
        with pytest.raises(ConfigError, match=r"polling_interval.*> 0"):
            load_config(p)

    def test_boundary_values_accepted(self, tmp_path: Path) -> None:
        """边界值（max_rounds=1, threshold=0, threshold=1 等）应被接受。"""
        yaml_content = """\
roles:
  dev:
    cli: claude
    model: sonnet
phases:
  - name: working
    role: dev
    type: structured_job
    next_on_success: done
convergent_loop:
  max_rounds: 1
  convergence_threshold: 0
max_concurrent_agents: 1
polling_interval: 0.5
timeout:
  structured_job: 1
  interactive_session: 1
cost:
  budget_per_story: 0.01
  blocking_threshold: 0
"""
        p = _write_yaml(tmp_path, yaml_content)
        config = load_config(p)
        assert config.convergent_loop.max_rounds == 1
        assert config.convergent_loop.convergence_threshold == 0
        assert config.cost is not None
        assert config.cost.blocking_threshold == 0
        assert config.polling_interval == 0.5


# ---------------------------------------------------------------------------
# AC3: 阶段定义生成
# ---------------------------------------------------------------------------


class TestBuildPhaseDefinitions:
    def test_basic_phase_definition(self, tmp_path: Path) -> None:
        """PhaseConfig + RoleConfig 正确合并为 PhaseDefinition。"""
        p = _write_yaml(tmp_path, _MINIMAL_VALID_YAML)
        config = load_config(p)
        defs = build_phase_definitions(config)

        assert len(defs) == 1
        d = defs[0]
        assert isinstance(d, PhaseDefinition)
        assert d.name == "working"
        assert d.role == "dev"
        assert d.cli_tool == "claude"
        assert d.model == "sonnet"
        assert d.sandbox is None
        assert d.phase_type == "structured_job"
        assert d.next_on_success == "done"
        assert d.next_on_failure is None
        assert d.timeout_seconds == 3600

    def test_cli_tool_from_role(self, tmp_path: Path) -> None:
        """cli_tool 从 RoleConfig.cli 解析。"""
        yaml_content = """\
roles:
  reviewer:
    cli: codex
    model: codex-mini-latest
    sandbox: read-only
phases:
  - name: reviewing
    role: reviewer
    type: convergent_loop
    next_on_success: done
"""
        p = _write_yaml(tmp_path, yaml_content)
        config = load_config(p)
        defs = build_phase_definitions(config)
        assert defs[0].cli_tool == "codex"
        assert defs[0].sandbox == "read-only"

    def test_model_from_model_map_override(self, tmp_path: Path) -> None:
        """model_map 中的值优先于角色默认 model。"""
        yaml_content = """\
roles:
  dev:
    cli: claude
    model: sonnet
phases:
  - name: working
    role: dev
    type: structured_job
    next_on_success: done
model_map:
  working: opus
"""
        p = _write_yaml(tmp_path, yaml_content)
        config = load_config(p)
        defs = build_phase_definitions(config)
        assert defs[0].model == "opus"

    def test_model_fallback_to_role_default(self, tmp_path: Path) -> None:
        """model_map 未覆盖时回退到角色默认 model。"""
        p = _write_yaml(tmp_path, _MINIMAL_VALID_YAML)
        config = load_config(p)
        defs = build_phase_definitions(config)
        assert defs[0].model == "sonnet"

    def test_timeout_structured_job(self, tmp_path: Path) -> None:
        """structured_job 使用 timeout.structured_job。"""
        p = _write_yaml(tmp_path, _MINIMAL_VALID_YAML)
        config = load_config(p)
        defs = build_phase_definitions(config)
        assert defs[0].timeout_seconds == config.timeout.structured_job

    def test_timeout_convergent_loop(self, tmp_path: Path) -> None:
        """convergent_loop 视为非交互阶段，使用 structured_job 超时。"""
        yaml_content = """\
roles:
  reviewer:
    cli: codex
    model: codex-mini-latest
phases:
  - name: reviewing
    role: reviewer
    type: convergent_loop
    next_on_success: done
"""
        p = _write_yaml(tmp_path, yaml_content)
        config = load_config(p)
        defs = build_phase_definitions(config)
        assert defs[0].timeout_seconds == config.timeout.structured_job

    def test_timeout_interactive_session(self, tmp_path: Path) -> None:
        """interactive_session 使用 timeout.interactive_session。"""
        yaml_content = """\
roles:
  dev:
    cli: claude
    model: sonnet
phases:
  - name: uat
    role: dev
    type: interactive_session
    next_on_success: done
"""
        p = _write_yaml(tmp_path, yaml_content)
        config = load_config(p)
        defs = build_phase_definitions(config)
        assert defs[0].timeout_seconds == config.timeout.interactive_session

    def test_phase_order_preserved(self, tmp_path: Path) -> None:
        """PhaseDefinition 顺序与 config.phases 一致。"""
        yaml_content = """\
roles:
  dev:
    cli: claude
    model: sonnet
phases:
  - name: alpha
    role: dev
    type: structured_job
    next_on_success: beta
  - name: beta
    role: dev
    type: structured_job
    next_on_success: done
"""
        p = _write_yaml(tmp_path, yaml_content)
        config = load_config(p)
        defs = build_phase_definitions(config)
        assert [d.name for d in defs] == ["alpha", "beta"]

    def test_role_without_model_loads_successfully(self, tmp_path: Path) -> None:
        """角色缺少 model 字段仍可成功加载。"""
        yaml_content = """\
roles:
  dev:
    cli: claude
phases:
  - name: working
    role: dev
    type: structured_job
    next_on_success: done
"""
        p = _write_yaml(tmp_path, yaml_content)
        config = load_config(p)
        assert config.roles["dev"].model is None

    def test_phase_definition_model_is_none_when_role_omits(self, tmp_path: Path) -> None:
        """角色未指定 model 且 model_map 无覆盖时，PhaseDefinition.model 为 None。"""
        yaml_content = """\
roles:
  dev:
    cli: claude
phases:
  - name: working
    role: dev
    type: structured_job
    next_on_success: done
"""
        p = _write_yaml(tmp_path, yaml_content)
        config = load_config(p)
        defs = build_phase_definitions(config)
        assert defs[0].model is None

    def test_model_map_overrides_none_role_model(self, tmp_path: Path) -> None:
        """model_map 可覆盖角色的 None model。"""
        yaml_content = """\
roles:
  dev:
    cli: claude
phases:
  - name: working
    role: dev
    type: structured_job
    next_on_success: done
model_map:
  working: opus
"""
        p = _write_yaml(tmp_path, yaml_content)
        config = load_config(p)
        defs = build_phase_definitions(config)
        assert defs[0].model == "opus"

    def test_no_state_machine_instantiation(self, tmp_path: Path) -> None:
        """build_phase_definitions 只返回数据，不实例化状态机。"""
        p = _write_yaml(tmp_path, _MINIMAL_VALID_YAML)
        config = load_config(p)
        defs = build_phase_definitions(config)
        assert isinstance(defs, list)
        assert all(isinstance(d, PhaseDefinition) for d in defs)


# ---------------------------------------------------------------------------
# AC4: 配置缺失引导
# ---------------------------------------------------------------------------


class TestConfigMissing:
    def test_nonexistent_path_raises_config_error(self, tmp_path: Path) -> None:
        """配置文件不存在时抛出 ConfigError。"""
        p = tmp_path / "nonexistent.yaml"
        with pytest.raises(ConfigError, match="不存在"):
            load_config(p)

    def test_error_message_contains_example_hint(self, tmp_path: Path) -> None:
        """错误消息包含从 ato.yaml.example 复制的指引。"""
        p = tmp_path / "ato.yaml"
        with pytest.raises(ConfigError, match=r"ato\.yaml\.example"):
            load_config(p)


# ---------------------------------------------------------------------------
# AC5: 配置模板（ato.yaml.example）
# ---------------------------------------------------------------------------


class TestConfigTemplate:
    def test_example_file_exists(self) -> None:
        """ato.yaml.example 存在于项目根目录。"""
        assert Path("ato.yaml.example").exists()

    def test_example_is_valid_config(self) -> None:
        """ato.yaml.example 可被 load_config 成功加载。"""
        config = load_config(Path("ato.yaml.example"))
        assert isinstance(config, ATOSettings)

    def test_example_has_required_roles(self) -> None:
        """模板包含至少 6 个角色。"""
        config = load_config(Path("ato.yaml.example"))
        expected_roles = {"creator", "validator", "developer", "reviewer", "fixer", "qa"}
        assert expected_roles.issubset(set(config.roles.keys()))

    def test_example_has_full_lifecycle_phases(self) -> None:
        """模板包含完整 story 生命周期阶段。"""
        config = load_config(Path("ato.yaml.example"))
        phase_names = [p.name for p in config.phases]
        expected = [
            "creating",
            "designing",
            "validating",
            "dev_ready",
            "developing",
            "reviewing",
            "fixing",
            "qa_testing",
            "uat",
            "merging",
            "regression",
        ]
        assert phase_names == expected

    def test_example_roles_no_model_no_sandbox_by_default(self) -> None:
        """模板中角色默认不再包含 model 和 sandbox（fixer_escalation 除外）。"""
        # fixer_escalation 需要 sandbox=workspace-write（梯度降级 Phase 2 设计要求）
        sandbox_allowed = {"fixer_escalation"}
        config = load_config(Path("ato.yaml.example"))
        for name, role in config.roles.items():
            assert role.model is None, f"角色 {name} 不应默认指定 model"
            if name not in sandbox_allowed:
                assert role.sandbox is None, f"角色 {name} 不应默认指定 sandbox"

    def test_example_contains_comments(self) -> None:
        """模板文件包含说明注释。"""
        content = Path("ato.yaml.example").read_text(encoding="utf-8")
        assert "# " in content
        assert "ato.yaml.example" in content or "FR51" in content


# ---------------------------------------------------------------------------
# Story 8.4: 多命令 regression 配置
# ---------------------------------------------------------------------------


class TestRegressionMultiCommand:
    """AC1/AC2: regression_test_commands 多命令配置加载与优先级。"""

    def test_plural_commands_loaded_in_order(self, tmp_path: Path) -> None:
        """regression_test_commands 列表按声明顺序保留。"""
        yaml_content = """\
roles:
  dev:
    cli: claude
    model: sonnet
phases:
  - name: working
    role: dev
    type: structured_job
    next_on_success: done
regression_test_commands:
  - "uv run pytest tests/unit/"
  - "uv run pytest tests/integration/"
  - "uv run pytest tests/smoke/"
"""
        p = _write_yaml(tmp_path, yaml_content)
        config = load_config(p)
        assert config.regression_test_commands == [
            "uv run pytest tests/unit/",
            "uv run pytest tests/integration/",
            "uv run pytest tests/smoke/",
        ]

    def test_get_regression_commands_returns_plural_when_both_set(self, tmp_path: Path) -> None:
        """plural 和 singular 同时存在时，get_regression_commands() 优先返回 plural。"""
        yaml_content = """\
roles:
  dev:
    cli: claude
    model: sonnet
phases:
  - name: working
    role: dev
    type: structured_job
    next_on_success: done
regression_test_command: "echo old"
regression_test_commands:
  - "uv run pytest tests/unit/"
  - "uv run pytest tests/integration/"
"""
        p = _write_yaml(tmp_path, yaml_content)
        config = load_config(p)
        cmds = config.get_regression_commands()
        assert cmds == ["uv run pytest tests/unit/", "uv run pytest tests/integration/"]

    def test_get_regression_commands_fallback_to_singular(self, tmp_path: Path) -> None:
        """仅配置 singular 时，get_regression_commands() 回退为包含该单命令的列表。"""
        yaml_content = """\
roles:
  dev:
    cli: claude
    model: sonnet
phases:
  - name: working
    role: dev
    type: structured_job
    next_on_success: done
regression_test_command: "uv run pytest"
"""
        p = _write_yaml(tmp_path, yaml_content)
        config = load_config(p)
        cmds = config.get_regression_commands()
        assert cmds == ["uv run pytest"]

    def test_get_regression_commands_default_when_neither_set(self, tmp_path: Path) -> None:
        """两者都未显式配置时，使用 singular 的默认值。"""
        p = _write_yaml(tmp_path, _MINIMAL_VALID_YAML)
        config = load_config(p)
        cmds = config.get_regression_commands()
        assert cmds == ["uv run pytest"]

    def test_plural_commands_preserves_order(self, tmp_path: Path) -> None:
        """验证顺序严格等于声明顺序。"""
        yaml_content = """\
roles:
  dev:
    cli: claude
    model: sonnet
phases:
  - name: working
    role: dev
    type: structured_job
    next_on_success: done
regression_test_commands:
  - "cmd-c"
  - "cmd-a"
  - "cmd-b"
"""
        p = _write_yaml(tmp_path, yaml_content)
        config = load_config(p)
        assert config.get_regression_commands() == ["cmd-c", "cmd-a", "cmd-b"]

    def test_empty_plural_falls_back_to_singular(self, tmp_path: Path) -> None:
        """regression_test_commands: [] 不能绕过 regression gate，必须回退到 singular。"""
        yaml_content = """\
roles:
  dev:
    cli: claude
    model: sonnet
phases:
  - name: working
    role: dev
    type: structured_job
    next_on_success: done
regression_test_commands: []
regression_test_command: "uv run pytest"
"""
        p = _write_yaml(tmp_path, yaml_content)
        config = load_config(p)
        cmds = config.get_regression_commands()
        assert cmds == ["uv run pytest"], (
            "Empty plural list must fall back to singular to prevent "
            "silently skipping regression gate"
        )


# ---------------------------------------------------------------------------
# 显式路径加载
# ---------------------------------------------------------------------------


class TestExplicitPathLoading:
    def test_custom_filename(self, tmp_path: Path) -> None:
        """自定义文件名也可被 load_config 读取。"""
        p = _write_yaml(tmp_path, _MINIMAL_VALID_YAML, filename="custom.yaml")
        config = load_config(p)
        assert isinstance(config, ATOSettings)

    def test_nested_directory(self, tmp_path: Path) -> None:
        """嵌套目录中的配置文件也可被加载。"""
        nested = tmp_path / "config" / "sub"
        nested.mkdir(parents=True)
        p = nested / "my-config.yaml"
        p.write_text(_MINIMAL_VALID_YAML, encoding="utf-8")
        config = load_config(p)
        assert isinstance(config, ATOSettings)

    def test_concurrent_loads_dont_interfere(self, tmp_path: Path) -> None:
        """连续加载不同配置互不干扰。"""
        yaml1 = """\
roles:
  a:
    cli: claude
    model: sonnet
phases:
  - name: p1
    role: a
    type: structured_job
    next_on_success: done
max_concurrent_agents: 2
"""
        yaml2 = """\
roles:
  b:
    cli: codex
    model: codex-mini-latest
phases:
  - name: p2
    role: b
    type: structured_job
    next_on_success: done
max_concurrent_agents: 6
"""
        p1 = _write_yaml(tmp_path, yaml1, "config1.yaml")
        p2 = _write_yaml(tmp_path, yaml2, "config2.yaml")

        c1 = load_config(p1)
        c2 = load_config(p2)

        assert "a" in c1.roles
        assert "b" in c2.roles
        assert c1.max_concurrent_agents == 2
        assert c2.max_concurrent_agents == 6


# ---------------------------------------------------------------------------
# Story 9.2: workspace 字段
# ---------------------------------------------------------------------------


class TestPhaseConfigWorkspace:
    """PhaseConfig / PhaseDefinition workspace 字段解析与默认值。"""

    def test_phase_config_workspace_omitted_is_none(self, tmp_path: Path) -> None:
        """PhaseConfig 不指定 workspace 时原始值为 None（由 build_phase_definitions 推断）。"""
        p = _write_yaml(tmp_path, _MINIMAL_VALID_YAML)
        config = load_config(p)
        assert config.phases[0].workspace is None

    def test_phase_config_workspace_main_parsed(self, tmp_path: Path) -> None:
        """PhaseConfig 显式指定 workspace: main 被正确解析。"""
        yaml_content = """\
roles:
  dev:
    cli: claude
phases:
  - name: planning
    role: dev
    type: structured_job
    next_on_success: done
    workspace: main
"""
        p = _write_yaml(tmp_path, yaml_content)
        config = load_config(p)
        assert config.phases[0].workspace == "main"

    def test_phase_definition_propagates_workspace(self, tmp_path: Path) -> None:
        """build_phase_definitions() 将 workspace 从 PhaseConfig 传播到 PhaseDefinition。"""
        yaml_content = """\
roles:
  dev:
    cli: claude
phases:
  - name: planning
    role: dev
    type: structured_job
    next_on_success: developing
    workspace: main
  - name: developing
    role: dev
    type: structured_job
    next_on_success: done
    workspace: worktree
"""
        p = _write_yaml(tmp_path, yaml_content)
        config = load_config(p)
        defs = build_phase_definitions(config)
        assert defs[0].workspace == "main"
        assert defs[1].workspace == "worktree"

    def test_phase_definition_workspace_inferred_from_name(self, tmp_path: Path) -> None:
        """workspace 省略时 build_phase_definitions 按 phase 名推断。"""
        yaml_content = """\
roles:
  dev:
    cli: claude
  rev:
    cli: codex
phases:
  - name: creating
    role: dev
    type: structured_job
    next_on_success: reviewing
  - name: reviewing
    role: rev
    type: convergent_loop
    next_on_success: done
    next_on_failure: creating
"""
        p = _write_yaml(tmp_path, yaml_content)
        config = load_config(p)
        defs = build_phase_definitions(config)
        # creating 在已知 main phase 列表中
        assert defs[0].workspace == "main"
        # reviewing 不在已知 main phase 列表中 → worktree
        assert defs[1].workspace == "worktree"

    def test_legacy_config_all_phases_inferred_correctly(self, tmp_path: Path) -> None:
        """旧 YAML 不写 workspace：已知 main phase 推断 main，其余推断 worktree。"""
        yaml_content = """\
roles:
  dev:
    cli: claude
  rev:
    cli: codex
phases:
  - name: creating
    role: dev
    type: structured_job
    next_on_success: validating
  - name: validating
    role: rev
    type: convergent_loop
    next_on_success: fixing
    next_on_failure: creating
  - name: fixing
    role: dev
    type: structured_job
    next_on_success: reviewing
  - name: reviewing
    role: rev
    type: convergent_loop
    next_on_success: done
    next_on_failure: fixing
"""
        p = _write_yaml(tmp_path, yaml_content)
        config = load_config(p)
        defs = build_phase_definitions(config)
        ws = {d.name: d.workspace for d in defs}
        assert ws["creating"] == "main"
        assert ws["validating"] == "main"
        assert ws["fixing"] == "worktree"
        assert ws["reviewing"] == "worktree"

    def test_explicit_workspace_overrides_inference(self, tmp_path: Path) -> None:
        """显式 workspace 覆盖按名推断的值。"""
        yaml_content = """\
roles:
  dev:
    cli: claude
phases:
  - name: planning
    role: dev
    type: structured_job
    next_on_success: done
    workspace: worktree
"""
        p = _write_yaml(tmp_path, yaml_content)
        config = load_config(p)
        defs = build_phase_definitions(config)
        assert defs[0].workspace == "worktree"

    def test_invalid_workspace_value_rejected(self, tmp_path: Path) -> None:
        """workspace 非 'main' 或 'worktree' 时验证失败。"""
        yaml_content = """\
roles:
  dev:
    cli: claude
phases:
  - name: working
    role: dev
    type: structured_job
    next_on_success: done
    workspace: invalid
"""
        p = _write_yaml(tmp_path, yaml_content)
        with pytest.raises(ConfigError):
            load_config(p)


class TestPhaseConfigSkipWhen:
    """PhaseConfig / PhaseDefinition skip_when 字段（Story 9.3）。"""

    def test_skip_when_defaults_to_none(self, tmp_path: Path) -> None:
        """skip_when 省略时默认为 None。"""
        yaml_content = """\
roles:
  dev:
    cli: claude
phases:
  - name: working
    role: dev
    type: structured_job
    next_on_success: done
"""
        p = _write_yaml(tmp_path, yaml_content)
        config = load_config(p)
        assert config.phases[0].skip_when is None

    def test_skip_when_accepted(self, tmp_path: Path) -> None:
        """skip_when 字符串正确解析。"""
        yaml_content = """\
roles:
  ux:
    cli: claude
  dev:
    cli: claude
phases:
  - name: designing
    role: ux
    type: structured_job
    next_on_success: developing
    skip_when: "not story.has_ui"
  - name: developing
    role: dev
    type: structured_job
    next_on_success: done
"""
        p = _write_yaml(tmp_path, yaml_content)
        config = load_config(p)
        assert config.phases[0].skip_when == "not story.has_ui"
        assert config.phases[1].skip_when is None

    def test_skip_when_propagated_to_phase_definition(self, tmp_path: Path) -> None:
        """build_phase_definitions() 传播 skip_when 到 PhaseDefinition。"""
        yaml_content = """\
roles:
  ux:
    cli: claude
  dev:
    cli: claude
phases:
  - name: designing
    role: ux
    type: structured_job
    next_on_success: developing
    skip_when: "not story.has_ui"
  - name: developing
    role: dev
    type: structured_job
    next_on_success: done
"""
        p = _write_yaml(tmp_path, yaml_content)
        config = load_config(p)
        defs = build_phase_definitions(config)
        assert defs[0].skip_when == "not story.has_ui"
        assert defs[1].skip_when is None


class TestEvaluateSkipCondition:
    """evaluate_skip_condition() 安全表达式求值器（Story 9.3 AC4）。"""

    @pytest.fixture()
    def story_no_ui(self) -> StoryRecord:
        from datetime import UTC, datetime

        return StoryRecord(
            story_id="s-backend",
            title="Backend story",
            status="in_progress",
            current_phase="designing",
            has_ui=False,
            created_at=datetime.now(tz=UTC),
            updated_at=datetime.now(tz=UTC),
        )

    @pytest.fixture()
    def story_with_ui(self) -> StoryRecord:
        from datetime import UTC, datetime

        return StoryRecord(
            story_id="s-frontend",
            title="Frontend story",
            status="in_progress",
            current_phase="designing",
            has_ui=True,
            created_at=datetime.now(tz=UTC),
            updated_at=datetime.now(tz=UTC),
        )

    def test_not_story_has_ui_true_when_no_ui(self, story_no_ui: StoryRecord) -> None:
        assert evaluate_skip_condition("not story.has_ui", story_no_ui) is True

    def test_not_story_has_ui_false_when_has_ui(self, story_with_ui: StoryRecord) -> None:
        assert evaluate_skip_condition("not story.has_ui", story_with_ui) is False

    def test_story_has_ui_direct(self, story_with_ui: StoryRecord) -> None:
        assert evaluate_skip_condition("story.has_ui", story_with_ui) is True

    def test_and_expression(self, story_with_ui: StoryRecord) -> None:
        assert evaluate_skip_condition("story.has_ui and story.has_ui", story_with_ui) is True

    def test_or_expression(self, story_no_ui: StoryRecord) -> None:
        assert evaluate_skip_condition("story.has_ui or not story.has_ui", story_no_ui) is True

    def test_complex_expression(self, story_no_ui: StoryRecord) -> None:
        result = evaluate_skip_condition("not story.has_ui and story.story_id", story_no_ui)
        assert result is True  # not False == True, and "s-backend" is truthy

    def test_illegal_attribute_returns_false(self, story_no_ui: StoryRecord) -> None:
        """非白名单属性安全降级为 False（不跳过）。"""
        assert evaluate_skip_condition("story.worktree_path", story_no_ui) is False

    def test_eval_not_used(self, story_no_ui: StoryRecord) -> None:
        """危险表达式不被执行，安全降级为 False。"""
        assert (
            evaluate_skip_condition("__import__('os').system('echo pwned')", story_no_ui) is False
        )

    def test_empty_expression_returns_false(self, story_no_ui: StoryRecord) -> None:
        assert evaluate_skip_condition("", story_no_ui) is False

    def test_parentheses(self, story_no_ui: StoryRecord) -> None:
        result = evaluate_skip_condition("(not story.has_ui)", story_no_ui)
        assert result is True

    def test_double_not(self, story_no_ui: StoryRecord) -> None:
        result = evaluate_skip_condition("not not story.has_ui", story_no_ui)
        assert result is False  # not not False == False


# ---------------------------------------------------------------------------
# parallel_safe 配置字段测试
# ---------------------------------------------------------------------------


class TestParallelSafeConfig:
    """parallel_safe 字段在 PhaseConfig / PhaseDefinition / phase_cfg 中的传播。"""

    def test_parallel_safe_field_in_phase_config(self) -> None:
        """PhaseConfig 默认 parallel_safe=False。"""
        from ato.config import PhaseConfig

        pc = PhaseConfig(
            name="creating",
            role="dev",
            type="structured_job",
            next_on_success="done",
        )
        assert pc.parallel_safe is False

    def test_parallel_safe_explicit_true(self) -> None:
        """PhaseConfig 可以显式设置 parallel_safe=True。"""
        from ato.config import PhaseConfig

        pc = PhaseConfig(
            name="creating",
            role="dev",
            type="structured_job",
            next_on_success="done",
            parallel_safe=True,
        )
        assert pc.parallel_safe is True

    def test_parallel_safe_default_false_in_phase_definition(self) -> None:
        """PhaseDefinition 默认 parallel_safe=False。"""
        pd = PhaseDefinition(
            name="x",
            role="dev",
            cli_tool="claude",
            model=None,
            sandbox=None,
            phase_type="structured_job",
            next_on_success="done",
            next_on_failure=None,
            timeout_seconds=1800,
        )
        assert pd.parallel_safe is False

    def test_parallel_safe_propagated_to_phase_definition(self) -> None:
        """parallel_safe: true 从 PhaseConfig 传播到 PhaseDefinition。"""
        settings = ATOSettings(
            roles={"dev": {"cli": "claude"}},  # type: ignore[dict-item]
            phases=[
                {  # type: ignore[list-item]
                    "name": "creating",
                    "role": "dev",
                    "type": "structured_job",
                    "next_on_success": "done",
                    "workspace": "main",
                    "parallel_safe": True,
                },
            ],
        )
        defs = build_phase_definitions(settings)
        assert len(defs) == 1
        assert defs[0].parallel_safe is True

    def test_parallel_safe_false_propagated(self) -> None:
        """parallel_safe: false（默认）也正确传播。"""
        settings = ATOSettings(
            roles={"dev": {"cli": "claude"}},  # type: ignore[dict-item]
            phases=[
                {  # type: ignore[list-item]
                    "name": "merging",
                    "role": "dev",
                    "type": "structured_job",
                    "next_on_success": "done",
                    "workspace": "main",
                },
            ],
        )
        defs = build_phase_definitions(settings)
        assert defs[0].parallel_safe is False

    def test_parallel_safe_in_resolve_phase_config_static(self) -> None:
        """_resolve_phase_config_static 返回 parallel_safe 字段。"""
        from ato.recovery import RecoveryEngine

        settings = ATOSettings(
            roles={"dev": {"cli": "claude"}},  # type: ignore[dict-item]
            phases=[
                {  # type: ignore[list-item]
                    "name": "creating",
                    "role": "dev",
                    "type": "structured_job",
                    "next_on_success": "done",
                    "workspace": "main",
                    "parallel_safe": True,
                },
            ],
        )
        cfg = RecoveryEngine._resolve_phase_config_static(settings, "creating")
        assert cfg["parallel_safe"] is True

    def test_parallel_safe_absent_when_settings_none(self) -> None:
        """settings=None 时 _resolve_phase_config_static 返回空 dict。"""
        from ato.recovery import RecoveryEngine

        cfg = RecoveryEngine._resolve_phase_config_static(None, "creating")
        assert cfg == {}
        assert cfg.get("parallel_safe", False) is False


# ---------------------------------------------------------------------------
# max_planning_concurrent 配置测试
# ---------------------------------------------------------------------------


class TestMaxPlanningConcurrent:
    """max_planning_concurrent 配置字段校验。"""

    def test_max_planning_concurrent_in_settings(self) -> None:
        """ATOSettings 默认 max_planning_concurrent=3。"""
        settings = ATOSettings(
            roles={"dev": {"cli": "claude"}},  # type: ignore[dict-item]
            phases=[
                {  # type: ignore[list-item]
                    "name": "working",
                    "role": "dev",
                    "type": "structured_job",
                    "next_on_success": "done",
                },
            ],
        )
        assert settings.max_planning_concurrent == 3

    def test_max_planning_concurrent_custom_value(self) -> None:
        """max_planning_concurrent 可以自定义。"""
        settings = ATOSettings(
            roles={"dev": {"cli": "claude"}},  # type: ignore[dict-item]
            phases=[
                {  # type: ignore[list-item]
                    "name": "working",
                    "role": "dev",
                    "type": "structured_job",
                    "next_on_success": "done",
                },
            ],
            max_planning_concurrent=5,
        )
        assert settings.max_planning_concurrent == 5

    def test_invalid_max_planning_concurrent_rejected(self, tmp_path: Path) -> None:
        """max_planning_concurrent < 1 被领域验证拒绝。"""
        yaml_content = """\
roles:
  dev:
    cli: claude

phases:
  - name: working
    role: dev
    type: structured_job
    next_on_success: done

max_planning_concurrent: 0
"""
        p = _write_yaml(tmp_path, yaml_content)
        with pytest.raises(ConfigError, match="max_planning_concurrent 必须 >= 1"):
            load_config(p)
