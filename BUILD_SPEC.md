# BUILD SPEC — `rmf-ato-core` Hugging Face Dataset

**Audience:** An AI coding agent (Claude Code / Codex) building this project under the supervision of the maintainer (Eze, @ezesecops).
**Read this entire document before writing any code.**

---

## 0. Mission and context

Build a **curated, provenance-tracked dataset of current-revision NIST RMF/ATO publications plus AI governance documents**, published to the Hugging Face Hub as `ezesecops/rmf-ato-core`.

This dataset exists because the popular alternatives in this space are indiscriminate scrapes of the entire NIST corpus: they mix superseded 1989 documents with current guidance, contain fabricated control IDs produced by naive PDF chunking (e.g., "control HA-25", "control WE-12" — not real SP 800-53 families), bake a system prompt into every row, and ship precomputed embeddings that lock users to one embedding model.

This dataset is the opposite: **small, current, structurally correct, and fully traceable**. Every design decision below serves one of those four properties. When you face an ambiguous implementation choice, resolve it in favor of *correctness and traceability over size and coverage*.

The maintainer is a NIST RMF/ATO subject-matter expert (ISSM) but a developing Python programmer. Therefore:

- Write **simple, readable, boring Python**. No clever metaprogramming, no async, no ORM, no framework. Plain functions, dataclasses, and the standard library wherever possible.
- **Comment the "why," not the "what."** The maintainer will read every file.
- Each pipeline stage is a **separate, independently runnable script** with an idempotent CLI. Re-running any stage must be safe.
- At every checkpoint marked `⏸ HUMAN REVIEW`, **stop and present results to the maintainer before proceeding**. Do not push anything to the Hugging Face Hub, GitHub, or any external service without explicit approval at the relevant checkpoint.

---

## 1. Non-negotiable curation rules

1. **Current revisions only.** The manifest (`manifest.json`, provided) is the sole authority on which documents and revisions are in scope. If you discover a manifest entry is superseded (e.g., SP 800-18 Rev 2 has gone final), do NOT silently substitute — flag it to the maintainer, and only update the manifest with approval. The manifest carries per-document notes about known possible supersessions; check each landing page during Stage 1.
2. **No fabricated structure.** A row may carry a `control_id` ONLY if that ID came from OSCAL structured data. PDF-derived rows never get control IDs, even if the text mentions one.
3. **No baked-in prompts.** Row text is source text only. No "You are a cybersecurity expert..." prefixes anywhere.
4. **No precomputed embeddings.** Text only. (An optional embeddings companion dataset is explicitly out of scope for v1.)
5. **Every row traces to the manifest.** `doc_id` on every row must match a manifest entry. Fetch stage records a sha256 of every retrieved artifact.
6. **Rejected content is logged, never silently dropped.** The rejection log is a first-class output of this project.
7. **Nothing sensitive.** All sources are public, unclassified US government works (public domain per 17 U.S.C. § 105). If any manually supplied document appears to carry distribution markings other than "Distribution A / approved for public release," halt and ask the maintainer.

---

## 2. Repository layout

Create this structure (GitHub repo name: `rmf-ato-core`):

```
rmf-ato-core/
├── README.md                  # project readme (not the dataset card)
├── LICENSE                    # CC0-1.0 for the compilation; note sources are US-gov public domain
├── pyproject.toml             # project metadata + deps; use hatchling or setuptools, keep minimal
├── manifest.json              # PROVIDED — copy in verbatim, do not regenerate
├── Makefile                   # make fetch / parse / chunk / validate / export / all / test
├── src/rmf_ato_core/
│   ├── __init__.py
│   ├── schema.py              # dataclass for Row + chunk_type enum + family whitelist
│   ├── manifest.py            # load/validate manifest, verify URLs (Stage 1)
│   ├── fetch.py               # Stage 2
│   ├── parse_oscal.py         # Stage 3
│   ├── parse_pdf.py           # Stage 4
│   ├── chunk.py               # Stage 5
│   ├── validate.py            # Stage 6
│   └── export.py              # Stage 7 (parquet + HF upload)
├── scripts/
│   ├── 01_verify_manifest.py  # thin CLI wrappers around src functions
│   ├── 02_fetch.py
│   ├── 03_parse_oscal.py
│   ├── 04_parse_pdf.py
│   ├── 05_chunk.py
│   ├── 06_validate.py
│   ├── 07_export.py
│   └── 08_upload.py           # gated: refuses to run without --i-have-approval flag
├── data/                      # gitignored except .gitkeep and provenance.json
│   ├── raw/                   #   downloaded artifacts
│   ├── raw/manual/            #   human-placed PDFs (CNSSI-1253, DoDI-8510.01)
│   ├── interim/               #   parsed JSON per document
│   ├── processed/             #   final parquet + rejection log
│   └── provenance.json        #   sha256 + retrieval timestamp per artifact (IS committed)
├── tests/
│   ├── test_schema.py
│   ├── test_parse_oscal.py    # uses a small fixture catalog, not the real 8MB file
│   ├── test_chunk.py
│   └── test_validate.py
├── dataset_card/
│   └── README.md              # the HF dataset card (template in §12)
└── .gitignore                 # data/raw, data/interim, data/processed, .venv, __pycache__
```

---

## 3. Environment

- Python 3.11+.
- Dependencies (keep this list this short): `httpx` (fetch), `pymupdf` (PDF text), `pyarrow` (parquet), `huggingface_hub` (upload), `pytest` (tests). Do not add pandas, langchain, or any embedding library.
- `pip install -e ".[dev]"` must work from a clean venv. Verify this before Stage 2.
- All scripts run from repo root, take `--manifest manifest.json` and data-dir args with sane defaults, and exit nonzero on failure.

---

## 4. Data schema

Define in `src/rmf_ato_core/schema.py` as a frozen dataclass. One row = one chunk.

| field          | type          | rules |
|----------------|---------------|-------|
| `id`           | str           | Deterministic, human-readable: `{doc_id}/{chunk_type}/{slug}` e.g. `SP-800-53r5/control/ac-2`, `SP-800-37r2/section/3.2-task-c-1`, `AI-100-1/ai_rmf_subcategory/govern-1.1`. Deterministic IDs make diffs between dataset versions meaningful. Must be unique. |
| `text`         | str           | The chunk content. Clean UTF-8, no control chars, normalized whitespace (collapse runs of spaces; preserve paragraph breaks as `\n\n`). 200–8000 chars (validated). |
| `doc_id`       | str           | Must exist in manifest. |
| `doc_title`    | str           | Copied from manifest. |
| `revision`     | str           | Copied from manifest. |
| `pub_date`     | str           | Copied from manifest (YYYY-MM or YYYY). |
| `tier`         | int           | 1 or 2, from manifest. |
| `chunk_type`   | str (enum)    | One of: `control`, `control_enhancement`, `control_discussion`, `assessment_objective`, `assessment_method`, `baseline`, `section`, `task`, `ai_rmf_subcategory`, `ssdf_practice`, `definition`, `table`. |
| `control_id`   | str or null   | Lowercase OSCAL style (`ac-2`, `ac-2.3`). Null for all PDF-derived rows. If non-null, family prefix MUST be in the whitelist (§10). |
| `section_path` | str or null   | Human-readable location, e.g. `Chapter 3 > 3.4 Assess > Task A-2` or `AC > AC-2 > Discussion`. |
| `source_url`   | str           | The manifest `url` (or `landing_page` for manual docs). |
| `sha256_source`| str           | Hash of the exact artifact this chunk came from (from provenance.json). |

**SP 800-53 Rev 5 control family whitelist** (the ONLY valid `control_id` prefixes):
`ac, at, au, ca, cm, cp, ia, ir, ma, mp, pe, pl, pm, ps, pt, ra, sa, sc, si, sr`

---

## 5. Stage 1 — Manifest verification (`01_verify_manifest.py`)

1. Load and schema-validate `manifest.json` (required fields present, `format` in {oscal, pdf, embedded-in-oscal, pdf-manual, web}, urls are https or null).
2. For every non-null `url`: HTTP HEAD (fall back to ranged GET if HEAD unsupported). Record status code.
3. For each document whose manifest notes flag a possible supersession (SP 800-60, SP 800-18, SP 800-218): fetch the `landing_page` and check whether the page indicates the listed revision is still current. This is a best-effort text check — report findings, don't auto-decide.
4. Output a verification report table: doc_id, url status, supersession finding.
5. Any 404 on nvlpubs: the URL pattern is predictable but pre-2015 pubs live under `/nistpubs/Legacy/SP/nistspecialpublication{number}.pdf` while newer ones use `/nistpubs/SpecialPublications/NIST.SP.{number}.pdf` — try the alternate pattern, and if both fail, resolve the correct link from the landing page HTML.

**⏸ HUMAN REVIEW:** Present the verification report. Get approval (including any manifest corrections) before fetching.

---

## 6. Stage 2 — Fetch (`02_fetch.py`)

- Download every `oscal` and `pdf` artifact to `data/raw/{doc_id}.{ext}`. Skip if the file exists and its sha256 matches provenance (idempotency).
- Set a real User-Agent (`rmf-ato-core-builder/1.0 (+github.com/<user>/rmf-ato-core)`), 30s timeout, 3 retries with backoff, and **be polite: ≥2s between requests to the same host**. This hits nist.gov a dozen times total; there is no excuse for hammering it.
- For `pdf-manual` docs: check `data/raw/manual/{doc_id}.pdf` exists; if missing, print clear instructions for the human and continue (don't fail the whole run).
- For the `web` doc (AI RMF Playbook): SKIP in v1 unless the maintainer opts in. If skipped, record `"skipped": true` in provenance.
- Write/update `data/provenance.json`: `{doc_id: {sha256, bytes, retrieved_at (ISO 8601 UTC), url, http_status}}`. This file IS committed to git.
- Sanity checks: PDFs start with `%PDF`, OSCAL files parse as JSON and contain a top-level `catalog` or `profile` key. Fail loudly otherwise.

---

## 7. Stage 3 — Parse OSCAL (`03_parse_oscal.py`)

This is the highest-value stage. Output: `data/interim/{doc_id}.rows.jsonl` (one JSON row per line, matching the schema, pre-validation).

### 7.1 The SP 800-53r5 catalog structure

The catalog JSON shape (verify against the actual file; adjust if the schema has moved):

```
catalog
├── metadata {title, version, ...}
├── groups []                    # one per control family (id: "ac", title: "Access Control")
│   └── controls []              # controls (id: "ac-1", "ac-2", ...)
│       ├── id, title, class
│       ├── props []             # name/value pairs — includes {"name": "status", "value": "withdrawn"} for withdrawn controls
│       ├── params []            # ODP parameters: {id, label or select{choice[]}}
│       ├── parts []             # THE CONTENT — see below
│       └── controls []          # NESTED: control enhancements (id: "ac-2.1", ...) — recurse
```

Relevant `parts` by `name`:

- `statement` — the control text. Prose lives in `prose` fields; sub-parts (items a., b., c.) nest inside `parts` with their own prose. Flatten depth-first into readable text, rendering item labels from each part's `props` where `name == "label"` (e.g., "a.", "1.").
- `guidance` — the discussion text → separate row, `chunk_type: control_discussion`.
- `assessment-objective` (`name` may be `assessment-objective`) → rows with `doc_id: SP-800-53Ar5`, `chunk_type: assessment_objective`. **Confirm the exact part name empirically** by inspecting the real file before coding to it — print the set of all part names encountered and include it in the stage report.
- `assessment-method` → `chunk_type: assessment_method`, also attributed to `SP-800-53Ar5`.

### 7.2 Rules

1. **Skip withdrawn controls** (status prop = withdrawn) — log each to the rejection log with reason `withdrawn_control`, do not emit.
2. **Parameter rendering:** control text references parameters via insertion markers (e.g., `{{ insert: param, ac-02_odp.01 }}`). Replace each with `[Assignment: {param label or joined choices}]` — the human-readable ODP convention every RMF practitioner recognizes. No raw template markers may survive into `text` (validated in Stage 6).
3. One row per control (`chunk_type: control`, text = title + statement), one per discussion, one per enhancement (`control_enhancement`, and its own discussion row if present), plus assessment rows. `section_path` = `{FAMILY} > {CONTROL-ID}` etc.
4. **Baselines:** parse each 800-53B profile's `imports[].include-controls[].with-ids` list. Emit ONE row per baseline: a sentence naming the baseline + the sorted control-id list as text. `chunk_type: baseline`.
5. Log a per-family count summary (controls, enhancements, discussions, objectives) at the end of the run.

**⏸ HUMAN REVIEW:** Present 10 sample rows (mix of AC-2, an enhancement, a discussion, an assessment objective, a baseline) plus the family count table. The maintainer will eyeball these against the official pubs — his SME review IS the quality gate. Only proceed on approval.

---

## 8. Stage 4 — Parse PDFs (`04_parse_pdf.py`)

Output: `data/interim/{doc_id}.rows.jsonl` for each PDF document.

Approach — deliberately simple, tuned per document, honest about limits:

1. Extract with **PyMuPDF** (`page.get_text("dict")`) so font-size/flags information is available for heading detection.
2. **Strip furniture:** running headers/footers (lines repeating on >50% of pages), page numbers, the NIST title-page boilerplate, "This publication is available free of charge from:" lines.
3. **Heading detection:** numbered-heading regex first (`^\d+(\.\d+)*\s+\S`, plus `^(CHAPTER|APPENDIX|TASK)\s`), font-size signal as tiebreaker. Build a section tree; `section_path` = joined heading trail.
4. **Per-document handling** (driven by manifest `notes` — read them; they are instructions):
   - `SP-800-37r2`: Chapter 3 tasks (`Task P-1` … `Task M-6`) each become one `chunk_type: task` row. Everything else: `section`.
   - `FIPS-199`: the C/I/A × impact-level definitions become one `table` row (transcribe the table to structured text: one line per cell, `{objective} / {level}: {definition}`).
   - `FIPS-200`: one row per security-related area.
   - `SP-800-60v2r1`: one row per information type (D.x entries) including its provisional categorization line.
   - `AI-100-1`: one row per AI RMF subcategory (`GOVERN 1.1`, `MAP 2.3`, …), `chunk_type: ai_rmf_subcategory`; trustworthiness characteristics one row each.
   - `SP-800-218` / `218A`: one row per practice/task (PO.1.1 style), `chunk_type: ssdf_practice`. The practices live in a wide table — if table extraction is unreliable, fall back to text-mode extraction of the practices section and reconstruct rows by practice-ID regex (`^(PO|PS|PW|RV)\.\d`).
   - `CNSSI-1253` / `DoDI-8510.01` (if manually supplied): section-level chunking only; do NOT attempt to extract their control tables in v1.
5. **Glossaries:** every document's glossary/appendix of terms → one `definition` row per term.
6. **Never emit `control_id` from this stage.** Even when SP 800-53 IDs appear in prose.
7. Anything unparseable (garbled ligatures, table soup) → rejection log with reason `extraction_failure` and the page number. Target is high precision, not total recall; the data card will say section coverage is partial for PDF-derived docs.

**⏸ HUMAN REVIEW:** 3 sample rows per PDF document + per-document row counts + rejection counts.

---

## 9. Stage 5 — Chunk normalization (`05_chunk.py`)

OSCAL rows and per-unit PDF rows (tasks, practices, subcategories, definitions) are already right-sized. This stage only post-processes `section` rows:

- Split any section > 8000 chars at paragraph boundaries into parts ≤ 6000 chars with 1-paragraph overlap; append ` (part n)` to `id` and `section_path`.
- Merge any section < 200 chars into its following sibling (or drop-and-log if it's trailing furniture).
- Normalize whitespace everywhere (per schema rules); fix common PDF ligature artifacts (ﬁ→fi, ﬂ→fl, smart quotes → ASCII where unambiguous).

---

## 10. Stage 6 — Validation (`06_validate.py`)

This stage is the project's thesis made executable: **treat the dataset like a supply-chain artifact**. It reads all interim rows and produces `data/processed/rows.validated.jsonl` + `data/processed/rejections.jsonl` + a summary report.

Checks (each rejection records `{row_id, rule, detail}`):

1. `schema_valid` — all fields present, types correct, chunk_type in enum.
2. `doc_in_manifest` — doc_id exists in manifest; sha256_source matches provenance.
3. `control_family_whitelist` — non-null control_id matches `^(ac|at|au|ca|cm|cp|ia|ir|ma|mp|pe|pl|pm|ps|pt|ra|sa|sc|si|sr)-\d+(\.\d+)?$`. **This is the rule that makes "control HA-25" impossible.**
4. `control_id_source` — control_id non-null ⟹ the row's doc_id is an OSCAL-format manifest entry.
5. `length_bounds` — 200 ≤ len(text) ≤ 8000.
6. `no_template_residue` — text contains none of: `{{`, `}}`, `insert: param`, `You are a`, `As an AI`.
7. `no_furniture` — text doesn't match footer/header patterns (`^NIST SP 800-\d+.*Page \d+`, bare page numbers, `This publication is available free of charge`).
8. `unique_id` and `near_dupe` — exact-duplicate `text` across rows (normalized) is rejected on the later row.
9. `encoding_clean` — valid UTF-8, no replacement chars (�), no control characters.
10. `expected_counts` (WARN, not reject): SP 800-53r5 base controls in plausible range (900–1,200 including enhancements ~1,000+; family count == 20); AI RMF subcategories in range 60–80; SSDF practice rows ≥ 40. Out-of-range ⟹ print warning for human review — counts drifting means a parser bug.

Also emit `data/processed/summary.md`: rows by doc and chunk_type, rejection counts by rule, total size.

**⏸ HUMAN REVIEW:** the summary + the full rejection log. Nothing proceeds until the maintainer signs off on the rejections being correct rejections.

---

## 11. Stage 7 — Export (`07_export.py`)

- Write `data/processed/train.parquet` (pyarrow, snappy compression, explicit schema, stable row order: sort by `doc_id`, then `id`).
- **No split shenanigans:** single `train` split. Users make their own eval splits; a fake validation split of reference text is meaningless.
- Print final stats: row count, parquet size (should be single-digit MB — if it's hundreds of MB, something is wrong), rows per doc.

---

## 12. Stage 8 — Dataset card (`dataset_card/README.md`)

Write the card with this structure (YAML header first):

```yaml
---
license: cc0-1.0
language: [en]
task_categories: [text-retrieval, question-answering]
tags: [cybersecurity, nist, rmf, compliance, fedramp, ato, ai-governance, oscal]
pretty_name: RMF/ATO Core Corpus
size_categories: [10K<n<100K]
---
```

Sections, in order:

1. **What this is** — 3-sentence summary: curated current-revision RMF corpus + AI governance tier, built for RAG/fine-tuning around authorization workflows.
2. **Why another NIST dataset** — the curation argument. Name the failure modes of scrape-everything datasets factually and without disparaging any specific dataset by name: superseded-document contamination, fabricated control IDs from naive chunking, baked-in prompts, embedding lock-in. State how each is prevented here (manifest, OSCAL-only control IDs + family whitelist, clean text, no embeddings).
3. **What's included / excluded** — the manifest table (doc, revision, date, tier, format) and the explicit exclusion policy. State clearly that SP 800-53A content is extracted from the OSCAL catalog's embedded assessment parts.
4. **Schema** — the field table from §4, with 2 real example rows.
5. **How it was built** — pipeline diagram (fetch → parse → chunk → validate → export), link to the GitHub repo, note that the rejection log is published in the repo.
6. **Provenance & integrity** — retrieval dates and sha256 per source (generate this table from provenance.json).
7. **Limitations** — PDF-derived sections are best-effort; AI RMF Playbook deferred (if it was); CNSSI/DoDI control tables not extracted in v1; corpus reflects publications as of the build date and NIST revises documents.
8. **License** — sources are US government works (public domain, 17 U.S.C. § 105); compilation released CC0-1.0.
9. **Maintainer & citation** — @ezesecops links, a BibTeX-style citation stub, and a "report issues" pointer to the repo.

---

## 13. Stage 9 — Upload (`08_upload.py`)

- Refuses to run without `--i-have-approval` AND a confirmation prompt.
- Uses `huggingface_hub.HfApi`: create repo `ezesecops/rmf-ato-core` (type dataset, **private first**), upload `train.parquet` + dataset card + `manifest.json` + `provenance.json`.
- The human flips it public in the HF UI after final inspection. The script never sets public visibility itself.
- Token via `HF_TOKEN` env var only. Never write the token to any file, log, or commit.

**⏸ HUMAN REVIEW (final):** maintainer inspects the private repo's dataset viewer, then makes it public himself.

---

## 14. Testing & acceptance criteria

Tests (pytest, run in CI via a minimal GitHub Actions workflow on push):

- `test_schema.py` — row construction, id determinism, whitelist regex (must accept `ac-2`, `ac-2.3`, `sr-11`; must reject `ha-1`, `we-12`, `am-6`, `AC-2` uppercase, `ac2`).
- `test_parse_oscal.py` — run the parser against a **hand-made fixture catalog** (~2 controls, 1 enhancement, 1 withdrawn control, 1 param insertion, 1 assessment objective) checked into `tests/fixtures/`. Assert: withdrawn skipped+logged, param rendered as `[Assignment: …]`, enhancement typed correctly, objective attributed to SP-800-53Ar5.
- `test_chunk.py` — split/merge boundary behavior.
- `test_validate.py` — each rule fires on a crafted bad row and passes a good row.

**Definition of done for v1:**

1. All tests green; `make all` runs the full pipeline from a clean checkout (with manual PDFs present or explicitly skipped) with zero unhandled exceptions.
2. Zero rows violate rules 1–9; all expected-count warnings reviewed by maintainer.
3. Spot-check protocol passed: maintainer verifies AC-2, AC-2(3), RA-5 discussion, one FIPS-199 row, GOVERN 1.1, and PO.1.1 against the official publications, word-for-word start of each.
4. Dataset card complete, provenance table generated, private HF repo populated.
5. Total dataset in the 10k–40k row range. If materially outside, investigate before shipping.

---

## 15. Guardrails — what NOT to do

- Do NOT add documents beyond the manifest "to be thorough." Scope creep is the failure mode this project is designed against.
- Do NOT generate synthetic Q&A pairs, summaries, or paraphrases of source text. v1 is source text only.
- Do NOT compute embeddings.
- Do NOT scrape csrc.nist.gov broadly; fetch exactly the manifest URLs plus at most the listed landing pages.
- Do NOT push to GitHub or Hugging Face, install git hooks, create HF repos, or take any action outside this repo's working directory without hitting the designated human-review checkpoint first.
- Do NOT silently "fix" data problems; every drop goes through the rejection log.
- Do NOT vendor huge dependencies to save an hour of parsing work.

---

## 16. Suggested build order (maps to maintainer's weekend cadence)

| Milestone | Contents | Checkpoint |
|---|---|---|
| M1 | Repo scaffold, schema.py + tests, Stage 1 verify | ⏸ review verification report |
| M2 | Stage 2 fetch + provenance; Stage 3 OSCAL parse | ⏸ review OSCAL sample rows |
| M3 | Stage 4 PDF parse (start with FIPS-199 — smallest — then 800-37r2, then the rest) | ⏸ review PDF sample rows |
| M4 | Stages 5–7 chunk/validate/export | ⏸ review rejection log + summary |
| M5 | Dataset card, CI, upload script, private HF push | ⏸ final inspection, human flips public |

Work in small commits with descriptive messages; the git history is part of the maintainer's shipped-software evidence.

---

*End of spec. When in doubt: smaller, cleaner, traceable — and ask the human.*
