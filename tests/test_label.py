"""Tests for the checks applied to every label before it reaches the output.

`assemble_comment_rows` is pure, so these run without an API key and without
any network access. The model's response is supplied directly, which is how
the failure paths can be exercised at all.
"""

import pytest

from open_feedback_coder.label import assemble_comment_rows
from open_feedback_coder.text import canonicalise

COMMENT_TEXT = (
    "Onboarding took three weeks longer than promised — nobody owned it. "
    "The pay is genuinely competitive though, and my manager shielded me from "
    "the worst of it."
)


def assignment(theme_id, role, valence, quote):
    return {"theme_id": theme_id, "role": role, "valence": valence, "quote": quote}


def response(*assignments, unassigned=False):
    return {"unassigned": unassigned, "assignments": list(assignments)}


def test_every_output_quote_is_an_exact_substring_of_its_comment_text(book, make_comment):
    """The guarantee the README makes, checked on the rows that get written."""
    comment = make_comment(COMMENT_TEXT)
    outcome = assemble_comment_rows(
        comment,
        response(
            assignment("onboarding", "primary", "negative", "nobody owned it"),
            assignment("pay", "secondary", "positive", "pay is genuinely competitive"),
            assignment("management", "secondary", "positive", "my manager shielded me"),
        ),
        book,
    )

    assert not outcome.failed
    assert len(outcome.rows) == 3

    for row in outcome.rows:
        start, end = int(row["quote_start"]), int(row["quote_end"])
        assert row["comment_text"][start:end] == row["quote"]
        assert row["quote"], "a label must never be written without a quote"


def test_no_labelled_row_can_exist_without_a_verified_quote(book, make_comment):
    """Swept over several responses at once, including the unassigned case."""
    comment = make_comment(COMMENT_TEXT)
    responses = [
        response(assignment("onboarding", "primary", "negative", "nobody owned it")),
        response(
            assignment("pay", "primary", "positive", "The pay is genuinely competitive"),
            assignment("onboarding", "secondary", "negative", "Onboarding took three weeks"),
        ),
        response(unassigned=True),
    ]

    rows = []
    for model_response in responses:
        outcome = assemble_comment_rows(comment, model_response, book)
        assert not outcome.failed
        rows.extend(outcome.rows)

    for row in rows:
        if row["assignment"] == "unassigned":
            assert row["quote"] == ""
            assert row["theme_id"] == ""
            continue
        start, end = int(row["quote_start"]), int(row["quote_end"])
        assert row["comment_text"][start:end] == row["quote"] != ""


def test_a_comment_with_one_unlocatable_quote_is_excluded_entirely(book, make_comment):
    """One bad quote takes the whole comment out, not just its own label."""
    comment = make_comment(COMMENT_TEXT)
    outcome = assemble_comment_rows(
        comment,
        response(
            assignment("onboarding", "primary", "negative", "nobody owned it"),
            # Faithful in meaning, absent from the text. That is the case this
            # whole project exists to catch.
            assignment("pay", "secondary", "positive", "the salary is competitive"),
        ),
        book,
    )

    assert outcome.rows == []
    assert outcome.failed
    assert outcome.failure["failure_reason"] == "quote_not_found_in_comment"
    assert outcome.failure["rejected_theme_id"] == "pay"
    assert outcome.failure["rejected_quote"] == "the salary is competitive"
    assert outcome.failure["comment_id"] == comment.comment_id


def test_quote_from_a_different_comment_is_rejected(book, make_comment):
    comment = make_comment(COMMENT_TEXT)
    outcome = assemble_comment_rows(
        comment,
        response(assignment("workload", "primary", "negative", "I work every weekend")),
        book,
    )

    assert outcome.failed
    assert outcome.failure["failure_reason"] == "quote_not_found_in_comment"


def test_an_unassigned_comment_produces_one_row_with_no_theme(book, make_comment):
    comment = make_comment("Thanks for asking.")
    outcome = assemble_comment_rows(comment, response(unassigned=True), book)

    assert not outcome.failed
    assert len(outcome.rows) == 1
    row = outcome.rows[0]
    assert row["assignment"] == "unassigned"
    assert row["theme_id"] == ""
    assert row["theme_label"] == ""
    assert row["valence"] == ""
    assert row["quote"] == ""
    assert row["quote_start"] == ""


def test_an_empty_assignment_list_is_treated_as_unassigned(book, make_comment):
    comment = make_comment("Thanks for asking.")
    outcome = assemble_comment_rows(comment, response(), book)

    assert not outcome.failed
    assert outcome.rows[0]["assignment"] == "unassigned"


def test_primary_row_comes_first(book, make_comment):
    comment = make_comment(COMMENT_TEXT)
    outcome = assemble_comment_rows(
        comment,
        response(
            assignment("pay", "secondary", "positive", "pay is genuinely competitive"),
            assignment("onboarding", "primary", "negative", "nobody owned it"),
        ),
        book,
    )

    assert [row["assignment"] for row in outcome.rows] == ["primary", "secondary"]
    assert outcome.rows[0]["theme_id"] == "onboarding"


@pytest.mark.parametrize(
    ("assignments", "expected_reason"),
    [
        (
            [assignment("hybrid_work", "primary", "negative", "nobody owned it")],
            "unknown_theme_id",
        ),
        (
            [assignment("onboarding", "secondary", "negative", "nobody owned it")],
            "no_primary_theme",
        ),
        (
            [
                assignment("onboarding", "primary", "negative", "nobody owned it"),
                assignment("pay", "primary", "positive", "pay is genuinely competitive"),
            ],
            "multiple_primary_themes",
        ),
        (
            [
                assignment("onboarding", "primary", "negative", "nobody owned it"),
                assignment("pay", "secondary", "positive", "pay is genuinely"),
                assignment("management", "secondary", "positive", "my manager shielded me"),
                assignment("workload", "secondary", "negative", "the worst of it"),
            ],
            "too_many_secondary_themes",
        ),
        (
            [
                assignment("onboarding", "primary", "negative", "nobody owned it"),
                assignment("onboarding", "secondary", "negative", "three weeks longer"),
            ],
            "duplicate_theme",
        ),
        (
            [assignment("onboarding", "primary", "mixed", "nobody owned it")],
            "invalid_valence",
        ),
    ],
)
def test_malformed_responses_exclude_the_comment(book, make_comment, assignments, expected_reason):
    comment = make_comment(COMMENT_TEXT)
    outcome = assemble_comment_rows(comment, response(*assignments), book)

    assert outcome.rows == []
    assert outcome.failure["failure_reason"] == expected_reason


def test_failure_rows_keep_the_full_comment_text(book, make_comment):
    comment = make_comment(COMMENT_TEXT, comment_id="R42", row_number=42)
    outcome = assemble_comment_rows(
        comment,
        response(assignment("pay", "primary", "positive", "the salary is competitive")),
        book,
    )

    assert outcome.failure["comment_text"] == canonicalise(COMMENT_TEXT)
    assert outcome.failure["comment_id"] == "R42"
    assert outcome.failure["row_number"] == 42
