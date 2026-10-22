import { Bar, BarChart, CartesianGrid, LabelList, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

const AXIS = { stroke: "var(--line)", tick: { fill: "var(--ink-3)", fontSize: 11 }, tickLine: false } as const;

interface Datum { label: string; value: number }

function ValueTooltip({ active, payload, unit }: { active?: boolean; payload?: { payload: Datum }[]; unit: string }) {
  const d = active ? payload?.[0]?.payload : undefined;
  if (!d) return null;
  return (
    <div className="rounded-md border border-line bg-panel px-2 py-1 text-[12px] shadow-sm">
      <strong className="text-ink">{d.value.toFixed(unit === "ms" ? 0 : 3)}{unit === "ms" ? " ms" : ""}</strong>
      <span className="ml-1 text-ink-3">{d.label}</span>
    </div>
  );
}

/** Single-series horizontal bars (one hue, no legend; the card title names the measure). */
export function HBarChart({ data, unit, domain }: { data: Datum[]; unit: "ratio" | "ms"; domain?: [number, number] }) {
  return (
    <ResponsiveContainer width="100%" height={Math.max(120, data.length * 30 + 30)}>
      <BarChart data={data} layout="vertical" margin={{ left: 8, right: 72, top: 4, bottom: 4 }}>
        <CartesianGrid horizontal={false} stroke="var(--line)" strokeOpacity={0.6} />
        <XAxis type="number" domain={domain ?? [0, "auto"]} {...AXIS} />
        <YAxis type="category" dataKey="label" width={150} {...AXIS} axisLine={false} />
        <Tooltip cursor={{ fill: "var(--panel-2)" }} content={<ValueTooltip unit={unit} />} />
        <Bar dataKey="value" fill="var(--chart-1)" barSize={14} radius={[0, 4, 4, 0]} isAnimationActive={false}>
          <LabelList dataKey="value" position="right" formatter={(v: unknown) => (typeof v === "number" ? (unit === "ms" ? `${Math.round(v)} ms` : v.toFixed(3)) : "")}
            style={{ fill: "var(--ink-2)", fontSize: 11 }} />
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

export function TrendChart({ data }: { data: Datum[] }) {
  return (
    <ResponsiveContainer width="100%" height={180}>
      <LineChart data={data} margin={{ left: 0, right: 16, top: 8, bottom: 4 }}>
        <CartesianGrid vertical={false} stroke="var(--line)" strokeOpacity={0.6} />
        <XAxis dataKey="label" {...AXIS} />
        <YAxis domain={[0, 1]} width={36} {...AXIS} axisLine={false} />
        <Tooltip cursor={{ stroke: "var(--ink-3)", strokeWidth: 1 }} content={<ValueTooltip unit="ratio" />} />
        <Line dataKey="value" stroke="var(--chart-1)" strokeWidth={2} dot={{ r: 4, fill: "var(--chart-1)", stroke: "var(--panel)", strokeWidth: 2 }} isAnimationActive={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}
