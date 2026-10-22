import { useQuery } from "@tanstack/react-query";
import { Fragment, useEffect, useState } from "react";
import { PageHeader } from "../components/Layout";
import { Badge, Card, ErrorBanner, Spinner } from "../components/ui";
import { api } from "../lib/api";

interface SettingsOverview {
  models: Record<string, unknown>;
  retrieval: Record<string, number>;
  agent_limits: Record<string, number>;
  approvals: Record<string, unknown>;
  credentials: Record<string, boolean>;
  you: { role: string; permissions: string[]; tool_scopes: string[]; access_scopes: string[] };
  roles: Record<string, { permissions: string[]; tool_scopes: string[] }>;
  members?: { email: string; name: string; role: string; access_scopes: string[] }[];
}

export function SettingsPage() {
  const q = useQuery({ queryKey: ["settings"], queryFn: () => api<SettingsOverview>("/api/v1/settings") });
  const [theme, setTheme] = useState(() => localStorage.getItem("opspilot.theme") ?? "dark");
  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark");
    localStorage.setItem("opspilot.theme", theme);
  }, [theme]);
  return (
    <>
      <PageHeader title="Settings" description="Your preferences, and the workspace's effective configuration. Configuration is set by deployment environment variables and shown read-only." />
      <div className="grid gap-4 p-6 xl:grid-cols-2">
        <Card title="Preferences">
          <label className="flex items-center gap-2 text-[13px]">Theme
            <select value={theme} onChange={(e) => { setTheme(e.target.value); }} className="rounded border border-line bg-canvas px-2 py-1">
              <option value="dark">Dark</option><option value="light">Light</option>
            </select>
          </label>
        </Card>
        {q.isLoading && <Spinner />}
        {q.error && <ErrorBanner error={q.error} />}
        {q.data && (
          <>
            <Card title="Your access">
              <KV rows={{ Role: q.data.you.role, "Document scopes": q.data.you.access_scopes.join(", "), Permissions: q.data.you.permissions.join(", "), "Agent tool scopes": q.data.you.tool_scopes.join(", ") }} />
            </Card>
            <Card title="Credentials (presence only; values are never shown)">
              <ul className="space-y-1 text-[13px]">
                {Object.entries(q.data.credentials).map(([k, v]) => <li key={k} className="flex justify-between"><span className="font-mono text-[12px]">{k}</span><Badge tone={v ? "ok" : "warn"}>{v ? "configured" : "not configured"}</Badge></li>)}
              </ul>
            </Card>
            <Card title="Models and pricing"><KV rows={flatten(q.data.models)} /></Card>
            <Card title="Retrieval"><KV rows={flatten(q.data.retrieval)} /></Card>
            <Card title="Agent limits and approvals"><KV rows={{ ...flatten(q.data.agent_limits), ...flatten(q.data.approvals) }} /></Card>
            <Card title="Roles" className="xl:col-span-2">
              <table className="w-full text-left text-[12px]">
                <thead className="text-[11px] uppercase tracking-wide text-ink-3"><tr><th className="py-1">Role</th><th>Permissions</th><th>Agent tool scopes</th></tr></thead>
                <tbody className="divide-y divide-line">{Object.entries(q.data.roles).map(([r, v]) => <tr key={r}><td className="py-1.5 font-medium">{r}</td><td>{v.permissions.join(", ")}</td><td>{v.tool_scopes.join(", ")}</td></tr>)}</tbody>
              </table>
            </Card>
            {q.data.members && (
              <Card title="Members" className="xl:col-span-2">
                <table className="w-full text-left text-[13px]">
                  <thead className="text-[11px] uppercase tracking-wide text-ink-3"><tr><th className="py-1">Name</th><th>Email</th><th>Role</th><th>Scopes</th></tr></thead>
                  <tbody className="divide-y divide-line">{q.data.members.map((m) => <tr key={m.email}><td className="py-1.5">{m.name}</td><td>{m.email}</td><td><Badge>{m.role}</Badge></td><td>{m.access_scopes.join(", ")}</td></tr>)}</tbody>
                </table>
                <p className="mt-2 text-[12px] text-ink-3">Members are managed with <span className="font-mono">python -m opspilot.cli create-user</span>.</p>
              </Card>
            )}
          </>
        )}
      </div>
    </>
  );
}

function flatten(o: Record<string, unknown>, prefix = ""): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(o)) {
    if (v && typeof v === "object" && !Array.isArray(v)) Object.assign(out, flatten(v as Record<string, unknown>, `${prefix}${k}.`));
    else out[`${prefix}${k}`] = v === null || v === undefined ? "—" : JSON.stringify(v).replace(/^"|"$/g, "");
  }
  return out;
}

function KV({ rows }: { rows: Record<string, string> }) {
  return (
    <dl className="grid grid-cols-[minmax(10rem,auto)_1fr] gap-x-4 gap-y-1 text-[13px]">
      {Object.entries(rows).map(([k, v]) => (
        <Fragment key={k}><dt className="font-mono text-[12px] text-ink-3">{k}</dt><dd className="break-words">{v}</dd></Fragment>
      ))}
    </dl>
  );
}
