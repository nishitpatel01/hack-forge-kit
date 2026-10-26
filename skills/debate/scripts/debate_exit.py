#!/usr/bin/env python3
"""debate_exit.py <dir> <phase> - print the mechanical verdict for the current round.

Verdicts: CONTINUE | EXIT | QUORUM | STUCK <reason> | PROTOCOL-ERROR
<participant> <reason>
Stdlib only. `--selftest` runs the embedded fixtures.
"""

import json
import os
from pathlib import Path
import re
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from debate_cite import check_citation  # noqa: E402
from debate_round import _find_subagent_transcript  # noqa: E402

HEADER = re.compile(
    r"^## R(\d+) (frame|diverge|verify|converge|closing) ([a-zA-Z0-9_-]+)"
    r" role=(reviser|advocate|none)\s*$"
)
ITEM = re.compile(r"^### (I\d+): (.+)$")
HEADING = re.compile(r"^## (.+)$")
# a citation always ends with `")`, so match to the first `")`: a `)` inside the snippet is fine
FACT = re.compile(
    r"^- (VERIFIED\((.+?(?<!\\)\")\)|ASSUMED) (.+)$"
)  # an escaped \" does not end it
# A citation carrying a path:line that is NOT on a FACT line: the participant wrote it inline in
# prose, where parse_artifact cannot see it. Requiring the `:<digit>` is what keeps a line that
# merely DISCUSSES the syntax (`VERIFIED(...)` in a debate about this skill) from matching.
INLINE_FACT = re.compile(r"VERIFIED\([^)]*:\d")


def parse_transcript(text):
  turns = []
  cur = None
  for line in text.splitlines():
    m = HEADER.match(line)
    if m:
      if cur:
        turns.append(cur)
      cur = dict(
          round=int(m.group(1)),
          phase=m.group(2),
          participant=m.group(3),
          role=m.group(4),
          body="",
      )
    elif cur is not None:
      cur["body"] += line + "\n"
  if cur:
    turns.append(cur)
  return turns


def parse_artifact(text):
  items = {}
  heading = None
  item = None
  for line in text.splitlines():
    h = HEADING.match(line)
    if h and not line.startswith("### "):
      heading = h.group(1).strip()
      item = None
      continue
    i = ITEM.match(line)
    if i:
      item = dict(
          id=i.group(1),
          heading=heading,
          title=i.group(2).strip(),
          body="",
          facts=[],
      )
      if i.group(1) in items:
        item["dup"] = True
      items[i.group(1)] = item
      continue
    if item is None:
      continue
    f = FACT.match(line)
    if f:
      kind = "VERIFIED" if f.group(1).startswith("VERIFIED") else "ASSUMED"
      item["facts"].append(
          dict(kind=kind, citation=f.group(2), claim=f.group(3).strip())
      )
    elif line.strip() != "FACTS:":
      item["body"] += line + "\n"
  return items


def validate_structure(items, turns):
  errors = []
  # gaps are legal (a reviser may drop an item); ids only have to be unique and stable
  for k, it in items.items():
    if it.get("dup"):
      errors.append("duplicate item id %s" % k)
  ref = re.compile(r"\((I\d+)\)")
  for t in turns:
    for m in ref.finditer(t["body"]):
      if m.group(1) not in items:
        errors.append(
            "%s R%d refers to missing %s"
            % (t["participant"], t["round"], m.group(1))
        )
  for t in turns:
    # frame turns carry seed items as T-blocks (resolved by assign-ids from a separate file),
    # so only verify, converge and closing turns may not mention an alias
    if re.search(r"\bT\d+\b", t["body"]) and t["phase"] not in (
        "diverge",
        "frame",
    ):
      errors.append(
          "%s R%d has unresolved alias" % (t["participant"], t["round"])
      )
    if t["phase"] == "diverge" and re.search(
        r"^(STANCE:|O\d+ \()", t["body"], re.M
    ):
      errors.append(
          "%s R%d critique in diverge" % (t["participant"], t["round"])
      )
    if t["phase"] == "verify":
      m = re.search(r"^DISPUTE: (.+)$", t["body"], re.M)
      for k in (disputed_ids(m.group(1)) if m else []):
        if k not in items:
          errors.append(
              "%s R%d disputes missing %s" % (t["participant"], t["round"], k)
          )
  return errors


def disputed_ids(rest):
  """Item ids on a DISPUTE line.

  Two runs (a1f3c7, 54beb5) wrote `DISPUTE: I20 <why>`; the ids are the dispute,
  the prose is not, so only `I<k>` tokens count and `none` means none.
  """
  return [] if rest.strip() == "none" else re.findall(r"\bI\d+\b", rest)


def validate_companion_provenance(d, turns):
  """Verify that companion turns carry authentic out-of-band provenance.

  Rejects turns where driver simulated the companion in-situ.
  """
  st = _state(d)
  if not st:
    return []
  # Check if testing mock / bypass is allowed
  if st.get("mock_for_testing"):
    return []

  driver_name = st.get("driver", "gemini")
  driver_family = st.get("driver_family")
  if not driver_family:
    driver_family = "gemini" if "gemini" in driver_name.lower() else "anthropic"
  session_id = st.get("session_id")

  errors = []
  calls_dir = Path(d) / "calls"
  # Find all companion turns in the transcript
  comp_turns = [
      t
      for t in turns
      if t["participant"] != driver_name and t["participant"] != "driver"
  ]
  if not comp_turns:
    return []

  # Verify companion model family is not same as driver family
  for comp in st.get("companions", []):
    comp_family = (
        "gemini"
        if any(k in comp.lower() for k in ("gemini", "google"))
        else "anthropic"
    )
    if comp_family == driver_family and not st.get("allow_same_family"):
      errors.append(
          "companion %s belongs to same model family (%s) as driver %s"
          % (comp, comp_family, driver_name)
      )

  # If calls directory exists, check for provenance markers or verify provenance JSON
  prov_files = list(calls_dir.glob("*.prov.json")) if calls_dir.is_dir() else []
  # If there are companion turns but calls dir has no provenance files
  if calls_dir.is_dir() and not prov_files and not st.get("mock_for_testing"):
    # Check if there are companion out files authored without prov
    out_files = [
        p
        for p in calls_dir.glob("*.out.md")
        if not any(driver_name in p.name or "driver" in p.name for _ in [0])
    ]
    if out_files:
      errors.append(
          "driver self-simulated companion turn without verified subagent"
          " provenance metadata"
      )

  for prov_p in prov_files:
    try:
      meta = json.loads(prov_p.read_text())
      subagent_conv = meta.get("subagent_conversation_id")
      if not subagent_conv:
        errors.append(
            "companion turn %s has missing subagent_conversation_id"
            % prov_p.name
        )
      elif session_id and subagent_conv == session_id:
        errors.append(
            "companion turn %s executed within driver parent session %s"
            " (self-simulation)" % (prov_p.name, session_id)
        )
      elif not st.get("mock_for_testing"):
        if not _find_subagent_transcript(subagent_conv):
          errors.append(
              "companion turn %s references subagent %s with no authentic"
              " transcript on disk" % (prov_p.name, subagent_conv)
          )
        if meta.get("attestation_status") != "CONFIRMED":
          errors.append(
              "companion turn %s failed attestation: %s"
              % (prov_p.name, meta.get("attestation_status", "UNATTESTED"))
          )
        prov_fam = meta.get("family")
        if (
            prov_fam
            and prov_fam == driver_family
            and not st.get("allow_same_family")
        ):
          errors.append(
              "companion turn %s attested same model family (%s) as driver"
              " (self-simulation)" % (prov_p.name, prov_fam)
          )
    except Exception as e:
      errors.append("corrupt provenance file %s: %s" % (prov_p.name, e))

  return errors


ALIAS_ITEM = re.compile(r"^### (T\d+): (.+)$")
ALIAS = re.compile(r"(?<![`\w])(T\d+)(?![`\w])")
CODE_SPAN = re.compile(
    r"`[^`]*`"
)  # spans pair left to right; an alias BETWEEN two spans is fine
ALIAS_IN_SPAN = re.compile(r"\bT\d+\b")


def assign_ids(artifact_text, turn_body, heading):
  m_items = re.search(r"^ITEMS:", turn_body, re.M)
  block = turn_body[m_items.end() :] if m_items else turn_body
  if block.strip() == "none":
    return artifact_text, {}
  items = parse_artifact(artifact_text)
  next_k = max((int(k[1:]) for k in items), default=0) + 1
  mapping = {}
  for line in turn_body.splitlines():
    m = ALIAS_ITEM.match(line)
    if m:
      mapping[m.group(1)] = "I%d" % next_k
      next_k += 1
  for span in CODE_SPAN.findall(turn_body):
    if ALIAS_IN_SPAN.search(span):
      raise ValueError("alias inside code span: %s" % span)
  unknown = set(ALIAS.findall(turn_body)) - set(mapping)
  if unknown:
    raise ValueError("unresolved alias %s" % sorted(unknown))
  block = ALIAS.sub(lambda m: mapping[m.group(1)], block)
  marker = "## %s\n" % heading
  if marker not in artifact_text:
    artifact_text = artifact_text.rstrip("\n") + "\n\n" + marker
  head, tail = artifact_text.split(marker, 1)
  nxt = tail.find("\n## ")
  if nxt == -1:
    tail = tail.rstrip("\n") + "\n" + block.strip("\n") + "\n"
  else:
    tail = (
        tail[:nxt].rstrip("\n") + "\n" + block.strip("\n") + "\n" + tail[nxt:]
    )
  return head + marker + tail, mapping


def _state(d):
  p = Path(d) / "state.json"
  return json.loads(p.read_text()) if p.is_file() else {}


def _turns(d, phase):
  return [
      t
      for t in parse_transcript((Path(d) / "transcript.md").read_text())
      if t["phase"] == phase
  ]


FRAME_SIGNALS = (
    "MISSING QUESTIONS",
    "CRITERIA",
    "SEED ITEMS",
    "QUESTIONS FOR HUMAN",
)


def _frame_empty(body):
  """True only when the whole reply is frame sections whose bodies are empty or 'none'.

  Prose outside any section is a signal, not emptiness: a companion that
  critiques the brief
  without using the headers must not be read as having found nothing. Every
  ambiguity here
  resolves towards 'not empty', because a false abort ends a debate that should
  have run.
  """
  rest = body
  for name in FRAME_SIGNALS:
    text = _section(body, name, [n for n in FRAME_SIGNALS if n != name])
    if text.strip() and text.strip().strip(".").lower() != "none":
      return False
    rest = rest.replace(name + ":" + text, "", 1)
  return rest.strip().strip(".").lower() in ("", "none")


def frame_verdict(d):
  turns = _turns(d, "frame")
  who = {t["participant"] for t in turns}
  st = _state(d)
  expected_comps = st.get("companions")
  driver_name = st.get("driver")

  merged_turns = [
      t for t in turns if re.search(r"^(ADOPTED|REJECTED): ", t["body"], re.M)
  ]
  merged = bool(merged_turns)
  effective_driver = (
      merged_turns[0]["participant"]
      if merged_turns
      else (driver_name or "driver")
  )

  comp = [t for t in turns if t["participant"] != effective_driver]
  comp_names = {t["participant"] for t in comp}

  if expected_comps:
    ready = set(expected_comps) <= comp_names and merged
  else:
    ready = len(comp_names) >= 1 and merged

  if not ready:
    return "CONTINUE"
  return (
      "NOT-DEBATE-SHAPED"
      if all(_frame_empty(t["body"]) for t in comp)
      else "EXIT"
  )


def _check_stale_continue(d, phase, rnd):
  """Detect when a round already exited with CONTINUE and new companion calls were recorded without appending R<rnd+1> to transcript.md."""
  st = _state(d)
  exits = st.get("exits", [])
  had_continue = any(
      isinstance(e, list)
      and len(e) >= 3
      and e[0] == phase
      and e[1] == rnd
      and e[2] == "CONTINUE"
      for e in exits
  )
  if not had_continue:
    return None
  exit_counts = st.get("exit_call_counts", {})
  key = "%s:R%d" % (phase, rnd)
  if key not in exit_counts:
    return None
  prev_calls = exit_counts[key]
  curr_calls = st.get("calls", 0)
  if curr_calls > prev_calls:
    return (
        "PROTOCOL-ERROR driver transcript.md is still at %s R%d (already exited"
        " with CONTINUE at call %d, now at call %d); append ## R%d %s turns to"
        " transcript.md before running debate_exit.py"
        % (phase, rnd, prev_calls, curr_calls, rnd + 1, phase)
    )
  return None


def diverge_verdict(d):
  d = Path(d)
  turns = _turns(d, "diverge")
  if not turns:
    return (
        "PROTOCOL-ERROR driver no diverge turns recorded in transcript.md;"
        " append ## R1 diverge <participant> role=none before running"
        " debate_exit.py"
    )
  rnd = max(t["round"] for t in turns)
  stale = _check_stale_continue(d, "diverge", rnd)
  if stale:
    return stale
  before = len(parse_artifact((d / "artifact.prev.md").read_text()))
  after = len(parse_artifact((d / "artifact.md").read_text()))
  if after == before:
    return "EXIT"  # nothing left to add: the phase converged
  # The round still added items; only the cap stops it. Recorded distinctly so
  # the ledger can tell a converged phase from a truncated one.
  return "EXIT cap" if rnd >= _state(d)["caps"]["diverge"] else "CONTINUE"


def verify_verdict(d, out):
  d = Path(d)
  st = _state(d)
  turns = _turns(d, "verify")
  if not turns:
    return (
        "PROTOCOL-ERROR driver no verify turns recorded in transcript.md;"
        " append ## R1 verify <participant> role=none before running"
        " debate_exit.py"
    )
  rnd = max(t["round"] for t in turns)
  stale = _check_stale_continue(d, "verify", rnd)
  if stale:
    return stale
  scope = st.get("scope")
  art_path = d / "artifact.md"
  text = art_path.read_text()
  items = parse_artifact(text)
  for k, it in items.items():
    for f in it["facts"]:
      if f["kind"] != "VERIFIED":
        continue
      ok, why = check_citation(f["citation"], st.get("repo", "."), scope=scope)
      if not ok:
        old = "- VERIFIED(%s) %s" % (f["citation"], f["claim"])
        new = "- ASSUMED %s [downgraded: %s]" % (f["claim"], why)
        text = text.replace(
            old, new
        )  # every identical line; a duplicate is not a crash
        out.append("DOWNGRADED %s %s" % (k, why))
  art_path.write_text(text)
  items = parse_artifact(text)
  # The whole phase downgrades and disputes FACTS lines. An artifact carrying none of them makes
  # every check below vacuous: the dispute loop finds nothing and returns EXIT, which is
  # indistinguishable from a phase that checked every claim and found them all sound. Name the
  # empty case before the semantic one runs, so a verify that produced nothing cannot read as a
  # verify that produced a clean bill. (d-2026-09-19-43b400 EXITed this way on 12 real citations.)
  if not any(it["facts"] for it in items.values()):
    inline = [
        l.strip()
        for l in text.splitlines()
        if INLINE_FACT.search(l) and not FACT.match(l)
    ]
    if inline:
      return (
          "PROTOCOL-ERROR driver %d citation(s) written inline in prose; a fact"
          ' is only read as `- VERIFIED(path:line "quote") claim` on its own'
          " line under an item's FACTS: block, so no item carries one."
          " First: %s" % (len(inline), inline[0][:90])
      )
    return (
        "PROTOCOL-ERROR driver verify produced no fact on any item, so nothing"
        " can be downgraded or disputed; re-run merge-facts or check the verify"
        " replies for FACTS blocks"
    )
  disputed = set()
  for t in turns:
    if t["round"] != rnd:
      continue
    m = re.search(r"^DISPUTE: (.+)$", t["body"], re.M)
    if m:
      disputed.update(disputed_ids(m.group(1)))
  for k in disputed:
    if k in items and any(f["kind"] == "ASSUMED" for f in items[k]["facts"]):
      # A live dispute remains; only the cap stops it. See diverge_verdict.
      return "EXIT cap" if rnd >= st["caps"]["verify"] else "CONTINUE"
  return "EXIT"  # no disputed item still rests on an ASSUMED fact


OBJ = re.compile(r"^(O\d+) \((I\d+)\)( \[advocate\])?: (.+)$", re.M)
SAME = re.compile(r"^same as (O\d+)$")
REVIEW = re.compile(r"^(O\d+) (ANSWERED|EVADED)$", re.M)
STANCE = re.compile(r"^STANCE: (ACCEPT|OBJECT)$", re.M)
ATTACK = re.compile(r"^(I\d+): .+$", re.M)


def _section(body, name, nxt):
  """Text between 'name:' and the next section header in nxt (a list)."""
  start = body.find(name + ":")
  if start == -1:
    return ""
  start += len(name) + 1
  ends = [body.find("\n" + n + ":", start) for n in nxt]
  ends = [e for e in ends if e != -1]
  return body[start : min(ends)] if ends else body[start:]


def _item_text(art_text, k):
  it = parse_artifact(art_text).get(k)
  return (
      None
      if it is None
      else (
          it["title"],
          it["body"],
          [f["citation"] for f in it["facts"] if f["kind"] == "VERIFIED"],
      )
  )


def _norm_items(items_dict):
  return {
      k: (
          it["title"].strip(),
          it["body"].strip(),
          [
              (f["kind"], f.get("citation"), f["claim"].strip())
              for f in it["facts"]
          ],
      )
      for k, it in items_dict.items()
  }


def converge_verdict(d):
  d = Path(d)
  st = _state(d)
  turns = _turns(d, "converge")
  if not turns:
    return "CONTINUE"
  R = max(t["round"] for t in turns)
  stale = _check_stale_continue(d, "converge", R)
  if stale:
    return stale
  cur_art = (d / "artifact.md").read_text()
  items = parse_artifact(cur_art)
  # Per-round artifacts come from the transcript itself: each reviser turn carries the whole
  # artifact, so a diff-based answer from round 2 is still scored against rounds 1 and 2 when the
  # script runs in round 4. artifact.prev.md is the artifact at the start of converge round 1.
  art_by_round = {0: (d / "artifact.prev.md").read_text()}

  raised = {}  # O -> dict(item, advocate, round)
  answered = {}  # O -> bool (latest)
  evasions = {}  # O -> count
  open_nonadv_by_round = {}
  for r in range(1, R + 1):
    rt = [t for t in turns if t["round"] == r]
    rev = [t for t in rt if t["role"] == "reviser"]
    non = [t for t in rt if t["role"] != "reviser"]
    adv = [t for t in non if t["role"] == "advocate"]
    if r < R:
      if len(rev) != 1 or len(adv) != 1 or len(non) not in (1, 2):
        return "PROTOCOL-ERROR driver round %d roles" % r
    else:
      if (
          len(rt) == 1
          or (len(rev) == 1 and len(adv) == 0 and len(non) < 2)
          or (len(adv) == 1 and len(rev) == 0)
      ):
        return "CONTINUE"
      if len(rev) != 1 or len(adv) != 1 or len(non) not in (1, 2):
        return "PROTOCOL-ERROR driver round %d roles" % r
    # answers from the reviser, scored against this round's artifact and the previous one
    art_by_round[r] = _section(rev[0]["body"], "ARTIFACT", ["ANSWERS"])
    round_items = parse_artifact(art_by_round[r])
    prev_items = parse_artifact(art_by_round[r - 1])
    if not round_items and (prev_items or items or not art_by_round[r].strip()):
      return (
          "PROTOCOL-ERROR %s reviser turn in R%d has empty or missing ARTIFACT"
          " section" % (rev[0]["participant"], r)
      )
    if r == R and _norm_items(items) != _norm_items(round_items):
      return (
          "PROTOCOL-ERROR driver artifact.md is out of sync with R%d reviser"
          " ARTIFACT in transcript.md; run debate_report.py preserve-facts"
          " <dir> to synchronize artifact.md" % r
      )
    dropped = sorted(
        (
            k
            for k, it in prev_items.items()
            if k in round_items
            and len(round_items[k]["facts"]) < len(it["facts"])
        ),
        key=lambda k: (len(k), k),
    )
    if dropped:
      return (
          "PROTOCOL-ERROR %s reviser dropped FACTS lines from %s; an item kept"
          " in the artifact must carry every fact it already had, verbatim -"
          " add facts freely, never drop one"
          % (rev[0]["participant"], ", ".join(dropped))
      )
    ans_text = _section(rev[0]["body"], "ANSWERS", [])
    answers = {}
    for part in re.split(r"(?m)^(?=O\d+: )", ans_text):
      m_a = re.match(r"(O\d+): (.*)", part, re.S)
      if m_a:
        answers[m_a.group(1)] = m_a.group(2).strip()
    for o, text in answers.items():
      sm = SAME.match(text.strip())
      if sm:
        tgt = sm.group(1)
        if (
            int(tgt[1:]) >= int(o[1:])
            or tgt not in answers
            or SAME.match(answers[tgt].strip())
        ):
          return "PROTOCOL-ERROR %s bad reference %s" % (
              rev[0]["participant"],
              o,
          )
        answered[o] = answered.get(tgt, False)
        continue
      if o not in raised:
        continue
      k = raised[o]["item"]
      changed = _item_text(art_by_round[r - 1], k) != _item_text(
          art_by_round[r], k
      )
      cites = round_items.get(k, {}).get("facts", [])
      cited = any(
          ("VERIFIED(%s)" % f["citation"]) in text
          for f in cites
          if f["kind"] == "VERIFIED"
      )
      answered[o] = changed or cited
    # non-reviser turns
    round_ids = (
        {}
    )  # O -> item raised in THIS round, to catch parallel id collisions
    for t in non:
      b = t["body"]
      sm = STANCE.search(b)
      if not sm:
        return "PROTOCOL-ERROR %s malformed STANCE" % t["participant"]
      obj_text = _section(
          b, "OBJECTIONS", ["REVIEW", "QUESTIONS", "ATTACKS TRIED"]
      )
      objs = OBJ.findall(obj_text)
      is_adv = t["role"] == "advocate"
      if is_adv and not objs:
        if "[advocate exhausted]" not in obj_text:
          return "PROTOCOL-ERROR %s advocate must object" % t["participant"]
        if len(items) == 0:
          return (
              "PROTOCOL-ERROR %s empty artifact cannot converge"
              % t["participant"]
          )
        att = ATTACK.findall(_section(b, "ATTACKS TRIED", []))
        items_keys = set(items.keys())
        distinct_attacks = set(att)
        min_attacks = min(3, len(items_keys))
        if len(distinct_attacks) < min_attacks:
          return "PROTOCOL-ERROR %s attacks tried (%d of %d required)" % (
              t["participant"],
              len(distinct_attacks),
              min_attacks,
          )
        if not distinct_attacks <= items_keys:
          return "PROTOCOL-ERROR %s unknown attacks tried" % t["participant"]
        t["exhausted"] = True
      for o, k, tag, _ in objs:
        if k not in items:
          return "PROTOCOL-ERROR %s unknown item %s" % (t["participant"], k)
        if bool(tag) != is_adv:
          return "PROTOCOL-ERROR %s advocate tag mismatch %s" % (
              t["participant"],
              o,
          )
        if o in round_ids:
          # any second use of an id inside one round, same item or not, is two objections
          # wearing one id; the driver hands the two reviewers disjoint ranges to avoid this
          return "PROTOCOL-ERROR %s objection id collision %s" % (
              t["participant"],
              o,
          )
        if o in raised and raised[o]["item"] != k:
          return "PROTOCOL-ERROR %s objection id reused for another item %s" % (
              t["participant"],
              o,
          )
        round_ids[o] = k
        raised.setdefault(o, dict(item=k, advocate=is_adv, round=r))
        if not is_adv:
          raised[o][
              "advocate"
          ] = False  # a non-advocate re-raise makes it a real blocker
        answered[o] = False
      for o, mark in REVIEW.findall(
          _section(b, "REVIEW", ["QUESTIONS", "ATTACKS TRIED"])
      ):
        if o not in answers:
          return "PROTOCOL-ERROR %s review of unanswered %s" % (
              t["participant"],
              o,
          )
        if mark == "EVADED" and o in raised:
          evasions[o] = evasions.get(o, 0) + 1
          answered[o] = False
          if evasions[o] >= 2:
            return "STUCK evaded twice %s" % o
    open_nonadv_by_round[r] = frozenset(
        o
        for o, m in raised.items()
        if not m["advocate"] and not answered.get(o, False)
    )

  cur = [t for t in turns if t["round"] == R and t["role"] != "reviser"]
  adv = next((t for t in cur if t["role"] == "advocate"), None)
  other = next((t for t in cur if t["role"] != "advocate"), None)
  if adv is None:
    return "PROTOCOL-ERROR driver round %d missing advocate" % R
  other_accepts = (
      STANCE.search(other["body"]).group(1) == "ACCEPT" if other else True
  )
  adv_done = all(
      answered.get(o, False) for o, m in raised.items() if m["advocate"]
  )
  if (
      other_accepts
      and not open_nonadv_by_round[R]
      and adv.get("exhausted")
      and adv_done
  ):
    return "QUORUM"  # an acceptance in the cap round is still an acceptance
  if R >= st["caps"]["converge"]:
    return "STUCK cap"
  if (
      R >= 2
      and open_nonadv_by_round[R]
      and open_nonadv_by_round[R] == open_nonadv_by_round[R - 1]
  ):
    return "STUCK same objections"
  return "CONTINUE"


def _strip_companion_log(text):
  """Companion output has a log preamble of `[<name>] ...` lines.

  Drop those lines first, then start at the earliest protocol marker
  that sits at a LINE start.
  """
  body = (
      "\n".join(
          l
          for l in text.splitlines()
          if not re.match(r"^\[[a-zA-Z0-9_-]+\] ", l)
      )
      + "\n"
  )
  best = None
  for marker in (
      "MISSING QUESTIONS:",
      "ITEMS:",
      "FACTS I",
      "STANCE:",
      "ARTIFACT:",
  ):
    m = re.search(r"^" + re.escape(marker), body, re.M)
    if m and (best is None or m.start() < best):
      best = m.start()
  return body[best:] if best is not None else body


def replay(fx):
  """Parse every real reply under the rules of its phase.

  Returns error strings.
  """
  errs = []
  for line in (Path(fx) / "index.txt").read_text().splitlines():
    if not line.strip():
      continue
    fname, phase, role = line.split()
    body = _strip_companion_log((Path(fx) / fname).read_text())
    if phase == "frame":
      if "MISSING QUESTIONS:" not in body or "SEED ITEMS:" not in body:
        errs.append("%s: frame sections missing" % fname)
    elif phase == "diverge":
      try:
        assign_ids("## Directions\n", body, "Directions")
      except ValueError as e:
        errs.append("%s: %s" % (fname, e))
    elif phase == "verify":
      n = len(re.findall(r"^FACTS I\d+:", body, re.M))
      facts = [l for l in body.splitlines() if l.startswith("- ")]
      bad = [l for l in facts if not FACT.match(l)]
      if n == 0 or bad or not re.search(r"^DISPUTE: ", body, re.M):
        errs.append(
            "%s: verify parse (%d blocks, %d bad fact lines)"
            % (fname, n, len(bad))
        )
    elif phase == "converge":
      if role == "reviser":
        if "ARTIFACT:" not in body or "ANSWERS:" not in body:
          errs.append("%s: reviser sections missing" % fname)
        elif not parse_artifact(_section(body, "ARTIFACT", ["ANSWERS"])):
          errs.append("%s: reviser artifact has no items" % fname)
      else:
        if not STANCE.search(body):
          errs.append("%s: malformed STANCE" % fname)
        obj = _section(
            body, "OBJECTIONS", ["REVIEW", "QUESTIONS", "ATTACKS TRIED"]
        )
        if (
            role == "advocate"
            and not OBJ.findall(obj)
            and "[advocate exhausted]" not in obj
        ):
          errs.append("%s: advocate neither objected nor exhausted" % fname)
  return errs


# ---------------------------------------------------------------- selftest
FIX_ARTIFACT = """## Options
### I1: Separate table
Reminders get their own table.
FACTS:
- VERIFIED(src/db.py:12 "CREATE TABLE events") events table exists
- ASSUMED nobody queries reminders by kind
### I2: Kind on events
A kind column on events.
FACTS:
- ASSUMED kind index is cheap
"""

FIX_TRANSCRIPT = """## R1 diverge driver role=none
ITEMS:
### T1: Separate table
Reminders get their own table.
## R1 diverge opus role=none
ITEMS: none
"""


def selftest():
  turns = parse_transcript(FIX_TRANSCRIPT)
  assert len(turns) == 2, turns
  assert (
      turns[0]["participant"] == "driver"
      and turns[1]["body"].strip() == "ITEMS: none"
  )
  items = parse_artifact(FIX_ARTIFACT)
  assert list(items) == ["I1", "I2"], list(items)
  assert items["I1"]["heading"] == "Options"
  assert items["I1"]["facts"][0]["kind"] == "VERIFIED"
  assert (
      items["I1"]["facts"][0]["citation"]
      == 'src/db.py:12 "CREATE TABLE events"'
  )
  assert items["I2"]["facts"][0]["kind"] == "ASSUMED"
  paren = parse_artifact(
      '## O\n### I1: X\nFACTS:\n- VERIFIED(a.py:1 "print(\\"x\\") % y") the'
      " claim\n"
  )
  assert (
      paren["I1"]["facts"][0]["citation"] == 'a.py:1 "print(\\"x\\") % y"'
  ), paren["I1"]["facts"]
  assert paren["I1"]["facts"][0]["claim"] == "the claim"
  assert validate_structure(items, turns) == []
  dup = parse_artifact(FIX_ARTIFACT.replace("### I2:", "### I1:"))
  assert any("duplicate" in e for e in validate_structure(dup, []))
  dangling = parse_transcript(
      "## R1 converge gemini role=none\nO1 (I9): nope\n"
  )
  assert any("missing I9" in e for e in validate_structure(items, dangling))
  alias = parse_transcript("## R1 converge gemini role=none\nsee T1\n")
  assert any("unresolved alias" in e for e in validate_structure(items, alias))
  frame_alias = parse_transcript(
      "## R1 frame gemini role=none\nSEED ITEMS:\n### T1: X\n"
  )
  assert (
      validate_structure(items, frame_alias) == []
  ), "frame turns may carry seed aliases"
  crit = parse_transcript(
      "## R1 diverge gemini role=none\nITEMS: none\nO1 (I1): bad\n"
  )
  assert any(
      "critique in diverge" in e for e in validate_structure(items, crit)
  )
  disp = parse_transcript("## R1 verify gemini role=none\nDISPUTE: I7\n")
  assert any(
      "disputes missing I7" in e for e in validate_structure(items, disp)
  )
  # assign-ids: aliases become the next unused ids, cross-refs rewritten
  body = "ITEMS:\n### T1: Parent\nNeeds T2.\n### T2: Child\nUnder T1.\n"
  new_art, mapping = assign_ids(FIX_ARTIFACT, body, "Options")
  assert mapping == {"T1": "I3", "T2": "I4"}, mapping
  assert (
      "### I3: Parent\nNeeds I4." in new_art
      and "### I4: Child\nUnder I3." in new_art
  )
  assert "T1" not in new_art and "T2" not in new_art
  # an alias inside a code span is not rewritten and is reported
  try:
    assign_ids(FIX_ARTIFACT, "ITEMS:\n### T1: X\nsee `T9`\n", "Options")
    assert False, "expected unresolved alias error"
  except ValueError as e:
    assert "T9" in str(e)
  # an alias BETWEEN two code spans is not inside one (found by the first real run: gemini wrote
  # `a.py:37 "x"`; T1 matters ... `b.py`, and the old regex matched closing-to-opening backtick)
  _, m2 = assign_ids(
      FIX_ARTIFACT,
      'ITEMS:\n### T1: X\ncited by `a.py:1 "x"`; T1 matters, see `b.py`.\n',
      "Options",
  )
  assert m2 == {"T1": "I3"}, m2
  # assign_ids on ITEMS: none with alias text returns empty dict without error
  art_none, m_none = assign_ids(
      FIX_ARTIFACT,
      "Examined T1 and T2 from past round.\nITEMS: none\n",
      "Options",
  )
  assert art_none == FIX_ARTIFACT and m_none == {}, (art_none, m_none)
  # diverge verdict: item count changed -> CONTINUE, unchanged -> EXIT, cap -> EXIT cap
  import tempfile

  d = Path(tempfile.mkdtemp())
  (d / "state.json").write_text(json.dumps({"caps": {"diverge": 2}}))
  (d / "artifact.prev.md").write_text(FIX_ARTIFACT)
  (d / "artifact.md").write_text(new_art)
  (d / "transcript.md").write_text("## R1 diverge driver role=none\n" + body)
  assert diverge_verdict(d) == "CONTINUE"
  (d / "artifact.prev.md").write_text(new_art)
  assert diverge_verdict(d) == "EXIT"
  (d / "artifact.prev.md").write_text(FIX_ARTIFACT)
  (d / "transcript.md").write_text("## R2 diverge driver role=none\n" + body)
  assert (
      diverge_verdict(d) == "EXIT cap"
  ), "a cap round that still added items is truncated, not converged"
  (d / "artifact.prev.md").write_text(new_art)
  assert (
      diverge_verdict(d) == "EXIT"
  ), "a cap round that added nothing converged; the cap is incidental"
  # frame verdict: both companions replied and Claude recorded dispositions -> EXIT
  df = Path(tempfile.mkdtemp())
  ALL_NONE = (
      "MISSING QUESTIONS: none\nCRITERIA: none\nSEED ITEMS: none\nQUESTIONS FOR"
      " HUMAN: none\n"
  )
  MERGE = "## R1 frame driver role=none\nADOPTED: none\n"
  (df / "transcript.md").write_text(
      "## R1 frame gemini role=none\nMISSING QUESTIONS: 1. what happens on a"
      " partial write\n## R1 frame opus role=none\nCRITERIA: cost is ranked"
      " above correctness\n"
  )
  assert frame_verdict(df) == "CONTINUE", "no merge on the record yet"
  (df / "transcript.md").write_text((df / "transcript.md").read_text() + MERGE)
  assert (
      frame_verdict(df) == "EXIT"
  ), "critiques from both companions -> debate-shaped"

  # every companion section empty or 'none' -> the task has one obvious shape, end the run
  dn = Path(tempfile.mkdtemp())
  (dn / "transcript.md").write_text(
      "## R1 frame gemini role=none\n"
      + ALL_NONE
      + "## R1 frame opus role=none\nnone\n"
      + MERGE
  )
  assert frame_verdict(dn) == "NOT-DEBATE-SHAPED"
  # one non-empty section from ONE companion is enough to keep going
  for section, value in (
      ("MISSING QUESTIONS", "1. what happens on a partial write"),
      ("CRITERIA", "cost is ranked above correctness"),
      ("SEED ITEMS", "### T1: do nothing\nKeep the current shape."),
      ("QUESTIONS FOR HUMAN", "is the 7-day refresh token acceptable"),
  ):
    (dn / "transcript.md").write_text(
        "## R1 frame gemini role=none\n"
        + ALL_NONE
        + "## R1 frame opus role=none\n%s: %s\n" % (section, value)
        + MERGE
    )
    assert frame_verdict(dn) == "EXIT", section
  # prose outside any section header is a signal, not emptiness: a malformed reply must not abort
  (dn / "transcript.md").write_text(
      "## R1 frame gemini role=none\n"
      + ALL_NONE
      + "## R1 frame opus role=none\nI think the criteria are in the wrong"
      " order"
      " but have no sections.\n"
      + MERGE
  )
  assert frame_verdict(dn) == "EXIT", "preamble-only reply"
  (dn / "transcript.md").write_text(
      "## R1 frame gemini role=none\n"
      + ALL_NONE
      + "## R1 frame opus role=none\nThe brief omits the partial-write case.\n"
      + ALL_NONE
      + MERGE
  )
  assert frame_verdict(dn) == "EXIT", "prose before all-none sections"
  # a bare 'none' body, and a body with nothing at all, are both empty
  (dn / "transcript.md").write_text(
      "## R1 frame gemini role=none\nnone\n## R1 frame opus role=none\n" + MERGE
  )
  assert frame_verdict(dn) == "NOT-DEBATE-SHAPED", "bare none and an empty body"
  # the abort is reachable only once the merge is on the record
  (dn / "transcript.md").write_text(
      "## R1 frame gemini role=none\n"
      + ALL_NONE
      + "## R1 frame opus role=none\nnone\n"
  )
  assert (
      frame_verdict(dn) == "CONTINUE"
  ), "an unmerged frame round is not an abort"
  # verify: a failing citation is downgraded; exit when no disputed item rests on ASSUMED
  d = Path(tempfile.mkdtemp())
  (d / "src").mkdir()
  (d / "src" / "db.py").write_text("\n" * 11 + "CREATE TABLE events\n")
  (d / "state.json").write_text(
      json.dumps({"caps": {"verify": 2}, "repo": str(d)})
  )
  (d / "artifact.md").write_text(FIX_ARTIFACT)
  (d / "transcript.md").write_text(
      "## R1 verify gemini role=none\nDISPUTE: none\n"
  )
  out = []
  assert verify_verdict(d, out) == "EXIT", out
  assert out == [], out
  (d / "transcript.md").write_text(
      "## R1 verify gemini role=none\nDISPUTE: I2\n"
  )
  assert verify_verdict(d, out) == "CONTINUE"
  (d / "transcript.md").write_text(
      "## R2 verify gemini role=none\nDISPUTE: I2\n"
  )
  assert (
      verify_verdict(d, out) == "EXIT cap"
  ), "a live dispute at the cap is truncated, not settled"
  (d / "transcript.md").write_text(
      "## R2 verify gemini role=none\nDISPUTE: none\n"
  )
  assert (
      verify_verdict(d, out) == "EXIT"
  ), "no live dispute at the cap round is a real exit"
  (d / "artifact.md").write_text(
      FIX_ARTIFACT.replace(
          'db.py:12 "CREATE TABLE events"', 'db.py:3 "CREATE TABLE events"'
      )
  )
  (d / "transcript.md").write_text(
      "## R1 verify gemini role=none\nDISPUTE: none\n"
  )
  out = []
  verify_verdict(d, out)
  assert out and out[0].startswith("DOWNGRADED I1"), out
  assert (
      "ASSUMED events table exists [downgraded:"
      in (d / "artifact.md").read_text()
  )

  # An artifact with no parseable fact must NOT read as a clean verify. Two distinct causes, and
  # the verdict names which: citations written inline in prose, versus no citation at all.
  (d / "transcript.md").write_text(
      "## R1 verify gemini role=none\nDISPUTE: none\n"
  )
  nofacts = (
      "## Options\n### I1: Separate table\nReminders get their own table.\n"
  )
  (d / "artifact.md").write_text(nofacts)
  v = verify_verdict(d, [])
  assert v.startswith("PROTOCOL-ERROR driver verify produced no fact"), v
  inline_art = (
      nofacts
      + 'The events table, VERIFIED(src/db.py:12 "CREATE TABLE events"),'
      " exists.\n"
  )
  (d / "artifact.md").write_text(inline_art)
  v = verify_verdict(d, [])
  assert "citation(s) written inline in prose" in v and v.startswith(
      "PROTOCOL-ERROR driver 1 "
  ), v
  # ...and a line merely DISCUSSING the syntax is not a citation: no path:line, so no match. A
  # debate ABOUT this skill writes exactly this line, and must not be failed for it.
  (d / "artifact.md").write_text(
      nofacts + "Tag each objection with `VERIFIED(...)` or `ASSUMED`.\n"
  )
  v = verify_verdict(d, [])
  assert v.startswith("PROTOCOL-ERROR driver verify produced no fact"), v
  # the guard is reached only when NO item has a fact: a normal artifact still exits cleanly
  (d / "artifact.md").write_text(FIX_ARTIFACT)
  assert (
      verify_verdict(d, []) == "EXIT"
  ), "the empty-facts guard must not fire on a real artifact"

  # a structural refusal records WHY in state.json, not just the word: a census of protocol errors
  # across fourteen debates could not attribute 8 of them because the reason only went to stdout
  (d / "state.json").write_text(json.dumps({"caps": {"converge": 3}}))
  (d / "artifact.md").write_text(FIX_ARTIFACT)
  (d / "transcript.md").write_text(
      "## R1 converge gemini role=reviser\nfixes `T7` in the artifact\n"
  )
  assert main([str(d), "converge"]) == 1
  last = json.loads((d / "state.json").read_text())["exits"][-1]
  assert (
      last[2].startswith("PROTOCOL-ERROR gemini ")
      and "unresolved alias" in last[2]
  ), last

  # converge fixtures: build a dir helper
  def conv(dirpath, rounds, prev_art, cur_art, cap=5):
    dp = Path(dirpath)
    dp.mkdir(exist_ok=True)
    (dp / "state.json").write_text(json.dumps({"caps": {"converge": cap}}))
    (dp / "artifact.prev.md").write_text(prev_art)
    (dp / "artifact.md").write_text(cur_art)
    (dp / "transcript.md").write_text(rounds)
    return converge_verdict(dp)

  REV = "## R%d converge %s role=reviser\nARTIFACT:\n%s\nANSWERS:\n%s\n"
  NON = (
      "## R%d converge %s role=%s\nSTANCE:"
      " %s\nOBJECTIONS:\n%s\nREVIEW:\n%s\nQUESTIONS:\nnone\n%s"
  )
  EXH = "none [advocate exhausted]"
  ATT = (
      "ATTACKS TRIED:\nI1: table adds a join, but the join is on a pk\nI2: kind"
      " column bloats events, measured small\nI1: migration cost, one"
      " statement\n"
  )
  ART2 = FIX_ARTIFACT.replace(
      "Reminders get their own table.",
      "Reminders get their own table, with a pk join.",
  )

  # R1: advocate objects O1 on I1, other accepts -> CONTINUE (advocate not exhausted)
  r1 = (
      REV % (1, "driver", FIX_ARTIFACT, "none")
      + NON
      % (
          1,
          "gemini",
          "advocate",
          "OBJECT",
          "O1 (I1) [advocate]: joins are slow",
          "none",
          "",
      )
      + NON % (1, "opus", "none", "ACCEPT", "none", "none", "")
  )
  base = Path(tempfile.mkdtemp())
  assert conv(base / "a", r1, FIX_ARTIFACT, FIX_ARTIFACT) == "CONTINUE"

  # regression pin, not a mutation-proved guard: a CONFIDENCE group between OBJECTIONS and
  # REVIEW must not disturb converge parsing. No mutation of _section kills this - OBJ ignores
  # unknown lines and REVIEW parses from its own section - so it only catches a LATER change
  # that makes converge parsing sensitive to an unknown section.
  r1_conf = r1.replace(
      "O1 (I1) [advocate]: joins are slow\n",
      "O1 (I1) [advocate]: joins are slow\nCONFIDENCE:\nCONFIDENCE O1: 0.4\n",
  )
  assert (
      r1_conf != r1
  ), "the CONFIDENCE group was not inserted - the NON template changed shape"
  assert conv(base / "conf", r1_conf, FIX_ARTIFACT, FIX_ARTIFACT) == "CONTINUE"

  # R2: reviser changes I1 (answers O1 by diff), new advocate exhausted, other accepts -> QUORUM
  r2 = r1 + (
      REV % (2, "gemini", ART2, "O1: I1 now states the join is on a pk")
      + NON % (2, "opus", "advocate", "ACCEPT", EXH, "O1 ANSWERED", ATT)
      + NON % (2, "driver", "none", "ACCEPT", "none", "O1 ANSWERED", "")
  )
  assert conv(base / "b", r2, FIX_ARTIFACT, ART2) == "QUORUM"

  # a reviser that rewrites the artifact without carrying an item's FACTS lines forward is a
  # protocol error naming the items, not a silent loss of the evidence the phase was built on
  STRIPPED = ART2.replace(
      '- VERIFIED(src/db.py:12 "CREATE TABLE events") events table exists\n', ""
  )
  assert (
      STRIPPED != ART2
  ), "the fixture no longer contains the line this case strips"
  r2f = r1 + (
      REV % (2, "gemini", STRIPPED, "O1: I1 now states the join is on a pk")
      + NON % (2, "opus", "advocate", "ACCEPT", EXH, "O1 ANSWERED", ATT)
      + NON % (2, "driver", "none", "ACCEPT", "none", "O1 ANSWERED", "")
  )
  v = conv(base / "bf", r2f, FIX_ARTIFACT, STRIPPED)
  assert v.startswith(
      "PROTOCOL-ERROR gemini reviser dropped FACTS lines from I1;"
  ), v
  # dropping the whole ITEM is not dropping its facts: a reviser may still cut or merge an item
  CUT = (
      "## Options\n" + FIX_ARTIFACT.split("### I2:")[0].split("## Options\n")[1]
  )
  assert "I2" not in CUT and "I1" in CUT, CUT
  r2g = r1 + (
      REV % (2, "gemini", CUT, "O1: I1 now states the join is on a pk")
      + NON % (2, "opus", "advocate", "ACCEPT", EXH, "O1 ANSWERED", ATT)
      + NON % (2, "driver", "none", "ACCEPT", "none", "O1 ANSWERED", "")
  )
  v = conv(base / "bg", r2g, FIX_ARTIFACT, CUT)
  assert "dropped FACTS" not in v, (
      "cutting an item must not read as dropping its facts: %s" % v
  )

  # R2 variant: answer neither changes I1 nor cites -> O1 open -> CONTINUE
  r2b = r1 + (
      REV % (2, "gemini", FIX_ARTIFACT, "O1: joins are fine")
      + NON % (2, "opus", "advocate", "ACCEPT", EXH, "O1 ANSWERED", ATT)
      + NON % (2, "driver", "none", "ACCEPT", "none", "O1 ANSWERED", "")
  )
  assert conv(base / "c", r2b, FIX_ARTIFACT, FIX_ARTIFACT) == "CONTINUE"

  # R2 variant: answer cites a VERIFIED fact of I1 -> answered -> QUORUM
  r2c = r1 + (
      REV
      % (
          2,
          "gemini",
          FIX_ARTIFACT,
          'O1: see VERIFIED(src/db.py:12 "CREATE TABLE events")',
      )
      + NON % (2, "opus", "advocate", "ACCEPT", EXH, "O1 ANSWERED", ATT)
      + NON % (2, "driver", "none", "ACCEPT", "none", "O1 ANSWERED", "")
  )
  assert conv(base / "d", r2c, FIX_ARTIFACT, FIX_ARTIFACT) == "QUORUM"

  # EVADED wins over ANSWERED; one evasion reopens -> CONTINUE
  r2d = r1 + (
      REV % (2, "gemini", ART2, "O1: I1 changed")
      + NON % (2, "opus", "advocate", "ACCEPT", EXH, "O1 EVADED", ATT)
      + NON % (2, "driver", "none", "ACCEPT", "none", "O1 ANSWERED", "")
  )
  assert conv(base / "e", r2d, FIX_ARTIFACT, ART2) == "CONTINUE"

  # second evasion of O1 -> STUCK
  r3 = r2d + (
      REV % (3, "opus", ART2, "O1: I1 changed again")
      + NON % (3, "driver", "advocate", "ACCEPT", EXH, "O1 EVADED", ATT)
      + NON % (3, "gemini", "none", "ACCEPT", "none", "O1 ANSWERED", "")
  )
  assert conv(base / "f", r3, ART2, ART2) == "STUCK evaded twice O1"

  # new advocate objection in the same round as the other ACCEPTs -> not QUORUM
  r2e = r1 + (
      REV % (2, "gemini", ART2, "O1: I1 changed")
      + NON
      % (
          2,
          "opus",
          "advocate",
          "OBJECT",
          "O2 (I2) [advocate]: kind is a stringly type",
          "O1 ANSWERED",
          "",
      )
      + NON % (2, "driver", "none", "ACCEPT", "none", "O1 ANSWERED", "")
  )
  assert conv(base / "g", r2e, FIX_ARTIFACT, ART2) == "CONTINUE"

  # same open non-advocate objection two rounds running -> STUCK
  s1 = (
      REV % (1, "driver", FIX_ARTIFACT, "none")
      + NON % (1, "gemini", "advocate", "ACCEPT", EXH, "none", ATT)
      + NON
      % (1, "opus", "none", "OBJECT", "O1 (I2): kind is stringly", "none", "")
  )
  s2 = s1 + (
      REV % (2, "gemini", FIX_ARTIFACT, "O1: disagree")
      + NON % (2, "opus", "advocate", "ACCEPT", EXH, "none", ATT)
      + NON
      % (
          2,
          "driver",
          "none",
          "OBJECT",
          "O1 (I2): kind is stringly",
          "O1 EVADED",
          "",
      )
  )
  assert (
      conv(base / "h", s2, FIX_ARTIFACT, FIX_ARTIFACT)
      == "STUCK same objections"
  )

  # cap
  assert conv(base / "i", r1, FIX_ARTIFACT, FIX_ARTIFACT, cap=1) == "STUCK cap"
  # quorum reached IN the cap round is QUORUM, not STUCK (d-2026-09-16-54beb5 hit this at round 5)
  assert conv(base / "i2", r2, FIX_ARTIFACT, ART2, cap=2) == "QUORUM"
  assert (
      conv(base / "i3", r2b, FIX_ARTIFACT, FIX_ARTIFACT, cap=2) == "STUCK cap"
  )

  # same-as: valid reference resolves with its target; circular is a protocol error
  t1 = (
      REV % (1, "driver", FIX_ARTIFACT, "none")
      + NON
      % (
          1,
          "gemini",
          "advocate",
          "OBJECT",
          "O1 (I1) [advocate]: slow",
          "none",
          "",
      )
      + NON % (1, "opus", "none", "OBJECT", "O2 (I1): slow join", "none", "")
  )
  t2 = t1 + (
      REV % (2, "gemini", ART2, "O1: I1 changed\nO2: same as O1")
      + NON
      % (2, "opus", "advocate", "ACCEPT", EXH, "O1 ANSWERED\nO2 ANSWERED", ATT)
      + NON
      % (2, "driver", "none", "ACCEPT", "none", "O1 ANSWERED\nO2 ANSWERED", "")
  )
  assert conv(base / "j", t2, FIX_ARTIFACT, ART2) == "QUORUM"
  t2bad = t1 + (
      REV % (2, "gemini", ART2, "O1: same as O2\nO2: same as O1")
      + NON % (2, "opus", "advocate", "ACCEPT", EXH, "none", ATT)
      + NON % (2, "driver", "none", "ACCEPT", "none", "none", "")
  )
  assert conv(base / "k", t2bad, FIX_ARTIFACT, ART2).startswith(
      "PROTOCOL-ERROR gemini"
  )
  t2miss = t1 + (
      REV % (2, "gemini", ART2, "O2: same as O1")
      + NON % (2, "opus", "advocate", "ACCEPT", EXH, "none", ATT)
      + NON % (2, "driver", "none", "ACCEPT", "none", "none", "")
  )
  assert conv(base / "p", t2miss, FIX_ARTIFACT, ART2).startswith(
      "PROTOCOL-ERROR gemini bad reference"
  )

  # a non-advocate wearing the advocate tag is a protocol error (it would hide a real blocker)
  tagged = (
      REV % (1, "driver", FIX_ARTIFACT, "none")
      + NON
      % (
          1,
          "gemini",
          "advocate",
          "OBJECT",
          "O1 (I1) [advocate]: slow",
          "none",
          "",
      )
      + NON
      % (1, "opus", "none", "OBJECT", "O2 (I2) [advocate]: hidden", "none", "")
  )
  assert (
      conv(base / "o", tagged, FIX_ARTIFACT, FIX_ARTIFACT)
      == "PROTOCOL-ERROR opus advocate tag mismatch O2"
  )

  # a diff-based answer from round 2 still counts when the script runs in round 3
  r3ok = r2 + (
      REV % (3, "opus", ART2, "none")
      + NON % (3, "driver", "advocate", "ACCEPT", EXH, "none", ATT)
      + NON % (3, "gemini", "none", "ACCEPT", "none", "none", "")
  )
  assert conv(base / "q", r3ok, FIX_ARTIFACT, ART2) == "QUORUM"

  # 2-item artifact converging when both items are attacked -> QUORUM
  bad_att = r1 + (
      REV % (2, "gemini", ART2, "O1: I1 changed")
      + NON
      % (
          2,
          "opus",
          "advocate",
          "ACCEPT",
          EXH,
          "O1 ANSWERED",
          "ATTACKS TRIED:\nI1: a\nI2: b\n",
      )
      + NON % (2, "driver", "none", "ACCEPT", "none", "O1 ANSWERED", "")
  )
  assert conv(base / "l", bad_att, FIX_ARTIFACT, ART2) == "QUORUM"

  # duplicate attacks (I1, I1, I1) rejected
  dup_att = r1 + (
      REV % (2, "gemini", ART2, "O1: I1 changed")
      + NON
      % (
          2,
          "opus",
          "advocate",
          "ACCEPT",
          EXH,
          "O1 ANSWERED",
          "ATTACKS TRIED:\nI1: a\nI1: b\nI1: c\n",
      )
      + NON % (2, "driver", "none", "ACCEPT", "none", "O1 ANSWERED", "")
  )
  assert (
      conv(base / "dup_att", dup_att, FIX_ARTIFACT, ART2)
      == "PROTOCOL-ERROR opus attacks tried (1 of 2 required)"
  )

  # 0-item artifact rejected
  empty_art = "## Options\n"
  zero_r1 = (
      REV % (1, "driver", empty_art, "none")
      + NON
      % (
          1,
          "opus",
          "advocate",
          "ACCEPT",
          EXH,
          "none",
          "ATTACKS TRIED:\nnone\n",
      )
      + NON % (1, "gemini", "none", "ACCEPT", "none", "none", "")
  )
  assert (
      conv(base / "zero_item", zero_r1, empty_art, empty_art)
      == "PROTOCOL-ERROR opus empty artifact cannot converge"
  )

  # active round with 1 turn returning CONTINUE
  active_r2 = r1 + REV % (2, "gemini", ART2, "O1: I1 changed")
  assert conv(base / "active_r2", active_r2, FIX_ARTIFACT, ART2) == "CONTINUE"
  lazy = (
      REV % (1, "driver", FIX_ARTIFACT, "none")
      + NON % (1, "gemini", "advocate", "ACCEPT", "none", "none", "")
      + NON % (1, "opus", "none", "ACCEPT", "none", "none", "")
  )
  assert (
      conv(base / "m", lazy, FIX_ARTIFACT, FIX_ARTIFACT)
      == "PROTOCOL-ERROR gemini advocate must object"
  )

  # malformed STANCE line
  mal = (
      REV % (1, "driver", FIX_ARTIFACT, "none")
      + "## R1 converge gemini role=advocate\nSTANCE:"
      " MAYBE\nOBJECTIONS:\nnone\nREVIEW:\nnone\nQUESTIONS:\nnone\n"
      + NON % (1, "opus", "none", "ACCEPT", "none", "none", "")
  )
  assert (
      conv(base / "n", mal, FIX_ARTIFACT, FIX_ARTIFACT)
      == "PROTOCOL-ERROR gemini malformed STANCE"
  )

  # I25: an ITEMS: mention inside a preamble sentence must not start the block
  pre = (
      "I looked at the ITEMS: list and here is mine.\nITEMS:\n### T1:"
      " Real\nBody.\n"
  )
  new_art, m = assign_ids(FIX_ARTIFACT, pre, "Options")
  assert "I looked at" not in new_art and m == {"T1": "I3"}, (m, new_art)
  # I28: gaps are legal; allocation is max id plus one so a deleted id is never reused
  gapped = FIX_ARTIFACT.replace(
      "### I2: Kind on events\nA kind column on events.\nFACTS:\n- ASSUMED kind"
      " index is cheap\n",
      "",
  )
  assert validate_structure(parse_artifact(gapped), []) == []
  with_gap = gapped + "### I3: Third\nBody.\n"
  assert validate_structure(parse_artifact(with_gap), []) == []
  _, m2 = assign_ids(with_gap, "ITEMS:\n### T1: Fourth\nBody.\n", "Options")
  assert m2 == {"T1": "I4"}, m2
  dupid = FIX_ARTIFACT + "### I1: Again\nBody.\n"
  assert any(
      "duplicate" in e for e in validate_structure(parse_artifact(dupid), [])
  )
  # I23: an identical fact line twice is downgraded twice, not a crash
  d = Path(tempfile.mkdtemp())
  (d / "state.json").write_text(
      json.dumps({"caps": {"verify": 2}, "repo": str(d)})
  )
  dup_art = FIX_ARTIFACT.replace(
      "- ASSUMED nobody queries reminders by kind\n",
      '- VERIFIED(src/db.py:3 "CREATE TABLE events") twice\n-'
      ' VERIFIED(src/db.py:3 "CREATE TABLE events") twice\n',
  )
  (d / "artifact.md").write_text(dup_art)
  (d / "transcript.md").write_text(
      "## R1 verify gemini role=none\nDISPUTE: none\n"
  )
  out = []
  assert verify_verdict(d, out) == "EXIT"
  assert (d / "artifact.md").read_text().count("[downgraded:") == 3, out
  # I26: a comma-separated dispute list still names the items
  (d / "artifact.md").write_text(FIX_ARTIFACT)
  assert disputed_ids(
      "I20 ASSUMED claim that a blocking pull starves the loop; I3 too"
  ) == ["I20", "I3"]
  assert disputed_ids(" none ") == [] and disputed_ids("I2, I1") == ["I2", "I1"]
  (d / "transcript.md").write_text(
      "## R1 verify gemini role=none\nDISPUTE: I2, I1\n"
  )
  assert verify_verdict(d, []) == "CONTINUE"
  # I24: a multi-line answer keeps its citation on the second line
  two = (
      REV % (1, "driver", FIX_ARTIFACT, "none")
      + NON
      % (
          1,
          "gemini",
          "advocate",
          "OBJECT",
          "O1 (I1) [advocate]: slow",
          "none",
          "",
      )
      + NON % (1, "opus", "none", "ACCEPT", "none", "none", "")
  )
  two += (
      REV
      % (
          2,
          "gemini",
          FIX_ARTIFACT,
          (
              'O1: the join is fine.\nSee VERIFIED(src/db.py:12 "CREATE TABLE'
              ' events") for the table.'
          ),
      )
      + NON % (2, "opus", "advocate", "ACCEPT", EXH, "O1 ANSWERED", ATT)
      + NON % (2, "driver", "none", "ACCEPT", "none", "O1 ANSWERED", "")
  )
  assert conv(base / "u", two, FIX_ARTIFACT, FIX_ARTIFACT) == "QUORUM"
  # every phase verdict is recorded in state for the record generator
  d = Path(tempfile.mkdtemp())
  (d / "state.json").write_text(json.dumps({"caps": {"diverge": 2}}))
  (d / "artifact.prev.md").write_text(FIX_ARTIFACT)
  (d / "artifact.md").write_text(FIX_ARTIFACT)
  (d / "transcript.md").write_text(
      "## R1 diverge driver role=none\nITEMS: none\n"
  )
  assert main([str(d), "diverge"]) == 0
  assert json.loads((d / "state.json").read_text())["exits"] == [
      ["diverge", 1, "EXIT"]
  ]
  # the shim must skip gemini's log line that quotes the reply's first words
  logged = (
      "[gemini] Assistant message captured: ITEMS: ### T1: Fake\n[gemini] Turn"
      " completed.\nITEMS:\n### T1: Real\nBody.\n"
  )
  assert _strip_companion_log(logged).startswith(
      "ITEMS:\n### T1: Real"
  ), _strip_companion_log(logged)
  fxroot = Path(__file__).resolve().parent.parent / "fixtures"
  for fx in sorted(fxroot.iterdir()) if fxroot.is_dir() else []:
    if (fx / "index.txt").is_file():
      errs = replay(fx)
      assert errs == [], "\n".join(errs)

  # two reviewers using the same new id for different items in one round -> protocol error
  coll = (
      REV % (1, "driver", FIX_ARTIFACT, "none")
      + NON
      % (
          1,
          "gemini",
          "advocate",
          "OBJECT",
          "O1 (I1) [advocate]: slow",
          "none",
          "",
      )
      + NON % (1, "opus", "none", "OBJECT", "O1 (I2): stringly", "none", "")
  )
  assert (
      conv(base / "r", coll, FIX_ARTIFACT, FIX_ARTIFACT)
      == "PROTOCOL-ERROR opus objection id collision O1"
  )
  # same id, same item, different text from the two parallel reviewers -> still a collision
  coll2 = (
      REV % (1, "driver", FIX_ARTIFACT, "none")
      + NON
      % (
          1,
          "gemini",
          "advocate",
          "OBJECT",
          "O1 (I1) [advocate]: slow",
          "none",
          "",
      )
      + NON
      % (1, "opus", "none", "OBJECT", "O1 (I1): migration cost", "none", "")
  )
  assert (
      conv(base / "r2", coll2, FIX_ARTIFACT, FIX_ARTIFACT)
      == "PROTOCOL-ERROR opus objection id collision O1"
  )
  # an id reused later for a different item -> protocol error
  reuse = r1 + (
      REV % (2, "gemini", ART2, "O1: I1 changed")
      + NON
      % (
          2,
          "opus",
          "advocate",
          "OBJECT",
          "O1 (I2) [advocate]: now about kind",
          "O1 ANSWERED",
          "",
      )
      + NON % (2, "driver", "none", "ACCEPT", "none", "O1 ANSWERED", "")
  )
  assert (
      conv(base / "s", reuse, FIX_ARTIFACT, ART2)
      == "PROTOCOL-ERROR opus objection id reused for another item O1"
  )
  # EVADED on an objection the reviser did not answer this round -> protocol error, not an evasion
  stale = r1 + (
      REV % (2, "gemini", ART2, "none")
      + NON % (2, "opus", "advocate", "ACCEPT", EXH, "O1 EVADED", ATT)
      + NON % (2, "driver", "none", "ACCEPT", "none", "none", "")
  )
  # 2-party debate test: Gemini driver vs Claude Opus companion
  twoway = base / "twoway"
  twoway.mkdir(parents=True, exist_ok=True)
  (twoway / "state.json").write_text(
      json.dumps({
          "driver": "gemini",
          "companions": ["opus"],
          "caps": {"diverge": 2, "verify": 2, "converge": 3},
      })
  )
  (twoway / "artifact.md").write_text(FIX_ARTIFACT)
  (twoway / "transcript.md").write_text(
      "## R1 frame opus role=none\n"
      "MISSING QUESTIONS: 1. scale limit\n\n"
      "## R1 frame gemini role=none\n"
      "ADOPTED: 1. scale limit\n"
  )
  assert frame_verdict(twoway) == "EXIT", frame_verdict(twoway)

  (twoway / "artifact.prev.md").write_text(FIX_ARTIFACT)
  twoway_r1 = REV % (1, "gemini", FIX_ARTIFACT, "none") + NON % (
      1,
      "opus",
      "advocate",
      "OBJECT",
      "O1 (I1) [advocate]: joins slow",
      "none",
      "",
  )
  twoway_r2 = twoway_r1 + (
      REV % (2, "opus", ART2, "O1: I1 changed")
      + NON % (2, "gemini", "advocate", "ACCEPT", EXH, "O1 ANSWERED", ATT)
  )
  assert conv(twoway / "conv", twoway_r2, FIX_ARTIFACT, ART2) == "QUORUM"

  # Negative tests: driver self-simulating companion without provenance
  sim_dir = base / "sim_test"
  sim_dir.mkdir(parents=True, exist_ok=True)
  (sim_dir / "calls").mkdir(parents=True, exist_ok=True)
  (sim_dir / "state.json").write_text(
      json.dumps({
          "driver": "gemini",
          "companions": ["opus"],
          "session_id": "sess-xyz",
      })
  )
  (sim_dir / "artifact.md").write_text(FIX_ARTIFACT)
  (sim_dir / "transcript.md").write_text(
      "## R1 frame opus role=none\nMISSING QUESTIONS: none\n\n## R1 frame"
      " gemini role=none\nADOPTED: none\n"
  )
  # Write simulated output with NO .prov.json
  (sim_dir / "calls" / "01-opus.out.md").write_text("MISSING QUESTIONS: none\n")
  errs_sim = validate_companion_provenance(
      sim_dir, parse_transcript((sim_dir / "transcript.md").read_text())
  )
  assert any("driver self-simulated" in e for e in errs_sim), errs_sim

  # Negative tests: same model family between driver and companion
  same_fam_dir = base / "same_fam_test"
  same_fam_dir.mkdir(parents=True, exist_ok=True)
  (same_fam_dir / "state.json").write_text(
      json.dumps({
          "driver": "gemini",
          "driver_family": "gemini",
          "companions": ["gemini"],
          "companion_families": ["gemini"],
      })
  )
  (same_fam_dir / "artifact.md").write_text(FIX_ARTIFACT)
  (same_fam_dir / "transcript.md").write_text(
      "## R1 frame gemini-auditor role=none\nMISSING QUESTIONS: none\n"
  )
  errs_same = validate_companion_provenance(
      same_fam_dir,
      parse_transcript((same_fam_dir / "transcript.md").read_text()),
  )
  assert any("same model family" in e for e in errs_same), errs_same

  # Positive test: verified provenance metadata passes
  good_dir = base / "good_test"
  good_dir.mkdir(parents=True, exist_ok=True)
  (good_dir / "calls").mkdir(parents=True, exist_ok=True)
  mock_brain = base / "mock_brain"
  os.environ["DEBATE_SELFTEST"] = "1"
  os.environ["DEBATE_MOCK_BRAIN_ROOT"] = str(mock_brain)
  subagent_conv_id = "subagent-opus-conv-456"
  subagent_tr_dir = mock_brain / subagent_conv_id
  subagent_tr_dir.mkdir(parents=True, exist_ok=True)
  (subagent_tr_dir / "transcript.jsonl").write_text('{"event": "start"}\n')

  (good_dir / "state.json").write_text(
      json.dumps({
          "driver": "gemini",
          "driver_family": "gemini",
          "companions": ["opus"],
          "session_id": "parent-123",
      })
  )
  (good_dir / "artifact.md").write_text(FIX_ARTIFACT)
  (good_dir / "transcript.md").write_text(
      "## R1 frame opus role=none\nMISSING QUESTIONS: none\n\n## R1 frame"
      " gemini role=none\nADOPTED: none\n"
  )
  (good_dir / "calls" / "01-opus.out.md").write_text(
      "MISSING QUESTIONS: none\n"
  )
  (good_dir / "calls" / "01-opus.prov.json").write_text(
      json.dumps({
          "companion": "opus",
          "call": 1,
          "parent_session_id": "parent-123",
          "subagent_conversation_id": subagent_conv_id,
          "family": "anthropic",
          "attestation_status": "CONFIRMED",
          "timestamp": time.time(),
      })
  )
  errs_good = validate_companion_provenance(
      good_dir, parse_transcript((good_dir / "transcript.md").read_text())
  )
  assert errs_good == [], errs_good

  # Frame exit test: NOT-DEBATE-SHAPED when companion finds no faults or seed items
  not_deb = base / "not_debate_shaped"
  not_deb.mkdir(parents=True, exist_ok=True)
  (not_deb / "state.json").write_text(
      json.dumps({
          "driver": "gemini",
          "companions": ["opus"],
      })
  )
  (not_deb / "transcript.md").write_text(
      "## R1 frame opus role=none\nMISSING QUESTIONS: none\nCRITERIA:"
      " none\nSEED ITEMS: none\nQUESTIONS FOR HUMAN: none\n\n## R1 frame gemini"
      " role=none\nADOPTED: none\n"
  )
  assert frame_verdict(not_deb) == "NOT-DEBATE-SHAPED", frame_verdict(not_deb)

  # Empty-turn guard and stale-round guard tests
  d_empty = Path(tempfile.mkdtemp())
  (d_empty / "state.json").write_text(
      json.dumps({
          "driver": "gemini",
          "companions": ["opus"],
          "calls": 2,
          "caps": {"diverge": 2, "verify": 2, "converge": 3},
      })
  )
  (d_empty / "artifact.md").write_text("## Options\n### I1: One\nBody.\n")
  (d_empty / "artifact.prev.md").write_text("## Options\n### I1: One\nBody.\n")
  (d_empty / "transcript.md").write_text(
      "## R1 frame opus role=none\nMISSING QUESTIONS:\n1. Q?\n"
  )
  assert diverge_verdict(d_empty).startswith(
      "PROTOCOL-ERROR driver no diverge turns"
  )
  assert verify_verdict(d_empty, []).startswith(
      "PROTOCOL-ERROR driver no verify turns"
  )

  # Stale-round guard test: verify R1 exits CONTINUE at call 2; then calls increases to 3 without R2 in transcript.md
  (d_empty / "artifact.md").write_text(
      "## Options\n### I1: One\nBody.\nFACTS:\n- ASSUMED claim\n"
  )
  (d_empty / "transcript.md").write_text(
      "## R1 verify opus role=none\nFACTS I1:\n- ASSUMED claim\nDISPUTE: I1\n\n"
      "## R1 verify gemini role=none\nFACTS I1:\n- ASSUMED claim\nDISPUTE: I1\n"
  )
  v_r1 = verify_verdict(d_empty, [])
  assert v_r1 == "CONTINUE", v_r1
  _record_exit(d_empty, "verify", v_r1)
  # Idempotent re-run at same call count (2) must STILL return CONTINUE
  assert verify_verdict(d_empty, []) == "CONTINUE"
  # Now simulate a new companion call recorded (calls: 2 -> 3) while transcript.md still only has R1 verify
  st_e = json.loads((d_empty / "state.json").read_text())
  st_e["calls"] = 3
  (d_empty / "state.json").write_text(json.dumps(st_e))
  v_stale = verify_verdict(d_empty, [])
  assert v_stale.startswith(
      "PROTOCOL-ERROR driver transcript.md is still at verify R1"
  ), v_stale

  # Reviser turn missing ARTIFACT section when prev_items has items
  missing_rev_art = (
      REV % (1, "driver", "", "none")
      + NON
      % (
          1,
          "opus",
          "advocate",
          "ACCEPT",
          EXH,
          "none",
          "ATTACKS TRIED:\nI1: a\nI2: b\nI1: c\n",
      )
      + NON % (1, "gemini", "none", "ACCEPT", "none", "none", "")
  )
  assert (
      conv(
          base / "missing_rev_art", missing_rev_art, FIX_ARTIFACT, FIX_ARTIFACT
      )
      == "PROTOCOL-ERROR driver reviser turn in R1 has empty or missing"
      " ARTIFACT section"
  )

  # TOCTOU drift between artifact.md and transcript.md R1 reviser ARTIFACT
  toctou_r1 = (
      REV % (1, "driver", ART2, "none")
      + NON
      % (
          1,
          "opus",
          "advocate",
          "ACCEPT",
          EXH,
          "none",
          "ATTACKS TRIED:\nI1: a\nI2: b\nI1: c\n",
      )
      + NON % (1, "gemini", "none", "ACCEPT", "none", "none", "")
  )
  assert (
      conv(base / "toctou_r1", toctou_r1, FIX_ARTIFACT, FIX_ARTIFACT)
      == "PROTOCOL-ERROR driver artifact.md is out of sync with R1 reviser"
      " ARTIFACT in transcript.md; run debate_report.py preserve-facts"
      " <dir> to synchronize artifact.md"
  )

  print("debate_exit selftest: ok")


def _record_exit(d, phase, verdict):
  p = Path(d) / "state.json"
  st = json.loads(p.read_text())
  turns = _turns(d, phase)
  rnd = max((t["round"] for t in turns), default=0)
  st.setdefault("exits", []).append([phase, rnd, verdict])
  if verdict == "CONTINUE" and rnd > 0:
    st.setdefault("exit_call_counts", {})["%s:R%d" % (phase, rnd)] = st.get(
        "calls", 0
    )
  p.write_text(json.dumps(st))


def main(argv):
  if not argv or argv[0] in ("-h", "--help"):
    print(
        "Usage:\n"
        "  debate_exit.py <dir> <frame|diverge|verify|converge>\n"
        "  debate_exit.py assign-ids <dir> <heading> <items_file>\n"
        "  debate_exit.py --replay <dir>\n"
        "  debate_exit.py --selftest"
    )
    return 0
  if argv == ["--selftest"]:
    selftest()
    return 0
  if argv and argv[0] == "--replay":
    errs = replay(argv[1])
    for e in errs:
      print(e)
    print("replay: %s" % ("ok" if not errs else "%d failures" % len(errs)))
    return 0 if not errs else 1
  if argv and argv[0] == "assign-ids":
    d, heading, items_file = Path(argv[1]), argv[2], Path(argv[3])
    art = (d / "artifact.md").read_text()
    try:
      new_art, mapping = assign_ids(art, items_file.read_text(), heading)
    except ValueError as e:
      print("PROTOCOL-ERROR %s %s" % (items_file.name, e))
      return 1
    (d / "artifact.md").write_text(new_art)
    print(" ".join("%s=%s" % kv for kv in mapping.items()) or "none")
    return 0
  d, phase = Path(argv[0]), argv[1]
  items = parse_artifact((d / "artifact.md").read_text())
  turns = parse_transcript((d / "transcript.md").read_text())
  errs = validate_structure(items, turns)
  prov_errs = validate_companion_provenance(d, turns)
  if prov_errs:
    errs.extend(prov_errs)
  if errs:
    last = turns[-1]["participant"] if turns else "driver"
    verdict = "PROTOCOL-ERROR %s %s" % (last, "; ".join(errs))
    _record_exit(d, phase, verdict)
    print(verdict)
    return 1
  if phase == "frame":
    v = frame_verdict(d)
    _record_exit(d, phase, v)
    print(v)
    return 1 if v.startswith("PROTOCOL-ERROR") else 0
  if phase == "diverge":
    v = diverge_verdict(d)
    _record_exit(d, phase, v)
    print(v)
    return 1 if v.startswith("PROTOCOL-ERROR") else 0
  if phase == "verify":
    out = []
    v = verify_verdict(d, out)
    _record_exit(d, phase, v)
    for line in out:
      print(line)
    print(v)
    return 1 if v.startswith("PROTOCOL-ERROR") else 0
  if phase == "converge":
    v = converge_verdict(d)
    _record_exit(d, phase, v)
    print(v)
    return 1 if v.startswith("PROTOCOL-ERROR") else 0
  print("PROTOCOL-ERROR driver phase %s not implemented" % phase)
  return 2


if __name__ == "__main__":
  sys.exit(main(sys.argv[1:]))
