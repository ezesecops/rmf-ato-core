"""Stage 6 tests — every rule fires on a crafted bad row and passes a good one."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from rmf_ato_core.fetch import ProvenanceEntry
from rmf_ato_core.manifest import load_manifest
from rmf_ato_core.chunk import chunk_rows
from rmf_ato_core.validate import (
    check_expected_counts,
    summary_markdown,
    validate_rows,
)
from conftest import make_row

MANIFEST = load_manifest(Path(__file__).resolve().parent.parent / "manifest.json")
GOOD_SHA = "a" * 64

PROVENANCE = {
    "SP-800-53r5": ProvenanceEntry(GOOD_SHA, 10, "2026-08-08T00:00:00+00:00",
                                   "https://example.gov/c.json", 200, "download"),
    "FIPS-199": ProvenanceEntry("b" * 64, 10, "2026-08-08T00:00:00+00:00",
                                "https://example.gov/f.pdf", 200, "download"),
}


def good_row(**overrides):
    base = make_row(text="A" * 400, sha256_source=GOOD_SHA)
    return replace(base, **overrides) if overrides else base


def run(*rows):
    return validate_rows(list(rows), MANIFEST, PROVENANCE)


def rules(report):
    return [rejection.rule for rejection in report.rejections]


# --- the good row ------------------------------------------------------------

def test_a_good_row_passes_every_rule():
    report = run(good_row())
    assert not report.rejections
    assert len(report.valid) == 1


# --- rule 1: schema_valid ----------------------------------------------------

def test_unknown_chunk_type_is_rejected():
    assert rules(run(good_row(chunk_type="embedding"))) == ["schema_valid"]


def test_bad_tier_is_rejected():
    assert rules(run(good_row(tier=3))) == ["schema_valid"]


# --- rule 2: doc_in_manifest -------------------------------------------------

def test_doc_id_outside_the_manifest_is_rejected():
    assert rules(run(good_row(doc_id="SP-800-53r9"))) == ["doc_in_manifest"]


def test_hash_matching_no_retrieved_artifact_is_rejected():
    assert rules(run(good_row(sha256_source="f" * 64))) == ["doc_in_manifest"]


def test_assessment_rows_may_cite_the_artifact_they_were_embedded_in():
    # SP 800-53A content lives inside the 800-53 catalog, so it carries that
    # artifact's hash rather than one of its own.
    row = good_row(
        id="SP-800-53Ar5/assessment_objective/ac-2",
        doc_id="SP-800-53Ar5",
        chunk_type="assessment_objective",
        sha256_source=GOOD_SHA,
    )
    assert not run(row).rejections


# --- rule 3: control_family_whitelist ---------------------------------------

@pytest.mark.parametrize("control_id", ["ha-25", "we-12", "am-6", "AC-2", "ac2"])
def test_fabricated_control_ids_are_rejected(control_id):
    assert rules(run(good_row(control_id=control_id))) == ["control_family_whitelist"]


# --- rule 4: control_id_source ----------------------------------------------

def test_a_pdf_derived_row_may_not_carry_a_control_id():
    row = good_row(
        id="FIPS-199/section/1-purpose", doc_id="FIPS-199", chunk_type="section",
        control_id="ac-2", sha256_source="b" * 64,
    )
    assert rules(run(row)) == ["control_id_source"]


# --- rule 5: length_bounds ---------------------------------------------------

def test_text_outside_the_length_bounds_is_rejected():
    assert rules(run(good_row(text="short"))) == ["length_bounds"]
    assert rules(run(good_row(text="A" * 9000))) == ["length_bounds"]


def test_structured_rows_use_the_lower_floor():
    # 100 chars: too short for a section, complete for a control.
    assert not run(good_row(text="A" * 100)).rejections
    section_row = good_row(
        id="FIPS-199/section/x", doc_id="FIPS-199", chunk_type="section",
        control_id=None, text="A" * 100, sha256_source="b" * 64,
    )
    assert rules(run(section_row)) == ["length_bounds"]


# --- rule 6: no_template_residue --------------------------------------------

@pytest.mark.parametrize(
    "text",
    [
        "Require {{ insert: param, ac-02_odp.01 }} for membership. " + "A" * 300,
        "You are a cybersecurity expert. " + "A" * 300,
        "As an AI language model, " + "A" * 300,
    ],
)
def test_template_and_prompt_residue_is_rejected(text):
    assert rules(run(good_row(text=text))) == ["no_template_residue"]


# --- rule 7: no_furniture ----------------------------------------------------

def test_furniture_text_is_rejected():
    row = good_row(text="NIST SP 800-37, REVISION 2 Page 42 " + "A" * 300)
    assert rules(run(row)) == ["no_furniture"]

    boilerplate = good_row(
        text="This publication is available free of charge from: https://x " + "A" * 300
    )
    assert rules(run(boilerplate)) == ["no_furniture"]


# --- rule 9: bibliography_entry ---------------------------------------------

@pytest.mark.parametrize(
    "text",
    [
        "[44USC3502] Title 44 U.S. Code, Sec. 3502, Definitions. 2017 ed. Available at "
        "https://www.govinfo.gov/app/details/USCODE. " + "A" * 200,
        "[EO14028] Executive Order 14028 (2021) Improving the Nation's Cybersecurity. "
        "(The White House, Washington, DC). " + "A" * 200,
        'Ross, R. (2018). "Risk Management Framework for Information Systems." ' + "A" * 200,
    ],
)
def test_reference_list_entries_are_rejected(text):
    row = good_row(id="FIPS-199/section/appendix-b-references", doc_id="FIPS-199",
                   chunk_type="section", control_id=None, sha256_source="b" * 64, text=text)
    assert rules(run(row)) == ["bibliography_entry"]


def test_guidance_behind_a_citation_tag_is_not_a_bibliography_entry():
    # "[SP800-37] • Disposal: The system is no longer authorized…" is guidance
    # wearing a tag; Stage 5 strips the tag and the row is published.
    row = good_row(
        id="SP-800-18r2/section/disposal", doc_id="SP-800-18r2", chunk_type="section",
        control_id=None, sha256_source="b" * 64,
        text="[SP800-37] Disposal: The system is no longer authorized or operational. "
             "Organizations may use other operational statuses as needed. " + "A" * 200,
    )
    assert not run(row).rejections


def test_a_bracketed_citation_inside_a_definition_is_not_a_bibliography_entry():
    # SP 800-37's glossary prints the source after the term, not at the start.
    row = good_row(
        id="SP-800-37r2/definition/assurance", doc_id="SP-800-37r2",
        chunk_type="definition", control_id=None, sha256_source="b" * 64,
        text="assurance: [ISO 15026, Adapted] Grounds for justified confidence that a "
             "claim has been or will be achieved. " + "A" * 100,
    )
    assert not run(row).rejections


# --- rule 5: running_header_fragment ----------------------------------------

def test_a_row_that_is_only_a_running_header_is_rejected():
    row = good_row(
        id="SP-800-37r2/section/appendix-b", doc_id="SP-800-37r2", chunk_type="section",
        control_id=None, sha256_source="b" * 64,
        text="APPENDIX B PAGE B-2",
    )
    assert rules(run(row)) == ["running_header_fragment"]


def test_a_running_header_over_substantive_body_is_kept_and_stripped():
    # Stage 5 does the stripping; validation must not reject what it produced.
    raw = good_row(
        id="SP-800-137/definition/allocation", doc_id="SP-800-137", chunk_type="definition",
        control_id=None, sha256_source="b" * 64,
        text="APPENDIX B PAGE B-2 Allocation: The process an organization employs to "
             "determine whether security controls are defined as system-specific, "
             "hybrid, or common. " + "A" * 100,
    )
    cleaned, _ = chunk_rows([raw])
    assert len(cleaned) == 1
    assert cleaned[0].text.startswith("Allocation:")
    assert not run(cleaned[0]).rejections


def test_single_character_path_segments_alone_do_not_reject_substantive_rows():
    # Drop caps are no longer headings, but a stale single-letter path segment
    # must not throw away a row that still has real content.
    row = good_row(
        id="SP-800-137/section/x", doc_id="SP-800-137", chunk_type="section",
        control_id=None, sha256_source="b" * 64,
        section_path="CHAPTER TWO > T",
        text="ISCM is a tactic in a larger strategy of organization-wide risk "
             "management. " + "A" * 300,
    )
    assert not run(row).rejections


def test_a_single_character_path_segment_with_no_body_is_rejected():
    row = good_row(
        id="SP-800-137/section/t", doc_id="SP-800-137", chunk_type="section",
        control_id=None, sha256_source="b" * 64,
        section_path="CHAPTER TWO > T", text="T",
    )
    assert rules(run(row)) == ["running_header_fragment"]


def test_a_real_path_with_short_but_multi_character_segments_survives():
    row = good_row(
        id="SP-800-218/ssdf_practice/po.1.1", doc_id="SP-800-218",
        chunk_type="ssdf_practice", control_id=None, sha256_source="b" * 64,
        section_path="SSDF Practices > PO > PO.1 > PO.1.1",
    )
    assert not run(row).rejections


# --- rule 10: midsentence_fragment ------------------------------------------

def test_sections_starting_mid_sentence_are_rejected():
    row = good_row(
        id="SP-800-39/section/x", doc_id="SP-800-39", chunk_type="section",
        control_id=None, sha256_source="b" * 64,
        text="and the organization must therefore consider. " + "A" * 300,
    )
    assert rules(run(row)) == ["midsentence_fragment"]


def test_dangling_numeric_fragments_are_rejected():
    row = good_row(
        id="SP-800-60v2r1/section/12958", doc_id="SP-800-60v2r1", chunk_type="section",
        control_id=None, sha256_source="b" * 64,
        text="12958, as amended by Executive Order 13292. " + "A" * 300,
    )
    assert rules(run(row)) == ["midsentence_fragment"]


def test_a_numbered_heading_is_not_a_dangling_fragment():
    row = good_row(
        id="FIPS-199/section/1-purpose", doc_id="FIPS-199", chunk_type="section",
        control_id=None, sha256_source="b" * 64,
        text="1 PURPOSE\n\nThe E-Government Act of 2002 recognized the importance. " + "A" * 300,
    )
    assert not run(row).rejections


def test_midsentence_rule_applies_only_to_sections():
    # A glossary term is lowercase by convention and must not be caught.
    row = good_row(
        id="SP-800-37r2/definition/assurance", doc_id="SP-800-37r2",
        chunk_type="definition", control_id=None, sha256_source="b" * 64,
        text="assurance: Grounds for justified confidence that a claim has been achieved. "
             + "A" * 100,
    )
    assert not run(row).rejections


# --- rule 11: unique_id and near_dupe ---------------------------------------

def test_duplicate_ids_are_rejected_on_the_later_row():
    report = run(good_row(), good_row(text="B" * 400))
    assert rules(report) == ["unique_id"]
    assert len(report.valid) == 1


def test_identical_text_is_rejected_on_the_later_row():
    first = good_row(id="SP-800-53r5/control/ac-2")
    second = good_row(id="SP-800-53r5/control/ac-3", control_id="ac-3")
    report = run(first, second)
    assert rules(report) == ["near_dupe"]
    assert report.valid[0].id == "SP-800-53r5/control/ac-2"


def test_whitespace_differences_do_not_hide_a_duplicate():
    first = good_row(id="SP-800-53r5/control/ac-2", text="Same text here. " * 30)
    second = good_row(
        id="SP-800-53r5/control/ac-3", control_id="ac-3",
        text="Same  text   here. " * 30,
    )
    assert rules(run(first, second)) == ["near_dupe"]


# --- rule 9: encoding_clean --------------------------------------------------

def test_replacement_and_control_characters_are_rejected():
    assert rules(run(good_row(text="A" * 300 + "�"))) == ["encoding_clean"]
    assert rules(run(good_row(text="A" * 300 + "\x07"))) == ["encoding_clean"]


# --- rule 10: expected counts (warn, never reject) --------------------------

def test_expected_counts_warn_when_a_parser_drifts():
    thin = [good_row(id=f"SP-800-53r5/control/ac-{n}", control_id=f"ac-{n}") for n in range(1, 5)]
    warnings = check_expected_counts(thin)
    assert any("control + enhancement rows" in warning for warning in warnings)
    assert any("families represented" in warning for warning in warnings)


def test_subcategories_are_counted_per_document_not_in_total():
    # Both the framework and the Playbook cover the same 72 subcategories; the
    # total must not be read as 144 out-of-range subcategories.
    rows = []
    for doc_id in ("AI-100-1", "AI-RMF-PLAYBOOK"):
        for n in range(1, 73):
            rows.append(good_row(
                id=f"{doc_id}/ai_rmf_subcategory/govern-{n}", doc_id=doc_id,
                chunk_type="ai_rmf_subcategory", control_id=None,
                text=f"GOVERN {n}: " + "A" * 300,
            ))
    assert not any("AI RMF subcategories" in warning for warning in check_expected_counts(rows))


def test_split_parts_do_not_inflate_the_subcategory_count():
    rows = [
        good_row(id=f"AI-100-1/ai_rmf_subcategory/govern-1.1 (part {n})",
                 doc_id="AI-100-1", chunk_type="ai_rmf_subcategory",
                 control_id=None, text="A" * 300)
        for n in (1, 2)
    ]
    warnings = check_expected_counts(rows)
    assert any("AI RMF subcategories in AI-100-1: 1 " in warning for warning in warnings)


# --- the summary -------------------------------------------------------------

def test_summary_reports_rows_rejections_and_warnings():
    report = run(good_row(), good_row(control_id="ha-25", id="SP-800-53r5/control/ha-25"))
    text = summary_markdown(report, report.rejections, total_bytes=1_000_000)
    assert "Rows published: **1**" in text
    assert "control_family_whitelist" in text
    assert "1.0 MB" in text
