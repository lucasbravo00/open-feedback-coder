"""Tests that one bad response cannot take down a labelling run.

Every comment is a separate paid call. If a single malformed response could
raise out of the worker, `label_comments` would return nothing, `ofc label`
would write no files at all, and every other comment in the run would be lost
along with the money already spent on it.
"""

import pytest

from open_feedback_coder.label import assemble_comment_rows, label_comments
from open_feedback_coder.llm import ModelError, Usage

GOOD_RESPONSE = {
    "unassigned": False,
    "assignments": [
        {
            "theme_id": "onboarding",
            "role": "primary",
            "valence": "negative",
            "quote": "nobody owned it",
        }
    ],
}


class ScriptedClient:
    """Replays a response, or raises, per comment id."""

    def __init__(self, script):
        self.model = "stub-model"
        self.script = script

    def complete_json(self, system, user, schema, schema_name):
        comment_text = user.split("Comment:\n", 1)[1]
        outcome = self.script[comment_text]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome, Usage(input_tokens=10, output_tokens=5)


@pytest.fixture
def three_comments(make_comment):
    return [
        make_comment("Onboarding dragged on and nobody owned it.", "1", 1),
        make_comment("Second comment, nobody owned it either.", "2", 2),
        make_comment("Third comment, nobody owned it as well.", "3", 3),
    ]


def test_an_unexpected_exception_fails_only_its_own_comment(book, three_comments):
    """A TypeError from the client must not escape and abort the run."""
    client = ScriptedClient(
        {
            three_comments[0].text: GOOD_RESPONSE,
            three_comments[1].text: TypeError("something in the SDK broke"),
            three_comments[2].text: GOOD_RESPONSE,
        }
    )

    run = label_comments(three_comments, book, client, concurrency=2)

    assert run.comments_labelled == 2
    assert run.comments_failed == 1
    assert [row["comment_id"] for row in run.rows] == ["1", "3"]
    assert run.failures[0]["comment_id"] == "2"
    assert run.failures[0]["failure_reason"] == "model_error"
    assert "TypeError" in run.failures[0]["detail"]


def test_a_response_that_is_not_an_object_fails_only_its_own_comment(book, three_comments):
    client = ScriptedClient(
        {
            three_comments[0].text: GOOD_RESPONSE,
            three_comments[1].text: ["not", "an", "object"],
            three_comments[2].text: GOOD_RESPONSE,
        }
    )

    run = label_comments(three_comments, book, client, concurrency=1)

    assert run.comments_labelled == 2
    assert run.failures[0]["comment_id"] == "2"
    assert run.failures[0]["failure_reason"] == "malformed_response"


def test_tokens_from_a_billed_but_rejected_call_are_still_reported(book, three_comments):
    """A response that arrived and was refused was paid for all the same."""
    client = ScriptedClient(
        {
            three_comments[0].text: GOOD_RESPONSE,
            three_comments[1].text: ModelError(
                "the model returned text that is not JSON", Usage(input_tokens=40, output_tokens=7)
            ),
            three_comments[2].text: GOOD_RESPONSE,
        }
    )

    run = label_comments(three_comments, book, client, concurrency=1)

    assert run.comments_failed == 1
    assert run.usage == Usage(input_tokens=10 + 40 + 10, output_tokens=5 + 7 + 5)


def test_every_comment_lands_in_exactly_one_of_the_two_outputs(book, three_comments):
    client = ScriptedClient(
        {
            three_comments[0].text: GOOD_RESPONSE,
            three_comments[1].text: RuntimeError("boom"),
            three_comments[2].text: {"unassigned": True, "assignments": []},
        }
    )

    run = label_comments(three_comments, book, client, concurrency=3)

    in_output = {row["comment_id"] for row in run.rows}
    in_failures = {failure["comment_id"] for failure in run.failures}

    assert in_output | in_failures == {"1", "2", "3"}
    assert in_output & in_failures == set()
    assert run.comments_labelled + run.comments_unassigned + run.comments_failed == 3


def test_output_keeps_the_input_order_even_when_a_comment_fails(book, three_comments):
    client = ScriptedClient(
        {
            three_comments[0].text: GOOD_RESPONSE,
            three_comments[1].text: RuntimeError("boom"),
            three_comments[2].text: GOOD_RESPONSE,
        }
    )

    run = label_comments(three_comments, book, client, concurrency=3)

    assert [row["row_number"] for row in run.rows] == [1, 3]


@pytest.mark.parametrize(
    "assignments",
    ["not a list", {"theme_id": "onboarding"}, ["a string instead of an object"], [None]],
)
def test_assignments_in_an_unreadable_shape_fail_the_comment(book, make_comment, assignments):
    comment = make_comment("Onboarding dragged on and nobody owned it.")

    outcome = assemble_comment_rows(
        comment, {"unassigned": False, "assignments": assignments}, book
    )

    assert outcome.rows == []
    assert outcome.failure["failure_reason"] == "malformed_response"
