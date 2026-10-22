import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Approval } from "../lib/types";
import { must } from "../test/must";
import { ApprovalCard } from "./ApprovalCard";
import { ReportView } from "./Report";

const base: Approval = {
  id: "a1", action: "deployment.rollback", tool_name: "execute_approved_rollback",
  arguments: { service: "orders-api", target_version: "v2.15.0" }, target_resource: "demo/orders-api",
  target_state: { from_version: "v2.16.0", state_version: 2 }, expected_impact: "Redeploys orders-api to v2.15.0",
  reason: "5xx spike", incident_id: null, status: "pending", requested_by: "u1", decided_by: null,
  requested_by_name: "Demo Engineer", decided_by_name: null, decision_note: null, result: {},
  expires_at: new Date(Date.now() + 600_000).toISOString(), created_at: new Date().toISOString(), decided_at: null, finished_at: null,
  can_decide: false,
};

function renderCard(a: Approval) {
  return render(<QueryClientProvider client={new QueryClient()}><ApprovalCard approval={a} /></QueryClientProvider>);
}

afterEach(() => { vi.restoreAllMocks(); });

it("shows the rollback, binding details and no decision controls without permission", () => {
  renderCard(base);
  expect(screen.getByText("Roll back orders-api: v2.16.0 → v2.15.0")).toBeInTheDocument();
  expect(screen.getByText(/demo\/orders-api @ state 2/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
  expect(screen.getByText(/someone other than the requester/)).toBeInTheDocument();
});

it("lets an eligible approver approve with a note", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ ...base, status: "approved" }), { status: 200 }));
  renderCard({ ...base, can_decide: true });
  await userEvent.type(screen.getByLabelText("Decision note"), "evidence reviewed");
  await userEvent.click(screen.getByRole("button", { name: "Approve" }));
  await vi.waitFor(() => { expect(fetchMock).toHaveBeenCalled(); });
  const [url, init] = must(fetchMock.mock.calls[0]);
  expect(url).toBe("http://localhost:8000/api/v1/approvals/a1/approve");
  expect(JSON.parse(init?.body as string)).toEqual({ note: "evidence reviewed" });
});

it("shows the execution outcome", () => {
  renderCard({ ...base, status: "succeeded", decided_by_name: "Demo SRE", decision_note: "ok", result: { message: "Rolled back orders-api; verified running version." } });
  expect(screen.getByText(/verified running version/)).toBeInTheDocument();
  expect(screen.getByText(/Demo SRE/)).toBeInTheDocument();
});

it("flags unverified evidence in reports", () => {
  render(<ReportView unverified={["E2"]} report={{
    incident_summary: "s", observed_symptoms: [], relevant_services: ["orders-api"],
    evidence: [{ id: "E1", kind: "metric", source: "get_error_rate", observation: "30% 5xx" }, { id: "E2", kind: "commit", source: "get_commit_diff abc1234", observation: "x" }],
    timeline: [], candidate_causes: [{ cause: "pool", assessment: "likely", supporting_evidence: ["E1"], contradicting_evidence: [] }],
    most_likely_cause: "pool", confidence: { evidence_strength: "moderate", explanation: "two sources" },
    missing_information: [], recommended_actions: [], risk_assessment: "low",
  }} />);
  expect(screen.getAllByText("unverified")).toHaveLength(1);
  expect(screen.getByText("moderate evidence")).toBeInTheDocument();
});
