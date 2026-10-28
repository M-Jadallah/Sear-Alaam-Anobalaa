"""Record the root agent's completed independent textual audit of 5 and 6."""
from pathlib import Path
import json, sys
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from backend.reading_revision import _load, authoring_digest, AUDIT_CHECKS, validate_batch, preview_batch

BASE = ROOT/'reference/reading-revisions/7ae7f691ffe236f5b888f5dfdf18becd9e5ea17ab6b5851528db5efd333886c0'
BATCH = BASE/'reading-7ae7f691ffe2-5-6-2ddbf981efa5'
FINDINGS = {
    5: [
        'قرأت النص المشكول كاملًا مستقلًا عن المؤلف وقابلت مواضع الالتباس وسياقها بالأصل؛ لا اعتماد لغوي بشري.',
        'أبقي لاق في إني لأراك لاق مني شرا دون إضافات بعد رفض الجر المضاف أولًا؛ رسم الأصل محفوظ.',
        'قُيد التباس كنت وعلمتك بموضعي الشكوى دون إزالة ضبط غيرهما، وبني بموضعي النداء فقط.',
        'فُحصت تعليلات الفقرات الافتتاحية والرؤيا والشكاية والأسانيد والشعر؛ وصل المنام بتفسيره مع بقاء الأخبار المستقلة.',
        'احتفظت ألفاظ التحقيق والشرط والنفي والأسماء والتلوين والحواشي بحدودها السابقة دون منح مراجعة موضوعية بشرية.',
    ],
    6: [
        'قرأت النص المشكول كاملًا مستقلًا عن المؤلف وقابلت مواضع الالتباس وسياقها بالأصل؛ لا اعتماد لغوي بشري.',
        'صُحح بن إلى بْنُ في حدثنا شعيب بن يونس لاتصاله بالفاعل المرفوع، دون تغيير حرف من الأصل.',
        'أبقي موضع ثمان في سنة ثمان عشرة دون إضافات لأن الرسم يحتاج تحقيقًا؛ تحققت من نطاق الكلمة حتى لا يلتبس بعثمان.',
        'رُبط تقديم حديث المؤاخاة الطويل بنكارته ونقد زيد بعده؛ وطريق شبابة بنفي الصحة، لا بالمحفوظ بعده.',
        'فُحصت حدود الشعر والحوار ونقد الإرسال ووفاة ورقة والأخبار المستقلة؛ لا تسوية لهويات الرواة أو حكم حديثي مستحدث.',
    ],
}

if __name__ == '__main__':
    _, manifest, bundles = _load(BATCH)
    assert manifest['curator'] == '/root/published_reading_revision'
    for identifier, bundle in bundles.items():
        audit = {'authoring_sha256': authoring_digest(bundle), 'reviewer': '/root',
                 'all_words_checked': True, 'all_paragraphs_checked': True,
                 'checks': {key: True for key in AUDIT_CHECKS},
                 'findings': [{'note': note, 'resolved': True} for note in FINDINGS[identifier]]}
        (BATCH/f'biography-{identifier}'/'audit.json').write_text(json.dumps(audit, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(validate_batch(BATCH), ensure_ascii=False))
    result = preview_batch(BATCH, db_path=BASE/'reading-7ae7f691ffe2-7-10-eec353900a9b'/'preview.sqlite3')
    (ROOT/'reference/reading-revisions/final-preview.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))
