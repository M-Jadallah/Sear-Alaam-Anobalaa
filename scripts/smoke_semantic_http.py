"""Verify local HTTP retrieval, literal evidence, pagination, and concurrent reads.

Run after restarting the API to include initial model loading in the first
uncached request. This script does not load models directly, alter the source,
or judge relevance. It refuses non-loopback URLs and HTTP redirects.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import threading
import time
import urllib.parse
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_QUERY = "أبحث عن مواقف رفض فيها العلماء المال أو منصبًا خوفًا على دينهم"
CANONICAL_TABLES = (
    "sources", "page_markers", "biographies", "source_sections", "people",
    "person_aliases", "person_mentions", "segments", "topics", "topic_hits", "review_log",
)


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("HTTP redirects are disabled for this loopback-only smoke check")


def request_json(base_url, path, payload=None, started_event=None, timeout=180):
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(base_url + path, data=data,
                                     headers={"Content-Type": "application/json", "Accept": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirects())
    started = time.perf_counter()
    if started_event is not None:
        started_event.set()
    with opener.open(request, timeout=timeout) as response:
        result = json.loads(response.read().decode("utf-8"))
    finished = time.perf_counter()
    return result, {"elapsed_ms": (finished - started) * 1000,
                    "started": started, "finished": finished}


def canonical_snapshot(database):
    """Hash every canonical row, so existing-row edits cannot evade row counts."""
    result = {}
    for table in CANONICAL_TABLES:
        digest, count = hashlib.sha256(), 0
        # Table names are a fixed internal allowlist, never user input.
        for row in database.execute(f'SELECT * FROM "{table}" ORDER BY id'):
            digest.update((json.dumps(tuple(row), ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))
            count += 1
        result[table] = {"rows": count, "sha256": digest.hexdigest()}
    return result


def verify_item(database, item, source_cache):
    source_id = item["source_id"]
    if source_id not in source_cache:
        source_cache[source_id] = database.execute(
            "SELECT sha256,full_text FROM sources WHERE id=?", (source_id,)).fetchone()
    source = source_cache[source_id]
    if source is None or item["source_sha256"] != source["sha256"]:
        raise AssertionError("Search evidence source identity/SHA differs from the database")
    a, b = item["source_char_start"], item["source_char_end"]
    if not 0 <= a < b <= len(source["full_text"]):
        raise AssertionError("Search evidence lies outside the source")
    if not source["full_text"][a:b] == item["evidence_text"] == item["text_original"]:
        raise AssertionError("Search evidence is not a literal source slice")
    section = database.execute("SELECT source_id,source_char_start,source_char_end FROM source_sections WHERE id=?",
                               (item["section_id"],)).fetchone()
    if (section is None or section["source_id"] != source_id
            or not section["source_char_start"] <= a < b <= section["source_char_end"]):
        raise AssertionError("Search evidence crosses section boundaries")
    biography = database.execute("SELECT id,source_id,source_char_start,source_char_end FROM biographies WHERE section_id=?",
                                 (item["section_id"],)).fetchone()
    if biography is not None:
        if (item.get("biography_id") != biography["id"] or biography["source_id"] != source_id
                or not biography["source_char_start"] <= a < b <= biography["source_char_end"]):
            raise AssertionError("Search evidence biography identity/bounds differ from the database")
    elif item.get("biography_id") is not None:
        raise AssertionError("Search evidence invents a biography for a non-biography section")
    if item["review_status"] != "candidate":
        raise AssertionError("Search-only evidence must remain a candidate")
    return {"source_id": source_id, "section_id": item["section_id"],
            "biography_id": item.get("biography_id"), "source_char_start": a,
            "source_char_end": b, "match_type": item["match_type"]}


def smoke(base_url, db_path):
    parsed = urllib.parse.urlsplit(base_url)
    if (parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1")
            or parsed.username or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
        raise ValueError("Only a plain loopback HTTP base URL is allowed")
    base_url = base_url.rstrip("/")
    database = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)
    database.row_factory = sqlite3.Row
    try:
        before = canonical_snapshot(database)
        status, status_timing = request_json(base_url, "/api/semantic/status")
        if not status.get("ready") or not status.get("paragraph_status", {}).get("ready"):
            raise AssertionError("Both the context and paragraph semantic indexes must be ready")
        interpretation, interpret_timing = request_json(base_url, "/api/search/interpret", {"q": DEFAULT_QUERY})
        if any(key in interpretation for key in ("items", "results", "total", "evidence_text")):
            raise AssertionError("Interpretation must not claim source evidence before search")
        cards = interpretation["interpretations"]
        if not 1 <= len(cards) <= 3:
            raise AssertionError("Expected one to three editable meaning cards")
        card = cards[0]
        plan = {key: card[key] for key in ("id", "meaning", "include_terms", "exclude_terms", "expansion_terms", "topic_ids") if key in card}
        payload = {"q": DEFAULT_QUERY, "plan": plan, "page": 1, "page_size": 10}
        started = threading.Event()
        with ThreadPoolExecutor(max_workers=3) as pool:
            search_future = pool.submit(request_json, base_url, "/api/search/smart", payload, started)
            if not started.wait(5):
                raise AssertionError("Search HTTP thread did not start")
            time.sleep(0.2)
            was_in_flight = not search_future.done()
            health_future = pool.submit(request_json, base_url, "/api/health")
            reader_future = pool.submit(request_json, base_url, "/api/biographies/1")
            health, health_timing = health_future.result()
            reader, reader_timing = reader_future.result()
            first, first_timing = search_future.result()
        if health.get("status") != "ok":
            raise AssertionError("Concurrent health request failed")
        original_biography = database.execute("SELECT full_text FROM biographies WHERE id=?", (1,)).fetchone()
        if original_biography is None or reader["full_text"] != original_biography["full_text"]:
            raise AssertionError("Concurrent reader must return its complete original text")
        concurrent_completed = {"health": health_timing["finished"] < first_timing["finished"],
                                "reader": reader_timing["finished"] < first_timing["finished"]}
        if not was_in_flight or not all(concurrent_completed.values()):
            raise AssertionError("Could not demonstrate concurrent reads during search; restart the API and rerun")
        if first.get("mode") != "hybrid" or first.get("cache_hit"):
            raise AssertionError("First search must use an uncached hybrid request; restart the API before running")
        total = first["total"]
        if not 0 < total <= 40:
            raise AssertionError("Expected one to forty displayed candidates")
        pages = [(first, first_timing)]
        # Fetch page two even for a smaller pool, to measure its cached response.
        for number in range(2, max(2, math.ceil(total / 10)) + 1):
            page, timing = request_json(base_url, "/api/search/smart", {**payload, "page": number})
            if page.get("mode") != "hybrid" or page["total"] != total or not page.get("cache_hit"):
                raise AssertionError("All later pages must use the same cached hybrid pool")
            pages.append((page, timing))
        source_cache, verified, seen = {}, [], set()
        for page, _ in pages:
            if len(page["items"]) > 10:
                raise AssertionError("A result page exceeds ten candidates")
            for item in page["items"]:
                checked = verify_item(database, item, source_cache)
                key = (checked["source_id"], checked["source_char_start"], checked["source_char_end"])
                if key in seen:
                    raise AssertionError("A literal candidate was repeated across result pages")
                seen.add(key)
                verified.append(checked)
        if len(verified) != total:
            raise AssertionError("Pagination did not expose the complete candidate pool")
        after = canonical_snapshot(database)
        if before != after:
            raise AssertionError("Canonical source/review data changed during the read-only smoke check")
        return {"check": "local-http-semantic-integration-v1", "base_url": base_url,
                "network_scope": "loopback HTTP only; proxies and redirects disabled; no direct model calls",
                "latency_scope": "HTTP wall time including transport; first uncached search includes initial loading only if API was restarted",
                "query": DEFAULT_QUERY, "chosen_plan": plan, "semantic_status": status,
                "latencies_ms": {"status": status_timing["elapsed_ms"], "interpret": interpret_timing["elapsed_ms"],
                                 "first_uncached_search": first_timing["elapsed_ms"], "concurrent_health": health_timing["elapsed_ms"],
                                 "concurrent_reader": reader_timing["elapsed_ms"],
                                 "cached_pages": [{"page": page["page"], "elapsed_ms": timing["elapsed_ms"]} for page, timing in pages[1:]]},
                "reads_completed_while_search_in_flight": concurrent_completed,
                "candidate_count": total, "literal_candidates_verified": verified,
                "canonical_tables_before": before, "canonical_tables_after": after, "canonical_data_unchanged": True,
                "review_log_before": before["review_log"]["rows"], "review_log_after": after["review_log"]["rows"],
                "quality_judgment": "Not measured: this check verifies literal provenance and runtime behavior, not relevance or scientific accuracy."}
    finally:
        database.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "library.sqlite3")
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "semantic" / "http-smoke.json")
    args = parser.parse_args()
    report = smoke(args.base_url, args.db)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "candidate_count": report["candidate_count"],
                      "latencies_ms": report["latencies_ms"], "canonical_data_unchanged": True}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
