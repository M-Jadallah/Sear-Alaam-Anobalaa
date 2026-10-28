"""Fresh real inference must work without importing any heavyweight framework."""
import json
import os
import subprocess
import sys
import pytest
from conftest import ROOT


def test_real_offline_onnx_search_does_not_import_torch_or_transformers():
    from backend.semantic import DEFAULT_DB, runtime_backend, semantic_status
    if not runtime_backend().startswith("onnxruntime-cpu-int8-") or not semantic_status(DEFAULT_DB).get("ready"):
        pytest.skip("A complete real ONNX index is required")
    code = r'''
import importlib.abc,json,socket,sys,urllib.request
from unittest.mock import patch
class BlockHeavy(importlib.abc.MetaPathFinder):
 def find_spec(self, fullname, path=None, target=None):
  if fullname.split('.')[0] in {'torch','transformers','sentence_transformers'}:
   raise AssertionError('Heavy framework import: '+fullname)
sys.meta_path.insert(0,BlockHeavy())
def forbidden(*args,**kwargs):raise AssertionError('Network access during offline inference')
with patch.object(socket.socket,'connect',forbidden),patch.object(socket.socket,'connect_ex',forbidden),patch.object(socket,'create_connection',forbidden),patch.object(urllib.request,'urlopen',forbidden):
 from backend.smart_search import smart_search
 result=smart_search({'q':'العلم ليس بكثرة الرواية ولكنه نور يقذفه الله في القلب'})
 assert result['mode']=='hybrid' and result['items'], result.get('warning')
 assert not any(name.split('.')[0] in {'torch','transformers','sentence_transformers'} for name in sys.modules)
 print(json.dumps({'mode':result['mode'],'elapsed_ms':result['elapsed_ms'],'results':len(result['items']),'heavy_imports':False,'network':False}))
'''
    process = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env={**os.environ, "SIYAR_ONNX_TOKENIZER": "rust", "SIYAR_CPU_THREADS": "1", "SIYAR_RERANK_BATCH": "1", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}, capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert process.returncode == 0, process.stdout + process.stderr
    result = json.loads(process.stdout.strip().splitlines()[-1])
    assert result["heavy_imports"] is False and result["network"] is False
