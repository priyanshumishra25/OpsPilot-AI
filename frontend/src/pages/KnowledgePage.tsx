import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileText, RefreshCw, Trash2, Upload, X } from "lucide-react";
import { useRef, useState, type DragEvent } from "react";
import { useSearchParams } from "react-router";
import { PageHeader } from "../components/Layout";
import { Badge, Button, Card, DemoBadge, EmptyState, ErrorBanner, Spinner, StatusBadge } from "../components/ui";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatBytes, timeAgo } from "../lib/format";
import type { Chunk, DocumentDetail, DocumentSummary } from "../lib/types";

const ACCEPT = ".md,.markdown,.txt,.pdf";

export function KnowledgePage() {
  const { can } = useAuth();
  const [params, setParams] = useSearchParams();
  const selected = params.get("doc");
  const docs = useQuery({
    queryKey: ["documents"],
    queryFn: () => api<DocumentSummary[]>("/api/v1/documents"),
    refetchInterval: (q) =>
      q.state.data?.some((d) => d.status === "pending" || ["queued", "running", "retry_scheduled"].includes(d.latest_job?.status ?? "")) ? 2000 : false,
  });

  return (
    <>
      <PageHeader title="Knowledge Base" description="Engineering documentation indexed for grounded answers. Markdown, plain text and PDF." />
      <div className="grid gap-4 p-6 xl:grid-cols-[1fr_28rem]">
        <div className="space-y-4">
          {can("documents:write") && <UploadPanel />}
          <Card title={`Documents${docs.data ? ` (${docs.data.length})` : ""}`}>
            {docs.isLoading && <Spinner />}
            {docs.error && <ErrorBanner error={docs.error} onRetry={() => void docs.refetch()} />}
            {docs.data?.length === 0 && <EmptyState title="No documents yet">Upload runbooks, architecture notes or postmortems.</EmptyState>}
            {docs.data && docs.data.length > 0 && (
              <div className="overflow-x-auto">
                <table className="w-full text-left text-[13px]">
                  <thead className="text-[11px] uppercase tracking-wide text-ink-3">
                    <tr><th className="py-2 pr-3">Title</th><th className="pr-3">Scope</th><th className="pr-3">Status</th><th className="pr-3">Passages</th><th>Updated</th></tr>
                  </thead>
                  <tbody className="divide-y divide-line">
                    {docs.data.map((d) => (
                      <tr key={d.id} className={selected === d.id ? "bg-panel-2" : undefined}>
                        <td className="py-2 pr-3">
                          <button className="flex items-center gap-2 text-left hover:text-accent" onClick={() => { setParams({ doc: d.id }); }}>
                            <FileText className="size-4 shrink-0 text-ink-3" aria-hidden />
                            <span className="truncate">{d.title}</span>
                          </button>
                          <div className="ml-6 mt-0.5 flex gap-1">{d.is_demo_seed && <DemoBadge />}</div>
                        </td>
                        <td className="pr-3"><Badge tone={d.access_scope === "general" ? "neutral" : "info"}>{d.access_scope}</Badge></td>
                        <td className="pr-3">
                          <StatusBadge status={d.latest_job && d.latest_job.status !== "succeeded" ? d.latest_job.status : d.status} />
                          {d.latest_job?.status === "running" && <span className="ml-1 text-[11px] text-ink-3">{d.latest_job.stage}</span>}
                        </td>
                        <td className="pr-3 tabular-nums text-ink-2">{d.current_version?.chunk_count ?? "—"}</td>
                        <td className="whitespace-nowrap text-ink-3">{timeAgo(d.updated_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </div>
        {selected && <DocumentPanel id={selected} onClose={() => { setParams({}); }} />}
      </div>
    </>
  );
}

function UploadPanel() {
  const { me } = useAuth();
  const qc = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const [scope, setScope] = useState("general");
  const [dragging, setDragging] = useState(false);
  const [results, setResults] = useState<{ name: string; ok: boolean; message: string }[]>([]);
  const upload = useMutation({
    mutationFn: async (files: File[]) => {
      const out: { name: string; ok: boolean; message: string }[] = [];
      for (const f of files) {
        const form = new FormData();
        form.append("file", f);
        form.append("access_scope", scope);
        try {
          const r = await api<{ deduplicated: boolean }>("/api/v1/documents", { method: "POST", body: form });
          out.push({ name: f.name, ok: true, message: r.deduplicated ? "unchanged (identical content already indexed)" : "queued for ingestion" });
        } catch (e) {
          out.push({ name: f.name, ok: false, message: e instanceof Error ? e.message : "upload failed" });
        }
      }
      return out;
    },
    onSuccess: (out) => { setResults(out); void qc.invalidateQueries({ queryKey: ["documents"] }); },
  });
  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    upload.mutate([...e.dataTransfer.files]);
  };
  return (
    <Card title="Upload documentation">
      <div
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => { setDragging(false); }}
        onDrop={onDrop}
        className={`flex flex-wrap items-center gap-3 rounded-md border border-dashed px-4 py-4 ${dragging ? "border-accent bg-accent/5" : "border-line"}`}
      >
        <Upload className="size-5 text-ink-3" aria-hidden />
        <p className="flex-1 text-[13px] text-ink-2">Drop files here or choose files. Max 20 MB each; re-uploading a file with the same name creates a new version.</p>
        <label className="text-[12px] text-ink-3">
          Access scope{" "}
          <select value={scope} onChange={(e) => { setScope(e.target.value); }} className="ml-1 rounded border border-line bg-canvas px-2 py-1 text-[12px] text-ink">
            {(me?.access_scopes ?? ["general"]).map((s) => <option key={s}>{s}</option>)}
          </select>
        </label>
        <input ref={input} type="file" multiple accept={ACCEPT} className="sr-only" aria-label="Choose files to upload"
          onChange={(e) => { if (e.target.files) upload.mutate([...e.target.files]); e.target.value = ""; }} />
        <Button onClick={() => input.current?.click()} loading={upload.isPending}>Choose files</Button>
      </div>
      {results.length > 0 && (
        <ul className="mt-3 space-y-1 text-[12px]" aria-live="polite">
          {results.map((r) => (
            <li key={r.name} className={r.ok ? "text-ink-2" : "text-bad"}><span className="font-medium">{r.name}</span>: {r.message}</li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function DocumentPanel({ id, onClose }: { id: string; onClose: () => void }) {
  const { can } = useAuth();
  const qc = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const detail = useQuery({ queryKey: ["document", id], queryFn: () => api<DocumentDetail>(`/api/v1/documents/${id}`), refetchInterval: 3000 });
  const chunks = useQuery({ queryKey: ["document", id, "chunks", detail.data?.current_version?.id], queryFn: () => api<Chunk[]>(`/api/v1/documents/${id}/chunks`), enabled: !!detail.data?.current_version });
  const reindex = useMutation({
    mutationFn: () => api(`/api/v1/documents/${id}/reindex`, { method: "POST" }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["documents"] }),
  });
  const remove = useMutation({
    mutationFn: () => api(`/api/v1/documents/${id}`, { method: "DELETE" }),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ["documents"] }); onClose(); },
  });
  const d = detail.data;
  return (
    <Card
      className="xl:sticky xl:top-4 xl:self-start"
      title={d?.title ?? "Document"}
      actions={<button onClick={onClose} aria-label="Close document panel" className="rounded p-1 text-ink-3 hover:bg-panel-2"><X className="size-4" /></button>}
    >
      {detail.isLoading && <Spinner />}
      {detail.error && <ErrorBanner error={detail.error} />}
      {d && (
        <div className="space-y-4 text-[13px]">
          <dl className="grid grid-cols-[7rem_1fr] gap-y-1">
            <dt className="text-ink-3">Source</dt><dd className="truncate font-mono text-[12px]">{d.source_uri}</dd>
            <dt className="text-ink-3">Scope</dt><dd>{d.access_scope}</dd>
            <dt className="text-ink-3">Status</dt><dd><StatusBadge status={d.status} /></dd>
            {d.current_version && (<>
              <dt className="text-ink-3">Live version</dt>
              <dd>v{d.current_version.version_number} · {formatBytes(d.current_version.size_bytes)} · {d.current_version.chunk_count} passages</dd>
              <dt className="text-ink-3">Content hash</dt><dd className="truncate font-mono text-[11px]">{d.current_version.content_hash}</dd>
            </>)}
          </dl>
          {(can("documents:write") || can("documents:delete")) && (
            <div className="flex gap-2">
              {can("documents:write") && <Button variant="ghost" onClick={() => { reindex.mutate(); }} loading={reindex.isPending}><RefreshCw className="size-3.5" />Re-index</Button>}
              {can("documents:delete") && (confirming
                ? <Button variant="danger" onClick={() => { remove.mutate(); }} loading={remove.isPending}>Confirm delete</Button>
                : <Button variant="danger" onClick={() => { setConfirming(true); }}><Trash2 className="size-3.5" />Delete</Button>)}
            </div>
          )}
          {(reindex.error ?? remove.error) && <ErrorBanner error={reindex.error ?? remove.error} />}
          <section>
            <h3 className="mb-1 text-[12px] font-semibold uppercase tracking-wide text-ink-3">Ingestion jobs</h3>
            <ul className="divide-y divide-line">
              {d.jobs.map((j) => (
                <li key={j.id} className="py-1.5">
                  <div className="flex items-center justify-between">
                    <span className="text-ink-2">{timeAgo(j.created_at)} · attempt {j.attempts}/{j.max_attempts}</span>
                    <StatusBadge status={j.status} />
                  </div>
                  {j.error && <p className="mt-0.5 text-[12px] text-bad">{j.error_class}: {j.error}</p>}
                  {j.status === "succeeded" && (
                    <p className="text-[11px] text-ink-3">
                      {j.timings_ms.chunks} passages · {j.timings_ms.embeddings_reused ?? 0} embeddings reused · embed {Math.round(j.timings_ms.embed_ms ?? 0)} ms
                    </p>
                  )}
                </li>
              ))}
            </ul>
          </section>
          <section>
            <h3 className="mb-1 text-[12px] font-semibold uppercase tracking-wide text-ink-3">Versions</h3>
            <ul className="space-y-1">
              {d.versions.map((v) => (
                <li key={v.id} className="flex items-center justify-between">
                  <span>v{v.version_number} · {v.original_filename} · {timeAgo(v.created_at)}</span>
                  <StatusBadge status={v.status} />
                </li>
              ))}
            </ul>
          </section>
          <section>
            <h3 className="mb-1 text-[12px] font-semibold uppercase tracking-wide text-ink-3">Indexed passages</h3>
            {chunks.isLoading && <Spinner />}
            <ol className="max-h-[28rem] space-y-2 overflow-y-auto pr-1">
              {chunks.data?.map((c) => (
                <li key={c.id} id={`chunk-${c.id}`} className="rounded border border-line bg-canvas p-2">
                  <p className="mb-1 text-[11px] text-ink-3">#{c.ordinal} · {c.section_path.join(" › ") || "—"}{c.page_number ? ` · p.${c.page_number}` : ""}</p>
                  <p className="whitespace-pre-wrap text-[12px] leading-relaxed text-ink-2">{c.content}</p>
                </li>
              ))}
            </ol>
          </section>
        </div>
      )}
    </Card>
  );
}
