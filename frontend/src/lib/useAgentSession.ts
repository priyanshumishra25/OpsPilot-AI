import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { API_BASE, api, authHeaders } from "./api";
import { readSse } from "./sse";
import type { AgentEvent, AgentSessionDetail } from "./types";

const ACTIVE = new Set(["queued", "running"]);

/** Merge events by sequence number (idempotent: replayed and live events may overlap). */
export function mergeEvents(current: AgentEvent[], incoming: AgentEvent[]): AgentEvent[] {
  const bySeq = new Map(current.map((e) => [e.seq, e]));
  for (const e of incoming) bySeq.set(e.seq, e);
  return [...bySeq.values()].sort((a, b) => a.seq - b.seq);
}

/** Session detail from the API plus live events from the SSE stream while a turn is active. The database snapshot
 *  and live events are merged by sequence number, so reconnects and refetches never duplicate or lose events. */
export function useAgentSession(id: string | undefined) {
  const qc = useQueryClient();
  const detail = useQuery({
    queryKey: ["agent-session", id],
    queryFn: () => api<AgentSessionDetail>(`/api/v1/agent/sessions/${id ?? ""}`),
    enabled: !!id,
  });
  const [live, setLive] = useState<{ id: string | undefined; events: AgentEvent[]; streaming: boolean }>({ id, events: [], streaming: false });
  const snapshot = detail.data?.events;
  const lastSeq = useRef(0);
  const liveForSession = live.id === id ? live : { id, events: [], streaming: false };
  const events = useMemo(() => mergeEvents(snapshot ?? [], liveForSession.events), [snapshot, liveForSession.events]);
  useEffect(() => { lastSeq.current = events.at(-1)?.seq ?? 0; }, [events]);

  const active = detail.data ? ACTIVE.has(detail.data.status) : false;
  useEffect(() => {
    if (!id || !active) return;
    const ac = new AbortController();
    const after = lastSeq.current;
    const push = (ev: AgentEvent | null, streaming: boolean) => {
      setLive((cur) => {
        const base = cur.id === id ? cur.events : [];
        return { id, events: ev ? mergeEvents(base, [ev]) : base, streaming };
      });
    };
    void (async () => {
      try {
        const res = await fetch(`${API_BASE}/api/v1/agent/sessions/${id}/stream?after=${after}`, {
          headers: { Accept: "text/event-stream", ...authHeaders() }, signal: ac.signal,
        });
        if (!res.ok || !res.body) return;
        push(null, true);
        for await (const msg of readSse(res.body, ac.signal)) {
          if (msg.event === "idle") break;
          const e = JSON.parse(msg.data) as AgentEvent;
          push({ seq: e.seq, turn: e.turn, type: msg.event, payload: e.payload, created_at: e.created_at }, true);
          if (msg.event === "turn_finished" || msg.event === "approval_requested") {
            void qc.invalidateQueries({ queryKey: ["agent-session", id] });
            void qc.invalidateQueries({ queryKey: ["agent-sessions"] });
            void qc.invalidateQueries({ queryKey: ["approvals"] });
          }
        }
      } catch {
        // Aborted or network loss: the next detail refetch replays from the database.
      } finally {
        if (!ac.signal.aborted) {
          push(null, false);
          void qc.invalidateQueries({ queryKey: ["agent-session", id] });
        }
      }
    })();
    return () => { ac.abort(); };
  }, [id, active, qc]);

  return { detail, events, streaming: liveForSession.streaming, active };
}
