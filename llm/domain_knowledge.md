# Domain Knowledge Base — AI Finance Controller

> Injected into LLM system prompts (explain + chat). Single source of truth.
> Facts ONLY; the agent cites records and never invents amounts/IDs/causes.

## Money lifecycle (this system)

```
customer places ORDER (ORD-…)  →  PAYMENT captured (PAY-…, method, processor_ref pay_RP…)
   →  SETTLEMENT created (SET-…): gross − fee − GST = net, carries UTR
   →  BANK CREDIT lands (BANK-…): narration embeds UTR and/or mutated order ref
REFUNDS/REVERSALS appear as adjustments (ADJ-…) and negative bank lines.
```

## Processor fee schedule (canonical, integer paise, HALF_UP)

| method | rate | fixed | GST on fee |
|---|---|---|---|
| upi | 0 bps (zero-MDR) | ₹0 | — |
| debit_card | 90 bps | ₹2 | 18% |
| credit_card | 200 bps | ₹2 | 18% |
| netbanking | 180 bps | ₹5 | 18% |
| wallet | 160 bps | ₹2 | 18% |

`net = gross − fee − tax`. A settlement whose fee fields equal this schedule is
**policy-consistent**. A gap between payment and bank credit equal to fee+GST is a
*normal processor flow*, not an exception.

## Timing windows

- Normal settlement: **T+1..T+3** days after capture.
- Late-but-acceptable: **T+4..T+7** → still reconciles (reason: TIMING_OK).
- Beyond T+7 with money missing: investigate as potential loss.

## Narration channels (Indian rails)

Payout narrations embed identifiers with noise:
`NEFT Cr-{UTR}-{IFSC}-RAZORPAY SOFTWARE-PAYOUT {ref}--`, `RTGS-…-/URGENT/`,
`IMPS-{12d}-…`, `ACH-C/D-{utr}-…`, `UPI/CR/{12d}/{payer}/{vpa}-{ref}`,
`By Clg:HDFC BANK-RAZORPAY, COLLECTION {ref}`.
Refs may be separator-stripped (`ORD1042`), case-mangled (`ord#1042`),
OCR-corrupted (`ORD-1O042` — letter O), truncated, or accompanied by decoy refs
(`ORD-9xxxx` are dead decoys). Every payout keeps ≥1 identifier (ref or UTR).
Ambient noise lines: `Service Charges-*`, `REVERSAL-*`.

## Exception taxonomy & what counts as proof

| verdict | meaning | proof required |
|---|---|---|
| matched | flows exact end-to-end | zero deltas |
| matched_after_reasoning | gap fully explained | check output + cited record IDs |
| genuine_discrepancy | something IS wrong and named | duplicate pair IDs / shortfall vs settlement / unmatched inflow |
| unresolved | gap exists, ALL checks ran, no evidence | exhaustive-search result + list of checks |
| data_quality | record unusable | null/negative amount, unknown method, corrupt glyphs |

## Abstention policy (non-negotiable)

1. Never invent fees, refunds, dates, or IDs.
2. Every claim cites record IDs visible in provided context.
3. If evidence is insufficient after all checks: say `unresolved`, enumerate what
   was checked, state exactly what extra document would resolve it.
4. Amounts come from records/tools only — never mental arithmetic beyond restating.

## Indian rails facts

- UPI consumer P2M: zero MDR (merchant pays nothing) → zero-fee settlements normal.
- RTGS minimum ₹2,00,000; NEFT/IMPS commonly below.
- UTR = bank reference per payout; IFSC = 11-char branch code; VPA = `name@bank`.
- GST 18% applies to processor fees, not to customer payment amounts.
