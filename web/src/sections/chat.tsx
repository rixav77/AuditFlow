import { useEffect, useRef, useState } from "react";
import { streamChat, type ChatEvent, type BatchInfo } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Send, Wrench, Brain, Sparkles } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

const SUGGESTIONS = [
  "How many transactions didn't reconcile cleanly? Cite them.",
  "Any lessons from previous runs about unresolved cases?",
  "What is the class mix of this batch?",
];

function TypewriterText({ text, endRef }: { text: string; endRef: React.RefObject<HTMLDivElement | null> }) {
  const [displayed, setDisplayed] = useState("");

  useEffect(() => {
    let i = 0;
    const t = setInterval(() => {
      setDisplayed(text.slice(0, i));
      i += 3;
      
      if (endRef && endRef.current) {
        const container = endRef.current.parentElement;
        if (container) {
          const isNearBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 250;
          if (isNearBottom) {
             container.scrollTo({ top: container.scrollHeight, behavior: "smooth" });
          }
        }
      }

      if (i > text.length + 3) {
        setDisplayed(text);
        clearInterval(t);
      }
    }, 15);
    return () => clearInterval(t);
  }, [text, endRef]);

  return <ReactMarkdown remarkPlugins={[remarkGfm]}>{displayed}</ReactMarkdown>;
}

export function Chat({ batch }: { batch: BatchInfo | null }) {
  const [events, setEvents] = useState<ChatEvent[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const ctrlRef = useRef<AbortController | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const historyRef = useRef<{ role: string; content: string }[]>([]);

  useEffect(() => {
    const container = endRef.current?.parentElement;
    if (container) {
      container.scrollTo({ top: container.scrollHeight, behavior: "smooth" });
    }
  }, [events, busy]);

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
  }

  return (
    <div className="flex h-[75vh] flex-col overflow-hidden rounded-[2rem] border border-foreground/5 bg-card shadow-xl ring-1 ring-foreground/5">
      {/* header */}
      <div className="flex items-center justify-between border-b border-foreground/[0.04] bg-muted/30 px-6 py-4">
        <div className="flex items-center gap-3">
          <div className="bg-gradient-to-br from-foreground to-foreground/80 flex size-10 items-center justify-center rounded-full text-background shadow-md">
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
            <div className="text-muted-foreground mx-auto flex h-full max-w-md flex-col items-center justify-center space-y-3 pb-20 text-center text-sm">
              <div className="bg-muted/50 mb-2 flex size-12 items-center justify-center rounded-full">
                <Sparkles className="size-5 text-foreground/40" />
              </div>
              <p className="leading-relaxed">
                Ask about any transaction in{" "}
                <span className="text-foreground/80 font-mono">
                  {batch?.batch_name.replace("batch_", "").replace(".db", "") ?? "the batch"}
                </span>{" "}
                — the agent reads the records with its tools before it answers.
              </p>
            </div>
          )}
          {events.map((ev, i) => {
            if (ev.type === "user")
              return (
                <div key={i} className="flex justify-end">
                  <div className="bg-foreground text-background max-w-[75%] rounded-3xl rounded-br-sm px-5 py-3.5 text-[15px] leading-relaxed shadow-md">
                    {ev.content}
                  </div>
                </div>
              );
            if (ev.type === "tool_call")
              return (
                <div key={i} className="flex items-center gap-3 pl-2 text-xs opacity-75 transition-opacity hover:opacity-100">
                  <span className="bg-muted/70 flex size-6 shrink-0 items-center justify-center rounded-full">
                    <Wrench className="text-muted-foreground size-3" />
                  </span>
                  <Badge variant="secondary" className="font-mono bg-background/50 border-foreground/10">
                    {ev.name}
                  </Badge>
                  <span className="text-muted-foreground truncate font-mono text-[11px]">{JSON.stringify(ev.args)}</span>
                </div>
              );
            if (ev.type === "tool_result")
              return (
                <div key={i} className="flex items-center gap-3 pl-11 text-xs opacity-75 transition-opacity hover:opacity-100">
                  <span className="text-muted-foreground font-mono text-[11px]">→ {ev.summary}</span>
                  {ev.citations && ev.citations.length > 0 && (
                    <Badge variant="outline" className="font-mono text-[10px] text-ok border-ok/20 bg-ok/5">
                      {ev.citations.length} IDs verified
                    </Badge>
                  )}
                </div>
              );
            if (ev.type === "answer")
              return (
                <div key={i} className="flex justify-start">
                  <div className="bg-muted/40 max-w-[82%] rounded-3xl rounded-bl-sm border border-foreground/[0.04] px-5 py-4 text-[15px] leading-relaxed shadow-sm">
                    <div className="prose prose-sm prose-neutral dark:prose-invert max-w-none prose-p:leading-relaxed prose-pre:bg-background/50 prose-pre:border prose-pre:border-foreground/10 prose-th:font-semibold prose-td:border-t prose-td:border-border prose-table:w-full prose-table:my-4 prose-a:text-foreground">
                      {i === events.length - 1 ? <TypewriterText text={ev.content || ""} endRef={endRef} /> : <ReactMarkdown remarkPlugins={[remarkGfm]}>{ev.content || ""}</ReactMarkdown>}
                    </div>
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
        <div className="p-4 bg-gradient-to-t from-card via-card to-transparent pt-8 flex flex-col">
          {events.length === 0 && (
            <div className="mb-3 flex flex-wrap gap-2 px-1">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  onClick={() => send(s)}
                  className="rounded-full border border-foreground/10 bg-background/80 backdrop-blur-md px-4 py-2 text-xs text-foreground/80 shadow-sm transition-all hover:border-foreground/30 hover:bg-background hover:shadow hover:-translate-y-0.5"
                >
                  {s}
                </button>
              ))}
            </div>
          )}
          <form
            className="flex items-center gap-3 rounded-[2rem] border border-foreground/15 bg-background/60 backdrop-blur-xl p-2 pl-6 shadow-sm transition-all hover:border-foreground/25 focus-within:border-foreground/40 focus-within:bg-background focus-within:shadow-md"
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
              className="bg-foreground text-background flex size-10 shrink-0 items-center justify-center rounded-full transition-all hover:bg-foreground/90 hover:shadow-md disabled:cursor-not-allowed disabled:opacity-40 active:scale-95"
            >
              <Send className="size-3.5" />
            </button>
          </form>
        </div>
      </div>
  );
}
