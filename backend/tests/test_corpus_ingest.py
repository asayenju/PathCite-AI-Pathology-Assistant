"""Ingest CLI (issue #45): guards, resume, per-document failures, --rebuild, --limit, the PostgREST client and the
report, against httpx.MockTransport fakes of OpenAI and PostgREST. No network."""

import json
import logging
from urllib.parse import parse_qs

import httpx
import pytest

from app.corpus import cli
from app.corpus.config import ChunkerConfig
from app.corpus.local_embedder import HashingEmbedder
from app.corpus.plan import load_or_build_plan
from app.corpus.supabase import SupabaseAuthError, SupabaseCorpus, SupabaseUnreachable

from .test_corpus_documents import abstract, guideline, write_corpus
from .test_corpus_embed import ok as openai_ok

OPENAI_KEY = "sk-openai-test-key-do-not-leak"
SB_KEY = "sb_secret_test-key-do-not-leak"
SB_URL = "https://fake-project.supabase.co"
CFG = ChunkerConfig()
GOOD_META = {"embedding_model": "text-embedding-3-small", "dims": 1536, "chunker_config_hash": CFG.config_hash()}


class FakePostgREST:
    """In-memory documents/chunks/corpus_meta behind the routes SupabaseCorpus uses. `max_rows` mimics the server
    cap; `fail_docs` makes ingest_document return 400 for those ids."""

    def __init__(self, *, max_rows: int = 1000, fail_docs=(), meta=None):
        self.documents: dict[str, dict] = {}
        self.chunks: dict[str, list[dict]] = {}
        self.meta: dict = dict(meta or {})
        self.max_rows = max_rows
        self.fail_docs = set(fail_docs)
        self.ingested: list[str] = []
        self.requests: list[httpx.Request] = []

    def put(self, doc_id: str, hashes: list[str]) -> None:
        self.documents[doc_id] = {"id": doc_id}
        self.chunks[doc_id] = [{"doc_id": doc_id, "ord": i, "content_hash": h} for i, h in enumerate(hashes)]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.headers["apikey"] == SB_KEY and "authorization" not in request.headers
        path = request.url.path.removeprefix("/rest/v1")
        q = {k: v[0] for k, v in parse_qs(request.url.query.decode()).items()}
        m = request.method
        if m == "POST" and path == "/rpc/ingest_document":
            body = json.loads(request.content)
            doc, chunks = body["doc"], body["chunks"]
            if doc["id"] in self.fail_docs:
                return httpx.Response(400, json={"code": "22000", "message": "expected 1536 dimensions, not 3"})
            assert all(len(c["embedding"]) == 1536 for c in chunks)
            self.documents[doc["id"]] = doc
            self.chunks[doc["id"]] = [{"doc_id": doc["id"], **c} for c in chunks]
            self.ingested.append(doc["id"])
            return httpx.Response(200, json=len(chunks))
        if m == "POST" and path == "/rpc/corpus_stats":
            n = sum(len(v) for v in self.chunks.values())
            return httpx.Response(200, json={"rows": {"documents": len(self.documents), "chunks": n,
                                                      "corpus_meta": len(self.meta)},
                                             "total_bytes": 12345, "total_pretty": "12 kB"})  # fmt: skip
        if m == "GET" and path == "/corpus_meta":
            return httpx.Response(200, json=[{"key": k, "value": v} for k, v in self.meta.items()])
        if m == "POST" and path == "/corpus_meta":
            assert "merge-duplicates" in request.headers["prefer"]
            self.meta.update({r["key"]: r["value"] for r in json.loads(request.content)})
            return httpx.Response(201)
        if m == "GET" and path in ("/chunks", "/documents"):
            if path == "/chunks":
                rows = [r for d in sorted(self.chunks) for r in sorted(self.chunks[d], key=lambda r: r["ord"])]
                assert q["order"] == "doc_id.asc,ord.asc"
            else:
                rows = [{"id": d} for d in sorted(self.documents)]
            offset, limit = int(q.get("offset", 0)), min(int(q.get("limit", 10**9)), self.max_rows)
            cols = q["select"].split(",")
            return httpx.Response(200, json=[{c: r[c] for c in cols} for r in rows[offset : offset + limit]])
        if m == "DELETE":
            assert q, "PostgREST refuses an unfiltered delete"
            if path == "/documents":
                if q["id"].startswith("in."):
                    ids = [s.strip('"') for s in q["id"][4:-1].split(",")]
                else:
                    ids = list(self.documents)
                for d in ids:
                    self.documents.pop(d, None)
                    self.chunks.pop(d, None)
            elif path == "/chunks":
                self.chunks.clear()
            elif path == "/corpus_meta":
                self.meta.clear()
            return httpx.Response(204)
        return httpx.Response(404, json={"code": "PGRST", "message": f"no route {m} {path}"})


class FakeOpenAI:
    def __init__(self):
        self.inputs: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {OPENAI_KEY}"
        body = json.loads(request.content)
        self.inputs.extend(body["input"])
        return openai_ok(request, body)


DOCS = [
    guideline("nice:a", text="# Staging\nOffer sentinel lymph node biopsy to people with stage IB melanoma after "
                             "a discussion of the benefits and risks.\n# Follow-up\nOffer follow-up every 3 months "
                             "for the first 3 years to people who had stage IIC melanoma."),  # fmt: skip
    guideline("wikidoc:b", source="wikidoc", text="Colorectal cancer screening with colonoscopy every 10 years is "
                                                    "recommended for average-risk adults from age 45."),  # fmt: skip
    abstract("123", text="BACKGROUND: Breast cancer screening with mammography reduces mortality in women aged 50 "
                         "to 69. CONCLUSIONS: Biennial screening is recommended in this group."),  # fmt: skip
]


@pytest.fixture
def corpus(tmp_path):
    """A corpus with a cached plan (breakpoints from the offline embedder under the OpenAI id, so no API calls)."""
    d = write_corpus(tmp_path / "corpus", DOCS[:2], DOCS[2:])
    plan, _ = load_or_build_plan(d, HashingEmbedder(), embedder_id=cli.EMBEDDER_ID, config=CFG, rechunk=False)
    env = tmp_path / ".env"
    env.write_text(f"SUPABASE_URL={SB_URL}\nSUPABASE_SECRET_KEY={SB_KEY}\nOPEN_AI_VECTOR_KEY={OPENAI_KEY}\n")
    return d, env, plan


def hashes(plan, doc_id):
    return [c.content_hash for c in sorted(plan.chunks, key=lambda c: c.ord) if c.doc_id == doc_id]


def run(corpus, sb, oa, *extra):
    d, env, _ = corpus
    return cli.main(["--corpus-dir", str(d), "--env", str(env), *extra],
                    openai_transport=httpx.MockTransport(oa), supabase_transport=httpx.MockTransport(sb),
                    sleep=lambda s: None)  # fmt: skip


def test_first_run_ingests_every_document_and_writes_the_locks(corpus, capsys):
    _, _, plan = corpus
    sb, oa = FakePostgREST(), FakeOpenAI()
    assert run(corpus, sb, oa) == 0
    assert sb.meta == GOOD_META
    assert set(sb.ingested) == {"nice:a", "wikidoc:b", "pubmed:123"}
    assert len(oa.inputs) == len(plan.chunks)
    assert sorted(oa.inputs) == sorted(c.embed_text for c in plan.chunks)
    for doc_id in sb.ingested:
        assert [c["content_hash"] for c in sb.chunks[doc_id]] == hashes(plan, doc_id)
    doc = sb.documents["pubmed:123"]
    assert doc["url"] == "https://pubmed.ncbi.nlm.nih.gov/123/" and doc["trust"] == "medium" and doc["year"] == 2023
    assert set(doc) == {"id", "source", "trust", "title", "url", "pmid", "journal", "year", "pub_types", "cancers"}
    out = capsys.readouterr().out
    assert "3 planned: 0 skipped (unchanged), 3 written, 0 failed" in out
    assert "melanoma" in out and "breast" in out and "wikidoc (low)" in out and "pubmed (medium)" in out
    assert "corpus_stats()     documents 3" in out and "12 kB" in out
    assert "actual cost $0.0000" in out


def test_second_run_embeds_nothing(corpus, capsys):
    sb, oa = FakePostgREST(), FakeOpenAI()
    assert run(corpus, sb, oa) == 0
    capsys.readouterr()
    oa2 = FakeOpenAI()
    assert run(corpus, sb, oa2) == 0
    assert oa2.inputs == []
    out = capsys.readouterr().out
    assert "3 skipped (unchanged), 0 written" in out and "Chunks written     0" in out
    assert "Tokens embedded    0 this run" in out


def test_resume_skips_unchanged_documents_and_rewrites_changed_ones(corpus, capsys):
    _, _, plan = corpus
    sb = FakePostgREST(meta=GOOD_META)
    sb.put("nice:a", hashes(plan, "nice:a"))  # unchanged
    sb.put("wikidoc:b", ["stale-hash"])  # changed
    oa = FakeOpenAI()
    assert run(corpus, sb, oa) == 0
    assert sorted(sb.ingested) == ["pubmed:123", "wikidoc:b"]
    assert len(oa.inputs) == len(plan.chunks) - len(hashes(plan, "nice:a"))
    assert "1 skipped (unchanged), 2 written" in capsys.readouterr().out


def test_a_failing_document_is_reported_and_the_others_continue(corpus, capsys):
    sb, oa = FakePostgREST(fail_docs={"nice:a"}), FakeOpenAI()
    assert run(corpus, sb, oa) == 1
    assert sorted(sb.ingested) == ["pubmed:123", "wikidoc:b"]
    out = capsys.readouterr().out
    assert "2 written, 1 failed" in out
    assert "FAILED             nice:a: POST /rpc/ingest_document failed: HTTP 400 22000" in out
    # Next run retries only the failed document.
    sb.fail_docs.clear()
    oa2 = FakeOpenAI()
    assert run(corpus, sb, oa2) == 0
    assert sb.ingested[-1] == "nice:a" and len(sb.ingested) == 3


@pytest.mark.parametrize("key", ["embedding_model", "dims", "chunker_config_hash"])
def test_guard_refuses_a_model_or_config_mismatch(corpus, capsys, key):
    sb = FakePostgREST(meta={**GOOD_META, key: "something-else"})
    sb.put("nice:a", ["old"])
    oa = FakeOpenAI()
    assert run(corpus, sb, oa) == 2
    assert oa.inputs == [] and sb.ingested == []
    err = capsys.readouterr().err
    assert "Refused" in err and key in err and "--rebuild" in err


def test_guard_refuses_documents_without_locks(corpus, capsys):
    sb = FakePostgREST()
    sb.put("nice:a", ["old"])
    assert run(corpus, sb, FakeOpenAI()) == 2
    assert "no corpus_meta locks" in capsys.readouterr().err


def test_rebuild_clears_everything_then_ingests(corpus, capsys):
    sb = FakePostgREST(meta={**GOOD_META, "embedding_model": "text-embedding-ada-002"})
    sb.put("old:gone", ["x", "y"])
    sb.put("nice:a", ["old"])
    oa = FakeOpenAI()
    assert run(corpus, sb, oa, "--rebuild") == 0
    assert sb.meta == GOOD_META
    assert sorted(sb.documents) == ["nice:a", "pubmed:123", "wikidoc:b"]
    assert "old:gone" not in sb.chunks
    assert "--rebuild: deleted" in capsys.readouterr().out


def test_stale_documents_are_reported_not_deleted(corpus, capsys):
    sb = FakePostgREST(meta=GOOD_META)
    sb.put("old:gone", ["x"])
    assert run(corpus, sb, FakeOpenAI()) == 0
    assert "old:gone" in sb.documents
    assert "Not in the plan    1 documents in the database (kept; --rebuild removes them): old:gone" in (
        capsys.readouterr().out
    )


def test_limit_ingests_only_the_first_n_documents(corpus, capsys):
    sb = FakePostgREST()
    assert run(corpus, sb, FakeOpenAI(), "--limit", "1") == 0
    assert len(sb.ingested) == 1
    assert "1 planned" in capsys.readouterr().out


def test_documents_are_embedded_in_groups_but_written_one_by_one(corpus):
    from app.corpus.embed import OpenAIEmbedder
    from app.corpus.ingest import ingest

    _, _, plan = corpus
    sb, oa = FakePostgREST(), FakeOpenAI()
    calls = []
    with OpenAIEmbedder(OPENAI_KEY, transport=httpx.MockTransport(oa)) as emb:
        real = emb.embed
        emb.embed = lambda texts: calls.append(len(texts)) or real(texts)
        with SupabaseCorpus(SB_URL, SB_KEY, transport=httpx.MockTransport(sb)) as store:
            result = ingest(plan, emb, store, group_chunks=1)
    assert len(calls) == 3 and sum(calls) == len(plan.chunks)  # one group per document at this size
    assert result.written == 3 and len(sb.ingested) == 3


def test_missing_keys_are_refused(tmp_path, capsys):
    env = tmp_path / ".env"
    env.write_text(f"SUPABASE_URL={SB_URL}\n")
    assert cli.main(["--corpus-dir", str(tmp_path), "--env", str(env)]) == 2
    err = capsys.readouterr().err
    assert "SUPABASE_SECRET_KEY" in err and "OPEN_AI_VECTOR_KEY" in err


def test_unreachable_project_gives_a_clear_message(corpus, capsys):
    def down(request):
        raise httpx.ConnectError("connection refused")

    assert run(corpus, down, FakeOpenAI()) == 2
    err = capsys.readouterr().err
    assert "Supabase project unreachable or paused — open the dashboard to resume it" in err


@pytest.mark.parametrize("response", [
    httpx.Response(540, text="project paused"),
    httpx.Response(503, headers={"content-type": "text/html"}, text="<html>paused</html>"),
    httpx.Response(200, headers={"content-type": "text/html"}, text="<html>resume your project</html>"),
])  # fmt: skip
def test_paused_project_responses_are_unreachable(response):
    sleeps = []
    store = SupabaseCorpus(SB_URL, SB_KEY, transport=httpx.MockTransport(lambda r: response), sleep=sleeps.append)
    with pytest.raises(SupabaseUnreachable, match="open the dashboard to resume it"):
        store.corpus_stats()


def test_retries_with_backoff_then_succeeds():
    sleeps, responses = [], [httpx.Response(503, json={}), httpx.ReadTimeout("slow"), httpx.Response(200, json={})]

    def handler(request):
        r = responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    store = SupabaseCorpus(SB_URL, SB_KEY, transport=httpx.MockTransport(handler), sleep=sleeps.append)
    assert store.corpus_stats() == {}
    assert sleeps == [1.0, 2.0]


def test_401_is_a_key_message_without_the_key():
    store = SupabaseCorpus(SB_URL, SB_KEY, transport=httpx.MockTransport(
        lambda r: httpx.Response(401, json={"message": "Invalid API key"})))  # fmt: skip
    with pytest.raises(SupabaseAuthError) as e:
        store.read_meta()
    assert "SUPABASE_SECRET_KEY" in str(e.value) and SB_KEY not in str(e.value)


def test_existing_hashes_paginates_past_the_server_row_cap():
    sb = FakePostgREST(max_rows=2)
    sb.put("a", ["a0", "a1", "a2"])
    sb.put("b", ["b0", "b1"])
    sb.put("c", ["c0"])
    store = SupabaseCorpus(SB_URL, SB_KEY, transport=httpx.MockTransport(sb))
    assert store.existing_hashes() == {"a": ["a0", "a1", "a2"], "b": ["b0", "b1"], "c": ["c0"]}
    assert store.document_ids() == ["a", "b", "c"]
    offsets = [r.url.params["offset"] for r in sb.requests if r.url.path.endswith("/chunks")]
    assert offsets == ["0", "2", "4", "6"]


def test_delete_documents_quotes_ids_with_colons():
    sb = FakePostgREST()
    sb.put("pubmed:1", ["x"])
    sb.put("nice:2", ["y"])
    SupabaseCorpus(SB_URL, SB_KEY, transport=httpx.MockTransport(sb)).delete_documents(["pubmed:1"])
    assert list(sb.documents) == ["nice:2"]


def test_keys_never_appear_in_output_or_logs(corpus, capsys, caplog):
    caplog.set_level(logging.DEBUG)
    sb, oa = FakePostgREST(fail_docs={"wikidoc:b"}), FakeOpenAI()
    run(corpus, sb, oa)

    def down(request):
        raise httpx.ConnectError("connection refused")

    run(corpus, down, oa)
    run(corpus, lambda r: httpx.Response(401, json={"message": "bad key"}), oa)
    out = capsys.readouterr()
    for text in (out.out, out.err, caplog.text):
        assert SB_KEY not in text and OPENAI_KEY not in text
