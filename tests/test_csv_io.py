"""Tests for reading someone else's CSV, whatever their columns are called."""

import csv

import pytest

from open_feedback_coder.csv_io import (
    FAILURE_COLUMNS,
    OUTPUT_COLUMNS,
    InputError,
    read_comments,
    write_rows,
)


def write_csv(path, rows, fieldnames, delimiter=",", encoding="utf-8"):
    with open(path, "w", newline="", encoding=encoding) as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter=delimiter)
        writer.writeheader()
        writer.writerows(rows)
    return str(path)


def test_reads_the_named_column_whatever_it_is_called(tmp_path):
    path = write_csv(
        tmp_path / "input.csv",
        [
            {"respondent": "A1", "comentario libre": "The onboarding was chaotic."},
            {"respondent": "A2", "comentario libre": "Pay is fine."},
        ],
        ["respondent", "comentario libre"],
    )

    comments, skipped = read_comments(path, text_column="comentario libre")

    assert [comment.text for comment in comments] == [
        "The onboarding was chaotic.",
        "Pay is fine.",
    ]
    assert skipped.total == 0


def test_comment_id_defaults_to_the_row_number(tmp_path):
    path = write_csv(
        tmp_path / "input.csv",
        [{"text": "first"}, {"text": "second"}],
        ["text"],
    )

    comments, _ = read_comments(path, text_column="text")

    assert [c.comment_id for c in comments] == ["1", "2"]
    assert [c.row_number for c in comments] == [1, 2]


def test_comment_id_comes_from_the_id_column_when_given(tmp_path):
    path = write_csv(
        tmp_path / "input.csv",
        [{"rid": "R-900", "text": "first"}, {"rid": "R-901", "text": "second"}],
        ["rid", "text"],
    )

    comments, _ = read_comments(path, text_column="text", id_column="rid")

    assert [c.comment_id for c in comments] == ["R-900", "R-901"]
    # The row number is kept regardless, so output can be traced back by position.
    assert [c.row_number for c in comments] == [1, 2]


def test_row_number_counts_skipped_rows_so_it_matches_the_source_file(tmp_path):
    path = write_csv(
        tmp_path / "input.csv",
        [{"text": "first"}, {"text": ""}, {"text": "third"}],
        ["text"],
    )

    comments, skipped = read_comments(path, text_column="text")

    assert [c.row_number for c in comments] == [1, 3]
    assert skipped.empty == 1


def test_empty_and_placeholder_answers_are_skipped_and_counted(tmp_path):
    path = write_csv(
        tmp_path / "input.csv",
        [
            {"text": "a real answer"},
            {"text": ""},
            {"text": "   "},
            {"text": "N/A"},
            {"text": "none"},
            {"text": "-"},
            {"text": "no strong feelings"},
        ],
        ["text"],
    )

    comments, skipped = read_comments(path, text_column="text")

    assert [c.text for c in comments] == ["a real answer", "no strong feelings"]
    assert skipped.empty == 2
    assert skipped.placeholder == 3
    assert skipped.placeholder_values == {"n/a": 1, "none": 1, "-": 1}


def test_missing_text_column_names_the_columns_that_do_exist(tmp_path):
    path = write_csv(tmp_path / "input.csv", [{"feedback": "hi"}], ["feedback"])

    with pytest.raises(InputError) as error:
        read_comments(path, text_column="comment")

    assert "'comment'" in str(error.value)
    assert "'feedback'" in str(error.value)


def test_missing_id_column_is_reported(tmp_path):
    path = write_csv(tmp_path / "input.csv", [{"feedback": "hi"}], ["feedback"])

    with pytest.raises(InputError):
        read_comments(path, text_column="feedback", id_column="respondent_id")


def test_missing_file_is_reported(tmp_path):
    with pytest.raises(InputError):
        read_comments(str(tmp_path / "absent.csv"), text_column="text")


def test_tab_separated_input(tmp_path):
    path = write_csv(
        tmp_path / "input.tsv",
        [{"text": "tab separated answer", "other": "x"}],
        ["text", "other"],
        delimiter="\t",
    )

    comments, _ = read_comments(path, text_column="text", delimiter="\t")

    assert comments[0].text == "tab separated answer"


def test_byte_order_mark_does_not_corrupt_the_first_column_name(tmp_path):
    path = tmp_path / "bom.csv"
    path.write_text("\ufefftext\nan answer\n", encoding="utf-8")

    comments, _ = read_comments(str(path), text_column="text")

    assert comments[0].text == "an answer"


def test_text_is_canonicalised_on_read(tmp_path):
    path = write_csv(tmp_path / "input.csv", [{"text": "café talk"}], ["text"])

    comments, _ = read_comments(path, text_column="text")

    assert comments[0].text == "café talk"


def test_write_rows_round_trips(tmp_path):
    rows = [
        {
            "comment_id": "1",
            "row_number": 1,
            "comment_text": "The onboarding was chaotic.",
            "assignment": "primary",
            "theme_id": "onboarding",
            "theme_label": "Onboarding",
            "valence": "negative",
            "quote": "onboarding was chaotic",
            "quote_start": 4,
            "quote_end": 26,
        }
    ]
    path = tmp_path / "out.csv"

    write_rows(str(path), OUTPUT_COLUMNS, rows)

    with open(path, newline="", encoding="utf-8") as handle:
        read_back = list(csv.DictReader(handle))

    assert read_back[0]["theme_id"] == "onboarding"
    start, end = int(read_back[0]["quote_start"]), int(read_back[0]["quote_end"])
    assert read_back[0]["comment_text"][start:end] == read_back[0]["quote"]


def test_write_rows_writes_a_header_even_with_no_rows(tmp_path):
    path = tmp_path / "failures.csv"

    write_rows(str(path), FAILURE_COLUMNS, [])

    assert path.read_text(encoding="utf-8").strip() == ",".join(FAILURE_COLUMNS)
