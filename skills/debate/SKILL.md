---
name: debate
description: >-
  Run an adversarial dialogue between the primary model (driver) and
  its cross-family peer (companion) over a shared artifact to reach a decision,
  a ranked list of directions, a verified issue list, or a scope boundary. If
  the session runs on Gemini, the companion is Claude Opus; if Claude Opus is
  selected, the companion is Gemini. Every phase exit and convergence call is
  scored mechanically by a script. Use when the user says "/debate", "debate
  this", or wants an ambiguous design decision resolved before building.
---

# debate

This skill coordinates an adversarial, cross-family dialogue between the
**primary model** (the active driver) and its **cross-family companion**
over a shared artifact:

-   If the session is running on **Gemini**: the companion is **Claude Opus**
    (dispatched via `opus-auditor`, pinned to `MODEL_CLAUDE_OPUS`).
-   If the session is running on **Claude Opus**: the companion is **Gemini**
    (dispatched via `gemini-auditor`, pinned to `MODEL_GEMINI_PRO`).

Every phase exit, objection tracking, and quorum/stuck verdict is computed
mechanically by [`debate_exit.py`](scripts/debate_exit.py), never by the LLMs
themselves.

Scripts live in `~/.gemini/config/skills/debate/scripts/`; call
them by full path, written below as `$S`.

Default state root is `~/.gemini/agent/state/debate` (override via
`$DEBATE_STATE_ROOT`).

## Cost, stated before the first call

10–18 paid companion calls at default caps with no retries (frame 1, diverge 2,
verify 2, converge 4, closing 2), hard cap 20 including retries. A task that
turns out not to be debate-shaped ends after the 1 frame call.

Say this to the user before dispatching, with the outcome mix, then proceed:
historical debates show ~40% reach QUORUM, ~50% end STUCK with a human
tiebreaker vote, and ~10% end early as not-debate-shaped.

## -1. Pre-Flight Triage Gate

Before initializing disk state or burning companion quota, the agent must check
whether the task warrants an adversarial multi-round debate. This forms the
**first layer** of a two-layer defense against trivial debates:

1.  **Layer 1 (Pre-Flight Triage Gate)**: If a task has $\le 1$ plausible
    implementation, is an unambiguous bug/typo fix, or touches $\lt3$ files with
    no architectural tradeoff, the agent **MUST NOT** initialize a debate on
    disk or call companions. Inform the user immediately that the task has one
    obvious shape and recommend immediate implementation or a single review
    gate.
2.  **Layer 2 (Mechanical Frame Check)**: If a debate proceeds to setup on disk,
    the Phase 2 Frame round companion check serves as the mechanical fail-safe:
    if the companion finds no missing questions, criteria faults, or seed items,
    [`debate_exit.py`](scripts/debate_exit.py) returns `NOT-DEBATE-SHAPED` and
    aborts the debate early before any Diverge or Converge rounds run.
3.  **Debate-Warranted Tasks**: Proceed to Setup only when genuine architectural
    dilemmas, competing tradeoffs (e.g. memory vs disk, sync vs async), or
    substantive design ambiguities exist.

## Hard Anti-Simulation & Anti-Runaway Invariants

A model debating itself shares the same training priors, reward model, and blind
spots; simulating the other participant turns the debate into confirmation-bias
theater.

-   The driver agent is **STRICTLY PROHIBITED** from authoring, mocking, or
    synthesizing companion responses (`calls/*-<companion>.out.md` or
    `calls/*-<companion>.items.md`) directly via shell (`cat`, `echo`), native
    file tools (`write_to_file`, `replace_file_content`), or scratch scripts.
-   Every companion turn must be obtained via genuine subagent invocation
    (`invoke_subagent` with `TypeName: "opus-auditor"` or `"gemini-auditor"`) or
    verified LanguageServer companion dispatch (`debate_round.py dispatch-turn`
    / `scripts/companions/bridge.py`).
-   Any self-synthesized companion output is a fatal protocol violation rejected
    by [`debate_exit.py`](scripts/debate_exit.py).
-   **Zero Scratch Scripts / Zero Inline One-Liners (`REC-20260907-01`)**: Never
    write custom Python scratch scripts (`extract_raw_*.py`, `append_r2.py`,
    `fix_facts.py`) or `python3 -c` one-liners to extract subagent responses,
    append turns to `transcript.md`, or repair `FACTS:` blocks. Use the built-in
    CLI subcommands (`record-turn --phase <p> --round <n>`, `append-turn`, and
    `preserve-facts`) documented below.

-   **Prompt Isolation & Communication Lines**: Every companion subagent prompt
    MUST begin with:

    1.  `[ISOLATION MANDATE] Tool use is forbidden. You have no filesystem
        access; evaluate ONLY the evidence provided in this prompt.`
    2.  `[COMMUNICATION MANDATE] You must transmit your final reply to the
        parent orchestrator by calling send_message(Recipient="<parent_id>",
        Message=...).`

-   **Runtime `<parent_id>` Substitution**: Before dispatching, the driver agent
    MUST replace the placeholder `<parent_id>` in the prompt template with its
    active session conversation ID from context (e.g. from `CONVERSATION_ID`
    environment variable or session metadata).

-   **Subagent Conversation ID, Auto-Extraction & Atomic Transcript Appending**:
    Capture the exact `conversationId` returned by `invoke_subagent` and pass it
    as `<subagent_conversation_id>` to `record-turn` along with `--phase <phase>
    --round <n> --role <role>`:

    ```bash
    python3 $S/debate_round.py record-turn $D <companion> <subagent_conversation_id> $D/calls/<call_num>-<companion>.prompt.md --phase <phase> --round <n> --role <role>
    ```

    `record-turn` automatically:

    1.  Extracts the `NONCE`-prefixed companion response from
        `<subagent_conversation_id>`'s `transcript_full.jsonl` (supporting both
        `send_message` tool calls and direct `PLANNER_RESPONSE.content` replies)
        if `--response-file` is omitted, saving
        `$D/calls/<call_num>-<companion>.raw.md` and `.out.md`.
    2.  Inspects the subagent's SQLite telemetry (`gen_metadata`) to attest
        cross-family model provenance (`0X-<companion>.prov.json`).
    3.  Atomically appends `## R<n> <phase> <companion> role=<role>` (with the
        leading `NONCE` line stripped) to `$D/transcript.md`.

-   **Banned Subagent Transcript Polling & Verify Re-Read Loops**: Never call
    `view_file` on subagent `.system_generated/logs/transcript.jsonl` or
    `transcript_full.jsonl` in a polling loop to extract companion replies; wait
    for the reactive `send_message` wakeup (paired with
    `schedule(DurationSeconds=300, TimerCondition="<subagent_id>")`). If
    `debate_exit.py` returns a format or citation error during Verify/Converge,
    inspect the exact stdout error line once and fix the target file
    directly—never re-read `SKILL.md`, `transcript.md`, and
    `<call>-<companion>.raw.md` in a repetitive cycle.

## 0. Setup

### Execution Hygiene & Hook Safety

-   **Zero Inline Python Scripts (`REC-20260907-01`)**: Strictly prohibit
    executing inline Python scripts (`python3 -c`) or nested-quote f-strings in
    `run_command`. Write temporary scripts in `<scratch_dir>/script.py` using
    `write_to_file`.
-   **Canonical Mercurial/Mercurial Flags**: Every `hg` invocation MUST strictly use
    subcommand-first notation with non-interactive flags: `hg <subcommand>
    --pager=never --noninteractive [args]`.
-   **Anti-Speculation Invariant**: Do NOT invoke `view_file` on `$D/brief.md`
    before it is written, or poll companion transcripts in loops. Wait for the
    reactive `send_message` wakeup.

1.  Preflight and pin:

    ```bash
    python3 $S/debate_setup.py <repo> --mode attended|unattended --driver gemini --companion opus --heading "<Deliverable heading>"
    ```

    (Swap `--driver opus --companion gemini` if the primary session model is
    Claude Opus.) This creates `$D` (printed as `D=<dir>`), pins the repo (via
    detached Git worktree or Mercurial/workspace snapshot symlink; every citation check
    reads that tree), writes the empty transcript and artifact skeleton, and
    verifies all script selftests. On `PREFLIGHT-FAILED` stop and report.
2.  Mode is `attended` if the user is present in this session, `unattended` if
    invoked from an autonomous run.
3.  While a debate is live, do not edit the repo under debate. The pin makes
    citation checks safe against edits.

## 1. Brief

Draft `$D/brief.md` from the task and a quick read of the code it touches, with
these headings in this order: `Task`, `Read these`, `Deliverable` (artifact
headings), `Questions in dispute` (numbered `Q1..`), `Criteria` (ranked),
`Constraints and out of scope`, `Seed items` (optional, written as `### T1:
title` blocks), `Caps` (only if defaults change).

### Pre-Flight Brief Self-Check (Anti-Scope-Ratcheting)

Before proceeding to Phase 2 (Frame), execute a self-check against the brief:

1.  For architectural decisions with competing designs, ensure all candidate
    hypotheses (minimum 2 orthogonal directions) are enumerated as explicit
    `Seed items` (`### T1:`, `### T2:`).
2.  Ensure evaluation criteria are explicitly ranked.
3.  Write `$D/brief.md` via `write_to_file` before attempting any file reads on
    it. (Do not call `view_file` on `$D/brief.md` until it has been written).

If seed items exist, write their `ITEMS:` block to `$D/calls/seed.items.md`,
append to the transcript under `## R0 diverge <driver> role=none`, then:

```bash
python3 $S/debate_exit.py assign-ids $D "<Deliverable heading>" $D/calls/seed.items.md
```

## 2. Frame (1 round, 1 companion call)

Prepare prompt file `$D/calls/01-<companion>.prompt.md` with a random nonce:

> [ISOLATION MANDATE] Tool use is forbidden. You have no filesystem access;
> evaluate ONLY the evidence provided in this prompt. [COMMUNICATION MANDATE]
> You must transmit your final reply to the parent orchestrator by calling
> send_message(Recipient="<parent_id>", Message=...).
>
> DEBATE-NONCE <nonce>: begin your reply with the single line `NONCE <nonce>`,
> then the reply itself.
>
> You are the cross-family companion in a structured debate. Read the brief
> below. Do not argue the task yet. Return ONLY, in this order: `MISSING
> QUESTIONS:` (numbered, or none), `CRITERIA:` (wrong, missing or misordered
> criteria, or none), `SEED ITEMS:` (as `### T1: title` blocks with one
> paragraph each, or none), `QUESTIONS FOR HUMAN:` (or none).
>
> --- BRIEF ---
>
> <brief.md>

Dispatch companion turn:

-   If companion is `opus`: call `invoke_subagent` with `TypeName:
    "opus-auditor"` and prompt content (substituting `<parent_id>`).
-   If companion is `gemini`: call `invoke_subagent` with `TypeName:
    "gemini-auditor"` (or `self` with `Model: "pro"`).
-   Pair with `schedule(DurationSeconds=180, TimerCondition="<conv_id>")` for
    watchdog safety.

Record the companion turn with authentic out-of-band provenance and atomically
append `## R1 frame <companion> role=none` to `transcript.md` (auto-extracting
from `<subagent_conversation_id>`):

```bash
python3 $S/debate_round.py record-turn $D <companion> <subagent_conversation_id> $D/calls/01-<companion>.prompt.md --phase frame --round 1 --role none
```

Merge: write `ADOPTED: <one line>` or `REJECTED: <one line reason>` to
`$D/calls/r1-frame-<driver>.md` (write `ADOPTED: none` if there was nothing to
merge), append to `transcript.md` via `append-turn`, and update `brief.md`:

```bash
python3 $S/debate_round.py append-turn $D 1 frame <driver> none $D/calls/r1-frame-<driver>.md
```

Add adopted seed items to `$D/calls/frame-seed.items.md` and run `assign-ids $D
"<heading>" $D/calls/frame-seed.items.md`.

Check exit:

```bash
python3 $S/debate_exit.py $D frame
```

On `EXIT`: continue to Diverge. On `NOT-DEBATE-SHAPED`: companion found no
missing questions, criteria faults, or seed items; stop and report that the task
has one obvious shape.

## 3. Diverge (cap 2 rounds)

Before each round: `cp $D/artifact.md $D/artifact.prev.md`.

1.  Driver writes its own turn (an `ITEMS:` block or `ITEMS: none`) to
    `$D/calls/d<n>-<driver>.items.md`, appends to `transcript.md` via `python3
    $S/debate_round.py append-turn $D <n> diverge <driver> none
    $D/calls/d<n>-<driver>.items.md`, and runs `assign-ids $D "<heading>"
    $D/calls/d<n>-<driver>.items.md`.
2.  Prepare `$D/calls/<call_num>-<companion>.prompt.md` with `DEBATE-NONCE
    <nonce>`:

> [ISOLATION MANDATE] Tool use is forbidden. You have no filesystem access;
> evaluate ONLY the evidence provided in this prompt. [COMMUNICATION MANDATE]
> You must transmit your final reply to the parent orchestrator by calling
> send_message(Recipient="<parent_id>", Message=...).
>
> DEBATE-NONCE <nonce>: begin your reply with the single line `NONCE <nonce>`,
> then the reply itself.
>
> Phase: DIVERGE, round <n>. Add items to the artifact. Critique is forbidden in
> this phase; any objection is a protocol error. Refer to your own new items by
> `T1`, `T2` inside the paragraph, never in a code span. Return ONLY an `ITEMS:`
> block of `### T<n>: title` entries with one paragraph each, or `ITEMS: none`.
>
> --- BRIEF ---
>
> <brief.md>
>
> --- ARTIFACT ---
>
> <artifact.md>

Dispatch companion via `invoke_subagent` and record + append turn with:

```bash
python3 $S/debate_round.py record-turn $D <companion> <subagent_conversation_id> $D/calls/<call_num>-<companion>.prompt.md --phase diverge --round <n> --role none
```

Save its `ITEMS:` block to `$D/calls/d<n>-<companion>.items.md`, run
`assign-ids`, then:

```bash
python3 $S/debate_exit.py $D diverge
```

`EXIT` moves to Verify; `CONTINUE` runs round 2.

## 4. Verify: source-cited repository claims (1 round, may repeat once)

Verify checks which factual claims the repository text supports. Citations must
strictly use the syntax: `<path>:<line> "<snippet>"` (e.g. `foo/bar.py:42 "def
helper()"`).

Prepare line-numbered code excerpts from `$D/pinned/` (`debate_cite.py
pack-excerpts`) and embed them into `$D/calls/<call_num>-<companion>.prompt.md`
with `DEBATE-NONCE <nonce>` so the isolated companion can cite exact line
numbers:

```bash
python3 $S/debate_cite.py pack-excerpts $D > $D/calls/verify-excerpts.md
```

> [ISOLATION MANDATE] Tool use is forbidden. You have no filesystem access;
> evaluate ONLY the evidence provided in this prompt. [COMMUNICATION MANDATE]
> You must transmit your final reply to the parent orchestrator by calling
> send_message(Recipient="<parent_id>", Message=...).
>
> DEBATE-NONCE <nonce>: begin your reply with the single line `NONCE <nonce>`,
> then the reply itself.
>
> Phase: VERIFY, round <n>. For every item, tag each factual claim it rests on:
> `- VERIFIED(<path>:<line> "<snippet>") <claim>` or `- ASSUMED <claim>`. The
> path is relative to the repo: no absolute path, no `..`, no `~`. Use the line
> numbers from `--- PINNED EXCERPTS ---` below. Return ONLY `FACTS I<k>:` blocks
> for items you tagged and one `DISPUTE:` line listing only item ids (`DISPUTE:
> I3, I7`), or `DISPUTE: none`.
>
> --- BRIEF ---
>
> <brief.md>
>
> --- ARTIFACT ---
>
> <artifact.md>
>
> --- PINNED EXCERPTS ---
>
> <verify-excerpts.md>

1.  Dispatch companion via `invoke_subagent` and record + append turn to
    `transcript.md` with:

    ```bash
    python3 $S/debate_round.py record-turn $D <companion> <subagent_conversation_id> $D/calls/<call_num>-<companion>.prompt.md --phase verify --round <n> --role none
    cp $D/calls/<call_num>-<companion>.out.md $D/calls/verify<n>-<companion>.facts.md
    ```
2.  Driver writes its own verify facts to `$D/calls/verify<n>-<driver>.facts.md`
    and appends to `transcript.md`:

    ```bash
    python3 $S/debate_round.py append-turn $D <n> verify <driver> none $D/calls/verify<n>-<driver>.facts.md
    ```
3.  Snap any near-miss line citations against `$D/pinned/` and merge facts into
    `artifact.md`:

    ```bash
    python3 $S/debate_cite.py resolve-lines $D $D/calls/verify<n>-<driver>.facts.md $D/calls/verify<n>-<companion>.facts.md
    python3 $S/debate_report.py merge-facts $D $D/calls/verify<n>-<driver>.facts.md $D/calls/verify<n>-<companion>.facts.md
    ```
4.  Run verification check (ONLY after both `## R<n> verify` turns are appended
    to `transcript.md`):

    ```bash
    python3 $S/debate_exit.py $D verify
    ```

    Append each `DOWNGRADED:` line to the transcript. `EXIT` moves to Converge.

## 5. Converge (cap 3 rounds)

Roles alternate each round between Driver and Companion:

-   **Round 1**: Driver is **Reviser**, Companion is **Advocate**.
-   **Round 2**: Companion is **Reviser**, Driver is **Advocate**.
-   **Round 3**: Driver is **Reviser**, Companion is **Advocate**.

Before round 1 only: `cp $D/artifact.md $D/artifact.prev.md`.

### Reviser Turn

Reviser rewrites `artifact.md` to address all open objections, carrying all
`FACTS:` lines forward verbatim:

```
ARTIFACT:
<full artifact text>

ANSWERS:
O1: <explanation of fix or citation>
```

When Driver is Reviser: Driver writes its turn to
`$D/calls/c<n>-<driver>-reviser.md`, appends to `transcript.md` via `python3
$S/debate_round.py append-turn$D <n> converge <driver> reviser
$D/calls/c<n>-<driver>-reviser.md`, and runs `preserve-facts` to guarantee every
retained item `I<k>` keeps its verbatim `FACTS:` lines:

```bash
python3 $S/debate_report.py preserve-facts $D
```

When Companion is Reviser (Round 2): Prepare prompt
`$D/calls/<call_num>-<companion>.prompt.md` with `DEBATE-NONCE <nonce>`:

> [ISOLATION MANDATE] Tool use is forbidden. You have no filesystem access;
> evaluate ONLY the evidence provided in this prompt. [COMMUNICATION MANDATE]
> You must transmit your final reply to the parent orchestrator by calling
> send_message(Recipient="<parent_id>", Message=...).
>
> DEBATE-NONCE <nonce>: begin your reply with the single line `NONCE <nonce>`,
> then the reply itself.
>
> Phase: CONVERGE, round <n>. You are the Reviser. Rewrite the artifact to
> answer all open objections below. Carry all FACTS lines forward verbatim.
>
> --- OPEN OBJECTIONS ---
>
> <open objections>
>
> --- ARTIFACT ---
>
> <artifact.md>
>
> Return ONLY: ARTIFACT: <revised artifact>
>
> ANSWERS: O<n>: <explanation of fix or citation>

Dispatch companion via `invoke_subagent`, record + append under `## R<n>
converge <companion> role=reviser`, update `artifact.md`, and run
`preserve-facts`:

```bash
python3 $S/debate_round.py record-turn $D <companion> <subagent_conversation_id> $D/calls/<call_num>-<companion>.prompt.md --phase converge --round <n> --role reviser
python3 $S/debate_report.py preserve-facts $D
```

### Advocate Turn

Advocate is mandated to attack weak reasoning and find decisive flaws:

```
STANCE: ACCEPT | OBJECT
OBJECTIONS:
O<n> (I<k>) [advocate]: <one paragraph objection>
none
CONFIDENCE:
CONFIDENCE O<n>: <0.0 to 1.0>
REVIEW:
O<n> ANSWERED | EVADED
none
QUESTIONS:
none
```

When Driver is Advocate (Round 2): Driver writes its turn to
`$D/calls/c2-<driver>-advocate.md` and appends via `python3 $S/debate_round.py
append-turn $D 2 converge \ltdriver\gt
advocate$D/calls/c2-<driver>-advocate.md`. When Companion is Advocate (Round 1,
Round 3): Prepare prompt `$D/calls/<call_num>-<companion>.prompt.md` with
`DEBATE-NONCE <nonce>`:

> [ISOLATION MANDATE] Tool use is forbidden. You have no filesystem access;
> evaluate ONLY the evidence provided in this prompt. [COMMUNICATION MANDATE]
> You must transmit your final reply to the parent orchestrator by calling
> send_message(Recipient="<parent_id>", Message=...).
>
> DEBATE-NONCE <nonce>: begin your reply with the single line `NONCE <nonce>`,
> then the reply itself.
>
> Phase: CONVERGE, round <n>. You are the Advocate. Attack weak reasoning and
> find decisive flaws in the revised artifact.
>
> --- BRIEF ---
>
> <brief.md>
>
> --- ARTIFACT ---
>
> <artifact.md>
>
> --- PREVIOUS OBJECTIONS ---
>
> <previous objections and reviser answers>
>
> Return ONLY: STANCE: ACCEPT | OBJECT OBJECTIONS: O<n> (I<k>) [advocate]:
> <one paragraph objection> none CONFIDENCE: CONFIDENCE O<n>: <0.0 to 1.0>
> REVIEW: O<n> ANSWERED | EVADED none QUESTIONS: none

Dispatch companion via `invoke_subagent` and record + append under `## R<n>
converge <companion> role=advocate`:

```bash
python3 $S/debate_round.py record-turn $D <companion> <subagent_conversation_id> $D/calls/<call_num>-<companion>.prompt.md --phase converge --round <n> --role advocate
```

Check converge exit:

```bash
python3 $S/debate_exit.py $D converge
```

-   `CONTINUE`: proceed to next converge round.
-   `QUORUM`: consensus reached, proceed to Step 7.
-   `STUCK <reason>`: deadlock, proceed to Step 6.
-   `PROTOCOL-ERROR <p> <reason>`: retry turn once with reason quoted.

## 6. Closing statements & Tiebreak (STUCK only)

When the debate is stuck: Both Driver and Companion author a 200-word closing
statement recommending an action, and end with a ranked vote line:

```
VOTE: I2 > I1 > I3
```

Prepare closing prompt `$D/calls/<call_num>-<companion>.prompt.md` with
`DEBATE-NONCE <nonce>`:

> [ISOLATION MANDATE] Tool use is forbidden. You have no filesystem access;
> evaluate ONLY the evidence provided in this prompt. [COMMUNICATION MANDATE]
> You must transmit your final reply to the parent orchestrator by calling
> send_message(Recipient="<parent_id>", Message=...).
>
> DEBATE-NONCE <nonce>: begin your reply with the single line `NONCE <nonce>`,
> then the reply itself.
>
> Phase: CLOSING. The debate has deadlocked. Author a 200-word closing statement
> explaining which proposal to adopt and why. End with a ranked vote line `VOTE:
> I<x> > I<y>`.

Dispatch companion via `invoke_subagent` and record + append under `## R1
closing <companion> role=none`:

```bash
python3 $S/debate_round.py record-turn $D <companion> <subagent_conversation_id> $D/calls/<call_num>-<companion>.prompt.md --phase closing --round 1 --role none
```

Driver authors its own closing turn and appends via `python3 $S/debate_round.py
append-turn $D 1 closing <driver> none $D/calls/closing-<driver>.md`.

Human Tiebreaker Vote: Driver prompts the user via `ask_question` presenting the
closing statements and requesting a human tiebreak decision among the disputed
items (`I1`, `I2`, etc.).

## 7. Report, Record, Teardown & Post-Debate Approval Gate

1.  Generate report:

    ```bash
    python3 $S/debate_report.py report $D --task "<task description>" --outcome <QUORUM|STUCK|NOT-DEBATE-SHAPED>
    ```
2.  Fill in the `## Decision` summary in `$D/report.md`.
3.  Record outcome in central ledger (passing `--tiebreak "<choice>"` if STUCK):

    ```bash
    python3 $S/debate_report.py record $D --task "<task description>" --outcome <QUORUM|STUCK|NOT-DEBATE-SHAPED> [--tiebreak "<choice>"]
    ```
4.  Teardown pinned tree:

    ```bash
    python3 $S/debate_setup.py --teardown $D
    ```
5.  Print summary to user:

```
Debate <id>: <outcome>
Artifacts: <absolute $D>
  report.md      the write-up: decision, objections, assumed facts, artifact diff
  transcript.md  every turn from all participants
  artifact.md    the final artifact
  calls/         one .prompt.md + .out.md per model call
```

6.  **Mode-Aware Post-Debate Approval Gate & Next-Step Bridge**:
    -   **Mandatory Next-Step Bridge**: Immediately upon completing
        `--teardown`, the driver agent MUST present the concise Decision summary
        from `report.md` along with a concrete roadmap continuation proposal for
        the Rank 1 adopted direction. Never yield the turn with passive
        standing-by phrasing (e.g., "Let me know what you want to do").
    -   **Interactive (`attended`) Mode (Plan Gate & Gating Mandate)**: Do not
        implement anything or write code without approval. Upon completing
        `--teardown`:
        1.  **Draft Implementation Plan**: Immediately draft the concrete
            implementation plan artifact in the active session artifact
            directory (`plan.md`) implementing the adopted direction (consensus
            architecture for QUORUM, or winning direction for STUCK tiebreak)
            from `report.md`.
        2.  **Gated Plan Approval**: Present the plan artifact to the user and
            invoke `ask_question` offering explicit options:
            -   "(Recommended) Proceed with implementation of plan.md"
            -   "Gate the plan with Claude (via gate-with-claude)"
            -   "Explore alternative directions"
            -   "Conclude session"
    -   **Autonomous (`unattended` / `/goal`) Mode (`REC-20260909-01`)**: Emit
        the decision record and draft the implementation plan artifact
        (`plan.md`) in the session artifact directory for the adopted direction.
        If running within the scope of a pre-approved top-level plan, proceed
        autonomously to implementation (executing `gate-with-claude`
        autonomously if cross-family verification is mandated by the task
        specification). If running unattended without a prior approved plan,
        stop after authoring `plan.md` and logging the report; do not execute
        code.
