"""Chunk the cancer evidence corpus (docs/PRD-corpus-ingest.md). Run from backend/ with the venv:

    python scripts/ingest_corpus.py [--dry-run] [--limit N] [--rebuild] [--rechunk] [--env PATH] [--corpus-dir PATH]

The logic lives in app/corpus/cli.py so it is importable and tested.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.corpus.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
