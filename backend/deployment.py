"""Verified bootstrap/backup commands; never overwrite an existing library."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import time

from .db import ROOT, DEFAULT_DB, DEFAULT_SOURCE

TABLES = ("sources", "biographies", "segments", "topic_hits", "person_mentions", "review_log",
          "curation_batches", "curation_batch_members", "curation_diacritics", "reading_revision_batches",
          "reading_revision_history", "pilot_documents", "pilot_evidence", "pilot_events", "pilot_reading_overlays")


def sha256(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def readonly(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=60)


def inventory(path):
    result = {}
    with closing(readonly(path)) as db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in TABLES:
            if table not in tables:
                continue
            digest = hashlib.sha256()
            count = 0
            # Names come only from the fixed allowlist above, never from input.
            for row in db.execute('SELECT * FROM "' + table + '" ORDER BY rowid'):
                digest.update(json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode())
                digest.update(b"\n")
                count += 1
            result[table] = {"rows": count, "sha256": digest.hexdigest()}
    return result


def verify_library(database, source, *, full=True):
    if not Path(database).is_file() or not Path(source).is_file():
        raise ValueError("اختر data/source.mARkdown وقاعدة المكتبة الأصلية؛ البيانات مفقودة.")
    with closing(readonly(database)) as db:
        columns = "sha256,is_synthetic,full_text" if full else "sha256,is_synthetic"
        rows = db.execute("SELECT " + columns + " FROM sources").fetchall()
        if len(rows) != 1 or rows[0][1]:
            raise ValueError("يجب أن تحتوي حزمة الإنتاج مصدرًا حقيقيًا واحدًا.")
        if sha256(source) != rows[0][0]:
            raise ValueError("بصمة ملف المصدر لا تطابق قاعدة المكتبة.")
        if full:
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or db.execute("PRAGMA foreign_key_check").fetchone():
                raise ValueError("فشل فحص سلامة SQLite.")
            text = Path(source).read_bytes().decode("utf-8-sig")
            if rows[0][2] != text:
                raise ValueError("النص المخزن ليس مطابقًا للمصدر حرفيًا.")
        return {"source_sha256": rows[0][0]}


def snapshot(database, destination):
    with closing(readonly(database)) as original, closing(sqlite3.connect(destination)) as target:
        original.backup(target, pages=256, sleep=0.01)


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
        staged = Path(temporary) / "library.sqlite3"
        with gzip.open(zipped, "rb") as src, staged.open("wb") as dst:
            remaining = manifest["database_bytes"]
            while chunk := src.read(min(1024 * 1024, remaining + 1)):
                remaining -= len(chunk)
                if remaining < 0:
                    raise ValueError("حجم الحزمة يتجاوز البيان.")
                dst.write(chunk)
        if remaining != 0 or sha256(staged) != manifest["database_sha256"]:
            raise ValueError("بصمة قاعدة التهيئة غير صالحة.")
        verify_library(staged, source)
        if inventory(staged) != manifest["tables"]:
            raise ValueError("تغيرت الطبقات أو سجلات المراجعة أثناء النقل.")
        # Bootstrap has one writer (Compose init). Never replace a live DB.
        if database.exists():
            raise ValueError("ظهرت قاعدة أثناء التهيئة؛ رفض استبدالها.")
        os.replace(staged, database)
    return {"status": "initialized", "source_sha256": manifest["source_sha256"]}


def backup(database, folder, *, kind="daily"):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    with tempfile.TemporaryDirectory(dir=folder) as temporary:
        staged = Path(temporary) / "library.sqlite3"
        snapshot(database, staged)
        with closing(readonly(staged)) as db:
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("فشل فحص النسخة الاحتياطية.")
        output = folder / f"{Path(database).stem}-{kind}-{stamp}.sqlite3.gz"
        working = output.with_suffix(".tmp")
        with staged.open("rb") as src, gzip.open(working, "wb") as dst:
            shutil.copyfileobj(src, dst, length=1024 * 1024)
        os.replace(working, output)
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
        result = unpack(args.folder, args.db, args.source)
        if result["status"] == "preserved":
            backup(args.db, Path(os.environ.get("SIYAR_BACKUP_DIR", args.db.parent / "backups")), kind="before-migration")
        # Initialization touches schema only in the isolated persistent copy.
        from .db import initialize
        initialize(args.db)
        verify_library(args.db, args.source)
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
