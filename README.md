# Pindorama

> An open dataset of curated Brazilian Portuguese literary public-domain texts.

Pindorama scrapes the [Brazilian Public Domain](https://dominiopublico.gov.br/) repository, downloads the catalog of Portuguese-language literary works, extracts clean text (native PDF parsing for digital-born documents, modern VLM-based OCR for scans), deduplicates, filters for quality, and packages the result as a HuggingFace `datasets` artifact with a Zenodo DOI for academic citation.

**Target size:** ≈220M tokens of literary prose and poetry.
**License:** Public Domain (Brazil), redistributed under an Apache-2.0 wrapper.
**Hub target:** `tylerxdurden/Pindorama`.

This is a research project at the **University of Colorado Boulder, MS-AI program**, run on the [CURC Alpine](https://curc.readthedocs.io/) cluster. A separate technical report comparing modern OCR systems will be derived from the engineering effort.

## Scope of this repository

**This repo is dataset-only.** Its job is the pipeline that produces and publishes the Pindorama dataset — scrape, download, triage, extract, clean, dedup, package, push to HuggingFace Hub, mint a Zenodo DOI. Nothing more.

Three downstream pieces of the broader research arc live in **separate repositories** (not yet created):

1. A **benchmark** built on the Pindorama dataset.
2. A **finetune** of `Gemma 4 26B MoE` on the Pindorama dataset, used to generate large-scale synthetic data.
3. A **from-scratch pretrained model** trained on Pindorama + that synthetic data.

## Status

Pre-implementation. The repository scaffolding is in place; the pipeline itself has not yet been written. See [`PROGRESS.md`](./PROGRESS.md) for the stage manifest and the next concrete action.

## Pipeline (planned)

1. Catalog scrape (CPU, network-bound) → SQLite metadata.
2. PDF download (CPU, network-bound, content-addressed by SHA256).
3. Triage (CPU): native vs needs-OCR vs unusable.
4. Extraction:
   - 4a. Native PDFs via `pymupdf` (CPU).
   - 4b. Scans via `rednote-hilab/dots.ocr` on vLLM (GPU, primary).
   - 4c. Difficult pages via `datalab-to/chandra` on vLLM (GPU, fallback).
5. Post-processing: dehyphenation, normalization, language ID, dedup (MinHash+LSH).
6. Corpus analysis (token counts, vocabulary, distributions).
7. HuggingFace dataset packaging (Parquet shards + dataset card).
8. Zenodo DOI minting.

## Layout

```
.
├── CLAUDE.md           # agent feedforward anchor — start here
├── AGENTS.md           # cross-agent redirect
├── PROGRESS.md         # stage manifest + handoff note
├── pyproject.toml      # uv-managed dependencies (pinned later)
├── src/pindorama/      # library code
├── slurm/              # SLURM submit templates
├── scripts/            # sensors (lint, typecheck, test, secret scan)
├── docs/
│   ├── adr/            # architectural decision records
│   ├── conventions.md  # style + logging
│   ├── runbooks/       # operational procedures
│   └── curc/           # mirrored CURC Alpine documentation
├── tests/
└── notebooks/
```

## Reproducing locally

```bash
# Requires Python 3.11+ and uv (https://docs.astral.sh/uv/).
uv sync
uv run pytest
bash scripts/check.sh
```

To run on CURC Alpine, see [`docs/runbooks/connect-to-alpine.md`](./docs/runbooks/connect-to-alpine.md).

## Author

Daniel Nobrega Medeiros — `dame9177@colorado.edu` — CU Boulder MS-AI.
