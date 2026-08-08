"""Stage 4 (companion) — parse the AI RMF Playbook.

The Playbook has no canonical PDF; its page is a JavaScript shell. The site
publishes the underlying records as JSON, so this reads that directly: one row
per subcategory, structurally clean, with no HTML scraping anywhere.
"""

from __future__ import annotations

import json
from pathlib import Path

from .manifest import Document
from .parse_oscal import Rejection
from .schema import Row, make_id, normalize_text

# Record field -> the heading it gets in the row text. Order is the order the
# site presents them in.
SECTION_LABELS = (
    ("section_about", "About"),
    ("section_actions", "Suggested Actions"),
    ("section_doc", "Transparency and Documentation"),
    ("section_ref", "References"),
)


def parse_playbook(
    json_path: Path, doc: Document, sha256_source: str
) -> tuple[list[Row], list[Rejection]]:
    records = json.loads(Path(json_path).read_text(encoding="utf-8"))
    rows: list[Row] = []
    rejections: list[Rejection] = []
    seen: set[str] = set()

    for index, record in enumerate(records):
        title = (record.get("title") or "").strip()
        if not title:
            rejections.append(
                Rejection(doc.doc_id, f"record[{index}]", "missing_title",
                          "record has no title", stage="parse_playbook")
            )
            continue
        if title in seen:
            rejections.append(
                Rejection(doc.doc_id, title, "duplicate_subcategory",
                          "title already emitted", stage="parse_playbook")
            )
            continue
        seen.add(title)

        parts = [f"{title}: {(record.get('description') or '').strip()}".strip(": ")]
        for field, label in SECTION_LABELS:
            value = (record.get(field) or "").strip()
            if value:
                parts.append(f"{label}:\n{value}")
        text = normalize_text("\n\n".join(parts))

        if len(text) < 200:
            rejections.append(
                Rejection(doc.doc_id, title, "playbook_entry_too_short",
                          f"{len(text)} chars", stage="parse_playbook")
            )
            continue

        function = (record.get("type") or title.split()[0]).strip()
        rows.append(
            Row(
                id=make_id(doc.doc_id, "ai_rmf_subcategory", title),
                text=text,
                doc_id=doc.doc_id,
                doc_title=doc.title,
                revision=doc.revision,
                pub_date=doc.pub_date,
                tier=doc.tier,
                chunk_type="ai_rmf_subcategory",
                # Playbook guidance is AI governance, not SP 800-53 controls.
                control_id=None,
                section_path=f"AI RMF Playbook > {function} > {title}",
                source_url=doc.effective_source_url,
                sha256_source=sha256_source,
            )
        )
    return rows, rejections
