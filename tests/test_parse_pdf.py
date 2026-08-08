"""Stage 4 tests.

These exercise the layout heuristics directly with synthetic lines rather than
real PDFs: the heuristics are where the bugs live, and a unit test that names
the failure ("a bold bullet line is not a heading") is worth more than one that
opens a 10MB publication.
"""

from __future__ import annotations

import json

import pytest

from rmf_ato_core.parse_pdf import (
    Line,
    _starts_a_term,
    extract_term_definitions,
    PdfRowBuilder,
    Section,
    build_sections,
    build_vocabulary,
    clean_heading,
    extract_blocks,
    extract_definitions,
    find_contents_pages,
    find_repeating_furniture,
    heading_level,
    is_furniture,
    join_lines,
    keep_longest_per_key,
    transcribe_impact_table,
)
from rmf_ato_core.parse_playbook import parse_playbook
from tests.test_parse_oscal import make_doc

BODY = 10.0


def line(text: str, page: int = 1, size: float = BODY, bold: bool = False) -> Line:
    return Line(text=text, page=page, size=size, bold=bold, y=0.0)


# --- furniture ---------------------------------------------------------------

def test_running_headers_are_detected_by_repetition():
    lines = [line("FIPS Publication 199   Standards for Security", page=p) for p in range(1, 11)]
    lines += [line("real content on one page only", page=3)]
    repeating = find_repeating_furniture(lines, page_count=10)
    assert is_furniture(lines[0], repeating)
    assert not is_furniture(lines[-1], repeating)


def test_page_numbers_rules_and_toc_leaders_are_furniture():
    for text in ["12", "Page 7", "iv", "______________________", "1 PURPOSE .......... 3"]:
        assert is_furniture(line(text), set()), text


def test_boilerplate_is_furniture():
    assert is_furniture(line("This publication is available free of charge from: x"), set())
    assert is_furniture(line("https://doi.org/10.6028/NIST.SP.800-37r2"), set())


def test_contents_pages_are_detected_early_and_not_late():
    early = [line(f"Chapter {n} Something Useful   {n}", page=2) for n in range(1, 8)]
    late = [line(f"Some table row value   {n}", page=90) for n in range(1, 8)]
    pages = find_contents_pages(early + late, page_count=100)
    assert pages == {2}


# --- heading detection -------------------------------------------------------

def test_numbered_headings_carry_their_depth():
    assert heading_level(line("1 PURPOSE"), BODY) == 1
    assert heading_level(line("3.4 Assess"), BODY) == 2
    assert heading_level(line("3.4.1 Assessment Preparation"), BODY) == 3


def test_keyword_headings():
    assert heading_level(line("CHAPTER THREE"), BODY) == 1
    assert heading_level(line("APPENDIX A TERMS AND DEFINITIONS"), BODY) == 1
    # A task nests inside its chapter rather than replacing it, so that
    # section_path keeps the chapter trail.
    assert heading_level(line("TASK P-1   Identify and assign individuals"), BODY) == 3


def test_footnotes_are_not_headings():
    # Opens with a digit, but runs long and is set smaller than body text.
    footnote = line(
        "1 Information is categorized according to its information type, which is a specific "
        "category of information defined by an organization.",
        size=BODY - 1.5,
    )
    assert heading_level(footnote, BODY) is None


def test_prose_is_not_a_heading():
    assert heading_level(line("This sentence ends with a period."), BODY) is None


def test_bold_bullet_lines_are_not_headings():
    # SP 800-218 sets its bullets in bold; treating them as headings once cost
    # a quarter of that document.
    assert heading_level(line("• Securely Provision (SP): Risk Management", bold=True), BODY) is None


def test_lines_ending_mid_clause_are_not_headings():
    assert heading_level(line("Systems Requirements Planning (SRP), Test and", bold=True), BODY) is None
    assert heading_level(line("Roles and Responsibilities of the", bold=True), BODY) is None
    # …but a word merely ending in those letters is fine.
    assert heading_level(line("Unified Command", bold=True), BODY) == 3


def test_larger_type_makes_an_unnumbered_heading():
    assert heading_level(line("Security Objectives", size=BODY + 2), BODY) == 2


# --- sections ----------------------------------------------------------------

def test_sections_nest_by_heading_depth():
    lines = [
        line("1 PURPOSE"),
        line("Body of purpose."),
        line("1.1 Scope"),
        line("Body of scope."),
        line("2 APPLICABILITY"),
        line("Body of applicability."),
    ]
    sections = build_sections(lines, BODY, set())
    paths = [s.path for s in sections]
    assert paths == ["1 PURPOSE", "1 PURPOSE > 1.1 Scope", "2 APPLICABILITY"]
    assert sections[1].level == 2


def test_clean_heading_collapses_layout_whitespace():
    assert clean_heading("1     PURPOSE") == "1 PURPOSE"


# --- line joining ------------------------------------------------------------

def test_hyphenation_is_resolved_against_the_document_vocabulary():
    vocabulary = build_vocabulary([line("integrated systems"), line("risk-based approach")])
    # "inte-" + "grated": the document uses "integrated", so join.
    assert join_lines(["The characteristics are inte-", "grated into policy."], vocabulary) == (
        "The characteristics are integrated into policy."
    )
    # "risk-" + "based": the document uses "risk-based", so keep the hyphen.
    assert join_lines(["Apply a risk-", "based approach."], vocabulary) == (
        "Apply a risk-based approach."
    )


def test_lines_join_with_a_space_by_default():
    assert join_lines(["first line", "second line"]) == "first line second line"


# --- identifier blocks -------------------------------------------------------

import re  # noqa: E402

TASK_RE = re.compile(r"^(TASK\s+[A-Z]-\d+)")


def make_section(heading: str, body: list[str], level: int = 1, page: int = 1) -> Section:
    return Section(
        heading=heading, trail=[], lines=[line(text, page=page) for text in body],
        page=page, level=level,
    )


def test_blocks_do_not_run_past_a_major_heading():
    sections = [
        make_section("CHAPTER THREE", ["TASK P-1", "Do the first thing.", "More about it."]),
        make_section("APPENDIX A", ["Unrelated appendix text."], level=1),
    ]
    blocks = extract_blocks(sections, TASK_RE)
    assert len(blocks) == 1
    assert "Unrelated appendix text." not in " ".join(blocks[0].lines)


def test_blocks_may_span_minor_headings_when_allowed():
    sections = [
        make_section("CHAPTER THREE", ["TASK P-1", "Do the first thing."]),
        make_section("Categories", ["still part of the task"], level=3),
    ]
    spanning = extract_blocks(sections, TASK_RE, max_span_level=2)
    assert "still part of the task" in " ".join(spanning[0].lines)

    bounded = extract_blocks(sections, TASK_RE)
    assert "still part of the task" not in " ".join(bounded[0].lines)


def test_the_longest_occurrence_of_an_identifier_wins():
    sections = [
        make_section("Summary table", ["TASK P-1", "short stub"]),
        make_section("CHAPTER THREE", ["TASK P-1", "the real, much longer task description here"]),
    ]
    kept, discarded = keep_longest_per_key(extract_blocks(sections, TASK_RE))
    assert len(kept) == 1 and len(discarded) == 1
    assert "much longer" in " ".join(kept[0].lines)


# --- definitions -------------------------------------------------------------

def test_definitions_split_on_terms_and_keep_wrapped_lines():
    section = make_section("APPENDIX A TERMS AND DEFINITIONS", [
        "AVAILABILITY: Ensuring timely and reliable access to and use of",
        "information. [44 U.S.C., SEC. 3542]",
        "CONFIDENTIALITY: Preserving authorized restrictions on information access",
        "and disclosure. [44 U.S.C., SEC. 3542]",
    ])
    builder = PdfRowBuilder(make_doc("FIPS-199", fmt="pdf"), "a" * 64)
    rows = extract_definitions(builder, section)
    assert [row.id.rsplit("/", 1)[-1] for row in rows] == ["availability", "confidentiality"]
    assert rows[0].text.startswith("AVAILABILITY: Ensuring timely")
    assert "[44 U.S.C., SEC. 3542]" in rows[0].text
    assert all(row.chunk_type == "definition" for row in rows)


# --- tables ------------------------------------------------------------------

def test_impact_table_is_transcribed_cell_by_cell():
    grid = [
        ["", "POTENTIAL IMPACT", "", ""],
        ["Security Objective", "LOW", "MODERATE", "HIGH"],
        ["Confidentiality Preserving authorized restrictions.",
         "limited adverse effect", "serious adverse effect", "severe adverse effect"],
        ["Integrity Guarding against improper modification.",
         "limited adverse effect", "serious adverse effect", "severe adverse effect"],
        ["Availability Ensuring timely and reliable access.",
         "limited adverse effect", "serious adverse effect", "severe adverse effect"],
    ]
    transcript = transcribe_impact_table(grid)
    assert "Confidentiality / LOW: limited adverse effect" in transcript
    assert "Availability / HIGH: severe adverse effect" in transcript
    assert "Integrity (definition): Guarding against improper modification" in transcript


def test_impact_table_returns_none_when_the_grid_is_unusable():
    assert transcribe_impact_table([["nothing", "useful"]]) is None


# --- the builder -------------------------------------------------------------

def test_pdf_rows_never_carry_a_control_id():
    builder = PdfRowBuilder(make_doc("SP-800-37r2", fmt="pdf"), "a" * 64)
    row = builder.row("section", "3.4 Assess", "Implement AC-2 and RA-5 as described." * 10, "3 > 3.4")
    assert row.control_id is None


def test_duplicate_headings_get_distinct_ids():
    builder = PdfRowBuilder(make_doc("SP-800-39", fmt="pdf"), "a" * 64)
    first = builder.row("section", "Introduction", "x" * 300, "A > Introduction")
    second = builder.row("section", "Introduction", "y" * 300, "B > Introduction")
    assert first.id != second.id
    assert second.id.endswith("-2")


# --- the Playbook ------------------------------------------------------------

def test_playbook_records_become_subcategory_rows(tmp_path):
    records = [
        {
            "type": "Govern",
            "title": "GOVERN 1.1",
            "description": "Legal and regulatory requirements involving AI are understood.",
            "section_about": "About text. " * 20,
            "section_actions": "Suggested actions text. " * 10,
            "section_doc": "",
            "section_ref": "A reference list.",
        },
        {"title": "", "description": "no title"},
        {"title": "GOVERN 1.1", "description": "duplicate"},
        {"title": "MAP 9.9", "description": "too short"},
    ]
    path = tmp_path / "playbook.json"
    path.write_text(json.dumps(records), encoding="utf-8")

    doc = make_doc("AI-RMF-PLAYBOOK", fmt="web", url="https://airc.nist.gov/docs/playbook.json")
    rows, rejections = parse_playbook(path, doc, "a" * 64)

    assert len(rows) == 1
    row = rows[0]
    assert row.id == "AI-RMF-PLAYBOOK/ai_rmf_subcategory/govern-1.1"
    assert row.chunk_type == "ai_rmf_subcategory"
    assert row.control_id is None
    assert row.section_path == "AI RMF Playbook > Govern > GOVERN 1.1"
    assert row.text.startswith("GOVERN 1.1: Legal and regulatory requirements")
    assert "About:" in row.text and "Suggested Actions:" in row.text
    # An absent field contributes no empty heading.
    assert "Transparency and Documentation:" not in row.text

    assert {r.rule for r in rejections} == {
        "missing_title", "duplicate_subcategory", "playbook_entry_too_short",
    }


# --- drop caps ---------------------------------------------------------------

def test_a_single_character_line_is_never_a_heading():
    # NIST chapters open with a large decorative drop cap, set bigger than any
    # real heading; as a heading it split the chapter and left a one-letter
    # section_path segment.
    assert heading_level(line("O", size=47.5), BODY) is None
    assert heading_level(line("T", size=47.5, bold=True), BODY) is None
    # Two characters can still be a heading.
    assert heading_level(line("AC", size=BODY + 2), BODY) == 2


def test_a_drop_cap_becomes_body_text_of_its_chapter():
    lines = [
        line("CHAPTER TWO"),
        line("O", size=47.5),
        line("rganizations can make effective use of their security budgets."),
    ]
    sections = build_sections(lines, BODY, set())
    assert [s.path for s in sections] == ["CHAPTER TWO"]
    assert "rganizations can make" in sections[0].body


# --- glossary term detection -------------------------------------------------

def test_a_bold_line_at_body_size_starts_a_term():
    # SP 800-37's convention.
    assert _starts_a_term(line("assurance", bold=True), line("Grounds for confidence."), BODY)
    assert not _starts_a_term(line("Grounds for confidence."), None, BODY)


def test_a_line_followed_by_a_smaller_source_line_starts_a_term():
    # SP 800-137's convention: terms are not bold, but each entry is marked by a
    # smaller "[SOURCE]" line beneath it.
    assert _starts_a_term(line("Allocation"), line("[NISTIR 7298]", size=BODY - 1.2), BODY)
    # The same line without the source beneath it is just prose.
    assert not _starts_a_term(line("Allocation"), line("more prose here"), BODY)
    # A source line at body size is not a source marker.
    assert not _starts_a_term(line("Allocation"), line("[NISTIR 7298]"), BODY)


def test_glossary_extraction_handles_both_conventions():
    lines = [
        # SP 800-137 shape: term, smaller source, definition.
        line("Allocation", page=2),
        line("[NISTIR 7298]", page=2, size=BODY - 1.2),
        line("The process an organization employs to determine whether security "
             "controls are defined as system-specific, hybrid, or common.", page=2),
        # SP 800-37 shape: bold term, definition.
        line("audit trail", page=2, bold=True),
        line("A chronological record that reconstructs and examines the sequence "
             "of activities surrounding a security-relevant transaction.", page=2),
    ]
    builder = PdfRowBuilder(make_doc("SP-800-137", fmt="pdf"), "a" * 64)
    rows = extract_term_definitions(builder, lines, BODY, (1, 3), "APPENDIX > GLOSSARY",
                                    set(), frozenset())
    assert [r.id.rsplit("/", 1)[-1] for r in rows] == ["allocation", "audit-trail"]
    assert rows[0].text.startswith("Allocation: [NISTIR 7298] The process")
    assert all(r.chunk_type == "definition" for r in rows)
    assert all(r.control_id is None for r in rows)
