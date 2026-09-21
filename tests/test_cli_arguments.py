"""Tests that meaningless counts are refused before anything is spent.

A negative `--max-themes` used to reach the paid proposal call and come back
reporting more discarded themes than the model returned, and a negative cost
on the confirmation screen. Numbers the tool prints have to be real.
"""

import pytest

from open_feedback_coder.cli import build_parser


@pytest.mark.parametrize(
    "arguments",
    [
        ["propose", "--input", "x.csv", "--text-column", "t", "--max-themes", "0"],
        ["propose", "--input", "x.csv", "--text-column", "t", "--max-themes", "-1"],
        ["propose", "--input", "x.csv", "--text-column", "t", "--max-input-tokens", "0"],
        ["label", "--input", "x.csv", "--text-column", "t", "--concurrency", "0"],
        ["label", "--input", "x.csv", "--text-column", "t", "--concurrency", "-4"],
    ],
)
def test_counts_below_one_are_refused_at_parse_time(arguments):
    with pytest.raises(SystemExit):
        build_parser().parse_args(arguments)


def test_sensible_counts_are_accepted():
    parsed = build_parser().parse_args(
        [
            "propose",
            "--input", "x.csv",
            "--text-column", "t",
            "--max-themes", "8",
            "--max-input-tokens", "120000",
        ]
    )

    assert parsed.max_themes == 8
    assert parsed.max_input_tokens == 120000


def test_counts_are_pluralised_properly():
    """The run summary is the last thing a user reads; it should read well."""
    from open_feedback_coder.phrasing import plural as _plural

    assert _plural(0, "comment") == "0 comments"
    assert _plural(1, "comment") == "1 comment"
    assert _plural(2, "comment") == "2 comments"
    assert _plural(1, "repeated theme assignment") == "1 repeated theme assignment"
    assert _plural(1234, "row") == "1,234 rows"
