"""Reference notes: the half of an answer that is not live data.

"What does the median mean here" and "why does a 2021 salary convert at a 2021 rate"
are questions no tool can answer, because they are about how the product works
rather than about the numbers in it. Without notes the model either refuses - unhelpful
when the answer is written down in the design - or invents policy, which is worse.

These are deliberately about *this product's rules*, not about HR in general. A model
already knows what a median is; what it cannot know is that ACME freezes the exchange
rate at write time, or that a leaver keeps their history but leaves the payroll total.

Matched by keyword rather than embeddings. A dozen short notes do not justify a vector
store and an embedding service, and a keyword match is debuggable when it picks the
wrong note - you can see exactly which word did it. The trigger to change: notes past
~50, or questions that miss because they use words the notes do not.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# At most three notes reach the prompt. The context window is shared with tool
# results, and a model given six notes starts quoting the least relevant one.
MAX_NOTES = 3

# A question has to share at least this many distinct keywords with a note before it
# counts as a match. One is too loose: "what is our total pay" would pull in every
# note containing "pay".
MIN_HITS = 1

_STOPWORDS = frozenset(
    """
    a an the is are was were do does did how what why when which who whom our us we
    i you it its of in on at to for from by with about into over and or not no if
    this that these those can could should would will shall may might must have has
    had be been being me my your their there here show tell give list many much more
    most some any all please
    """.split()
)


@dataclass(frozen=True)
class Note:
    title: str
    keywords: frozenset[str]
    body: str


def _note(title: str, keywords: str, body: str) -> Note:
    return Note(title=title, keywords=frozenset(keywords.split()), body=body.strip())


NOTES: tuple[Note, ...] = (
    _note(
        "How the median salary is calculated",
        "median middle percentile average mean typical distribution midpoint",
        """
        The median is the middle salary once everyone's pay is sorted, computed in the
        database with percentile_cont. With an even headcount it is the average of the
        two middle values. It counts one salary per active employee, unweighted - it is
        not the midpoint of total payroll. The mean is reported beside it because the
        two differ: one very high salary moves the mean and leaves the median alone.
        """,
    ),
    _note(
        "Why old salaries use old exchange rates",
        "fx exchange rate currency usd conversion convert dollars historical frozen",
        """
        Every salary stores its own USD value, converted once when the salary was
        recorded, using the exchange rate in force on its effective date. Rates are
        never re-applied afterwards. This is why a report run twice gives the same
        answer: a figure from 2021 still converts at a 2021 rate. If no rate exists for
        a currency on that date the salary is refused rather than converted at parity -
        a salary stored at the wrong rate looks like money and is wrong by a factor of
        eighty.
        """,
    ),
    _note(
        "What a band is",
        "band bands level seniority grade rank l1 l7 junior senior",
        """
        A band is ACME's seniority level, L1 to L7, where L1 is most junior. Bands are
        ordered by rank, not by name - sorted as text they come out L1, L10, L11, L2,
        which makes any average-pay-by-band comparison meaningless. Not every employee
        has one: contractors and executives sit outside the banding system.
        """,
    ),
    _note(
        "How pay history works",
        "history raise increase change promotion effective backdate previous past",
        """
        A salary is never edited in place. Recording a pay change closes the current
        period the day before the new one starts and opens a new one, so an employee's
        pay is a sequence of dated periods rather than a single number. That is what
        makes "what did this person earn last March" answerable. A change dated on or
        before the current period is refused, because it would rewrite a period that
        has already been reported on.
        """,
    ),
    _note(
        "Who counts as on the payroll",
        "leaver leavers terminated inactive active headcount count employees former",
        """
        Every payroll figure counts active employees with an open salary period.
        Somebody who has left keeps their full history - their past pay is still
        answerable - but they are excluded from headcount, totals, averages and the
        median from their termination date. This is why the headcount here can differ
        from the number of rows in an export that includes former staff.
        """,
    ),
    _note(
        "Where the figures come from",
        "source data trust accurate where figures numbers calculated computed",
        """
        Every figure the assistant reports is read live from the salary service at the
        moment the question is asked, through the same endpoints the dashboard uses.
        Nothing is cached and nothing is estimated. The assistant can only read what the
        person asking is allowed to read, and it cannot change any data - pay changes
        are made on an employee's own page.
        """,
    ),
    _note(
        "How money is shown",
        "currency usd dollars local amount comparison compare rupees total",
        """
        Totals, averages and comparisons are always in US dollars, because adding
        rupees to yen gives a number that means nothing. Each employee's own pay is
        also held in the currency they are actually paid in, at that currency's own
        precision - yen has no minor units, Kuwaiti dinar has three.
        """,
    ),
)


def search(question: str, *, limit: int = MAX_NOTES) -> list[Note]:
    """The notes whose keywords the question actually uses.

    Scored by how many distinct keywords match, so a question about both bands and
    currency gets both notes, in the order they are most relevant.
    """
    words = _words(question)
    if not words:
        return []

    scored = [
        (len(note.keywords & words), note)
        for note in NOTES
        if len(note.keywords & words) >= MIN_HITS
    ]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [note for _, note in scored[:limit]]


def as_context(notes: list[Note]) -> str:
    """Render notes for the system turn."""
    return "\n\n".join(f"{note.title}: {note.body}" for note in notes)


def _words(text: str) -> frozenset[str]:
    return frozenset(
        word
        for word in re.findall(r"[a-z0-9]+", (text or "").lower())
        if word not in _STOPWORDS and len(word) > 1
    )
