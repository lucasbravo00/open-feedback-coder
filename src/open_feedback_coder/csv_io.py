"""Reading the input CSV and writing the two output CSVs.

The reader is split in two so that a file on disk and a file uploaded to the
web interface go through exactly the same code. They used to be separate
implementations, which is how the two paths came to disagree about what
counts as a placeholder answer.

Output is written as it is produced rather than at the end, so a run that is
interrupted keeps everything it had already paid for and checked.
"""

from __future__ import annotations

import csv
import io
import os
import sys
import tempfile
from dataclasses import dataclass, field

from .text import canonicalise

# Values that are treated as "no answer" and never sent to the model. The list
# is deliberately short and is documented in the README, because discarding a
# respondent's answer is a decision the tool makes on the user's behalf.
PLACEHOLDER_ANSWERS = frozenset(
    {"n/a", "n.a.", "na", "none", "nil", "null", "-", "--", ".", "?", "blank", "<blank>"}
)

DEFAULT_ENCODING = "utf-8-sig"

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

# `scope` says what was rejected: an entire comment, kept out of the output,
# or a single assignment dropped from a comment that is still in it. One file
# holds both so that everything the checks refused is in one place, and the
# two are told apart by a column rather than by counting.
FAILURE_COLUMNS = [
    "scope",
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


def decode_document(source, encoding: str = DEFAULT_ENCODING) -> str:
    """Return the text of an in-memory document, leaving it usable afterwards.

    Written for the file object a web upload produces, which is handed back
    unchanged on every rerun of the page. Wrapping such an object in an
    io.TextIOWrapper closes the underlying buffer as soon as the wrapper is
    collected, so the next read of the same upload fails; reading the bytes
    once and decoding them does not.
    """
    raw = source.getvalue() if hasattr(source, "getvalue") else source.read()
    if isinstance(raw, str):
        return raw
    return raw.decode(encoding)


def column_names(text: str, delimiter: str = ",") -> list[str]:
    """Return the header of a delimited document, ignoring blank names."""
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    try:
        header = next(reader)
    except StopIteration:
        return []
    return [name for name in header if name]


def read_comments_from_text(
    text: str,
    text_column: str,
    id_column: str | None = None,
    delimiter: str = ",",
    origin: str = "input",
) -> tuple[list[Comment], SkipReport]:
    """Read open-ended answers from the contents of a delimited document.

    `row_number` counts data rows starting at 1, ignoring the header, and is
    also used as the comment id when `id_column` is not given. Skipped rows
    still consume a row number, so a number always points at the same line of
    the original file.
    """
    reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=delimiter)
    if reader.fieldnames is None:
        raise InputError(f"{origin} is empty.")

    available = [name for name in reader.fieldnames if name is not None]
    for wanted, flag in ((text_column, "--text-column"), (id_column, "--id-column")):
        if wanted is not None and wanted not in available:
            raise InputError(
                f"Column {wanted!r} ({flag}) not found in {origin}.\n"
                f"Available columns: {', '.join(repr(name) for name in available)}"
            )

    comments: list[Comment] = []
    skipped = SkipReport()

    for row_number, row in enumerate(reader, start=1):
        value = canonicalise(row.get(text_column) or "").strip()

        if not value:
            skipped.empty += 1
            continue
        if value.lower() in PLACEHOLDER_ANSWERS:
            skipped.placeholder += 1
            key = value.lower()
            skipped.placeholder_values[key] = skipped.placeholder_values.get(key, 0) + 1
            continue

        if id_column is None:
            comment_id = str(row_number)
        else:
            comment_id = canonicalise(row.get(id_column) or "").strip() or str(row_number)

        comments.append(Comment(comment_id=comment_id, row_number=row_number, text=value))

    return comments, skipped


def read_comments(
    path: str,
    text_column: str,
    id_column: str | None = None,
    delimiter: str = ",",
    encoding: str = DEFAULT_ENCODING,
) -> tuple[list[Comment], SkipReport]:
    """Read open-ended answers from a delimited file on disk."""
    try:
        with open(path, newline="", encoding=encoding) as handle:
            text = handle.read()
    except FileNotFoundError as error:
        raise InputError(f"Input file not found: {path}") from error
    except LookupError as error:
        raise InputError(f"Unknown encoding: {encoding}") from error
    except UnicodeDecodeError as error:
        raise InputError(
            f"{path} is not valid {encoding}: {error}. Pass --encoding with the "
            "encoding the file was written in."
        ) from error

    return read_comments_from_text(
        text,
        text_column=text_column,
        id_column=id_column,
        delimiter=delimiter,
        origin=path,
    )


class RowWriter:
    """Append rows to a CSV as they are produced, header first.

    A labelling run makes one paid call per comment, and used to hold every
    result in memory until the last one arrived. A run that died at comment
    900 of 1000 wrote nothing, throwing away everything already paid for.
    Flushing after each batch means whatever got as far as being checked is on
    disk, and the partial file is a valid CSV.
    """

    def __init__(self, path: str | None, columns: list[str]):
        self.path = path
        self.columns = columns
        self._handle = None
        self._writer = None

    def __enter__(self) -> "RowWriter":
        if self.path is None:
            return self
        self._handle = open(self.path, "w", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(
            self._handle, fieldnames=self.columns, extrasaction="ignore"
        )
        self._writer.writeheader()
        self._handle.flush()
        return self

    def write(self, rows) -> None:
        if self._writer is None or not rows:
            return
        self._writer.writerows(rows)
        self._handle.flush()

    def __exit__(self, *exception) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None
            self._writer = None


def same_file(first: str | None, second: str | None) -> bool:
    """Whether two paths name the same file, however they are spelled.

    String equality misses `out.csv` against `./out.csv`, an absolute
    spelling against a relative one, and anything reached through a symlink or
    a `..`. Two writers on one file destroy it, so the comparison has to be
    about the file rather than about the text of the argument.
    """
    if not first or not second:
        return False
    try:
        if os.path.exists(first) and os.path.exists(second):
            return os.path.samefile(first, second)
    except OSError:
        pass
    return os.path.realpath(first) == os.path.realpath(second)


def _stage(path: str, columns: list[str], rows: list[dict]) -> str:
    """Write `rows` to a temporary file beside `path` and return its name."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    handle = tempfile.NamedTemporaryFile(
        "w", newline="", encoding="utf-8", dir=directory, delete=False
    )
    try:
        with handle:
            writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        if os.path.exists(handle.name):
            os.unlink(handle.name)
        raise
    return handle.name


def rewrite_atomically(path: str, columns: list[str], rows: list[dict]) -> None:
    """Replace a file with `rows`, leaving the old one intact until it is whole.

    The review sheet holds marks somebody typed into it. Writing the
    replacement to a temporary file and renaming it means an interruption
    cannot leave a half-written sheet where their work used to be.
    """
    temporary = _stage(path, columns, rows)
    try:
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise


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
