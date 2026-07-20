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
import contextlib
import hashlib
import io
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

import report_audit

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
COMMANDS_DIR_REL = Path("evidence") / "commands"
BASELINE_REL = Path("evidence") / "audit-baseline.txt"
RESULT_REL = Path("evidence") / "04-验收器结果.json"

EVIDENCE_KEYS = ("facts", "calculations", "judgments", "role_runs",
                 "command_receipts", "artifact_records", "limitations", "audit")
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


def artifact_id_for(skill_name, artifact_path):
    material = f"{skill_name}\0{artifact_path}".encode("utf-8")
    return "artifact-" + hashlib.sha256(material).hexdigest()[:20]


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


def atomic_write_bytes(path, data):
    """同目录原子写 bytes，供命令原始输出冻结。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f"{path.name}.tmp-{os.getpid()}"
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def run_git(repo_root, *args):
    return subprocess.run(["git", "-C", str(repo_root), *args],
                          capture_output=True, text=True)


def is_git_workspace(repo_root):
    """Git 可用且 repo_root 位于工作树时返回 True；Git 不是运行前提。"""
    try:
        cp = run_git(repo_root, "rev-parse", "--is-inside-work-tree")
    except OSError:
        return False
    return cp.returncode == 0 and cp.stdout.strip() == "true"


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


def _dir_children_matching(watch_path, child_regex):
    """watch_path 目录下直接子项中匹配 child_regex 的名字 (排序)。

    child_regex 针对相对 watch_path 的子路径 (本注册表下参数化尾段恒为单层
    文件名)。目录不存在/正则非法均返回 []。用于参数化项"新增匹配文件"侦测,
    取代对目录自身 mtime 的比对 (后者会被无关活动误触发)。
    """
    try:
        pat = re.compile(child_regex)
        names = os.listdir(watch_path)
    except (OSError, re.error):
        return []
    return sorted(n for n in names if pat.match(n))


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
            entry = {
                "skill": sk["name"],
                "index": sk["index"],
                "pattern": pattern,
            }
            if unknown:
                # prefix = 占位符之前的稳定目录段; watch = 该目录 (占位符在首
                # 段时退化到 base = HOME/repo 根)。剩余尾段含占位符 → 编成子
                # 路径正则, 仅当 watch 目录下"新增匹配该正则的文件"才算越界
                # (§8.3)。不用目录自身 mtime 判变 —— 活机器上 HOME/reports 被
                # 缓存、会话文件、INDEX 重建等无关活动持续改写, mtime 必误报。
                prefix = []
                for seg in rel.split("/"):
                    if "{" in seg:
                        break
                    prefix.append(seg)
                watch = base.joinpath(*prefix) if prefix else base
                tail = "/".join(rel.split("/")[len(prefix):])
                child_regex = re.escape(tail)
                for ph in ("period", "industry"):
                    child_regex = child_regex.replace(
                        re.escape("{%s}" % ph), ".+?")
                child_regex = "^" + child_regex + "$"
                entry.update({
                    "kind": "parameterized",
                    "regex": child_regex,
                    "child_regex": child_regex,
                    "watch_path": str(watch),
                    "snapshot": snapshot_path(watch),
                    "baseline_matches": _dir_children_matching(
                        watch, child_regex),
                })
            else:
                watch = base / rel
                entry.update({
                    "kind": "exact",
                    "regex": None,
                    "watch_path": str(watch),
                    "snapshot": snapshot_path(watch),
                })
            entries.append(entry)
    return entries


def watchlist_changes(watchlist):
    changes = []
    for entry in watchlist:
        if entry.get("kind") == "parameterized" and entry.get("child_regex"):
            baseline = set(entry.get("baseline_matches", []))
            current = set(_dir_children_matching(
                entry["watch_path"], entry["child_regex"]))
            new = sorted(current - baseline)
            if new:
                changes.append(
                    f"watchlist 候选新增匹配输出 [{entry['skill']}] "
                    f"pattern={entry['pattern']!r} dir={entry['watch_path']} "
                    f"新增={new}")
        else:
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


def repo_root_for_run(manifest):
    rel = PurePosixPath(manifest["run"]["run_root"])
    return Path(manifest["run"]["root_real"]).parents[len(rel.parts) - 1]


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
MARKDOWN_HEADING_RE = re.compile(
    r"^#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*$")
MARKDOWN_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
MARKDOWN_CONTAINER_RE = re.compile(
    r"^ {0,3}(?:>\s?|(?:[-+*]|\d+[.)])(?:[ \t]+|$))")
HTML_BLOCK_OPEN_RE = re.compile(
    r"^\s{0,3}</?([A-Za-z][A-Za-z0-9-]*)(?:\s|>|/>)")
HTML_RAW_TAG_RE = re.compile(
    r"^\s{0,3}<(script|pre|style|textarea)(?:\s|>|$)", re.IGNORECASE)
HEADING_NUMBER_RE = re.compile(
    r"^(?:(?:\d+(?:\.\d+)*[.)、．]?)|"
    r"(?:第?[一二三四五六七八九十百]+[章节部分]))"
    r"\s*[:：.、\-—]?\s*")


def strip_markdown_container_prefix(line):
    """Remove nested blockquote/list markers for block-context detection."""
    current = line
    while True:
        match = MARKDOWN_CONTAINER_RE.match(current)
        if not match:
            return current
        current = current[match.end():]


def markdown_headings(text):
    """Return root ATX headings, excluding fenced code and HTML blocks."""
    headings = []
    fence_char = None
    fence_length = 0
    fence_container_width = 0
    in_html_comment = False
    html_special_end = None
    html_block_until_blank = False
    for line in text.splitlines():
        container_line = strip_markdown_container_prefix(line)
        if fence_char is not None:
            close_re = re.compile(
                rf"^ {{0,3}}{re.escape(fence_char)}"
                rf"{{{fence_length},}}[ \t]*$")
            raw_close_re = re.compile(
                rf"^ {{0,{fence_container_width + 3}}}"
                rf"{re.escape(fence_char)}{{{fence_length},}}[ \t]*$")
            if close_re.match(container_line) or raw_close_re.match(line):
                fence_char = None
                fence_length = 0
                fence_container_width = 0
            continue

        if html_special_end is not None:
            if html_special_end.lower() in line.lower():
                html_special_end = None
            continue

        if html_block_until_blank:
            if not container_line.strip():
                html_block_until_blank = False
            continue

        visible = ""
        remainder = line
        while remainder:
            if in_html_comment:
                end = remainder.find("-->")
                if end < 0:
                    remainder = ""
                    break
                remainder = remainder[end + 3:]
                in_html_comment = False
                continue
            start = remainder.find("<!--")
            if start < 0:
                visible += remainder
                break
            visible += remainder[:start]
            remainder = remainder[start + 4:]
            in_html_comment = True
        line = visible
        if not line.strip():
            continue

        container_line = strip_markdown_container_prefix(line)
        fence = MARKDOWN_FENCE_RE.match(container_line)
        if fence and not (fence.group(1).startswith("`")
                          and "`" in fence.group(2)):
            marker = fence.group(1)
            fence_char = marker[0]
            fence_length = len(marker)
            fence_container_width = len(line) - len(container_line)
            continue

        stripped = (container_line.lstrip(" ")
                    if len(container_line) - len(container_line.lstrip(" ")) <= 3
                    else container_line)
        special = None
        if stripped.startswith("<?"):
            special = "?>"
        elif stripped.startswith("<![CDATA["):
            special = "]]" + ">"
        elif re.match(r"<![A-Z]", stripped):
            special = ">"
        else:
            raw_tag = HTML_RAW_TAG_RE.match(container_line)
            if raw_tag:
                special = f"</{raw_tag.group(1)}>"
        if special is not None:
            start_at = container_line.find(stripped[:2])
            remainder_after_start = container_line[start_at + 2:]
            if special.lower() not in remainder_after_start.lower():
                html_special_end = special
            continue

        html_open = HTML_BLOCK_OPEN_RE.match(container_line)
        if html_open:
            html_block_until_blank = True
            continue

        match = MARKDOWN_HEADING_RE.match(line)
        if not match:
            continue
        title = match.group(1).strip()
        title = HEADING_NUMBER_RE.sub("", title, count=1).strip()
        headings.append(title)
    return headings


def heading_section_present(headings, section):
    """Accept an exact title or a title followed by a descriptive suffix."""
    suffix = re.compile(
        rf"^{re.escape(section)}(?:\s*[:：\-—]\s*.+|\s*[（(].+[）)]\s*)$")
    return any(title == section or suffix.match(title) for title in headings)


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
    headings = markdown_headings(text)
    for section in rule.get("required_heading_sections", []):
        if not heading_section_present(headings, section):
            errors.append(
                f"产物 {rel_path} 缺 required_heading_section 标题: {section!r}")
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


def git_path_fingerprint(repo_root, rel_path):
    """对 Git 可见基线路径记录可恢复比较的内容摘要。"""
    path = Path(repo_root) / rel_path
    try:
        st = os.lstat(path)
    except OSError:
        return {"exists": False, "type": None, "mode": None, "size": None,
                "sha256": None}
    mode = stat.S_IMODE(st.st_mode)
    if stat.S_ISREG(st.st_mode):
        kind = "regular"
        digest = sha256_file(path)
    elif stat.S_ISLNK(st.st_mode):
        kind = "symlink"
        digest = hashlib.sha256(
            os.readlink(path).encode("utf-8", errors="surrogateescape")).hexdigest()
    elif stat.S_ISDIR(st.st_mode):
        kind = "directory"
        digest = None
    else:
        kind = "other"
        digest = None
    return {"exists": True, "type": kind, "mode": mode, "size": st.st_size,
            "sha256": digest}


def build_git_baseline(repo_root, status_text):
    entries = []
    for record, path in parse_porcelain_v2_z(status_text):
        entries.append({
            "record": record,
            "path": path,
            "fingerprint": git_path_fingerprint(repo_root, path),
        })
    return {"schema_version": 1, "mode": "git", "entries": entries}


def build_no_git_baseline():
    return {"schema_version": 1, "mode": "none", "entries": []}


def new_skill_entry(item, repo_root):
    spec = Path(repo_root) / item["spec_source"]
    if not spec.is_file():
        raise GateExit(EXIT_USAGE, f"spec_source 不存在: {item['spec_source']}")
    assigned_paths = [rule["path"] for rule in item["artifact_rules"]]
    return {
        "index": item["index"],
        "name": item["name"],
        "spec_sha256": sha256_file(spec),
        "execution_state": "PENDING",
        "computed_status": None,
        "execution_mode": None,
        "independent_context_count": 0,
        "assigned_artifact_paths": assigned_paths,
        "assigned_artifacts": [
            {"artifact_id": artifact_id_for(item["name"], path), "path": path}
            for path in assigned_paths
        ],
        "artifacts": [],
        "artifact_records": [],
        "facts": [],
        "calculations": [],
        "judgments": [],
        "role_runs": [],
        "command_receipts": [],
        "limitations": [],
        "audit": [],
        "attempts": [],
        "violations": [],
    }


def cmd_init(args):
    repo_root = Path(args.repo_root).resolve()
    git_workspace = is_git_workspace(repo_root)
    validate_company(args.company)
    if not AS_OF_RE.match(args.as_of):
        raise GateExit(EXIT_USAGE, f"--as-of 必须为 YYYY-MM-DD: {args.as_of}")
    if args.path_enforcement_level == "SANDBOXED":
        raise GateExit(EXIT_USAGE,
                       "声明 SANDBOXED 需要 canary 收据, 本工具无法出具; "
                       "两平台一律 MONITORED (监测式)")

    registry = load_registry(args.registry)
    if args.mode == "resume":
        return _init_resume(args, repo_root, registry, git_workspace)

    run_id = f"{now_stamp()}-{args.company}"
    if args.visibility == "private":
        rel_root = Path("local") / "company" / args.company / run_id
    else:
        rel_root = Path("筛选公司") / args.company / "全量分析" / run_id
    run_root = repo_root / rel_root
    if args.visibility == "public" and rel_root.parts[0] == "local":
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

    workspace_audit_mode = "git" if git_workspace else "none"
    baseline = build_git_baseline(repo_root, git_status_text(repo_root)) \
        if git_workspace else build_no_git_baseline()
    with open(run_root / BASELINE_REL, "w", encoding="utf-8",
              errors="strict") as f:
        json.dump(baseline, f, ensure_ascii=True, indent=2)
        f.write("\n")
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
            "workspace_audit_mode": workspace_audit_mode,
            "completion_status": None,
            "validation_result": None,
            "assurance_level": "SINGLE_CONTEXT",
            "review_mode": "self_review",
            "capabilities": {
                "tushare_configured": bool(os.environ.get("TUSHARE_TOKEN")),
            },
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
        "workspace_audit_mode": workspace_audit_mode,
    }, ensure_ascii=False))
    return EXIT_OK


def _init_resume(args, repo_root, registry, git_workspace):
    if not args.run_id:
        raise GateExit(EXIT_USAGE, "--mode resume 必须提供 --run-id")
    if args.visibility == "private":
        rel_root = Path("local") / "company" / args.company / args.run_id
    else:
        rel_root = Path("筛选公司") / args.company / "全量分析" / args.run_id
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

    if run.get("workspace_audit_mode", "git") == "git" \
            and not git_workspace:
        run["workspace_audit_mode"] = "none"
        atomic_write_json(run_root / BASELINE_REL, build_no_git_baseline())

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


def command_operations(item):
    operations = set()
    for rule in item.get("evidence_rules", []):
        if not isinstance(rule, dict):
            continue
        if rule.get("kind") == "required_command_operations":
            operations.update(rule.get("values", []))
        elif rule.get("kind") == "conditional_command_operations":
            operations.update(
                entry.get("op") for entry in rule.get("values", [])
                if isinstance(entry, dict) and entry.get("op")
            )
    return operations


def cmd_run_ashare_command(args):
    """由 gate 实际执行 allowlisted ashare 命令并冻结输出收据。"""
    run_root = Path(args.run_root)
    manifest = load_manifest(run_root)
    _require_working(manifest, "run-ashare-command")
    registry = load_registry(args.registry)
    check_registry_matches_manifest(args.registry, manifest)
    sk = find_skill(manifest, "ashare-data")
    item = find_registry_item(registry, "ashare-data")
    enforce_watchlist_or_block(manifest, run_root, sk)
    if sk["execution_state"] != "RUNNING":
        raise GateExit(EXIT_USAGE,
                       "run-ashare-command 仅允许 ashare-data 为 RUNNING 时执行")
    allowed = command_operations(item)
    if args.operation not in allowed:
        raise GateExit(EXIT_USAGE,
                       f"operation 未登记于 ashare-data 契约: {args.operation}")
    company_codes = manifest.get("company", {}).get("codes", [])
    if not company_codes:
        raise GateExit(EXIT_USAGE,
                       "run-ashare-command 要求 init --codes 冻结证券代码")
    normalized = args.code.upper()
    known_codes = {str(code).upper() for code in company_codes}
    if normalized not in known_codes:
        raise GateExit(EXIT_USAGE,
                       f"命令代码不在 manifest company.codes: {args.code}")

    repo_root = repo_root_for_run(manifest)
    script = (repo_root / "tools" / "ashare_data.py").resolve()
    if not script.is_file():
        raise GateExit(EXIT_USAGE, f"ashare CLI 不存在: {script}")
    argv = [sys.executable, str(script), args.operation, args.code]
    started = now_iso()
    try:
        cp = subprocess.run(argv, cwd=repo_root, capture_output=True,
                            timeout=args.timeout)
        stdout, stderr, exit_code = cp.stdout, cp.stderr, cp.returncode
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or b""
        stderr = exc.stderr or b""
        exit_code = 124
    finished = now_iso()

    command_id = f"ashare-{len(sk.get('command_receipts', [])) + 1:03d}-{args.operation}"
    stdout_rel = COMMANDS_DIR_REL / f"{command_id}.stdout.txt"
    stderr_rel = COMMANDS_DIR_REL / f"{command_id}.stderr.txt"
    atomic_write_bytes(run_root / stdout_rel, stdout)
    atomic_write_bytes(run_root / stderr_rel, stderr)
    receipt = {
        "command_id": command_id,
        "operation": args.operation,
        "argv": argv,
        "exit_code": exit_code,
        "started_at": started,
        "finished_at": finished,
        "sources": list(dict.fromkeys(args.source)),
        "warnings": [] if exit_code == 0 else ["command_failed"],
        "receipt_origin": "gate",
        "stdout_path": stdout_rel.as_posix(),
        "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
        "stderr_path": stderr_rel.as_posix(),
        "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
    }
    sk["command_receipts"].append(receipt)
    save_manifest(run_root, manifest)
    print(json.dumps(receipt, ensure_ascii=False))
    return EXIT_OK if exit_code == 0 else EXIT_FAIL


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
    fact_required = {"fact_id", "field", "subject", "period", "unit",
                     "value", "tolerance_pct", "sources"}
    fact_allowed = fact_required | {"data_domain"}
    source_required = {"publisher_id", "acquisition_chain_id", "source_type",
                       "observed_value", "accessed_at"}
    source_allowed = source_required | {"document_id", "url", "subject",
                                        "period", "unit", "precedence_status"}
    seen_fact_ids = set()
    for i, fact in enumerate(payload.get("facts", [])):
        if not isinstance(fact, dict) \
                or not fact_required.issubset(fact) \
                or not set(fact).issubset(fact_allowed):
            raise GateExit(EXIT_USAGE,
                           f"facts[{i}] 字段不符合封闭 schema")
        if "data_domain" in fact \
                and fact["data_domain"] != "market_structured":
            raise GateExit(EXIT_USAGE,
                           f"facts[{i}].data_domain 仅允许 market_structured")
        for key in ("fact_id", "field", "subject", "period", "unit"):
            if not isinstance(fact[key], str) or not fact[key].strip():
                raise GateExit(EXIT_USAGE, f"facts[{i}].{key} 必须为非空字符串")
        if fact["fact_id"] in seen_fact_ids:
            raise GateExit(EXIT_USAGE, f"facts[{i}].fact_id 重复: {fact['fact_id']}")
        seen_fact_ids.add(fact["fact_id"])
        if not _decimal_string(fact["value"]) \
                or not _decimal_string(fact["tolerance_pct"]):
            raise GateExit(EXIT_USAGE,
                           f"facts[{i}] value/tolerance_pct 必须为有限十进制字符串")
        if not isinstance(fact["sources"], list):
            raise GateExit(EXIT_USAGE, f"facts[{i}].sources 必须为数组")
        has_tushare_market_source = False
        for j, source in enumerate(fact["sources"]):
            if not isinstance(source, dict) \
                    or not source_required.issubset(source) \
                    or not set(source).issubset(source_allowed):
                raise GateExit(EXIT_USAGE,
                               f"facts[{i}].sources[{j}] 字段不符合封闭 schema")
            for key in source_required - {"observed_value",
                                           "acquisition_chain_id"}:
                if not isinstance(source[key], str) or not source[key].strip():
                    raise GateExit(EXIT_USAGE,
                                   f"facts[{i}].sources[{j}].{key} 必须为非空字符串")
            if not _decimal_string(source["observed_value"]):
                raise GateExit(EXIT_USAGE,
                               f"facts[{i}].sources[{j}].observed_value "
                               "必须为有限十进制字符串")
            if not source.get("document_id") and not source.get("url"):
                raise GateExit(EXIT_USAGE,
                               f"facts[{i}].sources[{j}] 必须含 document_id 或 url")
            if source.get("precedence_status"):
                if source["precedence_status"] != "superseded_by_tushare" \
                        or fact.get("data_domain") != "market_structured" \
                        or source.get("source_type") != "market_data":
                    raise GateExit(
                        EXIT_USAGE,
                        f"facts[{i}].sources[{j}].precedence_status 不适用",
                    )
            if source.get("publisher_id") == "Tushare" \
                    and source.get("acquisition_chain_id") == "tushare-api" \
                    and source.get("source_type") == "market_data" \
                    and not source.get("precedence_status"):
                has_tushare_market_source = True
        if any(src.get("precedence_status") == "superseded_by_tushare"
               for src in fact["sources"]) and not has_tushare_market_source:
            raise GateExit(EXIT_USAGE,
                           f"facts[{i}] 缺少可采用的 Tushare 市场来源")

    artifact_record_required = {
        "artifact_id", "artifact_path", "input_artifact_ids", "fact_ids",
        "command_ids",
    }
    seen_artifact_ids = set()
    for i, record in enumerate(payload.get("artifact_records", [])):
        if not isinstance(record, dict) or set(record) != artifact_record_required:
            raise GateExit(EXIT_USAGE,
                           f"artifact_records[{i}] 必须且只能含 "
                           f"{sorted(artifact_record_required)}")
        for key in ("artifact_id", "artifact_path"):
            if not isinstance(record[key], str) or not record[key].strip():
                raise GateExit(EXIT_USAGE,
                               f"artifact_records[{i}].{key} 必须为非空字符串")
        if record["artifact_id"] in seen_artifact_ids:
            raise GateExit(EXIT_USAGE,
                           f"artifact_records[{i}].artifact_id 重复")
        seen_artifact_ids.add(record["artifact_id"])
        for key in ("input_artifact_ids", "fact_ids", "command_ids"):
            values = record[key]
            if not isinstance(values, list) or not all(
                    isinstance(value, str) and value for value in values) \
                    or len(values) != len(set(values)):
                raise GateExit(EXIT_USAGE,
                               f"artifact_records[{i}].{key} "
                               "必须为唯一非空字符串数组")

    for i, calc in enumerate(payload.get("calculations", [])):
        if not isinstance(calc, dict) or set(calc) != {
                "calculation_id", "type", "args", "expected"}:
            raise GateExit(EXIT_USAGE,
                           f"calculations[{i}] 必须且只能含 "
                           "calculation_id/type/args/expected")
        if not isinstance(calc["calculation_id"], str) \
                or not calc["calculation_id"].strip() \
                or not isinstance(calc["type"], str) \
                or not isinstance(calc["args"], dict) \
                or not isinstance(calc["expected"], dict):
            raise GateExit(EXIT_USAGE, f"calculations[{i}] 字段类型非法")

    judgment_required = {
        "judgment_id", "rule_id", "conclusion", "confidence",
        "falsification_condition", "fact_ids", "calculation_ids",
        "artifact_sections",
    }
    for i, judgment in enumerate(payload.get("judgments", [])):
        if not isinstance(judgment, dict) or set(judgment) != judgment_required:
            raise GateExit(EXIT_USAGE,
                           f"judgments[{i}] 必须且只能含 "
                           f"{sorted(judgment_required)}")
        for key in ("judgment_id", "rule_id", "conclusion"):
            if not isinstance(judgment[key], str) or not judgment[key].strip():
                raise GateExit(EXIT_USAGE,
                               f"judgments[{i}].{key} 必须为非空字符串")
        if not isinstance(judgment["falsification_condition"], str):
            raise GateExit(EXIT_USAGE,
                           f"judgments[{i}].falsification_condition 必须为字符串")
        if judgment["confidence"] not in {"low", "medium", "high"}:
            raise GateExit(EXIT_USAGE,
                           f"judgments[{i}].confidence 必须为 low/medium/high")
        for key in ("fact_ids", "calculation_ids", "artifact_sections"):
            if not isinstance(judgment[key], list) or not all(
                    isinstance(x, str) and x for x in judgment[key]):
                raise GateExit(EXIT_USAGE,
                               f"judgments[{i}].{key} 必须为字符串数组")
        if not judgment["artifact_sections"]:
            raise GateExit(EXIT_USAGE,
                           f"judgments[{i}].artifact_sections 不得为空")

    role_required = {"role", "context_id", "execution_mode", "artifact_paths",
                     "started_at", "finished_at"}
    for i, role in enumerate(payload.get("role_runs", [])):
        if not isinstance(role, dict) or set(role) != role_required:
            raise GateExit(EXIT_USAGE,
                           f"role_runs[{i}] 必须且只能含 {sorted(role_required)}")
        for key in ("role", "context_id", "execution_mode", "started_at",
                    "finished_at"):
            if not isinstance(role[key], str) or not role[key].strip():
                raise GateExit(EXIT_USAGE,
                               f"role_runs[{i}].{key} 必须为非空字符串")
        if not isinstance(role["artifact_paths"], list) \
                or not role["artifact_paths"] \
                or not all(isinstance(x, str) and x
                           for x in role["artifact_paths"]):
            raise GateExit(EXIT_USAGE,
                           f"role_runs[{i}].artifact_paths 必须为非空字符串数组")

    for i, limitation in enumerate(payload.get("limitations", [])):
        if not isinstance(limitation, dict) or not isinstance(
                limitation.get("code"), str):
            raise GateExit(EXIT_USAGE, f"limitations[{i}] 必须含字符串 code")
        if limitation["code"] == "not_applicable":
            required = {"code", "predicate_id", "input_facts", "alternative"}
            if set(limitation) != required:
                raise GateExit(EXIT_USAGE,
                               f"limitations[{i}] not_applicable 必须且只能含 "
                               f"{sorted(required)}")
            if not isinstance(limitation["predicate_id"], str) \
                    or not isinstance(limitation["input_facts"], list) \
                    or not all(isinstance(x, str) and x
                               for x in limitation["input_facts"]) \
                    or limitation["alternative"] is not None \
                    and not isinstance(limitation["alternative"], str):
                raise GateExit(EXIT_USAGE,
                               f"limitations[{i}] not_applicable 字段类型非法")
        elif set(limitation) != {"code", "note"} \
                or not isinstance(limitation.get("note"), str):
            raise GateExit(EXIT_USAGE,
                           f"limitations[{i}] 普通限制必须且只能含 code/note")

    command_required = {"command_id", "operation", "argv", "exit_code",
                        "started_at", "finished_at", "sources", "warnings"}
    for i, receipt in enumerate(payload.get("command_receipts", [])):
        if not isinstance(receipt, dict) or set(receipt) != command_required:
            raise GateExit(EXIT_USAGE,
                           f"command_receipts[{i}] 必须且只能含 "
                           f"{sorted(command_required)}")
        for key in ("command_id", "operation", "started_at", "finished_at"):
            if not isinstance(receipt[key], str) or not receipt[key].strip():
                raise GateExit(EXIT_USAGE,
                               f"command_receipts[{i}].{key} 必须为非空字符串")
        if not isinstance(receipt["argv"], list) or not receipt["argv"] \
                or not all(isinstance(x, str) and x for x in receipt["argv"]):
            raise GateExit(EXIT_USAGE,
                           f"command_receipts[{i}].argv 必须为非空字符串数组")
        if not isinstance(receipt["exit_code"], int) \
                or isinstance(receipt["exit_code"], bool):
            raise GateExit(EXIT_USAGE,
                           f"command_receipts[{i}].exit_code 必须为 int")
        for key in ("sources", "warnings"):
            if not isinstance(receipt[key], list) or not all(
                    isinstance(x, str) for x in receipt[key]):
                raise GateExit(EXIT_USAGE,
                               f"command_receipts[{i}].{key} 必须为字符串数组")

    audit_required = {"artifact", "ratio", "seed", "results"}
    audit_result_required = {"id", "fetched_value", "fetched_source",
                             "fetched_value2", "fetched_source2"}
    for i, record in enumerate(payload.get("audit", [])):
        if not isinstance(record, dict) or set(record) != audit_required:
            raise GateExit(EXIT_USAGE,
                           f"audit[{i}] 必须且只能含 {sorted(audit_required)}; "
                           "verdict/sample_count 只能由 gate 重算")
        if not isinstance(record["artifact"], str) \
                or not isinstance(record["ratio"], (int, float)) \
                or isinstance(record["ratio"], bool) \
                or not 0 < record["ratio"] <= 1 \
                or not isinstance(record["seed"], int) \
                or isinstance(record["seed"], bool) \
                or not isinstance(record["results"], list):
            raise GateExit(EXIT_USAGE, f"audit[{i}] 字段类型/范围非法")
        for j, result in enumerate(record["results"]):
            if not isinstance(result, dict) or set(result) != audit_result_required:
                raise GateExit(EXIT_USAGE,
                               f"audit[{i}].results[{j}] 字段不符合封闭 schema")
            if not isinstance(result["id"], int) \
                    or isinstance(result["id"], bool):
                raise GateExit(EXIT_USAGE,
                               f"audit[{i}].results[{j}].id 必须为 int")
            for key in ("fetched_value", "fetched_value2"):
                if result[key] is not None and not _decimal_string(result[key]):
                    raise GateExit(EXIT_USAGE,
                                   f"audit[{i}].results[{j}].{key} "
                                   "必须为十进制字符串或 null")
            for key in ("fetched_source", "fetched_source2"):
                if not isinstance(result[key], str):
                    raise GateExit(EXIT_USAGE,
                                   f"audit[{i}].results[{j}].{key} 必须为字符串")


def cmd_finish_skill(args):
    run_root = Path(args.run_root)
    manifest = load_manifest(run_root)
    registry = load_registry(args.registry)
    check_registry_matches_manifest(args.registry, manifest)
    sk = find_skill(manifest, args.skill)
    item = find_registry_item(registry, args.skill)
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
    if payload.get("command_receipts") and item.get("name") == "ashare-data":
        raise GateExit(
            EXIT_USAGE,
            "含命令契约的 skill 不接受调用者提交 command_receipts; "
            "使用 run-ashare-command 由 gate 实际执行并记录",
        )
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
# 运行上下文：review mode / industry 均只能经 gate 写入
# ---------------------------------------------------------------------------
def _require_working(manifest, command):
    if manifest.get("run", {}).get("phase") != "WORKING":
        raise GateExit(EXIT_USAGE,
                       f"{command} 仅允许 WORKING 运行, 当前 "
                       f"{manifest.get('run', {}).get('phase')}")


def cmd_set_review_mode(args):
    run_root = Path(args.run_root)
    manifest = load_manifest(run_root)
    _require_working(manifest, "set-review-mode")
    if args.mode == "independent_context":
        max_contexts = max(
            (sk.get("independent_context_count", 0)
             for sk in manifest.get("skills", [])), default=0)
        if max_contexts < 2:
            raise GateExit(
                EXIT_FAIL,
                "review_mode=independent_context 需要至少一项已记录 "
                f"independent_context_count>=2, 实际最大 {max_contexts}")
    manifest["run"]["review_mode"] = args.mode
    save_manifest(run_root, manifest)
    print(f"set-review-mode: {args.mode}")
    return EXIT_OK


def cmd_set_industry(args):
    run_root = Path(args.run_root)
    manifest = load_manifest(run_root)
    _require_working(manifest, "set-industry")
    payload = load_json_or_exit(args.industry_file, "industry-file")
    required = {"basis", "period", "source_fact_ids", "segments"}
    if not isinstance(payload, dict) or set(payload) != required:
        raise GateExit(EXIT_USAGE,
                       f"industry-file 必须且只能含 {sorted(required)}")
    if payload["basis"] != "latest_fy_revenue_or_operating_income":
        raise GateExit(EXIT_USAGE,
                       "industry-file basis 必须为 "
                       "latest_fy_revenue_or_operating_income")
    if not isinstance(payload["period"], str) or not payload["period"].strip():
        raise GateExit(EXIT_USAGE, "industry-file period 必须为非空字符串")
    source_ids = payload["source_fact_ids"]
    if not isinstance(source_ids, list) or not source_ids \
            or len(set(source_ids)) != len(source_ids) \
            or not all(isinstance(x, str) and x for x in source_ids):
        raise GateExit(EXIT_USAGE,
                       "industry-file source_fact_ids 必须为非空唯一字符串数组")
    known_ids = {fact.get("fact_id") for fact in _all_facts(manifest)}
    dangling = [fact_id for fact_id in source_ids if fact_id not in known_ids]
    if dangling:
        raise GateExit(EXIT_FAIL,
                       f"industry source_fact_ids 存在悬空 fact ID: {dangling}")

    segments = payload["segments"]
    if not isinstance(segments, list) or not segments:
        raise GateExit(EXIT_USAGE, "industry-file segments 必须为非空数组")
    parsed = []
    labels = set()
    for i, segment in enumerate(segments):
        if not isinstance(segment, dict) or set(segment) != {
                "label", "revenue_share_pct"}:
            raise GateExit(EXIT_USAGE,
                           f"segments[{i}] 必须且只能含 label/revenue_share_pct")
        label = segment["label"]
        share = _dec(segment["revenue_share_pct"])
        if not isinstance(label, str) or not label.strip() or label in labels:
            raise GateExit(EXIT_USAGE,
                           f"segments[{i}].label 必须为非空且不重复")
        if not isinstance(segment["revenue_share_pct"], str) \
                or share is None or not share.is_finite() \
                or share <= 0 or share > 100:
            raise GateExit(EXIT_USAGE,
                           f"segments[{i}].revenue_share_pct "
                           "必须为 (0,100] 的十进制字符串")
        labels.add(label)
        parsed.append((label, share))
    total = sum((share for _label, share in parsed), Decimal("0"))
    if not Decimal("99") <= total <= Decimal("101"):
        raise GateExit(EXIT_FAIL,
                       f"industry 分部占比合计须在 99%-101%, 实际 {total}%")
    parsed.sort(key=lambda row: (-row[1], row[0]))
    if parsed[0][1] >= Decimal("50"):
        scope_type = "primary"
        selected = [parsed[0][0]]
    else:
        scope_type = "multi_segment"
        selected = []
        cumulative = Decimal("0")
        for label, share in parsed:
            selected.append(label)
            cumulative += share
            if cumulative >= Decimal("80"):
                break
        if cumulative < Decimal("80"):
            raise GateExit(EXIT_FAIL,
                           "industry multi_segment 无法覆盖累计 80%")
    manifest["company"]["industry"] = {
        "scope_type": scope_type,
        "labels": selected,
        "basis": payload["basis"],
        "period": payload["period"],
        "source_fact_ids": source_ids,
    }
    save_manifest(run_root, manifest)
    print(json.dumps(manifest["company"]["industry"], ensure_ascii=False))
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
        na, na_errors = evaluate_not_applicable(
            sk, item, registry, run_root, manifest)
        if na is not None:
            for err in na_errors:
                problems.append(f"[{sk['name']}] {err}")
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
    if any(not isinstance(fact.get(key), str) or not fact.get(key).strip()
           for key in ("subject", "period", "unit")):
        return "UNAVAILABLE"
    value = _dec(fact.get("value"))
    tol = _dec(fact.get("tolerance_pct"))
    comparable = []
    for src in fact.get("sources", []):
        if not isinstance(src, dict):
            continue
        if fact.get("data_domain") == "market_structured" \
                and src.get("precedence_status") == "superseded_by_tushare":
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
    "min_command_receipts", "required_fact_fields",
    "required_judgment_rule_ids", "required_command_operations",
    "conditional_command_operations",
}


def validate_gate_command_receipt(receipt, manifest, run_root):
    required = {
        "command_id", "operation", "argv", "exit_code", "started_at",
        "finished_at", "sources", "warnings", "receipt_origin",
        "stdout_path", "stdout_sha256", "stderr_path", "stderr_sha256",
    }
    if not isinstance(receipt, dict) or set(receipt) != required:
        return ["gate 命令收据字段不符合封闭 schema"]
    errors = []
    if receipt.get("receipt_origin") != "gate":
        errors.append("命令收据 receipt_origin 必须为 gate")
    argv = receipt.get("argv")
    repo_root = repo_root_for_run(manifest)
    expected_script = (repo_root / "tools" / "ashare_data.py").resolve()
    if not isinstance(argv, list) or len(argv) != 4:
        errors.append("gate 命令收据 argv 必须为 python/script/operation/code")
    else:
        if not Path(argv[0]).name.startswith("python"):
            errors.append("gate 命令收据 argv[0] 不是 Python 解释器")
        try:
            actual_script = Path(argv[1]).resolve(strict=True)
        except OSError:
            actual_script = None
        if actual_script != expected_script:
            errors.append("gate 命令收据 argv[1] 不是当前仓库 ashare_data.py")
        if argv[2] != receipt.get("operation"):
            errors.append("gate 命令收据 operation 与 argv 不一致")
        known_codes = {str(code).upper() for code in
                       manifest.get("company", {}).get("codes", [])}
        if str(argv[3]).upper() not in known_codes:
            errors.append("gate 命令收据 code 不在 manifest company.codes")
    if not isinstance(receipt.get("sources"), list) or not receipt.get("sources"):
        errors.append("gate 命令收据 sources 不得为空")
    for key in ("started_at", "finished_at"):
        try:
            datetime.fromisoformat(receipt[key])
        except (KeyError, TypeError, ValueError):
            errors.append(f"gate 命令收据 {key} 不是 ISO 时间")
    try:
        if datetime.fromisoformat(receipt["finished_at"]) < \
                datetime.fromisoformat(receipt["started_at"]):
            errors.append("gate 命令收据 finished_at 早于 started_at")
    except (KeyError, TypeError, ValueError):
        pass
    for stream in ("stdout", "stderr"):
        rel_value = receipt.get(f"{stream}_path")
        try:
            rel = PurePosixPath(rel_value)
        except TypeError:
            errors.append(f"gate 命令收据 {stream}_path 非法")
            continue
        if rel.is_absolute() or ".." in rel.parts \
                or rel.parts[:2] != ("evidence", "commands"):
            errors.append(f"gate 命令收据 {stream}_path 越界")
            continue
        path = Path(run_root) / rel
        if not path.is_file():
            errors.append(f"gate 命令收据 {stream} 文件不存在: {rel}")
            continue
        actual_hash = sha256_file(path)
        if actual_hash != receipt.get(f"{stream}_sha256"):
            errors.append(f"gate 命令收据 {stream} SHA-256 不匹配")
        if stream == "stdout" and receipt.get("exit_code") == 0 \
                and path.stat().st_size == 0:
            errors.append("成功命令的 stdout 不得为空")
    return errors


def evaluate_evidence_rules(sk, item, manifest, run_root):
    """Phase 2 (§6.4/§15.3): 领域证据结构性存在检查, 不判内容对错 (§2.2)。

    须在 facts 分类之后调用 (依赖 fact.computed_status)。返回错误列表。
    """
    errors = []
    facts = [f for f in sk.get("facts", []) if isinstance(f, dict)]
    calcs = [c for c in sk.get("calculations", []) if isinstance(c, dict)]
    judgments = [j for j in sk.get("judgments", []) if isinstance(j, dict)]
    role_runs = [r for r in sk.get("role_runs", []) if isinstance(r, dict)]
    receipts = [r for r in sk.get("command_receipts", [])
                if isinstance(r, dict)]
    if item.get("name") == "ashare-data":
        for receipt in receipts:
            errors.extend(validate_gate_command_receipt(
                receipt, manifest, run_root))
    successful_receipts = [receipt for receipt in receipts
                           if receipt.get("exit_code") == 0]
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
        elif kind == "min_command_receipts" and len(successful_receipts) < n:
            errors.append(f"领域证据不足: 需 ≥{n} 条成功命令执行收据, "
                          f"实际 {len(successful_receipts)}")
        elif kind == "required_fact_fields":
            present = {fact.get("field") for fact in facts}
            missing = [value for value in rule.get("values", [])
                       if value not in present]
            if missing:
                errors.append(f"领域证据缺 required fact fields: {missing}")
        elif kind == "required_judgment_rule_ids":
            present = {judgment.get("rule_id") for judgment in judgments}
            missing = [value for value in rule.get("values", [])
                       if value not in present]
            if missing:
                errors.append(f"领域证据缺 required judgment rule IDs: {missing}")
        elif kind == "required_command_operations":
            present = {receipt.get("operation") for receipt in successful_receipts}
            missing = [value for value in rule.get("values", [])
                       if value not in present]
            if missing:
                errors.append("领域证据缺成功的 required command operations "
                              f"(exit_code 必须为 0): {missing}")
        elif kind == "conditional_command_operations":
            capability = rule.get("capability")
            configured = bool(manifest.get("run", {}).get("capabilities", {})
                              .get(capability))
            required_ops = [entry.get("op") for entry in rule.get("values", [])
                            if isinstance(entry, dict) and entry.get("op")]
            if configured:
                by_operation = {}
                for receipt in receipts:
                    by_operation.setdefault(receipt.get("operation"), []).append(receipt)
                missing = [op for op in required_ops if op not in by_operation]
                failed = [op for op in required_ops
                          if op in by_operation and not any(
                              receipt.get("exit_code") == 0
                              for receipt in by_operation[op])]
                if missing:
                    errors.append(f"条件命令缺失 ({capability}=true): {missing}")
                if failed:
                    errors.append(f"条件命令 exit_code 非零 ({capability}=true): "
                                  f"{failed}")
            else:
                limitation_codes = {
                    limitation.get("code") for limitation in sk.get("limitations", [])
                    if isinstance(limitation, dict)
                }
                if "tushare_not_configured" not in limitation_codes:
                    errors.append("条件命令未配置时必须记录 limitation: "
                                  "tushare_not_configured")
    return errors


def load_calc_schema():
    return load_json_or_exit(RESULT_SCHEMA_PATH, "financial_rigor 结果 schema")


def _decimal_string(value):
    if not isinstance(value, str):
        return False
    parsed = _dec(value)
    return parsed is not None and parsed.is_finite()


def _validate_issue_items(items, field):
    errors = []
    if not isinstance(items, list):
        return [f"{field} 必须为数组"]
    for i, item in enumerate(items):
        if not isinstance(item, dict) or set(item) != {"code", "message"}:
            errors.append(f"{field}[{i}] 必须且只能含 code/message")
        elif not all(isinstance(item[k], str) for k in ("code", "message")):
            errors.append(f"{field}[{i}].code/message 必须为字符串")
    return errors


def _validate_operation_result(operation, result, spec):
    errors = []
    if not isinstance(result, dict):
        return [f"{operation} result 必须为 object"]
    required = set(spec.get("result_required", []))
    if set(result) != required:
        errors.append(f"{operation} result 字段必须逐项等于 schema: "
                      f"期望 {sorted(required)} 实际 {sorted(result)}")
        return errors
    for field in spec.get("decimal_string_fields", []):
        value = result.get(field)
        if value is not None and not _decimal_string(value):
            errors.append(f"{operation} result.{field} 必须为有限十进制字符串或 null")
    for field, values in spec.get("enums", {}).items():
        if result.get(field) not in values:
            errors.append(f"{operation} result.{field} 不在枚举 {values}")

    if operation == "verify-valuation":
        metrics = result.get("metrics")
        allowed = set(spec.get("metrics_allowed", []))
        if not isinstance(metrics, dict) or not set(metrics).issubset(allowed):
            errors.append("verify-valuation result.metrics 含非法字段")
        elif any(not _decimal_string(v) for v in metrics.values()):
            errors.append("verify-valuation result.metrics 值必须为有限十进制字符串")
        skipped = result.get("skipped")
        expected = set(spec.get("skipped_item_fields", []))
        if not isinstance(skipped, list) or any(
                not isinstance(x, dict) or set(x) != expected for x in skipped):
            errors.append("verify-valuation result.skipped 字段不符合 schema")
    elif operation == "cross-validate":
        if not isinstance(result.get("all_consistent"), bool):
            errors.append("cross-validate result.all_consistent 必须为 bool")
        source_fields = set(spec.get("source_item_fields", []))
        sources = result.get("sources")
        if not isinstance(sources, list) or any(
                not isinstance(x, dict) or set(x) != source_fields for x in sources):
            errors.append("cross-validate result.sources 字段不符合 schema")
        else:
            for i, source in enumerate(sources):
                for field in spec.get("source_decimal_string_fields", []):
                    if not _decimal_string(source.get(field)):
                        errors.append(f"cross-validate sources[{i}].{field} "
                                      "必须为有限十进制字符串")
                if not isinstance(source.get("within_tolerance"), bool):
                    errors.append(f"cross-validate sources[{i}].within_tolerance "
                                  "必须为 bool")
    elif operation == "benford":
        if not isinstance(result.get("sample_size"), int) \
                or isinstance(result.get("sample_size"), bool):
            errors.append("benford result.sample_size 必须为 int")
        conforms = result.get("is_conforming")
        if conforms is not None and not isinstance(conforms, bool):
            errors.append("benford result.is_conforming 必须为 bool 或 null")
    elif operation == "calc":
        if not isinstance(result.get("expression"), str):
            errors.append("calc result.expression 必须为字符串")
    elif operation == "three-scenario":
        if not isinstance(result.get("years"), int) \
                or isinstance(result.get("years"), bool):
            errors.append("three-scenario result.years 必须为 int")
        if not isinstance(result.get("currency"), str):
            errors.append("three-scenario result.currency 必须为字符串")
        rows = result.get("scenarios")
        fields = set(spec.get("scenario_item_fields", []))
        ids = spec.get("scenario_id_enum", [])
        if not isinstance(rows, list) or len(rows) != len(ids) or any(
                not isinstance(x, dict) or set(x) != fields for x in rows):
            errors.append("three-scenario result.scenarios 字段不符合 schema")
        else:
            if [row.get("id") for row in rows] != ids:
                errors.append("three-scenario scenario id/顺序不符合 schema")
            for i, row in enumerate(rows):
                for field in spec.get("scenario_decimal_string_fields", []):
                    if not _decimal_string(row.get(field)):
                        errors.append(f"three-scenario scenarios[{i}].{field} "
                                      "必须为有限十进制字符串")
    return errors


def validate_financial_envelope(env, process_exit_code, schema):
    """按冻结 schema 校验重放输出；返回错误列表。"""
    if not isinstance(env, dict):
        return ["financial_rigor envelope 必须为 object"]
    errors = []
    envelope = schema.get("envelope", {})
    required = set(envelope.get("required", []))
    if set(env) != required:
        errors.append("financial_rigor envelope 字段必须逐项等于 schema: "
                      f"期望 {sorted(required)} 实际 {sorted(env)}")
        return errors
    operation = env.get("operation")
    op_spec = schema.get("operations", {}).get(operation)
    if op_spec is None:
        errors.append(f"operation 不在 schema: {operation!r}")
        return errors
    if env.get("schema_version") != schema.get("schema_version"):
        errors.append("schema_version 与冻结 schema 不一致")
    if not isinstance(env.get("inputs"), dict):
        errors.append("inputs 必须为 object")
    if env.get("outcome") not in envelope.get("outcome_enum", []):
        errors.append(f"outcome 非法: {env.get('outcome')!r}")
    if env.get("exit_code") not in envelope.get("exit_codes", []):
        errors.append(f"exit_code 非法: {env.get('exit_code')!r}")
    if env.get("exit_code") != process_exit_code:
        errors.append(f"进程 exit {process_exit_code} 与 JSON exit_code "
                      f"{env.get('exit_code')!r} 不一致")
    if env.get("outcome") in ("PASS", "FAIL"):
        if not isinstance(env.get("is_pass"), bool):
            errors.append("PASS/FAIL 的 is_pass 必须为 bool")
    elif env.get("is_pass") is not None:
        errors.append("INSUFFICIENT/ERROR 的 is_pass 必须为 null")
    errors.extend(_validate_issue_items(env.get("warnings"), "warnings"))
    errors.extend(_validate_issue_items(env.get("errors"), "errors"))
    errors.extend(_validate_operation_result(operation, env.get("result"), op_spec))
    return errors


def _semantic_equal(want, got):
    if isinstance(want, dict) and isinstance(got, dict):
        return set(want) == set(got) and all(
            _semantic_equal(want[k], got[k]) for k in want)
    if isinstance(want, list) and isinstance(got, list):
        return len(want) == len(got) and all(
            _semantic_equal(a, b) for a, b in zip(want, got))
    want_d, got_d = _dec(want), _dec(got)
    if isinstance(want, str) and isinstance(got, str) \
            and want_d is not None and got_d is not None:
        return want_d == got_d
    return want == got


def replay_calculation(calc, schema):
    """#10: 用当前工具 --json 重放, 语义字段比较; 返回错误列表。"""
    cid = calc.get("calculation_id", "<无id>")
    ctype = calc.get("type")
    operations = schema.get("operations", {})
    if ctype not in operations:
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
    errors = [f"计算 {cid} {e}"
              for e in validate_financial_envelope(env, cp.returncode, schema)]
    expected = calc.get("expected")
    expected_keys = {"outcome", "is_pass", "exit_code", "result"}
    if not isinstance(expected, dict) or set(expected) != expected_keys:
        errors.append(f"计算 {cid} expected 必须且只能含 "
                      "outcome/is_pass/exit_code/result")
        return errors
    for e in _validate_operation_result(ctype, expected.get("result"),
                                        operations[ctype]):
        errors.append(f"计算 {cid} expected {e}")
    for key in ("outcome", "is_pass", "exit_code", "result"):
        if not _semantic_equal(expected.get(key), env.get(key)):
            errors.append(f"计算 {cid} {key} 不一致: 期望 {expected.get(key)!r} "
                          f"实际 {env.get(key)!r}")
    if env.get("outcome") != "PASS" or env.get("is_pass") is not True \
            or env.get("exit_code") != 0:
        errors.append(f"计算 {cid} 重放未得到 PASS/true/exit 0")
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


def _compute_audit_record(record, run_root):
    artifact = record["artifact"]
    try:
        text = (Path(run_root) / artifact).read_text(
            encoding="utf-8", errors="replace")
    except OSError as e:
        return None, 0, [f"审计产物不可读: {artifact}: {e}"]
    points, _stats = report_audit.extract_data_points(text)
    sampled = report_audit.sample_points(
        points, ratio=record["ratio"], seed=record["seed"])
    submitted = record["results"]
    by_id = {row["id"]: row for row in submitted}
    sample_ids = [point["id"] for point in sampled]
    if len(by_id) != len(submitted) or set(by_id) != set(sample_ids):
        return None, len(sampled), [
            f"审计 results.id 必须逐项等于 gate 抽样 ID: "
            f"期望 {sample_ids} 实际 {[row['id'] for row in submitted]}"]
    merged = []
    for point in sampled:
        merged.append({**point, **by_id[point["id"]]})
    with contextlib.redirect_stdout(io.StringIO()):
        outcome = report_audit.render_verdict(merged, report_name=artifact)
    record["computed_verdict"] = outcome["verdict"]
    record["computed_sample_count"] = outcome["total"]
    record["computed_counts"] = {
        key: outcome[key] for key in (
            "pass_count", "warn_count", "fail_count", "skipped_count",
            "single_source_count")}
    return outcome["verdict"], outcome["total"], []


def evaluate_audit(sk, item, run_root):
    """#14: required/advisory/none 三态审计策略。返回 (errors, caps)。"""
    errors, caps = [], []
    records = sk.get("audit", [])
    for rule in item.get("artifact_rules", []):
        policy = rule.get("audit_policy", "none")
        if policy == "none":
            continue
        matched = [a for a in records
                   if isinstance(a, dict) and a.get("artifact") == rule["path"]]
        computed = []
        for rec in matched:
            verdict, sample_count, audit_errors = _compute_audit_record(
                rec, run_root)
            errors.extend(audit_errors)
            computed.append((verdict, sample_count))
        if policy == "required":
            if not matched:
                errors.append(f"required 审计缺记录: {rule['path']}")
            for verdict, _count in computed:
                if verdict != "PASS":
                    errors.append(f"required 审计 computed_verdict="
                                  f"{verdict}: {rule['path']}")
        elif policy == "advisory":
            if not matched:
                caps.append(f"advisory 审计缺记录: {rule['path']}")
            for verdict, _count in computed:
                if verdict == "FAIL":
                    errors.append(f"advisory 审计 computed_verdict=FAIL: "
                                  f"{rule['path']}")
                elif verdict == "INSUFFICIENT":
                    caps.append(f"advisory 审计 INSUFFICIENT: {rule['path']}")
    return errors, caps


def _all_facts(manifest):
    return [fact for entry in manifest.get("skills", [])
            for fact in entry.get("facts", []) if isinstance(fact, dict)]


def _fact_truthy(fact):
    value = str(fact.get("value", "")).strip().lower()
    numeric = _dec(value)
    if numeric is not None:
        return numeric != 0
    return value in {"true", "yes", "present", "available"}


def evaluate_applicability(predicate_id, manifest, sk, input_facts):
    """计算注册适用性谓词；None 表示证据不足，不能准许 N/A。"""
    facts = _all_facts(manifest)
    selected = [fact for fact in facts if fact.get("fact_id") in input_facts]
    company = manifest.get("company", {})
    run = manifest.get("run", {})
    by_name = {entry.get("name"): entry for entry in manifest.get("skills", [])}
    if predicate_id == "always_applicable":
        return True
    if predicate_id == "is_a_share":
        return any(re.search(r"(^|\D)\d{6}(?:\.(?:SH|SZ))?$", str(code), re.I)
                   for code in company.get("codes", []))
    if predicate_id == "has_comparable_financial_history":
        return len({fact.get("period") for fact in selected}) >= 2
    if predicate_id == "has_investable_price":
        return any("price" in fact.get("field", "").lower()
                   and _fact_truthy(fact) for fact in selected)
    if predicate_id == "min_independent_contexts_2":
        return sk.get("independent_context_count", 0) >= 2
    if predicate_id == "identifiable_key_managers":
        return any("manager" in fact.get("field", "").lower()
                   and _fact_truthy(fact) for fact in selected)
    if predicate_id == "has_primary_filing_for_period":
        return any(source.get("source_type") in {
            "filing", "annual_report", "interim_report", "announcement"}
            for fact in selected for source in fact.get("sources", []))
    if predicate_id == "earnings_review_complete_and_min_2_contexts":
        return by_name.get("earnings-review", {}).get(
            "execution_state") == "COMPLETE" \
            and sk.get("independent_context_count", 0) >= 2
    if predicate_id == "main_business_definable":
        return company.get("industry") is not None
    if predicate_id == "listed_and_main_industry_definable":
        return company.get("listing_status") == "listed" \
            and company.get("industry") is not None
    if predicate_id == "physical_bottleneck_exists":
        return any("bottleneck" in fact.get("field", "").lower()
                   and _fact_truthy(fact) for fact in selected)
    if predicate_id == "has_two_pairable_snapshots":
        return len({fact.get("period") for fact in selected}) >= 2
    if predicate_id == "private_run_with_portfolio_input":
        return run.get("visibility") == "private" and any(
            "portfolio" in fact.get("field", "").lower()
            and _fact_truthy(fact) for fact in selected)
    if predicate_id == "is_unlisted":
        return company.get("listing_status") == "unlisted"
    if predicate_id == "core_research_passed_min_3_questions":
        base = by_name.get("investment-research", {})
        return base.get("execution_state") == "COMPLETE" \
            and len(base.get("judgments", [])) >= 3
    if predicate_id == "has_fact_base":
        return bool(selected)
    if predicate_id == "core_research_passed_draft_allowed":
        return by_name.get("investment-research", {}).get(
            "execution_state") == "COMPLETE"
    return None


def evaluate_not_applicable(sk, item, registry, run_root, manifest):
    """[b] N/A 负向验收: 谓词/输入事实/负向产物/替代路径四要素齐备才 N/A PASS。"""
    na = next((l for l in sk.get("limitations", [])
               if isinstance(l, dict) and l.get("code") == "not_applicable"),
              None)
    if na is None:
        return None, []
    errors = []
    registered = item.get("applicability_rule", {}).get("predicate_id")
    if na.get("predicate_id") != registered:
        errors.append(f"N/A 谓词与当前契约不一致: {na.get('predicate_id')!r} "
                      f"!= {registered!r}")
    if registered not in registry.get("predicates", []):
        errors.append(f"N/A 谓词不在注册表: {registered!r}")
    input_ids = na.get("input_facts")
    if not input_ids:
        errors.append("N/A 缺 input_facts (谓词输入事实)")
        input_ids = []
    known_ids = {fact.get("fact_id") for fact in _all_facts(manifest)}
    dangling = [fact_id for fact_id in input_ids if fact_id not in known_ids]
    if dangling:
        errors.append(f"N/A input_facts 存在悬空 fact ID: {dangling}")
    if not dangling and registered in registry.get("predicates", []):
        applicable = evaluate_applicability(registered, manifest, sk, input_ids)
        if applicable is None:
            errors.append(f"N/A 谓词无法由已登记事实计算: {registered}")
        elif applicable:
            errors.append(f"N/A 非法: 适用性谓词 {registered} 实际为 true")
    neg_dir = registry.get("negative_acceptance_dir", "06-负向验收")
    neg = Path(run_root) / neg_dir / f"{item['index']:02d}-{item['name']}.md"
    if not neg.is_file() or neg.stat().st_size == 0:
        errors.append(f"N/A 缺负向验收产物: {neg_dir}/{neg.name}")
    else:
        text = neg.read_text(encoding="utf-8", errors="replace")
        required_tokens = [str(registered), *input_ids]
        expect_alt = item.get("applicability_rule", {}).get("alternative")
        if expect_alt is not None:
            required_tokens.append(str(expect_alt))
        missing = [token for token in required_tokens if token not in text]
        if missing:
            errors.append(f"N/A 负向验收产物缺谓词/fact/替代路径: {missing}")
    expect_alt = item.get("applicability_rule", {}).get("alternative")
    if na.get("alternative") != expect_alt:
        errors.append(f"N/A alternative 与注册表不一致: "
                      f"{na.get('alternative')!r} != {expect_alt!r}")
    return na, errors


def evaluate_roles(sk, item, registry, run_root, manifest):
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
            na, na_errors = evaluate_not_applicable(
                sk, item, registry, run_root, manifest)
            if na is None:
                errors.append(f"独立上下文不足且未走负向验收 "
                              f"(cap={cap}, 需 N/A 收口)")
            else:
                errors.extend(na_errors)
        elif cap == "PASS_WITH_LIMITATIONS":
            caps.append(f"独立上下文不足 ({sk.get('independent_context_count')}"
                        f"/{min_ctx}), 封顶 PWL")
    return errors, caps


# 真实通用检索/外部带宽来源类型 (WebSearch / 新闻 / 卖方 / 官网 / 监管网页).
# 判断密集契约 (Layer 3-5) 靠这些来源支撑深度; ashare CLI 的 market_data/filing
# 等结构化来源不算 web 带宽 —— 只有它们退化成 CLI-only 才是本层要抓的静默降级。
WEB_SOURCE_TYPES = {"web", "news", "analyst", "official_page", "regulator_web"}


def _has_na_limitation(sk):
    """该 skill 是否以合法 not_applicable 收口 (即真 N/A, 不参与保障/带宽聚合)。"""
    return any(isinstance(lim, dict) and lim.get("code") == "not_applicable"
               for lim in sk.get("limitations", []))


def _has_web_source(sk):
    return any(
        isinstance(src, dict) and src.get("source_type") in WEB_SOURCE_TYPES
        for fact in sk.get("facts", []) if isinstance(fact, dict)
        for src in (fact.get("sources")
                    if isinstance(fact.get("sources"), list) else []))


def _web_bandwidth_degraded(sk):
    return any(
        isinstance(lim, dict) and lim.get("code") == "web_bandwidth_degraded"
        for lim in sk.get("limitations", []))


def evaluate_web_bandwidth(sk, item):
    """[h] 信息带宽: requires_web_bandwidth 的契约必须有真实 web 来源事实,
    或显式记 web_bandwidth_degraded 限制 (封顶 PWL); 二者皆无 = FAIL。"""
    if not item.get("requires_web_bandwidth"):
        return [], []
    if _has_web_source(sk):
        return [], []
    if _web_bandwidth_degraded(sk):
        return [], ["信息带宽降级 (web_bandwidth_degraded): 通用检索路径不可用"]
    return ["信息带宽缺口: 判断密集契约无 web 来源事实且未记 "
            "web_bandwidth_degraded (静默 CLI-only 不得准出)"], []


def compute_information_bandwidth(manifest, registry):
    """聚合 requires_web_bandwidth 契约的带宽轴: FULL/DEGRADED/CLI_ONLY。

    全部有真实 web 来源=FULL; 存在缺口=CLI_ONLY (已被 evaluate_web_bandwidth
    判 FAIL); 否则有降级=DEGRADED; 无此类契约=NOT_APPLICABLE。
    not_applicable 收口的契约不计入 (与 evaluate_skill 的 N/A 短路一致)。
    """
    items = {item["name"]: item for item in registry["skills"]}
    states = []
    for sk in manifest["skills"]:
        item = items.get(sk["name"])
        if not item or not item.get("requires_web_bandwidth"):
            continue
        if _has_na_limitation(sk):
            continue
        if _has_web_source(sk):
            states.append("FULL")
        elif _web_bandwidth_degraded(sk):
            states.append("DEGRADED")
        else:
            states.append("CLI_ONLY")
    if not states:
        return "NOT_APPLICABLE"
    if all(s == "FULL" for s in states):
        return "FULL"
    if any(s == "CLI_ONLY" for s in states):
        return "CLI_ONLY"
    return "DEGRADED"


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
    workspace_audit_mode = run.get("workspace_audit_mode", "git")
    if workspace_audit_mode == "none":
        return []
    if workspace_audit_mode != "git":
        return [f"workspace_audit_mode 非法: {workspace_audit_mode!r}"]
    rel = PurePosixPath(run["run_root"])
    repo_root = Path(run["root_real"]).parents[len(rel.parts) - 1]
    try:
        baseline = json.loads((Path(run_root) / BASELINE_REL).read_text(
            encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return [f"审计基线不可读: {e}"]
    if baseline.get("schema_version") != 1 \
            or not isinstance(baseline.get("entries"), list):
        return ["审计基线 schema 非法或为无内容摘要的旧版本"]
    current = git_status_text(repo_root)
    baseline_by_path = {entry.get("path"): entry
                        for entry in baseline["entries"]
                        if isinstance(entry, dict) and isinstance(
                            entry.get("path"), str)}
    current_by_path = {path: {"record": rec,
                              "fingerprint": git_path_fingerprint(repo_root, path)}
                       for rec, path in parse_porcelain_v2_z(current)}
    rel_str = rel.as_posix()
    errors = []

    def excluded(path):
        if path == rel_str or path.startswith(rel_str + "/"):
            return True
        segments = path.split("/")
        if ".git" in segments or "__pycache__" in segments:
            return True
        return False

    for path, entry in baseline_by_path.items():
        if excluded(path):
            continue
        current_entry = current_by_path.get(path)
        if current_entry is None:
            errors.append(f"git 基线既有路径消失或状态被清除: {path}")
            continue
        if current_entry["record"] != entry.get("record"):
            errors.append(f"git 基线既有路径状态变化: {path}")
        if current_entry["fingerprint"] != entry.get("fingerprint"):
            errors.append(f"git 基线既有路径内容/类型/大小变化: {path}")

    for path in current_by_path:
        if path in baseline_by_path or excluded(path):
            continue
        errors.append(f"git 可见越界变化 (不在基线, 不在运行根): {path}")
    return errors


def evaluate_artifact_records(sk, item, manifest, registry):
    """校验产物 ID、事实/命令引用与注册表 feeds 血缘。"""
    errors = []
    all_facts = {fact.get("fact_id") for entry in manifest["skills"]
                 for fact in entry.get("facts", []) if isinstance(fact, dict)}
    all_commands = {receipt.get("command_id") for entry in manifest["skills"]
                    for receipt in entry.get("command_receipts", [])
                    if isinstance(receipt, dict)}
    artifact_owners = {}
    for entry in manifest["skills"]:
        for assigned in entry.get("assigned_artifacts", []):
            artifact_owners[assigned.get("artifact_id")] = entry.get("index")
    assigned_by_id = {assigned.get("artifact_id"): assigned.get("path")
                      for assigned in sk.get("assigned_artifacts", [])}
    records = [record for record in sk.get("artifact_records", [])
               if isinstance(record, dict)]
    seen = set()
    for record in records:
        artifact_id = record.get("artifact_id")
        path = record.get("artifact_path")
        if artifact_id in seen:
            errors.append(f"artifact_record 重复 artifact_id: {artifact_id}")
        seen.add(artifact_id)
        if artifact_id not in assigned_by_id:
            errors.append(f"artifact_record 使用未分配 artifact_id: {artifact_id}")
        elif assigned_by_id[artifact_id] != path:
            errors.append(f"artifact_record path 与分配不一致: {artifact_id}")
        if path not in sk.get("artifacts", []):
            errors.append(f"artifact_record path 未由 finish-skill 声明: {path}")
        dangling_facts = sorted(set(record.get("fact_ids", [])) - all_facts)
        dangling_commands = sorted(set(record.get("command_ids", [])) - all_commands)
        dangling_inputs = sorted(set(record.get("input_artifact_ids", [])) -
                                 set(artifact_owners))
        if dangling_facts:
            errors.append(f"artifact_record 悬空 fact ID: {dangling_facts}")
        if dangling_commands:
            errors.append(f"artifact_record 悬空 command ID: {dangling_commands}")
        if dangling_inputs:
            errors.append(f"artifact_record 悬空 input artifact ID: {dangling_inputs}")
        backward = sorted(
            input_id for input_id in record.get("input_artifact_ids", [])
            if input_id in artifact_owners
            and artifact_owners[input_id] >= sk.get("index", 0)
        )
        if backward:
            errors.append(f"artifact_record 后向/同层自依赖: {backward}")

    if item.get("name") == "ashare-data":
        if not records:
            errors.append("ashare-data 必须提交 artifact_record")
        linked_facts = {fact_id for record in records
                        for fact_id in record.get("fact_ids", [])}
        if not linked_facts:
            errors.append("ashare-data artifact_record 必须连接共享事实")
        successful_commands = {
            receipt.get("command_id") for receipt in sk.get("command_receipts", [])
            if isinstance(receipt, dict) and receipt.get("exit_code") == 0
        }
        linked_commands = {command_id for record in records
                           for command_id in record.get("command_ids", [])}
        missing_commands = sorted(successful_commands - linked_commands)
        if missing_commands:
            errors.append("ashare-data artifact_record 缺成功 command ID: "
                          f"{missing_commands}")

    capabilities = manifest.get("run", {}).get("capabilities", {})
    if item.get("name") != "ashare-data" \
            and capabilities.get("tushare_configured"):
        ashare_item = next((candidate for candidate in registry["skills"]
                            if candidate.get("name") == "ashare-data"), None)
        ashare_sk = next((entry for entry in manifest["skills"]
                          if entry.get("name") == "ashare-data"), None)
        if ashare_item and ashare_sk:
            ashare_artifact_ids = {
                assigned.get("artifact_id")
                for assigned in ashare_sk.get("assigned_artifacts", [])
            }
            for rule in ashare_item.get("evidence_rules", []):
                if rule.get("kind") != "conditional_command_operations":
                    continue
                for mapping in rule.get("values", []):
                    if not isinstance(mapping, dict) \
                            or mapping.get("feeds") != item.get("name"):
                        continue
                    operation = mapping.get("op")
                    command_ids = {
                        receipt.get("command_id")
                        for receipt in ashare_sk.get("command_receipts", [])
                        if isinstance(receipt, dict)
                        and receipt.get("operation") == operation
                        and receipt.get("exit_code") == 0
                    }
                    linked = any(
                        ashare_artifact_ids.intersection(
                            record.get("input_artifact_ids", []))
                        and command_ids.intersection(record.get("command_ids", []))
                        for record in records
                    )
                    if not linked:
                        errors.append(
                            f"artifact_record 未消费 ashare feeds 映射: "
                            f"{operation} -> {item.get('name')}")
    return errors


def evaluate_skill(sk, item, registry, run_root, calc_schema, manifest):
    """逐项计算 computed_status; 返回 (status, errors, caps)。"""
    if sk["execution_state"] == "BLOCKED":
        return "FAIL", ["执行态 BLOCKED (阻塞只能计为 FAIL)"], []

    errors, caps = [], []

    na, na_errors = evaluate_not_applicable(
        sk, item, registry, run_root, manifest)
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
        errors.extend(replay_calculation(calc, calc_schema))

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
    errors.extend(evaluate_evidence_rules(sk, item, manifest, run_root))
    errors.extend(evaluate_artifact_records(sk, item, manifest, registry))

    audit_errors, audit_caps = evaluate_audit(sk, item, run_root)
    errors.extend(audit_errors)
    caps.extend(audit_caps)

    role_errors, role_caps = evaluate_roles(
        sk, item, registry, run_root, manifest)
    errors.extend(role_errors)
    caps.extend(role_caps)

    web_errors, web_caps = evaluate_web_bandwidth(sk, item)
    errors.extend(web_errors)
    caps.extend(web_caps)

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
        # 合法 N/A 收口的契约不参与保障轴 (与 compute_information_bandwidth 的
        # N/A 短路一致): 否则对不适用该契约的公司 (如上市公司对 is_unlisted 契约),
        # 其 count=0 恒为 False 会令 INDEPENDENT 永不可达。PWL 单上下文契约无 N/A
        # 限制, 仍计入并如实降级。
        if min_ctx and not _has_na_limitation(sk):
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

    calc_schema = load_calc_schema()
    matrix = []
    any_fail = False
    any_pwl = False
    for sk in manifest["skills"]:
        item = items.get(sk["name"])
        if item is None:
            status, errors, caps = "FAIL", [f"不在注册表: {sk['name']}"], []
        else:
            status, errors, caps = evaluate_skill(
                sk, item, registry, run_root, calc_schema, manifest)
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

    run = manifest["run"]
    workspace_audit_mode = run.get("workspace_audit_mode", "git")
    run_errors = git_boundary_errors(manifest, run_root)
    run_errors.extend(watchlist_changes(manifest.get("watchlist", [])))
    run_caps = []
    if workspace_audit_mode == "none":
        run_caps.append(
            "Git 工作区审计未启用（Git 不可用或 repo_root 非 Git 工作树）："
            "已跳过工作区级越界变化审计；运行根路径 gate 与 legacy watchlist "
            "仍生效")

    completion = "COMPLETE"
    assurance = compute_assurance(manifest, registry)
    information_bandwidth = compute_information_bandwidth(manifest, registry)

    review_mode = manifest.get("run", {}).get("review_mode")
    if review_mode not in {"independent_context", "user", "self_review"}:
        run_errors.append(f"review_mode 非法或缺失: {review_mode!r}")
    elif review_mode == "independent_context" \
            and assurance == "SINGLE_CONTEXT":
        run_errors.append(
            "review_mode=independent_context 与 assurance=SINGLE_CONTEXT 矛盾")
    validation = "FAIL" if (any_fail or run_errors) else (
        "PASS_WITH_LIMITATIONS" if (any_pwl or run_caps) else "PASS")

    run["completion_status"] = completion
    run["validation_result"] = validation
    run["assurance_level"] = assurance
    run["information_bandwidth"] = information_bandwidth
    run["phase"] = "FINALIZED"

    result = {
        "run_id": run["run_id"],
        "completion_status": completion,
        "validation_result": validation,
        "assurance_level": assurance,
        "information_bandwidth": information_bandwidth,
        "review_mode": review_mode,
        "workspace_audit_mode": workspace_audit_mode,
        "industry": manifest.get("company", {}).get("industry"),
        "matrix": matrix,
        "run_errors": run_errors,
        "run_caps": run_caps,
        "finalized_at": now_iso(),
    }
    atomic_write_json(run_root / RESULT_REL, result)
    save_manifest(run_root, manifest)
    release_lock(run_root)

    print(f"finalize: completion_status={completion} "
          f"validation_result={validation} assurance_level={assurance} "
          f"information_bandwidth={information_bandwidth}")
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
    print("information_bandwidth="
          + result.get("information_bandwidth", "NOT_APPLICABLE"))
    print(f"review_mode={result['review_mode']}")
    print("workspace_audit_mode=" + result.get("workspace_audit_mode", "git"))
    print("industry=" + json.dumps(result.get("industry"), ensure_ascii=False,
                                   separators=(",", ":")))
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
    if result.get("run_caps"):
        print("运行级限制:")
        for cap in result["run_caps"]:
            print(f"  - {cap}")
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
    p_init.add_argument("--visibility", default="private",
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

    p_run_command = sub.add_parser(
        "run-ashare-command",
        help="由 gate 执行注册表登记的 ashare 命令并冻结收据",
    )
    add_registry(p_run_command)
    add_run_root(p_run_command)
    p_run_command.add_argument("--operation", required=True)
    p_run_command.add_argument("--code", required=True)
    p_run_command.add_argument("--source", action="append", required=True)
    p_run_command.add_argument("--timeout", type=int, default=120)
    p_run_command.set_defaults(func=cmd_run_ashare_command)

    p_finish = sub.add_parser("finish-skill", help="单项收口 COMPLETE/BLOCKED")
    add_registry(p_finish)
    add_run_root(p_finish)
    p_finish.add_argument("--skill", required=True)
    p_finish.add_argument("--state", required=True,
                          choices=["COMPLETE", "BLOCKED"])
    p_finish.add_argument("--artifact", action="append", default=[])
    p_finish.add_argument("--evidence-file", type=Path, default=None)
    p_finish.set_defaults(func=cmd_finish_skill)

    p_review = sub.add_parser("set-review-mode", help="记录实际复核模式")
    add_run_root(p_review)
    p_review.add_argument(
        "--mode", required=True,
        choices=["independent_context", "user", "self_review"])
    p_review.set_defaults(func=cmd_set_review_mode)

    p_industry = sub.add_parser(
        "set-industry", help="按 50%%/累计 80%% 规则写入公司行业 scope")
    add_run_root(p_industry)
    p_industry.add_argument("--industry-file", type=Path, required=True)
    p_industry.set_defaults(func=cmd_set_industry)

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
