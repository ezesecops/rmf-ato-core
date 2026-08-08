"""Stage 7 — export the validated rows to parquet.

Explicit schema, stable row order, one split. Users make their own eval splits;
a fake validation split of reference text would mean nothing.
"""

from __future__ import annotations

from pathlib import Path

from .parse_oscal import control_sort_key
from .schema import FIELD_ORDER, Row


def arrow_schema():
    import pyarrow as pa

    # Written out rather than inferred, so a column cannot change type between
    # dataset versions because one build happened to have no nulls.
    return pa.schema([
        pa.field("id", pa.string(), nullable=False),
        pa.field("text", pa.string(), nullable=False),
        pa.field("doc_id", pa.string(), nullable=False),
        pa.field("doc_title", pa.string(), nullable=False),
        pa.field("revision", pa.string(), nullable=False),
        pa.field("pub_date", pa.string(), nullable=False),
        pa.field("tier", pa.int32(), nullable=False),
        pa.field("chunk_type", pa.string(), nullable=False),
        pa.field("control_id", pa.string(), nullable=True),
        pa.field("section_path", pa.string(), nullable=True),
        pa.field("source_url", pa.string(), nullable=False),
        pa.field("sha256_source", pa.string(), nullable=False),
    ])


def sort_rows(rows: list[Row]) -> list[Row]:
    """Stable order: by doc_id, then by id — with control ids ordered the way a
    practitioner reads them, so AC-2 precedes AC-11."""
    def key(row: Row):
        return (
            row.doc_id,
            control_sort_key(row.control_id) if row.control_id else ("", 0, 0),
            row.id,
        )
    return sorted(rows, key=key)


def write_parquet(rows: list[Row], destination: Path) -> Path:
    import pyarrow as pa
    import pyarrow.parquet as pq

    ordered = sort_rows(rows)
    columns = {
        name: [getattr(row, name) for row in ordered] for name in FIELD_ORDER
    }
    table = pa.table(columns, schema=arrow_schema())
    destination.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, destination, compression="snappy")
    return destination
