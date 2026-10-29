"""Transport tests use clearly labelled synthetic data in temporary paths only."""
import json
import hashlib
import sqlite3
from contextlib import closing

import pytest
from backend.deployment import backup, inventory, package, restore, sha256, unpack, verify_library


def test_inventory_hash_is_compatible_and_streams_large_unicode_strings(monkeypatch):
    from backend import deployment
    value = ('عَرَبِيٌّ 🧪 " \\ \r\n\t\x00' * 12000) + 'خاتمة مصطنعة للاختبار'
    row = (1, None, 2.5, value, "")
    dumps = json.dumps
    expected = hashlib.sha256(dumps(row, ensure_ascii=False, separators=(",", ":")).encode() + b"\n").hexdigest()
    sizes = []
    def bounded(value, *args, **kwargs):
        if isinstance(value, str):
            sizes.append(len(value))
            assert len(value) <= 65536
        return dumps(value, *args, **kwargs)
    monkeypatch.setattr(deployment.json, "dumps", bounded)
    digest = hashlib.sha256()
    deployment._hash_row(digest, row)
    assert digest.hexdigest() == expected
    assert len(sizes) > 1


def test_production_verification_rejects_synthetic_or_missing_source(book):
    with pytest.raises(ValueError, match="حقيقي"):
        verify_library(book["database"], book["source"])
    with pytest.raises(ValueError, match="مفقودة"):
        verify_library(book["database"], book["source"].with_name("missing"))


@pytest.fixture
def transport(book):
    # Simulate the production eligibility flag solely to exercise the archive
    # format. Text and fixture naming remain explicitly synthetic throughout.
    with closing(sqlite3.connect(book["database"])) as db, db:
        db.execute("UPDATE sources SET is_synthetic=0")
    return book


def test_seed_preserves_all_rows_and_never_overwrites_later_edits(transport, tmp_path):
    folder = tmp_path / "seed"
    manifest = package(transport["database"], transport["source"], folder)
    target = tmp_path / "production.sqlite3"
    assert unpack(folder, target, transport["source"])["status"] == "initialized"
    assert inventory(target) == manifest["tables"]
    with closing(sqlite3.connect(target)) as db, db:
        db.execute("INSERT INTO review_log(entity_type,entity_id,change_summary,reviewer) VALUES ('test',1,'synthetic','tester')")
    before = inventory(target)
    assert unpack(folder, target, transport["source"])["status"] == "preserved"
    assert inventory(target) == before
    assert all(item["size"] <= 20 * 1024 * 1024 for item in manifest["parts"])


def test_corrupt_seed_rejected_without_publishing_database(transport, tmp_path):
    folder = tmp_path / "seed"
    manifest = package(transport["database"], transport["source"], folder)
    part = folder / manifest["parts"][0]["name"]
    part.write_bytes(b"corrupt")
    target = tmp_path / "production.sqlite3"
    with pytest.raises(ValueError, match="تالف"):
        unpack(folder, target, transport["source"])
    assert not target.exists()


def test_live_wal_backup_restore_and_retention(book, tmp_path):
    folder = tmp_path / "backups"
    for _ in range(4):
        archive = backup(book["database"], folder)
    assert len(list(folder.glob("*.gz"))) == 3
    restored = tmp_path / "restored.sqlite3"
    restore(archive, restored)
    assert inventory(restored) == inventory(book["database"])
    with pytest.raises(ValueError, match="حيّة|حية"):
        restore(archive, restored)
