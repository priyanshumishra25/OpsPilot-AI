import { execFileSync } from "node:child_process";
import { expect, request, test, type Page } from "@playwright/test";
import { API, signInAs, token } from "./session";

// Runs against the live Compose stack. Rollback requests/executions go through the real operations MCP server via the
// independent MCP Inspector CLI (standing in for the agent, which needs a model API key).
const ROOT = new URL("../../", import.meta.url).pathname;

test.describe.configure({ mode: "serial" });

function mcp(tool: string, args: Record<string, string>): Record<string, unknown> {
  const token = execFileSync("docker", ["compose", "exec", "-T", "api", "python", "-m", "opspilot.cli", "mint-tool-token",
    "--email", "engineer@opspilot.demo", "--workspace", "demo", "--server", "operations"], { cwd: ROOT, encoding: "utf8" }).trim().split("\n").at(-1) ?? "";
  const argv = ["--yes", "@modelcontextprotocol/inspector", "--cli", "http://127.0.0.1:8104/mcp", "--transport", "http",
    "--header", `Authorization: Bearer ${token}`, "--method", "tools/call", "--tool-name", tool,
    ...Object.entries(args).flatMap(([k, v]) => ["--tool-arg", `${k}=${v}`])];
  let out: string;
  try {
    out = execFileSync("npx", argv, { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] });
  } catch (e) {
    out = (e as { stdout?: string }).stdout ?? "";
  }
  const parsed = JSON.parse(out) as { structuredContent?: Record<string, unknown>; isError?: boolean };
  return parsed.structuredContent ?? { isError: true };
}

async function adminApi(path: string) {
  const ctx = await request.newContext({ baseURL: API });
  await ctx.post(path, { headers: { Authorization: `Bearer ${token("admin@opspilot.demo")}` } });
  await ctx.dispose();
}

async function signIn(page: Page, email: string) {
  await signInAs(page, email);
}

const nav = (page: Page, name: string) => page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name });

test.afterAll(async () => { await adminApi("/api/v1/demo/reset"); });

test("rollback requires a different human to approve, then executes once", async ({ browser }) => {
  test.setTimeout(180_000);
  await adminApi("/api/v1/demo/reset");
  await adminApi("/api/v1/demo/scenarios/orders-bad-deploy/trigger");
  const req = mcp("request_deployment_rollback", { service: "orders-api", target_version: "v2.15.0", reason: "e2e: 5xx after v2.16.0" });
  const approvalId = String(req.approval_id);
  expect(req.status).toBe("pending");

  const eng = await (await browser.newContext()).newPage();
  await signIn(eng, "engineer@opspilot.demo");
  await nav(eng, "Approvals").click();
  const engCard = eng.getByRole("article", { name: `Approval ${approvalId}` });
  await expect(engCard.getByText("Roll back orders-api: v2.16.0 → v2.15.0")).toBeVisible();
  await expect(engCard.getByRole("button", { name: "Approve" })).toHaveCount(0);
  expect(mcp("execute_approved_rollback", { approval_id: approvalId, service: "orders-api", target_version: "v2.15.0" }).isError).toBe(true);

  const sre = await (await browser.newContext()).newPage();
  await signIn(sre, "sre@opspilot.demo");
  await nav(sre, "Approvals").click();
  const card = sre.getByRole("article", { name: `Approval ${approvalId}` });
  await card.getByLabel("Decision note").fill("e2e: evidence reviewed");
  await card.getByRole("button", { name: "Approve" }).click();
  await sre.getByRole("tab", { name: "all" }).click();
  await expect(card.getByText("approved", { exact: true })).toBeVisible();

  const done = mcp("execute_approved_rollback", { approval_id: approvalId, service: "orders-api", target_version: "v2.15.0" });
  expect(done.status).toBe("succeeded");
  // Polling pauses for pages the headless browser treats as hidden, so check the persisted outcome after a reload.
  await sre.reload();
  await sre.getByRole("tab", { name: "all" }).click();
  await expect(card.getByText("succeeded", { exact: true })).toBeVisible();
  await expect(card.getByText(/verified running version/)).toBeVisible();
  expect(mcp("execute_approved_rollback", { approval_id: approvalId, service: "orders-api", target_version: "v2.15.0" }).isError).toBe(true);
});

test("integrations page shows live MCP servers and demo controls for admins", async ({ page }) => {
  await signIn(page, "admin@opspilot.demo");
  await nav(page, "Integrations").click();
  for (const s of ["knowledge", "github", "observability", "operations"]) {
    await expect(page.getByText(s, { exact: true }).first()).toBeVisible({ timeout: 20_000 });
  }
  await expect(page.getByText("execute_approved_rollback")).toBeVisible();
  await expect(page.getByRole("button", { name: "Trigger orders-bad-deploy" })).toBeVisible();
});

test("investigate explains when no model key is configured", async ({ page }) => {
  await signIn(page, "engineer@opspilot.demo");
  await nav(page, "Investigate").click();
  await page.getByLabel("Message to the agent").fill("Investigate orders-api 500 errors");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByRole("alert")).toContainText("ANTHROPIC_API_KEY");
});
