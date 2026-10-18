"""
Normalise the blind judge's "Answer A / Answer B" labels to the UI's columns.

The judge sees the two answers in RANDOM order (position-bias mitigation), but the
compare UI always shows RAG on the left. Without this, half the time the judge's
"Answer A" is the right-hand column — confusing to read. We keep the judge blind
and randomised, and swap A<->B in its streamed reply whenever no-RAG was shown
first, so "Answer A" always means the left (RAG) column.

The swap is length-preserving, so the streamed text can be rewritten on the fly:
only the last character is ever held back (a trailing "Answer A" isn't a whole
label until we see the character after the letter).
"""
from __future__ import annotations

import re

# "answer" is case-insensitive, the letter is NOT — "answer a question" must survive.
_LABEL = re.compile(r"\b((?i:answer))(\s+)([AB])\b")


def swap_labels(text: str) -> str:
    """Swap every whole-word "Answer A" <-> "Answer B" (case of "answer" kept)."""
    return _LABEL.sub(lambda m: m.group(1) + m.group(2) + ("B" if m.group(3) == "A" else "A"), text)


class LabelNormaliser:
    """Streaming A<->B swapper. feed() returns text safe to emit now; flush() the rest."""

    def __init__(self, swap: bool):
        self.swap = swap
        self._raw = ""
        self._sent = 0

    def feed(self, chunk: str) -> str:
        if not self.swap:
            return chunk
        self._raw += chunk
        out = swap_labels(self._raw)[self._sent:len(self._raw) - 1]
        self._sent += len(out)
        return out

    def flush(self) -> str:
        if not self.swap:
            return ""
        out = swap_labels(self._raw)[self._sent:]
        self._sent = len(self._raw)
        return out
