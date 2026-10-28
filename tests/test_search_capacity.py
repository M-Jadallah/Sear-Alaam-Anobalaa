"""Bound public workloads without delaying ordinary reads or leaking queue slots."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading

import httpx


def test_fifth_search_is_rejected_while_four_requests_are_outstanding(book, monkeypatch):
    from backend.app import create_app
    from backend import smart_search as module
    submitted = []
    release = threading.Event()
    class Executor(ThreadPoolExecutor):
        def submit(self, *args, **kwargs):
            future = super().submit(*args, **kwargs)
            submitted.append(future)
            return future
    executor = Executor(max_workers=1)
    monkeypatch.setattr(module, "_EXECUTOR", executor)
    def worker(*args):
        assert release.wait(10)
        return {"result": {"mode": "synthetic", "items": []}}
    monkeypatch.setattr(module, "_worker", worker)
    app = create_app(book["database"], book["source"])
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://testserver") as client:
            tasks = [asyncio.create_task(client.post("/api/search/smart", json={"q": "عينة مصطنعة"})) for _ in range(4)]
            for _ in range(100):
                if len(submitted) == 4:
                    break
                await asyncio.sleep(.01)
            assert len(submitted) == 4
            rejected = await client.post("/api/search/smart", json={"q": "عينة مصطنعة"})
            assert rejected.status_code == 429 and rejected.headers["retry-after"] == "5"
            assert (await client.get("/api/search", params={"q": "الإخلاص"})).status_code == 200
            release.set()
            assert all(response.status_code == 200 for response in await asyncio.gather(*tasks))
    try:
        asyncio.run(scenario())
    finally:
        release.set()
        module.shutdown_search_worker()


def test_search_timeout_discards_worker_and_keeps_lexical_search_available(book, monkeypatch):
    from backend.app import create_app
    from backend import smart_search as module
    from fastapi.testclient import TestClient
    release = threading.Event()
    executor = ThreadPoolExecutor(max_workers=1)
    monkeypatch.setattr(module, "_EXECUTOR", executor)
    monkeypatch.setenv("SIYAR_SEARCH_TIMEOUT", "0.02")
    monkeypatch.setattr(module, "_worker", lambda *args: (release.wait(5), {"result": {}})[1])
    try:
        with TestClient(create_app(book["database"], book["source"])) as client:
            assert client.post("/api/search/smart", json={"q": "عينة مصطنعة"}).status_code == 504
            assert module._EXECUTOR is None
            assert client.get("/api/search", params={"q": "الإخلاص"}).status_code == 200
    finally:
        release.set()
        executor.shutdown(wait=True)
