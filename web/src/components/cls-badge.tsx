import { Badge, badgeVariants } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

export const CLS_LABELS: Record<string, string> = {
  matched: "matched",
  matched_after_reasoning: "matched · reasoning",
  genuine_discrepancy: "discrepancy",
  unresolved: "unresolved",
  data_quality: "data quality",
  ignored_noise: "noise",
};

export function clsBadgeVariant(cls: string): "ok" | "warn" | "destructive" | "outline" | "secondary" {
  if (cls === "matched" || cls === "matched_after_reasoning") return "ok";
  if (cls === "genuine_discrepancy") return "warn";
  if (cls === "unresolved") return "destructive";
  if (cls === "data_quality") return "outline";
  return "secondary";
}

export function ClsBadge({ cls, className }: { cls: string; className?: string }) {
  return (
    <Badge variant={clsBadgeVariant(cls)} className={className}>
      {CLS_LABELS[cls] ?? cls}
    </Badge>
  );
}

export { badgeVariants };
