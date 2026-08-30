import { useEffect, useState } from "react";
import { api, type BatchInfo, type Metrics } from "@/lib/api";
import { Card, CardContent, CardDescription } from "@/components/ui/card";
import { MetricCard } from "@/components/metric-card";
import { ClsBadge } from "@/components/cls-badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";

function BatchHeader({ batch, metrics }: { batch: BatchInfo; metrics: Metrics }) {
  const hard = batch.generator_meta?.difficulty === "HARD";
  return (
    <div className="flex flex-wrap items-center justify-between gap-4 rounded-2xl border border-foreground/10 bg-card px-6 py-4 shadow-sm">
      <div className="flex flex-wrap items-center gap-3">
        <span className="font-mono text-[15px] font-semibold tracking-tight">
          {batch.batch_name.replace("batch_", "").replace(".db", "")}
        </span>
        <Badge variant={hard ? "warn" : "secondary"} className="font-mono">
          {hard ? "HARD" : batch.generator_meta?.difficulty ?? "?"}
        </Badge>
        <span className="rounded-full border border-foreground/10 bg-muted/50 px-3 py-1 font-mono text-[11px] text-muted-foreground">
          sha {metrics.results_sha256.slice(0, 10)}
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-3 text-sm text-muted-foreground">
        <span>
          engine <b className="font-semibold text-foreground">{metrics.elapsed_ms} ms</b>
        </span>
        <span className="h-4 w-px bg-border/70" />
        <span className="flex items-center gap-1.5 font-mono text-[11px]">
          <span className="h-2 w-2 rounded-full bg-ok" />
          deterministic
        </span>
      </div>
    </div>
  );
}

export function Overview({ batch }: { batch: BatchInfo | null }) {
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!batch) return;
    setMetrics(null);
    setErr(null);
    (async () => {
      try {
        if (!batch.has_verdicts) await api.runBatch(batch.batch_name);
        setMetrics(await api.metrics(batch.batch_name));
      } catch (e) {
        setErr(String(e));
      }
    })();
  }, [batch]);

  if (!batch) return <p className="text-muted-foreground text-sm">No batches found.</p>;
  if (err) return <p className="text-destructive text-sm">{err}</p>;
  if (!metrics)
    return (
      <div className="grid gap-4 md:grid-cols-4">
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-28" />
        ))}
      </div>
    );

  const mix = Object.entries(metrics.class_mix);
  const total = mix.reduce((s, [, n]) => s + n, 0) || 1;
  const exCount = metrics.honest_exception_list.length;

  return (
    <div className="space-y-4">
      <BatchHeader batch={batch} metrics={metrics} />

      {/* Bento grid */}
      <div className="grid gap-4 md:grid-cols-5">
        {/* hero tile — reconciled rate, wide */}
        <Card className="gap-4 md:col-span-2">
          <CardContent className="flex h-full flex-col justify-between gap-5">
            <div>
              <CardDescription className="flex items-center gap-2 font-mono text-[11px] uppercase tracking-wider">
                <span className="h-px w-4 bg-foreground/30" />
                reconciled rate
              </CardDescription>
              <div className="font-display mt-1 text-5xl font-light text-foreground/90 tracking-tight lg:text-6xl">
                {(metrics.reconciled_rate * 100).toFixed(1)}
                <span className="text-foreground/40">%</span>
              </div>
              <p className="text-muted-foreground mt-2 text-sm leading-relaxed">
                Clean matches ÷ all bundles — the number we refuse to inflate.
              </p>
            </div>
            {/* stacked class-mix bar */}
            <div>
              <div className="flex h-2 w-full overflow-hidden rounded-full bg-muted">
                {mix.map(([cls, n]) => (
                  <div key={cls} className={clsColor(cls)} style={{ width: `${(n / total) * 100}%` }} title={cls} />
                ))}
              </div>
              <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
                {mix.map(([cls, n]) => (
                  <span key={cls} className="flex items-center gap-1.5 font-mono text-[10px] text-muted-foreground">
                    <span className={`h-2 w-2 rounded-sm ${clsColor(cls)}`} />
                    {n} {shortCls(cls)}
                  </span>
                ))}
              </div>
            </div>
          </CardContent>
        </Card>

        <MetricCard
          label="Transactions"
          value={metrics.transactions}
          hint="Total bundles processed in this run"
        />
        <MetricCard
          label="Exceptions"
          value={exCount}
          hint={`${exCount > 0 ? metrics.honest_exception_list.filter((e) => e.cls === "unresolved").length : 0} unresolved · each with an evidence drawer`}
        />
        <MetricCard
          label="Throughput"
          value={Math.round(metrics.throughput_orders_per_sec)}
          suffix="/s"
          hint="Orders reconciled per second, deterministic core"
        />
      </div>
<div className="grid gap-4 lg:grid-cols-2">
        {/* class mix — bars */}
        <Card>
          <CardContent className="space-y-3">
            <CardDescription className="flex items-center gap-2 font-mono text-[11px] uppercase tracking-wider">
              <span className="h-px w-4 bg-foreground/30" />
              class mix
            </CardDescription>
            {mix.map(([cls, n]) => (
              <div key={cls} className="space-y-1.5">
                <div className="flex items-center justify-between text-sm">
                  <ClsBadge cls={cls} />
                  <span className="font-mono text-xs">
                    {n} · {((n / total) * 100).toFixed(1)}%
                  </span>
                </div>
                <div className="bg-muted/70 h-1.5 w-full overflow-hidden rounded-full">
                  <div className={`${clsColor(cls)} h-full rounded-full transition-all duration-700`} style={{ width: `${(n / total) * 100}%` }} />
                </div>
              </div>
            ))}
          </CardContent>
        </Card>

        {/* honest exception list */}
        <Card>
          <CardContent className="space-y-1">
            <CardDescription className="flex items-center justify-between gap-2 font-mono text-[11px] uppercase tracking-wider">
              <span className="flex items-center gap-2">
                <span className="h-px w-4 bg-foreground/30" />
                honest exceptions
              </span>
              <Badge variant={exCount ? "warn" : "ok"} className="font-mono text-[10px]">
                {exCount ? `${exCount} open` : "clean"}
              </Badge>
            </CardDescription>
            {metrics.honest_exception_list.slice(0, 7).map((e) => (
              <div key={e.work_key} className="flex items-center justify-between gap-3 border-b border-foreground/[0.04] py-2 text-sm last:border-0">
                <span className="font-mono text-xs">{e.work_key}</span>
                <span className="text-muted-foreground truncate font-mono text-[11px]">{e.reason_code}</span>
                <ClsBadge cls={e.cls} />
              </div>
            ))}
            {exCount === 0 && (
              <p className="text-muted-foreground pt-2 text-sm">Nothing to hide, nothing to show.</p>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function clsColor(cls: string): string {
  switch (cls) {
    case "matched":
      return "bg-ok";
    case "matched_after_reasoning":
      return "bg-foreground/70";
    case "genuine_discrepancy":
      return "bg-warn";
    case "unresolved":
      return "bg-destructive";
    default:
      return "bg-muted-foreground/40";
  }
}

function shortCls(cls: string): string {
  switch (cls) {
    case "matched_after_reasoning":
      return "reasoned";
    case "genuine_discrepancy":
      return "discr";
    case "data_quality":
      return "dq";
    default:
      return cls;
  }
}
