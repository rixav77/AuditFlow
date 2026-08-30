import { useEffect, useState } from "react";

/**
 * Reconciliation curve — the signature visual.
 * Two streams (order inflow & bank credit) draw toward each other and converge;
 * a dot pulses at the reconciled point, then a resolved tail draws onward.
 * Pure SVG + CSS keyframes, looping. Meaningful for the product, not decorative ASCII.
 */
export function ReconCurve({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 420 280" fill="none" className={`w-full h-full ${className}`} aria-hidden>
      {/* soft grid backdrop */}
      <g stroke="currentColor" strokeOpacity="0.06">
        {[70, 140, 210].map((y) => (
          <line key={`h${y}`} x1="0" y1={y} x2="420" y2={y} />
        ))}
        {[105, 210, 315].map((x) => (
          <line key={`v${x}`} x1={x} y1="0" x2={x} y2="280" />
        ))}
      </g>

      {/* order stream — amber */}
      <path
        d="M 8 220 C 90 190 140 170 210 120 S 330 70 388 60"
        pathLength={1}
        stroke="oklch(0.62 0.13 70)"
        strokeWidth="2.5"
        strokeLinecap="round"
        className="recon-draw"
        style={{ animationDelay: "0.15s" }}
      />
      {/* bank stream — dark */}
      <path
        d="M 8 246 C 80 230 150 190 210 120 S 340 84 390 74"
        pathLength={1}
        stroke="oklch(0.12 0.01 60)"
        strokeWidth="2.5"
        strokeLinecap="round"
        className="recon-draw"
        style={{ animationDelay: "0.55s" }}
      />

      {/* converged equilibrium zone */}
      <path
        d="M 210 120 C 262 100 320 92 388 88"
        pathLength={1}
        stroke="oklch(0.45 0.02 60)"
        strokeWidth="2"
        strokeLinecap="round"
        strokeDasharray="1"
        opacity="0"
        className="recon-tail"
      />

      {/* reconciled dot */}
      <circle cx="210" cy="120" r="5" fill="oklch(0.62 0.13 70)" className="recon-dot" />
      <circle cx="210" cy="120" r="11" fill="none" stroke="oklch(0.62 0.13 70)" strokeOpacity="0.3" strokeWidth="1.5" className="recon-dot-ring" />

      {/* labels */}
      <g fill="currentColor" fontFamily="JetBrains Mono Variable, monospace" fontSize="11" className="recon-labels">
        <text x="8" y="214" fill="oklch(0.62 0.13 70)">orders</text>
        <text x="8" y="270" fill="currentColor" opacity="0.55">settlements</text>
        <text x="394" y="96" fill="currentColor" opacity="0.4">✓</text>
        <text x="404" y="64" textAnchor="end" fill="currentColor" opacity="0.45">reconciled</text>
      </g>
    </svg>
  );
}

/** Warm drifting gradient blobs — footer backdrop. Subtle, premium. */
export function FooterGlow({ className = "" }: { className?: string }) {
  return (
    <div className={`pointer-events-none absolute inset-0 overflow-hidden ${className}`} aria-hidden>
      <div className="footer-blob-a" />
      <div className="footer-blob-b" />
    </div>
  );
}

/** Animated ring — counts to value once in view. Determinism ring on Eval. */
export function MetricRing({
  value = 1,
  size = 148,
  stroke = 10,
  label = "deterministic",
}: {
  value?: number;
  size?: number;
  stroke?: number;
  label?: string;
}) {
  const [on, setOn] = useState(false);
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;

  useEffect(() => {
    const t = setTimeout(() => setOn(true), 120);
    return () => clearTimeout(t);
  }, []);

  return (
    <div className="relative" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="currentColor" strokeOpacity="0.08" strokeWidth={stroke} />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke="currentColor"
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={c}
          strokeDashoffset={on ? c * (1 - value) : c}
          style={{ transition: "stroke-dashoffset 1.4s cubic-bezier(0.22,1,0.36,1)" }}
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="font-display text-3xl">{Math.round(value * 100)}</span>
        <span className="text-muted-foreground font-mono text-[10px]">{label}</span>
      </div>
    </div>
  );
}
