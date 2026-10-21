import { applyEvent, type AnswerState } from "./useAnswerStream";

const initial: AnswerState = {
  status: "idle", queryId: null, traceId: null, text: "", markers: [], citations: [], retrieval: null,
  usage: null, abstained: false, latencyMs: null, error: null,
};

const citation = { index: 1, chunk_id: "c1", document_id: "d1", version_id: "v1", document_title: "Runbook", section_path: ["Recovery"], source_uri: "upload://r.md", page_number: null, char_start: 0, char_end: 10, cited_text: "Restart pods" };

it("accumulates a streamed answer with citation markers at the right offsets", () => {
  let s = applyEvent(initial, "started", { query_id: "q1", trace_id: "t1" });
  expect(s.status).toBe("retrieving");
  s = applyEvent(s, "retrieval", { results: [], evidence_sufficient: true });
  expect(s.status).toBe("generating");
  s = applyEvent(s, "text", { text: "Restart the pods." });
  s = applyEvent(s, "citation", citation);
  s = applyEvent(s, "text", { text: " Then verify." });
  s = applyEvent(s, "citation", citation);
  expect(s.text).toBe("Restart the pods. Then verify.");
  expect(s.markers).toEqual([{ offset: 17, index: 1 }, { offset: 30, index: 1 }]);
  expect(s.citations).toHaveLength(1);
  s = applyEvent(s, "done", { answer: s.text, citations: [citation], usage: {}, abstained: false, latency_ms: 1200 });
  expect(s.status).toBe("done");
  expect(s.usage).toBeNull();
});

it("records server errors", () => {
  const s = applyEvent(initial, "error", { code: "not_configured", message: "no key" });
  expect(s.status).toBe("error");
  expect(s.error?.code).toBe("not_configured");
});
