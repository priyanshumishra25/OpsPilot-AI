import { expect, request, test, type Page } from "@playwright/test";
import { API, PASSWORD, signInAs, token } from "./session";



// Remove documents created by this suite (requires an admin; engineers cannot delete).
test.afterAll(async () => {
  const ctx = await request.newContext({ baseURL: API });
  const headers = { Authorization: `Bearer ${token("admin@opspilot.demo")}` };
  const docs = (await (await ctx.get("/api/v1/documents", { headers })).json()) as { id: string; title: string }[];
  for (const d of docs.filter((x) => x.title.startsWith("Cache warmup runbook e2e-"))) {
    await ctx.delete(`/api/v1/documents/${d.id}`, { headers });
  }
  await ctx.dispose();
});

function nav(page: Page, name: string) {
  return page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name });
}

async function signIn(page: Page, email: string) {
  await signInAs(page, email);
}

test("upload a document, watch it index, and retrieve it with evidence @responsive", async ({ page }) => {
  await signIn(page, "engineer@opspilot.demo");
  await nav(page, "Knowledge Base").click();
  const marker = `e2e-${test.info().project.name}-${Math.random().toString(36).slice(2, 10)}`;
  const body = `# Cache warmup runbook ${marker}\n\n## Procedure\n\nTo warm the ${marker} cache, run opsctl cache warm --service ${marker} and wait for the hit rate to exceed 90 percent.\n`;
  await page.getByLabel("Choose files to upload").setInputFiles({ name: `${marker}.md`, mimeType: "text/markdown", buffer: Buffer.from(body) });
  await expect(page.getByText(`${marker}.md: queued for ingestion`)).toBeVisible();
  const row = page.getByRole("row", { name: new RegExp(`Cache warmup runbook ${marker}`) });
  await expect(row.getByText("indexed", { exact: true })).toBeVisible({ timeout: 60_000 });

  await row.getByRole("button").click();
  await expect(page.getByText("Indexed passages")).toBeVisible();
  await expect(page.getByText(`opsctl cache warm --service ${marker}`).first()).toBeVisible();

  await nav(page, "Ask").click();
  await page.getByLabel("Question", { exact: true }).fill(`How do I warm the ${marker} cache?`);
  await page.getByRole("button", { name: "Ask" }).click();
  const evidence = page.getByRole("button", { name: /Retrieval evidence/ });
  await expect(evidence).toBeVisible({ timeout: 30_000 });
  await expect(evidence).toContainText("evidence sufficient");
  await evidence.click();
  await expect(page.getByRole("cell", { name: new RegExp(`Cache warmup runbook ${marker}`) }).first()).toBeVisible();
});

test("restricted documents are invisible to users without the scope", async ({ page }) => {
  await signIn(page, "engineer@opspilot.demo");
  await nav(page, "Knowledge Base").click();
  await expect(page.getByText("Orders API Runbook")).toBeVisible();
  await expect(page.getByText("Database Credential Rotation")).toHaveCount(0);
  await nav(page, "Ask").click();
  await page.getByLabel("Question", { exact: true }).fill("What is the name of the break-glass database administrator account?");
  await page.getByRole("button", { name: "Ask" }).click();
  await expect(page.getByText("insufficient evidence").first()).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("dba_breakglass")).toHaveCount(0);
});

test("viewer cannot upload or delete", async ({ page }) => {
  await signIn(page, "viewer@opspilot.demo");
  await nav(page, "Knowledge Base").click();
  await expect(page.getByText("Orders API Runbook")).toBeVisible();
  await expect(page.getByText("Upload documentation")).toHaveCount(0);
  await page.getByRole("button", { name: /Orders API Runbook/ }).click();
  await expect(page.getByText("Indexed passages")).toBeVisible();
  await expect(page.getByRole("button", { name: "Delete" })).toHaveCount(0);
  await expect(nav(page, "Activity")).toHaveCount(0);
});

test("the login form signs a user in", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill("engineer@opspilot.demo");
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
});

test("invalid credentials show an error", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill("engineer@opspilot.demo");
  await page.getByLabel("Password").fill("not-the-password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("alert")).toContainText("Invalid email or password");
});
