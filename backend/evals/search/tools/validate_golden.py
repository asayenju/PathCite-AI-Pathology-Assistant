"""Check golden.jsonl against its schema and the live corpus. Run from backend/:

    python evals/search/tools/validate_golden.py [--env PATH] [--offline]

Checks: unique ids with the right prefix, field set and enum values, the 6 x (9 supported + 6 contradicted) + 10
not_covered distribution, empty gold ids only on not_covered rows, and (unless --offline) that every gold chunk id
exists in Supabase and belongs to a document tagged with the row's cancer. Prints the cancer x type x style table.
Read-only; the key is never printed.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

GOLDEN = HERE.parent / "golden.jsonl"
CANCERS = {"colorectal": "crc", "breast": "brc", "melanoma": "mel", "lymphoma": "lym", "prostate": "pro", "thyroid": "thy"}
TYPES = {"supported", "contradicted", "not_covered"}
STYLES = {"technical", "lay"}
FIELDS = {"id", "cancer", "type", "claim", "context", "needed", "gold_chunk_ids", "style", "reviewed"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()

    rows = [json.loads(line) for line in GOLDEN.read_text().splitlines() if line.strip()]
    errors: list[str] = []
    ids = [r.get("id") for r in rows]
    if len(rows) != 100:
        errors.append(f"expected 100 rows, got {len(rows)}")
    if len(set(ids)) != len(ids):
        errors.append(f"duplicate ids: {[i for i, n in Counter(ids).items() if n > 1]}")

    for r in rows:
        rid = r.get("id")
        if set(r) != FIELDS:
            errors.append(f"{rid}: fields {sorted(set(r) ^ FIELDS)}")
            continue
        if r["cancer"] not in CANCERS or r["type"] not in TYPES or r["style"] not in STYLES:
            errors.append(f"{rid}: bad enum value")
        if not isinstance(r["reviewed"], bool) or not isinstance(r["context"], list):
            errors.append(f"{rid}: reviewed must be a bool and context a list")
        if not r["claim"].strip() or not r["needed"].strip():
            errors.append(f"{rid}: empty claim/needed")
        if not all(isinstance(i, int) for i in r["gold_chunk_ids"]) or len(set(r["gold_chunk_ids"])) != len(
            r["gold_chunk_ids"]
        ):
            errors.append(f"{rid}: gold_chunk_ids must be unique ints")
        if r["type"] == "not_covered":
            if not re.fullmatch(r"nc-\d{2}", rid) or r["gold_chunk_ids"]:
                errors.append(f"{rid}: not_covered rows are nc-NN with no gold ids")
        else:
            if not re.fullmatch(rf"{CANCERS.get(r['cancer'], '?')}-\d{{2}}", rid) or not r["gold_chunk_ids"]:
                errors.append(f"{rid}: id prefix must match cancer and gold ids must be non-empty")

    by_type = Counter((r["cancer"], r["type"]) for r in rows)
    for cancer in CANCERS:
        if by_type[(cancer, "supported")] != 9 or by_type[(cancer, "contradicted")] != 6:
            errors.append(f"{cancer}: expected 9 supported + 6 contradicted, got {by_type}")
    if sum(1 for r in rows if r["type"] == "not_covered") != 10:
        errors.append("expected 10 not_covered rows")

    if not args.offline:
        from corpus_get import client, get

        wanted = sorted({i for r in rows for i in r["gold_chunk_ids"]})
        with client(args.env) as c:
            chunks = {}
            for start in range(0, len(wanted), 100):
                batch = ",".join(str(i) for i in wanted[start : start + 100])
                for ch in get(c, "/chunks", {"select": "id,doc_id", "id": f"in.({batch})"}):
                    chunks[ch["id"]] = ch["doc_id"]
            doc_ids = sorted(set(chunks.values()))
            docs = {}
            for start in range(0, len(doc_ids), 50):
                batch = ",".join(f'"{d}"' for d in doc_ids[start : start + 50])
                for d in get(c, "/documents", {"select": "id,cancers", "id": f"in.({batch})"}):
                    docs[d["id"]] = d["cancers"]
        for r in rows:
            for cid in r["gold_chunk_ids"]:
                if cid not in chunks:
                    errors.append(f"{r['id']}: chunk {cid} not found")
                elif r["cancer"] not in docs.get(chunks[cid], []):
                    errors.append(f"{r['id']}: chunk {cid} doc {chunks[cid]} not tagged {r['cancer']}")
        print(f"verified {len(chunks)}/{len(wanted)} gold chunks in {len(docs)} documents")

    table = Counter((r["cancer"], r["type"], r["style"]) for r in rows)
    print(f"\n{'cancer':<11}{'type':<14}{'technical':>10}{'lay':>5}{'total':>7}")
    for cancer in CANCERS:
        for t in ("supported", "contradicted", "not_covered"):
            tech, lay = table[(cancer, t, "technical")], table[(cancer, t, "lay")]
            if tech or lay:
                print(f"{cancer:<11}{t:<14}{tech:>10}{lay:>5}{tech + lay:>7}")
    styles = Counter(r["style"] for r in rows)
    print(f"\nrows {len(rows)}; technical {styles['technical']}, lay {styles['lay']}")

    if errors:
        print("\nERRORS:\n" + "\n".join(errors))
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
