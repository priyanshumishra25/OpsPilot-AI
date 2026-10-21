// Captures README screenshots from the running application: `npx tsx e2e/screenshots.ts` (not a test).
import { chromium } from "@playwright/test";

const base = process.env.E2E_BASE_URL ?? "http://localhost:8080";
const out = "../docs/screenshots";
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 });
await page.goto(`${base}/login`);
await page.getByLabel("Email").fill("sre@opspilot.demo");
await page.getByLabel("Password").fill(process.env.E2E_DEMO_PASSWORD ?? "opspilot-demo-password");
await page.getByRole("button", { name: "Sign in" }).click();
await page.getByRole("heading", { name: "Overview" }).waitFor();
await page.waitForTimeout(800);
await page.screenshot({ path: `${out}/overview.png` });
const nav = (n: string) => page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: n });
await nav("Knowledge Base").click();
await page.getByRole("button", { name: /Checkout Service Runbook/ }).click();
await page.getByText("Indexed passages").waitFor();
await page.waitForTimeout(500);
await page.screenshot({ path: `${out}/knowledge-base.png` });
await nav("Ask").click();
await page.getByLabel("Question", { exact: true }).fill("What is the documented procedure for recovering the checkout service after a database connection failure?");
await page.getByRole("button", { name: "Ask" }).click();
await page.getByRole("button", { name: /Retrieval evidence/ }).click();
await page.waitForTimeout(500);
await page.screenshot({ path: `${out}/ask-retrieval-evidence.png` });
await browser.close();
