"""test_config_workflow — Story 1.3 配置引擎集成测试。

覆盖从真实 YAML 文件加载配置到生成 PhaseDefinition 的完整工作流，
并验证关键错误场景会在入口处被拒绝。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ato.config import build_phase_definitions, load_config
from ato.models.schemas import ConfigError


def _write_config(tmp_path: Path, content: str, filename: str = "ato.yaml") -> Path:
    """将 YAML 内容写入临时目录并返回配置路径。"""
    config_path = tmp_path / filename
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(content, encoding="utf-8")
    return config_path


class TestConfigWorkflow:
    def test_example_template_to_phase_definitions_end_to_end(self, tmp_path: Path) -> None:
        """从模板复制的 ato.yaml 可以完整生成阶段定义列表。"""
        example_path = Path("ato.yaml.example")
        config_path = _write_config(tmp_path, example_path.read_text(encoding="utf-8"))

        settings = load_config(config_path)
        definitions = build_phase_definitions(settings)

        assert [definition.name for definition in definitions] == [
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
        assert definitions[4].cli_tool == "codex"
        assert definitions[4].sandbox is None  # 模板不再默认指定 sandbox
        assert definitions[4].next_on_failure == "fixing"
        assert definitions[7].phase_type == "interactive_session"
        assert definitions[7].timeout_seconds == settings.timeout.interactive_session

    def test_nested_custom_config_end_to_end_applies_model_overrides(self, tmp_path: Path) -> None:
        """自定义路径中的配置可加载，并正确应用 model_map 与 timeout 映射。"""
        config_path = _write_config(
            tmp_path,
            """\
roles:
  creator:
    cli: claude
    model: sonnet
  reviewer:
    cli: codex
    model: codex-mini-latest
    sandbox: read-only

phases:
  - name: creating
    role: creator
    type: structured_job
    next_on_success: reviewing
  - name: reviewing
    role: reviewer
    type: convergent_loop
    next_on_success: uat
    next_on_failure: creating
  - name: uat
    role: creator
    type: interactive_session
    next_on_success: done

timeout:
  structured_job: 120
  interactive_session: 600

model_map:
  reviewing: gpt-5-review
""",
            filename="configs/story-1.3/custom.yaml",
        )

        settings = load_config(config_path)
        definitions = build_phase_definitions(settings)

        assert [definition.name for definition in definitions] == ["creating", "reviewing", "uat"]
        assert definitions[1].model == "gpt-5-review"
        assert definitions[1].timeout_seconds == 120
        assert definitions[2].timeout_seconds == 600

    def test_typo_field_rejected_before_workflow_continues(self, tmp_path: Path) -> None:
        """拼写错误字段不会被静默吞掉，配置工作流在入口处失败。"""
        config_path = _write_config(
            tmp_path,
            """\
roles:
  reviewer:
    cli: codex
    model: codex-mini-latest
    sanbox: read-only

phases:
  - name: reviewing
    role: reviewer
    type: structured_job
    next_on_success: done
""",
        )

        with pytest.raises(ConfigError, match="配置验证失败"):
            load_config(config_path)

    def test_reserved_done_phase_name_rejected(self, tmp_path: Path) -> None:
        """保留终态名 done 不能被声明为普通阶段。"""
        config_path = _write_config(
            tmp_path,
            """\
roles:
  dev:
    cli: claude
    model: sonnet

phases:
  - name: done
    role: dev
    type: structured_job
    next_on_success: done
""",
        )

        with pytest.raises(ConfigError, match=r"done.*保留.*终态"):
            load_config(config_path)
