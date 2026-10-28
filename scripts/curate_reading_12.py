"""Authored reading paragraphs and source criticism for pilot biographies 1–2.

The ordinal groups below were selected after reading every source segment in
context. They are presentation annotations, not replacements for canonical
segments, and never update the database or the earlier topic-review payloads.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from backend.reading import build_reading_view

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_SHA256 = "7ae7f691ffe236f5b888f5dfdf18becd9e5ea17ab6b5851528db5efd333886c0"

# Inclusive source-paragraph ordinals. Distinct reports retain distinct groups;
# an isnad, its matn/dialogue, and the source's qualifying comment stay together.
GROUPS = {
    1: [
        (1, 1), (2, 6), (7, 8), (9, 17), (18, 19), (20, 21),
        (22, 22), (23, 24), (25, 27), (28, 28), (29, 34), (35, 36),
        (37, 45), (46, 56), (57, 60), (61, 62), (63, 66), (67, 72),
        (73, 78), (79, 81), (82, 82), (83, 85), (86, 87), (88, 91),
        (92, 93), (94, 94), (95, 96), (97, 103), (104, 107),
        (108, 116), (117, 124), (125, 129), (130, 131), (132, 133),
        (134, 135), (136, 136), (137, 144), (145, 145), (146, 153),
        (154, 157), (158, 161), (162, 164), (165, 167), (168, 172),
        (173, 175), (176, 178), (179, 180), (181, 182), (183, 185),
    ],
    2: [
        (1, 1), (2, 5), (6, 6), (7, 9), (10, 10), (11, 11),
        (12, 12), (13, 16), (17, 18), (19, 21), (22, 42), (43, 46),
        (47, 53), (54, 55), (56, 58), (59, 60), (61, 64), (65, 66),
        (67, 69), (70, 71), (72, 82), (83, 88), (89, 92), (93, 94),
        (95, 102), (103, 104), (105, 107), (108, 108), (109, 110),
        (111, 114), (115, 115), (116, 121), (122, 127), (128, 131),
        (132, 134), (135, 136), (137, 141), (142, 143), (144, 146),
        (147, 150), (151, 151), (152, 155), (156, 163), (164, 169),
        (170, 171), (172, 172), (173, 175), (176, 176), (177, 179),
        (180, 185), (186, 187), (188, 188), (189, 191),
    ],
}

# Context paragraph ordinals, kind, exact opening/closing literal fragments,
# and a reason describing the existing text rather than judging the report.
CRITICISM = {
    1: [
        (10, 17, "hadith", "وهذا حديث حسن غريب", "-رضي الله عنه-.",
         "تقويم الحديث بلفظه في سياق النقل عن الترمذي؛ لا ينشئ العرض حكمًا جديدًا ولا يساوي الغرابة بالضعف."),
        (25, 27, "report", "قلت: إن كان هاجر إليها", "اللبث.",
         "تعليق النص المشروط على مدة الهجرة إلى الحبشة؛ يبقى شرط «إن كان» ظاهرًا ولا تتحول الرواية إلى جزم."),
        (35, 36, "hadith", "وثبت من وجوه عن أنس:", "وثبت من وجوه عن أنس:",
         "إثبات تعدد وجوه الخبر كما يذكر النص، مرتبط بحديث أمانة أبي عبيدة اللاحق."),
        (46, 56, "isnad", "كذا يرويه حماد.", "ثم أبو عبيدة بن الجراح (2) .",
         "يعرض النص رواية حماد ومخالفة الجماعة له في طريق الخبر ونص الجواب؛ تُبرز المخالفة مع البديل دون ترجيح مستحدث."),
        (57, 60, "hadith", "اتفقا عليه من حديث شعبة.", "اتفقا عليه من حديث شعبة.",
         "عبارة الاتفاق الواردة بعد حديث حذيفة، مرتبطة بهذا الخبر بعينه لا بجميع ما يليه."),
        (61, 62, "hadith", "واتفقا من حديث خالد الحذاء", "عن أنس:",
         "نقل الاتفاق على طريق خالد الحذاء إلى أنس كما في المصدر؛ يبقى الطريق والمتن في السياق."),
        (73, 78, "hadith", "حديث غريب.", "حديث غريب.",
         "وصف النص خبر الخدم والدواب والوصية بالغرابة؛ لا يستنتج العارض منه ضعف الحديث."),
        (79, 81, "isnad", "هذا مرسل (2) .", "هذا مرسل (2) .",
         "نقد النص لإرسال خبر الحسن السابق، مع حفظ الإشارة الرقمية في الملحق وعدم عزله عن الخبر."),
        (88, 91, "isnad", "خالفه غيره، ففي (الجعديات)", "فذكره (1) .",
         "بيان اختلاف طريق أثر ابن مسعود بين أبي عبيدة وأبي الأحوص كما ورد، دون حسم هوية أو تصحيح من الوكيل."),
        (92, 93, "report", "قلت: يعني: أموال المسلمين", "فأول من اتخذه عمر.",
         "تقييد تاريخي لمعنى تولية بيت المال في الخبر السابق؛ لا يُختزل النقل بحيث يوهم وجود البناء المؤسسي قبله."),
        (183, 185, "report", "وانفرد ابن عائذ، عن أبي مسهر", "توفي سنة سبع عشرة.",
         "ينقل النص انفرادًا في تاريخ الوفاة مقابل سنة ثمان عشرة التي سبقت؛ عرض للاختلاف لا اختيار لتاريخ نهائي."),
    ],
    2: [
        (17, 18, "isnad", "بإسناد حسن:", "بإسناد حسن:",
         "وصف الإسناد بالحسن في النص نفسه، متصل بخبر «أوجب طلحة» دون تعميم على الأخبار المجاورة."),
        (22, 42, "narrator", "رواته ثقات.", "رواته ثقات.",
         "تقويم رواة خبر الدفاع يوم أحد كما ورد؛ لا تُحوّل العبارة إلى ادعاء صحة جميع تفاصيل الرواية من الموقع."),
        (61, 64, "isnad", "وشذ أبو يعلى الموصلي", "عن أبيه، عن عقبة.",
         "يذكر النص شذوذ صورة الإسناد عند أبي يعلى بعد ذكر رواية الآخرين؛ تُحفظ أسماء الطريق والمخالفة كما هي."),
        (67, 69, "isnad", "إسناده لين.", "إسناده لين.",
         "الحكم المنقول في المصدر على إسناد خبر ألقاب طلحة، دون تعميمه على كل ما ورد في بابه."),
        (89, 92, "isnad", "إسناده منقطع، مع ضعف الكديمي.", "إسناده منقطع، مع ضعف الكديمي.",
         "إبقاء النقد المركب كاملًا: انقطاع الإسناد وضعف الكديمي في الخبر السابق، دون إسقاط أحد القيدين."),
        (147, 150, "isnad", "مرسل.", "مرسل.",
         "نقد إرسال الخبر الذي يصف ترحم علي على طلحة؛ يظل الإرسال مربوطًا بهذه الرواية ولا يُنسب إلى خبر مجاور."),
        (152, 155, "narrator", "قال: أما أن قد سمع من رسول الله", "لم يقل (3) ؟",
         "حجة طلحة المنقولة في سعة سماع أبي هريرة والرد على التشكيك فيه، بجميع تعليلها وسؤالها الختامي؛ ليست تصحيحًا آليًا لأحاديثه."),
        (177, 179, "report", "أعجب ما مر بي قول ابن الجوزي", "ثلاث مائة حمل من الذهب.",
         "استغراب صاحب النص مقدار التركة المنقول عن ابن الجوزي، في سياق الأرقام السابقة؛ لا يُستبدل الاستغراب بحكم جازم بالكذب."),
        (180, 185, "report", "وحكى المسعودي: أن عائشة بنته", "هي التي رأت المنام.",
         "اختلاف منقول في تعيين من رأى المنام؛ يُعرض بوصفه اختلافًا في الرواية لا إثباتًا لصحة المنام أو الجزم بأحد النقلين."),
    ],
}


def build(biography_id: int, source: str, source_sha256: str) -> dict:
    previous = json.loads((ROOT / "reference" / "pilot" / f"biography-{biography_id}.json").read_text(encoding="utf-8"))
    assert previous["source_sha256"] == source_sha256
    start, end = previous["source_char_start"], previous["source_char_end"]
    view = build_reading_view(source[start:end], start, starts_at_line_start=True)
    blocks = view["blocks"]
    assert blocks[0]["type"] == "heading"
    paragraphs = blocks[1:]
    assert len(paragraphs) == (185 if biography_id == 1 else 191)
    assert all(block["type"] == "paragraph" for block in paragraphs)
    assert [n for first, last in GROUPS[biography_id] for n in range(first, last + 1)] == list(range(1, len(paragraphs) + 1))
    cuts = {start, end, paragraphs[0]["source_char_start"]}
    for first, last in GROUPS[biography_id]:
        cuts.add(paragraphs[first - 1]["source_char_start"])
        cuts.add(paragraphs[last - 1]["source_char_end"])
    annotations = previous["presentation"]
    cuts.update(heading["at"] for heading in annotations["headings"])
    for poem in annotations["poetry"]:
        cuts.update((poem["start"], poem["end"]))
        for block in blocks:
            if block["source_char_end"] > poem["start"] and block["source_char_start"] < poem["end"]:
                cuts.update((max(poem["start"], block["source_char_start"]), min(poem["end"], block["source_char_end"])))
    for listing in annotations["lists"]:
        cuts.update((listing["start"], listing["end"]))
        cuts.update(item["start"] for item in listing["items"])
    ordered = sorted(cuts)
    assert ordered[0] == start and ordered[-1] == end
    groups = [{"start": left, "end": right} for left, right in zip(ordered, ordered[1:])]
    assert all(group["start"] < group["end"] for group in groups)
    critical = []
    for first, last, kind, opening, closing, reason in CRITICISM[biography_id]:
        context_start = paragraphs[first - 1]["source_char_start"]
        context_end = paragraphs[last - 1]["source_char_end"]
        context = source[context_start:context_end]
        assert context.count(opening) == 1, (biography_id, opening)
        selected_start = context_start + context.index(opening)
        selected_end = source.index(closing, selected_start, context_end) + len(closing)
        assert context_start <= selected_start < selected_end <= context_end
        critical.append({"start": selected_start, "end": selected_end, "context_start": context_start,
                         "context_end": context_end, "kind": kind, "reason": reason})
    critical.sort(key=lambda item: (item["start"], item["end"]))
    assert all(left["end"] <= right["start"] for left, right in zip(critical, critical[1:]))
    return {"version": "pilot-reading-v2", "source_sha256": source_sha256, "biography_id": biography_id,
            "source_char_start": start, "source_char_end": end, "reviewer": "مراجعة نصية بواسطة وكيل",
            "paragraph_groups": groups, "criticism": critical}


def main() -> None:
    raw = (ROOT / "data" / "source.mARkdown").read_bytes()
    source_sha256 = hashlib.sha256(raw).hexdigest()
    if source_sha256 != EXPECTED_SHA256:
        raise SystemExit("Source fingerprint changed; authored reading annotations require a new source audit.")
    source = raw.decode("utf-8")
    target = ROOT / "reference" / "pilot-reading"
    target.mkdir(parents=True, exist_ok=True)
    documents = [build(bid, source, source_sha256) for bid in (1, 2)]
    for document in documents:
        path = target / f"biography-{document['biography_id']}.json"
        path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"{path.name}: {len(document['paragraph_groups'])} paragraph groups, {len(document['criticism'])} criticism annotations")


if __name__ == "__main__":
    main()
