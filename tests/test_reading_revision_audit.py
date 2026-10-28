"""Independent conservation checker on an invented, disposable library."""
from contextlib import closing
from backend import reading_revision as revisions
from backend.db import connect
from scripts.audit_reading_revision import audit
from test_curation import library
from test_reading_revision import published, correction, authored, ready


def test_independent_checker_proves_history_and_rejects_changed_reference_data(published):
    directory = correction(published)
    authored(directory); ready(published, directory)
    result = revisions.publish_batch(directory, db_path=published["db"], source_path=published["source"])
    report = audit(published["db"], result["backup"], [6, 7], published["source"])
    assert report["passed"]
    assert all(row["exact_previous_rows_and_current_payloads"] for row in report["history_checks"])
    with closing(connect(published["db"])) as db, db:
        db.execute("UPDATE topics SET name=? WHERE id=(SELECT MIN(id) FROM topics)", ("تغيير مرجعي مصطنع يجب كشفه",))
    report = audit(published["db"], result["backup"], [6, 7], published["source"])
    assert not report["passed"]
    assert not report["protected_tables"]["topics"]["unchanged"]


def test_independent_checker_detects_missing_previous_reading_snapshot(published):
    directory = correction(published)
    authored(directory); ready(published, directory)
    result = revisions.publish_batch(directory, db_path=published["db"], source_path=published["source"])
    with closing(connect(published["db"])) as db, db:
        db.execute("UPDATE reading_revision_history SET previous_reading_json=? WHERE biography_id=?", ("null", 6))
    report = audit(published["db"], result["backup"], [6, 7], published["source"])
    assert not report["passed"]
    assert not report["history_checks"][0]["exact_previous_rows_and_current_payloads"]
