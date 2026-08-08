# rmf-ato-core

Build pipeline for **`ezesecops/rmf-ato-core`** — a curated, provenance-tracked dataset of
current-revision NIST RMF/ATO publications plus AI governance documents.

The design goal is the opposite of a scrape-everything corpus: **small, current, structurally
correct, and fully traceable**. Concretely:

- `manifest.json` is the sole authority on what is in scope. Every row traces to one entry.
- Control IDs come **only** from OSCAL structured data, and must match the 20 real SP 800-53
  Rev 5 families. PDF-derived rows never carry a control ID — which makes fabricated IDs like
  `HA-25` impossible to publish.
- No baked-in prompts, no synthetic Q&A, no precomputed embeddings. Source text only.
- Rejected content is logged, never silently dropped.

## Current build

`make all` reproduces the dataset end to end in about a minute (artifacts cached after the first
run) and produces:

| | |
|---|---|
| rows published | **5,623** |
| parquet size | **2.1 MB**, single `train` split |
| documents | 19 retrieved, 2 awaiting manual placement |
| rows rejected | **921**, every one logged with a rule and reason |
| control IDs failing the family whitelist | **0** |

Headline counts landed on the published figures independently: 47 RMF tasks, 72 AI RMF
subcategories, 20 control families, and baselines of 149 / 287 / 370 / 96 for
LOW / MODERATE / HIGH / PRIVACY.

## Pipeline

```
01 verify -> 02 fetch -> 03 parse OSCAL -> 04 parse PDF -> 05 chunk -> 06 validate -> 07 export -> 08 upload
```

Each stage is an independently runnable, idempotent script under `scripts/`, wrapping functions
in `src/rmf_ato_core/`. Several stages end at a human-review checkpoint (see `BUILD_SPEC.md`).
Stage 08 (Hugging Face upload) refuses to run without explicit approval.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q
make verify          # Stage 1: manifest + URL verification report
```

## Layout

| path | what |
|---|---|
| `manifest.json` | source of truth: documents, revisions, URLs, per-doc parsing notes |
| `src/rmf_ato_core/schema.py` | the `Row` dataclass, chunk-type enum, control-family whitelist |
| `src/rmf_ato_core/manifest.py` | manifest loading/validation + Stage 1 checks |
| `scripts/0*.py` | one CLI per pipeline stage |
| `data/provenance.json` | sha256 + retrieval time per artifact (committed) |
| `data/raw`, `data/interim`, `data/processed` | build artifacts (gitignored) |
| `dataset_card/README.md` | the Hugging Face dataset card |
| `BUILD_SPEC.md` | the full specification this repo implements |

## Manual documents

Two sources block scripted fetches and must be placed by hand before parsing:

- `data/raw/manual/CNSSI-1253.pdf` — from <https://www.cnss.gov/CNSS/issuances/Instructions.cfm>
- `data/raw/manual/DoDI-8510.01.pdf` — from <https://www.esd.whs.mil/DD/DoD-Issuances/>

Both are public releases; the pipeline records their sha256 in provenance like any other artifact.

## License

Compilation released under CC0-1.0 (`LICENSE`). Source documents are US Government works in the
public domain per 17 U.S.C. § 105.
