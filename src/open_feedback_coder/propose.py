"""Step one: propose an inductive codebook from the whole corpus.

The corpus is sent in full, in one call. There is no sampling: deciding which
comments represent the rest is a methodological choice, and this tool does not
make it silently. If the corpus is too large, the run stops and says so.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .budget import ASSUMED_OUTPUT_TOKENS_PER_THEME, Estimate, TokenCounter
from .codebook import Codebook, Example, Theme
from .llm import Client, Usage
from .prompts import PROPOSE_SCHEMA, PROPOSE_SYSTEM, render_corpus
from .text import locate_quote

_NON_SLUG = re.compile(r"[^a-z0-9]+")


def slugify(value: str, fallback: str) -> str:
    """Coerce a model-supplied id into the form the codebook loader accepts.

    This repairs formatting only. It is not a judgement about the theme, and
    the label and description are left exactly as the model wrote them.
    """
    slug = _NON_SLUG.sub("_", value.strip().lower()).strip("_")
    return slug or fallback


@dataclass
class ProposeResult:
    """What came back from the proposal call, after checking the quotes."""

    codebook: Codebook
    usage: Usage
    rejected_examples: list[dict] = field(default_factory=list)
    themes_discarded_over_limit: int = 0


def build_estimate(
    comments,
    counter: TokenCounter,
    max_themes: int,
    price_in: float | None = None,
    price_out: float | None = None,
) -> Estimate:
    """Measure what the proposal call will send."""
    system = PROPOSE_SYSTEM.format(max_themes=max_themes)
    user = render_corpus(comments)
    input_tokens = counter.count(system) + counter.count(user)
    assumed_output = max_themes * ASSUMED_OUTPUT_TOKENS_PER_THEME

    return Estimate(
        step=f"propose a codebook from {len(comments):,} comments (1 call, full corpus)",
        calls=1,
        input_tokens=input_tokens,
        assumed_output_tokens=assumed_output,
        output_assumption=(
            f"assumes ~{ASSUMED_OUTPUT_TOKENS_PER_THEME} output tokens per theme, "
            f"up to {max_themes} themes"
        ),
        encoding_is_exact=counter.is_exact,
        price_in=price_in,
        price_out=price_out,
    )


def build_codebook(response: dict, comments, max_themes: int) -> ProposeResult:
    """Turn a model response into a codebook, keeping only verified examples.

    Example quotes are checked against the comment they claim to come from,
    with the same verifier used for labels. An example that does not check out
    is dropped and reported; the theme itself is kept, because a theme with a
    bad illustration is still a theme a person can judge.
    """
    by_id = {comment.comment_id: comment for comment in comments}
    themes: list[Theme] = []
    rejected: list[dict] = []
    seen_ids: set[str] = set()

    raw_themes = response.get("themes") or []
    discarded = max(0, len(raw_themes) - max_themes)

    for position, raw_theme in enumerate(raw_themes[:max_themes], start=1):
        theme_id = slugify(str(raw_theme.get("id", "")), fallback=f"theme_{position}")
        while theme_id in seen_ids:
            theme_id = f"{theme_id}_{position}"
        seen_ids.add(theme_id)

        examples: list[Example] = []
        for raw_example in raw_theme.get("examples") or []:
            quote = str(raw_example.get("quote", "") or "")
            comment_id = str(raw_example.get("comment_id", "") or "").strip()
            comment = by_id.get(comment_id)

            if comment is None:
                rejected.append(
                    {
                        "theme_id": theme_id,
                        "comment_id": comment_id,
                        "quote": quote,
                        "reason": "unknown_comment_id",
                    }
                )
                continue

            match = locate_quote(quote, comment.text)
            if match is None:
                rejected.append(
                    {
                        "theme_id": theme_id,
                        "comment_id": comment_id,
                        "quote": quote,
                        "reason": "quote_not_found_in_comment",
                    }
                )
                continue

            examples.append(Example(comment_id=comment_id, quote=match.text))

        themes.append(
            Theme(
                id=theme_id,
                label=str(raw_theme.get("label", "") or theme_id).strip(),
                description=str(raw_theme.get("description", "") or "").strip(),
                examples=examples,
            )
        )

    return ProposeResult(
        codebook=Codebook(themes=themes),
        usage=Usage(),
        rejected_examples=rejected,
        themes_discarded_over_limit=discarded,
    )


def propose(comments, client: Client, max_themes: int) -> ProposeResult:
    """Run the proposal call and return the verified codebook."""
    system = PROPOSE_SYSTEM.format(max_themes=max_themes)
    response, usage = client.complete_json(
        system=system,
        user=render_corpus(comments),
        schema=PROPOSE_SCHEMA,
        schema_name="codebook_proposal",
    )
    result = build_codebook(response, comments, max_themes)
    result.usage = usage
    return result
