#!/usr/bin/env python3
"""Stage 5 — normalize chunk sizes across all interim rows.

Reads data/interim/*.rows.jsonl, merges stub sections into their following
sibling, splits oversized rows at paragraph boundaries, and writes
data/interim/chunked.rows.jsonl plus its rejection log.

    python scripts/05_chunk.py [--data-dir data]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rmf_ato_core.chunk import chunk_rows  # noqa: E402
from rmf_ato_core.parse_oscal import write_rejections, write_rows  # noqa: E402
from rmf_ato_core.schema import MAX_TEXT_LEN, Row, min_text_len  # noqa: E402

CHUNKED_NAME = "chunked.rows.jsonl"


def load_interim(interim: Path) -> list[Row]:
    rows: list[Row] = []
    for path in sorted(interim.glob("*.rows.jsonl")):
        if path.name == CHUNKED_NAME:
            continue
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                rows.append(Row.from_dict(json.loads(line)))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="data")
    args = parser.parse_args()

    interim = Path(args.data_dir) / "interim"
    rows = load_interim(interim)
    if not rows:
        print("FAIL: no interim rows found — run Stages 3 and 4 first.", file=sys.stderr)
        return 1

    print(f"Loaded {len(rows):,} interim rows from {interim}")
    before_over = sum(1 for row in rows if len(row.text) > MAX_TEXT_LEN)
    before_under = sum(1 for row in rows if len(row.text) < min_text_len(row.chunk_type))

    chunked, rejections = chunk_rows(rows)

    write_rows(interim / CHUNKED_NAME, chunked)
    write_rejections(interim / "chunked.rejections.jsonl", rejections)

    after_over = sum(1 for row in chunked if len(row.text) > MAX_TEXT_LEN)
    after_under = sum(1 for row in chunked if len(row.text) < min_text_len(row.chunk_type))
    parts = sum(1 for row in chunked if " (part " in row.id)

    print(f"\n  rows in                {len(rows):>7,}")
    print(f"  rows out               {len(chunked):>7,}")
    print(f"  split parts created    {parts:>7,}")
    print(f"  dropped as trailing    {len(rejections):>7,}")
    print(f"\n  over {MAX_TEXT_LEN} chars:  {before_over:>5}  ->{after_over:>5}")
    print(f"  under length floor:  {before_under:>5}  ->{after_under:>5}")

    lengths = sorted(len(row.text) for row in chunked)
    print(f"\n  length  min={lengths[0]}  median={lengths[len(lengths) // 2]}  max={lengths[-1]}")
    print("\n  by chunk type:")
    for chunk_type, count in sorted(Counter(row.chunk_type for row in chunked).items()):
        print(f"    {chunk_type:<24}{count:>7,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
