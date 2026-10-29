"""Supplement safety uses isolated synthetic text and deterministic fake models."""
import hashlib
import json
from pathlib import Path
import re
import sqlite3

import pytest

np = pytest.importorskip("numpy")


class ParagraphTokenizer:
    def __call__(self, text, **kwargs):
        offsets = [(match.start(), match.end()) for match in re.finditer(r"\S+", text)]
        return {"offset_mapping": offsets, "input_ids": list(range(len(offsets) + (2 if kwargs.get("add_special_tokens") else 0)))}


class ParagraphEmbedder:
    tokenizer = ParagraphTokenizer()

    def __init__(self, fail_after=None):
        self.calls = 0
        self.fail_after = fail_after

    def encode(self, texts, **kwargs):
        if self.fail_after is not None and self.calls >= self.fail_after:
            raise RuntimeError("synthetic-paragraph-interruption")
        self.calls += 1
        vectors = np.zeros((len(texts), 384), dtype=np.float32)
        for index, text in enumerate(texts):
            values = np.frombuffer(hashlib.sha256(text.encode()).digest(), dtype=np.uint8).astype(np.float32) + 1
            vectors[index, :32] = values / np.linalg.norm(values)
        return vectors


@pytest.fixture
def paragraph_index(book, monkeypatch, tmp_path):
    from backend import semantic, semantic_paragraphs

    manifest = tmp_path / "synthetic-model-manifest.json"
    payload = {"fingerprint": "synthetic-paragraph-model", "models": []}
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    state = {"embedder": ParagraphEmbedder()}
    monkeypatch.setattr(semantic, "model_manifest_path", lambda: manifest)
    monkeypatch.setattr(semantic, "optional_dependencies_available", lambda: True)
    monkeypatch.setattr(semantic, "runtime_backend", lambda: "torch-fp32-v1")
    monkeypatch.setattr(semantic, "verify_model_files", lambda: payload)
    monkeypatch.setattr(semantic, "load_models", lambda with_reranker=True: (state["embedder"], None))
    monkeypatch.delenv("SIYAR_SEMANTIC_DISABLED", raising=False)
    directory = tmp_path / "synthetic-index"
    semantic.prepare(book["database"], directory, download=False, batch_size=2)
    state["embedder"] = ParagraphEmbedder()
    return semantic, semantic_paragraphs, directory, state


def _published(directory):
    pointer = json.loads((directory / "paragraphs-active.json").read_text(encoding="utf-8"))
    return directory / pointer["directory"]


def _assert_vectors(work):
    with sqlite3.connect(work / "windows.sqlite3") as db:
        rows = db.execute("SELECT id,clean_text FROM windows ORDER BY id").fetchall()
    vectors = np.load(work / "vectors.npy", allow_pickle=False)
    expected = ParagraphEmbedder().encode(["passage: " + row[1] for row in rows])
    assert np.allclose(vectors, expected)
    assert vectors.shape == (len(rows), 384)
    assert np.isfinite(vectors).all()


def test_morphology_sidecar_preserves_bounds_scope_and_detects_corruption(book, paragraph_index):
    pytest.importorskip("nltk")
    from backend import morphology
    semantic, paragraphs, directory, _ = paragraph_index
    paragraphs.prepare_paragraphs(book["database"], directory, batch_size=2)
    status = semantic.semantic_status(book["database"], directory)
    report = morphology.prepare(status)
    rows = morphology.candidates("الاخلاص", status)
    assert rows
    assert all(0 <= row["source_char_start"] < row["source_char_end"] <= len(book["text"]) for row in rows)
    assert morphology.candidates("الاخلاص", {**status, "pilot_biography_ids": []}) == []
    biography = rows[0]["biography_id"]
    scoped = morphology.candidates("الاخلاص", {**status, "pilot_biography_ids": [biography]})
    assert scoped and all(row["biography_id"] == biography for row in scoped)
    target = Path(status["paragraph_status"]["directory"]) / report["file"]
    with target.open("ab") as output:
        output.write(b"corrupted-synthetic-sidecar")
    with pytest.raises(RuntimeError, match="بصمة"):
        morphology.candidates("الاخلاص", status)


def test_all_nonempty_synthetic_segments_are_indexed_without_crossing_source_bounds(book, paragraph_index):
    from backend.reading import reading_plain_with_map

    semantic, paragraphs, directory, _ = paragraph_index
    before = hashlib.sha256(Path(book["database"]).read_bytes()).hexdigest()
    context_before = (directory / "active.json").read_bytes()
    report = paragraphs.prepare_paragraphs(book["database"], directory, batch_size=2)
    assert report["complete"]
    work = _published(directory)
    with sqlite3.connect(work / "windows.sqlite3") as index, sqlite3.connect(book["database"]) as canonical:
        index.row_factory = canonical.row_factory = sqlite3.Row
        segments = canonical.execute("SELECT * FROM segments").fetchall()
        windows = index.execute("SELECT * FROM windows").fetchall()
        assert len(segments) == report["canonical_segment_count"]
        for window in windows:
            matching = [segment for segment in segments if segment["source_id"] == window["source_id"]
                        and segment["section_id"] == window["section_id"]
                        and segment["source_char_start"] <= window["source_char_start"] < window["source_char_end"] <= segment["source_char_end"]]
            assert matching, "A supplemental window must stay within one original canonical segment"
            assert 0 < window["token_count"] <= 320
            assert book["text"][window["source_char_start"]:window["source_char_end"]]
            assert matching[0]["biography_id"] == window["biography_id"]
        for segment in segments:
            clean, _ = reading_plain_with_map(segment["text_original"], segment["source_char_start"], starts_at_line_start=True)
            if clean.strip():
                assert any(segment["source_char_start"] <= window["source_char_start"] < window["source_char_end"] <= segment["source_char_end"]
                           and window["section_id"] == segment["section_id"] for window in windows)
        assert canonical.execute("SELECT COUNT(*) FROM review_log").fetchone()[0] == 0
        assert canonical.execute("SELECT full_text FROM sources").fetchone()[0] == book["text"]
    assert hashlib.sha256(Path(book["database"]).read_bytes()).hexdigest() == before
    assert (directory / "active.json").read_bytes() == context_before
    _assert_vectors(work)
    status = semantic.semantic_status(book["database"], directory)
    assert status["semantic_granularity"] == "context_and_paragraph"
    assert status["dense_allocation"] == {"context": 50, "paragraph": 50, "maximum_total": 100}
    assert status["base_index_fingerprint"] != status["fingerprint"]


@pytest.mark.parametrize("remove_vectors", [False, True])
def test_interrupted_supplement_resumes_without_displacing_ready_context(book, paragraph_index, remove_vectors):
    semantic, paragraphs, directory, state = paragraph_index
    state["embedder"] = ParagraphEmbedder(fail_after=1)
    with pytest.raises(RuntimeError, match="synthetic-paragraph-interruption"):
        paragraphs.prepare_paragraphs(book["database"], directory, batch_size=1)
    assert not (directory / "paragraphs-active.json").exists()
    assert not (directory / "paragraphs-prepare.lock").exists()
    assert semantic.semantic_status(book["database"], directory)["ready"]
    assert semantic.semantic_status(book["database"], directory)["semantic_granularity"] == "context_only"
    work = next(directory.glob("paragraphs-*/"))
    checkpoint = json.loads((work / "checkpoint.json").read_text(encoding="utf-8"))
    assert checkpoint["encoded"] == 1
    if remove_vectors:
        (work / "vectors.npy").unlink()
    state["embedder"] = ParagraphEmbedder()
    report = paragraphs.prepare_paragraphs(book["database"], directory, batch_size=1)
    assert state["embedder"].calls == report["window_count"] - (0 if remove_vectors else 1)
    _assert_vectors(_published(directory))


@pytest.mark.parametrize("invalid", ["negative", "beyond_count", "order", "model"])
def test_invalid_supplement_checkpoint_never_publishes(book, paragraph_index, invalid):
    _, paragraphs, directory, state = paragraph_index
    state["embedder"] = ParagraphEmbedder(fail_after=1)
    with pytest.raises(RuntimeError, match="synthetic-paragraph-interruption"):
        paragraphs.prepare_paragraphs(book["database"], directory, batch_size=1)
    work = next(directory.glob("paragraphs-*/"))
    path = work / "checkpoint.json"
    checkpoint = json.loads(path.read_text(encoding="utf-8"))
    if invalid == "negative":
        checkpoint["encoded"] = -1
    elif invalid == "beyond_count":
        checkpoint["encoded"] = 10**9
    else:
        checkpoint["order_fingerprint" if invalid == "order" else "model_fingerprint"] = "synthetic-wrong-version"
    path.write_text(json.dumps(checkpoint), encoding="utf-8")
    state["embedder"] = ParagraphEmbedder()
    with pytest.raises(RuntimeError, match="استئناف"):
        paragraphs.prepare_paragraphs(book["database"], directory, batch_size=1)
    assert not (directory / "paragraphs-active.json").exists()


def test_segment_review_invalidates_supplement_but_preserves_context(book, paragraph_index):
    semantic, paragraphs, directory, _ = paragraph_index
    paragraphs.prepare_paragraphs(book["database"], directory, batch_size=2)
    with sqlite3.connect(book["database"]) as db:
        db.execute("INSERT INTO review_log(entity_type,entity_id,change_summary,reviewer) VALUES (?,?,?,?)", ("segment", 1, "synthetic boundary review", "synthetic test"))
    status = semantic.semantic_status(book["database"], directory)
    assert status["ready"] and status["semantic_granularity"] == "context_only"
    assert status["paragraph_status"]["status"] == "stale"


@pytest.mark.parametrize("artifact", ["vectors.npy", "windows.sqlite3"])
def test_supplement_corruption_is_detected_before_retrieval(book, paragraph_index, artifact):
    from backend import smart_search

    _, paragraphs, directory, _ = paragraph_index
    paragraphs.prepare_paragraphs(book["database"], directory, batch_size=2)
    status = paragraphs.paragraph_status(book["database"], directory)
    smart_search._PARAGRAPH_VECTOR_CACHE.clear()
    smart_search._paragraph_vectors(status)
    path = _published(directory) / artifact
    if artifact.endswith("sqlite3"):
        with sqlite3.connect(path) as db:
            db.execute("UPDATE windows SET clean_text=? WHERE id=1", ("synthetic-tampering",))
    else:
        with path.open("r+b") as file:
            file.seek(-1, 2)
            byte = file.read(1)
            file.seek(-1, 2)
            file.write(bytes([byte[0] ^ 1]))
    with pytest.raises(RuntimeError, match="سلامة"):
        smart_search._paragraph_vectors(status)
    smart_search._PARAGRAPH_VECTOR_CACHE.clear()


def test_supplement_unchanged_prepare_and_path_containment(book, paragraph_index):
    _, paragraphs, directory, _ = paragraph_index
    paragraphs.prepare_paragraphs(book["database"], directory, batch_size=2)
    before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in _published(directory).iterdir() if path.is_file()}
    assert paragraphs.prepare_paragraphs(book["database"], directory, batch_size=2)["unchanged"]
    after = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in _published(directory).iterdir() if path.is_file()}
    assert before == after
    pointer = directory / "paragraphs-active.json"
    active = json.loads(pointer.read_text(encoding="utf-8"))
    active["directory"] = "../outside-index"
    pointer.write_text(json.dumps(active), encoding="utf-8")
    assert paragraphs.paragraph_status(book["database"], directory)["status"] == "stale"


def test_existing_supplement_lock_is_preserved(book, paragraph_index):
    _, paragraphs, directory, _ = paragraph_index
    lock = directory / "paragraphs-prepare.lock"
    lock.write_text("synthetic-another-builder", encoding="utf-8")
    with pytest.raises(RuntimeError, match="تجهيز"):
        paragraphs.prepare_paragraphs(book["database"], directory)
    assert lock.read_text(encoding="utf-8") == "synthetic-another-builder"


def test_actual_paragraph_audit_detects_missing_and_cross_segment_ranges(book, paragraph_index):
    from scripts.audit_window_coverage import audit_paragraphs

    _, paragraphs, directory, _ = paragraph_index
    paragraphs.prepare_paragraphs(book["database"], directory, batch_size=2)
    work = _published(directory)
    report = audit_paragraphs(book["database"], work)
    assert report["complete"] and report["nonempty_segments"] == report["segments_with_windows"]
    with sqlite3.connect(work / "windows.sqlite3") as db:
        db.execute("UPDATE windows SET source_char_end=source_char_end+10000 WHERE id=1")
    crossed = audit_paragraphs(book["database"], work)
    assert not crossed["complete"] and crossed["invalid_windows"]
    with sqlite3.connect(work / "windows.sqlite3") as db:
        db.execute("DELETE FROM windows")
    missing = audit_paragraphs(book["database"], work)
    assert not missing["complete"] and missing["uncovered"]


def test_oversized_single_paragraph_preserves_all_tokens_and_literal_bounds(book, paragraph_index):
    from backend.importer import import_book

    _, paragraphs, directory, _ = paragraph_index
    # A separate synthetic import exercises splitting independently of the
    # existing short fixture and without loading any actual model.
    words = [f"مصطنعة{index}" for index in range(701)]
    original = "######OpenITI#\r\n#META#Header#End#\r\n### $ 1 - عينة صناعية\r\n# " + " ".join(words) + " PageV01P001\r\n"
    source = directory.parent / "synthetic-long.mARkdown"
    source.write_bytes(original.encode("utf-8"))
    database = directory.parent / "synthetic-long.sqlite3"
    import_book(source, database)
    from backend import semantic
    long_directory = directory.parent / "synthetic-long-index"
    semantic.prepare(database, long_directory, download=False, batch_size=2)
    report = paragraphs.prepare_paragraphs(database, long_directory, batch_size=2)
    with sqlite3.connect(_published(long_directory) / "windows.sqlite3") as db:
        db.row_factory = sqlite3.Row
        windows = db.execute("SELECT * FROM windows ORDER BY id").fetchall()
    assert report["window_count"] >= 3
    assert {word for window in windows for word in window["clean_text"].split()} >= set(words)
    for window in windows:
        assert window["token_count"] <= 320
        assert window["clean_text"] in original[window["source_char_start"]:window["source_char_end"]]
    for previous, current in zip(windows, windows[1:]):
        assert len(set(previous["clean_text"].split()) & set(current["clean_text"].split())) <= 64


def test_empty_source_publishes_valid_zero_row_supplement(paragraph_index):
    from backend.importer import import_book

    semantic, paragraphs, directory, _ = paragraph_index
    source = directory.parent / "synthetic-empty-source.mARkdown"
    source.write_text("######OpenITI#\n#META#Header#End#\n", encoding="utf-8")
    database = directory.parent / "synthetic-empty-source.sqlite3"
    import_book(source, database)
    empty_directory = directory.parent / "synthetic-empty-source-index"
    semantic.prepare(database, empty_directory, download=False, batch_size=2)
    report = paragraphs.prepare_paragraphs(database, empty_directory, batch_size=2)
    assert report["window_count"] == 0
    assert report["canonical_segment_count"] == 0
    _assert_vectors(_published(empty_directory))
