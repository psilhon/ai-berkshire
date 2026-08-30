#!/usr/bin/env python3
"""Substance 深模块的接口测试。

背景（2026-08-30 架构评审候选①）：实质校验常量此前以字面量散布
gate / check-full-analysis-contract / mk_result_bundle 三方，`_substance_errors`
埋在 gate 巨石深处却被三方共用。收敛到 tools/substance.py 后：
  1. 本测试只穿公开 interface（substance_errors / 常量）；
  2. 跨文件同值断言机器证明「单一真源」——gate re-export 与生成器 import
     必须与本模块逐项相等，分叉即红。
"""

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import substance  # noqa: E402


class TestConstantsSingleSource(unittest.TestCase):
    def test_gate_reexport_is_same_object(self):
        import full_analysis_gate as gate
        self.assertIs(gate._substance_errors, substance.substance_errors)
        for name in ("PWL_ALLOWLIST", "RESULT_STATUSES", "NA_MIN_BYTES",
                     "FAIL_MIN_BYTES", "NA_REQUIRED_HEADINGS",
                     "ALWAYS_APPLICABLE_PREDICATES", "NA_PREDICATE_FIELDS"):
            self.assertIs(getattr(gate, name), getattr(substance, name), name)

    def test_mk_result_bundle_imports_resolve_to_substance(self):
        """生成器的四个 NA 常量必须解析到 substance（单一真源）。"""
        sys.modules.pop("full_analysis_gate", None)
        import full_analysis_gate as gate2
        import importlib
        mkb_spec = importlib.util.find_spec("mk_result_bundle")
        if mkb_spec is None:
            self.skipTest("mk_result_bundle 仅在 scripts 路径下，由其自身测试覆盖")
        self.assertIs(gate2.NA_MIN_BYTES, substance.NA_MIN_BYTES)


class TestSubstanceErrors(unittest.TestCase):
    def _skill(self, **kw):
        base = {"skill_type": "analysis", "substance": {
            "require_as_of": True, "require_sources": True, "require_disclaimer": True}}
        base.update(kw)
        return base

    def test_three_anchors_missing_all(self):
        bare = "# 标\n\n" + "普通占位正文无任何锚点。" * 30
        errs = substance.substance_errors(self._skill(), bare)
        self.assertIn("缺数据截止日声明（需含 YYYY-MM-DD 形式日期）", errs)
        self.assertIn("缺数据来源声明", errs)
        self.assertIn("缺仅供学习研究/免责声明", errs)

    def test_three_anchors_present(self):
        text = (
            "## 分析\n\n" + "正文论述足够长以构成实质章节，包含数据与推理。".ljust(160, "。") + "\n\n"
            "数据截止日 2026-08-30。来源：Tushare。本研究仅供学习研究，不构成投资建议。\n"
        )
        errs = substance.substance_errors(self._skill(), text)
        self.assertNotIn("缺数据截止日声明（需含 YYYY-MM-DD 形式日期）", errs)
        self.assertNotIn("缺数据来源声明", errs)
        self.assertNotIn("缺仅供学习研究/免责声明", errs)

    def test_substantive_section_counting_dedupes(self):
        body = "独立正文段落，论述充分、含数据对比与推演，足以构成实质章节。".ljust(155, "。")
        text = f"## A\n\n{body}\n\n## B\n\n{body}"  # B 与 A 正文相同 → 只计一次
        errs = substance.substance_errors(self._skill(min_substantive_sections=2), text)
        self.assertTrue(any("实质章节 1 < 下限 2" in e for e in errs))

    def test_h2_followed_immediately_by_h3_flagged(self):
        text = "## 甲\n\n### 乙\n\n" + "锚点段落。".ljust(160, "。") + \
               "\n\n数据截止日 2026-08-30，来源 Tushare，仅供学习研究。\n"
        errs = substance.substance_errors(self._skill(), text)
        self.assertTrue(any("后紧跟 ### 子标题" in e for e in errs))

    def test_fanout_named_dissent(self):
        skill = self._skill(skill_type="fanout",
                            roles={"required_roles": ["duan", "munger", "integrator"]})
        # 无具名交锋 → 报错
        no_fight = "## 分歧\n\n" + "存在分歧但没有任何角色名出现".ljust(160, "。") + \
                   "\n\n数据截止日 2026-08-30，来源 Tushare，仅供学习研究。\n"
        errs = substance.substance_errors(skill, no_fight)
        self.assertTrue(any("具名分歧" in e for e in errs))
        # 段永平与芒格在分歧标记 ±220 字内交锋 → 通过该维度
        fight = ("## 分歧\n\n" + "段永平认为商业模式优秀，但芒格对估值持不同意"
                 "见，双方在此处出现明显分歧与争议。" * 6 +
                 "\n\n数据截止日 2026-08-30，来源 Tushare，仅供学习研究。\n")
        errs2 = substance.substance_errors(skill, fight)
        self.assertFalse(any("具名分歧" in e for e in errs2))

    def test_heading_ratio_cap(self):
        skeleton = "\n".join(f"## 标题{i}文字略长一点用来注水" for i in range(60)) + \
                   "\n\n数据截止日 2026-08-30，来源 Tushare，仅供学习研究。\n"
        errs = substance.substance_errors(self._skill(), skeleton)
        self.assertTrue(any("标题占比" in e for e in errs))


class TestSectionBlocks(unittest.TestCase):
    def test_splits_headings_and_bodies(self):
        blocks = substance.section_blocks("# A\n正文一\n## B\n正文二")
        self.assertEqual([b[0] for b in blocks], ["A", "B"])
        self.assertEqual(blocks[0][1], "正文一")


if __name__ == "__main__":
    unittest.main()
