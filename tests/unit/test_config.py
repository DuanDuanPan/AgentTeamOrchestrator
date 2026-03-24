"""test_config — 声明式配置引擎单元测试。"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from ato.config import (
    ATOSettings,
    ConvergentLoopConfig,
    CostConfig,
    PhaseDefinition,
    TimeoutConfig,
    build_phase_definitions,
    load_config,
)
from ato.models.schemas import ConfigError

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
        assert isinstance(config.cost, CostConfig)
        assert isinstance(config.model_map, dict)
        assert config.max_concurrent_agents == 4

    def test_default_values_applied(self, tmp_path: Path) -> None:
        """未指定的可选字段使用默认值。"""
        p = _write_yaml(tmp_path, _MINIMAL_VALID_YAML)
        config = load_config(p)
        assert config.convergent_loop.max_rounds == 3
        assert config.convergent_loop.convergence_threshold == 0.5
        assert config.timeout.structured_job == 1800
        assert config.timeout.interactive_session == 7200
        assert config.cost.budget_per_story == 5.0
        assert config.cost.blocking_threshold == 10
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
        assert config.cost.blocking_threshold == 0


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
        assert d.timeout_seconds == 1800

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

    def test_example_has_readonly_sandbox(self) -> None:
        """模板中有角色使用 read-only sandbox。"""
        config = load_config(Path("ato.yaml.example"))
        has_readonly = any(r.sandbox == "read-only" for r in config.roles.values())
        assert has_readonly

    def test_example_contains_comments(self) -> None:
        """模板文件包含说明注释。"""
        content = Path("ato.yaml.example").read_text(encoding="utf-8")
        assert "# " in content
        assert "ato.yaml.example" in content or "FR51" in content


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
