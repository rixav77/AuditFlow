"""Bank narration composer: AgamiAI-derived templates + difficulty-driven hardening."""

from __future__ import annotations

import random
import re
from datetime import date

from generator.config import DEAD_ORDER_RANGE, RTGS_MIN_PAISE, DifficultyParams
from generator.entities import MERCHANT_BANK, PERSONS, rand_ifsc, rand_utr, rand_vpa

CHANNEL_WEIGHTS = [
    ("NEFT", 35),
    ("ACH", 20),
    ("IMPS", 15),
    ("RTGS", 10),
    ("UPI", 10),
    ("CLG", 10),
]

SVC_VARIANTS = [
    "Foreign Curr",
    "Cheque Book ",
    "Online Banki",
    "Demat Accoun",
    "Account Main",
    "Statement Ch",
    "NEFT/RTGS Ch",
    "SMS Alert Ch",
]

JUNK_SUFFIXES = [" /IBL", " TXN", " VALOK", " *PGI"]

REF_PATTERNS = [re.compile(r"\bORD[-#\s]?(\d{3,6})\b", re.IGNORECASE)]


def pick_channel(rng: random.Random, amount_paise: int | None = None) -> str:
    if amount_paise is not None and amount_paise >= RTGS_MIN_PAISE:
        return rng.choice(["RTGS", "RTGS", "NEFT"])
    pool = [(n, w) for n, w in CHANNEL_WEIGHTS if n != "RTGS"]
    total = sum(w for _, w in pool)
    roll = rng.randrange(total)
    for name, w in pool:
        if roll < w:
            return name
        roll -= w
    return "NEFT"


def mutate_ref(rng: random.Random, order_id: str, absent_p: float = 0.08) -> str:
    roll = rng.random()
    if roll < 0.50:
        return order_id
    if roll < 0.72:
        return order_id.replace("-", "")
    if roll < 0.84:
        return order_id.replace("-", "#").lower()
    if roll < 0.92:
        head, num = order_id.split("-")
        return f"{head.capitalize()} {num}"
    if roll < 0.92 + absent_p:
        return ""
    return order_id


HOMOGLYPHS = {"0": "O", "1": "I", "5": "S", "8": "B"}


def _typo(rng: random.Random, token: str, p: float, live: set[str] | None = None) -> str:
    """OCR-style homoglyph corruption: breaks regex extraction without ever
    producing another valid numeric id."""
    if rng.random() >= p:
        return token
    positions = [i for i, ch in enumerate(token) if ch in HOMOGLYPHS]
    if not positions:
        return token
    i = rng.choice(positions)
    chars = list(token)
    chars[i] = HOMOGLYPHS[chars[i]]
    return "".join(chars)


def _decoy_token(rng: random.Random, dp: DifficultyParams) -> str:
    dead_num = rng.randrange(DEAD_ORDER_RANGE[0], DEAD_ORDER_RANGE[1] + 1)
    return f"ORD-{dead_num}"


def credit_narration(
    rng: random.Random,
    hint_day,
    order_id: str,
    utr: str | None,
    amount_paise: int | None = None,
    dp: DifficultyParams | None = None,
    live_orders: set[str] | None = None,
) -> str:
    dp = dp or DifficultyParams()
    channel = pick_channel(rng, amount_paise)
    ref = mutate_ref(rng, order_id, dp.ref_absent_p)
    utr = utr or rand_utr(rng, hint_day)
    digits12 = "".join(rng.choices("0123456789", k=12))
    if channel == "NEFT":
        line = f"NEFT Cr-{utr}-{rand_ifsc(rng)}-RAZORPAY SOFTWARE-PAYOUT {ref}--"
    elif channel == "ACH":
        line = f"ACH-C-{utr}-RAZORPAY PAYOUTS-{ref}"
    elif channel == "IMPS":
        line = f"IMPS-{digits12}-RAZORPAY-{ref}"
    elif channel == "RTGS":
        line = f"RTGS-{utr}-RAZORPAY SOFTWARE PVT LTD-/{ref}/URGENT/"
    elif channel == "UPI":
        person = rng.choice(PERSONS)
        line = f"UPI/CR/{digits12}/{person.upper()}/{rand_vpa(rng, person)}-{ref}"
    else:
        line = f"By Clg:{MERCHANT_BANK[0]} BANK-RAZORPAY, COLLECTION {ref}"

    if ref:
        idx = line.find(ref)
        if idx >= 0:
            seg = _typo(rng, ref, dp.typo_ref_p, live_orders)
            line = f"{line[:idx]}{seg}{line[idx + len(ref) :]}"

    if rng.random() < dp.decoy_ref_p:
        sep = "/" if "/" in line[-6:] or channel == "RTGS" else "-"
        line = f"{line}{sep}{_decoy_token(rng, dp)}"

    if rng.random() < dp.junk_suffix_p:
        line = f"{line}{rng.choice(JUNK_SUFFIXES)}"

    if not ref:
        line = line.replace("  ", " ").replace("//", "/").rstrip("-")

    if rng.random() < dp.truncate_p and len(line) > 38:
        cut = rng.randrange(30, 39)
        line = line[:cut]
    return line


def ensure_identifier(narration: str, order_id: str, utr: str | None) -> str:
    """Real payout narrations always carry at least one usable identifier."""
    has_ref = bool(extract_order_refs(narration))
    has_utr = bool(utr and utr in narration)
    if has_ref or has_utr:
        return narration
    sep = "" if narration.endswith("-") else "-"
    return f"{narration}{sep}{order_id}"


def refund_debit_narration(rng: random.Random, order_id: str, utr: str) -> str:
    return f"ACH-D-{utr}-RAZORPAY-REFUND {order_id}"


def svc_debit_narration(rng: random.Random) -> str:
    return f"Service Charges-{rng.choice(SVC_VARIANTS)}"


def reversal_debit_narration(rng: random.Random) -> str:
    person = rng.choice(PERSONS)
    digits12 = "".join(rng.choices("0123456789", k=12))
    return f"REVERSAL-UPI/CR/{digits12}/{person.upper()}/{rand_vpa(rng, person)}"


MOJIBAKE_CHARS = list("\ufffd\u00e2\u20ac\u2122\u00c2\ufeff")


def mojibake_glitch(rng: random.Random) -> str:
    return "".join(rng.choices(MOJIBAKE_CHARS, k=3))


def bank_only_narration(rng: random.Random, business: str, on_day: date) -> str:
    utr = rand_utr(rng, on_day)
    return f"NEFT Cr-{utr}-{rand_ifsc(rng)}-{business}--"


def extract_order_refs(text: str) -> set[str]:
    """Canonical order ids found in free text; typo'd refs intentionally do not match."""
    found: set[str] = set()
    for pat in REF_PATTERNS:
        for num in pat.findall(text):
            found.add(f"ORD-{int(num)}")
    return found


def extract_raw_ref_tokens(text: str) -> list[str]:
    """Raw numeric strings as they appear (leading zeros preserved)."""
    toks: list[str] = []
    for pat in REF_PATTERNS:
        toks.extend(pat.findall(text))
    return toks
