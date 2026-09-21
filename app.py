"""Streamlit front end for open-feedback-coder.

Reading the CSV, validating the edited codebook, proposing and labelling all
go through the same functions the command line uses, so the two interfaces
cannot drift apart on what a placeholder answer is, what a valid theme id is,
or when a quote counts as verified.

Run with:  uv run --extra ui streamlit run app.py
"""

from __future__ import annotations

import csv
import io
import os

import pandas as pd
import streamlit as st

from open_feedback_coder import label as label_step, propose as propose_step
from open_feedback_coder.budget import TokenCounter
from open_feedback_coder.codebook import CodebookError, to_yaml
from open_feedback_coder.csv_io import (
    FAILURE_COLUMNS,
    OUTPUT_COLUMNS,
    InputError,
    column_names,
    decode_document,
    read_comments_from_text,
)
from open_feedback_coder.editing import codebook_from_records
from open_feedback_coder.llm import Client, ConfigError, ModelError
from open_feedback_coder.phrasing import plural

st.set_page_config(page_title="open-feedback-coder", layout="wide")

DEFAULT_MAX_THEMES = 15
DELIMITER_LABELS = {",": "comma", ";": "semicolon", "\t": "tab", "|": "pipe"}


def rows_to_csv(columns: list[str], rows: list[dict]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def forget(*keys: str) -> None:
    for key in keys:
        st.session_state.pop(key, None)


st.title("open-feedback-coder")
st.caption(
    "Propose a codebook from open-ended answers, edit it yourself, then label "
    "every comment with a theme, a valence and a quote checked against the "
    "source text."
)

with st.sidebar:
    st.header("Model")
    st.write(
        "Set `OPENAI_API_KEY` and `OPENAI_MODEL` in the environment before "
        "starting the app."
    )
    model_override = st.text_input(
        "Model id", value=os.environ.get("OPENAI_MODEL", ""), help="Overrides OPENAI_MODEL."
    )
    st.header("Cost estimate")
    st.caption("Optional. Without prices the app shows token counts only.")
    price_in = st.number_input("USD per 1M input tokens", min_value=0.0, value=0.0, step=0.01)
    price_out = st.number_input("USD per 1M output tokens", min_value=0.0, value=0.0, step=0.01)

st.subheader("1. Load comments")

upload = st.file_uploader("CSV file", type=["csv", "tsv", "txt"])
delimiter = st.selectbox(
    "Delimiter", options=list(DELIMITER_LABELS), format_func=DELIMITER_LABELS.get
)

if upload is None:
    st.stop()

try:
    document = decode_document(upload)
except UnicodeDecodeError:
    st.error(
        "This file is not UTF-8. Re-export it as UTF-8, or use the command "
        "line, which takes an --encoding option."
    )
    st.stop()

available = column_names(document, delimiter)
if not available:
    st.error("No columns found. Check the delimiter.")
    st.stop()

left, right = st.columns(2)
with left:
    text_column = st.selectbox("Column with the open-ended answers", options=available)
with right:
    chosen_id = st.selectbox("Comment id column (optional)", options=["(row number)"] + available)
id_column = None if chosen_id == "(row number)" else chosen_id

try:
    comments, skipped = read_comments_from_text(
        document,
        text_column=text_column,
        id_column=id_column,
        delimiter=delimiter,
        origin="the uploaded file",
    )
except InputError as error:
    st.error(str(error))
    st.stop()

# A proposal and a set of results mean something only for the corpus they were
# computed from. Streamlit reruns the whole script on every interaction and
# carries session state across those reruns, so without this the page would go
# on showing an old run under a new file.
input_fingerprint = (
    getattr(upload, "file_id", None) or upload.name,
    len(document),
    delimiter,
    text_column,
    id_column,
)
if st.session_state.get("input_fingerprint") != input_fingerprint:
    st.session_state["input_fingerprint"] = input_fingerprint
    forget("proposal", "run")

if not comments:
    st.error("No usable comments in that column.")
    st.stop()

message = f"**{len(comments):,}** comments ready."
if skipped.total:
    message += (
        f" {skipped.total:,} rows skipped before any model call "
        f"({skipped.empty} empty, {skipped.placeholder} placeholder)."
    )
st.success(message)

with st.expander("Preview"):
    st.dataframe(
        pd.DataFrame(
            [{"id": c.comment_id, "row": c.row_number, "text": c.text} for c in comments[:25]]
        ),
        width="stretch",
        hide_index=True,
    )

st.subheader("2. Propose a codebook")

max_themes = st.slider("Maximum themes to propose", 3, 40, DEFAULT_MAX_THEMES)

try:
    client = Client(model=model_override or None)
except ConfigError as error:
    st.error(str(error))
    st.stop()

counter = TokenCounter(client.model)
estimate = propose_step.build_estimate(
    comments, counter, max_themes, price_in or None, price_out or None
)
st.code(estimate.render(), language="text")

if st.button("Propose codebook", type="primary"):
    with st.spinner("Reading the whole corpus..."):
        try:
            st.session_state["proposal"] = propose_step.propose(comments, client, max_themes)
        except ModelError as error:
            st.error(str(error))
            st.stop()
    forget("run")

result = st.session_state.get("proposal")
if result is None:
    st.stop()

st.info(
    f"{len(result.codebook.themes)} themes proposed. "
    f"{sum(len(t.examples) for t in result.codebook.themes)} example quotes verified "
    f"against the comments they came from, {len(result.rejected_examples)} rejected "
    "and dropped."
)

st.subheader("3. Edit the codebook")
st.caption(
    "Rename, rewrite, merge and delete. Only the themes left here are used for "
    "labelling, and nothing is labelled until you press the button below. An id "
    "is lowercase letters, digits and underscores, and no two themes may share one."
)

edited = st.data_editor(
    pd.DataFrame(
        [
            {"id": theme.id, "label": theme.label, "description": theme.description}
            for theme in result.codebook.themes
        ]
    ),
    num_rows="dynamic",
    width="stretch",
    hide_index=True,
    key="codebook_editor",
)

for theme in result.codebook.themes:
    if theme.examples:
        with st.expander(f"Examples for {theme.label}"):
            for example in theme.examples:
                st.markdown(f"> {example.quote}")
                st.caption(f"from comment {example.comment_id}")

# Exactly the checks `ofc label` runs when it loads codebook.yaml, so this page
# cannot label against a codebook the command line would refuse, and the file
# it offers for download is one the command line will accept.
try:
    approved = codebook_from_records(edited.to_dict("records"))
except CodebookError as error:
    st.error(str(error))
    st.stop()

# Results computed against a different set of themes are no longer results.
codebook_fingerprint = tuple(
    (theme.id, theme.label, theme.description) for theme in approved.themes
)
if st.session_state.get("codebook_fingerprint") != codebook_fingerprint:
    st.session_state["codebook_fingerprint"] = codebook_fingerprint
    forget("run")

st.download_button(
    "Download codebook.yaml", to_yaml(approved), file_name="codebook.yaml", mime="text/yaml"
)

st.subheader("4. Label the comments")

concurrency = st.slider("Comments in flight at once", 1, 16, 4)
label_estimate = label_step.build_estimate(
    comments, approved, counter, price_in or None, price_out or None
)
st.code(label_estimate.render(), language="text")

if st.button("Label comments", type="primary"):
    progress = st.progress(0.0, text="Labelling...")

    def on_progress(done: int, total: int) -> None:
        progress.progress(done / total, text=f"Labelling {done:,}/{total:,}")

    st.session_state["run"] = label_step.label_comments(
        comments, approved, client, concurrency=concurrency, on_progress=on_progress
    )
    progress.empty()

run = st.session_state.get("run")
if run is None:
    st.stop()

st.subheader("5. Results")

first, second, third = st.columns(3)
first.metric("Comments labelled", f"{run.comments_labelled:,}")
second.metric("Unassigned", f"{run.comments_unassigned:,}")
third.metric("Excluded after checking", f"{run.comments_failed:,}")

st.caption(
    "These three counts were tallied in code as the run went, not estimated by "
    "the model. Every quote below is a verbatim span of the comment beside it."
)

# The table and the download are built from one variable, so what is shown
# and what is saved cannot drift apart.
labelled_rows = run.rows
st.dataframe(pd.DataFrame(labelled_rows), width="stretch", hide_index=True)
st.download_button(
    "Download labelled.csv",
    rows_to_csv(OUTPUT_COLUMNS, labelled_rows),
    file_name="labelled.csv",
    mime="text/csv",
)

rejection_rows = run.rejections
if rejection_rows:
    dropped = run.dropped_assignments
    notes = []
    if run.comments_failed:
        notes.append(
            f"{plural(run.comments_failed, 'comment')} excluded because a check failed"
        )
    if dropped:
        affected = len({entry["comment_id"] for entry in dropped})
        notes.append(
            f"{plural(len(dropped), 'repeated theme assignment')} dropped from "
            f"{plural(affected, 'comment')} that stayed in the results"
        )
    st.warning(". ".join(note[0].upper() + note[1:] for note in notes) + ".")
    st.caption(
        "The `scope` column says which happened: `comment` for a comment kept out "
        "of the results, `assignment` for one label removed from a comment that is "
        "still in them."
    )
    st.dataframe(pd.DataFrame(rejection_rows), width="stretch", hide_index=True)
    st.download_button(
        "Download labelling_failures.csv",
        rows_to_csv(FAILURE_COLUMNS, rejection_rows),
        file_name="labelling_failures.csv",
        mime="text/csv",
    )
