"""config — 声明式配置引擎。

通过 pydantic-settings 的 YAML source 加载 ato.yaml，
验证角色/阶段/转换/阈值配置，生成阶段定义供后续 Epic 消费。
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Literal

import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

from ato.models.schemas import ConfigError, LoopStage, StoryRecord

logger = structlog.get_logger()

__all__ = [
    "ATOSettings",
    "CLIDefaultsConfig",
    "ConvergentLoopConfig",
    "CostConfig",
    "DispatchProfile",
    "EffectiveTestPolicy",
    "PhaseConfig",
    "PhaseDefinition",
    "PhaseTestPolicyConfig",
    "ResolvedTestLayer",
    "RoleConfig",
    "TestLayerConfig",
    "TimeoutConfig",
    "build_phase_definitions",
    "evaluate_skip_condition",
    "load_config",
    "resolve_effective_test_policy",
    "resolve_loop_dispatch_profiles",
    "resolve_role_dispatch_config",
]

# ---------------------------------------------------------------------------
# 嵌套配置模型（BaseModel）
# ---------------------------------------------------------------------------


class CLIDefaultsConfig(BaseModel):
    """CLI 工具全局默认参数。角色级配置优先于此处。"""

    model_config = ConfigDict(extra="forbid")

    model: str | None = None
    sandbox: Literal["read-only", "workspace-write"] | None = None
    effort: Literal["low", "medium", "high", "max"] | None = None
    reasoning_effort: str | None = None
    reasoning_summary_format: str | None = None


class RoleConfig(BaseModel):
    """角色配置。"""

    model_config = ConfigDict(extra="forbid")

    cli: Literal["claude", "codex"]
    model: str | None = None
    sandbox: Literal["read-only", "workspace-write"] | None = None
    effort: Literal["low", "medium", "high", "max"] | None = None
    reasoning_effort: str | None = None
    reasoning_summary_format: str | None = None


class PhaseConfig(BaseModel):
    """阶段配置。"""

    model_config = ConfigDict(extra="forbid")

    name: str
    role: str
    type: Literal["structured_job", "convergent_loop", "interactive_session"]
    next_on_success: str
    next_on_failure: str | None = None
    workspace: Literal["main", "worktree"] | None = None
    skip_when: str | None = None
    parallel_safe: bool = False
    batchable: bool = False


class ConvergentLoopConfig(BaseModel):
    """Convergent Loop 参数。"""

    model_config = ConfigDict(extra="forbid")

    max_rounds: int = 3
    max_rounds_escalated: int = 3
    convergence_threshold: float = 0.5


class TimeoutConfig(BaseModel):
    """超时配置（秒）。"""

    model_config = ConfigDict(extra="forbid")

    structured_job: int = 3600
    interactive_session: int = 7200
    idle_timeout: int = 300
    post_result_timeout: int = 30
    semantic_parser: int = 120


class CostConfig(BaseModel):
    """成本控制配置。"""

    model_config = ConfigDict(extra="forbid")

    budget_per_story: float = 5.0
    blocking_threshold: int = 10


AllowedWhen = Literal["never", "after_required_commands", "after_required_failure", "always"]
CommandSource = Literal["project_defined", "llm_discovered", "llm_diagnostic"]
CommandTriggerReason = Literal[
    "required_layer",
    "optional_layer",
    "discovery_fallback",
    "diagnostic",
    "legacy_baseline",
]

TEST_POLICY_SUPPORTED_PHASES: frozenset[str] = frozenset({"qa_testing", "regression"})
RECOMMENDED_TEST_LAYERS: tuple[str, ...] = (
    "bootstrap",
    "lint",
    "typecheck",
    "unit",
    "integration",
    "system",
    "build",
    "smoke",
    "package",
)
TEST_COMMAND_SOURCES: tuple[CommandSource, ...] = (
    "project_defined",
    "llm_discovered",
    "llm_diagnostic",
)
TEST_COMMAND_TRIGGER_REASONS: tuple[CommandTriggerReason, ...] = (
    "required_layer",
    "optional_layer",
    "discovery_fallback",
    "diagnostic",
    "legacy_baseline",
)
DEFAULT_QA_FALLBACK_MAX_ADDITIONAL_COMMANDS = 4
DEFAULT_REGRESSION_MAX_ADDITIONAL_COMMANDS = 4
DEFAULT_REGRESSION_TEST_COMMAND = "uv run pytest"
LEGACY_REGRESSION_BASELINE_LAYER = "_legacy_baseline"
BOOTSTRAP_TEST_LAYER = "bootstrap"
_NODE_PACKAGE_MANAGER_TOKENS: frozenset[str] = frozenset({"pnpm", "npm", "yarn", "bun", "npx"})


class TestLayerConfig(BaseModel):
    """项目级测试 layer 到真实命令的映射。"""

    model_config = ConfigDict(extra="forbid")

    commands: list[str] = Field(default_factory=list)

    @field_validator("commands")
    @classmethod
    def _validate_commands(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("每个 test_catalog layer 至少需要一条命令")
        cleaned: list[str] = []
        for command in value:
            stripped = command.strip()
            if not stripped:
                raise ValueError("test_catalog layer 命令不能为空字符串")
            cleaned.append(stripped)
        return cleaned


class PhaseTestPolicyConfig(BaseModel):
    """phase 级测试策略声明。"""

    model_config = ConfigDict(extra="forbid")

    required_layers: list[str] = Field(default_factory=list)
    optional_layers: list[str] = Field(default_factory=list)
    allow_discovery: bool = False
    max_additional_commands: int = 0
    allowed_when: AllowedWhen = "never"

    @field_validator("required_layers", "optional_layers")
    @classmethod
    def _validate_layer_names(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for layer in value:
            stripped = layer.strip()
            if not stripped:
                raise ValueError("layer 名称不能为空字符串")
            cleaned.append(stripped)
        return cleaned

    @field_validator("max_additional_commands")
    @classmethod
    def _validate_max_additional_commands(cls, value: int) -> int:
        if value < 0:
            raise ValueError("max_additional_commands 必须 >= 0")
        return value


class EffectiveTestPolicy(BaseModel):
    """供 runtime surface 消费的解析后测试策略。"""

    model_config = ConfigDict(extra="forbid")

    phase: str
    policy_source: Literal[
        "explicit",
        "legacy_regression",
        "qa_bounded_fallback",
        "regression_discovery_fallback",
    ]
    required_layers: list[str] = Field(default_factory=list)
    optional_layers: list[str] = Field(default_factory=list)
    required_layer_commands: list[ResolvedTestLayer] = Field(default_factory=list)
    optional_layer_commands: list[ResolvedTestLayer] = Field(default_factory=list)
    missing_optional_layers: list[str] = Field(default_factory=list)
    allow_discovery: bool = False
    max_additional_commands: int = 0
    allowed_when: AllowedWhen = "never"
    required_commands: list[str] = Field(default_factory=list)
    optional_commands: list[str] = Field(default_factory=list)
    project_defined_commands: list[str] = Field(default_factory=list)
    discovery_priority: list[str] = Field(default_factory=list)
    legacy_baseline: bool = False


class ResolvedTestLayer(BaseModel):
    """展开后用于 prompt/runtime surface 的 layer 视图。"""

    model_config = ConfigDict(extra="forbid")

    layer: str
    commands: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 主配置类（BaseSettings）
# ---------------------------------------------------------------------------


class ATOSettings(BaseSettings):
    """ATO 主配置，通过 pydantic-settings YAML source 加载。"""

    model_config = SettingsConfigDict(
        yaml_file="ato.yaml",
        yaml_file_encoding="utf-8",
    )

    # 运行时路径注入（load_config 设置，settings_customise_sources 消费）
    _yaml_file_override: ClassVar[Path | None] = None

    cli_defaults: dict[str, CLIDefaultsConfig] = {}
    roles: dict[str, RoleConfig]
    phases: list[PhaseConfig]
    max_concurrent_agents: int = 4
    max_planning_concurrent: int = 3
    polling_interval: float = 3.0
    convergent_loop: ConvergentLoopConfig = ConvergentLoopConfig()
    timeout: TimeoutConfig = TimeoutConfig()
    cost: CostConfig | None = None
    model_map: dict[str, str] = {}
    test_catalog: dict[str, TestLayerConfig] = Field(default_factory=dict)
    phase_test_policy: dict[str, PhaseTestPolicyConfig] = Field(default_factory=dict)
    regression_test_command: str = "uv run pytest"
    regression_test_commands: list[str] | None = None
    merge_rebase_timeout: int = 120
    merge_conflict_resolution_max_attempts: int = 1

    def get_regression_commands(self) -> list[str]:
        """返回 regression 测试命令列表。

        优先使用 regression_test_commands（plural），否则回退为 [regression_test_command]。
        """
        if self.regression_test_commands:
            return list(self.regression_test_commands)
        return [self.regression_test_command]

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """以 init 参数 + YAML 文件为主配置来源；MVP 不依赖隐式 env/dotenv。"""
        yaml_file = (
            cls._yaml_file_override
            if cls._yaml_file_override is not None
            else settings_cls.model_config.get("yaml_file", "ato.yaml")
        )
        return (
            init_settings,
            YamlConfigSettingsSource(settings_cls, yaml_file=yaml_file, yaml_file_encoding="utf-8"),
        )


# ---------------------------------------------------------------------------
# 阶段定义 DTO（非 Pydantic model）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PhaseDefinition:
    """合并 PhaseConfig + RoleConfig 后的内部阶段定义。"""

    name: str
    role: str
    cli_tool: str
    model: str | None
    sandbox: str | None
    phase_type: str
    next_on_success: str
    next_on_failure: str | None
    timeout_seconds: int
    workspace: str = "main"
    skip_when: str | None = None
    effort: str | None = None
    reasoning_effort: str | None = None
    reasoning_summary_format: str | None = None
    parallel_safe: bool = False
    batchable: bool = False


# ---------------------------------------------------------------------------
# Dispatch Profile（角色级调度配置 DTO）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DispatchProfile:
    """角色级调度配置，合并 RoleConfig + CLIDefaultsConfig 后的运行时 DTO。"""

    role: str
    cli_tool: Literal["claude", "codex"]
    model: str | None = None
    sandbox: Literal["read-only", "workspace-write"] | None = None
    effort: str | None = None
    reasoning_effort: str | None = None
    reasoning_summary_format: str | None = None


def resolve_role_dispatch_config(
    settings: ATOSettings,
    role_name: str,
) -> DispatchProfile:
    """从 ATOSettings 解析指定角色的完整 dispatch profile。

    合并优先级：角色级 > cli_defaults 级。

    Args:
        settings: 已验证的 ATOSettings。
        role_name: 角色名（必须在 settings.roles 中定义）。

    Returns:
        合并后的 DispatchProfile。

    Raises:
        ConfigError: role_name 未在 settings.roles 中定义。
    """
    role_cfg = settings.roles.get(role_name)
    if role_cfg is None:
        raise ConfigError(f"配置错误：角色 '{role_name}' 未定义")
    cli_default = settings.cli_defaults.get(role_cfg.cli, CLIDefaultsConfig())
    return DispatchProfile(
        role=role_name,
        cli_tool=role_cfg.cli,
        model=role_cfg.model or cli_default.model,
        sandbox=role_cfg.sandbox or cli_default.sandbox,
        effort=role_cfg.effort or cli_default.effort,
        reasoning_effort=role_cfg.reasoning_effort or cli_default.reasoning_effort,
        reasoning_summary_format=(
            role_cfg.reasoning_summary_format or cli_default.reasoning_summary_format
        ),
    )


def resolve_loop_dispatch_profiles(
    settings: ATOSettings,
    stage: LoopStage = "standard",
) -> tuple[DispatchProfile, DispatchProfile]:
    """返回 (review_profile, fix_profile) 对，按 stage 选择角色。

    standard: reviewer + developer（默认 Phase 1）
    escalated: reviewer_escalated + fixer_escalation（Phase 2 角色互换）

    Args:
        settings: 已验证的 ATOSettings。
        stage: 当前降级阶段。

    Returns:
        (review_profile, fix_profile) 元组。
    """
    if stage == "escalated":
        return (
            resolve_role_dispatch_config(settings, "reviewer_escalated"),
            resolve_role_dispatch_config(settings, "fixer_escalation"),
        )
    return (
        resolve_role_dispatch_config(settings, "reviewer"),
        resolve_role_dispatch_config(settings, "developer"),
    )


# ---------------------------------------------------------------------------
# 配置加载
# ---------------------------------------------------------------------------


def load_config(config_path: Path) -> ATOSettings:
    """加载并验证配置文件。

    Args:
        config_path: ato.yaml 文件路径。

    Returns:
        经验证的 ATOSettings 配置对象。

    Raises:
        ConfigError: 文件不存在、YAML 语法错误、验证失败或领域约束违反。
    """
    if not config_path.exists():
        raise ConfigError(
            f"配置文件 {config_path} 不存在。"
            "请从 ato.yaml.example 复制：\n  cp ato.yaml.example ato.yaml"
        )

    ATOSettings._yaml_file_override = config_path
    try:
        settings = ATOSettings()  # type: ignore[call-arg]  # fields from YAML source
    except ValidationError as e:
        raise ConfigError(f"配置验证失败：\n{e}") from e
    except Exception as e:
        raise ConfigError(f"配置加载失败：{e}") from e
    finally:
        ATOSettings._yaml_file_override = None

    _validate_config(settings)
    return settings


# ---------------------------------------------------------------------------
# 领域级配置验证
# ---------------------------------------------------------------------------


def _validate_config(config: ATOSettings) -> None:
    """领域级配置验证。验证失败时抛出 ConfigError。"""
    # phases 非空
    if not config.phases:
        raise ConfigError("配置错误：phases 不能为空")

    # phase 名唯一且不得使用保留终态名
    reserved_names = {"done"}
    phase_names: set[str] = set()
    for phase in config.phases:
        if phase.name in reserved_names:
            raise ConfigError(
                f"配置错误：阶段名 '{phase.name}' 是保留的终态标识符，不能用作普通阶段名"
            )
        if phase.name in phase_names:
            raise ConfigError(f"配置错误：阶段名 '{phase.name}' 重复定义")
        phase_names.add(phase.name)

    # parallel_safe 仅对 workspace: main 有效
    for phase in config.phases:
        resolved_ws = _resolve_workspace(phase)
        if phase.parallel_safe and resolved_ws != "main":
            logger.warning(
                "config_parallel_safe_ignored",
                phase=phase.name,
                workspace=resolved_ws,
                hint="parallel_safe: true 仅对 workspace: main 的阶段有效，此设置将被忽略",
            )

    # 验证角色引用
    for phase in config.phases:
        if phase.role not in config.roles:
            raise ConfigError(f"配置错误：阶段 '{phase.name}' 引用了未定义的角色 '{phase.role}'")

    # 验证阶段转换引用
    valid_targets = phase_names | {"done"}
    for phase in config.phases:
        if phase.next_on_success not in valid_targets:
            raise ConfigError(
                f"配置错误：阶段 '{phase.name}' 的 next_on_success "
                f"引用了未定义的目标 '{phase.next_on_success}'"
            )
        if phase.next_on_failure is not None and phase.next_on_failure not in valid_targets:
            raise ConfigError(
                f"配置错误：阶段 '{phase.name}' 的 next_on_failure "
                f"引用了未定义的目标 '{phase.next_on_failure}'"
            )

    # 验证可达 done（图遍历）
    _validate_reachability(config.phases, phase_names)
    _validate_test_policy_config(config, phase_names)

    # 数值边界
    _validate_numeric_bounds(config)

    # model_map key 引用
    for key in config.model_map:
        if key not in phase_names:
            raise ConfigError(f"配置错误：model_map 键 '{key}' 引用了未定义的阶段")


def _validate_reachability(phases: list[PhaseConfig], phase_names: set[str]) -> None:
    """验证从每个阶段都存在通向 'done' 的路径。

    允许受控回环（如 review → fix → review），但禁止"只有环、没有出口"的配置。
    使用反向固定点迭代：从 "done" 出发，逐步标记所有可达 done 的节点。
    """
    # 构建邻接表
    adjacency: dict[str, set[str]] = {}
    for p in phases:
        targets: set[str] = {p.next_on_success}
        if p.next_on_failure is not None:
            targets.add(p.next_on_failure)
        adjacency[p.name] = targets

    # 固定点迭代：从 {done} 反向传播可达性
    can_reach_done: set[str] = {"done"}
    changed = True
    while changed:
        changed = False
        for name in phase_names:
            if name in can_reach_done:
                continue
            if any(n in can_reach_done for n in adjacency.get(name, set())):
                can_reach_done.add(name)
                changed = True

    for name in phase_names:
        if name not in can_reach_done:
            raise ConfigError(
                f"配置错误：阶段 '{name}' 不存在通向 'done' 的路径（只有回环，没有出口）"
            )


def _validate_numeric_bounds(config: ATOSettings) -> None:
    """验证数值边界。"""
    if config.convergent_loop.max_rounds < 1:
        raise ConfigError("配置错误：convergent_loop.max_rounds 必须 >= 1")
    if config.convergent_loop.max_rounds_escalated < 1:
        raise ConfigError("配置错误：convergent_loop.max_rounds_escalated 必须 >= 1")
    if not (0 <= config.convergent_loop.convergence_threshold <= 1):
        raise ConfigError("配置错误：convergent_loop.convergence_threshold 必须在 [0, 1] 范围内")
    if config.max_concurrent_agents < 1:
        raise ConfigError("配置错误：max_concurrent_agents 必须 >= 1")
    if config.max_planning_concurrent < 1:
        raise ConfigError("配置错误：max_planning_concurrent 必须 >= 1")
    if config.max_planning_concurrent > config.max_concurrent_agents:
        logger.warning(
            "config_planning_exceeds_agents",
            max_planning_concurrent=config.max_planning_concurrent,
            max_concurrent_agents=config.max_concurrent_agents,
            hint="max_planning_concurrent 大于 max_concurrent_agents，"
            "并发 planning dispatch 可能导致系统总资源消耗超出预期"
            "（SubprocessManager 是实例级限流，不构成跨 dispatch 全局上限）",
        )
    if config.timeout.structured_job <= 0:
        raise ConfigError("配置错误：timeout.structured_job 必须 > 0")
    if config.timeout.interactive_session <= 0:
        raise ConfigError("配置错误：timeout.interactive_session 必须 > 0")
    if config.cost is not None:
        if config.cost.budget_per_story <= 0:
            raise ConfigError("配置错误：cost.budget_per_story 必须 > 0")
        if config.cost.blocking_threshold < 0:
            raise ConfigError("配置错误：cost.blocking_threshold 必须 >= 0")
    if config.timeout.idle_timeout <= 0:
        raise ConfigError("配置错误：timeout.idle_timeout 必须 > 0")
    if config.timeout.post_result_timeout <= 0:
        raise ConfigError("配置错误：timeout.post_result_timeout 必须 > 0")
    if config.polling_interval <= 0:
        raise ConfigError("配置错误：polling_interval 必须 > 0")


def _validate_test_policy_config(config: ATOSettings, phase_names: set[str]) -> None:
    """验证跨项目测试策略配置。"""
    phase_by_name = {phase.name: phase for phase in config.phases}

    for layer_name in config.test_catalog:
        if not layer_name.strip():
            raise ConfigError("配置错误：test_catalog 不能包含空 layer 名")

    for phase_name, policy in config.phase_test_policy.items():
        if phase_name not in phase_names:
            raise ConfigError(f"配置错误：phase_test_policy 键 '{phase_name}' 引用了未定义的阶段")
        if phase_name not in TEST_POLICY_SUPPORTED_PHASES:
            raise ConfigError(
                f"配置错误：phase_test_policy 当前仅支持 {sorted(TEST_POLICY_SUPPORTED_PHASES)}，"
                f"不支持阶段 '{phase_name}'"
            )
        missing_required = [
            layer for layer in policy.required_layers if layer not in config.test_catalog
        ]
        if missing_required:
            missing_str = ", ".join(missing_required)
            raise ConfigError(
                f"配置错误：阶段 '{phase_name}' 的 required_layers 引用了未声明的 layer: "
                f"{missing_str}"
            )

        declared_optional = [
            layer for layer in policy.optional_layers if layer in config.test_catalog
        ]
        has_required_path = bool(policy.required_layers)
        has_additional_path = (
            policy.max_additional_commands > 0
            and policy.allowed_when in {"always", "after_required_commands"}
            and (bool(declared_optional) or policy.allow_discovery)
        )
        if not has_required_path and not has_additional_path:
            raise ConfigError(
                f"配置错误：阶段 '{phase_name}' 的 test policy 不会执行任何命令；"
                "请至少配置一个 required layer，或提供可达的 additional command 路径"
            )

        if (
            phase_name == "regression"
            and (phase_cfg := phase_by_name.get(phase_name)) is not None
            and _resolve_workspace(phase_cfg) == "main"
            and _should_warn_missing_regression_bootstrap(config, policy)
        ):
            logger.warning(
                "config_regression_bootstrap_missing",
                phase=phase_name,
                required_layers=policy.required_layers,
                optional_layers=policy.optional_layers,
                hint=(
                    "workspace: main 的 regression 使用 Node package-manager 命令，但未声明 "
                    "bootstrap layer；worktree 中的 install 不会随 merge 带到 main。"
                    "建议在 regression.required_layers 最前面添加 bootstrap "
                    "（如 `pnpm install --frozen-lockfile`）。"
                ),
            )


def _command_uses_node_package_manager(command: str) -> bool:
    """Return True when a command is driven by a Node package manager executable."""
    executable_tokens = _tokenize_command_for_bootstrap_detection(command)
    if not executable_tokens:
        return False

    first = executable_tokens[0]
    if first in _NODE_PACKAGE_MANAGER_TOKENS:
        return True

    return (
        first == "corepack"
        and len(executable_tokens) > 1
        and executable_tokens[1] in _NODE_PACKAGE_MANAGER_TOKENS
    )


def _command_is_node_bootstrap(command: str) -> bool:
    """Return True when a command performs deterministic Node dependency bootstrap."""
    executable_tokens = _tokenize_command_for_bootstrap_detection(command)
    if not executable_tokens:
        return False

    first = executable_tokens[0]
    if first in _NODE_PACKAGE_MANAGER_TOKENS:
        return len(executable_tokens) > 1 and executable_tokens[1] in {"install", "ci"}

    return (
        first == "corepack"
        and len(executable_tokens) > 2
        and executable_tokens[1] in _NODE_PACKAGE_MANAGER_TOKENS
        and executable_tokens[2] in {"install", "ci"}
    )


def _tokenize_command_for_bootstrap_detection(command: str) -> list[str]:
    """Return command tokens with leading env assignments stripped."""
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        tokens = command.strip().split()

    executable_tokens: list[str] = []
    for token in tokens:
        if not token:
            continue
        if "=" in token and not token.startswith("/") and token.index("=") > 0:
            continue
        executable_tokens.append(token)
    return executable_tokens


def _should_warn_missing_regression_bootstrap(
    config: ATOSettings,
    policy: PhaseTestPolicyConfig,
) -> bool:
    """Detect regression policies that likely need a bootstrap layer but omit it."""
    if BOOTSTRAP_TEST_LAYER in policy.required_layers:
        return False

    required_commands: list[str] = []
    for layer in policy.required_layers:
        layer_cfg = config.test_catalog.get(layer)
        if layer_cfg is None:
            continue
        required_commands.extend(layer_cfg.commands)

    if any(_command_is_node_bootstrap(command) for command in required_commands):
        return False

    configured_commands = list(required_commands)
    for layer in policy.optional_layers:
        layer_cfg = config.test_catalog.get(layer)
        if layer_cfg is None:
            continue
        configured_commands.extend(layer_cfg.commands)

    return any(_command_uses_node_package_manager(command) for command in configured_commands)


def _expand_policy_layers(
    settings: ATOSettings,
    layers: list[str],
    *,
    required: bool,
    phase: str,
) -> tuple[list[str], list[str], list[str], list[ResolvedTestLayer]]:
    """按 layer 声明顺序展开为命令列表。"""
    resolved_layers: list[str] = []
    commands: list[str] = []
    missing_layers: list[str] = []
    resolved_layer_commands: list[ResolvedTestLayer] = []

    for layer in layers:
        layer_cfg = settings.test_catalog.get(layer)
        if layer_cfg is None:
            if required:
                raise ConfigError(
                    f"配置错误：阶段 '{phase}' 的 required_layers 引用了未声明的 layer '{layer}'"
                )
            missing_layers.append(layer)
            continue
        resolved_layers.append(layer)
        commands.extend(layer_cfg.commands)
        resolved_layer_commands.append(
            ResolvedTestLayer(layer=layer, commands=list(layer_cfg.commands))
        )

    return resolved_layers, commands, missing_layers, resolved_layer_commands


def _default_discovery_priority(phase: str) -> list[str]:
    if phase == "qa_testing":
        return ["repo_native_wrappers", "standard_test_entrypoints"]
    return ["standard_test_entrypoints"]


def _resolve_explicit_test_policy(
    settings: ATOSettings,
    phase: str,
    policy: PhaseTestPolicyConfig,
) -> EffectiveTestPolicy:
    required_layers, required_commands, _, required_layer_commands = _expand_policy_layers(
        settings,
        policy.required_layers,
        required=True,
        phase=phase,
    )
    (
        optional_layers,
        optional_commands,
        missing_optional_layers,
        optional_layer_commands,
    ) = _expand_policy_layers(
        settings,
        policy.optional_layers,
        required=False,
        phase=phase,
    )
    return EffectiveTestPolicy(
        phase=phase,
        policy_source="explicit",
        required_layers=required_layers,
        optional_layers=optional_layers,
        required_layer_commands=required_layer_commands,
        optional_layer_commands=optional_layer_commands,
        missing_optional_layers=missing_optional_layers,
        allow_discovery=policy.allow_discovery,
        max_additional_commands=policy.max_additional_commands,
        allowed_when=policy.allowed_when,
        required_commands=required_commands,
        optional_commands=optional_commands,
        project_defined_commands=[*required_commands, *optional_commands],
        discovery_priority=_default_discovery_priority(phase),
        legacy_baseline=False,
    )


def _resolve_legacy_regression_policy(settings: ATOSettings) -> EffectiveTestPolicy:
    has_explicit_plural = settings.regression_test_commands is not None
    has_explicit_singular = settings.regression_test_command != DEFAULT_REGRESSION_TEST_COMMAND

    if has_explicit_plural or has_explicit_singular:
        commands = settings.get_regression_commands()
        return EffectiveTestPolicy(
            phase="regression",
            policy_source="legacy_regression",
            required_layers=[LEGACY_REGRESSION_BASELINE_LAYER],
            optional_layers=[],
            required_layer_commands=[
                ResolvedTestLayer(
                    layer=LEGACY_REGRESSION_BASELINE_LAYER,
                    commands=list(commands),
                )
            ],
            optional_layer_commands=[],
            allow_discovery=True,
            max_additional_commands=DEFAULT_REGRESSION_MAX_ADDITIONAL_COMMANDS,
            allowed_when="after_required_commands",
            required_commands=commands,
            optional_commands=[],
            project_defined_commands=list(commands),
            discovery_priority=_default_discovery_priority("regression"),
            legacy_baseline=True,
        )

    return EffectiveTestPolicy(
        phase="regression",
        policy_source="regression_discovery_fallback",
        required_layers=[],
        optional_layers=[],
        required_layer_commands=[],
        optional_layer_commands=[],
        allow_discovery=True,
        max_additional_commands=DEFAULT_REGRESSION_MAX_ADDITIONAL_COMMANDS,
        allowed_when="always",
        required_commands=[],
        optional_commands=[],
        project_defined_commands=[],
        discovery_priority=_default_discovery_priority("regression"),
        legacy_baseline=False,
    )


def resolve_effective_test_policy(settings: ATOSettings, phase: str) -> EffectiveTestPolicy | None:
    """根据 phase 返回解析后的 effective test policy。"""
    phase_test_policy = (
        settings.phase_test_policy if isinstance(settings.phase_test_policy, dict) else {}
    )
    explicit_policy = phase_test_policy.get(phase)
    if isinstance(explicit_policy, dict):
        explicit_policy = PhaseTestPolicyConfig.model_validate(explicit_policy)
    if explicit_policy is not None:
        return _resolve_explicit_test_policy(settings, phase, explicit_policy)

    if phase == "qa_testing":
        return EffectiveTestPolicy(
            phase="qa_testing",
            policy_source="qa_bounded_fallback",
            required_layers=[],
            optional_layers=[],
            required_layer_commands=[],
            optional_layer_commands=[],
            allow_discovery=True,
            max_additional_commands=DEFAULT_QA_FALLBACK_MAX_ADDITIONAL_COMMANDS,
            allowed_when="always",
            required_commands=[],
            optional_commands=[],
            project_defined_commands=[],
            discovery_priority=_default_discovery_priority("qa_testing"),
            legacy_baseline=False,
        )

    if phase == "regression":
        return _resolve_legacy_regression_policy(settings)

    return None


# ---------------------------------------------------------------------------
# 阶段定义生成
# ---------------------------------------------------------------------------


# 已知的 main-workspace phase 名（省略 workspace 时用于向后兼容推断）。
# 这些 phase 在 workspace 字段出现前就在 project_root 上执行：
# - creating/designing: pre-worktree structured_job
# - validating: convergent_loop 但 gate 在 main 上运行
# - dev_ready: worktree 创建前的最后一个 main phase
# - merging/regression: merge_queue 控制流，在 main 上执行
_KNOWN_MAIN_PHASES: frozenset[str] = frozenset(
    {"creating", "designing", "validating", "dev_ready", "merging", "regression"}
)


def _resolve_workspace(phase: PhaseConfig) -> str:
    """解析 phase 的 workspace 值。

    显式配置优先；省略时按 phase 名推断（向后兼容旧 YAML）。
    未知 phase 名默认 ``"worktree"``（安全侧：需要隔离的假设）。
    """
    if phase.workspace is not None:
        return phase.workspace
    return "main" if phase.name in _KNOWN_MAIN_PHASES else "worktree"


def build_phase_definitions(config: ATOSettings) -> list[PhaseDefinition]:
    """将 PhaseConfig + RoleConfig 合并为 PhaseDefinition 列表。

    保持阶段顺序与 config.phases 一致，不实例化状态机类。
    """
    definitions: list[PhaseDefinition] = []

    for phase in config.phases:
        role_config = config.roles[phase.role]
        cli_default = config.cli_defaults.get(role_config.cli, CLIDefaultsConfig())

        # model 解析：model_map[phase.name] > 角色级 > cli_defaults 级
        model = config.model_map.get(phase.name) or role_config.model or cli_default.model
        sandbox = role_config.sandbox or cli_default.sandbox
        effort = role_config.effort or cli_default.effort
        reasoning_effort = role_config.reasoning_effort or cli_default.reasoning_effort
        reasoning_summary_format = (
            role_config.reasoning_summary_format or cli_default.reasoning_summary_format
        )

        # timeout 由 phase.type 决定；convergent_loop 视为非交互阶段
        if phase.type == "interactive_session":
            timeout_seconds = config.timeout.interactive_session
        else:
            timeout_seconds = config.timeout.structured_job

        definitions.append(
            PhaseDefinition(
                name=phase.name,
                role=phase.role,
                cli_tool=role_config.cli,
                model=model,
                sandbox=sandbox,
                phase_type=phase.type,
                next_on_success=phase.next_on_success,
                next_on_failure=phase.next_on_failure,
                timeout_seconds=timeout_seconds,
                workspace=_resolve_workspace(phase),
                skip_when=phase.skip_when,
                effort=effort,
                reasoning_effort=reasoning_effort,
                reasoning_summary_format=reasoning_summary_format,
                parallel_safe=phase.parallel_safe,
                batchable=phase.batchable,
            )
        )

    return definitions


# ---------------------------------------------------------------------------
# 条件跳过表达式求值（Story 9.3）
# ---------------------------------------------------------------------------

# 白名单属性：仅允许访问这些 StoryRecord 字段
_SKIP_ALLOWED_ATTRS: frozenset[str] = frozenset({"has_ui", "story_id", "title"})

# 支持的 token 类型
_SKIP_KEYWORDS: frozenset[str] = frozenset({"not", "and", "or"})


def evaluate_skip_condition(expression: str, story: StoryRecord) -> bool:
    """安全求值 skip_when 表达式。

    仅允许 ``story.<attr>`` 形式访问白名单属性，
    支持 ``not`` / ``and`` / ``or`` 布尔运算。
    不使用 Python ``eval()``。

    非法或无法解析的表达式返回 False（不跳过）并记录 warning。

    Args:
        expression: skip_when 字符串（如 ``"not story.has_ui"``）。
        story: 当前 StoryRecord。

    Returns:
        True 表示应跳过当前阶段，False 表示不跳过。
    """
    try:
        tokens = _tokenize_skip_expr(expression)
        result = _parse_or_expr(tokens, story)
        if tokens:
            # 未消费完的 token → 语法错误
            logger.warning(
                "skip_when_trailing_tokens",
                expression=expression,
                remaining=tokens,
            )
            return False
        return result
    except _SkipExprError as exc:
        logger.warning(
            "skip_when_invalid_expression",
            expression=expression,
            error=str(exc),
        )
        return False


class _SkipExprError(Exception):
    """skip_when 表达式解析错误（内部使用）。"""


def _tokenize_skip_expr(expression: str) -> list[str]:
    """将表达式拆分为 token 列表。

    支持 ``story.attr``、``not``、``and``、``or``、``(``、``)``。
    """
    import re

    pattern = re.compile(r"story\.\w+|not|and|or|[()]|\S+")
    return pattern.findall(expression.strip())


def _resolve_attr(token: str, story: StoryRecord) -> object:
    """解析 ``story.<attr>`` token 到实际值。"""
    if not token.startswith("story."):
        raise _SkipExprError(f"Invalid token: {token!r} (expected 'story.<attr>')")
    attr = token[len("story.") :]
    if attr not in _SKIP_ALLOWED_ATTRS:
        raise _SkipExprError(
            f"Attribute 'story.{attr}' not allowed (whitelist: {sorted(_SKIP_ALLOWED_ATTRS)})"
        )
    return getattr(story, attr)


def _parse_or_expr(tokens: list[str], story: StoryRecord) -> bool:
    """解析 or 表达式（最低优先级）。"""
    left = _parse_and_expr(tokens, story)
    while tokens and tokens[0] == "or":
        tokens.pop(0)
        right = _parse_and_expr(tokens, story)
        left = left or right
    return left


def _parse_and_expr(tokens: list[str], story: StoryRecord) -> bool:
    """解析 and 表达式。"""
    left = _parse_not_expr(tokens, story)
    while tokens and tokens[0] == "and":
        tokens.pop(0)
        right = _parse_not_expr(tokens, story)
        left = left and right
    return left


def _parse_not_expr(tokens: list[str], story: StoryRecord) -> bool:
    """解析 not 表达式。"""
    if tokens and tokens[0] == "not":
        tokens.pop(0)
        return not _parse_not_expr(tokens, story)
    return _parse_primary(tokens, story)


def _parse_primary(tokens: list[str], story: StoryRecord) -> bool:
    """解析原子表达式：``story.<attr>`` 或 ``(expr)``。"""
    if not tokens:
        raise _SkipExprError("Unexpected end of expression")

    token = tokens[0]

    if token == "(":
        tokens.pop(0)
        result = _parse_or_expr(tokens, story)
        if not tokens or tokens[0] != ")":
            raise _SkipExprError("Missing closing parenthesis")
        tokens.pop(0)
        return bool(result)

    if token.startswith("story."):
        tokens.pop(0)
        return bool(_resolve_attr(token, story))

    raise _SkipExprError(f"Unexpected token: {token!r}")
