#!/usr/bin/env python3
"""独立注册表校验器 — 全量公司分析 20 项机器契约 (v1.4 §6.1.4/§15.2#1#12).

不 import full_analysis_gate: 与 gate 单测构成两条独立实现路径, 防注册表与 gate 同错。
所有错误一次全部列出后再退出 (失败要大声, 不只报第一个)。
退出码: 0=通过 / 1=校验失败。

保存路径覆盖检查 (§15.2#12):
- 只扫"保存语义章节" (标题含 保存/输出/报告位置/文件命名/存储/写入), 到下一同级或更高级标题为止,
  避免把正文里的参考样本误当写入目标;
- 章节内提取: 行内代码 `...` 中的路径状字符串 + 围栏代码块内以 reports/ 或 ~/ 开头的锚定 token
  (earnings-team 的输出文件树即此形态);
- 排除仓库基础设施引用 (skills/ tools/ scripts/ tests/ data/ docs/ 前缀) —— 它们是交叉引用不是写入目标;
- 每条提取路径必须被该项 legacy_output_patterns 覆盖: 模板占位符 {company}/{date}/{period}/{industry}
  替换为 .+? 后正则全匹配; 候选路径里的中文占位符变体 ({公司名}/[公司名]/{YYYYMMDD}/{日期}/{期间}/{行业名})
  先归一为字面样例再匹配; 裸文件名候选允许与模板 basename 匹配;
- 模板禁止 glob 通配 ("*"): §8.3 过宽模式不能算覆盖证明。
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ALLOWED_AUDIT_POLICY = {"required", "advisory", "none"}
ALLOWED_SEQUENTIAL_CAP = {"PASS", "PASS_WITH_LIMITATIONS", "NOT_APPLICABLE_PASS"}
ALLOWED_EVIDENCE_STATUS = {"PHASE2_PENDING", "IMPLEMENTED"}
ALLOWED_PLACEHOLDERS = {"company", "date", "period", "industry"}
# Phase 2: gate 可机械执法的领域证据规则 (须与 full_analysis_gate.EVIDENCE_RULE_KINDS 一致)
EVIDENCE_RULE_KINDS = {
    "min_facts", "min_dual_source_facts", "min_calculations",
    "min_judgments_with_falsification", "min_role_runs",
    "min_command_receipts", "required_fact_fields",
    "required_judgment_rule_ids", "required_command_operations",
}
COUNT_RULE_KINDS = {
    "min_facts", "min_dual_source_facts", "min_calculations",
    "min_judgments_with_falsification", "min_role_runs",
    "min_command_receipts",
}
DOMAIN_RULE_KINDS = {
    "required_fact_fields", "required_judgment_rule_ids",
    "required_command_operations",
}

SAVE_HEADING_KEYWORDS = ("保存", "输出", "报告位置", "文件命名", "存储", "写入",
                         "更新", "写作")

# 候选路径里的占位符变体 → 归一为字面样例后与模板正则匹配
_CANDIDATE_PLACEHOLDER_RE = re.compile(
    r"\{公司名\}|\[公司名\]|\{日期\}|\{YYYYMMDD\}|\{期间\}|\{行业名\}|\{行业\}|"
    r"\{趋势名\}|\{主题\}|\{name\}")
# 仓库基础设施前缀: 交叉引用, 不是保存目标
_INFRA_PREFIXES = ("skills/", "tools/", "scripts/", "tests/", "data/", "docs/",
                   "codex-skills/", "codex-prompts/")


def _err(errors, msg):
    errors.append(msg)


def extract_save_paths(text):
    """从一个 skill 规范提取保存语义章节内的硬编码保存路径候选。"""
    candidates = []
    in_save = False
    save_level = 0
    in_fence = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            m = re.match(r"^(#{1,4})\s+(.*)", line)
            if m:
                level = len(m.group(1))
                title = m.group(2)
                if in_save and level <= save_level:
                    in_save = False
                if any(k in title for k in SAVE_HEADING_KEYWORDS):
                    in_save = True
                    save_level = level
                continue
        if not in_save:
            continue
        if in_fence:
            # 围栏块内只取锚定 token (reports/... 或 ~/...), 目录树叶子等裸名跳过
            for tok in re.findall(r"\S+", line):
                tok = tok.strip("`,;，。()（）<>")
                if tok.startswith("reports/") or tok.startswith("~/"):
                    candidates.append(tok)
        else:
            for span in re.findall(r"`([^`]+)`", line):
                s = span.strip()
                if " " in s:
                    continue  # 含空格 = 命令/句子, 非路径
                if s.startswith(_INFRA_PREFIXES):
                    continue
                if (s.startswith("reports/") or s.startswith("~")
                        or s.startswith("local/")
                        or s.endswith(".md") or s.endswith("/")):
                    candidates.append(s)
    return candidates


def pattern_to_regex(pattern):
    """模板 → 正则: 占位符 {company}/{date}/{period}/{industry} → .+? , 其余转义。"""
    esc = re.escape(pattern)
    for ph in ALLOWED_PLACEHOLDERS:
        esc = esc.replace(re.escape("{%s}" % ph), r".+?")
    return re.compile("^" + esc + "$")


def normalize_candidate(candidate):
    return _CANDIDATE_PLACEHOLDER_RE.sub("样例", candidate)


def candidate_covered(candidate, patterns):
    norm = normalize_candidate(candidate)
    for p in patterns:
        rx = pattern_to_regex(p)
        if rx.match(norm):
            return True
        # 裸文件名候选允许匹配模板 basename (如 `{公司名}-checklist-{YYYYMMDD}.md`)
        if "/" not in norm.rstrip("/"):
            base = p.rstrip("/").rsplit("/", 1)[-1]
            if pattern_to_regex(base).match(norm):
                return True
    return False


def validate(registry_path: Path, repo_root: Path):
    errors = []
    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return [f"注册表不可读或非法 JSON: {registry_path}: {e}"]

    for key in ("registry_schema_version", "manifest_schema_version",
                "annotations_schema_version"):
        if not isinstance(registry.get(key), int):
            _err(errors, f"顶层 {key} 缺失或不是 int")

    stage_dirs = registry.get("stage_dirs")
    if not isinstance(stage_dirs, dict) or not stage_dirs:
        _err(errors, "顶层 stage_dirs 缺失或为空")
        stage_dirs = {}
    predicates = registry.get("predicates")
    if not isinstance(predicates, list) or not predicates:
        _err(errors, "顶层 predicates 缺失或为空")
        predicates = []
    generic_sections = registry.get("generic_required_sections") or []

    skills = registry.get("skills")
    if not isinstance(skills, list):
        return errors + ["顶层 skills 缺失或不是数组"]

    if len(skills) != 20:
        _err(errors, f"skills 必须恰好 20 项, 实际 {len(skills)} 项")
    indexes = [s.get("index") for s in skills]
    if sorted(indexes) != list(range(1, len(skills) + 1)):
        _err(errors, f"index 必须为 1..{len(skills)} 无重无缺, 实际 {sorted(indexes)}")
    names = [s.get("name") for s in skills]
    dup_names = {n for n in names if names.count(n) > 1}
    if dup_names:
        _err(errors, f"name 重复: {sorted(dup_names)}")

    seen_paths = {}
    for item in skills:
        label = f"[{item.get('index')}:{item.get('name')}]"

        stage = item.get("stage")
        if stage not in stage_dirs:
            _err(errors, f"{label} stage {stage!r} 不在 stage_dirs")

        src = item.get("spec_source")
        if not isinstance(src, str) or not src:
            _err(errors, f"{label} spec_source 缺失")
            src = None
        else:
            src_path = repo_root / src
            if not src_path.is_file():
                _err(errors, f"{label} spec_source 不存在: {src}")
                src = None
            else:
                hashlib.sha256(src_path.read_bytes()).hexdigest()  # sha256 可计算

        rules = item.get("artifact_rules")
        if not isinstance(rules, list) or not rules:
            _err(errors, f"{label} artifact_rules 缺失或为空")
            rules = []
        for rule in rules:
            path = rule.get("path", "")
            if not path or path.startswith("/") or ".." in path.split("/"):
                _err(errors, f"{label} artifact path 非法 (绝对路径或含 ..): {path!r}")
            elif stage in stage_dirs and not path.startswith(stage_dirs[stage] + "/"):
                _err(errors, f"{label} artifact path {path!r} 不在 stage 目录 "
                             f"{stage_dirs[stage]!r} 下")
            if path in seen_paths:
                _err(errors, f"{label} artifact path 与 {seen_paths[path]} 冲突: {path}")
            else:
                seen_paths[path] = label
            mb = rule.get("min_bytes")
            if not isinstance(mb, int) or isinstance(mb, bool) or mb <= 0:
                _err(errors, f"{label} min_bytes 必须为正 int, 实际 {mb!r}")
            if rule.get("audit_policy") not in ALLOWED_AUDIT_POLICY:
                _err(errors, f"{label} audit_policy 非法: {rule.get('audit_policy')!r} "
                             f"(允许 {sorted(ALLOWED_AUDIT_POLICY)})")
            secs = rule.get("required_sections")
            if not isinstance(secs, list) or not secs \
                    or not all(isinstance(x, str) and x for x in secs):
                _err(errors, f"{label} required_sections 必须为非空字符串列表")

        ar = item.get("applicability_rule", {})
        pid = ar.get("predicate_id")
        if not pid:
            _err(errors, f"{label} applicability_rule.predicate_id 缺失")
        elif pid not in predicates:
            _err(errors, f"{label} predicate_id {pid!r} 不在顶层 predicates 列表")
        alt = ar.get("alternative")
        if alt is not None and not isinstance(alt, str):
            _err(errors, f"{label} alternative 必须为 null 或字符串")

        rr = item.get("role_rule", {})
        if not isinstance(rr.get("required_roles"), list):
            _err(errors, f"{label} role_rule.required_roles 必须为 list")
        mic = rr.get("min_independent_contexts")
        if not isinstance(mic, int) or isinstance(mic, bool) or mic < 0:
            _err(errors, f"{label} role_rule.min_independent_contexts 必须为非负 int")
        if rr.get("sequential_cap") not in ALLOWED_SEQUENTIAL_CAP:
            _err(errors, f"{label} role_rule.sequential_cap 非法: "
                         f"{rr.get('sequential_cap')!r}")

        status = item.get("domain_evidence_status")
        if status not in ALLOWED_EVIDENCE_STATUS:
            _err(errors, f"{label} domain_evidence_status 非法: {status!r}")

        # Phase 2: evidence_rules 词表校验
        ev_rules = item.get("evidence_rules", [])
        if not isinstance(ev_rules, list):
            _err(errors, f"{label} evidence_rules 必须为 list")
            ev_rules = []
        for rule in ev_rules:
            if not isinstance(rule, dict) or rule.get("kind") not in EVIDENCE_RULE_KINDS:
                _err(errors, f"{label} evidence_rule kind 非法 (允许 "
                             f"{sorted(EVIDENCE_RULE_KINDS)}): {rule!r}")
                continue
            if rule["kind"] in COUNT_RULE_KINDS:
                n = rule.get("n", 1)
                if not isinstance(n, int) or isinstance(n, bool) or n <= 0:
                    _err(errors, f"{label} evidence_rule n 必须为正 int: {rule!r}")
            else:
                values = rule.get("values")
                if not isinstance(values, list) or not values \
                        or len(values) != len(set(values)) \
                        or not all(isinstance(x, str) and x for x in values):
                    _err(errors, f"{label} evidence_rule values 必须为非空唯一"
                                 f"字符串数组: {rule!r}")

        # §15.3: 标题字符串只能证明结构存在，不能代替可执行领域证据。
        if status == "IMPLEMENTED" and not ev_rules:
            _err(errors, f"{label} 标 IMPLEMENTED 但 evidence_rules 为空 —— "
                         "领域 required_sections 不能代替机器证据")
        elif status == "IMPLEMENTED" and not any(
                rule.get("kind") in DOMAIN_RULE_KINDS
                for rule in ev_rules if isinstance(rule, dict)):
            _err(errors, f"{label} 标 IMPLEMENTED 但缺带领域标识的 "
                         "evidence rule")

        patterns = item.get("legacy_output_patterns")
        if not isinstance(patterns, list):
            _err(errors, f"{label} legacy_output_patterns 必须为 list")
            patterns = []
        for p in patterns:
            if "*" in p:
                _err(errors, f"{label} legacy pattern 含 glob 通配 '*' (过宽模式"
                             f"不能算覆盖证明 §8.3): {p!r}")
            for ph in re.findall(r"\{([^}]+)\}", p):
                if ph not in ALLOWED_PLACEHOLDERS:
                    _err(errors, f"{label} legacy pattern 占位符非法 {{{ph}}} "
                                 f"(允许 {sorted(ALLOWED_PLACEHOLDERS)}): {p!r}")

        # §15.2#12 保存路径覆盖检查
        if src:
            text = (repo_root / src).read_text(encoding="utf-8")
            clean_patterns = [p for p in patterns if "*" not in p]
            for cand in extract_save_paths(text):
                if not candidate_covered(cand, clean_patterns):
                    _err(errors, f"{label} 保存章节提取路径未被 legacy_output_patterns "
                                 f"覆盖: {cand!r} (需精确模板, 不许 glob 兜底)")

    return errors


def main():
    parser = argparse.ArgumentParser(description="全量公司分析注册表独立校验器")
    default_root = Path(__file__).resolve().parents[1]
    parser.add_argument("--registry", type=Path,
                        default=default_root / "tools" / "full_analysis_contract.json")
    parser.add_argument("--repo-root", type=Path, default=default_root)
    args = parser.parse_args()

    errors = validate(args.registry, args.repo_root)
    if errors:
        print(f"❌ 注册表校验失败, 共 {len(errors)} 项:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    print("✅ 注册表校验通过: 20 项契约结构合法, 保存路径覆盖完整")
    sys.exit(0)


if __name__ == "__main__":
    main()
