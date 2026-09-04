
<p align="center">
  <img src="assets/auditflow_final_architecture.png" width="100%" />
</p>

<h1 align="center">AuditFlow</h1>
<p align="center"><strong>AI Finance Controller — Multi-Source Financial Reconciliation Agent</strong></p>

<p align="center">
  <em>Razorpay Buildathon · Track 04</em><br>
  <a href="https://github.com/rixav77/AuditFlow">https://github.com/rixav77/AuditFlow</a>
</p>

---

AuditFlow ingests financial records from four independent sources — orders, payments, settlements, and bank statements — links records belonging to the same underlying transaction through a 5-pass graph solver, checks whether the financial story reconciles down to the last paisa, investigates exceptions with deterministic evidence, resolves what the evidence supports, and **explicitly abstains** when evidence is insufficient. Every amount is integer paise. Every explanation cites record IDs. Every metric is honest.

## Why This Exists

Financial reconciliation at scale is a graph problem disguised as a spreadsheet problem. Orders flow into payments, payments settle through processors who deduct fees and taxes, and the net amount eventually appears as a bank credit — often days later, sometimes split across multiple legs, occasionally combined with other transactions into a single payout. The bank's only record of what happened is a narration string like `NEFT Cr-HDFC0612345-RAZORPAY SOFTWARE-PAYOUT ORD1042--`.

Commercial platforms (BlackLine, HighRadius, Trintech) solve this with configurable rules engines, but they treat matching as a classification problem. AuditFlow treats it as what it actually is: **entity resolution across heterogeneous sources with causal exception analysis**.

The key differentiator: when something doesn't reconcile, the system doesn't guess. It runs every applicable investigation check, and if none of them can explain the discrepancy with evidence, it returns `unresolved` — not a hallucinated cause. This is built on research showing that even frontier LLMs achieve only 59.5% paired accuracy on abstention tasks (Liu et al., AgentAbstain). Abstention must be engineered as an explicit gate, not left to model confidence.

---

## Table of Contents

- [Architecture](#architecture)
- [The Reconciliation Engine](#the-reconciliation-engine)
- [Synthetic Data Generator](#synthetic-data-generator)
- [Citation Verification Layer](#citation-verification-layer)
- [Context-Grounded Memory](#context-grounded-memory)
- [Evaluation & Benchmarks](#evaluation--benchmarks)
- [LLM Integration & Copilot Agent](#llm-integration--copilot-agent)
- [Dashboard](#dashboard)
- [Quick Start](#quick-start)
- [Repository Layout](#repository-layout)
- [Design Principles](#design-principles)
- [Research Foundations](#research-foundations)
- [Tech Stack](#tech-stack)

---

## Architecture

AuditFlow is a multi-layered autonomous financial controller: synthetic multi-source data generation → deterministic 5-pass reconciliation engine → LLM-powered explanation with mechanical citation verification → long-term grounded memory with 3-score retrieval → interactive finance dashboard.

The entire pipeline is designed around one principle: **facts come from structured records and deterministic tools — the LLM never invents amounts, IDs, fees, dates, or evidence.**

<p align="center">
  <img src="assets/auditflow_linkage_engine.png" width="100%" />
</p>

---

## The Reconciliation Engine

The engine is fully deterministic — no LLM in the critical path. Given a batch SQLite database, it executes four stages in sequence: **Link → Reconcile → Investigate → Classify**.

### 5-Pass Graph Linkage

Record linkage follows the Christophides et al. end-to-end ER workflow (profiling → indexing → matching → clustering), adapted for financial multi-source resolution. The design draws from Papadakis et al.'s blocking/filtering taxonomy and uses a hand-calibrated Fellegi–Sunter scoring model with domain-specific features.

| Pass | Strategy | What It Links |
|------|----------|---------------|
| **P0** | Reference normalization | Cleans IDs from bank narrations — strips prefixes, handles homoglyph corruption (`0→O`, `1→I`, `5→S`), expands truncations |
| **P1** | Explicit foreign keys | `payments.order_id → orders`, `settlements.payment_id → payments` — direct database joins |
| **P2** | Narration-extracted references | Regex + normalized matching of `ORD-*`, `PAY-*`, UTR tokens embedded in bank narration text |
| **P3** | Scored window candidates | Fellegi–Sunter-style scoring on amount±fee-tolerance × date-window blocks with UTR equality boost. Features: exact-amount match, net-proximity-to-fee-model, date-gap, reference hit. Length-aware thresholds per Kloo et al. |
| **P4** | Combine-merge | Subset-sum DP for combined settlements (N payments → 1 bank payout). Mandatory-member seeding from narration-named orders. UTR-evidence preference. Union-find with explicit `uf.union()` calls at every merge |

Each link stores provenance: `{pass, evidence_ids, score_breakdown}`. Bundles are materialized as graphs with member roles (order / payment / settlement / bank / adjustment).

**P3b — LLM Adjudication Touchpoint:** When P3 produces exact score ties between candidates, the engine can optionally invoke an LLM tie-breaker (T1 touchpoint). The LLM sees only the top-k candidates with scores and raw record JSON, and returns `{choice|ambiguous, rationale, cited_ids}`. If the LLM is unavailable, the deterministic fallback selects the highest-scoring candidate marked `low_confidence`. Engine behavior is byte-identical with and without T1 — verified by SHA-256 result hashes.

### Paise-Level Reconciliation

After linkage, every bundle's financial edges are checked at integer paise precision:
- Order amount vs. payment amount
- Gross payment vs. settlement gross
- Settlement net vs. bank credit (after fee + GST deduction)
- Adjustment flows vs. refund/reversal legs

Any non-zero delta produces a `Finding` with `{kind, expected_paise, actual_paise, delta_paise, member_ids}`.

### 7-Check Investigation Gate

Investigation is **gated** — it runs only when there's a meaningful break (non-zero delta) or structural markers (split settlements, combined payouts, adjustments, late settlement lags >3 days). This is the anti-post-hoc gate inspired by Liu et al.'s AgentAbstain: the system must complete evidence checks *before* it can classify, never after.

| Check | What It Tests |
|-------|--------------|
| **Fee Schedule Match** | Does `fee = half_up(gross × bps/10000) + fixed` and `tax = half_up(fee × 18%)` explain the delta exactly? Per-method schedule: UPI 0bps/₹0, debit_card 90bps/₹2, credit_card 200bps/₹2, netbanking 180bps/₹5, wallet 160bps/₹2 |
| **Refund/Adjustment Lookup** | Do adjustment records (partial/full refund, reversal) account for the shortfall? |
| **Split/Combine Test** | Do multiple settlement legs sum to the expected net? Does a subset-sum of member nets equal the combined bank payout? |
| **Duplicate Scan** | Are there multiple bank credits with the same UTR and amount (within tolerance)? |
| **Timing Window** | Is the settlement lag within the source policy's `late_max_days` window? |
| **Unmatched Inflow** | Is there a bank credit with no upstream chain (order → payment → settlement)? |
| **Data Quality Triage** | Is the record malformed — null amount, negative amount, mojibake narration, unknown payment method? |

Each check returns a `CheckResult{check, supported, evidence_ids, note}`. **Unresolved requires all applicable checks ran AND none supported** — the system cannot finalize a non-matched verdict unless it has exhausted the investigation checklist.

### Verdict Classification

A deterministic priority ladder maps investigation results to final verdicts:

| Verdict Class | When Assigned |
|--------------|---------------|
| `matched` | Zero delta across all edges; standard processor fees with settlement present |
| `matched_after_reasoning` | Delta explained by fee schedule, split/combine math, refund adjustment, or late settlement timing |
| `genuine_discrepancy` | Evidence found: duplicate bank credit, unmatched inflow, short-settled with no adjustment |
| `unresolved` | Meaningful delta exists, all applicable checks ran, none explains it |
| `data_quality` | Malformed source record (null/negative amount, unknown method, mojibake) |
| `ignored_noise` | Failed payments, cancelled orders, ambient service charge debits — correctly excluded |

Every verdict carries: `{class, reason_code, evidence_ids[], findings[], checks_run[], llm_assists[]}`.

### Source-Adaptive Policy

The engine doesn't hardcode rules for one processor. Fee schedules, settlement windows, materiality thresholds, and aggregation style travel with the batch as `_policy` metadata:

```json
{
  "fee_schedule": {"credit_card": [200, 200, 1800]},
  "settle_min_days": 1, "settle_max_days": 3,
  "late_max_days": 7, "short_pct_threshold": 0.20,
  "aggregation": "per_payment"
}
```

This is what makes the engine work on foreign data (ReconRiver: flat 2.90% + ₹0.30, no GST) without code changes — the rulebook adapts per source.

---

## Synthetic Data Generator

The generator produces reproducible multi-source financial datasets with machine-checkable ground truth. Every batch is seeded — same seed, same bytes, deterministically.

### Data Model

One SQLite database per batch (`batch_seed<N>.db`) containing six tables: `orders`, `payments`, `settlements`, `bank_txns`, `adjustments`, `audit_events`. Money is integer paise everywhere. IDs use sparse jittered schemes: `ORD-1000xx`, `PAY-5000xx`, `SET-8000xx`, `BANK-30000xx`.

### 14 Exception Causes + Noise Layer

| Category | Causes | Expected Verdict |
|----------|--------|-----------------|
| Clean | `CLEAN_MATCH` — full chain, exact amounts | `matched` |
| Reasoning Required | `FEE_EXPLAINED` · `LATE_SETTLEMENT` · `SPLIT_SETTLEMENT` · `COMBINED_SETTLEMENT` · `REFUND_PARTIAL` · `REFUND_FULL` · `AMBIGUOUS_CANDIDATES` | `matched_after_reasoning` |
| Genuine Issues | `DUPLICATE_BANK_CREDIT` · `SHORT_SETTLED` · `BANK_ONLY_CREDIT` | `genuine_discrepancy` |
| Unresolvable | `MISSING_SETTLEMENT` · `UNEXPLAINED_DELTA` | `unresolved` |
| Data Quality | `MALFORMED_SOURCE_ROW` (null amount / negative / mojibake) | `data_quality` |
| Ambient Noise | `FAILED_PAYMENT` ×4 · `CANCELLED_ORDER` ×2 · `SVC_DEBIT` ×3 · `REVERSAL_DEBIT` ×1 | correctly ignored |

### Bank Narration Composer

Bank narrations are generated from templates mined from the AgamiAI Indian Bank Statements corpus (64,698 transactions across 400 statements). Channel-specific formats: NEFT, RTGS (≥₹2L per RBI rules), IMPS, UPI, ACH, Clearing. Narrations embed transaction references with realistic corruption:

- Format mutations: `ORD-1042` → `ORD1042` / `ord#1042` / `Ord 1042`
- Homoglyph corruption: `0→O`, `1→I`, `5→S`, `8→B` (never forms a valid ID)
- Decoy references from dead range (`ORD-90000..99999`)
- Junk suffixes (`/IBL`, `TXN`), column-width truncation

### Difficulty Presets

| Knob | EASY | NORMAL | HARD |
|------|------|--------|------|
| Reference absent probability | 0.02 | 0.08 | 0.18 |
| Typo probability | 0.03 | 0.08 | 0.15 |
| Decoy reference probability | 0.05 | 0.12 | 0.22 |
| Unexplained delta band | ₹20–200 | ₹10–5,000 | ₹10–9,999 |
| Noise multiplier | ×0.5 | ×1 | ×2 |

### 12 Validation Gates

The generator self-tests every batch: referential integrity of ground truth links, per-cause delta recomputation, fee schedule exactness, exhaustive proof that unresolved cases have no explaining records, zero-delta clean chain verification, timestamp ordering, determinism hash check, noise isolation, RTGS amount threshold, narration reference validity, ID sparsity, and combined-group sum correctness.

### Anti-Leakage Rule

No database column or table encodes ground truth — no cause codes, no `is_distractor` flags. Distractors differ only through legitimate evidence (`status=failed`, `status=cancelled`). Truth lives exclusively in the external `ground_truth.json`.

---

## Citation Verification Layer

<p align="center">
  <img src="assets/auditflow_citation_cage.png" width="100%" />
</p>

Every LLM-generated explanation is mechanically verified against the exact structured payload it was built from. Financial AI agents must never commit unit errors (e.g., confusing ₹3,969.00 with ₹3,96,900) or hallucinate non-existent record identifiers.

### 4-Layer Verification

| Layer | Type | What It Checks | On Failure |
|-------|------|---------------|------------|
| **A** | Hard | Every cited `ORD/PAY/SET/BANK/ADJ` reference exists in payload records | RARR repair pass → deterministic fallback |
| **B** | Hard | Every ₹/paise figure equals a payload amount (both unit forms, Indian-style grouping) | RARR repair pass → deterministic fallback |
| **C** | Soft | Per-sentence lexical support — valid citation or ≥2 payload content tokens | Flagged in report, never silently dropped |
| **D** | Stub | Semantic NLI judge (non-breaking stub by design — deterministic exact checks dominate with structured payloads) | — |

**Acceptance rule:** Hard-clean after ≤2 provider calls. Soft flags travel in the report — they're never hidden.

### Metrics

Adapted from ALCE (arXiv 2305.14627) citation evaluation to a structured payload instead of a retrieval corpus:
- **Citation Recall** = supported sentences / total sentences
- **Citation Precision** = valid citations / total citations

Grounded in: ALCE for recall/precision definitions, AIS (arXiv 2112.12870) for "attributable to identified source", RARR (arXiv 2210.08726) for minimal-revision repair, FActScore (arXiv 2305.14251) for sentence-level decomposition.

Every verification report is traced to `data/traces/explain_<date>.jsonl` and surfaced in the application's evidence drawer plus aggregate citation audits.

---

## Context-Grounded Memory

<p align="center">
  <img src="assets/auditflow_context_memory_architecture.png" width="100%" />
</p>

AuditFlow includes a mem0-style long-term memory system that learns from reconciliation sessions — but with a critical constraint: **financial-fact memories that don't cite verifiable record IDs are dropped, not stored.** This is the grounding gate.

### Memory Store

SQLite + FTS5 full-text search. Each memory record contains:
- Content text with embedded source references
- Entity links (which orders, payments, settlements the memory relates to)
- Confidence score and creation context
- Operation log tracking every insert/update/delete

### Grounded Ingestion

When the system encounters a pattern worth remembering (e.g., "credit_card settlements from this processor typically lag 4 days"), it must back the claim with specific record IDs from the current batch. If it can't cite verifiable sources, the memory is **DROPPED** — never persisted. Standing `grounded_memory_rate`: **1.0**.

### 3-Score Retrieval

Memory retrieval ranks candidates by three signals:
1. **Semantic similarity** — FTS5 text match against the query
2. **Entity overlap** — shared record IDs between query context and memory
3. **Recency** — newer memories weighted with 14-day exponential half-life decay

Retrieved memories are injected into chat context with smart truncation (Arize-style head/tail preservation) to respect the context window budget.

### Self-Improvement Loop

`scripts/auto_improve.py` implements an AutoAgent-fenced improvement cycle: the agent proposes edits to prompt files → runs the full benchmark → keeps only if engine SHA-256 is unchanged, no outcome metric regressed, and citation probes pass. Two critical fences:

1. **Allowlist:** The agent can only edit two markdown files (`llm/prompts/chat_system.md`, `memory/skills.md`) — engine code, tools, and verifier are structurally unreachable
2. **Deterministic Gate:** Self-improvement operations are bound by deterministic test suites and strict evaluation gates before prompt updates can be accepted

---

## Evaluation & Benchmarks

<p align="center">
  <img src="assets/auditflow_eval_benchmark.png" width="100%" />
</p>

### Financial Accuracy & Matching

Tested across 5 internal seeds (3 dev + 2 fresh, never-seen-during-development) and 1 external benchmark:

| Batch | Difficulty | Match Rate | Exception P/R | Abstention P/R | False Match |
|-------|-----------|------------|---------------|---------------|-------------|
| seed7 | NORMAL | 1.0 | 1.0 / 1.0 | 1.0 | 0.0 |
| seed42 | NORMAL | 1.0 | 1.0 / 1.0 | 1.0 | 0.0 |
| seed1337 | NORMAL | 1.0 | 1.0 / 1.0 | 1.0 | 0.0 |
| seed9001 *(fresh)* | NORMAL | **0.9672** | 1.0 / 1.0 | 1.0 | 0.0 |
| seed9002 *(fresh)* | HARD | 1.0 | 1.0 / 1.0 | **0.875** | 0.0 |
| ReconRiver *(external)* | — | 84.3% (86/102) | — | — | — |

**Known gaps (documented, not tuned away):**
- seed9001: 2 GT-matched orders classified `ignored_noise` (noise-overlap edge case)
- seed9002: 1 GT-unresolved case returned `genuine_discrepancy` (unsupported reasoning — the exact failure mode the abstention gate exists for)

These misses are named in the standing audit and the eval report. They are not hidden or optimized away.

**ReconRiver note:** The 84.3% is a crude binary accuracy (did our label match theirs, yes/no). We cannot measure abstention recall, evidence P/R, or other rich metrics on external data because ReconRiver lacks the multi-dimensional ground truth our harness requires. The score reflects the engine's refusal to guess on contradictory external data.

### Agent Safety & Tool Audit

- Chat loop bounded to **≤6 turns** — budget exhausted → explicit "no supported answer"
- Explanation loop: generate → verify → one repair pass → accept or deterministic fallback. **Never more than 2 provider calls**
- 100% citation state validity — every cited ID in chat responses exists in tool-verified results
- All 12 agent tools are **read-only** — no writes to the batch database

### Dirty Data Stress-Testing

Perturbation suite mutates bank narrations (character swaps, heavy noise, truncation) and measures verdict stability:
- **99.8%+ stability** under heavy narration noise and character mutations
- Engine throughput: ~4,000 orders/second on synthetic batches

### Grounded Long-Term Memory

- **Grounded retention rate:** 1.0 — every stored memory cites verifiable record IDs
- **Retrieval Hit@1:** 1.0 — correct memory retrieved as top result across test queries

### Metric Definitions

All metrics have locked definitions:
- **Match rate** = correctly matched / GT-matched
- **Exception P/R** = per cause code, pred exception vs GT exception
- **Correct-abstention rate** = unresolved-designed ∧ pred=unresolved / total unresolved-designed
- **False-match rate** = pred∈matched ∧ GT∈{unresolved, genuine_discrepancy} / total such cases
- **Fabricated-cause count** = explanations citing records outside expected links on unresolved cases

---

## LLM Integration & Copilot Agent

### Provider Architecture

AuditFlow supports multi-provider failover across standard foundation models (OpenAI, OpenRouter, Gemini, Anthropic) via a unified client abstraction. A strict token ceiling (default 700 tokens) prevents runaway costs and ensures synthesized explanations remain concise and verifiable.

### 12 Read-Only Agent Tools

The chat agent operates within a bounded tool loop with 12 read-only tools:

| Tool | Purpose |
|------|---------|
| `get_verdict(work_key)` | Retrieve verdict + findings + member IDs for a transaction |
| `get_records(work_key)` | Raw source rows of the bundle |
| `list_transactions(cls?, limit)` | Verdict listing, filterable by class |
| `get_unresolved()` | All unresolved cases (capped at 50) |
| `check_fee_schedule(method, gross)` | Compute fee/tax/net for a payment method |
| `search_narrations(pattern, limit)` | Search bank narration text |
| `get_settlement_chain(payment_id)` | Settlement legs for a payment |
| `list_adjustments(payment_id)` | Refunds/reversals for a payment |
| `find_candidate_matches(record_id)` | Alternative linkage candidates (capped at 5) |
| `get_batch_summary()` | Class mix overview |
| `query_table(table, where, limit)` | Guarded SQL query (table allowlist, limit ≤50) |
| `search_memory(query, top_k)` | Long-term memory search (grounded refs only) |

All tools read the batch SQLite only. Every call is logged (name, args, ok, result hash) to `data/traces/chat_<date>.jsonl`.

### Domain Knowledge

A curated domain knowledge document (`llm/domain_knowledge.md`) is injected into every LLM prompt. It encodes payment processing semantics — fee structures, settlement flows, common exception patterns — without leaking implementation details.

### Explanation Synthesis

For each transaction verdict, the LLM generates a natural-language explanation citing specific record IDs and amounts. The explanation passes through the 4-layer citation cage before reaching a human. If verification fails twice, the system falls back to a deterministic template-based explanation. No hallucinated narrative ever reaches the user.

---

## Dashboard

React 19 + Vite + Tailwind v4 with an axiom design language (oklch warm-neutral tokens, Instrument Sans/Serif + JetBrains Mono, shadcn-style vendored components).

### Four Tabs

| Tab | Features |
|-----|----------|
| **Overview** | Animated metric cards, classification-mix bars, honest exception list with delta amounts |
| **Transactions** | Filter/search by verdict class, amount range, date. Evidence drawer with citation-verification badge showing hard/soft check results |
| **Chat** | Interactive copilot panel rendering tool calls in real time. Injected long-term memory context. Bounded to 6 turns with explicit budget indicator |
| **Eval** | Standing evaluation report including memory metrics and named failed cases — nothing hidden |

---

## Quick Start

### Prerequisites

- Python 3.12+
- [uv](https://astral.sh/uv) package manager
- Node.js 18+ (for dashboard)

### Setup

```bash
# Clone repository
git clone https://github.com/rixav77/AuditFlow.git
cd AuditFlow

# Install Python dependencies
uv sync
```

### Generate a Batch & Run the Engine

```bash
# Generate a 60-transaction batch (seed 42, NORMAL difficulty)
uv run python -m generator.cli --seed 42 --size 60 --difficulty NORMAL

# Run the deterministic reconciliation engine
uv run python -m engine.runner \
  --db data/synthetic/batch_seed42.db \
  --json-out /tmp/results.json
```

### Evaluate Against Ground Truth

```bash
# Full evaluation with honest metrics
uv run python -m eval.run \
  --db data/synthetic/batch_seed42.db \
  --gt data/synthetic/ground_truth_seed42.json \
  --results /tmp/results.json

# Generate standing multi-layer evaluation report
uv run python -m eval.report
```

### Launch the Dashboard

```bash
cd web
npm install
npm run dev
# → http://localhost:5173
```

### Run Tests

```bash
uv run pytest                    # 102+ unit & integration tests
uv run ruff check .              # linting and formatting
```

### Additional Commands

```bash
# Ablation: compare engine with/without LLM adjudication
uv run python -m eval.ablation \
  --db data/synthetic/batch_seed42.db \
  --gt data/synthetic/ground_truth_seed42.json

# External benchmark (ReconRiver)
uv run python -m scripts.benchmark_reconriver

# Self-improvement loop (fenced, eval-gated)
uv run python -m scripts.auto_improve \
  --db data/synthetic/batch_seed42.db \
  --gt data/synthetic/ground_truth_seed42.json \
  --baseline-only
```

---

## Repository Layout

```
generator/          Synthetic multi-source data generation + ground truth labels
  ├── cli.py          CLI entry point (--seed, --size, --difficulty)
  ├── config.py       Fee schedules, difficulty presets, ID ranges
  ├── entities.py     Order/payment/settlement/bank entity builders
  ├── generate.py     Batch orchestrator with cause-code composition
  ├── inject.py       14-cause discrepancy injector + noise layer
  ├── narrate.py      Bank narration composer (AgamiAI-derived templates)
  ├── schema.py       SQLite schema definitions
  └── validate.py     12 self-test validation gates

engine/             Deterministic reconciliation engine
  ├── linkage.py      5-pass graph linkage solver (630 lines)
  ├── reconcile.py    Paise-level edge reconciliation
  ├── investigate.py  7-check evidence investigation gate
  ├── classify.py     Verdict classification priority ladder
  ├── runner.py       Batch runner (load → link → reconcile → investigate → classify → persist)
  ├── policy.py       Source-adaptive fee/settlement policy resolution
  ├── types.py        Core types (Bundle, Link, Finding, CheckResult, Verdict)
  ├── assist.py       LLM touchpoint integration (T1 adjudication)
  ├── explain.py      Deterministic explanation templates
  ├── fees_ext.py     Fee computation with merged schedules
  └── adapters/       Foreign data adapters (ReconRiver, etc.)

llm/                LLM provider abstraction & verification
  ├── provider.py     Multi-provider fallback client abstraction
  ├── citations.py    4-layer citation verifier (ALCE/RARR-grounded)
  ├── chat_agent.py   Bounded tool-loop chat agent (≤6 turns)
  ├── tools.py        12 read-only agent tools with dispatch + tracing
  ├── explain.py      Explanation synthesis with citation verification
  ├── traces.py       JSONL trace exporter
  └── domain_knowledge.md  Injected domain KB (fee structures, settlement patterns)

memory/             Grounded long-term memory system
  ├── store.py        SQLite + FTS5 memory store with entity links and op log
  ├── ingest.py       Grounded ingestion gate (unverifiable facts → DROPPED)
  ├── retrieve.py     3-score retrieval (semantic + entity overlap + recency)
  └── improve.py      AutoAgent-fenced self-improvement with eval gate

eval/               Evaluation harness
  ├── run.py          Evaluation runner against ground truth
  ├── outcome.py      Outcome metrics (match rate, P/R, abstention, false-match)
  ├── trajectory.py   Trajectory audit (checks per case, citation state validity)
  ├── robustness.py   Perturbation suite (narration mutation stability)
  ├── memory_eval.py  Memory layer metrics (grounded rate, retrieval hit@1)
  ├── report.py       Standing report generator
  └── ablation.py     ASSIST_MODE null|live comparison

web/                React dashboard
  └── src/
      ├── App.tsx         Tab navigation + batch selector
      ├── sections/
      │   ├── overview.tsx    Metric cards + classification bars + exception list
      │   ├── transactions.tsx  Filterable ledger + evidence drawer
      │   ├── chat.tsx        SSE chat panel with tool call rendering
      │   └── eval.tsx        Standing eval report with named failures
      └── components/       Shared UI components (metric cards, badges, drawers)

scripts/            Utility & benchmark scripts
  ├── verify_providers.py      Provider diagnostic check
  ├── benchmark_reconriver.py  External benchmark runner + divergence taxonomy
  ├── auto_improve.py          Eval-gated self-improvement loop
  ├── seed_memory.py           Seed memory store from eval rows
  ├── filter_reconriver.py     ReconRiver data pipeline flattener
  ├── diversity_report.py      Batch diversity analysis
  └── profile_external_data.py External dataset profiler

tests/              Test suite (102+ tests)
data/               Generated batches (gitignored), seed databases, traces
assets/             Architecture diagrams and benchmarks
```

---

## Design Principles

### 1. Facts From Records, Not Models
The LLM never invents amounts, IDs, fees, dates, or evidence. All money math is integer paise. The engine is fully deterministic — the LLM is consulted only for natural-language explanation and tie-breaking, both of which are mechanically verified.

### 2. Abstention Is First-Class
`unresolved` is a schema-level output state, not a side effect. It requires: a meaningful delta exists AND every applicable investigation check ran AND none provided supporting evidence. The system will never fabricate a cause to fill an explanation gap.

### 3. Evidence-First
Every classification and explanation cites specific record IDs. Citations are mechanically verified — not by asking another LLM, but by exact string and amount matching against the structured payload.

### 4. Reproducibility
Seeded generation means any case can be replayed from its records and tool results. Determinism hashes verify that the same seed produces identical bytes.

### 5. Honest Metrics
Match rate, exception precision/recall per cause type, correct-abstention rate, false-match rate — all have documented definitions. Fresh-seed misses are named, not tuned away. The eval report includes failed cases with root-cause analysis.

---

## Research Foundations

AuditFlow's design is grounded in peer-reviewed research:

### Record Linkage & Entity Resolution
- **Christophides et al. (2019, arXiv:1905.06397)** — End-to-end ER workflow (profiling → indexing → matching → clustering → evaluation) that structures our pipeline stages.
- **Papadakis et al. (2019, arXiv:1905.06167)** — Blocking & filtering taxonomy informing our amount-bucket × date-window candidate generation.
- **Fellegi & Sunter (1969)** — Probabilistic record linkage model adapted as our P3 scorer with hand-calibrated, documented weights.
- **Kloo et al. (WSC 2019)** — Dynamic Jaro-Winkler thresholds informing length-aware reference matching; never fuzzy-match short numeric tokens.

### Abstention & Agent Safety
- **Liu et al., AgentAbstain (arXiv:2607.10059)** — Demonstrated only 59.5% paired accuracy for frontier agents on abstention; validates engineering explicit gates rather than trusting model confidence.
- **Luo et al., Agentic Abstention (arXiv:2606.28733)** — Timely abstention recall metric and trajectory-distilled stopping rules informing our investigation budget.
- **Selective Prediction Cluster** — Evidence-gated abstention > confidence-gated; explicit coverage vs. correctness tradeoff.

### Citation & Mechanical Verification
- **ALCE (arXiv:2305.14627)** — Citation recall/precision definitions adapted to structured payloads.
- **AIS (arXiv:2112.12870)** — "Attributable to identified source" standard.
- **RARR (arXiv:2210.08726)** — Minimal-revision repair for citation failures.
- **FActScore (arXiv:2305.14251)** — Sentence-level fact decomposition.

### Platform Patterns
- **HighRadius, BlackLine, Trintech** — Commercial reconciliation workflows (normalize → match → reconcile → investigate → document) that validate our pipeline design.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Runtime | Python 3.12+ |
| Package Manager | uv |
| Data Validation | Pydantic ≥2.7 |
| LLM Client | OpenAI SDK ≥1.50 (compatible with OpenAI, OpenRouter, Gemini, Anthropic) |
| HTTP Client | httpx ≥0.27 |
| Database | SQLite (one per batch) + FTS5 Full-Text Search |
| Data Processing | Pandas ≥2.2 + PyArrow ≥17 |
| Frontend | React 19 + Vite + Tailwind v4 |
| Design System | oklch warm-neutral tokens, Instrument Sans/Serif, JetBrains Mono |
| Testing | pytest ≥8.0 (102+ tests) |
| Linting | ruff ≥0.6 |

---

## License

Built for the Razorpay Buildathon, Track 04.

---

<p align="center"><em>Built by Rishav — because reconciliation deserves better than VLOOKUP.</em></p>
