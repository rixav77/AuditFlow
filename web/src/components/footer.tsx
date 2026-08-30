import { FooterGlow } from "@/components/visuals";

export function Footer() {
  return (
    <footer className="relative border-t border-foreground/10 overflow-hidden">
      <FooterGlow />
      <div className="relative z-10 max-w-[1400px] mx-auto px-6 lg:px-12">
        <div className="py-12 lg:py-16">
          <div className="grid grid-cols-2 md:grid-cols-6 gap-12 lg:gap-8">
            <div className="col-span-3">
              <div className="inline-flex items-center gap-2 mb-6">
                <span className="text-2xl font-sans">AuditFlow</span>
              </div>
              <p className="text-muted-foreground leading-relaxed mb-8 max-w-sm">
                Multi-source financial reconciliation: link, reconcile, investigate,
                and abstain — deterministically. Every answer carries its evidence.
              </p>
            </div>
            <div>
              <h3 className="text-sm font-medium mb-6">Product</h3>
              <ul className="space-y-4 text-sm text-muted-foreground">
                <li>Reconciliation engine</li>
                <li>Evidence drawer</li>
                <li>Investigator assistant</li>
              </ul>
            </div>
            <div>
              <h3 className="text-sm font-medium mb-6">Integrity</h3>
              <ul className="space-y-4 text-sm text-muted-foreground">
                <li>Citation verification</li>
                <li>Grounded memory</li>
                <li>Standing evaluation</li>
              </ul>
            </div>
            <div>
              <h3 className="text-sm font-medium mb-6">Built with</h3>
              <ul className="space-y-4 text-sm text-muted-foreground font-mono text-xs">
                <li>5-pass linkage</li>
                <li>12 read-only tools</li>
                <li>Seed-replayable</li>
              </ul>
            </div>
          </div>
        </div>

        <div className="py-8 border-t border-foreground/10 flex flex-col md:flex-row items-center justify-between gap-4">
          <p className="text-sm text-muted-foreground">
            Razorpay Buildathon · Track 04
          </p>
          <div className="flex items-center gap-4 text-sm text-muted-foreground">
            <span className="flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-green-500" />
              Deterministic core operational
            </span>
          </div>
        </div>
      </div>
    </footer>
  );
}
