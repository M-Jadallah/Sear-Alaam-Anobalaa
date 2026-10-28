"""Assemble explicitly authored counterparts1–3; does not invent vowel choices."""
from pathlib import Path
import json,sys
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT))
from backend.diacritics import export_document,derive_insertions,validate_document
from backend.reading_revision import validate_batch
SHA='7ae7f691ffe236f5b888f5dfdf18becd9e5ea17ab6b5851528db5efd333886c0'
HERE=Path(__file__).parent
def read(p):return json.loads(p.read_text(encoding='utf-8'))
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
for ids,name,curator in [((1,2),'reading-7ae7f691ffe2-1-2-52a5d4ea39c4','/root/vocalize_abu_talha'),((3,),'reading-7ae7f691ffe2-3-3-a63733d5700a','/root/vocalize_zubair_abdurrahman')]:
    batch=ROOT/'reference/reading-revisions'/SHA/name
    manifest=read(batch/'manifest.json');manifest.update(curator=curator,reason='تطبيق طلب المستخدم التشكيل الكامل وتدقيق سياق الفقرات للتراجم العشر الأولى، مع حفظ الموضوعات والمراجعات وتاريخ القراءة.')
    save(batch/'manifest.json',manifest)
    for bid in ids:
        folder=batch/f'biography-{bid}';source=read(folder/'source.json')
        reading=read(HERE/f'biography-{bid}-reading.json');reading['reviewer']=curator
        voice=(HERE/f'biography-{bid}.txt').read_text(encoding='utf-8')
        uncertainties=read(HERE/f'biography-{bid}-uncertain.json')
        uncertain={(row['start'],row['end']):row['reason'] for row in uncertainties}
        doc=export_document(source,SHA,curator)
        doc['insertions']=derive_insertions(source['full_text'],voice,source['source_char_start'])
        selected=set()
        for word in doc['words']:
            key=(word['start'],word['end'])
            literal=source['full_text'][word['start']-source['source_char_start']:word['end']-source['source_char_start']]
            if key in uncertain:
                word.update(status='uncertain',reason=uncertain[key]);selected.add(key)
            elif word['status']=='not_applicable':continue
            elif len(literal)==1 and literal in ('ع','م','ق','ح','ت','د','خ','س'):
                word.update(status='not_applicable',reason='رمز تخريج أو تحويل إسناد في الأصل؛ فُحص موضعه ولا يمثل كلمة منطوقة.')
            else:word.update(status='complete');word.pop('reason',None)
        assert selected==set(uncertain)
        coverage=validate_document(doc,source,reading,SHA,curator)
        save(folder/'reading.json',reading);save(folder/'diacritics.json',doc)
        print(bid,coverage)
    print(validate_batch(batch,require_audit=False))
