#!/usr/bin/env python3
"""将 full_analysis_contract.json 重写为 lean 模式：
- 删除 172 个固定 ## 标题（sections[]）→ 改为 report_guidance（自然结构提示）
- 删除 38 条 evidence_rules（含双源强制/角色运行/命令回执）→ 改为 substance 底线
- 删除 conditional_command_operations / pwl_* / 冗余 predicates
保留：depends_on（波次顺序）、artifact.formal_path+min_bytes、roles（扇出信息）、
      applicability、core、skill_type、min_substantive_sections
"""
import json, sys

SRC = "tools/full_analysis_contract.json"
OUT = "tools/full_analysis_contract.json"  # 原地重写

GUIDANCE = {
    "data": "遵循 {spec} 方法论的自然章节结构撰写：数据截止日、直接来源、核心结论、关键数据表、限制与缺口、仅供学习研究声明。不强制固定标题。",
    "research": "遵循 {spec} 方法论的自然章节结构撰写：核心结论、估值区间、反面检验、AI 置信度、投资确定性与证伪条件、仅供学习研究声明。不强制固定标题。",
    "team": "四大师（巴菲特/芒格/段永平/李录）独立备忘录整合为一份报告：各方视角、共识与分歧、整合结论、证伪条件、仅供学习研究声明。不强制固定标题。",
    "default": "遵循 {spec} 方法论的自然章节结构撰写，覆盖核心结论、关键分析、限制与缺口、仅供学习研究声明。不强制固定标题。",
}

def guidance_for(skill):
    st = skill.get("skill_type", "")
    tmpl = GUIDANCE.get(st, GUIDANCE["default"])
    return tmpl.format(spec=skill.get("spec_source", f"skills/{skill['skill_id']}.md"))

def lean_skill(s):
    return {
        "skill_id": s["skill_id"],
        "depends_on": s.get("depends_on", []),
        "category": s.get("category"),
        "stage_dir": s.get("stage_dir"),
        "spec_source": s.get("spec_source"),
        "core": s.get("core", True),
        "skill_type": s.get("skill_type", "research"),
        "applicability": s.get("applicability", {"predicate": "always", "alternative": None}),
        "artifact": {
            "formal_path": s["artifact"]["formal_path"],
            "min_bytes": s["artifact"].get("min_bytes", 3000),
        },
        "roles": {
            "mode": (s.get("roles") or {}).get("mode", "single_agent"),
            "required_roles": (s.get("roles") or {}).get("required_roles", []),
        },
        "min_substantive_sections": s.get("min_substantive_sections", 1),
        "report_guidance": guidance_for(s),
        "substance": {
            "require_as_of": True,
            "require_sources": True,
            "require_disclaimer": True,
        },
    }

def main():
    c = json.load(open(SRC, encoding="utf-8"))
    lean = {
        "schema_version": "full-analysis-contract/lean-v1",
        "manifest_schema_version": "full-analysis-manifest/lean-v1",
        "description": "全量公司分析 lean 契约：报告为唯一交付物，允许单元失败，失败即声明。仅保留波次顺序、报告路径、实质性下限与扇出信息；移除固定标题、证据账本规则、双源强制、租约机。",
        "authorization_profile": c.get("authorization_profile"),
        "stage_dirs": c.get("stage_dirs"),
        "skills": [lean_skill(s) for s in c.get("skills", [])],
    }
    json.dump(lean, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    # 校验
    back = json.load(open(OUT, encoding="utf-8"))
    assert len(back["skills"]) == len(c["skills"]), "skill 数不一致"
    total_sections = sum(len(s.get("sections", [])) for s in c["skills"])
    total_rules = sum(len(s.get("evidence_rules", [])) for s in c["skills"])
    print(f"OK: {len(back['skills'])} skills | 移除固定标题 {total_sections} 个 | 移除 evidence_rules {total_rules} 条")

if __name__ == "__main__":
    main()
