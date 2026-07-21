#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${CODEX_HOME:-$HOME/.codex}/skills"

# --only <skill-name>: 只安装该 skill；缺省保持全量安装
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

python3 "$ROOT/scripts/sync-codex-skills.py"

if [ -n "$ONLY" ] && [ ! -d "$ROOT/codex-skills/$ONLY" ]; then
  echo "Error: unknown skill '$ONLY' (no codex-skills/$ONLY directory)" >&2
  exit 1
fi

mkdir -p "$DEST"

# 覆盖前把已存在的同名 skill 移入备份目录（滚动保留上一代），
# 防止用户本地修改过的版本被无提示删除
BACKUP="$DEST-backup"
backed_up=0

for skill_dir in "$ROOT"/codex-skills/*; do
  [ -d "$skill_dir" ] || continue
  name="$(basename "$skill_dir")"
  if [ -n "$ONLY" ] && [ "$name" != "$ONLY" ]; then
    continue
  fi
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
if [ -n "$ONLY" ]; then
  echo "Installed Codex skill '$ONLY' to $DEST"
else
  echo "Installed Codex skills to $DEST"
fi
echo "Restart Codex to pick up new skills."
