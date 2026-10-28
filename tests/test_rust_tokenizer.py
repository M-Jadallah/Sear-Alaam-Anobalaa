"""Real tokenizer equivalence; no model inference or network download."""
from pathlib import Path
import pytest


@pytest.mark.parametrize("kind", ["embed", "rerank"])
def test_rust_adapter_preserves_ids_masks_pairs_and_unicode_offsets(kind):
    from backend.semantic import model_path
    from backend.semantic_onnx import RustTokenizer
    directory = model_path(kind)
    if not (directory / "tokenizer.json").is_file():
        pytest.skip("Pinned tokenizer files are absent")
    transformers = pytest.importorskip("transformers")
    expected = transformers.AutoTokenizer.from_pretrained(str(directory), local_files_only=True, trust_remote_code=False)
    actual = RustTokenizer(directory)
    examples = ["الإِخْلَاصُ 🧪 في سِيَرِ أَعْلَامِ النُّبَلاءِ", "query: نور العلم", "passage: قال: حدثنا\nفلانٌ."]
    for text in examples:
        for special in (True, False):
            a = actual(text, add_special_tokens=special, return_offsets_mapping=True)
            b = expected(text, add_special_tokens=special, return_offsets_mapping=True)
            for key in ["input_ids", "attention_mask", "offset_mapping"]:
                assert a[key] == b[key]
    import numpy as np
    for pairs in (None, list(reversed(examples))):
        a = actual(examples, text_pair=pairs, padding=True, return_tensors="np")
        b = expected(examples, text_pair=pairs, padding=True, truncation=False, return_tensors="np")
        for key in ["input_ids", "attention_mask"]:
            assert np.array_equal(a[key], b[key])
