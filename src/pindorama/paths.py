"""Canonical filesystem paths for Pindorama, derived from environment.

Conventions (cf. CLAUDE.md → "Storage rule"):
    - Code → repo (committed).
    - Persistent dataset / cleaned corpus → /projects/$USER/pindorama
    - Hot working artifacts (raw PDFs, extracted text, intermediate state)
      → /scratch/alpine/$USER/pindorama  (purge-eligible)
    - Per-job ephemeral → $SLURM_SCRATCH (wiped at job end)

This module never *creates* directories silently; callers should `path.mkdir(
parents=True, exist_ok=True)` if they need it. That keeps file-system writes
loud and intentional.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _user() -> str:
    """Identikey of the running user. Falls back to $USER, then $LOGNAME, then `unknown`."""
    return os.environ.get("USER") or os.environ.get("LOGNAME") or "unknown"


@dataclass(frozen=True)
class PindoramaPaths:
    """Resolved filesystem layout for one user.

    Use the module-level `default()` to construct from the current env, or
    instantiate explicitly when you need to point at a specific user's tree
    (e.g. in tests).
    """

    scratch_root: Path
    persistent_root: Path
    job_scratch: Path | None  # None when not running inside a SLURM job.

    @property
    def metadata_db(self) -> Path:
        return self.scratch_root / "metadata.sqlite"

    @property
    def raw_pdfs(self) -> Path:
        return self.scratch_root / "raw_pdfs"

    @property
    def extracted_text(self) -> Path:
        return self.scratch_root / "extracted_text"

    @property
    def cleaned_text(self) -> Path:
        return self.scratch_root / "cleaned_text"

    @property
    def logs(self) -> Path:
        return self.scratch_root / "logs"

    @property
    def backups(self) -> Path:
        return self.scratch_root / "backups"

    @property
    def corpus_report(self) -> Path:
        return self.scratch_root / "corpus_report.md"


def default() -> PindoramaPaths:
    """Resolve paths from environment.

    Honors PINDORAMA_SCRATCH and PINDORAMA_PERSISTENT overrides (see .env.example).
    Falls back to the canonical Alpine layout otherwise.
    """
    user = _user()
    scratch = Path(os.environ.get("PINDORAMA_SCRATCH") or f"/scratch/alpine/{user}/pindorama")
    persistent = Path(os.environ.get("PINDORAMA_PERSISTENT") or f"/projects/{user}/pindorama")
    job_scratch_env = os.environ.get("SLURM_SCRATCH")
    job_scratch = Path(job_scratch_env) if job_scratch_env else None
    return PindoramaPaths(
        scratch_root=scratch,
        persistent_root=persistent,
        job_scratch=job_scratch,
    )
