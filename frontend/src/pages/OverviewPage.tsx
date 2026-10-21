import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";
import { PageHeader } from "../components/Layout";
import { Badge, Card, EmptyState, ErrorBanner, Spinner, Stat, StatusBadge } from "../components/ui";
import { API_BASE, api } from "../lib/api";
import { formatUsd, timeAgo } from "../lib/format";
import type { DocumentSummary, Job, RagQueryRecord, Readiness } from "../lib/types";

export function OverviewPage() {
  const ready = useQuery({
    queryKey: ["readyz"],
    queryFn: async () => (await fetch(`${API_BASE}/readyz`)).json() as Promise<Readiness>,
    refetchInterval: 15_000,
  });
  const docs = useQuery({ queryKey: ["documents"], queryFn: () => api<DocumentSummary[]>("/api/v1/documents") });
  const jobs = useQuery({ queryKey: ["jobs"], queryFn: () => api<Job[]>("/api/v1/ingestion/jobs?limit=8"), refetchInterval: 5000 });
  const queries = useQuery({ queryKey: ["queries"], queryFn: () => api<RagQueryRecord[]>("/api/v1/queries?limit=8") });

  const indexed = docs.data?.filter((d) => d.status === "indexed").length ?? 0;
  const failing = docs.data?.filter((d) => d.status === "failed").length ?? 0;
  const spend = queries.data?.reduce((s, q) => s + (q.usage.estimated_cost_usd ?? 0), 0) ?? 0;

  return (
    <>
      <PageHeader title="Overview" description="Platform readiness, knowledge ingestion and recent activity in this workspace." />
      <div className="space-y-4 p-6">
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <Stat label="Platform" value={ready.data ? <StatusBadge status={ready.data.status} /> : "…"}
            hint={ready.data && (ready.data.generation_configured ? "Answer generation enabled" : "Answer generation not configured")} />
          <Stat label="Indexed documents" value={docs.data ? indexed : "…"} hint={failing ? `${failing} failed` : undefined} />
          <Stat label="Recent questions" value={queries.data?.length ?? "…"} hint="last 8 shown below" />
          <Stat label="Est. model spend (recent)" value={queries.data ? formatUsd(spend) : "…"} hint="from provider-reported tokens × configured prices" />
        </div>
        <div className="grid gap-4 lg:grid-cols-2">
          <Card title="Dependencies">
            {ready.isLoading && <Spinner />}
            {ready.error && <ErrorBanner error={ready.error} />}
            {ready.data && (
              <ul className="divide-y divide-line text-[13px]">
                {Object.entries(ready.data.checks).map(([name, status]) => (
                  <li key={name} className="flex items-center justify-between py-1.5">
                    <span className="text-ink-2">{name.replace("_", " ")}</span>
                    <StatusBadge status={status === "ok" ? "ok" : "failed"} />
                  </li>
                ))}
              </ul>
            )}
          </Card>
          <Card title="Ingestion activity" actions={<Link to="/knowledge" className="text-[12px] text-accent">Knowledge Base →</Link>}>
            {jobs.error && <ErrorBanner error={jobs.error} />}
            {jobs.data?.length === 0 && <EmptyState title="No ingestion jobs yet">Upload documentation to get started.</EmptyState>}
            <ul className="divide-y divide-line text-[13px]">
              {jobs.data?.map((j) => {
                const title = docs.data?.find((d) => d.id === j.document_id)?.title ?? j.document_id.slice(0, 8);
                return (
                  <li key={j.id} className="flex items-center justify-between gap-2 py-1.5">
                    <span className="truncate text-ink-2">{title}</span>
                    <span className="flex shrink-0 items-center gap-2 text-[12px] text-ink-3">
                      {timeAgo(j.created_at)} <StatusBadge status={j.status} />
                    </span>
                  </li>
                );
              })}
            </ul>
          </Card>
        </div>
        <Card title="Recent questions" actions={<Link to="/ask" className="text-[12px] text-accent">Ask →</Link>}>
          {queries.data?.length === 0 && <EmptyState title="No questions asked yet" />}
          <ul className="divide-y divide-line text-[13px]">
            {queries.data?.map((q) => (
              <li key={q.id} className="flex items-center justify-between gap-3 py-1.5">
                <Link to={`/ask?q=${q.id}`} className="truncate text-ink-2 hover:text-ink">{q.query}</Link>
                <span className="flex shrink-0 items-center gap-2 text-[12px] text-ink-3">
                  {q.abstained && <Badge tone="warn">abstained</Badge>}
                  {q.citations.length > 0 && <span>{q.citations.length} citations</span>}
                  <StatusBadge status={q.status} />
                </span>
              </li>
            ))}
          </ul>
        </Card>
      </div>
    </>
  );
}
