"""Reading the input CSV and writing the two output CSVs."""

from __future__ import annotations

import csv
import sys
from dataclasses import dataclass, field

from .text import canonicalise

# Values that are treated as "no answer" and never sent to the model. The list
# is deliberately short and is documented in the README, because discarding a
# respondent's answer is a decision the tool makes on the user's behalf.
PLACEHOLDER_ANSWERS = frozenset(
    {"n/a", "n.a.", "na", "none", "nil", "null", "-", "--", ".", "?", "blank", "<blank>"}
)

OUTPUT_COLUMNS = [
    "comment_id",
    "row_number",
    "comment_text",
    "assignment",
    "theme_id",
    "theme_label",
    "valence",
    "quote",
    "quote_start",
    "quote_end",
]

FAILURE_COLUMNS = [
    "comment_id",
    "row_number",
    "comment_text",
    "failure_reason",
    "detail",
    "rejected_theme_id",
    "rejected_quote",
]


class InputError(Exception):
    """Raised when the input file cannot be read as asked."""


@dataclass(frozen=True)
class Comment:
    """One open-ended answer, carrying canonical text."""

    comment_id: str
    row_number: int
    text: str


@dataclass
class SkipReport:
    """Counts of rows dropped before any model call, for reporting."""

    empty: int = 0
    placeholder: int = 0
    placeholder_values: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return self.empty + self.placeholder


def read_comments(
    path: str,
    text_column: str,
    id_column: str | None = None,
    delimiter: str = ",",
    encoding: str = "utf-8-sig",
) -> tuple[list[Comment], SkipReport]:
    """Read open-ended answers from a delimited text file.

    `row_number` counts data rows starting at 1, ignoring the header, and is
    also used as the comment id when `id_column` is not given.
    """
    try:
        handle = open(path, newline="", encoding=encoding)
    except FileNotFoundError as error:
        raise InputError(f"Input file not found: {path}") from error
    except LookupError as error:
        raise InputError(f"Unknown encoding: {encoding}") from error

    comments: list[Comment] = []
    skipped = SkipReport()

    with handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        if reader.fieldnames is None:
            raise InputError(f"Input file is empty: {path}")

        available = [name for name in reader.fieldnames if name is not None]
        if text_column not in available:
            raise InputError(
                f"Column {text_column!r} not found in {path}.\n"
                f"Available columns: {', '.join(repr(name) for name in available)}"
            )
        if id_column is not None and id_column not in available:
            raise InputError(
                f"Column {id_column!r} not found in {path}.\n"
                f"Available columns: {', '.join(repr(name) for name in available)}"
            )

        for row_number, row in enumerate(reader, start=1):
            raw = row.get(text_column) or ""
            text = canonicalise(raw).strip()

            if not text:
                skipped.empty += 1
                continue
            if text.strip().lower() in PLACEHOLDER_ANSWERS:
                skipped.placeholder += 1
                key = text.strip().lower()
                skipped.placeholder_values[key] = skipped.placeholder_values.get(key, 0) + 1
                continue

            if id_column is None:
                comment_id = str(row_number)
            else:
                comment_id = canonicalise(row.get(id_column) or "").strip() or str(row_number)

            comments.append(Comment(comment_id=comment_id, row_number=row_number, text=text))

    return comments, skipped


def write_rows(path: str | None, columns: list[str], rows: list[dict]) -> None:
    """Write `rows` as a CSV with `columns`, or to stdout when path is None."""
    handle = sys.stdout if path is None else open(path, "w", newline="", encoding="utf-8")
    try:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    finally:
        if handle is not sys.stdout:
            handle.close()
