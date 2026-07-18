#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${CLAUDE_COMMANDS_DIR:-$HOME/.claude/commands}"

# --only <skill-name>: 只安装该 skill 的 .md；缺省保持全量安装
ONLY=""
while [ $# -gt 0 ]; do
  case "$1" in
    --only)
      [ $# -ge 2 ] || { echo "Error: --only requires a skill name" >&2; exit 2; }
      ONLY="$2"
      shift 2
      ;;
    *)
      echo "Error: unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

if [ -n "$ONLY" ] && [ ! -f "$ROOT/skills/$ONLY.md" ]; then
  echo "Error: unknown skill '$ONLY' (no skills/$ONLY.md file)" >&2
  exit 1
fi

mkdir -p "$DEST"
if [ -n "$ONLY" ]; then
  cp "$ROOT/skills/$ONLY.md" "$DEST/"
else
  cp "$ROOT"/skills/*.md "$DEST"/
fi
chmod +x "$ROOT"/tools/*.py "$ROOT"/tools/*.sh 2>/dev/null || true

if [ -n "$ONLY" ]; then
  echo "Installed Claude Code command '$ONLY' to $DEST"
else
  echo "Installed Claude Code commands to $DEST"
fi
