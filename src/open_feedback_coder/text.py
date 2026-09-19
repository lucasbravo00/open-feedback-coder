"""Text canonicalisation and verbatim quote location.

This module holds the guarantee the whole tool rests on: a quote returned by
the model is only accepted if it can be located as an exact substring of the
comment it was drawn from.

Two separate transformations are involved, and keeping them apart matters:

`canonicalise` runs once, when a comment is read from the input CSV. It applies
Unicode NFC normalisation and nothing else. The result is the *canonical text*:
it is what gets sent to the model, what gets written to the `comment_text`
column of the output, and what character offsets refer to. Everything
downstream of the CSV reader sees only canonical text.

`fold` is the comparison form used to decide whether a quote matches. It is
deliberately more forgiving than equality, because models reliably reproduce
wording but unreliably reproduce whitespace and typographic punctuation. It
collapses runs of whitespace to a single space and maps curly quotes, dashes
and non-breaking spaces to their ASCII equivalents. Folding is never written to
any output file.

`locate_quote` searches with the folded forms but reports offsets into the
canonical text, so the quote stored in the output CSV is an exact substring of
the `comment_text` stored beside it. That property is checkable with nothing
but the output file, which is the point.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

# Typographic characters folded to an ASCII equivalent before comparison.
# Every entry maps one character to exactly one character, so folding never
# changes the number of characters except where whitespace runs collapse.
_CHARACTER_FOLDS = {
    "‘": "'",  # left single quotation mark
    "’": "'",  # right single quotation mark
    "‚": "'",  # single low-9 quotation mark
    "‛": "'",  # single high-reversed-9 quotation mark
    "′": "'",  # prime
    "´": "'",  # acute accent used as an apostrophe
    "`": "'",  # grave accent used as an apostrophe
    "“": '"',  # left double quotation mark
    "”": '"',  # right double quotation mark
    "„": '"',  # double low-9 quotation mark
    "‟": '"',  # double high-reversed-9 quotation mark
    "″": '"',  # double prime
    "‐": "-",  # hyphen
    "‑": "-",  # non-breaking hyphen
    "‒": "-",  # figure dash
    "–": "-",  # en dash
    "—": "-",  # em dash
    "―": "-",  # horizontal bar
    "−": "-",  # minus sign
}

# Characters treated as whitespace and collapsed into a single space.
_WHITESPACE = frozenset(
    " \t\n\r\f\v         "
    "        　​﻿"
)


def canonicalise(raw: str) -> str:
    """Return the canonical form of text read from an input file.

    Applies Unicode NFC normalisation only. Every character offset produced by
    this package refers to a string in this form.
    """
    return unicodedata.normalize("NFC", raw)


def fold(text: str) -> tuple[str, list[int]]:
    """Return the comparison form of `text` and a per-character index map.

    The returned map has one entry per character of the folded string, holding
    the index in `text` of the character that produced it. Leading and trailing
    whitespace is dropped and internal whitespace runs collapse to one space,
    so the folded string can be shorter than the input but never longer.
    """
    folded: list[str] = []
    index_map: list[int] = []
    pending_space_at: int | None = None

    for position, character in enumerate(text):
        if character in _WHITESPACE:
            # Remember that a gap occurred, but only emit a space once we know
            # a non-space character follows. This drops trailing whitespace
            # without a second pass.
            if folded:
                pending_space_at = position if pending_space_at is None else pending_space_at
            continue

        if pending_space_at is not None:
            folded.append(" ")
            index_map.append(pending_space_at)
            pending_space_at = None

        folded.append(_CHARACTER_FOLDS.get(character, character))
        index_map.append(position)

    return "".join(folded), index_map


def folded(text: str) -> str:
    """Return only the comparison form of `text`, discarding the index map."""
    return fold(text)[0]


@dataclass(frozen=True)
class QuoteMatch:
    """An accepted quote, reported as a span of the canonical comment text."""

    start: int
    end: int
    text: str


def locate_quote(quote: str, comment: str) -> QuoteMatch | None:
    """Locate `quote` inside `comment` and return its canonical span.

    Both arguments must already be canonical text. Returns None when the quote
    is empty once folded, or when it cannot be found. The returned span always
    satisfies `comment[start:end] == match.text`.
    """
    folded_quote, _ = fold(quote)
    if not folded_quote:
        return None

    folded_comment, index_map = fold(comment)
    position = folded_comment.find(folded_quote)
    if position == -1:
        return None

    start = index_map[position]
    end = index_map[position + len(folded_quote) - 1] + 1
    span = comment[start:end]

    # The index map is only as trustworthy as the folding that produced it.
    # Re-folding the span we are about to return costs little and turns a
    # mapping bug into a rejected quote rather than a false guarantee.
    if folded(span) != folded_quote:
        return None

    return QuoteMatch(start=start, end=end, text=span)


def quote_is_verbatim(quote: str, comment: str) -> bool:
    """Return whether `quote` can be located inside `comment`."""
    return locate_quote(quote, comment) is not None
