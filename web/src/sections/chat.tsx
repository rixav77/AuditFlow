import { useRef, useState } from "react";
import { streamChat, type ChatEvent, type BatchInfo } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Send, Wrench, Brain, Sparkles } from "lucide-react";

const SUGGESTIONS = [
  "How many transactions didn't reconcile cleanly? Cite them.",
  "Any lessons from previous runs about unresolved cases?",
  "What is the class mix of this batch?",
];

export function Chat({ batch }: { batch: BatchInfo | null }) {
  const [events, setEvents] = useState<ChatEvent[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const ctrlRef = useRef<AbortController | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const historyRef = useRef<{ role: string; content: string }[]>([]);

  function send(msg: string) {
    if (!batch || busy || !msg.trim()) return;
    setInput("");
    setBusy(true);
    setEvents((prev) => [...prev, { type: "user", content: msg }]);
    const session = `web-${batch.batch_name}`;
    ctrlRef.current = streamChat(
      batch.batch_name,
      msg,
      session,
      historyRef.current,
      (ev) => {
        if (ev.type === "user") return;
        if (ev.type === "answer" && ev.content) {
          historyRef.current = [
            ...historyRef.current,
            { role: "user", content: msg },
            { role: "assistant", content: ev.content },
          ].slice(-16);
        }
        setEvents((prev) => [...prev, ev]);
      },
      () => setBusy(false),
    );
    setTimeout(() => endRef.current?.scrollIntoView({ behavior: "smooth" }), 50);
  }

  return (
    <div className="flex h-[72vh] flex-col overflow-hidden rounded-2xl border border-foreground/10 bg-card shadow-sm">
      {/* header */}
      <div className="flex items-center justify-between border-b border-foreground/[0.06] px-5 py-3.5">
        <div className="flex items-center gap-3">
          <div className="bg-foreground/90 flex size-8 items-center justify-center rounded-full text-background">
            <Sparkles className="size-4" />
          </div>
          <div>
            <p className="text-sm font-semibold leading-tight">Investigator assistant</p>
            <p className="text-muted-foreground font-mono text-[10px]">12 read-only tools · citation-verified · memory-injected</p>
          </div>
        </div>
        <span className="flex items-center gap-1.5 font-mono text-[10px] text-muted-foreground">
          <span className={`size-1.5 rounded-full ${busy ? "animate-pulse bg-warn" : "bg-ok"}`} />
          {busy ? "investigating" : "ready"}
        </span>
      </div>

      {events.some(e => e.type === "memory_context") && (
        <div className="flex items-center gap-3 border-b border-warn/15 bg-warn/5 px-5 py-2 text-xs">
          <div className="text-warn flex shrink-0 items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide">
            <Brain className="size-3" /> memory
          </div>
          <div className="text-muted-foreground truncate font-mono text-[11px]" title={events.slice().reverse().find(e => e.type === "memory_context")?.content}>
            {events.slice().reverse().find(e => e.type === "memory_context")?.content?.replace(/\n/g, " ")}
          </div>
        </div>
      )}

      {/* message stream */}
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-5">
          {events.length === 0 && (
            <div className="text-muted-foreground mx-auto max-w-md space-y-3 py-10 text-center text-sm">
              <p className="leading-relaxed">
                Ask about any transaction in{" "}
                <span className="text-foreground/80 font-mono">
                  {batch?.batch_name.replace("batch_", "").replace(".db", "") ?? "the batch"}
                </span>{" "}
                — the agent reads the records with its tools before it answers.
              </p>
              <div className="flex flex-col items-center gap-2 pt-2">
                {SUGGESTIONS.map((s) => (
                  <button
                    key={s}
                    onClick={() => send(s)}
                    className="w-full max-w-sm rounded-full border border-foreground/15 px-4 py-2 text-xs text-foreground/80 transition-all hover:border-foreground/35 hover:bg-foreground/[0.03]"
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}
          {events.map((ev, i) => {
            if (ev.type === "user")
              return (
                <div key={i} className="flex justify-end">
                  <div className="bg-foreground text-background max-w-[78%] rounded-2xl rounded-br-sm px-4 py-2.5 text-sm leading-relaxed shadow-sm">
                    {ev.content}
                  </div>
                </div>
              );
            if (ev.type === "tool_call")
              return (
                <div key={i} className="flex items-center gap-2.5 pl-1 text-xs">
                  <span className="bg-muted/70 flex size-6 shrink-0 items-center justify-center rounded-full">
                    <Wrench className="text-muted-foreground size-3" />
                  </span>
                  <Badge variant="outline" className="font-mono">
                    {ev.name}
                  </Badge>
                  <span className="text-muted-foreground truncate font-mono text-[11px]">{JSON.stringify(ev.args)}</span>
                </div>
              );
            if (ev.type === "tool_result")
              return (
                <div key={i} className="flex items-center gap-2.5 pl-9 text-xs">
                  <span className="text-muted-foreground font-mono text-[11px]">→ {ev.summary}</span>
                  {ev.citations && ev.citations.length > 0 && (
                    <Badge variant="secondary" className="font-mono text-[10px]">
                      {ev.citations.length} IDs verified
                    </Badge>
                  )}
                </div>
              );
            if (ev.type === "answer")
              return (
                <div key={i} className="flex justify-start">
                  <div className="bg-background max-w-[85%] rounded-2xl rounded-bl-sm border border-foreground/[0.07] px-4 py-3 text-sm leading-relaxed shadow-sm">
                    <p className="whitespace-pre-wrap">{ev.content}</p>
                    {ev.provider && (
                      <div className="text-muted-foreground mt-2.5 flex items-center gap-2 font-mono text-[10px]">
                        <span className="flex items-center gap-1">
                          <span className="size-1 rounded-full bg-ok" />
                          {ev.provider}
                        </span>
                        <span>·</span>
                        <span>{ev.latency_ms} ms</span>
                      </div>
                    )}
                  </div>
                </div>
              );
            return null;
          })}
          {busy && (
            <div className="flex items-center gap-2 pl-1">
              <span className="bg-muted/70 flex size-6 items-center justify-center rounded-full">
                <Sparkles className="text-muted-foreground size-3" />
              </span>
              <div className="flex items-center gap-1">
                <span className="typing-dot" style={{ animationDelay: "0ms" }} />
                <span className="typing-dot" style={{ animationDelay: "150ms" }} />
                <span className="typing-dot" style={{ animationDelay: "300ms" }} />
              </div>
              <span className="text-muted-foreground font-mono text-[10px]">investigating…</span>
            </div>
          )}
          <div ref={endRef} />
        </div>

        {/* input bar */}
        <div className="border-t border-foreground/[0.06] p-3.5">
          <form
            className="flex items-center gap-2 rounded-full border border-foreground/15 bg-background py-1.5 pr-1.5 pl-4 transition-[border-color] focus-within:border-foreground/40"
            onSubmit={(e) => {
              e.preventDefault();
              send(input);
            }}
          >
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={batch ? `Ask about ${batch.batch_name.replace("batch_", "").replace(".db", "")}…` : "No batch selected"}
              disabled={!batch || busy}
              className="min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground disabled:opacity-50"
            />
            <button
              type="submit"
              disabled={!batch || busy || !input.trim()}
              className="bg-foreground text-background flex size-8 shrink-0 items-center justify-center rounded-full transition-all hover:bg-foreground/90 disabled:cursor-not-allowed disabled:opacity-40 active:scale-95"
            >
              <Send className="size-3.5" />
            </button>
          </form>
        </div>
      </div>
  );
}
