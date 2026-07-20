#!/usr/bin/env python3
"""tools/full_analysis_gate.py 单测 — 全量公司分析确定性验收器 (任务 D, v1.4 §7/§8/§14/§15.2).

所有测试在 tempfile.TemporaryDirectory 内搭临时 git 仓库 + 临时 HOME + 合成注册表,
绝不写真实 ~/.claude / ~/.codex / 本仓库工作区。gate 一律走 subprocess CLI;
仅 path_gate 独立函数按 §8.2 直接 import 逐条测。
"""
import itertools
import json
import hashlib
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TOOLS = REPO / "tools"
GATE = TOOLS / "full_analysis_gate.py"
CHECKER = REPO / "scripts" / "check-full-analysis-contract.py"

sys.path.insert(0, str(TOOLS))
import full_analysis_gate as gate_mod  # noqa: E402

STAGES = ["s1", "s2", "s3", "s4", "s5"]
STAGE_DIRS = {
    "s1": "01-数据与快筛",
    "s2": "02-公司与财报",
    "s3": "03-行业与机会",
    "s4": "04-论文与组合",
    "s5": "05-内容生产",
}
SECTIONS = ["数据截止日", "直接来源", "限制", "仅供学习研究"]

ARTIFACT_TEXT = (
    "# 合成报告\n\n"
    "数据截止日: synthetic-cutoff\n"
    "直接来源: 合成测试数据源A; 合成测试数据源B\n"
    "限制: 本文件为单测合成产物\n"
    "仅供学习研究, 不构成投资建议。\n"
    "营业收入: 100\n"
    "净利润: 20\n"
    "总资产: 300\n"
)


def audit_record(artifact, verdict="PASS"):
    reported = {1: "100", 2: "20", 3: "300"}
    results = []
    for point_id, value in reported.items():
        if verdict == "FAIL":
            first = second = str(int(value) * 2)
        else:
            first = value
            second = value if verdict == "PASS" else None
        results.append({
            "id": point_id,
            "fetched_value": first,
            "fetched_source": "source-a",
            "fetched_value2": second,
            "fetched_source2": "source-b" if second is not None else "",
        })
    return {"artifact": artifact, "ratio": 0.15, "seed": 42,
            "results": results}


def artifact_path(i):
    stage = STAGES[(i - 1) % 5]
    return f"{STAGE_DIRS[stage]}/{i:02d}-sk{i:02d}.md"


def make_registry(nskills=20, audit_policies=None, legacy=None, role_rules=None,
                  evidence_rules=None, required_sections=None):
    """程序化合成注册表: 结构与真实 tools/full_analysis_contract.json 同形。"""
    audit_policies = audit_policies or {}
    legacy = legacy or {}
    role_rules = role_rules or {}
    evidence_rules = evidence_rules or {}
    required_sections = required_sections or {}
    skills = []
    for i in range(1, nskills + 1):
        stage = STAGES[(i - 1) % 5]
        skills.append({
            "index": i,
            "name": f"sk{i:02d}",
            "stage": stage,
            "spec_source": f"skills/sk{i:02d}.md",
            "artifact_rules": [{
                "path": artifact_path(i),
                "min_bytes": 64,
                "required_sections": required_sections.get(i, SECTIONS),
                "audit_policy": audit_policies.get(i, "none"),
            }],
            "evidence_rules": evidence_rules.get(i, []),
            "domain_evidence_status": "PHASE2_PENDING",
            "applicability_rule": {"predicate_id": "always_applicable",
                                   "alternative": None},
            "role_rule": role_rules.get(
                i, {"required_roles": [], "min_independent_contexts": 0,
                    "sequential_cap": "PASS"}),
            "legacy_output_patterns": legacy.get(i, []),
        })
    return {
        "registry_schema_version": 1,
        "manifest_schema_version": 1,
        "annotations_schema_version": 1,
        "stage_dirs": STAGE_DIRS,
        "negative_acceptance_dir": "06-负向验收",
        "generic_required_sections": SECTIONS,
        "predicates": ["always_applicable", "is_a_share"],
        "skills": skills,
    }


class GateWorkspace:
    """临时 git 仓库 + 临时 HOME + 合成注册表 + gate subprocess 封装。"""

    def __init__(self, registry=None, git=True, gitignore="/local/\n"):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.repo = base / "repo"
        self.repo.mkdir()
        self.home = base / "home"
        self.home.mkdir()
        if git:
            subprocess.run(["git", "init", "-q"], cwd=self.repo,
                           check=True, capture_output=True)
        if gitignore is not None:
            (self.repo / ".gitignore").write_text(gitignore, encoding="utf-8")
        (self.repo / "skills").mkdir()
        self.registry = registry if registry is not None else make_registry()
        for sk in self.registry.get("skills", []):
            spec = self.repo / sk["spec_source"]
            spec.parent.mkdir(parents=True, exist_ok=True)
            spec.write_text(f"# {sk['name']} 合成规范\n\n正文占位。\n",
                            encoding="utf-8")
        self.registry_path = base / "registry.json"
        self.registry_path.write_text(
            json.dumps(self.registry, ensure_ascii=False, indent=1),
            encoding="utf-8")
        self._ctr = itertools.count(1)
        self.env_overrides = {}

    def cleanup(self):
        self.tmp.cleanup()

    def env(self):
        env = os.environ.copy()
        env["HOME"] = str(self.home)
        env.pop("TUSHARE_TOKEN", None)
        env.update(self.env_overrides)
        return env

    def gate(self, *args):
        return subprocess.run(
            [sys.executable, str(GATE), *[str(a) for a in args]],
            capture_output=True, text=True, env=self.env(), cwd=self.repo)

    def git(self, *args):
        return subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
            cwd=self.repo, capture_output=True, text=True)

    def init(self, company="评测公司", visibility="private",
             platform="claude_code", as_of="2026-07-17", extra=()):
        return self.gate("init", "--registry", self.registry_path,
                         "--company", company, "--visibility", visibility,
                         "--platform", platform, "--as-of", as_of,
                         "--repo-root", self.repo, *extra)

    def init_ok(self, **kw):
        cp = self.init(**kw)
        assert cp.returncode == 0, f"init 应成功: {cp.stdout}\n{cp.stderr}"
        data = json.loads(cp.stdout)
        return Path(data["run_root"]), data

    def manifest(self, run_root):
        return json.loads((Path(run_root) / "manifest.json")
                          .read_text(encoding="utf-8"))

    def write_evidence(self, data):
        p = Path(self.tmp.name) / f"evidence-{next(self._ctr)}.json"
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return p

    def write_artifact(self, run_root, rel, text=ARTIFACT_TEXT):
        p = Path(run_root) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def begin(self, run_root, name, extra=()):
        return self.gate("begin-skill", "--run-root", run_root,
                         "--skill", name, *extra)

    def finish(self, run_root, name, state="COMPLETE", artifacts=(),
               evidence=None):
        args = ["finish-skill", "--registry", self.registry_path,
                "--run-root", run_root, "--skill", name, "--state", state]
        for a in artifacts:
            args += ["--artifact", a]
        if evidence is not None:
            args += ["--evidence-file", self.write_evidence(evidence)]
        return self.gate(*args)

    def write_fake_ashare_cli(self, exit_code=0):
        script = self.repo / "tools" / "ashare_data.py"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(
            "import sys\n"
            "print('数据来源: synthetic-source')\n"
            "print('operation=' + sys.argv[1])\n"
            f"raise SystemExit({exit_code})\n",
            encoding="utf-8",
        )
        return script

    def run_ashare(self, run_root, operation, code="600519", sources=()):
        args = ["run-ashare-command", "--registry", self.registry_path,
                "--run-root", run_root, "--operation", operation,
                "--code", code]
        for source in sources:
            args += ["--source", source]
        return self.gate(*args)

    def complete_skill(self, run_root, index, evidence=None, text=ARTIFACT_TEXT):
        name = f"sk{index:02d}"
        cp = self.begin(run_root, name)
        assert cp.returncode == 0, f"begin {name}: {cp.stdout}\n{cp.stderr}"
        art = artifact_path(index)
        self.write_artifact(run_root, art, text=text)
        cp = self.finish(run_root, name, artifacts=[art], evidence=evidence)
        assert cp.returncode == 0, f"finish {name}: {cp.stdout}\n{cp.stderr}"

    def complete_all(self, run_root, skip=(), evidence=None):
        evidence = evidence or {}
        for sk in self.registry["skills"]:
            if sk["index"] in skip:
                continue
            self.complete_skill(run_root, sk["index"],
                                evidence=evidence.get(sk["index"]))

    def checkpoint(self, run_root):
        return self.gate("checkpoint", "--registry", self.registry_path,
                         "--run-root", run_root)

    def finalize(self, run_root):
        return self.gate("finalize", "--registry", self.registry_path,
                         "--run-root", run_root)

    def summary(self, run_root):
        return self.gate("summary", "--run-root", run_root)

    def set_review_mode(self, run_root, mode):
        return self.gate("set-review-mode", "--run-root", run_root,
                         "--mode", mode)

    def set_industry(self, run_root, data):
        return self.gate("set-industry", "--run-root", run_root,
                         "--industry-file", self.write_evidence(data))


def out(cp):
    return (cp.stdout or "") + (cp.stderr or "")


class GateTestCase(unittest.TestCase):
    def make_ws(self, **kw):
        ws = GateWorkspace(**kw)
        self.addCleanup(ws.cleanup)
        return ws


# ---------------------------------------------------------------------------
# #1 contracts 命令 与 独立校验器结论一致
# ---------------------------------------------------------------------------
class TestContractsCommand(GateTestCase):

    def test_help_renders_literal_percent_without_traceback(self):
        ws = self.make_ws()
        cp = ws.gate("--help")
        self.assertEqual(cp.returncode, 0, out(cp))
        self.assertIn("50%/累计 80%", cp.stdout)
        self.assertNotIn("Traceback", out(cp))

    def test_contracts_outputs_20_items_and_checker_agrees(self):
        ws = self.make_ws()
        cp = ws.gate("contracts", "--registry", ws.registry_path)
        self.assertEqual(cp.returncode, 0, out(cp))
        self.assertNotIn("Traceback", out(cp))
        data = json.loads(cp.stdout)
        self.assertEqual(data["count"], 20)
        self.assertEqual(len(data["items"]), 20)
        for item in data["items"]:
            self.assertEqual(set(item),
                             {"index", "name", "stage", "audit_policy"})
        self.assertEqual([it["index"] for it in data["items"]],
                         list(range(1, 21)))
        # 独立校验器对同一合成注册表结论一致 (exit 0)
        cc = subprocess.run(
            [sys.executable, str(CHECKER), "--registry", ws.registry_path,
             "--repo-root", ws.repo], capture_output=True, text=True)
        self.assertEqual(cc.returncode, 0, out(cc))

    def test_contracts_broken_registry_checker_agrees(self):
        ws = self.make_ws(registry=make_registry(nskills=19))
        cp = ws.gate("contracts", "--registry", ws.registry_path)
        self.assertNotEqual(cp.returncode, 0)
        cc = subprocess.run(
            [sys.executable, str(CHECKER), "--registry", ws.registry_path,
             "--repo-root", ws.repo], capture_output=True, text=True)
        self.assertEqual(cc.returncode, 1)


# ---------------------------------------------------------------------------
# #2 init 前置校验 + 结构 + manifest / #13 路径执法级别
# ---------------------------------------------------------------------------
class TestInit(GateTestCase):

    def test_non_git_workspace_defaults_to_private_and_succeeds(self):
        ws = self.make_ws(git=False)
        cp = ws.gate("init", "--registry", ws.registry_path,
                     "--company", "评测公司", "--platform", "claude_code",
                     "--as-of", "2026-07-17", "--repo-root", ws.repo)
        self.assertEqual(cp.returncode, 0, out(cp))
        data = json.loads(cp.stdout)
        run_root = Path(data["run_root"])
        rel = run_root.resolve().relative_to(ws.repo.resolve())
        self.assertEqual(rel.parts[:2], ("local", "company"))
        run = ws.manifest(run_root)["run"]
        self.assertEqual(run["visibility"], "private")
        self.assertEqual(run["workspace_audit_mode"], "none")

    def test_init_assigns_stable_artifact_ids(self):
        ws = self.make_ws(registry=make_registry(nskills=1))
        run_root, _ = ws.init_ok()
        skill = ws.manifest(run_root)["skills"][0]
        assigned = skill["assigned_artifacts"]
        self.assertEqual(len(assigned), 1)
        self.assertEqual(assigned[0]["path"], artifact_path(1))
        expected = gate_mod.artifact_id_for("sk01", artifact_path(1))
        self.assertEqual(assigned[0]["artifact_id"], expected)

    def test_missing_visibility_defaults_to_private(self):
        ws = self.make_ws()
        cp = ws.gate("init", "--registry", ws.registry_path,
                     "--company", "评测公司", "--platform", "claude_code",
                     "--as-of", "2026-07-17", "--repo-root", ws.repo)
        self.assertEqual(cp.returncode, 0, out(cp))
        data = json.loads(cp.stdout)
        run = ws.manifest(data["run_root"])["run"]
        self.assertEqual(run["visibility"], "private")
        self.assertEqual(run["workspace_audit_mode"], "git")

    def test_private_output_does_not_require_gitignore(self):
        ws = self.make_ws(gitignore="")  # /local/ 未被忽略
        cp = ws.init()
        self.assertEqual(cp.returncode, 0, out(cp))
        data = json.loads(cp.stdout)
        self.assertEqual(
            ws.manifest(data["run_root"])["run"]["visibility"], "private")

    def test_public_local_path_shape_rejected(self):
        ws = self.make_ws()
        cp = ws.init(company="../local/坏公司", visibility="public")
        self.assertEqual(cp.returncode, 2, out(cp))

    def test_bad_as_of_exit_2(self):
        ws = self.make_ws()
        cp = ws.init(as_of="20260717")
        self.assertEqual(cp.returncode, 2, out(cp))

    def test_sandboxed_claim_without_canary_exit_2(self):
        ws = self.make_ws()
        cp = ws.init(extra=("--path-enforcement-level", "SANDBOXED"))
        self.assertEqual(cp.returncode, 2, out(cp))
        self.assertNotIn("已预防越界", out(cp))

    def test_init_structure_manifest_lock_baseline(self):
        legacy = {1: ["~/{company}投资研究报告_{date}.md"],
                  2: ["reports/{company}-earnings-{period}.md"]}
        ws = self.make_ws(registry=make_registry(legacy=legacy))
        run_root, data = ws.init_ok(extra=("--codes", "600519",
                                           "--listing-status", "listed"))
        self.assertNotIn("已预防越界", data.get("_raw", "") + json.dumps(data))
        # run_root 落在 local/company/<公司>/<run_id> (private, 无中间层)
        rel = run_root.resolve().relative_to(ws.repo.resolve())
        self.assertEqual(rel.parts[0], "local")
        self.assertEqual(rel.parts[1], "company")
        self.assertEqual(rel.parts[2], "评测公司")
        run_id = data["run_id"]
        self.assertRegex(run_id, r"^\d{8}T\d{6}\+\d{4}-评测公司$")
        self.assertEqual(rel.parts[3], run_id)
        # 目录: 5 stage + 06-负向验收 + evidence/locks
        for d in STAGE_DIRS.values():
            self.assertTrue((run_root / d).is_dir(), d)
        self.assertTrue((run_root / "06-负向验收").is_dir())
        self.assertTrue((run_root / "evidence" / "locks").is_dir())
        self.assertTrue((run_root / "evidence" / "audit-baseline.txt").is_file())
        # 锁
        lock = run_root / "evidence" / ".full-analysis.lock"
        self.assertTrue(lock.is_file())
        lock_data = json.loads(lock.read_text(encoding="utf-8"))
        for key in ("run_id", "host", "pid", "start_fingerprint", "platform",
                    "root_real", "started_at"):
            self.assertIn(key, lock_data)
        self.assertEqual(lock_data["run_id"], run_id)
        # manifest
        m = ws.manifest(run_root)
        self.assertEqual(m["manifest_schema_version"], 1)
        self.assertEqual(m["registry_schema_version"], 1)
        self.assertEqual(m["annotations_schema_version"], 1)
        reg_sha = hashlib.sha256(
            ws.registry_path.read_bytes()).hexdigest()
        self.assertEqual(m["registry_sha256"], reg_sha)
        run = m["run"]
        self.assertEqual(run["phase"], "WORKING")
        self.assertEqual(run["platform"], "claude_code")
        self.assertEqual(run["visibility"], "private")
        self.assertEqual(run["path_enforcement_level"], "MONITORED")
        self.assertIsNone(run["completion_status"])
        self.assertIsNone(run["validation_result"])
        self.assertEqual(run["assurance_level"], "SINGLE_CONTEXT")
        self.assertEqual(run["review_mode"], "self_review")
        self.assertEqual(run["run_root"], rel.as_posix())
        comp = m["company"]
        self.assertEqual(comp["name"], "评测公司")
        self.assertEqual(comp["codes"], ["600519"])
        self.assertEqual(comp["as_of"], "2026-07-17")
        self.assertEqual(comp["timezone"], "Asia/Shanghai")
        self.assertIsNone(comp["industry"])
        self.assertEqual(len(m["skills"]), 20)
        for i, sk in enumerate(m["skills"], start=1):
            self.assertEqual(sk["index"], i)
            self.assertEqual(sk["execution_state"], "PENDING")
            self.assertIsNone(sk["computed_status"])
            self.assertEqual(sk["independent_context_count"], 0)
            self.assertEqual(sk["assigned_artifact_paths"], [artifact_path(i)])
            spec_sha = hashlib.sha256(
                (ws.repo / f"skills/sk{i:02d}.md").read_bytes()).hexdigest()
            self.assertEqual(sk["spec_sha256"], spec_sha)
            for lst in ("artifacts", "facts", "calculations", "judgments",
                        "role_runs", "command_receipts", "limitations", "audit", "attempts",
                        "violations"):
                self.assertEqual(sk[lst], [])
        self.assertEqual(m["annotations"], {})
        # watchlist: ~ 展开到临时 HOME 的精确候选 + {period} 参数化候选记父目录
        wl = m["watchlist"]
        exact = [e for e in wl if e["pattern"].startswith("~/")]
        self.assertEqual(len(exact), 1)
        self.assertEqual(exact[0]["kind"], "exact")
        self.assertEqual(
            exact[0]["watch_path"],
            str(ws.home / "评测公司投资研究报告_20260717.md"))
        self.assertFalse(exact[0]["snapshot"]["exists"])
        param = [e for e in wl if "{period}" in e["pattern"]]
        self.assertEqual(len(param), 1)
        self.assertEqual(param[0]["kind"], "parameterized")
        self.assertEqual(Path(param[0]["watch_path"]).resolve(),
                         (ws.repo / "reports").resolve())

    def test_monitored_constant_for_both_platforms(self):
        for platform in ("codex", "claude_code"):
            ws = self.make_ws()
            cp = ws.init(platform=platform)
            self.assertEqual(cp.returncode, 0, out(cp))
            self.assertNotIn("已预防越界", out(cp))
            data = json.loads(cp.stdout)
            m = ws.manifest(data["run_root"])
            self.assertEqual(m["run"]["path_enforcement_level"], "MONITORED")


# ---------------------------------------------------------------------------
# #3 路径 gate 独立函数逐条测 (§8.2)
# ---------------------------------------------------------------------------
class TestPathGate(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.root = base / "run"
        (self.root / "01-数据与快筛").mkdir(parents=True)
        self.outside = base / "外部"
        self.outside.mkdir()
        self.assigned = ["01-数据与快筛/01-sk01.md"]
        self.art = self.root / "01-数据与快筛" / "01-sk01.md"
        self.art.write_text(ARTIFACT_TEXT, encoding="utf-8")

    def gate_errors(self, cand, assigned=None):
        return gate_mod.path_gate(self.root, cand,
                                  self.assigned if assigned is None
                                  else assigned)

    def test_valid_artifact_passes(self):
        self.assertEqual(self.gate_errors("01-数据与快筛/01-sk01.md"), [])

    def test_rejects_absolute_path(self):
        target = str(self.outside / "abs.md")
        Path(target).write_text("x", encoding="utf-8")
        errs = self.gate_errors(target)
        self.assertTrue(errs)
        self.assertTrue(any("绝对路径" in e for e in errs), errs)

    def test_rejects_dotdot_and_dot_and_empty_segment(self):
        for cand in ("../01-sk01.md", "./01-数据与快筛/01-sk01.md",
                     "01-数据与快筛//01-sk01.md"):
            errs = self.gate_errors(cand)
            self.assertTrue(errs, cand)

    def test_rejects_control_chars(self):
        errs = self.gate_errors("01-数据与快筛/\x01bad.md")
        self.assertTrue(any("控制字符" in e for e in errs), errs)

    def test_rejects_symlink_candidate(self):
        real = self.outside / "real.md"
        real.write_text(ARTIFACT_TEXT, encoding="utf-8")
        self.art.unlink()
        self.art.symlink_to(real)
        errs = self.gate_errors("01-数据与快筛/01-sk01.md")
        self.assertTrue(any("软链接" in e or "symlink" in e for e in errs),
                        errs)

    def test_rejects_escape_via_symlink_dir(self):
        # run_root 内目录软链指向外部 → resolve 后越出 root_real
        (self.root / "逃逸").symlink_to(self.outside)
        leak = self.outside / "leak.md"
        leak.write_text(ARTIFACT_TEXT, encoding="utf-8")
        errs = self.gate_errors("逃逸/leak.md", assigned=["逃逸/leak.md"])
        self.assertTrue(errs)

    def test_rejects_unassigned_path(self):
        extra = self.root / "01-数据与快筛" / "99-extra.md"
        extra.write_text(ARTIFACT_TEXT, encoding="utf-8")
        errs = self.gate_errors("01-数据与快筛/99-extra.md")
        self.assertTrue(any("未分配" in e for e in errs), errs)

    def test_rejects_hardlink(self):
        os.link(self.art, self.root / "01-数据与快筛" / "hl.md")
        errs = self.gate_errors("01-数据与快筛/01-sk01.md")
        self.assertTrue(any("硬链接" in e for e in errs), errs)

    def test_rejects_empty_file(self):
        self.art.write_text("", encoding="utf-8")
        errs = self.gate_errors("01-数据与快筛/01-sk01.md")
        self.assertTrue(any("空文件" in e for e in errs), errs)

    def test_rejects_missing_file(self):
        self.art.unlink()
        self.assertTrue(self.gate_errors("01-数据与快筛/01-sk01.md"))

    def test_rejects_symlink_run_root(self):
        link_root = Path(self.tmp.name) / "link-root"
        link_root.symlink_to(self.root)
        errs = gate_mod.path_gate(link_root, "01-数据与快筛/01-sk01.md",
                                  self.assigned)
        self.assertTrue(errs)

    def test_rejects_cross_device(self):
        self.skipTest("测试环境无法在 run_root 内构造跨设备 (st_dev 不同) 路径")


# ---------------------------------------------------------------------------
# #5 状态机 + 封闭 evidence schema
# ---------------------------------------------------------------------------
class TestSkillStateMachine(GateTestCase):

    def setUp(self):
        super().setUp()
        self.ws = self.make_ws()
        self.run_root, _ = self.ws.init_ok()

    def test_begin_unknown_skill_exit_2(self):
        cp = self.ws.begin(self.run_root, "不存在的skill")
        self.assertEqual(cp.returncode, 2, out(cp))

    def test_begin_finish_roundtrip_then_rebegin_complete_exit_2(self):
        cp = self.ws.begin(self.run_root, "sk01",
                           extra=("--execution-mode", "sequential",
                                  "--independent-context-count", "0"))
        self.assertEqual(cp.returncode, 0, out(cp))
        m = self.ws.manifest(self.run_root)
        sk = m["skills"][0]
        self.assertEqual(sk["execution_state"], "RUNNING")
        self.assertEqual(len(sk["attempts"]), 1)
        att = sk["attempts"][0]
        for key in ("attempt_id", "started_at", "execution_mode",
                    "assigned_artifact_paths"):
            self.assertIn(key, att)
        self.assertEqual(att["assigned_artifact_paths"], [artifact_path(1)])
        art = artifact_path(1)
        self.ws.write_artifact(self.run_root, art)
        cp = self.ws.finish(self.run_root, "sk01", artifacts=[art])
        self.assertEqual(cp.returncode, 0, out(cp))
        m = self.ws.manifest(self.run_root)
        self.assertEqual(m["skills"][0]["execution_state"], "COMPLETE")
        self.assertEqual(m["skills"][0]["artifacts"], [art])
        # COMPLETE 未失效 → 再 begin exit 2
        cp = self.ws.begin(self.run_root, "sk01")
        self.assertEqual(cp.returncode, 2, out(cp))

    def test_finish_without_begin_exit_2(self):
        cp = self.ws.finish(self.run_root, "sk01")
        self.assertEqual(cp.returncode, 2, out(cp))

    def test_finish_unassigned_artifact_exit_2(self):
        self.assertEqual(self.ws.begin(self.run_root, "sk01").returncode, 0)
        cp = self.ws.finish(self.run_root, "sk01",
                            artifacts=["01-数据与快筛/99-别人的.md"])
        self.assertEqual(cp.returncode, 2, out(cp))

    def test_evidence_closed_schema_rejects_status_keys(self):
        self.assertEqual(self.ws.begin(self.run_root, "sk01").returncode, 0)
        for bad in ({"computed_status": "PASS"},
                    {"status": "PASS"},
                    {"counts": {"facts": 3}},
                    {"assurance": "INDEPENDENT"},
                    {"facts": [{"fact_id": "f1", "status": "DUAL_SOURCE"}]},
                    {"facts": [{"fact_id": "f1",
                                "computed_status": "DUAL_SOURCE"}]}):
            cp = self.ws.finish(self.run_root, "sk01", evidence=bad)
            self.assertEqual(cp.returncode, 2, (bad, out(cp)))

    def test_evidence_allowed_keys_recorded(self):
        self.assertEqual(self.ws.begin(self.run_root, "sk01").returncode, 0)
        art = artifact_path(1)
        self.ws.write_artifact(self.run_root, art)
        ev = {"judgments": [_judgment("j1")],
              "limitations": [{"code": "synthetic_note", "note": "测试"}]}
        cp = self.ws.finish(self.run_root, "sk01", artifacts=[art],
                            evidence=ev)
        self.assertEqual(cp.returncode, 0, out(cp))
        sk = self.ws.manifest(self.run_root)["skills"][0]
        self.assertEqual(sk["judgments"], ev["judgments"])
        self.assertEqual(sk["limitations"], ev["limitations"])


# ---------------------------------------------------------------------------
# checkpoint 中间验证
# ---------------------------------------------------------------------------
class TestCheckpoint(GateTestCase):

    def setUp(self):
        super().setUp()
        self.ws = self.make_ws()
        self.run_root, _ = self.ws.init_ok()

    def test_checkpoint_clean_run_exit_0_despite_stray_tmp(self):
        # 原子写残留 .tmp 不影响读取
        (self.run_root / "manifest.json.tmp-99999").write_text(
            "{垃圾", encoding="utf-8")
        cp = self.ws.checkpoint(self.run_root)
        self.assertEqual(cp.returncode, 0, out(cp))

    def test_checkpoint_bad_artifact_lists_all_problems_exit_1(self):
        # sk01: 太小且缺章节; sk02: 声明了产物但文件不存在
        self.assertEqual(self.ws.begin(self.run_root, "sk01").returncode, 0)
        self.ws.write_artifact(self.run_root, artifact_path(1), text="短")
        cp = self.ws.finish(self.run_root, "sk01",
                            artifacts=[artifact_path(1)])
        self.assertEqual(cp.returncode, 0, out(cp))
        self.assertEqual(self.ws.begin(self.run_root, "sk02").returncode, 0)
        cp = self.ws.finish(self.run_root, "sk02",
                            artifacts=[artifact_path(2)])
        self.assertEqual(cp.returncode, 0, out(cp))
        cp = self.ws.checkpoint(self.run_root)
        self.assertEqual(cp.returncode, 1, out(cp))
        self.assertIn("sk01", out(cp))
        self.assertIn("sk02", out(cp))

    def test_checkpoint_accepts_valid_negative_acceptance_without_normal_artifact(self):
        registry = make_registry()
        registry["skills"][0]["applicability_rule"] = {
            "predicate_id": "is_a_share",
            "alternative": "negative-acceptance",
        }
        ws = self.make_ws(registry=registry)
        run_root, _ = ws.init_ok()
        self.assertEqual(ws.begin(run_root, "sk01").returncode, 0)
        fact = _fact("f1", [_src("source-a", "chain-a", "0")], value="0")
        evidence = {
            "facts": [fact],
            "limitations": [{
                "code": "not_applicable",
                "predicate_id": "is_a_share",
                "input_facts": ["f1"],
                "alternative": "negative-acceptance",
            }],
        }
        cp = ws.finish(run_root, "sk01", evidence=evidence)
        self.assertEqual(cp.returncode, 0, out(cp))
        neg = run_root / "06-负向验收" / "01-sk01.md"
        neg.write_text(
            "# 负向验收\n\npredicate_id: is_a_share\n"
            "input_facts: f1\nalternative: negative-acceptance\n",
            encoding="utf-8",
        )

        cp = ws.checkpoint(run_root)

        self.assertEqual(cp.returncode, 0, out(cp))
        self.assertIn("checkpoint 通过", cp.stdout)
        ws.complete_all(run_root, skip=(1,))
        cp = ws.finalize(run_root)
        self.assertEqual(cp.returncode, 0, out(cp))
        self.assertEqual(
            read_result(run_root)["matrix"][0]["computed_status"],
            "NOT_APPLICABLE_PASS",
        )

    def test_checkpoint_rejects_negative_acceptance_without_negative_artifact(self):
        registry = make_registry()
        registry["skills"][0]["applicability_rule"] = {
            "predicate_id": "is_a_share",
            "alternative": "negative-acceptance",
        }
        ws = self.make_ws(registry=registry)
        run_root, _ = ws.init_ok()
        self.assertEqual(ws.begin(run_root, "sk01").returncode, 0)
        fact = _fact("f1", [_src("source-a", "chain-a", "0")], value="0")
        evidence = {
            "facts": [fact],
            "limitations": [{
                "code": "not_applicable",
                "predicate_id": "is_a_share",
                "input_facts": ["f1"],
                "alternative": "negative-acceptance",
            }],
        }
        cp = ws.finish(run_root, "sk01", evidence=evidence)
        self.assertEqual(cp.returncode, 0, out(cp))

        cp = ws.checkpoint(run_root)

        self.assertEqual(cp.returncode, 1, out(cp))
        self.assertIn("N/A 缺负向验收产物", cp.stdout)


# ---------------------------------------------------------------------------
# finalize 状态门 (#5 余下部分)
# ---------------------------------------------------------------------------
def read_result(run_root):
    return json.loads(
        (Path(run_root) / "evidence" / "04-验收器结果.json")
        .read_text(encoding="utf-8"))


class TestFinalizeStateGates(GateTestCase):

    def test_finalize_with_pending_exit_2(self):
        ws = self.make_ws()
        run_root, _ = ws.init_ok()
        cp = ws.finalize(run_root)
        self.assertEqual(cp.returncode, 2, out(cp))
        self.assertIn("PENDING", out(cp))

    def test_blocked_skill_computed_fail_finalize_exit_1(self):
        ws = self.make_ws()
        run_root, _ = ws.init_ok()
        ws.complete_all(run_root, skip=(20,))
        self.assertEqual(ws.begin(run_root, "sk20").returncode, 0)
        cp = ws.finish(run_root, "sk20", state="BLOCKED")
        self.assertEqual(cp.returncode, 0, out(cp))
        cp = ws.finalize(run_root)
        self.assertEqual(cp.returncode, 1, out(cp))
        res = read_result(run_root)
        row = [r for r in res["matrix"] if r["index"] == 20][0]
        self.assertEqual(row["computed_status"], "FAIL")
        self.assertEqual(res["validation_result"], "FAIL")
        m = ws.manifest(run_root)
        self.assertEqual(m["skills"][19]["computed_status"], "FAIL")


# ---------------------------------------------------------------------------
# #4 全流程闭环 + summary
# ---------------------------------------------------------------------------
class TestFullFlow(GateTestCase):

    def test_full_flow_20_skills_pass(self):
        ws = self.make_ws()
        run_root, data = ws.init_ok()
        ws.complete_all(run_root)
        cp = ws.checkpoint(run_root)
        self.assertEqual(cp.returncode, 0, out(cp))
        cp = ws.finalize(run_root)
        self.assertEqual(cp.returncode, 0, out(cp))
        self.assertNotIn("已预防越界", out(cp))
        res = read_result(run_root)
        self.assertEqual(res["completion_status"], "COMPLETE")
        self.assertEqual(res["validation_result"], "PASS")
        self.assertEqual(res["assurance_level"], "SINGLE_CONTEXT")
        self.assertEqual(res["review_mode"], "self_review")
        self.assertIsNone(res["industry"])
        self.assertEqual(len(res["matrix"]), 20)
        self.assertTrue(all(r["computed_status"] == "PASS"
                            for r in res["matrix"]))
        m = ws.manifest(run_root)
        self.assertEqual(m["run"]["completion_status"], "COMPLETE")
        self.assertEqual(m["run"]["validation_result"], "PASS")
        self.assertEqual(m["run"]["assurance_level"], "SINGLE_CONTEXT")
        self.assertTrue(all(sk["computed_status"] == "PASS"
                            for sk in m["skills"]))
        # 锁归档 + 活动锁删除
        self.assertFalse(
            (run_root / "evidence" / ".full-analysis.lock").exists())
        released = list((run_root / "evidence" / "locks")
                        .glob("*-released.json"))
        self.assertEqual(len(released), 1)
        # summary 三轴 + 20 行矩阵
        cp = ws.summary(run_root)
        self.assertEqual(cp.returncode, 0, out(cp))
        self.assertIn("completion_status=COMPLETE", cp.stdout)
        self.assertIn("validation_result=PASS\n", cp.stdout + "\n")
        self.assertIn("assurance_level=SINGLE_CONTEXT", cp.stdout)
        self.assertIn("review_mode=self_review", cp.stdout)
        self.assertIn("industry=null", cp.stdout)
        rows = re.findall(r"(?m)^\s*\d{2}\s+sk\d{2}\s+", cp.stdout)
        self.assertEqual(len(rows), 20)

    def test_non_git_full_flow_completes_with_explicit_audit_limitation(self):
        ws = self.make_ws(git=False)
        cp = ws.gate("init", "--registry", ws.registry_path,
                     "--company", "评测公司", "--platform", "claude_code",
                     "--as-of", "2026-07-17", "--repo-root", ws.repo)
        self.assertEqual(cp.returncode, 0, out(cp))
        run_root = Path(json.loads(cp.stdout)["run_root"])
        ws.complete_all(run_root)

        cp = ws.finalize(run_root)
        self.assertEqual(cp.returncode, 0, out(cp))
        result = read_result(run_root)
        self.assertEqual(result["completion_status"], "COMPLETE")
        self.assertEqual(result["validation_result"],
                         "PASS_WITH_LIMITATIONS")
        self.assertEqual(result["workspace_audit_mode"], "none")
        self.assertTrue(any("Git" in cap for cap in result["run_caps"]),
                        result["run_caps"])

        cp = ws.summary(run_root)
        self.assertEqual(cp.returncode, 0, out(cp))
        self.assertIn("workspace_audit_mode=none", cp.stdout)
        self.assertIn("运行级限制", cp.stdout)

    def test_summary_without_result_file_exit_2(self):
        ws = self.make_ws()
        run_root, _ = ws.init_ok()
        cp = ws.summary(run_root)
        self.assertEqual(cp.returncode, 2, out(cp))

    def test_pwl_multi_context_skill_single_context_caps_pwl(self):
        """min_ctx=2 + PWL 的 skill 在单上下文 (count=0) 下:
        不进 N/A, 产物照验, computed_status=PASS_WITH_LIMITATIONS, 且不 credit INDEPENDENT。"""
        role_rules = {5: {"required_roles": [],
                          "min_independent_contexts": 2,
                          "sequential_cap": "PASS_WITH_LIMITATIONS"}}
        ws = GateWorkspace(registry=make_registry(role_rules=role_rules))
        self.addCleanup(ws.cleanup)
        run_root, _ = ws.init_ok()
        # 其余 19 项走默认 PASS; sk05 单上下文 (不传 independent-context-count)
        for sk in ws.manifest(run_root)["skills"]:
            name = sk["name"]
            path = sk["assigned_artifact_paths"][0]
            ws.begin(run_root, name)
            ws.write_artifact(run_root, path)
            ws.finish(run_root, name, artifacts=[path])
        cp = ws.gate("finalize", "--registry", ws.registry_path,
                     "--run-root", run_root)
        self.assertEqual(cp.returncode, 0, f"{cp.stdout}\n{cp.stderr}")
        m = ws.manifest(run_root)
        sk05 = next(s for s in m["skills"] if s["name"] == "sk05")
        self.assertEqual(sk05["computed_status"], "PASS_WITH_LIMITATIONS")
        # sk05 min_ctx=2 但 count=0 → 不满足 → assurance 不得为 INDEPENDENT
        self.assertIn(m["run"]["assurance_level"], ("MIXED", "SINGLE_CONTEXT"))


class TestRunContextCommands(GateTestCase):

    def test_independent_review_mode_requires_recorded_independent_context(self):
        ws = self.make_ws()
        run_root, _ = ws.init_ok()
        cp = ws.set_review_mode(run_root, "independent_context")
        self.assertEqual(cp.returncode, 1, out(cp))
        self.assertEqual(ws.manifest(run_root)["run"]["review_mode"],
                         "self_review")

    def test_user_review_mode_is_recorded_by_gate(self):
        ws = self.make_ws()
        run_root, _ = ws.init_ok()
        cp = ws.set_review_mode(run_root, "user")
        self.assertEqual(cp.returncode, 0, out(cp))
        self.assertEqual(ws.manifest(run_root)["run"]["review_mode"], "user")

    def _run_with_industry_fact(self):
        ws = self.make_ws()
        run_root, _ = ws.init_ok()
        ws.complete_skill(
            run_root, 1,
            evidence={"facts": [_fact(
                "f-industry", [_src("年报", "filing-chain", "100")])]})
        return ws, run_root

    def test_set_industry_computes_primary_scope_at_50_percent(self):
        ws, run_root = self._run_with_industry_fact()
        cp = ws.set_industry(run_root, {
            "basis": "latest_fy_revenue_or_operating_income",
            "period": "2025FY",
            "source_fact_ids": ["f-industry"],
            "segments": [
                {"label": "银行", "revenue_share_pct": "60"},
                {"label": "财富管理", "revenue_share_pct": "40"},
            ],
        })
        self.assertEqual(cp.returncode, 0, out(cp))
        industry = ws.manifest(run_root)["company"]["industry"]
        self.assertEqual(industry["scope_type"], "primary")
        self.assertEqual(industry["labels"], ["银行"])
        self.assertEqual(industry["source_fact_ids"], ["f-industry"])

    def test_set_industry_computes_multi_segment_to_80_percent(self):
        ws, run_root = self._run_with_industry_fact()
        cp = ws.set_industry(run_root, {
            "basis": "latest_fy_revenue_or_operating_income",
            "period": "2025FY",
            "source_fact_ids": ["f-industry"],
            "segments": [
                {"label": "板块A", "revenue_share_pct": "45"},
                {"label": "板块B", "revenue_share_pct": "35"},
                {"label": "板块C", "revenue_share_pct": "20"},
            ],
        })
        self.assertEqual(cp.returncode, 0, out(cp))
        industry = ws.manifest(run_root)["company"]["industry"]
        self.assertEqual(industry["scope_type"], "multi_segment")
        self.assertEqual(industry["labels"], ["板块A", "板块B"])

    def test_set_industry_rejects_dangling_source_fact(self):
        ws = self.make_ws()
        run_root, _ = ws.init_ok()
        cp = ws.set_industry(run_root, {
            "basis": "latest_fy_revenue_or_operating_income",
            "period": "2025FY",
            "source_fact_ids": ["missing-fact"],
            "segments": [{"label": "银行", "revenue_share_pct": "100"}],
        })
        self.assertEqual(cp.returncode, 1, out(cp))
        self.assertIsNone(ws.manifest(run_root)["company"]["industry"])


# ---------------------------------------------------------------------------
# #6 fact 双源独立性判定
# ---------------------------------------------------------------------------
def _src(pub, chain, obs, **over):
    d = {"publisher_id": pub, "acquisition_chain_id": chain,
         "source_type": "filing", "url": "https://example.invalid/doc",
         "observed_value": obs, "accessed_at": "2026-07-17"}
    d.update(over)
    return d


def _fact(fid, sources, value="100", tol="2"):
    return {"fact_id": fid, "field": "revenue", "subject": "评测公司",
            "period": "2025FY", "unit": "CNY", "value": value,
            "tolerance_pct": tol, "sources": sources}


def _judgment(jid, rule_id="synthetic-rule"):
    return {
        "judgment_id": jid,
        "rule_id": rule_id,
        "conclusion": "合成判断",
        "confidence": "medium",
        "falsification_condition": "若关键指标恶化则证伪",
        "fact_ids": [],
        "calculation_ids": [],
        "artifact_sections": ["合成章节"],
    }


def _role(role, artifact=None, context_id="ctx-1"):
    return {
        "role": role,
        "context_id": context_id,
        "execution_mode": "synthetic_context",
        "artifact_paths": [artifact or artifact_path(1)],
        "started_at": "2026-07-17T12:00:00+08:00",
        "finished_at": "2026-07-17T12:00:01+08:00",
    }


class TestFactClassification(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.ws = GateWorkspace()
        cls.addClassCleanup(cls.ws.cleanup)
        run_root, _ = cls.ws.init_ok()
        cls.run_root = run_root
        facts = [
            _fact("f_dual", [_src("巨潮", "chain-a", "100"),
                             _src("东财", "chain-b", "101")]),
            _fact("f_single", [_src("巨潮", "chain-a", "100")]),
            _fact("f_conflict", [_src("巨潮", "chain-a", "100"),
                                 _src("东财", "chain-b", "150")]),
            _fact("f_unavailable", []),
            _fact("f_same_publisher", [_src("巨潮", "chain-a", "100"),
                                       _src("巨潮", "chain-b", "101")]),
            _fact("f_empty_chain", [_src("巨潮", "", "100"),
                                    _src("东财", "chain-b", "101")]),
            _fact("f_period_mismatch",
                  [_src("巨潮", "chain-a", "100"),
                   _src("东财", "chain-b", "999", period="2024FY")]),
        ]
        cls.ws.complete_all(run_root, evidence={1: {"facts": facts}})
        cls.finalize_cp = cls.ws.finalize(run_root)
        m = cls.ws.manifest(run_root)
        cls.by_id = {f["fact_id"]: f for f in m["skills"][0]["facts"]}

    def status(self, fid):
        return self.by_id[fid]["computed_status"]

    def test_dual_source(self):
        self.assertEqual(self.status("f_dual"), "DUAL_SOURCE")

    def test_single_source(self):
        self.assertEqual(self.status("f_single"), "SINGLE_SOURCE")

    def test_conflict(self):
        self.assertEqual(self.status("f_conflict"), "CONFLICT")

    def test_unavailable(self):
        self.assertEqual(self.status("f_unavailable"), "UNAVAILABLE")

    def test_same_publisher_not_dual(self):
        self.assertEqual(self.status("f_same_publisher"), "SINGLE_SOURCE")

    def test_empty_chain_not_dual(self):
        self.assertEqual(self.status("f_empty_chain"), "SINGLE_SOURCE")

    def test_period_mismatch_source_excluded(self):
        self.assertEqual(self.status("f_period_mismatch"), "SINGLE_SOURCE")

    def test_tushare_match_is_an_independent_second_chain(self):
        fact = _fact("f-tushare", [
            _src("东方财富", "eastmoney-http", "100"),
            _src("Tushare", "tushare-api", "100.5"),
        ], value="100", tol="1")
        self.assertEqual(gate_mod.classify_fact(fact), "DUAL_SOURCE")

    def test_tushare_conflict_remains_fail_closed(self):
        fact = _fact("f-tushare-conflict", [
            _src("东方财富", "eastmoney-http", "100"),
            _src("Tushare", "tushare-api", "102"),
        ], value="100", tol="1")
        self.assertEqual(gate_mod.classify_fact(fact), "CONFLICT")

    def test_tushare_selected_market_fact_excludes_superseded_primary_from_conflict(self):
        fact = _fact("f-tushare-market-precedence", [
            _src("腾讯", "tencent-http", "0.87",
                 source_type="market_data",
                 precedence_status="superseded_by_tushare"),
            _src("Tushare", "tushare-api", "0.8468",
                 source_type="market_data"),
        ], value="0.8468", tol="1")
        fact["field"] = "pb"
        fact["data_domain"] = "market_structured"

        self.assertEqual(gate_mod.classify_fact(fact), "SINGLE_SOURCE")

    def test_missing_fact_scope_cannot_be_dual_source(self):
        fact = {
            "fact_id": "f-no-scope", "field": "revenue", "value": "100",
            "tolerance_pct": "2",
            "sources": [_src("巨潮", "chain-a", "100"),
                        _src("东财", "chain-b", "100")],
        }
        self.assertNotEqual(gate_mod.classify_fact(fact), "DUAL_SOURCE")

    def test_conflict_fact_fails_skill(self):
        self.assertEqual(self.finalize_cp.returncode, 1,
                         out(self.finalize_cp))
        res = read_result(self.run_root)
        row = [r for r in res["matrix"] if r["index"] == 1][0]
        self.assertEqual(row["computed_status"], "FAIL")


# ---------------------------------------------------------------------------
# #6a 市场字段 Tushare 优先的证据 schema
# ---------------------------------------------------------------------------
class TestMarketPrecedenceEvidenceSchema(GateTestCase):

    def test_market_fact_accepts_explicit_tushare_supersession_metadata(self):
        ws = self.make_ws()
        run_root, _ = ws.init_ok()
        cp = ws.begin(run_root, "sk01")
        self.assertEqual(cp.returncode, 0, out(cp))
        artifact = artifact_path(1)
        ws.write_artifact(run_root, artifact)
        fact = _fact("f-market-precedence", [
            _src("腾讯", "tencent-http", "0.87",
                 source_type="market_data",
                 precedence_status="superseded_by_tushare"),
            _src("Tushare", "tushare-api", "0.8468",
                 source_type="market_data"),
        ], value="0.8468", tol="1")
        fact["field"] = "pb"
        fact["data_domain"] = "market_structured"

        cp = ws.finish(run_root, "sk01", artifacts=[artifact],
                       evidence={"facts": [fact]})

        self.assertEqual(cp.returncode, 0, out(cp))


# ---------------------------------------------------------------------------
# #10 计算重放 (真实 tools/financial_rigor.py)
# ---------------------------------------------------------------------------
class TestCalcReplay(GateTestCase):

    def _flow(self, calcs):
        ws = self.make_ws()
        run_root, _ = ws.init_ok()
        ws.complete_all(run_root, evidence={1: {"calculations": calcs}})
        return ws, run_root, ws.finalize(run_root)

    def test_valid_replay_passes(self):
        calcs = [
            {"calculation_id": "c-calc", "type": "calc",
             "args": {"expr": "3*4+1"},
             "expected": {"outcome": "PASS", "is_pass": True, "exit_code": 0,
                          "result": {"expression": "3*4+1", "value": "13"}}},
            {"calculation_id": "c-mcap", "type": "verify-market-cap",
             "args": {"price": "10", "shares": "100", "reported": "1000",
                      "currency": "CNY"},
             "expected": {"outcome": "PASS", "is_pass": True, "exit_code": 0,
                          "result": {"calculated_market_cap": "1000",
                                     "reported_market_cap": "1000",
                                     "band": "PASS", "deviation_pct": "0"}}},
        ]
        ws, run_root, cp = self._flow(calcs)
        self.assertEqual(cp.returncode, 0, out(cp))
        res = read_result(run_root)
        self.assertEqual(res["matrix"][0]["computed_status"], "PASS")

    def test_tampered_expected_result_fails(self):
        calcs = [{"calculation_id": "c-bad", "type": "calc",
                  "args": {"expr": "1+1"},
                  "expected": {"outcome": "PASS", "is_pass": True,
                               "exit_code": 0,
                               "result": {"expression": "1+1", "value": "3"}}}]
        ws, run_root, cp = self._flow(calcs)
        self.assertEqual(cp.returncode, 1, out(cp))
        res = read_result(run_root)
        row = res["matrix"][0]
        self.assertEqual(row["computed_status"], "FAIL")
        self.assertTrue(any("c-bad" in e for e in row["errors"]),
                        row["errors"])

    def test_non_allowlist_type_fails(self):
        calcs = [{"calculation_id": "c-evil", "type": "rm-rf",
                  "args": {"expr": "1"},
                  "expected": {"outcome": "PASS", "is_pass": True,
                               "exit_code": 0, "result": {}}}]
        ws, run_root, cp = self._flow(calcs)
        self.assertEqual(cp.returncode, 1, out(cp))
        res = read_result(run_root)
        self.assertEqual(res["matrix"][0]["computed_status"], "FAIL")

    def test_empty_expected_cannot_validate_replay(self):
        calcs = [{"calculation_id": "c-empty", "type": "calc",
                  "args": {"expr": "1/0"}, "expected": {}}]
        ws, run_root, cp = self._flow(calcs)
        self.assertEqual(cp.returncode, 1, out(cp))
        row = read_result(run_root)["matrix"][0]
        self.assertEqual(row["computed_status"], "FAIL")
        self.assertTrue(any("expected" in e for e in row["errors"]),
                        row["errors"])

    def test_error_outcome_cannot_count_as_successful_replay(self):
        calcs = [{
            "calculation_id": "c-divzero", "type": "calc",
            "args": {"expr": "1/0"},
            "expected": {"outcome": "ERROR", "is_pass": None,
                         "exit_code": 1,
                         "result": {"expression": "1/0", "value": None}},
        }]
        ws, run_root, cp = self._flow(calcs)
        self.assertEqual(cp.returncode, 1, out(cp))
        row = read_result(run_root)["matrix"][0]
        self.assertEqual(row["computed_status"], "FAIL")
        self.assertTrue(any("PASS" in e for e in row["errors"]), row["errors"])


# ---------------------------------------------------------------------------
# #11 git 越界侦测 + watchlist 即时比对
# ---------------------------------------------------------------------------
class TestBoundaryDetection(GateTestCase):

    def test_preexisting_dirty_file_no_false_positive(self):
        ws = self.make_ws()
        (ws.repo / "既有脏文件.md").write_text("init 之前就存在\n",
                                              encoding="utf-8")
        run_root, _ = ws.init_ok()
        ws.complete_all(run_root)
        cp = ws.finalize(run_root)
        self.assertEqual(cp.returncode, 0, out(cp))

    def test_preexisting_untracked_file_content_change_is_boundary_failure(self):
        ws = self.make_ws()
        dirty = ws.repo / "既有脏文件.md"
        dirty.write_text("init 之前的内容\n", encoding="utf-8")
        run_root, _ = ws.init_ok()
        ws.complete_all(run_root)
        dirty.write_text("init 之后被改写\n", encoding="utf-8")
        cp = ws.finalize(run_root)
        self.assertEqual(cp.returncode, 1, out(cp))
        self.assertIn("既有脏文件.md", out(cp))
        self.assertIn("内容", out(cp))

    def test_new_repo_file_and_index_change_boundary_fail(self):
        ws = self.make_ws()
        (ws.repo / "reports").mkdir()
        (ws.repo / "reports" / "INDEX.md").write_text("# 索引\n",
                                                      encoding="utf-8")
        ws.git("add", "reports/INDEX.md")
        ws.git("commit", "-q", "-m", "索引基线")
        run_root, _ = ws.init_ok()
        ws.complete_all(run_root)
        (ws.repo / "越界新文件.md").write_text("运行中越界写入\n",
                                              encoding="utf-8")
        (ws.repo / "reports" / "INDEX.md").write_text("# 索引\n被改了\n",
                                                      encoding="utf-8")
        cp = ws.finalize(run_root)
        self.assertEqual(cp.returncode, 1, out(cp))
        self.assertIn("越界新文件.md", out(cp))
        self.assertIn("INDEX.md", out(cp))
        res = read_result(run_root)
        self.assertEqual(res["validation_result"], "FAIL")
        self.assertTrue(res["run_errors"])

    def test_home_legacy_output_blocks_begin_skill(self):
        legacy = {1: ["~/{company}投资研究报告_{date}.md"]}
        ws = self.make_ws(registry=make_registry(legacy=legacy))
        run_root, _ = ws.init_ok()
        (ws.home / "评测公司投资研究报告_20260717.md").write_text(
            "越界旧习惯输出\n", encoding="utf-8")
        cp = ws.begin(run_root, "sk02")
        self.assertEqual(cp.returncode, 1, out(cp))
        m = ws.manifest(run_root)
        sk02 = m["skills"][1]
        self.assertEqual(sk02["execution_state"], "BLOCKED")
        self.assertTrue(sk02["violations"])
        self.assertEqual(sk02["violations"][0]["type"], "watchlist_change")

    def test_parameterized_home_watch_ignores_unrelated_activity(self):
        # 真跑回归: 参数化 legacy 模式 (占位符在首段) 退化到监视 HOME 根时,
        # 不得因无关文件 (缓存/会话/历史) 触碰 HOME 改变目录 mtime 而误 BLOCK;
        # 只有新增匹配该 skill 输出正则的文件才算越界。活机器上 HOME 一直在变。
        legacy = {1: ["~/{industry}产业链投资研究报告.md"]}
        ws = self.make_ws(registry=make_registry(legacy=legacy))
        run_root, _ = ws.init_ok()
        # 无关活动: HOME 下新建不匹配正则的文件 (模拟缓存/会话写)
        (ws.home / ".unrelated_cache_20260718").write_text(
            "x\n", encoding="utf-8")
        (ws.home / "随手草稿.txt").write_text("y\n", encoding="utf-8")
        cp = ws.begin(run_root, "sk02")
        self.assertEqual(cp.returncode, 0, out(cp))
        m = ws.manifest(run_root)
        self.assertEqual(m["skills"][1]["execution_state"], "RUNNING")
        self.assertEqual(m["skills"][1]["violations"], [])

    def test_parameterized_home_watch_blocks_matching_legacy_write(self):
        # 回归对偶: 真正写出匹配 skill 输出正则的文件 → 必须侦测并 BLOCK。
        legacy = {1: ["~/{industry}产业链投资研究报告.md"]}
        ws = self.make_ws(registry=make_registry(legacy=legacy))
        run_root, _ = ws.init_ok()
        (ws.home / "新能源产业链投资研究报告.md").write_text(
            "越界的行业研究旧习惯输出\n", encoding="utf-8")
        cp = ws.begin(run_root, "sk02")
        self.assertEqual(cp.returncode, 1, out(cp))
        m = ws.manifest(run_root)
        self.assertEqual(m["skills"][1]["execution_state"], "BLOCKED")
        self.assertTrue(m["skills"][1]["violations"])
        self.assertEqual(m["skills"][1]["violations"][0]["type"],
                         "watchlist_change")

    def test_parameterized_reports_watch_ignores_index_rebuild(self):
        # 回归: reports/ 参数化项不得因 INDEX.md / 无关报告变化误报
        # (build_report_index --check 场景); 只有匹配 {industry}-industry 才算。
        legacy = {1: ["reports/{industry}-industry-{date}.md"]}
        ws = self.make_ws(registry=make_registry(legacy=legacy))
        run_root, _ = ws.init_ok()
        reports = ws.repo / "reports"
        reports.mkdir(exist_ok=True)
        (reports / "INDEX.md").write_text("# 索引\n无关重建\n", encoding="utf-8")
        (reports / "别的公司-research-20260718.md").write_text(
            "无关报告\n", encoding="utf-8")
        cp = ws.begin(run_root, "sk02")
        self.assertEqual(cp.returncode, 0, out(cp))
        # 写出匹配 {industry}-industry-{date} 的文件 → 应 BLOCK
        (reports / "证券-industry-20260717.md").write_text(
            "越界行业报告\n", encoding="utf-8")
        cp2 = ws.begin(run_root, "sk03")
        self.assertEqual(cp2.returncode, 1, out(cp2))


# ---------------------------------------------------------------------------
# #14 审计策略 + 隐私扫描
# ---------------------------------------------------------------------------
class TestAuditAndPrivacy(GateTestCase):

    def _flow(self, policy, audit_records):
        ws = self.make_ws(registry=make_registry(audit_policies={1: policy}))
        run_root, _ = ws.init_ok()
        evidence = {1: {"audit": audit_records}} if audit_records is not None \
            else {}
        ws.complete_all(run_root, evidence=evidence)
        return ws, run_root, ws.finalize(run_root)

    def test_required_insufficient_fail(self):
        ws, run_root, cp = self._flow(
            "required", [audit_record(artifact_path(1), "INSUFFICIENT")])
        self.assertEqual(cp.returncode, 1, out(cp))
        self.assertEqual(read_result(run_root)["matrix"][0]["computed_status"],
                         "FAIL")

    def test_required_missing_record_fail(self):
        ws, run_root, cp = self._flow("required", None)
        self.assertEqual(cp.returncode, 1, out(cp))
        self.assertEqual(read_result(run_root)["matrix"][0]["computed_status"],
                         "FAIL")

    def test_advisory_insufficient_caps_pwl(self):
        ws, run_root, cp = self._flow(
            "advisory", [audit_record(artifact_path(1), "INSUFFICIENT")])
        self.assertEqual(cp.returncode, 0, out(cp))
        res = read_result(run_root)
        self.assertEqual(res["matrix"][0]["computed_status"],
                         "PASS_WITH_LIMITATIONS")
        self.assertEqual(res["validation_result"], "PASS_WITH_LIMITATIONS")

    def test_missing_sample_results_fail(self):
        record = audit_record(artifact_path(1))
        record["results"] = []
        ws, run_root, cp = self._flow(
            "advisory", [record])
        self.assertEqual(cp.returncode, 1, out(cp))
        self.assertEqual(read_result(run_root)["matrix"][0]["computed_status"],
                         "FAIL")

    def test_required_audit_is_recomputed_from_artifact_and_passes(self):
        ws, run_root, cp = self._flow(
            "required", [audit_record(artifact_path(1), "PASS")])
        self.assertEqual(cp.returncode, 0, out(cp))
        manifest = ws.manifest(run_root)
        record = manifest["skills"][0]["audit"][0]
        self.assertEqual(record["computed_verdict"], "PASS")
        self.assertEqual(record["computed_sample_count"], 3)

    def test_caller_self_reported_required_pass_is_rejected(self):
        ws = self.make_ws(registry=make_registry(audit_policies={1: "required"}))
        run_root, _ = ws.init_ok()
        ws.complete_all(run_root, skip=(1,))
        self.assertEqual(ws.begin(run_root, "sk01").returncode, 0)
        art = artifact_path(1)
        ws.write_artifact(run_root, art)
        cp = ws.finish(
            run_root, "sk01", artifacts=[art],
            evidence={"audit": [{"artifact": art, "verdict": "PASS",
                                  "sample_count": 1}]})
        self.assertEqual(cp.returncode, 2, out(cp))

    def test_privacy_secret_fail_without_leaking_value(self):
        ws = self.make_ws()
        run_root, _ = ws.init_ok()
        ws.complete_all(run_root, skip=(1,))
        self.assertEqual(ws.begin(run_root, "sk01").returncode, 0)
        art = artifact_path(1)
        ws.write_artifact(run_root, art,
                          text=ARTIFACT_TEXT + "API_KEY=supersecret999\n")
        self.assertEqual(
            ws.finish(run_root, "sk01", artifacts=[art]).returncode, 0)
        cp = ws.finalize(run_root)
        self.assertEqual(cp.returncode, 1, out(cp))
        self.assertNotIn("supersecret999", out(cp))
        result_text = (run_root / "evidence" / "04-验收器结果.json") \
            .read_text(encoding="utf-8")
        self.assertNotIn("supersecret999", result_text)
        self.assertIn("API_KEY", out(cp))

    def test_share_count_terms_do_not_trigger(self):
        ws = self.make_ws()
        run_root, _ = ws.init_ok()
        ws.complete_all(run_root, skip=(1,))
        self.assertEqual(ws.begin(run_root, "sk01").returncode, 0)
        art = artifact_path(1)
        ws.write_artifact(run_root, art,
                          text=ARTIFACT_TEXT + "总股本=113亿股\n持股数量: 1000\n")
        self.assertEqual(
            ws.finish(run_root, "sk01", artifacts=[art]).returncode, 0)
        cp = ws.finalize(run_root)
        self.assertEqual(cp.returncode, 0, out(cp))


# ---------------------------------------------------------------------------
# Phase 2: 领域证据 evidence_rules 结构性执法 (§6.4/§15.3)
# ---------------------------------------------------------------------------
class TestEvidenceRules(GateTestCase):

    def _flow(self, rules, evidence):
        ws = self.make_ws(registry=make_registry(evidence_rules={1: rules}))
        run_root, _ = ws.init_ok()
        ws.complete_all(run_root, evidence={1: evidence} if evidence else None)
        return ws, run_root, ws.finalize(run_root)

    def test_min_facts_unmet_fails(self):
        ws, rr, cp = self._flow([{"kind": "min_facts", "n": 2}],
                                {"facts": [_fact("f1", [_src("巨潮", "a", "100")])]})
        self.assertEqual(cp.returncode, 1, out(cp))
        self.assertEqual(read_result(rr)["matrix"][0]["computed_status"], "FAIL")

    def test_min_facts_met_passes(self):
        facts = [_fact("f1", [_src("巨潮", "a", "100")]),
                 _fact("f2", [_src("东财", "b", "100")])]
        ws, rr, cp = self._flow([{"kind": "min_facts", "n": 2}],
                                {"facts": facts})
        self.assertEqual(cp.returncode, 0, out(cp))


    def test_min_dual_source_facts_unmet_fails(self):
        # 单源事实不满足 min_dual_source_facts
        ws, rr, cp = self._flow(
            [{"kind": "min_dual_source_facts", "n": 1}],
            {"facts": [_fact("f1", [_src("巨潮", "a", "100")])]})
        self.assertEqual(cp.returncode, 1, out(cp))

    def test_min_dual_source_facts_met_passes(self):
        ws, rr, cp = self._flow(
            [{"kind": "min_dual_source_facts", "n": 1}],
            {"facts": [_fact("f1", [_src("巨潮", "a", "100"),
                                    _src("东财", "b", "101")])]})
        self.assertEqual(cp.returncode, 0, out(cp))

    def test_min_calculations_unmet_fails(self):
        ws, rr, cp = self._flow([{"kind": "min_calculations", "n": 1}], {})
        self.assertEqual(cp.returncode, 1, out(cp))

    def test_min_judgments_with_falsification_unmet_fails(self):
        # 有判断但无证伪条件
        ws, rr, cp = self._flow(
            [{"kind": "min_judgments_with_falsification", "n": 1}],
            {"judgments": [{**_judgment("j1"),
                             "falsification_condition": ""}]})
        self.assertEqual(cp.returncode, 1, out(cp))

    def test_min_judgments_with_falsification_met_passes(self):
        ws, rr, cp = self._flow(
            [{"kind": "min_judgments_with_falsification", "n": 1}],
            {"judgments": [_judgment("j1")]})
        self.assertEqual(cp.returncode, 0, out(cp))

    def _conditional_flow(self, *, configured, evidence):
        rules = [{
            "kind": "conditional_command_operations",
            "capability": "tushare_configured",
            "values": [{"op": "ratios", "feeds": "sk02", "layer": 1}],
        }]
        ws = self.make_ws(registry=make_registry(evidence_rules={1: rules}))
        if configured:
            ws.env_overrides["TUSHARE_TOKEN"] = "test-placeholder"
        run_root, _ = ws.init_ok()
        ws.complete_all(run_root, evidence={1: evidence} if evidence else None)
        return ws, run_root, ws.finalize(run_root)

    def test_conditional_commands_configured_but_missing_fails(self):
        ws, run_root, cp = self._conditional_flow(configured=True, evidence={})
        self.assertEqual(cp.returncode, 1, out(cp))
        self.assertIn("ratios", json.dumps(read_result(run_root), ensure_ascii=False))

    def test_conditional_commands_configured_but_nonzero_exit_fails(self):
        receipt = {
            "command_id": "cmd-ratios", "operation": "ratios",
            "argv": ["python3", "tools/ashare_data.py", "ratios", "600519"],
            "exit_code": 1,
            "started_at": "2026-07-17T12:00:00+08:00",
            "finished_at": "2026-07-17T12:00:01+08:00",
            "sources": [], "warnings": ["all sources failed"],
        }
        ws, run_root, cp = self._conditional_flow(
            configured=True, evidence={"command_receipts": [receipt]})
        self.assertEqual(cp.returncode, 1, out(cp))
        self.assertIn("exit_code", json.dumps(read_result(run_root), ensure_ascii=False))

    def test_conditional_commands_not_configured_requires_limitation(self):
        ws, run_root, cp = self._conditional_flow(configured=False, evidence={})
        self.assertEqual(cp.returncode, 1, out(cp))
        self.assertIn("tushare_not_configured",
                      json.dumps(read_result(run_root), ensure_ascii=False))

    def test_conditional_commands_not_configured_with_limitation_is_pwl(self):
        evidence = {"limitations": [{
            "code": "tushare_not_configured",
            "note": "Tushare capability was absent at init",
        }]}
        ws, run_root, cp = self._conditional_flow(
            configured=False, evidence=evidence)
        self.assertEqual(cp.returncode, 0, out(cp))
        self.assertEqual(read_result(run_root)["matrix"][0]["computed_status"],
                         "PASS_WITH_LIMITATIONS")


class TestGateOwnedCommandReceipts(GateTestCase):

    def _workspace(self):
        rules = [{"kind": "required_command_operations", "values": ["quote"]},
                 {"kind": "min_command_receipts", "n": 1}]
        registry = make_registry(nskills=1, evidence_rules={1: rules})
        registry["skills"][0]["name"] = "ashare-data"
        registry["skills"][0]["spec_source"] = "skills/ashare-data.md"
        ws = self.make_ws(registry=registry)
        ws.write_fake_ashare_cli()
        run_root, _ = ws.init_ok(extra=("--codes", "600519"))
        self.assertEqual(ws.begin(run_root, "ashare-data").returncode, 0)
        return ws, run_root

    def test_run_ashare_command_executes_and_freezes_hashed_output(self):
        ws, run_root = self._workspace()
        cp = ws.run_ashare(run_root, "quote", sources=("synthetic-source",))
        self.assertEqual(cp.returncode, 0, out(cp))
        receipt = ws.manifest(run_root)["skills"][0]["command_receipts"][0]
        self.assertEqual(receipt["operation"], "quote")
        self.assertEqual(receipt["exit_code"], 0)
        self.assertEqual(receipt["receipt_origin"], "gate")
        self.assertEqual(receipt["argv"][2:], ["quote", "600519"])
        stdout = run_root / receipt["stdout_path"]
        stderr = run_root / receipt["stderr_path"]
        self.assertTrue(stdout.is_file())
        self.assertTrue(stderr.is_file())
        self.assertEqual(receipt["stdout_sha256"], hashlib.sha256(
            stdout.read_bytes()).hexdigest())
        self.assertEqual(receipt["stderr_sha256"], hashlib.sha256(
            stderr.read_bytes()).hexdigest())

    def test_finish_rejects_caller_submitted_ashare_receipt(self):
        ws, run_root = self._workspace()
        art = artifact_path(1)
        ws.write_artifact(run_root, art)
        receipt = {
            "command_id": "fabricated", "operation": "quote",
            "argv": ["definitely-not-a-real-command", "quote"],
            "exit_code": 0,
            "started_at": "2026-07-17T12:00:00+08:00",
            "finished_at": "2026-07-17T12:00:01+08:00",
            "sources": ["fabricated-source"], "warnings": [],
        }
        cp = ws.finish(run_root, "ashare-data", artifacts=[art],
                       evidence={"command_receipts": [receipt]})
        self.assertEqual(cp.returncode, 2, out(cp))
        self.assertIn("run-ashare-command", cp.stdout + cp.stderr)

    def test_run_ashare_command_rejects_unregistered_operation(self):
        ws, run_root = self._workspace()
        cp = ws.run_ashare(run_root, "valuation",
                           sources=("synthetic-source",))
        self.assertEqual(cp.returncode, 2, out(cp))
        self.assertIn("未登记", cp.stdout + cp.stderr)

    def test_run_ashare_command_requires_source(self):
        ws, run_root = self._workspace()
        cp = ws.run_ashare(run_root, "quote")
        self.assertEqual(cp.returncode, 2, out(cp))
        self.assertIn("source", cp.stdout + cp.stderr)

    def test_required_operation_with_nonzero_exit_fails_finalize(self):
        ws, run_root = self._workspace()
        ws.write_fake_ashare_cli(exit_code=1)
        cp = ws.run_ashare(run_root, "quote", sources=("synthetic-source",))
        self.assertEqual(cp.returncode, 1, out(cp))
        art = artifact_path(1)
        ws.write_artifact(run_root, art)
        self.assertEqual(ws.finish(run_root, "ashare-data",
                                   artifacts=[art]).returncode, 0)
        cp = ws.finalize(run_root)
        self.assertEqual(cp.returncode, 1, out(cp))
        self.assertIn("exit_code", json.dumps(read_result(run_root),
                                               ensure_ascii=False))


class TestArtifactLineage(GateTestCase):

    def test_finish_accepts_closed_artifact_record(self):
        ws = self.make_ws(registry=make_registry(nskills=1))
        run_root, _ = ws.init_ok()
        self.assertEqual(ws.begin(run_root, "sk01").returncode, 0)
        art = artifact_path(1)
        ws.write_artifact(run_root, art)
        assigned = ws.manifest(run_root)["skills"][0]["assigned_artifacts"][0]
        fact = _fact("f1", [_src("巨潮", "chain-a", "100")])
        record = {
            "artifact_id": assigned["artifact_id"],
            "artifact_path": art,
            "input_artifact_ids": [],
            "fact_ids": ["f1"],
            "command_ids": [],
        }
        cp = ws.finish(run_root, "sk01", artifacts=[art],
                       evidence={"facts": [fact],
                                 "artifact_records": [record]})
        self.assertEqual(cp.returncode, 0, out(cp))
        stored = ws.manifest(run_root)["skills"][0]["artifact_records"]
        self.assertEqual(stored, [record])

    def test_finalize_rejects_dangling_lineage_ids(self):
        ws = self.make_ws(registry=make_registry(nskills=1))
        run_root, _ = ws.init_ok()
        self.assertEqual(ws.begin(run_root, "sk01").returncode, 0)
        art = artifact_path(1)
        ws.write_artifact(run_root, art)
        artifact_id = ws.manifest(run_root)["skills"][0]["assigned_artifacts"][0][
            "artifact_id"]
        record = {
            "artifact_id": artifact_id,
            "artifact_path": art,
            "input_artifact_ids": ["missing-artifact"],
            "fact_ids": ["missing-fact"],
            "command_ids": ["missing-command"],
        }
        self.assertEqual(ws.finish(
            run_root, "sk01", artifacts=[art],
            evidence={"artifact_records": [record]}).returncode, 0)
        cp = ws.finalize(run_root)
        self.assertEqual(cp.returncode, 1, out(cp))
        self.assertIn("悬空", json.dumps(read_result(run_root),
                                         ensure_ascii=False))

    def test_finalize_rejects_backward_artifact_dependency(self):
        ws = self.make_ws(registry=make_registry(nskills=2))
        run_root, _ = ws.init_ok()
        manifest = ws.manifest(run_root)
        first_id = manifest["skills"][0]["assigned_artifacts"][0]["artifact_id"]
        second_id = manifest["skills"][1]["assigned_artifacts"][0]["artifact_id"]
        for index, artifact_id, inputs in (
                (1, first_id, [second_id]), (2, second_id, [])):
            name = f"sk{index:02d}"
            art = artifact_path(index)
            self.assertEqual(ws.begin(run_root, name).returncode, 0)
            ws.write_artifact(run_root, art)
            record = {
                "artifact_id": artifact_id, "artifact_path": art,
                "input_artifact_ids": inputs, "fact_ids": [],
                "command_ids": [],
            }
            self.assertEqual(ws.finish(
                run_root, name, artifacts=[art],
                evidence={"artifact_records": [record]}).returncode, 0)
        cp = ws.finalize(run_root)
        self.assertEqual(cp.returncode, 1, out(cp))
        self.assertIn("后向", json.dumps(read_result(run_root),
                                         ensure_ascii=False))

    def test_ashare_requires_shared_facts_and_artifact_record(self):
        rules = [{"kind": "required_command_operations", "values": ["quote"]},
                 {"kind": "min_command_receipts", "n": 1}]
        registry = make_registry(nskills=1, evidence_rules={1: rules})
        registry["skills"][0]["name"] = "ashare-data"
        registry["skills"][0]["spec_source"] = "skills/ashare-data.md"
        ws = self.make_ws(registry=registry)
        ws.write_fake_ashare_cli()
        run_root, _ = ws.init_ok(extra=("--codes", "600519"))
        self.assertEqual(ws.begin(run_root, "ashare-data").returncode, 0)
        self.assertEqual(ws.run_ashare(
            run_root, "quote", sources=("synthetic-source",)).returncode, 0)
        art = artifact_path(1)
        ws.write_artifact(run_root, art)
        self.assertEqual(ws.finish(run_root, "ashare-data",
                                   artifacts=[art]).returncode, 0)
        cp = ws.finalize(run_root)
        self.assertEqual(cp.returncode, 1, out(cp))
        result_text = json.dumps(read_result(run_root), ensure_ascii=False)
        self.assertIn("artifact_record", result_text)
        self.assertIn("共享事实", result_text)

    def _feed_flow(self, *, link_consumer):
        registry = make_registry(nskills=2)
        registry["skills"][0].update({
            "name": "ashare-data",
            "spec_source": "skills/ashare-data.md",
            "evidence_rules": [
                {"kind": "required_command_operations", "values": ["quote"]},
                {"kind": "min_command_receipts", "n": 2},
                {"kind": "conditional_command_operations",
                 "capability": "tushare_configured",
                 "values": [{"op": "ratios", "feeds": "quality-screen",
                             "layer": 1}]},
            ],
        })
        registry["skills"][1].update({
            "name": "quality-screen",
            "spec_source": "skills/quality-screen.md",
        })
        ws = self.make_ws(registry=registry)
        ws.env_overrides["TUSHARE_TOKEN"] = "test-placeholder"
        ws.write_fake_ashare_cli()
        run_root, _ = ws.init_ok(extra=("--codes", "600519"))
        manifest = ws.manifest(run_root)
        ashare_id = manifest["skills"][0]["assigned_artifacts"][0]["artifact_id"]
        consumer_id = manifest["skills"][1]["assigned_artifacts"][0]["artifact_id"]

        self.assertEqual(ws.begin(run_root, "ashare-data").returncode, 0)
        for operation in ("quote", "ratios"):
            self.assertEqual(ws.run_ashare(
                run_root, operation, sources=("synthetic-source",)).returncode, 0)
        command_ids = [receipt["command_id"] for receipt in
                       ws.manifest(run_root)["skills"][0]["command_receipts"]]
        ashare_artifact = artifact_path(1)
        ws.write_artifact(run_root, ashare_artifact)
        fact = _fact("price-fact", [_src("腾讯", "quote-chain", "100")])
        ashare_record = {
            "artifact_id": ashare_id, "artifact_path": ashare_artifact,
            "input_artifact_ids": [], "fact_ids": ["price-fact"],
            "command_ids": command_ids,
        }
        self.assertEqual(ws.finish(
            run_root, "ashare-data", artifacts=[ashare_artifact],
            evidence={"facts": [fact],
                      "artifact_records": [ashare_record]}).returncode, 0)

        self.assertEqual(ws.begin(run_root, "quality-screen").returncode, 0)
        consumer_artifact = artifact_path(2)
        ws.write_artifact(run_root, consumer_artifact)
        consumer_record = {
            "artifact_id": consumer_id, "artifact_path": consumer_artifact,
            "input_artifact_ids": [ashare_id] if link_consumer else [],
            "fact_ids": [],
            "command_ids": [command_ids[1]] if link_consumer else [],
        }
        self.assertEqual(ws.finish(
            run_root, "quality-screen", artifacts=[consumer_artifact],
            evidence={"artifact_records": [consumer_record]}).returncode, 0)
        return ws, run_root, ws.finalize(run_root)

    def test_conditional_feed_requires_downstream_artifact_lineage(self):
        ws, run_root, cp = self._feed_flow(link_consumer=False)
        self.assertEqual(cp.returncode, 1, out(cp))
        self.assertIn("ratios -> quality-screen",
                      json.dumps(read_result(run_root), ensure_ascii=False))

    def test_conditional_feed_accepts_linked_downstream_artifact(self):
        ws, run_root, cp = self._feed_flow(link_consumer=True)
        self.assertEqual(cp.returncode, 0, out(cp))


class TestNotApplicableEvidence(GateTestCase):

    def _finish_fake_na(self, *, fact_ids):
        ws = self.make_ws()
        run_root, _ = ws.init_ok()
        ws.complete_all(run_root, skip=(1,))
        self.assertEqual(ws.begin(run_root, "sk01").returncode, 0)
        fact = _fact("f1", [_src("巨潮", "chain-a", "100")])
        evidence = {
            "facts": [fact],
            "limitations": [{
                "code": "not_applicable",
                "predicate_id": "always_applicable",
                "input_facts": fact_ids,
                "alternative": None,
            }],
        }
        cp = ws.finish(run_root, "sk01", evidence=evidence)
        self.assertEqual(cp.returncode, 0, out(cp))
        neg = run_root / "06-负向验收" / "01-sk01.md"
        neg.write_text(
            "# 负向验收\n\npredicate_id: always_applicable\n"
            f"input_facts: {', '.join(fact_ids)}\n",
            encoding="utf-8")
        return ws, run_root, ws.finalize(run_root)

    def test_always_applicable_cannot_be_marked_not_applicable(self):
        ws, run_root, cp = self._finish_fake_na(fact_ids=["f1"])
        self.assertEqual(cp.returncode, 1, out(cp))
        self.assertEqual(read_result(run_root)["matrix"][0]["computed_status"],
                         "FAIL")

    def test_not_applicable_rejects_dangling_input_fact_id(self):
        ws, run_root, cp = self._finish_fake_na(fact_ids=["missing-fact"])
        self.assertEqual(cp.returncode, 1, out(cp))
        self.assertEqual(read_result(run_root)["matrix"][0]["computed_status"],
                         "FAIL")


# ---------------------------------------------------------------------------
# #15 锁 / 陈旧恢复 / resume
# ---------------------------------------------------------------------------
class TestLocksAndResume(GateTestCase):

    def _lock_path(self, run_root):
        return Path(run_root) / "evidence" / ".full-analysis.lock"

    def _make_stale(self, run_root, host=None):
        lock = self._lock_path(run_root)
        info = json.loads(lock.read_text(encoding="utf-8"))
        p = subprocess.Popen(["/usr/bin/true"])
        p.wait()
        info["pid"] = p.pid  # 已死 pid
        info["start_fingerprint"] = "很久以前的指纹"
        if host is not None:
            info["host"] = host
        lock.write_text(json.dumps(info, ensure_ascii=False),
                        encoding="utf-8")
        return info

    def _resume(self, ws, run_id, extra=()):
        return ws.init(extra=("--mode", "resume", "--run-id", run_id, *extra))

    def test_active_lock_resume_exit_3(self):
        ws = self.make_ws()
        run_root, data = ws.init_ok()
        cp = self._resume(ws, data["run_id"])
        self.assertEqual(cp.returncode, 3, out(cp))

    def test_stale_lock_requires_recover_stale_and_archives(self):
        ws = self.make_ws()
        run_root, data = ws.init_ok()
        self._make_stale(run_root)
        cp = self._resume(ws, data["run_id"])
        self.assertEqual(cp.returncode, 3, out(cp))
        cp = self._resume(ws, data["run_id"],
                          extra=("--recover-stale", data["run_id"]))
        self.assertEqual(cp.returncode, 0, out(cp))
        recovered = list((run_root / "evidence" / "locks")
                         .glob("*-recovered.json"))
        self.assertEqual(len(recovered), 1)
        self.assertTrue(self._lock_path(run_root).is_file())

    def test_other_host_lock_always_exit_3(self):
        ws = self.make_ws()
        run_root, data = ws.init_ok()
        self._make_stale(run_root, host="别的机器")
        cp = self._resume(ws, data["run_id"],
                          extra=("--recover-stale", data["run_id"]))
        self.assertEqual(cp.returncode, 3, out(cp))

    def test_resume_preserves_complete_and_resets_changed_spec(self):
        ws = self.make_ws()
        run_root, data = ws.init_ok()
        ws.complete_skill(run_root, 1)
        ws.complete_skill(run_root, 2)
        self._make_stale(run_root)
        (ws.repo / "skills" / "sk02.md").write_text(
            "# sk02 合成规范 v2\n\n规范改了。\n", encoding="utf-8")
        cp = self._resume(ws, data["run_id"],
                          extra=("--recover-stale", data["run_id"]))
        self.assertEqual(cp.returncode, 0, out(cp))
        m = ws.manifest(run_root)
        self.assertEqual(m["skills"][0]["execution_state"], "COMPLETE")
        sk02 = m["skills"][1]
        self.assertEqual(sk02["execution_state"], "PENDING")
        self.assertTrue(any(
            lim.get("code") == "invalidated_by_spec_change"
            for lim in sk02["limitations"]))

    def test_resume_mismatch_exit_2(self):
        ws = self.make_ws()
        run_root, data = ws.init_ok()
        # run-id 对不上 (派生 run_root 下无 manifest)
        cp = self._resume(ws, "19990101T000000+0800-评测公司")
        self.assertEqual(cp.returncode, 2, out(cp))
        # platform 对不上
        cp = ws.init(platform="codex",
                     extra=("--mode", "resume", "--run-id", data["run_id"]))
        self.assertEqual(cp.returncode, 2, out(cp))


# ---------------------------------------------------------------------------
# 信息带宽层: requires_web_bandwidth 契约禁止静默 CLI-only
# ---------------------------------------------------------------------------
class TestWebBandwidth(GateTestCase):
    """判断密集契约必须有真实 web 来源事实或显式记 web_bandwidth_degraded。"""

    def _drive_all(self, ws, run_root, web_skill, evidence=None):
        """所有 20 项走 begin/finish; 仅 web_skill 挂 evidence。"""
        for sk in ws.manifest(run_root)["skills"]:
            name, path = sk["name"], sk["assigned_artifact_paths"][0]
            ws.begin(run_root, name)
            ws.write_artifact(run_root, path)
            ws.finish(run_root, name, artifacts=[path],
                      evidence=evidence if name == web_skill else None)

    def test_web_bandwidth_skill_without_web_source_or_limitation_fails(self):
        reg = make_registry()
        reg["skills"][9]["requires_web_bandwidth"] = True  # sk10, Layer 3
        ws = GateWorkspace(registry=reg)
        self.addCleanup(ws.cleanup)
        run_root, _ = ws.init_ok()
        self._drive_all(ws, run_root, "sk10")  # sk10 无 web 事实/限制
        cp = ws.gate("finalize", "--registry", ws.registry_path,
                     "--run-root", run_root)
        self.assertEqual(cp.returncode, 1, f"应 FAIL: {cp.stdout}\n{cp.stderr}")
        sk10 = next(s for s in ws.manifest(run_root)["skills"]
                    if s["name"] == "sk10")
        self.assertEqual(sk10["computed_status"], "FAIL")

    def test_web_bandwidth_degraded_limitation_caps_pwl_not_fail(self):
        reg = make_registry()
        reg["skills"][9]["requires_web_bandwidth"] = True  # sk10, Layer 3
        ws = GateWorkspace(registry=reg)
        self.addCleanup(ws.cleanup)
        run_root, _ = ws.init_ok()
        degraded = {"limitations": [{
            "code": "web_bandwidth_degraded",
            "note": "WebSearch/curl/Browser/Tushare 兜底阶梯皆不可用",
        }]}
        self._drive_all(ws, run_root, "sk10", evidence=degraded)
        cp = ws.gate("finalize", "--registry", ws.registry_path,
                     "--run-root", run_root)
        self.assertEqual(cp.returncode, 0, f"应准出: {cp.stdout}\n{cp.stderr}")
        sk10 = next(s for s in ws.manifest(run_root)["skills"]
                    if s["name"] == "sk10")
        self.assertEqual(sk10["computed_status"], "PASS_WITH_LIMITATIONS")

    def test_web_bandwidth_skill_with_web_source_fact_passes_clean(self):
        reg = make_registry()
        reg["skills"][9]["requires_web_bandwidth"] = True  # sk10, Layer 3
        ws = GateWorkspace(registry=reg)
        self.addCleanup(ws.cleanup)
        run_root, _ = ws.init_ok()
        web_fact = {"facts": [_fact("webf1", [
            _src("行业媒体A", "web-chain-a", "100", source_type="web"),
            _src("行业媒体B", "web-chain-b", "100", source_type="news")])]}
        self._drive_all(ws, run_root, "sk10", evidence=web_fact)
        cp = ws.gate("finalize", "--registry", ws.registry_path,
                     "--run-root", run_root)
        self.assertEqual(cp.returncode, 0, f"应准出: {cp.stdout}\n{cp.stderr}")
        sk10 = next(s for s in ws.manifest(run_root)["skills"]
                    if s["name"] == "sk10")
        self.assertEqual(sk10["computed_status"], "PASS")
        cp = ws.summary(run_root)
        self.assertIn("information_bandwidth=FULL", cp.stdout)


if __name__ == "__main__":
    unittest.main()
