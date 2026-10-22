import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mergeEvents } from "../lib/useAgentSession";
import type { AgentEvent } from "../lib/types";
import { must } from "../test/must";
import { buildTurns, ToolStepRow } from "./Timeline";

const ev = (seq: number, type: string, payload: Record<string, unknown>, turn = 1): AgentEvent => ({ seq, turn, type, payload, created_at: "2026-10-22T00:00:00Z" });

const events = [
  ev(1, "user_message", { text: "Investigate orders-api" }),
  ev(2, "turn_started", {}),
  ev(3, "tool_call", { tool_use_id: "a", server: "observability", tool: "get_service_health", input: { service: "orders-api" } }),
  ev(4, "tool_call", { tool_use_id: "b", server: "github", tool: "get_commit_diff", input: { sha: "4f6e9a5" } }),
  ev(5, "tool_result", { tool_use_id: "b", is_error: true, duration_ms: 12, output: { error: { code: "not_found", message: "no such commit" } } }),
  ev(6, "tool_result", { tool_use_id: "a", is_error: false, duration_ms: 40, output: { status: "unhealthy" } }),
  ev(7, "answer", { answer: "v2.16.0 caused it [E1]" }),
  ev(8, "turn_finished", { status: "completed", usage: { input_tokens: 3000 } }),
  ev(9, "user_message", { text: "Roll it back" }, 2),
];

it("groups events into turns and pairs tool calls with results out of order", () => {
  const turns = buildTurns(events);
  expect(turns).toHaveLength(2);
  const t1 = turns[0];
  expect(t1?.message).toBe("Investigate orders-api");
  const seqs = (t1?.items ?? []).flatMap((i) => (i.kind === "tool" ? [i.step.result?.seq] : []));
  expect(seqs).toEqual([6, 5]);
  expect(t1?.answer?.payload.answer).toContain("v2.16.0");
  expect(turns[1]?.message).toBe("Roll it back");
});

it("merges replayed and live events idempotently by sequence", () => {
  const merged = mergeEvents(events.slice(0, 5), [must(events[3]), must(events[5]), must(events[4])]);
  expect(merged.map((e) => e.seq)).toEqual([1, 2, 3, 4, 5, 6]);
});

it("shows tool errors inline and reveals input/output on expand", async () => {
  const step = must(buildTurns(events)[0]).items.find((i) => i.kind === "tool" && i.step.call.payload.tool === "get_commit_diff");
  if (!step || step.kind !== "tool") throw new Error("missing step");
  render(<ul><ToolStepRow step={step.step} /></ul>);
  expect(screen.getByText("not_found: no such commit")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { expanded: false }));
  expect(screen.getByText("Input")).toBeInTheDocument();
  expect(screen.getByText(/"sha": "4f6e9a5"/)).toBeInTheDocument();
});
