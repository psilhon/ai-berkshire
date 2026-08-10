#!/usr/bin/env python3
"""为全量分析子 Agent 构造合规的 Result Bundle v1 (result.json)。

设计目标：把"机械且易错"的 bundle 组装（sha256/bytes/证据最低结构/章节核对）
收敛到一个确定性工具里，让子 Agent 专注真实调研与报告写作。

用法（两种模式）：

1) 直接生成（最常用）：
   python3 scripts/mk_result_bundle.py \
     --run-root <run_root> \
     --skill-id <skill_id> \
     --work-unit-id <wu-xxx> \
     --attempt-id <attempt-xxx> \
     --report <attempt_dir>/report.md \
     --status PASS \
     --extra-evidence facts.json     # 真实 fact_updates 数组
     --extra-sources sources.json    # 真实 source_records 数组（与上者必须同时提供）
     [--extra-calculations calcs.json]  [--extra-judgments judg.json]
     [--extra-receipts rcpt.json]       [--extra-capabilities cap.json]
     [--started-at ISO] [--completed-at ISO]

输出：写入 <attempt_dir>/result.json，并打印校验摘要。

它会自动：
- lean 模式（v3.7+）：编排不再签发租约，故不校验租约身份；agent_job_id /
  lease_nonce 仅作为可选溯源字段原样写入（可空）；
- 按 contract 的 evidence_rules 组装证据账本：真实输入优先，缺失部分补**带水印的
  结构地板**（facts/sources/calcs/judgments/receipts/capabilities）；
- 按 report 实际文件重算 bytes/sha256，核对必需章节标题与 min_bytes。

自证红线（v3.4.13）：本工具**不会为未发生的事签发成功证明**。
- 命令回执地板一律 status=UNAVAILABLE + PLACEHOLDER reason，绝不代签 PASS；
- 判断/计算地板带 PLACEHOLDER 水印；capability 地板一律 available=false；
- 真实调研成果必须经 --extra-* 传入，机械字段（sha256/bytes/id 规范）才由工具代劳。
- **命令回执必须由执行器签发（v3.4.15）**：经 `--extra-receipts` 传入的 `status=PASS`
  回执必须是 `scripts/run_evidence_command.py` 真实执行后签发的完整回执（含 signature /
  exit_code / output_digest / executed_at / executor_version），**原样粘贴、禁止手写或改动**。
  v3.4.14 曾要求 `argv`+`output`，但两者都是同一份 JSON 里的自述字符串——跑一条无关命令、
  编一段输出即可通过，所谓"执行绑定"名不副实。现在改由 Gate 的
  `_precheck_command_receipts` → `verify_executor_receipt` 六项判定统一负责
  （签名/退出码/输出摘要/时间窗/operation∈argv/journal 留痕）；生成器通过
  `admit_bundle` 复用同一判定，不再自己维护第二套口径。

2) 负向验收（NOT_APPLICABLE）模式：当契约谓词不成立时，用同一工具产出 Gate
   可接受的负向验收 bundle（此前本工具无法生成合法 NA bundle，逼得 NA 路径手写
   result.json，与 E16 冲突）：
   python3 scripts/mk_result_bundle.py ... --status NOT_APPLICABLE \
     --report <attempt_dir>/report.md      # NA 报告：需含「不适用结论/判定事实/
                                           # 证据来源/替代路径/限制」五章，>= 800 字节
     --extra-evidence facts.json           # 必须含证伪谓词的判定事实
     --extra-sources sources.json          # 判定事实引用的真实来源
     --na-fact-id <证伪谓词的 fact_id> \
     --limitation "code|detail"            # NA 必须显式记录限制
   predicate / alternative 一律从契约 applicability 取，不接受命令行覆写（防笔误
   与自定义谓词）；artifact_id 自动切为 `artifact.na.<skill_id>`；NA 模式**不生成
   任何占位地板**（负向验收不适用 evidence_rules，补地板只会污染账本）。

3) 失败上报（FAIL）模式：
   python3 scripts/mk_result_bundle.py ... --status FAIL \
     --error "code|detail" [--error-retryable false]
   Gate 强制 FAIL bundle 必须携带 error 对象（此前生成器恒写 error=null，FAIL
   状态结构上不可能被接受）；FAIL 同样不补占位地板（ingest-result 会摄入账本）。

退出码契约（v3.4.14 完整化）：
- `0` ⟺ **Gate 预期会接受**：零占位 AND 状态 ∈ {PASS, PASS_WITH_LIMITATIONS,
  NOT_APPLICABLE(证明完整)} AND 报告满足该状态对应的章节/字节要求；
- `2` = 输入非法（JSON 解析失败/单边证据/NA 证明不成立）或报告缺必需章节；
- `3` = 账本仍是 PLACEHOLDER 地板（未做真实调研）；
- `4` = status=FAIL——bundle 结构合法但这是"如实上报失败"，不是成功信号。
没有任何开关能把非 0 降级为 0（旧的 `--allow-placeholder-floor` 会让占位地板返回 0，
直接击穿本不变量，已移除；地板 bundle 依然会正常落盘，调试时忽略退出码即可）。

本工具只生成 bundle，不改任何正式产物、不触发 submit；submit-result 仍由编排器执行。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
REGISTRY = REPO / "tools" / "full_analysis_contract.json"
TZ = timezone(timedelta(hours=8))

# 负向验收（NOT_APPLICABLE）判定口径**直接复用 Gate 常量**，不做本地副本：
# 谓词字段映射/始终适用谓词/NA 报告章节/NA 字节下限一旦两边分叉，生成器就会
# 产出「自认合规、Gate 拒收」的 bundle——退出码又将失去意义。单一真源优于守卫测试。
sys.path.insert(0, str(REPO / "tools"))
from full_analysis_gate import (  # noqa: E402
    ALWAYS_APPLICABLE_PREDICATES,
    NA_MIN_BYTES,
    NA_PREDICATE_FIELDS,
    NA_REQUIRED_HEADINGS,
    admit_bundle,
)

# 结构地板水印：确定性字符串，Gate `_precheck_placeholder_evidence` 按它硬拒收。
# 生成器与 Gate 必须同口径，任何新增的地板字段都要带上它。
PLACEHOLDER = "PLACEHOLDER"

# 退出码契约（v3.4.14 完整化）：0 ⟺ **Gate 预期会接受**这个 bundle
# = 零占位 AND 状态为可验收终态 AND 报告满足该状态对应的章节/字节要求。
# 任何"生成成功但不可提交"的情形都必须落到互不重叠的非 0 码上，且无任何开关
# 能把非 0 降级为 0（此前 --allow-placeholder-floor 可让占位地板返回 0，
# 直接击穿"0 ⟺ 可提交"，已移除）。
EXIT_OK = 0
EXIT_INVALID = 2          # 输入非法(JSON 解析失败/单边证据/NA 证明不成立)/报告缺必需章节
EXIT_PLACEHOLDER = 3      # 账本仍是 PLACEHOLDER 地板（未做真实调研）
EXIT_NOT_SUCCESS = 4      # bundle 合法但状态为 FAIL（如实上报失败，不是成功信号）

ROLE_CN = {
    "duan": "段永平", "buffett": "巴菲特", "munger": "芒格", "li": "李录",
    "company": "公司", "regulatory": "监管", "industry": "行业",
    "sentiment": "情绪", "integrator": "整合",
}


def now_iso() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def load_json(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def fail(msg: str) -> None:
    print(f"❌ {msg}", file=sys.stderr)
    sys.exit(2)


def find_skill(registry: dict, skill_id: str) -> dict:
    for s in registry["skills"]:
        if s["skill_id"] == skill_id:
            return s
    fail(f"contract 中找不到 skill_id={skill_id}")


def build_evidence_ledger(skill: dict, extra_facts: list, extra_sources: list,
                          extra_calcs: list | None = None,
                          extra_judgments: list | None = None,
                          extra_receipts: list | None = None,
                          extra_capabilities: list | None = None):
    """按 lean-v1 契约组装证据账本：只承载 Agent 真实提供的部分，缺失留空。

    lean 语义（v3.7 起）：报告才是唯一交付物，契约已移除 evidence_rules，Gate
    不再强制账本形状，故空账本合法。**绝不合成 PLACEHOLDER 占位**；失败单元由
    调用方在 status/error 中声明。手写 PLACEHOLDER 水印仍会被 Gate 的
    `_precheck_placeholder_evidence` 硬拒收（防自证）。

    role_runs 例外且无需水印：Gate 不信任 bundle 里的 role_runs，而是从磁盘
    role-<role>.md 备忘录独立校验后派生（verified_by_gate=True，见 gate 1000-1030），
    伪造它不产生信任效果。
    """
    extra_calcs = extra_calcs or []
    extra_judgments = extra_judgments or []
    extra_receipts = extra_receipts or []
    extra_capabilities = extra_capabilities or []

    sources = [dict(s) for s in extra_sources] if extra_sources else []
    facts = [dict(f) for f in extra_facts] if extra_facts else []
    calcs = [dict(c) for c in extra_calcs] if extra_calcs else []
    judgments = [dict(j) for j in extra_judgments] if extra_judgments else []
    role_runs = []
    receipts = [dict(r) for r in extra_receipts] if extra_receipts else []
    capabilities = [dict(c) for c in extra_capabilities] if extra_capabilities else []

    return facts, sources, calcs, judgments, role_runs, receipts, capabilities


def placeholder_offenders(bundle: dict) -> list:
    """返回 bundle 中所有带 PLACEHOLDER 水印的证据条目描述（空=零占位）。

    与 Gate 的 `_precheck_placeholder_evidence` 同口径，用于在**提交前**就把
    "含占位的 bundle"暴露为非零退出，而不是等到 Gate 才拒收。
    """
    hits = []
    for fact in bundle.get("fact_updates") or []:
        if PLACEHOLDER in str(fact.get("value", "")):
            hits.append(f"fact {fact.get('fact_id')}")
    for src in bundle.get("source_records") or []:
        if PLACEHOLDER in f"{src.get('publisher', '')}{src.get('title', '')}":
            hits.append(f"source {src.get('source_id')}")
    for calc in bundle.get("calculation_requests") or []:
        if PLACEHOLDER in str(calc.get("calculation_id", "")):
            hits.append(f"calculation {calc.get('calculation_id')}")
    for judgment in bundle.get("judgments") or []:
        if PLACEHOLDER in f"{judgment.get('judgment_id', '')}{judgment.get('conclusion', '')}":
            hits.append(f"judgment {judgment.get('judgment_id')}")
    for rcpt in bundle.get("command_receipts") or []:
        blob = f"{rcpt.get('receipt_id', '')}{rcpt.get('reason', '')}{rcpt.get('detail', '')}"
        if PLACEHOLDER in blob:
            hits.append(f"receipt {rcpt.get('receipt_id')}")
    return hits


# 回执伪造标记（v3.4.14）：PASS 回执的 argv/详情/输出含这些串即视为自报成功而无真实执行。


# 阻断标记（v3.4.14）：带此前缀的预检问题 = Gate 会**硬拒收**，生成器必须非 0 退出。
# 不带前缀的仅为提示。此前缺必需章节 / 字节不足只打印警告却仍返回 0，等于给
# "Gate 必拒的 bundle" 发成功信号——退出码 0 因此不再等价于"可提交"。
BLOCK = "BLOCK::"


def build_not_applicable(skill: dict, na_fact_id: str | None, facts: list,
                         known_source_ids: set, limitations: list) -> dict:
    """构造并**就地校验**负向验收证明，口径与 Gate `_validate_not_applicable` 一致。

    predicate/alternative 一律取自契约（不接受命令行覆写），判定事实必须由
    --extra-evidence 真实提供并能证伪谓词，来源必须已登记，limitations 必须非空。
    任一不满足即 fail(exit 2)——不允许产出"生成器放行、Gate 拒收"的 NA bundle。
    """
    applicability = skill.get("applicability") or {}
    predicate = applicability.get("predicate")
    alternative = applicability.get("alternative")
    if not predicate:
        fail(f"{skill['skill_id']} 契约未声明 applicability.predicate，无法生成负向验收 bundle")
    if predicate in ALWAYS_APPLICABLE_PREDICATES:
        fail(f"{skill['skill_id']} 的适用性谓词 {predicate!r} 始终适用，不得标记 NOT_APPLICABLE；"
             f"请如实按 PASS/PASS_WITH_LIMITATIONS 或 FAIL 提交。")
    if not na_fact_id:
        fail("--status NOT_APPLICABLE 必须同时提供 --na-fact-id（证伪契约谓词的判定事实 id）")
    fact = next((f for f in facts if f.get("fact_id") == na_fact_id), None)
    if not fact:
        fail(f"--na-fact-id={na_fact_id} 未出现在 --extra-evidence 中；"
             f"负向验收的判定事实必须随本次提交一起给出。")

    if predicate == "min_independent_contexts_2":
        expected_field = "independent_context_count"
        ok = (fact.get("field") == expected_field
              and isinstance(fact.get("value"), int)
              and not isinstance(fact.get("value"), bool)
              and fact["value"] < 2)
        expectation = f"field={expected_field!r} 且 value 为 <2 的整数"
    else:
        expected_field = NA_PREDICATE_FIELDS.get(predicate)
        ok = expected_field is not None and fact.get("field") == expected_field \
            and fact.get("value") is False
        expectation = f"field={expected_field!r} 且 value=false"
    if not ok:
        fail(f"判定事实 {na_fact_id} 不能证明谓词 {predicate!r} 为假："
             f"期望 {expectation}，实际 field={fact.get('field')!r} value={fact.get('value')!r}。")

    source_ids = fact.get("source_ids") or []
    unknown = [s for s in source_ids if s not in known_source_ids]
    if not source_ids or unknown:
        fail(f"判定事实 {na_fact_id} 必须引用已登记来源"
             f"（--extra-sources 或 manifest 中已存在）；缺失/未登记: {unknown or '（空 source_ids）'}")
    if not limitations:
        fail("NOT_APPLICABLE 必须显式记录 limitations（至少一条 --limitation \"code|detail\"）")
    return {"predicate": predicate, "fact_id": na_fact_id, "alternative": alternative}


def check_report(skill: dict, report: Path, *, na: bool = False):
    """核对报告结构。na=True 时按负向验收口径（NA 五章 + NA_MIN_BYTES）校验。

    返回警告字符串列表；以 BLOCK:: 开头的条目表示 Gate 会硬拒收（必须非 0 退出）。
    """
    txt = report.read_text(encoding="utf-8")
    body_bytes = report.stat().st_size
    if na:
        # 负向验收产物走 Gate 的 NA 口径：章节与字节下限都与 PASS 路径不同，
        # 沿用 skill.sections 会让 NA 报告永远"缺章节"。
        required_headings = list(NA_REQUIRED_HEADINGS)
        min_bytes = NA_MIN_BYTES
    else:
        required_headings = [sec.get("heading", "")
                             for sec in skill.get("sections", [])
                             if sec.get("required")]
        min_bytes = skill["artifact"].get("min_bytes", 0)
    warnings = []
    missing = [h for h in required_headings
               if not re.search(rf"^#{{1,6}}\s+{re.escape(h)}\s*$", txt, re.M)]
    if missing:
        warnings.append(f"{BLOCK}缺必需章节标题: {missing}")
    if isinstance(min_bytes, int) and min_bytes > 0 and body_bytes < min_bytes:
        warnings.append(f"{BLOCK}字节数 {body_bytes} < min_bytes {min_bytes}"
                        f"（Gate 防坍塌下限，低于即硬拒收）")
    return warnings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-root", required=True)
    ap.add_argument("--skill-id", required=True)
    ap.add_argument("--work-unit-id", required=True)
    ap.add_argument("--attempt-id", required=True)
    ap.add_argument("--lease-nonce", default=None,
                    help="可选：历史租约 nonce，仅用于向后兼容旧 bundle 字段；"
                         "lean 模式不再校验租约，可省略")
    ap.add_argument("--agent-job-id", default=None,
                    help="可选：执行该单元的 Agent job id，仅作 bundle 溯源字段；"
                         "lean 模式不再校验，可省略")
    ap.add_argument("--report", required=True, help="attempt_dir/report.md 绝对或相对 run_root 路径")
    ap.add_argument("--status", default="PASS",
                    choices=["PASS", "PASS_WITH_LIMITATIONS", "NOT_APPLICABLE", "FAIL"])
    ap.add_argument("--extra-evidence", help="JSON 文件，内容为 fact_updates 数组")
    ap.add_argument("--extra-sources", help="JSON 文件，内容为 source_records 数组")
    ap.add_argument("--extra-calculations",
                    help="JSON 文件，内容为 calculation_requests 数组（真实验算参数）")
    ap.add_argument("--extra-judgments",
                    help="JSON 文件，内容为 judgments 数组（真实判断+反证条件）")
    ap.add_argument("--extra-receipts",
                    help="JSON 文件，内容为 command_receipts 数组（真实命令回执；"
                         "未提供时地板只发 UNAVAILABLE，绝不代签 PASS）")
    ap.add_argument("--extra-capabilities",
                    help="JSON 文件，内容为 capability_records 数组（真实能力探测结果；"
                         "未提供时地板一律 available=false）")
    ap.add_argument("--error", default=None, metavar="code|detail",
                    help="仅 --status FAIL：失败原因（Gate 强制 FAIL bundle 必须带 error 对象，"
                         "缺失即拒收）。格式 code|detail。")
    ap.add_argument("--error-retryable", choices=["true", "false"], default="true",
                    help="仅 --status FAIL：该失败是否可重试（写入 error.retryable）。")
    ap.add_argument("--na-fact-id", default=None,
                    help="仅 --status NOT_APPLICABLE：证伪契约 applicability.predicate 的"
                         "判定事实 fact_id（必须出现在 --extra-evidence 中）。"
                         "predicate/alternative 一律取自契约，不接受命令行覆写。")
    ap.add_argument("--role-id", default=None)
    ap.add_argument("--started-at", default=None)
    ap.add_argument("--completed-at", default=None)
    ap.add_argument("--limitation", action="append", default=[],
                    help="可重复：code|detail")
    ap.add_argument("--pwl", action="append", default=[],
                    choices=["tushare_unavailable", "web_bandwidth_degraded", "ephemeral_source"])
    args = ap.parse_args()

    run_root = Path(args.run_root).resolve()
    registry = load_json(REGISTRY)
    skill = find_skill(registry, args.skill_id)
    state = load_json(run_root / "evidence" / "runtime-state.json")
    run_id = state.get("run_id")

    # lean 模式（v3.7+）：编排不再签发租约，故不再校验租约身份。agent_job_id /
    # lease_nonce 仅作为 bundle 溯源字段原样写入（可空），不作准入条件。

    # run_root 已 .resolve()（macOS 上 /var → /private/var）。report 必须做同样的
    # 符号链接解析，否则 /var/... 形式的合法路径会在 relative_to 处被误判为
    # "不在 run_root 内"（v3.4.13：macOS 临时目录下必现）。
    report = Path(args.report)
    if not report.is_absolute():
        report = run_root / report
    report = report.resolve()
    if not report.is_file():
        fail(f"report 文件不存在: {report}")
    try:
        rel = report.relative_to(run_root).as_posix()
    except ValueError:
        fail(f"report 必须位于 run_root 内: {report}（run_root={run_root}）")
    if not rel.startswith("evidence/attempts/"):
        fail(f"report 必须位于 evidence/attempts/ 下: {rel}")

    def load_extra(flag_value: str | None, wrapper_key: str, flag: str) -> list:
        """读取 --extra-* JSON。错误消息必须指向**真实存在的命令行参数**
        （此前误用 wrapper_key 拼出 `--fact_updates` 这种不存在的参数名）。"""
        if not flag_value:
            return []
        try:
            data = json.loads(Path(flag_value).read_text(encoding="utf-8"))
        except FileNotFoundError:
            fail(f"{flag} 指向的文件不存在: {flag_value}")
        except json.JSONDecodeError as exc:
            # 非法 JSON 必须统一退出码 2（与 CHANGELOG/Skill 声明一致），
            # 不得抛 traceback 以退出码 1 收场。
            fail(f"{flag} 不是合法 JSON（{flag_value}）：{exc}")
        if isinstance(data, dict):
            data = data.get(wrapper_key, [])
        if not isinstance(data, list):
            fail(f"{flag} 的内容必须是数组，或是含 {wrapper_key!r} 键的对象；"
                 f"实际为 {type(data).__name__}")
        return data

    extra_facts = load_extra(args.extra_evidence, "fact_updates", "--extra-evidence")
    extra_sources = load_extra(args.extra_sources, "source_records", "--extra-sources")
    extra_calcs = load_extra(args.extra_calculations, "calculation_requests",
                             "--extra-calculations")
    extra_judgments = load_extra(args.extra_judgments, "judgments", "--extra-judgments")
    extra_receipts = load_extra(args.extra_receipts, "command_receipts", "--extra-receipts")
    extra_caps = load_extra(args.extra_capabilities, "capability_records",
                            "--extra-capabilities")

    # 单边真实证据必须显式失败（v3.4.13 P1）：只传 facts 或只传 sources 时，
    # 另一类会退化成 PLACEHOLDER 地板并与真实证据混排，Gate 必然拒收整包，
    # 而生成器此前仍返回 0 —— 静默产出不可提交的 bundle。事实与来源互为引用
    # （fact.source_ids 指向 source_records），二者必须同真同假。
    if bool(extra_facts) != bool(extra_sources):
        got, lack = (("--extra-evidence", "--extra-sources") if extra_facts
                     else ("--extra-sources", "--extra-evidence"))
        fail(f"单边真实证据不被接受：已提供 {got} 但缺 {lack}。"
             f"fact.source_ids 必须指向真实 source_records，只补一边会让另一边退化为 "
             f"{PLACEHOLDER} 地板并被 Gate 拒收整包。请同时提供两者。")

    is_na = args.status == "NOT_APPLICABLE"
    if is_na or args.status == "FAIL":
        # 负向验收 / 失败上报：Gate 对这两类单元**跳过 evidence_rules**（负向验收事实
        # 不适用；失败单元没有交付义务），而 ingest-result 对 FAIL 同样会摄入账本——
        # 补占位地板只会往正式账本塞水印证据。故这两种模式原样采用真实输入，不补地板。
        facts, sources = extra_facts, extra_sources
        calcs, judgments = extra_calcs, extra_judgments
        receipts, capabilities = extra_receipts, extra_caps
        role_runs = []
    else:
        facts, sources, calcs, judgments, role_runs, receipts, capabilities = \
            build_evidence_ledger(skill, extra_facts, extra_sources,
                                  extra_calcs, extra_judgments, extra_receipts, extra_caps)
    # 回执执行绑定（v3.4.15）：PASS 回执的完整性由 admit_bundle 统一校验（与 Gate/ingest
    # 同一函数），此处不再单独预检——避免两套口径分叉导致「生成器放行、Gate 拒收」。

    limitations = []
    for item in args.limitation:
        if "|" in item:
            code, detail = item.split("|", 1)
            limitations.append({"code": code.strip(), "detail": detail.strip()})

    # FAIL 必须携带 error 对象（Gate: "FAIL Result Bundle 必须提供 error"）。
    # 此前生成器恒写 error=null，导致 CLI 暴露的 FAIL 状态**结构上不可能**被 Gate 接受。
    error = None
    if args.status == "FAIL":
        if not args.error or "|" not in args.error:
            fail("--status FAIL 必须提供 --error \"code|detail\"："
                 "Gate 强制 FAIL bundle 携带 error 对象，否则整包拒收。")
        code, detail = args.error.split("|", 1)
        if not code.strip() or not detail.strip():
            fail("--error 的 code 与 detail 均不得为空")
        error = {"code": code.strip(), "detail": detail.strip(),
                 "retryable": args.error_retryable == "true"}
    elif args.error:
        fail(f"--error 仅在 --status FAIL 时允许；当前 status={args.status}，"
             f"Gate 要求成功/PWL/NA bundle 的 error 必须为 null。")

    not_applicable = None
    if is_na:
        manifest_path = run_root / "evidence" / "00-analysis-manifest.json"
        known_source_ids = {s.get("source_id") for s in sources if s.get("source_id")}
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                fail(f"manifest 不是合法 JSON（{manifest_path}）：{exc}")
            known_source_ids |= {s.get("source_id")
                                 for s in (manifest.get("sources") or [])
                                 if s.get("source_id")}
        not_applicable = build_not_applicable(
            skill, args.na_fact_id, facts, known_source_ids, limitations)

    # NA 走负向验收产物 id（Gate: expected_na = f"artifact.na.{skill_id}"）；
    # 沿用正常 artifact_id 会被 Gate 以「负向验收 artifact_id 不匹配」直接拒收。
    art_id = (f"artifact.na.{args.skill_id}" if is_na
              else skill["artifact"].get("artifact_id", f"artifact.{args.skill_id}"))
    artifact_records = [{
        "artifact_id": art_id,
        "path": rel,
        "bytes": report.stat().st_size,
        "sha256": sha256_file(report),
        "formal": False,
        "accepted": False,
    }]

    bundle = {
        "schema_version": "result-schema/v1",
        "run_id": run_id,
        "work_unit_id": args.work_unit_id,
        "attempt_id": args.attempt_id,
        "agent_job_id": args.agent_job_id,
        "lease_nonce": args.lease_nonce,
        "skill_id": args.skill_id,
        "role_id": args.role_id,
        "status": args.status,
        "artifact_records": artifact_records,
        "fact_updates": facts,
        "source_records": sources,
        "calculation_requests": calcs,
        "judgments": judgments,
        "role_runs": role_runs,
        "command_receipts": receipts,
        "capability_records": capabilities,
        "limitations": limitations,
        "not_applicable": not_applicable,
        "pwl_candidates": args.pwl,
        "started_at": args.started_at or now_iso(),
        "completed_at": args.completed_at or now_iso(),
        "error": error,
    }

    attempt_dir = report.parent
    out = attempt_dir / "result.json"
    out.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")

    # 完整准入判定：生成器与 Gate/ingest 共用 admit_bundle（check_artifacts=True 复用
    # 文件/实质/角色memo/NA 章节校验）。这是 v3.4.15 的关键不变量：退出码 0 ⟺ Gate 真正接受，
    # 不再各写各的校验（此前生成器只查标题/字节，导致 rc0 但 Gate 拒收）。
    warnings = check_report(skill, report, na=is_na) if args.status != "FAIL" else []
    offenders = placeholder_offenders(bundle)
    blockers = [w for w in warnings if w.startswith(BLOCK)]
    status_ok = args.status in {"PASS", "PASS_WITH_LIMITATIONS", "NOT_APPLICABLE"}
    report_ok = not blockers
    admission = admit_bundle(bundle, run_root, registry, check_artifacts=True)
    summary = {
        "result_path": str(out),
        "skill_id": args.skill_id,
        "status": args.status,
        "report_bytes": report.stat().st_size,
        "min_bytes": NA_MIN_BYTES if is_na else skill["artifact"].get("min_bytes"),
        "facts": len(facts),
        "sources": len(sources),
        "calculations": len(calcs),
        "judgments": len(judgments),
        "role_runs": len(role_runs),
        "receipts": len(receipts),
        "placeholder_entries": len(offenders),
        # 准入信号必须诚实（v3.4.15）：submittable=true ⟺ 退出码 0 ⟺ Gate 真正接受 =
        # 零占位 AND 状态为可验收终态 AND admit_bundle 全绿（章节/字节/实质/角色memo/
        # 回执执行绑定/占位水印/证据规则/NA 证明/run_id 全部通过）。
        "submittable": (not offenders) and status_ok and not admission,
        "report_blockers": blockers,
        "admission_blockers": admission,
        "status_acceptable": status_ok,
        "not_applicable": not_applicable,
        "precheck_warnings": warnings,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    # 不变量（v3.4.13 起，v3.4.15 完整化）：退出码 0 ⟺ submittable ⟺ Gate 真正接受。
    # 每一类"Gate 必拒"的情形都映射到互不重叠的非 0 码，且无开关可降级为 0。
    if offenders:
        print(
            f"❌ 本 bundle 含 {len(offenders)} 条 {PLACEHOLDER} 结构地板证据"
            f"（非真实调研）：{offenders[:8]}{' …' if len(offenders) > 8 else ''}\n"
            f"   地板只用于本地调 bundle 结构；Gate 预提交门禁会硬拒收，禁止 submit。\n"
            f"   请补齐真实证据后重跑："
            f"--extra-evidence/--extra-sources/--extra-calculations/"
            f"--extra-judgments/--extra-receipts。",
            file=sys.stderr,
        )
        return EXIT_PLACEHOLDER

    if args.status == "FAIL":
        print(
            "❌ status=FAIL：bundle 已按「如实上报失败」生成并落盘，但这不是成功信号"
            "（退出码 4）。请按编排纪律走重试/降级，不要把它当作单元完成。",
            file=sys.stderr)
        return EXIT_NOT_SUCCESS
    if not status_ok:  # 防御性：--status choices 之外的取值不得静默放行
        print(f"❌ status={args.status} 不是可验收终态（退出码 2）。", file=sys.stderr)
        return EXIT_INVALID
    if admission:
        # 与 Gate 同口径的拦截：逐条打印，Agent 一轮改全，不进 audit、不耗 attempt。
        print(
            f"❌ 准入拦截 {len(admission)} 处（与 Gate 同一 admit_bundle 判定，Gate 会硬拒收）：",
            file=sys.stderr)
        for e in admission:
            print(f"  - {e}", file=sys.stderr)
        return EXIT_INVALID
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
