# Full Analysis Quality Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close every verified path that can falsely produce `DONE`, `APPROVED`, `REVIEW_PASSED`, or `STABLE`.

**Architecture:** Runtime becomes the sole lease authority and all result recovery uses one acceptance path. Gate and Audit enforce the complete contract, while Audit and Review decisions are bound to deterministic input snapshots. Benchmark rejects incomparable cohorts and treats missing data explicitly.

**Tech Stack:** Python 3 standard library, `unittest`, JSON manifests and registries.

## Global Constraints

- Preserve the existing Result Bundle v1 fields and existing report locations.
- Add no dependency and perform no external write.
- Preserve unrelated dirty-worktree changes.
- Use test-first red/green cycles for every behavior change.
- Do not commit implementation files automatically because they already contain user changes.

---

### Task 1: Atomic result acceptance and orphan recovery

**Files:**
- Modify: `tools/full_analysis_runtime.py`
- Test: `tests/test_full_analysis_runtime.py`

**Interfaces:**
- Consumes: current Runtime state, Result Bundle path, registry path.
- Produces: `_validate_result_lease(state, bundle)` and a single
  `submit_result(run_root, registry, result)` path used by normal submission and
  orphan recovery.

- [x] Add a regression test submitting a valid artifact with a foreign
  `attempt_id`, nonce, or job ID; assert rejection and unchanged manifest/file.
- [x] Run the focused test and verify it fails because the foreign result is accepted.
- [x] Validate the full lease identity before invoking Gate.
- [x] Add a regression test with a status-only orphan result; assert it is not
  marked DONE and is routed to retry/failure.
- [x] Run the focused test and verify it fails because Runtime becomes DONE.
- [x] Make orphan recovery call the same validated submission path and preserve
  concurrency accounting.
- [x] Run `python3 -m unittest tests.test_full_analysis_runtime` and verify PASS.

### Task 2: Complete contract evaluation and substantive sections

**Files:**
- Modify: `tools/full_analysis_gate.py`
- Modify: `tools/full_analysis_audit.py`
- Modify: `scripts/check-full-analysis-contract.py`
- Modify: `tools/full_analysis_result_schema.json`
- Test: `tests/test_full_analysis_gate_v2.py`
- Test: `tests/test_full_analysis_audit.py`
- Test: `tests/test_full_analysis_contract_v2.py`
- Test: `tests/test_full_analysis_e2e.py`

**Interfaces:**
- Consumes: all `EVIDENCE_KINDS`, Result Bundle evidence ledgers, contract sections.
- Produces: persisted `judgments`, `command_receipts`, and `role_runs`; exhaustive
  `RULE_EVALUATORS`; replay-qualified calculation counts.

- [x] Add failing tests for missing judgments, falsification, role runs, command
  receipts, required command operations, unreplayed calculations, and same-publisher
  dual sources.
- [x] Add a failing test proving one padded section cannot satisfy a skill with
  required sections and `min_substantive_sections`.
- [x] Persist all contract evidence ledgers during Gate merge.
- [x] Implement one evaluator per evidence-rule kind and make the independent
  contract checker assert exact evaluator coverage.
- [x] Count only successfully replayed calculations and independent publishers.
- [x] Enforce required sections, per-section minimum content, and substantive
  section count while retaining dissent/fanout checks.
- [x] Update E2E evidence builders to emit genuinely compliant evidence rather
  than empty judgments or bare calculations.
- [x] Run the four focused test modules and verify PASS.

### Task 3: Snapshot-bound Audit and finalize

**Files:**
- Modify: `tools/full_analysis_gate.py`
- Modify: `tools/full_analysis_audit.py`
- Test: `tests/test_full_analysis_audit.py`
- Test: `tests/test_full_analysis_gate_v2.py`
- Test: `tests/test_full_analysis_e2e.py`

**Interfaces:**
- Produces: deterministic `analysis_snapshot_digest(manifest, registry_path)` used
  by Audit and finalize.

- [x] Add a failing test that audits, changes accepted evidence, then finalizes;
  assert stale Audit rejection.
- [x] Implement a deterministic digest excluding timestamps and mutable decision
  status while including registry, artifacts, facts, sources, calculations,
  judgments, receipts, role runs, and skill terminal states.
- [x] Record the digest and registry SHA in `audit-result.json`.
- [x] Recompute and compare both values in finalize.
- [x] Run focused Audit/Gate/E2E tests and verify PASS.

### Task 4: Bound and complete semantic reviews

**Files:**
- Modify: `tools/full_analysis_review.py`
- Modify: `tools/full_analysis_gate.py`
- Test: `tests/test_full_analysis_review.py`
- Test: `tests/test_full_analysis_gate_v2.py`

**Interfaces:**
- Produces: review briefs with report/evidence/brief digests and results containing
  `run_id`, those digests, and one decision per review dimension.

- [x] Add failing tests for foreign run ID, stale brief digest, incomplete review
  scope, missing dimensions, and corrupted result files.
- [x] Bind briefs and ingested results to the current run and current report/evidence.
- [x] Revalidate every stored result during aggregation and require all prepared
  skills and dimensions.
- [x] Return `REVIEW_REQUIRED` for incomplete/stale input and never label it
  `REVIEW_PASSED`.
- [x] Make finalize expose incomplete/stale review accurately without converting
  it to an approval signal.
- [x] Run Review and Gate tests and verify PASS.

### Task 5: Comparable stability benchmark

**Files:**
- Modify: `tools/full_analysis_benchmark.py`
- Test: `tests/test_full_analysis_benchmark.py`

**Interfaces:**
- Produces: `INCOMPARABLE`, `STABLE`, or `UNSTABLE`, with explicit missing facts,
  calculations, and judgments.

- [x] Add failing tests for different companies, different as-of dates, different
  contract digests, non-approved runs, zero-fact runs, and a fact missing in one run.
- [x] Add cohort validation before metric calculation.
- [x] Treat missing values as missing/divergent; assign zero coverage to zero facts.
- [x] Compare judgments by persisted `conclusion` and report missing judgments.
- [x] Rename fact-count variance so it is not represented as semantic quality.
- [x] Run benchmark tests and verify PASS.

### Task 6: Margin regression, workflow documentation, and closure

**Files:**
- Modify: `tools/ashare_data.py`
- Modify: `tests/test_ashare_data.py`
- Modify: `skills/full-company-analysis.md`
- Regenerate: `codex-skills/full-company-analysis/SKILL.md`
- Modify: `workbuddy-skills/full-company-analysis/SKILL.md`

**Interfaces:**
- Preserves: `cmd_margin(code=None)` market-summary mode.

- [x] Add a failing test calling `cmd_margin(None, trade_date)` with a fake client.
- [x] Move code normalization into the individual-stock branch.
- [x] Update workflow text so Audit/Review snapshot invalidation and quality states
  match implementation.
- [x] Run `python3 scripts/sync-codex-skills.py`.
- [x] Run all focused tests.
- [x] Run `bash scripts/check.sh`.
- [x] Run `git diff --check` and inspect the final diff for unrelated changes.
