"""Pinned ONNX Runtime CPU adapters for the same approved retrieval models."""
from pathlib import Path
import json
import os
import urllib.request

RUNTIME = "onnxruntime-cpu-int8-v1"
PORTABLE_RUNTIME = "onnxruntime-cpu-int8-rowwise-v3"


def quantize_portable(source, target):
    import tempfile
    import numpy as np
    import onnx
    from onnx import helper, numpy_helper, TensorProto
    from onnxruntime.quantization import QuantType, quantize_dynamic
    # Per-tensor vocabulary quantization loses low-amplitude word vectors when
    # a rare row has an outlier. Quantize each vocabulary row independently,
    # gather its scale, then dequantize only the requested token rows.
    graph = onnx.load(str(source))
    initializers = {value.name: value for value in graph.graph.initializer}
    replacements = {}
    additions = []
    for node in graph.graph.node:
        if node.op_type != "Gather" or node.input[0] not in initializers:
            continue
        weight = initializers[node.input[0]]
        if weight.name in replacements or weight.data_type != TensorProto.FLOAT or len(weight.dims) != 2 or weight.dims[0] < 1024:
            continue
        users = [other for other in graph.graph.node if weight.name in other.input]
        if any(other.op_type != "Gather" or any(attr.name == "axis" and attr.i != 0 for attr in other.attribute) for other in users):
            continue
        values = numpy_helper.to_array(weight)
        quantized = np.empty(values.shape, dtype=np.int8)
        scales = np.empty(values.shape[0], dtype=np.float32)
        for start in range(0, len(values), 4096):
            batch = values[start:start + 4096]
            scale = np.maximum(np.max(np.abs(batch), axis=1) / 127, np.finfo(np.float32).tiny)
            scales[start:start + len(batch)] = scale
            quantized[start:start + len(batch)] = np.clip(np.rint(batch / scale[:, None]), -127, 127).astype(np.int8)
        qname, sname = weight.name + "_row_int8", weight.name + "_row_scales"
        additions.extend([numpy_helper.from_array(quantized, qname), numpy_helper.from_array(scales, sname)])
        replacements[weight.name] = (qname, sname)
    nodes = []
    for index, node in enumerate(graph.graph.node):
        if node.op_type == "Gather" and node.input[0] in replacements:
            qname, sname = replacements[node.input[0]]
            output = node.output[0]
            prefix = "siyar_row_embedding_" + str(index)
            axes = prefix + "_axes"
            additions.append(numpy_helper.from_array(np.array([-1], dtype=np.int64), axes))
            nodes.extend([
                helper.make_node("Gather", [qname, node.input[1]], [prefix + "_q"], axis=0),
                helper.make_node("Gather", [sname, node.input[1]], [prefix + "_scale"], axis=0),
                helper.make_node("Unsqueeze", [prefix + "_scale", axes], [prefix + "_expanded"]),
                helper.make_node("Cast", [prefix + "_q"], [prefix + "_float"], to=TensorProto.FLOAT),
                helper.make_node("Mul", [prefix + "_float", prefix + "_expanded"], [output]),
            ])
        else:
            nodes.append(node)
    retained = [value for value in graph.graph.initializer if value.name not in replacements]
    del graph.graph.node[:]
    graph.graph.node.extend(nodes)
    del graph.graph.initializer[:]
    graph.graph.initializer.extend(retained + additions)
    with tempfile.TemporaryDirectory() as temporary:
        staged = Path(temporary) / "rowwise.onnx"
        onnx.save(graph, str(staged))
        del graph
        # Per-channel signed 7-bit weights avoid AVX2 accumulation saturation;
        # the format occupies one byte per weight on every x86 CPU.
        quantize_dynamic(str(staged), str(target), weight_type=QuantType.QInt8,
                         per_channel=True, reduce_range=True, op_types_to_quantize=["MatMul"])


def validate_onnx_manifest(manifest):
    from .semantic import MODEL_DIR, EMBED_MODEL, EMBED_REVISION, RERANK_MODEL, RERANK_REVISION, _json_hash
    expected = {"embed": (EMBED_MODEL, EMBED_REVISION, "model_qint8_avx512_vnni.onnx"), "rerank": (RERANK_MODEL, RERANK_REVISION, "model_quint8_avx2.onnx")}
    entries = manifest.get("models", [])
    if manifest.get("runtime") not in (RUNTIME, PORTABLE_RUNTIME) or len(entries) != 2 or {item.get("kind") for item in entries} != set(expected):
        raise RuntimeError("وصف نماذج ONNX غير مكتمل أو نوع التشغيل غير معتمد.")
    for item in entries:
        kind = item["kind"]
        name, revision, filename = expected[kind]
        if manifest["runtime"] == PORTABLE_RUNTIME:
            filename = "model_rowwise_int8.onnx"
        if item.get("model") != name or item.get("revision") != revision:
            raise RuntimeError("إصدار نموذج ONNX لا يطابق الإصدار المثبت.")
        relative = Path(item["file"])
        target = (MODEL_DIR / relative).resolve()
        required = (MODEL_DIR / ("onnx-" + kind + "-" + revision) / filename).resolve()
        if relative.is_absolute() or ".." in relative.parts or target != required:
            raise RuntimeError("مسار نموذج ONNX غير آمن أو غير مطابق للملف المعتمد.")
    if manifest.get("fingerprint") != _json_hash(entries):
        raise RuntimeError("بصمة وصف نماذج ONNX غير صحيحة.")
    return manifest


def download_onnx_models():
    from .semantic import MODEL_DIR, EMBED_MODEL, EMBED_REVISION, RERANK_MODEL, RERANK_REVISION, file_sha256, atomic_json, _json_hash, runtime_backend
    portable = runtime_backend() == PORTABLE_RUNTIME
    entries = []
    for kind, name, revision, filename in (("embed", EMBED_MODEL, EMBED_REVISION, "model_qint8_avx512_vnni.onnx"), ("rerank", RERANK_MODEL, RERANK_REVISION, "model_quint8_avx2.onnx")):
        if portable:
            filename = "model_rowwise_int8.onnx"
        directory = MODEL_DIR / ("onnx-" + kind + "-" + revision)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / filename
        if not target.exists():
            temporary = target.with_name(target.name + ".incomplete")
            offset = temporary.stat().st_size if temporary.exists() else 0
            headers = {"Range": "bytes=" + str(offset) + "-"} if offset else {}
            url = "https://huggingface.co/" + name + "/resolve/" + revision + "/onnx/" + ("model.onnx" if portable else filename)
            print(json.dumps({"stage": "download_onnx", "model": name, "revision": revision, "file": filename}), flush=True)
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=120) as response:
                append = offset > 0 and response.status == 206
                expected, received = int(response.headers.get("Content-Length", "0")), 0
                with temporary.open("ab" if append else "wb") as file:
                    for chunk in iter(lambda: response.read(1024 * 1024), b""):
                        file.write(chunk)
                        received += len(chunk)
                if expected and received != expected:
                    raise RuntimeError("لم يكتمل تنزيل ONNX.")
            if portable:
                # Build-only dependencies. Row-wise embeddings and signed 7-bit MatMul weights run without AVX512.
                quantized = target.with_suffix(".quantizing.onnx")
                quantize_portable(temporary, quantized)
                os.replace(quantized, target)
                temporary.unlink()
            else:
                os.replace(temporary, target)
        entries.append({"kind": kind, "model": name, "revision": revision, "file": target.relative_to(MODEL_DIR).as_posix(), "sha256": file_sha256(target), "size": target.stat().st_size})
    manifest = {"runtime": PORTABLE_RUNTIME if portable else RUNTIME, "models": entries, "fingerprint": _json_hash(entries)}
    atomic_json(MODEL_DIR / "onnx-manifest.json", manifest)
    return manifest


def load_onnx_models(with_reranker=True):
    import onnxruntime as ort
    from .semantic import MODEL_DIR, model_path, file_sha256, THREADS
    manifest = validate_onnx_manifest(json.loads((MODEL_DIR / "onnx-manifest.json").read_text(encoding="utf-8")))
    models = {}
    for entry in manifest["models"]:
        if entry["kind"] == "rerank" and not with_reranker:
            continue
        file = MODEL_DIR / entry["file"]
        if file_sha256(file) != entry["sha256"]:
            raise RuntimeError("فشلت بصمة نموذج ONNX المحلي.")
        options = ort.SessionOptions()
        options.intra_op_num_threads = THREADS
        options.inter_op_num_threads = 1
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        options.enable_cpu_mem_arena = False
        options.enable_mem_pattern = False
        session = ort.InferenceSession(str(file), options, providers=["CPUExecutionProvider"])
        if os.environ.get("SIYAR_ONNX_TOKENIZER") == "rust":
            tokenizer = RustTokenizer(model_path(entry["kind"]))
        else:
            from transformers import AutoTokenizer
            tokenizer = AutoTokenizer.from_pretrained(str(model_path(entry["kind"])), local_files_only=True, trust_remote_code=False)
        models[entry["kind"]] = OnnxEmbedding(session, tokenizer) if entry["kind"] == "embed" else OnnxReranker(session, tokenizer)
    return models["embed"], models.get("rerank")


class RustTokenizer:
    """Small offline adapter preserving fast-tokenizer IDs and Unicode offsets."""
    def __init__(self, directory):
        from tokenizers import Tokenizer
        self.backend = Tokenizer.from_file(str(Path(directory) / "tokenizer.json"))
        # Transformers 5's XLM-R loader rebuilds the older E5 tokenizer from
        # SentencePiece. Match that normalization/whitespace behavior exactly,
        # including offsets around combining marks and astral Unicode.
        description = json.loads(self.backend.to_str())
        normalizer = description.get("normalizer", {})
        if normalizer.get("type") == "Sequence" and normalizer.get("normalizers", [{}])[0].get("type") == "Precompiled":
            from tokenizers import normalizers, pre_tokenizers
            import base64
            self.backend.normalizer = normalizers.Precompiled(base64.b64decode(normalizer["normalizers"][0]["precompiled_charsmap"]))
            self.backend.pre_tokenizer = pre_tokenizers.Sequence([pre_tokenizers.WhitespaceSplit(), pre_tokenizers.Metaspace(replacement="▁", prepend_scheme="always")])
        self.backend.no_truncation()
        self.backend.no_padding()

    def __call__(self, texts, text_pair=None, padding=False, truncation=False, return_tensors=None, add_special_tokens=True, return_offsets_mapping=False, **kwargs):
        import numpy as np
        if truncation:
            raise ValueError("لا يسمح بالبتر الصامت.")
        single = isinstance(texts, str)
        values = [texts] if single else texts
        pairs = [text_pair] if single and text_pair is not None else text_pair
        inputs = list(zip(values, pairs)) if pairs is not None else values
        encoded = self.backend.encode_batch(inputs, add_special_tokens=add_special_tokens)
        width = max((len(row.ids) for row in encoded), default=0)
        pad = self.backend.token_to_id("<pad>")
        pad = 0 if pad is None else pad
        output = {"input_ids": [], "attention_mask": [], "token_type_ids": []}
        if return_offsets_mapping:
            output["offset_mapping"] = []
        for row in encoded:
            extra = width - len(row.ids) if padding else 0
            output["input_ids"].append(row.ids + [pad] * extra)
            output["attention_mask"].append(row.attention_mask + [0] * extra)
            output["token_type_ids"].append(row.type_ids + [0] * extra)
            if return_offsets_mapping:
                output["offset_mapping"].append(row.offsets + [(0, 0)] * extra)
        if return_tensors == "np":
            return {key: np.asarray(value, dtype=np.int64) for key, value in output.items()}
        return {key: value[0] if single else value for key, value in output.items()}


class OnnxBase:
    def __init__(self, session, tokenizer):
        self.session = session
        self.tokenizer = tokenizer
        self.input_names = {item.name for item in session.get_inputs()}
        self.max_seq_length = 512

    def run(self, texts, texts_pair=None):
        import numpy as np
        tokens = self.tokenizer(texts, text_pair=texts_pair, padding=True, truncation=False, return_tensors="np")
        if tokens["input_ids"].shape[1] > 512:
            raise ValueError("مدخل أطول من حد النموذج؛ لم يبتر.")
        inputs = {key: value.astype(np.int64) for key, value in tokens.items() if key in self.input_names}
        if "token_type_ids" in self.input_names and "token_type_ids" not in inputs:
            inputs["token_type_ids"] = np.zeros_like(inputs["input_ids"], dtype=np.int64)
        return self.session.run(None, inputs), tokens


class OnnxEmbedding(OnnxBase):
    def encode(self, texts, batch_size=16, normalize_embeddings=True, **kwargs):
        import numpy as np
        if not texts:
            return np.zeros((0, 384), dtype=np.float32)
        vectors = []
        for start in range(0, len(texts), batch_size):
            outputs, tokens = self.run(texts[start:start+batch_size])
            hidden = outputs[0]
            if hidden.ndim == 2:
                pooled = hidden
            else:
                mask = tokens["attention_mask"][..., None].astype(np.float32)
                pooled = (hidden * mask).sum(axis=1) / np.maximum(mask.sum(axis=1), 1)
            if normalize_embeddings:
                pooled /= np.maximum(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-12)
            vectors.append(pooled.astype(np.float32))
        return np.concatenate(vectors, axis=0)


class OnnxReranker(OnnxBase):
    def predict(self, pairs, batch_size=8, **kwargs):
        import numpy as np
        values = []
        for start in range(0, len(pairs), batch_size):
            batch = pairs[start:start+batch_size]
            outputs, _ = self.run([pair[0] for pair in batch], [pair[1] for pair in batch])
            values.extend(np.asarray(outputs[0]).reshape(-1).tolist())
        return np.asarray(values)


if __name__ == "__main__":
    print(json.dumps(download_onnx_models(), indent=2))
