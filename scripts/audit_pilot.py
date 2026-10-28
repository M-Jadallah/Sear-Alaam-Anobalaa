"""Read-only conservation audit against a consistent pre-pilot SQLite backup."""
from __future__ import annotations
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.pilot import presentation,validate_document
from backend.semantic import semantic_status

TABLES=('sources','page_markers','biographies','source_sections','people','person_aliases','person_mentions','segments','topic_hits','review_log')

def row_hash(db,table):
    digest=hashlib.sha256()
    count=0
    for row in db.execute('SELECT * FROM '+table+' ORDER BY id'):
        digest.update(json.dumps(list(row),ensure_ascii=False,separators=(',',':')).encode('utf-8'));digest.update(b'\n');count+=1
    return {'rows':count,'sha256':digest.hexdigest()}

def audit(database,baseline):
    with closing(sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro',uri=True)) as db,closing(sqlite3.connect(Path(baseline).resolve().as_uri()+'?mode=ro',uri=True)) as old:
        db.row_factory=sqlite3.Row;old.row_factory=sqlite3.Row
        checks={t:{'before':row_hash(old,t),'after':row_hash(db,t)} for t in TABLES}
        for value in checks.values():value['unchanged']=value['before']==value['after']
        before={r['id']:dict(r) for r in old.execute('SELECT * FROM topics')}
        after={r['id']:dict(r) for r in db.execute('SELECT * FROM topics')}
        original_topics_unchanged=all(after.get(k)==v for k,v in before.items())
        new_topics=[v for k,v in after.items() if k not in before]
        documents=[json.loads(path.read_text(encoding='utf-8')) for path in sorted((ROOT/'reference/pilot').glob('biography-*.json'))]
        source=(ROOT/'data/source.mARkdown').read_bytes()
        source_sha=hashlib.sha256(source).hexdigest()
        views=[]
        for doc in documents:
            validate_document(db,doc)
            view=presentation(database,doc['biography_id'])
            assert view['available'] and view['reading_view']['coverage']['complete']
            notes=view['annotations']['footnotes']
            assert len(set((n['start'],n['end']) for n in notes))==len(notes)
            views.append({'biography_id':doc['biography_id'],'source_chars':doc['source_char_end']-doc['source_char_start'],'mapped_chars':view['reading_view']['coverage']['accounted_chars'],'complete':True,'footnotes':len(notes),'topic_annotations':sum(len(s['topics']) for s in doc['segments']),'segments':len(doc['segments'])})
        status=semantic_status(database)
        report={'source_sha256':source_sha,'baseline':str(Path(baseline).resolve()),'canonical_tables':checks,'original_topics_unchanged':original_topics_unchanged,'new_topic_count':len(new_topics),'new_topics_all_proposed':all(t['dictionary_status']=='proposed_from_text' for t in new_topics),'source_text_matches_file':db.execute('SELECT full_text FROM sources WHERE sha256=?',(source_sha,)).fetchone()[0].encode('utf-8')==source,'database_integrity':db.execute('PRAGMA integrity_check').fetchone()[0],'foreign_key_errors':[list(r) for r in db.execute('PRAGMA foreign_key_check')],'biographies':views,'sixth_presentation':presentation(database,6),'semantic_fingerprint':status.get('fingerprint'),'semantic_index_unchanged':status.get('fingerprint')=='100350c97cfdeb65d910de55031cabc2a42de58f64476d68ff42d7a8ce837223','human_events':db.execute('SELECT COUNT(*) FROM pilot_events').fetchone()[0],'human_topic_approvals':db.execute("SELECT COUNT(*) FROM pilot_evidence WHERE human_status='reviewed'").fetchone()[0],'reference_hashes':{name:hashlib.sha256((ROOT/'reference'/name).read_bytes()).hexdigest() for name in ('topic_dictionary.csv','legacy_indexer.html')}}
        report['passed']=all(x['unchanged'] for x in checks.values()) and original_topics_unchanged and report['new_topics_all_proposed'] and report['source_text_matches_file'] and report['database_integrity']=='ok' and not report['foreign_key_errors'] and report['semantic_index_unchanged'] and report['sixth_presentation']['available'] is False
        return report

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',type=Path,default=ROOT/'data/library.sqlite3')
    parser.add_argument('--baseline',type=Path,required=True)
    parser.add_argument('--output',type=Path,default=ROOT/'docs/PILOT_INTEGRITY.json')
    args=parser.parse_args();report=audit(args.db,args.baseline)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'passed':report['passed'],'mapped_chars':sum(b['mapped_chars'] for b in report['biographies']),'new_topics':report['new_topic_count'],'output':str(args.output)},ensure_ascii=False))
    if not report['passed']:raise SystemExit(1)

if __name__=='__main__':main()
