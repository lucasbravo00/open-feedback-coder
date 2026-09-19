"""The codebook: proposed by the model, approved by a person, read back as YAML.

The file written by `ofc propose` is meant to be opened and edited by hand
before `ofc label` reads it. That round trip is the reason the format is YAML
and not a pickle or a JSON blob, and the reason loading validates loudly
instead of repairing quietly: a codebook that was silently fixed up is no
longer one a person approved.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import yaml

CODEBOOK_VERSION = 1
THEME_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")

_HEADER_COMMENT = """\
# Codebook proposed by open-feedback-coder.
#
# Nothing here is final. Rename labels, rewrite descriptions, merge themes that
# say the same thing, and delete themes you do not want. `ofc label` assigns
# comments only to the themes left in this file, so this is where you decide
# what the analysis is allowed to find.
#
# Rules the loader enforces:
#   - `id` must be lowercase letters, digits and underscores, and unique.
#   - `label` must not be empty.
#   - at least one theme must remain.
# `examples` are quotes taken verbatim from the corpus and verified against it.
# They are here to help you judge each theme; editing or deleting them changes
# nothing about how labelling works.
"""


class CodebookError(Exception):
    """Raised when a codebook file cannot be used as written."""


@dataclass(frozen=True)
class Example:
    """A verbatim quote from the corpus that illustrates a theme."""

    comment_id: str
    quote: str


@dataclass(frozen=True)
class Theme:
    """One category a comment can be assigned to."""

    id: str
    label: str
    description: str = ""
    examples: list[Example] = field(default_factory=list)


@dataclass(frozen=True)
class Codebook:
    """A set of themes plus a record of how the proposal was produced."""

    themes: list[Theme]
    source: dict = field(default_factory=dict)

    def theme_ids(self) -> set[str]:
        return {theme.id for theme in self.themes}

    def get(self, theme_id: str) -> Theme | None:
        for theme in self.themes:
            if theme.id == theme_id:
                return theme
        return None


def to_yaml(codebook: Codebook) -> str:
    """Render a codebook as the YAML text written to disk."""
    document = {
        "version": CODEBOOK_VERSION,
        "source": codebook.source,
        "themes": [
            {
                "id": theme.id,
                "label": theme.label,
                "description": theme.description,
                "examples": [
                    {"comment_id": example.comment_id, "quote": example.quote}
                    for example in theme.examples
                ],
            }
            for theme in codebook.themes
        ],
    }
    body = yaml.safe_dump(document, sort_keys=False, allow_unicode=True, width=88)
    return _HEADER_COMMENT + "\n" + body


def save(codebook: Codebook, path: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(to_yaml(codebook))


def from_dict(document: object, origin: str = "codebook") -> Codebook:
    """Build a codebook from parsed YAML, rejecting anything unusable."""
    if not isinstance(document, dict):
        raise CodebookError(f"{origin}: expected a mapping at the top level.")

    raw_themes = document.get("themes")
    if not isinstance(raw_themes, list) or not raw_themes:
        raise CodebookError(
            f"{origin}: 'themes' must be a non-empty list. "
            "A codebook with no themes cannot label anything."
        )

    themes: list[Theme] = []
    seen_ids: set[str] = set()

    for position, entry in enumerate(raw_themes, start=1):
        where = f"{origin}: theme #{position}"
        if not isinstance(entry, dict):
            raise CodebookError(f"{where} is not a mapping.")

        theme_id = str(entry.get("id", "")).strip()
        label = str(entry.get("label", "")).strip()

        if not theme_id:
            raise CodebookError(f"{where} has no 'id'.")
        if not THEME_ID_PATTERN.match(theme_id):
            raise CodebookError(
                f"{where}: id {theme_id!r} must be lowercase letters, digits and "
                "underscores, for example 'onboarding_support'."
            )
        if theme_id in seen_ids:
            raise CodebookError(
                f"{where}: id {theme_id!r} is used more than once. "
                "If you merged two themes, keep one entry and delete the other."
            )
        if not label:
            raise CodebookError(f"{where} ({theme_id}) has no 'label'.")

        seen_ids.add(theme_id)

        raw_examples = entry.get("examples") or []
        examples: list[Example] = []
        if isinstance(raw_examples, list):
            for raw_example in raw_examples:
                if isinstance(raw_example, dict):
                    quote = str(raw_example.get("quote", "")).strip()
                    if quote:
                        examples.append(
                            Example(
                                comment_id=str(raw_example.get("comment_id", "")).strip(),
                                quote=quote,
                            )
                        )
                elif isinstance(raw_example, str) and raw_example.strip():
                    examples.append(Example(comment_id="", quote=raw_example.strip()))

        themes.append(
            Theme(
                id=theme_id,
                label=label,
                description=str(entry.get("description", "") or "").strip(),
                examples=examples,
            )
        )

    source = document.get("source")
    return Codebook(themes=themes, source=source if isinstance(source, dict) else {})


def load(path: str) -> Codebook:
    """Read and validate a codebook from disk."""
    try:
        with open(path, encoding="utf-8") as handle:
            document = yaml.safe_load(handle)
    except FileNotFoundError as error:
        raise CodebookError(
            f"Codebook not found: {path}. Run `ofc propose` first, then edit the file."
        ) from error
    except yaml.YAMLError as error:
        raise CodebookError(f"{path} is not valid YAML: {error}") from error

    return from_dict(document, origin=path)
