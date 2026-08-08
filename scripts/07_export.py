#!/usr/bin/env python3
"""Stage 7 — write the single-split parquet file.

    python scripts/07_export.py [--data-dir data]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rmf_ato_core.export import write_parquet  # noqa: E402
from rmf_ato_core.schema import Row  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="data")
    args = parser.parse_args()

    processed = Path(args.data_dir) / "processed"
    source = processed / "rows.validated.jsonl"
    if not source.exists():
        print(f"FAIL: {source} not found — run Stage 6 first.", file=sys.stderr)
        return 1

    with source.open(encoding="utf-8") as handle:
        rows = [Row.from_dict(json.loads(line)) for line in handle]

    destination = write_parquet(rows, processed / "train.parquet")
    size = destination.stat().st_size

    print(f"Wrote {destination}")
    print(f"  rows        {len(rows):,}")
    print(f"  size        {size / 1_000_000:.1f} MB")
    if size > 100_000_000:
        print("  WARNING: single-digit MB expected — something is wrong.", file=sys.stderr)

    print("\n  rows per document:")
    for doc_id, count in sorted(Counter(row.doc_id for row in rows).items()):
        print(f"    {doc_id:<24}{count:>7,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
