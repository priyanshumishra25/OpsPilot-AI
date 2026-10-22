import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { ApprovalCard } from "../components/ApprovalCard";
import { PageHeader } from "../components/Layout";
import { EmptyState, ErrorBanner, Spinner } from "../components/ui";
import { api } from "../lib/api";
import type { Approval } from "../lib/types";

const FILTERS = ["pending", "all"] as const;

export function ApprovalsPage() {
  const [filter, setFilter] = useState<(typeof FILTERS)[number]>("pending");
  const q = useQuery({
    queryKey: ["approvals", filter],
    queryFn: () => api<Approval[]>(`/api/v1/approvals${filter === "pending" ? "?status=pending" : ""}`),
    refetchInterval: 5000,
  });
  return (
    <>
      <PageHeader title="Approvals" description="Privileged actions requested by the agent or engineers. Each approval authorises one exact action, once, before it expires." />
      <div className="space-y-3 p-6">
        <div role="tablist" className="flex gap-1">
          {FILTERS.map((f) => (
            <button key={f} role="tab" aria-selected={filter === f} onClick={() => { setFilter(f); }}
              className={`rounded-md px-3 py-1 text-[13px] ${filter === f ? "bg-panel-2 font-medium" : "text-ink-3 hover:bg-panel-2"}`}>{f}</button>
          ))}
        </div>
        {q.isLoading && <Spinner />}
        {q.error && <ErrorBanner error={q.error} />}
        {q.data?.length === 0 && <EmptyState title={filter === "pending" ? "No pending approvals" : "No approvals yet"} />}
        {q.data?.map((a) => <ApprovalCard key={a.id} approval={a} />)}
      </div>
    </>
  );
}
