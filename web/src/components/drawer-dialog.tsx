import { Badge } from "@/components/ui/badge";
import { ClsBadge } from "@/components/cls-badge";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import type { Drawer } from "@/lib/api";
import { CheckCircle2, Loader2 } from "lucide-react";

export function DrawerDialog({
  open,
  onOpenChange,
  drawer,
  loading,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  drawer: Drawer | null;
  loading: boolean;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        {loading || !drawer ? (
          <p className="text-muted-foreground flex items-center gap-2 text-sm">
            <Loader2 className="size-4 animate-spin" /> Building evidence drawer…
          </p>
        ) : (
          <>
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2">
                <span className="font-mono">{drawer.work_key}</span>
                <ClsBadge cls={String(drawer.verdict.cls ?? "")} />
              </DialogTitle>
              <DialogDescription className="font-mono">
                {String(drawer.verdict.reason_code ?? "")} · {drawer.members.length} member records
              </DialogDescription>
            </DialogHeader>

            <div className="space-y-4 text-sm">
              <div className="rounded-lg border p-3">
                <div className="mb-1 flex items-center gap-2">
                  <span className="text-xs font-semibold tracking-wide uppercase">Narrative</span>
                  <Badge variant={drawer.explanation_source === "llm" ? "secondary" : "outline"}>
                    {drawer.explanation_source}
                  </Badge>
                  {drawer.verification.verified ? (
                    <Badge variant="ok" className="gap-1">
                      <CheckCircle2 className="size-3" /> citations verified
                    </Badge>
                  ) : (
                    <Badge variant="destructive">verification failed</Badge>
                  )}
                </div>
                <p className="leading-relaxed">{drawer.explanation}</p>
                {drawer.verification.citation_recall != null && (
                  <p className="text-muted-foreground mt-2 font-mono text-xs">
                    recall {drawer.verification.citation_recall} · precision {drawer.verification.citation_precision}
                    {drawer.verification.id_errors.length > 0 &&
                      ` · ID errors: ${drawer.verification.id_errors.join(", ")}`}
                  </p>
                )}
              </div>

              <div>
                <p className="mb-1 text-xs font-semibold tracking-wide uppercase">Member records</p>
                <div className="flex flex-wrap gap-1">
                  {drawer.members.map((m) => (
                    <Badge key={m} variant="outline" className="font-mono text-[10px]">
                      {m}
                    </Badge>
                  ))}
                </div>
              </div>

              {drawer.findings.length > 0 && (
                <div>
                  <p className="mb-1 text-xs font-semibold tracking-wide uppercase">Findings</p>
                  <pre className="bg-muted overflow-x-auto rounded-lg p-3 font-mono text-xs">
                    {JSON.stringify(drawer.findings, null, 2)}
                  </pre>
                </div>
              )}

              <div>
                <p className="mb-1 text-xs font-semibold tracking-wide uppercase">Source records</p>
                <pre className="bg-muted max-h-56 overflow-auto rounded-lg p-3 font-mono text-xs">
                  {JSON.stringify(drawer.records, null, 2)}
                </pre>
              </div>
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
