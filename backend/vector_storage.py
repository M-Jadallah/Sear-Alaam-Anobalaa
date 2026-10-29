"""Optional byte vectors with bounded scratch memory and cosine scoring."""
import numpy as np


def quantize_rows(vectors):
    """Keep each row's direction; queries retain float32 precision."""
    values = np.asarray(vectors, dtype=np.float32)
    maximum = np.max(np.abs(values), axis=1, keepdims=True)
    if not np.isfinite(values).all() or np.any(maximum == 0):
        raise ValueError("متجهات فارغة أو غير صالحة؛ لم تحفظ.")
    return np.clip(np.rint(values * (127 / maximum)), -127, 127).astype(np.int8)


def cosine_scores(vectors, query, chunk_size=2048):
    """Never materialize the complete mmap as float32 on a small server."""
    if vectors.dtype == np.float32:
        return vectors @ query
    if vectors.dtype != np.int8 or chunk_size < 1:
        raise ValueError("صيغة المتجهات غير مدعومة.")
    result = np.empty(len(vectors), dtype=np.float32)
    for start in range(0, len(vectors), chunk_size):
        chunk = np.asarray(vectors[start:start + chunk_size], dtype=np.float32)
        norms = np.linalg.norm(chunk, axis=1)
        if np.any(norms == 0):
            raise ValueError("صف صفري في فهرس المتجهات؛ أعد بناءه.")
        result[start:start + len(chunk)] = (chunk @ query) / norms
    return result
