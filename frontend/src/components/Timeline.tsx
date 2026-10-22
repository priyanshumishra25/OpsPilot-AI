import { CheckCircle2, ChevronRight, CircleAlert, Loader2, Wrench } from "lucide-react";
import { useState } from "react";
import type { AgentEvent } from "../lib/types";
import { Badge } from "./ui";

export interface ToolStep { call: AgentEvent; result: AgentEvent | undefined }

export interface TurnView {
  turn: number;
  message: string | null;
  items: ({ kind: "text"; event: AgentEvent } | { kind: "tool"; step: ToolStep } | { kind: "event"; event: AgentEvent })[];
  answer: AgentEvent | undefined;
  finished: AgentEvent | undefined;
}

/** Group a session's events into turns, pairing each tool call with its result. Exported for tests. */
export function buildTurns(events: AgentEvent[]): TurnView[] {
  const turns = new Map<number, TurnView>();
  const results = new Map<string, AgentEvent>();
  for (const e of events) if (e.type === "tool_result") results.set(String(e.payload.tool_use_id), e);
  for (const e of events) {
    const t = turns.get(e.turn) ?? { turn: e.turn, message: null, items: [], answer: undefined, finished: undefined };
    turns.set(e.turn, t);
    switch (e.type) {
      case "user_message": t.message = typeof e.payload.text === "string" ? e.payload.text : ""; break;
      case "assistant_text": t.items.push({ kind: "text", event: e }); break;
      case "tool_call": t.items.push({ kind: "tool", step: { call: e, result: results.get(String(e.payload.tool_use_id)) } }); break;
      case "approval_requested": case "action_executed": case "report": t.items.push({ kind: "event", event: e }); break;
      case "answer": t.answer = e; break;
      case "turn_finished": t.finished = e; break;
      default: break;
    }
  }
  return [...turns.values()].sort((a, b) => a.turn - b.turn);
}

export function ToolStepRow({ step }: { step: ToolStep }) {
  const [open, setOpen] = useState(false);
  const p = step.call.payload;
  const r = step.result?.payload;
  const isError = r?.is_error === true;
  const err = isError ? (r.output as { error?: { code?: string; message?: string } } | undefined)?.error : undefined;
  return (
    <li className="rounded-md border border-line bg-panel">
      <button onClick={() => { setOpen(!open); }} aria-expanded={open} className="flex w-full items-center gap-2 px-3 py-2 text-left text-[13px]">
        <ChevronRight className={`size-3.5 shrink-0 text-ink-3 transition-transform ${open ? "rotate-90" : ""}`} aria-hidden />
        {!step.result ? <Loader2 className="size-4 shrink-0 animate-spin text-info" aria-label="running" />
          : isError ? <CircleAlert className="size-4 shrink-0 text-bad" aria-label="error" />
          : <CheckCircle2 className="size-4 shrink-0 text-ok" aria-label="succeeded" />}
        <Wrench className="size-3.5 shrink-0 text-ink-3" aria-hidden />
        <span className="font-mono text-[12px]"><span className="text-ink-3">{String(p.server)}.</span>{String(p.tool)}</span>
        <span className="truncate text-[12px] text-ink-3">{summarise(p.input)}</span>
        {r && <span className="ml-auto shrink-0 text-[11px] tabular-nums text-ink-3">{Math.round(Number(r.duration_ms))} ms</span>}
      </button>
      {err && !open && <p className="border-t border-line px-3 py-1.5 text-[12px] text-bad">{err.code}: {err.message}</p>}
      {open && (
        <div className="grid gap-2 border-t border-line p-3 md:grid-cols-2">
          <JsonBlock title="Input" value={p.input} />
          <JsonBlock title={isError ? "Error" : "Output"} value={r?.output ?? "running…"} />
        </div>
      )}
    </li>
  );
}

function summarise(input: unknown): string {
  if (!input || typeof input !== "object") return "";
  return Object.entries(input as Record<string, unknown>).map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`).join(" ").slice(0, 120);
}

export function JsonBlock({ title, value }: { title: string; value: unknown }) {
  return (
    <div className="min-w-0">
      <p className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-ink-3">{title}</p>
      <pre className="max-h-72 overflow-auto rounded bg-canvas p-2 font-mono text-[11px] leading-relaxed text-ink-2">{JSON.stringify(value, null, 2)}</pre>
    </div>
  );
}

export function UsageLine({ usage, error, status }: { usage: Record<string, unknown>; error: unknown; status: unknown }) {
  const cost = typeof usage.estimated_cost_usd === "number" ? usage.estimated_cost_usd : null;
  return (
    <p className="flex flex-wrap items-center gap-2 text-[12px] text-ink-3">
      <Badge tone={status === "completed" ? "ok" : status === "awaiting_approval" ? "warn" : status === "cancelled" ? "neutral" : "bad"}>{String(status).replace("_", " ")}</Badge>
      {typeof usage.input_tokens === "number" && <span>{usage.input_tokens.toLocaleString()} in / {Number(usage.output_tokens ?? 0).toLocaleString()} out tokens</span>}
      {cost !== null && <span>est. ${cost.toFixed(4)}</span>}
      {typeof usage.wall_ms === "number" && <span>{(usage.wall_ms / 1000).toFixed(1)} s</span>}
      {typeof error === "string" && error && <span className="text-bad">{error}</span>}
    </p>
  );
}
