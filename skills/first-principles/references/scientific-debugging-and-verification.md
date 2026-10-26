# Scientific Debugging, Contracts & Verification
> **Refines**: `GEMINI.md` `Grounded`, `Root Cause`, `[EC6]` (Proven TDD), `[EC7]` (Removal Is Work) | **Load Trigger**: Root-cause debugging, $\mathcal{O}(\log N)$ fault isolation, observability, or test matrix design.

Operational rules for formal contracts, $\mathcal{O}(\log N)$ fault isolation, control-theoretic observability, and high-signal test matrices.

---

## 1. Formal Contracts ($\{P\}\ C\ \{Q\}$) & Loop Variants (`[refines EC1, EC4]`)

Treat every function and loop as a mathematical proof obligation:

- **Hoare Triple $\{P\}\ C\ \{Q\}$**:
  - **Precondition ($P$)**: What must hold on entry; evaluated before any state mutation or I/O occurs.
  - **Postcondition ($Q$)**: The exact relation between input and output that callers and tests assert.
  - **Representation Invariant ($I$)**: If $I(\text{self})$ holds before method $C$, $I(\text{self}')$ must hold after $C$ (including when $C$ returns a recoverable error).
- **Termination Variant ($V \in \mathbb{N}$)**:
  - For every `while`/`for` loop, retry loop, queue consumer, or recursive call, define a scalar variant $V$ (e.g., `remainingItems`, `hi - lo`, `maxDepth - depth`, `maxAttempts - attempt`) such that:
    1. $V \ge 0$ is an invariant.
    2. Every step strictly decreases $V$ ($V_{\text{next}} < V_{\text{prev}}$).
  - If an iteration path can leave $V$ unchanged (e.g., `continue` before incrementing an index or draining a queue), it is an infinite-loop bug.

---

## 2. Scientific Falsification & $\mathcal{O}(\log N)$ Bisection (`[refines Root Cause]`)

Isolate the fault before touching production code:

1. **Deterministic Reproduction**: Capture the exact inputs, environment, and sequence of events that trigger the failure. If a bug is intermittent, look for unseeded randomness, wall-clock races, or unordered map iteration.
2. **$\mathcal{O}(\log N)$ Binary-Search Bisection**:
   - Do not trace linearly through $N$ commits, $N$ pipeline stages, or an $N$-kilobyte input.
   - **Data/Input Bisection**: Halve the failing input repeatedly until you have the minimal reproducing input.
   - **Pipeline/Code Bisection**: Inspect the state at the midpoint of the call chain; determine whether the invariant $I$ is already violated before the midpoint or corrupted after it, halving the search space each step.
   - **History Bisection**: Use `git bisect` with an automated repro command to isolate the introducing commit in $\lceil \log_2 N \rceil$ steps.
3. **Explicit Hypothesis & Falsifiable Prediction**:
   - State: *"Root cause is X because invariant Y is violated when input Z occurs; therefore, inspecting/logging state at point P must show value V."*
   - Run the single probe that can falsify the hypothesis. If falsified, discard the hypothesis immediately and revert any exploratory edits.
4. **Fix Location**: Apply the fix at the point where the invariant was first violated, not where the downstream crash surfaced.

---

## 3. Control-Theoretic Observability (`[refines EC3]`)

A system is observable if its internal state transitions can be reconstructed solely from its boundary outputs:

- **Structured Boundary Context**: Every error or boundary log must carry the **operation name**, **causality/request ID**, **entity identifier**, and the **violated constraint or error cause** — never bare strings like `"invalid input"` or `"operation failed"`.
- **State-Transition Telemetry**: Emit metrics/events at state-machine transitions and queue boundaries (monotonic counters for total requests/errors by reason; histograms for latency and batch size).
- **No Log-and-Throw Duplication**: Either handle an error and log it once at the top-level boundary, or wrap it with context and return it up the stack — never both log and re-return the same error (which floods logs with $k$ duplicate entries per failure).

---

## 4. High-Signal Test Matrix Design (`[refines EC6, EC7]`)

Test behavior and invariants, not implementation details:

- **Boundary Value Analysis (BVA)**: Every numeric, collection, or time bound must be tested at:
  - Empty / zero ($0$, `[]`, `""`)
  - Singleton ($1$)
  - Exact boundary ($N_{\max} - 1$, $N_{\max}$, $N_{\max} + 1$)
  - Malformed / adversarial (negative, overflow, `NaN`, expired deadline/timeout)
- **Empirical Big-O & Operation-Count Verification**:
  - Unit tests with $N = 2$ cannot distinguish $\mathcal{O}(N)$ from $\mathcal{O}(N^2)$ or $\mathcal{O}(2^N)$.
  - Assert operation counts (e.g., comparator/query invocations $\le c \cdot N \log N$) or test scaling at $N = 1{,}000+$ so accidental quadratic regressions fail deterministically in CI.
- **Metamorphic & Invariant Assertions**:
  - **Round-Trip**: `decode(encode(x)) == x`
  - **Idempotency**: `apply(apply(s, msg), msg) == apply(s, msg)`
  - **Oracle Equivalence**: Compare a fast optimized algorithm against a simple reference implementation across edge cases.
- **Avoid Mock-Interaction Fragility**: Assert on the returned state and observable outputs of the unit, not the exact internal sequence of private helper calls.

---

## 5. Debugging & Verification Transformations (Do This, Not That)

| Default Anti-Pattern | First-Principles Transformation |
| :--- | :--- |
| Designing around "I remember API/tool X works like Y" | Treat memory as Boxed; run one probe command to make it Observed before coding |
| Tracing linearly through $N$ commits, stages, or input bytes to find a bug | Bisect in $\mathcal{O}(\log N)$ steps to the point where the invariant is first violated |
| Testing only happy-path $N=2$ inputs | Exercise BVA ($0, 1, N, N\pm 1$, empty, timeout) and empirical scaling at realistic $N$ |
