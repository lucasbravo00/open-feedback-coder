"""Tests that the quote written out comes from the comment, not from the model.

The matcher is deliberately forgiving about typography, which creates a trap:
a model can send `chaotic - nobody owned it` and be right, while the comment
actually reads `chaotic — nobody  owned it`. Accepting the match is correct.
Storing the model's string would not be, because the output file would then
contain a quote that is not in the source, and the offsets beside it would
point at something else.

These tests fail if the pipeline ever stores what the model sent.
"""

import csv

import pytest

from open_feedback_coder.label import assemble_comment_rows
from open_feedback_coder.propose import build_codebook
from open_feedback_coder.text import canonicalise, locate_quote

EM_DASH = "—"
CURLY_OPEN = "“"
CURLY_CLOSE = "”"
ZERO_WIDTH_SPACE = "​"
SOFT_HYPHEN = "­"
NEXT_LINE = ""
NON_BREAKING_SPACE = " "

# Typography the model is likely to flatten, plus a doubled space it will not
# reproduce. Both are inside the span the tests quote.
SOURCE = (
    "Onboarding was chaotic " + EM_DASH + " nobody  owned it, and the "
    + CURLY_OPEN + "buddy" + CURLY_CLOSE + " never appeared."
)
MODEL_QUOTE = 'chaotic - nobody owned it, and the "buddy" never appeared'


def response(theme_id, quote, role="primary", valence="negative"):
    return {
        "unassigned": False,
        "assignments": [
            {"theme_id": theme_id, "role": role, "valence": valence, "quote": quote}
        ],
    }


def test_the_row_stores_the_source_span_not_the_model_string(book, make_comment):
    comment = make_comment(SOURCE)

    outcome = assemble_comment_rows(comment, response("onboarding", MODEL_QUOTE), book)

    assert not outcome.failed
    row = outcome.rows[0]

    # The match was accepted...
    assert row["quote"]
    # ...but what got stored is the comment's own text, not what was sent.
    assert row["quote"] != MODEL_QUOTE
    assert EM_DASH in row["quote"]
    assert CURLY_OPEN in row["quote"]
    assert "nobody  owned it" in row["quote"]
    assert row["comment_text"][row["quote_start"] : row["quote_end"]] == row["quote"]


def test_the_stored_span_survives_a_round_trip_through_csv(tmp_path, book, make_comment):
    """The guarantee has to hold in the file, not only in memory."""
    comment = make_comment(SOURCE)
    outcome = assemble_comment_rows(comment, response("onboarding", MODEL_QUOTE), book)

    path = tmp_path / "labelled.csv"
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(outcome.rows[0]))
        writer.writeheader()
        writer.writerows(outcome.rows)

    with open(path, newline="", encoding="utf-8") as handle:
        written = list(csv.DictReader(handle))

    row = written[0]
    start, end = int(row["quote_start"]), int(row["quote_end"])
    assert row["comment_text"][start:end] == row["quote"]
    assert row["quote"] != MODEL_QUOTE


def test_a_proposed_example_also_stores_the_source_span(make_comment):
    corpus = [make_comment(SOURCE, "1", 1)]

    result = build_codebook(
        {
            "themes": [
                {
                    "id": "onboarding",
                    "label": "Onboarding",
                    "description": "",
                    "examples": [{"comment_id": "1", "quote": MODEL_QUOTE}],
                }
            ]
        },
        corpus,
        max_themes=15,
    )

    stored = result.codebook.themes[0].examples[0].quote
    assert stored != MODEL_QUOTE
    assert stored in corpus[0].text


@pytest.mark.parametrize(
    ("raw_comment", "model_quote"),
    [
        # A zero-width character inside a word must not create a word break
        # the reader and the model cannot see.
        ("The team was co" + ZERO_WIDTH_SPACE + "operative throughout", "cooperative throughout"),
        ("Pay" + SOFT_HYPHEN + "rise never happened", "Payrise never happened"),
        # NEL is whitespace, as it already is for str.strip().
        ("line one" + NEXT_LINE + "line two matters", "line one line two matters"),
        (
            "Tabs\tand\tnon-breaking" + NON_BREAKING_SPACE + "spaces",
            "and non-breaking spaces",
        ),
    ],
)
def test_invisible_characters_do_not_reject_a_quote_of_the_visible_text(
    raw_comment, model_quote
):
    comment = canonicalise(raw_comment)

    match = locate_quote(model_quote, comment)

    assert match is not None, f"{model_quote!r} should be findable in {raw_comment!r}"
    assert comment[match.start : match.end] == match.text


def test_a_quote_that_invents_a_word_break_is_still_rejected():
    """The tolerance goes one way only: invisible, not imaginary."""
    comment = canonicalise("The team was co" + ZERO_WIDTH_SPACE + "operative throughout")

    assert locate_quote("co operative throughout", comment) is None


def test_a_decomposed_quote_matches_a_composed_comment(book, make_comment):
    """The direction that actually occurs: NFC comment, NFD from the model."""
    import unicodedata

    comment = make_comment("El proceso de evaluación no sirve")
    decomposed = unicodedata.normalize("NFD", "proceso de evaluación")
    assert decomposed != "proceso de evaluación"

    outcome = assemble_comment_rows(comment, response("onboarding", decomposed), book)

    assert not outcome.failed, "a quote differing only in Unicode composition must match"
    row = outcome.rows[0]
    assert row["comment_text"][row["quote_start"] : row["quote_end"]] == row["quote"]
    assert row["quote"] == "proceso de evaluación"
