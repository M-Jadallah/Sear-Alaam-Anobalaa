"""Transparent local query hypotheses; not a language model or a claim of fact."""
import hashlib
import re
from .normalization import normalize_arabic
from .semantic import canonical_connection

# Retrieval vocabulary only. These associations never classify a source passage.
CONCEPTS = [
    ("الإخلاص", ["النية", "لله", "الرياء"], ["يريد وجه الله", "العمل لله", "إخلاص النية", "خالصا لله"]),
    ("الرجوع عن الرأي", ["رجع عن", "تراجع", "اعترف بخط", "غير رأيه", "تصحيح الخط"], ["رجع عن", "أخطأت", "استغفر", "الحق"]),
    ("قبول النقد والتواضع", ["قبل النقد", "قبول النصيحة", "تواضع", "يعترف بخط"], ["نصح", "تواضع", "الحق", "أخطأت"]),
    ("طلب العلم", ["طلب العلم", "الرحلة", "سافر ليتعلم", "رحل ليتعلم", "التعلم"], ["طلب العلم", "طلب الحديث", "رحل", "سمع من"]),
    ("الصبر عند المحنة", ["ثبت", "المحن", "التعذيب", "عذب", "صبر", "السجن"], ["صبر", "المحنة", "السجن", "ضرب"]),
    ("الزهد وترك المال", ["رفض المال", "رد العطاء", "رفض العطاء", "زهد", "لم يقبل هدية"], ["رد", "هدية", "المال", "زهد"]),
    ("العلم والشهرة", ["الشهرة", "الرئاسة", "الرياسة", "حب الظهور"], ["الشهرة", "الرياسة", "الرياء", "الجاه"]),
    ("الكرم والإحسان", ["الكرم", "سخي", "كرم", "ساعد فقير", "الفقراء", "إنفاق"], ["تصدق", "الصدقة", "درهم", "الفقراء"]),
    ("العلاقة بالسلطة", ["السلطان", "الحاكم", "السلطة", "الخليفة", "رفض منصب"], ["السلطان", "الخليفة", "القضاء", "الأمير"]),
    ("التثبت من الخبر", ["تحقق من", "التثبت", "صحة الخبر", "كذب", "وثوق"], ["ثقة", "كذب", "الخبر", "تحقق"]),
    ("الحفظ والنسيان", ["الحفظ", "الذاكرة", "نسي", "حافظ"], ["الحفظ", "حفظ", "نسي", "اختلط"]),
    ("التعليم وآداب الطالب", ["معلم", "الطلاب", "التلاميذ", "تعليم", "درس"], ["التعليم", "التدريس", "تلميذ", "أصحاب"]),
]
STOP_WORDS = set(normalize_arabic("ابحث أريد اريد مواقف موقف قصص قصة أمثلة مثال عن في من هو هم كان كانت الذين التي الذي هل كيف لماذا ماذا على إلى مع أن إن ما هذا هذه ذلك بها له لهم حول كل بين ثم عند غير دون ليس فقط ولا أو و قد لقد كتاب سير أعلام النبلاء").split())


def query_terms(q):
    tokens = normalize_arabic(q).split()
    return list(dict.fromkeys(token for token in tokens if token not in STOP_WORDS and len(token) >= 2))[:16]


def explicit_exclusions(q):
    # Do not treat ordinary Arabic negation (e.g. لا إله إلا الله) as an
    # exclusion. Only explicit instructions are parsed, always editable in UI.
    terms = []
    for match in re.finditer(r"(?:باستثناء|لا أريد|لا اريد|ليس عن|مع استبعاد|استبعد)\s+([^،؛.!؟\n]+)", q):
        phrase = re.split(r"\s+(?:ولكن|لكن|بل|وإنما)\s+", match.group(1))[0].strip()
        if phrase:
            terms.append(phrase[:120])
    return list(dict.fromkeys(terms))[:6]


def interpret_query(q, db_path):
    q = str(q).strip()
    if not q:
        raise ValueError("اكتب طلبًا للبحث.")
    normalized = normalize_arabic(q)
    exclusions = explicit_exclusions(q)
    separated_refusal = bool(re.search(r"(?:رفض|امتنع|رد|ابى|لم يقبل).{0,65}(?:مال|عطا|هدي|منصب|القضا|ولاي)", normalized))
    has_alternative = bool(re.search(r"(?<!\w)(?:او|اما)(?!\w)", normalized))
    has_negation = bool(re.search(r"(?<!\w)(?:لا|ليس|لم|لن|دون)(?!\w)", normalized))
    dictionary = []
    with canonical_connection(db_path) as db:
        for row in db.execute("SELECT id,name,search_terms_csv FROM topics WHERE parent_id IS NOT NULL ORDER BY id"):
            terms = [row["name"], *(row["search_terms_csv"] or "").split(",")]
            from .pilot import has_pilot
            if has_pilot(db):
                terms.extend(alias[0] for alias in db.execute('SELECT alias FROM pilot_topic_aliases WHERE topic_id=?',(row['id'],)))
            matches = [term for term in terms if normalize_arabic(term) and re.search(r"(?<!\w)" + re.escape(normalize_arabic(term)) + r"(?!\w)", normalized)]
            if matches:
                dictionary.append({"id": row["id"], "name": row["name"], "terms": matches[:3]})
        if separated_refusal:
            for row in db.execute("SELECT id,name,search_terms_csv FROM topics WHERE name IN (?,?,?)", ("الزهد", "الورع", "القضاء والولايات")):
                if not any(topic["id"] == row["id"] for topic in dictionary):
                    dictionary.append({"id": row["id"], "name": row["name"], "terms": [row["name"]]})
    interpretations = []
    if separated_refusal:
        interpretations.append({"label": "الورع ورفض المال أو الولاية",
            "meaning": "البحث عن مواقف قد تتصل برد المال أو الامتناع عن القضاء والولاية حفظًا للدين؛ يلزم فحص الدافع من النص.",
            "expansion_terms": ["رد المال", "هدية", "الورع", "أبى القضاء", "الولاية"],
            "include_terms": [], "exclude_terms": exclusions, "topic_ids": [topic["id"] for topic in dictionary if topic["name"] in ("الزهد", "الورع", "القضاء والولايات")],
            "uncertainty": "الأموال والمناصب بدائل استرجاعية؛ لا يشترط اجتماعهما ولا ينسب الدافع إلى شخص دون شاهد."})
    for label, triggers, expansions in CONCEPTS:
        if any(normalize_arabic(trigger) in normalized for trigger in triggers):
            interpretations.append({"label": label, "meaning": "البحث عن مقاطع قد تتصل بـ" + label + "، مع إبقاء دلالة السياق موضع مراجعة.",
                "expansion_terms": expansions[:4], "include_terms": [], "exclude_terms": exclusions,
                "topic_ids": [topic["id"] for topic in dictionary[:3]],
                "uncertainty": "تفسير لغرض الاسترجاع؛ قد لا يطابق قصدك. عدّل الألفاظ قبل البحث."})
            if len(interpretations) == 2:
                break
    if dictionary and len(interpretations) < 2:
        topic = dictionary[0]
        interpretations.append({"label": "صلة معجمية مقترحة: " + topic["name"],
            "meaning": "توسيع البحث بألفاظ الموضوع المقترح في المعجم، دون إثبات دلالة المقاطع مسبقًا.",
            "expansion_terms": topic["terms"], "include_terms": [], "exclude_terms": exclusions, "topic_ids": [topic["id"]],
            "uncertainty": "المعجم قائمة اقتراحات؛ ظهور لفظه لا يثبت المعنى."})
    # A literal/context-preserving option always remains available. It is never
    # mislabeled as generated semantic understanding.
    interpretations.append({"label": "المعنى كما صغته", "meaning": q, "include_terms": [], "expansion_terms": [],
        "exclude_terms": exclusions, "topic_ids": [],
        "uncertainty": "يحافظ على صياغتك؛ لا يفترض هوية علم أو معنى لم تذكره."})
    cards = []
    for index, item in enumerate(interpretations[:3]):
        if item["meaning"] != q:
            expanded_meaning = q + "\n" + item["meaning"]
            item["meaning"] = expanded_meaning if len(expanded_meaning) <= 2000 else q
        item["id"] = hashlib.sha256((q + str(index) + item["label"]).encode()).hexdigest()[:12]
        item["query_text"] = q
        cards.append(item)
    flags = []
    if has_alternative:
        flags.append("رصدت صيغة بدائل؛ ألفاظ التوسيع بدائل، وليست شروطًا مجتمعة.")
    if has_negation:
        flags.append("رصدت نفيًا؛ لم يحوّل إلى استبعاد حرفي إلا عند طلب استبعاد صريح. راجع المعنى والألفاظ المستبعدة.")
    return {"query": q, "interpretations": cards, "method": "local_rules",
            "uncertainty_flags": flags,
            "dictionary_suggestions": [{"id": topic["id"], "name": topic["name"]} for topic in dictionary[:6]],
            "note": "هذه احتمالات محلية شفافة لفهم الطلب، وليست إجابة علمية ولا اقتباسات أو نتائج من الكتاب. اختر احتمالًا وعدّل ألفاظه."}
