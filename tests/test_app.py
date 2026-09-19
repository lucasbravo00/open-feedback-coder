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


class StubClient:
    def __init__(self, *args, **kwargs):
        self.model = "stub-model"

    def complete_json(self, system, user, schema, schema_name):
        assert schema_name == "codebook_proposal"
        return PROPOSAL, Usage(input_tokens=100, output_tokens=50)


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
    monkeypatch.setattr(streamlit, "file_uploader", lambda *a, **k: upload)
    monkeypatch.setattr(budget, "TokenCounter", StubCounter)
    monkeypatch.setattr(llm, "Client", StubClient)

    harness = AppTest.from_file(APP, default_timeout=60)
    harness.upload = upload
    return harness


def test_an_upload_is_read_and_stays_readable(app):
    """The regression: reading the columns must not close the upload."""
    app.run()

    assert not app.exception, app.exception
    assert [error.value for error in app.error] == []
    assert not app.upload.closed
    assert "2. Propose a codebook" in [s.value for s in app.subheader]


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
