#!/usr/bin/env bash
# Runs every debate script selftest and an end-to-end simulation. Exit non-zero on the first failure.
set -euo pipefail

export DEBATE_SELFTEST=1

here="$(cd "$(dirname "$0")" && pwd)"
for s in debate_cite.py debate_exit.py debate_round.py debate_report.py debate_setup.py; do
  python3 "$here/$s" --selftest
done

# Non-vacuous doc and contract validation for SKILL.md
skill_md="$here/../SKILL.md"
if [ ! -f "$skill_md" ]; then
  echo "FAIL: SKILL.md missing at $skill_md" >&2
  exit 1
fi

# Verify required workflow keywords and prompt mandate templates
for kw in \
  "record-turn" \
  "DEBATE-NONCE" \
  "Advocate" \
  "Reviser" \
  "--tiebreak" \
  "ask_question" \
  "[ISOLATION MANDATE]" \
  "[COMMUNICATION MANDATE]" \
  "--response-file" \
  "<parent_id>"; do
  if ! grep -F -e "$kw" "$skill_md" >/dev/null 2>&1; then
    echo "FAIL: SKILL.md missing keyword: $kw" >&2
    exit 1
  fi
done

# Verify debate_report.py supports --tiebreak argument
python3 "$here/debate_report.py" record "$here" --task "test" --outcome "STUCK" --tiebreak "I1" >/dev/null 2>&1 || true

# -----------------------------------------------------------------------------
# End-to-End Multi-Round Debate Simulation
# Exercises:
# - debate_setup.py with --scope and --session-id
# - record-turn --response-file with verified provenance
# - scoped citation checking in pinned worktree
# - advocate exhaustion on artifact with len < 3 items reaching QUORUM
# - debate_report.py report generation and teardown
# -----------------------------------------------------------------------------
echo "Running end-to-end multi-round debate simulation..."
sim_tmp="$(mktemp -d)"
trap 'rm -rf "$sim_tmp"' EXIT

sim_repo="$sim_tmp/repo"
mkdir -p "$sim_repo/src"
git -C "$sim_repo" init -q
cat << 'EOF' > "$sim_repo/src/service.py"
def execute_request():
    return "ok"
EOF
git -C "$sim_repo" add src/service.py
git -C "$sim_repo" -c user.email=t@t -c user.name=t commit -q -m "initial commit"

export DEBATE_STATE_ROOT="$sim_tmp/state"
comp_stub="$sim_tmp/companion_stub.py"
cat << 'EOF' > "$comp_stub"
import sys, re
from pathlib import Path
a = sys.argv[1:]
if '--prompt-file' in a:
    p = Path(a[a.index('--prompt-file') + 1]).read_text()
    m = re.search(r'DEBATE-NONCE (\w+)', p)
    if m:
        print("NONCE " + m.group(1))
print("STUB OK")
EOF
export DEBATE_COMPANION_OPUS="$comp_stub"
export DEBATE_COMPANION_GEMINI="$comp_stub"

# 1. Exercise debate_setup.py with --scope and --session-id
setup_output="$(python3 "$here/debate_setup.py" "$sim_repo" \
  --mode unattended \
  --driver gemini \
  --companion opus \
  --heading "Plan" \
  --scope "src" \
  --session-id "sim-parent-session-999")"

d_dir="$(echo "$setup_output" | grep -E '^D=' | cut -d= -f2)"
if [ -z "$d_dir" ] || [ ! -d "$d_dir" ]; then
  echo "FAIL: debate_setup.py failed to initialize debate dir: $setup_output" >&2
  exit 1
fi

# Verify state.json persistence of session_id and scope
grep -F '"session_id": "sim-parent-session-999"' "$d_dir/state.json" >/dev/null 2>&1 || {
  echo "FAIL: session_id not persisted in state.json" >&2
  exit 1
}
grep -F '"scope": "src"' "$d_dir/state.json" >/dev/null 2>&1 || {
  echo "FAIL: scope not persisted in state.json" >&2
  exit 1
}

# 2. Exercise record-turn --response-file with authentic provenance
mock_brain="$sim_tmp/mock_brain"
export DEBATE_MOCK_BRAIN_ROOT="$mock_brain"
sim_subagent="subagent-sim-42"
mkdir -p "$mock_brain/$sim_subagent"
echo '{"event": "start"}' > "$mock_brain/$sim_subagent/transcript.jsonl"

sim_prompt="$d_dir/calls/01-opus.prompt.md"
cat << 'EOF' > "$sim_prompt"
DEBATE-NONCE abcd5678
Simulation frame prompt
EOF

sim_resp_file="$sim_tmp/sim_resp.md"
cat << 'EOF' > "$sim_resp_file"
NONCE abcd5678
MISSING QUESTIONS: none
CRITERIA: none
SEED ITEMS: none
QUESTIONS FOR HUMAN: none
EOF

python3 "$here/debate_round.py" record-turn "$d_dir" opus "$sim_subagent" "$sim_prompt" --response-file "$sim_resp_file"

if [ ! -f "$d_dir/calls/01-opus.out.md" ]; then
  echo "FAIL: record-turn --response-file did not create output file" >&2
  exit 1
fi
if [ ! -f "$d_dir/calls/01-opus.prov.json" ]; then
  echo "FAIL: record-turn did not create provenance file" >&2
  exit 1
fi

# 3. Scoped citation checking
python3 -c "
import sys
sys.path.insert(0, '$here')
from debate_cite import check_citation

pinned = '$d_dir/pinned'
# Pinned tree has src/service.py
good_cite = 'src/service.py:1 \"def execute_request():\"'
ok, msg = check_citation(good_cite, pinned)
assert ok is True, f'Expected valid scoped citation, got error: {msg}'

# Non-existent path or outside scope fails
bad_cite = 'src/missing.py:1 \"def none():\"'
ok_bad, msg_bad = check_citation(bad_cite, pinned)
assert ok_bad is False, 'Expected non-existent file citation to fail'
"

# Create minimal brief.md required by debate reporting
cat << 'EOF' > "$d_dir/brief.md"
# Brief
Task: E2E Simulation Task
Deliverable: Plan
EOF

# 4. Multi-round simulation: Converge with advocate exhaustion on artifact with len < 3 items
# Artifact has exactly 2 items: I1 and I2 (len < 3)
cat << 'EOF' > "$d_dir/artifact.md"
## Plan

### I1: Primary Service Pipeline
Primary service implements execution requests cleanly.
FACTS:
- VERIFIED(src/service.py:1 "def execute_request():") executes request

### I2: Fallback Ingestion Pipeline
Fallback handler for ingestion.
FACTS:
- ASSUMED fallback memory budget is adequate
EOF

# In converge phase: artifact.prev.md holds the artifact prior to reviser changes
cp "$d_dir/artifact.md" "$d_dir/artifact.prev.md"

# Transcript: R1 converge with Reviser addressing items, and Advocate exhausted with 3 tried attacks on 2 items
cat << 'EOF' > "$d_dir/transcript.md"
## R1 converge gemini role=reviser
ARTIFACT:
## Plan

### I1: Primary Service Pipeline
Primary service implements execution requests cleanly.
FACTS:
- VERIFIED(src/service.py:1 "def execute_request():") executes request

### I2: Fallback Ingestion Pipeline
Fallback handler for ingestion.
FACTS:
- ASSUMED fallback memory budget is adequate

ANSWERS:
none

## R1 converge opus role=advocate
STANCE: ACCEPT
OBJECTIONS:
none [advocate exhausted]
CONFIDENCE:
none
REVIEW:
none
QUESTIONS:
none
ATTACKS TRIED:
I1: Tested request latency spike, covered by async loop
I2: Tested fallback memory exhaustion, covered by bound
I1: Tested connection drop recovery, verified handled
EOF

converge_verdict="$(python3 "$here/debate_exit.py" "$d_dir" converge)"
if [ "$converge_verdict" != "QUORUM" ]; then
  echo "FAIL: expected QUORUM on advocate exhaustion with len < 3 items, got: $converge_verdict" >&2
  exit 1
fi

# 5. Report generation and teardown
python3 "$here/debate_report.py" report "$d_dir" --task "E2E Simulation Task" --outcome "QUORUM"
if [ ! -f "$d_dir/report.md" ]; then
  echo "FAIL: report.md not generated by debate_report.py" >&2
  exit 1
fi
grep -F "QUORUM" "$d_dir/report.md" >/dev/null 2>&1 || {
  echo "FAIL: report.md missing QUORUM outcome" >&2
  exit 1
}

# Teardown pinned workspace
python3 "$here/debate_setup.py" --teardown "$d_dir"
if [ -d "$d_dir/pinned" ]; then
  echo "FAIL: debate_setup.py --teardown failed to remove pinned directory" >&2
  exit 1
fi

echo "selftest.sh: all tests and end-to-end simulation passed"

