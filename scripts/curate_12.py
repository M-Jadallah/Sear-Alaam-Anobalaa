"""Reproduce hand-read pilot annotations for biographies 1 and 2.

The ordinal selections below were made after reading every source segment,
including the connected narratives and their source criticism. No runtime
classifier, canonical-data writes, or human-review claims are involved.
"""
from pathlib import Path
import hashlib
import json
import re
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
DB = sqlite3.connect((ROOT / "data/library.sqlite3").as_uri() + "?mode=ro", uri=True)
DB.row_factory = sqlite3.Row
with (ROOT / "data/source.mARkdown").open(encoding="utf-8", newline="") as handle:
    SOURCE = handle.read()
SHA = hashlib.sha256((ROOT / "data/source.mARkdown").read_bytes()).hexdigest()
CATEGORIES = {row["name"]: row["category"] for row in DB.execute(
    "SELECT t.name,c.name category FROM topics t JOIN topics c ON c.id=t.parent_id")}


def document(bid):
    bio = dict(DB.execute("SELECT * FROM biographies WHERE id=?", (bid,)).fetchone())
    assert SOURCE[bio["source_char_start"]:bio["source_char_end"]] == bio["full_text"]
    raw_rows = [dict(row) for row in DB.execute(
        "SELECT * FROM segments WHERE biography_id=? ORDER BY segment_ordinal", (bid,))]
    segments = [{"segment_id": row["id"], "assessment": "no_clear_topic",
                 "note": "فُحص النص ضمن سياقه المتصل؛ لم يُفرد لهذا الجزء وسم مستقل، ولا يعني ذلك خلوه من المعاني.",
                 "topics": [], "existing_hits": []} for row in raw_rows]
    output = {"source_sha256": SHA, "biography_id": bid,
              "source_char_start": bio["source_char_start"], "source_char_end": bio["source_char_end"],
              "reviewer": "مراجعة نصية بواسطة وكيل", "segments": segments,
              "presentation": {key: [] for key in ("headings", "quotes", "poetry", "lists", "bold", "keywords", "names", "footnotes")},
              "gold": []}

    def topic(ordinal, name, reason, first=None, last=None, *, category=None,
              relation="event", scope="biography_subject", stance="reported", aliases=()):
        evidence = raw_rows[ordinal - 1]
        first = first or ordinal
        last = last or ordinal
        item = {"topic": name, "category": category or CATEGORIES.get(name, "التاريخ والمجتمع"),
                "aliases": list(aliases), "rationale": reason, "relation_type": relation,
                "subject_scope": scope, "stance": stance,
                "evidence_start": evidence["source_char_start"], "evidence_end": evidence["source_char_end"],
                "context_start": raw_rows[first - 1]["source_char_start"], "context_end": raw_rows[last - 1]["source_char_end"]}
        segments[ordinal - 1]["topics"].append(item)
        segments[ordinal - 1].update(assessment="supported", note="فُحصت دلالة الشاهد في سياق الخبر؛ الربط اقتراح نصي ينتظر قرار الإنسان.")

    def mark(kind, first, last=None):
        output["presentation"][kind].append({"start": raw_rows[first - 1]["source_char_start"],
                                              "end": raw_rows[(last or first) - 1]["source_char_end"]})

    def heading(ordinal, label):
        output["presentation"]["headings"].append({"at": raw_rows[ordinal - 1]["source_char_start"], "label": label})

    def phrase(kind, phrase_text, ordinal=None):
        # Match only a manually selected literal expression. Technical OpenITI
        # continuation/line separators can occur between its original words.
        pattern = r"(?:\s|~~|ms[0-9]+)+".join(map(re.escape, phrase_text.split()))
        source_a = raw_rows[ordinal - 1]["source_char_start"] if ordinal else bio["source_char_start"]
        source_b = raw_rows[ordinal - 1]["source_char_end"] if ordinal else bio["source_char_end"]
        for match in re.finditer(pattern, SOURCE[source_a:source_b]):
            output["presentation"][kind].append({"start": source_a + match.start(), "end": source_a + match.end()})

    def gold(question, first, last):
        output["gold"].append({"question": question, "start": raw_rows[first - 1]["source_char_start"],
                               "end": raw_rows[last - 1]["source_char_end"]})

    return output, raw_rows, topic, mark, heading, phrase, gold


one, rows1, t, mark, heading, phrase, gold = document(1)
t(1, "الكنية والنسبة", "النص يسرد نسب صاحب الترجمة ونسبته القرشية الفهرية.", relation="description")
t(2, "الخلافة والسياسة", "خبر عرض أبي بكر تولية أبي عبيدة، وتكملته تصرح بأهليته.", 2, 3)
t(5, "الأمانة", "لفظ أمين الأمة منقول في وصف أبي عبيدة، وليس اسم راوٍ.", relation="description")
t(5, "المناقب والفضائل", "وردت الشهادة بالجنة في العبارة نفسها؛ ننقلها بوصفها خبر النص.", category="التراجم والرجال", relation="description", aliases=("فضائل الصحابة", "مناقب الصحابة"))
t(7, "التلاميذ", "تعداد من روى عن أبي عبيدة بأسمائهم.", relation="description")
t(8, "الرواية والسماع", "بيان مرويات صاحب الترجمة ومواضعها في الكتب المذكورة.", relation="discussion")
t(11, "الدجال والفتن", "متن حديث الإنذار من الدجال؛ يُحفظ الإسناد وحكم النص اللاحق ضمن السياق.", 10, 17, category="العقيدة", relation="saying", scope="general_context", aliases=("فتنة الدجال", "التحذير من الدجال"))
t(17, "صحيح الحديث", "تقييم النص للخبر بأنه حسن غريب؛ ليس حكمًا جديدًا من الوكيل.", 10, 17, relation="discussion", scope="general_context", stance="criticism")
t(19, "الصفة الخلقية", "وصف هيئة أبي عبيدة بنص الراوي.", 18, 19, category="التراجم والرجال", relation="description", aliases=("هيئة البدن", "الصفات الجسمية"))
t(21, "السبق إلى الإسلام", "خبر عرض الإسلام على الجماعة وإسلامهم قبل دار الأرقم.", 20, 21, category="التراجم والرجال", aliases=("السابقون الأولون", "المبادرة إلى الإسلام"))
t(22, "غزوة أحد", "نزع أبي عبيدة حلقتي المغفر من وجه النبي وفقد ثنيتيه في الخبر.", category="التاريخ والمجتمع", aliases=("وقعة أحد", "الدفاع عن النبي"))
t(24, "الخلافة والسياسة", "قول أبي بكر بالسقيفة في رضاه بعمر وأبي عبيدة.", 23, 24, relation="saying")
t(25, "الهجرة إلى الحبشة", "ذُكرت الهجرة للحبشة، وتُحفظ عبارة المؤلف المشروطة التي تليها.", 25, 27, category="التاريخ والمجتمع", stance="contested", aliases=("هجرة الحبشة", "المهاجرون إلى الحبشة"))
t(28, "حفظ القرآن", "عبارة صريحة في عد أبي عبيدة ممن جمعوا القرآن العظيم.", relation="description")
t(34, "حسن الخلق", "التنازل عن الإمارة لعمرو في سياق وصف حسن الخلق ولين الشيمة.", 29, 34, category="الأخلاق والآداب", aliases=("لين الجانب", "حسن المعاملة"))
t(34, "التواضع", "تسليم الإمارة عند اختلاف الأمراء مع بقاء السياق المفسر.", 29, 34)
t(36, "الأمانة", "نص القول في أمين هذه الأمة.", 35, 36, relation="saying")
t(40, "الخلافة والسياسة", "تمني عمر استخلاف أبي عبيدة إن بقي حيًا مع تعليله بأمانته.", 38, 43, relation="saying")
t(45, "المناقب والفضائل", "الخبر في فضل معاذ بن جبل المذكور، لا صاحب الترجمة.", 44, 45, category="التراجم والرجال", relation="saying", scope="mentioned_person")
t(56, "الصحبة", "جواب عائشة في أحب الصحابة مع عرض الطريق المخالف قبله.", 46, 56, relation="saying", scope="general_context", stance="contested")
t(59, "الأمانة", "إرسال أبي عبيدة بعد وصف المبعوث بأنه أمين، مع الخبر قبله.", 57, 60)
t(62, "الأمانة", "متن أمين الأمة مع عبارة التخريج التي تسبقه.", 61, 62, relation="saying")
t(65, "التواضع", "عرض أبي عبيدة تقديم خالد للصلاة ثم امتناع خالد معللًا بفضل أبي عبيدة.", 63, 66)
t(65, "المدن والأمصار", "ذكر حصار دمشق في الحدث نفسه، لا مجرد عنوان كتاب.", 63, 66, scope="general_context")
t(66, "الأمانة", "تعليل خالد عدم التقدم بما سمع في أمانة أبي عبيدة.", 65, 66, relation="saying")
t(70, "الأمانة", "تعيين أبي عبيدة استجابة لطلب أسقفي نجران رجلا أمينا.", 67, 72)
t(76, "محاسبة النفس", "بكاؤه ومحاسبته نفسه على كثرة الرقيق والخيل، مع وصف الخبر بأنه غريب.", 73, 78, relation="saying")
t(75, "الزهد", "نص تقليل الخدم والدواب في رواية يصفها المصدر بأنها غريبة؛ لا يُرفع إلى خبر محقق.", 73, 78, relation="saying", scope="general_context", stance="contested")
t(81, "علل الحديث", "المصدر يصف الخبر السابق بأنه مرسل؛ لا يُسقط هذا النقد من السياق.", 79, 81, relation="discussion", scope="general_context", stance="criticism")
t(82, "حسن الخلق", "وصف صريح لأبي عبيدة بالحلم وحسن الخلق.", category="الأخلاق والآداب", relation="description")
t(82, "التواضع", "وصف صريح بالتواضع في النص.", relation="description")
t(85, "الثناء والنقد", "قول عمر في تمنيه رجالًا مثل أبي عبيدة.", 83, 85, relation="saying")
t(92, "القضاء والولايات", "خبر توليته أموال المسلمين مع تصحيح المؤلف لتاريخ إنشاء بيت المال.", 92, 93)
t(94, "القضاء والولايات", "التوجيه إلى الشام وعزل خالد وتولية أبي عبيدة.")
t(98, "الصبر", "رسالة عمر للمحاصرين في الشام تربط الشدة بالفرج وتستشهد بالمصابرة.", 97, 103, relation="saying", scope="general_context")
t(100, "الزهد", "تذكير أبي عبيدة بزوال الدنيا في جوابه لعمر، ويُحفظ رد عمر في السياق.", 97, 103, relation="saying")
t(103, "الجهاد", "دعوة عمر أهل المدينة إلى الجهاد بعد قراءة رسالة أبي عبيدة.", 97, 103, category="الفقه وأصوله", relation="saying", scope="mentioned_person")
t(106, "الثناء والنقد", "دفاع معاذ عن أبي عبيدة عند انتقاص بعض الناس من قيادته.", 104, 107, relation="saying")
t(113, "الزهد", "قلة أثاث بيت القائد، مع قوله في كفاية المتاع.", 108, 116)
t(121, "الزهد", "كسيرات الطعام ومتاع البيت القليل في زيارة عمر.", 117, 124)
t(124, "الزهد", "تعليق صريح يصف ما سبق بالزهد الخالص؛ لا يُستنتج من اسم كتاب.", 117, 124, relation="discussion")
t(127, "الكرم", "تفريق المال الذي بعث به عمر بدل ادخاره.", 125, 129)
t(131, "التوبة", "دعوة إلى تدارك السيئات القديمة بالحسنات الحديثة، لا اسم سورة.", 130, 131, relation="saying", scope="general_context")
t(133, "التقوى", "تصريحه بتمني منزلة من يفضله بالتقوى أيًا كان لونه.", 132, 133, relation="saying")
t(133, "التواضع", "تقديم التفاضل بالتقوى على الانتساب إلى قريش.", 132, 133, relation="saying")
t(135, "الخوف من الله", "قول التمني المنقول عن أبي عبيدة؛ لا يساوى بقول عمران التالي.", 134, 136, relation="saying")
t(141, "الإيثار", "امتناعه عن مفارقة جنده في الطاعون مع طلب عمر استبقاءه.", 137, 145, category="الأخلاق والآداب", relation="saying", aliases=("مواساة الجنود", "عدم الاستئثار بالنجاة"))
t(144, "الوباء والمجاعة", "وفاته وخبر انكشاف الطاعون في السياق.", 137, 145)
t(152, "الزكاة والصدقة", "حديث النفقة على الأهل وفي سبيل الله مع تتمة الحديث؛ ليس كل المذكور زكاة.", 146, 153, relation="saying", scope="general_context")
t(153, "المرض وتكفير الخطايا", "تتمة الخبر في الابتلاء الجسدي والحطة، مع سؤال المريض في صدره؛ لا ينص هذا الموضع على فعل الصبر.", 146, 153, category="القلب والسلوك", relation="saying", scope="general_context", aliases=("الابتلاء الجسدي", "حط الخطايا بالمرض"))
t(157, "العبادات", "نص حديث الصيام جنة؛ يُحفظ الإسناد وحدود القول.", 154, 157, relation="saying", scope="general_context")
t(160, "الفتوى", "الرجوع عن المنع من أكل الحوت إلى إباحته في سياق السرية.", 158, 161, relation="saying")
t(163, "الفتن والحروب", "خبر أجنادين وفحل ومرج الصفر في فتح الشام.", 162, 164)
t(165, "القضاء والولايات", "التقليد هنا كتاب التولية؛ يُربط بالولاية لا بالتقليد الفقهي.", 164, 167)
t(166, "الصلح والعهد", "إمضاء أبي عبيدة الصلح في فتح دمشق مع اختلاف طريقة دخول الجيش.", 164, 167, category="التاريخ والمجتمع", aliases=("الوفاء بالصلح", "معاهدة أهل دمشق"))
t(167, "الفتن والحروب", "خبر اليرموك في سياق قيادة أبي عبيدة.", 162, 167)
t(173, "الدعاء", "دعاؤه المنقول في خبر طاعون عمواس، دون حكم جديد على الخبر.", 168, 175, relation="saying")
t(175, "الرضا", "رجاؤه البركة في البثرة المرتبطة بالطاعون في الخبر.", 168, 175, relation="saying")
t(177, "العبادات", "سيره إلى بيت المقدس للصلاة واستخلافه معاذًا على الناس.", 176, 180)
t(181, "الوباء والمجاعة", "تفسير اسم طاعون عمواس وموقع القرية في النص نفسه.", 180, 182, relation="discussion", scope="general_context")
t(183, "الوفاة", "التأريخ الأول للوفاة وعمره وصفته مع الرواية المخالفة التالية.", 183, 185, relation="description", stance="contested")
t(185, "الوفاة", "الرأي المخالف في سنة الوفاة يظل ظاهرًا ولا تُحسم إحدى السنتين آليًا.", 183, 185, relation="discussion", stance="contested")

for ordinal, title in [(1,"النسب والسابقة"),(7,"الرواية ومن روى عنه"),(18,"الصفة والإسلام والمشاهد"),
                       (29,"الإمارة والأمانة"),(73,"وصايا وأخبار في الخلق"),(92,"الولاية والرسائل"),
                       (108,"أخبار في الزهد وبذل المال"),(130,"أقوال ومحاسبة النفس"),(137,"الطاعون والمرض"),
                       (158,"السرايا وفتح الشام"),(168,"أواخر أخباره ووفاته")]: heading(ordinal,title)
for first,last in [(10,14),(24,24),(31,34),(39,45),(47,56),(68,72),(74,77),(84,85),(89,89),
                   (98,103),(109,116),(118,124),(126,129),(131,131),(133,136),(138,145),
                   (147,153),(156,157),(159,160),(171,175)]: mark("quotes",first,last)
for literal in ["أمين الأمة","حسن الخلق","الحلم الزائد","الزهد الخالص","لا أرغب بنفسي عنهم","بادروا السيئات القديمات بالحسنات الحديثات"]: phrase("bold",literal)
for literal in ["السقيفة","جمع القرآن العظيم","التواضع","الزهد الخالص","الطاعون","طاعون عمواس"]: phrase("keywords",literal)
# These are two explicit three-item enumerations in the same reported saying.
for item_phrases in [("خادم يخدمك", "خادم يسافر معك", "خادم ms0910 يخدم أهلك"),
                     ("دابة لرحلك", "دابة لثقلك", "دابة لغلامك")]:
    items=[]
    for literal in item_phrases:
        a=SOURCE.index(literal,rows1[74]["source_char_start"],rows1[74]["source_char_end"])
        items.append({"start":a,"end":a+len(literal)})
    one["presentation"]["lists"].append({"start":items[0]["start"],"end":items[-1]["end"],"ordered":False,"items":items})
gold("قائد يعيش في منزل لا يكاد يوجد فيه أثاث رغم منصبه، ويبكي زائره عندما يرى حاله",117,124)
gold("رجل يوزع المال الذي أرسل إليه بدل أن يحتفظ به، فيفرح المرسل بما صنع",125,129)
gold("قائد يرفض مفارقة الجنود عند انتشار المرض ويقول إنه لا يريد النجاة وحده",137,145)

two, rows2, t, mark, heading, phrase, gold = document(2)
t(1,"الكنية والنسبة","تعداد النسب والكنية لصاحب الترجمة.",relation="description")
t(2,"المناقب والفضائل","النص يعده من العشرة المشهود لهم بالجنة.",category="التراجم والرجال",relation="description")
t(4,"الرواية والسماع","عدد المرويات في الصحيحين كما يذكره النص.",3,4,relation="discussion")
t(5,"التلاميذ","قائمة من رووا عن طلحة ومنهم أبناؤه.",relation="description")
t(6,"الصفة الخلقية","الوصف الأول لهيئته، مع الرواية التالية التي تختلف في لون بشرته.",6,9,category="التراجم والرجال",relation="description",stance="contested")
t(10,"السبق إلى الإسلام","السبق إلى الإسلام والأذى فيه في خبر صاحب الترجمة.",category="التراجم والرجال",aliases=("السابقون الأولون","المبادرة إلى الإسلام"))
t(10,"الهجرة","تصريح الخبر بهجرته بعد الأذى؛ لا يحدد هذا الموضع جهة الهجرة.",category="التاريخ والمجتمع")
t(11,"القضاء والولايات","خبر جعل عمر طلحة على المهاجرين بالجابية.")
t(12,"غزوة أحد","شلل يده مما وقى به النبي في الخبر.",12,21,category="التاريخ والمجتمع")
t(18,"المناقب والفضائل","نص أوجب طلحة مع وصف الإسناد السابق بأنه حسن.",16,18,category="التراجم والرجال",relation="saying")
t(20,"غزوة أحد","شهادة قيس في اليد التي وقى بها النبي.",19,21,category="التاريخ والمجتمع")
t(38,"غزوة أحد","خبر قتال طلحة وقطع أصابعه، مع عبارة رواته ثقات التالية.",22,42,category="التاريخ والمجتمع")
t(45,"غزوة أحد","خبر بقاء طلحة وسعد عند النبي في القتال.",43,46,category="التاريخ والمجتمع")
t(49,"الصحبة","توقير الصحابة للنبي واستعانتهم بالأعرابي للسؤال.",47,55,relation="description",scope="general_context")
t(58,"المناقب والفضائل","القول المنقول في النبي والصديق والشهيد على حراء.",56,58,category="التراجم والرجال",relation="saying",scope="general_context")
t(62,"الجنة والنار","نص جوار طلحة والزبير في الجنة مع اختلاف الطريق اللاحق.",61,64,relation="saying",stance="contested")
t(66,"الكرم","شراء البئر وإطعام الناس؛ خبر في البذل لا مجرد لقب في اسم راوٍ.",65,66)
t(68,"الكرم","الألقاب في خبر يليه نص إسناده لين؛ يُحفظ النقد معه.",67,69,relation="description",stance="contested")
t(69,"علل الحديث","نقد إسناد خبر ألقاب طلحة في المصدر.",67,69,relation="discussion",scope="general_context",stance="criticism")
t(71,"الكرم","شهادة قبيصة في كثرة عطائه بغير سؤال.",70,71,relation="description")
t(79,"الكرم","تفريق المال بين المهاجرين والأنصار بعد مشورة الزوجة.",72,82)
t(77,"النكاح والأسرة","مشورة الزوجة في توزيع المال وحوارهما في سياق الأسرة.",72,82,relation="saying",scope="general_context")
t(76,"محاسبة النفس","قلقه من مبيت المال في بيته ومحاسبته نفسه.",72,82,relation="saying")
t(86,"صلة الرحم","عرضه الأرض أو ثمنها على من سأله بالرحم.",83,88,category="الأخلاق والآداب",relation="saying",aliases=("بر الأقارب","الإحسان إلى الرحم"))
t(91,"الكرم","عرض الحائط أو ثمنه في رواية يصفها المصدر بالانقطاع وضعف الكديمي.",89,92,relation="saying",stance="contested")
t(92,"علل الحديث","حكم النص بالانقطاع وضعف الكديمي يرافق الخبر ولا ينشئ الوكيل تصحيحًا.",89,92,relation="discussion",scope="general_context",stance="criticism")
t(94,"غزوة أحد","تفصيل جراح طلحة وحمله النبي ومقاتلته دونه في الخبر.",93,94,category="التاريخ والمجتمع")
t(100,"الكرم","توزيع المال على قومه بعد أن غمه وجوده.",95,102)
t(104,"الزهد","خبر أرقه من مخافة المال ثم تفريقه؛ لا يستنتج أنه ترك التجارة.",103,104)
t(107,"رعاية الفقراء","إعالة المحتاجين من بني تيم وقضاء ديونهم ومواصلته عائشة.",105,108,category="الأخلاق والآداب",aliases=("كفاية المحتاج","إعانة المعوزين"))
t(108,"قضاء الديون","خبر قضائه دين عبيد الله بن معمر وعبد الله بن عامر.",107,108,category="الفقه وأصوله",aliases=("سداد الدين","الوفاء بالدين"))
t(114,"الثناء والنقد","ترحم معاوية وثناؤه على طلحة بعد ذكر تركته.",111,114,relation="saying")
t(119,"المناقب والفضائل","أبيات في السابقين من الصحابة؛ شعر منقول لا تقرير مستقل.",115,121,category="التراجم والرجال",relation="saying",scope="general_context")
t(123,"الفتن والحروب","خروج طلحة والزبير وعائشة للمطالبة بدم عثمان كما يرويه الخبر.",122,127)
t(126,"محاسبة النفس","قول طلحة في انقسام الجماعة وشعوره بالمسؤولية عن أمر عثمان، مع تعليق النص بعده.",122,127,relation="saying",stance="contested")
t(129,"الفتن والحروب","محاولة كعب بن سور حقن الدماء يوم الجمل؛ الفعل يخص شخصًا مذكورًا.",128,131,scope="mentioned_person")
t(136,"الدعاء","دعاء طلحة بشأن عثمان في سياق الفتنة، دون تقرير تاريخي جديد.",135,136,relation="saying")
t(138,"الوفاة","روايات رمي مروان طلحة بالسهم مع تتمتها وتعليق المؤلف.",137,143)
t(145,"الثناء والنقد","حزن علي على طلحة بعد مقتله، مع شرح الأصمعي للعبارة.",144,146,relation="saying",scope="mentioned_person")
t(149,"علل الحديث","خبر ترحم علي يتلوه حكم النص بأنه مرسل؛ يظهر معه دون إسقاطه.",147,150,relation="discussion",scope="general_context",stance="criticism")
t(155,"الرواية والسماع","تفسير طلحة كثرة سماع أبي هريرة لملازمته باب النبي.",152,155,relation="saying",scope="mentioned_person")
t(155,"الصدق","استبعاد طلحة تعمد صاحب الخير القول على النبي بما لم يقل، في سياق الدفاع عن أبي هريرة.",152,155,relation="saying",scope="mentioned_person")
t(158,"الموت والآخرة","انشغال طلحة بكلمة عند حضور الموت، مع سؤال عمر وجوابه.",156,163,relation="saying",scope="general_context")
t(167,"التفسير","استشهاد علي بآية نزع الغل وحديثه مع ابن طلحة، مع اعتراض الرجلين ورد علي.",164,169,relation="saying",scope="mentioned_person",stance="contested")
t(169,"الصحبة","قول علي في رجائه الأخوة مع طلحة وترحيبه بابنه بعد الجمل، لا اعتماد اعتراض الرجلين.",164,169,relation="saying",scope="mentioned_person")
t(176,"غزوة أحد","قول طلحة في انتشار الجراح في جسده.",170,176,category="التاريخ والمجتمع",relation="saying")
t(178,"التركة والأموال","مقدار الأموال والعقار في رواية المصدر مع تعليق الاستغراب التالي.",177,179,category="التراجم والرجال",relation="description",stance="contested",aliases=("الأموال المتروكة","ميراث المال"))
t(186,"الوفاة","الروايات في تاريخ قتله وعمره مع موضع قبره.",186,188,relation="description",stance="contested")
t(190,"العبادات","وصف محمد السجاد ابن طلحة بالعبادة؛ يخص شخصًا مذكورًا لا طلحة.",189,191,relation="description",scope="mentioned_person")
t(191,"بر الوالدين","قول علي في محمد السجاد صرعه بره بأبيه؛ لا يُنسب فعل الابن للأب.",189,191,relation="saying",scope="mentioned_person")
for ordinal in range(181,186):
    two["segments"][ordinal-1].update(assessment="uncertain",note="خبر منام ونقل قبر مع اختلاف في صاحب الرؤيا؛ تُرك بلا وسم بحثي مستقل إلى المراجعة البشرية.")
for ordinal,title in [(1,"النسب والسابقة والرواية"),(6,"الصفة ومشاهده الأولى"),(13,"أخبار في الدفاع عن النبي يوم أحد"),
                      (47,"أقوال في مناقبه"),(65,"أخبار البذل ومشاورة الأهل"),(93,"جراح أحد وأخبار المال"),
                      (115,"أبيات في السابقين"),(122,"خبر الفتنة ويوم الجمل"),(144,"ما روي بعد مقتله"),
                      (152,"أقوال في الرواية والموت"),(164,"ذكره بعد الجمل ومشاهد أحد"),(177,"التركة والوفاة وولده")]:heading(ordinal,title)
for first,last in [(14,15),(18,18),(23,42),(48,55),(57,58),(62,64),(66,66),(71,71),(73,82),
                   (85,88),(90,92),(96,102),(123,127),(133,136),(138,143),(145,146),(148,150),
                   (153,155),(156,163),(165,169),(171,171)]:mark("quotes",first,last)
mark("poetry",116,121);mark("poetry",173,175)
for literal in ["وقى بها النبي","فبات أرقا","فقسمه فيهم","رواته ثقات","إسناده لين","إسناده منقطع، مع ضعف الكديمي","مرسل."]:phrase("bold",literal)
for literal in ["يوم أحد","المهاجرين والأنصار","عائلا إلا كفاه","قضى دينه","يوم الجمل"]:phrase("keywords",literal)
gold("رجل يقلق من بقاء ثروة في منزله، فتشير عليه زوجته أن يوزعها على أصحابه",72,82)
gold("رجل يكفل فقراء أقاربه ويسدد ديونهم ويصل غيرهم من ماله",105,108)
gold("صحابي يشرح سبب كثرة رواية صاحبه لأن صاحبه كان ملازمًا للنبي بينما كانوا يزورونه صباحًا ومساء",152,155)


# Every legacy candidate was read in its connected source context. Decisions
# remain recommendations; they do not edit canonical topic_hits.
DECISIONS = {
701:("modify","صلة بالخلافة صحيحة، ويجب توسيع الشاهد إلى خبر السقيفة لا الاكتفاء بكلمة الخلافة."),
702:("keep","أمين الأمة وصف صريح لصاحب الترجمة في سياق المناقب."),
703:("modify","حوار اختلاف الإمارة يحتاج سياقه الكامل، وأثره الظاهر تسليم الإمارة وحسن الخلق."),
704:("keep","القول يصف أمانة أبي عبيدة، وتُحفظ العبارة كاملة."),
705:("exclude","الصوفي نسبة راوٍ هو أحمد بن علي الصوفي في الإسناد؛ لا يثبت موضوع التصوف."),
706:("modify","دمشق مكان حصار في خبر تقديم خالد للصلاة؛ لا يكفي الاسم وحده."),
707:("keep","ذكر الأمانة في تعليل خالد عدم تقدمه، مع سياق المتكلم."),
708:("modify","طلب الأسقفين أمينا تُكمله تسمية أبي عبيدة، فلا يعزل الطلب عن الجواب."),
709:("modify","وعد إرسال أمين تُكمله تسمية أبي عبيدة في السياق التالي."),
710:("exclude","الزهد هنا عنوان كتاب لابن المبارك؛ تُفهرس دلالة الخبر في قلة المتاع بشاهد آخر."),
711:("exclude","أمير المؤمنين نداء عمر في خبر قلة المتاع، وليس موضوع تولية أبي عبيدة."),
712:("exclude","أنت أمير قرينة داخل خبر زهد القائد؛ لا يكفي لإفراد موضوع الولاية لهذا المقطع."),
713:("exclude","أمير المؤمنين نداء عمر في خبر الطعام، وليس شاهد تولية مستقلًا."),
714:("keep","تعليق صريح بالزهد الخالص يجب وصله بقصة المتاع والطعام السابقة."),
715:("modify","الطاعون موضوع الخبر، ويحتاج رسالة عمر وامتناع أبي عبيدة عن مفارقة جنده."),
716:("exclude","أمير المؤمنين ذكر لقب عمر في الرسالة، وليس دليلا على موضوع القضاء والولايات."),
717:("modify","انكشاف الطاعون ووفاته مرتبطان بالقصة السابقة؛ يوسع السياق."),
718:("keep","حديث الصيام صريح مع حفظ حدود القول والإسناد دون اعتماد حديثي جديد."),
719:("modify","دمشق مكان الفتح داخل الحدث؛ يتطلب السياق العسكري لا الكلمة وحدها."),
720:("exclude","التقليد في هذا الموضع كتاب تولية أبي عبيدة، وليس التقليد الفقهي."),
721:("modify","صلة بالفتح والمدينة صحيحة؛ تدرج مع خبر التولية والصلح."),
722:("exclude","الزهد هنا اسم كتاب، والخبر التالي عن الطاعون والمرض والدعاء."),
723:("modify","تفسير لفظ الطاعون يحتاج ضم بيان قرية عمواس السابق."),
724:("modify","عبارة كذلك في وفاته تحيل إلى التأريخ السابق؛ لا تعرض منفردة."),
725:("modify","قول مخالف في سنة الوفاة يجب أن يعرض مع التأريخ الآخر دون حسم آلي."),
726:("exclude","ابن أبي الجود اسم راوٍ داخل الإسناد، ولا يثبت الكرم."),
727:("modify","قول الجوار في الجنة يحتاج إسناده واختلاف الطريق التالي."),
728:("modify","لقب الجود وارد في خبر يليه إسناده لين، فيجب حفظ النقد معه."),
729:("keep","سياق صريح في سماع أبي هريرة وملازمته النبي؛ لا ينسب سماعه إلى طلحة."),
730:("exclude","وجود سمع من في بيان ملازمة أبي هريرة لا يقدم فهرسًا لشيوخ طلحة."),
731:("modify","هذه الجملة اعتراض رجلين، ويجب عرض رجاء علي وردّه معها بدل اعتماد الاعتراض."),
}

NAME_ALLOWLIST = """أبو عبيدة بن الجراح|أبا عبيدة بن الجراح|أبي عبيدة بن الجراح|أبو عبيدة|أبا عبيدة|أبي عبيدة|عمر بن الخطاب|أبو بكر الصديق|أبي بكر|أبو بكر|أبا بكر|معاذ بن جبل|خالد بن الوليد|عمرو بن العاص|عبد الرحمن بن عوف|عبد الله بن سراقة|عبد الله بن شقيق|حماد بن سلمة|محمد بن عبد السلام|عبد المعز بن محمد|تميم بن أبي سعيد|محمد بن عبد الرحمن|أبو عمرو بن حمدان|أبو يعلى|عبد الله بن معاوية|خالد الحذاء|جابر بن عبد الله|العرباض بن سارية|أبو أمامة الباهلي|سمرة بن جندب|عبد الرحمن بن غنم|أبو القاسم المعري|الزبير بن بكار|موسى بن عقبة|أنس|عائشة|ابن مسعود|حذيفة|صلة بن زفر|عبد الله بن أحمد|أحمد بن علي الصوفي|أبو غالب الباقلاني|عبد الله بن عثمان بن خثيم|أبو الزبير|مالك بن يخامر|محمد بن عمر|ابن مظعون|عبيدة بن الحارث|الوليد بن مسلم|الحارث بن عميرة|شهر بن حوشب|الحسن|معمر|ثابت البناني|عمران بن حصين|عبد الله بن عمر|ابن عمر|ابن المبارك|ابن سعد|ابن إسحاق|مالك|نافع|معن بن عيسى|طارق|عياض بن غطيف|تحيفة|محمد بن عمرو المروزي|عروة بن رويم|أبو حفص الفلاس|ابن عائذ|أبو مسهر|يزيد بن عبيدة|يزيد بن أبي سفيان|شرحبيل بن حسنة|طلحة بن عبيد الله|طلحة|موسى بن طلحة|عيسى|يحيى|السائب بن يزيد|مالك بن أوس|أبو عثمان النهدي|قيس بن أبي حازم|الأحنف بن قيس|أبو سلمة بن عبد الرحمن|عبد الله بن مندة|إبراهيم بن المنذر الحزامي|عبد العزيز بن عمران|إسحاق بن يحيى|ابن عساكر|الصلت بن دينار|أبو نضرة|ابن أبي الجود|ابن الطلابة|عبد العزيز الأنماطي|أبو طاهر المخلص|داود بن رشيد|قيس|سعد|أبو كريب|يونس بن بكير|أبو هريرة|عثمان|علي|الزبير|عائشة بنت طلحة|عقبة بن علقمة|ابن زيدان البجلي|نضر بن منصور|دحيم|سلمة بن الأكوع|سليمان بن أيوب|قبيصة بن جابر|أم كلثوم بنت الصديق|المسلم بن علان|عمر بن محمد|الحسن بن دينار|علي بن زيد|ابن عمران|سعدى بنت عوف|عبيد الله بن معمر|عبد الله بن عامر بن كريز|معاوية|الرياشي|علقمة بن وقاص|عروة بن الزبير|أبو بكر بن عبد الرحمن|كعب بن سور|حكيم بن جابر|مروان بن الحكم|أبان|أبو رجاء|جويرية بن أسماء|الأصمعي|عبد الله بن إدريس|طلحة بن مصرف|عمران بن طلحة|الحارث الأعور|محمد السجاد|سعيد بن عامر الضبعي|المثنى بن سعيد|المسعودي|يحيى بن بكير|خليفة بن خياط|أبو نصر الكلاباذي""".split("|")

for output in (one,two):
    bid = output["biography_id"]
    rows = rows1 if bid == 1 else rows2
    for row, entry in zip(rows,output["segments"]):
        for hit in DB.execute("SELECT id FROM topic_hits WHERE segment_id=? ORDER BY id",(row["id"],)):
            decision,reason=DECISIONS[hit["id"]]
            entry["existing_hits"].append({"hit_id":hit["id"],"decision":decision,"reason":reason})
    a,b=output["source_char_start"],output["source_char_end"]
    for match in re.finditer(r"\([0-9]+\)",SOURCE[a:b]):
        output["presentation"]["footnotes"].append({"start":a+match.start(),"end":a+match.end()})
    # Resolve overlapping literal names longest-first; this colors occurrences,
    # never binds an ambiguous short name to a person record.
    spans=[]
    for literal in sorted(set(NAME_ALLOWLIST),key=len,reverse=True):
        pattern=r"(?<!\w)"+r"(?:\s|~~|ms[0-9]+)+".join(map(re.escape,literal.split()))+r"(?!\w)"
        for match in re.finditer(pattern,SOURCE[a:b]):
            x,y=a+match.start(),a+match.end()
            if not any(x<old["end"] and y>old["start"] for old in spans):spans.append({"start":x,"end":y})
    output["presentation"]["names"]=sorted(spans,key=lambda span:span["start"])
    for key in ("quotes","poetry","lists","bold","keywords","names","footnotes"):
        for span in output["presentation"][key]:assert a<=span["start"]<span["end"]<=b
    for segment,row in zip(output["segments"],rows):
        assert segment["segment_id"]==row["id"]
        for topic in segment["topics"]:
            assert a<=topic["context_start"]<=topic["evidence_start"]<topic["evidence_end"]<=topic["context_end"]<=b
    target=ROOT/"reference/pilot"/f"biography-{bid}.json"
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(output,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"biography_id":bid,"segments":len(output["segments"]),"topic_links":sum(len(s["topics"]) for s in output["segments"]),
                      "legacy_decisions":sum(len(s["existing_hits"]) for s in output["segments"]),
                      "presentation":{k:len(v) for k,v in output["presentation"].items()},"gold":len(output["gold"])},ensure_ascii=False))
