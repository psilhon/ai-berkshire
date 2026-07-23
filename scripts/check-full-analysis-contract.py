#!/usr/bin/env python3
"""独立校验全量公司分析 Contract v2。

此脚本故意不 import Gate/Runtime，避免注册表和执行代码同时出错。
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path


EXPECTED_SKILLS = {
    "ashare-data", "financial-data", "quality-screen", "investment-checklist",
    "investment-research", "investment-team", "management-deep-dive",
    "earnings-review", "earnings-team", "industry-research", "industry-funnel",
    "bottleneck-hunter", "news-pulse", "thesis-tracker", "thesis-drift",
    "portfolio-review", "private-company-research", "deep-company-series",
    "dyp-ask", "wechat-article",
}
EXPECTED_SCHEMA = {
    "schema_version": "full-analysis-contract/v2",
    "manifest_schema_version": "full-analysis-manifest/v2",
    "result_schema_version": "result-schema/v1",
}
EXPECTED_STAGE_KEYS = {
    "01-data-screen", "02-company-earnings", "03-industry-opportunity",
    "04-thesis-boundary", "05-content",
}
MACHINE_SECTIONS = {
    "data_cutoff", "sources_scope", "limitations", "research_disclaimer",
    "core_conclusion", "downstream_evidence", "contract_calculations",
}
PWL_ALLOWLIST = {"tushare_unavailable", "web_bandwidth_degraded", "ephemeral_source"}
PWL_FORBIDDEN = {"single_context_fallback", "manual_intervention", "budget_exhausted"}
EVIDENCE_KINDS = {
    "min_facts", "min_dual_source_facts", "min_calculations",
    "min_judgments_with_falsification", "min_role_runs", "min_command_receipts",
    "required_fact_fields", "required_judgment_rule_ids",
    "required_command_operations", "conditional_command_operations",
}
SEQUENTIAL_CAPS = {"PASS", "PASS_WITH_LIMITATIONS", "NOT_APPLICABLE_PASS"}


def _err(errors: list[str], message: str) -> None:
    errors.append(message)


def _ashare_cli_commands(repo_root: Path) -> tuple[set[str] | None, str | None]:
    path = repo_root / "tools" / "ashare_data.py"
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError) as exc:
        return None, f"ashare CLI 不可读或语法非法: {exc}"
    commands = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        if isinstance(node.func, ast.Attribute) and node.func.attr == "add_parser":
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                commands.add(first.value)
    if not commands:
        return None, "ashare CLI 未提取到 add_parser 命令"
    return commands, None


def _validate_evidence(errors: list[str], label: str, rules: object,
                       known_skills: set[str], ashare_commands: set[str] | None) -> None:
    if not isinstance(rules, list) or not rules:
        _err(errors, f"{label} evidence_rules 必须为非空数组")
        return
    registered_ops: set[str] = set()
    for rule in rules:
        if not isinstance(rule, dict) or rule.get("kind") not in EVIDENCE_KINDS:
            _err(errors, f"{label} evidence_rule kind 非法: {rule!r}")
            continue
        kind = rule["kind"]
        if kind.startswith("min_"):
            n = rule.get("n")
            if not isinstance(n, int) or isinstance(n, bool) or n <= 0:
                _err(errors, f"{label} evidence_rule n 必须为正整数: {rule!r}")
        elif kind == "conditional_command_operations":
            if rule.get("capability") != "tushare_configured":
                _err(errors, f"{label} conditional capability 非法")
            values = rule.get("values")
            if not isinstance(values, list) or not values:
                _err(errors, f"{label} conditional values 必须为非空数组")
                continue
            ops = []
            for value in values:
                if not isinstance(value, dict):
                    _err(errors, f"{label} conditional value 必须为对象")
                    continue
                op, feed, layer = value.get("op"), value.get("feeds"), value.get("layer")
                if not isinstance(op, str) or not op:
                    _err(errors, f"{label} conditional op 缺失")
                else:
                    ops.append(op); registered_ops.add(op)
                if not isinstance(feed, str) or feed not in known_skills:
                    _err(errors, f"{label} conditional feeds 非注册 skill: {feed!r}")
                if not isinstance(layer, int) or isinstance(layer, bool) or not 1 <= layer <= 6:
                    _err(errors, f"{label} conditional layer 必须为 1..6")
            if len(ops) != len(set(ops)):
                _err(errors, f"{label} conditional op 必须唯一")
        else:
            values = rule.get("values")
            if not isinstance(values, list) or not values or any(
                    not isinstance(v, str) or not v for v in values):
                _err(errors, f"{label} evidence values 必须为非空字符串数组")
            if kind == "required_command_operations" and isinstance(values, list):
                registered_ops.update(v for v in values if isinstance(v, str))
    if ashare_commands is not None and label.startswith("[ashare-data:"):
        missing = sorted(registered_ops - ashare_commands)
        if missing:
            _err(errors, f"{label} 注册 operation 不存在于 ashare CLI: {missing}")


def validate(registry_path: Path, repo_root: Path) -> list[str]:
    errors: list[str] = []
    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"注册表不可读或非法 JSON: {exc}"]
    if not isinstance(registry, dict):
        return ["注册表顶层必须为对象"]
    for key, expected in EXPECTED_SCHEMA.items():
        if registry.get(key) != expected:
            _err(errors, f"顶层 {key} 必须为 {expected!r}, 实际 {registry.get(key)!r}")
    result_schema_path = repo_root / "tools/full_analysis_result_schema.json"
    if not result_schema_path.is_file():
        _err(errors, f"result schema 不存在: {result_schema_path}")
    else:
        try:
            result_schema = json.loads(result_schema_path.read_text(encoding="utf-8"))
            if result_schema.get("schema_version") != registry.get("result_schema_version"):
                _err(errors, "Contract result_schema_version 与 Result Bundle schema 不一致")
        except (OSError, json.JSONDecodeError) as exc:
            _err(errors, f"result schema 非法: {exc}")
    if "generic_required_sections" in registry:
        _err(errors, "v2 禁止 generic_required_sections")
    stage_dirs = registry.get("stage_dirs")
    if not isinstance(stage_dirs, dict) or set(stage_dirs) != EXPECTED_STAGE_KEYS:
        _err(errors, "stage_dirs 必须包含完整五阶段键")
        stage_dirs = stage_dirs if isinstance(stage_dirs, dict) else {}
    predicates = registry.get("predicates")
    if not isinstance(predicates, list) or not all(isinstance(p, str) for p in predicates):
        _err(errors, "predicates 必须为字符串数组")
        predicates = []
    if set(registry.get("pwl_allowlist", [])) != PWL_ALLOWLIST:
        _err(errors, "pwl_allowlist 必须是封闭三项集合")
    if not PWL_FORBIDDEN.issubset(set(registry.get("pwl_forbidden", []))):
        _err(errors, "pwl_forbidden 缺少禁止降级项")
    skills = registry.get("skills")
    if not isinstance(skills, list):
        return errors + ["顶层 skills 必须为数组"]
    if len(skills) != 20:
        _err(errors, f"skills 必须恰好 20 项, 实际 {len(skills)} 项")
    ids = [s.get("skill_id") for s in skills if isinstance(s, dict)]
    if set(ids) != EXPECTED_SKILLS or len(ids) != len(set(ids)):
        _err(errors, "skill_id 必须与 20 项白名单完全一致且无重复")
    paths: dict[str, str] = {}
    known = set(ids)
    ashare_commands, cli_error = _ashare_cli_commands(repo_root)
    if cli_error:
        _err(errors, cli_error)
    for item in skills:
        if not isinstance(item, dict):
            _err(errors, f"skill 条目必须为对象: {item!r}"); continue
        sid = item.get("skill_id"); label = f"[{sid}:v2]"
        if not isinstance(item.get("core"), bool):
            _err(errors, f"{label} core 必须为 bool")
        if "required_sections" in item:
            _err(errors, f"{label} 禁止 per-skill required_sections")
        stage = item.get("stage_dir")
        if stage not in stage_dirs:
            _err(errors, f"{label} stage_dir 不在 stage_dirs: {stage!r}")
        src = item.get("spec_source")
        if not isinstance(src, str) or not (repo_root / src).is_file():
            _err(errors, f"{label} spec_source 不存在: {src!r}")
        artifact = item.get("artifact")
        if not isinstance(artifact, dict):
            _err(errors, f"{label} artifact 必须为对象"); artifact = {}
        aid, path, minimum = artifact.get("artifact_id"), artifact.get("formal_path"), artifact.get("min_bytes")
        if not isinstance(aid, str) or not aid.startswith("artifact."):
            _err(errors, f"{label} artifact_id 必须以 artifact. 开头")
        if not isinstance(path, str) or not path or path.startswith("/") or ".." in path.split("/"):
            _err(errors, f"{label} formal_path 非法: {path!r}")
        elif stage in stage_dirs and not path.startswith(stage_dirs[stage] + "/"):
            _err(errors, f"{label} formal_path 不在阶段目录下: {path!r}")
        elif path in paths:
            _err(errors, f"{label} formal_path 与 {paths[path]} 冲突")
        else:
            paths[path] = label
        if not isinstance(minimum, int) or isinstance(minimum, bool) or minimum <= 0:
            _err(errors, f"{label} min_bytes 必须为正整数")
        if artifact.get("audit_policy") not in {"required", "advisory", "none"}:
            _err(errors, f"{label} audit_policy 非法")
        sections = item.get("sections")
        if not isinstance(sections, list) or not sections:
            _err(errors, f"{label} sections 必须为非空数组")
        else:
            section_ids = []
            for section in sections:
                if not isinstance(section, dict):
                    _err(errors, f"{label} section 必须为对象"); continue
                section_id = section.get("section_id")
                section_ids.append(section_id)
                if not isinstance(section_id, str) or not section_id.isidentifier() or not section_id.isascii():
                    _err(errors, f"{label} section_id 非法: {section_id!r}")
                if not isinstance(section.get("heading"), str) or not section["heading"]:
                    _err(errors, f"{label} section heading 缺失")
                if not isinstance(section.get("required"), bool):
                    _err(errors, f"{label} section required 必须为 bool")
                if not isinstance(section.get("min_content_chars"), int) or section["min_content_chars"] < 0:
                    _err(errors, f"{label} section min_content_chars 非法")
            if len(section_ids) != len(set(section_ids)):
                _err(errors, f"{label} section_id 必须唯一")
            if not MACHINE_SECTIONS.issubset(set(section_ids)):
                _err(errors, f"{label} 缺少机器必需章节")
        app = item.get("applicability")
        if not isinstance(app, dict) or app.get("predicate") not in predicates:
            _err(errors, f"{label} applicability.predicate 未注册")
        elif app.get("alternative") is not None and not isinstance(app["alternative"], str):
            _err(errors, f"{label} applicability.alternative 必须为 null 或字符串")
        projected = item.get("predicates")
        actual = app.get("predicate") if isinstance(app, dict) else None
        if projected != [actual]:
            _err(errors, f"{label} predicates 必须精确投影 applicability.predicate")
        roles = item.get("roles")
        if not isinstance(roles, dict) or not isinstance(roles.get("required_roles"), list):
            _err(errors, f"{label} roles.required_roles 必须为数组")
        else:
            if len(set(roles["required_roles"])) != len(roles["required_roles"]):
                _err(errors, f"{label} required_roles 不得重复")
            if roles.get("mode") not in {"single_agent", "independent_then_integrator"}:
                _err(errors, f"{label} roles.mode 非法")
            mic = roles.get("min_independent_contexts")
            if not isinstance(mic, int) or isinstance(mic, bool) or mic < 0:
                _err(errors, f"{label} min_independent_contexts 非法")
            if roles.get("sequential_cap") not in SEQUENTIAL_CAPS:
                _err(errors, f"{label} sequential_cap 非法")
        _validate_evidence(errors, label, item.get("evidence_rules"), known, ashare_commands)
    return errors


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="全量公司分析 Contract v2 校验器")
    parser.add_argument("--registry", type=Path, default=root / "tools" / "full_analysis_contract.json")
    parser.add_argument("--repo-root", type=Path, default=root)
    args = parser.parse_args()
    errors = validate(args.registry, args.repo_root)
    if errors:
        print(f"❌ 注册表校验失败, 共 {len(errors)} 项:")
        for error in errors:
            print(f"  - {error}")
        raise SystemExit(1)
    print("✅ 注册表校验通过: Contract v2 的 20 项契约结构合法")


if __name__ == "__main__":
    main()
