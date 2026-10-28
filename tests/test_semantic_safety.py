"""Failure/resume/integrity regressions use isolated fake models and sidecars."""
import json
import re
import sqlite3
import sys
import hashlib
from types import SimpleNamespace

import pytest

np = pytest.importorskip("numpy")


class SafetyTokenizer:
    def __call__(self,text,pair=None,**kwargs):
        if pair is not None:
            text += " " + pair
        offsets=[(m.start(),m.end()) for m in re.finditer(r"\S+",text)]
        return {"offset_mapping":offsets,"input_ids":list(range(len(offsets)+(2 if kwargs.get("add_special_tokens") else 0)))}


class SafetyEmbedder:
    tokenizer=SafetyTokenizer()
    def __init__(self,axis=0,fail_after=None):
        self.axis=axis
        self.fail_after=fail_after
        self.calls=0
    def encode(self,texts,**kwargs):
        if self.fail_after is not None and self.calls>=self.fail_after:
            raise RuntimeError("synthetic-safety-interruption")
        self.calls+=1
        result=np.zeros((len(texts),384),dtype=np.float32)
        result[:,self.axis]=1.0
        return result


@pytest.fixture
def safety_models(monkeypatch,tmp_path):
    from backend import semantic
    manifest=tmp_path/"model-manifest.json"
    state={"fingerprint":"fake-model-A","embedder":SafetyEmbedder()}
    def refresh():
        payload={"fingerprint":state["fingerprint"],"models":[]}
        manifest.write_text(json.dumps(payload),encoding="utf-8")
        return payload
    refresh()
    monkeypatch.setattr(semantic,"model_manifest_path",lambda:manifest)
    monkeypatch.setattr(semantic,"runtime_backend",lambda:"torch-fp32-v1")
    monkeypatch.setattr(semantic,"verify_model_files",refresh)
    monkeypatch.setattr(semantic,"load_models",lambda with_reranker=True:(state["embedder"],None))
    monkeypatch.setattr(semantic,"optional_dependencies_available",lambda:True)
    monkeypatch.delenv("SIYAR_SEMANTIC_DISABLED",raising=False)
    return semantic,state,refresh


def _interrupt(book,directory,models):
    semantic,state,_=models
    state["embedder"]=SafetyEmbedder(fail_after=1)
    with pytest.raises(RuntimeError,match="synthetic-safety-interruption"):
        semantic.prepare(book["database"],directory,download=False,batch_size=1)
    assert not (directory/"active.json").exists()
    work=next(directory.glob("version-*"))
    checkpoint=json.loads((work/"checkpoint.json").read_text(encoding="utf-8"))
    assert checkpoint["encoded"]==1
    state["embedder"]=SafetyEmbedder()
    return work


def _assert_published_vectors_complete(directory,report,axis=0):
    pointer=json.loads((directory/"active.json").read_text(encoding="utf-8"))
    vectors=np.load(directory/pointer["directory"]/"vectors.npy",allow_pickle=False)
    assert vectors.shape==(report["window_count"],384)
    assert np.isfinite(vectors).all()
    assert np.allclose(vectors[:,axis],1.0), "No encoded prefix may be skipped or mixed with an older model"
    assert np.allclose(np.linalg.norm(vectors,axis=1),1.0)


def test_missing_vector_file_never_skips_previously_checkpointed_rows(book,safety_models,tmp_path):
    semantic,state,_=safety_models
    directory=tmp_path/"missing-vectors"
    work=_interrupt(book,directory,safety_models)
    (work/"vectors.npy").unlink()
    report=semantic.prepare(book["database"],directory,download=False,batch_size=1)
    _assert_published_vectors_complete(directory,report)
    assert state["embedder"].calls==report["window_count"]


@pytest.mark.parametrize("invalid",["beyond-count","negative","wrong-fingerprint"])
def test_invalid_checkpoint_cannot_publish_unencoded_rows(book,safety_models,tmp_path,invalid):
    semantic,state,_=safety_models
    directory=tmp_path/invalid
    work=_interrupt(book,directory,safety_models)
    path=work/"checkpoint.json"
    checkpoint=json.loads(path.read_text(encoding="utf-8"))
    if invalid=="beyond-count":checkpoint["encoded"]=10**9
    elif invalid=="negative":checkpoint["encoded"]=-4
    else:checkpoint["fingerprint"]="another-source-configuration"
    path.write_text(json.dumps(checkpoint),encoding="utf-8")
    try:
        report=semantic.prepare(book["database"],directory,download=False,batch_size=1)
    except (RuntimeError,ValueError):
        assert not (directory/"active.json").exists()
    else:
        _assert_published_vectors_complete(directory,report)


def test_actual_model_fingerprint_change_never_reuses_old_model_vectors(book,safety_models,tmp_path):
    semantic,state,refresh=safety_models
    directory=tmp_path/"changed-model"
    first=semantic.prepare(book["database"],directory,download=False,batch_size=1)
    _assert_published_vectors_complete(directory,first,axis=0)
    state["fingerprint"]="fake-model-B"
    state["embedder"]=SafetyEmbedder(axis=1)
    refresh()
    try:
        second=semantic.prepare(book["database"],directory,download=False,batch_size=1)
    except RuntimeError:
        # Explicitly refusing reuse is safe; a fresh sidecar may be requested by
        # the operator after keeping the earlier model's version separately.
        assert semantic.semantic_status(book["database"],directory)["ready"] is False
        return
    assert second["model_fingerprint"]=="fake-model-B"
    _assert_published_vectors_complete(directory,second,axis=1)
    assert state["embedder"].calls==second["window_count"]


def test_sidecar_tampering_is_checked_after_a_vector_cache_was_populated(book,safety_models,tmp_path,monkeypatch):
    semantic,_,_=safety_models
    from backend import smart_search
    directory=tmp_path/"tamper-after-load"
    semantic.prepare(book["database"],directory,download=False,batch_size=1)
    status=semantic.semantic_status(book["database"],directory)
    monkeypatch.setattr(smart_search,"_VECTOR_CACHE",{})
    smart_search._vectors(status)
    sidecar=directory/json.loads((directory/"active.json").read_text(encoding="utf-8"))["directory"]/"windows.sqlite3"
    with sqlite3.connect(sidecar) as db:
        db.execute("UPDATE windows SET heading=? WHERE id=1",("tampered-index-heading",))
    with pytest.raises(RuntimeError):
        smart_search._vectors(status)


def test_semantic_active_pointer_cannot_escape_its_sidecar_directory(book,safety_models,tmp_path):
    semantic,_,_=safety_models
    directory=tmp_path/"pointer-home"
    semantic.prepare(book["database"],directory,download=False,batch_size=1)
    pointer=json.loads((directory/"active.json").read_text(encoding="utf-8"))
    outside=tmp_path/"outside-version"
    (directory/pointer["directory"]).rename(outside)
    pointer["directory"]="../outside-version"
    (directory/"active.json").write_text(json.dumps(pointer),encoding="utf-8")
    assert semantic.semantic_status(book["database"],directory)["ready"] is False


def test_empty_import_has_zero_windows_without_invalid_mmap_or_division(safety_models,tmp_path):
    from backend.importer import import_book
    semantic,_,_=safety_models
    source=tmp_path/"synthetic-empty.mARkdown"
    source.write_text("######OpenITI#\n#META#Header#End#\n",encoding="utf-8")
    database=tmp_path/"empty.sqlite3"
    import_book(source,database)
    directory=tmp_path/"empty-index"
    report=semantic.prepare(database,directory,download=False,batch_size=1)
    assert report["window_count"]==0
    _assert_published_vectors_complete(directory,report)


def test_model_cache_reloads_when_actual_model_fingerprint_changes(monkeypatch):
    from backend import smart_search
    calls=[]
    def loader():
        models=(object(),object())
        calls.append(models)
        return models
    monkeypatch.setattr(smart_search,"_MODEL_CACHE",None)
    monkeypatch.setattr(smart_search,"load_models",loader)
    first=smart_search._models("model-A")
    assert smart_search._models("model-A") is first
    second=smart_search._models("model-B")
    assert second is not first
    assert len(calls)==2


def test_smart_pipeline_checks_sidecar_before_lexical_read_and_falls_back_to_canonical(book,safety_models,tmp_path,monkeypatch):
    semantic,_,_=safety_models
    from backend import smart_search
    directory=tmp_path/"lexical-integrity"
    semantic.prepare(book["database"],directory,download=False,batch_size=1)
    status=semantic.semantic_status(book["database"],directory)
    monkeypatch.setattr(smart_search,"_VECTOR_CACHE",{})
    monkeypatch.setattr(smart_search,"_CACHE",{})
    smart_search._vectors(status)
    sidecar=directory/json.loads((directory/"active.json").read_text(encoding="utf-8"))["directory"]/"windows.sqlite3"
    with sqlite3.connect(sidecar) as db:
        db.execute("UPDATE windows SET heading=?",("tampered-lexical-title",))
    result=smart_search.smart_search({"q":"الإخلاص"},book["database"],directory)
    assert result["mode"]=="lexical_only"
    assert result["warning"]
    assert result["items"]
    assert all(item["heading"]!="tampered-lexical-title" for item in result["items"])
    assert all(book["text"][item["source_char_start"]:item["source_char_end"]]==item["evidence_text"] for item in result["items"])


def test_cached_response_revision_changes_when_sidecar_artifacts_change(book,safety_models,tmp_path):
    semantic,_,_=safety_models
    from backend import smart_search
    directory=tmp_path/"response-cache-integrity"
    semantic.prepare(book["database"],directory,download=False,batch_size=1)
    status=semantic.semantic_status(book["database"],directory)
    first=smart_search._cache_revision(book["database"],status)
    sidecar=directory/json.loads((directory/"active.json").read_text(encoding="utf-8"))["directory"]/"windows.sqlite3"
    with sqlite3.connect(sidecar) as db:
        db.execute("UPDATE windows SET heading=? WHERE id=1",("changed-local-index",))
    second=smart_search._cache_revision(book["database"],status)
    assert first!=second


def test_overlong_query_is_rejected_before_embedding_without_truncation(monkeypatch):
    from backend import smart_search
    embedder=SafetyEmbedder()
    monkeypatch.setattr(smart_search,"_models",lambda fingerprint=None:(embedder,None))
    with pytest.raises(ValueError,match="لم تُبتر"):
        smart_search._semantic_candidates(" ".join(["كلمة"]*600),{"model_fingerprint":"synthetic"})
    assert embedder.calls==0


def test_overlong_rerank_pair_keeps_literal_candidate_and_marks_skip(monkeypatch):
    from backend import smart_search
    reranker=SimpleNamespace(tokenizer=SafetyTokenizer(),predict=lambda pairs,**kwargs:[0.9]*len(pairs))
    monkeypatch.setattr(smart_search,"_models",lambda fingerprint=None:(None,reranker))
    original=" ".join(["نص"]*320)
    rows=[{"id":1,"clean_text":original,"scores":{"rrf":0.1}},{"id":2,"clean_text":"شاهد مصطنع","scores":{"rrf":0.2}}]
    result=smart_search._rerank(" ".join(["سؤال"]*250),rows,"synthetic")
    skipped=next(row for row in result if row["id"]==1)
    assert skipped["clean_text"]==original
    assert skipped["scores"]["rerank_skipped"]=="pair_exceeds_512_tokens"
    assert "rerank" not in skipped["scores"]
    assert next(row for row in result if row["id"]==2)["scores"]["rerank"]==0.9


def test_model_load_uses_only_local_cpu_files_and_never_remote_code(monkeypatch):
    from backend import semantic
    calls=[]
    def model(kind):
        def load(path,**kwargs):
            calls.append((kind,path,kwargs))
            return SimpleNamespace()
        return load
    monkeypatch.setitem(sys.modules,"sentence_transformers",SimpleNamespace(SentenceTransformer=model("embed"),CrossEncoder=model("rerank")))
    monkeypatch.setattr(semantic,"configure_cpu",lambda:None)
    monkeypatch.setattr(semantic,"verify_model_files",lambda:{"models":[]})
    monkeypatch.setattr(semantic,"runtime_backend",lambda:"torch-fp32-v1")
    monkeypatch.setattr(semantic.urllib.request,"urlopen",lambda *args,**kwargs:pytest.fail("Query-time model loading must never use the network"))
    embed,_=semantic.load_models()
    assert embed.max_seq_length==512
    assert len(calls)==2
    for kind,path,kwargs in calls:
        assert kwargs["device"]=="cpu"
        assert kwargs["local_files_only"] is True
        assert kwargs["trust_remote_code"] is False


class IdentityEmbedder(SafetyEmbedder):
    """A different deterministic unit vector for each clean source window."""
    def encode(self,texts,**kwargs):
        if self.fail_after is not None and self.calls>=self.fail_after:
            raise RuntimeError("synthetic-safety-interruption")
        self.calls+=1
        vectors=np.zeros((len(texts),384),dtype=np.float32)
        for index,text in enumerate(texts):
            digest=hashlib.sha256(text.encode()).digest()
            values=np.frombuffer(digest,dtype=np.uint8).astype(np.float32)+1
            vectors[index,:32]=values/np.linalg.norm(values)
        return vectors


@pytest.mark.parametrize("remove_vectors",[False,True])
def test_nonsequential_length_order_resume_keeps_each_vector_at_its_own_window_row(book,safety_models,tmp_path,remove_vectors):
    semantic,state,_=safety_models
    directory=tmp_path/("length-order-missing" if remove_vectors else "length-order-resume")
    state["embedder"]=IdentityEmbedder(fail_after=2)
    with pytest.raises(RuntimeError,match="synthetic-safety-interruption"):
        semantic.prepare(book["database"],directory,download=False,batch_size=1)
    work=next(directory.glob("version-*"))
    order=json.loads((work/"encoding-order.json").read_text(encoding="utf-8"))
    assert order!=list(range(len(order))), "Fixture must exercise a genuinely reordered encoding batch"
    assert len(order)==len(set(order))
    if remove_vectors:(work/"vectors.npy").unlink()
    state["embedder"]=IdentityEmbedder()
    report=semantic.prepare(book["database"],directory,download=False,batch_size=1)
    vectors=np.load(work/"vectors.npy",allow_pickle=False)
    with sqlite3.connect(work/"windows.sqlite3") as db:
        rows=db.execute("SELECT id,clean_text FROM windows ORDER BY id").fetchall()
    reference=IdentityEmbedder().encode(["passage: "+row[1] for row in rows])
    assert np.allclose(vectors,reference)
    assert state["embedder"].calls==report["window_count"]-(0 if remove_vectors else 2)
