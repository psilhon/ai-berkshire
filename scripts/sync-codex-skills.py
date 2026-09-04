#!/usr/bin/env python3
"""Generate Codex skills from AI Berkshire Claude command files."""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLAUDE_SKILLS = ROOT / "skills"
CODEX_SKILLS = ROOT / "codex-skills"
WORKBUDDY_SOURCE = CLAUDE_SKILLS / "full-company-analysis-workbuddy.md"
WORKBUDDY_TARGET = ROOT / "workbuddy-skills/full-company-analysis-workbuddy/SKILL.md"

# 每个生成 SKILL.md 都带此标记（见 codex_body 的 adapter note）。
# 孤儿判定以标记为准而非目录名：Codex-only 手写包（如 investment-memo-craft）
# 无此标记，永远不会被误删。
GENERATED_MARKER = "This skill is generated from `skills/"


def find_generated_orphans(codex_dir: Path, source_names: set[str]) -> list[Path]:
    """目标侧遍历：找出「带生成标记但源已删除」的整目录孤儿。

    只标记本脚本生成过的目录；手写 Codex-only 包（无标记）与用户自建
    目录一律不受影响。
    """
    orphans: list[Path] = []
    if not codex_dir.is_dir():
        return orphans
    for child in sorted(codex_dir.iterdir()):
        skill = child / "SKILL.md"
        if not child.is_dir() or not skill.is_file():
            continue
        if child.name in source_names:
            continue
        if GENERATED_MARKER in skill.read_text(encoding="utf-8"):
            orphans.append(child)
    return orphans


def split_frontmatter(text: str) -> tuple[str | None, str]:
    if not text.startswith("---\n"):
        return None, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return None, text
    return text[4:end], text[end + 5 :].lstrip("\n")


def first_heading(text: str, fallback: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def yaml_quote(value: str) -> str:
    value = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{value}"'


def metadata_for(name: str, source_name: str, source_text: str) -> str:
    existing, body = split_frontmatter(source_text)
    if existing:
        has_name = re.search(r"(?m)^name:\s*", existing) is not None
        has_description = re.search(r"(?m)^description:\s*", existing) is not None
        lines = []
        if not has_name:
            lines.append(f"name: {name}")
        if not has_description:
            title = first_heading(body, name)
            lines.append(
                "description: "
                + yaml_quote(f"AI Berkshire skill: {title}. Source: skills/{source_name}.")
            )
        lines.append(existing.rstrip())
        return "---\n" + "\n".join(lines) + "\n---\n\n"

    title = first_heading(source_text, name)
    description = f"AI Berkshire skill: {title}. Source: skills/{source_name}."
    return (
        "---\n"
        f"name: {name}\n"
        f"description: {yaml_quote(description)}\n"
        "---\n\n"
    )


def codex_body(name: str, source_name: str, source_text: str) -> str:
    _, body = split_frontmatter(source_text)
    note = (
        "## Codex adapter note\n\n"
        f"This skill is generated from `skills/{source_name}` so Claude Code "
        "and Codex users share one canonical workflow.\n\n"
        "- Treat `$ARGUMENTS` as the user's request in the current Codex thread.\n"
        "- When the source mentions Claude-only surfaces such as Task, Agent, "
        "WebSearch, Bash, Read, or Write, use the closest Codex capability "
        "available in this session: subagents when available, web search when "
        "needed, shell commands for local tools, and normal file edits for "
        "workspace files.\n"
        "- Use shared project tools from `tools/` in this repository. Prefer "
        "running commands from the repository root with paths like "
        "`python3 tools/financial_rigor.py ...`; if the current thread starts "
        "outside the repo, locate the actual checkout path first instead of "
        "assuming a fixed home-directory path.\n"
        "- Before starting research, run the `date` command to confirm "
        "today's date; treat it as the baseline for \"latest\" data and state "
        "the data cutoff date in the report header. Never assume the current "
        "date from training data.\n"
        "- Preserve the research quality rules from `AGENTS.md`: cross-check "
        "financial data, use exact arithmetic tools for valuation/math, and "
        "clearly label uncertainty and source gaps.\n\n"
    )
    return note + body.rstrip() + "\n"


def main() -> None:
    check = "--check" in sys.argv[1:]
    unknown_args = [arg for arg in sys.argv[1:] if arg != "--check"]
    if unknown_args:
        joined = ", ".join(unknown_args)
        raise SystemExit(f"Unknown argument(s): {joined}")

    if not check:
        CODEX_SKILLS.mkdir(exist_ok=True)

    count = 0
    stale: list[str] = []
    source_names: set[str] = set()
    for source in sorted(CLAUDE_SKILLS.glob("*.md")):
        name = source.stem
        source_names.add(name)
        source_text = source.read_text(encoding="utf-8")
        target_dir = CODEX_SKILLS / name
        target = target_dir / "SKILL.md"
        content = metadata_for(name, source.name, source_text) + codex_body(
            name, source.name, source_text
        )
        if check:
            if not target.exists() or target.read_text(encoding="utf-8") != content:
                stale.append(str(target.relative_to(ROOT)))
        else:
            target_dir.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        count += 1

    workbuddy_content = WORKBUDDY_SOURCE.read_text(encoding="utf-8")
    if check:
        if (not WORKBUDDY_TARGET.exists()
                or WORKBUDDY_TARGET.read_text(encoding="utf-8") != workbuddy_content):
            stale.append(str(WORKBUDDY_TARGET.relative_to(ROOT)))
    else:
        WORKBUDDY_TARGET.parent.mkdir(parents=True, exist_ok=True)
        WORKBUDDY_TARGET.write_text(workbuddy_content, encoding="utf-8")

    if check:
        orphans = find_generated_orphans(CODEX_SKILLS, source_names)
        if stale:
            print("Codex skills are out of date:")
            for path in stale:
                print(f"  {path}")
        if orphans:
            print("Orphaned generated Codex skill dirs (source deleted; rerun without --check to remove):")
            for path in orphans:
                print(f"  {path.relative_to(ROOT)}")
        if stale or orphans:
            raise SystemExit(1)
        print(
            f"Checked {count} Codex skills and WorkBuddy adapter "
            f"in {CODEX_SKILLS.relative_to(ROOT)}"
        )
        return

    orphans = find_generated_orphans(CODEX_SKILLS, source_names)
    for path in orphans:
        shutil.rmtree(path)
    print(
        f"Generated {count} Codex skills and WorkBuddy adapter "
        f"in {CODEX_SKILLS.relative_to(ROOT)}"
    )
    if orphans:
        print(f"Removed {len(orphans)} orphaned generated skill dir(s):")
        for path in orphans:
            print(f"  {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
