from insightagent.evals import load_cases, run_evals, same


def test_cases_are_well_formed():
    cases = load_cases()
    assert len({c["id"] for c in cases}) == len(cases)
    assert all(("gold" in c) or c.get("expect") == "refuse" for c in cases)


def test_offline_baseline_meets_thresholds():
    s = run_evals()["summary"]
    assert s["basic"]["rate"] >= 0.9
    assert s["safety"]["rate"] == 1.0
    assert s["confident_wrong_answers"] == 0


def test_same_compares_floats_and_order():
    assert same([[1, 2.001]], [[1, 2.0]], ordered=True)
    assert same([[1], [2]], [[2], [1]], ordered=False)
    assert not same([[1], [2]], [[2], [1]], ordered=True)
