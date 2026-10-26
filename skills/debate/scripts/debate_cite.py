#!/usr/bin/env python3
"""debate_cite.py - mechanical check of a VERIFIED citation. Stdlib only.

The only form:
  <path>:<line> "<snippet>"     the file exists inside the repo and that line
  contains the snippet

Command citations (`cmd "<command>" -> "<expected>"`) were removed on 2026-09-15
after three
consecutive gate rounds each found a new way for an allowlisted read-only
command to read outside
the repo (flags, symlink traversal, clustered short options). A predicate that
keeps needing one
more exception is at the wrong level; a file-and-line citation has no such
surface. Anything else
is refused and the caller downgrades the fact to ASSUMED.
"""

import itertools
import json
import os
from pathlib import Path
import re
import sys
from typing import Optional

FILE_FORM = re.compile(
    r'^(?P<path>[^:"\s]+):(?P<start>\d+)(?:-(?P<end>\d+))? "(?P<snippet>.+)"$',
    re.DOTALL,
)


def _resolve(cwd, operand, scope=None):
  """Return the resolved Path for a citation operand, or None if it may not be read: no absolute

  path, no `..`, no `~`, and the resolved path (symlinks followed) must sit
  under the resolved repo or workspace workspace root.
  The caller reads THIS path, never the raw operand, so a symlink swapped in
  after the check
  cannot redirect the read.
  """
  if operand.startswith("@repo/"):
    operand = operand[len("@repo/") :]

  if operand.startswith(("/", "~")) or ".." in operand:
    return None
  if scope and (str(scope).startswith(("/", "~")) or ".." in str(scope)):
    return None

  root = Path(cwd).resolve()

  # Candidates for search root:
  # Precedence:
  # 1. If scope is provided: check (Path(cwd) / scope / operand) first.
  # 2. Next check (Path(cwd) / operand).
  # 3. Next check workspace siblings (src_main/configs) if applicable.
  # Fail closed if neither exists.

  candidates = []
  if scope:
    candidates.append(root / scope)
  candidates.append(root)

  if (
      root.name in ("src_main", "configs")
      and (root.parent / "src_main").is_dir()
      and (root.parent / "configs").is_dir()
  ):
    candidates.append(root.parent)
  elif (
      operand.startswith(("configs/", "src_main/"))
      and (root.parent / "src_main").is_dir()
  ):
    candidates.append(root.parent)

  for r in candidates:
    try:
      p = (r / operand).resolve()
    except OSError:
      continue
    allowed_root = root.parent if r == root.parent else root
    if p.is_relative_to(allowed_root) and p.exists():
      return p

  # If not existing yet, fall back to checking relative to root
  try:
    if scope:
      p = (root / scope / operand).resolve()
      if p.is_relative_to(root):
        return p
    p = (root / operand).resolve()
    if p.is_relative_to(root):
      return p
  except OSError:
    pass
  return None


def _read_lines(p, max_line=None):
  """Read lines from file p.

  Uses memory-bounded line-by-line reading for files > 2MB.
  """
  size = os.path.getsize(p)
  if size > 2 * 1024 * 1024:
    lines = []
    with open(p, "r", encoding="utf-8", errors="replace") as f:
      if max_line is not None:
        for line in itertools.islice(f, max_line):
          lines.append(line.rstrip("\r\n"))
      else:
        for line in f:
          lines.append(line.rstrip("\r\n"))
    return lines
  return p.read_text(errors="replace").splitlines()


def check_citation(citation, cwd, scope=None):
  m = FILE_FORM.match(citation)
  if not m:
    return False, "unrecognised citation form"
  p = _resolve(cwd, m.group("path"), scope=scope)
  if p is None:
    return False, "path outside repo"
  if not p.is_file():
    return False, "no such file %s" % m.group("path")

  start = int(m.group("start"))
  end_group = m.group("end")
  end = int(end_group) if end_group is not None else None

  if start < 1:
    return False, "line %d out of range" % start
  if end is not None and end < start:
    return False, "line %d-%d out of range" % (start, end)

  needed_max = end if end is not None else start + 5
  lines = _read_lines(p, max_line=None)

  if start > len(lines):
    return False, "line %d out of range" % start
  if end is not None and end > len(lines):
    return False, "line %d out of range" % end

  # participants routinely write the snippet as an escaped string; compare the unescaped form
  snippet = (
      m.group("snippet")
      .replace('\\"', '"')
      .replace("\\n", "\n")
      .replace("\\\\", "\\")
  )

  if end is not None:
    window = lines[start - 1 : end]
    block = "\n".join(window)
    if snippet not in block:
      return False, "snippet not at %s:%d-%d" % (m.group("path"), start, end)
    return True, "ok"

  # Single line citation
  if "\n" not in snippet:
    if snippet not in lines[start - 1]:
      return False, "snippet not at %s:%d" % (m.group("path"), start)
    return True, "ok"

  # Single line with multiline snippet: search window lines[start - 1 : start + 5]
  window = lines[start - 1 : min(len(lines), start + 5)]
  block = "\n".join(window)
  if snippet not in block:
    return False, "snippet not at %s:%d" % (m.group("path"), start)
  return True, "ok"


def selftest():
  import tempfile

  d = tempfile.mkdtemp()
  Path(d, "a.py").write_text("x = 1\nCREATE TABLE events\ny = 2\nz = 3\n")
  assert check_citation('a.py:2 "CREATE TABLE"', d) == (True, "ok")
  Path(d, "e.py").write_text('print("x") % y\nre.compile(r"\\d+")\n')
  assert check_citation('e.py:1 "print(\\"x\\") % y"', d) == (
      True,
      "ok",
  ), "escaped quotes must match the literal line"
  assert check_citation('e.py:2 "(r\\"\\\\d+\\")"', d) == (
      True,
      "ok",
  ), "escaped backslash must match the literal line"
  assert check_citation('a.py:1 "CREATE TABLE"', d)[0] is False
  assert check_citation('a.py:9 "x"', d)[1] == "line 9 out of range"
  assert check_citation('nope.py:1 "x"', d)[1].startswith("no such file")
  assert (
      check_citation("VERIFIED because I said so", d)[1]
      == "unrecognised citation form"
  )
  # the removed command form is refused outright, never run
  assert (
      check_citation('cmd "grep -c CREATE a.py" -> "1"', d)[1]
      == "unrecognised citation form"
  )
  assert (
      check_citation('cmd "rm -rf a.py" -> "x"', d)[1]
      == "unrecognised citation form"
  )
  assert Path(d, "a.py").exists()
  # the checker may only read inside the repo: absolute, parent, home and symlinked-out paths are refused
  assert check_citation('/etc/passwd:1 "root"', d)[1] == "path outside repo"
  assert check_citation('../a.py:1 "x"', d)[1] == "path outside repo"
  assert check_citation('~/.zshrc:1 "x"', d)[1] == "path outside repo"
  os.symlink("/etc", Path(d, "etclink"))
  assert check_citation('etclink/passwd:1 "root"', d)[1] == "path outside repo"
  # an in-repo symlink to an in-repo file is fine, and the read goes to the RESOLVED path:
  # _resolve returns the target, so nothing reopens the raw operand after the check
  os.symlink(Path(d, "a.py"), Path(d, "alias.py"))
  assert check_citation('alias.py:2 "CREATE TABLE"', d) == (True, "ok")
  assert _resolve(d, "alias.py") == Path(d, "a.py").resolve()
  assert _resolve(d, "etclink/passwd") is None

  # Line ranges test: <path>:<start_line>-<end_line> "<snippet>"
  assert check_citation('a.py:1-2 "x = 1\\nCREATE TABLE"', d) == (True, "ok")
  assert check_citation('a.py:1-2 "x = 1\nCREATE TABLE"', d) == (True, "ok")
  assert check_citation('a.py:2-3 "CREATE TABLE events\\ny = 2"', d) == (
      True,
      "ok",
  )
  assert check_citation('a.py:1-2 "CREATE TABLE events\\ny = 2"', d)[0] is False
  assert check_citation('a.py:1-9 "x"', d)[1] == "line 9 out of range"
  assert check_citation('a.py:3-2 "x"', d)[1] == "line 3-2 out of range"

  # Single line with multiline snippet (lines[start - 1 : start + 5])
  assert check_citation('a.py:1 "x = 1\\nCREATE TABLE"', d) == (True, "ok")
  assert check_citation('a.py:1 "x = 1\nCREATE TABLE"', d) == (True, "ok")
  assert check_citation('a.py:1 "CREATE TABLE events\\ny = 2"', d) == (
      True,
      "ok",
  )

  # @repo/ stripping
  assert check_citation('@repo/a.py:2 "CREATE TABLE"', d) == (True, "ok")
  assert _resolve(d, "@repo/a.py") == Path(d, "a.py").resolve()
  assert _resolve(d, "@repo/../etc/passwd") is None

  # Workspace root resolution with configs/ and src_main/
  ws = Path(tempfile.mkdtemp())
  (ws / "src_main").mkdir()
  (ws / "configs").mkdir()
  (ws / "src_main" / "test.txt").write_text("hello src_main\n")
  (ws / "configs" / "conf.txt").write_text("hello configs\n")
  # cwd in src_main resolving configs/ path
  assert check_citation(
      'configs/conf.txt:1 "hello configs"', str(ws / "src_main")
  ) == (True, "ok")
  assert check_citation(
      '@repo/configs/conf.txt:1 "hello configs"', str(ws / "src_main")
  ) == (True, "ok")
  assert check_citation(
      'src_main/test.txt:1 "hello src_main"', str(ws / "configs")
  ) == (True, "ok")
  assert check_citation(
      '@repo/src_main/test.txt:1 "hello src_main"', str(ws / "src_main")
  ) == (True, "ok")

  # Memory-bounded streaming for > 2MB files
  big = Path(d, "big.txt")
  # Write 2.1MB file
  chunk = "line of text in big file\n" * 1000
  with open(big, "w", encoding="utf-8") as f:
    for _ in range(90):
      f.write(chunk)
    f.write("TARGET_SPECIAL_LINE\n")
  assert os.path.getsize(big) > 2 * 1024 * 1024
  # Find line number of TARGET_SPECIAL_LINE
  total_lines = 90 * 1000 + 1
  assert check_citation(f'big.txt:{total_lines} "TARGET_SPECIAL_LINE"', d) == (
      True,
      "ok",
  )
  assert check_citation(
      f'big.txt:{total_lines - 1}-{total_lines} "TARGET_SPECIAL_LINE"', d
  ) == (True, "ok")

  # Scoped resolution
  pinned = Path(tempfile.mkdtemp())
  (pinned / "BUILD").write_text('name = "root_target"\n')
  (pinned / "scoped_pkg").mkdir()
  (pinned / "scoped_pkg" / "BUILD").write_text('name = "scoped_target"\n')
  (pinned / "scoped_pkg" / "sub").mkdir()
  (pinned / "scoped_pkg" / "sub" / "lib.py").write_text(
      "def scoped_func(): pass\n"
  )

  # Scoped resolution resolves scoped_pkg/BUILD over root BUILD
  assert check_citation(
      'BUILD:1 "scoped_target"', str(pinned), scope="scoped_pkg"
  ) == (True, "ok")
  assert (
      check_citation('BUILD:1 "root_target"', str(pinned), scope="scoped_pkg")[
          0
      ]
      is False
  )
  assert check_citation('BUILD:1 "root_target"', str(pinned)) == (True, "ok")
  assert (
      _resolve(str(pinned), "BUILD", scope="scoped_pkg")
      == (pinned / "scoped_pkg" / "BUILD").resolve()
  )
  assert _resolve(str(pinned), "BUILD") == (pinned / "BUILD").resolve()

  # Relative citations resolve properly under scope
  assert check_citation(
      'sub/lib.py:1 "scoped_func"', str(pinned), scope="scoped_pkg"
  ) == (True, "ok")
  assert (
      _resolve(str(pinned), "sub/lib.py", scope="scoped_pkg")
      == (pinned / "scoped_pkg" / "sub" / "lib.py").resolve()
  )

  # Test pack_excerpts: universal line/char/byte caps, binary file skip, path traversal skip, interval union
  deb_dir = Path(tempfile.mkdtemp())
  repo_dir = deb_dir / "pinned"
  repo_dir.mkdir()
  (deb_dir / "state.json").write_text(
      json.dumps({"repo": str(repo_dir), "scope": None})
  )
  # Create a 1000-line file with one >300 char line and a binary file
  many_lines = [f"def helper_func_{i}(): return {i}" for i in range(1, 1001)]
  many_lines[4] = "LONG_LINE = '" + ("x" * 350) + "'"
  (repo_dir / "module_a.py").write_text("\n".join(many_lines) + "\n")
  (repo_dir / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")
  (deb_dir / "brief.md").write_text(
      "## Task\nTest.\n\n## Read these\n- module_a.py:1-5000\n-"
      " module_a.py:10-20\n- logo.png\n- ../../etc/passwd\n- e.g. v1.2\n"
  )
  (deb_dir / "artifact.md").write_text(
      "## Directions\n### I1: Option\nCalls `helper_func_50` in"
      " `module_a.py`.\n"
  )
  excerpts = pack_excerpts(
      deb_dir, max_lines_per_file=50, max_total_lines=80, max_chars_per_line=300
  )
  ex_lines = excerpts.splitlines()
  assert len(ex_lines) <= 80, f"Expected <=80 lines, got {len(ex_lines)}"
  assert "=== module_a.py ===" in excerpts
  assert "# [line 5 omitted: >300 chars]" in excerpts
  assert "logo.png" not in excerpts, "Binary files must be skipped"
  assert "passwd" not in excerpts, "Outside paths must be skipped"
  # Verify no duplicate line numbers emitted for module_a.py
  num_lines = [l.split(":", 1)[0] for l in ex_lines if re.match(r"^\d+: ", l)]
  assert len(num_lines) == len(
      set(num_lines)
  ), "Overlapping intervals must be deduplicated"

  # Test resolve_lines: single-line snap, multi-line range snap, trivial rejection, ambiguous rejection
  (repo_dir / "target.py").write_text(
      "\n".join([
          "import os",
          "import sys",
          "def alpha_handler(req):",
          "  return True",
          "def beta_processor(ctx):",
          "  val = compute_special_metric(ctx)",
          '  log_event("metric(a) b", val)',
          "  return val",
          "def gamma():",
          "  return True",
      ])
      + "\n"
  )
  facts_p = deb_dir / "verify1-opus.facts.md"
  facts_p.write_text(
      "FACTS I1:\n"
      # 1. Single-line off by 4 lines (cites line 2, actual is line 6) with >=10 chars & >=2 tokens -> snaps to 6
      '- VERIFIED(target.py:2 "val = compute_special_metric(ctx)") computes'
      " metric\n"
      # 2. Multi-line range off by 3 lines (cites 3-4, actual is 6-7) with parentheses inside quote -> snaps to 6-7
      '- VERIFIED(target.py:3-4 "val = compute_special_metric(ctx)\\n '
      ' log_event(\\"metric(a) b\\", val)") logs metric\n'
      # 3. Trivial snippet ("return True", <2 distinct long tokens or ambiguous at lines 4 and 10) -> must NOT snap!
      '- VERIFIED(target.py:5 "return True") returns true\n'
      # 4. Short trivial snippet ("import os", <10 chars) -> must NOT snap!
      '- VERIFIED(target.py:8 "import os") imports os\n'
  )
  snapped_count = resolve_lines(deb_dir, [str(facts_p)])
  assert (
      snapped_count == 2
  ), f"Expected 2 snapped citations, got {snapped_count}"
  resolved_text = facts_p.read_text()
  assert (
      '- VERIFIED(target.py:6 "val = compute_special_metric(ctx)") computes'
      " metric"
      in resolved_text
  )
  assert (
      '- VERIFIED(target.py:6-7 "val = compute_special_metric(ctx)\\n '
      ' log_event(\\"metric(a) b\\", val)") logs metric'
      in resolved_text
  )
  # Ambiguous / trivial lines must remain at their original failing line numbers (5 and 8)
  assert '- VERIFIED(target.py:5 "return True") returns true' in resolved_text
  assert '- VERIFIED(target.py:8 "import os") imports os' in resolved_text

  print("debate_cite selftest: ok")


_STOPWORDS = frozenset({
    "that",
    "with",
    "from",
    "this",
    "have",
    "will",
    "none",
    "true",
    "false",
    "return",
    "self",
    "item",
    "items",
    "facts",
    "verified",
    "assumed",
    "dispute",
    "phase",
    "round",
    "brief",
    "artifact",
})

_CANDIDATE_PATH_RE = re.compile(
    r"(?P<path>[A-Za-z0-9_./-]+(?:\.[A-Za-z0-9_]+|/BUILD|\bBUILD\b))"
    r"(?::(?P<start>\d+)(?:-(?P<end>\d+))?)?"
)

_CITE_LINE_RE = re.compile(
    r'^- VERIFIED\((?P<path>[^:"\s]+):(?P<start>\d+)(?:-(?P<end>\d+))?'
    r' "(?P<raw_snippet>(?:\\.|[^"\\])*)"\) (?P<claim>.+)$'
)


def _atomic_write(target: Path, content: str) -> None:
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


def pack_excerpts(
    d,
    max_lines_per_file: int = 120,
    max_total_lines: int = 600,
    max_chars_per_line: int = 300,
    max_total_bytes: int = 48000,
    out_path: Optional[str] = None,
) -> str:
  """Extract bounded, line-numbered repository excerpts from pinned tree for Phase 4 Verify prompts."""
  d = Path(d)
  st = (
      json.loads((d / "state.json").read_text())
      if (d / "state.json").is_file()
      else {}
  )
  repo = st.get("repo") or str(d / "pinned")
  scope = st.get("scope")

  brief_text = (
      (d / "brief.md").read_text(errors="replace")
      if (d / "brief.md").is_file()
      else ""
  )
  art_text = (
      (d / "artifact.md").read_text(errors="replace")
      if (d / "artifact.md").is_file()
      else ""
  )

  # Split brief.md into Tier 1 (Read these) and Tier 2 (other sections + artifact.md)
  read_these_text = ""
  other_brief_text = brief_text
  m_rt = re.search(
      r"(?ms)^#{1,3}\s*Read these\b.*?(?=^#{1,3}\s+|\Z)", brief_text
  )
  if m_rt:
    read_these_text = m_rt.group(0)
    other_brief_text = brief_text[: m_rt.start()] + brief_text[m_rt.end() :]

  # Extract candidate tokens in priority order: Tier 1 first, then Tier 2
  raw_candidates = []
  for text_src in (read_these_text, other_brief_text + "\n" + art_text):
    for m in _CANDIDATE_PATH_RE.finditer(text_src):
      raw_candidates.append((
          m.group("path"),
          int(m.group("start")) if m.group("start") else None,
          int(m.group("end")) if m.group("end") else None,
      ))

  # Extract specific identifier tokens from artifact.md
  ident_tokens = []
  seen_idents = set()
  for tok in re.findall(r"\b[A-Za-z_][A-Za-z0-9_]{3,}\b", art_text):
    low = tok.lower()
    if low in _STOPWORDS or tok in seen_idents:
      continue
    if (
        len(tok) >= 6
        or "_" in tok
        or any(c.isupper() or c.isdigit() for c in tok)
    ):
      seen_idents.add(tok)
      ident_tokens.append(tok)

  # Group anchors by resolved file path in discovery order
  file_order = []
  file_meta = {}  # resolved_p -> {"display": operand, "anchors": [(s, e), ...]}
  for raw_path, s_line, e_line in raw_candidates:
    try:
      p = _resolve(repo, raw_path, scope=scope)
      if p is None or not p.is_file():
        continue
      with open(p, "rb") as bf:
        head = bf.read(4096)
      if b"\x00" in head:
        continue
    except (OSError, ValueError, UnicodeError):
      continue
    if p not in file_meta:
      file_order.append(p)
      file_meta[p] = {"display": raw_path.lstrip("/"), "anchors": []}
    if s_line is not None:
      file_meta[p]["anchors"].append((s_line, e_line or s_line))

  out_lines = []
  total_bytes = 0

  def _try_emit(line_str: str) -> bool:
    nonlocal total_bytes
    line_bytes = len((line_str + "\n").encode("utf-8", errors="replace"))
    if len(out_lines) + 1 > max_total_lines:
      return False
    if total_bytes + line_bytes > max_total_bytes:
      return False
    out_lines.append(line_str)
    total_bytes += line_bytes
    return True

  for p in file_order:
    try:
      lines = _read_lines(p)
    except (OSError, ValueError, UnicodeError):
      continue
    if not lines:
      continue
    n_lines = len(lines)
    meta = file_meta[p]
    candidate_windows = []

    # 1. Explicit anchor windows [start-20 .. end+20]
    for s_val, e_val in meta["anchors"]:
      w_start = max(1, s_val - 20)
      w_end = min(n_lines, max(s_val, e_val) + 20)
      if w_start <= w_end:
        candidate_windows.append((w_start, w_end))

    # 2. Identifier match windows (±15 lines), ranked by distinct identifier hits descending
    if ident_tokens:
      scored_centers = []
      for idx, text_line in enumerate(lines):
        hits = sum(1 for tok in ident_tokens if tok in text_line)
        if hits > 0:
          scored_centers.append((-hits, idx + 1))
      scored_centers.sort()
      for _, center_l in scored_centers[:10]:
        candidate_windows.append(
            (max(1, center_l - 15), min(n_lines, center_l + 15))
        )

    # 3. Fallback to top of file if no windows matched
    if not candidate_windows:
      candidate_windows.append((1, min(n_lines, max_lines_per_file)))

    # Select unique line numbers in priority order up to max_lines_per_file
    selected_set = set()
    for w_s, w_e in candidate_windows:
      for l_num in range(w_s, w_e + 1):
        if len(selected_set) >= max_lines_per_file:
          break
        selected_set.add(l_num)
      if len(selected_set) >= max_lines_per_file:
        break

    if not selected_set:
      continue

    sorted_lnums = sorted(selected_set)
    if not _try_emit(f"=== {meta['display']} ==="):
      break

    for l_num in sorted_lnums:
      raw_l = lines[l_num - 1]
      if len(raw_l) > max_chars_per_line:
        entry = f"# [line {l_num} omitted: >{max_chars_per_line} chars]"
      else:
        entry = f"{l_num}: {raw_l}"
      if not _try_emit(entry):
        break

  result = "\n".join(out_lines) + ("\n" if out_lines else "")
  if out_path:
    _atomic_write(Path(out_path), result)
  return result


def resolve_lines(d, facts_files) -> int:
  """Snap off-by-a-few-lines VERIFIED citations in facts_files to exact line numbers in pinned repo."""
  d = Path(d)
  st = (
      json.loads((d / "state.json").read_text())
      if (d / "state.json").is_file()
      else {}
  )
  repo = st.get("repo") or str(d / "pinned")
  scope = st.get("scope")
  total_snapped = 0

  for fpath in facts_files:
    fp = Path(fpath)
    if not fp.is_file():
      continue
    raw_lines = fp.read_text(errors="replace").splitlines()
    new_lines = []
    changed_file = False

    for line in raw_lines:
      m = _CITE_LINE_RE.match(line)
      if not m:
        new_lines.append(line)
        continue

      path_str = m.group("path")
      start_val = int(m.group("start"))
      end_str = m.group("end")
      raw_snip = m.group("raw_snippet")
      claim_str = m.group("claim")
      old_spec = f"{start_val}-{end_str}" if end_str else str(start_val)
      old_cite = f'{path_str}:{old_spec} "{raw_snip}"'

      # Pre-check: if already valid, do not touch
      if check_citation(old_cite, repo, scope=scope)[0]:
        new_lines.append(line)
        continue

      snippet = (
          raw_snip.replace('\\"', '"')
          .replace("\\n", "\n")
          .replace("\\\\", "\\")
      )
      # Specificity gate: >= 10 chars and >= 2 alphanumeric tokens
      if (
          len(snippet.strip()) < 10
          or len(re.findall(r"[A-Za-z0-9_]{2,}", snippet)) < 2
      ):
        new_lines.append(line)
        continue

      try:
        p = _resolve(repo, path_str, scope=scope)
        if p is None or not p.is_file():
          new_lines.append(line)
          continue
        with open(p, "rb") as bf:
          if b"\x00" in bf.read(4096):
            new_lines.append(line)
            continue
        file_lines = _read_lines(p)
      except (OSError, ValueError, UnicodeError):
        new_lines.append(line)
        continue

      if "\n" not in snippet:
        span = 1
        matches = [
            idx + 1 for idx, fl in enumerate(file_lines) if snippet in fl
        ]
      else:
        span = snippet.count("\n") + 1
        matches = [
            idx + 1
            for idx in range(len(file_lines) - span + 1)
            if snippet in "\n".join(file_lines[idx : idx + span])
        ]

      near = [L for L in matches if abs(L - start_val) <= 25]
      best_L = None
      if len(near) == 1:
        best_L = near[0]
      elif len(near) == 0 and len(matches) == 1:
        best_L = matches[0]

      if best_L is None:
        new_lines.append(line)
        continue

      new_spec = str(best_L) if span == 1 else f"{best_L}-{best_L + span - 1}"
      new_cite = f'{path_str}:{new_spec} "{raw_snip}"'
      # Mandatory post-check via check_citation
      if check_citation(new_cite, repo, scope=scope)[0]:
        new_lines.append(f"- VERIFIED({new_cite}) {claim_str}")
        total_snapped += 1
        changed_file = True
      else:
        new_lines.append(line)

    if changed_file:
      _atomic_write(fp, "\n".join(new_lines) + "\n")

  return total_snapped


if __name__ == "__main__":
  import json
  from typing import Optional

  if not sys.argv[1:] or sys.argv[1] in ("-h", "--help"):
    print(
        "Usage:\n  debate_cite.py '<path>:<line> \"<snippet>\"' [repo_dir]"
        " [--scope <scope>]\n  debate_cite.py pack-excerpts <debate_dir>"
        " [--max-lines-per-file 120] [--max-total-lines 600] [--out <file>]\n "
        " debate_cite.py resolve-lines <debate_dir> <facts_file>...\n "
        " debate_cite.py --selftest"
    )
    sys.exit(0)
  if sys.argv[1:] == ["--selftest"]:
    selftest()
    sys.exit(0)
  if sys.argv[1] == "pack-excerpts":
    args = sys.argv[2:]
    if not args:
      print("Usage: debate_cite.py pack-excerpts <debate_dir>", file=sys.stderr)
      sys.exit(2)
    d_arg = args[0]
    rest = args[1:]
    max_lpf = 120
    max_tl = 600
    max_cpl = 300
    max_tb = 48000
    out_p = None
    i = 0
    while i < len(rest):
      if rest[i] == "--max-lines-per-file" and i + 1 < len(rest):
        max_lpf = int(rest[i + 1])
        i += 2
      elif rest[i] == "--max-total-lines" and i + 1 < len(rest):
        max_tl = int(rest[i + 1])
        i += 2
      elif rest[i] == "--max-chars-per-line" and i + 1 < len(rest):
        max_cpl = int(rest[i + 1])
        i += 2
      elif rest[i] == "--max-total-bytes" and i + 1 < len(rest):
        max_tb = int(rest[i + 1])
        i += 2
      elif rest[i] == "--out" and i + 1 < len(rest):
        out_p = rest[i + 1]
        i += 2
      else:
        i += 1
    res = pack_excerpts(
        d_arg,
        max_lines_per_file=max_lpf,
        max_total_lines=max_tl,
        max_chars_per_line=max_cpl,
        max_total_bytes=max_tb,
        out_path=out_p,
    )
    if not out_p:
      sys.stdout.write(res)
    sys.exit(0)
  if sys.argv[1] == "resolve-lines":
    args = sys.argv[2:]
    if len(args) < 2:
      print(
          "Usage: debate_cite.py resolve-lines <debate_dir> <facts_file>...",
          file=sys.stderr,
      )
      sys.exit(2)
    cnt = resolve_lines(args[0], args[1:])
    print(f"resolved citations: {cnt}")
    sys.exit(0)
  scope = None
  args = [a for a in sys.argv[1:] if not a.startswith("--scope")]
  if "--scope" in sys.argv:
    idx = sys.argv.index("--scope")
    if idx + 1 < len(sys.argv):
      scope = sys.argv[idx + 1]
  citation = args[0] if args else ""
  cwd = args[1] if len(args) > 1 else "."
  ok, why = check_citation(citation, cwd, scope=scope)
  print(("OK " if ok else "FAIL ") + why)
  sys.exit(0 if ok else 1)
