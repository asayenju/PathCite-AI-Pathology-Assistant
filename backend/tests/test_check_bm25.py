"""Offline tests for the local BM25 keyword index (app/check/bm25.py, issue #56): build/save/load, tokenizing,
ranking, the cancer filter, the corpus fingerprint and the lazy, cached provider. No network."""

import json
import logging
import threading

import pytest

from app.check import bm25
from app.check.bm25 import Bm25Index, IndexRow, KeywordIndexProvider, tokenize

META = {"chunker_config_hash": "cfg-1", "quality_filter_hash": "qf-1", "embedding_model": "m", "dims": 1536}

ROWS = [
    IndexRow(1, "Staging", "Breslow thickness is measured from the granular layer of the epidermis.", ("melanoma",)),
    IndexRow(2, "Follow-up", "Offer follow-up to people with melanoma after treatment of the cancer.", ("melanoma",)),
    IndexRow(3, "Spread", "Liver metastases are common in colorectal cancer.", ("colorectal",)),
    IndexRow(4, "Screening", "Colonoscopy screening reduces colorectal cancer deaths.", ("colorectal",)),
    IndexRow(5, "Screening", "Mammography screening for breast cancer in women aged 50 to 70.", ("breast",)),
    IndexRow(6, "General", "The cancer team discusses treatment of the cancer with the patient.", ()),
    IndexRow(7, "Nodes", "Sentinel lymph node biopsy in melanoma and breast cancer.", ("melanoma", "breast")),
]


def fp(rows=ROWS, meta=META):
    return bm25.fingerprint(meta, chunks=len(rows), max_id=max(r.chunk_id for r in rows))


@pytest.fixture(scope="module")
def index():
    return Bm25Index.build(ROWS, META)


def ids(results):
    return [cid for cid, _ in results]


# --- tokenizing -------------------------------------------------------------------------------------------------


def test_tokenize_lowercases_drops_stopwords_and_stems():
    assert tokenize("The Tumours and the Nodes") == ["tumour", "node"]


@pytest.mark.parametrize("a,b", [("metastases", "metastasis"), ("diagnoses", "diagnosis"), ("biopsies", "biopsy"),
                                 ("screening", "screened"), ("HER2", "her2")])  # fmt: skip
def test_word_forms_share_a_token(a, b):
    assert tokenize(a) == tokenize(b) and tokenize(a)


def test_tokenize_is_safe_from_many_threads():
    words = "metastases screening biopsies nodes tumours diagnoses " * 50
    expected = tokenize(words)
    out, errors = [], []

    def work():
        try:
            for _ in range(20):
                out.append(tokenize(words) == expected)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and all(out)


# --- search -----------------------------------------------------------------------------------------------------


def test_stemmed_query_finds_the_other_word_form(index):
    assert ids(index.search("metastasis")) == [3]


def test_section_is_indexed_too(index):
    assert ids(index.search("spread")) == [3]


def test_rare_term_beats_common_terms(index):
    # "cancer" is in most chunks; "Breslow" in one: the Breslow chunk must win.
    results = index.search("cancer Breslow")
    assert results[0][0] == 1
    assert results[0][1] > results[1][1]


def test_scores_are_positive_and_sorted_and_only_matches_are_returned(index):
    results = index.search("screening")
    assert ids(results) and set(ids(results)) == {4, 5}
    scores = [s for _, s in results]
    assert scores == sorted(scores, reverse=True) and all(s > 0 for s in scores)
    assert all(isinstance(c, int) and isinstance(s, float) for c, s in results)


@pytest.mark.parametrize("text", ["", "   ", "the and of", "zzzunknownword"])
def test_no_known_terms_is_an_empty_list(index, text):
    assert index.search(text) == []


def test_k_caps_the_results(index):
    assert len(index.search("cancer", k=2)) == 2
    with pytest.raises(ValueError):
        index.search("cancer", k=0)


def test_cancer_filter_applies_before_top_k(index):
    # Unfiltered, the top 2 for "cancer screening" are 4 and 5; with the breast filter the breast chunks are kept
    # even though colorectal chunks outrank them.
    results = index.search("cancer screening", k=2, cancer="breast")
    assert ids(results)[0] == 5
    assert set(ids(results)) <= {5, 7}
    assert len(results) == 2


def test_cancer_filter_with_fewer_matches_than_k(index):
    assert set(ids(index.search("cancer", k=40, cancer="colorectal"))) == {3, 4}
    assert index.search("Breslow", cancer="colorectal") == []


def test_unknown_cancer_label_matches_nothing(index):
    assert index.search("cancer", cancer="thyroid") == []


def test_ties_break_by_chunk_id():
    rows = [IndexRow(i, "", "same words here", ()) for i in (30, 10, 20)]
    idx = Bm25Index.build(rows, META)
    assert ids(idx.search("same words")) == [10, 20, 30]


def test_concurrent_searches_agree(index):
    expected = index.search("cancer screening melanoma", cancer="melanoma")
    out = []

    def work():
        for _ in range(50):
            out.append(index.search("cancer screening melanoma", cancer="melanoma") == expected)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(out) and len(out) == 400


# --- save / load / manifest -------------------------------------------------------------------------------------


def test_round_trip(tmp_path, index):
    index.save(tmp_path / "bm25")
    loaded = Bm25Index.load(tmp_path / "bm25")
    assert len(loaded) == len(ROWS)
    for q, c in [("cancer Breslow", None), ("screening", "breast"), ("metastases", None), ("node", "melanoma")]:
        assert loaded.search(q, cancer=c) == index.search(q, cancer=c)
    assert loaded.manifest == index.manifest


def test_manifest_has_the_fingerprint(tmp_path, index):
    index.save(tmp_path / "bm25")
    manifest = json.loads((tmp_path / "bm25" / "manifest.json").read_text())
    assert manifest["fingerprint"] == fp()
    assert manifest["chunks"] == len(ROWS)
    assert len(manifest["ids_sha256"]) == 64
    assert Bm25Index.read_manifest(tmp_path / "bm25") == manifest
    assert index.matches(fp())


def test_save_replaces_an_older_index(tmp_path, index):
    Bm25Index.build(ROWS[:2], META).save(tmp_path / "bm25")
    index.save(tmp_path / "bm25")
    assert len(Bm25Index.load(tmp_path / "bm25")) == len(ROWS)
    assert [p.name for p in tmp_path.iterdir()] == ["bm25"]


def test_missing_index(tmp_path):
    with pytest.raises(bm25.IndexMissing):
        Bm25Index.load(tmp_path / "nope")
    assert Bm25Index.read_manifest(tmp_path / "nope") is None


def test_corrupt_index(tmp_path, index):
    index.save(tmp_path / "bm25")
    (tmp_path / "bm25" / "ids.npy").write_bytes(b"garbage")
    with pytest.raises(bm25.IndexError_):
        Bm25Index.load(tmp_path / "bm25")


def test_build_rejects_duplicate_ids():
    with pytest.raises(ValueError):
        Bm25Index.build([ROWS[0], ROWS[0]], META)


# --- fingerprint ------------------------------------------------------------------------------------------------


def test_fingerprint_fields():
    assert fp() == {"chunks": 7, "max_id": 7, "chunker_config_hash": "cfg-1", "quality_filter_hash": "qf-1"}


@pytest.mark.parametrize("change", [{"chunks": 8}, {"max_id": 9}, {"chunker_config_hash": "x"},
                                    {"quality_filter_hash": None}])  # fmt: skip
def test_fingerprint_mismatch(index, change):
    assert not index.matches({**fp(), **change})


# --- provider ---------------------------------------------------------------------------------------------------


class FakeStore:
    def __init__(self, meta=META, count=7, max_id=7, fail=None):
        self.meta, self.count, self.max_id, self.fail = meta, count, max_id, fail
        self.calls = 0

    def read_meta(self):
        self.calls += 1
        if self.fail:
            raise self.fail
        return dict(self.meta)

    def chunk_count_and_max_id(self):
        if self.fail:
            raise self.fail
        return self.count, self.max_id


def test_provider_loads_once_and_checks_the_fingerprint_once(tmp_path, index):
    index.save(tmp_path / "bm25")
    provider = KeywordIndexProvider(tmp_path / "bm25")
    store = FakeStore()
    first = provider.get(store)
    assert first is not None and provider.get(store) is first
    assert store.calls == 1
    assert provider.status == "ready"


def test_provider_missing_index_falls_back_and_logs_once(tmp_path, caplog):
    provider = KeywordIndexProvider(tmp_path / "bm25")
    store = FakeStore()
    with caplog.at_level(logging.WARNING, logger="pathcite"):
        assert provider.get(store) is None
        assert provider.get(store) is None
    assert provider.status == "missing"
    assert store.calls == 0  # no DB round trip without an index
    assert len([r for r in caplog.records if "BM25" in r.getMessage()]) == 1


@pytest.mark.parametrize("store", [FakeStore(count=8), FakeStore(max_id=99), FakeStore(meta={**META, "quality_filter_hash": "qf-2"})])
def test_provider_stale_index_falls_back_and_is_cached(tmp_path, index, store, caplog):
    index.save(tmp_path / "bm25")
    provider = KeywordIndexProvider(tmp_path / "bm25")
    with caplog.at_level(logging.WARNING, logger="pathcite"):
        assert provider.get(store) is None
        assert provider.get(store) is None
    assert provider.status == "stale"
    assert store.calls == 1
    assert len([r for r in caplog.records if "BM25" in r.getMessage()]) == 1


def test_provider_does_not_cache_an_upstream_failure(tmp_path, index, caplog):
    from app.corpus.supabase import SupabaseUnreachable

    index.save(tmp_path / "bm25")
    provider = KeywordIndexProvider(tmp_path / "bm25")
    down = FakeStore(fail=SupabaseUnreachable("down"))
    with caplog.at_level(logging.WARNING, logger="pathcite"):
        assert provider.get(down) is None
    assert "SupabaseUnreachable" in caplog.text and "down" not in caplog.text.replace("SupabaseUnreachable", "")
    assert provider.get(FakeStore()) is not None


def test_provider_disable(tmp_path, index):
    index.save(tmp_path / "bm25")
    provider = KeywordIndexProvider(tmp_path / "bm25")
    store = FakeStore()
    assert provider.get(store) is not None
    provider.disable("rpc_missing")
    assert provider.get(store) is None and provider.status == "rpc_missing"


def test_default_provider_is_shared_per_directory(tmp_path):
    a = bm25.default_provider(tmp_path / "x")
    assert bm25.default_provider(tmp_path / "x") is a
    assert bm25.default_provider(tmp_path / "y") is not a


# --- build from the store ---------------------------------------------------------------------------------------


class PagedStore(FakeStore):
    def __init__(self, rows):
        super().__init__(count=len(rows), max_id=max(r.chunk_id for r in rows))
        self.rows = rows

    def chunk_texts(self):
        for r in self.rows:
            yield {"id": r.chunk_id, "section": r.section, "content": r.content, "cancers": list(r.cancers)}


def test_build_from_store_saves_an_index_that_matches(tmp_path):
    store = PagedStore(ROWS)
    messages = []
    idx = bm25.build_from_store(store, tmp_path / "bm25", progress=messages.append)
    assert len(idx) == 7
    loaded = Bm25Index.load(tmp_path / "bm25")
    assert loaded.matches(bm25.store_fingerprint(store))
    assert ids(loaded.search("Breslow")) == [1]
    assert messages


# --- scripts/build_bm25_index.py -------------------------------------------------------------------------------


def _script():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts" / "build_bm25_index.py"
    spec = importlib.util.spec_from_file_location("build_bm25_index", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _postgrest(rows=ROWS):
    import httpx

    data = [{"id": r.chunk_id, "section": r.section, "content": r.content, "cancers": list(r.cancers)} for r in rows]
    reads = {"pages": 0}

    def handler(request):
        p = request.url.params
        path = request.url.path.removeprefix("/rest/v1")
        assert request.method == "GET"  # read-only
        if path == "/corpus_meta":
            return httpx.Response(200, json=[{"key": k, "value": v} for k, v in META.items()])
        if p.get("order") == "id.desc":
            return httpx.Response(200, json=[{"id": data[-1]["id"]}], headers={"content-range": f"0-0/{len(data)}"})
        reads["pages"] += 1
        after = int(p["id"].removeprefix("gt.")) if "id" in p else 0
        return httpx.Response(200, json=[r for r in data if r["id"] > after][:3])

    return httpx.MockTransport(handler), reads


def test_build_script(tmp_path, capsys):
    env = tmp_path / ".env"
    env.write_text("SUPABASE_URL=https://x.supabase.co\nSUPABASE_SECRET_KEY=sb_secret_do-not-leak\n")
    transport, reads = _postgrest()
    out = tmp_path / "bm25"
    script = _script()
    assert script.main(["--env", str(env), "--out", str(out)], supabase_transport=transport) == 0
    printed = capsys.readouterr().out
    assert "7 chunks" in printed and "MB" in printed and "do-not-leak" not in printed
    assert ids(Bm25Index.load(out).search("Breslow")) == [1]
    pages = reads["pages"]
    assert script.main(["--env", str(env), "--out", str(out), "--if-stale"], supabase_transport=transport) == 0
    assert "Up to date" in capsys.readouterr().out and reads["pages"] == pages


def test_build_script_needs_keys(tmp_path, capsys):
    env = tmp_path / ".env"
    env.write_text("SUPABASE_URL=https://x.supabase.co\n")
    assert _script().main(["--env", str(env), "--out", str(tmp_path / "b")]) == 2
    assert "SUPABASE_SECRET_KEY" in capsys.readouterr().err
