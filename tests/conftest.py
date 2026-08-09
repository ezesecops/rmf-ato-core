"""Shared test helpers.

These live here rather than in a test module so that every test file can import
them without importing another test file. A cross-test import like
`from tests.test_schema import make_row` only resolves when the repository root
happens to be on sys.path — true under `python -m pytest`, false under a bare
`pytest`, which is how it broke in CI. pytest puts this directory on sys.path
for any test package without an `__init__.py`, so `from conftest import …`
works either way.
"""

from __future__ import annotations

from rmf_ato_core.manifest import Document
from rmf_ato_core.schema import Row


def make_row(**overrides) -> Row:
    """A valid Row; pass keyword arguments to bend one field out of shape."""
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


def make_doc(
    doc_id: str, fmt: str = "oscal", url: str | None = "https://example.gov/x.json"
) -> Document:
    """A manifest entry, without reading the real manifest."""
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
