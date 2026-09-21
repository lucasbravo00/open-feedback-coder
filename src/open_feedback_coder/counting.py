"""Counting the labelled rows.

Arithmetic, in code, over a file that already exists. Nothing here asks a
model anything, and nothing here needs an API key: `ofc counts` runs on a CSV
somebody else produced, or on one produced months ago.

What these numbers are is worth being precise about. They count labels, not
people and not opinions. "18 comments name pay" means the model assigned that
theme to eighteen comments and a quote was found in each; it does not mean
eighteen people are unhappy about pay, and it does not mean the eighteen are
the right eighteen.
"""

from __future__ import annotations

from dataclasses import dataclass

from .prompts import VALENCES

COUNT_COLUMNS = [
    "theme_id",
    "theme_label",
    "comments",
    "as_primary",
    "as_secondary",
    "positive",
    "negative",
    "neutral",
]


@dataclass(frozen=True)
class ThemeCount:
    """How often one theme was assigned, and with what valence."""

    theme_id: str
    theme_label: str
    comments: int
    as_primary: int
    as_secondary: int
    valences: dict[str, int]

    def as_row(self) -> dict:
        row = {
            "theme_id": self.theme_id,
            "theme_label": self.theme_label,
            "comments": self.comments,
            "as_primary": self.as_primary,
            "as_secondary": self.as_secondary,
        }
        row.update({valence: self.valences.get(valence, 0) for valence in VALENCES})
        return row


@dataclass(frozen=True)
class Counts:
    """Everything countable in a labelled file."""

    themes: list[ThemeCount]
    comments: int
    comments_labelled: int
    comments_unassigned: int
    rows: int

    def as_rows(self) -> list[dict]:
        return [theme.as_row() for theme in self.themes]

    def render(self) -> str:
        """A table for the terminal, widest column first."""
        if not self.themes:
            return "No themes were assigned in this file."

        label_width = max(len(theme.theme_label) for theme in self.themes)
        label_width = max(label_width, len("theme"))

        lines = [
            f"{'theme':<{label_width}}  {'comments':>8}  {'primary':>7}  "
            f"{'second.':>7}  {'pos':>4}  {'neg':>4}  {'neu':>4}"
        ]
        for theme in self.themes:
            lines.append(
                f"{theme.theme_label:<{label_width}}  {theme.comments:>8,}  "
                f"{theme.as_primary:>7,}  {theme.as_secondary:>7,}  "
                f"{theme.valences.get('positive', 0):>4,}  "
                f"{theme.valences.get('negative', 0):>4,}  "
                f"{theme.valences.get('neutral', 0):>4,}"
            )

        lines.append("")
        lines.append(
            f"{self.comments:,} comments: {self.comments_labelled:,} with at least one "
            f"theme, {self.comments_unassigned:,} with none. {self.rows:,} rows."
        )
        lines.append(
            "A comment can carry up to three themes, so the theme column sums to "
            "more than the number of comments."
        )
        return "\n".join(lines)


def count(rows) -> Counts:
    """Count a labelled file's rows. Pure arithmetic over what is there."""
    rows = list(rows)
    tallies: dict[str, dict] = {}
    order: list[str] = []
    comments: set[str] = set()
    unassigned: set[str] = set()
    labelled: set[str] = set()

    for row in rows:
        key = (row.get("comment_id") or "", row.get("row_number") or "")
        comments.add(key)

        if row.get("assignment") == "unassigned":
            unassigned.add(key)
            continue

        labelled.add(key)
        theme_id = row.get("theme_id") or ""
        if theme_id not in tallies:
            tallies[theme_id] = {
                "label": row.get("theme_label") or theme_id,
                "comments": 0,
                "as_primary": 0,
                "as_secondary": 0,
                "valences": {},
            }
            order.append(theme_id)

        tally = tallies[theme_id]
        tally["comments"] += 1
        if row.get("assignment") == "primary":
            tally["as_primary"] += 1
        else:
            tally["as_secondary"] += 1

        valence = row.get("valence") or ""
        if valence:
            tally["valences"][valence] = tally["valences"].get(valence, 0) + 1

    themes = [
        ThemeCount(
            theme_id=theme_id,
            theme_label=tallies[theme_id]["label"],
            comments=tallies[theme_id]["comments"],
            as_primary=tallies[theme_id]["as_primary"],
            as_secondary=tallies[theme_id]["as_secondary"],
            valences=tallies[theme_id]["valences"],
        )
        for theme_id in order
    ]
    # Most-assigned first, and alphabetical within a tie so the order is stable.
    themes.sort(key=lambda theme: (-theme.comments, -theme.as_primary, theme.theme_label))

    return Counts(
        themes=themes,
        comments=len(comments),
        comments_labelled=len(labelled),
        comments_unassigned=len(unassigned - labelled),
        rows=len(rows),
    )
