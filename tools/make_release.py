"""Assemble the public release folder (no law texts, no internal notes).

    python tools/make_release.py [--out build/release/ai-law-atlas]

Copies the public-facing files in release/ (README, LICENSE, LICENSE-code, NOTICE, DATASHEET, CITATION, CHANGELOG)
plus the registry outputs, annotations, tables, schemas, method docs, validation records and code. Excluded:
cache/ (texts), logs/ (spend ledger), build/rollout/, handoffs and agent instructions, scouting notes, sources/,
the access log and the human-review package's pending answers.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = {
    "registry/instruments.jsonl": "registry/instruments.jsonl",
    "registry/links.csv": "registry/links.csv",
    "registry/families.jsonl": "registry/families.jsonl",
    "registry/triage.jsonl": "registry/triage.jsonl",
    "registry/coverage.md": "registry/coverage.md",
    "docs/CODEBOOK.md": "docs/CODEBOOK.md",
    "docs/DESIGN.md": "docs/DESIGN.md",
    "docs/VALIDATION_PROTOCOL.md": "docs/VALIDATION_PROTOCOL.md",
    "docs/validation_log.md": "docs/validation_log.md",
    "docs/pilot_2026-10.md": "docs/pilot_2026-10.md",
    "requirements.txt": "requirements.txt",
}
DIRS = {"annotations": "annotations", "schemas": "schemas", "docs/prompts": "docs/prompts",
        "registry/crosswalk": "registry/crosswalk", "validation": "validation", "tools": "tools",
        "connectors": "connectors"}
TABLES = ["instruments", "classifications", "events", "thresholds", "obligations", "enforcement", "definitions",
          "relations", "entities", "registry", "registry_domains", "registry_keys", "registry_families", "links"]


def main() -> None:
    out = ROOT / (sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else "build/release/ai-law-atlas")
    out.mkdir(parents=True, exist_ok=True)
    for p in out.iterdir():  # keep .git: the folder is the public repo's clone
        if p.name != ".git":
            shutil.rmtree(p) if p.is_dir() else p.unlink()
    for f in (ROOT / "release").iterdir():
        shutil.copy2(f, out / f.name)
    for src, dst in FILES.items():
        (out / dst).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / src, out / dst)
    for src, dst in DIRS.items():
        shutil.copytree(ROOT / src, out / dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (out / "data").mkdir()
    for t in TABLES:
        p = ROOT / "build" / f"{t}.csv"
        if p.exists():
            shutil.copy2(p, out / "data" / p.name)
    shutil.copy2(ROOT / "build" / "annotations.sqlite", out / "data" / "annotations.sqlite")
    files = [p for p in out.rglob("*") if p.is_file() and ".git" not in p.relative_to(out).parts]
    n, size = len(files), sum(p.stat().st_size for p in files) / 1e6
    print(f"{out}: {n} files, {size:.1f} MB")


if __name__ == "__main__":
    main()
