from pathlib import Path
import csv
import sqlite3
from .normalization import normalize_arabic

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "library.sqlite3"
DEFAULT_SOURCE = ROOT / "data" / "source.mARkdown"
DEFAULT_DICTIONARY = ROOT / "reference" / "topic_dictionary.csv"


def connect(db_path=DEFAULT_DB) -> sqlite3.Connection:
    connection = sqlite3.connect(str(db_path), timeout=60)
    connection.row_factory = sqlite3.Row
    connection.create_function("arabic_normalize", 1, normalize_arabic, deterministic=True)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=60000")
    return connection


def import_dictionary(connection, dictionary_path=DEFAULT_DICTIONARY):
    with Path(dictionary_path).open(encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            category = connection.execute("SELECT id FROM topics WHERE parent_id IS NULL AND name=?", (row["الباب"],)).fetchone()
            if category is None:
                category_id = connection.execute("INSERT INTO topics(name) VALUES (?)", (row["الباب"],)).lastrowid
            else:
                category_id = category["id"]
            connection.execute("INSERT INTO topics(parent_id,name,search_terms_csv,dictionary_status) VALUES (?,?,?,?) ON CONFLICT(parent_id,name) DO UPDATE SET search_terms_csv=excluded.search_terms_csv,dictionary_status=excluded.dictionary_status",
                               (category_id, row["الموضوع"], row["الألفاظ المقترحة"], row["حالة التوثيق"]))


def initialize(db_path=DEFAULT_DB, dictionary_path=DEFAULT_DICTIONARY):
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript((ROOT / "db" / "schema.sql").read_text(encoding="utf-8-sig"))
        if dictionary_path and Path(dictionary_path).exists():
            import_dictionary(connection, dictionary_path)
