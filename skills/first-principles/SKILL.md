---
name: first-principles
description: >-
  First-principles CS and software engineering protocol for multi-module design,
  schemas/state machines, API boundaries, Big-O performance, concurrency, or
  root-cause debugging (/first-principles). Do not load for single-file edits,
  renames, config tweaks, non-code tasks, or established repo patterns.
disable-model-invocation: false
disable-slash-command: false
---

# First-Principles Software Engineering (`/first-principles`)

Execute silently; surface only unprobed Boxed assumptions, struck candidates, and $\mathcal{O}(\cdot)$ bounds.
**Precedence**: Refines, never relaxes, `GEMINI.md` (`EC1`=Bounded Execution, `EC2`=Small Units/Flat Flow, `EC3`=No Silent Errors, `EC4`=Defensive Boundaries, `EC5`=Minimal Scope, `EC6`=Proven TDD, `EC7`=Removal Is Work).

## 1. The Operational Protocol

### Step 0: Precedent & Working-Sibling Gate
- Check whether an explicit user ruling, spec, or working sibling module in the repo already solves this problem within required bounds; if yes and unbroken, reuse its pattern.

### Step 1: Probe Boxed Premises (`[refines Grounded]`)
- Classify load-bearing premises as **Observed** (proven in-session), **Ruled** (operator policy), or **Boxed** (assumed from memory); run one cheap probe command to turn every **Boxed** constraint into **Observed** before coding.

### Step 2: Model Contracts & Canonical State (`[refines EC4, EC5]`)
- **Contracts**: Define Preconditions ($P$), Postconditions ($Q$), State Invariants ($I$), and strictly decreasing loop/recursion variants ($V_{k+1} < V_k$).
- **Single Source of Truth (SSOT)**: Store each fact once; every derived field or cache must declare its invalidation trigger.
- **Illegal States Unrepresentable**: Replace correlated booleans and nullable fields with sum types / tagged unions.
- **Parse at the Boundary**: Parse raw strings/maps/wire payloads into constrained domain types at module ingress; interior units enforce `EC4` via domain-type precondition checks rather than re-parsing raw formats.

### Step 3: Compose Candidates & Pure Core / Imperative Shell (`[refines EC2, EC5]`)
- **Simplest Survivor**: Sketch 2–3 candidates from Observed/Ruled constraints, strike any violating a fact or bound, and select the simplest survivor.
- **Functional Core, Imperative Shell (FCIS)**: Keep domain transitions and calculations pure and deterministic; isolate I/O, RPCs, clocks, and concurrency orchestration in a thin outer shell.
- **Deep Modules, Narrow Interfaces**: Hide internal representation behind minimal signatures (`no shallow pass-through wrappers`; pass only required fields, never god-objects).

### Step 4: Prove Complexity, Contention & Failure Bounds (`[refines EC1, EC3]`)
- **Time & Space $\mathcal{O}(\cdot)$**: Declare worst-case and amortized bounds for target scale $N$; eliminate hidden $\mathcal{O}(N^2)$ scans in loops and $N+1$ I/O round-trips.
- **Concurrency & Partial Failure**: Prefer immutable or task-confined state; quantify lock/queue contention; enforce idempotency and jittered backoff on external calls.

### Step 5: Execute Sequentially in Topological DAG Order (`[refines EC6]`)
- Sequence multi-layer work as a **Dependency DAG** (`1. Types & invariants` $\rightarrow$ `2. Pure core` $\rightarrow$ `3. Imperative shell`), ordering the highest-uncertainty node first and verifying each node `RED` (failing test in `tests/` first) $\rightarrow$ `GREEN` (minimal production code, test assertions untouched) before touching the next.

## 2. On-Demand Reference Router

Load only the 1–2 reference files matching the active bottleneck:

| Bottleneck / Task Type | Key Transformations Included | Reference File |
| :--- | :--- | :--- |
| Domain types, schemas, FSMs, caching, wire evolution | Sum types over boolean flags; ingress parsing; SSOT cache invalidation | [data-state-invariants.md](references/data-state-invariants.md) |
| Module boundaries, APIs, refactoring, security boundaries | Topological DAG execution; FCIS extraction; least-privilege args; AST/query parameterization | [modularity-and-interfaces.md](references/modularity-and-interfaces.md) |
| Big-O complexity, data structures, memory, backpressure | Pre-indexing $\mathcal{O}(N^2)$ scans to $\mathcal{O}(1)$ maps; batching $N+1$ RPCs/queries | [complexity-and-systems.md](references/complexity-and-systems.md) |
| Threads/tasks, locks, async ordering, retries, idempotency | Task-confined state; deterministic clock injection; idempotency keys | [concurrency-and-failure.md](references/concurrency-and-failure.md) |
| Root-cause debugging, $\mathcal{O}(\log N)$ bisection, test matrices | $\mathcal{O}(\log N)$ invariant bisection; BVA & scaling assertions | [scientific-debugging-and-verification.md](references/scientific-debugging-and-verification.md) |

## 3. Pre-Completion Verification Gate (`[refines EC6]`)

All `EC1`–`EC7` floor gates apply, plus these skill-specific checks:
- [ ] **State & Invariants**: Canonical state has a single source of truth; illegal states are unrepresentable at construction.
- [ ] **Complexity & Contention Bounds**: Worst-case/amortized Time and Space $\mathcal{O}(\cdot)$ are stated with zero hidden $\mathcal{O}(N^2)$ loops or $N+1$ I/O calls.
- [ ] **Boundary & Scale Coverage**: Tests exercise boundary values ($0, 1, N, N\pm 1$, empty, timeout) and realistic scale $N$, not only happy paths.
