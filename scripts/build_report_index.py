#!/usr/bin/env python3
"""Build reports/INDEX.md — 报告索引，便于在 2000+ 份研究产出中检索。

用法：
    python3 scripts/build_report_index.py

零外部依赖（仅 stdlib）。按 reports/ 一级条目分组：
公司/主题文件夹一组一节，根目录散文件（行业/漏斗/组合等）单独一节。
被 .gitignore 排除的本地文件（如 portfolio-latest.md）不进索引。
"""

import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / 'reports'
OUT = REPORTS / 'INDEX.md'

_DATE_RE = re.compile(r'(20\d{6})')


# git 过滤失效时的兜底排除名单（已知只存本地的文件，绝不能泄入公开索引）
_LOCAL_ONLY_NAMES = {'portfolio-latest.md'}


def _gitignored(paths):
    """返回 paths 中被 .gitignore 排除的集合；git 不可用时警告并返回空集。

    -c core.quotepath=off 保证非 ASCII 路径原样输出，否则中文路径匹配不上。
    """
    if not paths:
        return set()
    try:
        proc = subprocess.run(
            ['git', '-c', 'core.quotepath=off', 'check-ignore', '--stdin'],
            input='\n'.join(str(p) for p in paths),
            capture_output=True, text=True, cwd=ROOT,
        )
        # 退出码 0=有命中 1=无命中；其它值（128 非 git 仓库等）= 过滤失效
        if proc.returncode not in (0, 1):
            print(f'警告: git check-ignore 失败 (exit {proc.returncode})，'
                  f'.gitignore 过滤不生效，仅按兜底名单排除本地文件', file=sys.stderr)
            return set()
        return {line.strip() for line in proc.stdout.splitlines() if line.strip()}
    except OSError:
        print('警告: git 不可用，.gitignore 过滤不生效，仅按兜底名单排除本地文件',
              file=sys.stderr)
        return set()


def _latest_date(paths):
    dates = [m.group(1) for p in paths if (m := _DATE_RE.search(p.name))]
    return max(dates) if dates else None


def _link(rel_to_reports: Path, text: str) -> str:
    href = quote(str(rel_to_reports))
    return f'- [{text}]({href})'


def main():
    all_md = [p for p in sorted(REPORTS.rglob('*.md')) if p != OUT]
    ignored = _gitignored(all_md)
    all_md = [p for p in all_md
              if str(p) not in ignored and p.name not in _LOCAL_ONLY_NAMES]

    groups = {}
    root_files = []
    for p in all_md:
        rel = p.relative_to(REPORTS)
        if len(rel.parts) == 1:
            root_files.append(p)
        else:
            groups.setdefault(rel.parts[0], []).append(p)

    total = len(root_files) + sum(len(v) for v in groups.values())
    lines = [
        '# 报告索引',
        '',
        f'共 {total} 份报告，覆盖 {len(groups)} 个公司/主题文件夹。',
        '由 `python3 scripts/build_report_index.py` 生成，新增报告后重跑更新。',
        '',
        '## 公司/主题文件夹',
        '',
    ]
    for name in sorted(groups):
        files = groups[name]
        latest = _latest_date(files)
        suffix = f'，最新 {latest}' if latest else ''
        lines.append(f'### {name}（{len(files)} 篇{suffix}）')
        lines.append('')
        for p in files:
            rel = p.relative_to(REPORTS)
            lines.append(_link(rel, str(rel.relative_to(name))))
        lines.append('')

    lines += ['## 根目录（行业/漏斗/主题/组合）', '']
    for p in root_files:
        lines.append(_link(p.relative_to(REPORTS), p.name))
    lines.append('')

    OUT.write_text('\n'.join(lines), encoding='utf-8')
    print(f'Wrote {OUT.relative_to(ROOT)}: {total} reports in {len(groups)} folders')
    return 0


if __name__ == '__main__':
    sys.exit(main())
