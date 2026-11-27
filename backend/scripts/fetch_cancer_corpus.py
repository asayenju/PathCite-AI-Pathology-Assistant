"""Download a cancer-only evidence corpus for the claim checker, limited to the cancers in the sample transcripts.

Sources:
- Clinical guidelines from epfl-llm/guidelines (Hugging Face), filtered server-side by title, so the 425 MB dataset is
  never downloaded in full. Licence varies by source (see the dataset card); keep it out of git.
- PubMed reviews and guidelines with abstracts (NCBI E-utilities), searched by MeSH term per cancer type.

Writes JSONL to backend/.cache/corpus/ (gitignored) plus a manifest with counts. Stdlib only.

    python scripts/fetch_cancer_corpus.py                 # both sources
    python scripts/fetch_cancer_corpus.py --pubmed-max 500
    python scripts/fetch_cancer_corpus.py --relink-only   # just add PubMed links to the saved guidelines
"""

import argparse
import difflib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / ".cache" / "corpus"

# The cancers covered by the sample transcripts (backend/tests/fixtures/transcripts/scenarios.py).
CANCERS = {
    "colorectal": {
        "titles": ["colorectal", "colon cancer", "colon and rect", "colon polyp", "colonic polyp", "rectal cancer",
                   "bowel cancer", "bowel screening", "adenomatous polyp", "polyposis", "lynch", "colonoscop",
                   "ulcerative colitis"],
        "exclude": ["moderately to severely active", "acute exacerbation", "fundic gland", "ventriculi", "endometrial"],
        "mesh": '"Colorectal Neoplasms"[MeSH] OR "Adenomatous Polyps"[MeSH] OR "Colitis, Ulcerative/complications"[MeSH]',
    },
    "breast": {
        "titles": ["breast", "fibroadenoma", "mammogra"],
        "exclude": ["breastfeed", "breast milk", "breast pump", "engorgement", "breast reduction", "breast shell",
                    "prosthes", "breast implant", "tongue-tie", "breast health", "pain and discharge", "vaccination"],
        "mesh": '"Breast Neoplasms"[MeSH] OR "Fibroadenoma"[MeSH]'},
    "melanoma": {"titles": ["melanoma"], "mesh": '"Melanoma"[MeSH]'},
    "lymphoma": {"titles": ["lymphoma"], "mesh": '"Lymphoma, Large B-Cell, Diffuse"[MeSH]'},
    "prostate": {"titles": ["prostate cancer", "prostatic carcinoma", "prostate carcinoma", "prostatic neoplasm",
                             "prostatectomy", "prostate-specific antigen", "gleason"], "mesh": '"Prostatic Neoplasms"[MeSH]'},
    "thyroid": {"titles": ["thyroid cancer", "thyroid nodule", "thyroid carcinoma", "thyroid neoplasm"],
                "mesh": '"Thyroid Neoplasms"[MeSH] OR "Thyroid Nodule"[MeSH]'},
}  # fmt: skip

HF_FILTER = "https://datasets-server.huggingface.co/filter"
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
TOOL = "corpus-fetch"  # NCBI asks for a tool name; no email is sent.


def get(url: str, params: dict, *, tries: int = 6) -> bytes:
    full = f"{url}?{urllib.parse.urlencode(params)}"
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(full, timeout=90) as r:
                return r.read()
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
            code = getattr(e, "code", None)
            if code not in (None, 429, 500, 502, 503, 504) or attempt == tries - 1:
                raise
            time.sleep(2**attempt)
    raise AssertionError("unreachable")


def fetch_guidelines() -> dict[str, int]:
    seen: dict[str, dict] = {}
    for cancer, spec in CANCERS.items():
        where = "(" + " OR ".join(f"\"title\" ILIKE '%{t}%'" for t in spec["titles"]) + ")"
        exclude = [t.lower() for t in spec.get("exclude", [])]  # applied here: long WHERE clauses get a 422
        offset, total = 0, None
        while total is None or offset < total:
            d = json.loads(get(HF_FILTER, {"dataset": "epfl-llm/guidelines", "config": "default", "split": "train",
                                           "where": where, "offset": offset, "length": 10}))  # fmt: skip
            total = d["num_rows_total"]
            for r in d["rows"]:
                row = r["row"]
                if any(t in (row["title"] or "").lower() for t in exclude):
                    continue
                doc = seen.setdefault(row["id"], {
                    "id": f"guideline:{row['id']}", "source": row["source"], "title": row["title"],
                    "url": row["url"], "text": row["clean_text"], "cancers": [],
                })  # fmt: skip
                if cancer not in doc["cancers"]:
                    doc["cancers"].append(cancer)
            offset += len(d["rows"])
            if not d["rows"]:
                break
            time.sleep(0.3)
        print(f"guidelines {cancer}: {total}")
    return _write_guidelines(link_pubmed_guidelines(list(seen.values())))


def _write_guidelines(docs: list[dict]) -> dict[str, int]:
    with open(OUT / "guidelines.jsonl", "w") as f:
        for doc in docs:
            f.write(json.dumps(doc) + "\n")
    return _count(docs)


_STOPWORDS = frozenset(
    "about after all also and are been before being between both but can could did does done during each for from had "
    "has have how into its may more most not now off only other our out over should such than that the their them "
    "then there these they this those through under upon very was were what when where which while who why will with "
    "within without would".split()
)
_DOI = re.compile(r"10\.\d{4,9}/[^\s\"<>?#]+")


def link_pubmed_guidelines(docs: list[dict]) -> list[dict]:
    """Give epfl's PubMed-sourced guidelines a PMID and a stable PubMed link.

    epfl-llm/guidelines stores no PMID for these (its ids are hashes, and its url is a publisher PDF or the string
    "None"), so each is looked up in PubMed: by the DOI in its url when there is one, else by exact title. A guideline
    with no match keeps its publisher url if it has one and is dropped if it has none, so every document is citable.
    """
    kept: list[dict] = []
    linked = dropped = 0
    for doc in docs:
        url = (doc.get("url") or "").strip()
        if url in ("", "None"):
            url = doc["url"] = ""
        if doc["source"] != "pubmed" or doc.get("pmid"):
            kept.append(doc)
            continue
        pmid = _find_pmid(doc["title"], _doi_from(url))
        if pmid:
            doc.update(pmid=pmid, url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/")
            linked += 1
        if doc["url"]:
            kept.append(doc)
        else:
            dropped += 1
            print(f"  dropped (no PubMed match, no url): {doc['title'][:80]}")
    print(f"pubmed-sourced guidelines: {linked} linked to PubMed, {dropped} dropped")
    return kept


def _doi_from(url: str) -> str | None:
    m = _DOI.search(urllib.parse.unquote(url))
    return m.group().rstrip(".").removesuffix("/pdf").removesuffix(".pdf") if m else None


def _find_pmid(title: str, doi: str | None) -> str | None:
    if doi:
        pmids = _esearch(f"{doi}[doi]")
        if len(pmids) == 1:
            return pmids[0]
    # Tag each word: a bare phrase gets rewritten by PubMed's term mapping (e.g. "Chinese Society of …" as an author).
    # PubMed stopwords ("for", "with"…) tagged [ti] make the whole search return nothing, so they're left out.
    words = [w for w in re.findall(r"[A-Za-z0-9]+", title) if len(w) > 2 and w.lower() not in _STOPWORDS][:10]
    candidates = _esearch(" AND ".join(f"{w}[ti]" for w in words))[:5] if words else []
    if not candidates:
        return None
    time.sleep(0.4)
    summary = json.loads(get(f"{EUTILS}/esummary.fcgi", {"db": "pubmed", "id": ",".join(candidates),
                                                          "retmode": "json", "tool": TOOL}))["result"]  # fmt: skip
    for pmid in candidates:
        if _same_title(title, summary.get(pmid, {}).get("title", "")):
            return pmid
    return None


def _esearch(term: str) -> list[str]:
    time.sleep(0.4)  # 3 requests/s without an API key
    d = json.loads(get(f"{EUTILS}/esearch.fcgi", {"db": "pubmed", "term": term, "retmode": "json", "tool": TOOL}))
    return d["esearchresult"]["idlist"]


def _same_title(a: str, b: str) -> bool:
    norm = lambda s: re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()  # noqa: E731
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio() >= 0.9


def fetch_pubmed(per_cancer: int) -> dict[str, int]:
    seen: dict[str, dict] = {}
    for cancer, spec in CANCERS.items():
        term = (f"({spec['mesh']}) AND (review[pt] OR guideline[pt] OR practice guideline[pt]) "
                "AND hasabstract AND english[la] AND 2016:2026[dp]")  # fmt: skip
        ids = json.loads(get(f"{EUTILS}/esearch.fcgi", {"db": "pubmed", "term": term, "retmax": per_cancer,
                                                        "sort": "relevance", "retmode": "json", "tool": TOOL}))  # fmt: skip
        pmids = ids["esearchresult"]["idlist"]
        print(f"pubmed {cancer}: {len(pmids)} of {ids['esearchresult']['count']} matches")
        for i in range(0, len(pmids), 100):
            time.sleep(0.4)  # 3 requests/s without an API key
            xml = get(f"{EUTILS}/efetch.fcgi", {"db": "pubmed", "id": ",".join(pmids[i : i + 100]),
                                                "retmode": "xml", "tool": TOOL})  # fmt: skip
            for art in ET.fromstring(xml).iter("PubmedArticle"):
                doc = _parse_article(art)
                if doc is None:
                    continue
                kept = seen.setdefault(doc["id"], doc)
                if cancer not in kept["cancers"]:
                    kept["cancers"].append(cancer)
        time.sleep(0.4)
    with open(OUT / "pubmed.jsonl", "w") as f:
        for doc in seen.values():
            f.write(json.dumps(doc) + "\n")
    return _count(seen.values())


def _parse_article(art: ET.Element) -> dict | None:
    pmid = art.findtext(".//MedlineCitation/PMID")
    abstract = " ".join(
        (f"{p.get('Label')}: " if p.get("Label") else "") + "".join(p.itertext()).strip()
        for p in art.findall(".//Abstract/AbstractText")
    ).strip()
    if not pmid or not abstract:
        return None
    return {
        "id": f"pubmed:{pmid}",
        "source": "pubmed",
        "title": "".join(art.find(".//ArticleTitle").itertext()).strip() if art.find(".//ArticleTitle") is not None else "",
        "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        "journal": art.findtext(".//Journal/Title") or "",
        "year": art.findtext(".//PubDate/Year") or (art.findtext(".//PubDate/MedlineDate") or "")[:4],
        "pub_types": [p.text for p in art.findall(".//PublicationType") if p.text],
        "text": abstract,
        "cancers": [],
    }


def _count(docs) -> dict[str, int]:
    docs = list(docs)
    out = {c: sum(c in d["cancers"] for d in docs) for c in CANCERS}
    out["total_unique"] = len(docs)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pubmed-max", type=int, default=300, help="PubMed records per cancer type")
    ap.add_argument("--skip-guidelines", action="store_true")
    ap.add_argument("--skip-pubmed", action="store_true")
    ap.add_argument("--relink-only", action="store_true", help="only add PubMed links to the saved guidelines.jsonl")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    mpath = OUT / "manifest.json"
    manifest = json.loads(mpath.read_text()) if mpath.exists() else {}
    manifest.update({"fetched": time.strftime("%Y-%m-%d"), "cancers": list(CANCERS)})
    if args.relink_only:
        docs = [json.loads(line) for line in open(OUT / "guidelines.jsonl")]
        manifest["guidelines"] = _write_guidelines(link_pubmed_guidelines(docs))
    elif not args.skip_guidelines:
        manifest["guidelines"] = fetch_guidelines()
    if not args.skip_pubmed and not args.relink_only:
        manifest["pubmed"] = fetch_pubmed(args.pubmed_max)
    mpath.write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
