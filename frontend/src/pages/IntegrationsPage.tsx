import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { PlugZap, RotateCcw, Zap } from "lucide-react";
import { PageHeader } from "../components/Layout";
import { Badge, Button, Card, ErrorBanner, Spinner, StatusBadge } from "../components/ui";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { timeAgo } from "../lib/format";
import type { DemoState, IntegrationStatus } from "../lib/types";

export function IntegrationsPage() {
  const { can } = useAuth();
  const q = useQuery({ queryKey: ["integrations"], queryFn: () => api<IntegrationStatus>("/api/v1/integrations"), refetchInterval: 30_000 });
  return (
    <>
      <PageHeader title="Integrations" description="MCP servers the agent uses, checked live with your own permissions." />
      <div className="space-y-4 p-6">
        {q.isLoading && <Spinner label="Checking MCP servers" />}
        {q.error && <ErrorBanner error={q.error} />}
        <div className="grid gap-3 md:grid-cols-2">
          {q.data?.servers.map((s) => (
            <Card key={s.server} title={<span className="flex items-center gap-2"><PlugZap className="size-4" aria-hidden />{s.server}</span>}
              actions={<StatusBadge status={s.status === "connected" ? "ok" : "failed"} />}>
              {s.status !== "connected" && <p className="mb-2 text-[12px] text-bad">Unavailable for your role or unreachable{s.error ? ` (${s.error})` : ""}.</p>}
              <ul className="space-y-1 text-[12px]">
                {s.tools.map((t) => <li key={t.name}><span className="font-mono">{t.name}</span> <span className="text-ink-3">— {t.description}</span></li>)}
              </ul>
            </Card>
          ))}
        </div>
        {q.data && (
          <Card title="GitHub">
            <p className="text-[13px]">Token: {q.data.github.token_configured ? <Badge tone="ok">configured</Badge> : <Badge tone="warn">not configured</Badge>}
              {!q.data.github.token_configured && <span className="ml-2 text-[12px] text-ink-3">Local demo repositories work without one; GitHub code search needs it.</span>}</p>
            <p className="mt-2 text-[12px] text-ink-3">Allow-listed repositories: {q.data.github.repositories.join(", ") || "none"}</p>
          </Card>
        )}
        {can("workspace:admin") && <DemoControls />}
      </div>
    </>
  );
}

function DemoControls() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["demo"], queryFn: () => api<DemoState>("/api/v1/demo/scenarios"), refetchInterval: 10_000 });
  const trigger = useMutation({
    mutationFn: (name: string) => api(`/api/v1/demo/scenarios/${name}/trigger`, { method: "POST" }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["demo"] }),
  });
  const reset = useMutation({ mutationFn: () => api("/api/v1/demo/reset", { method: "POST" }), onSuccess: () => void qc.invalidateQueries({ queryKey: ["demo"] }) });
  return (
    <Card title={<span className="flex items-center gap-2">Demo environment <Badge tone="warn">fictional services</Badge></span>}
      actions={<Button variant="ghost" onClick={() => { reset.mutate(); }} loading={reset.isPending}><RotateCcw className="size-3.5" />Reset</Button>}>
      {q.error && <ErrorBanner error={q.error} />}
      {(trigger.error ?? reset.error) && <ErrorBanner error={trigger.error ?? reset.error} />}
      <div className="grid gap-4 md:grid-cols-2">
        <div>
          <h3 className="mb-1 text-[12px] font-semibold uppercase tracking-wide text-ink-3">Services</h3>
          <ul className="space-y-1 text-[13px]">
            {q.data?.services.map((s) => (
              <li key={s.service} className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{s.service}</span><Badge>{s.current_version}</Badge>
                <span className="font-mono text-[11px] text-ink-3">{s.commit_sha.slice(0, 8)}</span>
                <span className="text-[11px] text-ink-3">deployed {timeAgo(s.deployed_at)} by {s.deployed_by}</span>
                {Object.keys(s.active_faults).length > 0 && <Badge tone="bad">fault active</Badge>}
              </li>
            ))}
          </ul>
        </div>
        <div>
          <h3 className="mb-1 text-[12px] font-semibold uppercase tracking-wide text-ink-3">Failure scenarios</h3>
          <ul className="space-y-2">
            {q.data?.scenarios.map((sc) => (
              <li key={sc.name} className="flex items-start gap-2 text-[12px]">
                <Button variant="ghost" onClick={() => { trigger.mutate(sc.name); }} disabled={trigger.isPending} aria-label={`Trigger ${sc.name}`}><Zap className="size-3.5" />{sc.name}</Button>
                <span className="pt-1 text-ink-3">{sc.description}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </Card>
  );
}
