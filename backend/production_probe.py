"""Container-only read/search/auth acceptance against disposable CI volumes."""
import json
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import statistics
import time
import urllib.error
import urllib.request

ORIGIN = "https://sear-alaam-anobalaa.jadallah.work"


def request(path, payload=None, headers=None):
    values = {"Host": "sear-alaam-anobalaa.jadallah.work", "Origin": ORIGIN, **(headers or {})}
    if payload is not None:
        values["Content-Type"] = "application/json"
    req = urllib.request.Request("http://127.0.0.1:8000" + path, data=json.dumps(payload).encode() if payload is not None else None, headers=values)
    try:
        response = urllib.request.urlopen(req, timeout=65)
    except urllib.error.HTTPError as response:
        return response.code, dict(response.headers), json.load(response)
    with response:
        return response.status, dict(response.headers), json.load(response)


def main():
    assert request("/api/ready")[0] == 200
    semantic = request("/api/semantic/status")[2]
    assert semantic["ready"] and semantic["paragraph_status"]["ready"], semantic
    assert request("/api/reviews")[0] == 401
    code, headers, session = request("/api/auth/login", {"username": os.environ["SIYAR_ADMIN_USERNAME"], "password": os.environ["SIYAR_ADMIN_PASSWORD"]})
    assert code == 200
    cookie = next(value for key, value in headers.items() if key.lower() == "set-cookie").split(";", 1)[0]
    protected = {"Cookie": cookie, "X-CSRF-Token": session["csrf"]}
    assert request("/api/reviews", headers=protected)[0] == 200
    item = request("/api/reviews", headers=protected)[2]["items"][0]
    path = "/api/reviews/topic-hits/" + str(item["id"])
    assert request(path, {"review_status": "reviewed"}, {"Cookie": cookie})[0] == 403
    # This mutation is only executed against the randomly named CI library volume.
    assert request(path, {"review_status": "reviewed", "reviewer": "spoofed"}, protected)[0] == 200
    assert request("/api/review-log", headers=protected)[2]["items"][0]["reviewer"] == os.environ["SIYAR_ADMIN_USERNAME"]
    durations = []
    queries = [json.loads(line)["query"] for line in (Path("/app/fixtures/semantic_evaluation.jsonl")).read_text(encoding="utf-8").splitlines() if line.strip()]
    # Warmup is outside the timed sample; query response transport is measured.
    warm = request("/api/search/smart", {"q": "العلم والإخلاص"})
    assert warm[0] == 200 and warm[2]["mode"] == "hybrid", warm
    with ThreadPoolExecutor(max_workers=10) as readers:
        requests = list(readers.map(lambda _: request("/api/sources/1/text?start=0&end=1000")[0], range(10)))
        assert all(code == 200 for code in requests), requests
    for query in queries:
        start = time.monotonic()
        code, _, result = request("/api/search/smart", {"q": query})
        durations.append(time.monotonic() - start)
        assert code == 200 and result["mode"] == "hybrid", result
        for row in result["items"]:
            literal = request(f"/api/sources/{row['source_id']}/text?start={row['source_char_start']}&end={row['source_char_end']}")[2]
            assert row["text_original"] == literal["text_original"]
    p95 = sorted(durations)[max(0, int(len(durations) * .95 + .9999) - 1)]
    assert p95 <= 30, f"Warm search p95 exceeds 30s: {p95}"
    assert request("/api/auth/logout", {}, protected)[0] == 200
    assert request("/api/reviews", headers={"Cookie": cookie})[0] == 401
    print(json.dumps({"queries": len(durations), "warm_p95_seconds": p95, "median_seconds": statistics.median(durations), "offline_network": "internal Docker network; no Internet", "auth": "passed", "literal_witnesses": "passed"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
