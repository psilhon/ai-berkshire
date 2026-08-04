#!/usr/bin/env python3
"""把仓库生成的 Codex skill 部署到用户级目录，并提供机器可检的漂移检测。

背景（v3.4.13）：仓库内三副本（skills/ = workbuddy-skills/ = codex-skills/）由
`scripts/sync-codex-skills.py` 保证一致，且被 check.sh 守住。但**用户级部署副本**
（`~/.codex/skills/`）此前完全没有同步机制——发版后靠手工拷贝，于是静默落后仓库源
多个版本，review 每轮都能翻出"用户级副本仍有文案差异"。没有机器检查的一致性
不是一致性，是运气。

职责边界：
- 本脚本只管 **Codex** 用户级目录（`~/.codex/skills/`）。
- WorkBuddy 用户级目录（`~/.workbuddy/skills/`）由用户侧的
  `~/.workbuddy/berkshire-skill-sync/sync.py` 负责（它要做移植说明注入等适配渲染，
  是那一侧的权威真源）；这里绝不双写，避免制造第二真源。

用法：
    python3 scripts/deploy-user-skills.py            # 部署/更新
    python3 scripts/deploy-user-skills.py --check    # 只比对，漂移 exit 1
    python3 scripts/deploy-user-skills.py --dest DIR # 指定目标目录（测试用）

注意：只覆盖仓库拥有的 skill 名，用户自建的其它 skill 一律不动、不删。
本脚本不进 check.sh（CI 环境没有用户目录），属发版后的部署闭环步骤。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODEX_SRC = ROOT / "codex-skills"
DEFAULT_DEST = Path.home() / ".codex" / "skills"


def plan(src_dir: Path, dest_dir: Path) -> list:
    """返回 [(name, dest_path, content), ...]，按 name 排序（确定性）。"""
    items = []
    for src in sorted(src_dir.glob("*/SKILL.md")):
        name = src.parent.name
        items.append((name, dest_dir / name / "SKILL.md",
                      src.read_text(encoding="utf-8")))
    return items


def drifted(items: list) -> list:
    """返回漂移的 skill 名列表（缺失或内容不一致）。"""
    out = []
    for name, dest, content in items:
        if not dest.exists() or dest.read_text(encoding="utf-8") != content:
            out.append(name)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只比对不写文件，漂移则 exit 1")
    ap.add_argument("--dest", default=None, help="目标目录（默认 ~/.codex/skills）")
    args = ap.parse_args()

    if not CODEX_SRC.is_dir():
        print(f"❌ 找不到源目录 {CODEX_SRC}（先跑 scripts/sync-codex-skills.py）",
              file=sys.stderr)
        return 2

    dest_dir = Path(args.dest).expanduser() if args.dest else DEFAULT_DEST
    items = plan(CODEX_SRC, dest_dir)
    if not items:
        print(f"❌ 源目录为空：{CODEX_SRC}", file=sys.stderr)
        return 2

    if args.check:
        stale = drifted(items)
        if stale:
            print(f"❌ 用户级 Codex 副本漂移（{len(stale)}/{len(items)}）：{stale}\n"
                  f"   运行 python3 scripts/deploy-user-skills.py 重新部署",
                  file=sys.stderr)
            return 1
        print(f"✅ 一致：{len(items)} 个 Codex 副本与仓库源同步（{dest_dir}）")
        return 0

    for _, dest, content in items:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
    print(f"✅ 已部署 {len(items)} 个 Codex skill 到 {dest_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
