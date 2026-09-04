"""tests/conftest.py — run 目录测试工厂（经 run_layout/run_store 构造）。

双重身份：
- pytest 环境：conftest 自动加载（未来若引入 pytest）。
- unittest 环境（当前 check.sh 用 `python3 -m unittest discover -s tests`，
  discover 会把 tests/ 插入 sys.path）：测试文件里 `import conftest` 显式使用。

背景（2026-08-30 架构评审候选⑪）：test_evidence_receipt / test_mk_result_bundle
各自手拼 `evidence/runtime-state.json` / manifest 字面量路径与私有最小 shape，
一处目录改名要追 N 份测试。run 目录的「放在哪」已有 run_layout 单一真源、
「怎么读写」已有 run_store——测试构造同样只走这条缝。

本工厂经 run_store.write_manifest / write_runtime_state / reset_events 构造与
gate init 同构的 canonical run 目录；路径一律取 run_layout 常量，零字面量。
测试需要的特殊形状（LEASED work_units / 过去时刻 run_started_at / 指定 skills）
通过参数覆盖，工厂不做魔法。
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_TOOLS = str(REPO / "tools")
if _TOOLS not in sys.path:
    sys.path.insert(0, _TOOLS)

import run_layout  # noqa: E402
import run_store  # noqa: E402


def make_run_root(
    td: Path,
    *,
    run_id: str = "run-test",
    run_started_at: str | None = None,
    skills: list[str] | tuple[str, ...] = (),
    work_units: list[dict] | None = None,
    budget: dict | None = None,
) -> Path:
    """在 td/run 下构造 canonical run 目录，返回 run_root。

    - run_started_at：默认 now（ISO）；evidence 时效类测试传过去时刻。
    - skills：manifest.skills[] 的 skill_id 列表（PENDING / attempts 空）。
    - work_units：默认按 gate init 形状为每个 skill 生成 PENDING 条目；
      需要 LEASED 等特殊状态机形状时整体覆盖。
    - budget：默认 gate 的 13 单元 canonical（normal_target=2N+1, 30, 33）。
    """
    run_root = td / "run"
    n = len(skills)
    normal_target = 2 * n + 1
    manifest = {
        "manifest_schema_version": "full-analysis-manifest/v2",
        "contract": {"schema_version": "full-analysis-contract/lean-v1"},
        "run": {"run_id": run_id, "status": "RUNNING", "created_at": run_store.now_iso(),
                "updated_at": run_store.now_iso(), "platform": "test",
                "as_of": None, "run_root": str(run_root), "contract_commit": None},
        "company": {"code": None, "name": None},
        "skills": [{"skill_id": s, "status": "PENDING", "attempts": [],
                    "artifact_records": []} for s in skills],
        "artifacts": [], "facts": [], "sources": [], "calculations": [],
        "judgments": [], "command_receipts": [], "role_runs": [],
        "capabilities": {}, "events": [], "delivery": {"summary": None},
    }
    run_store.write_manifest(run_root, manifest)
    if work_units is None:
        work_units = [{
            "work_unit_id": f"wu-{s}", "skill_id": s, "core": True,
            "status": "PENDING", "attempts": 0, "max_attempts": 3,
            "next_retry_at": None, "depends_on": [],
        } for s in skills]
    if budget is None:
        budget = {"normal_target": normal_target, "stop_dispatch_at": 30,
                  "hard_max": 33, "used": 0, "preflight_count": 0, "reserved": 0}
    run_store.write_runtime_state(run_root, {
        "state_version": run_store.STATE_VERSION,
        "run_id": run_id,
        "budget": budget,
        "concurrency": {"max": 2, "current": 0, "cooldown_until": None},
        "authorization": "standard",
        "run_started_at": run_started_at or run_store.now_iso(),
        "dependency_graph": {},
        "work_units": work_units,
    })
    run_store.reset_events(run_root)
    for name in ("facts.json", "sources.json", "calculations.json", "artifacts.json"):
        run_store.atomic_write_json(run_root / run_layout.EVIDENCE_REL / name, [])
    return run_root


def seed_attempt(
    run_root: Path,
    skill_id: str,
    attempt_id: str,
    report_text: str,
    *,
    register_in_manifest: bool = True,
) -> Path:
    """播种一次 attempt：attempt 目录 + report.md，并登记进 manifest.skills[].attempts。

    路径经 run_layout.ATTEMPTS_REL，不做字面量拼接。
    """
    attempt_dir = run_root / run_layout.ATTEMPTS_REL / skill_id / attempt_id
    attempt_dir.mkdir(parents=True, exist_ok=True)
    (attempt_dir / "report.md").write_text(report_text, encoding="utf-8")
    if register_in_manifest:
        manifest = run_store.load_manifest(run_root)
        entry = next(s for s in manifest["skills"] if s["skill_id"] == skill_id)
        entry["attempts"] = [*(entry.get("attempts") or []), attempt_id]
        run_store.write_manifest(run_root, manifest)
    return attempt_dir
