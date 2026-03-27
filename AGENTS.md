# Repository Guidelines

## Project Structure & Module Organization

Primary code lives in `src/ato/`. Key areas:
- `src/ato/core.py`, `transition_queue.py`, `recovery.py`: orchestration and lifecycle control.
- `src/ato/adapters/`: CLI adapters and BMAD parsing.
- `src/ato/models/`: Pydantic schemas and SQLite helpers.
- `src/ato/tui/` and `src/ato/tui/widgets/`: Textual dashboard and custom widgets.
- `schemas/`: JSON Schema files used by validation flows.
- `tests/unit`, `tests/integration`, `tests/smoke`, `tests/performance`: test layers.
- `_bmad-output/`: planning and implementation artifacts; useful context, not runtime code.

## Build, Test, and Development Commands

Use `uv` for local work:

```bash
uv sync --dev
uv run pytest
uv run pytest tests/unit/
uv run pytest tests/integration/
uv run ruff check src tests
uv run ruff format src tests
uv run mypy src
uv run pre-commit run --all-files
uv run ato --help
```

`uv run` is preferred over invoking tools directly so the project environment is consistent.

## Coding Style & Naming Conventions

Target Python is 3.11+. Use 4-space indentation, type hints on public APIs, and `snake_case` for functions/modules, `PascalCase` for classes, and `UPPER_SNAKE_CASE` for constants. Ruff enforces import sorting and a 100-character line limit; mypy runs in strict mode. Keep logic in non-TUI modules when possible; TUI code should stay in `src/ato/tui/`.

## Testing Guidelines

Pytest with `pytest-asyncio` is the test stack. Name files `test_*.py` and keep tests close to the layer they verify:
- fast logic in `tests/unit/`
- SQLite/TUI workflows in `tests/integration/`
- command smoke checks in `tests/smoke/`
- benchmark-style checks in `tests/performance/` with `@pytest.mark.perf`

Prefer narrow regression tests for bug fixes and run the smallest relevant subset before broader suites.

## Commit & Pull Request Guidelines

History follows Conventional Commit-style prefixes such as `feat:`, `feat(story-4.2):`, `chore:`, and `docs:`. Keep subjects imperative and specific. PRs should include:
- a short problem/solution summary
- linked story, epic, or issue when available
- test evidence (`pytest`, `ruff`, `mypy` commands)
- TUI screenshots or terminal captures for visible UI changes

## Configuration & Operational Notes

Start from `ato.yaml.example` for local configuration. Do not commit runtime state, secrets, or `.ato/` SQLite data. If you touch approval, recovery, or convergent-loop flows, verify both CLI and TUI behavior because they share the same state store.
