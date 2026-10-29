"""Synthetic geometry and storage checks; real retrieval has a separate gate."""
import hashlib
import json
import re
import sqlite3

import pytest
np = pytest.importorskip("numpy")


def test_arabic_profile_indexes_keep_literal_geometry_with_byte_vectors(book, tmp_path, monkeypatch):
    from backend import semantic as base, semantic_paragraphs as paragraphs, smart_search as search
    from backend.retrieval_profiles import PROFILES
    profile = PROFILES["arabic-retrieval-v1"]
    for name, value in {"PROFILE_ID": "arabic-retrieval-v1", "PROFILE": profile, "DIMENSION": 768,
        "VECTOR_DTYPE": "int8", "QUERY_PREFIX": "", "PASSAGE_PREFIX": "",
        "EMBED_MODEL": profile["embed"], "EMBED_REVISION": profile["embed_revision"],
        "RERANK_MODEL": profile["rerank"], "RERANK_REVISION": profile["rerank_revision"]}.items():
        monkeypatch.setattr(base, name, value)
    monkeypatch.setattr(search, "DIMENSION", 768)
    monkeypatch.setattr(search, "VECTOR_DTYPE", "int8")

    class Tokenizer:
        def __call__(self, text, **kwargs):
            offsets = [(m.start(), m.end()) for m in re.finditer(r"\S+", text)]
            return {"offset_mapping": offsets, "input_ids": list(range(len(offsets) + (2 if kwargs.get("add_special_tokens") else 0)))}
    class Embedder:
        tokenizer = Tokenizer()
        def encode(self, texts, **kwargs):
            values = np.zeros((len(texts), 768), dtype=np.float32)
            for i, text in enumerate(texts):
                seed = int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big")
                values[i] = np.random.default_rng(seed).normal(size=768)
            return values / np.linalg.norm(values, axis=1, keepdims=True)
    manifest = {"models": [], "fingerprint": "synthetic-arabic-profile"}
    path = tmp_path / "model.json"; path.write_text(json.dumps(manifest))
    monkeypatch.setattr(base, "model_manifest_path", lambda: path)
    monkeypatch.setattr(base, "optional_dependencies_available", lambda: True)
    monkeypatch.setattr(base, "runtime_backend", lambda: "torch-fp32-v1")
    monkeypatch.setattr(base, "verify_model_files", lambda: manifest)
    monkeypatch.setattr(base, "load_models", lambda with_reranker=True: (Embedder(), None))
    monkeypatch.setattr(base, "window_tokenizer", lambda ignored: Tokenizer())
    directory = tmp_path / "index"
    base.prepare(book["database"], directory, download=False, batch_size=2)
    paragraphs.prepare_paragraphs(book["database"], directory, batch_size=2)
    status = base.semantic_status(book["database"], directory)
    assert status["ready"] and status["paragraph_status"]["ready"]
    for state, loader in [(status, search._vectors), (status["paragraph_status"], search._paragraph_vectors)]:
        vectors = loader(state)
        assert vectors.dtype == np.int8 and vectors.shape[1] == 768
        with sqlite3.connect(state["directory"] + "/windows.sqlite3") as db:
            for start, end, text in db.execute("SELECT source_char_start,source_char_end,clean_text FROM windows"):
                assert 0 <= start < end <= len(book["text"])
                assert text and book["text"][start:end]
    monkeypatch.setattr(base, "PROFILE_ID", "legacy-e5")
    assert not base.semantic_status(book["database"], directory)["ready"]
