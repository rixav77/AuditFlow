"""CLI: uv run python -m generator.cli --seed 42 --size 60"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from generator.config import GeneratorConfig
from generator.generate import canonical_dump, generate
from generator.validate import run as validate_run


def main() -> int:
    ap = argparse.ArgumentParser(prog="generator")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--size", type=int, default=60)
    ap.add_argument("--difficulty", choices=["EASY", "NORMAL", "HARD"], default="NORMAL")
    ap.add_argument("--out", type=Path, default=Path("data/synthetic"))
    ap.add_argument("--skip-validate", action="store_true")
    args = ap.parse_args()

    cfg = GeneratorConfig(seed=args.seed, size=args.size, difficulty=args.difficulty)
    paths = generate(cfg, args.out)

    print(f"batch   : {paths['db']}")
    print(f"truth   : {paths['gt']}")
    print(f"manifest: {paths['manifest']}")

    if not args.skip_validate:
        report = validate_run(paths["db"], paths["gt"])
        print(json.dumps(report, indent=2))
        dump = canonical_dump(paths["db"])
        print(f"dump_sha256: {dump[:16]}…")
        if not report["passed"]:
            print("VALIDATION FAILED")
            return 1
        print("VALIDATION PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
