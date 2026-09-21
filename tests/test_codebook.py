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
    assert loaded.source["comments_analysed"] == 412


def test_the_editable_file_carries_no_quotes(tmp_path):
    """They live in the evidence document; here they are only in the way."""
    path = tmp_path / "codebook.yaml"

    save(sample(), str(path))
    text = path.read_text(encoding="utf-8")

    assert "nobody owned it" not in text
    assert "examples" not in text
    assert load(str(path)).themes[0].examples == []


def test_a_codebook_that_still_has_examples_is_accepted(tmp_path):
    """Older files, and hand-written ones, must keep working."""
    path = tmp_path / "codebook.yaml"
    path.write_text(
        "themes:\n"
        "  - id: pay\n"
        "    label: Pay\n"
        "    examples:\n"
        "      - comment_id: '7'\n"
        "        quote: the pay is fine\n",
        encoding="utf-8",
    )

    loaded = load(str(path))

    assert loaded.themes[0].examples[0].quote == "the pay is fine"


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


# The project claims a person approved the codebook. These make that claim
# checkable against the file rather than something to take on trust.


def proposed(*pairs):
    return {"proposed": [{"id": i, "label": l} for i, l in pairs]}


def test_an_untouched_codebook_says_so():
    from open_feedback_coder.codebook import Codebook, Theme, compare_with_proposal

    book = Codebook(
        themes=[Theme(id="pay", label="Pay"), Theme(id="onboarding", label="Onboarding")],
        source=proposed(("pay", "Pay"), ("onboarding", "Onboarding")),
    )

    record = compare_with_proposal(book)

    assert record.untouched
    assert record.kept == 2
    assert "unedited" in record.describe()


def test_deleting_renaming_and_adding_are_each_counted():
    from open_feedback_coder.codebook import Codebook, Theme, compare_with_proposal

    book = Codebook(
        themes=[
            Theme(id="pay", label="Pay"),                    # kept
            Theme(id="onboarding", label="Getting started"),  # relabelled
            Theme(id="hybrid", label="Hybrid work"),          # added by hand
        ],
        source=proposed(
            ("pay", "Pay"), ("onboarding", "Onboarding"), ("workload", "Workload")
        ),
    )

    record = compare_with_proposal(book)

    assert (record.proposed, record.kept, record.relabelled, record.removed, record.added) == (
        3, 1, 1, 1, 1,
    )
    described = record.describe()
    assert "3 proposed" in described
    assert "1 relabelled" in described
    assert "1 deleted" in described
    assert "1 added by hand" in described
    assert not record.untouched


def test_a_codebook_with_no_record_compares_to_nothing():
    from open_feedback_coder.codebook import Codebook, Theme, compare_with_proposal

    assert compare_with_proposal(Codebook(themes=[Theme(id="pay", label="Pay")])) is None
    assert compare_with_proposal(
        Codebook(themes=[Theme(id="pay", label="Pay")], source={"proposed": []})
    ) is None


def test_the_record_survives_the_round_trip(tmp_path):
    from open_feedback_coder.codebook import (
        Codebook, Theme, compare_with_proposal, load, proposal_record, save,
    )

    original = Codebook(themes=[Theme(id="pay", label="Pay")])
    book = Codebook(themes=original.themes, source={"proposed": proposal_record(original)})
    path = tmp_path / "codebook.yaml"

    save(book, str(path))

    assert compare_with_proposal(load(str(path))).untouched


def test_the_evidence_document_holds_the_quotes_and_names_the_codebook():
    from open_feedback_coder.codebook import to_evidence

    text = to_evidence(sample(), codebook_path="my_codebook.yaml")

    assert "nobody owned it" in text
    assert "comment 7" in text
    assert "my_codebook.yaml" in text
    assert "never read" in text
    # A theme whose illustrations were all rejected says so rather than looking fine.
    assert "No example survived checking" in text
