import { useCallback, useRef, useState } from "react";
import { API_BASE, ApiError, authHeaders, toApiError } from "./api";
import { readSse } from "./sse";
import type { Citation, RetrievalDiagnostics, Usage } from "./types";

export type StreamStatus = "idle" | "retrieving" | "generating" | "done" | "error" | "cancelled";

export interface CitationMarker { offset: number; index: number }

export interface AnswerState {
  status: StreamStatus;
  queryId: string | null;
  traceId: string | null;
  text: string;
  markers: CitationMarker[];
  citations: Citation[];
  retrieval: RetrievalDiagnostics | null;
  usage: Usage | null;
  abstained: boolean;
  latencyMs: number | null;
  error: { code: string; message: string } | null;
}

const INITIAL: AnswerState = {
  status: "idle", queryId: null, traceId: null, text: "", markers: [], citations: [], retrieval: null,
  usage: null, abstained: false, latencyMs: null, error: null,
};

/** Reduce one SSE event into answer state. Exported for unit tests. */
export function applyEvent(state: AnswerState, event: string, data: unknown): AnswerState {
  const d = data as Record<string, unknown>;
  switch (event) {
    case "started":
      return { ...state, status: "retrieving", queryId: d.query_id as string, traceId: (d.trace_id as string | null) ?? null };
    case "retrieval":
      return { ...state, status: "generating", retrieval: data as RetrievalDiagnostics };
    case "text":
      return { ...state, text: state.text + (d.text as string) };
    case "citation": {
      const c = data as Citation;
      const citations = state.citations.some((x) => x.chunk_id === c.chunk_id) ? state.citations : [...state.citations, c];
      return { ...state, citations, markers: [...state.markers, { offset: state.text.length, index: c.index }] };
    }
    case "done":
      return {
        ...state, status: "done", text: (d.answer as string) || state.text, citations: d.citations as Citation[],
        usage: Object.keys(d.usage as object).length ? (d.usage as Usage) : null, abstained: d.abstained as boolean,
        latencyMs: d.latency_ms as number,
      };
    case "error":
      return { ...state, status: "error", error: { code: d.code as string, message: d.message as string } };
    default:
      return state;
  }
}

export function useAnswerStream() {
  const [state, setState] = useState<AnswerState>(INITIAL);
  const controller = useRef<AbortController | null>(null);

  const ask = useCallback(async (query: string) => {
    controller.current?.abort();
    const ac = new AbortController();
    controller.current = ac;
    setState({ ...INITIAL, status: "retrieving" });
    try {
      const res = await fetch(`${API_BASE}/api/v1/query`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "text/event-stream", ...authHeaders() },
        body: JSON.stringify({ query }),
        signal: ac.signal,
      });
      if (!res.ok || !res.body) throw await toApiError(res);
      for await (const msg of readSse(res.body, ac.signal)) {
        const parsed: unknown = JSON.parse(msg.data);
        setState((s) => applyEvent(s, msg.event, parsed));
      }
    } catch (e) {
      if (ac.signal.aborted) {
        setState((s) => ({ ...s, status: "cancelled" }));
        return;
      }
      const err = e instanceof ApiError ? { code: e.code, message: e.message } : { code: "network_error", message: "Connection to the API was lost" };
      setState((s) => ({ ...s, status: "error", error: err }));
    }
  }, []);

  const cancel = useCallback(() => { controller.current?.abort(); }, []);
  const reset = useCallback(() => { controller.current?.abort(); setState(INITIAL); }, []);
  return { state, ask, cancel, reset };
}
