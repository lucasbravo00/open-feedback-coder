"""Command line entry point: `ofc propose` and `ofc label`."""

from __future__ import annotations

import argparse
import sys

from . import __version__, codebook as codebook_module, label as label_step, propose as propose_step
from .budget import BudgetExceeded, Cancelled, TokenCounter, check_input_limit, confirm
from .codebook import Codebook, CodebookError
from .csv_io import FAILURE_COLUMNS, OUTPUT_COLUMNS, InputError, read_comments, write_rows
from .llm import Client, ConfigError, ModelError

DEFAULT_MAX_THEMES = 15


def _log(message: str = "") -> None:
    print(message, file=sys.stderr)


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

    client = Client(model=args.model)
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
        },
    )
    codebook_module.save(result.codebook, args.output)

    _log()
    _log(f"Wrote {args.output}.")
    _log("Open it, edit the themes, and delete what you do not want. Then run:")
    _log(
        f"  ofc label --input {args.input} --text-column {args.text_column} "
        f"--codebook {args.output}"
    )
    return 0


def command_label(args) -> int:
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

    client = Client(model=args.model)
    counter = TokenCounter(client.model)

    estimate = label_step.build_estimate(
        comments, book, counter, args.price_in, args.price_out
    )
    check_input_limit(estimate, args.max_input_tokens)
    confirm(estimate, args.yes)

    def progress(done: int, total: int) -> None:
        if done == total or done % 25 == 0:
            print(f"  labelled {done:,}/{total:,}", end="\r", file=sys.stderr, flush=True)

    run = label_step.label_comments(
        comments, book, client, concurrency=args.concurrency, on_progress=progress
    )

    write_rows(args.output, OUTPUT_COLUMNS, run.rows)
    write_rows(args.failures, FAILURE_COLUMNS, run.failures)

    _log()
    _log()
    _log(
        f"Comments: {run.comments_labelled:,} labelled, "
        f"{run.comments_unassigned:,} unassigned, "
        f"{run.comments_failed:,} excluded after checking."
    )
    _log(f"Wrote {args.output} ({len(run.rows):,} rows, one per comment-theme pair).")
    _log(f"Wrote {args.failures} ({len(run.failures):,} rows).")
    _log(
        f"Tokens reported by the API: {run.usage.input_tokens:,} in, "
        f"{run.usage.output_tokens:,} out."
    )
    if run.comments_failed:
        _log(
            "Excluded comments are in the failures file with the reason and the "
            "text the model returned."
        )
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


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
