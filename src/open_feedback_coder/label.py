"""Step two: assign each comment to themes from the approved codebook.

The rule this module enforces is the reason the project exists: a label is
written to the output only if the quote that supports it was found, character
for character, inside the comment it came from. Checking happens here, in
code, on every label, and a comment that fails any check is written to the
failures file instead of the output file.

Checking is all or nothing per comment. If one of a comment's three quotes
cannot be located, the whole comment is excluded rather than published with
its surviving labels: a partially verified row looks exactly like a verified
one once it is in a spreadsheet.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .budget import ASSUMED_OUTPUT_TOKENS_PER_COMMENT, Estimate, TokenCounter
from .codebook import Codebook
from .llm import Client, ModelError, Usage
from .prompts import (
    LABEL_SCHEMA,
    LABEL_SYSTEM,
    MAX_SECONDARY_THEMES,
    VALENCES,
    render_codebook,
)
from .text import locate_quote


def _user_message(codebook_text: str, comment_text: str) -> str:
    return f"Codebook:\n{codebook_text}\n\nComment:\n{comment_text}"


@dataclass
class CommentOutcome:
    """The result of checking one comment's labels."""

    rows: list[dict] = field(default_factory=list)
    failure: dict | None = None
    usage: Usage = field(default_factory=Usage)

    @property
    def failed(self) -> bool:
        return self.failure is not None


@dataclass
class LabelRun:
    """Everything produced by labelling a corpus."""

    rows: list[dict] = field(default_factory=list)
    failures: list[dict] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    comments_labelled: int = 0
    comments_unassigned: int = 0

    @property
    def comments_failed(self) -> int:
        return len(self.failures)


def build_estimate(
    comments,
    codebook: Codebook,
    counter: TokenCounter,
    price_in: float | None = None,
    price_out: float | None = None,
) -> Estimate:
    """Measure what labelling will send, one call per comment."""
    codebook_text = render_codebook(codebook)
    system_tokens = counter.count(LABEL_SYSTEM)
    codebook_tokens = counter.count(codebook_text)

    input_tokens = sum(
        system_tokens + codebook_tokens + counter.count(comment.text) for comment in comments
    )

    return Estimate(
        step=(
            f"label {len(comments):,} comments against {len(codebook.themes)} "
            f"approved themes (1 call per comment)"
        ),
        calls=len(comments),
        input_tokens=input_tokens,
        assumed_output_tokens=len(comments) * ASSUMED_OUTPUT_TOKENS_PER_COMMENT,
        output_assumption=(
            f"assumes ~{ASSUMED_OUTPUT_TOKENS_PER_COMMENT} output tokens per comment"
        ),
        encoding_is_exact=counter.is_exact,
        price_in=price_in,
        price_out=price_out,
    )


def _failure(comment, reason: str, detail: str = "", theme_id: str = "", quote: str = "") -> dict:
    return {
        "comment_id": comment.comment_id,
        "row_number": comment.row_number,
        "comment_text": comment.text,
        "failure_reason": reason,
        "detail": detail,
        "rejected_theme_id": theme_id,
        "rejected_quote": quote,
    }


def _unassigned_row(comment) -> dict:
    return {
        "comment_id": comment.comment_id,
        "row_number": comment.row_number,
        "comment_text": comment.text,
        "assignment": "unassigned",
        "theme_id": "",
        "theme_label": "",
        "valence": "",
        "quote": "",
        "quote_start": "",
        "quote_end": "",
    }


def assemble_comment_rows(comment, response: dict, codebook: Codebook) -> CommentOutcome:
    """Check one model response and turn it into output rows or a failure.

    Pure: no network, no files. Everything the tool promises about its output
    is decided here, which is also what makes it testable without an API key.
    """
    assignments = response.get("assignments") or []

    if not isinstance(assignments, list) or not all(
        isinstance(entry, dict) for entry in assignments
    ):
        return CommentOutcome(
            failure=_failure(
                comment,
                "malformed_response",
                "the model returned assignments in a shape this tool cannot read",
            )
        )

    if response.get("unassigned") or not assignments:
        return CommentOutcome(rows=[_unassigned_row(comment)])

    primaries = [a for a in assignments if a.get("role") == "primary"]
    secondaries = [a for a in assignments if a.get("role") == "secondary"]

    if len(primaries) == 0:
        return CommentOutcome(
            failure=_failure(
                comment,
                "no_primary_theme",
                f"the model returned {len(assignments)} assignments and no primary theme",
            )
        )
    if len(primaries) > 1:
        return CommentOutcome(
            failure=_failure(
                comment,
                "multiple_primary_themes",
                f"the model marked {len(primaries)} themes as primary",
            )
        )
    if len(secondaries) > MAX_SECONDARY_THEMES:
        return CommentOutcome(
            failure=_failure(
                comment,
                "too_many_secondary_themes",
                f"{len(secondaries)} secondary themes, the limit is {MAX_SECONDARY_THEMES}",
            )
        )

    ordered = primaries + secondaries
    rows: list[dict] = []
    seen_themes: set[str] = set()

    for assignment in ordered:
        theme_id = str(assignment.get("theme_id", "") or "").strip()
        valence = str(assignment.get("valence", "") or "").strip()
        quote = str(assignment.get("quote", "") or "")
        role = str(assignment.get("role", "") or "")

        theme = codebook.get(theme_id)
        if theme is None:
            return CommentOutcome(
                failure=_failure(
                    comment,
                    "unknown_theme_id",
                    f"{theme_id!r} is not in the approved codebook",
                    theme_id=theme_id,
                    quote=quote,
                )
            )
        if theme_id in seen_themes:
            return CommentOutcome(
                failure=_failure(
                    comment,
                    "duplicate_theme",
                    f"{theme_id!r} was assigned twice to the same comment",
                    theme_id=theme_id,
                    quote=quote,
                )
            )
        if valence not in VALENCES:
            return CommentOutcome(
                failure=_failure(
                    comment,
                    "invalid_valence",
                    f"{valence!r} is not one of {', '.join(VALENCES)}",
                    theme_id=theme_id,
                    quote=quote,
                )
            )

        match = locate_quote(quote, comment.text)
        if match is None:
            return CommentOutcome(
                failure=_failure(
                    comment,
                    "quote_not_found_in_comment",
                    "the quote is not a verbatim span of the comment",
                    theme_id=theme_id,
                    quote=quote,
                )
            )

        seen_themes.add(theme_id)
        rows.append(
            {
                "comment_id": comment.comment_id,
                "row_number": comment.row_number,
                "comment_text": comment.text,
                "assignment": role,
                "theme_id": theme_id,
                "theme_label": theme.label,
                "valence": valence,
                "quote": match.text,
                "quote_start": match.start,
                "quote_end": match.end,
            }
        )

    return CommentOutcome(rows=rows)


def label_comments(
    comments,
    codebook: Codebook,
    client: Client,
    concurrency: int = 4,
    on_progress=None,
) -> LabelRun:
    """Label every comment, preserving input order in the output."""
    codebook_text = render_codebook(codebook)

    def label_one(comment) -> CommentOutcome:
        """Label one comment, and never raise.

        An exception escaping here would leave `pool.map` and then `main`,
        which writes no files at all, so one bad response would throw away
        every other comment in the run along with the money already spent on
        them. Whatever goes wrong, it goes wrong for this comment only.
        """
        try:
            response, usage = client.complete_json(
                system=LABEL_SYSTEM,
                user=_user_message(codebook_text, comment.text),
                schema=LABEL_SCHEMA,
                schema_name="comment_labels",
            )
        except ModelError as error:
            return CommentOutcome(
                failure=_failure(comment, "model_error", str(error)),
                usage=getattr(error, "usage", Usage()),
            )
        except Exception as error:
            return CommentOutcome(
                failure=_failure(comment, "model_error", f"{type(error).__name__}: {error}")
            )

        try:
            outcome = assemble_comment_rows(comment, response, codebook)
        except Exception as error:
            return CommentOutcome(
                failure=_failure(
                    comment, "malformed_response", f"{type(error).__name__}: {error}"
                ),
                usage=usage,
            )

        outcome.usage = usage
        return outcome

    run = LabelRun()

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        for index, outcome in enumerate(pool.map(label_one, comments), start=1):
            run.usage = run.usage + outcome.usage
            if outcome.failed:
                run.failures.append(outcome.failure)
            else:
                run.rows.extend(outcome.rows)
                if outcome.rows and outcome.rows[0]["assignment"] == "unassigned":
                    run.comments_unassigned += 1
                else:
                    run.comments_labelled += 1
            if on_progress is not None:
                on_progress(index, len(comments))

    return run
