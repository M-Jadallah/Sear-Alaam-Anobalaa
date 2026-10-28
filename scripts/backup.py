"""Create a consistent SQLite backup without altering the working database."""
from datetime import datetime, timezone
from pathlib import Path
from contextlib import closing
import argparse
import sqlite3

ROOT = Path(__file__).resolve().parents[1]

def main():
    parser = argparse.ArgumentParser(description="نسخة احتياطية متسقة لقاعدة المراجعات")
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "library.sqlite3")
    args = parser.parse_args()
    if not args.db.is_file():
        parser.exit(1, "قاعدة البيانات غير موجودة؛ شغّل الاستيراد أولًا.\n")
    folder = args.db.parent / "backups"
    folder.mkdir(exist_ok=True)
    output = folder / f"{args.db.stem}-{datetime.now(timezone.utc):%Y%m%dT%H%M%S%fZ}.sqlite3"
    with closing(sqlite3.connect(args.db.resolve().as_uri() + "?mode=ro", uri=True)) as source:
        with closing(sqlite3.connect(output)) as target:
            source.backup(target)
    print(output)

if __name__ == "__main__":
    main()
