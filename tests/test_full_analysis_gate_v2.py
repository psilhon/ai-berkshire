import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
GATE = REPO / "tools" / "full_analysis_gate.py"
REGISTRY = REPO / "tools" / "full_analysis_contract.json"


def run_gate(root, *args):
    return subprocess.run(
        [sys.executable, str(GATE), *map(str, args)],
        cwd=root,
        capture_output=True,
        text=True,
    )


ROLE_CN = {
    "duan": "段永平", "buffett": "巴菲特", "munger": "芒格", "li": "李录",
    "editor": "编辑", "reader": "读者", "company": "公司", "regulatory": "监管",
    "industry": "行业", "sentiment": "情绪", "governance": "治理", "business": "业务",
    "technology": "技术", "finance": "财务", "alternative-data": "另类",
}
BOILERPLATE = {"研究免责", "仅供学习研究", "数据截止日", "命令执行记录", "下游证据", "契约计算"}


def build_compliant_report(registry_path, skill_id):
    """按 contract 必需要素小节生成能通过实质校验的达标报告（真回归测试用）。"""
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
    return "".join(lines)


class GateV2Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self.run_root = self.root / "local/company/000651.SZ-格力电器/20260723-120000-ab12"

    def tearDown(self):
        self.temp.cleanup()

    def init(self):
        result = run_gate(
            self.root, "init", "--registry", REGISTRY, "--repo-root", self.root,
            "--company", "格力电器", "--code", "000651.SZ", "--as-of", "2026-07-23",
            "--platform", "workbuddy",
            "--run-root", self.run_root,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_init_creates_canonical_manifest_and_uniform_intermediate_dirs(self):
        self.init()
        manifest_path = self.run_root / "evidence/00-analysis-manifest.json"
        self.assertTrue(manifest_path.is_file())
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["manifest_schema_version"], "full-analysis-manifest/v2")
        self.assertEqual(manifest["run"]["status"], "RUNNING")
        self.assertEqual(manifest["company"]["code"], "000651.SZ")
        self.assertEqual(len(manifest["skills"]), 20)
        self.assertTrue((self.run_root / "evidence/attempts").is_dir())
        self.assertTrue((self.run_root / "evidence/work-packets").is_dir())
        self.assertTrue((self.run_root / "05-内容生产").is_dir())
        self.assertFalse((self.run_root / "manifest.json").exists())

    def test_ingest_promotes_attempt_artifact_and_updates_skill_atomically(self):
        self.init()
        attempt_dir = self.run_root / "evidence/attempts/ashare-data/attempt-01"
        attempt_dir.mkdir(parents=True)
        source = attempt_dir / "result.md"
        source.write_text(build_compliant_report(REGISTRY, "ashare-data"), encoding="utf-8")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        result_bundle = attempt_dir / "result.json"
        result_bundle.write_text(json.dumps({
            "schema_version": "result-schema/v1",
            "run_id": json.loads((self.run_root / "evidence/00-analysis-manifest.json").read_text())["run"]["run_id"],
            "work_unit_id": "wu-ashare-data",
            "attempt_id": "attempt-01",
            "agent_job_id": "job-01",
            "lease_nonce": "lease-01",
            "skill_id": "ashare-data",
            "role_id": None,
            "status": "PASS",
            "artifact_records": [{
                "artifact_id": "artifact.ashare-data",
                "path": str(source.relative_to(self.run_root)),
                "bytes": source.stat().st_size,
                "sha256": digest,
                "formal": False,
                "accepted": False,
            }],
            "fact_updates": [], "source_records": [], "calculation_requests": [],
            "judgments": [], "limitations": [], "pwl_candidates": [],
            "started_at": "2026-07-23T12:00:00+08:00",
            "completed_at": "2026-07-23T12:01:00+08:00", "error": None,
        }, ensure_ascii=False), encoding="utf-8")
        ingested = run_gate(self.root, "ingest-result", "--run-root", self.run_root,
                            "--registry", REGISTRY, "--result", result_bundle)
        self.assertEqual(ingested.returncode, 0, ingested.stdout + ingested.stderr)
        formal = self.run_root / "01-数据与快筛/01-ashare-data.md"
        self.assertTrue(formal.is_file())
        manifest = json.loads((self.run_root / "evidence/00-analysis-manifest.json").read_text())
        skill = next(item for item in manifest["skills"] if item["skill_id"] == "ashare-data")
        self.assertEqual(skill["status"], "PASS")
        self.assertEqual(skill["artifact_records"][0]["sha256"], digest)

    def test_finalize_rejects_incomplete_run_loudly(self):
        self.init()
        result = run_gate(self.root, "finalize", "--run-root", self.run_root,
                          "--registry", REGISTRY)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PENDING", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
