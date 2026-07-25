#!/usr/bin/env python3
"""全量公司分析 Run 健康体检器（execution-integrity doctor）。

定位：**顾问式非阻断诊断**，用于捕捉"过了闸门但仍可能坍塌"的执行退化指纹。
背景：min_bytes 分级下限 + 实质校验是硬闸门（保证不会无声坍塌到 3KB 还 APPROVED）；
但"过下限≠同等深度"，深度取决于执行架构（是否真派子 Agent + 外部取数）。
本工具把坍塌指纹显性化，让退化永远可见、可复核，而不是被静默放行。

为什么不做硬闸门（实测标定，勿回退）：
- canary（tests/test_full_analysis_e2e）全程 0 heartbeat 且产物仅压到刚过下限；
- 合法真 run（中际旭创）也只 12/19 单元发 heartbeat（覆盖率 0.63，尾部缺口 4）；
  → heartbeat 与"余量"都无法作为硬闸门，否则误伤合法 run。
因此本工具默认 advisory（退出码恒 0）；仅 --strict 时 WARN 抬升为退出码 3，供人工/CI 选用。

指纹清单：
1. 分布坍塌：适用分析单元贴线（margin<1.15×下限）占比过高；
2. 事件完整性：events.jsonl 缺失/损坏 → 指纹不可用，显式 WARN（不允许静默 PASS）；
3. 心跳覆盖：全程零心跳 / 覆盖率偏低 / 尾部连续缺口（后半程疑似主上下文直写）；
4. 深度分化不足：按标准化 margin（bytes/floor）计算的变异系数过低且贴线单元多。

用法：
    python3 tools/full_analysis_doctor.py --run-root <RR> [--registry ...] [--json] [--strict]
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from pathlib import Path

DEFAULT_REGISTRY = str(Path(__file__).resolve().parent / "full_analysis_contract.json")

# 余量阈值：产物字节 / 下限 < 该比例 → 判定"贴线"（深度存疑，非硬失败）
THIN_MARGIN_RATIO = 1.15
# 全 run 贴线单元占比 ≥ 该值 → 判定分布坍塌风险
CLIFF_THIN_SHARE = 0.40
# 数据类 skill（下限本就低，不计入深度贴线/分化统计）
DATA_SKILLS = {"ashare-data", "financial-data"}
# 轻量内容类（下限极低，天然贴线，豁免贴线告警）；当前为空。
LIGHT_SKILLS = set()
# 指纹4：深度分化不足的判定阈值（基于标准化 margin 的变异系数）
CV_FLOOR = 0.25                    # margin 变异系数低于该值 → 离散度过低
DIVERGENCE_THIN_SHARE = 0.30       # 且贴线单元占比 ≥ 该值 → 批量骨架化嫌疑
# 指纹3：心跳覆盖判定阈值（按合法真 run 标定：旭创覆盖率 0.63 / 尾部缺口 4 须保持 PASS）
COVERAGE_FLOOR = 0.50              # 覆盖率低于该值 → 执行完整性存疑
TAIL_GAP_THRESHOLD = 8             # 尾部连续无心跳单元 ≥ 该值 → 后半程疑似主上下文直写
# 参与"贴线/分化"统计的状态：只有真正产出报告的单元
REPORTABLE_STATUSES = {"PASS", "PASS_WITH_LIMITATIONS"}
# 终态（用于区分未完成的 PENDING/RUNNING/FAILED）
TERMINAL_STATUSES = {"PASS", "PASS_WITH_LIMITATIONS", "NOT_APPLICABLE"}


class DoctorError(Exception):
    """doctor 专用异常（Exception 子类，确保被 finalize 的 `except Exception` 捕获，不致 SystemExit 逃逸）。"""


def _load(path: Path, label: str) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise DoctorError(f"{label} 不可读或非法 JSON: {path}: {exc}")
    if not isinstance(data, dict):
        raise DoctorError(f"{label} 顶层必须为对象: {path}")
    return data


def _atomic_write_json(path: Path, data) -> None:
    """临时文件 + os.replace 原子写入，避免半成品报告污染 evidence/。"""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _work_unit_to_skill(wid: str) -> str:
    return wid[3:] if isinstance(wid, str) and wid.startswith("wu-") else (wid or "")


def diagnose(run_root: Path, registry_path: Path) -> dict:
    registry = _load(registry_path, "注册表")
    floors = {s["skill_id"]: (s.get("artifact") or {}).get("min_bytes") or 0
              for s in registry.get("skills", [])}
    manifest = _load(run_root / "evidence/00-analysis-manifest.json", "manifest")

    # ---- 各单元产物字节数与适用性（只对真正产出报告的单元做深度统计）----
    units = []
    for item in manifest.get("skills", []):
        sid = item["skill_id"]
        status = item.get("status")
        recs = item.get("artifact_records") or []
        size = -1
        accepted_attempt_id = None
        if recs:
            fp = run_root / recs[0].get("path", "")
            size = fp.stat().st_size if fp.exists() else -1
            accepted_attempt_id = recs[0].get("attempt_id")
        if accepted_attempt_id is None and item.get("attempts"):
            accepted_attempt_id = item["attempts"][-1]
        floor = floors.get(sid, 0)
        applicable = status in REPORTABLE_STATUSES and size > 0
        margin = (size / floor) if (applicable and floor > 0) else None
        thin = bool(applicable and floor > 0 and margin is not None
                    and margin < THIN_MARGIN_RATIO and sid not in LIGHT_SKILLS)
        units.append({
            "skill_id": sid, "status": status, "bytes": size, "floor": floor,
            "accepted_attempt_id": accepted_attempt_id,
            "margin": round(margin, 3) if margin is not None else None,
            "applicable": applicable, "thin": thin,
        })

    # ---- 状态分桶（N/A、未完成、缺产物分别报告，不计入贴线统计）----
    na_units = [u["skill_id"] for u in units if u["status"] == "NOT_APPLICABLE"]
    pending_units = [u["skill_id"] for u in units if u["status"] not in TERMINAL_STATUSES]
    missing_artifact = [u["skill_id"] for u in units
                        if u["status"] in REPORTABLE_STATUSES and u["bytes"] <= 0]

    applicable_units = [u for u in units if u["applicable"]]
    analytic_units = [u for u in applicable_units
                      if u["skill_id"] not in (DATA_SKILLS | LIGHT_SKILLS)]
    thin_analytic = [u for u in analytic_units if u["thin"]]
    thin_share = (len(thin_analytic) / len(analytic_units)) if analytic_units else 0.0

    # ---- 事件完整性 + 心跳覆盖（execution-integrity 指纹）----
    ev_path = run_root / "evidence/events.jsonl"
    hb_total = 0
    job_started = 0
    total_lines = 0
    bad_lines = 0
    # 按 (skill_id, attempt_id) 关联心跳：只认最终被接受 attempt 的心跳，
    # 避免旧失败 attempt 的心跳替零心跳的 accepted attempt "背书"
    hb_pairs: set = set()
    hb_skill_any: set = set()
    if not ev_path.exists():
        events_status = "missing"
    else:
        for line in ev_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            total_lines += 1
            try:
                e = json.loads(line)
            except Exception:  # noqa: BLE001
                bad_lines += 1
                continue
            t = e.get("type")
            if t == "heartbeat":
                hb_total += 1
                sid = _work_unit_to_skill(e.get("work_unit_id") or e.get("skill_id"))
                hb_pairs.add((sid, e.get("attempt_id")))
                hb_skill_any.add(sid)
            elif t in ("job_started", "job-started"):
                job_started += 1
        events_status = "ok"
        if total_lines > 0 and bad_lines == total_lines:
            events_status = "corrupt"
        elif bad_lines > 0:
            events_status = "partial"

    # 覆盖率/尾部缺口按 manifest 顺序的"已执行单元"（REPORTABLE）计算；
    # 心跳归属优先匹配 (skill, accepted_attempt_id)，accepted attempt 无记录时回退到 skill 级
    ran_unit_list = [u for u in units if u["status"] in REPORTABLE_STATUSES]
    ran_units = [u["skill_id"] for u in ran_unit_list]

    def _covered(u) -> bool:
        aid = u.get("accepted_attempt_id")
        if aid is not None:
            return (u["skill_id"], aid) in hb_pairs
        return u["skill_id"] in hb_skill_any

    covered_set = {u["skill_id"] for u in ran_unit_list if _covered(u)}
    hb_covered = [s for s in ran_units if s in covered_set]
    coverage = (len(hb_covered) / len(ran_units)) if ran_units else 0.0
    tail_gap = 0
    for s in reversed(ran_units):
        if s in covered_set:
            break
        tail_gap += 1

    sizes = [u["bytes"] for u in units if u["bytes"] > 0]
    total_bytes = sum(sizes)
    thin_units = [u["skill_id"] for u in analytic_units if u["thin"]]

    # ---- 告警 ----
    warnings = []
    # 指纹 1：分布坍塌
    if thin_share >= CLIFF_THIN_SHARE:
        warnings.append(
            f"分布坍塌风险：{len(thin_analytic)}/{len(analytic_units)} 个适用分析单元贴线"
            f"（<{THIN_MARGIN_RATIO:.2f}×下限）；深度可能仅够过闸，建议复核")
    # 指纹 2：事件完整性缺失/损坏/部分损坏 → 指纹不可用或不可信，显式 WARN（不允许静默 PASS）
    if events_status in ("missing", "corrupt"):
        label = "缺失" if events_status == "missing" else "损坏"
        warnings.append(
            f"事件日志{label}：heartbeat/job 指纹不可用，执行完整性无法核验；"
            f"不能据此判定为健康，请人工核查或重跑")
    elif events_status == "partial":
        warnings.append(
            f"事件日志部分损坏：{bad_lines}/{total_lines} 行无法解析，"
            f"心跳/覆盖指纹不完整、可能低估真实退化；不能据此判定为健康，请人工核查")
    # 指纹 3：心跳覆盖（仅事件日志可信时评估；missing/corrupt 已由指纹2告警，不再叠加）
    if events_status not in ("missing", "corrupt"):
        if len(ran_units) >= 5:
            if coverage == 0:
                warnings.append(
                    f"全程零 heartbeat：{len(ran_units)} 个已执行单元无一心跳，"
                    f"疑似主上下文直写而非真子 Agent；若经历过会话压缩务必复核 10 号后单元")
            elif coverage < COVERAGE_FLOOR:
                warnings.append(
                    f"心跳覆盖率偏低：{coverage:.0%}（{len(hb_covered)}/{len(ran_units)}），"
                    f"部分单元缺乏真子 Agent 执行证据，建议复核")
        if tail_gap >= TAIL_GAP_THRESHOLD:
            warnings.append(
                f"尾部连续 {tail_gap} 个单元无心跳：疑似后半程（会话压缩后）改为主上下文直写，"
                f"务必逐一核查这些单元的取数与深度")
    # 指纹 4：深度分化不足（按标准化 margin 的变异系数，避免 skill 下限差异造成的假性高离散）
    margins = [u["margin"] for u in analytic_units if u["margin"] is not None]
    if len(margins) >= 10:
        mean = statistics.mean(margins)
        cv = (statistics.pstdev(margins) / mean) if mean else 0
        if cv < CV_FLOOR and thin_share >= DIVERGENCE_THIN_SHARE:
            warnings.append(
                f"深度分化不足：标准化 margin 变异系数 {cv:.2f} 偏低且贴线单元多，疑似批量骨架化")

    verdict = "WARN" if warnings else "PASS"
    return {
        "run_root": str(run_root),
        "verdict": verdict,
        "events_status": events_status,
        "total_bytes": total_bytes,
        "total_kb": round(total_bytes / 1024, 1),
        "unit_count": len(units),
        "applicable_count": len(applicable_units),
        "na_units": na_units,
        "pending_units": pending_units,
        "missing_artifact": missing_artifact,
        "thin_units": thin_units,
        "thin_share_analytic": round(thin_share, 3),
        "heartbeat_total": hb_total,
        "heartbeat_units": len([s for s in hb_skill_any if s]),
        "heartbeat_coverage": round(coverage, 3),
        "tail_gap": tail_gap,
        "job_started": job_started,
        "bad_event_lines": bad_lines,
        "total_event_lines": total_lines,
        "warnings": warnings,
        "units": units,
    }


def render(report: dict) -> str:
    lines = []
    v = report["verdict"]
    mark = "✅ PASS" if v == "PASS" else "⚠️  WARN"
    lines.append(f"[doctor] {mark}  总量 {report['total_kb']}KB  单元 {report['unit_count']}"
                 f"（适用 {report['applicable_count']} / N/A {len(report['na_units'])}）  "
                 f"heartbeat {report['heartbeat_total']}（覆盖率 {report['heartbeat_coverage']:.0%}，"
                 f"尾部缺口 {report['tail_gap']}）")
    if report["events_status"] != "ok":
        lines.append(f"[doctor] ⚠️  事件日志状态: {report['events_status']}")
    if report["thin_units"]:
        lines.append(f"[doctor] 贴线单元({len(report['thin_units'])}): " + ", ".join(report["thin_units"]))
    if report["missing_artifact"]:
        lines.append(f"[doctor] 声明产出但缺产物: {', '.join(report['missing_artifact'])}")
    if report["pending_units"]:
        lines.append(f"[doctor] 未完成单元: {', '.join(report['pending_units'])}")
    for w in report["warnings"]:
        lines.append(f"[doctor] ⚠️  {w}")
    if v == "PASS":
        lines.append("[doctor] 未发现执行退化指纹。")
    return "\n".join(lines)


def run_and_render(run_root: Path, registry: Path, *, as_json: bool = False,
                   write: bool = False, strict: bool = False) -> int:
    """诊断 + 输出 + 返回退出码的唯一入口，供 CLI 与其他工具复用。

    - write: 原子落盘 evidence/doctor-report.json
    - as_json: 以 JSON 打印（否则人类可读的 render 文本）
    - strict: WARN 时返回退出码 3（默认 advisory 恒 0）
    """
    report = diagnose(run_root, registry)
    if write:
        _atomic_write_json(run_root / "evidence/doctor-report.json", report)
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render(report))
    return 3 if (strict and report["verdict"] == "WARN") else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="全量分析 Run 健康体检（advisory）")
    ap.add_argument("--run-root", required=True)
    ap.add_argument("--registry", default=DEFAULT_REGISTRY)
    ap.add_argument("--json", action="store_true", help="输出 JSON 报告")
    ap.add_argument("--strict", action="store_true", help="WARN 时以退出码 3 返回（默认 advisory 恒 0）")
    ap.add_argument("--write", action="store_true", help="原子写 evidence/doctor-report.json")
    args = ap.parse_args(argv)
    try:
        return run_and_render(Path(args.run_root), Path(args.registry),
                              as_json=args.json, write=args.write, strict=args.strict)
    except DoctorError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
