#!/usr/bin/env python3
"""Static + integration checks for skill CLI examples (design v1.4 §15.4).

Verifies that:
1. The canonical cross-validate fixture actually parses and exits 0.
2. The three broken legacy forms (--metric / array --values / --sources)
   are rejected with exit code 2.
3. skills/earnings-review.md and skills/earnings-team.md no longer contain
   the broken financial_rigor --metric/--sources usage.
"""

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "financial_rigor.py"


def run_tool(*args):
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )


class TestCrossValidateFixture(unittest.TestCase):
    """§15.4 固定 fixture 必须能被当前 CLI 解析并通过。"""

    def test_canonical_fixture_exits_zero(self):
        proc = run_tool(
            "cross-validate",
            "--field",
            "revenue",
            "--values",
            '{"公司财报":108300000000,"第二来源":107900000000}',
            "--unit",
            "CNY",
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)


class TestBrokenFormsRejected(unittest.TestCase):
    """三种失效形态必须被参数解析拒绝 (exit 2)。"""

    def test_metric_flag_rejected(self):
        proc = run_tool(
            "cross-validate",
            "--metric",
            "revenue",
            "--values",
            '{"公司财报":108300000000,"第二来源":107900000000}',
        )
        self.assertEqual(proc.returncode, 2, msg=proc.stderr)

    def test_array_values_rejected(self):
        proc = run_tool(
            "cross-validate",
            "--field",
            "revenue",
            "--values",
            "[108300000000, 107900000000]",
        )
        self.assertEqual(proc.returncode, 2, msg=proc.stderr)

    def test_sources_flag_rejected(self):
        proc = run_tool(
            "cross-validate",
            "--field",
            "revenue",
            "--values",
            '{"公司财报":108300000000,"第二来源":107900000000}',
            "--sources",
            "公司财报",
            "Yahoo Finance",
        )
        self.assertEqual(proc.returncode, 2, msg=proc.stderr)


class TestSkillFilesNoBrokenExamples(unittest.TestCase):
    """两个财报 skill 文件不得再含 financial_rigor 的 --metric/--sources 用法。"""

    SKILL_FILES = [
        ROOT / "skills" / "earnings-review.md",
        ROOT / "skills" / "earnings-team.md",
    ]

    def _rigor_blocks(self, text):
        """Return lines belonging to financial_rigor invocations, including
        backslash-continuation lines."""
        lines = text.splitlines()
        collected = []
        in_continuation = False
        for line in lines:
            if in_continuation:
                collected.append(line)
                in_continuation = line.rstrip().endswith("\\")
                continue
            if "financial_rigor.py" in line:
                collected.append(line)
                in_continuation = line.rstrip().endswith("\\")
        return collected

    def test_no_metric_or_sources_in_rigor_examples(self):
        for path in self.SKILL_FILES:
            text = path.read_text(encoding="utf-8")
            block = "\n".join(self._rigor_blocks(text))
            self.assertNotIn(
                "--metric", block, msg=f"{path.name} 仍含失效的 --metric 用法"
            )
            self.assertNotIn(
                "--sources", block, msg=f"{path.name} 仍含失效的 --sources 用法"
            )

    def test_cross_validate_examples_use_field_and_json_values(self):
        for path in self.SKILL_FILES:
            text = path.read_text(encoding="utf-8")
            self.assertIn(
                "cross-validate",
                text,
                msg=f"{path.name} 应保留 cross-validate 示例",
            )
            block = "\n".join(self._rigor_blocks(text))
            if "cross-validate" in block:
                self.assertIn(
                    "--field",
                    block,
                    msg=f"{path.name} 的 cross-validate 示例必须用 --field",
                )
                self.assertIn(
                    "--values '{",
                    block,
                    msg=f"{path.name} 的 cross-validate 示例必须用 JSON 对象 --values",
                )


if __name__ == "__main__":
    unittest.main()
