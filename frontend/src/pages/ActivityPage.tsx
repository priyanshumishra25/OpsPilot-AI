import { useQuery } from "@tanstack/react-query";
import { PageHeader } from "../components/Layout";
import { Badge, Card, ErrorBanner, Spinner } from "../components/ui";
import { api } from "../lib/api";
import type { AuditEntry } from "../lib/types";

export function ActivityPage() {
  const audit = useQuery({ queryKey: ["audit"], queryFn: () => api<AuditEntry[]>("/api/v1/audit?limit=200") });
  return (
    <>
      <PageHeader title="Activity" description="Audit log of security-relevant actions in this workspace." />
      <div className="p-6">
        <Card>
          {audit.isLoading && <Spinner />}
          {audit.error && <ErrorBanner error={audit.error} />}
          {audit.data && (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-[13px]">
                <thead className="text-[11px] uppercase tracking-wide text-ink-3">
                  <tr><th className="py-2 pr-4">Time</th><th className="pr-4">Action</th><th className="pr-4">Resource</th><th className="pr-4">Outcome</th><th>Details</th></tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {audit.data.map((a) => (
                    <tr key={a.id}>
                      <td className="whitespace-nowrap py-1.5 pr-4 text-ink-3">{new Date(a.created_at).toLocaleString()}</td>
                      <td className="pr-4 font-mono text-[12px]">{a.action}</td>
                      <td className="pr-4 text-ink-2">{a.resource_type}{a.resource_id ? ` ${a.resource_id.slice(0, 8)}` : ""}</td>
                      <td className="pr-4"><Badge tone={a.outcome === "success" ? "ok" : "bad"}>{a.outcome}</Badge></td>
                      <td className="max-w-md truncate font-mono text-[11px] text-ink-3">{JSON.stringify(a.details)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>
    </>
  );
}
