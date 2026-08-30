import { useEffect, useMemo, useState } from "react";
import { api, type Drawer, type Txn, type BatchInfo } from "@/lib/api";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ClsBadge } from "@/components/cls-badge";
import { DrawerDialog } from "@/components/drawer-dialog";
import { Search, ChevronRight } from "lucide-react";

const CLS_FILTERS = ["all", "matched", "matched_after_reasoning", "genuine_discrepancy", "unresolved", "data_quality"];

export function Transactions({ batch }: { batch: BatchInfo | null }) {
  const [txns, setTxns] = useState<Txn[] | null>(null);
  const [filter, setFilter] = useState("all");
  const [query, setQuery] = useState("");
  const [drawer, setDrawer] = useState<Drawer | null>(null);
  const [drawerLoading, setDrawerLoading] = useState(false);

  useEffect(() => {
    if (!batch) return;
    setTxns(null);
    api.transactions(batch.batch_name).then(setTxns).catch(() => setTxns([]));
  }, [batch]);

  const rows = useMemo(
    () =>
      (txns ?? []).filter(
        (t) =>
          (filter === "all" || t.cls === filter) &&
          (query === "" ||
            t.work_key.toLowerCase().includes(query.toLowerCase()) ||
            (t.order?.customer_name ?? "").toLowerCase().includes(query.toLowerCase())),
      ),
    [txns, filter, query],
  );

  async function openDrawer(workKey: string) {
    if (!batch) return;
    setDrawerLoading(true);
    setDrawer(null);
    try {
      setDrawer(await api.drawer(workKey, batch.batch_name));
    } finally {
      setDrawerLoading(false);
    }
  }

  return (
    <div className="space-y-4">
      {/* toolbar */}
      <div className="flex flex-wrap items-center gap-2 rounded-2xl border border-foreground/10 bg-card px-4 py-3 shadow-sm">
        <div className="flex flex-wrap items-center gap-1.5">
          {CLS_FILTERS.map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`rounded-full px-3.5 py-1.5 text-xs font-medium transition-all ${
                filter === f
                  ? "bg-foreground text-background shadow-sm"
                  : "text-muted-foreground hover:bg-accent hover:text-foreground"
              }`}
            >
              {f === "all" ? "All" : f.replace(/_/g, " ")}
            </button>
          ))}
        </div>
        <div className="relative ml-auto">
          <Search className="text-muted-foreground pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search ID or customer…"
            className="border-input placeholder:text-muted-foreground h-8.5 w-60 rounded-full border bg-background py-1.5 pl-9 pr-3 text-sm outline-none transition-[box-shadow] focus-visible:border-ring focus-visible:ring-ring/40 focus-visible:ring-[3px]"
          />
        </div>
      </div>

      {/* results */}
      <div className="overflow-hidden rounded-2xl border border-foreground/10 bg-card shadow-sm">
        <div className="flex items-center justify-between px-5 pt-4 pb-1">
          <span className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground">
            {txns === null ? "loading…" : `${rows.length} bundles${filter !== "all" ? ` · ${filter.replace(/_/g, " ")}` : ""}`}
          </span>
          {batch && (
            <span className="font-mono text-[11px] text-muted-foreground">{batch.batch_name.replace("batch_", "").replace(".db", "")}</span>
          )}
        </div>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Work key</TableHead>
              <TableHead>Class</TableHead>
              <TableHead>Reason code</TableHead>
              <TableHead>Customer</TableHead>
              <TableHead className="text-right">Amount</TableHead>
              <TableHead className="w-8" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {(txns ?? []).length === 0 && (
              <TableRow>
                <TableCell colSpan={6} className="text-muted-foreground p-12 text-center">
                  Loading bundles…
                </TableCell>
              </TableRow>
            )}
            {rows.map((t) => (
              <TableRow
                key={t.work_key}
                className="group cursor-pointer transition-colors hover:bg-foreground/[0.025]"
                onClick={() => openDrawer(t.work_key)}
              >
                <TableCell>
                  <span className="text-xs font-medium">{t.work_key}</span>
                </TableCell>
                <TableCell>
                  <ClsBadge cls={t.cls} />
                </TableCell>
                <TableCell className="text-muted-foreground text-[11px]">{t.reason_code}</TableCell>
                <TableCell>{t.order?.customer_name ?? "—"}</TableCell>
                <TableCell className="text-right text-xs font-medium">
                  {t.order ? rupees(t.order.amount_paise) : "—"}
                </TableCell>
                <TableCell className="w-8">
                  <ChevronRight className="text-muted-foreground/40 size-3.5 opacity-0 transition-opacity group-hover:opacity-100" />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      <DrawerDialog open={drawerLoading || !!drawer} onOpenChange={(o) => !o && setDrawer(null)} drawer={drawer} loading={drawerLoading} />
    </div>
  );
}

function rupees(paise: number | null | undefined): string {
  if (paise == null) return "—";
  return new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 2 }).format(paise / 100);
}
