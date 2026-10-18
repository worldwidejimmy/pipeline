"""T0: the compare judge's A/B labels are normalised to the UI columns (A = left = RAG).

The judge sees a random order (position-bias mitigation); when no-RAG was shown first
its "Answer A" is the right-hand column, so the stream swaps A<->B. These pin the swap
— including the streamed form, which must match the one-shot form for any chunking.
"""
import pytest

from src.judge_labels import LabelNormaliser, swap_labels

VERDICT = ("**Verdict:** Answer A — more specific.\n"
           "- Answer B invents a release date; Answer A cites docs.\n"
           "- Neither fully answers a question about ANSWER B's sources. Tie? No.")


def test_swaps_whole_labels_only():
    out = swap_labels(VERDICT)
    assert out.startswith("**Verdict:** Answer B")
    assert "Answer A invents" in out and "Answer B cites" in out
    assert "ANSWER A's" in out                       # case of "answer" kept, letter swapped
    assert "answer a question" in swap_labels("answer a question")   # lowercase a is prose
    assert swap_labels("Answer AB and Answer C") == "Answer AB and Answer C"


def test_double_swap_is_identity():
    assert swap_labels(swap_labels(VERDICT)) == VERDICT


@pytest.mark.parametrize("size", [1, 2, 3, 7, 50])
def test_streaming_matches_one_shot(size):
    n = LabelNormaliser(swap=True)
    out = "".join(n.feed(VERDICT[i:i + size]) for i in range(0, len(VERDICT), size)) + n.flush()
    assert out == swap_labels(VERDICT)


def test_label_split_across_chunks():
    # "Answer A" arrives as "Ans" + "wer " + "A" + " wins" — must still swap.
    n = LabelNormaliser(swap=True)
    out = "".join(n.feed(c) for c in ["Ans", "wer ", "A", " wins"]) + n.flush()
    assert out == "Answer B wins"


def test_no_swap_is_passthrough():
    n = LabelNormaliser(swap=False)
    assert n.feed("Answer A") == "Answer A" and n.flush() == ""
