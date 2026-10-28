"""All mutations use synthetic SQLite libraries; never the real book."""
from contextlib import closing
from copy import deepcopy
import base64
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend import curation
from backend.db import connect
from backend.importer import import_book
from backend.pilot import prepare, presentation, topic_candidates, search_scope
from backend.reading import build_reading_view


def synthetic(count=12, title="اختبار دفعات مصطنع"):
    text="######OpenITI#\n#META# 020.BookTITLE :: "+title+"\n#META#Header#End#\n"
    for i in range(1,count+1):
        text+=f"### $ 1 - شخصية مصطنعة {i}\n# خبر تجريبي {i} عن الإخلاص ونقد القول (293) .\n# متابعة متصلة لا تنسب إلى الكتاب. PageV01P{i:03d}\n"
    return text


@pytest.fixture
def library(tmp_path,monkeypatch):
    monkeypatch.setenv("SIYAR_SEMANTIC_DISABLED","1")
    source=tmp_path/"synthetic.mARkdown";source.write_text(synthetic(),encoding="utf-8")
    db=tmp_path/"library.sqlite3";import_book(source,db)
    return {"source":source,"db":db,"work":tmp_path/"work"}


def exported(library,start=6,end=7):
    result=curation.export_batch(start,end,db_path=library["db"],source_path=library["source"],work_dir=library["work"])
    return Path(result["batch"])


def write(path,obj):
    path.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding="utf-8")


def author(batch,*,topic=True,zero_gold=False):
    manifest=curation._read_json(batch/"manifest.json");manifest["curator"]="وكيل تأليف مصطنع";write(batch/"manifest.json",manifest)
    for entry in manifest["biographies"]:
        if entry["status"]=="skipped":continue
        folder=batch/entry["directory"];bundle=curation._bundle(batch,entry)
        pilot,reading,footnotes=bundle["pilot"],bundle["reading"],bundle["footnotes"]
        pilot["reviewer"]=reading["reviewer"]=manifest["curator"]
        for i,segment in enumerate(pilot["segments"]):
            segment.update(assessment="supported" if topic and i==0 else "no_clear_topic",note="قرأ الوكيل الفقرة المصطنعة كاملة")
            for hit in segment["existing_hits"]:hit.update(decision="uncertain",reason="غير محسوم في الاختبار")
            if topic and i==0:
                s=bundle["source"]["segments"][i]
                segment["topics"]=[{"topic":"موضوع دفعة مصطنع","category":"القلب والسلوك","aliases":["خبر تجريبي"],"rationale":"شاهد اصطناعي لا يتعلق بالكتاب الحقيقي","relation_type":"discussion","subject_scope":"general_context","stance":"reported","evidence_start":s["source_char_start"],"evidence_end":s["source_char_end"],"context_start":s["source_char_start"],"context_end":s["source_char_end"]}]
        for candidate in footnotes["candidates"]:candidate.update(decision="body_literal",reason="رقم في المتن المصطنع وليس حاشية")
        footnotes.update(bodies_status="absent",body_ranges=[],notice="لا متون حواش في هذا النص المصطنع بعد الفحص")
        pilot["presentation"]["footnote_notice"]=footnotes["notice"]
        source=bundle["source"];view=build_reading_view(source["full_text"],entry["source_char_start"],starts_at_line_start=True)
        reading["paragraph_groups"]=[{"start":b["source_char_start"],"end":b["source_char_end"]} for b in view["blocks"]]
        if manifest["version"] == curation.VERSION:
            reading["paragraph_notes"] = [{**group, "context_start": group["start"], "context_end": group["end"], "reason": "حد عرض في العينة الاصطناعية: العنوان مستقل وتليه فقرة الخبر ثم متابعته"} for group in reading["paragraph_groups"]]
            diacritics = bundle["diacritics"]
            diacritics["reviewer"] = manifest["curator"]
            for word in diacritics["words"]:
                if word["status"] == "unexamined":
                    word.update(status="uncertain", reason="اختبار تعاقدي مصطنع لا يدعي حسم التشكيل اللغوي")
            write(folder/"diacritics.json", diacritics)
        gold={"items":[],"unavailable_reason":"العنوان التجريبي لا يقدم مادة مستقلة لسؤال"} if zero_gold else [{"question":"خبر مصطنع عن الإخلاص","start":source["segments"][0]["source_char_start"],"end":source["segments"][0]["source_char_end"]}]
        for key,value in (("pilot",pilot),("reading",reading),("footnotes",footnotes),("gold",gold)):write(folder/f"{key}.json",value)
        bundle=curation._bundle(batch,entry)
        audit={"authoring_sha256":curation.authoring_digest(bundle),"reviewer":"وكيل تدقيق مستقل مصطنع","all_segments_checked":True,"all_footnotes_checked":True,"findings":[],"checks":{key:True for key in curation.AUDIT_CHECKS}}
        write(folder/"audit.json",audit)
    return manifest


def qa(batch,preview):
    # A 1px image is solely a synthetic artifact-shape fixture, never corpus QA.
    image=batch/"synthetic-proof.png";image.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aY9sAAAAASUVORK5CYII="))
    write(batch/"qa.json",{"revision":preview["revision"],"gold_frozen_sha256":preview["gold_frozen_sha256"],"reviewer":"فاحص واجهة مصطنع","checks":{key:True for key in curation.QA_CHECKS},"screenshots":[str(image)]})


def preview_and_publish(lib,batch):
    p=curation.preview_batch(batch,db_path=lib["db"],source_path=lib["source"])
    qa(batch,p)
    return curation.publish_batch(batch,db_path=lib["db"],source_path=lib["source"])


def table_rows(db_path,table):
    with closing(connect(db_path)) as db:return [tuple(r) for r in db.execute("SELECT * FROM "+table+" ORDER BY 1")]


def test_export_is_readonly_resumable_and_ordinal_not_heading(library):
    before=hashlib.sha256(library["db"].read_bytes()).hexdigest()
    batch=exported(library)
    manifest=curation._read_json(batch/"manifest.json")
    assert [e["source_ordinal"] for e in manifest["biographies"]]==[6,7]
    assert all(e["heading"].startswith("1 -") for e in manifest["biographies"])
    draft=batch/"biography-6/pilot.json";draft.write_text(draft.read_text(encoding="utf-8")+"\n",encoding="utf-8")
    original=draft.read_bytes()
    assert curation.export_batch(6,7,db_path=library["db"],source_path=library["source"],work_dir=library["work"])["status"]=="resumed"
    assert draft.read_bytes()==original
    assert hashlib.sha256(library["db"].read_bytes()).hexdigest()==before
    with pytest.raises(ValueError):exported(library,1,6)


def test_second_source_ids_differ_from_ordinals(library):
    other=library["source"].with_name("synthetic-second.mARkdown");other.write_text(synthetic(3,"مصدر ثان مصطنع"),encoding="utf-8")
    import_book(other,library["db"])
    result=curation.export_batch(1,2,db_path=library["db"],source_path=other,work_dir=library["work"])
    assert [e["id"] for e in result["manifest"]["biographies"]]==[13,14]
    assert [e["source_ordinal"] for e in result["manifest"]["biographies"]]==[1,2]
    author(Path(result["batch"]))
    assert curation.validate_batch(result["batch"],db_path=library["db"],source_path=other)["valid"]


def test_authoring_coverage_audit_and_footnote_negative_validation(library):
    batch=exported(library);author(batch)
    assert curation.validate_batch(batch,db_path=library["db"],source_path=library["source"])["valid"]
    path=batch/"biography-6/footnotes.json";foot=curation._read_json(path)
    foot["candidates"].pop();write(path,foot)
    with pytest.raises(ValueError,match="جميع مرشحات"):curation.validate_batch(batch,db_path=library["db"],source_path=library["source"])
    author(batch)
    path=batch/"biography-6/pilot.json";pilot=curation._read_json(path);pilot["segments"][0]["topics"][0]["human_status"]="reviewed";write(path,pilot)
    with pytest.raises(ValueError,match="اعتمادًا بشريًا"):curation.validate_batch(batch,db_path=library["db"],source_path=library["source"])


def test_stale_audit_and_same_reviewer_fail(library):
    batch=exported(library);author(batch)
    path=batch/"biography-6/audit.json";audit=curation._read_json(path);audit["reviewer"]="وكيل تأليف مصطنع";write(path,audit)
    with pytest.raises(ValueError,match="مستقل"):curation.validate_batch(batch,db_path=library["db"],source_path=library["source"])
    author(batch);path=batch/"biography-6/reading.json";reading=curation._read_json(path);reading["criticism"]=[{"start":reading["paragraph_groups"][1]["start"],"end":reading["paragraph_groups"][1]["end"],"context_start":reading["paragraph_groups"][1]["start"],"context_end":reading["paragraph_groups"][1]["end"],"kind":"report","reason":"تعليق اصطناعي"}];write(path,reading)
    with pytest.raises(ValueError,match="ببصمة"):curation.validate_batch(batch,db_path=library["db"],source_path=library["source"])


def test_preview_is_isolated_and_qa_blocks_stale_or_failed_publication(library):
    batch=exported(library);author(batch)
    with pytest.raises((ValueError,FileNotFoundError)):curation.publish_batch(batch,db_path=library["db"],source_path=library["source"])
    p=curation.preview_batch(batch,db_path=library["db"],source_path=library["source"])
    assert Path(p["database"]).is_file()
    assert not presentation(library["db"],6)["available"]
    assert presentation(p["database"],6)["available"]
    qa(batch,p);q=curation._read_json(batch/"qa.json");q["checks"]["mobile"]=False;write(batch/"qa.json",q)
    with pytest.raises(ValueError,match="QA"):curation.publish_batch(batch,db_path=library["db"],source_path=library["source"])
    qa(batch,p)
    result=curation.publish_batch(batch,db_path=library["db"],source_path=library["source"])
    assert result["status"]=="published" and Path(result["backup"]).is_file()
    assert curation.publish_batch(batch,db_path=library["db"],source_path=library["source"])["status"]=="unchanged"


def test_successive_batches_preserve_previous_human_data_and_ids(library):
    first=exported(library);author(first);r=preview_and_publish(library,first)
    from backend.app import create_app
    with TestClient(create_app(library["db"],library["source"])) as client:
        item=next(i for i in client.get("/api/pilot/reviews",params={"batch_id":r["batch_id"]}).json()["items"] if i["agent_decision"]=="supported")
        assert client.post(f"/api/pilot/reviews/{item['id']}",json={"decision":"reviewed","reviewer":"إنسان اصطناعي","batch_id":r["batch_id"]}).status_code==200
    before={table:table_rows(library["db"],table) for table in ("topic_hits","review_log","pilot_events","pilot_documents","pilot_coverage","pilot_evidence","pilot_reading_overlays")}
    second=exported(library,8,9);author(second);preview_and_publish(library,second)
    for table,rows in before.items():
        after=table_rows(library["db"],table)
        assert all(row in after for row in rows),table
    with pytest.raises(ValueError,match="دفعات"):prepare(library["db"],[] or [curation._read_json(first/"biography-6/pilot.json")])
    mixed=exported(library,7,10)
    assert [e["status"] for e in curation._read_json(mixed/"manifest.json")["biographies"]]==["skipped","skipped","skipped","pending"]


def test_publish_rolls_back_all_rows_on_second_document_failure(library,monkeypatch):
    batch=exported(library);author(batch)
    p=curation.preview_batch(batch,db_path=library["db"],source_path=library["source"]);qa(batch,p)
    original=curation._install_document;count=0
    def fail(*args):
        nonlocal count
        count+=1
        if count==2:raise RuntimeError("اختبار انقطاع")
        return original(*args)
    monkeypatch.setattr(curation,"_install_document",fail)
    with pytest.raises(RuntimeError):curation.publish_batch(batch,db_path=library["db"],source_path=library["source"])
    with closing(connect(library["db"])) as db:
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name='curation_batches'").fetchone() is None
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name='pilot_documents'").fetchone() is None


def test_batch_scope_applies_to_topic_channel_and_unknown_fails_closed(library):
    batch=exported(library);author(batch);r=preview_and_publish(library,batch)
    with closing(connect(library["db"])) as db:tid=db.execute("SELECT id FROM topics WHERE name='موضوع دفعة مصطنع'").fetchone()[0]
    assert {row["biography_id"] for row in topic_candidates(library["db"],"",{"topic_ids":[tid],"batch_id":r["batch_id"]})}=={6,7}
    assert topic_candidates(library["db"],"",{"topic_ids":[tid],"pilot_only":True})==[]
    assert search_scope(library["db"],{"batch_id":r["batch_id"],"pilot_only":True})[0]==[]
    with pytest.raises(ValueError):topic_candidates(library["db"],"",{"batch_id":"missing"})
    with pytest.raises(ValueError):search_scope(library["db"],{"batch_id":"missing"})
    from backend.app import create_app
    with TestClient(create_app(library["db"],library["source"])) as client:
        assert client.get("/api/pilot/reviews?batch_id=missing").status_code==422
        assert client.get("/api/pilot/coverage?batch_id=missing").status_code==422
        item=client.get("/api/curation/batches").json()["items"][0]
        assert item["id"]==r["batch_id"] and not item["legacy"]
        assert client.get("/api/biographies/6/presentation").json()["batch_id"]==r["batch_id"]


def test_zero_gold_requires_specific_reason_and_source_mismatch_fails(library):
    batch=exported(library);author(batch,zero_gold=True)
    assert curation.validate_batch(batch,db_path=library["db"],source_path=library["source"])["valid"]
    another=library["source"].with_name("other.mARkdown");another.write_text(synthetic(2),encoding="utf-8")
    with pytest.raises(ValueError,match="بصمة"):curation.validate_batch(batch,db_path=library["db"],source_path=another)


def test_status_reports_actual_resume_gates_and_coverage(library):
    batch=exported(library)
    draft=curation.status_batch(batch,db_path=library["db"],source_path=library["source"])
    assert draft["phase"]=="authoring_required" and not any(draft["gates"].values())
    assert draft["coverage"][0]["assessed_segments"]==0
    assert draft["coverage"][0]["total_words"]>draft["coverage"][0]["checked_words"]
    author(batch)
    ready=curation.status_batch(batch,db_path=library["db"],source_path=library["source"])
    assert ready["phase"]=="audit_ready" and ready["gates"]["audit_valid"]
    assert ready["coverage"][0]["total_words"]==ready["coverage"][0]["checked_words"]
    assert ready["coverage"][0]["uncertain_words"]>0
    p=curation.preview_batch(batch,db_path=library["db"],source_path=library["source"])
    assert curation.status_batch(batch,db_path=library["db"],source_path=library["source"])["phase"]=="preview_ready"
    qa(batch,p)
    assert curation.status_batch(batch,db_path=library["db"],source_path=library["source"])["phase"]=="qa_ready"


def test_body_text_ranges_stay_visible_and_unsupported_links_fail(library):
    batch=exported(library);author(batch)
    path=batch/"biography-6/footnotes.json";ledger=curation._read_json(path)
    source=curation._read_json(batch/"biography-6/source.json")
    ledger.update(bodies_status="linked",body_ranges=[{"start":source["segments"][0]["source_char_start"],"end":source["segments"][0]["source_char_end"]}])
    write(path,ledger)
    with pytest.raises(ValueError,match="غير مدعوم"):curation.validate_batch(batch,db_path=library["db"],source_path=library["source"])


def legacy_draft(batch):
    """Reconstruct the old documented contract, including its exact hash input."""
    manifest=curation._read_json(batch/"manifest.json")
    manifest["version"]=curation.LEGACY_VERSION
    write(batch/"manifest.json",manifest)
    for entry in manifest["biographies"]:
        if entry["status"]=="skipped":continue
        path=batch/entry["directory"]/"reading.json"
        reading=curation._read_json(path);reading.pop("paragraph_notes",None);write(path,reading)
    author(batch)
    for entry in manifest["biographies"]:
        if entry["status"]=="skipped":continue
        path=batch/entry["directory"]/"audit.json"
        audit=curation._read_json(path)
        audit["checks"]={key:True for key in curation.BASE_AUDIT_CHECKS}
        write(path,audit)
    return curation._load(batch)


def test_v1_hash_qa_and_published_revision_remain_compatible(library):
    batch=exported(library)
    _,manifest,bundles=legacy_draft(batch)
    legacy_digests={str(identifier):curation._digest({key:bundle[key] for key in ("source","pilot","reading","footnotes","gold")}) for identifier,bundle in bundles.items()}
    expected=curation._digest({"manifest":manifest,"authoring":legacy_digests,"audit":{str(identifier):bundle["audit"] for identifier,bundle in bundles.items()}})
    assert curation.batch_revision(manifest,bundles)==expected
    assert all("diacritics" not in bundle for bundle in bundles.values())
    assert curation.validate_batch(batch,db_path=library["db"],source_path=library["source"])["valid"]
    proof=curation.preview_batch(batch,db_path=library["db"],source_path=library["source"])
    qa(batch,proof)
    report=curation._read_json(batch/"qa.json")
    report["checks"]={key:True for key in curation.BASE_QA_CHECKS};write(batch/"qa.json",report)
    assert curation.publish_batch(batch,db_path=library["db"],source_path=library["source"])["revision"]==expected
    assert curation.publish_batch(batch,db_path=library["db"],source_path=library["source"])["status"]=="unchanged"
    with pytest.raises(ValueError,match="منشورة"):
        curation.upgrade_batch(batch,db_path=library["db"],source_path=library["source"])


def test_upgrade_preserves_authoring_and_invalidates_all_proofs(library):
    batch=exported(library)
    _,manifest,bundles=legacy_draft(batch)
    proof=curation.preview_batch(batch,db_path=library["db"],source_path=library["source"]);qa(batch,proof)
    preserved={str(path.relative_to(batch)):path.read_bytes() for entry in manifest["biographies"] for path in (batch/entry["directory"]).glob("*.json") if path.stem in ("source","pilot","footnotes","gold")}
    before_database=hashlib.sha256(library["db"].read_bytes()).hexdigest()
    result=curation.upgrade_batch(batch,db_path=library["db"],source_path=library["source"])
    backup=Path(result["backup"])
    assert result["status"]=="upgraded" and backup.is_dir()
    assert curation._read_json(backup/"manifest.json")["version"]==curation.LEGACY_VERSION
    assert (backup/"preview.sqlite3").is_file() and (backup/"preview.json").is_file()
    assert not (batch/"preview.json").exists()
    assert hashlib.sha256(library["db"].read_bytes()).hexdigest()==before_database
    for name,content in preserved.items():assert (batch/name).read_bytes()==content
    for entry in manifest["biographies"]:
        folder=batch/entry["directory"]
        assert all(not value for value in curation._read_json(folder/"audit.json")["checks"].values())
        assert all(word["status"] in ("unexamined","not_applicable") for word in curation._read_json(folder/"diacritics.json")["words"])
        assert all(not note["reason"] for note in curation._read_json(folder/"reading.json")["paragraph_notes"])
    assert curation.upgrade_batch(batch,db_path=library["db"],source_path=library["source"])["status"]=="unchanged"
    with pytest.raises(ValueError):curation.validate_batch(batch,db_path=library["db"],source_path=library["source"])


def test_upgrade_directory_swap_failure_restores_original(library,monkeypatch):
    batch=exported(library);legacy_draft(batch)
    before={str(path.relative_to(batch)):path.read_bytes() for path in batch.rglob("*.json")}
    replace=curation.os.replace
    def interrupted(src,dst):
        if Path(dst)==batch and "-upgrade-" in Path(src).name:
            raise RuntimeError("انقطاع تبديل المجلد الاختباري")
        return replace(src,dst)
    monkeypatch.setattr(curation.os,"replace",interrupted)
    with pytest.raises(RuntimeError):curation.upgrade_batch(batch,db_path=library["db"],source_path=library["source"])
    assert {str(path.relative_to(batch)):path.read_bytes() for path in batch.rglob("*.json")}==before


def test_v2_requires_word_review_paragraph_reasons_and_new_audit_qa(library):
    batch=exported(library)
    assert curation._read_json(batch/"manifest.json")["version"]==curation.VERSION
    author(batch)
    folder=batch/"biography-6"
    reading=curation._read_json(folder/"reading.json");original=deepcopy(reading)
    reading["paragraph_notes"][1]["reason"]="";write(folder/"reading.json",reading)
    with pytest.raises(ValueError,match="تعليل"):curation.validate_batch(batch,db_path=library["db"],source_path=library["source"],require_audit=False)
    write(folder/"reading.json",original)
    diacritics=curation._read_json(folder/"diacritics.json");original=deepcopy(diacritics)
    next(word for word in diacritics["words"] if word["status"]=="uncertain")["status"]="unexamined"
    write(folder/"diacritics.json",diacritics)
    with pytest.raises(ValueError):curation.validate_batch(batch,db_path=library["db"],source_path=library["source"],require_audit=False)
    write(folder/"diacritics.json",original)
    audit_path=folder/"audit.json";audit=curation._read_json(audit_path);audit["checks"].pop("diacritics");write(audit_path,audit)
    with pytest.raises(ValueError,match="غير مكتمل"):curation.validate_batch(batch,db_path=library["db"],source_path=library["source"])
    author(batch)
    proof=curation.preview_batch(batch,db_path=library["db"],source_path=library["source"]);qa(batch,proof)
    report=curation._read_json(batch/"qa.json");report["checks"].pop("diacritics_toggle");write(batch/"qa.json",report)
    with pytest.raises(ValueError,match="QA"):curation.publish_batch(batch,db_path=library["db"],source_path=library["source"])


def test_diacritics_changes_invalidate_independent_audit(library):
    batch=exported(library);author(batch)
    path=batch/"biography-6/diacritics.json";document=curation._read_json(path)
    document["words"][0]["reason"]+=" تعليل لغوي تغير بعد توقيع المدقق"
    write(path,document)
    with pytest.raises(ValueError,match="ببصمة"):curation.validate_batch(batch,db_path=library["db"],source_path=library["source"])


def test_v2_rejects_structural_cut_inside_original_diacritic_cluster(library):
    source=library["source"].with_name("cluster-synthetic.mARkdown")
    source.write_text(synthetic(1,"اختبار مصطنع للضبط").replace("خبر تجريبي","خَبَرٌ تجريبي"),encoding="utf-8")
    import_book(source,library["db"])
    local={**library,"source":source};batch=exported(local,1,1);author(batch)
    folder=batch/"biography-1";original=curation._read_json(folder/"source.json")
    cut=original["source_char_start"]+original["full_text"].index("خَبَرٌ")+1
    reading=curation._read_json(folder/"reading.json")
    groups=[]
    for group in reading["paragraph_groups"]:
        if group["start"]<cut<group["end"]:groups.extend([{"start":group["start"],"end":cut},{"start":cut,"end":group["end"]}])
        else:groups.append(group)
    reading["paragraph_groups"]=groups
    reading["paragraph_notes"]=[{**group,"context_start":group["start"],"context_end":group["end"],"reason":"حد اصطناعي خاطئ لاختبار حماية حرف مع حركته"} for group in groups]
    write(folder/"reading.json",reading)
    with pytest.raises(ValueError,match="حركاته الأصلية"):curation.validate_batch(batch,db_path=library["db"],source_path=source,require_audit=False)


def test_v2_editorial_headings_require_reviewed_vocalization(library):
    batch=exported(library);author(batch)
    folder=batch/"biography-6";pilot=curation._read_json(folder/"pilot.json");reading=curation._read_json(folder/"reading.json")
    pilot["presentation"]["headings"]=[{"at":reading["paragraph_groups"][1]["start"],"label":"عنوان بلا حركات"}]
    write(folder/"pilot.json",pilot)
    with pytest.raises(ValueError,match="عنوان تنظيمي"):curation.validate_batch(batch,db_path=library["db"],source_path=library["source"],require_audit=False)
