"""Optional pinned, offline-at-query-time CPU retrieval index.

The canonical SQLite database is opened read-only. Every vector indexes a literal
source range; the sidecar is disposable and published atomically after completion.
Run ``python -m backend.semantic prepare`` to download the approved models and
resume/build the complete index. No model download occurs while searching.
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import importlib.util
import json
import os
import re
from pathlib import Path
import sqlite3
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from .db import DEFAULT_DB, ROOT
from .normalization import normalize_arabic

EMBED_MODEL = "intfloat/multilingual-e5-small"
EMBED_REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
RERANK_MODEL = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
RERANK_REVISION = "1427fd652930e4ba29e8149678df786c240d8825"
INDEX_VERSION = "siyar-semantic-v2"
WINDOW_TOKENS = 320
OVERLAP_TOKENS = 64
THREADS = max(1, min(4, int(os.environ.get("SIYAR_CPU_THREADS", "4"))))
DIMENSION = 384
DEFAULT_SEMANTIC_DIR = ROOT / "data" / "semantic"
MODEL_DIR = Path(os.environ.get("SIYAR_MODEL_DIR", ROOT / "data" / "models"))


def canonical_connection(db_path):
    connection = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True, timeout=30)
    connection.row_factory = sqlite3.Row
    return connection


def _json_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    # Windows readers/virus scanners can briefly hold a handle without delete
    # sharing. Keep the prior valid JSON and retry the atomic rename; do not
    # abandon an hour-long resumable build for a transient sharing violation.
    for attempt in range(15):
        try:
            os.replace(temporary, path)
            break
        except PermissionError:
            if attempt == 14:
                raise
            time.sleep(min(0.05 * (attempt + 1), 0.5))


def sources_fingerprint(db_path):
    try:
        with canonical_connection(db_path) as db:
            return [dict(row) for row in db.execute("SELECT id,sha256,parser_version,char_count FROM sources ORDER BY id")]
    except sqlite3.Error:
        return []


def renderer_version():
    from .reading import RENDERER_VERSION
    return RENDERER_VERSION


def runtime_backend():
    selected = MODEL_DIR / "runtime.json"
    if selected.is_file():
        return json.loads(selected.read_text(encoding="utf-8")).get("runtime", "torch-fp32-v1")
    return "torch-fp32-v1"


def effective_model_manifest(base=None, verify=False):
    base = base or json.loads(model_manifest_path().read_text(encoding="utf-8"))
    if not runtime_backend().startswith("onnxruntime-cpu-int8-"):
        return base
    quant = json.loads((MODEL_DIR / "onnx-manifest.json").read_text(encoding="utf-8"))
    from .semantic_onnx import validate_onnx_manifest
    validate_onnx_manifest(quant)
    if verify:
        for item in quant["models"]:
            file = MODEL_DIR / item["file"]
            if file_sha256(file) != item["sha256"]:
                raise RuntimeError("فشلت بصمة ONNX المحلي.")
    return {**base, "runtime": quant["runtime"], "onnx_models": quant["models"], "fingerprint": _json_hash({"base": base["fingerprint"], "onnx": quant["fingerprint"], "runtime": quant["runtime"]})}


def model_artifact_stamps():
    """Cheap cache invalidation; byte integrity is verified before model use."""
    try:
        manifest = effective_model_manifest()
        paths = [model_path(model["kind"]) / item["path"] for model in manifest["models"] for item in model["files"]]
        paths.extend(MODEL_DIR / item["file"] for item in manifest.get("onnx_models", []))
        return [(str(path), path.stat().st_size, path.stat().st_mtime_ns) for path in paths]
    except (OSError, ValueError, KeyError, RuntimeError):
        return None


def _safe_version_path(directory, relative):
    relative = Path(relative)
    resolved = (Path(directory) / relative).resolve()
    if relative.is_absolute() or ".." in relative.parts or resolved.parent != Path(directory).resolve() or not relative.name.startswith("version-"):
        raise ValueError("مسار إصدار الفهرس غير آمن.")
    return resolved


def index_configuration(db_path):
    return {"index_version": INDEX_VERSION, "sources": sources_fingerprint(db_path),
            "renderer_version": renderer_version(), "window_tokens": WINDOW_TOKENS,
            "runtime": runtime_backend(),
            "overlap_tokens": OVERLAP_TOKENS, "dimension": DIMENSION,
            "embed_model": EMBED_MODEL, "embed_revision": EMBED_REVISION,
            "rerank_model": RERANK_MODEL, "rerank_revision": RERANK_REVISION}


def model_path(kind):
    revision = EMBED_REVISION if kind == "embed" else RERANK_REVISION
    return MODEL_DIR / (kind + "-" + revision)


def model_manifest_path():
    return MODEL_DIR / "manifest.json"


def optional_dependencies_available():
    names = ["numpy", "onnxruntime", "tokenizers"] if runtime_backend().startswith("onnxruntime-cpu-int8-") else ["torch", "numpy", "sentence_transformers"]
    return all(importlib.util.find_spec(name) is not None for name in names)


def download_models():
    """Only the explicit prepare/download CLI may use the network."""
    models = []
    for kind, name, revision in (("embed", EMBED_MODEL, EMBED_REVISION), ("rerank", RERANK_MODEL, RERANK_REVISION)):
        destination = model_path(kind)
        print(json.dumps({"stage": "download", "model": name, "revision": revision}), flush=True)
        destination.mkdir(parents=True, exist_ok=True)
        # The standard-library downloader lets an explicit download run while
        # optional inference wheels are still installing. Only pinned repository
        # files are accepted; interrupted files resume using HTTP byte ranges.
        api = "https://huggingface.co/api/models/" + name + "/revision/" + revision
        with urllib.request.urlopen(api, timeout=60) as response:
            details = json.load(response)
        extensions = (".json", ".txt", ".model") if runtime_backend() == "onnxruntime-cpu-int8-rowwise-v3" else (".json", ".safetensors", ".txt", ".model")
        names = [item["rfilename"] for item in details["siblings"]
                 if (item["rfilename"].endswith(extensions)
                     or Path(item["rfilename"]).name in {"README.md", "LICENSE", "LICENSE.md", "LICENSE.txt", "NOTICE"})
                 and not item["rfilename"].startswith(("onnx/", "openvino/"))]
        def fetch(relative):
            relative_path = Path(relative)
            if relative_path.is_absolute() or ".." in relative_path.parts:
                raise RuntimeError("مسار نموذج غير آمن.")
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            url = "https://huggingface.co/" + name + "/resolve/" + revision + "/" + relative
            if target.exists():
                return
            temporary = target.with_name(target.name + ".incomplete")
            offset = temporary.stat().st_size if temporary.exists() else 0
            headers = {"User-Agent": "SiyarLocalSearch/1.0"}
            if offset:
                headers["Range"] = "bytes=" + str(offset) + "-"
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=120) as response:
                append = offset > 0 and response.status == 206
                expected = int(response.headers.get("Content-Length", "0"))
                received = 0
                with temporary.open("ab" if append else "wb") as file:
                    for chunk in iter(lambda: response.read(1024 * 1024), b""):
                        file.write(chunk)
                        received += len(chunk)
                if expected and received != expected:
                    raise RuntimeError("تنزيل ملف نموذج غير مكتمل: " + relative)
            os.replace(temporary, target)
        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(fetch, names))
        files = []
        for path in sorted(destination.rglob("*")):
            if path.is_file() and ".cache" not in path.parts:
                files.append({"path": path.relative_to(destination).as_posix(), "size": path.stat().st_size, "sha256": file_sha256(path)})
        if runtime_backend() == "onnxruntime-cpu-int8-rowwise-v3":
            if not {"tokenizer.json", "config.json"}.issubset({item["path"] for item in files}):
                raise RuntimeError("ملفات محول النص المحلي غير مكتملة.")
        elif not any(item["path"].endswith(".safetensors") for item in files):
            raise RuntimeError("لم يُنزّل ملف safetensors للنموذج؛ لا يُنشر فهرس ناقص.")
        models.append({"kind": kind, "model": name, "revision": revision, "files": files, "fingerprint": _json_hash(files)})
    manifest = {"models": models, "fingerprint": _json_hash(models)}
    atomic_json(model_manifest_path(), manifest)
    if runtime_backend().startswith("onnxruntime-cpu-int8-"):
        from .semantic_onnx import download_onnx_models
        download_onnx_models()
    return effective_model_manifest(manifest)


def verify_model_files():
    manifest = json.loads(model_manifest_path().read_text(encoding="utf-8"))
    for model in manifest["models"]:
        expected_revision = EMBED_REVISION if model["kind"] == "embed" else RERANK_REVISION
        if model["revision"] != expected_revision:
            raise RuntimeError("إصدار النموذج المحلي لا يطابق الإصدار المثبت.")
        for item in model["files"]:
            path = model_path(model["kind"]) / item["path"]
            if not path.is_file() or path.stat().st_size != item["size"] or file_sha256(path) != item["sha256"]:
                raise RuntimeError("فشل تحقق بصمة ملف النموذج: " + item["path"])
    return effective_model_manifest(manifest, verify=True)


def configure_cpu():
    for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[key] = str(THREADS)
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    from threadpoolctl import threadpool_limits
    threadpool_limits(limits=THREADS)
    if runtime_backend().startswith("onnxruntime-cpu-int8-"):
        return
    import torch
    torch.set_num_threads(THREADS)
    try:
        torch.set_num_interop_threads(1)
    except RuntimeError:
        pass


def load_models(with_reranker=True):
    configure_cpu()
    verify_model_files()
    if runtime_backend().startswith("onnxruntime-cpu-int8-"):
        from .semantic_onnx import load_onnx_models
        return load_onnx_models(with_reranker)
    from sentence_transformers import SentenceTransformer, CrossEncoder
    embed = SentenceTransformer(str(model_path("embed")), device="cpu", local_files_only=True, trust_remote_code=False)
    # The largest content window is 320 tokens plus the passage prefix and
    # special tokens. 512 is a hard architecture limit, never a truncation goal.
    embed.max_seq_length = 512
    reranker = CrossEncoder(str(model_path("rerank")), device="cpu", local_files_only=True, trust_remote_code=False, max_length=512) if with_reranker else None
    return embed, reranker


def token_windows(clean, positions, tokenizer, max_tokens=WINDOW_TOKENS, overlap=OVERLAP_TOKENS):
    """Yield clean slices and exact absolute source boundaries without truncation.

    Adjacent paragraphs are already joined by the reading renderer, and callers
    pass one section at a time. Fast tokenizer offsets preserve every included
    Unicode character, including astral characters and removed OpenITI markers.
    """
    if not clean.strip():
        return
    encoded = tokenizer(clean, add_special_tokens=False, return_offsets_mapping=True, truncation=False)
    offsets = encoded["offset_mapping"]
    if not offsets:
        return
    starts = [offset[0] for offset in offsets]
    spans = []
    # Page/technical markers can leave whitespace at paragraph ends. Accept it
    # before both separators and EOF, rather than omitting the last paragraph or
    # accidentally joining short paragraphs across their real boundary.
    for paragraph in re.finditer(r"\S(?:.*?\S)?(?=\s*(?:\n\s*\n|\Z))", clean, re.S):
        begin = bisect.bisect_left(starts, paragraph.start())
        stop = bisect.bisect_left(starts, paragraph.end())
        if stop > begin:
            spans.append((begin, stop))
    token_ranges = []
    index = 0
    while index < len(spans):
        begin, stop = spans[index]
        if stop - begin > max_tokens:
            for part in range(begin, stop, max_tokens - overlap):
                token_ranges.append((part, min(part + max_tokens, stop)))
                if part + max_tokens >= stop:
                    break
            index += 1
            continue
        end_index = index + 1
        while end_index < len(spans) and spans[end_index][1] - begin <= max_tokens:
            stop = spans[end_index][1]
            end_index += 1
        token_ranges.append((begin, stop))
        next_index = end_index
        while next_index > index + 1 and stop - spans[next_index - 1][0] <= overlap:
            next_index -= 1
        index = next_index
    for begin, stop in token_ranges:
        a, b = offsets[begin][0], offsets[stop-1][1]
        while a < b and (not clean[a].strip() or positions[a][0] == positions[a][1]):
            a += 1
        while b > a and (not clean[b-1].strip() or positions[b-1][0] == positions[b-1][1]):
            b -= 1
        if a < b:
            yield {"clean_text": clean[a:b], "source_char_start": positions[a][0],
                   "source_char_end": positions[b-1][1], "token_count": stop - begin}


def semantic_status(db_path=DEFAULT_DB, semantic_dir=None, include_paragraphs=True):
    directory = Path(semantic_dir or os.environ.get("SIYAR_SEMANTIC_DIR", DEFAULT_SEMANTIC_DIR))
    sources = sources_fingerprint(db_path)
    base = {"ready": False, "semantic_coverage": False, "network_at_query_time": False,
            "cpu_threads": THREADS, "model": EMBED_MODEL, "model_revision": EMBED_REVISION,
            "runtime": runtime_backend(),
            "reranker": RERANK_MODEL, "reranker_revision": RERANK_REVISION,
            "sources": sources, "window_tokens": WINDOW_TOKENS, "overlap_tokens": OVERLAP_TOKENS}
    if os.environ.get("SIYAR_SEMANTIC_DISABLED") == "1":
        return {**base, "status": "disabled", "reason": "البحث الدلالي معطل محليًا؛ البحث اللفظي متاح."}
    if not sources:
        return {**base, "status": "missing", "reason": "لا يوجد مصدر مستورد لبناء الفهرس الدلالي."}
    state = directory / "building.json"
    if state.exists():
        try:
            progress = json.loads(state.read_text(encoding="utf-8"))
            if time.time() - progress.get("updated_at", 0) < 180:
                return {**base, "status": "building", "progress": progress, "reason": "الفهرس الدلالي قيد البناء؛ البحث اللفظي متاح."}
        except (ValueError, OSError):
            pass
    pointer = directory / "active.json"
    if not pointer.exists():
        return {**base, "status": "missing", "dependencies_available": optional_dependencies_available(), "reason": "لم يُجهز الفهرس المحلي؛ نفذ python -m backend.semantic prepare."}
    try:
        active = json.loads(pointer.read_text(encoding="utf-8"))
        version = _safe_version_path(directory, active["directory"])
        manifest = json.loads((version / "manifest.json").read_text(encoding="utf-8"))
        if not manifest.get("complete"):
            return {**base, "status": "stale", "reason": "الفهرس لم يكتمل ولم يعتمد للنشر المحلي."}
        if manifest["configuration"] != index_configuration(db_path):
            return {**base, "status": "stale", "reason": "تغير المصدر أو نموذج القراءة أو إعدادات النموذج؛ أعد بناء الفهرس."}
        if not (version / "vectors.npy").is_file() or not (version / "windows.sqlite3").is_file():
            return {**base, "status": "stale", "reason": "ملفات الفهرس غير مكتملة؛ أعد تجهيزه."}
        if not optional_dependencies_available() or not model_manifest_path().is_file():
            return {**base, "status": "disabled", "reason": "متطلبات الاستدلال أو النماذج المحلية غير موجودة؛ البحث اللفظي متاح."}
        local_models = effective_model_manifest()
        for model in local_models["models"]:
            for item in model.get("files", []):
                path = model_path(model["kind"]) / item["path"]
                if not path.is_file() or path.stat().st_size != item["size"]:
                    return {**base, "status": "missing", "reason": "ملف نموذج محلي مفقود أو غير مكتمل؛ أعد تنزيل النماذج."}
        for item in local_models.get("onnx_models", []):
            path = MODEL_DIR / item["file"]
            if not path.is_file() or path.stat().st_size != item["size"]:
                return {**base, "status": "missing", "reason": "ملف نموذج ONNX محلي مفقود أو غير مكتمل."}
        if manifest["model_fingerprint"] != local_models["fingerprint"]:
            return {**base, "status": "stale", "reason": "تغيرت بصمة ملفات النموذج المحلي؛ أعد بناء الفهرس."}
        ready = {**base, "status": "ready", "ready": True, "semantic_coverage": True,
                "window_count": manifest["window_count"], "fingerprint": manifest["fingerprint"],
                "model_fingerprint": manifest["model_fingerprint"], "directory": str(version),
                "created_at": manifest["created_at"], "reason": "الفهرس الدلالي المحلي جاهز؛ النتائج اقتراحات تحتاج مراجعة."}
        if include_paragraphs:
            from .semantic_paragraphs import enrich_status
            return enrich_status(db_path,directory,ready)
        return ready
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        return {**base, "status": "stale", "reason": "تعذر التحقق من ملفات الفهرس: " + str(error)}


def prepare(db_path=DEFAULT_DB, semantic_dir=None, download=True, batch_size=16):
    directory = Path(semantic_dir or os.environ.get("SIYAR_SEMANTIC_DIR", DEFAULT_SEMANTIC_DIR))
    directory.mkdir(parents=True, exist_ok=True)
    lock = directory / "prepare.lock"
    try:
        lock_fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise RuntimeError("توجد عملية تجهيز أخرى أو ملف قفل سابق. تحقق من العملية قبل إزالة data/semantic/prepare.lock.")
    os.write(lock_fd, str(os.getpid()).encode())
    os.close(lock_fd)
    try:
        return _prepare_locked(db_path, directory, download, batch_size)
    finally:
        lock.unlink(missing_ok=True)


def _prepare_locked(db_path, directory, download, batch_size):
    if not optional_dependencies_available():
        raise RuntimeError("ثبت متطلبات البحث المحلي الاختيارية: python -m pip install -r requirements-semantic.txt")
    if not 1 <= batch_size <= 128:
        raise ValueError("حجم دفعة التضمين يجب أن يكون بين 1 و128.")
    configuration = index_configuration(db_path)
    if not configuration["sources"]:
        raise RuntimeError("لا يوجد مصدر مستورد. استورد الكتاب أولًا.")
    fingerprint = _json_hash(configuration)
    pointer = directory / "active.json"
    if pointer.is_file():
        active = json.loads(pointer.read_text(encoding="utf-8"))
        active_directory = _safe_version_path(directory, active["directory"])
        existing = json.loads((active_directory / "manifest.json").read_text(encoding="utf-8"))
        if existing.get("complete") and existing.get("configuration") == configuration:
            verified_models = verify_model_files()
            if existing.get("model_fingerprint") != verified_models["fingerprint"]:
                raise RuntimeError("تغيرت بصمة النموذج؛ احتفظ بالفهرس السابق وانشئ إصدار بناء جديد بعد مراجعة الملفات.")
            if file_sha256(active_directory / "vectors.npy") != existing["vectors_sha256"] or file_sha256(active_directory / "windows.sqlite3") != existing["windows_sha256"]:
                raise RuntimeError("ملفات الفهرس المنشور تالفة؛ لم تُعدل. احتفظ بنسخة تشخيصية وأزل مؤشر active.json والإصدار التالف قبل إعادة التجهيز.")
            print(json.dumps({"stage": "unchanged", "fingerprint": fingerprint, "windows": existing["window_count"]}), flush=True)
            return {**existing, "unchanged": True}
    work = directory / ("version-" + fingerprint[:20])
    work.mkdir(parents=True, exist_ok=True)
    def progress(stage, **values):
        item = {"stage": stage, "fingerprint": fingerprint, "updated_at": time.time(), **values}
        atomic_json(directory / "building.json", item)
        print(json.dumps(item, ensure_ascii=False), flush=True)
    progress("models")
    models = download_models() if download else verify_model_files()
    embed, _ = load_models(with_reranker=False)
    from .reading import reading_plain_with_map
    import numpy as np
    sidecar = work / "windows.sqlite3"
    db = sqlite3.connect(sidecar)
    db.row_factory = sqlite3.Row
    db.executescript((Path(__file__).with_name("semantic_schema.sql")).read_text(encoding="utf-8"))
    prior_models = db.execute("SELECT value FROM metadata WHERE key='model_fingerprint'").fetchone()
    if prior_models and prior_models[0] != models["fingerprint"]:
        db.close()
        raise RuntimeError("تغيرت ملفات النموذج منذ بدء هذا الإصدار؛ لا يجوز خلط التضمينات. انقل إصدار العمل القديم وأعد التجهيز.")
    db.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES ('model_fingerprint',?)", (models["fingerprint"],))
    db.commit()
    complete = db.execute("SELECT value FROM metadata WHERE key='windows_complete'").fetchone()
    if not complete:
        db.execute("DELETE FROM windows")
        db.execute("DELETE FROM windows_fts")
        with canonical_connection(db_path) as source:
            sections = source.execute("SELECT sc.*,s.sha256,b.id biography_id FROM source_sections sc JOIN sources s ON s.id=sc.source_id LEFT JOIN biographies b ON b.section_id=sc.id WHERE sc.section_kind!='front_matter' ORDER BY sc.source_id,sc.source_ordinal")
            count = 0
            for section_index, section in enumerate(sections, 1):
                text = section["full_text"]
                clean, positions = reading_plain_with_map(text, section["source_char_start"], starts_at_line_start=True)
                for window in token_windows(clean, positions, embed.tokenizer):
                    # Explicitly reject overlong model inputs; encode must never truncate.
                    model_length = len(embed.tokenizer("passage: " + window["clean_text"], add_special_tokens=True, truncation=False)["input_ids"])
                    if model_length > 512:
                        raise RuntimeError("نافذة تتجاوز حد النموذج؛ أوقف البناء بدل بتر النص.")
                    count += 1
                    db.execute("INSERT INTO windows(id,source_id,source_sha256,section_id,biography_id,heading,source_char_start,source_char_end,clean_text,token_count) VALUES (?,?,?,?,?,?,?,?,?,?)",
                               (count, section["source_id"], section["sha256"], section["id"], section["biography_id"], section["heading"], window["source_char_start"], window["source_char_end"], window["clean_text"], window["token_count"]))
                    db.execute("INSERT INTO windows_fts(window_id,normalized_text) VALUES (?,?)", (count, normalize_arabic(window["clean_text"])))
                if section_index % 100 == 0:
                    db.commit()
                    progress("windows", sections=section_index, windows=count)
        db.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES ('windows_complete','1')")
        db.commit()
    count = db.execute("SELECT COUNT(*) FROM windows").fetchone()[0]
    checkpoint = work / "checkpoint.json"
    checkpoint_state = json.loads(checkpoint.read_text(encoding="utf-8")) if checkpoint.exists() else {}
    encoded = checkpoint_state.get("encoded", 0)
    if checkpoint_state and (checkpoint_state.get("fingerprint") != fingerprint or not isinstance(encoded, int) or not 0 <= encoded <= count):
        db.close()
        raise RuntimeError("ملف استئناف غير صالح أو لإصدار آخر؛ لم تستخدم التضمينات الجزئية.")
    if checkpoint_state.get("model_fingerprint", models["fingerprint"]) != models["fingerprint"]:
        db.close()
        raise RuntimeError("بصمة نموذج نقطة الاستئناف مختلفة؛ أوقف البناء لمنع خلط المتجهات.")
    vectors_path = work / "vectors.npy"
    if not vectors_path.exists():
        encoded = 0
    order_path = work / "encoding-order.json"
    if order_path.exists():
        order = json.loads(order_path.read_text(encoding="utf-8"))
        if len(order) != count or sorted(order) != list(range(count)):
            raise RuntimeError("ترتيب استئناف التضمين غير صالح.")
    else:
        # Keep an already committed sequential prefix, then bucket all remaining
        # windows by length. This nearly halves padding on this corpus without
        # changing a single source window, model or vector's row identity.
        remaining = [row[0] - 1 for row in db.execute("SELECT id FROM windows WHERE id>? ORDER BY token_count,id", (encoded,))]
        order = list(range(encoded)) + remaining
        atomic_json(order_path, order)
    order_fingerprint = _json_hash(order)
    if checkpoint_state.get("order_fingerprint", order_fingerprint) != order_fingerprint:
        raise RuntimeError("تغير ترتيب التضمين منذ نقطة الاستئناف؛ لم ينشر الفهرس.")
    vectors = np.lib.format.open_memmap(vectors_path, mode="r+" if vectors_path.exists() else "w+", dtype="float32", shape=(count, DIMENSION))
    progress("encode", encoded=encoded, windows=count)
    for begin in range(encoded, count, batch_size):
        indices = order[begin:begin+batch_size]
        rows = [db.execute("SELECT clean_text FROM windows WHERE id=?", (index + 1,)).fetchone() for index in indices]
        embeddings = embed.encode(["passage: " + row[0] for row in rows], batch_size=batch_size, normalize_embeddings=True, show_progress_bar=False, convert_to_numpy=True)
        if embeddings.shape != (len(rows), DIMENSION) or not np.isfinite(embeddings).all():
            raise RuntimeError("نتيجة تضمين غير صالحة؛ لم ينشر الفهرس.")
        vectors[indices] = embeddings
        vectors.flush()
        atomic_json(checkpoint, {"encoded": begin + len(rows), "fingerprint": fingerprint, "model_fingerprint": models["fingerprint"], "order_fingerprint": order_fingerprint})
        if begin % (batch_size * 10) == 0 or begin + len(rows) == count:
            progress("encode", encoded=begin + len(rows), windows=count)
    for begin in range(0, count, 4096):
        chunk = vectors[begin:begin+4096]
        norms = np.linalg.norm(chunk, axis=1)
        if not np.isfinite(chunk).all() or not np.all((norms > 0.99) & (norms < 1.01)):
            raise RuntimeError("متجهات ناقصة أو غير مطبعة؛ لم ينشر الفهرس.")
    del vectors
    db.close()
    # A source changed during a long build must never acquire a ready pointer.
    if configuration != index_configuration(db_path):
        raise RuntimeError("تغير المصدر أثناء التجهيز؛ الفهرس لم ينشر. أعد التجهيز للمصدر الجديد.")
    manifest = {"fingerprint": fingerprint, "configuration": configuration, "window_count": count,
                "model_fingerprint": models["fingerprint"], "vectors_sha256": file_sha256(vectors_path),
                "encoding_policy": "length_buckets_with_committed_prefix_v1", "encoding_order_sha256": order_fingerprint,
                "windows_sha256": file_sha256(sidecar), "created_at": time.time(), "complete": True,
                "coverage": {"all_indexable_sections": True, "semantic_completeness_claimed": False}}
    atomic_json(work / "manifest.json", manifest)
    atomic_json(directory / "active.json", {"directory": work.name, "fingerprint": fingerprint})
    (directory / "building.json").unlink(missing_ok=True)
    progress("complete", windows=count)
    (directory / "building.json").unlink(missing_ok=True)
    return manifest


def main():
    parser = argparse.ArgumentParser(description="إعداد البحث الدلالي المحلي الاختياري")
    parser.add_argument("command", choices=("prepare", "download", "status"))
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--offline", action="store_true", help="استخدم ملفات النماذج الموجودة دون تنزيل")
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--runtime", choices=("torch", "onnx", "onnx-portable"), help="اختيار محلي محفوظ؛ أي تغيير يتطلب فهرسًا بإصدار مستقل")
    parser.add_argument("--context-only", action="store_true", help="جهز الفهرس السياقي وحده دون إضافة الفقرات")
    args = parser.parse_args()
    try:
        if args.runtime:
            atomic_json(MODEL_DIR / "runtime.json", {"runtime": {"onnx": "onnxruntime-cpu-int8-v1", "onnx-portable": "onnxruntime-cpu-int8-rowwise-v3", "torch": "torch-fp32-v1"}[args.runtime]})
        result = semantic_status(args.db, args.directory) if args.command == "status" else download_models() if args.command == "download" else prepare(args.db, args.directory, not args.offline, args.batch_size)
        if args.command == "prepare" and not args.context_only:
            from .semantic_paragraphs import prepare_paragraphs
            paragraphs = prepare_paragraphs(args.db,args.directory,max(args.batch_size,64))
            result = {"context":result,"paragraphs":paragraphs,"status":semantic_status(args.db,args.directory)}
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
