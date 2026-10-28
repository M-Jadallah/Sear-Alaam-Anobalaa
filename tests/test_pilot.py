"""The pilot mutates only isolated, explicitly synthetic fixtures in API tests."""
import copy
import hashlib
import json
import sqlite3

import pytest

from backend.db import connect
from backend.pilot import prepare, presentation, validate_document, topic_candidates


@pytest.fixture
def pilot_book(book):
    documents=[]
    with connect(book['database']) as db:
        sha=db.execute('SELECT sha256 FROM sources').fetchone()[0]
        for bio in db.execute('SELECT * FROM biographies ORDER BY id'):
            segments=[]
            for segment in db.execute('SELECT * FROM segments WHERE biography_id=?',(bio['id'],)):
                topics=[]
                if bio['id']==1 and not segments:
                    topics=[{'topic':'موضوع اصطناعي خارج المعجم','category':'القلب والسلوك','aliases':['معنى اختباري'],'rationale':'عينة اصطناعية لقياس الربط الحرفي فقط','relation_type':'discussion','subject_scope':'general_context','stance':'reported','evidence_start':segment['source_char_start'],'evidence_end':segment['source_char_end'],'context_start':segment['source_char_start'],'context_end':segment['source_char_end']}]
                old=[{'hit_id':hit[0],'decision':'exclude','reason':'توصية اختبارية صريحة لا اعتماد بشري'} for hit in db.execute('SELECT id FROM topic_hits WHERE segment_id=?',(segment['id'],))]
                segments.append({'segment_id':segment['id'],'assessment':'supported' if topics else 'no_clear_topic','note':'قرأ هذا المقطع الاصطناعي لغرض الاختبار','topics':topics,'existing_hits':old})
            documents.append({'source_sha256':sha,'biography_id':bio['id'],'source_char_start':bio['source_char_start'],'source_char_end':bio['source_char_end'],'reviewer':'وكيل اختبار','segments':segments,'presentation':{key:[] for key in ('headings','quotes','poetry','lists','bold','keywords','names','footnotes')},'gold':[]})
    return {**book,'documents':documents}


def test_prepare_backup_idempotency_and_no_automatic_human_approval(pilot_book):
    p=pilot_book
    with connect(p['database']) as db:
        before={table:[tuple(r) for r in db.execute('SELECT * FROM '+table+' ORDER BY id')] for table in ('sources','biographies','segments','topic_hits','review_log','person_mentions')}
    result=prepare(p['database'],p['documents'])
    assert result['status']=='ready'
    assert __import__('pathlib').Path(result['backup']).is_file()
    assert prepare(p['database'],p['documents'])['status']=='unchanged'
    with connect(p['database']) as db:
        assert all(before[t]==[tuple(r) for r in db.execute('SELECT * FROM '+t+' ORDER BY id')] for t in before)
        assert db.execute("SELECT COUNT(*) FROM pilot_evidence WHERE human_status!='pending'").fetchone()[0]==0
        assert db.execute('SELECT COUNT(*) FROM pilot_coverage').fetchone()[0]==sum(len(d['segments']) for d in p['documents'])
        assert db.execute('PRAGMA foreign_key_check').fetchall()==[]
    rows=topic_candidates(p['database'],'"معني اختباري"',{})
    assert rows and all(r['agent_decision']=='supported' for r in rows)
    assert topic_candidates(p['database'],'"معني اختباري"',{'reviewed_only':True})==[]


def test_validation_rejects_changed_source_missing_coverage_foreign_hits_and_crossing_spans(pilot_book):
    p=pilot_book
    with connect(p['database']) as db:
        original=p['documents'][0]
        validate_document(db,original)
        bad=copy.deepcopy(original);bad['source_sha256']='0'*64
        with pytest.raises(ValueError):validate_document(db,bad)
        bad=copy.deepcopy(original);bad['segments'].pop()
        with pytest.raises(ValueError):validate_document(db,bad)
        bad=copy.deepcopy(original);bad['segments'][0]['topics'][0]['evidence_end']=original['source_char_end']+1
        with pytest.raises(ValueError):validate_document(db,bad)
        bad=copy.deepcopy(original);bad['segments'][0]['existing_hits'].append({'hit_id':999999,'decision':'keep','reason':'invalid'})
        with pytest.raises(ValueError):validate_document(db,bad)
        bad=copy.deepcopy(original);bad['presentation']['footnotes']=[{'start':original['source_char_start'],'end':original['source_char_start']+3}]
        with pytest.raises(ValueError):validate_document(db,bad)


def test_presentation_scope_and_stale_source_fails_closed(pilot_book):
    p=pilot_book
    prepare(p['database'],p['documents'])
    view=presentation(p['database'],1)
    assert view['available'] and view['reading_view']['coverage']['complete']
    assert view['source_sha256']==p['documents'][0]['source_sha256']
    assert '🧪' in view['plain_text']
    with connect(p['database']) as db:
        db.execute("UPDATE sources SET sha256=?",('f'*64,))
    assert presentation(p['database'],1)['status']=='stale'
    assert topic_candidates(p['database'],'"معني اختباري"',{})==[]


def test_human_review_transaction_and_search_cache_invalidation(pilot_book,monkeypatch):
    from backend.app import create_app
    from backend import smart_search
    from fastapi.testclient import TestClient
    monkeypatch.setenv('SIYAR_SEMANTIC_DISABLED','1')
    p=pilot_book;prepare(p['database'],p['documents'])
    smart_search._CACHE.clear()
    plan={'meaning':'معنى اختباري','pilot_only':True,'topic_mode':'boost','reviewed_only':False}
    first=smart_search.smart_search({'q':'معنى اختباري','plan':plan},p['database'])
    assert first['ranking']['topic_candidates']>=1
    assert any(i['match_type']=='topic_candidate' for i in first['items'])
    filtered=smart_search.smart_search({'q':'معنى اختباري','plan':{**plan,'reviewed_only':True}},p['database'])
    assert filtered['total']==0
    with TestClient(create_app(p['database'],p['source'])) as client:
        queue=client.get('/api/pilot/reviews').json()
        item=next(x for x in queue['items'] if x['agent_decision']=='supported')
        assert client.get('/api/biographies/1/topics').json()['coverage']['human_checked']==0
        bad=client.post(f"/api/pilot/reviews/{item['id']}",json={'decision':'reviewed','reviewer':'إنسان اختباري','source_char_end':999999})
        assert bad.status_code==422
        approved=client.post(f"/api/pilot/reviews/{item['id']}",json={'decision':'reviewed','reviewer':'إنسان اختباري','note':'اعتماد ضمن قاعدة اصطناعية فقط'})
        assert approved.status_code==200
        assert approved.json()['human_status']=='reviewed'
        assert client.post(f"/api/pilot/reviews/{item['id']}",json={'decision':'rejected','reviewer':'إنسان'}).status_code==409
        assert client.post(f"/api/pilot/reviews/{item['id']}",json={'decision':'reviewed','reviewer':'إنسان'},headers={'origin':'https://evil.invalid'}).status_code==403
    assert smart_search.smart_search({'q':'معنى اختباري','plan':plan},p['database'])['cache_hit'] is False
    positive=smart_search.smart_search({'q':'معنى اختباري','plan':{**plan,'reviewed_only':True}},p['database'])
    assert positive['items']
    with connect(p['database']) as db:
        assert db.execute("SELECT COUNT(*) FROM topic_hits WHERE review_status='reviewed'").fetchone()[0]==1
        assert db.execute('SELECT COUNT(*) FROM pilot_events').fetchone()[0]==1
    changed=copy.deepcopy(p['documents']);changed[0]['segments'][0]['note']+=' تغيير'
    with pytest.raises(ValueError,match='قرارات بشرية'):prepare(p['database'],changed)


def test_approved_exclusion_never_enters_topic_channel(pilot_book,monkeypatch):
    from backend.app import create_app
    from fastapi.testclient import TestClient
    monkeypatch.setenv('SIYAR_SEMANTIC_DISABLED','1')
    p=pilot_book;prepare(p['database'],p['documents'])
    with TestClient(create_app(p['database'],p['source'])) as client:
        queue=client.get('/api/pilot/reviews?page_size=100').json()['items']
        item=next(x for x in queue if x['agent_decision']=='exclude')
        response=client.post(f"/api/pilot/reviews/{item['id']}",json={'decision':'reviewed','reviewer':'إنسان اختباري'})
        assert response.status_code==200
    with connect(p['database']) as db:
        assert db.execute('SELECT review_status FROM topic_hits WHERE id=?',(item['original_hit_id'],)).fetchone()[0]=='rejected'
    assert all(row['id']!=item['id'] for row in topic_candidates(p['database'],'',{'topic_ids':[item['topic_id']]}))


def test_explicit_topic_filter_and_empty_selection_are_not_silent_boost(pilot_book,monkeypatch):
    from backend import smart_search
    monkeypatch.setenv('SIYAR_SEMANTIC_DISABLED','1')
    p=pilot_book;prepare(p['database'],p['documents']);smart_search._CACHE.clear()
    with connect(p['database']) as db:
        topic=db.execute("SELECT id FROM topics WHERE name='موضوع اصطناعي خارج المعجم'").fetchone()[0]
    result=smart_search.smart_search({'q':'معنى اختباري','plan':{'topic_ids':[topic],'topic_mode':'filter','pilot_only':True}},p['database'])
    assert result['items'] and all(i['biography_id']==1 for i in result['items'])
    empty=smart_search.smart_search({'q':'الإخلاص','plan':{'topic_mode':'filter'}},p['database'])
    assert empty['total']==0


def test_human_coverage_is_separate_from_topic_approval(pilot_book,monkeypatch):
    from backend.app import create_app
    from fastapi.testclient import TestClient
    monkeypatch.setenv('SIYAR_SEMANTIC_DISABLED','1')
    p=pilot_book;prepare(p['database'],p['documents'])
    with TestClient(create_app(p['database'],p['source'])) as client:
        rows=client.get('/api/pilot/coverage?biography_id=1').json()
        item=next(r for r in rows['items'] if r['assessment']=='no_clear_topic')
        response=client.post(f"/api/pilot/coverage/{item['segment_id']}",json={'reviewer':'إنسان اختباري','note':'قراءة الفقرة فقط؛ دون اعتماد أي وسم'})
        assert response.status_code==200
        assert client.get('/api/biographies/1/topics').json()['coverage']['human_checked']==1
        assert client.get('/api/pilot/coverage?status=reviewed').json()['total']==1
        assert client.post(f"/api/pilot/coverage/{item['segment_id']}",json={'reviewer':'آخر'}).status_code==409
    with connect(p['database']) as db:
        assert db.execute("SELECT COUNT(*) FROM topic_hits WHERE review_status='reviewed'").fetchone()[0]==0
        assert db.execute("SELECT actor_kind FROM pilot_events").fetchone()[0]=='human'


def test_human_approval_log_keeps_canonical_rows_before_and_after(pilot_book,monkeypatch):
    from backend.app import create_app
    from fastapi.testclient import TestClient
    monkeypatch.setenv('SIYAR_SEMANTIC_DISABLED','1')
    p=pilot_book;prepare(p['database'],p['documents'])
    with TestClient(create_app(p['database'],p['source'])) as client:
        item=next(r for r in client.get('/api/pilot/reviews').json()['items'] if r['agent_decision']=='supported')
        assert client.post(f"/api/pilot/reviews/{item['id']}",json={'decision':'reviewed','reviewer':'إنسان اختباري'}).status_code==200
    with connect(p['database']) as db:
        change=json.loads(db.execute("SELECT change_summary FROM review_log WHERE entity_type='pilot_topic'").fetchone()[0])
        assert 'canonical_before' in change['after']
        after=change['after']['canonical_after']
        assert after and any(r['review_status']=='reviewed' for r in after)
        assert all(r['evidence_text']==p['text'][r['source_char_start']:r['source_char_end']] for r in after)
