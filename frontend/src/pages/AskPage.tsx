import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Square, SendHorizontal } from "lucide-react";
import { useEffect, useState, type SyntheticEvent } from "react";
import { useSearchParams } from "react-router";
import { AnswerText, CitationCard, EvidencePanel } from "../components/Answer";
import { PageHeader } from "../components/Layout";
import { Badge, Button, ErrorBanner, Spinner, StatusBadge } from "../components/ui";
import { api } from "../lib/api";
import { formatUsd, timeAgo } from "../lib/format";
import type { RagQueryRecord } from "../lib/types";
import { useAnswerStream, type AnswerState } from "../lib/useAnswerStream";

const EXAMPLES = [
  "What is the documented procedure for recovering the checkout service after a database connection failure?",
  "Who has to approve a production rollback?",
  "What caused the March 2026 orders API outage?",
];

function fromRecord(r: RagQueryRecord): AnswerState {
  return {
    status: r.status === "succeeded" ? "done" : "error", queryId: r.id, traceId: r.trace_id, text: r.answer ?? "",
    markers: r.diagnostics.citation_spans?.map((s) => ({ offset: s.offset, index: s.citation })) ?? [],
    citations: r.citations, retrieval: r.diagnostics.results ? (r.diagnostics as AnswerState["retrieval"]) : null,
    usage: r.usage.model ? (r.usage as AnswerState["usage"]) : null, abstained: r.abstained, latencyMs: r.latency_ms,
    error: r.error ? { code: r.status, message: r.error } : null,
  };
}

export function AskPage() {
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const [question, setQuestion] = useState("");
  const [submitted, setSubmitted] = useState<string | null>(null);
  const [activeCitation, setActiveCitation] = useState<number | null>(null);
  const { state: live, ask, cancel, reset } = useAnswerStream();
  const history = useQuery({ queryKey: ["queries"], queryFn: () => api<RagQueryRecord[]>("/api/v1/queries?limit=30") });
  const historicId = params.get("q");
  const historic = useQuery({ queryKey: ["query", historicId], queryFn: () => api<RagQueryRecord>(`/api/v1/queries/${historicId ?? ""}`), enabled: !!historicId && live.status === "idle" });

  const state = historicId && historic.data && live.status === "idle" ? fromRecord(historic.data) : live;
  const shownQuestion = historicId && historic.data && live.status === "idle" ? historic.data.query : submitted;
  const busy = live.status === "retrieving" || live.status === "generating";

  useEffect(() => {
    if (live.status === "done" || live.status === "error") void qc.invalidateQueries({ queryKey: ["queries"] });
  }, [live.status, qc]);

  function submit(e?: SyntheticEvent, text: string = question) {
    e?.preventDefault();
    const q = text.trim();
    if (q.length < 2 || busy) return;
    setParams({});
    setSubmitted(q);
    setActiveCitation(null);
    void ask(q);
  }

  return (
    <>
      <PageHeader title="Ask" description="Grounded answers from your engineering documentation, with verifiable citations." />
      <div className="grid gap-4 p-6 xl:grid-cols-[1fr_18rem]">
        <div className="min-w-0 space-y-4">
          <form onSubmit={submit} className="flex gap-2">
            <label htmlFor="question" className="sr-only">Question</label>
            <textarea id="question" rows={2} value={question} onChange={(e) => { setQuestion(e.target.value); }} maxLength={2000}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); } }}
              placeholder="Ask about runbooks, architecture, deployment policy…"
              className="min-h-[3.25rem] flex-1 resize-y rounded-md border border-line bg-panel px-3 py-2 text-[14px]" />
            {busy
              ? <Button type="button" variant="ghost" onClick={cancel} aria-label="Stop generating"><Square className="size-4" />Stop</Button>
              : <Button type="submit" disabled={question.trim().length < 2} aria-label="Ask"><SendHorizontal className="size-4" />Ask</Button>}
          </form>

          {state.status === "idle" && !historicId && (
            <div className="flex flex-wrap gap-2">
              {EXAMPLES.map((ex) => (
                <button key={ex} onClick={() => { setQuestion(ex); submit(undefined, ex); }} className="rounded-full border border-line px-3 py-1 text-[12px] text-ink-2 hover:bg-panel-2">{ex}</button>
              ))}
            </div>
          )}
          {historic.isLoading && <Spinner />}

          {shownQuestion && state.status !== "idle" && (
            <article className="space-y-3" aria-live="polite" aria-busy={busy}>
              <p className="text-[13px] text-ink-3">Q: <span className="text-ink-2">{shownQuestion}</span></p>
              {state.status === "retrieving" && <Spinner label="Searching documentation" />}
              {state.retrieval && <EvidencePanel retrieval={state.retrieval} />}
              {(state.text || state.status === "generating") && (
                <div className="rounded-lg border border-line bg-panel p-4">
                  {state.abstained && <div className="mb-2"><Badge tone="warn">insufficient evidence</Badge></div>}
                  <AnswerText text={state.text} markers={state.markers} onCite={(i) => { setActiveCitation(i); document.getElementById(`citation-${i}`)?.scrollIntoView({ block: "nearest" }); }} />
                  {state.status === "generating" && !state.text && <Spinner label="Generating answer" />}
                </div>
              )}
              {state.status === "cancelled" && <p className="text-[13px] text-warn">Stopped. The partial answer above was not saved as complete.</p>}
              {state.error && (
                <ErrorBanner error={new Error(state.error.code === "not_configured"
                  ? "Answer generation is not configured (no model API key). The retrieved evidence above is still shown."
                  : state.error.message)} onRetry={shownQuestion ? () => { submit(undefined, shownQuestion); } : undefined} />
              )}
              {state.citations.length > 0 && (
                <section aria-label="Sources" className="grid gap-2 md:grid-cols-2">
                  {state.citations.map((c) => (
                    <CitationCard key={c.chunk_id} citation={c} active={activeCitation === c.index} onSelect={() => { setActiveCitation(activeCitation === c.index ? null : c.index); }} />
                  ))}
                </section>
              )}
              {state.status === "done" && (
                <p className="text-[12px] text-ink-3">
                  {state.latencyMs !== null && `${(state.latencyMs / 1000).toFixed(1)} s`}
                  {state.usage && ` · ${state.usage.input_tokens.toLocaleString()} in / ${state.usage.output_tokens.toLocaleString()} out tokens · est. ${formatUsd(state.usage.estimated_cost_usd)} (${state.usage.served_by})`}
                  {state.usage?.fallback_used && " · served by fallback model"}
                  {state.traceId && <span className="font-mono"> · trace {state.traceId.slice(0, 16)}</span>}
                </p>
              )}
            </article>
          )}
        </div>

        <aside aria-label="Question history" className="space-y-2">
          <div className="flex items-center justify-between">
            <h2 className="text-[12px] font-semibold uppercase tracking-wide text-ink-3">History</h2>
            {(state.status !== "idle" || historicId) && <button className="text-[12px] text-accent" onClick={() => { reset(); setParams({}); setSubmitted(null); }}>New question</button>}
          </div>
          {history.error && <ErrorBanner error={history.error} />}
          <ul className="space-y-1">
            {history.data?.map((h) => (
              <li key={h.id}>
                <button onClick={() => { reset(); setParams({ q: h.id }); }} className={`w-full rounded-md px-2 py-1.5 text-left text-[12px] hover:bg-panel-2 ${historicId === h.id ? "bg-panel-2" : ""}`}>
                  <span className="line-clamp-2 text-ink-2">{h.query}</span>
                  <span className="mt-0.5 flex items-center gap-1 text-[11px] text-ink-3">{timeAgo(h.created_at)} <StatusBadge status={h.status} /></span>
                </button>
              </li>
            ))}
          </ul>
        </aside>
      </div>
    </>
  );
}
