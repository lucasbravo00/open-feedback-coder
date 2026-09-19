import pytest

from open_feedback_coder.codebook import Codebook, Theme
from open_feedback_coder.csv_io import Comment
from open_feedback_coder.text import canonicalise


@pytest.fixture
def make_comment():
    """Factory for comments carrying canonical text, as the CSV reader produces."""

    def build(text: str, comment_id: str = "1", row_number: int = 1) -> Comment:
        return Comment(comment_id=comment_id, row_number=row_number, text=canonicalise(text))

    return build


@pytest.fixture
def book() -> Codebook:
    return Codebook(
        themes=[
            Theme(id="onboarding", label="Onboarding"),
            Theme(id="pay", label="Pay and benefits"),
            Theme(id="management", label="Management"),
            Theme(id="workload", label="Workload"),
        ]
    )
