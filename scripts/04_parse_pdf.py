#!/usr/bin/env python3
"""Stage 4 — parse the PDF documents (and the Playbook JSON) into interim rows.

Writes data/interim/{doc_id}.rows.jsonl and .rejections.jsonl per document, then
prints per-document counts, rejection counts by rule, and sample rows.

    python scripts/04_parse_pdf.py [--manifest manifest.json] [--data-dir data]
                                   [--only DOC_ID ...] [--samples 3]
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rmf_ato_core.fetch import load_provenance, raw_path  # noqa: E402
from rmf_ato_core.manifest import load_manifest  # noqa: E402
from rmf_ato_core.parse_oscal import write_rejections, write_rows  # noqa: E402
from rmf_ato_core.parse_pdf import parse_pdf_document  # noqa: E402
from rmf_ato_core.parse_playbook import parse_playbook  # noqa: E402
from rmf_ato_core.schema import Row  # noqa: E402

PLAYBOOK_DOC_ID = "AI-RMF-PLAYBOOK"


def print_samples(rows: list[Row], limit: int) -> None:
    """Show a spread of chunk types rather than the first N of one kind."""
    by_type: dict[str, list[Row]] = {}
    for row in rows:
        by_type.setdefault(row.chunk_type, []).append(row)

    shown = 0
    for chunk_type in sorted(by_type):
        for row in by_type[chunk_type][:1] if len(by_type) > 1 else by_type[chunk_type][:limit]:
            if shown >= limit:
                return
            shown += 1
            print(f"\n  --- {row.id}  ({row.chunk_type}, {len(row.text)} chars)")
            print(f"      path: {row.section_path}")
            excerpt = row.text if len(row.text) <= 700 else row.text[:700] + " …"
            for line in excerpt.splitlines():
                print(f"      {line}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default="manifest.json")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--only", nargs="+", metavar="DOC_ID")
    parser.add_argument("--samples", type=int, default=3)
    args = parser.parse_args()

    manifest = load_manifest(args.manifest)
    data_dir = Path(args.data_dir)
    interim = data_dir / "interim"
    provenance = load_provenance(data_dir / "provenance.json")

    targets = [
        doc for doc in manifest.documents
        if doc.format in {"pdf", "pdf-manual"} or doc.doc_id == PLAYBOOK_DOC_ID
    ]
    if args.only:
        targets = [doc for doc in targets if doc.doc_id in args.only]
        if not targets:
            print(f"FAIL: no PDF documents match {args.only}", file=sys.stderr)
            return 1

    totals: Counter[str] = Counter()
    rejection_totals: Counter[str] = Counter()
    missing: list[str] = []
    report: list[tuple[str, int, dict[str, int], int]] = []

    for doc in targets:
        path = raw_path(data_dir, doc)
        if doc.doc_id == PLAYBOOK_DOC_ID:
            path = data_dir / "raw" / f"{doc.doc_id}.json"
        if not path.exists():
            missing.append(f"{doc.doc_id}: {path} not present")
            continue

        entry = provenance.get(doc.doc_id)
        if entry is None or not entry.sha256:
            missing.append(f"{doc.doc_id}: no provenance hash — run Stage 2 first")
            continue

        if doc.doc_id == PLAYBOOK_DOC_ID:
            rows, rejections = parse_playbook(path, doc, entry.sha256)
        else:
            result = parse_pdf_document(doc, path, entry.sha256)
            rows, rejections = result.rows, result.rejections

        write_rows(interim / f"{doc.doc_id}.rows.jsonl", rows)
        write_rejections(interim / f"{doc.doc_id}.rejections.jsonl", rejections)

        kinds = Counter(row.chunk_type for row in rows)
        totals.update(kinds)
        rejection_totals.update(rejection.rule for rejection in rejections)
        report.append((doc.doc_id, len(rows), dict(kinds), len(rejections)))

        if args.samples:
            print(f"\n=== {doc.doc_id} — {len(rows)} rows, {len(rejections)} rejections ===")
            print_samples(rows, args.samples)

    print("\n\n### Rows by document\n")
    print(f"  {'doc_id':<18}{'rows':>6}{'rejected':>10}   chunk types")
    print("  " + "-" * 76)
    for doc_id, count, kinds, rejected in report:
        breakdown = ", ".join(f"{kind}={number}" for kind, number in sorted(kinds.items()))
        print(f"  {doc_id:<18}{count:>6}{rejected:>10}   {breakdown}")
    print("  " + "-" * 76)
    print(f"  {'TOTAL':<18}{sum(c for _, c, _, _ in report):>6}"
          f"{sum(r for _, _, _, r in report):>10}")

    print("\n### Rows by chunk type\n")
    for chunk_type, count in sorted(totals.items()):
        print(f"  {chunk_type:<22}{count:>6}")

    print("\n### Rejections by rule\n")
    for rule, count in sorted(rejection_totals.items()):
        print(f"  {rule:<30}{count:>6}")

    if missing:
        print("\n### Not parsed (artifact missing)\n")
        for item in missing:
            print(f"  - {item}")
        print("\n  Manual documents must be placed by hand; see README.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
