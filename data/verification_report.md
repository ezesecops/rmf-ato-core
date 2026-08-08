# Stage 1 — Manifest verification report

Documents in manifest: **21**

| doc_id | format | tier | URL status | notes |
|---|---|---|---|---|
| `SP-800-37r2` | pdf | 1 | 200 (HEAD) |  |
| `SP-800-53r5` | oscal | 1 | 200 (HEAD) |  |
| `SP-800-53Ar5` | embedded-in-oscal | 1 | — (no url) | no url (manual or embedded source) |
| `SP-800-53B-LOW` | oscal | 1 | 200 (HEAD) |  |
| `SP-800-53B-MODERATE` | oscal | 1 | 200 (HEAD) |  |
| `SP-800-53B-HIGH` | oscal | 1 | 200 (HEAD) |  |
| `SP-800-53B-PRIVACY` | oscal | 1 | 200 (HEAD) |  |
| `FIPS-199` | pdf | 1 | 200 (HEAD) |  |
| `FIPS-200` | pdf | 1 | 206 (GET (range)) | HEAD returned 404, confirmed by ranged GET |
| `SP-800-60v1r1` | pdf | 1 | 206 (GET (range)) | HEAD returned 404, confirmed by ranged GET |
| `SP-800-60v2r1` | pdf | 1 | 206 (GET (range)) | HEAD returned 404, confirmed by ranged GET |
| `SP-800-18r2` | pdf | 1 | 206 (GET (range)) | HEAD returned 404, confirmed by ranged GET |
| `SP-800-30r1` | pdf | 1 | 206 (GET (range)) | HEAD returned 404, confirmed by ranged GET |
| `SP-800-39` | pdf | 1 | 200 (HEAD) |  |
| `SP-800-137` | pdf | 1 | 206 (GET (range)) | HEAD returned 404, confirmed by ranged GET |
| `AI-100-1` | pdf | 2 | 206 (GET (range)) | HEAD returned 404, confirmed by ranged GET |
| `AI-RMF-PLAYBOOK` | web | 2 | 200 (HEAD) |  |
| `SP-800-218` | pdf | 2 | 206 (GET (range)) | HEAD returned 404, confirmed by ranged GET |
| `SP-800-218A` | pdf | 2 | 206 (GET (range)) | HEAD returned 404, confirmed by ranged GET |
| `CNSSI-1253` | pdf-manual | 2 | — (no url) | no url (manual or embedded source) |
| `DoDI-8510.01` | pdf-manual | 2 | — (no url) | no url (manual or embedded source) |

## Supersession checks

| doc_id | manifest revision | finding |
|---|---|---|
| `SP-800-60v1r1` | Vol 1 Rev 1 | no supersession signal found on landing page |
| `SP-800-60v2r1` | Vol 2 Rev 1 | no supersession signal found on landing page |
| `SP-800-18r2` | Rev 2 | no supersession signal found on landing page |
| `SP-800-218` | 1.1 | no supersession signal found on landing page |

## Verdict

All non-null URLs resolved successfully.

Manual documents (human must place the PDF before Stage 4):

- `CNSSI-1253` -> `data/raw/manual/CNSSI-1253.pdf` (source: https://www.cnss.gov/CNSS/issuances/Instructions.cfm)
- `DoDI-8510.01` -> `data/raw/manual/DoDI-8510.01.pdf` (source: https://www.esd.whs.mil/DD/DoD-Issuances/)

**HUMAN REVIEW:** approve this report (and any manifest corrections) before Stage 2 fetch.
