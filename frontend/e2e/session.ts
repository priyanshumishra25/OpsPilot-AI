import { request, type Page } from "@playwright/test";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";

// Each demo user signs in once per run (global setup) and tests reuse the session, so the suite runs against the
// default login rate limit (10/min) instead of needing it relaxed. The login form itself is covered separately.
export const PASSWORD = process.env.E2E_DEMO_PASSWORD ?? "opspilot-demo-password";
export const API = process.env.E2E_API_URL ?? "http://localhost:8000";
const FILE = new URL(".auth/sessions.json", import.meta.url);
const USERS = ["admin@opspilot.demo", "sre@opspilot.demo", "engineer@opspilot.demo", "viewer@opspilot.demo"];

interface Session { token: string; expiresAt: string }

export default async function globalSetup(): Promise<void> {
  const ctx = await request.newContext({ baseURL: API });
  const sessions: Record<string, Session> = {};
  for (const email of USERS) {
    const r = await ctx.post("/api/v1/auth/login", { data: { email, password: PASSWORD } });
    if (!r.ok()) throw new Error(`login failed for ${email}: ${String(r.status())}`);
    const body = (await r.json()) as { access_token: string; expires_at: string };
    sessions[email] = { token: body.access_token, expiresAt: body.expires_at };
  }
  await ctx.dispose();
  mkdirSync(new URL(".auth/", import.meta.url), { recursive: true });
  writeFileSync(FILE, JSON.stringify(sessions));
}

export function session(email: string): Session {
  const all = JSON.parse(readFileSync(FILE, "utf8")) as Record<string, Session>;
  const s = all[email];
  if (!s) throw new Error(`no session for ${email}`);
  return s;
}

export function token(email: string): string {
  return session(email).token;
}

/** Open the app already signed in as `email` (same sessionStorage entry the login form writes). */
export async function signInAs(page: Page, email: string): Promise<void> {
  const s = session(email);
  await page.addInitScript((value) => { sessionStorage.setItem("opspilot.session", value); }, JSON.stringify(s));
  await page.goto("/");
  await page.getByRole("heading", { name: "Overview" }).waitFor();
}
