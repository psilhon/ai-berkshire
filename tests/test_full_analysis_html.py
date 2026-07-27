#!/usr/bin/env python3
"""确定性 HTML 渲染器（tools/full_analysis_html.py）回归测试。

把用户认可的展示件设计系统固化为代码后，这些测试守住四条底线：
1. 确定性：同一份 markdown 永远渲染出同一份 HTML（无方差、可复现）。
2. 安全性：注入式元数据被转义，javascript:/属性逃逸链接被剔除。
3. 结构完整性：8 个标准章节锚点齐全、报头/导航/印章/返回顶部/免责声明在场、标签配平。
4. 忠实性：markdown 原文（表格/列表/代码块/引用）被正确转换，stash 占位符不泄漏。
"""
import re
import sys
import unittest
from html.parser import HTMLParser
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
import full_analysis_html as html_module  # noqa: E402


# 模仿 finalize 总结报告结构的合成 markdown（含全部 8 个标准章节标题与常见语法）
SUMMARY_MD = """\
# 看懂示例公司（600584.SH）——全量分析深度总结

## 核心结论速览

一句话总结：**半导体封测龙头，先进封装打开第二成长曲线——周期底部的质量资产**。

| 指标 | 数值 | 说明 |
| --- | --- | --- |
| 营业收入 | 388.71亿元 | 同比+15.65% |
| 综合得分 | 82.90 | 一流偏上 |

## 主干①·投资分析

### 估值与买点

正文段落，包含 `代码片段` 与 **加粗** 文字。

> 重点引用：安全边际充足。

- 无序项一
- 无序项二

1. 有序项一
2. 有序项二

[安全链接](https://example.com/page)
[危险脚本](javascript:alert%281%29)
[属性逃逸](https://safe.example/" onmouseover="alert(1))

```
code fence 内容 <不应被解析为标签>
```

## 主干②·财报研读

财务内容。

## 主干③·行业分析

行业内容。

## 补充与参考

补充内容。

## 产物索引

产物清单。

## 数据截止日

2026-07-26。

## 仅供学习研究

本报告仅供学习研究，不构成投资建议。
"""

KW = dict(company="示例公司", code="600584.SH", as_of="2026-07-26",
          skill_count=13, status="APPROVED")

EXPECTED_IDS = ["overview", "invest", "finance", "industry",
                "supplement", "artifacts", "asof", "disclaimer"]


class _TagBalance(HTMLParser):
    """简易标签配平检查器（忽略 void 元素）。"""
    VOID = {"meta", "br", "hr", "img", "link", "input", "area",
            "base", "col", "embed", "source", "track", "wbr"}

    def __init__(self):
        super().__init__()
        self.stack = []
        self.errors = []

    def handle_starttag(self, tag, attrs):
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        if not self.stack:
            self.errors.append(f"extra </{tag}>")
        elif self.stack[-1] == tag:
            self.stack.pop()
        else:
            self.errors.append(f"mismatch: open <{self.stack[-1]}> got </{tag}>")


class HtmlRendererTest(unittest.TestCase):
    def _render(self, md=SUMMARY_MD, **overrides):
        kw = dict(KW)
        kw.update(overrides)
        return html_module.build_summary_page(md, **kw)

    def test_deterministic_rendering(self):
        """同一输入两次渲染结果逐字节一致（确定性是该渲染器的核心契约）。"""
        self.assertEqual(self._render(), self._render())

    def test_section_anchors_all_present_and_ordered(self):
        html = self._render()
        positions = [html.index(f'id="{sid}"') for sid in EXPECTED_IDS]
        self.assertEqual(positions, sorted(positions), "章节锚点应按章节顺序出现")

    def test_page_skeleton_present(self):
        html = self._render()
        for marker in ('class="masthead"', 'class="nav"', 'class="stamp"',
                       'id="backTop"', "数据截止 2026-07-26", "13 份正式产物",
                       "不构成任何投资建议"):
            self.assertIn(marker, html)

    def test_stamp_extracted_from_overview(self):
        """印章主句应取自核心结论的『一句话总结』加粗句。"""
        html = self._render()
        self.assertIn("半导体封测龙头", html)

    def test_tags_are_balanced(self):
        checker = _TagBalance()
        checker.feed(self._render())
        self.assertEqual(checker.errors, [])
        self.assertEqual(checker.stack, [])

    def test_table_renders_with_wrapper_and_numeric_alignment(self):
        html = self._render()
        self.assertIn('class="tbl-wrap', html)
        self.assertIn("<table>", html)
        # 数值列（388.71亿元/82.90）应右对齐 mono
        self.assertIn('class="num"', html)
        # 首列（指标名）应加粗
        self.assertIn("first-col", html)

    def test_lists_are_closed(self):
        html = self._render()
        self.assertIn("<ol", html)
        self.assertIn("</ol>", html)
        self.assertIn("<ul", html)
        self.assertIn("</ul>", html)

    def test_code_fence_is_escaped(self):
        html = self._render()
        self.assertNotIn("<不应被解析为标签>", html)
        self.assertIn("&lt;不应被解析为标签&gt;", html)

    def test_inline_code_and_bold_rendered(self):
        html = self._render()
        self.assertIn("<code>代码片段</code>", html)
        self.assertIn("<strong>加粗</strong>", html)

    def test_dangerous_links_stripped(self):
        html = self._render()
        self.assertNotIn("javascript:", html)
        self.assertNotIn("onmouseover", html)
        # 安全链接应保留
        self.assertIn('href="https://example.com/page"', html)

    def test_metadata_is_escaped(self):
        html = self._render(
            company='<img src=x onerror="alert(1)">',
            status='APPROVED"><script>alert(1)</script>')
        self.assertNotIn("<img", html)
        self.assertNotIn("<script>alert", html)
        self.assertIn("&lt;img", html)
        # head 中启用渐进增强，body 中保留微交互脚本。
        self.assertEqual(len(re.findall(r"<script", html)), 2)

    def test_no_stash_token_leaks(self):
        """行内渲染的 \x00 占位符必须全部还原，不得泄漏到产物。"""
        html = self._render()
        self.assertNotIn("\x00", html)

    def test_design_system_inlined(self):
        """设计令牌与微交互脚本内联，产物零外部依赖。"""
        html = self._render()
        self.assertIn("--terra:#B85235", html)
        self.assertIn("--paper:#F5F4ED", html)
        self.assertIn("IntersectionObserver", html)

    def test_report_content_is_visible_without_javascript(self):
        html = self._render()

        self.assertIsNone(
            re.search(r"(?<!\.js\.enhanced )\.reveal\{[^}]*opacity:0", html)
        )
        self.assertRegex(html, r"\.js\.enhanced \.reveal\{[^}]*opacity:0")
        self.assertIn(
            "document.documentElement.classList.add('js')",
            html,
        )
        self.assertIn(
            'document.documentElement.classList.add("enhanced")',
            html,
        )
        self.assertGreater(
            html.index('document.documentElement.classList.add("enhanced")'),
            html.index("IntersectionObserver"),
        )


# 真实 run 冒烟（local/ 为 gitignore，CI 无此目录则跳过；本地有则校验体量与无泄漏）
_REAL = (REPO / "local/company/600584.SH-长电科技/20260726-114034-8552ee"
         / "长电科技-全量分析-总结报告.md")


@unittest.skipUnless(_REAL.is_file(), "本地无长电科技真实 run 总结，跳过冒烟")
class RealSummarySmokeTest(unittest.TestCase):
    def test_real_summary_renders(self):
        md = _REAL.read_text(encoding="utf-8")
        html = html_module.build_summary_page(
            md, company="长电科技", code="600584.SH", as_of="2026-07-26",
            skill_count=13, status="APPROVED")
        self.assertGreater(len(html.encode()), 30000)
        self.assertNotIn("\x00", html)
        checker = _TagBalance()
        checker.feed(html)
        self.assertEqual(checker.errors, [])
        self.assertEqual(checker.stack, [])
        for key in ["388.71", "82.90", "1483"]:
            self.assertIn(key, html)


if __name__ == "__main__":
    unittest.main()
