"""Manifest loading, schema validation, and Stage 1 URL/supersession verification.

The manifest is the sole authority on what is in scope. Nothing downstream may
invent a document, so this module's job is to prove — before a single byte is
fetched — that every entry is well formed and every URL still resolves.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

VALID_FORMATS = {"oscal", "pdf", "embedded-in-oscal", "pdf-manual", "web"}

REQUIRED_FIELDS = (
    "doc_id", "title", "revision", "pub_date", "tier",
    "format", "url", "landing_page", "status", "license", "notes",
)

USER_AGENT = "rmf-ato-core-builder/1.0 (+github.com/ezesecops/rmf-ato-core)"

# Be a good citizen: at least this many seconds between requests to one host.
MIN_SECONDS_BETWEEN_REQUESTS = 2.0

# Documents whose manifest notes warn that a newer revision may have gone final
# (BUILD_SPEC §5.3). Kept explicit so the check cannot silently stop running,
# and supplemented by a notes scan so manifest edits are picked up too.
SUPERSESSION_WATCHLIST = {
    "SP-800-60v1r1",
    "SP-800-60v2r1",
    "SP-800-18r1",
    "SP-800-218",
}

_NOTES_WATCH_RE = re.compile(
    r"check (?:the )?landing page|if rev\w* \d+ is final|in development|was in draft",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Document:
    doc_id: str
    title: str
    revision: str
    pub_date: str
    tier: int
    format: str
    url: str | None
    landing_page: str | None
    status: str
    license: str
    notes: str

    @property
    def needs_supersession_check(self) -> bool:
        return (
            self.doc_id in SUPERSESSION_WATCHLIST
            or bool(_NOTES_WATCH_RE.search(self.notes or ""))
        )

    @property
    def effective_source_url(self) -> str:
        """The URL recorded on rows: the artifact URL, or the landing page for
        manual/embedded documents that have no direct artifact URL."""
        return self.url or self.landing_page or ""


@dataclass(frozen=True)
class Manifest:
    manifest_version: str
    dataset_name: str
    maintainer: str
    documents: tuple[Document, ...]
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    def by_id(self, doc_id: str) -> Document | None:
        for doc in self.documents:
            if doc.doc_id == doc_id:
                return doc
        return None

    def __contains__(self, doc_id: object) -> bool:
        return self.by_id(str(doc_id)) is not None


def load_manifest(path: str | Path) -> Manifest:
    """Load and schema-validate the manifest. Raises on any structural problem."""
    path = Path(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    problems = validate_manifest_dict(raw)
    if problems:
        raise ValueError(
            "manifest failed validation:\n  - " + "\n  - ".join(problems)
        )
    documents = tuple(
        Document(**{key: entry.get(key) for key in REQUIRED_FIELDS})
        for entry in raw["documents"]
    )
    return Manifest(
        manifest_version=raw["manifest_version"],
        dataset_name=raw["dataset_name"],
        maintainer=raw["maintainer"],
        documents=documents,
        raw=raw,
    )


def validate_manifest_dict(raw: dict[str, Any]) -> list[str]:
    """Return a list of human-readable problems; empty means valid."""
    problems: list[str] = []

    for key in ("manifest_version", "dataset_name", "maintainer", "documents"):
        if key not in raw:
            problems.append(f"top level: missing '{key}'")
    if not isinstance(raw.get("documents"), list) or not raw.get("documents"):
        problems.append("top level: 'documents' must be a non-empty list")
        return problems

    seen_ids: set[str] = set()
    for index, entry in enumerate(raw["documents"]):
        label = entry.get("doc_id", f"documents[{index}]")
        for key in REQUIRED_FIELDS:
            if key not in entry:
                problems.append(f"{label}: missing field '{key}'")
        if entry.get("doc_id") in seen_ids:
            problems.append(f"{label}: duplicate doc_id")
        seen_ids.add(entry.get("doc_id"))

        fmt = entry.get("format")
        if fmt not in VALID_FORMATS:
            problems.append(f"{label}: format {fmt!r} not in {sorted(VALID_FORMATS)}")

        tier = entry.get("tier")
        if tier not in (1, 2):
            problems.append(f"{label}: tier must be 1 or 2, got {tier!r}")

        for url_field in ("url", "landing_page"):
            value = entry.get(url_field)
            if value is None:
                continue
            if not isinstance(value, str) or not value.startswith("https://"):
                problems.append(f"{label}: {url_field} must be https or null, got {value!r}")

        # A fetchable format needs something to fetch.
        if fmt in {"oscal", "pdf", "web"} and not entry.get("url"):
            problems.append(f"{label}: format {fmt!r} requires a url")
        # A manual document needs a landing page so the human knows where to go.
        if fmt == "pdf-manual" and not entry.get("landing_page"):
            problems.append(f"{label}: format 'pdf-manual' requires a landing_page")

    return problems


# --- Stage 1: live checks ----------------------------------------------------

@dataclass
class UrlCheck:
    url: str | None
    status: int | None
    method: str
    note: str = ""
    resolved_url: str | None = None  # set when an alternate pattern worked

    @property
    def ok(self) -> bool:
        return self.status is not None and 200 <= self.status < 300


@dataclass
class SupersessionFinding:
    checked: bool
    detail: str


@dataclass
class DocReport:
    doc: Document
    url_check: UrlCheck
    supersession: SupersessionFinding


class PoliteClient:
    """httpx client wrapper that spaces out requests per host.

    This pipeline hits nist.gov roughly a dozen times in total; there is no
    excuse for hammering it, so the rate limit lives in the transport layer
    where no caller can forget it.
    """

    def __init__(self, timeout: float = 30.0, min_interval: float = MIN_SECONDS_BETWEEN_REQUESTS):
        import httpx

        self._client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )
        self._min_interval = min_interval
        self._last_request_at: dict[str, float] = {}

    def _wait_turn(self, url: str) -> None:
        host = urlparse(url).netloc
        last = self._last_request_at.get(host)
        if last is not None:
            elapsed = time.monotonic() - last
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
        self._last_request_at[host] = time.monotonic()

    def head(self, url: str):
        self._wait_turn(url)
        return self._client.head(url)

    def get(self, url: str, **kwargs):
        self._wait_turn(url)
        return self._client.get(url, **kwargs)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "PoliteClient":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


def _alternate_nvlpubs_urls(url: str) -> list[str]:
    """Guess the other nvlpubs path pattern for an SP.

    Pre-2015 publications live under /nistpubs/Legacy/SP/nistspecialpublication{n}.pdf
    while newer ones use /nistpubs/SpecialPublications/NIST.SP.{n}.pdf. A 404 on
    one pattern is usually just the wrong era, not a missing document.
    """
    modern = re.search(r"/nistpubs/SpecialPublications/NIST\.SP\.(.+)\.pdf$", url)
    if modern:
        number = modern.group(1)
        return [
            f"https://nvlpubs.nist.gov/nistpubs/Legacy/SP/nistspecialpublication{number.replace('-', '')}.pdf",
            f"https://nvlpubs.nist.gov/nistpubs/Legacy/SP/nistspecialpublication{number}.pdf",
        ]
    legacy = re.search(r"/nistpubs/Legacy/SP/nistspecialpublication(.+)\.pdf$", url)
    if legacy:
        number = legacy.group(1)
        # Legacy names drop the hyphen after "800"; restore it for the modern form.
        with_hyphen = re.sub(r"^800(?!-)", "800-", number)
        return [f"https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.{with_hyphen}.pdf"]
    return []


def check_url(client: PoliteClient, doc: Document) -> UrlCheck:
    """Probe a document's URL, escalating through the fallbacks in BUILD_SPEC §5.

    HEAD (or ranged GET if HEAD is refused) -> the other nvlpubs path pattern ->
    the PDF link advertised on the document's own landing page. A resolved
    alternate is reported, never silently written back to the manifest.
    """
    url = doc.url
    if url is None:
        return UrlCheck(
            url=None, status=None, method="none",
            note="no url (manual or embedded source)",
        )

    check = _probe(client, url)
    if check.ok:
        return check

    original_status = check.status
    for alternate in _alternate_nvlpubs_urls(url):
        alt_check = _probe(client, alternate)
        if alt_check.ok:
            alt_check.resolved_url = alternate
            alt_check.note = (
                f"original URL returned {original_status}; alternate nvlpubs pattern works"
            )
            return alt_check

    landing_check = _resolve_from_landing_page(client, doc)
    if landing_check is not None:
        landing_check.note = (
            f"original URL returned {original_status}; link resolved from landing page"
        )
        return landing_check

    check.note = (check.note + " " if check.note else "") + (
        "alternate nvlpubs pattern and landing-page resolution both failed"
    )
    return check


_HREF_RE = re.compile(r"""href=["']([^"']+?\.pdf)["']""", re.IGNORECASE)


def _doc_number_key(doc: Document) -> str:
    """The publication number as bare alphanumerics: SP-800-60v1r1 -> 80060v1r1.

    Used only to rank candidate links found on a landing page, so a loose match
    is fine — every candidate is probed before being reported.
    """
    _, _, rest = doc.doc_id.partition("-")
    return re.sub(r"[^a-z0-9]", "", (rest or doc.doc_id).lower())


def find_pdf_links(html: str, base_url: str, doc: Document) -> list[str]:
    """Absolute https PDF links from a landing page, best candidates first."""
    from urllib.parse import urljoin

    key = _doc_number_key(doc)
    seen: set[str] = set()
    scored: list[tuple[int, str]] = []
    for href in _HREF_RE.findall(html):
        absolute = urljoin(base_url, href)
        if not absolute.startswith("https://") or absolute in seen:
            continue
        seen.add(absolute)
        flat = re.sub(r"[^a-z0-9]", "", absolute.lower())
        score = (2 if key and key in flat else 0) + (1 if "nvlpubs.nist.gov" in absolute else 0)
        if score:
            scored.append((score, absolute))
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    return [url for _, url in scored]


def _resolve_from_landing_page(
    client: PoliteClient, doc: Document, max_candidates: int = 3
) -> UrlCheck | None:
    if not doc.landing_page:
        return None
    try:
        response = client.get(doc.landing_page)
    except Exception:
        return None
    if response.status_code != 200:
        return None

    for candidate in find_pdf_links(response.text, doc.landing_page, doc)[:max_candidates]:
        candidate_check = _probe(client, candidate)
        if candidate_check.ok:
            candidate_check.resolved_url = candidate
            return candidate_check
    return None


def _probe(client: PoliteClient, url: str) -> UrlCheck:
    """HEAD first, then a ranged GET on any non-2xx.

    nvlpubs.nist.gov's CDN answers HEAD with 404 for files that a ranged GET
    serves as 206, so a failed HEAD proves nothing on its own. The ranged GET
    confirms existence without downloading the whole artifact.
    """
    import httpx

    try:
        response = client.head(url)
        head_status = response.status_code
        if 200 <= head_status < 300:
            return UrlCheck(url=url, status=head_status, method="HEAD")
    except httpx.HTTPError as exc:
        head_status = None
        head_note = f"HEAD failed: {exc}"
    else:
        head_note = f"HEAD returned {head_status}"

    try:
        response = client.get(url, headers={"Range": "bytes=0-1023"})
    except httpx.HTTPError as exc:
        return UrlCheck(url=url, status=head_status, method="HEAD",
                        note=f"{head_note}; ranged GET failed: {exc}")

    check = UrlCheck(url=url, status=response.status_code, method="GET (range)")
    if check.ok:
        # Only worth reporting when the two methods disagree.
        check.note = f"{head_note}, confirmed by ranged GET"
    return check


_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"<(script|style)\b.*?</\1>", re.IGNORECASE | re.DOTALL)


def html_to_text(html: str) -> str:
    text = _SCRIPT_RE.sub(" ", html)
    text = _TAG_RE.sub(" ", text)
    text = (
        text.replace("&nbsp;", " ").replace("&amp;", "&")
        .replace("&lt;", "<").replace("&gt;", ">").replace("&#39;", "'")
        .replace("&quot;", '"')
    )
    return re.sub(r"\s+", " ", text).strip()


_REV_RE = re.compile(r"\bRev(?:ision)?\.?\s*(\d+)\b", re.IGNORECASE)


def check_supersession(client: PoliteClient, doc: Document) -> SupersessionFinding:
    """Best-effort read of the landing page for supersession signals.

    Deliberately reports rather than decides: swapping a manifest entry is the
    maintainer's call (curation rule 1).
    """
    if not doc.landing_page:
        return SupersessionFinding(False, "no landing page to check")

    try:
        response = client.get(doc.landing_page)
    except Exception as exc:  # network problems are a finding, not a crash
        return SupersessionFinding(False, f"landing page fetch failed: {exc}")

    if response.status_code != 200:
        return SupersessionFinding(False, f"landing page HTTP {response.status_code}")

    text = html_to_text(response.text)
    lowered = text.lower()
    signals: list[str] = []

    if "withdrawn" in lowered:
        signals.append("page mentions 'withdrawn'")
    if "superseded" in lowered:
        # Capture a little context so the maintainer can judge it.
        match = re.search(r".{80}superseded.{120}", lowered, re.DOTALL)
        snippet = match.group(0).strip() if match else "superseded"
        signals.append(f"page mentions 'superseded': …{snippet}…")

    manifest_rev = _REV_RE.search(doc.revision or "")
    manifest_rev_num = int(manifest_rev.group(1)) if manifest_rev else None
    page_revs = {int(m) for m in _REV_RE.findall(text)}
    if manifest_rev_num is not None and page_revs:
        higher = sorted(r for r in page_revs if r > manifest_rev_num)
        if higher:
            signals.append(
                f"page mentions higher revision(s) {higher} than manifest Rev {manifest_rev_num}"
            )

    if not signals:
        return SupersessionFinding(True, "no supersession signal found on landing page")
    return SupersessionFinding(True, "; ".join(signals))


def verify(
    manifest: Manifest,
    check_supersessions: bool = True,
    client: PoliteClient | None = None,
) -> list[DocReport]:
    """Run Stage 1 checks across the whole manifest."""
    owns_client = client is None
    client = client or PoliteClient()
    reports: list[DocReport] = []
    try:
        for doc in manifest.documents:
            url_check = check_url(client, doc)
            if check_supersessions and doc.needs_supersession_check:
                finding = check_supersession(client, doc)
            else:
                finding = SupersessionFinding(False, "not flagged for supersession check")
            reports.append(DocReport(doc=doc, url_check=url_check, supersession=finding))
    finally:
        if owns_client:
            client.close()
    return reports
