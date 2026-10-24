"""The writer's paragraph guard never lets a paragraph give away our price position (T21): a rejected
paragraph falls back to the template, in both arms of any comparison."""

import pytest

from hackit2 import model

P11_CONCESSION = (
    "We agree that your casting and labour cost indices give no case for a further price cut, "
    "and price is not our main focus here."
)


def written(text):
    """What the writer returns for a model that answers `text`."""
    return model.write_paragraph(lambda system, prompt: text, "brief")


def test_the_p11_concession_is_rejected():
    assert written(P11_CONCESSION) is None


@pytest.mark.parametrize(
    "text",
    [
        "Price is not our priority in this renewal.",
        "We accept your price and look forward to the next steps.",
        "We agree that the price reflects your situation well.",
        "Your price seems fair given the market.",
        "Your price is justified by the higher material costs.",
        "We see no room for a further reduction on your side.",
        "There is no scope for a price cut, we understand.",
    ],
)
def test_other_concessions_are_rejected(text):
    assert written(text) is None


@pytest.mark.parametrize(
    "text",
    [
        "Thank you for explaining your cost situation so openly. We value the long cooperation with your "
        "foundry and have moved towards you in the attached term sheet.",
        "We cannot accept your price yet, but we have taken a further step towards you.",
        "We do not agree that the price change is fully justified by the indices.",
        "We are unable to accept the price as it stands.",
    ],
)
def test_normal_paragraphs_and_refusals_are_kept(text):
    assert written(text) == text


def test_the_writer_is_told_not_to_concede():
    assert "Never concede that the price is fair" in model.WRITER_SYSTEM
    assert "no room to move" in model.WRITER_SYSTEM
