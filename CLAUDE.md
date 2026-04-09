# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Agent Team Orchestrator (ATO) is a locally-operated, human-in-the-loop multi-role AI software team orchestration system. A single technical lead steers decisions while AI agents execute role-specific work (PM, Architect, UX, Dev, Reviewer, QA, etc.) through BMAD methodology SOPs.

**Core principle:** Human decides, system orchestrates, agents execute. Agents collaborate through structured artifacts, not shared conversational context.

## Current Status

Pre-implementation. All planning artifacts are complete in `_bmad-output/planning-artifacts/` (PRD, architecture, epics, UX design, technical research, sprint status). No source code exists yet. Implementation starts with Epic 1 (project scaffolding).

## Architecture (Five Layers)

1. **Human Control** — approvals, UAT, judgment calls via TUI/CLI
2. **Orchestrator Control Plane** — single-process asyncio event loop + embedded SQLite (WAL mode) state store
3. **Stateless Role Workers** — per-task CLI invocations: `claude -p` (OAuth, BMAD skills auto-loaded) and `codex exec` (subprocess)
4. **Git/Worktree Execution Plane** — story-level parallelization via git worktrees
5. **Artifacts & Audit Trail** — git-managed files + SQLite event log

## Critical Constraints

- **No ANTHROPIC_API_KEY** — must use `claude -p` with OAuth (not `--bare`), cannot use Agent SDK
- **BMAD skills are immutable** — adapter layer uses LLM-based semantic parsing (Markdown → JSON)
- **Python ≥3.11 required** — asyncio TaskGroup dependency
- **Local single-user single-process** — no distributed coordination needed

## Technology Stack

- **Package manager:** uv (Astral ecosystem)
- **Core deps:** aiosqlite, python-statemachine (≥3.0), textual (≥2.0), pydantic (≥2.0), typer
- **Dev deps:** pytest, pytest-asyncio, ruff, mypy, pre-commit
- **Build backend:** hatchling

## Development Commands

```bash
# Package management
uv init agent-team-orchestrator --python ">=3.11"
uv add <package>
uv add --group dev <package>

# Run commands through uv (auto-activates venv)
uv run pytest                    # run all tests
uv run pytest tests/unit/        # run unit tests only
uv run pytest -k "test_name"     # run single test
uv run ruff check src/           # lint
uv run ruff format src/          # format
uv run mypy src/                 # type check
```

## Planned Source Layout

```
src/ato/                         # main package (ato = Agent Team Orchestrator)
├── core.py                      # main event loop, startup/recovery
├── state_machine.py             # StoryLifecycle state machine
├── transition_queue.py          # serial transition queue
├── subprocess_mgr.py            # subprocess manager
├── convergent_loop.py           # review → fix → re-review quality gates
├── recovery.py                  # crash recovery (≤30s target)
├── cli.py                       # CLI entry point (typer)
├── adapters/
│   ├── claude_cli.py            # Claude CLI wrapper
│   ├── codex_cli.py             # Codex CLI wrapper + price table
│   └── bmad_adapter.py          # BMAD Markdown → JSON parsing
├── models/
│   ├── schemas.py               # Pydantic models
│   └── db.py                    # SQLite schema + helpers
└── tui/                         # Textual TUI
```

## Key Design Decisions

- **SQLite WAL** for crash recovery with zero data loss — every task registers PID + expected_artifact
- **TransitionQueue** serializes all state machine transitions (no concurrent writes)
- **CLI Adapter isolation** — adapter interface is the abstraction boundary; orchestrator core never touches CLI parameters directly
- **Finding deduplication** uses SHA256 of `(file_path, rule_id, severity, normalize(description))`
- **Convergent Loop** re-review scope narrows each round (only open findings passed to next review)
- **Codex cost** calculated from token counts via maintained price table (no direct cost field in CLI)
- **Interactive sessions** — system only starts, registers, times, and collects artifacts; human drives the session

## Key Planning Documents

- `docs/agent-team-orchestrator-system-design-input-2026-03-23.md` — system design brief
- `_bmad-output/planning-artifacts/prd.md` — full requirements (53 FRs, 14 NFRs)
- `_bmad-output/planning-artifacts/architecture.md` — architecture decisions
- `_bmad-output/planning-artifacts/epics.md` — 7 epics, 40+ stories
- `_bmad-output/planning-artifacts/ux-design-specification.md` — TUI/CLI interaction design
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — sprint tracking

## BMAD Integration

BMAD skills are installed in `_bmad/` with modules: core (6.2.0), bmm (6.2.0), tea (1.7.1), cis (0.1.9), bmb (1.1.0). Skills are auto-loaded when using `claude -p`. Nine role-based agents are configured (analyst, architect, dev, pm, qa, ux-designer, tech-writer, sm, quick-flow-solo-dev).

## Communication

User prefers Chinese (中文) for communication and document output.

## graphify

This project has a graphify knowledge graph at graphify-out/.

Rules:
- Before answering architecture or codebase questions, read graphify-out/GRAPH_REPORT.md for god nodes and community structure
- If graphify-out/wiki/index.md exists, navigate it instead of reading raw files
- After modifying code files in this session, run `python3 -c "from graphify.watch import _rebuild_code; from pathlib import Path; _rebuild_code(Path('.'))"` to keep the graph current
