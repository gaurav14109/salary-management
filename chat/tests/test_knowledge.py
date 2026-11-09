"""The reference notes: the half of an answer that is not live data.

"What does median mean here" and "why does a 2021 salary convert at a 2021 rate"
are questions no tool can answer, because they are about how the product works
rather than about the numbers in it. Without notes the model either refuses or
invents policy, and inventing policy is the worse failure.

Matched by keyword, not embeddings. A dozen short notes do not justify a vector
store, and a keyword match is debuggable when it picks the wrong one.
"""

from app.knowledge import search


def test_a_question_about_the_median_finds_the_median_note():
    found = search("how is the median salary calculated?")

    assert any("median" in note.title.lower() for note in found)


def test_a_question_about_currency_finds_the_fx_note():
    found = search("why is an old salary converted at an old exchange rate?")

    assert any("exchange" in note.title.lower() or "fx" in note.title.lower() for note in found)


def test_a_question_about_bands_finds_the_band_note():
    found = search("what is a band?")

    assert any("band" in note.title.lower() for note in found)


def test_an_unrelated_question_matches_nothing():
    """Returning a note regardless of relevance would put the FX policy in front of
    a model answering a question about headcount."""
    assert search("what is the weather in Oslo") == []


def test_results_are_capped_so_the_prompt_stays_small():
    found = search("salary pay band currency median employee country department")

    assert len(found) <= 3


def test_notes_render_as_text_for_the_prompt():
    from app.knowledge import as_context

    rendered = as_context(search("median"))

    assert "median" in rendered.lower()
    assert len(rendered) < 4000


def test_no_note_is_empty():
    from app.knowledge import NOTES

    assert NOTES
    for note in NOTES:
        assert note.title and len(note.body) > 50
