#!/usr/bin/env python3
"""全量公司分析 Gate v2：只负责确定性登记、晋级与最终收口。

WorkBuddy Runtime 负责调度；Gate 不启动 Agent、不读取报告正文做主观判断，
只验证 Contract/Result Bundle、路径、哈希和状态机。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


TOOLS_DIR = Path(__file__).resolve().parent
DEFAULT_REGISTRY = TOOLS_DIR / "full_analysis_contract.json"
RESULT_SCHEMA_PATH = TOOLS_DIR / "full_analysis_result_schema.json"
MANIFEST_REL = Path("evidence/00-analysis-manifest.json")
RUNTIME_STATE_REL = Path("evidence/runtime-state.json")
EVENTS_REL = Path("evidence/events.jsonl")
PWL_ALLOWLIST = {"tushare_unavailable", "web_bandwidth_degraded", "ephemeral_source"}
RESULT_STATUSES = {"PASS", "PASS_WITH_LIMITATIONS", "NOT_APPLICABLE", "FAIL"}
TERMINAL_STATUSES = {"PASS", "PASS_WITH_LIMITATIONS", "NOT_APPLICABLE"}
TZ_SHANGHAI = timezone(timedelta(hours=8))


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


def atomic_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as out, source.open("rb") as inp:
            shutil.copyfileobj(inp, out)
            out.flush()
            os.fsync(out.fileno())
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
    if len(registry.get("skills", [])) != 20:
        raise GateError("注册表必须恰好包含 20 个 skill", 2)
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


def validate_result_bundle(bundle: dict, run_root: Path, registry: dict) -> None:
    schema = load_json(RESULT_SCHEMA_PATH, "Result Bundle schema")
    required = set(schema["required"])
    missing = sorted(required - set(bundle))
    extra = sorted(set(bundle) - required)
    if missing or extra:
        raise GateError(f"Result Bundle 顶层字段不匹配 missing={missing} extra={extra}")
    if bundle.get("schema_version") != "result-schema/v1":
        raise GateError("Result Bundle schema_version 必须为 result-schema/v1")
    if bundle.get("status") not in RESULT_STATUSES:
        raise GateError(f"Result Bundle status 非法: {bundle.get('status')!r}")
    if bundle["status"] == "FAIL" and not isinstance(bundle.get("error"), dict):
        raise GateError("FAIL Result Bundle 必须提供 error")
    if bundle["status"] in TERMINAL_STATUSES and bundle.get("error") is not None:
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


def build_run_root(repo_root: Path, code: str, company: str) -> Path:
    stamp = datetime.now(TZ_SHANGHAI).strftime("%Y%m%d-%H%M%S")
    short = hashlib.sha256(f"{code}:{company}:{stamp}".encode()).hexdigest()[:6]
    return repo_root / "local" / "company" / f"{code}-{company}" / f"{stamp}-{short}"


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
        "artifacts": [], "facts": [], "sources": [], "calculations": [], "events": [],
    }
    atomic_write_json(root / MANIFEST_REL, manifest)
    atomic_write_json(root / RUNTIME_STATE_REL, {
        "state_version": "runtime-state/v1",
        "run_id": run_id,
        "budget": {
            "normal_target": 40, "stop_dispatch_at": 45, "hard_max": 50,
            "used": 0, "preflight_count": 0, "reserved": 3,
        },
        "concurrency": {"max": 2, "current": 0, "cooldown_until": None},
        "work_units": [{
            "work_unit_id": f"wu-{item['skill_id']}", "skill_id": item["skill_id"],
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


def cmd_ingest(args: argparse.Namespace) -> int:
    root, registry = Path(args.run_root), load_registry(Path(args.registry))
    manifest = load_manifest(root)
    bundle = load_json(Path(args.result), "Result Bundle")
    validate_result_bundle(bundle, root, registry)
    skill = find_skill(registry, bundle["skill_id"])
    records = []
    for record in bundle["artifact_records"]:
        rel = safe_relative(root, record.get("path", ""))
        source = root / rel
        if not source.is_file() or source.is_symlink() or not str(rel).startswith("evidence/attempts/"):
            raise GateError(f"artifact 必须来自 evidence/attempts 且为普通文件: {rel}")
        if source.stat().st_size != record.get("bytes") or sha256_file(source) != record.get("sha256"):
            raise GateError(f"artifact bytes/sha256 与 Result Bundle 不一致: {rel}")
        formal_rel = safe_relative(root, skill["artifact"]["formal_path"])
        formal = root / formal_rel
        atomic_copy(source, formal)
        accepted = {**record, "path": str(formal_rel), "formal": True, "accepted": True}
        records.append(accepted)
    entry = next(item for item in manifest["skills"] if item["skill_id"] == bundle["skill_id"])
    entry.update({"status": bundle["status"], "attempts": [*entry.get("attempts", []), bundle["attempt_id"]],
                  "artifact_records": records, "limitations": bundle["limitations"], "updated_at": now_iso()})
    manifest["artifacts"] = [r for item in manifest["skills"] for r in item.get("artifact_records", [])]
    manifest["facts"].extend(bundle["fact_updates"])
    manifest["sources"].extend(bundle["source_records"])
    manifest["calculations"].extend(bundle["calculation_requests"])
    save_manifest(root, manifest)
    append_event(root, {"type": "result_ingested", "skill_id": bundle["skill_id"], "attempt_id": bundle["attempt_id"], "status": bundle["status"]})
    print(json.dumps({"skill_id": bundle["skill_id"], "status": bundle["status"], "formal_artifacts": records}, ensure_ascii=False))
    return 0


def cmd_finalize(args: argparse.Namespace) -> int:
    root, registry = Path(args.run_root), load_registry(Path(args.registry))
    manifest = load_manifest(root)
    states = {item["status"] for item in manifest["skills"]}
    pending = [item["skill_id"] for item in manifest["skills"] if item["status"] not in TERMINAL_STATUSES]
    missing = [item["skill_id"] for item in manifest["skills"] if item["status"] in {"PASS", "PASS_WITH_LIMITATIONS"} and not item.get("artifact_records")]
    if pending or missing:
        manifest["run"]["status"] = "PARTIAL"
        save_manifest(root, manifest)
        raise GateError(f"finalize 未准出: PENDING/非终态={pending}; 缺正式产物={missing}")
    manifest["run"]["status"] = "APPROVED" if "FAIL" not in states else "FAILED"
    save_manifest(root, manifest)
    append_event(root, {"type": "run_finalized", "status": manifest["run"]["status"]})
    print(json.dumps({"run_root": str(root), "status": manifest["run"]["status"]}, ensure_ascii=False))
    return 0


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
    finalize = sub.add_parser("finalize")
    finalize.add_argument("--run-root", required=True)
    finalize.add_argument("--registry", default=DEFAULT_REGISTRY)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return {"init": cmd_init, "ingest-result": cmd_ingest, "finalize": cmd_finalize}[args.command](args)
    except GateError as exc:
        print(f"❌ {exc}")
        return exc.code


if __name__ == "__main__":
    raise SystemExit(main())
