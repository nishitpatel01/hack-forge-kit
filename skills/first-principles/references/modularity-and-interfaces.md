# Modularity, Decomposition & Interfaces
> **Refines**: `GEMINI.md` `[EC2]` (Small Units, Flat Flow), `[EC4]` (Defensive Boundaries), `[EC5]` (Minimal Scope) | **Load Trigger**: Multi-file decomposition, module boundaries, API signatures, refactoring, or security boundaries.

Operational rules for decomposing problems, sequencing execution, designing module boundaries, and enforcing security hygiene at interfaces.

---

## 1. Topological DAG Decomposition & Sequential Execution (`[refines EC6]`)

Complex changes fail when edits are batched horizontally across many files before verification.

1. **Decompose into Atomic Nodes**: Split the task into minimal units where each unit has a single testable postcondition.
2. **Construct the Dependency DAG**: If unit $B$ depends on unit $A$, draw $A \prec B$. No cycles are permitted.
3. **Order Topologically (Risk-First & Leaf-to-Root)**:
   - **Risk-First**: If an external constraint, API contract, or algorithmic bound carries uncertainty, verify/prototype that leaf first before building dependent scaffolding.
   - **Bottom-Up Construction**:
     1. Domain types, schemas, and representation invariants (leaf nodes, zero dependencies).
     2. Pure state transitions, parsers, and algorithms (depend only on domain types).
     3. Adapters, storage/RPC clients, and CLI/UI/handler wiring (root nodes).
4. **Monotonic Step Gate**: Complete, compile, and pass tests for node $k$ before starting edits for node $k+1$.

---

## 2. Deep Modules Over Shallow Pass-Throughs (`[refines EC2]`)

Module value is the ratio of hidden capability to interface surface:
$$\text{Module Depth} = \text{Complexity Hidden Internally} - \text{Complexity of Public Interface}$$

- **Maximize Depth**: A good module exposes a small, simple signature (e.g., `read(fd, buf, n)`) while hiding substantial internal mechanism (buffering, retries, indexing, caching, platform quirks).
- **Eliminate Pass-Throughs**: A wrapper function or class that merely forwards its arguments to another layer with the same abstraction level adds interface surface without hiding complexity. Inline or collapse pass-through layers.
- **Pull Complexity Downward**: If a design choice is between making a module's internal implementation 20% harder or making every caller write boilerplate setup/error-recovery code, pull the complexity inside the module.

---

## 3. Information Hiding (One Secret per Module) (`[refines EC2, EC5]`)

Decompose modules around **design decisions that can change**, not around the chronological steps of a flowchart:

- **Identify the Secret**: Each module must encapsulate a single secret — a storage format, a wire protocol, a caching policy, a concurrency strategy, or a third-party SDK.
- **Prevent Representation Leakage**: Never expose internal backing collections (mutable slices/maps), database row structs, or wire protobufs across domain boundaries if callers don't need them. If the backing store or wire format changes, only the owning module should recompile.

---

## 4. Functional Core, Imperative Shell (FCIS) (`[refines EC2, EC5]`)

Mixing I/O with decision logic forces brittle mock-heavy tests and hides branching bugs:

```text
[Imperative Shell: Read I/O / Clock]
        │  (Immutable Value Inputs)
        ▼
[Functional Core: Pure Decision & State Transition]
        │  (New State + Declarative Effect Descriptions)
        ▼
[Imperative Shell: Execute Effects / Write I/O]
```

- **Functional Core**: Pure functions `(State, Event, Now) -> (NewState, []Command)`. Zero network calls, zero filesystem reads, zero `time.Now()`, zero global mutation. Test exhaustively with plain value assertions in milliseconds.
- **Imperative Shell**: Thin coordinator (~20–40 lines) that gathers inputs from the outside world, invokes the Functional Core, and executes the returned commands.

---

## 5. Least-Privilege Signatures & Code/Data Separation (`[refines EC4, EC5]`)

- **Minimal Parameter Scope**: If a function needs `userId` and `timeout`, pass `(userId: UserId, timeout: Duration)` — never pass the entire `AppServer`, `RequestContext`, or `User` god-object. Narrow signatures document exact data dependencies and prevent hidden coupling.
- **Strict Code vs. Data Separation (Zero Taint Crossing)**:
  - Untrusted input is **data**, never **control syntax**.
  - Never build SQL, shell commands, HTML/JS, regexes, or file paths by string concatenation or template interpolation.
  - Always use parameterized queries (`?` / `$1`), argument arrays (`execve` / `Command(bin, arg1, arg2)` without a shell), context-aware AST encoders, and canonical path confinement (`filepath.Clean` + prefix verification against base directory).

---

## 6. Modularity Transformations (Do This, Not That)

| Default Anti-Pattern | First-Principles Transformation |
| :--- | :--- |
| Inventing a new architecture when a working sibling exists in the repo | Check for a working sibling first; reuse its pattern unless it violates a bound |
| Modifying 5+ files across layers before running tests | Sequence into a topological DAG; build and verify one atomic step at a time |
| Mixing business rules with DB/HTTP calls in the same function | Extract pure transformation function `(State, Input) -> (State, Effects)` |
| Passing full `Config`/`User`/`Context` struct when 2 fields are needed | Pass only the minimal required values or narrow capability interface |
| Constructing SQL, shell, HTML, or paths via string concatenation | Use parameterized queries, structured argv arrays, or safe path joiners |
