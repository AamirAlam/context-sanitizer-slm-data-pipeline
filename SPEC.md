# Product specification — context-sanitizer-slm-data-pipeline

> Status: draft. A coding agent must not implement product code until this specification is approved through Genesis.
>
> Evidence: derived from `.humanlayer/tasks/design-data-labeling-pipeline-architecture-micksk/` — `task.md` (objective + decided DAG), `02-research-labeling-pipeline-ecosystem.md` (OSS ecosystem survey), `03-design-discussion-pipeline-architecture.md` (9 resolved design questions). Where `task.md` and `03-design` disagree on tech stack, the later design doc is treated as authoritative pending confirmation (see Open questions).

## Problem

The context-distillation SLM (1.5B) needs training/eval data that does not exist in labeled form: redacted, character-offset-anchored, schema-conformant, human-verified records distilled from raw incident/log dumps. Today there is no safe way to view a raw dump (secrets/PII are exposed), no way to propose and human-correct labels, and no versioned, audit-stamped dataset produced. This project builds the **data-labeling pipeline** that produces that dataset. It does **not** train the model.

Desired outcome: a batch of raw dumps flows end-to-end through a privacy-preserving, human-in-the-loop DAG to a versioned, audit-stamped, offset-anchored labeled dataset, with zero raw-secret leakage into annotations or exports.

## Users

- **Reviewer** (primary human-in-the-loop): sees a redacted record + SLM-proposed label spans and acts (accept / rewrite / skip / reject / escalate). Single reviewer in v1.
- **Pipeline operator**: drops raw dumps in, runs the stages, cuts dataset versions.
- **Downstream SLM trainer** (consumer, out of scope here): reads the thread-grouped labeled dataset.
- **Affected non-users**: people/systems whose secrets and PII appear in raw dumps; the trust boundary exists to protect them.

## Functional requirements

- FR-1: **Secure intake** — accept a raw dump, capture provenance (source_system, submitter_id, ingested_at), compute `content_sha256`, and store the raw text verbatim.
- FR-2: **Offset-preserving parse** — produce a parse where `doc.text == raw_text` and raw character offsets are recoverable; no destructive normalization in v1.
- FR-3: **Candidate checks** — flag or reject records failing format, duplicate, or truncation checks before scanning.
- FR-4: **PII + secret scan** — run Presidio (entity PII + custom `Bearer`/`sk-` recognizers) plus an offline secret scanner; emit redaction spans `{entity_type, start, end, score}`; secret-scanner spans win on overlap.
- FR-5: **Propose labels** — the SLM proposes candidate label spans with an evidence note, referencing raw character offsets.
- FR-6: **Secure annotation view** — serve the redacted record plus proposed spans to the reviewer; store every span offset-only (`{doc_id, start, end, label}`), never copied sensitive text.
- FR-7: **Human review actions** — the reviewer can accept, rewrite, skip, reject, or escalate each proposed label; each action persists a decision.
- FR-8: **Automated validation** — validate each reviewed record with a Pydantic model: offsets in bounds, `start < end`, non-overlap, conditional-required evidence, policy conformance; failures route to adjudication.
- FR-9: **Adjudication loop** — records that fail validation or are escalated are queued for a second look and can re-enter review (v1: a flat `needs-second-look` flag, not a multi-reviewer queue).
- FR-10: **Dataset versioning** — write approved records as thread-grouped JSONL versioned with DVC, carrying per-record audit columns; export HF-`datasets` splits with no cross-split thread leakage.
- FR-11: **Audit logging** — append every review and access action to a plaintext-free (HMAC) append-only audit log; serving an unredacted span requires explicit, logged authorization.

## Non-functional requirements

- NFR-1: **Redaction safety** — raw secret/PII text never appears in annotations, exports, or the review DOM (offset-only storage, W3C TextPositionSelector model).
- NFR-2: **Offline sanitization** — the scan path makes no live verification or network calls; identical input yields identical redaction spans.
- NFR-3: **Offset integrity** — all offsets are Unicode code points; `raw[start:end]` reproduces each flagged/labeled span; misaligned offsets are detectable (`char_span(strict)` → `None`).
- NFR-4: **Auditability** — the audit log is append-only, stores HMAC hashes not plaintext, and fails closed (refuse the action if it cannot be recorded).
- NFR-5: **Orchestrator reversibility** — human-review state lives in the project's own DB so the orchestrator choice (none → Prefect/Temporal) stays reversible.
- NFR-6: **Reproducibility** — a dataset version is reproducible from the DVC-tracked inputs plus the pipeline definition.
- NFR-7: **Minimal surface** — Python backend; a single thin review UI screen; no new dependency where a few lines or an installed one suffices (Ponytail).

## Constraints

- Privacy-first: raw dumps and their secrets must not egress to any third-party API; all sanitization runs in-environment.
- Stack (per `03-design`, pending confirmation): Python backend (Presidio, spaCy, Pydantic, DVC, HF `datasets`); thin React/TS (or server-rendered) review screen.
- Human review is mandatory: Presidio documents "no guarantee it finds all," so automated scanning never ships labels without a human gate.
- Greenfield: no git repository yet; Genesis governs the workflow (no product code before spec approval).
- v1 is a single-reviewer thin slice; adjudication is a flag, not a team workflow.

## Non-goals

- Training or fine-tuning the SLM (downstream consumer of this dataset).
- Multi-reviewer inter-annotator agreement (IAA) and a full adjudication-team workflow.
- A distributed orchestrator (Prefect/Temporal/Airflow/Dagster) in v1.
- Multi-tenant RBAC/ABAC, SSO, or a policy engine (OPA/Cedar) in v1.
- Destructive text normalization (collapse/NFKC/lowercase/dedup) in v1.
- Off-the-shelf annotation platform adoption (Label Studio/Argilla/Doccano/Prodigy).

## Failure cases

- Scanner misses a secret → mitigated by the mandatory human review stage and offset round-trip tests; never assume the scanner is complete.
- Offset drift (any normalization, multibyte, whitespace) → detected by `raw[start:end] == span.text` assertions and `char_span(strict)` None.
- Span crosses a truncation boundary → trimmed/rejected, never silently shifted.
- Same source thread lands in two dataset splits → export must fail the no-leakage check.
- Audit sink unavailable → action is refused (fail closed), not performed unlogged.
- Invalid SLM proposal (bad offsets, missing evidence) → validation rejects, routes to adjudication.

## Acceptance criteria

- AC-1: Given a raw dump containing a known secret, the redacted view and every export contain no substring of that secret, and `raw[start:end]` for its redaction span reproduces the original token. (FR-1, FR-4, NFR-1, NFR-3)
- AC-2: A proposed span with `end <= start` or out-of-bounds offsets is rejected by validation, and a span not landing on token boundaries yields `char_span(strict) is None`. (FR-2, FR-8, NFR-3)
- AC-3: Each reviewer action (accept/rewrite/skip/reject/escalate) persists a decision and writes exactly one append-only audit entry that contains no plaintext secret. (FR-7, FR-11, NFR-4)
- AC-4: An `EVIDENCE`-labeled span with no evidence note fails validation. (FR-8)
- AC-5: Exported approved records are thread-grouped JSONL in which every record of a given source thread appears in exactly one split (no thread spans two splits). (FR-10, NFR-1)
- AC-6: A `view_unredacted` request without authorization is denied and logged; with authorization it returns the span and logs it. (FR-11, NFR-4)
- AC-7: Running the scan twice on identical input produces identical redaction spans with no network call. (FR-4, NFR-2)

## Risks

- SLM proposal quality unknown → pipeline must be useful even if it degrades to human-from-scratch labeling (assumption ASSUMPTION-7bd0a9ca).
- Single-reviewer throughput may bottleneck dataset volume (ASSUMPTION-b7bddc2c).
- Offset preservation is the whole-system seam; a bug there corrupts every downstream stage — mitigated by round-trip assertions as a first-class gate.
- Research "Unverified" sub-facts (exact span JSON shapes, Prefect pause terminal state, Airflow HITL version, Llama-3 offsets) must be confirmed before implementation relies on them (ASSUMPTION-4c36bb5a).

## Open questions

- Confirm that `03-design`'s resolved tech stack supersedes `task.md`'s "stack not yet decided" (document precedence) — recorded as an unresolved artifact disagreement, not yet human-confirmed (ASSUMPTION-fd80e5a6).
- What are the concrete source systems/formats of raw dumps beyond incident/log text? (ASSUMPTION-fd80e5a6 / ASSUMPTION-7bd0a9ca)
- Target dataset size and split ratios for v1 (drives whether Pandera/dataset gates are needed early).
