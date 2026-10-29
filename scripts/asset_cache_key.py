"""Cache derived assets by their inputs, independently of API-only fixes."""
from pathlib import Path
import argparse
from contextlib import closing
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def asset_key(db_path):
    from backend.semantic import index_configuration, canonical_connection, file_sha256, _json_hash
    from backend.semantic_onnx import PORTABLE_RUNTIME
    from backend.semantic_paragraphs import PARAGRAPH_VERSION
    from backend.morphology import VERSION
    configuration = index_configuration(db_path)
    configuration["runtime"] = PORTABLE_RUNTIME
    with closing(canonical_connection(db_path)) as db:
        segmentation = db.execute("SELECT COALESCE(MAX(id),0) FROM review_log WHERE entity_type='segment'").fetchone()[0]
    return _json_hash({"configuration": configuration, "paragraph_version": PARAGRAPH_VERSION,
        "segmentation_revision": segmentation, "morphology_version": VERSION,
        "requirements": file_sha256(ROOT / "requirements-production.txt"),
        "build_tools": {"torch": "2.14.1", "transformers": "5.21.0", "onnx": "1.23.2"}})


if __name__ == "__main__":
    from backend.semantic import DEFAULT_DB
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    print("key=" + asset_key(args.db))
