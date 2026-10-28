"""Publish only audited, browser-verified first-ten reading corrections."""
from pathlib import Path
import json,sys
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT))
from backend.reading_revision import status_batch,publish_batch
SHA='7ae7f691ffe236f5b888f5dfdf18becd9e5ea17ab6b5851528db5efd333886c0'
NAMES=['reading-7ae7f691ffe2-1-2-52a5d4ea39c4','reading-7ae7f691ffe2-3-3-a63733d5700a','reading-7ae7f691ffe2-4-4-36e71356407c','reading-7ae7f691ffe2-5-6-2ddbf981efa5','reading-7ae7f691ffe2-7-10-eec353900a9b']
def main():
    # Check every gate BEFORE publishing even the first part.
    directories=[ROOT/'reference/reading-revisions'/SHA/n for n in NAMES]
    for d in directories:
        status=status_batch(d)
        if not all(status['gates'][key] for key in ('authoring_valid','audit_valid','preview_current','qa_current')):
            raise ValueError((d,status['gates'],status['reasons']))
    results=[]
    target=ROOT/'docs/FIRST_TEN_READING_PUBLICATION.json'
    for d in directories:
        result=publish_batch(d)
        results.append(result)
        target.write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(result,ensure_ascii=False))
if __name__=='__main__':main()
