"""FastAPI read and review API for the local source-preserving library."""
from contextlib import contextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse
import json
import os
import sqlite3

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .db import DEFAULT_DB, DEFAULT_SOURCE, connect, initialize
from .normalization import normalize_arabic, fts_query
from .reading import reading_router, enrich_reading_views
from .smart_search import smart_router
from .semantic import semantic_status
from .pilot import pilot_router
from .curation import curation_router
from .auth import Auth, AuthSettings, reviewer_name


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid")
    review_status: Literal["candidate", "reviewed", "rejected"]
    reviewer: str = Field(default="مراجع محلي", min_length=1, max_length=100)
    note: str = Field(default="", max_length=3000)
    topic_id: int | None = None
    person_id: int | None = None
    source_char_start: int | None = None
    source_char_end: int | None = None

class SegmentReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_char_start: int
    source_char_end: int
    segment_type: Literal["paragraph", "saying", "story", "unverified_excerpt"] = "paragraph"
    reviewer: str = Field(default="مراجع محلي", min_length=1, max_length=100)
    note: str = Field(default="", max_length=3000)

class ManualTopicHit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    topic_id: int
    source_char_start: int
    source_char_end: int
    review_status: Literal["candidate", "reviewed"] = "reviewed"
    reviewer: str = Field(default="مراجع محلي", min_length=1, max_length=100)
    note: str = Field(default="", max_length=3000)


def _one(db, sql, args=()):
    row = db.execute(sql, args).fetchone()
    if row is None:
        raise HTTPException(404, "العنصر غير موجود")
    return dict(row)


def _list(db, sql, args=(), page=1, page_size=24):
    count = db.execute("SELECT COUNT(*) FROM (" + sql + ")", args).fetchone()[0]
    items = [dict(row) for row in db.execute(sql + " LIMIT ? OFFSET ?", (*args, page_size, (page-1)*page_size))]
    return {"items": items, "total": count, "page": page, "page_size": page_size}


def _source_summary(row):
    if not row:
        return None
    source = dict(row)
    source.pop("full_text", None)
    source["metadata"] = json.loads(source["metadata"])
    source["known_missing_parts"] = "ملف OpenITI يذكر نقص أجزاء من PageV01P376 وPageV01P385 واضطراب ترقيم الأجزاء الثلاثة الأولى؛ يلزم التحقيق." if source["sha256"] == "7ae7f691ffe236f5b888f5dfdf18becd9e5ea17ab6b5851528db5efd333886c0" else None
    return source


def _witness_page_markers(db, result):
    """Resolve raw page context at the witness, not its containing paragraph.

    OpenITI tags close preceding text. Tags themselves belong to that page;
    placeholders remain available as raw context but never become page claims.
    """
    def marker_at(source_id, offset):
        previous = db.execute("SELECT raw_marker,source_char_offset,source_char_end FROM page_markers WHERE source_id=? AND source_char_offset<=? ORDER BY source_char_offset DESC LIMIT 1", (source_id, offset)).fetchone()
        if previous and offset < previous["source_char_end"]:
            return previous
        return db.execute("SELECT raw_marker,source_char_offset,source_char_end FROM page_markers WHERE source_id=? AND source_char_offset>=? ORDER BY source_char_offset LIMIT 1", (source_id, offset)).fetchone()

    for row in result["items"]:
        first = marker_at(row["source_id"], row["source_char_start"])
        last = marker_at(row["source_id"], max(row["source_char_start"], row["source_char_end"] - 1))
        row["first_page_marker"] = first["raw_marker"] if first else None
        row["last_page_marker"] = last["raw_marker"] if last else None
        row["page_marker"] = first["raw_marker"] if first and first["raw_marker"] != "PageV00P000" else None
        row["page_marker_crossed"] = (first["source_char_offset"] if first else None) != (last["source_char_offset"] if last else None)
        row["page_reference_status"] = "raw_unverified"
    return enrich_reading_views(db, result)


def create_app(db_path=None, source_path=None):
    database = Path(db_path or os.environ.get("SIYAR_DB", DEFAULT_DB))
    source_file = Path(source_path or os.environ.get("SIYAR_SOURCE", DEFAULT_SOURCE))
    initialize(database)
    app = FastAPI(title="موسوعة سير أعلام النبلاء", version="1.0.0")
    app.state.db_path = database
    auth = Auth(AuthSettings.from_environment(database))
    app.state.auth = auth
    auth.install(app)
    app.include_router(reading_router(database))
    app.include_router(smart_router(database))
    app.include_router(pilot_router(database))
    app.include_router(curation_router(database))

    @contextmanager
    def session():
        connection = connect(database)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @app.get("/api/health")
    def health():
        return {"status": "ok", "mode": "production" if auth.settings.production else "local", "semantic_ai": semantic_status(database)["ready"], "generative_ai": False}

    @app.get("/api/live")
    def live():
        return {"status": "alive"}

    @app.get("/api/ready")
    def ready():
        from .deployment import verify_library
        try:
            verify_library(database, source_file, full=False)
            state = semantic_status(database)
            return {"status": "ready", "semantic_ready": state["ready"], "semantic_status": state["status"]}
        except (OSError, ValueError, sqlite3.Error) as error:
            raise HTTPException(503, "بيانات المصدر غير جاهزة: " + str(error)) from error

    @app.get("/api/stats")
    def stats():
        with session() as db:
            counts = {}
            for key, table in (("sources","sources"),("biographies","biographies"),("people","people"),("segments","segments"),("topic_hits","topic_hits"),("mentions","person_mentions")):
                counts[key] = db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
            counts["topics"] = db.execute("SELECT COUNT(*) FROM topics WHERE parent_id IS NOT NULL").fetchone()[0]
            counts["categories"] = db.execute("SELECT COUNT(*) FROM topics WHERE parent_id IS NULL").fetchone()[0]
            counts["reviewed"] = db.execute("SELECT COUNT(*) FROM topic_hits WHERE review_status='reviewed'").fetchone()[0] + db.execute("SELECT COUNT(*) FROM person_mentions WHERE identity_review_status='reviewed'").fetchone()[0]
            counts["pending"] = db.execute("SELECT COUNT(*) FROM topic_hits WHERE review_status='candidate'").fetchone()[0] + db.execute("SELECT COUNT(*) FROM person_mentions WHERE identity_review_status='candidate'").fetchone()[0]
            counts["pending_topics"] = db.execute("SELECT COUNT(*) FROM topic_hits WHERE review_status='candidate'").fetchone()[0]
            counts["pending_mentions"] = db.execute("SELECT COUNT(*) FROM person_mentions WHERE identity_review_status='candidate'").fetchone()[0]
            counts["pending_segments"] = db.execute("SELECT COUNT(*) FROM segments WHERE segmentation_status='candidate'").fetchone()[0]
            counts["pending_people"] = db.execute("SELECT COUNT(*) FROM people WHERE identity_review_status='candidate'").fetchone()[0]
            counts["pending_scope"] = "topic_hits_and_person_mentions; segmentation and identities have separate counters"
            counts["reviewed_topics"] = db.execute("SELECT COUNT(*) FROM topic_hits WHERE review_status='reviewed'").fetchone()[0]
            counts["reviewed_mentions"] = db.execute("SELECT COUNT(*) FROM person_mentions WHERE identity_review_status='reviewed'").fetchone()[0]
            counts["page_markers"] = db.execute("SELECT COUNT(*) FROM page_markers").fetchone()[0]
            counts["sections"] = db.execute("SELECT COUNT(*) FROM source_sections").fetchone()[0]
            counts["source_missing"] = counts["sources"] == 0
            counts["source_file_exists"] = source_file.is_file()
            counts["source"] = _source_summary(db.execute("SELECT * FROM sources ORDER BY id DESC LIMIT 1").fetchone())
            if counts["source"]:
                source_id = counts["source"]["id"]
                chars = db.execute("SELECT COALESCE(SUM(source_char_end-source_char_start),0) FROM source_sections WHERE source_id=?", (source_id,)).fetchone()[0]
                counts["source"]["coverage"] = {"stored_chars": counts["source"]["char_count"], "partition_chars": chars, "partition_complete": chars == counts["source"]["char_count"], "semantic_coverage_claimed": False}
            counts["warning"] = "المصدر غير مستورد. اختر data/source.mARkdown الأصلي من OpenITI ثم شغّل أمر الاستيراد." if counts["source_missing"] else "الفهرسة اللفظية مرشحة للمراجعة؛ لا تدل على شمول موضوعات الكتاب أو حسم هويات الأعلام."
            return counts

    @app.get("/api/sources")
    def sources():
        with session() as db:
            items = [_source_summary(row) for row in db.execute("SELECT * FROM sources ORDER BY id DESC")]
            return {"items":items,"total":len(items),"page":1,"page_size":len(items)}

    @app.get("/api/sources/{source_id}/text")
    def source_text(source_id: int, start: int = Query(0,ge=0), end: int | None = Query(None,ge=0)):
        with session() as db:
            row = _one(db,"SELECT id,full_text,char_count FROM sources WHERE id=?",(source_id,))
            stop = min(end if end is not None else start+20000, row["char_count"])
            if start > stop or stop-start > 100000:
                raise HTTPException(422,"نطاق القراءة غير صالح أو يتجاوز 100000 محرف")
            return {"source_id":source_id,"source_char_start":start,"source_char_end":stop,"text_original":row["full_text"][start:stop],"total_chars":row["char_count"]}

    @app.get("/api/categories")
    def categories():
        with session() as db:
            return _list(db,"SELECT t.id,t.name,(SELECT COUNT(*) FROM topics c WHERE c.parent_id=t.id) topic_count,(SELECT COUNT(*) FROM topic_hits h JOIN topics c ON c.id=h.topic_id WHERE c.parent_id=t.id AND h.review_status!='rejected') hit_count FROM topics t WHERE t.parent_id IS NULL ORDER BY t.id",page_size=100)

    @app.get("/api/topics")
    def topics(category_id: int | None = None,q: str = Query("",max_length=300),page: int=Query(1,ge=1),page_size: int=Query(100,ge=1,le=100)):
        with session() as db:
            sql = "SELECT t.*,(SELECT COUNT(*) FROM topic_hits h WHERE h.topic_id=t.id AND h.review_status!='rejected') hit_count,(SELECT COUNT(*) FROM topic_hits h WHERE h.topic_id=t.id AND h.review_status='reviewed') reviewed_count FROM topics t WHERE t.parent_id IS NOT NULL"
            args=[]
            if category_id is not None: sql += " AND t.parent_id=?"; args.append(category_id)
            if q:sql+=" AND arabic_normalize(t.name) LIKE ?";args.append("%"+normalize_arabic(q)+"%")
            result = _list(db,sql+" ORDER BY t.id",args,page,page_size)
            return result

    evidence_sql = "SELECT h.*,t.name topic_name,s.source_id,s.biography_id,s.section_id,s.text_original,s.source_char_start context_char_start,COALESCE(b.heading,sc.heading) heading,b.subject_person_id FROM topic_hits h JOIN topics t ON t.id=h.topic_id JOIN segments s ON s.id=h.segment_id LEFT JOIN biographies b ON b.id=s.biography_id JOIN source_sections sc ON sc.id=s.section_id"
    mentions_sql = "SELECT m.*,s.text_original,s.source_char_start context_char_start,COALESCE(b.heading,sc.heading) heading,p.canonical_name FROM person_mentions m JOIN segments s ON s.id=m.segment_id LEFT JOIN biographies b ON b.id=m.biography_id JOIN source_sections sc ON sc.id=m.section_id LEFT JOIN people p ON p.id=m.person_id"

    @app.get("/api/topics/{topic_id}/evidence")
    def topic_evidence(topic_id:int,status:Literal["all","candidate","reviewed","rejected"]="all",page:int=Query(1,ge=1),page_size:int=Query(24,ge=1,le=100)):
        with session() as db:
            topic=_one(db,"SELECT * FROM topics WHERE id=? AND parent_id IS NOT NULL",(topic_id,))
            sql=evidence_sql+" WHERE h.topic_id=?";args=[topic_id]
            if status!="all":sql+=" AND h.review_status=?";args.append(status)
            result=_list(db,sql+" ORDER BY h.id",args,page,page_size);result["topic"]=topic
            return _witness_page_markers(db,result)

    @app.get("/api/biographies")
    def biographies(q:str=Query("",max_length=300),page:int=Query(1,ge=1),page_size:int=Query(24,ge=1,le=100)):
        with session() as db:
            sql="SELECT id,source_id,section_id,subject_person_id,source_ordinal,heading,source_char_start,source_char_end,first_page_marker,last_page_marker,extraction_status FROM biographies"
            args=[]
            if q:sql+=" WHERE normalized_heading LIKE ?";args.append("%"+normalize_arabic(q)+"%")
            return _list(db,sql+" ORDER BY id",args,page,page_size)

    @app.get("/api/biographies/{biography_id}")
    def biography(biography_id:int):
        with session() as db:
            row=_one(db,"SELECT * FROM biographies WHERE id=?",(biography_id,))
            row.pop("normalized_search_text",None);row.pop("normalized_heading",None)
            row["source"]=_source_summary(db.execute("SELECT * FROM sources WHERE id=?",(row["source_id"],)).fetchone())
            row["segments_count"]=db.execute("SELECT COUNT(*) FROM segments WHERE biography_id=?",(biography_id,)).fetchone()[0]
            row["previous_id"]=db.execute("SELECT MAX(id) FROM biographies WHERE source_id=? AND id<?",(row["source_id"],biography_id)).fetchone()[0]
            row["next_id"]=db.execute("SELECT MIN(id) FROM biographies WHERE source_id=? AND id>?",(row["source_id"],biography_id)).fetchone()[0]
            return row

    @app.get("/api/biographies/{biography_id}/segments")
    def biography_segments(biography_id:int,page:int=Query(1,ge=1),page_size:int=Query(100,ge=1,le=100)):
        with session() as db:
            _one(db,"SELECT id FROM biographies WHERE id=?",(biography_id,))
            return _list(db,"SELECT id,source_id,section_id,biography_id,segment_ordinal,text_original,source_char_start,source_char_end,segment_type,segmentation_status,page_marker FROM segments WHERE biography_id=? ORDER BY segment_ordinal",(biography_id,),page,page_size)

    @app.get("/api/sections")
    def sections(page:int=Query(1,ge=1),page_size:int=Query(24,ge=1,le=100)):
        with session() as db:
            return _list(db,"SELECT id,source_id,source_ordinal,heading,section_kind,source_char_start,source_char_end FROM source_sections WHERE section_kind!='biography' ORDER BY id",page=page,page_size=page_size)

    @app.get("/api/sections/{section_id}")
    def section(section_id:int):
        with session() as db:
            row=_one(db,"SELECT * FROM source_sections WHERE id=?",(section_id,))
            row["source"]=_source_summary(db.execute("SELECT * FROM sources WHERE id=?",(row["source_id"],)).fetchone())
            row["previous_id"]=db.execute("SELECT MAX(id) FROM source_sections WHERE source_id=? AND id<?",(row["source_id"],section_id)).fetchone()[0]
            row["next_id"]=db.execute("SELECT MIN(id) FROM source_sections WHERE source_id=? AND id>?",(row["source_id"],section_id)).fetchone()[0]
            return row

    @app.get("/api/people")
    def people(q:str=Query("",max_length=300),page:int=Query(1,ge=1),page_size:int=Query(24,ge=1,le=100)):
        with session() as db:
            sql="SELECT p.*,(SELECT MIN(id) FROM biographies b WHERE b.subject_person_id=p.id) biography_id FROM people p"
            args=[]
            if q:sql+=" WHERE p.normalized_name LIKE ? OR EXISTS(SELECT 1 FROM person_aliases a WHERE a.person_id=p.id AND a.normalized_alias LIKE ?)";args=["%"+normalize_arabic(q)+"%"]*2
            result=_list(db,sql+" ORDER BY p.id",args,page,page_size)
            for row in result["items"]:
                assigned=db.execute("SELECT COUNT(*) FROM person_mentions WHERE person_id=? AND identity_review_status!='rejected'",(row["id"],)).fetchone()[0]
                candidates=db.execute("SELECT COUNT(*) FROM mention_candidates mc JOIN person_mentions m ON m.id=mc.mention_id WHERE mc.person_id=? AND m.person_id IS NULL AND m.identity_review_status!='rejected'",(row["id"],)).fetchone()[0]
                row["mention_count"]=assigned+candidates
            return result

    @app.get("/api/people/{person_id}")
    def person(person_id:int):
        with session() as db:
            row=_one(db,"SELECT * FROM people WHERE id=?",(person_id,))
            return {"person":row,"biographies":[dict(r) for r in db.execute("SELECT id,heading,source_id,source_char_start,source_char_end,first_page_marker,last_page_marker,extraction_status FROM biographies WHERE subject_person_id=?",(person_id,))],"aliases":[dict(r) for r in db.execute("SELECT * FROM person_aliases WHERE person_id=?",(person_id,))]}

    def enrich_mentions(db,result):
        for row in result["items"]:
            row["candidates"]=[dict(p) for p in db.execute("SELECT p.id,p.canonical_name FROM mention_candidates mc JOIN people p ON p.id=mc.person_id WHERE mc.mention_id=? ORDER BY p.id",(row["id"],))]
            row["review_status"]=row["identity_review_status"]
        return _witness_page_markers(db,result)

    @app.get("/api/people/{person_id}/mentions")
    def person_mentions(person_id:int,status:Literal["all","candidate","reviewed","rejected"]="all",page:int=Query(1,ge=1),page_size:int=Query(24,ge=1,le=100)):
        with session() as db:
            _one(db,"SELECT id FROM people WHERE id=?",(person_id,))
            sql=mentions_sql+" WHERE (m.person_id=? OR (m.person_id IS NULL AND EXISTS(SELECT 1 FROM mention_candidates mc WHERE mc.mention_id=m.id AND mc.person_id=?))) AND (b.subject_person_id IS NULL OR b.subject_person_id!=?)"
            args=[person_id,person_id,person_id]
            if status!="all":sql+=" AND m.identity_review_status=?";args.append(status)
            return enrich_mentions(db,_list(db,sql+" ORDER BY m.id",args,page,page_size))

    @app.get("/api/search")
    def search(q:str=Query("",max_length=300),scope:Literal["text","titles","people","topics"]="text",mode:Literal["words","phrase"]="words",page:int=Query(1,ge=1),page_size:int=Query(24,ge=1,le=100)):
        query=fts_query(q,mode=="phrase")
        if not query:return {"items":[],"total":0,"page":page,"page_size":page_size,"query":q}
        if scope=="people":
            result=people(q,page,page_size)
            result["items"]=[dict(row,heading=row["canonical_name"],text_original=row["canonical_name"],match_type="name_candidate") for row in result["items"]]
            return result
        if scope=="topics":
            result=topics(None,q,page,page_size)
            result["items"]=[dict(row,heading=row["name"],text_original=row["search_terms_csv"],match_type="dictionary_candidate",topic_id=row["id"]) for row in result["items"]]
            return result
        with session() as db:
            if scope=="titles":
                sql="SELECT b.id,b.id biography_id,b.section_id,b.source_id,b.heading,SUBSTR(b.full_text,1,400) text_original,b.source_char_start,MIN(b.source_char_end,b.source_char_start+400) source_char_end,b.source_char_end biography_char_end,(LENGTH(b.full_text)>400) preview_truncated,b.first_page_marker page_marker,'title_match' match_type FROM biographies_fts f JOIN biographies b ON b.id=f.biography_id WHERE biographies_fts MATCH ? ORDER BY bm25(biographies_fts)"
                query="normalized_heading : ("+query+")"
            else:
                sql="SELECT s.id,s.id segment_id,s.source_id,s.section_id,s.biography_id,COALESCE(b.heading,sc.heading) heading,s.text_original,s.source_char_start,s.source_char_end,s.page_marker,'keyword_candidate' match_type,s.segmentation_status review_status FROM segments_fts f JOIN segments s ON s.id=f.segment_id LEFT JOIN biographies b ON b.id=s.biography_id JOIN source_sections sc ON sc.id=s.section_id WHERE segments_fts MATCH ? ORDER BY bm25(segments_fts)"
            result=_list(db,sql,(query,),page,page_size);result["query"]=q
            return _witness_page_markers(db,result)

    @app.get("/api/reviews")
    def review_queue(kind:Literal["topics","mentions","segments"]="topics",status:Literal["all","candidate","reviewed","rejected"]="candidate",page:int=Query(1,ge=1),page_size:int=Query(24,ge=1,le=100)):
        with session() as db:
            if kind=="topics":sql=evidence_sql;col="h.review_status"
            elif kind=="mentions":sql=mentions_sql;col="m.identity_review_status"
            else:sql="SELECT s.*,COALESCE(b.heading,sc.heading) heading FROM segments s LEFT JOIN biographies b ON b.id=s.biography_id JOIN source_sections sc ON sc.id=s.section_id";col="s.segmentation_status"
            args=[]
            if status!="all":sql+=" WHERE "+col+"=?";args.append(status)
            result=_list(db,sql+" ORDER BY 1",args,page,page_size)
            if kind=="mentions":
                return enrich_mentions(db,result)
            return _witness_page_markers(db,result)

    def log(db,kind,entity_id,before,after,reviewer,note):
        db.execute("INSERT INTO review_log(entity_type,entity_id,change_summary,reviewer) VALUES (?,?,?,?)",(kind,entity_id,json.dumps({"before":before,"after":after,"note":note},ensure_ascii=False),reviewer_name(reviewer)))

    @app.post("/api/reviews/topic-hits/{hit_id}")
    def review_topic(hit_id:int,payload:Review):
        with session() as db:
            before=_one(db,"SELECT * FROM topic_hits WHERE id=?",(hit_id,))
            segment=_one(db,"SELECT * FROM segments WHERE id=?",(before["segment_id"],))
            a=payload.source_char_start if payload.source_char_start is not None else before["source_char_start"]
            b=payload.source_char_end if payload.source_char_end is not None else before["source_char_end"]
            if not segment["source_char_start"]<=a<b<=segment["source_char_end"]:
                raise HTTPException(422,"حدود الشاهد يجب أن تقع داخل المقطع الأصلي")
            topic_id=payload.topic_id if payload.topic_id is not None else before["topic_id"]
            _one(db,"SELECT id FROM topics WHERE id=? AND parent_id IS NOT NULL",(topic_id,))
            literal=segment["text_original"][a-segment["source_char_start"]:b-segment["source_char_start"]]
            try:
                db.execute("UPDATE topic_hits SET topic_id=?,source_char_start=?,source_char_end=?,evidence_text=?,review_status=?,reviewer_note=? WHERE id=?",(topic_id,a,b,literal,payload.review_status,payload.note,hit_id))
            except sqlite3.IntegrityError:
                raise HTTPException(409,"يوجد وسم بهذا الموضوع لهذا المقطع؛ راجع الوسم الموجود")
            after=_one(db,"SELECT * FROM topic_hits WHERE id=?",(hit_id,))
            log(db,"topic_hit",hit_id,before,after,payload.reviewer,payload.note)
            return after

    @app.post("/api/segments/{segment_id}/topic-hits")
    def manual_topic_hit(segment_id:int,payload:ManualTopicHit):
        with session() as db:
            segment=_one(db,"SELECT * FROM segments WHERE id=?",(segment_id,))
            _one(db,"SELECT id FROM topics WHERE id=? AND parent_id IS NOT NULL",(payload.topic_id,))
            a,b=payload.source_char_start,payload.source_char_end
            if not segment["source_char_start"]<=a<b<=segment["source_char_end"]:
                raise HTTPException(422,"الشاهد اليدوي يجب أن يقع داخل المقطع الأصلي")
            literal=segment["text_original"][a-segment["source_char_start"]:b-segment["source_char_start"]]
            try:
                hit_id=db.execute("INSERT INTO topic_hits(topic_id,segment_id,evidence_text,source_char_start,source_char_end,matched_phrase,method,review_status,reviewer_note) VALUES (?,?,?,?,?,?,?,?,?)",(payload.topic_id,segment_id,literal,a,b,literal,"manual",payload.review_status,payload.note)).lastrowid
            except sqlite3.IntegrityError:
                raise HTTPException(409,"يوجد وسم لهذا الموضوع والمقطع؛ عدّل الوسم الموجود")
            after=_one(db,"SELECT * FROM topic_hits WHERE id=?",(hit_id,))
            log(db,"topic_hit",hit_id,None,after,payload.reviewer,payload.note)
            return after

    @app.post("/api/reviews/mentions/{mention_id}")
    def review_mention(mention_id:int,payload:Review):
        with session() as db:
            before=_one(db,"SELECT * FROM person_mentions WHERE id=?",(mention_id,))
            person_id=payload.person_id if "person_id" in payload.model_fields_set else before["person_id"]
            if payload.review_status=="reviewed" and not person_id:
                raise HTTPException(422,"اختر هوية العلم قبل اعتماد المطابقة")
            if person_id:
                person=_one(db,"SELECT * FROM people WHERE id=?",(person_id,))
                if person["source_id"]!=before["source_id"]:
                    raise HTTPException(422,"الهوية من مصدر آخر؛ يلزم ربط مصادر محقق")
                owner=db.execute("SELECT subject_person_id FROM biographies WHERE id=?",(before["biography_id"],)).fetchone()
                if owner and owner[0]==person_id:raise HTTPException(422,"هذا صاحب الترجمة؛ ليس موضع ذكر في ترجمة غيره")
            segment=_one(db,"SELECT * FROM segments WHERE id=?",(before["segment_id"],))
            a=payload.source_char_start if payload.source_char_start is not None else before["source_char_start"]
            b=payload.source_char_end if payload.source_char_end is not None else before["source_char_end"]
            if not segment["source_char_start"]<=a<b<=segment["source_char_end"]:
                raise HTTPException(422,"حدود ذكر العلم يجب أن تقع داخل المقطع الأصلي")
            literal=segment["text_original"][a-segment["source_char_start"]:b-segment["source_char_start"]]
            db.execute("UPDATE person_mentions SET person_id=?,identity_review_status=?,reviewer_note=?,source_char_start=?,source_char_end=?,mention_literal=?,evidence_text=? WHERE id=?",(person_id,payload.review_status,payload.note,a,b,literal,literal,mention_id))
            after=_one(db,"SELECT * FROM person_mentions WHERE id=?",(mention_id,))
            log(db,"person_mention",mention_id,before,after,payload.reviewer,payload.note)
            return after

    @app.post("/api/reviews/segments/{segment_id}")
    def review_segment(segment_id:int,payload:SegmentReview):
        with session() as db:
            before=_one(db,"SELECT * FROM segments WHERE id=?",(segment_id,))
            section=_one(db,"SELECT * FROM source_sections WHERE id=?",(before["section_id"],))
            a,b=payload.source_char_start,payload.source_char_end
            if not section["source_char_start"]<=a<b<=section["source_char_end"]:
                raise HTTPException(422,"حدود المقطع يجب أن تبقى داخل قسم المصدر الأصلي")
            outside=db.execute("SELECT COUNT(*) FROM topic_hits WHERE segment_id=? AND (source_char_start<? OR source_char_end>?)",(segment_id,a,b)).fetchone()[0]+db.execute("SELECT COUNT(*) FROM person_mentions WHERE segment_id=? AND (source_char_start<? OR source_char_end>?)",(segment_id,a,b)).fetchone()[0]
            if outside:raise HTTPException(409,"الحدود تستبعد شواهد مرتبطة؛ وسّع الحدود أو راجع الشواهد أولًا")
            literal=section["full_text"][a-section["source_char_start"]:b-section["source_char_start"]]
            normalized=normalize_arabic(literal)
            marker=db.execute("SELECT raw_marker FROM page_markers WHERE source_id=? AND source_char_offset>=? ORDER BY source_char_offset LIMIT 1",(before["source_id"],a)).fetchone()
            page_marker=marker[0] if marker and marker[0]!="PageV00P000" else None
            db.execute("UPDATE segments SET source_char_start=?,source_char_end=?,text_original=?,normalized_search_text=?,segment_type=?,segmentation_status='reviewed',page_marker=? WHERE id=?",(a,b,literal,normalized,payload.segment_type,page_marker,segment_id))
            db.execute("DELETE FROM segments_fts WHERE segment_id=?",(segment_id,))
            db.execute("INSERT INTO segments_fts(segment_id,normalized_search_text) VALUES (?,?)",(segment_id,normalized))
            after=_one(db,"SELECT * FROM segments WHERE id=?",(segment_id,))
            log(db,"segment",segment_id,before,after,payload.reviewer,payload.note)
            return after

    @app.get("/api/review-log")
    def review_log(page:int=Query(1,ge=1),page_size:int=Query(24,ge=1,le=100)):
        with session() as db:return _list(db,"SELECT * FROM review_log ORDER BY id DESC",page=page,page_size=page_size)

    return app

app = create_app()
