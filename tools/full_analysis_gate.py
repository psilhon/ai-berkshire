#!/usr/bin/env python3
"""全量公司分析 Gate v2：负责确定性登记、实质验收、晋级与最终收口。

WorkBuddy Runtime 负责调度；Gate 不启动 Agent、不读取报告正文做主观判断，
但会确定性验证 Contract/Result Bundle、报告结构、路径、哈希和状态机。
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from full_analysis_snapshot import analysis_snapshot


TOOLS_DIR = Path(__file__).resolve().parent
DEFAULT_REGISTRY = TOOLS_DIR / "full_analysis_contract.json"
RESULT_SCHEMA_PATH = TOOLS_DIR / "full_analysis_result_schema.json"
MANIFEST_REL = Path("evidence/00-analysis-manifest.json")
RUNTIME_STATE_REL = Path("evidence/runtime-state.json")
EVENTS_REL = Path("evidence/events.jsonl")
PWL_ALLOWLIST = {"tushare_unavailable", "web_bandwidth_degraded", "ephemeral_source"}
RESULT_STATUSES = {"PASS", "PASS_WITH_LIMITATIONS", "NOT_APPLICABLE", "FAIL"}
SUCCESS_TERMINAL_STATUSES = {
    "PASS", "PASS_WITH_LIMITATIONS", "NOT_APPLICABLE",
}
COMPLETED_STATUSES = SUCCESS_TERMINAL_STATUSES | {"FAIL"}
TZ_SHANGHAI = timezone(timedelta(hours=8))

# ---- 实质校验常量（防凑数 / 防片面 / 防坍塌，替代纯字节门槛）----
HEADING_RATIO_CAP = 0.18                # 标题字符占比上限，超则骨架/注水嫌疑
DISSENT_RE = re.compile(r"分歧|争议|🔴|不同意|反向|反面|硬伤|风险点|风险|隐患|不确定性|存疑")
# 扇出角色 id -> 中文名，用于"具名分歧"判定（>=2 角色交锋）
ROLE_NAME_MAP = {
    "duan": "段永平", "buffett": "巴菲特", "munger": "芒格", "li": "李录",
    "editor": "编辑", "reader": "读者",
    "company": "公司", "regulatory": "监管", "industry": "行业", "sentiment": "情绪",
    "governance": "治理", "business": "业务", "technology": "技术", "finance": "财务",
    "alternative-data": "另类", "integrator": "整合",
}
NAMED_DISSENT_DEFAULT = 2               # 扇出类需 >=2 角色在分歧处交锋
# 实质小节判定门槛（归一化后字符数）：低于此值视为"一句话带过/占位"，不计入实质章节。
# 防凑数的关键闸门——逼出真论证（数据/对比/推演），而非短占位。非"写作字数目标"。
SUBSTANTIVE_MIN_CHARS = 150
NON_SUBSTANTIVE_SECTION_IDS = {
    "data_cutoff", "sources_scope", "limitations", "research_disclaimer",
    "downstream_evidence", "contract_calculations", "command_receipts",
    "source_dates", "warnings_gaps",
}
NA_PREDICATE_FIELDS = {
    "has_comparable_financial_history": "has_comparable_financial_history",
    "has_investable_price": "has_investable_price",
    "identifiable_key_managers": "identifiable_key_managers",
    "has_primary_filing_for_period": "has_primary_filing_for_period",
    "main_business_definable": "main_business_definable",
    "physical_bottleneck_exists": "physical_bottleneck_exists",
}
ALWAYS_APPLICABLE_PREDICATES = {"always", "always_applicable", "is_a_share"}
NA_REQUIRED_HEADINGS = ("不适用结论", "判定事实", "证据来源", "替代路径", "限制")
SUMMARY_REQUIRED_HEADINGS = (
    "核心结论速览",
    "主干①·投资分析",
    "主干②·财报研读",
    "主干③·行业分析",
    "补充与参考",
    "产物索引",
    "数据截止日",
    "仅供学习研究",
)
NA_MIN_BYTES = 800
SUMMARY_MIN_BYTES = 2500


class GateError(Exception):
    def __init__(self, message: str, code: int = 1):
        self.code = code
        super().__init__(message)


def now_iso() -> str:
    return datetime.now(TZ_SHANGHAI).isoformat()


def atomic_write_json(path: Path, value: object) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def atomic_copy(
    source: Path,
    target: Path,
    *,
    expected_bytes: int | None = None,
    expected_sha256: str | None = None,
) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as out, source.open("rb") as inp:
            shutil.copyfileobj(inp, out)
            out.flush()
            os.fsync(out.fileno())
        copied = Path(name)
        if (expected_bytes is not None
                and copied.stat().st_size != expected_bytes):
            raise GateError(
                f"复制期间 artifact bytes 发生变化: {source}")
        if (expected_sha256 is not None
                and sha256_file(copied) != expected_sha256):
            raise GateError(
                f"复制期间 artifact sha256 发生变化: {source}")
        os.replace(name, target)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path, label: str) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GateError(f"{label} 不可读或非法 JSON: {path}: {exc}", 2)
    if not isinstance(value, dict):
        raise GateError(f"{label} 顶层必须为对象: {path}", 2)
    return value


def load_registry(path: Path) -> dict:
    registry = load_json(path, "注册表")
    if registry.get("schema_version") != "full-analysis-contract/v2":
        raise GateError("只接受 full-analysis-contract/v2 注册表", 2)
    if len(registry.get("skills", [])) != 13:
        raise GateError("注册表必须恰好包含 13 个 skill", 2)
    return registry


def manifest_path(run_root: Path) -> Path:
    return Path(run_root) / MANIFEST_REL


def load_manifest(run_root: Path) -> dict:
    manifest = load_json(manifest_path(run_root), "manifest")
    if manifest.get("manifest_schema_version") != "full-analysis-manifest/v2":
        raise GateError("只接受 full-analysis-manifest/v2 manifest", 2)
    return manifest


def save_manifest(run_root: Path, manifest: dict) -> None:
    manifest["run"]["updated_at"] = now_iso()
    atomic_write_json(manifest_path(run_root), manifest)


def append_event(run_root: Path, event: dict) -> None:
    path = Path(run_root) / EVENTS_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"event_at": now_iso(), **event}, ensure_ascii=False) + "\n")


def safe_relative(run_root: Path, value: str) -> Path:
    candidate = Path(value)
    if candidate.is_absolute() or "" in candidate.parts or "." in candidate.parts or ".." in candidate.parts:
        raise GateError(f"路径非法: {value!r}")
    root = Path(run_root).resolve()
    target = (root / candidate)
    try:
        if target.exists() and not target.resolve().is_relative_to(root):
            raise GateError(f"路径越出 run_root: {value!r}")
    except OSError as exc:
        raise GateError(f"路径无法解析: {value!r}: {exc}")
    return candidate


def find_skill(registry: dict, skill_id: str) -> dict:
    for item in registry["skills"]:
        if item.get("skill_id") == skill_id:
            return item
    raise GateError(f"未知 skill_id: {skill_id}", 2)


def _schema_type_matches(value, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return True


def _validate_schema_value(value, schema: dict, path: str = "$") -> None:
    """校验 Result Bundle schema 使用的 JSON Schema 子集。"""
    expected = schema.get("type")
    expected_types = expected if isinstance(expected, list) else [expected]
    if expected and not any(
        _schema_type_matches(value, item) for item in expected_types
    ):
        raise GateError(f"{path} 类型非法，期望 {expected}")
    if "const" in schema and value != schema["const"]:
        raise GateError(f"{path} 必须等于 {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        raise GateError(f"{path} 不在允许枚举中")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            raise GateError(f"{path} 长度不足")
        if schema.get("pattern") and not re.search(schema["pattern"], value):
            raise GateError(f"{path} 格式非法")
    if isinstance(value, int) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise GateError(f"{path} 小于最小值 {schema['minimum']}")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            raise GateError(f"{path} 项目数不足")
        if schema.get("uniqueItems"):
            encoded = [
                json.dumps(item, ensure_ascii=False, sort_keys=True)
                for item in value
            ]
            if len(encoded) != len(set(encoded)):
                raise GateError(f"{path} 含重复项目")
        item_schema = schema.get("items")
        if item_schema:
            for index, item in enumerate(value):
                _validate_schema_value(item, item_schema, f"{path}[{index}]")
    if isinstance(value, dict):
        required = set(schema.get("required", []))
        missing = sorted(required - set(value))
        if missing:
            raise GateError(f"{path} 缺字段 {missing}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extra = sorted(set(value) - set(properties))
            if extra:
                raise GateError(f"{path} 含未知字段 {extra}")
        for key, item in value.items():
            if key in properties:
                _validate_schema_value(item, properties[key], f"{path}.{key}")


def validate_result_bundle(bundle: dict, run_root: Path, registry: dict) -> None:
    schema = load_json(RESULT_SCHEMA_PATH, "Result Bundle schema")
    _validate_schema_value(bundle, schema)
    required = set(schema["required"])
    allowed = set(schema.get("properties", {})) or required
    missing = sorted(required - set(bundle))
    # 仅拒绝 schema 未声明的未知字段；schema 中声明的可选实质字段（key_claims/dissent_points 等）放行
    extra = sorted(set(bundle) - allowed)
    if missing or extra:
        raise GateError(f"Result Bundle 顶层字段不匹配 missing={missing} extra={extra}")
    if bundle.get("schema_version") != "result-schema/v1":
        raise GateError("Result Bundle schema_version 必须为 result-schema/v1")
    if bundle.get("status") not in RESULT_STATUSES:
        raise GateError(f"Result Bundle status 非法: {bundle.get('status')!r}")
    if bundle["status"] == "FAIL" and not isinstance(bundle.get("error"), dict):
        raise GateError("FAIL Result Bundle 必须提供 error")
    if (bundle["status"] in SUCCESS_TERMINAL_STATUSES
            and bundle.get("error") is not None):
        raise GateError("成功/PWL/NA Result Bundle 的 error 必须为 null")
    if not isinstance(bundle.get("pwl_candidates"), list) or not set(bundle["pwl_candidates"]).issubset(PWL_ALLOWLIST):
        raise GateError("pwl_candidates 含未注册的 PWL 原因")
    skill = find_skill(registry, bundle.get("skill_id"))
    if bundle.get("run_id") != load_manifest(run_root)["run"]["run_id"]:
        raise GateError("Result Bundle run_id 与 manifest 不一致")
    if not isinstance(bundle.get("artifact_records"), list):
        raise GateError("artifact_records 必须为数组")
    expected = skill["artifact"]["artifact_id"]
    if bundle["status"] in {"PASS", "PASS_WITH_LIMITATIONS"}:
        if len(bundle["artifact_records"]) != 1:
            raise GateError(f"{bundle['skill_id']} 必须恰好提交 1 个正式 artifact")
        if bundle["artifact_records"][0].get("artifact_id") != expected:
            raise GateError(f"artifact_id 不匹配: 期望 {expected}")
        if bundle.get("not_applicable") is not None:
            raise GateError("PASS/PASS_WITH_LIMITATIONS 不得携带 not_applicable")
    elif bundle["status"] == "NOT_APPLICABLE":
        expected_na = f"artifact.na.{bundle['skill_id']}"
        if len(bundle["artifact_records"]) != 1:
            raise GateError(
                f"{bundle['skill_id']} 的 NOT_APPLICABLE 必须恰好提交 1 个负向验收 artifact")
        if bundle["artifact_records"][0].get("artifact_id") != expected_na:
            raise GateError(f"负向验收 artifact_id 不匹配: 期望 {expected_na}")
        _validate_not_applicable(bundle, skill, load_manifest(run_root))
    elif bundle.get("not_applicable") is not None:
        raise GateError("非 NOT_APPLICABLE 状态不得携带 not_applicable")


def _validate_not_applicable(bundle: dict, skill: dict, manifest: dict) -> None:
    proof = bundle.get("not_applicable")
    if not isinstance(proof, dict):
        raise GateError("NOT_APPLICABLE 必须提供可由 Gate 验证的 not_applicable 证明")
    expected_predicate = (skill.get("applicability") or {}).get("predicate")
    if proof.get("predicate") != expected_predicate:
        raise GateError(
            f"not_applicable predicate 不匹配: 期望 {expected_predicate!r}")
    if proof.get("alternative") != (skill.get("applicability") or {}).get("alternative"):
        raise GateError("not_applicable alternative 与 Contract 不一致")
    if expected_predicate in ALWAYS_APPLICABLE_PREDICATES:
        raise GateError(
            f"{skill['skill_id']} 的适用性谓词 {expected_predicate!r} 始终适用，不得标记 N/A")

    fact = next(
        (item for item in bundle.get("fact_updates") or []
         if item.get("fact_id") == proof.get("fact_id")),
        None,
    )
    if not fact:
        raise GateError("not_applicable.fact_id 必须引用本次提交的判定事实")
    if expected_predicate == "min_independent_contexts_2":
        valid_value = (
            fact.get("field") == "independent_context_count"
            and isinstance(fact.get("value"), int)
            and not isinstance(fact.get("value"), bool)
            and fact["value"] < 2
        )
    else:
        valid_value = (
            fact.get("field") == NA_PREDICATE_FIELDS.get(expected_predicate)
            and fact.get("value") is False
        )
    if not valid_value:
        raise GateError(
            f"not_applicable 判定事实不能证明谓词 {expected_predicate!r} 为假")

    source_ids = fact.get("source_ids") or []
    known_source_ids = {
        item.get("source_id")
        for item in [*(manifest.get("sources") or []), *(bundle.get("source_records") or [])]
        if item.get("source_id")
    }
    if not source_ids or any(source_id not in known_source_ids for source_id in source_ids):
        raise GateError("not_applicable 判定事实必须引用已登记来源")
    if not bundle.get("limitations"):
        raise GateError("NOT_APPLICABLE 必须显式记录 limitations")


def build_run_root(repo_root: Path, code: str, company: str) -> Path:
    stamp = datetime.now(TZ_SHANGHAI).strftime("%Y%m%d-%H%M%S")
    short = hashlib.sha256(f"{code}:{company}:{stamp}".encode()).hexdigest()[:6]
    return repo_root / "local" / "Company" / f"{code}-{company}" / f"{stamp}-{short}"


def _company_base_from_run_root(root: Path) -> Path:
    root = Path(root)
    company_dir = root.parent
    company_base = company_dir.parent
    if not root.is_dir() or not company_dir.is_dir() or not company_base.is_dir():
        raise GateError(f"无法从 run_root 推导公司目录: {root}")
    return company_base


def cmd_init(args: argparse.Namespace) -> int:
    registry = load_registry(Path(args.registry))
    if args.platform != "workbuddy":
        raise GateError("生产全量分析只接受 WorkBuddy platform", 2)
    if not re.match(r"^[0-9A-Z]{6}\.(SH|SZ|BJ)$", args.code):
        raise GateError(f"证券代码格式非法: {args.code}", 2)
    root = Path(args.run_root) if args.run_root else build_run_root(Path(args.repo_root), args.code, args.company)
    if root.exists() and any(root.iterdir()):
        raise GateError(f"run_root 已存在且非空: {root}", 2)
    root.mkdir(parents=True, exist_ok=True)
    stage_dirs = list(registry["stage_dirs"].values())
    for rel in stage_dirs + ["06-负向验收"]:
        (root / rel).mkdir(parents=True, exist_ok=True)
    for rel in ["evidence/attempts", "evidence/work-packets", "evidence/snapshots",
                "evidence/preflight", "evidence/commands", "evidence/sources",
                "evidence/audit", "evidence/locks"]:
        (root / rel).mkdir(parents=True, exist_ok=True)
    run_id = "run-" + hashlib.sha256(str(root).encode()).hexdigest()[:16]
    manifest = {
        "manifest_schema_version": "full-analysis-manifest/v2",
        "contract": {"schema_version": registry["schema_version"],
                      "result_schema_version": registry["result_schema_version"],
                      "registry_sha256": sha256_file(Path(args.registry)),
                      "skill_count": len(registry["skills"])},
        "run": {"run_id": run_id, "status": "RUNNING", "created_at": now_iso(), "updated_at": now_iso(),
                "platform": args.platform, "as_of": args.as_of, "run_root": str(root)},
        "company": {"code": args.code, "name": args.company},
        "skills": [{"skill_id": item["skill_id"], "status": "PENDING", "attempts": [], "artifact_records": []}
                   for item in registry["skills"]],
        "artifacts": [], "facts": [], "sources": [], "calculations": [],
        "judgments": [], "command_receipts": [], "role_runs": [],
        "capabilities": {}, "events": [], "delivery": {"summary": None},
    }
    atomic_write_json(root / MANIFEST_REL, manifest)
    atomic_write_json(root / RUNTIME_STATE_REL, {
        "state_version": "runtime-state/v1",
        "run_id": run_id,
        "budget": {
            "normal_target": 26, "stop_dispatch_at": 30, "hard_max": 33,
            "used": 0, "preflight_count": 0, "reserved": 0,
        },
        "concurrency": {"max": 4, "current": 0, "cooldown_until": None},
        "authorization": registry["authorization_profile"],
        "run_started_at": now_iso(),
        "work_units": [{
            "work_unit_id": f"wu-{item['skill_id']}", "skill_id": item["skill_id"],
            "core": item["core"],
            "status": "PENDING", "attempts": 0, "max_attempts": 3,
            "lease": None, "next_retry_at": None,
        } for item in registry["skills"]],
    })
    (root / EVENTS_REL).write_text("", encoding="utf-8")
    for name in ("facts.json", "sources.json", "calculations.json", "artifacts.json"):
        atomic_write_json(root / "evidence" / name, [])
    append_event(root, {"type": "run_initialized", "run_id": run_id})
    print(json.dumps({"run_root": str(root), "run_id": run_id}, ensure_ascii=False))
    return 0


def _section_blocks(text: str) -> list[tuple[str, str]]:
    """把 markdown 切成 (标题, 正文) 块列表。"""
    blocks: list[tuple[str, str]] = []
    cur_h: str | None = None
    cur: list[str] = []
    for ln in text.splitlines():
        m = re.match(r"^(#{1,6})\s+(.*)$", ln)
        if m:
            if cur_h is not None:
                blocks.append((cur_h, "\n".join(cur)))
            cur_h = m.group(2).strip()
            cur = []
        else:
            cur.append(ln)
    if cur_h is not None:
        blocks.append((cur_h, "\n".join(cur)))
    return blocks


def _substance_errors(skill: dict, text: str) -> list[str]:
    """确定性实质校验：防凑数、防空壳、防片面。返回错误列表（空=通过）。

    不依赖字节总数，也不强行匹配 contract 的小节标题原文（避免拒绝措辞不同但扎实的报告），
    只校验可机器核验的"结果"信号：
      - 有实质内容的小节数（每节足够正文/含表格/含数字，防空壳/纯标题）
      - 分歧/反面检验标记数（防片面，逼出不同视角交锋）
      - 扇出类具名分歧（>=2 角色在分歧处交锋）
      - 标题占比（防骨架/注水）
    contract.sections 是确定性准出契约；required/min_content_chars/min_substantive_sections
    均在此执行，避免"注册了章节规则但 Gate 不检查"。
    """
    errors: list[str] = []
    stype = skill.get("skill_type", "analysis")
    blocks = _section_blocks(text)
    bodies_by_heading: dict[str, list[str]] = {}
    for heading, body in blocks:
        bodies_by_heading.setdefault(heading, []).append(body)

    # 1. 必需章节与章节最小内容
    substantive_bodies = set()
    for section in skill.get("sections", []):
        if not section.get("required"):
            continue
        heading = section.get("heading", "")
        bodies = bodies_by_heading.get(heading, [])
        if not bodies:
            errors.append(f"缺必需章节: {heading}")
            continue
        normalized = re.sub(r"\s+", "", "\n".join(bodies))
        minimum = section.get("min_content_chars", 0)
        if len(normalized) < minimum:
            errors.append(f"章节 {heading} 正文 {len(normalized)} < 下限 {minimum}")
            continue
        if section.get("section_id") not in NON_SUBSTANTIVE_SECTION_IDS and len(normalized) >= max(SUBSTANTIVE_MIN_CHARS, minimum):
            substantive_bodies.add(normalized)
    required_substantive = skill.get("min_substantive_sections", 0)
    if required_substantive and len(substantive_bodies) < required_substantive:
        errors.append(
            f"实质章节 {len(substantive_bodies)} < 下限 {required_substantive}"
            "（重复正文只计一次）")

    # 2. 分歧 / 反面检验标记（防片面，逼出不同视角交锋）
    dissent_pts = len(DISSENT_RE.findall(text))
    need_d = skill.get("min_dissent_points", 0)
    if need_d and dissent_pts < need_d:
        errors.append(f"分歧/反面检验标记 {dissent_pts} < 下限 {need_d}（报告片面，缺不同视角交锋）")
    # 3. 扇出类具名分歧（>=2 角色在分歧处交锋）
    if stype == "fanout":
        roles = (skill.get("roles") or {}).get("required_roles", [])
        names = [ROLE_NAME_MAP.get(r, r) for r in roles if r != "integrator"]
        named = 0
        for m in DISSENT_RE.finditer(text):
            start = max(0, m.start() - 220)
            end = min(len(text), m.end() + 220)
            ctx = text[start:end]
            if sum(1 for nm in set(names) if nm in ctx) >= 2:
                named += 1
        if named < NAMED_DISSENT_DEFAULT:
            errors.append(f"具名分歧（>=2 角色交锋）{named} < 下限 {NAMED_DISSENT_DEFAULT}")
    # 4. 标题占比（防骨架/注水）
    if text:
        head_chars = sum(len(h) for h in re.findall(r"^#{1,6}\s.*$", text, re.M))
        ratio = head_chars / len(text)
        if ratio > HEADING_RATIO_CAP:
            errors.append(f"标题占比 {ratio:.2f} > {HEADING_RATIO_CAP}（骨架/注水嫌疑）")
    # 5. ## 后紧跟 ### 诊断（帮助 Agent 定位"正文为 0"的具体章节）
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        m_h2 = re.match(r"^##\s+(.+)$", ln)
        if m_h2:
            next_line = i + 1
            while next_line < len(lines) and not lines[next_line].strip():
                next_line += 1
            if (next_line < len(lines)
                    and re.match(r"^###\s+", lines[next_line].strip())):
                errors.append(
                    f"章节「{m_h2.group(1).strip()}」后紧跟 ### 子标题，"
                    "缺少正文段落（需在 ## 与 ### 之间插入 ≥150 字正文）")
    return errors


# 溯源账本聚合：跨 bundle 合并 facts/sources/calculations 时保持完整性。
# - sources：按 source_id 去重（同一 id = 同一逻辑来源，多技能引用属正常共享）；
#   丢弃无 id 的占位记录（不携带溯源价值，是噪声）。
# - facts：按 fact_id 去重（后到覆盖）；无 source_ids 的管线事实自动挂接
#   规范来源 src.ashare_pipeline（真实来自 ashare_data.py 管线，非编造）。
# - calculations：按 calculation_id 去重；丢弃无 id 的占位记录。
CANONICAL_PIPELINE_SOURCE = "src.ashare_pipeline"


def _merge_provenance(
    manifest: dict,
    bundle: dict,
    *,
    verified_role_runs: list[dict] | None = None,
) -> None:
    # 证据归因：用 bundle 自带的 skill_id 给每条 fact/calc 打标记（向后兼容，不改 result schema）。
    # 让 Audit 能按 skill 计算应有证据、强制执行 contract 的 evidence_rules。
    owner_skill = bundle.get("skill_id")
    manifest.setdefault("judgments", [])
    manifest.setdefault("command_receipts", [])
    manifest.setdefault("role_runs", [])
    manifest.setdefault("capabilities", {})
    known_sources = {s.get("source_id") for s in manifest["sources"] if s.get("source_id")}
    for src in bundle.get("source_records") or []:
        sid = src.get("source_id")
        if not sid or sid in known_sources:
            continue
        src = {**src, "skill_id": owner_skill} if owner_skill else src
        manifest["sources"].append(src)
        known_sources.add(sid)

    fact_index = {f.get("fact_id"): i for i, f in enumerate(manifest["facts"]) if f.get("fact_id")}
    for fact in bundle.get("fact_updates") or []:
        fid = fact.get("fact_id")
        refs = fact.get("source_ids")
        if not isinstance(refs, list) or not refs:
            # 无来源的管线事实：挂接规范管线来源，保证可追溯
            fact = {**fact, "source_ids": [CANONICAL_PIPELINE_SOURCE]}
        if owner_skill and not fact.get("skill_id"):
            fact = {**fact, "skill_id": owner_skill}
        if fid and fid in fact_index:
            manifest["facts"][fact_index[fid]] = fact
        else:
            if fid:
                fact_index[fid] = len(manifest["facts"])
            manifest["facts"].append(fact)

    calc_ids = {c.get("calculation_id") for c in manifest["calculations"] if c.get("calculation_id")}
    for calc in bundle.get("calculation_requests") or []:
        cid = calc.get("calculation_id")
        if not cid or cid in calc_ids:
            continue
        calc = {**calc, "skill_id": owner_skill} if owner_skill else calc
        manifest["calculations"].append(calc)
        calc_ids.add(cid)

    judgment_index = {
        judgment.get("judgment_id"): i
        for i, judgment in enumerate(manifest["judgments"])
        if judgment.get("judgment_id")
    }
    for judgment in bundle.get("judgments") or []:
        record = {**judgment, "skill_id": owner_skill}
        jid = record.get("judgment_id")
        if jid in judgment_index:
            manifest["judgments"][judgment_index[jid]] = record
        else:
            if jid:
                judgment_index[jid] = len(manifest["judgments"])
            manifest["judgments"].append(record)

    receipt_ids = {
        receipt.get("receipt_id")
        for receipt in manifest["command_receipts"]
        if receipt.get("receipt_id")
    }
    for receipt in bundle.get("command_receipts") or []:
        if receipt.get("receipt_id") in receipt_ids:
            continue
        record = {**receipt, "skill_id": owner_skill}
        manifest["command_receipts"].append(record)
        receipt_ids.add(record.get("receipt_id"))

    role_keys = {
        (record.get("skill_id"), record.get("attempt_id"), record.get("role_id"))
        for record in manifest["role_runs"]
    }
    # 角色运行不得由 Agent 自证；只接收 Gate 从实际独立备忘录生成的记录。
    for role_run in verified_role_runs or []:
        record = {
            **role_run,
            "skill_id": owner_skill,
            "attempt_id": bundle.get("attempt_id"),
        }
        key = (record.get("skill_id"), record.get("attempt_id"), record.get("role_id"))
        if key not in role_keys:
            manifest["role_runs"].append(record)
            role_keys.add(key)

    for capability in bundle.get("capability_records") or []:
        manifest["capabilities"][capability["capability"]] = capability["available"]

    # 确保规范管线来源在账本中登记（供事实挂接引用）
    if any(CANONICAL_PIPELINE_SOURCE in (f.get("source_ids") or []) for f in manifest["facts"]) \
            and CANONICAL_PIPELINE_SOURCE not in {s.get("source_id") for s in manifest["sources"]}:
        manifest["sources"].append({
            "source_id": CANONICAL_PIPELINE_SOURCE,
            "publisher": "ashare_data.py(Tushare+东财+腾讯)",
            "acquired_at": now_iso(),
        })


def cmd_ingest(args: argparse.Namespace) -> int:
    root, registry = Path(args.run_root), load_registry(Path(args.registry))
    manifest = load_manifest(root)
    bundle = load_json(Path(args.result), "Result Bundle")
    validate_result_bundle(bundle, root, registry)
    skill = find_skill(registry, bundle["skill_id"])

    accepted_status = bundle["status"] in {"PASS", "PASS_WITH_LIMITATIONS"}

    # ===== 阶段一：只读校验（全部通过后才晋级；被拒 attempt 绝不触碰正式文件）=====
    prepared: list[tuple[Path, Path, Path, dict]] = []  # (source, formal, formal_rel, record)
    for record in bundle["artifact_records"]:
        rel = safe_relative(root, record.get("path", ""))
        source = root / rel
        if not source.is_file() or source.is_symlink() or not str(rel).startswith("evidence/attempts/"):
            raise GateError(f"artifact 必须来自 evidence/attempts 且为普通文件: {rel}")
        if source.stat().st_size != record.get("bytes") or sha256_file(source) != record.get("sha256"):
            raise GateError(f"artifact bytes/sha256 与 Result Bundle 不一致: {rel}")
        # 防坍塌软下限：仅挡住 403 字节式空报告，不作为深度目标（深度由实质校验保证）
        min_bytes = (
            skill["artifact"].get("min_bytes")
            if accepted_status else NA_MIN_BYTES
        )
        if isinstance(min_bytes, int) and min_bytes > 0 and source.stat().st_size < min_bytes:
            raise GateError(f"artifact 字节数 {source.stat().st_size} < 防坍塌下限 {min_bytes}（{skill['skill_id']}）；报告过浅，拒收退回重试")
        formal_rel = safe_relative(
            root,
            skill["artifact"]["formal_path"]
            if accepted_status
            else f"{registry['negative_acceptance_dir']}/{skill['skill_id']}.md",
        )
        prepared.append((source, root / formal_rel, formal_rel, record))

    # 多角色 skill 必须存在各角色独立备忘录（仅 PASS/PASS_WITH_LIMITATIONS 时校验，NOT_APPLICABLE 跳过）
    verified_role_memos: list[tuple[Path, Path, dict]] = []
    roles = skill.get("roles") or {}
    if accepted_status and roles.get("mode") == "independent_then_integrator":
        attempt_dir = (root / safe_relative(root, bundle["artifact_records"][0].get("path", ""))).parent
        missing = []
        for role in roles.get("required_roles", []):
            if role == "integrator":
                continue
            memo = attempt_dir / f"role-{role}.md"
            if not memo.is_file() or memo.is_symlink() or memo.stat().st_size < 300:
                missing.append(role)
                continue
            formal_rel = Path(
                "evidence/roles",
                bundle["skill_id"],
                bundle["attempt_id"],
                memo.name,
            )
            verified_role_memos.append((
                memo,
                root / formal_rel,
                {
                    "role_id": role,
                    "status": "PASS",
                    "artifact_path": formal_rel.as_posix(),
                    "bytes": memo.stat().st_size,
                    "sha256": sha256_file(memo),
                    "verified_by_gate": True,
                },
            ))
        if missing:
            raise GateError(
                f"多角色 skill {skill['skill_id']} 缺角色独立备忘录: {missing}；"
                f"须先为各角色产出 role-<role>.md（>=300 字节）再整合"
            )

    # 实质校验：防凑数 / 防空壳 / 防片面（替代纯字节门槛）
    if accepted_status and prepared:
        try:
            txt = prepared[0][0].read_text(encoding="utf-8")
        except Exception:
            txt = ""
        sub_errs = _substance_errors(skill, txt)
        if sub_errs:
            raise GateError(
                f"实质校验未通过（{skill['skill_id']}）：" + "；".join(sub_errs)
            )
    elif bundle["status"] == "NOT_APPLICABLE" and prepared:
        try:
            txt = prepared[0][0].read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            txt = ""
        missing_headings = [
            heading for heading in NA_REQUIRED_HEADINGS
            if not re.search(rf"^#{{1,6}}\s+{re.escape(heading)}\s*$", txt, re.M)
        ]
        if missing_headings:
            raise GateError(
                f"负向验收报告缺必需章节: {missing_headings}")

    # ===== 阶段二：内存准备（所有数据处理成功前不得写正式文件）=====
    records = []
    for source, formal, formal_rel, record in prepared:
        records.append({**record, "path": str(formal_rel), "formal": True, "accepted": True})
    verified_role_runs = [record for _, _, record in verified_role_memos]
    next_manifest = copy.deepcopy(manifest)
    entry = next(
        item for item in next_manifest["skills"]
        if item["skill_id"] == bundle["skill_id"]
    )
    entry.update({"status": bundle["status"], "attempts": [*entry.get("attempts", []), bundle["attempt_id"]],
                  "artifact_records": records, "limitations": bundle["limitations"],
                  "not_applicable": bundle.get("not_applicable"),
                  "updated_at": now_iso()})
    next_manifest["artifacts"] = [
        record
        for item in next_manifest["skills"]
        for record in item.get("artifact_records", [])
    ]
    _merge_provenance(
        next_manifest,
        bundle,
        verified_role_runs=verified_role_runs,
    )

    # ===== 阶段三：持久化提交（内存准备已完成，只做原子文件替换）=====
    for source, formal, _, record in prepared:
        atomic_copy(
            source,
            formal,
            expected_bytes=record["bytes"],
            expected_sha256=record["sha256"],
        )
    for source, formal, record in verified_role_memos:
        atomic_copy(
            source,
            formal,
            expected_bytes=record["bytes"],
            expected_sha256=record["sha256"],
        )
    save_manifest(root, next_manifest)
    append_event(root, {"type": "result_ingested", "skill_id": bundle["skill_id"], "attempt_id": bundle["attempt_id"], "status": bundle["status"]})
    print(json.dumps({"skill_id": bundle["skill_id"], "status": bundle["status"], "formal_artifacts": records}, ensure_ascii=False))
    return 0


def cmd_register_summary(args: argparse.Namespace) -> int:
    root, registry = Path(args.run_root), load_registry(Path(args.registry))
    manifest = load_manifest(root)
    incomplete = [
        item["skill_id"] for item in manifest["skills"]
        if item.get("status") not in SUCCESS_TERMINAL_STATUSES
    ]
    if incomplete:
        raise GateError(
            f"总结报告只能在全部业务单元终态后登记，未完成={incomplete}")
    missing_artifacts = [
        item["skill_id"] for item in manifest["skills"]
        if not item.get("artifact_records")
    ]
    if missing_artifacts:
        raise GateError(
            f"总结报告登记前每个业务单元都必须有正式或负向验收产物，缺失={missing_artifacts}")

    source = Path(args.summary)
    if not source.is_absolute():
        source = root / source
    try:
        resolved = source.resolve(strict=True)
        rel = resolved.relative_to(root.resolve())
    except (OSError, ValueError) as exc:
        raise GateError(f"总结报告必须位于 run_root 内: {source}: {exc}")
    if not rel.as_posix().startswith("evidence/attempts/summary/"):
        raise GateError("总结报告必须先写入 evidence/attempts/summary/")
    if not resolved.is_file() or resolved.is_symlink():
        raise GateError("总结报告必须是普通 Markdown 文件")
    try:
        text = resolved.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise GateError(f"总结报告不可读: {exc}")
    if resolved.stat().st_size < SUMMARY_MIN_BYTES:
        raise GateError(
            f"总结报告字节数 {resolved.stat().st_size} < 下限 {SUMMARY_MIN_BYTES}")
    missing_headings = [
        heading for heading in SUMMARY_REQUIRED_HEADINGS
        if not re.search(rf"^#{{1,6}}\s+{re.escape(heading)}\s*$", text, re.M)
    ]
    if missing_headings:
        raise GateError(f"总结报告缺必需章节: {missing_headings}")
    indexed_paths = [
        record["path"]
        for item in manifest["skills"]
        for record in item.get("artifact_records") or []
    ]
    missing_index = [path for path in indexed_paths if path not in text]
    if missing_index:
        raise GateError(
            f"总结报告的产物索引未覆盖全部正式产物: {missing_index}")

    company = re.sub(r"[/\\\\\\x00-\\x1f]+", "-", manifest["company"]["name"]).strip()
    formal_rel = Path(f"{company}-全量分析-总结报告.md")
    formal = root / formal_rel
    atomic_copy(resolved, formal)
    record = {
        "artifact_id": "artifact.delivery-summary",
        "path": formal_rel.as_posix(),
        "bytes": formal.stat().st_size,
        "sha256": sha256_file(formal),
        "formal": True,
        "accepted": True,
        "registered_at": now_iso(),
    }
    manifest.setdefault("delivery", {})["summary"] = record
    save_manifest(root, manifest)
    append_event(root, {
        "type": "summary_registered",
        "path": record["path"],
        "sha256": record["sha256"],
    })
    # 注：索引刷新不在此处——由 _generate_summary_html 成功后统一触发，
    # 确保索引页链接到已落盘的 HTML 展示件。
    print(json.dumps(record, ensure_ascii=False))
    return 0


def _verify_formal_artifacts(root: Path, manifest: dict) -> list[dict]:
    """复核每个已接受正式产物的实际字节/哈希与 manifest 记录一致。

    返回不一致项列表（含 skill_id/path/reason）；空列表 = 全部一致。
    用于 finalize 兜底，防止"manifest 合格、正式文件被覆盖/污染"的隐蔽状态。
    """
    corrupt: list[dict] = []
    for item in manifest.get("skills", []):
        for record in item.get("artifact_records") or []:
            rel = record.get("path", "")
            fp = root / rel
            if not fp.is_file():
                corrupt.append({"skill_id": item["skill_id"], "path": rel, "reason": "missing"})
                continue
            if fp.stat().st_size != record.get("bytes"):
                corrupt.append({"skill_id": item["skill_id"], "path": rel, "reason": "size_mismatch"})
            elif sha256_file(fp) != record.get("sha256"):
                corrupt.append({"skill_id": item["skill_id"], "path": rel, "reason": "sha256_mismatch"})
    for record in manifest.get("role_runs") or []:
        rel = record.get("artifact_path", "")
        fp = root / rel
        if not record.get("verified_by_gate"):
            corrupt.append({
                "skill_id": record.get("skill_id"),
                "path": rel,
                "reason": "role_not_verified",
            })
        elif not fp.is_file() or fp.is_symlink():
            corrupt.append({
                "skill_id": record.get("skill_id"),
                "path": rel,
                "reason": "role_missing",
            })
        elif fp.stat().st_size != record.get("bytes"):
            corrupt.append({
                "skill_id": record.get("skill_id"),
                "path": rel,
                "reason": "role_size_mismatch",
            })
        elif sha256_file(fp) != record.get("sha256"):
            corrupt.append({
                "skill_id": record.get("skill_id"),
                "path": rel,
                "reason": "role_sha256_mismatch",
            })
    summary = (manifest.get("delivery") or {}).get("summary")
    if summary:
        rel = summary.get("path", "")
        fp = root / rel
        if not fp.is_file() or fp.is_symlink():
            corrupt.append({
                "skill_id": "delivery-summary",
                "path": rel,
                "reason": "summary_missing",
            })
        elif fp.stat().st_size != summary.get("bytes"):
            corrupt.append({
                "skill_id": "delivery-summary",
                "path": rel,
                "reason": "summary_size_mismatch",
            })
        elif sha256_file(fp) != summary.get("sha256"):
            corrupt.append({
                "skill_id": "delivery-summary",
                "path": rel,
                "reason": "summary_sha256_mismatch",
            })
    return corrupt


def cmd_finalize(args: argparse.Namespace) -> int:
    root, registry = Path(args.run_root), load_registry(Path(args.registry))
    manifest = load_manifest(root)
    # 完整性前置：先复核正式文件与 manifest 记录一致（捕获"manifest 合格、正式文件被覆盖/污染"）
    corrupt = _verify_formal_artifacts(root, manifest)
    if corrupt:
        manifest["run"]["status"] = "PARTIAL"
        save_manifest(root, manifest)
        raise GateError(f"finalize 未准出: 正式文件与 manifest 哈希不一致={corrupt}")
    pending = [
        item["skill_id"] for item in manifest["skills"]
        if item["status"] not in COMPLETED_STATUSES
    ]
    missing = [item["skill_id"] for item in manifest["skills"] if item["status"] in {"PASS", "PASS_WITH_LIMITATIONS"} and not item.get("artifact_records")]
    if pending or missing:
        manifest["run"]["status"] = "PARTIAL"
        save_manifest(root, manifest)
        raise GateError(f"finalize 未准出: PENDING/非终态={pending}; 缺正式产物={missing}")
    failed = [
        item["skill_id"] for item in manifest["skills"]
        if item["status"] == "FAIL"
    ]
    if failed:
        manifest["run"]["status"] = "FAILED"
        save_manifest(root, manifest)
        append_event(root, {
            "type": "run_finalized",
            "status": "FAILED",
            "failed_skills": failed,
        })
        print(json.dumps({
            "run_root": str(root),
            "status": "FAILED",
            "failed_skills": failed,
        }, ensure_ascii=False))
        return 1
    if not (manifest.get("delivery") or {}).get("summary"):
        manifest["run"]["status"] = "PARTIAL"
        save_manifest(root, manifest)
        raise GateError("finalize 未准出: 缺少已登记的最终总结报告")
    audit_path = root / "evidence/audit/audit-result.json"
    if not audit_path.is_file():
        manifest["run"]["status"] = "PARTIAL"
        save_manifest(root, manifest)
        raise GateError("finalize 未准出: 缺少共享 Audit 结果")
    audit = load_json(audit_path, "Audit 结果")
    if audit.get("status") != "PASS":
        manifest["run"]["status"] = "PARTIAL"
        save_manifest(root, manifest)
        raise GateError(f"finalize 未准出: Audit status={audit.get('status')!r}")
    current_snapshot = analysis_snapshot(manifest, Path(args.registry))
    if any(audit.get(key) != current_snapshot[key]
           for key in ("snapshot_schema_version", "registry_sha256", "snapshot_digest")):
        manifest["run"]["status"] = "PARTIAL"
        save_manifest(root, manifest)
        raise GateError("finalize 未准出: Audit 快照与当前 manifest/registry 不一致，须重新 Audit")
    review_info = _run_review_gate(root, Path(args.registry))
    if (review_info.get("status") != "ok"
            or review_info.get("verdict") != "REVIEW_PASSED"):
        manifest["run"]["status"] = "PARTIAL"
        save_manifest(root, manifest)
        raise GateError(
            "finalize 未准出: 语义评审未完整通过 "
            f"status={review_info.get('status')!r} verdict={review_info.get('verdict')!r}")
    manifest["run"]["status"] = "APPROVED"
    save_manifest(root, manifest)
    append_event(root, {"type": "run_finalized", "status": manifest["run"]["status"]})
    # 生成 HTML 版总结报告（APPROVED 后自动执行，非阻断——失败不影响准出）
    if manifest["run"]["status"] == "APPROVED":
        _generate_summary_html(root, manifest)
    # 健康体检（advisory，非阻断）：把"过了闸门但仍可能坍塌"的执行退化指纹显性化。
    # 永不影响准出与退出码——任何异常都被捕获并记录 doctor_unavailable，绝不静默。
    doctor_info = _run_doctor_advisory(root, Path(args.registry))
    print(json.dumps({"run_root": str(root), "status": manifest["run"]["status"],
                      "doctor_status": doctor_info["status"],
                      "doctor_verdict": doctor_info["verdict"],
                      "review_status": review_info["status"],
                      "review_verdict": review_info.get("verdict")}, ensure_ascii=False))
    return 0


def _generate_summary_html(root: Path, manifest: dict) -> bool:
    """用确定性渲染器生成 HTML 版总结报告（非阻断，失败只打印警告）。

    从 manifest.delivery.summary.path 读取已登记的 markdown 总结，调用确定性渲染器
    full_analysis_html.build_summary_page 转为自包含 HTML（内联设计系统与微交互脚本，
    零外部依赖），写入同目录 .html 文件。

    设计系统（cream paper / terracotta / trust 墨蓝 / serif + 报头 + sticky 导航 +
    编号章节 + 样式化表格 + 滚动显现）已固化为代码：同一份 markdown 永远渲染出同一份
    HTML，无 LLM 参与、无 token 消耗、无输出方差，保证每个 run 展示件品质一致。

    调用方：① 步骤 B2 的独立命令 render-html（register-summary 后立即生成，解耦于
    audit/finalize）；② finalize APPROVED 后的幂等兜底。二者共用本函数，因渲染确定性
    保证两次输出逐字节一致、互不冲突。返回 True 表示已写出 HTML。
    """
    try:
        import importlib.util

        delivery = manifest.get("delivery") or {}
        summary = delivery.get("summary") or {}
        md_rel = summary.get("path", "")
        if not md_rel:
            print("[html-gen] ⚠  manifest 中无 summary.path，跳过 HTML 生成", file=sys.stderr)
            return False
        md_path = root / md_rel
        if not md_path.is_file():
            print(f"[html-gen] ⚠  summary 文件不存在: {md_path}", file=sys.stderr)
            return False
        renderer_path = TOOLS_DIR / "full_analysis_html.py"
        if not renderer_path.is_file():
            print(f"[html-gen] ⚠  渲染器不存在: {renderer_path}，跳过 HTML 生成", file=sys.stderr)
            return False
        spec = importlib.util.spec_from_file_location("full_analysis_html", renderer_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]

        company_info = manifest.get("company") or {}
        run_info = manifest.get("run") or {}
        md_text = md_path.read_text(encoding="utf-8")
        html = module.build_summary_page(
            md_text,
            company=company_info.get("name", ""),
            code=company_info.get("code", ""),
            as_of=run_info.get("as_of", ""),
            skill_count=len(manifest.get("skills", [])),
            status=run_info.get("status", ""),
        )
        html_path = md_path.with_suffix(".html")
        html_path.write_text(html, encoding="utf-8")
        print(f"[html-gen] ✓ {html_path.name} ({len(html.encode())} bytes)", file=sys.stderr)
        append_event(root, {"type": "html_generated", "path": str(html_path.relative_to(root)),
                             "bytes": len(html.encode())})
        # HTML 展示件落盘后立即刷新公司索引页（非阻断）。
        # 覆盖两个调用方：cmd_render_html（步骤 B2）和 cmd_finalize（APPROVED 兜底），
        # 确保"HTML 生成 → 索引自动更新"在所有路径上成立。
        _rebuild_company_index(root)
        return True
    except Exception as exc:  # noqa: BLE001 — HTML 是派生展示件，绝不影响 APPROVED 状态
        print(f"[html-gen] ⚠  HTML 生成失败（不影响 APPROVED 状态）: {exc}", file=sys.stderr)
        return False


def _rebuild_company_index(root: Path) -> bool:
    """重建公司研究索引页 local/Company/index.html（非阻断，失败只打印警告）。

    触发点：_generate_summary_html 成功写出 HTML 展示件后立即调用。
    这覆盖两条路径：① cmd_render_html（步骤 B2 独立命令）；② cmd_finalize
    （APPROVED 后兜底生成）。无论哪条路径产出 HTML，索引都会同步刷新。

    扫描整个公司目录，把新增/更新的公司纳入索引。索引由
    scripts/build_company_index.py 确定性渲染——同一组报告永远产出同一份
    index.html，可安全反复重建。索引是派生展示件，绝不影响 APPROVED 状态。
    """
    try:
        import importlib.util

        scripts_dir = TOOLS_DIR.parent / "scripts"
        builder_path = scripts_dir / "build_company_index.py"
        if not builder_path.is_file():
            print(f"[index-gen] ⚠  索引生成器不存在: {builder_path}，跳过索引重建", file=sys.stderr)
            return False
        company_base = _company_base_from_run_root(root)
        spec = importlib.util.spec_from_file_location("build_company_index", builder_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]

        rows = module.collect(company_base)
        if not rows:
            print("[index-gen] ⚠  未找到任何总结报告，跳过索引重建", file=sys.stderr)
            return False
        html = module.build_index_html(
            rows,
            base_label=str(company_base),
        )
        index_path = company_base / "index.html"
        index_path.write_text(html, encoding="utf-8")
        print(f"[index-gen] ✓ index.html 已更新：{len(rows)} 家公司", file=sys.stderr)
        append_event(root, {"type": "company_index_rebuilt",
                             "companies": len(rows), "bytes": len(html.encode())})
        return True
    except Exception as exc:  # noqa: BLE001 — 索引是派生展示件，绝不影响 APPROVED 状态
        print(f"[index-gen] ⚠  索引重建失败（不影响 APPROVED 状态）: {exc}", file=sys.stderr)
        return False


def cmd_render_html(args: argparse.Namespace) -> int:
    """步骤 B2 独立命令：register-summary 后立即用确定性渲染器生成 HTML 展示件。

    解耦关键：不依赖 audit/review/finalize，只要 summary 已登记即可渲染。
    渲染是纯函数（同一 markdown → 同一 HTML），finalize APPROVED 后的兜底生成
    与本命令产出逐字节一致，幂等无冲突。本命令永不阻断（非 Gate 产物），
    失败仅打印警告并返回 0。
    """
    root = Path(args.run_root).resolve()
    if not manifest_path(root).is_file():
        print(f"[html-gen] ⚠  未找到 manifest，请确认 run-root 正确: {root}", file=sys.stderr)
        return 0
    manifest = load_manifest(root)
    _generate_summary_html(root, manifest)
    return 0


def _run_review_gate(root: Path, registry: Path) -> dict:
    """聚合语义评审结果；缺失、不完整、过期或异常均由 finalize 阻断准出。"""
    try:
        import importlib.util

        review_path = TOOLS_DIR / "full_analysis_review.py"
        if not review_path.is_file():
            return {"status": "not_available"}
        spec = importlib.util.spec_from_file_location("full_analysis_review", review_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]

        review_dir = root / "evidence/review"
        if not review_dir.is_dir() or not list(review_dir.glob("review-result-*.json")):
            return {"status": "not_run"}
        index = load_json(
            review_dir / "review-index.json",
            "语义评审索引",
        )
        required_scope = set(
            module.required_review_scope(load_manifest(root)))
        prepared_scope = set(index.get("scope") or [])
        missing_scope = sorted(required_scope - prepared_scope)
        if missing_scope:
            return {
                "status": "incomplete",
                "missing_scope": missing_scope,
            }
        # 聚合已有评审结果
        summary, _ = module.aggregate(root)
        summary_path = root / "evidence/review/semantic-review-summary.json"
        _atomic_write_json_safe(summary_path, summary)
        print(f"[review] {summary['overall_verdict']}  "
              f"评审 {summary['skills_reviewed']} 个核心单元  "
              f"findings {summary['total_findings']}  "
              f"(high={summary['severity_counts']['high']} "
              f"medium={summary['severity_counts']['medium']} "
              f"low={summary['severity_counts']['low']})", file=sys.stderr)
        if summary["skills_review_required"]:
            print(f"[review] ⚠️  需定向返工: {summary['skills_review_required']}", file=sys.stderr)
        return {"status": "ok", "verdict": summary["overall_verdict"]}
    except Exception as exc:  # noqa: BLE001
        print(f"❌ 语义评审聚合不可用（finalize 将拒绝准出）: {exc}", file=sys.stderr)
        try:
            append_event(root, {"type": "review_unavailable", "reason": str(exc)})
        except Exception:  # noqa: BLE001
            pass
        return {"status": "unavailable"}


def _atomic_write_json_safe(path: Path, data) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _run_doctor_advisory(root: Path, registry: Path) -> dict:
    """调用 full_analysis_doctor 做执行完整性体检，写 evidence/doctor-report.json 并打印。

    非阻断：诊断结果仅作参考，不参与 APPROVE/FAIL 判定。
    可见性：doctor 成功返回 {"status":"ok","verdict":...}；
    失败（DoctorError 等）不静默——写 doctor_unavailable 事件 + stderr，返回 {"status":"unavailable"}。
    """
    try:
        import importlib.util

        doctor_path = TOOLS_DIR / "full_analysis_doctor.py"
        spec = importlib.util.spec_from_file_location("full_analysis_doctor", doctor_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        report = module.diagnose(root, registry)
        # 原子写入报告，避免半成品污染 evidence/
        tmp = root / "evidence/doctor-report.json.tmp"
        tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, root / "evidence/doctor-report.json")
        print(module.render(report), file=sys.stderr)
        return {"status": "ok", "verdict": report["verdict"]}
    except Exception as exc:  # noqa: BLE001 — advisory 永不中断 finalize，但必须留痕
        print(f"⚠️  doctor 不可用（不影响准出）: {exc}", file=sys.stderr)
        try:
            append_event(root, {"type": "doctor_unavailable", "reason": str(exc)})
        except Exception:  # noqa: BLE001 — 事件写入失败也不能中断
            pass
        return {"status": "unavailable", "verdict": None}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="全量公司分析 Gate v2")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--registry", default=DEFAULT_REGISTRY)
    init.add_argument("--repo-root", default=Path.cwd())
    init.add_argument("--company", required=True)
    init.add_argument("--code", required=True)
    init.add_argument("--as-of", required=True)
    init.add_argument("--platform", choices=["workbuddy"], required=True)
    init.add_argument("--run-root")
    ingest = sub.add_parser("ingest-result")
    ingest.add_argument("--run-root", required=True)
    ingest.add_argument("--registry", default=DEFAULT_REGISTRY)
    ingest.add_argument("--result", required=True)
    summary = sub.add_parser("register-summary")
    summary.add_argument("--run-root", required=True)
    summary.add_argument("--registry", default=DEFAULT_REGISTRY)
    summary.add_argument("--summary", required=True)
    finalize = sub.add_parser("finalize")
    finalize.add_argument("--run-root", required=True)
    finalize.add_argument("--registry", default=DEFAULT_REGISTRY)
    render_html = sub.add_parser("render-html")
    render_html.add_argument("--run-root", required=True)
    render_html.add_argument("--registry", default=DEFAULT_REGISTRY)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return {
            "init": cmd_init,
            "ingest-result": cmd_ingest,
            "register-summary": cmd_register_summary,
            "finalize": cmd_finalize,
            "render-html": cmd_render_html,
        }[args.command](args)
    except GateError as exc:
        print(f"❌ {exc}")
        return exc.code


if __name__ == "__main__":
    raise SystemExit(main())
