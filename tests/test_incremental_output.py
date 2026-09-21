"""Tests that results reach disk as they are produced, not at the end.

A labelling run makes one paid call per comment. It used to hold every result
in memory and write both files only after the last call returned, so a run
that died at comment 900 of 1000 wrote nothing at all and threw away
everything it had already paid for and checked.
"""

from __future__ import annotations

import csv

import pytest

from open_feedback_coder.csv_io import FAILURE_COLUMNS, OUTPUT_COLUMNS, RowWriter
from open_feedback_coder.label import label_comments
from open_feedback_coder.llm import Usage

TEXT = {
    "1": "Onboarding dragged on and nobody owned it.",
    "2": "The pay is fine, honestly.",
    "3": "Nobody owned it here either.",
}


def clean_response(quote):
    return {
        "unassigned": False,
        "assignments": [
            {
                "theme_id": "onboarding",
                "role": "primary",
                "valence": "negative",
                "quote": quote,
            }
        ],
    }


class Client:
    model = "stub-model"
    retries = 0

    def complete_json(self, system, user, schema, schema_name):
        text = user.split("Comment:\n", 1)[1]
        if text == TEXT["2"]:
            # An invented quote: this comment goes to the rejections file.
            return clean_response("the salary is good"), Usage(10, 5)
        return clean_response("owned it"), Usage(10, 5)


@pytest.fixture
def corpus(make_comment):
    return [make_comment(text, cid, int(cid)) for cid, text in TEXT.items()]


def read(path):
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_the_header_is_on_disk_before_any_row(tmp_path):
    path = tmp_path / "labelled.csv"

    with RowWriter(str(path), OUTPUT_COLUMNS):
        assert path.read_text(encoding="utf-8").startswith("comment_id,")


def test_each_batch_is_flushed_and_readable_immediately(tmp_path):
    path = tmp_path / "labelled.csv"
    row = dict.fromkeys(OUTPUT_COLUMNS, "")
    row["comment_id"] = "1"

    with RowWriter(str(path), OUTPUT_COLUMNS) as writer:
        writer.write([row])
        assert [r["comment_id"] for r in read(path)] == ["1"]
        writer.write([dict(row, comment_id="2")])
        assert [r["comment_id"] for r in read(path)] == ["1", "2"]


def test_a_writer_with_no_path_is_a_no_op():
    with RowWriter(None, OUTPUT_COLUMNS) as writer:
        writer.write([{"comment_id": "1"}])


def test_rows_are_handed_over_one_comment_at_a_time(book, corpus):
    batches = []

    run = label_comments(
        corpus, book, Client(), concurrency=1, on_rows=batches.append
    )

    # One call per comment, in input order, and together they are the result.
    assert len(batches) == 3
    assert [row["comment_id"] for batch in batches for row in batch] == ["1", "3"]
    assert [row["comment_id"] for row in run.rows] == ["1", "3"]


def test_rejections_are_handed_over_as_they_happen(book, corpus):
    batches = []

    label_comments(corpus, book, Client(), concurrency=1, on_rejections=batches.append)

    handed_over = [entry for batch in batches for entry in batch]
    assert [entry["comment_id"] for entry in handed_over] == ["2"]


def test_what_was_finished_survives_a_run_that_dies(tmp_path, book, corpus):
    """The point of all of it: a crash keeps what was already paid for."""
    labelled_path = tmp_path / "labelled.csv"
    failures_path = tmp_path / "failures.csv"
    seen = []

    def explode_after_the_first(rows):
        seen.append(rows)
        if len(seen) == 2:
            raise KeyboardInterrupt("the user pressed ctrl-c")

    with RowWriter(str(labelled_path), OUTPUT_COLUMNS) as labelled:
        with RowWriter(str(failures_path), FAILURE_COLUMNS) as rejected:
            def on_rows(rows):
                labelled.write(rows)
                explode_after_the_first(rows)

            with pytest.raises(KeyboardInterrupt):
                label_comments(
                    corpus,
                    book,
                    Client(),
                    concurrency=1,
                    on_rows=on_rows,
                    on_rejections=rejected.write,
                )

    # Both files are valid CSVs holding what had been checked by then.
    assert [row["comment_id"] for row in read(labelled_path)] == ["1"]
    assert read(failures_path) == []
    for row in read(labelled_path):
        start, end = int(row["quote_start"]), int(row["quote_end"])
        assert row["comment_text"][start:end] == row["quote"]
