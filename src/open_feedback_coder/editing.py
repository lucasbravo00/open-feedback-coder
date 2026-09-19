"""Turning a table of hand-edited rows into an approved codebook.

The web interface edits the codebook in a spreadsheet-style table rather than
in a YAML file, but the result has to clear exactly the same bar: the same id
format, the same ban on duplicates, the same refusal of an empty codebook.
This module is the bridge, and it lives in the package rather than in the app
script so that it can be tested without a browser.

Its other job is to survive what a table editor actually hands back. A cell
the user never touched does not arrive as an empty string; depending on the
column type it can be None or a float NaN, and `str(nan)` is the perfectly
valid-looking theme id "nan".
"""

from __future__ import annotations

from .codebook import Codebook, from_dict

THEME_FIELDS = ("id", "label", "description")


def text_cell(value: object) -> str:
    """Return a table cell as trimmed text, treating every blank as empty.

    None, NaN and the strings a table editor uses for a missing value all
    become "". Without this a half-filled row becomes a theme literally named
    "nan", which passes every validation rule there is.
    """
    if value is None:
        return ""
    if isinstance(value, float) and value != value:  # NaN is the only value unequal to itself
        return ""
    text = str(value).strip()
    if text.lower() in {"nan", "none", "<na>", "nat"}:
        return ""
    return text


def codebook_from_records(records, origin: str = "the codebook table") -> Codebook:
    """Validate hand-edited rows and return the codebook they describe.

    Rows left entirely blank are dropped, because adding a row and changing
    your mind is a normal thing to do in a table. A row with some fields
    filled is kept and validated, so a missing id or label is reported rather
    than invented.

    Raises CodebookError, with the same messages `ofc label` produces when it
    loads a YAML codebook.
    """
    rows = []
    for record in records:
        theme = {field: text_cell(record.get(field)) for field in THEME_FIELDS}
        if any(theme.values()):
            rows.append(theme)

    return from_dict({"themes": rows}, origin=origin)
