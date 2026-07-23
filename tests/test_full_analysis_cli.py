import subprocess
import sys
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
CLI = REPO / "scripts/full_analysis.py"


class FullAnalysisCliTests(unittest.TestCase):
    def test_public_help_only_exposes_lifecycle_commands(self):
        result = subprocess.run([sys.executable, str(CLI), "--help"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn("start", result.stdout)
        self.assertIn("status", result.stdout)
        self.assertIn("resume", result.stdout)
        self.assertIn("cleanup", result.stdout)
        self.assertNotIn("next-work", result.stdout)
        self.assertNotIn("submit-result", result.stdout)


if __name__ == "__main__":
    unittest.main()
