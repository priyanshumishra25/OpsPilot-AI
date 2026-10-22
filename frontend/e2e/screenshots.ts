// Captures README screenshots from the running application: `npx tsx e2e/screenshots.ts` (not a test).
import { chromium, type Page } from "@playwright/test";

const base = process.env.E2E_BASE_URL ?? "http://localhost:8080";
const out = "../docs/screenshots";
const password = process.env.E2E_DEMO_PASSWORD ?? "opspilot-demo-password";
const browser = await chromium.launch();

async function session(email: string): Promise<Page> {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 });
  await page.goto(`${base}/login`);
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.getByRole("heading", { name: "Overview" }).waitFor();
  return page;
}
const nav = (page: Page, n: string) => page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: n });

const sre = await session("sre@opspilot.demo");
await sre.waitForTimeout(800);
await sre.screenshot({ path: `${out}/overview.png` });
await nav(sre, "Knowledge Base").click();
await sre.getByRole("button", { name: /Checkout Service Runbook/ }).click();
await sre.getByText("Indexed passages").waitFor();
await sre.waitForTimeout(500);
await sre.screenshot({ path: `${out}/knowledge-base.png` });
await nav(sre, "Ask").click();
await sre.getByLabel("Question", { exact: true }).fill("What is the documented procedure for recovering the checkout service after a database connection failure?");
await sre.getByRole("button", { name: "Ask" }).click();
await sre.getByRole("button", { name: /Retrieval evidence/ }).click();
await sre.waitForTimeout(500);
await sre.screenshot({ path: `${out}/ask-retrieval-evidence.png` });
await nav(sre, "Approvals").click();
await sre.getByRole("tab", { name: "all" }).click();
await sre.waitForTimeout(800);
await sre.screenshot({ path: `${out}/approvals.png` });
await nav(sre, "Evaluations").click();
await sre.getByText("by retrieval configuration").waitFor();
await sre.waitForTimeout(800);
await sre.screenshot({ path: `${out}/evaluations.png` });

const admin = await session("admin@opspilot.demo");
await nav(admin, "Integrations").click();
await admin.getByText("execute_approved_rollback").waitFor({ timeout: 30_000 });
await admin.waitForTimeout(500);
await admin.screenshot({ path: `${out}/integrations.png`, fullPage: true });
await browser.close();
