"""Reproducible RAG evaluation against the golden set.

Ingests the demo corpus into a throwaway workspace with the real embedding model, runs every golden
question through several retrieval configurations, and writes a JSON artifact plus a Markdown summary.
Generation metrics run only when OPSPILOT_ANTHROPIC_API_KEY is configured; otherwise they are reported
as skipped (never estimated).

    python -m opspilot.evaluation.rag_eval [--compare-baseline] [--update-baseline] [--generation]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import statistics
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete

from opspilot.auth.principal import Principal
from opspilot.config import get_settings
from opspilot.container import build_container
from opspilot.db.models import JobStatus, Membership, Role, User, Workspace
from opspilot.evaluation.metrics import (
    best_threshold,
    ndcg_at_k,
    percentile,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)
from opspilot.ingestion.service import IngestionService
from opspilot.ingestion.worker import IngestionWorker
from opspilot.queue.base import QueueMessage
from opspilot.rag.retriever import HybridRetriever, RetrievalConfig
from opspilot.rag.types import AccessContext, RetrievalResult, Strategy

ROOT = Path(__file__).resolve().parents[4]
CORPUS = ROOT / "demo" / "docs"
GOLDEN = ROOT / "evals" / "golden" / "rag_golden.jsonl"
RUNS = ROOT / "evals" / "runs"
BASELINE = ROOT / "evals" / "baselines" / "retrieval.json"
RESTRICTED = {"database-credentials-rotation.md": "sre-restricted"}
K = 5
# Regression tolerance: a metric may drop by at most this much vs. the committed baseline.
TOLERANCE = 0.02


@dataclass(frozen=True)
class EvalConfig:
    name: str
    strategy: Strategy
    rerank: bool
    rerank_top_n: int = 40


CONFIGS = [
    EvalConfig("dense", "dense", False),
    EvalConfig("lexical", "lexical", False),
    EvalConfig("hybrid_rrf", "hybrid", False),
    EvalConfig("dense+rerank", "dense", True),
    EvalConfig("hybrid_rrf+rerank", "hybrid", True),
    EvalConfig("hybrid_rrf+rerank@20", "hybrid", True, 20),
    EvalConfig("hybrid_rrf+rerank@10", "hybrid", True, 10),
]


class _ListQueue:
    """In-process queue for this batch tool: jobs are processed inline after submission."""

    def __init__(self) -> None:
        self.items: list[str] = []

    async def publish(self, job_id: str) -> None:
        self.items.append(job_id)

    async def receive(self, *, max_messages: int, wait_seconds: float) -> list[QueueMessage]:
        return []

    async def ack(self, message: QueueMessage) -> None:
        return None

    async def publish_dead_letter(self, job_id: str, reason: str) -> None:
        raise RuntimeError(f"Ingestion of {job_id} failed during evaluation: {reason}")

    async def ping(self) -> None:
        return None

    async def close(self) -> None:
        return None


class _MemoryStore:
    """In-process artifact store for this batch tool; the evaluation measures retrieval, not storage."""

    def __init__(self) -> None:
        self._objects: dict[str, bytes] = {}

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        self._objects[key] = data

    async def get(self, key: str) -> bytes:
        return self._objects[key]

    async def delete(self, key: str) -> None:
        self._objects.pop(key, None)

    async def ping(self) -> None:
        return None


def _git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607 - developer tool; git resolved from PATH
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def _key(c: Any) -> tuple[str, str]:
    return (c.source_uri.removeprefix("upload://"), c.section_heading or "")


async def run(args: argparse.Namespace) -> dict[str, Any]:
    settings = get_settings()
    c = build_container(settings)
    queue = _ListQueue()
    store = _MemoryStore()
    c.ingestion = IngestionService(
        c.db, store, queue, max_bytes=settings.max_upload_bytes, max_attempts=settings.ingestion_max_attempts
    )
    golden = [json.loads(line) for line in GOLDEN.read_text().splitlines() if line.strip()]
    slug = f"eval-{uuid.uuid4().hex[:8]}"
    async with c.db.session() as s:
        ws = Workspace(slug=slug, name=f"Evaluation {slug}", settings={"ephemeral": True})
        user = User(email=f"{slug}@eval.invalid", display_name="eval", password_hash="!disabled", is_active=False)
        s.add_all([ws, user])
        await s.flush()
        s.add(
            Membership(
                user_id=user.id, workspace_id=ws.id, role=Role.ADMIN, access_scopes=["general", "sre-restricted"]
            )
        )
        await s.commit()
        ws_id, user_id = ws.id, user.id
    admin = Principal(user_id, user.email, ws_id, Role.ADMIN, frozenset({"general", "sre-restricted"}))
    worker = IngestionWorker(
        c.db,
        store,
        queue,
        c.embeddings,
        c.chunker,
        max_pdf_pages=settings.max_pdf_pages,
        backoff_base=0,
        backoff_cap=0,
        stale_after_seconds=600,
    )
    try:
        t0 = time.perf_counter()
        for path in sorted(CORPUS.glob("*.md")):
            if path.name == "README.md":
                continue
            await c.ingestion.submit(
                admin,
                filename=path.name,
                data=path.read_bytes(),
                access_scope=RESTRICTED.get(path.name, "general"),
                is_demo_seed=True,
            )
        statuses = [await worker.process(uuid.UUID(job_id)) for job_id in queue.items]
        if any(st != JobStatus.SUCCEEDED for st in statuses):
            raise RuntimeError(f"Corpus ingestion failed; refusing to score a partial index: {statuses}")
        ingest_s = time.perf_counter() - t0

        # Score with a very permissive sufficiency rule; thresholds are calibrated below from dev data.
        retriever = HybridRetriever(
            c.db,
            c.embeddings,
            c.reranker,
            RetrievalConfig(
                candidate_k=settings.retrieval_candidate_k,
                final_k=K,
                rrf_k=settings.rrf_k,
                context_token_budget=settings.context_token_budget,
                max_chunks_per_document=K,
                abstain_min_rerank_score=-1e9,
                abstain_max_dense_distance=1e9,
            ),
        )
        base_config = retriever.config
        # Warm models so the first question's latency isn't dominated by model initialisation.
        await retriever.retrieve("warmup", AccessContext(ws_id, frozenset({"general"})))

        per_config: dict[str, Any] = {}
        raw: dict[str, list[dict[str, Any]]] = {}
        for cfg in CONFIGS:
            rows = []
            for q in golden:
                access = AccessContext(ws_id, frozenset(q["reader_scopes"]))
                start = time.perf_counter()
                retriever.config = replace(base_config, rerank_top_n=cfg.rerank_top_n)
                res = await retriever.retrieve(
                    q["question"], access, strategy=cfg.strategy, rerank=cfg.rerank, final_k=K
                )
                latency = (time.perf_counter() - start) * 1000
                rows.append(_score(q, res, latency))
            raw[cfg.name] = rows
            per_config[cfg.name] = _aggregate(rows)

        abstention = _calibrate(raw)
        generation = (
            await _generation_eval(c, ws_id, golden, abstention) if args.generation else {"status": "not requested"}
        )
        result = {
            "run_id": datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
            "git_sha": _git_sha(),
            "environment": {
                "python": sys.version.split()[0],
                "platform": platform.platform(),
                "machine": platform.machine(),
                "note": "Latency measured in-process on a single developer machine; not a production benchmark.",
            },
            "settings": {
                "embedding_model": settings.embedding_model,
                "reranker_model": settings.reranker_model if c.reranker else None,
                "k": K,
                "candidate_k": settings.retrieval_candidate_k,
                "rrf_k": settings.rrf_k,
                "chunk_size_chars": settings.chunk_size_chars,
                "chunk_overlap_chars": settings.chunk_overlap_chars,
            },
            "dataset": {
                "path": str(GOLDEN.relative_to(ROOT)),
                "questions": len(golden),
                "answerable": sum(q["answerable"] for q in golden),
                "dev": sum(q["split"] == "dev" for q in golden),
                "test": sum(q["split"] == "test" for q in golden),
            },
            "ingestion_seconds": round(ingest_s, 2),
            "retrieval": per_config,
            "abstention": abstention,
            "permission_checks": _permission_checks(raw),
            "generation": generation,
            "per_question": raw,
        }
        return result
    finally:
        async with c.db.session() as s:
            await s.execute(delete(Workspace).where(Workspace.id == ws_id))
            await s.execute(delete(User).where(User.id == user_id))
            await s.commit()
        await c.close()


def _score(q: dict[str, Any], res: RetrievalResult, latency_ms: float) -> dict[str, Any]:
    ranked = [_key(ch) for ch in res.chunks]
    relevant = {(r["document"], r["section"]) for r in q["relevant"]}
    top = res.chunks[0] if res.chunks else None
    row: dict[str, Any] = {
        "id": q["id"],
        "split": q["split"],
        "answerable": q["answerable"],
        "latency_ms": round(latency_ms, 2),
        "top_rerank_score": top.rerank_score if top else None,
        "top_dense_distance": top.dense_distance if top else None,
        "returned": [f"{d}#{s}" for d, s in ranked],
        "forbidden_hit": any(d in q.get("forbidden_documents", []) for d, _ in ranked),
    }
    if q["answerable"] and relevant:
        row.update(
            recall=recall_at_k(ranked, relevant, K),
            precision=precision_at_k(ranked, relevant, K),
            mrr=reciprocal_rank(ranked, relevant),
            ndcg=ndcg_at_k(ranked, relevant, K),
        )
    return row


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [r for r in rows if "recall" in r]
    lat = [r["latency_ms"] for r in rows]

    def mean(key: str, split: str | None = None) -> float:
        vals = [r[key] for r in scored if split is None or r["split"] == split]
        return round(statistics.fmean(vals), 4) if vals else float("nan")

    return {
        f"recall@{K}": mean("recall"),
        f"precision@{K}": mean("precision"),
        "mrr": mean("mrr"),
        f"ndcg@{K}": mean("ndcg"),
        "by_split": {
            s: {f"recall@{K}": mean("recall", s), "mrr": mean("mrr", s), f"ndcg@{K}": mean("ndcg", s)}
            for s in ("dev", "test")
        },
        "latency_ms": {"p50": round(percentile(lat, 50), 1), "p95": round(percentile(lat, 95), 1)},
        "n_scored": len(scored),
    }


def _calibrate(raw: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Fit the evidence-sufficiency threshold on the dev split; report accuracy on the held-out test split."""
    out: dict[str, Any] = {}
    for cfg, field, higher_is_better in (
        ("hybrid_rrf+rerank", "top_rerank_score", True),
        ("dense", "top_dense_distance", False),
    ):
        rows = [r for r in raw[cfg] if r[field] is not None]
        sign = 1.0 if higher_is_better else -1.0

        def split(
            name: str, rows: list[dict[str, Any]] = rows, field: str = field, sign: float = sign
        ) -> tuple[list[float], list[float]]:
            pos = [sign * r[field] for r in rows if r["split"] == name and r["answerable"]]
            neg = [sign * r[field] for r in rows if r["split"] == name and not r["answerable"]]
            return pos, neg

        dev_pos, dev_neg = split("dev")
        t, dev_bacc = best_threshold(dev_pos, dev_neg)
        test_pos, test_neg = split("test")
        tpr = sum(s >= t for s in test_pos) / len(test_pos) if test_pos else float("nan")
        tnr = sum(s < t for s in test_neg) / len(test_neg) if test_neg else float("nan")
        out[cfg] = {
            "signal": field,
            "threshold": round(sign * t, 4),
            "rule": f"{field} {'>=' if higher_is_better else '<='} threshold ⇒ sufficient",
            "dev_balanced_accuracy": round(dev_bacc, 4),
            "test_answerable_kept": round(tpr, 4),
            "test_unanswerable_abstained": round(tnr, 4),
            "test_balanced_accuracy": round((tpr + tnr) / 2, 4),
            "n_test": {"answerable": len(test_pos), "unanswerable": len(test_neg)},
        }
    return out


def _permission_checks(raw: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    hits = {cfg: [r["id"] for r in rows if r["forbidden_hit"]] for cfg, rows in raw.items()}
    return {"forbidden_document_returned": hits, "passed": not any(hits.values())}


async def _generation_eval(
    c: Any, ws_id: uuid.UUID, golden: list[dict[str, Any]], abstention: dict[str, Any]
) -> dict[str, Any]:
    if not c.answerer.configured:
        return {
            "status": "skipped",
            "reason": "OPSPILOT_ANTHROPIC_API_KEY not configured; no generation metrics were produced",
        }
    threshold = abstention["hybrid_rrf+rerank"]["threshold"]
    retriever = HybridRetriever(
        c.db, c.embeddings, c.reranker, RetrievalConfig(final_k=K, abstain_min_rerank_score=threshold)
    )
    rows = []
    for q in [g for g in golden if g["split"] == "test"]:
        access = AccessContext(ws_id, frozenset(q["reader_scopes"]))
        res = await retriever.retrieve(q["question"], access)
        outcome = None
        async for ev in c.answerer.stream(q["question"], res):
            if ev["type"] == "done":
                outcome = ev["outcome"]
        assert outcome is not None
        relevant = {(r["document"], r["section"]) for r in q["relevant"]}
        by_id = {str(ch.chunk_id): _key(ch) for ch in res.chunks}
        cited = [by_id[cit.chunk_id] for cit in outcome.citations if cit.chunk_id in by_id]
        text = outcome.answer.lower()
        rows.append(
            {
                "id": q["id"],
                "answerable": q["answerable"],
                "abstained": outcome.abstained,
                "abstention_correct": outcome.abstained == (not q["answerable"]),
                "citation_precision": (sum(k in relevant for k in cited) / len(cited)) if cited and relevant else None,
                "citation_completeness": (len(set(cited) & relevant) / len(relevant)) if relevant else None,
                "facts_present": all(f.lower() in text for f in q["must_include"]) if q["answerable"] else None,
                "forbidden_text_absent": all(f.lower() not in text for f in q.get("must_not_include", [])),
                "rejected_citations": outcome.rejected_citations,
                "usage": outcome.usage.as_dict() if outcome.usage else None,
                "latency_ms": round(outcome.latency_ms, 1),
            }
        )

    def avg(key: str) -> float | None:
        vals = [float(r[key]) for r in rows if r[key] is not None]
        return round(statistics.fmean(vals), 4) if vals else None

    usages = [r["usage"] for r in rows if r["usage"]]
    return {
        "status": "completed",
        "model": c.answerer._model,
        "n": len(rows),
        "abstention_accuracy": avg("abstention_correct"),
        "citation_precision": avg("citation_precision"),
        "citation_completeness": avg("citation_completeness"),
        "required_facts_present": avg("facts_present"),
        "injection_text_absent": avg("forbidden_text_absent"),
        "tokens": {"input": sum(u["input_tokens"] for u in usages), "output": sum(u["output_tokens"] for u in usages)},
        "estimated_cost_usd": round(sum(u["estimated_cost_usd"] for u in usages), 4),
        "latency_ms_p50": round(percentile([r["latency_ms"] for r in rows], 50), 1),
        "rows": rows,
    }


def compare(result: dict[str, Any], baseline: dict[str, Any]) -> list[str]:
    failures = []
    for cfg, metrics in baseline["retrieval"].items():
        for m in (f"recall@{K}", "mrr", f"ndcg@{K}"):
            new, old = result["retrieval"][cfg][m], metrics[m]
            if new < old - TOLERANCE:
                failures.append(f"{cfg} {m}: {new:.4f} < baseline {old:.4f} - {TOLERANCE}")
    if not result["permission_checks"]["passed"]:
        failures.append("A forbidden (out-of-scope) document was returned")
    return failures


async def record(result: dict[str, Any], failures: list[str] | None) -> None:
    from opspilot.db.base import Database
    from opspilot.db.models import EvaluationRun

    db = Database(get_settings().database_url, pool_size=1)
    try:
        async with db.session() as s:
            s.add(
                EvaluationRun(
                    kind="retrieval",
                    run_id=result["run_id"],
                    git_sha=result["git_sha"],
                    dataset=result["dataset"],
                    settings=result["settings"],
                    retrieval=result["retrieval"],
                    abstention=result["abstention"],
                    permission_checks=result["permission_checks"],
                    generation=result["generation"],
                    environment=result["environment"],
                    baseline_comparison={"compared": failures is not None, "regressions": failures or []},
                )
            )
            await s.commit()
    finally:
        await db.dispose()


def summary_markdown(r: dict[str, Any]) -> str:
    lines = [
        f"# RAG evaluation {r['run_id']} (git {r['git_sha']})",
        "",
        f"Dataset: `{r['dataset']['path']}` — {r['dataset']['questions']} questions "
        f"({r['dataset']['answerable']} answerable; dev {r['dataset']['dev']} / test {r['dataset']['test']}). "
        f"Embeddings: `{r['settings']['embedding_model']}`; reranker: `{r['settings']['reranker_model']}`; K={K}.",
        "",
        f"| Configuration | Recall@{K} | Precision@{K} | MRR | nDCG@{K} | p50 ms | p95 ms |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, m in r["retrieval"].items():
        cells = [m[f"recall@{K}"], m[f"precision@{K}"], m["mrr"], m[f"ndcg@{K}"]]
        lat = m["latency_ms"]
        lines.append(f"| {name} | " + " | ".join(f"{v:.3f}" for v in cells) + f" | {lat['p50']} | {lat['p95']} |")
    lines += [
        "",
        "## Evidence-sufficiency (abstention) calibration",
        "",
        "| Signal | Threshold (fit on dev) | Dev bal. acc. | Test answerable kept | Test unanswerable abstained |",
        "|---|---|---|---|---|",
    ]
    for cfg, a in r["abstention"].items():
        n = a["n_test"]
        kept = f"{a['test_answerable_kept']} ({n['answerable']})"
        abstained = f"{a['test_unanswerable_abstained']} ({n['unanswerable']})"
        lines.append(
            f"| {cfg}: {a['signal']} | {a['threshold']} | {a['dev_balanced_accuracy']} | {kept} | {abstained} |"
        )
    lines += [
        "",
        f"Permission checks passed: **{r['permission_checks']['passed']}**",
        "",
        f"Generation: {r['generation'].get('status')} {r['generation'].get('reason', '')}",
        "",
        f"_{r['environment']['note']}_",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--compare-baseline", action="store_true")
    p.add_argument("--update-baseline", action="store_true")
    p.add_argument("--generation", action="store_true", help="Also run generation metrics (needs API key; incurs cost)")
    p.add_argument(
        "--record", action="store_true", help="Store the run in the database (shown on the Evaluations page)"
    )
    args = p.parse_args()
    result = asyncio.run(run(args))
    RUNS.mkdir(parents=True, exist_ok=True)
    out = RUNS / f"rag-{result['run_id']}.json"
    out.write_text(json.dumps(result, indent=2, default=str))
    md = summary_markdown(result)
    (RUNS / f"rag-{result['run_id']}.md").write_text(md)
    print(md)
    print(f"Artifacts: {out}")
    if args.update_baseline:
        keep = {
            k: result[k]
            for k in ("run_id", "git_sha", "settings", "dataset", "retrieval", "abstention", "permission_checks")
        }
        BASELINE.write_text(json.dumps(keep, indent=2) + "\n")
        print(f"Baseline updated: {BASELINE}")
    failures: list[str] | None = None
    if BASELINE.exists():
        failures = compare(result, json.loads(BASELINE.read_text()))
    if args.record:
        asyncio.run(record(result, failures))
        print("Recorded run in the database.")
    if args.compare_baseline:
        if not BASELINE.exists() or failures is None:
            sys.exit("No baseline committed; run with --update-baseline first")
        if failures:
            print("REGRESSION:\n  " + "\n  ".join(failures))
            sys.exit(1)
        print("No regression against baseline.")


if __name__ == "__main__":
    main()
