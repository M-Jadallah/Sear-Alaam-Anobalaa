"""Real local-model query proof, isolated from any already-loaded model cache."""
import json
import os
import subprocess
import sys

import pytest

from conftest import ROOT


def test_fresh_real_model_query_is_hybrid_with_all_python_network_connections_blocked():
    from backend.semantic import DEFAULT_DB, semantic_status

    if not semantic_status(DEFAULT_DB).get("ready"):
        pytest.skip("A complete pinned real-model index is required for the offline integration proof")
    code = r'''
import json
import socket
import urllib.request
from unittest.mock import patch

attempts = []
def forbidden(*args, **kwargs):
    attempts.append(str(args[:1]))
    raise AssertionError("Network access during offline semantic query is forbidden")

with patch.object(socket.socket, "connect", forbidden), patch.object(socket.socket, "connect_ex", forbidden), patch.object(socket, "create_connection", forbidden), patch.object(urllib.request, "urlopen", forbidden):
    from backend.smart_search import smart_search
    from backend.semantic import DEFAULT_DB
    result = smart_search({"q": "العلم ليس بكثرة الرواية ولكنه نور يقذفه الله في القلب", "plan": {"meaning": "العلم ليس بكثرة الرواية ولكنه نور يقذفه الله في القلب", "include_terms": [], "exclude_terms": [], "expansion_terms": [], "topic_ids": []}, "page_size": 10}, DEFAULT_DB)
    assert result["mode"] == "hybrid", result.get("warning")
    assert result["items"]
    assert all(item["review_status"] == "candidate" for item in result["items"])
    assert all(item["text_original"] == item["evidence_text"] for item in result["items"])
    assert not result["cache_hit"]
    assert not attempts, attempts
    print(json.dumps({"mode": result["mode"], "elapsed_ms": result["elapsed_ms"], "cache_hit": result["cache_hit"], "network_attempts": len(attempts), "source_sha256": sorted({item["source_sha256"] for item in result["items"]}), "results": len(result["items"])}, ensure_ascii=False))
'''
    process = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                             env={**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "PYTHONIOENCODING": "utf-8"},
                             capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert process.returncode == 0, process.stdout + "\n" + process.stderr
    payload = json.loads(process.stdout.strip().splitlines()[-1])
    assert payload["mode"] == "hybrid" and payload["network_attempts"] == 0
    assert payload["cache_hit"] is False and payload["results"] > 0
