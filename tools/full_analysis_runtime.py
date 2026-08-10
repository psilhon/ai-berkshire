#!/usr/bin/env python3
"""WorkBuddy 专用 Runtime：调度、重试和预算；不写正式业务 manifest（lean：无租约）。"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows compatibility
    fcntl = None
    import msvcrt


TOOLS_DIR = Path(__file__).resolve().parent
from full_analysis_contract import (  # noqa: E402
    CONTRACT_PATH,
    get_skill_or_none,
    load_contract,
)
DEFAULT_REGISTRY = CONTRACT_PATH
STATE_REL = Path("evidence/runtime-state.json")
EVENTS_REL = Path("evidence/events.jsonl")
USAGE_REL = Path("evidence/usage.jsonl")
MANIFEST_REL = Path("evidence/00-analysis-manifest.json")
LEASE_MINUTES = 20  # 扇出单元基础 TTL（按角色数倍增）
NON_FANOUT_LEASE_MINUTES = 40  # 非扇出单元 TTL（宏景 run 实证：mgmt ~35min、ind-research ~25min）
BACKOFF_SECONDS = (60, 180)
RATE_LIMIT_COOLDOWN_SECONDS = 600
PARTIAL_REPORT = "PARTIAL_REPORT.md"
SUMMARY_REPORT = "SUMMARY.md"
LOCK_REL = Path("evidence/locks/runtime-state.lock")
TZ_SHANGHAI = timezone(timedelta(hours=8))

# 反凑数刚性指令：随 methodology_text 一并注入执行 Agent，明确"深度优先于字数"。
# 关键口径：payload 里的 min_bytes 是 Gate 的"拒收地板"（挡住空壳式坍塌），
# 绝非写作目标——严禁 Agent 奔着字节数注水。深度由实质校验保证，字数不设目标。
ANTI_PADDING_DIRECTIVE = """
【质量自觉 · 反凑数】
完整性 = 推理链完整 + 关键判断有数据/来源支撑 + 分歧被显式标记。
严禁为凑篇幅复述标题、堆砌无信息表格、用"综上所述"式废话注水。
一份短而精、解决核心问题的分析，远胜一份长而空、只在重述框架的分析。
写透为止，不设字数上限。每个分析小节须有实质论证（数据、对比、推演），
不得仅列标题或一句话带过；多视角 skill 必须显式呈现不同角色的分歧与交锋。

【关于长度 · 必读】
派发包里的 min_bytes 只是拒收地板——用来挡住空壳式坍塌报告，不是你要凑的写作目标。
严禁奔着字节数写：数据写到位、推理写透即止，宁短勿水。
真实有效的短报告永远优于注水的长报告；详略由问题本身决定，不由字数决定。
"""
AUTHORIZATION_DIRECTIVE = """
【全量运行授权信封 · full-analysis-internal/v1】
本次 run 的启动请求已满足下列内部步骤原本要求的重复确认：
1. 只读外部研究与数据查询；
2. 仅在本次 run_root（{run_root}）内写入 attempt、证据与报告；
3. 输出本次公司研究所需的分析结论。
因此，方法论中只针对上述三类动作的独立 STOP/询问点视为已满足，继续执行，
不要在每个 work unit 重复向用户确认。

授权边界不扩张：不得据此执行 push、PR、publish、send、提交表单或任何外部写入；
不得写出 run_root，不得读取或披露 secret、私人账本或敏感个人数据。
遇到这些未授权动作时仍须停止并交回主上下文处理。
"""

# 章节结构强制指令：防止 Agent 用 section_id 代替 heading、## 后紧跟 ### 导致正文为 0。
STRUCTURE_DIRECTIVE = """
【章节结构 · 强制】
1. 报告中的 ## 标题必须逐字使用派发包 sections 数组中每个条目的 heading 字段值
   （如「数据截止日」「直接来源」「核心结论」），严禁使用 section_id
   （如 data_cutoff、sources_scope）。Gate 按 heading 精确匹配，
   用 section_id 会导致章节匹配失败、整份报告被拒收。
2. 每个 ## 章节下必须先有 ≥150 字的正文段落（数据、推理、结论），
   然后再展开 ### 子节。严禁 ## 标题后紧跟 ### 子标题——
   这会导致该章节被判定为「无正文」而不计入实质章节数，触发 Gate 拒收。
3. 所有 required=true 的章节必须出现；缺失任一必需章节即被拒收。
"""

# 结构化证据强制指令：防止 Agent 只写报告正文、不提交 fact/source/calculation/judgment。
EVIDENCE_DIRECTIVE = """
【结构化证据 · 强制】
除报告正文外，Result Bundle 必须包含以下结构化证据字段（写入 result.json）：
- fact_updates: 关键事实数组，每条含 fact_id / field / value / source_ids
- source_records: 数据来源数组，每条含 source_id / url / retrieved_at / source_type
- calculation_requests: 估值/测算数组，每条含 calculation_id / operation / args
- judgments: 关键判断数组，每条含 judgment_id / rule_id / conclusion / falsification
- command_receipts: 工具调用回执数组，每条含 receipt_id / operation / status
- capability_records: 能力可用性声明数组，每条含 capability / available
上述字段必须存在；lean-v1 下数组可为空（报告才是唯一交付物，空账本合法，
绝不合成 PLACEHOLDER 占位）。calculation_requests 只提交 operation 和 args，
重放结果由 Audit Job 调用 financial_rigor.py 生成，Agent 不得自证计算结果。
"""

# Result Bundle v1 结构参考：注入 payload 供 Agent 理解字段语义与自检。
# v3.4.13：本模板此前写作"将以下 JSON 写入 result.json"，与 canonical skill 的 E16
# （禁止手写 result.json，必须走 mk_result_bundle.py）直接矛盾——Agent 照模板手写正是
# 五粮液 run 四类 schema 返工的根因。现改为「生成器为唯一产出路径，模板仅供理解字段」。
RESULT_BUNDLE_TEMPLATE = """
【Result Bundle v1 · 结构参考（禁止手写，见下方生成命令）】
⚠️ E16 强制：result.json 必须由确定性生成器产出，禁止手写 JSON。
完成分析后执行（真实证据经 --extra-* 传入，机械字段由工具计算）：

python3 scripts/mk_result_bundle.py \\
  --run-root <run_root> --skill-id <skill_id> \\
  --work-unit-id <work_unit_id> --attempt-id <attempt_id> \\
  --report <attempt_dir>/report.md --status PASS \\
  --extra-evidence <facts.json> --extra-sources <sources.json> \\
  [--extra-calculations <calcs.json>] [--extra-judgments <judgments.json>] \\
  [--extra-receipts <receipts.json>] [--extra-capabilities <caps.json>]

生成器退出码 0 才代表 bundle 零占位、可提交；退出码 3 表示证据账本仍是
PLACEHOLDER 结构地板（未做真实调研），Gate 会硬拒收，禁止 submit。
--extra-evidence 与 --extra-sources 必须同时提供（单边会直接失败退出 2）。

下列结构仅供理解字段语义与自检，**不要照抄手写**：
{
  "schema_version": "result-schema/v1",
  "run_id": "<派发包中的 run_id>",
  "work_unit_id": "<派发包中的 work_unit_id>",
  "attempt_id": "<派发包中的 attempt_id>",
  "agent_job_id": "<可选；执行该单元的 agent job id，可空>",
  "lease_nonce": "<可选；历史租约 nonce，可空>",
  "skill_id": "<本 skill 的 skill_id>",
  "role_id": null,
  "status": "PASS",
  "artifact_records": [
    {
      "artifact_id": "artifact.<skill_id>",
      "path": "<attempt_dir 内的报告路径>",
      "bytes": <文件字节数>,
      "sha256": "<文件 SHA-256>",
      "formal": false,
      "accepted": false
    }
  ],
  "fact_updates": [ ... ],
  "source_records": [ ... ],
  "calculation_requests": [ ... ],
  "judgments": [ ... ],
  "command_receipts": [ ... ],
  "capability_records": [],
  "limitations": [],
  "pwl_candidates": [],
  "not_applicable": null,
  "started_at": "<ISO 8601 开始时间>",
  "completed_at": "<ISO 8601 完成时间>",
  "error": null
}
注意：
- status 枚举值：PASS / PASS_WITH_LIMITATIONS / NOT_APPLICABLE / FAIL（不是 SUCCESS）
- artifact_records 是数组（不是单个 artifact 对象）
- path 必须指向 evidence/attempts/<skill_id>/<attempt_id>/ 下的文件
- attempt 产物尚未被 Gate 晋级，formal / accepted 必须保持 false
- NOT_APPLICABLE 时，not_applicable 改为：
  {"predicate": "<contract applicability.predicate>",
   "fact_id": "<证明谓词为假的 fact_id>",
   "alternative": "<contract applicability.alternative 或 null>"}
- command_receipts：PASS 回执**必须由 `scripts/run_evidence_command.py` 真实执行后签发**
  （含 signature/exit_code/output_digest/executed_at/executor_version），不得手写——手写
  argv/output 而无合法签名即被 Gate 拒收；未执行一律 UNAVAILABLE + reason，执行失败一律
  FAIL + reason。`mk_result_bundle.py --extra-receipts` 只负责把执行器产出的回执原样并入
  bundle，不代签。虚构 PASS 回执等同伪造证据，Gate 会按白名单、占位水印与签名校验拒收。
- calculation_requests 只提交 operation 与 args，重放结果由 Audit Job 生成，Agent 不得自证。
- 生成 result.json 后立即调用 submit-result；即使 submit-result 失败，
  磁盘上的 result.json 可被 resume 的孤儿恢复机制接管
"""


class RuntimeErrorState(Exception):
    def __init__(self, message: str = "", *, code: int = 1):
        super().__init__(message)
        self.code = code


@contextmanager
def runtime_lock(run_root: Path):
    """Serialize every runtime-state read-modify-write across processes."""
    path = Path(run_root) / LOCK_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        else:  # pragma: no cover - Windows compatibility
            handle.seek(0)
            if handle.read(1) == b"":
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            else:  # pragma: no cover - Windows compatibility
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def now() -> datetime:
    return datetime.now(TZ_SHANGHAI)


def iso(value: datetime) -> str:
    return value.isoformat()


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def atomic_json(path: Path, value: object) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def load_state(run_root: Path) -> dict:
    path = Path(run_root) / STATE_REL
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeErrorState(f"runtime-state 不可读: {path}: {exc}")
    if state.get("state_version") != "runtime-state/v1":
        raise RuntimeErrorState("runtime-state 版本不匹配")
    return state


def save_state(run_root: Path, state: dict) -> None:
    atomic_json(Path(run_root) / STATE_REL, state)


def event(run_root: Path, kind: str, **payload: object) -> None:
    path = Path(run_root) / EVENTS_REL
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"event_at": iso(now()), "type": kind, **payload}, ensure_ascii=False) + "\n")


def budget_adjust(run_root: Path, *, stop_dispatch_at: int | None = None,
                  hard_max: int | None = None, reason: str = "") -> dict:
    """调高派发预算（budget 触顶 CHECKPOINT 的「调高预算继续」分支）。

    只允许上调（防静默降标），且强制 stop_dispatch_at < hard_max（防倒置配置）；
    调整成功后清除 PARTIAL_REPORT.md/SUMMARY.md（恢复派发后不得残留 PARTIAL 状态声明），
    调整结果写入 events.jsonl 可追溯。
    """
    with runtime_lock(run_root):
        state = load_state(run_root)
        budget = state["budget"]
        changes: dict[str, tuple[int, int]] = {}
        # 先求生效后的值，统一做交叉校验（防 stop=133/hard=33 倒置）
        new_stop = stop_dispatch_at if stop_dispatch_at is not None else budget.get("stop_dispatch_at", 0)
        new_hard = hard_max if hard_max is not None else budget.get("hard_max", 0)
        if new_stop >= new_hard:
            raise RuntimeErrorState(
                f"预算配置倒置：stop_dispatch_at({new_stop}) 必须 < hard_max({new_hard})")
        if stop_dispatch_at is not None:
            if stop_dispatch_at <= budget.get("stop_dispatch_at", 0):
                raise RuntimeErrorState(
                    f"stop_dispatch_at 只能上调（当前 {budget.get('stop_dispatch_at')}，收到 {stop_dispatch_at}）")
            old = budget.get("stop_dispatch_at")
            budget["stop_dispatch_at"] = stop_dispatch_at
            changes["stop_dispatch_at"] = (old, stop_dispatch_at)
        if hard_max is not None:
            if hard_max <= budget.get("hard_max", 0):
                raise RuntimeErrorState(
                    f"hard_max 只能上调（当前 {budget.get('hard_max')}，收到 {hard_max}）")
            old = budget.get("hard_max")
            budget["hard_max"] = hard_max
            changes["hard_max"] = (old, hard_max)
        if not changes:
            raise RuntimeErrorState("budget-adjust 至少提供 stop_dispatch_at 或 hard_max 之一")
        # 清除 PARTIAL 残留：预算恢复派发后，PARTIAL_REPORT.md/SUMMARY.md 不得继续宣称 PARTIAL
        cleared = []
        for stale in (PARTIAL_REPORT, SUMMARY_REPORT):
            p = Path(run_root) / stale
            if p.exists():
                p.unlink()
                cleared.append(stale)
        save_state(run_root, state)
        event(run_root, "budget_adjusted",
              **{k: {"from": v[0], "to": v[1]} for k, v in changes.items()},
              reason=reason or "budget 触顶人工调高",
              cleared_partial=cleared)
        return {"status": "OK", "adjusted": {k: {"from": v[0], "to": v[1]} for k, v in changes.items()},
                "cleared_partial": cleared}


def log_event(run_root: Path, *, kind: str, note: str = "") -> dict:
    """受支持的人工事件写入入口（doctor CHECKPOINT 复核结论等）。

    仅允许写入受信任的事件类型（拒绝任意注入），note 为复核结论文本。
    """
    allowed = {"human_review", "manual_rework", "doctor_checkpoint"}
    if kind not in allowed:
        raise RuntimeErrorState(
            f"event-log 类型 {kind!r} 不在白名单 {sorted(allowed)} 内，请用受支持类型")
    if not note or not note.strip():
        raise RuntimeErrorState(f"event-log --note 必填且非空（{kind} 的结论/说明不可留空）")
    with runtime_lock(run_root):
        event(run_root, kind, note=note.strip(), source="orchestrator")
        return {"status": "OK", "kind": kind, "note": note.strip()}


def initialize(run_root: Path) -> dict:
    with runtime_lock(run_root):
        return _initialize_locked(run_root)


def _initialize_locked(run_root: Path) -> dict:
    state = load_state(run_root)
    if state["budget"].get("preflight_count", 0) == 0:
        state["budget"]["preflight_count"] = 1
        state["budget"]["used"] += 1
        event(run_root, "preflight_completed", budget_used=state["budget"]["used"])
        save_state(run_root, state)
    return state


def _active_units(state: dict) -> list[dict]:
    return [unit for unit in state["work_units"] if unit.get("status") in {"LEASED", "RUNNING"}]


def _load_registry() -> dict:
    """容错档：委托 Contract 深模块（strict=False），读取失败降级为空表。"""
    return load_contract(strict=False)


# v3.3.10 依赖波次调度：contract depends_on → 拓扑分层 → 波次并行派发。
# 这三个纯函数是依赖图的单一所有者，gate.cmd_init（持久化）与
# check-full-analysis-contract.py（校验环）均复用，避免多处各自解析漂移。
PIPELINE_ROOT = "ashare-data"


def build_dependency_graph(skills: list) -> dict:
    """从 contract skills 构建 {skill_id: [dep_skill_id,...]}。

    缺省 depends_on 视为仅依赖 ashare-data（向后兼容旧契约）；ashare-data 自身为根，
    无论契约是否声明，其依赖强制为空（防止自环/依赖下游）。
    依赖中引用未知 skill_id 抛 ValueError（init 前即暴露契约错误）。
    """
    ids = {s["skill_id"] for s in skills}
    graph: dict[str, list] = {}
    for skill in skills:
        sid = skill["skill_id"]
        if sid == PIPELINE_ROOT:
            graph[sid] = []
            continue
        deps = skill.get("depends_on")
        if deps is None:
            deps = [PIPELINE_ROOT]
        deps = [d for d in deps if d != sid]
        unknown = [d for d in deps if d not in ids]
        if unknown:
            raise ValueError(f"{sid} depends_on 引用未知 skill: {unknown}")
        graph[sid] = list(deps)
    return graph


def detect_dependency_cycle(graph: dict) -> list | None:
    """DFS 检测依赖环，返回构成环的节点列表（无环返回 None）。"""
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {node: WHITE for node in graph}
    parent: dict = {}

    def dfs(node: str) -> list | None:
        color[node] = GRAY
        for dep in graph.get(node, []):
            if dep not in color:
                continue
            if color[dep] == GRAY:
                # 回溯环路径
                cycle = [dep, node]
                cur = node
                while cur in parent and parent[cur] != dep:
                    cur = parent[cur]
                    cycle.append(cur)
                return cycle
            if color[dep] == WHITE:
                parent[dep] = node
                found = dfs(dep)
                if found:
                    return found
        color[node] = BLACK
        return None

    for node in graph:
        if color[node] == WHITE:
            found = dfs(node)
            if found:
                return found
    return None


def compute_dependency_waves(graph: dict) -> list[list[str]]:
    """按拓扑层级把 skill 分层为波次：layer 0 = 无依赖，layer N = 依赖全在 <N 层。

    返回 [[wave0 skills], [wave1 skills], ...]，每波内的单元可并行派发。
    要求传入图已无环（调用方先 detect_dependency_cycle）。
    """
    layer_of: dict[str, int] = {}

    def layer(node: str) -> int:
        if node in layer_of:
            return layer_of[node]
        deps = graph.get(node, [])
        if not deps:
            layer_of[node] = 0
            return 0
        depth = max(layer(dep) for dep in deps) + 1
        layer_of[node] = depth
        return depth

    for node in graph:
        layer(node)

    waves: dict[int, list[str]] = {}
    for node, depth in layer_of.items():
        waves.setdefault(depth, []).append(node)
    # 每波内按契约 registry 顺序稳定排序，保证派发顺序可复现
    return [sorted(waves[d]) for d in sorted(waves)]

def next_work(run_root: Path, *, methodology_mode: str = "full") -> dict:
    """领取下一个可派发 work unit（lean：无租约、无波次白名单）。

    依赖由 depends_on 拓扑门禁决定就绪性；并发上限内连续租出多个就绪单元。
    """
    with runtime_lock(run_root):
        return _next_work_locked(run_root, methodology_mode)


def _next_work_locked(run_root: Path, methodology_mode: str = "full") -> dict:
    state = load_state(run_root)
    budget = state["budget"]
    if budget["used"] >= budget["hard_max"]:
        render_partial(run_root, "JOB_LIMIT")
        raise RuntimeErrorState(f"硬预算已达 {budget['hard_max']}，停止新派发")
    cooldown = parse_time(state["concurrency"].get("cooldown_until"))
    if cooldown and cooldown > now():
        return {"status": "NO_WORK", "reason": "RATE_LIMIT_COOLDOWN", "cooldown_until": iso(cooldown)}
    # lean（v3.7+）：不再自动回收过期租约（watchdog 跨回合失效且无收效），
    # 失败/卡死由编排器显式 mark_failed 判定并声明。
    active = _active_units(state)
    if len(active) >= state["concurrency"]["max"]:
        return {"status": "NO_WORK", "reason": "CONCURRENCY_LIMIT"}
    # 依赖门禁：只有 depends_on 全 DONE 的单元才就绪可派发；FAILED 依赖不放行。
    done_skills = {u["skill_id"] for u in state["work_units"] if u["status"] == "DONE"}
    candidates = []
    current = now()
    for unit in state["work_units"]:
        if unit["status"] not in {"PENDING", "RETRY_WAIT"}:
            continue
        retry_at = parse_time(unit.get("next_retry_at"))
        if retry_at and retry_at > current:
            continue
        if budget["used"] >= budget["stop_dispatch_at"] and not unit.get("core", True):
            continue
        deps = unit.get("depends_on")
        if deps is None:
            deps = [] if unit["skill_id"] == PIPELINE_ROOT else [PIPELINE_ROOT]
        if any(dep not in done_skills for dep in deps):
            continue
        candidates.append(unit)
    if not candidates:
        waiting = any(u["status"] in {"PENDING", "RETRY_WAIT"} for u in state["work_units"])
        reason = "DEPENDENCIES_PENDING" if waiting else "QUEUE_EMPTY"
        return {"status": "NO_WORK", "reason": reason}
    unit = candidates[0]
    # 注入 skill 方法论，避免执行 Agent 退化为单遍写大纲（根因修复）。
    registry = _load_registry()
    skill = get_skill_or_none(registry, unit["skill_id"])
    attempt = unit["attempts"] + 1
    attempt_id = f"attempt-{unit['work_unit_id']}-{attempt}"
    unit.update({"status": "LEASED", "attempts": attempt})
    save_state(run_root, state)
    event(run_root, "work_leased", work_unit_id=unit["work_unit_id"], attempt_id=attempt_id)
    authorization = (
        state.get("authorization")
        or registry.get("authorization_profile")
        or {}
    )
    methodology_text = ""
    methodology_ref = None
    methodology_sha256 = None
    if skill:
        spec = skill.get("spec_source")
        if spec:
            spec_path = TOOLS_DIR.parent / spec
            if spec_path.is_file():
                spec_text = spec_path.read_text(encoding="utf-8")
                if methodology_mode == "ref":
                    methodology_ref = spec
                    methodology_sha256 = hashlib.sha256(spec_text.encode("utf-8")).hexdigest()
                    methodology_text = AUTHORIZATION_DIRECTIVE.format(run_root=Path(run_root))
                else:
                    methodology_text = (
                        AUTHORIZATION_DIRECTIVE.format(run_root=Path(run_root))
                        + spec_text
                        + ANTI_PADDING_DIRECTIVE
                        + STRUCTURE_DIRECTIVE
                        + EVIDENCE_DIRECTIVE
                        + RESULT_BUNDLE_TEMPLATE
                    )
    roles = skill.get("roles", {}) if skill else {}
    return {
        "status": "LEASED",
        "work_unit_id": unit["work_unit_id"],
        "skill_id": unit["skill_id"],
        "attempt_id": attempt_id,
        "methodology_path": skill.get("spec_source") if skill else None,
        "methodology_text": methodology_text,
        "methodology_ref": methodology_ref,
        "methodology_sha256": methodology_sha256,
        "methodology_mode": methodology_mode,
        # 校准路线（防凑数）：不把 min_bytes 具体数字暴露给执行 Agent——它是 Gate 的拒收
        # 地板（挡空壳式坍塌），不是写作目标；奔字数写是凑数的根源。深度由 _substance_errors 实质校验保证。
        "min_bytes": None,
        "length_policy": (
            "min_bytes 仅是拒收地板，不是写作目标；数据写到位、推理写透即止，"
            "宁短勿水。深度由实质校验保证，详略由问题本身决定。"
        ),
        "skill_type": skill.get("skill_type") if skill else None,
        "min_dissent_points": skill.get("min_dissent_points") if skill else None,
        "min_substantive_sections": skill.get("min_substantive_sections") if skill else None,
        "roles": roles,
        "fanout_required": bool(roles.get("mode") == "independent_then_integrator"),
        # rework 重置的单元带可复用基准 attempt，编排器按此复用 report/role-memo/raw
        "reuse_base_attempt": unit.get("reuse_attempt"),
        "authorization": authorization,
    }


def _unit(state: dict, work_unit_id: str) -> dict:
    for unit in state["work_units"]:
        if unit["work_unit_id"] == work_unit_id:
            return unit
    raise RuntimeErrorState(f"未知 work_unit_id: {work_unit_id}")


def _validate_result_lease(state: dict, bundle: dict, *, allow_expired: bool = False) -> dict:
    """在 Gate 产生任何副作用前，将 Result Bundle 绑定到当前活动单元。

    lean（v3.7+）：已无租约身份机（无 nonce / 无 expiry / 无 agent_job_id）。
    仅校验 Result Bundle 与当前单元的强身份一致：run_id / work_unit_id /
    skill_id / attempt_id（attempt_id 由 `attempt-{work_unit_id}-{attempts}` 派生）。
    allow_expired 保留为兼容参数（无过期概念，恒忽略）。
    """
    if bundle.get("run_id") != state.get("run_id"):
        raise RuntimeErrorState("Result Bundle run_id 与 Runtime 不匹配")
    work_unit_id = bundle.get("work_unit_id")
    if not isinstance(work_unit_id, str):
        raise RuntimeErrorState("Result Bundle work_unit_id 缺失")
    unit = _unit(state, work_unit_id)
    if unit.get("skill_id") != bundle.get("skill_id"):
        raise RuntimeErrorState("Result Bundle skill_id 与 work unit 不匹配")
    if unit.get("status") not in {"RUNNING", "LEASED"}:
        raise RuntimeErrorState(f"submit-result 状态非法: {unit.get('status')}")
    expected_attempt = f"attempt-{work_unit_id}-{unit['attempts']}"
    if bundle.get("attempt_id") != expected_attempt:
        raise RuntimeErrorState(
            f"Result Bundle attempt_id 与当前单元不匹配: "
            f"{bundle.get('attempt_id')} != {expected_attempt}")
    return unit


def _accept_result(
    run_root: Path,
    registry: Path,
    result: Path,
    *,
    state: dict | None = None,
    allow_expired: bool = False,
    event_kind: str = "result_submitted",
) -> dict:
    bundle = json.loads(result.read_text(encoding="utf-8"))
    current_state = state if state is not None else load_state(run_root)
    unit = _validate_result_lease(current_state, bundle, allow_expired=allow_expired)
    gate = Path(__file__).resolve().parent / "full_analysis_gate.py"
    completed = subprocess.run([sys.executable, str(gate), "ingest-result", "--run-root", str(run_root), "--registry", str(registry), "--result", str(result)], capture_output=True, text=True)
    if completed.returncode:
        raise RuntimeErrorState(completed.stdout + completed.stderr)
    unit["status"] = "DONE" if bundle["status"] in {"PASS", "PASS_WITH_LIMITATIONS", "NOT_APPLICABLE"} else "FAILED"
    current_state["concurrency"]["current"] = max(
        0, current_state["concurrency"].get("current", 0) - 1)
    save_state(run_root, current_state)
    event(run_root, event_kind, work_unit_id=unit["work_unit_id"],
          attempt_id=bundle["attempt_id"], status=unit["status"])
    return {"status": unit["status"], "gate": completed.stdout.strip()}


def submit_result(run_root: Path, registry: Path, result: Path) -> dict:
    with runtime_lock(run_root):
        return _accept_result(run_root, registry, result)


def render_partial(run_root: Path, reason: str) -> None:
    root = Path(run_root)
    state = load_state(root)
    pending = [u["skill_id"] for u in state["work_units"] if u["status"] not in {"DONE", "FAILED"}]
    (root / PARTIAL_REPORT).write_text(
        "# PARTIAL_REPORT\n\n未准出；本次运行不产生投资结论。\n\n"
        f"停止原因：`{reason}`\n\n未完成工作单元：{', '.join(pending) or '无'}\n",
        encoding="utf-8",
    )
    (root / SUMMARY_REPORT).write_text(
        "# SUMMARY\n\n状态：PARTIAL（未准出）\n\n"
        f"停止原因：`{reason}`\n\n已使用 Agent job：{state['budget']['used']}\n",
        encoding="utf-8",
    )
    event(root, "partial_rendered", reason=reason)

# ---------------------------------------------------------------- 失败声明（lean 核心）
def mark_failed(run_root: Path, skill_id: str, reason: str, *, retry: bool = False) -> dict:
    """声明一个单元失败（lean 模式核心：允许失败，必须显式声明）。

    - retry=False：将该单元置 FAILED 并记录 reason；依赖它的下游保持阻塞，
      由编排器在终稿中声明缺口。
    - retry=True：重新置 PENDING（attempts+1），供编排器重新派发。
    不自动回收租约——失败由编排器显式判定并声明，避免静默卡死无人发现。
    """
    with runtime_lock(run_root):
        state = load_state(run_root)
        unit = next((u for u in state["work_units"] if u["skill_id"] == skill_id), None)
        if unit is None:
            raise RuntimeErrorState(f"未知 skill_id: {skill_id}")
        if retry:
            unit["status"] = "PENDING"
            unit["attempts"] = unit.get("attempts", 0) + 1
            event(run_root, "unit_retried", skill_id=skill_id, attempts=unit["attempts"])
            save_state(run_root, state)
            return {"status": "RETRIED", "skill_id": skill_id}
        unit["status"] = "FAILED"
        unit["failure"] = {"reason": reason, "declared_at": iso(now())}
        event(run_root, "unit_failed", skill_id=skill_id, reason=reason)
        save_state(run_root, state)
        return {"status": "FAILED", "skill_id": skill_id, "reason": reason}


# ---------------------------------------------------------------- usage 计量
USAGE_PHASES = {"work", "summary", "review"}


def _usage_records(run_root: Path) -> list[dict]:
    path = Path(run_root) / USAGE_REL
    if not path.exists():
        return []
    records = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _refresh_usage_summary(run_root: Path) -> dict:
    """从 usage.jsonl 全量重算 manifest.usage_summary（幂等，单一真源=jsonl）。"""
    records = _usage_records(run_root)
    by_phase: dict[str, dict] = {}
    by_skill: dict[str, dict] = {}
    total_tokens = 0
    for rec in records:
        phase = rec.get("phase", "?")
        skill = rec.get("skill_id", "?")
        in_tok = rec.get("input_tokens") or 0
        out_tok = rec.get("output_tokens") or 0
        total_tokens += in_tok + out_tok
        for bucket, key in ((by_phase, phase), (by_skill, skill)):
            agg = bucket.setdefault(key, {"records": 0, "input_tokens": 0,
                                          "output_tokens": 0, "input_bytes": 0,
                                          "output_bytes": 0, "duration_ms": 0,
                                          "cache_hits": 0})
            agg["records"] += 1
            agg["input_tokens"] += in_tok
            agg["output_tokens"] += out_tok
            agg["input_bytes"] += rec.get("input_bytes") or 0
            agg["output_bytes"] += rec.get("output_bytes") or 0
            agg["duration_ms"] += rec.get("duration_ms") or 0
            if rec.get("cache_hit"):
                agg["cache_hits"] += 1
    summary = {
        "schema_version": "usage-summary/v1",
        "total_records": len(records),
        "total_tokens": total_tokens,
        "by_phase": [{"phase": k, **v} for k, v in sorted(by_phase.items())],
        "by_skill": [{"skill_id": k, **v} for k, v in sorted(by_skill.items())],
    }
    manifest_path = Path(run_root) / MANIFEST_REL
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["usage_summary"] = summary
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=1) + "\n",
            encoding="utf-8",
        )
    return summary


def record_usage(
    run_root: Path,
    *,
    phase: str,
    attempt_id: str,
    skill_id: str,
    input_tokens: int | None,
    output_tokens: int | None,
    input_bytes: int,
    output_bytes: int,
    duration_ms: int,
    cache_hit: bool = False,
) -> dict:
    """记录一次 Agent 阶段的真实 usage，写入 evidence/usage.jsonl 并刷新 manifest 汇总。"""
    if phase not in USAGE_PHASES:
        raise RuntimeErrorState(f"phase 必须是 {sorted(USAGE_PHASES)} 之一，收到 {phase!r}", code=1)
    for label, value in (("input_tokens", input_tokens), ("output_tokens", output_tokens),
                         ("input_bytes", input_bytes), ("output_bytes", output_bytes),
                         ("duration_ms", duration_ms)):
        if value is not None and value < 0:
            raise RuntimeErrorState(f"{label} 不能为负数（收到 {value}）", code=1)
    if input_bytes is None or output_bytes is None:
        raise RuntimeErrorState("input_bytes/output_bytes 必填（attempt 绑定必须存在）", code=1)

    state = load_state(run_root)
    run_id = state.get("run_id") or state.get("run", {}).get("run_id", "")
    receipt = {
        "schema_version": "usage-receipt/v1",
        "run_id": run_id,
        "phase": phase,
        "skill_id": skill_id,
        "attempt_id": attempt_id,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "input_bytes": input_bytes,
        "output_bytes": output_bytes,
        "duration_ms": duration_ms,
        "cache_hit": cache_hit,
        "recorded_at": iso(now()),
    }
    path = Path(run_root) / USAGE_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    for existing in _usage_records(run_root):
        if existing.get("attempt_id") == attempt_id and existing.get("phase") == phase:
            raise RuntimeErrorState(
                f"重复 usage 记录：attempt_id={attempt_id} phase={phase} 已存在",
                code=1,
            )
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(receipt, ensure_ascii=False) + "\n")
    event(run_root, "usage_recorded", phase=phase, skill_id=skill_id, attempt_id=attempt_id)
    summary = _refresh_usage_summary(run_root)
    return {"status": "RECORDED", "receipt": receipt, "usage_summary": summary}


def rework(run_root: Path, work_unit_id: str, reason: str = "") -> dict:
    """报告正文/artifact 类返工：DONE/PARTIAL → PENDING + 重置复用指引 + 事件。

    与 submit-correction 分工：确定性证据错误走 correction（不耗 attempt）；
    缺章节/缺正文/实质校验失败才走本命令（耗一次 attempt）。
    防呆：未派发/PENDING/LEASED 单元与无被 Gate 接受产物的单元拒绝。
    """
    with runtime_lock(run_root):
        return _rework_locked(run_root, work_unit_id, reason)


def _rework_locked(run_root: Path, work_unit_id: str, reason: str) -> dict:
    state = load_state(run_root)
    unit = next((u for u in state["work_units"] if u["work_unit_id"] == work_unit_id), None)
    if unit is None:
        raise RuntimeErrorState(f"未知 work_unit_id: {work_unit_id}", code=1)
    if unit["status"] not in {"DONE", "PARTIAL"}:
        raise RuntimeErrorState(
            f"rework 只接受 DONE/PARTIAL 单元（{work_unit_id} 当前 {unit['status']}）；"
            f"PENDING/LEASED/未派发单元请走正常 next-work",
            code=1,
        )
    # 防呆：必须已有被 Gate 接受的 attempt（manifest.skills[].attempts 非空）
    manifest_path = Path(run_root) / MANIFEST_REL
    accepted: list[str] = []
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            entry = next(
                (item for item in manifest.get("skills", []) if item["skill_id"] == unit["skill_id"]),
                None,
            )
            accepted = list((entry or {}).get("attempts") or [])
        except (OSError, json.JSONDecodeError):
            accepted = []
    if not accepted:
        raise RuntimeErrorState(
            f"{work_unit_id} 无被 Gate 接受的 attempt，不能 rework（先提交合格产物）",
            code=1,
        )
    base = accepted[-1]
    unit["reuse_attempt"] = base  # next-work 据此下发 reuse_base_attempt 指引
    unit["status"] = "PENDING"
    state.setdefault("rework_count", 0)
    state["rework_count"] += 1
    save_state(run_root, state)
    event(
        run_root, "rework_initiated",
        work_unit_id=work_unit_id, reason=reason,
        base_attempt=base,
    )
    return {
        "status": "REWORKED",
        "work_unit_id": work_unit_id,
        "base_attempt": base,
        "reuse_base_attempt": base,
    }
