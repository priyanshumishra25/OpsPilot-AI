import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router";
import { ApprovalCard } from "../components/ApprovalCard";
import { PageHeader } from "../components/Layout";
import { ReportView } from "../components/Report";
import { Badge, Card, EmptyState, ErrorBanner, Spinner, StatusBadge } from "../components/ui";
import { api } from "../lib/api";
import { timeAgo } from "../lib/format";
import type { Approval, Incident } from "../lib/types";

export function IncidentsPage() {
  const { id } = useParams();
  const list = useQuery({ queryKey: ["incidents"], queryFn: () => api<Incident[]>("/api/v1/incidents") });
  return (
    <>
      <PageHeader title="Incidents" description="Investigation reports, evidence, proposed actions and their approval history." />
      <div className="grid gap-4 p-6 xl:grid-cols-[20rem_1fr]">
        <aside aria-label="Incident list" className="space-y-1">
          {list.isLoading && <Spinner />}
          {list.error && <ErrorBanner error={list.error} />}
          {list.data?.length === 0 && <EmptyState title="No incidents yet">Investigations that produce a report appear here.</EmptyState>}
          {list.data?.map((i) => (
            <Link key={i.id} to={`/incidents/${i.id}`} className={`block rounded-md border px-3 py-2 text-[13px] hover:bg-panel-2 ${i.id === id ? "border-accent" : "border-line"}`}>
              <span className="flex items-center gap-2"><Badge tone={i.severity === "sev1" ? "bad" : "warn"}>{i.severity}</Badge><span className="truncate font-medium">{i.title}</span></span>
              <span className="mt-0.5 block text-[11px] text-ink-3">{i.service ?? "—"} · {timeAgo(i.created_at)}</span>
            </Link>
          ))}
        </aside>
        <div className="min-w-0">{id ? <IncidentDetail id={id} /> : <EmptyState title="Select an incident" />}</div>
      </div>
    </>
  );
}

function IncidentDetail({ id }: { id: string }) {
  const q = useQuery({ queryKey: ["incident", id], queryFn: () => api<Incident>(`/api/v1/incidents/${id}`) });
  const approvals = useQuery({ queryKey: ["approvals", "all"], queryFn: () => api<Approval[]>("/api/v1/approvals") });
  if (q.isLoading) return <Spinner />;
  if (q.error) return <ErrorBanner error={q.error} />;
  if (!q.data) return null;
  const linked = approvals.data?.filter((a) => a.incident_id === id) ?? [];
  return (
    <div className="space-y-4">
      <Card title={<span className="flex items-center gap-2">{q.data.title} <StatusBadge status={q.data.status === "open" ? "pending" : q.data.status} /></span>}>
        <ReportView report={q.data.report} />
      </Card>
      <Card title="Actions and approvals">
        {linked.length === 0 ? <EmptyState title="No actions requested for this incident" /> : <div className="space-y-2">{linked.map((a) => <ApprovalCard key={a.id} approval={a} />)}</div>}
      </Card>
    </div>
  );
}
