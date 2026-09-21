"""Tests for retrying the failures that are worth retrying, and only those.

The rule against retrying is about the model's answer: a quote that cannot be
found in its comment is not asked for again, because a second attempt is a
second chance to invent something. A connection that dropped produced no
answer to judge, and treating it as a modelling failure put the comment in the
failures file as though the model had misbehaved.
"""

from __future__ import annotations

import pytest

from open_feedback_coder.label import label_comments
from open_feedback_coder.llm import Client, ModelError, Usage, is_transient


class Transient(Exception):
    """Stands in for an SDK error class, matched by name."""


class RateLimitError(Exception):
    pass


class APIConnectionError(Exception):
    pass


class Status(Exception):
    def __init__(self, status_code):
        super().__init__(f"status {status_code}")
        self.status_code = status_code


@pytest.mark.parametrize(
    "error",
    [
        RateLimitError("slow down"),
        APIConnectionError("connection reset"),
        Status(429),
        Status(500),
        Status(503),
        Status(408),
    ],
)
def test_failures_worth_trying_again_are_recognised(error):
    assert is_transient(error)


@pytest.mark.parametrize(
    "error",
    [
        ValueError("bad argument"),
        Status(400),
        Status(401),
        Status(403),
        Status(404),
        Status(422),
        ModelError("the quote is not in the comment"),
    ],
)
def test_failures_not_worth_trying_again_are_not(error):
    assert not is_transient(error)


class FakeCompletions:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def make_client(outcomes, max_retries=3):
    """A Client with its network and its clock replaced."""
    client = Client.__new__(Client)
    client.model = "stub-model"
    client.max_retries = max_retries
    client.slept = []
    client._sleep = client.slept.append
    client._retries = 0
    import threading

    client._retry_lock = threading.Lock()
    completions = FakeCompletions(outcomes)
    client._client = type("API", (), {"chat": type("Chat", (), {"completions": completions})()})()
    client.completions = completions
    return client


def a_response(content='{"themes": []}'):
    message = type("M", (), {"content": content})()
    choice = type("C", (), {"message": message, "finish_reason": "stop"})()
    usage = type("U", (), {"prompt_tokens": 10, "completion_tokens": 5})()
    return type("R", (), {"choices": [choice], "usage": usage})()


def test_a_transient_failure_is_tried_again_and_counted():
    client = make_client([RateLimitError("slow down"), a_response()])

    parsed, usage = client.complete_json("s", "u", {"type": "object"}, "n")

    assert parsed == {"themes": []}
    assert usage == Usage(input_tokens=10, output_tokens=5)
    assert client.completions.calls == 2
    assert client.retries == 1
    assert len(client.slept) == 1


def test_backoff_grows_between_attempts():
    client = make_client([Status(503), Status(503), Status(503), a_response()])

    client.complete_json("s", "u", {"type": "object"}, "n")

    assert client.completions.calls == 4
    assert client.retries == 3
    assert client.slept == sorted(client.slept), "each wait should be at least the last"
    assert client.slept[0] < client.slept[-1]


def test_retries_run_out_and_the_error_is_reported():
    client = make_client([Status(503)] * 10, max_retries=2)

    with pytest.raises(ModelError) as error:
        client.complete_json("s", "u", {"type": "object"}, "n")

    assert client.completions.calls == 3, "the first attempt plus two retries"
    assert client.retries == 2
    assert "503" in str(error.value)


def test_a_permanent_failure_is_not_tried_again():
    client = make_client([Status(400), a_response()])

    with pytest.raises(ModelError):
        client.complete_json("s", "u", {"type": "object"}, "n")

    assert client.completions.calls == 1
    assert client.retries == 0
    assert client.slept == []


def test_max_retries_zero_means_never():
    client = make_client([Status(429), a_response()], max_retries=0)

    with pytest.raises(ModelError):
        client.complete_json("s", "u", {"type": "object"}, "n")

    assert client.completions.calls == 1


def test_an_unverifiable_quote_is_never_retried(book, make_comment):
    """The rule that must not be softened by any of the above."""
    comment = make_comment("Onboarding dragged on and nobody owned it.")
    calls = []

    class Client_:
        model = "stub-model"
        retries = 0

        def complete_json(self, system, user, schema, schema_name):
            calls.append(user)
            return (
                {
                    "unassigned": False,
                    "assignments": [
                        {
                            "theme_id": "onboarding",
                            "role": "primary",
                            "valence": "negative",
                            "quote": "nobody was put in charge",
                        }
                    ],
                },
                Usage(10, 5),
            )

    run = label_comments([comment], book, Client_(), concurrency=1)

    assert len(calls) == 1, "one call, no second chance to invent a quote"
    assert run.comments_failed == 1
    assert run.failures[0]["failure_reason"] == "quote_not_found_in_comment"


def test_the_run_reports_how_many_calls_were_retried(book, make_comment):
    comment = make_comment("Onboarding dragged on and nobody owned it.")

    class Client_:
        model = "stub-model"
        retries = 7

        def complete_json(self, system, user, schema, schema_name):
            return {"unassigned": True, "assignments": []}, Usage(10, 5)

    run = label_comments([comment], book, Client_(), concurrency=1)

    assert run.retries == 7
