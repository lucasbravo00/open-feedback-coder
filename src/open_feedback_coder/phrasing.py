"""Wording shared by the command line and the web interface.

Both report the same counts at the end of a run, and they drifted: the command
line was taught to say "1 comment" and the app went on saying "1 comments".
Saying it once means they cannot drift again.
"""

from __future__ import annotations


def plural(count: int, singular: str, plural_form: str | None = None) -> str:
    """Return "1 comment" or "4 comments", with thousands separators."""
    noun = singular if count == 1 else (plural_form or singular + "s")
    return f"{count:,} {noun}"
