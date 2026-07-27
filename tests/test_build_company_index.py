#!/usr/bin/env python3
"""公司研究索引页生成器（scripts/build_company_index.py）回归测试。

守住三条底线：
1. 确定性：同一组报告永远渲染出同一份 index.html（生成时间取自源文件 mtime，可复跑一致）。
2. 元数据提取：一句话结论的多种标记变体（一句话总结/综合判断/速览首段）与截止日均能提取；
   板块分类正确。
3. 自动更新：新增公司目录后重跑 collect+build，新公司自动进入索引（"后续新增自动更新"的回归保障）。
4. 安全性：公司名/结论中的 HTML 注入被转义。
"""
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import build_company_index as idx  # noqa: E402


SUMMARY_A = """\
# 看懂测试甲（600001.SH）

## 核心结论速览

一句话总结：**测试甲是好公司，但价格不便宜——等回调再买**。

数据截止日：2026-07-25。本报告仅供学习研究。
"""

SUMMARY_B = """\
# 测试乙（300999.SZ）总结报告

## 核心结论速览

9. 综合判断：测试乙治理有重大污点，四大师一致 PASS，建议回避。

10. 数据截止日：2026-07-26。
"""

SUMMARY_C = """\
# 测试丙（688000.SH）

## 核心结论速览

测试丙是科创板稀缺标的，处于产业周期共振向上的拐点，建议关注。

## 数据截止日

2026-07-20。
"""


def _write_company(base: Path, dirname: str, runname: str, md_name: str, body: str) -> None:
    run = base / dirname / runname
    run.mkdir(parents=True, exist_ok=True)
    (run / md_name).write_text(body, encoding="utf-8")


class BuildCompanyIndexTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        _write_company(self.base, "600001.SH-测试甲", "20260725-000000-aaaaaa",
                       "测试甲-全量分析-总结报告.md", SUMMARY_A)
        _write_company(self.base, "300999.SZ-测试乙", "20260726-000000-bbbbbb",
                       "测试乙-全量分析-总结报告.md", SUMMARY_B)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # ---- 底线一：确定性 ----
    def test_deterministic_same_input_same_output(self) -> None:
        rows = idx.collect(self.base)
        html1 = idx.build_index_html(rows, base_label="local/Company")
        html2 = idx.build_index_html(rows, base_label="local/Company")
        self.assertEqual(html1, html2, "同一组输入必须渲染出逐字节一致的索引页")

    def test_rebuild_index_writes_complete_index(self) -> None:
        out = self.base / "index.html"

        result = idx.rebuild_index(self.base, out)

        self.assertEqual(result["index"], str(out))
        self.assertEqual(result["companies"], 2)
        html = out.read_text(encoding="utf-8")
        self.assertEqual(html.count("<article"), 2)
        self.assertEqual(html.count("<article"), html.count("</article>"))

    def test_atomic_write_failure_preserves_previous_index(self) -> None:
        out = self.base / "index.html"
        out.write_text("previous", encoding="utf-8")

        with mock.patch.object(
            idx.os, "replace", side_effect=OSError("replace failed")
        ):
            with self.assertRaises(OSError):
                idx.atomic_write_text(out, "new")

        self.assertEqual(out.read_text(encoding="utf-8"), "previous")

    def test_concurrent_rebuilds_serialize_collection_and_write(self) -> None:
        from concurrent.futures import ThreadPoolExecutor
        import threading
        import time

        active = 0
        max_active = 0
        counter_lock = threading.Lock()
        original_collect = idx.collect

        def measured_collect(base, **kwargs):
            nonlocal active, max_active
            with counter_lock:
                active += 1
                max_active = max(max_active, active)
            try:
                time.sleep(0.02)
                return original_collect(base, **kwargs)
            finally:
                with counter_lock:
                    active -= 1

        with mock.patch.object(idx, "collect", side_effect=measured_collect):
            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(
                    lambda _: idx.rebuild_index(self.base), range(8)))

        html = (self.base / "index.html").read_text(encoding="utf-8")
        self.assertEqual(max_active, 1)
        self.assertEqual(html.count("<article"), 2)
        self.assertEqual(html.count("<article"), html.count("</article>"))
        self.assertTrue(all(item["companies"] == 2 for item in results))

    # ---- 底线二：元数据提取 ----
    def test_collect_extracts_marker_line(self) -> None:
        rows = {r["company"]: r for r in idx.collect(self.base)}
        self.assertIn("测试甲", rows)
        jia = rows["测试甲"]
        self.assertIn("好公司", jia["one"])
        self.assertEqual(jia["asof"], "2026-07-25")
        self.assertEqual(jia["board"], "沪主板")
        self.assertEqual(jia["verdict"], "等待")  # 命中"等回调再买"
        self.assertTrue(jia["html_rel"].endswith(".html") or jia["html"] == "")

    def test_collect_extracts_comprehensive_judgment(self) -> None:
        rows = {r["company"]: r for r in idx.collect(self.base)}
        yi = rows["测试乙"]
        self.assertIn("治理有重大污点", yi["one"])
        self.assertEqual(yi["asof"], "2026-07-26")
        self.assertEqual(yi["board"], "创业板")
        self.assertEqual(yi["verdict"], "回避")  # 命中"回避/PASS"

    def test_board_classification(self) -> None:
        self.assertEqual(idx.board_of("600001.SH"), "沪主板")
        self.assertEqual(idx.board_of("000333.SZ"), "深主板")
        self.assertEqual(idx.board_of("300308.SZ"), "创业板")
        self.assertEqual(idx.board_of("688012.SH"), "科创板")

    def test_board_classification_includes_beijing_exchange(self) -> None:
        self.assertEqual(idx.board_of("920001.BJ"), "北交所")
        self.assertEqual(idx.board_of("430001.BJ"), "北交所")
        self.assertEqual(idx.board_of("830001.BJ"), "北交所")

    def test_verdict_ignores_disclaimer_and_ambiguous_pass(self) -> None:
        self.assertEqual(idx.verdict_of("四大师一致 PASS，建议持有"), "关注")
        self.assertEqual(idx.verdict_of("本报告不构成投资建议"), "中性")
        self.assertEqual(idx.verdict_of("公司等待治理改善后再评估"), "等待")
        self.assertEqual(idx.verdict_of("业绩同比增长，建议关注"), "关注")

    def test_collect_keeps_only_latest_run_per_company(self) -> None:
        _write_company(
            self.base,
            "600001.SH-测试甲",
            "20260727-120000-zzzzzz",
            "测试甲-全量分析-总结报告.md",
            "# 测试甲\n\n一句话总结：最新报告建议持有。\n",
        )

        rows = [
            row for row in idx.collect(self.base)
            if row["company"] == "测试甲"
        ]

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["run"], "20260727-120000-zzzzzz")
        self.assertIn("最新报告", rows[0]["one"])

    # ---- 底线三：自动更新（新增公司后重跑即纳入） ----
    def test_new_company_picked_up_on_rebuild(self) -> None:
        html_before = idx.build_index_html(idx.collect(self.base), base_label="x")
        self.assertNotIn("测试丙", html_before)
        # 模拟新增一家公司
        _write_company(self.base, "688000.SH-测试丙", "20260726-090000-cccccc",
                       "测试丙-全量分析-总结报告.md", SUMMARY_C)
        rows_after = idx.collect(self.base)
        html_after = idx.build_index_html(rows_after, base_label="x")
        self.assertIn("测试丙", html_after)
        self.assertIn("科创板", html_after)
        # 新增公司的一句话结论被提取
        self.assertIn("稀缺标的", html_after)

    # ---- 底线四：安全性（HTML 转义） ----
    def test_html_escaping(self) -> None:
        # 直接在渲染层注入恶意字符串（不经过文件系统，避免文件名限制）。
        evil_row = {
            "code": "600002.SH",
            "company": "测试<script>alert(1)</script>",
            "board": "沪主板",
            "run": "20260726-100000-dddddd",
            "md": "总结报告.md",
            "md_rel": "600002.SH-x/20260726-100000-dddddd/总结报告.md",
            "html": "总结报告.html",
            "html_rel": "600002.SH-x/20260726-100000-dddddd/总结报告.html",
            "one": "测试<script>alert(1)</script>是好公司——等回调",
            "verdict": "等待",
            "asof": "2026-07-26",
            "status": "APPROVED",
            "bytes": 100,
            "mtime": 0.0,
        }
        html = idx.build_index_html([evil_row], base_label="x")
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)

    def test_corrupt_manifest_is_loud(self) -> None:
        run = self.base / "600001.SH-测试甲/20260725-000000-aaaaaa"
        manifest = run / "evidence/00-analysis-manifest.json"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text("{broken", encoding="utf-8")
        warnings: list[str] = []

        rows = idx.collect(self.base, warnings=warnings)
        row = next(item for item in rows if item["company"] == "测试甲")

        self.assertEqual(row["status"], "MANIFEST_ERROR")
        self.assertEqual(len(warnings), 1)
        self.assertIn(str(manifest), warnings[0])

    def test_base_label_is_html_escaped(self) -> None:
        html = idx.build_index_html(
            idx.collect(self.base),
            base_label='<img src=x onerror="alert(1)">',
        )

        self.assertNotIn('<img src=x onerror="alert(1)">', html)
        self.assertIn("&lt;img", html)

    def test_cards_are_visible_without_javascript(self) -> None:
        html = idx.build_index_html(idx.collect(self.base), base_label="x")

        self.assertIsNone(
            re.search(r"(?<!\.js )\.card\{[^}]*opacity:0", html)
        )
        self.assertRegex(html, r"\.js \.card\{[^}]*opacity:0")
        self.assertIn(
            "document.documentElement.classList.add('js')",
            html,
        )

    # ---- 结构完整性：标签配平 + 链接相对路径 ----
    def test_links_are_relative_and_balanced(self) -> None:
        rows = idx.collect(self.base)
        html = idx.build_index_html(rows, base_label="x")
        # 链接必须是相对路径（index.html 与公司目录同级）
        self.assertIn('href="600001.SH-测试甲/', html)
        self.assertNotIn('href="/', html)
        # 简易配平校验：<article> 与 </article> 数量一致
        self.assertEqual(html.count("<article"), html.count("</article>"))


if __name__ == "__main__":
    unittest.main()
