"""Tests that drive the Streamlit app itself.

The app used to fail on every upload: it wrapped the uploaded file in an
io.TextIOWrapper, which closes the underlying buffer when it is collected, so
the next read of the same upload raised. Nothing caught it, because the app
had no tests and a screenshot of the page before the upload looks fine.

These run the real app.py in-process with the model client stubbed out. They
are skipped when the optional `ui` extra is not installed.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

pytest.importorskip("streamlit", reason="the Streamlit interface is an optional extra")

import streamlit  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402

import open_feedback_coder.budget as budget  # noqa: E402
import open_feedback_coder.llm as llm  # noqa: E402
from open_feedback_coder.llm import Usage  # noqa: E402

APP = str(Path(__file__).resolve().parent.parent / "app.py")

SURVEY = (
    b"respondent,open_answer\r\n"
    b"A1,Onboarding dragged on and nobody owned it.\r\n"
    b"A2,\r\n"
    b"A3,N/A\r\n"
    b"A4,The pay is genuinely competitive.\r\n"
)

PROPOSAL = {
    "themes": [
        {
            "id": "onboarding",
            "label": "Onboarding",
            "description": "Getting started.",
            "examples": [{"comment_id": "1", "quote": "nobody owned it"}],
        },
        {
            "id": "pay",
            "label": "Pay",
            "description": "Money.",
            "examples": [{"comment_id": "4", "quote": "genuinely competitive"}],
        },
    ]
}


class Upload(io.BytesIO):
    """Stands in for a Streamlit UploadedFile, which is also a BytesIO."""

    name = "survey.csv"
    file_id = "fid-1"
    type = "text/csv"


# Comment A1 names the same theme twice: the repeat is dropped, the comment
# stays. A4 is clean. Between them they drive the whole results section.
LABELS = {
    "Onboarding dragged on and nobody owned it.": {
        "unassigned": False,
        "assignments": [
            {"theme_id": "onboarding", "role": "primary", "valence": "negative",
             "quote": "nobody owned it"},
            {"theme_id": "onboarding", "role": "secondary", "valence": "negative",
             "quote": "Onboarding dragged on"},
        ],
    },
    "The pay is genuinely competitive.": {
        "unassigned": False,
        "assignments": [
            {"theme_id": "pay", "role": "primary", "valence": "positive",
             "quote": "genuinely competitive"},
        ],
    },
}


class StubClient:
    def __init__(self, *args, **kwargs):
        self.model = "stub-model"

    def complete_json(self, system, user, schema, schema_name):
        if schema_name == "codebook_proposal":
            return PROPOSAL, Usage(input_tokens=100, output_tokens=50)
        return LABELS[user.split("Comment:\n", 1)[1]], Usage(input_tokens=10, output_tokens=5)


class StubCounter:
    """Stands in for tiktoken so the suite never reaches the network."""

    is_exact = True

    def __init__(self, model):
        self.model = model

    def count(self, text: str) -> int:
        return max(1, len(text) // 4)


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    monkeypatch.setenv("OPENAI_MODEL", "stub-model")
    upload = Upload(SURVEY)
    # Two uploaders now: the comments, and optionally an existing codebook.
    # They are told apart by their label, as a person would.
    uploads = {"csv": upload, "codebook": None}

    def uploader(label, *args, **kwargs):
        return uploads["codebook"] if "codebook" in label.lower() else uploads["csv"]

    monkeypatch.setattr(streamlit, "file_uploader", uploader)
    monkeypatch.setattr(budget, "TokenCounter", StubCounter)
    monkeypatch.setattr(llm, "Client", StubClient)

    harness = AppTest.from_file(APP, default_timeout=60)
    harness.upload = upload
    harness.uploads = uploads
    return harness


def test_an_upload_is_read_and_stays_readable(app):
    """The regression: reading the columns must not close the upload."""
    app.run()

    assert not app.exception, app.exception
    assert [error.value for error in app.error] == []
    assert not app.upload.closed
    assert "2. Get a codebook" in [s.value for s in app.subheader]


def test_a_rerun_reads_the_same_upload_again(app):
    """Streamlit hands back the same file object on every interaction."""
    app.run()
    first = [s.value for s in app.success]

    app.run()

    assert not app.exception, app.exception
    assert [s.value for s in app.success] == first


def test_choosing_the_text_column_applies_the_shared_skip_rules(app):
    app.run()

    app.selectbox[1].set_value("open_answer").run()

    assert not app.exception, app.exception
    message = app.success[0].value
    assert "**2** comments ready." in message
    assert "2 rows skipped" in message
    assert "1 empty" in message
    assert "1 placeholder" in message


def test_proposing_reaches_the_editing_step(app):
    app.run()
    app.selectbox[1].set_value("open_answer").run()

    app.button[0].click().run()

    assert not app.exception, app.exception
    assert "3. Edit the codebook" in [s.value for s in app.subheader]
    assert "2 themes proposed" in app.info[0].value


def test_a_duplicate_theme_id_in_the_table_is_refused(app):
    """The app must not label against a codebook its own CLI would reject."""
    app.run()
    app.selectbox[1].set_value("open_answer").run()
    app.button[0].click().run()

    app.session_state["codebook_editor"] = {
        "edited_rows": {1: {"id": "onboarding"}},
        "added_rows": [],
        "deleted_rows": [],
    }
    app.run()

    assert not app.exception, app.exception
    assert any("more than once" in error.value for error in app.error), [
        error.value for error in app.error
    ]
    assert "4. Label the comments" not in [s.value for s in app.subheader]


def test_a_half_filled_new_row_is_refused_rather_than_named_nan(app):
    """A blank cell must not become a theme literally called "nan"."""
    app.run()
    app.selectbox[1].set_value("open_answer").run()
    app.button[0].click().run()

    app.session_state["codebook_editor"] = {
        "edited_rows": {},
        "added_rows": [{"label": "Something I started typing"}],
        "deleted_rows": [],
    }
    app.run()

    assert not app.exception, app.exception
    assert any("has no 'id'" in error.value for error in app.error), [
        error.value for error in app.error
    ]


def labelled(app):
    """Drive the app all the way to its results section."""
    app.run()
    app.selectbox[1].set_value("open_answer").run()
    app.button[0].click().run()
    app.button[1].click().run()
    assert not app.exception, app.exception
    return app


def test_the_app_reaches_its_results(app):
    labelled(app)

    assert "5. Results" in [s.value for s in app.subheader]
    assert [(m.label, m.value) for m in app.metric] == [
        ("Comments labelled", "2"),
        ("Unassigned", "0"),
        ("Excluded after checking", "0"),
    ]


def test_every_quote_shown_is_a_span_of_the_comment_beside_it(app):
    labelled(app)

    table = next(d.value for d in app.dataframe if "quote_start" in d.value.columns)
    assert len(table) == 2
    for _, row in table.iterrows():
        start, end = int(row["quote_start"]), int(row["quote_end"])
        assert row["comment_text"][start:end] == row["quote"] != ""


def test_a_dropped_repeat_is_shown_with_its_scope(app):
    """The app must report a drop, not quietly keep it to itself."""
    labelled(app)

    table = next(d.value for d in app.dataframe if "scope" in d.value.columns)
    assert list(table["scope"]) == ["assignment"]
    assert list(table["failure_reason"]) == ["duplicate_theme"]
    assert list(table["comment_id"]) == ["1"]

    warning = app.warning[0].value
    assert "1 repeated theme assignment dropped from 1 comment" in warning
    assert "assignments" not in warning, "a count of one should not be plural"


def test_the_results_survive_nothing_that_should_invalidate_them(app):
    """Re-running with no interaction leaves the same results in place."""
    labelled(app)
    first = [(m.label, m.value) for m in app.metric]

    app.run()

    assert [(m.label, m.value) for m in app.metric] == first


class CodebookUpload(io.BytesIO):
    name = "codebook.yaml"
    file_id = "cb-1"
    type = "text/yaml"


CODEBOOK_YAML = b"""
version: 1
themes:
  - id: hybrid_work
    label: Hybrid work
    description: Where people work from.
  - id: tooling
    label: Tooling
    description: The software people are given.
"""


def test_a_codebook_made_elsewhere_can_be_brought_in(app):
    """`ofc propose` on the command line, edited here instead of in a text editor."""
    app.uploads["codebook"] = CodebookUpload(CODEBOOK_YAML)

    app.run()
    app.selectbox[1].set_value("open_answer").run()

    assert not app.exception, app.exception
    assert any("Using the 2 themes in codebook.yaml" in s.value for s in app.success)
    assert "3. Edit the codebook" in [s.value for s in app.subheader]
    # No proposal was made, so nothing was sent for that step.
    assert app.button[0].label == "Label comments"

    editor = next(d.value for d in app.dataframe if "label" in d.value.columns)
    assert list(editor["id"]) == ["hybrid_work", "tooling"]


def test_a_brought_in_codebook_still_faces_the_loader(app):
    app.uploads["codebook"] = CodebookUpload(b"themes:\n  - id: Bad Id\n    label: Nope\n")

    app.run()
    app.selectbox[1].set_value("open_answer").run()

    assert not app.exception, app.exception
    assert any("lowercase" in error.value for error in app.error)
    assert "3. Edit the codebook" not in [s.value for s in app.subheader]


def test_a_file_that_is_not_a_codebook_is_refused(app):
    app.uploads["codebook"] = CodebookUpload(b"just: some\nrandom: mapping\n")

    app.run()
    app.selectbox[1].set_value("open_answer").run()

    assert not app.exception, app.exception
    assert any("not a codebook" in error.value for error in app.error)


CODEBOOK_WITH_RECORD = b"""
version: 1
source:
  model: some-model
  proposed:
    - id: hybrid_work
      label: Hybrid work
      description: Where people work from.
themes:
  - id: hybrid_work
    label: Hybrid work
    description: Where people work from.
"""


def test_a_brought_in_codebook_keeps_its_run_record(app):
    """It used to be dropped, so a codebook proposed on the command line came
    back from the app with nothing for `ofc label` to compare against."""
    app.uploads["codebook"] = CodebookUpload(CODEBOOK_WITH_RECORD)

    app.run()
    app.selectbox[1].set_value("open_answer").run()

    assert not app.exception, app.exception
    captions = [c.value for c in app.caption]
    assert any("keeps the record of the run that proposed it" in c for c in captions)


def test_a_codebook_with_no_record_says_so(app):
    app.uploads["codebook"] = CodebookUpload(CODEBOOK_YAML)

    app.run()
    app.selectbox[1].set_value("open_answer").run()

    captions = [c.value for c in app.caption]
    assert any("carries no proposal record" in c for c in captions)


def test_a_codebook_proposed_in_the_app_carries_a_record(app):
    app.run()
    app.selectbox[1].set_value("open_answer").run()
    app.button[0].click().run()

    captions = [c.value for c in app.caption]
    assert any("keeps the record of the run that proposed it" in c for c in captions)
