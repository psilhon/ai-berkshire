# Full Analysis Quality Closure Design

## Goal

Make `APPROVED`, `REVIEW_PASSED`, and `STABLE` trustworthy statements about the
current run rather than labels that can be produced from stale, incomplete, or
incomparable inputs.

## Scope

This change closes the defects identified in the 2026-07-25 full-analysis
review:

1. Bind every result to the active Runtime lease before Gate promotion.
2. Route orphan-result recovery through the same validated acceptance path.
3. Execute every registered evidence-rule kind and persist the required
   judgments, role runs, and command receipts.
4. Bind Audit and Review outputs to immutable input digests.
5. Enforce contract sections and substantive-section minimums.
6. Reject incomplete, foreign, or stale semantic reviews.
7. Reject incomparable benchmark cohorts and treat missing evidence as missing.
8. Preserve the optional-code `margin` summary command.

No external dependency, public report migration, remote write, or unrelated
refactor is included.

## Architecture

### One result-acceptance path

Runtime owns lease authorization. Before Gate reads or promotes an artifact,
Runtime validates the full identity tuple:

`run_id, work_unit_id, skill_id, attempt_id, lease_nonce, agent_job_id`.

Normal submission and orphan recovery call the same acceptance function. Gate
remains responsible for bundle schema, artifact integrity, contract content,
and manifest promotion. Runtime marks a unit `DONE` only after Gate succeeds.

### Executable contract

Audit uses an explicit evaluator registry covering every value accepted by
`EVIDENCE_KINDS`. Contract validation fails if a registered rule has no Audit
evaluator. Gate persists facts, sources, replayed calculations, judgments,
command receipts, and role-run records with per-skill ownership.

Calculations count toward `min_calculations` only when they carry a successful
replay result. Dual-source rules require two distinct source publishers.

### Snapshot-bound decisions

An input snapshot digest is derived deterministically from the current
manifest, registry digest, and accepted artifact/evidence records while
excluding mutable decision metadata such as `run.status` and timestamps.
Audit records that digest. Finalize recomputes it and rejects stale Audit
results.

Review briefs record their own digest plus report/evidence digests. Review
results must echo those values and the current run ID. Aggregation requires the
complete prepared scope and a result for every review dimension. Any changed
report or evidence invalidates the review.

### Deterministic content gate

Required contract sections must exist and meet `min_content_chars`.
`min_substantive_sections` counts only non-boilerplate sections whose normalized
body contains enough non-repeated text. Existing dissent and fanout checks stay
in place.

### Comparable stability benchmark

Benchmark comparison first validates cohort identity: company code, company
name, `as_of`, contract digest, and terminal approval status. Facts,
calculations, and judgments missing from any run are reported as missing and
cannot be counted as consistent. A run with zero facts has zero source
coverage. Incomparable cohorts return `INCOMPARABLE`, not `STABLE`.

## Failure Semantics

- Invalid or stale Result Bundle: reject without changing formal artifacts,
  manifest, or Runtime terminal state.
- Invalid orphan result: enter the existing retry/failure path.
- Missing Audit evaluator or stale Audit snapshot: fail closed.
- Missing, foreign, or stale Review result: `REVIEW_REQUIRED`.
- Incomparable benchmark cohort: exit non-zero with `INCOMPARABLE`.
- Doctor remains advisory; it must never manufacture approval.

## Compatibility

New runs use the strengthened ledgers and digests. Historical manifests remain
readable but are not silently treated as compatible with new Audit, Review, or
Benchmark results. Benchmark groups must share the same contract digest.

## Verification

Each reviewed defect receives a regression test that first reproduces the old
failure. Completion requires:

- all targeted full-analysis tests pass;
- the adversarial lease, orphan, stale-Audit, stale-Review, incomplete-review,
  missing-benchmark-data, cross-company benchmark, section-padding, and
  margin-summary tests pass;
- `bash scripts/check.sh` passes;
- `git diff --check` passes.
