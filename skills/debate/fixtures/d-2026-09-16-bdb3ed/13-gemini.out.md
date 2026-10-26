[gemini] Starting Gemini task thread.
[gemini] Thread ready (01a0aacb-4200-70a2-9df1-821af10476c2).
[gemini] Turn started (01a0aacb-468c-7ea2-a139-39f1f57753f1).
[gemini] Assistant message captured: I’ll pin my checks to `c39b508` for the line citations and then revise only the disputed piec...
[gemini] Running command: /bin/zsh -lc "git show c39b508:skills/debate/scripts/debate_cite.py | nl -ba | sed -n '40,55p'"
[gemini] Running command: /bin/zsh -lc "git show c39b508:skills/debate/SKILL.md | nl -ba | sed -n '14,40p;108,116p'"
[gemini] Running command: /bin/zsh -lc "git show c39b508:skills/debate/scripts/debate_exit.py | nl -ba | sed -n '60,125p'"
[gemini] Command completed: /bin/zsh -lc "git show c39b508:skills/debate/scripts/debate_cite.py | nl -ba | sed -n '40,55p'" (exit 0)
[gemini] Command completed: /bin/zsh -lc "git show c39b508:skills/debate/SKILL.md | nl -ba | sed -n '14,40p;108,116p'" (exit 0)
[gemini] Command completed: /bin/zsh -lc "git show c39b508:skills/debate/scripts/debate_exit.py | nl -ba | sed -n '60,125p'" (exit 0)
[gemini] Running command: /bin/zsh -lc "git show c39b508:docs/superpowers/specs/2026-09-15-debate-skill-design.md | nl ...
[gemini] Command completed: /bin/zsh -lc "git show c39b508:docs/superpowers/specs/2026-09-15-debate-skill-design.md | nl ... (exit 0)
[gemini] Assistant message captured: ARTIFACT: ## Directions ### I1: Pin the repo and add a preflight/smoke-test gate (gemini) Rank...
[gemini] Turn completed.
ARTIFACT:
## Directions
### I1: Pin the repo and add a preflight/smoke-test gate (gemini)
Rank 1. ADOPT before first real use (design). This outranks dispatch-length fixes because Verify only has evidentiary value if citations are checked against the same tree all participants read. The shipped state records a repo path, not a commit, and the citation checker reads live files at check time.
Before first real use, require a preflight that creates or selects a detached worktree pinned to the debate commit, records that commit in state, runs the three script selftests, checks both companions are installed, and verifies writable debate state and log paths. The first live run is otherwise also the first integration test and can discover missing companions, signature drift, or moving-line citation failures in the middle of paid deliberation.
FACTS:
- VERIFIED(skills/debate/SKILL.md:17 "`\"repo\": \"<git toplevel>\"`") shipped state records a repo path, not a pinned commit.
- VERIFIED(skills/debate/scripts/debate_cite.py:45 "lines = p.read_text(errors=\"replace\").splitlines()") citation checking reads live file contents.
- VERIFIED(skills/debate/scripts/debate_cite.py:49 "if m.group(\"snippet\") not in lines[n - 1]:") line validation is against the current file line at check time.
- VERIFIED(skills/debate/scripts/debate_exit.py:166 "ok, why = check_citation(f[\"citation\"], st.get(\"repo\", \".\"))") Verify delegates to the live-tree citation checker.
- VERIFIED(skills/debate/scripts/selftest.sh:2 "Runs every debate script selftest.") a script exists to run debate script selftests.
- VERIFIED(skills/debate/scripts/selftest.sh:5 "for s in debate_cite.py debate_exit.py debate_round.py; do") the selftest runner covers the three debate scripts.
- VERIFIED(skills/debate/scripts/debate_round.py:29 "if not paths:") companion lookup detects when no matching companion path exists.
- VERIFIED(skills/debate/SKILL.md:16 "mkdir -p $D/calls") setup creates debate state and call paths.
- ASSUMED a moving worktree can falsely downgrade or falsely validate repository claims.
- ASSUMED a preflight plus pinned detached worktree costs one short script and one SKILL.md step.

### I13: Pass opus prompts by file, not argv
Rank 2. ADOPT before first real use (bug). On this run the largest prompt was 132 lines and passed, but converge prompts carry the artifact plus a transcript slice and are the riskiest prompt shape.
Bug fix, before first real use: change `debate_round.py` so opus receives the prompt through a file-based interface or a temporary stdin/file shim instead of `Path(prompt_file).read_text()` as a command argument. Cost: one dispatch-path change. I13 matters because debate prompts include the artifact and transcript slices, so long runs can hit shell or OS argument limits before the debate reaches the reasoning it was created to improve.
FACTS:
- VERIFIED(skills/debate/scripts/debate_round.py:37 "Path(prompt_file).read_text()") opus receives the prompt text as a command argument.
- VERIFIED(skills/debate/SKILL.md:57 "> --- ARTIFACT --- <artifact.md>") Verify prompts include the artifact.
- VERIFIED(skills/debate/SKILL.md:91 "> --- LAST TWO ROUNDS --- <transcript slice>") converge prompts include transcript slices.
- ASSUMED Verify prompts include the brief.
- ASSUMED long runs can hit shell or OS argument limits.
- ASSUMED changing the dispatch path is small.
- ASSUMED cost is one dispatch-path change.

### I23: Fix verify phase crash on duplicate citations
Rank 3. ADOPT before first real use (bug), narrowed to the duplicate case. The exit script should dedupe identical fact lines instead of asserting. Trailing whitespace does not trip it; a duplicate line does, and it bypasses the protocol-error path.
Bug fix, before first real use: change the `DOWNGRADED` rewrite string replacement in `verify_verdict` to avoid an `assert` crash. Cost: one line of robust replacement logic. I23 matters because this bug raises an uncaught Python exception that bypasses the protocol-error retry mechanism, halting the autonomous skill loop entirely and forcing manual recovery.
FACTS:
- VERIFIED(skills/debate/scripts/debate_exit.py:59 "claim=f.group(3).strip()") parse_artifact strips fact claim text.
- VERIFIED(skills/debate/scripts/debate_exit.py:168 "old = \"- VERIFIED(%s) %s\"") verify_verdict reconstructs the old line from citation and stripped claim.
- VERIFIED(skills/debate/scripts/debate_exit.py:170 "assert text.count(old) == 1, old") verify_verdict asserts exactly one reconstructed old-line match.
- VERIFIED(skills/debate/SKILL.md:96 "PROTOCOL-ERROR <p> <reason>") the retry path is for protocol-error verdicts.
- ASSUMED exact duplicate citation lines can make the count greater than one.
- ASSUMED trailing whitespace alone does not produce count zero because the reconstructed old string is still a substring of the original line.
- ASSUMED one line of robust replacement logic fixes it.

### I24: Allow multi-line reviser answers to preserve citation checks
Rank 4. ADOPT before first real use (bug). A multi-paragraph answer is the natural shape; truncating it wrongly leaves objections open and burns converge rounds.
Bug fix, before first real use: update the answers regex in the converge phase to capture multi-line responses. The regex assumes answers fit on exactly one line; if an LLM naturally formats its response across paragraphs or puts a citation on a second line, the parser truncates the text. Cost: a small regex update to capture until the next objection or section. I24 matters because truncated answers will silently fail the mechanical citation check, causing the script to wrongly mark objections as unanswered.
FACTS:
- VERIFIED(skills/debate/scripts/debate_exit.py:193 "ANS = re.compile(r\"^(O\\d+): (.+)$\", re.M)") answers are captured one line at a time.
- VERIFIED(skills/debate/scripts/debate_exit.py:245 "answers = dict(ANS.findall(ans_text))") converge stores regex-captured answers.
- VERIFIED(skills/debate/scripts/debate_exit.py:259 "cited = any((\"VERIFIED(%s)\" % f[\"citation\"]) in text") answer text is searched for a VERIFIED citation string.
- ASSUMED LLMs will naturally format answers across paragraphs.
- ASSUMED a small regex update can safely capture multi-line answers.
- ASSUMED truncation will burn calls until the cap.

### I25: Anchor the block extraction to prevent artifact state corruption
Rank 5. ADOPT before first real use (bug). Anchor the `ITEMS:` split to a line start.
Bug fix, before first real use: harden the extraction of the items block in `assign_ids` to require a strictly anchored line. The script splits on the first literal occurrence of the string, so any conversational preamble containing that word causes the remainder of that preamble to be permanently appended into the shared artifact text. Cost: a minor parsing adjustment. I25 matters because accumulating conversational junk over multiple diverge rounds pollutes the shared state, confusing the participants and degrading decision quality.
FACTS:
- VERIFIED(skills/debate/scripts/debate_exit.py:114 "block = turn_body.split(\"ITEMS:\", 1)[1] if \"ITEMS:\" in turn_body else turn_body") assign_ids splits on the first literal ITEMS marker.
- VERIFIED(skills/debate/scripts/debate_exit.py:124 "tail = tail.rstrip(\"\\n\") + \"\\n\" + block.strip(\"\\n\") + \"\\n\"") the extracted block is appended into the artifact.
- ASSUMED conversational preamble containing ITEMS: can be permanently appended into shared artifact text.
- ASSUMED anchoring the extraction is a minor parsing adjustment.

### I28: Permit item deletion without reusing stable IDs
Rank 6. ADOPT before first real use (design). Legal id gaps need max-id-plus-one allocation, not count-based allocation.
Design change, before first real use: relax the structure validation check to allow gaps in the item ID sequence, and change alias allocation from `len(items) + 1` to max existing numeric id plus one. Add a smoke test for deleting an item, preserving the gap, and then adding a new item without reusing the deleted id. I28 matters because the current rigidity wedges the debate if participants try to remove an option entirely, but a gap-legal parser must still preserve stable IDs for objections and verification.
FACTS:
- VERIFIED(skills/debate/scripts/debate_exit.py:68 "if ids != list(range(1, len(ids) + 1)):") structure validation requires contiguous item IDs.
- VERIFIED(docs/superpowers/specs/2026-09-15-debate-skill-design.md:34 "Items are numbered `I1, I2, ...` and keep their ids") the design requires stable item ids.
- VERIFIED(skills/debate/scripts/debate_exit.py:101 "next_k = len(items) + 1") new IDs are allocated from the item count.
- VERIFIED(skills/debate/scripts/debate_exit.py:106 "mapping[m.group(1)] = \"I%d\" % next_k") alias assignment uses that computed next ID.
- ASSUMED if gaps become legal and an item is deleted, count-based allocation can reuse an existing later ID.
- ASSUMED the cost is a small parser change plus a gap-preserving deletion/allocation test.
- ASSUMED current rigidity wedges debate when participants remove an option.

### I6: Move brief and merge power into auditable mechanical checks
Rank 7. ADOPT before first real use (design). Kept distinct from I4 because Q5 is about influence, not only log observability.
Before first real use, add a driver-audit step for the two highest-leverage Claude controls: record the brief diff after frame, and require the frame merge to list each companion critique as ADOPTED or REJECTED with a cited or ASSUMED reason. This does not remove Claude from the loop, but it moves brief-shaping and critique-merge power onto the record where participants and the later retro can inspect it.
FACTS:
- VERIFIED(docs/superpowers/specs/2026-09-15-debate-skill-design.md:23 "Claude is a peer.") Claude argues as a peer.
- VERIFIED(docs/superpowers/specs/2026-09-15-debate-skill-design.md:23 "drives the loop.") Claude drives the loop.
- VERIFIED(skills/debate/SKILL.md:23 "Draft `$D/brief.md`") Claude drafts the brief.
- VERIFIED(skills/debate/SKILL.md:37 "Merge: for every critique write") Claude merges frame critiques.
- VERIFIED(docs/superpowers/specs/2026-09-15-debate-skill-design.md:24 "Every phase exit and the converged/stuck call are decided by a script") mechanical exits partly constrain Claude.
- VERIFIED(skills/debate/SKILL.md:115 "Every number comes from the transcript and `state.json`") log computation is a separate driver duty.
- ASSUMED automating metrics alone does not meaningfully constrain brief-shaping or critique-merge power.
- ASSUMED a brief diff plus explicit critique ledger is the smallest useful audit of Claude's influence.

### I4: Automate debate.log metrics and verify FACTS merging, or cut the schema (gemini)
Rank 8. ADOPT before first real use (design). The driver wrote an ad-hoc merge script mid-run because merging 3 x 28 fact blocks by hand is not credible; that script is the seed of this item.
Before first real use, add a small stdlib reporter that computes the record's fields from the transcript and state, and a merger that applies Verify results to FACTS blocks, or cut the schema to what the driver can compute reliably. The procedure demands many derived numbers in one JSON line and no shipped script computes them.
FACTS:
- VERIFIED(skills/debate/SKILL.md:109 "Append one line to `<repo>/.claude/state/debate.log`:") the procedure requires a debate.log record.
- VERIFIED(skills/debate/SKILL.md:112 "\"items_added\": 6") the record schema includes derived numeric fields.
- VERIFIED(skills/debate/SKILL.md:115 "Every number comes from the transcript and `state.json`") the driver must compute numbers from transcript and state.
- ASSUMED no script currently computes all debate.log fields.
- ASSUMED adding a stdlib reporter is small.
- ASSUMED merging verify FACTS blocks by hand is not credible.

### I14: Define non-closing CAP, FAIL and TIMEOUT recovery
Rank 9. ADOPT before first real use (doc). One paragraph in SKILL.md: TIMEOUT or FAIL on an ordinary call means one re-dispatch, then the participant skips that round with a transcript note.
Bug fix, before first real use: add explicit driver behavior for `CAP`, `FAIL`, and `TIMEOUT` from ordinary companion calls, not only closing statements. I14 matters because a wedged or failed companion is a robustness outcome, not a debate position, and the first real use should leave a coherent record instead of requiring improvised hand recovery.
FACTS:
- VERIFIED(skills/debate/scripts/debate_round.py:65 "print(\"TIMEOUT %s\" % out_path)") debate_round can emit TIMEOUT.
- VERIFIED(skills/debate/scripts/debate_round.py:72 "print(\"FAIL %d %s\" % (rc, out_path))") debate_round can emit FAIL.
- VERIFIED(skills/debate/scripts/debate_round.py:48 "print(\"CAP %d\" % st[\"cap\"])") debate_round can emit CAP.
- VERIFIED(skills/debate/SKILL.md:101 "## 6. Closing statements (STUCK only, 3 calls)") CAP handling is documented for closing statements.
- VERIFIED(skills/debate/SKILL.md:103 "If `debate_round.py` prints `CAP`") closing CAP behavior is defined.
- ASSUMED ordinary companion CAP, FAIL and TIMEOUT recovery is undefined.
- ASSUMED the cost is a small driver-text change plus one transcript/report convention.

### I2: Make the one-call alternative an explicit eligibility gate (gemini)
Rank 10. ADOPT before first real use (doc). One paragraph; the cost is already disclosed.
Before first real use, add a short "use debate only when" gate to the driver: multi-option architectural choices, irreversible scope decisions, or ambiguous tradeoffs with real disagreement; otherwise use one adversarial review. The cost is disclosed today but not justified by task shape.
FACTS:
- VERIFIED(skills/debate/SKILL.md:12 "A typical decision runs 8 to 12.") the driver discloses typical paid companion calls.
- VERIFIED(skills/debate/SKILL.md:3 "Do NOT use for a one-shot review") the shipped skill distinguishes debate from one-shot review.
- VERIFIED(skills/debate/SKILL.md:3 "wants an ambiguous task resolved before building") the shipped trigger is broad ambiguous-task use.
- ASSUMED the one-call baseline costs exactly one paid call in this repo.
- ASSUMED the cost of adding the eligibility gate is small.

### I3: Keep file-and-line Verify but narrow its promise (gemini)
Rank 11. ADOPT before first real use (doc). Wording only.
Before first real use, change the Verify wording from "verified factual claims" to "source-cited repository claims", and treat runtime-only claims as known ASSUMED evidence rather than failed verification. The checker accepts only the file-and-line form, so Verify is useful but narrower than many engineering claims.
FACTS:
- VERIFIED(skills/debate/scripts/debate_cite.py:4 "The only form:") the checker documents one accepted citation form.
- VERIFIED(skills/debate/scripts/debate_cite.py:5 "<path>:<line> \"<snippet>\"") the accepted citation form is file, line and snippet.
- VERIFIED(skills/debate/scripts/debate_cite.py:11 "is refused and the caller downgrades the fact to ASSUMED.") non-file-line citations are refused and downgraded.
- VERIFIED(docs/superpowers/specs/2026-09-15-debate-skill-design.md:112 "There is no command citation form") the deletion is recorded in the spec.
- VERIFIED(skills/debate/SKILL.md:55 "There is no command form") Verify prompt disallows command citations.
- VERIFIED(skills/debate/SKILL.md:55 "a fact you can only show by running something is ASSUMED") runtime-only facts are treated as ASSUMED.
- ASSUMED the cost of narrowing the wording is small.

### I26: Strip punctuation from dispute lists to prevent needless retries
Rank 12. ADOPT before first real use (bug, minor). The structure check already turns a comma-joined list into a protocol error, so this costs a retry rather than a silent skip; stripping commas removes a needless retry.
Bug fix, before first real use: update the dispute parser in the verify phase to strip commas from item IDs. The parser uses a naive whitespace split, so if a participant formats its list naturally with commas, the first element retains the comma and fails to match the clean dictionary key. Cost: one line of string cleaning.
FACTS:
- VERIFIED(skills/debate/scripts/debate_exit.py:185 "disputed.update(m.group(1).split())") verify_verdict splits dispute ids on whitespace only.
- VERIFIED(skills/debate/scripts/debate_exit.py:186 "for k in disputed:") verify_verdict iterates those raw tokens.
- VERIFIED(skills/debate/scripts/debate_exit.py:187 "if k in items") verify continuation only considers tokens that exactly match item ids.
- VERIFIED(skills/debate/scripts/debate_exit.py:87 "for k in (m.group(1).split()") validate_structure also splits dispute ids on whitespace.
- VERIFIED(skills/debate/scripts/debate_exit.py:89 "errors.append(\"%s R%d disputes missing %s\"") validate_structure flags dispute tokens that are not item ids.
- ASSUMED comma-punctuated dispute ids trigger a protocol error instead of being silently ignored.
- ASSUMED one line of string cleaning is sufficient.

### I17: Add a minimal post-run scoring rubric to the report
Rank 13. ADOPT after the first run (design). Three human-filled outcome fields are the only way Q1 ever gets answered.
Design change, after the first completed run: add three human-filled fields to `report.md` or `debate.log`: whether the final artifact changed the intended build, whether the change was kept after implementation, and whether the debate was worth the calls versus one review. Cost: manual one-minute annotation after each run. I17 matters because Q1 cannot be answered from transcript mechanics alone, and the solo developer needs outcome data, not just protocol data.
FACTS:
- VERIFIED(skills/debate/SKILL.md:107 "Write `$D/report.md`:") the procedure writes a report.
- VERIFIED(skills/debate/SKILL.md:109 "Append one line to `<repo>/.claude/state/debate.log`:") the procedure appends a log line.
- VERIFIED(docs/superpowers/specs/2026-09-15-debate-skill-design.md:247 "section itself is a follow-up") the retro section is deferred.
- ASSUMED three human-filled outcome fields cost one minute per run.
- ASSUMED Q1 cannot be answered from transcript mechanics alone.

### I5: Keep the devil's advocate until three runs have measured it (gemini)
Rank 14. ADOPT: keep the advocate and measure it over three runs. This run is the first data point and it is not yet in.
After three runs, judge whether mandatory advocate objections changed artifacts often enough to keep. The three-entry floor is a work-cost floor, not a semantic guarantee, so the first change is to log advocate impact, not to delete the role before evidence exists.
FACTS:
- VERIFIED(docs/superpowers/specs/2026-09-15-debate-skill-design.md:150 "One non-reviser per round is the designated devil's advocate") the design has a mandatory devil's advocate.
- VERIFIED(docs/superpowers/specs/2026-09-15-debate-skill-design.md:154 "`ATTACKS TRIED:` list of at least three entries") exhausted advocate requires at least three attacks tried.
- VERIFIED(docs/superpowers/specs/2026-09-15-debate-skill-design.md:157 "That is a work-cost floor, not a semantic guarantee") the spec says the floor is not a semantic guarantee.
- VERIFIED(skills/debate/SKILL.md:112 "\"advocate_changed_artifact\": true") the log schema includes advocate impact.
- ASSUMED three runs are enough evidence to judge the role.

### I11: Bug: frame turns rejected as unresolved aliases (fixed mid-run)
Rank 15. DONE during this run (bug), landed after c39b508. Kept for the record.
Bug fix, already landed during this debate: validate_structure treated the T-blocks that the frame prompt itself asks for as unresolved aliases and refused all three frame turns. It cost one hand recovery and no paid calls, and it is the first data point for Q8: the driver drifted zero times but the scripts had a gap on their first real input.
FACTS:
- VERIFIED(skills/debate/scripts/debate_exit.py:79 "frame turns carry seed items as T-blocks") validate_structure now recognizes frame T-blocks.
- VERIFIED(skills/debate/scripts/debate_exit.py:81 "t[\"phase\"] not in (\"diverge\", \"frame\")") frame turns are exempt from unresolved alias errors.
- VERIFIED(skills/debate/scripts/debate_exit.py:82 "has unresolved alias") the alias check now exempts frame turns after the mid-run fix.
- VERIFIED(skills/debate/scripts/debate_exit.py:359 "frame_alias = parse_transcript") there is a selftest for frame aliases.
- ASSUMED the fix landed during this debate.
- ASSUMED it cost one hand recovery and no paid calls.
- ASSUMED the driver drifted zero times.

### I9: Relax the parser's strict regex anchors (opus)
Rank 16. DEFER until three runs (design). Ten companion calls this run produced zero format errors from either companion; every protocol error came from scripts, the driver, or moving-tree evidence. Relaxing anchors before a measured need trades a proven parser for a guess.
Make the turn-format regexes tolerate trailing whitespace and markdown artifacts such as bold markers around a field name if future runs show ordinary formatting variation causes paid retries or wedged rounds.
FACTS:
- VERIFIED(skills/debate/scripts/debate_exit.py:192 "OBJ = re.compile") objection parsing uses regexes.
- VERIFIED(skills/debate/scripts/debate_exit.py:196 "STANCE = re.compile(r\"^STANCE: (ACCEPT|OBJECT)$\", re.M)") STANCE currently requires an exact anchored line.
- VERIFIED(skills/debate/SKILL.md:96 "re-dispatch `<p>` once") converge protocol errors get one retry.
- ASSUMED ordinary LLM markdown or whitespace variation will dominate retries.
- ASSUMED relaxing parser anchors is cheap.
- ASSUMED this run produced zero companion format errors.

### I22: Add fact tags to Converge objections
Rank 17. DEFER until three runs (design). Useful for the retro, but no converge round has yet shown objections resting on false premises after the existing prompt rule.
Design change, after measurement: extend each Converge objection line with the same `VERIFIED(...)` or `ASSUMED` fact tags used in Verify, or add a short `FACTS:` sub-block per objection. I22 matters because Converge is where new evidence-shaped claims most affect the final artifact, so making objection premises visible preserves decision quality and gives the later report useful data.
FACTS:
- VERIFIED(skills/debate/SKILL.md:75 "Never introduce a fact without a VERIFIED tag") Converge prompt forbids new facts without VERIFIED tags.
- VERIFIED(skills/debate/scripts/debate_exit.py:73 "ref = re.compile(r\"\\((I\\d+)\\)\")") structure validation finds item references in parentheses.
- VERIFIED(skills/debate/scripts/debate_exit.py:192 "OBJ = re.compile") objection parsing does not include a FACTS sub-block.
- ASSUMED adding fact tags to Converge objections is a small prompt and parser change.
- ASSUMED Converge is where new evidence-shaped claims most affect the final artifact.

### I15: Add machine-readable protocol examples to every prompt
Rank 18. DEFER until three runs (design). Same evidence as I9: no companion format errors yet.
Design change, after measurement: include a tiny valid example block for each phase prompt, especially Verify and Converge, using the exact field order the parser expects. I15 matters only if examples reduce paid retries for companions without weakening mechanical exits.
FACTS:
- VERIFIED(skills/debate/scripts/debate_exit.py:192 "OBJ = re.compile") converge object parsing expects exact regex forms.
- VERIFIED(skills/debate/scripts/debate_exit.py:196 "STANCE = re.compile") converge stance parsing expects exact regex forms.
- VERIFIED(skills/debate/SKILL.md:75 "Return ONLY this exact format:") the prompt demands an exact format.
- VERIFIED(skills/debate/SKILL.md:76 "> ```") the converge prompt includes an example format block.
- ASSUMED every phase prompt lacks enough machine-readable examples.
- ASSUMED adding examples would reduce paid retries.
- ASSUMED the cost is a few prompt lines per phase.

### I8: Replace the SKILL.md manual loop with a Python driver (opus)
Rank 19. DEFER until three runs (design). Five hand recoveries this run were all script defects or the moving tree, none was driver drift; I1, I4 and I6 move the heaviest driver duties into scripts or audit records first and may make this unnecessary.
Move the loop orchestration into one stdlib script so Claude acts only as a peer participant. A long repetitive manual procedure can drift; a script can reduce that risk and cut the tokens the driver spends re-reading the procedure each round.
FACTS:
- VERIFIED(skills/debate/SKILL.md:14 "## 0. Setup") the shipped driver procedure is manual text.
- VERIFIED(skills/debate/SKILL.md:115 "Print the report path and the id to the user and stop.") the manual procedure runs through report and stop.
- VERIFIED(docs/superpowers/specs/2026-09-15-debate-skill-design.md:23 "Claude is a peer.") Claude is a peer in the design.
- VERIFIED(docs/superpowers/specs/2026-09-15-debate-skill-design.md:23 "drives the loop.") Claude also drives the loop.
- ASSUMED SKILL.md is 115 lines today.
- ASSUMED a Python driver would reduce driver drift.
- ASSUMED a Python driver would cut token cost.
- ASSUMED this run had no driver-drift recovery.

### I16: Record participant capability metadata in state
Rank 20. DEFER (design). Opus cited exact lines whenever the prompt insisted on them and none when it did not; capability varies by prompt more than by participant, so a static map would mislead.
Design change, after measurement: add a `participants` map to `state.json` if future runs show stable capability differences by participant. It would record whether each participant has shell access, file-reading access, and citation capability for this run.
FACTS:
- VERIFIED(skills/debate/SKILL.md:17 "\"calls\": 0") state.json schema includes calls.
- VERIFIED(skills/debate/SKILL.md:17 "\"phase\": \"frame\"") state.json schema includes phase.
- VERIFIED(skills/debate/SKILL.md:17 "\"caps\": {\"diverge\": 2, \"verify\": 2, \"converge\": 5}") state.json schema includes caps.
- ASSUMED state.json lacks participant capability metadata.
- ASSUMED one setup probe and a few JSON fields is the cost.
- ASSUMED capability metadata would prevent rediscovering Q7 each time.
- ASSUMED opus cited exact lines when prompted to do so.

### I12: Give a shell-less participant the cited files inline in Verify
Rank 21. REJECT (design). Premise false on this run: opus has a shell and used it in diverge and verify round 2.
Design change, only if a future participant lacks repository access: when a participant cannot run a shell, the Verify prompt should inline the relevant file excerpts with line numbers so it can cite honestly, instead of dropping line numbers for everyone as I7 proposes.
FACTS:
- VERIFIED(skills/debate/SKILL.md:55 "`VERIFIED(<path>:<line> \"<snippet>\")` or `ASSUMED`") Verify asks for line-number citations.
- VERIFIED(skills/debate/scripts/debate_cite.py:49 "if m.group(\"snippet\") not in lines[n - 1]:") the checker needs exact line placement.
- ASSUMED opus in print mode cited no line numbers in the frame round.
- ASSUMED opus cited exact lines in diverge round 2, so the premise that it lacks a shell is doubtful.
- ASSUMED inlining relevant file excerpts with line numbers would let a truly shell-less participant cite honestly.
- ASSUMED the cost is one prompt paragraph and a driver step.

### I7: Remove line numbers from Verify citations (opus)
Rank 22. REJECT (design). 147 line-anchored citations verified against the pinned tree this run; opus withdrew the claim that most would fail.
Simplify the citation form to a path plus a snippet only if future evidence shows exact line numbers fail often even in a pinned tree. This run supports keeping line-anchored citations.
FACTS:
- VERIFIED(skills/debate/scripts/debate_cite.py:18 "FILE_FORM = re.compile") citation parsing is regex-based.
- VERIFIED(skills/debate/scripts/debate_cite.py:18 "(?P<line>\\d+)") the citation regex requires a line number.
- VERIFIED(skills/debate/scripts/debate_cite.py:49 "if m.group(\"snippet\") not in lines[n - 1]:") the checker tests the snippet on the cited line.
- ASSUMED a participant without a shell cannot predict exact line numbers.
- ASSUMED participants without shell access may have a higher citation-failure rate, but this run does not establish that almost all citations fail.
- ASSUMED gemini and opus both cited exact lines in diverge round 2.
- ASSUMED almost all citations will not fail in the measured participant set.

### I18: Increase protocol-error retries from one to three
Rank 23. REJECT (design). Zero companion format errors in ten calls; retries already count against the hard cap, so the cost claim was wrong in both directions.
Keep one retry per protocol-error turn until there is evidence that companion formatting variation, rather than script defects or moving evidence, wastes debates late in Converge.
FACTS:
- VERIFIED(skills/debate/SKILL.md:96 "re-dispatch `<p>` once") one retry per protocol-error turn today.
- VERIFIED(docs/superpowers/specs/2026-09-15-debate-skill-design.md:260 "re-dispatched once") the shipped retry rule is one retry.
- VERIFIED(docs/superpowers/specs/2026-09-15-debate-skill-design.md:261 "a second error stops the loop") the shipped rule stops on a second protocol error.
- VERIFIED(skills/debate/SKILL.md:12 "hard cap 27 including protocol retries") retries already count against the hard cap.
- ASSUMED increasing retries from one to three improves robustness enough to justify extra calls.
- ASSUMED three retries do not cost two extra calls in the worst case because the overall hard cap is fixed.
- ASSUMED this run had zero companion format errors.

### I19: Remove Claude as a peer participant
Rank 24. REJECT (design), but the reason changes: mechanical exits do not bound Claude's brief drafting or frame-critique merging. The better before-first-use fix is I6's audit of those powers, because removing Claude as a peer saves zero paid companion calls and does not by itself remove Claude's driver control over the brief and merge.
This influence concern is real. Rejecting I19 means rejecting the proposed remedy, not denying the problem. Adopt I6 first; after three runs, revisit whether the audit record shows Claude's peer role still distorts outcomes.
FACTS:
- VERIFIED(docs/superpowers/specs/2026-09-15-debate-skill-design.md:23 "Claude is a peer.") Claude is a peer participant.
- VERIFIED(docs/superpowers/specs/2026-09-15-debate-skill-design.md:23 "drives the loop.") Claude drives the loop.
- VERIFIED(skills/debate/SKILL.md:23 "Draft `$D/brief.md`") Claude drafts the brief.
- VERIFIED(skills/debate/SKILL.md:37 "Merge: for every critique write") Claude merges frame critiques.
- VERIFIED(docs/superpowers/specs/2026-09-15-debate-skill-design.md:24 "Every phase exit and the converged/stuck call are decided by a script") mechanical exits decide phase exits and converged/stuck calls, not brief content or frame merge choices.
- VERIFIED(skills/debate/SKILL.md:12 "Worst case 23 paid companion calls") cost accounting is paid companion calls.
- VERIFIED(skills/debate/SKILL.md:12 "(frame 2, diverge 4, verify 4, converge 10, closing 3)") the paid companion call total excludes Claude's in-session turns.
- ASSUMED removing Claude as peer saves Claude in-session work, not approximately one third of paid companion calls.
- ASSUMED removing Claude as peer saves zero paid companion calls.
- ASSUMED removing Claude as a peer does not by itself remove Claude's driver control over drafting the brief and merging frame critiques.
- ASSUMED Claude as peer may still distort outcomes even after I6, to be judged after measured runs.

### I20: Lower the advocate exhaustion floor to one attack
Rank 25. REJECT until measured (design). Lowering the floor before the first advocate round has run is a guess.
This is a design change to revisit after three runs. It matters because Q2 questions whether the mandatory three-entry ATTACKS TRIED floor generates useful objections or hollow theatre. I5 measures that before changing the floor.
FACTS:
- VERIFIED(skills/debate/SKILL.md:75 "list of at least three") the floor is three today.
- VERIFIED(skills/debate/SKILL.md:75 "ATTACKS TRIED:` list of at least three") advocate exhaustion requires at least three attacks tried.
- VERIFIED(docs/superpowers/specs/2026-09-15-debate-skill-design.md:157 "work-cost floor, not a semantic guarantee") the three-entry requirement is a work-cost floor.
- VERIFIED(skills/debate/scripts/debate_exit.py:275 "if len(att) < 3") the parser enforces at least three attacks.
- ASSUMED lowering the floor to one saves a small number of tokens.
- ASSUMED forcing three entries produces padded text when no new best case exists.

### I21: Delete the Verify phase entirely
Rank 26. REJECT (design). Verify corrected three false claims this run (I7, I19, I23) and verified 147 facts; deleting it would have shipped those claims.
This is a design change to evaluate after three runs only if Verify rarely changes outcomes. For now, keep Verify but narrow its promise through I3 and pin its evidence through I1.
FACTS:
- VERIFIED(skills/debate/SKILL.md:51 "## 4. Verify (1 round, may repeat once)") Verify may run one or two rounds.
- VERIFIED(skills/debate/SKILL.md:59 "dispatch both in parallel") Verify dispatches gemini and opus.
- VERIFIED(skills/debate/SKILL.md:55 "There is no command form") Verify has no command citation form.
- VERIFIED(skills/debate/SKILL.md:55 "a fact you can only show by running something is ASSUMED") runtime-only claims remain ASSUMED.
- ASSUMED deleting Verify would save two to four paid calls per debate.
- ASSUMED most substantive engineering claims require execution.
- ASSUMED Verify corrected false claims in this run.
- ASSUMED Verify verified 147 facts in this run.

### I27: Exempt closing statements from the state call cap
Rank 27. REJECT (design). Deliberate: a gate finding routed closing calls through the cap; exempting them reopens an uncapped path.
Do not bypass the cap for closing statements. If the debate hits the cap, the skill already defines that a participant may get no closing statement; that is an observability tradeoff, not a bug.
FACTS:
- VERIFIED(skills/debate/scripts/debate_round.py:47 "if st[\"calls\"] >= st[\"cap\"]:") debate_round refuses dispatches at the cap.
- VERIFIED(skills/debate/scripts/debate_round.py:48 "print(\"CAP %d\" % st[\"cap\"])") debate_round prints CAP when capped.
- VERIFIED(skills/debate/SKILL.md:103 "closing calls count against the cap") closing calls deliberately count against the cap.
- VERIFIED(skills/debate/SKILL.md:103 "not obtained (cap)") the skill explicitly defines the capped-closing outcome.
- VERIFIED(skills/debate/SKILL.md:103 "that participant gets no closing statement") CAP means no closing statement is obtained.
- ASSUMED exempting closing statements from the state cap is a design change, not a bug fix.
- ASSUMED a bypass flag or separate accounting is small.

## Assumptions
### I10: Q10 assumed - one retry per turn stays
The human did not choose between fail-fast and aggressive retry, so the debate proceeds on the shipped one-retry rule.
FACTS:
- VERIFIED(docs/superpowers/specs/2026-09-15-debate-skill-design.md:260 "re-dispatched once") the shipped rule is one retry per protocol error.
- ASSUMED the human is content with one retry per turn.

ANSWERS:
O1: I1 changed: it is now Rank 1 and explicitly includes pinning the repo to a commit before dispatch, because VERIFIED(skills/debate/SKILL.md:17 "`\"repo\": \"<git toplevel>\"`") shows state records only a repo path while VERIFIED(skills/debate/scripts/debate_cite.py:45 "lines = p.read_text(errors=\"replace\").splitlines()") and VERIFIED(skills/debate/scripts/debate_cite.py:49 "if m.group(\"snippet\") not in lines[n - 1]:") show Verify checks the live file at check time. I13 moved to Rank 2.

O2: I28 changed: it no longer claims the fix is just deleting one comparison. It now requires both legal id gaps and max-id-plus-one allocation, with a test for deletion followed by addition, because VERIFIED(skills/debate/scripts/debate_exit.py:101 "next_k = len(items) + 1") and VERIFIED(skills/debate/scripts/debate_exit.py:106 "mapping[m.group(1)] = \"I%d\" % next_k") show allocation is currently count-based while VERIFIED(docs/superpowers/specs/2026-09-15-debate-skill-design.md:34 "Items are numbered `I1, I2, ...` and keep their ids") requires stable ids.

O3: I6 changed: it is no longer merged into I4. It is now a distinct Rank 7 ADOPT item focused on Q5, requiring a brief diff and explicit frame-critique ledger, because VERIFIED(skills/debate/SKILL.md:23 "Draft `$D/brief.md`") and VERIFIED(skills/debate/SKILL.md:37 "Merge: for every critique write") concern brief-shaping and merge power, while VERIFIED(skills/debate/SKILL.md:115 "Every number comes from the transcript and `state.json`") concerns only report/log computation.

O11: I19 changed but remains REJECT. It now concedes the objection's factual point: mechanical exits do not bound Claude's brief drafting or frame merge, as shown by VERIFIED(skills/debate/SKILL.md:23 "Draft `$D/brief.md`") and VERIFIED(skills/debate/SKILL.md:37 "Merge: for every critique write"). The remedy moved to I6 because removing Claude as a peer saves zero paid companion calls and does not itself remove Claude's driver control over the brief or frame merge.
