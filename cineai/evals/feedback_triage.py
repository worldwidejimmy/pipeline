#!/usr/bin/env python3
"""Turn 👎 answer feedback into DRAFT eval cases, for a human to review.

    make feedback-triage                       # last 30 days
    docker compose exec -T backend python3 /app/evals/feedback_triage.py --days 7

Reads data/feedback/ (written by src/feedback.py; already redacted), prints each 👎
with the visitor's note and the answer they got, and writes canary-shaped drafts to
evals/results/feedback_candidates.jsonl (gitignored).

Why not straight into the datasets: the datasets are in a PUBLIC repo, and these are
things strangers typed. A person decides (1) that it's a real failure and not taste,
(2) what a correct answer must contain (`contain_any`), and (3) that the question is
safe to publish, then copies the case into datasets/canaries.jsonl (or quality.jsonl)
by hand. That review step is the point of the flywheel, not friction in it.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from src import feedback  # noqa: E402

OUT = Path(__file__).resolve().parent / "results" / "feedback_candidates.jsonl"


def draft(r: dict, i: int) -> dict:
    return {"id": f"fb-{r['ts'][:10]}-{i}", "q": r["question"], "contain_any": [], "min_any": 1,
            "why": f"👎 from a visitor: {r['comment'] or '(no note)'}; route was {r['route']}, model {r['model']}",
            "_answer_they_got": r["answer_head"],
            "_todo": "decide it's a real failure; fill contain_any with facts a correct answer must include; "
                     "check the question is safe to publish; then copy into datasets/canaries.jsonl"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    a = ap.parse_args()
    downs = [r for r in feedback.load(a.days) if r["rating"] < 0]
    if not downs:
        print(f"No 👎 in the last {a.days} days.")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w") as f:
        for i, r in enumerate(downs, 1):
            d = draft(r, i)
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
            print(f"{i:2}. [{r['ts'][:16]}] {r['route'] or '?':12} “{r['question']}”")
            if r["comment"]:
                print(f"    note: {r['comment']}")
            print(f"    got:  {r['answer_head'][:160].replace(chr(10), ' ')}…")
    print(f"\n{len(downs)} draft case(s) → {OUT.relative_to(Path(__file__).resolve().parents[1])} (gitignored). Review before adding to datasets/.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
