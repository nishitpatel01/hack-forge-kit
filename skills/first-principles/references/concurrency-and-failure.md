# Concurrency, Determinism & Distributed Failure Modes
> **Refines**: `GEMINI.md` `[EC1]` (Bounded Execution), `[EC3]` (No Silent Errors), `[EC5]` (Minimal Scope) | **Load Trigger**: Threads/tasks, locks, async ordering, deterministic clocks, retries, or idempotency.

Operational rules for safe state sharing, deterministic execution, and resilience under partial failure.

---

## 1. Hierarchy of State Sharing (`[refines EC5]`)

Choose the highest level in this hierarchy that solves the problem; drop down only with measured proof:

1. **Immutable Values (Level 1 — Preferred)**: Data constructed once and shared freely across threads/tasks with zero locks and zero race conditions.
2. **Task Confinement & Message Passing (Level 2)**: Mutable state is owned by a single thread/actor/goroutine and mutated sequentially via a bounded input channel/queue (`share memory by communicating`).
3. **Explicit Coarse Locks / Atomics (Level 3 — Last Resort)**:
   - Document which mutex guards which exact fields.
   - Never hold a lock across blocking I/O, network calls, or unknown callbacks.
   - When acquiring multiple locks, enforce a strict global lock acquisition order to make deadlock mathematically impossible.

---

## 2. Determinism by Construction (`[refines EC5, EC6]`)

Non-deterministic code cannot be reliably reproduced or falsified in tests. Isolate every source of non-determinism behind an explicit seam:

- **Time**: Never call wall-clock `now()` or `sleep()` directly inside domain or coordination logic. Inject a `Clock` interface (`now()`, `after(d)`) so tests can advance virtual time deterministically.
- **Monotonic vs. Wall Clock**: Use monotonic clocks for measuring elapsed durations and timeouts (immune to NTP jumps/leap seconds); use UTC wall clocks only for human timestamps.
- **Randomness & UUIDs**: Pass an explicit seedable RNG or ID generator interface rather than calling global `rand()` or `uuidv4()`.
- **Map Iteration Order**: Never assume hash map iteration order is stable; sort keys explicitly before serializing, hashing, diffing, or presenting output.

---

## 3. Distributed Partial Failure & Idempotency (`[refines EC1, EC3]`)

In any networked or multi-process system, a timeout gives **zero information** about whether the remote side executed the request.

- **At-Least-Once + Idempotent Receiver**:
  - Assume every retry can deliver a duplicate message.
  - Attach a deterministic **Idempotency Key** (or client request ID) to every mutating operation so repeating the request $k$ times produces the exact same state as executing it once: $f(f(x)) = f(x)$.
- **Optimistic Concurrency (Compare-and-Swap)**:
  - Guard state updates with a version number or ETag (`UPDATE ... WHERE id = ? AND version = ?`). Return a conflict error on version mismatch instead of overwriting concurrent writes (lost-update anomaly).
- **Bounded Retries with Full Jitter**:
  - Retry only transient, idempotent failures (never `4xx` validation/auth errors).
  - Cap `maxAttempts` and `maxElapsedTime`, and apply exponential backoff with full jitter (`sleep = random(0, min(cap, base * 2^attempt))`) to prevent thundering-herd retry storms.
- **Compensating Actions & Clean Rollback**:
  - When a multi-step external workflow fails at step $k$, either stage work in a temporary/uncommitted location and atomically swap at the end (write-temp-then-rename), or execute explicit idempotent compensating actions for steps $1 \dots k-1$.

---

## 4. Concurrency & Failure Transformations (Do This, Not That)

| Default Anti-Pattern | First-Principles Transformation |
| :--- | :--- |
| Sharing mutable structs across threads with ad-hoc locks | Use immutable values or task-confined state via bounded queues |
| Calling `time.Now()` / `sleep()` directly in domain logic | Inject a deterministic `Clock` interface (`now()`, `after(d)`) |
| Retrying non-idempotent writes or retrying without jitter | Attach an idempotency key and cap retries with exponential backoff + full jitter |
