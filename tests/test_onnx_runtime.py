"""CPU adapter checks use only fake sessions, tokenizers and isolated manifests."""
import json
import sys
from types import SimpleNamespace

import pytest
np=pytest.importorskip("numpy")


class FakeTokenizer:
    def __init__(self,lengths=None,width=None):
        self.lengths=lengths or [2]
        self.width=width
        self.calls=[]
    def __call__(self,texts,**kwargs):
        self.calls.append((texts,kwargs))
        width=self.width or max(self.lengths)
        count=len(texts)
        ids=np.ones((count,width),dtype=np.int32)
        mask=np.zeros((count,width),dtype=np.int32)
        for index in range(count):mask[index,:self.lengths[index%len(self.lengths)]]=1
        return {"input_ids":ids,"attention_mask":mask,"unexpected_tokenizer_metadata":np.ones((count,width))}


class FakeSession:
    def __init__(self,output=None,token_types=False):
        self.output=output
        self.token_types=token_types
        self.calls=[]
    def get_inputs(self):
        return [SimpleNamespace(name=name) for name in (["input_ids","attention_mask","token_type_ids"] if self.token_types else ["input_ids","attention_mask"])]
    def run(self,outputs,inputs):
        self.calls.append(inputs)
        if callable(self.output):return [self.output(inputs)]
        return [self.output]


def test_onnx_base_filters_inputs_and_uses_int64_without_truncation():
    from backend.semantic_onnx import OnnxBase
    tokenizer=FakeTokenizer([2],width=3)
    session=FakeSession(np.zeros((1,3,384)),token_types=True)
    adapter=OnnxBase(session,tokenizer)
    adapter.run(["نص مصطنع"])
    assert tokenizer.calls[0][1]["truncation"] is False
    assert tokenizer.calls[0][1]["padding"] is True
    assert tokenizer.calls[0][1]["return_tensors"]=="np"
    inputs=session.calls[0]
    assert set(inputs)=={"input_ids","attention_mask","token_type_ids"}
    assert all(value.dtype==np.int64 for value in inputs.values())
    assert not inputs["token_type_ids"].any()


def test_onnx_rejects_overlong_inputs_before_session_execution():
    from backend.semantic_onnx import OnnxBase
    session=FakeSession()
    tokenizer=FakeTokenizer([513],width=513)
    with pytest.raises(ValueError,match="لم يبتر"):
        OnnxBase(session,tokenizer).run(["نص طويل"])
    assert not session.calls
    assert tokenizer.calls[0][1]["truncation"] is False


def test_onnx_embedding_attention_mean_pooling_ignores_padding_and_normalizes():
    from backend.semantic_onnx import OnnxEmbedding
    # Masked padding is huge on purpose; it must contribute nothing.
    hidden=np.array([[[2.,0.],[0.,2.],[1000.,1000.]],[[0.,4.],[1000.,1000.],[1000.,1000.]]])
    tokenizer=FakeTokenizer([2,1],width=3)
    result=OnnxEmbedding(FakeSession(hidden),tokenizer).encode(["أ","ب"],batch_size=2)
    assert result.dtype==np.float32
    assert np.allclose(result,np.array([[2**-.5,2**-.5],[0.,1.]]),atol=1e-6)
    assert np.allclose(np.linalg.norm(result,axis=1),1.0)


def test_onnx_embedding_already_pooled_outputs_keep_batch_order():
    from backend.semantic_onnx import OnnxEmbedding
    session=FakeSession(lambda inputs:np.tile(np.array([[3.,4.]],dtype=np.float32),(len(inputs["input_ids"]),1)))
    result=OnnxEmbedding(session,FakeTokenizer()).encode(["1","2","3"],batch_size=2)
    assert result.shape==(3,2)
    assert len(session.calls)==2
    assert np.allclose(result,[[.6,.8]]*3)


def test_onnx_empty_embedding_batch_returns_well_shaped_empty_array():
    from backend.semantic_onnx import OnnxEmbedding
    session=FakeSession()
    result=OnnxEmbedding(session,FakeTokenizer()).encode([])
    assert result.shape==(0,384)
    assert result.dtype==np.float32
    assert not session.calls


def test_onnx_reranker_pairs_keep_raw_logits_and_order():
    from backend.semantic_onnx import OnnxReranker
    tokenizer=FakeTokenizer()
    session=FakeSession(lambda inputs:np.array([[float(i)] for i in range(len(inputs["input_ids"]))]))
    result=OnnxReranker(session,tokenizer).predict([("س1","ج1"),("س2","ج2")],batch_size=2)
    assert result.tolist()==[0.,1.]
    texts,kwargs=tokenizer.calls[0]
    assert texts==["س1","س2"]
    assert kwargs["text_pair"]==["ج1","ج2"]
    assert kwargs["truncation"] is False


def test_onnx_load_uses_pinned_local_files_cpu_four_threads_and_no_network(monkeypatch,tmp_path):
    from backend import semantic,semantic_onnx
    manifest={"runtime":semantic_onnx.RUNTIME,"models":[]}
    for kind,name,revision,filename in (("embed",semantic.EMBED_MODEL,semantic.EMBED_REVISION,"model_qint8_avx512_vnni.onnx"),("rerank",semantic.RERANK_MODEL,semantic.RERANK_REVISION,"model_quint8_avx2.onnx")):
        path=tmp_path/("onnx-"+kind+"-"+revision)/filename
        path.parent.mkdir()
        path.write_bytes(("fake-"+kind).encode())
        manifest["models"].append({"kind":kind,"model":name,"revision":revision,"file":path.relative_to(tmp_path).as_posix(),"size":path.stat().st_size,"sha256":semantic.file_sha256(path)})
    manifest["fingerprint"]=semantic._json_hash(manifest["models"])
    (tmp_path/"onnx-manifest.json").write_text(json.dumps(manifest),encoding="utf-8")
    sessions=[];tokenizer_calls=[]
    def inference(path,options,providers):
        sessions.append((path,options,providers))
        return FakeSession()
    def tokenizer(path,**kwargs):
        tokenizer_calls.append((path,kwargs))
        return FakeTokenizer()
    fake_ort=SimpleNamespace(SessionOptions=lambda:SimpleNamespace(),GraphOptimizationLevel=SimpleNamespace(ORT_ENABLE_ALL="all"),InferenceSession=inference)
    monkeypatch.setitem(sys.modules,"onnxruntime",fake_ort)
    monkeypatch.setitem(sys.modules,"transformers",SimpleNamespace(AutoTokenizer=SimpleNamespace(from_pretrained=tokenizer)))
    monkeypatch.setattr(semantic,"MODEL_DIR",tmp_path)
    monkeypatch.setattr(semantic.urllib.request,"urlopen",lambda *a,**k:pytest.fail("Offline adapter tried network"))
    embed,rerank=semantic_onnx.load_onnx_models()
    assert isinstance(embed,semantic_onnx.OnnxEmbedding)
    assert isinstance(rerank,semantic_onnx.OnnxReranker)
    assert len(sessions)==len(tokenizer_calls)==2
    for _,options,providers in sessions:
        assert options.intra_op_num_threads==4
        assert options.inter_op_num_threads==1
        assert providers==["CPUExecutionProvider"]
    for path,kwargs in tokenizer_calls:
        assert kwargs["local_files_only"] is True
        assert kwargs["trust_remote_code"] is False


def test_atomic_json_retries_temporary_windows_sharing_violation(monkeypatch,tmp_path):
    from backend import semantic
    path=tmp_path/"checkpoint.json"
    path.write_text('{"encoded":1}',encoding="utf-8")
    real_replace=semantic.os.replace
    attempts=[]
    def replace(source,target):
        attempts.append((source,target))
        if len(attempts)<3:
            assert json.loads(path.read_text(encoding="utf-8"))["encoded"]==1
            raise PermissionError("synthetic temporary Windows handle")
        real_replace(source,target)
    monkeypatch.setattr(semantic.os,"replace",replace)
    monkeypatch.setattr(semantic.time,"sleep",lambda *_:None)
    semantic.atomic_json(path,{"encoded":2})
    assert len(attempts)==3
    assert json.loads(path.read_text(encoding="utf-8"))["encoded"]==2
    assert not path.with_name(path.name+".tmp").exists()


def test_atomic_json_persistent_failure_preserves_previous_and_recoverable_temp(monkeypatch,tmp_path):
    from backend import semantic
    path=tmp_path/"checkpoint.json"
    path.write_text('{"encoded":1}',encoding="utf-8")
    attempts=[]
    def replace(*args):
        attempts.append(args)
        raise PermissionError("synthetic persistent lock")
    monkeypatch.setattr(semantic.os,"replace",replace)
    monkeypatch.setattr(semantic.time,"sleep",lambda *_:None)
    with pytest.raises(PermissionError):semantic.atomic_json(path,{"encoded":2})
    assert len(attempts)==15
    assert json.loads(path.read_text(encoding="utf-8"))["encoded"]==1
    assert json.loads(path.with_name(path.name+".tmp").read_text(encoding="utf-8"))["encoded"]==2


@pytest.mark.parametrize("invalid",["runtime","kinds","revision","model","path","fingerprint"])
def test_onnx_manifest_rejects_unapproved_profiles_and_paths(monkeypatch,tmp_path,invalid):
    from backend import semantic,semantic_onnx
    monkeypatch.setattr(semantic,"MODEL_DIR",tmp_path)
    entries=[]
    for kind,name,revision,filename in (("embed",semantic.EMBED_MODEL,semantic.EMBED_REVISION,"model_qint8_avx512_vnni.onnx"),("rerank",semantic.RERANK_MODEL,semantic.RERANK_REVISION,"model_quint8_avx2.onnx")):
        entries.append({"kind":kind,"model":name,"revision":revision,"file":("onnx-"+kind+"-"+revision+"/"+filename),"sha256":"0"*64,"size":1})
    manifest={"runtime":semantic_onnx.RUNTIME,"models":entries,"fingerprint":semantic._json_hash(entries)}
    assert semantic_onnx.validate_onnx_manifest(manifest)==manifest
    if invalid=="runtime":manifest["runtime"]="unapproved-runtime"
    elif invalid=="kinds":manifest["models"]=manifest["models"][:1]
    elif invalid=="revision":manifest["models"][0]["revision"]="unknown-revision"
    elif invalid=="model":manifest["models"][0]["model"]="unknown-model"
    elif invalid=="path":manifest["models"][0]["file"]="../outside.onnx"
    else:manifest["fingerprint"]="invalid"
    with pytest.raises(RuntimeError):semantic_onnx.validate_onnx_manifest(manifest)
