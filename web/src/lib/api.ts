export interface BatchInfo {
  batch_name: string;
  seed: string;
  has_verdicts: boolean;
  has_ground_truth: boolean;
  row_counts: Record<string, number>;
  generator_meta: { difficulty: string; generator_version: string; seed: number };
}

export interface HonestException {
  work_key: string;
  cls: string;
  reason_code: string;
}

export interface Metrics {
  batch_name: string;
  seed: string;
  transactions: number;
  class_mix: Record<string, number>;
  reconciled_rate: number;
  throughput_orders_per_sec: number;
  elapsed_ms: number;
  results_sha256: string;
  honest_exception_list: HonestException[];
  eval_backed?: Record<string, number> | null;
  error?: string;
}

export interface Txn {
  work_key: string;
  cls: string;
  reason_code: string;
  bundle_bid: string;
  order: { order_id: string; amount_paise: number; customer_name: string; status: string; created_at: string } | null;
}

export interface Verification {
  verified: boolean;
  fully_supported?: boolean;
  id_errors: string[];
  amount_errors: string[];
  unsupported_sentences: string[];
  citation_recall: number | null;
  citation_precision: number | null;
}

export interface Drawer {
  work_key: string;
  verdict: Record<string, unknown>;
  findings: Record<string, unknown>[];
  members: string[];
  records: Record<string, unknown>[];
  explanation: string;
  explanation_source: string;
  verification: Verification;
}

export interface ChatEvent {
  type: string;
  content?: string;
  name?: string;
  args?: Record<string, unknown>;
  citations?: string[];
  summary?: string;
  provider?: string;
  latency_ms?: number;
}

async function j<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return r.json();
}

export const api = {
  batches: () => j<{ batches: BatchInfo[] }>("/api/batches").then((d) => d.batches),
  runBatch: (b: string) =>
    fetch(`/api/batches/${b}/run`, { method: "POST" }).then((r) => r.json()),
  metrics: (b: string) => j<Metrics>(`/api/batches/${b}/metrics`),
  transactions: (b: string, cls: string | null = null, limit = 200) =>
    j<{ items: Txn[] }>(
      `/api/transactions?batch_name=${b}&limit=${limit}${cls ? `&cls=${cls}` : ""}`,
    ).then((d) => d.items),
  exceptions: (b: string) =>
    j<{ count: number; exceptions: Txn[] }>(`/api/exceptions?batch_name=${b}`),
  drawer: (workKey: string, b: string) =>
    j<Drawer>(`/api/exceptions/${workKey}/drawer?batch_name=${b}`),
};

export function streamChat(
  batch: string,
  message: string,
  session: string | null,
  history: { role: string; content: string }[],
  onEvent: (ev: ChatEvent) => void,
  onDone: () => void,
): AbortController {
  const ctrl = new AbortController();
  fetch(`/api/chat/${batch}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, session, history, format: "sse" }),
    signal: ctrl.signal,
  })
    .then(async (r) => {
      const reader = r.body!.getReader();
      const dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const parts = buf.split("\n\n");
        buf = parts.pop() ?? "";
        for (const p of parts) {
          const line = p.trim();
          if (!line.startsWith("data:")) continue;
          const ev = JSON.parse(line.slice(5).trim()) as ChatEvent;
          if (ev.type === "done") {
            onDone();
            return;
          }
          onEvent(ev);
        }
      }
      onDone();
    })
    .catch(() => onDone());
  return ctrl;
}

export function rupees(paise: number | null | undefined): string {
  if (paise == null) return "—";
  return new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 2 }).format(paise / 100);
}
