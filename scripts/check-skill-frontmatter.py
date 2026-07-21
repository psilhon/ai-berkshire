#!/usr/bin/env python3
"""Validate governance frontmatter in skills/*.md (CI gate).

Each canonical skill must declare a stable set of frontmatter fields so that
the skill corpus is machine-readable and consistent across Claude Code and
Codex. Run by `scripts/check.sh` after any change to `skills/`.

Required fields:
    name              kebab-case skill id (matches the filename)
    description       trigger scenario + input + output (drives auto-matching)
    owner             who maintains the skill
    category          one of the allowed research categories
    maturity          stable | beta | governed(Phase2-gated)
    review-cadence    per-release | on-change | quarterly | annual
"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLAUDE_SKILLS = ROOT / "skills"

REQUIRED_FIELDS = ("name", "description", "owner", "category", "maturity", "review-cadence")

ALLOWED_CATEGORIES = {
    "深度公司研究",
    "财报分析",
    "行业与筛选",
    "持仓与论文管理",
    "数据与思维工具",
    "编排层",
}
ALLOWED_MATURITY = {"stable", "beta", "governed(Phase2-gated)"}
ALLOWED_REVIEW_CADENCE = {"per-release", "on-change", "quarterly", "annual"}


def split_frontmatter(text: str) -> tuple[str | None, str]:
    if not text.startswith("---\n"):
        return None, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return None, text
    return text[4:end], text[end + 5:]


def parse_fields(frontmatter: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in frontmatter.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if key:
            fields[key] = value
    return fields


def main() -> int:
    errors: list[str] = []
    count = 0

    for source in sorted(CLAUDE_SKILLS.glob("*.md")):
        count += 1
        name = source.stem
        text = source.read_text(encoding="utf-8")
        frontmatter, _ = split_frontmatter(text)
        if frontmatter is None:
            errors.append(f"{source.name}: 缺少 YAML frontmatter（应以 --- 开头）")
            continue

        fields = parse_fields(frontmatter)

        for field in REQUIRED_FIELDS:
            value = fields.get(field, "").strip()
            if not value:
                errors.append(f"{source.name}: 缺少必填字段 `{field}`")
                continue
            if field == "name" and value != name:
                # name should match the filename for a predictable slash command id
                errors.append(
                    f"{source.name}: frontmatter name `{value}` 与文件名 `{name}` 不一致"
                )
            if field == "category" and value not in ALLOWED_CATEGORIES:
                errors.append(
                    f"{source.name}: category `{value}` 非法，应为 {sorted(ALLOWED_CATEGORIES)}"
                )
            if field == "maturity" and value not in ALLOWED_MATURITY:
                errors.append(
                    f"{source.name}: maturity `{value}` 非法，应为 {sorted(ALLOWED_MATURITY)}"
                )
            if field == "review-cadence" and value not in ALLOWED_REVIEW_CADENCE:
                errors.append(
                    f"{source.name}: review-cadence `{value}` 非法，"
                    f"应为 {sorted(ALLOWED_REVIEW_CADENCE)}"
                )

    if errors:
        print("❌ Skill frontmatter 校验失败：")
        for err in errors:
            print(f"  - {err}")
        return 1

    print(f"✅ 校验通过：{count} 个 skill frontmatter 全部合规（{len(REQUIRED_FIELDS)} 必填字段）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
