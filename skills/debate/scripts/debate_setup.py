#!/usr/bin/env python3
"""debate_setup.py <repo> --mode attended|unattended [--heading H ...] - preflight and pin.

Creates the debate directory, pins the repo at a detached worktree of its
current HEAD (so every
citation check reads the tree the participants read, however the live checkout
moves), runs the
other scripts' selftests, and checks both companions resolve. `--teardown <dir>`
removes the
pinned worktree. Found necessary by the first real run, where fixes landed
mid-debate moved 44
correct citations off their lines.
"""

import datetime
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from debate_round import companion  # noqa: E402

CAPS = {"diverge": 2, "verify": 2, "converge": 3}


def discover_conversation_id():
  """Discover active conversation ID from environment or newest DB."""
  for env_var in (
      "CONVERSATION_ID",
      "AGENT_CONVERSATION_ID",
      "AGENT_SESSION_ID",
  ):
    val = os.environ.get(env_var)
    if val and val.strip() and val.strip() != "default-session":
      return val.strip()
  conv_dir = Path.home() / ".gemini" / "agent" / "conversations"
  if conv_dir.is_dir():
    dbs = sorted(
        conv_dir.glob("*.db"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    for db in dbs:
      stem = db.stem
      if re.match(
          r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
          stem,
      ):
        return stem
  return None


def setup(
    repo,
    mode,
    headings,
    driver=None,
    companion_model=None,
    scope=None,
    session_id=None,
):
  root = Path(
      os.environ.get(
          "DEBATE_STATE_ROOT",
          os.path.expanduser("~/.gemini/agent/state/debate"),
      )
  )
  did = "d-%s-%s" % (datetime.date.today().isoformat(), secrets.token_hex(3))
  d = root / did
  (d / "calls").mkdir(parents=True)
  repo_path = Path(repo).resolve()
  pinned = d / "pinned"
  is_git = (repo_path / ".git").is_dir() or (repo_path / ".git").is_file()
  sha = "unknown"
  if is_git:
    r = subprocess.run(
        ["git", "-C", str(repo_path), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
      return d, "git rev-parse failed: %s" % r.stderr.strip()
    sha = r.stdout.strip()
    r = subprocess.run(
        [
            "git",
            "-C",
            str(repo_path),
            "worktree",
            "add",
            "--detach",
            "-q",
            str(pinned),
            sha,
        ],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
      return d, "worktree add failed: %s" % r.stderr.strip()
  else:
    r = subprocess.run(
        [
            "hg",
            "-R",
            str(repo_path),
            "log",
            "-r",
            ".",
            "-T",
            "{node}",
            "--pager=never",
            "--noninteractive",
        ],
        capture_output=True,
        text=True,
    )
    if r.returncode == 0 and r.stdout.strip():
      sha = r.stdout.strip()
    else:
      sha = "head"

    # Pre-flight bound check using os.scandir: check total file count (<5,000 files)
    # and total size (<50MB) to prevent unbounded workspace traversal.
    ignore_names = {
        ".ws_snap",
        ".hg",
        ".git",
        "node_modules",
        ".hg_snap",
        "__pycache__",
    }
    scan_target = (repo_path / scope) if scope else repo_path
    if not scan_target.exists():
      return d, "scoped path does not exist: %s" % scan_target

    file_count = 0
    total_size = 0
    stack = [str(scan_target)]
    while stack:
      curr = stack.pop()
      try:
        with os.scandir(curr) as it:
          for entry in it:
            name = entry.name
            if name in ignore_names:
              continue
            if (
                name.startswith("build-")
                or name.startswith("bazel-")
                or name.endswith(".pyc")
            ):
              continue
            try:
              if entry.is_dir(follow_symlinks=False):
                stack.append(entry.path)
              else:
                file_count += 1
                total_size += entry.stat(follow_symlinks=False).st_size
                if file_count >= 5000 or total_size >= 50 * 1024 * 1024:
                  scope_advice = (
                      ""
                      if scope
                      else (
                          ". Specify a target subdirectory using --scope"
                          " <relpath> (e.g. --scope path/to/pkg)"
                      )
                  )
                  return (
                      d,
                      "repo path exceeds freeze budget: %d files, %d bytes%s"
                      % (file_count, total_size, scope_advice),
                  )
            except OSError:
              continue
      except OSError as e:
        return d, "scandir failed: %s" % e

    try:
      ignore_pat = shutil.ignore_patterns(
          ".ws_snap",
          ".hg",
          ".git",
          "build-*",
          "bazel-*",
          "node_modules",
          ".hg_snap",
          "__pycache__",
          "*.pyc",
      )
      if scope:
        target_pinned = pinned / scope
        target_pinned.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(
            str(scan_target),
            str(target_pinned),
            ignore=ignore_pat,
            symlinks=True,
        )
      else:
        shutil.copytree(
            str(repo_path),
            str(pinned),
            ignore=ignore_pat,
            symlinks=True,
        )
    except OSError as e:
      return d, "pin copytree failed: %s" % e
  (d / "transcript.md").write_text("")
  (d / "artifact.md").write_text(
      "".join("## %s\n\n" % h for h in headings) + "## Assumptions\n"
  )
  driver_name = driver or os.environ.get("DEBATE_DRIVER", "gemini")
  if companion_model:
    comps = [companion_model]
  elif driver_name == "gemini":
    comps = ["opus"]
  else:
    comps = ["gemini"]

  def _model_family(m):
    m_lower = m.lower()
    if any(k in m_lower for k in ("gemini", "google")):
      return "gemini"
    if any(k in m_lower for k in ("opus", "claude", "anthropic")):
      return "anthropic"
    return m_lower

  driver_family = _model_family(driver_name)
  companion_families = [_model_family(c) for c in comps]
  resolved_session = session_id or discover_conversation_id()
  if not resolved_session and os.environ.get("DEBATE_SELFTEST") == "1":
    resolved_session = "00000000-0000-4000-8000-000000000001"
  if not resolved_session:
    return (
        d,
        (
            "could not determine active conversation ID (specify via"
            " --session-id or set CONVERSATION_ID)"
        ),
    )
  session_id = resolved_session

  st = {
      "id": did,
      "repo": str(pinned),
      "repo_live": str(repo_path),
      "scope": scope,
      "pinned": sha,
      "driver": driver_name,
      "driver_family": driver_family,
      "companions": comps,
      "companion_families": companion_families,
      "session_id": session_id,
      "calls": 0,
      "cap": 27,
      "phase": "frame",
      "round": 1,
      "caps": CAPS,
      "mode": mode,
      "exits": [],
      "started": (
          datetime.datetime.now(datetime.timezone.utc).strftime(
              "%Y-%m-%dT%H:%M:%SZ"
          )
      ),
  }
  (d / "state.json").write_text(json.dumps(st))
  err = _preflight(repo, comps)
  if err:
    _unpin(
        str(repo_path), pinned
    )  # never leave a pinned worktree behind a failed preflight
    return d, err
  return d, None


def stale_pins(root, skip):
  out = []
  now = time.time()
  for p in sorted(Path(root).glob("d-*")):
    if p == skip or not (
        (p / "pinned").is_dir() or (p / "pinned").is_symlink()
    ):
      continue
    try:
      st = json.loads((p / "state.json").read_text())
    except (OSError, ValueError):
      continue
    last = (st.get("exits") or [["setup", 0, "-"]])[-1]
    try:
      idle = "%.0fm since a turn" % (
          (now - (p / "transcript.md").stat().st_mtime) / 60
      )
    except OSError:
      idle = "no transcript"
    out.append(
        "STALE PIN %s (last %s R%s %s, %s, report.md %s)"
        % (
            p,
            last[0],
            last[1],
            last[2],
            idle,
            "yes" if (p / "report.md").exists() else "NO",
        )
    )
  return out


def _preflight(repo, companions=None):
  comps = companions or ["opus"]
  for name in comps:
    try:
      companion(name)
    except SystemExit as e:
      return str(e)
  here = Path(__file__).resolve().parent
  test_env = dict(os.environ, DEBATE_SELFTEST="1")
  for s in (
      "debate_cite.py",
      "debate_exit.py",
      "debate_round.py",
      "debate_report.py",
  ):
    r = subprocess.run(
        [sys.executable, str(here / s), "--selftest"],
        capture_output=True,
        text=True,
        env=test_env,
    )
    if r.returncode != 0:
      return "selftest red: %s\n%s" % (s, (r.stdout + r.stderr)[-1500:])
  log = Path(
      os.environ.get(
          "DEBATE_STATE_ROOT",
          os.path.expanduser("~/.gemini/agent/state/debate"),
      )
  )
  log.mkdir(parents=True, exist_ok=True)
  if not os.access(log, os.W_OK):
    return "log dir not writable: %s" % log
  return None


def _unpin(repo, pinned):
  pinned = Path(pinned)
  if pinned.is_symlink():
    pinned.unlink()
    return None
  if not pinned.exists():
    return None
  if (pinned / ".git").is_file():
    r = subprocess.run(
        ["git", "-C", repo, "worktree", "remove", "--force", str(pinned)],
        capture_output=True,
        text=True,
    )
    return None if r.returncode == 0 else r.stderr.strip()
  if pinned.is_dir():
    try:
      shutil.rmtree(pinned)
      return None
    except OSError as e:
      return str(e)
  return None


def teardown(d):
  st = json.loads((Path(d) / "state.json").read_text())
  return _unpin(st["repo_live"], st["repo"])


def selftest():
  os.environ["DEBATE_SELFTEST"] = "1"
  import tempfile

  tmp = Path(tempfile.mkdtemp())
  repo = tmp / "repo"
  repo.mkdir()
  subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
  (repo / "a.py").write_text("x = 1\n")
  subprocess.run(["git", "-C", str(repo), "add", "a.py"], check=True)
  subprocess.run(
      [
          "git",
          "-C",
          str(repo),
          "-c",
          "user.email=t@t",
          "-c",
          "user.name=t",
          "commit",
          "-q",
          "-m",
          "one",
      ],
      check=True,
  )
  os.environ["DEBATE_STATE_ROOT"] = str(tmp / "state")
  stub = tmp / "stub.py"
  stub.write_text("print('ok')")
  os.environ["DEBATE_COMPANION_OPUS"] = str(stub)
  os.environ["DEBATE_COMPANION_GEMINI"] = str(stub)
  d, err = setup(
      str(repo),
      "attended",
      ["Options"],
      driver="gemini",
      companion_model="opus",
  )
  assert err is None, err
  st = json.loads((d / "state.json").read_text())
  assert st["driver"] == "gemini" and st["companions"] == ["opus"]
  assert Path(st["repo"]).is_dir() and (Path(st["repo"]) / "a.py").exists()
  assert (
      st["pinned"]
      == subprocess.run(
          ["git", "-C", str(repo), "rev-parse", "HEAD"],
          capture_output=True,
          text=True,
      ).stdout.strip()
  )
  # the live repo moves; the pinned tree does not
  (repo / "a.py").write_text("x = 2\n")
  assert (Path(st["repo"]) / "a.py").read_text() == "x = 1\n"
  assert (d / "artifact.md").read_text() == "## Options\n\n## Assumptions\n"
  assert (d / "transcript.md").read_text() == ""
  assert st["exits"] == [] and st["mode"] == "attended"
  # git worktree teardown
  assert teardown(d) is None
  assert not Path(st["repo"]).exists()

  # non-git directory pinning via hermetic subtree freeze
  nongit = tmp / "nongit"
  nongit.mkdir()
  (nongit / "b.txt").write_text("data\n")
  d_ng, err_ng = setup(
      str(nongit),
      "attended",
      ["Options"],
      driver="gemini",
      companion_model="opus",
  )
  assert err_ng is None, err_ng
  st_ng = json.loads((d_ng / "state.json").read_text())
  assert Path(st_ng["repo"]).is_dir() and not Path(st_ng["repo"]).is_symlink()
  assert (Path(st_ng["repo"]) / "b.txt").read_text() == "data\n"
  # live changes to nongit do not affect frozen copy
  (nongit / "b.txt").write_text("modified\n")
  assert (Path(st_ng["repo"]) / "b.txt").read_text() == "data\n"
  assert teardown(d_ng) is None
  assert not Path(st_ng["repo"]).exists()

  # freeze budget limit check
  big_dir = tmp / "big_dir"
  big_dir.mkdir()
  # Create a file that exceeds 50MB (using sparse file via truncate to be fast)
  with open(big_dir / "big.bin", "wb") as f:
    f.truncate(51 * 1024 * 1024)
  d_big, err_big = setup(
      str(big_dir),
      "attended",
      ["Options"],
      driver="gemini",
      companion_model="opus",
  )
  assert (
      err_big is not None and "repo path exceeds freeze budget" in err_big
  ), err_big
  assert not (d_big / "pinned").exists()

  # scoped freeze test: big_dir has a small subdirectory scoped_pkg
  scoped_pkg = big_dir / "scoped_pkg"
  scoped_pkg.mkdir()
  (scoped_pkg / "pkg.py").write_text("print('scoped')\n")
  d_scoped, err_scoped = setup(
      str(big_dir),
      "attended",
      ["Options"],
      driver="gemini",
      companion_model="opus",
      scope="scoped_pkg",
  )
  assert err_scoped is None, err_scoped
  st_scoped = json.loads((d_scoped / "state.json").read_text())
  assert st_scoped["scope"] == "scoped_pkg"
  assert (Path(st_scoped["repo"]) / "scoped_pkg" / "pkg.py").exists()
  # verify big.bin was not copied
  assert not (Path(st_scoped["repo"]) / "big.bin").exists()
  assert teardown(d_scoped) is None

  # a pin left behind by a dead session is reported at the next setup; a torn-down one is not.
  # Built by hand rather than by a second setup(), whose preflight re-runs every other selftest.
  assert stale_pins(d.parent, d) == [], "a torn-down debate is not a stale pin"
  dead = d.parent / "d-2026-01-01-dead"
  (dead / "pinned").mkdir(parents=True)
  (dead / "state.json").write_text(
      json.dumps({
          "exits": [["converge", 1, "CONTINUE"]],
          "started": "2026-01-01T00:00:00Z",
      })
  )
  (dead / "transcript.md").write_text("## R1 converge opus role=reviser\n")
  os.utime(dead / "transcript.md", (0, time.time() - 3 * 3600))
  lines = stale_pins(d.parent, d)
  assert (
      len(lines) == 1
      and str(dead) in lines[0]
      and "last converge R1 CONTINUE" in lines[0]
  ), lines
  # the age of the last turn is on the line: it is the only thing that separates a dead run from
  # one still being driven by another session, and report.md NO does not mean the debate is over
  assert "180m since a turn" in lines[0] and "report.md NO" in lines[0], lines
  assert (
      stale_pins(d.parent, dead) == []
  ), "only a dir that still HAS pinned/ is stale; d was torn down"
  # a missing companion is a preflight failure, not a first-round surprise, and it leaves no worktree
  os.environ["DEBATE_COMPANION_OPUS"] = str(tmp / "missing.py")
  d2, err2 = setup(
      str(repo),
      "attended",
      ["Options"],
      driver="gemini",
      companion_model="opus",
  )
  assert err2 is not None and "companion" in err2, err2
  assert not (d2 / "pinned").exists(), "preflight failure must unpin"
  assert (
      subprocess.run(
          ["git", "-C", str(repo), "worktree", "list"],
          capture_output=True,
          text=True,
      ).stdout.count("\n")
      == 1
  )
  # CLI parsing safe bounds-checking tests
  r, m, dr, c, h, sc, s_id = _parse_cli(["myrepo", "--mode"])
  assert r == "myrepo" and m == "attended"
  r, m, dr, c, h, sc, s_id = _parse_cli([
      "myrepo",
      "--driver",
      "--companion",
      "--scope",
      "--session-id",
      "--heading",
  ])
  assert (
      r == "myrepo"
      and dr == "gemini"
      and c is None
      and sc is None
      and s_id is None
      and h == []
  )
  r, m, dr, c, h, sc, s_id = _parse_cli([
      "myrepo",
      "--mode",
      "unattended",
      "--session-id",
      "sess-999",
      "--scope",
      "subpkg",
      "--heading",
      "H1",
      "--heading",
      "H2",
  ])
  assert (
      r == "myrepo"
      and m == "unattended"
      and s_id == "sess-999"
      and sc == "subpkg"
  )
  assert h == ["H1", "H2"]

  # Explicit --session-id persistence test in setup()
  os.environ["DEBATE_COMPANION_OPUS"] = str(stub)
  d_sess, err_sess = setup(
      str(repo),
      "attended",
      ["Options"],
      driver="gemini",
      companion_model="opus",
      session_id="explicit-session-id-123",
  )
  assert err_sess is None, err_sess
  st_sess = json.loads((d_sess / "state.json").read_text())
  assert st_sess["session_id"] == "explicit-session-id-123"
  assert teardown(d_sess) is None

  print("debate_setup selftest: ok")


def _parse_cli(args):
  """Parse CLI arguments with safe bounds checking to avoid IndexError on trailing flags."""
  flags = {
      "--mode": "attended",
      "--driver": "gemini",
      "--companion": None,
      "--scope": None,
      "--session-id": None,
  }
  consumed = set()
  for i, arg in enumerate(args):
    if arg in flags:
      consumed.add(i)
      if i + 1 < len(args) and not args[i + 1].startswith("--"):
        flags[arg] = args[i + 1]
        consumed.add(i + 1)

  headings = []
  for i, arg in enumerate(args):
    if arg == "--heading":
      consumed.add(i)
      if i + 1 < len(args) and not args[i + 1].startswith("--"):
        headings.append(args[i + 1])
        consumed.add(i + 1)

  repo = None
  for i, arg in enumerate(args):
    if i not in consumed and not arg.startswith("--"):
      repo = arg
      break

  return (
      repo,
      flags["--mode"],
      flags["--driver"],
      flags["--companion"],
      headings,
      flags["--scope"],
      flags["--session-id"],
  )


if __name__ == "__main__":
  a = sys.argv[1:]
  if not a or a[0] in ("-h", "--help"):
    print(
        "Usage: debate_setup.py <repo> [--mode attended|unattended]"
        " [--driver D] [--companion C] [--heading H ...] [--scope S]"
        " [--session-id ID]\n"
        "       debate_setup.py --teardown <dir>\n"
        "       debate_setup.py --selftest"
    )
    sys.exit(0)
  if a == ["--selftest"]:
    selftest()
    sys.exit(0)
  if a and a[0] == "--teardown":
    if len(a) < 2:
      print("TEARDOWN-FAILED missing dir", file=sys.stderr)
      sys.exit(1)
    err = teardown(a[1])
    if err:
      print("TEARDOWN-FAILED %s" % err)
      sys.exit(1)
    sys.exit(0)

  repo, mode, driver, companion_model, headings, scope, session_id = _parse_cli(
      a
  )
  if not repo:
    print(
        "Usage: debate_setup.py <repo> [--mode attended|unattended]"
        " [--driver D] [--companion C] [--heading H ...] [--scope S]"
        " [--session-id ID]",
        file=sys.stderr,
    )
    sys.exit(1)

  d, err = setup(
      repo,
      mode,
      headings or ["Directions"],
      driver=driver,
      companion_model=companion_model,
      scope=scope,
      session_id=session_id,
  )
  if err:
    print("PREFLIGHT-FAILED %s" % err)
    sys.exit(1)
  for line in stale_pins(d.parent, d):
    print(line)
  print("D=%s" % d)
