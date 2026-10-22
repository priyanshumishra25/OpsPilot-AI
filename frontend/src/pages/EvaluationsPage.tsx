import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { HBarChart, TrendChart } from "../components/charts";
import { PageHeader } from "../components/Layout";
import { Badge, Card, EmptyState, ErrorBanner, Spinner } from "../components/ui";
import { api } from "../lib/api";
import type { EvaluationRun } from "../lib/types";

const METRICS = [
  { key: "recall@5", label: "Recall@5" },
  { key: "mrr", label: "MRR" },
  { key: "ndcg@5", label: "nDCG@5" },
  { key: "precision@5", label: "Precision@5" },
] as const;
type MetricKey = (typeof METRICS)[number]["key"];
const DEFAULT_CONFIG = "hybrid_rrf+rerank@20";

export function EvaluationsPage() {
  const runs = useQuery({ queryKey: ["evaluations"], queryFn: () => api<EvaluationRun[]>("/api/v1/evaluations") });
  const [metric, setMetric] = useState<MetricKey>("recall@5");
  const [tables, setTables] = useState(false);
  const latest = runs.data?.[0];
  const configs = latest ? Object.entries(latest.retrieval) : [];
  const quality = configs.map(([label, m]) => ({ label, value: m[metric] })).sort((a, b) => b.value - a.value);
  const latency = configs.map(([label, m]) => ({ label, value: m.latency_ms.p50 })).sort((a, b) => a.value - b.value);
  const trend = [...(runs.data ?? [])].reverse()
    .filter((r) => DEFAULT_CONFIG in r.retrieval)
    .map((r) => ({ label: r.run_id.slice(4, 13), value: r.retrieval[DEFAULT_CONFIG]?.[metric] ?? 0 }));
  const metricLabel = METRICS.find((m) => m.key === metric)?.label ?? metric;

  return (
    <>
      <PageHeader title="Evaluations" description="Measured retrieval quality on the golden set. Numbers come only from recorded runs (python -m opspilot.evaluation.rag_eval --record)." />
      <div className="space-y-4 p-6">
        {runs.isLoading && <Spinner />}
        {runs.error && <ErrorBanner error={runs.error} />}
        {runs.data?.length === 0 && <EmptyState title="No evaluation runs recorded">Run the evaluation with --record to populate this page.</EmptyState>}
        {latest && (
          <>
            <div className="flex flex-wrap items-center gap-2 text-[13px]">
              <span className="text-ink-3">Latest run</span><span className="font-mono">{latest.run_id}</span>
              <span className="text-ink-3">git</span><span className="font-mono">{latest.git_sha}</span>
              <Badge>{latest.dataset.questions} questions (dev {latest.dataset.dev} / test {latest.dataset.test})</Badge>
              <Badge tone={latest.permission_checks.passed ? "ok" : "bad"}>{latest.permission_checks.passed ? "permission checks passed" : "permission check FAILED"}</Badge>
              {latest.baseline_comparison.compared && (
                <Badge tone={latest.baseline_comparison.regressions?.length ? "bad" : "ok"}>
                  {latest.baseline_comparison.regressions?.length ? `${latest.baseline_comparison.regressions.length} regressions vs baseline` : "no regression vs baseline"}
                </Badge>
              )}
            </div>
            <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Metric">
              {METRICS.map((m) => (
                <button key={m.key} aria-pressed={metric === m.key} onClick={() => { setMetric(m.key); }}
                  className={`rounded-md px-3 py-1 text-[13px] ${metric === m.key ? "bg-panel-2 font-medium" : "text-ink-3 hover:bg-panel-2"}`}>{m.label}</button>
              ))}
              <label className="ml-auto flex items-center gap-1 text-[12px] text-ink-3">
                <input type="checkbox" checked={tables} onChange={(e) => { setTables(e.target.checked); }} /> Show tables
              </label>
            </div>
            <div className="grid gap-4 xl:grid-cols-2">
              <Card title={`${metricLabel} by retrieval configuration`}>
                {tables ? <MetricTable run={latest} /> : <HBarChart data={quality} unit="ratio" domain={[0, 1]} />}
              </Card>
              <Card title="Retrieval latency p50 (in-process, single laptop)">
                {tables ? <MetricTable run={latest} /> : <HBarChart data={latency} unit="ms" />}
              </Card>
            </div>
            <Card title={`${metricLabel} over runs (${DEFAULT_CONFIG})`}>
              {trend.length < 2 ? <p className="text-[13px] text-ink-3">Trend appears after two or more recorded runs ({trend.length} so far).</p> : <TrendChart data={trend} />}
            </Card>
            <Card title="Evidence-sufficiency calibration (threshold fit on dev, reported on held-out test)">
              <table className="w-full text-left text-[13px]">
                <thead className="text-[11px] uppercase tracking-wide text-ink-3"><tr><th className="py-1">Signal</th><th>Threshold</th><th>Test answerable kept</th><th>Test unanswerable abstained</th></tr></thead>
                <tbody className="divide-y divide-line">
                  {Object.entries(latest.abstention).map(([cfg, a]) => (
                    <tr key={cfg}><td className="py-1.5">{cfg}: <span className="font-mono text-[12px]">{a.signal}</span></td><td className="tabular-nums">{a.threshold}</td>
                      <td className="tabular-nums">{Math.round(a.test_answerable_kept * a.n_test.answerable)}/{a.n_test.answerable}</td>
                      <td className="tabular-nums">{Math.round(a.test_unanswerable_abstained * a.n_test.unanswerable)}/{a.n_test.unanswerable}
                        {a.n_test.unanswerable < 10 && <span className="ml-1 text-[11px] text-ink-3">small sample</span>}</td></tr>
                  ))}
                </tbody>
              </table>
            </Card>
            <Card title="Answer generation">
              <p className="text-[13px]">Status: <Badge tone={latest.generation.status === "completed" ? "ok" : "warn"}>{latest.generation.status}</Badge>{" "}
                <span className="text-ink-3">{latest.generation.reason ?? (latest.generation.status === "not requested" ? "Generation metrics need a model API key and the --generation flag; none were produced." : "")}</span></p>
            </Card>
            {latest.environment.note && <p className="text-[12px] text-ink-3">{latest.environment.note}</p>}
          </>
        )}
      </div>
    </>
  );
}

function MetricTable({ run }: { run: EvaluationRun }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-[12px]">
        <thead className="text-[11px] uppercase tracking-wide text-ink-3">
          <tr><th className="py-1 pr-2">Configuration</th>{METRICS.map((m) => <th key={m.key} className="pr-2">{m.label}</th>)}<th className="pr-2">p50 ms</th><th>p95 ms</th></tr>
        </thead>
        <tbody className="divide-y divide-line tabular-nums">
          {Object.entries(run.retrieval).map(([cfg, m]) => (
            <tr key={cfg}><td className="py-1 pr-2">{cfg}</td>{METRICS.map((x) => <td key={x.key} className="pr-2">{m[x.key].toFixed(3)}</td>)}
              <td className="pr-2">{m.latency_ms.p50}</td><td>{m.latency_ms.p95}</td></tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
