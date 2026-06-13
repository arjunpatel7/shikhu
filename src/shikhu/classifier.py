"""Classify prompts as conceptual vs not using regex heuristics.

Conceptual prompts ask about *why* code works, design decisions, trade-offs,
architecture, etc. Non-conceptual prompts are commands, acknowledgments, or
simple requests.

74% accuracy on 50 labeled prompts — good enough for bootstrapping.
"""

import re

CONCEPTUAL_PATTERNS = [
    r"\bwhy\b",
    r"\bhow does\b",
    r"\bhow do\b",
    r"\bhow would\b",
    r"\bhow can\b",
    r"\bhow should\b",
    r"\bexplain\b",
    r"\bwhat is the (?:purpose|point|reason|difference|trade-?off|benefit|advantage|disadvantage)\b",
    r"\bwhat are the (?:trade-?offs|benefits|advantages|disadvantages|options|differences)\b",
    r"\bwhat makes\b",
    r"\bwhat happens (?:if|when)\b",
    r"\bhelp me (?:think|understand|reason)\b",
    r"\bwhat\'s the (?:idea|concept|theory|principle|design|architecture)\b",
    r"\bshould i\b",
    r"\bis it (?:possible|better|worth|important)\b",
    r"\bwhat (?:would|could) happen\b",
    r"\bdesign\b.*\b(?:decision|choice|pattern|approach)\b",
    r"\btrade-?off\b",
    r"\barchitecture\b",
    r"\bunder the hood\b",
    r"\bbehind the scenes\b",
    r"\bconceptually\b",
    r"\bin theory\b",
    r"\bprocess for\b",
    r"\bthink (?:about|through)\b",
]

NOT_CONCEPTUAL_PATTERNS = [
    r"^(?:yes|no|yeah|yep|nah|ok|okay|sure|sweet|great|thanks|ack|bingo|hmm+)\b",
    r"\b(?:fix|run|clear|dump|check|make|delete|remove|add|create|update|commit|push)\b.*\b(?:it|this|that|the)\b",
    r"^(?:test prompt|remind me|give me|show me|list)\b",
]

MIN_CONCEPTUAL_LENGTH = 15


def classify(text: str) -> str:
    """Return 'conceptual' or 'not_conceptual'."""
    lower = text.lower().strip()

    if len(lower) < MIN_CONCEPTUAL_LENGTH:
        return "not_conceptual"

    # Check anti-patterns first
    for pattern in NOT_CONCEPTUAL_PATTERNS:
        if re.search(pattern, lower):
            # But still check if there's a strong conceptual signal
            has_conceptual = any(re.search(p, lower) for p in CONCEPTUAL_PATTERNS[:8])
            if not has_conceptual:
                return "not_conceptual"

    # Check conceptual patterns
    for pattern in CONCEPTUAL_PATTERNS:
        if re.search(pattern, lower):
            return "conceptual"

    return "not_conceptual"
