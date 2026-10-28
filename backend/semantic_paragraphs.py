"""Source-wide short-passage supplement to the contextual retrieval index.

All canonical paragraphs are included with the same pinned encoder. The complete
context index remains usable until this independent supplement is atomically
published. No gold-query-specific passage, expansion, or model is used.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sqlite3
import time

from . import semantic as base
from .normalization import normalize_arabic

PARAGRAPH_VERSION = "all-canonical-paragraphs-v2"


def _directory(semantic_dir=None):
    return Path(semantic_dir or os.environ.get("SIYAR_SEMANTIC_DIR", base.DEFAULT_SEMANTIC_DIR))


def paragraph_configuration(db_path, base_status):
    with base.canonical_connection(db_path) as db:
        segment_revision=db.execute("SELECT COALESCE(MAX(id),0) FROM review_log WHERE entity_type='segment'").fetchone()[0]
    return {"version": PARAGRAPH_VERSION, "base_index_fingerprint": base_status.get("base_index_fingerprint", base_status["fingerprint"]),
            "configuration": base.index_configuration(db_path), "model_fingerprint": base_status["model_fingerprint"],
            "segmentation_review_revision":segment_revision}


def paragraph_status(db_path=base.DEFAULT_DB, semantic_dir=None, base_status=None):
    directory = _directory(semantic_dir)
    base_status = base_status or base.semantic_status(db_path, directory, include_paragraphs=False)
    missing = {"ready": False, "status": "missing", "reason": "فهرس الفقرات الإضافي غير مجهز؛ الفهرس السياقي يبقى متاحًا."}
    if not base_status.get("ready"):
        return {**missing, "status": "stale", "reason": "يلزم فهرس سياقي صالح قبل استخدام فهرس الفقرات."}
    pointer = directory / "paragraphs-active.json"
    progress = None
    try:
        if (directory / "paragraphs-building.json").is_file():
            progress = json.loads((directory / "paragraphs-building.json").read_text(encoding="utf-8"))
        if not pointer.is_file():
            return {**missing, "status": "building" if progress and time.time()-progress.get("updated_at",0)<180 else "missing", "progress": progress}
        active = json.loads(pointer.read_text(encoding="utf-8"))
        relative = Path(active["directory"])
        version = (directory / relative).resolve()
        if relative.is_absolute() or ".." in relative.parts or version.parent != directory.resolve() or not relative.name.startswith("paragraphs-"):
            raise RuntimeError("مسار فهرس الفقرات غير آمن.")
        manifest = json.loads((version / "manifest.json").read_text(encoding="utf-8"))
        if not manifest.get("complete") or manifest.get("configuration") != paragraph_configuration(db_path, base_status):
            return {**missing, "status": "stale", "reason": "فهرس الفقرات لا يطابق المصدر أو النموذج أو الفهرس السياقي."}
        if not (version / "vectors.npy").is_file() or not (version / "windows.sqlite3").is_file():
            return {**missing, "status": "stale", "reason": "ملفات فهرس الفقرات غير مكتملة."}
        return {"ready": True, "status": "ready", "directory": str(version), "fingerprint": manifest["fingerprint"],
                "window_count": manifest["window_count"], "canonical_segment_count": manifest["canonical_segment_count"],
                "reason": "فهرس جميع الفقرات جاهز مع نطاقات المصدر الأصلية.", "progress": progress}
    except (ValueError, OSError, KeyError, RuntimeError) as error:
        return {**missing, "status": "stale", "reason": str(error)}


def enrich_status(db_path, semantic_dir, status):
    paragraph = paragraph_status(db_path, semantic_dir, status)
    combined = dict(status, paragraph_status=paragraph, base_index_fingerprint=status["fingerprint"],
                    semantic_granularity="context_and_paragraph" if paragraph.get("ready") else "context_only")
    if paragraph.get("ready"):
        combined["fingerprint"] = base._json_hash({"base": status["fingerprint"], "paragraphs": paragraph["fingerprint"]})
        combined["paragraph_window_count"] = paragraph["window_count"]
        combined["dense_allocation"] = {"context": 50, "paragraph": 50, "maximum_total": 100}
    return combined


def prepare_paragraphs(db_path=base.DEFAULT_DB, semantic_dir=None, batch_size=64):
    import numpy as np
    from .reading import reading_plain_with_map
    if not 1 <= batch_size <= 128:
        raise ValueError("حجم الدفعة يجب أن يقع بين 1 و128.")
    directory = _directory(semantic_dir)
    directory.mkdir(parents=True, exist_ok=True)
    status = base.semantic_status(db_path, directory, include_paragraphs=False)
    if not status.get("ready"):
        raise RuntimeError("جهز الفهرس السياقي الكامل أولًا؛ لم يستخدم فهرس جزئي.")
    configuration = paragraph_configuration(db_path, status)
    fingerprint = base._json_hash(configuration)
    work = directory / ("paragraphs-" + fingerprint[:20])
    work.mkdir(exist_ok=True)
    lock = directory / "paragraphs-prepare.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise RuntimeError("يوجد تجهيز فقرات آخر؛ تحقق من العملية قبل إزالة القفل.")
    os.write(fd, str(os.getpid()).encode()); os.close(fd)
    def progress(stage, **values):
        item = {"stage": stage, "fingerprint": fingerprint, "updated_at": time.time(), **values}
        base.atomic_json(directory / "paragraphs-building.json", item)
        print(json.dumps(item, ensure_ascii=False), flush=True)
    try:
        existing = paragraph_status(db_path, directory, status)
        if existing.get("ready") and existing["fingerprint"] == fingerprint:
            manifest = json.loads((Path(existing["directory"]) / "manifest.json").read_text(encoding="utf-8"))
            if base.file_sha256(work / "vectors.npy") != manifest["vectors_sha256"] or base.file_sha256(work / "windows.sqlite3") != manifest["windows_sha256"]:
                raise RuntimeError("فشل تحقق فهرس الفقرات المنشور؛ لم تعدل الملفات.")
            return {**manifest, "unchanged": True}
        progress("paragraph_models")
        models = base.verify_model_files()
        if models["fingerprint"] != status["model_fingerprint"]:
            raise RuntimeError("بصمة النموذج لا تطابق الفهرس السياقي.")
        embed, _ = base.load_models(False)
        sidecar = work / "windows.sqlite3"
        db = sqlite3.connect(sidecar); db.row_factory = sqlite3.Row
        db.executescript(Path(__file__).with_name("semantic_schema.sql").read_text(encoding="utf-8"))
        done = db.execute("SELECT value FROM metadata WHERE key='windows_complete'").fetchone()
        canonical_count = 0
        if not done:
            db.execute("DELETE FROM windows"); db.execute("DELETE FROM windows_fts")
            with base.canonical_connection(db_path) as source:
                source_rows = source.execute("SELECT s.*,src.sha256,COALESCE(b.heading,sc.heading) heading FROM segments s JOIN sources src ON src.id=s.source_id JOIN source_sections sc ON sc.id=s.section_id LEFT JOIN biographies b ON b.id=s.biography_id ORDER BY s.id")
                count = 0
                for segment in source_rows:
                    canonical_count += 1
                    clean, positions = reading_plain_with_map(segment["text_original"], segment["source_char_start"], starts_at_line_start=True)
                    # Hidden terminal page markers can leave one formatting
                    # space. Exclude that derived whitespace from tokenization
                    # so the final paragraph is never silently omitted.
                    clean = clean.rstrip()
                    positions = positions[:len(clean)]
                    for window in base.token_windows(clean, positions, embed.tokenizer):
                        length = len(embed.tokenizer("passage: " + window["clean_text"], add_special_tokens=True, truncation=False)["input_ids"])
                        if length > 512:
                            raise RuntimeError("فقرة تتجاوز حد النموذج؛ لم تبتر.")
                        count += 1
                        db.execute("INSERT INTO windows(id,source_id,source_sha256,section_id,biography_id,heading,source_char_start,source_char_end,clean_text,token_count) VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (count,segment["source_id"],segment["sha256"],segment["section_id"],segment["biography_id"],segment["heading"],window["source_char_start"],window["source_char_end"],window["clean_text"],window["token_count"]))
                        db.execute("INSERT INTO windows_fts(window_id,normalized_text) VALUES (?,?)",(count,normalize_arabic(window["clean_text"])))
                    if canonical_count % 5000 == 0:
                        db.commit(); progress("paragraph_windows", segments=canonical_count, windows=count)
            db.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES ('canonical_segment_count',?)", (str(canonical_count),))
            db.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES ('windows_complete','1')"); db.commit()
        else:
            canonical_count = int(db.execute("SELECT value FROM metadata WHERE key='canonical_segment_count'").fetchone()[0])
        count = db.execute("SELECT COUNT(*) FROM windows").fetchone()[0]
        order = [row[0]-1 for row in db.execute("SELECT id FROM windows ORDER BY token_count,id")]
        order_fp = base._json_hash(order)
        checkpoint = work / "checkpoint.json"
        checkpoint_state = json.loads(checkpoint.read_text(encoding="utf-8")) if checkpoint.exists() else {}
        cursor = checkpoint_state.get("encoded", 0)
        if checkpoint_state and (checkpoint_state.get("fingerprint") != fingerprint or checkpoint_state.get("order_fingerprint") != order_fp or checkpoint_state.get("model_fingerprint") != models["fingerprint"] or not isinstance(cursor,int) or not 0<=cursor<=count):
            raise RuntimeError("نقطة استئناف الفقرات لا تطابق المصدر أو النموذج أو ترتيب التضمين.")
        vector_path = work / "vectors.npy"
        if not vector_path.exists(): cursor = 0
        vectors = np.lib.format.open_memmap(vector_path,mode="r+" if vector_path.exists() else "w+",dtype="float32",shape=(count,base.DIMENSION))
        progress("paragraph_encode", encoded=cursor, windows=count)
        for begin in range(cursor,count,batch_size):
            indices=order[begin:begin+batch_size]
            texts=[db.execute("SELECT clean_text FROM windows WHERE id=?",(index+1,)).fetchone()[0] for index in indices]
            embeddings=embed.encode(["passage: "+text for text in texts],batch_size=batch_size,normalize_embeddings=True,show_progress_bar=False,convert_to_numpy=True)
            if embeddings.shape!=(len(indices),base.DIMENSION) or not np.isfinite(embeddings).all():raise RuntimeError("تضمين فقرة غير صالح.")
            vectors[indices]=embeddings;vectors.flush()
            base.atomic_json(checkpoint,{"encoded":begin+len(indices),"fingerprint":fingerprint,"model_fingerprint":models["fingerprint"],"order_fingerprint":order_fp})
            if begin % (batch_size*30)==0 or begin+len(indices)==count:progress("paragraph_encode",encoded=begin+len(indices),windows=count)
        for begin in range(0,count,4096):
            chunk=vectors[begin:begin+4096];norms=np.linalg.norm(chunk,axis=1)
            if not np.isfinite(chunk).all() or not np.all((norms>0.99)&(norms<1.01)):raise RuntimeError("متجهات فقرات ناقصة؛ لم ينشر الفهرس.")
        del vectors;db.close()
        latest=base.semantic_status(db_path,directory,include_paragraphs=False)
        if not latest.get("ready") or configuration!=paragraph_configuration(db_path,latest):raise RuntimeError("تغير المصدر أو الفهرس السياقي أثناء تجهيز الفقرات.")
        manifest={"configuration":configuration,"fingerprint":fingerprint,"complete":True,"window_count":count,"canonical_segment_count":canonical_count,
                  "model_fingerprint":models["fingerprint"],"vectors_sha256":base.file_sha256(vector_path),"windows_sha256":base.file_sha256(sidecar),
                  "created_at":time.time(),"granularity":"paragraph","encoding_order_sha256":order_fp}
        base.atomic_json(work/"manifest.json",manifest)
        base.atomic_json(directory/"paragraphs-active.json",{"directory":work.name,"fingerprint":fingerprint})
        (directory/"paragraphs-building.json").unlink(missing_ok=True)
        return manifest
    finally:
        lock.unlink(missing_ok=True)


def main():
    parser=argparse.ArgumentParser(description="تجهيز جميع الفقرات لفهرسة محلية متعددة المستويات")
    parser.add_argument("command",choices=("prepare","status"))
    parser.add_argument("--db",type=Path,default=base.DEFAULT_DB)
    parser.add_argument("--directory",type=Path)
    parser.add_argument("--batch-size",type=int,default=64)
    args=parser.parse_args()
    result=prepare_paragraphs(args.db,args.directory,args.batch_size) if args.command=="prepare" else paragraph_status(args.db,args.directory)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=="__main__":main()
