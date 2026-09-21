"""Building a sample to read by hand.

The README tells anyone using these results to read a sample against the
source comments before deciding anything, because the tool is not validated
against human coding. Saying that and then leaving the reader to build the
sample themselves is most of the way to nobody doing it.

This draws the sample and lays it out with the quote beside the comment it
came from, plus an empty column to mark agreement in.

It deliberately stops there. Turning those marks into an agreement rate would
produce a number that looks like validation, computed on a sample the user
chose, against a standard that is one person's reading. Anyone can count their
own ticks; this tool will not do it for them and give the result a name.

What it must not do is lose them. The sample is fixed by seed precisely so
that the same rows come back across several sittings, so re-running has to
carry forward what was already written in.
"""

from __future__ import annotations

import random

REVIEW_COLUMNS = [
    "comment_id",
    "row_number",
    "assignment",
    "theme_id",
    "theme_label",
    "valence",
    "quote",
    "comment_text",
    "in_sample",
    "agree",
    "notes",
]


def sample(rows, size: int, seed: int = 0, include_unassigned: bool = False) -> list[dict]:
    """Draw `size` labelled rows to read, reproducibly.

    The seed is fixed by default so that re-running while working through a
    sample gives the same sample rather than a fresh one.
    """
    candidates = [
        row
        for row in rows
        if include_unassigned or row.get("assignment") != "unassigned"
    ]
    if size >= len(candidates):
        chosen = list(candidates)
    else:
        chosen = random.Random(seed).sample(candidates, size)

    chosen.sort(key=lambda row: (str(row.get("row_number", "")).zfill(12), row.get("theme_label", "")))
    return chosen


def mark_key(row) -> tuple:
    """What identifies one judgement across re-runs.

    A mark is an answer to a specific question: does this quote support this
    theme, with this valence, on this comment? All of that is in the key. Two
    themes may share a label, so the id is here as well as the label; and the
    quote and the valence are here because they are what the reviewer looked
    at. Relabel the corpus and the old answers no longer apply to the new
    codings - they become marks about something that is no longer on the
    sheet, rather than answers transplanted onto a coding nobody saw.
    """
    return (
        str(row.get("comment_id", "")),
        str(row.get("row_number", "")),
        str(row.get("assignment", "")),
        str(row.get("theme_id", "")),
        str(row.get("theme_label", "")),
        str(row.get("valence", "")),
        str(row.get("quote", "")),
    )


def existing_marks(rows) -> dict:
    """Every marked line of a sheet, whole, keyed by what it judged."""
    marks = {}
    for row in rows:
        agree = str(row.get("agree", "") or "").strip()
        notes = str(row.get("notes", "") or "").strip()
        if agree or notes:
            marks[mark_key(row)] = dict(row, agree=agree, notes=notes)
    return marks


def to_review_rows(rows, marks: dict | None = None) -> list[dict]:
    """Lay a sample out for marking, carrying forward any marks already made."""
    marks = marks or {}
    sheet = []
    for row in rows:
        mark = marks.get(mark_key(row), {})
        sheet.append(
            {
                "comment_id": row.get("comment_id", ""),
                "row_number": row.get("row_number", ""),
                "assignment": row.get("assignment", ""),
                "theme_id": row.get("theme_id", ""),
                "theme_label": row.get("theme_label", ""),
                "valence": row.get("valence", ""),
                "quote": row.get("quote", ""),
                "comment_text": row.get("comment_text", ""),
                "in_sample": "yes",
                "agree": mark.get("agree", ""),
                "notes": mark.get("notes", ""),
            }
        )
    return sheet


def orphaned_marks(rows, marks: dict) -> list[dict]:
    """Marked lines from an older sheet that this sample does not contain.

    A smaller sample, a different seed or a relabelled corpus leaves marks
    with no row to sit on. Dropping them would delete work somebody did by
    hand, so they are kept at the end of the sheet exactly as they were
    written, quote and all, marked `in_sample = no` so they are not mistaken
    for part of the current sample, and left there to be read against the new
    coding and deleted deliberately.
    """
    present = {mark_key(row) for row in rows}
    return [
        dict({column: mark.get(column, "") for column in REVIEW_COLUMNS}, in_sample="no")
        for key, mark in marks.items()
        if key not in present
    ]


def render(rows, width: int = 88) -> str:
    """Print the sample so it can be read without opening a spreadsheet."""
    if not rows:
        return "Nothing to review: the labelled file has no rows to sample."

    import textwrap

    blocks = []
    for position, row in enumerate(rows, start=1):
        header = (
            f"[{position}] comment {row.get('comment_id', '')} "
            f"(row {row.get('row_number', '')})"
        )
        body = textwrap.fill(
            row.get("comment_text", ""), width, initial_indent="    ", subsequent_indent="    "
        )
        assignment = row.get("assignment", "")
        theme = row.get("theme_label", "") or "(no theme)"
        valence = row.get("valence", "") or "-"
        quote = row.get("quote", "")
        verdict = f"    {assignment}: {theme}  [{valence}]"
        evidence = (
            textwrap.fill(quote, width - 6, initial_indent="      > ", subsequent_indent="      > ")
            if quote
            else "      (no quote: no theme was assigned)"
        )
        blocks.append("\n".join([header, body, "", verdict, evidence]))

    return "\n\n".join(blocks)
