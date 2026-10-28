"""Reproduce hand-read paragraph and criticism overlays for biographies 3–5.

The ordinal boundaries and critical passages below were selected after reading
all 899 source segments in order.  They are editorial display choices, not a
runtime classifier or a scholarly authentication.  This script reads the
library in SQLite read-only mode and writes only its three overlay JSON files.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.reading import build_reading_view

EXPECTED = "7ae7f691ffe236f5b888f5dfdf18becd9e5ea17ab6b5851528db5efd333886c0"

# Each number starts a new prose unit.  The next number ends that unit.
# Existing editorial headings, explicit list items, and verse lines add hard
# boundaries below. Quote decorations deliberately do not add boundaries.
STARTS = {
    3: [
        1, 2, 3, 7, 8, 11, 18, 25, 27, 28, 29, 33, 34, 38, 42,
        43, 45, 47, 49, 51, 55, 56, 60, 68, 70, 72, 74, 79, 80,
        82, 85, 87, 89, 93, 95, 99, 100, 102, 104, 106, 107, 108,
        115, 117, 118, 120, 122, 132, 135, 137, 139, 142, 146,
        158, 160, 161, 163, 169, 172, 175, 177, 181, 185, 187,
        188, 195, 198, 201, 205, 210, 218, 224, 227, 229, 231,
        232, 236, 240, 243, 244, 255, 259, 264, 272, 280, 285,
        286, 288,
    ],
    4: [
        1, 2, 4, 5, 6, 9, 11, 12, 13, 22, 23, 24, 34, 45, 47,
        49, 50, 53, 54, 56, 58, 60, 63, 65, 67, 69, 80,
        83, 94, 99, 101, 104, 107, 108, 109, 111, 112, 114,
        116, 119, 122, 125, 129, 134, 140, 142, 145, 146,
        152, 158, 159, 162, 165, 166, 167, 172, 175, 181,
        183, 184, 185, 188, 189, 192, 196, 202, 204, 210,
        212, 220, 221, 222, 224, 225, 226, 229, 232, 234,
        237, 238, 242, 244, 245, 246, 247,
    ],
    5: [
        1, 2, 3, 4, 5, 6, 11, 12, 30, 34, 38, 42, 43, 45,
        46, 48, 49, 51, 53, 56, 59, 61, 66, 68, 73, 76, 79,
        81, 82, 84, 88, 89, 91, 96, 98, 99, 107, 111, 116,
        124, 128, 129, 131, 133, 138, 141, 154, 156, 159,
        161, 164, 167, 170, 172, 181, 185, 189, 194, 196,
        198, 200, 208, 219, 224, 228, 239, 241, 245, 248,
        249, 253, 254, 255, 256, 257, 258, 265, 269, 273,
        276, 277, 279, 280, 281, 284, 287, 291, 298, 305,
        313, 326, 328, 332, 333, 335, 341, 345, 347, 348,
        351, 353, 354, 356, 358,
    ],
}


def generate():
    raw = (ROOT / "data/source.mARkdown").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == EXPECTED
    source = raw.decode("utf-8")
    output = ROOT / "reference/pilot-reading"
    output.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect((ROOT / "data/library.sqlite3").as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        for bid in (3, 4, 5):
            bio = db.execute("SELECT * FROM biographies WHERE id=?", (bid,)).fetchone()
            rows = list(db.execute("SELECT * FROM segments WHERE biography_id=? ORDER BY segment_ordinal", (bid,)))
            by_n = {r["segment_ordinal"]: r for r in rows}
            assert len(rows) == {3: 293, 4: 248, 5: 358}[bid]
            assert source[bio["source_char_start"]:bio["source_char_end"]] == bio["full_text"]
            base = build_reading_view(bio["full_text"], bio["source_char_start"], starts_at_line_start=True)
            old = json.loads((ROOT / f"reference/pilot/biography-{bid}.json").read_text(encoding="utf-8"))["presentation"]
            cuts = {bio["source_char_start"], bio["source_char_end"], base["blocks"][0]["source_char_end"]}
            cuts.update(by_n[n]["source_char_start"] for n in STARTS[bid])
            heading_overrides = []
            if bid == 4:
                # The earlier editorial heading stood after the beginning of
                # this isnad. Move only its display position to that beginning.
                lo, hi = by_n[69]["source_char_start"], by_n[69]["source_char_end"]
                heading_overrides = [{"from_at": by_n[70]["source_char_start"],
                                      "at": source.index("أخبرنا أبو الحسين", lo, hi)}]
            positions = {r["from_at"]: r["at"] for r in heading_overrides}
            cuts.update(positions.get(h["at"], h["at"]) for h in old["headings"])
            for poem in old["poetry"]:
                cuts.update((poem["start"], poem["end"]))
                for block in base["blocks"]:
                    if poem["start"] < block["source_char_start"] < poem["end"]:
                        cuts.add(block["source_char_start"])
                    if poem["start"] < block["source_char_end"] < poem["end"]:
                        cuts.add(block["source_char_end"])
            for group in old["lists"]:
                cuts.update((group["start"], group["end"]))
                cuts.update(item["start"] for item in group["items"])
            points = sorted(cuts)
            data = {
                "version": "pilot-reading-v2", "source_sha256": EXPECTED,
                "biography_id": bid, "source_char_start": bio["source_char_start"],
                "source_char_end": bio["source_char_end"],
                "reviewer": "مراجعة نصية بواسطة وكيل",
                "paragraph_groups": [{"start": a, "end": b} for a, b in zip(points, points[1:])],
                "criticism": [],
            }
            if heading_overrides:
                data["heading_overrides"] = heading_overrides

            def bounds(lo, hi):
                return by_n[lo]["source_char_start"], by_n[hi]["source_char_end"]

            def critical(lo, hi, kind, reason, context_lo=None, context_hi=None,
                         first=None, last=None):
                a, b = bounds(lo, hi)
                # Exclude the initial structural prefix and trailing newline;
                # the underlying literal content and ranges remain untouched.
                a += len(by_n[lo]["text_original"]) - len(by_n[lo]["text_original"].lstrip("# "))
                b -= len(source[a:b]) - len(source[a:b].rstrip("\r\n"))
                if first is not None:
                    a = source.index(first, a, b)
                if last is not None:
                    b = source.index(last, a, b) + len(last)
                ca, cb = bounds(context_lo or lo, context_hi or hi)
                if bid == 4 and context_lo == 70:
                    ca = heading_overrides[0]["at"]
                assert ca <= a < b <= cb
                data["criticism"].append({"start": a, "end": b,
                    "context_start": ca, "context_end": cb, "kind": kind, "reason": reason})

            if bid == 3:
                critical(17,17,"hadith","يبين النص اختلاف لفظ أبي يعلى في الحديث؛ لا يضاف لفظ إلى روايته.",11,24,first="لم يقل",last="متعمدا.")
                critical(84,84,"narrator","توثيق منقول لراويي الطريق، لا وصف لصاحب الترجمة.",82,84,last="رواه: ثقتان عنه،")
                critical(122,122,"narrator","تحفظ الراوي المنقول في اتهام مروان محفوظ بنفيه، دون تبنيه أو نسبته إلى الزبير.",122,131,first="-ولا إخاله",last="علينا-")
                critical(209,209,"hadith","غرابة وتفرد بالخبر كما نص المصدر، دون تحويل الغريب إلى حكم ضعف.",205,209)
                critical(217,217,"report","تعيين الوقعة في تعليق المصدر يحتفظ بتحفظ إن شاء الله وسبب التأريخ.",210,217)
                critical(223,223,"isnad","يبقى نقد الإرسال والنكارة مع كامل النهي عن الجزم بمصير الأتباع والتحفظ اللاحق؛ ليس حكمًا ينشئه التطبيق.",218,223)
                critical(230,230,"report","ترجيح العمر المنقول يظل مع القول الذي يرجحه والسياق المخالف.",227,230)
                critical(242,242,"report","تنبيه المصدر كذا هذه الرواية يرافق أرقام التركة والرواية الأخرى دون تصحيح الأرقام آليًا.",240,243)
            elif bid == 4:
                critical(21,21,"hadith","وصف الحديث بالغرابة وتخريجه نص المصدر، ولا يحول إلى تضعيف مستنتج.",13,23)
                critical(31,31,"hadith","درجة حسن غريب منقولة حرفيًا، مع نقد النسائي التالي في السياق.",24,33)
                critical(33,33,"isnad","تصويب النسائي لطريق الحديث منسوب إليه كما في الأصل.",24,33)
                critical(43,43,"hadith","حكم الحسن كما ورد في المصدر، لا اعتماد علمي جديد.",34,46)
                critical(44,44,"hadith","التصحيح منسوب صراحة إلى الترمذي؛ لا ينسب إلى التطبيق.",34,46,last="صححه الترمذي")
                critical(47,48,"narrator","تقويم التثبت والعدالة يحفظ استدلال النص كله بقبول عمر وطلبه الشاهد واستحلاف علي وتصديقه أبا بكر، مع والله أعلم، دون تقرير أحكام جديدة.",34,48)
                critical(52,52,"report","يذكر المصدر اختلاف نقل نسبه وإسقاط اسم عبد في بعض طرقه؛ لا يصحح التطبيق النسب ولا يختار إحدى الروايات.",50,53)
                critical(72,72,"report","علامة التحفظ كذا هذا في خبر المؤاخاة تحفظ كما هي؛ لا يصحح التطبيق الاسم في المتن.",67,82,first="كذا هذا.")
                critical(78,79,"hadith","حكم حديث منكر يتضمن عزوه لأحمد في العبارة السابقة؛ لا تظهر البشارة منزوعة النقد.",70,82)
                critical(93,93,"isnad","تقويم إسناد الخبر الثاني بالوهاء موجود حرفيًا، مع سياق الروايتين.",83,100)
                critical(94,98,"narrator","يجتمع اللين مع أقوال أبي حاتم وابن معين وابن عدي وتحفظ النسائي، فلا ينتقى رأي واحد ويحذف خلافه.",70,100)
                critical(99,100,"report","التوجيه الاحتمالي للاحتساب والاستعارة يظل بصيغة الشرط ولا يتحول إلى إثبات صحة الرواية.",83,100)
                critical(115,115,"narrator","التدليس وعدم السماع للحسن في الطريق المذكور، لا لعبد الرحمن بن عوف.",111,115)
                critical(118,118,"narrator","ضعف عثمان في الإسناد مع الاستدراك بذكر طريق آخر محفوظ كاملًا.",116,118)
                critical(132,133,"report","يشمل الإسناد الحسن وقيد المنام وتأويله والتعليق المتصل؛ يمنع إيهام أن الحديث خبر يقظة محقق.",129,133)
                critical(151,151,"isnad","بيان تفرد الطريق ووجود رواية عن الحسن مرسلة منقول دون إسقاط أحد القيدين.",146,151)
                critical(161,161,"hadith","التصحيح منسوب إلى الترمذي صراحة مع سياق تعدد الطرق.",152,161,last="وصححه الترمذي،")
                critical(187,187,"narrator","التضعيف يخص أبا المعلى في سند الخبر، لا صاحب الترجمة.",185,187,first="وأبو المعلى")
            else:
                critical(37,37,"isnad","الانقطاع وصف للخبر السابق المحدد، ولا ينسحب على الأخبار الأخرى في رخص السفر.",34,41)
                critical(64,64,"hadith","اتفاق الشيخين وصف مذكور لهذا الحديث، بخلاف تعريف أبي عون في أول الترجمة.",61,65)
                critical(75,75,"isnad","الانقطاع متصل بخبر السهم المتكرر نفسه، ولا يحذف عند قراءة الموقف.",73,75)
                critical(86,87,"isnad","يحفظ تفرد ابن عيينة مع الطريق الآخر الموصوف بأنه أصح، فلا ينتقى الترجيح منفردًا.",84,87)
                critical(167,167,"narrator","توثيق منقول عن ابن شهاب لراو مبهم؛ لا يعيّن التطبيق هويته ولا يستنتج منه تصحيح الحديث.",167,169,first="حدثني من لا أتهم",last="حدثني من لا أتهم")
                critical(218,218,"hadith","متفق عليه تقويم منقول في سياق الخبر المحدد عن الشكوى والتحقق، لا اعتماد للاتهامات الواردة في الحوار.",208,218)
                critical(272,272,"narrator","التضعيف بالترك يخص مينا راوِي الخبر الخارق، ولا يحول الخبر إلى كرامة محققة.",269,272)
                critical(325,325,"hadith","اتفاق منقول من طرق الزهري لحديث الوصية، ولا يوسع إلى جميع ما في الترجمة.",313,325)
                critical(357,357,"report","ترجيح تاريخ الوفاة يظل مرتبطًا بتواريخ الأقوال السابقة؛ لا تغير سنة في النص أو بيانات الأعلام.",353,357)

            data["criticism"].sort(key=lambda x: (x["start"], x["end"]))
            for group in data["paragraph_groups"]:
                assert bio["source_char_start"] <= group["start"] < group["end"] <= bio["source_char_end"]
            assert sum(g["end"]-g["start"] for g in data["paragraph_groups"]) == len(bio["full_text"])
            path = output / f"biography-{bid}.json"
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(json.dumps({"biography":bid,"source_segments_read":len(rows),"paragraph_groups":len(data["paragraph_groups"]),"criticism":len(data["criticism"])}, ensure_ascii=False))


if __name__ == "__main__":
    generate()
