import { useEffect, useState } from "react";
import { api, type BatchInfo } from "@/lib/api";
import { Navigation } from "@/components/navigation";
import { Footer } from "@/components/footer";
import { ReconCurve } from "@/components/visuals";
import { Overview } from "@/sections/overview";
import { Transactions } from "@/sections/transactions";
import { Chat } from "@/sections/chat";
import { Eval } from "@/sections/eval";
import { ArrowRight } from "lucide-react";

const HEADINGS: Record<string, { kicker: string; title: string; sub: string }> = {
  overview: {
    kicker: "Live reconciliation",
    title: "Every rupee, accounted for.",
    sub: "Multi-source bundles linked, reconciled, and investigated — with the exceptions we cannot explain named, not hidden.",
  },
  transactions: {
    kicker: "Transaction ledger",
    title: "One row per bundle.",
    sub: "Click any transaction to open its evidence drawer: member records, findings, and a citation-verified narrative.",
  },
  chat: {
    kicker: "Investigator assistant",
    title: "Ask, and it investigates.",
    sub: "A bounded tool loop answers from the records only — 12 read-only tools, citation-disciplined, with long-term memory.",
  },
  eval: {
    kicker: "Standing evaluation",
    title: "Numbers we publish on purpose.",
    sub: "No LLM judge. Deterministic metrics against ground truth, with the failed cases named — including on fresh seeds.",
  },
};

export default function App() {
  const [batches, setBatches] = useState<BatchInfo[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [page, setPage] = useState("overview");

  useEffect(() => {
    api.batches().then((bs) => {
      setBatches(bs);
      const ready = bs.find((b) => b.has_verdicts) ?? bs[0];
      if (ready) setSelected(ready.batch_name);
    });
  }, []);

  const batch = batches.find((b) => b.batch_name === selected) ?? null;
  const head = HEADINGS[page];
  const titleChars = Array.from(head.title);

  return (
    <div className="noise-overlay relative min-h-screen">
      <Navigation
        active={page}
        onNavigate={(k) => {
          setPage(k);
          window.scrollTo({ top: 0, behavior: "smooth" });
        }}
        batches={batches}
        selected={selected}
        onSelectBatch={setSelected}
      />

      <main className="relative">
        {/* Hero band — char-in headline + reconciliation curve */}
        <section className="relative overflow-hidden border-b border-foreground/10">
          <div className="absolute inset-0 grid-lines opacity-30" aria-hidden />
          <div className="absolute -right-16 top-1/2 -translate-y-1/2 w-[460px] h-[300px] lg:w-[560px] lg:h-[360px] opacity-90 pointer-events-none hidden md:block">
            <ReconCurve />
          </div>
          <div className="relative z-10 max-w-[1400px] mx-auto px-6 lg:px-12 pt-36 pb-14 lg:pt-44">
            <div className="mb-8">
              <span className="inline-flex items-center gap-3 text-sm font-mono text-muted-foreground">
                <span className="w-8 h-px bg-foreground/30" />
                {head.kicker}
                {batch && (
                  <span className="text-foreground/60">
                    · {batch.batch_name.replace("batch_", "").replace(".db", "")}
                    {batch.generator_meta?.difficulty === "HARD" ? " · HARD" : ""}
                  </span>
                )}
              </span>
            </div>
            <h1 className="font-display text-[clamp(1.75rem,3.5vw,3.75rem)] leading-[1.1] tracking-tight max-w-4xl">
              {titleChars.map((c, i) => (
                <span
                  key={i}
                  className="animate-char-in inline-block"
                  style={{ animationDelay: `${i * 22}ms` }}
                >
                  {c === " " ? "\u00A0" : c}
                </span>
              ))}
            </h1>
            <p className="text-muted-foreground mt-5 max-w-2xl leading-relaxed text-lg">
              {head.sub}
            </p>
            <div className="flex flex-wrap gap-4 mt-9">
              <button
                onClick={() => setPage("transactions")}
                className="group bg-foreground hover:bg-foreground/90 text-background rounded-full h-12 px-8 text-base font-medium inline-flex items-center gap-2 transition-all active:scale-[0.98]"
              >
                Open ledger
                <ArrowRight className="w-4 h-4 transition-transform group-hover:translate-x-1" />
              </button>
              <button
                onClick={() => setPage("chat")}
                className="rounded-full h-12 px-8 text-base border border-foreground/25 hover:border-foreground/45 hover:bg-foreground/[0.03] text-foreground font-medium transition-all active:scale-[0.98]"
              >
                Ask the assistant
              </button>
            </div>
          </div>
        </section>

        {/* Source systems marquee — axiom's marquee pattern */}
        {page === "overview" && (
          <div className="border-b border-foreground/10 bg-card/40 overflow-hidden py-3">
            <div className="marquee-track gap-10 text-muted-foreground font-mono text-[11px] tracking-wide">
              {[0, 1].map((half) => (
                <span key={half} className="flex gap-10 shrink-0 items-center">
                  {["ORDERS", "PAYMENTS", "SETTLEMENTS", "BANK STATEMENTS", "REFUNDS", "FEE SCHEDULES", "ADJUSTMENTS", "UTR MATCHES"].map((s) => (
                    <span key={s} className="flex items-center gap-10">
                      <span>{s}</span>
                      <span className="w-1 h-1 rounded-full bg-foreground/20" />
                    </span>
                  ))}
                </span>
              ))}
            </div>
          </div>
        )}

        <section className="max-w-[1400px] mx-auto px-6 lg:px-12 py-12 relative z-10">
          <div style={{ display: page === "overview" ? "block" : "none" }}>
            <Overview batch={batch} />
          </div>
          <div style={{ display: page === "transactions" ? "block" : "none" }}>
            <Transactions batch={batch} />
          </div>
          <div style={{ display: page === "chat" ? "block" : "none" }}>
            <Chat batch={batch} />
          </div>
          <div style={{ display: page === "eval" ? "block" : "none" }}>
            <Eval />
          </div>
        </section>
      </main>

      <Footer />
    </div>
  );
}
