import clsx from "clsx";
import { AlertTriangle, Loader2 } from "lucide-react";
import type { ButtonHTMLAttributes, ReactNode } from "react";
import { ApiError } from "../lib/api";

type Tone = "ok" | "warn" | "bad" | "info" | "neutral";

const TONE: Record<Tone, string> = {
  ok: "text-ok bg-ok/10 ring-ok/30",
  warn: "text-warn bg-warn/10 ring-warn/30",
  bad: "text-bad bg-bad/10 ring-bad/30",
  info: "text-info bg-info/10 ring-info/30",
  neutral: "text-ink-2 bg-panel-2 ring-line",
};

export function Badge({ tone = "neutral", children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return (
    <span title={title} className={clsx("inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-medium ring-1 ring-inset", TONE[tone])}>
      {children}
    </span>
  );
}

const STATUS_TONE: Record<string, Tone> = {
  indexed: "ok", succeeded: "ok", ready: "ok", ok: "ok",
  pending: "info", queued: "info", running: "info", processing: "info",
  retry_scheduled: "warn", generation_unavailable: "warn", cancelled: "warn", superseded: "neutral",
  failed: "bad", dead: "bad", not_ready: "bad",
};

export function StatusBadge({ status }: { status: string }) {
  return <Badge tone={STATUS_TONE[status] ?? "neutral"}>{status.replace(/_/g, " ")}</Badge>;
}

export function DemoBadge() {
  return <Badge tone="warn" title="Fictional demonstration data seeded for the local environment">demo data</Badge>;
}

export function Card({ title, actions, children, className }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={clsx("rounded-lg border border-line bg-panel", className)}>
      {(title ?? actions) && (
        <header className="flex items-center justify-between gap-2 border-b border-line px-4 py-2.5">
          <h2 className="text-[13px] font-semibold text-ink">{title}</h2>
          {actions}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function Button({ variant = "primary", loading, className, children, ...rest }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "ghost" | "danger"; loading?: boolean }) {
  return (
    <button
      {...rest}
      disabled={rest.disabled ?? loading}
      className={clsx(
        "inline-flex items-center justify-center gap-1.5 rounded-md px-3 py-1.5 text-[13px] font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50",
        variant === "primary" && "bg-accent text-accent-ink hover:opacity-90",
        variant === "ghost" && "text-ink-2 ring-1 ring-inset ring-line hover:bg-panel-2",
        variant === "danger" && "text-bad ring-1 ring-inset ring-bad/40 hover:bg-bad/10",
        className,
      )}
    >
      {loading && <Loader2 className="size-3.5 animate-spin" aria-hidden />}
      {children}
    </button>
  );
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <div role="status" className="flex items-center gap-2 text-[13px] text-ink-3">
      <Loader2 className="size-4 animate-spin" aria-hidden /> {label}…
    </div>
  );
}

export function ErrorBanner({ error, onRetry }: { error: unknown; onRetry?: (() => void) | undefined }) {
  const message = error instanceof ApiError ? error.message : error instanceof Error ? error.message : "Unexpected error";
  const trace = error instanceof ApiError ? error.traceId : null;
  return (
    <div role="alert" className="flex items-start gap-2 rounded-md border border-bad/40 bg-bad/10 px-3 py-2 text-[13px] text-bad">
      <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
      <div className="flex-1">
        <p>{message}</p>
        {trace && <p className="mt-0.5 font-mono text-[11px] opacity-80">trace {trace}</p>}
      </div>
      {onRetry && <button className="underline" onClick={onRetry}>Retry</button>}
    </div>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-md border border-dashed border-line px-4 py-8 text-center">
      <p className="text-[13px] font-medium text-ink-2">{title}</p>
      {children && <div className="mt-1 text-[12px] text-ink-3">{children}</div>}
    </div>
  );
}

export function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: ReactNode }) {
  return (
    <div className="rounded-lg border border-line bg-panel px-4 py-3">
      <p className="text-[11px] font-medium uppercase tracking-wide text-ink-3">{label}</p>
      <p className="mt-1 text-xl font-semibold tabular-nums text-ink">{value}</p>
      {hint && <p className="mt-0.5 text-[12px] text-ink-3">{hint}</p>}
    </div>
  );
}
