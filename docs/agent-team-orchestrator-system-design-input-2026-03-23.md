# Agent Team Orchestrator System Design Input

## Project Name

Agent Team Orchestrator

## Positioning

This project is not a single AI assistant and not a fragile collection of terminal tricks, prompt conventions, tmux panes, or long-lived orchestration sessions. It is a true multi-role AI software team orchestration system.

The system should allow product managers, architects, UI/UX experts, developers, reviewers, QA engineers, and regression verifiers to collaborate through BMAD SOPs inside one coherent workflow. The system owns orchestration, state, approvals, recovery, audit, and parallel execution. Skills and LLM agents own professional work within clearly bounded role contracts.

## Background Problem

Previous attempts relied too heavily on tmux, PTY, pane protocols, sentinel output, FIFO/event files, and long-running commander-style agents. That family of solutions created five recurring problems:

1. Runtime mechanics became too complex, with deep call chains and too many failure points.
2. Terminal output parsing was fragile and sensitive to ANSI codes, prompt variations, and model behavior changes.
3. Long-running global agent contexts degraded over time and reduced decision quality.
4. Recovery was expensive because runtime truth had to be reconstructed from logs, panes, or partial files.
5. Shared state, role responsibilities, approvals, and artifact flow were not cleanly separated.

The new system should return to first principles:

- Humans define goals, authorize irreversible actions, and make judgment calls.
- The system orchestrates collaboration and maintains runtime truth.
- Agents execute role-specific work.
- Skills provide SOP and expertise, not orchestration.

## Goals

The system should achieve the following:

1. Support multi-role collaboration across PM, Architect, UX, Story Creator, Dev, Reviewer, QA, Regression, and Human Operator roles.
2. Reuse BMAD skills as role-specific working methods.
3. Support batch planning, story-level execution, and parallel implementation via git worktrees.
4. Support human-in-the-loop checkpoints including batch selection, design approval, timeout recovery, UAT, and merge authorization.
5. Use structured artifacts as the collaboration medium instead of relying on long conversational context between agents.
6. Support retry, recovery, auditability, and explicit evidence for decisions and outcomes.
7. Work first as a local single-user development system, without requiring cloud infrastructure or distributed coordination.

## Non-Goals

The first version should not aim to do the following:

1. Replace the human project lead.
2. Fully automate every development activity.
3. Treat GUI as the first priority.
4. Use tmux panes or terminal sessions as the core system boundary.
5. Let agents manage global runtime state directly.
6. Optimize first for multi-user cloud collaboration.

## Core Principles

The architecture should follow these principles:

1. Human decides, system orchestrates, agents execute.
2. The system owns flow, state, approvals, and recovery. Agents do not.
3. Roles collaborate through artifacts, not through shared long-lived dialogue.
4. Runtime truth belongs in a structured state store, not ad hoc YAML files and terminal output.
5. Git and worktrees are the execution plane, not the runtime state system.
6. Headless core first, TUI first, GUI later if needed.
7. Prefer stateless, per-task agent invocation over long-lived orchestration agents.
8. Skills remain role SOP plugins, not workflow engines.

## Primary User

The primary user is a developer or AI-driven team lead who wants to run a local human-in-the-loop software delivery team with strong process control and parallel execution.

This is a system for operating an AI-assisted software team, not an end-user application.

## System Shape

The target system should include:

1. A headless Orchestrator as the control plane.
2. A structured runtime state store as the source of truth.
3. An Artifact Registry for deliverables and evidence.
4. A Worker Runner that launches Claude, Codex, or other role agents.
5. A Worktree Manager for story-level parallel execution.
6. An Approval Engine for human decisions.
7. A TUI as the first interaction surface.
8. A role layer powered by BMAD skills and task contracts.

## Recommended Architecture

The recommended direction is an artifact-driven role orchestration architecture.

It should be organized into five layers:

1. Human Control Layer
   Humans inspect state, review artifacts, approve irreversible actions, and resolve ambiguity.

2. Orchestrator Control Plane
   The system dispatches tasks, advances state, records approvals, tracks runs, and handles recovery.

3. Stateless Role Workers
   Claude, Codex, and other workers are invoked per task with explicit contracts, then exit.

4. Git and Worktree Execution Plane
   Planning work runs on the main repository, while implementation work runs in story-specific worktrees.

5. Artifacts and Audit Trail
   Story files, architecture notes, review findings, QA reports, regression reports, prototypes, and logs are persisted as explicit artifacts with traceable provenance.

## Key Architecture Decisions

1. The core should be headless rather than UI-driven.
2. The first interface should be a TUI rather than a GUI.
3. Runtime state should be stored in SQLite rather than YAML.
4. Artifacts should remain Git-managed files.
5. Agent execution should prefer structured CLI contracts over interactive terminal control.
6. The system should not rely on tmux metadata, pane titles, or stdout sentinels as protocol.
7. Skills should remain reusable, but orchestration should move into the system.

## Role Model

The system should support at least the following roles:

1. PM
   Owns batch goals, requirements shaping, story selection proposals, and prioritization input.

2. Architect
   Owns technical constraints, architecture decisions, interfaces, and implementation boundaries.

3. UX and UI
   Own visual structure, interaction design, prototypes, and design specifications.

4. Story Creator
   Converts planning artifacts into implementation-ready story documents.

5. Developer
   Implements a story inside a specific worktree.

6. Reviewer
   Performs independent code review and outputs structured findings.

7. QA
   Generates and runs automated checks, reporting evidence, gaps, and blocking issues.

8. Regression Runner
   Performs integration-level validation on main after merge.

9. Human Operator
   Selects the batch, authorizes merges, performs UAT decisions, and resolves exceptional situations.

## Task Types

The system must distinguish between two types of work:

### 1. Structured Jobs

These can be fully automated with explicit input and output contracts. Examples include:

- batch assessment
- story creation
- story validation
- architecture review
- code review
- QA generation and execution
- regression
- gate verification

### 2. Interactive Sessions

These are not suitable for deep machine-controlled orchestration. Examples include:

- complex implementation
- exploratory debugging
- iterative UX refinement
- high-uncertainty solution work

The first version should not try to fully orchestrate the internal dialogue of interactive sessions. The system only needs to launch, register, time-bound, and record them.

## Collaboration Medium

Roles should collaborate through explicit artifacts rather than hidden conversational memory.

Typical artifacts include:

1. batch brief
2. architecture decision
3. UX spec or prototype
4. story file
5. validation result
6. review findings
7. QA report
8. regression report
9. merge decision
10. run evidence and audit logs

For every task, the system should know:

1. Which artifacts are inputs
2. Which artifacts are outputs
3. How outputs are validated
4. How the result affects state progression

## Recommended Workflow

The end-to-end system should be divided into three lanes.

### 1. Planning Lane

- batch assessment
- PM, Architect, and UX collaboration
- story creation
- story validation
- human approval of the batch

### 2. Delivery Lane

- worktree creation
- development inside worktree
- code review
- fix loop
- QA
- UAT support

### 3. Integration Lane

- merge queue
- rebase and merge
- regression on main
- cleanup and archive

## Human Approval Points

The following actions should require human approval or recorded human judgment:

1. Selecting the current batch
2. Approving critical architecture or design changes
3. Deciding timeout recovery actions such as retry, skip, or abort
4. UAT pass or fail decisions
5. Merge authorization
6. High-risk conflict handling
7. Continue or stop decisions after failed regression

Approvals should be stored as structured records, not handled as transient stdin prompts.

## Recommended Technical Route

The initial technical route should be:

1. Core Orchestrator
   Use Python asyncio or Node.js, prioritizing simplicity, testability, and recovery.

2. Runtime State Store
   Use SQLite as the runtime source of truth.

3. Worker Runner
   Standardize how Claude, Codex, and other workers are launched.
   Prefer structured output capabilities over interactive terminal parsing.

4. Artifact Registry
   Every run should register produced artifacts, evidence, and validation status.

5. Worktree Manager
   Centralize worktree creation, validation, opening, cleanup, and merge queue preparation.

6. Approval Engine
   Track pending approvals, decisions, timestamps, and rationale.

7. TUI
   Use Textual or a similar framework for the first operator-facing control surface.

## TUI Scope

The TUI is not the core system, only the first interaction layer.

The first TUI should support:

1. viewing batch and story status
2. viewing task runs and blockers
3. viewing pending approvals
4. opening worktrees and artifacts
5. retrying, canceling, or re-dispatching tasks
6. showing logs, summaries, and evidence paths
7. surfacing timeout, failure, and escalation states

## MVP Scope

The first version should focus on:

1. batch selection and runtime state management
2. artifact registration
3. worktree lifecycle management
4. structured job orchestration
5. automated validation, review, QA, and regression
6. human approval checkpoints
7. TUI inspection and control
8. audit logs and recovery

For development itself, the MVP may use a hybrid approach:

- the system starts or registers a development session
- the human and agent collaborate inside the worktree
- after explicit submit-for-review, the system resumes automated review, QA, merge, and regression flow

## Success Criteria

The system will be successful if:

1. parallel story delivery becomes more reliable than the current workflow
2. recovery is much simpler and more deterministic
3. role boundaries become clear and enforceable
4. the human spends more time making judgments than managing terminal mechanics
5. review, QA, merge, and regression all produce clear evidence trails
6. BMAD skills remain reusable but no longer control the system
7. adding a new role or phase does not require rewriting the entire orchestration mechanism

## Critical Risks to Address During Design

The full BMAD design process should explicitly address:

1. the boundary between interactive development sessions and orchestrated structured tasks
2. which phases must be fully structured and which may remain human-guided
3. how artifacts are named, versioned, and resolved
4. how to prevent multiple roles from writing shared state directly
5. how retryable, cancelable, and recoverable task models should work
6. how BMAD skills will align with the system task contract
7. how special artifacts such as `.pen` prototypes fit into the artifact model

## One-Sentence Vision

Build a locally operated AI software team orchestration system where humans steer, the system coordinates, multiple BMAD-powered specialist agents execute role-based work, and git worktrees enable parallel delivery through explicit artifacts and auditable control.
