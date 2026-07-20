#!/usr/bin/env bash
# 统一本地检查入口：改 tools/ 或 skills/ 后必跑
#   1) tests/ 单元测试（financial_rigor / report_audit 行为回归）
#   2) codex-skills / codex-prompts 生成物是否与权威源 skills/*.md 同步
#   3) local/reports/INDEX.md 报告索引是否与 local/reports/ 实际内容同步
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "== 单元测试 =="
python3 -m unittest discover -s "$ROOT/tests"

echo "== Codex skills 生成物同步检查 =="
python3 "$ROOT/scripts/sync-codex-skills.py" --check

echo "== Codex prompts 生成物同步检查 =="
python3 "$ROOT/scripts/sync-codex-prompts.py" --check

echo "== 报告索引同步检查 =="
python3 "$ROOT/scripts/build_report_index.py" --check

echo "== 全量分析注册表校验 =="
python3 "$ROOT/scripts/check-full-analysis-contract.py"

echo "✅ 全部检查通过"
