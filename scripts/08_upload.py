#!/usr/bin/env python3
"""Stage 9 — upload to a PRIVATE Hugging Face dataset repository.

Gated on purpose. This script refuses to run without `--i-have-approval` and an
interactive confirmation, creates the repo **private**, and never makes anything
public — flipping visibility is a human action in the HF UI, after inspecting
the dataset viewer.

    HF_TOKEN=... python scripts/08_upload.py --i-have-approval

The token is read from the environment only. It is never written to a file, a
log line, or a commit.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

REPO_ID = "ezesecops/rmf-ato-core"

# (local path, path in the dataset repo)
UPLOADS = (
    ("data/processed/train.parquet", "data/train.parquet"),
    ("dataset_card/README.md", "README.md"),
    ("manifest.json", "manifest.json"),
    ("data/provenance.json", "provenance.json"),
    ("data/processed/rejections.jsonl", "rejections.jsonl"),
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--i-have-approval",
        action="store_true",
        help="required: the maintainer has reviewed the summary and rejection log",
    )
    parser.add_argument("--repo-id", default=REPO_ID)
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--dry-run", action="store_true", help="check everything, upload nothing")
    args = parser.parse_args()

    if not args.i_have_approval:
        print(
            "REFUSED: this script publishes to Hugging Face.\n"
            "Re-run with --i-have-approval once you have reviewed\n"
            "  data/processed/summary.md and data/processed/rejections.jsonl.",
            file=sys.stderr,
        )
        return 2

    repo_root = Path(__file__).resolve().parent.parent
    missing = [local for local, _ in UPLOADS if not (repo_root / local).exists()]
    if missing:
        print(f"FAIL: missing artifacts, run the pipeline first: {missing}", file=sys.stderr)
        return 1

    token = os.environ.get("HF_TOKEN")
    if not token:
        print("FAIL: set HF_TOKEN in the environment (never in a file).", file=sys.stderr)
        return 1

    print(f"About to upload to '{args.repo_id}' as a PRIVATE dataset repository:\n")
    for local, remote in UPLOADS:
        size = (repo_root / local).stat().st_size
        print(f"  {local:<38} -> {remote:<22} {size / 1_000_000:>7.2f} MB")
    print("\nThis script never makes the repository public; you do that in the HF UI.")

    if args.dry_run:
        print("\n--dry-run: nothing uploaded.")
        return 0

    answer = input(f"\nType the repo id '{args.repo_id}' to confirm: ").strip()
    if answer != args.repo_id:
        print("Aborted — confirmation did not match.", file=sys.stderr)
        return 1

    from huggingface_hub import HfApi

    api = HfApi(token=token)
    api.create_repo(repo_id=args.repo_id, repo_type="dataset", private=True, exist_ok=True)
    print(f"\nRepository ready (private): {args.repo_id}")

    for local, remote in UPLOADS:
        api.upload_file(
            path_or_fileobj=str(repo_root / local),
            path_in_repo=remote,
            repo_id=args.repo_id,
            repo_type="dataset",
        )
        print(f"  uploaded {remote}")

    print(
        f"\nDone. Inspect the dataset viewer at "
        f"https://huggingface.co/datasets/{args.repo_id}\n"
        "Make it public yourself once the rows look right."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
