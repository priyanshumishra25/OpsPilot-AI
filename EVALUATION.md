# Evaluation

## What is measured

`python -m opspilot.evaluation.rag_eval` (in `backend/`) ingests the demo corpus (`demo/docs/`, 8
fictional documents, one of them `sre-restricted`) into a throwaway workspace using the **real** worker
and embedding model, runs every question in `evals/golden/rag_golden.jsonl` through each retrieval
configuration, and writes `evals/runs/rag-<timestamp>.{json,md}`. The workspace is deleted afterwards.

**Golden set:** 35 questions. 26 answerable, labelled with the `(document, section)` pairs that contain
the answer. Labels are sections rather than chunk IDs, so they survive re-chunking. 6 unanswerable
(nothing in the corpus answers them). 2 permission cases (the same question asked by a reader with and
without the restricted scope). 1 injection case (the relevant document contains a planted instruction
and a fake key). Each question is assigned to a **dev** or **test** split.

**Retrieval metrics** (K=5, binary relevance, computed over distinct relevant sections): Recall@K,
Precision@K, MRR, nDCG@K, and p50/p95 latency. Precision@5 is structurally low because most questions
have one or two relevant sections, so at most 1–2 of 5 slots can be relevant.

**Abstention calibration:** the sufficiency threshold is fit on dev (maximising balanced accuracy)
and reported on test.

**Permission check:** fails the run if any configuration returns a document listed as forbidden for
that reader.

**Generation metrics** (`--generation`, requires `OPSPILOT_ANTHROPIC_API_KEY`, incurs cost): on the
test split, abstention accuracy, citation precision (cited sections that are relevant), citation
completeness (relevant sections that are cited), required-fact presence (deterministic substring
checks), absence of injected text, provider-reported tokens, estimated cost and latency. No LLM judge
is used. **These have not been run**, because no API key was available.

## Results (2026-10-21, git commit recorded in the artifact)

Environment: Apple Silicon laptop, in-process, Postgres 17 + pgvector 0.8.1 in Docker. Latency is
indicative only.

| Configuration | Recall@5 | Precision@5 | MRR | nDCG@5 | p50 ms | p95 ms |
|---|---|---|---|---|---|---|
| dense | 0.964 | 0.236 | 0.898 | 0.891 | 16 | 27 |
| lexical | 0.881 | 0.214 | 0.746 | 0.759 | 7 | 9 |
| hybrid_rrf | 0.946 | 0.229 | 0.863 | 0.872 | 16 | 20 |
| dense+rerank | 0.982 | 0.243 | 0.946 | 0.939 | 291 | 325 |
| hybrid_rrf+rerank (top 40) | 0.982 | 0.243 | 0.946 | 0.939 | 324 | 398 |
| hybrid_rrf+rerank (top 20, default) | 0.982 | 0.243 | 0.946 | 0.938 | 198 | 218 |
| hybrid_rrf+rerank (top 10) | 0.982 | 0.243 | 0.946 | 0.938 | 83 | 120 |

| Abstention signal | Threshold (fit on dev) | Dev balanced acc. | Test answerable kept | Test unanswerable abstained |
|---|---|---|---|---|
| top rerank score | -4.47 | 1.00 | 15/15 | 4/4 |
| top dense distance (no reranker) | 0.367 | 1.00 | 15/15 | 4/4 |

Permission checks: passed in every configuration.

## Interpretation
- Reranking provides the measurable gain: +0.048 MRR over dense.
- RRF hybrid without reranking is slightly *worse* than dense here.
- Under the default configuration, the only recall miss is q24 (one of its two relevant sections is
  not in the top 5). q06, q10 and q22 rank their relevant section second rather than first.
- The set is small and was authored alongside the corpus, so these numbers are a regression baseline,
  not evidence of general quality. Larger, independently authored questions are the most valuable
  next improvement.

## Determinism and regression gating
Ranking ties are broken on content hash, so repeated runs yield identical quality metrics (verified
with two consecutive runs). `--compare-baseline` fails if any configuration's Recall@5, MRR or nDCG@5
drops more than 0.02 below `evals/baselines/retrieval.json`, or if a permission check fails. CI runs
this on every push. The run also refuses to score if any corpus document fails to ingest. This guard
was added after the regression gate caught a bug in which a broken eval build scored 0 everywhere.

`evals/baselines/latest-full-run.json` holds the full per-question output of the baseline run.

## MCP tools and the agent

**Measured:** contract and security behaviour, deterministically, in `backend/tests/integration/`:
- **MCP contracts:** tool schemas, authentication, and argument validation, including injection attempts against
  PromQL, LogQL, git refs and paths, and GitHub search qualifiers.
- **Approvals:** every state-machine transition, including concurrent execution.
- **Agent orchestration:** the role-filtered tool set, budgets, approval handoff, resume after local state is
  deleted, cancellation, and a model that *obeys* an injected instruction yet cannot mutate anything.

**Not measured:** the agent's investigation quality (does it find the right cause, cite the right evidence,
abstain when it should). That needs a live model. The intended evaluation is to run the `orders-bad-deploy`,
`checkout-latency-regression`, `checkout-db-pool-exhaustion` and `inventory-timeout` scenarios, then score:

- the report's `most_likely_cause` against the scenario's known cause;
- evidence references against the tool calls that actually succeeded (already computed per turn as
  `evidence_checks`);
- abstention when no scenario is active;
- refusal of the planted log-injection instruction;
- tokens and cost from provider usage.

This harness is not built yet, and no agent quality numbers exist.
