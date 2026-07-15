#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${CODEX_HOME:-$HOME/.codex}/skills"

python3 "$ROOT/scripts/sync-codex-skills.py"
mkdir -p "$DEST"

# 覆盖前把已存在的同名 skill 移入备份目录（滚动保留上一代），
# 防止用户本地修改过的版本被无提示删除
BACKUP="$DEST-backup"
backed_up=0

for skill_dir in "$ROOT"/codex-skills/*; do
  [ -d "$skill_dir" ] || continue
  name="$(basename "$skill_dir")"
  if [ -e "$DEST/$name" ]; then
    mkdir -p "$BACKUP"
    rm -rf "$BACKUP/$name"
    mv "$DEST/$name" "$BACKUP/$name"
    backed_up=1
  fi
  cp -R "$skill_dir" "$DEST/$name"
done

chmod +x "$ROOT"/tools/*.py "$ROOT"/tools/*.sh 2>/dev/null || true

if [ "$backed_up" -eq 1 ]; then
  echo "Previous versions backed up to $BACKUP (one generation kept)."
fi
echo "Installed Codex skills to $DEST"
echo "Run ./scripts/install-codex-prompts.sh if you want slash-command prompts."
echo "Restart Codex to pick up new skills."
