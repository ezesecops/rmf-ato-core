---
license: cc0-1.0
language: [en]
task_categories: [text-retrieval, question-answering]
tags: [cybersecurity, nist, rmf, compliance, fedramp, ato, ai-governance, oscal]
pretty_name: RMF/ATO Core Corpus
size_categories: [1K<n<10K]
---

# RMF/ATO Core Corpus

## What this is

A curated corpus of **current-revision** NIST Risk Management Framework and authorization
publications, plus a second tier of AI governance documents. Every row is source text — one
control, one assessment objective, one RMF task, one AI RMF subcategory, one SSDF practice, one
document section — carrying the identifiers a practitioner actually cites. It is built for
retrieval and fine-tuning around authorization workflows: control selection, SSP and assessment
work, categorization, continuous monitoring, and the emerging AI governance overlay.

**5,623 rows, 2.1 MB, single `train` split.** No embeddings, no synthetic Q&A, no system prompts.

## Why another NIST dataset

Existing NIST corpora are typically indiscriminate scrapes, and they share four failure modes.
Each is prevented here by construction rather than by cleanup:

| Failure mode | How it is prevented |
|---|---|
| **Superseded-document contamination** — 1989 guidance sitting beside current guidance | A manifest is the sole authority on scope. Every row traces to one manifest entry. Landing pages are checked for supersession at build time; SP 800-18 Rev 1 was found withdrawn during this build and replaced with Rev 2. |
| **Fabricated control IDs** — "control HA-25", "control WE-12", produced when a chunker turns any two capital letters near a number into an identifier | A row may carry a `control_id` **only** if it came from OSCAL structured data, and the ID must match one of the 20 real SP 800-53 Rev 5 families. PDF-derived rows never carry a control ID, even where the prose names one. Both rules are executable checks, not conventions. |
| **Baked-in prompts** — "You are a cybersecurity expert…" prefixed to every row | Row text is source text. Validation rejects any row containing prompt scaffolding or unrendered template markers. |
| **Embedding lock-in** — precomputed vectors tying users to one model | Text only. Bring your own embedding model. |

The dataset is deliberately small. It is meant to be *right*, not exhaustive.

## What's included

| doc_id | document | revision | date | tier | format |
|---|---|---|---|---|---|
| `SP-800-37r2` | Risk Management Framework for Information Systems and Organizations | Rev 2 | 2018-12 | 1 | PDF |
| `SP-800-53r5` | Security and Privacy Controls for Information Systems and Organizations | Rev 5 (OSCAL 5.2.0) | 2020-09 | 1 | OSCAL |
| `SP-800-53Ar5` | Assessing Security and Privacy Controls | Rev 5 | 2022-01 | 1 | embedded in OSCAL |
| `SP-800-53B-LOW/MODERATE/HIGH/PRIVACY` | Control Baselines | Rev 5 (OSCAL 5.2.0) | 2020-10 | 1 | OSCAL |
| `FIPS-199` | Standards for Security Categorization | Initial (in force) | 2004-02 | 1 | PDF |
| `FIPS-200` | Minimum Security Requirements | Initial (in force) | 2006-03 | 1 | PDF |
| `SP-800-60v1r1` / `v2r1` | Mapping Information Types to Security Categories, Vols 1–2 | Rev 1 | 2008-08 | 1 | PDF |
| `SP-800-18r2` | Developing Security, Privacy, and C-SCRM Plans for Systems | Rev 2 | 2026-06 | 1 | PDF |
| `SP-800-30r1` | Guide for Conducting Risk Assessments | Rev 1 | 2012-09 | 1 | PDF |
| `SP-800-39` | Managing Information Security Risk | Initial (in force) | 2011-03 | 1 | PDF |
| `SP-800-137` | Information Security Continuous Monitoring | Initial (in force) | 2011-09 | 1 | PDF |
| `AI-100-1` | AI Risk Management Framework (AI RMF 1.0) | 1.0 | 2023-01 | 2 | PDF |
| `AI-RMF-PLAYBOOK` | NIST AI RMF Playbook | rolling | 2026-08 | 2 | JSON |
| `SP-800-218` | Secure Software Development Framework (SSDF) | 1.1 | 2022-02 | 2 | PDF |
| `SP-800-218A` | SSDF Community Profile for Generative AI | Initial | 2024-07 | 2 | PDF |

**SP 800-53A content is extracted from the SP 800-53 Rev 5 OSCAL catalog's embedded assessment
parts**, not from a separate 53A file — NIST publishes no standalone 53A OSCAL artifact. Those
rows are attributed to `SP-800-53Ar5` and cite the catalog's hash.

### Excluded

Superseded or withdrawn revisions (including 182 withdrawn SP 800-53 controls, each logged by
name); NIST annual reports and workshop proceedings; pre-2010 legacy publications except FIPS
199/200, which remain in force; and draft publications. Also excluded from v1: CNSSI 1253 and
DoD Instruction 8510.01, whose publishers block automated retrieval.

## Schema

One row = one chunk.

| field | type | notes |
|---|---|---|
| `id` | string | Deterministic and human-readable: `{doc_id}/{chunk_type}/{slug}`, e.g. `SP-800-53r5/control/ac-2`. Oversized rows split into ` (part n)`. Stable across versions, so diffs are meaningful. |
| `text` | string | Source text. Normalized whitespace, paragraph breaks preserved. 80–8,000 chars. |
| `doc_id` | string | Matches a manifest entry. |
| `doc_title` | string | From the manifest. |
| `revision` | string | From the manifest; names the exact OSCAL content release where applicable. |
| `pub_date` | string | `YYYY-MM` or `YYYY`. |
| `tier` | int32 | 1 = RMF/ATO core, 2 = AI governance. |
| `chunk_type` | string | `control`, `control_enhancement`, `control_discussion`, `assessment_objective`, `assessment_method`, `baseline`, `section`, `task`, `ai_rmf_subcategory`, `ssdf_practice`, `definition`, `table`. |
| `control_id` | string or null | Lowercase OSCAL form (`ac-2`, `ac-2.3`). **Null for every PDF-derived row.** |
| `section_path` | string or null | Where it sits: `AC > AC-2 > Discussion`, `CHAPTER THREE > TASK P-1`, `SSDF Practices > PO > PO.1 > PO.1.1`. |
| `source_url` | string | The retrieved artifact's URL. |
| `sha256_source` | string | Hash of the exact artifact the row came from. |

### Rows by chunk type

| chunk_type | rows | | chunk_type | rows |
|---|---:|---|---|---:|
| `assessment_method` | 1,014 | | `ai_rmf_subcategory` | 154 |
| `assessment_objective` | 1,014 | | `ssdf_practice` | 94 |
| `control_discussion` | 999 | | `task` | 47 |
| `control_enhancement` | 714 | | `definition` | 18 |
| `control` | 300 | | `baseline` | 4 |
| `section` | 1,264 | | `table` | 1 |

### Example rows

```json
{
  "id": "SP-800-53r5/control_enhancement/ac-2.3",
  "text": "AC-2(3) Account Management | Disable Accounts\nFamily: Access Control (AC) > AC-2 Account Management\n\nDisable accounts within [Assignment: organization-defined time period] when the accounts:\n(a) Have expired;\n(b) Are no longer associated with a user or individual;\n(c) Are in violation of organizational policy; or\n(d) Have been inactive for [Assignment: organization-defined time period].",
  "doc_id": "SP-800-53r5",
  "revision": "Rev 5 (OSCAL content version 5.2.0)",
  "tier": 1,
  "chunk_type": "control_enhancement",
  "control_id": "ac-2.3",
  "section_path": "AC > AC-2 > AC-2(3)"
}
```

```json
{
  "id": "AI-100-1/ai_rmf_subcategory/govern-1.1",
  "text": "GOVERN 1.1: Legal and regulatory requirements involving AI are understood, managed, and documented.",
  "doc_id": "AI-100-1",
  "revision": "1.0",
  "tier": 2,
  "chunk_type": "ai_rmf_subcategory",
  "control_id": null,
  "section_path": "AI RMF Core > Govern > GOVERN 1.1"
}
```

Note the ODP rendering: `{{ insert: param, ac-02_odp.01 }}` in the OSCAL source becomes
`[Assignment: organization-defined …]` / `[Selection; one or more: …]`, the convention SP 800-53
itself prints. No template marker survives into any row.

## How it was built

```
01 verify → 02 fetch → 03 parse OSCAL → 04 parse PDF → 05 chunk → 06 validate → 07 export
```

Each stage is an independently runnable, idempotent script. Source, tests, and the **full
rejection log** live in the GitHub repository: <https://github.com/ezesecops/rmf-ato-core>

**921 rows were rejected** across the pipeline, every one recorded with a rule and a reason in
`rejections.jsonl`. The largest categories: 545 layout fragments (whose text survives, merged into
neighbouring sections), 182 withdrawn controls, 96 duplicate task stubs from summary tables, 36
trailing furniture blocks, 15 empty "None." discussions, 4 exact duplicates, and 3 rows below the
length floor. Rejected content is logged, never silently dropped.

## Provenance & integrity

Every artifact was retrieved once, hashed, and recorded. `provenance.json` ships with the dataset.

| doc_id | revision | retrieved | bytes | sha256 (first 16) |
|---|---|---|---:|---|
| `AI-100-1` | 1.0 | 2026-08-08 | 1,946,127 | `7576edb531d98488…` |
| `AI-RMF-PLAYBOOK` | rolling | 2026-08-08 | 413,720 | `aecbee3d3c882081…` |
| `FIPS-199` | Initial (in force) | 2026-08-08 | 80,356 | `73d19f05f71e30f3…` |
| `FIPS-200` | Initial (in force) | 2026-08-08 | 218,892 | `107a9b9cdc8eccf3…` |
| `SP-800-137` | Initial (in force) | 2026-08-08 | 986,916 | `2d1c0bf459f5e1bf…` |
| `SP-800-18r2` | Rev 2 | 2026-08-08 | 1,313,448 | `640f9124469f285f…` |
| `SP-800-218` | 1.1 | 2026-08-08 | 739,891 | `617746e553a9e2da…` |
| `SP-800-218A` | Initial | 2026-08-08 | 650,661 | `e088c8bc75716824…` |
| `SP-800-30r1` | Rev 1 | 2026-08-08 | 826,897 | `f214087f0bdb3593…` |
| `SP-800-37r2` | Rev 2 | 2026-08-08 | 2,270,327 | `4f75e1136bb905a6…` |
| `SP-800-39` | Initial (in force) | 2026-08-08 | 1,228,127 | `cf680760d171fc59…` |
| `SP-800-53B-HIGH` | Rev 5 (OSCAL 5.2.0) | 2026-08-08 | 12,492 | `60576970caef91b2…` |
| `SP-800-53B-LOW` | Rev 5 (OSCAL 5.2.0) | 2026-08-08 | 7,234 | `8fd206017c8d718b…` |
| `SP-800-53B-MODERATE` | Rev 5 (OSCAL 5.2.0) | 2026-08-08 | 10,498 | `9030dbf1f1316994…` |
| `SP-800-53B-PRIVACY` | Rev 5 (OSCAL 5.2.0) | 2026-08-08 | 6,064 | `7e650c4397ad633e…` |
| `SP-800-53r5` | Rev 5 (OSCAL 5.2.0) | 2026-08-08 | 10,442,037 | `01f37cf90ea99d92…` |
| `SP-800-60v1r1` | Vol 1 Rev 1 | 2026-08-08 | 338,329 | `6f13f57f11697efc…` |
| `SP-800-60v2r1` | Vol 2 Rev 1 | 2026-08-08 | 1,193,436 | `0b4c5128b39a90f1…` |

`SP-800-53Ar5` rows cite the `SP-800-53r5` catalog hash, because that is the artifact they were
extracted from.

## Limitations

- **PDF section coverage is partial and best-effort.** Layout is not structure. Per-unit rows
  (controls, tasks, subcategories, practices, definitions) are high-confidence; `section` rows are
  the residue of heading detection. Content loss to furniture stripping is under 1% for most
  documents and about 8% for SP 800-218, whose bold bullet lists and two-line headings fragment
  worst.
- **Assessment objectives and methods are one row per control**, not per leaf clause. A single
  determination statement ("account managers are assigned;") is not retrievable on its own. This
  keeps the corpus at ~5.6k coherent rows rather than ~13k fragments.
- **CNSSI 1253 and DoDI 8510.01 are absent** from v1 — cnss.gov and esd.whs.mil block scripted
  retrieval, and their control tables were out of scope for v1 regardless.
- **The AI RMF Playbook is a rolling web resource.** Its rows reflect the version retrieved on the
  date above and will drift as NIST updates it.
- **Two AI RMF Core rows are shorter than the length floor** and were rejected from `AI-100-1`;
  the Playbook carries the same subcategories with full guidance text.
- **This corpus reflects publications as of the build date.** NIST revises documents, sometimes
  without notice — SP 800-18 Rev 1 was withdrawn six weeks before this build. Re-run the pipeline
  rather than assuming currency.
- **Not legal or compliance advice.** These are reference texts; authorization decisions belong to
  the authorizing official.

## License

Source documents are works of the United States Government and are in the public domain under
17 U.S.C. § 105. No copyright is claimed in them. The compilation, curation, manifest, and derived
structure are released under **CC0 1.0 Universal**.

## Maintainer & citation

Maintained by **[@ezesecops](https://github.com/ezesecops)** — <https://ezesecops.com>

```bibtex
@misc{rmf_ato_core_2026,
  author       = {Anene, Ebubeze},
  title        = {RMF/ATO Core Corpus: a curated, provenance-tracked NIST RMF and AI governance dataset},
  year         = {2026},
  publisher    = {Hugging Face},
  howpublished = {\url{https://huggingface.co/datasets/ezesecops/rmf-ato-core}},
  note         = {Built 2026-08-08 from current-revision NIST publications}
}
```

**Found a bad row?** That is the most useful thing you can report. Open an issue at
<https://github.com/ezesecops/rmf-ato-core/issues> with the row `id` — every row traces back
through `sha256_source` to the exact artifact it came from, so problems are reproducible.
