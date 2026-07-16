#!/usr/bin/env python3
"""Unit tests for tools/xueqiu_scraper.py CLI — playwright 缺失时 --help 仍可用。

用 meta_path 钩子在子进程内屏蔽 playwright（raise ImportError），不真装/卸载任何依赖。
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = str(Path(__file__).resolve().parents[1] / "tools" / "xueqiu_scraper.py")

# 子进程 bootstrap：先装 meta_path 钩子屏蔽 playwright，再用 runpy 以 __main__ 跑目标脚本
BOOTSTRAP = r"""
import importlib.abc
import runpy
import sys


class _BlockPlaywright(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'playwright' or fullname.startswith('playwright.'):
            raise ImportError('playwright blocked by test')
        return None


sys.meta_path.insert(0, _BlockPlaywright())
for name in [m for m in sys.modules if m == 'playwright' or m.startswith('playwright.')]:
    del sys.modules[name]

tool = sys.argv[1]
sys.argv = [tool] + sys.argv[2:]
runpy.run_path(tool, run_name='__main__')
"""


def run_cli(*args):
    return subprocess.run(
        [sys.executable, "-c", BOOTSTRAP, TOOL, *args],
        capture_output=True, text=True, timeout=60,
    )


class TestCliWithoutPlaywright(unittest.TestCase):
    def test_help_exits_zero_with_usage(self):
        # 修复前: playwright 缺失时模块顶层 sys.exit，--help 都无法输出
        proc = run_cli("--help")
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertIn("usage", proc.stdout)

    def test_bad_args_show_argparse_error(self):
        proc = run_cli("--user-id", "not-a-number")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("usage", proc.stderr)
        self.assertNotIn("playwright", proc.stderr)

    def test_run_path_exits_with_playwright_hint(self):
        # 无参数实跑：进入 main 后触发延迟导入，应报缺依赖并保留中文安装提示
        proc = run_cli()
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("playwright", proc.stderr)
        self.assertIn("零依赖原则的唯一例外", proc.stderr)
        self.assertIn("pip install playwright && playwright install chromium", proc.stderr)


def _write_cache(tmp):
    cache = Path(tmp) / "cache.json"
    cache.write_text(json.dumps([
        {"id": "1", "date": "2024-01-01 10:00", "title": "",
         "text": "聊聊拼多多的商业模式", "url": "https://xueqiu.com/1/1"},
        {"id": "2", "date": "2024-01-02 10:00", "title": "",
         "text": "茅台无关内容", "url": "https://xueqiu.com/1/2"},
    ], ensure_ascii=False), encoding="utf-8")
    return cache


class TestFromCacheOffline(unittest.TestCase):
    def test_from_cache_filters_without_playwright(self):
        # 修复前: --from-cache 纯离线过滤也要求安装 playwright（导入位置高于离线分支）
        with tempfile.TemporaryDirectory() as tmp:
            cache = _write_cache(tmp)
            out = Path(tmp) / "out.md"
            proc = run_cli("--from-cache", str(cache),
                           "--keywords", "拼多多", "--output", str(out))
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            self.assertTrue(out.exists(), msg=proc.stdout + proc.stderr)
            content = out.read_text(encoding="utf-8")
            self.assertIn("拼多多", content)
            self.assertNotIn("茅台", content)

    def test_from_cache_missing_output_exits_nonzero(self):
        # 修复前: 参数不全打印提示但退出码 0, 脚本会把误用当成功
        with tempfile.TemporaryDirectory() as tmp:
            cache = _write_cache(tmp)
            proc = run_cli("--from-cache", str(cache), "--keywords", "拼多多")
            self.assertNotEqual(proc.returncode, 0, msg=proc.stdout)

    def test_from_cache_without_user_id_no_fabricated_link(self):
        # 修复前: 缺省 --user-id 时报告头写死"用户 0"并伪造来源链接 xueqiu.com/u/0
        with tempfile.TemporaryDirectory() as tmp:
            cache = _write_cache(tmp)
            out = Path(tmp) / "out.md"
            proc = run_cli("--from-cache", str(cache),
                           "--keywords", "拼多多", "--output", str(out))
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            content = out.read_text(encoding="utf-8")
            self.assertNotIn("xueqiu.com/u/0", content)
            self.assertNotIn("用户 0", content)


if __name__ == "__main__":
    unittest.main()
