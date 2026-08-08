"""Stage 6 — validation.

This stage treats the dataset like a supply-chain artifact: every row must
prove where it came from and what it is, and anything that cannot is rejected
with a named rule and a reason. Rule 3 is the one that matters most — it is
what makes a fabricated control ID like "HA-25" impossible to publish.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass

from .fetch import ProvenanceEntry
from .manifest import Manifest
from .parse_oscal import Rejection
from .schema import (
    CHUNK_TYPES,
    CONTROL_ID_RE,
    FIELD_ORDER,
    MAX_TEXT_LEN,
    Row,
    min_text_len,
)

# Formats whose rows may legitimately carry a control_id: the catalog itself and
# the assessment content embedded inside it. Both are OSCAL structured data.
OSCAL_FORMATS = {"oscal", "embedded-in-oscal"}

TEMPLATE_RESIDUE = ("{{", "}}", "insert: param", "You are a", "As an AI")

FURNITURE_PATTERNS = (
    re.compile(r"^NIST SP 800-\d+.*Page \d+", re.IGNORECASE),
    re.compile(r"^\s*\d+\s*$"),
    re.compile(r"this publication is available free of charge", re.IGNORECASE),
)

# Expected-count ranges. Out-of-range is a warning, not a rejection: it means a
# parser probably drifted, which is a question for the maintainer.
EXPECTED_COUNTS = {
    "sp_800_53_control_rows": (900, 1200),
    "sp_800_53_families": (20, 20),
    "ai_rmf_subcategories": (60, 80),
    "ssdf_practices": (40, None),
}


@dataclass
class ValidationReport:
    valid: list[Row]
    rejections: list[Rejection]
    warnings: list[str]
    counts_by_doc: Counter
    counts_by_type: Counter


def _has_control_chars(text: str) -> bool:
    return any(
        char not in "\n\t" and unicodedata.category(char)[0] == "C" for char in text
    )


def validate_rows(
    rows: list[Row],
    manifest: Manifest,
    provenance: dict[str, ProvenanceEntry],
) -> ValidationReport:
    valid: list[Row] = []
    rejections: list[Rejection] = []
    seen_ids: set[str] = set()
    seen_text: dict[str, str] = {}

    # Every hash we actually retrieved. A row may cite the artifact it was
    # derived from (SP 800-53A rows cite the 800-53 catalog they live inside),
    # so membership in this set is the check, not a per-doc equality.
    known_hashes = {
        entry.sha256 for entry in provenance.values() if entry.sha256
    }

    def reject(row: Row, rule: str, detail: str) -> None:
        rejections.append(
            Rejection(doc_id=row.doc_id, ref=row.id, rule=rule, detail=detail, stage="validate")
        )

    for row in rows:
        # 1. schema_valid
        missing = [name for name in FIELD_ORDER if getattr(row, name, None) is None
                   and name not in {"control_id", "section_path"}]
        if missing:
            reject(row, "schema_valid", f"missing values: {missing}")
            continue
        if row.chunk_type not in CHUNK_TYPES:
            reject(row, "schema_valid", f"chunk_type {row.chunk_type!r} not in enum")
            continue
        if not isinstance(row.tier, int) or row.tier not in (1, 2):
            reject(row, "schema_valid", f"tier {row.tier!r} is not 1 or 2")
            continue

        # 2. doc_in_manifest
        document = manifest.by_id(row.doc_id)
        if document is None:
            reject(row, "doc_in_manifest", f"doc_id {row.doc_id!r} is not in the manifest")
            continue
        if row.sha256_source not in known_hashes:
            reject(row, "doc_in_manifest",
                   f"sha256_source {row.sha256_source[:12]}… matches no retrieved artifact")
            continue

        # 3. control_family_whitelist — the rule that makes "HA-25" impossible
        if row.control_id is not None and not CONTROL_ID_RE.match(row.control_id):
            reject(row, "control_family_whitelist",
                   f"control_id {row.control_id!r} is not a real SP 800-53r5 family identifier")
            continue

        # 4. control_id_source
        if row.control_id is not None and document.format not in OSCAL_FORMATS:
            reject(row, "control_id_source",
                   f"control_id present but {row.doc_id} is format {document.format!r}, not OSCAL")
            continue

        # 5. length_bounds
        floor = min_text_len(row.chunk_type)
        if not floor <= len(row.text) <= MAX_TEXT_LEN:
            reject(row, "length_bounds",
                   f"{len(row.text)} chars outside [{floor}, {MAX_TEXT_LEN}] for {row.chunk_type}")
            continue

        # 6. no_template_residue
        residue = [marker for marker in TEMPLATE_RESIDUE if marker in row.text]
        if residue:
            reject(row, "no_template_residue", f"text contains {residue}")
            continue

        # 7. no_furniture
        if any(pattern.search(row.text) for pattern in FURNITURE_PATTERNS):
            reject(row, "no_furniture", "text matches a header/footer/page-number pattern")
            continue

        # 8. unique_id and near_dupe
        if row.id in seen_ids:
            reject(row, "unique_id", "duplicate row id")
            continue
        fingerprint = re.sub(r"\s+", " ", row.text).strip().lower()
        if fingerprint in seen_text:
            reject(row, "near_dupe", f"text identical to {seen_text[fingerprint]}")
            continue

        # 9. encoding_clean
        if "�" in row.text or _has_control_chars(row.text):
            reject(row, "encoding_clean", "replacement or control characters in text")
            continue
        try:
            row.text.encode("utf-8")
        except UnicodeEncodeError as exc:
            reject(row, "encoding_clean", f"not encodable as UTF-8: {exc}")
            continue

        seen_ids.add(row.id)
        seen_text[fingerprint] = row.id
        valid.append(row)

    warnings = check_expected_counts(valid)
    return ValidationReport(
        valid=valid,
        rejections=rejections,
        warnings=warnings,
        counts_by_doc=Counter(row.doc_id for row in valid),
        counts_by_type=Counter(row.chunk_type for row in valid),
    )


def check_expected_counts(rows: list[Row]) -> list[str]:
    """Rule 10 — counts drifting out of range means a parser bug, not a reject."""
    warnings: list[str] = []

    control_rows = sum(
        1 for row in rows if row.chunk_type in {"control", "control_enhancement"}
    )
    families = {
        row.control_id.split("-")[0]
        for row in rows if row.control_id and row.chunk_type == "control"
    }
    practices = sum(1 for row in rows if row.chunk_type == "ssdf_practice")

    # Counted per document and per distinct identifier: two documents cover the
    # AI RMF Core (the framework and the Playbook), and splitting an oversized
    # entry adds "(part n)" rows that are not extra subcategories.
    subcategories_by_doc: dict[str, set[str]] = {}
    for row in rows:
        if row.chunk_type == "ai_rmf_subcategory":
            base = row.id.split(" (part ")[0]
            subcategories_by_doc.setdefault(row.doc_id, set()).add(base)

    def check(label: str, value: int, bounds: tuple[int, int | None]) -> None:
        low, high = bounds
        if value < low or (high is not None and value > high):
            warnings.append(
                f"{label}: {value} outside expected range "
                f"[{low}, {high if high is not None else '∞'}] — check the parser"
            )

    check("SP 800-53 control + enhancement rows", control_rows,
          EXPECTED_COUNTS["sp_800_53_control_rows"])
    check("SP 800-53 families represented", len(families), EXPECTED_COUNTS["sp_800_53_families"])
    for doc_id, identifiers in sorted(subcategories_by_doc.items()):
        check(f"AI RMF subcategories in {doc_id}", len(identifiers),
              EXPECTED_COUNTS["ai_rmf_subcategories"])
    check("SSDF practice rows", practices, EXPECTED_COUNTS["ssdf_practices"])
    return warnings


def summary_markdown(
    report: ValidationReport,
    all_rejections: list[Rejection],
    total_bytes: int,
) -> str:
    lines = ["# Dataset summary\n"]
    lines.append(f"- Rows published: **{len(report.valid):,}**")
    lines.append(f"- Rows rejected across all stages: **{len(all_rejections):,}**")
    lines.append(f"- Total text: **{total_bytes / 1_000_000:.1f} MB**\n")

    lines.append("## Rows by document\n")
    lines.append("| doc_id | rows |")
    lines.append("|---|---:|")
    for doc_id, count in sorted(report.counts_by_doc.items()):
        lines.append(f"| `{doc_id}` | {count:,} |")

    lines.append("\n## Rows by chunk type\n")
    lines.append("| chunk_type | rows |")
    lines.append("|---|---:|")
    for chunk_type, count in sorted(report.counts_by_type.items()):
        lines.append(f"| `{chunk_type}` | {count:,} |")

    lines.append("\n## Rejections by rule\n")
    lines.append("| stage | rule | count |")
    lines.append("|---|---|---:|")
    by_rule = Counter((r.stage, r.rule) for r in all_rejections)
    for (stage, rule), count in sorted(by_rule.items()):
        lines.append(f"| {stage} | `{rule}` | {count:,} |")

    if report.warnings:
        lines.append("\n## Expected-count warnings\n")
        for warning in report.warnings:
            lines.append(f"- {warning}")
    else:
        lines.append("\n## Expected-count warnings\n\nNone — all counts in range.")
    return "\n".join(lines) + "\n"
