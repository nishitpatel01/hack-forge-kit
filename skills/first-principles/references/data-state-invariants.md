# Data, State & Invariants
> **Refines**: `GEMINI.md` `[EC4]` (Defensive Boundaries), `[EC5]` (Minimal Scope) | **Load Trigger**: Domain types, schemas, state machines (FSMs), caching, or wire compatibility.

Operational rules for domain modeling, state transitions, normalization, and schema evolution.

---

## 1. Parse, Don't Validate (`[refines EC4]`)

A validation function `(raw: string) -> bool` checks a property and throws away the proof, forcing every downstream caller to either trust blindly or re-validate. A parsing function `(raw: string) -> Result<DomainType, Error>` preserves the proof in the type system.

- **Ingress Parsing**: Parse raw primitives (`string`, `int`, untyped JSON/maps, CLI flags) at the outermost system boundary into constrained domain types (`NonEmptyString`, `Port`, `UserId`, `PositiveAmount`, `ValidatedConfig`).
- **Canonical Normal Form**: Canonicalize during construction (e.g., trim whitespace, lowercase case-insensitive identifiers, sort set elements, normalize paths, convert timestamps to UTC). Never store un-normalized representations inside domain structs.
- **Interior Trust (`[refines EC4]`)**: Functions inside the domain boundary accept parsed domain types and do not repeat raw format *parsing*; interior units still enforce the floor's ≥2 checks per unit via precondition/invariant assertions on validated domain types.

---

## 2. Make Illegal States Unrepresentable (`[refines EC5]`)

If a state struct has $k$ boolean flags, it defines $2^k$ possible combinations, most of which are invalid and untested.

- **Replace Product Types with Sum Types**:
  - **Bad (Product of Nullables/Booleans)**:
    `{ status: string, data: T | null, error: Error | null, retryCount: int }`
    *(Allows `status == "success"` with `data == null` and `error != null`.)*
  - **Good (Tagged Union / Disjoint Variants)**:
    `Idle | InFlight(attempt: PositiveInt) | Succeeded(data: T) | Failed(err: Error, retriesExhausted: bool)`
- **Cardinality Matching**: Ensure the cardinality of the type $\lvert \text{Type} \rvert$ matches the cardinality of valid domain states $\lvert \text{Domain} \rvert$:
  - A collection that must have at least one item uses `NonEmptyList<T>` (`{ head: T, tail: List<T> }`), eliminating empty-list branches in every consumer.
  - Mutually exclusive configuration options use a tagged union, never two optional fields with a runtime check.

---

## 3. Explicit Finite State Machines (FSMs)

Whenever an entity has a lifecycle (jobs, connections, orders, workflows, UI states), model it as an explicit Finite State Machine $(S, E, \delta)$:

1. **Enumerate States ($S$) and Events ($E$)**: Define closed sets of states and events.
2. **Pure Transition Function**: Implement $\delta: (S, E) \to \text{Result}<(S', \text{List}<\text{Effect}>), \text{TransitionError}>$.
3. **Exhaustive Matching**: Handle every $(S, E)$ pair explicitly. Out-of-order, duplicate, or terminal-state transitions return an explicit `TransitionError`.
4. **Monotonicity**: Identify terminal states ($S_{\text{terminal}} \subset S$) and assert that no transition exits a terminal state.

---

## 4. Single Source of Truth (SSOT) & Normalization

Every piece of duplicated state is a consistency bug waiting to happen:

- **Canonical Storage**: Store each authoritative fact in exactly one place.
- **Compute vs. Cache**: Prefer computing derived properties on read when cost is $\mathcal{O}(1)$ or small $\mathcal{O}(N)$.
- **Cache Invalidation Contract**: If a derived value *must* be materialized/cached for performance, document:
  1. The exact canonical source fields it depends on.
  2. The atomic write path or invalidation hook that updates/evicts it whenever any source field mutates.
  3. An invariant check in tests asserting `cachedValue == computeFromCanonical(state)`.

---

## 5. Additive Schema Evolution

Persisted data and wire protocols outlive individual binaries:

- **Additive Changes Only**: Add new optional fields with safe zero/default semantics; never change the type or semantic meaning of an existing field or reuse a deleted field's tag/name.
- **Two-Phase Deprecation**: To rename or remove a field:
  1. Dual-write (or support reading both old and new fields).
  2. Migrate all readers to the new field.
  3. Stop writing the old field and reserve its identifier/tag.
- **Unknown Field Preservation**: Readers should tolerate unknown fields from newer writers without crashing; read-modify-write pipelines must not silently drop unrecognized fields unless explicitly filtering.

---

## 6. Domain Transformations (Do This, Not That)

| Default Anti-Pattern | First-Principles Transformation |
| :--- | :--- |
| Multiple boolean flags (`isLoading`, `isError`, `hasData`) | Explicit state sum type (`Idle \| Loading \| Ready(T) \| Failed(Err)`) |
| Re-parsing raw strings/maps in every helper function | Parse once at the ingress boundary into a constrained domain type |
| Storing derived/cached fields without an invalidation hook | Compute on read, or declare canonical source fields + atomic invalidation trigger |
