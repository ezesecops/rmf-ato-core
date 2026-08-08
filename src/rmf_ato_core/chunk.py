"""Stage 5 — chunk normalization.

OSCAL rows and per-unit PDF rows (tasks, practices, subcategories, definitions)
arrive already right-sized, because their size is the source's own idea of a
unit. This stage fixes the two ways a row can be the wrong size anyway:

- too long: split at paragraph boundaries, with one paragraph of overlap so a
  sentence spanning the cut is still retrievable from both halves;
- too short: merge a stub section into the section that follows it, or, if
  nothing follows, drop and log it as trailing furniture.
"""

from __future__ import annotations

import re
from dataclasses import replace

from .parse_oscal import Rejection
from .schema import (
    MAX_TEXT_LEN,
    Row,
    is_reference_entry,
    min_text_len,
    normalize_text,
    strip_reference_tag,
    strip_running_header,
)

# Parts are cut to this, below the hard ceiling, so that adding the overlap
# paragraph cannot push a part back over the limit.
TARGET_PART_LEN = 6000


def paragraphs(text: str) -> list[str]:
    return [part.strip() for part in text.split("\n\n") if part.strip()]


def _hard_split(paragraph: str, limit: int) -> list[str]:
    """Last resort for a single paragraph longer than a whole part.

    Splits between sentences where possible so the pieces still read.
    """
    sentences = re.split(r"(?<=[.;:])\s+", paragraph)
    pieces: list[str] = []
    current = ""
    for sentence in sentences:
        while len(sentence) > limit:
            # A "sentence" this long is a run-on list; cut it at a space.
            cut = sentence.rfind(" ", 0, limit) or limit
            pieces.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if not current:
            current = sentence
        elif len(current) + 1 + len(sentence) <= limit:
            current = f"{current} {sentence}"
        else:
            pieces.append(current)
            current = sentence
    if current:
        pieces.append(current)
    return pieces


def split_text(text: str, target: int = TARGET_PART_LEN) -> list[str]:
    """Split at paragraph boundaries into parts of at most `target` chars,
    repeating the previous part's last paragraph as overlap."""
    blocks: list[str] = []
    for paragraph in paragraphs(text):
        blocks.extend(_hard_split(paragraph, target) if len(paragraph) > target else [paragraph])

    parts: list[list[str]] = []
    current: list[str] = []
    length = 0
    added_since_split = False
    for block in blocks:
        addition = len(block) + (2 if current else 0)
        if current and length + addition > target:
            parts.append(current)
            # One paragraph of overlap, unless it would fill the next part.
            tail = current[-1]
            current = [tail] if len(tail) < target // 2 else []
            length = len(tail) if current else 0
            added_since_split = False
            addition = len(block) + (2 if current else 0)
        current.append(block)
        length += addition
        added_since_split = True
    # A final part holding nothing but the overlap paragraph would be a pure
    # duplicate of the previous part's tail.
    if current and (added_since_split or not parts):
        parts.append(current)

    return _balance(["\n\n".join(part) for part in parts])


def _balance(parts: list[str]) -> list[str]:
    """Fold an undersized part into its neighbour.

    A heading that lands alone before a long block would otherwise be published
    as "(part 1)" containing just the heading — technically a part, useless as a
    chunk.
    """
    if len(parts) < 2:
        return parts
    minimum = TARGET_PART_LEN // 8
    balanced: list[str] = []
    for part in parts:
        if balanced and len(part) < minimum:
            balanced[-1] = f"{balanced[-1]}\n\n{part}"
        else:
            balanced.append(part)
    # A short first part has no previous neighbour, so it folds forward.
    if len(balanced) > 1 and len(balanced[0]) < minimum:
        balanced[1] = f"{balanced[0]}\n\n{balanced[1]}"
        balanced.pop(0)
    return balanced


def split_row(row: Row, max_len: int = MAX_TEXT_LEN) -> list[Row]:
    """Split an oversized row, numbering the parts in id and section_path."""
    if len(row.text) <= max_len:
        return [row]
    pieces = split_text(row.text)
    if len(pieces) == 1:
        return [row]
    return [
        replace(
            row,
            id=f"{row.id} (part {number})",
            text=piece,
            section_path=(
                f"{row.section_path} (part {number})" if row.section_path else None
            ),
        )
        for number, piece in enumerate(pieces, start=1)
    ]


def merge_short_sections(rows: list[Row]) -> tuple[list[Row], list[Rejection]]:
    """Fold a too-short section into the next section of the same document.

    Only `section` rows merge: every other type is an identifier-keyed unit
    whose boundaries come from the source, not from page layout.
    """
    merged: list[Row] = []
    rejections: list[Rejection] = []
    pending: list[Row] = []

    for index, row in enumerate(rows):
        is_short_section = (
            row.chunk_type == "section" and len(row.text) < min_text_len(row.chunk_type)
        )
        if not is_short_section:
            if pending:
                # Attach the stubs only if the next row belongs to the same
                # document and section family; otherwise they are trailing.
                if row.doc_id == pending[0].doc_id and row.chunk_type == "section":
                    row = replace(
                        row,
                        text=normalize_text(
                            "\n\n".join([*(stub.text for stub in pending), row.text])
                        ),
                    )
                    pending = []
                else:
                    rejections.extend(_trailing(stub) for stub in pending)
                    pending = []
            merged.append(row)
            continue

        next_row = rows[index + 1] if index + 1 < len(rows) else None
        if next_row is None or next_row.doc_id != row.doc_id or next_row.chunk_type != "section":
            rejections.append(_trailing(row))
            continue
        pending.append(row)

    rejections.extend(_trailing(stub) for stub in pending)
    return merged, rejections


def _trailing(row: Row) -> Rejection:
    return Rejection(
        doc_id=row.doc_id,
        ref=row.id,
        rule="trailing_furniture",
        detail=f"{len(row.text)} chars, no following sibling section to merge into",
        stage="chunk",
    )


def strip_leading_furniture(row: Row) -> Row:
    """Remove a running-header remnant or a leading citation tag from a row that
    has real content underneath it.

    A row is only cleaned when what remains is still substantive. A row that is
    *nothing but* furniture, or that is genuinely a reference-list entry, is
    left exactly as it is so that Stage 6 can reject it under the rule that
    names the problem rather than a generic length failure.
    """
    floor = min_text_len(row.chunk_type)
    text = row.text

    cleaned = strip_running_header(text)
    if cleaned != text and len(cleaned) >= floor:
        text = cleaned

    if not is_reference_entry(text):
        untagged = strip_reference_tag(text)
        if untagged != text and len(untagged) >= floor:
            text = untagged

    if text == row.text:
        return row

    section_path = row.section_path
    if section_path:
        # Removing a leading segment leaves its separator behind.
        stripped_path = strip_running_header(section_path).lstrip("> ").strip()
        section_path = stripped_path or section_path

    return replace(row, text=text, section_path=section_path)


def chunk_rows(rows: list[Row]) -> tuple[list[Row], list[Rejection]]:
    """Run the whole stage: normalize, strip furniture, merge stubs, split."""
    normalized = [
        strip_leading_furniture(replace(row, text=normalize_text(row.text)))
        for row in rows
    ]
    merged, rejections = merge_short_sections(normalized)

    final: list[Row] = []
    for row in merged:
        final.extend(split_row(row))
    return final, rejections
