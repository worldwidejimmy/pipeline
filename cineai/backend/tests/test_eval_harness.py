"""T0: the eval harness's own pass/fail logic (canary checks + baseline gates).
An eval that silently passes is worse than none — so the judge of the judges gets tests."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "evals"))

from run_evals import check_canary, gate  # noqa: E402

INCEPTION = {"contain_any": ["DiCaprio", "Nolan"], "min_any": 2}


def test_canary_pass():
    assert check_canary(INCEPTION, "Christopher Nolan directs Leonardo DiCaprio.") == []


def test_canary_missing_fact():
    assert check_canary(INCEPTION, "Leonardo DiCaprio stars.") == ["only 1/2 of ['DiCaprio', 'Nolan']"]


def test_canary_fail_phrase_even_if_facts_present():
    # The fenced-JSON weeks: confident, well-formed, and wrong.
    out = check_canary(INCEPTION, "I couldn't find Inception. Nolan? DiCaprio?")
    assert out == ['says "couldn\'t find"']


def test_canary_empty():
    assert check_canary(INCEPTION, "  ") == ["empty answer"]


def test_canary_second_list():
    c = {"contain_any": ["Ebert"], "contain_any_2": ["Minnesota", "Marge"]}
    assert check_canary(c, "Ebert loved it.") == ["only 0/1 of ['Minnesota', 'Marge']"]


BASE = {"t1": {"accuracy": 0.98, "tolerance": 0.06, "failures": []},
        "t2": {"mrr": 0.8, "misses": []},
        "t4": {"failures": []},
        "t5": {"p95_ms": 10000, "tokens_per_query": 5000}}


def test_t1_noise_tolerated_but_drop_fails():
    assert gate({"tier": "t1", "accuracy": 0.94, "failures": ["x", "y"]}, BASE)[0] == "pass"
    assert gate({"tier": "t1", "accuracy": 0.90, "failures": ["x"]}, BASE)[0] == "fail"


def test_t2_new_miss_or_mrr_drop_fails():
    assert gate({"tier": "t2", "mrr": 0.8, "misses": ["police"]}, BASE)[0] == "fail"
    assert gate({"tier": "t2", "mrr": 0.7, "misses": []}, BASE)[0] == "fail"
    assert gate({"tier": "t2", "mrr": 0.78, "misses": []}, BASE)[0] == "pass"


def test_t4_any_new_failure_fails():
    assert gate({"tier": "t4", "failures": ["inception"]}, BASE)[0] == "fail"


def test_t5_doubling_fails():
    assert gate({"tier": "t5", "p95_ms": 19000, "tokens_per_query": 5100}, BASE)[0] == "pass"
    assert gate({"tier": "t5", "p95_ms": 21000, "tokens_per_query": 5100}, BASE)[0] == "fail"
    assert gate({"tier": "t5", "p95_ms": 9000, "tokens_per_query": 10001}, BASE)[0] == "fail"


def test_missing_baseline_is_new_not_pass():
    assert gate({"tier": "t1", "accuracy": 1.0, "failures": []}, {})[0] == "new"


# ── T3 judge pass rules + gate ────────────────────────────────────────────────
from run_evals import quality_pass  # noqa: E402

V = lambda g, r, c, admits=False: {"groundedness": g, "relevance": r, "completeness": c,  # noqa: E731
                                   "admits_limits": admits, "unsupported_claims": []}


def test_quality_answerable_needs_grounding_and_relevance():
    assert quality_pass({"answerable": True}, V(4, 4, 3))
    assert not quality_pass({"answerable": True}, V(3, 5, 5))     # well-written but ungrounded
    assert not quality_pass({"answerable": True}, V(5, 3, 5))


def test_quality_unanswerable_rewards_admitting_it():
    assert quality_pass({"answerable": False}, V(5, 5, 5, admits=True))
    # Confident invention fails even if the judge thought it "relevant".
    assert not quality_pass({"answerable": False}, V(5, 5, 5, admits=False))
    assert not quality_pass({"answerable": False}, V(2, 5, 5, admits=True))


def test_t3_uncalibrated_judge_fails_the_run():
    base = {"t3": {"pass_rate": 1.0, "tolerance": 0.15, "failures": []}}
    good = {"tier": "t3", "pass_rate": 1.0, "failures": [], "judge_calibrated": True, "judge_calibration": "4/4"}
    assert gate(good, base)[0] == "pass"
    assert gate(good | {"judge_calibrated": False, "judge_calibration": "3/4"}, base)[0] == "fail"
    assert gate(good | {"pass_rate": 0.8, "failures": ["a", "b", "c"]}, base)[0] == "fail"
