#!/usr/bin/env python3
"""run_store.py — run 状态文件 I/O 的单一所有者（2026-08-30 架构评审候选②·完整版）。

背景：四个状态文件（manifest / runtime-state / events / usage）的读写此前散布
gate 与 runtime 两模块——gate 带 fsync 版 atomic_write_json，runtime 自带简化版
atomic_json（无 fsync），且 runtime 刷新 usage 汇总时用**非原子** write_text 直写
manifest（进程中途被杀会留下半截 JSON）。同一文件两套原子性、两套错误类型。

本模块收拢"怎么读写"为一条缝：
  - 原语：atomic_write_json / atomic_write_text（tmp+fsync+权限保持，gate 实现上移）
  - 状态：load/write_manifest、load/write_runtime_state（版本校验单点 STATE_VERSION）
  - 账本：append_event / read_events / reset_events、append_usage / read_usage

所有权边界（有意保留）：**何时写、写什么策略**仍归 gate（manifest/events）
与 runtime（runtime-state/usage）——本模块不碰业务语义，不设锁（跨进程互斥
仍由 runtime.runtime_lock 承担）。三层分工：
  run_layout（放在哪）→ run_store（怎么读写）→ gate/runtime（何时写、写什么）。

与 substance.py / run_layout.py 同模式：小 interface、确定性、无副作用。
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from run_layout import (  # noqa: F401  (LOCK_REL 经此 re-export，单一真源仍在 run_layout)
    EVENTS_REL,
    LOCK_REL,
    MANIFEST_REL,
    RUNTIME_STATE_REL,
    USAGE_REL,
)

STATE_VERSION = "runtime-state/v1"
TZ_SHANGHAI = timezone(timedelta(hours=8))


class RunStoreError(Exception):
    """状态文件 I/O 错误；由 gate/runtime 翻译为各自的领域异常（GateError/RuntimeErrorState）。"""

    def __init__(self, message: str, *, code: int = 2):
        super().__init__(message)
        self.code = code


def now_iso() -> str:
    return datetime.now(TZ_SHANGHAI).isoformat()


# ---------------------------------------------------------------- 原语：原子写
def atomic_write_json(path: Path, value: object) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def atomic_write_text(path: Path, content: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _load_json_object(path: Path, label: str) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RunStoreError(f"{label} 不可读或非法 JSON: {path}: {exc}", code=2)
    if not isinstance(value, dict):
        raise RunStoreError(f"{label} 顶层必须为对象: {path}", code=2)
    return value


# ---------------------------------------------------------------- manifest
def manifest_path(run_root: Path) -> Path:
    return Path(run_root) / MANIFEST_REL


def load_manifest(run_root: Path) -> dict:
    return _load_json_object(manifest_path(run_root), "manifest")


def write_manifest(run_root: Path, manifest: dict) -> None:
    """原子写 manifest（无策略：updated_at 等业务字段由调用方自行维护）。"""
    atomic_write_json(manifest_path(run_root), manifest)


# ---------------------------------------------------------------- runtime-state
def runtime_state_path(run_root: Path) -> Path:
    return Path(run_root) / RUNTIME_STATE_REL


def load_runtime_state(run_root: Path) -> dict:
    path = runtime_state_path(run_root)
    state = _load_json_object(path, "runtime-state")
    if state.get("state_version") != STATE_VERSION:
        raise RunStoreError("runtime-state 版本不匹配", code=1)
    return state


def write_runtime_state(run_root: Path, state: dict) -> None:
    atomic_write_json(runtime_state_path(run_root), state)


# ---------------------------------------------------------------- events.jsonl
def append_event(run_root: Path, event: dict) -> dict:
    """追加一条事件（event_at 缺省补齐，键序 event_at 在前）。返回写入的完整记录。"""
    path = Path(run_root) / EVENTS_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"event_at": now_iso(), **event}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def reset_events(run_root: Path) -> None:
    path = Path(run_root) / EVENTS_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, "")


def read_events(run_root: Path) -> list[dict]:
    path = Path(run_root) / EVENTS_REL
    if not path.exists():
        return []
    records: list[dict] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


# ---------------------------------------------------------------- usage.jsonl
def usage_path(run_root: Path) -> Path:
    return Path(run_root) / USAGE_REL


def read_usage(run_root: Path) -> list[dict]:
    path = Path(run_root) / USAGE_REL
    if not path.exists():
        return []
    records: list[dict] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def append_usage(run_root: Path, receipt: dict) -> None:
    path = Path(run_root) / USAGE_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(receipt, ensure_ascii=False) + "\n")
