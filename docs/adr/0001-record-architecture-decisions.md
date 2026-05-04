# ADR-0001 — We record architecture decisions

**Status:** Accepted
**Date:** 2026-05-04

## Context

Pindorama will be operated on by autonomous coding agents (Claude Code in primary, possibly others). Agents lose continuity across sessions. They also tend to re-derive past decisions from scratch, sometimes arriving at different answers and silently changing the design.

A short, append-only record of "why we chose X over Y" lets a fresh agent (or a human reviewer) understand the constraints in 30 seconds without reading the whole codebase or git history.

## Decision

Record non-obvious architectural decisions as ADRs in `docs/adr/`, numbered sequentially. Format:

- One file per decision: `NNNN-short-slug.md`.
- Sections: `Context`, `Decision`, `Consequences` (and optionally `Alternatives Considered`).
- Status one of: `Proposed`, `Accepted`, `Superseded by ADR-NNNN`, `Deprecated`.
- 1–2 paragraphs each. ADRs are summaries, not specs.

ADRs are reachable from `CLAUDE.md` and from `.claude/agents/researcher.md`. The `reviewer` subagent reads them when evaluating diffs.

## Consequences

- **Pro:** A new agent can answer "why is the storage layout content-addressed?" without scrolling through code.
- **Pro:** Decisions become reviewable artifacts; bad ones get superseded explicitly instead of drifting silently.
- **Con:** Mild friction on every architectural decision. Mitigated by keeping ADRs short.

## Alternatives considered

- **No ADRs, only CLAUDE.md.** Rejected: CLAUDE.md must stay ≤80 lines per Operating Brief §1.5; long-form rationale crowds it.
- **Inline comments in code.** Rejected: rationale tied to a single file decays when files are renamed/split.
- **Wiki / external doc.** Rejected: keeping decisions in-repo means they travel with the code and are visible to agents.
