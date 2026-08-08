"""Stage 5 tests — split and merge boundary behaviour."""

from __future__ import annotations

from dataclasses import replace

from rmf_ato_core.chunk import (
    TARGET_PART_LEN,
    chunk_rows,
    merge_short_sections,
    split_row,
    split_text,
)
from rmf_ato_core.schema import MAX_TEXT_LEN
from tests.test_schema import make_row


def section(text: str, doc_id: str = "SP-800-39", row_id: str = "SP-800-39/section/x"):
    return make_row(id=row_id, doc_id=doc_id, chunk_type="section", control_id=None, text=text)


# --- splitting ---------------------------------------------------------------

def test_short_rows_are_untouched():
    row = section("x" * 500)
    assert split_row(row) == [row]


def test_oversized_rows_split_at_paragraph_boundaries():
    paragraph = "Sentence about risk management. " * 40  # ~1,280 chars
    row = section("\n\n".join([paragraph] * 10))          # ~12,800 chars
    parts = split_row(row)

    assert len(parts) > 1
    assert all(len(part.text) <= MAX_TEXT_LEN for part in parts)
    assert [part.id for part in parts] == [
        f"{row.id} (part {n})" for n in range(1, len(parts) + 1)
    ]
    assert parts[0].section_path.endswith("(part 1)")
    # Splitting must not invent or lose paragraphs.
    assert parts[0].text.startswith(paragraph.strip()[:40])


def test_split_parts_overlap_by_one_paragraph():
    paragraphs = [f"Paragraph {n}. " + "filler words here. " * 60 for n in range(8)]
    parts = split_text("\n\n".join(paragraphs))
    assert len(parts) > 1
    tail_of_first = parts[0].split("\n\n")[-1]
    assert parts[1].startswith(tail_of_first)


def test_a_single_huge_paragraph_still_splits():
    parts = split_text("word " * 4000)  # 20,000 chars, no paragraph breaks
    assert len(parts) > 1
    assert all(len(part) <= TARGET_PART_LEN for part in parts)


def test_no_part_is_left_undersized():
    # A short heading paragraph followed by a very long one used to produce a
    # "(part 1)" containing only the heading.
    text = "2.1 COMPONENTS OF RISK MANAGEMENT\n\n" + ("body text here. " * 700)
    parts = split_text(text)
    assert min(len(part) for part in parts) > TARGET_PART_LEN // 8
    assert parts[0].startswith("2.1 COMPONENTS OF RISK MANAGEMENT")


# --- merging -----------------------------------------------------------------

def test_short_section_merges_into_the_following_sibling():
    rows = [
        section("A stub heading.", row_id="doc/section/stub"),
        section("Real content. " * 40, row_id="doc/section/real"),
    ]
    merged, rejections = merge_short_sections(rows)
    assert len(merged) == 1
    assert merged[0].id == "doc/section/real"
    assert merged[0].text.startswith("A stub heading.")
    assert not rejections


def test_consecutive_stubs_all_merge_forward():
    rows = [
        section("Stub one.", row_id="doc/section/1"),
        section("Stub two.", row_id="doc/section/2"),
        section("Real content. " * 40, row_id="doc/section/3"),
    ]
    merged, rejections = merge_short_sections(rows)
    assert len(merged) == 1
    assert "Stub one." in merged[0].text and "Stub two." in merged[0].text
    assert not rejections


def test_trailing_stub_is_dropped_and_logged():
    rows = [
        section("Real content. " * 40, row_id="doc/section/1"),
        section("Trailing furniture.", row_id="doc/section/2"),
    ]
    merged, rejections = merge_short_sections(rows)
    assert [row.id for row in merged] == ["doc/section/1"]
    assert [r.rule for r in rejections] == ["trailing_furniture"]
    assert rejections[0].stage == "chunk"


def test_a_stub_never_merges_across_documents():
    rows = [
        section("Trailing stub.", doc_id="SP-800-39", row_id="SP-800-39/section/last"),
        section("Other doc. " * 40, doc_id="FIPS-199", row_id="FIPS-199/section/first"),
    ]
    merged, rejections = merge_short_sections(rows)
    assert [row.id for row in merged] == ["FIPS-199/section/first"]
    assert [r.rule for r in rejections] == ["trailing_furniture"]


def test_only_sections_merge():
    # A short control is complete, not a stub, and must survive untouched.
    short_control = make_row(text="IR-5 Incident Monitoring\n\nTrack and document incidents.")
    rows = [short_control, section("Real content. " * 40)]
    merged, rejections = merge_short_sections(rows)
    assert len(merged) == 2
    assert not rejections


# --- the whole stage ---------------------------------------------------------

def test_chunk_rows_normalizes_splits_and_merges_together():
    rows = [
        section("Stub.", row_id="doc/section/stub"),
        section("Body   with    odd   spacing. " * 30, row_id="doc/section/body"),
        section("\n\n".join(["Long paragraph. " * 80] * 8), row_id="doc/section/long"),
    ]
    chunked, rejections = chunk_rows(rows)

    assert not rejections
    assert "   " not in chunked[0].text          # whitespace normalized
    assert chunked[0].text.startswith("Stub.")   # stub merged forward
    assert any(" (part " in row.id for row in chunked)
    assert all(len(row.text) <= MAX_TEXT_LEN for row in chunked)


def test_chunking_preserves_provenance_fields():
    row = section("\n\n".join(["Long paragraph. " * 80] * 8))
    parts = split_row(row)
    for part in parts:
        assert part.sha256_source == row.sha256_source
        assert part.doc_id == row.doc_id
        assert part.source_url == row.source_url
        assert part.chunk_type == row.chunk_type
