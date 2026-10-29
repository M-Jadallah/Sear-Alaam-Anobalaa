"""Read-only hybrid retrieval with one isolated CPU inference worker."""
from __future__ import annotations

import asyncio
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
import copy
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import re
import sqlite3
import time
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from .normalization import normalize_arabic
from .semantic import (DEFAULT_DB, DEFAULT_SEMANTIC_DIR, canonical_connection, semantic_status,
                       load_models, file_sha256)
from .semantic import model_artifact_stamps, DIMENSION, VECTOR_DTYPE, QUERY_PREFIX, PROFILE_ID
from .smart_interpret import interpret_query, query_terms

LEXICAL_CANDIDATES = 100
VECTOR_CANDIDATES = 100
RRF_K = 60
RERANK_CANDIDATES = 40
CACHE_SECONDS = 15 * 60
CACHE_LIMIT = 64
RERANK_BATCH = max(1, min(8, int(os.environ.get("SIYAR_RERANK_BATCH", "8"))))
_CACHE = {}
_MODEL_CACHE = None
_VECTOR_CACHE = {}
_PARAGRAPH_VECTOR_CACHE = {}
_EXECUTOR = None
SearchTerm = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]


class MeaningPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str | None = Field(None, max_length=100)
    label: str | None = Field(None, max_length=300)
    meaning: str = Field("", max_length=2000)
    include_terms: list[SearchTerm] = Field(default_factory=list, max_length=16)
    expansion_terms: list[SearchTerm] = Field(default_factory=list, max_length=16)
    exclude_terms: list[SearchTerm] = Field(default_factory=list, max_length=16)
    topic_ids: list[int] = Field(default_factory=list, max_length=16)
    topic_mode: Literal['boost', 'filter'] = 'boost'
    reviewed_only: bool = False
    pilot_only: bool = False
    batch_id: str | None = Field(None,min_length=1,max_length=100)
    query_text: str | None = Field(None, max_length=2000)
    uncertainty: str | None = Field(None, max_length=1000)

    @field_validator("meaning")
    @classmethod
    def reject_whitespace_meaning(cls, value):
        # The empty default preserves the existing fallback to q. An explicit
        # whitespace-only edit must not turn into an empty semantic query.
        if value and not value.strip():
            raise ValueError("اكتب معنى للبحث؛ لا تكفي المسافات وحدها.")
        return value


class InterpretRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    q: str = Field(min_length=1, max_length=2000)


class SmartRequest(InterpretRequest):
    plan: MeaningPlan | None = None
    page: int = Field(1, ge=1)
    page_size: int = Field(10, ge=1, le=10)


def _bound_terms(values):
    return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))[:16]


def _topic_expansions(db_path, topic_ids):
    values = []
    with canonical_connection(db_path) as db:
        for topic_id in topic_ids[:16]:
            row = db.execute("SELECT name,search_terms_csv FROM topics WHERE id=? AND parent_id IS NOT NULL", (topic_id,)).fetchone()
            if row:
                values.extend([row["name"], *(row["search_terms_csv"] or "").split(",")])
                from .pilot import has_pilot
                if has_pilot(db):
                    values.extend(alias[0] for alias in db.execute('SELECT alias FROM pilot_topic_aliases WHERE topic_id=?',(topic_id,)))
    return _bound_terms(values)[:12]


def _lexical_query(q, plan, topic_terms=()):
    # OR across useful original words plus explicit phrases provides recall for
    # natural questions; exclusions are applied literally after retrieval.
    alternatives = query_terms(q)[:12]
    alternatives.extend(normalize_arabic(value) for value in _bound_terms(plan.get("expansion_terms", [])))
    alternatives.extend(normalize_arabic(value) for value in topic_terms)
    alternatives = list(dict.fromkeys(value for value in alternatives if value))[:24]
    optional = " OR ".join('"' + value.replace('"', '""') + '"' for value in alternatives)
    mandatory = [normalize_arabic(value) for value in _bound_terms(plan.get("include_terms", []))]
    clauses = ['"' + value.replace('"', '""') + '"' for value in mandatory if value]
    if optional and not clauses:
        clauses.insert(0, "(" + optional + ")")
    return " AND ".join(clauses)


def _attach_topic_context(target, row):
    """Preserve an authored qualification without inventing a second rank vote."""
    if not row.get('topic_audit_id'):
        return
    qualified={'criticism','contested','accusation','negated'}
    if target.get('stance') in qualified and row.get('stance') not in qualified:
        return
    for field in ('topic_audit_id','topic_id','topic_name','topic_review_status','agent_decision','human_status','subject_scope','stance','rationale','relation_type','context_char_start','context_char_end','context_text'):
        if field in row:target[field]=row[field]


def rrf_fuse(lexical, semantic, k=RRF_K, topical=None):
    fused = {}
    for kind, rows in (("lexical", lexical), ("semantic", semantic), ("topic", topical or [])):
        seen, rank = set(), 0
        for row in rows:
            key = (row["source_id"], row["source_char_start"], row["source_char_end"])
            # Context and paragraph retrieval can return the exact same span.
            # Each channel contributes once, with ranks over distinct spans;
            # lexical and semantic votes remain independent of one another.
            if key in seen:
                continue
            seen.add(key)
            rank += 1
            target = fused.setdefault(key, {**row, "scores": {"rrf": 0.0}})
            target["scores"][kind + "_rank"] = rank
            target["scores"]["rrf"] += 1.0 / (k + rank)
            if kind == "semantic":
                target["scores"]["cosine"] = row.get("cosine")
            if kind == 'topic':
                _attach_topic_context(target,row)
    return sorted(fused.values(), key=lambda row: row["scores"]["rrf"], reverse=True)


def balanced_pool(lexical, semantic, morphological, topical=(), maximum=100, seed=20):
    """Reserve recall from each retrieval channel before final cross-encoding."""
    channels = {"lexical": lexical, "morphology": morphological,
                "context": [row for row in semantic if row.get("granularity") != "paragraph"],
                "paragraph": [row for row in semantic if row.get("granularity") == "paragraph"]}
    if topical:
        channels["topic"] = topical
    fused, reserved = {}, set()
    for kind, rows in channels.items():
        seen = set()
        for row in rows:
            key = (row["source_id"], row["source_char_start"], row["source_char_end"])
            if key in seen:
                continue
            seen.add(key)
            rank = len(seen)
            if rank > 40:
                break
            target = fused.setdefault(key, {**row, "scores": {"rrf": 0.0}})
            target["scores"]["rrf"] += 1 / (RRF_K + rank)
            target["scores"][kind + "_rank"] = rank
            if kind in ("context", "paragraph"):
                target["scores"]["semantic_rank"] = min(rank, target["scores"].get("semantic_rank", rank))
                target["scores"]["cosine"] = row.get("cosine")
            if kind == "topic":
                _attach_topic_context(target, row)
            if rank <= seed:
                reserved.add(key)
    ranked = sorted(fused.items(), key=lambda pair: pair[1]["scores"]["rrf"], reverse=True)
    selected = [pair for pair in ranked if pair[0] in reserved][:maximum]
    keys = {pair[0] for pair in selected}
    for pair in ranked:
        if len(selected) >= maximum:
            break
        if pair[0] not in keys:
            selected.append(pair); keys.add(pair[0])
    return [row for _, row in selected]


def deduplicate_overlaps(rows, threshold=0.70):
    selected = []
    for row in rows:
        duplicate = False
        for previous in selected:
            if row["source_id"] != previous["source_id"]:
                continue
            intersection = max(0, min(row["source_char_end"], previous["source_char_end"]) - max(row["source_char_start"], previous["source_char_start"]))
            denominator = min(row["source_char_end"] - row["source_char_start"], previous["source_char_end"] - previous["source_char_start"])
            if denominator and intersection / denominator >= threshold:
                if row.get('topic_audit_id'):
                    # Keep the retrieved literal window and its ranking. The
                    # larger audited context must survive deduplication too.
                    _attach_topic_context(previous,row)
                    if row.get('scores',{}).get('topic_rank'):
                        previous['scores']['topic_rank']=row['scores']['topic_rank']
                duplicate = True
                break
        if not duplicate:
            selected.append(row)
    return selected


def _exclude(rows, plan):
    phrases = [normalize_arabic(value) for value in _bound_terms(plan.get("exclude_terms", []))]
    return [row for row in rows if not any(re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", normalize_arabic(row["clean_text"])) for term in phrases if term)]


def _include(rows, plan):
    phrases = [normalize_arabic(value) for value in _bound_terms(plan.get("include_terms", []))]
    return [row for row in rows if all(re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", normalize_arabic(row["clean_text"])) for term in phrases if term)]


def _lexical_candidates(db_path, query, status):
    if not query:
        return []
    if status.get("ready"):
        _vectors(status)
        with sqlite3.connect(Path(status["directory"]) / "windows.sqlite3") as db:
            db.row_factory = sqlite3.Row
            scope=status.get('pilot_biography_ids')
            if scope == []: return []
            clause = ' AND w.biography_id IN ('+','.join('?' for _ in scope)+')' if scope is not None else ''
            return [dict(row) for row in db.execute("SELECT w.* FROM windows_fts f JOIN windows w ON w.id=f.window_id WHERE windows_fts MATCH ?"+clause+" ORDER BY bm25(windows_fts) LIMIT ?", (query,*(scope or []),LEXICAL_CANDIDATES))]
    from .reading import build_reading_view
    with canonical_connection(db_path) as db:
        scope=status.get('pilot_biography_ids')
        if scope == []: return []
        clause=' AND s.biography_id IN ('+','.join('?' for _ in scope)+')' if scope is not None else ''
        rows = db.execute("SELECT s.id,s.source_id,s.section_id,s.biography_id,s.source_char_start,s.source_char_end,s.text_original,COALESCE(b.heading,sc.heading) heading FROM segments_fts f JOIN segments s ON s.id=f.segment_id JOIN source_sections sc ON sc.id=s.section_id LEFT JOIN biographies b ON b.id=s.biography_id WHERE segments_fts MATCH ?"+clause+" ORDER BY bm25(segments_fts) LIMIT ?", (query,*(scope or []),LEXICAL_CANDIDATES)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            view = build_reading_view(item["text_original"], item["source_char_start"], starts_at_line_start=True)
            item["clean_text"] = view["plain_text"]
            result.append(item)
        return result


def _models(model_fingerprint=None):
    global _MODEL_CACHE
    key = (model_fingerprint, model_artifact_stamps())
    if _MODEL_CACHE is None or _MODEL_CACHE[0] != key:
        _MODEL_CACHE = (key, load_models())
    return _MODEL_CACHE[1]


def _vectors(status):
    import numpy as np
    directory = Path(status["directory"])
    stamps = tuple((path.stat().st_size, path.stat().st_mtime_ns) for path in (directory / "vectors.npy", directory / "windows.sqlite3", directory / "manifest.json"))
    key = (status["fingerprint"], status.get("model_fingerprint"), stamps)
    if key not in _VECTOR_CACHE:
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        if not manifest.get("complete") or file_sha256(directory / "vectors.npy") != manifest["vectors_sha256"] or file_sha256(directory / "windows.sqlite3") != manifest["windows_sha256"]:
            raise RuntimeError("فشل تحقق سلامة الفهرس الدلالي؛ لا تستخدم نتائجه قبل إعادة البناء.")
        vectors = np.load(directory / "vectors.npy", mmap_mode="r", allow_pickle=False)
        if vectors.shape != (manifest["window_count"], DIMENSION) or vectors.dtype != np.dtype(VECTOR_DTYPE):
            raise RuntimeError("أبعاد الفهرس الدلالي أو نوعه غير صالح.")
        _VECTOR_CACHE.clear()
        _VECTOR_CACHE[key] = vectors
    return _VECTOR_CACHE[key]


def _semantic_candidates(query, status):
    import numpy as np
    embed, _ = _models(status.get("model_fingerprint"))
    inputs = embed.tokenizer(QUERY_PREFIX + query, add_special_tokens=True, truncation=False)
    if len(inputs["input_ids"]) > 512:
        raise ValueError("الاستعلام أطول من حد النموذج المحلي. اختصر خطة البحث؛ صياغتك الأصلية محفوظة ولم تُبتر.")
    vector = embed.encode([QUERY_PREFIX + query], normalize_embeddings=True, show_progress_bar=False, convert_to_numpy=True)[0]
    vectors = _vectors(status)
    from .vector_storage import cosine_scores
    scores = cosine_scores(vectors, vector)
    scope=status.get('pilot_biography_ids')
    def scoped_indices(directory, values, count):
        if scope is None:
            eligible=np.arange(len(values))
        elif not scope:
            return []
        else:
            with sqlite3.connect(Path(directory)/'windows.sqlite3') as scoped_db:
                eligible=np.array([row[0]-1 for row in scoped_db.execute('SELECT id FROM windows WHERE biography_id IN ('+','.join('?' for _ in scope)+')',scope)],dtype=np.int64)
        count=min(count,len(eligible))
        if not count:return []
        indices=eligible[np.argpartition(values[eligible],-count)[-count:]]
        return indices[np.argsort(values[indices])[::-1]]
    paragraph = status.get("paragraph_status",{})
    use_paragraphs = paragraph.get("ready",False)
    count = min(50 if use_paragraphs else VECTOR_CANDIDATES, len(scores))
    if not count:
        return []
    indices = scoped_indices(status['directory'],scores,count)
    rows = []
    with sqlite3.connect(Path(status["directory"]) / "windows.sqlite3") as db:
        db.row_factory = sqlite3.Row
        for index in indices:
            row = dict(db.execute("SELECT * FROM windows WHERE id=?", (int(index) + 1,)).fetchone())
            row["cosine"] = float(scores[index])
            row["granularity"] = "context"
            rows.append(row)
    if use_paragraphs:
        paragraph_vectors = _paragraph_vectors(paragraph)
        paragraph_scores = cosine_scores(paragraph_vectors, vector)
        take = min(50,len(paragraph_scores))
        selected = scoped_indices(paragraph['directory'],paragraph_scores,take)
        paragraph_rows=[]
        with sqlite3.connect(Path(paragraph["directory"])/"windows.sqlite3") as db:
            db.row_factory=sqlite3.Row
            for index in selected:
                row=dict(db.execute("SELECT * FROM windows WHERE id=?",(int(index)+1,)).fetchone())
                row["id"]=-row["id"]
                row["granularity"]="paragraph"
                row["cosine"]=float(paragraph_scores[index])
                paragraph_rows.append(row)
        # Different granularities have different cosine distributions. Equal
        # per-granularity budgets and rank interleaving avoid a length bias.
        combined=[]
        for index in range(max(len(rows),len(paragraph_rows))):
            if index<len(rows):combined.append(rows[index])
            if index<len(paragraph_rows):combined.append(paragraph_rows[index])
        rows=combined
    return rows


def _paragraph_vectors(status):
    import numpy as np
    directory=Path(status["directory"])
    stamps=tuple((path.stat().st_size,path.stat().st_mtime_ns) for path in (directory/"vectors.npy",directory/"windows.sqlite3",directory/"manifest.json"))
    key=(status["fingerprint"],stamps)
    if key not in _PARAGRAPH_VECTOR_CACHE:
        manifest=json.loads((directory/"manifest.json").read_text(encoding="utf-8"))
        if not manifest.get("complete") or file_sha256(directory/"vectors.npy")!=manifest["vectors_sha256"] or file_sha256(directory/"windows.sqlite3")!=manifest["windows_sha256"]:
            raise RuntimeError("فشل تحقق سلامة فهرس الفقرات؛ أعد تجهيزه.")
        vectors=np.load(directory/"vectors.npy",mmap_mode="r",allow_pickle=False)
        if vectors.shape!=(manifest["window_count"],DIMENSION) or vectors.dtype!=np.dtype(VECTOR_DTYPE):raise RuntimeError("أبعاد متجهات الفقرات غير صالحة.")
        _PARAGRAPH_VECTOR_CACHE.clear();_PARAGRAPH_VECTOR_CACHE[key]=vectors
    return _PARAGRAPH_VECTOR_CACHE[key]


def _rerank(query, rows, model_fingerprint=None):
    if not rows:
        return rows
    _, reranker = _models(model_fingerprint)
    pairs = []
    for row in rows:
        length = len(reranker.tokenizer(query, row["clean_text"], add_special_tokens=True, truncation=False)["input_ids"])
        if length > 512:
            # Keep the literal candidate intact. The architecture cannot consume
            # this pair; mark it unreranked rather than silently trimming text.
            row["scores"]["rerank_skipped"] = "pair_exceeds_512_tokens"
        else:
            pairs.append((row, (query, row["clean_text"])))
    if pairs:
        predictions = reranker.predict([pair for _, pair in pairs], batch_size=RERANK_BATCH, show_progress_bar=False)
        for (row, _), score in zip(pairs, predictions):
            row["scores"]["rerank"] = float(score)
    return sorted(rows, key=lambda row: (row["scores"].get("rerank", float("-inf")), row["scores"]["rrf"]), reverse=True)


def _literal_results(rows, db_path, hybrid):
    from .reading import build_reading_view
    result = []
    with canonical_connection(db_path) as db:
        sources = {row["id"]: dict(row) for row in db.execute("SELECT id,sha256,full_text FROM sources")}
        for row in rows:
            item = {key: value for key, value in row.items() if key not in ("clean_text", "text_original", "cosine", "source_sha256", "token_count")}
            source = sources[item["source_id"]]
            if row.get("source_sha256") and row["source_sha256"] != source["sha256"]:
                raise RuntimeError("تغيرت بصمة المصدر بين الاسترجاع وقراءة الشاهد؛ أعد البحث بعد تحديث الفهرس.")
            a, b = item["source_char_start"], item["source_char_end"]
            literal = source["full_text"][a:b]
            if not literal or not 0 <= a < b <= len(source["full_text"]):
                raise RuntimeError("شاهد خارج حدود المصدر؛ رفضت النتيجة.")
            item["text_original"] = literal
            item["evidence_text"] = literal
            item["source_sha256"] = source["sha256"]
            item["reading_view"] = build_reading_view(literal, a, starts_at_line_start=(a == 0 or source["full_text"][a-1] == "\n"))
            item["match_type"] = "semantic_candidate" if hybrid and item["scores"].get("semantic_rank") else "keyword_candidate"
            item["review_status"] = "candidate"
            if item["match_type"] == "semantic_candidate":
                item["reason"] = ("اقتراح اجتمع فيه الاسترجاع الدلالي واللفظي" if item["scores"].get("lexical_rank") else "اقتراح استرجاع دلالي") + "؛ الترتيب لا يثبت صحة المعنى أو الإسناد."
            else:
                item["reason"] = ("اقتراح لفظي باشتقاق صيغ الكلمات" if item["scores"].get("morphology_rank") else "استرجاع لفظي بالألفاظ المحددة") + "؛ الدلالة تحتاج مراجعة السياق."
            methods=[]
            if item['scores'].get('lexical_rank'):methods.append('lexical')
            if item['scores'].get('semantic_rank'):methods.append('semantic')
            if item['scores'].get('morphology_rank'):methods.append('morphology')
            if item['scores'].get('topic_rank'):
                methods.append('topic_candidate')
                item['match_type']='topic_candidate'
                item['reason']='ترشيح من فهرس الموضوعات: '+item.get('topic_name','')+'؛ '+item.get('rationale','')+' · '+('اعتماد بشري للوسم' if item.get('topic_review_status')=='reviewed' else 'مراجعة نصية بواسطة وكيل، بانتظار اعتمادك')
            item['retrieval_methods']=methods
            def page_at(offset):
                previous = db.execute("SELECT raw_marker,source_char_offset,source_char_end FROM page_markers WHERE source_id=? AND source_char_offset<=? ORDER BY source_char_offset DESC LIMIT 1", (item["source_id"], offset)).fetchone()
                if previous and offset < previous["source_char_end"]:
                    return previous
                return db.execute("SELECT raw_marker,source_char_offset,source_char_end FROM page_markers WHERE source_id=? AND source_char_offset>=? ORDER BY source_char_offset LIMIT 1", (item["source_id"], offset)).fetchone()
            marker, last_marker = page_at(a), page_at(b - 1)
            item["first_page_marker"] = marker[0] if marker else None
            item["last_page_marker"] = last_marker[0] if last_marker else None
            item["page_marker_crossed"] = (marker["source_char_offset"] if marker else None) != (last_marker["source_char_offset"] if last_marker else None)
            item["page_marker"] = marker[0] if marker and marker[0] != "PageV00P000" else None
            item["page_reference_status"] = "raw_unverified"
            # Context can be read separately, but rejected/approved tags are not
            # turned into automatic scientific assertions about this new search.
            item["reader_url"] = f"/reader/{item['biography_id']}?start={a}&end={b}" if item.get("biography_id") else f"/sections/{item['section_id']}?start={a}&end={b}"
            if item.get('topic_audit_id'):
                ca,cb=item.get('context_char_start',a),item.get('context_char_end',b)
                bio=db.execute('SELECT source_id,source_char_start,source_char_end FROM biographies WHERE id=?',(item['biography_id'],)).fetchone()
                if not bio or bio['source_id']!=item['source_id'] or not bio['source_char_start']<=ca<cb<=bio['source_char_end']:
                    raise RuntimeError('سياق موضوعي خارج الترجمة؛ رفضت النتيجة')
                item['context_text']=source['full_text'][ca:cb]
                item['context_reading_view']=build_reading_view(item['context_text'],ca,starts_at_line_start=(ca==0 or source['full_text'][ca-1]=='\n'))
                item['reader_url']=f"/reader/{item['biography_id']}?start={ca}&end={cb}"
            result.append(item)
    return result


def _cache_revision(db_path, status):
    with canonical_connection(db_path) as db:
        review = db.execute("SELECT COALESCE(MAX(id),0) FROM review_log").fetchone()[0]
        dictionary = [tuple(row) for row in db.execute("SELECT id,name,search_terms_csv,dictionary_status FROM topics ORDER BY id")]
        from .pilot import pilot_revision
        pilot=pilot_revision(db)
    artifacts = None
    if status.get("directory"):
        directory = Path(status["directory"])
        artifacts = [(path.stat().st_size, path.stat().st_mtime_ns) for path in (directory / "vectors.npy", directory / "windows.sqlite3", directory / "manifest.json") if path.exists()]
    paragraph_artifacts=None
    if status.get("paragraph_status",{}).get("directory"):
        directory=Path(status["paragraph_status"]["directory"])
        paths = [directory/"vectors.npy",directory/"windows.sqlite3",directory/"manifest.json"]
        if PROFILE_ID != "legacy-e5":
            paths.extend([directory/"morphology.json", *directory.glob("morphology-*.sqlite3")])
        paragraph_artifacts=[(str(path.name),path.stat().st_size,path.stat().st_mtime_ns) for path in paths if path.exists()]
    dictionary_fingerprint = hashlib.sha256(json.dumps(dictionary, ensure_ascii=False).encode()).hexdigest()
    return {"index": status.get("fingerprint"), "profile": PROFILE_ID, "model_fingerprint": status.get("model_fingerprint"), "model_artifacts": model_artifact_stamps(), "dictionary": dictionary_fingerprint, "artifacts": artifacts,"paragraph_artifacts":paragraph_artifacts, "status": status["status"], "sources": status["sources"], "review_log": review,'pilot':pilot}


def smart_search(payload, db_path=DEFAULT_DB, semantic_dir=None):
    """Pure synchronous worker entry point, also callable by evaluation tools."""
    started = time.perf_counter()
    request = SmartRequest.model_validate(payload)
    q = request.q.strip()
    if not q:
        raise ValueError("اكتب طلبًا للبحث.")
    plan = request.plan.model_dump() if request.plan else {"meaning": q, "include_terms": [], "exclude_terms": [], "topic_ids": []}
    status = semantic_status(db_path, semantic_dir)
    revision = _cache_revision(db_path, status)
    cache_key = hashlib.sha256(json.dumps({"db": str(Path(db_path).resolve()), "q": q, "plan": plan, "revision": revision}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    cached = _CACHE.get(cache_key)
    if cached and time.monotonic() - cached[0] < CACHE_SECONDS:
        all_items, metadata = copy.deepcopy(cached[1]), copy.deepcopy(cached[2])
        cache_hit = True
    else:
        integrity_warning = None
        if status.get("ready"):
            try:
                _vectors(status)
            except Exception as error:
                integrity_warning = "فشل تحقق الفهرس الدلالي؛ عُرض الفهرس اللفظي الأصلي فقط. " + str(error)
                status = {**status, "ready": False, "status": "stale", "reason": integrity_warning}
        if status.get("ready") and status.get("paragraph_status",{}).get("ready"):
            try:
                _paragraph_vectors(status["paragraph_status"])
            except Exception as error:
                integrity_warning="فهرس الفقرات غير صالح؛ استخدم الفهرس السياقي المكتمل وحده. "+str(error)
                status={**status,"paragraph_status":{**status["paragraph_status"],"ready":False,"status":"stale"},"semantic_granularity":"context_only"}
        topic_terms = _topic_expansions(db_path, plan.get("topic_ids", []))
        from .pilot import topic_candidates, search_scope
        biography_scope,topic_spans=search_scope(db_path,plan)
        status={**status,'pilot_biography_ids':biography_scope}
        def scope_rows(rows):
            return [row for row in rows if (biography_scope is None or row.get('biography_id') in biography_scope) and
                    (topic_spans is None or any(row['source_id']==source and row['source_char_start']<b and row['source_char_end']>a for source,a,b in topic_spans))]
        selected_meaning = (plan.get("meaning") or q).strip()
        lexical_query = _lexical_query(selected_meaning, plan, topic_terms)
        lexical = scope_rows(_include(_exclude(_lexical_candidates(db_path, lexical_query, status), plan), plan))
        topical=scope_rows(_include(_exclude(topic_candidates(db_path,lexical_query,plan),plan),plan))
        semantic, morphological, warning = [], [], integrity_warning
        # An edited description is authoritative. The original request remains
        # returned as query for audit; it must not silently override the edit.
        query_text = selected_meaning
        hints = _bound_terms([*plan.get("include_terms", []), *plan.get("expansion_terms", []), *topic_terms])
        if hints:
            query_text += "\n" + "، ".join(hints)
        hybrid = bool(status.get("ready"))
        if hybrid:
            try:
                semantic = scope_rows(_include(_exclude(_semantic_candidates(query_text, status), plan), plan))
                if PROFILE_ID != "legacy-e5":
                    from .morphology import candidates as morphology_candidates
                    morphological = scope_rows(_include(_exclude(morphology_candidates(selected_meaning, status), plan), plan))
            except ValueError:
                raise
            except Exception as error:
                hybrid = False
                semantic, morphological = [], []
                warning = "تعذر الاستدلال الدلالي المحلي؛ عُرض البحث اللفظي فقط. " + str(error)
        balanced = hybrid and PROFILE_ID != "legacy-e5"
        pool_limit = 100 if balanced else RERANK_CANDIDATES
        if balanced:
            # The manually curated topic pilot covers a small part of the book.
            # It supplies candidates when explicitly selected; otherwise only
            # attach its qualification to a retrieved overlapping witness.
            explicit_topics = topical if plan.get("topic_ids") or plan.get("pilot_only") or plan.get("reviewed_only") or plan.get("batch_id") else []
            fused = balanced_pool(lexical, semantic, morphological, explicit_topics, maximum=pool_limit)
        else:
            fused = deduplicate_overlaps(rrf_fuse(lexical, semantic, topical=topical))[:pool_limit]
        reranked = 0
        if hybrid:
            try:
                fused = _rerank(query_text, fused, status.get("model_fingerprint"))
                reranked = sum("rerank" in row["scores"] for row in fused)
            except Exception as error:
                warning = "عمل الاسترجاع الدلالي وتعذر إعادة الترتيب؛ الترتيب الحالي RRF. " + str(error)
        if balanced:
            fused = deduplicate_overlaps(fused)
            for row in fused:
                for topic in topical:
                    intersection = min(row["source_char_end"], topic["source_char_end"]) - max(row["source_char_start"], topic["source_char_start"])
                    minimum = min(row["source_char_end"] - row["source_char_start"], topic["source_char_end"] - topic["source_char_start"])
                    if row["source_id"] == topic["source_id"] and minimum > 0 and intersection / minimum >= 0.7:
                        _attach_topic_context(row, topic)
        all_items = _literal_results(fused, db_path, hybrid)
        metadata = {"mode": "hybrid" if hybrid else "lexical_only", "semantic_status": status,
            "interpretation": plan, "warning": warning or (None if hybrid else status["reason"]),
            "ranking": {"lexical_candidates": len(lexical), "semantic_candidates": len(semantic), "morphology_candidates": len(morphological), 'topic_candidates':len(topical), "reranked": reranked,
                        "rrf_k": RRF_K, "dedup_overlap": 0.70, "result_pool_limit": pool_limit},
            "total_scope": "retrieved_candidates_not_book_coverage"}
        _CACHE[cache_key] = (time.monotonic(), copy.deepcopy(all_items), copy.deepcopy(metadata))
        for old in list(_CACHE):
            if time.monotonic() - _CACHE[old][0] >= CACHE_SECONDS:
                del _CACHE[old]
        while len(_CACHE) > CACHE_LIMIT:
            del _CACHE[next(iter(_CACHE))]
        cache_hit = False
    begin = (request.page - 1) * request.page_size
    return {**metadata, "items": all_items[begin:begin+request.page_size], "total": len(all_items),
            "page": request.page, "page_size": request.page_size, "query": q, "cache_hit": cache_hit,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 2)}


def _worker(payload, db_path, semantic_dir):
    try:
        return {"result": smart_search(payload, db_path, semantic_dir)}
    except ValueError as error:
        return {"error": str(error), "status": 422}
    except Exception as error:
        return {"error": str(error), "status": 503}


def shutdown_search_worker(terminate=False):
    global _EXECUTOR
    if _EXECUTOR is not None:
        if terminate:
            # Python 3.14 exposes terminate_workers; older supported versions
            # need explicit process termination so timed-out work cannot pile up.
            if hasattr(_EXECUTOR, "terminate_workers"):
                _EXECUTOR.terminate_workers()
                _EXECUTOR = None
                return
            for process in tuple(getattr(_EXECUTOR, "_processes", {}).values()):
                process.terminate()
        _EXECUTOR.shutdown(wait=False, cancel_futures=True)
        _EXECUTOR = None


def smart_router(db_path, semantic_dir=None):
    router = APIRouter()
    pending = 0

    @router.post("/api/search/interpret")
    def interpret(payload: InterpretRequest):
        try:
            return interpret_query(payload.q, db_path)
        except ValueError as error:
            raise HTTPException(422, str(error))

    @router.get("/api/semantic/status")
    def status():
        return semantic_status(db_path, semantic_dir)

    @router.post("/api/search/smart")
    async def search(payload: SmartRequest):
        global _EXECUTOR
        nonlocal pending
        if pending >= 4:
            raise HTTPException(429, "البحث مشغول؛ أعد المحاولة بعد قليل.", headers={"Retry-After": "5"})
        if _EXECUTOR is None:
            _EXECUTOR = ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn"))
        executor = _EXECUTOR
        pending += 1
        try:
            future = asyncio.get_running_loop().run_in_executor(executor, _worker, payload.model_dump(), str(db_path), str(semantic_dir) if semantic_dir else None)
        except BrokenProcessPool as error:
            pending -= 1
            if _EXECUTOR is executor:
                shutdown_search_worker()
            raise HTTPException(503, "تعطل عامل البحث؛ أعد المحاولة.") from error
        def finished(_):
            nonlocal pending
            pending -= 1
        future.add_done_callback(finished)
        try:
            result = await asyncio.wait_for(asyncio.shield(future), timeout=float(os.environ.get("SIYAR_SEARCH_TIMEOUT", "60")))
        except asyncio.TimeoutError as error:
            if _EXECUTOR is executor:
                shutdown_search_worker(terminate=True)
            raise HTTPException(504, "انتهت مهلة البحث. يبقى البحث بالكلمات متاحًا.") from error
        except BrokenProcessPool as error:
            # Other queued requests can still refer to this failed pool. Do not
            # let their later exceptions shut down a newly created worker.
            if _EXECUTOR is executor:
                shutdown_search_worker()
            raise HTTPException(503, "تعطل عامل البحث المحلي. يبقى البحث بالكلمات متاحًا؛ أعد طلب البحث بالوصف لإنشاء عامل جديد.") from error
        if "error" in result:
            raise HTTPException(result["status"], result["error"])
        return result["result"]

    router.add_event_handler("shutdown", shutdown_search_worker)
    return router
