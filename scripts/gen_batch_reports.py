#!/usr/bin/env python3
"""已停用的旧批量报告入口。

该脚本曾直接拼接报告并自行宣称 COMPLETE/PASS_WITH_LIMITATIONS，绕过
Runtime、Gate、Audit、Review 与总结登记，不能提供可信的质量状态。
"""

from __future__ import annotations

import sys


def main() -> int:
    print(
        "❌ scripts/gen_batch_reports.py 已停用：它绕过全量分析质量闭环，"
        "不得生成或宣称完成状态。请为每家公司分别使用 "
        "`python3 scripts/full_analysis.py start ...`。",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
