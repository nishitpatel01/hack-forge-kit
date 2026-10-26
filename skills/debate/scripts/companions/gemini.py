#!/usr/bin/env python3
"""gemini.py - Gemini companion runner for AI Coding Agents debate skill."""

from pathlib import Path
import subprocess
import sys

if __name__ == "__main__":
  bridge = Path(__file__).resolve().parent / "bridge.py"
  sys.exit(
      subprocess.run(
          [sys.executable, str(bridge), "--model", "gemini"] + sys.argv[1:]
      ).returncode
  )
