import json
import sqlite3

from generator.config import FIXED_CAUSE_MIX, NOISE_COUNTS, GeneratorConfig
from generator.generate import canonical_dump, generate
from generator.validate import run as validate_run


def test_generation_validates_green(tmp_path):
    paths = generate(GeneratorConfig(seed=42, size=60), tmp_path)
    report = validate_run(paths["db"], paths["gt"])
    assert report["passed"], report["problems"][:10]


def test_composition_matches_spec(tmp_path):
    paths = generate(GeneratorConfig(seed=42, size=60), tmp_path)
    gt = json.loads(paths["gt"].read_text())
    for cause, n in FIXED_CAUSE_MIX.items():
        if cause == "COMBINED_SETTLEMENT":
            assert gt["composition"][cause] >= n, cause
        else:
            assert gt["composition"][cause] == n, cause
    assert gt["composition"]["CLEAN_MATCH"] == 60 - sum(FIXED_CAUSE_MIX.values())
    assert gt["composition"]["BANK_ONLY_CREDIT"] == 2
    for k, v in NOISE_COUNTS.items():
        assert gt["noise_counts"][k] == v


def test_combined_groups_share_credits(tmp_path):
    paths = generate(GeneratorConfig(seed=42, size=60), tmp_path)
    gt = json.loads(paths["gt"].read_text())
    members_by_bank: dict[str, list] = {}
    for t in gt["transactions"]:
        if t["cause_code"] == "COMBINED_SETTLEMENT":
            for bid in t["expected_links"]["bank_txns"]:
                members_by_bank.setdefault(bid, []).append(t)
    assert members_by_bank, "expected at least one combined group"
    multi = {bid: ts for bid, ts in members_by_bank.items() if len(ts) >= 2}
    assert multi, "combined credits must be shared by >=2 transactions"
    con = sqlite3.connect(paths["db"])
    for bid, ts in multi.items():
        (amt,) = con.execute(
            "SELECT amount_paise FROM bank_txns WHERE bank_txn_id=?", (bid,)
        ).fetchone()
        net_sum = 0
        for t in ts:
            for sid in t["expected_links"]["settlements"]:
                (n,) = con.execute(
                    "SELECT net_paise FROM settlements WHERE settlement_id=?", (sid,)
                ).fetchone()
                net_sum += n
        assert amt == net_sum, f"{bid}: payout {amt} != member nets {net_sum}"


def test_determinism_same_seed(tmp_path):
    p1 = generate(GeneratorConfig(seed=99, size=60), tmp_path / "a")
    p2 = generate(GeneratorConfig(seed=99, size=60), tmp_path / "b")
    assert canonical_dump(p1["db"]) == canonical_dump(p2["db"])
    assert p1["gt"].read_bytes() == p2["gt"].read_bytes()


def test_different_seed_different_data(tmp_path):
    p1 = generate(GeneratorConfig(seed=1, size=60), tmp_path / "a")
    p2 = generate(GeneratorConfig(seed=2, size=60), tmp_path / "b")
    assert canonical_dump(p1["db"]) != canonical_dump(p2["db"])


def test_size100_also_validates(tmp_path):
    paths = generate(GeneratorConfig(seed=7, size=100), tmp_path)
    report = validate_run(paths["db"], paths["gt"])
    assert report["passed"], report["problems"][:10]


def test_no_cause_leakage_into_db(tmp_path):
    paths = generate(GeneratorConfig(seed=42, size=60), tmp_path)
    con = sqlite3.connect(paths["db"])
    try:
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        blob = ""
        for t in tables:
            for row in con.execute(f"SELECT * FROM {t}"):
                blob += " ".join(str(v) for v in row if v is not None)
        for cause in [
            "CLEAN_MATCH",
            "FEE_EXPLAINED",
            "UNEXPLAINED_DELTA",
            "MISSING_SETTLEMENT",
            "COMBINED_SETTLEMENT",
            "genuine_discrepancy",
            "matched_after_reasoning",
        ]:
            assert cause not in blob, f"ground-truth token leaked into DB: {cause}"
    finally:
        con.close()


def test_unresolved_are_provably_unexplainable(tmp_path):
    paths = generate(GeneratorConfig(seed=42, size=60), tmp_path)
    gt = json.loads(paths["gt"].read_text())
    unresolved = [t for t in gt["transactions"] if t["expected_class"] == "unresolved"]
    assert len(unresolved) == 8
    for t in unresolved:
        links = t["expected_links"]
        assert not links["adjustments"]
        if t["cause_code"] == "MISSING_SETTLEMENT":
            assert not links["settlements"] and not links["bank_txns"]
