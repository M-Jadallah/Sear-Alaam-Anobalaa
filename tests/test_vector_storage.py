import pytest
np = pytest.importorskip("numpy")

from backend.vector_storage import quantize_rows, cosine_scores


def test_row_quantization_keeps_cosines_and_uses_quarter_disk(tmp_path):
    rng = np.random.default_rng(1847)
    original = rng.normal(size=(3000, 768)).astype(np.float32)
    original /= np.linalg.norm(original, axis=1, keepdims=True)
    stored = quantize_rows(original)
    path = tmp_path / "vectors.npy"
    np.save(path, stored)
    mapped = np.load(path, mmap_mode="r")
    assert mapped.nbytes == original.nbytes / 4
    # Includes a real nearest neighbour and unrelated queries.
    for query in [original[81], *original[100:104]]:
        expected = original @ query
        actual = cosine_scores(mapped, query, chunk_size=127)
        assert np.max(np.abs(actual - expected)) < .002
        assert np.argmax(actual) == np.argmax(expected)


def test_byte_vector_scoring_only_reads_bounded_slices():
    class Bounded:
        dtype = np.dtype("int8")
        def __len__(self): return 10001
        def __getitem__(self, key):
            assert isinstance(key, slice) and key.stop - key.start <= 2048
            return np.ones((min(key.stop, len(self)) - key.start, 768), dtype=np.int8)
        def __array__(self, *args, **kwargs):
            pytest.fail("The entire mapped index was materialized")
    scores = cosine_scores(Bounded(), np.ones(768, dtype=np.float32) / np.sqrt(768))
    assert len(scores) == 10001
    assert np.allclose(scores, 1)


def test_invalid_vectors_are_rejected():
    for value in [np.zeros((1, 768)), np.full((1, 768), np.nan)]:
        with pytest.raises(ValueError): quantize_rows(value)
    with pytest.raises(ValueError): cosine_scores(np.zeros((1, 768), dtype=np.int8), np.ones(768))
