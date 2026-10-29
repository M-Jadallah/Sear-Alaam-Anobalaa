"""Disposable lexical stemming index; it never changes source text or labels."""
from functools import lru_cache
from contextlib import closing, ExitStack
import json
from pathlib import Path
import sqlite3

VERSION = "snowball-isri-fts-v1"
_VERIFIED = set()


@lru_cache(maxsize=2)
def _stemmer(method):
    if method == "stem":
        from nltk.stem.snowball import ArabicStemmer
        return ArabicStemmer()
    from nltk.stem.isri import ISRIStemmer
    return ISRIStemmer()


@lru_cache(maxsize=131072)
def stem_word(word, method):
    return _stemmer(method).stem(word)


def transform(text, method):
    from .normalization import normalize_arabic
    return " ".join(stem_word(word, method) for word in normalize_arabic(text).split() if len(word) >= 3)


def _configuration(status):
    import importlib.metadata
    return {"version": VERSION, "nltk": importlib.metadata.version("nltk"),
            "context": status.get("base_index_fingerprint", status["fingerprint"]),
            "paragraphs": status["paragraph_status"]["fingerprint"],
            "model": status["model_fingerprint"]}


def prepare(status):
    from .semantic import _json_hash, file_sha256, atomic_json
    folder = Path(status["paragraph_status"]["directory"])
    configuration = _configuration(status)
    fingerprint = _json_hash(configuration)
    target = folder / ("morphology-" + fingerprint[:20] + ".sqlite3")
    manifest_path = folder / "morphology.json"
    if manifest_path.is_file():
        saved = json.loads(manifest_path.read_text(encoding="utf-8"))
        if saved.get("configuration") == configuration and target.is_file() and file_sha256(target) == saved.get("sha256"):
            return saved
    temporary = target.with_suffix(".building.sqlite3")
    temporary.unlink(missing_ok=True)
    with closing(sqlite3.connect(temporary)) as db, db:
        db.executescript("""
            CREATE TABLE items(id INTEGER PRIMARY KEY,kind TEXT NOT NULL,window_id INTEGER NOT NULL,biography_id INTEGER);
            CREATE VIRTUAL TABLE stems USING fts5(text,roots);
        """)
        count = 0
        for kind, path in [("context", Path(status["directory"])), ("paragraph", folder)]:
            with closing(sqlite3.connect(path / "windows.sqlite3")) as source:
                for identifier, biography, text in source.execute("SELECT id,biography_id,clean_text FROM windows ORDER BY id"):
                    count += 1
                    db.execute("INSERT INTO items VALUES (?,?,?,?)", (count, kind, identifier, biography))
                    db.execute("INSERT INTO stems(rowid,text,roots) VALUES (?,?,?)", (count, transform(text, "stem"), transform(text, "root")))
                    if count % 10000 == 0:
                        db.commit()
        db.commit()
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("فشل فحص فهرس الاشتقاق اللفظي.")
    temporary.replace(target)
    manifest = {"configuration": configuration, "file": target.name, "sha256": file_sha256(target), "rows": count}
    atomic_json(manifest_path, manifest)
    stem_word.cache_clear()
    return manifest


def candidates(query, status, limit=100):
    from .semantic import file_sha256, _json_hash
    from .smart_interpret import query_terms
    folder = Path(status["paragraph_status"]["directory"])
    manifest_path = folder / "morphology.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    configuration = _configuration(status)
    expected = "morphology-" + _json_hash(configuration)[:20] + ".sqlite3"
    if manifest.get("configuration") != configuration or manifest.get("file") != expected:
        raise RuntimeError("فهرس الاشتقاق لا يطابق المصدر أو النماذج.")
    target = folder / expected
    key = (str(target), target.stat().st_size, target.stat().st_mtime_ns, manifest["sha256"])
    if key not in _VERIFIED:
        if file_sha256(target) != manifest["sha256"]:
            raise RuntimeError("فشلت بصمة فهرس الاشتقاق اللفظي.")
        _VERIFIED.clear(); _VERIFIED.add(key)
    results = {}
    scope = status.get("pilot_biography_ids")
    if scope == []:
        return []
    with ExitStack() as stack:
        db = stack.enter_context(closing(sqlite3.connect(target.resolve().as_uri() + "?mode=ro", uri=True)))
        sources = {}
        for kind, path in [("context", Path(status["directory"])), ("paragraph", folder)]:
            source = stack.enter_context(closing(sqlite3.connect((path / "windows.sqlite3").resolve().as_uri() + "?mode=ro", uri=True)))
            source.row_factory = sqlite3.Row
            sources[kind] = source
        for method, column in [("stem", "text"), ("root", "roots")]:
            terms = list(dict.fromkeys(stem_word(word, method) for word in query_terms(query) if len(word) >= 3))
            if not terms:
                continue
            expression = column + " : (" + " OR ".join('"' + term.replace('"', '""') + '"' for term in terms) + ")"
            clause = " AND i.biography_id IN (" + ",".join("?" for _ in scope) + ")" if scope is not None else ""
            matches = db.execute("SELECT i.kind,i.window_id FROM stems JOIN items i ON i.id=stems.rowid WHERE stems MATCH ?" + clause + " ORDER BY bm25(stems) LIMIT ?", (expression, *(scope or []), limit)).fetchall()
            for rank, (kind, identifier) in enumerate(matches, 1):
                found = sources[kind].execute("SELECT * FROM windows WHERE id=?", (identifier,)).fetchone()
                if not found:
                    raise RuntimeError("إحالة اشتقاق خارج فهرس المصدر.")
                row = dict(found)
                key = (row["source_id"], row["source_char_start"], row["source_char_end"])
                item = results.setdefault(key, {**row, "granularity": kind, "morphology_score": 0.0})
                item["morphology_score"] += 1 / (60 + rank)
    return sorted(results.values(), key=lambda row: row["morphology_score"], reverse=True)
