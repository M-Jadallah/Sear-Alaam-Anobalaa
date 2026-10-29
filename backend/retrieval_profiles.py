"""Pinned model choices. A profile change requires rebuilding every index."""
import os

GEOMETRY_MODEL = "intfloat/multilingual-e5-small"
GEOMETRY_REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
PROFILES = {
    "legacy-e5": {
        "embed": GEOMETRY_MODEL, "embed_revision": GEOMETRY_REVISION,
        "rerank": "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1",
        "rerank_revision": "1427fd652930e4ba29e8149678df786c240d8825",
        "dimension": 384, "query_prefix": "query: ", "passage_prefix": "passage: ",
        "storage": "float32",
    },
    "arabic-retrieval-v1": {
        "embed": "omarelshehy/Arabic-Retrieval-v1.0",
        "embed_revision": "899f6e1b765915a72d5e4ace6bb2b221715550d8",
        "rerank": "NAMAA-Space/GATE-Reranker-V1",
        "rerank_revision": "664ebca13e4b79b8996b3b1bc60489a75997c8e6",
        "dimension": 768, "query_prefix": "", "passage_prefix": "",
        "storage": "row-int8-unit-v1",
    },
}
PROFILE_ID = os.environ.get("SIYAR_MODEL_PROFILE", "legacy-e5")
if PROFILE_ID not in PROFILES:
    raise ValueError("SIYAR_MODEL_PROFILE غير معتمد.")
PROFILE = PROFILES[PROFILE_ID]
