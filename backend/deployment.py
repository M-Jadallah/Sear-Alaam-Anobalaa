"""Verified bootstrap/backup commands; never overwrite an existing library."""
import argparse
import codecs
from contextlib import closing
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import time

from .db import ROOT, DEFAULT_DB, DEFAULT_SOURCE

TABLES = ("sources", "biographies", "segments", "topic_hits", "person_mentions", "review_log",
          "curation_batches", "curation_batch_members", "curation_diacritics", "reading_revision_batches",
          "reading_revision_history", "pilot_documents", "pilot_evidence", "pilot_events", "pilot_reading_overlays")


def _progress(stage):
    if os.environ.get("SIYAR_DEPLOYMENT_DIAGNOSTICS") == "1":
        import resource
        print(json.dumps({"deployment_stage": stage, "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}), file=sys.stderr, flush=True)


def sha256(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def readonly(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=60)


def _flush_disk_cache(stream):
    """Bound dirty page cache charged to the small bootstrap cgroup on Linux."""
    stream.flush()
    os.fsync(stream.fileno())
    if hasattr(os, "posix_fadvise"):
        os.posix_fadvise(stream.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)


class _SQLiteText:
    def __init__(self, db, rowid):
        self.db, self.rowid = db, rowid

    def chunks(self):
        decoder = codecs.getincrementaldecoder("utf-8")()
        with self.db.blobopen("sources", "full_text", self.rowid, readonly=True) as stream:
            while raw := stream.read(65536):
                yield decoder.decode(raw)
            yield decoder.decode(b"", final=True)


def _hash_row(digest, row):
    """Preserve the v1 JSON bytes without duplicating the whole book string."""
    digest.update(b"[")
    for index, value in enumerate(row):
        if index:
            digest.update(b",")
        if isinstance(value, (str, _SQLiteText)):
            digest.update(b'"')
            chunks = value.chunks() if isinstance(value, _SQLiteText) else (value[start:start + 65536] for start in range(0, len(value), 65536))
            for chunk in chunks:
                escaped = json.dumps(chunk, ensure_ascii=False)
                digest.update(escaped[1:-1].encode("utf-8"))
            digest.update(b'"')
        else:
            digest.update(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    digest.update(b"]\n")


def inventory(path):
    result = {}
    with closing(readonly(path)) as db:
        db.execute("BEGIN")
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in TABLES:
            if table not in tables:
                continue
            digest = hashlib.sha256()
            count = 0
            # Names come only from the fixed allowlist above, never from input.
            if table == "sources":
                columns = [item[1] for item in db.execute('PRAGMA table_info("sources")')]
                text_index = columns.index("full_text")
                selection = ",".join("NULL" if name == "full_text" else '"' + name.replace('"', '""') + '"' for name in columns)
                def source_rows():
                    for raw in db.execute('SELECT rowid,' + selection + ' FROM sources ORDER BY rowid'):
                        values = list(raw[1:])
                        values[text_index] = _SQLiteText(db, raw[0])
                        yield values
                rows = source_rows()
            else:
                rows = db.execute('SELECT * FROM "' + table + '" ORDER BY rowid')
            for row in rows:
                _hash_row(digest, row)
                count += 1
            result[table] = {"rows": count, "sha256": digest.hexdigest()}
    return result


def verify_library(database, source, *, full=True):
    if not Path(database).is_file() or not Path(source).is_file():
        raise ValueError("اختر data/source.mARkdown وقاعدة المكتبة الأصلية؛ البيانات مفقودة.")
    with closing(readonly(database)) as db:
        rows = db.execute("SELECT sha256,is_synthetic,rowid FROM sources").fetchall()
        if len(rows) != 1 or rows[0][1]:
            raise ValueError("يجب أن تحتوي حزمة الإنتاج مصدرًا حقيقيًا واحدًا.")
        if sha256(source) != rows[0][0]:
            raise ValueError("بصمة ملف المصدر لا تطابق قاعدة المكتبة.")
        if full:
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or db.execute("PRAGMA foreign_key_check").fetchone():
                raise ValueError("فشل فحص سلامة SQLite.")
            # Incremental BLOB I/O also accepts SQLite TEXT and returns its UTF-8
            # bytes. Compare without creating several whole-book strings.
            with db.blobopen("sources", "full_text", rows[0][2], readonly=True) as stored, Path(source).open("rb") as original:
                if original.read(3) != b"\xef\xbb\xbf":
                    original.seek(0)
                while chunk := original.read(65536):
                    if stored.read(len(chunk)) != chunk:
                        raise ValueError("النص المخزن ليس مطابقًا للمصدر حرفيًا.")
                if stored.read(1):
                    raise ValueError("النص المخزن ليس مطابقًا للمصدر حرفيًا.")
        return {"source_sha256": rows[0][0]}


def snapshot(database, destination):
    deadline = time.monotonic() + 120
    flushed_pages = 0
    def progress(status, remaining, total):
        nonlocal flushed_pages
        if status not in (sqlite3.SQLITE_OK, sqlite3.SQLITE_DONE, sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
            raise RuntimeError(f"SQLite backup failed with status {status}")
        if time.monotonic() > deadline:
            raise TimeoutError("SQLite backup did not complete within 120 seconds")
        copied = total - remaining
        if os.name == "posix" and (copied - flushed_pages) * page_size >= 16 * 1024 * 1024:
            _flush_disk_cache(staged)
            flushed_pages = copied
    with closing(readonly(database)) as original, closing(sqlite3.connect(destination)) as target:
        page_size = original.execute("PRAGMA page_size").fetchone()[0]
        with Path(destination).open("rb") as staged:
            original.backup(target, pages=256, sleep=0.01, progress=progress)


def package(database, source, folder):
    verify_library(database, source)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    if (folder / "manifest.json").exists():
        raise ValueError("حزمة التهيئة موجودة؛ اختر مجلد إصدار جديدًا.")
    with tempfile.TemporaryDirectory() as temporary:
        copied = Path(temporary) / "library.sqlite3"
        snapshot(database, copied)
        details = verify_library(copied, source)
        details.update({"format": "siyar-seed-v1", "database_sha256": sha256(copied),
                        "database_bytes": copied.stat().st_size, "tables": inventory(copied), "parts": []})
        compressed = Path(temporary) / "library.sqlite3.gz"
        with copied.open("rb") as src, compressed.open("wb") as dst:
            with gzip.GzipFile(fileobj=dst, mode="wb", mtime=0) as zipped:
                shutil.copyfileobj(src, zipped, length=1024 * 1024)
        with compressed.open("rb") as stream:
            number = 0
            while chunk := stream.read(20 * 1024 * 1024):
                part = folder / f"library.sqlite3.gz.part{number:03d}"
                part.write_bytes(chunk)
                details["parts"].append({"name": part.name, "size": len(chunk), "sha256": sha256(part)})
                number += 1
        (folder / "manifest.json").write_text(json.dumps(details, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return details


def unpack(folder, database, source):
    folder, database = Path(folder), Path(database)
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("format") != "siyar-seed-v1" or manifest["source_sha256"] != sha256(source):
        raise ValueError("حزمة التهيئة أو بصمة المصدر غير صالحة.")
    if database.exists():
        verify_library(database, source)
        return {"status": "preserved", "source_sha256": manifest["source_sha256"]}
    database.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=database.parent) as temporary:
        zipped = Path(temporary) / "seed.gz"
        with zipped.open("wb") as target:
            for item in manifest["parts"]:
                name = item["name"]
                if Path(name).name != name or "/" in name or "\\" in name:
                    raise ValueError("مسار جزء الحزمة غير آمن.")
                path = folder / name
                if path.stat().st_size != item["size"] or sha256(path) != item["sha256"]:
                    raise ValueError("جزء حزمة تالف: " + name)
                with path.open("rb") as stream:
                    shutil.copyfileobj(stream, target, length=1024 * 1024)
                _flush_disk_cache(target)
        staged = Path(temporary) / "library.sqlite3"
        _progress("seed_parts_verified")
        with gzip.open(zipped, "rb") as src, staged.open("wb") as dst:
            remaining = manifest["database_bytes"]
            pending_flush = 0
            while chunk := src.read(min(1024 * 1024, remaining + 1)):
                remaining -= len(chunk)
                if remaining < 0:
                    raise ValueError("حجم الحزمة يتجاوز البيان.")
                dst.write(chunk)
                pending_flush += len(chunk)
                if pending_flush >= 16 * 1024 * 1024:
                    _flush_disk_cache(dst)
                    pending_flush = 0
            _flush_disk_cache(dst)
        if remaining != 0 or sha256(staged) != manifest["database_sha256"]:
            raise ValueError("بصمة قاعدة التهيئة غير صالحة.")
        _progress("seed_database_verified")
        verify_library(staged, source)
        _progress("seed_source_verified")
        if inventory(staged) != manifest["tables"]:
            raise ValueError("تغيرت الطبقات أو سجلات المراجعة أثناء النقل.")
        _progress("seed_inventory_verified")
        # Bootstrap has one writer (Compose init). Never replace a live DB.
        if database.exists():
            raise ValueError("ظهرت قاعدة أثناء التهيئة؛ رفض استبدالها.")
        os.replace(staged, database)
    return {"status": "initialized", "source_sha256": manifest["source_sha256"]}


def backup(database, folder, *, kind="daily"):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    # Coordinate periodic and manual backups across processes. The lock is a
    # separate tiny SQLite database, never a write transaction on the library.
    with closing(sqlite3.connect(folder / ".backup-coordination.sqlite3", timeout=120)) as lock:
        lock.execute("BEGIN IMMEDIATE")
        return _backup_locked(database, folder, kind=kind)


def _backup_locked(database, folder, *, kind):
    _progress("backup_snapshot_started")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    with tempfile.TemporaryDirectory(dir=folder) as temporary:
        staged = Path(temporary) / "library.sqlite3"
        snapshot(database, staged)
        _progress("backup_snapshot_complete")
        with closing(readonly(staged)) as db:
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("فشل فحص النسخة الاحتياطية.")
        output = folder / f"{Path(database).stem}-{kind}-{stamp}.sqlite3.gz"
        working = output.with_suffix(".tmp")
        # Explicit level is stable across Python versions (3.14 changed the
        # gzip default). Runtime backups share only 0.1 CPU on the VPS.
        with staged.open("rb") as src, gzip.open(working, "wb", compresslevel=3) as dst:
            shutil.copyfileobj(src, dst, length=1024 * 1024)
        os.replace(working, output)
        _progress("backup_compression_complete")
    previous = sorted(folder.glob(f"{Path(database).stem}-{kind}-*.sqlite3.gz"), reverse=True)
    for old in previous[3 if kind == "daily" else 1:]:
        old.unlink()
    return output


def restore(archive, destination):
    destination = Path(destination)
    if destination.exists():
        raise ValueError("الاستعادة إلى ملف جديد فقط؛ لا تستبدل قاعدة حية.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".restore-tmp")
    try:
        with gzip.open(archive, "rb") as src, temporary.open("xb") as dst:
            shutil.copyfileobj(src, dst, length=1024 * 1024)
        with closing(readonly(temporary)) as db:
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("النسخة المستعادة غير سليمة.")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("package", "init", "verify", "backup", "backup-loop", "restore"))
    parser.add_argument("--db", type=Path, default=Path(os.environ.get("SIYAR_DB", DEFAULT_DB)))
    parser.add_argument("--source", type=Path, default=Path(os.environ.get("SIYAR_SOURCE", DEFAULT_SOURCE)))
    parser.add_argument("--folder", type=Path, default=ROOT / "deploy" / "seed")
    parser.add_argument("--archive", type=Path)
    args = parser.parse_args()
    if args.command == "package":
        result = package(args.db, args.source, args.folder)
    elif args.command == "init":
        _progress("init_started")
        result = unpack(args.folder, args.db, args.source)
        _progress("unpack_complete")
        if result["status"] == "preserved":
            backup(args.db, Path(os.environ.get("SIYAR_BACKUP_DIR", args.db.parent / "backups")), kind="before-migration")
        # Initialization touches schema only in the isolated persistent copy.
        from .db import initialize
        initialize(args.db)
        _progress("schema_complete")
        verify_library(args.db, args.source)
        _progress("init_complete")
    elif args.command == "verify":
        result = verify_library(args.db, args.source)
    elif args.command == "restore":
        if not args.archive:
            parser.error("--archive is required")
        restore(args.archive, args.db)
        result = {"restored": str(args.db)}
    else:
        while True:
            result = {"backup": str(backup(args.db, args.folder))}
            auth = Path(os.environ.get("SIYAR_AUTH_DB", args.db.parent / "auth.sqlite3"))
            if auth.is_file():
                result["auth_backup"] = str(backup(auth, args.folder))
            print(json.dumps(result, ensure_ascii=False), flush=True)
            if args.command != "backup-loop":
                return
            time.sleep(24 * 3600)
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
