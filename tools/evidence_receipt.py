"""执行器回执（executor-issued receipt）v1 —— 签发、留痕与校验。

# 这个模块解决什么问题

v3.4.14 之前（以及 v3.4.14 的「执行绑定」），一条 PASS 回执要通过 Gate，只需
Agent 在 result.json 里自填非空的 `argv` 与 `output` 两个字符串。两者都是同一份
JSON 里的自述文本，Gate 无从分辨它们是真实执行的产物还是编造的。实测后果：
**运行一条完全无关的命令、附上任意输出字符串，即可让生成器返回 rc0 并通过 Gate
进入 DONE**。换句话说，v3.4.14 的「执行绑定」名实不符——它绑定的是「有没有填这
两个字段」，而不是「命令有没有真的跑过」。

本模块把这件事改成：PASS 回执**只能由执行器签发**。
`scripts/run_evidence_command.py` 真实 subprocess 执行命令，捕获退出码与输出，
落盘输出文件、计算 sha256、追加 journal 留痕，最后用本 run 的密钥对回执字段做
HMAC-SHA256 签名。Agent 拿到的是一个已签名的完整回执，不能改其中任何一个字节
（改了签名即失效），也无法凭空造出一个（缺 journal 条目与落盘输出文件）。

# 诚实的威胁模型声明（勿在文档里拔高）

**本模块的签名不是对抗恶意 Agent 的密码学不可否认性。**
签名密钥 `receipt_signing_secret` 存放在 run_root 的 runtime-state 内，任何有权
写 result.json 的进程同样有权读取它，因而理论上可以自行计算出合法签名。

它真正提供的保证是：**把「自报成功」从「一次 JSON 字段自填」提升为「必须真实调
用执行器」**。伪造一条回执不再是写两个字符串，而需要刻意地读取密钥、复刻规范化
序列化、伪造 journal 条目、并伪造一个与 digest 一致的输出文件——这已不属于「模
型顺手编造」的失效模式，而是蓄意造假。本项目的对手是幻觉与偷懒，不是攻击者。

# PASS 回执的六项校验（全部为确定性判定，误报为零）

  ① signature == HMAC-SHA256(secret, canonical(受签字段))  字段被整体绑定
  ② exit_code == 0                                        命令确实成功退出
  ③ output_digest == sha256(落盘输出文件)                  输出可复核、不可事后改写
  ④ executed_at ∈ [run_started_at - skew, now + skew]      执行发生在本 run 窗口内
  ⑤ operation ∈ argv                                       命令与所声称的操作一致
  ⑥ journal(command-log.jsonl) 内有同 receipt_id 同 digest 条目   执行器留痕

UNAVAILABLE / FAIL 回执不需要签名（它们本就不宣称成功），只需 reason。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

EXECUTOR_VERSION = "evidence-executor/v1"

# 受签字段：任何一个被改动，签名即失效。顺序无关（canonical 用 sort_keys）。
SIGNED_FIELDS = (
    "receipt_id", "operation", "status", "argv", "output",
    "executed_at", "exit_code", "output_digest", "executor_version", "run_id",
)

JOURNAL_REL = Path("evidence/command-log.jsonl")
OUTPUT_DIR_REL = Path("evidence/command-output")
RUNTIME_STATE_REL = Path("evidence/runtime-state.json")
# 密钥独立成文件而非塞进 runtime-state：runtime-state 是租约/预算的高频读改写热点
# （有跨进程锁），执行器若参与其读改写会引入不必要的锁耦合与并发覆盖风险。
SECRET_REL = Path("evidence/receipt-signing-key")

# 时钟偏移容忍：容器/宿主时钟不完全同步时不误杀真实执行。
CLOCK_SKEW = timedelta(minutes=10)

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class ReceiptError(RuntimeError):
    """执行器回执签发/校验过程中的确定性错误。"""


# --------------------------------------------------------------------------
# 签名
# --------------------------------------------------------------------------

def canonical_payload(receipt: dict, run_id: str) -> bytes:
    """受签字段的规范化字节串。

    只取 SIGNED_FIELDS，`run_id` 由调用方传入（不信任 receipt 自带值），
    sort_keys 保证与字段书写顺序无关，separators 去掉空白歧义。
    """
    payload = {k: receipt.get(k) for k in SIGNED_FIELDS if k != "run_id"}
    payload["run_id"] = run_id
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def sign_payload(secret: str, receipt: dict, run_id: str) -> str:
    """对回执受签字段做 HMAC-SHA256，返回十六进制签名。"""
    return hmac.new(secret.encode("utf-8"),
                    canonical_payload(receipt, run_id),
                    hashlib.sha256).hexdigest()


def load_signing_secret(run_root: Path) -> str | None:
    """读取本 run 的签名密钥；缺失返回 None。

    缺失即表示该 run_root 未经 v3.4.15 的 `start` 初始化（历史 run 或单测裸目录），
    此时 Gate 会降级到 v1 绑定校验并在消息中说明——见 gate 的 `_receipt_binding_mode`。
    """
    try:
        secret = (run_root / SECRET_REL).read_text(encoding="utf-8").strip()
    except Exception:
        return None
    return secret or None


def ensure_signing_secret(run_root: Path) -> str:
    """返回本 run 的签名密钥；不存在则生成并落盘（0600）。

    执行器调用此函数，使 v3.4.15 之前初始化的在途 run 也能开始签发合规回执，
    不必重跑 start。生成是幂等的：已存在则原样返回。
    """
    existing = load_signing_secret(run_root)
    if existing:
        return existing
    path = run_root / SECRET_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    secret = secrets.token_hex(32)
    path.write_text(secret, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return secret


def load_runtime_state(run_root: Path) -> dict:
    """只读取 runtime-state（不加锁、不写回）；不可读时返回空 dict。"""
    try:
        state = json.loads((run_root / RUNTIME_STATE_REL).read_text(encoding="utf-8"))
    except Exception:
        return {}
    return state if isinstance(state, dict) else {}


def load_run_started_at(run_root: Path) -> datetime | None:
    """从 runtime-state 读取 run 起始时间，用于 ④ 时间窗校验。"""
    return parse_iso(load_runtime_state(run_root).get("run_started_at"))


def load_run_id(run_root: Path) -> str | None:
    """从 runtime-state 读取 run_id —— 签名以此为准，而非 bundle 自述的 run_id。

    受签 run_id 取自 runtime-state 而不是 result.json，是为了让「跨 run 复用回执」
    这条路径直接在签名层失败：把别的 run 的回执搬过来，其签名绑定的是原 run_id。
    """
    rid = load_runtime_state(run_root).get("run_id")
    return rid if isinstance(rid, str) and rid else None


def parse_iso(value) -> datetime | None:
    """宽松解析 ISO 8601；失败返回 None。统一归一到 UTC 以便比较。"""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# --------------------------------------------------------------------------
# journal（执行器留痕）
# --------------------------------------------------------------------------

def append_journal(run_root: Path, entry: dict) -> None:
    """向 command-log.jsonl 追加一条执行留痕（append-only，永不重写）。"""
    path = run_root / JOURNAL_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def load_journal(run_root: Path) -> dict:
    """读取 journal，返回 {receipt_id: 最后一条 entry}。

    同一 receipt_id 多次执行（重跑）取最后一条——与 result.json 里 Agent 提交的
    那一条对应；若 Agent 提交的是更早的一次，digest 比对会失败，属预期拦截。
    """
    path = run_root / JOURNAL_REL
    out: dict = {}
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except Exception:
            continue
        rid = entry.get("receipt_id")
        if isinstance(rid, str) and rid:
            out[rid] = entry
    return out


# --------------------------------------------------------------------------
# 校验
# --------------------------------------------------------------------------

def verify_executor_receipt(receipt: dict, *, run_root: Path, run_id: str,
                            secret: str, run_started_at: datetime | None = None,
                            journal: dict | None = None,
                            now: datetime | None = None) -> list[str]:
    """校验一条 PASS 回执是否确由执行器签发。返回错误消息列表（空=通过）。

    调用前提：receipt["status"] == "PASS"。非 PASS 回执不走这里。
    """
    rid = receipt.get("receipt_id")
    op = receipt.get("operation")
    errs: list[str] = []

    sig = receipt.get("signature")
    if not (isinstance(sig, str) and _HEX64.match(sig.strip().lower() or "")):
        return [
            f"  - [回执未经执行器签发] {rid} 状态 PASS 但缺合法 signature；"
            f"PASS 回执必须由 scripts/run_evidence_command.py 真实执行后签发，"
            f"不得手写。请用执行器重跑该操作："
            f"python3 scripts/run_evidence_command.py --run-root <run_root> "
            f"--receipt-id {rid} --operation {op} -- <真实命令>；"
            f"若该操作确实不可用，请把状态改为 UNAVAILABLE/FAIL 并附 reason。"
        ]

    # ② 退出码
    if receipt.get("exit_code") != 0:
        errs.append(
            f"  - [回执退出码非零] {rid} 声称 PASS 但 exit_code="
            f"{receipt.get('exit_code')!r}；命令未成功退出的操作不得记为 PASS，"
            f"请改为 FAIL 并在 reason 说明。")

    # ③ 输出摘要 + 落盘文件复核
    digest = receipt.get("output_digest")
    if not (isinstance(digest, str) and _HEX64.match(digest.strip().lower() or "")):
        errs.append(f"  - [回执缺输出摘要] {rid} 的 output_digest 不是 64 位十六进制 sha256。")
    else:
        errs += _verify_output_file(receipt, run_root, rid, digest)

    # ④ 执行时间窗
    executed_at = parse_iso(receipt.get("executed_at"))
    if executed_at is None:
        errs.append(f"  - [回执缺执行时间] {rid} 的 executed_at 不是合法 ISO 8601 时间。")
    else:
        current = now or datetime.now(timezone.utc)
        if executed_at > current + CLOCK_SKEW:
            errs.append(
                f"  - [回执时间越界] {rid} 的 executed_at={receipt.get('executed_at')} "
                f"晚于当前时间，命令不可能在未来执行。")
        elif run_started_at is not None and executed_at < run_started_at - CLOCK_SKEW:
            errs.append(
                f"  - [回执时间越界] {rid} 的 executed_at={receipt.get('executed_at')} "
                f"早于本 run 起始时间 {run_started_at.isoformat()}；"
                f"禁止复用其他 run 的历史回执。")

    # ⑤ operation ↔ argv 一致性
    argv = receipt.get("argv")
    if not (isinstance(argv, list) and argv
            and all(isinstance(a, str) and a.strip() for a in argv)):
        errs.append(f"  - [回执缺 argv] {rid} 状态 PASS 但 argv 不是非空字符串数组。")
    elif isinstance(op, str) and op not in argv:
        errs.append(
            f"  - [回执命令与操作不符] {rid} 声称 operation={op!r}，"
            f"但实际执行的 argv 中不含该操作 token：{argv}；"
            f"禁止用无关命令为某个操作背书。")

    # ⑥ journal 留痕
    entries = load_journal(run_root) if journal is None else journal
    entry = entries.get(rid)
    if entry is None:
        errs.append(
            f"  - [回执无执行留痕] {rid} 在 {JOURNAL_REL} 中查无记录；"
            f"执行器每次执行都会追加留痕，缺失说明该回执未经执行器产生。")
    elif isinstance(digest, str) and entry.get("output_digest") != digest:
        errs.append(
            f"  - [回执与留痕不符] {rid} 的 output_digest 与 {JOURNAL_REL} 记录不一致；"
            f"提交的回执不是最近一次真实执行的结果。")

    # ① 签名（放最后：先报可读的具体问题，再报签名总校验）
    expect = sign_payload(secret, receipt, run_id)
    if not hmac.compare_digest(expect, str(sig).strip().lower()):
        errs.append(
            f"  - [回执签名无效] {rid} 的 signature 与受签字段不匹配；"
            f"回执字段在签发后被改动过（或签名系手写）。请勿编辑执行器产出的回执，"
            f"需要更新请用执行器重跑。")
    return errs


# --------------------------------------------------------------------------
# 签发（执行核心）
# --------------------------------------------------------------------------

def execute_and_sign(run_root: Path, receipt_id: str, operation: str,
                     command: list, *, timeout: int = 900,
                     reason: str | None = None) -> tuple:
    """真实执行 command 并签发回执。返回 (receipt, exit_code)。

    这是签发的**唯一实现**：`scripts/run_evidence_command.py` 是它的 CLI 包装，
    测试夹具也直接调用它。之所以不让测试自己拼签名回执——那正是 v3.4.14 的
    false oracle 成因：夹具与被测代码共享同一套错误假设，测试便永远发现不了绑定失效。

    exit_code != 0 时返回 FAIL 回执（不签名，但同样写 journal 留痕，便于 audit
    复核"确实试过"）。
    """
    if operation not in command:
        raise ReceiptError(
            f"命令中不含 operation token {operation!r}: {command}；"
            f"Gate 会校验 operation ∈ argv，禁止用无关命令为某操作背书。")
    run_id = load_run_id(run_root)
    if not run_id:
        raise ReceiptError(f"runtime-state 缺 run_id，run_root 是否正确: {run_root}")
    secret = ensure_signing_secret(run_root)
    executed_at = datetime.now(timezone.utc).isoformat()

    import subprocess  # 局部导入：校验路径（Gate/审计）用不到 subprocess
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        exit_code, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        exit_code, stdout, stderr = 124, "", f"命令超时（>{timeout}s）"
    except FileNotFoundError as exc:
        exit_code, stdout, stderr = 127, "", f"命令不存在: {exc}"

    body = stdout if not stderr else f"{stdout}\n--- stderr ---\n{stderr}"
    out_dir = run_root / OUTPUT_DIR_REL
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{receipt_id}.txt"
    out_path.write_text(body, encoding="utf-8")
    rel_out = str(OUTPUT_DIR_REL / f"{receipt_id}.txt")
    digest = hashlib.sha256(out_path.read_bytes()).hexdigest()

    append_journal(run_root, {
        "receipt_id": receipt_id, "operation": operation, "argv": list(command),
        "exit_code": exit_code, "executed_at": executed_at, "output": rel_out,
        "output_digest": digest, "executor_version": EXECUTOR_VERSION,
        "run_id": run_id,
    })

    if exit_code != 0:
        return ({
            "receipt_id": receipt_id, "operation": operation, "status": "FAIL",
            "reason": (reason or "").strip()
            or f"命令退出码 {exit_code}；输出见 {rel_out}",
        }, exit_code)

    receipt = {
        "receipt_id": receipt_id, "operation": operation, "status": "PASS",
        "argv": list(command), "output": rel_out, "executed_at": executed_at,
        "exit_code": exit_code, "output_digest": digest,
        "executor_version": EXECUTOR_VERSION,
    }
    receipt["signature"] = sign_payload(secret, receipt, run_id)
    return receipt, 0


def _verify_output_file(receipt: dict, run_root: Path, rid, digest: str) -> list[str]:
    """复核 output 指向的落盘文件确实存在且内容摘要一致。"""
    out = receipt.get("output")
    if not (isinstance(out, str) and out.strip()):
        return [f"  - [回执缺 output] {rid} 状态 PASS 但 output（落盘输出相对路径）为空。"]
    rel = out.strip()
    if Path(rel).is_absolute() or ".." in Path(rel).parts:
        return [f"  - [回执输出路径非法] {rid} 的 output 必须是 run_root 下的相对路径: {rel}"]
    path = run_root / rel
    if not path.is_file() or path.is_symlink():
        return [
            f"  - [回执输出缺失] {rid} 的 output 指向 {rel}，但该文件不存在；"
            f"执行器会把命令输出落盘，缺失说明回执非执行器产出。"]
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != digest:
        return [
            f"  - [回执输出被篡改] {rid} 的落盘输出 {rel} 实际 sha256={actual[:16]}… "
            f"与回执声明的 output_digest={digest[:16]}… 不一致。"]
    return []
