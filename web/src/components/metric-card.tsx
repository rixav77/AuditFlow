import { useEffect, useRef, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";

export function AnimatedCounter({
  end,
  decimals = 0,
  suffix = "",
  prefix = "",
}: {
  end: number;
  decimals?: number;
  suffix?: string;
  prefix?: string;
}) {
  const [count, setCount] = useState(0);
  const ref = useRef<HTMLDivElement>(null);
  const [hasAnimated, setHasAnimated] = useState(false);

  useEffect(() => {
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting && !hasAnimated) {
          setHasAnimated(true);
          const duration = 1200;
          const startTime = performance.now();
          const animate = (t: number) => {
            const progress = Math.min((t - startTime) / duration, 1);
            const eased = 1 - Math.pow(1 - progress, 3);
            setCount(eased * end);
            if (progress < 1) requestAnimationFrame(animate);
          };
          requestAnimationFrame(animate);
        }
      },
      { threshold: 0.5 },
    );
    if (ref.current) observer.observe(ref.current);
    return () => observer.disconnect();
  }, [end, hasAnimated]);

  return (
    <div ref={ref} className="font-display text-5xl lg:text-6xl font-light text-foreground/90 tracking-tight">
      {prefix}
      {count.toLocaleString("en-IN", { maximumFractionDigits: decimals, minimumFractionDigits: decimals })}
      {suffix}
    </div>
  );
}

export function MetricCard({
  label,
  value,
  decimals = 0,
  suffix = "",
  hint,
}: {
  label: string;
  value: number;
  decimals?: number;
  suffix?: string;
  hint?: string;
}) {
  return (
    <Card className="gap-3">
      <CardHeader className="gap-2">
        <CardDescription className="flex items-center gap-2 font-mono text-[11px] uppercase tracking-wider">
          <span className="h-px w-4 bg-foreground/30" />
          {label}
        </CardDescription>
        <CardTitle>
          <AnimatedCounter end={value} decimals={decimals} suffix={suffix} />
        </CardTitle>
      </CardHeader>
      {hint ? (
        <CardContent className="text-muted-foreground text-xs leading-relaxed">{hint}</CardContent>
      ) : null}
    </Card>
  );
}
