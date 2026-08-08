"""The one definition of a dataset row.

Every stage in the pipeline emits or consumes this shape. Keeping it in a single
frozen dataclass is what makes the validation stage able to make hard promises
about the published dataset (see BUILD_SPEC §4 and §10).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, fields
from typing import Any

# --- Controlled vocabularies -------------------------------------------------

# The complete set of chunk kinds. Anything else is a parser bug, not a new
# category — add to this list only with a spec change.
CHUNK_TYPES: tuple[str, ...] = (
    "control",
    "control_enhancement",
    "control_discussion",
    "assessment_objective",
    "assessment_method",
    "baseline",
    "section",
    "task",
    "ai_rmf_subcategory",
    "ssdf_practice",
    "definition",
    "table",
)

# The 20 SP 800-53 Rev 5 control families. This whitelist is the mechanism that
# makes a fabricated ID like "HA-25" or "WE-12" — the signature failure of naive
# PDF chunking — impossible to publish.
CONTROL_FAMILIES: tuple[str, ...] = (
    "ac", "at", "au", "ca", "cm", "cp", "ia", "ir", "ma", "mp",
    "pe", "pl", "pm", "ps", "pt", "ra", "sa", "sc", "si", "sr",
)

# Lowercase OSCAL style only: ac-2, ac-2.3. Uppercase or unseparated forms are
# rejected rather than normalized, because a control ID arriving in the wrong
# shape means it came from prose, not from OSCAL structure.
CONTROL_ID_RE = re.compile(
    r"^(" + "|".join(CONTROL_FAMILIES) + r")-\d+(\.\d+)?$"
)

# Text length bounds (chars). Below the floor a chunk carries no retrievable
# meaning; above the ceiling it stops being one idea.
MIN_TEXT_LEN = 200
MAX_TEXT_LEN = 8000


def is_valid_control_id(value: str) -> bool:
    """True only for lowercase OSCAL control IDs in a real 800-53r5 family."""
    return isinstance(value, str) and CONTROL_ID_RE.match(value) is not None


# --- ID construction ---------------------------------------------------------

_SLUG_STRIP_RE = re.compile(r"[^a-z0-9.]+")


def slugify(value: str) -> str:
    """Make a stable, readable slug fragment for use inside a row `id`.

    Dots survive because they carry meaning in the identifiers we slug
    (`ac-2.3`, `govern-1.1`, `po.1.1`, section `3.2`).
    """
    normalized = unicodedata.normalize("NFKD", value)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = _SLUG_STRIP_RE.sub("-", ascii_only).strip("-")
    # Collapse runs introduced by the substitution above.
    slug = re.sub(r"-{2,}", "-", slug)
    return slug


def make_id(doc_id: str, chunk_type: str, slug_source: str) -> str:
    """Deterministic row id: `{doc_id}/{chunk_type}/{slug}`.

    Deterministic (not hashed, not sequential) so that a diff between two
    dataset versions shows which chunks actually changed.
    """
    if chunk_type not in CHUNK_TYPES:
        raise ValueError(f"unknown chunk_type: {chunk_type!r}")
    slug = slugify(slug_source)
    if not slug:
        raise ValueError(f"slug source produced an empty slug: {slug_source!r}")
    return f"{doc_id}/{chunk_type}/{slug}"


# --- Text normalization ------------------------------------------------------

# Ligatures and typographic characters PDF extraction leaves behind. Mapped only
# where the replacement is unambiguous.
_CHAR_FIXES = {
    "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl",
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", "−": "-",
    " ": " ", " ": " ", " ": " ", "​": "",
    "…": "...",
}


def normalize_text(text: str) -> str:
    """Collapse whitespace, drop control chars, keep paragraph breaks as \\n\\n."""
    for bad, good in _CHAR_FIXES.items():
        text = text.replace(bad, good)
    # Drop control characters except newline and tab (tab becomes a space below).
    text = "".join(
        ch for ch in text
        if ch in "\n\t" or unicodedata.category(ch)[0] != "C"
    )
    text = text.replace("\t", " ")
    # Normalize line endings, then collapse 3+ newlines into a paragraph break.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ ]{2,}", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# --- The row -----------------------------------------------------------------

@dataclass(frozen=True)
class Row:
    """One chunk of the published dataset.

    Frozen because rows are produced once by a parser and never patched in
    place; a correction means re-running the stage that made it.
    """

    id: str
    text: str
    doc_id: str
    doc_title: str
    revision: str
    pub_date: str
    tier: int
    chunk_type: str
    control_id: str | None
    section_path: str | None
    source_url: str
    sha256_source: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json_line(self) -> str:
        import json

        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Row":
        """Build a Row from a parsed JSONL record, failing loudly on drift.

        Unknown or missing keys are errors rather than silently tolerated: an
        interim file that no longer matches the schema means a stage is out of
        date, and we would rather stop than publish a half-shaped row.
        """
        expected = {f.name for f in fields(cls)}
        missing = expected - data.keys()
        extra = data.keys() - expected
        if missing:
            raise ValueError(f"row missing fields: {sorted(missing)}")
        if extra:
            raise ValueError(f"row has unknown fields: {sorted(extra)}")
        return cls(**data)


# Field order used for the parquet schema and any tabular output, so column
# order stays stable across dataset versions.
FIELD_ORDER: tuple[str, ...] = tuple(f.name for f in fields(Row))
