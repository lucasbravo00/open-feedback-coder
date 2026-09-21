"""Tests for the quote verifier.

The strings here are constructed to exercise specific failure modes of the
matcher (typographic punctuation, odd whitespace, Unicode composition). They
are test inputs, not survey data, and none of them is presented anywhere as a
real comment.
"""

import random

import pytest

from open_feedback_coder.text import canonicalise, fold, folded, locate_quote, quote_is_verbatim

# Each string exercises something the matcher has to survive.
TRICKY_COMMENTS = [
    "The onboarding was chaotic.",
    "The  onboarding   was\n\nchaotic — nobody told me who my “buddy” was.",
    "Pay is fine; it’s the promotion process that’s broken.",
    "¡El proceso de evaluación no sirve! Y nadie lo revisa.",
    "Manager's great – team's a mess... 50/50, I'd say.",
    "line one\r\nline two\r\nline three",
    "   leading and trailing whitespace   ",
    "Tabs\tand\tnon-breaking\u00a0spaces everywhere.",
    "café conversations are the only real feedback channel",
    "A" * 300 + " and then something specific about workload",
    # Invisible characters that survive a copy-and-paste out of a web form.
    "Pay\u00adrise never happened and co\u200boperation was worse.",
    "Zero\u2060width\ufeff joiners\u200d hide inside words.",
]


@pytest.mark.parametrize("comment", TRICKY_COMMENTS)
def test_fold_index_map_has_one_entry_per_folded_character(comment):
    canonical = canonicalise(comment)
    folded_text, index_map = fold(canonical)

    assert len(folded_text) == len(index_map)
    assert all(0 <= position < len(canonical) for position in index_map)
    assert index_map == sorted(index_map), "index map must be non-decreasing"


@pytest.mark.parametrize("comment", TRICKY_COMMENTS)
def test_any_substring_of_a_comment_is_accepted_and_located_exactly(comment):
    """The central guarantee, checked over many slices of each comment.

    Whatever span is handed back must be a real substring of the comment and
    must fold to the same text as the quote that was asked about. The matcher
    may legitimately return an earlier occurrence of the same wording, so the
    assertion is about equivalence, not about the original offsets.
    """
    canonical = canonicalise(comment)
    rng = random.Random(20260919)

    for _ in range(60):
        start = rng.randrange(0, len(canonical))
        end = rng.randrange(start + 1, len(canonical) + 1)
        candidate = canonical[start:end]

        match = locate_quote(candidate, canonical)

        if not folded(candidate):
            assert match is None, "a quote that folds to nothing must be rejected"
            continue

        assert match is not None, f"failed to locate {candidate!r}"
        assert canonical[match.start : match.end] == match.text
        assert folded(match.text) == folded(candidate)


def test_returned_span_keeps_the_original_typography():
    comment = canonicalise("Nobody told me who my “buddy” was — ever.")

    match = locate_quote('my "buddy" was - ever', comment)

    assert match is not None
    # The model may send ASCII punctuation; what is stored is the source text.
    assert match.text == "my “buddy” was — ever"
    assert comment[match.start : match.end] == match.text


def test_whitespace_differences_are_tolerated():
    comment = canonicalise("The  onboarding   was\n\nchaotic.")

    assert quote_is_verbatim("The onboarding was chaotic.", comment)


def test_non_breaking_space_matches_a_normal_space():
    comment = canonicalise("Tabs\tand\tnon-breaking\u00a0spaces everywhere.")

    assert quote_is_verbatim("non-breaking spaces everywhere.", comment)


def test_decomposed_and_precomposed_accents_match_after_canonicalisation():
    comment = canonicalise("café conversations")  # e + combining acute

    assert quote_is_verbatim("café conversations", comment)


@pytest.mark.parametrize(
    "quote",
    [
        "",
        "   ",
        "\n\t ",
        "the onboarding process was disorganised",  # paraphrase, not a quote
        "onboarding ... chaotic",  # fragments joined with an ellipsis
        "The onboarding was chaotic. The onboarding was chaotic.",  # longer than source
        "ONBOARDING WAS CHAOTIC",  # case changed
    ],
)
def test_quotes_that_are_not_verbatim_are_rejected(quote):
    comment = canonicalise("The onboarding was chaotic.")

    assert locate_quote(quote, comment) is None
    assert not quote_is_verbatim(quote, comment)


def test_matching_is_case_sensitive():
    comment = canonicalise("Pay is fine.")

    assert quote_is_verbatim("Pay is fine.", comment)
    assert not quote_is_verbatim("pay is fine.", comment)
