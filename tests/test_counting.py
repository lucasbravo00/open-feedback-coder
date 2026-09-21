"""Tests for counting the labelled rows.

Counting is the one thing this project insists must never be asked of a
model, so the arithmetic had better be right. It also has to be right about
what it is counting: a comment can carry three themes, so the theme column
does not sum to the number of comments, and saying otherwise would be exactly
the kind of number nobody can defend.
"""

from __future__ import annotations

import csv

from open_feedback_coder.counting import COUNT_COLUMNS, count


def row(comment_id, assignment, theme_id="", theme_label="", valence="", row_number=None):
    return {
        "comment_id": comment_id,
        "row_number": str(row_number if row_number is not None else comment_id),
        "comment_text": "some comment",
        "assignment": assignment,
        "theme_id": theme_id,
        "theme_label": theme_label,
        "valence": valence,
        "quote": "a quote" if assignment != "unassigned" else "",
        "quote_start": "0",
        "quote_end": "7",
    }


ROWS = [
    row("1", "primary", "pay", "Pay", "negative"),
    row("1", "secondary", "onboarding", "Onboarding", "neutral"),
    row("2", "primary", "pay", "Pay", "negative"),
    row("2", "secondary", "workload", "Workload", "negative"),
    row("3", "primary", "onboarding", "Onboarding", "positive"),
    row("4", "unassigned"),
]


def test_a_theme_is_counted_once_per_comment_that_carries_it():
    counts = count(ROWS)

    by_label = {theme.theme_label: theme for theme in counts.themes}
    assert by_label["Pay"].comments == 2
    assert by_label["Pay"].as_primary == 2
    assert by_label["Pay"].as_secondary == 0
    assert by_label["Onboarding"].comments == 2
    assert by_label["Onboarding"].as_primary == 1
    assert by_label["Onboarding"].as_secondary == 1


def test_valences_are_counted_per_theme():
    counts = count(ROWS)

    by_label = {theme.theme_label: theme for theme in counts.themes}
    assert by_label["Pay"].valences == {"negative": 2}
    assert by_label["Onboarding"].valences == {"neutral": 1, "positive": 1}


def test_comments_are_counted_once_however_many_themes_they_carry():
    counts = count(ROWS)

    assert counts.comments == 4
    assert counts.comments_labelled == 3
    assert counts.comments_unassigned == 1
    assert counts.rows == 6
    # The point of the caveat: these deliberately do not add up the same way.
    assert sum(theme.comments for theme in counts.themes) == 5


def test_themes_are_ordered_by_how_often_they_appear():
    """Most comments first, and at a tie the one that was primary more often."""
    counts = count(ROWS + [row("5", "primary", "workload", "Workload", "negative")])

    # All three now carry two comments; Pay was primary in both of its.
    assert [(t.theme_label, t.comments, t.as_primary) for t in counts.themes] == [
        ("Pay", 2, 2),
        ("Onboarding", 2, 1),
        ("Workload", 2, 1),
    ]


def test_ties_are_broken_alphabetically_so_the_order_is_stable():
    tied = [
        row("1", "primary", "beta", "Beta", "neutral"),
        row("2", "primary", "alpha", "Alpha", "neutral"),
    ]

    assert [theme.theme_label for theme in count(tied).themes] == ["Alpha", "Beta"]
    assert [theme.theme_label for theme in count(list(reversed(tied))).themes] == [
        "Alpha",
        "Beta",
    ]


def test_an_all_unassigned_file_counts_no_themes():
    counts = count([row("1", "unassigned"), row("2", "unassigned")])

    assert counts.themes == []
    assert counts.comments == 2
    assert counts.comments_unassigned == 2
    assert "No themes were assigned" in counts.render()


def test_the_rendered_table_states_what_it_is_not():
    rendered = count(ROWS).render()

    assert "4 comments" in rendered
    assert "3 with at least one theme" in rendered
    assert "1 with none" in rendered
    assert "5 theme assignments across 3 labelled comments" in rendered


def test_the_footer_states_numbers_rather_than_asserting_a_relationship():
    """It used to claim the theme column always sums to more than the
    comment count. When every labelled comment carries exactly one theme,
    that claim is simply false, and a false number is the one thing this
    tool is supposed not to print."""
    one_each = [
        row("1", "primary", "pay", "Pay", "negative"),
        row("2", "primary", "pay", "Pay", "negative"),
        row("3", "primary", "onboarding", "Onboarding", "neutral"),
    ]

    rendered = count(one_each).render()

    assert "3 theme assignments across 3 labelled comments" in rendered
    assert "more than" not in rendered


def test_the_csv_rows_carry_every_column(tmp_path):
    counts = count(ROWS)

    path = tmp_path / "counts.csv"
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COUNT_COLUMNS)
        writer.writeheader()
        writer.writerows(counts.as_rows())

    with open(path, newline="", encoding="utf-8") as handle:
        written = list(csv.DictReader(handle))

    assert [w["theme_label"] for w in written] == ["Pay", "Onboarding", "Workload"]
    pay = next(w for w in written if w["theme_label"] == "Pay")
    assert pay["comments"] == "2"
    assert pay["negative"] == "2"
    assert pay["positive"] == "0"


def test_counting_an_empty_file_does_not_crash():
    counts = count([])

    assert counts.comments == 0
    assert counts.rows == 0
    assert counts.themes == []
