"""Tests for the codebook round trip and for rejecting unusable edits."""

import pytest
import yaml

from open_feedback_coder.codebook import (
    Codebook,
    CodebookError,
    Example,
    Theme,
    from_dict,
    load,
    save,
    to_yaml,
)


def sample() -> Codebook:
    return Codebook(
        themes=[
            Theme(
                id="onboarding",
                label="Onboarding",
                description="Getting set up and up to speed in the first weeks.",
                examples=[Example(comment_id="7", quote="nobody owned it")],
            ),
            Theme(id="pay", label="Pay and benefits"),
        ],
        source={"comments_analysed": 412},
    )


def test_round_trip_through_a_file(tmp_path):
    path = tmp_path / "codebook.yaml"

    save(sample(), str(path))
    loaded = load(str(path))

    assert [theme.id for theme in loaded.themes] == ["onboarding", "pay"]
    assert loaded.themes[0].examples[0].quote == "nobody owned it"
    assert loaded.source["comments_analysed"] == 412


def test_the_written_file_explains_that_it_is_meant_to_be_edited(tmp_path):
    text = to_yaml(sample())

    assert text.lstrip().startswith("#")
    assert "edit" in text.lower() or "Rename" in text


def test_a_hand_edited_codebook_still_loads(tmp_path):
    """Deleting themes and rewriting labels by hand is the intended workflow."""
    path = tmp_path / "codebook.yaml"
    save(sample(), str(path))

    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["themes"] = [document["themes"][1]]
    document["themes"][0]["label"] = "Compensation"
    path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")

    loaded = load(str(path))

    assert len(loaded.themes) == 1
    assert loaded.themes[0].label == "Compensation"


def test_examples_may_be_deleted_by_hand():
    book = from_dict({"themes": [{"id": "pay", "label": "Pay"}]})

    assert book.themes[0].examples == []


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        ({}, "non-empty list"),
        ({"themes": []}, "non-empty list"),
        ({"themes": [{"label": "No id"}]}, "no 'id'"),
        ({"themes": [{"id": "Pay Themes", "label": "Pay"}]}, "lowercase"),
        ({"themes": [{"id": "pay", "label": ""}]}, "no 'label'"),
        (
            {"themes": [{"id": "pay", "label": "Pay"}, {"id": "pay", "label": "Also pay"}]},
            "more than once",
        ),
    ],
)
def test_unusable_codebooks_are_rejected_with_an_explanation(document, expected):
    with pytest.raises(CodebookError) as error:
        from_dict(document)

    assert expected in str(error.value)


def test_a_missing_file_points_at_the_propose_step(tmp_path):
    with pytest.raises(CodebookError) as error:
        load(str(tmp_path / "absent.yaml"))

    assert "ofc propose" in str(error.value)


def test_invalid_yaml_is_reported_as_such(tmp_path):
    path = tmp_path / "codebook.yaml"
    path.write_text("themes: [oops\n", encoding="utf-8")

    with pytest.raises(CodebookError) as error:
        load(str(path))

    assert "not valid YAML" in str(error.value)


def test_get_returns_none_for_a_theme_that_was_deleted():
    book = from_dict({"themes": [{"id": "pay", "label": "Pay"}]})

    assert book.get("pay") is not None
    assert book.get("onboarding") is None
