#!/usr/bin/env python3
"""substance.py — 全量分析「实质地板」的唯一解析缝（Substance 深模块）。

此前实质校验常量（PWL 白名单 / 状态词表 / NA 谓词与章节 / 字节下限）以
字面量散布在 gate / check-full-analysis-contract / mk_result_bundle 三方，
`_substance_errors` 本体埋在 1976 行的 gate 巨石深处却被三个运行时入口
（Gate 边界兜底 / self-check 入口 / mk_result_bundle）与两类测试共用——
是事实公共接口却顶着私有命名。口径一旦分叉，生成器就会产出
「自认合规、Gate 拒收」的 bundle（mk_result_bundle.py 自述的风险模型）。

本模块收敛为一条缝（同 full_analysis_contract.py 的收敛模式）：
  调用方只学 substance_errors() 一个函数 + 一组权威常量。

兼容层：gate 保留 `_substance_errors = substance_errors` 别名与常量
re-export，既有 import（mk_result_bundle 的四个常量、测试直捅）不破。
新代码一律 import substance。
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# 状态词表（Result Bundle v1 状态机）
# ---------------------------------------------------------------------------
RESULT_STATUSES = {"PASS", "PASS_WITH_LIMITATIONS", "NOT_APPLICABLE", "FAIL"}
SUCCESS_TERMINAL_STATUSES = {
    "PASS", "PASS_WITH_LIMITATIONS", "NOT_APPLICABLE",
}
COMPLETED_STATUSES = SUCCESS_TERMINAL_STATUSES | {"FAIL"}

# PASS_WITH_LIMITATIONS 已注册原因白名单（Gate 与生成器同口径）
PWL_ALLOWLIST = {"tushare_unavailable", "web_bandwidth_degraded", "ephemeral_source"}

# ---------------------------------------------------------------------------
# 实质校验常量（防凑数 / 防片面 / 防坍塌，替代纯字节门槛）
# ---------------------------------------------------------------------------
HEADING_RATIO_CAP = 0.18                # 标题字符占比上限，超则骨架/注水嫌疑
DISSENT_RE = re.compile(r"分歧|争议|🔴|不同意|反向|反面|硬伤|风险点|风险|隐患|不确定性|存疑")
# 扇出角色 id -> 中文名，用于"具名分歧"判定（>=2 角色交锋）
ROLE_NAME_MAP = {
    "duan": "段永平", "buffett": "巴菲特", "munger": "芒格", "li": "李录",
    "editor": "编辑", "reader": "读者",
    "company": "公司", "regulatory": "监管", "industry": "行业", "sentiment": "情绪",
    "governance": "治理", "business": "业务", "technology": "技术", "finance": "财务",
    "alternative-data": "另类", "integrator": "整合",
}
NAMED_DISSENT_DEFAULT = 2               # 扇出类需 >=2 角色在分歧处交锋
# 实质小节判定门槛（归一化后字符数）：低于此值视为"一句话带过/占位"，不计入实质章节。
# 防凑数的关键闸门——逼出真论证（数据/对比/推演），而非短占位。非"写作字数目标"。
SUBSTANTIVE_MIN_CHARS = 150
NA_PREDICATE_FIELDS = {
    "has_comparable_financial_history": "has_comparable_financial_history",
    "has_investable_price": "has_investable_price",
    "identifiable_key_managers": "identifiable_key_managers",
    "has_primary_filing_for_period": "has_primary_filing_for_period",
    "main_business_definable": "main_business_definable",
    "physical_bottleneck_exists": "physical_bottleneck_exists",
}
ALWAYS_APPLICABLE_PREDICATES = {"always", "always_applicable", "is_a_share"}
NA_REQUIRED_HEADINGS = ("不适用结论", "判定事实", "证据来源", "替代路径", "限制")
SUMMARY_REQUIRED_HEADINGS = (
    "核心结论速览",
    "主干①·投资分析",
    "主干②·财报研读",
    "主干③·行业分析",
    "补充与参考",
    "产物索引",
    "数据截止日",
    "仅供学习研究",
)
NA_MIN_BYTES = 800
SUMMARY_MIN_BYTES = 2500
# FAIL 报告字节下限（v3.4.15）：短失败上报只需「真实失败说明」，远轻于 PASS/NA。
# 此前 ingest 对 FAIL 复用 NA_MIN_BYTES(800) 导致「生成器 rc4 但 ingest 拒收」的断路；
# 统一到本常量后，生成器与 Gate 用同一门槛，rc4 即代表「如实上报且可提交为失败」。
FAIL_MIN_BYTES = 200


# ---------------------------------------------------------------------------
# 实质校验本体
# ---------------------------------------------------------------------------
def section_blocks(text: str) -> list[tuple[str, str]]:
    """把 markdown 切成 (标题, 正文) 块列表。"""
    blocks: list[tuple[str, str]] = []
    cur_h: str | None = None
    cur: list[str] = []
    for ln in text.splitlines():
        m = re.match(r"^(#{1,6})\s+(.*)$", ln)
        if m:
            if cur_h is not None:
                blocks.append((cur_h, "\n".join(cur)))
            cur_h = m.group(2).strip()
            cur = []
        else:
            cur.append(ln)
    if cur_h is not None:
        blocks.append((cur_h, "\n".join(cur)))
    return blocks


def substance_errors(skill: dict, text: str) -> list[str]:
    """确定性实质校验：防凑数、防空壳、防片面。返回错误列表（空=通过）。

    不依赖字节总数，也不强行匹配 contract 的小节标题原文（避免拒绝措辞不同但扎实的报告），
    只校验可机器核验的"结果"信号：
      - 有实质内容的小节数（每节足够正文/含表格/含数字，防空壳/纯标题）
      - 扇出类具名分歧（>=2 角色在分歧处交锋）
      - 标题占比（防骨架/注水）
    contract.sections 是确定性准出契约；required/min_content_chars/min_substantive_sections
    均在此执行，避免"注册了章节规则但 Gate 不检查"。
    """
    errors: list[str] = []
    stype = skill.get("skill_type", "analysis")
    blocks = section_blocks(text)
    bodies_by_heading: dict[str, list[str]] = {}
    for heading, body in blocks:
        bodies_by_heading.setdefault(heading, []).append(body)

    # 1. 实质章节计数（lean：不再依赖契约 sections，直接统计报告自身的 ## 小节，
    #    与 v2「契约固定标题规则」解耦——lean 契约已移除固定标题，改为检查报告自身深度）。
    #    section_blocks 会把 # 标记剥掉、无法判断层级，故此处直接按 ^#{2,6} 重扫原文。
    substantive_bodies = set()
    rlines = text.splitlines()
    k = 0
    klen = len(rlines)
    while k < klen:
        hm = re.match(r"^(#{2,6})\s+(.+)$", rlines[k])
        if hm:
            body_pieces = []
            j = k + 1
            while j < klen and not re.match(r"^#{1,6}\s", rlines[j]):
                body_pieces.append(rlines[j])
                j += 1
            normalized = re.sub(r"\s+", "", "\n".join(body_pieces))
            if len(normalized) >= SUBSTANTIVE_MIN_CHARS:
                substantive_bodies.add(normalized)
            k = j
        else:
            k += 1
    required_substantive = skill.get("min_substantive_sections", 0)
    if required_substantive and len(substantive_bodies) < required_substantive:
        errors.append(
            f"实质章节 {len(substantive_bodies)} < 下限 {required_substantive}"
            "（重复正文只计一次）")

    # 2. 扇出类具名分歧（>=2 角色在分歧处交锋）
    if stype == "fanout":
        roles = (skill.get("roles") or {}).get("required_roles", [])
        names = [ROLE_NAME_MAP.get(r, r) for r in roles if r != "integrator"]
        named = 0
        for m in DISSENT_RE.finditer(text):
            start = max(0, m.start() - 220)
            end = min(len(text), m.end() + 220)
            ctx = text[start:end]
            if sum(1 for nm in set(names) if nm in ctx) >= 2:
                named += 1
        if named < NAMED_DISSENT_DEFAULT:
            errors.append(f"具名分歧（>=2 角色交锋）{named} < 下限 {NAMED_DISSENT_DEFAULT}")
    # 3. 标题占比（防骨架/注水）
    if text:
        head_chars = sum(len(h) for h in re.findall(r"^#{1,6}\s.*$", text, re.M))
        ratio = head_chars / len(text)
        if ratio > HEADING_RATIO_CAP:
            errors.append(f"标题占比 {ratio:.2f} > {HEADING_RATIO_CAP}（骨架/注水嫌疑）")
    # 4. ## 后紧跟 ### 诊断（帮助 Agent 定位"正文为 0"的具体章节）
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        m_h2 = re.match(r"^##\s+(.+)$", ln)
        if m_h2:
            next_line = i + 1
            while next_line < len(lines) and not lines[next_line].strip():
                next_line += 1
            if (next_line < len(lines)
                    and re.match(r"^###\s+", lines[next_line].strip())):
                errors.append(
                    f"章节「{m_h2.group(1).strip()}」后紧跟 ### 子标题，"
                    "缺少正文段落（需在 ## 与 ### 之间插入 ≥150 字正文）")
    # 5. lean 契约 substance 底线：报告必须声明数据截止日、来源、免责（可信度三锚）。
    # 不强制固定标题，但要求内容层面出现这三要素；缺失即视为不可发布。
    sub = skill.get("substance", {})
    if sub.get("require_as_of") and not re.search(r"\d{4}[-/年]\d{1,2}[-/月]\d{1,2}", text):
        errors.append("缺数据截止日声明（需含 YYYY-MM-DD 形式日期）")
    if sub.get("require_sources") and not re.search(r"(来源|source|数据来自|取自|出处)", text, re.I):
        errors.append("缺数据来源声明")
    if sub.get("require_disclaimer") and not re.search(r"(仅供学习研究|免责|本研究不构成|非投资建议|学习研究)", text):
        errors.append("缺仅供学习研究/免责声明")
    return errors
