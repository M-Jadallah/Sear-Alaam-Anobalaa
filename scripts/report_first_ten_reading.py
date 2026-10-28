"""Summarize published reading revisions from immutable authoring/proof files."""
from pathlib import Path
import hashlib, json, sqlite3, sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from backend.reading_revision import _load, status_batch

def main():
    publications=json.loads((ROOT/'docs/FIRST_TEN_READING_PUBLICATION.json').read_text(encoding='utf-8'))
    baseline=json.loads((ROOT/'reference/reading-revisions/first-ten-baseline.json').read_text(encoding='utf-8'))
    records=[];states=[]
    for item in publications:
        folder=ROOT/'reference/reading-revisions'/baseline['files']['data\\source.mARkdown']/item['batch_id']
        _,manifest,bundles=_load(folder)
        state=status_batch(folder);assert all(state['gates'].values())
        states.append({'batch_id':manifest['batch_id'],'revision':item['revision'],'gates':state['gates']})
        for identifier,bundle in bundles.items():
            source,reading,d=bundle['source'],bundle['reading'],bundle['diacritics']
            words=d['words'];counts={name:sum(w['status']==name for w in words) for name in ('complete','uncertain','not_applicable')}
            old=json.loads(source['reading_row']['payload'])
            records.append({'biography_id':identifier,'source_ordinal':source['source_ordinal'],'heading':source['heading'],
                'source_char_start':source['source_char_start'],'source_char_end':source['source_char_end'],'source_chars':len(source['full_text']),
                'word_coverage':{'total':len(words),'checked':len(words),**counts},'insertion_sites':len(d['insertions']),
                'paragraphs_before':len(old['paragraph_groups']),'paragraphs_after':len(reading['paragraph_groups']),
                'paragraph_reasons':len(reading['paragraph_notes']),'criticism':len(reading['criticism']),
                'curator':manifest['curator'],'independent_reviewer':bundle['audit']['reviewer'],'revision':item['revision'],
                'uncertainties':[{**w,'literal':source['full_text'][w['start']-source['source_char_start']:w['end']-source['source_char_start']]} for w in words if w['status']=='uncertain']})
    records.sort(key=lambda r:r['source_ordinal'])
    assert [r['source_ordinal'] for r in records]==list(range(1,11))
    totals={name:sum(r[name] for r in records) for name in ('source_chars','insertion_sites','paragraphs_before','paragraphs_after','criticism')}
    totals['words']={name:sum(r['word_coverage'][name] for r in records) for name in ('total','checked','complete','uncertain','not_applicable')}
    file_hashes={path:hashlib.sha256((ROOT/path).read_bytes()).hexdigest() for path in baseline['files']}
    assert file_hashes==baseline['files']
    report={'scope':'published source ordinals 1–10; agent editorial reading only','source_sha256':file_hashes['data\\source.mARkdown'],
        'original_files_unchanged':True,'totals':totals,'records':records,'batches':states,
        'human_linguistic_approval':False,'topic_and_identity_review_unchanged':True}
    target=ROOT/'docs/FIRST_TEN_READING_CONTENT.json'
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'totals':totals,'report':str(target)},ensure_ascii=False))

if __name__=='__main__':main()
