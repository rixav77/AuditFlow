"""Entity pools: names, items, banks, identifier minters."""

from __future__ import annotations

import random
import string
from datetime import UTC, date, datetime, timedelta

BUSINESSES = [
    "SHARMA TEXTILES",
    "MEHTA ELECTRONICS",
    "GUPTA FOODS",
    "KRISHNA HARDWARE",
    "PRIYA BOOKS",
    "ARJUN SPORTS",
    "LAKSHMI SILKS",
    "VERMA PHARMA",
    "IYER ORGANICS",
    "SINGH AUTO PARTS",
    "REDDY AGRO",
    "BOSE FURNITURE",
    "NAIR JEWELLERS",
    "JOSHY BAKERY",
    "KHAN LEATHER",
    "PATEL STATIONERY",
    "DESAI TOYS",
    "MISHRA PAINTS",
    "CHOPRA FOOTWEAR",
    "RATHOR CROCKERY",
    "SEN OPTICALS",
    "BHATIA MUSIC",
    "NAIK GARDEN",
    "RAJAN CYCLES",
    "PILLAI HOME NEEDS",
    "THAKUR MOBILE",
    "DAS STATIONERY",
    "KULKARNI LIGHTS",
    "SAHA PACKAGING",
    "MENON EXPORTS",
]

PERSONS = [
    "Rahul Sharma",
    "Priya Mehta",
    "Amit Gupta",
    "Sneha Iyer",
    "Vikram Singh",
    "Ananya Reddy",
    "Karthik Nair",
    "Deepika Bose",
    "Arjun Patel",
    "Meera Joshi",
    "Rohit Verma",
    "Kavya Menon",
    "Sanjay Kumar",
    "Divya Raghavan",
    "Nikhil Chopra",
    "Pooja Desai",
    "Aditya Mishra",
    "Shreya Banerjee",
    "Varun Rao",
    "Neha Kulkarni",
]

ITEMS = [
    "Cotton sarees bulk order",
    "LED bulb carton 50pcs",
    "Basmati rice 25kg x10",
    "Hand tools set 120pcs",
    "Notebooks A4 bundle",
    "Footballs size-5 dozen",
    "Kanchipuram silk batch",
    "Paracetamol 500mg strips x200",
    "Organic soap tray",
    "Brake pads set of 8",
    "Hybrid seeds 5kg",
    "Sheesham dining chairs pair",
    "Gold-plated bangles set",
    "Eggless chocolate cakes x6",
    "Cowhide wallet lot",
    "A4 paper reams x20",
    "STEM robot kits x5",
    "Interior emerald paint drums",
    "Running shoes assorted x12",
    "Ceramic dinner sets x4",
    "Reading glasses box",
    "Acoustic guitar strings pack",
    "Terracotta pots x30",
    "MTB cycles x3",
    "Mixer grinders x6",
    "Budget smartphones x4",
    "Register books x25",
    "CFL/LED mixed lights x40",
    "Corrugated boxes medium x100",
    "Cashew kernels 10kg",
]

BANKS = [
    ("HDFC", "HDFC0000123"),
    ("ICIC", "ICIC0004567"),
    ("UTIB", "UTIB0007890"),
    ("SBIN", "SBIN0002345"),
    ("KKBK", "KKBK0006789"),
    ("YESB", "YESB0009012"),
    ("IDIB", "IDIB0003456"),
    ("PUNB", "PUNB0005678"),
]
MERCHANT_BANK = ("HDFC", "HDFC0000123")
MERCHANT_NAME = "AARAV ENTERPRISES"


def minter(prefix_counts: dict[str, int], rng: random.Random | None = None, max_step: int = 1):
    """Sequential (optionally jittered) ID minter; jitter creates realistic id gaps."""
    counters = dict(prefix_counts)
    seen: dict[str, set[str]] = {}

    class Minter:
        def next(self, kind: str) -> str:
            step = rng.randrange(1, max_step + 1) if rng is not None and max_step > 1 else 1
            counters[kind] = counters.get(kind, 0) + step
            base = {
                "order": "ORD",
                "payment": "PAY",
                "settlement": "SET",
                "bank": "BANK",
                "adjustment": "ADJ",
            }[kind]
            ident = f"{base}-{counters[kind]}"
            seen.setdefault(kind, set()).add(ident)
            return ident

        def seen(self, kind: str) -> set[str]:
            return set(seen.get(kind, set()))

        def peek(self, kind: str) -> int:
            return counters[kind]

    return Minter()


def rand_amount(rng: random.Random) -> int:
    """Mixture: 70% small / 25% mid / 5% large. Whole rupees in paise."""
    roll = rng.random()
    if roll < 0.70:
        rupees = rng.randrange(500, 5_001)
    elif roll < 0.95:
        rupees = rng.randrange(5_000, 50_001)
    else:
        rupees = rng.randrange(50_000, 300_001)
    return rupees * 100


def rand_utr(rng: random.Random, on_date: date) -> str:
    code = rng.choice(BANKS)[0]
    return f"{code}{on_date.strftime('%y%m%d')}{rng.randrange(10**6):06d}"


def rand_processor_ref(rng: random.Random) -> str:
    suffix = "".join(rng.choices(string.ascii_letters + string.digits, k=11))
    return f"pay_RP{suffix}"


def rand_ifsc(rng: random.Random) -> str:
    return rng.choice(BANKS)[1]


def rand_vpa(rng: random.Random, person: str) -> str:
    handle = person.lower().split()[0] + rng.randrange(100, 999).__str__()
    return f"{handle}@{rng.choice(['pay', 'upi', 'okicici', 'ybl', 'axl'])}"


def ts(day: date, hour: int, minute: int) -> str:
    dt = datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


def rand_daytime(rng: random.Random, day: date) -> str:
    return ts(day, rng.randrange(8, 22), rng.randrange(60))


def plus_minutes(iso_ts: str, minutes: int) -> str:
    dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    return (dt + timedelta(minutes=minutes)).isoformat(timespec="seconds").replace("+00:00", "Z")


def synth_date(base: tuple[int, int, int], offset_days: int) -> date:
    return date(*base) + timedelta(days=offset_days)
