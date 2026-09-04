#!/usr/bin/env python3
"""全量公司分析 Gate v2：负责确定性登记、实质验收、晋级与最终收口。

WorkBuddy Runtime 负责调度；Gate 不启动 Agent、不读取报告正文做主观判断，
但会确定性验证 Contract/Result Bundle、报告结构、路径、哈希和状态机。
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from full_analysis_snapshot import analysis_snapshot
from financial_rigor import preflight_diagnose_params
from evidence_receipt import (
    ensure_signing_secret, load_journal, load_run_id, load_run_started_at,
    load_signing_secret, verify_executor_receipt,
)
import full_analysis_runtime as runtime_mod


TOOLS_DIR = Path(__file__).resolve().parent
from full_analysis_contract import CONTRACT_PATH, ContractError  # noqa: E402
import full_analysis_contract as contract_mod  # noqa: E402
# Substance 深模块（2026-08-30 架构评审候选①）：实质校验与契约语义常量的唯一真源。
# gate 保留 re-export 以兼容既有 import（mk_result_bundle 四常量、测试直捅）。
from substance import (  # noqa: E402,F401
    ALWAYS_APPLICABLE_PREDICATES,
    COMPLETED_STATUSES,
    DISSENT_RE,
    FAIL_MIN_BYTES,
    HEADING_RATIO_CAP,
    NA_MIN_BYTES,
    NA_PREDICATE_FIELDS,
    NA_REQUIRED_HEADINGS,
    NAMED_DISSENT_DEFAULT,
    PWL_ALLOWLIST,
    RESULT_STATUSES,
    ROLE_NAME_MAP,
    SUBSTANTIVE_MIN_CHARS,
    SUCCESS_TERMINAL_STATUSES,
    SUMMARY_MIN_BYTES,
    SUMMARY_REQUIRED_HEADINGS,
    section_blocks as _section_blocks,
    substance_errors as _substance_errors,
)
DEFAULT_REGISTRY = CONTRACT_PATH
RESULT_SCHEMA_PATH = TOOLS_DIR / "full_analysis_result_schema.json"
# Run 目录布局（2026-08-30 架构评审候选②）：路径常量与判定谓词收进 run_layout，
# 此前 5 处字面量散布 gate / mk_result_bundle / scripts 三模块。
# gate 保留 re-export 以兼容既有 import。
from run_layout import (  # noqa: E402,F401
    ATTEMPTS_REL,
    EVIDENCE_REL,
    EVENTS_REL,
    MANIFEST_REL,
    RUNTIME_STATE_REL,
    SUMMARY_ATTEMPTS_REL,
    in_attempts,
    in_summary_attempts,
)
# 状态文件 I/O 单一所有者（2026-08-30 候选②·完整版）：原子写/事件/账本收进 run_store，
# gate 只保留"何时写、写什么策略"（schema 校验、updated_at 等）。
import run_store  # noqa: E402
# Bundle 准入判定单一真源（2026-08-30 候选③）：占位水印扫描与 NA 证明规则
# 收进 bundle_checks（纯判定），gate 与 mk_result_bundle 共用，各包装呈现。
import bundle_checks  # noqa: E402
# 派生展示件（2026-08-30 候选⑩）：静态 import 取代 importlib 文件加载——
# 单次导入无重复 exec 开销；两模块均无反向依赖（零循环），import 副作用为零。
import full_analysis_html  # noqa: E402
_SCRIPTS_DIR = str(TOOLS_DIR.parent / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
import build_company_index  # noqa: E402
TZ_SHANGHAI = timezone(timedelta(hours=8))


class GateError(Exception):
    def __init__(self, message: str, code: int = 1):
        self.code = code
        super().__init__(message)


# 回执伪造标记（v3.4.14）：PASS 回执的 argv/详情/输出含这些串即视为自报成功而无真实执行。
_FORGERY_TOKENS = ("placeholder", "test_fixture", "未连接真实命令日志",
                   "fixture", "mock", "mocked", "simulated", "fake")


def _forgery_token_in(text: str | None) -> str | None:
    t = (text or "").lower()
    for tok in _FORGERY_TOKENS:
        if tok in t:
            return tok
    return None


def now_iso() -> str:
    return datetime.now(TZ_SHANGHAI).isoformat()


# 原子写原语直接别名绑定 run_store（同一对象，机器可证单一真源；保留名兼容既有调用/测试）
atomic_write_json = run_store.atomic_write_json
atomic_write_text = run_store.atomic_write_text


def atomic_copy(
    source: Path,
    target: Path,
    *,
    expected_bytes: int | None = None,
    expected_sha256: str | None = None,
) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else 0o644
    fd, name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as out, source.open("rb") as inp:
            shutil.copyfileobj(inp, out)
            out.flush()
            os.fsync(out.fileno())
        copied = Path(name)
        if (expected_bytes is not None
                and copied.stat().st_size != expected_bytes):
            raise GateError(
                f"复制期间 artifact bytes 发生变化: {source}")
        if (expected_sha256 is not None
                and sha256_file(copied) != expected_sha256):
            raise GateError(
                f"复制期间 artifact sha256 发生变化: {source}")
        os.chmod(name, mode)
        os.replace(name, target)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _git_head_commit() -> str | None:
    """读取仓库 HEAD commit（无 git 环境时为 None；仅作版本记录，不阻断）。"""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True,
            timeout=5, cwd=Path(__file__).resolve().parents[1],
        )
        return proc.stdout.strip() if proc.returncode == 0 and proc.stdout.strip() else None
    except Exception:
        return None


def _git_stale_check() -> dict:
    """检测仓库 checkout 是否过期（HEAD 落后于最新发版 tag）。

    返回 {"stale": bool|None, "head": str, "head_tag": str|None, "latest_tag": str|None, "detail": str}。
    三态语义（v3.4.9 起 cmd_init 对 True 与 None 均拒绝，即 fail-close）：
    stale=False 放行（HEAD 为最新 tag 或领先）；stale=True 拒绝（HEAD 落后）；
    stale=None 拒绝（无 git 环境/无 tag/命令异常，不可判定）。
    唯一放行路径是显式 --allow-stale（人工确认目标版本无误后覆盖）。
    """
    repo = Path(__file__).resolve().parents[1]
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                              timeout=5, cwd=repo)
        if head.returncode != 0:
            return {"stale": None, "head": "", "head_tag": None, "latest_tag": None,
                    "detail": "git rev-parse HEAD 失败，版本门禁未生效"}
        head_sha = head.stdout.strip()
        # HEAD 是否恰为某 tag（精确匹配）
        exact = subprocess.run(["git", "describe", "--tags", "--exact-match", "HEAD"],
                               capture_output=True, text=True, timeout=5, cwd=repo)
        head_tag = exact.stdout.strip() if exact.returncode == 0 else None
        # 最新发版 tag（语义化排序）
        tags = subprocess.run(["git", "tag", "--list", "v*"],
                              capture_output=True, text=True, timeout=5, cwd=repo)
        tag_list = [t for t in tags.stdout.split() if t]
        if not tag_list:
            return {"stale": None, "head": head_sha, "head_tag": head_tag,
                    "latest_tag": None, "detail": "仓库无 v* tag（未 fetch tags？），版本门禁未生效"}
        latest = sorted(tag_list, key=lambda t: [int(x) for x in t.lstrip("v").split(".")])
        latest_tag = latest[-1]
        if head_tag == latest_tag:
            return {"stale": False, "head": head_sha, "head_tag": head_tag,
                    "latest_tag": latest_tag, "detail": f"HEAD 恰为最新发版 tag {latest_tag}"}
        # HEAD 未精确命中最新 tag：检查 HEAD 是否可到达 latest_tag（即落后于它）
        is_ancestor = subprocess.run(
            ["git", "merge-base", "--is-ancestor", latest_tag, "HEAD"],
            capture_output=True, text=True, timeout=5, cwd=repo)
        if is_ancestor.returncode == 0:
            return {"stale": False, "head": head_sha, "head_tag": head_tag,
                    "latest_tag": latest_tag, "detail": f"HEAD 包含最新 tag {latest_tag}（领先或平级）"}
        return {"stale": True, "head": head_sha, "head_tag": head_tag,
                "latest_tag": latest_tag,
                "detail": f"HEAD({head_sha[:8]}) 落后于最新发版 tag {latest_tag}：checkout 过期"}
    except Exception as exc:  # 检测异常：不静默放行，标记不确定由调用方决定
        return {"stale": None, "head": "", "head_tag": None, "latest_tag": None,
                "detail": f"版本检测异常跳过: {exc}"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path, label: str) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GateError(f"{label} 不可读或非法 JSON: {path}: {exc}", 2)
    if not isinstance(value, dict):
        raise GateError(f"{label} 顶层必须为对象: {path}", 2)
    return value


def load_registry(path: Path) -> dict:
    """薄包装：委托 Contract 深模块（full_analysis_contract.py），错误翻译为 GateError。"""
    try:
        return contract_mod.load_contract(path, strict=True)
    except ContractError as exc:
        raise GateError(str(exc), exc.code) from exc


def manifest_path(run_root: Path) -> Path:
    return run_store.manifest_path(run_root)


def load_manifest(run_root: Path) -> dict:
    try:
        manifest = run_store.load_manifest(run_root)
    except run_store.RunStoreError as exc:
        raise GateError(str(exc), exc.code) from exc
    if manifest.get("manifest_schema_version") != "full-analysis-manifest/v2":
        raise GateError("只接受 full-analysis-manifest/v2 manifest", 2)
    return manifest


def save_manifest(run_root: Path, manifest: dict) -> None:
    manifest["run"]["updated_at"] = now_iso()
    run_store.write_manifest(run_root, manifest)


def append_event(run_root: Path, event: dict) -> None:
    run_store.append_event(run_root, event)


def safe_relative(run_root: Path, value: str) -> Path:
    candidate = Path(value)
    if candidate.is_absolute() or "" in candidate.parts or "." in candidate.parts or ".." in candidate.parts:
        raise GateError(f"路径非法: {value!r}")
    root = Path(run_root).resolve()
    target = (root / candidate)
    try:
        if target.exists() and not target.resolve().is_relative_to(root):
            raise GateError(f"路径越出 run_root: {value!r}")
    except OSError as exc:
        raise GateError(f"路径无法解析: {value!r}: {exc}")
    return candidate


def find_skill(registry: dict, skill_id: str) -> dict:
    """薄包装：委托 Contract 深模块，ContractError 翻译为 GateError（消息逐字一致）。"""
    try:
        return contract_mod.find_skill(registry, skill_id)
    except ContractError as exc:
        raise GateError(str(exc), exc.code) from exc


def _schema_type_matches(value, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return True


def _validate_schema_value(value, schema: dict, path: str = "$") -> None:
    """校验 Result Bundle schema 使用的 JSON Schema 子集。"""
    expected = schema.get("type")
    expected_types = expected if isinstance(expected, list) else [expected]
    if expected and not any(
        _schema_type_matches(value, item) for item in expected_types
    ):
        raise GateError(f"{path} 类型非法，期望 {expected}")
    if "const" in schema and value != schema["const"]:
        raise GateError(f"{path} 必须等于 {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        raise GateError(f"{path} 不在允许枚举中")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            raise GateError(f"{path} 长度不足")
        if schema.get("pattern") and not re.search(schema["pattern"], value):
            raise GateError(f"{path} 格式非法")
    if isinstance(value, int) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise GateError(f"{path} 小于最小值 {schema['minimum']}")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            raise GateError(f"{path} 项目数不足")
        if schema.get("uniqueItems"):
            encoded = [
                json.dumps(item, ensure_ascii=False, sort_keys=True)
                for item in value
            ]
            if len(encoded) != len(set(encoded)):
                raise GateError(f"{path} 含重复项目")
        item_schema = schema.get("items")
        if item_schema:
            for index, item in enumerate(value):
                _validate_schema_value(item, item_schema, f"{path}[{index}]")
    if isinstance(value, dict):
        required = set(schema.get("required", []))
        missing = sorted(required - set(value))
        if missing:
            raise GateError(f"{path} 缺字段 {missing}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extra = sorted(set(value) - set(properties))
            if extra:
                # E7: 报错时列出允许键，减少 Agent 试错
                allowed_keys = ", ".join(sorted(properties)) or "（无）"
                raise GateError(f"{path} 含未知字段 {extra}；允许键: {allowed_keys}")
        for key, item in value.items():
            if key in properties:
                _validate_schema_value(item, properties[key], f"{path}.{key}")


def _manifest_or_empty(run_root: Path) -> dict:
    """返回 manifest；缺失/非法时返回空壳（仅含 sources=[]），让调用方降级而非崩溃。"""
    try:
        return load_manifest(run_root)
    except Exception:
        return {"sources": [], "skills": []}


def _admit_artifact_checks(bundle: dict, run_root: Path, skill: dict, status: str) -> list:
    """准入判定的文件侧检查（check_artifacts=True 时启用）：artifact 文件存在-字节-sha
    一致性、报告字节下限、多角色备忘录、实质校验、NA 章节。仅读取，不写文件。返回拦截列表。"""
    errs: list = []
    records = bundle.get("artifact_records") or []
    if not records:
        return errs
    rec = records[0]
    try:
        rel = safe_relative(run_root, rec.get("path", ""))
    except GateError as exc:
        return [str(exc)]
    source = Path(run_root) / rel
    if not source.is_file() or source.is_symlink() or not in_attempts(rel):
        return [f"artifact 必须来自 {ATTEMPTS_REL.as_posix()} 且为普通文件: {rel}"]
    actual_bytes = source.stat().st_size
    if actual_bytes != rec.get("bytes") or sha256_file(source) != rec.get("sha256"):
        errs.append(f"artifact bytes/sha256 与 Result Bundle 不一致: {rel}")
    if status in {"PASS", "PASS_WITH_LIMITATIONS"}:
        min_bytes = skill["artifact"].get("min_bytes", 0)
    elif status == "NOT_APPLICABLE":
        min_bytes = NA_MIN_BYTES
    else:  # FAIL
        min_bytes = FAIL_MIN_BYTES
    if isinstance(min_bytes, int) and min_bytes > 0 and actual_bytes < min_bytes:
        errs.append(f"artifact 字节数 {actual_bytes} < 下限 {min_bytes}（{skill['skill_id']}）")
    try:
        txt = source.read_text(encoding="utf-8")
    except Exception:
        txt = ""
    if status in {"PASS", "PASS_WITH_LIMITATIONS"}:
        roles = skill.get("roles") or {}
        if roles.get("mode") == "independent_then_integrator":
            attempt_dir = source.parent
            missing = []
            for role in roles.get("required_roles", []):
                if role == "integrator":
                    continue
                memo = attempt_dir / f"role-{role}.md"
                if not memo.is_file() or memo.is_symlink() or memo.stat().st_size < 300:
                    missing.append(role)
            if missing:
                errs.append(f"多角色 skill {skill['skill_id']} 缺角色独立备忘录: {missing}")
        errs += _substance_errors(skill, txt)
    elif status == "NOT_APPLICABLE":
        missing_headings = [
            h for h in NA_REQUIRED_HEADINGS
            if not re.search(rf"^#{{1,6}}\s+{re.escape(h)}\s*$", txt, re.M)
        ]
        if missing_headings:
            errs.append(f"负向验收报告缺必需章节: {missing_headings}")
    return errs


def admit_bundle(bundle: dict, run_root: Path, registry: dict, *,
                 check_artifacts: bool = True) -> list:
    """单一准入判定（v3.4.15）：生成器、cmd_ingest、correction 共用同一函数。

    返回拦截消息列表（空=可接受）。此前三处校验分叉——生成器只查标题/字节、
    validate_result_bundle 不查角色memo/实质、ingest 才查——导致「rc0 ⟺ Gate 接受」
    长期为假。本函数聚合全部确定性拦截：
      schema/status/artifact_id/calc 参数/回执绑定/占位水印/NA 证明/run_id
      （check_artifacts=True 时追加）artifact 文件存在-字节-sha/角色memo/实质校验/报告字节下限。
    validate_result_bundle(check_artifacts=False) 与 cmd_ingest/生成器(check_artifacts=True)
    都走这里，保证「生成器 rc0 ⟺ Gate 真正接受」成为机器事实。
    """
    errs: list = []
    schema = load_json(RESULT_SCHEMA_PATH, "Result Bundle schema")
    try:
        _validate_schema_value(bundle, schema)
    except GateError as exc:
        errs.append(str(exc))
    required = set(schema["required"])
    allowed = set(schema.get("properties", {})) or required
    missing = sorted(required - set(bundle))
    if missing:
        errs.append(f"Result Bundle 顶层缺字段: {missing}")
    extra = sorted(set(bundle) - allowed)
    if extra:
        errs.append(f"Result Bundle 含未声明字段: {extra}")
    if bundle.get("schema_version") != "result-schema/v1":
        errs.append("Result Bundle schema_version 必须为 result-schema/v1")
    status = bundle.get("status")
    if status not in RESULT_STATUSES:
        errs.append(f"Result Bundle status 非法: {status!r}")
    if status == "FAIL" and not isinstance(bundle.get("error"), dict):
        errs.append("FAIL Result Bundle 必须提供 error")
    if status in SUCCESS_TERMINAL_STATUSES and bundle.get("error") is not None:
        errs.append("成功/PWL/NA Result Bundle 的 error 必须为 null")
    if not isinstance(bundle.get("pwl_candidates"), list) \
            or not set(bundle.get("pwl_candidates") or []).issubset(PWL_ALLOWLIST):
        errs.append("pwl_candidates 含未注册的 PWL 原因")

    # 结构校验未过（schema/字段/枚举/类型）时提前返回，避免对畸形子结构
    # （如 fact_updates 为字符串数组、artifact_records 非数组）做 .get() 而崩溃；
    # 深层校验（evidence_rules/calc/receipt/占位/NA/artifact 文件）均假设子结构良构。
    if errs:
        return errs

    try:
        skill = find_skill(registry, bundle.get("skill_id"))
    except GateError as exc:
        return [str(exc)]

    try:
        mfid = load_manifest(run_root)["run"]["run_id"]
    except Exception:
        mfid = None
    if mfid is not None and bundle.get("run_id") != mfid:
        errs.append("Result Bundle run_id 与 manifest 不一致")
    if not isinstance(bundle.get("artifact_records"), list):
        errs.append("artifact_records 必须为数组")
    else:
        expected = skill["artifact"].get("artifact_id", f"artifact.{skill['skill_id']}")
        if status in {"PASS", "PASS_WITH_LIMITATIONS"}:
            if len(bundle["artifact_records"]) != 1:
                errs.append(f"{skill['skill_id']} 必须恰好提交 1 个正式 artifact")
            elif bundle["artifact_records"][0].get("artifact_id") != expected:
                errs.append(f"artifact_id 不匹配: 期望 {expected}")
            if bundle.get("not_applicable") is not None:
                errs.append("PASS/PASS_WITH_LIMITATIONS 不得携带 not_applicable")
        elif status == "NOT_APPLICABLE":
            expected_na = f"artifact.na.{skill['skill_id']}"
            if len(bundle["artifact_records"]) != 1:
                errs.append(f"{skill['skill_id']} 的 NOT_APPLICABLE 必须恰好提交 1 个负向验收 artifact")
            elif bundle["artifact_records"][0].get("artifact_id") != expected_na:
                errs.append(f"负向验收 artifact_id 不匹配: 期望 {expected_na}")
            try:
                _validate_not_applicable(bundle, skill, _manifest_or_empty(run_root))
            except GateError as exc:
                errs.append(str(exc))
        elif bundle.get("not_applicable") is not None:
            errs.append("非 NOT_APPLICABLE 状态不得携带 not_applicable")
    if status in {"PASS", "PASS_WITH_LIMITATIONS"}:
        errs += _precheck_calculation_params(bundle) or []
        errs += _precheck_command_receipts(bundle, skill, run_root) or []
        errs += _precheck_placeholder_evidence(bundle) or []
    if check_artifacts:
        errs += _admit_artifact_checks(bundle, run_root, skill, status)
    return errs


def validate_result_bundle(bundle: dict, run_root: Path, registry: dict) -> None:
    """轻量准入（check_artifacts=False）：仅做 schema/逻辑校验，不触碰 artifact 文件。

    供单测/局部校验使用；完整准入（含文件/实质/角色memo）由 cmd_ingest 与生成器
    通过 admit_bundle(check_artifacts=True) 调用，保证三处口径一致。"""
    errs = admit_bundle(bundle, run_root, registry, check_artifacts=False)
    if errs:
        raise GateError(
            f"{bundle.get('skill_id')} 准入拦截 {len(errs)} 处：\n" + "\n".join(errs))
def _precheck_calculation_params(bundle: dict) -> list:
    """确定性 dry-run 每条可重放 calculation，仅拦参数错（financial_rigor rc=2）。

    返回错误消息列表（空=通过），由 validate_result_bundle 聚合抛出。与 audit 重放互补：
    audit 事后判 PASS/CONFLICT/FAIL 并写 expected；本门禁只把「一个笔误毁全量」挡在
    提交当下——缺必需 flag、类型转换失败等 argparse 层错误（退出码 2）记为错误，
    Agent 原地修正后重交，不必跑完整轮才在 audit 暴露。
    退出码语义对齐 financial_rigor：0 通过 / 1 业务不通过（放行）/ 2 参数错（记错误）。
    每条消息含真实 argv + stderr 末三行 + 必需参数清单，供 Agent 一次改对。
    """
    errors = []
    for calc in bundle.get("calculation_requests") or []:
        diagnosis = preflight_diagnose_params(calc.get("operation"), calc.get("args"))
        if diagnosis.get("ok"):
            continue
        hint = diagnosis.get("required_hint") or []
        tail = "；".join(line for line in diagnosis.get("stderr_tail") or [])
        errors.append(
            f"  - [参数错误] {calc.get('calculation_id')}（operation={calc.get('operation')}）"
            f"rc={diagnosis.get('rc')}"
            + (f"；必需参数 {hint}" if hint else "")
            + (f"；argparse: {tail}" if tail else "")
            + f"\n      argv: {' '.join(diagnosis.get('argv') or [])}")
    return errors


def _receipt_binding_mode(run_root: Path) -> str:
    """判定该 run 采用哪一档回执绑定校验：'executor'（v2）或 'legacy'（v1）。

    有签名密钥（即经 v3.4.15 的 start 初始化，或执行器已自愈生成）→ executor 档，
    PASS 回执必须由执行器签发。无密钥 → legacy 档，只能退回 v1 的
    「argv/output 非空 + 无伪造标记」弱校验。

    为什么允许 legacy 降级：v3.4.15 之前初始化的在途 run 与单测裸目录都没有密钥，
    fail-close 会把它们全部打死。**但要诚实地说清这不是安全边界**——能写
    result.json 的进程同样能删掉密钥文件把自己降级到 legacy。真正的防线是
    「执行器是唯一便捷路径」，而非密码学不可绕过。降级发生时 doctor 会看到
    journal 为空的指纹。
    """
    return "executor" if load_signing_secret(run_root) else "legacy"


def _legacy_receipt_binding_errors(receipt: dict) -> list:
    """v1 弱绑定（无密钥的历史 run）：argv/output 非空即可，仅能拦最粗劣的自报。"""
    rid = receipt.get("receipt_id")
    argv = receipt.get("argv")
    if not (isinstance(argv, list) and argv
            and all(isinstance(a, str) and a.strip() for a in argv)):
        return [f"  - [回执无执行绑定] {rid} 状态 PASS 但缺 argv（真实执行的命令行）；"
                f"禁止自报成功而无真实执行痕迹。请补 argv（实际命令）与 output（落盘引用）。"]
    out = receipt.get("output")
    if not (isinstance(out, str) and out.strip()):
        return [f"  - [回执无执行绑定] {rid} 状态 PASS 但缺 output（真实执行输出/落盘引用）；"
                f"禁止自报成功而无真实执行痕迹。"]
    return []


def _precheck_command_receipts(bundle: dict, skill: dict, run_root: Path) -> list:
    """校验回执：① 操作不越出契约白名单；② PASS 回执必须由执行器真实签发；
    ③ 不含伪造标记。

    返回错误消息列表（空=通过），由 admit_bundle 聚合。
    白名单 = 契约 required_command_operations.values ∪ conditional_command_operations
    各 op。非 PASS 的白名单外操作不在此拦（可能合法豁免，满足率属 audit）。

    ② 的历史：v3.4.14 曾称之为「执行绑定」，实际只检查 argv/output 两个字符串非空
    ——而它们都是 Agent 在同一份 JSON 里自填的，跑一条无关命令附任意输出即可通过。
    v3.4.15 起改为校验执行器签发的回执（签名/退出码/输出摘要/时间窗/op↔argv/journal
    留痕六项，见 tools/evidence_receipt.py）。无密钥的历史 run 降级到 v1 弱校验。
    """
    rules = skill.get("evidence_rules") or []
    whitelist: set = set()
    for rule in rules:
        kind = rule.get("kind")
        if kind == "required_command_operations":
            whitelist.update(rule.get("values", []))
        elif kind == "conditional_command_operations":
            for value in rule.get("values", []):
                if isinstance(value, str):
                    whitelist.add(value)
                elif isinstance(value, dict) and value.get("op"):
                    whitelist.add(value["op"])
    if not whitelist:
        return []  # 契约未声明命令操作清单的技能（非 ashare 类）不做白名单校验
    mode = _receipt_binding_mode(run_root)
    secret = load_signing_secret(run_root) if mode == "executor" else None
    run_id = load_run_id(run_root) or bundle.get("run_id") or ""
    run_started = load_run_started_at(run_root)
    journal = load_journal(run_root)
    errors = []
    for receipt in bundle.get("command_receipts") or []:
        if receipt.get("status") != "PASS":
            continue
        op = str(receipt.get("operation"))
        rid = receipt.get("receipt_id")
        if op not in whitelist:
            errors.append(
                f"  - [回执越界] 成功操作 {op!r}（{rid}）不在契约白名单内；operation 必须取自"
                f"契约白名单（required_command_operations / conditional_command_operations 声明的 op），"
                f"禁止自定义操作或虚构成功。请改用白名单内操作重跑，"
                f"或把该回执状态改为 FAIL/UNAVAILABLE 并附 reason 说明。")
            continue
        if secret is None:
            bound = _legacy_receipt_binding_errors(receipt)
        else:
            bound = verify_executor_receipt(
                receipt, run_root=run_root, run_id=run_id, secret=secret,
                run_started_at=run_started, journal=journal)
        if bound:
            errors += bound
            continue
        blob = " ".join(str(a) for a in receipt.get("argv") or []) + " " + " ".join(
            str(receipt.get(k, "")) for k in ("detail", "output", "reason"))
        token = _forgery_token_in(blob)
        if token:
            errors.append(
                f"  - [回执伪造痕迹] {rid} 的 argv/详情/输出含伪造标记 {token!r}；"
                f"PASS 回执必须来自真实执行，不得含 PLACEHOLDER/TEST_FIXTURE/未连接真实命令日志 等。")
    return errors


def _precheck_placeholder_evidence(bundle: dict) -> list:
    """拒收 mk_result_bundle「结构地板」生成的 PLACEHOLDER 水印证据（v3.4.10）。

    返回错误消息列表（空=通过），由 validate_result_bundle 聚合抛出。
    扫描口径委托 bundle_checks.placeholder_offenders（与 mk 生成侧同一真源）；
    本函数只负责把 offender 包装成逐类建议消息。
    """
    _ADVICE = {
        "fact": "（生成器结构地板，非真实调研）；请用真实数值替换，"
                f"并通过 --extra-evidence 提供真实 fact_updates。",
        "source": "（生成器结构地板，非真实检索）；请用真实检索来源替换，"
                  f"并通过 --extra-sources 提供真实 source_records。",
        "calculation": "（非真实验算）；请通过 --extra-calculations 提供真实 calculation_requests。",
        "judgment": "（非真实判断）；请通过 --extra-judgments 提供真实 judgments。",
        "receipt": "（命令未实际执行）；请通过 --extra-receipts 提供真实回执，"
                   "或如实标注 UNAVAILABLE/FAIL + reason。",
    }
    _LABEL = {"fact": "fact {id} 的 value 为 PLACEHOLDER 水印",
              "source": "source {id} 为 PLACEHOLDER 占位来源",
              "calculation": "calculation {id} 为生成器结构地板",
              "judgment": "judgment {id} 为生成器结构地板",
              "receipt": "command_receipt {id} 为生成器结构地板"}
    errors = []
    for kind, entry_id in bundle_checks.placeholder_offenders(bundle):
        errors.append(
            f"  - [占位证据] {_LABEL[kind].format(id=entry_id)}{_ADVICE[kind]}"
        )
    return errors


def _validate_not_applicable(bundle: dict, skill: dict, manifest: dict) -> None:
    proof = bundle.get("not_applicable")
    if not isinstance(proof, dict):
        raise GateError("NOT_APPLICABLE 必须提供可由 Gate 验证的 not_applicable 证明")
    expected_predicate = (skill.get("applicability") or {}).get("predicate")
    if proof.get("predicate") != expected_predicate:
        raise GateError(
            f"not_applicable predicate 不匹配: 期望 {expected_predicate!r}")
    if proof.get("alternative") != (skill.get("applicability") or {}).get("alternative"):
        raise GateError("not_applicable alternative 与 Contract 不一致")
    if expected_predicate in ALWAYS_APPLICABLE_PREDICATES:
        raise GateError(
            f"{skill['skill_id']} 的适用性谓词 {expected_predicate!r} 始终适用，不得标记 N/A")

    fact = next(
        (item for item in bundle.get("fact_updates") or []
         if item.get("fact_id") == proof.get("fact_id")),
        None,
    )
    if not fact:
        raise GateError("not_applicable.fact_id 必须引用本次提交的判定事实")
    # 谓词证伪规则委托 bundle_checks（与 mk 生成侧同一真源，特例只写一遍）
    violation = bundle_checks.na_predicate_violation(expected_predicate, fact)
    if violation:
        raise GateError(
            f"not_applicable 判定事实不能证明谓词 {expected_predicate!r} 为假"
            f"（期望 {violation}，实际 field={fact.get('field')!r} value={fact.get('value')!r}）")

    source_ids = fact.get("source_ids") or []
    known_source_ids = {
        item.get("source_id")
        for item in [*(manifest.get("sources") or []), *(bundle.get("source_records") or [])]
        if item.get("source_id")
    }
    if not source_ids or any(source_id not in known_source_ids for source_id in source_ids):
        raise GateError("not_applicable 判定事实必须引用已登记来源")
    if not bundle.get("limitations"):
        raise GateError("NOT_APPLICABLE 必须显式记录 limitations")


def build_run_root(repo_root: Path, code: str, company: str) -> Path:
    stamp = datetime.now(TZ_SHANGHAI).strftime("%Y%m%d-%H%M%S")
    short = hashlib.sha256(f"{code}:{company}:{stamp}".encode()).hexdigest()[:6]
    return repo_root / "local" / "Company" / f"{code}-{company}" / f"{stamp}-{short}"


def _company_base_from_run_root(root: Path) -> Path:
    root = Path(root)
    company_dir = root.parent
    company_base = company_dir.parent
    if not root.is_dir() or not company_dir.is_dir() or not company_base.is_dir():
        raise GateError(f"无法从 run_root 推导公司目录: {root}")
    return company_base


def cmd_init(args: argparse.Namespace) -> int:
    registry = load_registry(Path(args.registry))
    if args.platform != "workbuddy":
        raise GateError("生产全量分析只接受 WorkBuddy platform", 2)
    # 2026-08-30：放行港股 .HK（5 位代码，如 00700.HK）；A 股仍限 6 位。
    if not re.match(r"^[0-9A-Z]{6}\.(SH|SZ|BJ)$|^[0-9A-Z]{5}\.HK$", args.code):
        raise GateError(f"证券代码格式非法: {args.code}", 2)
    # v3.3.10：init 时即构建依赖图并落盘，供 runtime 依赖门禁（next_work 按 depends_on）使用。
    # 契约环在 init 前即拒绝（runtime/contract 校验器同源语义，此处刻意不 import 校验器）。
    dep_graph = runtime_mod.build_dependency_graph(registry["skills"])
    dep_cycle = runtime_mod.detect_dependency_cycle(dep_graph)
    if dep_cycle:
        raise GateError(f"contract depends_on 存在依赖环: {' -> '.join(dep_cycle)}", 2)
    # v3.4.4：start 版本机器门禁（E1 机器化）——不确定或过期的 checkout 不得启动新 run。
    # 仅当显式 --allow-stale 才放行（人工确认目标版本后覆盖）。
    if not getattr(args, "allow_stale", False):
        stale = _git_stale_check()
        if stale["stale"] is not False:  # True 落后 or None 不可判定 → 拒绝
            reason = stale["detail"]
            hint = f"（git checkout {stale['latest_tag']}）" if stale.get("latest_tag") else ""
            raise GateError(
                f"E1 版本门禁：{reason}{hint}；"
                f"确认目标版本无误可用 --allow-stale 覆盖", 2)
    root = Path(args.run_root) if args.run_root else build_run_root(Path(args.repo_root), args.code, args.company)
    if root.exists() and any(root.iterdir()):
        raise GateError(f"run_root 已存在且非空: {root}", 2)
    root.mkdir(parents=True, exist_ok=True)
    stage_dirs = list(registry["stage_dirs"].values())
    for rel in stage_dirs + ["06-负向验收"]:
        (root / rel).mkdir(parents=True, exist_ok=True)
    for rel in ["evidence/attempts", "evidence/work-packets", "evidence/snapshots",
                "evidence/preflight", "evidence/commands", "evidence/sources",
                "evidence/audit", "evidence/locks"]:
        (root / rel).mkdir(parents=True, exist_ok=True)
    run_id = "run-" + hashlib.sha256(str(root).encode()).hexdigest()[:16]
    manifest = {
        "manifest_schema_version": "full-analysis-manifest/v2",
        "contract": {"schema_version": registry["schema_version"],
                      "result_schema_version": registry.get("result_schema_version", "result-schema/v1"),
                      "registry_sha256": sha256_file(Path(args.registry)),
                      "skill_count": len(registry["skills"])},
        "run": {"run_id": run_id, "status": "RUNNING", "created_at": now_iso(), "updated_at": now_iso(),
                "platform": args.platform, "as_of": args.as_of, "run_root": str(root),
                # E10：钉死启动时的契约版本（digest 复用 contract.registry_sha256），
                # finalize 校验防过期编排 run 被准出。
                "contract_commit": _git_head_commit()},
        "company": {"code": args.code, "name": args.company},
        "skills": [{"skill_id": item["skill_id"], "status": "PENDING", "attempts": [], "artifact_records": []}
                   for item in registry["skills"]],
        "artifacts": [], "facts": [], "sources": [], "calculations": [],
        "judgments": [], "command_receipts": [], "role_runs": [],
        "capabilities": {}, "events": [], "delivery": {"summary": None},
    }
    run_store.write_manifest(root, manifest)
    # v3.4.10：normal_target 唯一机器真源 = 2 × 契约单元数 + 1（13 单元 → 27）：
    # preflight（1，计入 used 一次）+ 全员一次成功（13）+ 一轮全员返工余量（13）。
    # 口径与 runtime 的 used 计数严格对齐（used 在 preflight 与每次 job-started 各 +1），
    # 仅作编排器启动时的版本错配核对信号，无运行时阻断逻辑——派发阻断由
    # stop_dispatch_at（软停非 core）与 hard_max（硬停全部）承担。
    # 历史口径：v3.4.9 前为 2N（26），漏计 preflight 导致与 used 实际计数差 1。
    budget_normal_target = 2 * len(registry["skills"]) + 1
    run_store.write_runtime_state(root, {
        "state_version": run_store.STATE_VERSION,
        "run_id": run_id,
        "budget": {
            "normal_target": budget_normal_target, "stop_dispatch_at": 30, "hard_max": 33,
            "used": 0, "preflight_count": 0, "reserved": 0,
        },
        "concurrency": {"max": 2, "current": 0, "cooldown_until": None},
        "authorization": registry["authorization_profile"],
        "run_started_at": now_iso(),
        "dependency_graph": dep_graph,
        "work_units": [{
            "work_unit_id": f"wu-{item['skill_id']}", "skill_id": item["skill_id"],
            "core": item["core"],
            "status": "PENDING", "attempts": 0, "max_attempts": 3,
            "next_retry_at": None,
            "depends_on": dep_graph.get(item["skill_id"], []),
        } for item in registry["skills"]],
    })
    run_store.reset_events(root)
    # v3.4.15：本 run 的回执签名密钥。有它 Gate 才会启用执行器档（executor）回执校验；
    # 缺失则降级到 v1 弱绑定，见 _receipt_binding_mode。
    ensure_signing_secret(root)
    for name in ("facts.json", "sources.json", "calculations.json", "artifacts.json"):
        atomic_write_json(root / EVIDENCE_REL / name, [])
    append_event(root, {"type": "run_initialized", "run_id": run_id})
    print(json.dumps({"run_root": str(root), "run_id": run_id}, ensure_ascii=False))
    return 0




def cmd_self_check(args: argparse.Namespace) -> int:
    """每个 skill 产出后的自我质量校验（优化点 1：质量归属下放到 skill 自身）。

    复用 Gate 边界兜底的同一套 _substance_errors（实质章节 / 分歧交锋 / 标题占比 /
    数据截止日·来源·免责三锚），并对该 skill 的 lean 契约阈值做确定性校验；
    同时校验报告字节下限（artifact.min_bytes）。通过才允许进入 mk_result_bundle /
    submit-result。
    退出码 0 = 通过（无错误）；1 = 发现实质错误（Agent 应修复或 mark-failed）；2 = 文件缺失。
    """
    registry = load_registry(Path(args.registry))
    skill = find_skill(registry, args.skill_id)
    report = Path(args.report)
    if not report.is_file():
        print(json.dumps({"skill_id": args.skill_id, "passed": False,
                          "errors": [f"report 不存在: {report}"]}, ensure_ascii=False, indent=2))
        return 2
    text = report.read_text(encoding="utf-8")
    errors = _substance_errors(skill, text)
    actual_bytes = report.stat().st_size
    min_bytes = skill.get("artifact", {}).get("min_bytes", 0)
    if isinstance(min_bytes, int) and min_bytes > 0 and actual_bytes < min_bytes:
        errors.append(f"报告字节数 {actual_bytes} < 下限 {min_bytes}（{skill['skill_id']}）")
    result = {
        "skill_id": args.skill_id,
        "report": str(report),
        "report_bytes": actual_bytes,
        "min_bytes": min_bytes,
        "passed": not errors,
        "errors": errors,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


# 溯源账本聚合：跨 bundle 合并 facts/sources/calculations 时保持完整性。
# - sources：按 source_id 去重（同一 id = 同一逻辑来源，多技能引用属正常共享）；
#   丢弃无 id 的占位记录（不携带溯源价值，是噪声）。
# - facts：按 fact_id 去重（后到覆盖）；无 source_ids 的管线事实自动挂接
#   规范来源 src.ashare_pipeline（真实来自 ashare_data.py 管线，非编造）。
# - calculations：按 calculation_id 去重；丢弃无 id 的占位记录。
CANONICAL_PIPELINE_SOURCE = "src.ashare_pipeline"


def _merge_provenance(
    manifest: dict,
    bundle: dict,
    *,
    verified_role_runs: list[dict] | None = None,
    run_root: Path | None = None,
) -> None:
    # 证据归因：用 bundle 自带的 skill_id 给每条 fact/calc 打标记（向后兼容，不改 result schema）。
    # 让 Audit 能按 skill 归属证据（lean-v1 契约已无 evidence_rules，此处只做归属打标）。
    owner_skill = bundle.get("skill_id")
    manifest.setdefault("judgments", [])
    manifest.setdefault("command_receipts", [])
    manifest.setdefault("role_runs", [])
    manifest.setdefault("capabilities", {})
    # v3.6.0 修复：sources 与 facts/calcs/judgments 一致改为 last-write-wins（同 id 覆盖）。
    # 此前对已存在 source_id 静默跳过，返工/修正提交的 source 记录（如 url/title 指向新
    # attempt）永远不生效，导致 run 级 manifest 残留指向旧产物的来源（thesis-tracker 第 4 轮
    # 返工实证：正文/判断已用新口径，证据索引层却无法闭环）。
    source_index = {
        s.get("source_id"): i for i, s in enumerate(manifest["sources"])
        if s.get("source_id")
    }
    for src in bundle.get("source_records") or []:
        sid = src.get("source_id")
        if not sid:
            continue
        src = {**src, "skill_id": owner_skill} if owner_skill else src
        if sid in source_index:
            manifest["sources"][source_index[sid]] = src
        else:
            source_index[sid] = len(manifest["sources"])
            manifest["sources"].append(src)

    fact_index = {f.get("fact_id"): i for i, f in enumerate(manifest["facts"]) if f.get("fact_id")}
    for fact in bundle.get("fact_updates") or []:
        fid = fact.get("fact_id")
        refs = fact.get("source_ids")
        if not isinstance(refs, list) or not refs:
            # 无来源的管线事实：挂接规范管线来源，保证可追溯
            fact = {**fact, "source_ids": [CANONICAL_PIPELINE_SOURCE]}
        if owner_skill and not fact.get("skill_id"):
            fact = {**fact, "skill_id": owner_skill}
        if fid and fid in fact_index:
            prior = manifest["facts"][fact_index[fid]]
            prior_skill = prior.get("skill_id")
            # E6: 跨 skill 同 fact_id 覆盖（last-write-wins 抢归因）——audit 按 skill_id
            # 归因事实，核心事实被下游 skill 覆盖会使上游缺 required_fact_fields。
            # 写 warning 事件供 doctor/人工排查（不阻断提交）。
            if owner_skill and prior_skill and owner_skill != prior_skill and run_root is not None:
                append_event(run_root, {
                    "type": "fact_overridden", "fact_id": fid,
                    "from_skill": prior_skill, "to_skill": owner_skill,
                })
            manifest["facts"][fact_index[fid]] = fact
        else:
            if fid:
                fact_index[fid] = len(manifest["facts"])
            manifest["facts"].append(fact)

    # 与 facts/judgments 一致：同 ID 后到覆盖（last-write-wins），
    # 否则返工提交的修正计算会被旧记录静默屏蔽，Audit 永远重放旧参数。
    calc_index = {
        c.get("calculation_id"): i
        for i, c in enumerate(manifest["calculations"])
        if c.get("calculation_id")
    }
    for calc in bundle.get("calculation_requests") or []:
        cid = calc.get("calculation_id")
        if not cid:
            continue
        calc = {**calc, "skill_id": owner_skill} if owner_skill else calc
        if cid in calc_index:
            manifest["calculations"][calc_index[cid]] = calc
        else:
            calc_index[cid] = len(manifest["calculations"])
            manifest["calculations"].append(calc)

    judgment_index = {
        judgment.get("judgment_id"): i
        for i, judgment in enumerate(manifest["judgments"])
        if judgment.get("judgment_id")
    }
    for judgment in bundle.get("judgments") or []:
        record = {**judgment, "skill_id": owner_skill}
        jid = record.get("judgment_id")
        if jid in judgment_index:
            manifest["judgments"][judgment_index[jid]] = record
        else:
            if jid:
                judgment_index[jid] = len(manifest["judgments"])
            manifest["judgments"].append(record)

    # 与 facts/judgments 一致：同 receipt_id 后到覆盖（last-write-wins），
    # 保证返工补充的豁免 reason 能进入 manifest 被 Audit 认可。
    receipt_index = {
        receipt.get("receipt_id"): i
        for i, receipt in enumerate(manifest["command_receipts"])
        if receipt.get("receipt_id")
    }
    for receipt in bundle.get("command_receipts") or []:
        record = {**receipt, "skill_id": owner_skill}
        rid = record.get("receipt_id")
        if rid and rid in receipt_index:
            manifest["command_receipts"][receipt_index[rid]] = record
        else:
            if rid:
                receipt_index[rid] = len(manifest["command_receipts"])
            manifest["command_receipts"].append(record)

    role_keys = {
        (record.get("skill_id"), record.get("attempt_id"), record.get("role_id"))
        for record in manifest["role_runs"]
    }
    # 角色运行不得由 Agent 自证；只接收 Gate 从实际独立备忘录生成的记录。
    for role_run in verified_role_runs or []:
        record = {
            **role_run,
            "skill_id": owner_skill,
            "attempt_id": bundle.get("attempt_id"),
        }
        key = (record.get("skill_id"), record.get("attempt_id"), record.get("role_id"))
        if key not in role_keys:
            manifest["role_runs"].append(record)
            role_keys.add(key)

    for capability in bundle.get("capability_records") or []:
        manifest["capabilities"][capability["capability"]] = capability["available"]

    # 确保规范管线来源在账本中登记（供事实挂接引用）
    if any(CANONICAL_PIPELINE_SOURCE in (f.get("source_ids") or []) for f in manifest["facts"]) \
            and CANONICAL_PIPELINE_SOURCE not in {s.get("source_id") for s in manifest["sources"]}:
        manifest["sources"].append({
            "source_id": CANONICAL_PIPELINE_SOURCE,
            "publisher": "ashare_data.py(Tushare+东财+腾讯)",
            "acquired_at": now_iso(),
        })


def ingest_result(run_root: Path, registry: Path | None = None, result: Path | None = None) -> dict:
    """Result Bundle 登记的唯一进程内入口（RunStore 收拢的第一步，评审候选②）。

    runtime._accept_result 原先 spawn 子进程环回 `full_analysis_gate.py ingest-result`
    靠 stdout 文本回传结果——同一事务拆两进程、以打印代返回。现在 runtime 与 CLI
    共用本函数：gate 校验成为调用方的一步，返回结构化 dict（不再解析文本）。
    抛 GateError = 准入拦截（CLI 侧由 main 翻译为退出码与 stderr）。
    """
    root = Path(run_root)
    registry = load_registry(Path(registry if registry is not None else DEFAULT_REGISTRY))
    result_path = Path(result) if result is not None else None
    assert result_path is not None, "ingest_result 需要 result 路径"
    manifest = load_manifest(root)
    bundle = load_json(result_path, "Result Bundle")
    # v3.4.15：完整准入口径（含 artifact 文件/实质/角色memo/NA 章节）统一由 admit_bundle
    # 判定，与生成器、correction 共用同一函数——消除「生成器 rc0 但 ingest 拒收」的口径分叉。
    errs = admit_bundle(bundle, root, registry, check_artifacts=True)
    if errs:
        raise GateError(
            f"{bundle['skill_id']} 准入拦截 {len(errs)} 处（完整 ingest 口径）：\n"
            + "\n".join(errs))
    skill = find_skill(registry, bundle["skill_id"])

    accepted_status = bundle["status"] in {"PASS", "PASS_WITH_LIMITATIONS"}

    # ===== 阶段一：只读校验（全部通过后才晋级；被拒 attempt 绝不触碰正式文件）=====
    prepared: list[tuple[Path, Path, Path, dict]] = []  # (source, formal, formal_rel, record)
    for record in bundle["artifact_records"]:
        rel = safe_relative(root, record.get("path", ""))
        source = root / rel
        if not source.is_file() or source.is_symlink() or not in_attempts(rel):
            raise GateError(f"artifact 必须来自 {ATTEMPTS_REL.as_posix()} 且为普通文件: {rel}")
        # 注：bytes/sha256/字节下限已由 admit_bundle(_admit_artifact_checks) 统一校验，此处不再重复。
        if accepted_status:
            formal_rel = safe_relative(root, skill["artifact"]["formal_path"])
        else:
            # FAIL / NOT_APPLICABLE：lean 契约已无 negative_acceptance_dir，不晋级正式路径；
            # 证据保留在 attempt 目录，仅登记为未接受。
            formal_rel = rel
        prepared.append((source, root / formal_rel, formal_rel, record))

    # 多角色 skill 的角色备忘录：存在性/字节下限已由 admit_bundle 校验；此处仅登记晋级记录。
    # （admit_bundle 已拒收缺备忘录的 bundle，下方兜底不应触发。）
    verified_role_memos: list[tuple[Path, Path, dict]] = []
    roles = skill.get("roles") or {}
    if accepted_status and roles.get("mode") == "independent_then_integrator":
        attempt_dir = (root / safe_relative(root, bundle["artifact_records"][0].get("path", ""))).parent
        for role in roles.get("required_roles", []):
            if role == "integrator":
                continue
            memo = attempt_dir / f"role-{role}.md"
            if not memo.is_file() or memo.is_symlink() or memo.stat().st_size < 300:
                raise GateError(f"多角色 skill {skill['skill_id']} 缺角色独立备忘录: {role}")
            formal_rel = Path(
                "evidence/roles",
                bundle["skill_id"],
                bundle["attempt_id"],
                memo.name,
            )
            verified_role_memos.append((
                memo,
                root / formal_rel,
                {
                    "role_id": role,
                    "status": "PASS",
                    "artifact_path": formal_rel.as_posix(),
                    "bytes": memo.stat().st_size,
                    "sha256": sha256_file(memo),
                    "verified_by_gate": True,
                },
            ))

    # 注：实质校验/NA 章节校验已由 admit_bundle(check_artifacts=True) 统一完成，此处不再重复。

    # ===== 阶段二：内存准备（所有数据处理成功前不得写正式文件）=====
    records = []
    for source, formal, formal_rel, record in prepared:
        records.append({**record, "path": str(formal_rel), "formal": accepted_status, "accepted": accepted_status})
    verified_role_runs = [record for _, _, record in verified_role_memos]
    next_manifest = copy.deepcopy(manifest)
    entry = next(
        item for item in next_manifest["skills"]
        if item["skill_id"] == bundle["skill_id"]
    )
    entry.update({"status": bundle["status"], "attempts": [*entry.get("attempts", []), bundle["attempt_id"]],
                  "artifact_records": records, "limitations": bundle["limitations"],
                  "not_applicable": bundle.get("not_applicable"),
                  "updated_at": now_iso()})
    next_manifest["artifacts"] = [
        record
        for item in next_manifest["skills"]
        for record in item.get("artifact_records", [])
    ]
    _merge_provenance(
        next_manifest,
        bundle,
        verified_role_runs=verified_role_runs,
        run_root=root,
    )

    # ===== 阶段三：持久化提交（内存准备已完成，只做原子文件替换）=====
    for source, formal, _, record in prepared:
        atomic_copy(
            source,
            formal,
            expected_bytes=record["bytes"],
            expected_sha256=record["sha256"],
        )
    for source, formal, record in verified_role_memos:
        atomic_copy(
            source,
            formal,
            expected_bytes=record["bytes"],
            expected_sha256=record["sha256"],
        )
    save_manifest(root, next_manifest)
    append_event(root, {"type": "result_ingested", "skill_id": bundle["skill_id"], "attempt_id": bundle["attempt_id"], "status": bundle["status"]})
    receipt = {"skill_id": bundle["skill_id"], "status": bundle["status"], "formal_artifacts": records}
    return receipt


def cmd_ingest(args: argparse.Namespace) -> int:
    """CLI 薄壳：与 runtime 共用同一 ingest_result（消除 stdout 文本协议）。"""
    print(json.dumps(ingest_result(Path(args.run_root), Path(args.registry) if args.registry else None, Path(args.result)), ensure_ascii=False))
    return 0


def cmd_register_summary(args: argparse.Namespace) -> int:
    root, registry = Path(args.run_root), load_registry(Path(args.registry))
    manifest = load_manifest(root)
    incomplete = [
        item["skill_id"] for item in manifest["skills"]
        if item.get("status") not in SUCCESS_TERMINAL_STATUSES
    ]
    if incomplete:
        raise GateError(
            f"总结报告只能在全部业务单元终态后登记，未完成={incomplete}")
    missing_artifacts = [
        item["skill_id"] for item in manifest["skills"]
        if not item.get("artifact_records")
    ]
    if missing_artifacts:
        raise GateError(
            f"总结报告登记前每个业务单元都必须有正式或负向验收产物，缺失={missing_artifacts}")

    source = Path(args.summary)
    if not source.is_absolute():
        source = root / source
    try:
        resolved = source.resolve(strict=True)
        rel = resolved.relative_to(root.resolve())
    except (OSError, ValueError) as exc:
        raise GateError(f"总结报告必须位于 run_root 内: {source}: {exc}")
    if not in_summary_attempts(rel):
        raise GateError(f"总结报告必须先写入 {SUMMARY_ATTEMPTS_REL.as_posix()}/")
    if not resolved.is_file() or resolved.is_symlink():
        raise GateError("总结报告必须是普通 Markdown 文件")
    try:
        text = resolved.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise GateError(f"总结报告不可读: {exc}")
    if resolved.stat().st_size < SUMMARY_MIN_BYTES:
        raise GateError(
            f"总结报告字节数 {resolved.stat().st_size} < 下限 {SUMMARY_MIN_BYTES}")
    missing_headings = [
        heading for heading in SUMMARY_REQUIRED_HEADINGS
        if not re.search(rf"^#{{1,6}}\s+{re.escape(heading)}\s*$", text, re.M)
    ]
    if missing_headings:
        raise GateError(f"总结报告缺必需章节: {missing_headings}")
    indexed_paths = [
        record["path"]
        for item in manifest["skills"]
        for record in item.get("artifact_records") or []
    ]
    missing_index = [path for path in indexed_paths if path not in text]
    if missing_index:
        raise GateError(
            f"总结报告的产物索引未覆盖全部正式产物: {missing_index}")

    company = re.sub(r"[/\\\\\\x00-\\x1f]+", "-", manifest["company"]["name"]).strip()
    formal_rel = Path(f"{company}-全量分析-总结报告.md")
    formal = root / formal_rel
    atomic_copy(resolved, formal)
    record = {
        "artifact_id": "artifact.delivery-summary",
        "path": formal_rel.as_posix(),
        "bytes": formal.stat().st_size,
        "sha256": sha256_file(formal),
        "formal": True,
        "accepted": True,
        "registered_at": now_iso(),
    }
    manifest.setdefault("delivery", {})["summary"] = record
    save_manifest(root, manifest)
    append_event(root, {
        "type": "summary_registered",
        "path": record["path"],
        "sha256": record["sha256"],
    })
    # 注：索引刷新不在此处——由 _generate_summary_html 成功后统一触发，
    # 确保索引页链接到已落盘的 HTML 展示件。
    print(json.dumps(record, ensure_ascii=False))
    return 0


def _verify_formal_artifacts(root: Path, manifest: dict) -> list[dict]:
    """复核每个已接受正式产物的实际字节/哈希与 manifest 记录一致。

    返回不一致项列表（含 skill_id/path/reason）；空列表 = 全部一致。
    用于 finalize 兜底，防止"manifest 合格、正式文件被覆盖/污染"的隐蔽状态。
    """
    corrupt: list[dict] = []
    for item in manifest.get("skills", []):
        for record in item.get("artifact_records") or []:
            rel = record.get("path", "")
            fp = root / rel
            if not fp.is_file():
                corrupt.append({"skill_id": item["skill_id"], "path": rel, "reason": "missing"})
                continue
            if fp.stat().st_size != record.get("bytes"):
                corrupt.append({"skill_id": item["skill_id"], "path": rel, "reason": "size_mismatch"})
            elif sha256_file(fp) != record.get("sha256"):
                corrupt.append({"skill_id": item["skill_id"], "path": rel, "reason": "sha256_mismatch"})
    for record in manifest.get("role_runs") or []:
        rel = record.get("artifact_path", "")
        fp = root / rel
        if not record.get("verified_by_gate"):
            corrupt.append({
                "skill_id": record.get("skill_id"),
                "path": rel,
                "reason": "role_not_verified",
            })
        elif not fp.is_file() or fp.is_symlink():
            corrupt.append({
                "skill_id": record.get("skill_id"),
                "path": rel,
                "reason": "role_missing",
            })
        elif fp.stat().st_size != record.get("bytes"):
            corrupt.append({
                "skill_id": record.get("skill_id"),
                "path": rel,
                "reason": "role_size_mismatch",
            })
        elif sha256_file(fp) != record.get("sha256"):
            corrupt.append({
                "skill_id": record.get("skill_id"),
                "path": rel,
                "reason": "role_sha256_mismatch",
            })
    summary = (manifest.get("delivery") or {}).get("summary")
    if summary:
        rel = summary.get("path", "")
        fp = root / rel
        if not fp.is_file() or fp.is_symlink():
            corrupt.append({
                "skill_id": "delivery-summary",
                "path": rel,
                "reason": "summary_missing",
            })
        elif fp.stat().st_size != summary.get("bytes"):
            corrupt.append({
                "skill_id": "delivery-summary",
                "path": rel,
                "reason": "summary_size_mismatch",
            })
        elif sha256_file(fp) != summary.get("sha256"):
            corrupt.append({
                "skill_id": "delivery-summary",
                "path": rel,
                "reason": "summary_sha256_mismatch",
            })
    return corrupt


COST_BUDGET_REVIEW_BYTES_WARN = 500 * 1024  # 单份 compact brief 异常阈值


def _cost_budget_check(run_root: Path, manifest: dict) -> dict:
    """Task 6 成本门槛告警（非阻断）：只显式提示超限项，不静默关闭任何质量校验。

    - missing_usage_summary：run 无 usage_summary（旧流程 run 或 usage 未回传）
    - excessive_attempts：同一 skill 完整 attempt > 1（确定性证据错误应走 correction，
      报告问题走 rework 后仍重复完整提交视为成本超限）
    - oversized_review_brief：任一 review brief 超过 compact 异常阈值
    """
    exceeded: list[dict] = []
    if not manifest.get("usage_summary"):
        exceeded.append({"code": "missing_usage_summary",
                         "detail": "run 无 usage_summary（Task 1 record-usage 未回传或旧流程 run）"})
    for item in manifest.get("skills", []):
        attempts = item.get("attempts") or []
        if len(attempts) > 1:
            exceeded.append({"code": "excessive_attempts",
                             "detail": f"{item['skill_id']} 完整 attempt {len(attempts)} > 1"
                                       f"（确定性证据错误应走 submit-correction，不耗 attempt）"})
    review_dir = Path(run_root) / "evidence/review"
    if review_dir.is_dir():
        for fp in sorted(review_dir.glob("review-brief-*.json")):
            size = fp.stat().st_size
            if size > COST_BUDGET_REVIEW_BYTES_WARN:
                exceeded.append({"code": "oversized_review_brief",
                                 "detail": f"{fp.name} {size}B > {COST_BUDGET_REVIEW_BYTES_WARN}B"
                                           f"（compact 模式异常，检查 payload_mode）"})
    return {
        "verdict": "COST_BUDGET_EXCEEDED" if exceeded else "COST_BUDGET_OK",
        "exceeded": exceeded,
        "note": "成本门槛为可观测性告警，不阻断 APPROVED；质量由 audit/review 独立把关",
    }


def cmd_finalize(args: argparse.Namespace) -> int:
    root, registry = Path(args.run_root), load_registry(Path(args.registry))
    manifest = load_manifest(root)
    # E10 契约版本钉死：run 启动时的契约 digest 必须等于当前契约 digest，
    # 防过期编排 run（旧 HEAD 启动、中途更新契约）被 APPROVED。无 --force 绕过路径。
    pinned = (manifest.get("contract") or {}).get("registry_sha256")
    current_digest = sha256_file(Path(args.registry))
    if not pinned or current_digest != pinned:
        manifest["run"]["status"] = "PARTIAL"
        save_manifest(root, manifest)
        raise GateError(
            "CONTRACT_VERSION_MISMATCH: 当前契约 digest 与 run 启动时不一致，"
            "run 基于过期契约；请用最新版契约重新 start"
            "（旧 run 产物在 run_root 目录，可按 rework 复用规则迁移）")
    # 完整性前置：先复核正式文件与 manifest 记录一致（捕获"manifest 合格、正式文件被覆盖/污染"）
    corrupt = _verify_formal_artifacts(root, manifest)
    if corrupt:
        manifest["run"]["status"] = "PARTIAL"
        save_manifest(root, manifest)
        raise GateError(f"finalize 未准出: 正式文件与 manifest 哈希不一致={corrupt}")
    pending = [
        item["skill_id"] for item in manifest["skills"]
        if item["status"] not in COMPLETED_STATUSES
    ]
    missing = [item["skill_id"] for item in manifest["skills"] if item["status"] in {"PASS", "PASS_WITH_LIMITATIONS"} and not item.get("artifact_records")]
    if pending or missing:
        manifest["run"]["status"] = "PARTIAL"
        save_manifest(root, manifest)
        raise GateError(f"finalize 未准出: PENDING/非终态={pending}; 缺正式产物={missing}")
    failed = [
        item["skill_id"] for item in manifest["skills"]
        if item["status"] == "FAIL"
    ]
    if failed:
        manifest["run"]["status"] = "FAILED"
        save_manifest(root, manifest)
        append_event(root, {
            "type": "run_finalized",
            "status": "FAILED",
            "failed_skills": failed,
        })
        print(json.dumps({
            "run_root": str(root),
            "status": "FAILED",
            "failed_skills": failed,
        }, ensure_ascii=False))
        return 1
    if not (manifest.get("delivery") or {}).get("summary"):
        manifest["run"]["status"] = "PARTIAL"
        save_manifest(root, manifest)
        raise GateError("finalize 未准出: 缺少已登记的最终总结报告")
    audit_path = root / "evidence/audit/audit-result.json"
    if not audit_path.is_file():
        manifest["run"]["status"] = "PARTIAL"
        save_manifest(root, manifest)
        raise GateError("finalize 未准出: 缺少共享 Audit 结果")
    audit = load_json(audit_path, "Audit 结果")
    if audit.get("status") != "PASS":
        manifest["run"]["status"] = "PARTIAL"
        save_manifest(root, manifest)
        raise GateError(f"finalize 未准出: Audit status={audit.get('status')!r}")
    current_snapshot = analysis_snapshot(manifest, Path(args.registry))
    if any(audit.get(key) != current_snapshot[key]
           for key in ("snapshot_schema_version", "registry_sha256", "snapshot_digest")):
        manifest["run"]["status"] = "PARTIAL"
        save_manifest(root, manifest)
        raise GateError("finalize 未准出: Audit 快照与当前 manifest/registry 不一致，须重新 Audit")
    review_info = _run_review_gate(root, Path(args.registry))
    if (review_info.get("status") != "ok"
            or review_info.get("verdict") != "REVIEW_PASSED"):
        manifest["run"]["status"] = "PARTIAL"
        save_manifest(root, manifest)
        raise GateError(
            "finalize 未准出: 语义评审未完整通过 "
            f"status={review_info.get('status')!r} verdict={review_info.get('verdict')!r}")
    manifest["run"]["status"] = "APPROVED"
    save_manifest(root, manifest)
    append_event(root, {"type": "run_finalized", "status": manifest["run"]["status"]})
    # Task 6：成本门槛告警（非阻断，显式输出 COST_BUDGET_EXCEEDED 项）
    cost_budget = _cost_budget_check(root, manifest)
    if cost_budget["exceeded"]:
        append_event(root, {"type": "cost_budget_exceeded",
                            "items": [item["code"] for item in cost_budget["exceeded"]]})
    # 生成 HTML 版总结报告（APPROVED 后自动执行，非阻断——失败不影响准出）
    if manifest["run"]["status"] == "APPROVED":
        _generate_summary_html(root, manifest)
    # 健康体检（advisory，非阻断）：把"过了闸门但仍可能坍塌"的执行退化指纹显性化。
    # 永不影响准出与退出码——任何异常都被捕获并记录 doctor_unavailable，绝不静默。
    doctor_info = _run_doctor_advisory(root, Path(args.registry))
    print(json.dumps({"run_root": str(root), "status": manifest["run"]["status"],
                      "doctor_status": doctor_info["status"],
                      "doctor_verdict": doctor_info["verdict"],
                      "review_status": review_info["status"],
                      "review_verdict": review_info.get("verdict"),
                      "cost_budget": cost_budget}, ensure_ascii=False))
    return 0


def _generate_summary_html(root: Path, manifest: dict) -> bool:
    """用确定性渲染器生成 HTML 版总结报告（非阻断，失败只打印警告）。

    从 manifest.delivery.summary.path 读取已登记的 markdown 总结，调用确定性渲染器
    full_analysis_html.build_summary_page 转为自包含 HTML（内联设计系统与微交互脚本，
    零外部依赖），写入同目录 .html 文件。

    设计系统（cream paper / terracotta / trust 墨蓝 / serif + 报头 + sticky 导航 +
    编号章节 + 样式化表格 + 滚动显现）已固化为代码：同一份 markdown 永远渲染出同一份
    HTML，无 LLM 参与、无 token 消耗、无输出方差，保证每个 run 展示件品质一致。

    调用方：① 步骤 B2 的独立命令 render-html（register-summary 后立即生成，解耦于
    audit/finalize）；② finalize APPROVED 后的幂等兜底。二者共用本函数，因渲染确定性
    保证两次输出逐字节一致、互不冲突。返回 True 表示已写出 HTML。
    """
    try:
        delivery = manifest.get("delivery") or {}
        summary = delivery.get("summary") or {}
        md_rel = summary.get("path", "")
        if not md_rel:
            print("[html-gen] ⚠  manifest 中无 summary.path，跳过 HTML 生成", file=sys.stderr)
            return False
        md_path = root / md_rel
        if not md_path.is_file():
            print(f"[html-gen] ⚠  summary 文件不存在: {md_path}", file=sys.stderr)
            return False
        if not (TOOLS_DIR / "full_analysis_html.py").is_file():
            print("[html-gen] ⚠  渲染器 full_analysis_html.py 不存在，跳过 HTML 生成", file=sys.stderr)
            return False

        company_info = manifest.get("company") or {}
        run_info = manifest.get("run") or {}
        md_text = md_path.read_text(encoding="utf-8")
        html = full_analysis_html.build_summary_page(
            md_text,
            company=company_info.get("name", ""),
            code=company_info.get("code", ""),
            as_of=run_info.get("as_of", ""),
            skill_count=len(manifest.get("skills", [])),
            status=run_info.get("status", ""),
        )
        html_path = md_path.with_suffix(".html")
        atomic_write_text(html_path, html)
        print(f"[html-gen] ✓ {html_path.name} ({len(html.encode())} bytes)", file=sys.stderr)
        append_event(root, {"type": "html_generated", "path": str(html_path.relative_to(root)),
                             "bytes": len(html.encode())})
        # HTML 展示件落盘后立即刷新公司索引页（非阻断）。
        # 覆盖两个调用方：cmd_render_html（步骤 B2）和 cmd_finalize（APPROVED 兜底），
        # 确保"HTML 生成 → 索引自动更新"在所有路径上成立。
        _rebuild_company_index(root)
        return True
    except Exception as exc:  # noqa: BLE001 — HTML 是派生展示件，绝不影响 APPROVED 状态
        print(f"[html-gen] ⚠  HTML 生成失败（不影响 APPROVED 状态）: {exc}", file=sys.stderr)
        return False


def _rebuild_company_index(root: Path) -> bool:
    """重建公司研究索引页 local/Company/index.html（非阻断，失败只打印警告）。

    触发点：_generate_summary_html 成功写出 HTML 展示件后立即调用。
    这覆盖两条路径：① cmd_render_html（步骤 B2 独立命令）；② cmd_finalize
    （APPROVED 后兜底生成）。无论哪条路径产出 HTML，索引都会同步刷新。

    扫描整个公司目录，把新增/更新的公司纳入索引。索引由
    scripts/build_company_index.py 确定性渲染——同一组报告永远产出同一份
    index.html，可安全反复重建。索引是派生展示件，绝不影响 APPROVED 状态。
    """
    try:
        scripts_dir = TOOLS_DIR.parent / "scripts"
        builder_path = scripts_dir / "build_company_index.py"
        if not builder_path.is_file():
            print(f"[index-gen] ⚠  索引生成器不存在: {builder_path}，跳过索引重建", file=sys.stderr)
            return False
        company_base = _company_base_from_run_root(root)

        result = build_company_index.rebuild_index(company_base)
        for warning in result["warnings"]:
            print(f"[index-gen] ⚠  manifest 读取失败: {warning}", file=sys.stderr)
        print(
            f"[index-gen] ✓ index.html 已更新：{result['companies']} 家公司",
            file=sys.stderr,
        )
        append_event(root, {"type": "company_index_rebuilt",
                             "companies": result["companies"],
                             "bytes": result["bytes"]})
        return True
    except Exception as exc:  # noqa: BLE001 — 索引是派生展示件，绝不影响 APPROVED 状态
        print(f"[index-gen] ⚠  索引重建失败（不影响 APPROVED 状态）: {exc}", file=sys.stderr)
        return False


def cmd_render_html(args: argparse.Namespace) -> int:
    """步骤 B2 独立命令：register-summary 后立即用确定性渲染器生成 HTML 展示件。

    解耦关键：不依赖 audit/review/finalize，只要 summary 已登记即可渲染。
    渲染是纯函数（同一 markdown → 同一 HTML），finalize APPROVED 后的兜底生成
    与本命令产出逐字节一致，幂等无冲突。本命令永不阻断（非 Gate 产物），
    失败仅打印警告并返回 0。
    """
    root = Path(args.run_root).resolve()
    if not manifest_path(root).is_file():
        print(f"[html-gen] ⚠  未找到 manifest，请确认 run-root 正确: {root}", file=sys.stderr)
        return 0
    manifest = load_manifest(root)
    _generate_summary_html(root, manifest)
    return 0


def _run_review_gate(root: Path, registry: Path) -> dict:
    """聚合语义评审结果；缺失、不完整、过期或异常均由 finalize 阻断准出。"""
    try:
        import importlib.util

        review_path = TOOLS_DIR / "full_analysis_review.py"
        if not review_path.is_file():
            return {"status": "not_available"}
        spec = importlib.util.spec_from_file_location("full_analysis_review", review_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]

        review_dir = root / "evidence/review"
        if not review_dir.is_dir() or not list(review_dir.glob("review-result-*.json")):
            return {"status": "not_run"}
        index = load_json(
            review_dir / "review-index.json",
            "语义评审索引",
        )
        required_scope = set(
            module.required_review_scope(load_manifest(root)))
        prepared_scope = set(index.get("scope") or [])
        missing_scope = sorted(required_scope - prepared_scope)
        if missing_scope:
            return {
                "status": "incomplete",
                "missing_scope": missing_scope,
            }
        # 聚合已有评审结果
        summary, _ = module.aggregate(root)
        summary_path = root / "evidence/review/semantic-review-summary.json"
        atomic_write_json(summary_path, summary)
        print(f"[review] {summary['overall_verdict']}  "
              f"评审 {summary['skills_reviewed']} 个核心单元  "
              f"findings {summary['total_findings']}  "
              f"(high={summary['severity_counts']['high']} "
              f"medium={summary['severity_counts']['medium']} "
              f"low={summary['severity_counts']['low']})", file=sys.stderr)
        if summary["skills_review_required"]:
            print(f"[review] ⚠️  需定向返工: {summary['skills_review_required']}", file=sys.stderr)
        return {"status": "ok", "verdict": summary["overall_verdict"]}
    except Exception as exc:  # noqa: BLE001
        print(f"❌ 语义评审聚合不可用（finalize 将拒绝准出）: {exc}", file=sys.stderr)
        try:
            append_event(root, {"type": "review_unavailable", "reason": str(exc)})
        except Exception:  # noqa: BLE001
            pass
        return {"status": "unavailable"}


def _run_doctor_advisory(root: Path, registry: Path) -> dict:
    """调用 full_analysis_doctor 做执行完整性体检，写 evidence/doctor-report.json 并打印。

    非阻断：诊断结果仅作参考，不参与 APPROVE/FAIL 判定。
    可见性：doctor 成功返回 {"status":"ok","verdict":...}；
    失败（DoctorError 等）不静默——写 doctor_unavailable 事件 + stderr，返回 {"status":"unavailable"}。
    """
    try:
        import importlib.util

        doctor_path = TOOLS_DIR / "full_analysis_doctor.py"
        spec = importlib.util.spec_from_file_location("full_analysis_doctor", doctor_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        report = module.diagnose(root, registry)
        # 原子写入报告，避免半成品污染 evidence/
        tmp = root / "evidence/doctor-report.json.tmp"
        tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, root / "evidence/doctor-report.json")
        print(module.render(report), file=sys.stderr)
        return {"status": "ok", "verdict": report["verdict"]}
    except Exception as exc:  # noqa: BLE001 — advisory 永不中断 finalize，但必须留痕
        print(f"⚠️  doctor 不可用（不影响准出）: {exc}", file=sys.stderr)
        try:
            append_event(root, {"type": "doctor_unavailable", "reason": str(exc)})
        except Exception:  # noqa: BLE001 — 事件写入失败也不能中断
            pass
        return {"status": "unavailable", "verdict": None}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="全量公司分析 Gate v2")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--registry", default=DEFAULT_REGISTRY)
    init.add_argument("--repo-root", default=Path.cwd())
    init.add_argument("--company", required=True)
    init.add_argument("--code", required=True)
    init.add_argument("--as-of", required=True)
    init.add_argument("--platform", choices=["workbuddy"], required=True)
    init.add_argument("--run-root")
    init.add_argument(
        "--allow-stale", action="store_true", default=False,
        help="人工确认目标版本无误后，覆盖 E1 版本门禁（stale=True/None 均拒绝，仅此开关放行）",
    )
    ingest = sub.add_parser("ingest-result")
    ingest.add_argument("--run-root", required=True)
    ingest.add_argument("--registry", default=DEFAULT_REGISTRY)
    ingest.add_argument("--result", required=True)
    summary = sub.add_parser("register-summary")
    summary.add_argument("--run-root", required=True)
    summary.add_argument("--registry", default=DEFAULT_REGISTRY)
    summary.add_argument("--summary", required=True)
    finalize = sub.add_parser("finalize")
    finalize.add_argument("--run-root", required=True)
    finalize.add_argument("--registry", default=DEFAULT_REGISTRY)
    render_html = sub.add_parser("render-html")
    render_html.add_argument("--run-root", required=True)
    render_html.add_argument("--registry", default=DEFAULT_REGISTRY)
    # 质量归属：每个 skill 产出后的自我校验（优化点 1）。复用 _substance_errors 对该
    # skill 的 lean 契约阈值做确定性校验，并通过才进入 mk_result_bundle / submit-result。
    self_check = sub.add_parser("self-check")
    self_check.add_argument("--run-root", required=True)
    self_check.add_argument("--registry", default=DEFAULT_REGISTRY)
    self_check.add_argument("--skill-id", required=True)
    self_check.add_argument("--report", required=True)
    return parser


# Gate 意图命令 → 处理函数的**唯一** dispatch 表（2026-08-30 候选⑩合一）：
# gate.main 与 scripts/full_analysis.py 共用此表，此前两份 if/elif/dict 各自为政。
GATE_COMMANDS = {
    "init": cmd_init,
    "ingest-result": cmd_ingest,
    "register-summary": cmd_register_summary,
    "finalize": cmd_finalize,
    "render-html": cmd_render_html,
    "self-check": cmd_self_check,
}


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return GATE_COMMANDS[args.command](args)
    except GateError as exc:
        print(f"❌ {exc}")
        return exc.code


if __name__ == "__main__":
    raise SystemExit(main())
