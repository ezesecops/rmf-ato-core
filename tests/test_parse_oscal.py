"""Stage 3 tests, run against a hand-made fixture catalog rather than the real
10MB file so they stay fast and independent of NIST's release schedule."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rmf_ato_core.manifest import Document
from rmf_ato_core.parse_oscal import (
    ASSESSMENT_DOC_ID,
    build_param_index,
    control_sort_key,
    display_id,
    parse_catalog,
    parse_profile,
    render_param,
    render_prose,
)
from rmf_ato_core.schema import is_valid_control_id

FIXTURES = Path(__file__).parent / "fixtures"
CATALOG = FIXTURES / "mini_catalog.json"
PROFILE = FIXTURES / "mini_profile.json"
FAKE_SHA = "a" * 64


def make_doc(doc_id: str, fmt: str = "oscal", url: str | None = "https://example.gov/x.json") -> Document:
    return Document(
        doc_id=doc_id,
        title=f"Title of {doc_id}",
        revision="Rev 5",
        pub_date="2020-09",
        tier=1,
        format=fmt,
        url=url,
        landing_page="https://example.gov/landing",
        status="final",
        license="public-domain-us-gov",
        notes="",
    )


@pytest.fixture(scope="module")
def parsed():
    rows, rejections, stats = parse_catalog(
        CATALOG,
        make_doc("SP-800-53r5"),
        make_doc(ASSESSMENT_DOC_ID, fmt="embedded-in-oscal", url=None),
        FAKE_SHA,
    )
    return rows, rejections, stats


def row_by_id(rows, row_id):
    return next((row for row in rows if row.id == row_id), None)


# --- withdrawn controls ------------------------------------------------------

def test_withdrawn_controls_are_skipped_and_logged(parsed):
    rows, rejections, _ = parsed
    published_ids = {row.control_id for row in rows}
    assert "ac-99" not in published_ids
    assert "ac-1.2" not in published_ids
    assert not any("must never be published" in row.text for row in rows)

    withdrawn = {r.ref for r in rejections if r.rule == "withdrawn_control"}
    assert withdrawn == {"ac-99", "ac-1.2"}
    assert all(r.stage == "parse_oscal" for r in rejections)


def test_rejections_serialize_as_json_lines(parsed):
    _, rejections, _ = parsed
    record = json.loads(rejections[0].to_json_line())
    assert set(record) == {"doc_id", "ref", "rule", "detail", "stage"}


# --- parameter rendering -----------------------------------------------------

def test_parameters_render_in_odp_convention(parsed):
    rows, _, _ = parsed
    control = row_by_id(rows, "SP-800-53r5/control/ac-1")
    assert "[Assignment: organization-defined personnel or roles]" in control.text
    # A Selection keeps its choice list and its how-many qualifier.
    assert "[Selection; one or more: organization-level;" in control.text


def test_no_template_markers_survive(parsed):
    rows, _, _ = parsed
    for row in rows:
        assert "{{" not in row.text
        assert "insert: param" not in row.text


def test_nested_markers_inside_a_selection_choice_are_resolved(parsed):
    rows, _, _ = parsed
    control = row_by_id(rows, "SP-800-53r5/control/ac-1")
    # The fixture's second choice embeds an insertion, mirroring AC-7 in the
    # real catalog.
    assert "system-level for [Assignment: organization-defined personnel or roles]" in control.text


def test_unresolved_parameter_gets_a_placeholder_and_is_counted():
    from rmf_ato_core.parse_oscal import ParseStats

    stats = ParseStats()
    out = render_prose("Do it every {{ insert: param, nope_odp.01 }}.", {}, stats)
    assert "{{" not in out
    assert "[Assignment: organization-defined parameter]" in out
    assert stats.unresolved_params["nope_odp.01"] == 1


def test_render_param_shapes():
    assert render_param({"id": "x", "label": "frequency"}) == (
        "[Assignment: organization-defined frequency]"
    )
    assert render_param({"id": "x", "select": {"how-many": "one-or-more", "choice": ["a", "b"]}}) == (
        "[Selection; one or more: a; b]"
    )


def test_param_index_covers_parameters_declared_on_parent_controls():
    catalog = json.loads(CATALOG.read_text())["catalog"]
    index = build_param_index(catalog)
    assert "ac-01_odp.01" in index and "ac-01_odp.02" in index


# --- typing, attribution, identity -------------------------------------------

def test_enhancement_is_typed_and_pathed_correctly(parsed):
    rows, _, _ = parsed
    enhancement = row_by_id(rows, "SP-800-53r5/control_enhancement/ac-1.1")
    assert enhancement.chunk_type == "control_enhancement"
    assert enhancement.control_id == "ac-1.1"
    assert enhancement.section_path == "AC > AC-1 > AC-1(1)"
    assert enhancement.text.startswith("AC-1(1) Policy and Procedures | Automated Policy Distribution")


def test_base_control_and_discussion_rows(parsed):
    rows, _, _ = parsed
    control = row_by_id(rows, "SP-800-53r5/control/ac-1")
    discussion = row_by_id(rows, "SP-800-53r5/control_discussion/ac-1")
    assert control.chunk_type == "control"
    assert control.section_path == "AC > AC-1"
    assert "a. Develop, document" in control.text
    assert discussion.chunk_type == "control_discussion"
    assert discussion.section_path == "AC > AC-1 > Discussion"
    assert "address the controls in the AC family" in discussion.text


def test_assessment_rows_are_attributed_to_sp_800_53a(parsed):
    rows, _, _ = parsed
    objective = row_by_id(rows, f"{ASSESSMENT_DOC_ID}/assessment_objective/ac-1")
    method = row_by_id(rows, f"{ASSESSMENT_DOC_ID}/assessment_method/ac-1")

    assert objective.doc_id == ASSESSMENT_DOC_ID
    assert objective.chunk_type == "assessment_objective"
    assert objective.control_id == "ac-1"
    # Objectives carry only a class='sp800-53a' label, and that numbering is
    # what an assessor cites, so it must survive into the text.
    assert "AC-01a. an access control policy is developed and documented;" in objective.text

    assert method.doc_id == ASSESSMENT_DOC_ID
    assert method.chunk_type == "assessment_method"
    assert "Examine:" in method.text
    assert "system security plan" in method.text
    # Assessment rows carry the source URL of their own manifest entry, which
    # for the embedded 53A entry is its landing page.
    assert method.source_url == "https://example.gov/landing"


def test_every_control_id_passes_the_family_whitelist(parsed):
    rows, _, _ = parsed
    for row in rows:
        if row.control_id is not None:
            assert is_valid_control_id(row.control_id), row.id


def test_rows_carry_the_artifact_hash(parsed):
    rows, _, _ = parsed
    assert {row.sha256_source for row in rows} == {FAKE_SHA}


def test_family_counts(parsed):
    _, _, stats = parsed
    assert stats.by_family["ac"]["control"] == 1
    assert stats.by_family["ac"]["enhancement"] == 1
    assert stats.by_family["ac"]["discussion"] == 2
    assert stats.by_family["ac"]["withdrawn"] == 2
    assert set(stats.part_names) == {
        "statement", "item", "guidance", "assessment-objective",
        "assessment-method", "assessment-objects",
    }


def test_display_id_matches_nist_printed_form():
    assert display_id("ac-2") == "AC-2"
    assert display_id("ac-2.3") == "AC-2(3)"
    assert display_id("sr-11") == "SR-11"


# --- baselines ---------------------------------------------------------------

def test_control_ids_sort_the_way_a_practitioner_reads_them():
    ids = ["ac-11", "ac-2", "sr-10", "sr-2", "ac-2.3", "ac-2.10"]
    assert sorted(ids, key=control_sort_key) == [
        "ac-2", "ac-2.3", "ac-2.10", "ac-11", "sr-2", "sr-10",
    ]


def test_baseline_row_lists_controls_and_carries_no_control_id():
    doc = make_doc("SP-800-53B-MODERATE")
    rows = parse_profile(PROFILE, doc, FAKE_SHA)
    assert len(rows) == 1
    baseline = rows[0]
    assert baseline.chunk_type == "baseline"
    assert baseline.id == "SP-800-53B-MODERATE/baseline/moderate"
    # A baseline is a set of controls, not a control.
    assert baseline.control_id is None
    assert "AC-1, AC-1(1), AU-2" in baseline.text
    assert "2 base controls, 1 enhancements" in baseline.text
