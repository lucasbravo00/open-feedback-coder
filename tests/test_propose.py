"""Tests for turning a proposal response into an editable codebook.

The example quotes in a proposed codebook go through the same verifier as
labels do, so a theme cannot arrive illustrated by a quote that nobody wrote.
"""

import pytest

from open_feedback_coder.prompts import PROPOSE_SYSTEM, render_corpus
from open_feedback_coder.propose import build_codebook, resolve_comment, slugify


@pytest.fixture
def corpus(make_comment):
    return [
        make_comment("Onboarding took three weeks longer than promised.", "1", 1),
        make_comment("Nobody owned my onboarding at all.", "2", 2),
        make_comment("The pay is genuinely competitive.", "3", 3),
    ]


def theme(theme_id, label, examples):
    return {
        "id": theme_id,
        "label": label,
        "description": "",
        "examples": [{"comment_id": cid, "quote": quote} for cid, quote in examples],
    }


def test_verified_examples_are_kept(corpus):
    result = build_codebook(
        {
            "themes": [
                theme(
                    "onboarding",
                    "Onboarding",
                    [("1", "three weeks longer than promised"), ("2", "Nobody owned my onboarding")],
                )
            ]
        },
        corpus,
        max_themes=15,
    )

    assert result.rejected_examples == []
    assert [example.quote for example in result.codebook.themes[0].examples] == [
        "three weeks longer than promised",
        "Nobody owned my onboarding",
    ]


def test_an_invented_example_quote_is_dropped_but_the_theme_survives(corpus):
    result = build_codebook(
        {
            "themes": [
                theme(
                    "onboarding",
                    "Onboarding",
                    [("1", "three weeks longer than promised"), ("2", "onboarding was a shambles")],
                )
            ]
        },
        corpus,
        max_themes=15,
    )

    assert len(result.codebook.themes) == 1
    assert len(result.codebook.themes[0].examples) == 1
    assert result.rejected_examples[0]["reason"] == "quote_not_found_in_comment"
    assert result.rejected_examples[0]["quote"] == "onboarding was a shambles"


def test_an_example_attributed_to_the_wrong_comment_is_dropped(corpus):
    result = build_codebook(
        {"themes": [theme("pay", "Pay", [("1", "The pay is genuinely competitive")])]},
        corpus,
        max_themes=15,
    )

    assert result.codebook.themes[0].examples == []
    assert result.rejected_examples[0]["reason"] == "quote_not_found_in_comment"


def test_an_example_pointing_at_a_comment_that_does_not_exist_is_dropped(corpus):
    result = build_codebook(
        {"themes": [theme("pay", "Pay", [("999", "The pay is genuinely competitive")])]},
        corpus,
        max_themes=15,
    )

    assert result.rejected_examples[0]["reason"] == "unknown_comment_id"


def test_stored_example_keeps_the_source_typography(make_comment):
    corpus = [make_comment("Nobody owned it — ever.", "1", 1)]

    result = build_codebook(
        {"themes": [theme("ownership", "Ownership", [("1", "Nobody owned it - ever.")])]},
        corpus,
        max_themes=15,
    )

    assert result.codebook.themes[0].examples[0].quote == "Nobody owned it — ever."


def test_themes_beyond_the_limit_are_discarded_and_counted(corpus):
    response = {"themes": [theme(f"t{n}", f"Theme {n}", []) for n in range(5)]}

    result = build_codebook(response, corpus, max_themes=3)

    assert len(result.codebook.themes) == 3
    assert result.themes_discarded_over_limit == 2


def test_ids_the_loader_would_reject_are_repaired(corpus):
    result = build_codebook(
        {"themes": [theme("Onboarding & Ramp-Up", "Onboarding and ramp-up", [])]},
        corpus,
        max_themes=15,
    )

    assert result.codebook.themes[0].id == "onboarding_ramp_up"
    assert result.codebook.themes[0].label == "Onboarding and ramp-up"


def test_duplicate_ids_are_made_unique(corpus):
    result = build_codebook(
        {"themes": [theme("pay", "Pay", []), theme("pay", "Pay again", [])]},
        corpus,
        max_themes=15,
    )

    ids = [t.id for t in result.codebook.themes]
    assert len(set(ids)) == 2


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Onboarding", "onboarding"),
        ("Pay & Benefits", "pay_benefits"),
        ("  spaced  out  ", "spaced_out"),
        ("---", "fallback"),
        ("", "fallback"),
    ],
)
def test_slugify(raw, expected):
    assert slugify(raw, fallback="fallback") == expected


# The first real call to the API rejected all 26 example quotes. Not one was a
# paraphrase: the corpus was rendered as "[7] text", so the model answered with
# comment_id "[7]", and every lookup missed. These pin the fix.


def test_the_corpus_puts_each_id_on_its_own_delimiter_line(make_comment):
    rendered = render_corpus([make_comment("First one.", "7", 7)])

    assert rendered == "<comment 7>\nFirst one.\n</comment>"
    assert not rendered.startswith("[")


def test_the_prompt_says_what_the_id_is_without_its_delimiters():
    instructions = PROPOSE_SYSTEM.format(max_themes=15)

    assert "<comment ID>" in instructions
    assert "the id is 7" in instructions


@pytest.mark.parametrize(
    ("returned", "expected"),
    [
        ("7", "7"),
        ("[7]", "7"),
        ("<7>", "7"),
        ("<comment 7>", "7"),
        ("comment 7", "7"),
        ("#7", "7"),
        (" 7 ", "7"),
        ('"7"', "7"),
        ("7.", "7"),
        ("R-900", "R-900"),
        ("99", None),
        ("", None),
    ],
)
def test_a_comment_id_is_resolved_through_whatever_punctuation_came_with_it(
    make_comment, returned, expected
):
    by_id = {
        "7": make_comment("seventh", "7", 7),
        "R-900": make_comment("nine hundredth", "R-900", 8),
    }

    comment = resolve_comment(by_id, returned)

    assert (comment.comment_id if comment else None) == expected


def test_an_id_that_really_contains_brackets_resolves_to_its_own_comment(make_comment):
    """The literal value is tried first, so repair cannot hijack a real id."""
    by_id = {
        "[9]": make_comment("the bracketed one", "[9]", 9),
        "9": make_comment("the plain one", "9", 10),
    }

    assert resolve_comment(by_id, "[9]").text == "the bracketed one"
    assert resolve_comment(by_id, "9").text == "the plain one"


def test_an_example_whose_id_came_back_wrapped_is_kept(corpus):
    """The exact shape of the bug: a verbatim quote under a bracketed id."""
    result = build_codebook(
        {
            "themes": [
                {
                    "id": "onboarding",
                    "label": "Onboarding",
                    "description": "",
                    "examples": [
                        {"comment_id": "[1]", "quote": "three weeks longer than promised"}
                    ],
                }
            ]
        },
        corpus,
        max_themes=15,
    )

    assert result.rejected_examples == []
    example = result.codebook.themes[0].examples[0]
    assert example.quote == "three weeks longer than promised"
    # The id stored is the corpus's own, not the decorated one the model sent.
    assert example.comment_id == "1"


def test_a_wrapped_id_that_matches_nothing_is_still_rejected(corpus):
    result = build_codebook(
        {
            "themes": [
                {
                    "id": "onboarding",
                    "label": "Onboarding",
                    "description": "",
                    "examples": [{"comment_id": "[404]", "quote": "Nobody owned my onboarding"}],
                }
            ]
        },
        corpus,
        max_themes=15,
    )

    assert result.codebook.themes[0].examples == []
    assert result.rejected_examples[0]["reason"] == "unknown_comment_id"
