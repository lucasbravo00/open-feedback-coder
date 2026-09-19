"""Tests for reading an in-memory upload without consuming or closing it.

The web interface hands the same file object back on every rerun of the page,
so anything that reads it has to leave it exactly as it found it.
"""

import io

import pytest

from open_feedback_coder.csv_io import (
    InputError,
    column_names,
    decode_document,
    read_comments,
    read_comments_from_text,
)

SURVEY = (
    "respondent,open_answer\r\n"
    "A1,Onboarding dragged on and nobody owned it.\r\n"
    "A2,\r\n"
    "A3,N/A\r\n"
    "A4,The pay is genuinely competitive.\r\n"
)


def upload_of(text: str, encoding: str = "utf-8") -> io.BytesIO:
    return io.BytesIO(text.encode(encoding))


def test_decoding_leaves_the_upload_open_and_repeatable():
    """The regression: the upload used to be closed by the first read."""
    upload = upload_of(SURVEY)

    first = decode_document(upload)
    second = decode_document(upload)

    assert first == second == SURVEY
    assert not upload.closed


def test_a_byte_order_mark_is_stripped():
    upload = upload_of(SURVEY, encoding="utf-8-sig")

    document = decode_document(upload)

    assert document.startswith("respondent,")


def test_a_file_that_is_not_utf8_is_reported_not_mangled():
    upload = io.BytesIO("id,text\r\n1,café\r\n".encode("latin-1"))

    with pytest.raises(UnicodeDecodeError):
        decode_document(upload)


def test_column_names_reads_the_header():
    assert column_names(SURVEY) == ["respondent", "open_answer"]


def test_column_names_of_an_empty_document():
    assert column_names("") == []


def test_column_names_honours_the_delimiter():
    assert column_names("a\tb\r\n1\t2\r\n", delimiter="\t") == ["a", "b"]
    assert column_names("a\tb\r\n1\t2\r\n") == ["a\tb"]


def test_reading_text_applies_the_same_rules_as_reading_a_file(tmp_path):
    """The two entry points must not drift apart."""
    path = tmp_path / "survey.csv"
    path.write_text(SURVEY, encoding="utf-8", newline="")

    from_file, skipped_file = read_comments(str(path), text_column="open_answer")
    from_text, skipped_text = read_comments_from_text(SURVEY, text_column="open_answer")

    assert from_file == from_text
    assert (skipped_file.empty, skipped_file.placeholder) == (
        skipped_text.empty,
        skipped_text.placeholder,
    )
    assert [comment.row_number for comment in from_text] == [1, 4]
    assert skipped_text.empty == 1
    assert skipped_text.placeholder == 1


def test_a_missing_column_names_the_flag_that_set_it():
    with pytest.raises(InputError) as error:
        read_comments_from_text(SURVEY, text_column="comentario", origin="the uploaded file")

    assert "--text-column" in str(error.value)
    assert "the uploaded file" in str(error.value)
    assert "'open_answer'" in str(error.value)


def test_a_missing_id_column_names_its_own_flag():
    with pytest.raises(InputError) as error:
        read_comments_from_text(SURVEY, text_column="open_answer", id_column="uuid")

    assert "--id-column" in str(error.value)
