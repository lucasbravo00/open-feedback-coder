"""Tests for turning hand-edited table rows into an approved codebook.

A table editor does not hand back empty strings for cells nobody touched. It
hands back None, or a float NaN, and `str(nan)` is "nan" - a string that looks
like a perfectly good theme id and passes every validation rule there is.
"""

import pytest

from open_feedback_coder.codebook import CodebookError
from open_feedback_coder.editing import codebook_from_records, text_cell


@pytest.mark.parametrize(
    "value",
    [None, float("nan"), "", "   ", "nan", "NaN", "None", "<NA>", "NaT"],
)
def test_blank_cells_of_every_flavour_read_as_empty(value):
    assert text_cell(value) == ""


@pytest.mark.parametrize(
    ("value", "expected"),
    [("onboarding", "onboarding"), ("  Pay  ", "Pay"), (42, "42")],
)
def test_filled_cells_are_returned_trimmed(value, expected):
    assert text_cell(value) == expected


def test_a_table_of_themes_becomes_a_codebook():
    book = codebook_from_records(
        [
            {"id": "onboarding", "label": "Onboarding", "description": "First weeks."},
            {"id": "pay", "label": "Pay and benefits", "description": ""},
        ]
    )

    assert [theme.id for theme in book.themes] == ["onboarding", "pay"]
    assert book.themes[0].description == "First weeks."


def test_rows_left_entirely_blank_are_dropped():
    """Adding a row and changing your mind is a normal thing to do."""
    book = codebook_from_records(
        [
            {"id": "pay", "label": "Pay", "description": ""},
            {"id": None, "label": float("nan"), "description": None},
            {"id": "", "label": "", "description": ""},
        ]
    )

    assert [theme.id for theme in book.themes] == ["pay"]


def test_a_row_with_a_label_and_no_id_is_refused_not_invented():
    """The bug this guards: the theme used to come out named "nan"."""
    with pytest.raises(CodebookError) as error:
        codebook_from_records(
            [
                {"id": "pay", "label": "Pay", "description": ""},
                {"id": float("nan"), "label": "Something I started typing", "description": None},
            ]
        )

    assert "has no 'id'" in str(error.value)


def test_a_row_with_only_a_description_is_refused():
    with pytest.raises(CodebookError) as error:
        codebook_from_records(
            [
                {"id": "pay", "label": "Pay", "description": ""},
                {"id": float("nan"), "label": float("nan"), "description": "just a note"},
            ]
        )

    assert "has no 'id'" in str(error.value)


@pytest.mark.parametrize(
    ("records", "expected"),
    [
        ([], "non-empty list"),
        ([{"id": "", "label": "", "description": ""}], "non-empty list"),
        ([{"id": "Pay Themes", "label": "Pay", "description": ""}], "lowercase"),
        (
            [
                {"id": "pay", "label": "Pay", "description": ""},
                {"id": "pay", "label": "Pay again", "description": ""},
            ],
            "more than once",
        ),
        ([{"id": "pay", "label": "", "description": "x"}], "has no 'label'"),
    ],
)
def test_the_table_faces_the_same_rules_as_the_yaml_file(records, expected):
    with pytest.raises(CodebookError) as error:
        codebook_from_records(records)

    assert expected in str(error.value)


def test_missing_keys_are_treated_as_blank_cells():
    book = codebook_from_records([{"id": "pay", "label": "Pay"}])

    assert book.themes[0].description == ""
