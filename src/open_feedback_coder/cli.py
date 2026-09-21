"""Command line entry point: `ofc propose` and `ofc label`."""

from __future__ import annotations

import argparse
import csv
import os
import sys

from . import (
    __version__,
    codebook as codebook_module,
    counting,
    label as label_step,
    propose as propose_step,
    reviewing,
)
from .budget import BudgetExceeded, Cancelled, TokenCounter, check_input_limit, confirm
from .codebook import Codebook, CodebookError, compare_with_proposal, proposal_record
from .csv_io import (
    FAILURE_COLUMNS,
    OUTPUT_COLUMNS,
    InputError,
    RowWriter,
    read_comments,
    write_rows,
)
from .llm import DEFAULT_MAX_RETRIES, Client, ConfigError, ModelError
from .phrasing import plural as _plural

DEFAULT_MAX_THEMES = 15


def _evidence_path(codebook_path: str, explicit: str | None) -> str:
    """Where the quotes behind a proposed codebook go, beside the codebook."""
    if explicit:
        return explicit
    stem, _ = os.path.splitext(codebook_path)
    return f"{stem}.evidence.md"


def _log(message: str = "", end: str = "\n") -> None:
    """Write progress to stderr, and never let that be why a run dies.

    If whatever was reading stderr goes away mid-run - a pipe into `head`, a
    closed terminal - the model calls have already been made and paid for.
    Losing the output files over a failed progress message would be the worst
    possible response to that.
    """
    try:
        print(message, file=sys.stderr, end=end, flush=True)
    except (BrokenPipeError, OSError, ValueError):
        pass


def _non_negative_int(value: str) -> int:
    """An argparse type for counts where zero is a meaningful choice."""
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not a whole number") from None
    if number < 0:
        raise argparse.ArgumentTypeError(f"must be 0 or more, got {number}")
    return number


def _positive_int(value: str) -> int:
    """An argparse type for counts that are meaningless at zero or below.

    Without this a negative --max-themes reaches a paid call and comes back
    reporting more discarded themes than the model returned, which is a
    fabricated number on the user's screen.
    """
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not a whole number") from None
    if number < 1:
        raise argparse.ArgumentTypeError(f"must be 1 or more, got {number}")
    return number


def _add_input_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--input", required=True, metavar="FILE", help="Input CSV file.")
    parser.add_argument(
        "--text-column",
        required=True,
        metavar="NAME",
        help="Name of the column holding the open-ended answers.",
    )
    parser.add_argument(
        "--id-column",
        metavar="NAME",
        help="Column holding a comment id. Defaults to the row number.",
    )
    parser.add_argument(
        "--delimiter", default=",", metavar="CHAR", help="Field delimiter. Use $'\\t' for TSV."
    )
    parser.add_argument(
        "--encoding", default="utf-8-sig", metavar="NAME", help="Input file encoding."
    )


def _add_model_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--model",
        metavar="ID",
        help="OpenAI model id. Defaults to the OPENAI_MODEL environment variable.",
    )
    parser.add_argument(
        "--price-in",
        type=float,
        metavar="USD",
        help="Input price in USD per 1M tokens, used only for the cost estimate.",
    )
    parser.add_argument(
        "--price-out",
        type=float,
        metavar="USD",
        help="Output price in USD per 1M tokens, used only for the cost estimate.",
    )
    parser.add_argument(
        "--max-input-tokens",
        type=_positive_int,
        metavar="N",
        help="Stop before sending anything if the measured input exceeds N tokens.",
    )
    parser.add_argument(
        "--max-retries",
        type=_non_negative_int,
        default=DEFAULT_MAX_RETRIES,
        metavar="N",
        help=(
            f"How many times to try a call again after a connection error, a "
            f"timeout or a rate limit (default {DEFAULT_MAX_RETRIES}). A quote "
            "that cannot be verified is never retried; that is a different thing."
        ),
    )
    parser.add_argument(
        "--yes", action="store_true", help="Skip the confirmation prompt before spending."
    )


def _report_input(comments, skipped, args) -> None:
    _log(f"Read {len(comments):,} comments from {args.input} (column {args.text_column!r}).")
    if skipped.total:
        detail = [f"{skipped.empty} empty"] if skipped.empty else []
        if skipped.placeholder:
            values = ", ".join(
                f"{value!r} x{count}"
                for value, count in sorted(
                    skipped.placeholder_values.items(), key=lambda item: -item[1]
                )
            )
            detail.append(f"{skipped.placeholder} placeholder ({values})")
        _log(f"Skipped {skipped.total:,} rows before any model call: {'; '.join(detail)}.")
    if not comments:
        raise InputError("No usable comments found. Nothing to do.")


def command_propose(args) -> int:
    comments, skipped = read_comments(
        args.input,
        text_column=args.text_column,
        id_column=args.id_column,
        delimiter=args.delimiter,
        encoding=args.encoding,
    )
    _report_input(comments, skipped, args)

    client = Client(model=args.model, max_retries=args.max_retries)
    counter = TokenCounter(client.model)

    estimate = propose_step.build_estimate(
        comments, counter, args.max_themes, args.price_in, args.price_out
    )
    check_input_limit(estimate, args.max_input_tokens)
    confirm(estimate, args.yes)

    result = propose_step.propose(comments, client, args.max_themes)

    verified = sum(len(theme.examples) for theme in result.codebook.themes)
    rejected = len(result.rejected_examples)
    _log()
    _log(f"Proposed {len(result.codebook.themes)} themes.")
    _log(
        f"Example quotes: {verified} verified against the source comments, "
        f"{rejected} rejected and dropped."
    )
    if result.themes_discarded_over_limit:
        _log(
            f"The model returned {result.themes_discarded_over_limit} themes beyond "
            f"--max-themes {args.max_themes}; they were discarded."
        )
    _log(
        f"Tokens reported by the API: {result.usage.input_tokens:,} in, "
        f"{result.usage.output_tokens:,} out."
    )
    if getattr(client, "retries", 0):
        _log(
            f"Retried {_plural(client.retries, 'call')} after a connection error, a "
            "timeout or a rate limit."
        )

    result.codebook = Codebook(
        themes=result.codebook.themes,
        source={
            "tool_version": __version__,
            "input_file": args.input,
            "text_column": args.text_column,
            "comments_analysed": len(comments),
            "rows_skipped": skipped.total,
            "model": client.model,
            "max_themes": args.max_themes,
            # What the model proposed, so that `ofc label` can report what you
            # changed. It is the evidence behind "a person approved this".
            "proposed": proposal_record(result.codebook),
        },
    )

    evidence_path = _evidence_path(args.output, args.evidence)
    codebook_module.save(result.codebook, args.output)
    codebook_module.save_evidence(result.codebook, evidence_path, args.output)

    _log()
    _log(f"Wrote {args.output} ({len(result.codebook.themes)} themes to edit).")
    _log(f"Wrote {evidence_path} (the quotes behind them, to read while you edit).")
    _log("Edit the codebook, delete what you do not want, then run:")
    _log(
        f"  ofc label --input {args.input} --text-column {args.text_column} "
        f"--codebook {args.output}"
    )
    return 0


def command_label(args) -> int:
    if args.output and args.output == args.failures:
        raise InputError(
            "--output and --failures cannot be the same file: both are opened for "
            "writing at once and would truncate each other. Nothing was sent."
        )

    book = codebook_module.load(args.codebook)

    comments, skipped = read_comments(
        args.input,
        text_column=args.text_column,
        id_column=args.id_column,
        delimiter=args.delimiter,
        encoding=args.encoding,
    )
    _report_input(comments, skipped, args)
    _log(f"Using {len(book.themes)} approved themes from {args.codebook}.")

    approval = compare_with_proposal(book)
    if approval is not None:
        _log(approval.describe())
    else:
        _log(
            "No record of a proposal in this codebook, so there is nothing to say "
            "about what was changed in it."
        )

    client = Client(model=args.model, max_retries=args.max_retries)
    counter = TokenCounter(client.model)

    estimate = label_step.build_estimate(
        comments, book, counter, args.price_in, args.price_out
    )
    check_input_limit(estimate, args.max_input_tokens)
    confirm(estimate, args.yes)

    def progress(done: int, total: int) -> None:
        if done == total or done % 25 == 0:
            _log(f"  labelled {done:,}/{total:,}", end="\r")

    # Written as they arrive, so a run that dies partway leaves behind
    # everything it had already paid for and checked.
    with (
        RowWriter(args.output, OUTPUT_COLUMNS) as labelled,
        RowWriter(args.failures, FAILURE_COLUMNS) as rejected,
    ):
        run = label_step.label_comments(
            comments,
            book,
            client,
            concurrency=args.concurrency,
            on_progress=progress,
            on_rows=labelled.write,
            on_rejections=rejected.write,
        )

    dropped = run.dropped_assignments

    _log()
    _log()
    _log(
        f"Comments: {run.comments_labelled:,} labelled, "
        f"{run.comments_unassigned:,} unassigned, "
        f"{run.comments_failed:,} excluded after checking."
    )
    if dropped:
        affected = len({entry["comment_id"] for entry in dropped})
        _log(
            f"Kept {_plural(affected, 'comment')} after dropping "
            f"{_plural(len(dropped), 'repeated theme assignment')}."
        )
    _log(
        f"Wrote {args.output} ({_plural(len(run.rows), 'row')}, "
        "one per comment-theme pair)."
    )
    _log(
        f"Wrote {args.failures} ({_plural(len(run.rejections), 'row')}: "
        f"{_plural(run.comments_failed, 'excluded comment')}, "
        f"{_plural(len(dropped), 'dropped assignment')})."
    )
    _log(
        f"Tokens reported by the API: {run.usage.input_tokens:,} in, "
        f"{run.usage.output_tokens:,} out."
    )
    if run.retries:
        _log(
            f"Retried {_plural(run.retries, 'call')} after a connection error, a "
            "timeout or a rate limit."
        )
    if run.comments_failed:
        _log(
            "Excluded comments are in the failures file with the reason and the "
            "text the model returned."
        )
    return 0


def _read_labelled(path: str) -> list[dict]:
    """Read a file this tool wrote earlier, checking it is one."""
    try:
        with open(path, newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    except FileNotFoundError as error:
        raise InputError(f"Labelled file not found: {path}") from error

    if not rows:
        raise InputError(f"{path} has no rows.")

    # theme_id is the column that tells a labelled file from every other file
    # this tool writes: the review sheet carries comment_id, assignment and
    # quote too, and counting it merges every theme into one fabricated row.
    required = ("comment_id", "assignment", "theme_id", "theme_label", "quote")
    missing = [column for column in required if column not in rows[0]]
    if missing:
        raise InputError(
            f"{path} does not look like a file `ofc label` wrote: "
            f"missing {', '.join(missing)}."
        )
    return rows


def command_counts(args) -> int:
    rows = _read_labelled(args.labelled)
    counts = counting.count(rows)

    # The table and the caveat that qualifies it go to the same stream, so
    # they cannot arrive in the wrong order or be separated by a redirect.
    print(counts.render())
    print(
        "\nThese count labels, not people: a theme's number is how many comments "
        "it was assigned to and a quote was found for, not how many people hold "
        "that view, and not a claim that those are the right comments."
    )

    if args.output:
        write_rows(args.output, counting.COUNT_COLUMNS, counts.as_rows())
        _log(f"Wrote {args.output}.")
    return 0


def command_review(args) -> int:
    rows = _read_labelled(args.labelled)
    chosen = reviewing.sample(
        rows, args.sample, seed=args.seed, include_unassigned=args.include_unassigned
    )

    if not chosen:
        raise InputError("Nothing to review: no rows matched.")

    print(reviewing.render(chosen))
    print(
        f"\n{_plural(len(chosen), 'row')} sampled from {_plural(len(rows), 'row')} "
        f"(seed {args.seed}, so the same sample comes back on a re-run)."
    )
    print(
        "Read each quote against the comment beside it. This tool does not turn "
        "your answers into a score: a number computed by one reader on a sample "
        "they chose would look like validation, and it is not."
    )

    # The sample is seeded so the same rows come back across several sittings.
    # Rewriting the sheet blank on the second sitting would throw away exactly
    # the work this command exists to collect.
    marks = {}
    if os.path.exists(args.output) and not args.overwrite:
        try:
            with open(args.output, newline="", encoding="utf-8") as handle:
                marks = reviewing.existing_marks(csv.DictReader(handle))
        except OSError as error:
            raise InputError(f"Could not read the existing {args.output}: {error}") from error

    write_rows(
        args.output, reviewing.REVIEW_COLUMNS, reviewing.to_review_rows(chosen, marks)
    )
    if marks:
        _log(
            f"Wrote {args.output}, keeping {_plural(len(marks), 'mark')} already in it. "
            "Pass --overwrite to start the sheet again."
        )
    else:
        _log(f"Wrote {args.output} with an empty `agree` column to fill in.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ofc",
        description=(
            "Thematic coding of open-ended feedback. Propose a codebook, edit it "
            "by hand, then label every comment with a theme, a valence and a "
            "quote that has been checked against the source text."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    propose_parser = subparsers.add_parser(
        "propose", help="Propose an editable codebook from the whole corpus."
    )
    _add_input_arguments(propose_parser)
    _add_model_arguments(propose_parser)
    propose_parser.add_argument(
        "--max-themes",
        type=_positive_int,
        default=DEFAULT_MAX_THEMES,
        metavar="N",
        help=f"Maximum number of themes to propose (default {DEFAULT_MAX_THEMES}).",
    )
    propose_parser.add_argument(
        "--output", default="codebook.yaml", metavar="FILE", help="Where to write the codebook."
    )
    propose_parser.add_argument(
        "--evidence",
        metavar="FILE",
        help="Where to write the quotes behind each theme. Defaults to the "
        "codebook's name with .evidence.md.",
    )
    propose_parser.set_defaults(handler=command_propose)

    label_parser = subparsers.add_parser(
        "label", help="Label every comment against an approved codebook."
    )
    _add_input_arguments(label_parser)
    _add_model_arguments(label_parser)
    label_parser.add_argument(
        "--codebook", default="codebook.yaml", metavar="FILE", help="The edited codebook."
    )
    label_parser.add_argument(
        "--output", default="labelled.csv", metavar="FILE", help="Where to write labelled rows."
    )
    label_parser.add_argument(
        "--failures",
        default="labelling_failures.csv",
        metavar="FILE",
        help="Where to write comments excluded by the checks.",
    )
    label_parser.add_argument(
        "--concurrency",
        type=_positive_int,
        default=4,
        metavar="N",
        help="How many comments to label at once (default 4).",
    )
    label_parser.set_defaults(handler=command_label)

    counts_parser = subparsers.add_parser(
        "counts",
        help="Count the labelled rows. Reads a file, calls no model, costs nothing.",
    )
    counts_parser.add_argument(
        "--labelled",
        default="labelled.csv",
        metavar="FILE",
        help="A file written by `ofc label`.",
    )
    counts_parser.add_argument(
        "--output", metavar="FILE", help="Also write the counts as a CSV."
    )
    counts_parser.set_defaults(handler=command_counts)

    review_parser = subparsers.add_parser(
        "review",
        help="Draw a sample to read by hand, with each quote beside its comment.",
    )
    review_parser.add_argument(
        "--labelled",
        default="labelled.csv",
        metavar="FILE",
        help="A file written by `ofc label`.",
    )
    review_parser.add_argument(
        "--sample", type=_positive_int, default=20, metavar="N", help="How many rows to draw."
    )
    review_parser.add_argument(
        "--seed",
        type=_non_negative_int,
        default=0,
        metavar="N",
        help="Fixed by default, so re-running gives the same sample.",
    )
    review_parser.add_argument(
        "--include-unassigned",
        action="store_true",
        help="Also sample comments no theme was assigned to.",
    )
    review_parser.add_argument(
        "--output", default="review.csv", metavar="FILE", help="Where to write the sheet."
    )
    review_parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Start the sheet again, discarding any agree and notes already in it. "
        "Without this, marks already made are carried forward.",
    )
    review_parser.set_defaults(handler=command_review)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except Cancelled as error:
        _log(str(error))
        return 130
    except (InputError, CodebookError, ConfigError, BudgetExceeded, ModelError) as error:
        _log(f"error: {error}")
        return 1
    except OSError as error:
        # A path that cannot be written, a full disk, a permission denied.
        _log(f"error: {error}")
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
