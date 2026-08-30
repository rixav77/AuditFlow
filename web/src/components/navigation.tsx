import { useState, useEffect } from "react";
import { Menu, X } from "lucide-react";
import { cn } from "@/lib/utils";
import type { BatchInfo } from "@/lib/api";

const navLinks = [
  { name: "Overview", key: "overview" },
  { name: "Transactions", key: "transactions" },
  { name: "Assistant", key: "chat" },
  { name: "Evaluation", key: "eval" },
];

interface NavigationProps {
  active: string;
  onNavigate: (key: string) => void;
  batches: BatchInfo[];
  selected: string | null;
  onSelectBatch: (name: string) => void;
}

export function Navigation({ active, onNavigate, batches, selected, onSelectBatch }: NavigationProps) {
  const [isScrolled, setIsScrolled] = useState(false);
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);
  const [batchOpen, setBatchOpen] = useState(false);

  useEffect(() => {
    const handleScroll = () => setIsScrolled(window.scrollY > 20);
    window.addEventListener("scroll", handleScroll);
    return () => window.removeEventListener("scroll", handleScroll);
  }, []);

  const current = batches.find((b) => b.batch_name === selected);
  const pill = isScrolled || isMobileMenuOpen;

  return (
    <header className={`fixed z-50 transition-all duration-500 ${pill ? "top-4 left-4 right-4" : "top-0 left-0 right-0"}`}>
      <nav className={cn("mx-auto transition-all duration-500", pill ? "bg-background/80 backdrop-blur-xl border border-foreground/10 rounded-2xl shadow-lg max-w-[1200px]" : "bg-transparent max-w-[1400px]")}>
        <div className={cn("flex items-center justify-between transition-all duration-500 px-6 lg:px-8", pill ? "h-14" : "h-20")}>
          <button onClick={() => onNavigate("overview")} className="flex items-center gap-2">
            <span className={cn("font-sans tracking-tight transition-all duration-500", pill ? "text-xl" : "text-2xl")}>AuditFlow</span>
            <span className={cn("text-muted-foreground font-mono transition-all duration-500", pill ? "text-[10px] mt-0.5" : "text-xs mt-1")}>AI</span>
          </button>

          <div className="hidden md:flex items-center gap-12">
            {navLinks.map((link) => (
              <button
                key={link.key}
                onClick={() => onNavigate(link.key)}
                className={cn("text-sm transition-colors duration-300 relative group", active === link.key ? "text-foreground" : "text-foreground/70 hover:text-foreground")}
              >
                {link.name}
                <span className={cn("absolute -bottom-1 left-0 h-px bg-foreground transition-all duration-300", active === link.key ? "w-full" : "w-0 group-hover:w-full")} />
              </button>
            ))}
          </div>

          <div className="hidden md:flex items-center gap-4 relative">
            <button
              onClick={() => setBatchOpen((o) => !o)}
              className={cn("bg-foreground hover:bg-foreground/90 text-background rounded-full transition-all duration-500 font-medium", pill ? "px-4 h-8 text-xs" : "px-6 h-9 text-sm")}
            >
              {current ? current.batch_name.replace("batch_", "").replace(".db", "") : "Select batch"}
              <span className="ml-2 opacity-60">▾</span>
            </button>
            {batchOpen && (
              <div className="absolute right-0 top-full mt-2 w-64 bg-background border border-foreground/10 rounded-xl shadow-lg overflow-hidden">
                {batches.map((b) => (
                  <button
                    key={b.batch_name}
                    onClick={() => {
                      onSelectBatch(b.batch_name);
                      setBatchOpen(false);
                    }}
                    className={cn("w-full text-left px-4 py-2.5 text-sm flex items-center justify-between hover:bg-accent transition-colors", b.batch_name === selected && "bg-accent/60")}
                  >
                    <span className="font-mono text-xs">{b.batch_name.replace("batch_", "").replace(".db", "")}</span>
                    <span className={cn("font-mono text-[10px]", b.generator_meta?.difficulty === "HARD" ? "text-warn" : "text-muted-foreground")}>
                      {b.generator_meta?.difficulty}
                    </span>
                  </button>
                ))}
              </div>
            )}
          </div>

          <button onClick={() => setIsMobileMenuOpen(!isMobileMenuOpen)} className="md:hidden p-2" aria-label="Toggle menu">
            {isMobileMenuOpen ? <X className="w-6 h-6" /> : <Menu className="w-6 h-6" />}
          </button>
        </div>
      </nav>

      {isMobileMenuOpen && (
        <div className="md:hidden fixed inset-0 bg-background z-40">
          <div className="flex flex-col h-full px-8 pt-28 pb-8">
            <div className="flex-1 flex flex-col justify-center gap-8">
              {navLinks.map((link) => (
                <button key={link.key} onClick={() => { onNavigate(link.key); setIsMobileMenuOpen(false); }} className="text-5xl font-display text-foreground hover:text-muted-foreground transition-all">
                  {link.name}
                </button>
              ))}
            </div>
            <div className="flex flex-col gap-3 pt-8 border-t border-foreground/10">
              {batches.map((b) => (
                <button key={b.batch_name} onClick={() => { onSelectBatch(b.batch_name); setIsMobileMenuOpen(false); }} className="text-left font-mono text-sm text-muted-foreground hover:text-foreground">
                  {b.batch_name.replace("batch_", "").replace(".db", "")}
                </button>
              ))}
            </div>
          </div>
        </div>
      )}
    </header>
  );
}
