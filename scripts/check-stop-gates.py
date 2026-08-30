#!/usr/bin/env python3
"""check-stop-gates.py — skill 用户确认门（🔴 STOP）的骨架守卫。

背景（2026-08-30 架构评审候选④）：
评审最初把 48 处「🔴 STOP」判定为"同一段话术的复制"，提议引入共享块模板注入。
实测推翻该前提——它们只是**同构句式**，每处的触发动作 / 选项 / 后果都是各 skill
定制的（唯一重复仅 2 处 report_audit 发布门）。模板化杠杆极低，且会破坏
「skills/*.md 裸读即用」与仓库外的第三条同步链（~/.workbuddy/berkshire-skill-sync）。

真正的风险不是重复，而是**被削弱的门**：骨架要素缺失的 STOP 对 Agent 没有约束力
（没有"明确选项"的门 = 用户无法真正选择；没有"未经确认不得" = 没有停止效力）。
本脚本把「门必须具备的全部要素」变成机器断言：缺一项即红。

骨架五要素（缺一即违规）：
  1. 检查点            — 标明这是流程中的确认节点
  2. 必须先向用户确认   — 确认义务的显式声明
  3. 明确选项          — 必须给出可点选的具体选项，否则用户无从选择
  4. 获得明确同意      — 必须等到明确同意（而非"未反对"）
  5. 未经确认不得      — 停止效力：未确认时禁止自主执行

用法：
  python3 scripts/check-stop-gates.py            # 校验（违规即 exit 1）
  python3 scripts/check-stop-gates.py --inventory # 只打印清单，不判红
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = ROOT / "skills"

# 要素名 -> 匹配该要素的正则（任一命中即算具备）
ELEMENTS: list[tuple[str, str]] = [
    ("检查点", r"检查点"),
    ("必须先向用户确认", r"必须先向用户确认|须先向用户确认|需先向用户确认"),
    ("明确选项", r"明确选项|给出选项|选项[:：]"),
    ("获得明确同意", r"获得明确同意|得到明确同意|明确同意后"),
    ("未经确认不得", r"未经确认不得|未获确认不得|未经确认前不得"),
]

STOP_RE = re.compile(r"🔴\s*STOP")

# 「🔴 STOP」在本仓有三种语义，只有第一种是真门：
#   (a) 用户确认门 —— 含「必须先向用户确认」，必须给用户可点选的选项 + 停止效力
#   (b) 硬停止规则 —— 确定性的否决/前置规则（如 earnings-review A.7 可信度否决），
#       不面向用户征询，不适用本守卫
#   (c) 散文提及  —— 设计说明、交叉引用中顺带提到该标记
# 判定：含 CONFIRM_RE 的按 (a) 严格校验；不含的只计数并列出，交人工判语义。
CONFIRM_RE = r"必须先向用户确认|须先向用户确认|需先向用户确认"
# 选项表达：显式写「明确选项」，或括号内给出 A/B/C 式枚举（如「（已核实/未核实）」）
OPTION_RE = r"明确选项|给出选项|选项[:：]|[（(][^）)]*[/／][^）)]*[）)]"
# 停止效力：「未经确认不得…」是禁止式，「获得明确同意后再继续」是许可式，二者等价
EFFICACY_RE = r"未经确认不得|未获确认不得|未经确认前不得|获得明确同意|得到明确同意|明确同意后再继续"

# (a) 类真门必须同时具备：确认义务 + 选项 + 停止效力
GATE_ELEMENTS: list[tuple[str, str]] = [
    ("必须先向用户确认", CONFIRM_RE),
    ("明确选项", OPTION_RE),
    ("停止效力", EFFICACY_RE),
]


def _gates_in(text: str) -> list[tuple[int, str]]:
    """返回 (行号, 门文本) 列表。一个门可能跨行，此处按「标记所在行 + 后续续行」拼接。"""
    lines = text.splitlines()
    gates: list[tuple[int, str]] = []
    for i, ln in enumerate(lines, start=1):
        if not STOP_RE.search(ln):
            continue
        # 拼到句末（句号）或空行为止，最多再取 4 行
        buf = [ln.strip()]
        for extra in lines[i:i + 4]:
            buf.append(extra.strip())
            if "。" in extra:
                break
        gates.append((i, "".join(buf)))
    return gates


def check(skills_dir: Path = SKILLS_DIR) -> tuple[int, int, list[str]]:
    """返回 (真门数, 非确认类标记数, 违规描述列表)。

    只把「必须先向用户确认」的门纳入强校验；其余 🔴 STOP 标记列作 INFO
    （硬停止规则 / 散文提及），由人工判语义，不判红。
    """
    gates = 0
    others = 0
    violations: list[str] = []
    for path in sorted(skills_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        for lineno, gate in _gates_in(text):
            if not re.search(CONFIRM_RE, gate):
                others += 1
                print(f"  · INFO 非确认类标记 {path.name}:{lineno}"
                      f" — {gate[:90]}")
                continue
            gates += 1
            missing = [name for name, pat in GATE_ELEMENTS
                       if not re.search(pat, gate)]
            if missing:
                violations.append(
                    f"{path.relative_to(ROOT)}:{lineno} 缺要素 {missing}\n"
                    f"    原文: {gate[:160]}"
                )
    return gates, others, violations


def inventory(skills_dir: Path = SKILLS_DIR) -> None:
    total = 0
    for path in sorted(skills_dir.glob("*.md")):
        gates = _gates_in(path.read_text(encoding="utf-8"))
        if gates:
            total += len(gates)
            print(f"  {path.name:<42} {len(gates):>2} 处")
    print(f"合计 {total} 处用户确认门")


def main() -> int:
    if "--inventory" in sys.argv:
        inventory()
        return 0
    gates, others, violations = check()
    print(f"扫描 {gates + others} 处 🔴 STOP 标记："
          f"用户确认门 {gates} 处，非确认类 {others} 处（硬停止规则 / 散文提及，INFO 如上）")
    if violations:
        print(f"❌ {len(violations)} 处用户确认门不完整：")
        for v in violations:
            print("  - " + v)
        print("\n用户确认门必须具备三要素：" + "、".join(n for n, _ in GATE_ELEMENTS))
        return 1
    print(f"✅ {gates} 处用户确认门骨架完整（确认义务 + 明确选项 + 停止效力）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
