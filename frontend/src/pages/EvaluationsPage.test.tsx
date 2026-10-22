import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { EvaluationRun } from "../lib/types";
import { EvaluationsPage } from "./EvaluationsPage";

const m = (r: number, l: number) => ({ "recall@5": r, "precision@5": 0.2, mrr: r - 0.05, "ndcg@5": r - 0.04, latency_ms: { p50: l, p95: l + 10 }, n_scored: 28 });
const run: EvaluationRun = {
  id: "e1", kind: "retrieval", run_id: "20261022T053529Z", git_sha: "69d0cb4",
  dataset: { questions: 35, dev: 16, test: 19 }, settings: {},
  retrieval: { dense: m(0.964, 16), "hybrid_rrf+rerank@20": m(0.982, 198) },
  abstention: { "hybrid_rrf+rerank": { signal: "top_rerank_score", threshold: -4.47, dev_balanced_accuracy: 1, test_answerable_kept: 1, test_unanswerable_abstained: 1, n_test: { answerable: 15, unanswerable: 4 } } },
  permission_checks: { passed: true }, generation: { status: "not requested" },
  baseline_comparison: { compared: true, regressions: [] }, environment: { note: "single laptop" }, created_at: "2026-10-22T05:35:29Z",
};

afterEach(() => { vi.restoreAllMocks(); });

it("shows recorded metrics, calibration counts with a sample-size caveat, and honest generation status", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify([run]), { status: 200 }));
  render(<QueryClientProvider client={new QueryClient()}><EvaluationsPage /></QueryClientProvider>);
  expect(await screen.findByText("no regression vs baseline")).toBeInTheDocument();
  expect(screen.getByText("15/15")).toBeInTheDocument();
  expect(screen.getByText("4/4")).toBeInTheDocument();
  expect(screen.getByText("small sample")).toBeInTheDocument();
  expect(screen.getByText("not requested")).toBeInTheDocument();
  expect(screen.getByText(/Trend appears after two or more recorded runs \(1 so far\)/)).toBeInTheDocument();
  await userEvent.click(screen.getByLabelText("Show tables"));
  expect(screen.getAllByRole("cell", { name: "0.982" }).length).toBeGreaterThan(0);
});
