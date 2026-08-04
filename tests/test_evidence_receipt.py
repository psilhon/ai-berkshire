"""执行器回执（v3.4.15）故障注入测试。

背景：v3.4.14 的「执行绑定」只检查 argv/output 两个字符串非空，而两者都是 Agent
在同一份 result.json 里自填的——**跑一条无关命令、编一段输出即可通过 Gate 进 DONE**。
本模块用机器断言把「必须真实执行」钉死：先证明执行器签发的回执能被接受（真阳），
再逐项破坏六条绑定证明每一条都能变红（真阴）。

纪律：不允许只测「我改的那一处」。六项校验（签名/退出码/输出摘要/时间窗/
operation∈argv/journal 留痕）逐一注入故障，任何一项失去牙齿都会让本模块变红。
"""
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EXECUTOR = REPO / "scripts" / "run_evidence_command.py"

sys.path.insert(0, str(REPO / "tools"))
import evidence_receipt as er  # noqa: E402

RUN_ID = "run-executor-test"


def make_run_root(td: Path, run_id: str = RUN_ID) -> Path:
    """最小 run_root：只需 runtime-state（run_id + run_started_at）。"""
    run_root = td / "run"
    (run_root / "evidence").mkdir(parents=True)
    (run_root / "evidence" / "runtime-state.json").write_text(json.dumps({
        "state_version": "runtime-state/v1",
        "run_id": run_id,
        "run_started_at": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
    }, ensure_ascii=False), encoding="utf-8")
    return run_root


def make_fake_cmd(td: Path) -> Path:
    """一个真实存在、真实执行、会打印可辨识输出的命令。"""
    path = td / "fake_cmd.py"
    path.write_text(
        "import sys\n"
        "print(f'op={sys.argv[1]} rows=42')\n"
        "sys.exit(int(sys.argv[2]) if len(sys.argv) > 2 else 0)\n",
        encoding="utf-8")
    return path


def run_executor(run_root: Path, cmd: list, *, receipt_id: str,
                 operation: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(EXECUTOR), "--run-root", str(run_root),
         "--receipt-id", receipt_id, "--operation", operation, "--"] + cmd,
        capture_output=True, text=True)


def issue(run_root: Path, td: Path, *, operation="quote",
          receipt_id=None, rc=0) -> tuple[dict, subprocess.CompletedProcess]:
    """用执行器真实签发一条回执。"""
    receipt_id = receipt_id or f"rcpt-ashare-data-{operation}"
    cmd = [sys.executable, str(make_fake_cmd(td)), operation, str(rc)]
    proc = run_executor(run_root, cmd, receipt_id=receipt_id, operation=operation)
    receipt = json.loads(proc.stdout) if proc.stdout.strip() else {}
    return receipt, proc


def verify(receipt: dict, run_root: Path, run_id: str = RUN_ID) -> list:
    return er.verify_executor_receipt(
        receipt, run_root=run_root, run_id=run_id,
        secret=er.load_signing_secret(run_root),
        run_started_at=er.load_run_started_at(run_root))


class ExecutorHappyPathTests(unittest.TestCase):

    def test_executor_issued_receipt_is_accepted(self):
        """真阳：执行器签发的回执必须零拦截通过——否则后面的红都没有意义。"""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            run_root = make_run_root(td)
            receipt, proc = issue(run_root, td)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(receipt["status"], "PASS")
            self.assertEqual(receipt["exit_code"], 0)
            self.assertEqual(receipt["executor_version"], er.EXECUTOR_VERSION)
            self.assertRegex(receipt["signature"], r"^[0-9a-f]{64}$")
            self.assertEqual(verify(receipt, run_root), [])

    def test_output_is_persisted_and_digest_matches_file(self):
        """输出必须真的落盘，且 digest 与文件内容一致（可事后复核）。"""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            run_root = make_run_root(td)
            receipt, _ = issue(run_root, td)
            out = run_root / receipt["output"]
            self.assertTrue(out.is_file())
            self.assertIn("op=quote rows=42", out.read_text(encoding="utf-8"))
            import hashlib
            self.assertEqual(hashlib.sha256(out.read_bytes()).hexdigest(),
                             receipt["output_digest"])

    def test_journal_records_every_execution(self):
        """每次执行都留痕，且 journal 与回执 digest 一致。"""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            run_root = make_run_root(td)
            r1, _ = issue(run_root, td, operation="quote")
            r2, _ = issue(run_root, td, operation="financials")
            journal = er.load_journal(run_root)
            self.assertEqual(set(journal), {r1["receipt_id"], r2["receipt_id"]})
            self.assertEqual(journal[r1["receipt_id"]]["output_digest"],
                             r1["output_digest"])

    def test_failed_command_yields_unsigned_fail_receipt_rc2(self):
        """命令失败 → rc2 + FAIL 回执（不签名）。如实上报是合规路径，不是断路。"""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            run_root = make_run_root(td)
            receipt, proc = issue(run_root, td, rc=3)
            self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
            self.assertEqual(receipt["status"], "FAIL")
            self.assertNotIn("signature", receipt)
            self.assertIn("reason", receipt)
            # 失败也留痕，便于 audit 复核「确实试过」
            self.assertIn(receipt["receipt_id"], er.load_journal(run_root))

    def test_unavailable_receipt_needs_reason_and_no_execution(self):
        """UNAVAILABLE 不宣称成功 → 无需签名，但必须给 reason。"""
        with tempfile.TemporaryDirectory() as td:
            run_root = make_run_root(Path(td))
            bad = subprocess.run(
                [sys.executable, str(EXECUTOR), "--run-root", str(run_root),
                 "--receipt-id", "rcpt-x", "--operation", "pledge", "--unavailable"],
                capture_output=True, text=True)
            self.assertEqual(bad.returncode, 1)
            self.assertIn("--reason", bad.stdout + bad.stderr)
            ok = subprocess.run(
                [sys.executable, str(EXECUTOR), "--run-root", str(run_root),
                 "--receipt-id", "rcpt-x", "--operation", "pledge",
                 "--unavailable", "--reason", "该数据源无质押接口"],
                capture_output=True, text=True)
            self.assertEqual(ok.returncode, 0, ok.stderr)
            self.assertEqual(json.loads(ok.stdout)["status"], "UNAVAILABLE")

    def test_operation_must_appear_in_argv(self):
        """执行器自身就拒绝「用无关命令为某操作背书」——这是 P0 漏洞的入口。"""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            run_root = make_run_root(td)
            proc = run_executor(
                run_root, [sys.executable, "-c", "print('whatever')"],
                receipt_id="rcpt-ashare-data-quote", operation="quote")
            self.assertEqual(proc.returncode, 1)
            self.assertIn("不含 operation token", proc.stdout + proc.stderr)


class ReceiptFaultInjectionTests(unittest.TestCase):
    """逐项破坏六条绑定，证明每一条都真的能变红。"""

    def _issued(self, td: Path):
        run_root = make_run_root(td)
        receipt, proc = issue(run_root, td)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(verify(receipt, run_root), [], "基线必须先绿")
        return run_root, receipt

    def test_p0_handwritten_receipt_with_argv_and_output_is_rejected(self):
        """**review 点名的 P0**：v3.4.14 下这条能通过（argv/output 非空即可），
        v3.4.15 必须拒收——它没有签名，不是执行器产出。"""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            run_root, _ = self._issued(td)
            forged = {
                "receipt_id": "rcpt-ashare-data-valuation",
                "operation": "valuation", "status": "PASS",
                "argv": ["python3", "tools/ashare_data.py", "valuation", "000651.SZ"],
                "output": "valuation 已执行并落盘，PE 12.3",
                "detail": "已实际执行",
            }
            errs = verify(forged, run_root)
            self.assertTrue(errs)
            self.assertIn("未经执行器签发", " ".join(errs))

    def test_tampered_signature_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            run_root, receipt = self._issued(td)
            receipt["signature"] = "0" * 64
            errs = verify(receipt, run_root)
            self.assertTrue(any("签名无效" in e for e in errs), errs)

    def test_tampering_any_signed_field_breaks_signature(self):
        """受签字段逐个改动都必须变红——证明签名绑定的是全部字段而非其中几个。"""
        mutations = {
            "receipt_id": "rcpt-other",
            "operation": "financials",
            "status": "PASS_WITH_LIMITATIONS",
            "argv": ["python3", "quote", "--faked"],
            "output": "evidence/command-output/other.txt",
            "executed_at": "2026-08-04T00:00:01+00:00",
            "exit_code": 1,
            "output_digest": "a" * 64,
            "executor_version": "evidence-executor/v9",
        }
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            run_root, base = self._issued(td)
            for field, bad in mutations.items():
                with self.subTest(field=field):
                    receipt = dict(base)
                    self.assertNotEqual(receipt.get(field), bad,
                                        f"{field} 的变异值与原值相同，该子用例形同虚设")
                    receipt[field] = bad
                    errs = verify(receipt, run_root)
                    # 断言必须落在「签名无效」上：只断言 errs 非空的话，
                    # 某字段一旦被移出 SIGNED_FIELDS 仍可能因其它校验偶然变红，
                    # 掩盖「签名不再绑定该字段」这一真实退化。
                    self.assertTrue(any("签名无效" in e for e in errs),
                                    f"改动受签字段 {field} 后签名仍有效：{errs}")

    def test_nonzero_exit_code_cannot_be_pass(self):
        """②：即便签名自洽，exit_code!=0 也不得记 PASS。"""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            run_root, receipt = self._issued(td)
            receipt["exit_code"] = 1
            secret = er.load_signing_secret(run_root)
            receipt["signature"] = er.sign_payload(secret, receipt, RUN_ID)
            errs = verify(receipt, run_root)
            self.assertTrue(any("退出码非零" in e for e in errs), errs)

    def test_output_file_tampered_after_signing_is_detected(self):
        """③：签发后偷改落盘输出 → digest 复核变红。"""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            run_root, receipt = self._issued(td)
            (run_root / receipt["output"]).write_text("伪造的漂亮结果", encoding="utf-8")
            errs = verify(receipt, run_root)
            self.assertTrue(any("被篡改" in e for e in errs), errs)

    def test_missing_output_file_is_detected(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            run_root, receipt = self._issued(td)
            (run_root / receipt["output"]).unlink()
            errs = verify(receipt, run_root)
            self.assertTrue(any("输出缺失" in e for e in errs), errs)

    def test_executed_at_outside_run_window_is_rejected(self):
        """④：复用其它 run 的历史回执 → 执行时间早于本 run 起始。"""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            run_root, receipt = self._issued(td)
            receipt["executed_at"] = "2020-01-01T00:00:00+00:00"
            secret = er.load_signing_secret(run_root)
            receipt["signature"] = er.sign_payload(secret, receipt, RUN_ID)
            errs = verify(receipt, run_root)
            self.assertTrue(any("时间越界" in e for e in errs), errs)

    def test_operation_not_in_argv_is_rejected_at_verify_time(self):
        """⑤：绕开执行器的 CLI 检查、直接构造签名回执，校验侧仍要拦住。"""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            run_root, receipt = self._issued(td)
            receipt["argv"] = ["python3", "-c", "print(1)"]
            secret = er.load_signing_secret(run_root)
            receipt["signature"] = er.sign_payload(secret, receipt, RUN_ID)
            errs = verify(receipt, run_root)
            self.assertTrue(any("命令与操作不符" in e for e in errs), errs)

    def test_missing_journal_entry_is_rejected(self):
        """⑥：删掉 journal 留痕 → 变红（回执必须对应一次真实执行记录）。"""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            run_root, receipt = self._issued(td)
            (run_root / er.JOURNAL_REL).unlink()
            errs = verify(receipt, run_root)
            self.assertTrue(any("无执行留痕" in e for e in errs), errs)

    def test_cross_run_replay_is_rejected(self):
        """把 A run 的合法回执搬到 B run：签名绑定 run_id，必红。"""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            run_a, receipt = self._issued(td)
            run_b = make_run_root(td / "b", run_id="run-other")
            # 连同输出与留痕一起搬过去，只有 run_id 不同——隔离出 run 绑定这一条
            (run_b / er.OUTPUT_DIR_REL).mkdir(parents=True, exist_ok=True)
            (run_b / receipt["output"]).write_bytes((run_a / receipt["output"]).read_bytes())
            (run_b / er.JOURNAL_REL).write_bytes((run_a / er.JOURNAL_REL).read_bytes())
            (run_b / er.SECRET_REL).write_bytes((run_a / er.SECRET_REL).read_bytes())
            errs = er.verify_executor_receipt(
                receipt, run_root=run_b, run_id="run-other",
                secret=er.load_signing_secret(run_b),
                run_started_at=er.load_run_started_at(run_b))
            self.assertTrue(any("签名无效" in e for e in errs), errs)


class BindingModeTests(unittest.TestCase):
    """把「无密钥时降级到 v1 弱校验」变成**被测试固化的已知行为**，
    而不是某天被人当成 bug 悄悄改掉、或误以为 executor 档永远生效。"""

    def test_no_secret_means_legacy_mode(self):
        with tempfile.TemporaryDirectory() as td:
            run_root = make_run_root(Path(td))
            self.assertIsNone(er.load_signing_secret(run_root))
            import full_analysis_gate as gate
            self.assertEqual(gate._receipt_binding_mode(run_root), "legacy")

    def test_executor_self_heals_missing_secret(self):
        """v3.4.15 之前初始化的在途 run 首次调用执行器即自动获得密钥，
        无需重跑 start（否则在途 run 会被卡死）。"""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            run_root = make_run_root(td)
            self.assertIsNone(er.load_signing_secret(run_root))
            issue(run_root, td)
            self.assertIsNotNone(er.load_signing_secret(run_root))
            import full_analysis_gate as gate
            self.assertEqual(gate._receipt_binding_mode(run_root), "executor")

    def test_secret_is_stable_across_executions(self):
        """密钥必须幂等：第二次执行不得换钥匙，否则先前回执集体失效。"""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            run_root = make_run_root(td)
            r1, _ = issue(run_root, td, operation="quote")
            secret1 = er.load_signing_secret(run_root)
            r2, _ = issue(run_root, td, operation="financials")
            self.assertEqual(secret1, er.load_signing_secret(run_root))
            self.assertEqual(verify(r1, run_root), [])
            self.assertEqual(verify(r2, run_root), [])


if __name__ == "__main__":
    unittest.main()
