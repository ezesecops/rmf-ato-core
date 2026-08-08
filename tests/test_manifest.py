"""Manifest tests. All offline — nothing here touches the network."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rmf_ato_core.manifest import (
    VALID_FORMATS,
    _alternate_nvlpubs_urls,
    find_pdf_links,
    html_to_text,
    load_manifest,
    validate_manifest_dict,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = REPO_ROOT / "manifest.json"


def test_shipped_manifest_loads_and_validates():
    manifest = load_manifest(MANIFEST_PATH)
    assert manifest.dataset_name == "rmf-ato-core"
    assert len(manifest.documents) == 21
    assert all(doc.format in VALID_FORMATS for doc in manifest.documents)


def test_lookup_by_id():
    manifest = load_manifest(MANIFEST_PATH)
    assert manifest.by_id("SP-800-53r5").format == "oscal"
    assert manifest.by_id("nope") is None
    assert "FIPS-199" in manifest


def test_supersession_watchlist_matches_the_documented_documents():
    manifest = load_manifest(MANIFEST_PATH)
    flagged = {doc.doc_id for doc in manifest.documents if doc.needs_supersession_check}
    # SP 800-60 (both volumes), SP 800-18, SP 800-218 carry "check at build time"
    # notes; the notes scan may add more, but must never drop these.
    assert {"SP-800-60v1r1", "SP-800-60v2r1", "SP-800-18r1", "SP-800-218"} <= flagged


def test_manual_documents_fall_back_to_landing_page_for_source_url():
    manifest = load_manifest(MANIFEST_PATH)
    cnssi = manifest.by_id("CNSSI-1253")
    assert cnssi.url is None
    assert cnssi.effective_source_url == cnssi.landing_page


def _minimal_entry(**overrides):
    entry = {
        "doc_id": "TEST-1",
        "title": "Test",
        "revision": "Rev 1",
        "pub_date": "2020-01",
        "tier": 1,
        "format": "pdf",
        "url": "https://example.gov/x.pdf",
        "landing_page": "https://example.gov/x",
        "status": "final",
        "license": "public-domain-us-gov",
        "notes": "",
    }
    entry.update(overrides)
    return entry


def _wrap(entries):
    return {
        "manifest_version": "1.0.0",
        "dataset_name": "t",
        "maintainer": "t",
        "documents": entries,
    }


def test_validation_catches_bad_format_tier_and_scheme():
    problems = validate_manifest_dict(
        _wrap([_minimal_entry(format="csv", tier=3, url="http://example.gov/x.pdf")])
    )
    joined = " ".join(problems)
    assert "format" in joined and "tier" in joined and "url must be https" in joined


def test_validation_catches_duplicate_doc_ids_and_missing_fields():
    entry = _minimal_entry()
    incomplete = _minimal_entry()
    del incomplete["notes"]
    problems = validate_manifest_dict(_wrap([entry, incomplete]))
    joined = " ".join(problems)
    assert "duplicate doc_id" in joined and "missing field 'notes'" in joined


def test_validation_requires_url_for_fetchable_formats():
    problems = validate_manifest_dict(_wrap([_minimal_entry(url=None)]))
    assert any("requires a url" in p for p in problems)


def test_validation_requires_landing_page_for_manual_documents():
    problems = validate_manifest_dict(
        _wrap([_minimal_entry(format="pdf-manual", url=None, landing_page=None)])
    )
    assert any("requires a landing_page" in p for p in problems)


def test_load_manifest_raises_on_invalid_file(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(_wrap([_minimal_entry(tier=9)])), encoding="utf-8")
    with pytest.raises(ValueError, match="failed validation"):
        load_manifest(bad)


def test_alternate_url_patterns_cover_both_nvlpubs_eras():
    modern = "https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-37r2.pdf"
    assert any("Legacy/SP/nistspecialpublication" in u for u in _alternate_nvlpubs_urls(modern))

    legacy = "https://nvlpubs.nist.gov/nistpubs/Legacy/SP/nistspecialpublication800-39.pdf"
    assert _alternate_nvlpubs_urls(legacy) == [
        "https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-39.pdf"
    ]

    assert _alternate_nvlpubs_urls("https://example.gov/whatever.pdf") == []


def test_find_pdf_links_ranks_matching_nvlpubs_links_first():
    manifest = load_manifest(MANIFEST_PATH)
    doc = manifest.by_id("SP-800-39")
    html = """
      <a href="/random/other.pdf">other</a>
      <a href="https://nvlpubs.nist.gov/nistpubs/Legacy/SP/nistspecialpublication800-39.pdf">SP 800-39</a>
      <a href="https://nvlpubs.nist.gov/nistpubs/unrelated.pdf">unrelated</a>
      <a href="https://example.gov/notapdf.html">html</a>
    """
    links = find_pdf_links(html, "https://csrc.nist.gov/pubs/sp/800/39/final", doc)
    assert links[0].endswith("nistspecialpublication800-39.pdf")
    assert "notapdf.html" not in " ".join(links)
    # Relative link to a non-matching, non-nvlpubs PDF scores zero and is dropped.
    assert not any("random/other.pdf" in link for link in links)


def test_html_to_text_drops_markup_and_scripts():
    html = "<html><script>var x = 1;</script><p>Rev 2 is <b>final</b></p></html>"
    assert html_to_text(html) == "Rev 2 is final"
