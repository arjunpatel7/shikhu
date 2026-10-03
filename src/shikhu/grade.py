"""Grade a free-text answer against a question's keyed answer, using Jev.

Multiple choice turned out to be answerable without any code (no-code models scored 90-98%
because the options let them eliminate their way to the sensible one), so the target format
is a short written answer. A written answer has no letter to compare, so a Jev Noul judges
whether it makes the same point as the key.

Validated on clean cases only: it accepted 40/40 keyed answers and rejected 120/120
distractors. It has NOT been checked against real paraphrased answers, where it looked strict
on a few near-paraphrases. Treat a score just under ACCEPT as "unsure", not "wrong", and
remember it can only be as right as the key it is given — a mis-keyed question is graded
wrongly no matter how good the grader is.
"""

from shikhu import systemone

ACCEPT = 0.5  # probability at or above which an answer counts as matching the key

# Recorded on each graded attempt so scores from different graders are never conflated.
# Bump the suffix when the Noul wording below changes.
GRADER_ID = f"{systemone.DEFAULT_JEV_MODEL}/match-v1"

_MATCHES = systemone.noul(
    "Does `state.candidate_answer` give the same answer to `state.question` as "
    "`state.reference_answer` does?",
    true_means=(
        "The candidate states the same key fact, reason or behavior as the reference. "
        "Different wording, extra detail, or a more general phrasing of the same point is fine."
    ),
    false_means=(
        "The candidate gives a different reason or behavior, contradicts the reference, is "
        "vague enough to fit many answers, declines to answer, or merely restates the question."
    ),
)


def grade(question: str, reference: str, candidate: str) -> float:
    """Probability that `candidate` answers `question` the way `reference` does.

    An empty answer scores 0 without a call: there is nothing to judge, and a respondent that
    produced nothing (an error, a refusal) should not cost anything to grade."""
    if not candidate or not candidate.strip():
        return 0.0
    answers, _ = systemone.ask(
        state={
            "question": question,
            "reference_answer": reference,
            "candidate_answer": candidate,
        },
        questions={"matches": _MATCHES},
    )
    return answers["matches"]["noul"]


def is_correct(score: float) -> bool:
    return score >= ACCEPT
