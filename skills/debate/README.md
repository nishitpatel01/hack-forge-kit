# Debate Skill for AI Coding Agents

An adversarial, cross-family technical debate skill running in the coding agent between
**Gemini** and **Claude Opus** over a shared architectural artifact.

Unlike typical LLM multi-agent discussions where models subjectively judge
consensus, the debate skill enforces **100% mechanical state machine scoring**:
every phase exit, protocol check, citation verification, and convergence call is
evaluated by deterministic Python scripts.

--------------------------------------------------------------------------------

## Why Debate?

1.  **Eliminating Model Blind Spots**: A single model family reviewing its own
    proposal shares the same training priors, reward biases, and blind spots. A
    cross-family debate forces proposals to survive two distinct frontier
    architectures (**Gemini Pro** and **Claude Opus**).
2.  **Deterministic Repository Grounding**: Every factual claim about code or
    documentation must cite exact repository lines (`path:line "snippet"`).
    Citations are mechanically checked by `debate_cite.py` against a pinned
    workspace tree; ungrounded claims are automatically downgraded to `ASSUMED`.
3.  **Anti-Conformity Guardrails**: Numerical confidence scores are stripped
    from in-flight prompts to prevent conformity cascades, and rotating
    adversarial roles (Reviser vs. Advocate) ensure thorough stress-testing.

--------------------------------------------------------------------------------

## Model Architecture & Roles

The debate skill operates between two model families:

Role          | Standard Configuration                       | Alternate (when driven by Opus)  | Function in Protocol
:------------ | :------------------------------------------- | :------------------------------- | :-------------------
**Driver**    | **Gemini** (Active agent session)           | **Claude Opus** (Active session) | Drives the debate, drafts brief, merges feedback, compiles report.
**Companion** | **Claude Opus** (`opus-auditor`, `M367`) | **Gemini** (`gemini-auditor`)    | Challenges assumptions, proposes seed items, stress-tests reasoning.

--------------------------------------------------------------------------------

## Directory Structure

```
skills/debate/
├── README.md                         # This technical documentation
├── SKILL.md                          # Agent instructions and driver procedure
├── fixtures/                         # Replay test fixtures (14-turn regression suite)
│   └── d-2026-09-16-bdb3ed/
│       ├── 01-gemini.out.md ... 14-opus.out.md
│       └── index.txt
└── scripts/
    ├── debate_setup.py               # Workspace preflight, Git/Mercurial pinning, and teardown
    ├── debate_round.py               # Stateless companion runner with timeout & cap locks
    ├── debate_cite.py                # Mechanical string/line citation verifier
    ├── debate_exit.py                # Deterministic phase state machine & grammar checker
    ├── debate_report.py              # Report generation, artifact diffing, and ledger tracking
    ├── selftest.sh                   # Hermetic test runner verifying all scripts and fixtures
    └── companions/                   # Standalone companion runners
        ├── bridge.py                 # Common dispatch bridge
        ├── gemini.py                 # Gemini companion runner
        └── opus.py                   # Claude Opus companion runner
```

--------------------------------------------------------------------------------

## The 7-Phase Protocol

```
+-----------------------------------------------------------------------------+
| 0. Setup & Pinning (debate_setup.py pins repo to prevent mid-debate churn)  |
+-----------------------------------------------------------------------------+
                                      |
                                      v
+-----------------------------------------------------------------------------+
| 1. Brief Formulation (brief.md defines task, criteria, and boundaries)      |
+-----------------------------------------------------------------------------+
                                      |
                                      v
+-----------------------------------------------------------------------------+
| 2. Frame Phase (Companion checks brief. Exits early if NOT-DEBATE-SHAPED)   |
+-----------------------------------------------------------------------------+
                                      |
                                      v
+-----------------------------------------------------------------------------+
| 3. Diverge Phase (Cap: 2 rounds. Expands options; critique forbidden)       |
+-----------------------------------------------------------------------------+
                                      |
                                      v
+-----------------------------------------------------------------------------+
| 4. Verify Phase (Cites repo text. debate_cite.py downgrades bogus claims)   |
+-----------------------------------------------------------------------------+
                                      |
                                      v
+-----------------------------------------------------------------------------+
| 5. Converge Phase (Cap: 3 rounds. Reviser vs Advocate alternating rounds)   |
+-----------------------------------------------------------------------------+
               |                                             |
         (Consensus)                                    (Deadlock)
               v                                             v
+-----------------------------+               +-------------------------------+
| 7. Final Report & Teardown  | <------------ | 6. Closing Statements & Vote  |
|    (QUORUM reached)         |               |    (Ranked Borda tiebreak)    |
+-----------------------------+               +-------------------------------+
```

### Phase Details

1.  **Phase -1: Pre-Flight Triage Gate (Early Abort)**:
    -   Evaluates whether the task warrants setting up a multi-round debate.
    -   If the task has $\le 1$ plausible implementation, is an unambiguous bug
        or typo fix, or touches $\lt3$ files with no architectural tradeoff, the
        agent aborts immediately before initializing disk state or burning
        calls.
2.  **Phase 0: Setup & Pinning (`debate_setup.py`)**:
    -   Initializes `$D` (`~/.gemini/agent/state/debate/d-YYYY-MM-DD-xxxxxx/`).
    -   Pins the active workspace (via detached Git worktree or Mercurial/workspace
        snapshot symlink) so subsequent edits cannot move citations off their
        lines.
    -   Verifies that all internal self-tests pass and companions resolve.
3.  **Phase 1: Brief (`brief.md`)**:
    -   Driver frames the problem: `Task`, `Read these`, `Deliverable`,
        `Questions in dispute` (`Q1..`), ranked `Criteria`, and `Constraints`.
4.  **Phase 2: Frame (Early Abort Gate)**:
    -   Companion inspects the brief for missing questions or criteria flaws.
    -   If neither participant finds any defect or missing question,
        `debate_exit.py` returns `NOT-DEBATE-SHAPED`. The skill aborts
        immediately after 1 companion call, saving ~15 calls on trivial tasks.
5.  **Phase 3: Diverge (Cap: 2 Rounds)**:
    -   Both models propose options (`### T<n>: title`).
    -   Critique is strictly prohibited in this phase; any premature objection
        triggers a `PROTOCOL-ERROR`.
6.  **Phase 4: Verify (Repository Grounding)**:
    -   Participants tag claims: `- VERIFIED(path:line "snippet")` or `-
        ASSUMED`.
    -   `debate_cite.py` mechanically checks snippets against the pinned tree.
        Failed citations are automatically rewritten as `ASSUMED [downgraded:
        ...]`.
7.  **Phase 5: Converge (Cap: 3 Rounds)**:
    -   Models alternate roles:
        -   **Reviser**: Rewrites `artifact.md` to answer open objections,
            retaining all `FACTS:` lines verbatim (anti-tamper invariant).
        -   **Advocate**: Mandated to attack weak assumptions (`[advocate]`)
            until exhausted.
    -   Reaches `QUORUM` when the advocate exhausts all attacks and all open
        objections are resolved.
8.  **Phase 6: Closing Statements (STUCK Only)**:
    -   If converge rounds cap out, both models write a 200-word statement and a
        ranked vote line (`VOTE: I1 > I2 > I3`).
    -   `debate_report.py` tallies the Borda rank votes to give the human a
        decisive tiebreaker.
9.  **Phase 7: Report, Record & Teardown**:
    -   Generates `$D/report.md` with artifact diffs and objection ledgers.
    -   Records outcome metrics in the central ledger (`debate.log`).
    -   Tears down the pinned workspace tree.

--------------------------------------------------------------------------------

## Running a Debate

### 1. In Interactive Agent Chat

Invoke the skill:

```
/debate "Should we use an in-memory cache or a PostgreSQL-backed table for session storage?"
```

The agent automatically initializes `$D`, drafts the brief, dispatches Claude Opus
via `invoke_subagent` (`opus-auditor`), and scores every phase mechanically with
`debate_exit.py`.

### 2. Via Command-Line Scripts

```bash
# 1. Setup run directory and pin repo
python3 skills/debate/scripts/debate_setup.py . \
  --mode attended \
  --driver gemini \
  --companion opus \
  --heading "Architecture Options"

# 2. Run companion turn
python3 skills/debate/scripts/debate_round.py $D opus calls/frame-opus.prompt.md

# 3. Check phase exit
python3 skills/debate/scripts/debate_exit.py $D frame
```

--------------------------------------------------------------------------------

## Testing & Verification

The skill contains a hermetic self-test suite covering all 5 core scripts and a
14-turn replay fixture:

```bash
bash skills/debate/scripts/selftest.sh
```

Expected output:

```
debate_cite selftest: ok
debate_exit selftest: ok
debate_round selftest: ok
debate_report selftest: ok
debate_setup selftest: ok
```

