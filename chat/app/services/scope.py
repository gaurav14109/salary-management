"""What this assistant will and will not take a question about.

The system prompt asks the model to answer only from tool results and reference
notes. That is a request, and a model asked "what is the capital of France" knows
the answer perfectly well. An assistant inside a payroll tool that confidently
answers general knowledge is one whose payroll answers nobody can calibrate either -
the user has no way to tell which kind of answer they just got.

So scope is decided here, before the model is involved, by one question: does this
sentence use the vocabulary of the thing we actually hold data about? A keyword
gate, not a classifier. It is crude in a known direction - it lets through anything
that mentions pay, and that is fine, because the *second* guard catches what it
misses: an answer that no tool and no note grounded is never returned at all.

The crudeness is deliberate. A model asked to decide "is this in scope" is one more
thing that can be talked round, and it costs a call.
"""

from __future__ import annotations

import re

# The vocabulary of this product. A question that touches none of it is not a
# question this assistant has data for.
VOCABULARY = frozenset(
    """
    salary salaries salaried pay paid pays payroll payrolls wage wages compensation
    comp earn earning earnings income remuneration raise raises increment increase
    employee employees employed employer staff headcount person people worker workers
    hire hired hiring joiner joiners leaver leavers termination terminated attrition
    team teams department departments division engineering sales marketing finance
    legal design data operations product
    band bands level levels seniority grade grades rank junior senior principal
    country countries region office location
    currency currencies usd dollar dollars inr rupee rupees eur euro gbp pound jpy
    yen exchange fx rate rates conversion convert
    average mean median total sum highest lowest top bottom minimum maximum spread
    distribution breakdown summary count how many figure figures number numbers
    budget cost costs spend spending
    import imported upload file spreadsheet csv row rows
    history historical effective date dated period periods change changes
    dashboard report reporting analytics
    acme company organisation organization
    """.split()
)

# Place names are deliberately absent. "How many employees in India" already says
# "employees"; "what is the capital of France" says only France, and listing country
# names made that a payroll question. A gate that lets through anything naming a
# country we employ in is a gate with a hole the width of general knowledge.

REFUSAL = (
    "I can only answer questions about ACME's payroll data — headcount, salaries, "
    "averages and medians, pay by country, department or band, and how this system "
    "handles pay. I don't have anything else to go on, so I'd rather say so than "
    "guess."
)


UNAVAILABLE = "I could not read the payroll figures just now. Please try again in a moment."


def in_scope(question: str) -> bool:
    """Does this question use the vocabulary of the data we hold?"""
    return bool(VOCABULARY & _words(question))


def _words(text: str) -> frozenset[str]:
    return frozenset(re.findall(r"[a-z]+", (text or "").lower()))
