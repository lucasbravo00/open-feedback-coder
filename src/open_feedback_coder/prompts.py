"""Prompt text and response schemas.

Everything the model is told lives here, in one file, so that the instructions
behind a published codebook or a published label can be read without going
through the rest of the code.
"""

from __future__ import annotations

# This sentence defines what "primary" means. It is quoted verbatim in the
# README; if it changes here it must change there too.
PRIMARY_THEME_RULE = (
    "The primary theme is what the comment is mainly about: the concern or "
    "experience that prompted the person to write it. Secondary themes are "
    "mentioned but are not what the comment is driving at."
)

MAX_SECONDARY_THEMES = 2

VALENCES = ("positive", "negative", "neutral")

_QUOTE_RULE = (
    "A quote must be copied from the comment character for character. Do not "
    "paraphrase, summarise, translate, fix spelling or punctuation, or join "
    "separate fragments together. Copy one continuous span of the original "
    "text. A quote that cannot be found in the comment is discarded."
)

PROPOSE_SYSTEM = f"""\
You are helping a researcher build an inductive codebook for a set of \
open-ended survey answers.

Read the whole corpus below and propose the themes that actually emerge from \
it. Do not impose a standard survey taxonomy, and do not propose a theme that \
only one or two comments support unless it is clearly distinct.

Rules:
- Propose at most {{max_themes}} themes. Fewer is fine if the corpus supports fewer.
- Write every theme id, label and description in English, whatever language \
the comments are in.
- An id is lowercase letters, digits and underscores, for example \
onboarding_support.
- A label is a short noun phrase. A description is one or two sentences saying \
what belongs in the theme and what does not.
- Give each theme exactly two example quotes drawn from different comments, \
each with the id of the comment it came from.
- Each comment below is introduced by a line reading <comment ID> and closed \
by a line reading </comment>. The comment_id you return is that ID on its own: \
for a comment introduced by <comment 7>, the id is 7, not <comment 7> and not \
<7>.
- {_QUOTE_RULE}

The person who receives this codebook will edit it before anything is \
labelled, so propose themes that are distinct enough to be judged one by one.
"""

LABEL_SYSTEM = f"""\
You are assigning an open-ended survey answer to themes from a codebook that a \
person has already reviewed and approved.

Use only the theme ids given to you. Never invent a theme.

Assign exactly one primary theme and at most {MAX_SECONDARY_THEMES} secondary \
themes.

{PRIMARY_THEME_RULE}

For each theme you assign, give:
- valence: how the person feels about that theme in this comment, one of \
positive, negative or neutral. Judge the theme, not the comment as a whole: \
one comment can be positive about one theme and negative about another.
- quote: the span of the comment that supports this assignment. \
{_QUOTE_RULE}

If no theme in the codebook fits this comment, set unassigned to true and \
return an empty list of assignments. Do not stretch a theme to make it fit; a \
comment that does not belong anywhere is useful information about the codebook.
"""

PROPOSE_SCHEMA = {
    "type": "object",
    "properties": {
        "themes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "label": {"type": "string"},
                    "description": {"type": "string"},
                    "examples": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "comment_id": {"type": "string"},
                                "quote": {"type": "string"},
                            },
                            "required": ["comment_id", "quote"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["id", "label", "description", "examples"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["themes"],
    "additionalProperties": False,
}

LABEL_SCHEMA = {
    "type": "object",
    "properties": {
        "unassigned": {"type": "boolean"},
        "assignments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "theme_id": {"type": "string"},
                    "role": {"type": "string", "enum": ["primary", "secondary"]},
                    "valence": {"type": "string", "enum": list(VALENCES)},
                    "quote": {"type": "string"},
                },
                "required": ["theme_id", "role", "valence", "quote"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["unassigned", "assignments"],
    "additionalProperties": False,
}


def render_corpus(comments) -> str:
    """Render comments for the proposal prompt, one block per comment.

    The id sits alone on its own delimiter line. It used to be a bracketed
    prefix on the text, `[7] ...`, and models answered with the id "[7]",
    which is a fair reading of what they were shown.
    """
    return "\n\n".join(
        f"<comment {comment.comment_id}>\n{comment.text}\n</comment>"
        for comment in comments
    )


def render_codebook(codebook) -> str:
    """Render an approved codebook for the labelling prompt."""
    blocks = []
    for theme in codebook.themes:
        block = f"- id: {theme.id}\n  label: {theme.label}"
        if theme.description:
            block += f"\n  description: {theme.description}"
        blocks.append(block)
    return "\n".join(blocks)
