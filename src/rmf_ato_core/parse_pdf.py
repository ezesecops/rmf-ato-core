"""Stage 4 — extract rows from the PDF documents.

Deliberately simple and honest about its limits. PDF layout is not structure, so
this stage aims for high precision rather than total recall: anything it cannot
place confidently goes to the rejection log with its page number, and the
dataset card says PDF section coverage is partial.

Two rules hold everywhere in this module:

1. **No control IDs, ever.** SP 800-53 identifiers appear in this prose
   constantly, and a control ID that came from prose rather than from OSCAL is
   exactly the fabrication this dataset exists to prevent.
2. **Nothing is invented.** Text is transcribed, never summarized or rephrased.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .manifest import Document
from .parse_oscal import Rejection
from .schema import Row, make_id, normalize_text, slugify


@dataclass
class Line:
    """One rendered line of text, with the font signal we need for headings."""

    text: str
    page: int          # 1-based, as a human would cite it
    size: float        # dominant span size on the line
    bold: bool
    y: float

    @property
    def is_blank(self) -> bool:
        return not self.text.strip()


@dataclass
class Section:
    """A heading and the lines beneath it, before any row shaping."""

    heading: str
    trail: list[str]           # ancestor headings, outermost first
    lines: list[Line] = field(default_factory=list)
    page: int = 0
    level: int = 0             # heading depth; 0 for the pre-heading preamble

    @property
    def path(self) -> str:
        return " > ".join([*self.trail, self.heading]) if self.heading else " > ".join(self.trail)

    @property
    def body(self) -> str:
        return "\n".join(line.text for line in self.lines)


# --- extraction --------------------------------------------------------------

def extract_lines(pdf_path: Path) -> tuple[list[Line], int]:
    """Flatten a PDF into lines, keeping page number and font size per line."""
    import pymupdf

    lines: list[Line] = []
    with pymupdf.open(pdf_path) as document:
        page_count = document.page_count
        for index, page in enumerate(document, start=1):
            for block in page.get_text("dict")["blocks"]:
                for raw_line in block.get("lines", []):
                    spans = [s for s in raw_line["spans"] if s["text"].strip()]
                    if not spans:
                        continue
                    text = "".join(span["text"] for span in raw_line["spans"])
                    # The dominant span decides the line's size, so a stray
                    # superscript or footnote marker cannot demote a heading.
                    dominant = max(spans, key=lambda s: len(s["text"]))
                    lines.append(
                        Line(
                            text=text.strip(),
                            page=index,
                            size=round(dominant["size"], 1),
                            # PyMuPDF flags bit 4 (value 16) marks bold.
                            bold=bool(dominant["flags"] & 2 ** 4),
                            y=round(raw_line["bbox"][1], 1),
                        )
                    )
    return lines, page_count


# --- furniture ---------------------------------------------------------------

_PAGE_NUMBER_RE = re.compile(r"^(page\s+)?[ivxlcdm\d]+(\s+of\s+\d+)?$", re.IGNORECASE)
_RULE_RE = re.compile(r"^[_\-=–—\s]{6,}$")
# Table-of-contents entries: dot leaders, with or without a trailing page number.
_TOC_RE = re.compile(r"\.{4,}\s*\d*\s*$")

BOILERPLATE_PATTERNS = (
    re.compile(r"this publication is available free of charge", re.IGNORECASE),
    re.compile(r"^https?://(dx\.)?doi\.org/", re.IGNORECASE),
    re.compile(r"^\s*NIST (Special Publication|SP)\s+[\d\-.A-Za-z]+\s*$", re.IGNORECASE),
    re.compile(r"national institute of standards and technology", re.IGNORECASE),
    re.compile(r"u\.s\. department of commerce", re.IGNORECASE),
)


def _furniture_key(text: str) -> str:
    """Normalize a line for repetition counting: case, spacing and digits out.

    Digits go because a running header usually carries the page number, which
    would otherwise make every occurrence look unique.
    """
    collapsed = re.sub(r"\s+", " ", text.strip().lower())
    return re.sub(r"\d+", "#", collapsed)


def find_repeating_furniture(lines: list[Line], page_count: int, threshold: float = 0.5) -> set[str]:
    """Lines appearing on more than `threshold` of pages are running headers,
    footers or rules — never content."""
    pages_by_key: dict[str, set[int]] = {}
    for line in lines:
        if len(line.text.strip()) < 3:
            continue
        pages_by_key.setdefault(_furniture_key(line.text), set()).add(line.page)
    minimum = max(2, int(page_count * threshold))
    return {key for key, pages in pages_by_key.items() if len(pages) > minimum}


# A contents entry: a short title followed by a page number, with or without
# dot leaders. Roman numerals cover front-matter page numbering.
_CONTENTS_ENTRY_RE = re.compile(r"^\S.{2,90}?[\s.]+(\d{1,3}|[ivxl]{1,6})$", re.IGNORECASE)


def find_contents_pages(lines: list[Line], page_count: int, minimum_entries: int = 5) -> set[int]:
    """Pages that are a table of contents.

    Contents entries look exactly like headings — that is the point of them —
    so without this the contents page becomes a parallel, empty section tree
    whose entries then show up as ancestors of the real chapters.
    Only early pages qualify: later in a document, lines ending in a number are
    usually table data (SP 800-60's impact tables, for one).
    """
    horizon = max(3, int(page_count * 0.2))
    counts: Counter[int] = Counter()
    for line in lines:
        if line.page <= horizon and _CONTENTS_ENTRY_RE.match(line.text.strip()):
            counts[line.page] += 1
    return {page for page, count in counts.items() if count >= minimum_entries}


def is_furniture(line: Line, repeating: set[str], contents_pages: frozenset[int] = frozenset()) -> bool:
    text = line.text.strip()
    if not text or _RULE_RE.match(text) or _PAGE_NUMBER_RE.match(text) or _TOC_RE.search(text):
        return True
    if line.page in contents_pages:
        return True
    if _furniture_key(text) in repeating:
        return True
    return any(pattern.search(text) for pattern in BOILERPLATE_PATTERNS)


# --- heading detection -------------------------------------------------------

NUMBERED_HEADING_RE = re.compile(r"^(\d+(?:\.\d+)*)\s+(\S.*)$")
KEYWORD_HEADING_RE = re.compile(
    r"^(CHAPTER|APPENDIX|ANNEX|SECTION|TASK)\s+([A-Z0-9][-A-Z0-9.]*)\s*:?\s*(.*)$",
    re.IGNORECASE,
)
# A task sits inside a chapter, so it must not reset the heading trail the way
# a chapter does — otherwise every task row loses its chapter in section_path.
KEYWORD_HEADING_LEVELS = {"chapter": 1, "section": 1, "appendix": 1, "annex": 1, "task": 3}
LETTERED_APPENDIX_RE = re.compile(r"^([A-Z](?:\.\d+)+)\s+(\S.*)$")


def body_font_size(lines: list[Line]) -> float:
    """The most common font size, weighted by characters — i.e. body text."""
    weights: Counter[float] = Counter()
    for line in lines:
        weights[line.size] += len(line.text)
    return weights.most_common(1)[0][0] if weights else 10.0


def heading_level(line: Line, body_size: float) -> int | None:
    """Depth of this line as a heading, or None if it is body text.

    Numbering is trusted first because it is explicit; the font signal only
    breaks ties for headings that carry no number.
    """
    text = line.text.strip()
    if not text or len(text) > 160:
        return None

    # A single character is never a heading. NIST chapters open with a large
    # decorative drop cap, which is set bigger than any real heading and would
    # otherwise split the chapter and leave a one-letter section_path segment.
    if len(text) == 1:
        return None

    keyword_match = KEYWORD_HEADING_RE.match(text)
    # A line that ends like a sentence is prose, however it starts.
    if text.endswith((".", ";")) and not text.isupper() and not keyword_match:
        return None

    # A numbered heading must also look like a heading: short, and set at least
    # at body size. Footnotes ("1 Information is categorized according to…") open
    # with a digit too, but run long and are set smaller.
    match = NUMBERED_HEADING_RE.match(text)
    if match and len(text) <= 120 and line.size + 0.2 >= body_size:
        return match.group(1).count(".") + 1

    if keyword_match:
        return KEYWORD_HEADING_LEVELS.get(keyword_match.group(1).lower(), 1)

    match = LETTERED_APPENDIX_RE.match(text)
    if match:
        return match.group(1).count(".") + 1

    # Unnumbered heading: must stand out from body text by size or weight, and
    # be short enough that it cannot be a sentence.
    if len(text) <= 80 and not text.endswith(".") and not _looks_like_body_line(text):
        if line.size >= body_size + 1.0:
            return 2
        if (line.bold or text.isupper()) and line.size >= body_size:
            return 3
    return None


_BULLET_START_RE = re.compile(r"^[•▪◦○*·–—-]\s")


def _looks_like_body_line(text: str) -> bool:
    """Body text that would otherwise pass the font test for a heading.

    SP 800-218 sets its bullet lists in bold, so without this every wrapped
    bullet line became its own heading and shredded the document into
    fragments — a quarter of that publication's text was being dropped as
    "too short" before this check existed.
    """
    if _BULLET_START_RE.match(text):
        return True
    if text.endswith((",", ";")):
        return True
    # A heading does not end mid-clause. Compare the last *word*, so that
    # "Command" and "Vendor" are not read as ending in "and" and "or".
    trailing_word = text.rsplit(" ", 1)[-1].lower().strip(",;:")
    return trailing_word in {"and", "or", "the", "of", "to", "a", "an", "for", "with", "in"}


def build_sections(
    lines: list[Line],
    body_size: float,
    repeating: set[str],
    contents_pages: frozenset[int] = frozenset(),
) -> list[Section]:
    """Group lines under their heading trail."""
    sections: list[Section] = []
    trail: list[tuple[int, str]] = []  # (level, heading)
    current = Section(heading="", trail=[], page=lines[0].page if lines else 1)

    for line in lines:
        if is_furniture(line, repeating, contents_pages):
            continue
        level = heading_level(line, body_size)
        if level is None:
            current.lines.append(line)
            continue

        if current.lines or current.heading:
            sections.append(current)
        while trail and trail[-1][0] >= level:
            trail.pop()
        current = Section(
            heading=line.text.strip(),
            trail=[heading for _, heading in trail],
            page=line.page,
            level=level,
        )
        trail.append((level, line.text.strip()))

    if current.lines or current.heading:
        sections.append(current)
    return sections


# --- row building ------------------------------------------------------------

class PdfRowBuilder:
    """Builds rows for one document, with the shared provenance fields filled in."""

    def __init__(self, doc: Document, sha256_source: str):
        self.doc = doc
        self.sha256 = sha256_source
        self.rejections: list[Rejection] = []
        self._seen_ids: set[str] = set()

    def row(self, chunk_type: str, slug: str, text: str, section_path: str) -> Row:
        row_id = make_id(self.doc.doc_id, chunk_type, slug)
        # Two sections can share a heading ("Introduction" in two appendices);
        # disambiguate rather than silently overwrite.
        if row_id in self._seen_ids:
            suffix = 2
            while f"{row_id}-{suffix}" in self._seen_ids:
                suffix += 1
            row_id = f"{row_id}-{suffix}"
        self._seen_ids.add(row_id)
        return Row(
            id=row_id,
            text=normalize_text(text),
            doc_id=self.doc.doc_id,
            doc_title=self.doc.title,
            revision=self.doc.revision,
            pub_date=self.doc.pub_date,
            tier=self.doc.tier,
            chunk_type=chunk_type,
            # Rule 2 of this module: PDF-derived rows never carry a control ID.
            control_id=None,
            section_path=section_path or None,
            source_url=self.doc.effective_source_url,
            sha256_source=self.sha256,
        )

    def reject(self, ref: str, rule: str, detail: str) -> None:
        self.rejections.append(
            Rejection(doc_id=self.doc.doc_id, ref=ref, rule=rule, detail=detail, stage="parse_pdf")
        )


# --- front matter ------------------------------------------------------------

# Front matter worth keeping even though it sits before the numbered body.
FRONT_MATTER_KEEP_RE = re.compile(r"^(executive summary|abstract)\b", re.IGNORECASE)


def find_body_start_page(
    lines: list[Line], body_size: float, contents_pages: frozenset[int] = frozenset()
) -> int:
    """Page of the first numbered or CHAPTER/SECTION heading.

    Everything before it is title page, signature blocks, foreword and contents
    — publication furniture, not guidance.
    """
    for line in lines:
        text = line.text.strip()
        if line.page in contents_pages or heading_level(line, body_size) != 1:
            continue
        numbered = NUMBERED_HEADING_RE.match(text)
        # Only the *first* numbered section counts. Any numbered line would do
        # otherwise, and a stray one in the front matter (SP 800-218 has one on
        # page 3) leaves the address block and trademark notice looking like
        # body content, which then pollutes every section_path beneath them.
        if (numbered and numbered.group(1) == "1") or re.match(
            r"^(CHAPTER|SECTION)\s", text, re.IGNORECASE
        ):
            candidate = line.page
            # Front matter is never a third of a publication. SP 800-218 sets
            # its numbered headings across two lines ("1" then "Introduction"),
            # so no line ever matches and detection would otherwise land deep in
            # the body and delete most of the document.
            page_count = max((l.page for l in lines), default=1)
            return candidate if candidate <= max(3, page_count * 0.33) else 1
    return 1


# Front-matter headings that must never appear as an ancestor in section_path,
# whether or not the front-matter drop caught the section they head.
FRONT_MATTER_HEADING_RE = re.compile(
    r"(gaithersburg|submit comments|trademark|patent disclosure|"
    r"^abstract$|^keywords$|^acknowledg|^disclaimer|^audience$|"
    r"bureau drive|attn:|^certain commercial)",
    re.IGNORECASE,
)


def clean_trail(trail: list[str]) -> list[str]:
    return [head for head in trail if not FRONT_MATTER_HEADING_RE.search(head)]


def drop_front_matter(
    sections: list[Section], body_start_page: int, builder: "PdfRowBuilder"
) -> list[Section]:
    kept: list[Section] = []
    dropped: list[Section] = []
    for section in sections:
        if section.page < body_start_page and not FRONT_MATTER_KEEP_RE.match(section.heading):
            dropped.append(section)
        else:
            kept.append(section)

    # A dropped heading must not survive as an ancestor in someone else's path:
    # a contents entry is not the parent of the chapter it lists.
    dropped_headings = {section.heading for section in dropped}
    for section in kept:
        section.trail = [head for head in section.trail if head not in dropped_headings]

    if dropped:
        builder.reject(
            ref=f"pages 1-{body_start_page - 1}",
            rule="front_matter",
            detail=(
                f"dropped {len(dropped)} pre-body section(s) (title page, foreword, "
                f"signature blocks, contents) before page {body_start_page}"
            ),
        )
    return kept


# --- shared shaping ----------------------------------------------------------

def clean_heading(heading: str) -> str:
    """'1     PURPOSE' -> '1 PURPOSE'."""
    return re.sub(r"\s+", " ", heading).strip()


def section_text(section: Section) -> str:
    """Heading plus body, so a retrieved section names itself."""
    body = section.body.strip()
    heading = clean_heading(section.heading)
    return f"{heading}\n\n{body}" if heading and body else (body or heading)


def section_slug(section: Section) -> str:
    """A slug source for the row id, falling back to the page.

    Some headings are pure punctuation once extracted (a stray bullet), which
    would slug to nothing.
    """
    heading = clean_heading(section.heading)
    return heading if slugify(heading) else f"page-{section.page}"


def emit_sections(
    builder: PdfRowBuilder,
    sections: list[Section],
    chunk_type: str = "section",
    min_chars: int = 40,
) -> list[Row]:
    """The default shaping: one row per section.

    The guard here only drops true fragments. Short-but-real sections are kept
    and left for Stage 5, which merges a short section into its following
    sibling rather than discarding it — dropping them here would silently lose
    a quarter of a document whose layout fragments badly.
    """
    rows: list[Row] = []
    for section in sections:
        text = section_text(section)
        if len(text.strip()) < min_chars:
            builder.reject(
                ref=f"p{section.page}:{clean_heading(section.heading)[:60]}",
                rule="section_too_short",
                detail=f"{len(text.strip())} chars on page {section.page}",
            )
            continue
        path = " > ".join(
            clean_heading(part) for part in [*clean_trail(section.trail), section.heading] if part
        )
        rows.append(builder.row(chunk_type, section_slug(section), text, path))
    return rows


# --- glossaries --------------------------------------------------------------

GLOSSARY_HEADING_RE = re.compile(
    r"(terms and definitions|glossary|appendix\s+[a-z]\s*[-—:]?\s*(glossary|terms))",
    re.IGNORECASE,
)

# "CONFIDENTIALITY: Preserving authorized restrictions…" or "Availability - …".
# The term must be short and title/upper case; a sentence containing a colon
# must not qualify.
DEFINITION_RE = re.compile(
    r"^(?P<term>[A-Z][A-Za-z0-9()/,'’\- ]{2,70}?)\s*[:–—]\s+(?P<body>[A-Z(\[].*)$"
)


def extract_definitions(builder: PdfRowBuilder, section: Section) -> list[Row]:
    """One row per glossary term.

    Definitions wrap across lines, so a new term only starts when a line matches
    the term pattern; everything else continues the previous definition.
    """
    rows: list[Row] = []
    current_term: str | None = None
    current_body: list[str] = []
    path = " > ".join(
        clean_heading(part) for part in [*clean_trail(section.trail), section.heading] if part
    )

    def flush() -> None:
        if current_term is None:
            return
        body = " ".join(current_body).strip()
        text = f"{current_term}: {body}"
        # "INFORMATION: An instance of an information type." is a whole
        # definition at 48 chars; only fragments are dropped here.
        if len(text) < 40:
            builder.reject(
                ref=current_term, rule="definition_too_short",
                detail=f"{len(text)} chars on page {section.page}",
            )
            return
        rows.append(builder.row("definition", current_term, text, f"{path} > {current_term}"))

    for line in section.lines:
        match = DEFINITION_RE.match(line.text.strip())
        if match:
            flush()
            current_term = re.sub(r"\s+", " ", match.group("term")).strip()
            current_body = [match.group("body").strip()]
        elif current_term is not None:
            current_body.append(line.text.strip())
    flush()
    return rows


# --- tables ------------------------------------------------------------------

def extract_grid(pdf_path: Path, page_number: int) -> list[list[str]]:
    """Return the largest table on a page as a grid, spacer columns removed.

    PyMuPDF's table finder emits empty columns where a table uses wide cell
    padding; they carry no data and would produce blank cells in the transcript.
    """
    import pymupdf

    with pymupdf.open(pdf_path) as document:
        page = document[page_number - 1]
        found = page.find_tables()
        if not found.tables:
            return []
        grid = max(found.tables, key=lambda t: len(t.extract())).extract()

    grid = [[(cell or "").strip() for cell in row] for row in grid]
    keep = [
        index for index in range(len(grid[0]))
        if any(row[index] for row in grid if index < len(row))
    ]
    return [[row[index] if index < len(row) else "" for index in keep] for row in grid]


def find_pages_containing(lines: list[Line], pattern: re.Pattern[str]) -> list[int]:
    return sorted({line.page for line in lines if pattern.search(line.text)})


# --- per-document handlers ---------------------------------------------------

@dataclass
class ParseResult:
    rows: list[Row]
    rejections: list[Rejection]
    page_count: int


FIPS199_TABLE_RE = re.compile(r"TABLE 1:\s*POTENTIAL IMPACT DEFINITIONS", re.IGNORECASE)


def handle_fips_199(
    builder: PdfRowBuilder, pdf_path: Path, lines: list[Line], sections: list[Section]
) -> list[Row]:
    """Sections, plus the C/I/A x impact-level matrix as one dedicated row."""
    rows = emit_sections(builder, [s for s in sections if not GLOSSARY_HEADING_RE.search(s.heading)])

    for section in sections:
        if GLOSSARY_HEADING_RE.search(section.heading):
            rows.extend(extract_definitions(builder, section))

    pages = find_pages_containing(lines, FIPS199_TABLE_RE)
    if not pages:
        builder.reject(ref="Table 1", rule="table_not_found",
                       detail="could not locate 'TABLE 1: POTENTIAL IMPACT DEFINITIONS'")
        return rows

    grid = extract_grid(pdf_path, pages[-1])
    transcript = transcribe_impact_table(grid)
    if transcript is None:
        builder.reject(ref="Table 1", rule="extraction_failure",
                       detail=f"impact table on page {pages[-1]} did not extract as a usable grid")
        return rows

    text = (
        "Table 1: Potential Impact Definitions for Security Objectives (FIPS 199).\n"
        "The potential impact on each security objective, by impact level:\n\n" + transcript
    )
    rows.append(builder.row("table", "table-1-potential-impact-definitions", text,
                            "Table 1 > Potential Impact Definitions for Security Objectives"))
    return rows


def transcribe_impact_table(grid: list[list[str]]) -> str | None:
    """Render the FIPS 199 matrix as one line per cell: '{objective} / {level}: …'.

    A matrix is unreadable once flattened into prose, so each cell is emitted
    with its row and column labels attached.
    """
    objectives = ("Confidentiality", "Integrity", "Availability")
    levels = ("LOW", "MODERATE", "HIGH")
    lines: list[str] = []

    for row in grid:
        if not row:
            continue
        first = re.sub(r"\s+", " ", row[0]).strip()
        objective = next((o for o in objectives if first.upper().startswith(o.upper())), None)
        if objective is None:
            continue
        # The label cell also carries the objective's statutory definition.
        definition = first[len(objective):].strip(" .:-")
        if definition:
            lines.append(f"{objective} (definition): {re.sub(r'\\s+', ' ', definition)}")
        cells = [re.sub(r"\s+", " ", cell).strip() for cell in row[1:] if cell.strip()]
        for level, cell in zip(levels, cells):
            lines.append(f"{objective} / {level}: {cell}")

    if len({line.split(" /")[0] for line in lines}) < 3:
        return None
    return "\n".join(lines)


# --- line joining ------------------------------------------------------------

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’-]*")


def build_vocabulary(lines: list[Line]) -> set[str]:
    """Every word the document uses, lowercased. Used to settle hyphenation."""
    vocabulary: set[str] = set()
    for line in lines:
        vocabulary.update(word.lower() for word in _WORD_RE.findall(line.text))
    return vocabulary


def join_lines(texts: list[str], vocabulary: set[str] | None = None) -> str:
    """Join wrapped lines, resolving line-final hyphens against the document.

    Justified two-column text (AI 100-1) breaks words mid-syllable: "inte-" +
    "grated". A line-final hyphen can also be a real compound hyphen ("risk-" +
    "based"). The document itself settles it: if the document uses "integrated"
    elsewhere, join; if it uses "risk-based", keep the hyphen.
    """
    joined = ""
    for raw in texts:
        text = raw.strip()
        if not text:
            continue
        if not joined:
            joined = text
            continue
        if joined.endswith("-") and vocabulary is not None:
            prefix_match = re.search(r"([A-Za-z]+)-$", joined)
            suffix_match = re.match(r"([a-z]+)", text)
            if prefix_match and suffix_match:
                prefix, suffix = prefix_match.group(1), suffix_match.group(1)
                merged = f"{prefix}{suffix}".lower()
                hyphenated = f"{prefix}-{suffix}".lower()
                if merged in vocabulary and hyphenated not in vocabulary:
                    joined = joined[:-1] + text
                    continue
                if hyphenated in vocabulary:
                    joined = f"{joined}{text}"
                    continue
                # Unseen either way: a line-final hyphen is far more often a
                # wrap than a compound, so join.
                joined = joined[:-1] + text
                continue
        joined = f"{joined} {text}"
    return joined


# --- per-identifier blocks ---------------------------------------------------

@dataclass
class Block:
    key: str                  # slug source for the row id
    heading: str
    path: str
    page: int
    lines: list[str] = field(default_factory=list)
    label: str = ""           # how the block is named in section_path

    def __post_init__(self) -> None:
        if not self.label:
            self.label = self.key


def extract_blocks(
    sections: list[Section],
    pattern: re.Pattern[str],
    stop_pattern: re.Pattern[str] | None = None,
    max_span_level: int = 99,
) -> list[Block]:
    """Split the document into blocks that each start at an identifier line.

    Used for the documents whose real structure is a per-identifier list —
    RMF tasks, AI RMF subcategories, SSDF practices, SP 800-60 information
    types — where PDF section headings are not the unit anyone wants.
    """
    blocks: list[Block] = []
    current: Block | None = None
    for section in sections:
        # A block ends at a major heading. Without any bound, the last
        # identifier in a list absorbs every appendix that follows it (MANAGE
        # 4.3 once ran to 22,905 chars); with a bound at *every* heading, a
        # spurious mid-list heading truncates a real entry instead. Sections
        # deeper than max_span_level are absorbed, major ones close the block.
        if section.level <= max_span_level:
            current = None
        trail_path = " > ".join(clean_heading(part) for part in clean_trail(section.trail) if part)
        full_path = " > ".join(
            part for part in [trail_path, clean_heading(section.heading)] if part
        )
        heading_line = Line(section.heading, section.page, 0, False, 0)
        for line in [heading_line, *section.lines]:
            text = line.text.strip()
            if not text:
                continue
            match = pattern.match(text)
            if match:
                # An identifier found on the section heading itself would
                # otherwise repeat in the path.
                path = trail_path if line is heading_line else full_path
                current = Block(key=match.group(1), heading=text, path=path, page=line.page)
                blocks.append(current)
                continue
            if current is not None:
                if stop_pattern is not None and stop_pattern.match(text):
                    current = None
                    continue
                current.lines.append(text)
                continue
    return blocks


def keep_longest_per_key(blocks: list[Block]) -> tuple[list[Block], list[Block]]:
    """Identifiers recur in summary tables and contents listings; the real
    occurrence is the longest one. Returns (kept, discarded)."""
    best: dict[str, Block] = {}
    for block in blocks:
        existing = best.get(block.key)
        if existing is None or len(" ".join(block.lines)) > len(" ".join(existing.lines)):
            best[block.key] = block
    kept = list(best.values())
    discarded = [block for block in blocks if block not in kept]
    return kept, discarded


def emit_blocks(
    builder: PdfRowBuilder,
    blocks: list[Block],
    chunk_type: str,
    vocabulary: set[str] | None = None,
    min_chars: int = 120,
) -> list[Row]:
    rows: list[Row] = []
    for block in blocks:
        text = join_lines([block.heading, *block.lines], vocabulary)
        if len(text) < min_chars:
            builder.reject(
                ref=block.key, rule="block_too_short",
                detail=f"{len(text)} chars on page {block.page}",
            )
            continue
        path = f"{block.path} > {block.label}" if block.path else block.label
        rows.append(builder.row(chunk_type, block.key, text, path))
    return rows


# --- SP 800-37r2: RMF tasks --------------------------------------------------

TASK_RE = re.compile(r"^TASK\s+([PCSIARM]-\d+)\b")
# A task block ends at the next task or at a chapter/appendix heading.
TASK_STOP_RE = re.compile(r"^(CHAPTER|APPENDIX)\s", re.IGNORECASE)


def layout_context(lines: list[Line]) -> tuple[float, set[str], frozenset[int]]:
    """Recompute the layout signals a handler needs.

    Cheap enough to redo inside a handler, and keeps the handler signature the
    same for every document.
    """
    page_count = max((line.page for line in lines), default=1)
    body_size = body_font_size(lines)
    repeating = find_repeating_furniture(lines, page_count)
    contents_pages = frozenset(find_contents_pages(lines, page_count))
    return body_size, repeating, contents_pages


def find_appendix_span(
    lines: list[Line], title: str, body_size: float, min_size_delta: float = 3.0
) -> tuple[int, int] | None:
    """Page range of an appendix identified by its display title.

    An appendix title is set much larger than body text, so the next line at
    that size ends the span. Returns None when the title never appears at title
    size, which means the assumption behind the caller no longer holds.
    """
    start: int | None = None
    for line in lines:
        text = line.text.strip()
        is_title_size = line.size >= body_size + min_size_delta
        if start is None:
            if is_title_size and text.upper() == title.upper():
                start = line.page
            continue
        if is_title_size and line.page > start:
            return (start, line.page - 1)
    if start is None:
        return None
    return (start, max(line.page for line in lines))


# A source line opens with a bracket and is set smaller than the body. The
# closing bracket may be on the next line ("[OMB Circular A-130," / "Appendix
# III]"), so only the opening is required.
SOURCE_LINE_RE = re.compile(r"^\[[^\]]{2,}")

# A bare running-header line inside an appendix: "APPENDIX B", "PAGE B-2".
HEADER_ONLY_LINE_RE = re.compile(r"^(APPENDIX|CHAPTER|PAGE)\s+[A-Za-z0-9-]{1,10}$", re.IGNORECASE)


def _starts_a_term(
    line: Line, next_line: Line | None, body_size: float
) -> bool:
    """Whether this line opens a new glossary entry.

    Two publications, two typographic conventions for the same structure:
    SP 800-37 sets its terms in bold, while SP 800-137 sets them in the body
    face and marks each entry with a smaller `[SOURCE]` line underneath. Both
    are font signals, because neither glossary punctuates its terms.
    """
    text = line.text.strip()
    if not text or len(text) > 80:
        return False
    at_body_size = abs(line.size - body_size) < 0.5
    if not at_body_size:
        return False
    if line.bold:
        return True
    return (
        next_line is not None
        and next_line.size < body_size - 0.3
        and SOURCE_LINE_RE.match(next_line.text.strip()) is not None
    )


def extract_term_definitions(
    builder: PdfRowBuilder,
    lines: list[Line],
    body_size: float,
    span: tuple[int, int],
    path: str,
    repeating: set[str],
    contents_pages: frozenset[int],
) -> list[Row]:
    """One row per term for a glossary whose entries are marked by type, not
    punctuation."""
    span_lines = [
        line for line in lines
        if span[0] <= line.page <= span[1]
        and line.text.strip()
        and not is_furniture(line, repeating, contents_pages)
        # An appendix running header repeats on too few pages to be caught by
        # frequency, and would otherwise be swept into the next term's name.
        and not HEADER_ONLY_LINE_RE.match(line.text.strip())
    ]

    rows: list[Row] = []
    current_term: str | None = None
    current_body: list[str] = []

    def flush() -> None:
        if current_term is None:
            return
        body = join_lines(current_body).strip()
        text = f"{current_term}: {body}" if body else current_term
        if len(text) < 40:
            builder.reject(current_term, "definition_too_short", f"{len(text)} chars")
            return
        rows.append(builder.row("definition", current_term, text, f"{path} > {current_term}"))

    for index, line in enumerate(span_lines):
        text = line.text.strip()
        next_line = span_lines[index + 1] if index + 1 < len(span_lines) else None

        if _starts_a_term(line, next_line, body_size):
            if current_term is not None and not current_body:
                # A long term wraps onto a second line ("authorizing official" /
                # "designated representative"); it is one term, not two, and
                # splitting it leaves the first half with no body.
                current_term = f"{current_term} {text}"
                continue
            # When the term is detected by its source line, earlier lines of a
            # wrapped term are already sitting in the body; pull them back.
            carried: list[str] = []
            while (
                current_body
                and len(carried) < 2
                and len(current_body[-1]) <= 60
                and not current_body[-1].endswith((".", ";", ":"))
            ):
                carried.insert(0, current_body.pop())
            flush()
            current_term = " ".join([*carried, text])
            current_body = []
        elif current_term is not None and line.size <= body_size:
            current_body.append(text)
    flush()
    return rows


def handle_bold_glossary_document(
    builder: PdfRowBuilder,
    lines: list[Line],
    sections: list[Section],
    title: str,
) -> tuple[list[Row], tuple[int, int] | None]:
    """Extract a type-marked glossary appendix and report its page span so the
    caller can stop emitting those pages as sections."""
    body_size, repeating, contents_pages = layout_context(lines)
    span = find_appendix_span(lines, title, body_size)
    if span is None:
        builder.reject(title, "glossary_not_found",
                       f"no '{title}' appendix title found at title size")
        return [], None
    rows = extract_term_definitions(
        builder, lines, body_size, span,
        f"APPENDIX > {title}", repeating, contents_pages,
    )
    return rows, span


def handle_sp_800_37r2(
    builder: PdfRowBuilder, pdf_path: Path, lines: list[Line], sections: list[Section]
) -> list[Row]:
    """Chapter 3 tasks become `task` rows; everything else stays a section."""
    blocks = extract_blocks(sections, TASK_RE, TASK_STOP_RE)
    kept, discarded = keep_longest_per_key(blocks)
    for block in discarded:
        builder.reject(
            ref=block.key, rule="duplicate_task_stub",
            detail=f"shorter duplicate of {block.key} on page {block.page} (summary table or contents)",
        )
    for block in kept:
        block.label = f"TASK {block.key}"
    rows = emit_blocks(builder, sorted(kept, key=lambda b: (b.key[0], int(b.key.split("-")[1]))),
                       "task", min_chars=200)

    glossary_rows, glossary_span = handle_bold_glossary_document(
        builder, lines, sections, "GLOSSARY"
    )
    rows.extend(glossary_rows)

    # Sections that only exist to hold task blocks would duplicate them, and the
    # glossary appendix is now covered term by term.
    task_pages = {block.page for block in kept}
    remaining = [
        section for section in sections
        if not TASK_RE.match(section.heading)
        and section.page not in task_pages
        and not (glossary_span and glossary_span[0] <= section.page <= glossary_span[1])
    ]
    rows.extend(_sections_and_glossary(builder, remaining))
    return rows


def _sections_and_glossary(builder: PdfRowBuilder, sections: list[Section]) -> list[Row]:
    rows: list[Row] = []
    for section in sections:
        if GLOSSARY_HEADING_RE.search(section.heading):
            rows.extend(extract_definitions(builder, section))
        else:
            rows.extend(emit_sections(builder, [section]))
    return rows


# --- FIPS 200: the 17 security-related areas ---------------------------------

# FIPS 200 names 17 areas; the two-letter codes anchor the match so that an
# ordinary parenthetical cannot be mistaken for one.
FIPS200_AREA_CODES = (
    "AC", "AT", "AU", "CA", "CM", "CP", "IA", "IR", "MA",
    "MP", "PE", "PL", "PS", "RA", "SA", "SC", "SI",
)
FIPS200_AREA_RE = re.compile(
    r"^[A-Z][A-Za-z, /and]+\s\((" + "|".join(FIPS200_AREA_CODES) + r")\):\s*\S"
)


def handle_fips_200(
    builder: PdfRowBuilder, pdf_path: Path, lines: list[Line], sections: list[Section]
) -> list[Row]:
    blocks = extract_blocks(sections, FIPS200_AREA_RE)
    kept, discarded = keep_longest_per_key(blocks)
    for block in discarded:
        builder.reject(ref=block.key, rule="duplicate_area_stub",
                       detail=f"shorter duplicate of area {block.key} on page {block.page}")
    rows = emit_blocks(builder, kept, "section", min_chars=150)

    area_pages = {block.page for block in kept}
    remaining = [s for s in sections if s.page not in area_pages]
    rows.extend(_sections_and_glossary(builder, remaining))
    return rows


# --- AI 100-1: AI RMF subcategories ------------------------------------------

AI_SUBCATEGORY_RE = re.compile(r"^((?:GOVERN|MAP|MEASURE|MANAGE)\s+\d+\.\d+):")


def handle_ai_100_1(
    builder: PdfRowBuilder, pdf_path: Path, lines: list[Line], sections: list[Section]
) -> list[Row]:
    vocabulary = build_vocabulary(lines)
    # AI 100-1 sets bold run-in labels inside the Core tables that read as
    # minor headings; a block must be allowed to span those.
    blocks = extract_blocks(sections, AI_SUBCATEGORY_RE, max_span_level=2)
    kept, discarded = keep_longest_per_key(blocks)
    for block in discarded:
        builder.reject(ref=block.key, rule="duplicate_subcategory_stub",
                       detail=f"shorter duplicate of {block.key} on page {block.page}")
    # AI RMF subcategory statements are one sentence by design ("GOVERN 1.1:
    # Legal and regulatory requirements involving AI are understood, managed,
    # and documented." is the whole thing, 99 chars). The guard here only
    # catches fragments; the published floor is Stage 6's business.
    rows = emit_blocks(builder, kept, "ai_rmf_subcategory", vocabulary, min_chars=60)

    subcategory_pages = {block.page for block in kept}
    remaining = [s for s in sections if s.page not in subcategory_pages]
    rows.extend(_sections_and_glossary(builder, remaining))
    return rows


# --- SSDF practices ----------------------------------------------------------

SSDF_PRACTICE_RE = re.compile(r"^((?:PO|PS|PW|RV)\.\d+(?:\.\d+)?):\s*\S")


def handle_ssdf(
    builder: PdfRowBuilder, pdf_path: Path, lines: list[Line], sections: list[Section]
) -> list[Row]:
    vocabulary = build_vocabulary(lines)
    blocks = extract_blocks(sections, SSDF_PRACTICE_RE, max_span_level=2)
    kept, discarded = keep_longest_per_key(blocks)
    for block in discarded:
        builder.reject(ref=block.key, rule="duplicate_practice_stub",
                       detail=f"shorter duplicate of {block.key} on page {block.page}")
    # The SSDF's own identifier carries its hierarchy (PO.1.1 sits under
    # practice PO.1 in group PO), which is more reliable than the heading trail
    # in a document laid out almost entirely as wide tables.
    for block in kept:
        parts = block.key.split(".")
        block.path = " > ".join(
            ["SSDF Practices", parts[0], ".".join(parts[:2])][: len(parts) + 1]
        )
        block.label = block.key
    rows = emit_blocks(builder, kept, "ssdf_practice", vocabulary, min_chars=60)

    practice_pages = {block.page for block in kept}
    remaining = [s for s in sections if s.page not in practice_pages]
    rows.extend(_sections_and_glossary(builder, remaining))
    return rows


# --- SP 800-60 Volume 2: information types -----------------------------------

INFO_TYPE_RE = re.compile(r"^(D\.\d+(?:\.\d+)*)\s+(?=\S)")


# SP 800-60 Volume 2's Appendix E reproduces OMB memoranda and legislative
# provisions as wide reference tables. It is source material for the impact
# determinations, not guidance, and it extracts as citation soup — so it is
# excluded whole rather than published as damaged sections.
EXCLUDED_APPENDIX_RE = re.compile(r"^APPENDIX\s+E\b", re.IGNORECASE)


def find_excluded_appendix_start(
    lines: list[Line], pattern: re.Pattern[str], contents_pages: frozenset[int]
) -> int | None:
    """First body page of an excluded appendix, ignoring its contents entries."""
    for line in lines:
        if line.page in contents_pages or _TOC_RE.search(line.text):
            continue
        if pattern.match(line.text.strip()):
            return line.page
    return None


def handle_sp_800_60v2(
    builder: PdfRowBuilder, pdf_path: Path, lines: list[Line], sections: list[Section]
) -> list[Row]:
    """One row per information type, carrying its provisional categorization.

    Appendix E is dropped entirely; each excluded section is logged so the
    exclusion is auditable rather than invisible.
    """
    _, _, contents_pages = layout_context(lines)
    excluded_from = find_excluded_appendix_start(lines, EXCLUDED_APPENDIX_RE, contents_pages)
    if excluded_from is not None:
        last_page = max(line.page for line in lines)
        excluded = [s for s in sections if s.page >= excluded_from]
        for section in excluded:
            builder.reject(
                ref=f"p{section.page}:{clean_heading(section.heading)[:60]}",
                rule="excluded_appendix",
                detail=(
                    f"SP 800-60 Vol 2 Appendix E (OMB memoranda and legal-provision "
                    f"tables), pages {excluded_from}-{last_page}, excluded from v1"
                ),
            )
        sections = [s for s in sections if s.page < excluded_from]
        lines = [line for line in lines if line.page < excluded_from]

    blocks = extract_blocks(sections, INFO_TYPE_RE)
    kept, discarded = keep_longest_per_key(blocks)
    for block in discarded:
        builder.reject(ref=block.key, rule="duplicate_information_type_stub",
                       detail=f"shorter duplicate of {block.key} on page {block.page}")
    rows = emit_blocks(builder, kept, "section", min_chars=200)

    info_pages = {block.page for block in kept}
    remaining = [s for s in sections if s.page not in info_pages]
    rows.extend(_sections_and_glossary(builder, remaining))
    return rows


def handle_sp_800_137(
    builder: PdfRowBuilder, pdf_path: Path, lines: list[Line], sections: list[Section]
) -> list[Row]:
    """Sections, plus Appendix B's glossary as `definition` rows.

    SP 800-137 marks its terms with a smaller `[SOURCE]` line rather than bold
    type, but the entry structure is the same one SP 800-37 uses.
    """
    rows, span = handle_bold_glossary_document(builder, lines, sections, "GLOSSARY")
    remaining = [
        section for section in sections
        if not (span and span[0] <= section.page <= span[1])
    ]
    rows.extend(_sections_and_glossary(builder, remaining))
    return rows


def handle_default(
    builder: PdfRowBuilder, pdf_path: Path, lines: list[Line], sections: list[Section]
) -> list[Row]:
    """Section-level chunking plus glossary terms — the baseline treatment."""
    rows: list[Row] = []
    for section in sections:
        if GLOSSARY_HEADING_RE.search(section.heading):
            rows.extend(extract_definitions(builder, section))
        else:
            rows.extend(emit_sections(builder, [section]))
    return rows


# doc_id -> handler. Anything not listed gets section-level chunking, which is
# also the explicit v1 treatment for CNSSI-1253 and DoDI-8510.01.
HANDLERS = {
    "FIPS-199": handle_fips_199,
    "FIPS-200": handle_fips_200,
    "SP-800-37r2": handle_sp_800_37r2,
    "SP-800-60v2r1": handle_sp_800_60v2,
    "SP-800-137": handle_sp_800_137,
    "AI-100-1": handle_ai_100_1,
    "SP-800-218": handle_ssdf,
    "SP-800-218A": handle_ssdf,
}


def parse_pdf_document(doc: Document, pdf_path: Path, sha256_source: str) -> ParseResult:
    """Parse one PDF into rows using its document-specific handler."""
    lines, page_count = extract_lines(pdf_path)
    if not lines:
        builder = PdfRowBuilder(doc, sha256_source)
        builder.reject(ref=doc.doc_id, rule="extraction_failure", detail="no text extracted")
        return ParseResult([], builder.rejections, page_count)

    body_size = body_font_size(lines)
    repeating = find_repeating_furniture(lines, page_count)
    contents_pages = frozenset(find_contents_pages(lines, page_count))
    sections = build_sections(lines, body_size, repeating, contents_pages)

    builder = PdfRowBuilder(doc, sha256_source)
    sections = drop_front_matter(
        sections, find_body_start_page(lines, body_size, contents_pages), builder
    )

    handler = HANDLERS.get(doc.doc_id, handle_default)
    rows = handler(builder, pdf_path, lines, sections)
    return ParseResult(rows, builder.rejections, page_count)
