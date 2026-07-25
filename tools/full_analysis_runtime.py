#!/usr/bin/env python3
"""WorkBuddy 专用 Runtime：调度、租约、重试和预算；不写正式业务 manifest。"""

from __future__ import annotations

import hashlib
import json
import secrets
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows compatibility
    fcntl = None
    import msvcrt


TOOLS_DIR = Path(__file__).resolve().parent
DEFAULT_REGISTRY = TOOLS_DIR / "full_analysis_contract.json"
STATE_REL = Path("evidence/runtime-state.json")
EVENTS_REL = Path("evidence/events.jsonl")
LEASE_MINUTES = 20
BACKOFF_SECONDS = (60, 180)
RATE_LIMIT_COOLDOWN_SECONDS = 600
PARTIAL_REPORT = "PARTIAL_REPORT.md"
SUMMARY_REPORT = "SUMMARY.md"
LOCK_REL = Path("evidence/locks/runtime-state.lock")
TZ_SHANGHAI = timezone(timedelta(hours=8))

# 反凑数刚性指令：随 methodology_text 一并注入执行 Agent，明确"深度优先于字数"。
# 关键口径：payload 里的 min_bytes 是 Gate 的"拒收地板"（挡住空壳式坍塌），
# 绝非写作目标——严禁 Agent 奔着字节数注水。深度由实质校验保证，字数不设目标。
ANTI_PADDING_DIRECTIVE = """
【质量自觉 · 反凑数】
完整性 = 推理链完整 + 关键判断有数据/来源支撑 + 分歧被显式标记。
严禁为凑篇幅复述标题、堆砌无信息表格、用"综上所述"式废话注水。
一份短而精、解决核心问题的分析，远胜一份长而空、只在重述框架的分析。
写透为止，不设字数上限。每个分析小节须有实质论证（数据、对比、推演），
不得仅列标题或一句话带过；多视角 skill 必须显式呈现不同角色的分歧与交锋。

【关于长度 · 必读】
派发包里的 min_bytes 只是拒收地板——用来挡住空壳式坍塌报告，不是你要凑的写作目标。
严禁奔着字节数写：数据写到位、推理写透即止，宁短勿水。
真实有效的短报告永远优于注水的长报告；详略由问题本身决定，不由字数决定。
"""
AUTHORIZATION_DIRECTIVE = """
【全量运行授权信封 · full-analysis-internal/v1】
本次 run 的启动请求已满足下列内部步骤原本要求的重复确认：
1. 只读外部研究与数据查询；
2. 仅在本次 run_root（{run_root}）内写入 attempt、证据与报告；
3. 输出本次公司研究所需的分析结论。
因此，方法论中只针对上述三类动作的独立 STOP/询问点视为已满足，继续执行，
不要在每个 work unit 重复向用户确认。

授权边界不扩张：不得据此执行 push、PR、publish、send、提交表单或任何外部写入；
不得写出 run_root，不得读取或披露 secret、私人账本或敏感个人数据。
遇到这些未授权动作时仍须停止并交回主上下文处理。
"""


class RuntimeErrorState(Exception):
    pass


@contextmanager
def runtime_lock(run_root: Path):
    """Serialize every runtime-state read-modify-write across processes."""
    path = Path(run_root) / LOCK_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        else:  # pragma: no cover - Windows compatibility
            handle.seek(0)
            if handle.read(1) == b"":
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            else:  # pragma: no cover - Windows compatibility
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


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
    with runtime_lock(run_root):
        return _initialize_locked(run_root)


def _initialize_locked(run_root: Path) -> dict:
    state = load_state(run_root)
    if state["budget"].get("preflight_count", 0) == 0:
        state["budget"]["preflight_count"] = 1
        state["budget"]["used"] += 1
        event(run_root, "preflight_completed", budget_used=state["budget"]["used"])
        save_state(run_root, state)
    return state


def _active_units(state: dict) -> list[dict]:
    return [unit for unit in state["work_units"] if unit.get("status") in {"LEASED", "RUNNING"}]


def _sweep_expired_leases(state: dict, run_root: Path) -> int:
    """清理过期租约（LEASED）和卡住 job（RUNNING 过期无心跳）。

    处理三种来源的僵死状态：
    1. job-started 工具断连 → unit 卡在 LEASED
    2. Agent 启动后心跳中断（崩溃/无限循环/网络断开）→ unit 卡在 RUNNING
    3. Agent 正常完成但 submit-result 通知丢失 → orphan result 在磁盘上

    每种状态先检查孤儿产物（Agent 静默完成），再决定 PENDING/RETRY_WAIT/DONE。
    """
    current = now()
    swept = 0
    for unit in state["work_units"]:
        if unit["status"] not in {"LEASED", "RUNNING"}:
            continue
        lease = unit.get("lease") or {}
        expires = parse_time(lease.get("expires_at"))
        if not expires or expires > current:
            continue
        attempt_id = lease.get("attempt_id")
        if attempt_id:
            # 通用孤儿产物恢复：Agent 已完成但 submit-result 通知丢失
            result_path = Path(run_root) / "evidence/attempts" / unit["skill_id"] / attempt_id / "result.json"
            if result_path.is_file():
                try:
                    old_status = unit["status"]
                    _accept_result(
                        run_root, DEFAULT_REGISTRY, result_path,
                        state=state, allow_expired=True, event_kind="orphan_result_recovered",
                    )
                    swept += 1
                    event(run_root, "orphan_result_validated", work_unit_id=unit["work_unit_id"],
                          attempt_id=attempt_id, from_status=old_status)
                    continue
                except (RuntimeErrorState, OSError, json.JSONDecodeError) as exc:
                    event(run_root, "orphan_result_rejected", work_unit_id=unit["work_unit_id"],
                          attempt_id=attempt_id, reason=str(exc))
            unit.setdefault("abandoned_attempts", []).append(attempt_id)
        # P2: RUNNING 过期无产物 → Agent 心跳丢失（崩溃/超时），记录失败
        if unit["status"] == "RUNNING":
            reason = "heartbeat_lost" if unit["lease"].get("started_at") else "job_timeout"
            if unit["attempts"] >= unit["max_attempts"]:
                unit["status"] = "FAILED"
                event(run_root, "job_failed", work_unit_id=unit["work_unit_id"],
                      attempt_id=attempt_id, reason=reason, max_attempts_reached=True)
            else:
                unit["status"] = "RETRY_WAIT"
                delay = BACKOFF_SECONDS[min(unit["attempts"] - 1, 1)]
                unit["next_retry_at"] = iso(current + timedelta(seconds=delay))
                event(run_root, "job_timed_out", work_unit_id=unit["work_unit_id"],
                      attempt_id=attempt_id, reason=reason, next_retry_at=unit["next_retry_at"])
        else:  # LEASED
            unit["status"] = "PENDING"
        unit["lease"] = None
        swept += 1
    if swept:
        save_state(run_root, state)
        event(run_root, "expired_or_stuck_swept", swept=swept)
    return swept


def _load_registry() -> dict:
    try:
        return json.loads(DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"skills": []}


def next_work(run_root: Path) -> dict:
    with runtime_lock(run_root):
        return _next_work_locked(run_root)


def _next_work_locked(run_root: Path) -> dict:
    state = load_state(run_root)
    budget = state["budget"]
    if budget["used"] >= budget["hard_max"]:
        render_partial(run_root, "JOB_LIMIT")
        raise RuntimeErrorState(f"硬预算已达 {budget['hard_max']}，停止新派发")
    cooldown = parse_time(state["concurrency"].get("cooldown_until"))
    if cooldown and cooldown > now():
        return {"status": "NO_WORK", "reason": "RATE_LIMIT_COOLDOWN", "cooldown_until": iso(cooldown)}
    # P1+P2: 清理过期租约和卡住的 RUNNING job（心跳丢失/工具断连）
    _sweep_expired_leases(state, run_root)
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
    registry = _load_registry()
    skill = next(
        (s for s in registry.get("skills", [])
         if s.get("skill_id") == unit["skill_id"]),
        None,
    )
    authorization = (
        state.get("authorization")
        or registry.get("authorization_profile")
        or {}
    )
    methodology_text = ""
    if skill:
        spec = skill.get("spec_source")
        if spec:
            spec_path = TOOLS_DIR.parent / spec
            if spec_path.is_file():
                methodology_text = (
                    AUTHORIZATION_DIRECTIVE.format(run_root=Path(run_root))
                    + spec_path.read_text(encoding="utf-8")
                    + ANTI_PADDING_DIRECTIVE
                )
    roles = skill.get("roles", {}) if skill else {}
    return {
        "status": "LEASED",
        "work_unit_id": unit["work_unit_id"],
        "skill_id": unit["skill_id"],
        "methodology_path": skill.get("spec_source") if skill else None,
        "methodology_text": methodology_text,
        # 校准路线（防凑数）：不把 min_bytes 具体数字暴露给执行 Agent——它是 Gate 的拒收
        # 地板（挡空壳式坍塌），不是写作目标；奔字数写是凑数的根源。保留 key（值为 None）
        # 以兼容下游派发脚本，深度由 _substance_errors 实质校验保证。
        "min_bytes": None,
        "length_policy": (
            "min_bytes 仅是拒收地板，不是写作目标；数据写到位、推理写透即止，"
            "宁短勿水。深度由实质校验保证，详略由问题本身决定。"
        ),
        "skill_type": skill.get("skill_type") if skill else None,
        "min_dissent_points": skill.get("min_dissent_points") if skill else None,
        "min_substantive_sections": skill.get("min_substantive_sections") if skill else None,
        "sections": skill.get("sections", []) if skill else [],
        "roles": roles,
        "fanout_required": bool(roles.get("mode") == "independent_then_integrator"),
        "authorization": authorization,
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
    """注册 Agent job 启动，将 work_unit 从 LEASED 切换到 RUNNING。

    P1 幂等：若同一 attempt_id 已在前一次调用中切换为 RUNNING（但输出因工具断连丢失），
    直接返回已有状态和 attempt_dir，不重复扣预算、不抛异常。调用方可安全重试。
    """
    with runtime_lock(run_root):
        return _job_started_locked(
            run_root, work_unit_id, attempt_id, nonce, agent_job_id)


def _job_started_locked(
    run_root: Path,
    work_unit_id: str,
    attempt_id: str,
    nonce: str,
    agent_job_id: str,
) -> dict:
    state = load_state(run_root)
    if state["budget"]["used"] >= state["budget"]["hard_max"]:
        raise RuntimeErrorState(
            f"硬预算已达 {state['budget']['hard_max']}，拒绝启动 Agent job")
    unit = _unit(state, work_unit_id)
    # P1: 幂等检测 — 已 RUNNING 且 attempt_id 匹配 → 直接返回
    if unit["status"] == "RUNNING":
        lease = unit.get("lease") or {}
        if lease.get("attempt_id") == attempt_id:
            attempt_dir = Path(run_root) / "evidence/attempts" / unit["skill_id"] / attempt_id
            event(run_root, "job_started_idempotent", work_unit_id=work_unit_id, attempt_id=attempt_id)
            return {"status": "RUNNING", "attempt_dir": str(attempt_dir),
                    "budget_used": state["budget"]["used"], "idempotent": True}
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
    with runtime_lock(run_root):
        return _heartbeat_locked(run_root, work_unit_id, attempt_id, nonce)


def _heartbeat_locked(
    run_root: Path, work_unit_id: str, attempt_id: str, nonce: str,
) -> dict:
    state = load_state(run_root)
    unit = _unit(state, work_unit_id)
    _check_lease(unit, attempt_id, nonce)
    unit["lease"]["expires_at"] = iso(now() + timedelta(minutes=LEASE_MINUTES))
    save_state(run_root, state)
    event(run_root, "heartbeat", work_unit_id=work_unit_id, attempt_id=attempt_id)
    return {"status": "HEARTBEAT", "expires_at": unit["lease"]["expires_at"]}


def record_failure(run_root: Path, work_unit_id: str, attempt_id: str, reason: str) -> dict:
    with runtime_lock(run_root):
        return _record_failure_locked(
            run_root, work_unit_id, attempt_id, reason)


def _record_failure_locked(
    run_root: Path, work_unit_id: str, attempt_id: str, reason: str,
) -> dict:
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


def _validate_result_lease(state: dict, bundle: dict, *, allow_expired: bool = False) -> dict:
    """在 Gate 产生任何副作用前，将 Result Bundle 绑定到当前活动租约。"""
    if bundle.get("run_id") != state.get("run_id"):
        raise RuntimeErrorState("Result Bundle run_id 与 Runtime 不匹配")
    work_unit_id = bundle.get("work_unit_id")
    if not isinstance(work_unit_id, str):
        raise RuntimeErrorState("Result Bundle work_unit_id 缺失")
    unit = _unit(state, work_unit_id)
    if unit.get("skill_id") != bundle.get("skill_id"):
        raise RuntimeErrorState("Result Bundle skill_id 与 work unit 不匹配")
    if unit.get("status") not in {"RUNNING", "LEASED"}:
        raise RuntimeErrorState(f"submit-result 状态非法: {unit.get('status')}")
    lease = unit.get("lease") or {}
    expected = {
        "attempt_id": lease.get("attempt_id"),
        "lease_nonce": lease.get("lease_nonce"),
        "agent_job_id": lease.get("agent_job_id"),
    }
    actual = {key: bundle.get(key) for key in expected}
    if actual != expected or not all(expected.values()):
        raise RuntimeErrorState("Result Bundle 与当前租约身份不匹配")
    expires = parse_time(lease.get("expires_at"))
    if not allow_expired and expires and expires <= now():
        raise RuntimeErrorState("Result Bundle 对应租约已过期")
    return unit


def _accept_result(
    run_root: Path,
    registry: Path,
    result: Path,
    *,
    state: dict | None = None,
    allow_expired: bool = False,
    event_kind: str = "result_submitted",
) -> dict:
    bundle = json.loads(result.read_text(encoding="utf-8"))
    current_state = state if state is not None else load_state(run_root)
    unit = _validate_result_lease(current_state, bundle, allow_expired=allow_expired)
    gate = Path(__file__).resolve().parent / "full_analysis_gate.py"
    completed = subprocess.run([sys.executable, str(gate), "ingest-result", "--run-root", str(run_root), "--registry", str(registry), "--result", str(result)], capture_output=True, text=True)
    if completed.returncode:
        raise RuntimeErrorState(completed.stdout + completed.stderr)
    unit["status"] = "DONE" if bundle["status"] in {"PASS", "PASS_WITH_LIMITATIONS", "NOT_APPLICABLE"} else "FAILED"
    unit["lease"] = None
    current_state["concurrency"]["current"] = max(
        0, current_state["concurrency"].get("current", 0) - 1)
    save_state(run_root, current_state)
    event(run_root, event_kind, work_unit_id=unit["work_unit_id"],
          attempt_id=bundle["attempt_id"], status=unit["status"])
    return {"status": unit["status"], "gate": completed.stdout.strip()}


def submit_result(run_root: Path, registry: Path, result: Path) -> dict:
    with runtime_lock(run_root):
        return _accept_result(run_root, registry, result)


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
    with runtime_lock(run_root):
        return _resume_locked(run_root, now_value=now_value)


def _resume_locked(
    run_root: Path, now_value: datetime | None = None,
) -> dict:
    state = load_state(run_root)
    current = now_value or now()
    started_at = parse_time(state.get("run_started_at"))
    if started_at and current - started_at > timedelta(hours=24):
        return {"status": "NEW_RUN_REQUIRED", "reason": "RUN_OLDER_THAN_24_HOURS"}
    abandoned = []
    recovered = []
    for unit in state["work_units"]:
        if unit["status"] in {"LEASED", "RUNNING"}:
            lease = unit.get("lease") or {}
            old_attempt = lease.get("attempt_id")
            if old_attempt:
                result_path = (
                    Path(run_root) / "evidence/attempts" /
                    unit["skill_id"] / old_attempt / "result.json"
                )
                if result_path.is_file():
                    try:
                        _accept_result(
                            run_root,
                            DEFAULT_REGISTRY,
                            result_path,
                            state=state,
                            allow_expired=True,
                            event_kind="orphan_result_recovered_on_resume",
                        )
                        recovered.append(unit["work_unit_id"])
                        event(
                            run_root,
                            "orphan_result_validated_on_resume",
                            work_unit_id=unit["work_unit_id"],
                            attempt_id=old_attempt,
                        )
                        continue
                    except (
                        RuntimeErrorState, OSError, json.JSONDecodeError,
                    ) as exc:
                        event(
                            run_root,
                            "orphan_result_rejected_on_resume",
                            work_unit_id=unit["work_unit_id"],
                            attempt_id=old_attempt,
                            reason=str(exc),
                        )
            abandoned.append(unit["work_unit_id"])
            if old_attempt:
                unit.setdefault("abandoned_attempts", []).append(old_attempt)
            unit["status"] = "PENDING"
            unit["lease"] = None
    state["concurrency"]["current"] = 0
    save_state(run_root, state)
    event(
        run_root, "runtime_resumed",
        abandoned=abandoned, recovered=recovered,
    )
    return {
        "status": "RESUMED",
        "abandoned": abandoned,
        "recovered": recovered,
    }
