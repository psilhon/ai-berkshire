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
    "earnings-review", "industry-research", "industry-funnel",
    "bottleneck-hunter", "news-pulse", "thesis-tracker",
}
EXPECTED_SCHEMA = {
    "schema_version": "full-analysis-contract/v2",
    "manifest_schema_version": "full-analysis-manifest/v2",
    "result_schema_version": "result-schema/v1",
}
EXPECTED_STAGE_KEYS = {
    "01-data-screen", "02-company-earnings", "03-industry-opportunity",
    "04-thesis-boundary",
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
EXPECTED_AUTHORIZATION = {
    "profile": "full-analysis-internal/v1",
    "granted": [
        "read_only_external_research",
        "run_root_local_writes",
        "research_conclusions",
    ],
    "denied": [
        "external_publish",
        "external_messages",
        "vcs_remote_writes",
        "writes_outside_run_root",
        "sensitive_private_data",
    ],
}


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


def _audit_evaluator_kinds(repo_root: Path) -> tuple[set[str] | None, str | None]:
    """独立解析 Audit evaluator 注册表，防止 Contract 规则注册后无人执行。"""
    path = repo_root / "tools" / "full_analysis_audit.py"
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError) as exc:
        return None, f"Audit evaluator 注册表不可读或语法非法: {exc}"
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "RULE_EVALUATORS"
                   for target in node.targets):
            continue
        if not isinstance(node.value, ast.Dict):
            return None, "RULE_EVALUATORS 必须是字面量字典"
        kinds = {
            key.value for key in node.value.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        }
        return kinds, None
    return None, "Audit 未声明 RULE_EVALUATORS"


def _validate_evidence(errors: list[str], label: str, rules: object,
                       known_skills: set[str], ashare_commands: set[str] | None) -> None:
    if not isinstance(rules, list) or not rules:
        _err(errors, f"{label} evidence_rules 必须为非空数组")
        return
    # 跨字段一致性：kind 唯一（同一 skill 不允许重复证据规则类型）
    kinds = [r.get("kind") for r in rules if isinstance(r, dict)]
    dup_kinds = sorted({k for k in kinds if kinds.count(k) > 1})
    if dup_kinds:
        _err(errors, f"{label} evidence_rules kind 重复: {dup_kinds}")
    registered_ops: set[str] = set()
    min_facts: int | None = None
    min_dual: int | None = None
    req_fields_count: int | None = None
    for rule in rules:
        if not isinstance(rule, dict) or rule.get("kind") not in EVIDENCE_KINDS:
            _err(errors, f"{label} evidence_rule kind 非法: {rule!r}")
            continue
        kind = rule["kind"]
        if kind.startswith("min_"):
            n = rule.get("n")
            if not isinstance(n, int) or isinstance(n, bool) or n <= 0:
                _err(errors, f"{label} evidence_rule n 必须为正整数: {rule!r}")
            elif kind == "min_facts":
                min_facts = n
            elif kind == "min_dual_source_facts":
                min_dual = n
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
            else:
                if kind == "required_fact_fields":
                    req_fields_count = len(values)
                    dup_fields = sorted({v for v in values if values.count(v) > 1})
                    if dup_fields:
                        _err(errors, f"{label} required_fact_fields 值重复: {dup_fields}")
                if kind == "required_command_operations":
                    registered_ops.update(values)
    # 跨字段一致性：min_facts 必须 >= required_fact_fields 数量（否则逻辑不可达）
    if min_facts is not None and req_fields_count is not None and min_facts < req_fields_count:
        _err(errors, f"{label} min_facts({min_facts}) < required_fact_fields 数量({req_fields_count})，逻辑不可达")
    # 跨字段一致性：min_dual_source_facts 不得超过 min_facts（否则逻辑不可达）
    if min_facts is not None and min_dual is not None and min_dual > min_facts:
        _err(errors, f"{label} min_dual_source_facts({min_dual}) > min_facts({min_facts})，逻辑不可达")
    if ashare_commands is not None and label.startswith("[ashare-data:"):
        missing = sorted(registered_ops - ashare_commands)
        if missing:
            _err(errors, f"{label} 注册 operation 不存在于 ashare CLI: {missing}")


def validate_v2(registry_path: Path, repo_root: Path) -> list[str]:
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
    if registry.get("authorization_profile") != EXPECTED_AUTHORIZATION:
        _err(
            errors,
            "authorization_profile 必须与 full-analysis-internal/v1 "
            "封闭授权边界完全一致",
        )
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
    if len(skills) != 13:
        _err(errors, f"skills 必须恰好 13 项, 实际 {len(skills)} 项")
    ids = [s.get("skill_id") for s in skills if isinstance(s, dict)]
    if set(ids) != EXPECTED_SKILLS or len(ids) != len(set(ids)):
        _err(errors, "skill_id 必须与 13 项白名单完全一致且无重复")
    paths: dict[str, str] = {}
    known = set(ids)
    ashare_commands, cli_error = _ashare_cli_commands(repo_root)
    if cli_error:
        _err(errors, cli_error)
    evaluator_kinds, evaluator_error = _audit_evaluator_kinds(repo_root)
    if evaluator_error:
        _err(errors, evaluator_error)
    elif evaluator_kinds != EVIDENCE_KINDS:
        _err(errors, "Audit evaluator 与 Contract evidence kind 不一致: "
             f"missing={sorted(EVIDENCE_KINDS - evaluator_kinds)} "
             f"extra={sorted(evaluator_kinds - EVIDENCE_KINDS)}")
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
            section_headings = []
            for section in sections:
                if not isinstance(section, dict):
                    _err(errors, f"{label} section 必须为对象"); continue
                section_id = section.get("section_id")
                section_ids.append(section_id)
                if not isinstance(section_id, str) or not section_id.isidentifier() or not section_id.isascii():
                    _err(errors, f"{label} section_id 非法: {section_id!r}")
                heading = section.get("heading")
                section_headings.append(heading)
                if not isinstance(heading, str) or not heading:
                    _err(errors, f"{label} section heading 缺失")
                if not isinstance(section.get("required"), bool):
                    _err(errors, f"{label} section required 必须为 bool")
                min_chars = section.get("min_content_chars")
                if not isinstance(min_chars, int) or isinstance(min_chars, bool) or min_chars < 0:
                    _err(errors, f"{label} section min_content_chars 非法")
                elif section.get("required") is True and min_chars <= 0:
                    _err(errors, f"{label} required section {section_id!r} min_content_chars 必须 > 0")
            if len(section_ids) != len(set(section_ids)):
                _err(errors, f"{label} section_id 必须唯一")
            if len(section_headings) != len(set(section_headings)):
                _err(errors, f"{label} section heading 必须唯一")
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
            role_rules = [
                rule for rule in item.get("evidence_rules", [])
                if isinstance(rule, dict) and rule.get("kind") == "min_role_runs"
            ]
            if roles.get("mode") == "single_agent" and role_rules:
                _err(errors, f"{label} single_agent 不得配置 min_role_runs")
            if roles.get("mode") == "independent_then_integrator" and role_rules:
                verifiable_roles = [
                    role for role in roles["required_roles"]
                    if role != "integrator"
                ]
                if role_rules[0].get("n", 0) > len(verifiable_roles):
                    _err(
                        errors,
                        f"{label} min_role_runs 超过可验证独立角色数 "
                        f"{len(verifiable_roles)}",
                    )
        _validate_evidence(errors, label, item.get("evidence_rules"), known, ashare_commands)

    # v3.3.10 T4：depends_on 依赖图校验（自包含，不 import Runtime——本脚本刻意独立）。
    # 语义与 runtime.build_dependency_graph 一致：ashare-data 为根；缺省 depends_on 视为
    # 仅依赖 ashare-data；依赖须引用已注册 skill；整图不得有环（否则波次调度死锁）。
    graph: dict[str, list[str]] = {}
    for item in skills:
        sid = item.get("skill_id")
        if not isinstance(sid, str):
            continue
        if sid == "ashare-data":
            deps: list = []
        else:
            deps = item.get("depends_on")
            if deps is None:
                deps = ["ashare-data"]
            if not isinstance(deps, list) or not all(isinstance(d, str) for d in deps):
                _err(errors, f"[{sid}:v2] depends_on 必须为字符串数组")
                deps = []
            unknown = [d for d in deps if d not in known]
            if unknown:
                _err(errors, f"[{sid}:v2] depends_on 引用未注册 skill: {unknown}")
            if sid in deps:
                _err(errors, f"[{sid}:v2] depends_on 不得自引用")
        graph[sid] = [d for d in deps if d in known and d != sid]

    WHITE, GRAY, BLACK = 0, 1, 2
    color = {node: WHITE for node in graph}

    def _has_cycle(node: str, stack: list) -> list | None:
        color[node] = GRAY
        stack.append(node)
        for dep in graph.get(node, []):
            if dep not in color:
                continue
            if color[dep] == GRAY:
                return stack[stack.index(dep):] + [dep]
            if color[dep] == WHITE:
                found = _has_cycle(dep, stack)
                if found:
                    return found
        stack.pop()
        color[node] = BLACK
        return None

    for node in graph:
        if color[node] == WHITE:
            cycle = _has_cycle(node, [])
            if cycle:
                _err(errors, f"depends_on 存在依赖环: {' -> '.join(cycle)}")
                break
    return errors


LEAN_FORBIDDEN_SKILL_KEYS = {"sections", "evidence_rules", "artifact_id", "predicates",
                                "audit_policy", "dual_source", "negative_acceptance_dir"}
LEAN_FORBIDDEN_TOP_LEVEL_KEYS = {"result_schema_version", "predicates",
                                 "pwl_allowlist", "pwl_forbidden",
                                 "generic_required_sections", "negative_acceptance_dir"}


def validate_lean(registry: dict, repo_root: Path) -> list[str]:
    """lean-v1 契约校验：仅保留报告路径、实质下限、扇出与依赖图；不校验已移除的
    sections/evidence_rules/artifact_id 等 v2 专有字段。"""
    errors: list[str] = []
    if registry.get("schema_version") != "full-analysis-contract/lean-v1":
        _err(errors, f"顶层 schema_version 必须为 full-analysis-contract/lean-v1, 实际 {registry.get('schema_version')!r}")
    if registry.get("manifest_schema_version") != "full-analysis-manifest/lean-v1":
        _err(errors, "manifest_schema_version 必须为 full-analysis-manifest/lean-v1")
    for key in LEAN_FORBIDDEN_TOP_LEVEL_KEYS:
        if key in registry:
            _err(errors, f"lean-v1 禁止顶层键 {key!r}")
    if registry.get("authorization_profile") != EXPECTED_AUTHORIZATION:
        _err(errors, "authorization_profile 必须与 full-analysis-internal/v1 封闭授权边界完全一致")
    stage_dirs = registry.get("stage_dirs")
    if not isinstance(stage_dirs, dict) or not stage_dirs:
        _err(errors, "stage_dirs 必须为非空对象")
        stage_dirs = {}
    stage_roots = set(stage_dirs.values())
    skills = registry.get("skills")
    if not isinstance(skills, list):
        return errors + ["顶层 skills 必须为数组"]
    if len(skills) != 13:
        _err(errors, f"skills 必须恰好 13 项, 实际 {len(skills)} 项")
    ids = [s.get("skill_id") for s in skills if isinstance(s, dict)]
    if set(ids) != EXPECTED_SKILLS or len(ids) != len(set(ids)):
        _err(errors, "skill_id 必须与 13 项白名单完全一致且无重复")
    known = set(ids)
    paths: dict[str, str] = {}
    for item in skills:
        if not isinstance(item, dict):
            _err(errors, f"skill 条目必须为对象: {item!r}"); continue
        sid = item.get("skill_id"); label = f"[{sid}:lean]"
        for fk in LEAN_FORBIDDEN_SKILL_KEYS:
            if fk in item:
                _err(errors, f"{label} 禁止 v2-only 字段 {fk!r}")
            elif fk in (item.get("artifact") or {}):
                _err(errors, f"{label} artifact 禁止 v2-only 字段 {fk!r}")
        stage = item.get("stage_dir")
        if stage not in stage_dirs:
            _err(errors, f"{label} stage_dir 不在 stage_dirs: {stage!r}")
        spec = item.get("spec_source")
        if not isinstance(spec, str) or not (repo_root / spec).is_file():
            _err(errors, f"{label} spec_source 不存在: {spec!r}")
        artifact = item.get("artifact")
        if not isinstance(artifact, dict):
            _err(errors, f"{label} artifact 必须为对象"); artifact = {}
        path = artifact.get("formal_path")
        if not isinstance(path, str) or not path or path.startswith("/") or ".." in path.split("/"):
            _err(errors, f"{label} formal_path 非法: {path!r}")
        elif stage in stage_dirs and not path.startswith(stage_dirs[stage] + "/"):
            _err(errors, f"{label} formal_path 不在阶段目录下: {path!r}")
        elif path in paths:
            _err(errors, f"{label} formal_path 与 {paths[path]} 冲突")
        else:
            paths[path] = label
        if "artifact_id" in artifact:
            _err(errors, f"{label} artifact 不得含 artifact_id（lean 由 skill_id 推导）")
        minb = artifact.get("min_bytes")
        if not isinstance(minb, int) or isinstance(minb, bool) or minb <= 0:
            _err(errors, f"{label} min_bytes 必须为正整数")
        mins = item.get("min_substantive_sections")
        if not isinstance(mins, int) or isinstance(mins, bool) or mins < 1:
            _err(errors, f"{label} min_substantive_sections 必须为 >=1 的整数")
        rg = item.get("report_guidance")
        if not isinstance(rg, str) or not rg.strip():
            _err(errors, f"{label} report_guidance 必须为非空字符串")
        sub = item.get("substance")
        if not isinstance(sub, dict):
            _err(errors, f"{label} substance 必须为对象")
        else:
            for key in ("require_as_of", "require_sources", "require_disclaimer"):
                if not isinstance(sub.get(key), bool):
                    _err(errors, f"{label} substance.{key} 必须为 bool")
        app = item.get("applicability")
        if not isinstance(app, dict) or not isinstance(app.get("predicate"), str) or not app["predicate"]:
            _err(errors, f"{label} applicability.predicate 必须为非空字符串")
        elif app.get("alternative") is not None and not isinstance(app["alternative"], str):
            _err(errors, f"{label} applicability.alternative 必须为 null 或字符串")
        roles = item.get("roles")
        if not isinstance(roles, dict) or not isinstance(roles.get("required_roles"), list):
            _err(errors, f"{label} roles.required_roles 必须为数组")
        elif roles.get("mode") not in {"single_agent", "independent_then_integrator"}:
            _err(errors, f"{label} roles.mode 非法")

    # depends_on 依赖图校验（自包含，与 runtime.build_dependency_graph 语义一致）
    graph: dict[str, list[str]] = {}
    for item in skills:
        sid = item.get("skill_id")
        if not isinstance(sid, str):
            continue
        if sid == "ashare-data":
            deps = []
        else:
            deps = item.get("depends_on")
            if deps is None:
                deps = ["ashare-data"]
            if not isinstance(deps, list) or not all(isinstance(d, str) for d in deps):
                _err(errors, f"[{sid}:lean] depends_on 必须为字符串数组")
                deps = []
        unknown = [d for d in deps if d not in known]
        if unknown:
            _err(errors, f"[{sid}:lean] depends_on 引用未注册 skill: {unknown}")
        if sid in deps:
            _err(errors, f"[{sid}:lean] depends_on 不得自引用")
        graph[sid] = [d for d in deps if d in known and d != sid]

    WHITE, GRAY, BLACK = 0, 1, 2
    color = {node: WHITE for node in graph}

    def _has_cycle(node: str, stack: list) -> list | None:
        color[node] = GRAY
        stack.append(node)
        for dep in graph.get(node, []):
            if dep not in color:
                continue
            if color[dep] == GRAY:
                return stack[stack.index(dep):] + [dep]
            if color[dep] == WHITE:
                found = _has_cycle(dep, stack)
                if found:
                    return found
        stack.pop()
        color[node] = BLACK
        return None

    for node in graph:
        if color[node] == WHITE:
            cycle = _has_cycle(node, [])
            if cycle:
                _err(errors, f"depends_on 存在依赖环: {' -> '.join(cycle)}")
                break
    return errors


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="全量公司分析 Contract 校验器（v2 / lean-v1 自动识别）")
    parser.add_argument("--registry", type=Path, default=root / "tools" / "full_analysis_contract.json")
    parser.add_argument("--repo-root", type=Path, default=root)
    args = parser.parse_args()
    try:
        registry = json.loads(args.registry.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"❌ 注册表不可读或非法 JSON: {exc}")
        raise SystemExit(1)
    sv = registry.get("schema_version")
    if sv == "full-analysis-contract/v2":
        errors = validate_v2(args.registry, args.repo_root)
    elif sv == "full-analysis-contract/lean-v1":
        errors = validate_lean(registry, args.repo_root)
    else:
        print(f"❌ 不支持的 schema_version: {sv!r}（仅支持 v2 / lean-v1）")
        raise SystemExit(1)
    if errors:
        print(f"❌ 注册表校验失败, 共 {len(errors)} 项:")
        for error in errors:
            print(f"  - {error}")
        raise SystemExit(1)
    print(f"✅ 注册表校验通过: {sv} 契约结构合法")


if __name__ == "__main__":
    main()
