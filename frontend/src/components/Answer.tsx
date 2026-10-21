import { useQuery } from "@tanstack/react-query";
import { ExternalLink } from "lucide-react";
import { Fragment, useState } from "react";
import { Link } from "react-router";
import { api } from "../lib/api";
import type { Chunk, Citation, RetrievalDiagnostics } from "../lib/types";
import type { CitationMarker } from "../lib/useAnswerStream";
import { Badge, ErrorBanner, Spinner } from "./ui";

/** Render answer text with citation markers placed where the model attached each citation. */
export function AnswerText({ text, markers, onCite }: { text: string; markers: CitationMarker[]; onCite: (index: number) => void }) {
  const sorted = [...markers].sort((a, b) => a.offset - b.offset);
  const parts: React.ReactNode[] = [];
  let cursor = 0;
  sorted.forEach((m, i) => {
    const at = Math.min(m.offset, text.length);
    if (at > cursor) parts.push(<Fragment key={`t${i}`}>{text.slice(cursor, at)}</Fragment>);
    cursor = Math.max(cursor, at);
    const dup = i > 0 && sorted[i - 1]?.offset === m.offset && sorted[i - 1]?.index === m.index;
    if (!dup) {
      parts.push(
        <button key={`c${i}`} onClick={() => { onCite(m.index); }} aria-label={`Show source ${m.index}`}
          className="mx-0.5 inline-grid h-4 min-w-4 place-items-center rounded bg-accent/15 px-1 align-super text-[10px] font-semibold text-accent hover:bg-accent/25">
          {m.index}
        </button>,
      );
    }
  });
  if (cursor < text.length) parts.push(<Fragment key="tail">{text.slice(cursor)}</Fragment>);
  return <div className="whitespace-pre-wrap text-[14px] leading-relaxed text-ink">{parts}</div>;
}

export function CitationCard({ citation, active, onSelect }: { citation: Citation; active: boolean; onSelect: () => void }) {
  return (
    <div id={`citation-${citation.index}`} className={`rounded-md border p-3 text-[13px] ${active ? "border-accent" : "border-line"} bg-panel`}>
      <button onClick={onSelect} className="flex w-full items-start gap-2 text-left" aria-expanded={active}>
        <span className="grid size-5 shrink-0 place-items-center rounded bg-accent/15 text-[11px] font-semibold text-accent">{citation.index}</span>
        <span className="min-w-0">
          <span className="block truncate font-medium">{citation.document_title}</span>
          <span className="block truncate text-[12px] text-ink-3">{citation.section_path.join(" › ")}{citation.page_number ? ` · p.${citation.page_number}` : ""}</span>
        </span>
      </button>
      <blockquote className="mt-2 border-l-2 border-accent/40 pl-2 text-[12px] italic text-ink-2">“{citation.cited_text}”</blockquote>
      {active && <PassagePreview citation={citation} />}
    </div>
  );
}

function PassagePreview({ citation }: { citation: Citation }) {
  const q = useQuery({ queryKey: ["chunk", citation.chunk_id], queryFn: () => api<Chunk>(`/api/v1/chunks/${citation.chunk_id}`) });
  if (q.isLoading) return <div className="mt-2"><Spinner label="Loading passage" /></div>;
  if (q.error) return <div className="mt-2"><ErrorBanner error={q.error} /></div>;
  if (!q.data) return null;
  const content = q.data.content;
  const at = content.indexOf(citation.cited_text);
  return (
    <div className="mt-2 space-y-2">
      <p className="max-h-60 overflow-y-auto whitespace-pre-wrap rounded bg-canvas p-2 text-[12px] leading-relaxed text-ink-2">
        {at >= 0 ? (<>{content.slice(0, at)}<mark className="rounded bg-accent/20 text-ink">{citation.cited_text}</mark>{content.slice(at + citation.cited_text.length)}</>) : content}
      </p>
      <Link to={`/knowledge?doc=${citation.document_id}`} className="inline-flex items-center gap-1 text-[12px] text-accent">
        Open document <ExternalLink className="size-3" />
      </Link>
    </div>
  );
}

export function EvidencePanel({ retrieval }: { retrieval: RetrievalDiagnostics }) {
  const [open, setOpen] = useState(false);
  const total = Object.values(retrieval.timings_ms).reduce((a, b) => a + b, 0);
  return (
    <div className="rounded-md border border-line bg-panel text-[13px]">
      <button onClick={() => { setOpen(!open); }} aria-expanded={open} className="flex w-full flex-wrap items-center gap-2 px-3 py-2 text-left">
        <span className="font-medium">Retrieval evidence</span>
        <Badge>{retrieval.strategy}</Badge>
        <Badge>{retrieval.query_class}</Badge>
        <Badge tone={retrieval.evidence_sufficient ? "ok" : "warn"}>{retrieval.evidence_sufficient ? "evidence sufficient" : "insufficient evidence"}</Badge>
        <span className="ml-auto text-[12px] text-ink-3">{retrieval.returned} of {retrieval.candidates_considered} candidates · {Math.round(total)} ms</span>
      </button>
      {open && (
        <div className="border-t border-line px-3 py-2">
          <p className="mb-2 text-[12px] text-ink-3">
            Timings: {Object.entries(retrieval.timings_ms).map(([k, v]) => `${k} ${Math.round(v)} ms`).join(" · ")}
            {retrieval.top_rerank_score !== null && ` · top rerank score ${retrieval.top_rerank_score.toFixed(2)}`}
          </p>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-[12px]">
              <thead className="text-[11px] uppercase text-ink-3"><tr><th className="py-1 pr-2">#</th><th className="pr-2">Passage</th><th className="pr-2">Dense</th><th className="pr-2">Lexical</th><th className="pr-2">RRF</th><th>Rerank</th></tr></thead>
              <tbody className="divide-y divide-line">
                {retrieval.results.map((r, i) => (
                  <tr key={r.chunk_id}>
                    <td className="py-1 pr-2 text-ink-3">{i + 1}</td>
                    <td className="max-w-xs pr-2"><span className="block truncate">{r.document_title}</span><span className="block truncate text-ink-3">{r.section_path.join(" › ")}</span></td>
                    <td className="pr-2 tabular-nums">{r.dense_rank ?? "—"}</td>
                    <td className="pr-2 tabular-nums">{r.lexical_rank ?? "—"}</td>
                    <td className="pr-2 tabular-nums">{r.fused_score.toFixed(4)}</td>
                    <td className="tabular-nums">{r.rerank_score?.toFixed(2) ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
