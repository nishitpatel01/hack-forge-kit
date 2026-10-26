#!/usr/bin/env bash
# Installs GEMINI.md, first-principles, and debate skills into ~/.gemini/
set -euo pipefail

KIT_DIR="$(cd "$(dirname "$0")" && pwd)"
GEMINI_HOME="${GEMINI_HOME:-$HOME/.gemini}"
SKILLS_DIR="$GEMINI_HOME/config/skills"

mkdir -p "$SKILLS_DIR"

if [ -f "$GEMINI_HOME/GEMINI.md" ]; then
  cp "$GEMINI_HOME/GEMINI.md" "$GEMINI_HOME/GEMINI.md.bak.$(date +%Y%m%d%H%M%S)"
fi
cp "$KIT_DIR/GEMINI.md" "$GEMINI_HOME/GEMINI.md"

rm -rf "$SKILLS_DIR/first-principles" "$SKILLS_DIR/debate"
cp -R "$KIT_DIR/skills/first-principles" "$SKILLS_DIR/first-principles"
cp -R "$KIT_DIR/skills/debate" "$SKILLS_DIR/debate"

# Rewrite the scripts path in debate/SKILL.md to the user's actual home directory
sed -i.bak "s|~/.gemini/config/skills/debate/scripts/|$SKILLS_DIR/debate/scripts/|g" "$SKILLS_DIR/debate/SKILL.md"
rm -f "$SKILLS_DIR/debate/SKILL.md.bak"

chmod +x "$SKILLS_DIR/debate/scripts/"*.py "$SKILLS_DIR/debate/scripts/"*.sh "$SKILLS_DIR/debate/scripts/companions/"*.py

echo "Installed GEMINI.md, first-principles, and debate into $GEMINI_HOME"
