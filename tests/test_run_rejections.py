"""Tests for what a whole run records about what it refused.

A third review found that nothing exercised this. Every duplicate-theme test
called `assemble_comment_rows` directly and asserted on the outcome, so the
single line carrying a dropped repeat into the run result could be deleted,
`LabelRun.failures` could be made to return every rejection regardless of
scope, and the column that tells the two apart could be removed from the
output file — each with the whole suite still green.

These tests run a real `label_comments` and a real `ofc label`, and read the
files that land on disk.
"""

from __future__ import annotations

import csv

import pytest

from open_feedback_coder import cli
from open_feedback_coder.csv_io import FAILURE_COLUMNS, OUTPUT_COLUMNS, write_rows
from open_feedback_coder.label import label_comments
from open_feedback_coder.llm import Usage

TEXT = {
    "1": "Onboarding dragged on and nobody owned it, and the pay is fine.",
    "2": "The pay is fine and nobody owned it either.",
    "3": "Nobody owned it here either, frankly.",
}


def assignment(theme_id, role, valence, quote):
    return {"theme_id": theme_id, "role": role, "valence": valence, "quote": quote}


# Comment 1 names the same theme twice: the repeat is dropped, the comment stays.
WITH_DUPLICATE = {
    "unassigned": False,
    "assignments": [
        assignment("onboarding", "primary", "negative", "nobody owned it"),
        assignment("onboarding", "secondary", "negative", "Onboarding dragged on"),
        assignment("pay", "secondary", "neutral", "the pay is fine"),
    ],
}

# Comment 2 quotes something nobody wrote: the whole comment is excluded.
WITH_INVENTED_QUOTE = {
    "unassigned": False,
    "assignments": [assignment("pay", "primary", "positive", "the salary is excellent")],
}

# "owned it" appears verbatim in all three, and matching is case sensitive.
CLEAN = {
    "unassigned": False,
    "assignments": [assignment("onboarding", "primary", "negative", "owned it")],
}


class ScriptedClient:
    def __init__(self, script):
        self.model = "stub-model"
        self.script = script

    def complete_json(self, system, user, schema, schema_name):
        comment_text = user.split("Comment:\n", 1)[1]
        for comment_id, text in TEXT.items():
            if text == comment_text:
                return self.script[comment_id], Usage(input_tokens=10, output_tokens=5)
        raise AssertionError(f"unexpected comment: {comment_text!r}")


@pytest.fixture
def corpus(make_comment):
    return [make_comment(text, cid, int(cid)) for cid, text in TEXT.items()]


def test_a_dropped_repeat_reaches_the_run_result(book, corpus):
    client = ScriptedClient({"1": WITH_DUPLICATE, "2": CLEAN, "3": CLEAN})

    run = label_comments(corpus, book, client, concurrency=1)

    assert run.comments_labelled == 3
    assert run.comments_failed == 0
    assert run.failures == []

    dropped = run.dropped_assignments
    assert len(dropped) == 1
    assert dropped[0]["scope"] == "assignment"
    assert dropped[0]["comment_id"] == "1"
    assert dropped[0]["failure_reason"] == "duplicate_theme"
    assert run.rejections == dropped

    # The comment itself is still in the output, with its other two labels.
    kept = [row["theme_id"] for row in run.rows if row["comment_id"] == "1"]
    assert kept == ["onboarding", "pay"]


def test_the_two_scopes_are_counted_apart(book, corpus):
    client = ScriptedClient(
        {"1": WITH_DUPLICATE, "2": WITH_INVENTED_QUOTE, "3": CLEAN}
    )

    run = label_comments(corpus, book, client, concurrency=1)

    assert len(run.rejections) == 2
    assert [entry["scope"] for entry in run.rejections] == ["assignment", "comment"]
    assert run.comments_failed == 1
    assert [entry["comment_id"] for entry in run.failures] == ["2"]
    assert [entry["comment_id"] for entry in run.dropped_assignments] == ["1"]
    assert run.comments_labelled == 2

    # A comment kept despite a drop is in both files; an excluded one is not.
    in_output = {row["comment_id"] for row in run.rows}
    assert "1" in in_output
    assert "2" not in in_output


def test_a_run_with_no_rejections_reports_none(book, corpus):
    client = ScriptedClient({"1": CLEAN, "2": CLEAN, "3": CLEAN})

    run = label_comments(corpus, book, client, concurrency=1)

    assert run.rejections == []
    assert run.failures == []
    assert run.dropped_assignments == []


# --- the files that actually land on disk ----------------------------------


def test_the_labelled_header_is_exactly_this(tmp_path):
    """Pinned literally, so removing a column cannot pass unnoticed."""
    path = tmp_path / "labelled.csv"

    write_rows(str(path), OUTPUT_COLUMNS, [])

    assert path.read_text(encoding="utf-8").strip() == (
        "comment_id,row_number,comment_text,assignment,theme_id,theme_label,"
        "valence,quote,quote_start,quote_end"
    )


def test_the_rejections_header_is_exactly_this(tmp_path):
    path = tmp_path / "failures.csv"

    write_rows(str(path), FAILURE_COLUMNS, [])

    assert path.read_text(encoding="utf-8").strip() == (
        "scope,comment_id,row_number,comment_text,failure_reason,detail,"
        "rejected_theme_id,rejected_quote"
    )


def test_ofc_label_writes_the_dropped_repeat_to_the_failures_file(
    tmp_path, monkeypatch, capsys
):
    """End to end: the drop survives the CLI, the file and the summary."""
    monkeypatch.setattr(cli, "Client", lambda *a, **k: ScriptedClient(
        {"1": WITH_DUPLICATE, "2": CLEAN, "3": CLEAN}
    ))
    monkeypatch.setattr(
        cli, "TokenCounter", lambda model: type("C", (), {"is_exact": True, "count": lambda self, t: 1})()
    )

    input_path = tmp_path / "survey.csv"
    with open(input_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["rid", "answer"])
        writer.writeheader()
        for comment_id, text in TEXT.items():
            writer.writerow({"rid": comment_id, "answer": text})

    codebook_path = tmp_path / "codebook.yaml"
    codebook_path.write_text(
        "version: 1\nthemes:\n"
        "  - id: onboarding\n    label: Onboarding\n"
        "  - id: pay\n    label: Pay\n",
        encoding="utf-8",
    )

    output_path = tmp_path / "labelled.csv"
    failures_path = tmp_path / "failures.csv"

    exit_code = cli.main(
        [
            "label",
            "--input", str(input_path),
            "--text-column", "answer",
            "--id-column", "rid",
            "--codebook", str(codebook_path),
            "--output", str(output_path),
            "--failures", str(failures_path),
            "--yes",
        ]
    )
    assert exit_code == 0

    with open(failures_path, newline="", encoding="utf-8") as handle:
        rejections = list(csv.DictReader(handle))

    assert len(rejections) == 1
    assert rejections[0]["scope"] == "assignment"
    assert rejections[0]["comment_id"] == "1"
    assert rejections[0]["failure_reason"] == "duplicate_theme"
    assert rejections[0]["rejected_quote"] == "Onboarding dragged on"

    summary = capsys.readouterr().err
    assert "Kept 1 comment after dropping 1 repeated theme assignment." in summary
    assert "0 excluded comments, 1 dropped assignment" in summary

    with open(output_path, newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert "1" in {row["comment_id"] for row in rows}
    for row in rows:
        start, end = int(row["quote_start"]), int(row["quote_end"])
        assert row["comment_text"][start:end] == row["quote"]


def test_one_path_for_both_outputs_is_refused_before_anything_is_sent(tmp_path, monkeypatch):
    """Two writers on one path truncate each other, and the run had already
    been paid for by the time anyone could tell."""
    called = []
    monkeypatch.setattr(
        cli, "Client", lambda *a, **k: called.append(1) or ScriptedClient({})
    )

    input_path = tmp_path / "survey.csv"
    input_path.write_text("answer\nsomething\n", encoding="utf-8")
    codebook_path = tmp_path / "codebook.yaml"
    codebook_path.write_text("themes:\n  - id: pay\n    label: Pay\n", encoding="utf-8")
    both = tmp_path / "same.csv"

    exit_code = cli.main(
        [
            "label",
            "--input", str(input_path),
            "--text-column", "answer",
            "--codebook", str(codebook_path),
            "--output", str(both),
            "--failures", str(both),
            "--yes",
        ]
    )

    assert exit_code == 1
    assert called == [], "no client should even be built"
    assert not both.exists()
