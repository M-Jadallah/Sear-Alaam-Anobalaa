import hashlib
import importlib.util
import json
import sqlite3
from collections import Counter

import pytest

from conftest import ROOT


def evaluation_module():
    spec = importlib.util.spec_from_file_location("siyar_evaluation", ROOT / "scripts" / "evaluate_semantic.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_gold_has_30_inspected_cases_with_required_distribution_and_exact_source_ranges():
    path = ROOT / "fixtures" / "semantic_evaluation.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == len({row["id"] for row in rows}) == 30
    assert Counter(row["category"] for row in rows) == {"paraphrase": 15, "negation_or_alternative": 5, "name_variant": 5, "outside_dictionary": 5}
    source = ROOT / "data" / "source.mARkdown"
    if not source.is_file():
        pytest.skip("The real-source gold set requires the corresponding source file")
    raw = source.read_bytes()
    original = raw.decode("utf-8")
    sha = hashlib.sha256(raw).hexdigest()
    for row in rows:
        assert row["source_sha256"] == sha
        assert row["judgment_provenance"] and row["judgment_note"]
        assert row["expected"]
        for passage in row["expected"]:
            assert original[passage["source_char_start"]:passage["source_char_end"]] == passage["evidence_text"]
            assert passage["page_reference_status"] == "raw_unverified"


def test_known_hit_requires_source_section_and_substantive_overlap_not_just_nearby_offsets():
    module = evaluation_module()
    expected = [{"source_id": 1, "section_id": 2, "source_char_start": 100, "source_char_end": 200}]
    assert module.first_known_hit([{"source_id": 1, "section_id": 2, "source_char_start": 80, "source_char_end": 160}], expected)["rank"] == 1
    assert module.first_known_hit([{"source_id": 1, "section_id": 2, "source_char_start": 50, "source_char_end": 105}], expected) is None
    assert module.first_known_hit([{"source_id": 2, "section_id": 2, "source_char_start": 100, "source_char_end": 200}], expected) is None
    assert module.first_known_hit([{"source_id": 1, "section_id": 9, "source_char_start": 100, "source_char_end": 200}], expected) is None
    assert module.first_known_hit([{"source_id": 1, "section_id": 2, "source_char_start": 100, "source_char_end": 200}] * 11, expected, 0) is None


def test_percentile_is_explicit_for_small_sets():
    module = evaluation_module()
    assert module.percentile([]) is None
    assert module.percentile([4]) == 4
    assert module.percentile([0, 100]) == 95


@pytest.mark.parametrize("mutation", [None, "wrong_section", "wrong_biography", "missing_biography", "section_bounds", "biography_bounds"])
def test_gold_loader_checks_synthetic_section_and_biography_identity_and_bounds(book, db, tmp_path, mutation):
    module = evaluation_module()
    segment = db.execute("SELECT * FROM segments WHERE biography_id IS NOT NULL ORDER BY id LIMIT 1").fetchone()
    source = db.execute("SELECT sha256 FROM sources WHERE id=?", (segment["source_id"],)).fetchone()
    passage = {key: segment[key] for key in ("source_id", "section_id", "biography_id", "source_char_start", "source_char_end")}
    passage["evidence_text"] = segment["text_original"]
    case = {"id": "synthetic-gold", "source_sha256": source["sha256"], "expected": [passage]}
    if mutation == "wrong_section":
        passage["section_id"] = db.execute("SELECT id FROM source_sections WHERE id!=? ORDER BY id LIMIT 1", (passage["section_id"],)).fetchone()[0]
    elif mutation == "wrong_biography":
        passage["biography_id"] = db.execute("SELECT id FROM biographies WHERE id!=? ORDER BY id LIMIT 1", (passage["biography_id"],)).fetchone()[0]
    elif mutation == "missing_biography":
        passage["biography_id"] = None
    elif mutation == "section_bounds":
        db.execute("UPDATE source_sections SET source_char_start=? WHERE id=?", (passage["source_char_start"] + 1, passage["section_id"]))
    elif mutation == "biography_bounds":
        db.execute("UPDATE biographies SET source_char_start=? WHERE id=?", (passage["source_char_start"] + 1, passage["biography_id"]))
    path = tmp_path / "synthetic-gold.jsonl"
    path.write_text(json.dumps(case, ensure_ascii=False) + "\n", encoding="utf-8")
    if mutation is None:
        assert module.load_verified_gold(path, db) == [case]
    else:
        with pytest.raises(ValueError, match="Gold (section|biography)"):
            module.load_verified_gold(path, db)
