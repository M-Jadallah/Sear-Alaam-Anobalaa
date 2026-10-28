"""Source-bound, authored five-biography pilot; agent findings are never human approval.

The preparation command alone installs the additive schema. HTTP reads do not
import or migrate the book, and the base renderer/semantic vectors stay intact.
"""
from __future__ import annotations

import argparse
from collections import OrderedDict
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Literal

from .auth import reviewer_name
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from .db import DEFAULT_DB, ROOT, connect
from .normalization import normalize_arabic, fts_query
from .reading import build_reading_view

VERSION = "five-biography-pilot-v1"
DATA_DIR = ROOT / "reference" / "pilot"
_SOURCE_TEXTS = OrderedDict()


def has_pilot(db):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pilot_documents'").fetchone() is not None


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _literal(db, source_id, a, b):
    # SQLite substr on a 21 MB UTF-8 value repeatedly scans preceding codepoints.
    # Source texts are immutable; cache a bounded, hash-verified Unicode copy.
    source=db.execute('SELECT sha256 FROM sources WHERE id=?',(source_id,)).fetchone()
    if not source:return ''
    path=db.execute('PRAGMA database_list').fetchone()[2]
    key=(path,source_id,source[0])
    text=_SOURCE_TEXTS.get(key)
    if text is None:
        text=db.execute('SELECT full_text FROM sources WHERE id=?',(source_id,)).fetchone()[0]
        if hashlib.sha256(text.encode('utf-8')).hexdigest()!=source[0]:
            raise ValueError('النص المخزن لا يطابق بصمة المصدر؛ أوقف تطبيق التجربة')
        if path:
            _SOURCE_TEXTS[key]=text
            if len(_SOURCE_TEXTS)>4:_SOURCE_TEXTS.popitem(last=False)
    return text[a:b]


def validate_document(db, document):
    bio = db.execute("SELECT b.*,s.sha256 FROM biographies b JOIN sources s ON s.id=b.source_id WHERE b.id=?", (document["biography_id"],)).fetchone()
    if not bio or bio["sha256"] != document["source_sha256"]:
        raise ValueError("بصمة المصدر أو الترجمة لا تطابق بيانات التجربة")
    if (document["source_char_start"], document["source_char_end"]) != (bio["source_char_start"], bio["source_char_end"]):
        raise ValueError("حدود الترجمة لا تطابق المصدر")
    rows = {row["id"]: row for row in db.execute("SELECT * FROM segments WHERE biography_id=?", (bio["id"],))}
    if len(document["segments"]) != len(rows) or {item["segment_id"] for item in document["segments"]} != set(rows):
        raise ValueError("يجب تسجيل حالة كل مقطع مرة واحدة")
    def bounds(a, b, lo=bio["source_char_start"], hi=bio["source_char_end"]):
        if not isinstance(a, int) or not isinstance(b, int) or not lo <= a < b <= hi:
            raise ValueError("نطاق تحرير أو شاهد خارج حدوده")
    for item in document["segments"]:
        segment = rows[item["segment_id"]]
        if item["assessment"] not in ("supported", "no_clear_topic", "uncertain"):
            raise ValueError("حالة فحص غير معروفة")
        if not item.get("note", "").strip():
            raise ValueError("يلزم توثيق نتيجة فحص المقطع")
        for topic in item.get("topics", []):
            if topic.get('relation_type') not in ('event','saying','description','discussion','passing') or topic.get('subject_scope') not in ('biography_subject','mentioned_person','general_context') or topic.get('stance') not in ('reported','negated','accusation','contested','criticism'):
                raise ValueError('نوع الصلة أو جهة النسبة أو وضع العبارة غير صالح')
            bounds(topic["evidence_start"], topic["evidence_end"], segment["source_char_start"], segment["source_char_end"])
            bounds(topic["context_start"], topic["context_end"])
            if not topic["context_start"] <= topic["evidence_start"] < topic["evidence_end"] <= topic["context_end"]:
                raise ValueError("السياق لا يحتوي الشاهد")
            if not topic.get("rationale", "").strip():
                raise ValueError("يلزم سبب نصي للربط")
        expected_hits = {row[0] for row in db.execute("SELECT id FROM topic_hits WHERE segment_id=?", (segment["id"],))}
        recommendations = item.get("existing_hits", [])
        if {hit["hit_id"] for hit in recommendations} != expected_hits or len(recommendations) != len(expected_hits):
            raise ValueError("يلزم فحص جميع المرشحات السابقة دون تكرار")
        if any(hit["decision"] not in ("keep", "modify", "exclude", "uncertain") or not hit.get("reason", "").strip() for hit in recommendations):
            raise ValueError("توصية غير مكتملة")
    presentation = document["presentation"]
    for kind in ("quotes", "poetry", "bold", "keywords", "names", "footnotes", "lists"):
        for span in presentation.get(kind, []):
            bounds(span["start"], span["end"])
            if kind == "footnotes":
                import re
                if not re.fullmatch(r"\([0-9]+\)", _literal(db, bio["source_id"], span["start"], span["end"])):
                    raise ValueError("الإشارة المحددة ليست قوسًا رقميًا حرفيًا")
            if kind == "lists":
                if not span.get('items'):
                    raise ValueError('قائمة بلا عناصر')
                previous = span["start"]
                for part in span["items"]:
                    bounds(part["start"], part["end"], span["start"], span["end"])
                    if part["start"] < previous:
                        raise ValueError("تداخل أو إعادة ترتيب عناصر القائمة")
                    previous = part["end"]
    for heading in presentation.get("headings", []):
        if not bio["source_char_start"] <= heading["at"] < bio["source_char_end"] or not heading["label"].strip():
            raise ValueError("عنوان تنظيمي غير صالح")
    for gold in document.get("gold", []):
        bounds(gold["start"], gold["end"])
    return bio


def backup_database(path):
    path = Path(path)
    directory = path.parent / "backups"
    directory.mkdir(exist_ok=True)
    output = directory / f"{path.stem}-before-pilot-{datetime.now(timezone.utc):%Y%m%dT%H%M%S%fZ}.sqlite3"
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as source, closing(sqlite3.connect(output)) as target:
        source.backup(target)
    return output


def prepare(db_path=DEFAULT_DB, documents=None):
    documents = documents if documents is not None else [json.loads(path.read_text(encoding="utf-8")) for path in sorted(DATA_DIR.glob("biography-*.json"))]
    if not documents:
        raise ValueError("لا توجد بيانات مراجعة التجربة")
    revision = _digest(documents)
    with closing(connect(db_path)) as db:
        from .curation import has_new_batches
        if has_new_batches(db):
            raise ValueError("توجد دفعات تحريرية منشورة؛ استخدم backend.curation دون استبدالها")
        if has_pilot(db):
            previous = {row[0] for row in db.execute("SELECT revision FROM pilot_documents")}
            if previous == {revision}:
                if len(_valid_documents(db))!=len(documents):raise ValueError('تغير مصدر التجربة؛ يلزم مراجعة التعليقات')
                return {"status": "unchanged", "revision": revision}
            if db.execute("SELECT 1 FROM pilot_events LIMIT 1").fetchone():
                raise ValueError("توجد قرارات بشرية؛ لا تستبدل طبقة التجربة. رحّل التعليقات مع حفظ قراراتها أولًا")
        for document in documents:
            validate_document(db, document)
        eligible = [row[0] for row in db.execute("SELECT id FROM biographies ORDER BY source_id,source_char_start LIMIT 5")]
        if sorted(doc["biography_id"] for doc in documents) != sorted(eligible):
            raise ValueError("التجربة مقتصرة على أول خمس تراجم؛ يلزم اكتمال ملفاتها")
    backup = backup_database(db_path)
    with closing(connect(db_path)) as db:
        db.executescript((ROOT / "backend" / "pilot_schema.sql").read_text(encoding="utf-8"))
        with db:
            db.execute("DELETE FROM pilot_topics_fts")
            db.execute("DELETE FROM pilot_evidence")
            db.execute("DELETE FROM pilot_coverage")
            db.execute("DELETE FROM pilot_documents")
            db.execute('DELETE FROM pilot_topic_aliases')
            for document in documents:
                bio = validate_document(db, document)
                db.execute("INSERT INTO pilot_documents VALUES (?,?,?,?,?)", (bio["id"], bio["source_id"], bio["sha256"], revision, json.dumps(document, ensure_ascii=False)))
                for segment in document["segments"]:
                    db.execute("INSERT INTO pilot_coverage(segment_id,biography_id,assessment,note,reviewer) VALUES (?,?,?,?,?)", (segment["segment_id"], bio["id"], segment["assessment"], segment["note"], document["reviewer"]))
                    for index, topic in enumerate(segment.get("topics", [])):
                        parent = db.execute("SELECT id FROM topics WHERE parent_id IS NULL AND name=?", (topic["category"],)).fetchone()
                        if not parent:
                            raise ValueError("باب موضوعي غير موجود: " + topic["category"])
                        existing = db.execute("SELECT id FROM topics WHERE parent_id=? AND name=?", (parent[0], topic["topic"])).fetchone()
                        if existing:
                            topic_id = existing[0]
                        else:
                            topic_id = db.execute("INSERT INTO topics(parent_id,name,search_terms_csv,dictionary_status) VALUES (?,?,?,?)", (parent[0], topic["topic"], ",".join(topic.get("aliases", [])), "proposed_from_text")).lastrowid
                        for alias in topic.get('aliases',[]):
                            db.execute('INSERT OR IGNORE INTO pilot_topic_aliases(topic_id,alias) VALUES (?,?)',(topic_id,alias))
                        original = db.execute("SELECT id FROM topic_hits WHERE segment_id=? AND topic_id=?", (segment["segment_id"], topic_id)).fetchone()
                        _insert_evidence(db, document, bio, segment["segment_id"], topic_id, original[0] if original else None,
                                         topic, "supported" if segment["assessment"] == "supported" else "uncertain", f"{bio['id']}:{segment['segment_id']}:topic:{index}")
                    for old in segment.get("existing_hits", []):
                        hit = db.execute("SELECT h.*,s.source_char_start ca,s.source_char_end cb FROM topic_hits h JOIN segments s ON s.id=h.segment_id WHERE h.id=?", (old["hit_id"],)).fetchone()
                        if old["decision"] in ("keep", "modify") and db.execute("SELECT 1 FROM pilot_evidence WHERE original_hit_id=?", (old["hit_id"],)).fetchone():
                            continue
                        topic = {"evidence_start": hit["source_char_start"], "evidence_end": hit["source_char_end"], "context_start": hit["ca"], "context_end": hit["cb"], "rationale": old["reason"], "relation_type": "passing", "subject_scope": "general_context", "stance": "reported"}
                        decision = "exclude" if old["decision"] == "exclude" else "uncertain"
                        _insert_evidence(db, document, bio, segment["segment_id"], hit["topic_id"], hit["id"], topic, decision, f"{bio['id']}:old:{hit['id']}")
            # Remove only unused labels created by this disposable pilot draft.
            # CSV labels and any label referenced by canonical data survive.
            db.execute("DELETE FROM topics WHERE dictionary_status='proposed_from_text' AND NOT EXISTS (SELECT 1 FROM pilot_evidence e WHERE e.topic_id=topics.id) AND NOT EXISTS (SELECT 1 FROM topic_hits h WHERE h.topic_id=topics.id) AND NOT EXISTS (SELECT 1 FROM topics child WHERE child.parent_id=topics.id)")
            rebuild_fts(db)
        return {"status": "ready", "revision": revision, "backup": str(backup), "biographies": len(documents), "segments": db.execute("SELECT COUNT(*) FROM pilot_coverage").fetchone()[0], "evidence": db.execute("SELECT COUNT(*) FROM pilot_evidence").fetchone()[0]}


def _insert_evidence(db, document, bio, segment_id, topic_id, original_id, topic, decision, key):
    db.execute("""INSERT INTO pilot_evidence(stable_key,biography_id,segment_id,source_id,source_sha256,topic_id,original_hit_id,
    source_char_start,source_char_end,context_char_start,context_char_end,rationale,relation_type,subject_scope,stance,agent_decision,reviewer)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (key, bio["id"], segment_id, bio["source_id"], document["source_sha256"], topic_id, original_id,
        topic["evidence_start"], topic["evidence_end"], topic["context_start"], topic["context_end"], topic["rationale"], topic["relation_type"], topic["subject_scope"], topic["stance"], decision, document["reviewer"]))


def rebuild_fts(db):
    db.execute("DELETE FROM pilot_topics_fts")
    for row in db.execute("SELECT e.*,t.name,t.search_terms_csv FROM pilot_evidence e JOIN topics t ON t.id=e.topic_id"):
        aliases=' '.join(r[0] for r in db.execute('SELECT alias FROM pilot_topic_aliases WHERE topic_id=?',(row['topic_id'],)))
        db.execute("INSERT INTO pilot_topics_fts(evidence_id,topic_text,context_text) VALUES (?,?,?)", (row["id"], normalize_arabic(row["name"] + " " + (row["search_terms_csv"] or "")+' '+aliases), normalize_arabic(_literal(db, row["source_id"], row["context_char_start"], row["context_char_end"]))))


def _valid_documents(db):
    if not has_pilot(db):
        return []
    return [row[0] for row in db.execute("SELECT p.biography_id FROM pilot_documents p JOIN sources s ON s.id=p.source_id WHERE p.source_sha256=s.sha256")]


def presentation(db_path, biography_id):
    with closing(connect(db_path)) as db:
        if not db.execute("SELECT 1 FROM biographies WHERE id=?", (biography_id,)).fetchone():
            raise HTTPException(404, "الترجمة غير موجودة")
        if not has_pilot(db):
            return {"available": False, "status": "not_available"}
        row = db.execute("SELECT p.*,b.full_text,b.source_char_start,b.source_char_end,s.sha256 FROM pilot_documents p JOIN biographies b ON b.id=p.biography_id JOIN sources s ON s.id=p.source_id WHERE p.biography_id=?", (biography_id,)).fetchone()
        if not row:
            return {"available": False, "status": "not_available"}
        if row["source_sha256"] != row["sha256"]:
            return {"available": False, "status": "stale", "warning": "تغيرت بصمة المصدر؛ أوقف تطبيق التنسيق التحريري حتى إعادة مراجعته."}
        document = json.loads(row["payload"])
        annotations = document["presentation"]
        text, offset = row["full_text"], row["source_char_start"]
        for note in annotations.get("footnotes", []):
            note["raw"] = text[note["start"]-offset:note["end"]-offset]
            note["context"] = text[max(0,note["start"]-offset-55):min(len(text),note["end"]-offset+55)]
        view = build_reading_view(text, offset, starts_at_line_start=True)
        result = {"available": True, "status": "ready", "revision": row["revision"], "source_sha256": row["sha256"], "source_id": row["source_id"], "biography_id": biography_id,
                "source_char_start": offset, "source_char_end": row["source_char_end"], "annotations": annotations, "reading_view": view, "plain_text": presentation_plain_text(view, annotations)}
        if annotations.get("footnote_notice"):
            result["footnote_notice"]=annotations["footnote_notice"]
        from .curation import biography_batch
        batch=biography_batch(db,biography_id)
        if batch:
            result.update(batch_id=batch["id"],batch_label=batch["label"],batch_legacy=batch["legacy"])
        from .pilot_reading import apply_reading_overlay
        return apply_reading_overlay(db, result)


def presentation_plain_text(view, annotations):
    """Copy includes every linguistic run and a literal appendix for moved marks."""
    notes = annotations.get("footnotes", [])
    ordered_notes = sorted(notes, key=lambda note: note["start"])
    headings = sorted(annotations.get("headings", []), key=lambda h:h["at"])
    output, index = [], 0
    for block in view["blocks"]:
        while index < len(headings) and headings[index]["at"] <= block["source_char_start"]:
            output.append("[عنوان تنظيمي: " + headings[index]["label"] + "]")
            index += 1
        chunks = []
        for run in block["runs"]:
            if run["type"] == "virtual_space":
                chunks.append(run["text"])
            elif run["type"] == "text":
                a, b = run["source_char_start"], run["source_char_end"]
                # Subtract intervals instead of checking every note per character.
                # Python slicing uses the same Unicode code points as source spans.
                cursor = a
                for note in ordered_notes:
                    if note["end"] <= cursor:
                        continue
                    if note["start"] >= b:
                        break
                    left, right = max(cursor, note["start"]), min(b, note["end"])
                    chunks.append(run["text"][cursor-a:left-a])
                    cursor = max(cursor, right)
                chunks.append(run["text"][cursor-a:])
        paragraph = "".join(chunks)
        if paragraph.strip() or view["renderer_version"] != "pilot-reading-v2":
            output.append(paragraph)
    if notes:
        output.append("[ملحق إشارات الحواشي: " + annotations.get("footnote_notice", "متونها المقابلة غير موجودة في العينة") + "]")
        output.extend(f"{note['raw']} · [{note['start']}, {note['end']}) · {note['context']}" for note in notes)
    return "\n\n".join(output)


def _serialize(db, row):
    item = dict(row)
    if "topic_name" not in item:
        item["topic_name"] = db.execute("SELECT name FROM topics WHERE id=?", (item["topic_id"],)).fetchone()[0]
    item["evidence_text"] = _literal(db,item["source_id"],item["source_char_start"],item["source_char_end"])
    item["context_text"] = _literal(db,item["source_id"],item["context_char_start"],item["context_char_end"])
    item['reading_view']=build_reading_view(item['evidence_text'],item['source_char_start'],starts_at_line_start=_literal(db,item['source_id'],max(0,item['source_char_start']-1),item['source_char_start'])=='\n')
    item["reader_url"] = f"/reader/{item['biography_id']}?start={item['context_char_start']}&end={item['context_char_end']}"
    return item


def pilot_revision(db):
    if not has_pilot(db):
        return None
    return [tuple(row) for row in db.execute("SELECT biography_id,revision,source_sha256 FROM pilot_documents ORDER BY biography_id")] + [db.execute("SELECT COALESCE(MAX(id),0) FROM pilot_events").fetchone()[0]]


def topic_candidates(db_path, query, plan, limit=100):
    with closing(connect(db_path)) as db:
        valid = _valid_documents(db)
        valid = _scope_biographies(db,plan,valid)
        if not valid:
            return []
        args = list(valid)
        conditions = ["e.biography_id IN (" + ",".join("?" for _ in valid) + ")", "e.human_status!='rejected'", "e.agent_decision!='exclude'", "(e.agent_decision='supported' OR e.human_status='reviewed')"]
        if plan.get("reviewed_only"):
            conditions.append("e.human_status='reviewed'")
        topic_ids = plan.get("topic_ids", [])
        if topic_ids:
            conditions.append("e.topic_id IN (" + ",".join("?" for _ in topic_ids) + ")")
            args.extend(topic_ids)
            sql = "SELECT e.*,b.heading,b.section_id FROM pilot_evidence e JOIN biographies b ON b.id=e.biography_id WHERE " + " AND ".join(conditions) + " ORDER BY (e.human_status='reviewed') DESC,e.id LIMIT ?"
        else:
            if not query:
                return []
            conditions.append("pilot_topics_fts MATCH ?")
            args.append(query)
            sql = "SELECT e.*,b.heading,b.section_id FROM pilot_topics_fts f JOIN pilot_evidence e ON e.id=f.evidence_id JOIN biographies b ON b.id=e.biography_id WHERE " + " AND ".join(conditions) + " ORDER BY bm25(pilot_topics_fts,0,4,1) LIMIT ?"
        args.append(limit)
        result=[]
        for row in db.execute(sql,args):
            item=_serialize(db,row)
            # Retrieval returns the full qualifying context, preserving criticism.
            item.update(topic_audit_id=item["id"], source_char_start=item["context_char_start"],source_char_end=item["context_char_end"],clean_text=build_reading_view(item["context_text"],item["context_char_start"])["plain_text"],topic_review_status=item["human_status"])
            result.append(item)
        return result


def search_scope(db_path, plan):
    """Return explicit eligible biographies and topic witness spans for filters."""
    with closing(connect(db_path)) as db:
        biographies = _scope_biographies(db,plan,_valid_documents(db)) if plan.get("pilot_only") or plan.get("batch_id") else None
        spans = None
        if plan.get("reviewed_only") or plan.get("topic_mode") == "filter":
            spans=[]
            if has_pilot(db):
                valid=_valid_documents(db)
                for row in db.execute("SELECT * FROM pilot_evidence WHERE human_status!='rejected' AND agent_decision!='exclude' AND (agent_decision='supported' OR human_status='reviewed')"):
                    if row["biography_id"] not in valid or (plan.get("reviewed_only") and row["human_status"]!='reviewed'):
                        continue
                    if plan.get("topic_mode") == "filter" and (not plan.get("topic_ids") or row["topic_id"] not in plan["topic_ids"]):
                        continue
                    spans.append((row["source_id"],row["context_char_start"],row["context_char_end"]))
            for row in db.execute("SELECT h.*,s.source_id FROM topic_hits h JOIN segments s ON s.id=h.segment_id WHERE h.review_status='reviewed'"):
                if plan.get("topic_mode") == "filter" and (not plan.get("topic_ids") or row["topic_id"] not in plan["topic_ids"]):
                    continue
                spans.append((row["source_id"],row["source_char_start"],row["source_char_end"]))
        return biographies,spans


def _scope_biographies(db,plan,valid):
    if plan.get("pilot_only"):
        from .curation import list_batches
        legacy={bio['id'] for batch in list_batches(db)['items'] if batch['legacy'] for bio in batch['biographies']}
        valid=[identifier for identifier in valid if identifier in legacy]
    if plan.get("batch_id"):
        from .curation import batch_biography_ids
        batch=set(batch_biography_ids(db,plan["batch_id"]))
        valid=[identifier for identifier in valid if identifier in batch]
    return valid


class PilotReview(BaseModel):
    model_config=ConfigDict(extra="forbid")
    decision: Literal["reviewed","rejected"]
    reviewer: str=Field(min_length=1,max_length=100)
    note: str=Field(default="",max_length=3000)
    topic_id: int|None=None
    source_char_start: int|None=None
    source_char_end: int|None=None
    batch_id: str|None=Field(None,min_length=1,max_length=100)


class CoverageReview(BaseModel):
    model_config=ConfigDict(extra='forbid')
    reviewer:str=Field(min_length=1,max_length=100)
    note:str=Field(default='',max_length=3000)
    batch_id:str|None=Field(None,min_length=1,max_length=100)


def pilot_router(db_path):
    router=APIRouter()
    @router.get("/api/biographies/{biography_id}/presentation")
    def get_presentation(biography_id:int):
        return presentation(db_path,biography_id)

    @router.get("/api/biographies/{biography_id}/topics")
    def get_topics(biography_id:int):
        with closing(connect(db_path)) as db:
            if not db.execute("SELECT 1 FROM biographies WHERE id=?",(biography_id,)).fetchone():raise HTTPException(404,"الترجمة غير موجودة")
            coverage={"total":0,"agent_checked":0,"human_checked":0,"uncertain":0,"no_clear_topic":0}
            if biography_id not in _valid_documents(db):return {"items":[],"coverage":coverage,"pilot":False}
            for row in db.execute("SELECT * FROM pilot_coverage WHERE biography_id=?",(biography_id,)):
                coverage["total"]+=1;coverage["agent_checked"]+=1;coverage["human_checked"]+=row["human_checked"]
                if row["assessment"] in ("uncertain","no_clear_topic"):coverage[row["assessment"]]+=1
            topics={}
            for row in db.execute("SELECT e.*,t.name topic_name,t.dictionary_status FROM pilot_evidence e JOIN topics t ON t.id=e.topic_id WHERE e.biography_id=? AND e.human_status!='rejected' AND e.agent_decision!='exclude' AND (e.agent_decision='supported' OR e.human_status='reviewed') ORDER BY t.name,e.id",(biography_id,)):
                item=_serialize(db,row)
                group=topics.setdefault(item["topic_id"],{"topic_id":item["topic_id"],"name":item["topic_name"],"dictionary_status":item["dictionary_status"],"evidence_count":0,"human_reviewed_count":0,"evidence":[]})
                group["evidence"].append(item);group["evidence_count"]+=item["agent_decision"]=="supported" and item["human_status"]!='rejected';group["human_reviewed_count"]+=item["human_status"]=='reviewed'
            return {"items":list(topics.values()),"coverage":coverage,"pilot":True}

    @router.get("/api/pilot/reviews")
    def reviews(biography_id:int|None=None,topic_id:int|None=None,batch_id:str|None=None,eligible_only:bool=False,status:Literal["all","pending","reviewed","rejected"]="pending",page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100)):
        with closing(connect(db_path)) as db:
            valid=_valid_documents(db)
            if batch_id:
                try:valid=_scope_biographies(db,{"batch_id":batch_id},valid)
                except ValueError as error:raise HTTPException(422,str(error))
            if not valid:return {"items":[],"total":0,"page":page,"page_size":page_size}
            conditions=["e.biography_id IN ("+",".join("?" for _ in valid)+")"];args=list(valid)
            if biography_id is not None:conditions.append("e.biography_id=?");args.append(biography_id)
            if topic_id is not None:conditions.append('e.topic_id=?');args.append(topic_id)
            if eligible_only:conditions.extend(["e.human_status!='rejected'","e.agent_decision!='exclude'","(e.agent_decision='supported' OR e.human_status='reviewed')"])
            if status!="all":conditions.append("e.human_status=?");args.append(status)
            sql=" FROM pilot_evidence e JOIN topics t ON t.id=e.topic_id WHERE "+" AND ".join(conditions)
            total=db.execute("SELECT COUNT(*)"+sql,args).fetchone()[0]
            rows=db.execute("SELECT e.*,t.name topic_name"+sql+" ORDER BY e.id LIMIT ? OFFSET ?",(*args,page_size,(page-1)*page_size))
            return {"items":[_serialize(db,row) for row in rows],"total":total,"page":page,"page_size":page_size}

    @router.get('/api/pilot/coverage')
    def coverage(biography_id:int|None=None,batch_id:str|None=None,status:Literal['pending','reviewed','all']='pending',page:int=Query(1,ge=1),page_size:int=Query(10,ge=1,le=100)):
        with closing(connect(db_path)) as db:
            valid=_valid_documents(db)
            if batch_id:
                try:valid=_scope_biographies(db,{"batch_id":batch_id},valid)
                except ValueError as error:raise HTTPException(422,str(error))
            if not valid:return {'items':[],'total':0,'page':page,'page_size':page_size}
            conditions=['c.biography_id IN ('+','.join('?' for _ in valid)+')'];args=list(valid)
            if biography_id is not None:conditions.append('c.biography_id=?');args.append(biography_id)
            if status!='all':conditions.append('c.human_checked=?');args.append(int(status=='reviewed'))
            sql=' FROM pilot_coverage c JOIN segments s ON s.id=c.segment_id WHERE '+' AND '.join(conditions)
            total=db.execute('SELECT COUNT(*)'+sql,args).fetchone()[0]
            rows=[]
            for row in db.execute('SELECT c.*,s.text_original,s.source_char_start,s.source_char_end'+sql+' ORDER BY c.biography_id,s.source_char_start LIMIT ? OFFSET ?',(*args,page_size,(page-1)*page_size)):
                item=dict(row);item['reading_view']=build_reading_view(item['text_original'],item['source_char_start'],starts_at_line_start=True);rows.append(item)
            return {'items':rows,'total':total,'page':page,'page_size':page_size}

    @router.post('/api/pilot/coverage/{segment_id}')
    def review_coverage(segment_id:int,payload:CoverageReview):
        with closing(connect(db_path)) as db,db:
            if not has_pilot(db):raise HTTPException(404,'التجربة غير موجودة')
            row=db.execute('SELECT * FROM pilot_coverage WHERE segment_id=?',(segment_id,)).fetchone()
            if not row:raise HTTPException(404,'المقطع غير موجود في التجربة')
            if row['biography_id'] not in _valid_documents(db):raise HTTPException(409,'تغيرت بصمة المصدر')
            if payload.batch_id:
                try:scope=_scope_biographies(db,{"batch_id":payload.batch_id},_valid_documents(db))
                except ValueError as error:raise HTTPException(422,str(error))
                if row['biography_id'] not in scope:raise HTTPException(422,'المقطع خارج الدفعة المحددة')
            if not reviewer_name(payload.reviewer).strip():raise HTTPException(422,'اسم المراجع مطلوب')
            if row['human_checked']:raise HTTPException(409,'سبق تسجيل مراجعة بشرية لهذا المقطع')
            db.execute('UPDATE pilot_coverage SET human_checked=1 WHERE segment_id=?',(segment_id,))
            event={'segment_id':segment_id,'human_checked':1,'reviewer':reviewer_name(payload.reviewer),'note':payload.note}
            db.execute("INSERT INTO pilot_events(actor_kind,reviewer,before_json,after_json,note) VALUES ('human',?,?,?,?)",(reviewer_name(payload.reviewer),json.dumps(dict(row),ensure_ascii=False),json.dumps(event,ensure_ascii=False),payload.note))
            db.execute("INSERT INTO review_log(entity_type,entity_id,change_summary,reviewer) VALUES ('pilot_coverage',?,?,?)",(segment_id,json.dumps(event,ensure_ascii=False),reviewer_name(payload.reviewer)))
            return event

    @router.post("/api/pilot/reviews/{evidence_id}")
    def review(evidence_id:int,payload:PilotReview):
        with closing(connect(db_path)) as db,db:
            if not has_pilot(db):raise HTTPException(404,"لا توجد تجربة معدة")
            before=db.execute("SELECT * FROM pilot_evidence WHERE id=?",(evidence_id,)).fetchone()
            if not before:raise HTTPException(404,"الشاهد غير موجود")
            before=dict(before)
            if before["biography_id"] not in _valid_documents(db):raise HTTPException(409,"بصمة المصدر تغيرت")
            if payload.batch_id:
                try:scope=_scope_biographies(db,{"batch_id":payload.batch_id},_valid_documents(db))
                except ValueError as error:raise HTTPException(422,str(error))
                if before['biography_id'] not in scope:raise HTTPException(422,'الشاهد خارج الدفعة المحددة')
            if before["human_status"]!='pending':raise HTTPException(409,"سبق اتخاذ قرار في هذا الاقتراح؛ لا يستبدل قرار بشري تلقائيًا")
            if not reviewer_name(payload.reviewer).strip():raise HTTPException(422,"اسم المراجع مطلوب")
            a=payload.source_char_start if payload.source_char_start is not None else before["source_char_start"]
            b=payload.source_char_end if payload.source_char_end is not None else before["source_char_end"]
            topic=payload.topic_id if payload.topic_id is not None else before["topic_id"]
            segment=db.execute("SELECT * FROM segments WHERE id=?",(before["segment_id"],)).fetchone()
            if not segment["source_char_start"]<=a<b<=segment["source_char_end"]:raise HTTPException(422,"الشاهد يجب أن يبقى داخل المقطع الأصلي")
            if not db.execute("SELECT 1 FROM topics WHERE id=? AND parent_id IS NOT NULL",(topic,)).fetchone():raise HTTPException(422,"موضوع غير صالح")
            canonical_sql='SELECT * FROM topic_hits WHERE id=? OR (segment_id=? AND topic_id=?) ORDER BY id'
            canonical_args=(before['original_hit_id'],before['segment_id'],topic)
            canonical_before=[dict(r) for r in db.execute(canonical_sql,canonical_args)]
            if payload.decision=='reviewed':
                old=db.execute("SELECT * FROM topic_hits WHERE id=?",(before["original_hit_id"],)).fetchone() if before["original_hit_id"] else None
                if old and old["review_status"]=='reviewed':raise HTTPException(409,"الوسم الأصلي معتمد بشريًا؛ احتفظنا به دون استبدال")
                if old and topic!=old['topic_id'] and before['agent_decision']!='exclude':
                    db.execute("UPDATE topic_hits SET review_status='rejected',reviewer_note=? WHERE id=?",('استبدل المراجع البشري الموضوع في اقتراح التجربة. '+payload.note,old['id']))
                if before["agent_decision"]=='exclude':
                    if old:db.execute("UPDATE topic_hits SET review_status='rejected',reviewer_note=? WHERE id=?",(payload.note,old["id"]))
                else:
                    existing=db.execute("SELECT * FROM topic_hits WHERE segment_id=? AND topic_id=?",(before["segment_id"],topic)).fetchone()
                    if existing and existing["review_status"]=='reviewed':raise HTTPException(409,"يوجد وسم معتمد؛ لم نستبدله")
                    literal=_literal(db,before["source_id"],a,b)
                    if existing:
                        db.execute("UPDATE topic_hits SET evidence_text=?,source_char_start=?,source_char_end=?,review_status='reviewed',reviewer_note=? WHERE id=?",(literal,a,b,payload.note,existing["id"]))
                    else:
                        db.execute("INSERT INTO topic_hits(topic_id,segment_id,evidence_text,source_char_start,source_char_end,method,review_status,reviewer_note) VALUES (?,?,?,?,?,'manual','reviewed',?)",(topic,before["segment_id"],literal,a,b,payload.note))
            db.execute("UPDATE pilot_evidence SET human_status=?,human_reviewer=?,human_note=?,topic_id=?,source_char_start=?,source_char_end=?,context_char_start=MIN(context_char_start,?),context_char_end=MAX(context_char_end,?),updated_at=CURRENT_TIMESTAMP WHERE id=?",(payload.decision,reviewer_name(payload.reviewer),payload.note,topic,a,b,a,b,evidence_id))
            after=dict(db.execute("SELECT * FROM pilot_evidence WHERE id=?",(evidence_id,)).fetchone())
            after['canonical_before']=canonical_before
            after['canonical_after']=[dict(r) for r in db.execute(canonical_sql,canonical_args)]
            db.execute("INSERT INTO pilot_events(evidence_id,actor_kind,reviewer,before_json,after_json,note) VALUES (?,'human',?,?,?,?)",(evidence_id,reviewer_name(payload.reviewer),json.dumps(before,ensure_ascii=False),json.dumps(after,ensure_ascii=False),payload.note))
            db.execute("INSERT INTO review_log(entity_type,entity_id,change_summary,reviewer) VALUES ('pilot_topic',?,?,?)",(evidence_id,json.dumps({"before":before,"after":after,"note":payload.note},ensure_ascii=False),reviewer_name(payload.reviewer)))
            rebuild_fts(db)
            return _serialize(db,after)
    return router


def main():
    parser=argparse.ArgumentParser(description="إعداد تجربة التنسيق ومراجعة موضوعات أول خمس تراجم")
    parser.add_argument("command",choices=["prepare", "prepare-reading"])
    parser.add_argument("--db",type=Path,default=DEFAULT_DB)
    args=parser.parse_args()
    if args.command == "prepare-reading":
        from .pilot_reading import prepare_reading
        result = prepare_reading(args.db)
    else:
        result = prepare(args.db)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=="__main__":main()
