from scripts.evaluate_curation import known_witness_hit, percentile95


def test_known_witness_requires_source_biography_and_sufficient_overlap():
    gold = {"start": 100, "end": 300}
    hit = {"source_id": 1, "biography_id": 8, "source_char_start": 260, "source_char_end": 320}
    assert known_witness_hit(hit, gold, 1, 8)
    assert not known_witness_hit({**hit, "source_char_start": 261}, gold, 1, 8)
    assert not known_witness_hit({**hit, "biography_id": 9}, gold, 1, 8)
    assert not known_witness_hit({**hit, "source_id": 2}, gold, 1, 8)
    assert not known_witness_hit({**hit, "source_char_start": 400}, gold, 1, 8)


def test_short_diacritic_anchor_uses_codepoint_quarter_rounded_up():
    gold = {"start": 100, "end": 109}
    hit = {"source_id": 1, "biography_id": 8, "source_char_start": 106, "source_char_end": 110}
    assert known_witness_hit(hit, gold, 1, 8)
    assert not known_witness_hit({**hit, "source_char_start": 107}, gold, 1, 8)


def test_latency_percentile_interpolates_without_changing_sample():
    values = [100, 10, 40]
    assert percentile95(values) == 94
    assert values == [100, 10, 40]
    assert percentile95([17]) == 17
    assert percentile95([]) is None
