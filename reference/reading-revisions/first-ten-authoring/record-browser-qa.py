"""Bind the completed, inspected browser reports to their exact preview content."""
from pathlib import Path
import json, sys
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT))
from backend.reading_revision import status_batch
BASE=ROOT/'reference/reading-revisions/7ae7f691ffe236f5b888f5dfdf18becd9e5ea17ab6b5851528db5efd333886c0'
SHOTS=Path('C:/Users/asala/.codex/visualizations/siyar-first-ten-20261028')
NAMES=['reading-7ae7f691ffe2-1-2-52a5d4ea39c4','reading-7ae7f691ffe2-3-3-a63733d5700a','reading-7ae7f691ffe2-4-4-36e71356407c','reading-7ae7f691ffe2-5-6-2ddbf981efa5','reading-7ae7f691ffe2-7-10-eec353900a9b']
def read(path):return json.loads(path.read_text(encoding='utf-8'))
for name in NAMES:
    folder=BASE/name;manifest=read(folder/'manifest.json');proof=read(folder/'preview.json')
    report_path=SHOTS/('qa-first-ten-5-6.json' if manifest['start_ordinal']==5 else 'qa-first-ten-1-4-7-10.json')
    report=read(report_path);ids={e['id'] for e in manifest['biographies']}
    assert ids.issubset({b['id'] for b in report['biographies']}) and all(report['checks'].values()) and not report['console_errors']
    screenshots=[p for p in report['screenshots'] if any(Path(p).name.startswith(f'reader-{i}-') for i in ids)]
    assert len(screenshots)==2*len(ids)
    qa={**{k:proof[k] for k in ('revision','content_sha256')},'reviewer':'/root','checks':report['checks'],
        'screenshots':screenshots,'report':str(report_path),'visual_review':'/root inspected every desktop and mobile35 screenshot; marks attached, natural wrapping and color legends visible.'}
    (folder/'qa.json').write_text(json.dumps(qa,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    state=status_batch(folder);print(json.dumps({'batch':name,'gates':state['gates']},ensure_ascii=False))
