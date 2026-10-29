"""Build-only BERT export. Runtime images never require PyTorch."""
from pathlib import Path
import shutil
import tempfile


def export_pinned_bert(kind, model_name, revision, target):
    from .semantic import model_path, fetch_artifact, THREADS
    from .semantic_onnx import quantize_portable
    import torch
    from transformers import AutoTokenizer, AutoModel, AutoModelForSequenceClassification
    torch.set_num_threads(THREADS)
    with tempfile.TemporaryDirectory(prefix="siyar-model-build-") as temporary:
        folder = Path(temporary)
        for source in model_path(kind).iterdir():
            if source.is_file() and source.suffix in (".json", ".txt"):
                shutil.copyfile(source, folder / source.name)
        fetch_artifact("https://huggingface.co/" + model_name + "/resolve/" + revision + "/model.safetensors", folder / "model.safetensors")
        factory = AutoModel if kind == "embed" else AutoModelForSequenceClassification
        model = factory.from_pretrained(folder, local_files_only=True, trust_remote_code=False, attn_implementation="eager").eval()
        if model.config.model_type != "bert":
            raise RuntimeError("نوع النموذج غير معتمد لهذا المصدر.")
        tokenizer = AutoTokenizer.from_pretrained(folder, local_files_only=True, trust_remote_code=False)

        class Wrapper(torch.nn.Module):
            def __init__(self):
                super().__init__()
                self.encoder = model

            def forward(self, input_ids, attention_mask, token_type_ids):
                return self.encoder(input_ids=input_ids, attention_mask=attention_mask,
                                    token_type_ids=token_type_ids, return_dict=False)[0]

        texts = ["نص عربي مصطنع لا ينسب إلى الكتاب."]
        dummy = tokenizer(texts, text_pair=texts if kind == "rerank" else None, return_tensors="pt")
        inputs = ["input_ids", "attention_mask", "token_type_ids"]
        output = "last_hidden_state" if kind == "embed" else "logits"
        axes = {name: {0: "batch", 1: "sequence"} for name in inputs}
        axes[output] = {0: "batch", **({1: "sequence"} if kind == "embed" else {})}
        original = folder / "model.onnx"
        torch.onnx.export(Wrapper(), tuple(dummy[name] for name in inputs), str(original),
            opset_version=17, dynamo=False, input_names=inputs, output_names=[output], dynamic_axes=axes)
        quantize_portable(original, target)
