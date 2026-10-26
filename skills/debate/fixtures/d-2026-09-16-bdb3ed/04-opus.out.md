[opus] Running task in foreground...
ITEMS:
### T1: Increase protocol-error retries from one to three
This is a design change, to be applied before the first real use. Its cost is ASSUMED at two extra paid calls in the worst-case failure mode. It matters because Q4 questions whether real companions will hold the fixed turn formats. Given typical LLM formatting variations that I9 attempts to address, one retry is fragile; failing an entire debate late in the Converge phase over a markdown hiccup wastes all prior calls. Implementing T1 ensures robustness without unbounded looping.

### T2: Remove Claude as a peer participant
This is a design change, to be applied before the first real use. Its cost is ASSUMED to save approximately one third of the paid calls per debate. It matters because Q5 highlights the unacceptable concentration of influence when Claude drafts the brief, drives every phase mechanically, and argues as a peer. By restricting Claude to the loop driver role, T2 simplifies the procedure, reduces the cost per decision, and eliminates the conflict of interest where the driver could favor its own arguments, which builds on the automation goals of I8.

### T3: Lower the advocate exhaustion floor to one attack
This is a design change, to be applied before the first real use. Its cost is ASSUMED to be a small token saving per exhausted converge round. It matters because Q2 questions whether the mandatory three-entry ATTACKS TRIED floor generates useful objections or hollow theatre. If a participant genuinely has no new best case against the artifact, forcing three entries produces padded text that clutters the record. T3 lowers this floor to one, providing a clean concede path while still requiring a specific reason, complementing the measurement proposed in I5.

### T4: Delete the Verify phase entirely
This is a design change, to be evaluated after three runs. Its cost is ASSUMED to save two to four paid calls per debate by skipping the phase. It matters because Q3 asks if Verify is useful without command citations. Since most substantive engineering claims require execution, a file-and-line-only check merely verifies static text, downgrading the rest to ASSUMED. If early runs show Verify rarely changes the outcome, T4 removes the phase to improve the overall cost-benefit ratio for a solo developer, offering an alternative path to I3.
