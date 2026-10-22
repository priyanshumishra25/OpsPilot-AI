import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, FileWarning, SendHorizontal, Square } from "lucide-react";
import { useState, type SyntheticEvent } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { ApprovalCard } from "../components/ApprovalCard";
import { PageHeader } from "../components/Layout";
import { ReportView } from "../components/Report";
import { buildTurns, ToolStepRow, UsageLine, type TurnView } from "../components/Timeline";
import { Button, Card, EmptyState, ErrorBanner, Spinner, StatusBadge } from "../components/ui";
import { api } from "../lib/api";
import { timeAgo } from "../lib/format";
import type { AgentSessionSummary, Approval, IncidentReport } from "../lib/types";
import { useAgentSession } from "../lib/useAgentSession";

const EXAMPLES = [
  "Investigate why the orders API started returning HTTP 500 errors after the latest deployment.",
  "Is checkout-api healthy right now? Check metrics and recent deployments.",
  "Roll back the problematic orders-api deployment.",
];

export function InvestigatePage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const sessions = useQuery({ queryKey: ["agent-sessions"], queryFn: () => api<AgentSessionSummary[]>("/api/v1/agent/sessions") });
  const { detail, events, streaming, active } = useAgentSession(id);
  const [text, setText] = useState("");
  const send = useMutation({
    mutationFn: (message: string) =>
      id ? api<AgentSessionSummary>(`/api/v1/agent/sessions/${id}/messages`, { method: "POST", body: JSON.stringify({ message }) })
        : api<AgentSessionSummary>("/api/v1/agent/sessions", { method: "POST", body: JSON.stringify({ message }) }),
    onSuccess: (s) => {
      setText("");
      void qc.invalidateQueries({ queryKey: ["agent-sessions"] });
      void qc.invalidateQueries({ queryKey: ["agent-session", s.id] });
      if (!id) void navigate(`/investigate/${s.id}`);
    },
  });
  const cancel = useMutation({
    mutationFn: () => api(`/api/v1/agent/sessions/${id ?? ""}/cancel`, { method: "POST" }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["agent-session", id] }),
  });
  const submit = (e?: SyntheticEvent, override?: string) => {
    e?.preventDefault();
    const m = (override ?? text).trim();
    if (m.length >= 2 && !send.isPending && !active) send.mutate(m);
  };
  const turns = buildTurns(events);

  return (
    <>
      <PageHeader title="Investigate" description="An agent that investigates incidents with documentation, telemetry, code and deployment tools. Actions require human approval." />
      <div className="grid gap-4 p-6 xl:grid-cols-[16rem_1fr]">
        <aside aria-label="Investigations" className="space-y-2">
          <Link to="/investigate" className="block rounded-md border border-dashed border-line px-3 py-2 text-[13px] text-ink-2 hover:bg-panel-2">+ New investigation</Link>
          {sessions.error && <ErrorBanner error={sessions.error} />}
          <ul className="space-y-1">
            {sessions.data?.map((s) => (
              <li key={s.id}>
                <Link to={`/investigate/${s.id}`} className={`block rounded-md px-2 py-1.5 text-[12px] hover:bg-panel-2 ${s.id === id ? "bg-panel-2" : ""}`}>
                  <span className="line-clamp-2 text-ink-2">{s.title}</span>
                  <span className="mt-0.5 flex items-center gap-1 text-[11px] text-ink-3">{timeAgo(s.updated_at)} <StatusBadge status={s.status} /></span>
                </Link>
              </li>
            ))}
          </ul>
        </aside>

        <div className="min-w-0 space-y-4">
          {!id && (
            <Card title="New investigation">
              <div className="flex flex-wrap gap-2">
                {EXAMPLES.map((ex) => (
                  <button key={ex} onClick={() => { submit(undefined, ex); }} className="rounded-full border border-line px-3 py-1 text-left text-[12px] text-ink-2 hover:bg-panel-2">{ex}</button>
                ))}
              </div>
            </Card>
          )}
          {id && detail.isLoading && <Spinner />}
          {detail.error && <ErrorBanner error={detail.error} />}
          {turns.map((t) => <Turn key={t.turn} turn={t} />)}
          {active && (
            <div className="flex items-center gap-3 text-[13px] text-ink-3">
              <Spinner label={detail.data?.status === "queued" ? "Waiting for an agent worker" : "Agent is working"} />
              {streaming && <span className="text-[11px]">live</span>}
              <Button variant="ghost" onClick={() => { cancel.mutate(); }} loading={cancel.isPending}><Square className="size-3.5" />Stop</Button>
            </div>
          )}
          {detail.data && !active && <UsageLine usage={detail.data.usage} error={detail.data.error} status={detail.data.status} />}

          <form onSubmit={submit} className="flex gap-2">
            <label htmlFor="agent-input" className="sr-only">Message to the agent</label>
            <textarea id="agent-input" rows={2} value={text} maxLength={4000} disabled={active}
              onChange={(e) => { setText(e.target.value); }}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); } }}
              placeholder={id ? "Follow up, e.g. “The rollback was approved, proceed.”" : "Describe the incident to investigate…"}
              className="min-h-[3.25rem] flex-1 resize-y rounded-md border border-line bg-panel px-3 py-2 text-[14px] disabled:opacity-60" />
            <Button type="submit" disabled={active || text.trim().length < 2} loading={send.isPending} aria-label="Send"><SendHorizontal className="size-4" />Send</Button>
          </form>
          {send.error && <ErrorBanner error={send.error} />}
        </div>
      </div>
    </>
  );
}

function Turn({ turn }: { turn: TurnView }) {
  const answer = turn.answer?.payload;
  const unverified = ((answer?.evidence_checks as { unverified?: { id: string }[] } | undefined)?.unverified ?? []).map((u) => u.id);
  return (
    <section className="space-y-2" aria-label={`Turn ${turn.turn}`}>
      {turn.message && <p className="ml-auto max-w-2xl rounded-lg bg-accent/10 px-3 py-2 text-[14px] text-ink">{turn.message}</p>}
      {turn.items.length > 0 && (
        <ol className="space-y-1.5" aria-label="Execution timeline">
          {turn.items.map((it, i) => {
            if (it.kind === "tool") return <ToolStepRow key={i} step={it.step} />;
            if (it.kind === "text") return <li key={i} className="flex gap-2 px-1 text-[13px] text-ink-2"><Bot className="mt-0.5 size-4 shrink-0 text-ink-3" aria-hidden />{String(it.event.payload.text)}</li>;
            if (it.event.type === "approval_requested") return <li key={i}><LiveApproval id={String(it.event.payload.approval_id)} /></li>;
            if (it.event.type === "action_executed") return <li key={i} className="rounded-md border border-ok/40 bg-ok/5 px-3 py-2 text-[13px]">{typeof it.event.payload.message === "string" ? it.event.payload.message : "Action executed"}</li>;
            if (it.event.type === "report") return (
              <li key={i} className="flex items-center gap-2 px-1 text-[13px] text-ink-2"><FileWarning className="size-4 text-warn" aria-hidden />
                Incident report saved. <Link className="text-accent" to={`/incidents/${String(it.event.payload.incident_id)}`}>Open incident</Link></li>
            );
            return null;
          })}
        </ol>
      )}
      {answer && (
        <div className="rounded-lg border border-line bg-panel p-4">
          <p className="whitespace-pre-wrap text-[14px] leading-relaxed">{String(answer.answer)}</p>
        </div>
      )}
      {turn.items.some((i) => i.kind === "event" && i.event.type === "report") && (() => {
        const ev = turn.items.find((i) => i.kind === "event" && i.event.type === "report");
        const report = ev && ev.kind === "event" ? (ev.event.payload.report as IncidentReport) : null;
        return report ? <Card title="Incident report"><ReportView report={report} unverified={unverified} /></Card> : null;
      })()}
      {turn.finished && <UsageLine usage={(turn.finished.payload.usage ?? {}) as Record<string, unknown>} error={turn.finished.payload.error} status={turn.finished.payload.status} />}
    </section>
  );
}

function LiveApproval({ id }: { id: string }) {
  const q = useQuery({ queryKey: ["approval", id], queryFn: () => api<Approval>(`/api/v1/approvals/${id}`), refetchInterval: (s) => (s.state.data?.status === "pending" || s.state.data?.status === "approved" ? 5000 : false) });
  if (q.isLoading) return <Spinner label="Loading approval" />;
  if (q.error) return <ErrorBanner error={q.error} />;
  return q.data ? <ApprovalCard approval={q.data} compact /> : <EmptyState title="Approval not found" />;
}
