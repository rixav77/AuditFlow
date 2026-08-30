import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
  RadarChart,
  PolarGrid,
  PolarAngleAxis,
  PolarRadiusAxis,
  Radar,
  Legend,
} from "recharts";

/* ── types ─────────────────────────────────────────────────────────── */

interface EvalRow {
  batch: string;
  difficulty?: string;
  transactions?: number | null;
  match_rate?: number;
  root_cause_accuracy?: number;
  abstention_precision?: number;
  abstention_recall?: number;
  unsupported_resolution_rate?: number;
  evidence_precision?: number;
  evidence_recall?: number;
  trap_breakdown?: Record<string, number>;
  failed_cases?: { work_key: string; cause: string; expected_class: string; predicted_class: string }[];
  memory?: { grounded?: { n: number; rate: number | null }; retrieval?: { hit_at_1: number | null } };
}

/* ── palette — darker, richer but not neon ─────────────────────────── */

const SEED_COLORS = [
  "oklch(0.48 0.12 155)",   // deep sage
  "oklch(0.45 0.13 250)",   // deep slate blue
  "oklch(0.50 0.14 310)",   // deep violet
  "oklch(0.55 0.12 70)",    // deep amber
  "oklch(0.45 0.16 27)",    // deep rose
];

const GRID_STROKE = "oklch(0.88 0.01 90)";
const LABEL_COLOR = "oklch(0.45 0.02 60)";

const METRIC_LABELS: Record<string, string> = {
  match_rate: "Match",
  root_cause_accuracy: "Cause Acc",
  abstention_precision: "Abstain P",
  abstention_recall: "Abstain R",
  evidence_precision: "Evidence P",
  evidence_recall: "Evidence R",
};

/* ── tooltip ───────────────────────────────────────────────────────── */

function BarTooltip({ active, payload, label }: { active?: boolean; payload?: Array<{ value: number }>; label?: string }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-lg border border-foreground/10 bg-card px-3.5 py-2 shadow-md">
      <p className="font-mono text-[11px] text-muted-foreground">{label}</p>
      <p className="font-mono text-sm font-medium text-foreground">
        {(payload[0].value * 100).toFixed(1)}%
      </p>
    </div>
  );
}

function RadarTooltip({ active, payload, label }: { active?: boolean; payload?: Array<{ name: string; value: number; color: string }>; label?: string }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-lg border border-foreground/10 bg-card px-3.5 py-2 shadow-md">
      <p className="font-mono text-[11px] font-semibold text-foreground mb-1">{label}</p>
      {payload.map((p) => (
        <p key={p.name} className="font-mono text-[10px] text-muted-foreground flex items-center gap-1.5">
          <span className="w-2 h-2 rounded-full shrink-0" style={{ background: p.color }} />
          {p.name}: <span className="text-foreground font-medium">{(p.value * 100).toFixed(1)}%</span>
        </p>
      ))}
    </div>
  );
}

/* ── 1. Vertical bar chart — aggregate performance ─────────────────── */

export function MetricBarsChart({ rows }: { rows: EvalRow[] }) {
  const internalBatches = rows.filter((r) => r.transactions != null);
  if (internalBatches.length === 0) return null;

  const metrics = Object.entries(METRIC_LABELS);
  const barData = metrics.map(([key, label]) => {
    const values = internalBatches
      .map((r) => r[key as keyof EvalRow])
      .filter((v): v is number => typeof v === "number");
    const avg = values.length > 0 ? values.reduce((a, b) => a + b, 0) / values.length : 0;
    return { metric: label, value: avg, key };
  });

  return (
    <ResponsiveContainer width="100%" height={320}>
      <BarChart data={barData} margin={{ left: 0, right: 8, top: 16, bottom: 8 }}>
        <CartesianGrid vertical={false} stroke={GRID_STROKE} strokeDasharray="3 3" />
        <XAxis
          dataKey="metric"
          tick={{ fill: LABEL_COLOR, fontSize: 11, fontFamily: "var(--font-mono)" }}
          axisLine={{ stroke: GRID_STROKE }}
          tickLine={false}
        />
        <YAxis
          domain={[0.9, 1]}
          tickFormatter={(v: number) => `${(v * 100).toFixed(0)}%`}
          tick={{ fill: LABEL_COLOR, fontSize: 10, fontFamily: "var(--font-mono)" }}
          width={44}
          axisLine={false}
          tickLine={false}
        />
        <Tooltip content={<BarTooltip />} cursor={{ fill: "oklch(0.12 0.01 60 / 0.03)" }} />
        <Bar
          dataKey="value"
          radius={[6, 6, 0, 0]}
          barSize={36}
          fill="oklch(0.55 0.06 60)"
        />
      </BarChart>
    </ResponsiveContainer>
  );
}

/* ── 2. Radar chart — per-seed metric fingerprint ──────────────────── */

export function MetricRadarChart({ rows }: { rows: EvalRow[] }) {
  const metrics = Object.keys(METRIC_LABELS);
  const internalBatches = rows.filter((r) => r.transactions != null);

  const radarData = metrics.map((m) => {
    const point: Record<string, string | number> = { metric: METRIC_LABELS[m] };
    internalBatches.forEach((r) => {
      const seed = r.batch.replace("batch_", "").replace(".db", "");
      const val = r[m as keyof EvalRow];
      point[seed] = typeof val === "number" ? val : 0;
    });
    return point;
  });

  const seeds = internalBatches.map((r) => r.batch.replace("batch_", "").replace(".db", ""));

  // Check which seeds have any imperfect metric
  const isPerfect = (r: EvalRow) =>
    metrics.every((m) => {
      const v = r[m as keyof EvalRow];
      return typeof v === "number" && v >= 0.9999;
    });

  return (
    <ResponsiveContainer width="100%" height={380}>
      <RadarChart data={radarData} cx="50%" cy="50%" outerRadius="70%">
        <PolarGrid stroke="oklch(0.12 0.01 60 / 0.10)" />
        <PolarAngleAxis
          dataKey="metric"
          tick={{ fill: LABEL_COLOR, fontSize: 11, fontFamily: "var(--font-mono)" }}
        />
        <PolarRadiusAxis
          angle={90}
          domain={[0.85, 1]}
          tick={{ fill: LABEL_COLOR, fontSize: 9, fontFamily: "var(--font-mono)" }}
          tickCount={4}
        />
        {/* Render perfect seeds first (behind), imperfect on top */}
        {seeds.map((seed, i) => {
          const batch = internalBatches[i];
          const perfect = isPerfect(batch);
          return (
            <Radar
              key={seed}
              name={seed}
              dataKey={seed}
              stroke={SEED_COLORS[i % SEED_COLORS.length]}
              fill={SEED_COLORS[i % SEED_COLORS.length]}
              fillOpacity={perfect ? 0.03 : 0.12}
              strokeWidth={perfect ? 1.2 : 2.5}
              strokeDasharray={perfect ? "4 3" : undefined}
              dot={perfect
                ? false
                : { r: 4, fill: SEED_COLORS[i % SEED_COLORS.length], strokeWidth: 0 }
              }
            />
          );
        })}
        <Tooltip content={<RadarTooltip />} />
        <Legend
          wrapperStyle={{ fontFamily: "var(--font-mono)", fontSize: 11, paddingTop: 12 }}
          iconType="circle"
          iconSize={8}
        />
      </RadarChart>
    </ResponsiveContainer>
  );
}
