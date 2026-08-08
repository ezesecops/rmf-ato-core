.PHONY: help install verify fetch parse parse-oscal parse-pdf chunk validate export all test clean

PY ?= python
MANIFEST ?= manifest.json

help:
	@echo "make install   - editable install with dev deps"
	@echo "make verify    - Stage 1: manifest + URL verification (HUMAN REVIEW)"
	@echo "make fetch     - Stage 2: download artifacts, write provenance.json"
	@echo "make parse     - Stages 3+4: OSCAL and PDF parsing"
	@echo "make chunk     - Stage 5: chunk normalization"
	@echo "make validate  - Stage 6: validation + rejection log (HUMAN REVIEW)"
	@echo "make export    - Stage 7: parquet export"
	@echo "make all       - verify -> export (upload is deliberately NOT included)"
	@echo "make test      - pytest"

install:
	$(PY) -m pip install -e ".[dev]"

verify:
	$(PY) scripts/01_verify_manifest.py --manifest $(MANIFEST)

fetch:
	$(PY) scripts/02_fetch.py --manifest $(MANIFEST)

parse-oscal:
	$(PY) scripts/03_parse_oscal.py --manifest $(MANIFEST)

parse-pdf:
	$(PY) scripts/04_parse_pdf.py --manifest $(MANIFEST)

parse: parse-oscal parse-pdf

chunk:
	$(PY) scripts/05_chunk.py

validate:
	$(PY) scripts/06_validate.py --manifest $(MANIFEST)

export:
	$(PY) scripts/07_export.py

# Upload (08) is never part of `all`: it is gated on human approval by design.
all: verify fetch parse chunk validate export

test:
	$(PY) -m pytest -q

clean:
	rm -rf data/interim/* data/processed/*
