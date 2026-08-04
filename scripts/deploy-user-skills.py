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

v3.4.15 增强：
- 目标目录跟随 `CODEX_HOME` 环境变量（默认 ~/.codex），不再硬编码。
- `--check` 除「源有目标无/内容不一致」外，还检测 **orphan**：目标 skill 目录内
  源已删除的残留文件（删除/改名资产后不再静默残留）。
- 部署时自动清理仓库拥有 skill 目录内的 orphan；只覆盖仓库拥有的 skill 名，
  用户自建的其它 skill 目录不扫不删（职责边界不变）。

本脚本不进 check.sh（CI 环境没有用户目录），属发版后的部署闭环步骤。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODEX_SRC = ROOT / "codex-skills"


def default_dest() -> Path:
    """用户级 Codex skills 目录。v3.4.15：跟随 `CODEX_HOME` 环境变量
    （Codex 官方约定，默认 ~/.codex），不再硬编码 ~/.codex。"""
    home = os.environ.get("CODEX_HOME") or str(Path.home() / ".codex")
    return Path(home).expanduser() / "skills"


def plan(src_dir: Path, dest_dir: Path) -> list:
    """返回 [(name, dest_path, content_bytes), ...]，按 name 排序（确定性）。

    覆盖 skill 目录下的**全部文件**（SKILL.md + 附属资产如 agents/openai.yaml），
    而非仅 SKILL.md——v3.4.14 修复：此前只复制 SKILL.md，导致附属资产
    （如 investment-memo-craft/agents/openai.yaml）静默缺失却 --check 仍报绿。
    文件相对路径在目标目录中保持；隐藏文件（.DS_Store）与 __pycache__ 跳过。
    内容按字节比较，文本与二进制资产一视同仁。
    """
    items = []
    for skill_dir in sorted(p for p in src_dir.glob("*") if p.is_dir()):
        name = skill_dir.name
        for src in sorted(skill_dir.rglob("*")):
            if not src.is_file():
                continue
            if src.name == ".DS_Store" or src.parent.name == "__pycache__":
                continue
            rel = src.relative_to(skill_dir)
            items.append((name, dest_dir / name / rel, src.read_bytes()))
    return items


def drifted(items: list, src_dir: Path | None = None,
            dest_dir: Path | None = None) -> list:
    """返回漂移的 skill 名列表（缺失/内容不一致）。每个 skill 至多报一次。

    v3.4.15：传入 src_dir+dest_dir 时额外检测 **orphan**——目标 skill 目录内存在
    但源已删除的残留文件（此前只查"源有的目标没有"，不查"目标有的源没有"，
    删除资产/改名后目标会静默残留旧文件且 --check 仍报绿）。
    不传 src_dir/dest_dir 时保持旧语义（仅缺失/不一致），兼容纯 items 调用。
    """
    out = []
    by_name: dict = {}
    for name, dest, content in items:
        by_name.setdefault(name, []).append((dest, content))
    for name in sorted(by_name):
        base_problem = any(
            not dest.exists() or dest.read_bytes() != content
            for dest, content in by_name[name])
        orphan_problem = False
        if src_dir is not None and dest_dir is not None:
            src_skill, dest_skill = src_dir / name, dest_dir / name
            if dest_skill.is_dir():
                src_rel = {
                    p.relative_to(src_skill)
                    for p in src_skill.rglob("*")
                    if p.is_file() and p.name != ".DS_Store"
                    and p.parent.name != "__pycache__"
                }
                orphan_problem = any(
                    p.is_file() and p.name != ".DS_Store"
                    and p.parent.name != "__pycache__"
                    and p.relative_to(dest_skill) not in src_rel
                    for p in dest_skill.rglob("*"))
        if base_problem and orphan_problem:
            out.append(f"{name}（且含 orphan 残留）")
        elif base_problem:
            out.append(name)
        elif orphan_problem:
            out.append(f"{name}（orphan 残留）")
    return out


def _clean_orphans(src_dir: Path, dest_dir: Path, items: list) -> list:
    """删除仓库拥有 skill 目录内、源已不存在的残留文件（orphan）。

    只扫仓库拥有的 skill 名；用户自建 skill 目录不扫不删（职责边界不变）。
    返回被删除的相对路径列表。
    """
    owned = {name for name, _, _ in items}
    removed = []
    for name in sorted(owned):
        src_skill, dest_skill = src_dir / name, dest_dir / name
        if not dest_skill.is_dir():
            continue
        src_rel = {
            p.relative_to(src_skill)
            for p in src_skill.rglob("*")
            if p.is_file() and p.name != ".DS_Store"
            and p.parent.name != "__pycache__"
        }
        for p in sorted(dest_skill.rglob("*")):
            if not p.is_file() or p.name == ".DS_Store" \
                    or p.parent.name == "__pycache__":
                continue
            if p.relative_to(dest_skill) not in src_rel:
                p.unlink()
                removed.append(str(p.relative_to(dest_dir)))
    return removed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只比对不写文件，漂移则 exit 1")
    ap.add_argument("--dest", default=None, help="目标目录（默认 ~/.codex/skills）")
    args = ap.parse_args()

    if not CODEX_SRC.is_dir():
        print(f"❌ 找不到源目录 {CODEX_SRC}（先跑 scripts/sync-codex-skills.py）",
              file=sys.stderr)
        return 2

    dest_dir = Path(args.dest).expanduser() if args.dest else default_dest()
    items = plan(CODEX_SRC, dest_dir)
    if not items:
        print(f"❌ 源目录为空：{CODEX_SRC}", file=sys.stderr)
        return 2

    if args.check:
        stale = drifted(items, CODEX_SRC, dest_dir)
        if stale:
            print(f"❌ 用户级 Codex 副本漂移（{len(stale)} 个 skill）：", file=sys.stderr)
            for entry in stale:
                print(f"   - {entry}", file=sys.stderr)
            print("   运行 python3 scripts/deploy-user-skills.py 重新部署",
                  file=sys.stderr)
            return 1
        print(f"✅ 一致：{len(items)} 个 Codex 副本与仓库源同步（{dest_dir}）")
        return 0

    removed = _clean_orphans(CODEX_SRC, dest_dir, items)
    for _, dest, content in items:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
    if removed:
        shown = ", ".join(removed[:5]) + ("…" if len(removed) > 5 else "")
        print(f"🧹 清理 orphan {len(removed)} 个（源已删除的残留）：{shown}")
    print(f"✅ 已部署 {len(items)} 个 Codex skill 到 {dest_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
