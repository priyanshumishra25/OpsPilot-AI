import type { IncidentReport } from "../lib/types";
import { Badge } from "./ui";

const ASSESS_TONE = { likely: "bad", possible: "warn", unlikely: "neutral", ruled_out: "neutral" } as const;
const STRENGTH_TONE = { strong: "ok", moderate: "warn", weak: "bad" } as const;

export function ReportView({ report, unverified = [] }: { report: IncidentReport; unverified?: string[] }) {
  return (
    <div className="space-y-4 text-[13px]">
      <section>
        <h3 className="text-[12px] font-semibold uppercase tracking-wide text-ink-3">Summary</h3>
        <p className="mt-1 text-ink">{report.incident_summary}</p>
        <div className="mt-1 flex flex-wrap gap-1">{report.relevant_services.map((s) => <Badge key={s}>{s}</Badge>)}</div>
      </section>
      {report.most_likely_cause && (
        <section className="rounded-md border border-line bg-panel-2 p-3">
          <h3 className="text-[12px] font-semibold uppercase tracking-wide text-ink-3">Most likely cause</h3>
          <p className="mt-1 font-medium">{report.most_likely_cause}</p>
          <p className="mt-1 text-[12px] text-ink-2">
            <Badge tone={STRENGTH_TONE[report.confidence.evidence_strength]}>{report.confidence.evidence_strength} evidence</Badge>{" "}
            {report.confidence.explanation}
          </p>
        </section>
      )}
      <ListSection title="Observed symptoms" items={report.observed_symptoms} />
      <section>
        <h3 className="mb-1 text-[12px] font-semibold uppercase tracking-wide text-ink-3">Evidence</h3>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-[12px]">
            <tbody className="divide-y divide-line">
              {report.evidence.map((e) => (
                <tr key={e.id} id={`evidence-${e.id}`}>
                  <td className="py-1.5 pr-2 font-mono font-semibold">{e.id}</td>
                  <td className="pr-2"><Badge>{e.kind}</Badge></td>
                  <td className="pr-2 text-ink">{e.observation}</td>
                  <td className="font-mono text-[11px] text-ink-3">
                    {e.source}
                    {unverified.includes(e.id) && <Badge tone="warn" title="Source did not match a successful tool call in this session">unverified</Badge>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      {report.timeline.length > 0 && (
        <section>
          <h3 className="mb-1 text-[12px] font-semibold uppercase tracking-wide text-ink-3">Timeline</h3>
          <ol className="space-y-1 border-l border-line pl-3">
            {report.timeline.map((t, i) => (
              <li key={i}><span className="font-mono text-[11px] text-ink-3">{t.time}</span> {t.event} {t.evidence.map((e) => <span key={e} className="font-mono text-[11px] text-accent"> [{e}]</span>)}</li>
            ))}
          </ol>
        </section>
      )}
      <section>
        <h3 className="mb-1 text-[12px] font-semibold uppercase tracking-wide text-ink-3">Candidate causes</h3>
        <ul className="space-y-1">
          {report.candidate_causes.map((c, i) => (
            <li key={i} className="flex flex-wrap items-baseline gap-2">
              <Badge tone={ASSESS_TONE[c.assessment]}>{c.assessment.replace("_", " ")}</Badge>
              <span>{c.cause}</span>
              <span className="font-mono text-[11px] text-ink-3">{c.supporting_evidence.length > 0 && `for: ${c.supporting_evidence.join(", ")}`}{c.contradicting_evidence.length > 0 && ` against: ${c.contradicting_evidence.join(", ")}`}</span>
            </li>
          ))}
        </ul>
      </section>
      {report.recommended_actions.length > 0 && (
        <section>
          <h3 className="mb-1 text-[12px] font-semibold uppercase tracking-wide text-ink-3">Recommended actions</h3>
          <ul className="space-y-2">
            {report.recommended_actions.map((a, i) => (
              <li key={i} className="rounded border border-line p-2">
                <div className="flex flex-wrap items-center gap-2 font-medium">{a.action}
                  <Badge tone={a.risk === "low" ? "ok" : a.risk === "medium" ? "warn" : "bad"}>{a.risk} risk</Badge>
                  {a.requires_approval && <Badge tone="info">needs approval</Badge>}
                </div>
                <p className="mt-0.5 text-[12px] text-ink-2">{a.rationale}</p>
              </li>
            ))}
          </ul>
        </section>
      )}
      <ListSection title="Missing information" items={report.missing_information} />
      <section>
        <h3 className="text-[12px] font-semibold uppercase tracking-wide text-ink-3">Risk assessment</h3>
        <p className="mt-1 text-ink-2">{report.risk_assessment}</p>
      </section>
    </div>
  );
}

function ListSection({ title, items }: { title: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <section>
      <h3 className="text-[12px] font-semibold uppercase tracking-wide text-ink-3">{title}</h3>
      <ul className="mt-1 list-disc space-y-0.5 pl-5 text-ink-2">{items.map((s, i) => <li key={i}>{s}</li>)}</ul>
    </section>
  );
}
