import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
CLI = REPO / "scripts/full_analysis.py"
GATE = REPO / "tools/full_analysis_gate.py"
REGISTRY = REPO / "tools/full_analysis_contract.json"

ROLE_CN = {
    "duan": "段永平", "buffett": "巴菲特", "munger": "芒格", "li": "李录",
    "editor": "编辑", "reader": "读者", "company": "公司", "regulatory": "监管",
    "industry": "行业", "sentiment": "情绪", "governance": "治理", "business": "业务",
    "technology": "技术", "finance": "财务", "alternative-data": "另类",
}
BOILERPLATE = {"研究免责", "仅供学习研究", "数据截止日", "命令执行记录", "下游证据", "契约计算"}


def build_compliant_report(registry_path, skill_id):
    reg = json.loads(Path(registry_path).read_text(encoding="utf-8"))
    skill = next(s for s in reg["skills"] if s["skill_id"] == skill_id)
    lines = [f"# {skill_id}\n"]
    for sec in skill.get("sections", []):
        if not sec.get("required"):
            continue
        h = sec["heading"]
        fill = ("数据详实论证内容充实满足下限要求 " * 30 + "\n") if h not in BOILERPLATE else "占位\n"
        lines.append(f"## {h}\n{fill}")
    need_d = skill.get("min_dissent_points", 0)
    for i in range(need_d):
        lines.append(f"## 分歧点{i + 1}\n与另一视角存在分歧需交锋。数据详实论证内容充实满足下限要求。\n")
    if skill.get("skill_type") == "fanout":
        roles = (skill.get("roles") or {}).get("required_roles", [])
        names = [ROLE_CN.get(r, r) for r in roles if r != "integrator"]
        if len(names) >= 2:
            for k in range(2):
                lines.append(f"## 分歧仲裁{k + 1}\n{names[0]}与{names[1]}在核心判断上分歧明显，需仲裁。"
                             f"数据详实论证内容充实满足下限要求。\n")
    body = "".join(lines)
    min_bytes = skill["artifact"]["min_bytes"]
    while len(body.encode("utf-8")) < min_bytes:
        body += "数据详实论证扩充内容 " * 20 + "\n"
    return body


class FullAnalysisE2ETests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self.run_root = self.root / "local/company/000651.SZ-格力电器/20260723-120000-e2e"

    def tearDown(self):
        self.temp.cleanup()

    def cli(self, *args):
        return subprocess.run([sys.executable, str(CLI), *map(str, args)], cwd=self.root, capture_output=True, text=True)

    def test_single_company_canary_closes_all_twenty_units(self):
        started = self.cli("start", "--registry", REGISTRY, "--repo-root", self.root,
                           "--company", "格力电器", "--code", "000651.SZ", "--as-of", "2026-07-23",
                           "--run-root", self.run_root)
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        manifest = json.loads((self.run_root / "evidence/00-analysis-manifest.json").read_text())
        run_id = manifest["run"]["run_id"]
        registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
        by_id = {item["skill_id"]: item for item in registry["skills"]}
        for _ in range(20):
            leased = self.cli("next-work", "--run-root", self.run_root)
            self.assertEqual(leased.returncode, 0, leased.stdout + leased.stderr)
            lease = json.loads(leased.stdout)
            self.assertEqual(lease["status"], "LEASED")
            started_job = self.cli("job-started", "--run-root", self.run_root,
                                   "--work-unit-id", lease["work_unit_id"], "--attempt-id", lease["attempt_id"],
                                   "--lease-nonce", lease["lease_nonce"], "--agent-job-id", f"job-{lease['attempt_id']}")
            self.assertEqual(started_job.returncode, 0, started_job.stdout + started_job.stderr)
            skill_id = lease["skill_id"]
            attempt_dir = self.run_root / "evidence/attempts" / skill_id / lease["attempt_id"]
            attempt_dir.mkdir(parents=True, exist_ok=True)
            artifact = attempt_dir / "report.md"
            body = build_compliant_report(REGISTRY, skill_id)
            artifact.write_text(body, encoding="utf-8")
            roles = (by_id[skill_id].get("roles") or {})
            if roles.get("mode") == "independent_then_integrator":
                for role in roles.get("required_roles", []):
                    if role == "integrator":
                        continue
                    (attempt_dir / f"role-{role}.md").write_text(
                        f"角色 {role} 独立分析：" + "数据详实论证 " * 80 + "\n", encoding="utf-8")
            bundle = {
                "schema_version": "result-schema/v1", "run_id": run_id,
                "work_unit_id": lease["work_unit_id"], "attempt_id": lease["attempt_id"],
                "agent_job_id": f"job-{lease['attempt_id']}", "lease_nonce": lease["lease_nonce"],
                "skill_id": skill_id, "role_id": None, "status": "PASS",
                "artifact_records": [{"artifact_id": by_id[skill_id]["artifact"]["artifact_id"],
                                      "path": str(artifact.relative_to(self.run_root)), "bytes": artifact.stat().st_size,
                                      "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(), "formal": False, "accepted": False}],
                "fact_updates": [], "source_records": [], "calculation_requests": [], "judgments": [],
                "limitations": [], "pwl_candidates": [], "started_at": "2026-07-23T12:00:00+08:00",
                "completed_at": "2026-07-23T12:01:00+08:00", "error": None,
            }
            result_path = attempt_dir / "result.json"
            result_path.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
            submitted = self.cli("submit-result", "--run-root", self.run_root, "--registry", REGISTRY, "--result", result_path)
            self.assertEqual(submitted.returncode, 0, submitted.stdout + submitted.stderr)
        audit = self.cli("audit", "--run-root", self.run_root)
        self.assertEqual(audit.returncode, 0, audit.stdout + audit.stderr)
        finalized = subprocess.run([sys.executable, str(GATE), "finalize", "--run-root", str(self.run_root), "--registry", str(REGISTRY)], cwd=self.root, capture_output=True, text=True)
        self.assertEqual(finalized.returncode, 0, finalized.stdout + finalized.stderr)
        final_manifest = json.loads((self.run_root / "evidence/00-analysis-manifest.json").read_text())
        self.assertEqual(final_manifest["run"]["status"], "APPROVED")


if __name__ == "__main__":
    unittest.main()
