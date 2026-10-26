#!/usr/bin/env python3
"""debate_report.py - the driver's bookkeeping, scripted.

merge-facts <dir> <turn-file>...   merge FACTS I<k>: blocks from the given turns
into artifact.md
record <dir> --task T --outcome O  compute the debate.log line from the files
and append it
ledger <dir>                       frame-critique ledger plus brief diff, for
report.md
report <dir> --task T --outcome O  write report.md: every mechanical section
from the files, one
                                   `## Decision` placeholder left for the driver
outcome <dir> --built yes|no --kept yes|no|unknown --worth yes|no
                                   the three human outcome fields, written into
                                   the debate's own line
"""

import datetime
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from debate_exit import parse_transcript, parse_artifact, OBJ, REVIEW, _section  # noqa: E402


def strip_facts(art):
  out, skip = [], False
  for line in art.splitlines():
    if line.strip() == "FACTS:":
      skip = True
      continue
    if skip and line.startswith("- "):
      continue
    skip = False
    out.append(line)
  return "\n".join(out) + "\n"


def merge_facts(d, turn_files):
  d = Path(d)
  facts = {}
  blk = re.compile(r"^FACTS (I\d+):\s*$")
  for src in turn_files:
    cur = None
    for line in Path(src).read_text().splitlines():
      m = blk.match(line)
      if m:
        cur = m.group(1)
        facts.setdefault(cur, [])
        continue
      if cur and line.startswith("- "):
        if line not in facts[cur]:
          facts[cur].append(line)
      elif line.startswith("DISPUTE") or line.startswith("## "):
        cur = None
  for k, lines in facts.items():
    ver = {
        re.sub(r'^- VERIFIED\(.*?"\) ', "", l).strip().rstrip(".").lower()
        for l in lines
        if l.startswith("- VERIFIED(")
    }
    facts[k] = [
        l
        for l in lines
        if not (
            l.startswith("- ASSUMED ")
            and l[10:].strip().rstrip(".").lower() in ver
        )
    ]
  base = strip_facts((d / "artifact.md").read_text())
  out, item = [], None
  for line in base.splitlines():
    m = re.match(r"^### (I\d+): ", line)
    if m:
      if item and item in facts:
        out.append("FACTS:")
        out.extend(facts[item])
      item = m.group(1)
    elif line.startswith("## ") and item and item in facts:
      out.append("FACTS:")
      out.extend(facts[item])
      item = None
    out.append(line)
  if item and item in facts:
    out.append("FACTS:")
    out.extend(facts[item])
  (d / "artifact.md").write_text("\n".join(out) + "\n")
  return len(facts)


def _extract_raw_item_facts(art_text: str) -> dict:
  """Extract verbatim '- VERIFIED(...)' and '- ASSUMED ...' lines per item I<k>."""
  item_facts = {}
  cur_item = None
  in_facts = False
  for line in art_text.splitlines():
    m = re.match(r"^### (I\d+): ", line)
    if m:
      cur_item = m.group(1)
      item_facts.setdefault(cur_item, [])
      in_facts = False
      continue
    if line.startswith("## "):
      cur_item = None
      in_facts = False
      continue
    if cur_item and line.strip() == "FACTS:":
      in_facts = True
      continue
    if cur_item and in_facts:
      if line.startswith("- "):
        if line not in item_facts[cur_item]:
          item_facts[cur_item].append(line)
      elif line.strip():
        in_facts = False
  return item_facts


def _fact_stem(line: str) -> str:
  """Normalize a fact line to detect reformatted duplicates of the same claim/citation."""
  m_ver = re.match(r"^- VERIFIED\(([^:\s]+:\d+)\b.*?\)\s*(.*)$", line.strip())
  if m_ver:
    return "VER:%s:%s" % (
        m_ver.group(1),
        re.sub(r"\s+", " ", m_ver.group(2)).strip().rstrip(".").lower(),
    )
  if line.strip().startswith("- ASSUMED "):
    claim = re.sub(r"\s*\[downgraded:.*?\]\s*$", "", line.strip()[10:])
    return "ASS:%s" % re.sub(r"\s+", " ", claim).strip().rstrip(".").lower()
  return re.sub(r"\s+", " ", line.strip()).lower()


def _merge_preserved_facts_into_text(curr_text: str, prev_text: str):
  """Superset-merge verbatim FACTS lines from prev_text into retained items of curr_text."""
  prev_facts = _extract_raw_item_facts(prev_text)
  curr_facts = _extract_raw_item_facts(curr_text)
  merged_facts = {}
  restored_items = []

  for k in curr_facts:
    p_lines = list(prev_facts.get(k, []))
    c_lines = list(curr_facts.get(k, []))
    if not p_lines and not c_lines:
      continue
    p_stems = {_fact_stem(l) for l in p_lines}
    combined = list(p_lines)
    for c_line in c_lines:
      if c_line in combined:
        continue
      if _fact_stem(c_line) in p_stems:
        # Reformatted version of a fact already in prev_text; keep prev_text's verbatim line
        continue
      combined.append(c_line)
    merged_facts[k] = combined
    if combined != c_lines:
      restored_items.append(k)

  base = strip_facts(curr_text)
  out, item = [], None
  for line in base.splitlines():
    m = re.match(r"^### (I\d+): ", line)
    if m:
      if item and merged_facts.get(item):
        out.append("FACTS:")
        out.extend(merged_facts[item])
      item = m.group(1)
    elif line.startswith("## ") and item and merged_facts.get(item):
      out.append("FACTS:")
      out.extend(merged_facts[item])
      item = None
    out.append(line)
  if item and merged_facts.get(item):
    out.append("FACTS:")
    out.extend(merged_facts[item])
  return "\n".join(out) + "\n", restored_items


def _atomic_write_report_file(target: Path, content: str) -> None:
  import tempfile

  target = Path(target)
  target.parent.mkdir(parents=True, exist_ok=True)
  with tempfile.NamedTemporaryFile(
      "w", dir=str(target.parent), delete=False, encoding="utf-8"
  ) as tf:
    tf.write(content)
    tf.flush()
    os.fsync(tf.fileno())
    tmp_name = tf.name
  os.replace(tmp_name, str(target))


def preserve_facts(d):
  """Synchronize artifact.md from the latest reviser ARTIFACT in transcript.md (if present) while preserving verbatim FACTS from artifact.prev.md / prior round."""
  d = Path(d)
  prev_path = d / "artifact.prev.md"
  art_path = d / "artifact.md"
  if not prev_path.is_file() or not art_path.is_file():
    return []

  prev_text = prev_path.read_text(errors="replace")
  tr_path = d / "transcript.md"
  if tr_path.is_file():
    raw_tr = tr_path.read_text(errors="replace")
    turns = [
        t
        for t in parse_transcript(raw_tr)
        if t["phase"] == "converge" and t["role"] == "reviser"
    ]
    if len(turns) >= 2:
      prior_art = _section(turns[-2]["body"], "ARTIFACT", ["ANSWERS"]).strip()
      if prior_art:
        prev_text = prior_art + "\n"

    rev_matches = list(
        re.finditer(r"^## R\d+ converge \S+ role=reviser\s*$", raw_tr, re.M)
    )
    if rev_matches:
      last_m = rev_matches[-1]
      tail = raw_tr[last_m.end() :]
      next_hdr = re.search(r"^## R\d+ ", tail, re.M)
      sec_end = last_m.end() + next_hdr.start() if next_hdr else len(raw_tr)
      sec_body = raw_tr[last_m.end() : sec_end]
      rev_art = _section(sec_body, "ARTIFACT", ["ANSWERS"]).strip()
      if not rev_art or not parse_artifact(rev_art):
        print(
            "PROTOCOL-ERROR: latest reviser turn in transcript.md has missing"
            " or empty ARTIFACT section",
            file=sys.stderr,
        )
        raise SystemExit(1)

      synced_art, restored = _merge_preserved_facts_into_text(
          rev_art + "\n", prev_text
      )
      _atomic_write_report_file(art_path, synced_art)

      art_idx = sec_body.find("ARTIFACT:")
      ans_idx = sec_body.find("\nANSWERS:")
      if art_idx != -1:
        end_idx = (
            ans_idx if (ans_idx != -1 and ans_idx > art_idx) else len(sec_body)
        )
        new_sec = (
            sec_body[: art_idx + len("ARTIFACT:")]
            + "\n"
            + synced_art.strip()
            + (
                "\n" + sec_body[end_idx:].lstrip("\n")
                if end_idx < len(sec_body)
                else "\n"
            )
        )
        _atomic_write_report_file(
            tr_path, raw_tr[: last_m.end()] + new_sec + raw_tr[sec_end:]
        )
      return restored

  curr_text = art_path.read_text(errors="replace")
  new_art, restored = _merge_preserved_facts_into_text(curr_text, prev_text)
  _atomic_write_report_file(art_path, new_art)
  return restored


def _root():
  return Path(
      os.environ.get(
          "DEBATE_STATE_ROOT",
          os.path.expanduser("~/.gemini/agent/state/debate"),
      )
  )


def _ledger(st=None):
  """Where a NEW row goes: one file beside the run directories.

  Rows used to go to `<repo_live>/.gemini/state/debate.log`, which is per
  CHECKOUT rather than
  per repo, because a debate is usually driven from a worktree. That produced
  five shards for
  thirteen debates, three of them inside `.gemini/worktrees/*` and one carrying
  a row that also
  exists elsewhere (`d-2026-09-17-e6b638`), since the duplicate guard only ever
  read the shard it
  was about to write. Two separate attempts to count the evidence base got the
  wrong answer from
  a partial glob. New rows go in one place; old rows stay where they are and
  `_shards` finds them.
  """
  return _root() / "debate.log"


def _legacy(st):
  """The per-checkout path this debate's row would have gone to before the ledger was centralised.

  `repo_live` is the live checkout; the `repo` fallback is for debates recorded
  before that key existed, whose rows sit under the pinned path
  (d-2026-09-16-a1f3c7 crashed outcome() without it).
  """
  return (
      Path(st.get("repo_live") or st["repo"])
      / ".gemini"
      / "state"
      / "debate.log"
  )


def _shards(st):
  """Every debate.log that could hold a row, newest scheme first: the central one, this debate's

  own legacy path, and the legacy path of every other run directory. Read from
  the run dirs rather
  than by walking ~/Projects, so a shard inside a worktree that has since been
  deleted is still
  found, and one belonging to an unrelated repo is not invented.
  """
  out, seen = [], set()
  for p in [_ledger(), _legacy(st)] + [_legacy(s) for s in _sibling_states()]:
    if p not in seen:
      seen.add(p)
      if p.exists():
        out.append(p)
  return out


def _sibling_states():
  for sj in sorted(_root().glob("d-*/state.json")):
    try:
      yield json.loads(sj.read_text())
    except (OSError, ValueError):
      continue


def record(d, task, outcome, tiebreak=None):
  d = Path(d)
  st = json.loads((d / "state.json").read_text())
  tr = parse_transcript((d / "transcript.md").read_text())
  art = (d / "artifact.md").read_text()
  items = parse_artifact(art)
  phases = {}
  for phase, rnd, verdict in st.get("exits", []):
    phases[phase] = {"rounds": rnd, "exit": verdict}
  raised, answered, evaded = {}, set(), set()
  adv_exhausted_round = None
  first_art = None
  for t in tr:
    if t["phase"] != "converge":
      continue
    if t["role"] == "reviser":
      if first_art is None:
        first_art = _section(t["body"], "ARTIFACT", ["ANSWERS"])
      continue
    obj = _section(
        t["body"], "OBJECTIONS", ["REVIEW", "QUESTIONS", "ATTACKS TRIED"]
    )
    for o, k, tag, _ in OBJ.findall(obj):
      raised.setdefault(o, (k, bool(tag)))
    if (
        t["role"] == "advocate"
        and "[advocate exhausted]" in obj
        and adv_exhausted_round is None
    ):
      adv_exhausted_round = t["round"]
    for o, mark in REVIEW.findall(
        _section(t["body"], "REVIEW", ["QUESTIONS", "ATTACKS TRIED"])
    ):
      (answered if mark == "ANSWERED" else evaded).add(o)
  # "changed the artifact" is a diff, not a REVIEW mark: the objection's item reads differently in
  # the final artifact than in the first reviser artifact of the debate
  first_items = parse_artifact(first_art) if first_art else {}

  def _txt(it):
    return None if it is None else (it["title"], it["body"].strip())

  changed = {
      o
      for o, (k, _) in raised.items()
      if _txt(first_items.get(k)) != _txt(items.get(k))
  }
  adv_changed = any(tag and o in changed for o, (k, tag) in raised.items())
  added = sum(1 for t in tr if t["phase"] == "diverge" and "### T" in t["body"])
  rec = {
      "id": st["id"],
      "task": task,
      "brief_sha256": (
          hashlib.sha256((d / "brief.md").read_bytes()).hexdigest()[:16]
      ),
      "mode": st.get("mode"),
      "pinned": st.get("pinned"),
      "phases": phases,
      "calls": st["calls"],
      "items_added": added,
      "items_final": len(items),
      "verified": sum(
          1 for l in art.splitlines() if l.startswith("- VERIFIED(")
      ),
      "assumed": sum(1 for l in art.splitlines() if l.startswith("- ASSUMED")),
      "objections": {
          "raised": len(raised),
          "answered": len(answered),
          "changed_artifact": len(changed),
          "evaded": len(evaded),
      },
      "advocate_changed_artifact": adv_changed,
      "advocate_exhausted_round": adv_exhausted_round,
      "outcome": outcome,
      "tiebreak": tiebreak,
      "dir": str(d),
      "started": st.get("started"),
      "finished": (
          datetime.datetime.now(datetime.timezone.utc).strftime(
              "%Y-%m-%dT%H:%M:%SZ"
          )
      ),
  }
  log = _ledger(st)
  log.parent.mkdir(parents=True, exist_ok=True)
  # fail closed on a re-run: a duplicate row silently doubled two debates on 2026-09-17 and was
  # repaired by hand. outcome() already refuses a doubled row, but only once someone runs it, and
  # the corruption happened before that. Every shard is checked, not just the one being written:
  # the guard used to read one file, which is how d-2026-09-17-e6b638 ended up in two of them.
  for shard in _shards(st):
    for line in shard.read_text().splitlines():
      if not line.strip():
        continue
      try:
        row = json.loads(line)
      except (ValueError, json.JSONDecodeError):
        continue
      if row.get("id") == st["id"]:
        raise SystemExit(
            "%s already has a row in %s; record is not idempotent, so refusing"
            % (st["id"], shard)
        )
  with open(log, "a") as f:
    f.write(json.dumps(rec) + "\n")
  return rec


OUTCOME = {
    "built": ("yes", "no"),
    "kept": ("yes", "no", "unknown"),
    "worth": ("yes", "no"),
}


def outcome(d, built, kept, worth):
  """Rewrite this debate's debate.log line with the three fields only a human can fill, after the

  decision has (or has not) been built: did the artifact change what gets built,
  was the change
  kept, and was it worth it against one review. Every other field stays as
  record() wrote it.
  """
  d = Path(d)
  vals = {"built": built, "kept": kept, "worth": worth}
  for k, v in vals.items():
    if v not in OUTCOME[k]:
      raise SystemExit(
          "%s must be one of %s, not %r" % (k, "|".join(OUTCOME[k]), v)
      )
  st = json.loads((d / "state.json").read_text())
  # the row is edited where it actually lives: a debate recorded before the ledger was centralised
  # still has its only row in a per-checkout shard, and moving it would drop the labels already on it
  found = [
      (
          s,
          [
              i
              for i, l in enumerate(s.read_text().splitlines())
              if json.loads(l)["id"] == st["id"]
          ],
      )
      for s in _shards(st)
  ]
  found = [(s, h) for s, h in found if h]
  if len(found) != 1 or len(found[0][1]) != 1:
    raise SystemExit(
        "expected exactly 1 debate.log line for %s, found %s"
        % (st["id"], {str(s): len(h) for s, h in found} or "none")
    )
  log, hits = found[0]
  lines = log.read_text().splitlines()
  rec = json.loads(lines[hits[0]])
  rec.update(vals)
  lines[hits[0]] = json.dumps(rec)
  log.write_text("\n".join(lines) + "\n")
  return rec


def ledger(d):
  d = Path(d)
  lines = [
      l
      for l in (d / "transcript.md").read_text().splitlines()
      if l.startswith(("ADOPTED: ", "REJECTED: "))
  ]
  prompt_file = None
  calls_dir = d / "calls"
  st = {}
  state_file = d / "state.json"
  if state_file.is_file():
    try:
      st = json.loads(state_file.read_text())
    except (OSError, ValueError):
      st = {}

  companions = [
      c.lower() for c in st.get("companions", []) if isinstance(c, str)
  ]
  frame_files = sorted(calls_dir.glob("frame-*.prompt.md"))
  indexed_files = sorted(calls_dir.glob("01-*.prompt.md"))

  for comp in companions:
    for f in frame_files:
      if comp in f.name.lower():
        prompt_file = f
        break
    if prompt_file:
      break
    for f in indexed_files:
      if comp in f.name.lower():
        prompt_file = f
        break
    if prompt_file:
      break

  if not prompt_file:
    if frame_files:
      prompt_file = frame_files[0]
    elif indexed_files:
      prompt_file = indexed_files[0]

  if not prompt_file or not prompt_file.is_file():
    raise FileNotFoundError(
        "No frame prompt file found in %s matching 'frame-*.prompt.md' or"
        " '01-*.prompt.md' (companions: %s)" % (calls_dir, companions or "none")
    )

  prompt = prompt_file.read_text()
  orig = (
      prompt.split("--- BRIEF ---\n", 1)[1]
      if "--- BRIEF ---\n" in prompt
      else ""
  )
  diff = "\n".join(
      difflib.unified_diff(
          orig.splitlines(),
          (d / "brief.md").read_text().splitlines(),
          "brief as dispatched to frame",
          "final brief",
          lineterm="",
      )
  )
  return (
      "## Frame-critique ledger\n"
      + "\n".join(lines)
      + "\n\n## Brief diff\n```\n"
      + diff
      + "\n```\n"
  )


def _objections(tr):
  """Every objection raised in converge with who raised it, the round, and the latest reviser answer."""
  raised, answers = {}, {}
  for t in tr:
    if t["phase"] != "converge":
      continue
    if t["role"] == "reviser":
      for line in _section(t["body"], "ANSWERS", []).splitlines():
        m = re.match(r"^(O\d+): (.+)$", line)
        if m:
          answers[m.group(1)] = (m.group(2), t["participant"], t["round"])
      continue
    obj = _section(
        t["body"], "OBJECTIONS", ["REVIEW", "QUESTIONS", "ATTACKS TRIED"]
    )
    for o, k, tag, text in OBJ.findall(obj):
      raised.setdefault(o, (k, bool(tag), t["participant"], t["round"], text))
  return raised, answers


def _votes_and_confidence(tr, d):
  """Closing votes, per-objection confidence, and any prompt file a confidence line leaked into."""
  votes = []
  for t in tr:
    if t["phase"] != "closing":
      continue
    m = re.search(r"^VOTE: (.+)$", t["body"], re.M)
    votes.append(
        "- %s: %s"
        % (
            t["participant"],
            m.group(1).strip() if m else "no vote on the record",
        )
    )
  confs = []
  for t in tr:
    # capture the value loosely and judge it here: a malformed number must stay ON the record
    # flagged, never crash the report and never vanish from it
    for oid, raw in re.findall(r"^CONFIDENCE (O\d+):(.*)$", t["body"], re.M):
      val = raw.strip()
      try:
        f = float(val)
      except ValueError:
        val, flag = val or "(blank)", " UNREADABLE"
      else:
        flag = " LOW" if f < 0.5 else ""
        if not 0.0 <= f <= 1.0:
          flag = " OUT-OF-RANGE"
      confs.append("- %s (%s): %s%s" % (oid, t["participant"], val, flag))
  leaks = []
  for p in sorted((Path(d) / "calls").glob("*.prompt.md")):
    try:
      text = p.read_text()
    except OSError:
      leaks.append("unreadable:%s" % p.name)
      continue
    # only a VALUE line is a leak: every converge prompt MUST carry the format template
    # line `CONFIDENCE O<n>: <0.0 to 1.0>`, and prose may use the word, so a bare
    # ^CONFIDENCE match flags every prompt file ever written (it did: 34/34 in d-2026-09-17-49d220)
    if re.search(r"^CONFIDENCE O\d+:[ \t]*[0-9]", text, re.M):
      leaks.append(p.name)
  return votes, confs, leaks or ["none"]


def report(d, task, outcome):
  """Write report.md from the files.

  The driver then replaces the one `## Decision` placeholder.
  """
  d = Path(d)
  st = json.loads((d / "state.json").read_text())
  tr = parse_transcript((d / "transcript.md").read_text())
  art = (d / "artifact.md").read_text()
  items = parse_artifact(art)
  now = datetime.datetime.now(datetime.timezone.utc)
  started_raw = st.get("started")
  started = None
  if started_raw and isinstance(started_raw, str):
    try:
      started = datetime.datetime.strptime(
          started_raw, "%Y-%m-%dT%H:%M:%SZ"
      ).replace(tzinfo=datetime.timezone.utc)
    except (ValueError, TypeError):
      try:
        started = datetime.datetime.fromisoformat(
            started_raw.replace("Z", "+00:00")
        )
      except (ValueError, TypeError):
        started = None

  if started:
    mins = int((now - started).total_seconds() // 60)
    start_str = started.strftime("%H:%M")
  else:
    mins = 0
    start_str = (
        started_raw[11:16]
        if isinstance(started_raw, str) and len(started_raw) >= 16
        else "--:--"
    )
  out = [
      "# Debate report %s" % st["id"],
      "",
      "Task: %s" % task,
      "Mode: %s. Repo pinned to %s for every citation check."
      % (st.get("mode"), (st.get("pinned") or "")[:7]),
      "",
      "## Phases",
  ]
  for phase, rnd, verdict in st.get("exits", []):
    if verdict not in ("CONTINUE",):
      out.append("- %s: %d round(s), %s" % (phase, rnd, verdict))
  out.append(
      "- calls: %d of %d; wall clock about %d minutes (%s to %s UTC); driver"
      " hand recoveries: %d; assumed human answers: %d"
      % (
          st["calls"],
          st["cap"],
          mins,
          start_str,
          now.strftime("%H:%M"),
          sum(
              1
              for l in (d / "transcript.md").read_text().splitlines()
              if l.startswith("FACT (driver)")
          ),
          sum(
              1
              for l in (d / "transcript.md").read_text().splitlines()
              if l.startswith("ASSUMED HUMAN ANSWER")
          ),
      )
  )
  out += [
      "",
      "## Outcome: %s" % outcome,
      "",
      "## Decision",
      (
          "<driver: summarize the final artifact: rank 1, why it beat the"
          " alternatives, what is not built, the flip conditions; on STUCK add"
          " `Driver recommends:`>"
      ),
      "",
  ]
  raised, answers = _objections(tr)
  out.append("## Objections and resolutions")
  for o in sorted(raised, key=lambda x: int(x[1:])):
    k, adv, who, rnd, text = raised[o]
    a = answers.get(o)
    res = "%s (%s R%d)" % (a[0], a[1], a[2]) if a else "OPEN"
    out.append(
        "- %s (%s)%s [%s R%d]: %s Resolution: %s"
        % (
            o,
            k,
            " [advocate]" if adv else "",
            who,
            rnd,
            text[:160] + ("..." if len(text) > 160 else ""),
            res,
        )
    )
  if not raised:
    out.append("- none raised")
  votes, confs, leaks = _votes_and_confidence(tr, d)
  if votes:
    out += ["", "## Closing votes"] + votes
  if confs:
    out += ["", "## Confidence on the record"] + confs
  out += ["", "## Confidence leaks into prompts: %s" % ", ".join(leaks), ""]
  out += ["", "## Driver hand recoveries (FACT lines on the transcript)"]
  facts = [
      l
      for l in (d / "transcript.md").read_text().splitlines()
      if l.startswith("FACT (driver)")
  ]
  out += ["- " + l for l in facts] or ["- none"]
  out += [
      "",
      (
          "## ASSUMED facts and assumed human answers (runtime and design"
          " claims no line can show)"
      ),
  ]
  for k, it in items.items():
    for f in it["facts"]:
      if f["kind"] == "ASSUMED":
        out.append("- %s: %s" % (k, f["claim"]))
  out += [
      l
      for l in (d / "transcript.md").read_text().splitlines()
      if l.startswith("ASSUMED HUMAN ANSWER")
  ]
  closing = [t for t in tr if t["phase"] == "closing"]
  if closing:
    out += ["", "## Closing statements"]
    for t in closing:
      out += ["### %s" % t["participant"], t["body"].strip(), ""]
  prev = d / "artifact.prev.md"
  if prev.is_file():
    a = [l for l in prev.read_text().splitlines() if not l.startswith("- ")]
    b = [l for l in art.splitlines() if not l.startswith("- ")]
    diff = "\n".join(
        difflib.unified_diff(
            a,
            b,
            "artifact after verify (before converge R1)",
            "final artifact",
            lineterm="",
            n=0,
        )
    )
    out += [
        "",
        "## Diff: artifact after verify versus final artifact (facts stripped)",
        "```",
        diff,
        "```",
    ]
  # I12 enforcement surface: the obligation SKILL.md:121 states in prose has been ignored in
  # five of eight debates, so the report ends with the command already filled in.
  pending = [
      "",
      "## Outcome fields: NOT YET FILLED",
      "",
      "Once this decision has been acted on (or deliberately not), run:",
      "",
      "    python3 %s/debate_report.py outcome %s --built yes|no --kept"
      " yes|no|unknown --worth yes|no" % (Path(__file__).resolve().parent, d),
      "",
      "Until then this debate is not evidence either way, in either direction.",
  ]
  # Model Attestation & Provenance section
  calls_dir = d / "calls"
  prov_files = (
      sorted(calls_dir.glob("*.prov.json")) if calls_dir.is_dir() else []
  )
  att_lines = ["", "## Model Attestation & Provenance"]
  if prov_files:
    att_lines.append(
        "| Call | Companion | Model Slug | Model Enum | Family | Attestation |"
    )
    att_lines.append("| :--- | :--- | :--- | :--- | :--- | :--- |")
    for pf in prov_files:
      try:
        pdata = json.loads(pf.read_text())
        att_lines.append(
            "| %s | %s | %s | %s | %s | %s |"
            % (
                pdata.get("call", "-"),
                pdata.get("companion", "-"),
                pdata.get("model_slug", "-"),
                pdata.get("model_enum", "-"),
                pdata.get("family", "-"),
                pdata.get("attestation_status", "CONFIRMED"),
            )
        )
      except Exception:
        continue
  else:
    att_lines.append("- none recorded")
  out += att_lines

  out += [
      "",
      ledger(d),
      "## Final brief",
      (d / "brief.md").read_text(),
      "## Final artifact",
      art,
  ]
  out += pending
  out += ["", "Decided in debate %s" % st["id"], ""]
  text = "\n".join(out)
  (d / "report.md").write_text(text)
  return text


def selftest():
  import tempfile

  d = Path(tempfile.mkdtemp())
  (d / "calls").mkdir()
  art = "## Directions\n### I1: A\nBody.\n\n## Assumptions\n"
  (d / "artifact.md").write_text(art)
  (d / "brief.md").write_text("# brief v2\n")
  (d / "calls" / "frame-gemini.prompt.md").write_text(
      "x\n--- BRIEF ---\n# brief v1\n"
  )
  t1 = d / "t1.md"
  t1.write_text(
      'FACTS I1:\n- VERIFIED(a.py:1 "x") the claim\n- ASSUMED other\nDISPUTE:'
      " none\n"
  )
  t2 = d / "t2.md"
  t2.write_text(
      'FACTS I1:\n- ASSUMED the claim\n- VERIFIED(a.py:1 "x") the'
      " claim\nDISPUTE: none\n"
  )
  assert merge_facts(d, [t1, t2]) == 1
  merged = (d / "artifact.md").read_text()
  assert (
      merged.count("- VERIFIED(") == 1
      and "- ASSUMED other" in merged
      and "- ASSUMED the claim" not in merged
  ), merged
  assert (
      merge_facts(d, [t1]) == 1
      and (d / "artifact.md").read_text().count("FACTS:") == 1
  ), "re-merge must not duplicate blocks"
  # two items under one heading each get their own block (probe from the round 2 gate)
  (d / "artifact.md").write_text(
      "## Directions\n### I1: A\nBody A.\n\n### I2: B\nBody B.\n\n##"
      " Assumptions\n"
  )
  t3 = d / "t3.md"
  t3.write_text(
      "FACTS I1:\n- ASSUMED a1\nFACTS I2:\n- ASSUMED b1\nDISPUTE: none\n"
  )
  assert merge_facts(d, [t3]) == 2
  both = (d / "artifact.md").read_text()
  assert (
      both.index("- ASSUMED a1")
      < both.index("### I2: B")
      < both.index("- ASSUMED b1")
  ), both
  (d / "artifact.md").write_text(art)
  tr = (
      "## R1 frame driver role=none\nADOPTED: q1\nREJECTED: c4\n"
      "## R1 diverge gemini role=none\nITEMS:\n### T1: A\nBody.\n"
      "## R1 converge driver role=reviser\nARTIFACT:\n"
      + art
      + "ANSWERS:\nnone\n## R1 converge gemini role=advocate\nSTANCE:"
      " OBJECT\nOBJECTIONS:\nO1 (I1) [advocate]:"
      " slow\nREVIEW:\nnone\nQUESTIONS:\nnone\n## R1 converge opus"
      " role=none\nSTANCE:"
      " ACCEPT\nOBJECTIONS:\nnone\nREVIEW:\nnone\nQUESTIONS:\nnone\n## R2"
      " converge gemini role=reviser\nARTIFACT:\n"
      + art.replace("Body.", "Body, now with a pk join.")
      + "ANSWERS:\nO1: changed\n## R2 converge opus role=advocate\nSTANCE:"
      " ACCEPT\nOBJECTIONS:\nnone [advocate exhausted]\nREVIEW:\nO1"
      " ANSWERED\nQUESTIONS:\nnone\nATTACKS TRIED:\nI1: a\nI1: b\nI1: c\n##"
      " R2 converge driver role=none\nSTANCE:"
      " ACCEPT\nOBJECTIONS:\nnone\nREVIEW:\nO1 ANSWERED\nQUESTIONS:\nnone\n"
  )
  (d / "transcript.md").write_text(tr)
  (d / "artifact.md").write_text(
      art.replace("Body.", "Body, now with a pk join.")
  )  # the final artifact carries the change
  repo = d / "repo"
  repo.mkdir()
  os.environ["DEBATE_STATE_ROOT"] = str(d / "state_root")
  (d / "state.json").write_text(
      json.dumps({
          "id": "d-test",
          "repo": str(d),
          "repo_live": str(repo),
          "pinned": "abc",
          "calls": 7,
          "cap": 27,
          "mode": "attended",
          "exits": [
              ["diverge", 1, "EXIT"],
              ["verify", 1, "EXIT"],
              ["converge", 2, "QUORUM"],
          ],
          "started": "2026-01-01T00:00:00Z",
      })
  )
  rec = record(d, "t", "QUORUM")
  assert rec["phases"]["converge"] == {"rounds": 2, "exit": "QUORUM"}, rec
  assert rec["objections"] == {
      "raised": 1,
      "answered": 1,
      "changed_artifact": 1,
      "evaded": 0,
  }, rec
  assert (
      rec["advocate_changed_artifact"] is True
      and rec["advocate_exhausted_round"] == 2
  )
  assert (
      rec["items_added"] == 1
      and rec["items_final"] == 1
      and rec["verified"] == 0
      and rec["calls"] == 7
  )
  # an ANSWERED mark alone is not a change: same transcript, final artifact identical to the first.
  # record() now refuses a second row for the same id, so this recomputation runs under a fresh id.
  (d / "artifact.md").write_text(art)
  _st = json.loads((d / "state.json").read_text())
  (d / "state.json").write_text(json.dumps(dict(_st, id="d-test2")))
  rec0 = record(d, "t", "QUORUM")
  assert (
      rec0["objections"]["changed_artifact"] == 0
      and rec0["objections"]["answered"] == 1
      and rec0["advocate_changed_artifact"] is False
  ), rec0
  # rows go to the central ledger beside the run dirs, not into the checkout the debate was driven
  # from, which is what produced five shards for thirteen debates
  assert not (
      repo / ".gemini" / "state" / "debate.log"
  ).exists(), "a row must not go into the repo"
  assert _ledger().read_text().count("\n") == 2
  (d / "state.json").write_text(json.dumps(_st))
  # outcome rewrites only this debate's line, in place, and refuses a value outside its domain
  log = _ledger()
  log.write_text(
      json.dumps({"id": "d-other", "calls": 3})
      + "\n"
      + log.read_text().splitlines()[0]
      + "\n"
  )
  rec1 = outcome(d, "yes", "unknown", "no")
  assert (rec1["built"], rec1["kept"], rec1["worth"]) == (
      "yes",
      "unknown",
      "no",
  ) and rec1["calls"] == 7, rec1
  rows = [json.loads(l) for l in log.read_text().splitlines()]
  assert (
      rows[0] == {"id": "d-other", "calls": 3}
      and rows[1]["built"] == "yes"
      and len(rows) == 2
  ), rows
  rec2 = outcome(d, "no", "no", "yes")
  assert (rec2["built"], rec2["kept"], rec2["worth"]) == (
      "no",
      "no",
      "yes",
  ) and len(log.read_text().splitlines()) == 2
  try:
    outcome(d, "maybe", "yes", "yes")
    raise AssertionError("a value outside the domain must be refused")
  except SystemExit as e:
    assert "built must be one of yes|no" in str(e), e
  # a row written before the ledger was centralised still lives in a per-checkout shard, and it is
  # labelled THERE: rewriting it anywhere else would strip the fields a human already filled in.
  # The `repo` fallback covers debates older still, recorded before repo_live existed at all.
  legacy = repo / ".gemini" / "state" / "debate.log"
  legacy.parent.mkdir(parents=True)
  legacy.write_text(json.dumps({"id": "d-legacy", "calls": 5}) + "\n")
  (d / "state.json").write_text(
      json.dumps(
          {k: v for k, v in _st.items() if k != "repo_live"}
          | {"id": "d-legacy", "repo": str(repo)}
      )
  )
  rec3 = outcome(d, "yes", "yes", "yes")
  assert (rec3["built"], rec3["kept"], rec3["worth"]) == (
      "yes",
      "yes",
      "yes",
  ) and rec3["calls"] == 5, rec3
  assert (
      len(legacy.read_text().splitlines()) == 1
  ), "the legacy row is rewritten in place, not moved"
  assert (
      len(log.read_text().splitlines()) == 2
  ), "and nothing is appended to the central ledger"
  # the same id in two shards is the corruption the old one-file guard let through: report it
  # rather than picking one, since the two rows can disagree about the labels
  log.write_text(
      log.read_text() + json.dumps({"id": "d-legacy", "calls": 5}) + "\n"
  )
  try:
    outcome(d, "no", "no", "no")
    raise AssertionError(
        "a row in two shards must be refused, not silently resolved"
    )
  except SystemExit as e:
    assert "expected exactly 1 debate.log line for d-legacy" in str(e), e
  # and record refuses an id that exists in ANY shard, not only the one it is about to write
  log.write_text("".join(l + "\n" for l in log.read_text().splitlines()[:-1]))
  try:
    record(d, "t", "QUORUM")
    raise AssertionError("record must refuse an id already in a legacy shard")
  except SystemExit as e:
    assert "already has a row in %s" % legacy in str(e), e
  (d / "state.json").write_text(
      json.dumps(_st)
  )  # back to d-test for the rest of the selftest
  (d / "state.json").write_text(json.dumps(_st))
  led = ledger(d)
  assert (
      "ADOPTED: q1" in led
      and "REJECTED: c4" in led
      and "-# brief v1" in led
      and "+# brief v2" in led
  ), led
  # ledger() resolves frame prompt dynamically for opus as well as gemini
  (d / "calls" / "frame-gemini.prompt.md").unlink()
  (d / "calls" / "frame-opus.prompt.md").write_text(
      "x\n--- BRIEF ---\n# brief v1 opus\n"
  )
  _st_opus = dict(_st, companions=["opus"])
  (d / "state.json").write_text(json.dumps(_st_opus))
  led_opus = ledger(d)
  assert "-# brief v1 opus" in led_opus and "+# brief v2" in led_opus, led_opus
  # ledger() raises FileNotFoundError with diagnostic message if no prompt file is found
  (d / "calls" / "frame-opus.prompt.md").unlink()
  try:
    ledger(d)
    raise AssertionError(
        "ledger() must raise FileNotFoundError when prompt missing"
    )
  except FileNotFoundError as e:
    assert "No frame prompt file found" in str(e) and "opus" in str(e), e
  # restore frame-gemini.prompt.md and state for the rest of selftest
  (d / "calls" / "frame-gemini.prompt.md").write_text(
      "x\n--- BRIEF ---\n# brief v1\n"
  )
  (d / "state.json").write_text(json.dumps(_st))
  # report: mechanical sections from the files, one placeholder for the driver, the record's numbers
  (d / "artifact.prev.md").write_text(art)
  (d / "artifact.md").write_text(
      art.replace("Body.", "Body, now with a pk join.")
  )
  rep = report(d, "the task", "QUORUM")
  assert rep.startswith("# Debate report d-test\n") and rep.rstrip().endswith(
      "Decided in debate d-test"
  ), rep[:80]
  assert (
      "## Outcome: QUORUM" in rep
      and rep.count("## Decision") == 1
      and "<driver:" in rep
  )
  assert (
      "- converge: 2 round(s), QUORUM" in rep
      and "calls: 7 of 27" in rep
      and "driver hand recoveries: 0" in rep
  ), rep
  assert (
      "O1 (I1) [advocate] [gemini R1]: slow Resolution: changed (gemini R2)"
      in rep
  ), rep
  assert (
      "+Body, now with a pk join." in rep
      and "-Body." in rep
      and "ADOPTED: q1" in rep
      and "# brief v2" in rep
  ), rep
  assert (d / "report.md").read_text() == rep
  # an unanswered objection is OPEN, a closing turn is printed
  (d / "transcript.md").write_text(
      tr
      + "## R3 converge gemini role=advocate\nSTANCE: OBJECT\nOBJECTIONS:\nO2"
      " (I1) [advocate]: late\nREVIEW:\nnone\nQUESTIONS:\nnone\n## R1 closing"
      " opus role=none\nopus says so.\n"
  )
  rep2 = report(d, "the task", "STUCK")
  assert (
      "O2 (I1) [advocate] [gemini R3]: late Resolution: OPEN" in rep2
      and "### opus\nopus says so." in rep2
  ), rep2
  # a closing turn with no VOTE line is named as such in the rendered report
  assert "## Closing votes\n- opus: no vote on the record" in rep2, rep2

  # closing votes, confidence and the leak check all come off the files
  dv = Path(tempfile.mkdtemp())
  (dv / "calls").mkdir()
  (dv / "transcript.md").write_text(
      "## R1 converge gemini role=advocate\nSTANCE: OBJECT\nOBJECTIONS:\nO1"
      " (I1) [advocate]: weak.\nCONFIDENCE:\nCONFIDENCE O1:"
      " 0.4\nREVIEW:\nnone\nQUESTIONS:\nnone\n## R1 closing gemini role=none\nI"
      " recommend I2.\nVOTE: I2 > I1\n## R1 closing opus role=none\nI recommend"
      " I1 and will not vote.\n"
  )
  (dv / "calls" / "converge1-opus.prompt.md").write_text(
      "--- OPEN OBJECTIONS ---\nO1 (I1): weak.\n"
  )
  votes, confs, leaks = _votes_and_confidence(
      parse_transcript((dv / "transcript.md").read_text()), dv
  )
  assert votes == ["- gemini: I2 > I1", "- opus: no vote on the record"], votes
  assert confs == ["- O1 (gemini): 0.4 LOW"], confs
  assert leaks == ["none"], leaks
  # a confidence line that reached a prompt file is named, not summarised
  (dv / "calls" / "converge2-opus.prompt.md").write_text(
      "--- OPEN OBJECTIONS ---\nO1 (I1): weak.\nCONFIDENCE O1: 0.4\n"
  )
  _, _, leaks2 = _votes_and_confidence(
      parse_transcript((dv / "transcript.md").read_text()), dv
  )
  assert leaks2 == ["converge2-opus.prompt.md"], leaks2
  # the group HEADER alone is not a leak: every converge prompt must carry it to ask for values.
  # This assertion was the other way round until 2026-09-17, which made the check flag 34/34
  # prompt files in d-2026-09-17-49d220 - a guard that fires always proves nothing.
  (dv / "calls" / "converge2-opus.prompt.md").write_text(
      "--- OPEN OBJECTIONS ---\nO1 (I1): weak.\nCONFIDENCE:\n"
  )
  _, _, leaks3 = _votes_and_confidence(
      parse_transcript((dv / "transcript.md").read_text()), dv
  )
  assert leaks3 == ["none"], leaks3
  (dv / "calls" / "converge2-opus.prompt.md").unlink()
  # a confidence at or above the threshold is not flagged LOW
  (dv / "transcript.md").write_text(
      (dv / "transcript.md")
      .read_text()
      .replace("CONFIDENCE O1: 0.4", "CONFIDENCE O1: 0.9")
  )
  _, confs2, _ = _votes_and_confidence(
      parse_transcript((dv / "transcript.md").read_text()), dv
  )
  assert confs2 == ["- O1 (gemini): 0.9"], confs2
  # a malformed or out-of-range value is flagged on the record, never a crash and never dropped
  for val, flag in (
      ("0.4.5", " UNREADABLE"),
      ("n/a", " UNREADABLE"),
      ("1.5", " OUT-OF-RANGE"),
      ("-0.2", " OUT-OF-RANGE"),
      ("1e-1", " LOW"),
      ("00.4", " LOW"),
      ("1", ""),
      ("nan", " OUT-OF-RANGE"),
      ("inf", " OUT-OF-RANGE"),
      ("0.4 maybe", " UNREADABLE"),
  ):
    (dv / "transcript.md").write_text(
        "## R1 converge gemini role=advocate\nSTANCE: OBJECT\nOBJECTIONS:\nO1"
        " (I1) [advocate]: weak.\nCONFIDENCE:\nCONFIDENCE O1:"
        " %s\nREVIEW:\nnone\nQUESTIONS:\nnone\n" % val
    )
    _, confs3, _ = _votes_and_confidence(
        parse_transcript((dv / "transcript.md").read_text()), dv
    )
    assert confs3 == ["- O1 (gemini): %s%s" % (val, flag)], (val, confs3)
  # an empty value is named, not dropped: a silently missing confidence is the failure mode here
  (dv / "transcript.md").write_text(
      "## R1 converge gemini role=advocate\nSTANCE: OBJECT\nOBJECTIONS:\nO1"
      " (I1) [advocate]: weak.\nCONFIDENCE:\nCONFIDENCE"
      " O1:\nREVIEW:\nnone\nQUESTIONS:\nnone\n"
  )
  _, confs4, _ = _votes_and_confidence(
      parse_transcript((dv / "transcript.md").read_text()), dv
  )
  assert confs4 == ["- O1 (gemini): (blank) UNREADABLE"], confs4
  # `CONFIDENCE junk` is not a value line and so not a leak: the detector's job is to catch a
  # participant's NUMBER reaching a prompt, not any use of the word
  (dv / "calls" / "converge3-opus.prompt.md").write_text(
      "--- OPEN OBJECTIONS ---\nCONFIDENCE junk\n"
  )
  _, _, leaks4 = _votes_and_confidence(
      parse_transcript((dv / "transcript.md").read_text()), dv
  )
  assert leaks4 == ["none"], leaks4
  (dv / "calls" / "converge3-opus.prompt.md").unlink()
  # the format template every converge prompt MUST carry is NOT a leak, nor is prose using the word
  (dv / "calls" / "template.prompt.md").write_text(
      "STANCE: ACCEPT | OBJECT\nOBJECTIONS:\nnone\nCONFIDENCE:\nCONFIDENCE"
      " O<n>: <0.0 to 1.0>                (one line per objection you"
      " raised)\nREVIEW:\nnone\n"
  )
  (dv / "calls" / "prose.prompt.md").write_text(
      "CONFIDENCE line, a closing VOTE, and three sections.\n"
  )
  _, _, leaks5 = _votes_and_confidence(
      parse_transcript((dv / "transcript.md").read_text()), dv
  )
  assert leaks5 == ["none"], leaks5
  # a real emitted value IS a leak, even next to the template
  (dv / "calls" / "real.prompt.md").write_text(
      "O1 (I1): weak.\nCONFIDENCE O1: 0.4\n"
  )
  _, _, leaks6 = _votes_and_confidence(
      parse_transcript((dv / "transcript.md").read_text()), dv
  )
  assert leaks6 == ["real.prompt.md"], leaks6
  for f in ("template.prompt.md", "prose.prompt.md", "real.prompt.md"):
    (dv / "calls" / f).unlink()

  # record() refuses a second row for the same debate id
  try:
    record(d, "t", "QUORUM")
    raise AssertionError("record must refuse a duplicate row")
  except SystemExit as e:
    assert "already has a row" in str(e), e
  # and the log still holds exactly one row for that id
  rows = [
      json.loads(l) for l in _ledger().read_text().splitlines() if l.strip()
  ]
  assert sum(1 for r in rows if r["id"] == "d-test") == 1, rows

  # corrupted lines in shard don't crash record()
  log_p = _ledger()
  with open(log_p, "a") as f:
    f.write("{corrupt line that is not json\n\n")
  (d / "state.json").write_text(
      json.dumps(dict(_st, id="d-test-corrupt-shard"))
  )
  rec_corr = record(d, "t", "QUORUM")
  assert rec_corr["id"] == "d-test-corrupt-shard"

  # report() handles missing or malformed started date gracefully
  (d / "state.json").write_text(
      json.dumps(dict(_st, id="d-test-no-start", started=None))
  )
  rep_no_start = report(d, "the task", "QUORUM")
  assert "--:--" in rep_no_start and rep_no_start.startswith(
      "# Debate report d-test-no-start"
  )

  (d / "state.json").write_text(
      json.dumps(dict(_st, id="d-test-bad-start", started="not-a-date"))
  )
  rep_bad_start = report(d, "the task", "QUORUM")
  assert "--:--" in rep_bad_start and rep_bad_start.startswith(
      "# Debate report d-test-bad-start"
  )

  # restore original state
  (d / "state.json").write_text(json.dumps(_st))

  # Test preserve_facts: superset rule (prev=[a, b], curr=[a_reformatted, b, c], deleted I2 stays deleted)
  import tempfile

  dp = Path(tempfile.mkdtemp())
  (dp / "artifact.prev.md").write_text(
      "## Options\n"
      "### I1: First\nBody 1.\nFACTS:\n"
      '- VERIFIED(foo.py:10 "def x") exact claim a.\n'
      "- ASSUMED claim b [downgraded: snippet not at bar.py:20]\n"
      "### I2: Deleted\nBody 2.\nFACTS:\n"
      "- ASSUMED deleted fact\n"
  )
  (dp / "artifact.md").write_text(
      "## Options\n"
      "### I1: First Revised\nBody 1 revised.\nFACTS:\n"
      '- VERIFIED(foo.py:10 "def x") exact claim a\n'
      '- VERIFIED(baz.py:30 "def z") brand new claim c.\n'
  )
  (dp / "transcript.md").write_text(
      "## R1 converge gemini role=reviser\nARTIFACT:\n"
      + (dp / "artifact.md").read_text()
      + "\nANSWERS:\nO1: fixed.\n"
  )
  restored_ids = preserve_facts(dp)
  assert restored_ids == ["I1"], restored_ids
  preserved_art = (dp / "artifact.md").read_text()
  assert '- VERIFIED(foo.py:10 "def x") exact claim a.' in preserved_art
  assert (
      "- ASSUMED claim b [downgraded: snippet not at bar.py:20]"
      in preserved_art
  )
  assert '- VERIFIED(baz.py:30 "def z") brand new claim c.' in preserved_art
  assert (
      "### I2:" not in preserved_art
  ), "Deleted item I2 must not be resurrected"
  # Ensure reformatted duplicate of claim a was not kept alongside exact claim a.
  assert preserved_art.count("foo.py:10") == 1, preserved_art

  # Test unidirectional transcript.md -> artifact.md authority in preserve_facts
  (dp / "transcript.md").write_text(
      "## R1 converge opus role=reviser\n"
      "ARTIFACT:\n"
      "## Options\n\n"
      "### I1: Alpha Revised\n"
      "companion reviser body from transcript.\n"
      "FACTS:\n"
      '- VERIFIED(baz.py:30 "def z") brand new claim c.\n\n'
      "ANSWERS:\n"
      "none\n"
  )
  preserve_facts(dp)
  synced_art = (dp / "artifact.md").read_text()
  assert "### I1: Alpha Revised" in synced_art, synced_art
  assert "companion reviser body from transcript." in synced_art, synced_art
  assert (
      '- VERIFIED(foo.py:10 "def x") exact claim a.' in synced_art
  ), synced_art

  # Test fail-closed SystemExit(1) when Reviser turn has empty ARTIFACT section
  (dp / "transcript.md").write_text(
      "## R1 converge opus role=reviser\nARTIFACT:\n\nANSWERS:\nnone\n"
  )
  try:
    preserve_facts(dp)
    assert False, "Expected SystemExit(1) on empty Reviser ARTIFACT"
  except SystemExit as e:
    assert e.code == 1

  print("debate_report selftest: ok")


if __name__ == "__main__":
  a = sys.argv[1:]
  if not a or a[0] in ("-h", "--help"):
    print(__doc__)
    sys.exit(0)
  if a == ["--selftest"]:
    selftest()
    sys.exit(0)
  if a[0] == "merge-facts":
    print("merged items:", merge_facts(a[1], a[2:]))
  elif a[0] == "preserve-facts":
    restored = preserve_facts(a[1])
    print("restored verbatim FACTS on: %s" % (", ".join(restored) or "none"))
  elif a[0] == "record":
    task = a[a.index("--task") + 1]
    outcome = a[a.index("--outcome") + 1]
    tiebreak = a[a.index("--tiebreak") + 1] if "--tiebreak" in a else None
    print(json.dumps(record(a[1], task, outcome, tiebreak)))
  elif a[0] == "ledger":
    print(ledger(a[1]))
  elif a[0] == "report":
    report(a[1], a[a.index("--task") + 1], a[a.index("--outcome") + 1])
    print(str(Path(a[1]) / "report.md"))
  elif a[0] == "outcome":
    print(
        json.dumps(
            outcome(
                a[1],
                a[a.index("--built") + 1],
                a[a.index("--kept") + 1],
                a[a.index("--worth") + 1],
            )
        )
    )
  else:
    print(__doc__)
    sys.exit(2)
