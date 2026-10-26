#!/usr/bin/env python3
"""debate_round.py <dir> <opus|gemini> <prompt-file> - one stateless companion call.

Counts the call against state.json's cap, runs the companion in its own process
group with a
timeout, kills the whole group on timeout, and writes the output beside the
prompt.
"""

import fcntl
import glob
import json
import os
from pathlib import Path
import re
import secrets
import signal
import subprocess
import sys
import tempfile
from typing import Optional

TIMEOUT = 900
# selftest markers: the fractional part is this process's pid, so the grandchild this run spawned is
# distinguishable from one spawned by a selftest running concurrently in another process
SLEEP_TOK = "30.%d" % os.getpid()
ORPHAN_TOK = "31.%d" % os.getpid()
COMPANION_DIRS = [
    Path.home() / ".gemini" / "agent" / "companions",
    Path(__file__).resolve().parent / "companions",
]


def companion(name):
  env = os.environ.get("DEBATE_COMPANION_" + name.upper().replace("-", "_"))
  if env:
    if not Path(env).exists():
      raise SystemExit("no %s companion at %s" % (name, env))
    return env
  for d in COMPANION_DIRS:
    for candidate in [
        name,
        f"{name}.py",
        f"{name}-companion.py",
        f"{name}-companion.sh",
        f"{name}-companion.mjs",
    ]:
      p = d / candidate
      if p.is_file():
        return str(p)
  if name in ("opus", "gemini", "opus-auditor", "gemini-auditor"):
    runner = Path(__file__).resolve().parent / "companions" / f"{name}.py"
    if runner.is_file():
      return str(runner)
    return f"companion-native:{name}"
  raise SystemExit(
      "no %s companion installed (set DEBATE_COMPANION_%s)"
      % (name, name.upper().replace("-", "_"))
  )


def argv_for(name, path, prompt_file):
  prompt_path = str(Path(prompt_file).resolve())
  if str(path).startswith("companion-native:"):
    bridge = Path(__file__).resolve().parent / "companions" / "bridge.py"
    return [
        sys.executable,
        str(bridge),
        "--model",
        name,
        "--prompt-file",
        prompt_path,
    ]
  p = Path(path)
  if p.suffix == ".py":
    return [sys.executable, str(p), "--prompt-file", prompt_path]
  if os.access(p, os.X_OK):
    return [str(p), "--prompt-file", prompt_path]
  return [sys.executable, str(p), "--prompt-file", prompt_path]


def run(d, name, prompt_file, timeout=TIMEOUT):
  d = Path(d)
  try:
    comp_target = companion(name)
  except SystemExit as e:
    print(
        "FAIL companion resolution error for %s: %s" % (name, e),
        file=sys.stderr,
    )
    return 1

  # Check companion runner exists
  if str(comp_target).startswith("companion-native:"):
    bridge = Path(__file__).resolve().parent / "companions" / "bridge.py"
    if not bridge.is_file():
      print(
          "FAIL companion bridge %s does not exist; cannot run %s"
          % (bridge, name),
          file=sys.stderr,
      )
      return 1
  elif not Path(comp_target).exists():
    print(
        "FAIL companion runner %s does not exist; cannot run %s"
        % (comp_target, name),
        file=sys.stderr,
    )
    return 1

  # The two companions are dispatched in parallel, so the reserve-a-call step is under a file lock;
  # without it two processes can both read 26, both run, and the debate makes 28 calls.
  with open(d / "state.lock", "w") as lk:
    fcntl.flock(lk, fcntl.LOCK_EX)
    st = json.loads((d / "state.json").read_text())
    if st["calls"] >= st["cap"]:
      print("CAP %d" % st["cap"])
      return 3
    st["calls"] += 1
    (d / "state.json").write_text(json.dumps(st))
  calls = d / "calls"
  calls.mkdir(exist_ok=True)
  out_path = calls / ("%02d-%s.out.md" % (st["calls"], name))
  nonce = secrets.token_hex(4)
  sent = calls / ("%02d-%s.prompt.md" % (st["calls"], name))
  sent.write_text(
      "DEBATE-NONCE %s: begin your reply with the single line `NONCE %s`, then"
      " the reply itself.\n\n" % (nonce, nonce)
      + Path(prompt_file).read_text()
  )
  argv = argv_for(name, comp_target, sent)
  with open(out_path, "wb") as out:
    try:
      p = subprocess.Popen(
          argv,
          stdout=out,
          stderr=subprocess.STDOUT,
          stdin=subprocess.DEVNULL,
          cwd=st.get("repo", "."),
          start_new_session=True,
      )
    except OSError as e:
      print("FAIL companion execution error: %s" % e, file=sys.stderr)
      return 1
    try:
      rc = p.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
      os.killpg(p.pid, signal.SIGKILL)
      p.wait()
      print("TIMEOUT %s" % out_path, file=sys.stderr)
      return 2
    try:
      os.killpg(
          p.pid, signal.SIGKILL
      )  # reap anything the companion left behind on a clean exit
    except ProcessLookupError:
      pass
  if ("NONCE %s" % nonce) not in out_path.read_text(errors="replace"):
    print(
        "FAIL 9 %s" % out_path, file=sys.stderr
    )  # the model never read the prompt
    return 9
  if rc != 0:
    print("FAIL %d %s" % (rc, out_path), file=sys.stderr)
    return 1

  # Persist out-of-band companion provenance metadata
  prov_path = calls / ("%02d-%s.prov.json" % (st["calls"], name))
  prov = {
      "companion": name,
      "call": st["calls"],
      "nonce": nonce,
      "parent_session_id": st.get("session_id", "unknown"),
      "subagent_conversation_id": f"subagent-{name}-{nonce}",
      "timestamp": os.path.getmtime(str(out_path)),
  }
  prov_path.write_text(json.dumps(prov))

  print("OK %s" % out_path)
  return 0


def _atomic_write_file(target_path, content):
  """Write content to target_path atomically within the same directory."""
  target = Path(target_path)
  parent = target.parent
  parent.mkdir(parents=True, exist_ok=True)
  with tempfile.NamedTemporaryFile(
      "w", dir=str(parent), delete=False, encoding="utf-8"
  ) as tf:
    tf.write(content)
    tf.flush()
    os.fsync(tf.fileno())
    tmp_name = tf.name
  os.replace(tmp_name, str(target))


def _find_subagent_transcript(subagent_id):
  """Locate subagent transcript on disk, checking standard and mock brain roots with retry polling."""
  import time

  wait_secs = float(os.environ.get("DEBATE_TRANSCRIPT_WAIT_SECS", "2.0"))
  poll_interval = 0.2
  deadline = time.time() + wait_secs

  roots = []
  if os.environ.get("DEBATE_SELFTEST") == "1":
    mock_root = os.environ.get("DEBATE_MOCK_BRAIN_ROOT")
    if mock_root:
      roots.append(Path(mock_root))

  app_data_dir = os.environ.get(
      "AGENT_APP_DATA_DIR", Path.home() / ".gemini" / "agent"
  )
  roots.append(Path(app_data_dir) / "brain")

  while True:
    for root in roots:
      candidates = [
          root
          / subagent_id
          / ".system_generated"
          / "logs"
          / "transcript_full.jsonl",
          root
          / subagent_id
          / ".system_generated"
          / "logs"
          / "transcript.jsonl",
          root / subagent_id / "transcript.jsonl",
      ]
      for c in candidates:
        if c.is_file():
          return c

    if time.time() >= deadline:
      break
    time.sleep(poll_interval)

  return None


_FAMILY_PATTERNS = (
    ("anthropic", re.compile(r"opus|sonnet|haiku|claude", re.I)),
    ("openai", re.compile(r"gpt|oss|o1|o3|o4", re.I)),
    ("google", re.compile(r"gemini|flash", re.I)),
)

_STATIC_ENUM_MAP = {
    "MODEL_CLAUDE_OPUS": ("claude-opus-4-6", "anthropic"),
    "MODEL_CLAUDE_SONNET": ("claude-sonnet-4", "anthropic"),
    "MODEL_GEMINI_PRO": ("gemini-2.5-pro", "google"),
    "MODEL_GEMINI_FLASH": ("gemini-2.5-flash", "google"),
}


def _parse_varint(data: bytes, pos: int):
  res = 0
  shift = 0
  start_pos = pos
  while pos < len(data):
    b = data[pos]
    pos += 1
    res |= (b & 0x7F) << shift
    if not (b & 0x80):
      return res, pos
    shift += 7
    if shift > 64:
      raise ValueError(f"Varint exceeds 64 bits at offset {start_pos}")
  raise ValueError(f"Unexpected EOF reading varint at offset {start_pos}")


def _enum_from_telemetry(blobs: list) -> Optional[str]:
  prefix = b"model_enum\x12"
  for blob in blobs:
    pos = 0
    enums = []
    while True:
      idx = blob.find(prefix, pos)
      if idx == -1:
        break
      len_pos = idx + len(prefix)
      if len_pos >= len(blob):
        break
      try:
        length, str_start = _parse_varint(blob, len_pos)
        if str_start + length <= len(blob):
          raw_enum = blob[str_start : str_start + length].decode(
              "ascii", errors="replace"
          )
          if raw_enum.startswith("MODEL_"):
            enums.append(raw_enum)
        pos = str_start + length
      except Exception:
        pos = len_pos + 1
    if enums:
      return max(sorted(set(enums)), key=lambda e: (enums.count(e), e))
  return None


def _inspect_subagent_model(subagent_id: str):
  """Inspect subagent SQLite database to extract served model_enum and family."""
  import sqlite3
  import time

  if os.environ.get("DEBATE_SELFTEST") == "1":
    mock_enum = os.environ.get("DEBATE_MOCK_SUBAGENT_ENUM")
    if mock_enum:
      slug, fam = _STATIC_ENUM_MAP.get(mock_enum, (mock_enum, "anthropic"))
      return {"model_enum": mock_enum, "model_slug": slug, "family": fam}

  conv_dir = Path.home() / ".gemini" / "agent" / "conversations"
  if os.environ.get("DEBATE_SELFTEST") == "1":
    mock_conv = os.environ.get("DEBATE_MOCK_CONV_DIR")
    if mock_conv:
      conv_dir = Path(mock_conv)

  db_path = conv_dir / f"{subagent_id}.db"
  deadline = time.time() + 3.0
  blobs = []

  while time.time() < deadline:
    if db_path.exists():
      conn = None
      try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5.0)
        c = conn.cursor()
        c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND"
            " name='gen_metadata'"
        )
        if c.fetchone():
          c.execute("SELECT data FROM gen_metadata ORDER BY idx DESC LIMIT 40")
          blobs = [r[0] for r in c.fetchall() if r[0]]
          if blobs:
            break
      except Exception:
        pass
      finally:
        if conn:
          conn.close()
    time.sleep(0.2)

  if not blobs:
    return None

  enum = _enum_from_telemetry(blobs)
  if not enum:
    return None

  slug, fam = _STATIC_ENUM_MAP.get(enum, (None, None))
  if not fam and slug:
    for f_name, pat in _FAMILY_PATTERNS:
      if pat.search(slug):
        fam = f_name
        break
  if not fam:
    fam = "google" if "gemini" in enum.lower() else "anthropic"

  return {"model_enum": enum, "model_slug": slug or enum, "family": fam}


_TURN_HDR_RE = re.compile(
    r"^## R(\d+) (frame|diverge|verify|converge|closing) (\S+)"
    r" role=(none|reviser|advocate)$"
)


def _extract_subagent_response(
    subagent_id: str, nonce: str, wait_seconds: float = 5.0
) -> Optional[str]:
  """Deterministically extract NONCE-prefixed response from subagent transcript."""
  import time

  deadline = time.time() + max(0.1, wait_seconds)
  nonce_prefix = f"NONCE {nonce}"
  prompt_marker = f"DEBATE-NONCE {nonce}"

  while True:
    tr_path = _find_subagent_transcript(subagent_id)
    if tr_path:
      full_path = tr_path.parent / "transcript_full.jsonl"
      candidates = [full_path, tr_path] if full_path.is_file() else [tr_path]
      for cand in candidates:
        try:
          lines = cand.read_text(errors="replace").splitlines()
        except OSError:
          continue
        steps = []
        for line in lines:
          line = line.strip()
          if not line:
            continue
          try:
            steps.append(json.loads(line))
          except Exception:
            continue

        # 1. Verify prompt-origin on the first USER_INPUT step if USER_INPUT steps exist
        user_inputs = [s for s in steps if s.get("type") == "USER_INPUT"]
        if user_inputs:
          first_input = str(user_inputs[0].get("content") or "")
          if prompt_marker not in first_input:
            return None

        # 2. Scan completed MODEL PLANNER_RESPONSE steps in reverse order (last wins)
        for step in reversed(steps):
          if step.get("type") != "PLANNER_RESPONSE":
            continue
          if step.get("source") and step.get("source") != "MODEL":
            continue
          if step.get("status") and step.get("status") != "DONE":
            continue

          # Priority 1: send_message tool call
          for tc in step.get("tool_calls") or []:
            if (tc.get("name") or tc.get("tool_name")) == "send_message":
              raw_args = tc.get("args") or tc.get("arguments") or {}
              if isinstance(raw_args, str):
                try:
                  raw_args = json.loads(raw_args)
                except Exception:
                  raw_args = {}
              msg = str((raw_args or {}).get("Message") or "").lstrip()
              if msg.startswith(nonce_prefix):
                return msg

          # Priority 2: direct content starting with NONCE <nonce>
          content = str(step.get("content") or "").lstrip()
          if content.startswith(nonce_prefix):
            return content

    if time.time() >= deadline:
      break
    time.sleep(0.25)

  return None


def append_turn(
    d,
    round_num: int,
    phase: str,
    participant: str,
    role: str,
    body_or_file: str,
    replace: bool = False,
) -> int:
  """Atomically append a turn section to transcript.md after validating header grammar."""
  d = Path(d)
  header = f"## R{int(round_num)} {phase} {participant} role={role}"
  if not _TURN_HDR_RE.match(header):
    print(
        f"PROTOCOL-ERROR invalid transcript header '{header}'",
        file=sys.stderr,
    )
    return 2

  body_text = str(body_or_file)
  try:
    bp = Path(body_or_file)
    if bp.is_file():
      body_text = bp.read_text(errors="replace")
  except (OSError, ValueError):
    pass

  # Strip leading NONCE <nonce> line so _frame_empty and transcript parsers see clean body
  body_lines = body_text.strip().splitlines()
  if body_lines and re.match(r"^NONCE\s+\w+\s*$", body_lines[0].strip()):
    body_lines = body_lines[1:]
  clean_body = "\n".join(body_lines).strip()

  tr_path = d / "transcript.md"
  existing = tr_path.read_text(errors="replace") if tr_path.is_file() else ""
  header_pat = re.compile(rf"^{re.escape(header)}\s*$", re.M)
  m_existing = header_pat.search(existing)
  if m_existing:
    if not replace:
      print(
          f"PROTOCOL-ERROR duplicate turn header '{header}' already in"
          " transcript.md (use --replace to overwrite)",
          file=sys.stderr,
      )
      return 1
    # Replace section up to next ## R header or EOF
    next_m = re.search(r"^## R\d+ ", existing[m_existing.end() :], re.M)
    sec_end = m_existing.end() + next_m.start() if next_m else len(existing)
    updated = (
        existing[: m_existing.start()].rstrip()
        + ("\n\n" if existing[: m_existing.start()].strip() else "")
        + f"{header}\n{clean_body}\n\n"
        + existing[sec_end:].lstrip()
    )
    _atomic_write_file(tr_path, updated.rstrip() + "\n")
    return 0

  sep = "\n\n" if existing.strip() else ""
  updated = existing.rstrip() + f"{sep}{header}\n{clean_body}\n"
  _atomic_write_file(tr_path, updated)
  return 0


def dispatch_turn(
    d,
    name: str,
    prompt_file: str,
    phase: Optional[str] = None,
    round_num: Optional[int] = None,
    role: str = "none",
    timeout: int = 180,
) -> int:
  """Launch a stateless cross-family companion call via bridge.py and record turn."""
  d = Path(d)
  prompt_path = Path(prompt_file)
  if not prompt_path.is_file():
    print(f"AUTH-ERROR: prompt file {prompt_file} not found", file=sys.stderr)
    return 5

  prompt_text = prompt_path.read_text(errors="replace")
  m_nonce = re.search(r"DEBATE-NONCE (\w+)", prompt_text)
  if not m_nonce:
    print(
        f"AUTH-ERROR: prompt file {prompt_file} missing DEBATE-NONCE",
        file=sys.stderr,
    )
    return 5
  nonce = m_nonce.group(1)

  bridge = Path(__file__).resolve().parent / "companions" / "bridge.py"
  try:
    proc = subprocess.run(
        [sys.executable, str(bridge), "--model", name, "--prompt-file", str(prompt_path)],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
  except subprocess.TimeoutExpired:
    print(f"TIMEOUT waiting for companion {name}", file=sys.stderr)
    return 2

  response_text = (proc.stdout or "").strip()
  if proc.returncode != 0 or not response_text:
    print(f"FAIL companion {name}: {proc.stderr}", file=sys.stderr)
    return 1

  child_id = f"companion-{name}-{nonce}"
  return record_turn(
      d,
      name,
      child_id,
      prompt_file,
      response_file_or_text=response_text,
      phase=phase,
      round_num=round_num,
      role=role,
  )


def record_turn(
    d,
    name,
    subagent_id,
    prompt_file,
    response_file_or_text=None,
    response_file=None,
    phase=None,
    round_num=None,
    role="none",
):
  """Record an interactive subagent companion turn with verified out-of-band provenance."""
  d = Path(d)
  prompt_path = Path(prompt_file)
  if not prompt_path.is_file():
    print("AUTH-ERROR: prompt file %s not found" % prompt_file, file=sys.stderr)
    return 5

  prompt_text = prompt_path.read_text(errors="replace")
  m_nonce = re.search(r"DEBATE-NONCE (\w+)", prompt_text)
  if not m_nonce:
    print(
        "AUTH-ERROR: prompt file %s missing required DEBATE-NONCE"
        % prompt_file,
        file=sys.stderr,
    )
    return 5
  nonce = m_nonce.group(1)

  st_path = d / "state.json"
  if not st_path.is_file():
    print("AUTH-ERROR: state.json not found in %s" % d, file=sys.stderr)
    return 1

  # Anti-simulation transcript check
  st = json.loads(st_path.read_text())
  parent_session = st.get("session_id", "unknown")
  if subagent_id == parent_session:
    print(
        "AUTH-ERROR: subagent_id matches parent session %s (self-simulation)"
        % parent_session,
        file=sys.stderr,
    )
    return 4

  if not st.get("mock_for_testing"):
    tr_path = _find_subagent_transcript(subagent_id)
    if not tr_path:
      print(
          "AUTH-ERROR: subagent conversation %s has no authentic transcript on"
          " disk" % subagent_id,
          file=sys.stderr,
      )
      return 4

  response_text = None
  if response_file:
    resp_p = Path(response_file)
    if not resp_p.is_file():
      print(
          "AUTH-ERROR: response file %s not found" % response_file,
          file=sys.stderr,
      )
      return 5
    response_text = resp_p.read_text(errors="replace")
  elif response_file_or_text is not None:
    try:
      resp_p = Path(response_file_or_text)
      if resp_p.is_file():
        response_text = resp_p.read_text(errors="replace")
      else:
        response_text = str(response_file_or_text)
    except (OSError, ValueError):
      response_text = str(response_file_or_text)
  else:
    response_text = _extract_subagent_response(subagent_id, nonce)
    if not response_text:
      print(
          "AUTH-ERROR: no response file provided and auto-extraction from"
          " subagent %s failed" % subagent_id,
          file=sys.stderr,
      )
      return 5

  if ("NONCE %s" % nonce) not in response_text:
    print(
        "AUTH-ERROR: companion response does not echo NONCE %s" % nonce,
        file=sys.stderr,
    )
    return 6

  driver_family = st.get("driver_family")
  if not driver_family or driver_family == "gemini":
    driver_name = st.get("driver", "gemini")
    driver_family = (
        "google"
        if any(k in driver_name.lower() for k in ("gemini", "google"))
        else "anthropic"
    )

  model_attestation = _inspect_subagent_model(subagent_id)
  if (
      not st.get("mock_for_testing")
      and os.environ.get("DEBATE_SELFTEST") != "1"
  ):
    if not model_attestation:
      print(
          "AUTH-ERROR: companion conversation %s has no valid generation"
          " metadata on disk" % subagent_id,
          file=sys.stderr,
      )
      return 4
    comp_family = model_attestation.get("family")
    if comp_family == driver_family and not st.get("allow_same_family"):
      print(
          "AUTH-ERROR: companion %s belongs to same model family (%s) as"
          " driver (self-simulation)" % (name, comp_family),
          file=sys.stderr,
      )
      return 4
  elif not model_attestation:
    # Test or mock fallback
    comp_fam = "anthropic" if driver_family == "google" else "google"
    model_attestation = {
        "model_enum": (
            "MODEL_CLAUDE_OPUS"
            if comp_fam == "anthropic"
            else "MODEL_GEMINI_PRO"
        ),
        "model_slug": (
            "claude-opus-4-6" if comp_fam == "anthropic" else "gemini-2.5-pro"
        ),
        "family": comp_fam,
    }

  with open(d / "state.lock", "w") as lk:
    fcntl.flock(lk, fcntl.LOCK_EX)
    st = json.loads((d / "state.json").read_text())
    if st["calls"] >= st["cap"]:
      print("CAP %d" % st["cap"])
      return 3
    st["calls"] += 1
    _atomic_write_file(d / "state.json", json.dumps(st))

  calls = d / "calls"
  calls.mkdir(parents=True, exist_ok=True)
  raw_path = calls / ("%02d-%s.raw.md" % (st["calls"], name))
  if not raw_path.exists():
    _atomic_write_file(raw_path, response_text)
  out_path = calls / ("%02d-%s.out.md" % (st["calls"], name))
  if out_path.exists():
    print(
        "AUTH-ERROR: %s already exists (clobber prevented)" % out_path,
        file=sys.stderr,
    )
    return 1
  _atomic_write_file(out_path, response_text)

  appended = False
  if phase and round_num is not None:
    rc = append_turn(
        d, int(round_num), phase, name, role or "none", response_text
    )
    if rc != 0:
      return rc
    appended = True

  prov_path = calls / ("%02d-%s.prov.json" % (st["calls"], name))
  prov = {
      "companion": name,
      "call": st["calls"],
      "nonce": nonce,
      "parent_session_id": parent_session,
      "subagent_conversation_id": subagent_id,
      "model_enum": model_attestation.get("model_enum"),
      "model_slug": model_attestation.get("model_slug"),
      "family": model_attestation.get("family"),
      "attestation_status": "CONFIRMED",
      "timestamp": os.path.getmtime(str(out_path)),
      "phase": phase,
      "round": int(round_num) if round_num is not None else None,
      "appended_to_transcript": appended,
  }
  _atomic_write_file(prov_path, json.dumps(prov))

  print("OK %s" % out_path)
  return 0


def _reaped(tok):
  """True once no `sleep <tok>` is left.

  A kill is asynchronous, so a single pgrep right after it races the kernel; and
  the duration carries this run's pid, so a selftest running in parallel (the
  preflight of another debate) can no longer be mistaken for our own unreaped
  grandchild.
  """
  import time

  for _ in range(40):
    left = subprocess.run(
        ["pgrep", "-f", "sleep " + tok], capture_output=True, text=True
    ).stdout.split()
    if not left:
      return []
    time.sleep(0.05)
  return left


def selftest():
  os.environ["DEBATE_SELFTEST"] = "1"
  import tempfile
  import threading
  import time

  d = Path(tempfile.mkdtemp())
  stub = d / "stub.py"
  # the stub reads its prompt (by --prompt-file or READ-FILE path), echoes
  # the nonce, and on SLEEP or ORPHAN spawns a child; blind.py never reads the prompt
  stub_code = (
      "import sys, re, subprocess, time\nfrom pathlib import Path\na ="
      " sys.argv[1:]\np = ''\nif '--prompt-file' in a:\n    p ="
      " Path(a[a.index('--prompt-file') + 1]).read_text()\nelse:\n    for arg"
      " in a:\n        m = re.match(r'^READ-FILE (\\S+)', arg)\n        if m:\n"
      "            p = Path(m.group(1)).read_text()\n           "
      " break\nprint('ARGS ' + ' '.join(a))\nm = re.search(r'DEBATE-NONCE"
      " (\\w+)', p)\nif m:\n    print('NONCE ' + m.group(1))\nif 'SLEEP' in"
      " p:\n    subprocess.Popen(['sleep', '%s'], stdin=subprocess.DEVNULL,"
      " stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n   "
      " time.sleep(30)\nif 'ORPHAN' in p:\n    subprocess.Popen(['sleep',"
      " '%s'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,"
      " stderr=subprocess.DEVNULL)\n" % (SLEEP_TOK, ORPHAN_TOK)
  )
  stub.write_text(stub_code)
  blind = d / "blind.py"
  blind.write_text("import sys\nprint('ARGS ' + ' '.join(sys.argv[1:]))\n")
  os.environ["DEBATE_COMPANION_OPUS"] = str(stub)
  os.environ["DEBATE_COMPANION_GEMINI"] = str(stub)
  (d / "state.json").write_text(
      json.dumps({"calls": 0, "cap": 2, "repo": str(d)})
  )
  pf = d / "p.md"
  pf.write_text("hello")
  assert run(d, "opus", pf) == 0
  out = (d / "calls" / "01-opus.out.md").read_text()
  assert "--prompt-file" in out, out
  assert (d / "calls" / "01-opus.prompt.md").read_text().endswith("hello")
  assert "NONCE " in out, "opus stub must echo the nonce it read"
  assert run(d, "gemini", pf) == 0
  gemini_out = (d / "calls" / "02-gemini.out.md").read_text()
  assert "--prompt-file" in gemini_out or "READ-FILE" in gemini_out, gemini_out
  assert (
      "NONCE " in gemini_out
  ), "gemini stub must have read the file to find the nonce"
  assert (
      (d / "calls" / "02-gemini.prompt.md")
      .read_text()
      .startswith("DEBATE-NONCE ")
  )
  assert run(d, "opus", pf) == 3, "cap must refuse the third call"
  assert json.loads((d / "state.json").read_text())["calls"] == 2
  (d / "state.json").write_text(
      json.dumps({"calls": 0, "cap": 9, "repo": str(d)})
  )
  pf.write_text("SLEEP")

  t0 = time.time()
  assert run(d, "opus", pf, timeout=2) == 2
  assert time.time() - t0 < 10, "timeout must fire"
  left = _reaped(SLEEP_TOK)
  assert not left, "process group kill must reap the grandchild: %s" % left
  # a child left behind after a CLEAN exit is reaped too
  pf.write_text("ORPHAN")
  assert run(d, "opus", pf) == 0
  left = _reaped(ORPHAN_TOK)
  assert not left, (
      "process group kill must reap children after a clean exit: %s" % left
  )
  # a companion that never reads the prompt is a FAIL, not a silent blind turn
  os.environ["DEBATE_COMPANION_GEMINI"] = str(blind)
  (d / "state.json").write_text(
      json.dumps({"calls": 0, "cap": 9, "repo": str(d)})
  )
  pf.write_text("hello")
  assert run(d, "gemini", pf) == 9
  os.environ["DEBATE_COMPANION_GEMINI"] = str(stub)
  # an env override naming a missing file is a resolution error, not a fallback to the installed companion
  os.environ["DEBATE_COMPANION_OPUS"] = str(d / "missing.py")
  try:
    companion("opus")
    assert False, "expected SystemExit"
  except SystemExit as e:
    assert "missing.py" in str(e)
  os.environ["DEBATE_COMPANION_OPUS"] = str(stub)
  # the cap holds under parallel dispatch: four concurrent calls against cap 2 -> exactly two run
  (d / "state.json").write_text(
      json.dumps({"calls": 0, "cap": 2, "repo": str(d)})
  )
  pf.write_text("hello")

  rcs = []
  ths = [
      threading.Thread(target=lambda: rcs.append(run(d, "opus", pf)))
      for _ in range(4)
  ]
  for t in ths:
    t.start()
  for t in ths:
    t.join()
  assert sorted(rcs) == [0, 0, 3, 3], rcs

  assert json.loads((d / "state.json").read_text())["calls"] == 2

  # Test run() when companion runner fails or does not exist: fails with actionable error and no fake IDs
  d_miss = d / "miss_dir"
  d_miss.mkdir(exist_ok=True)
  (d_miss / "state.json").write_text(
      json.dumps({"calls": 0, "cap": 5, "repo": str(d)})
  )
  os.environ["DEBATE_COMPANION_NONEXIST"] = str(d / "does_not_exist.py")
  rc_miss = run(d_miss, "nonexist", pf)
  assert rc_miss != 0
  assert not (d_miss / "calls" / "01-nonexist.prov.json").exists()

  # record_turn tests
  d_rec = Path(tempfile.mkdtemp())
  (d_rec / "state.json").write_text(
      json.dumps({
          "calls": 0,
          "cap": 5,
          "session_id": "parent-session-123",
          "repo": str(d_rec),
      })
  )
  mock_brain = d_rec / "mock_brain"
  os.environ["DEBATE_MOCK_BRAIN_ROOT"] = str(mock_brain)
  subagent_id = "subagent-conv-abc"
  subagent_tr_dir = mock_brain / subagent_id
  subagent_tr_dir.mkdir(parents=True, exist_ok=True)
  (subagent_tr_dir / "transcript.jsonl").write_text('{"event": "start"}\n')

  pf_rec = d_rec / "prompt.md"
  pf_rec.write_text("DEBATE-NONCE 9f8e7d\nBrief content")

  # Successful record_turn
  good_resp = "NONCE 9f8e7d\nCompanion findings: all good."
  assert record_turn(d_rec, "opus", subagent_id, pf_rec, good_resp) == 0
  st_after = json.loads((d_rec / "state.json").read_text())
  assert st_after["calls"] == 1
  out_file = d_rec / "calls" / "01-opus.out.md"
  assert out_file.is_file() and "Companion findings" in out_file.read_text()
  prov_file = d_rec / "calls" / "01-opus.prov.json"
  assert prov_file.is_file()
  prov_data = json.loads(prov_file.read_text())
  assert prov_data["companion"] == "opus"
  assert prov_data["nonce"] == "9f8e7d"
  assert prov_data["subagent_conversation_id"] == subagent_id
  assert prov_data["parent_session_id"] == "parent-session-123"
  assert prov_data["attestation_status"] == "CONFIRMED"
  assert prov_data["family"] in ("anthropic", "google")

  # Test --response-file flag to record_turn
  resp_file = d_rec / "resp_file.md"
  resp_file.write_text("NONCE 9f8e7d\nCompanion findings: via response file.")
  pf_rec_file = d_rec / "prompt_rf.md"
  pf_rec_file.write_text("DEBATE-NONCE 9f8e7d\nMore content")
  assert (
      record_turn(
          d_rec,
          "opus",
          subagent_id,
          pf_rec_file,
          response_file=str(resp_file),
      )
      == 0
  )
  st_after2 = json.loads((d_rec / "state.json").read_text())
  assert st_after2["calls"] == 2
  out_file2 = d_rec / "calls" / "02-opus.out.md"
  assert out_file2.is_file() and "via response file" in out_file2.read_text()

  # Test --response-file missing file
  assert (
      record_turn(
          d_rec,
          "opus",
          subagent_id,
          pf_rec_file,
          response_file=str(d_rec / "missing_resp.md"),
      )
      == 5
  )

  # Test flush retry polling in _find_subagent_transcript
  delayed_id = "subagent-delayed-flush"
  delayed_dir = mock_brain / delayed_id

  def _delayed_create():
    time.sleep(0.3)
    delayed_dir.mkdir(parents=True, exist_ok=True)
    (delayed_dir / "transcript.jsonl").write_text("{}\n")

  t_flush = threading.Thread(target=_delayed_create)
  t_flush.start()
  os.environ["DEBATE_TRANSCRIPT_WAIT_SECS"] = "2.0"
  found = _find_subagent_transcript(delayed_id)
  t_flush.join()
  assert (
      found is not None and found.is_file()
  ), "Flush retry should discover transcript after delay"

  # Verify mock root is ignored when DEBATE_SELFTEST != "1"
  mock_gate_id = "subagent-mock-gate-test"
  mock_gate_dir = mock_brain / mock_gate_id
  mock_gate_dir.mkdir(parents=True, exist_ok=True)
  (mock_gate_dir / "transcript.jsonl").write_text("{}\n")
  try:
    os.environ["DEBATE_TRANSCRIPT_WAIT_SECS"] = "0.05"
    os.environ["DEBATE_SELFTEST"] = "0"
    assert _find_subagent_transcript(mock_gate_id) is None
    os.environ["DEBATE_SELFTEST"] = "1"
    assert _find_subagent_transcript(mock_gate_id) is not None
  finally:
    os.environ["DEBATE_SELFTEST"] = "1"
    os.environ["DEBATE_TRANSCRIPT_WAIT_SECS"] = "2.0"

  # Negative test: missing nonce in prompt
  bad_pf = d_rec / "bad_prompt.md"
  bad_pf.write_text("No nonce here")
  assert record_turn(d_rec, "opus", subagent_id, bad_pf, good_resp) == 5

  # Negative test: companion response doesn't echo nonce
  bad_resp = "NONCE wrongnonce\nWrong!"
  assert record_turn(d_rec, "opus", subagent_id, pf_rec, bad_resp) == 6

  # Negative test: fake subagent with no transcript on disk
  assert (
      record_turn(d_rec, "opus", "fake-nonexistent-subagent", pf_rec, good_resp)
      == 4
  )

  # Negative test: subagent_id matches parent session_id (self-simulation)
  (mock_brain / "parent-session-123").mkdir(parents=True, exist_ok=True)
  (mock_brain / "parent-session-123" / "transcript.jsonl").write_text("{}\n")
  assert (
      record_turn(d_rec, "opus", "parent-session-123", pf_rec, good_resp) == 4
  )

  # Test auto-extraction when --response-file is omitted (Priority 1 send_message + Priority 2 content + append_turn)
  auto_id = "subagent-auto-extract-777"
  auto_dir = mock_brain / auto_id / ".system_generated" / "logs"
  auto_dir.mkdir(parents=True, exist_ok=True)
  pf_auto = d_rec / "prompt_auto.md"
  pf_auto.write_text("DEBATE-NONCE auto999\nPlease evaluate brief.")
  auto_steps = [
      {
          "step_index": 0,
          "type": "USER_INPUT",
          "status": "DONE",
          "content": "DEBATE-NONCE auto999\nPlease evaluate brief.",
      },
      {
          "step_index": 1,
          "type": "PLANNER_RESPONSE",
          "source": "MODEL",
          "status": "DONE",
          "content": "NONCE auto999\nMISSING QUESTIONS:\n1. Draft question",
          "tool_calls": [{
              "name": "send_message",
              "args": {
                  "Recipient": "parent-session-123",
                  "Message": (
                      "NONCE auto999\nMISSING QUESTIONS:\n1. Final"
                      " question\nCRITERIA:\nnone\nSEED ITEMS:\nnone\nQUESTIONS"
                      " FOR HUMAN:\nnone"
                  ),
              },
          }],
      },
  ]
  (auto_dir / "transcript_full.jsonl").write_text(
      "\n".join(json.dumps(s) for s in auto_steps) + "\n"
  )
  assert (
      record_turn(
          d_rec,
          "opus",
          auto_id,
          pf_auto,
          phase="frame",
          round_num=1,
          role="none",
      )
      == 0
  )
  tr_content = (d_rec / "transcript.md").read_text()
  assert "## R1 frame opus role=none" in tr_content, tr_content
  assert "1. Final question" in tr_content, tr_content
  assert (
      "NONCE auto999" not in tr_content
  ), "NONCE line should be stripped from transcript.md"
  # Duplicate append without --replace must fail
  assert (
      append_turn(d_rec, 1, "frame", "opus", "none", "duplicate body") == 1
  ), "Duplicate turn header must be rejected"
  assert (
      append_turn(
          d_rec, 1, "frame", "opus", "none", "replaced body", replace=True
      )
      == 0
  )

  print("debate_round selftest: ok")


def _print_usage(stream=sys.stdout):
  print(
      "Usage:\n"
      "  debate_round.py <dir> <companion> <prompt_file>\n"
      "  debate_round.py record-turn <dir> <companion> <subagent_id>"
      " <prompt_file> [--response-file <file>] [--phase <p> --round <n>"
      " [--role <role>]]\n"
      "  debate_round.py append-turn <dir> <round> <phase> <participant>"
      " <role> <body_or_file> [--replace]\n"
      "  debate_round.py dispatch-turn <dir> <companion> <prompt_file>"
      " [--phase <p> --round <n> [--role <role>]]\n"
      "  debate_round.py --selftest",
      file=stream,
  )


if __name__ == "__main__":
  if not sys.argv[1:] or sys.argv[1] in ("-h", "--help"):
    _print_usage(sys.stdout)
    sys.exit(0)
  if sys.argv[1:] == ["--selftest"]:
    selftest()
    sys.exit(0)
  if sys.argv[1] == "append-turn":
    args = sys.argv[2:]
    replace_flag = "--replace" in args
    args = [a for a in args if a != "--replace"]
    if len(args) < 6:
      _print_usage(sys.stderr)
      sys.exit(2)
    sys.exit(
        append_turn(
            args[0],
            int(args[1]),
            args[2],
            args[3],
            args[4],
            args[5],
            replace=replace_flag,
        )
    )
  if sys.argv[1] == "dispatch-turn":
    args = sys.argv[2:]
    if len(args) < 3:
      _print_usage(sys.stderr)
      sys.exit(2)
    d_arg, comp_arg, pf_arg = args[0], args[1], args[2]
    rest = args[3:]
    phase_val = None
    round_val = None
    role_val = "none"
    idx = 0
    while idx < len(rest):
      if rest[idx] == "--phase" and idx + 1 < len(rest):
        phase_val = rest[idx + 1]
        idx += 2
      elif rest[idx] == "--round" and idx + 1 < len(rest):
        round_val = int(rest[idx + 1])
        idx += 2
      elif rest[idx] == "--role" and idx + 1 < len(rest):
        role_val = rest[idx + 1]
        idx += 2
      else:
        idx += 1
    sys.exit(
        dispatch_turn(
            d_arg,
            comp_arg,
            pf_arg,
            phase=phase_val,
            round_num=round_val,
            role=role_val,
        )
    )
  if sys.argv[1] == "record-turn":
    args = sys.argv[2:]
    if len(args) < 4:
      _print_usage(sys.stderr)
      sys.exit(2)
    d = args[0]
    companion_name = args[1]
    subagent_id = args[2]
    prompt_file = args[3]
    rest = args[4:]

    response_file = None
    response_text = None
    phase_val = None
    round_val = None
    role_val = "none"
    idx = 0
    positional = []
    while idx < len(rest):
      if rest[idx] == "--response-file":
        if idx + 1 >= len(rest):
          print("Usage: --response-file <file>", file=sys.stderr)
          sys.exit(2)
        response_file = rest[idx + 1]
        idx += 2
      elif rest[idx] == "--phase" and idx + 1 < len(rest):
        phase_val = rest[idx + 1]
        idx += 2
      elif rest[idx] == "--round" and idx + 1 < len(rest):
        round_val = int(rest[idx + 1])
        idx += 2
      elif rest[idx] == "--role" and idx + 1 < len(rest):
        role_val = rest[idx + 1]
        idx += 2
      else:
        positional.append(rest[idx])
        idx += 1
    if positional and response_file is None:
      response_text = positional[0]

    sys.exit(
        record_turn(
            d,
            companion_name,
            subagent_id,
            prompt_file,
            response_file_or_text=response_text,
            response_file=response_file,
            phase=phase_val,
            round_num=round_val,
            role=role_val,
        )
    )
  if len(sys.argv) < 4:
    _print_usage(sys.stderr)
    sys.exit(2)
  sys.exit(run(sys.argv[1], sys.argv[2], sys.argv[3]))
