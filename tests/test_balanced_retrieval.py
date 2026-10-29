"""Channel starvation regression using clearly synthetic candidate ranges."""
from backend.smart_search import balanced_pool


def test_balanced_pool_reserves_channels_and_uses_independent_votes():
    def row(start, granularity=None):
        return {"source_id": 999, "source_char_start": start, "source_char_end": start + 10,
                "clean_text": "شاهد مصطنع للاختبار فقط", "granularity": granularity}
    lexical = [row(100 + i * 20) for i in range(6)]
    morphology = [row(300 + i * 20) for i in range(6)]
    morphology[3] = dict(lexical[3])
    semantic = [row(500 + i * 20, "context") for i in range(6)]
    semantic += [row(700 + i * 20, "paragraph") for i in range(6)]
    result = balanced_pool(lexical, semantic, morphology, maximum=6, seed=1)
    assert len(result) == 6
    starts = {r["source_char_start"] for r in result}
    assert {100, 300, 500, 700, 160}.issubset(starts)
    shared = next(r for r in result if r["source_char_start"] == 160)
    assert shared["scores"]["lexical_rank"] == 4
    assert shared["scores"]["morphology_rank"] == 4
    assert all("semantic_rank" in r["scores"] for r in result if r.get("granularity") in ("context", "paragraph"))
