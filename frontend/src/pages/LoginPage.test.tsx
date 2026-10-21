import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { AuthProvider } from "../lib/auth";
import { LoginPage } from "./LoginPage";

function setup() {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter><AuthProvider><LoginPage /></AuthProvider></MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => { sessionStorage.clear(); vi.restoreAllMocks(); });

it("shows the server error message on failed login", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify({ error: { code: "unauthenticated", message: "Invalid email or password", details: {}, trace_id: "abc" } }), { status: 401 }),
  );
  setup();
  await userEvent.type(screen.getByLabelText("Email"), "a@b.test");
  await userEvent.type(screen.getByLabelText("Password"), "wrong");
  await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Invalid email or password");
  expect(sessionStorage.getItem("opspilot.session")).toBeNull();
});

it("stores the session on successful login", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify({ access_token: "tok", token_type: "bearer", expires_at: new Date(Date.now() + 60_000).toISOString() }), { status: 200 }),
  );
  setup();
  await userEvent.type(screen.getByLabelText("Email"), "a@b.test");
  await userEvent.type(screen.getByLabelText("Password"), "right-password");
  await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
  await vi.waitFor(() => { expect(sessionStorage.getItem("opspilot.session")).toContain("tok"); });
});
