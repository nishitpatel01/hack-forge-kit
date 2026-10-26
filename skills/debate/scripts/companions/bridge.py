#!/usr/bin/env python3
"""bridge.py - Companion bridge for Gemini and Claude Opus models.

Accepts: --model <opus|gemini> --prompt-file <path>
Dispatches a cross-family companion call via configured CLI/SDK and returns
the NONCE-prefixed response.
"""

import argparse
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys


def _call_anthropic(prompt_text: str) -> str:
  import anthropic

  client = anthropic.Anthropic()
  model = os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-6")
  msg = client.messages.create(
      model=model,
      max_tokens=4096,
      messages=[{"role": "user", "content": prompt_text}],
  )
  parts = [getattr(b, "text", "") for b in msg.content]
  return "\n".join(p for p in parts if p).strip()


def _call_gemini(prompt_text: str) -> str:
  from google import genai

  client = genai.Client()
  model = os.environ.get("GEMINI_MODEL", "gemini-2.5-pro")
  resp = client.models.generate_content(model=model, contents=prompt_text)
  return (resp.text or "").strip()


def main() -> int:
  parser = argparse.ArgumentParser(description="Debate companion bridge")
  parser.add_argument(
      "--model", required=True, help="Model name: opus or gemini"
  )
  parser.add_argument(
      "--prompt-file", required=True, help="Path to prompt markdown file"
  )
  args = parser.parse_args()

  prompt_text = Path(args.prompt_file).read_text(errors="replace")
  m = re.search(r"DEBATE-NONCE (\w+)", prompt_text)
  nonce = m.group(1) if m else None

  if os.environ.get("DEBATE_SELFTEST") == "1":
    if nonce:
      print("NONCE %s" % nonce)
    print(
        "[companion:%s] Companion response generated for %s"
        % (args.model, Path(args.prompt_file).name)
    )
    return 0

  is_opus = "opus" in args.model.lower()
  cmd_override = os.environ.get(
      "DEBATE_OPUS_CMD" if is_opus else "DEBATE_GEMINI_CMD"
  )
  try:
    if cmd_override:
      proc = subprocess.run(
          shlex.split(cmd_override) + [args.prompt_file],
          capture_output=True,
          text=True,
          timeout=180,
          check=True,
      )
      reply = proc.stdout.strip()
    else:
      reply = (
          _call_anthropic(prompt_text)
          if is_opus
          else _call_gemini(prompt_text)
      )
    if reply:
      if nonce and f"NONCE {nonce}" not in reply:
        reply = f"NONCE {nonce}\n{reply}"
      print(reply)
      return 0
  except Exception as e:
    print(f"bridge error: {e}", file=sys.stderr)
    return 1

  return 1


if __name__ == "__main__":
  sys.exit(main())
