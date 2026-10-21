import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { AnswerText, CitationCard } from "./Answer";

const citation = { index: 2, chunk_id: "c9", document_id: "d1", version_id: "v1", document_title: "Checkout Runbook", section_path: ["Recovery procedure"], source_uri: "upload://r.md", page_number: null, char_start: 0, char_end: 40, cited_text: "rolling restart" };

it("renders citation markers inline and reports clicks", async () => {
  const onCite = vi.fn();
  render(<AnswerText text="Do a rolling restart. Done." markers={[{ offset: 21, index: 2 }]} onCite={onCite} />);
  const marker = screen.getByRole("button", { name: "Show source 2" });
  expect(marker.parentElement?.textContent).toBe("Do a rolling restart.2 Done.");
  await userEvent.click(marker);
  expect(onCite).toHaveBeenCalledWith(2);
});

it("expands a citation to the resolved source passage with the cited text highlighted", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify({ id: "c9", document_id: "d1", document_title: "Checkout Runbook", version_id: "v1", ordinal: 3, section_path: ["Recovery procedure"], content: "Then do a rolling restart one pod at a time.", page_number: null, char_start: 0, char_end: 44, source_uri: "upload://r.md" }), { status: 200 }),
  );
  const qc = new QueryClient();
  const { rerender } = render(
    <QueryClientProvider client={qc}><MemoryRouter><CitationCard citation={citation} active={false} onSelect={() => undefined} /></MemoryRouter></QueryClientProvider>,
  );
  expect(screen.getByText("Checkout Runbook")).toBeInTheDocument();
  rerender(<QueryClientProvider client={qc}><MemoryRouter><CitationCard citation={citation} active onSelect={() => undefined} /></MemoryRouter></QueryClientProvider>);
  const mark = await screen.findByText("rolling restart", { selector: "mark" });
  expect(mark).toBeInTheDocument();
  expect(fetchMock.mock.calls[0]?.[0]).toContain("/api/v1/chunks/c9");
  expect(screen.getByRole("link", { name: /open document/i })).toHaveAttribute("href", "/knowledge?doc=d1");
  fetchMock.mockRestore();
});
