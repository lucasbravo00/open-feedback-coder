"""Tests for what the user is shown before any money is spent.

The estimate is the one screen standing between a corpus and a bill, so the
numbers on it have to be honest about what they are: input tokens measured,
output tokens assumed, cost unknown unless prices were supplied.
"""

import pytest

from open_feedback_coder.budget import BudgetExceeded, Cancelled, Estimate, confirm


def estimate(**overrides) -> Estimate:
    fields = {
        "step": "label 41 comments",
        "calls": 41,
        "input_tokens": 2367,
        "assumed_output_tokens": 2100,
        "output_assumption": "assumes ~140 output tokens per theme",
        "encoding_is_exact": True,
    }
    fields.update(overrides)
    return Estimate(**fields)


def test_without_prices_the_cost_is_unknown_not_zero():
    rendered = estimate().render()

    assert estimate().cost is None
    assert "unknown" in rendered
    assert "--price-in" in rendered
    assert "$0" not in rendered


def test_a_small_run_is_not_reported_as_free():
    """2,367 in and 2,100 out at these prices is $0.0016, not $0.00."""
    rendered = estimate(price_in=0.15, price_out=0.60).render()

    assert "$0.0016" in rendered
    assert "$0.00 " not in rendered


def test_a_large_run_is_reported_in_cents():
    rendered = estimate(
        input_tokens=40_000_000, assumed_output_tokens=2_000_000, price_in=0.15, price_out=0.60
    ).render()

    assert "$7.20" in rendered


def test_the_assumption_behind_the_output_number_is_printed():
    rendered = estimate().render()

    assert "measured" in rendered
    assert "assumed" in rendered
    assert "assumes ~140 output tokens per theme" in rendered


def test_an_inexact_tokeniser_says_so():
    rendered = estimate(encoding_is_exact=False).render()

    assert "no tokeniser is registered for this model" in rendered
    assert "may differ" in rendered


def test_an_exact_tokeniser_stays_quiet():
    assert "tokeniser" not in estimate().render()


def test_going_over_the_input_ceiling_stops_the_run_before_it_starts():
    with pytest.raises(BudgetExceeded) as error:
        from open_feedback_coder.budget import check_input_limit

        check_input_limit(estimate(input_tokens=500_000), max_input_tokens=100_000)

    message = str(error.value)
    assert "500,000" in message
    assert "100,000" in message
    assert "Nothing was sent" in message
    # Sampling is the user's decision, and the tool says so rather than doing it.
    assert "sample" in message


def test_no_ceiling_means_no_check():
    from open_feedback_coder.budget import check_input_limit

    check_input_limit(estimate(input_tokens=10_000_000), max_input_tokens=None)
    check_input_limit(estimate(input_tokens=10_000_000), max_input_tokens=0)


def test_yes_skips_the_prompt(capsys):
    confirm(estimate(), assume_yes=True)

    assert "proceeding (--yes)" in capsys.readouterr().err


def test_without_a_terminal_and_without_yes_nothing_is_sent(monkeypatch):
    monkeypatch.setattr("sys.stdin", type("NoTty", (), {"isatty": lambda self: False})())

    with pytest.raises(Cancelled) as error:
        confirm(estimate(), assume_yes=False)

    assert "Nothing was sent" in str(error.value)
    assert "--yes" in str(error.value)


@pytest.mark.parametrize(
    ("answer", "cancelled"),
    [("y\n", False), ("yes\n", False), ("Y\n", False), ("n\n", True), ("\n", True), ("no\n", True)],
)
def test_the_prompt_defaults_to_not_spending(monkeypatch, answer, cancelled):
    import io

    stream = io.StringIO(answer)
    stream.isatty = lambda: True
    monkeypatch.setattr("sys.stdin", stream)

    if cancelled:
        with pytest.raises(Cancelled):
            confirm(estimate(), assume_yes=False)
    else:
        confirm(estimate(), assume_yes=False)
