#!/usr/bin/env python3
"""全量公司分析确定性验收器 (v1.4 §7/§8/§14) — 纯 stdlib, 零第三方依赖。

gate 只校验 manifest 里调用者提交的记录 + 实际文件, 不评判 judgments 内容
(领域证据 Phase 2)。子命令: init / begin-skill / finish-skill / checkpoint /
finalize / summary / contracts。

退出码 (§14.2):
  0 = 通过
  1 = 契约 / 数据 / 产物 / 最终验收失败
  2 = 参数 / schema 错误或非法状态转换
  3 = 锁冲突

路径执法级别: codex 与 claude_code 一律 MONITORED (监测式, 事后侦测);
本工具不做也不声称"预防式"拦截, 缺沙箱信号不阻断。
"""
import argparse
import hashlib
import json
import os
import re
import socket
import stat
import subprocess
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath

TOOLS_DIR = Path(__file__).resolve().parent
DEFAULT_REGISTRY = TOOLS_DIR / "full_analysis_contract.json"
FINANCIAL_RIGOR = TOOLS_DIR / "financial_rigor.py"
RESULT_SCHEMA_PATH = TOOLS_DIR / "financial_rigor_result_schema.json"

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2
EXIT_LOCK = 3

TZ_SHANGHAI = timezone(timedelta(hours=8))
MANIFEST_NAME = "manifest.json"
LOCK_REL = Path("evidence") / ".full-analysis.lock"
LOCKS_DIR_REL = Path("evidence") / "locks"
BASELINE_REL = Path("evidence") / "audit-baseline.txt"
RESULT_REL = Path("evidence") / "04-验收器结果.json"

EVIDENCE_KEYS = ("facts", "calculations", "judgments", "role_runs",
                 "limitations", "audit")
SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|secret|token|password|passwd|private[_-]?key)"
    r"\s*[=:]\s*\S+")
AS_OF_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
GIT_STATUS_ARGS = ("status", "--porcelain=v2", "-z",
                   "--untracked-files=all", "--ignored=no")


class GateExit(SystemExit):
    def __init__(self, code, *messages):
        for msg in messages:
            print(msg)
        super().__init__(code)


def now_iso():
    return datetime.now(TZ_SHANGHAI).isoformat()


def now_stamp():
    return datetime.now(TZ_SHANGHAI).strftime("%Y%m%dT%H%M%S%z")


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def atomic_write_json(path, obj):
    """同目录 tmp + flush + fsync + os.replace 原子写。"""
    path = Path(path)
    tmp = path.parent / f"{path.name}.tmp-{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def load_json_or_exit(path, what, code=EXIT_USAGE):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise GateExit(code, f"{what} 不可读或非法 JSON: {path}: {e}")


def load_registry(path):
    registry = load_json_or_exit(path, "注册表")
    if not isinstance(registry.get("skills"), list):
        raise GateExit(EXIT_USAGE, f"注册表缺 skills 数组: {path}")
    return registry


def load_manifest(run_root):
    mpath = Path(run_root) / MANIFEST_NAME
    if not mpath.is_file():
        raise GateExit(EXIT_USAGE, f"run-root 下无 manifest: {mpath}")
    return load_json_or_exit(mpath, "manifest")


def save_manifest(run_root, manifest):
    manifest["run"]["updated_at"] = now_iso()
    atomic_write_json(Path(run_root) / MANIFEST_NAME, manifest)


def run_git(repo_root, *args):
    return subprocess.run(["git", "-C", str(repo_root), *args],
                          capture_output=True, text=True)


# ---------------------------------------------------------------------------
# 路径 gate (§8.2) — 独立函数, 每个条件可单测
# ---------------------------------------------------------------------------
def path_gate(run_root, candidate, assigned_paths):
    """校验 run_root 内的产物候选路径, 返回错误列表 (空 = 通过)。"""
    errors = []
    run_root = Path(run_root)
    if os.path.islink(run_root):
        return [f"run_root 本身是软链接: {run_root}"]
    try:
        root_real = run_root.resolve(strict=True)
    except OSError as e:
        return [f"run_root 无法解析: {run_root}: {e}"]

    cand = unicodedata.normalize("NFC", str(candidate))
    if any(ord(ch) < 32 for ch in cand):
        return [f"候选路径含控制字符: {cand!r}"]
    if cand.startswith("/") or os.path.isabs(cand):
        return [f"候选路径是绝对路径: {cand}"]
    segments = cand.split("/")
    if any(seg == "" for seg in segments):
        return [f"候选路径含空段: {cand}"]
    if any(seg in (".", "..") for seg in segments):
        return [f"候选路径含相对段 . / .. : {cand}"]

    assigned_nfc = {unicodedata.normalize("NFC", p) for p in assigned_paths}
    if cand not in assigned_nfc:
        errors.append(f"路径未分配给该项 (不在 assigned_artifact_paths): {cand}")

    target = root_real / cand
    try:
        st = os.lstat(target)
    except OSError:
        errors.append(f"产物不存在: {cand}")
        return errors
    if stat.S_ISLNK(st.st_mode):
        errors.append(f"产物是软链接: {cand}")
        return errors
    try:
        real = target.resolve(strict=True)
    except OSError as e:
        errors.append(f"产物路径无法解析: {cand}: {e}")
        return errors
    if not real.is_relative_to(root_real):
        errors.append(f"产物解析后越出 run_root: {cand} -> {real}")
        return errors
    if not stat.S_ISREG(st.st_mode):
        errors.append(f"产物不是普通文件: {cand}")
        return errors
    if st.st_size == 0:
        errors.append(f"产物是空文件: {cand}")
    if st.st_nlink > 1:
        errors.append(f"产物存在硬链接 (st_nlink={st.st_nlink}): {cand}")
    if st.st_dev != os.lstat(root_real).st_dev:
        errors.append(f"产物跨设备 (st_dev 不一致): {cand}")
    return errors


# ---------------------------------------------------------------------------
# watchlist (§8.3): legacy 输出候选快照 + 比对
# ---------------------------------------------------------------------------
def snapshot_path(path):
    try:
        st = os.lstat(path)
    except OSError:
        return {"exists": False, "inode": None, "size": None, "mtime_ns": None}
    return {"exists": True, "inode": st.st_ino, "size": st.st_size,
            "mtime_ns": st.st_mtime_ns}


def build_watchlist(registry, company, as_of, repo_root):
    date = as_of.replace("-", "")
    home = os.environ.get("HOME", "")
    entries = []
    for sk in registry["skills"]:
        for pattern in sk.get("legacy_output_patterns", []):
            inst = pattern.replace("{company}", company).replace("{date}", date)
            if pattern.startswith("~/"):
                if not home:
                    continue
                base = Path(home)
                rel = inst[2:]
            else:
                base = Path(repo_root)
                rel = inst
            unknown = re.findall(r"\{(period|industry)\}", inst)
            if unknown:
                regex = re.escape(inst)
                for ph in ("period", "industry"):
                    regex = regex.replace(re.escape("{%s}" % ph), ".+?")
                prefix = []
                for seg in rel.split("/"):
                    if "{" in seg:
                        break
                    prefix.append(seg)
                watch = base.joinpath(*prefix) if prefix else base
                kind = "parameterized"
            else:
                regex = None
                watch = base / rel
                kind = "exact"
            entries.append({
                "skill": sk["name"],
                "index": sk["index"],
                "pattern": pattern,
                "kind": kind,
                "regex": regex,
                "watch_path": str(watch),
                "snapshot": snapshot_path(watch),
            })
    return entries


def watchlist_changes(watchlist):
    changes = []
    for entry in watchlist:
        current = snapshot_path(entry["watch_path"])
        if current != entry["snapshot"]:
            changes.append(
                f"watchlist 候选发生变化 [{entry['skill']}] "
                f"pattern={entry['pattern']!r} path={entry['watch_path']} "
                f"基线={entry['snapshot']} 当前={current}")
    return changes


def enforce_watchlist_or_block(manifest, run_root, skill_entry):
    """begin/finish 即时比对: 变化 → violation + 该项 BLOCKED + exit 1。"""
    changes = watchlist_changes(manifest.get("watchlist", []))
    if not changes:
        return
    skill_entry["violations"].append({
        "type": "watchlist_change",
        "detected_at": now_iso(),
        "changes": changes,
    })
    skill_entry["execution_state"] = "BLOCKED"
    save_manifest(run_root, manifest)
    raise GateExit(EXIT_FAIL, "watchlist 即时比对发现越界迹象, 该项已 BLOCKED:",
                   *[f"  - {c}" for c in changes])


def find_skill(manifest, name):
    for sk in manifest["skills"]:
        if sk["name"] == name:
            return sk
    raise GateExit(EXIT_USAGE, f"skill 不在注册表 / manifest: {name}")


def find_registry_item(registry, name):
    for item in registry["skills"]:
        if item["name"] == name:
            return item
    raise GateExit(EXIT_USAGE, f"skill 不在注册表: {name}")


# ---------------------------------------------------------------------------
# 锁 (§14.1)
# ---------------------------------------------------------------------------
def _pid_alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _fingerprint(pid):
    try:
        cp = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)],
                            capture_output=True, text=True)
        if cp.returncode != 0:
            return ""
        return cp.stdout.strip()
    except OSError:
        return ""


def acquire_lock(run_root, run_id, platform, root_real, recover_stale=None):
    run_root = Path(run_root)
    lock_path = run_root / LOCK_REL
    if lock_path.exists():
        try:
            info = json.loads(lock_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            info = {}
        host = socket.gethostname()
        if info.get("host") != host:
            raise GateExit(
                EXIT_LOCK,
                f"锁属于异 host ({info.get('host')!r}), 一律人工处理: {lock_path}")
        pid = info.get("pid")
        active = _pid_alive(pid) and _fingerprint(pid) == info.get(
            "start_fingerprint", "")
        if active:
            raise GateExit(EXIT_LOCK, f"存在活动锁 (pid={pid} 存活且指纹匹配): "
                                      f"{lock_path}")
        if recover_stale != info.get("run_id"):
            raise GateExit(
                EXIT_LOCK,
                f"存在陈旧锁, 需显式 --recover-stale {info.get('run_id')} 恢复: "
                f"{lock_path}")
        archive = run_root / LOCKS_DIR_REL / f"{now_stamp()}-recovered.json"
        archive.parent.mkdir(parents=True, exist_ok=True)
        os.rename(lock_path, archive)
    pid = os.getppid()
    fingerprint = _fingerprint(pid)
    if not fingerprint:
        print("warning: 取不到进程 start_fingerprint (ps lstart 失败), 记空串",
              file=sys.stderr)
    payload = {
        "run_id": run_id,
        "host": socket.gethostname(),
        "pid": pid,
        "start_fingerprint": fingerprint,
        "platform": platform,
        "root_real": str(root_real),
        "started_at": now_iso(),
    }
    fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    try:
        os.write(fd, json.dumps(payload, ensure_ascii=False,
                                indent=2).encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)


def release_lock(run_root):
    lock_path = Path(run_root) / LOCK_REL
    if not lock_path.exists():
        return
    archive = Path(run_root) / LOCKS_DIR_REL / f"{now_stamp()}-released.json"
    archive.parent.mkdir(parents=True, exist_ok=True)
    os.rename(lock_path, archive)


# ---------------------------------------------------------------------------
# 产物校验 (checkpoint / finalize 共用)
# ---------------------------------------------------------------------------
def check_artifact(run_root, rel_path, rule, assigned_paths):
    errors = path_gate(run_root, rel_path, assigned_paths)
    target = Path(run_root) / unicodedata.normalize("NFC", str(rel_path))
    if errors:
        return errors
    try:
        raw = target.read_bytes()
    except OSError as e:
        return [f"产物不可读: {rel_path}: {e}"]
    if len(raw) < rule.get("min_bytes", 0):
        errors.append(f"产物 {rel_path} 小于 min_bytes="
                      f"{rule.get('min_bytes')} (实际 {len(raw)} bytes)")
    text = raw.decode("utf-8", errors="replace")
    for section in rule.get("required_sections", []):
        if section not in text:
            errors.append(f"产物 {rel_path} 缺 required_section 子串: {section!r}")
    return errors


def rules_by_path(item):
    return {rule["path"]: rule for rule in item.get("artifact_rules", [])}


def check_registry_matches_manifest(registry_path, manifest):
    actual = sha256_file(registry_path)
    if actual != manifest.get("registry_sha256"):
        raise GateExit(EXIT_USAGE,
                       "注册表与 manifest.registry_sha256 不一致 "
                       "(传错 --registry 或注册表已变, 变更需走 resume): "
                       f"{registry_path}")


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------
def validate_company(company):
    if (not company or "/" in company or "\\" in company
            or ".." in company or company.startswith("~")
            or any(ord(ch) < 32 for ch in company)):
        raise GateExit(EXIT_USAGE, f"公司名非法 (不允许路径分隔/../控制字符): "
                                   f"{company!r}")


def git_status_text(repo_root):
    cp = subprocess.run(["git", "-C", str(repo_root), *GIT_STATUS_ARGS],
                        capture_output=True)
    if cp.returncode != 0:
        raise GateExit(EXIT_USAGE,
                       f"git status 失败: {cp.stderr.decode('utf-8', 'replace')}")
    return cp.stdout.decode("utf-8", errors="surrogateescape")


def new_skill_entry(item, repo_root):
    spec = Path(repo_root) / item["spec_source"]
    if not spec.is_file():
        raise GateExit(EXIT_USAGE, f"spec_source 不存在: {item['spec_source']}")
    return {
        "index": item["index"],
        "name": item["name"],
        "spec_sha256": sha256_file(spec),
        "execution_state": "PENDING",
        "computed_status": None,
        "execution_mode": None,
        "independent_context_count": 0,
        "assigned_artifact_paths": [r["path"] for r in item["artifact_rules"]],
        "artifacts": [],
        "facts": [],
        "calculations": [],
        "judgments": [],
        "role_runs": [],
        "limitations": [],
        "audit": [],
        "attempts": [],
        "violations": [],
    }


def cmd_init(args):
    repo_root = Path(args.repo_root).resolve()
    cp = run_git(repo_root, "rev-parse", "--is-inside-work-tree")
    if cp.returncode != 0 or cp.stdout.strip() != "true":
        raise GateExit(EXIT_USAGE,
                       f"--repo-root 不在 git 工作树内 (无非 Git fallback): "
                       f"{repo_root}")
    validate_company(args.company)
    if not AS_OF_RE.match(args.as_of):
        raise GateExit(EXIT_USAGE, f"--as-of 必须为 YYYY-MM-DD: {args.as_of}")
    if args.path_enforcement_level == "SANDBOXED":
        raise GateExit(EXIT_USAGE,
                       "声明 SANDBOXED 需要 canary 收据, 本工具无法出具; "
                       "两平台一律 MONITORED (监测式)")

    registry = load_registry(args.registry)
    if args.mode == "resume":
        return _init_resume(args, repo_root, registry)

    run_id = f"{now_stamp()}-{args.company}"
    prefix = Path("local") / "筛选公司" if args.visibility == "private" \
        else Path("筛选公司")
    rel_root = prefix / args.company / "全量分析" / run_id
    run_root = repo_root / rel_root
    if args.visibility == "private":
        ci = run_git(repo_root, "check-ignore", "--no-index", "-q", "--",
                     rel_root.as_posix())
        if ci.returncode != 0:
            raise GateExit(EXIT_FAIL,
                           f"private 运行根未被 .gitignore 忽略, 会入库泄露: "
                           f"{rel_root.as_posix()}")
    elif rel_root.parts[0] == "local":
        raise GateExit(EXIT_FAIL, f"public 运行根不得在 local/ 下: {rel_root}")

    run_root.mkdir(parents=True, exist_ok=True)
    for stage_dir in registry.get("stage_dirs", {}).values():
        (run_root / stage_dir).mkdir(exist_ok=True)
    (run_root / registry.get("negative_acceptance_dir", "06-负向验收")) \
        .mkdir(exist_ok=True)
    (run_root / LOCKS_DIR_REL).mkdir(parents=True, exist_ok=True)

    root_real = run_root.resolve(strict=True)
    acquire_lock(run_root, run_id, args.platform, root_real,
                 recover_stale=args.recover_stale)

    baseline = git_status_text(repo_root)
    with open(run_root / BASELINE_REL, "w", encoding="utf-8",
              errors="surrogateescape") as f:
        f.write(baseline)
        f.flush()
        os.fsync(f.fileno())

    ts = now_iso()
    manifest = {
        "manifest_schema_version": 1,
        "registry_schema_version": registry.get("registry_schema_version", 1),
        "annotations_schema_version": 1,
        "registry_sha256": sha256_file(args.registry),
        "run": {
            "run_id": run_id,
            "phase": "WORKING",
            "platform": args.platform,
            "visibility": args.visibility,
            "run_root": rel_root.as_posix(),
            "root_real": str(root_real),
            "path_enforcement_level": "MONITORED",
            "completion_status": None,
            "validation_result": None,
            "assurance_level": "SINGLE_CONTEXT",
            "review_mode": None,
            "created_at": ts,
            "updated_at": ts,
        },
        "company": {
            "name": args.company,
            "codes": list(args.codes or []),
            "listing_status": args.listing_status,
            "as_of": args.as_of,
            "timezone": "Asia/Shanghai",
            "industry": None,
        },
        "skills": [new_skill_entry(item, repo_root)
                   for item in registry["skills"]],
        "watchlist": build_watchlist(registry, args.company, args.as_of,
                                     repo_root),
        "annotations": {},
    }
    atomic_write_json(run_root / MANIFEST_NAME, manifest)
    print(json.dumps({
        "run_id": run_id,
        "run_root": str(run_root),
        "phase": "WORKING",
        "path_enforcement_level": "MONITORED",
    }, ensure_ascii=False))
    return EXIT_OK


def _init_resume(args, repo_root, registry):
    if not args.run_id:
        raise GateExit(EXIT_USAGE, "--mode resume 必须提供 --run-id")
    prefix = Path("local") / "筛选公司" if args.visibility == "private" \
        else Path("筛选公司")
    rel_root = prefix / args.company / "全量分析" / args.run_id
    run_root = repo_root / rel_root
    manifest = load_manifest(run_root)
    run = manifest["run"]
    try:
        root_real = str(run_root.resolve(strict=True))
    except OSError as e:
        raise GateExit(EXIT_USAGE, f"run_root 无法解析: {run_root}: {e}")
    mismatches = [
        (field, expect, got)
        for field, expect, got in (
            ("run_id", args.run_id, run.get("run_id")),
            ("visibility", args.visibility, run.get("visibility")),
            ("platform", args.platform, run.get("platform")),
            ("root_real", root_real, run.get("root_real")),
        ) if expect != got]
    if mismatches:
        raise GateExit(EXIT_USAGE, "resume 与 manifest 不匹配:",
                       *[f"  - {f}: 期望 {e!r} 实际 {g!r}"
                         for f, e, g in mismatches])

    acquire_lock(run_root, args.run_id, args.platform, root_real,
                 recover_stale=args.recover_stale)

    manifest["registry_sha256"] = sha256_file(args.registry)
    reset = []
    items = {item["name"]: item for item in registry["skills"]}
    for sk in manifest["skills"]:
        item = items.get(sk["name"])
        if item is None:
            continue
        spec = repo_root / item["spec_source"]
        new_sha = sha256_file(spec) if spec.is_file() else None
        if new_sha != sk.get("spec_sha256"):
            sk["spec_sha256"] = new_sha
            sk["execution_state"] = "PENDING"
            sk["computed_status"] = None
            sk["limitations"].append({
                "code": "invalidated_by_spec_change",
                "detected_at": now_iso(),
            })
            reset.append(sk["name"])
    save_manifest(run_root, manifest)
    print(json.dumps({
        "run_id": args.run_id,
        "run_root": str(run_root),
        "resumed": True,
        "reset_skills": reset,
    }, ensure_ascii=False))
    return EXIT_OK


# ---------------------------------------------------------------------------
# begin-skill / finish-skill
# ---------------------------------------------------------------------------
def cmd_begin_skill(args):
    run_root = Path(args.run_root)
    manifest = load_manifest(run_root)
    sk = find_skill(manifest, args.skill)
    enforce_watchlist_or_block(manifest, run_root, sk)
    state = sk["execution_state"]
    if state not in ("PENDING", "BLOCKED"):
        raise GateExit(EXIT_USAGE,
                       f"非法状态转换: {args.skill} 当前 {state}, "
                       f"仅 PENDING/BLOCKED 可 begin (COMPLETE 未失效不可重跑)")
    sk["execution_state"] = "RUNNING"
    if args.execution_mode is not None:
        sk["execution_mode"] = args.execution_mode
    if args.independent_context_count is not None:
        sk["independent_context_count"] = args.independent_context_count
    sk["attempts"].append({
        "attempt_id": f"{args.skill}-{len(sk['attempts']) + 1}-{now_stamp()}",
        "started_at": now_iso(),
        "execution_mode": sk["execution_mode"],
        "assigned_artifact_paths": list(sk["assigned_artifact_paths"]),
    })
    save_manifest(run_root, manifest)
    print(f"begin-skill: {args.skill} -> RUNNING")
    return EXIT_OK


def validate_evidence_payload(payload):
    if not isinstance(payload, dict):
        raise GateExit(EXIT_USAGE, "evidence-file 必须是 JSON object")
    extra = set(payload) - set(EVIDENCE_KEYS)
    if extra:
        raise GateExit(EXIT_USAGE,
                       f"evidence-file 封闭 schema, 不允许键: {sorted(extra)} "
                       f"(允许 {sorted(EVIDENCE_KEYS)}; status/computed_status/"
                       f"counts/assurance 只能 gate 计算)")
    for key in EVIDENCE_KEYS:
        if key in payload and not isinstance(payload[key], list):
            raise GateExit(EXIT_USAGE, f"evidence-file {key} 必须为数组")
    for fact in payload.get("facts", []):
        if isinstance(fact, dict) and (
                "status" in fact or "computed_status" in fact):
            raise GateExit(EXIT_USAGE,
                           "fact 记录不得携带 status/computed_status "
                           "(状态只能 gate 计算)")


def cmd_finish_skill(args):
    run_root = Path(args.run_root)
    manifest = load_manifest(run_root)
    sk = find_skill(manifest, args.skill)
    enforce_watchlist_or_block(manifest, run_root, sk)
    if sk["execution_state"] != "RUNNING":
        raise GateExit(EXIT_USAGE,
                       f"非法状态转换: {args.skill} 当前 "
                       f"{sk['execution_state']}, 仅 RUNNING 可 finish")
    assigned = {unicodedata.normalize("NFC", p)
                for p in sk["assigned_artifact_paths"]}
    for art in args.artifact or []:
        if unicodedata.normalize("NFC", art) not in assigned:
            raise GateExit(EXIT_USAGE,
                           f"--artifact 不在 assigned_artifact_paths: {art}")
    payload = {}
    if args.evidence_file:
        payload = load_json_or_exit(args.evidence_file, "evidence-file")
        validate_evidence_payload(payload)
    for art in args.artifact or []:
        norm = unicodedata.normalize("NFC", art)
        if norm not in sk["artifacts"]:
            sk["artifacts"].append(norm)
    for key in EVIDENCE_KEYS:
        if payload.get(key):
            sk[key].extend(payload[key])
    sk["execution_state"] = args.state
    save_manifest(run_root, manifest)
    print(f"finish-skill: {args.skill} -> {args.state}")
    return EXIT_OK


# ---------------------------------------------------------------------------
# checkpoint
# ---------------------------------------------------------------------------
def cmd_checkpoint(args):
    run_root = Path(args.run_root)
    manifest = load_manifest(run_root)
    registry = load_registry(args.registry)
    check_registry_matches_manifest(args.registry, manifest)
    items = {item["name"]: item for item in registry["skills"]}

    for sk in manifest["skills"]:
        if sk["computed_status"] is not None:
            raise GateExit(EXIT_USAGE,
                           f"checkpoint 要求所有 computed_status 仍为 null, "
                           f"但 {sk['name']} 已为 {sk['computed_status']}")

    problems = []
    wl_changes = watchlist_changes(manifest.get("watchlist", []))
    if wl_changes:
        problems.extend(wl_changes)
        # violation 记到 watchlist 所属项
        by_name = {sk["name"]: sk for sk in manifest["skills"]}
        for entry in manifest.get("watchlist", []):
            current = snapshot_path(entry["watch_path"])
            if current != entry["snapshot"]:
                owner = by_name.get(entry["skill"])
                if owner is not None:
                    owner["violations"].append({
                        "type": "watchlist_change",
                        "detected_at": now_iso(),
                        "pattern": entry["pattern"],
                        "watch_path": entry["watch_path"],
                    })
        save_manifest(run_root, manifest)

    for sk in manifest["skills"]:
        if sk["execution_state"] != "COMPLETE":
            continue
        item = items.get(sk["name"])
        if item is None:
            problems.append(f"[{sk['name']}] 不在注册表")
            continue
        rules = rules_by_path(item)
        declared = sk["artifacts"] or []
        for art in declared:
            rule = rules.get(unicodedata.normalize("NFC", art))
            if rule is None:
                problems.append(f"[{sk['name']}] 声明产物无对应规则: {art}")
                continue
            for err in check_artifact(run_root, art, rule,
                                      sk["assigned_artifact_paths"]):
                problems.append(f"[{sk['name']}] {err}")
        for path in sk["assigned_artifact_paths"]:
            if unicodedata.normalize("NFC", path) not in {
                    unicodedata.normalize("NFC", a) for a in declared}:
                problems.append(f"[{sk['name']}] COMPLETE 但未声明产物: {path}")

    if problems:
        print(f"checkpoint 发现 {len(problems)} 个问题:")
        for p in problems:
            print(f"  - {p}")
        return EXIT_FAIL
    print("checkpoint 通过: COMPLETE 项产物合规, watchlist 无变化")
    return EXIT_OK


# ---------------------------------------------------------------------------
# finalize (§7.2/§8.3/§10/§11) — 机器计算最终状态, 调用者不能提交任何状态
# ---------------------------------------------------------------------------
def _dec(text):
    try:
        return Decimal(str(text))
    except (InvalidOperation, ValueError, TypeError):
        return None


def classify_fact(fact):
    """#6: gate 计算 DUAL_SOURCE/SINGLE_SOURCE/CONFLICT/UNAVAILABLE。

    可比源 = 源级 subject/period/unit 缺省继承 fact 级, 提供则必须相等;
    一致 = 与 fact.value 偏差 ≤ tolerance_pct;
    双源 = 存在两个一致源 publisher 互异且 chain 互异且均非空;
    任一可比源偏差超容差 = CONFLICT (未解释分歧, fail-closed)。
    """
    value = _dec(fact.get("value"))
    tol = _dec(fact.get("tolerance_pct"))
    comparable = []
    for src in fact.get("sources", []):
        if not isinstance(src, dict):
            continue
        mismatch = any(
            key in src and src[key] != fact.get(key)
            for key in ("subject", "period", "unit"))
        if not mismatch:
            comparable.append(src)
    if not comparable:
        return "UNAVAILABLE"
    if value is None or value == 0 or tol is None:
        return "SINGLE_SOURCE"
    agreeing = []
    for src in comparable:
        obs = _dec(src.get("observed_value"))
        if obs is None:
            return "CONFLICT"
        deviation = abs(obs - value) / abs(value) * 100
        if deviation > tol:
            return "CONFLICT"
        agreeing.append(src)
    for i, a in enumerate(agreeing):
        for b in agreeing[i + 1:]:
            pa, pb = a.get("publisher_id"), b.get("publisher_id")
            ca, cb = (a.get("acquisition_chain_id"),
                      b.get("acquisition_chain_id"))
            if pa and pb and pa != pb and ca and cb and ca != cb:
                return "DUAL_SOURCE"
    return "SINGLE_SOURCE"


EVIDENCE_RULE_KINDS = {
    "min_facts", "min_dual_source_facts", "min_calculations",
    "min_judgments_with_falsification", "min_role_runs",
}


def evaluate_evidence_rules(sk, item):
    """Phase 2 (§6.4/§15.3): 领域证据结构性存在检查, 不判内容对错 (§2.2)。

    须在 facts 分类之后调用 (依赖 fact.computed_status)。返回错误列表。
    """
    errors = []
    facts = [f for f in sk.get("facts", []) if isinstance(f, dict)]
    calcs = [c for c in sk.get("calculations", []) if isinstance(c, dict)]
    judgments = [j for j in sk.get("judgments", []) if isinstance(j, dict)]
    role_runs = [r for r in sk.get("role_runs", []) if isinstance(r, dict)]
    for rule in item.get("evidence_rules", []):
        if not isinstance(rule, dict):
            continue
        kind = rule.get("kind")
        n = rule.get("n", 1)
        if kind == "min_facts" and len(facts) < n:
            errors.append(f"领域证据不足: 需 ≥{n} 条关键事实记录, 实际 {len(facts)}")
        elif kind == "min_dual_source_facts":
            dual = sum(1 for f in facts
                       if f.get("computed_status") == "DUAL_SOURCE")
            if dual < n:
                errors.append(f"领域证据不足: 需 ≥{n} 条双源关键事实, 实际 {dual}")
        elif kind == "min_calculations" and len(calcs) < n:
            errors.append(f"领域证据不足: 需 ≥{n} 条计算重放记录, 实际 {len(calcs)}")
        elif kind == "min_judgments_with_falsification":
            good = sum(1 for j in judgments
                       if str(j.get("falsification_condition", "")).strip())
            if good < n:
                errors.append(f"领域证据不足: 需 ≥{n} 条带证伪条件的判断, 实际 {good}")
        elif kind == "min_role_runs" and len(role_runs) < n:
            errors.append(f"领域证据不足: 需 ≥{n} 条角色执行记录, 实际 {len(role_runs)}")
    return errors


def load_calc_allowlist():
    schema = load_json_or_exit(RESULT_SCHEMA_PATH, "financial_rigor 结果 schema")
    return set(schema.get("operations", {}))


def replay_calculation(calc, allowlist):
    """#10: 用当前工具 --json 重放, 语义字段比较; 返回错误列表。"""
    cid = calc.get("calculation_id", "<无id>")
    ctype = calc.get("type")
    if ctype not in allowlist:
        return [f"计算 {cid} type 不在 allowlist: {ctype!r}"]
    cmd = [sys.executable, str(FINANCIAL_RIGOR), ctype]
    for key, val in (calc.get("args") or {}).items():
        flag = "--" + str(key).replace("_", "-")
        if isinstance(val, list):
            cmd.append(flag)
            cmd.extend(str(v) for v in val)
        else:
            cmd.extend([flag, str(val)])
    cmd.append("--json")
    try:
        cp = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        return [f"计算 {cid} 重放执行失败: {e}"]
    try:
        env = json.loads(cp.stdout)
    except json.JSONDecodeError:
        return [f"计算 {cid} 重放未产出合法 JSON (exit {cp.returncode})"]
    errors = []
    expected = calc.get("expected") or {}
    for key in ("outcome", "is_pass", "exit_code"):
        if key in expected and env.get(key) != expected[key]:
            errors.append(f"计算 {cid} {key} 不一致: 期望 {expected[key]!r} "
                          f"实际 {env.get(key)!r}")
    actual_result = env.get("result") or {}
    for key, want in (expected.get("result") or {}).items():
        got = actual_result.get(key)
        want_d, got_d = _dec(want), _dec(got)
        same = (want_d is not None and got_d is not None
                and want_d == got_d) or want == got
        if not same:
            errors.append(f"计算 {cid} result.{key} 不一致: 期望 {want!r} "
                          f"实际 {got!r}")
    return errors


def scan_artifact_privacy(run_root, rel_path):
    """#14: credential 赋值模式 → 只报 key 名, 绝不含值。"""
    try:
        text = (Path(run_root) / rel_path).read_text(encoding="utf-8",
                                                     errors="replace")
    except OSError:
        return []
    m = SECRET_RE.search(text)
    if m:
        return [f"隐私扫描命中 credential 赋值模式 "
                f"(rule=credential_assignment, key={m.group(1)}): {rel_path}"]
    return []


def evaluate_audit(sk, item):
    """#14: required/advisory/none 三态审计策略。返回 (errors, caps)。"""
    errors, caps = [], []
    records = sk.get("audit", [])
    for rule in item.get("artifact_rules", []):
        policy = rule.get("audit_policy", "none")
        if policy == "none":
            continue
        matched = [a for a in records
                   if isinstance(a, dict) and a.get("artifact") == rule["path"]]
        for rec in matched:
            if rec.get("sample_count") == 0 and rec.get("verdict") == "PASS":
                errors.append(f"审计记录 0 样本却 PASS (永不允许): {rule['path']}")
        if policy == "required":
            if not matched:
                errors.append(f"required 审计缺记录: {rule['path']}")
            for rec in matched:
                if rec.get("verdict") != "PASS":
                    errors.append(f"required 审计 verdict="
                                  f"{rec.get('verdict')}: {rule['path']}")
        elif policy == "advisory":
            if not matched:
                caps.append(f"advisory 审计缺记录: {rule['path']}")
            for rec in matched:
                if rec.get("verdict") == "FAIL":
                    errors.append(f"advisory 审计 verdict=FAIL: {rule['path']}")
                elif rec.get("verdict") == "INSUFFICIENT":
                    caps.append(f"advisory 审计 INSUFFICIENT: {rule['path']}")
    return errors, caps


def evaluate_not_applicable(sk, item, registry, run_root):
    """[b] N/A 负向验收: 谓词/输入事实/负向产物/替代路径四要素齐备才 N/A PASS。"""
    na = next((l for l in sk.get("limitations", [])
               if isinstance(l, dict) and l.get("code") == "not_applicable"),
              None)
    if na is None:
        return None, []
    errors = []
    predicates = registry.get("predicates", [])
    if na.get("predicate_id") not in predicates:
        errors.append(f"N/A 谓词不在注册表: {na.get('predicate_id')!r}")
    if not na.get("input_facts"):
        errors.append("N/A 缺 input_facts (谓词输入事实)")
    neg_dir = registry.get("negative_acceptance_dir", "06-负向验收")
    neg = Path(run_root) / neg_dir / f"{item['index']:02d}-{item['name']}.md"
    if not neg.is_file() or neg.stat().st_size == 0:
        errors.append(f"N/A 缺负向验收产物: {neg_dir}/{neg.name}")
    expect_alt = item.get("applicability_rule", {}).get("alternative")
    if na.get("alternative") != expect_alt:
        errors.append(f"N/A alternative 与注册表不一致: "
                      f"{na.get('alternative')!r} != {expect_alt!r}")
    return na, errors


def evaluate_roles(sk, item, registry, run_root):
    """[g] 角色规则: 缺必需角色 FAIL; 独立上下文不足按 sequential_cap 封顶。"""
    errors, caps = [], []
    rr = item.get("role_rule", {})
    required = rr.get("required_roles") or []
    if required:
        present = {r.get("role") for r in sk.get("role_runs", [])
                   if isinstance(r, dict)}
        missing = [r for r in required if r not in present]
        if missing:
            errors.append(f"缺必需角色产物: {missing}")
    min_ctx = rr.get("min_independent_contexts", 0)
    if min_ctx and sk.get("independent_context_count", 0) < min_ctx:
        cap = rr.get("sequential_cap", "PASS")
        if cap == "NOT_APPLICABLE_PASS":
            na, na_errors = evaluate_not_applicable(sk, item, registry,
                                                    run_root)
            if na is None:
                errors.append(f"独立上下文不足且未走负向验收 "
                              f"(cap={cap}, 需 N/A 收口)")
            else:
                errors.extend(na_errors)
        elif cap == "PASS_WITH_LIMITATIONS":
            caps.append(f"独立上下文不足 ({sk.get('independent_context_count')}"
                        f"/{min_ctx}), 封顶 PWL")
    return errors, caps


def parse_porcelain_v2_z(data):
    """解析 git status --porcelain=v2 -z 输出 → [(record, path)]。"""
    tokens = data.split("\0")
    records = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        i += 1
        if not tok:
            continue
        if tok.startswith("2 "):
            parts = tok.split(" ", 9)
            path = parts[9] if len(parts) > 9 else tok
            if i < len(tokens):
                i += 1  # -z 下 rename 的 origPath 是下一个 NUL 段
            records.append((tok, path))
        elif tok.startswith("1 "):
            parts = tok.split(" ", 8)
            path = parts[8] if len(parts) > 8 else tok
            records.append((tok, path))
        elif tok.startswith("u "):
            parts = tok.split(" ", 10)
            path = parts[10] if len(parts) > 10 else tok
            records.append((tok, path))
        elif tok.startswith(("? ", "! ")):
            records.append((tok, tok[2:]))
        else:
            records.append((tok, tok))
    return records


def git_boundary_errors(manifest, run_root):
    """#11 git 可见变化层: 与 init 基线 diff, 剔除 run_root/.git/__pycache__。

    reports/INDEX.md 不在 allowlist — 任何变化都是越界 (§8.3)。
    """
    run = manifest["run"]
    rel = PurePosixPath(run["run_root"])
    repo_root = Path(run["root_real"]).parents[len(rel.parts) - 1]
    try:
        baseline = (Path(run_root) / BASELINE_REL).read_text(
            encoding="utf-8", errors="surrogateescape")
    except OSError as e:
        return [f"审计基线不可读: {e}"]
    current = git_status_text(repo_root)
    known = {rec for rec, _ in parse_porcelain_v2_z(baseline)}
    rel_str = rel.as_posix()
    errors = []
    for rec, path in parse_porcelain_v2_z(current):
        if rec in known:
            continue
        if path == rel_str or path.startswith(rel_str + "/"):
            continue
        segments = path.split("/")
        if ".git" in segments or "__pycache__" in segments:
            continue
        errors.append(f"git 可见越界变化 (不在基线, 不在运行根): {path}")
    return errors


def evaluate_skill(sk, item, registry, run_root, allowlist):
    """逐项计算 computed_status; 返回 (status, errors, caps)。"""
    if sk["execution_state"] == "BLOCKED":
        return "FAIL", ["执行态 BLOCKED (阻塞只能计为 FAIL)"], []

    errors, caps = [], []

    na, na_errors = evaluate_not_applicable(sk, item, registry, run_root)
    if na is not None:
        return ("FAIL", na_errors, []) if na_errors \
            else ("NOT_APPLICABLE_PASS", [], [])

    rules = rules_by_path(item)
    declared = {unicodedata.normalize("NFC", a) for a in sk["artifacts"]}
    for path, rule in rules.items():
        norm = unicodedata.normalize("NFC", path)
        if norm not in declared:
            errors.append(f"必需产物未声明: {path}")
            continue
        errors.extend(check_artifact(run_root, norm, rule,
                                     sk["assigned_artifact_paths"]))
        errors.extend(scan_artifact_privacy(run_root, norm))

    for calc in sk.get("calculations", []):
        errors.extend(replay_calculation(calc, allowlist))

    for fact in sk.get("facts", []):
        if not isinstance(fact, dict):
            continue
        status = classify_fact(fact)
        fact["computed_status"] = status
        fid = fact.get("fact_id", "<无id>")
        if status == "CONFLICT":
            errors.append(f"关键事实 {fid} 为 CONFLICT (未解释冲突不得准出)")
        elif status in ("SINGLE_SOURCE", "UNAVAILABLE"):
            caps.append(f"关键事实 {fid} 证据不足 ({status})")

    # Phase 2 领域证据结构性检查 (须在 fact 分类之后)
    errors.extend(evaluate_evidence_rules(sk, item))

    audit_errors, audit_caps = evaluate_audit(sk, item)
    errors.extend(audit_errors)
    caps.extend(audit_caps)

    role_errors, role_caps = evaluate_roles(sk, item, registry, run_root)
    errors.extend(role_errors)
    caps.extend(role_caps)

    for lim in sk.get("limitations", []):
        code = lim.get("code") if isinstance(lim, dict) else str(lim)
        caps.append(f"已记录 limitation: {code}")

    if errors:
        return "FAIL", errors, caps
    if caps:
        return "PASS_WITH_LIMITATIONS", [], caps
    return "PASS", [], []


def compute_assurance(manifest, registry):
    items = {item["name"]: item for item in registry["skills"]}
    multi = []
    for sk in manifest["skills"]:
        rr = items.get(sk["name"], {}).get("role_rule", {})
        min_ctx = rr.get("min_independent_contexts", 0)
        if min_ctx:
            multi.append(sk.get("independent_context_count", 0) >= min_ctx)
    if not multi:
        return "SINGLE_CONTEXT"
    if all(multi):
        return "INDEPENDENT"
    if any(multi):
        return "MIXED"
    return "SINGLE_CONTEXT"


def cmd_finalize(args):
    run_root = Path(args.run_root)
    manifest = load_manifest(run_root)
    registry = load_registry(args.registry)
    check_registry_matches_manifest(args.registry, manifest)
    items = {item["name"]: item for item in registry["skills"]}

    unfinished = [f"{sk['name']}={sk['execution_state']}"
                  for sk in manifest["skills"]
                  if sk["execution_state"] in ("PENDING", "RUNNING")]
    if unfinished:
        raise GateExit(EXIT_USAGE,
                       "finalize 拒绝: 存在 PENDING/RUNNING 未收口项:",
                       *[f"  - {u}" for u in unfinished])

    allowlist = load_calc_allowlist()
    matrix = []
    any_fail = False
    any_pwl = False
    for sk in manifest["skills"]:
        item = items.get(sk["name"])
        if item is None:
            status, errors, caps = "FAIL", [f"不在注册表: {sk['name']}"], []
        else:
            status, errors, caps = evaluate_skill(
                sk, item, registry, run_root, allowlist)
        sk["computed_status"] = status
        any_fail = any_fail or status == "FAIL"
        any_pwl = any_pwl or status == "PASS_WITH_LIMITATIONS"
        matrix.append({
            "index": sk["index"],
            "name": sk["name"],
            "computed_status": status,
            "errors": errors,
            "caps": caps,
        })

    run_errors = git_boundary_errors(manifest, run_root)
    run_errors.extend(watchlist_changes(manifest.get("watchlist", [])))

    completion = "COMPLETE"
    validation = "FAIL" if (any_fail or run_errors) else (
        "PASS_WITH_LIMITATIONS" if any_pwl else "PASS")
    assurance = compute_assurance(manifest, registry)

    run = manifest["run"]
    run["completion_status"] = completion
    run["validation_result"] = validation
    run["assurance_level"] = assurance
    run["phase"] = "FINALIZED"

    result = {
        "run_id": run["run_id"],
        "completion_status": completion,
        "validation_result": validation,
        "assurance_level": assurance,
        "matrix": matrix,
        "run_errors": run_errors,
        "finalized_at": now_iso(),
    }
    atomic_write_json(run_root / RESULT_REL, result)
    save_manifest(run_root, manifest)
    release_lock(run_root)

    print(f"finalize: completion_status={completion} "
          f"validation_result={validation} assurance_level={assurance}")
    problems = [e for row in matrix for e in row["errors"]] + run_errors
    if problems:
        print(f"验收失败 {len(problems)} 项:")
        for p in problems:
            print(f"  - {p}")
        return EXIT_FAIL
    return EXIT_OK


# ---------------------------------------------------------------------------
# summary — 只从 gate 计算结果生成, 不接受调用者计数
# ---------------------------------------------------------------------------
def cmd_summary(args):
    result_path = Path(args.run_root) / RESULT_REL
    if not result_path.is_file():
        raise GateExit(EXIT_USAGE,
                       f"无验收器结果 (先跑 finalize): {result_path}")
    result = load_json_or_exit(result_path, "验收器结果")
    print(f"completion_status={result['completion_status']}")
    print(f"validation_result={result['validation_result']}")
    print(f"assurance_level={result['assurance_level']}")
    counts = {}
    for row in result["matrix"]:
        counts[row["computed_status"]] = \
            counts.get(row["computed_status"], 0) + 1
    print("状态计数: " + ", ".join(
        f"{k}={v}" for k, v in sorted(counts.items())))
    print("20 行矩阵:")
    for row in result["matrix"]:
        print(f"  {row['index']:02d}  {row['name']}  "
              f"{row['computed_status']}")
    if result.get("run_errors"):
        print("run 级错误:")
        for e in result["run_errors"]:
            print(f"  - {e}")
    return EXIT_OK


# ---------------------------------------------------------------------------
# contracts
# ---------------------------------------------------------------------------
def cmd_contracts(args):
    registry = load_registry(args.registry)
    skills = registry["skills"]
    errors = []
    if len(skills) != 20:
        errors.append(f"skills 必须恰好 20 项, 实际 {len(skills)}")
    indexes = [s.get("index") for s in skills]
    if len(set(indexes)) != len(indexes) or sorted(indexes) != list(
            range(1, len(skills) + 1)):
        errors.append(f"index 必须 1..{len(skills)} 唯一, 实际 {sorted(indexes)}")
    names = [s.get("name") for s in skills]
    if len(set(names)) != len(names):
        errors.append("name 存在重复")
    if errors:
        raise GateExit(EXIT_FAIL, "contracts 校验失败:",
                       *[f"  - {e}" for e in errors])
    items = [{
        "index": s["index"],
        "name": s["name"],
        "stage": s["stage"],
        "audit_policy": s["artifact_rules"][0]["audit_policy"]
        if s.get("artifact_rules") else None,
    } for s in skills]
    print(json.dumps({"count": len(items), "items": items},
                     ensure_ascii=False, indent=1))
    return EXIT_OK


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser():
    parser = argparse.ArgumentParser(
        prog="full_analysis_gate.py",
        description="全量公司分析确定性验收器 (v1.4)")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_registry(p):
        p.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)

    def add_run_root(p):
        p.add_argument("--run-root", type=Path, required=True)

    p_init = sub.add_parser("init", help="初始化运行根 + manifest + 锁")
    add_registry(p_init)
    p_init.add_argument("--company", required=True)
    p_init.add_argument("--visibility", required=True,
                        choices=["private", "public"])
    p_init.add_argument("--platform", required=True,
                        choices=["codex", "claude_code"])
    p_init.add_argument("--as-of", required=True)
    p_init.add_argument("--repo-root", type=Path, default=Path.cwd())
    p_init.add_argument("--codes", nargs="*", default=[])
    p_init.add_argument("--listing-status", choices=["listed", "unlisted"],
                        default=None)
    p_init.add_argument("--mode", choices=["full", "resume"], default="full")
    p_init.add_argument("--run-id", default=None)
    p_init.add_argument("--path-enforcement-level",
                        choices=["MONITORED", "SANDBOXED"], default=None)
    p_init.add_argument("--recover-stale", default=None, metavar="RUN_ID")
    p_init.set_defaults(func=cmd_init)

    p_begin = sub.add_parser("begin-skill", help="单项进入 RUNNING")
    add_registry(p_begin)
    add_run_root(p_begin)
    p_begin.add_argument("--skill", required=True)
    p_begin.add_argument("--execution-mode", default=None)
    p_begin.add_argument("--independent-context-count", type=int, default=None)
    p_begin.set_defaults(func=cmd_begin_skill)

    p_finish = sub.add_parser("finish-skill", help="单项收口 COMPLETE/BLOCKED")
    add_registry(p_finish)
    add_run_root(p_finish)
    p_finish.add_argument("--skill", required=True)
    p_finish.add_argument("--state", required=True,
                          choices=["COMPLETE", "BLOCKED"])
    p_finish.add_argument("--artifact", action="append", default=[])
    p_finish.add_argument("--evidence-file", type=Path, default=None)
    p_finish.set_defaults(func=cmd_finish_skill)

    p_ck = sub.add_parser("checkpoint", help="中间验证 COMPLETE 项产物")
    add_registry(p_ck)
    add_run_root(p_ck)
    p_ck.set_defaults(func=cmd_checkpoint)

    p_fin = sub.add_parser("finalize", help="重放计算 + 执行契约 + 机器计算最终状态")
    add_registry(p_fin)
    add_run_root(p_fin)
    p_fin.set_defaults(func=cmd_finalize)

    p_sum = sub.add_parser("summary", help="只从 gate 结果生成三轴汇总 + 矩阵")
    add_run_root(p_sum)
    p_sum.set_defaults(func=cmd_summary)

    p_ct = sub.add_parser("contracts", help="输出 20 项契约概览")
    add_registry(p_ct)
    p_ct.set_defaults(func=cmd_contracts)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
