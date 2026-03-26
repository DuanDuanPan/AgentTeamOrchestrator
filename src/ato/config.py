"""config — 声明式配置引擎。

通过 pydantic-settings 的 YAML source 加载 ato.yaml，
验证角色/阶段/转换/阈值配置，生成阶段定义供后续 Epic 消费。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Literal

import structlog
from pydantic import BaseModel, ConfigDict, ValidationError
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

from ato.models.schemas import ConfigError

logger = structlog.get_logger()

__all__ = [
    "ATOSettings",
    "ConvergentLoopConfig",
    "CostConfig",
    "PhaseConfig",
    "PhaseDefinition",
    "RoleConfig",
    "TimeoutConfig",
    "build_phase_definitions",
    "load_config",
]

# ---------------------------------------------------------------------------
# 嵌套配置模型（BaseModel）
# ---------------------------------------------------------------------------


class RoleConfig(BaseModel):
    """角色配置。"""

    model_config = ConfigDict(extra="forbid")

    cli: Literal["claude", "codex"]
    model: str
    sandbox: Literal["read-only", "workspace-write"] | None = None


class PhaseConfig(BaseModel):
    """阶段配置。"""

    model_config = ConfigDict(extra="forbid")

    name: str
    role: str
    type: Literal["structured_job", "convergent_loop", "interactive_session"]
    next_on_success: str
    next_on_failure: str | None = None


class ConvergentLoopConfig(BaseModel):
    """Convergent Loop 参数。"""

    model_config = ConfigDict(extra="forbid")

    max_rounds: int = 3
    convergence_threshold: float = 0.5


class TimeoutConfig(BaseModel):
    """超时配置（秒）。"""

    model_config = ConfigDict(extra="forbid")

    structured_job: int = 1800
    interactive_session: int = 7200


class CostConfig(BaseModel):
    """成本控制配置。"""

    model_config = ConfigDict(extra="forbid")

    budget_per_story: float = 5.0
    blocking_threshold: int = 10


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

    roles: dict[str, RoleConfig]
    phases: list[PhaseConfig]
    max_concurrent_agents: int = 4
    polling_interval: float = 3.0
    convergent_loop: ConvergentLoopConfig = ConvergentLoopConfig()
    timeout: TimeoutConfig = TimeoutConfig()
    cost: CostConfig = CostConfig()
    model_map: dict[str, str] = {}
    regression_test_command: str = "uv run pytest"
    merge_rebase_timeout: int = 120
    merge_conflict_resolution_max_attempts: int = 1

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
    model: str
    sandbox: str | None
    phase_type: str
    next_on_success: str
    next_on_failure: str | None
    timeout_seconds: int


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
    if not (0 <= config.convergent_loop.convergence_threshold <= 1):
        raise ConfigError("配置错误：convergent_loop.convergence_threshold 必须在 [0, 1] 范围内")
    if config.max_concurrent_agents < 1:
        raise ConfigError("配置错误：max_concurrent_agents 必须 >= 1")
    if config.timeout.structured_job <= 0:
        raise ConfigError("配置错误：timeout.structured_job 必须 > 0")
    if config.timeout.interactive_session <= 0:
        raise ConfigError("配置错误：timeout.interactive_session 必须 > 0")
    if config.cost.budget_per_story <= 0:
        raise ConfigError("配置错误：cost.budget_per_story 必须 > 0")
    if config.cost.blocking_threshold < 0:
        raise ConfigError("配置错误：cost.blocking_threshold 必须 >= 0")
    if config.polling_interval <= 0:
        raise ConfigError("配置错误：polling_interval 必须 > 0")


# ---------------------------------------------------------------------------
# 阶段定义生成
# ---------------------------------------------------------------------------


def build_phase_definitions(config: ATOSettings) -> list[PhaseDefinition]:
    """将 PhaseConfig + RoleConfig 合并为 PhaseDefinition 列表。

    保持阶段顺序与 config.phases 一致，不实例化状态机类。
    """
    definitions: list[PhaseDefinition] = []

    for phase in config.phases:
        role_config = config.roles[phase.role]

        # model 解析：model_map[phase.name] 优先，否则回退到角色默认
        model = config.model_map.get(phase.name, role_config.model)

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
                sandbox=role_config.sandbox,
                phase_type=phase.type,
                next_on_success=phase.next_on_success,
                next_on_failure=phase.next_on_failure,
                timeout_seconds=timeout_seconds,
            )
        )

    return definitions
