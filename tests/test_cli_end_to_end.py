"""An end-to-end run of both commands against a stand-in for the model.

Nothing here touches the network. The point is to check the files that land
on disk, in particular that the guarantee still holds after a full round trip
through CSV writing and reading: every quote in labelled.csv is the exact
substring of the comment_text beside it at the offsets recorded in that row.
"""

import csv

import pytest
import yaml

from open_feedback_coder import cli
from open_feedback_coder.llm import Usage

COMMENTS = {
    "1": "Onboarding took three weeks longer than promised and nobody owned it.",
    "2": "The pay is genuinely competitive.",
    "3": "My manager shielded me from the worst of it, but the workload is relentless.",
    "4": "Thanks for running this survey.",
    "5": "Onboarding was fine actually.",
}

# Comment 3 gets a quote that is faithful in meaning and absent from the text.
# It is the case the whole pipeline exists to catch.
LABELS = {
    "1": {
        "unassigned": False,
        "assignments": [
            {
                "theme_id": "onboarding",
                "role": "primary",
                "valence": "negative",
                "quote": "nobody owned it",
            }
        ],
    },
    "2": {
        "unassigned": False,
        "assignments": [
            {
                "theme_id": "pay",
                "role": "primary",
                "valence": "positive",
                "quote": "pay is genuinely competitive",
            }
        ],
    },
    "3": {
        "unassigned": False,
        "assignments": [
            {
                "theme_id": "workload",
                "role": "primary",
                "valence": "negative",
                "quote": "the workload never stops",
            }
        ],
    },
    "4": {"unassigned": True, "assignments": []},
    "5": {
        "unassigned": False,
        "assignments": [
            {
                "theme_id": "onboarding",
                "role": "primary",
                "valence": "positive",
                "quote": "Onboarding was fine actually",
            }
        ],
    },
}

PROPOSAL = {
    "themes": [
        {
            "id": "Onboarding & Ramp-up",
            "label": "Onboarding",
            "description": "Getting set up in the first weeks.",
            "examples": [
                {"comment_id": "1", "quote": "nobody owned it"},
                {"comment_id": "5", "quote": "Onboarding was fine actually"},
            ],
        },
        {
            "id": "pay",
            "label": "Pay and benefits",
            "description": "Salary and benefits.",
            "examples": [{"comment_id": "2", "quote": "pay is genuinely competitive"}],
        },
        {
            "id": "workload",
            "label": "Workload",
            "description": "How much work there is.",
            "examples": [{"comment_id": "3", "quote": "the workload is relentless"}],
        },
        {
            "id": "gratitude",
            "label": "Gratitude",
            "description": "Thanks for the survey.",
            # Invented: this wording appears in no comment, so it must be dropped.
            "examples": [{"comment_id": "4", "quote": "thanks a lot for the survey"}],
        },
    ]
}


class StubClient:
    """Answers with canned responses, keyed by which schema was asked for."""

    def __init__(self, *args, **kwargs):
        self.model = "stub-model"
        self.calls = 0

    def complete_json(self, system, user, schema, schema_name):
        self.calls += 1
        if schema_name == "codebook_proposal":
            return PROPOSAL, Usage(input_tokens=100, output_tokens=50)

        comment_text = user.split("Comment:\n", 1)[1]
        for comment_id, text in COMMENTS.items():
            if text == comment_text:
                return LABELS[comment_id], Usage(input_tokens=20, output_tokens=10)
        raise AssertionError(f"unexpected comment sent to the model: {comment_text!r}")


class StubCounter:
    """Stands in for tiktoken so the suite never reaches the network."""

    is_exact = True

    def __init__(self, model):
        self.model = model

    def count(self, text: str) -> int:
        return max(1, len(text) // 4)


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "Client", StubClient)
    monkeypatch.setattr(cli, "TokenCounter", StubCounter)

    input_path = tmp_path / "survey.csv"
    with open(input_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["respondent", "open_answer"])
        writer.writeheader()
        for comment_id, text in COMMENTS.items():
            writer.writerow({"respondent": comment_id, "open_answer": text})
        writer.writerow({"respondent": "6", "open_answer": ""})
        writer.writerow({"respondent": "7", "open_answer": "N/A"})

    return tmp_path, str(input_path)


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_propose_then_label(project, capsys):
    tmp_path, input_path = project
    codebook_path = tmp_path / "codebook.yaml"
    output_path = tmp_path / "labelled.csv"
    failures_path = tmp_path / "failures.csv"

    exit_code = cli.main(
        [
            "propose",
            "--input", input_path,
            "--text-column", "open_answer",
            "--id-column", "respondent",
            "--output", str(codebook_path),
            "--yes",
        ]
    )
    assert exit_code == 0

    document = yaml.safe_load(codebook_path.read_text(encoding="utf-8"))
    ids = [theme["id"] for theme in document["themes"]]
    assert ids == ["onboarding_ramp_up", "pay", "workload", "gratitude"]
    assert document["source"]["comments_analysed"] == 5
    assert document["source"]["rows_skipped"] == 2

    # The file you edit is short: three fields per theme and no quotes.
    assert all(set(theme) == {"id", "label", "description"} for theme in document["themes"])

    # The quotes are in the companion document, and only the verified ones.
    evidence = (tmp_path / "codebook.evidence.md").read_text(encoding="utf-8")
    assert "nobody owned it" in evidence
    assert "Onboarding was fine actually" in evidence
    assert "thanks a lot for the survey" not in evidence, "invented quote must not appear"
    assert "No example survived checking" in evidence, "the gratitude theme had none"

    # Stand in for the person editing the file: rename the repaired id to the
    # one the labeller will use, and delete a theme.
    document["themes"] = [t for t in document["themes"] if t["id"] != "gratitude"]
    for theme in document["themes"]:
        if theme["id"] == "onboarding_ramp_up":
            theme["id"] = "onboarding"
            theme["label"] = "Getting started"
    codebook_path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")

    exit_code = cli.main(
        [
            "label",
            "--input", input_path,
            "--text-column", "open_answer",
            "--id-column", "respondent",
            "--codebook", str(codebook_path),
            "--output", str(output_path),
            "--failures", str(failures_path),
            "--concurrency", "2",
            "--yes",
        ]
    )
    assert exit_code == 0

    # The run says out loud what the person changed, so the approval is on the
    # record rather than asserted.
    summary = capsys.readouterr().err
    assert "4 proposed" in summary
    assert "1 relabelled" in summary
    assert "1 deleted" in summary

    rows = read_csv(output_path)
    failures = read_csv(failures_path)

    # Every labelled row carries a quote that is exactly the span it claims.
    for row in rows:
        if row["assignment"] == "unassigned":
            assert row["quote"] == ""
            continue
        start, end = int(row["quote_start"]), int(row["quote_end"])
        assert row["comment_text"][start:end] == row["quote"] != ""

    labelled_ids = {row["comment_id"] for row in rows}
    assert labelled_ids == {"1", "2", "4", "5"}
    assert "3" not in labelled_ids, "the comment with an invented quote must be excluded"

    assert len(failures) == 1
    assert failures[0]["comment_id"] == "3"
    assert failures[0]["failure_reason"] == "quote_not_found_in_comment"
    assert failures[0]["rejected_quote"] == "the workload never stops"

    unassigned = [row for row in rows if row["assignment"] == "unassigned"]
    assert [row["comment_id"] for row in unassigned] == ["4"]


def test_label_refuses_a_codebook_with_no_themes(project, tmp_path):
    _, input_path = project
    codebook_path = tmp_path / "empty.yaml"
    codebook_path.write_text("version: 1\nthemes: []\n", encoding="utf-8")

    exit_code = cli.main(
        [
            "label",
            "--input", input_path,
            "--text-column", "open_answer",
            "--codebook", str(codebook_path),
            "--yes",
        ]
    )

    assert exit_code == 1


def test_a_missing_column_fails_before_any_model_call(project, tmp_path):
    _, input_path = project

    exit_code = cli.main(
        ["propose", "--input", input_path, "--text-column", "comentario", "--yes"]
    )

    assert exit_code == 1
