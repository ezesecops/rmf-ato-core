"""Stage 2 — retrieve every manifest artifact and record its provenance.

Provenance is the point of this stage as much as the bytes are: `provenance.json`
is what lets a downstream reader prove which exact artifact a published row came
from. It is committed to git; the artifacts themselves are not.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .manifest import Document, Manifest, PoliteClient

RETRY_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 2.0


@dataclass
class ProvenanceEntry:
    """One artifact's integrity record."""

    sha256: str | None
    bytes: int | None
    retrieved_at: str | None  # ISO 8601 UTC
    url: str | None
    http_status: int | None
    source: str  # "download", "manual", or "skipped"
    skipped: bool = False
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FetchResult:
    doc_id: str
    action: str  # "downloaded", "cached", "manual", "manual-missing", "skipped", "failed"
    detail: str = ""
    entry: ProvenanceEntry | None = None
    path: Path | None = None


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


# --- provenance file ---------------------------------------------------------

def load_provenance(path: str | Path) -> dict[str, ProvenanceEntry]:
    path = Path(path)
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {doc_id: ProvenanceEntry(**entry) for doc_id, entry in raw.items()}


def save_provenance(path: str | Path, provenance: dict[str, ProvenanceEntry]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Sorted keys so the committed file diffs cleanly between builds.
    payload = {doc_id: provenance[doc_id].to_dict() for doc_id in sorted(provenance)}
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")


# --- paths and sanity checks -------------------------------------------------

def artifact_extension(doc: Document) -> str:
    if doc.format == "oscal":
        return "json"
    if doc.format == "web":
        # A web source may publish its content as JSON (the AI RMF Playbook
        # does); keep the extension honest about what is on disk.
        return "json" if (doc.url or "").endswith(".json") else "html"
    return "pdf"


def raw_path(data_dir: Path, doc: Document) -> Path:
    if doc.format == "pdf-manual":
        return data_dir / "raw" / "manual" / f"{doc.doc_id}.pdf"
    return data_dir / "raw" / f"{doc.doc_id}.{artifact_extension(doc)}"


class SanityError(Exception):
    """The bytes we got are not the kind of artifact the manifest promised."""


def sanity_check(data: bytes, doc: Document) -> None:
    """Fail loudly rather than let a 200-OK error page into the corpus."""
    fmt = doc.format
    if fmt in {"pdf", "pdf-manual"}:
        # Some PDFs carry leading whitespace/BOM before the header.
        if not data.lstrip()[:5].startswith(b"%PDF"):
            raise SanityError(f"{doc.doc_id}: does not start with %PDF (got {data[:16]!r})")
        return
    if fmt == "oscal":
        try:
            parsed = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SanityError(f"{doc.doc_id}: not valid JSON ({exc})") from exc
        if not isinstance(parsed, dict) or not ({"catalog", "profile"} & parsed.keys()):
            raise SanityError(
                f"{doc.doc_id}: OSCAL file has no top-level 'catalog' or 'profile' "
                f"(top-level keys: {sorted(parsed)[:5] if isinstance(parsed, dict) else type(parsed)})"
            )
        return
    if fmt == "web":
        if (doc.url or "").endswith(".json"):
            try:
                parsed = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise SanityError(f"{doc.doc_id}: not valid JSON ({exc})") from exc
            if not parsed:
                raise SanityError(f"{doc.doc_id}: JSON source is empty")
            return
        head = data[:4096].lower()
        if b"<html" not in head and b"<!doctype html" not in head:
            raise SanityError(f"{doc.doc_id}: response does not look like HTML")


# --- downloading -------------------------------------------------------------

def download(client: PoliteClient, url: str) -> tuple[bytes, int]:
    """GET with retries and backoff. Returns (body, status).

    Uses GET rather than HEAD-then-GET because nvlpubs.nist.gov 404s HEAD for
    files it serves fine (see Stage 1).
    """
    import httpx

    last_error: Exception | None = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            response = client.get(url)
            if response.status_code == 200:
                return response.content, response.status_code
            # 4xx other than 429 will not improve on retry.
            if 400 <= response.status_code < 500 and response.status_code != 429:
                raise SanityError(f"HTTP {response.status_code} for {url}")
            last_error = SanityError(f"HTTP {response.status_code} for {url}")
        except httpx.HTTPError as exc:
            last_error = exc
        if attempt < RETRY_ATTEMPTS:
            time.sleep(RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1)))
    raise SanityError(f"failed after {RETRY_ATTEMPTS} attempts: {last_error}")


def fetch_document(
    client: PoliteClient,
    doc: Document,
    data_dir: Path,
    provenance: dict[str, ProvenanceEntry],
    include_web: bool = False,
    force: bool = False,
) -> FetchResult:
    """Fetch one document. Idempotent: an artifact already on disk whose hash
    matches provenance is left alone and no request is made."""
    destination = raw_path(data_dir, doc)

    if doc.format == "embedded-in-oscal":
        return FetchResult(doc.doc_id, "skipped", "content is embedded in another document")

    if doc.format == "pdf-manual":
        return _record_manual(doc, destination, provenance)

    if doc.format == "web" and not include_web:
        provenance[doc.doc_id] = ProvenanceEntry(
            sha256=None, bytes=None, retrieved_at=None, url=doc.url,
            http_status=None, source="skipped", skipped=True,
            note="web source deferred; not included in this build",
        )
        return FetchResult(doc.doc_id, "skipped", "web source not opted in (--include-web)")

    existing = provenance.get(doc.doc_id)
    if not force and destination.exists() and existing and existing.sha256:
        actual = sha256_file(destination)
        if actual == existing.sha256:
            return FetchResult(doc.doc_id, "cached", f"sha256 {actual[:12]}…",
                               entry=existing, path=destination)

    try:
        data, status = download(client, doc.url)
        sanity_check(data, doc)
    except SanityError as exc:
        return FetchResult(doc.doc_id, "failed", str(exc))

    destination.parent.mkdir(parents=True, exist_ok=True)
    # Write via a temp file so an interrupted run never leaves a half artifact
    # that the idempotency check would later trust.
    temporary = destination.with_suffix(destination.suffix + ".part")
    temporary.write_bytes(data)
    temporary.replace(destination)

    entry = ProvenanceEntry(
        sha256=sha256_bytes(data),
        bytes=len(data),
        retrieved_at=utc_now_iso(),
        url=doc.url,
        http_status=status,
        source="download",
    )
    provenance[doc.doc_id] = entry
    return FetchResult(doc.doc_id, "downloaded", f"{len(data):,} bytes", entry=entry, path=destination)


def _record_manual(
    doc: Document, destination: Path, provenance: dict[str, ProvenanceEntry]
) -> FetchResult:
    """Manual documents are placed by a human; we only hash and record them."""
    if not destination.exists():
        provenance[doc.doc_id] = ProvenanceEntry(
            sha256=None, bytes=None, retrieved_at=None,
            url=doc.landing_page, http_status=None,
            source="manual", skipped=True, note="awaiting manual placement",
        )
        return FetchResult(
            doc.doc_id, "manual-missing",
            f"place the PDF at {destination} (source: {doc.landing_page})",
        )

    digest = sha256_file(destination)
    existing = provenance.get(doc.doc_id)
    entry = ProvenanceEntry(
        sha256=digest,
        bytes=destination.stat().st_size,
        # Keep the original record date if the file has not changed, so a re-run
        # does not overstate when the human actually supplied it.
        retrieved_at=(
            existing.retrieved_at
            if existing and existing.sha256 == digest and existing.retrieved_at
            else utc_now_iso()
        ),
        url=doc.landing_page,
        http_status=None,
        source="manual",
        note="human-placed artifact",
    )
    provenance[doc.doc_id] = entry
    return FetchResult(doc.doc_id, "manual", f"{entry.bytes:,} bytes", entry=entry, path=destination)


def fetch_all(
    manifest: Manifest,
    data_dir: Path,
    include_web: bool = False,
    force: bool = False,
    client: PoliteClient | None = None,
) -> list[FetchResult]:
    owns_client = client is None
    client = client or PoliteClient()
    provenance_path = data_dir / "provenance.json"
    provenance = load_provenance(provenance_path)
    results: list[FetchResult] = []
    try:
        for doc in manifest.documents:
            result = fetch_document(client, doc, data_dir, provenance,
                                    include_web=include_web, force=force)
            results.append(result)
            print(f"  {result.action:<15} {doc.doc_id:<22} {result.detail}")
            # Save as we go: a crash mid-run must not lose the provenance of
            # artifacts already retrieved.
            save_provenance(provenance_path, provenance)
    finally:
        if owns_client:
            client.close()
    return results
