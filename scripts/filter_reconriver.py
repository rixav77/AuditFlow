import csv
import re
from collections import defaultdict
from pathlib import Path

SRC = Path("data/raw/reconriver/generated/mixed-exceptions")
DST = Path("data/raw/reconriver/generated/benchmark-standard")
DST.mkdir(parents=True, exist_ok=True)

batch_to_orders = defaultdict(set)
order_to_batches = defaultdict(set)

with open(SRC / "processor_transactions.csv", "r", encoding="utf-8") as f:
    for row in csv.DictReader(f):
        bid = row.get("settlement_batch_id")
        oid = row.get("merchant_order_id")
        if bid and oid:
            batch_to_orders[bid].add(oid)
            order_to_batches[oid].add(bid)

def get_component(start_oid):
    oids = set()
    bids = set()
    q = [start_oid]
    while q:
        curr = q.pop()
        if curr in oids: continue
        oids.add(curr)
        for b in order_to_batches[curr]:
            bids.add(b)
            for o in batch_to_orders[b]:
                if o not in oids:
                    q.append(o)
    return frozenset(oids), frozenset(bids)

components = set()
for oid in order_to_batches.keys():
    components.add(get_component(oid))

gt_outcomes = {}
gt_rows_dict = {}
with open(SRC / "expected_reconciliation.csv", "r", encoding="utf-8") as f:
    for row in csv.DictReader(f):
        oid = row.get("work_key")
        gt_outcomes[oid] = row.get("expected_outcome", "").upper()
        gt_rows_dict[oid] = row

valid_components = []
for oids, bids in components:
    is_valid = True
    for bid in bids:
        if any(bad in bid for bad in ["MISSING"]):
            is_valid = False
            break
    for oid in oids:
        if gt_outcomes.get(oid) == "CURRENCY_MISMATCH":
            is_valid = False
            break
    if is_valid:
        valid_components.append((oids, bids))

def score_comp(comp):
    oids, _ = comp
    return len(set(gt_outcomes.get(o) for o in oids))

valid_components.sort(key=score_comp, reverse=True)

selected_orders = set()
for oids, bids in valid_components:
    if len(selected_orders) + len(oids) <= 130:
        selected_orders.update(oids)
    if len(selected_orders) >= 90:
        break

def format_id(val: str) -> str:
    if not val: return val
    # Strip the leading SYNTH-ORDER-000... to get a pure number
    m = re.search(r'0*(\d+)$', val)
    if m:
        num = m.group(1)
        if "ORDER" in val: return f"ORD-{num}"
        if "INT" in val: return f"PAY-{num}"
        if "PROC" in val: return f"SET-{num}"
        if "BANK" in val: return f"BANK-{num}"
    return val

# GT
with open(DST / "expected_reconciliation.csv", "w", encoding="utf-8", newline="") as f:
    if selected_orders:
        writer = csv.DictWriter(f, fieldnames=gt_rows_dict[next(iter(selected_orders))].keys())
        writer.writeheader()
        for oid in selected_orders:
            row = dict(gt_rows_dict[oid])
            row["work_key"] = format_id(row["work_key"])
            writer.writerow(row)

# Internal
internal_rows = []
with open(SRC / "internal_transactions.csv", "r", encoding="utf-8") as f:
    for row in csv.DictReader(f):
        if row.get("merchant_order_id") in selected_orders:
            row["merchant_order_id"] = format_id(row["merchant_order_id"])
            row["internal_payment_id"] = format_id(row["internal_payment_id"])
            internal_rows.append(row)

with open(DST / "internal_transactions.csv", "w", encoding="utf-8", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=internal_rows[0].keys())
    writer.writeheader()
    writer.writerows(internal_rows)

processor_rows = []
bank_rows = []
bank_fields = ["bank_entry_id", "settlement_batch_id", "booked_at", "credited_amount", "currency", "bank_reference", "description"]

bank_counter = 1
with open(SRC / "processor_transactions.csv", "r", encoding="utf-8") as f:
    for row in csv.DictReader(f):
        oid = row.get("merchant_order_id")
        if oid in selected_orders:
            clean_oid = format_id(oid)
            clean_proc = format_id(row["processor_transaction_id"])
            
            base_batch = row["settlement_batch_id"]
            new_batch_id = f"{base_batch}-{clean_oid}"
            row["settlement_batch_id"] = new_batch_id
            row["merchant_order_id"] = clean_oid
            row["processor_transaction_id"] = clean_proc
            processor_rows.append(row)
            
            # The exact clean_oid like "ORD-23" is placed inside the narration so P2 links it perfectly.
            bank_rows.append({
                "bank_entry_id": f"BANK-{clean_oid.replace('ORD-', '')}-{bank_counter}",
                "settlement_batch_id": new_batch_id,
                "booked_at": row["processor_event_time"],
                "credited_amount": row["net_amount"],
                "currency": row["currency"],
                "bank_reference": f"REF-{clean_oid}",
                "description": f"Synthetic 1-to-1 settlement for {clean_oid} {new_batch_id}"
            })
            bank_counter += 1

with open(DST / "processor_transactions.csv", "w", encoding="utf-8", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=processor_rows[0].keys())
    writer.writeheader()
    writer.writerows(processor_rows)

with open(DST / "bank_settlements.csv", "w", encoding="utf-8", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=bank_fields)
    writer.writeheader()
    writer.writerows(bank_rows)

print("Robust extraction complete with canonical ID mapping.")
