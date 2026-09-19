"""The verifier, exercised against real survey answers rather than examples.

The text here was written by other people answering a real survey, and was
de-identified by that survey's authors. It has the properties invented test
strings tend not to have: non-breaking spaces in mid-sentence, a curly
apostrophe, bracketed redactions, respondents numbering their own points, and
answers running past a thousand characters. See tests/fixtures/SOURCE.md.

The same checks run over the whole converted corpus when it is present, which
it is only after scripts/download_dataset.py has been run.
"""

from __future__ import annotations

import os
import random
from pathlib import Path

import pytest

from open_feedback_coder.csv_io import read_comments
from open_feedback_coder.label import assemble_comment_rows
from open_feedback_coder.text import locate_quote

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "ospo_q12_sample.csv"
FULL_CORPUS = Path(__file__).resolve().parents[1] / "data" / "ospo_open_responses.csv"


def load(path: Path):
    comments, _ = read_comments(
        str(path), text_column="response", id_column="response_id"
    )
    return comments


@pytest.fixture(scope="module")
def corpus():
    return load(FIXTURE)


def as_a_model_would_retype(text: str) -> str:
    """Flatten the typography a model reliably fails to reproduce.

    Written out longhand rather than reusing the package's own folding, so
    that this test does not check the implementation against itself.
    """
    for original, flattened in (
        ("‘", "'"),
        ("’", "'"),
        ("“", '"'),
        ("”", '"'),
        ("–", "-"),
        ("—", "-"),
        ("\u00a0", " "),
    ):
        text = text.replace(original, flattened)
    return " ".join(text.split())


def word_spans(text: str, rng: random.Random, count: int, minimum_words: int = 6):
    """Yield (start, end) spans that begin and end on a word boundary.

    Trimmed at both ends. A span ending in whitespace is not something a model
    would ever send back, and the verifier reports the tightest match, so an
    untrimmed span would fail a comparison for a reason that says nothing
    about the verifier.
    """
    starts = [0] + [i + 1 for i, character in enumerate(text) if character.isspace()]
    starts = [s for s in starts if s < len(text) and not text[s].isspace()]
    if len(starts) <= minimum_words:
        return

    for _ in range(count):
        first = rng.randrange(0, len(starts) - minimum_words)
        last = rng.randrange(first + minimum_words, len(starts))
        start = starts[first]
        end = starts[last]
        while end > start and text[end - 1].isspace():
            end -= 1
        if end > start:
            yield start, end


def test_the_fixture_is_the_ten_real_answers(corpus):
    assert len(corpus) == 10
    assert [comment.comment_id for comment in corpus] == [
        "84", "85", "87", "88", "89", "94", "99", "116", "118", "123",
    ]
    assert max(len(comment.text) for comment in corpus) > 1000


def test_real_text_carries_the_characters_that_break_naive_matching(corpus):
    joined = "".join(comment.text for comment in corpus)
    assert "\u00a0" in joined, "expected a non-breaking space in the real answers"
    assert "’" in joined, "expected a curly apostrophe in the real answers"
    assert "[" in joined, "expected a bracketed redaction in the real answers"


def test_any_real_span_is_located_exactly(corpus):
    """Quote a real answer back at the verifier; it must find it, untouched."""
    rng = random.Random(20260919)

    checked = 0
    for comment in corpus:
        for start, end in word_spans(comment.text, rng, count=40):
            span = comment.text[start:end]

            match = locate_quote(span, comment.text)

            assert match is not None, f"could not locate {span[:60]!r}"
            assert comment.text[match.start : match.end] == match.text
            checked += 1

    assert checked > 200, f"only {checked} spans were checked"


def test_a_span_retyped_the_way_a_model_would_still_matches_the_original(corpus):
    """The tolerance that matters, measured on text nobody wrote for this test."""
    rng = random.Random(1789)

    flattened_at_least_once = 0
    for comment in corpus:
        for start, end in word_spans(comment.text, rng, count=25):
            span = comment.text[start:end]
            retyped = as_a_model_would_retype(span)

            match = locate_quote(retyped, comment.text)

            assert match is not None, f"rejected a faithful quote: {retyped[:60]!r}"
            # What gets stored is the source text, not the model's rendering.
            assert match.text == span
            if retyped != span:
                flattened_at_least_once += 1

    assert flattened_at_least_once > 0, "no span actually differed after retyping"


def test_dropping_a_word_from_a_real_span_is_rejected(corpus):
    """The commonest way a quote stops being a quote."""
    rng = random.Random(4242)

    tested = 0
    for comment in corpus:
        for start, end in word_spans(comment.text, rng, count=15, minimum_words=8):
            words = comment.text[start:end].split()
            if len(words) < 8:
                continue
            middle = len(words) // 2
            without = " ".join(words[:middle] + words[middle + 1 :])

            assert locate_quote(without, comment.text) is None, (
                f"accepted a quote with a word removed: {without[:60]!r}"
            )
            tested += 1

    assert tested > 50, f"only {tested} mutilated spans were checked"


def test_a_span_from_one_answer_is_not_found_in_another(corpus):
    rng = random.Random(31337)

    tested = 0
    for source in corpus:
        for start, end in word_spans(source.text, rng, count=6, minimum_words=8):
            span = source.text[start:end]
            for other in corpus:
                if other.comment_id == source.comment_id:
                    continue
                assert locate_quote(span, other.text) is None
                tested += 1

    assert tested > 100


def test_labelling_a_real_answer_stores_the_source_span(corpus, book):
    """End to end on real text: accept the model's typography, store the file's."""
    comment = next(c for c in corpus if "\u00a0" in c.text)
    start = comment.text.index("\u00a0") - 20
    end = start + 60
    while end > start and comment.text[end - 1].isspace():
        end -= 1
    span = comment.text[start:end]
    retyped = as_a_model_would_retype(span)
    assert retyped != span

    outcome = assemble_comment_rows(
        comment,
        {
            "unassigned": False,
            "assignments": [
                {
                    "theme_id": "workload",
                    "role": "primary",
                    "valence": "negative",
                    "quote": retyped,
                }
            ],
        },
        book,
    )

    assert not outcome.failed
    row = outcome.rows[0]
    assert row["quote"] == span
    assert row["quote"] != retyped
    assert row["comment_text"][row["quote_start"] : row["quote_end"]] == row["quote"]


@pytest.mark.skipif(
    not FULL_CORPUS.exists(),
    reason="run scripts/download_dataset.py to check the whole corpus",
)
def test_every_answer_in_the_whole_corpus_behaves():
    """All 300-odd answers, not just the ten kept in the repository."""
    comments = load(FULL_CORPUS)
    assert len(comments) > 250

    rng = random.Random(90210)
    checked = 0
    for comment in comments:
        for start, end in word_spans(comment.text, rng, count=3, minimum_words=4):
            span = comment.text[start:end]
            match = locate_quote(as_a_model_would_retype(span), comment.text)
            assert match is not None, f"{comment.comment_id}: {span[:50]!r}"
            assert comment.text[match.start : match.end] == match.text
            checked += 1

    assert checked > 100


def test_the_fixture_is_committed_but_the_full_corpus_is_not():
    """The repository ships no survey data beyond the ten quoted answers."""
    assert FIXTURE.exists()
    gitignore = (Path(__file__).resolve().parents[1] / ".gitignore").read_text()
    assert "data/" in gitignore
    assert os.path.relpath(FULL_CORPUS, Path(__file__).resolve().parents[1]).startswith("data")
