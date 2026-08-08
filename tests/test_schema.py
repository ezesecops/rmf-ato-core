"""Schema tests — the control-family whitelist is the project's central promise,
so it gets the most attention here."""

from __future__ import annotations

import json

import pytest

from rmf_ato_core.schema import (
    CHUNK_TYPES,
    CONTROL_FAMILIES,
    FIELD_ORDER,
    MAX_TEXT_LEN,
    MIN_TEXT_LEN,
    Row,
    is_valid_control_id,
    make_id,
    normalize_text,
    slugify,
)


def make_row(**overrides) -> Row:
    base = dict(
        id="SP-800-53r5/control/ac-2",
        text="x" * 300,
        doc_id="SP-800-53r5",
        doc_title="Security and Privacy Controls for Information Systems and Organizations",
        revision="Rev 5",
        pub_date="2020-09",
        tier=1,
        chunk_type="control",
        control_id="ac-2",
        section_path="AC > AC-2",
        source_url="https://example.invalid/catalog.json",
        sha256_source="0" * 64,
    )
    base.update(overrides)
    return Row(**base)


# --- Row construction --------------------------------------------------------

def test_row_round_trips_through_dict_and_json():
    row = make_row()
    assert Row.from_dict(row.to_dict()) == row
    assert json.loads(row.to_json_line())["control_id"] == "ac-2"


def test_row_is_frozen():
    row = make_row()
    with pytest.raises(Exception):
        row.text = "mutated"  # type: ignore[misc]


def test_from_dict_rejects_missing_and_unknown_fields():
    data = make_row().to_dict()
    del data["tier"]
    with pytest.raises(ValueError, match="missing fields"):
        Row.from_dict(data)

    data = make_row().to_dict()
    data["embedding"] = [0.1, 0.2]
    with pytest.raises(ValueError, match="unknown fields"):
        Row.from_dict(data)


def test_field_order_is_the_documented_schema():
    assert FIELD_ORDER == (
        "id", "text", "doc_id", "doc_title", "revision", "pub_date",
        "tier", "chunk_type", "control_id", "section_path",
        "source_url", "sha256_source",
    )


# --- ID determinism ----------------------------------------------------------

def test_make_id_is_deterministic_and_readable():
    assert make_id("SP-800-53r5", "control", "AC-2") == "SP-800-53r5/control/ac-2"
    assert make_id("SP-800-53r5", "control", "AC-2") == make_id("SP-800-53r5", "control", "AC-2")


def test_make_id_preserves_dotted_identifiers():
    assert make_id("SP-800-53r5", "control_enhancement", "ac-2.3") == (
        "SP-800-53r5/control_enhancement/ac-2.3"
    )
    assert make_id("AI-100-1", "ai_rmf_subcategory", "GOVERN 1.1") == (
        "AI-100-1/ai_rmf_subcategory/govern-1.1"
    )
    assert make_id("SP-800-218", "ssdf_practice", "PO.1.1") == (
        "SP-800-218/ssdf_practice/po.1.1"
    )


def test_make_id_rejects_unknown_chunk_type_and_empty_slug():
    with pytest.raises(ValueError, match="unknown chunk_type"):
        make_id("SP-800-53r5", "embedding", "ac-2")
    with pytest.raises(ValueError, match="empty slug"):
        make_id("SP-800-53r5", "control", "!!!")


def test_slugify_flattens_punctuation_and_case():
    assert slugify("Task P-1: Prepare (Organization Level)") == "task-p-1-prepare-organization-level"


# --- Control family whitelist ------------------------------------------------

@pytest.mark.parametrize("control_id", ["ac-2", "ac-2.3", "sr-11", "pm-1", "si-4.24"])
def test_whitelist_accepts_real_control_ids(control_id):
    assert is_valid_control_id(control_id)


@pytest.mark.parametrize(
    "control_id",
    [
        "ha-1",    # fabricated family, the classic naive-chunking artifact
        "we-12",   # fabricated family
        "am-6",    # fabricated family
        "AC-2",    # uppercase: came from prose, not OSCAL
        "ac2",     # missing separator
        "ac-",     # no number
        "ac-2.3.4",  # enhancements are one level deep
        "",
        " ac-2",
        "ac-2 ",
    ],
)
def test_whitelist_rejects_everything_else(control_id):
    assert not is_valid_control_id(control_id)


def test_whitelist_has_exactly_the_twenty_families():
    assert len(CONTROL_FAMILIES) == 20
    assert len(set(CONTROL_FAMILIES)) == 20


def test_chunk_types_are_unique():
    assert len(set(CHUNK_TYPES)) == len(CHUNK_TYPES)


# --- Text normalization ------------------------------------------------------

def test_normalize_text_collapses_spaces_and_keeps_paragraph_breaks():
    assert normalize_text("a   b\n\n\n\nc") == "a b\n\nc"


def test_normalize_text_fixes_pdf_ligatures_and_smart_quotes():
    assert normalize_text("the ﬁrst “ofﬂine” test") == 'the first "offline" test'


def test_normalize_text_strips_control_characters():
    assert "\x00" not in normalize_text("clean\x00text")


def test_length_bounds_are_the_spec_values():
    assert (MIN_TEXT_LEN, MAX_TEXT_LEN) == (200, 8000)
