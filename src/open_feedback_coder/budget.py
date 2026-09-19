"""Counting tokens and showing what a run will cost before it starts.

Token counts here are measured, not guessed: the text that will be sent is
encoded and counted. Output tokens cannot be measured in advance, so they are
an assumption, and the assumption is printed next to the number rather than
folded into it. Prices are never hard-coded; if the user does not supply them
the tool says the cost is unknown instead of inventing one.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

FALLBACK_ENCODING = "o200k_base"

# Used only to turn a comment count into an output-token estimate. It is a
# stated assumption, printed with the estimate, not a measurement.
ASSUMED_OUTPUT_TOKENS_PER_COMMENT = 160
ASSUMED_OUTPUT_TOKENS_PER_THEME = 140


class BudgetExceeded(Exception):
    """Raised when a run is stopped before any model call."""


class Cancelled(Exception):
    """Raised when the user declines to proceed."""


def _encoding(model: str):
    import tiktoken

    try:
        return tiktoken.encoding_for_model(model), True
    except Exception:
        return tiktoken.get_encoding(FALLBACK_ENCODING), False


class TokenCounter:
    """Counts tokens with the encoding that best matches the chosen model."""

    def __init__(self, model: str):
        self._encoding, self.is_exact = _encoding(model)
        self.model = model

    def count(self, text: str) -> int:
        return len(self._encoding.encode(text, disallowed_special=()))


@dataclass
class Estimate:
    """What a run is expected to send, and what that is expected to cost."""

    step: str
    calls: int
    input_tokens: int
    assumed_output_tokens: int
    output_assumption: str
    encoding_is_exact: bool
    price_in: float | None = None
    price_out: float | None = None

    @property
    def cost(self) -> float | None:
        """Estimated USD, or None when prices were not supplied."""
        if self.price_in is None or self.price_out is None:
            return None
        return (
            self.input_tokens * self.price_in + self.assumed_output_tokens * self.price_out
        ) / 1_000_000

    def render(self) -> str:
        lines = [
            f"About to run: {self.step}",
            f"  model calls              {self.calls:,}",
            f"  input tokens (measured)  {self.input_tokens:,}",
            f"  output tokens (assumed)  {self.assumed_output_tokens:,}"
            f"   <- {self.output_assumption}",
        ]
        cost = self.cost
        if cost is None:
            lines.append(
                "  estimated cost           unknown"
                "   <- pass --price-in and --price-out (USD per 1M tokens) to see one"
            )
        else:
            lines.append(
                f"  estimated cost           ${cost:,.2f}"
                f"   <- at ${self.price_in}/1M in, ${self.price_out}/1M out"
            )
        if not self.encoding_is_exact:
            lines.append(
                f"  note: no tokeniser is registered for this model, so tokens were"
                f" counted with {FALLBACK_ENCODING}; the real count may differ."
            )
        return "\n".join(lines)


def check_input_limit(estimate: Estimate, max_input_tokens: int | None) -> None:
    """Stop the run when the measured input exceeds an explicit ceiling."""
    if not max_input_tokens:
        return
    if estimate.input_tokens > max_input_tokens:
        raise BudgetExceeded(
            f"This run would send {estimate.input_tokens:,} input tokens, over the "
            f"--max-input-tokens ceiling of {max_input_tokens:,}.\n"
            "Nothing was sent. Reducing the corpus means deciding how to sample it, "
            "which is a decision for you and not for this tool."
        )


def confirm(estimate: Estimate, assume_yes: bool, stream=sys.stderr) -> None:
    """Print the estimate and ask to continue, unless --yes was given."""
    print(estimate.render(), file=stream)

    if assume_yes:
        print("  proceeding (--yes)", file=stream)
        return

    if not sys.stdin.isatty():
        raise Cancelled(
            "Nothing was sent: there is no terminal to confirm on. "
            "Re-run with --yes to skip this confirmation."
        )

    print("Proceed? [y/N] ", end="", file=stream, flush=True)
    answer = sys.stdin.readline().strip().lower()
    if answer not in {"y", "yes"}:
        raise Cancelled("Cancelled. Nothing was sent.")
