"""Streamlit front end for open-feedback-coder.

The same functions the command line uses are called here, so the checks and
the guarantees are identical: the codebook is edited by a person before any
labelling happens, and a label whose quote cannot be found in its comment is
excluded from the results and listed separately.

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
from open_feedback_coder.codebook import CodebookError, from_dict, to_yaml
from open_feedback_coder.csv_io import (
    FAILURE_COLUMNS,
    OUTPUT_COLUMNS,
    PLACEHOLDER_ANSWERS,
    Comment,
    SkipReport,
)
from open_feedback_coder.llm import Client, ConfigError, ModelError
from open_feedback_coder.text import canonicalise

st.set_page_config(page_title="open-feedback-coder", layout="wide")

DEFAULT_MAX_THEMES = 15


def read_uploaded(upload, text_column: str, id_column: str | None, delimiter: str):
    """Read comments from an uploaded file, mirroring csv_io.read_comments."""
    upload.seek(0)
    text = io.TextIOWrapper(upload, encoding="utf-8-sig", newline="")
    reader = csv.DictReader(text, delimiter=delimiter)

    comments: list[Comment] = []
    skipped = SkipReport()

    for row_number, row in enumerate(reader, start=1):
        value = canonicalise(row.get(text_column) or "").strip()
        if not value:
            skipped.empty += 1
            continue
        if value.lower() in PLACEHOLDER_ANSWERS:
            skipped.placeholder += 1
            key = value.lower()
            skipped.placeholder_values[key] = skipped.placeholder_values.get(key, 0) + 1
            continue

        comment_id = str(row_number)
        if id_column:
            comment_id = canonicalise(row.get(id_column) or "").strip() or str(row_number)

        comments.append(Comment(comment_id=comment_id, row_number=row_number, text=value))

    return comments, skipped


def column_names(upload, delimiter: str) -> list[str]:
    upload.seek(0)
    text = io.TextIOWrapper(upload, encoding="utf-8-sig", newline="")
    reader = csv.reader(text, delimiter=delimiter)
    try:
        return [name for name in next(reader) if name]
    except StopIteration:
        return []


def rows_to_csv(columns: list[str], rows: list[dict]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def get_client(model_override: str | None) -> Client | None:
    try:
        return Client(model=model_override or None)
    except ConfigError as error:
        st.error(str(error))
        return None


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
delimiter = st.selectbox("Delimiter", options=[",", ";", "\t"], format_func=lambda d: {",": "comma", ";": "semicolon", "\t": "tab"}[d])

if upload is None:
    st.stop()

available = column_names(upload, delimiter)
if not available:
    st.error("No columns found. Check the delimiter.")
    st.stop()

left, right = st.columns(2)
with left:
    text_column = st.selectbox("Column with the open-ended answers", options=available)
with right:
    id_column = st.selectbox("Comment id column (optional)", options=["(row number)"] + available)
id_column = None if id_column == "(row number)" else id_column

comments, skipped = read_uploaded(upload, text_column, id_column, delimiter)

# A proposal and a set of results only mean anything for the corpus they were
# computed from. Streamlit reruns the whole script on every interaction and
# keeps session state across those reruns, so without this the page would go
# on showing an old run under a new file.
input_fingerprint = (
    getattr(upload, "file_id", None) or upload.name,
    getattr(upload, "size", None),
    delimiter,
    text_column,
    id_column,
)
if st.session_state.get("input_fingerprint") != input_fingerprint:
    st.session_state["input_fingerprint"] = input_fingerprint
    st.session_state.pop("proposal", None)
    st.session_state.pop("run", None)

if not comments:
    st.error("No usable comments in that column.")
    st.stop()

message = f"**{len(comments):,}** comments ready."
if skipped.total:
    message += f" {skipped.total:,} rows skipped before any model call ({skipped.empty} empty, {skipped.placeholder} placeholder)."
st.success(message)

with st.expander("Preview"):
    st.dataframe(
        pd.DataFrame(
            [{"id": c.comment_id, "row": c.row_number, "text": c.text} for c in comments[:25]]
        ),
        use_container_width=True,
        hide_index=True,
    )

st.subheader("2. Propose a codebook")

max_themes = st.slider("Maximum themes to propose", 3, 40, DEFAULT_MAX_THEMES)

client = get_client(model_override)
if client is None:
    st.stop()

counter = TokenCounter(client.model)
estimate = propose_step.build_estimate(
    comments, counter, max_themes, price_in or None, price_out or None
)
st.code(estimate.render(), language="text")

if st.button("Propose codebook", type="primary"):
    with st.spinner("Reading the whole corpus..."):
        try:
            result = propose_step.propose(comments, client, max_themes)
        except ModelError as error:
            st.error(str(error))
            st.stop()
    st.session_state["proposal"] = result
    st.session_state.pop("run", None)

result = st.session_state.get("proposal")
if result is None:
    st.stop()

st.info(
    f"{len(result.codebook.themes)} themes proposed. "
    f"{sum(len(t.examples) for t in result.codebook.themes)} example quotes verified "
    f"against the source comments, {len(result.rejected_examples)} rejected and dropped."
)

st.subheader("3. Edit the codebook")
st.caption(
    "Rename, rewrite, merge and delete. Only the themes left here are used for "
    "labelling. Nothing is labelled until you press the button below. An id is "
    "lowercase letters, digits and underscores, and no two themes may share one."
)

editable = pd.DataFrame(
    [
        {"id": theme.id, "label": theme.label, "description": theme.description}
        for theme in result.codebook.themes
    ]
)
edited = st.data_editor(
    editable, num_rows="dynamic", use_container_width=True, hide_index=True, key="codebook_editor"
)

for theme in result.codebook.themes:
    if theme.examples:
        with st.expander(f"Examples for “{theme.label}”"):
            for example in theme.examples:
                st.markdown(f"> {example.quote}  \n<sub>comment {example.comment_id}</sub>", unsafe_allow_html=True)

edited_rows = [
    {
        "id": str(row.get("id") or "").strip(),
        "label": str(row.get("label") or "").strip(),
        "description": str(row.get("description") or "").strip(),
    }
    for _, row in edited.iterrows()
    if str(row.get("id") or "").strip() or str(row.get("label") or "").strip()
]

if not edited_rows:
    st.warning("Keep at least one theme to continue.")
    st.stop()

# Exactly the checks `ofc label` runs when it loads codebook.yaml, so the file
# this page offers for download is one the command line will accept, and the
# app cannot label against a codebook the CLI would refuse.
try:
    approved = from_dict({"themes": edited_rows}, origin="the table above")
except CodebookError as error:
    st.error(str(error))
    st.stop()

# Results computed against a different set of themes are no longer results.
codebook_fingerprint = tuple(
    (theme.id, theme.label, theme.description) for theme in approved.themes
)
if st.session_state.get("codebook_fingerprint") != codebook_fingerprint:
    st.session_state["codebook_fingerprint"] = codebook_fingerprint
    st.session_state.pop("run", None)

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

    run = label_step.label_comments(
        comments, approved, client, concurrency=concurrency, on_progress=on_progress
    )
    progress.empty()
    st.session_state["run"] = run

run = st.session_state.get("run")
if run is None:
    st.stop()

st.subheader("5. Results")

a, b, c = st.columns(3)
a.metric("Comments labelled", f"{run.comments_labelled:,}")
b.metric("Unassigned", f"{run.comments_unassigned:,}")
c.metric("Excluded after checking", f"{run.comments_failed:,}")

st.caption(
    "These three counts were tallied in code as the run went, not estimated by "
    "the model. Every quote below is a verbatim span of the comment beside it."
)

st.dataframe(pd.DataFrame(run.rows), use_container_width=True, hide_index=True)
st.download_button(
    "Download labelled.csv",
    rows_to_csv(OUTPUT_COLUMNS, run.rows),
    file_name="labelled.csv",
    mime="text/csv",
)

if run.failures:
    st.warning(f"{len(run.failures):,} comments were excluded because a check failed.")
    st.dataframe(pd.DataFrame(run.failures), use_container_width=True, hide_index=True)
    st.download_button(
        "Download labelling_failures.csv",
        rows_to_csv(FAILURE_COLUMNS, run.failures),
        file_name="labelling_failures.csv",
        mime="text/csv",
    )
