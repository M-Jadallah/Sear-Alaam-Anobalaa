"""Vocabulary weights must actually shrink; tiny MatMul-only tests miss this."""
import pytest


def test_portable_quantization_compresses_embeddings_and_preserves_values(tmp_path):
    onnx = pytest.importorskip("onnx")
    ort = pytest.importorskip("onnxruntime")
    import numpy as np
    from onnx import TensorProto, helper, numpy_helper
    from backend.semantic_onnx import quantize_portable
    weights = np.linspace(-1, 1, 1024 * 64, dtype=np.float32).reshape(1024, 64)
    # A rare large row must not destroy ordinary low-amplitude token rows.
    weights[1022] *= 1000
    graph = helper.make_graph([helper.make_node("Gather", ["weights", "ids"], ["result"], axis=0)], "synthetic-embedding",
        [helper.make_tensor_value_info("ids", TensorProto.INT64, [None, None])],
        [helper.make_tensor_value_info("result", TensorProto.FLOAT, [None, None, 64])],
        [numpy_helper.from_array(weights, "weights")])
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)])
    model.ir_version = 9
    original, quantized = tmp_path / "original.onnx", tmp_path / "quantized.onnx"
    onnx.save(model, original)
    quantize_portable(original, quantized)
    assert quantized.stat().st_size < original.stat().st_size * .4
    session = ort.InferenceSession(str(quantized), providers=["CPUExecutionProvider"])
    ids = np.array([[0, 23, 1023]], dtype=np.int64)
    actual = session.run(None, {"ids": ids})[0]
    assert np.allclose(actual, weights[ids], atol=.01)
    large = session.run(None, {"ids": np.array([[1022]], dtype=np.int64)})[0]
    assert np.allclose(large, weights[[[1022]]], rtol=.005, atol=.1)
