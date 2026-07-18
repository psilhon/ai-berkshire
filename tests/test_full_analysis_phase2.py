#!/usr/bin/env python3
"""Phase 2 契约参数化测试 (v1.4 §15.3) — 由真实注册表驱动的 20 组契约验收.

读 tools/full_analysis_contract.json (真实 20 项契约, 均 IMPLEMENTED), 对每一项
构造"单契约注册表"(1-skill registry), 用 tests/test_full_analysis_gate.py 的
GateWorkspace 驱动 init -> begin -> finish -> finalize, 逐项断言 gate 逐项计算的
matrix[0].computed_status.

每项契约生成 4 个断言 (subTest):
  - GREEN         : 一切合规 -> finalize exit 0 且 computed_status ∈ {PASS, PWL}
  - RED-section   : 删掉最后一个 domain 必需章节 -> FAIL
  - RED-evidence  : 缺该契约的专项证据 (领域证据键 / required 审计 / 侦察类章节) -> FAIL
  - RED-applicable: 错误的 not_applicable 适用性声明 -> FAIL

绝不写真实 ~/.claude / ~/.codex / 本仓库工作区; 全部在 GateWorkspace 的临时 git 仓库 +
临时 HOME 内完成. 纯 stdlib.
"""
import copy
import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
# 复用同目录 gate 单测的 fixtures (GateWorkspace / _fact / _src / out / read_result)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_full_analysis_gate import (  # noqa: E402
    GateWorkspace, _fact, _judgment, _role, _src, audit_record, out,
    read_result)

CONTRACT = REPO / "tools" / "full_analysis_contract.json"
REGISTRY = json.loads(CONTRACT.read_text(encoding="utf-8"))
GENERIC = REGISTRY["generic_required_sections"]

# evidence_rule.kind -> evidence payload 键
KIND_KEY = {
    "min_facts": "facts",
    "min_dual_source_facts": "facts",
    "min_calculations": "calculations",
    "min_judgments_with_falsification": "judgments",
    "min_role_runs": "role_runs",
    "min_command_receipts": "command_receipts",
    "required_fact_fields": "facts",
    "required_judgment_rule_ids": "judgments",
    "required_command_operations": "command_receipts",
}


# ---------------------------------------------------------------------------
# 单契约注册表: 深拷贝真实契约, index=1, 顶层沿用真实 stage_dirs 等
# ---------------------------------------------------------------------------
def build_single_registry(item):
    single = copy.deepcopy(item)
    single["index"] = 1
    single["spec_source"] = f"skills/{item['name']}.md"
    predicate = item["applicability_rule"]["predicate_id"]
    return {
        "registry_schema_version": 1,
        "manifest_schema_version": 1,
        "annotations_schema_version": 1,
        "stage_dirs": REGISTRY["stage_dirs"],
        "negative_acceptance_dir": REGISTRY["negative_acceptance_dir"],
        "generic_required_sections": REGISTRY["generic_required_sections"],
        "predicates": [predicate, "always_applicable"],
        "skills": [single],
    }


def artifact_rule(item):
    return item["artifact_rules"][0]


def domain_sections(item):
    """required_sections 去掉通用 4 项后的领域章节 (保序)。"""
    req = artifact_rule(item)["required_sections"]
    return [s for s in req if s not in GENERIC]


# ---------------------------------------------------------------------------
# 产物文本: 每个章节字符串各占一行 (## 标题), 其余正文/填充纯 ASCII, 按 utf-8 字节补足
# ---------------------------------------------------------------------------
def build_text(sections, min_bytes):
    lines = ["# synthetic full-analysis artifact", ""]
    for s in sections:
        lines.append(f"## {s}")
        lines.append("placeholder body line for section presence, ascii only.")
    lines.extend(["营业收入: 100", "净利润: 20", "总资产: 300"])
    body = "\n".join(lines) + "\n"
    # min_bytes 是字节数, 用纯 ASCII 填充补足 (中文章节字符串是文本中唯一的多字节内容)
    while len(body.encode("utf-8")) < min_bytes:
        body += "x" * 64 + "\n"
    return body


# ---------------------------------------------------------------------------
# GREEN evidence: 逐条满足 evidence_rules + 必需角色 + 审计策略
# ---------------------------------------------------------------------------
def green_evidence(item):
    facts, calcs, judgments, role_runs, command_receipts = [], [], [], [], []
    for i, rule in enumerate(item.get("evidence_rules", [])):
        kind = rule["kind"]
        n = rule.get("n", 1)
        for k in range(n):
            if kind == "min_facts":
                facts.append(_fact(f"f{i}_{k}",
                                   [_src("巨潮", f"chain-{i}-{k}", "100")]))
            elif kind == "min_dual_source_facts":
                facts.append(_fact(f"fd{i}_{k}",
                                   [_src("巨潮", f"chain-a-{i}-{k}", "100"),
                                    _src("东财", f"chain-b-{i}-{k}", "100")]))
            elif kind == "min_calculations":
                calcs.append({
                    "calculation_id": f"c{i}_{k}", "type": "calc",
                    "args": {"expr": "1+1"},
                    "expected": {"outcome": "PASS", "is_pass": True,
                                 "exit_code": 0,
                                 "result": {"expression": "1+1", "value": "2"}}})
            elif kind == "min_judgments_with_falsification":
                judgments.append(_judgment(f"j{i}_{k}"))
            elif kind == "min_role_runs":
                role_runs.append(_role(
                    f"r{i}_{k}", artifact_rule(item)["path"],
                    context_id=f"ctx-{i}-{k}"))
            elif kind == "min_command_receipts":
                command_receipts.append({
                    "command_id": f"cmd{i}_{k}",
                    "operation": f"synthetic-operation-{k}",
                    "argv": ["synthetic-tool", f"operation-{k}"],
                    "exit_code": 0,
                    "started_at": "2026-07-17T12:00:00+08:00",
                    "finished_at": "2026-07-17T12:00:01+08:00",
                    "sources": ["synthetic-source-a", "synthetic-source-b"],
                    "warnings": [],
                })
        if kind == "required_fact_fields":
            for k, field in enumerate(rule["values"]):
                fact = _fact(f"fr{i}_{k}",
                             [_src("巨潮", f"field-chain-{i}-{k}", "100")])
                fact["field"] = field
                facts.append(fact)
        elif kind == "required_judgment_rule_ids":
            for k, rule_id in enumerate(rule["values"]):
                judgments.append(_judgment(f"jr{i}_{k}", rule_id=rule_id))
        elif kind == "required_command_operations":
            for k, operation in enumerate(rule["values"]):
                command_receipts.append({
                    "command_id": f"required-cmd{i}_{k}",
                    "operation": operation,
                    "argv": ["synthetic-tool", operation],
                    "exit_code": 0,
                    "started_at": "2026-07-17T12:00:00+08:00",
                    "finished_at": "2026-07-17T12:00:01+08:00",
                    "sources": ["synthetic-source-a", "synthetic-source-b"],
                    "warnings": [],
                })
    # 必需角色必须按名字出现
    for role in item.get("role_rule", {}).get("required_roles", []):
        role_runs.append(_role(role, artifact_rule(item)["path"],
                               context_id=f"required-{role}"))
    ev = {}
    if facts:
        ev["facts"] = facts
    if calcs:
        ev["calculations"] = calcs
    if judgments:
        ev["judgments"] = judgments
    if role_runs:
        ev["role_runs"] = role_runs
    if command_receipts:
        ev["command_receipts"] = command_receipts
    policy = artifact_rule(item).get("audit_policy", "none")
    if policy in ("required", "advisory"):
        ev["audit"] = [audit_record(artifact_rule(item)["path"])]
    return ev


def green_begin_extra(item):
    min_ctx = item.get("role_rule", {}).get("min_independent_contexts", 0)
    if min_ctx > 0:
        return ("--execution-mode", "independent_contexts",
                "--independent-context-count", str(min_ctx))
    return ()


# ---------------------------------------------------------------------------
# RED 变体: 相对 GREEN 只改一处
# ---------------------------------------------------------------------------
def red_section_sections(item):
    """删掉最后一个 domain 必需章节。"""
    dom = domain_sections(item)
    target = dom[-1]
    return [s for s in artifact_rule(item)["required_sections"] if s != target]


def red_evidence_case(item):
    """返回 (sections, evidence): 缺该契约的专项证据。

    - evidence_rules 非空 -> 清空其要求的领域证据键 (audit 保留以隔离变量)
    - 否则 required 审计   -> 不给 audit 记录
    - 否则 (advisory-only) -> 删倒数第二个 domain 章节
    """
    green_sec = list(artifact_rule(item)["required_sections"])
    ev_rules = item.get("evidence_rules", [])
    policy = artifact_rule(item).get("audit_policy", "none")
    if ev_rules:
        ev = green_evidence(item)
        for rule in ev_rules:
            key = KIND_KEY.get(rule["kind"])
            if key:
                ev.pop(key, None)
        return green_sec, ev
    if policy == "required":
        ev = green_evidence(item)
        ev.pop("audit", None)
        return green_sec, ev
    # advisory-only 且无 evidence_rules: 专项即其侦察/命令类章节
    dom = domain_sections(item)
    target = dom[-2]
    secs = [s for s in green_sec if s != target]
    return secs, green_evidence(item)


def red_applicability_evidence(item):
    """GREEN evidence + 错误的 not_applicable 声明 (谓词不在注册表 + 空 input_facts)。"""
    ev = green_evidence(item)
    ev["limitations"] = [{
        "code": "not_applicable",
        "predicate_id": "nonexistent_predicate_xyz",
        "input_facts": [],
        "alternative": None,
    }]
    return ev


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------
class TestFullAnalysisPhase2Contracts(unittest.TestCase):
    """真实 20 项契约参数化: 每项 1 GREEN + 3 RED。"""

    def _drive(self, item, sections, evidence, begin_extra, artifact_text=None):
        """init -> begin -> write artifact -> finish -> finalize。

        返回 (finalize_returncode, computed_status, combined_output)。
        """
        ws = GateWorkspace(registry=build_single_registry(item))
        try:
            run_root, _ = ws.init_ok()
            name = item["name"]
            rule = artifact_rule(item)
            text = (artifact_text if artifact_text is not None
                    else build_text(sections, rule["min_bytes"]))
            ws.write_artifact(run_root, rule["path"], text=text)
            cp_begin = ws.begin(run_root, name, extra=begin_extra)
            self.assertEqual(cp_begin.returncode, 0,
                             f"begin 应成功: {out(cp_begin)}")
            cp_finish = ws.finish(run_root, name, artifacts=[rule["path"]],
                                  evidence=evidence)
            self.assertEqual(cp_finish.returncode, 0,
                             f"finish 应成功: {out(cp_finish)}")
            cp_final = ws.finalize(run_root)
            status = None
            try:
                status = read_result(run_root)["matrix"][0]["computed_status"]
            except (OSError, ValueError, KeyError, IndexError):
                pass
            return cp_final.returncode, status, out(cp_final)
        finally:
            ws.cleanup()

    def test_green_contracts_pass(self):
        for item in REGISTRY["skills"]:
            with self.subTest(contract=item["name"], case="GREEN"):
                sections = artifact_rule(item)["required_sections"]
                rc, status, log = self._drive(
                    item, sections, green_evidence(item),
                    green_begin_extra(item))
                self.assertEqual(rc, 0,
                                 f"[{item['name']}] GREEN finalize 应 exit 0; "
                                 f"status={status}\n{log}")
                self.assertIn(status, ("PASS", "PASS_WITH_LIMITATIONS"),
                              f"[{item['name']}] GREEN computed_status 应 "
                              f"PASS/PWL, 实际 {status}\n{log}")

    def test_red_missing_required_section_fails(self):
        for item in REGISTRY["skills"]:
            with self.subTest(contract=item["name"], case="RED-section"):
                rc, status, log = self._drive(
                    item, red_section_sections(item), green_evidence(item),
                    green_begin_extra(item))
                self.assertEqual(rc, 1,
                                 f"[{item['name']}] RED-section finalize 应 "
                                 f"exit 1; status={status}\n{log}")
                self.assertEqual(status, "FAIL",
                                 f"[{item['name']}] RED-section computed_status "
                                 f"应 FAIL, 实际 {status}\n{log}")

    def test_investment_team_each_named_section_is_fail_closed(self):
        item = next(skill for skill in REGISTRY["skills"]
                    if skill["name"] == "investment-team")
        required = [
            "段永平视角",
            "巴菲特视角",
            "芒格视角",
            "李录视角",
            "四视角对照表",
            "分歧仲裁",
            "综合结论",
        ]
        all_sections = artifact_rule(item)["required_sections"]
        for missing in required:
            with self.subTest(missing=missing):
                sections = [section for section in all_sections
                            if section != missing]
                rc, status, log = self._drive(
                    item, sections, green_evidence(item),
                    green_begin_extra(item))
                self.assertEqual(
                    (rc, status), (1, "FAIL"),
                    f"investment-team 缺少“{missing}”时必须 fail-closed\n{log}",
                )

    def test_investment_team_body_mentions_do_not_count_as_named_sections(self):
        item = next(skill for skill in REGISTRY["skills"]
                    if skill["name"] == "investment-team")
        named = {
            "段永平视角", "巴菲特视角", "芒格视角", "李录视角",
            "四视角对照表", "分歧仲裁", "综合结论",
        }
        generic_sections = [
            section for section in artifact_rule(item)["required_sections"]
            if section not in named
        ]
        text = build_text(generic_sections, artifact_rule(item)["min_bytes"])
        text += ("\n待办清单：段永平视角、巴菲特视角、芒格视角、李录视角、"
                 "四视角对照表、分歧仲裁、综合结论。\n")
        rc, status, log = self._drive(
            item, artifact_rule(item)["required_sections"],
            green_evidence(item), green_begin_extra(item),
            artifact_text=text)
        self.assertEqual(
            (rc, status), (1, "FAIL"),
            f"正文提及章节名不得冒充真实 Markdown 章节\n{log}",
        )

    def test_investment_team_numbered_named_headings_pass(self):
        item = next(skill for skill in REGISTRY["skills"]
                    if skill["name"] == "investment-team")
        named = item["artifact_rules"][0]["required_heading_sections"]
        other_sections = [
            section for section in artifact_rule(item)["required_sections"]
            if section not in named
        ]
        text = build_text(other_sections, artifact_rule(item)["min_bytes"])
        for number, section in enumerate(named, start=3):
            text += f"\n#### {number}. {section}\n合规章节正文。\n"
        rc, status, log = self._drive(
            item, artifact_rule(item)["required_sections"],
            green_evidence(item), green_begin_extra(item),
            artifact_text=text)
        self.assertEqual(
            (rc, status), (0, "PASS"),
            f"模板使用的编号 Markdown 标题应通过\n{log}",
        )

    def test_nested_list_fence_does_not_hide_following_named_headings(self):
        item = next(skill for skill in REGISTRY["skills"]
                    if skill["name"] == "investment-team")
        named = item["artifact_rules"][0]["required_heading_sections"]
        other_sections = [
            section for section in artifact_rule(item)["required_sections"]
            if section not in named
        ]
        text = build_text(other_sections, artifact_rule(item)["min_bytes"])
        text += "\n- item\n  - ```text\n    example\n    ```\n\n"
        for number, section in enumerate(named, start=3):
            text += f"#### {number}. {section}\n合规章节正文。\n"
        rc, status, log = self._drive(
            item, artifact_rule(item)["required_sections"],
            green_evidence(item), green_begin_extra(item),
            artifact_text=text)
        self.assertEqual(
            (rc, status), (0, "PASS"),
            f"嵌套列表 fence 收口后，真实根级章节应被识别\n{log}",
        )

    def test_investment_team_non_rendered_headings_do_not_count(self):
        item = next(skill for skill in REGISTRY["skills"]
                    if skill["name"] == "investment-team")
        named = item["artifact_rules"][0]["required_heading_sections"]
        other_sections = [
            section for section in artifact_rule(item)["required_sections"]
            if section not in named
        ]
        fake_headings = "\n".join(
            f"#### {number}. {section}"
            for number, section in enumerate(named, start=3))
        cases = {
            "fenced-code": f"```markdown\n{fake_headings}\n```\n",
            "fenced-code-tilde": f"~~~markdown `literal`\n{fake_headings}\n~~~\n",
            "list-fenced-code": (
                "- ```markdown\n"
                + "\n".join(f"  {line}" for line in fake_headings.splitlines())
                + "\n  ```\n"),
            "html-comment": f"<!--\n{fake_headings}\n-->\n",
            "html-block": f"<div>\n{fake_headings}\n</div>\n",
            "html-processing-instruction": (
                f"<?hidden\n{fake_headings}\n?>\n"),
            "html-cdata": f"<![CDATA[\n{fake_headings}\n]]>\n",
            "html-declaration": f"<!HIDDEN\n{fake_headings}\n>\n",
            "html-closed-before-blank": (
                f"<div></div>\n{fake_headings}\n\n"),
        }
        for case, hidden_text in cases.items():
            with self.subTest(case=case):
                text = build_text(
                    other_sections, artifact_rule(item)["min_bytes"])
                text += "\n" + hidden_text
                rc, status, log = self._drive(
                    item, artifact_rule(item)["required_sections"],
                    green_evidence(item), green_begin_extra(item),
                    artifact_text=text)
                self.assertEqual(
                    (rc, status), (1, "FAIL"),
                    f"{case} 中的标题不得冒充可渲染章节\n{log}",
                )

    def test_red_missing_domain_evidence_fails(self):
        for item in REGISTRY["skills"]:
            with self.subTest(contract=item["name"], case="RED-evidence"):
                sections, evidence = red_evidence_case(item)
                rc, status, log = self._drive(
                    item, sections, evidence, green_begin_extra(item))
                self.assertEqual(rc, 1,
                                 f"[{item['name']}] RED-evidence finalize 应 "
                                 f"exit 1; status={status}\n{log}")
                self.assertEqual(status, "FAIL",
                                 f"[{item['name']}] RED-evidence computed_status "
                                 f"应 FAIL, 实际 {status}\n{log}")

    def test_red_wrong_applicability_fails(self):
        for item in REGISTRY["skills"]:
            with self.subTest(contract=item["name"], case="RED-applicability"):
                sections = artifact_rule(item)["required_sections"]
                rc, status, log = self._drive(
                    item, sections, red_applicability_evidence(item),
                    green_begin_extra(item))
                self.assertEqual(rc, 1,
                                 f"[{item['name']}] RED-applicability finalize 应 "
                                 f"exit 1; status={status}\n{log}")
                self.assertEqual(status, "FAIL",
                                 f"[{item['name']}] RED-applicability "
                                 f"computed_status 应 FAIL, 实际 {status}\n{log}")


if __name__ == "__main__":
    unittest.main()
