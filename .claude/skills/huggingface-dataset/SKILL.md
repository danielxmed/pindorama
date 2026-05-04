---
name: huggingface-dataset
description: Use when packaging the cleaned Pindorama corpus into Parquet shards, writing the dataset card (README.md inside the dataset repo), or pushing/updating tylerxdurden/Pindorama on HuggingFace Hub. Triggered by Stage 7 work and any change to the dataset schema.
---

# HuggingFace dataset packaging

This skill is for Stage 7 of the pipeline. The dataset target is `tylerxdurden/Pindorama` (public, Apache-2.0 wrapper around public-domain content).

## Schema (literal — do not change without an ADR)

```python
SCHEMA = {
    "id": str,                # SHA256 of source PDF
    "title": str,
    "author": str,
    "year": "int | None",
    "text": str,              # cleaned full text
    "extraction_method": str, # 'pymupdf' | 'dots_ocr' | 'chandra_ocr'
    "token_count": int,
    "source_url": str,
    "license": str,           # always "Public Domain (Brazil)"
}
```

If a downstream change requires extending the schema, add a column rather than mutating an existing one — old Parquet shards must remain valid.

## Build pattern

Read from the SQLite source-of-truth + `<scratch>/cleaned_text/<sha256>.txt`. Use `datasets.Dataset.from_generator` to avoid loading the entire corpus into memory:

```python
from datasets import Dataset, Features, Value

features = Features(
    {
        "id": Value("string"),
        "title": Value("string"),
        "author": Value("string"),
        "year": Value("int32"),     # nullable in arrow; int32 is plenty
        "text": Value("string"),
        "extraction_method": Value("string"),
        "token_count": Value("int64"),
        "source_url": Value("string"),
        "license": Value("string"),
    }
)

def row_iter():
    for row in db_iter():       # query SQLite, yield dicts
        yield row

ds = Dataset.from_generator(row_iter, features=features)
```

Single split: `train` (this is a pretraining corpus). Shard size: ≈500 MB Parquet:

```python
ds.save_to_disk(out_dir, num_shards=ceil(total_bytes / (500 * 1024 * 1024)))
```

## Auth

`HF_TOKEN` is loaded from environment, **never** hardcoded:

```python
import os
from huggingface_hub import HfApi

token = os.environ["HF_TOKEN"]   # KeyError if absent — fail loud
api = HfApi(token=token)
```

The pre-commit hook scans for `\bhf_[A-Za-z0-9]{32,}\b`. Don't bypass it.

## Push pattern

```python
from datasets import load_from_disk

ds = load_from_disk(out_dir)
ds.push_to_hub(
    repo_id="tylerxdurden/Pindorama",
    token=os.environ["HF_TOKEN"],
    private=False,
    commit_message=f"Pindorama vX.Y — {n_docs} docs, {total_tokens:,} tokens",
)
```

Idempotency: pushes are content-addressed by HF; an unchanged dataset push is a no-op. Add a content hash to the commit message so Daniel can identify the build.

## Dataset card

The card lives at `<dataset_repo>/README.md` and is rendered on the Hub. Required sections:

1. **TL;DR** — one paragraph: what is Pindorama, size, license.
2. **Citation** — Zenodo DOI once minted (Stage 8); BibTeX block.
3. **Data sources** — `dominiopublico.gov.br`, scrape methodology, snapshot date.
4. **Schema** — table mirroring the SCHEMA above.
5. **Statistics** — total docs, total tokens against **one** canonical tokenizer (the dataset card names which one; default is `TucanoBR/Tucano-2b4` until Daniel says otherwise — this repo is dataset-only and downstream model choice is not a blocker here), distribution by extraction_method, by year, by top-50 authors. Reproduce from `corpus_report.md`. Downstream consumers re-tokenize against whatever model they target.
6. **Known limitations** — OCR error rate (sample-based estimate), language-ID misses, near-duplicate residual after MinHash+LSH dedup.
7. **Ethical considerations** — public-domain texts only; no living-author work; OCR may introduce factual errors not in the source.
8. **License** — Public Domain (Brazil) on the content; Apache-2.0 on the wrapper / scripts.
9. **Authors & contact** — Daniel Nobrega Medeiros, `dame9177@colorado.edu`, CU Boulder MS-AI.
10. **Acknowledgements** — CURC Alpine, dots.ocr authors, chandra authors.

## What this skill does NOT cover

- Cleaning / dedup / tokenization → those are upstream stages; the Parquet builder consumes their outputs and does no transformation beyond schema enforcement.
- Zenodo DOI minting → Stage 8 is interactive (manual form). Generate the metadata block to paste; do not try to automate it.
