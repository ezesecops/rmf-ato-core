#!/usr/bin/env python3
"""Stage 2 — download manifest artifacts and write data/provenance.json.

Idempotent: artifacts already on disk whose sha256 matches provenance are not
re-requested. Manual documents are hashed if present and reported if not.

    python scripts/02_fetch.py [--manifest manifest.json] [--data-dir data]
                               [--include-web] [--force] [--only DOC_ID ...]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rmf_ato_core.fetch import (  # noqa: E402
    fetch_all,
    fetch_document,
    load_provenance,
    save_provenance,
)
from rmf_ato_core.manifest import PoliteClient, load_manifest  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default="manifest.json")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument(
        "--include-web",
        action="store_true",
        help="also retrieve the web-native source (AI RMF Playbook); off by default",
    )
    parser.add_argument("--force", action="store_true", help="re-download even if cached")
    parser.add_argument("--only", nargs="+", metavar="DOC_ID", help="restrict to these doc_ids")
    args = parser.parse_args()

    manifest = load_manifest(args.manifest)
    data_dir = Path(args.data_dir)

    if args.only:
        unknown = [doc_id for doc_id in args.only if manifest.by_id(doc_id) is None]
        if unknown:
            print(f"FAIL: not in manifest: {unknown}", file=sys.stderr)
            return 1
        provenance_path = data_dir / "provenance.json"
        provenance = load_provenance(provenance_path)
        results = []
        with PoliteClient() as client:
            for doc_id in args.only:
                result = fetch_document(
                    client, manifest.by_id(doc_id), data_dir, provenance,
                    include_web=args.include_web, force=args.force,
                )
                results.append(result)
                print(f"  {result.action:<15} {doc_id:<22} {result.detail}")
                save_provenance(provenance_path, provenance)
    else:
        print(f"Fetching {len(manifest.documents)} manifest entries into {data_dir}/raw …\n")
        results = fetch_all(
            manifest, data_dir, include_web=args.include_web, force=args.force
        )

    print()
    counts: dict[str, int] = {}
    for result in results:
        counts[result.action] = counts.get(result.action, 0) + 1
    print("Summary: " + ", ".join(f"{action}={count}" for action, count in sorted(counts.items())))

    missing = [r for r in results if r.action == "manual-missing"]
    if missing:
        print("\nManual placement still needed (parsing will skip these until supplied):")
        for result in missing:
            print(f"  - {result.doc_id}: {result.detail}")

    failures = [r for r in results if r.action == "failed"]
    if failures:
        print("\nFAILED:", file=sys.stderr)
        for result in failures:
            print(f"  - {result.doc_id}: {result.detail}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
