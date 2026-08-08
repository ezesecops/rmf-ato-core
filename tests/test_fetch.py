"""Stage 2 tests. Offline: the sanity checks and provenance record are what
matter here, not the transport."""

from __future__ import annotations

import json

import pytest

from rmf_ato_core.fetch import (
    ProvenanceEntry,
    SanityError,
    artifact_extension,
    load_provenance,
    raw_path,
    sanity_check,
    save_provenance,
    sha256_bytes,
)
from tests.test_parse_oscal import make_doc


def test_pdf_must_start_with_the_pdf_header():
    doc = make_doc("FIPS-199", fmt="pdf", url="https://example.gov/x.pdf")
    sanity_check(b"%PDF-1.7\nrest", doc)
    with pytest.raises(SanityError, match="does not start with %PDF"):
        sanity_check(b"<html>404 Not Found</html>", doc)


def test_oscal_must_carry_a_catalog_or_profile():
    doc = make_doc("SP-800-53r5")
    sanity_check(json.dumps({"catalog": {}}).encode(), doc)
    sanity_check(json.dumps({"profile": {}}).encode(), doc)
    with pytest.raises(SanityError, match="no top-level 'catalog' or 'profile'"):
        sanity_check(json.dumps({"something-else": {}}).encode(), doc)
    with pytest.raises(SanityError, match="not valid JSON"):
        sanity_check(b"not json at all", doc)


def test_web_source_accepts_json_when_the_url_is_json():
    doc = make_doc("AI-RMF-PLAYBOOK", fmt="web", url="https://example.gov/playbook.json")
    sanity_check(b'[{"title": "GOVERN 1.1"}]', doc)
    with pytest.raises(SanityError, match="JSON source is empty"):
        sanity_check(b"[]", doc)


def test_artifact_paths_by_format(tmp_path):
    assert artifact_extension(make_doc("SP-800-53r5")) == "json"
    assert artifact_extension(make_doc("FIPS-199", fmt="pdf")) == "pdf"
    assert artifact_extension(make_doc("P", fmt="web", url="https://x.gov/a.json")) == "json"
    assert artifact_extension(make_doc("P", fmt="web", url="https://x.gov/a")) == "html"

    manual = raw_path(tmp_path, make_doc("CNSSI-1253", fmt="pdf-manual", url=None))
    assert manual == tmp_path / "raw" / "manual" / "CNSSI-1253.pdf"


def test_provenance_round_trips_and_sorts(tmp_path):
    path = tmp_path / "provenance.json"
    entries = {
        "B-DOC": ProvenanceEntry(sha256_bytes(b"b"), 1, "2026-08-08T00:00:00+00:00",
                                 "https://x.gov/b", 200, "download"),
        "A-DOC": ProvenanceEntry(sha256_bytes(b"a"), 1, "2026-08-08T00:00:00+00:00",
                                 "https://x.gov/a", 200, "download"),
    }
    save_provenance(path, entries)
    assert list(json.loads(path.read_text())) == ["A-DOC", "B-DOC"]
    assert load_provenance(path) == entries


def test_load_provenance_is_empty_when_absent(tmp_path):
    assert load_provenance(tmp_path / "nope.json") == {}
