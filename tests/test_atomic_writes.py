"""Tests for replacing a file without risking what is already in it.

`ofc review` rewrites a sheet that holds marks somebody typed. Truncating it
and then writing puts that work at the mercy of whatever happens in between,
so the replacement is staged and renamed instead.

The path comparison is here too. Two writers on one file destroy it, and
string equality misses every other way the same path can be spelled.
"""

from __future__ import annotations

import csv
import os

import pytest

from open_feedback_coder.csv_io import OUTPUT_COLUMNS, rewrite_atomically, same_file


def a_row(comment_id):
    text = f"Comment {comment_id}: nobody owned it."
    start = text.index("nobody owned it")
    return {
        "comment_id": comment_id,
        "row_number": comment_id,
        "comment_text": text,
        "assignment": "primary",
        "theme_id": "onboarding",
        "theme_label": "Onboarding",
        "valence": "negative",
        "quote": "nobody owned it",
        "quote_start": str(start),
        "quote_end": str(start + 15),
    }


def write(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def read(path):
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_rewriting_replaces_the_file(tmp_path):
    path = tmp_path / "sheet.csv"
    write(path, [a_row("1"), a_row("2")])

    rewrite_atomically(str(path), OUTPUT_COLUMNS, [a_row("1")])

    assert [row["comment_id"] for row in read(path)] == ["1"]


def test_a_failure_while_writing_leaves_the_old_file_alone(tmp_path):
    path = tmp_path / "sheet.csv"
    write(path, [a_row("1"), a_row("2")])

    class Exploding(dict):
        def get(self, *args, **kwargs):
            raise RuntimeError("disk full")

    with pytest.raises(RuntimeError):
        rewrite_atomically(str(path), OUTPUT_COLUMNS, [a_row("1"), Exploding()])

    assert [row["comment_id"] for row in read(path)] == ["1", "2"]
    assert list(tmp_path.iterdir()) == [path], "no temporary file left behind"


def test_a_failure_at_the_moment_of_replacement_leaves_the_original(tmp_path, monkeypatch):
    """Copying over the target would leave it truncated; renaming cannot.

    This is what makes the rewrite atomic rather than merely careful: the new
    file is complete on disk before anything touches the old one.
    """
    path = tmp_path / "sheet.csv"
    write(path, [a_row("1"), a_row("2")])

    def refuse(source, destination):
        raise OSError("rename failed")

    monkeypatch.setattr(os, "replace", refuse)

    with pytest.raises(OSError):
        rewrite_atomically(str(path), OUTPUT_COLUMNS, [a_row("1")])

    assert [row["comment_id"] for row in read(path)] == ["1", "2"]
    assert list(tmp_path.iterdir()) == [path]


def test_writing_a_file_that_does_not_exist_yet(tmp_path):
    path = tmp_path / "new.csv"

    rewrite_atomically(str(path), OUTPUT_COLUMNS, [a_row("1")])

    assert [row["comment_id"] for row in read(path)] == ["1"]


def test_same_file_sees_through_the_ways_a_path_can_be_written(tmp_path):
    target = tmp_path / "out.csv"
    target.write_text("x", encoding="utf-8")
    (tmp_path / "sub").mkdir()

    assert same_file(str(target), str(target))
    assert same_file(str(target), f"{tmp_path}/./out.csv")
    assert same_file(str(target), f"{tmp_path}/sub/../out.csv")


def test_same_file_is_false_for_different_files_and_for_nothing(tmp_path):
    target = tmp_path / "out.csv"
    target.write_text("x", encoding="utf-8")

    assert not same_file(str(target), str(tmp_path / "other.csv"))
    assert not same_file(str(target), None)
    assert not same_file(None, str(target))
    assert not same_file(None, None)
    assert not same_file("", "")


def test_same_file_works_before_either_file_exists(tmp_path):
    """The guards run before the run does, so neither path is on disk yet."""
    assert same_file(f"{tmp_path}/a.csv", f"{tmp_path}/./a.csv")
    assert not same_file(f"{tmp_path}/a.csv", f"{tmp_path}/b.csv")
