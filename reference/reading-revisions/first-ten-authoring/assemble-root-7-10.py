"""Authored decisions for7, and reuse of independently audited8–10 reading.

This is an explicit batch authoring artifact, not a generic vowel classifier.
It writes only the named unpublished reading-revision workspace.
"""
from pathlib import Path
import json,sys
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from backend.diacritics import derive_insertions,export_document,validate_document
from backend.reading_revision import validate_batch
SHA='7ae7f691ffe236f5b888f5dfdf18becd9e5ea17ab6b5851528db5efd333886c0'
WORK=ROOT/'reference/reading-revisions'/SHA/'reading-7ae7f691ffe2-7-10-eec353900a9b'
PRIOR=Path('C:/Users/asala/AppData/Local/Temp/siyar-diacritics-trial-h54sy2n1/work')/SHA
HERE=Path(__file__).parent
def load(p):return json.loads(p.read_text(encoding='utf-8'))
def save(p,o):p.write_text(json.dumps(o,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
labels={
7:['قُدُومُ مُصْعَبٍ مَعَ الْمُهَاجِرِينَ','خَبَرُ خَبَّابٍ عَنِ الْهِجْرَةِ وَالْكَفَنِ','بُكَاءُ عَبْدِ الرَّحْمَنِ بْنِ عَوْفٍ','بُرْدَةُ مُصْعَبٍ وَالْحِوَارُ عَنِ الْعَيْشِ','شِدَّةُ الْعَيْشِ بَعْدَ الْهِجْرَةِ','قِتَالُ مُصْعَبٍ وَمَقْتَلُهُ'],
8:['الْهِجْرَةُ وَالْقَرَابَةُ وَالْوَفَاةُ','خَبَرُ أُمِّ سَلَمَةَ وَالدُّعَاءِ','رِوَايَةُ الْمُصِيبَةِ وَالْخَلَفِ','الْجُرْحُ وَالسَّرِيَّةُ وَالْوَفَاةُ'],
9:['خَبَرُ وَضْعِ الْحَجَرِ عِنْدَ الْقَبْرِ','رَدُّ التَّبَتُّلِ','خَبَرُ الْخَمْرِ وَنَقْدُهُ','أَخْبَارُ الْوَفَاةِ وَالْبُكَاءِ','أَخْبَارُ التَّعَبُّدِ وَالْأُسْرَةِ','هِجْرَةُ آلِ مَظْعُونٍ','أَخْبَارُ الْوَفَاةِ وَالرُّؤْيَا'],
10:['الْهِجْرَةُ وَخَبَرُ الْحَدِّ وَالْعَزْلِ','أَوْلَادُهُ وَوَفَاتُهُ']}
reasons7=[
'عنوان المصدر مستقل بالاسم والنسب، ولا يجعل النجم بين عمير وبن هاشم بداية خبر جديد.',
'استكمال النسب والسابقة والشهادة وصف افتتاحي متصل قبل إسناد البراء.',
'يجمع قدوم مصعب والسؤال عن رسول الله والجواب وقدوم ابن أم مكتوم؛ ثم أتانا تعبر الصفحة ولا ينقطع الحوار عند قال.',
'إسناد الأعمش عن خباب يتصل بخبر الهجرة والكفن وأمر تغطية الرأس والمقارنة بمن أينعت ثمرته؛ لا يفصل الأمر عن سياق الافتقار.',
'رواية إبراهيم عن بكاء عبد الرحمن نقل مستقل، مع إبقاء إسناد شعبة والسبب وخشية تعجيل الطيبات، ووصل ثوبًا واحدًا عبر الصفحة.',
'رواية ابن إسحاق عن القرظي والمبهم تحكي حال بردة مصعب ثم الحوار عن الرخاء والعبادة؛ لا أفصل الجواب عن السؤال ولا أحسم هوية المبهم.',
'طريق صالح بن كيسان عن سعد يقابل حال العيش قبل الهجرة وبعدها ثم شدة مصعب وحمله على العواتق؛ بقي وصف مصعب مع إسناد هذا الطريق.',
'انتقل المتكلم من وصف مصعب إلى تجربة شخصية في اقتيات الجلد بلفظ ولقد رأيتني؛ فصل داخلي مفهوم مع إبقاء سياق الرواية في الفقرة السابقة، لا خبر مخترع مستقل.',
'رواية ابن إسحاق في قتاله ومقتله ودعوى القاتل وإعطاء اللواء متصلة؛ لا تتحول دعوى قتلت محمدًا إلى خبر مقرر ولا يفصل الضمير عن مرجعه.'
]
uncertain7={
'يهدبها':'لفظ في وصف جني الثمرة؛ لم أحسم حركة عين الفعل من سياق الرواية وحده.',
'ظلف':'لفظ في وصف شدة العيش؛ أبقيت بنية اللفظ كما وردت بدل ضبط معجمي غير محقق.',
'ينقطع':'تحتمل صيغة الفعل وجهًا مبنيًا للمعلوم أو للمجهول مع به؛ لا أحسمها من السياق وحده.',
'فنعرض':'لفظ هيئة حمله على القسي؛ لا أحسم باب الفعل أو تضعيفه دون قرينة كافية.',
'شقات':'جمع في وصف قطع الجلد؛ لم أحسم ضبط المفرد والجمع من الرسم وحده.',
'قمئة':'اسم القاتل كما في المصدر؛ أبقيت ضبط الاسم للمراجعة دون حسم الهوية أو تصويب رسمه.'}
manifest=load(WORK/'manifest.json');manifest.update(curator='/root',reason='تنفيذ طلب المستخدم تحديث التراجم العشر الأولى بالتشكيل الكامل والفقرات السياقية مع حفظ الوسوم والمراجعات والنسخ السابقة.')
save(WORK/'manifest.json',manifest)
for bid in (7,8,9,10):
    folder=WORK/f'biography-{bid}'
    source=load(folder/'source.json');reading=load(folder/'reading.json')
    # Start from immutable published reading each rerun; never append ranges.
    reading=json.loads(source['reading_row']['payload'])
    offset=source['source_char_start'];text=source['full_text']
    if bid==7:
        voiced=(HERE/'biography-7.txt').read_text(encoding='utf-8')
        doc=export_document(source,SHA,'/root')
        doc['insertions']=derive_insertions(text,voiced,offset)
        for word in doc['words']:
            literal=text[word['start']-offset:word['end']-offset]
            if word['status']=='not_applicable':continue
            if literal in uncertain7:word.update(status='uncertain',reason=uncertain7[literal])
            else:word.update(status='complete');word.pop('reason',None)
        cut=offset+text.index('# ولقد رأيتني')
        for index,group in enumerate(reading['paragraph_groups']):
            if group['start']<cut<group['end']:
                end=group['end'];group['end']=cut;reading['paragraph_groups'].insert(index+1,{'start':cut,'end':end});break
        reasons=reasons7
    else:
        prior=PRIOR/('7ae7f691ffe2-8-9' if bid<10 else '7ae7f691ffe2-10-11')/f'biography-{bid}'
        oldsource=load(prior/'source.json');assert oldsource['full_text']==text and oldsource['source_char_start']==offset
        doc=load(prior/'diacritics.json');doc['reviewer']='/root'
        priorreading=load(prior/'reading.json')
        # Reviewed group improvements:8 joins the alternate death date back
        # to its dated report;9 joins its description and wine counterclaim;
        #10 keeps the exclusivity claim and its correction with the incident.
        reading['paragraph_groups']=priorreading['paragraph_groups']
        reasons=[note['reason'] for note in priorreading['paragraph_notes']]
    reading['reviewer']='/root'
    headings=source['pilot']['presentation']['headings']
    assert len(headings)==len(labels[bid])
    reading['heading_labels']=[{'at':h['at'],'label':label} for h,label in zip(headings,labels[bid],strict=True)]
    groups=reading['paragraph_groups'];assert len(groups)==len(reasons)
    reading['paragraph_notes']=[{**g,'context_start':groups[max(0,i-1)]['start'],'context_end':groups[min(len(groups)-1,i+1)]['end'],'reason':reasons[i]} for i,g in enumerate(groups)]
    coverage=validate_document(doc,source,reading,SHA,'/root')
    save(folder/'reading.json',reading);save(folder/'diacritics.json',doc)
    if bid==7:
        save(HERE/'biography-7-uncertain.json',[{'start':w['start'],'end':w['end'],'reason':w['reason']} for w in doc['words'] if w['status']=='uncertain'])
        save(HERE/'biography-7-reading.json',reading)
    print(bid,coverage)
print(validate_batch(WORK,require_audit=False))
