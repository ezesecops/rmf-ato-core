#!/usr/bin/env python3
"""Stage 1 — verify the manifest before anything is fetched.

Checks every entry's structure, probes every URL, and reads the landing pages of
documents whose notes warn a newer revision may have gone final. Prints a report
for human review; changes nothing except the report file.

    python scripts/01_verify_manifest.py [--manifest manifest.json] [--no-network]
                                         [--report data/verification_report.md]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rmf_ato_core.manifest import DocReport, load_manifest, verify  # noqa: E402


def build_report(reports: list[DocReport]) -> str:
    lines: list[str] = []
    lines.append("# Stage 1 — Manifest verification report\n")
    lines.append(f"Documents in manifest: **{len(reports)}**\n")

    lines.append("| doc_id | format | tier | URL status | notes |")
    lines.append("|---|---|---|---|---|")
    for report in reports:
        check = report.url_check
        if check.url is None:
            status = "— (no url)"
        elif check.status is None:
            status = "ERROR"
        else:
            status = f"{check.status} ({check.method})"
        note = check.note or ""
        if check.resolved_url:
            note = f"{note} -> `{check.resolved_url}`"
        lines.append(
            f"| `{report.doc.doc_id}` | {report.doc.format} | {report.doc.tier} | {status} | {note} |"
        )

    checked = [r for r in reports if r.doc.needs_supersession_check]
    lines.append("\n## Supersession checks\n")
    if not checked:
        lines.append("_No documents flagged for supersession checking._")
    else:
        lines.append("| doc_id | manifest revision | finding |")
        lines.append("|---|---|---|")
        for report in checked:
            lines.append(
                f"| `{report.doc.doc_id}` | {report.doc.revision} | {report.supersession.detail} |"
            )

    failures = [r for r in reports if r.url_check.url and not r.url_check.ok]
    lines.append("\n## Verdict\n")
    if failures:
        lines.append(f"**{len(failures)} URL(s) did not resolve - resolve before Stage 2:**\n")
        for report in failures:
            lines.append(
                f"- `{report.doc.doc_id}`: {report.url_check.url} -> {report.url_check.status}"
            )
    else:
        lines.append("All non-null URLs resolved successfully.")

    manual = [r for r in reports if r.doc.format == "pdf-manual"]
    if manual:
        lines.append("\nManual documents (human must place the PDF before Stage 4):\n")
        for report in manual:
            lines.append(
                f"- `{report.doc.doc_id}` -> `data/raw/manual/{report.doc.doc_id}.pdf` "
                f"(source: {report.doc.landing_page})"
            )

    lines.append(
        "\n**HUMAN REVIEW:** approve this report (and any manifest corrections) before Stage 2 fetch."
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default="manifest.json")
    parser.add_argument(
        "--no-network",
        action="store_true",
        help="validate manifest structure only; skip URL and landing-page checks",
    )
    parser.add_argument(
        "--report",
        default="data/verification_report.md",
        help="where to write the markdown report ('-' for stdout only)",
    )
    args = parser.parse_args()

    try:
        manifest = load_manifest(args.manifest)
    except (OSError, ValueError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    print(
        f"Manifest structure OK: {len(manifest.documents)} documents, "
        f"version {manifest.manifest_version}"
    )

    if args.no_network:
        print("--no-network: skipping URL and supersession checks.")
        return 0

    reports = verify(manifest)
    report_text = build_report(reports)
    print()
    print(report_text)

    if args.report != "-":
        out_path = Path(args.report)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(report_text, encoding="utf-8")
        print(f"Report written to {out_path}")

    # Nonzero exit if any URL that should resolve did not - Stage 2 must not run.
    failures = [r for r in reports if r.url_check.url and not r.url_check.ok]
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
