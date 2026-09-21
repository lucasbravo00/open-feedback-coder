"""Tests for the sample a person is asked to read.

The README tells anyone acting on these results to check a sample first. This
is the part that hands them one, so it has to be reproducible between runs
(you are working through it) and it has to put the quote next to the comment
it came from (that is the whole job).
"""

from __future__ import annotations

import csv

from open_feedback_coder import cli
from open_feedback_coder.reviewing import REVIEW_COLUMNS, render, sample, to_review_rows


def row(comment_id, assignment="primary", theme_label="Pay", quote="the pay is fine"):
    return {
        "comment_id": comment_id,
        "row_number": comment_id,
        "comment_text": f"Comment {comment_id}: the pay is fine but the hours are not.",
        "assignment": assignment,
        "theme_id": "pay",
        "theme_label": theme_label,
        "valence": "neutral",
        "quote": quote,
        "quote_start": "0",
        "quote_end": "5",
    }


ROWS = [row(str(n)) for n in range(1, 51)]


def test_the_same_seed_gives_the_same_sample():
    first = sample(ROWS, 10, seed=7)
    second = sample(ROWS, 10, seed=7)

    assert [r["comment_id"] for r in first] == [r["comment_id"] for r in second]


def test_a_different_seed_gives_a_different_sample():
    assert [r["comment_id"] for r in sample(ROWS, 10, seed=1)] != [
        r["comment_id"] for r in sample(ROWS, 10, seed=2)
    ]


def test_asking_for_more_than_there_is_returns_everything():
    assert len(sample(ROWS, 500)) == len(ROWS)


def test_the_sample_comes_back_in_file_order():
    """Easier to work through beside the source file than in random order."""
    drawn = sample(ROWS, 10, seed=3)

    numbers = [int(r["row_number"]) for r in drawn]
    assert numbers == sorted(numbers)


def test_unassigned_rows_are_left_out_unless_asked_for():
    rows = ROWS + [row("99", assignment="unassigned", theme_label="", quote="")]

    assert "99" not in {r["comment_id"] for r in sample(rows, 100)}
    assert "99" in {r["comment_id"] for r in sample(rows, 100, include_unassigned=True)}


def test_the_sheet_has_two_empty_columns_to_fill_in():
    sheet = to_review_rows(sample(ROWS, 3, seed=0))

    assert all(set(entry) == set(REVIEW_COLUMNS) for entry in sheet)
    assert all(entry["agree"] == "" and entry["notes"] == "" for entry in sheet)
    assert all(entry["quote"] and entry["comment_text"] for entry in sheet)


def test_the_printed_sample_puts_the_quote_under_its_comment():
    text = render(sample(ROWS, 1, seed=0))

    assert "the pay is fine but the hours are not" in text
    assert "> the pay is fine" in text
    assert "primary: Pay" in text


def test_an_unassigned_row_says_why_it_has_no_quote():
    text = render([row("1", assignment="unassigned", theme_label="", quote="")])

    assert "no quote" in text
    assert "(no theme)" in text


def test_rendering_nothing_says_so():
    assert "Nothing to review" in render([])


def test_ofc_review_writes_a_sheet_and_refuses_to_score_it(tmp_path, capsys):
    labelled = tmp_path / "labelled.csv"
    with open(labelled, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(ROWS[0]))
        writer.writeheader()
        writer.writerows(ROWS)

    output = tmp_path / "review.csv"
    exit_code = cli.main(
        [
            "review",
            "--labelled", str(labelled),
            "--sample", "5",
            "--output", str(output),
        ]
    )

    assert exit_code == 0
    with open(output, newline="", encoding="utf-8") as handle:
        sheet = list(csv.DictReader(handle))
    assert len(sheet) == 5
    assert all(entry["agree"] == "" for entry in sheet)

    printed = capsys.readouterr().out
    assert "5 rows sampled from 50 rows" in printed
    assert "does not turn" in printed and "into a score" in printed


def test_ofc_review_rejects_a_file_it_did_not_write(tmp_path):
    other = tmp_path / "something_else.csv"
    other.write_text("name,age\nada,36\n", encoding="utf-8")

    assert cli.main(["review", "--labelled", str(other)]) == 1


def test_ofc_counts_rejects_a_file_it_did_not_write(tmp_path):
    other = tmp_path / "something_else.csv"
    other.write_text("name,age\nada,36\n", encoding="utf-8")

    assert cli.main(["counts", "--labelled", str(other)]) == 1


def test_ofc_counts_reports_on_a_real_labelled_file(tmp_path, capsys):
    labelled = tmp_path / "labelled.csv"
    with open(labelled, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(ROWS[0]))
        writer.writeheader()
        writer.writerows(ROWS[:4])

    counts_path = tmp_path / "counts.csv"
    exit_code = cli.main(
        ["counts", "--labelled", str(labelled), "--output", str(counts_path)]
    )

    assert exit_code == 0
    printed = capsys.readouterr().out
    assert "4 comments" in printed
    assert "count labels, not people" in printed
    assert counts_path.exists()


# The sample is seeded so the same rows come back across several sittings.
# Re-running used to rewrite the sheet blank, destroying exactly the work the
# command exists to collect.


def write_labelled(tmp_path, rows):
    path = tmp_path / "labelled.csv"
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return path


def read_sheet(path):
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def mark_the_sheet(path, agree="yes", notes="the quote fits"):
    sheet = read_sheet(path)
    for entry in sheet:
        entry["agree"] = agree
        entry["notes"] = notes
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_COLUMNS)
        writer.writeheader()
        writer.writerows(sheet)


def test_marks_already_made_survive_a_re_run(tmp_path, capsys):
    labelled = write_labelled(tmp_path, ROWS)
    output = tmp_path / "review.csv"
    arguments = ["review", "--labelled", str(labelled), "--sample", "5", "--output", str(output)]

    cli.main(arguments)
    mark_the_sheet(output)

    cli.main(arguments)

    sheet = read_sheet(output)
    assert len(sheet) == 5
    assert all(entry["agree"] == "yes" for entry in sheet)
    assert all(entry["notes"] == "the quote fits" for entry in sheet)
    assert "keeping 5 marks already in it" in capsys.readouterr().err


def test_overwrite_starts_the_sheet_again(tmp_path):
    labelled = write_labelled(tmp_path, ROWS)
    output = tmp_path / "review.csv"
    arguments = ["review", "--labelled", str(labelled), "--sample", "5", "--output", str(output)]

    cli.main(arguments)
    mark_the_sheet(output)

    cli.main(arguments + ["--overwrite"])

    assert all(entry["agree"] == "" for entry in read_sheet(output))


def test_a_larger_sample_keeps_the_marks_on_the_rows_it_repeats(tmp_path):
    """The seed is fixed, so a bigger sample contains work already done."""
    labelled = write_labelled(tmp_path, ROWS)
    output = tmp_path / "review.csv"
    base = ["review", "--labelled", str(labelled), "--output", str(output)]

    cli.main(base + ["--sample", "5"])
    mark_the_sheet(output, agree="no", notes="wrong theme")
    marked = {entry["comment_id"] for entry in read_sheet(output)}

    cli.main(base + ["--sample", "20"])

    sheet = read_sheet(output)
    assert len(sheet) == 20
    kept = [entry for entry in sheet if entry["comment_id"] in marked]
    assert kept, "the smaller sample should be inside the larger one"
    assert all(entry["agree"] == "no" for entry in kept)
    assert all(
        entry["agree"] == "" for entry in sheet if entry["comment_id"] not in marked
    )


def test_counts_refuses_the_review_sheet_it_just_wrote(tmp_path):
    """review.csv has comment_id, assignment and quote but no theme_id, so it
    used to be counted as a single fabricated theme."""
    labelled = write_labelled(tmp_path, ROWS)
    output = tmp_path / "review.csv"
    cli.main(["review", "--labelled", str(labelled), "--sample", "5", "--output", str(output)])

    assert cli.main(["counts", "--labelled", str(output)]) == 1


def test_review_refuses_the_review_sheet_it_just_wrote(tmp_path):
    labelled = write_labelled(tmp_path, ROWS)
    output = tmp_path / "review.csv"
    cli.main(["review", "--labelled", str(labelled), "--sample", "5", "--output", str(output)])

    assert cli.main(["review", "--labelled", str(output), "--output", str(tmp_path / "x.csv")]) == 1
