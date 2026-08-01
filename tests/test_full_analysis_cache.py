"""Task 5：跨运行 APPROVED 产物缓存（TDD 失败测试先行）。"""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tools import full_analysis_cache as cache

REPO = Path(__file__).resolve().parents[1]
REGISTRY = REPO / "tools" / "full_analysis_contract.json"


def _make_manifest(skill_id="ashare-data", fact_value="100", *, extra_fact=None):
    facts = [
        {"fact_id": "f.1", "field": "revenue", "value": fact_value,
         "source_ids": ["s.1"], "skill_id": skill_id},
    ]
    if extra_fact:
        facts.append(extra_fact)
    return {
        "manifest_schema_version": "full-analysis-manifest/v2",
        "run": {"run_id": "test-run", "status": "APPROVED", "as_of": "2026-07-23"},
        "company": {"code": "000651.SZ", "name": "格力电器"},
        "skills": [{
            "skill_id": skill_id, "status": "PASS",
            "artifact_records": [{
                "artifact_id": f"artifact.{skill_id}",
                "path": f"01-data-screen/{skill_id}.md",
                "bytes": 100, "sha256": hashlib.sha256(b"content").hexdigest(),
                "formal": True, "accepted": True,
            }],
        }],
        "facts": facts,
        "sources": [{"source_id": "s.1", "url": "https://example.invalid/a",
                     "retrieved_at": "2026-07-25", "source_type": "filing"}],
        "calculations": [], "judgments": [], "command_receipts": [],
        "capabilities": {"tushare_configured": True},
    }


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        # run_root = local/Company/000651.SZ-格力电器/<run>
        self.run_root = self.root / "local/Company/000651.SZ-格力电器/20260723-120000-cache1"
        self.run_root.mkdir(parents=True)
        (self.run_root / "evidence").mkdir()
        (self.run_root / "01-data-screen").mkdir()
        (self.run_root / "01-data-screen/ashare-data.md").write_text("content", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def _store(self, manifest):
        registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
        return cache.store_approved(self.run_root, manifest, registry)

    def test_key_is_deterministic(self):
        m1 = _make_manifest()
        registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
        k1 = cache.cache_key_for_skill(self.run_root, m1, registry, "ashare-data")
        k2 = cache.cache_key_for_skill(self.run_root, m1, registry, "ashare-data")
        self.assertEqual(k1, k2)
        self.assertEqual(len(k1), 64)

    def test_store_then_lookup_hits(self):
        manifest = _make_manifest()
        stored = self._store(manifest)
        self.assertEqual(stored["stored"], 1)
        hit = cache.lookup(self.run_root, manifest, json.loads(REGISTRY.read_text(encoding="utf-8")), "ashare-data")
        self.assertEqual(hit["status"], "HIT")
        self.assertEqual(hit["skill_id"], "ashare-data")
        self.assertIn("artifact_path", hit)
        self.assertIn("artifact_sha256", hit)

    def test_fact_change_invalidates_downstream_skill(self):
        # 上游 fact（ashare-data）变化 → 下游（financial-data）的 input_evidence_digest 变 → MISS
        (self.run_root / "01-data-screen/financial-data.md").write_text("content", encoding="utf-8")
        upstream = {"fact_id": "f.1", "field": "revenue", "value": "100",
                    "source_ids": ["s.1"], "skill_id": "ashare-data"}
        m1 = _make_manifest(skill_id="financial-data", extra_fact=upstream)
        self._store(m1)
        hit = cache.lookup(self.run_root, m1, json.loads(REGISTRY.read_text(encoding="utf-8")), "financial-data")
        self.assertEqual(hit["status"], "HIT")
        upstream2 = {**upstream, "value": "200"}
        m2 = _make_manifest(skill_id="financial-data", extra_fact=upstream2)
        miss = cache.lookup(self.run_root, m2, json.loads(REGISTRY.read_text(encoding="utf-8")), "financial-data")
        self.assertEqual(miss["status"], "MISS")

    def test_own_fact_change_keeps_own_skill_hit(self):
        # 本 skill 自己的 fact 变化不影响自己的缓存键（上游未变，artifact 可复用）
        m1 = _make_manifest(fact_value="100")
        self._store(m1)
        m2 = _make_manifest(fact_value="200")
        hit = cache.lookup(self.run_root, m2, json.loads(REGISTRY.read_text(encoding="utf-8")), "ashare-data")
        self.assertEqual(hit["status"], "HIT")

    def test_tampered_artifact_sha256_returns_miss(self):
        manifest = _make_manifest()
        self._store(manifest)
        # 篡改缓存 artifact → digest 校验失败 → MISS
        hit = cache.lookup(self.run_root, manifest, json.loads(REGISTRY.read_text(encoding="utf-8")), "ashare-data")
        cached_artifact = Path(hit["artifact_path"])
        cached_artifact.write_text("tampered", encoding="utf-8")
        miss = cache.lookup(self.run_root, manifest, json.loads(REGISTRY.read_text(encoding="utf-8")), "ashare-data")
        self.assertEqual(miss["status"], "MISS")


    def test_cache_lookup_cli_requires_registry_flag(self):
        # v3.3.7 生产验证发现：cache-lookup CLI 曾缺 --registry 参数（模块函数测试未覆盖 CLI）
        import subprocess, sys
        manifest = _make_manifest()
        (self.run_root / "evidence/00-analysis-manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        self._store(manifest)
        proc = subprocess.run(
            [sys.executable, str(REPO / "scripts/full_analysis.py"),
             "cache-lookup", "--run-root", self.run_root, "--skill-id", "ashare-data"],
            cwd=self.root, capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        out = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(out["status"], "HIT")


if __name__ == "__main__":
    unittest.main()
