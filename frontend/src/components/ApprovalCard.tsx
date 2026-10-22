import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ShieldCheck } from "lucide-react";
import { useState } from "react";
import { api } from "../lib/api";
import { timeAgo } from "../lib/format";
import type { Approval } from "../lib/types";
import { Badge, Button, ErrorBanner, StatusBadge } from "./ui";

export function ApprovalCard({ approval, compact = false }: { approval: Approval; compact?: boolean }) {
  const qc = useQueryClient();
  const [note, setNote] = useState("");
  const decide = useMutation({
    mutationFn: (approve: boolean) =>
      api<Approval>(`/api/v1/approvals/${approval.id}/${approve ? "approve" : "reject"}`, { method: "POST", body: JSON.stringify({ note: note || null }) }),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ["approvals"] }); void qc.invalidateQueries({ queryKey: ["approval", approval.id] }); },
  });
  const [renderedAt] = useState(() => Date.now());
  const expired = new Date(approval.expires_at).getTime() < renderedAt;
  const args = approval.arguments as { service?: string; target_version?: string };
  const from = approval.target_state.from_version as string | undefined;
  return (
    <article className="rounded-lg border border-line bg-panel p-4 text-[13px]" aria-label={`Approval ${approval.id}`}>
      <header className="flex flex-wrap items-center gap-2">
        <ShieldCheck className="size-4 text-warn" aria-hidden />
        <h3 className="font-semibold">{approval.action === "deployment.rollback" ? `Roll back ${args.service ?? ""}: ${from ?? "?"} → ${args.target_version ?? "?"}` : approval.action}</h3>
        <StatusBadge status={approval.status} />
        <Badge tone="warn">demo environment</Badge>
        <span className="ml-auto text-[12px] text-ink-3">requested by {approval.requested_by_name ?? "unknown"} · {timeAgo(approval.created_at)}</span>
      </header>
      <p className="mt-2 text-ink-2">{approval.expected_impact}</p>
      {!compact && (
        <dl className="mt-2 grid grid-cols-[8rem_1fr] gap-y-1 text-[12px]">
          <dt className="text-ink-3">Reason</dt><dd>{approval.reason}</dd>
          <dt className="text-ink-3">Target</dt><dd className="font-mono">{approval.target_resource} @ state {String(approval.target_state.state_version)}</dd>
          <dt className="text-ink-3">Arguments</dt><dd className="font-mono">{JSON.stringify(approval.arguments)}</dd>
          <dt className="text-ink-3">Expires</dt><dd>{new Date(approval.expires_at).toLocaleString()}{expired && approval.status === "pending" ? " (expired)" : ""}</dd>
          {approval.decided_by_name && (<><dt className="text-ink-3">Decided by</dt><dd>{approval.decided_by_name}{approval.decision_note ? `: “${approval.decision_note}”` : ""}</dd></>)}
          {approval.result.message && (<><dt className="text-ink-3">Outcome</dt><dd className={approval.status === "succeeded" ? "text-ok" : "text-bad"}>{approval.result.message}</dd></>)}
        </dl>
      )}
      {approval.status === "pending" && (
        approval.can_decide ? (
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <label className="sr-only" htmlFor={`note-${approval.id}`}>Decision note</label>
            <input id={`note-${approval.id}`} value={note} onChange={(e) => { setNote(e.target.value); }} placeholder="Note (optional)" maxLength={1000}
              className="min-w-48 flex-1 rounded-md border border-line bg-canvas px-2 py-1.5 text-[13px]" />
            <Button onClick={() => { decide.mutate(true); }} loading={decide.isPending}>Approve</Button>
            <Button variant="danger" onClick={() => { decide.mutate(false); }} disabled={decide.isPending}>Reject</Button>
          </div>
        ) : (
          <p className="mt-3 text-[12px] text-ink-3">Waiting for an approver. Requests must be approved by someone other than the requester with the approvals:decide permission.</p>
        )
      )}
      {decide.error && <div className="mt-2"><ErrorBanner error={decide.error} /></div>}
    </article>
  );
}
