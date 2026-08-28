"""Generic tabular adapter: ANY csv/json source + a JSON column-mapping spec.

Spec shape (see docs/CANONICAL_SCHEMA.md):
{
  "format": "csv"|"json",
  "tables": {
    "orders": {
      "file": "orders.csv",
      "fields": {
        "order_id":     {"col": "Order Ref", "norm": "id"},
        "amount_paise": {"col": "Amount",   "norm": "money"},
        "created_at":   {"col": "Ts",       "norm": "date"},
        "status":       {"col": "State",    "norm": "status",
                         "values": {"PLACED": "confirmed"}},
        ...
      }
    },
    ...
  }
}

Normalizers: id, date, money (rupees/etc -> paise), scaled (numeric * scale),
status (mapped vocabulary), plain (str passthrough).
"""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path

from engine.adapters.base import SourceAdapter, empty_report, empty_tabs, scan_report

DATE_FORMATS = (
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
    "%d/%m/%Y %H:%M",
    "%d/%m/%Y",
    "%m/%d/%Y",
)


def _to_iso(value: str) -> str:
    s = str(value).strip()
    for fmt in DATE_FORMATS:
        try:
            dt = datetime.strptime(
                s.replace("Z", "+0000") if fmt.startswith("%Y-%m-%dT") else s, fmt
            )
            return dt.isoformat(timespec="seconds").replace("+00:00", "Z")
        except ValueError:
            continue
    return s


def normalize(norm: str, value, spec_field: dict | None = None):
    if value is None:
        return None
    spec_field = spec_field or {}
    if norm == "id":
        cleaned = str(value).strip().upper()
        return "".join(ch for ch in cleaned if ch.isalnum())

    if norm == "date":
        return _to_iso(value)
    if norm == "money":
        cleaned = str(value).replace(",", "").replace("₹", "").replace("Rs.", "").strip()
        return int(round(float(cleaned) * 100))
    if norm == "scaled":
        factor = float(spec_field.get("scale", 1))
        return int(round(float(str(value).replace(",", "")) * factor))
    if norm == "status":
        mapping = {k.lower(): v for k, v in (spec_field.get("values") or {}).items()}
        raw = str(value).strip()
        return mapping.get(raw.lower(), raw.lower())
    return str(value).strip()


class GenericTabularAdapter(SourceAdapter):
    name = "generic_tabular"

    def load(self, source: str | Path, spec_path: str | Path | None = None):
        assert spec_path is not None, "generic_tabular requires a JSON mapping spec"
        spec = json.loads(Path(spec_path).read_text())
        fmt = spec.get("format", "csv")
        root = Path(source)
        tabs, report = empty_tabs(), empty_report()
        if isinstance(spec.get("policy"), dict):
            tabs["_policy"] = spec["policy"]
        skipped = []

        for table, tcfg in spec.get("tables", {}).items():
            if table not in tabs:
                continue
            fpath = root / tcfg["file"]
            rows: list[dict] = []
            if fmt == "json":
                data = json.loads(fpath.read_text())
                records = data if isinstance(data, list) else data.get(table, [])
            else:
                with open(fpath, newline="", encoding="utf-8-sig") as fh:
                    records = list(csv.DictReader(fh))
            for i, rec in enumerate(records):
                out: dict = {}
                missing_required = False
                for canon_field, fcfg in tcfg.get("fields", {}).items():
                    raw = rec.get(fcfg.get("col", canon_field))
                    val = normalize(fcfg.get("norm", "plain"), raw, fcfg)
                    if fcfg.get("required") and (val is None or val == ""):
                        skipped.append((table, i, canon_field))
                        missing_required = True
                    out[canon_field] = val
                for const_key, const_val in tcfg.get("constants", {}).items():
                    out.setdefault(const_key, const_val)
                if not missing_required:
                    rows.append(out)
            tabs[table] = rows

        report["skipped_rows"] = len(skipped)
        scan_report(tabs, report)
        return tabs, report
