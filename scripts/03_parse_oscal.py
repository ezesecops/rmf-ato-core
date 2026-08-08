#!/usr/bin/env python3
"""Stage 3 — parse the OSCAL catalog and baseline profiles into interim rows.

Writes data/interim/{doc_id}.rows.jsonl and .rejections.jsonl, then prints the
family count table, the set of part names encountered, and sample rows for the
maintainer's review.

    python scripts/03_parse_oscal.py [--manifest manifest.json] [--data-dir data]
                                     [--samples 10]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rmf_ato_core.fetch import load_provenance, raw_path  # noqa: E402
from rmf_ato_core.manifest import load_manifest  # noqa: E402
from rmf_ato_core.parse_oscal import (  # noqa: E402
    ASSESSMENT_DOC_ID,
    parse_catalog,
    parse_profile,
    write_rejections,
    write_rows,
)
from rmf_ato_core.schema import Row  # noqa: E402

CATALOG_DOC_ID = "SP-800-53r5"

# Rows the maintainer asked to eyeball against the official publications.
SAMPLE_IDS = [
    "SP-800-53r5/control/ac-2",
    "SP-800-53r5/control_discussion/ac-2",
    "SP-800-53r5/control_enhancement/ac-2.3",
    "SP-800-53r5/control_discussion/ra-5",
    "SP-800-53r5/control/sr-11",
    "SP-800-53Ar5/assessment_objective/ac-2",
    "SP-800-53Ar5/assessment_method/ac-2",
    "SP-800-53Ar5/assessment_objective/ac-2.3",
    "SP-800-53B-MODERATE/baseline/moderate",
    "SP-800-53r5/control/pm-1",
]


def print_family_table(stats) -> None:
    kinds = ["control", "enhancement", "discussion", "objective", "method", "withdrawn"]
    header = f"{'family':<8}" + "".join(f"{kind:>13}" for kind in kinds)
    print(header)
    print("-" * len(header))
    for family in sorted(stats.by_family):
        counter = stats.by_family[family]
        print(f"{family.upper():<8}" + "".join(f"{counter[kind]:>13}" for kind in kinds))
    print("-" * len(header))
    print(f"{'TOTAL':<8}" + "".join(f"{stats.total(kind):>13}" for kind in kinds))


def print_samples(rows: list[Row], wanted: list[str], limit: int) -> None:
    by_id = {row.id: row for row in rows}
    shown = 0
    for row_id in wanted:
        row = by_id.get(row_id)
        if row is None:
            print(f"\n[sample missing: {row_id}]")
            continue
        if shown >= limit:
            break
        shown += 1
        print("\n" + "=" * 78)
        print(f"id           {row.id}")
        print(f"doc_id       {row.doc_id}   revision={row.revision}   tier={row.tier}")
        print(f"chunk_type   {row.chunk_type}")
        print(f"control_id   {row.control_id}")
        print(f"section_path {row.section_path}")
        print(f"chars        {len(row.text)}")
        print("-" * 78)
        text = row.text
        print(text if len(text) <= 2000 else text[:2000] + f"\n… [{len(text) - 2000} more chars]")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default="manifest.json")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--samples", type=int, default=10)
    args = parser.parse_args()

    manifest = load_manifest(args.manifest)
    data_dir = Path(args.data_dir)
    interim = data_dir / "interim"
    provenance = load_provenance(data_dir / "provenance.json")

    catalog_doc = manifest.by_id(CATALOG_DOC_ID)
    assessment_doc = manifest.by_id(ASSESSMENT_DOC_ID)
    catalog_path = raw_path(data_dir, catalog_doc)
    if not catalog_path.exists():
        print(f"FAIL: {catalog_path} not found — run Stage 2 first.", file=sys.stderr)
        return 1

    catalog_sha = provenance[CATALOG_DOC_ID].sha256
    rows, rejections, stats = parse_catalog(catalog_path, catalog_doc, assessment_doc, catalog_sha)

    catalog_rows = [row for row in rows if row.doc_id == CATALOG_DOC_ID]
    assessment_rows = [row for row in rows if row.doc_id == ASSESSMENT_DOC_ID]
    write_rows(interim / f"{CATALOG_DOC_ID}.rows.jsonl", catalog_rows)
    write_rows(interim / f"{ASSESSMENT_DOC_ID}.rows.jsonl", assessment_rows)
    write_rejections(interim / f"{CATALOG_DOC_ID}.rejections.jsonl", rejections)

    baseline_rows: list[Row] = []
    for doc in manifest.documents:
        if doc.format != "oscal" or doc.doc_id == CATALOG_DOC_ID:
            continue
        profile_path = raw_path(data_dir, doc)
        if not profile_path.exists():
            print(f"WARN: {profile_path} missing, skipping {doc.doc_id}", file=sys.stderr)
            continue
        produced = parse_profile(profile_path, doc, provenance[doc.doc_id].sha256)
        write_rows(interim / f"{doc.doc_id}.rows.jsonl", produced)
        baseline_rows.extend(produced)

    all_rows = rows + baseline_rows

    print("\n### Part names encountered in the catalog\n")
    for name, count in stats.part_names.most_common():
        print(f"  {name:<26} {count:>6}")

    print("\n### Rows by family\n")
    print_family_table(stats)

    print("\n### Rows written\n")
    print(f"  {CATALOG_DOC_ID:<22} {len(catalog_rows):>6}  (control, control_enhancement, control_discussion)")
    print(f"  {ASSESSMENT_DOC_ID:<22} {len(assessment_rows):>6}  (assessment_objective, assessment_method)")
    print(f"  {'SP-800-53B baselines':<22} {len(baseline_rows):>6}  (baseline)")
    print(f"  {'TOTAL':<22} {len(all_rows):>6}")

    print("\n### Rejections\n")
    by_rule: dict[str, int] = {}
    for rejection in rejections:
        by_rule[rejection.rule] = by_rule.get(rejection.rule, 0) + 1
    for rule, count in sorted(by_rule.items()):
        print(f"  {rule:<26} {count:>6}")
    if stats.unresolved_params:
        print(f"\n  WARN unresolved param references: {len(stats.unresolved_params)}")

    lengths = sorted(len(row.text) for row in all_rows)
    under = sum(1 for length in lengths if length < 200)
    over = sum(1 for length in lengths if length > 8000)
    print("\n### Text length (chars)\n")
    print(f"  min={lengths[0]}  median={lengths[len(lengths) // 2]}  max={lengths[-1]}")
    print(f"  below 200-char floor: {under} ({under / len(lengths):.1%})   above 8000 ceiling: {over}")

    if args.samples:
        print("\n\n### Sample rows for SME review")
        print_samples(all_rows, SAMPLE_IDS, args.samples)

    # Residue check here as well as in Stage 6: a surviving template marker
    # means the parameter renderer missed something and should be fixed now.
    residue = [row.id for row in all_rows if "{{" in row.text or "insert: param" in row.text]
    if residue:
        print(f"\nFAIL: {len(residue)} rows still contain template markers, e.g. {residue[:3]}",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
