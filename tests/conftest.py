"""Isolated test data: every invented Arabic sentence is explicitly synthetic."""
from pathlib import Path
import sqlite3
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DICTIONARY = ROOT / "reference" / "topic_dictionary.csv"

SYNTHETIC = (
    "######OpenITI#\r\n"
    "#META# 020.BookTITLE :: عينة مصطنعة للاختبار وليست من الكتاب\r\n"
    "#META#Header#End#\r\n"
    "### | قسم مصطنع\r\n"
    "# هذا مدخل مصطنع، وليس ترجمة لشخصية تاريخية.\r\n"
    "### $ 1 - شخصية تجريبية أولى\r\n"
    "# 🧪 الإِخْلَاصُ لفظ وارد في هذه العينة المصطنعة للاختبار فقط.\r\n"
    "~~ وهذا استمرار حرفي في السطر الثاني. PageV01P001\r\n"
    "# أشعلت النار الحقيقية للطهو؛ لا يثبت ذلك تصنيفًا عقديًا.\r\n"
    "# ذكرت شخصية تجريبية ثانية في هذا الموضع المصطنع.\r\n"
    "PageV01P001\r\n"
    "### $$ 2 - شخصية تجريبية ثانية\r\n"
    "# فقرة مصطنعة ثانية فيها الإخلاص، الصدق معًا.\r\n"
    "PageV01P002\r\n"
    "### $ 3 - شخصية تجريبية ثانية\r\n"
    "# هذه هوية مصطنعة أخرى لها الاسم نفسه؛ لا تدمج بالسابقة.\r\n"
    "PageV01P003\r\n"
    "### |PARATEXT|\r\n"
    "# خاتمة مصطنعة يجب حفظها خارج الترجمة السابقة.\r\n"
)


@pytest.fixture
def book(tmp_path):
    from backend.importer import import_book

    source = tmp_path / "synthetic.mARkdown"
    source.write_bytes(SYNTHETIC.encode("utf-8"))
    database = tmp_path / "test.sqlite3"
    report = import_book(source_path=source, db_path=database, dictionary_path=DICTIONARY)
    return {"source": source, "database": database, "text": SYNTHETIC, "report": report}


@pytest.fixture
def db(book):
    connection = sqlite3.connect(book["database"])
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    yield connection
    connection.close()


@pytest.fixture
def client(book):
    from fastapi.testclient import TestClient
    from backend.app import create_app

    with TestClient(create_app(db_path=book["database"], source_path=book["source"])) as active:
        yield active
