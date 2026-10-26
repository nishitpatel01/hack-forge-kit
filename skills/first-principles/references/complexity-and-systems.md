# Complexity, Scalability & Systems
> **Refines**: `GEMINI.md` `[EC1]` (Bounded Execution), `[EC2]` (Small Units, Flat Flow) | **Load Trigger**: Big-O time/space complexity, data structure selection, memory layout, latency/throughput, or backpressure.

Operational rules for Big-O time/space complexity, data structure selection, hardware sympathy, scalability ceilings, and queueing/backpressure.

---

## 1. Big-O Time & Auxiliary Space Analysis (`[refines EC1]`)

Before implementing any loop, collection pipeline, or recursive traversal, determine its growth rate as input size $N \to \infty$:

$$\mathcal{O}(1) \prec \mathcal{O}(\log N) \prec \mathcal{O}(N) \prec \mathcal{O}(N \log N) \prec \mathcal{O}(N^2) \prec \mathcal{O}(2^N)$$

- **State Three Bounds Explicitly**:
  1. **Worst-Case Time $\mathcal{O}(\cdot)$**: Guarantees latency under adversarial or skewed inputs (e.g., hash collisions, unbalanced trees, pathological regexes).
  2. **Amortized Time $\mathcal{O}(\cdot)$**: Cost per operation averaged over a sequence of operations (e.g., dynamic array geometric growth $2\times$ gives $\mathcal{O}(1)$ amortized append; linear growth $+C$ gives $\mathcal{O}(N^2)$ total copy cost).
  3. **Auxiliary Space $\mathcal{O}(\cdot)$**: Peak working memory allocated beyond the input itself (including call-stack depth for recursion).

---

## 2. Data Structure Selection Matrix

Select data structures by matching the dominant operation mix to Big-O bounds and memory locality:

| Data Structure | Lookup / Search | Insert / Delete | Iteration Locality | Use When |
| :--- | :--- | :--- | :--- | :--- |
| **Contiguous Array / Slice** | $\mathcal{O}(1)$ index, $\mathcal{O}(\log N)$ sorted binary search | $\mathcal{O}(1)$ amortized tail, $\mathcal{O}(N)$ middle | Excellent (cache-line friendly) | Sequential scans, known/bounded size, batch sorting, small $N$ ($\le 64$) where linear scan beats hash overhead |
| **Hash Table (Map / Set)** | $\mathcal{O}(1)$ average, $\mathcal{O}(N)$ worst-case | $\mathcal{O}(1)$ average | Poor (pointer/bucket scatter) | Membership checks, deduplication, join indexing ($\mathcal{O}(N+M)$ instead of $\mathcal{O}(N \cdot M)$) |
| **Balanced BST / B-Tree** | $\mathcal{O}(\log N)$ | $\mathcal{O}(\log N)$ | Moderate (high in B-Tree) | Range queries (`[lo, hi]`), ordered iteration, predecessor/successor lookups |
| **Binary Heap (Priority Queue)** | $\mathcal{O}(1)$ min/max | $\mathcal{O}(\log N)$ | Good (array-backed) | Top-$K$ selection ($\mathcal{O}(N \log K)$), timers, Dijkstra/best-first scheduling |
| **Ring Buffer (Bounded Queue)** | $\mathcal{O}(1)$ head/tail | $\mathcal{O}(1)$ head/tail (zero alloc) | Excellent | Producer-consumer queues, sliding windows, bounded event logs |

---

## 3. Hidden $\mathcal{O}(N^2)$ & Superlinear Trap Audit (`[refines EC1]`)

Audit every loop for disguised linear work inside the body:

- **Linear Membership Scan in Loop**: Calling `list.contains(x)`, `array.find(...)`, or `slice.indexOf(...)` inside an $\mathcal{O}(N)$ loop yields $\mathcal{O}(N^2)$. **Fix**: Pre-build a hash set/map in $\mathcal{O}(N)$ outside the loop for $\mathcal{O}(1)$ lookups.
- **Repeated String / Slice Concatenation**: `str = str + chunk` or `arr = [...arr, item]` inside a loop reallocates and copies $1 + 2 + \dots + N = \mathcal{O}(N^2)$ bytes. **Fix**: Pre-allocate a builder/buffer with capacity $\sum \lvert\text{chunk}\rvert$.
- **$N+1$ I/O Queries / RPCs**: Issuing a DB query or network call per item in a collection of size $N$. **Fix**: Collect keys and execute 1 batched query (`WHERE id IN (...)` or batch RPC).
- **Unbounded Regex Backtracking (ReDoS)**: Nested quantifiers like `(a+)+$` take $\mathcal{O}(2^N)$ on non-matching input. **Fix**: Use linear-time $\mathcal{O}(N)$ engines (RE2) or explicit token parsers.

---

## 4. Scalability Ceilings: Amdahl's Law & Universal Scalability Law (USL)

Adding concurrency $p$ does not scale linearly. Throughput relative to 1 worker follows the Universal Scalability Law:
$$S(p) = \frac{p}{1 + \alpha(p - 1) + \beta \, p(p - 1)}$$

- **Serial Fraction & Contention ($\alpha$)**: Waiting on a shared mutex, single connection, or sequential bottleneck caps maximum speedup at $1/\alpha$ (Amdahl ceiling).
- **Coherency / Coordination Penalty ($\beta$)**: Point-to-point state synchronization, cache-line ping-ponging, or distributed consensus grows quadratically as $\mathcal{O}(p^2)$. When $\beta > 0$, throughput **retrogrades (decreases)** once $p > \sqrt{(1 - \alpha)/\beta}$.
- **Design Rule**: Partition state so workers operate on disjoint shards ($\alpha \to 0, \beta \to 0$) and merge results once at the end.

---

## 5. Mechanical Sympathy, Little's Law & Backpressure (`[refines EC1]`)

- **Hardware Latency Hierarchy**:
  - L1/L2 Cache: $\sim 1\text{–}4\text{ ns}$
  - Main RAM: $\sim 50\text{–}100\text{ ns}$
  - Local SSD / Syscall: $\sim 10\text{–}100\ \mu\text{s}$
  - Intra-Datacenter RPC: $\sim 0.5\text{–}5\text{ ms}$
  - Cross-Region WAN: $\sim 50\text{–}150\text{ ms}$
- **Little's Law ($L = \lambda W$)**: Concurrent in-flight items ($L$) equals arrival rate ($\lambda$) times mean latency ($W$). If downstream latency $W$ spikes $10\times$ and $\lambda$ is unchanged, in-flight memory $L$ grows $10\times$ until OOM unless bounded.
- **Backpressure & Load Shedding**:
  - Every queue and worker pool must have a fixed capacity bound $L_{\max}$.
  - Propagate caller deadlines/timeouts across every hop; abort work immediately if the caller's deadline has already expired.

---

## 6. Complexity Transformations (Do This, Not That)

| Default Anti-Pattern | First-Principles Transformation |
| :--- | :--- |
| $\mathcal{O}(N)$ scan (`find`/`includes`) inside an $\mathcal{O}(N)$ loop | Pre-index into an $\mathcal{O}(1)$ hash set/map or sort once in $\mathcal{O}(N \log N)$ |
| Sequential RPC/DB call inside a loop ($N+1$ query problem) | Batch into a single bulk query/RPC with bounded chunk size |
| Unbounded in-memory channel or worker queue | Enforce fixed capacity $L_{\max}$ with explicit backpressure or load shedding |
