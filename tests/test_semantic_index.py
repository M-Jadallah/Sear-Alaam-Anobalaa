"""Model-free index correctness tests; real-model quality is measured separately."""
import json
import re
import sqlite3
from pathlib import Path

import pytest

np = pytest.importorskip("numpy", reason="Index safety tests require the optional NumPy dependency")


class TinyTokenizer:
    def __call__(self, text, **kwargs):
        offsets = [(match.start(), match.end()) for match in re.finditer(r"\S+", text)]
        return {"offset_mapping": offsets, "input_ids": list(range(len(offsets) + (2 if kwargs.get("add_special_tokens") else 0)))}


class TinyEmbedder:
    tokenizer = TinyTokenizer()

    def __init__(self, fail_after=None):
        self.calls = []
        self.fail_after = fail_after

    def encode(self, texts, **kwargs):
        if self.fail_after is not None and len(self.calls) >= self.fail_after:
            raise RuntimeError("intentional interrupted synthetic embedding batch")
        self.calls.extend([list(texts)])
        vectors = np.zeros((len(texts), 384), dtype="float32")
        for index, text in enumerate(texts):
            vectors[index, len(text) % 384] = 1
        return vectors


@pytest.fixture
def fake_models(monkeypatch, tmp_path):
    from backend import semantic

    manifest = tmp_path / "fake-models.json"
    manifest.write_text(json.dumps({"fingerprint": "synthetic-model-fingerprint", "models": []}), encoding="utf-8")
    embedder = TinyEmbedder()
    monkeypatch.setattr(semantic, "model_manifest_path", lambda: manifest)
    monkeypatch.setattr(semantic, "optional_dependencies_available", lambda: True)
    monkeypatch.setattr(semantic, "runtime_backend", lambda: "torch-fp32-v1")
    monkeypatch.setattr(semantic, "verify_model_files", lambda: {"fingerprint": "synthetic-model-fingerprint", "models": []})
    monkeypatch.setattr(semantic, "load_models", lambda with_reranker=True: (embedder, None))
    monkeypatch.delenv("SIYAR_SEMANTIC_DISABLED", raising=False)
    return semantic, embedder


def test_short_paragraph_windows_preserve_complete_paragraph_boundaries():
    from backend.semantic import token_windows

    paragraphs = [" ".join(f"فقرة{p}كلمة{w}" for w in range(7)) for p in range(4)]
    text = "\n\n".join(paragraphs)
    positions = [(100 + index, 101 + index) for index in range(len(text))]
    windows = list(token_windows(text, positions, TinyTokenizer(), max_tokens=10, overlap=3))
    starts = {100 + text.index(paragraph) for paragraph in paragraphs}
    ends = {100 + text.index(paragraph) + len(paragraph) for paragraph in paragraphs}
    assert windows
    assert all(window["token_count"] <= 10 for window in windows)
    assert all(window["source_char_start"] in starts and window["source_char_end"] in ends for window in windows)
    assert {word for window in windows for word in window["clean_text"].split()} == set(text.split())


def test_overlong_paragraph_is_split_without_silent_truncation_with_bounded_overlap():
    from backend.semantic import token_windows

    text = " ".join(f"كلمة{index}" for index in range(25))
    positions = [(50 + index, 51 + index) for index in range(len(text))]
    windows = list(token_windows(text, positions, TinyTokenizer(), max_tokens=10, overlap=2))
    assert len(windows) >= 3
    assert all(window["token_count"] <= 10 for window in windows)
    assert windows[0]["source_char_start"] == 50
    assert windows[-1]["source_char_end"] == 50 + len(text)
    assert {word for window in windows for word in window["clean_text"].split()} == set(text.split())
    for previous, current in zip(windows, windows[1:]):
        assert len(set(previous["clean_text"].split()) & set(current["clean_text"].split())) <= 2


@pytest.mark.parametrize("line_ending", ["\n", "\r\n"])
@pytest.mark.parametrize("trailing", [" ", "\t", "  \t  "])
def test_trailing_formatting_whitespace_never_drops_or_splits_short_paragraphs(line_ending, trailing):
    from backend.semantic import token_windows

    paragraphs = [f"🧪 فِقْرَةٌ{index} مصطنعة كاملة" for index in range(3)]
    separator = line_ending * 2
    text = separator.join(paragraph + trailing for paragraph in paragraphs) + line_ending
    positions = [(777 + index, 778 + index) for index in range(len(text))]
    windows = list(token_windows(text, positions, TinyTokenizer(), max_tokens=6, overlap=2))
    assert len(windows) == 3
    assert [window["clean_text"] for window in windows] == paragraphs
    for paragraph, window in zip(paragraphs, windows):
        assert window["source_char_start"] == 777 + text.index(paragraph)
        assert window["source_char_end"] == 777 + text.index(paragraph) + len(paragraph)
        assert window["token_count"] == 4


def test_final_page_formatting_space_cannot_omit_the_last_paragraph():
    from backend.reading import reading_plain_with_map
    from backend.semantic import token_windows

    original = "# عينة مصطنعة ذات شاهد نهائي. PageV01P001\r\n"
    clean, positions = reading_plain_with_map(original, 21, starts_at_line_start=True)
    assert clean.endswith(" "), "Fixture reproduces the formatting space from a hidden terminal page marker"
    windows = list(token_windows(clean, positions, TinyTokenizer()))
    assert len(windows) == 1
    assert windows[0]["clean_text"] == "عينة مصطنعة ذات شاهد نهائي."
    assert original[windows[0]["source_char_start"] - 21:windows[0]["source_char_end"] - 21] == windows[0]["clean_text"]


def test_prepared_windows_are_exact_ranges_within_one_section_and_do_not_change_canonical_data(book, fake_models, tmp_path):
    semantic, embedder = fake_models
    directory = tmp_path / "semantic"
    report = semantic.prepare(book["database"], directory, download=False, batch_size=2)
    assert report["complete"] is True
    pointer = json.loads((directory / "active.json").read_text(encoding="utf-8"))
    version = directory / pointer["directory"]
    assert pointer["fingerprint"] == report["fingerprint"]
    assert not (directory / "prepare.lock").exists()
    assert not (directory / "building.json").exists()
    with sqlite3.connect(version / "windows.sqlite3") as sidecar, sqlite3.connect(book["database"]) as source:
        sidecar.row_factory = source.row_factory = sqlite3.Row
        rows = sidecar.execute("SELECT * FROM windows").fetchall()
        assert len(rows) == report["window_count"] > 0
        for row in rows:
            section = source.execute("SELECT * FROM source_sections WHERE id=?", (row["section_id"],)).fetchone()
            assert section["source_char_start"] <= row["source_char_start"] < row["source_char_end"] <= section["source_char_end"]
            assert row["token_count"] <= semantic.WINDOW_TOKENS
            assert book["text"][row["source_char_start"]:row["source_char_end"]]
        assert source.execute("SELECT full_text FROM sources").fetchone()[0] == book["text"]
        assert source.execute("SELECT COUNT(*) FROM review_log").fetchone()[0] == 0
    vectors = np.load(version / "vectors.npy", mmap_mode="r")
    assert vectors.shape == (report["window_count"], semantic.DIMENSION)
    assert np.isfinite(vectors).all()
    # Batching may reorder windows by length for CPU efficiency. The vector
    # stored at row id-1 must still belong to that exact window after scatter.
    with sqlite3.connect(version / "windows.sqlite3") as sidecar:
        sidecar.row_factory = sqlite3.Row
        for row in sidecar.execute("SELECT id,clean_text FROM windows"):
            expected_axis = len("passage: " + row["clean_text"]) % 384
            assert vectors[row["id"] - 1, expected_axis] == 1
            assert np.count_nonzero(vectors[row["id"] - 1]) == 1
    assert semantic.semantic_status(book["database"], directory)["status"] == "ready"


def test_actual_window_auditor_detects_missing_ranges_and_invalid_token_counts(book, fake_models, tmp_path):
    from scripts.audit_window_coverage import audit

    semantic, _ = fake_models
    directory = tmp_path / "audited-index"
    semantic.prepare(book["database"], directory, download=False, batch_size=2)
    pointer = json.loads((directory / "active.json").read_text(encoding="utf-8"))
    work = directory / pointer["directory"]
    assert audit(book["database"], work)["complete"]
    with sqlite3.connect(work / "windows.sqlite3") as db:
        db.execute("UPDATE windows SET token_count=? WHERE id=1", (321,))
    invalid = audit(book["database"], work)
    assert not invalid["complete"] and invalid["invalid_windows"]
    with sqlite3.connect(work / "windows.sqlite3") as db:
        db.execute("DELETE FROM windows")
    missing = audit(book["database"], work)
    assert not missing["complete"] and missing["uncovered"]


def test_interrupted_build_resumes_checkpoint_and_only_publishes_complete_index(book, fake_models, monkeypatch, tmp_path):
    semantic, _ = fake_models
    directory = tmp_path / "resume-index"
    broken = TinyEmbedder(fail_after=1)
    monkeypatch.setattr(semantic, "load_models", lambda with_reranker=True: (broken, None))
    with pytest.raises(RuntimeError, match="interrupted"):
        semantic.prepare(book["database"], directory, download=False, batch_size=1)
    assert not (directory / "active.json").exists()
    assert not (directory / "prepare.lock").exists()
    checkpoints = list(directory.glob("version-*/checkpoint.json"))
    assert len(checkpoints) == 1
    encoded = json.loads(checkpoints[0].read_text(encoding="utf-8"))["encoded"]
    assert encoded == 1
    resumed = TinyEmbedder()
    monkeypatch.setattr(semantic, "load_models", lambda with_reranker=True: (resumed, None))
    report = semantic.prepare(book["database"], directory, download=False, batch_size=1)
    assert len(resumed.calls) == report["window_count"] - encoded
    assert semantic.semantic_status(book["database"], directory)["status"] == "ready"


def test_stale_renderer_and_incomplete_manifest_never_claim_ready(book, fake_models, monkeypatch, tmp_path):
    semantic, _ = fake_models
    directory = tmp_path / "status-index"
    semantic.prepare(book["database"], directory, download=False, batch_size=2)
    pointer = json.loads((directory / "active.json").read_text(encoding="utf-8"))
    manifest_path = directory / pointer["directory"] / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["complete"] = False
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert semantic.semantic_status(book["database"], directory)["ready"] is False
    manifest["complete"] = True
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(semantic, "renderer_version", lambda: "a-new-renderer-version")
    status = semantic.semantic_status(book["database"], directory)
    assert status["status"] == "stale" and status["ready"] is False


def test_existing_prepare_lock_is_not_silently_removed_or_overwritten(book, fake_models, tmp_path):
    semantic, _ = fake_models
    directory = tmp_path / "locked-index"
    directory.mkdir()
    lock = directory / "prepare.lock"
    lock.write_text("existing-builder", encoding="utf-8")
    with pytest.raises(RuntimeError):
        semantic.prepare(book["database"], directory, download=False)
    assert lock.read_text(encoding="utf-8") == "existing-builder"


def test_atomic_json_retries_windows_sharing_violation_without_replacing_prior_valid_state(tmp_path, monkeypatch):
    from backend import semantic

    target = tmp_path / "checkpoint.json"
    target.write_text('{"encoded": 1}', encoding="utf-8")
    actual_replace = semantic.os.replace
    attempts = []

    def transient_lock(source, destination):
        attempts.append(True)
        if len(attempts) <= 2:
            assert json.loads(target.read_text(encoding="utf-8")) == {"encoded": 1}
            raise PermissionError("synthetic Windows sharing violation")
        return actual_replace(source, destination)

    monkeypatch.setattr(semantic.os, "replace", transient_lock)
    monkeypatch.setattr(semantic.time, "sleep", lambda duration: None)
    semantic.atomic_json(target, {"encoded": 2})
    assert len(attempts) == 3
    assert json.loads(target.read_text(encoding="utf-8")) == {"encoded": 2}
    assert not target.with_name("checkpoint.json.tmp").exists()


def test_vector_integrity_is_checked_before_any_search_or_cached_reuse(book, fake_models, tmp_path):
    from backend import smart_search

    semantic, _ = fake_models
    directory = tmp_path / "integrity-index"
    semantic.prepare(book["database"], directory, download=False, batch_size=2)
    status = semantic.semantic_status(book["database"], directory)
    smart_search._VECTOR_CACHE.clear()
    vectors = smart_search._vectors(status)
    assert vectors.shape[1] == semantic.DIMENSION
    del vectors
    # Modify one byte while retaining a valid-sized NPY file. A mere existence
    # or shape check must not accept a model/index artifact with a broken SHA.
    path = tmp_path / "broken-version"
    path.mkdir()
    real_version = Path(status["directory"])
    import shutil
    for name in ("vectors.npy", "manifest.json", "windows.sqlite3"):
        shutil.copyfile(real_version / name, path / name)
    with (path / "vectors.npy").open("r+b") as file:
        file.seek(-1, 2)
        byte = file.read(1)
        file.seek(-1, 2)
        file.write(bytes([byte[0] ^ 1]))
    with pytest.raises(RuntimeError, match="سلامة"):
        smart_search._vectors({**status, "directory": str(path)})
    smart_search._VECTOR_CACHE.clear()


def test_missing_and_explicitly_disabled_indexes_do_not_download_during_status(book, monkeypatch, tmp_path):
    from backend import semantic

    def no_download():
        pytest.fail("A status/read-only request must never download models")

    monkeypatch.setattr(semantic, "download_models", no_download)
    monkeypatch.delenv("SIYAR_SEMANTIC_DISABLED", raising=False)
    missing = semantic.semantic_status(book["database"], tmp_path / "missing-index")
    assert missing["ready"] is False and missing["status"] == "missing"
    monkeypatch.setenv("SIYAR_SEMANTIC_DISABLED", "1")
    disabled = semantic.semantic_status(book["database"], tmp_path / "missing-index")
    assert disabled["ready"] is False and disabled["status"] == "disabled"
    assert disabled["network_at_query_time"] is False


def test_complete_real_index_windows_stay_within_source_sections_and_pinned_source_if_ready():
    from backend.semantic import DEFAULT_DB, semantic_status, canonical_connection

    status = semantic_status(DEFAULT_DB)
    if not status.get("ready"):
        pytest.skip("The complete real-model semantic index is not ready; model-free safety tests remain active")
    version = Path(status["directory"])
    with sqlite3.connect((version / "windows.sqlite3").as_uri() + "?mode=ro", uri=True) as windows, canonical_connection(DEFAULT_DB) as source:
        windows.row_factory = sqlite3.Row
        sections = {row["id"]: row for row in source.execute("SELECT * FROM source_sections")}
        sources = {row["id"]: row for row in source.execute("SELECT id,sha256,full_text FROM sources")}
        count = 0
        for window in windows.execute("SELECT * FROM windows ORDER BY id"):
            count += 1
            section = sections[window["section_id"]]
            original = sources[window["source_id"]]
            assert window["source_sha256"] == original["sha256"]
            assert section["source_id"] == window["source_id"]
            assert section["source_char_start"] <= window["source_char_start"] < window["source_char_end"] <= section["source_char_end"]
            assert original["full_text"][window["source_char_start"]:window["source_char_end"]]
            assert 0 < window["token_count"] <= 320
        assert count == status["window_count"]
