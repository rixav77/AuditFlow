import { useEffect, useState } from "react";
import { MetricRing } from "@/components/visuals";
import { MetricRadarChart, MetricBarsChart } from "@/components/eval-charts";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";

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

export function Eval() {
  const [rows, setRows] = useState<EvalRow[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/batches/eval-report")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((d) => setRows(d.batches))
      .catch((e) => setErr(String(e)));
  }, []);

  if (err) return <p className="text-destructive text-sm">Eval report unavailable ({err}) — run `uv run python -m eval.report`.</p>;
  if (!rows)
    return (
      <div className="grid gap-4 md:grid-cols-3">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-40" />
        ))}
      </div>
    );

  const METRICS: [string, keyof EvalRow][] = [
    ["match", "match_rate"],
    ["cause acc", "root_cause_accuracy"],
    ["abstain P", "abstention_precision"],
    ["abstain R", "abstention_recall"],
    ["URR", "unsupported_resolution_rate"],
    ["evidence P", "evidence_precision"],
    ["evidence R", "evidence_recall"],
  ];
  const fmt = (v: unknown) =>
    typeof v === "number" ? String(parseFloat(v.toFixed(4))) : "—";

  // Summary
  const internalBatches = rows.filter((r) => r.transactions != null);
  const totalTxns = internalBatches.reduce((s, r) => s + (r.transactions ?? 0), 0);
  const totalFailed = rows.reduce((s, r) => s + (r.failed_cases?.length ?? 0), 0);
  const avgMatch = internalBatches.length > 0
    ? internalBatches.reduce((s, r) => s + (r.match_rate ?? 0), 0) / internalBatches.length
    : 0;

  return (
    <div className="space-y-10">
      {/* ── Header ──────────────────────────────────────────── */}
      <div className="relative flex items-end justify-between gap-6 overflow-hidden">
        <div>
          <h2 className="font-display text-2xl">Standing evaluation</h2>
          <p className="text-muted-foreground text-sm mt-1">
            No LLM judge — every number is deterministic. Misses are named, never tuned away.
          </p>
        </div>
        <div className="text-foreground shrink-0 hidden md:block opacity-90">
          <MetricRing value={1} label="deterministic" />
        </div>
      </div>

      {/* ── Summary pills ──────────────────────────────────── */}
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        {[
          { label: "Seeds Tested", value: String(internalBatches.length), warn: false },
          { label: "Transactions", value: totalTxns.toLocaleString("en-IN"), warn: false },
          { label: "Avg Match Rate", value: `${(avgMatch * 100).toFixed(1)}%`, warn: avgMatch < 0.99 },
          { label: "Named Misses", value: String(totalFailed), warn: totalFailed > 0 },
        ].map((s) => (
          <div key={s.label} className="rounded-xl border border-foreground/8 bg-card px-5 py-4">
            <p className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">{s.label}</p>
            <p className={`font-display text-3xl font-light mt-1 ${s.warn ? "text-warn" : "text-foreground/80"}`}>{s.value}</p>
          </div>
        ))}
      </div>

      {/* ── Charts row ─────────────────────────────────────── */}
      <div className="grid gap-6 lg:grid-cols-2">
        {/* Vertical histogram */}
        <Card>
          <CardContent className="pt-6 pb-4">
            <div className="flex items-center gap-2 mb-4">
              <span className="h-px w-4 bg-foreground/30" />
              <span className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground">aggregate performance</span>
            </div>
            <MetricBarsChart rows={rows} />
          </CardContent>
        </Card>

        {/* Radar fingerprint */}
        <Card>
          <CardContent className="pt-6 pb-4">
            <div className="flex items-center gap-2 mb-4">
              <span className="h-px w-4 bg-foreground/30" />
              <span className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground">metric fingerprint</span>
            </div>
            <MetricRadarChart rows={rows} />
          </CardContent>
        </Card>
      </div>

      {/* ── Per-seed detail cards ──────────────────────────── */}
      <div>
        <div className="flex items-center gap-2 mb-4">
          <span className="h-px w-4 bg-foreground/30" />
          <span className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground">per-seed breakdown</span>
        </div>
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {rows.map((r) => (
            <Card key={r.batch}>
              <CardHeader>
                <CardTitle className="flex items-center justify-between">
                  <span className="font-mono text-sm">{r.batch.replace("batch_", "").replace(".db", "")}</span>
                  {r.difficulty ? <Badge variant={r.difficulty === "HARD" ? "warn" : "secondary"}>{r.difficulty}</Badge> : null}
                </CardTitle>
                <CardDescription>
                  {r.transactions != null ? `${r.transactions} transactions` : "external benchmark"}
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-1.5 font-mono text-xs">
                {METRICS.map(([label, key]) => (
                  <div key={key} className="flex justify-between">
                    <span className="text-muted-foreground">{label}</span>
                    <span className={typeof r[key] === "number" && (r[key] as number) < 1 ? "text-warn" : ""}>
                      {fmt(r[key])}
                    </span>
                  </div>
                ))}
                {r.memory?.grounded?.rate != null && (
                  <div className="flex justify-between border-t pt-1.5">
                    <span className="text-muted-foreground">memory grounded</span>
                    <span>{r.memory.grounded.rate} (n={r.memory.grounded.n})</span>
                  </div>
                )}
                {r.memory?.retrieval?.hit_at_1 != null && (
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">retrieval hit@1</span>
                    <span>{r.memory.retrieval.hit_at_1}</span>
                  </div>
                )}
                {(r.failed_cases ?? []).length > 0 && (
                  <div className="border-t pt-1.5">
                    <p className="text-muted-foreground mb-1">failed cases (named):</p>
                    {r.failed_cases!.map((fc) => (
                      <p key={fc.work_key} className="text-warn">
                        {fc.work_key} · {fc.cause}: {fc.expected_class} → {fc.predicted_class}
                      </p>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
          ))}
        </div>
      </div>
    </div>
  );
}
