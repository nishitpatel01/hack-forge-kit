[opus] Running task in foreground...
MISSING QUESTIONS:
Q7: Since participants cannot run commands like `grep -n`, how can they reliably cite exact line numbers in the Verify phase without guessing and failing the strict mechanical check?
Q8: Will Claude, acting as the manual loop driver following a 116-line `SKILL.md` procedure, drift or make state-management errors over the course of a typical 12-call loop?
Q9: Are the exact-match regexes in `debate_exit.py` too brittle for typical LLM formatting variations (like bolding or trailing whitespace), risking excessive protocol errors?

CRITERIA:
Robustness should be ranked above Cost per decision (moving it from #3 to #2). If protocol formatting errors or loop driver drift wedge the debate, the cost per decision becomes infinite and the skill fails its primary purpose.

SEED ITEMS:
### T1: Remove line numbers from Verify citations
LLMs cannot reliably predict exact file line numbers from memory, and the participants have no access to `grep -n` during a round. The mechanical check in `debate_cite.py` strictly requires the snippet to appear exactly on the cited line. Consequently, almost all citations will fail and be downgraded to `ASSUMED`, making the Verify phase an expensive theater. The citation format should be simplified to `<path> "<snippet>"` and the verification script should simply search the file contents for the snippet.

### T2: Replace `SKILL.md` with a Python loop driver
Currently, Claude acts as a manual driver: reading `SKILL.md`, tracking state, rewriting artifacts, running Python scripts, and prompting companions. LLMs are notoriously poor at strictly executing long, repetitive procedural loops and will inevitably drift. Moving the loop orchestration into a single Python script will dramatically improve robustness, reduce token costs, and allow Claude to act solely as a peer participant.

### T3: Relax strict regex anchors in parsers
The parsers in `debate_exit.py` rely on extremely strict regex anchors (e.g., `^STANCE: (ACCEPT|OBJECT)$` which fails if there is trailing whitespace or markdown formatting like `**STANCE:**`). LLMs frequently introduce such minor formatting variations. These strict anchors will trigger constant `PROTOCOL-ERROR` retries. Relaxing the regexes to tolerate markdown artifacts and whitespace will save calls and prevent wedged dispatches.

QUESTIONS FOR HUMAN:
- Do you prefer the debate to fail fast on protocol errors (saving paid calls) or retry aggressively (saving your wall-clock time)?
