"""Build the claim checker's local BM25 keyword index (app/check/bm25.py, issue #56). Run from backend/:

    python scripts/build_bm25_index.py [--env PATH] [--out DIR] [--if-stale]

Reads every chunk (id, section, content, cancers) from Supabase in keyset pages (read-only, SUPABASE_URL and
SUPABASE_SECRET_KEY from the repo-root .env), builds the index and saves it to backend/.cache/corpus/bm25/ (or --out),
with a manifest holding the corpus fingerprint. --if-stale skips the build when the saved index already matches the
corpus. Restart the backend afterwards so /check loads it. The ingest CLI runs this automatically after an ingest.

Exit codes: 0 ok, 2 missing keys or Supabase unreachable. Keys are never printed.
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.check import bm25  # noqa: E402
from app.config import Settings  # noqa: E402
from app.corpus.supabase import SupabaseCorpus, SupabaseError  # noqa: E402


def main(argv=None, *, supabase_transport=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--env", type=Path, default=None, help="the .env with the keys (default: repo root)")
    p.add_argument("--out", type=Path, default=None, help=f"index directory (default: {bm25.DEFAULT_DIR})")
    p.add_argument("--if-stale", action="store_true", help="only rebuild when the saved index doesn't match")
    args = p.parse_args(argv)
    if args.env is not None and not args.env.is_file():
        print(f"--env: no such file: {args.env}", file=sys.stderr)
        return 2
    settings = Settings(_env_file=args.env) if args.env else Settings()
    out = args.out or settings.bm25_index_dir
    key = settings.supabase_secret_key.get_secret_value().strip() if settings.supabase_secret_key else ""
    if not (settings.supabase_url or "").strip() or not key:
        print("Set SUPABASE_URL and SUPABASE_SECRET_KEY in the .env", file=sys.stderr)
        return 2
    t0 = time.perf_counter()
    try:
        with SupabaseCorpus(settings.supabase_url, key, transport=supabase_transport, timeout_s=60) as store:
            if args.if_stale:
                index, rebuilt = bm25.refresh_from_store(store, out, progress=print)
            else:
                index, rebuilt = bm25.build_from_store(store, out, progress=print), True
    except SupabaseError as e:
        print(f"Stopped: {e}", file=sys.stderr)
        return 2
    if not rebuilt:
        print(f"Up to date: {out}")
        return 0
    build_s = time.perf_counter() - t0
    t1 = time.perf_counter()
    loaded = bm25.Bm25Index.load(out)
    load_s = time.perf_counter() - t1
    print(f"Built {bm25.manifest_summary(index.manifest)}")
    print(f"  cancers: {', '.join(f'{k} {v:,}' for k, v in index.manifest['cancers'].items()) or '-'}")
    print(f"  {bm25.dir_size(out) / 1e6:.1f} MB at {out}; read + build {build_s:.1f} s, load {load_s:.2f} s "
          f"({len(loaded):,} chunks)")  # fmt: skip
    print("Restart the backend so /check loads it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
