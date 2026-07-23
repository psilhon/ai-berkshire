#!/usr/bin/env python3
"""WorkBuddy 专用 Runtime：调度、租约、重试和预算；不写正式业务 manifest。"""

from __future__ import annotations

import hashlib
import json
import secrets
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


TOOLS_DIR = Path(__file__).resolve().parent
DEFAULT_REGISTRY = TOOLS_DIR / "full_analysis_contract.json"
STATE_REL = Path("evidence/runtime-state.json")
EVENTS_REL = Path("evidence/events.jsonl")
LEASE_MINUTES = 20
BACKOFF_SECONDS = (60, 180)
RATE_LIMIT_COOLDOWN_SECONDS = 600
PARTIAL_REPORT = "PARTIAL_REPORT.md"
SUMMARY_REPORT = "SUMMARY.md"
TZ_SHANGHAI = timezone(timedelta(hours=8))

# 反凑数刚性指令：随 methodology_text 一并注入执行 Agent，明确"深度优先于字数"
ANTI_PADDING_DIRECTIVE = """
【质量自觉 · 反凑数】
完整性 = 推理链完整 + 关键判断有数据/来源支撑 + 分歧被显式标记。
严禁为凑篇幅复述标题、堆砌无信息表格、用"综上所述"式废话注水。
一份短而精、解决核心问题的分析，远胜一份长而空、只在重述框架的分析。
写透为止，不设字数上限。每个分析小节须有实质论证（数据、对比、推演），
不得仅列标题或一句话带过；多视角 skill 必须显式呈现不同角色的分歧与交锋。
"""


class RuntimeErrorState(Exception):
    pass


def now() -> datetime:
    return datetime.now(TZ_SHANGHAI)


def iso(value: datetime) -> str:
    return value.isoformat()


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def atomic_json(path: Path, value: object) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def load_state(run_root: Path) -> dict:
    path = Path(run_root) / STATE_REL
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeErrorState(f"runtime-state 不可读: {path}: {exc}")
    if state.get("state_version") != "runtime-state/v1":
        raise RuntimeErrorState("runtime-state 版本不匹配")
    return state


def save_state(run_root: Path, state: dict) -> None:
    atomic_json(Path(run_root) / STATE_REL, state)


def event(run_root: Path, kind: str, **payload: object) -> None:
    path = Path(run_root) / EVENTS_REL
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"event_at": iso(now()), "type": kind, **payload}, ensure_ascii=False) + "\n")


def initialize(run_root: Path) -> dict:
    state = load_state(run_root)
    if state["budget"].get("preflight_count", 0) == 0:
        state["budget"]["preflight_count"] = 1
        state["budget"]["used"] += 1
        event(run_root, "preflight_completed", budget_used=state["budget"]["used"])
        save_state(run_root, state)
    return state


def _active_units(state: dict) -> list[dict]:
    return [unit for unit in state["work_units"] if unit.get("status") in {"LEASED", "RUNNING"}]


def _load_registry() -> dict:
    try:
        return json.loads(DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"skills": []}


def next_work(run_root: Path) -> dict:
    state = load_state(run_root)
    budget = state["budget"]
    if budget["used"] >= budget["hard_max"]:
        render_partial(run_root, "JOB_LIMIT")
        raise RuntimeErrorState(f"硬预算已达 {budget['hard_max']}，停止新派发")
    cooldown = parse_time(state["concurrency"].get("cooldown_until"))
    if cooldown and cooldown > now():
        return {"status": "NO_WORK", "reason": "RATE_LIMIT_COOLDOWN", "cooldown_until": iso(cooldown)}
    active = _active_units(state)
    if len(active) >= state["concurrency"]["max"]:
        return {"status": "NO_WORK", "reason": "CONCURRENCY_LIMIT"}
    candidates = []
    current = now()
    for unit in state["work_units"]:
        if unit["status"] not in {"PENDING", "RETRY_WAIT"}:
            continue
        retry_at = parse_time(unit.get("next_retry_at"))
        if retry_at and retry_at > current:
            continue
        if budget["used"] >= budget["stop_dispatch_at"] and not unit.get("core", True):
            continue
        candidates.append(unit)
    if not candidates:
        return {"status": "NO_WORK", "reason": "QUEUE_EMPTY"}
    unit = candidates[0]
    attempt = unit["attempts"] + 1
    material = f"{state['run_id']}:{unit['work_unit_id']}:{attempt}:{secrets.token_hex(4)}"
    lease = {
        "attempt_id": f"attempt-{hashlib.sha256(material.encode()).hexdigest()[:12]}",
        "lease_nonce": secrets.token_hex(16),
        "leased_at": iso(current),
        "expires_at": iso(current + timedelta(minutes=LEASE_MINUTES)),
    }
    unit.update({"status": "LEASED", "attempts": attempt, "lease": lease})
    save_state(run_root, state)
    event(run_root, "work_leased", work_unit_id=unit["work_unit_id"], attempt_id=lease["attempt_id"])
    # 注入 skill 方法论与扇出要求，避免执行 Agent 退化为单遍写大纲（根因修复）
    skill = next((s for s in _load_registry().get("skills", []) if s.get("skill_id") == unit["skill_id"]), None)
    methodology_text = ""
    if skill:
        spec = skill.get("spec_source")
        if spec:
            spec_path = TOOLS_DIR.parent / spec
            if spec_path.is_file():
                methodology_text = spec_path.read_text(encoding="utf-8") + ANTI_PADDING_DIRECTIVE
    roles = skill.get("roles", {}) if skill else {}
    return {
        "status": "LEASED",
        "work_unit_id": unit["work_unit_id"],
        "skill_id": unit["skill_id"],
        "methodology_path": skill.get("spec_source") if skill else None,
        "methodology_text": methodology_text,
        "min_bytes": skill["artifact"]["min_bytes"] if skill else None,
        "skill_type": skill.get("skill_type") if skill else None,
        "min_dissent_points": skill.get("min_dissent_points") if skill else None,
        "min_substantive_sections": skill.get("min_substantive_sections") if skill else None,
        "sections": skill.get("sections", []) if skill else [],
        "roles": roles,
        "fanout_required": bool(roles.get("mode") == "independent_then_integrator"),
        **lease,
    }


def _unit(state: dict, work_unit_id: str) -> dict:
    for unit in state["work_units"]:
        if unit["work_unit_id"] == work_unit_id:
            return unit
    raise RuntimeErrorState(f"未知 work_unit_id: {work_unit_id}")


def _check_lease(unit: dict, attempt_id: str, nonce: str) -> None:
    lease = unit.get("lease") or {}
    if lease.get("attempt_id") != attempt_id or lease.get("lease_nonce") != nonce:
        raise RuntimeErrorState("租约不匹配")
    expires = parse_time(lease.get("expires_at"))
    if expires and expires <= now():
        raise RuntimeErrorState("租约已过期")


def job_started(run_root: Path, work_unit_id: str, attempt_id: str, nonce: str, agent_job_id: str) -> dict:
    state = load_state(run_root)
    if state["budget"]["used"] >= state["budget"]["hard_max"]:
        raise RuntimeErrorState("硬预算已达 50，拒绝启动 Agent job")
    unit = _unit(state, work_unit_id)
    _check_lease(unit, attempt_id, nonce)
    if unit["status"] != "LEASED":
        raise RuntimeErrorState(f"work unit 不是 LEASED: {unit['status']}")
    unit["status"] = "RUNNING"
    unit["lease"]["agent_job_id"] = agent_job_id
    unit["lease"]["started_at"] = iso(now())
    state["budget"]["used"] += 1
    save_state(run_root, state)
    attempt_dir = Path(run_root) / "evidence/attempts" / unit["skill_id"] / attempt_id
    attempt_dir.mkdir(parents=True, exist_ok=True)
    event(run_root, "job_started", work_unit_id=work_unit_id, attempt_id=attempt_id, agent_job_id=agent_job_id, budget_used=state["budget"]["used"])
    return {"status": "RUNNING", "attempt_dir": str(attempt_dir), "budget_used": state["budget"]["used"]}


def heartbeat(run_root: Path, work_unit_id: str, attempt_id: str, nonce: str) -> dict:
    state = load_state(run_root)
    unit = _unit(state, work_unit_id)
    _check_lease(unit, attempt_id, nonce)
    unit["lease"]["expires_at"] = iso(now() + timedelta(minutes=LEASE_MINUTES))
    save_state(run_root, state)
    event(run_root, "heartbeat", work_unit_id=work_unit_id, attempt_id=attempt_id)
    return {"status": "HEARTBEAT", "expires_at": unit["lease"]["expires_at"]}


def record_failure(run_root: Path, work_unit_id: str, attempt_id: str, reason: str) -> dict:
    state = load_state(run_root)
    unit = _unit(state, work_unit_id)
    lease = unit.get("lease") or {}
    if lease.get("attempt_id") != attempt_id or unit["status"] not in {"LEASED", "RUNNING"}:
        raise RuntimeErrorState("失败记录与当前租约不匹配")
    unit["lease"] = None
    state["concurrency"]["current"] = max(0, state["concurrency"].get("current", 0) - 1)
    if reason == "rate_limit":
        state["concurrency"]["max"] = 1
        state["concurrency"]["cooldown_until"] = iso(now() + timedelta(seconds=RATE_LIMIT_COOLDOWN_SECONDS))
    if unit["attempts"] >= unit["max_attempts"]:
        unit["status"] = "FAILED"
    else:
        unit["status"] = "RETRY_WAIT"
        delay = RATE_LIMIT_COOLDOWN_SECONDS if reason == "rate_limit" else BACKOFF_SECONDS[min(unit["attempts"] - 1, 1)]
        unit["next_retry_at"] = iso(now() + timedelta(seconds=delay))
    save_state(run_root, state)
    event(run_root, "job_failed", work_unit_id=work_unit_id, attempt_id=attempt_id, reason=reason, next_status=unit["status"])
    return {"status": unit["status"], "attempts": unit["attempts"], "next_retry_at": unit.get("next_retry_at")}


def submit_result(run_root: Path, registry: Path, result: Path) -> dict:
    gate = Path(__file__).resolve().parent / "full_analysis_gate.py"
    completed = subprocess.run([sys.executable, str(gate), "ingest-result", "--run-root", str(run_root), "--registry", str(registry), "--result", str(result)], capture_output=True, text=True)
    if completed.returncode:
        raise RuntimeErrorState(completed.stdout + completed.stderr)
    bundle = json.loads(result.read_text(encoding="utf-8"))
    state = load_state(run_root)
    unit = _unit(state, f"wu-{bundle['skill_id']}")
    if unit["status"] not in {"RUNNING", "LEASED"}:
        raise RuntimeErrorState(f"submit-result 状态非法: {unit['status']}")
    unit["status"] = "DONE" if bundle["status"] in {"PASS", "PASS_WITH_LIMITATIONS", "NOT_APPLICABLE"} else "FAILED"
    unit["lease"] = None
    save_state(run_root, state)
    event(run_root, "result_submitted", work_unit_id=unit["work_unit_id"], status=unit["status"])
    return {"status": unit["status"], "gate": completed.stdout.strip()}


def render_partial(run_root: Path, reason: str) -> None:
    root = Path(run_root)
    state = load_state(root)
    pending = [u["skill_id"] for u in state["work_units"] if u["status"] not in {"DONE", "FAILED"}]
    (root / PARTIAL_REPORT).write_text(
        "# PARTIAL_REPORT\n\n未准出；本次运行不产生投资结论。\n\n"
        f"停止原因：`{reason}`\n\n未完成工作单元：{', '.join(pending) or '无'}\n",
        encoding="utf-8",
    )
    (root / SUMMARY_REPORT).write_text(
        "# SUMMARY\n\n状态：PARTIAL（未准出）\n\n"
        f"停止原因：`{reason}`\n\n已使用 Agent job：{state['budget']['used']}\n",
        encoding="utf-8",
    )
    event(root, "partial_rendered", reason=reason)


def resume(run_root: Path, now_value: datetime | None = None) -> dict:
    state = load_state(run_root)
    current = now_value or now()
    started_at = parse_time(state.get("run_started_at"))
    if started_at and current - started_at > timedelta(hours=24):
        return {"status": "NEW_RUN_REQUIRED", "reason": "RUN_OLDER_THAN_24_HOURS"}
    abandoned = []
    for unit in state["work_units"]:
        if unit["status"] in {"LEASED", "RUNNING"}:
            abandoned.append(unit["work_unit_id"])
            old_attempt = (unit.get("lease") or {}).get("attempt_id")
            if old_attempt:
                unit.setdefault("abandoned_attempts", []).append(old_attempt)
            unit["status"] = "PENDING"
            unit["lease"] = None
    state["concurrency"]["current"] = 0
    save_state(run_root, state)
    event(run_root, "runtime_resumed", abandoned=abandoned)
    return {"status": "RESUMED", "abandoned": abandoned}
