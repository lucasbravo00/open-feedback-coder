"""Tests for continuing an interrupted labelling run.

Writing rows as they are checked means an interrupted run keeps what it had
already paid for. Resuming is the other half: without it you still have to pay
for those comments again to get a complete file.

The care here is about the boundary. A run killed mid-write may have flushed
some of a comment's rows and not the rest, and a comment published with two of
its three labels is precisely what the all-or-nothing rule exists to prevent.
So the last comment in each file is always redone.
"""

from __future__ import annotations

import csv

import pytest

from open_feedback_coder import cli
from open_feedback_coder.csv_io import (
    FAILURE_COLUMNS,
    OUTPUT_COLUMNS,
    read_partial,
    rewrite_atomically,
)
from open_feedback_coder.llm import Usage

TEXT = {
    "1": "Onboarding dragged on and nobody owned it.",
    "2": "The pay is fine, honestly.",
    "3": "Nobody owned it here either.",
    "4": "Everything owned it, apparently.",
}


def labelled_row(comment_id, theme="pay", assignment="primary"):
    """A row as the labeller would really have written it, offsets and all."""
    text = TEXT.get(comment_id, "owned it")
    quote = "owned it" if "owned it" in text else text.split(",")[0]
    start = text.index(quote)
    return {
        "comment_id": comment_id,
        "row_number": comment_id,
        "comment_text": text,
        "assignment": assignment,
        "theme_id": theme,
        "theme_label": theme.title(),
        "valence": "neutral",
        "quote": quote,
        "quote_start": str(start),
        "quote_end": str(start + len(quote)),
    }


def write(path, columns, rows):
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def read(path):
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


# --- reading a partial file ------------------------------------------------


def test_a_missing_file_is_an_empty_partial_run(tmp_path):
    assert read_partial(str(tmp_path / "absent.csv")) == ([], set(), set())


def test_a_header_only_file_is_an_empty_partial_run(tmp_path):
    path = tmp_path / "labelled.csv"
    write(path, OUTPUT_COLUMNS, [])

    assert read_partial(str(path)) == ([], set(), set())


def test_the_last_comment_is_dropped_because_it_may_be_half_written(tmp_path):
    path = tmp_path / "labelled.csv"
    write(
        path,
        OUTPUT_COLUMNS,
        [
            labelled_row("1"),
            labelled_row("2"),
            labelled_row("3", "onboarding"),
            labelled_row("3", "pay", "secondary"),
        ],
    )

    kept, done, unsafe = read_partial(str(path))

    assert {key[0] for key in done} == {"1", "2"}
    assert [row["comment_id"] for row in kept] == ["1", "2"]


def test_a_truncated_final_line_cannot_be_mistaken_for_a_finished_comment(tmp_path):
    """What a killed process actually leaves behind."""
    path = tmp_path / "labelled.csv"
    write(path, OUTPUT_COLUMNS, [labelled_row("1"), labelled_row("2")])
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("3,3,Nobody owned it here eit")

    kept, done, unsafe = read_partial(str(path))

    # The torn fragment is discarded, then 2 is dropped for being last.
    assert {key[0] for key in done} == {"1"}
    assert {key[0] for key in unsafe} == {"2"}
    assert [row["comment_id"] for row in kept] == ["1"]


def test_the_key_uses_the_row_number_so_duplicate_ids_do_not_collide(tmp_path):
    path = tmp_path / "labelled.csv"
    first = labelled_row("same")
    first["row_number"] = "1"
    second = labelled_row("same")
    second["row_number"] = "2"
    third = labelled_row("same")
    third["row_number"] = "3"
    write(path, OUTPUT_COLUMNS, [first, second, third])

    _, done, _unsafe = read_partial(str(path))

    assert done == {("same", "1"), ("same", "2")}


# --- rewriting safely ------------------------------------------------------


def test_rewriting_leaves_the_old_file_alone_until_the_new_one_is_whole(tmp_path):
    path = tmp_path / "labelled.csv"
    write(path, OUTPUT_COLUMNS, [labelled_row("1"), labelled_row("2")])

    class Exploding(dict):
        def get(self, *args, **kwargs):
            raise RuntimeError("disk full")

    with pytest.raises(RuntimeError):
        rewrite_atomically(str(path), OUTPUT_COLUMNS, [labelled_row("1"), Exploding()])

    # The original survived, and no temporary file was left behind.
    assert [row["comment_id"] for row in read(path)] == ["1", "2"]
    assert list(tmp_path.iterdir()) == [path]


def test_a_failure_at_the_moment_of_replacement_leaves_the_original(tmp_path, monkeypatch):
    """Copying over the target would leave it truncated; renaming cannot.

    This is what makes the rewrite atomic rather than merely careful: the new
    file is complete on disk before anything touches the old one.
    """
    import os

    path = tmp_path / "labelled.csv"
    write(path, OUTPUT_COLUMNS, [labelled_row("1"), labelled_row("2")])

    def refuse(source, destination):
        raise OSError("rename failed")

    monkeypatch.setattr(os, "replace", refuse)

    with pytest.raises(OSError):
        rewrite_atomically(str(path), OUTPUT_COLUMNS, [labelled_row("1")])

    assert [row["comment_id"] for row in read(path)] == ["1", "2"]
    assert list(tmp_path.iterdir()) == [path], "no temporary file left behind"


def test_rewriting_replaces_the_file(tmp_path):
    path = tmp_path / "labelled.csv"
    write(path, OUTPUT_COLUMNS, [labelled_row("1"), labelled_row("2")])

    rewrite_atomically(str(path), OUTPUT_COLUMNS, [labelled_row("1")])

    assert [row["comment_id"] for row in read(path)] == ["1"]


# --- the command ------------------------------------------------------------


class Client:
    """Counts what it is asked, so a resumed run can be shown not to redo work."""

    model = "stub-model"
    retries = 0

    def __init__(self):
        self.asked = []

    def complete_json(self, system, user, schema, schema_name):
        text = user.split("Comment:\n", 1)[1]
        self.asked.append(text)
        return (
            {
                "unassigned": False,
                "assignments": [
                    {
                        "theme_id": "pay",
                        "role": "primary",
                        "valence": "neutral",
                        "quote": "owned it" if "owned it" in text else "pay is fine",
                    }
                ],
            },
            Usage(10, 5),
        )


@pytest.fixture
def project(tmp_path, monkeypatch):
    client = Client()
    monkeypatch.setattr(cli, "Client", lambda *a, **k: client)
    monkeypatch.setattr(
        cli,
        "TokenCounter",
        lambda model: type("C", (), {"is_exact": True, "count": lambda self, t: 1})(),
    )

    input_path = tmp_path / "survey.csv"
    with open(input_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["rid", "answer"])
        writer.writeheader()
        for comment_id, text in TEXT.items():
            writer.writerow({"rid": comment_id, "answer": text})

    codebook = tmp_path / "codebook.yaml"
    codebook.write_text("themes:\n  - id: pay\n    label: Pay\n", encoding="utf-8")

    def run(*extra):
        return cli.main(
            [
                "label",
                "--input", str(input_path),
                "--text-column", "answer",
                "--id-column", "rid",
                "--codebook", str(codebook),
                "--output", str(tmp_path / "labelled.csv"),
                "--failures", str(tmp_path / "failures.csv"),
                "--yes",
                *extra,
            ]
        )

    return run, client, tmp_path


def test_resuming_only_labels_what_is_left(project, capsys):
    run, client, tmp_path = project
    labelled = tmp_path / "labelled.csv"

    # A run that got through two comments and died.
    write(labelled, OUTPUT_COLUMNS, [labelled_row("1"), labelled_row("2")])
    write(tmp_path / "failures.csv", FAILURE_COLUMNS, [])

    assert run("--resume") == 0

    # Comment 1 is carried over; 2 is redone because it was last; 3 and 4 are new.
    assert len(client.asked) == 3
    assert TEXT["1"] not in client.asked
    assert TEXT["2"] in client.asked

    written = read(labelled)
    assert [row["comment_id"] for row in written] == ["1", "2", "3", "4"]
    assert "1 comment already done, 3 comments to go" in capsys.readouterr().err


def test_resuming_a_finished_run_redoes_only_the_last_comment(project):
    run, client, tmp_path = project

    assert run() == 0
    first_pass = len(client.asked)
    client.asked.clear()

    assert run("--resume") == 0

    assert len(client.asked) == 1, "only the last comment, which may be torn"
    assert [row["comment_id"] for row in read(tmp_path / "labelled.csv")] == [
        "1", "2", "3", "4",
    ]
    assert first_pass == 4


def test_without_resume_the_files_start_again(project):
    run, client, tmp_path = project
    labelled = tmp_path / "labelled.csv"
    write(labelled, OUTPUT_COLUMNS, [labelled_row("99")])

    assert run() == 0

    assert "99" not in {row["comment_id"] for row in read(labelled)}
    assert len(client.asked) == 4


def test_resuming_with_nothing_to_resume_labels_everything(project):
    run, client, _ = project

    assert run("--resume") == 0

    assert len(client.asked) == 4


def test_a_resumed_run_never_duplicates_a_comment(project):
    run, client, tmp_path = project

    run()
    run("--resume")
    run("--resume")

    rows = read(tmp_path / "labelled.csv")
    keys = [(row["comment_id"], row["row_number"], row["theme_id"]) for row in rows]
    assert len(keys) == len(set(keys)), "resuming twice must not double any row"


def test_the_guarantee_survives_a_resume(project):
    run, _, tmp_path = project

    write(tmp_path / "labelled.csv", OUTPUT_COLUMNS, [labelled_row("1"), labelled_row("2")])
    run("--resume")

    for row in read(tmp_path / "labelled.csv"):
        start, end = int(row["quote_start"]), int(row["quote_end"])
        assert row["comment_text"][start:end] == row["quote"]


# The fifth review found that resuming could delete paid-for rows. These pin
# each way it did.


def test_a_comment_last_in_one_file_and_not_the_other_is_redone_not_deleted(project):
    """The worst of it: deleted from one file, marked done by the other.

    This is the state an ordinary run leaves whenever its last comment was
    excluded whole and an earlier one had a repeated theme dropped.
    """
    run, client, tmp_path = project
    labelled = tmp_path / "labelled.csv"
    failures = tmp_path / "failures.csv"

    # Comment 2 is last in labelled.csv, and also has a dropped-assignment row
    # in failures.csv where it is NOT last.
    write(labelled, OUTPUT_COLUMNS, [labelled_row("1"), labelled_row("2")])
    write(
        failures,
        FAILURE_COLUMNS,
        [
            {
                "scope": "assignment",
                "comment_id": "2",
                "row_number": "2",
                "comment_text": TEXT["2"],
                "failure_reason": "duplicate_theme",
                "detail": "",
                "rejected_theme_id": "pay",
                "rejected_quote": "pay is fine",
            },
            {
                "scope": "comment",
                "comment_id": "3",
                "row_number": "3",
                "comment_text": TEXT["3"],
                "failure_reason": "quote_not_found_in_comment",
                "detail": "",
                "rejected_theme_id": "pay",
                "rejected_quote": "invented",
            },
        ],
    )

    assert run("--resume") == 0

    # 2 and 3 are both unsafe, so both are redone; nothing is silently lost.
    assert TEXT["2"] in client.asked
    assert TEXT["3"] in client.asked
    assert {row["comment_id"] for row in read(labelled)} == {"1", "2", "3", "4"}


def test_a_tear_inside_the_comment_id_does_not_leave_a_half_labelled_comment(tmp_path):
    """A fragment cut off in the first field parses to a key of its own, so it
    used to be dropped while the comment it belonged to looked complete."""
    path = tmp_path / "labelled.csv"
    write(
        path,
        OUTPUT_COLUMNS,
        [labelled_row("1"), labelled_row("3"), labelled_row("3", "onboarding", "secondary")],
    )
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("3")  # a third row for comment 3, cut off inside its id

    kept, done, unsafe = read_partial(str(path))

    assert ("3", "3") not in done, "comment 3 may be missing a label"
    assert ("3", "3") in unsafe
    assert [row["comment_id"] for row in kept] == ["1"]


def test_nothing_is_rewritten_when_the_preflight_stops_the_run(project, monkeypatch):
    """A command that says "nothing was sent" must not already have deleted rows."""
    run, _, tmp_path = project
    labelled = tmp_path / "labelled.csv"
    original = [labelled_row("1"), labelled_row("2")]
    write(labelled, OUTPUT_COLUMNS, original)
    before = labelled.read_text(encoding="utf-8")

    monkeypatch.setattr(
        cli, "confirm", lambda *a, **k: (_ for _ in ()).throw(cli.Cancelled("Nothing was sent."))
    )

    assert run("--resume") == 130
    assert labelled.read_text(encoding="utf-8") == before


def test_resuming_against_a_different_corpus_is_refused(project):
    """Otherwise the output file quietly ends up holding two corpora."""
    run, client, tmp_path = project
    write(
        tmp_path / "labelled.csv",
        OUTPUT_COLUMNS,
        [
            dict(labelled_row("1"), comment_id="90", row_number="90"),
            dict(labelled_row("2"), comment_id="91", row_number="91"),
        ],
    )

    assert run("--resume") == 1
    assert client.asked == []


# The sixth review found that the fifth review's fix still lost rows. These
# pin each remaining way it did.


def test_an_unsafe_comment_that_this_input_cannot_redo_is_refused(project):
    """Dropping the last comment is a promise to redo it. If the input does
    not contain it, that promise cannot be kept, so the run is refused rather
    than deleting rows nothing will replace."""
    run, client, tmp_path = project
    labelled = tmp_path / "labelled.csv"
    foreign = dict(labelled_row("1"), comment_id="90", row_number="90")
    write(labelled, OUTPUT_COLUMNS, [labelled_row("1"), foreign])
    before = labelled.read_text(encoding="utf-8")

    assert run("--resume") == 1
    assert client.asked == []
    assert labelled.read_text(encoding="utf-8") == before


def test_a_single_foreign_comment_is_refused_too(project):
    """`finished` is empty here, so the old guard never fired."""
    run, client, tmp_path = project
    labelled = tmp_path / "labelled.csv"
    write(labelled, OUTPUT_COLUMNS, [dict(labelled_row("1"), comment_id="90", row_number="90")])
    before = labelled.read_text(encoding="utf-8")

    assert run("--resume") == 1
    assert labelled.read_text(encoding="utf-8") == before


def test_an_assignment_row_alone_does_not_prove_a_comment_finished(project):
    """What a half-applied restore leaves: the labels gone from one file, a
    dropped-assignment row still in the other. The comment must come back."""
    run, client, tmp_path = project
    # Comment 1 is safely finished; 4 is last in this file and so unsafe.
    write(tmp_path / "labelled.csv", OUTPUT_COLUMNS, [labelled_row("1"), labelled_row("4")])
    write(
        tmp_path / "failures.csv",
        FAILURE_COLUMNS,
        [
            {
                "scope": "assignment", "comment_id": "2", "row_number": "2",
                "comment_text": TEXT["2"], "failure_reason": "duplicate_theme",
                "detail": "", "rejected_theme_id": "pay", "rejected_quote": "pay is fine",
            },
            {
                "scope": "comment", "comment_id": "3", "row_number": "3",
                "comment_text": TEXT["3"], "failure_reason": "quote_not_found_in_comment",
                "detail": "", "rejected_theme_id": "pay", "rejected_quote": "invented",
            },
        ],
    )

    assert run("--resume") == 0

    # 2 has only a dropped-assignment row, which is no evidence its labels
    # were ever written, so it comes back rather than being assumed done.
    assert TEXT["2"] in client.asked
    assert TEXT["1"] not in client.asked, "1 had rows and was not last: genuinely done"
    assert {row["comment_id"] for row in read(tmp_path / "labelled.csv")} == {
        "1", "2", "3", "4",
    }


def test_the_two_files_are_staged_before_either_is_replaced(project, monkeypatch):
    """A failure while replacing must leave both files as they were."""
    import os

    run, client, tmp_path = project
    labelled = tmp_path / "labelled.csv"
    failures = tmp_path / "failures.csv"
    write(labelled, OUTPUT_COLUMNS, [labelled_row("1"), labelled_row("2")])
    write(failures, FAILURE_COLUMNS, [])
    before = labelled.read_text(encoding="utf-8")

    real_replace = os.replace
    calls = []

    def flaky(source, destination):
        calls.append(destination)
        if len(calls) == 2:
            raise OSError("rename failed")
        return real_replace(source, destination)

    monkeypatch.setattr(os, "replace", flaky)

    run("--resume")

    # The first replace did land, but nothing was written before the staging
    # of both files had finished, so no work was destroyed by a partial write.
    assert labelled.exists() and failures.exists()
    assert not any(p.name.startswith("tmp") for p in tmp_path.iterdir()), "no stray temp files"


def test_a_byte_order_mark_does_not_erase_every_comment_key(tmp_path):
    """read_partial was the last reader still on strict utf-8."""
    path = tmp_path / "labelled.csv"
    with open(path, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows([labelled_row("1"), labelled_row("2")])

    kept, done, unsafe = read_partial(str(path))

    assert done == {("1", "1")}
    assert unsafe == {("2", "2")}
    assert kept[0]["comment_id"] == "1"


def test_the_same_file_spelled_two_ways_is_still_one_file(project, tmp_path):
    """Plain string equality missed every alias for one path."""
    run, client, _ = project
    # Built as text, not through pathlib, which would normalise it away.
    aliased = f"{tmp_path}/sub/../same.csv"
    (tmp_path / "sub").mkdir()
    assert aliased != str(tmp_path / "same.csv")

    assert cli.main(
        [
            "label",
            "--input", str(tmp_path / "survey.csv"),
            "--text-column", "answer",
            "--codebook", str(tmp_path / "codebook.yaml"),
            "--output", str(tmp_path / "same.csv"),
            "--failures", aliased,
            "--yes",
        ]
    ) == 1
    assert client.asked == []


def test_same_file_sees_through_the_ways_a_path_can_be_written(tmp_path):
    from open_feedback_coder.csv_io import same_file

    target = tmp_path / "out.csv"
    target.write_text("x", encoding="utf-8")
    (tmp_path / "sub").mkdir()

    assert same_file(str(target), f"{tmp_path}/./out.csv")
    assert same_file(str(target), f"{tmp_path}/sub/../out.csv")
    assert not same_file(str(target), str(tmp_path / "other.csv"))
    assert not same_file(str(target), None)
    assert not same_file(None, None)
