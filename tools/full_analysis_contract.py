#!/usr/bin/env python3
"""full-analysis-contract 契约的唯一解析缝（Contract 深模块）。

全量公司分析管线的契约真源是 tools/full_analysis_contract.json（13 个工作单元）。
此前 gate.load_registry / runtime._load_registry / mk_result_bundle.find_skill /
review.load_registry 各自重新实现「读 JSON + 找 skill」，schema 演进要追 N 个调用点。
本模块收敛为一条缝：调用方只学 4 个名字。

设计约束：
- 零依赖：不 import gate/runtime/audit 等任何执行模块（与 check-full-analysis-contract.py
  的独立性立场一致——契约解析不得与执行代码耦合）。
- 错误通道留给调用方：本模块抛 ContractError（带 code），调用方在自己的 adapter
  里翻译成 GateError / fail() / 降级空表，保持既有退出码语义逐字不变。
"""
from __future__ import annotations

import json
from pathlib import Path

CONTRACT_PATH = Path(__file__).resolve().parent / "full_analysis_contract.json"

SUPPORTED_SCHEMA_VERSIONS = (
    "full-analysis-contract/v2",
    "full-analysis-contract/lean-v1",
)

EXPECTED_SKILL_COUNT = 13


class ContractError(Exception):
    """契约读取/结构错误。code 语义对齐 Gate 退出码：2=结构/参数类错误。"""

    def __init__(self, message: str, code: int = 2):
        self.code = code
        super().__init__(message)


def load_contract(path: Path | None = None, *, strict: bool = True) -> dict:
    """读取并校验契约。

    strict=True（生产默认）：文件可读、顶层为对象、schema_version 受支持、
    恰好 13 个 skill；任一不满足抛 ContractError（消息与 gate 历史口径逐字一致）。
    strict=False（runtime 容错档）：读取或解析失败降级返回 {"skills": []}，
    不校验结构——编排状态机在契约缺失时宁可空派发也不崩溃。
    """
    contract_path = Path(path) if path is not None else CONTRACT_PATH
    try:
        raw = contract_path.read_text(encoding="utf-8")
        registry = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        if not strict:
            return {"skills": []}
        raise ContractError(
            f"注册表 不可读或非法 JSON: {contract_path}: {exc}") from exc
    if not isinstance(registry, dict):
        if not strict:
            return {"skills": []}
        raise ContractError(f"注册表 顶层必须为对象: {contract_path}")
    if not strict:
        return registry
    sv = registry.get("schema_version")
    if sv not in SUPPORTED_SCHEMA_VERSIONS:
        raise ContractError(f"不支持的注册表 schema_version: {sv}")
    if len(registry.get("skills", [])) != EXPECTED_SKILL_COUNT:
        raise ContractError("注册表必须恰好包含 13 个 skill")
    return registry


def find_skill(registry: dict, skill_id: str) -> dict:
    """按 skill_id 精确查找契约单元；未找到抛 ContractError。"""
    for item in registry.get("skills", []):
        if item.get("skill_id") == skill_id:
            return item
    raise ContractError(f"未知 skill_id: {skill_id}")


def get_skill_or_none(registry: dict, skill_id: str) -> dict | None:
    """按 skill_id 查找；未找到返回 None（供容错调用点使用）。"""
    for item in registry.get("skills", []):
        if item.get("skill_id") == skill_id:
            return item
    return None
