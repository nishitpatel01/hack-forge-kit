# Hack Forge Kit

A lightweight, drop-in harness bundle for AI coding agents combining **Constitutional AI & Engineering Canon (`GEMINI.md`)**, **First-Principles Software Engineering (`/first-principles`)**, and **Mechanical Cross-Model Deliberation (`/debate`)**.

## Contents

| Path | Purpose |
| :--- | :--- |
| [`GEMINI.md`](GEMINI.md) | Constitutional AI rules, Engineering Canon (`EC1`–`EC7`: bounded execution, flat flow, fail-closed errors, defensive boundaries, minimal scope, TDD), and zero-fluff BLUF communication rules. |
| [`skills/first-principles/`](skills/first-principles/SKILL.md) | 5-step operational engineering protocol (`Observed/Ruled/Boxed` premise probing, canonical state invariants, Functional Core / Imperative Shell, Big-O & contention bounds, Topological DAG execution) + 5 on-demand reference guides. |
| [`skills/debate/`](skills/debate/SKILL.md) | Deterministic 7-phase adversarial debate protocol between **Gemini** and **Claude Opus** with mechanical citation checking (`debate_cite.py`) and state-machine convergence scoring (`debate_exit.py`). |

## Quick Install (< 1 Minute)

```bash
git clone <repo-url> hack-forge-kit
cd hack-forge-kit
bash install.sh
```

Or copy [`GEMINI.md`](GEMINI.md) and [`skills/`](skills/) directly into your project repository root.

## Verify Installation

Run the hermetic self-test suite for `/debate`:

```bash
bash skills/debate/scripts/selftest.sh
```

## Usage in Your Hackathon Project

1. **Always-On Engineering Floor (`GEMINI.md`)**: Automatically enforces bounded loops/timeouts, guard clauses, `.env`-only configuration, TDD verification, and concise BLUF responses.
2. **Multi-Module Design & Debugging (`/first-principles`)**:
   ```text
   /first-principles Design the ingestion pipeline and state machine for our service.
   ```
3. **Resolving Architecture Tradeoffs (`/debate`)**:
   ```text
   /debate "Should our hackathon prototype use an in-memory ring buffer or SQLite WAL for event replay?"
   ```
