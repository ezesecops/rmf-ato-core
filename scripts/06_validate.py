#!/usr/bin/env python3
"""Stage 6 — validate every row and publish the rejection log.

Reads the chunked rows, applies the ten validation rules, and writes
data/processed/rows.validated.jsonl, data/processed/rejections.jsonl (merged
across every stage) and data/processed/summary.md.

    python scripts/06_validate.py [--manifest manifest.json] [--data-dir data]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rmf_ato_core.fetch import load_provenance  # noqa: E402
from rmf_ato_core.manifest import load_manifest  # noqa: E402
from rmf_ato_core.parse_oscal import Rejection, write_rejections, write_rows  # noqa: E402
from rmf_ato_core.schema import Row  # noqa: E402
from rmf_ato_core.validate import summary_markdown, validate_rows  # noqa: E402


def load_prior_rejections(interim: Path) -> list[Rejection]:
    rejections: list[Rejection] = []
    for path in sorted(interim.glob("*.rejections.jsonl")):
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                rejections.append(Rejection(**json.loads(line)))
    return rejections


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default="manifest.json")
    parser.add_argument("--data-dir", default="data")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    interim, processed = data_dir / "interim", data_dir / "processed"
    chunked = interim / "chunked.rows.jsonl"
    if not chunked.exists():
        print(f"FAIL: {chunked} not found — run Stage 5 first.", file=sys.stderr)
        return 1

    manifest = load_manifest(args.manifest)
    provenance = load_provenance(data_dir / "provenance.json")
    with chunked.open(encoding="utf-8") as handle:
        rows = [Row.from_dict(json.loads(line)) for line in handle]

    report = validate_rows(rows, manifest, provenance)

    prior = load_prior_rejections(interim)
    all_rejections = prior + report.rejections
    total_bytes = sum(len(row.text.encode("utf-8")) for row in report.valid)

    write_rows(processed / "rows.validated.jsonl", report.valid)
    write_rejections(processed / "rejections.jsonl", all_rejections)
    summary = summary_markdown(report, all_rejections, total_bytes)
    (processed / "summary.md").write_text(summary, encoding="utf-8")

    print(f"Validated {len(rows):,} rows -> {len(report.valid):,} published, "
          f"{len(report.rejections):,} rejected at this stage.\n")
    print("Rejections by rule (this stage):")
    stage_rules = Counter(r.rule for r in report.rejections)
    if not stage_rules:
        print("  none")
    for rule, count in sorted(stage_rules.items()):
        print(f"  {rule:<28}{count:>7,}")

    print("\nExpected-count checks:")
    if report.warnings:
        for warning in report.warnings:
            print(f"  WARN {warning}")
    else:
        print("  all counts in range")

    print(f"\nWrote {processed}/rows.validated.jsonl, rejections.jsonl, summary.md")
    print("\nHUMAN REVIEW: read summary.md and rejections.jsonl before export.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
